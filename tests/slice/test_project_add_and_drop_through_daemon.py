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
import shlex
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


# ── Story 11e: a drop typed at the prompt is sent as typed; nobody asks ──────


class _Recording:
    """A prompt's client that records what it would send, and answers 0."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, line: str):
        from pm_ai.surfaces.cli.loopback import Answer

        self.sent.append(line)
        return Answer("", EXIT_OK)

    def follow(self, job_id, on_state=None):  # pragma: no cover — never acknowledged
        raise AssertionError("not acknowledged")

    def reachable(self):  # pragma: no cover — the prompt no longer asks first
        raise AssertionError("a drop asks nothing, so the prompt checks nothing first")


def _nobody_asks(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail(f"asked {prompt!r}"))


def _at_the_prompt(words: list[str], client: _Recording) -> None:
    from pm_ai.surfaces.cli import repl

    repl._through_daemon(words, client, None, " ".join(words))  # noqa: SLF001


FIXTURE_DOCX = (
    Path(__file__).parents[1] / "core" / "fixtures" / "transcripts" / "teams_export_recorded.docx"
)


def test_a_tilde_drop_at_the_prompt_is_sent_as_typed_and_the_daemon_reads_the_home_file(
    rig, tmp_path, monkeypatch
):
    home = tmp_path / "person"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (home / "x.vtt").write_text(SAID)
    _nobody_asks(monkeypatch)
    client = _Recording()
    words = ["transcript", "drop", "~/x.vtt", "--scope", "personal"]

    _at_the_prompt(words, client)

    (line,) = client.sent
    assert shlex.split(line) == words
    result = _job_result(rig, line)
    assert result["exit"] == EXIT_OK, result
    output = str(result["output"])
    assert "a new manual-only record" in output
    assert "Title 'x', from the file name" in output and "from the last cue" in output
    captures = list((rig.root / ".manager-ai" / "transcripts").glob("*-manual.vtt"))
    assert len(captures) == 1 and captures[0].read_text() == SAID
    assert str(captures[0]) in output


def test_a_relative_drop_at_the_prompt_is_sent_with_its_path_made_absolute(
    tmp_path, monkeypatch
):
    _nobody_asks(monkeypatch)
    monkeypatch.chdir(tmp_path)
    client = _Recording()

    _at_the_prompt(["transcript", "drop", "absent.vtt", "--scope", "personal"], client)

    (line,) = client.sent
    assert shlex.split(line) == [
        "transcript", "drop", str(Path.cwd() / "absent.vtt"), "--scope", "personal"
    ]


def test_the_recorded_docx_at_the_prompt_asks_nothing_and_the_daemon_names_each_source(
    rig, tmp_path, monkeypatch
):
    """Matrix, "At the prompt": the daemon decides every field and says where from."""
    dropped = tmp_path / "export.docx"
    dropped.write_bytes(FIXTURE_DOCX.read_bytes())
    _nobody_asks(monkeypatch)
    client = _Recording()
    words = ["transcript", "drop", str(dropped), "--scope", "personal"]

    _at_the_prompt(words, client)

    (line,) = client.sent
    assert shlex.split(line) == words
    result = _job_result(rig, line)
    assert result["exit"] == EXIT_OK, result
    output = str(result["output"])
    assert "a new manual-only record" in output
    assert "Title 'Example Weekly Sync-up', from the file header" in output
    assert "30 minutes, from the file header" in output
    # The rig's `display_timezone` is UTC, so the header's 09:30 is read in it.
    assert "start 2026-09-14T09:30:00+00:00 (2026-09-14 09:30 in UTC), estimated from the file header" in output
    assert "Participants Alex Morgan (not linked), Sam Rivera (not linked)." in output
    captures = list((rig.root / ".manager-ai" / "transcripts").glob("*-manual.txt"))
    assert len(captures) == 1 and str(captures[0]) in output


def test_a_dry_run_at_the_prompt_is_sent_as_typed_and_the_daemon_prints_the_object(
    rig, tmp_path, monkeypatch
):
    dropped = tmp_path / "export.docx"
    dropped.write_bytes(FIXTURE_DOCX.read_bytes())
    _nobody_asks(monkeypatch)
    client = _Recording()
    words = ["transcript", "drop", str(dropped), "--scope", "personal", "--dry-run", "--json"]
    meetings = rig.root / ".manager-ai" / "meetings"
    before = sorted(meetings.iterdir()) if meetings.exists() else []

    _at_the_prompt(words, client)

    (line,) = client.sent
    assert set(shlex.split(line)) == set(words)
    result = _job_result(rig, line)
    assert result["exit"] == EXIT_OK, result
    found = json.loads(str(result["output"]))
    assert found["title"] == {
        "value": "Example Weekly Sync-up", "source": "file header", "estimated": False
    }
    assert found["duration_minutes"] == {"value": 30, "source": "file header", "estimated": False}
    assert found["start"] == {
        "value": "2026-09-14T09:30:00+00:00", "source": "file header", "estimated": True
    }
    assert found["participants"] == [
        {"name": "Alex Morgan", "handle": None}, {"name": "Sam Rivera", "handle": None}
    ]
    assert found["outcome"] == {"kind": "new", "candidates": []}
    assert (sorted(meetings.iterdir()) if meetings.exists() else []) == before


def test_the_loopback_client_says_no_daemon_without_a_token_file(tmp_path):
    from pm_ai.surfaces.cli.loopback import LoopbackClient, NoDaemon

    client = LoopbackClient(token_path=lambda: tmp_path / "absent", port=lambda: 1)
    assert isinstance(client.reachable(), NoDaemon)


def test_the_drops_bound_is_the_git_question_and_one_model_call():
    """Story 11e: the header reading runs inside the drop, so its deadline is in the bound."""
    from pm_ai.app.entry import DAEMON_BOUNDS
    from pm_ai.app.pipelines import SUMMARY_CALL_DEADLINE_SECONDS, TRANSCRIPT_DROP_BOUND_SECONDS
    from pm_ai.app.wiring import GIT_QUESTION_SECONDS

    assert TRANSCRIPT_DROP_BOUND_SECONDS == GIT_QUESTION_SECONDS + SUMMARY_CALL_DEADLINE_SECONDS
    assert DAEMON_BOUNDS[("transcript", "drop")](None) == TRANSCRIPT_DROP_BOUND_SECONDS  # type: ignore[arg-type]


class _HeaderModel:
    def __init__(self, answer: str = "", *, delay: float = 0.0) -> None:
        self.answer = answer
        self.delay = delay
        self.threads: list[str] = []

    def complete(self, **call):
        from pm_ai.domain.model_calls import ModelAnswer

        self.threads.append(threading.current_thread().name)
        time.sleep(self.delay)
        return ModelAnswer(text=self.answer, model="fake", local=True, prompt_tokens=1, output_tokens=1)


def _with_pool(model):
    from types import SimpleNamespace

    from pm_ai.app.concurrency import ModelPool

    pool = ModelPool()
    pool.start()
    return SimpleNamespace(model=model, model_pool=pool), pool


def test_the_header_call_runs_on_the_model_pool():
    answer = json.dumps({"title": "Example Weekly Sync-up", "start": None, "length_minutes": 30})
    model = _HeaderModel(answer)
    daemon, pool = _with_pool(model)
    try:
        reading = pipelines._header_by_model(daemon, PERSONAL, ["Example Weekly Sync-up · 29m"])  # noqa: SLF001
    finally:
        pool.shutdown()
    assert reading is not None and reading.title == "Example Weekly Sync-up"
    assert reading.length == 30
    assert model.threads == ["pm-ai-model-1"]


def test_a_header_call_past_its_deadline_or_a_stopped_pool_reads_nothing(monkeypatch):
    model = _HeaderModel("{}", delay=0.5)
    daemon, pool = _with_pool(model)
    monkeypatch.setattr(pipelines, "SUMMARY_CALL_DEADLINE_SECONDS", 0.05)
    try:
        assert pipelines._header_by_model(daemon, PERSONAL, ["x · 29m"]) is None  # noqa: SLF001
    finally:
        pool.shutdown()
    assert pipelines._header_by_model(daemon, PERSONAL, ["x · 29m"]) is None  # noqa: SLF001
