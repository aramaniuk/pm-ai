"""`project add` and `transcript drop` through the daemon, as steps (story 4r).

Andrei's decision on the held item: through the daemon both commands follow
the job rule — everything that reads or waits (`git`, file reads, binding,
extraction) is a bounded step on the I/O worker, and only the writes are
claimed steps on the writer thread. Typed at a shell they are unchanged.
"""

from __future__ import annotations

import asyncio
import functools
import io
import json
import logging
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from test_line_answered_or_acknowledged import (  # noqa: F401 — fixtures
    APPLICATION,
    PERSONAL,
    WATCHED,
    Rig,
    _NoKey,
    rig,
)
from pm_ai.app import daemon as daemon_module
from pm_ai.app import pipelines
from pm_ai.app.pipelines import drop_transcript
from pm_ai.app.prompts import DAEMON_LOGGER
from pm_ai.app.wiring import build, onboard_project
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.health import Report
from pm_ai.platform.paths import ScopePaths
from pm_ai.storage.service import StorageService
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_USAGE, RESTART_SENTENCE, dispatch

START = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
MEETING = "mtg_0123456789abcdef"
SAID = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Alex>we ship on Friday\n"


def _meeting() -> Meeting:
    return Meeting(
        meeting_id=MEETING,
        title="Weekly Sync",
        start=START,
        duration_minutes=30,
        attendees=(Actor("email:alex@example.com"),),
        scope=PERSONAL,
    )


def _job_result(rig: Rig, line: str) -> dict[str, object]:
    reply = rig.ask(line)
    assert reply.status == 202, reply.body
    row = rig.wait(reply.body["job_id"], "done", "failed")
    assert row.state == "done", row.result
    return json.loads(row.result)


def _normalised(text: str, root: Path) -> str:
    return text.replace(str(root.resolve()), "<root>").replace(str(root), "<root>")


# ── Every claim and write on the writer; no git and no keychain there ────────


def test_every_write_is_on_the_writer_thread_and_nothing_waits_there(rig, monkeypatch, tmp_path):
    rig.daemon.meetings.put(_meeting())
    seen: list[tuple[str, str]] = []
    for name in (
        "exclusive", "write_artifact", "write_capture", "write_project_gitignore",
        "append_event_log", "_transaction", "_publish", "stage_unless_acted",
        "record_transcript_state",
    ):
        real = getattr(StorageService, name)

        def wrapped(self, *args, _real=real, _name=name, **kwargs):
            seen.append((_name, threading.current_thread().name))
            return _real(self, *args, **kwargs)

        monkeypatch.setattr(StorageService, name, wrapped)
    spawned: list[str] = []
    real_run = subprocess.run

    def run(*args, **kwargs):
        spawned.append(threading.current_thread().name)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    keychain = rig.daemon.keychain
    asked_before = keychain.asked
    transcript = tmp_path / "standup.vtt"
    transcript.write_text(SAID)
    project = tmp_path / "zeta"

    for line in (
        "dashboard",
        "connector check",
        "doctor",
        f"project add {project}",
        f"transcript drop {transcript} --scope personal --meeting {MEETING}",
    ):
        result = _job_result(rig, line)
        # `connector check` answers 4: the built-in GitLab rows hold no token.
        assert result["exit"] in (EXIT_OK, 4), (line, result)
    assert rig.ask("config show").body["exit"] == EXIT_OK

    writes = {name for name, _ in seen}
    assert {"write_capture", "write_project_gitignore", "write_artifact", "exclusive"} <= writes
    elsewhere = [(name, thread) for name, thread in seen if thread != rig.writer.name]
    assert elsewhere == [], f"claims or writes off the writer thread: {elsewhere}"
    assert spawned, "git was asked somewhere"
    assert rig.writer.name not in spawned, "a subprocess ran on the writer thread"
    assert keychain.asked == asked_before, "no keychain call after start"


# ── Output and exit code match the terminal's ────────────────────────────────


def test_project_add_through_the_daemon_says_what_the_terminal_says(rig, tmp_path):
    target = rig.root / "zeta"
    daemon_said = _job_result(rig, f"project add {target}")
    again = _job_result(rig, f"project add {target}")

    root = tmp_path / "other"
    out = io.StringIO()
    paths = ScopePaths.rooted(root)

    def shell(argv: list[str]) -> tuple[str, int]:
        out.seek(0)
        out.truncate()
        code = dispatch(
            argv,
            daemon=None,
            diagnose=lambda: Report(()),
            probe_connectors=None,
            onboard=lambda path, alias: onboard_project(_NoKey(), path, alias, paths=paths),
            out=out,
            err=out,
        )
        return out.getvalue(), code

    first, code = shell(["project", "add", str(root / "zeta")])
    assert daemon_said["exit"] == code == EXIT_OK
    assert _normalised(daemon_said["output"], rig.root) == _normalised(first, root)
    assert RESTART_SENTENCE in first
    second, code = shell(["project", "add", str(root / "zeta")])
    assert again["exit"] == code == EXIT_OK
    assert _normalised(again["output"], rig.root) == _normalised(second, root)
    registry = (rig.root / ".pm-ai" / "projects.toml").read_text()
    assert "zeta" in registry and all(project in registry for project in WATCHED)


def test_transcript_drop_through_the_daemon_says_what_the_terminal_says(rig, tmp_path):
    rig.daemon.meetings.put(_meeting())
    dropped = rig.root / "standup.vtt"
    dropped.write_text(SAID)
    daemon_said = _job_result(rig, f"transcript drop {dropped} --scope personal --meeting {MEETING}")

    root = tmp_path / "other"
    other = build(root, DataScope(ScopeKind.APPLICATION), keychain=_NoKey(), encryption_disabled=True)
    try:
        other.meetings.put(_meeting())
        (root / "standup.vtt").write_text(SAID)
        out = io.StringIO()
        code = dispatch(
            ["transcript", "drop", str(root / "standup.vtt"), "--scope", "personal",
             "--meeting", MEETING],
            daemon=other,
            diagnose=lambda: Report(()),
            probe_connectors=None,
            transcript_drop=functools.partial(drop_transcript, other),
            out=out,
            err=out,
        )
    finally:
        other.storage.close()
    assert daemon_said["exit"] == code == EXIT_OK
    assert _normalised(daemon_said["output"], rig.root) == _normalised(out.getvalue(), root)


# ── A relative path names the daemon's directory, not the client's ───────────


@pytest.mark.parametrize(
    "line",
    ["project add zeta", "project add ./zeta alias", "transcript drop notes.vtt --scope personal"],
)
def test_a_relative_path_is_refused_by_name(rig, line):
    reply = rig.ask(line)
    assert reply.status == 200
    assert reply.body["exit"] == EXIT_USAGE
    assert "relative path" in reply.body["output"] and "absolute path" in reply.body["output"]
    assert rig.rows() == []


def test_a_home_relative_path_is_expanded_not_refused(rig, monkeypatch):
    monkeypatch.setenv("HOME", str(rig.root))
    result = _job_result(rig, "project add ~/zeta")
    assert result["exit"] == EXIT_OK
    assert (rig.root / "zeta" / ".gitignore").is_file()


# ── A deadline abandoning a bounded step ─────────────────────────────────────


def test_a_drop_stopped_in_a_bounded_step_fails_naming_its_last_write(rig, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=DAEMON_LOGGER)
    rig.daemon.meetings.put(_meeting())
    dropped = rig.root / "standup.vtt"
    dropped.write_text(SAID)
    entered, release = threading.Event(), threading.Event()
    real_extract = pipelines.extract

    def slow_extract(*args, **kwargs):
        entered.set()
        release.wait(30)
        return real_extract(*args, **kwargs)

    monkeypatch.setattr(pipelines, "extract", slow_extract)
    job = rig.ask(f"transcript drop {dropped} --scope personal --meeting {MEETING}").body["job_id"]
    assert entered.wait(10), "the drop never reached extraction"
    daemon_module._stop_writer(rig.path)  # noqa: SLF001 — the stop's deadline
    row = rig.row(job)
    assert row.state == "failed"
    assert "'save the capture'" in json.loads(row.result)["error"]
    captures = [
        path for path in rig.root.rglob("*-manual.vtt")
        if path.is_file() and "transcripts" in path.parts and "temp" not in path.parts
    ]
    assert len(captures) == 1 and captures[0].read_text() == SAID, "the capture is whole"
    release.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not any(
        "save the meeting record" in r.getMessage() for r in caplog.records
    ):
        time.sleep(0.02)
    assert any("save the meeting record" in r.getMessage() for r in caplog.records)
    assert rig.row(job).state == "failed"
    with sqlite3.connect(rig.store) as db:
        assert db.execute("SELECT count(*) FROM proposals").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM transcript_state").fetchone()[0] == 0


# ── `HandedStorage` fails closed ──────────────────────────────────────────────


def _public_storage_calls() -> set[str]:
    return {
        name
        for name, value in vars(StorageService).items()
        if not name.startswith("_") and (callable(value) or isinstance(value, property))
    }


def test_every_public_storage_call_is_classified_once():
    from pm_ai.app.wiring import HANDED_CALLS, NEVER_FROM_A_JOB, PLAIN_READS

    public = _public_storage_calls()
    handed, plain, never = set(HANDED_CALLS), set(PLAIN_READS), set(NEVER_FROM_A_JOB)
    unclassified = public - handed - plain - never
    assert unclassified == set(), (
        f"StorageService calls with no rule for a job: {sorted(unclassified)}. "
        f"List each in HANDED_CALLS, PLAIN_READS or NEVER_FROM_A_JOB (pm_ai/app/wiring.py)."
    )
    assert not (handed & plain or handed & never or plain & never), "classified twice"
    assert (handed | plain | never) <= public, "a rule for a call that does not exist"


def test_an_unclassified_or_forbidden_call_is_refused_never_run(rig, monkeypatch):
    from pm_ai.app.wiring import HandedStorage, UnclassifiedStorageCall

    ran = threading.Event()
    monkeypatch.setattr(StorageService, "write_somewhere_new", lambda self: ran.set(), raising=False)
    handed = HandedStorage(rig.daemon.storage, hand=lambda work, label: work())
    with pytest.raises(UnclassifiedStorageCall, match="write_somewhere_new"):
        handed.write_somewhere_new()
    with pytest.raises(UnclassifiedStorageCall, match="exclusive"):
        handed.exclusive
    assert not ran.is_set()


def test_no_handed_call_puts_a_git_question_on_the_writer_thread(rig, monkeypatch):
    from pm_ai.app.wiring import HandedStorage
    from pm_ai.domain.disclosure import DisclosureRecord
    from pm_ai.domain.harvest import Cursor
    from ledger_fixtures import entry

    spawned: list[str] = []
    real_run = subprocess.run

    def run(*args, **kwargs):
        spawned.append(threading.current_thread().name)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    handed = HandedStorage(
        rig.daemon.storage,
        hand=lambda work, label: rig.writer.call(work, label=label),
    )
    alpha = DataScope(ScopeKind.PROJECT, "alpha")
    events = rig.daemon.connectors["gitlab:alpha"].sample_events()
    assert events, "the GitLab adapter declares sample events"

    def calls() -> None:
        handed.persist_events(events, scope=alpha)
        handed.save_cursor("gitlab:alpha", Cursor(), None, None)
        handed.append_disclosure(
            DisclosureRecord(
                at=datetime.now(timezone.utc), task_class="summarize", model="claude-opus-5",
                contributing_scopes=frozenset({PERSONAL}), input_tokens=1, output_tokens=1,
                estimated_cost_usd=0.0,
            )
        )
        handed.append_event_log(entry("handed"), scope=alpha)
        handed.write_artifact(b"# dash\n", scope=alpha, artifact="daily_dashboard.md")
        handed.write_capture("said\n", scope=alpha, name="note-manual.txt")

    errors: list[BaseException] = []
    worker = threading.Thread(target=_capture, args=(calls, errors), name="a-worker")
    worker.start()
    worker.join(60)
    assert errors == [], errors
    assert spawned, "git was asked"
    assert rig.writer.name not in spawned, "a git question ran on the writer thread"


def _capture(work, errors: list[BaseException]) -> None:
    try:
        work()
    except BaseException as error:  # noqa: BLE001 — reported by the test
        errors.append(error)


def test_connector_check_s_credential_saves_are_noted_as_the_job_s_steps(rig):
    from pm_ai.core.connector_enrolment import enrol_connector

    enrol_connector(
        rig.daemon.storage, system="gitlab", instance="gitlab:beta",
        credential="token-one", probe=lambda system, credential: "accepted",
    )
    store = rig.path._store_for("gitlab:beta", "gitlab", job="job_noted0000000000")  # noqa: SLF001
    with store.exclusive():
        store.read()
        store.write("token-two")
    assert rig.path.notes.last("job_noted0000000000") == "save gitlab:beta's refreshed credential"
