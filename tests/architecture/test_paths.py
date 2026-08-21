"""The scope layout, asserted (AD-4, AD-31, AD-3, AD-11, AD-38).

One test per row of story 1a's I/O matrix, then the consequences of that layout
that a matrix row implies but does not spell out.

These are path assertions rather than filesystem ones on purpose: the resolver's
job is to be the single answer to "where does this live", and that answer is
checkable without creating anything. The `production` fixture is given an
explicit home for the same reason — a test whose expected value is derived from
the ambient `HOME` cannot fail when the code stops reading `HOME`.

The containment assertions here rest entirely on paths being normalized. A `..`
that survives into `Path.is_relative_to` makes it answer True about a path that
escaped, so `test_a_dot_dot_cannot_survive_a_containment_check` is load-bearing
for most of the file, not a curiosity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.storage_tiers import (
    ARTIFACT_TIER,
    GITIGNORE_REQUIRED,
    RETENTION_MANAGED,
    ScopeResolutionError,
    Tier,
)
from pm_ai.platform.paths import (
    PERSONAL_SUBJECT_ARTIFACTS,
    ArtifactNotInScope,
    MalformedSubjectId,
    RepositoryOutsideRoot,
    ScopePathError,
    ScopePaths,
    UnknownArtifact,
    UnknownProject,
    artifacts_in,
    is_directory,
)

# An explicit home and an explicit repository, so nothing here depends on the
# machine it runs on.
HOME = Path("/home/pm")
REPO = Path("/repositories/alpha")

APPLICATION = DataScope(ScopeKind.APPLICATION)
PERSONAL = DataScope(ScopeKind.PERSONAL)
PEOPLE = DataScope(ScopeKind.PEOPLE, person_id="p1")
PROJECT = DataScope(ScopeKind.PROJECT, project_id="alpha")

# Every scope a resolver can address: one of each kind, subjects included.
SCOPES = (APPLICATION, PERSONAL, PEOPLE, PROJECT)


@pytest.fixture
def production() -> ScopePaths:
    return ScopePaths.production(home=HOME, projects={"alpha": REPO})


@pytest.fixture
def rooted(tmp_path: Path) -> ScopePaths:
    return ScopePaths.rooted(tmp_path)


def _resolved(paths: ScopePaths, tier: Tier | None) -> list[tuple[DataScope, str, Path]]:
    """Every (scope, artifact, path) triple at one tier, across every scope.

    `None` means every tier, retention-managed artifacts included.
    """
    return [
        (scope, artifact, paths.resolve(scope, artifact))
        for scope in SCOPES
        for artifact in sorted(artifacts_in(scope.kind))
        if tier is None or ARTIFACT_TIER.get(artifact) is tier
    ]


def _personal_only() -> set[str]:
    """Artifacts the scope table permits in the personal scope and nowhere else."""
    elsewhere: set[str] = set()
    for kind in ScopeKind:
        if kind is not ScopeKind.PERSONAL:
            elsewhere |= artifacts_in(kind)
    return set(artifacts_in(ScopeKind.PERSONAL)) - elsewhere


# ── Matrix rows ──────────────────────────────────────────────────────────────


def test_application_scope_holds_the_disclosure_ledger(production):
    """AD-38 — one ledger, at the application scope's root, outside every repo."""
    expected = HOME / ".pm-ai" / "disclosure.md"
    assert production.disclosure_ledger == expected
    assert production.resolve(APPLICATION, "disclosure.md") == expected


def test_personal_scope_event_log_lives_in_the_sovereign_hub(production):
    """The PM's own audit trail, in the scope that survives a company transition."""
    assert (
        production.resolve(PERSONAL, "event_log/")
        == HOME / ".manager-ai" / "memory" / "event_log"
    )


def test_project_event_log_lives_in_the_repository_it_was_enrolled_from(production):
    """AD-11 — the repository path was supplied, not found by scanning for it."""
    assert (
        production.resolve(PROJECT, "event_log/")
        == REPO / ".project-ai" / "memory" / "event_log"
    )


def test_people_scope_is_one_directory_per_report_under_the_enclave(production):
    """AD-31 — deleted on leaving the role, so it is a directory and not a tag."""
    assert production.scope_root(PEOPLE) == HOME / ".pm-ai" / "private" / "people" / "p1"


def test_operational_store_sits_outside_every_markdown_tree(production):
    """AD-3/AD-5 — Tier 2 is a file, not a subdirectory of append-only truth.

    If the job queue lived inside a Markdown tree, "back up Tier 1" and "rebuild
    Tier 3" would both have to reason about which files in that tree are which.
    """
    store = production.operational_store
    assert store == HOME / ".pm-ai" / "private" / "operational.db"

    truth = _resolved(production, Tier.TRUTH)
    assert truth, "no Tier 1 artifacts resolved — this check would pass vacuously"
    for scope, artifact, markdown in truth:
        assert not store.is_relative_to(markdown), (
            f"AD-3: the operational store {store} lies inside the Tier 1 tree "
            f"{artifact} of {scope}, so a Markdown-tree operation can reach it."
        )


def test_tier_three_shares_no_file_or_directory_with_tier_two(production):
    """AD-3 — `pm-ai reindex` deletes Tier 3, and must not be able to reach Tier 2.

    The original defect was one `event_telemetry.db` holding both, which made
    "rebuild Tier 3 only" unimplementable while its test stayed green.
    """
    tier_two = (production.operational_store, production.personal_analytics_store)
    tier_three = (production.derived_store, production.vector_index)

    # The acceptance criterion in its strongest form: no delete of one path,
    # recursive or not, can remove another.
    for keep in tier_two:
        for drop in tier_three:
            assert keep != drop
            assert not keep.is_relative_to(drop), f"rebuilding {drop} would remove {keep}"
            assert not drop.is_relative_to(keep), f"deleting {keep} would remove {drop}"

    assert production.derived_store != production.vector_index
    assert production.operational_store != production.personal_analytics_store


def test_rooted_resolver_reproduces_production_structure_beneath_its_root(
    tmp_path, rooted, production
):
    """The test factory is the real layout relocated, not a parallel fake of it."""
    resolved: list[Path] = []
    covered: set[str] = set()
    for scope in SCOPES:
        assert rooted.scope_root(scope).is_relative_to(tmp_path)
        for artifact in sorted(artifacts_in(scope.kind)):
            path = rooted.resolve(scope, artifact)
            resolved.append(path)
            covered.add(artifact)
            assert path.is_absolute()
            assert path.is_relative_to(tmp_path), f"{artifact} in {scope} escaped the root"
            assert "~" not in path.parts, f"{path} carries an unexpanded ~"
            assert ".." not in path.parts, f"{path} carries an unnormalized .."
            # Same relative structure as production: identical path below the
            # scope root, which is the part any caller actually depends on.
            assert path.relative_to(rooted.scope_root(scope)) == production.resolve(
                scope, artifact
            ).relative_to(production.scope_root(scope))

    assert len(set(resolved)) == len(resolved), "two artifacts resolved to one path"
    # Sets, not counts: `resolved` counts (scope, artifact) pairs, so comparing
    # its length against a count of artifacts is an assertion that cannot fail.
    unreachable = (set(ARTIFACT_TIER) | RETENTION_MANAGED) - covered
    assert not unreachable, f"no scope resolves {sorted(unreachable)}"


def test_project_scope_without_a_project_id_never_reaches_the_resolver(production):
    """The check lives on `DataScope`; the resolver inherits it, unchanged.

    Built inside the `resolve` call deliberately: the matrix row is that the
    resolver adds nothing to this and hides nothing from it.
    """
    with pytest.raises(ValueError, match="PROJECT scope requires a project_id"):
        production.resolve(DataScope(ScopeKind.PROJECT), "event_log/")


# ── Subject ids are a trust boundary ─────────────────────────────────────────


def test_a_traversing_person_id_cannot_leave_the_enclave(production):
    """AD-31 — a report's record outside the enclave has none of its guarantees.

    `DataScope` cannot catch this: it is a domain type, and "is this a usable
    directory name" is a question only the layer owning the layout can ask.
    """
    escape = DataScope(ScopeKind.PEOPLE, person_id="../../../../tmp/evil")
    with pytest.raises(MalformedSubjectId):
        production.resolve(escape, "event_log/")
    with pytest.raises(MalformedSubjectId):
        production.scope_root(escape)


@pytest.mark.parametrize(
    "bad",
    [
        "   ",
        "../../../../tmp/evil",
        "..",
        ".hidden",
        ".project-ai",
        "a/b",
        "a\\b",
        "/absolute",
        "Alice",
        " alice",
        "alice ",
    ],
)
def test_a_subject_id_that_is_not_a_directory_name_is_refused(production, bad):
    """One helper, both subjects: a person id and a project id are the same risk."""
    with pytest.raises(MalformedSubjectId):
        production.scope_root(DataScope(ScopeKind.PEOPLE, person_id=bad))
    with pytest.raises(MalformedSubjectId):
        production.repository(bad)


def test_an_empty_project_id_is_refused(production):
    """`DataScope` catches the empty *scope*; `repository()` is reachable directly."""
    with pytest.raises(MalformedSubjectId):
        production.repository("")


def test_a_valid_subject_id_stays_beneath_its_scope(production):
    """The guard above must not have been bought by rejecting everything."""
    assert production.scope_root(PEOPLE).is_relative_to(production.people_root)
    assert production.people_root.is_relative_to(production.enclave_root)


# ── Normalization, which every containment check above rests on ──────────────


def test_a_dot_dot_cannot_survive_a_containment_check(tmp_path):
    """`Path.absolute()` does not normalize, so `is_relative_to` lies about `..`.

    This test states the premise first, because the failure it guards is not a
    wrong path — it is a *containment assertion that cannot fail*, anywhere in
    this file.
    """
    root = tmp_path / "root"
    escaping = root / "projects" / ".." / ".." / "escape"
    assert Path(escaping).absolute().is_relative_to(root), (
        "premise changed: .absolute() no longer reports an escaped path as contained"
    )

    paths = ScopePaths.production(home=escaping)
    assert ".." not in paths.application_root.parts
    assert not paths.application_root.is_relative_to(root)
    assert paths.application_root == tmp_path / "escape" / ".pm-ai"


def test_registry_repository_paths_are_expanded_and_absolute():
    """A registry is exactly where `~/code/alpha` and a relative path show up.

    Unexpanded, the first creates a directory literally named `~`; unresolved,
    the second means a different repository per working directory.
    """
    paths = ScopePaths.production(
        home=HOME, projects={"alpha": "~/code/alpha", "beta": "code/beta"}
    )
    for project_id in ("alpha", "beta"):
        repo = paths.repository(project_id)
        assert repo.is_absolute(), f"{project_id} resolved to a relative path"
        assert "~" not in repo.parts
        assert ".." not in repo.parts
    assert paths.repository("alpha") == Path.home() / "code" / "alpha"


def test_rooted_refuses_a_repository_outside_its_root(tmp_path):
    """The factory's whole value is that everything it returns is beneath one dir."""
    with pytest.raises(RepositoryOutsideRoot):
        ScopePaths.rooted(tmp_path, projects={"beta": tmp_path.parent / "elsewhere"})
    with pytest.raises(RepositoryOutsideRoot):
        ScopePaths.rooted(tmp_path, projects={"beta": tmp_path / ".." / "escape"})

    inside = ScopePaths.rooted(tmp_path, projects={"beta": tmp_path / "repos" / "beta"})
    assert inside.repository("beta").is_relative_to(tmp_path)


# ── Consequences of the layout the matrix rows describe ──────────────────────


def test_every_tiered_and_retention_managed_artifact_has_a_home():
    """An artifact with no path is an artifact whose path the next caller invents."""
    placed = {a for kind in ScopeKind for a in artifacts_in(kind)}
    missing = (set(ARTIFACT_TIER) | RETENTION_MANAGED) - placed
    assert not missing, f"no scope holds {sorted(missing)}"


def test_no_tier_one_artifact_lives_inside_a_rebuildable_one(production):
    """AD-3 — `pm-ai reindex` deletes Tier 3 recursively; Tier 1 is truth.

    The people scope legitimately lives inside the same `private/` enclave as the
    Tier-3 stores, so the enclave boundary proves nothing. What must hold is the
    narrower thing a rebuild actually does: it removes the artifacts
    `REBUILD_TARGETS` names, so no Tier-1 path may sit inside one of those.
    """
    rebuildable = _resolved(production, Tier.DERIVED)
    truth = _resolved(production, Tier.TRUTH)
    assert rebuildable and truth, "one of the tiers resolved nothing"

    for _, target, target_path in rebuildable:
        for scope, artifact, path in truth:
            assert path != target_path, f"{artifact} and {target} are one path"
            assert not path.is_relative_to(target_path), (
                f"AD-3: rebuilding {target} would delete {artifact} in {scope} "
                f"({path}), which is Tier 1 truth and cannot be reconstructed."
            )


def test_gitignore_rules_cover_the_paths_the_resolver_returns(production):
    """AD-38 — the rule and the path must be the same place, or the rule is a lie.

    `assert_capture_dir_ignored` reports "protected" when it finds its rule in a
    `.gitignore`. If the resolver puts the capture directory somewhere the rule
    does not cover, that check passes over a directory git tracks, and verbatim
    meeting transcripts are committed to the employer's repository.
    """
    repo = production.repository("alpha")
    for artifact, rule in GITIGNORE_REQUIRED.items():
        assert ScopeKind.PROJECT in _scopes_of(artifact), (
            f"{artifact} has a .gitignore rule but no path in a committed scope"
        )
        path = production.resolve(PROJECT, artifact)
        assert path.relative_to(repo) == Path(rule.strip("/")), (
            f"AD-38: {artifact} resolves to {path.relative_to(repo)} inside the "
            f"repository, but its .gitignore rule only covers {rule!r}."
        )


def _scopes_of(artifact: str) -> set[ScopeKind]:
    return {kind for kind in ScopeKind if artifact in artifacts_in(kind)}


def test_the_resolver_agrees_with_the_bare_string_it_replaces():
    """`pm_ai.domain.disclosure.DISCLOSURE_LEDGER_PATH` is the same file.

    The domain may not import the platform (AD-30), so that constant stays where
    it is. This is the one test that deliberately reads the ambient home: the
    constant spells `~`, and agreeing with it is the point.
    """
    from pm_ai.domain.disclosure import DISCLOSURE_LEDGER_PATH

    assert ScopePaths.production().disclosure_ledger == Path(
        DISCLOSURE_LEDGER_PATH
    ).expanduser()


def test_the_personal_only_set_matches_the_scope_table():
    """Intent and mechanism, cross-checked.

    `PERSONAL_SUBJECT_ARTIFACTS` states which artifacts have the PM as their
    subject; `_HOMES` implements it. Granting one of them a second scope changes
    the mechanism only, and this is what notices.
    """
    assert _personal_only() == set(PERSONAL_SUBJECT_ARTIFACTS)


def test_personal_material_has_no_path_in_a_committed_scope(production):
    """AD-31 — the boundary refuses the pair, rather than trusting a caller.

    Derived from the declared set rather than a list written here, so an artifact
    added to the personal scope tomorrow is covered without editing this test.
    """
    assert PERSONAL_SUBJECT_ARTIFACTS, "vacuous — nothing declared personal"
    for artifact in sorted(PERSONAL_SUBJECT_ARTIFACTS):
        with pytest.raises(ArtifactNotInScope):
            production.resolve(PROJECT, artifact)
        assert _scopes_of(artifact) == {ScopeKind.PERSONAL}


def test_an_unregistered_project_is_an_error_not_a_guess(production):
    """AD-11 — a resolver that can invent a repository path can enrol one."""
    with pytest.raises(UnknownProject):
        production.resolve(DataScope(ScopeKind.PROJECT, project_id="beta"), "event_log/")


def test_an_unknown_artifact_does_not_fall_back_to_the_scope_root(production):
    """Guessing is how one file acquires two paths and therefore two tiers."""
    with pytest.raises(UnknownArtifact):
        production.resolve(APPLICATION, "notes.md")


def test_is_directory_refuses_an_artifact_that_does_not_exist():
    """Otherwise it is usable as a validity check and answers confidently wrong."""
    assert is_directory("event_log/")
    assert not is_directory("disclosure.md")
    with pytest.raises(UnknownArtifact):
        is_directory("nonsense/")


def test_every_refusal_is_catchable_as_one_error(production):
    """A caller wiring this into storage should not enumerate five exception types.

    Two bases, and the domain one is the load-bearing half: `storage`, `core`,
    and `surfaces` are forbidden to import this module, so `ScopePathError` is
    not a name they can write in an `except` clause. Without
    `ScopeResolutionError` in `pm_ai.domain`, the only way to survive a resolver
    refusal outside `platform` and `app` is to catch `Exception`.
    """
    refusals = (
        lambda: production.resolve(APPLICATION, "notes.md"),
        lambda: production.resolve(PROJECT, "strategic_goals.md"),
        lambda: production.repository("beta"),
        lambda: production.repository("../evil"),
        lambda: ScopePaths.rooted("/tmp/rooted-check", projects={"b": "/elsewhere"}),
    )
    for refuse in refusals:
        with pytest.raises(ScopePathError):
            refuse()
        with pytest.raises(ScopeResolutionError):
            refuse()

    assert issubclass(ScopePathError, ScopeResolutionError)

    # Not `KeyError`: its `__str__` is `repr` of the argument, so a multi-line
    # explanation arrives in the traceback as an escaped one-liner.
    for error in (
        UnknownArtifact,
        UnknownProject,
        ArtifactNotInScope,
        MalformedSubjectId,
        RepositoryOutsideRoot,
    ):
        assert issubclass(error, ScopePathError)
        assert not issubclass(error, KeyError), f"{error.__name__} repr-escapes its message"


def test_create_makes_directories_and_never_a_file(rooted):
    """AD-5 — the resolver may prepare a place; only storage puts content in it."""
    directory = rooted.resolve(PROJECT, "event_log/", create=True)
    assert directory.is_dir() and is_directory("event_log/")

    ledger = rooted.resolve(PROJECT, "commitments_log.md", create=True)
    assert ledger.parent.is_dir()
    assert not ledger.exists(), "the resolver wrote a file; that is the single writer's job"

    store = rooted.resolve(APPLICATION, "operational.db", create=True)
    assert store.parent.is_dir()
    assert not store.exists()


def test_direct_construction_is_normalized_and_the_result_is_no_dict_key():
    """The factories are the door, but this one is unlocked, so it must be safe."""
    paths = ScopePaths(
        application_root="rel/.pm-ai",
        personal_root="rel/.manager-ai",
        project_roots={"alpha": "~/code/alpha"},
    )
    assert paths.application_root.is_absolute()
    assert paths.repository("alpha") == Path.home() / "code" / "alpha"
    with pytest.raises(TypeError):
        paths.project_roots["beta"] = Path("/x")  # type: ignore[index]
    # Deliberately unhashable: it carries a mapping, and `frozen=True` would
    # otherwise advertise a `__hash__` that raises.
    with pytest.raises(TypeError):
        hash(paths)


def test_home_override_is_expanded():
    """A `~` reaching the filesystem creates a directory literally named `~`."""
    paths = ScopePaths.production(home="~")
    assert "~" not in paths.application_root.parts
    assert paths.application_root == Path.home() / ".pm-ai"
