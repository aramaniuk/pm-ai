"""The proposals as every reader sees them, by path (story 13a).

One place for what the proposal queue, completion and `transcript list`'s
review step share: the scopes enrolled now, the meeting records by name, the
display zone, where a proposal belongs (`ProposalWorld.scope_of`) and the
state a reader shows (`seen_state`). Read-only: no master key, keychain or
`StorageService`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone, tzinfo
from pathlib import Path

from pm_ai.app.scope_reads import (
    ScopeView,
    read_display_config,
    read_or_none,
    scope_view,
)
from pm_ai.app.wiring import REGISTRY_ARTIFACT, registered_proposal_types
from pm_ai.core.meeting_listing import zone_of
from pm_ai.core.meeting_records import MeetingRecord
from pm_ai.core.meeting_summary import digest
from pm_ai.core.project_registry import parse_registry
from pm_ai.core.proposals import ProposalTypes
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.domain.storage_tiers import OPERATIONAL_DB
from pm_ai.platform.paths import ScopePaths
from pm_ai.storage.service import ProposalIndexRow, ProposalsUnreadable, proposal_index_at

__all__ = ["ProposalWorld", "open_world", "seen_state", "waiting_by_meeting"]

APPLICATION = DataScope(ScopeKind.APPLICATION)
PERSONAL = DataScope(ScopeKind.PERSONAL)


@dataclass
class ProposalWorld:
    """Everything one command read: projects, zone, store, the meeting views it opened."""

    resolver: ScopePaths
    projects: Mapping[str, Path]
    """The enrolled projects' roots, by id, as `projects.toml` names them now."""
    registry_fault: str | None
    zone: tzinfo
    now: datetime
    store: Path
    views: dict[str, ScopeView | str] = field(default_factory=dict)

    @property
    def scopes(self) -> list[DataScope]:
        return [PERSONAL, *(DataScope(ScopeKind.PROJECT, pid) for pid in self.projects)]

    def view(self, scope: DataScope) -> ScopeView | str:
        key = str(scope)
        if key not in self.views:
            root = None
            if scope.kind is ScopeKind.PROJECT and scope.project_id in self.projects:
                root = self.projects[str(scope.project_id)]
            self.views[key] = scope_view(self.resolver, scope, root=root)
        return self.views[key]

    def record(self, scope: DataScope, meeting_id: str) -> MeetingRecord | None:
        view = self.view(scope)
        if isinstance(view, str):
            return None
        name = view.record_name(meeting_id)
        if name is None:
            return None
        found = view.record(name)
        return None if isinstance(found, str) else found

    def record_day(self, scope: DataScope, meeting_id: str) -> str | None:
        """The UTC day at the start of the record's file name, read without parsing it."""
        view = self.view(scope)
        if isinstance(view, str):
            return None
        name = view.record_name(meeting_id)
        return None if name is None else name[:10]

    def holds(self, scope: DataScope, meeting_id: str) -> bool:
        view = self.view(scope)
        return not isinstance(view, str) and view.record_name(meeting_id) is not None

    def scope_of(self, row: ProposalIndexRow) -> DataScope | None:
        """Where a row belongs: its recorded scope, else from its id or its meeting (13a).

        A row staged before 13a records none. An action item id carries its
        scope's digest; another proposal is placed by the one scope holding its
        meeting's record. `None` when it cannot be told — never guessed (AD-4).
        """
        if row.scope:
            try:
                return DataScope.parse(row.scope)
            except ValueError:
                return None
        known = self.scopes
        head = row.proposal_id.split("_")
        if len(head) > 2 and head[0] == "prp" and len(head[1]) == 12:
            for scope in known:
                if digest(str(scope)) == head[1]:
                    return scope
        if row.meeting_id:
            holding = [scope for scope in known if self.holds(scope, row.meeting_id)]
            if len(holding) == 1:
                return holding[0]
        return None


def open_world(paths: ScopePaths | None, now: datetime) -> ProposalWorld:
    """`projects.toml` and `config.toml` read now, over `paths` or the owner's home."""
    base = paths if paths is not None else ScopePaths.production(projects={})
    roots: dict[str, Path] = {}
    fault: str | None = None
    try:
        registry = parse_registry(read_or_none(base.resolve(APPLICATION, REGISTRY_ARTIFACT)))
        roots = {pid: Path(entry.path) for pid, entry in registry.items()}
    except (OSError, ValueError) as refused:
        fault = str(refused) or type(refused).__name__
    resolver = (
        ScopePaths.production(projects=roots)
        if paths is None
        else replace(paths, project_roots=dict(roots))
    )
    zone_name, _, _ = read_display_config(resolver)
    return ProposalWorld(
        resolver=resolver,
        projects=roots,
        registry_fault=fault,
        zone=zone_of(zone_name) or timezone.utc,
        now=now,
        store=Path(resolver.resolve(APPLICATION, OPERATIONAL_DB)),
    )


def seen_state(row: ProposalIndexRow, types: ProposalTypes, now: datetime) -> ProposalState:
    """The state a reader shows for a row: a staged one past its window is `expired`."""
    state = ProposalState(row.state)
    if state is not ProposalState.STAGED or row.staged_at is None:
        return state
    rule = types.rule(row.type) if row.type is not None else None
    stored = row.ttl_seconds or 0.0
    window = (
        max(stored, rule.ttl.total_seconds()) if rule is not None and rule.known else stored
    )
    if (now - row.staged_at).total_seconds() >= window:
        return ProposalState.EXPIRED
    return state


def waiting_by_meeting(
    resolver: ScopePaths, *, now: datetime, types: ProposalTypes | None = None
) -> dict[tuple[str, str], int]:
    """How many proposals wait for review per `(scope, meeting_id)` (story 13a).

    By the indexed columns, with the registered windows; a row staged before
    13a, which records no scope, is placed by `ProposalWorld.scope_of`. A
    store that cannot be read is no counts.
    """
    chosen = types if types is not None else registered_proposal_types()
    world = ProposalWorld(
        resolver=resolver,
        projects=dict(resolver.project_roots),
        registry_fault=None,
        zone=timezone.utc,
        now=now,
        store=Path(resolver.resolve(APPLICATION, OPERATIONAL_DB)),
    )
    try:
        rows = proposal_index_at(world.store, states=(ProposalState.STAGED.value,))
    except ProposalsUnreadable:
        return {}
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        if row.type is None or row.meeting_id is None:
            continue
        if seen_state(row, chosen, now) is not ProposalState.STAGED:
            continue
        scope = world.scope_of(row)
        if scope is None:
            continue
        key = (str(scope), row.meeting_id)
        counts[key] = counts.get(key, 0) + 1
    return counts
