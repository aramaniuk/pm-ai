"""Stories 4f and 4q — one test per row of the terminal prompt's I/O matrices.

The loop is driven with a fake runner and a scripted reader, so each row is a
unit test. The rows that need the real `entry.main` are in
`tests/surfaces/test_cli_dispatch.py`; the rows that need a real terminal (the
startup time, recall, the server imports) are in `tests/slice/test_repl_startup.py`.
"""

from __future__ import annotations

import ast
import shlex
import sys
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path

import pytest

import pm_ai
from pm_ai.surfaces.cli import dispatch, loopback, repl
from pm_ai.surfaces.cli.dispatch import (
    EXIT_OK,
    EXIT_REFUSAL,
    EXIT_UNEXPECTED,
    EXIT_USAGE,
    RESTART_SENTENCE,
    TABLE,
    TERMINAL_BOUND,
)


class _Reader:
    """`input` replaced by a script; a step may be a line or an exception."""

    def __init__(self, steps: Iterable[str | BaseException]) -> None:
        self._steps = list(steps)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._steps:
            raise EOFError
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


class _Runner:
    """The command line, faked: records the words and returns a set code."""

    def __init__(self, code: int = EXIT_OK, *, raises: BaseException | None = None):
        self.code = code
        self.raises = raises
        self.calls: list[list[str]] = []

    def __call__(self, words: Sequence[str]) -> int:
        self.calls.append(list(words))
        if self.raises is not None:
            raise self.raises
        print(f"ran {' '.join(words)}")
        return self.code


def _session(*steps: str | BaseException, runner: _Runner | None = None):
    runner = runner or _Runner()
    reader = _Reader(steps)
    code = repl.run(runner, read=reader)
    return code, runner, reader


# ── Opening and ending ───────────────────────────────────────────────────────


def test_the_prompt_text_is_pm_ai():
    code, _, reader = _session("exit")
    assert code == EXIT_OK
    assert reader.prompts == ["pm-ai> "]


@pytest.mark.parametrize("ending", ["exit", "quit", "  exit  "])
def test_exit_and_quit_end_the_session_with_0(ending):
    code, runner, reader = _session(ending, "doctor")
    assert code == EXIT_OK
    assert runner.calls == []
    assert len(reader.prompts) == 1


def test_ctrl_d_ends_the_session_with_0():
    code, runner, _ = _session(EOFError())
    assert code == EXIT_OK
    assert runner.calls == []


def test_exit_with_words_is_a_usage_error_and_the_session_continues(capsys):
    code, _, reader = _session("exit now", "exit")
    assert code == EXIT_OK
    captured = capsys.readouterr()
    assert "takes no arguments" in captured.err
    assert "exit 2" in captured.out.splitlines()
    assert len(reader.prompts) == 2


# ── Fixed commands ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("code", [EXIT_OK, 4])
def test_a_fixed_command_runs_and_shows_its_exit_code(code, capsys):
    runner = _Runner(code)
    assert _session("doctor", "exit", runner=runner)[0] == EXIT_OK
    assert runner.calls == [["doctor"]]
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["ran doctor", f"exit {code}"]


def test_options_are_passed_as_spelled():
    _, runner, _ = _session("dashboard --scope personal", "exit")
    assert runner.calls == [["dashboard", "--scope", "personal"]]


def test_quoted_arguments_split_as_the_shell_splits_them():
    _, runner, _ = _session('project add "/path/with space" alpha', "exit")
    assert runner.calls == [["project", "add", "/path/with space", "alpha"]]


def test_an_unbalanced_quote_runs_nothing_and_is_exit_2(capsys):
    _, runner, reader = _session('project add "/path alpha', "exit")
    assert runner.calls == []
    captured = capsys.readouterr()
    assert "unclosed" in captured.err
    assert captured.err.strip().count("\n") == 0
    assert captured.out.splitlines() == ["exit 2"]
    assert len(reader.prompts) == 2


def test_a_refusal_shows_its_code_and_the_session_continues(capsys):
    runner = _Runner(EXIT_REFUSAL)
    _, _, reader = _session("goal set", "exit", runner=runner)
    assert "exit 3" in capsys.readouterr().out.splitlines()
    assert len(reader.prompts) == 2


def test_a_usage_error_from_the_command_line_shows_exit_2(capsys):
    runner = _Runner(EXIT_USAGE)
    _session("dashboard --bogus", "exit", runner=runner)
    assert runner.calls == [["dashboard", "--bogus"]]
    assert "exit 2" in capsys.readouterr().out.splitlines()


@pytest.mark.parametrize("word", ["help", "--help", "-h"])
def test_help_runs_through_the_command_line(word, capsys):
    _, runner, _ = _session(word, "exit")
    assert runner.calls == [[word]]
    assert "exit 0" in capsys.readouterr().out.splitlines()


def test_ctrl_c_inside_a_command_is_its_own_interruption(capsys):
    """The real runner turns Ctrl-C into exit 3; one that escapes it still does."""
    runner = _Runner(raises=KeyboardInterrupt())
    code, _, reader = _session("setup", "exit", runner=runner)
    assert code == EXIT_OK
    assert "exit 3" in capsys.readouterr().out.splitlines()
    assert len(reader.prompts) == 2


# ── Open lines, blank lines, Ctrl-C at the prompt ────────────────────────────


@pytest.mark.parametrize(
    ("line", "word"),
    [("what did I promise Laura", "what"), ("dashbord", "dashbord")],
)
def test_an_open_line_is_answered_in_one_sentence_and_run_nowhere(line, word, capsys):
    _, runner, reader = _session(line, "exit")
    assert runner.calls == []
    captured = capsys.readouterr()
    assert captured.err == ""
    (sentence,) = captured.out.splitlines()
    assert sentence.startswith(f"`{word}` names no command")
    assert "names no command" in sentence
    assert "`help`" in sentence
    assert "no model" in sentence
    assert "not sent anywhere" in sentence
    assert "exit" not in sentence.split()
    assert len(reader.prompts) == 2


def test_an_empty_first_word_is_answered_with_what_was_typed(capsys):
    """`'' doctor` names nothing; quoting the empty word would say nothing."""
    _, runner, _ = _session("'' doctor", "exit")
    assert runner.calls == []
    (sentence,) = capsys.readouterr().out.splitlines()
    assert sentence.startswith("`'' doctor` names no command")


@pytest.mark.parametrize("line", ["--bogus", "--version", "-x doctor"])
def test_a_first_word_starting_with_a_dash_goes_to_the_command_line(line, capsys):
    """The command line's own usage and `exit 2`, never the open-line sentence."""
    runner = _Runner(EXIT_USAGE)
    _session(line, "exit", runner=runner)
    assert runner.calls == [line.split()]
    printed = capsys.readouterr().out
    assert "names no command" not in printed
    assert "exit 2" in printed.splitlines()


def test_exit_and_quit_name_no_command_in_the_table():
    """A command called `exit` or `quit` would be silently shadowed by the ending."""
    assert not repl.ENDINGS & set(TABLE)


def test_ctrl_c_while_splitting_shows_a_fresh_prompt(monkeypatch):
    def interrupted(line):
        raise KeyboardInterrupt

    monkeypatch.setattr(repl.shlex, "split", interrupted)
    code, runner, reader = _session("doctor", EOFError())
    assert code == EXIT_OK
    assert runner.calls == []
    assert len(reader.prompts) == 2


@pytest.mark.parametrize("target", ["_open_line", "_status"])
def test_ctrl_c_while_printing_shows_a_fresh_prompt(target, monkeypatch):
    """The open-line sentence and the status line are both outside the runner."""

    def interrupted(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(repl, target, interrupted)
    line = "what now" if target == "_open_line" else "doctor"
    code, _, reader = _session(line, "doctor", EOFError())
    assert code == EXIT_OK
    assert len(reader.prompts) == 3


@pytest.mark.parametrize(
    "fault",
    [UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), OSError(5, "EIO")],
)
def test_a_line_that_cannot_be_read_is_exit_2_and_the_session_continues(fault, capsys):
    code, runner, reader = _session(fault, "doctor", "exit")
    assert code == EXIT_OK
    assert runner.calls == [["doctor"]]
    captured = capsys.readouterr()
    assert len(captured.err.strip().splitlines()) == 1
    assert "could not be read" in captured.err
    assert captured.out.splitlines()[0] == "exit 2"
    assert len(reader.prompts) == 3


def test_an_open_line_reaches_no_file(tmp_path, monkeypatch):
    """Nothing reads an open line, so the sentence saying it went nowhere stays true."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".pm-ai").mkdir()
    before = sorted(tmp_path.rglob("*"))
    _session("what did I promise Laura", "exit")
    assert sorted(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_line_shows_a_fresh_prompt(blank, capsys):
    _, runner, reader = _session(blank, "exit")
    assert runner.calls == []
    assert capsys.readouterr().out == ""
    assert len(reader.prompts) == 2


def test_ctrl_c_while_typing_clears_the_line_and_never_ends_the_session():
    code, runner, reader = _session(KeyboardInterrupt(), "doctor", "exit")
    assert code == EXIT_OK
    assert runner.calls == [["doctor"]]
    assert len(reader.prompts) == 3


# ── Line editing ─────────────────────────────────────────────────────────────


def _without_prompt_toolkit(monkeypatch) -> None:
    """The runtime extra's line editor made unimportable (story 4u).

    `sys.modules[name] = None` makes the import raise `ImportError`; the
    prompt's own module is dropped too, so a copy an earlier test imported
    cannot answer for it.
    """
    import pm_ai.surfaces.cli as cli

    monkeypatch.setitem(sys.modules, "prompt_toolkit", None)
    monkeypatch.delitem(sys.modules, "pm_ai.surfaces.cli.editor", raising=False)
    monkeypatch.delattr(cli, "editor", raising=False)


def test_lines_are_still_read_when_readline_cannot_be_imported(monkeypatch, capsys):
    """Neither line editor imports: `input` reads every line, nothing else changes."""
    _without_prompt_toolkit(monkeypatch)
    monkeypatch.setitem(sys.modules, "readline", None)
    loaded: list[bool] = []
    original = repl._line_editing

    def recorded() -> bool:
        loaded.append(original())
        return loaded[-1]

    monkeypatch.setattr(repl, "_line_editing", recorded)
    runner = _Runner()
    reader = _Reader(["doctor", "exit"])
    monkeypatch.setattr(repl, "_input", reader)
    assert repl.run(runner, line_editing=True) == EXIT_OK
    assert loaded == [False]
    assert runner.calls == [["doctor"]]
    assert capsys.readouterr().err.count("uv sync --extra runtime") == 1


def test_line_editing_is_off_unless_asked_for(monkeypatch):
    called: list[bool] = []
    monkeypatch.setattr(repl, "_line_editing", lambda: called.append(True) or True)
    _session("exit")
    assert called == []


def test_no_history_file_is_written(tmp_path, monkeypatch):
    """`4q`'s line-editing path, with readline's history writers recorded."""
    import readline
    _without_prompt_toolkit(monkeypatch)
    home, working = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    working.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(working)
    written: list[tuple[str, tuple[object, ...]]] = []
    for name in ("write_history_file", "append_history_file"):
        # `raising=False`: libedit's build has no `append_history_file`, and
        # a recorder there still catches a later build that adds one.
        monkeypatch.setattr(
            readline,
            name,
            lambda *a, _name=name: written.append((_name, a)),
            raising=False,
        )
    loaded: list[bool] = []
    original = repl._line_editing
    monkeypatch.setattr(repl, "_line_editing", lambda: loaded.append(original()) or True)

    monkeypatch.setattr(repl, "_input", _Reader(["doctor", "what now", "exit"]))
    assert repl.run(_Runner(), line_editing=True) == EXIT_OK
    assert loaded == [True]
    assert written == []
    assert list(home.iterdir()) == []
    assert list(working.iterdir()) == []


# ══ Story 4q — the prompt through the daemon ═════════════════════════════════
#
# One test per row of 4q's I/O matrix, with a stand-in daemon client. The rows
# that need HTTP are in `tests/surfaces/test_cli_loopback.py`; the rows that need
# a real daemon are in `tests/slice/test_repl_through_daemon.py`.


class _Daemon:
    """The daemon client, faked: records each line and answers from a script.

    `answers` maps a line to what `send` returns (or raises). `results` maps a
    job id to what `follow` returns; `hold` keeps a job's stream open until it
    is released, so a test decides when its result arrives.
    """

    def __init__(self, answers=None, results=None, *, hold=()):
        self.answers = dict(answers or {})
        self.results = dict(results or {})
        self.sent: list[str] = []
        self.followed: list[str] = []
        self.release = {job: threading.Event() for job in hold}
        self.arrived = threading.Event()

    def send(self, line):
        self.sent.append(line)
        answer = self.answers.get(line, loopback.Answer(f"daemon ran {line}\n", EXIT_OK))
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def follow(self, job_id):
        self.followed.append(job_id)
        gate = self.release.get(job_id)
        if gate is not None:
            gate.wait(10)
        try:
            return self.results.get(job_id, loopback.StreamLost(job_id, "no script"))
        finally:
            self.arrived.set()


class _Screen:
    """`input` replaced by a script that also shows the prompt on stdout, as `input` does.

    A step may be a line, an exception, or a callable returning a line — the
    callable runs while the prompt is showing, before the line is "typed".
    """

    def __init__(self, steps):
        self._steps = list(steps)
        self.prompts = 0

    def __call__(self, prompt):
        sys.stdout.write(prompt)
        sys.stdout.flush()
        self.prompts += 1
        if not self._steps:
            raise EOFError
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step()
        return step


def _through(daemon, *steps, runner=None):
    runner = runner or _Runner()
    screen = _Screen(steps)
    code = repl.run(runner, read=screen, client=daemon)
    return code, runner, screen


def _join_followers() -> None:
    for thread in threading.enumerate():
        if thread.name.startswith("pm-ai-follow-"):
            thread.join(10)


def _arrived(daemon, then=""):
    """A step: release every held stream, wait until its result is queued, type `then`."""

    def step():
        for gate in daemon.release.values():
            gate.set()
        assert daemon.arrived.wait(10), "the result never arrived"
        _join_followers()
        return then

    return step


def _lines(out: str) -> list[str]:
    return out.replace("pm-ai> ", "").splitlines()


ACK = "acknowledged; the daemon runs it, and its result is shown here, prefixed by this id, at a prompt once it is ready."


def test_inline_via_the_daemon_shows_its_output_and_exit_code(capsys):
    daemon = _Daemon({"config show": loopback.Answer("daemon_port = 8747  (default)\n", EXIT_OK)})
    code, runner, _ = _through(daemon, "config show", "exit")
    assert code == EXIT_OK
    assert daemon.sent == ["config show"]
    assert runner.calls == []
    assert capsys.readouterr().out == "pm-ai> daemon_port = 8747  (default)\nexit 0\npm-ai> "


def test_a_job_is_acknowledged_and_its_result_follows_with_its_id_at_the_next_prompt(capsys):
    job = "job_0123456789abcdef"
    daemon = _Daemon(
        {"dashboard --scope personal": loopback.Acknowledged(job)},
        {job: loopback.Answer("the personal dashboard is written: /x.md\n", EXIT_OK)},
        hold=(job,),
    )
    code, runner, _ = _through(daemon, "dashboard --scope personal", _arrived(daemon), "exit")
    assert code == EXIT_OK
    assert runner.calls == []
    assert daemon.followed == [job]
    # The prompt returned at once; the result was printed only before a later one.
    assert capsys.readouterr().out == (
        f"pm-ai> {job}: {ACK}\n"
        "pm-ai> "
        f"{job}: the personal dashboard is written: /x.md\n"
        f"{job}: exit 0\n"
        "pm-ai> "
    )


def test_a_render_failure_arrives_as_its_sentence_and_exit_3_with_the_id(capsys):
    job = "job_aaaaaaaaaaaaaaaa"
    daemon = _Daemon(
        {"dashboard": loopback.Acknowledged(job)},
        {job: loopback.Answer("pm-ai: no display_timezone is set.\n", EXIT_REFUSAL)},
        hold=(job,),
    )
    _through(daemon, "dashboard", _arrived(daemon), "exit")
    assert f"{job}: pm-ai: no display_timezone is set.\n{job}: exit 3\n" in capsys.readouterr().out


@pytest.mark.parametrize("line", ["dashboard --bogus", "key", "connector", "connector bogus", "dashboard --scope alpha"])
@pytest.mark.parametrize("with_daemon", [True, False])
def test_a_usage_error_is_answered_here_with_or_without_a_daemon(line, with_daemon):
    """Parsed here first: a usage error needs no daemon and is the command line's own."""
    daemon = _Daemon() if with_daemon else _Daemon(
        {line: loopback.NoDaemon.refused(8747)}
    )
    _, runner, _ = _through(daemon, line, "exit")
    assert daemon.sent == []
    assert runner.calls == [line.split()]


def test_an_unwatched_project_is_the_daemons_refusal_and_exit_3(capsys):
    line = "dashboard --scope project:gamma"
    refusal = "pm-ai: project:gamma is not a project this daemon watches, so the line was not run; it watches alpha.\n"
    daemon = _Daemon({line: loopback.Answer(refusal, EXIT_REFUSAL)})
    _, _, screen = _through(daemon, line, "exit")
    out = capsys.readouterr().out
    assert refusal in out and "exit 3" in _lines(out)
    assert screen.prompts == 2


@pytest.mark.parametrize(
    "line",
    ["goal set", "setup", "connector add gitlab gitlab:alpha", "connector sign-in graph:work", "key enrol"],
)
def test_a_command_that_needs_this_terminal_runs_here_and_is_never_sent(line, capsys):
    daemon = _Daemon()
    _, runner, _ = _through(daemon, line, "exit")
    assert daemon.sent == []
    assert runner.calls == [line.split()]
    assert "exit 0" in _lines(capsys.readouterr().out)


@pytest.mark.parametrize("line", ["doctor", "project add /work/beta beta", "help", "--help", "--bogus"])
def test_a_command_needing_no_daemon_and_help_run_here(line):
    daemon = _Daemon()
    _, runner, _ = _through(daemon, line, "exit")
    assert daemon.sent == []
    assert runner.calls == [line.split()]


def test_the_routing_reads_the_command_table():
    """Local exactly when the table says no daemon, or the command needs this terminal."""
    for name, command in TABLE.items():
        leaves = command.leaves.items() if command.run is None else [(None, command)]
        for leaf_name, leaf in leaves:
            words = [name] if leaf_name is None else [name, leaf_name]
            words += [f"<{argument}>" for argument in leaf.takes]
            words += [part for option in leaf.requires for part in (f"--{option}", "personal")]
            terminal = tuple(words[:2]) in TERMINAL_BOUND or tuple(words[:1]) in TERMINAL_BOUND
            assert repl.runs_here(words) == (terminal or leaf.target is None), words


def test_a_command_added_later_goes_through_the_daemon_by_default(monkeypatch):
    added = dispatch.Command(
        "a new command", lambda context: 0, target=lambda options: dispatch.APPLICATION
    )
    table = {**TABLE, "brand-new": added}
    monkeypatch.setattr(repl, "TABLE", table)
    monkeypatch.setattr(dispatch, "TABLE", table)
    assert not repl.runs_here(["brand-new"])


def test_enrolment_while_the_daemon_runs_is_local_and_carries_the_restart_sentence(capsys):
    class _Enrol(_Runner):
        def __call__(self, words):
            self.calls.append(list(words))
            print(f"beta is enrolled.\n{RESTART_SENTENCE}")
            return EXIT_OK

    daemon = _Daemon()
    _, runner, _ = _through(daemon, "project add /work/beta beta", "key enrol", "exit", runner=_Enrol())
    assert daemon.sent == []
    assert runner.calls == [["project", "add", "/work/beta", "beta"], ["key", "enrol"]]
    assert capsys.readouterr().out.count(RESTART_SENTENCE) == 2


# ── Paths and words, as sent ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("typed", "sent"),
    [
        ("transcript drop ./x.vtt --scope personal", ["{cwd}/x.vtt", "--scope", "personal"]),
        ("transcript drop --scope personal x.vtt", ["{cwd}/x.vtt", "--scope", "personal"]),
        ("transcript drop ../up/x.vtt --scope personal --meeting m1", ["{cwd}/../up/x.vtt", "--scope", "personal", "--meeting", "m1"]),
    ],
)
def test_a_relative_path_is_made_absolute_against_this_directory(typed, sent, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    daemon = _Daemon()
    _through(daemon, typed, "exit")
    (line,) = daemon.sent
    expected = ["transcript", "drop", *(part.format(cwd=Path.cwd()) for part in sent)]
    assert shlex.split(line) == expected


@pytest.mark.parametrize("given", ["/abs/x.vtt", "~/x.vtt"])
def test_an_absolute_or_home_path_is_sent_as_typed(given):
    daemon = _Daemon()
    _through(daemon, f"transcript drop {given} --scope personal", "exit")
    assert shlex.split(daemon.sent[0]) == ["transcript", "drop", given, "--scope", "personal"]


def test_a_quoted_argument_with_a_space_round_trips(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    daemon = _Daemon()
    _through(daemon, 'transcript drop "/tmp/my file.vtt" --scope personal', "exit")
    assert shlex.split(daemon.sent[0]) == ["transcript", "drop", "/tmp/my file.vtt", "--scope", "personal"]


# ── A line that does not reach a command ─────────────────────────────────────


def _sentence_and_exit_1(capsys, needle):
    captured = capsys.readouterr()
    (sentence,) = captured.err.strip().splitlines()
    assert needle in sentence, sentence
    assert "exit 1" in _lines(captured.out)
    return sentence


def test_no_daemon_is_one_sentence_naming_pm_ai_daemon_run_and_exit_1(capsys):
    daemon = _Daemon({"config show": loopback.NoDaemon.refused(8747)})
    _, _, screen = _through(daemon, "config show", "exit")
    sentence = _sentence_and_exit_1(capsys, "`pm-ai daemon run`")
    assert "127.0.0.1:8747, the port in config.toml" in sentence
    assert screen.prompts == 2


def test_a_rejected_token_is_one_sentence_to_restart_the_daemon_and_exit_1(capsys):
    daemon = _Daemon({"config show": loopback.TokenRejected(Path("/h/.pm-ai/private/api_token"))})
    _through(daemon, "config show", "exit")
    sentence = _sentence_and_exit_1(capsys, "disagree")
    assert "restart the daemon" in sentence and "api_token" in sentence


def test_a_busy_daemon_says_when_to_try_again_and_nothing_is_retried(capsys):
    daemon = _Daemon({"config show": loopback.Busy(1)})
    _through(daemon, "config show", "exit")
    sentence = _sentence_and_exit_1(capsys, "busy")
    assert "try again in 1 second." in sentence
    assert daemon.sent == ["config show"]


@pytest.mark.parametrize(
    ("answer", "named"),
    [
        (loopback.UnknownAnswer(404, ""), "HTTP 404"),
        (loopback.UnknownAnswer(500, ""), "HTTP 500"),
        (loopback.UnknownAnswer(200, " with a body that is not JSON"), "not JSON"),
    ],
)
def test_an_unknown_answer_is_one_sentence_naming_the_status_and_exit_1(answer, named, capsys):
    daemon = _Daemon({"config show": answer})
    _through(daemon, "config show", "exit")
    _sentence_and_exit_1(capsys, named)


def test_a_token_file_that_cannot_be_used_is_named_and_nothing_is_sent(capsys):
    sentence = "/h/.pm-ai/private/api_token is readable by group or others, so this line was not sent."
    daemon = _Daemon({"config show": loopback.NotSent(sentence)})
    _, _, screen = _through(daemon, "config show", "exit")
    _sentence_and_exit_1(capsys, "api_token")
    assert screen.prompts == 2


def test_a_client_that_raises_is_one_sentence_and_exit_1_and_the_session_continues(capsys):
    daemon = _Daemon({"config show": RuntimeError("boom")})
    code, _, screen = _through(daemon, "config show", "doctor", "exit")
    assert code == EXIT_OK and screen.prompts == 3
    sentence = _sentence_and_exit_1(capsys, "RuntimeError")
    assert "boom" not in sentence


@pytest.mark.parametrize(
    ("raised", "said"),
    [
        (loopback.WaitAbandoned(sent=True), "it finishes the command on its own"),
        (KeyboardInterrupt(), "it finishes the command on its own"),
        (loopback.WaitAbandoned(sent=False), "so it was not sent"),
    ],
)
def test_ctrl_c_while_waiting_abandons_the_wait_and_the_session_continues(raised, said, capsys):
    daemon = _Daemon({"config show": raised})
    code, _, screen = _through(daemon, "config show", "doctor", "exit")
    assert code == EXIT_OK
    assert screen.prompts == 3
    captured = capsys.readouterr()
    assert said in captured.err
    assert "exit 3" in _lines(captured.out)
    assert daemon.sent == ["config show"]


# ── Jobs ─────────────────────────────────────────────────────────────────────


def test_a_result_arriving_while_a_line_is_typed_waits_for_the_next_prompt(capsys):
    """The result arrives while `read` is showing the prompt; it is printed after Enter."""
    job = "job_bbbbbbbbbbbbbbbb"
    daemon = _Daemon(
        {"dashboard": loopback.Acknowledged(job)},
        {job: loopback.Answer("written\n", EXIT_OK)},
        hold=(job,),
    )

    def typing():
        # Half a line typed when the result arrives: nothing may be printed into it.
        daemon.release[job].set()
        assert daemon.arrived.wait(10)
        _join_followers()
        sys.stdout.write("<typed>")
        return "doctor"

    _through(daemon, "dashboard", typing, "exit")
    out = capsys.readouterr().out
    assert "pm-ai> <typed>ran doctor\nexit 0\n" in out
    assert out.index("<typed>") < out.index(f"{job}: written")


def test_a_second_acknowledgement_for_another_scope_lists_both_ids(capsys):
    first, second = "job_1111111111111111", "job_2222222222222222"
    daemon = _Daemon(
        {"dashboard": loopback.Acknowledged(first), "dashboard --scope project:alpha": loopback.Acknowledged(second)},
        hold=(first, second),
    )
    try:
        _through(daemon, "dashboard", "dashboard --scope project:alpha", "exit")
    finally:
        for gate in daemon.release.values():
            gate.set()
    assert f"jobs in flight: {first}, {second}" in capsys.readouterr().out


def test_the_same_id_twice_has_one_follower_and_its_result_printed_once(capsys):
    job = "job_3333333333333333"
    daemon = _Daemon(
        {"dashboard": loopback.Acknowledged(job)},
        {job: loopback.Answer("written\n", EXIT_OK)},
        hold=(job,),
    )
    _through(daemon, "dashboard", "dashboard", _arrived(daemon), "exit")
    out = capsys.readouterr().out
    assert daemon.followed == [job]
    assert out.count(f"{job}: written") == 1
    assert out.count(f"{job}: exit 0") == 1
    assert "jobs in flight" not in out


def test_a_lost_stream_is_one_sentence_naming_the_job(capsys):
    job = "job_cccccccccccccccc"
    daemon = _Daemon(
        {"dashboard": loopback.Acknowledged(job)},
        {job: loopback.StreamLost(job, "the daemon closed its stream before the job finished")},
        hold=(job,),
    )
    code, _, _ = _through(daemon, "dashboard", _arrived(daemon), "exit")
    assert code == EXIT_OK
    (sentence,) = capsys.readouterr().err.strip().splitlines()
    assert job in sentence and "could not be read" in sentence


@pytest.mark.parametrize("ending", ["exit", EOFError()])
def test_ending_with_a_job_pending_names_it_and_says_it_continues_in_the_daemon(ending, capsys):
    job = "job_dddddddddddddddd"
    daemon = _Daemon({"dashboard": loopback.Acknowledged(job)}, hold=(job,))
    try:
        code, _, _ = _through(daemon, "dashboard", ending)
    finally:
        daemon.release[job].set()
    assert code == EXIT_OK
    assert f"{job} is still running; it continues in the daemon" in capsys.readouterr().out


def test_a_job_finishing_as_the_session_ends_is_printed_not_lost(capsys, monkeypatch):
    """The result lands between reading the jobs in flight and delivering: it is printed."""
    job = "job_ffffffffffffffff"
    daemon = _Daemon(
        {"dashboard": loopback.Acknowledged(job)},
        {job: loopback.Answer("written\n", EXIT_OK)},
        hold=(job,),
    )
    original = repl._Jobs.in_flight
    ending: list[bool] = []

    def in_flight(self):
        running = original(self)
        if ending:
            # The job finishes right after it was read as running.
            daemon.release[job].set()
            assert daemon.arrived.wait(10)
            _join_followers()
        return running

    monkeypatch.setattr(repl._Jobs, "in_flight", in_flight)

    def exiting():
        ending.append(True)
        return "exit"

    code, _, _ = _through(daemon, "dashboard", exiting)
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert f"{job}: written\n{job}: exit 0\n" in out
    assert "still running" not in out


def test_a_failed_follow_is_a_lost_stream_not_a_crash(capsys):
    job = "job_eeeeeeeeeeeeeeee"

    class _Broken(_Daemon):
        def follow(self, job_id):
            try:
                raise RuntimeError("boom")
            finally:
                self.arrived.set()

    daemon = _Broken({"dashboard": loopback.Acknowledged(job)})
    _through(daemon, "dashboard", _arrived(daemon), "exit")
    assert job in capsys.readouterr().err


# ── Never sent ───────────────────────────────────────────────────────────────


def test_an_open_line_is_never_sent_to_the_daemon(capsys):
    daemon = _Daemon()
    _, runner, _ = _through(daemon, "what did I promise Laura", "exit")
    assert daemon.sent == [] and runner.calls == []
    assert "names no command" in capsys.readouterr().out


def test_opening_the_prompt_sends_nothing():
    daemon = _Daemon()
    _through(daemon, "exit")
    assert daemon.sent == [] and daemon.followed == []


def test_no_module_in_pm_ai_redirects_the_process_streams():
    """Per-request output streams, never process-wide redirection (story 4q).

    Read from the syntax tree — imports and calls — so a docstring that names
    the function to say why it is not used is not a hit.
    """
    banned = {"redirect_stdout", "redirect_stderr"}
    root = Path(pm_ai.__file__).parent
    hits: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            named: set[str] = set()
            if isinstance(node, ast.ImportFrom):
                named = {alias.name for alias in node.names}
            elif isinstance(node, ast.Call):
                func = node.func
                named = {func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")}
            elif isinstance(node, ast.Attribute):
                named = {node.attr}
            if named & banned:
                hits.append(f"{path.relative_to(root)}:{node.lineno}")
    assert hits == []


def test_a_job_failure_reads_as_its_sentence_and_exit_1():
    """`failed` carries no exit code; it is shown as the command stopping unexpectedly."""
    event = {"state": "failed", "result": {"error": "the daemon stopped before this job finished", "type": "DaemonStopped"}}
    assert loopback._result("job_x", event) == loopback.Answer(
        "pm-ai: the daemon stopped before this job finished\n", EXIT_UNEXPECTED
    )
