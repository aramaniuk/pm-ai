"""The scope layout, and the durability promise each artifact carries (AD-4, AD-3).

Four scopes exist so that personal coaching material cannot reach a team
repository and a direct report's record cannot be read by that report's peers
(AD-31). This module is the one place that says which artifacts exist, where each
one sits relative to its scope root, and what happens to it on backup and on
rebuild.

    APPLICATION  ~/.pm-ai/
    PERSONAL     ~/.manager-ai/
    PEOPLE       ~/.pm-ai/private/people/<person_id>/
    PROJECT      <repository>/.project-ai/

None of that touches the OS, which is why it lives in `domain` rather than in
`pm_ai.platform.paths`. Anchoring a scope to a real `~/.pm-ai` or to a registered
repository is the platform's job and stays there; the shape being anchored is
data, and `domain` is the one package every layer may import (AD-30).

## Three layers, declared once each

The layout is stated as **one literal tree per scope**, laid out to mirror the
diagrams in `_bmad-output/specs/spec-pm-ai/scope-model.md` §A, §B and §C so a
reader can diff this file against that document line by line. The three layers
of the model are declared exactly once each and nothing else is written down:

1. **Where a scope lives** — not here. `ScopePaths` owns it.
2. **What shape a scope has** — `Dir` and `Collection` nodes in that scope's
   tree.
3. **What content a scope holds, and what it is worth** — `File` nodes, each
   carrying its tier.

Everything else is computed: the address index, `artifacts_in()`, `ARTIFACT_TIER`,
`BACKUP_TARGETS`, `REBUILD_TARGETS`, `RETENTION_MANAGED`, `DIAGNOSTIC_ONLY`. A
flat table of artifacts was the previous shape of the layout, and it failed
twice. It could not say *which* of the 34 leaves in `scope-model.md` were
declared, because a set cannot be diffed against a tree; and it keyed artifacts
by a globally unique name, so `persona.md` — which exists in both
`~/.manager-ai/rules/` and `<repo>/.project-ai/rules/` with different content —
was unrepresentable.

## The tier belongs on the node

`storage-contract.md`'s three tiers were a *second*, flat, basename-keyed table
in `pm_ai.domain.storage_tiers`, with the trees over in `pm_ai.platform.paths`
mentioning `Tier` only in comments. That cost three things. Adding an artifact
meant two edits in two modules, and the only thing catching a mismatch was a pair
of import-time assertions that existed purely because the two structures could
drift. The table could not tell personal `daily_dashboard.md` from project
`daily_dashboard.md` — the exact flaw the per-scope trees had just removed from
path resolution. And `domain` may not import `pm_ai.platform` (AD-30), so the
table structurally could not see the tree it was supposed to agree with.

A tier is now a required field of the node that carries it, so the states those
assertions guarded are unreachable: a `File` cannot be declared without a tier,
and no key can appear in `ARTIFACT_TIER` without a node to derive it from.

## The three node types

`File(name, tier)`
    A declared artifact, and the `Tier` it belongs to. The tier is required, not
    defaulted: a file with a path and no tier is in neither the backup set nor
    the rebuild set, which is how `personal_analytics.db` spent months backed up
    by nothing.

`Dir(name, children, tier=None)`
    Structure whose members are declared right here: `rules/`, `memory/`,
    `private/`. A `Dir` needs no tier of its own — its declared members carry
    theirs — though `rules/` has one, because the directory is addressed as a
    unit.

`Collection(name, durability, namespaces=())`
    Structure whose members are created at runtime with names this module cannot
    enumerate: `event_log/` dated segments, one directory per meeting, one per
    person id, whatever `.py` files the PM drops in `skills/`. **Nothing inside a
    `Collection` is declarable**, and that is the whole point: the absence is a
    stated decision rather than an omission, which is what the old flat set could
    not express. `namespaces` is the one exception, and it is not a hole — a
    nested `Collection` is the same statement one level down. `skills/telemetry/`
    is a named sub-namespace of an unenumerable directory; the harvesters inside
    it are as arbitrary as the skills beside it. A `File` or a `Dir` inside a
    `Collection` is refused at construction.

    The directory is the whole artifact, which is how `event_log/` and
    `vector_index/` have always been tiered — so a `Collection` states its
    durability too. Where that is not a `Tier`, it is an `OutsideTierModel`
    member naming *why*: an artifact outside the tier model is a decision made at
    the node, not membership of a side set somebody has to remember to update.

## Addressing

A key is the scope-relative path as declared in the tree — `"rules/persona.md"`.
A bare basename is also accepted when it is unambiguous within that scope,
because `pm_ai.storage.service` passes `EVENT_LOG` (`"event_log/"`) and
`OPERATIONAL_DB`, and `ARTIFACT_TIER`'s keys are basenames. A basename that names
two nodes in one scope is ambiguous, and the resolver refuses it: picking one is
how a record acquires the wrong path silently. A trailing slash means a
directory, and it is derived from the node type rather than typed by hand.

Because the trees are per scope, `"persona.md"` in the personal scope and
`"persona.md"` in a project scope are two different declarations that happen to
share a spelling — they resolve through different trees. What they do share is
one `ARTIFACT_TIER` key, which is correct here and asserted below: a persona file
is Tier 1 Markdown truth in both scopes, so the two nodes must declare the same
tier or the flat projection would be lying about one of them.

## Divergences from the `scope-model.md` diagrams

Two leaves are declared here that those diagrams omit. Both are current
behaviour and both are stated elsewhere, so they are kept — resolved in favour
of the spine — and marked at their declaration site rather than silently dropped:

- personal `memory/meetings/` — `ARCHITECTURE-SPINE.md:683` names it
  ("meetings/ (personal-subject sessions only)"); `scope-model.md` §B draws no
  `meetings/`.
- application `memory/event_log/` — `scope-model.md`'s own prose says every scope
  holds "its own `event_log/`"; its §A diagram draws neither that nor a
  `memory/` to hold it. The document contradicts itself and the prose wins,
  because the daemon has been writing an application-scope audit trail all along.

This module performs no I/O and imports nothing from `pm_ai` outside `domain`
(AD-30). It composes no absolute path and opens no file.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Union

from pm_ai.domain.identity import ScopeKind


# ── Errors ───────────────────────────────────────────────────────────────────


class ScopeResolutionError(Exception):
    """A path resolver refused to locate an artifact.

    The concrete refusals live in `pm_ai.platform.paths`, which `storage`,
    `core`, and `surfaces` may not import — so without a base here, no caller
    could catch a refusal by type and every one of them would either catch
    `Exception` or let the daemon abort. Declared in `domain` because that is the
    one package every layer may reach (AD-30).
    """


class MalformedLayout(ScopeResolutionError, ValueError):
    """A scope tree that does not mean what its node types claim.

    Raised while the trees below are being constructed, so the module fails to
    import rather than serving a layout whose declaration contradicts itself.
    """


# ── Durability: a tier, or a named reason there is none ──────────────────────


class Tier(Enum):
    """AD-3. Exactly one tier per artifact.

    The earlier spine named three tiers while the job queue (Tier 2) and the
    search indexes (Tier 3) shared one `event_telemetry.db`. "Rebuild Tier 3
    only" was therefore unimplementable, and the obvious implementation of a
    rebuild — delete the file, recreate it — would have destroyed every pending
    external write and every connector cursor, silently, with the AD-3 test still
    green. Separation is physical in the trees below, so `reindex` cannot reach
    Tier 2 by construction rather than by careful coding.
    """

    TRUTH = 1
    OPERATIONAL = 2
    DERIVED = 3

    @property
    def rebuildable(self) -> bool:
        """Only Tier 3 can be reconstructed; the others must survive."""
        return self is Tier.DERIVED

    @property
    def backed_up(self) -> bool:
        """Tier 2 is a backup target precisely because it is NOT rebuildable.

        Backing up markdown alone — the earlier rule — would have lost the job
        queue, cursors, and executed-key ledger.
        """
        return self in (Tier.TRUTH, Tier.OPERATIONAL)


class OutsideTierModel(Enum):
    """Why an artifact carries no tier. Declared on the node, never inferred.

    AD-3 tiers *persistent state*. Two kinds of thing in the trees below are not
    that, for two different reasons, and both say so where they are declared —
    because an artifact that is simply absent from every set is an oversight
    (that is how `personal_analytics.db` ended up backed up by nothing), while
    one that names its exclusion is a decision the assertions can keep honest.

    `RETENTION_MANAGED`
        Raw input the pipeline consumes and NFR-09 purges at 30 days. Not Tier 3:
        Tier 3 promises *rebuildable from Tier 1 with zero loss*, and no rebuild
        reconstructs a recording. Per-scope, like `event_log/`: a transcript
        lives in the scope owning its meeting (AD-33). Nothing may depend on
        these surviving.

    `DIAGNOSTIC_ONLY`
        Not *state* at all. The spine says so in as many words — "logs/ —
        diagnostics, not a tier" (ARCHITECTURE-SPINE.md:675). Nothing reads it
        back, no rebuild produces it, and a backup of it restores nothing the
        daemon needs. A separate member rather than a second retention-managed
        one: these are not raw captures under NFR-09's purge, and conflating the
        two would put `logs/` under a retention promise nothing implements.
    """

    RETENTION_MANAGED = "retention-managed"
    DIAGNOSTIC_ONLY = "diagnostics-only"


Durability = Union[Tier, OutsideTierModel]


# ── The three node types ─────────────────────────────────────────────────────
# Three frozen dataclasses rather than one with a discriminator field, so that
# `Collection("logs", Tier.TRUTH, (File("today.log", Tier.TRUTH),))` is a
# TypeError at the declaration site instead of a flag nobody set.

# Both spellings, on every platform: a name is persisted and may be read back on
# another OS, so a backslash is a separator here even where the OS disagrees.
# `pm_ai.platform.paths` validates subject ids against this same tuple — one
# spelling, so a node name and an interpolated id cannot disagree about what a
# separator is.
PATH_SEPARATORS = ("/", "\\", os.sep, os.altsep or "/")


def _segment(name: str) -> str:
    """A node name is exactly one path segment.

    Not defensive dressing: a name carrying a separator would make the key it
    produces indistinguishable from a genuine nested declaration, and the
    relative-path invariant below would then be comparing two different things.
    """
    if not name or name.strip() != name or any(s and s in name for s in PATH_SEPARATORS):
        raise MalformedLayout(
            f"{name!r} is not usable as one path segment. A node names a single "
            f"directory entry; nesting is expressed by nesting nodes."
        )
    return name


def _tier(owner: str, value: object) -> Tier:
    """A `File`'s tier, checked as a `Tier` and nothing else.

    The dataclass already makes the argument required, so the unreachable state
    the old import-time assertion guarded — a declared artifact in neither the
    backup set nor the rebuild set — is now a missing positional argument. This
    catches the remaining way to get there: passing something that is not a tier.
    """
    if not isinstance(value, Tier):
        raise MalformedLayout(
            f"{owner} was given {value!r} as its tier. A declared file belongs to "
            f"exactly one Tier (AD-3); nothing else is in the backup set or the "
            f"rebuild set."
        )
    return value


@dataclass(frozen=True, slots=True)
class File:
    """A declared artifact: one file this system creates and writes, and its tier.

    `tier` is required. A file with a path and no tier is in neither the backup
    set nor the rebuild set, which is how `personal_analytics.db` spent months
    backed up by nothing — and the check for it used to be an assertion running
    at import time in another package, against a table that could not see this
    declaration.
    """

    name: str
    tier: Tier
    is_dir: ClassVar[bool] = False

    def __post_init__(self) -> None:
        _segment(self.name)
        _tier(f"File({self.name!r})", self.tier)

    @property
    def children(self) -> tuple[LayoutNode, ...]:
        return ()

    @property
    def durability(self) -> Durability:
        return self.tier

    @property
    def key(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class Dir:
    """Structure whose members are declared, right here, as `children`.

    `rules/`, `memory/`, `private/`. A `Dir` with no children would be a
    `Collection` that forgot to say so, so it is refused.

    `tier` is optional and usually absent: a `Dir` is pure structure and its
    declared members carry the tiers. `rules/` sets it because the directory is
    also addressed as a unit — back it up and you have backed up the rules.
    """

    name: str
    children: tuple[LayoutNode, ...]
    tier: Tier | None = None
    is_dir: ClassVar[bool] = True

    def __post_init__(self) -> None:
        _segment(self.name)
        if not self.children:
            raise MalformedLayout(
                f"Dir({self.name!r}) declares no members. A directory whose "
                f"contents this module cannot enumerate is a Collection; saying "
                f"so is what makes the absence a decision rather than an omission."
            )
        if self.tier is not None:
            _tier(f"Dir({self.name!r})", self.tier)

    @property
    def durability(self) -> Durability | None:
        return self.tier

    @property
    def key(self) -> str:
        return f"{self.name}/"


@dataclass(frozen=True, slots=True)
class Collection:
    """Structure whose members are named at runtime, so none are declarable.

    `event_log/` holds one dated segment per month, `meetings/` one record per
    meeting, `people/` one directory per person id, `skills/` whatever `.py`
    modules the PM writes. Naming any of them here would be a claim this module
    cannot keep.

    `durability` is required, because the directory is the whole artifact: either
    a `Tier` or the `OutsideTierModel` member that says why there is none.

    `namespaces` may hold further `Collection`s only — `skills/telemetry/` is a
    known sub-namespace of an unenumerable directory. A `File` or `Dir` here
    would be a declared artifact inside a namespace that just said it declares
    nothing, so it is refused.
    """

    name: str
    durability: Durability
    namespaces: tuple[Collection, ...] = ()
    is_dir: ClassVar[bool] = True

    def __post_init__(self) -> None:
        _segment(self.name)
        if not isinstance(self.durability, (Tier, OutsideTierModel)):
            raise MalformedLayout(
                f"Collection({self.name!r}) was given {self.durability!r} as its "
                f"durability. The directory is the whole artifact, so it is either "
                f"a Tier or the OutsideTierModel member naming why it has none."
            )
        intruders = [n for n in self.namespaces if not isinstance(n, Collection)]
        if intruders:
            raise MalformedLayout(
                f"Collection({self.name!r}) was given "
                f"{[type(n).__name__ + '(' + n.name + ')' for n in intruders]}. "
                f"Its members are created at runtime with names this module "
                f"cannot enumerate, so nothing inside it is declarable; only a "
                f"further Collection — the same statement one level down — may "
                f"nest here."
            )

    @property
    def children(self) -> tuple[LayoutNode, ...]:
        return self.namespaces

    @property
    def key(self) -> str:
        return f"{self.name}/"


LayoutNode = Union[File, Dir, Collection]

RETAINED = OutsideTierModel.RETENTION_MANAGED
DIAGNOSTIC = OutsideTierModel.DIAGNOSTIC_ONLY


# ── A. Application Scope — `~/.pm-ai/` ───────────────────────────────────────
# scope-model.md §A, in its order. System-level state only, so that no
# employer-specific configuration lands in the sovereign personal scope.

APPLICATION_TREE: tuple[LayoutNode, ...] = (
    # Daemon settings & global defaults.
    File("config.toml", Tier.TRUTH),
    # AD-38 — one ledger, outside every repository. Per-scope would push it into
    # a committed one.
    File("disclosure.md", Tier.TRUTH),
    # The registry `pm-ai project add` writes. AD-11: this file is the only way a
    # repository enters the system, which is why `production()` takes it as an
    # argument and never searches for `.project-ai` directories. Tier 1 and not
    # rebuildable from anything: lose it and every enrolled project is forgotten.
    File("projects.toml", Tier.TRUTH),
    # Per-project and personal connector configuration, including the
    # team-member career MCP. One file per connector instance, named after the
    # instance — so the directory is the artifact and its members are not
    # declarable here. Backed up rather than rebuilt: no Tier 1 markdown
    # encodes it.
    Collection("connectors", Tier.TRUTH),
    # Rotating structured diagnostic logs, NOT `event_log/`. Outside the tier
    # model on purpose, and it says so here rather than in a set elsewhere.
    Collection("logs", DIAGNOSTIC),
    # DIVERGENCE, kept: `scope-model.md` §A draws no `memory/` here, but the same
    # document's prose says every scope holds "its own `event_log/`" — the
    # diagram and the prose disagree, and the prose is what the daemon has always
    # done. AD-38: an audit trail that lived in one place would be a committed
    # file naming personal material, so per-scope is the requirement.
    Dir("memory", (Collection("event_log", Tier.TRUTH),)),
    # THE OPERATIONAL ENCLAVE (gitignored).
    Dir(
        "private",
        (
            # Tier 2 — job queue, cursors, executed-key ledger, staged
            # proposals. Encrypted, never rebuilt.
            File("operational.db", Tier.OPERATIONAL),
            # Tier 3 — search & commitment indexes. Separate *files* from Tier 2,
            # so a rebuild cannot reach the job queue (AD-3). The shared enclave
            # proves nothing on its own; what holds is the assertion in
            # tests/architecture/test_paths.py that no Tier-1 path lies inside a
            # Tier-3 one, since a rebuild deletes what it names by path.
            File("derived.db", Tier.DERIVED),
            # API credentials (encrypted). Tier 2, not Tier 1: it is not derivable
            # from Markdown truth, and Tier 3 would mean a rebuild could delete
            # every connector credential the daemon holds.
            File("config.json", Tier.OPERATIONAL),
            # Tier 3 — pruned embeddings, not encrypted.
            Collection("vector_index", Tier.DERIVED),
            # THE TEAM-MEMBER SCOPE. One directory per person id — see
            # PEOPLE_TREE for what is inside one of them. Never committed;
            # deleted on role change. Tier 1 markdown inside the gitignored
            # enclave: career dossiers and agreed 1:1 goals are truth, and
            # encryption is orthogonal to tier.
            Collection("people", Tier.TRUTH),
        ),
    ),
)


# ── B. Sovereign Personal PM Scope — `~/.manager-ai/` ────────────────────────
# scope-model.md §B, in its order. Contains no project-specific information and
# survives a company transition intact.

PERSONAL_TREE: tuple[LayoutNode, ...] = (
    Dir(
        "rules",
        (
            # Leadership philosophy & career guidelines.
            File("manager_principles.md", Tier.TRUTH),
            # Personal coach persona, tone & constructiveness. `persona.md`
            # exists in two scopes with different content — personal and project
            # — and both are Tier 1: plaintext Markdown truth (AD-6), authored or
            # rendered from Tier 1, and no rebuild reconstructs either. The tier
            # is a property of the kind of file, not of the scope holding it.
            File("persona.md", Tier.TRUTH),
            # Briefing prefs & voice triggers.
            File("communication_preferences.md", Tier.TRUTH),
            # PM-configurable literature & web sources.
            File("article_sources.md", Tier.TRUTH),
        ),
        tier=Tier.TRUTH,
    ),
    Dir(
        "memory",
        (
            # Manager Strategic Focus morning briefing. Two scopes, one tier, for
            # the same reason as `persona.md` above.
            File("daily_dashboard.md", Tier.TRUTH),
            # All three goal domains live here today. A project artifact citing a
            # personal goal is the cross-scope violation the model exists to
            # prevent, which is why there is no project-scope counterpart.
            File("strategic_goals.md", Tier.TRUTH),
            # Socratic 1:1 logs & growth notes.
            File("coaching_1on1_history.md", Tier.TRUTH),
            # Personal-scope audit trail; dated segments.
            Collection("event_log", Tier.TRUTH),
            # DIVERGENCE, kept: `ARCHITECTURE-SPINE.md:683` names
            # "meetings/ (personal-subject sessions only)" here; `scope-model.md`
            # §B draws no `meetings/`. Resolved in favour of the spine, and it is
            # what AD-33 requires anyway — a purely personal session's record has
            # to live in the scope that owns the meeting.
            Collection("meetings", Tier.TRUTH),
        ),
    ),
    # PERSONAL CONCIERGE & CAREER SKILLS. The `.py` module names in
    # `scope-model.md` §B (`synthesize_manager_dashboard.py`,
    # `anti_burnout_shield.py`) are deliberately NOT declared: they are arbitrary
    # filenames the PM chooses, and a table naming two of them would read as an
    # exhaustive list of the skills that may exist.
    #
    # Tier 1 because nothing regenerates a skill (AD-42.6 leaves authoring to a
    # human), so losing the directory loses the work. `telemetry/` is the
    # cross-project harvesters, as arbitrary inside as its siblings.
    Collection("skills", Tier.TRUTH, (Collection("telemetry", Tier.TRUTH),)),
    # THE PERSONAL ENCLAVE (gitignored, encrypted).
    Dir(
        "private",
        (
            # The PM's own voice notes and dialogue state. Retention-managed raw
            # input, outside the tier model (NFR-09); transient, never a backup
            # target. It is in the PERSONAL enclave by subject, and
            # `test_ad25_...` in tests/architecture treats any path naming
            # `manager-ai` as personal — this is one, deliberately.
            Collection("telegram_cache", RETAINED),
            # Tier 2, inside the personal scope, so project-scope rendering has
            # no code path that could join burnout metrics into team-facing
            # output (AD-25). Backed up, never rebuilt: burnout trends outlive
            # the telemetry they came from once FR-37 compaction runs. AD-25
            # calls it "derived telemetry", but that word means *calculated*, not
            # *rebuildable*, and Tier 3's test is the latter.
            File("personal_analytics.db", Tier.OPERATIONAL),
        ),
    ),
    # Raw captures of purely personal sessions. `scope-model.md` §B draws none,
    # but its ownership rule is explicit that a capture lives in the scope owning
    # its meeting and that "every scope holds its captures at the same relative
    # path (`transcripts/`)". Current behaviour unchanged.
    Collection("transcripts", RETAINED),
)


# ── The team-member sub-scope — `~/.pm-ai/private/people/<person_id>/` ───────
# One person's directory, from `ARCHITECTURE-SPINE.md:679`: "dossiers,
# CareerGoals, FR-30 metrics, 1:1 meetings + their transcripts/". Career
# dossiers and agreed goals are per-report records under `meetings/` and the
# metric files the daemon names at runtime, so nothing here is a declared `File`
# — which is exactly what a `Collection` says.

PEOPLE_TREE: tuple[LayoutNode, ...] = (
    Dir(
        "memory",
        (
            Collection("event_log", Tier.TRUTH),
            # A 1:1 with a direct report is people-scoped, never project-scoped
            # (AD-33): a report's record must not be readable by that report's
            # peers.
            Collection("meetings", Tier.TRUTH),
        ),
    ),
    Collection("transcripts", RETAINED),
)


# ── C. Isolated Project Scopes — `<repository>/.project-ai/` ─────────────────
# scope-model.md §C, in its order. Committed to version control, with exactly
# one gitignored subdirectory.

PROJECT_TREE: tuple[LayoutNode, ...] = (
    Dir(
        "rules",
        (
            # Project assistant persona — NOT the personal one.
            File("persona.md", Tier.TRUTH),
            # Project team cultural rules.
            File("conventions.md", Tier.TRUTH),
            # Architecture & code guidelines.
            File("engineering_specs.md", Tier.TRUTH),
        ),
        tier=Tier.TRUTH,
    ),
    Dir(
        "memory",
        (
            # Project daily team dashboard.
            File("daily_dashboard.md", Tier.TRUTH),
            # Spoken commitments & promise tracking.
            File("commitments_log.md", Tier.TRUTH),
            # Meeting SUMMARIES — the citation root for every extracted fact. A
            # commitment in this scope may cite only a meeting in this scope.
            Collection("meetings", Tier.TRUTH),
            Collection("event_log", Tier.TRUTH),
        ),
    ),
    # PROJECT-SPECIFIC SKILLS. As with the personal scope, the `.py` names in
    # `scope-model.md` §C are the team's to choose and are not declared.
    Collection("skills", Tier.TRUTH),
    # RAW CAPTURES (gitignored, encrypted; 30-day purge). At the scope root
    # rather than under `memory/`, because `GITIGNORE_REQUIRED` anchors the
    # exclusion at `/.project-ai/transcripts/`. Move this and
    # `assert_capture_dir_ignored` still reports "protected" for a directory git
    # tracks, which is how verbatim minutes reach the employer's repository.
    Collection("transcripts", RETAINED),
)


SCOPE_TREES: Mapping[ScopeKind, tuple[LayoutNode, ...]] = MappingProxyType(
    {
        ScopeKind.APPLICATION: APPLICATION_TREE,
        ScopeKind.PERSONAL: PERSONAL_TREE,
        ScopeKind.PEOPLE: PEOPLE_TREE,
        ScopeKind.PROJECT: PROJECT_TREE,
    }
)


# ── Everything below is derived from the trees ───────────────────────────────


@dataclass(frozen=True, slots=True)
class Placement:
    """One declared node, addressed.

    `key` is the canonical spelling `artifacts_in` reports and the derived tables
    are keyed by: the bare basename where that is unambiguous inside the scope,
    the full relative path otherwise.
    """

    key: str
    relative: Path
    node: LayoutNode


def walk_tree(
    nodes: Sequence[LayoutNode], prefix: str = ""
) -> Iterator[tuple[str, LayoutNode]]:
    """Every node in a tree, paired with its relative key.

    The relative key is the single source of truth for a node's location: the
    `Path` below is derived from it, never composed a second way.
    """
    for node in nodes:
        relative = f"{prefix}{node.name}"
        yield (f"{relative}/" if node.is_dir else relative), node
        yield from walk_tree(node.children, f"{relative}/")


def index_tree(
    nodes: Sequence[LayoutNode],
) -> tuple[tuple[Placement, ...], Mapping[str, Placement], frozenset[str]]:
    """Address one scope's tree: its placements, its keys, and its collisions."""
    walked = list(walk_tree(nodes))
    duplicated = {key for key, count in Counter(k for k, _ in walked).items() if count > 1}
    if duplicated:
        raise MalformedLayout(f"a scope tree declares {sorted(duplicated)} twice")

    basenames = Counter(node.key for _, node in walked)
    ambiguous = frozenset(name for name, count in basenames.items() if count > 1)

    placements: list[Placement] = []
    address: dict[str, Placement] = {}
    for relative_key, node in walked:
        canonical = relative_key if node.key in ambiguous else node.key
        placement = Placement(canonical, Path(relative_key.rstrip("/")), node)
        placements.append(placement)
        address[relative_key] = placement
        if node.key not in ambiguous:
            address[node.key] = placement
    return tuple(placements), MappingProxyType(address), ambiguous


_INDEXED = {kind: index_tree(tree) for kind, tree in SCOPE_TREES.items()}

PLACEMENTS: Mapping[ScopeKind, tuple[Placement, ...]] = MappingProxyType(
    {kind: indexed[0] for kind, indexed in _INDEXED.items()}
)
ADDRESS: Mapping[ScopeKind, Mapping[str, Placement]] = MappingProxyType(
    {kind: indexed[1] for kind, indexed in _INDEXED.items()}
)
AMBIGUOUS: Mapping[ScopeKind, frozenset[str]] = MappingProxyType(
    {kind: indexed[2] for kind, indexed in _INDEXED.items()}
)


def _key_scopes() -> Mapping[str, frozenset[ScopeKind]]:
    """Which scope kinds answer to each key, ambiguous basenames included.

    An ambiguous basename is still a *known* key: asking for it in the wrong
    scope should say "not in this scope", and asking in the right one should say
    "ambiguous". Dropping it here would report it as an unknown artifact and hide
    both answers.
    """
    scopes: dict[str, set[ScopeKind]] = {}
    for kind in SCOPE_TREES:
        for key in (*ADDRESS[kind], *AMBIGUOUS[kind]):
            scopes.setdefault(key, set()).add(kind)
    return MappingProxyType({k: frozenset(v) for k, v in scopes.items()})


KEY_SCOPES: Mapping[str, frozenset[ScopeKind]] = _key_scopes()

# Every key the resolver can answer, in any scope.
KEYS: frozenset[str] = frozenset(KEY_SCOPES)


def declared_nodes(kind: ScopeKind) -> tuple[Placement, ...]:
    """Every node declared in that scope's tree, in declaration order."""
    return PLACEMENTS[kind]


def artifacts_in(kind: ScopeKind) -> frozenset[str]:
    """Every artifact that scope kind may hold, at its canonical spelling.

    One entry per declared node, never two spellings of one node: callers count
    on `artifacts_in` resolving to as many distinct paths as it has members.
    """
    return frozenset(placement.key for placement in PLACEMENTS[kind])


# ── The durability projection ────────────────────────────────────────────────
# One pass over every tree, collapsing the per-node declarations onto the flat,
# basename-keyed spellings `pm_ai.storage` and `pm-ai reindex` address artifacts
# by. Derived, never hand-written — which is the point: a key cannot appear here
# without a node, and a node cannot exist without a durability.


def _durability_index() -> Mapping[str, Durability]:
    """Each artifact key and the one durability every node declaring it agrees on.

    Disagreement is refused rather than resolved. The flat key is shared across
    scopes on purpose — `persona.md` is Tier 1 Markdown truth whether it is the
    personal coach persona or the project assistant one — but that is a claim
    about the *kind* of file, so two nodes sharing a key and declaring different
    tiers means the projection would have to lie about one of them. That is
    exactly the failure the old basename-keyed table could not detect, because it
    had no nodes to compare.
    """
    declared: dict[str, Durability] = {}
    conflicts: list[str] = []
    for kind, tree in SCOPE_TREES.items():
        for _, node in walk_tree(tree):
            durability = node.durability
            if durability is None:
                continue  # a Dir that is pure structure; its members carry theirs
            previous = declared.setdefault(node.key, durability)
            if previous is not durability:
                conflicts.append(
                    f"{node.key} is {previous.name} elsewhere and "
                    f"{durability.name} in the {kind.value} scope"
                )
    if conflicts:
        raise MalformedLayout(
            f"one artifact key, two durability promises: {sorted(conflicts)}. A "
            f"key is a claim about the kind of file, so every scope declaring it "
            f"must agree on what happens to it on backup and on rebuild."
        )
    return MappingProxyType(declared)


_DURABILITY = _durability_index()

# Every persistent artifact, assigned once. A path that appears in two tiers is
# the bug this projection exists to prevent, and it is now unrepresentable: the
# durability is a field of the node.
ARTIFACT_TIER: Mapping[str, Tier] = MappingProxyType(
    {key: value for key, value in _DURABILITY.items() if isinstance(value, Tier)}
)

REBUILD_TARGETS = frozenset(a for a, t in ARTIFACT_TIER.items() if t.rebuildable)
BACKUP_TARGETS = frozenset(a for a, t in ARTIFACT_TIER.items() if t.backed_up)

RETENTION_MANAGED: frozenset[str] = frozenset(
    a for a, d in _DURABILITY.items() if d is OutsideTierModel.RETENTION_MANAGED
)
DIAGNOSTIC_ONLY: frozenset[str] = frozenset(
    a for a, d in _DURABILITY.items() if d is OutsideTierModel.DIAGNOSTIC_ONLY
)


# The artifacts whose subject is the PM personally (AD-31). Stated separately
# from the trees on purpose: which scope's tree a node sits in is the mechanism,
# this is the intent, and a test that reads only the mechanism cannot notice the
# mechanism changing. Moving one of these into a second scope's tree is caught by
# comparing the two.
#
# A committed scope holds none of them. `event_log/`, `meetings/`, `transcripts/`
# and `daily_dashboard.md` are absent because they are per-scope by construction:
# the personal one is personal, the project one was never the PM's. `persona.md`
# is absent for the same reason — the project scope declares its own.
#
# `telemetry/` is `skills/telemetry/`, the personal scope's cross-project
# harvesters. It is code rather than a record, but it is declared in the
# sovereign hub and nowhere else, and the property this set is checked against —
# no committed scope holds it — is exactly the one that must stay true of it.
PERSONAL_SUBJECT_ARTIFACTS: frozenset[str] = frozenset(
    {
        "manager_principles.md",
        "communication_preferences.md",
        "article_sources.md",
        "strategic_goals.md",
        "coaching_1on1_history.md",
        "telemetry/",
        "telegram_cache/",
        "personal_analytics.db",
    }
)


def _assert_declarations_agree() -> None:
    """What the node types cannot enforce on their own, at import time.

    Everything a node can check about itself is checked in its `__post_init__`: a
    `File` has a `Tier`, a `Collection` has a durability, a `Dir` has members, a
    name is one segment, nothing declarable sits inside a `Collection`. What is
    left is the relationships *between* declarations.
    """
    disagreement: dict[str, set[str]] = {}
    for kind in SCOPE_TREES:
        for key, placement in ADDRESS[kind].items():
            disagreement.setdefault(key, set()).add(placement.relative.as_posix())
    split = {key: sorted(paths) for key, paths in disagreement.items() if len(paths) > 1}
    assert not split, (
        f"a key means two relative paths in two scopes: {split}. Every scope "
        f"holds a given artifact at the same relative path, which is what lets "
        f"`ScopePaths.rooted()` reproduce production rather than approximate it."
    )

    assert PERSONAL_SUBJECT_ARTIFACTS <= KEYS, (
        "a personal artifact with no path: "
        f"{sorted(PERSONAL_SUBJECT_ARTIFACTS - KEYS)}"
    )

    assert all(SCOPE_TREES.get(kind) for kind in ScopeKind), (
        "a scope kind with no declared tree: "
        f"{sorted(k.value for k in ScopeKind if not SCOPE_TREES.get(k))}"
    )

    # The three ways an artifact can be accounted for, kept pairwise disjoint so
    # that exactly one of them answers "what happens to this on backup and on
    # rebuild". `_durability_index` already refuses a key with two durabilities,
    # which is what makes these hold; they are asserted anyway because the
    # consequence of losing the property is a raw capture in a backup set or a
    # diagnostic log under a retention promise nothing implements.
    tiered = frozenset(ARTIFACT_TIER)
    assert not (tiered & RETENTION_MANAGED), (
        "an artifact is both tiered and retention-managed; it must be exactly one."
    )
    assert not (tiered & DIAGNOSTIC_ONLY), (
        "an artifact is both tiered and diagnostics-only; it must be exactly one."
    )
    assert not (DIAGNOSTIC_ONLY & RETENTION_MANAGED), (
        "an artifact is both diagnostics-only and retention-managed; a diagnostic "
        "log is not a raw capture and is under no NFR-09 purge."
    )

    # Tier 2 and Tier 3 must never share a physical artifact — the original
    # defect. `Tier.rebuildable` and `Tier.backed_up` are what keep this true, so
    # this is the assertion that notices either of them being widened.
    assert not (REBUILD_TARGETS & BACKUP_TARGETS), (
        "AD-3: an artifact is both a rebuild target and a backup target, so a "
        "rebuild would destroy state that cannot be reconstructed."
    )


_assert_declarations_agree()
