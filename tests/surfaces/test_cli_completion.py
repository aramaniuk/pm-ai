"""Story 4u — one test per row of the prompt's completion and hint matrix.

The completer is driven with stand-in readers, so each row is a unit test; the
rows about the line editor drive the real `prompt_toolkit` session over a pipe
in place of a terminal. The startup row and the acceptance criterion need a
pseudo-terminal and are in `tests/slice/test_repl_startup.py`.
"""

from __future__ import annotations

import json
import shlex
import socket
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

import pm_ai.surfaces.cli as cli
from pm_ai.app import entry
from pm_ai.surfaces.cli import completion as completing
from pm_ai.surfaces.cli import repl
from pm_ai.surfaces.cli.completion import Completion, Readers, Split, _quoted, source_of, split_line
from pm_ai.surfaces.cli.dispatch import _HELP_FLAGS, EXIT_OK, TABLE

ALPHA_BETA = ("alpha", "beta")

GOALS = b"""## Project

- [g_b] (long) Second project goal
- [g_a] (medium) Cut latency
"""


def _completion(
    *,
    projects: Callable[[], Sequence[str]] = lambda: ALPHA_BETA,
    graph: Callable[[], Sequence[str]] = lambda: ("graph:work",),
    goals: Callable[[], Sequence[str]] = lambda: ("g_a", "g_b"),
) -> Completion:
    return Completion(Readers(projects=projects, graph_instances=graph, goal_ids=goals))


def _offered(line: str, completion: Completion | None = None) -> list[str]:
    """The candidates' inserted text for the word `line` ends with."""
    return [candidate.text for candidate in (completion or _completion()).complete(line)]


def _completed(line: str, completion: Completion | None = None) -> list[str]:
    """`line` with each candidate put in place, as the prompt would put it."""
    return [
        line[: candidate.start] + candidate.text
        for candidate in (completion or _completion()).complete(line)
    ]


# ── Tab ──────────────────────────────────────────────────────────────────────


def test_the_first_word_completes_from_the_table():
    assert _offered("da") == ["dashboard", "daemon"]


def test_an_empty_line_offers_every_command():
    assert set(TABLE) <= set(_offered(""))


def test_a_group_offers_its_subcommands_as_the_table_declares_them():
    assert _offered("goal ") == ["set", "update", "delete", "list", "show"]
    assert _offered("connector ") == list(TABLE["connector"].leaves)


def test_options_are_offered_and_one_already_given_is_not():
    answers = ["--title", "--start", "--attendees", "--duration"]
    assert _offered("transcript drop x.vtt --") == ["--scope", "--meeting", *answers]
    assert _offered("transcript drop x.vtt --scope personal --") == ["--meeting", *answers]


def test_a_flag_is_offered_like_an_option():
    assert _offered("goal delete g_a --") == ["--yes"]


def test_scope_offers_personal_and_each_enrolled_project():
    assert _offered("dashboard --scope ") == ["personal", "project:alpha", "project:beta"]


def test_an_unreadable_registry_offers_personal_only_and_says_nothing(capsys):
    def broken() -> Sequence[str]:
        raise OSError("unreadable")

    assert _offered("dashboard --scope ", _completion(projects=broken)) == ["personal"]
    assert capsys.readouterr() == ("", "")


def test_scope_with_equals_completes_as_scope_with_a_space():
    assert _completed("dashboard --scope=pro") == [
        "dashboard --scope=project:alpha",
        "dashboard --scope=project:beta",
    ]


def test_sign_in_offers_an_enrolled_graph_row_even_when_disabled(tmp_path, monkeypatch):
    """Through the composition root's reader: `4n`'s row rule, by path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rows = tmp_path / ".pm-ai" / "connectors"
    rows.mkdir(parents=True)
    (rows / "graph:work.json").write_text(
        json.dumps({"instance": "graph:work", "system": "graph", "enabled": False})
    )
    (rows / "gitlab:alpha.json").write_text(
        json.dumps({"instance": "gitlab:alpha", "system": "gitlab", "enabled": True})
    )
    (rows / "notes.txt").write_text("not a row")
    assert _offered("connector sign-in ", entry._completion()) == ["graph:work"]


def test_add_offers_the_supported_systems_then_nothing_for_a_new_name():
    assert _offered("connector add ") == ["gitlab", "graph"]
    assert _offered("connector add graph ") == []


def test_goal_ids_on_file_are_offered(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    goals = tmp_path / ".manager-ai" / "memory" / "strategic_goals.md"
    goals.parent.mkdir(parents=True)
    goals.write_bytes(GOALS)
    for leaf in ("show", "update", "delete"):
        assert _offered(f"goal {leaf} ", entry._completion()) == ["g_b", "g_a"]


def test_the_member_commands_offer_the_named_projects_members_meetings_and_links(tmp_path, monkeypatch):
    """Story 7e, through the composition root's readers over real files."""
    from datetime import datetime, timedelta, timezone

    from pm_ai.app.wiring import build
    from pm_ai.core.member_updates import ItemKind, LinkKind, UpdateLine, render_update
    from pm_ai.core.project_registry import ProjectEntry, render_registry
    from pm_ai.domain.handles import gitlab_handle
    from pm_ai.domain.identity import Actor, DataScope, ScopeKind
    from pm_ai.domain.meetings import Meeting
    from pm_ai.domain.work import GitLabPerson, Member, Section, SectionRead, WorkChange
    from pm_ai.platform.paths import ScopePaths

    monkeypatch.setenv("HOME", str(tmp_path))
    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    repos = {"alpha": tmp_path / "alpha", "beta": tmp_path / "beta"}
    for repo in repos.values():
        repo.mkdir()
    registry = tmp_path / ".pm-ai" / "projects.toml"
    registry.parent.mkdir(parents=True)
    registry.write_bytes(render_registry({pid: ProjectEntry(path) for pid, path in repos.items()}))
    daemon = build(
        None, "alpha", paths=ScopePaths.production(home=tmp_path, projects=repos), now=lambda: now
    )
    alpha = DataScope(ScopeKind.PROJECT, "alpha")
    host = "https://gitlab.example.com"
    ivan = GitLabPerson(gitlab_handle(host, 2), "Ivan Petrov", "ivan.p", False)
    old = GitLabPerson(gitlab_handle(host, 5), "Old Timer", "old.t", False)
    bot = GitLabPerson(gitlab_handle(host, 6), "Release Bot", "release-bot", True)
    daemon.storage.record_project_work(
        alpha,
        WorkChange(
            instance="gitlab:alpha",
            started_at=now - timedelta(hours=1),
            members=(Member(ivan, "active"), Member(old, "blocked"), Member(bot, "active")),
            sections=tuple(SectionRead(section, True) for section in Section),
        ),
    )
    daemon.meetings.put(
        Meeting("mtg_a", "Weekly planning", now - timedelta(days=1), 30, (Actor("email:a@example.com"),), alpha)
    )
    line = UpdateLine(
        kind=ItemKind.ACTION, meeting="mtg_a", start=now - timedelta(days=1), title="Weekly planning",
        item="prp_x", issue=41, by=LinkKind.MODEL, text="rework export", at=now, owner="Ваня",
    )
    daemon.storage.append_member_updates([render_update(line)] * 2, scope=alpha, after=0)
    daemon.storage.close()

    completion = entry._completion()
    assert _offered("member name --scope project:alpha Ваня ", completion) == [
        "ivan.p",
        str(ivan.handle),
    ]
    assert _offered("member name --scope project:alpha Ваня ivan.p --meeting ", completion) == ["mtg_a"]
    assert _offered("member link --scope project:alpha ", completion) == [line.update]
    assert _offered("member link --scope project:beta ", completion) == []
    assert _offered("member link ", completion) == []
    assert _offered("member name --scope project:nowhere Ваня ", completion) == []


def test_a_broken_goal_file_offers_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    goals = tmp_path / ".manager-ai" / "memory" / "strategic_goals.md"
    goals.parent.mkdir(parents=True)
    goals.write_bytes(b"## Nowhere\n\n- [g_a] (forever) Not a goal\n")
    assert _offered("goal show ", entry._completion()) == []
    assert capsys.readouterr() == ("", "")


def test_domain_and_horizon_offer_the_goal_vocabulary():
    assert _offered("goal set --domain ") == ["project", "team", "personal"]
    assert _offered("goal update g_a --horizon t") == ["tactical"]


def test_free_text_offers_nothing():
    assert _offered("goal set ") == []
    assert _offered("goal set g_a --title ") == []
    assert _offered("transcript drop x.vtt --meeting ") == []
    assert _offered("project add ./repo ") == []


def test_a_path_with_a_space_is_inserted_quoted_and_splits_back(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "My Notes.vtt").write_text("WEBVTT\n")
    assert _offered("transcript drop My") == ["'My Notes.vtt'"]
    (line,) = _completed("transcript drop My")
    assert shlex.split(line) == ["transcript", "drop", "My Notes.vtt"]


def test_a_dollar_or_a_tilde_mid_word_is_quoted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a$b.vtt").write_text("")
    (tmp_path / "x~y.vtt").write_text("")
    for typed, name in (("a", "a$b.vtt"), ("x", "x~y.vtt")):
        (line,) = _completed(f"transcript drop {typed}")
        assert line == f"transcript drop '{name}'"
        assert shlex.split(line)[-1] == name


def test_inside_an_open_quote_the_path_completes_within_it_and_closes_it(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "My Notes.vtt").write_text("WEBVTT\n")
    assert _completed('transcript drop "My') == ['transcript drop "My Notes.vtt"']
    assert _completed("transcript drop 'My") == ["transcript drop 'My Notes.vtt'"]


def test_paths_are_relative_to_the_folder_and_expand_the_home(tmp_path, monkeypatch):
    home, working = tmp_path / "home", tmp_path / "work"
    (home / "Documents").mkdir(parents=True)
    (working / "sub").mkdir(parents=True)
    (working / ".hidden").write_text("")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(working)
    assert _offered("project add s") == ["sub/"]
    assert _offered("project add ") == ["sub/"]
    assert _offered("project add ~/Doc") == ["~/Documents/"]


def test_a_folder_that_cannot_be_listed_offers_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _offered("transcript drop absent/x") == []


def test_an_unknown_word_offers_nothing():
    assert _offered("what did I ") == []


# ── The bottom line ──────────────────────────────────────────────────────────


def test_a_command_shows_its_usage_and_summary():
    hint = _completion().hint("transcript drop ")
    assert hint is not None
    assert "transcript drop <file> --scope <scope> [--meeting <meeting>]" in hint
    assert TABLE["transcript"].leaves["drop"].summary in hint


def test_a_group_alone_lists_its_subcommands_as_the_table_declares_them():
    hint = _completion().hint("connector ")
    assert hint is not None
    assert "{" + ", ".join(TABLE["connector"].leaves) + "}" in hint


@pytest.mark.parametrize("word", ["help", "-h", "--help"])
def test_a_help_word_says_it_lists_every_command(word):
    assert _completion().hint(word) == f"{word}: lists every command"


@pytest.mark.parametrize("line", ["", "what did I", "dash"])
def test_an_open_line_or_an_unknown_word_shows_nothing(line):
    assert _completion().hint(line) is None


# ── The table is the one list ────────────────────────────────────────────────


def test_every_name_a_command_declares_has_a_source_or_is_free_text():
    """A new argument or option cannot go uncompleted silently."""
    unsourced: list[str] = []
    for name, entry_ in TABLE.items():
        leaves = entry_.leaves.items() if entry_.leaves else [(None, entry_)]
        for leaf_name, leaf in leaves:
            command = (name,) if leaf_name is None else (name, leaf_name)
            for declared in (*leaf.takes, *leaf.optional, *leaf.options):
                try:
                    source_of(command, declared)
                except LookupError as missing:
                    unsourced.append(str(missing))
    assert unsourced == []


def test_the_check_fails_for_a_name_nobody_declared_a_source_for():
    with pytest.raises(LookupError, match="level"):
        source_of(("dashboard",), "level")


def test_free_text_is_listed_in_completion_not_in_the_table():
    assert (("connector", "add"), "instance") in completing.FREE_TEXT
    assert (("goal", "set"), "id") in completing.FREE_TEXT
    assert {"alias", "title", "meeting"} <= completing.FREE_TEXT_NAMES


# ── Reading the files ────────────────────────────────────────────────────────


def test_each_reader_runs_at_the_first_tab_that_needs_it_and_once_between_commands():
    calls: list[str] = []

    def projects() -> Sequence[str]:
        calls.append("projects")
        return ALPHA_BETA

    completion = _completion(projects=projects)
    assert calls == []
    _offered("connector add ", completion)
    assert calls == []
    _offered("dashboard --scope ", completion)
    _offered("dashboard --scope p", completion)
    assert calls == ["projects"]
    completion.forget()
    _offered("dashboard --scope ", completion)
    assert calls == ["projects", "projects"]


def test_a_project_enrolled_during_the_session_is_offered_next():
    """`project add ~/x gamma`, then `--scope ` Tab: read again after the command."""
    enrolled = ["alpha"]
    completion = _completion(projects=lambda: tuple(enrolled))
    offered: list[list[str]] = []
    steps = iter(["dashboard", "project add ~/x gamma", "doctor"])

    def read(prompt: str) -> str:
        offered.append(_offered("dashboard --scope ", completion))
        return next(steps, "exit")

    def runner(words: Sequence[str]) -> int:
        if list(words[:2]) == ["project", "add"]:
            enrolled.append(words[3])
        return EXIT_OK

    assert repl.run(runner, read=read, completion=completion) == EXIT_OK
    assert offered[1] == ["personal", "project:alpha"]
    assert offered[2] == ["personal", "project:alpha", "project:gamma"]


def test_no_tab_reaches_the_daemon_or_opens_a_store(tmp_path, monkeypatch):
    """No daemon running, Tab anywhere: completes, and no connection is made."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    connected: list[object] = []
    monkeypatch.setattr(socket.socket, "connect", lambda self, *a: connected.append(a))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: connected.append(a))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("completion built a store or asked the keychain")

    monkeypatch.setattr(entry, "StorageService", forbidden)
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", forbidden)
    completion = entry._completion()
    for line in (
        "",
        "da",
        "dashboard --scope ",
        "connector sign-in ",
        "goal show ",
        "transcript drop ",
        # Story 7e: the member commands read by path or read-only, never a store.
        "member name --scope project:alpha Ваня ",
        "member name --scope project:alpha Ваня ivan.p --meeting ",
        "member link --scope project:alpha ",
    ):
        completion.complete(line)
        completion.hint(line)
    assert _offered("dashboard --scope ", completion) == ["personal"]
    assert connected == []
    assert list(tmp_path.iterdir()) == []


# ── The line editor ──────────────────────────────────────────────────────────


@pytest.fixture
def pipe():
    from prompt_toolkit.input import create_pipe_input

    with create_pipe_input() as created:
        yield created


def _editor(pipe, completion: Completion | None = None) -> Callable[[str], str]:
    from prompt_toolkit.output import DummyOutput

    from pm_ai.surfaces.cli import editor

    return editor.session(
        completion or _completion(), keep=repl.is_a_command_line, input=pipe, output=DummyOutput()
    )


def _typed(read: Callable[[str], str], pipe, *keys: str) -> str:
    """One line read while `keys` arrive a moment apart, as a person types them.

    The library finds a suggestion or a completion between keys, so keys sent
    all at once would arrive before there is anything to accept.
    """

    def type_() -> None:
        for key in keys:
            time.sleep(0.1)
            pipe.send_text(key)

    typist = threading.Thread(target=type_, daemon=True)
    typist.start()
    try:
        return read(repl.PROMPT)
    finally:
        typist.join(timeout=5)


def test_tab_inserts_the_one_candidate(pipe):
    read = _editor(pipe)
    assert _typed(read, pipe, "dashb", "\t", "\r") == "dashboard"
    assert _typed(read, pipe, "dashboard --scope=project:a", "\t", "\r") == (
        "dashboard --scope=project:alpha"
    )


def test_a_suggestion_from_an_earlier_command_is_accepted_with_the_right_arrow(pipe):
    read = _editor(pipe)
    assert _typed(read, pipe, "config show\r") == "config show"
    assert _typed(read, pipe, "con", "\x1b[C", "\r") == "config show"


def test_an_open_line_is_not_kept_suggested_or_recalled(pipe):
    read = _editor(pipe)
    assert _typed(read, pipe, "config show\r") == "config show"
    assert _typed(read, pipe, "what did I promise\r") == "what did I promise"
    assert _typed(read, pipe, "wh", "\x1b[C", "\r") == "wh"
    assert _typed(read, pipe, "\x1b[A", "\r") == "config show"


def test_up_arrow_recalls_the_previous_command(pipe):
    read = _editor(pipe)
    assert _typed(read, pipe, "config show\r") == "config show"
    assert _typed(read, pipe, "\x1b[A", "\r") == "config show"


def test_ctrl_c_clears_ctrl_d_and_exit_end_the_session(pipe):
    """Through `run`: Ctrl-C clears a half-typed line; Ctrl-D ends; so does `exit`."""
    editing = _editor(pipe)
    ran: list[list[str]] = []

    def runner(words: Sequence[str]) -> int:
        ran.append(list(words))
        return EXIT_OK

    def session(*lines: tuple[str, ...]) -> Callable[[str], str]:
        queue = list(lines)
        return lambda prompt: _typed(editing, pipe, *queue.pop(0))

    assert repl.run(runner, read=session(("doc", "\x03"), ("doctor\r",), ("\x04",))) == EXIT_OK
    assert ran == [["doctor"]]
    assert repl.run(runner, read=session(("exit\r",))) == EXIT_OK
    assert ran == [["doctor"]]


def test_no_history_file_is_written(tmp_path, monkeypatch, pipe):
    home, working = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    working.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(working)
    read = _editor(pipe)
    for line in ("config show\r", "what now\r", "\x1b[A\r"):
        _typed(read, pipe, line)
    assert list(home.iterdir()) == []
    assert list(working.iterdir()) == []


def test_the_process_starting_features_are_off(pipe):
    """No editor (Ctrl-X Ctrl-E), no system prompt (Meta-!), no suspend (Ctrl-Z)."""
    from prompt_toolkit.output import DummyOutput

    from pm_ai.surfaces.cli import editor

    built = editor.build(
        _completion(), keep=repl.is_a_command_line, input=pipe, output=DummyOutput()
    )
    assert built.enable_open_in_editor is False
    assert built.enable_system_prompt is False
    assert built.enable_suspend is False
    read = _editor(pipe)
    assert _typed(read, pipe, "\x18\x05", "\x1b!", "\r") == "!"
    # Ctrl-Z neither suspends nor inserts itself: the line is unchanged.
    assert _typed(read, pipe, "doc", "\x1a", "tor\r") == "doctor"


def test_the_cursor_position_request_is_off_and_the_hint_line_still_draws(monkeypatch):
    import os
    import pty

    from pm_ai.surfaces.cli import editor

    leader, follower = pty.openpty()
    try:
        with os.fdopen(follower, "w", closefd=True) as terminal:
            monkeypatch.setattr(sys, "stdout", terminal)
            output = editor.terminal_output()
            assert output.enable_cpr is False
            assert output.responds_to_cpr is False
            # Without the request's answer the library would never draw the
            # bottom line; the output says the cursor's own row is there.
            assert output.get_rows_below_cursor_position() == 1
    finally:
        os.close(leader)


# ── When the line editor is not used ─────────────────────────────────────────


def _without_prompt_toolkit(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "prompt_toolkit", None)
    monkeypatch.delitem(sys.modules, "pm_ai.surfaces.cli.editor", raising=False)
    monkeypatch.delattr(cli, "editor", raising=False)


def _lines(*lines: str) -> Callable[[str], str]:
    queue = list(lines)

    def read(prompt: str) -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return read


def test_without_the_runtime_extra_the_prompt_says_so_once_and_continues(
    monkeypatch, capsys
):
    _without_prompt_toolkit(monkeypatch)
    monkeypatch.setattr(repl, "_input", _lines("doctor", "config show", "exit"))
    ran: list[list[str]] = []
    code = repl.run(lambda words: ran.append(list(words)) or EXIT_OK, line_editing=True)
    assert code == EXIT_OK
    assert ran == [["doctor"], ["config", "show"]]
    err = capsys.readouterr().err
    assert err.splitlines() == [repl.NO_LINE_EDITOR]
    assert "uv sync --extra runtime" in err


def test_a_session_that_cannot_start_names_the_failure_and_continues(monkeypatch, capsys):
    from pm_ai.surfaces.cli import editor

    def refuses(*_args, **_kwargs):
        raise RuntimeError("no terminal for you")

    monkeypatch.setattr(editor, "session", refuses)
    monkeypatch.setattr(repl, "_is_a_terminal", lambda stream: True)
    monkeypatch.setattr(repl, "_input", _lines("doctor", "exit"))
    ran: list[list[str]] = []
    code = repl.run(lambda words: ran.append(list(words)) or EXIT_OK, line_editing=True)
    assert code == EXIT_OK
    assert ran == [["doctor"]]
    err = capsys.readouterr().err.splitlines()
    assert err == [repl.line_editor_failed(RuntimeError())]
    assert "RuntimeError" in err[0]


def test_a_library_failure_mid_session_falls_back_and_continues(monkeypatch, capsys):
    from pm_ai.surfaces.cli import editor

    def fails(prompt: str) -> str:
        raise RuntimeError("the library broke")

    monkeypatch.setattr(editor, "session", lambda *a, **k: fails)
    monkeypatch.setattr(repl, "_is_a_terminal", lambda stream: True)
    monkeypatch.setattr(repl, "_input", _lines("doctor", "exit"))
    ran: list[list[str]] = []
    assert repl.run(lambda words: ran.append(list(words)) or EXIT_OK, line_editing=True) == EXIT_OK
    assert ran == [["doctor"]]
    assert capsys.readouterr().err.splitlines() == [
        repl.line_editor_failed(RuntimeError(), started=True)
    ]


def test_the_command_line_never_loads_the_library():
    """`pm-ai <command>` imports the prompt's modules but not `prompt_toolkit`."""
    import subprocess

    probe = (
        "import sys, pm_ai.app.entry\n"
        "from pm_ai.app.entry import main\n"
        "main(['--help'])\n"
        "print('LOADED' if 'prompt_toolkit' in sys.modules else 'NOT LOADED')\n"
    )
    printed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout
    assert printed.strip().endswith("NOT LOADED"), printed


def test_the_history_keeps_command_lines_only():
    assert repl.is_a_command_line("config show")
    assert repl.is_a_command_line("--help")
    assert not repl.is_a_command_line("what did I promise")
    assert not repl.is_a_command_line("exit")
    assert not repl.is_a_command_line("'unclosed")
    assert not repl.is_a_command_line("")


def test_completion_modules_import_no_line_editing_library():
    """`completion` is plain Python: only `editor` names `prompt_toolkit`."""
    package = Path(cli.__file__).parent
    for name in ("completion.py", "repl.py", "dispatch.py"):
        text = (package / name).read_text()
        assert "import prompt_toolkit" not in text and "from prompt_toolkit" not in text, name


# ── Review round: the fallback's edges ───────────────────────────────────────


def test_ctrl_d_through_the_line_editor_ends_the_session_quietly(pipe, monkeypatch, capsys):
    """`_Edited` is in the path: Ctrl-D is the ending, not a library failure."""
    from pm_ai.surfaces.cli import editor

    real = _editor(pipe)
    chunks = ["doctor\r", "\x04"]

    def read(prompt: str) -> str:
        pipe.send_text(chunks.pop(0))
        return real(prompt)

    monkeypatch.setattr(editor, "session", lambda *a, **k: read)
    monkeypatch.setattr(repl, "_is_a_terminal", lambda stream: True)
    monkeypatch.setattr(repl, "_input", _lines())
    ran: list[list[str]] = []
    assert repl.run(lambda words: ran.append(list(words)) or EXIT_OK, line_editing=True) == EXIT_OK
    assert ran == [["doctor"]]
    err = capsys.readouterr().err
    assert "line editor" not in err, err


def _import_fails_with(monkeypatch, fake) -> None:
    monkeypatch.setitem(sys.modules, "prompt_toolkit", fake)
    monkeypatch.delitem(sys.modules, "pm_ai.surfaces.cli.editor", raising=False)
    monkeypatch.delattr(cli, "editor", raising=False)


def test_an_installed_but_incompatible_library_is_said_by_type(monkeypatch, capsys):
    """`cannot import name ...` is not a missing extra."""
    import types

    _import_fails_with(monkeypatch, types.ModuleType("prompt_toolkit"))
    monkeypatch.setattr(repl, "_input", _lines("doctor", "exit"))
    ran: list[list[str]] = []
    assert repl.run(lambda words: ran.append(list(words)) or EXIT_OK, line_editing=True) == EXIT_OK
    assert ran == [["doctor"]]
    assert capsys.readouterr().err.splitlines() == [repl.line_editor_failed(ImportError())]


def test_any_other_failure_importing_the_editor_is_said_by_type(monkeypatch, capsys):
    import types

    class Exploding(types.ModuleType):
        def __getattr__(self, name: str) -> object:
            raise RuntimeError("broken on import")

    _import_fails_with(monkeypatch, Exploding("prompt_toolkit"))
    monkeypatch.setattr(repl, "_input", _lines("doctor", "exit"))
    ran: list[list[str]] = []
    assert repl.run(lambda words: ran.append(list(words)) or EXIT_OK, line_editing=True) == EXIT_OK
    assert ran == [["doctor"]]
    assert capsys.readouterr().err.splitlines() == [repl.line_editor_failed(RuntimeError())]


# ── Review round: paths ──────────────────────────────────────────────────────


def test_a_lone_tilde_offers_the_home_and_no_other_tilde_form_is_listed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    assert _offered("project add ~") == ["~/"]
    assert _offered("project add ~root") == []
    assert _offered("project add ~root/") == []
    assert _offered("project add ~root/Doc") == []


def test_dot_and_dot_dot_offer_their_folders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".hidden").write_text("")
    assert _offered("project add .") == ["./", "../", ".hidden"]
    assert _offered("project add ..") == ["../"]


def test_a_home_path_with_a_space_keeps_its_tilde_and_splits_back(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "My Repo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    (line,) = _completed("project add ~/My")
    assert line == "project add ~/'My Repo/'"
    assert shlex.split(line)[-1] == "~/My Repo/"


def test_after_a_bare_double_dash_a_dashed_word_is_a_positional(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "--odd.vtt").write_text("")
    assert _offered("transcript drop --scope personal -- --o") == ["--odd.vtt"]
    assert _offered("transcript drop --scope personal -- --") == ["--odd.vtt"]
    assert _offered("transcript drop -- x.vtt --") == []


# ── Review round: the Graph rows on disk ─────────────────────────────────────


def test_hidden_files_and_claim_sidecars_are_not_rows(tmp_path, monkeypatch):
    from pm_ai.domain.scope_model import CLAIM_SIDECAR_SUFFIX

    monkeypatch.setenv("HOME", str(tmp_path))
    rows = tmp_path / ".pm-ai" / "connectors"
    rows.mkdir(parents=True)
    row = {"instance": "graph:hidden", "system": "graph", "enabled": True}
    (rows / ".graph:hidden.json").write_text(json.dumps(row))
    (rows / f"graph:sidecar.json{CLAIM_SIDECAR_SUFFIX}").write_text(
        json.dumps({**row, "instance": "graph:sidecar"})
    )
    (rows / "graph:real.json").mkdir()
    assert _offered("connector sign-in ", entry._completion()) == []


def test_a_missing_connectors_folder_offers_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _offered("connector sign-in ", entry._completion()) == []
    assert capsys.readouterr() == ("", "")
    assert list(tmp_path.iterdir()) == []


# ── Review round: copies that must not drift ─────────────────────────────────


def test_the_help_and_ending_words_are_the_originals():
    assert set(completing.HELP_WORDS) == set(_HELP_FLAGS)
    assert set(completing.ENDING_WORDS) == set(repl.ENDINGS)


# ── Review round: splitting ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "a\\ b c",
        "a\\\\b",
        'say "" twice',
        "x \\'y\\'",
        'in "a \\" b" out',
        "'' doctor",
        '"\\$HOME \\x"',
    ],
)
def test_finished_words_split_as_shlex_splits_them(line):
    split = split_line(line + " ")
    assert list(split.words) == shlex.split(line)
    assert (split.current, split.quote, split.broken) == ("", None, False)


def test_an_escaped_space_stays_in_the_current_word():
    assert split_line("transcript drop My\\ No") == Split(("transcript", "drop"), "My No", 16, None)


def test_an_empty_quoted_word_is_a_word():
    assert split_line('"" x').words == ("",)


def test_an_open_quote_is_the_current_word_with_its_quote():
    assert split_line('drop "My No') == Split(("drop",), "My No", 5, '"')
    assert split_line("drop 'a") == Split(("drop",), "a", 5, "'")


def test_a_trailing_backslash_offers_nothing_and_shows_no_hint():
    """`shlex.split` refuses the line, so it would not run."""
    with pytest.raises(ValueError):
        shlex.split("transcript drop My\\")
    assert split_line("transcript drop My\\").broken
    assert _offered("transcript drop My\\") == []
    assert _completion().hint("transcript drop \\") is None


def test_inside_double_quotes_the_specials_are_escaped_and_split_back():
    quoted = _quoted('a "b" $c `d` \\e', '"')
    # `shlex` escapes only `"` and `\\` inside double quotes; `$` stays as is.
    assert quoted == '"a \\"b\\" $c `d` \\\\e"'
    assert shlex.split(quoted) == ['a "b" $c `d` \\e']


def test_transcript_summarize_by_offers_exactly_local_and_claude():
    """Story 7b: `--by` completes to who makes a summary, and nothing else."""
    assert _offered("transcript summarize --scope personal --by ") == ["local", "claude"]
