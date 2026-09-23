"""Story 22b — `pm-ai goal set`, end to end against a real temporary root.

Spec: `_bmad-output/specs/spec-pm-ai/stories/22b-goal-writer.md`.

The rows here are the ones only a real root can show: that the file lands where
the personal tree declares it, that a hand-edited file on disk survives a set
line for line, that a refused file is byte-identical *on disk* afterwards, and
that every set leaves exactly one `goal_set` entry in the personal event log —
two for two, because an `ObservedEventType` would have deduped the second away.
What the renderer produces is proven in `tests/core/test_goal_render.py`.
"""

from __future__ import annotations

import difflib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pm_ai.app import entry
from pm_ai.app.entry import GoalBook
from pm_ai.app.wiring import Bootstrap, bootstrap, build
from pm_ai.core.config import Config
from pm_ai.core.event_log import EventLog
from pm_ai.core.goal_register import ARTIFACT as GOALS_ARTIFACT
from pm_ai.core.goal_register import (
    GOALS_SCOPE,
    HEADER,
    GoalUnrecorded,
    MalformedGoals,
    parse_goals,
)
from pm_ai.core.project_registry import ProjectEntry, render_registry
from pm_ai.domain.event_entries import MAX_ENTRY_LENGTH, SelfActionType
from pm_ai.domain.goals import Goal, GoalDomain, GoalHorizon
from pm_ai.domain.health import Report
from pm_ai.platform.doctor import ArtifactState
from pm_ai.ports import ArtifactBusy, KeyNotFound
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, EXIT_USAGE, dispatch

NOW = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
PERSONAL = GOALS_SCOPE

HAND_EDITED = b"""# My goals

<!-- reviewed with Dana -->

## Team

- [g_oncall] (tactical) Halve the on-call pages
  - measured weekly

Some prose the PM wrote.

## Project

- [g_latency] (short) Cut latency (p99) [see dashboard]

## Notes

- ask Dana
"""

BROKEN = b"""## Project

- [g_latency] (quarterly) A horizon nobody declared
"""


class _NoKey:
    """A keychain with nothing in it: setting a goal must not need the enclave."""

    def fetch(self, name: str) -> bytes:
        raise KeyNotFound(f"nothing is stored under {name!r}")

    def store(self, name: str, secret: bytes) -> None:
        raise AssertionError("setting a goal must never enrol a key")

    def store_if_absent(self, name: str, secret: bytes) -> None:
        raise AssertionError("setting a goal must never enrol a key")


def daemon(tmp_path: Path):
    return build(
        tmp_path,
        "alpha",
        now=lambda: NOW,
        keychain=_NoKey(),
        config=Config(pm_handle="andrei@example.com"),
    )


def book(built) -> GoalBook:
    return GoalBook(built.storage, channel="cli")


def goal(
    goal_id: str,
    title: str,
    domain: GoalDomain = GoalDomain.PROJECT,
    horizon: GoalHorizon = GoalHorizon.SHORT,
) -> Goal:
    return Goal(goal_id=goal_id, title=title, domain=domain, horizon=horizon, scope=PERSONAL)


def on_disk(built) -> bytes | None:
    return built.storage.read_artifact(scope=PERSONAL, artifact=GOALS_ARTIFACT)


def goal_entries(built):
    return [
        entry
        for entry in EventLog(built.storage).read(scope=PERSONAL)
        if entry.category is SelfActionType.GOAL_SET
    ]


def terminal(monkeypatch, answers=(), *, tty=True, before_last=None):
    """A scripted terminal. `before_last` runs just before the final answer."""
    queue = list(answers)

    def scripted(prompt: str) -> str:
        if not queue:
            raise AssertionError(f"an unexpected prompt: {prompt!r}")
        if len(queue) == 1 and before_last is not None:
            before_last()
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", scripted)
    monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
    return queue


def run(built, argv, monkeypatch, *, answers=(), tty=True, before_last=None):
    """`dispatch` as `main` wires it, with a scripted terminal."""
    queue = terminal(monkeypatch, answers, tty=tty, before_last=before_last)
    code = dispatch(
        argv,
        daemon=built,
        diagnose=lambda: Report(()),
        probe_connectors=lambda: Report(()),
        goals=book(built),
    )
    assert not queue, f"answers left unasked: {queue}"
    return code


# ── The file ─────────────────────────────────────────────────────────────────


def test_a_first_goal_creates_the_file_where_the_personal_tree_declares_it(tmp_path):
    built = daemon(tmp_path)
    outcome = book(built).set(goal("g_latency", "Cut latency"))
    assert outcome.changed and not outcome.revised
    path = outcome.path
    assert path is not None
    assert path.name == GOALS_ARTIFACT
    assert path.parent.name == "memory"
    assert ".manager-ai" in str(path)
    raw = path.read_bytes()
    assert raw.decode("utf-8").startswith(HEADER)
    assert list(parse_goals(raw, scope=PERSONAL)) == ["g_latency"]


def test_a_set_on_a_hand_edited_file_keeps_every_unrelated_line_and_the_order(tmp_path):
    """Acceptance — diffed on disk, because a regenerating renderer passes a round trip."""
    built = daemon(tmp_path)
    built.storage.write_artifact(HAND_EDITED, scope=PERSONAL, artifact=GOALS_ARTIFACT)

    book(built).set(goal("g_errors", "Halve 5xx", GoalDomain.PROJECT))

    after = on_disk(built)
    assert after is not None
    diff = [
        line
        for line in difflib.ndiff(
            HAND_EDITED.decode().split("\n"), after.decode().split("\n")
        )
        if line[:1] in {"+", "-"}
    ]
    assert diff == ["+ - [g_errors] (short) Halve 5xx"]
    text = after.decode()
    assert text.index("## Team") < text.index("## Project") < text.index("## Notes")


def test_a_malformed_file_is_refused_and_left_byte_identical(tmp_path):
    """Acceptance — `22a` refuses it, and the writer does not get to overwrite it."""
    built = daemon(tmp_path)
    built.storage.write_artifact(BROKEN, scope=PERSONAL, artifact=GOALS_ARTIFACT)

    with pytest.raises(MalformedGoals, match="quarterly"):
        book(built).set(goal("g_new", "Anything"))

    assert on_disk(built) == BROKEN
    assert goal_entries(built) == []


# ── The event log ────────────────────────────────────────────────────────────


def test_one_set_appends_exactly_one_goal_set_entry(tmp_path):
    built = daemon(tmp_path)
    book(built).set(goal("g_latency", "Cut latency [p99] | (now)"))

    (entry,) = goal_entries(built)
    fields = dict(entry.fields)
    assert fields["goal_id"] == "g_latency"
    assert fields["domain"] == "project"
    assert fields["horizon"] == "short"
    assert fields["title"] == "Cut latency [p99] | (now)"
    assert fields["channel"] == "cli"
    assert entry.entry_id


def test_a_second_revision_of_one_goal_adds_a_second_entry(tmp_path):
    """Acceptance — two, because an `ObservedEventType` would have deduped one away."""
    built = daemon(tmp_path)
    book(built).set(goal("g_latency", "Cut latency"))
    book(built).set(goal("g_latency", "Cut latency further", horizon=GoalHorizon.MEDIUM))

    entries = goal_entries(built)
    assert len(entries) == 2
    assert entries[0].entry_id != entries[1].entry_id
    assert [dict(e.fields)["title"] for e in entries] == ["Cut latency", "Cut latency further"]
    assert parse_goals(on_disk(built), scope=PERSONAL)["g_latency"].horizon is GoalHorizon.MEDIUM


# ── The command ──────────────────────────────────────────────────────────────


def test_goal_set_at_a_terminal_writes_the_goal_and_exits_zero(tmp_path, monkeypatch, capsys):
    built = daemon(tmp_path)
    code = run(
        built,
        ["goal", "set"],
        monkeypatch,
        answers=["g_staff", "Personal", "strategic", "Reach staff engineer"],
    )
    assert code == EXIT_OK
    register = parse_goals(on_disk(built), scope=PERSONAL)
    assert register["g_staff"] == goal(
        "g_staff", "Reach staff engineer", GoalDomain.PERSONAL, GoalHorizon.LONG
    )
    assert "g_staff is set" in capsys.readouterr().out


def test_a_revision_offers_the_current_values_and_empty_keeps_them(
    tmp_path, monkeypatch, capsys
):
    built = daemon(tmp_path)
    book(built).set(goal("g_staff", "Reach staff", GoalDomain.PERSONAL, GoalHorizon.LONG))

    code = run(built, ["goal", "set"], monkeypatch, answers=["g_staff", "", "medium", ""])

    assert code == EXIT_OK
    assert "g_staff is revised" in capsys.readouterr().out
    assert parse_goals(on_disk(built), scope=PERSONAL)["g_staff"] == goal(
        "g_staff", "Reach staff", GoalDomain.PERSONAL, GoalHorizon.MEDIUM
    )
    assert len(goal_entries(built)) == 2


def test_a_non_interactive_invocation_is_refused_without_prompting(tmp_path, monkeypatch, capsys):
    built = daemon(tmp_path)
    code = run(built, ["goal", "set"], monkeypatch, tty=False)
    assert code == EXIT_REFUSAL
    assert "TTY" in capsys.readouterr().err
    assert on_disk(built) is None
    assert goal_entries(built) == []


@pytest.mark.parametrize(
    ("answers", "named"),
    [
        (["my goal", "project", "short", "T"], "letters, digits"),
        (["g_a", "projetc", "short", "T"], "not a goal domain"),
        (["g_a", "project", "soon", "T"], "not a goal horizon"),
    ],
)
def test_an_inadmissible_answer_is_refused_and_nothing_is_written(
    tmp_path, monkeypatch, capsys, answers, named
):
    built = daemon(tmp_path)
    code = run(built, ["goal", "set"], monkeypatch, answers=answers)
    assert code == EXIT_REFUSAL
    assert named in capsys.readouterr().err
    assert on_disk(built) is None
    assert goal_entries(built) == []


def test_a_malformed_file_is_refused_before_the_first_prompt(tmp_path, monkeypatch, capsys):
    built = daemon(tmp_path)
    built.storage.write_artifact(BROKEN, scope=PERSONAL, artifact=GOALS_ARTIFACT)
    code = run(built, ["goal", "set"], monkeypatch, answers=())
    assert code == EXIT_REFUSAL
    assert "quarterly" in capsys.readouterr().err
    assert on_disk(built) == BROKEN


def test_goal_set_takes_no_arguments(tmp_path, monkeypatch):
    built = daemon(tmp_path)
    assert run(built, ["goal", "set", "g_a"], monkeypatch) == EXIT_USAGE


# ── Review patches: nothing refused after the write, and no false sentences ──


def test_an_unchanged_revision_writes_nothing_records_nothing_and_says_so(
    tmp_path, monkeypatch, capsys
):
    built = daemon(tmp_path)
    book(built).set(goal("g_staff", "Reach staff", GoalDomain.PERSONAL, GoalHorizon.LONG))
    before = on_disk(built)

    def refuse(*args, **kwargs):
        raise AssertionError("an unchanged goal must not be written")

    monkeypatch.setattr(built.storage, "write_artifact", refuse)
    code = run(built, ["goal", "set"], monkeypatch, answers=["g_staff", "", "", ""])

    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "g_staff is unchanged" in out
    assert "revised" not in out
    assert on_disk(built) == before
    assert len(goal_entries(built)) == 1


def test_the_verb_comes_from_the_read_the_set_merged_onto(tmp_path, monkeypatch, capsys):
    """A goal set by someone else while the prompts were open is a revision."""
    built = daemon(tmp_path)
    code = run(
        built,
        ["goal", "set"],
        monkeypatch,
        answers=["g_a", "project", "short", "Mine"],
        before_last=lambda: book(built).set(goal("g_a", "Theirs")),
    )
    assert code == EXIT_OK
    assert "g_a is revised" in capsys.readouterr().out
    assert parse_goals(on_disk(built), scope=PERSONAL)["g_a"].title == "Mine"


def test_a_title_too_long_for_one_ledger_line_is_refused_before_the_write(tmp_path):
    built = daemon(tmp_path)
    long_title = "x" * MAX_ENTRY_LENGTH
    with pytest.raises(MalformedGoals, match="nothing was written"):
        book(built).set(goal("g_a", long_title))
    assert on_disk(built) is None
    assert goal_entries(built) == []


def test_a_failed_append_after_the_write_says_the_file_was_written(
    tmp_path, monkeypatch, capsys
):
    built = daemon(tmp_path)

    def full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(built.storage, "append_event_log", full)
    with pytest.raises(GoalUnrecorded, match="WAS written"):
        book(built).set(goal("g_a", "First"))
    assert "g_a" in parse_goals(on_disk(built), scope=PERSONAL)

    code = run(built, ["goal", "set"], monkeypatch, answers=["g_b", "team", "long", "Second"])
    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "g_b WAS written" in err
    assert "could not be written, so nothing changed" not in err
    assert "g_b" in parse_goals(on_disk(built), scope=PERSONAL)


@pytest.mark.parametrize(
    "failure",
    [ArtifactBusy("strategic_goals.md is claimed by another writer"), OSError(13, "Permission denied")],
    ids=["busy", "oserror"],
)
def test_a_write_that_fails_is_refused_and_records_nothing(
    tmp_path, monkeypatch, capsys, failure
):
    built = daemon(tmp_path)

    def fails(*args, **kwargs):
        raise failure

    monkeypatch.setattr(built.storage, "write_artifact", fails)
    code = run(built, ["goal", "set"], monkeypatch, answers=["g_a", "project", "short", "T"])

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "strategic_goals.md could not be written, so nothing changed" in err
    assert str(failure) in err
    assert on_disk(built) is None
    assert goal_entries(built) == []


def test_main_wires_goal_set_end_to_end(tmp_path, monkeypatch):
    """`entry.main` passes `goals=`; without it the command refuses as a wiring fault."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repository = tmp_path / "repo"
    repository.mkdir()
    entries = {"alpha": ProjectEntry(path=repository)}
    registry = ArtifactState.read(render_registry(entries))
    monkeypatch.setattr(
        entry,
        "_bootstrap",
        lambda keychain: Bootstrap(entries, registry, bootstrap(keychain).config),
    )
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", _NoKey)
    terminal(monkeypatch, ["g_staff", "personal", "long", "Reach staff engineer"])

    assert entry.main(["goal", "set"]) == EXIT_OK

    written = home / ".manager-ai" / "memory" / GOALS_ARTIFACT
    assert written.exists(), sorted(str(p) for p in home.rglob("*"))
    register = parse_goals(written.read_bytes(), scope=PERSONAL)
    assert register["g_staff"].title == "Reach staff engineer"
