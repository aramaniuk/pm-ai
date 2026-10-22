"""Story 13a — the scheduler's expiry sweep of staged proposals (AD-13).

The matrix rows "Sweep" and "Sweep, unreadable body", acceptance 3's second
half, and the timer's schedule. The daemon's writer thread and I/O pool are
the real ones over a temporary root; the clock is the test's and the sweep's
sleeps advance it, so a pass runs without waiting.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_morning_render import Clock, _NoKey
from pm_ai.app.concurrency import IOPool, WriterThread
from pm_ai.app.entry import DAEMON_BOUNDS
from pm_ai.app.pipelines import prepare_dashboard
from pm_ai.app.prompts import DAEMON_LOGGER, RequestPath
from pm_ai.app.ticks import SWEEP_BATCH, SWEEP_INTERVAL, ProposalSweep
from pm_ai.app.wiring import build
from pm_ai.core import proposals as core_proposals
from pm_ai.core.config import Config
from pm_ai.core.meeting_summary import action_item_id
from pm_ai.domain.health import Report
from pm_ai.domain.identity import DataScope, ScopeKind, SourceRef
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.domain.meeting_summaries import SummaryActionItem
from pm_ai.domain.proposals import Proposal
from pm_ai.domain.storage_tiers import OPERATIONAL_DB

UTC = timezone.utc
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
PERSONAL = DataScope(ScopeKind.PERSONAL)
APPLICATION = DataScope(ScopeKind.APPLICATION)


class Rig:
    def __init__(self, root: Path) -> None:
        self.clock = Clock(NOW)
        self.daemon = build(
            root,
            APPLICATION,
            keychain=_NoKey(),
            encryption_disabled=True,
            now=self.clock,
            config=Config(display_timezone="UTC"),
        )
        self.writer = WriterThread(
            log=logging.getLogger(DAEMON_LOGGER), step_scope=self.daemon.storage.claimed_step
        )
        self.writer.start()
        self.writer.call(self.daemon.storage.adopt_daemon_connection, label="adopt")
        self.pool = IOPool()
        self.pool.start()
        self.path = RequestPath(
            daemon=self.daemon,
            writer=self.writer,
            pool=self.pool,
            diagnose=lambda: Report(()),
            bounds=DAEMON_BOUNDS,
        )

        async def sleep(seconds: float) -> None:
            self.clock.advance(timedelta(seconds=seconds))

        self.sweep = ProposalSweep(requests=self.path, now=self.clock, sleep=sleep)

    @property
    def store(self) -> Path:
        return Path(self.daemon.storage.paths.resolve(APPLICATION, OPERATIONAL_DB))

    def stage(self, what: str, *, staged: datetime = NOW, meeting: str = "mtg_a") -> str:
        proposal_id = action_item_id(PERSONAL, meeting, SummaryActionItem(what))
        self.writer.call(
            lambda: self.daemon.storage.stage_proposal(
                Proposal(
                    proposal_id=proposal_id,
                    type="action_item",
                    summary=what,
                    payload={"what": what, "owner": None, "due": None},
                    target=None,
                    cites=SourceRef.parse(f"meeting:{meeting}"),
                    created_at=staged,
                    staged_at=staged,
                    scope="personal",
                    ttl=core_proposals.PROPOSAL_TTL,
                )
            ),
            label="stage a proposal",
        )
        return proposal_id

    def load(self, proposal_id: str) -> Proposal:
        return self.writer.call(
            lambda: self.daemon.storage.load_proposal(proposal_id), label="read a proposal"
        )

    def tick(self):
        return asyncio.run(self.sweep.tick())

    def close(self) -> None:
        self.writer.close()
        self.pool.shutdown()
        self.daemon.storage.close()


@pytest.fixture
def rig(tmp_path) -> Iterator[Rig]:
    built = Rig(tmp_path)
    try:
        yield built
    finally:
        built.close()


def test_the_sweep_expires_every_item_past_its_window(rig):
    """Matrix "Sweep": within 15 minutes, `changed_at` at their expiry, `changed_via: sweep`."""
    staged = [
        rig.stage(f"task {n}", staged=NOW - timedelta(days=14, hours=n)) for n in range(3)
    ]
    waiting = rig.stage("still waiting")
    done = rig.tick()
    assert done.expired == 3 and done.unreadable == 0
    for n, proposal_id in enumerate(staged):
        moved = rig.load(proposal_id)
        assert moved.state is ProposalState.EXPIRED
        assert moved.changed_via == "sweep"
        assert moved.changed_at == NOW - timedelta(hours=n)
        assert moved.version == 2
    assert rig.load(waiting).state is ProposalState.STAGED
    assert rig.sweep.wake == NOW + SWEEP_INTERVAL


def test_the_dashboard_counts_the_items_the_sweep_expired(rig):
    """Matrix "Sweep": the page counts "3 expired without review"."""
    from test_dashboard_slice import _summarized

    _summarized(
        rig.daemon, "mtg_a", items=(), start=NOW - timedelta(hours=2),
        recorded=NOW - timedelta(hours=1),
    )
    for n in range(3):
        rig.stage(f"task {n}", staged=NOW - timedelta(days=15))
    rig.tick()
    body = prepare_dashboard(rig.daemon, scope=PERSONAL, now=NOW).decode("utf-8")
    assert (
        "  - 3 action items expired without review: `pm-ai proposal list --scope personal "
        "--meeting mtg_a --state expired`"
    ) in body


def test_an_unreadable_body_is_counted_and_never_expired(rig, caplog):
    """Matrix "Sweep, unreadable body"."""
    broken = rig.stage("broken", staged=NOW - timedelta(days=15))
    fine = rig.stage("fine", staged=NOW - timedelta(days=15))
    db = sqlite3.connect(rig.store)
    db.execute("UPDATE proposals SET body = 'not json {' WHERE proposal_id = ?", (broken,))
    db.commit()
    db.close()
    with caplog.at_level(logging.WARNING):
        done = rig.tick()
    assert done.expired == 1 and done.unreadable == 1
    assert rig.load(fine).state is ProposalState.EXPIRED
    db = sqlite3.connect(rig.store)
    state, body = db.execute(
        "SELECT state, body FROM proposals WHERE proposal_id = ?", (broken,)
    ).fetchone()
    db.close()
    assert state == "staged" and body == "not json {"
    assert "not json" not in caplog.text


def test_approval_before_the_sweep_runs_is_refused_then_the_sweep_expires_it(rig):
    """Acceptance 3: the row unchanged by a refused approve, then `expired` by the sweep.

    The approve is the terminal's: its own connection on the store the daemon
    writes, while the daemon's writer thread holds its own.
    """
    from pm_ai.app.proposals import ProposalDesk
    from pm_ai.core.proposal_cards import ProposalRefused

    item = rig.stage("late", staged=NOW - timedelta(days=15))
    before = rig.load(item)
    terminal = build(
        None,
        APPLICATION,
        paths=rig.daemon.storage.paths,
        keychain=_NoKey(),
        encryption_disabled=True,
        now=rig.clock,
    )
    desk = ProposalDesk(
        now=rig.clock,
        paths=rig.daemon.storage.paths,
        storage=terminal.storage,
        types=terminal.proposal_types,
    )
    try:
        with pytest.raises(ProposalRefused) as refused:
            desk.approve(item)
        assert "not reviewed" in str(refused.value)
        assert rig.load(item) == before
    finally:
        terminal.storage.close()
    rig.tick()
    moved = rig.load(item)
    assert moved.state is ProposalState.EXPIRED and moved.changed_via == "sweep"


def test_the_next_pass_comes_at_the_earliest_expiry_when_sooner(rig):
    rig.stage("soon", staged=NOW - timedelta(days=14) + timedelta(minutes=5))
    done = rig.tick()
    assert done.expired == 0
    assert done.next_expiry == NOW + timedelta(minutes=5)
    assert rig.sweep.wake == NOW + timedelta(minutes=5)


def test_a_row_that_changed_in_between_is_skipped_for_the_next_pass(rig):
    item = rig.stage("raced", staged=NOW - timedelta(days=15))
    scan = rig.daemon.storage.overdue_proposals(rules=rig.daemon.proposal_types.rules())
    # Another writer moved it on after the sweep read it.
    db = sqlite3.connect(rig.store)
    db.execute("UPDATE proposals SET version = 2 WHERE proposal_id = ?", (item,))
    db.commit()
    db.close()
    step = rig.writer.call(
        lambda: rig.daemon.storage.expire_overdue(
            scan.candidates, rules=rig.daemon.proposal_types.rules()
        ),
        label="expire the proposals past their window",
    )
    assert step.expired == () and step.skipped == 1


def test_steps_hold_at_most_two_hundred_proposals(rig):
    labels: list[str] = []
    real = rig.writer.call

    def counting(work, *, label, **kwargs):
        labels.append(label)
        return real(work, label=label, **kwargs)

    for n in range(SWEEP_BATCH + 5):
        rig.stage(f"task {n}", staged=NOW - timedelta(days=15))
    rig.writer.call = counting  # type: ignore[method-assign]
    done = rig.tick()
    assert done.expired == SWEEP_BATCH + 5
    assert labels.count("expire the proposals past their window") == 2


def test_a_registered_type_sets_only_its_window_and_never_sweeps():
    """AD-13: no type defines its own expiry; the scheduler's sweep is the only one."""
    for name, spec in core_proposals.registered_types().items():
        assert not hasattr(spec, "expire") and spec.ttl == timedelta(days=14), name


def test_the_sweep_runs_at_start_and_again_at_its_wake(rig):
    """Finding 28: `run` makes a pass at once, then the next at `wake`, by the clock."""
    first = rig.stage("first", staged=NOW - timedelta(days=15))
    passes: list[datetime] = []
    real_tick = rig.sweep.tick

    async def counted():
        passes.append(rig.clock())
        if len(passes) == 2:
            rig.stage("second", staged=NOW - timedelta(days=15))
        if len(passes) == 3:
            raise asyncio.CancelledError
        return await real_tick()

    rig.sweep.tick = counted  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(rig.sweep.run())
    assert passes[0] == NOW and passes[1] == NOW + SWEEP_INTERVAL
    assert rig.load(first).state is ProposalState.EXPIRED


def test_a_pass_that_raises_is_logged_and_the_next_is_due(rig, caplog):
    """Finding 28: the error path."""
    calls = 0

    async def broken():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        raise asyncio.CancelledError

    rig.sweep.tick = broken  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR), pytest.raises(asyncio.CancelledError):
        asyncio.run(rig.sweep.run())
    assert "stopped on an unexpected RuntimeError" in caplog.text
    assert calls == 2 and rig.clock() == NOW + SWEEP_INTERVAL


def test_a_busy_store_leaves_the_rows_for_the_next_pass(rig, monkeypatch):
    """Finding 28: busy is skipped, never a failure."""
    from pm_ai.storage.service import OperationalStoreBusy

    item = rig.stage("busy", staged=NOW - timedelta(days=15))

    def busy(*_a, **_k):
        raise OperationalStoreBusy("operational.db is busy")

    monkeypatch.setattr(rig.sweep, "_expire", busy)
    done = rig.tick()
    assert done.expired == 0 and done.skipped == 1
    assert rig.load(item).state is ProposalState.STAGED


def test_a_stopping_writer_ends_the_pass(rig, monkeypatch):
    """Finding 28: `WriterStopped` stops the pass; the next is the regular one."""
    from pm_ai.app.concurrency import WriterStopped

    rig.stage("stopping", staged=NOW - timedelta(days=15))
    rig.stage("soon", staged=NOW - timedelta(days=14) + timedelta(minutes=5))

    def stopped(*_a, **_k):
        raise WriterStopped("the writer is stopping")

    monkeypatch.setattr(rig.sweep, "_expire", stopped)
    done = rig.tick()
    assert done.stopped and done.expired == 0
    assert rig.sweep.wake == NOW + SWEEP_INTERVAL


def test_each_read_loads_at_most_a_batch(rig, monkeypatch):
    """Finding 22: the backlog is read a batch at a time."""
    for n in range(SWEEP_BATCH + 5):
        rig.stage(f"task {n}", staged=NOW - timedelta(days=15))
    storage = rig.daemon.storage
    real = type(storage).overdue_proposals
    sizes: list[int] = []

    def counted(self, **kwargs):
        scan = real(self, **kwargs)
        assert kwargs.get("limit") == SWEEP_BATCH
        sizes.append(len(scan.candidates))
        return scan

    monkeypatch.setattr(type(storage), "overdue_proposals", counted)
    done = rig.tick()
    assert done.expired == SWEEP_BATCH + 5
    assert sizes[:2] == [SWEEP_BATCH, 5]


def test_an_unreadable_row_is_warned_about_once_and_counted_once(rig, caplog):
    """Finding 22: the same stuck row is not logged every pass, nor counted twice."""
    broken = rig.stage("broken", staged=NOW - timedelta(days=15))
    db = sqlite3.connect(rig.store)
    db.execute("UPDATE proposals SET body = 'not json {' WHERE proposal_id = ?", (broken,))
    db.commit()
    db.close()
    with caplog.at_level(logging.WARNING):
        first = rig.tick()
        second = rig.tick()
    assert first.unreadable == 1 and second.unreadable == 1
    assert caplog.text.count("could not be read") == 1
