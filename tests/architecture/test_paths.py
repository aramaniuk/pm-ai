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

# The durability marker a node carries when it is outside the tier model. It
# lives with the trees, because that is where it is declared.
from pm_ai.domain.scope_model import FOREIGN_ROOTS, OutsideTierModel
from pm_ai.domain.storage_tiers import (
    ARTIFACT_TIER,
    DIAGNOSTIC_ONLY,
    GITIGNORED,
    gitignore_rule_for,
    RETENTION_MANAGED,
    ScopeResolutionError,
    Tier,
)
from pm_ai.platform.paths import (
    _ADDRESS,
    ForeignScopeRoot,
    PERSONAL_SUBJECT_ARTIFACTS,
    SCOPE_TREES,
    AmbiguousArtifact,
    ArtifactNotInScope,
    Collection,
    Dir,
    File,
    MalformedLayout,
    MalformedSubjectId,
    RepositoryOutsideRoot,
    ScopePathError,
    ScopePaths,
    UnknownArtifact,
    UnknownProject,
    artifacts_in,
    declared_nodes,
    is_directory,
    scopes_of,
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
        (scope, artifact, _path_of(paths, scope, artifact))
        for scope in SCOPES
        for artifact in sorted(artifacts_in(scope.kind))
        if tier is None or ARTIFACT_TIER.get(artifact) is tier
    ]


def _path_of(paths: ScopePaths, scope: DataScope, artifact: str) -> Path:
    """Where a declared node sits, including the ones `resolve` refuses.

    `people/` is Tier 1 and shares the `private/` enclave with the Tier-3 stores,
    so the containment assertions below have to include it — but it is a foreign
    scope root, and addressing it through `resolve` is exactly what was closed on
    2026-08-28. Routed by `FOREIGN_ROOTS` rather than by name, so a second such
    node is covered without editing this helper.
    """
    if artifact in FOREIGN_ROOTS:
        return paths.foreign_scope_root(artifact)
    return paths.resolve(scope, artifact)


def _personal_only() -> set[str]:
    """Artifacts the scope table permits in the personal scope and nowhere else."""
    elsewhere: set[str] = set()
    for kind in ScopeKind:
        if kind is not ScopeKind.PERSONAL:
            elsewhere |= artifacts_in(kind)
    return set(artifacts_in(ScopeKind.PERSONAL)) - elsewhere


# ── The whole resolution table, pinned to literal paths ──────────────────────

# Every (scope kind, artifact key) pair the resolver answers, and the absolute
# path it must answer with. The scope subjects are `p1` and `alpha`, matching the
# resolver the test below builds from the same literals.
RESOLUTION_TABLE: dict[tuple[ScopeKind, str], str] = {
    # ── application — /home/pm/.pm-ai/ (scope-model.md §A) ───────────────────
    (ScopeKind.APPLICATION, "config.toml"):
        "/home/pm/.pm-ai/config.toml",
    (ScopeKind.APPLICATION, "disclosure.md"):
        "/home/pm/.pm-ai/disclosure.md",
    (ScopeKind.APPLICATION, "projects.toml"):
        "/home/pm/.pm-ai/projects.toml",
    (ScopeKind.APPLICATION, "connectors/"):
        "/home/pm/.pm-ai/connectors",
    (ScopeKind.APPLICATION, "logs/"):
        "/home/pm/.pm-ai/logs",
    # The application-scope audit trail. `scope-model.md` §A draws no `memory/`;
    # its prose says every scope holds its own `event_log/`, and this is where
    # the daemon has always written one.
    (ScopeKind.APPLICATION, "memory/"):
        "/home/pm/.pm-ai/memory",
    (ScopeKind.APPLICATION, "event_log/"):
        "/home/pm/.pm-ai/memory/event_log",
    (ScopeKind.APPLICATION, "private/"):
        "/home/pm/.pm-ai/private",
    # Pinned like any other node — its location is load-bearing, since it shares
    # the `private/` enclave with the Tier-3 stores. `resolve` refuses it (it is
    # a foreign scope root); `_path_of` routes to `foreign_scope_root`.
    (ScopeKind.APPLICATION, "people/"):
        "/home/pm/.pm-ai/private/people",
    (ScopeKind.APPLICATION, "operational.db"):
        "/home/pm/.pm-ai/private/operational.db",
    (ScopeKind.APPLICATION, "event_index.db"):
        "/home/pm/.pm-ai/private/event_index.db",
    (ScopeKind.APPLICATION, "commitment_index.db"):
        "/home/pm/.pm-ai/private/commitment_index.db",
    (ScopeKind.APPLICATION, "config.json"):
        "/home/pm/.pm-ai/private/config.json",
    (ScopeKind.APPLICATION, "vector_index/"):
        "/home/pm/.pm-ai/private/vector_index",
    # ── personal — /home/pm/.manager-ai/ (scope-model.md §B) ─────────────────
    (ScopeKind.PERSONAL, "rules/"):
        "/home/pm/.manager-ai/rules",
    (ScopeKind.PERSONAL, "manager_principles.md"):
        "/home/pm/.manager-ai/rules/manager_principles.md",
    # The personal coach persona. The project scope declares its own
    # `rules/persona.md` with different content, and the two rows below are the
    # reason the layout is one tree per scope rather than one global name table.
    (ScopeKind.PERSONAL, "persona.md"):
        "/home/pm/.manager-ai/rules/persona.md",
    (ScopeKind.PERSONAL, "communication_preferences.md"):
        "/home/pm/.manager-ai/rules/communication_preferences.md",
    (ScopeKind.PERSONAL, "article_sources.md"):
        "/home/pm/.manager-ai/rules/article_sources.md",
    (ScopeKind.PERSONAL, "memory/"):
        "/home/pm/.manager-ai/memory",
    (ScopeKind.PERSONAL, "daily_dashboard.md"):
        "/home/pm/.manager-ai/memory/daily_dashboard.md",
    (ScopeKind.PERSONAL, "strategic_goals.md"):
        "/home/pm/.manager-ai/memory/strategic_goals.md",
    (ScopeKind.PERSONAL, "coaching_1on1_history.md"):
        "/home/pm/.manager-ai/memory/coaching_1on1_history.md",
    (ScopeKind.PERSONAL, "event_log/"):
        "/home/pm/.manager-ai/memory/event_log",
    (ScopeKind.PERSONAL, "meetings/"):
        "/home/pm/.manager-ai/memory/meetings",
    (ScopeKind.PERSONAL, "skills/"):
        "/home/pm/.manager-ai/skills",
    (ScopeKind.PERSONAL, "telemetry/"):
        "/home/pm/.manager-ai/skills/telemetry",
    (ScopeKind.PERSONAL, "private/"):
        "/home/pm/.manager-ai/private",
    (ScopeKind.PERSONAL, "telegram_cache/"):
        "/home/pm/.manager-ai/private/telegram_cache",
    (ScopeKind.PERSONAL, "personal_analytics.db"):
        "/home/pm/.manager-ai/private/personal_analytics.db",
    (ScopeKind.PERSONAL, "transcripts/"):
        "/home/pm/.manager-ai/transcripts",
    # ── people — /home/pm/.pm-ai/private/people/p1/ ──────────────────────────
    (ScopeKind.PEOPLE, "memory/"):
        "/home/pm/.pm-ai/private/people/p1/memory",
    (ScopeKind.PEOPLE, "event_log/"):
        "/home/pm/.pm-ai/private/people/p1/memory/event_log",
    (ScopeKind.PEOPLE, "meetings/"):
        "/home/pm/.pm-ai/private/people/p1/memory/meetings",
    (ScopeKind.PEOPLE, "transcripts/"):
        "/home/pm/.pm-ai/private/people/p1/transcripts",
    # ── project — /repositories/alpha/.project-ai/ (scope-model.md §C) ───────
    (ScopeKind.PROJECT, "rules/"):
        "/repositories/alpha/.project-ai/rules",
    (ScopeKind.PROJECT, "persona.md"):
        "/repositories/alpha/.project-ai/rules/persona.md",
    (ScopeKind.PROJECT, "conventions.md"):
        "/repositories/alpha/.project-ai/rules/conventions.md",
    (ScopeKind.PROJECT, "engineering_specs.md"):
        "/repositories/alpha/.project-ai/rules/engineering_specs.md",
    (ScopeKind.PROJECT, "memory/"):
        "/repositories/alpha/.project-ai/memory",
    (ScopeKind.PROJECT, "daily_dashboard.md"):
        "/repositories/alpha/.project-ai/memory/daily_dashboard.md",
    (ScopeKind.PROJECT, "commitments_log.md"):
        "/repositories/alpha/.project-ai/memory/commitments_log.md",
    (ScopeKind.PROJECT, "meetings/"):
        "/repositories/alpha/.project-ai/memory/meetings",
    (ScopeKind.PROJECT, "event_log/"):
        "/repositories/alpha/.project-ai/memory/event_log",
    (ScopeKind.PROJECT, "skills/"):
        "/repositories/alpha/.project-ai/skills",
    (ScopeKind.PROJECT, "transcripts/"):
        "/repositories/alpha/.project-ai/transcripts",
}


def test_every_scope_and_artifact_resolves_to_its_pinned_path():
    """The full layout, as literal strings, so relocating anything fails here.

    The expectations are hand-written literals rather than paths composed from
    `ARTIFACTS`, `ScopeDir`, or `SCOPE_SKELETON`, and that is the whole point: a
    table derived from the structure under test moves when the structure moves,
    so it cannot notice the move. Every other test in this file pins one
    interesting row and leaves the rest to the reader — which is how
    `config.toml` came to be an artifact whose directory nothing asserted, free
    to migrate from the scope root into `memory/` with the suite still green.

    A relocation is not a cosmetic change. Each of these paths is a place a
    previous version of the daemon has already written to, so moving one orphans
    real content silently: the new path is empty, nothing errors, and the
    Markdown truth AD-3 promises is unrecoverable simply reads as absent.

    The completeness check at the end is what keeps the literals honest. Without
    it a new artifact, or an artifact quietly losing a scope, would be a pair
    this table never mentions and therefore never checks.
    """
    paths = ScopePaths.production(
        home="/home/pm", projects={"alpha": "/repositories/alpha"}
    )
    scopes = {
        ScopeKind.APPLICATION: DataScope(ScopeKind.APPLICATION),
        ScopeKind.PERSONAL: DataScope(ScopeKind.PERSONAL),
        ScopeKind.PEOPLE: DataScope(ScopeKind.PEOPLE, person_id="p1"),
        ScopeKind.PROJECT: DataScope(ScopeKind.PROJECT, project_id="alpha"),
    }

    for (kind, artifact), expected in sorted(
        RESOLUTION_TABLE.items(), key=lambda item: (item[0][0].value, item[0][1])
    ):
        assert _path_of(paths, scopes[kind], artifact) == Path(expected), (
            f"{artifact} in the {kind.value} scope moved: it now resolves to "
            f"{_path_of(paths, scopes[kind], artifact)}, not {expected}. If the move "
            f"is intended, migrating whatever the old path already holds is part "
            f"of it."
        )

    # Not derived from the table, so a pair the table forgot is a failure rather
    # than a silent omission.
    reachable = {
        (kind, artifact) for kind in ScopeKind for artifact in artifacts_in(kind)
    }
    assert reachable == set(RESOLUTION_TABLE), (
        f"the pinned table no longer matches what the resolver answers. "
        f"Unpinned: "
        f"{sorted((k.value, a) for k, a in reachable - set(RESOLUTION_TABLE))}; "
        f"gone: {sorted((k.value, a) for k, a in set(RESOLUTION_TABLE) - reachable)}"
    )


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
    tier_three = (
        production.event_index_store,
        production.commitment_index_store,
        production.vector_index,
    )

    # The acceptance criterion in its strongest form: no delete of one path,
    # recursive or not, can remove another.
    for keep in tier_two:
        for drop in tier_three:
            assert keep != drop
            assert not keep.is_relative_to(drop), f"rebuilding {drop} would remove {keep}"
            assert not drop.is_relative_to(keep), f"deleting {keep} would remove {drop}"

    assert len(set(tier_three)) == len(tier_three), (
        "the two indexes and the vector index are three distinct paths, so "
        "rebuilding one cannot delete another"
    )
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
            path = _path_of(rooted, scope, artifact)
            resolved.append(path)
            covered.add(artifact)
            assert path.is_absolute()
            assert path.is_relative_to(tmp_path), f"{artifact} in {scope} escaped the root"
            assert "~" not in path.parts, f"{path} carries an unexpanded ~"
            assert ".." not in path.parts, f"{path} carries an unnormalized .."
            # Same relative structure as production: identical path below the
            # scope root, which is the part any caller actually depends on.
            assert path.relative_to(rooted.scope_root(scope)) == _path_of(production,
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

    Git is what the write path asks, so this is no longer a check on a matcher —
    it is a check on the *instruction*. `assert_capture_dir_untracked` tells the
    operator to add `rule` when git says the directory is not excluded. If the
    resolver puts the capture directory somewhere that rule does not cover, the
    operator adds it, git still tracks the directory, and the refusal repeats
    forever with no way to satisfy it.
    """
    repo = production.repository("alpha")
    for artifact in GITIGNORED[ScopeKind.PROJECT]:
        rule = gitignore_rule_for(production.resolve(PROJECT, artifact), repository=repo)
        assert ScopeKind.PROJECT in _scopes_of(artifact), (
            f"{artifact} has a .gitignore rule but no path in a committed scope"
        )
        path = production.resolve(PROJECT, artifact)
        assert path.relative_to(repo) == Path(rule.strip("/")), (
            f"AD-38: {artifact} resolves to {path.relative_to(repo)} inside the "
            f"repository, but its .gitignore rule only covers {rule!r}."
        )


def test_the_gitignore_is_pinned_to_the_repository_root():
    """The exclusion file the capture guard names, as a literal string.

    Hand-written rather than composed from `repository()` or `GITIGNORE_FILENAME`,
    for the reason `test_every_scope_and_artifact_resolves_to_its_pinned_path`
    gives: an expectation derived from the code under test moves when the code
    moves and therefore cannot fail. This accessor had exactly that problem —
    changing it to `repository(project_id).parent / ".gitignore"` left the entire
    suite green, because the one test that used it wrote the rule *through* this
    accessor before reading it back, so the two agreed wherever it pointed.

    Nothing about this path is arbitrary. Git reads the exclusion file at the root
    of the worktree; a `.gitignore` one directory up governs a different
    repository, and one inside `.project-ai/` anchors its rules to that
    subdirectory instead — so `/.project-ai/transcripts/` would match nothing.
    """
    paths = ScopePaths.production(home="/home/pm", projects={"alpha": "/repositories/alpha"})

    assert paths.gitignore("alpha") == Path("/repositories/alpha/.gitignore")


def test_the_capture_directory_lies_inside_the_repository_it_is_checked_against(production):
    """The guard asks git about one path, using the `.gitignore` of another.

    Both come from this resolver, and the guard is only meaningful if they are the
    same repository: a verdict obtained from one worktree says nothing about a
    capture written into another. This is the relation the pinned literals above
    cannot state, because either of them could move consistently with the other.
    """
    repository = production.repository("alpha")
    capture = production.resolve(PROJECT, "transcripts/")
    exclusion = production.gitignore("alpha")

    assert capture.is_relative_to(repository), (
        f"the capture directory {capture} is not inside the repository "
        f"{repository} whose git state the guard consults"
    )
    assert exclusion.parent == repository, (
        f"{exclusion} does not sit at the root of {repository}, so git reads its "
        f"rules relative to somewhere else"
    )
    assert not exclusion.is_relative_to(capture)


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
    subject; each `Artifact`'s own scope set implements it. Granting one of them
    a second scope changes the mechanism only, and this is what notices.
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
        # The capture guard reads this one, and a fail-soft reimplementation that
        # returned `<unregistered>/.gitignore` instead of raising would have the
        # writer tell an operator to add a rule to a repository that was never
        # registered — for a project the daemon must refuse outright (AD-11).
        lambda: production.gitignore("beta"),
        lambda: production.gitignore("../evil"),
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


# ── The three node types, and what each one promises ─────────────────────────


def test_a_collection_cannot_declare_an_artifact_inside_it():
    """The point of the type: an unenumerable directory declares nothing.

    A flat artifact set could not say whether `skills/` held two declared
    modules or two hundred undeclared ones, and the reader had no way to tell an
    omission from a decision. `Collection` is that decision, so a `File` or `Dir`
    smuggled inside one would put the ambiguity straight back.
    """
    for intruder in (
        File("anti_burnout_shield.py", Tier.TRUTH, encrypted=False, gitignored=False),
        Dir("x", (File("y.md", Tier.TRUTH, encrypted=False, gitignored=False),)),
    ):
        with pytest.raises(MalformedLayout, match="cannot enumerate"):
            Collection("skills", Tier.TRUTH, (intruder,), encrypted=False, gitignored=False)  # type: ignore[arg-type]

    # A nested Collection is the same statement one level down, and is the one
    # thing that may sit here: `skills/telemetry/` is a known sub-namespace whose
    # own contents are as arbitrary as its siblings'.
    assert Collection(
        "skills",
        Tier.TRUTH,
        (Collection("telemetry", Tier.TRUTH, encrypted=False, gitignored=False),),
        encrypted=False,
        gitignored=False,
    ).children


def test_no_declared_artifact_sits_inside_a_collection_in_any_real_tree():
    """The construction-time check, re-asserted over the trees as declared.

    `Collection.__post_init__` guards its own arguments; this walks what was
    actually built, so a future node type or a hand-assembled tuple cannot slip
    a declared artifact into a namespace that says it declares nothing.
    """
    checked = 0
    for tree in SCOPE_TREES.values():
        stack = list(tree)
        while stack:
            node = stack.pop()
            stack.extend(node.children)
            if isinstance(node, Collection):
                checked += 1
                assert all(isinstance(m, Collection) for m in node.children), (
                    f"{node.name}/ is a Collection holding a declared artifact"
                )
    assert checked, "no Collection in any tree — this check would pass vacuously"


def test_a_directory_with_no_declared_members_must_say_it_is_a_collection():
    """`Dir` means "the members are listed here", so an empty one is a lie."""
    with pytest.raises(MalformedLayout, match="declares no members"):
        Dir("memory", ())
    with pytest.raises(MalformedLayout, match="one path segment"):
        File("rules/persona.md", Tier.TRUTH, encrypted=False, gitignored=False)


def test_a_declared_artifact_carries_its_durability_on_the_node():
    """The tier tables are a projection of the trees, not a second structure.

    They used to be a flat, hand-written dict in another module, kept in step
    with the trees by a pair of import-time assertions that existed only because
    the two could drift. Durability is now a required field of the node, so the
    drift is unrepresentable in both directions: a `File` without a tier does not
    construct, and no key can appear in `ARTIFACT_TIER` without a node to derive
    it from.
    """
    with pytest.raises(TypeError):
        File("notes.md", encrypted=False, gitignored=False)  # type: ignore[call-arg]
    with pytest.raises(MalformedLayout, match="exactly one Tier"):
        File("notes.md", OutsideTierModel.RETENTION_MANAGED, encrypted=False, gitignored=False)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Collection("logs", encrypted=False, gitignored=False)  # type: ignore[call-arg]
    with pytest.raises(MalformedLayout, match="durability"):
        Collection("logs", "diagnostics", encrypted=False, gitignored=False)  # type: ignore[arg-type]

    # Every row of every projection came from a node that declared it, and every
    # node that declared one is in the matching projection.
    checked = 0
    for kind in ScopeKind:
        for placement in declared_nodes(kind):
            durability = placement.node.durability
            if durability is None:
                assert isinstance(placement.node, Dir), (
                    f"{placement.key} declares no durability and is not structure"
                )
                continue
            checked += 1
            key = placement.node.key
            if isinstance(durability, Tier):
                assert ARTIFACT_TIER[key] is durability
            elif durability is OutsideTierModel.RETENTION_MANAGED:
                assert key in RETENTION_MANAGED
            else:
                assert key in DIAGNOSTIC_ONLY
    assert checked, "no node declared a durability — this check would pass vacuously"

    # And nothing in a projection is missing its node.
    keys = {p.node.key for kind in ScopeKind for p in declared_nodes(kind)}
    assert (set(ARTIFACT_TIER) | RETENTION_MANAGED | DIAGNOSTIC_ONLY) <= keys


def test_every_declared_artifact_is_tiered_or_deliberately_excluded():
    """The oversight the tier tables exist to catch, stated over the trees.

    `personal_analytics.db` held months of burnout history while belonging to
    neither the backup set nor the rebuild set, and nothing noticed because
    nothing walked the layout asking. A `Dir` is exempt — it is structure whose
    declared members carry the tiers — but a `File` or a `Collection` is the
    artifact itself.
    """
    accounted = set(ARTIFACT_TIER) | RETENTION_MANAGED | DIAGNOSTIC_ONLY
    unaccounted = [
        f"{kind.value}:{placement.key}"
        for kind in ScopeKind
        for placement in declared_nodes(kind)
        if not isinstance(placement.node, Dir) and placement.key not in accounted
    ]
    assert not unaccounted, (
        f"{sorted(unaccounted)} have a path and no tier, so no backup covers "
        f"them and no rebuild reproduces them"
    )


def test_the_three_exclusion_sets_are_pairwise_disjoint():
    """Exactly one answer to "what happens to this on backup and on rebuild".

    `logs/` is excluded for a different reason than `transcripts/` — diagnostics
    that are not state, versus raw captures under NFR-09's purge — and folding
    the two together would put `logs/` under a retention promise nothing
    implements.
    """
    tiered = set(ARTIFACT_TIER)
    assert not (tiered & RETENTION_MANAGED)
    assert not (tiered & DIAGNOSTIC_ONLY)
    assert not (RETENTION_MANAGED & DIAGNOSTIC_ONLY)
    assert DIAGNOSTIC_ONLY == {"logs/"}, (
        "the spine calls logs/ diagnostics rather than a tier; anything else "
        "arriving here needs the same argument made explicitly"
    )


# ── Addressing: relative paths, basenames, and refusing to guess ─────────────


def test_an_artifact_resolves_the_same_by_relative_path_and_by_basename(production):
    """Two spellings of one declaration, never two declarations.

    The relative path is the canonical form; the basename is accepted because
    `pm_ai.storage.service` passes `EVENT_LOG` and `ARTIFACT_TIER` is keyed by
    basename. If the two ever disagreed, a write and its tier would be talking
    about different files.
    """
    assert production.resolve(PERSONAL, "rules/persona.md") == production.resolve(
        PERSONAL, "persona.md"
    )
    assert production.resolve(PROJECT, "memory/event_log/") == production.resolve(
        PROJECT, "event_log/"
    )
    # `people/` is the exception, and it proves the same property harder: both
    # spellings reach one node, so both must be REFUSED identically. A string
    # check on the artifact name caught `people/` and missed `private/people/` —
    # the guard reappearing one spelling to the left — so it keys on the node.
    for spelling in ("people/", "private/people/"):
        with pytest.raises(ForeignScopeRoot):
            production.resolve(APPLICATION, spelling)
    # `foreign_scope_root` takes the canonical key only, like `artifacts_in`,
    # which reports one spelling per node. The two-spelling property is carried
    # by the refusal above — that is where it matters, because that is the
    # boundary a caller could have crossed.
    assert production.foreign_scope_root("people/") == (
        HOME / ".pm-ai" / "private" / "people"
    )


def test_the_same_basename_in_two_scopes_is_two_different_declarations(production):
    """The correctness ceiling the flat artifact set could not get past.

    `persona.md` is a personal coach persona in one scope and a project
    assistant persona in another, with different content and different
    audiences. A layout keyed by a globally unique name could not hold both, so
    one of them had to be undeclared — and an undeclared file is one whose path
    the next caller invents.
    """
    assert scopes_of("persona.md") == {ScopeKind.PERSONAL, ScopeKind.PROJECT}
    personal = production.resolve(PERSONAL, "persona.md")
    project = production.resolve(PROJECT, "persona.md")
    assert personal != project
    assert personal.is_relative_to(production.personal_root)
    assert project.is_relative_to(production.repository("alpha"))

    # And a scope that declares no such file says so, rather than answering with
    # a plausible path nothing wrote.
    with pytest.raises(ArtifactNotInScope):
        production.resolve(APPLICATION, "persona.md")
    with pytest.raises(ArtifactNotInScope):
        production.resolve(PEOPLE, "persona.md")


def test_an_ambiguous_basename_is_refused_rather_than_picked():
    """No scope has one today, which is exactly why this uses a synthetic tree.

    Picking either candidate would put a record at a plausible wrong path and
    nothing downstream could tell. The check has to exist before the collision
    does, because the day it arrives is the day it silently resolves.
    """
    from pm_ai.platform.paths import _index

    placements, address, ambiguous = _index(
        (
            Dir("rules", (File("notes.md", Tier.TRUTH, encrypted=False, gitignored=False),)),
            Dir("memory", (File("notes.md", Tier.TRUTH, encrypted=False, gitignored=False),)),
        )
    )
    assert ambiguous == {"notes.md"}, "the collision was not noticed"
    assert "notes.md" not in address, "an ambiguous basename resolved anyway"
    # Both remain addressable, by the spelling that distinguishes them.
    assert {p.key for p in placements} >= {"rules/notes.md", "memory/notes.md"}


def test_an_ambiguous_basename_refuses_at_the_resolver_too(production, monkeypatch):
    """`_index` noticing is only half of it; `resolve` must act on the notice.

    Injected rather than declared, for the same reason as above: the guard has
    to be wired before a real collision exists, or its first exercise is the
    incident.
    """
    import pm_ai.platform.paths as module

    monkeypatch.setattr(
        module,
        "_AMBIGUOUS",
        {**module._AMBIGUOUS, ScopeKind.PERSONAL: frozenset({"persona.md"})},
    )
    with pytest.raises(AmbiguousArtifact, match="more than one node"):
        production.resolve(PERSONAL, "persona.md")
    # Still addressable by the spelling that cannot be mistaken.
    assert production.resolve(PERSONAL, "rules/persona.md")
    assert issubclass(AmbiguousArtifact, ScopePathError)
    assert not issubclass(AmbiguousArtifact, KeyError)


def test_config_toml_stays_at_the_application_scope_root(production):
    """A relocation this file has to notice, not a cosmetic assertion.

    `config.toml` is what `pm-ai doctor` and the debug profile read. Moved into
    `memory/`, the new path is simply empty: nothing errors, and the daemon reads
    defaults while the operator's real settings sit somewhere nothing looks.
    """
    assert production.resolve(APPLICATION, "config.toml") == HOME / ".pm-ai" / "config.toml"
    assert production.resolve(APPLICATION, "config.toml").parent == production.scope_root(
        APPLICATION
    )
    # Not the credentials file, which is a different artifact in the enclave.
    assert production.resolve(APPLICATION, "config.json").parent == production.enclave_root


def test_the_project_registry_is_application_scoped(production):
    """AD-11 — the one door into the system, outside every repository."""
    assert production.project_registry == HOME / ".pm-ai" / "projects.toml"
    assert scopes_of("projects.toml") == {ScopeKind.APPLICATION}


# ── Foreign scope roots (2026-08-28) ─────────────────────────────────────────


def test_a_foreign_scope_root_is_not_addressable_under_any_label(production):
    """A node that is another scope's root is declared here and addressed there.

    `~/.pm-ai/private/people/` sits in the application tree so its tier and its
    git exclusion derive from that tree like any other node. What it *contains*
    is a direct report's record, and every protection for one keys on the PEOPLE
    label (`is_people`, AD-38) rather than on the directory. So while `resolve`
    answered for it under the application label, a caller could write a dossier
    into exactly the right place carrying a label under which none of those
    guards fire — the right directory with the wrong guarantees.

    Refused under *every* label, PEOPLE included: the container is never a write
    target, only the records inside it are, and those are reached as
    `DataScope(ScopeKind.PEOPLE, person_id=...)`.
    """
    assert FOREIGN_ROOTS, "no foreign roots declared — this check would be vacuous"

    for artifact, owner in FOREIGN_ROOTS.items():
        for kind in ScopeKind:
            scope = (
                DataScope(kind, person_id="p1")
                if kind is ScopeKind.PEOPLE
                else DataScope(kind, project_id="alpha")
                if kind is ScopeKind.PROJECT
                else DataScope(kind)
            )
            with pytest.raises(ScopeResolutionError):
                production.resolve(scope, artifact)
        assert owner is not ScopeKind.APPLICATION or artifact not in artifacts_in(owner)


def test_every_spelling_of_a_foreign_root_is_refused(production):
    """The refusal keys on the node, never on the artifact string.

    One node is addressable under several spellings — `people/` and
    `private/people/` both reach this one. A string check caught the first and
    missed the second, which is the same hole one spelling to the left. Caught
    while building the fix, and this is what keeps it caught.
    """
    canonical = "people/"
    spellings = [
        key
        for key, placement in _ADDRESS[ScopeKind.APPLICATION].items()
        if placement is _ADDRESS[ScopeKind.APPLICATION][canonical]
    ]
    assert len(spellings) > 1, (
        "the people node is reachable under one spelling only, so this test no "
        "longer covers the case it was written for"
    )
    for spelling in spellings:
        with pytest.raises(ForeignScopeRoot):
            production.resolve(APPLICATION, spelling)


def test_the_records_inside_a_foreign_root_still_resolve(production):
    """The refusal must not wall off the scope it protects.

    A guard that made a direct report's record unreachable would be caught by no
    test in this file, because nothing else here resolves under the PEOPLE label
    — and it would be discovered as a broken feature rather than a broken guard.
    """
    people = DataScope(ScopeKind.PEOPLE, person_id="p1")
    root = production.foreign_scope_root("people/")
    resolved = [production.resolve(people, a) for a in sorted(artifacts_in(ScopeKind.PEOPLE))]
    assert resolved, "the people tree declares nothing — the check is vacuous"
    for path in resolved:
        assert path.is_relative_to(root / "p1")
