"""Story 4f — one test per row of the terminal prompt's I/O matrix.

The loop is driven with a fake runner and a scripted reader, so each row is a
unit test. The rows that need the real `entry.main` are in
`tests/surfaces/test_cli_dispatch.py`; the rows that need a real terminal (the
startup time, recall, the server imports) are in `tests/slice/test_repl_startup.py`.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence

import pytest

from pm_ai.surfaces.cli import repl
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, EXIT_USAGE, TABLE


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


def test_lines_are_still_read_when_readline_cannot_be_imported(monkeypatch):
    """`sys.modules[name] = None` makes the import raise `ImportError`."""
    monkeypatch.setitem(sys.modules, "readline", None)
    loaded: list[bool] = []
    original = repl._line_editing

    def recorded() -> bool:
        loaded.append(original())
        return loaded[-1]

    monkeypatch.setattr(repl, "_line_editing", recorded)
    runner = _Runner()
    reader = _Reader(["doctor", "exit"])
    assert repl.run(runner, read=reader, line_editing=True) == EXIT_OK
    assert loaded == [False]
    assert runner.calls == [["doctor"]]


def test_line_editing_is_off_unless_asked_for(monkeypatch):
    called: list[bool] = []
    monkeypatch.setattr(repl, "_line_editing", lambda: called.append(True) or True)
    _session("exit")
    assert called == []


def test_no_history_file_is_written(tmp_path, monkeypatch):
    """The real line-editing path, with readline's history writers recorded."""
    import readline
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

    reader = _Reader(["doctor", "what now", "exit"])
    assert repl.run(_Runner(), read=reader, line_editing=True) == EXIT_OK
    assert loaded == [True]
    assert written == []
    assert list(home.iterdir()) == []
    assert list(working.iterdir()) == []
