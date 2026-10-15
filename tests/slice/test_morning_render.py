"""Story 9b — the daemon renders every dashboard at 07:00 local, one test per matrix row.

The render timer (`pm_ai.app.ticks.RenderTimers`) is driven with an injected
clock and sleep against a real composed daemon over a throwaway root: the real
single writer and `operational.db`, the real writer thread and I/O pool, the
real request path. Only the calendars are stood in — scripted answers, so no
row opens a socket. `display_timezone` is Europe/Warsaw, so 07:00 local is
05:00 UTC in October before the clocks change.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from pm_ai.app.concurrency import IOPool, StepKind, WriterThread
from pm_ai.app.entry import DAEMON_BOUNDS
from pm_ai.app.pipelines import DASHBOARD_ARTIFACT, run_dashboard
from pm_ai.app.prompts import DAEMON_LOGGER, RequestPath
from pm_ai.app.ticks import HarvestTimers, RenderOutcome, RenderTimers
from pm_ai.app.wiring import Daemon, build
from pm_ai.core.cadence import look_back
from pm_ai.core.connector_enrolment import enrol_connector, stored_credentials
from pm_ai.core.goal_register import ARTIFACT as GOALS_ARTIFACT
from pm_ai.core.project_registry import ProjectEntry, render_registry
from pm_ai.domain.events import ObservedEventType
from pm_ai.domain.harvest import Cursor, HarvestFailure, HarvestOutcome, HarvestResult
from pm_ai.domain.health import Health, Probe, Report
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.lifecycle import CoverageWindow
from pm_ai.domain.scope_model import ARTIFACT_TIER, BACKUP_TARGETS, REBUILD_TARGETS, Tier
from pm_ai.domain.storage_tiers import requires_git_exclusion
from pm_ai.domain.vcs import TrackingVerdict
from pm_ai.platform.doctor import RENDERS_PROBE, renders_recorded
from pm_ai.ports import KeyNotFound
from pm_ai.storage.service import StorageService

ZONE = "Europe/Warsaw"
WARSAW = ZoneInfo(ZONE)
SEVEN = datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc)
"""07:00 in Warsaw on 2026-10-14 (CEST, UTC+2)."""
MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
APPLICATION = DataScope(ScopeKind.APPLICATION)
PERSONAL = DataScope(ScopeKind.PERSONAL)
ALPHA = DataScope(ScopeKind.PROJECT, "alpha")
PERSONAL_SAVE = "save the personal dashboard"
MALFORMED_GOALS = b"""# Strategic Goals

## Project

- [g_latency] (short) Cut payment latency below 200ms
- [g_latency] (short) Cut it again
"""
"""Two goals under one id: `parse_goals` refuses it, so the personal render fails."""
ALPHA_SAVE = "save the project:alpha dashboard"


class _NoKey:
    def store(self, name: str, secret: bytes) -> None:  # pragma: no cover
        raise AssertionError("nothing here enrols a key")

    def store_if_absent(self, name: str, secret: bytes) -> None:  # pragma: no cover
        raise AssertionError("nothing here enrols a key")

    def fetch(self, name: str) -> bytes:
        raise KeyNotFound(name)

    def delete(self, name: str) -> None:  # pragma: no cover
        raise AssertionError("nothing here deletes a key")


class Clock:
    """The one clock the daemon, the storage and the timer read; thread-safe."""

    def __init__(self, at: datetime) -> None:
        self._at = at
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self._at

    def set(self, at: datetime) -> None:
        with self._lock:
            self._at = at

    def advance(self, by: timedelta) -> None:
        with self._lock:
            self._at += by


@dataclass
class Vcs:
    """A git that answers for the project's repository as told, by thread."""

    repository: Path
    ignored: bool = True
    threads: list[str] = field(default_factory=list)

    def working_tree(self, path: Path) -> Path | None:
        self.threads.append(threading.current_thread().name)
        return self.repository if self.repository in path.parents else None

    def tracking(self, path: Path, *, repository: Path) -> TrackingVerdict:
        self.threads.append(threading.current_thread().name)
        return TrackingVerdict(ignored=self.ignored)

    def repository_marker_above(self, path: Path) -> Path | None:  # pragma: no cover
        return None


class Calendar:
    """A connector declaring the calendar capability; each read runs `answer`."""

    system = "double"

    def __init__(self, instance: str, answer: Callable[[], HarvestResult] | None = None) -> None:
        self.instance = instance
        self.name = instance
        self.answer = answer
        self.asked = 0

    def emits(self) -> frozenset[ObservedEventType]:
        return frozenset({ObservedEventType.CALENDAR_EVENT_HELD})

    def harvest(self, since: Cursor) -> HarvestResult:
        self.asked += 1
        if self.answer is not None:
            return self.answer()
        return HarvestResult(
            events=(), cursor=since, outcome=HarvestOutcome.EMPTY, answered_at=SEVEN
        )

    def sample_events(self) -> tuple:
        return ()

    def check_health(self) -> Probe:
        return Probe(self.instance, Health.OK, "a double")


@dataclass
class Rig:
    root: Path
    clock: Clock
    daemon: Daemon
    writer: WriterThread
    pool: IOPool
    path: RequestPath
    vcs: Vcs
    steps: list[str] = field(default_factory=list)
    timers: RenderTimers | None = None

    def config(self, zone: str | None = ZONE) -> None:
        body = f'display_timezone = "{zone}"\n'.encode() if zone else b""
        _write(self.daemon.storage.paths.resolve(APPLICATION, "config.toml"), body)

    def use(self, *connectors: Any) -> None:
        """Every fresh read sees these connectors, and config.toml as it is now."""
        table = {connector.instance: connector for connector in connectors}
        real = RequestPath.fresh

        def fresh() -> Daemon:
            return replace(real(self.path), connectors=dict(table))

        self.path.fresh = fresh  # type: ignore[method-assign]

    def make_timers(self, *, budget: float = 30.0, sleeps: list[timedelta] | None = None) -> RenderTimers:
        async def sleep(seconds: float) -> None:
            # A scripted sleep may last longer than asked: a laptop asleep.
            self.clock.advance(sleeps.pop(0) if sleeps else timedelta(seconds=seconds))

        self.timers = RenderTimers(requests=self.path, now=self.clock, sleep=sleep, budget=budget)
        return self.timers

    def drive(self, until: datetime, *, first: bool = True) -> None:
        """Evaluate now, then every wake up to `until`."""
        timers = self.timers or self.make_timers()

        async def go() -> None:
            if first:
                await timers.tick()
            while timers.wake is not None and timers.wake <= until:
                await timers._wait()  # noqa: SLF001
                await timers.tick()

        asyncio.run(go())

    def records(self) -> dict[DataScope, Any]:
        return {record.scope: record for record in self.daemon.storage.render_records()}

    def dashboard(self, scope: DataScope) -> Path:
        return self.daemon.storage.paths.resolve(scope, DASHBOARD_ARTIFACT)

    def saves(self) -> list[str]:
        return [step for step in self.steps if step.endswith(" dashboard")]

    def doctor(self) -> Probe:
        return renders_recorded(
            (PERSONAL, ALPHA),
            self.daemon.storage.render_records(),
            zone_set=bool(self.path.fresh().config.display_timezone),
        )


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    clock = Clock(SEVEN - HOUR)
    vcs = Vcs(tmp_path / "projects" / "alpha")
    daemon = build(
        tmp_path,
        APPLICATION,
        watched=("alpha",),
        keychain=_NoKey(),
        encryption_disabled=True,
        now=clock,
        vcs=vcs,
    )
    _write(
        daemon.storage.paths.resolve(APPLICATION, "projects.toml"),
        render_registry({"alpha": ProjectEntry(path=tmp_path / "projects" / "alpha")}),
    )
    writer = WriterThread(log=logging.getLogger(DAEMON_LOGGER), step_scope=daemon.storage.claimed_step)
    writer.start()
    writer.call(daemon.storage.adopt_daemon_connection, label="adopt")
    real_call = writer.call
    pool = IOPool()
    pool.start()
    path = RequestPath(
        daemon=daemon, writer=writer, pool=pool, diagnose=lambda: Report(()), bounds=DAEMON_BOUNDS
    )
    daemon.writer, daemon.pool = writer, pool
    built = Rig(tmp_path, clock, daemon, writer, pool, path, vcs)
    built.config()
    built.use()

    def call(work, *, label, kind=StepKind.ORDINARY, job=None, start_within=None):
        built.steps.append(label)
        return real_call(work, label=label, kind=kind, job=job, start_within=start_within)

    writer.call = call  # type: ignore[method-assign]
    try:
        yield built
    finally:
        writer.close()
        pool.shutdown()
        daemon.storage.close()


# ── 07:00, once per local day per scope ──────────────────────────────────────


def test_07_00_renders_personal_then_the_project_and_records_both_instants(rig):
    """Rows "07:00 arrives" and the first acceptance criterion."""
    rig.drive(until=SEVEN + MINUTE)

    assert rig.saves() == [PERSONAL_SAVE, ALPHA_SAVE]
    records = rig.records()
    assert records[PERSONAL].last_success == SEVEN
    assert records[ALPHA].last_success == SEVEN
    assert rig.dashboard(PERSONAL).is_file() and rig.dashboard(ALPHA).is_file()
    assert rig.timers is not None and rig.timers.wake == SEVEN + timedelta(minutes=15)

    # Every evaluation before tomorrow's 07:00 writes nothing.
    rig.steps.clear()
    rig.drive(until=SEVEN + DAY - MINUTE, first=False)
    assert rig.steps == []
    assert rig.timers.wake == SEVEN + DAY


def test_before_07_00_nothing_is_rendered_and_07_00_is_a_wake(rig):
    rig.drive(until=SEVEN - MINUTE)
    assert rig.saves() == []
    assert rig.timers is not None and rig.timers.wake == SEVEN


def test_started_after_07_00_with_no_render_today_renders_at_once(rig):
    """Row "Started after 07:00": 09:30, nothing today — rendered at once."""
    rig.clock.set(SEVEN + timedelta(hours=2, minutes=30))
    rig.drive(until=rig.clock())
    assert rig.saves() == [PERSONAL_SAVE, ALPHA_SAVE]
    assert rig.records()[PERSONAL].last_success == SEVEN + timedelta(hours=2, minutes=30)


def test_started_after_a_render_today_waits_for_tomorrow(rig):
    """Row "Started after a render": 09:30, today already rendered — nothing now."""
    for scope in (PERSONAL, ALPHA):
        rig.daemon.storage.record_render(scope, at=SEVEN + 10 * MINUTE)
        _write(rig.dashboard(scope), b"this morning's page\n")
    rig.clock.set(SEVEN + timedelta(hours=2, minutes=30))
    rig.drive(until=SEVEN + DAY - MINUTE)
    assert rig.saves() == []
    rig.drive(until=SEVEN + DAY, first=False)
    assert rig.saves() == [PERSONAL_SAVE, ALPHA_SAVE]


def test_a_render_made_by_hand_before_07_00_does_not_stand_for_today(rig):
    rig.daemon.storage.record_render(PERSONAL, at=SEVEN - 2 * HOUR)
    rig.drive(until=SEVEN)
    assert rig.saves() == [PERSONAL_SAVE, ALPHA_SAVE]


def test_a_laptop_asleep_across_07_00_renders_on_waking(rig):
    """Row "Asleep across 07:00": the one sleep lasts until 10:00."""
    timers = rig.make_timers(sleeps=[timedelta(hours=4)])
    rig.drive(until=SEVEN + 3 * HOUR)
    assert rig.saves() == [PERSONAL_SAVE, ALPHA_SAVE]
    assert rig.records()[PERSONAL].last_success == SEVEN + 3 * HOUR
    assert timers.wake is not None and timers.wake > SEVEN + 3 * HOUR


def test_07_00_is_the_zones_wall_clock_across_the_autumn_change(rig):
    """Row "Day boundary": Warsaw leaves CEST on 2026-10-25; 07:00 is 06:00 UTC then."""
    rig.clock.set(datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc))  # 08:00 CEST
    rig.drive(until=rig.clock())
    rig.steps.clear()
    after = datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc)  # 07:00 CET
    assert rig.timers is not None
    rig.drive(until=after - MINUTE, first=False)
    assert rig.saves() == []
    rig.drive(until=after, first=False)
    assert rig.saves() == [PERSONAL_SAVE, ALPHA_SAVE]
    assert rig.records()[PERSONAL].last_success == after


def test_a_zone_changed_while_running_is_used_at_the_next_evaluation(rig):
    """Row "Zone changed": 07:30 in Warsaw is 01:30 in New York — not yet."""
    rig.config("America/New_York")
    rig.clock.set(SEVEN + 30 * MINUTE)
    rig.drive(until=rig.clock())
    assert rig.saves() == []
    assert rig.timers is not None and rig.timers.wake == rig.clock() + timedelta(minutes=15)
    rig.config(ZONE)
    rig.drive(until=rig.timers.wake, first=False)
    assert rig.saves() == [PERSONAL_SAVE, ALPHA_SAVE]


def test_the_rendered_day_follows_the_zone_read_at_the_render(rig):
    rig.clock.set(SEVEN + 30 * MINUTE)
    rig.drive(until=rig.clock())
    personal = rig.dashboard(PERSONAL).read_text(encoding="utf-8")
    assert "2026-10-14 07:30" in personal  # the render's instant, in Warsaw


# ── Failures ─────────────────────────────────────────────────────────────────


def test_one_scope_failing_does_not_stop_the_next(rig):
    """Row "One scope fails" and the second acceptance criterion: a malformed
    goals file fails the personal render, ahead of the project's."""
    _write(rig.daemon.storage.paths.resolve(PERSONAL, GOALS_ARTIFACT), MALFORMED_GOALS)
    yesterday = b"yesterday's page\n"
    _write(rig.dashboard(PERSONAL), yesterday)
    rig.drive(until=SEVEN)

    assert rig.saves() == [ALPHA_SAVE]
    assert rig.dashboard(PERSONAL).read_bytes() == yesterday
    assert rig.dashboard(ALPHA).is_file()
    records = rig.records()
    assert records[PERSONAL].failing and records[PERSONAL].last_success is None
    assert "personal dashboard was not rendered" in (records[PERSONAL].reason or "")
    assert records[ALPHA].last_success == SEVEN and not records[ALPHA].failing
    probe = rig.doctor()
    assert probe.name == RENDERS_PROBE and probe.health is Health.FAILING
    assert "personal: last render never, FAILING, last failed at" in probe.detail
    assert "project:alpha: last render 2026-10-14T05:00:00+00:00" in probe.detail


def test_a_render_failing_on_its_own_input_waits_for_tomorrow(rig):
    """Row "Render fails": tried again at the next 07:00, not before."""
    _write(rig.daemon.storage.paths.resolve(PERSONAL, GOALS_ARTIFACT), MALFORMED_GOALS)
    rig.drive(until=SEVEN)
    rig.steps.clear()
    rig.drive(until=SEVEN + DAY - MINUTE, first=False)
    assert PERSONAL_SAVE not in rig.steps
    rig.daemon.storage.paths.resolve(PERSONAL, GOALS_ARTIFACT).unlink()
    rig.drive(until=SEVEN + DAY, first=False)
    assert PERSONAL_SAVE in rig.steps
    assert not rig.records()[PERSONAL].failing


def test_a_busy_dashboard_fails_naming_the_claim_and_is_retried_after_a_minute(rig):
    """Rows "File busy" and "Hand-run and daemon at once": another holder of the
    claim refuses the attempt by name; the retry after one minute clears it."""
    held, release, done = threading.Event(), threading.Event(), threading.Event()

    def hold() -> None:
        with rig.daemon.storage.exclusive(scope=PERSONAL, artifact=DASHBOARD_ARTIFACT):
            held.set()
            release.wait(10)
        done.set()

    holder = threading.Thread(target=hold)
    holder.start()
    assert held.wait(5)
    rig.drive(until=SEVEN)
    record = rig.records()[PERSONAL]
    assert record.failing and "claimed by another pm-ai process" in (record.reason or "")
    assert not rig.dashboard(PERSONAL).exists()
    assert rig.records()[ALPHA].last_success == SEVEN
    assert rig.timers is not None and rig.timers.wake == SEVEN + MINUTE

    release.set()
    assert done.wait(5)
    rig.drive(until=SEVEN + MINUTE, first=False)
    record = rig.records()[PERSONAL]
    assert record.last_success == SEVEN + MINUTE and not record.failing
    assert rig.dashboard(PERSONAL).is_file()


def test_busy_retries_double_and_stop_at_the_next_07_00(rig):
    timers = rig.make_timers()
    timers._today = {PERSONAL: SEVEN}  # noqa: SLF001
    rig.clock.set(SEVEN)
    for _ in range(3):
        timers._settle(PERSONAL, RenderOutcome.BUSY)  # noqa: SLF001
    assert timers._retries[PERSONAL].due == SEVEN + 4 * MINUTE  # noqa: SLF001
    timers._settle(PERSONAL, RenderOutcome.RENDERED)  # noqa: SLF001
    assert PERSONAL not in timers._retries  # noqa: SLF001


def test_the_row_changes_only_through_one_insert_or_update_statement():
    """Row "Hand-run and daemon at once": never read in one statement and written
    in another; the new value never depends on the old (AD-5)."""
    from pm_ai.storage import service

    for statement in (service._RECORD_RENDER, service._RECORD_FAILURE):  # noqa: SLF001
        assert "ON CONFLICT(scope) DO UPDATE" in statement
        assert "renders." not in statement, "a column set from its own old value"
        assert "MAX(" not in statement.upper() and "WHERE" not in statement.upper()
    for method in (
        StorageService.record_render,
        StorageService.recording_render,
        StorageService.record_render_failure,
    ):
        source = inspect.getsource(method)
        assert source.count(".execute(") == 1, method.__name__
        assert "SELECT" not in source
        assert "_RECORD_RENDER" in source or "_RECORD_FAILURE" in source
        assert "renders." not in source, "a column set from its own old value"


def test_a_hand_run_and_a_daemon_render_each_record_with_their_own_statement(rig):
    run_dashboard(rig.path.fresh(), now=SEVEN - 9 * HOUR)  # 22:00 the evening before, by hand
    assert rig.records()[PERSONAL].last_success == SEVEN - 9 * HOUR
    rig.drive(until=SEVEN)
    assert rig.records()[PERSONAL].last_success == SEVEN


def test_an_unreadable_calendar_is_not_a_failed_render(rig):
    """Row "Calendar unreadable": the page is written with the unread line."""

    def stale() -> HarvestResult:
        return HarvestResult(
            events=(),
            cursor=Cursor(),
            outcome=HarvestOutcome.FAILED,
            failure=HarvestFailure("the refresh token is stale", retryable=False),
        )

    rig.use(Calendar("graph:work", stale))
    rig.drive(until=SEVEN)
    page = rig.dashboard(PERSONAL).read_text(encoding="utf-8")
    assert "the refresh token is stale" in page
    assert not rig.records()[PERSONAL].failing


def test_a_calendar_read_past_its_budget_is_abandoned_and_the_page_still_written(rig):
    release = threading.Event()

    def hung() -> HarvestResult:
        release.wait(10)
        return HarvestResult(events=(), cursor=Cursor(), outcome=HarvestOutcome.EMPTY, answered_at=SEVEN)

    rig.use(Calendar("graph:work", hung))
    rig.make_timers(budget=0.2)
    try:
        rig.drive(until=SEVEN)
    finally:
        release.set()
    page = rig.dashboard(PERSONAL).read_text(encoding="utf-8")
    assert "did not answer within its 0.2 s budget" in page
    assert not rig.records()[PERSONAL].failing


def test_with_no_display_zone_nothing_is_scheduled_and_every_scope_is_recorded(rig):
    """Row "No display zone": recorded at every evaluation; doctor names the key."""
    rig.config(None)
    rig.clock.set(SEVEN + HOUR)
    rig.drive(until=SEVEN + 2 * HOUR)
    assert rig.saves() == []
    records = rig.records()
    for scope in (PERSONAL, ALPHA):
        assert records[scope].failing
        assert "display_timezone is not set" in (records[scope].reason or "")
    assert rig.timers is not None
    assert rig.timers.wake == SEVEN + 2 * HOUR + timedelta(minutes=15)
    assert rig.steps.count("record the personal render's failure") == 5  # every evaluation
    probe = rig.doctor()
    assert probe.health is Health.FAILING
    assert "display_timezone is not set" in probe.detail
    assert "display_timezone" in probe.remediation


def test_a_project_copy_git_cannot_confirm_excluded_is_refused_by_name(rig):
    """Row "Project copy": git asked in a bounded step before the claimed write,
    on a worker; a copy git does not confirm excluded is refused by name."""
    rig.vcs.ignored = False
    rig.drive(until=SEVEN)
    assert rig.dashboard(PERSONAL).is_file()
    assert not rig.dashboard(ALPHA).exists()
    record = rig.records()[ALPHA]
    assert record.failing and "daily_dashboard.md" in (record.reason or "")
    assert rig.vcs.threads and not any(name.startswith("pm-ai-writer") for name in rig.vcs.threads)


def test_git_is_never_asked_on_the_writer_thread_on_any_render_path(rig, monkeypatch):
    """The claimed step receives git's answer with the bytes and never calls git:
    the timer's render, a dashboard job's and a `pm-ai dashboard` line sent to
    the daemon — and no subprocess runs on the writer thread at all."""
    import subprocess

    spawned: list[str] = []
    real_run, real_popen = subprocess.run, subprocess.Popen

    def run(*args, **kwargs):
        spawned.append(threading.current_thread().name)
        return real_run(*args, **kwargs)

    class Popen(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            spawned.append(threading.current_thread().name)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", Popen)
    writer_thread = rig.writer.call(lambda: threading.current_thread().name, label="name")

    rig.drive(until=SEVEN)  # the timer
    asked = len(rig.vcs.threads)
    assert asked, "the project copy's exclusion was asked"
    rig.clock.advance(MINUTE)
    rig.path._dashboard(rig.path.fresh(), ALPHA, job=None)  # noqa: SLF001 — a job's render
    assert len(rig.vcs.threads) > asked, "asked again, never from a cache"
    rig.clock.advance(MINUTE)
    line = asyncio.run(rig.path.prompt("dashboard --scope project:alpha"))
    job = line.body["job_id"]
    deadline = time.monotonic() + 15
    while rig.daemon.storage.read_request(job).state not in ("done", "failed"):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert rig.daemon.storage.read_request(job).state == "done"
    assert rig.records()[ALPHA].last_success == SEVEN + 2 * MINUTE
    assert writer_thread not in rig.vcs.threads
    assert writer_thread not in spawned


# ── Concurrency and shutdown ─────────────────────────────────────────────────


def test_a_render_due_during_a_harvest_runs_and_neither_is_lost(rig):
    """Row "Render due during a harvest": both on the I/O pool, their claimed
    steps taking turns on the writer thread."""
    inside, release = threading.Event(), threading.Event()

    class Slow:
        instance = "graph:slow"
        scope = PERSONAL

        def harvest(self, cursor: Cursor) -> HarvestResult:
            inside.set()
            release.wait(10)
            now = rig.clock()
            return HarvestResult(
                events=(),
                cursor=Cursor(b"after"),
                outcome=HarvestOutcome.EMPTY,
                coverage=CoverageWindow(self.instance, now, now),
                answered_at=now,
            )

        def emits(self) -> frozenset:
            return frozenset()

        def sample_events(self) -> tuple:
            return ()

        def check_health(self) -> Probe:
            return Probe(self.instance, Health.OK, "slow")

    slow = Slow()
    real = RequestPath.fresh
    rig.path.fresh = lambda: replace(  # type: ignore[method-assign]
        real(rig.path), connectors={slow.instance: slow}, instance_scopes={slow.instance: PERSONAL}
    )
    harvests = HarvestTimers(requests=rig.path, now=rig.clock)
    harvesting = threading.Thread(target=lambda: harvests._attempt(slow.instance))  # noqa: SLF001
    harvesting.start()
    assert inside.wait(5)
    rig.clock.set(SEVEN)
    rig.drive(until=SEVEN)
    assert rig.records()[PERSONAL].last_success == SEVEN, "rendered while the harvest fetched"
    release.set()
    harvesting.join(10)
    assert rig.daemon.storage.load_cursor(slow.instance) == Cursor(b"after")


def test_a_token_refreshed_during_the_read_is_saved_as_soon_as_it_is_issued(rig):
    """Row "Token refreshed during the read": the compare-and-save is handed to
    the writer before the render's own save."""
    enrol_connector(
        rig.daemon.storage,
        system="gitlab",
        instance="gitlab:alpha",
        credential="token-one",
        probe=lambda system, credential: "accepted",
    )
    store = rig.path._store_for("gitlab:alpha", "gitlab")  # noqa: SLF001

    def refreshing() -> HarvestResult:
        with store.exclusive():
            assert store.read() == "token-one"
            store.write("token-two")
        return HarvestResult(events=(), cursor=Cursor(), outcome=HarvestOutcome.EMPTY, answered_at=SEVEN)

    rig.use(Calendar("graph:work", refreshing))
    rig.drive(until=SEVEN)
    assert stored_credentials(rig.daemon.storage)["gitlab:alpha"]["credential"] == "token-two"
    saved = rig.steps.index("save gitlab:alpha's refreshed credential")
    assert saved < rig.steps.index(PERSONAL_SAVE)


def test_shutdown_during_the_read_records_the_render_failed_and_writes_nothing(rig):
    """Row "Shutdown during the read"."""
    yesterday = b"yesterday's page\n"
    _write(rig.dashboard(PERSONAL), yesterday)
    inside, release = threading.Event(), threading.Event()

    def blocked() -> HarvestResult:
        inside.set()
        release.wait(10)
        return HarvestResult(events=(), cursor=Cursor(), outcome=HarvestOutcome.EMPTY, answered_at=SEVEN)

    rig.use(Calendar("graph:work", blocked))
    timers = rig.make_timers()
    rig.clock.set(SEVEN)
    outcomes: list[RenderOutcome] = []
    worker = threading.Thread(target=lambda: outcomes.append(timers._attempt(PERSONAL)))  # noqa: SLF001
    worker.start()
    assert inside.wait(5)

    rig.writer.stop_accepting()
    rig.path.shutdown_pass()
    release.set()
    worker.join(10)

    record = rig.records()[PERSONAL]
    assert record.failing and "reading the calendar" in (record.reason or "")
    assert outcomes == [RenderOutcome.STOPPED]
    assert rig.dashboard(PERSONAL).read_bytes() == yesterday


def test_shutdown_during_the_write_lets_it_finish_and_record(rig, monkeypatch):
    """Row "Shutdown during the write": the claimed step finishes and records."""
    storage = rig.daemon.storage
    real = storage.write_artifact
    writing, go_on = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        writing.set()
        go_on.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(storage, "write_artifact", slow)
    timers = rig.make_timers()
    rig.clock.set(SEVEN)
    worker = threading.Thread(target=lambda: timers._attempt(PERSONAL))  # noqa: SLF001
    worker.start()
    assert writing.wait(5)

    rig.writer.stop_accepting()
    stopping = threading.Thread(target=rig.path.shutdown_pass)
    stopping.start()
    go_on.set()
    stopping.join(10)
    worker.join(10)

    record = rig.records()[PERSONAL]
    assert record.last_success == SEVEN and not record.failing
    assert rig.dashboard(PERSONAL).is_file()


def test_the_publish_and_its_record_are_one_claimed_step_the_record_last(rig, monkeypatch):
    """The record's statement opens the step's transaction first — a busy store
    refuses before the rename — and is committed last: while the file is
    written, another connection still sees no record; after the step, it does."""
    from pm_ai.storage.service import render_records_at

    storage = rig.daemon.storage
    store = storage._store  # noqa: SLF001
    order: list[str] = []
    seen_at_write: list[tuple] = []
    real_write, real_recording = storage.write_artifact, storage.recording_render

    def write(*args, **kwargs):
        order.append(f"write on {threading.current_thread().name}")
        # What a separate reader sees while the step is still open.
        seen_at_write.append(render_records_at(store))
        return real_write(*args, **kwargs)

    @contextlib.contextmanager
    def recording(*args, **kwargs):
        with real_recording(*args, **kwargs):
            order.append(f"record statement on {threading.current_thread().name}")
            yield

    monkeypatch.setattr(storage, "write_artifact", write)
    monkeypatch.setattr(storage, "recording_render", recording)
    rig.drive(until=SEVEN)
    writer = rig.writer.call(lambda: threading.current_thread().name, label="name")
    assert order[:2] == [f"record statement on {writer}", f"write on {writer}"]
    assert seen_at_write[0] == (), "the record was not yet committed when the file was written"
    assert rig.records()[PERSONAL].last_success == SEVEN, "committed after the step"
    assert rig.steps.count(PERSONAL_SAVE) == 1


def test_a_busy_store_refuses_the_render_before_the_file_is_touched(rig):
    yesterday = b"yesterday's page\n"
    _write(rig.dashboard(PERSONAL), yesterday)
    other = sqlite3.connect(rig.daemon.storage._store, timeout=0)  # noqa: SLF001
    other.execute("BEGIN IMMEDIATE")
    try:
        timers = rig.make_timers()
        rig.clock.set(SEVEN)
        assert timers._attempt(PERSONAL) is RenderOutcome.BUSY  # noqa: SLF001
        assert rig.dashboard(PERSONAL).read_bytes() == yesterday
    finally:
        other.rollback()
        other.close()


class _CommitFails:
    """`recording_render` whose commit fails after the body (the publish) ran."""

    def __init__(self, real: Any) -> None:
        self.real = real

    @contextlib.contextmanager
    def __call__(self, *args: Any, **kwargs: Any) -> Iterator[None]:
        with self.real(*args, **kwargs):
            yield
            raise sqlite3.OperationalError("disk I/O error")


def test_a_record_failing_after_the_publish_says_the_page_was_written(rig, monkeypatch):
    storage = rig.daemon.storage
    _write(rig.dashboard(PERSONAL), b"yesterday's page\n")
    monkeypatch.setattr(storage, "recording_render", _CommitFails(storage.recording_render))
    timers = rig.make_timers()
    rig.clock.set(SEVEN)
    assert timers._attempt(PERSONAL) is RenderOutcome.FAILED  # noqa: SLF001
    assert rig.dashboard(PERSONAL).read_bytes() != b"yesterday's page\n", "the page is new"
    record = rig.records()[PERSONAL]
    assert record.failing and record.last_success is None
    reason = record.reason or ""
    assert "the personal dashboard was written" in reason
    assert "recording its instant failed" in reason and "OperationalError" in reason
    assert "unchanged" not in reason


def test_a_hand_run_whose_record_fails_after_the_publish_says_so(rig, monkeypatch):
    from pm_ai.domain.renders import RenderUnrecorded

    storage = rig.daemon.storage
    monkeypatch.setattr(storage, "recording_render", _CommitFails(storage.recording_render))
    with pytest.raises(RenderUnrecorded, match="was written"):
        run_dashboard(rig.path.fresh(), now=SEVEN)
    assert rig.dashboard(PERSONAL).is_file()
    assert "recording its instant failed" in (rig.records()[PERSONAL].reason or "")


# ── The look-back ────────────────────────────────────────────────────────────


def _enablement(page: str) -> str:
    return page.split("## Proactive Enablement", 1)[1].split("\n## ", 1)[0]


def test_after_a_weekend_the_section_looks_back_to_fridays_render(rig):
    """Row "After a weekend"."""
    friday = SEVEN - 3 * DAY
    rig.daemon.storage.record_render(PERSONAL, at=friday)
    rig.drive(until=SEVEN)
    section = _enablement(rig.dashboard(PERSONAL).read_text(encoding="utf-8"))
    assert "since the last render at 2026-10-11 07:00" in section


def test_a_long_gap_is_clamped_to_seven_days_and_says_so(rig):
    """Row "Long gap": three weeks ago."""
    rig.daemon.storage.record_render(PERSONAL, at=SEVEN - 21 * DAY)
    rig.drive(until=SEVEN)
    section = _enablement(rig.dashboard(PERSONAL).read_text(encoding="utf-8"))
    assert "in the last 7 days (the earlier render, at 2026-09-23 07:00, was older" in section
    assert "clamped" in section


def test_a_clock_stepped_back_is_clamped_and_says_so(rig):
    rig.daemon.storage.record_render(PERSONAL, at=SEVEN + 2 * DAY)
    record = rig.records()[PERSONAL]
    assert look_back(record.last_success, SEVEN).since == SEVEN - 7 * DAY
    run_dashboard(rig.path.fresh(), now=SEVEN)
    section = _enablement(rig.dashboard(PERSONAL).read_text(encoding="utf-8"))
    assert "in the last 7 days (the recorded render" in section
    assert "is later than this one, so the window was clamped" in section


def test_the_first_render_ever_looks_back_24_hours_and_says_so(rig):
    """Row "First render ever"."""
    rig.drive(until=SEVEN)
    section = _enablement(rig.dashboard(PERSONAL).read_text(encoding="utf-8"))
    assert "in the last 24 hours (no earlier render recorded)" in section


def test_a_hand_run_render_at_22_00_is_where_07_00_looks_back_to(rig):
    """Row "Hand-run dashboard"."""
    run_dashboard(rig.path.fresh(), now=SEVEN - 9 * HOUR)
    rig.drive(until=SEVEN)
    section = _enablement(rig.dashboard(PERSONAL).read_text(encoding="utf-8"))
    assert "since the last render at 2026-10-13 22:00" in section


def test_each_scope_looks_back_to_its_own_render(rig):
    rig.daemon.storage.record_render(PERSONAL, at=SEVEN - DAY)
    rig.daemon.storage.record_render(ALPHA, at=SEVEN - 2 * DAY)
    rig.clock.set(SEVEN)
    rig.drive(until=SEVEN)
    alpha = _enablement(rig.dashboard(ALPHA).read_text(encoding="utf-8"))
    assert "since the last render at 2026-10-12 07:00" in alpha


# ── The tier move ────────────────────────────────────────────────────────────


def test_both_dashboard_copies_are_disposable_and_keep_their_git_answers():
    """Row "Tier move" and the third acceptance criterion."""
    assert ARTIFACT_TIER["daily_dashboard.md"] is Tier.DERIVED
    assert "daily_dashboard.md" in REBUILD_TARGETS
    assert "daily_dashboard.md" not in BACKUP_TARGETS
    assert requires_git_exclusion(ScopeKind.PROJECT, "daily_dashboard.md") is True
    assert requires_git_exclusion(ScopeKind.PERSONAL, "daily_dashboard.md") is False


def test_a_rebuild_rerenders_the_personal_copy_and_skips_an_unconfirmed_project_copy(rig):
    """Row "Rebuild, exclusion unconfirmed": both copies deleted after today's
    render; the next evaluation re-renders personal, and the project copy, with
    git unable to confirm its exclusion now, is skipped by name."""
    rig.drive(until=SEVEN)
    assert rig.dashboard(PERSONAL).is_file() and rig.dashboard(ALPHA).is_file()
    rig.dashboard(PERSONAL).unlink()
    rig.dashboard(ALPHA).unlink()
    rig.vcs.ignored = False
    rig.steps.clear()
    rig.clock.advance(5 * MINUTE)
    rig.drive(until=rig.clock())
    assert rig.dashboard(PERSONAL).is_file()
    assert rig.records()[PERSONAL].last_success == SEVEN + 5 * MINUTE
    assert not rig.dashboard(ALPHA).exists()
    record = rig.records()[ALPHA]
    assert record.failing and "project:alpha" in (record.reason or "")
    assert "daily_dashboard.md" in (record.reason or "")


# ── Review round: the slot, the clock, the job path, robustness ──────────────


def _blocking_calendar(inside: threading.Event, release: threading.Event) -> Calendar:
    def blocked() -> HarvestResult:
        inside.set()
        release.wait(10)
        return HarvestResult(events=(), cursor=Cursor(), outcome=HarvestOutcome.EMPTY, answered_at=SEVEN)

    return Calendar("graph:work", blocked)


def test_a_job_queued_behind_a_timer_render_reads_the_clock_once_it_may_run(rig):
    """The clock is read inside the render slot: the job behind the timer's
    render records a later instant and looks back to the timer's render."""
    inside, release = threading.Event(), threading.Event()
    rig.use(_blocking_calendar(inside, release))
    timers = rig.make_timers()
    rig.clock.set(SEVEN)
    timer = threading.Thread(target=lambda: timers._attempt(PERSONAL))  # noqa: SLF001
    timer.start()
    assert inside.wait(5)
    job = threading.Thread(
        target=lambda: rig.path._dashboard(rig.path.fresh(), PERSONAL, job=None)  # noqa: SLF001
    )
    job.start()
    time.sleep(0.1)
    rig.clock.advance(3 * MINUTE)
    release.set()
    timer.join(10)
    job.join(10)
    assert rig.records()[PERSONAL].last_success == SEVEN + 3 * MINUTE
    section = _enablement(rig.dashboard(PERSONAL).read_text(encoding="utf-8"))
    assert "since the last render at 2026-10-14 07:00" in section
    assert "clamped" not in section


def test_a_timer_render_and_a_job_never_overlap_their_prepares(rig, monkeypatch):
    from pm_ai.app import prompts

    real = prompts.prepare_dashboard
    active, most = [0], [0]
    lock = threading.Lock()

    def prepare(*args, **kwargs):
        with lock:
            active[0] += 1
            most[0] = max(most[0], active[0])
        try:
            time.sleep(0.15)
            return real(*args, **kwargs)
        finally:
            with lock:
                active[0] -= 1

    monkeypatch.setattr(prompts, "prepare_dashboard", prepare)
    timers = rig.make_timers()
    rig.clock.set(SEVEN)
    threads = [
        threading.Thread(target=lambda: timers._attempt(PERSONAL)),  # noqa: SLF001
        threading.Thread(
            target=lambda: rig.path._dashboard(rig.path.fresh(), PERSONAL, job=None)  # noqa: SLF001
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert most[0] == 1
    assert rig.path._render_slots_lock is not rig.path._slots_lock  # noqa: SLF001


def test_a_stepped_back_clock_makes_the_scope_due_with_a_clamped_window(rig):
    for scope in (PERSONAL, ALPHA):
        rig.daemon.storage.record_render(scope, at=SEVEN + DAY)
        _write(rig.dashboard(scope), b"a page from the future\n")
    rig.clock.set(SEVEN + HOUR)
    rig.drive(until=rig.clock())
    assert rig.saves() == [PERSONAL_SAVE, ALPHA_SAVE]
    section = _enablement(rig.dashboard(PERSONAL).read_text(encoding="utf-8"))
    assert "is later than this one, so the window was clamped" in section
    assert rig.records()[PERSONAL].last_success == SEVEN + HOUR


def test_a_job_renders_calendar_read_is_held_to_the_budget(rig):
    release = threading.Event()

    def hung() -> HarvestResult:
        release.wait(10)
        return HarvestResult(events=(), cursor=Cursor(), outcome=HarvestOutcome.EMPTY, answered_at=SEVEN)

    rig.use(Calendar("graph:work", hung))
    rig.path.render_budget = 0.2
    try:
        rig.path._dashboard(rig.path.fresh(), PERSONAL, job=None)  # noqa: SLF001
    finally:
        release.set()
    page = rig.dashboard(PERSONAL).read_text(encoding="utf-8")
    assert "did not answer within its 0.2 s budget" in page


def test_shutdown_during_a_job_renders_read_records_it_failed(rig):
    from pm_ai.app.concurrency import WriterStopped

    yesterday = b"yesterday's page\n"
    _write(rig.dashboard(PERSONAL), yesterday)
    inside, release = threading.Event(), threading.Event()
    rig.use(_blocking_calendar(inside, release))
    raised: list[BaseException] = []

    def job() -> None:
        try:
            rig.path._dashboard(rig.path.fresh(), PERSONAL, job=None)  # noqa: SLF001
        except BaseException as error:  # noqa: BLE001
            raised.append(error)

    worker = threading.Thread(target=job)
    worker.start()
    assert inside.wait(5)
    rig.writer.stop_accepting()
    rig.path.shutdown_pass()
    release.set()
    worker.join(10)
    record = rig.records()[PERSONAL]
    assert record.failing and "reading the calendar" in (record.reason or "")
    assert [type(error) for error in raised] == [WriterStopped]
    assert rig.dashboard(PERSONAL).read_bytes() == yesterday


def test_an_unreadable_records_store_is_recorded_for_every_scope_and_retried(rig, monkeypatch):
    def unreadable(scope):
        raise OSError("the store went away")

    monkeypatch.setattr(rig.daemon.storage, "last_render", unreadable)
    rig.clock.set(SEVEN)
    timers = rig.make_timers()
    rig.drive(until=SEVEN)
    assert rig.saves() == []
    assert timers.wake == SEVEN + MINUTE
    for scope in (PERSONAL, ALPHA):
        reason = rig.records()[scope].reason or ""
        assert "OSError" in reason and "the store went away" not in reason
    rig.drive(until=SEVEN + MINUTE, first=False)
    assert timers.wake == SEVEN + 3 * MINUTE, "the second wait doubles"
    monkeypatch.undo()
    rig.drive(until=SEVEN + 3 * MINUTE, first=False)
    assert rig.saves() == [PERSONAL_SAVE, ALPHA_SAVE]


def test_a_writer_stopped_while_reading_the_daemon_stops_the_round(rig, caplog):
    from pm_ai.app.concurrency import WriterStopped

    caplog.set_level(logging.WARNING, logger=DAEMON_LOGGER)

    def stopped() -> Daemon:
        raise WriterStopped("the writer has stopped")

    rig.path.fresh = stopped  # type: ignore[method-assign]
    timers = rig.make_timers()
    rig.clock.set(SEVEN)
    assert timers._attempt(PERSONAL) is RenderOutcome.STOPPED  # noqa: SLF001
    rig.drive(until=SEVEN)
    assert rig.saves() == []
    assert ALPHA not in rig.records(), "the round ended at the first stop"
    assert any("stopped with the daemon's writer" in r.getMessage() for r in caplog.records)


def test_a_round_that_keeps_failing_backs_off_doubling_to_15_minutes(rig, monkeypatch):
    timers = rig.make_timers()
    timers.nap = 10**6
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        rig.clock.advance(timedelta(seconds=seconds))
        if len(waits) == 6:
            raise asyncio.CancelledError

    async def broken() -> None:
        raise RuntimeError("an evaluation that cannot run")

    timers.sleep = sleep
    monkeypatch.setattr(timers, "tick", broken)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(timers.run())
    assert waits == [60, 120, 240, 480, 900, 900]


def test_a_failure_waiting_can_clear_is_retried_and_its_own_input_waits(rig, monkeypatch):
    from pm_ai.app import prompts

    real = prompts.prepare_dashboard
    raising: list[BaseException] = [OSError("a disk hiccup")]

    def prepare(*args, **kwargs):
        if raising:
            raise raising.pop(0)
        return real(*args, **kwargs)

    monkeypatch.setattr(prompts, "prepare_dashboard", prepare)
    timers = rig.make_timers()
    rig.clock.set(SEVEN)
    assert timers._attempt(PERSONAL) is RenderOutcome.BUSY  # noqa: SLF001
    raising.append(ConfigRefusedForTest("a refusal"))
    assert timers._attempt(PERSONAL) is RenderOutcome.FAILED  # noqa: SLF001


class ConfigRefusedForTest(Exception):
    """Neither waitable nor pm-ai's: a failure on the render's own input."""


def test_a_failure_reason_never_carries_foreign_text(rig, monkeypatch):
    from pm_ai.app import prompts

    def leaky(*args, **kwargs):
        raise RuntimeError("Bearer eyJ-a-provider-token")

    monkeypatch.setattr(prompts, "prepare_dashboard", leaky)
    rig.drive(until=SEVEN)
    reason = rig.records()[PERSONAL].reason or ""
    assert "RuntimeError" in reason and "eyJ" not in reason
    probe = rig.doctor()
    assert "RuntimeError" in probe.detail and "eyJ" not in probe.detail


def test_a_directory_where_the_page_belongs_is_named_without_its_path(rig):
    rig.dashboard(PERSONAL).mkdir(parents=True)
    rig.drive(until=SEVEN)
    reason = rig.records()[PERSONAL].reason or ""
    assert "a directory stands where daily_dashboard.md is declared a file" in reason
    assert str(rig.root) not in reason


def test_a_malformed_renders_row_is_skipped_not_raised(rig, caplog):
    store = rig.daemon.storage._store  # noqa: SLF001
    raw = sqlite3.connect(store)
    raw.execute(
        "INSERT INTO renders (scope, last_success, last_failure, reason) VALUES (?, ?, NULL, NULL)",
        ("personal", "not an instant"),
    )
    raw.commit()
    raw.close()
    caplog.set_level(logging.WARNING)
    assert rig.records()[PERSONAL].last_success is None, "the column dropped, the row kept"
    assert rig.daemon.storage.last_render(PERSONAL) is None
    assert any("not an instant" in r.getMessage() for r in caplog.records)
    rig.drive(until=SEVEN)
    assert rig.records()[PERSONAL].last_success == SEVEN
    assert rig.doctor().health is Health.OK


def test_a_zone_edited_between_evaluations_is_the_next_renders_zone(rig):
    """Row "Zone changed": rendered at Warsaw's 07:00, then the zone is edited;
    the next render is at New York's 07:00 and its page is New York's day."""
    rig.drive(until=SEVEN)
    rig.config("America/New_York")
    # 07:00 in New York on the 14th: no render since it, so it is due then.
    new_york_seven = datetime(2026, 10, 14, 11, 0, tzinfo=timezone.utc)
    rig.steps.clear()
    rig.drive(until=new_york_seven - MINUTE, first=False)
    assert rig.saves() == []
    rig.drive(until=new_york_seven, first=False)
    assert rig.saves() == [PERSONAL_SAVE, ALPHA_SAVE]
    page = rig.dashboard(PERSONAL).read_text(encoding="utf-8")
    # The window ends at the render's instant in New York's wall clock, and
    # starts at Warsaw's 07:00 render, also shown in New York's.
    assert "and 2026-10-14 07:00 — since the last render at 2026-10-14 01:00" in page


def test_a_hand_run_that_fails_leaves_the_scope_failing_with_its_reason(rig):
    _write(rig.daemon.storage.paths.resolve(PERSONAL, GOALS_ARTIFACT), MALFORMED_GOALS)
    from pm_ai.core.goal_register import MalformedGoals

    with pytest.raises(MalformedGoals):
        run_dashboard(rig.path.fresh(), now=SEVEN)
    record = rig.records()[PERSONAL]
    assert record.failing and "personal dashboard was not rendered" in (record.reason or "")


def test_a_claim_held_by_another_process_fails_the_attempt_naming_it(rig):
    """Row "Hand-run and daemon at once", across two real processes."""
    target = rig.dashboard(PERSONAL)
    target.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import sys
                from pathlib import Path
                from pm_ai.platform.claims import exclusive
                with exclusive(Path({str(target)!r})):
                    print("held", flush=True)
                    sys.stdin.readline()
                """
            ),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        rig.drive(until=SEVEN)
        record = rig.records()[PERSONAL]
        assert record.failing and "claimed by another pm-ai process" in (record.reason or "")
        assert not target.exists()
    finally:
        holder.communicate("\n", timeout=10)


def test_serve_attaches_the_render_timer_and_cancels_it_at_stop(tmp_path, monkeypatch):
    """`serve` builds `RenderTimers`, the lifespan starts its `run()`, and the
    stop cancels it — the page is rendered by the real daemon on its start."""
    import socket

    from pm_ai.app import daemon as daemon_module

    daemon = build(tmp_path, APPLICATION, keychain=_NoKey(), encryption_disabled=True)
    # A zone where it is about noon now, so the start is past 07:00.
    offset = 12 - datetime.now(timezone.utc).hour
    zone = "Etc/GMT" if offset == 0 else f"Etc/GMT{'-' if offset > 0 else '+'}{abs(offset)}"
    _write(
        daemon.storage.paths.resolve(APPLICATION, "config.toml"),
        f'display_timezone = "{zone}"\n'.encode(),
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    daemon.config = replace(daemon.config, daemon_port=port)
    cancelled = threading.Event()

    class Timers(RenderTimers):
        async def run(self) -> None:
            try:
                await super().run()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    monkeypatch.setattr(daemon_module, "RenderTimers", Timers)
    servers: list[Any] = []
    real_class = daemon_module._server_class  # noqa: SLF001

    def capturing(uvicorn: Any) -> Any:
        base = real_class(uvicorn)

        class Captured(base):  # type: ignore[misc, valid-type]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                servers.append(self)

        return Captured

    monkeypatch.setattr(daemon_module, "_server_class", capturing)
    returned: list[int] = []
    serving = threading.Thread(
        target=lambda: returned.append(
            daemon_module.serve(daemon, probes=lambda: Report(()), announce=lambda line: None)
        )
    )
    serving.start()
    deadline = time.monotonic() + 20
    rendered = None
    while time.monotonic() < deadline:
        try:
            rendered = daemon.storage.last_render(PERSONAL)
        except Exception:  # noqa: BLE001 — the store may not be open yet
            rendered = None
        if rendered is not None:
            break
        time.sleep(0.05)
    assert rendered is not None
    assert daemon.storage.paths.resolve(PERSONAL, DASHBOARD_ARTIFACT).is_file()
    while not servers:
        time.sleep(0.01)
    servers[0].should_exit = True
    serving.join(20)
    assert returned == [0]
    assert cancelled.is_set(), "the render task was cancelled at stop"


# ── Second review round ──────────────────────────────────────────────────────


def test_an_overdue_retry_wakes_the_timer_at_once(rig):
    timers = rig.make_timers()
    rig.clock.set(SEVEN + 30 * MINUTE)
    from pm_ai.app.ticks import _Plan, _Retry

    timers._retries[PERSONAL] = _Retry(day=SEVEN, due=SEVEN + MINUTE, failures_in_a_row=1)  # noqa: SLF001
    assert timers._next_wake(_Plan(WARSAW, ())) == SEVEN + 30 * MINUTE  # noqa: SLF001


def test_a_failure_older_than_a_newer_success_does_not_read_failing(rig):
    from pm_ai.domain.renders import RenderRecord

    stale = RenderRecord(PERSONAL, SEVEN + HOUR, SEVEN, "an older failure")
    assert not stale.failing
    assert RenderRecord(PERSONAL, SEVEN, SEVEN + HOUR, "newer").failing
    assert RenderRecord(PERSONAL, None, SEVEN, "only").failing
    probe = renders_recorded([PERSONAL], [stale], zone_set=True)
    assert probe.health is Health.OK and "FAILING" not in probe.detail


def test_a_busy_refusal_recorded_by_a_hand_run_is_retried_on_the_waits(rig):
    """Rendered at 07:00; a hand-run at 08:00 meets another command's claim; the
    daemon retries it a minute later, though today was already rendered."""
    rig.drive(until=SEVEN)
    rig.clock.set(SEVEN + HOUR)
    held, release, done = threading.Event(), threading.Event(), threading.Event()

    def hold() -> None:
        with rig.daemon.storage.exclusive(scope=PERSONAL, artifact=DASHBOARD_ARTIFACT):
            held.set()
            release.wait(10)
        done.set()

    holder = threading.Thread(target=hold)
    holder.start()
    assert held.wait(5)
    from pm_ai.ports import ArtifactBusy

    with pytest.raises(ArtifactBusy):
        run_dashboard(rig.path.fresh(), now=rig.clock())
    record = rig.records()[PERSONAL]
    assert record.failing and record.retryable
    release.set()
    assert done.wait(5)
    rig.steps.clear()
    rig.drive(until=rig.clock())
    assert rig.saves() == [], "the first wait is a minute from the refusal"
    assert rig.timers is not None and rig.timers.wake == SEVEN + HOUR + MINUTE
    rig.drive(until=SEVEN + HOUR + MINUTE, first=False)
    assert rig.saves() == [PERSONAL_SAVE]
    assert rig.records()[PERSONAL].last_success == SEVEN + HOUR + MINUTE
    assert "retried on its own" not in rig.doctor().detail


def test_doctor_marks_a_waitable_failure_as_retried_on_its_own(rig):
    rig.daemon.storage.record_render_failure(PERSONAL, "busy", at=SEVEN, retryable=True)
    probe = rig.doctor()
    assert "personal: last render never, FAILING, last failed at" in probe.detail
    assert "retried on its own: busy" in probe.detail
    assert "1, 2, 4" in probe.remediation


def test_one_mark_failing_does_not_undo_the_others(rig, monkeypatch):
    """The shutdown step records each unfinished render in its own statement."""
    storage = rig.daemon.storage
    at = SEVEN
    real_scope = PERSONAL
    with monkeypatch.context() as patch:
        original = storage._transaction  # noqa: SLF001

        @contextlib.contextmanager
        def flaky():
            with original() as db:
                real_execute = db.execute

                class Db:
                    def execute(self, sql, *params):
                        if params and params[0] and params[0][0] == str(real_scope):
                            raise sqlite3.OperationalError("this one statement failed")
                        return real_execute(sql, *params)

                yield Db()

        patch.setattr(storage, "_transaction", flaky)
        recorded = storage.record_render_failures(
            [(PERSONAL, "a"), (ALPHA, "b")], at=at
        )
    assert recorded == (ALPHA,)
    records = rig.records()
    assert PERSONAL not in records
    assert records[ALPHA].reason == "b"


def test_a_render_waiting_for_its_slot_at_shutdown_is_marked(rig, monkeypatch):
    storage = rig.daemon.storage
    marked: list[str] = []
    real_marks = storage.record_render_failures

    def capture(failures, *, at):
        marked.extend(reason for _, reason in failures)
        return real_marks(failures, at=at)

    monkeypatch.setattr(storage, "record_render_failures", capture)
    inside, release = threading.Event(), threading.Event()
    rig.use(_blocking_calendar(inside, release))
    first = threading.Thread(
        target=lambda: _quietly(lambda: rig.path._dashboard(rig.path.fresh(), PERSONAL, job=None))  # noqa: SLF001
    )
    first.start()
    assert inside.wait(5)
    second = threading.Thread(
        target=lambda: _quietly(lambda: rig.path._dashboard(rig.path.fresh(), PERSONAL, job=None))  # noqa: SLF001
    )
    second.start()
    deadline = time.monotonic() + 5
    while len(rig.path._renders) < 2:  # noqa: SLF001
        assert time.monotonic() < deadline
        time.sleep(0.01)
    rig.writer.stop_accepting()
    rig.path.shutdown_pass()
    release.set()
    first.join(10)
    second.join(10)
    assert len(marked) == 2, "both marked, in one step"
    assert any("reading the calendar" in reason for reason in marked)
    assert any("waiting for another render of it to finish" in reason for reason in marked)
    assert rig.steps.count("mark an unfinished render failed") == 1
    assert rig.records()[PERSONAL].failing


def _quietly(work: Callable[[], Any]) -> None:
    try:
        work()
    except BaseException:  # noqa: BLE001 — the writer stopped under it
        pass


def test_a_locked_keychain_is_waited_out(rig, monkeypatch):
    from pm_ai.app import prompts
    from pm_ai.ports import KeychainUnavailable

    def locked(*args, **kwargs):
        raise KeychainUnavailable("the keychain is locked")

    monkeypatch.setattr(prompts, "prepare_dashboard", locked)
    timers = rig.make_timers()
    rig.clock.set(SEVEN)
    assert timers._attempt(PERSONAL) is RenderOutcome.BUSY  # noqa: SLF001
    assert rig.records()[PERSONAL].retryable


def test_last_render_with_no_store_or_table_is_none_and_a_transient_error_is_waitable(
    rig, monkeypatch
):
    from pm_ai.storage.service import OperationalStoreBusy

    storage = rig.daemon.storage
    assert storage.last_render(PERSONAL) is None

    @contextlib.contextmanager
    def failing_reader():
        raise sqlite3.OperationalError("disk I/O error")
        yield  # pragma: no cover

    monkeypatch.setattr(storage, "_reader", failing_reader)
    with pytest.raises(OperationalStoreBusy):
        storage.last_render(PERSONAL)

    @contextlib.contextmanager
    def no_table():
        raise sqlite3.OperationalError("no such table: renders")
        yield  # pragma: no cover

    monkeypatch.setattr(storage, "_reader", no_table)
    assert storage.last_render(PERSONAL) is None


def test_a_config_refused_inside_a_render_is_tried_at_the_next_evaluation(rig, monkeypatch):
    from pm_ai.app import prompts
    from pm_ai.core.config import ConfigRefused

    real = prompts.prepare_dashboard
    refusals = [ConfigRefused("config.toml changed under the render")]

    def prepare(*args, **kwargs):
        if refusals:
            raise refusals.pop(0)
        return real(*args, **kwargs)

    monkeypatch.setattr(prompts, "prepare_dashboard", prepare)
    rig.drive(until=SEVEN)
    assert rig.timers is not None
    assert PERSONAL not in rig.timers._given_up  # noqa: SLF001
    assert rig.records()[PERSONAL].failing
    rig.drive(until=SEVEN + timedelta(minutes=15), first=False)
    assert rig.records()[PERSONAL].last_success == SEVEN + timedelta(minutes=15)


def test_a_file_that_goes_missing_clears_a_given_up_scope(rig):
    _write(rig.daemon.storage.paths.resolve(PERSONAL, GOALS_ARTIFACT), MALFORMED_GOALS)
    _write(rig.dashboard(PERSONAL), b"yesterday's page\n")
    rig.drive(until=SEVEN)
    assert rig.timers is not None and PERSONAL in rig.timers._given_up  # noqa: SLF001
    rig.daemon.storage.paths.resolve(PERSONAL, GOALS_ARTIFACT).unlink()
    rig.dashboard(PERSONAL).unlink()
    rig.steps.clear()
    rig.drive(until=SEVEN + timedelta(minutes=15), first=False)
    assert PERSONAL_SAVE in rig.steps
    assert rig.dashboard(PERSONAL).is_file()


def test_an_unreadable_success_keeps_the_rows_valid_failure(rig):
    store = rig.daemon.storage._store  # noqa: SLF001
    raw = sqlite3.connect(store)
    raw.execute(
        "INSERT INTO renders (scope, last_success, last_failure, reason, retryable) "
        "VALUES (?, ?, ?, ?, 0)",
        ("personal", "garbage", SEVEN.isoformat(), "the goals file is malformed"),
    )
    raw.commit()
    raw.close()
    record = rig.records()[PERSONAL]
    assert record.last_success is None and record.failing
    assert record.reason == "the goals file is malformed"
    assert "the goals file is malformed" in rig.doctor().detail


def _artifact_files(rig: Rig) -> set[Path]:
    return {path for path in rig.root.rglob("daily_dashboard.md")}


def test_a_git_answer_not_minted_by_this_store_for_this_artifact_is_refused(rig, tmp_path):
    from pm_ai.storage.service import GitExclusion, GitNotAsked

    storage = rig.daemon.storage
    other = build(
        tmp_path / "other",
        APPLICATION,
        keychain=_NoKey(),
        encryption_disabled=True,
        vcs=Vcs(tmp_path / "other" / "projects" / "alpha"),
    )
    answers = {
        "hand-built": GitExclusion(PERSONAL, DASHBOARD_ARTIFACT),
        "another scope": storage.ask_git_exclusion(scope=ALPHA, artifact=DASHBOARD_ARTIFACT),
        "another artifact": storage.ask_git_exclusion(scope=PERSONAL, artifact="strategic_goals.md"),
        "another store": other.storage.ask_git_exclusion(scope=PERSONAL, artifact=DASHBOARD_ARTIFACT),
    }
    try:
        for name, answer in answers.items():
            with pytest.raises(GitNotAsked):
                storage.write_artifact(
                    b"page\n", scope=PERSONAL, artifact=DASHBOARD_ARTIFACT, exclusion=answer
                )
            assert not rig.dashboard(PERSONAL).exists(), name
        good = storage.ask_git_exclusion(scope=PERSONAL, artifact=DASHBOARD_ARTIFACT)
        storage.write_artifact(b"page\n", scope=PERSONAL, artifact=DASHBOARD_ARTIFACT, exclusion=good)
        assert rig.dashboard(PERSONAL).read_bytes() == b"page\n"
    finally:
        other.storage.close()
