"""Story 4t — goals listed, shown, updated and deleted, through `main()`.

Spec: `_bmad-output/specs/spec-pm-ai/stories/4t-goals-listed-shown-updated-deleted.md`.

One test per row of the I/O matrix, each against a throwaway home through
`entry.main`, the way a person types it. Every "nothing written" row compares
the file's bytes before and after, and the personal event log's goal entries.
"""

from __future__ import annotations

from pathlib import Path

import dataclasses

import pytest

from test_line_answered_or_acknowledged import rig  # noqa: F401 — the daemon fixture
from pm_ai.app import entry
from pm_ai.app.goals import GoalReader
from pm_ai.app.wiring import application_storage, refreshed
from pm_ai.core.event_log import EventLog
from pm_ai.core.goal_register import ARTIFACT as GOALS_ARTIFACT
from pm_ai.core.goal_register import GOALS_SCOPE, parse_goals
from pm_ai.domain.event_entries import (
    MAX_ENTRY_LENGTH,
    MalformedEntry,
    SelfActionType,
    render_entry,
)
from pm_ai.domain.health import Report
from pm_ai.domain.scope_model import APPLICATION_DIRNAME
from pm_ai.platform.environment import DISABLE_ENCRYPTION_VAR
from pm_ai.ports import KeyNotFound
from pm_ai.storage.service import StorageService
from pm_ai.surfaces.cli import repl
from pm_ai.surfaces.cli.dispatch import (
    EXIT_OK,
    EXIT_REFUSAL,
    EXIT_USAGE,
    dispatch,
)

PERSONAL = GOALS_SCOPE

TWO_PROJECT_ONE_TEAM = b"""## Project

- [g_b] (long) Second project goal
- [g_a] (medium) Cut latency

## Team

- [g_hire] (short) Hire two
"""


class Keychain:
    """A keychain with one key in it, so nothing reaches the real one."""

    def __init__(self) -> None:
        self.secret: bytes | None = b"\x11" * 32

    def store(self, name: str, secret: bytes) -> None:
        self.secret = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        if self.secret is None:
            self.secret = secret

    def fetch(self, name: str) -> bytes:
        if self.secret is None:
            raise KeyNotFound(name)
        return self.secret

    def delete(self, name: str) -> None:
        self.secret = None


class Home:
    def __init__(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.home = home
        self.monkeypatch = monkeypatch
        self.path = home / ".manager-ai" / "memory" / GOALS_ARTIFACT

    def write(self, raw: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(raw)

    def raw(self) -> bytes | None:
        return self.path.read_bytes() if self.path.exists() else None

    def entries(self, category: SelfActionType | None = None) -> list:
        read = EventLog(application_storage(Keychain())).read(scope=PERSONAL)
        wanted = (
            {SelfActionType.GOAL_SET, SelfActionType.GOAL_DELETED}
            if category is None
            else {category}
        )
        return [item for item in read if item.category in wanted]

    def run(self, *argv: str, answers=(), tty=True, before_last=None) -> int:
        queue = list(answers)
        asked: list[str] = []

        def scripted(prompt: str) -> str:
            asked.append(prompt)
            if not queue:
                raise AssertionError(f"an unexpected prompt: {prompt!r}")
            if len(queue) == 1 and before_last is not None:
                before_last()
            answer = queue.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        self.monkeypatch.setattr("builtins.input", scripted)
        self.monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
        self.asked = asked
        code = entry.main(list(argv))
        assert not queue, f"answers left unasked: {queue}"
        return code


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Home:
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.delenv(DISABLE_ENCRYPTION_VAR, raising=False)
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", Keychain)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return Home(root, monkeypatch)


def _unchanged(home: Home, before: bytes | None, entries_before: int = 0) -> None:
    assert home.raw() == before
    assert len(home.entries()) == entries_before


# ── List ─────────────────────────────────────────────────────────────────────


def test_list_groups_by_domain_and_sorts_by_id(home, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "list", tty=False) == EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "Project",
        "  g_a  (medium)  Cut latency",
        "  g_b  (long)    Second project goal",
        "Team",
        "  g_hire  (short)   Hire two",
    ]


@pytest.mark.parametrize("raw", [None, b"", b"# My goals\n\nnothing yet\n"], ids=["absent", "empty", "prose"])
def test_list_with_nothing_names_goal_set(home, capsys, raw):
    if raw is not None:
        home.write(raw)
    assert home.run("goal", "list", tty=False) == EXIT_OK
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1
    assert "`pm-ai goal set`" in out


def test_list_of_a_broken_file_is_the_parsers_message_with_its_line(home, capsys):
    home.write(b"## Project\n\n- [g_a] (medium) Fine\n- [g_b] (soonish) Broken\n")
    assert home.run("goal", "list", tty=False) == EXIT_REFUSAL
    seen = capsys.readouterr()
    assert "line 4" in seen.err and "soonish" in seen.err
    assert "g_a" not in seen.out


def test_list_works_with_broken_settings_and_composes_nothing(home, monkeypatch, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    config = home.home / APPLICATION_DIRNAME / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(b"this is = not [toml\n")

    def never(*args, **kwargs):
        raise AssertionError("goal list composed a daemon")

    monkeypatch.setattr(entry, "_compose", never)
    assert home.run("goal", "list", tty=False) == EXIT_OK
    assert "g_hire" in capsys.readouterr().out


# ── Show ─────────────────────────────────────────────────────────────────────


def test_show_prints_the_goal_its_citation_and_its_file(home, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "show", "g_a", tty=False) == EXIT_OK
    out = capsys.readouterr().out
    for said in ("g_a", "goal:g_a", "project", "medium", "Cut latency", str(home.path)):
        assert said in out


def test_show_of_an_unknown_id_names_goal_list(home, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "show", "g_none", tty=False) == EXIT_REFUSAL
    assert "`pm-ai goal list`" in capsys.readouterr().err


def test_show_of_a_broken_file_is_the_parsers_message_with_its_line(home, capsys):
    home.write(b"## Project\n\n- [g_a] (soonish) Broken\n")
    assert home.run("goal", "show", "g_a", tty=False) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "line 3" in err and "soonish" in err


def test_show_with_no_file_names_goal_list(home, capsys):
    assert home.run("goal", "show", "g_a", tty=False) == EXIT_REFUSAL
    assert "`pm-ai goal list`" in capsys.readouterr().err
    assert home.raw() is None


# ── Set ──────────────────────────────────────────────────────────────────────


def test_set_by_options_writes_without_a_terminal_or_a_question(home, capsys):
    code = home.run(
        "goal", "set", "g_new", "--domain", "team", "--horizon", "short", "--title", "Hire two",
        tty=False,
    )
    assert code == EXIT_OK
    assert home.asked == []
    register = parse_goals(home.raw(), scope=PERSONAL)
    assert register["g_new"].title == "Hire two"
    (recorded,) = home.entries(SelfActionType.GOAL_SET)
    assert dict(recorded.fields)["goal_id"] == "g_new"


def test_set_with_a_value_missing_and_no_terminal_names_the_missing_options(home, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "set", "g_new", "--domain", "team", tty=False) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "--horizon" in err and "--title" in err and "--domain" not in err
    _unchanged(home, TWO_PROJECT_ONE_TEAM)


def test_set_with_no_id_and_no_terminal_names_the_id_and_the_missing_options(home, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "set", "--domain", "team", "--title", "x", tty=False) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "`<id>`" in err and "--horizon" in err
    assert "--domain" not in err and "--title" not in err
    _unchanged(home, TWO_PROJECT_ONE_TEAM)


def test_set_at_a_terminal_asks_todays_four_questions(home, capsys):
    code = home.run("goal", "set", answers=["g_staff", "personal", "long", "Reach staff"])
    assert code == EXIT_OK
    assert home.asked == [
        "goal id, e.g. g_payments_latency: ",
        "domain (project, team or personal): ",
        "horizon (short, medium or long): ",
        "title: ",
    ]
    assert parse_goals(home.raw(), scope=PERSONAL)["g_staff"].title == "Reach staff"


def test_set_at_a_terminal_asks_only_what_was_not_given(home):
    code = home.run("goal", "set", "g_new", "--domain", "team", answers=["short", "Hire two"])
    assert code == EXIT_OK
    assert home.asked == ["horizon (short, medium or long): ", "title: "]
    assert parse_goals(home.raw(), scope=PERSONAL)["g_new"].domain.value == "team"


def test_set_with_an_id_at_a_terminal_does_not_ask_the_id(home):
    code = home.run("goal", "set", "g_new", answers=["team", "short", "Hire two"])
    assert code == EXIT_OK
    assert not any(prompt.startswith("goal id") for prompt in home.asked)
    assert len(home.asked) == 3


def test_a_vocabulary_refusal_is_declare_goals_sentence(home, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    code = home.run(
        "goal", "set", "g_new", "--domain", "team", "--horizon", "soonish", "--title", "x",
        tty=False,
    )
    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "'soonish' is not a goal horizon" in err
    for horizon in ("`short`", "`medium`", "`long`"):
        assert horizon in err
    _unchanged(home, TWO_PROJECT_ONE_TEAM)


def test_a_title_that_begins_with_two_dashes_is_given_with_equals(home):
    code = home.run(
        "goal", "set", "g_new", "--domain", "team", "--horizon", "short", "--title=--fast",
        tty=False,
    )
    assert code == EXIT_OK
    assert parse_goals(home.raw(), scope=PERSONAL)["g_new"].title == "--fast"


# ── Update ───────────────────────────────────────────────────────────────────


def test_update_changes_only_what_is_given_and_asks_nothing(home):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "update", "g_a", "--title", "Under 150ms") == EXIT_OK
    assert home.asked == []
    goal = parse_goals(home.raw(), scope=PERSONAL)["g_a"]
    assert (goal.title, goal.domain.value, goal.horizon.value) == ("Under 150ms", "project", "medium")
    assert len(home.entries(SelfActionType.GOAL_SET)) == 1


def test_update_at_a_terminal_without_options_asks_all_three_with_defaults(home):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "update", "g_a", answers=["", "long", ""]) == EXIT_OK
    assert "[project]" in home.asked[0] and "[Cut latency]" in home.asked[2]
    assert parse_goals(home.raw(), scope=PERSONAL)["g_a"].horizon.value == "long"


def test_update_with_no_options_and_no_terminal_names_the_three(home, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "update", "g_a", tty=False) == EXIT_REFUSAL
    err = capsys.readouterr().err
    for option in ("--domain", "--horizon", "--title"):
        assert option in err
    _unchanged(home, TWO_PROJECT_ONE_TEAM)


def test_update_of_an_unknown_id_names_goal_set_and_writes_nothing(home, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "update", "g_typo", "--title", "x", tty=False) == EXIT_REFUSAL
    assert "`pm-ai goal set g_typo`" in capsys.readouterr().err
    _unchanged(home, TWO_PROJECT_ONE_TEAM)


def test_update_with_no_change_says_unchanged_and_records_nothing(home, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    code = home.run("goal", "update", "g_a", "--horizon", "medium", "--title", "Cut latency", tty=False)
    assert code == EXIT_OK
    assert "unchanged" in capsys.readouterr().out
    _unchanged(home, TWO_PROJECT_ONE_TEAM)


def test_update_that_moves_the_domain_moves_the_detail_lines_too(home):
    home.write(
        b"## Project\n\n- [g_a] (medium) Cut latency\n  - measured weekly\n\n"
        b"## Personal\n\n- [g_me] (long) Grow\n"
    )
    assert home.run("goal", "update", "g_a", "--domain", "personal", tty=False) == EXIT_OK
    text = home.raw().decode()
    personal = text.index("## Personal")
    assert text.index("- [g_a] (medium) Cut latency") > personal
    assert text.index("  - measured weekly") > text.index("- [g_a]")
    assert parse_goals(home.raw(), scope=PERSONAL)["g_a"].domain.value == "personal"


def test_acceptance_update_the_horizon_then_show_prints_it(home, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "update", "g_a", "--horizon", "long", tty=False) == EXIT_OK
    goal = parse_goals(home.raw(), scope=PERSONAL)["g_a"]
    assert (goal.title, goal.domain.value) == ("Cut latency", "project")
    capsys.readouterr()
    assert home.run("goal", "show", "g_a", tty=False) == EXIT_OK
    assert "long" in capsys.readouterr().out


# ── Delete ───────────────────────────────────────────────────────────────────


def test_delete_at_a_terminal_shows_the_lines_then_removes_them(home, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "delete", "g_a", answers=[" Yes "]) == EXIT_OK
    out = capsys.readouterr().out
    assert "  - [g_a] (medium) Cut latency" in out
    assert out.index("- [g_a]") < out.index("is deleted")
    assert "next render" in out
    assert "g_a" not in parse_goals(home.raw(), scope=PERSONAL)
    (recorded,) = home.entries(SelfActionType.GOAL_DELETED)
    fields = dict(recorded.fields)
    assert (fields["goal_id"], fields["domain"], fields["horizon"], fields["title"], fields["channel"]) == (
        "g_a", "project", "medium", "Cut latency", "cli",
    )


@pytest.mark.parametrize(
    "answer", ["n", "", "nope", EOFError(), KeyboardInterrupt()], ids=["n", "empty", "nope", "eof", "ctrl-c"]
)
def test_delete_answered_anything_but_yes_writes_nothing(home, capsys, answer):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "delete", "g_a", answers=[answer]) == EXIT_REFUSAL
    # After Ctrl-C or end of input the cursor is still on the prompt's line,
    # so a newline is printed first and the refusal lands on a line of its own.
    out = capsys.readouterr().out
    assert out.endswith("\n\n") is isinstance(answer, BaseException)
    _unchanged(home, TWO_PROJECT_ONE_TEAM)


def test_a_block_changed_while_asking_refuses_the_delete(home, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    edited = TWO_PROJECT_ONE_TEAM.replace(b"Cut latency", b"Cut latency now")
    code = home.run("goal", "delete", "g_a", answers=["y"], before_last=lambda: home.write(edited))
    assert code == EXIT_REFUSAL
    assert "changed after they were shown" in capsys.readouterr().err
    _unchanged(home, edited)


def test_delete_for_a_script_with_yes_asks_nothing(home):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "delete", "g_a", "--yes", tty=False) == EXIT_OK
    assert home.asked == []
    assert "g_a" not in parse_goals(home.raw(), scope=PERSONAL)


def test_the_flag_may_come_before_the_id(home):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "delete", "--yes", "g_a", tty=False) == EXIT_OK
    assert "g_a" not in parse_goals(home.raw(), scope=PERSONAL)


def test_delete_without_a_terminal_or_yes_names_yes(home, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "delete", "g_a", tty=False) == EXIT_REFUSAL
    assert "`--yes`" in capsys.readouterr().err
    _unchanged(home, TWO_PROJECT_ONE_TEAM)


def test_delete_keeps_every_other_byte(home):
    before = (
        b"\xef\xbb\xbf# Goals\r\n\r\nSome prose.\r\n\r\n## Project\r\n\r\n"
        b"1. [g_keep] (short) Keep me\r\n"
        b"2. [g_a] (medium) Cut latency\r\n"
        b"   - measured weekly\r\n"
        b"\r\n"
        b"   - owned by Dana\r\n"
        b"\r\n"
        b"More prose.\r\n"
    )
    home.write(before)
    assert home.run("goal", "delete", "g_a", "--yes", tty=False) == EXIT_OK
    assert home.raw() == (
        b"\xef\xbb\xbf# Goals\r\n\r\nSome prose.\r\n\r\n## Project\r\n\r\n"
        b"1. [g_keep] (short) Keep me\r\n"
        b"\r\n"
        b"More prose.\r\n"
    )


def test_a_goal_nested_in_the_block_refuses_the_delete_by_name(home, capsys):
    raw = b"## Project\n\n- [g_a] (medium) Cut latency\n  - [g_sub] (short) Nested\n"
    home.write(raw)
    assert home.run("goal", "delete", "g_a", "--yes", tty=False) == EXIT_REFUSAL
    assert "`g_sub`" in capsys.readouterr().err
    _unchanged(home, raw)


def test_a_long_title_is_cut_in_the_entry_and_the_goal_deleted(home):
    title = "x" * (MAX_ENTRY_LENGTH + 10)
    home.write(f"## Project\n\n- [g_a] (short) {title}\n".encode())
    assert home.run("goal", "delete", "g_a", "--yes", tty=False) == EXIT_OK
    assert parse_goals(home.raw(), scope=PERSONAL) == {}
    (recorded,) = home.entries(SelfActionType.GOAL_DELETED)
    cut = dict(recorded.fields)["title"]
    assert cut.endswith("…") and cut[:-1] == title[: len(cut) - 1]
    assert MAX_ENTRY_LENGTH // 2 < len(cut) < len(title)
    # Maximal: the line as written fits, and one more character of the title
    # would not have.
    assert len(render_entry(recorded)) <= MAX_ENTRY_LENGTH
    longer = title[: len(cut)] + "…"
    widened = dataclasses.replace(
        recorded,
        fields=tuple(
            (name, longer if name == "title" else value) for name, value in recorded.fields
        ),
    )
    with pytest.raises(MalformedEntry):
        render_entry(widened)


def test_an_id_too_long_to_record_refuses_the_delete_by_its_own_sentence(home, capsys):
    goal_id = "g" * MAX_ENTRY_LENGTH
    raw = f"## Project\n\n- [{goal_id}] (short) A\n".encode()
    home.write(raw)
    assert home.run("goal", "delete", goal_id, "--yes", tty=False) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "too long to record in the event log" in err and "by hand" in err
    _unchanged(home, raw)


def test_delete_of_an_unknown_id_names_goal_list(home, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "delete", "g_none", "--yes", tty=False) == EXIT_REFUSAL
    assert "`pm-ai goal list`" in capsys.readouterr().err
    _unchanged(home, TWO_PROJECT_ONE_TEAM)


@pytest.mark.parametrize("flags", [["--yes"], []], ids=["yes", "at-a-terminal"])
def test_delete_with_no_file_names_goal_list_and_creates_nothing(home, capsys, flags):
    assert home.run("goal", "delete", "g_a", *flags) == EXIT_REFUSAL
    assert "`pm-ai goal list`" in capsys.readouterr().err
    _unchanged(home, None)


def test_a_failed_append_after_the_delete_says_the_file_changed(home, monkeypatch, capsys):
    home.write(TWO_PROJECT_ONE_TEAM)

    def full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(StorageService, "append_event_log", full)
    assert home.run("goal", "delete", "g_a", "--yes", tty=False) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "g_a WAS deleted" in err and "goal_deleted entry" in err
    assert "g_a" not in parse_goals(home.raw(), scope=PERSONAL)


@pytest.mark.parametrize("argv", [["--yes=1"], ["--yes", "--yes"]], ids=["valued", "twice"])
def test_a_misused_flag_is_a_usage_error(home, capsys, argv):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "delete", "g_a", *argv, tty=False) == EXIT_USAGE
    _unchanged(home, TWO_PROJECT_ONE_TEAM)


def test_acceptance_a_scripted_delete_keeps_the_other_goal_byte_for_byte(home):
    home.write(TWO_PROJECT_ONE_TEAM)
    assert home.run("goal", "delete", "g_b", "--yes", tty=False) == EXIT_OK
    assert home.raw() == TWO_PROJECT_ONE_TEAM.replace(b"- [g_b] (long) Second project goal\n", b"")
    last = home.entries()[-1]
    assert last.category is SelfActionType.GOAL_DELETED
    fields = dict(last.fields)
    assert (fields["goal_id"], fields["domain"], fields["horizon"], fields["title"]) == (
        "g_b", "project", "long", "Second project goal",
    )


# ── Help, usage, the prompt ──────────────────────────────────────────────────


def test_subcommand_help_prints_its_usage_line_and_summary(home, capsys):
    assert home.run("goal", "delete", "--help", tty=False) == EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("usage: pm-ai goal delete <id> [--yes]")
    assert "asking first" in out


def test_goal_create_is_the_goal_usage_listing_the_five(home, capsys):
    assert home.run("goal", "create", tty=False) == EXIT_USAGE
    err = capsys.readouterr().err
    for leaf in ("set", "update", "delete", "list", "show"):
        assert f"  {leaf}" in err


def test_at_the_prompt_list_goes_to_the_daemon_and_the_writers_stay_here():
    assert not repl.runs_here(["goal", "list"])
    assert not repl.runs_here(["goal", "show", "g_a"])
    for words in (["goal", "delete", "g_a"], ["goal", "update", "g_a", "--title", "x"], ["goal", "set"]):
        assert repl.runs_here(words)


def test_the_daemon_answers_goal_list_inline_with_the_command_lines_output(rig, capsys):  # noqa: F811
    path = rig.daemon.storage.paths.resolve(PERSONAL, GOALS_ARTIFACT, create=True)
    path.write_bytes(TWO_PROJECT_ONE_TEAM)
    reply = rig.ask("goal list")
    assert reply.body["inline"] is True and reply.body["exit"] == EXIT_OK
    fresh = refreshed(rig.daemon, store_for=rig.path._store_for)  # noqa: SLF001
    assert (
        dispatch(
            ["goal", "list"],
            daemon=fresh,
            diagnose=lambda: Report(()),
            probe_connectors=None,
            goal_reader=GoalReader(fresh.storage),
        )
        == EXIT_OK
    )
    assert reply.body["output"] == capsys.readouterr().out
    # Read afresh for every line (AD-7): a hand edit shows at the next one.
    path.write_bytes(TWO_PROJECT_ONE_TEAM.replace(b"Hire two", b"Hire three"))
    assert "Hire three" in rig.ask("goal list").body["output"]


@pytest.mark.parametrize("line", ["goal delete g_a --yes", "goal update g_a --title x"])
def test_the_daemon_refuses_a_goal_write_as_needing_a_terminal(rig, line):  # noqa: F811
    path = rig.daemon.storage.paths.resolve(PERSONAL, GOALS_ARTIFACT, create=True)
    path.write_bytes(TWO_PROJECT_ONE_TEAM)
    reply = rig.ask(line)
    assert reply.body["exit"] == EXIT_REFUSAL
    assert "needs a terminal" in reply.body["output"]
    assert path.read_bytes() == TWO_PROJECT_ONE_TEAM
    logged = EventLog(rig.daemon.storage).read(scope=PERSONAL)
    assert [
        item
        for item in logged
        if item.category in {SelfActionType.GOAL_SET, SelfActionType.GOAL_DELETED}
    ] == []
