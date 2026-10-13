"""Story 4n — `pm-ai doctor` and `pm-ai setup` name the connectors enrolled here.

Every row runs `entry.main` over a real temporary home, so the `connectors/`
rows, the registry, the sealed credentials file and the probes are the real
ones. Faked: the keychain (`test_connector_add_graph.Keychain`, counting its
fetches), the operator's answers, and Microsoft — only where a sign-in has to
put a sealed token on disk, through `GraphEnrolment.client_factory`.

The line is read off printed output, because that is what an operator sees and
what `setup`'s closing report is: `run_all` does not carry it.
"""

from __future__ import annotations

import importlib.abc
import json
import subprocess
import sys
from pathlib import Path

import pytest

from pm_ai.app import entry, wiring
from pm_ai.app.wiring import bootstrap, build
from pm_ai.connectors.gitlab import GitLabConnectorAdapter
from pm_ai.connectors.graph import GraphConnector
from pm_ai.domain.health import Health
from pm_ai.platform import doctor as doctor_probes
from pm_ai.platform.environment import DISABLE_ENCRYPTION_VAR
from pm_ai.ports import KeyNotFound
from pm_ai.storage.service import StorageService
from pm_ai.surfaces.cli.dispatch import EXIT_OK
from tests.slice.test_connector_add_graph import (
    ROTATED,
    SIGNED_IN,
    VALID,
    Keychain,
    Microsoft,
    _add,
    _microsoft,
)
from tests.slice.test_first_run import Operator, answers_for

CONNECTORS_LINE = "] connectors: "
ADD_GRAPH = "pm-ai connector add graph <name>"
ADD_GITLAB = "pm-ai connector add gitlab <name>"


class CountingKeychain(Keychain):
    """`8l`'s keychain, counting every time the master key is asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.fetches = 0

    def fetch(self, name: str) -> bytes:
        self.fetches += 1
        return super().fetch(name)


class RefusingKeychain(Keychain):
    """A keychain that has no key to give: anything needing one fails."""

    def __init__(self) -> None:
        super().__init__(secret=None)

    def fetch(self, name: str) -> bytes:
        raise KeyNotFound(name)


class Machine:
    def __init__(self, home: Path, root: Path, keychain: CountingKeychain) -> None:
        self.home = home
        self.root = root
        self.keychain = keychain

    @property
    def connectors(self) -> Path:
        return self.home / ".pm-ai" / "connectors"

    def row(self, file: str, **row: object) -> Path:
        """A row written by hand, the way an operator edits one."""
        self.connectors.mkdir(parents=True, exist_ok=True)
        path = self.connectors / file
        path.write_text(json.dumps(row), encoding="utf-8")
        return path

    def enabled(self, instance: str, system: str, **more: object) -> Path:
        return self.row(
            f"{instance}.json", instance=instance, system=system, enabled=True, **more
        )


@pytest.fixture
def machine(tmp_path, monkeypatch) -> Machine:
    """A redirected home with a master key and nothing enrolled yet."""
    keychain = CountingKeychain()
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: keychain)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(DISABLE_ENCRYPTION_VAR, raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # The runtime extra stood in, as `test_first_run.py` does, so a setup run
    # can exit 0 and `with_project` can say it did.
    monkeypatch.setattr(doctor_probes, "missing_distributions", lambda _names: ())
    return Machine(home, tmp_path, keychain)


@pytest.fixture
def operator(monkeypatch) -> Operator:
    scripted = Operator()
    monkeypatch.setattr("builtins.input", scripted)
    return scripted


@pytest.fixture
def uncontacted(monkeypatch) -> list[str]:
    """Every provider health check attempted; `doctor` must attempt none."""
    contacted: list[str] = []

    def refuse(self):
        contacted.append(self.instance)
        raise AssertionError(f"{self.instance} was contacted")

    monkeypatch.setattr(GraphConnector, "check_health", refuse)
    monkeypatch.setattr(GitLabConnectorAdapter, "check_health", refuse)
    return contacted


def with_project(machine: Machine, operator: Operator, name: str = "alpha") -> None:
    """One project enrolled through `setup` itself, as a person would."""
    operator.script(*answers_for(machine.root / name))
    assert entry.main(["setup"]) == EXIT_OK


def doctor(capsys) -> str:
    capsys.readouterr()
    entry.main(["doctor"])
    return capsys.readouterr().out


def line_of(printed: str) -> str:
    """The printed `connectors` line, and its remediation line when it has one."""
    lines = printed.splitlines()
    (at,) = [i for i, line in enumerate(lines) if CONNECTORS_LINE in line]
    remedy = [line for line in lines[at + 1 : at + 2] if line.lstrip().startswith("→")]
    return "\n".join([lines[at], *remedy])


def health_of(line: str) -> str:
    return line.split("]", 1)[0].strip("[ ")


def denied(path: object = "") -> PermissionError:
    return PermissionError(13, "Permission denied", str(path))


def failing_read(monkeypatch, name: str, outcome: BaseException | None) -> None:
    """`connectors/<name>` read as raising `outcome`, or as gone when `None`.

    A stand-in rather than a file mode, so the row runs the same as any user,
    root included: root reads a file whatever its mode.
    """
    real = StorageService.read_artifact

    def read(self, *, scope, artifact, name: str | None = None, **kwargs):
        if artifact == "connectors/" and name == wanted:
            if outcome is None:
                return None
            raise outcome
        return real(self, scope=scope, artifact=artifact, name=name, **kwargs)

    wanted = name
    monkeypatch.setattr(StorageService, "read_artifact", read)


# ── Rows listed ──────────────────────────────────────────────────────────────


def test_a_graph_and_a_gitlab_row_are_listed_sorted_and_nothing_is_contacted(
    machine, operator, uncontacted, capsys
):
    with_project(machine, operator)
    machine.enabled("graph:work", "graph")
    machine.enabled("gitlab:alpha", "gitlab", project="group/alpha")

    line = line_of(doctor(capsys))

    assert "[     ok] connectors: 2 enrolled: gitlab:alpha, graph:work" in line, line
    assert uncontacted == []


def test_one_row_is_one_enrolled(machine, uncontacted, capsys):
    machine.enabled("graph:work", "graph")

    line = line_of(doctor(capsys))

    assert "[     ok] connectors: 1 enrolled: graph:work" in line, line


def test_none_enrolled_says_so_with_both_add_commands(machine, capsys):
    """Nothing at all: no project and no rows. The registry still names `project add`."""
    printed = doctor(capsys)
    line = line_of(printed)

    assert "[     ok] connectors: none enrolled" in line, line
    assert ADD_GRAPH in line and ADD_GITLAB in line
    assert "pm-ai project add" in printed
    assert printed.index("] project registry: ") < printed.index(CONNECTORS_LINE)


def test_only_disabled_rows_is_none_enabled_never_none_enrolled(machine, capsys):
    machine.row("graph:work.json", instance="graph:work", system="graph", enabled=False)

    line = line_of(doctor(capsys))

    assert "[     ok] connectors: none enabled; 1 disabled: graph:work" in line, line
    assert "none enrolled" not in line


@pytest.mark.parametrize(
    "beside",
    [
        {"instance": "graph:old", "system": "graph", "enabled": False},
        {"instance": "graph:old", "system": "graph", "enabled": "true"},
        {"instance": "jira:x", "system": "jira", "enabled": True},
    ],
    ids=["disabled", "enabled-as-text", "unknown-system"],
)
def test_a_disabled_or_unknown_system_row_is_not_listed_as_enrolled(
    machine, capsys, beside
):
    machine.enabled("graph:work", "graph")
    machine.row(f"{beside['instance']}.json", **beside)

    line = line_of(doctor(capsys))

    assert "connectors: 1 enrolled: graph:work;" in line, line
    assert health_of(line) == Health.OK.value


# ── Rows that could not be used ──────────────────────────────────────────────


def test_a_row_that_is_not_json_is_a_warning_naming_it_beside_the_others(
    machine, capsys
):
    machine.enabled("graph:work", "graph")
    (machine.connectors / "broken.json").write_text("{ not json", encoding="utf-8")

    printed = doctor(capsys)
    line = line_of(printed)

    assert health_of(line) == Health.WARNING.value, line
    assert "1 enrolled: graph:work" in line
    assert "connectors/broken.json is not valid JSON" in line
    assert "NOT healthy" in printed, "the warning changes the verdict"


def test_a_row_that_cannot_be_read_is_a_warning_naming_it_and_its_cause(
    machine, monkeypatch, capsys
):
    machine.enabled("graph:work", "graph")
    machine.enabled("gitlab:locked", "gitlab")
    failing_read(monkeypatch, "gitlab:locked.json", denied())

    line = line_of(doctor(capsys))

    assert health_of(line) == Health.WARNING.value, line
    assert "1 enrolled: graph:work" in line
    assert "connectors/gitlab:locked.json could not be read (Permission denied)" in line


def test_a_row_removed_between_listing_and_reading_is_skipped_not_a_problem(
    machine, monkeypatch, capsys
):
    machine.enabled("graph:work", "graph")
    machine.enabled("gitlab:gone", "gitlab")
    failing_read(monkeypatch, "gitlab:gone.json", None)

    line = line_of(doctor(capsys))

    assert "[     ok] connectors: 1 enrolled: graph:work" in line, line
    assert "gitlab:gone" not in line


@pytest.mark.parametrize(
    "body",
    ['{"instance": "graph:x", "n": ' + "1" * 5000 + "}", "[" * 100_000 + "]" * 100_000],
    ids=["integer-past-the-conversion-limit", "nesting-past-the-recursion-limit"],
)
def test_json_the_parser_refuses_by_value_or_recursion_is_not_valid_json(
    machine, capsys, body
):
    machine.enabled("graph:work", "graph")
    (machine.connectors / "huge.json").write_text(body, encoding="utf-8")

    assert bootstrap(machine.keychain).connectors.problems == (
        "connectors/huge.json is not valid JSON",
    )
    line = line_of(doctor(capsys))
    assert health_of(line) == Health.WARNING.value, line
    assert "1 enrolled: graph:work" in line


def test_a_backup_or_a_subdirectory_beside_a_row_is_not_a_row(machine, capsys):
    """An editor's backup carries the row's own name; it must not make it a duplicate."""
    real = machine.enabled("graph:work", "graph")
    for backup in ("graph:work.json~", "graph:work.json.bak", "graph:work.bak"):
        (machine.connectors / backup).write_bytes(real.read_bytes())
    (machine.connectors / "nested.json").mkdir()

    line = line_of(doctor(capsys))

    assert "[     ok] connectors: 1 enrolled: graph:work" in line, line
    assert "could not be used" not in line


@pytest.mark.parametrize(
    "instance",
    ["graph:\nwork", "graph:\x1b[2Jwork", "   ", ""],
    ids=["newline", "escape", "blank", "empty"],
)
def test_a_name_that_is_not_printable_text_is_a_malformed_row(
    machine, capsys, instance
):
    machine.row("odd.json", instance=instance, system="graph", enabled=True)

    printed = doctor(capsys)
    line = line_of(printed)

    assert health_of(line) == Health.WARNING.value, line
    assert "connectors/odd.json has no text 'instance'" in line
    assert "\x1b" not in printed
    assert "none enrolled" in line


def test_two_files_naming_one_connector_are_one_problem_naming_both(machine, capsys):
    machine.row("a.json", instance="graph:work", system="graph", enabled=True)
    machine.row("b.json", instance="graph:work", system="graph", enabled=True)

    line = line_of(doctor(capsys))

    assert health_of(line) == Health.WARNING.value, line
    assert "none enrolled" in line, "a duplicate is not two enrolled connectors"
    assert "1 row could not be used" in line
    assert "connectors/a.json" in line and "connectors/b.json" in line


def test_three_files_naming_one_connector_are_one_problem_naming_all_three(
    machine, capsys
):
    for file in ("a.json", "b.json", "c.json"):
        machine.row(file, instance="graph:work", system="graph", enabled=True)

    line = line_of(doctor(capsys))

    assert (
        "graph:work is named by 3 rows: connectors/a.json, connectors/b.json and "
        "connectors/c.json"
    ) in line, line
    rows = wiring.read_connector_rows(build(machine.home, "alpha").storage)
    assert rows.refused == (
        (
            "graph:work",
            "connectors/a.json, connectors/b.json and connectors/c.json all name "
            "it, so pm-ai cannot tell which row is meant. Remove all but one.",
        ),
    )


def test_a_disabled_row_sharing_an_enabled_rows_name_is_not_listed_twice(
    machine, capsys
):
    machine.enabled("graph:work", "graph")
    machine.row("old.json", instance="graph:work", system="graph", enabled=False)

    line = line_of(doctor(capsys))

    assert "[     ok] connectors: 1 enrolled: graph:work" in line, line
    assert "disabled" not in line


def test_two_disabled_rows_with_one_name_are_listed_once(machine, capsys):
    machine.row("a.json", instance="graph:work", system="graph", enabled=False)
    machine.row("b.json", instance="graph:work", system="graph", enabled=False)

    line = line_of(doctor(capsys))

    assert "[     ok] connectors: none enabled; 1 disabled: graph:work" in line, line


def test_two_files_naming_one_connector_build_neither_and_connector_check_says_why(
    machine, uncontacted, capsys
):
    machine.row("a.json", instance="graph:work", system="graph", enabled=True)
    machine.row("b.json", instance="graph:work", system="graph", enabled=True)

    capsys.readouterr()
    entry.main(["connector", "check"])
    checked = capsys.readouterr().out

    rows = [line for line in checked.splitlines() if line.startswith("[")]
    assert rows == [
        "[unbuilt] graph:work: enrolled, not built: connectors/a.json and "
        "connectors/b.json both name it, so pm-ai cannot tell which row is meant. "
        "Remove all but one."
    ], checked
    assert uncontacted == []
    assert "graph:work" not in build(machine.home, "alpha").connectors


def test_a_directory_that_cannot_be_listed_fails_and_never_says_none_enrolled(
    machine, monkeypatch, capsys
):
    machine.enabled("graph:work", "graph")
    real = StorageService.list_collection

    def listing(self, *, scope, artifact):
        if artifact == "connectors/":
            raise denied(machine.connectors)
        return real(self, scope=scope, artifact=artifact)

    monkeypatch.setattr(StorageService, "list_collection", listing)

    line = line_of(doctor(capsys))

    assert health_of(line) == Health.FAILING.value, line
    assert "could not be listed" in line and "Permission denied" in line
    assert str(machine.connectors) in line, "the remedy names the directory read"
    assert "none" not in line


# ── No daemon, no provider ───────────────────────────────────────────────────


def test_two_projects_run_from_outside_both_doctor_and_setup_print_the_same_line(
    machine, operator, monkeypatch, capsys
):
    with_project(machine, operator, "alpha")
    with_project(machine, operator, "beta")
    machine.enabled("graph:work", "graph")
    monkeypatch.chdir(machine.root)

    operator.script(str(machine.root / "alpha"), "")
    capsys.readouterr()
    entry.main(["setup"])
    setup_said = capsys.readouterr().out
    doctor_said = doctor(capsys)

    assert "no project is selected here" in doctor_said, "no daemon can be started here"
    assert line_of(setup_said) == line_of(doctor_said)
    assert "[     ok] connectors: 1 enrolled: graph:work" in line_of(doctor_said)
    for said in (setup_said, doctor_said):
        assert said.index("] project registry: ") < said.index(CONNECTORS_LINE)
    assert doctor_said.index(CONNECTORS_LINE) < doctor_said.index("project selection")


class _RefuseMsal(importlib.abc.MetaPathFinder):
    """`msal` not installed, and every attempt to import it recorded."""

    def __init__(self) -> None:
        self.attempts: list[str] = []

    def find_spec(self, name, path=None, target=None):
        if name == "msal" or name.startswith("msal."):
            self.attempts.append(name)
            raise ImportError(f"No module named {name!r}")
        return None


def test_without_msal_a_graph_row_is_still_listed_and_no_import_is_attempted(
    machine, operator, monkeypatch, capsys
):
    with_project(machine, operator)
    _microsoft(monkeypatch, Microsoft())
    code, _ = _add(monkeypatch, VALID)
    assert code == EXIT_OK

    refusing = _RefuseMsal()
    for loaded in [m for m in sys.modules if m == "msal" or m.startswith("msal.")]:
        monkeypatch.delitem(sys.modules, loaded)
    monkeypatch.setattr(sys, "meta_path", [refusing, *sys.meta_path])

    line = line_of(doctor(capsys))

    assert "1 enrolled: graph:work" in line, line
    assert refusing.attempts == []
    # The positive control: the finder does refuse an import that is attempted.
    with pytest.raises(ImportError):
        __import__("msal")
    assert refusing.attempts == ["msal"]


def test_this_modules_own_imports_do_not_load_msal():
    """Otherwise the `msal` row above would prove nothing about the line."""
    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, tests.slice.test_doctor_names_connectors; "
            "print(any(m == 'msal' or m.startswith('msal.') for m in sys.modules))",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert loaded.stdout.strip() == "False", loaded.stderr


def test_a_sealed_sign_in_never_reaches_the_output_or_asks_the_keychain(
    machine, operator, monkeypatch, capsys
):
    with_project(machine, operator)
    _microsoft(monkeypatch, Microsoft())
    code, _ = _add(monkeypatch, VALID)
    assert code == EXIT_OK

    # The master key is never asked for while the rows are read or the line
    # is built, whichever command reads them.
    asked_while_reading: list[int] = []
    real_read, real_probe = wiring.read_connector_rows, entry.connectors_enrolled

    def reading(storage):
        before = machine.keychain.fetches
        try:
            return real_read(storage)
        finally:
            asked_while_reading.append(machine.keychain.fetches - before)

    def probing(rows):
        before = machine.keychain.fetches
        try:
            return real_probe(rows)
        finally:
            asked_while_reading.append(machine.keychain.fetches - before)

    monkeypatch.setattr(wiring, "read_connector_rows", reading)
    monkeypatch.setattr(entry, "connectors_enrolled", probing)

    doctor_said = doctor(capsys)
    monkeypatch.setattr("builtins.input", operator)  # `_add` replaced it
    operator.script(str(machine.root / "alpha"), "")
    entry.main(["setup"])
    setup_said = capsys.readouterr().out

    for said in (doctor_said, setup_said):
        assert "1 enrolled: graph:work" in line_of(said)
        for secret in (SIGNED_IN, ROTATED, "an-access-token"):
            assert secret not in said
    assert asked_while_reading and set(asked_while_reading) == {0}

    # And with no key to give at all, the rows are still read and named.
    named = bootstrap(RefusingKeychain()).connectors
    assert named.enrolled == ("graph:work",)


# ── doctor and connector check count one set of rows ─────────────────────────


def test_doctor_and_connector_check_count_the_same_row_the_daemon_cannot_build(
    machine, capsys
):
    machine.enabled("gitlab:x", "gitlab", project="group/x")

    doctor_line = line_of(doctor(capsys))
    entry.main(["connector", "check"])
    checked = capsys.readouterr().out

    assert "[     ok] connectors: 1 enrolled: gitlab:x" in doctor_line, doctor_line
    rows = [line for line in checked.splitlines() if line.startswith("[")]
    assert len(rows) == 1, checked
    (row,) = rows
    assert row.startswith("[unbuilt] gitlab:x: enrolled, not built:"), row
    assert "no project is enrolled" in row
