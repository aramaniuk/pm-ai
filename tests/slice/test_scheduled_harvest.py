"""Story 9a — the daemon harvests every connector on a schedule, one test per matrix row.

The timers (`pm_ai.app.ticks.HarvestTimers`) are driven with an injected clock,
draw and sleep against a real composed daemon over a throwaway root: the real
single writer and `operational.db`, the real writer thread and I/O pool, the
real request path's refresh and credential stores. Only the connectors are
stood in — scripted answers, so no row opens a socket — except where a row is
about the list the daemon reads from storage, which then runs for real.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from pm_ai.app import daemon as daemon_module
from pm_ai.app import ticks as ticks_module
from pm_ai.app.concurrency import IOPool, StepKind, WriterStopped, WriterThread
from pm_ai.app.entry import DAEMON_BOUNDS
from pm_ai.app.pipelines import (
    AttemptOutcome,
    FetchAbandoned,
    HarvestAttempt,
    harvest_once,
    instance_scope,
)
from pm_ai.app.prompts import DAEMON_LOGGER, RequestPath
from pm_ai.app.ticks import HarvestTimers, _Running
from pm_ai.app.wiring import Daemon, build
from pm_ai.connectors.graph import (
    CLIENT_ID_KEY,
    REACH_BACK_KEY,
    WIDTH_KEY,
    CategoryScopes,
    GraphConnector,
)
from pm_ai.connectors.graph.auth import SealedCredential
from pm_ai.connectors.graph.calendar import GraphCalendarFetch, WindowPolicy
from pm_ai.connectors.graph.client import GraphClient, GraphRequest, GraphResponse
from pm_ai.core.cadence import CYCLE
from pm_ai.core.config import Config
from pm_ai.core.connector_enrolment import enrol_connector, stored_credentials
from pm_ai.core.meeting_records import capture_name
from pm_ai.core.project_registry import ProjectEntry, render_registry
from pm_ai.domain.harvest import Cursor, HarvestFailure, HarvestOutcome, HarvestResult
from pm_ai.domain.health import Health, Probe, Report
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.lifecycle import CoverageWindow
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.storage_tiers import CAPTURES
from pm_ai.domain.transcripts import (
    CaptureFormat,
    TranscriptAbsent,
    TranscriptCapture,
    TranscriptCaptured,
    TranscriptOutcome,
    TranscriptStateKind,
)
from pm_ai.domain.vcs import TrackingVerdict
from pm_ai.platform.claims import hold_for_life
from pm_ai.platform.doctor import HARVESTS_PROBE, harvests_recorded
from pm_ai.ports import ArtifactBusy, DaemonRefused, KeyNotFound
from pm_ai.storage.service import StorageService
from pm_ai.surfaces.cli.dispatch import RESTART_SENTENCE

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)
APPLICATION = DataScope(ScopeKind.APPLICATION)
PERSONAL = DataScope(ScopeKind.PERSONAL)
ALPHA = DataScope(ScopeKind.PROJECT, "alpha")
SECRET = "eyJ0eXAi.secret-refresh-token-value"


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
    """The one clock the daemon, the storage and the timers read; thread-safe."""

    def __init__(self, at: datetime) -> None:
        self._at = at
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self._at

    def advance(self, by: timedelta) -> None:
        with self._lock:
            self._at += by


@dataclass
class RecordingVcs:
    """A git that says every path is ignored, and remembers which thread asked."""

    root: Path
    threads: list[str] = field(default_factory=list)

    def working_tree(self, path: Path) -> Path | None:
        self.threads.append(threading.current_thread().name)
        return self.root if self.root in path.parents or path == self.root else None

    def tracking(self, path: Path, *, repository: Path) -> TrackingVerdict:
        self.threads.append(threading.current_thread().name)
        return TrackingVerdict(ignored=True)


@dataclass
class Rig:
    root: Path
    clock: Clock
    daemon: Daemon
    writer: WriterThread
    pool: IOPool
    path: RequestPath
    vcs: RecordingVcs
    draws: list[float] = field(default_factory=list)
    active: int = 0
    overlap: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    steps: list[str] = field(default_factory=list)
    timers: HarvestTimers | None = None
    started: bool = False

    def use(self, *connectors: Any, **extra: Any) -> None:
        """Every fresh read sees exactly these connectors, filed where each says."""
        table = {connector.instance: connector for connector in connectors}
        scopes = {
            connector.instance: getattr(connector, "scope", PERSONAL) for connector in connectors
        }
        self.path.fresh = lambda: replace(  # type: ignore[method-assign]
            self.daemon, connectors=dict(table), instance_scopes=scopes, **extra
        )

    def make_timers(self, *, budget: float = 30.0) -> HarvestTimers:
        async def sleep(seconds: float) -> None:
            self.clock.advance(timedelta(seconds=seconds))

        def draw() -> float:
            return self.draws.pop(0) if self.draws else 0.5

        self.timers = HarvestTimers(
            requests=self.path, now=self.clock, draw=draw, sleep=sleep, budget=budget
        )
        self.started = False
        return self.timers

    def drive(self, until: timedelta) -> None:
        """Run the timers from their start until nothing more is due by `T0 + until`."""
        timers = self.timers or self.make_timers()
        limit = T0 + until

        async def go() -> None:
            if not self.started:
                await timers.start()
                self.started = True
            while timers._next_wake() <= limit:  # noqa: SLF001
                await timers._wait()  # noqa: SLF001
                await timers.tick()

        asyncio.run(go())

    def records(self) -> dict[str, Any]:
        return {record.instance: record for record in self.daemon.storage.harvest_records()}

    def doctor(self, *instances: str) -> Probe:
        return harvests_recorded(instances, self.daemon.storage.harvest_records())

    def entered(self) -> None:
        with self.lock:
            self.active += 1
            self.overlap = max(self.overlap, self.active)

    def left(self) -> None:
        with self.lock:
            self.active -= 1


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _registry(rig_root: Path, daemon: Daemon, *projects: str) -> None:
    _write(
        daemon.storage.paths.resolve(APPLICATION, "projects.toml"),
        render_registry({pid: ProjectEntry(path=rig_root / pid) for pid in projects}),
    )


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    clock = Clock(T0)
    vcs = RecordingVcs(tmp_path / "projects")
    daemon = build(
        tmp_path,
        APPLICATION,
        watched=("alpha",),
        keychain=_NoKey(),
        encryption_disabled=True,
        now=clock,
        vcs=vcs,
    )
    _write(daemon.storage.paths.resolve(APPLICATION, "config.toml"), b'display_timezone = "UTC"\n')
    _registry(tmp_path, daemon, "alpha")
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


# ── Scripted connectors ──────────────────────────────────────────────────────


def ok(token: bytes = b"position") -> Callable[[Any, Cursor], HarvestResult]:
    def answer(connector: Any, cursor: Cursor) -> HarvestResult:
        now = connector.rig.clock()
        return HarvestResult(
            events=(),
            cursor=Cursor(token),
            outcome=HarvestOutcome.EMPTY,
            coverage=CoverageWindow(connector.instance, now, now),
            answered_at=now,
        )

    return answer


def failing(
    reason: str, *, retryable: bool, retry_after: timedelta | None = None
) -> Callable[[Any, Cursor], HarvestResult]:
    def answer(connector: Any, cursor: Cursor) -> HarvestResult:
        return HarvestResult(
            events=(),
            cursor=cursor,
            outcome=HarvestOutcome.FAILED,
            failure=HarvestFailure(reason, retryable=retryable, retry_after=retry_after),
        )

    return answer


class Scripted:
    """A connector answering each harvest with the next scripted answer.

    Records when it was asked and the cursor it was handed, and counts how
    many harvests are inside it at once. `takes` is how long, on the shared
    clock, each harvest takes.
    """

    def __init__(
        self,
        rig: Rig,
        instance: str,
        *answers: Any,
        scope: DataScope = PERSONAL,
        takes: timedelta = 2 * MINUTE,
    ) -> None:
        self.rig = rig
        self.instance = instance
        self.scope = scope
        self.answers = list(answers)
        self.takes = takes
        self.asked: list[datetime] = []
        self.cursors: list[Cursor] = []

    def harvest(self, cursor: Cursor) -> HarvestResult:
        self.rig.entered()
        try:
            self.asked.append(self.rig.clock())
            self.cursors.append(cursor)
            answer = self.answers.pop(0) if self.answers else ok()
            if isinstance(answer, BaseException):
                raise answer
            self.rig.clock.advance(self.takes)
            return answer(self, cursor)
        finally:
            self.rig.left()

    def emits(self) -> frozenset:
        return frozenset()

    def sample_events(self) -> tuple:
        return ()

    def check_health(self) -> Probe:
        return Probe(self.instance, Health.OK, "scripted")


def _gaps(asked: Sequence[datetime]) -> list[timedelta]:
    return [later - earlier for earlier, later in zip(asked, asked[1:])]


# ── Daemon start, and the list read again each cycle ─────────────────────────


def test_at_start_two_instances_are_spread_and_each_is_due_a_cycle_after_it_finished(rig):
    a, b = Scripted(rig, "graph:a"), Scripted(rig, "graph:b")
    rig.use(a, b)
    rig.make_timers()
    rig.draws = [0.5, 0.0]
    rig.drive(until=20 * MINUTE)

    assert a.asked == [T0]
    assert b.asked == [T0 + 7.5 * MINUTE], "one after the other across the first 15 minutes"
    due = rig.timers.due
    # Each harvest took two minutes; the next is a cycle after it finished,
    # moved by the draw: 0.5 is exactly on it, 0.0 fifteen minutes early.
    assert due["graph:a"] == T0 + 2 * MINUTE + CYCLE
    assert due["graph:b"] == T0 + 9.5 * MINUTE + CYCLE - 15 * MINUTE
    assert rig.overlap == 1


def test_the_jitter_is_drawn_again_each_cycle_so_instances_drift_apart(rig):
    a = Scripted(rig, "graph:a", takes=timedelta(0))
    rig.use(a)
    rig.make_timers()
    rig.draws = [0.0, 0.9999, 0.5]
    rig.drive(until=3 * CYCLE + 30 * MINUTE)

    assert _gaps(a.asked)[:2] == [CYCLE - 15 * MINUTE, CYCLE + 15 * MINUTE * (2 * 0.9999 - 1)]


def test_an_instance_enrolled_after_start_gets_a_timer_at_the_next_list_read(rig, monkeypatch):
    """The real list, read from storage: `connector add` while the daemon runs,
    filing into the one project it loaded at start. With only the built-in,
    which is not set up, the list is read every minute — so the new instance
    is due at the next read, without a restart."""
    attempted: list[tuple[str, datetime]] = []

    def attempt(daemon, instance, **_):
        attempted.append((instance, rig.clock()))
        return HarvestAttempt(instance, AttemptOutcome.SUCCEEDED)

    monkeypatch.setattr(ticks_module, "harvest_once", attempt)
    rig.make_timers()
    rig.drive(until=20 * MINUTE)
    assert [name for name, _ in attempted] == ["gitlab:alpha"]

    enrol_connector(
        rig.daemon.storage,
        system="gitlab",
        instance="gitlab:web",
        credential="glpat-0123456789",
        probe=lambda system, credential: "accepted",
        project="group/web",
    )
    rig.drive(until=21 * MINUTE)

    assert attempted[1:] == [("gitlab:web", T0 + 21 * MINUTE)], "at the next read"
    assert "gitlab:web" in rig.timers.due, "a timer without a restart"
    # Something is set up now: the list is read again within a cycle.
    assert rig.timers.reread_at == T0 + 21 * MINUTE + CYCLE


def test_a_row_for_a_project_enrolled_after_start_is_skipped_and_named_each_cycle(
    rig, monkeypatch, caplog
):
    caplog.set_level(logging.WARNING, logger=DAEMON_LOGGER)
    attempted: list[str] = []
    monkeypatch.setattr(
        ticks_module,
        "harvest_once",
        lambda daemon, instance, **_: (
            attempted.append(instance) or HarvestAttempt(instance, AttemptOutcome.SUCCEEDED)
        ),
    )
    rig.make_timers()
    rig.drive(until=20 * MINUTE)
    # `project add … beta`, then `connector add gitlab gitlab:beta`.
    _registry(rig.root, rig.daemon, "alpha", "beta")
    enrol_connector(
        rig.daemon.storage,
        system="gitlab",
        instance="gitlab:beta",
        credential="glpat-0123456789",
        probe=lambda system, credential: "accepted",
        project="group/beta",
    )
    caplog.clear()
    rig.drive(until=2 * CYCLE + 40 * MINUTE)

    assert "gitlab:beta" not in attempted and "gitlab:beta" not in rig.timers.due
    named = [r for r in caplog.records if "gitlab:beta" in r.getMessage()]
    assert len(named) >= 2, "named at each cycle until a restart"
    assert all(RESTART_SENTENCE in r.getMessage() for r in named)
    # Once a cycle, not at every read.
    assert all(
        later.created >= earlier.created for earlier, later in zip(named, named[1:])
    )
    assert len(named) <= 3
    assert "gitlab:beta" not in rig.records(), "nothing is saved for it"


def test_a_second_daemon_is_refused_naming_the_lifetime_lock_whatever_its_port(tmp_path, monkeypatch):
    daemon = build(
        tmp_path, APPLICATION, keychain=_NoKey(), encryption_disabled=True, now=Clock(T0)
    )
    lock = daemon.storage.daemon_lock_path()
    assert lock.parent == daemon.storage.api_token_path().parent
    first = hold_for_life(lock)
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        daemon.config = replace(daemon.config, daemon_port=port)
        def writers() -> int:
            return sum(t.name == "pm-ai-writer" for t in threading.enumerate())

        attached: list[object] = []

        class Recording(HarvestTimers):
            def __init__(self, **options: Any) -> None:
                attached.append(self)
                super().__init__(**options)

        monkeypatch.setattr(daemon_module, "HarvestTimers", Recording)
        before = writers()
        with pytest.raises(DaemonRefused) as refused:
            daemon_module.serve(daemon, probes=lambda: Report(()))
        assert attached == [], "no timer was attached"
        assert str(lock) in str(refused.value)
        assert "whatever port" in str(refused.value)
        assert writers() == before, "no writer thread"
        # The port it bound was given back.
        with socket.socket() as again:
            again.bind(("127.0.0.1", port))
    finally:
        first.release()
    # Released, the next daemon may take it.
    hold_for_life(lock).release()


# ── One harvest, its steps and its save ──────────────────────────────────────


def test_a_success_saves_position_coverage_and_no_failure_in_one_claimed_step(rig):
    a = Scripted(rig, "graph:a", ok(b"after"))
    rig.use(a)
    rig.make_timers()
    rig.drive(until=MINUTE)

    storage = rig.daemon.storage
    assert storage.load_cursor("graph:a") == Cursor(b"after")
    assert storage.coverage_windows("graph:a") == [(T0 + 2 * MINUTE, T0 + 2 * MINUTE)]
    assert storage.harvest_failure("graph:a") is None
    record = rig.records()["graph:a"]
    assert record.last_attempt == record.last_success == T0 + 2 * MINUTE
    # The position, the coverage, "no failure" and the attempt: one step.
    assert rig.steps.count("save a connector's position") == 1
    assert rig.steps[-2:] == ["save a connector's position", "remove old coverage records"]
    probe = rig.doctor("graph:a")
    assert probe.name == HARVESTS_PROBE and probe.health is Health.OK
    assert "last attempt 2026-10-14T09:02:00+00:00" in probe.detail
    assert "last success 2026-10-14T09:02:00+00:00" in probe.detail


def _enrol_gitlab(rig: Rig, instance: str = "gitlab:alpha", credential: str = "token-one") -> None:
    enrol_connector(
        rig.daemon.storage,
        system="gitlab",
        instance=instance,
        credential=credential,
        probe=lambda system, credential: "accepted",
    )


def _held(rig: Rig, instance: str = "gitlab:alpha") -> str:
    return stored_credentials(rig.daemon.storage)[instance]["credential"]


def test_a_token_refreshed_mid_harvest_is_saved_before_the_harvests_own_save(rig):
    _enrol_gitlab(rig)
    store = rig.path._store_for("gitlab:alpha", "gitlab")  # noqa: SLF001

    def refreshing(connector, cursor):
        with store.exclusive():
            assert store.read() == "token-one"
            store.write("token-two")
        return ok()(connector, cursor)

    rig.use(Scripted(rig, "gitlab:alpha", refreshing, scope=ALPHA))
    rig.make_timers()
    rig.drive(until=MINUTE)

    assert _held(rig) == "token-two"
    saved = rig.steps.index("save gitlab:alpha's refreshed credential")
    assert saved < rig.steps.index("save a connector's position")


def test_a_token_whose_entry_changed_meanwhile_is_dropped_and_the_harvest_goes_on(rig, caplog):
    caplog.set_level(logging.WARNING, logger=DAEMON_LOGGER)
    _enrol_gitlab(rig)
    store = rig.path._store_for("gitlab:alpha", "gitlab")  # noqa: SLF001
    other = rig.path._store_for("gitlab:alpha", "gitlab")  # noqa: SLF001

    def raced(connector, cursor):
        with store.exclusive():
            store.read()
            # A sign-in saved a newer one between the read and the save.
            holder = threading.Thread(target=lambda: _replace(other, "token-theirs"))
            holder.start()
            holder.join()
            store.write("token-mine")
        return ok()(connector, cursor)

    rig.use(Scripted(rig, "gitlab:alpha", raced, scope=ALPHA))
    rig.make_timers()
    rig.drive(until=MINUTE)

    assert _held(rig) == "token-theirs"
    assert any("dropped" in r.getMessage() for r in caplog.records)
    assert rig.daemon.storage.load_cursor("gitlab:alpha") == Cursor(b"position")


def _replace(store, credential: str) -> None:
    with store.exclusive():
        store.read()
        store.write(credential)


def test_a_fetch_past_its_budget_is_abandoned_and_fails_retryable_naming_the_last_step(rig):
    _enrol_gitlab(rig)
    store = rig.path._store_for("gitlab:alpha", "gitlab")  # noqa: SLF001
    finished = threading.Event()

    def slow(connector, cursor):
        time.sleep(0.6)
        # A credential save the abandoned fetch hands over is still made.
        with store.exclusive():
            store.read()
            store.write("token-late")
        finished.set()
        return ok(b"too-late")(connector, cursor)

    rig.use(Scripted(rig, "gitlab:alpha", slow, scope=ALPHA, takes=timedelta(0)))
    rig.make_timers(budget=0.2)
    rig.drive(until=timedelta(seconds=30))

    failure = rig.daemon.storage.harvest_failure("gitlab:alpha")
    assert failure is not None and failure.retryable
    assert "did not finish within its budget" in failure.reason and "timeout" in failure.reason
    assert "read a connector's position" in failure.reason
    assert rig.timers.due["gitlab:alpha"] == T0 + MINUTE
    assert finished.wait(5)
    rig.writer.call(lambda: None, label="drain")
    assert _held(rig) == "token-late"
    assert rig.daemon.storage.load_cursor("gitlab:alpha") == Cursor(), "nothing saved after"
    assert rig.daemon.storage.coverage_windows("gitlab:alpha") == []


def test_a_claimed_step_handed_from_an_abandoned_fetch_is_refused_and_logged(rig, caplog):
    caplog.set_level(logging.WARNING, logger=DAEMON_LOGGER)
    timers = rig.make_timers(budget=0.1)
    running = _Running("graph:a")
    ran = threading.Event()
    late: list[BaseException] = []
    done = threading.Event()

    def fetch() -> None:
        time.sleep(0.4)
        try:
            timers._hand(running, ran.set, "save the capture")  # noqa: SLF001
        except BaseException as refused:  # noqa: BLE001
            late.append(refused)
        done.set()

    with pytest.raises(FetchAbandoned, match="budget"):
        timers._bounded(running, fetch, "graph:a's transcript fetch")  # noqa: SLF001
    assert done.wait(5)
    assert not ran.is_set()
    assert [type(error) for error in late] == [WriterStopped]
    assert any("'save the capture'" in r.getMessage() and "abandoned" in r.getMessage() for r in caplog.records)


def test_a_connector_that_raises_is_recorded_by_type_with_the_position_unchanged(rig, caplog):
    caplog.set_level(logging.DEBUG)
    a = Scripted(rig, "graph:a", ok(b"before"), RuntimeError(f"401 with token={SECRET}"))
    rig.use(a)
    rig.make_timers()
    rig.drive(until=CYCLE + 20 * MINUTE)

    assert a.cursors[-1] == Cursor(b"before")
    assert rig.daemon.storage.load_cursor("graph:a") == Cursor(b"before")
    failure = rig.daemon.storage.harvest_failure("graph:a")
    assert failure is not None and not failure.retryable, "treated as needing a human"
    assert "RuntimeError" in failure.reason
    # No secret reaches a recorded reason or a log line.
    assert SECRET not in failure.reason
    assert all(SECRET not in r.getMessage() for r in caplog.records)
    assert rig.doctor("graph:a").health is Health.FAILING


# ── The waits ────────────────────────────────────────────────────────────────


def test_transient_failures_wait_one_then_two_minutes_and_a_success_clears_them(rig):
    a = Scripted(
        rig,
        "graph:a",
        failing("graph:a's provider answered 503", retryable=True),
        failing("graph:a's provider answered 503", retryable=True),
        ok(),
        takes=timedelta(0),
    )
    rig.use(a)
    rig.make_timers()
    rig.drive(until=30 * MINUTE)

    assert _gaps(a.asked) == [MINUTE, 2 * MINUTE]
    assert rig.daemon.storage.harvest_failure("graph:a") is None
    assert rig.timers.due["graph:a"] == T0 + 3 * MINUTE + CYCLE


def test_doctor_warns_while_a_retryable_failure_is_pending(rig):
    rig.use(Scripted(rig, "graph:a", failing("graph:a's provider answered 503", retryable=True)))
    rig.make_timers()
    rig.drive(until=timedelta(seconds=30))
    probe = rig.doctor("graph:a")
    assert probe.health is Health.WARNING
    assert "retried on its own" in probe.detail and "503" in probe.detail


def test_a_providers_wait_is_honoured_and_survives_a_restart(rig):
    asked = failing(
        "graph:a was throttled", retryable=True, retry_after=timedelta(seconds=600)
    )
    a = Scripted(rig, "graph:a", asked, takes=timedelta(0))
    rig.use(a)
    rig.make_timers()
    rig.drive(until=timedelta(seconds=30))
    assert rig.timers.due["graph:a"] == T0 + 10 * MINUTE, "never sooner than asked"

    # A restart three minutes in waits the remaining seven.
    rig.clock.advance(3 * MINUTE - (rig.clock() - T0))
    rig.make_timers()
    rig.drive(until=5 * MINUTE)
    assert rig.timers.due["graph:a"] == T0 + 10 * MINUTE
    rig.drive(until=11 * MINUTE)
    assert a.asked == [T0, T0 + 10 * MINUTE]


def test_nine_transient_failures_in_a_row_wait_doubling_to_the_cap(rig):
    transient = [failing("503", retryable=True) for _ in range(11)]
    a = Scripted(rig, "graph:a", *transient, takes=timedelta(0))
    rig.use(a)
    rig.make_timers()
    rig.drive(until=timedelta(hours=14))

    minutes = [gap / MINUTE for gap in _gaps(a.asked)]
    assert minutes[:10] == [1, 2, 4, 8, 16, 32, 64, 128, 240, 240]


def test_a_permanent_failure_waits_for_the_next_regular_cycle_and_a_success_clears_it(rig):
    expired = failing(
        "graph:a's sign-in has expired; run `pm-ai connector sign-in graph:a`",
        retryable=False,
    )
    a = Scripted(rig, "graph:a", expired, ok(), takes=timedelta(0))
    rig.use(a)
    rig.make_timers()
    rig.drive(until=timedelta(seconds=30))

    failure = rig.daemon.storage.harvest_failure("graph:a")
    assert failure is not None and not failure.retryable, "recorded at once"
    assert rig.timers.due["graph:a"] == T0 + CYCLE, "no waits: the next regular cycle"
    probe = rig.doctor("graph:a")
    assert probe.health is Health.FAILING
    assert "graph:a" in probe.detail and "sign-in has expired" in probe.detail
    assert "retried on its own" not in probe.detail, "a regular cycle is not a retry"

    rig.drive(until=CYCLE + MINUTE)
    assert a.asked == [T0, T0 + CYCLE]
    assert rig.daemon.storage.harvest_failure("graph:a") is None
    assert rig.doctor("graph:a").health is Health.OK


# ── A refused save ───────────────────────────────────────────────────────────


def test_a_refused_save_is_a_failed_attempt_retried_in_a_minute_with_nothing_recorded(
    rig, monkeypatch, caplog
):
    caplog.set_level(logging.WARNING, logger=DAEMON_LOGGER)
    storage = rig.daemon.storage
    real = storage.save_cursor
    refusals = ["operational.db in application is claimed by another pm-ai process"]

    def refusing(*args, **kwargs):
        if refusals:
            raise ArtifactBusy(refusals.pop())
        return real(*args, **kwargs)

    monkeypatch.setattr(storage, "save_cursor", refusing)
    a = Scripted(rig, "graph:a", ok(b"first"), ok(b"second"), takes=timedelta(0))
    rig.use(a)
    rig.make_timers()
    rig.drive(until=timedelta(seconds=30))

    assert rig.timers.due["graph:a"] == T0 + MINUTE
    assert storage.harvest_failure("graph:a") is None, "nothing recorded against it"
    assert "graph:a" not in rig.records()
    assert any(
        "claimed by another pm-ai process" in r.getMessage() and "graph:a" in r.getMessage()
        for r in caplog.records
    ), "the refusal is logged, naming the claim"

    rig.drive(until=2 * MINUTE)
    assert a.asked == [T0, T0 + MINUTE]
    assert storage.load_cursor("graph:a") == Cursor(b"second")


# ── Shutdown ─────────────────────────────────────────────────────────────────


def test_shutdown_during_a_fetch_records_the_attempt_failed_naming_the_last_step(rig, caplog):
    caplog.set_level(logging.WARNING, logger=DAEMON_LOGGER)
    inside, release = threading.Event(), threading.Event()

    def blocked(connector, cursor):
        inside.set()
        release.wait(10)
        return ok(b"after-stop")(connector, cursor)

    rig.use(Scripted(rig, "graph:a", blocked, takes=timedelta(0)))
    timers = rig.make_timers()
    attempts: list[HarvestAttempt] = []
    worker = threading.Thread(target=lambda: attempts.append(timers._attempt("graph:a")))  # noqa: SLF001
    worker.start()
    assert inside.wait(5)

    rig.writer.stop_accepting()
    rig.path.shutdown_pass()
    release.set()
    worker.join(10)

    failure = rig.daemon.storage.harvest_failure("graph:a")
    assert failure is not None and failure.retryable
    assert "the daemon stopped" in failure.reason
    assert "read a connector's position" in failure.reason
    assert [attempt.outcome for attempt in attempts] == [AttemptOutcome.STOPPED]
    assert rig.daemon.storage.load_cursor("graph:a") == Cursor(), "a later step was refused"
    assert any("refused" in r.getMessage() for r in caplog.records)


def test_shutdown_during_a_save_lets_it_finish_and_records_no_failure(rig, monkeypatch):
    storage = rig.daemon.storage
    real = storage.save_cursor
    saving, go_on = threading.Event(), threading.Event()

    def slow_save(*args, **kwargs):
        saving.set()
        go_on.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(storage, "save_cursor", slow_save)
    rig.use(Scripted(rig, "graph:a", ok(b"saved"), takes=timedelta(0)))
    timers = rig.make_timers()
    worker = threading.Thread(target=lambda: timers._attempt("graph:a"))  # noqa: SLF001
    worker.start()
    assert saving.wait(5)

    rig.writer.stop_accepting()
    stopping = threading.Thread(target=rig.path.shutdown_pass)
    stopping.start()
    time.sleep(0.2)
    go_on.set()
    stopping.join(10)
    worker.join(10)
    rig.writer.close()

    assert storage.load_cursor("graph:a") == Cursor(b"saved"), "the save finished"
    assert storage.harvest_failure("graph:a") is None


# ── Transcripts and the capture write ────────────────────────────────────────


class _Calendar:
    def __call__(self, request: GraphRequest) -> GraphResponse:
        assert "/me/calendarView" in request.url, f"no route for {request!r}"
        return GraphResponse(status=200, body={"value": []}, headers={"Content-Type": "application/json"})


class _Auth:
    instance = "graph:work"

    def access_token(self, *, force_refresh: bool = False) -> str:
        return "token-1"

    def check_health(self) -> Probe:
        return Probe("graph:work", Health.OK, "graph answered")


class _Source:
    name = "graph"
    requires_network = True

    def __init__(self, texts: dict[str, str]) -> None:
        self.texts = texts

    def acquire(self, meetings: Sequence[Meeting], *, since: datetime) -> tuple[TranscriptOutcome, ...]:
        answers: list[TranscriptOutcome] = []
        for meeting in meetings:
            text = self.texts.get(meeting.meeting_id)
            if text is None:
                answers.append(TranscriptAbsent(meeting.meeting_id, "nothing recorded"))
                continue
            answers.append(
                TranscriptCaptured(
                    meeting.meeting_id,
                    TranscriptCapture(
                        meeting_id=meeting.meeting_id,
                        transcript_id=f"T-{meeting.meeting_id}",
                        created=meeting.start,
                        ended=meeting.start + timedelta(minutes=meeting.duration_minutes),
                        text=text,
                        attributed=True,
                        format=CaptureFormat.GRAPH,
                    ),
                )
            )
        return tuple(answers)


GOOD_VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<v Alex>I'll send the deck by Friday</v>\n"


def _graph(rig: Rig, texts: dict[str, str] | None = None) -> GraphConnector:
    """A Graph connector over an empty calendar, with a scripted transcript source,
    in use; one recorded personal meeting the source has a good capture for."""
    rig.daemon.meetings.put(
        Meeting(
            meeting_id="AAMk-one", title="Sprint review", start=T0 - timedelta(days=1),
            duration_minutes=60, attendees=(Actor("email:alex@example.com"),),
            scope=PERSONAL, calendar_event_ref="AAMk-one",
        )
    )
    client = GraphClient(auth=_Auth(), transport=_Calendar(), now=rig.clock, page_cap=200)
    connector = GraphConnector(
        auth=_Auth(),
        calendar=GraphCalendarFetch(
            client=client,
            instance="graph:work",
            windows=WindowPolicy(width=timedelta(hours=24), first_run_reach_back=timedelta(days=7)),
            now=rig.clock,
        ),
        categories=CategoryScopes(projects={}, registered=lambda p: False),
        now=rig.clock,
    )
    source = _Source(texts if texts is not None else {"AAMk-one": GOOD_VTT})
    rig.use(connector, transcript_sources={"graph:work": source})
    return connector


def test_transcripts_left_over_and_fetched_now_are_both_ingested_and_refusals_reach_doctor(
    rig, monkeypatch
):
    """A capture an earlier run fetched and never ingested (a daemon killed
    between the two), and one fetched now: both handed to ingestion this cycle.
    The one that cannot be read is refused, and the refusal is on the
    `harvests` line. The project meeting's capture asks git on a worker, just
    before the write; the claimed write never asks."""
    rig.vcs.root = rig.daemon.storage.paths.scope_root(ALPHA).parent
    storage = rig.daemon.storage
    earlier = Meeting(
        meeting_id="AAMk-left", title="Sprint review", start=T0 - timedelta(days=1),
        duration_minutes=60, attendees=(Actor("email:alex@example.com"),), scope=PERSONAL,
        calendar_event_ref="AAMk-left",
    )
    now_one = Meeting(
        meeting_id="AAMk-now", title="Platform sync", start=T0 - timedelta(days=2),
        duration_minutes=30, attendees=(Actor("email:alex@example.com"),), scope=ALPHA,
        calendar_event_ref="AAMk-now",
    )
    for meeting in (earlier, now_one):
        rig.daemon.meetings.put(meeting)
    name = capture_name(earlier.meeting_id, earlier.start, CaptureFormat.GRAPH)
    storage.write_capture(GOOD_VTT, scope=PERSONAL, name=name)
    storage.record_transcript_state(
        PERSONAL, earlier.meeting_id, TranscriptStateKind.FETCHED, "fetched earlier", retryable=False
    )
    client = GraphClient(auth=_Auth(), transport=_Calendar(), now=rig.clock, page_cap=200)
    connector = GraphConnector(
        auth=_Auth(),
        calendar=GraphCalendarFetch(
            client=client,
            instance="graph:work",
            windows=WindowPolicy(width=timedelta(hours=24), first_run_reach_back=timedelta(days=7)),
            now=rig.clock,
        ),
        categories=CategoryScopes(projects={"Alpha": "alpha"}, registered=lambda p: p == "alpha"),
        now=rig.clock,
    )
    source = _Source({"AAMk-now": "no header, no timings, no speakers\n"})
    rig.use(connector, transcript_sources={"graph:work": source})
    rig.vcs.threads.clear()
    rig.make_timers()
    rig.drive(until=MINUTE)

    personal = storage.transcript_states(PERSONAL)
    project = storage.transcript_states(ALPHA)
    assert personal["AAMk-left"].state == TranscriptStateKind.INGESTED.value
    assert project["AAMk-now"].state == TranscriptStateKind.INGEST_FAILED.value
    record = rig.records()["graph:work"]
    assert len(record.transcript_refusals) == 1
    assert "AAMk-now" in record.transcript_refusals[0]
    probe = rig.doctor("graph:work")
    assert "1 transcript refusal" in probe.detail
    assert rig.vcs.threads, "git was asked about the project capture"
    assert rig.writer.name not in rig.vcs.threads, "the claimed write never asks git"


def test_no_harvest_claim_or_write_runs_off_the_writer_thread(rig, monkeypatch):
    seen: list[tuple[str, str]] = []
    for name in ("exclusive", "_transaction", "save_cursor", "persist_events", "record_harvest_failure"):
        real = getattr(StorageService, name)

        def wrapped(self, *args, _real=real, _name=name, **kwargs):
            seen.append((_name, threading.current_thread().name))
            return _real(self, *args, **kwargs)

        monkeypatch.setattr(StorageService, name, wrapped)
    _enrol_gitlab(rig)
    store = rig.path._store_for("gitlab:alpha", "gitlab")  # noqa: SLF001

    def refreshing(connector, cursor):
        with store.exclusive():
            store.read()
            store.write("token-two")
        return ok()(connector, cursor)

    rig.use(
        Scripted(rig, "gitlab:alpha", refreshing, scope=ALPHA),
        Scripted(rig, "graph:a", RuntimeError("boom")),
    )
    seen.clear()
    rig.make_timers()
    rig.drive(until=20 * MINUTE)

    assert {name for name, _ in seen} >= {
        "exclusive", "save_cursor", "persist_events", "record_harvest_failure"
    }
    elsewhere = [(name, thread) for name, thread in seen if thread != rig.writer.name]
    assert elsewhere == [], f"claims or writes off the writer thread: {elsewhere}"


# ── Not set up; no acting project; two due at once; old coverage ─────────────


def test_a_built_in_with_no_credential_is_skipped_and_nothing_is_recorded(rig):
    rig.make_timers()
    rig.drive(until=20 * MINUTE)  # the real list: `gitlab:alpha`, the built-in

    assert "gitlab:alpha" in rig.timers.due
    assert rig.records() == {}
    assert rig.daemon.storage.load_cursor("gitlab:alpha") == Cursor()
    probe = rig.doctor("gitlab:alpha")
    assert probe.health is Health.OK and "gitlab:alpha: no harvest has run" in probe.detail


def test_with_no_acting_project_each_instance_writes_where_it_was_filed(tmp_path, monkeypatch):
    clock = Clock(T0)
    daemon = build(
        tmp_path, APPLICATION, watched=("alpha",), keychain=_NoKey(),
        encryption_disabled=True, now=clock,
    )
    enrol_connector(
        daemon.storage, system="graph", instance="graph:work",
        credential=SealedCredential(refresh_token="rt", home_account_id="a.b").encode(),
        probe=lambda system, credential: "accepted",
    )
    daemon.storage.write_artifact(
        json.dumps(
            {
                "instance": "graph:work", "system": "graph", "enabled": True,
                CLIENT_ID_KEY: "00000000-0000-0000-0000-000000000008",
                WIDTH_KEY: 24 * 60, REACH_BACK_KEY: 7 * 24 * 60,
            }
        ).encode(),
        scope=APPLICATION, artifact="connectors/", name="graph:work.json",
    )
    composed = build(
        tmp_path, APPLICATION, watched=("alpha",), keychain=_NoKey(),
        encryption_disabled=True, now=clock,
    )
    assert composed.scope == APPLICATION
    assert instance_scope(composed, "graph:work") == PERSONAL
    assert instance_scope(composed, "gitlab:alpha") == ALPHA

    written: list[tuple[str, DataScope]] = []
    real = composed.storage.persist_events

    def recording(events, *, scope):
        written.append((len(events), scope))
        return real(events, scope=scope)

    monkeypatch.setattr(composed.storage, "persist_events", recording)
    rig_like = type("R", (), {"clock": clock, "entered": lambda self: None, "left": lambda self: None})()
    fakes = {
        "graph:work": Scripted(rig_like, "graph:work"),  # type: ignore[arg-type]
        "gitlab:alpha": Scripted(rig_like, "gitlab:alpha", scope=ALPHA),  # type: ignore[arg-type]
    }
    harvesting = replace(composed, connectors=fakes)
    for instance in ("graph:work", "gitlab:alpha"):
        assert harvest_once(harvesting, instance).outcome is AttemptOutcome.SUCCEEDED
    assert written == [(0, PERSONAL), (0, ALPHA)]
    composed.storage.close()
    daemon.storage.close()


def test_two_due_at_once_run_one_after_the_other_and_neither_is_lost(rig):
    a = Scripted(rig, "graph:a", takes=10 * MINUTE)
    b = Scripted(rig, "graph:b", takes=10 * MINUTE)
    rig.use(a, b)
    rig.make_timers()
    rig.drive(until=30 * MINUTE)

    # `graph:b` came due at 7.5 minutes, during `graph:a`'s harvest.
    assert a.asked == [T0]
    assert b.asked == [T0 + 10 * MINUTE]
    assert rig.overlap == 1


def test_a_config_broken_after_start_is_recorded_at_once_as_needing_a_person(rig):
    from pm_ai.core.config import ConfigRefused

    a = Scripted(rig, "graph:a", takes=timedelta(0))
    rig.use(a)
    rig.make_timers()
    rig.drive(until=timedelta(seconds=30))
    assert a.asked == [T0]

    def broken():
        raise ConfigRefused("config.toml line 1: `display_timezone` is not a zone pm-ai knows")

    rig.path.fresh = broken  # type: ignore[method-assign]
    rig.drive(until=CYCLE + MINUTE)

    assert a.asked == [T0], "nothing asked with a configuration that cannot be used"
    failure = rig.daemon.storage.harvest_failure("graph:a")
    assert failure is not None and not failure.retryable
    assert "config.toml could not be used" in failure.reason
    assert rig.timers.due["graph:a"] == T0 + CYCLE + CYCLE, "kept its timer; the next regular cycle"


def test_a_transcript_ingestion_past_its_budget_fails_the_attempt_keeping_the_last_success(
    rig, monkeypatch
):
    """Ingestion is a bounded step with its own budget. Abandoned after the
    calendar save, the attempt is recorded failed naming the last claimed step
    it finished — the position stays saved — and the record does not show a
    success at this attempt beside its failure."""
    from pm_ai.app import pipelines

    _graph(rig)
    rig.make_timers(budget=0.3)
    rig.drive(until=timedelta(seconds=30))  # a first, whole success
    first = rig.records()["graph:work"]
    assert first.failure is None and first.last_success == T0

    release = threading.Event()
    real = pipelines.ingest_captures

    def hung(daemon, report):
        release.wait(10)
        return real(daemon, report)

    monkeypatch.setattr(pipelines, "ingest_captures", hung)
    rig.clock.advance(CYCLE + 20 * MINUTE)
    rig.timers._slots["graph:work"].due = rig.clock()  # noqa: SLF001
    rig.drive(until=CYCLE + 21 * MINUTE)
    release.set()

    record = rig.records()["graph:work"]
    assert record.last_attempt == rig.clock() and record.last_success == T0
    failure = record.failure
    assert failure is not None and failure.retryable
    assert "transcript ingestion did not finish within its budget" in failure.reason
    assert "The last claimed step it finished was" in failure.reason
    assert "what that step and the ones before it saved stays" in failure.reason
    assert "nothing was saved after it" not in failure.reason


def test_shutdown_during_the_ingestion_marks_it_like_a_fetch(rig, monkeypatch):
    from pm_ai.app import pipelines

    _graph(rig)
    inside, release = threading.Event(), threading.Event()
    real = pipelines.ingest_captures

    def hung(daemon, report):
        inside.set()
        release.wait(10)
        return real(daemon, report)

    monkeypatch.setattr(pipelines, "ingest_captures", hung)
    timers = rig.make_timers()
    worker = threading.Thread(target=lambda: timers._attempt("graph:work"))  # noqa: SLF001
    worker.start()
    assert inside.wait(5)
    rig.writer.stop_accepting()
    rig.path.shutdown_pass()
    release.set()
    worker.join(10)

    failure = rig.daemon.storage.harvest_failure("graph:work")
    assert failure is not None and "the daemon stopped" in failure.reason
    assert rig.daemon.storage.load_cursor("graph:work") != Cursor(), "the save before it stays"
    assert rig.records()["graph:work"].last_success is None, "no success at a failed attempt"


# ── Items from the 9a review ─────────────────────────────────────────────────


def test_a_settings_read_that_raises_mid_round_is_recorded_and_the_round_goes_on(rig):
    a, b = Scripted(rig, "graph:a", takes=timedelta(0)), Scripted(rig, "graph:b", takes=timedelta(0))
    rig.use(a, b)
    real = rig.path.fresh
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:  # the first attempt's read, after the list's
            raise OSError(5, "Input/output error")
        if calls["n"] == 3:
            raise LookupError("nothing pm-ai knows")
        return real()

    timers = rig.make_timers()
    asyncio.run(timers.start())
    for slot in timers._slots.values():  # noqa: SLF001
        slot.due = T0
    rig.path.fresh = flaky  # type: ignore[method-assign]
    rig.started = True
    asyncio.run(timers.tick())

    first = rig.daemon.storage.harvest_failure("graph:a")
    second = rig.daemon.storage.harvest_failure("graph:b")
    assert first is not None and first.retryable and "OSError" in first.reason
    assert second is not None and not second.retryable and "LookupError" in second.reason
    assert timers.due["graph:a"] == T0 + MINUTE, "backoff as usual"
    assert timers.due["graph:b"] == T0 + CYCLE


def test_the_timer_task_survives_a_round_that_raises_and_runs_again(rig):
    a = Scripted(rig, "graph:a", takes=timedelta(0))
    rig.use(a)
    timers = rig.make_timers()
    real_tick = timers.tick
    rounds = {"n": 0}

    async def tick():
        rounds["n"] += 1
        if rounds["n"] == 1:
            raise RuntimeError("a round that broke")
        await real_tick()

    timers.tick = tick  # type: ignore[method-assign]

    async def go() -> None:
        task = asyncio.create_task(timers.run())
        while not a.asked and not task.done():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert rounds["n"] >= 2 and a.asked[0] == T0 + MINUTE, "tried again a minute later"


def test_instances_first_listed_after_an_unusable_start_are_spread_and_honour_a_providers_wait(rig):
    from pm_ai.core.config import ConfigRefused

    rig.daemon.storage.record_harvest_failure(
        "graph:b",
        HarvestFailure("graph:b was throttled", retryable=True, retry_after=timedelta(minutes=30)),
    )
    a, b, c = (Scripted(rig, name, takes=timedelta(0)) for name in ("graph:a", "graph:b", "graph:c"))
    rig.use(a, b, c)
    usable = rig.path.fresh

    def broken():
        raise ConfigRefused("config.toml line 1: not TOML")

    rig.path.fresh = broken  # type: ignore[method-assign]
    timers = rig.make_timers()
    asyncio.run(timers.start())
    assert timers.due == {} and timers.reread_at == T0 + MINUTE

    rig.path.fresh = usable  # type: ignore[method-assign]
    rig.started = True
    rig.drive(until=MINUTE)
    assert a.asked == [T0 + MINUTE]
    assert timers.due["graph:c"] == T0 + MINUTE + 10 * MINUTE, "spread, not all at once"
    assert timers.due["graph:b"] == T0 + 30 * MINUTE, "the provider's wait"


def test_with_no_instance_the_list_is_read_every_minute(rig):
    rig.use()
    timers = rig.make_timers()
    rig.drive(until=3 * MINUTE)
    assert timers.due == {} and timers.reread_at == T0 + 4 * MINUTE

    a = Scripted(rig, "graph:a", takes=timedelta(0))
    rig.use(a)
    rig.drive(until=4 * MINUTE)
    assert a.asked == [T0 + 4 * MINUTE], "the first enrolment within a minute, not a cycle"


def test_a_built_in_given_a_credential_is_picked_up_at_the_next_read(rig, monkeypatch):
    from pm_ai.app.pipelines import set_up

    attempted: list[tuple[str, datetime, bool]] = []

    def attempt(daemon, instance, **_):
        ready = set_up(daemon.connectors[instance])
        attempted.append((instance, rig.clock(), ready))
        outcome = AttemptOutcome.SUCCEEDED if ready else AttemptOutcome.SKIPPED
        return HarvestAttempt(instance, outcome)

    monkeypatch.setattr(ticks_module, "harvest_once", attempt)
    rig.make_timers()
    rig.drive(until=5 * MINUTE)
    assert attempted == [("gitlab:alpha", T0, False)]

    enrol_connector(
        rig.daemon.storage,
        system="gitlab",
        instance="gitlab:alpha",
        credential="glpat-0123456789",
        probe=lambda system, credential: "accepted",
        project="group/alpha",
    )
    rig.drive(until=7 * MINUTE)
    assert attempted[1:] == [("gitlab:alpha", T0 + 6 * MINUTE, True)]


def test_a_deadline_always_abandons_even_when_the_call_finished_at_it(rig, monkeypatch):
    timers = rig.make_timers()

    def finished_at_the_deadline(call, *, timeout, label):
        call()
        raise TimeoutError(f"{label} did not answer within {timeout:g}s")

    monkeypatch.setattr(ticks_module, "run_bounded", finished_at_the_deadline)
    with pytest.raises(FetchAbandoned):
        timers._bounded(_Running("graph:a"), lambda: "answered", "graph:a's fetch")  # noqa: SLF001

    def times_out() -> None:
        raise TimeoutError("the socket timed out")

    monkeypatch.setattr(ticks_module, "run_bounded", lambda call, *, timeout, label: call())
    with pytest.raises(TimeoutError, match="socket"):
        timers._bounded(_Running("graph:a"), times_out, "graph:a's fetch")  # noqa: SLF001


def test_a_transient_timeout_out_of_a_connector_is_retryable_not_a_bug(rig):
    rig.use(Scripted(rig, "graph:a", TimeoutError("read timed out"), takes=timedelta(0)))
    rig.make_timers()
    rig.drive(until=timedelta(seconds=30))
    failure = rig.daemon.storage.harvest_failure("graph:a")
    assert failure is not None and failure.retryable
    assert "bug in pm-ai" not in failure.reason


def test_a_step_from_a_thread_abandoned_while_it_waited_is_refused_on_the_writer(rig, caplog):
    caplog.set_level(logging.WARNING, logger=DAEMON_LOGGER)
    timers = rig.make_timers()
    running = _Running("graph:a")
    entered, release = threading.Event(), threading.Event()
    holder = threading.Thread(
        target=lambda: rig.writer.call(lambda: (entered.set(), release.wait(10)), label="hold")
    )
    holder.start()
    assert entered.wait(5)
    ran = threading.Event()
    outcome: list[BaseException] = []
    idents: list[int] = []

    def handing() -> None:
        idents.append(threading.get_ident())
        try:
            timers._hand(running, ran.set, "save the capture")  # noqa: SLF001
        except BaseException as refused:  # noqa: BLE001
            outcome.append(refused)

    worker = threading.Thread(target=handing)
    worker.start()
    time.sleep(0.2)  # queued behind the held step
    with running.lock:
        running.abandoned.update(idents)
    release.set()
    worker.join(5)
    holder.join(5)
    assert not ran.is_set(), "committed nothing"
    assert [type(error) for error in outcome] == [WriterStopped]
    assert any("abandoned" in r.getMessage() for r in caplog.records)


def test_a_connector_that_raises_makes_one_claimed_write_not_a_receipt(rig, monkeypatch):
    def refused(*args, **kwargs):
        raise ArtifactBusy("the event log is claimed")

    monkeypatch.setattr(rig.daemon.storage, "persist_events", refused)
    rig.use(Scripted(rig, "graph:a", RuntimeError("boom"), takes=timedelta(0)))
    rig.make_timers()
    rig.drive(until=timedelta(seconds=30))
    failure = rig.daemon.storage.harvest_failure("graph:a")
    assert failure is not None and "RuntimeError" in failure.reason
    assert rig.timers.due["graph:a"] == T0 + CYCLE, "recorded, not refused and retried"


def test_coverage_older_than_90_days_is_pruned_daily_and_odd_rows_do_not_stop_it(rig):
    import sqlite3

    storage = rig.daemon.storage
    for days in (91, 89):
        end = T0 - timedelta(days=days)
        storage.save_cursor("graph:old", Cursor(), CoverageWindow("graph:old", end, end), None)
    store = storage.paths.resolve(APPLICATION, "operational.db")
    with sqlite3.connect(store) as db:
        db.execute(
            "INSERT INTO coverage (instance, start, end) VALUES "
            "('graph:odd', '2026-01-01T00:00:00', '2026-01-01T00:00:00'), "
            "('graph:odd', 'not a time', 'not a time')"
        )
    assert rig.path.prune_coverage() == 2
    assert storage.coverage_windows("graph:old") == [
        (T0 - timedelta(days=89), T0 - timedelta(days=89))
    ]
    with sqlite3.connect(store) as db:
        left = db.execute("SELECT end FROM coverage WHERE instance = 'graph:odd'").fetchall()
    assert left == [("not a time",)]


def test_the_lifespan_logs_a_dead_timer_and_still_cancels_the_rest(caplog):
    import types

    caplog.set_level(logging.ERROR, logger=DAEMON_LOGGER)
    pruning_cancelled = threading.Event()

    async def prune_daily() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pruning_cancelled.set()
            raise

    async def dying() -> None:
        raise RuntimeError("a timer that died")

    app = types.SimpleNamespace(
        state=types.SimpleNamespace(
            pm_ai_requests=types.SimpleNamespace(
                events=types.SimpleNamespace(attach=lambda loop: None), prune_daily=prune_daily
            ),
            pm_ai_timers=types.SimpleNamespace(run=dying),
        )
    )

    async def go() -> None:
        async with daemon_module._lifespan(app):  # noqa: SLF001
            await asyncio.sleep(0.05)

    asyncio.run(go())
    assert pruning_cancelled.is_set()
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


def test_last_success_survives_a_later_failure_and_refusals_are_cleared_by_the_next_attempt(rig):
    storage = rig.daemon.storage
    storage.save_cursor("graph:a", Cursor(b"one"), None, None)
    storage.record_transcript_refusals("graph:a", ["meeting X: refused"])
    assert rig.records()["graph:a"].transcript_refusals == ("meeting X: refused",)
    rig.clock.advance(MINUTE)
    storage.record_harvest_failure("graph:a", HarvestFailure("503", retryable=True))
    record = rig.records()["graph:a"]
    assert record.last_success == T0 and record.last_attempt == T0 + MINUTE
    assert record.transcript_refusals == ()


def test_an_instance_enrolled_after_start_is_filed_where_the_real_refresh_says(rig):
    enrol_connector(
        rig.daemon.storage,
        system="gitlab",
        instance="gitlab:web",
        credential="glpat-0123456789",
        probe=lambda system, credential: "accepted",
        project="group/web",
    )
    fresh = rig.path.fresh()
    assert "gitlab:web" in fresh.connectors
    assert instance_scope(fresh, "gitlab:web") == ALPHA
    assert instance_scope(fresh, "gitlab:alpha") == ALPHA


@pytest.mark.parametrize("stored", ['{"not": "a list"}', "not json", "[1, 2]"])
def test_unreadable_recorded_refusals_read_back_as_one_sentence_saying_so(rig, stored):
    import sqlite3

    storage = rig.daemon.storage
    storage.save_cursor("graph:a", Cursor(), None, None)
    with sqlite3.connect(storage.paths.resolve(APPLICATION, "operational.db")) as db:
        db.execute("UPDATE harvest_runs SET transcript_refusals = ?", (stored,))
    assert rig.records()["graph:a"].transcript_refusals == (
        "the recorded transcript refusals could not be read back",
    )


def test_the_harvests_line_fails_by_name_when_the_store_cannot_be_read():
    import types

    from pm_ai.app import entry

    def unreadable():
        raise OSError(5, "Input/output error")

    composed = types.SimpleNamespace(
        daemon=types.SimpleNamespace(
            connectors={"graph:a": object()},
            storage=types.SimpleNamespace(harvest_records=unreadable),
        )
    )
    probe = entry._harvests_probe(composed)  # noqa: SLF001
    assert probe is not None and probe.name == HARVESTS_PROBE
    assert probe.health is Health.FAILING and "OSError" in probe.detail


@pytest.mark.parametrize("shape", ["directory", "symlink"])
def test_a_lifetime_lock_that_is_not_a_plain_file_is_refused_by_name(tmp_path, shape):
    daemon = build(tmp_path, APPLICATION, keychain=_NoKey(), encryption_disabled=True, now=Clock(T0))
    lock = daemon.storage.daemon_lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    if shape == "directory":
        lock.mkdir()
    else:
        (tmp_path / "elsewhere").write_text("")
        lock.symlink_to(tmp_path / "elsewhere")
    try:
        with pytest.raises(DaemonRefused) as refused:
            daemon_module.take_lifetime_lock(daemon)
        assert str(lock) in str(refused.value)
    finally:
        daemon.storage.close()


def test_a_fresh_home_gets_its_pm_ai_and_private_directories_at_0700(tmp_path):
    import stat

    from pm_ai.platform.paths import ScopePaths

    home = tmp_path / "home"
    home.mkdir()
    daemon = build(
        None, APPLICATION, paths=ScopePaths.production(home=home), keychain=_NoKey(),
        encryption_disabled=True, now=Clock(T0),
    )
    try:
        daemon_module.take_lifetime_lock(daemon).release()
        for directory in (home / ".pm-ai", home / ".pm-ai" / "private"):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700, directory
    finally:
        daemon.storage.close()


def test_the_real_daemon_records_an_attempt_and_cancels_its_timers_at_stop(tmp_path, monkeypatch):
    from pm_ai.app import prompts

    daemon = build(tmp_path, APPLICATION, keychain=_NoKey(), encryption_disabled=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    daemon.config = replace(daemon.config, daemon_port=port)

    class Quick:
        instance = "graph:a"

        def harvest(self, cursor: Cursor) -> HarvestResult:
            now = datetime.now(timezone.utc)
            return HarvestResult(
                events=(), cursor=Cursor(b"served"), outcome=HarvestOutcome.EMPTY,
                coverage=CoverageWindow("graph:a", now, now), answered_at=now,
            )

        def emits(self) -> frozenset:
            return frozenset()

        def sample_events(self) -> tuple:
            return ()

        def check_health(self) -> Probe:
            return Probe("graph:a", Health.OK, "scripted")

    monkeypatch.setattr(
        prompts.RequestPath,
        "fresh",
        lambda self: replace(
            self.daemon, connectors={"graph:a": Quick()}, instance_scopes={"graph:a": PERSONAL}
        ),
    )
    cancelled = threading.Event()

    class Timers(HarvestTimers):
        def __init__(self, **options: Any) -> None:
            super().__init__(**options, nap=0.05)

        async def run(self) -> None:
            try:
                await super().run()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    monkeypatch.setattr(daemon_module, "HarvestTimers", Timers)
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
    records: tuple = ()
    while time.monotonic() < deadline:
        try:
            records = daemon.storage.harvest_records()
        except Exception:  # noqa: BLE001 — the store may not be open yet
            records = ()
        if records:
            break
        time.sleep(0.05)
    assert [record.instance for record in records] == ["graph:a"]
    assert records[0].last_success is not None
    while not servers:
        time.sleep(0.01)
    servers[0].should_exit = True
    serving.join(20)
    assert returned == [0]
    assert cancelled.is_set(), "the timer task was cancelled at stop"


def test_coverage_older_than_90_days_is_pruned_after_a_harvest(rig):
    storage = rig.daemon.storage
    for days in (91, 89):
        end = T0 - timedelta(days=days)
        storage.save_cursor("graph:old", Cursor(), CoverageWindow("graph:old", end, end), None)
    rig.use(Scripted(rig, "graph:a"))
    rig.make_timers()
    rig.drive(until=MINUTE)

    assert storage.coverage_windows("graph:old") == [
        (T0 - timedelta(days=89), T0 - timedelta(days=89))
    ]


def test_the_transcript_fetch_gets_only_what_the_calendar_fetch_left_of_the_budget(rig):
    """One per-connector budget per attempt: the transcript fetch's time counts
    against the harvest. Either fetch alone fits the budget; together they do
    not, so the transcript fetch is abandoned, naming the last claimed step."""

    class SlowCalendar(_Calendar):
        def __call__(self, request: GraphRequest) -> GraphResponse:
            time.sleep(0.7)
            return super().__call__(request)

    class SlowSource(_Source):
        def acquire(self, meetings, *, since):
            time.sleep(0.6)
            return super().acquire(meetings, since=since)

    connector = _graph(rig)
    connector.calendar.client.transport = SlowCalendar()
    rig.use(connector, transcript_sources={"graph:work": SlowSource({"AAMk-one": GOOD_VTT})})
    rig.make_timers(budget=1.0)
    rig.drive(until=timedelta(seconds=30))

    failure = rig.daemon.storage.harvest_failure("graph:work")
    assert failure is not None and failure.retryable
    assert "transcript fetch did not finish within its budget" in failure.reason
    assert "s left of the attempt's 1 s" in failure.reason
    assert "The last claimed step it finished was" in failure.reason
    assert rig.daemon.storage.load_cursor("graph:work") != Cursor(), "the calendar's save stays"
