"""Protocol definitions, expressed in domain types (AD-30).

Imports only `pm_ai.domain`. Adapters implement these; core depends on them.
"""

from __future__ import annotations

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
