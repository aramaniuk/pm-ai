"""The single writer (AD-5), with tiers physically separated (AD-3).

Tier 1 is markdown segments on disk. Tier 2 and Tier 3 are separate stores, so
`reindex` cannot reach operational state by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from pm_ai.domain.disclosure import assert_writable
from pm_ai.domain.events import NormalizedEvent
from pm_ai.domain.harvest import Cursor, PersistResult
from pm_ai.domain.identity import DataScope, TargetRef
from pm_ai.domain.storage_tiers import Tier


def _ulid() -> str:
    """Surrogate id, minted here and nowhere else (AD-34)."""
    import secrets

    return "evt_" + secrets.token_hex(10)


@dataclass
class _Tier2:
    """Operational state — durable, never rebuilt (AD-3)."""

    cursors: dict[str, Cursor] = field(default_factory=dict)
    coverage: list[object] = field(default_factory=list)
    executed: dict[str, tuple[str, str]] = field(default_factory=dict)


class StorageService:
    """Owns every write. Nothing else opens a file for writing (AD-5)."""

    tier_of_operational = Tier.OPERATIONAL

    def __init__(self, root: Path) -> None:
        self._root = root
        self._t2 = _Tier2()
        self._seen: set[tuple[str, str]] = set()  # natural keys (AD-34)

    # ── Tier 1: append-only markdown segments (AD-5) ─────────────────────────

    def _segment(self, scope: DataScope, ledger: str, at: datetime) -> Path:
        d = self._root / str(scope).replace(":", "_") / ledger
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{at:%Y-%m}.md"

    def append_event_log(self, entry: str, *, scope: DataScope) -> None:
        at = datetime.now(timezone.utc)
        with self._segment(scope, "event_log", at).open("a", encoding="utf-8") as fh:
            fh.write(entry.rstrip("\n") + "\n")

    def persist_events(
        self, events: tuple[NormalizedEvent, ...], *, scope: DataScope
    ) -> PersistResult:
        at = datetime.now(timezone.utc)
        persisted = duplicates = 0
        lines: list[str] = []
        for ev in events:
            if ev.natural_key in self._seen:  # AD-34 — re-harvest is idempotent
                duplicates += 1
                continue
            self._seen.add(ev.natural_key)
            stamped = replace(ev, ingested_at=at)  # AD-35 — local clock, assigned here
            assert_writable(stamped, scope=scope)  # AD-38
            lines.append(
                f"- [{_ulid()}] {stamped.type.value} "
                f"actor={stamped.actor.actor_id} src={stamped.source_ref} "
                f"occurred_at={stamped.occurred_at.isoformat() if stamped.occurred_at else 'unknown'} "
                f"ingested_at={at.isoformat()} authored_by={stamped.authored_by.value}"
            )
            persisted += 1
        if lines:
            with self._segment(scope, "event_log", at).open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        return PersistResult(persisted=persisted, duplicates=duplicates, at=at)

    # ── Tier 2: operational, never rebuilt (AD-3) ────────────────────────────

    def load_cursor(self, instance: str) -> Cursor:
        return self._t2.cursors.get(instance, Cursor())

    def save_cursor(self, instance: str, cursor: Cursor, coverage: object) -> None:
        self._t2.cursors[instance] = cursor
        self._t2.coverage.append(coverage)

    def was_executed(self, idempotency_key: str) -> bool:
        return idempotency_key in self._t2.executed

    def record_execution(self, idempotency_key: str, target: TargetRef, external_id: str) -> None:
        self._t2.executed[idempotency_key] = (target.lock_key, external_id)

    def executed_mutations(self) -> dict[str, tuple[str, str]]:
        """Read by normalization to mark harvested events as pm-ai's own (AD-36)."""
        return dict(self._t2.executed)
