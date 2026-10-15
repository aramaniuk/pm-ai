"""Story 4r, second review round: each fix with the test that would have caught it."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_line_answered_or_acknowledged import (  # noqa: F401 — fixtures
    APPLICATION,
    PERSONAL,
    WATCHED,
    Rig,
    _NoKey,
    held_render,
    rig,
)
from test_line_requests_hardening import INSTANCE, _enrol_graph, _Provider
from pm_ai.app import daemon as daemon_module
from pm_ai.app import prompts
from pm_ai.app.concurrency import StepKind, WriterStopped, WriterThread
from pm_ai.app.prompts import DAEMON_LOG_NAME, DAEMON_LOGGER, RequestPath
from pm_ai.app.wiring import build
from pm_ai.connectors.graph.auth import CredentialStoreBusy, StoreBusy
from pm_ai.core.connector_enrolment import enrol_connector, stored_credentials
from pm_ai.core.project_registry import ProjectEntry, render_registry
from pm_ai.domain.health import Report
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.ports import DaemonRefused, KeyNotFound
from pm_ai.storage.service import (
    SCHEMA_VERSION,
    OperationalStoreStuck,
    SchemaVersionChanged,
    SchemaVersionMoved,
    SchemaVersionRegressed,
    SchemaVersionTooNew,
)
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, RESTART_SENTENCE


def _result(rig: Rig, job: str, *states: str) -> dict[str, object]:
    return json.loads(rig.wait(job, *(states or ("done",))).result)


# ── 1. Only a job that is the same job is deduplicated ───────────────────────


def test_two_project_adds_of_different_directories_are_two_jobs(rig):
    first = rig.ask(f"project add {rig.root / 'one'}").body["job_id"]
    second = rig.ask(f"project add {rig.root / 'two'}").body["job_id"]
    assert first != second
    results = [_result(rig, job) for job in (first, second)]
    # Both ran. If they overlapped, the later save met the registry the earlier
    # one changed and refused by name (compare-and-save) rather than lose it.
    assert results[0]["exit"] == EXIT_OK or results[1]["exit"] == EXIT_OK
    for result in results:
        assert result["exit"] == EXIT_OK or (
            result["exit"] == EXIT_REFUSAL
            and "changed while this project was being enrolled" in result["output"]
        ), result
    registry = (rig.root / ".pm-ai" / "projects.toml").read_text()
    assert "one" in registry or "two" in registry


def test_two_drops_of_different_files_in_one_scope_are_two_jobs(rig):
    files = []
    for name in ("a.txt", "b.txt"):
        path = rig.root / name
        path.write_text("Alex: hello\n")
        files.append(path)
    jobs = [rig.ask(f"transcript drop {path} --scope personal").body["job_id"] for path in files]
    assert jobs[0] != jobs[1]
    for job in jobs:
        rig.wait(job, "done", "failed")


# ── 2, 3. A final move always lands, and publishes ───────────────────────────


def test_a_final_move_that_meets_a_busy_store_still_ends_done(rig, held_render, monkeypatch):
    monkeypatch.setattr(prompts, "_STATE_ATTEMPTS", 1)
    # Since story 9b the render's own claimed step records it in the store too;
    # that record would meet the held lock first. Under test here is the final
    # move alone, so the render records nothing.
    monkeypatch.setattr(
        rig.daemon.storage, "recording_render", lambda scope, *, at: contextlib.nullcontext()
    )
    job = rig.ask("dashboard").body["job_id"]
    rig.wait(job, "running")
    other = sqlite3.connect(rig.store, timeout=0)
    other.execute("BEGIN IMMEDIATE")
    held_render.set()
    time.sleep(1.0)
    assert rig.row(job).state == "running"
    other.rollback()
    other.close()
    assert _result(rig, job)["exit"] == EXIT_OK


def test_the_stream_ends_after_done_even_when_no_read_succeeds(rig, held_render, monkeypatch):
    async def scenario():
        rig.path.events.attach(asyncio.get_running_loop())
        job = (await rig.path.prompt("dashboard")).body["job_id"]
        stream = await rig.path.job_events(job)
        states = [(await anext(stream))["state"]]

        def unreadable(job_id):
            raise OSError("gone")

        monkeypatch.setattr(rig.daemon.storage, "read_request", unreadable)
        held_render.set()
        async for event in stream:
            states.append(event["state"])
        return states

    states = asyncio.run(asyncio.wait_for(scenario(), 15))
    assert states[-1] == "done"


def test_a_row_settled_by_the_shutdown_pass_ends_its_stream(rig, held_render):
    async def scenario():
        rig.path.events.attach(asyncio.get_running_loop())
        job = (await rig.path.prompt("dashboard")).body["job_id"]
        while rig.row(job).state != "running":
            await asyncio.sleep(0.02)
        stream = await rig.path.job_events(job)
        states = [(await anext(stream))["state"]]
        rig.path.shutdown_pass()
        async for event in stream:
            states.append(event["state"])
        return states

    states = asyncio.run(asyncio.wait_for(scenario(), 15))
    assert states == ["running", "failed"]


# ── 4. A failed commit is rolled back, never joined ──────────────────────────


def test_a_failed_commit_is_rolled_back_and_the_next_step_starts_clean(rig, monkeypatch):
    db = rig.daemon.storage._db  # noqa: SLF001
    real = db.execute

    def commit_fails(sql, *args, **kwargs):
        if sql == "COMMIT":
            raise sqlite3.OperationalError("disk I/O error")
        return real(sql, *args, **kwargs)

    monkeypatch.setattr(db, "execute", commit_fails)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        rig.writer.call(
            lambda: rig.daemon.storage.create_request(
                job_id="job_lost000000000000", kind="dashboard", scope="personal"
            ),
            label="record",
        )
    monkeypatch.undo()
    assert not db.in_transaction
    rig.writer.call(
        lambda: rig.daemon.storage.create_request(
            job_id="job_next000000000000", kind="dashboard", scope="personal"
        ),
        label="record",
    )
    assert [row[0] for row in rig.rows()] == ["job_next000000000000"]


def test_a_failed_commit_that_cannot_roll_back_halts_the_writer(rig, monkeypatch):
    db = rig.daemon.storage._db  # noqa: SLF001
    real = db.execute

    def commit_fails(sql, *args, **kwargs):
        if sql == "COMMIT":
            raise sqlite3.OperationalError("disk I/O error")
        return real(sql, *args, **kwargs)

    def rollback_fails():
        raise sqlite3.OperationalError("still broken")

    monkeypatch.setattr(db, "execute", commit_fails)
    monkeypatch.setattr(db, "rollback", rollback_fails)
    with pytest.raises(OperationalStoreStuck):
        rig.writer.call(
            lambda: rig.daemon.storage.create_request(
                job_id="job_lost000000000000", kind="dashboard", scope="personal"
            ),
            label="record",
        )
    assert rig.halts == [("OperationalStoreStuck", None)]
    monkeypatch.undo()
    if db.in_transaction:
        db.execute("ROLLBACK")


# ── 5. A second interrupt during the writer's wait ends the process at once ──


def test_a_second_interrupt_while_the_writer_drains_exits_without_closing_anything(rig, monkeypatch):
    exits: list[int] = []

    class Exited(BaseException):
        pass

    def exit_now(code: int) -> None:
        exits.append(code)
        raise Exited

    real_close = rig.writer.close
    calls: list[str] = []

    def interrupted() -> None:
        if not calls:
            calls.append("interrupted")
            raise KeyboardInterrupt
        real_close()

    monkeypatch.setattr(daemon_module, "_exit_now", exit_now)
    monkeypatch.setattr(rig.writer, "close", interrupted)
    with pytest.raises(Exited):
        daemon_module._stop_writer(rig.path)  # noqa: SLF001
    assert exits == [daemon_module.EXIT_INTERRUPTED]


# ── 6. The refresh slot is bounded ───────────────────────────────────────────


def test_a_refresh_slot_held_past_the_budget_is_refused_by_name(rig):
    _enrol_graph(rig)
    connector = rig.path._fresh().connectors[INSTANCE]  # noqa: SLF001
    store = connector.auth.store
    store.refresh_wait = 0.1
    connector.auth.client_factory = lambda client_id, authority: _Provider("rt")
    store.refresh_slot.acquire()
    try:
        with pytest.raises(StoreBusy, match="held its slot"):
            with store.refreshing():
                pass
        with pytest.raises(CredentialStoreBusy, match="could not be refreshed"):
            connector.auth.access_token(force_refresh=True)
    finally:
        store.refresh_slot.release()
    assert connector.auth.access_token(force_refresh=True) == "an-access-token"


# ── 8. Messages and the error hierarchy ──────────────────────────────────────


def test_a_regressed_store_is_its_own_refusal_not_a_newer_one():
    regressed = SchemaVersionRegressed(0)
    assert isinstance(regressed, SchemaVersionChanged)
    assert not isinstance(regressed, SchemaVersionTooNew)
    assert not isinstance(regressed, SchemaVersionMoved)
    assert regressed.found == 0 and "upgrade" not in str(regressed)
    assert isinstance(SchemaVersionMoved(2), SchemaVersionChanged)


def test_a_stopped_writer_while_recording_is_answered_as_stopped(rig):
    with sqlite3.connect(rig.store) as db:
        db.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    reply = rig.ask("dashboard")
    assert reply.status == 503
    assert "the daemon stopped" in reply.body["detail"]
    assert "Try again" not in reply.body["detail"]


def test_a_job_meeting_no_enrolled_key_names_key_enrol(rig, monkeypatch):
    def no_key(daemon, *, scope, now, **kwargs):
        raise KeyNotFound(daemon_module.NO_KEY_ENROLLED)

    monkeypatch.setattr(prompts, "prepare_dashboard", no_key)
    result = _result(rig, rig.ask("dashboard").body["job_id"], "failed")
    assert "pm-ai key enrol" in result["error"] and "bug" not in result["error"]


# ── 9. The halt names every unfinished job ───────────────────────────────────


def test_a_schema_stop_names_every_unfinished_job_for_the_restart(rig, held_render):
    jobs = [rig.ask(f"dashboard --scope {scope}").body["job_id"] for scope in ("personal", "project:alpha")]
    for job in jobs:
        rig.wait(job, "running")
    with sqlite3.connect(rig.store) as db:
        db.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    held_render.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not rig.halts:
        time.sleep(0.02)
    rig.writer.close()
    with sqlite3.connect(rig.store) as db:
        db.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not rig.daemon.storage.diagnostic_lines(DAEMON_LOG_NAME):
        time.sleep(0.05)
    later = WriterThread(log=logging.getLogger(DAEMON_LOGGER))
    later.start()
    try:
        path = RequestPath(daemon=rig.daemon, writer=later, pool=rig.pool, diagnose=lambda: Report(()))
        assert sorted(path.restart_pass()) == sorted(jobs)
    finally:
        later.close()
    for job in jobs:
        assert json.loads(rig.row(job).result)["error"].startswith(
            "the daemon stopped on a schema-version mismatch"
        )


# ── 10. No exception text in the diagnostic log ──────────────────────────────


def test_an_exception_is_logged_by_type_and_frames_never_its_text(tmp_path):
    daemon = build(tmp_path, APPLICATION, keychain=_NoKey(), encryption_disabled=True)
    attached = daemon_module._RequestLog.attach(daemon)  # noqa: SLF001
    try:
        try:
            raise ValueError("Bearer eyJ-secret-token")
        except ValueError:
            logging.getLogger(DAEMON_LOGGER).exception("a step failed")
    finally:
        attached.detach()
        daemon.storage.close()
    text = (tmp_path / ".pm-ai" / "logs" / DAEMON_LOG_NAME).read_text()
    assert "a step failed" in text and "ValueError" in text
    assert "eyJ-secret-token" not in text and "Bearer" not in text


# ── 11. A step refused without starting answers an unbounded wait ────────────


def test_call_async_without_a_start_wait_answers_a_step_refused_at_close():
    writer = WriterThread(log=logging.getLogger("test.writer"))  # never started

    async def scenario():
        waiting = asyncio.ensure_future(writer.call_async(lambda: "never", label="queued"))
        await asyncio.sleep(0.05)
        writer.close()
        return await asyncio.wait_for(waiting, 5)

    with pytest.raises(WriterStopped, match="closed"):
        asyncio.run(scenario())


# ── 12. Start order ──────────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_second_daemon_is_refused_for_the_port_before_the_store_or_the_keychain(tmp_path, monkeypatch):
    from pm_ai.core.config import Config
    from pm_ai.storage.crypto import LazyKeyCrypto
    from pm_ai.storage.service import StorageService

    keychain = _NoKey()
    daemon = build(tmp_path, APPLICATION, keychain=keychain)
    port = _free_port()
    daemon = replace(daemon, config=Config(daemon_port=port), crypto=LazyKeyCrypto(keychain, "master"))
    adopted: list[str] = []
    monkeypatch.setattr(StorageService, "adopt_daemon_connection", lambda self: adopted.append("x"))
    first = socket.socket()
    first.bind(("127.0.0.1", port))
    first.listen(1)
    try:
        with pytest.raises(DaemonRefused, match=f"port {port}"):
            daemon_module.serve(daemon, probes=lambda: Report(()))
    finally:
        first.close()
    assert adopted == [] and keychain.asked == 0


def test_a_request_log_that_cannot_open_still_closes_the_store(tmp_path, monkeypatch):
    from pm_ai.core.config import Config

    daemon = build(tmp_path, APPLICATION, keychain=_NoKey(), encryption_disabled=True)
    daemon = replace(daemon, config=Config(daemon_port=_free_port()))

    def unopenable(name):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(daemon.storage, "diagnostic_log", unopenable)
    with pytest.raises(DaemonRefused, match="request log"):
        daemon_module.serve(daemon, probes=lambda: Report(()))
    with pytest.raises(sqlite3.ProgrammingError):
        daemon.storage._db.execute("SELECT 1")  # noqa: SLF001 — closed


# ── 15. Missing tests ────────────────────────────────────────────────────────


def test_a_credential_save_during_the_stop_is_still_made(rig):
    enrol_connector(
        rig.daemon.storage, system="gitlab", instance="gitlab:beta",
        credential="token-one", probe=lambda system, credential: "accepted",
    )
    store = rig.path._store_for("gitlab:beta", "gitlab")  # noqa: SLF001
    with store.exclusive():
        assert store.read() == "token-one"
        rig.writer.stop_accepting()
        store.write("token-two")
    assert stored_credentials(rig.daemon.storage)["gitlab:beta"]["credential"] == "token-two"


def test_a_registry_changed_before_the_save_refuses_the_enrolment(rig, monkeypatch):
    real = prompts.onboard_in_daemon
    registry = rig.root / ".pm-ai" / "projects.toml"

    def meddled(daemon, path, alias, *, hand):
        def hand_after_a_change(work, label):
            entries = {pid: ProjectEntry(path=rig.root / pid) for pid in (*WATCHED, "other")}
            registry.write_bytes(render_registry(entries))
            return hand(work, label)

        return real(daemon, path, alias, hand=hand_after_a_change)

    monkeypatch.setattr(prompts, "onboard_in_daemon", meddled)
    result = _result(rig, rig.ask(f"project add {rig.root / 'zeta'}").body["job_id"])
    assert result["exit"] == EXIT_REFUSAL
    assert "changed while this project was being enrolled" in result["output"]
    text = registry.read_text()
    assert "other" in text and "zeta" not in text


def test_the_daily_prune_removes_old_rows_and_stops_on_cancel(rig):
    now = datetime.now(timezone.utc)
    with sqlite3.connect(rig.store) as db:
        db.execute(
            "INSERT INTO requests VALUES ('job_old0000000000000', 'dashboard', 'personal', "
            "'done', NULL, ?, ?)",
            ((now - timedelta(days=8)).isoformat(), (now - timedelta(days=8)).isoformat()),
        )
    waits: list[float] = []

    async def scenario():
        async def a_day(seconds: float) -> None:
            waits.append(seconds)
            if len(waits) > 1:
                await asyncio.sleep(3600)

        rig.path.wait_a_day = a_day
        task = asyncio.ensure_future(rig.path.prune_daily())
        while rig.row("job_old0000000000000") is not None:
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(asyncio.wait_for(scenario(), 10))
    assert waits[0] == 86400.0


def test_a_dashboard_for_a_project_enrolled_since_start_names_the_restart(rig):
    entries = {pid: ProjectEntry(path=rig.root / pid) for pid in (*WATCHED, "zeta")}
    (rig.root / ".pm-ai" / "projects.toml").write_bytes(render_registry(entries))
    # Refused in the answer since story 4q, before any job is recorded: the
    # line's scope is resolved per request against the watched projects.
    answered = rig.ask("dashboard --scope project:zeta")
    assert answered.status == 200
    assert answered.body["exit"] == EXIT_REFUSAL
    assert RESTART_SENTENCE in answered.body["output"]
    assert rig.rows() == []


# ── 14. A kill through the writer thread's claimed step ──────────────────────

_CHILD = textwrap.dedent(
    """
    import logging, os, sys, time
    from datetime import datetime, timezone
    from pathlib import Path
    from pm_ai.app.concurrency import WriterThread
    from pm_ai.domain.identity import DataScope, ScopeKind
    from pm_ai.platform.claims import FlockClaims
    from pm_ai.platform.paths import ScopePaths
    from pm_ai.platform.vcs import GitVcs
    from pm_ai.storage.crypto import PlaintextCrypto
    from pm_ai.storage.service import StorageService

    root, marker = Path(sys.argv[1]), Path(sys.argv[2])
    storage = StorageService(
        ScopePaths.rooted(root), now=lambda: datetime.now(timezone.utc),
        vcs=GitVcs(), crypto=PlaintextCrypto(), claims=FlockClaims(),
    )
    writer = WriterThread(log=logging.getLogger("child"), step_scope=storage.claimed_step)
    writer.start()
    writer.call(storage.adopt_daemon_connection, label="adopt")
    real = os.replace
    def stalled(source, target):
        marker.write_text("staged")
        time.sleep(60)
        return real(source, target)
    os.replace = stalled
    personal = DataScope(ScopeKind.PERSONAL)

    def step():
        storage.create_request(job_id="job_half", kind="dashboard", scope="personal")
        storage.write_artifact(b"new dashboard\\n" * 1000, scope=personal, artifact="daily_dashboard.md")

    writer.call(step, label="half")
    """
)


def test_a_kill_inside_a_writer_step_leaves_no_row_and_every_file_whole(tmp_path):
    from pm_ai.platform.claims import FlockClaims
    from pm_ai.platform.paths import ScopePaths
    from pm_ai.platform.vcs import GitVcs
    from pm_ai.storage.crypto import PlaintextCrypto
    from pm_ai.storage.service import StorageService

    def open_store() -> StorageService:
        return StorageService(
            ScopePaths.rooted(tmp_path), now=lambda: datetime.now(timezone.utc),
            vcs=GitVcs(), crypto=PlaintextCrypto(), claims=FlockClaims(),
        )

    first = open_store()
    target = first.write_artifact(b"old dashboard\n", scope=PERSONAL, artifact="daily_dashboard.md")
    first.close()
    marker, script = tmp_path / "marker", tmp_path / "child.py"
    script.write_text(_CHILD)
    child = subprocess.Popen(
        [sys.executable, str(script), str(tmp_path), str(marker)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while not marker.exists():
            if child.poll() is not None or time.monotonic() > deadline:
                pytest.fail(f"the child never reached the step: {child.communicate()[1].decode()}")
            time.sleep(0.02)
        os.kill(child.pid, signal.SIGKILL)
        child.wait(10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    assert target.read_bytes() == b"old dashboard\n", "the published file is whole: the old one"
    for leftover in target.parent.iterdir():
        if leftover.name.endswith(".part"):
            # Never visible under the file's name, and itself complete.
            assert leftover.read_bytes() == b"new dashboard\n" * 1000
    reopened = open_store()
    try:
        assert reopened.read_request("job_half") is None, "the step's row went with it"
    finally:
        reopened.close()
