"""Story 4s — `pm-ai connector categories <instance>`, one test per matrix row.

Every row runs `entry.main` against a real temporary home: the `connectors/`
row, the claim enrolment takes before writing one and the registry read are
the real ones. The keychain is stood in (a key enrolled), the registry is the
composition root's bootstrap read, stood in so a test can change it while the
questions are answered, and nothing talks to Microsoft — the command never does.

Where nothing is written, the row is compared byte for byte with what it was.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pm_ai.app import entry
from pm_ai.app.pipelines import run_harvest
from pm_ai.app.wiring import (
    GraphEnrolment,
    SealedRefreshTokenStore,
    bootstrap,
    build,
    refreshed,
)
from pm_ai.core.meeting_records import MEETINGS
from pm_ai.core.project_registry import ProjectEntry
from pm_ai.domain.health import ArtifactState
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.surfaces.cli import dispatch as cli
from pm_ai.surfaces.cli import repl
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, TERMINAL_BOUND
from tests.slice.test_connector_add_graph import VALID, Keychain, Microsoft, _microsoft

INSTANCE = "graph:work"
USER = "6f1c2b3a-0000-4000-8000-000000000001"
APPLICATION = DataScope(ScopeKind.APPLICATION)


class Terminal:
    """`input` from a script, recording every prompt; running out is Ctrl-D.

    An exception is raised at that prompt; a callable is run (something
    changing the machine while the person is answering) and the next item is
    the answer.
    """

    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        while self.answers and callable(self.answers[0]) and not isinstance(
            self.answers[0], BaseException
        ):
            self.answers.pop(0)()
        if not self.answers:
            raise EOFError
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


@dataclasses.dataclass
class Machine:
    home: Path
    keychain: Keychain
    projects: list[str]
    """The registry's ids, in its order — read afresh at every bootstrap."""
    root: Path

    @property
    def connectors(self) -> Path:
        return self.home / ".pm-ai" / "connectors"

    def row_path(self, instance: str = INSTANCE) -> Path:
        return self.connectors / f"{instance}.json"

    def write_row(self, instance: str = INSTANCE, **row: object) -> Path:
        """A row as `connector add graph` writes one, plus `row`'s keys."""
        self.connectors.mkdir(parents=True, exist_ok=True)
        document = {
            "instance": instance,
            "system": "graph",
            "enabled": True,
            "project": instance.split(":", 1)[1],
            "client_id": "00000000-0000-0000-0000-00000000004s",
            "tenant": "organizations",
            "window_width_minutes": 1440,
            "first_run_reach_back_minutes": 10080,
            "user_id": USER,
            **row,
        }
        path = self.row_path(instance)
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
        return path

    def row(self, instance: str = INSTANCE) -> dict:
        return json.loads(self.row_path(instance).read_text())


@pytest.fixture
def machine(tmp_path, monkeypatch) -> Machine:
    """`alpha` enrolled, a master key, a terminal, an empty home."""
    keychain = Keychain()
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: keychain)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    held = Machine(home, keychain, ["alpha"], tmp_path)

    def read(chain):
        entries = {}
        lines = []
        for project in held.projects:
            repository = tmp_path / f"repo-{project}"
            repository.mkdir(exist_ok=True)
            entries[project] = ProjectEntry(path=repository)
            lines.append(f'[projects.{project}]\npath = "{repository}"\n')
        state = ArtifactState.read("\n".join(lines).encode()) if entries else ArtifactState.absent()
        return dataclasses.replace(bootstrap(chain), projects=entries, registry=state)

    monkeypatch.setattr(entry, "_bootstrap", read)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # The claim's wait, without its sleeps.
    monkeypatch.setattr(
        entry, "GraphEnrolment", functools.partial(GraphEnrolment, claim_sleep=lambda _s: None)
    )
    return held


def _categories(monkeypatch, answers: list, instance: str = INSTANCE) -> tuple[int, Terminal]:
    terminal = Terminal(answers)
    monkeypatch.setattr("builtins.input", terminal)
    return entry.main(["connector", "categories", instance]), terminal


def _asked(terminal: Terminal) -> list[str]:
    return [p for p in terminal.prompts if p.startswith("Outlook category")]


def _clause(*projects: str) -> str:
    which = projects[0] if len(projects) == 1 else "one of them"
    listed = projects[0] if len(projects) == 1 else " and ".join(projects)
    return (
        cli.PROJECT_CLAUSE.format(instance=INSTANCE, projects=listed, which=which)
        + " "
        + cli.RESTART_SENTENCE
    )


# ── Changing the mapping ─────────────────────────────────────────────────────


def test_a_first_mapping_is_saved_and_every_other_key_is_kept(machine, monkeypatch, capsys):
    machine.write_row(note="a key pm-ai does not know")
    before = machine.row()

    code, terminal = _categories(monkeypatch, ["Alpha"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "none — every meeting is filed as personal" in captured.out
    assert _asked(terminal) == ["Outlook category that marks alpha's meetings [Enter for none]: "]
    after = machine.row()
    assert after.pop("categories") == {"Alpha": "alpha"}
    assert after == before, "only the categories key changed"
    assert "'Alpha' into alpha" in captured.out
    assert _clause("alpha") in captured.out


@pytest.mark.parametrize("typed", ["", "ALPHA", " alpha "], ids=["enter", "upper", "padded"])
def test_keeping_the_category_writes_nothing(machine, monkeypatch, capsys, typed):
    path = machine.write_row(categories={"Alpha": "alpha"})
    before = path.read_bytes()

    code, terminal = _categories(monkeypatch, [typed])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "  'Alpha' → alpha" in captured.out
    assert _asked(terminal) == [
        "Outlook category that marks alpha's meetings [Enter keeps 'Alpha'; - removes it]: "
    ]
    assert "Nothing changed, so nothing was written" in captured.out
    assert path.read_bytes() == before
    assert cli.RESTART_SENTENCE not in captured.out


def test_a_typed_category_replaces_the_current_one(machine, monkeypatch, capsys):
    machine.write_row(categories={"Alpha": "alpha"})

    code, _ = _categories(monkeypatch, ["Platform"])

    assert code == EXIT_OK, capsys.readouterr().err
    assert machine.row()["categories"] == {"Platform": "alpha"}


def test_two_projects_may_swap_categories_in_one_run(machine, monkeypatch, capsys):
    machine.projects[:] = ["alpha", "beta"]
    machine.write_row(categories={"A": "alpha", "B": "beta"})

    code, _ = _categories(monkeypatch, ["B", "A"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert machine.row()["categories"] == {"B": "alpha", "A": "beta"}
    assert _clause("alpha", "beta") in captured.out


def test_a_dash_removes_the_only_entry_and_the_key(machine, monkeypatch, capsys):
    machine.write_row(categories={"Alpha": "alpha"})

    code, _ = _categories(monkeypatch, ["-"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "categories" not in machine.row()
    assert "every meeting is filed as personal" in captured.out
    assert cli.RESTART_SENTENCE not in captured.out, "the mapping names no project"


def test_a_dash_where_nothing_can_be_removed_is_refused_and_asked_again(
    machine, monkeypatch, capsys
):
    path = machine.write_row()
    before = path.read_bytes()

    code, terminal = _categories(monkeypatch, ["-", ""])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(_asked(terminal)) == 2
    assert "there is nothing to remove; press Enter for none" in captured.err
    assert path.read_bytes() == before


def test_an_entry_for_a_project_no_longer_enrolled_is_shown_dropped_and_written_without(
    machine, monkeypatch, capsys
):
    machine.write_row(categories={"Old": "gone", "Alpha": "alpha"})

    code, terminal = _categories(monkeypatch, [""])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "'Old' → gone: gone is no longer enrolled" in captured.out
    assert "dropped when the answers are saved" in captured.out
    assert len(_asked(terminal)) == 1 and "gone" not in _asked(terminal)[0]
    assert machine.row()["categories"] == {"Alpha": "alpha"}


def test_a_stale_entry_is_left_out_of_the_same_category_check(machine, monkeypatch, capsys):
    machine.write_row(categories={"Old": "gone"})

    code, _ = _categories(monkeypatch, ["old"])

    assert code == EXIT_OK, capsys.readouterr().err
    assert machine.row()["categories"] == {"old": "alpha"}


def test_several_categories_mapped_by_hand_are_kept_together(machine, monkeypatch, capsys):
    path = machine.write_row(categories={"A": "alpha", "B": "alpha"})
    before = path.read_bytes()

    code, terminal = _categories(monkeypatch, [""])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert _asked(terminal) == [
        "Outlook category that marks alpha's meetings [Enter keeps 'A', 'B'; - removes them]: "
    ]
    assert path.read_bytes() == before


def test_several_categories_are_replaced_together_by_one_typed(machine, monkeypatch, capsys):
    machine.write_row(categories={"A": "alpha", "B": "alpha"})

    code, _ = _categories(monkeypatch, ["C"])

    assert code == EXIT_OK, capsys.readouterr().err
    assert machine.row()["categories"] == {"C": "alpha"}


def test_one_category_for_two_projects_is_refused_and_the_later_asked_again(
    machine, monkeypatch, capsys
):
    machine.projects[:] = ["alpha", "beta"]
    machine.write_row(categories={"A": "alpha", "B": "beta"})

    code, terminal = _categories(monkeypatch, ["", "a", ""])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(_asked(terminal)) == 3
    assert "'a' for beta is the same Outlook category as 'A' for alpha" in captured.err
    assert machine.row()["categories"] == {"A": "alpha", "B": "beta"}


@pytest.mark.parametrize(
    "written",
    [["Alpha"], {"": "alpha"}, "Alpha", {"A": "alpha", "a": "beta"}],
    ids=["a list", "a blank category", "text", "one tag, two projects"],
)
def test_a_mapping_the_reader_refuses_is_shown_as_written_and_replaced(
    machine, monkeypatch, capsys, written
):
    machine.projects[:] = ["alpha", "beta"]
    machine.write_row(categories=written)

    code, terminal = _categories(monkeypatch, ["Alpha", ""])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert f"written as {json.dumps(written, ensure_ascii=False)}" in captured.out
    assert "The answers below replace it" in captured.out
    assert "- removes" not in _asked(terminal)[0], "nothing current is offered"
    assert machine.row()["categories"] == {"Alpha": "alpha"}


def test_an_unreadable_mapping_answered_with_enter_everywhere_is_removed(
    machine, monkeypatch, capsys
):
    machine.write_row(categories=["Alpha"])

    code, _ = _categories(monkeypatch, [""])

    assert code == EXIT_OK, capsys.readouterr().err
    assert "categories" not in machine.row()


def test_a_disabled_row_is_asked_and_saved_as_an_enabled_one(machine, monkeypatch, capsys):
    machine.write_row(enabled=False)

    code, _ = _categories(monkeypatch, ["Alpha"])

    assert code == EXIT_OK, capsys.readouterr().err
    row = machine.row()
    assert row["categories"] == {"Alpha": "alpha"} and row["enabled"] is False


def test_with_no_project_enrolled_nothing_is_asked_or_written(machine, monkeypatch, capsys):
    machine.projects.clear()
    path = machine.write_row(categories={"Old": "gone"})
    before = path.read_bytes()

    code, terminal = _categories(monkeypatch, ["Alpha"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "'Old' → gone" in captured.out, "the mapping is shown"
    assert "nothing to map" in captured.out
    assert terminal.prompts == []
    assert path.read_bytes() == before


# ── Refusals ─────────────────────────────────────────────────────────────────


def _refused(capsys, code: int) -> str:
    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL, captured.out
    return captured.err


def test_a_row_that_is_not_graphs_is_refused_naming_its_system(machine, monkeypatch, capsys):
    path = machine.write_row("gitlab:alpha", system="gitlab")
    before = path.read_bytes()

    code, terminal = _categories(monkeypatch, ["Alpha"], instance="gitlab:alpha")

    err = _refused(capsys, code)
    assert "'gitlab' connector" in err and "gitlab:alpha" in err
    assert terminal.prompts == [] and path.read_bytes() == before


def test_no_row_for_the_instance_is_refused_naming_it(machine, monkeypatch, capsys):
    code, terminal = _categories(monkeypatch, ["Alpha"], instance="graph:none")

    err = _refused(capsys, code)
    assert "'graph:none'" in err and "Nothing was asked and nothing was saved" in err
    assert terminal.prompts == []
    assert not machine.row_path("graph:none").exists()


@pytest.mark.parametrize("body", ["[1, 2]\n", "{not json\n"], ids=["not an object", "not json"])
def test_a_row_file_that_is_not_a_json_object_is_refused_naming_it(
    machine, monkeypatch, capsys, body
):
    machine.connectors.mkdir(parents=True)
    path = machine.row_path()
    path.write_text(body)

    code, terminal = _categories(monkeypatch, ["Alpha"])

    err = _refused(capsys, code)
    assert f"{INSTANCE}.json" in err
    assert terminal.prompts == [] and path.read_text() == body


def test_an_unreadable_registry_is_refused(machine, monkeypatch, capsys):
    path = machine.write_row()
    before = path.read_bytes()
    real = entry._bootstrap
    calls: list[int] = []

    def read(chain):
        # Readable while the command composes; unreadable for the questions.
        calls.append(1)
        read = real(chain)
        if len(calls) == 1:
            return read
        return dataclasses.replace(read, registry=ArtifactState.unreadable("Permission denied"))

    monkeypatch.setattr(entry, "_bootstrap", read)

    code, terminal = _categories(monkeypatch, ["Alpha"])

    err = _refused(capsys, code)
    assert "projects.toml" in err and "Permission denied" in err
    assert terminal.prompts == [] and path.read_bytes() == before


def test_no_terminal_is_refused_before_anything_is_asked(machine, monkeypatch, capsys):
    path = machine.write_row()
    before = path.read_bytes()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code, terminal = _categories(monkeypatch, ["Alpha"])

    err = _refused(capsys, code)
    assert "needs a terminal" in err and "Nothing was asked and nothing was saved" in err
    assert terminal.prompts == [] and path.read_bytes() == before


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), EOFError()], ids=["ctrl-c", "ctrl-d"])
def test_an_interruption_at_a_question_saves_nothing(machine, monkeypatch, capsys, interruption):
    machine.projects[:] = ["alpha", "beta"]
    path = machine.write_row(categories={"A": "alpha"})
    before = path.read_bytes()

    code, _ = _categories(monkeypatch, ["B", interruption])

    err = _refused(capsys, code)
    assert "nothing was saved" in err
    assert path.read_bytes() == before


def test_categories_changed_while_answering_are_refused_and_kept(machine, monkeypatch, capsys):
    path = machine.write_row(categories={"A": "alpha"})

    def hand_edit() -> None:
        row = machine.row()
        row["categories"] = {"Edited": "alpha"}
        path.write_text(json.dumps(row))

    code, _ = _categories(monkeypatch, [hand_edit, "Mine"])

    err = _refused(capsys, code)
    assert "changed while the questions were answered" in err
    assert machine.row()["categories"] == {"Edited": "alpha"}, "the other edit is kept"


def test_a_second_run_that_saved_first_wins(machine, monkeypatch, capsys):
    machine.write_row()

    def another_run() -> None:
        row = machine.row()
        row["categories"] = {"Theirs": "alpha"}
        machine.row_path().write_text(json.dumps(row))

    code, _ = _categories(monkeypatch, [another_run, "Mine"])

    _refused(capsys, code)
    assert machine.row()["categories"] == {"Theirs": "alpha"}


def test_a_project_removed_while_answering_is_refused_naming_it(machine, monkeypatch, capsys):
    path = machine.write_row()
    before = path.read_bytes()

    code, _ = _categories(monkeypatch, [lambda: machine.projects.clear(), "Alpha"])

    err = _refused(capsys, code)
    assert "alpha is no longer enrolled" in err and "Nothing was written" in err
    assert path.read_bytes() == before


def test_the_row_removed_while_answering_is_refused(machine, monkeypatch, capsys):
    path = machine.write_row()

    code, _ = _categories(monkeypatch, [path.unlink, "Alpha"])

    err = _refused(capsys, code)
    assert f"{INSTANCE!r}" in err and "Nothing was written" in err
    assert not path.exists()


def test_a_row_lock_held_past_the_wait_is_refused_as_busy(machine, monkeypatch, capsys):
    path = machine.write_row()
    before = path.read_bytes()
    holder = build(machine.home, APPLICATION, keychain=machine.keychain).storage
    taken, release = threading.Event(), threading.Event()

    def hold() -> None:
        with holder.exclusive(scope=APPLICATION, artifact="config.json"):
            taken.set()
            release.wait(30)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert taken.wait(10)
        code, _ = _categories(monkeypatch, ["Alpha"])
    finally:
        release.set()
        thread.join()

    err = _refused(capsys, code)
    assert "busy" in err and "config.json" in err
    assert path.read_bytes() == before


def test_a_user_id_recorded_while_answering_is_kept(machine, monkeypatch, capsys):
    machine.write_row()
    signed_in = "6f1c2b3a-0000-4000-8000-000000000002"

    def sign_in_meanwhile() -> None:
        row = machine.row()
        row["user_id"] = signed_in
        machine.row_path().write_text(json.dumps(row))

    code, _ = _categories(monkeypatch, [sign_in_meanwhile, "Alpha"])

    assert code == EXIT_OK, capsys.readouterr().err
    row = machine.row()
    assert row["user_id"] == signed_in and row["categories"] == {"Alpha": "alpha"}


# ── Where it runs ────────────────────────────────────────────────────────────


def test_it_needs_its_own_terminal_so_the_daemon_refuses_it_and_the_prompt_runs_it():
    assert ("connector", "categories") in TERMINAL_BOUND
    assert repl.runs_here(["connector", "categories", INSTANCE])


def test_through_the_daemon_it_is_refused_by_name(tmp_path):
    from pm_ai.app import prompts

    daemon = build(tmp_path, APPLICATION, keychain=Keychain(), encryption_disabled=True)
    path = prompts.RequestPath(
        daemon=daemon, writer=None, pool=None, diagnose=lambda: None, bounds={}  # type: ignore[arg-type]
    )
    import asyncio

    reply = asyncio.run(path.prompt(f"connector categories {INSTANCE}"))
    assert reply.body["exit"] == EXIT_REFUSAL
    assert "`pm-ai connector categories` asks questions" in reply.body["output"]


def test_its_instance_completes_from_the_graph_rows():
    from pm_ai.surfaces.cli.completion import GRAPH_INSTANCES, source_of

    assert source_of(("connector", "categories"), "instance") == GRAPH_INSTANCES


# ── Acceptance ───────────────────────────────────────────────────────────────


def _calendar_row(event_id: str, start: datetime, categories: list[str]) -> dict:
    from tests.connectors.test_graph_transcripts import _calendar_row as row

    return row(event_id, start, categories=categories)


def _enrol_graph(machine: Machine, monkeypatch, capsys) -> None:
    _microsoft(monkeypatch, Microsoft())
    terminal = Terminal(VALID)
    monkeypatch.setattr("builtins.input", terminal)
    assert entry.main(["connector", "add", "graph", INSTANCE]) == EXIT_OK, capsys.readouterr().err
    monkeypatch.setattr(
        entry, "GraphEnrolment", functools.partial(GraphEnrolment, claim_sleep=lambda _s: None)
    )


def _fresh(daemon):
    return refreshed(
        daemon,
        store_for=lambda instance, system: SealedRefreshTokenStore(
            storage=daemon.storage, instance=instance, system=system
        ),
    )


def test_acceptance_a_running_daemon_files_a_tagged_meeting_by_the_new_mapping(
    machine, monkeypatch, capsys
):
    """A daemon that loaded `alpha` and harvests `graph:work`; the mapping is
    saved; the next harvest — which reads the row afresh, as every job does —
    files a meeting tagged `Alpha` into `alpha`, with no restart."""
    from tests.connectors.test_graph_transcripts import Auth, FakeGraph, _one_off

    _enrol_graph(machine, monkeypatch, capsys)
    now = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
    daemon = build(machine.home, "alpha", keychain=machine.keychain, now=lambda: now)
    assert "alpha" in daemon.watched

    code, _ = _categories(monkeypatch, ["Alpha"])
    assert code == EXIT_OK, capsys.readouterr().err

    fresh = _fresh(daemon)
    held = now - timedelta(hours=3)
    graph = FakeGraph()
    graph.calendar = [_calendar_row("AAMk-tagged", held, ["Alpha"])]
    _one_off(graph, "AAMk-tagged", start=held)
    client = fresh.connectors[INSTANCE].calendar.client
    client.transport, client.auth = graph, Auth()

    run_harvest(fresh, INSTANCE)

    assert fresh.storage.harvest_failure(INSTANCE) is None
    alpha = DataScope(ScopeKind.PROJECT, "alpha")
    filed = fresh.storage.list_collection(scope=alpha, artifact=MEETINGS)
    assert any("AAMk-tagged" in name for name in filed), filed
    personal = fresh.storage.list_collection(
        scope=DataScope(ScopeKind.PERSONAL), artifact=MEETINGS
    )
    assert not any("AAMk-tagged" in name for name in personal)


def test_acceptance_a_project_enrolled_after_the_start_waits_for_a_restart(
    machine, monkeypatch, capsys
):
    """Mapped to `beta`, enrolled after the daemon started: the output carried
    the project clause and the restart sentence, and the daemon names
    `graph:work` as not built until it is restarted."""
    _enrol_graph(machine, monkeypatch, capsys)
    daemon = build(machine.home, "alpha", keychain=machine.keychain, watched=("alpha",))
    machine.projects.append("beta")

    code, _ = _categories(monkeypatch, ["", "Beta"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    clause = cli.PROJECT_CLAUSE.format(instance=INSTANCE, projects="beta", which="beta")
    assert f"{clause} {cli.RESTART_SENTENCE}" in captured.out
    now = refreshed(
        daemon,
        store_for=lambda instance, system: SealedRefreshTokenStore(
            storage=daemon.storage, instance=instance, system=system
        ),
        enrolled_now=("alpha", "beta"),
    )
    assert INSTANCE not in now.connectors
    (unbuilt,) = [u for u in now.unbuilt if u.instance == INSTANCE]
    assert "beta" in unbuilt.reason and cli.RESTART_SENTENCE in unbuilt.reason


# ── Review round 1 ───────────────────────────────────────────────────────────


def test_a_save_that_finds_the_answers_already_there_says_nothing_changed(
    machine, monkeypatch, capsys
):
    """`save_categories` reads the row again under the claim; when that read
    already holds the answers, nothing is written and nothing is claimed."""
    path = machine.write_row()
    before = path.read_bytes()
    monkeypatch.setattr(GraphEnrolment, "save_categories", lambda self, *a, **k: False)

    code, _ = _categories(monkeypatch, ["Alpha"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "Nothing changed, so nothing was written" in captured.out
    assert "Saved." not in captured.out
    assert cli.RESTART_SENTENCE not in captured.out, "no project clause"
    assert path.read_bytes() == before


def test_a_kept_category_that_clashes_is_refused_with_another_or_dash_as_the_way_out(
    machine, monkeypatch, capsys
):
    machine.projects[:] = ["alpha", "beta"]
    machine.write_row(categories={"A": "alpha", "B": "beta"})

    code, terminal = _categories(monkeypatch, ["B", "", "A"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(_asked(terminal)) == 3, "beta's question was asked again"
    assert "'B' for beta is the same Outlook category as 'B' for alpha" in captured.err
    assert "Type another for beta, or - to remove it." in captured.err
    assert "press Enter for none" not in captured.err, "Enter would keep the clash"
    assert machine.row()["categories"] == {"B": "alpha", "A": "beta"}


def test_a_kept_category_that_clashes_can_be_removed_with_a_dash(machine, monkeypatch, capsys):
    machine.projects[:] = ["alpha", "beta"]
    machine.write_row(categories={"A": "alpha", "B": "beta"})

    code, _ = _categories(monkeypatch, ["B", "", "-"])

    assert code == EXIT_OK, capsys.readouterr().err
    assert machine.row()["categories"] == {"B": "alpha"}


@pytest.mark.parametrize(
    "written",
    [{"A": 1}, {"A": None}, {"A": {"project": "alpha"}}, {"A": ["alpha"]}],
    ids=["a number", "null", "a nested object", "a list"],
)
def test_a_project_value_that_is_not_text_is_shown_as_written_and_replaced(
    machine, monkeypatch, capsys, written
):
    machine.write_row(categories=written)

    code, _ = _categories(monkeypatch, ["Alpha"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert f"written as {json.dumps(written, ensure_ascii=False)}" in captured.out
    assert "Traceback" not in captured.err
    assert machine.row()["categories"] == {"Alpha": "alpha"}


def test_application_settings_that_stop_reading_meanwhile_are_refused(
    machine, monkeypatch, capsys
):
    """Readable while the command composed; the questions' read then fails on
    something other than the registry (a `config.toml` that stopped parsing)."""
    from pm_ai.core.config import ConfigRefused

    path = machine.write_row()
    before = path.read_bytes()
    real = entry._bootstrap
    calls: list[int] = []

    def read(chain):
        calls.append(1)
        if len(calls) == 1:
            return real(chain)
        raise ConfigRefused("config.toml stopped parsing")

    monkeypatch.setattr(entry, "_bootstrap", read)

    code, terminal = _categories(monkeypatch, ["Alpha"])

    err = _refused(capsys, code)
    assert "ConfigRefused" in err and "Nothing was saved" in err
    assert "Traceback" not in err
    assert terminal.prompts == [] and path.read_bytes() == before


def test_a_claim_that_cannot_be_taken_at_all_is_refused_and_writes_nothing(
    machine, monkeypatch, capsys
):
    import contextlib

    from pm_ai.connectors.graph.auth import StoreUnavailable

    path = machine.write_row()
    before = path.read_bytes()

    @contextlib.contextmanager
    def unclaimable(self):
        raise StoreUnavailable("the claim's lock file is a directory")
        yield

    monkeypatch.setattr(SealedRefreshTokenStore, "exclusive", unclaimable)

    code, _ = _categories(monkeypatch, ["Alpha"])

    err = _refused(capsys, code)
    assert "could not be taken" in err and "config.json" in err
    assert "Nothing was saved" in err
    assert path.read_bytes() == before


def test_the_categories_key_deleted_while_answering_is_refused_as_changed(
    machine, monkeypatch, capsys
):
    path = machine.write_row(categories={"A": "alpha"})

    def delete_the_key() -> None:
        row = machine.row()
        del row["categories"]
        path.write_text(json.dumps(row))

    code, _ = _categories(monkeypatch, [delete_the_key, "Mine"])

    err = _refused(capsys, code)
    assert "changed while the questions were answered" in err
    edited = path.read_bytes()
    assert "categories" not in json.loads(edited), "the hand edit is what stays"
