"""Story 1r — a harvest whose `meetings/` write finds the collection claimed.

The attempt fails by name: a retryable failure recorded beside the cursor, the
position left where it was, and then the refusal raised on — so the scheduled
harvest (`9a`) has a row to back off from and the command at the terminal still
reports what happened. Runs against a real temporary root and the real kernel
lock, because "another process holds it" is the platform lock held on the same
path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from claim_fakes import held_by_another_thread
from pm_ai.app.pipelines import run_harvest
from pm_ai.app.wiring import build
from pm_ai.core.config import Config
from pm_ai.core.meeting_records import MEETINGS, MeetingRecords
from pm_ai.domain.harvest import Cursor, HarvestFailure, HarvestOutcome, HarvestResult
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.lifecycle import CoverageWindow
from pm_ai.domain.meetings import Meeting
from pm_ai.platform.claims import exclusive
from pm_ai.ports import ArtifactBusy

NOW = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
PERSONAL = DataScope(ScopeKind.PERSONAL)
INSTANCE = "graph:work"
LOADED = Cursor(b"where-the-last-harvest-got-to")
EARLIER = CoverageWindow(INSTANCE, NOW - timedelta(days=2), NOW - timedelta(days=2))
WINDOW = CoverageWindow(INSTANCE, NOW - timedelta(seconds=3), NOW)
BUSY = (
    "meetings/ in personal is claimed by another pm-ai process; nothing was "
    "changed. Run the command again."
)


def _meeting(n: int) -> Meeting:
    return Meeting(
        meeting_id=f"mtg_busy_{n}",
        title=f"sync {n}",
        start=NOW - timedelta(days=1, hours=n),
        duration_minutes=30,
        attendees=(Actor("actor_andrei", "Andrei"),),
        scope=PERSONAL,
    )


class _ThreeRecords:
    """A connector whose harvest earned three meeting records, a real coverage
    window and no events — optionally with a failure of its own."""

    def __init__(self, failure: HarvestFailure | None = None) -> None:
        self.failure = failure

    def harvest(self, cursor: Cursor) -> HarvestResult:
        return HarvestResult(
            events=(),
            cursor=Cursor(b"further-along"),
            outcome=HarvestOutcome.FAILED if self.failure else HarvestOutcome.HARVESTED,
            coverage=WINDOW,
            failure=self.failure,
            # 8p: a window starts at the instant the provider answered.
            answered_at=WINDOW.start,
            records=tuple(_meeting(n) for n in (1, 2, 3)),
        )


def _daemon(tmp_path: Path, monkeypatch, *, busy_at: int, failure=None):
    """A daemon whose `meetings/` is claimed just before record `busy_at` is written.

    The holder is the platform lock taken on the same path by another thread of
    this process — a second open file description, which `flock` treats exactly
    as it treats another process. Not this thread: a claim taken again by its
    own holder is a programming error, not a busy file (AD-5, story 8q). Yields
    the daemon; the lock is released after the test.
    """
    daemon = build(tmp_path, "alpha", now=lambda: NOW, config=Config(pm_handle="pm@x"))
    daemon.connectors[INSTANCE] = _ThreeRecords(failure)
    daemon.storage.save_cursor(INSTANCE, LOADED, EARLIER, None)

    target = daemon.storage.paths.resolve(PERSONAL, MEETINGS).resolve()
    holder = held_by_another_thread(lambda: exclusive(target))
    entered = False
    real = MeetingRecords.put
    calls: list[str] = []

    def put(self, meeting):
        nonlocal entered
        calls.append(meeting.meeting_id)
        if len(calls) == busy_at:
            holder.__enter__()
            entered = True
        return real(self, meeting)

    monkeypatch.setattr(MeetingRecords, "put", put)
    try:
        yield daemon
    finally:
        if entered:
            holder.__exit__(None, None, None)


@pytest.fixture
def daemon(tmp_path: Path, monkeypatch):
    yield from _daemon(tmp_path, monkeypatch, busy_at=2)


@pytest.mark.parametrize(
    ("busy_at", "earlier"),
    [
        (1, "no record before it was written"),
        (2, "the 1 record(s) before it were written and will be rewritten"),
    ],
)
def test_a_busy_meetings_write_fails_the_attempt_by_name_and_raises(
    tmp_path, monkeypatch, busy_at, earlier
):
    for daemon in _daemon(tmp_path, monkeypatch, busy_at=busy_at):
        before = daemon.storage.coverage_windows(INSTANCE)
        with pytest.raises(ArtifactBusy) as refused:
            run_harvest(daemon, INSTANCE)

        assert str(refused.value) == BUSY
        failure = daemon.storage.harvest_failure(INSTANCE)
        assert failure is not None, "the busy attempt left no row to back off from"
        assert failure.retryable is True
        assert "meetings/ in personal" in failure.reason
        assert f"record {busy_at} of 3" in failure.reason
        assert f"mtg_busy_{busy_at}" in failure.reason
        assert earlier in failure.reason
        assert daemon.storage.load_cursor(INSTANCE) == LOADED, "the position moved"
        assert daemon.storage.coverage_windows(INSTANCE) == before, (
            "coverage was recorded for an attempt whose events were never persisted"
        )
        written = daemon.storage.list_collection(scope=PERSONAL, artifact=MEETINGS)
        assert len(written) == busy_at - 1, "only the records before the busy one"


def test_a_failure_the_connector_reported_is_kept_beside_the_busy_one(
    tmp_path, monkeypatch
):
    fetched = HarvestFailure(
        reason="page 2 answered 401: the token was revoked.", retryable=False
    )
    for daemon in _daemon(tmp_path, monkeypatch, busy_at=2, failure=fetched):
        with pytest.raises(ArtifactBusy):
            run_harvest(daemon, INSTANCE)

        failure = daemon.storage.harvest_failure(INSTANCE)
        assert failure is not None
        assert fetched.reason in failure.reason, "the connector's failure was replaced"
        assert "record 2 of 3" in failure.reason
        assert failure.retryable is False, "a permanent failure reported as retryable"


def test_a_failure_to_record_it_is_logged_and_the_refusal_still_raised(
    daemon, monkeypatch, caplog
):
    def broken(*args, **kwargs):
        raise RuntimeError("operational.db is gone")

    monkeypatch.setattr(daemon.storage, "save_cursor", broken)
    with caplog.at_level(logging.ERROR, logger="pm_ai.app.pipelines"):
        with pytest.raises(ArtifactBusy) as refused:
            run_harvest(daemon, INSTANCE)

    assert str(refused.value) == BUSY, "the logging failure replaced the refusal"
    assert any("busy meetings/" in record.getMessage() for record in caplog.records)
    assert daemon.storage.load_cursor(INSTANCE) == LOADED, "the position moved"
