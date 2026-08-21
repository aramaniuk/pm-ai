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

    One method, deliberately. A named accessor per store (`operational_store`,
    `derived_store`, …) would put the artifact-to-scope mapping on both sides of
    the boundary; `resolve` keeps that mapping wholly inside the resolver, which
    is the table that decides whether a record may exist in a scope at all.
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


@runtime_checkable
class StoragePort(Protocol):
    """AD-5 — the single writer, behind a port."""

    def persist_events(self, events: tuple[NormalizedEvent, ...], *, scope: DataScope) -> PersistResult: ...
    def load_cursor(self, instance: str) -> Cursor: ...
    def save_cursor(self, instance: str, cursor: Cursor, coverage: object) -> None: ...
    def was_executed(self, idempotency_key: str) -> bool: ...
    def record_execution(self, idempotency_key: str, target: TargetRef, external_id: str) -> None: ...
    def append_event_log(self, entry: str, *, scope: DataScope) -> None: ...


@runtime_checkable
class SkillPort(Protocol):
    """AD-1 class M — the only egress that mutates."""

    name: str
    system: str
    permission: SkillPermission

    def execute(self, target: TargetRef, payload: dict) -> str:
        """Perform the mutation, return the external id it produced."""
