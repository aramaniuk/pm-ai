"""Story 4r, review fixes: what the first cut of the request path got wrong.

Uses the in-process rig from `test_line_answered_or_acknowledged.py`: a real
composed daemon over a throwaway root, its writer thread and I/O pool.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from test_line_answered_or_acknowledged import (  # noqa: F401 — fixtures
    APPLICATION,
    Rig,
    held_render,
    rig,
)
from pm_ai.app import daemon as daemon_module
from pm_ai.app import prompts
from pm_ai.app.entry import DAEMON_BOUNDS
from pm_ai.app.prompts import DAEMON_LOG_NAME, DAEMON_LOGGER, RequestPath
from pm_ai.app.wiring import HandedRefreshTokenStore, refreshed
from pm_ai.connectors.graph import CLIENT_ID_KEY, REACH_BACK_KEY, WIDTH_KEY
from pm_ai.connectors.graph.auth import GRAPH_RESOURCE_SCOPES, SealedCredential
from pm_ai.core.connector_enrolment import enrol_connector, stored_credentials
from pm_ai.domain.health import Report
from pm_ai.storage.service import StorageService
from pm_ai.surfaces.cli.dispatch import EXIT_UNEXPECTED, TABLE, TERMINAL_BOUND

# ── 3. A recorded row is always handed to a worker ───────────────────────────


def test_a_request_cancelled_after_its_row_is_written_still_runs_the_job(rig):
    entered, release = threading.Event(), threading.Event()
    holding = threading.Thread(
        target=lambda: rig.writer.call(lambda: (entered.set(), release.wait(10)), label="hold")
    )
    holding.start()
    assert entered.wait(5)

    async def leave() -> None:
        task = asyncio.ensure_future(rig.path.prompt("dashboard"))
        await asyncio.sleep(0.2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.5)  # the shielded record-and-start finishes

    asyncio.run(leave())
    holding.join()
    rows = rig.rows()
    assert len(rows) == 1
    rig.wait(rows[0][0], "done")


def test_a_row_no_worker_will_take_is_failed_not_left_queued(rig):
    rig.pool.shutdown()
    reply = rig.ask("dashboard")
    assert reply.status == 503 and reply.headers == {"Retry-After": "1"}
    assert "stopping" in reply.body["detail"]
    ((job, _, _, state),) = rig.rows()
    assert state == "failed"
    assert json.loads(rig.row(job).result)["error"] == "the daemon stopped before this job started"


def test_a_stopping_writer_is_answered_as_stopping_not_try_again(rig):
    rig.writer.stop_accepting()
    reply = rig.ask("dashboard")
    assert reply.status == 503 and reply.headers == {"Retry-After": "1"}
    assert "stopping" in reply.body["detail"] and "again" not in reply.body["detail"]


# ── 4. Every command the daemon runs declares its bound ──────────────────────


def _routable() -> list[tuple[str, ...]]:
    commands: list[tuple[str, ...]] = []
    for name, command in TABLE.items():
        if command.leaves:
            commands += [(name, leaf) for leaf in command.leaves]
        else:
            commands.append((name,))
    return [spelled for spelled in commands if spelled not in TERMINAL_BOUND]


@pytest.mark.parametrize("spelled", _routable(), ids=" ".join)
def test_every_command_the_daemon_runs_declares_its_bound(spelled, rig):
    assert spelled in DAEMON_BOUNDS, f"`pm-ai {' '.join(spelled)}` declares no bound"
    seconds = DAEMON_BOUNDS[spelled](rig.daemon)
    assert seconds >= 0


def test_a_command_with_no_declared_bound_is_a_named_fault_never_a_guess(rig):
    bare = RequestPath(
        daemon=rig.daemon, writer=rig.writer, pool=rig.pool,
        diagnose=lambda: Report(()), bounds={},
    )
    reply = asyncio.run(bare.prompt("config show"))
    assert reply.body["exit"] == EXIT_UNEXPECTED
    assert "declares no bound" in reply.body["output"]


# ── 5. One refresh per instance, at the credential's refresh ─────────────────

INSTANCE = "graph:work"
GRANTED = " ".join(f"https://graph.microsoft.com/{s}" for s in sorted(GRAPH_RESOURCE_SCOPES))


class _Provider:
    """Microsoft's token endpoint for a refresh: rotates on each redemption."""

    def __init__(self, live: str) -> None:
        self.live = live
        self.lock = threading.Lock()
        self.inside = 0
        self.overlap = 0
        self.hold: threading.Event | None = None
        self.entered = threading.Event()

    def acquire_token_by_refresh_token(self, refresh_token, scopes):
        with self.lock:
            self.inside += 1
            self.overlap = max(self.overlap, self.inside)
        self.entered.set()
        if self.hold is not None:
            self.hold.wait(10)
        with self.lock:
            self.inside -= 1
            if refresh_token != self.live:
                return {"error": "invalid_grant", "error_description": "AADSTS70000: retired"}
            self.live = f"{refresh_token}+"
            return {
                "access_token": "an-access-token",
                "refresh_token": self.live,
                "expires_in": 3600,
                "scope": GRANTED,
                "token_type": "Bearer",
            }

    def acquire_token_silent(self, scopes, account, force_refresh=False):
        return None

    def get_accounts(self, username=None):
        return []


def _enrol_graph(rig: Rig) -> None:
    storage = rig.daemon.storage
    enrol_connector(
        storage, system="graph", instance=INSTANCE,
        credential=SealedCredential(refresh_token="rt", home_account_id="a.b").encode(),
        probe=lambda system, credential: "accepted",
    )
    storage.write_artifact(
        json.dumps(
            {
                "instance": INSTANCE, "system": "graph", "enabled": True,
                CLIENT_ID_KEY: "00000000-0000-0000-0000-000000000008",
                WIDTH_KEY: 24 * 60, REACH_BACK_KEY: 7 * 24 * 60,
            }
        ).encode(),
        scope=APPLICATION, artifact="connectors/", name=f"{INSTANCE}.json",
    )


def _held_token(rig: Rig) -> str:
    raw = stored_credentials(rig.daemon.storage)[INSTANCE]["credential"]
    return SealedCredential.decode(raw).refresh_token


def test_a_graph_connector_built_per_line_refreshes_through_the_writer(rig, monkeypatch):
    _enrol_graph(rig)
    seen: list[tuple[str, str]] = []
    for name in ("exclusive", "write_artifact"):
        real = getattr(StorageService, name)

        def wrapped(self, *args, _real=real, _name=name, **kwargs):
            seen.append((_name, threading.current_thread().name))
            return _real(self, *args, **kwargs)

        monkeypatch.setattr(StorageService, name, wrapped)
    fresh = rig.path._fresh()  # noqa: SLF001
    connector = fresh.connectors[INSTANCE]
    assert type(connector).__name__ == "GraphConnector", "never wrapped"
    assert isinstance(connector.auth.store, HandedRefreshTokenStore)
    provider = _Provider("rt")
    connector.auth.client_factory = lambda client_id, authority: provider
    assert connector.auth.access_token(force_refresh=True) == "an-access-token"
    assert _held_token(rig) == "rt+", "the rotated token was saved by compare-and-save"
    assert seen and {thread for _, thread in seen} == {rig.writer.name}


def test_two_builds_of_one_instance_share_one_refresh_slot_and_never_overlap(rig):
    _enrol_graph(rig)
    first = rig.path._fresh().connectors[INSTANCE]  # noqa: SLF001
    second = rig.path._fresh().connectors[INSTANCE]  # noqa: SLF001
    assert first.auth.store.refresh_slot is second.auth.store.refresh_slot
    provider = _Provider("rt")
    provider.hold = threading.Event()
    for connector in (first, second):
        connector.auth.client_factory = lambda client_id, authority: provider
    results: list[object] = []

    def refresh(connector) -> None:
        try:
            results.append(connector.auth.access_token(force_refresh=True))
        except Exception as error:  # noqa: BLE001 — reported below
            results.append(error)

    threads = [threading.Thread(target=refresh, args=(c,)) for c in (first, second)]
    for thread in threads:
        thread.start()
    assert provider.entered.wait(5)
    time.sleep(0.3)
    assert provider.inside == 1, "the second refresh waits for the first"
    provider.hold.set()
    for thread in threads:
        thread.join(15)
    assert provider.overlap == 1
    assert results == ["an-access-token", "an-access-token"]


# ── 6. `doctor` through the daemon ───────────────────────────────────────────


def test_doctor_through_the_daemon_reports_itself_without_asking_itself(rig, monkeypatch):
    asked: list[str] = []
    rig.path.diagnose = lambda: (asked.append("diagnosed"), Report(()))[1]
    reply = rig.ask("doctor")
    assert reply.status == 202  # its declared bound — two `git` questions — is over five
    result = json.loads(rig.wait(reply.body["job_id"], "done").result)
    assert asked == ["diagnosed"]
    assert result["exit"] == 0


# ── 9. One log file; the stop's line is the restart's input ──────────────────


def test_the_request_log_and_the_restart_pass_read_one_file():
    assert daemon_module.REQUEST_LOG is DAEMON_LOG_NAME


def test_the_line_a_schema_stop_writes_is_the_line_the_restart_pass_reads(rig):
    from pm_ai.storage.service import SchemaVersionMoved

    now = datetime.now(timezone.utc)
    with sqlite3.connect(rig.store) as db:
        db.execute(
            "INSERT INTO requests VALUES ('job_stopped000000000', 'dashboard', 'personal', "
            "'running', NULL, ?, ?)",
            (now.isoformat(), now.isoformat()),
        )
    daemon_module.record_writer_halt(SchemaVersionMoved(2), "job_stopped000000000")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not rig.daemon.storage.diagnostic_lines(DAEMON_LOG_NAME):
        time.sleep(0.05)
    assert rig.path.restart_pass() == ("job_stopped000000000",)
    assert json.loads(rig.row("job_stopped000000000").result)["error"] == (
        "the daemon stopped on a schema-version mismatch; see the diagnostic log"
    )


# ── 13, 14. Nothing kept for a job or a stream that is gone ──────────────────


def test_a_failed_job_leaves_no_notes(rig, monkeypatch):
    def broken(daemon, *, scope, now, **kwargs):
        raise RuntimeError("broken")

    monkeypatch.setattr(prompts, "prepare_dashboard", broken)
    job = rig.ask("dashboard").body["job_id"]
    rig.wait(job, "failed")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and rig.path.notes.last(job) is not None:
        time.sleep(0.02)
    assert rig.path.notes.last(job) is None


def test_a_stream_never_read_holds_no_subscription(rig):
    job = rig.ask("dashboard").body["job_id"]
    rig.wait(job, "done")

    async def open_and_leave() -> None:
        rig.path.events.attach(asyncio.get_running_loop())
        stream = await rig.path.job_events(job)
        assert stream is not None
        await stream.aclose()

    asyncio.run(open_and_leave())
    assert rig.path.events._readers == {}  # noqa: SLF001


# ── 15. Only `project:<id>` names a project ──────────────────────────────────


def test_a_chat_filed_personally_names_no_project():
    from pm_ai.app.wiring import _files_into  # noqa: PLC2701

    entry = {
        "categories": {"Alpha": "alpha"},
        "channels": [{"team": "t", "channel": "c", "project": "beta"}],
        "chats": [
            {"chat": "1", "scope": "personal"},
            {"chat": "2", "scope": "project:gamma"},
            {"chat": "3", "scope": "application"},
        ],
    }
    assert _files_into("graph:work", "graph", entry) == {"alpha", "beta", "gamma"}


def test_a_channel_names_a_project_under_either_spelling_and_personal_names_none():
    """`33i`: a channel's `scope` is read as a chat's, its `project` as before."""
    from pm_ai.app.wiring import _files_into  # noqa: PLC2701

    entry = {
        "channels": [
            {"team": "t", "channel": "c1", "project": "beta"},
            {"team": "t", "channel": "c2", "scope": "project:delta"},
            {"team": "t", "channel": "c3", "scope": "personal"},
            {"team": "t", "channel": "c4", "scope": "people:x"},
            {"team": "t", "channel": "c5", "project": " new "},
            {"team": "t", "channel": "c6", "project": "   "},
        ],
    }
    assert _files_into("graph:work", "graph", entry) == {"beta", "delta", "new"}


# ── 17. Per-line refreshes say nothing on the daemon's console ───────────────


def test_an_unbuildable_row_is_not_warned_about_on_every_line(rig, capsys):
    rig.daemon.storage.write_artifact(
        json.dumps({"instance": "graph:broken", "system": "graph", "enabled": True}).encode(),
        scope=APPLICATION, artifact="connectors/", name="graph:broken.json",
    )
    capsys.readouterr()
    for _ in range(3):
        fresh = refreshed(rig.daemon, store_for=rig.path._store_for)  # noqa: SLF001
        assert any(row.instance == "graph:broken" for row in fresh.unbuilt)
        rig.ask("config show")
    assert "graph:broken" not in capsys.readouterr().err


# ── 11. The migration claim belongs to opening, never to the running writer ──


def test_the_migration_claim_is_refused_on_the_daemons_connection(rig):
    with pytest.raises(RuntimeError, match="only when it is opened"):
        with rig.daemon.storage._migration_claim():  # noqa: SLF001
            pass


# ── 8. Start-time refusals ───────────────────────────────────────────────────


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.parametrize(
    "error",
    [OSError(13, "Permission denied"), None],
    ids=["unreadable-log", "busy-store"],
)
def test_a_restart_pass_refused_at_start_is_a_named_refusal(tmp_path, monkeypatch, error):
    from dataclasses import replace

    from pm_ai.app.wiring import build
    from pm_ai.core.config import Config
    from pm_ai.ports import DaemonRefused
    from pm_ai.storage.service import OperationalStoreBusy
    from test_line_answered_or_acknowledged import _NoKey

    daemon = build(tmp_path, APPLICATION, keychain=_NoKey(), encryption_disabled=True)
    daemon = replace(daemon, config=Config(daemon_port=_free_port()))
    raised = error if error is not None else OperationalStoreBusy("the store stayed locked")

    def refuse(self):
        raise raised

    monkeypatch.setattr(RequestPath, "restart_pass", refuse)
    with pytest.raises(DaemonRefused, match="did not start") as refused:
        daemon_module.serve(daemon, probes=lambda: Report(()))
    assert type(refused.value.__cause__) is type(raised)
    # `9a` and `9b` take the writer thread and the pool from the daemon.
    from pm_ai.app.concurrency import IOPool, WriterThread

    assert isinstance(daemon.writer, WriterThread) and isinstance(daemon.pool, IOPool)
