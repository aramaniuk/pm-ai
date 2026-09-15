"""`pm-ai project add` — story 4k's matrix, against a real temporary root.

Nothing is stubbed but the keychain, which would otherwise reach the developer's
login keychain, and the two rows that need a write to fail on demand. The rest
runs the real sequence over real directories, because every row here is about
what is on disk afterwards and a fake filesystem would assert the fake.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pm_ai.app import entry
from pm_ai.app.wiring import onboard_project
from pm_ai.core.project_registry import (
    DuplicateProject,
    ProjectPathUnusable,
    RegistryMalformed,
    parse_registry,
)
from pm_ai.core.project_scaffold import project_rules
from pm_ai.domain.claims import ClaimHeld
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.platform.claims import exclusive
from pm_ai.platform.paths import MalformedSubjectId, ScopePaths
from pm_ai.ports import KeyNotFound
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, EXIT_USAGE


class Keychain:
    """Enough of a `KeychainPort` to compose, holding one key."""

    def __init__(self, secret: bytes | None = b"\x00" * 32) -> None:
        self._secret = secret

    def store(self, name: str, secret: bytes) -> None:
        self._secret = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        if self._secret is None:
            self._secret = secret

    def fetch(self, name: str) -> bytes:
        if self._secret is None:
            raise KeyNotFound(name)
        return self._secret

    def delete(self, name: str) -> None:
        self._secret = None


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A redirected `HOME`, so `ScopePaths.production()` lands inside `tmp_path`."""
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("HOME", str(root))
    return root


def registry_of(home: Path) -> dict:
    raw = (home / ".pm-ai" / "projects.toml").read_bytes()
    return dict(parse_registry(raw))


# sqlite's own machinery, excluded from the byte-identical comparisons below.
# `StorageService.__init__` opens `operational.db` unconditionally, so merely
# constructing the writer creates the database and its write-ahead sidecars —
# and their bytes change on every open, whether or not anything was written.
# Naming them here rather than hashing a subtree is deliberate: the criterion is
# about *artifacts*, and a comparison scoped to `.project-ai/` would have passed
# while the registry was being rewritten.
SQLITE_SIDECARS = (".db", ".db-wal", ".db-shm")


def digests(root: Path) -> dict[str, str]:
    """Every artifact under `root`, by relative path, with its content hash."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.name.endswith(SQLITE_SIDECARS)
    }


# ── The ordinary outcomes ────────────────────────────────────────────────────


def test_first_onboarding_creates_the_structure_the_rules_and_the_entry(home, tmp_path):
    repository = tmp_path / "alpha"
    repository.mkdir()

    outcome = onboard_project(Keychain(), str(repository))

    assert outcome.project_id == "alpha"
    assert outcome.repository == repository.resolve()
    assert not outcome.already_registered
    assert (repository / ".project-ai" / "memory").is_dir()
    assert (repository / ".project-ai" / "rules").is_dir()
    text = (repository / ".gitignore").read_text()
    for rule in project_rules():
        assert rule in text
    assert registry_of(home)["alpha"].path == repository.resolve()


def test_an_absent_directory_is_created_then_onboarded(home, tmp_path):
    repository = tmp_path / "not" / "there" / "yet"

    onboard_project(Keychain(), str(repository))

    assert repository.is_dir()
    assert registry_of(home)["yet"].path == repository.resolve()


def test_a_relative_path_resolves_against_the_working_directory(home, tmp_path, monkeypatch):
    """Only absolute is stored: a relative entry means a different directory to
    every process that reads it, and the daemon is not started from the shell
    the operator typed this in."""
    monkeypatch.chdir(tmp_path)

    onboard_project(Keychain(), "./beta")

    stored = registry_of(home)["beta"].path
    assert stored.is_absolute()
    assert stored == (tmp_path / "beta").resolve()


def test_a_supplied_alias_becomes_the_id_and_the_path_is_untouched(home, tmp_path):
    """The row that makes an unusable directory name onboardable rather than fatal."""
    repository = tmp_path / "My Project"
    repository.mkdir()

    outcome = onboard_project(Keychain(), str(repository), "payments")

    assert outcome.project_id == "payments"
    assert registry_of(home)["payments"].path == repository.resolve()
    assert registry_of(home)["payments"].alias == "payments"


def test_a_second_project_joins_the_first_rather_than_replacing_it(home, tmp_path):
    """Asserted on the file, because `os.replace` keeps the last writer and the
    obvious implementation — render this one entry — loses the other."""
    for name in ("alpha", "beta"):
        (tmp_path / name).mkdir()
        onboard_project(Keychain(), str(tmp_path / name))

    assert set(registry_of(home)) == {"alpha", "beta"}


def test_onboarding_the_same_path_twice_changes_no_bytes_and_still_succeeds(home, tmp_path):
    repository = tmp_path / "alpha"
    repository.mkdir()
    onboard_project(Keychain(), str(repository))
    before = digests(repository) | digests(home)

    outcome = onboard_project(Keychain(), str(repository))

    assert outcome.already_registered
    assert digests(repository) | digests(home) == before


# ── Adoption ─────────────────────────────────────────────────────────────────


def test_an_existing_pm_ai_structure_is_adopted_with_every_file_byte_identical(
    home, tmp_path
):
    """Hashed before and after, because "adopted" and "regenerated" are
    indistinguishable from a success message — and regenerating would silently
    destroy a project's whole event log."""
    repository = tmp_path / "alpha"
    (repository / ".project-ai" / "memory" / "event_log").mkdir(parents=True)
    (repository / ".project-ai" / "memory" / "event_log" / "2026-09.md").write_text(
        "- [2026-09-01T00:00:00Z] something happened\n"
    )
    (repository / ".project-ai" / "rules").mkdir(parents=True, exist_ok=True)
    (repository / ".project-ai" / "rules" / "persona.md").write_text("# who I am\n")
    before = digests(repository / ".project-ai")

    onboard_project(Keychain(), str(repository))

    assert digests(repository / ".project-ai") == before


def test_an_existing_gitignore_keeps_its_own_rules(home, tmp_path):
    """A repository's `.gitignore` belongs to the team; onboarding appends."""
    repository = tmp_path / "alpha"
    repository.mkdir()
    (repository / ".gitignore").write_text("node_modules/\n*.pyc\n")

    onboard_project(Keychain(), str(repository))

    text = (repository / ".gitignore").read_text()
    assert "node_modules/" in text and "*.pyc" in text
    for rule in project_rules():
        assert rule in text


def test_a_gitignore_that_already_carries_the_rules_is_not_rewritten(home, tmp_path):
    repository = tmp_path / "alpha"
    repository.mkdir()
    onboard_project(Keychain(), str(repository))
    first = (repository / ".gitignore").read_bytes()

    (tmp_path / "alpha2").mkdir()
    onboard_project(Keychain(), str(repository))

    assert (repository / ".gitignore").read_bytes() == first


# ── Git, and the rule that has to satisfy the real guard ─────────────────────


def test_a_plain_directory_is_onboarded_and_gets_its_rules_anyway(home, tmp_path):
    """Git is optional (the human's Q1). `service.py:714-717` already treats "no
    working tree" as an answer, so nothing here checks for a repository."""
    repository = tmp_path / "alpha"
    repository.mkdir()

    onboard_project(Keychain(), str(repository))

    assert (repository / ".gitignore").is_file()
    assert not (repository / ".git").exists()


@pytest.mark.skipif(not os.environ.get("PATH"), reason="no PATH to find git on")
def test_inside_a_repository_a_gitignored_write_succeeds_afterwards(home, tmp_path, monkeypatch):
    """The criterion the whole `.gitignore` task exists for.

    A file that merely *exists* proves nothing: `_assert_git_excludes` asks git,
    not the text, so the only evidence that the generated rules are the right
    ones is a real write through the real guard succeeding.
    """
    repository = tmp_path / "alpha"
    repository.mkdir()
    for command in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *command], cwd=repository, check=True)
    monkeypatch.setenv("PM_AI_DISABLE_ENCRYPTION", "1")

    onboard_project(Keychain(), str(repository))

    read = entry._bootstrap(Keychain())
    paths = ScopePaths.production(projects={i: e.path for i, e in read.projects.items()})
    daemon = entry.build(None, "alpha", paths=paths, keychain=Keychain())
    written = daemon.storage.write_artifact(
        scope=DataScope(ScopeKind.PROJECT, "alpha"),
        artifact="memory/daily_dashboard.md",
        payload=b"## it works\n",
    )

    assert written.is_file()
    ignored = subprocess.run(
        ["git", "check-ignore", str(written)], cwd=repository, capture_output=True
    )
    assert ignored.returncode == 0, "git does not ignore what pm-ai just wrote"


# ── Refusals ─────────────────────────────────────────────────────────────────


def test_an_unusable_directory_name_without_an_alias_is_refused(home, tmp_path):
    repository = tmp_path / "My Project"
    repository.mkdir()

    with pytest.raises(MalformedSubjectId):
        onboard_project(Keychain(), str(repository))

    assert not (home / ".pm-ai" / "projects.toml").exists()
    assert not (repository / ".project-ai").exists()


def test_a_path_that_is_a_file_is_refused_before_anything_is_created(home, tmp_path):
    """Asserted on the filesystem: a refusal after a partial create leaves a
    project half-onboarded and no way to tell how far it got."""
    target = tmp_path / "alpha"
    target.write_text("I am a file\n")

    with pytest.raises(ProjectPathUnusable):
        onboard_project(Keychain(), str(target))

    assert target.read_text() == "I am a file\n"
    assert not (home / ".pm-ai" / "projects.toml").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_parent_is_refused_and_nothing_is_partially_created(home, tmp_path):
    parent = tmp_path / "locked"
    parent.mkdir(mode=0o500)
    try:
        with pytest.raises(ProjectPathUnusable) as refused:
            onboard_project(Keychain(), str(parent / "alpha"))
        assert str(parent / "alpha") in str(refused.value)
        assert not (parent / "alpha").exists()
        assert not (home / ".pm-ai" / "projects.toml").exists()
    finally:
        parent.chmod(0o700)


def test_the_same_id_at_a_different_path_is_refused_as_a_migration(home, tmp_path):
    """Refused rather than re-pointed: artifacts are already written and
    referenced under the old path, so moving the registration alone leaves a
    project whose event log and meetings are invisible."""
    (tmp_path / "one" / "alpha").mkdir(parents=True)
    (tmp_path / "two" / "alpha").mkdir(parents=True)
    onboard_project(Keychain(), str(tmp_path / "one" / "alpha"))

    with pytest.raises(DuplicateProject) as refused:
        onboard_project(Keychain(), str(tmp_path / "two" / "alpha"))

    assert "alpha" in str(refused.value)
    assert registry_of(home)["alpha"].path == (tmp_path / "one" / "alpha").resolve()


def test_an_alias_colliding_with_a_registered_id_is_refused(home, tmp_path):
    (tmp_path / "payments").mkdir()
    (tmp_path / "other").mkdir()
    onboard_project(Keychain(), str(tmp_path / "payments"))

    with pytest.raises(DuplicateProject):
        onboard_project(Keychain(), str(tmp_path / "other"), "payments")

    assert registry_of(home)["payments"].path == (tmp_path / "payments").resolve()


def test_a_malformed_registry_refuses_and_leaves_the_file_byte_identical(home, tmp_path):
    """`projects.toml` is rebuildable from nothing, so a registry that could not
    be parsed must never be the one that gets written over."""
    (home / ".pm-ai").mkdir(parents=True)
    broken = b"[projects.alpha]\npath = 7\n"
    (home / ".pm-ai" / "projects.toml").write_bytes(broken)
    (tmp_path / "beta").mkdir()

    with pytest.raises(RegistryMalformed):
        onboard_project(Keychain(), str(tmp_path / "beta"))

    assert (home / ".pm-ai" / "projects.toml").read_bytes() == broken


# ── Concurrency, and the entry that must not be lost ─────────────────────────


def test_a_second_run_refuses_while_the_claim_is_held(home, tmp_path):
    """`write_artifact` publishes with `os.replace`, which keeps the last writer
    whole and discards the other — so without the claim, two runs that both read
    a one-entry registry both render a two-entry one and one project vanishes
    with both commands reporting success."""
    (tmp_path / "alpha").mkdir()
    registry = ScopePaths.production().project_registry

    with exclusive(registry):
        with pytest.raises(ClaimHeld):
            onboard_project(Keychain(), str(tmp_path / "alpha"))

    assert not (home / ".pm-ai" / "projects.toml").exists()


def test_two_real_processes_onboarding_at_once_lose_no_entry(home, tmp_path):
    """The property the claim exists for, asserted across real processes.

    Threads would not prove it: the failure is two `os.replace` calls from two
    interpreters, and an in-process test can only simulate the interleaving it
    has already assumed.
    """
    for name in ("alpha", "beta"):
        (tmp_path / name).mkdir()
    program = (
        "import sys;"
        "sys.path.insert(0, %r);" % str(Path(__file__).resolve().parents[2])
        + "from pm_ai.app.wiring import onboard_project;"
        "from pm_ai.platform.keychain import MacOSKeychainAdapter;"
        "onboard_project(MacOSKeychainAdapter(), sys.argv[1])"
    )
    running = [
        subprocess.Popen(
            [sys.executable, "-c", program, str(tmp_path / name)],
            env={**os.environ, "HOME": str(home), "PM_AI_DISABLE_ENCRYPTION": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for name in ("alpha", "beta")
    ]
    outcomes = [(p.wait(timeout=60), p.communicate()) for p in running]

    survived = set(registry_of(home)) if (home / ".pm-ai" / "projects.toml").exists() else set()
    succeeded = {
        name for name, (code, _) in zip(("alpha", "beta"), outcomes) if code == 0
    }
    assert succeeded, f"neither run completed: {outcomes}"
    assert succeeded <= survived, (
        f"a run reported success and its entry is not in the registry: "
        f"succeeded={succeeded} survived={survived}"
    )


# ── Through the CLI ──────────────────────────────────────────────────────────


def test_the_cli_onboards_and_exits_zero(home, tmp_path, capsys):
    (tmp_path / "alpha").mkdir()

    assert entry.main(["project", "add", str(tmp_path / "alpha")]) == EXIT_OK

    assert "onboarded" in capsys.readouterr().out
    assert set(registry_of(home)) == {"alpha"}


def test_the_cli_takes_an_optional_alias(home, tmp_path, capsys):
    (tmp_path / "My Project").mkdir()

    assert entry.main(["project", "add", str(tmp_path / "My Project"), "payments"]) == EXIT_OK

    assert set(registry_of(home)) == {"payments"}


def test_the_cli_refuses_a_third_argument_rather_than_dropping_it(home, tmp_path, capsys):
    """The optional positional widened the arity check to a range; too many words
    still refuse, which is what keeps a typo'd argument from being swallowed."""
    (tmp_path / "alpha").mkdir()

    code = entry.main(["project", "add", str(tmp_path / "alpha"), "payments", "extra"])

    assert code == EXIT_USAGE
    assert "[alias]" in capsys.readouterr().err


def test_the_cli_maps_a_refusal_onto_exit_three(home, tmp_path, capsys):
    target = tmp_path / "alpha"
    target.write_text("a file\n")

    assert entry.main(["project", "add", str(target)]) == EXIT_REFUSAL


def test_the_cli_reports_an_already_onboarded_project_and_still_exits_zero(
    home, tmp_path, capsys
):
    """An ordinary outcome, so `project add` stays safe to put in a setup script."""
    (tmp_path / "alpha").mkdir()
    entry.main(["project", "add", str(tmp_path / "alpha")])
    capsys.readouterr()

    assert entry.main(["project", "add", str(tmp_path / "alpha")]) == EXIT_OK
    assert "already onboarded" in capsys.readouterr().out


def test_onboarding_then_doctor_reports_the_registry_healthy(home, tmp_path, capsys):
    """The end of the wave's first-boot path: a machine goes from nothing
    enrolled to a registry `doctor` calls healthy, using only the CLI."""
    (tmp_path / "alpha").mkdir()
    entry.main(["project", "add", str(tmp_path / "alpha")])
    capsys.readouterr()

    entry.main(["doctor"])

    printed = capsys.readouterr().out
    assert "1 project(s) enrolled: alpha" in printed


# ── The five the 2026-09-15 review found ─────────────────────────────────────
#
# Every one of them exited 0, or exited 1, on a machine the operator would have
# had no reason to distrust. They are grouped so a future reader can see what
# the matrix above did not cover: four of the five are about a *second* state of
# a file the matrix only considered absent or well-formed.


def test_an_unreadable_registry_is_never_written_over(home, tmp_path):
    """The worst of the five: every enrolled project, silently forgotten.

    `_artifact_state` reports UNREADABLE with no bytes, and `parse_registry(None)`
    means *absent* — so the sequence rendered a registry holding only the new
    project and `os.replace`d it over the one it could not read. `projects.toml`
    is Tier 1 and rebuildable from nothing, so there was no second copy.
    """
    for name in ("alpha", "beta"):
        (tmp_path / name).mkdir()
        onboard_project(Keychain(), str(tmp_path / name))
    registry = home / ".pm-ai" / "projects.toml"
    before = registry.read_bytes()
    registry.chmod(0o000)
    (tmp_path / "gamma").mkdir()

    try:
        with pytest.raises(ProjectPathUnusable) as refused:
            onboard_project(Keychain(), str(tmp_path / "gamma"))
        assert "could not be read" in str(refused.value)
    finally:
        registry.chmod(0o600)

    assert registry.read_bytes() == before
    assert set(registry_of(home)) == {"alpha", "beta"}


def test_one_directory_cannot_be_onboarded_under_two_ids(home, tmp_path):
    """Both ids resolve to the same `.project-ai`, so they are not two projects.

    They would share one event log, one meeting set and one dashboard while every
    `SourceRef` disagreed about which project owned them — and `_compose` would
    then refuse every subcommand but `doctor` as an ambiguous registry, on a
    machine with exactly one repository.
    """
    repository = tmp_path / "alpha"
    repository.mkdir()
    onboard_project(Keychain(), str(repository))

    with pytest.raises(DuplicateProject) as refused:
        onboard_project(Keychain(), str(repository), "payments")

    assert "alpha" in str(refused.value)
    assert set(registry_of(home)) == {"alpha"}


def test_the_cli_stays_usable_after_a_repeated_onboarding_with_an_alias(
    home, tmp_path, capsys
):
    """The consequence of the row above, asserted where an operator would meet it."""
    repository = tmp_path / "alpha"
    repository.mkdir()
    entry.main(["project", "add", str(repository)])

    assert entry.main(["project", "add", str(repository), "payments"]) == EXIT_REFUSAL
    capsys.readouterr()

    entry.main(["doctor"])
    assert "1 project(s) enrolled: alpha" in capsys.readouterr().out


def test_a_gitignore_that_is_not_utf8_survives_byte_for_byte(home, tmp_path):
    """The file belongs to the team and git treats it as bytes.

    Decoded with `errors="replace"` and re-encoded, a latin-1 rule excluding
    `café/` came back as `caf\\uFFFD/`, which matches nothing — so onboarding
    silently un-ignored whatever that rule protected.
    """
    repository = tmp_path / "alpha"
    repository.mkdir()
    original = "café/\nbuild/\n".encode("latin-1")
    (repository / ".gitignore").write_bytes(original)

    onboard_project(Keychain(), str(repository))

    written = (repository / ".gitignore").read_bytes()
    assert written.startswith(original), "the team's own rules were rewritten"
    for rule in project_rules():
        assert rule.encode() in written


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_an_unreadable_gitignore_is_a_refusal_and_not_a_traceback(home, tmp_path, capsys):
    """Exit 1 means "pm-ai broke" in the one table that decides these, and a
    `.gitignore` nobody can open is an ordinary thing to be wrong with a machine.
    """
    repository = tmp_path / "alpha"
    repository.mkdir()
    (repository / ".gitignore").write_text("node_modules/\n")
    (repository / ".gitignore").chmod(0o000)

    try:
        assert entry.main(["project", "add", str(repository)]) == EXIT_REFUSAL
        assert "Traceback" not in capsys.readouterr().err
    finally:
        (repository / ".gitignore").chmod(0o600)


def test_the_paths_argument_decides_the_layout_it_is_given(home, tmp_path):
    """`paths=` was read for its project map and discarded for everything else.

    A caller handing in `ScopePaths.rooted(...)` — the documented way to get a
    temporary layout, and the one `bootstrap` honours — had its registry written
    under the real `$HOME` regardless.

    The `home` fixture is here as a blast shield, not as the subject: this test
    ran once against the unfixed code without one and wrote a registry into the
    developer's actual `~/.pm-ai`, which is the bug, demonstrated the hard way.
    The assertion is that the registry lands in the *sandbox* and that the
    redirected home stays empty — which fails just as loudly, and cannot escape.
    """
    sandbox = tmp_path / "sandbox"
    repository = tmp_path / "alpha"
    repository.mkdir()

    onboard_project(Keychain(), str(repository), paths=ScopePaths.rooted(sandbox))

    written = list(sandbox.rglob("projects.toml"))
    assert written, f"nothing was written under the resolver it was given: {sandbox}"
    assert "alpha" in parse_registry(written[0].read_bytes())
    assert not (home / ".pm-ai" / "projects.toml").exists(), (
        "the registry went to $HOME instead of the resolver it was handed"
    )
