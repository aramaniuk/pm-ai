"""Protocol definitions, expressed in domain types (AD-30).

Imports nothing from `pm_ai` except `pm_ai.domain`; stdlib value types
(`pathlib.Path`, `datetime`) are permitted, because a protocol has to be able to
say what it returns. Adapters implement these; core depends on them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pm_ai.domain.events import NormalizedEvent, NormalizedEventType
from pm_ai.domain.harvest import Cursor, HarvestResult, PersistResult
from pm_ai.domain.identity import DataScope, SkillPermission, TargetRef
from pm_ai.domain.vcs import TrackingVerdict


@runtime_checkable
class ConnectorPort(Protocol):
    """AD-9 — one method, and no scheduling of its own."""

    name: str
    system: str

    def emits(self) -> frozenset[NormalizedEventType]:
        """The subset of the core taxonomy this connector produces (AD-27)."""

    def harvest(self, since: Cursor) -> HarvestResult:
        """Auth, fetch, map-to-schema. Read-only — class H egress (AD-1)."""


@runtime_checkable
class ScopePathPort(Protocol):
    """AD-4/AD-26 — where a scope keeps a given artifact.

    `StorageService` writes through this instead of importing the resolver:
    `pm_ai.storage` and `pm_ai.platform` are independent siblings in the import
    graph, so the composition root builds `pm_ai.platform.paths.ScopePaths` and
    passes it in. Declaring the shape here is what lets the single writer name
    its dependency without reaching across that boundary.

    One method for artifacts, deliberately. A named accessor per store
    (`operational_store`, `derived_store`, …) would put the artifact-to-scope
    mapping on both sides of the boundary; `resolve` keeps that mapping wholly
    inside the resolver, which is the table that decides whether a record may
    exist in a scope at all.

    `gitignore` is not an exception to that rule but a case outside it: the file
    it names belongs to the repository *containing* a project scope, so no scope
    tree declares it and `resolve` cannot address it. The alternative was for the
    single writer to compose `repository(project_id) / ".gitignore"` itself,
    which is a second copy of a layout fact (AD-4) in the layer least able to
    own one.
    """

    def resolve(self, scope: DataScope, artifact: str, *, create: bool = False) -> Path:
        """The absolute path of `artifact` in `scope`; `create` makes its directory.

        Never creates the file itself — content is the single writer's alone
        (AD-5).

        Refuses rather than guessing: an unknown artifact, an artifact that does
        not exist in this scope, an unregistered project, or a subject id that
        cannot be a directory name all raise. Every refusal is a
        `pm_ai.domain.ScopeResolutionError`, which is the only exception type a
        caller may rely on — the concrete classes live in the resolver's own
        module, which callers of this port are forbidden to import.
        """

    def gitignore(self, project_id: str) -> Path:
        """The `.gitignore` of the repository project `project_id` was enrolled from.

        Returned whether or not the file exists: an absent one is precisely the
        case `assert_capture_dir_ignored` must refuse, so "missing" has to be
        readable as "no rule" rather than arriving as a resolver refusal.

        Refuses an unregistered project or an unusable id, exactly as `resolve`
        does, and by the same exception type.
        """


@runtime_checkable
class VcsPort(Protocol):
    """AD-23/AD-38 — whether version control would carry a path into a commit.

    The single writer must not write a raw capture into a directory git tracks,
    and only git can answer whether it does. Text matching cannot: a negation
    line re-includes an excluded directory, a parent-directory exclude protects a
    child no rule names, and a directory already in the index is tracked whatever
    `.gitignore` says afterwards.

    A port rather than a direct call because answering means running `git`, and
    `.importlinter` forbids `subprocess` in `pm_ai.storage` — the adapter lives in
    `pm_ai.platform`, which is the layer AD-1 permits to shell out. That is the
    same boundary `ScopePathPort` exists for, and it lands the same way: the
    composition root builds the adapter and hands it to the writer.

    Implementations answer or raise. There is no third state and no default: an
    adapter that returned "probably fine" when git was missing would be the leak
    this port exists to prevent, arriving as a fallback.
    """

    def tracking(self, path: Path, *, repository: Path) -> TrackingVerdict:
        """Git's verdict on `path`, as seen from `repository`.

        `path` need not exist. The first capture write asks about a directory
        that is about to be created, and the answer must be the same one git
        would give afterwards.

        Raises `pm_ai.domain.VcsUnavailable` for every reason the question cannot
        be answered — no repository, no `git` binary, a path outside the
        repository, a timeout, an unrecognised failure. The caller refuses on it:
        unknown is not permission.
        """

    def working_tree(self, path: Path) -> Path | None:
        """The root of the git working tree containing `path`, or `None`.

        `None` is an *answer*, not a failure: this path is not inside a working
        tree, so there is nothing for git to carry into a commit and nothing to
        be excluded from. That distinction is what lets the capture guard cover
        every scope without refusing writes on a machine where the personal scope
        is an ordinary directory.

        Asked before `tracking`, because `tracking` needs a repository to be
        asked *from*, and which repository that is cannot be known from the scope
        — the project scope lives in the employer's checkout, the personal scope
        may be a private repository of its own, and either may be neither.

        `path` need not exist. Every first capture write concerns a directory
        about to be created, and the answer must be the one git would give
        afterwards.

        Raises `pm_ai.domain.VcsUnavailable` when the question cannot be
        answered at all — no `git` binary, a timeout, an exit code with no
        documented meaning. Not being in a repository is not one of those.
        """

    def repository_marker_above(self, path: Path) -> Path | None:
        """A `.git` at or above `path`, found without running anything.

        The fallback for when `working_tree` could not answer. git is *optional*:
        a machine without it, or a project that is not a checkout, must still be
        able to record a meeting. But "pm-ai cannot find git" is not the same
        fact as "no repository exists" — the daemon runs under `launchd` with a
        minimal PATH, so it can easily miss a `git` the developer's own shell
        uses. Captures would then land in a genuinely tracked directory.

        Answering "am I inside a repository at all" needs no binary; only "would
        git ignore this" does. So this narrows the refusal to the one case that
        can actually leak: a repository demonstrably present, and no way to ask
        it anything.

        Returns the `.git` itself, so a refusal can name what it found. Never
        raises: a directory walk has no failure mode worth propagating, and one
        that could not read a parent has already been reported by `working_tree`.
        """


@runtime_checkable
class StoragePort(Protocol):
    """AD-5 — the single writer, behind a port."""

    def persist_events(self, events: tuple[NormalizedEvent, ...], *, scope: DataScope) -> PersistResult: ...
    def load_cursor(self, instance: str) -> Cursor: ...
    def save_cursor(self, instance: str, cursor: Cursor, coverage: object) -> None: ...
    def was_executed(self, idempotency_key: str) -> bool: ...
    def append_event_log(self, entry: str, *, scope: DataScope) -> None: ...
    # AD-20 is two-phase, and this port declared only the one-shot form until
    # 2026-08-22. The key is *claimed* before the outbound call and *settled*
    # after, because recording only on success leaves a crash window in which
    # the mutation happened and the ledger does not know — the retry then acts
    # twice. `pm_ai.skills.registry` calls all three of these; a port that named
    # none of them left the class enforcing AD-18 and AD-20 typed as `object`,
    # which is how the security boundary became the least-checked code here.
    def begin_execution(self, idempotency_key: str, target: TargetRef) -> str: ...
    def settle_execution(self, idempotency_key: str, external_id: str) -> None: ...
    def executed_mutations(self) -> dict[str, tuple[str, str]]: ...
    # The single-phase convenience over the two above. Kept because it has a
    # caller; not what the registry uses.
    def record_execution(self, idempotency_key: str, target: TargetRef, external_id: str) -> None: ...


@runtime_checkable
class SkillPort(Protocol):
    """AD-1 class M — the only egress that mutates."""

    name: str
    system: str
    permission: SkillPermission

    def execute(self, target: TargetRef, payload: dict) -> str:
        """Perform the mutation, return the external id it produced."""
