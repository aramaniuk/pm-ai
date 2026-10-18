"""Story 4q end to end: the prompt as a client of a real daemon, in a throwaway home.

The daemon is the real console script, `pm-ai daemon run`, on a free port named
in the throwaway home's `config.toml`, with `keyring`'s null backend so nothing
reaches the developer's login keychain. The prompt is `repl.run` in this
process, with the composition root's own client (`entry._loopback`) and
`entry.main` as its local runner — `HOME` redirected, and `entry._compose`
forbidden, so a line that composed a daemon in the prompt's process would fail
the test rather than pass it.

The daemon's half — the line's scope resolved per request — is driven in
process against 4r's request path, in the last section.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from test_daemon_requests_slice import (
    Home,
    _enrol_quiet_gitlab,
    _free_port,
    _stop,
    _wait_listening,
)
from test_line_answered_or_acknowledged import WATCHED, rig  # noqa: F401 — the fixture
from pm_ai.app import entry
from pm_ai.core.project_registry import ProjectEntry, render_registry
from pm_ai.surfaces.cli import repl
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, RESTART_SENTENCE
from pm_ai.surfaces.cli.loopback import Answer, LoopbackClient


@pytest.fixture
def home(tmp_path: Path) -> Home:
    root = tmp_path / "home"
    (root / ".pm-ai").mkdir(parents=True)
    port = _free_port()
    (root / ".pm-ai" / "config.toml").write_text(
        f'daemon_port = {port}\ndisplay_timezone = "UTC"\n'
    )
    return Home(root, port)


@pytest.fixture
def daemon_process(home: Home) -> Iterator[subprocess.Popen[str]]:
    process = home.start()
    try:
        _wait_listening(process)
        yield process
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


class _Recorded:
    """The composition root's client, noting when each job's result has arrived."""

    def __init__(self, client: LoopbackClient) -> None:
        self._client = client
        self.answers: dict[str, Answer] = {}
        self.arrived = threading.Event()

    def send(self, line: str):
        return self._client.send(line)

    def follow(self, job_id: str):
        try:
            followed = self._client.follow(job_id)
            if isinstance(followed, Answer):
                self.answers[job_id] = followed
            return followed
        finally:
            self.arrived.set()


WAIT = object()
"""A step: wait for the acknowledged job's result to arrive, then press Enter."""


def _prompt(home: Home, monkeypatch: pytest.MonkeyPatch, *lines: object) -> _Recorded:
    """Run the prompt in this process over `lines`, as the person's own terminal would."""
    monkeypatch.setenv("HOME", str(home.root))
    monkeypatch.chdir(home.root)

    def composed(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the prompt's process composed a daemon")

    monkeypatch.setattr(entry, "_compose", composed)
    client = _Recorded(entry._loopback())
    steps = list(lines)

    def read(prompt: str) -> str:
        print(prompt, end="", flush=True)
        if not steps:
            raise EOFError
        step = steps.pop(0)
        if step is WAIT:
            assert client.arrived.wait(60), "the job's result never arrived"
            # The waiting thread queues the result just after `follow` returns.
            for _ in range(500):
                if not any(t.name.startswith("pm-ai-follow-") for t in threading.enumerate()):
                    break
                threading.Event().wait(0.01)
            return ""
        assert isinstance(step, str)
        return step

    assert repl.run(entry.main, read=read, client=client) == EXIT_OK
    return client


def _prompt_waiting(home: Home, monkeypatch: pytest.MonkeyPatch, line: str) -> tuple[_Recorded, str]:
    """`line`, then wait for its job's result, then `exit`; the client and the job id."""
    recorded = _prompt(home, monkeypatch, line, WAIT, "exit")
    (job,) = recorded.answers
    return recorded, job


def _requests(home: Home) -> list[tuple[str, str, str]]:
    with sqlite3.connect(home.store) as db:
        return list(db.execute("SELECT id, kind, state FROM requests ORDER BY created_at"))


# ── Through the real daemon ──────────────────────────────────────────────────


def test_dashboard_is_acknowledged_and_its_render_arrives_with_the_id(
    home, daemon_process, monkeypatch, capsys
):
    recorded, job = _prompt_waiting(home, monkeypatch, "dashboard")
    out = capsys.readouterr().out
    assert f"{job}: acknowledged" in out
    assert _requests(home) == [(job, "dashboard", "done")], "the requests row exists"
    written = home.root / ".manager-ai" / "memory" / "daily_dashboard.md"
    assert written.is_file()
    assert f"{job}: the personal dashboard is written: {written}\n{job}: exit 0\n" in out
    # Printed at a later prompt than the acknowledgement, never with it.
    assert out.index(f"{job}: acknowledged") < out.index("pm-ai> ", out.index(f"{job}: acknowledged")) < out.index(f"{job}: exit 0")
    assert _stop(daemon_process)[0] == 0


def test_connector_check_is_acknowledged_and_that_instances_row_follows(
    home, monkeypatch, capsys
):
    # One connector enrolled, pointed at a port nothing listens on: the probe
    # is refused at once, so its row says so.
    _enrol_quiet_gitlab(home, _free_port())
    process = home.start()
    try:
        _wait_listening(process)
        recorded, job = _prompt_waiting(home, monkeypatch, "connector check")
        out = capsys.readouterr().out
        assert f"{job}: acknowledged" in out
        lines = out.replace("pm-ai> ", "").splitlines()
        rows = [line for line in lines if line.startswith(f"{job}: ")]
        assert any("gitlab:alpha" in line for line in rows), out
        assert rows[-1].startswith(f"{job}: exit "), rows
        assert _requests(home)[0][1] == "connector check"
        assert _stop(process)[0] == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_config_show_answers_inline_as_the_terminal_does(home, daemon_process, monkeypatch, capsys):
    _prompt(home, monkeypatch, "config show", "exit")
    out = capsys.readouterr().out
    terminal = home.run("config", "show")
    assert terminal.returncode == 0
    assert f"pm-ai> {terminal.stdout}exit 0\n" in out
    assert _requests(home) == [], "an inline line writes no row"


def test_settings_edited_while_the_daemon_runs_show_at_the_next_line(
    home, daemon_process, monkeypatch, capsys
):
    config = home.application / "config.toml"
    config.write_text(config.read_text().replace('"UTC"', '"Europe/Vilnius"'))
    _prompt(home, monkeypatch, "config show", "exit")
    assert "Europe/Vilnius" in capsys.readouterr().out


def test_a_project_named_but_not_watched_is_refused_by_name(home, daemon_process, monkeypatch, capsys):
    _prompt(home, monkeypatch, "dashboard --scope project:gamma", "exit")
    out = capsys.readouterr().out
    assert "project:gamma is not a project this daemon watches" in out
    assert "exit 3" in out.replace("pm-ai> ", "").splitlines()
    assert _requests(home) == []


def test_two_commands_at_once_each_hold_only_their_own_output(home, daemon_process, monkeypatch):
    """Both write through their own request's buffers: `config show` to its
    output, `daemon run`'s refusal to its error stream (story 4r's streams)."""
    monkeypatch.setenv("HOME", str(home.root))
    client = entry._loopback()
    lines = ("config show", "daemon run")
    answers: dict[str, list[object]] = {line: [] for line in lines}
    start = threading.Barrier(len(lines) * 4)

    def ask(line: str) -> None:
        start.wait()
        answers[line].append(client.send(line))

    threads = [threading.Thread(target=ask, args=(line,)) for line in lines for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    terminal = home.run("config", "show").stdout
    assert answers["config show"] == [Answer(terminal, EXIT_OK)] * 4
    refusals = answers["daemon run"]
    assert len(refusals) == 4 and len(set(refusals)) == 1
    (refusal,) = set(refusals)
    assert isinstance(refusal, Answer) and refusal.code == EXIT_REFUSAL
    assert "already running" in refusal.output
    assert "daemon_port" not in refusal.output
    assert refusal.output.count("\n") == 1


def test_daemon_run_at_the_prompt_says_a_daemon_is_already_running(
    home, daemon_process, monkeypatch, capsys
):
    _prompt(home, monkeypatch, "daemon run", "exit")
    out = capsys.readouterr().out
    assert "pm-ai: a pm-ai daemon is already running" in out
    assert "exit 3" in out.replace("pm-ai> ", "").splitlines()


def test_a_relative_path_at_the_prompt_answers_as_the_terminal_does(
    home, daemon_process, monkeypatch, capsys
):
    _, job = _prompt_waiting(home, monkeypatch, "transcript drop ./absent.vtt --scope personal")
    out = capsys.readouterr().out
    assert "relative path" not in out
    terminal = home.run("transcript", "drop", "./absent.vtt", "--scope", "personal")
    assert f"{job}: exit {terminal.returncode}" in out.replace("pm-ai> ", "").splitlines()
    # The same refusal and code; the prompt's names the path it made absolute,
    # where the terminal repeats the path as typed.
    assert "does not exist. Nothing was written." in terminal.stderr
    assert f"{job}: pm-ai: {home.root / 'absent.vtt'} does not exist. Nothing was written." in out


def test_a_typed_line_runs_through_the_daemon_and_is_recalled(home, daemon_process):
    """The real console script on a pseudo-terminal, against the real daemon."""
    from test_repl_startup import _Terminal

    session = _Terminal(home.root)
    try:
        assert session.read_until(1), session.output
        session.type(b"config show\n")
        # Story 4u: `prompt_toolkit` draws the prompt again when a line is
        # accepted, so the wait is on the status line, not a count of prompts.
        assert session.read_until_text(b"exit 0\r\n", 1), session.output
        session.type(b"\x1b[A\n")  # up arrow, then Enter: recalled through prompt_toolkit
        assert session.read_until_text(b"exit 0\r\n", 2), session.output
        session.type(b"exit\n")
        assert session.finish() == 0
    finally:
        session.close()
    text = session.output.decode(errors="replace").replace("\r\n", "\n")
    assert text.count("daemon_port") == 2, text
    assert text.splitlines().count("exit 0") == 2, text
    assert _requests(home) == []


def test_with_no_daemon_doctor_runs_here_and_dashboard_names_daemon_run(
    home, monkeypatch, capsys
):
    """The acceptance row: no daemon running, `doctor` then `dashboard`."""
    monkeypatch.setenv("HOME", str(home.root))
    monkeypatch.chdir(home.root)
    steps = ["doctor", "dashboard", "dashboard --bogus", "exit"]

    def read(prompt: str) -> str:
        print(prompt, end="", flush=True)
        return steps.pop(0)

    assert repl.run(entry.main, read=read, client=entry._loopback()) == EXIT_OK
    captured = capsys.readouterr()
    assert "daemon" in captured.out and "not running" in captured.out, "doctor's report"
    assert "`pm-ai daemon run`" in captured.err
    statuses = [line for line in captured.out.replace("pm-ai> ", "").splitlines() if line.startswith("exit ")]
    # `dashboard` names the daemon; `dashboard --bogus` is a usage error, answered here.
    assert statuses[-2:] == ["exit 1", "exit 2"]
    assert "has no `--bogus` option" in captured.err


# ── The daemon's half: the line's scope, per request ─────────────────────────


def test_a_project_the_daemon_does_not_watch_is_refused_naming_the_watched(rig):
    answered = asyncio.run(rig.path.prompt("dashboard --scope project:omega"))
    assert answered.status == 200
    assert answered.body["exit"] == EXIT_REFUSAL
    output = answered.body["output"]
    assert "project:omega is not a project this daemon watches" in output
    assert ", ".join(sorted(WATCHED)) in output
    assert rig.rows() == []


def test_a_project_enrolled_since_start_is_refused_with_the_restart_sentence(rig):
    entries = {pid: ProjectEntry(path=rig.root / pid) for pid in (*WATCHED, "zeta")}
    (rig.root / ".pm-ai" / "projects.toml").write_bytes(render_registry(entries))
    answered = asyncio.run(rig.path.prompt("transcript drop /tmp/x.vtt --scope project:zeta"))
    assert answered.body["exit"] == EXIT_REFUSAL
    assert RESTART_SENTENCE in answered.body["output"]
    assert "zeta" in answered.body["output"]
    assert rig.rows() == []


def test_a_watched_project_is_planned_as_before(rig):
    answered = asyncio.run(rig.path.prompt("dashboard --scope project:alpha"))
    assert answered.status == 202
    row = rig.wait(answered.body["job_id"], "done", "failed")
    assert row.state == "done", row
    result = json.loads(row.result)
    assert result["exit"] == EXIT_OK
    assert result["output"].startswith("the project:alpha dashboard is written: ")


def test_an_unreadable_registry_is_named_not_read_as_never_enrolled(rig):
    (rig.root / ".pm-ai" / "projects.toml").write_bytes(b"this is = = not toml")
    answered = asyncio.run(rig.path.prompt("dashboard --scope project:omega"))
    assert answered.body["exit"] == EXIT_REFUSAL
    output = answered.body["output"]
    assert "registry could not be read" in output
    assert "enrol omega first" not in output
    assert rig.rows() == []
