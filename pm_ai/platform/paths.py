"""Anchoring the scope model to a real filesystem (AD-4, AD-26).

The layout itself — which artifacts exist, where each sits relative to its scope
root, and the durability promise each carries — is declared in
`pm_ai.domain.scope_model`. None of that touches the OS, so none of it belongs
here. What belongs here is the one thing that does: turning a scope and an
artifact key into an absolute path.

    APPLICATION  ~/.pm-ai/
    PERSONAL     ~/.manager-ai/
    PEOPLE       ~/.pm-ai/private/people/<person_id>/
    PROJECT      <repository>/.project-ai/

Those four roots, and a registered repository, are the only structure written
down in this module; everything below the root comes from the scope trees.

Two factories, and the difference between them is the point. `production()`
reads the real home directory and knows no project repository it was not told
about — AD-11 forbids discovering one by scanning the filesystem, so an
unregistered project is an error rather than a guess. `rooted()` puts all four
scopes beneath a directory the caller supplies, at the same relative structure,
so a test exercises the real layout instead of a parallel fake of it.

## Addressing

A key is the scope-relative path as declared in the tree —
`resolve(scope, "rules/persona.md")`. A bare basename is also accepted when it is
unambiguous within that scope, because `pm_ai.storage.service` passes
`EVENT_LOG` (`"event_log/"`) and `OPERATIONAL_DB`, and `ARTIFACT_TIER`'s keys are
basenames. A basename that names two nodes in one scope raises
`AmbiguousArtifact`: picking one is how a record acquires the wrong path
silently. A trailing slash means a directory, and it is derived from the node
type rather than typed by hand.

Because the trees are per scope, `resolve(PERSONAL, "persona.md")` and
`resolve(PROJECT, "persona.md")` are two different declarations that happen to
share a spelling — they resolve through different trees.

An unknown key is refused rather than composed as `scope_root / artifact`:
guessing is how the same file acquires two paths, and therefore two tiers.

## Subject ids are the trust boundary this module owns

Subject ids are interpolated into paths, which makes them a trust boundary: a
`person_id` of `../../..` would resolve outside the enclave that keeps a direct
report's record away from that report's peers. `DataScope` cannot catch it — it
is a domain type with no filesystem to reason about — so ids are validated as
directory names here, and every path this module composes is normalized so that
a `..` cannot survive into a containment check.

This module resolves paths and may create directories. It writes no file
contents: that is `StorageService`'s alone (AD-5), which is also why nothing here
opens a file.
"""

from __future__ import annotations

import os.path
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from pm_ai.domain.identity import DataScope, ScopeKind
# `GITIGNORE_FILENAME` is defined in `domain` rather than here: `pm_ai.storage`
# derives the path from git's reported working-tree root and may not import
# this package, so one definition serves both.
from pm_ai.domain.scope_model import (
    APPLICATION_DIRNAME,
    ENCLAVE_DIRNAME,
    FOREIGN_ROOTS,
    PEOPLE_DIRNAME,
    PERSONAL_DIRNAME,
    PROJECT_DIRNAME,
)
from pm_ai.domain.storage_tiers import GITIGNORE_FILENAME
from pm_ai.domain.scope_model import (
    ADDRESS as _ADDRESS,
    AMBIGUOUS as _AMBIGUOUS,
    APPLICATION_TREE,
    KEY_SCOPES as _KEY_SCOPES,
    KEYS as _KEYS,
    PATH_SEPARATORS as _SEPARATORS,
    PEOPLE_TREE,
    PERSONAL_SUBJECT_ARTIFACTS,
    PERSONAL_TREE,
    PLACEMENTS as _PLACEMENTS,
    PROJECT_TREE,
    SCOPE_TREES,
    Collection,
    Dir,
    File,
    LayoutNode,
    MalformedLayout,
    Placement,
    ScopeResolutionError,
    artifacts_in,
    declared_nodes,
    index_tree as _index,
)

# Re-exported so that "where does this live" stays one import for a caller, and
# so that moving the trees into `domain` did not move this module's surface. The
# declarations are `pm_ai.domain.scope_model`'s; the anchoring is this module's.
__all__ = [
    "APPLICATION_DIRNAME",
    "APPLICATION_TREE",
    "AmbiguousArtifact",
    "ArtifactNotInScope",
    "Collection",
    "Dir",
    "ENCLAVE_DIRNAME",
    "File",
    "LayoutNode",
    "MalformedLayout",
    "MalformedSubjectId",
    "PEOPLE_DIRNAME",
    "PEOPLE_TREE",
    "PERSONAL_DIRNAME",
    "PERSONAL_SUBJECT_ARTIFACTS",
    "PERSONAL_TREE",
    "PROJECT_DIRNAME",
    "PROJECT_TREE",
    "Placement",
    "ROOTED_PROJECTS_DIRNAME",
    "RepositoryOutsideRoot",
    "SCOPE_TREES",
    "ScopePathError",
    "ScopePaths",
    "UnknownArtifact",
    "UnknownProject",
    "artifacts_in",
    "declared_nodes",
    "is_directory",
    "scopes_of",
]

# ── Directory names ──────────────────────────────────────────────────────────
# The four scope roots, spelled once each. This is the whole of what this module
# knows about layout that the trees do not say.


# The exclusion file of the repository a project scope lives in. It sits *outside*
# `.project-ai/`, so it is not a node in any scope tree and cannot be addressed
# through `resolve` — and it is named here rather than in `pm_ai.storage` because
# a second spelling of a layout fact is a second layout (AD-4). The single writer
# reads it before writing a raw capture: the rule inside it is the only thing
# keeping verbatim minutes out of the team's repository.

# The gitignored enclave inside a scope. It holds the Tier-2 and Tier-3 stores
# and, under the application scope, the whole team-member scope — so it is a
# mixed directory, not a database-only one, and `private/people/p1/memory/` is
# Tier-1 Markdown living inside it.
#
# What keeps that safe is not the directory boundary but the shape of the
# operations: `pm-ai reindex` deletes the artifacts REBUILD_TARGETS names, by
# path, and never a containing directory. The invariant this module owes AD-3 is
# therefore the one asserted in tests/architecture/test_paths.py — no Tier-1
# path lies inside a Tier-3 one — and not "nothing else lives in `private/`".

# The team-member scope is stored *under* the application scope but is its own
# kind, because the rules that separate it from the sovereign personal scope
# cannot be written against a path (AD-31, UJ-4).

# Where `rooted()` puts a repository it was not given a path for.
ROOTED_PROJECTS_DIRNAME = "projects"


# ── Errors ───────────────────────────────────────────────────────────────────
# One base, so a caller wiring the resolver into storage can catch every way it
# can refuse in a single clause instead of enumerating them and missing the one
# added next. None of these subclass `KeyError`: its `__str__` is `repr` of the
# argument, which turns a multi-line explanation into an escaped one-liner in
# the traceback — the wording is the point of raising.
#
# `MalformedLayout` is the exception to the "declared here" rule: a tree that
# contradicts itself is refused where the tree is written, in
# `pm_ai.domain.scope_model`, and it shares `ScopeResolutionError` as its base so
# a caller still catches it with everything else.


class ScopePathError(ScopeResolutionError):
    """The resolver refused. Every failure below is one of these.

    The base is in `pm_ai.domain` so that `storage`, `core`, and `surfaces` —
    none of which may import this module — can still catch a refusal by type
    instead of by catching `Exception`.
    """


class ForeignScopeRoot(ScopeResolutionError):
    """The artifact is another scope's root, addressable only under its label."""


class UnknownArtifact(ScopePathError, LookupError):
    """No node in any scope tree answers to this key.

    Deliberately not a fallback to `scope_root / artifact`: guessing is how the
    same file acquires two paths, and `ARTIFACT_TIER`'s "a path that appears in
    two tiers is the bug this table exists to prevent" then has nothing to catch.
    """


class ArtifactNotInScope(ScopePathError, ValueError):
    """This artifact does not exist in this scope.

    A `coaching_1on1_history.md` under a project repository would be exactly the
    cross-scope leak the four scopes exist to prevent, so asking for one is an
    error here rather than a path the caller can go on to write.
    """


class AmbiguousArtifact(ScopePathError, LookupError):
    """A basename that names more than one node inside one scope.

    Basenames are accepted as a convenience, not as an addressing scheme. Where
    the convenience stops being unambiguous the resolver refuses instead of
    picking, because picking would put a record at a plausible wrong path and
    nothing downstream could tell.
    """


class UnknownProject(ScopePathError, LookupError):
    """A project scope naming a repository this resolver was never given.

    AD-11: projects enter the system through `pm-ai project add` and the registry
    it writes. Searching the filesystem for `.project-ai` directories would opt a
    repository into harvesting without anyone asking for it.
    """


class MalformedSubjectId(ScopePathError, ValueError):
    """A project or person id that cannot be used as a directory name.

    The dangerous case is traversal: `person_id="../../../tmp/evil"` would have
    resolved outside the team-member enclave, so a record about a direct report
    lands somewhere with none of the enclave's guarantees. Ids reach this module
    from a registry file, a connector handle, and a CLI argument, so none of them
    are the daemon's own strings.
    """


class RepositoryOutsideRoot(ScopePathError, ValueError):
    """A `rooted()` resolver was handed a repository outside its root.

    The whole value of the test factory is that everything it returns is beneath
    one directory a test can inspect and delete. A registered repository
    elsewhere silently voids that, and the test asserting containment then passes
    while writing outside the temporary directory.
    """


# ── Subject ids ──────────────────────────────────────────────────────────────


def _directory_name(label: str, value: str | None) -> str:
    """Validate a subject id as the single directory name it becomes.

    Refusals, in the order they matter:

    - a separator, `..`, or an absolute path escapes the scope it was supposed
      to sit inside — the traversal this function exists for;
    - a leading dot hides the directory and, for `.git` or `.project-ai`,
      collides with a name that already means something;
    - surrounding whitespace makes two ids that look identical in every log;
    - uppercase merges two ids into one directory on a case-insensitive
      filesystem, which on macOS silently joins two people's records.

    The separator tuple is the scope model's, not a second copy: a node name and
    an interpolated subject id must agree on what a separator is, or one of them
    admits a nested path the other refuses.
    """
    if value is None or not value.strip():
        raise MalformedSubjectId(
            f"{label} is empty. A scope's subject is what names its directory; "
            f"an empty one resolves to the parent scope's root."
        )
    if value != value.strip():
        raise MalformedSubjectId(
            f"{label}={value!r} has surrounding whitespace, which makes two "
            f"distinguishable ids indistinguishable in every log and path."
        )
    if any(sep and sep in value for sep in _SEPARATORS):
        raise MalformedSubjectId(
            f"{label}={value!r} contains a path separator. A subject id is one "
            f"directory name, and a nested one escapes the scope that contains it."
        )
    if value == ".." or value.startswith("."):
        raise MalformedSubjectId(
            f"{label}={value!r} starts with a dot. `..` traverses out of the "
            f"scope entirely, and a leading dot hides the directory or collides "
            f"with a name that already means something ({PROJECT_DIRNAME!r})."
        )
    if Path(value).is_absolute():
        raise MalformedSubjectId(
            f"{label}={value!r} is an absolute path, which replaces the scope "
            f"root rather than sitting beneath it."
        )
    if value != value.lower():
        raise MalformedSubjectId(
            f"{label}={value!r} is not lowercase. macOS filesystems are "
            f"case-insensitive by default, so `Alice` and `alice` would be one "
            f"directory holding two people's records."
        )
    return value


# ── Looking a key up in the scope model ──────────────────────────────────────
# The index is the scope model's; the refusals are this module's, because a
# refusal is what a caller of `resolve` has to catch.


def is_directory(artifact: str) -> bool:
    """Whether `artifact` is a directory rather than a file.

    Raises for an unknown artifact rather than reading the trailing slash off
    any string handed to it: a caller reaching for this as a validity check
    would otherwise get a confident answer about something that does not exist.
    The slash is not a convention typed by hand — it comes from the node type.
    """
    if artifact not in _KEYS:
        raise UnknownArtifact(_unknown_message(artifact))
    return artifact.endswith("/")


def scopes_of(artifact: str) -> frozenset[ScopeKind]:
    """Which scope kinds declare a node answering to `artifact`."""
    if artifact not in _KEY_SCOPES:
        raise UnknownArtifact(_unknown_message(artifact))
    return _KEY_SCOPES[artifact]


def _place(kind: ScopeKind, artifact: str) -> Placement:
    """Where `artifact` sits below a `kind` scope root.

    One lookup against one per-scope index. The previous shape needed two — a
    skeleton table and an artifact table — and keeping them apart was the whole
    difficulty; a tree makes "is this structure or content" the node's own type.
    """
    if artifact in _AMBIGUOUS[kind]:
        raise AmbiguousArtifact(
            f"{artifact!r} names more than one node in the {kind.value} scope. "
            f"Address it by its relative path instead: "
            f"{sorted(p.key for p in _PLACEMENTS[kind] if p.node.key == artifact)}"
        )
    placement = _ADDRESS[kind].get(artifact)
    if placement is not None:
        # Checked on the NODE, not on the artifact string. One node is addressable
        # under several spellings — `people/` and `private/people/` both reach
        # this one — and a string check caught the first and missed the second,
        # which is the whole bug reappearing one spelling to the left.
        owner = getattr(placement.node, "governed_by", None)
        if owner is not None:
            # Declared in this tree so its tier and git exclusion derive from it,
            # and deliberately not addressable through it: everything inside
            # belongs to `owner`, whose guards key on the scope label rather than
            # on the directory. Refused under *every* label, `owner` included —
            # the container is never a write target, and `ScopePaths.people_root`
            # composes it structurally for the one caller that needs it.
            raise ForeignScopeRoot(
                f"{artifact!r} is the root of the {owner.value} scope, not an "
                f"artifact of the {kind.value} scope. Address a record inside it "
                f"as DataScope(ScopeKind.{owner.name}, ...) — writing through "
                f"another label puts the record in the right directory with none "
                f"of the guarantees that label carries."
            )
        return placement
    homes = _KEY_SCOPES.get(artifact)
    if homes is None:
        raise UnknownArtifact(_unknown_message(artifact))
    raise ArtifactNotInScope(
        f"{artifact!r} does not exist in the {kind.value} scope. It belongs to "
        f"{sorted(k.value for k in homes)}."
    )


# Foreign roots by key, so `foreign_scope_root` reaches the same Placement
# `resolve` would have used rather than recomposing the path from a dirname —
# two ways of building one path is the disagreement AD-44 exists to prevent.
_PLACEMENTS_BY_KEY: dict[str, Placement] = {
    key: _ADDRESS[ScopeKind.APPLICATION][key]
    for key in FOREIGN_ROOTS
    if key in _ADDRESS[ScopeKind.APPLICATION]
}


def _unknown_message(artifact: str) -> str:
    return (
        f"{artifact!r} is not a declared artifact. Add it to the tree of the "
        f"scope that holds it in pm_ai.domain.scope_model — as a File with its "
        f"tier, a Dir, or a Collection with its durability: an artifact with a "
        f"path and no tier is in neither the backup set nor the rebuild set. "
        f"Known: {sorted(_KEYS)}"
    )


@dataclass(frozen=True, slots=True)
class ScopePaths:
    """Resolves a scope and an artifact to an absolute path.

    Construct through `production()` or `rooted()` rather than directly; the two
    differ in exactly one dangerous way, and the factory names say which one you
    have.
    """

    application_root: Path
    personal_root: Path
    project_roots: Mapping[str, Path] = field(default_factory=dict)
    project_parent: Path | None = None
    """Where an unregistered project's repository is assumed to be.

    `None` in production, and that is the AD-11 guarantee: a resolver that cannot
    invent a repository path cannot silently enrol a repository. The test factory
    sets it so a test may name any project id without a registry.
    """

    # Deliberately unhashable. `frozen=True` would otherwise advertise a
    # `__hash__` that raises `TypeError` on the mapping field, which is a worse
    # failure than not having one: a resolver is not a dict key, and a caller
    # wanting to cache by location should key on a resolved `Path`.
    __hash__ = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Normalize on the way in, so every path handed out afterwards is clean.

        Direct construction is not the intended door, but it is reachable, and a
        resolver built with a relative root or a plain mutable dict hands out
        paths that depend on the working directory and a mapping the caller can
        edit underneath it.
        """
        object.__setattr__(self, "application_root", _absolute(self.application_root))
        object.__setattr__(self, "personal_root", _absolute(self.personal_root))
        object.__setattr__(self, "project_roots", _absolute_map(self.project_roots))
        if self.project_parent is not None:
            object.__setattr__(self, "project_parent", _absolute(self.project_parent))

    # ── Factories ────────────────────────────────────────────────────────────

    @classmethod
    def production(
        cls,
        *,
        home: Path | str | None = None,
        projects: Mapping[str, Path | str] | None = None,
    ) -> ScopePaths:
        """The real layout: `~/.pm-ai`, `~/.manager-ai`, and registered repositories.

        `projects` comes from the registry the CLI writes (`projects.toml`), never
        from a filesystem search (AD-11). `home` is an override for the rare
        caller that has a home directory other than the process owner's; it is not
        the way to get a temporary layout — that is `rooted()`.
        """
        base = _absolute(home) if home is not None else Path.home()
        return cls(
            application_root=base / APPLICATION_DIRNAME,
            personal_root=base / PERSONAL_DIRNAME,
            # Coerced here rather than left to `__post_init__`: the field declares
            # `Mapping[str, Path]`, and a factory that hands it strings makes that
            # declaration a lie which only the coercion happens to cover.
            project_roots=_absolute_map(projects or {}),
        )

    @classmethod
    def rooted(
        cls,
        root: Path | str,
        *,
        projects: Mapping[str, Path | str] | None = None,
    ) -> ScopePaths:
        """All four scopes beneath `root`, at production's relative structure.

        A test gets the real directory names — `.pm-ai`, `.manager-ai`,
        `private/people`, `.project-ai` — so a layout mistake fails in a test
        rather than only on a real machine. Any project id not in `projects`
        resolves under `root`, which is what keeps "resolve everything and assert
        it is all beneath the root" a check a test can actually make.

        A registered repository outside `root` is refused for that same reason:
        it would leave the containment assertion true of every path except the
        ones that escaped.
        """
        base = _absolute(root)
        paths = cls(
            application_root=base / APPLICATION_DIRNAME,
            personal_root=base / PERSONAL_DIRNAME,
            project_roots=_absolute_map(projects or {}),
            project_parent=base / ROOTED_PROJECTS_DIRNAME,
        )
        outside = sorted(
            f"{pid}={p}"
            for pid, p in paths.project_roots.items()
            if not p.is_relative_to(base)
        )
        if outside:
            raise RepositoryOutsideRoot(
                f"a rooted resolver keeps everything beneath {base}, but "
                f"{', '.join(outside)} lies outside it. Use `production()` for "
                f"real repository paths."
            )
        return paths

    # ── Scope roots ──────────────────────────────────────────────────────────

    @property
    def enclave_root(self) -> Path:
        """The application scope's gitignored enclave."""
        return self.application_root / ENCLAVE_DIRNAME

    @property
    def people_root(self) -> Path:
        """The team-member scope as a whole — one directory, deleted on role change."""
        return self.enclave_root / PEOPLE_DIRNAME

    def gitignore(self, project_id: str) -> Path:
        """The `.gitignore` that decides whether git tracks this project's captures.

        Returned whether or not the file exists — an absent one is the very case
        `assert_capture_dir_ignored` has to refuse, so the caller reads the path
        and treats "missing" as "no rule", rather than asking here and getting a
        refusal it cannot tell apart from a resolver failure.

        Not reachable through `resolve`: this file belongs to the repository, not
        to the `.project-ai/` scope inside it, so no scope tree declares it.
        """
        return self.repository(project_id) / GITIGNORE_FILENAME

    def repository(self, project_id: str) -> Path:
        """The repository a project was enrolled from."""
        checked = _directory_name("project_id", project_id)
        known = self.project_roots.get(checked)
        if known is not None:
            return known
        if self.project_parent is None:
            raise UnknownProject(
                f"project {checked!r} is not registered. A repository path is "
                f"supplied by `pm-ai project add`, never found by scanning for "
                f"{PROJECT_DIRNAME!r} directories (AD-11). "
                f"Registered: {sorted(self.project_roots)}"
            )
        return self.project_parent / checked

    def scope_root(self, scope: DataScope) -> Path:
        """The directory that scope owns.

        `DataScope.__post_init__` has already refused a project scope with no
        project id and a people scope with no person id, so there is no
        subject-less scope to handle here. What it has *not* checked is whether
        the id is usable as a directory name — it is a domain type with no
        filesystem to reason about — so that check happens here.
        """
        match scope.kind:
            case ScopeKind.APPLICATION:
                return self.application_root
            case ScopeKind.PERSONAL:
                return self.personal_root
            case ScopeKind.PEOPLE:
                return self.people_root / _directory_name("person_id", scope.person_id)
            case ScopeKind.PROJECT:
                return self.repository(str(scope.project_id)) / PROJECT_DIRNAME
        raise AssertionError(f"unhandled scope kind {scope.kind!r}")

    # ── Artifacts ────────────────────────────────────────────────────────────

    def resolve(self, scope: DataScope, artifact: str, *, create: bool = False) -> Path:
        """Where `artifact` lives in `scope`.

        `artifact` is a scope-relative path as declared in that scope's tree —
        `"rules/persona.md"` — or a bare basename where that is unambiguous
        within the scope.

        `create` makes the directory the artifact needs — the directory itself for
        a directory artifact, its parent for a file. It never creates the file:
        `StorageService` owns content (AD-5), and a resolver that touched files
        would be a second writer.
        """
        placement = _place(scope.kind, artifact)
        path = self.scope_root(scope) / placement.relative
        if create:
            directory = path if placement.node.is_dir else path.parent
            directory.mkdir(parents=True, exist_ok=True)
        return path

    # ── The named stores (AD-3) ──────────────────────────────────────────────
    # Three tiers, and the two that share a directory do not share a file. Which
    # tier each one is, is declared on its node in `pm_ai.domain.scope_model`.

    @property
    def disclosure_ledger(self) -> Path:
        """AD-38 — the single frontier-call provenance and cost ledger."""
        return self.resolve(DataScope(ScopeKind.APPLICATION), "disclosure.md")

    def foreign_scope_root(self, artifact: str) -> Path:
        """The path of a node that is another scope's root (`FOREIGN_ROOTS`).

        `resolve` refuses these deliberately, because addressing a direct
        report's directory under the application label writes the record into
        exactly the right place with none of the guarantees that label carries.
        Some callers legitimately need the *location* without addressing
        anything in it — the layout assertions that check no Tier-1 path sits
        inside a rebuildable one have to include `people/`, since it shares the
        `private/` enclave with the Tier-3 stores.

        Named so it cannot be reached for by accident, and narrow: it answers
        only for a declared foreign root, so it cannot become a second `resolve`
        that skips the refusal.
        """
        owner = FOREIGN_ROOTS.get(artifact)
        if owner is None:
            raise ArtifactNotInScope(
                f"{artifact!r} is not a foreign scope root. This accessor exists "
                f"for the nodes `resolve` refuses; everything else goes through "
                f"`resolve`. Known: {sorted(FOREIGN_ROOTS)}."
            )
        return self.scope_root(DataScope(ScopeKind.APPLICATION)) / _PLACEMENTS_BY_KEY[
            artifact
        ].relative

    @property
    def project_registry(self) -> Path:
        """AD-11 — the enrolled-project registry `pm-ai project add` writes."""
        return self.resolve(DataScope(ScopeKind.APPLICATION), "projects.toml")

    @property
    def operational_store(self) -> Path:
        """Tier 2. Job queue, cursors, executed-key ledger, staged proposals."""
        return self.resolve(DataScope(ScopeKind.APPLICATION), "operational.db")

    @property
    def event_index_store(self) -> Path:
        """Tier 3. The search index over rules, event log and meetings.

        `pm-ai reindex` may delete this. One file per index rather than one per
        tier, because the job that rebuilds it declares this path as its whole
        output and two jobs sharing one file could not.
        """
        return self.resolve(DataScope(ScopeKind.APPLICATION), "event_index.db")

    @property
    def commitment_index_store(self) -> Path:
        """Tier 3. The commitment index over `commitments_log.md`."""
        return self.resolve(DataScope(ScopeKind.APPLICATION), "commitment_index.db")

    @property
    def vector_index(self) -> Path:
        """Tier 3. Pruned embeddings — not encrypted, rebuildable (AD-6)."""
        return self.resolve(DataScope(ScopeKind.APPLICATION), "vector_index/")

    @property
    def personal_analytics_store(self) -> Path:
        """Tier 2, inside the personal scope. Never opened by project rendering."""
        return self.resolve(DataScope(ScopeKind.PERSONAL), "personal_analytics.db")


def _absolute(value: Path | str) -> Path:
    """An absolute, `..`-free, `~`-free path.

    `Path.absolute()` alone is not enough, and the gap is not cosmetic: it does
    no normalization, so `/root/projects/../../escape` stays literally that and
    `is_relative_to("/root")` answers True about a path that left `/root`. Every
    containment check in this module and its tests rests on that answer.

    Normalization is lexical (`os.path.normpath`) rather than `Path.resolve()`
    on purpose: `resolve()` also follows symlinks, which would rewrite a
    caller's `/tmp/...` to `/private/tmp/...` on macOS and make the path it gets
    back not the one it asked about.
    """
    return Path(os.path.normpath(Path(value).expanduser().absolute()))


def _absolute_map(values: Mapping[str, Path | str] | None) -> Mapping[str, Path]:
    """Normalize a registry mapping, and validate its ids as directory names.

    Registry values are the natural place for `~/code/alpha` or a path relative
    to wherever the CLI happened to run, and both are wrong on arrival: one
    creates a directory literally named `~`, the other means a different place
    per working directory.
    """
    return MappingProxyType(
        {_directory_name("project_id", k): _absolute(v) for k, v in (values or {}).items()}
    )
