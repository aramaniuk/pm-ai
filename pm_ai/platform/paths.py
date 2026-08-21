"""The one place a directory layout is written down (AD-4, AD-26).

Four scopes exist so that personal coaching material cannot reach a team
repository and a direct report's record cannot be read by that report's peers
(AD-31). Until now no object could answer where any of them live: the layout was
four trailing comments on `ScopeKind` plus a bare string in
`pm_ai.domain.disclosure`. A boundary that exists only as a comment is a boundary
each new caller re-derives, and one of those derivations is the leak.

`ScopeKind`'s comments are the specification this implements:

    APPLICATION  ~/.pm-ai/
    PERSONAL     ~/.manager-ai/
    PEOPLE       ~/.pm-ai/private/people/<person_id>/
    PROJECT      <repository>/.project-ai/

Two factories, and the difference between them is the point. `production()`
reads the real home directory and knows no project repository it was not told
about — AD-11 forbids discovering one by scanning the filesystem, so an
unregistered project is an error rather than a guess. `rooted()` puts all four
scopes beneath a directory the caller supplies, at the same relative structure,
so a test exercises the real layout instead of a parallel fake of it.

Subject ids are interpolated into paths, which makes them a trust boundary: a
`person_id` of `../../..` would resolve outside the enclave that keeps a direct
report's record away from that report's peers. They are therefore validated as
directory names before use, and every path this module composes is normalized so
that a `..` cannot survive into a containment check.

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
from pm_ai.domain.storage_tiers import ARTIFACT_TIER, RETENTION_MANAGED

# ── Directory names ──────────────────────────────────────────────────────────
# The four scope roots, spelled once each.

APPLICATION_DIRNAME = ".pm-ai"
PERSONAL_DIRNAME = ".manager-ai"
PROJECT_DIRNAME = ".project-ai"

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
ENCLAVE_DIRNAME = "private"

# The team-member scope is stored *under* the application scope but is its own
# kind, because the rules that separate it from the sovereign personal scope
# cannot be written against a path (AD-31, UJ-4).
PEOPLE_DIRNAME = "people"

# Where `rooted()` puts a repository it was not given a path for.
ROOTED_PROJECTS_DIRNAME = "projects"

_ALL_SCOPES = frozenset(ScopeKind)
_SUBJECT_SCOPES = frozenset({ScopeKind.PERSONAL, ScopeKind.PEOPLE, ScopeKind.PROJECT})


# ── Errors ───────────────────────────────────────────────────────────────────
# One base, so a caller wiring the resolver into storage can catch every way it
# can refuse in a single clause instead of enumerating them and missing the one
# added next. None of these subclass `KeyError`: its `__str__` is `repr` of the
# argument, which turns a multi-line explanation into an escaped one-liner in
# the traceback — the wording is the point of raising.


class ScopePathError(Exception):
    """The resolver refused. Every failure below is one of these."""


class UnknownArtifact(ScopePathError, LookupError):
    """No layout entry for this artifact.

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

# Both spellings, on every platform: an id is persisted and may be read back on
# another OS, so a backslash is a separator here even where the OS disagrees.
_SEPARATORS = ("/", "\\", os.sep, os.altsep or "/")


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


# ── Layout ───────────────────────────────────────────────────────────────────
# artifact -> path relative to its scope root. Every scope holds a given artifact
# at the same relative path, which is what lets `rooted()` reproduce production
# structure exactly and what makes "the personal event log" a single idea rather
# than one per scope.

_LAYOUT: Mapping[str, str] = MappingProxyType(
    {
        # Tier 1 — Markdown truth, plaintext by design (AD-6).
        "config.toml": "config.toml",
        "disclosure.md": "disclosure.md",
        "rules/": "rules",
        "event_log/": "memory/event_log",
        "meetings/": "memory/meetings",
        "commitments_log.md": "memory/commitments_log.md",
        "coaching_1on1_history.md": "memory/coaching_1on1_history.md",
        "strategic_goals.md": "memory/strategic_goals.md",
        # Tier 2 — durable, never rebuilt.
        "operational.db": f"{ENCLAVE_DIRNAME}/operational.db",
        "personal_analytics.db": f"{ENCLAVE_DIRNAME}/personal_analytics.db",
        # Tier 3 — disposable, rebuilt by `pm-ai reindex`. Separate file and
        # separate directory from Tier 2, so a rebuild cannot reach the job
        # queue, the cursors, or the executed-key ledger (AD-3).
        "derived.db": f"{ENCLAVE_DIRNAME}/derived.db",
        "vector_index/": f"{ENCLAVE_DIRNAME}/vector_index",
        # Retention-managed raw input, outside the tier model (NFR-09). Placed
        # here anyway: a purge needs a path, and the alternative is a second
        # module that also knows the layout.
        #
        # `transcripts/` sits at the scope root, not under `memory/`, because
        # `GITIGNORE_REQUIRED` anchors its exclusion at `/.project-ai/transcripts/`.
        # Move it and the rule still reports "protected" for a directory git
        # tracks, which is how verbatim minutes reach the employer's repository.
        "transcripts/": "transcripts",
        # In the PERSONAL enclave, per the scope model: it holds the PM's own
        # voice notes and dialogue state, which is personal-scope material by
        # subject. `test_ad25_...` in tests/architecture treats any path naming
        # `manager-ai` as personal, and this is one — deliberately.
        # `test_ad6_markdown_is_never_encrypted` still spells it under
        # `~/.pm-ai/private/`; that list is an encryption-classifier fixture
        # (story 1e), and its strings will need the personal scope when it runs.
        "telegram_cache/": f"{ENCLAVE_DIRNAME}/telegram_cache",
    }
)

# artifact -> the scopes it may exist in. This table is the privacy boundary in
# executable form; a caller cannot route a record into the wrong scope by
# supplying the wrong `DataScope`, because the pair is checked here.
_HOMES: Mapping[str, frozenset[ScopeKind]] = MappingProxyType(
    {
        # Application scope holds system-level state only, so that no
        # employer-specific configuration lands in the sovereign personal scope.
        "config.toml": frozenset({ScopeKind.APPLICATION}),
        # AD-38 — one ledger, outside every repository. Per-scope would push it.
        "disclosure.md": frozenset({ScopeKind.APPLICATION}),
        "operational.db": frozenset({ScopeKind.APPLICATION}),
        "derived.db": frozenset({ScopeKind.APPLICATION}),
        "vector_index/": frozenset({ScopeKind.APPLICATION}),
        # A persona and a set of conventions are a property of the PM or of the
        # team, never of the daemon.
        "rules/": frozenset({ScopeKind.PERSONAL, ScopeKind.PROJECT}),
        # Per scope, every one of them: an audit trail that lived in one place
        # would be a committed file naming personal material (AD-38).
        "event_log/": _ALL_SCOPES,
        # A capture lives in the scope owning its meeting (AD-33), so both the
        # summary and the raw capture follow the subject, not the convenience.
        "meetings/": _SUBJECT_SCOPES,
        "transcripts/": _SUBJECT_SCOPES,
        "commitments_log.md": frozenset({ScopeKind.PROJECT}),
        # The sovereign hub. `strategic_goals.md` holds all three goal domains
        # here today; a project artifact citing a personal goal is the
        # cross-scope violation the scope model exists to prevent.
        "coaching_1on1_history.md": frozenset({ScopeKind.PERSONAL}),
        "strategic_goals.md": frozenset({ScopeKind.PERSONAL}),
        # A separate database inside the personal scope, so project-scope
        # rendering has no code path that could join burnout metrics into
        # team-facing output (AD-25).
        "personal_analytics.db": frozenset({ScopeKind.PERSONAL}),
        "telegram_cache/": frozenset({ScopeKind.PERSONAL}),
    }
)

# The artifacts whose subject is the PM personally (AD-31). Stated separately
# from `_HOMES` on purpose: `_HOMES` is the mechanism, this is the intent, and a
# test that reads only the mechanism cannot notice the mechanism changing. Adding
# a scope to one of these entries in `_HOMES` is caught by comparing the two.
#
# A committed scope holds none of them. `event_log/` and `transcripts/` are
# absent because they are per-scope by construction: the personal one is
# personal, the project one was never the PM's.
PERSONAL_SUBJECT_ARTIFACTS: frozenset[str] = frozenset(
    {
        "coaching_1on1_history.md",
        "strategic_goals.md",
        "personal_analytics.db",
        "telegram_cache/",
    }
)

# An artifact that is tiered or retention-managed but has no home is an artifact
# whose location the next caller invents. This is the same shape of check
# `storage_tiers` makes about tiers, for the same reason.
assert set(_LAYOUT) == set(_HOMES), "every laid-out artifact needs its scopes, and vice versa"
assert (set(ARTIFACT_TIER) | RETENTION_MANAGED) <= set(_LAYOUT), (
    "an artifact is tiered or retention-managed but has no path: "
    f"{sorted((set(ARTIFACT_TIER) | RETENTION_MANAGED) - set(_LAYOUT))}"
)
assert PERSONAL_SUBJECT_ARTIFACTS <= set(_LAYOUT), "a personal artifact with no path"


def is_directory(artifact: str) -> bool:
    """Whether `artifact` is a directory rather than a file.

    Raises for an unknown artifact rather than reading the trailing slash off
    any string handed to it: a caller reaching for this as a validity check
    would otherwise get a confident answer about something that does not exist.
    """
    if artifact not in _LAYOUT:
        raise UnknownArtifact(_unknown_message(artifact))
    return artifact.endswith("/")


def artifacts_in(kind: ScopeKind) -> frozenset[str]:
    """Every artifact that scope kind may hold."""
    return frozenset(a for a, homes in _HOMES.items() if kind in homes)


def _unknown_message(artifact: str) -> str:
    return (
        f"{artifact!r} has no layout entry. Add it to _LAYOUT and _HOMES here, "
        f"with a tier in pm_ai.domain.storage_tiers — an artifact with a path and "
        f"no tier is in neither the backup set nor the rebuild set. "
        f"Known: {sorted(_LAYOUT)}"
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
            project_roots=projects or {},
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
            project_roots=projects or {},
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

        `create` makes the directory the artifact needs — the directory itself for
        a directory artifact, its parent for a file. It never creates the file:
        `StorageService` owns content (AD-5), and a resolver that touched files
        would be a second writer.
        """
        if artifact not in _LAYOUT:
            raise UnknownArtifact(_unknown_message(artifact))
        homes = _HOMES[artifact]
        if scope.kind not in homes:
            raise ArtifactNotInScope(
                f"{artifact!r} does not exist in the {scope.kind.value} scope. It "
                f"belongs to {sorted(k.value for k in homes)}."
            )
        path = self.scope_root(scope) / _LAYOUT[artifact]
        if create:
            directory = path if is_directory(artifact) else path.parent
            directory.mkdir(parents=True, exist_ok=True)
        return path

    # ── The named stores (AD-3) ──────────────────────────────────────────────
    # Three tiers, and the two that share a directory do not share a file.

    @property
    def disclosure_ledger(self) -> Path:
        """AD-38 — the single frontier-call provenance and cost ledger."""
        return self.resolve(DataScope(ScopeKind.APPLICATION), "disclosure.md")

    @property
    def operational_store(self) -> Path:
        """Tier 2. Job queue, cursors, executed-key ledger, staged proposals."""
        return self.resolve(DataScope(ScopeKind.APPLICATION), "operational.db")

    @property
    def derived_store(self) -> Path:
        """Tier 3. Search and commitment indexes; `pm-ai reindex` may delete this."""
        return self.resolve(DataScope(ScopeKind.APPLICATION), "derived.db")

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
