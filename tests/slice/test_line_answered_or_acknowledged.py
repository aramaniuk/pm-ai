"""Story 4r — a line is answered at once or acknowledged with a job, in-process.

The request path (`pm_ai.app.prompts.RequestPath`) driven against a real
composed daemon over a throwaway root: the real single writer, the real
`operational.db`, the real writer thread and I/O pool. Only what would reach
outside the machine is stood in — the render's reads are replaced where a row
needs a job to stay in flight. The rows that need a real process (signals, the
port, the console) are in `test_daemon_requests_slice.py`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pm_ai.app import daemon as daemon_module
from pm_ai.app import prompts
from pm_ai.app.concurrency import IOPool, StepKind, WriterThread
from pm_ai.app.pipelines import DASHBOARD_ARTIFACT
from pm_ai.app.entry import DAEMON_BOUNDS
from pm_ai.app.prompts import DAEMON_LOGGER, RequestPath
from pm_ai.app.wiring import Daemon, HandedRefreshTokenStore, build, refreshed
from pm_ai.connectors.graph.auth import StoreEntryMissing
from pm_ai.core.connector_enrolment import enrol_connector, stored_credentials
from pm_ai.core.project_registry import ProjectEntry, render_registry
from pm_ai.domain.harvest import Cursor, HarvestOutcome, HarvestResult
from pm_ai.domain.health import Health, Probe, Report
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.ports import ArtifactBusy, DaemonRefused, KeychainUnavailable, KeyNotFound
from pm_ai.storage.crypto import LazyKeyCrypto
from pm_ai.storage.service import (
    SCHEMA_VERSION,
    OperationalStoreStuck,
    SchemaVersionChanged,
    SchemaVersionMoved,
    StorageService,
)
from pm_ai.surfaces.cli.dispatch import (
    EXIT_OK,
    EXIT_REFUSAL,
    EXIT_USAGE,
    RESTART_SENTENCE,
    TERMINAL_BOUND,
    dispatch,
    usage,
)

APPLICATION = DataScope(ScopeKind.APPLICATION)
PERSONAL = DataScope(ScopeKind.PERSONAL)
WATCHED = ("alpha", "beta", "gamma", "delta")


class _NoKey:
    """A keychain holding nothing; counts how often it is asked."""

    def __init__(self) -> None:
        self.asked = 0

    def store(self, name: str, secret: bytes) -> None:  # pragma: no cover
        raise AssertionError("nothing here enrols a key")

    def store_if_absent(self, name: str, secret: bytes) -> None:  # pragma: no cover
        raise AssertionError("nothing here enrols a key")

    def fetch(self, name: str) -> bytes:
        self.asked += 1
        raise KeyNotFound(name)

    def delete(self, name: str) -> None:  # pragma: no cover
        raise AssertionError("nothing here deletes a key")


class _Locked(_NoKey):
    def fetch(self, name: str) -> bytes:
        self.asked += 1
        raise KeychainUnavailable("the login keychain is locked")


class _Held(_NoKey):
    def fetch(self, name: str) -> bytes:
        self.asked += 1
        return b"K" * 32


@dataclass
class Rig:
    root: Path
    daemon: Daemon
    writer: WriterThread
    pool: IOPool
    path: RequestPath
    halts: list[tuple[str, str | None]]

    def ask(self, line: str):
        return asyncio.run(self.path.prompt(line))

    def row(self, job: str):
        return self.daemon.storage.read_request(job)

    def rows(self) -> list[tuple[str, str, str, str]]:
        with sqlite3.connect(self.store) as db:
            return list(db.execute("SELECT id, kind, scope, state FROM requests ORDER BY created_at"))

    @property
    def store(self) -> Path:
        return self.daemon.storage.paths.resolve(APPLICATION, "operational.db")

    def wait(self, job: str, *states: str, timeout: float = 15) -> object:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = self.row(job)
            if row is not None and row.state in states:
                return row
            time.sleep(0.02)
        raise AssertionError(f"{job} never reached {states}: {self.row(job)}")

    def drain(self) -> None:
        self.writer.call(lambda: None, label="drain")


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    daemon = build(
        tmp_path,
        APPLICATION,
        watched=WATCHED,
        keychain=_NoKey(),
        encryption_disabled=True,
    )
    paths = daemon.storage.paths
    _write(paths.resolve(APPLICATION, "config.toml"), b'display_timezone = "UTC"\n')
    _write(
        paths.resolve(APPLICATION, "projects.toml"),
        render_registry({pid: ProjectEntry(path=tmp_path / pid) for pid in WATCHED}),
    )
    halts: list[tuple[str, str | None]] = []

    def halted(error: BaseException, job: str | None) -> None:
        halts.append((type(error).__name__, job))
        daemon_module.record_writer_halt(
            error, job, unfinished=daemon.storage.unfinished_request_ids()
        )

    writer = WriterThread(
        log=logging.getLogger(DAEMON_LOGGER),
        step_scope=daemon.storage.claimed_step,
        halts_on=(SchemaVersionChanged, OperationalStoreStuck),
        on_halt=halted,
    )
    writer.start()
    writer.call(daemon.storage.adopt_daemon_connection, label="adopt")
    pool = IOPool()
    pool.start()
    path = RequestPath(
        daemon=daemon,
        writer=writer,
        pool=pool,
        diagnose=lambda: Report(()),
        bounds=DAEMON_BOUNDS,
    )
    log = daemon_module._RequestLog.attach(daemon)  # noqa: SLF001
    try:
        yield Rig(tmp_path, daemon, writer, pool, path, halts)
    finally:
        writer.close()
        pool.shutdown()
        log.detach()
        daemon.storage.close()


@pytest.fixture
def held_render(monkeypatch) -> Iterator[threading.Event]:
    """Every render waits in its bounded step until the returned event is set."""
    release = threading.Event()
    real = prompts.prepare_dashboard

    def prepare(daemon, *, scope, now):
        release.wait(30)
        return real(daemon, scope=scope, now=now)

    monkeypatch.setattr(prompts, "prepare_dashboard", prepare)
    yield release
    release.set()


def _log_lines(rig: Rig) -> list[dict[str, object]]:
    lines = rig.daemon.storage.diagnostic_lines(prompts.DAEMON_LOG_NAME)
    return [json.loads(line) for line in lines]


# ── Inline answers ───────────────────────────────────────────────────────────


def test_help_is_answered_inline_with_the_command_list(rig):
    reply = rig.ask("help")
    assert reply.status == 200
    assert reply.body == {"inline": True, "output": usage() + "\n", "exit": EXIT_OK}


def test_config_show_is_what_the_terminal_prints_and_nothing_reaches_the_console(rig, capsys):
    reply = rig.ask("config show")
    seen = capsys.readouterr()
    assert seen.out == "" and seen.err == "", "nothing a command prints reaches the console"
    assert reply.status == 200
    assert reply.body["exit"] == EXIT_OK and reply.body["inline"] is True
    fresh = refreshed(rig.daemon, store_for=rig.path._store_for)  # noqa: SLF001
    assert dispatch(["config", "show"], daemon=fresh, diagnose=lambda: Report(()), probe_connectors=None) == EXIT_OK
    assert reply.body["output"] == capsys.readouterr().out


def test_two_lines_at_once_each_get_only_their_own_words(rig):
    async def both():
        return await asyncio.gather(*(rig.path.prompt("config show") for _ in range(6)), rig.path.prompt("help"))

    *configs, help_reply = asyncio.run(both())
    assert len({reply.body["output"] for reply in configs}) == 1
    assert help_reply.body["output"] == usage() + "\n"


def test_a_refused_command_carries_its_sentence_and_exit_3(rig):
    _write(rig.daemon.storage.paths.resolve(APPLICATION, "config.toml"), b"daemon_port = 'x'\n")
    reply = rig.ask("config show")
    assert reply.body["exit"] == EXIT_REFUSAL
    assert "daemon_port" in reply.body["output"]


@pytest.mark.parametrize("spelled", sorted(TERMINAL_BOUND))
def test_a_command_that_needs_a_terminal_is_refused_by_name(rig, spelled):
    line = " ".join(spelled) + (" graph work" if spelled == ("connector", "add") else "")
    reply = rig.ask(line)
    assert reply.status == 200
    assert reply.body["exit"] == EXIT_REFUSAL
    assert f"`pm-ai {' '.join(spelled)}`" in reply.body["output"]
    assert "terminal" in reply.body["output"]


@pytest.mark.parametrize("line", ["what did I promise?", "what's next", "   "])
def test_a_line_whose_first_word_is_no_command_names_help(rig, line):
    reply = rig.ask(line)
    assert reply.status == 200
    assert reply.body["exit"] == EXIT_USAGE
    assert "`help`" in reply.body["output"]
    assert reply.body["output"].count("\n") == 1, "one sentence"


def test_a_usage_error_is_answered_as_the_terminal_answers_it(rig):
    reply = rig.ask("config")
    assert reply.body["exit"] == EXIT_USAGE
    assert reply.body["output"] == usage(group="config") + "\n"


# ── A job ────────────────────────────────────────────────────────────────────


def test_the_row_is_queued_before_the_202_and_the_job_ends_done_with_the_path(rig, monkeypatch):
    seen_at_reply: list[list[tuple[str, str, str, str]]] = []
    real_reply = prompts.Reply

    def reply(status, body, headers=None):
        if status == 202:
            seen_at_reply.append(rig.rows())
        return real_reply(status, body, headers or {})

    seen_at_start: list[list[tuple[str, str, str, str]]] = []
    real_submit = rig.pool.submit

    def submit(work):
        seen_at_start.append(rig.rows())
        return real_submit(work)

    monkeypatch.setattr(prompts, "Reply", reply)
    monkeypatch.setattr(rig.pool, "submit", submit)
    answered = rig.ask("dashboard")
    assert answered.status == 202
    job = answered.body["job_id"]
    assert answered.body == {"inline": False, "job_id": job}
    # Written `queued` before anything runs it, and there before the `202`.
    assert seen_at_start == [[(job, "dashboard", "personal", "queued")]]
    assert len(seen_at_reply) == 1 and [r[0] for r in seen_at_reply[0]] == [job]
    row = rig.wait(job, "done")
    result = json.loads(row.result)
    written = rig.daemon.storage.paths.resolve(PERSONAL, DASHBOARD_ARTIFACT)
    assert result == {"output": f"the personal dashboard is written: {written}\n", "exit": 0}
    assert written.is_file()


def test_a_render_that_fails_is_failed_with_pm_ais_sentence_and_the_type_name(rig, monkeypatch):
    def broken(daemon, *, scope, now):
        raise RuntimeError("Bearer eyJ-a-provider-token-in-an-exception")

    monkeypatch.setattr(prompts, "prepare_dashboard", broken)
    job = rig.ask("dashboard").body["job_id"]
    row = rig.wait(job, "failed")
    result = json.loads(row.result)
    assert result["type"] == "RuntimeError"
    assert "eyJ" not in row.result and "Bearer" not in row.result
    assert "pm-ai" in result["error"]


def test_a_refused_render_is_done_with_its_sentence_and_exit_3_as_the_terminal(rig):
    job = rig.ask("dashboard --scope people:bob").body["job_id"]
    row = rig.wait(job, "done", "failed")
    assert row.state == "done"
    result = json.loads(row.result)
    assert result["exit"] == EXIT_REFUSAL and "daily_dashboard.md" in result["output"]


def test_a_second_dashboard_for_the_same_scope_answers_with_the_running_jobs_id(rig, held_render):
    first = rig.ask("dashboard").body["job_id"]
    rig.wait(first, "running")
    again = rig.ask("dashboard")
    assert again.status == 202 and again.body["job_id"] == first
    other_scope = rig.ask("dashboard --scope project:alpha").body["job_id"]
    assert other_scope != first
    held_render.set()
    rig.wait(first, "done")
    rig.wait(other_scope, "done")
    assert [r[0] for r in rig.rows()] == [first, other_scope]


def test_five_jobs_at_once_run_four_and_queue_one(rig, held_render):
    scopes = ["personal", "project:alpha", "project:beta", "project:gamma", "project:delta"]
    jobs = [rig.ask(f"dashboard --scope {scope}").body["job_id"] for scope in scopes]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        states = sorted(rig.row(job).state for job in jobs)
        if states == ["queued", "running", "running", "running", "running"]:
            break
        time.sleep(0.02)
    assert states == ["queued", "running", "running", "running", "running"]
    time.sleep(0.3)
    assert sorted(rig.row(job).state for job in jobs).count("queued") == 1
    held_render.set()
    for job in jobs:
        rig.wait(job, "done")


def test_a_writer_that_refuses_the_row_is_503_and_never_202(rig, monkeypatch):
    def refuse(**_: object):
        raise ArtifactBusy("operational.db in application is claimed by another pm-ai process")

    monkeypatch.setattr(rig.daemon.storage, "create_request", refuse)
    reply = rig.ask("dashboard")
    assert reply.status == 503
    assert reply.headers == {"Retry-After": "1"}
    assert "job_id" not in reply.body
    assert rig.rows() == []


def test_a_busy_database_past_the_daemons_wait_is_503(rig):
    other = sqlite3.connect(rig.store)
    other.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        reply = rig.ask("dashboard")
        assert reply.status == 503 and reply.headers == {"Retry-After": "1"}
        assert time.monotonic() - started < 3
    finally:
        other.rollback()
        other.close()
    assert rig.rows() == []


def test_a_writer_not_free_within_one_second_is_503_and_no_row_ever_appears(rig):
    entered, release = threading.Event(), threading.Event()
    holding = threading.Thread(
        target=lambda: rig.writer.call(lambda: (entered.set(), release.wait(30)), label="long")
    )
    holding.start()
    assert entered.wait(5)
    started = time.monotonic()
    reply = rig.ask("dashboard")
    waited = time.monotonic() - started
    assert reply.status == 503 and reply.headers == {"Retry-After": "1"}
    assert "job_id" not in reply.body
    assert 0.9 <= waited < 5
    release.set()
    holding.join()
    rig.drain()
    assert rig.rows() == [], "the withdrawn step never created the row"


def test_a_late_finish_changes_nothing_and_is_logged_by_name(rig, held_render, caplog):
    caplog.set_level(logging.WARNING, logger=DAEMON_LOGGER)
    job = rig.ask("dashboard").body["job_id"]
    rig.wait(job, "running")
    failed = rig.writer.call(
        lambda: rig.daemon.storage.fail_unfinished_requests(
            lambda row: json.dumps({"error": "marked by the test", "type": "DaemonStopped"})
        ),
        label="mark",
    )
    assert [row.id for row in failed] == [job]
    held_render.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not any(
        "settle the job" in record.getMessage() for record in caplog.records
    ):
        time.sleep(0.02)
    row = rig.row(job)
    assert row.state == "failed"
    assert json.loads(row.result)["error"] == "marked by the test"
    assert any(
        "settle the job" in r.getMessage() and job in r.getMessage() for r in caplog.records
    )


def test_job_status_known_and_unknown(rig):
    job = rig.ask("dashboard").body["job_id"]
    rig.wait(job, "done")
    body = asyncio.run(rig.path.job(job))
    assert body["id"] == job and body["state"] == "done"
    assert set(body) == {"id", "kind", "scope", "state", "result", "created_at", "updated_at"}
    assert asyncio.run(rig.path.job("job_0000000000000000")) is None
    assert asyncio.run(rig.path.job_events("job_0000000000000000")) is None


def test_the_stream_on_a_live_job_has_one_event_per_change_and_closes_after_done(rig, held_render):
    async def scenario():
        rig.path.events.attach(asyncio.get_running_loop())
        job = (await rig.path.prompt("dashboard")).body["job_id"]
        stream = await rig.path.job_events(job)
        states = []
        async for event in stream:
            states.append(event["state"])
            if len(states) == 1:
                held_render.set()
        return states, event

    states, last = asyncio.run(scenario())
    assert states[-1] == "done"
    assert len(states) == len(set(states)), "one event per change"
    assert states in (["queued", "running", "done"], ["running", "done"])
    assert last["result"]["exit"] == 0


def test_the_stream_on_a_finished_job_sends_that_one_event_and_closes(rig):
    job = rig.ask("dashboard").body["job_id"]
    rig.wait(job, "done")

    async def read():
        rig.path.events.attach(asyncio.get_running_loop())
        return [event async for event in await rig.path.job_events(job)]

    events = asyncio.run(read())
    assert [event["state"] for event in events] == ["done"]


def test_a_reader_that_leaves_mid_stream_leaves_the_job_running(rig, held_render):
    async def leave():
        rig.path.events.attach(asyncio.get_running_loop())
        job = (await rig.path.prompt("dashboard")).body["job_id"]
        stream = await rig.path.job_events(job)
        await anext(stream)
        await stream.aclose()
        return job

    job = asyncio.run(leave())
    held_render.set()
    row = rig.wait(job, "done")
    assert json.loads(row.result)["exit"] == 0


# ── Restart, prune and stop ──────────────────────────────────────────────────


def _insert(rig: Rig, job: str, state: str, *, created: datetime) -> None:
    with sqlite3.connect(rig.store) as db:
        db.execute(
            "INSERT INTO requests VALUES (?, 'dashboard', 'personal', ?, NULL, ?, ?)",
            (job, state, created.isoformat(), created.isoformat()),
        )


def test_the_restart_pass_fails_what_an_earlier_run_left_unfinished(rig):
    now = datetime.now(timezone.utc)
    _insert(rig, "job_aaaaaaaaaaaaaaaa", "running", created=now)
    _insert(rig, "job_bbbbbbbbbbbbbbbb", "queued", created=now)
    _insert(rig, "job_cccccccccccccccc", "done", created=now)
    assert set(rig.path.restart_pass()) == {"job_aaaaaaaaaaaaaaaa", "job_bbbbbbbbbbbbbbbb"}
    for job in ("job_aaaaaaaaaaaaaaaa", "job_bbbbbbbbbbbbbbbb"):
        row = rig.row(job)
        assert row.state == "failed"
        assert json.loads(row.result)["error"] == "the daemon stopped before this job finished"
    assert rig.row("job_cccccccccccccccc").state == "done"


def test_rows_older_than_seven_days_are_removed(rig):
    now = datetime.now(timezone.utc)
    _insert(rig, "job_old0000000000000", "done", created=now - timedelta(days=8))
    _insert(rig, "job_new0000000000000", "done", created=now - timedelta(days=6))
    assert rig.path.prune() == 1
    assert rig.row("job_old0000000000000") is None
    assert rig.row("job_new0000000000000") is not None


def test_a_stop_finishes_queued_steps_fails_the_job_in_its_read_and_refuses_its_later_step(
    rig, held_render, caplog
):
    caplog.set_level(logging.WARNING, logger=DAEMON_LOGGER)
    job = rig.ask("dashboard").body["job_id"]
    rig.wait(job, "running")
    # One claimed step running on the writer, one queued behind it.
    entered, release = threading.Event(), threading.Event()
    finished: list[str] = []

    def running_step() -> None:
        entered.set()
        release.wait(30)
        finished.append("running")

    first = threading.Thread(target=lambda: rig.writer.call(running_step, label="running"))
    first.start()
    assert entered.wait(5)
    second = threading.Thread(
        target=lambda: rig.writer.call(lambda: finished.append("queued"), label="queued")
    )
    second.start()
    time.sleep(0.2)
    stopping = threading.Thread(target=daemon_module._stop_writer, args=(rig.path,))  # noqa: SLF001
    stopping.start()
    time.sleep(0.5)
    assert stopping.is_alive(), "the stop waits for the running step, however long"
    release.set()
    stopping.join(15)
    first.join()
    second.join()
    assert finished == ["running", "queued"]
    row = rig.row(job)
    assert row.state == "failed"
    reason = json.loads(row.result)["error"]
    assert "stopped before this job finished" in reason and "'start the job'" in reason
    held_render.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not any(
        "save the personal dashboard" in r.getMessage() for r in caplog.records
    ):
        time.sleep(0.02)
    assert any(
        "save the personal dashboard" in r.getMessage() and job in r.getMessage()
        for r in caplog.records
    ), "its later claimed step is refused and logged by name"
    assert rig.row(job).state == "failed"


def test_a_credential_save_handed_over_during_the_stop_is_made_then_refused_after_close(rig, caplog):
    caplog.set_level(logging.WARNING, logger=DAEMON_LOGGER)
    rig.writer.stop_accepting()
    assert rig.writer.call(lambda: "saved", label="save a token", kind=StepKind.CREDENTIAL_SAVE) == "saved"
    rig.writer.close()
    with pytest.raises(prompts.WriterStopped):
        rig.writer.call(lambda: "saved", label="save a late token", kind=StepKind.CREDENTIAL_SAVE)
    assert any("save a late token" in r.getMessage() for r in caplog.records)


def test_a_schema_move_while_running_stops_the_writer_and_the_restart_names_it(rig, held_render):
    job = rig.ask("dashboard").body["job_id"]
    rig.wait(job, "running")
    with sqlite3.connect(rig.store) as db:
        db.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    held_render.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not rig.halts:
        time.sleep(0.02)
    assert rig.halts == [("SchemaVersionMoved", job)]
    assert rig.row(job).state == "running", "the job stays running for the next start"
    # Nothing more is written to the store: the next step is refused.
    assert rig.ask("dashboard --scope project:alpha").status == 503
    with sqlite3.connect(rig.store) as db:
        assert db.execute("SELECT count(*) FROM requests").fetchone()[0] == 1
    # The next start knows the new version; its restart pass reads the line
    # the stop left in the diagnostic log (written by the log's own thread).
    rig.writer.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not any(
        line.get("event") == "schema-version-mismatch" for line in _log_lines(rig)
    ):
        time.sleep(0.05)
    (stop_line,) = [l for l in _log_lines(rig) if l.get("event") == "schema-version-mismatch"]
    assert stop_line["job"] == job and stop_line["found"] == SCHEMA_VERSION + 1
    later = WriterThread(log=logging.getLogger(DAEMON_LOGGER))
    later.start()
    try:
        storage = rig.daemon.storage
        with sqlite3.connect(rig.store) as db:
            db.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        path = RequestPath(
            daemon=rig.daemon, writer=later, pool=rig.pool, diagnose=lambda: Report(()),
            bounds=DAEMON_BOUNDS,
        )
        assert path.restart_pass() == (job,)
        result = json.loads(storage.read_request(job).result)
        assert result["error"] == (
            "the daemon stopped on a schema-version mismatch; see the diagnostic log"
        )
    finally:
        later.close()


# ── Status off the loop ──────────────────────────────────────────────────────


def test_a_slow_status_probe_never_holds_up_a_line(rig):
    release = threading.Event()

    def slow_probes() -> dict[str, object]:
        release.wait(10)
        return {"probes": []}

    async def scenario():
        status = asyncio.ensure_future(rig.path.status(slow_probes))
        await asyncio.sleep(0.1)
        answered = await asyncio.wait_for(rig.path.prompt("config show"), 5)
        still_waiting = not status.done()
        release.set()
        await status
        return answered, still_waiting

    answered, still_waiting = asyncio.run(scenario())
    assert answered.body["exit"] == EXIT_OK
    assert still_waiting


# ── Every claim and every write on the writer thread ─────────────────────────


def test_every_claim_and_write_in_the_daemon_runs_on_the_writer_thread(rig, monkeypatch):
    seen: list[tuple[str, str]] = []
    storage_type = StorageService

    def watch(name: str) -> None:
        real = getattr(storage_type, name)

        def wrapped(self, *args, **kwargs):
            seen.append((name, threading.current_thread().name))
            return real(self, *args, **kwargs)

        monkeypatch.setattr(storage_type, name, wrapped)

    for name in ("exclusive", "write_artifact", "_transaction", "append_event_log", "_replace"):
        watch(name)
    enrol_connector(
        rig.daemon.storage,
        system="gitlab",
        instance="gitlab:alpha",
        credential="glpat-before",
        probe=lambda system, credential: "accepted",
    )
    seen.clear()
    job = rig.ask("dashboard").body["job_id"]
    rig.wait(job, "done")
    check = rig.ask("connector check").body["job_id"]
    rig.wait(check, "done")
    rig.path.restart_pass()
    rig.path.prune()
    store = rig.path._store_for("gitlab:alpha", "gitlab")  # noqa: SLF001
    with store.exclusive():
        assert store.read() == "glpat-before"
        store.write("glpat-after")
    assert {name for name, _ in seen} >= {"exclusive", "write_artifact", "_transaction"}
    elsewhere = [(name, thread) for name, thread in seen if thread != rig.writer.name]
    assert elsewhere == [], f"claims or writes off the writer thread: {elsewhere}"


# ── The refreshed credential: read and compare-and-save on the writer ────────


def _enrolled(rig: Rig, credential: str = "token-one") -> HandedRefreshTokenStore:
    enrol_connector(
        rig.daemon.storage,
        system="gitlab",
        instance="gitlab:beta",
        credential=credential,
        probe=lambda system, credential: "accepted",
    )
    return rig.path._store_for("gitlab:beta", "gitlab")  # noqa: SLF001


def _held(rig: Rig) -> str:
    return stored_credentials(rig.daemon.storage)["gitlab:beta"]["credential"]


def test_a_refreshed_credential_is_saved_by_compare_and_save(rig):
    store = _enrolled(rig)
    with store.exclusive():
        assert store.read() == "token-one"
    with store.exclusive():
        assert store.read() == "token-one"
        store.write("token-two")
    assert _held(rig) == "token-two"


def test_a_lost_race_drops_the_new_token_and_logs_it(rig, caplog):
    caplog.set_level(logging.WARNING, logger=DAEMON_LOGGER)
    store = _enrolled(rig)
    other = rig.path._store_for("gitlab:beta", "gitlab")  # noqa: SLF001
    with store.exclusive():
        assert store.read() == "token-one"
        # Another refresh (or a sign-in) saved in between.
        holder = threading.Thread(target=lambda: _replace(other, "token-theirs"))
        holder.start()
        holder.join()
        store.write("token-mine")
    assert _held(rig) == "token-theirs"
    assert any("dropped" in r.getMessage() and "gitlab:beta" in r.getMessage() for r in caplog.records)
    assert not any("token-mine" in r.getMessage() for r in caplog.records)


def _replace(store: HandedRefreshTokenStore, credential: str) -> None:
    with store.exclusive():
        store.read()
        store.write(credential)


def test_a_write_without_a_read_under_the_hold_is_refused(rig):
    store = _enrolled(rig)
    with pytest.raises(RuntimeError, match="nothing to compare"):
        store.write("token-two")
    with store.exclusive(), pytest.raises(RuntimeError, match="nothing to compare"):
        store.write("token-two")


def test_a_busy_file_is_retried_by_the_handing_worker_never_the_writer(rig):
    store = _enrolled(rig)
    tries: list[str] = []
    real = store.hand

    def hand(work, label, save):
        tries.append(threading.current_thread().name)
        if len(tries) < 3:
            raise ArtifactBusy("config.json in application is claimed")
        return real(work, label, save)

    store.hand = hand
    store.interval = 0.01
    with store.exclusive():
        assert store.read() == "token-one"
    assert len(tries) == 3
    assert all(name == threading.current_thread().name for name in tries)


def test_a_missing_entry_is_the_stores_own_refusal(rig):
    store = rig.path._store_for("gitlab:nobody", "gitlab")  # noqa: SLF001
    with store.exclusive():
        assert store.read() is None
        with pytest.raises(StoreEntryMissing):
            store.write("token")


# ── A connector row filing into a project enrolled since start ───────────────


def test_connector_check_names_a_row_for_a_later_project_and_probes_the_rest(rig):
    paths = rig.daemon.storage.paths
    _write(
        paths.resolve(APPLICATION, "projects.toml"),
        render_registry(
            {pid: ProjectEntry(path=rig.root / pid) for pid in (*WATCHED, "zeta")}
        ),
    )
    for instance in ("gitlab:zeta", "gitlab:alpha"):
        enrol_connector(
            rig.daemon.storage,
            system="gitlab",
            instance=instance,
            credential="glpat-0123456789",
            probe=lambda system, credential: "accepted",
            project="group/project",
        )
    fresh = refreshed(
        rig.daemon,
        store_for=rig.path._store_for,  # noqa: SLF001
        enrolled_now=(*WATCHED, "zeta"),
    )
    assert "gitlab:zeta" not in fresh.connectors
    assert "gitlab:alpha" in fresh.connectors
    (named,) = [row for row in fresh.unbuilt if row.instance == "gitlab:zeta"]
    assert RESTART_SENTENCE in named.reason and "zeta" in named.reason


# ── The master key, once, at start ───────────────────────────────────────────


def _crypto_daemon(rig: Rig, keychain) -> Daemon:
    from dataclasses import replace

    return replace(rig.daemon, crypto=LazyKeyCrypto(keychain, "master"))


def test_a_locked_keychain_refuses_the_start_naming_it(rig):
    keychain = _Locked()
    with pytest.raises(DaemonRefused, match="keychain"):
        daemon_module._master_key(_crypto_daemon(rig, keychain), rig.writer)  # noqa: SLF001
    assert keychain.asked == 1


def test_no_key_enrolled_starts_and_refuses_each_encrypted_step_naming_key_enrol(rig):
    keychain = _NoKey()
    daemon = _crypto_daemon(rig, keychain)
    assert daemon_module._master_key(daemon, rig.writer) is False  # noqa: SLF001
    assert keychain.asked == 1
    for _ in range(3):
        with pytest.raises(KeyNotFound, match="pm-ai key enrol"):
            daemon.crypto.encrypt(b"secret")
    assert keychain.asked == 1, "no further keychain call"


def test_an_enrolled_key_is_fetched_once_on_the_writer_thread(rig, monkeypatch):
    keychain = _Held()
    asked_on: list[str] = []
    real = keychain.fetch

    def fetch(name: str) -> bytes:
        asked_on.append(threading.current_thread().name)
        return real(name)

    keychain.fetch = fetch  # type: ignore[method-assign]
    daemon = _crypto_daemon(rig, keychain)
    daemon_module._master_key(daemon, rig.writer)  # noqa: SLF001
    sealed = daemon.crypto.encrypt(b"secret")
    assert daemon.crypto.decrypt(sealed) == b"secret"
    assert asked_on == [rig.writer.name]


def test_with_encryption_off_no_key_is_asked_for(rig):
    keychain = _Locked()
    daemon_module._master_key(rig.daemon, rig.writer)  # noqa: SLF001 — plaintext cipher
    assert keychain.asked == 0


# ── The daemon's own connection ──────────────────────────────────────────────


def test_the_writer_connection_is_autocommit_with_a_100_ms_wait(rig):
    db = rig.daemon.storage._db  # noqa: SLF001
    assert db.autocommit is True
    timeout = rig.writer.call(lambda: db.execute("PRAGMA busy_timeout").fetchone()[0], label="ask")
    assert timeout == 100
    assert not db.in_transaction


def _transcript_rows(rig: Rig) -> list[tuple[str, str]]:
    with sqlite3.connect(rig.store) as db:
        return list(db.execute("SELECT meeting_id, state FROM transcript_state"))


def test_an_older_writer_s_statements_are_one_transaction_in_a_claimed_step(rig):
    from pm_ai.domain.transcripts import TranscriptStateKind

    with sqlite3.connect(rig.store) as db:
        db.execute("DROP TABLE transcript_ingest_attempts")
    with pytest.raises(sqlite3.OperationalError, match="transcript_ingest_attempts"):
        rig.writer.call(
            lambda: rig.daemon.storage.record_transcript_state(
                PERSONAL, "mtg_1", TranscriptStateKind.INGEST_FAILED, "broke", retryable=True
            ),
            label="record a failed ingestion",
        )
    assert _transcript_rows(rig) == [], "the first statement went with the second"
    assert not rig.daemon.storage._db.in_transaction  # noqa: SLF001


def test_an_older_writer_commits_with_its_step(rig):
    from pm_ai.domain.transcripts import TranscriptStateKind

    def two_writes() -> None:
        storage = rig.daemon.storage
        storage.record_transcript_state(
            PERSONAL, "mtg_1", TranscriptStateKind.INGESTED, "ok", retryable=False
        )
        storage.record_transcript_state(
            PERSONAL, "mtg_2", TranscriptStateKind.INGESTED, "ok", retryable=False
        )
        # Deferred to the step's end: another connection sees nothing yet.
        assert _transcript_rows(rig) == []

    rig.writer.call(two_writes, label="two writes")
    assert sorted(_transcript_rows(rig)) == [("mtg_1", "ingested"), ("mtg_2", "ingested")]


def test_an_older_writer_meets_the_version_check_first_and_writes_nothing(rig):
    from pm_ai.domain.transcripts import TranscriptStateKind

    with sqlite3.connect(rig.store) as db:
        db.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    with pytest.raises(SchemaVersionMoved):
        rig.writer.call(
            lambda: rig.daemon.storage.record_transcript_state(
                PERSONAL, "mtg_1", TranscriptStateKind.INGESTED, "ok", retryable=False
            ),
            label="record",
        )
    assert _transcript_rows(rig) == []
    assert rig.halts == [("SchemaVersionMoved", None)]


def test_an_older_store_is_its_own_refusal_never_upgrade_pm_ai(rig):
    from pm_ai.storage.service import SchemaVersionRegressed

    with sqlite3.connect(rig.store) as db:
        db.execute("DELETE FROM schema_version")
    with pytest.raises(SchemaVersionRegressed) as regressed:
        rig.writer.call(lambda: rig.daemon.storage.prune_requests(older_than=timedelta(days=7)), label="prune")
    assert "upgrade" not in str(regressed.value)
    assert "replaced or rewritten" in str(regressed.value)


def test_a_rollback_that_fails_halts_the_writer_naming_it(rig, monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger=DAEMON_LOGGER)
    db = rig.daemon.storage._db  # noqa: SLF001

    def broken_rollback() -> None:
        raise sqlite3.OperationalError("disk I/O error")

    def fails() -> None:
        db.execute(
            "INSERT INTO requests VALUES ('job_x000000000000000', 'k', 's', 'queued', NULL, 'a', 'a')"
        )
        monkeypatch.setattr(db, "rollback", broken_rollback)
        raise ValueError("the step failed")

    with pytest.raises(OperationalStoreStuck, match="could not be rolled back"):
        rig.writer.call(fails, label="fails")
    assert rig.halts == [("OperationalStoreStuck", None)]
    with pytest.raises(prompts.WriterStopped, match="halted"):
        rig.writer.call(lambda: None, label="after")
    monkeypatch.undo()
    db.execute("ROLLBACK") if db.in_transaction else None
    assert any(getattr(r, "event", None) == "writer-halted" for r in caplog.records)


def test_a_claimed_step_meeting_a_held_claim_fails_the_attempt_naming_it(rig, monkeypatch):
    def held(daemon, *, scope, document):
        raise ArtifactBusy("daily_dashboard.md in personal is claimed by another pm-ai process; nothing was changed. Run the command again.")

    monkeypatch.setattr(prompts, "publish_dashboard", held)
    job = rig.ask("dashboard").body["job_id"]
    row = rig.wait(job, "failed")
    result = json.loads(row.result)
    assert result["type"] == "ArtifactBusy"
    assert "daily_dashboard.md in personal is claimed" in result["error"]


def test_a_command_that_would_ask_a_question_never_asks_at_the_daemons_terminal(rig, monkeypatch):
    import sys

    class _Terminal:
        def isatty(self) -> bool:
            return True

        def readline(self, *args: object) -> str:  # pragma: no cover — the failure
            raise AssertionError("the daemon asked a question at its own terminal")

    monkeypatch.setattr(sys, "stdin", _Terminal())
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("asked")))
    transcript = rig.root / "standup.txt"
    transcript.write_text("Alice: we ship on Friday\n")
    reply = rig.ask(f"transcript drop {transcript} --scope personal")
    # Its declared bound (the capture guard's `git` question) makes it a job.
    assert reply.status == 202, reply.body
    result = json.loads(rig.wait(reply.body["job_id"], "done", "failed").result)
    assert result["exit"] == EXIT_REFUSAL, result
    assert "not a terminal" in result["output"]
