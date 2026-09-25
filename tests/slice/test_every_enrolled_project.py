"""Story 4l — one row per line of "pm-ai works with every project you enrol".

Enrolling a second project used to stop pm-ai composing at all: `_compose`
refused at `len(projects) > 1` and reported the refusal as a `FAILING` probe. So
the second project's work was never looked at, and a machine in the arrangement
the architecture describes (AD-10) was told it was broken.

Two questions were being answered with one value, and every test here is about
one of them: **which projects pm-ai watches** — all of them — and **which
project a command is about** — the one whose folder contains the working
directory (AD-11).

Everything runs over a real `HOME`, a real `projects.toml` and real directories.
Only the keychain is stood in, because the real one is the developer's login
keychain. **None of these directories is a git repository**, which is the point
of the containment rule: a project is onboarded at a directory and
`onboard_project` runs no `git init`, so a selection that asked git would refuse
every command run from inside a perfectly good project.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pm_ai.app import entry
from pm_ai.app.pipelines import DASHBOARD_ARTIFACT, run_dashboard
from pm_ai.app.wiring import application_storage, build
from pm_ai.core.config import Config, render_config
from pm_ai.core.connector_enrolment import enrol_connector
from pm_ai.core.project_registry import ProjectEntry, render_registry
from pm_ai.domain.harvest import Cursor
from pm_ai.domain.health import Health
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.scope_model import APPLICATION_DIRNAME, PROJECT_DIRNAME
from pm_ai.platform.doctor import registry_readable
from pm_ai.platform.environment import DISABLE_ENCRYPTION_VAR
from pm_ai.ports import KeyNotFound
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL

REGISTRY_ARTIFACT = "projects.toml"
CONFIG_ARTIFACT = "config.toml"
SECRET = "glpat-not-a-real-token-0123456789"
NOW = datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc)


def accepts(system: str, credential: str) -> str:
    """A provider that answers, so enrolment's probe is not what a row is about."""
    return f"{system} accepted the credential"


class Keychain:
    """A `KeychainPort` holding one key, so nothing here reaches the real one."""

    def __init__(self, secret: bytes | None = b"\x11" * 32) -> None:
        self.secret = secret

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


@dataclass(frozen=True)
class Machine:
    """One throwaway home, and a registry written the way `project add` writes it."""

    home: Path
    root: Path
    monkeypatch: pytest.MonkeyPatch

    def enrol(self, **projects: Path) -> dict[str, Path]:
        """Register these projects, creating each directory, and hand them back.

        Written through `render_registry` rather than by hand so the file is the
        one the parser reads, and created on disk so `registry_readable` sees
        repositories that are still there.
        """
        for path in projects.values():
            path.mkdir(parents=True, exist_ok=True)
        registry = self.home / APPLICATION_DIRNAME / REGISTRY_ARTIFACT
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_bytes(
            render_registry(
                {name: ProjectEntry(path=path) for name, path in projects.items()}
            )
        )
        return dict(projects)

    def configure(self) -> None:
        """A `config.toml` complete enough for the dashboard to render."""
        config = self.home / APPLICATION_DIRNAME / CONFIG_ARTIFACT
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_bytes(
            render_config(
                Config(pm_handle="pm@example.com", display_timezone="Europe/Warsaw")
            )
        )

    def enrol_connector(self, instance: str) -> None:
        """Seal a credential for one connector instance, as `connector add` does.

        Through `application_storage`, so the cipher is the one the daemon
        composes with — a store sealed under a different key would be reported
        as absent and this row would pass for the wrong reason.
        """
        enrol_connector(
            application_storage(Keychain()),
            system="gitlab",
            instance=instance,
            credential=SECRET,
            probe=accepts,
        )

    def standing_in(self, where: Path) -> None:
        self.monkeypatch.chdir(where)

    def compose(self) -> entry._Composition:
        return entry._compose(Keychain())


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # An ambient debug flag would print a console warning on every compose and
    # record a security entry on the first protected write, and neither is what
    # any row here is measuring.
    monkeypatch.delenv(DISABLE_ENCRYPTION_VAR, raising=False)
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", Keychain)
    # Start every test standing somewhere that is inside no project, so a row
    # about being outside them all does not depend on where pytest was invoked.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return Machine(home=home, root=tmp_path, monkeypatch=monkeypatch)


def project_scope(project_id: str) -> DataScope:
    return DataScope(ScopeKind.PROJECT, project_id)


# ── Every enrolled project is watched ────────────────────────────────────────


def test_two_enrolled_projects_assemble_rather_than_refusing(machine):
    """The central row: two projects is a working machine, not a fault.

    `failure` is asserted `None` as well as the daemon being built, because the
    old behaviour produced *both* — no daemon and a `FAILING` probe — and a test
    that only asked for a daemon would pass while `doctor` still called this
    machine broken.
    """
    projects = machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")
    machine.standing_in(projects["alpha"])

    composed = machine.compose()

    assert composed.daemon is not None
    assert composed.failure is None
    assert composed.undecided is None


def test_a_connector_instance_exists_for_each_enrolled_project(machine):
    """Asserted per project: "it started" passes with one of them silently missing.

    And each instance is bound to *its own* scope, which is what keeps one
    project's commits out of the other's tree.
    """
    projects = machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")
    machine.standing_in(projects["alpha"])

    daemon = machine.compose().daemon
    assert daemon is not None

    assert daemon.watched == ("alpha", "beta")
    for project_id in sorted(projects):
        instance = f"gitlab:{project_id}"
        assert instance in daemon.connectors, sorted(daemon.connectors)
        assert daemon.connectors[instance].scope == project_scope(project_id)


def test_each_project_keeps_its_own_place_in_the_queue(machine):
    """One project's progress never advances another's.

    The cursor is keyed on the connector instance, and the instances are named
    per project — so this is a property of there being an instance each, which
    is exactly what the row above stopped being true before 4l.
    """
    projects = machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")
    machine.standing_in(projects["alpha"])
    daemon = machine.compose().daemon
    assert daemon is not None

    daemon.storage.save_cursor("gitlab:alpha", Cursor(b"page-2"), None, None)

    assert daemon.storage.load_cursor("gitlab:alpha") == Cursor(b"page-2")
    assert daemon.storage.load_cursor("gitlab:beta") == Cursor()


def test_a_repository_that_moved_is_reported_and_the_others_still_work(machine):
    """Unchanged from today for the project that moved; nothing else is affected.

    `ScopePaths.repository()` performs no existence check, so composition is not
    what notices — the registry probe is, and it names the project. What 4l adds
    is the second half: the projects that are still there compose anyway.
    """
    projects = machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")
    machine.standing_in(projects["alpha"])
    projects["beta"].rmdir()

    composed = machine.compose()
    assert composed.daemon is not None
    assert "gitlab:beta" in composed.daemon.connectors

    probe = registry_readable(composed.registry)
    assert probe.health is Health.FAILING
    assert "beta" in probe.detail and "alpha" not in probe.detail


# ── Which project a command is about ─────────────────────────────────────────


def test_a_command_inside_a_project_acts_in_that_project(machine):
    projects = machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")
    machine.standing_in(projects["beta"])

    daemon = machine.compose().daemon

    assert daemon is not None
    assert daemon.scope == project_scope("beta")


def test_a_sub_folder_several_levels_down_still_acts_in_that_project(machine):
    """Being in a sub-folder counts, and none of these directories is a repo."""
    projects = machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")
    deep = projects["beta"] / "src" / "pm" / "internals"
    deep.mkdir(parents=True)
    machine.standing_in(deep)

    daemon = machine.compose().daemon

    assert daemon is not None
    assert daemon.scope == project_scope("beta")


def test_the_innermost_enclosing_project_is_the_one_meant(machine):
    """One project's folder inside another's: the nearest one, not the outer one."""
    outer = machine.root / "outer"
    inner = outer / "nested" / "inner"
    machine.enrol(outer=outer, inner=inner)
    deeper = inner / "deeper"
    deeper.mkdir(parents=True)
    machine.standing_in(deeper)

    daemon = machine.compose().daemon

    assert daemon is not None
    assert daemon.scope == project_scope("inner")
    # And the outer one is still watched: "innermost" decides which project the
    # command acts in, never which projects pm-ai looks at.
    assert daemon.watched == ("inner", "outer")


def test_exactly_one_project_acts_from_anywhere_at_all(machine):
    """Nobody with a single project sees any change, including from nowhere near it.

    The fixture leaves the working directory outside every project, so this is
    the "nowhere near it" case rather than a restatement of the row above.
    """
    machine.enrol(alpha=machine.root / "alpha")

    composed = machine.compose()

    assert composed.undecided is None
    assert composed.daemon is not None
    assert composed.daemon.scope == project_scope("alpha")
    assert composed.daemon.watched == ("alpha",)


def test_outside_every_project_with_several_enrolled_is_refused_not_guessed(machine):
    """Refused, and the refusal names every enrolled project and its directory.

    A refusal that does not say what the choices are has moved the guesswork to
    the operator rather than resolved it.
    """
    projects = machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")

    composed = machine.compose()

    assert composed.daemon is None
    # Not a probe: `doctor` must not report this machine as faulty.
    assert composed.failure is None
    assert composed.undecided is not None
    for project_id, path in projects.items():
        assert project_id in composed.undecided
        assert str(path) in composed.undecided


def test_the_refusal_reaches_the_shell_as_the_existing_deliberate_no(machine, capsys):
    """No new exit code: 3 is the one that already means a stated, deliberate no."""
    machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")

    assert entry.main(["config", "show"]) == EXIT_REFUSAL

    printed = capsys.readouterr().err
    assert "alpha" in printed and "beta" in printed
    # The generic "could not build a daemon … `pm-ai doctor` reports why"
    # sentence would be false here: doctor reports a healthy machine.
    assert "could not build a daemon" not in printed


def test_two_ids_at_one_directory_are_refused_rather_than_settled_alphabetically(
    machine,
):
    """A tie is not a depth to break — it is one directory under two ids.

    `project add` refuses to create it, so this is a hand-edited registry; a
    winner picked by sort order would be pm-ai guessing which project a write
    belongs to, which is the one thing this slice will not do.
    """
    shared = machine.root / "shared"
    machine.enrol(alpha=shared, beta=shared)
    machine.standing_in(shared)

    composed = machine.compose()

    assert composed.daemon is None
    assert composed.undecided is not None
    # The wording, not just the refusal. One sentence served all three undecided
    # states until the 4l review, and here it was false: it opened "this command
    # was not run inside any enrolled project" and told an operator standing
    # inside two of them to go and stand inside one.
    assert "was not run inside any enrolled project" not in composed.undecided
    assert str(shared) in composed.undecided
    assert "alpha" in composed.undecided and "beta" in composed.undecided
    assert "projects.toml" in composed.undecided


def test_an_unreadable_working_directory_says_that_rather_than_blaming_the_registry(
    machine, monkeypatch
):
    """Deleted or unmounted under the shell: the cause is invisible otherwise.

    An operator told to "run it from inside a project" would go and read
    `projects.toml`, where nothing is wrong.
    """
    machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")

    def gone() -> Path:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(entry.Path, "cwd", staticmethod(gone))

    composed = machine.compose()

    assert composed.daemon is None
    assert composed.undecided is not None
    assert "working directory" in composed.undecided
    assert "No such file or directory" in composed.undecided
    assert "was not run inside any enrolled project" not in composed.undecided


def test_a_registry_path_that_cannot_be_resolved_is_named_in_the_refusal(
    machine, monkeypatch
):
    """Dropped from matching silently until the 4l review.

    An entry whose directory cannot be resolved — a symlink loop, an unreadable
    parent — matches nothing, so an operator standing inside that very project
    was told they were inside none, with no hint that their own registry entry
    was the reason.
    """
    projects = machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")
    unresolvable = projects["beta"]
    monkeypatch.setattr(
        entry, "_folder", lambda path: None if path == unresolvable else path.resolve()
    )

    composed = machine.compose()

    assert composed.daemon is None
    assert composed.undecided is not None
    assert "could not resolve the registered directory of beta" in composed.undecided


def test_nothing_enrolled_is_unchanged(machine):
    """An empty registry still says nothing is enrolled, in the registry's own probe."""
    composed = machine.compose()

    assert composed.daemon is None
    assert composed.undecided is None
    assert composed.failure is not None
    assert composed.failure.health is Health.ABSENT
    assert "no project is enrolled" in composed.failure.detail


# ── Nothing that separates projects loosens ──────────────────────────────────


def test_an_enrolled_connector_files_into_its_own_projects_tree(machine):
    """The highest-consequence behaviour in the slice, and it needs a credential.

    `gitlab:beta` is the instance name `gitlab.py`'s own remediation tells the
    operator to type, so the enrolled adapter *replaces* beta's built-in. It
    must inherit beta's scope: handed the acting command's, a connector enrolled
    for beta would file beta's commits into whichever project the operator was
    standing in, and into a different one on the next invocation.
    """
    projects = machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")
    machine.enrol_connector("gitlab:beta")
    machine.standing_in(projects["alpha"])

    daemon = machine.compose().daemon
    assert daemon is not None

    enrolled = daemon.connectors["gitlab:beta"]
    # The credential proves this is the *enrolled* adapter and not the built-in
    # it replaced — without it the scope assertion below would pass either way.
    assert enrolled.credential == SECRET
    assert enrolled.scope == project_scope("beta")
    assert daemon.scope == project_scope("alpha")


def test_an_enrolment_naming_no_watched_project_is_skipped_rather_than_misfiled(
    machine, capsys
):
    """The fallback closed: a row that matches no built-in has nowhere to file.

    A renamed project, a removed one, or a provider-shaped instance name. On a
    multi-project machine, defaulting to the acting scope is the same leak the
    row above closes, reached by another route. Skipped and said out loud,
    because a connector that silently vanishes is indistinguishable from one
    that was never enrolled.
    """
    projects = machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")
    machine.enrol_connector("gitlab:gamma")
    machine.standing_in(projects["alpha"])

    daemon = machine.compose().daemon
    assert daemon is not None

    assert "gitlab:gamma" not in daemon.connectors
    assert sorted(daemon.connectors) == ["gitlab:alpha", "gitlab:beta"]
    printed = capsys.readouterr().err
    assert "gitlab:gamma" in printed and "was not built" in printed


def test_a_single_project_machine_still_builds_an_unmatched_enrolment(machine):
    """One watched project is nothing to be ambiguous between, so nothing is skipped.

    Every machine that exists today is this one, and skipping their connectors
    to close a leak they cannot have would be this slice breaking them.
    """
    machine.enrol(alpha=machine.root / "alpha")
    machine.enrol_connector("gitlab:gamma")

    daemon = machine.compose().daemon
    assert daemon is not None

    enrolled = daemon.connectors["gitlab:gamma"]
    assert enrolled.credential == SECRET
    assert enrolled.scope == project_scope("alpha")


def test_a_command_writes_under_the_tree_of_the_project_it_bound_to(machine):
    """Where the bytes land, which is what "acts in that project" has to mean.

    Through `run_dashboard` with `daemon.scope` rather than through
    `entry.main`, and the reason is worth recording: **no subcommand today binds
    its write target to the acting project.** `dashboard` takes `--scope`
    explicitly and defaults to personal, `goal set` writes personal, and
    `project add` names its project as an argument. This is the pipeline a
    project-scoped command would go through, and the row below covers the CLI.
    """
    machine.configure()
    projects = machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")
    inside = projects["beta"] / "src"
    inside.mkdir(parents=True)
    machine.standing_in(inside)

    daemon = machine.compose().daemon
    assert daemon is not None
    written = run_dashboard(daemon, scope=daemon.scope, now=NOW)

    assert written == projects["beta"] / PROJECT_DIRNAME / "memory" / DASHBOARD_ARTIFACT
    assert written.is_file()
    assert not (projects["alpha"] / PROJECT_DIRNAME / "memory" / DASHBOARD_ARTIFACT).exists()


def test_the_cli_renders_the_other_projects_dashboard_into_the_other_projects_tree(
    machine, capsys
):
    """End to end through `entry.main`, on the machine that used to refuse to compose.

    Standing in `alpha`, `--scope project:beta` writes beta's dashboard under
    beta's repository and leaves nothing under alpha's. Before 4l this exited 3
    without writing anything at all, because two enrolled projects stopped the
    daemon being built.
    """
    machine.configure()
    projects = machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")
    machine.standing_in(projects["alpha"])

    assert entry.main(["dashboard", "--scope", "project:beta"]) == EXIT_OK

    landed = projects["beta"] / PROJECT_DIRNAME / "memory" / DASHBOARD_ARTIFACT
    assert landed.is_file()
    assert str(landed) in capsys.readouterr().out
    assert not (projects["alpha"] / PROJECT_DIRNAME / "memory" / DASHBOARD_ARTIFACT).exists()


# ── What the operator is told ────────────────────────────────────────────────


def test_connector_check_refuses_rather_than_reporting_a_first_run(machine, capsys):
    """An undecided machine has an empty registry for a reason that is not a first run.

    `connector check` is deliberately independent of the daemon, because a
    machine with nothing enrolled is an ordinary first run. But the registry is
    populated by composition, so without a check of its own this command
    answered "nothing is registered" about a machine with two projects and a
    sealed credential.
    """
    machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")
    machine.enrol_connector("gitlab:beta")

    assert entry.main(["connector", "check"]) == EXIT_REFUSAL

    captured = capsys.readouterr()
    assert "no connectors are registered" not in captured.out
    assert "alpha" in captured.err and "beta" in captured.err


def test_doctor_names_the_project_this_invocation_binds_to(machine, capsys):
    projects = machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")
    machine.standing_in(projects["beta"])

    entry.main(["doctor"])

    printed = capsys.readouterr().out
    assert entry.SELECTION_PROBE in printed
    assert "acts in beta" in printed


def test_doctor_says_when_the_directory_chooses_no_project_without_calling_it_a_fault(
    machine, capsys
):
    """The line exists so the operator sees the refusal coming — not to grade the machine.

    `Report.healthy` is "every probe is OK", so any other health here would let
    the working directory decide `doctor`'s exit code while `setup`, which
    reports through `run_all` alone, kept its own — the disagreement this slice
    exists to end, returning under a new name.
    """
    machine.enrol(alpha=machine.root / "alpha", beta=machine.root / "beta")

    entry.main(["doctor"])

    printed = capsys.readouterr().out
    assert entry.SELECTION_PROBE in printed
    assert "no project is selected here" in printed
    assert f"[{Health.FAILING.value:>7}] {entry.SELECTION_PROBE}" not in printed


def test_no_selection_line_at_all_when_nothing_is_enrolled(machine, capsys):
    """The registry probe already says it, and a second line is noise on a first run."""
    entry.main(["doctor"])

    assert entry.SELECTION_PROBE not in capsys.readouterr().out


# ── The two fields that must agree ───────────────────────────────────────────


def test_a_daemon_cannot_act_in_a_project_it_does_not_watch(machine):
    """Checked where the two are set, because the consequence is invisible.

    A `scope` outside `watched` writes into a project whose telemetry nothing
    collects, and nobody finds out until they go looking for a harvest that
    never ran. `build()` cannot produce one — it puts the acting project in the
    set whatever the caller passed — so the reachable way in is a construction
    of the dataclass, which is what the field's default used to permit.
    """
    daemon = build(machine.root / "rooted", "alpha")
    assert daemon.watched == ("alpha",)

    with pytest.raises(ValueError, match="does not include it"):
        replace(daemon, watched=("beta",))


def test_the_acting_project_joins_the_watched_set_whatever_the_caller_passed(machine):
    daemon = build(machine.root / "rooted", "alpha", watched=("beta",))

    assert daemon.watched == ("alpha", "beta")


@pytest.mark.parametrize("construct", ["build", "daemon"])
def test_a_bare_string_is_not_a_set_of_projects(machine, construct):
    """`watched="alpha"` is a valid `Sequence[str]` of five one-character ids.

    Every check downstream would pass, and the daemon would silently watch
    `a`, `l`, `p`, `h` and `a`. Refused by name at both doors.
    """
    if construct == "build":
        with pytest.raises(TypeError, match="not one project's name"):
            build(machine.root / "rooted", "alpha", watched="alpha")
        return
    daemon = build(machine.root / "rooted", "alpha")
    with pytest.raises(TypeError, match="not one project's name"):
        replace(daemon, watched="alpha")

