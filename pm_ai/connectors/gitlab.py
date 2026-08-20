"""GitLab harvester — class H egress, read-only by construction (AD-1, AD-9).

The HTTP call is stubbed for the slice; everything around it is the real shape a
connector must have: one method, no scheduling, no id minting, no writes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from pm_ai.domain.events import CommitPayload, NormalizedEvent, NormalizedEventType, Provenance
from pm_ai.domain.harvest import Cursor, HarvestResult
from pm_ai.domain.identity import DataScope, SourceRef, resolve_actor
from pm_ai.domain.lifecycle import CoverageWindow


@dataclass
class GitLabConnector:
    project: str
    scope: DataScope
    name: str = "gitlab"
    system: str = "gitlab"
    # Injected (AD-30), never read from the ambient environment: AD-35's coverage
    # windows are a fail-closed guard, and a guard you cannot test deterministically
    # is a guard you cannot trust.
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    _fake_api: list[dict] = field(default_factory=list)

    def emits(self) -> frozenset[NormalizedEventType]:
        """Only from the core taxonomy — a connector may not mint a type (AD-27)."""
        return frozenset({NormalizedEventType.COMMIT_PUSHED})

    def harvest(self, since: Cursor) -> HarvestResult:
        started = self.now()
        offset = int(since.token or b"0")
        rows = self._fake_api[offset:]

        events = tuple(
            NormalizedEvent(
                scope=self.scope,
                type=NormalizedEventType.COMMIT_PUSHED,
                # AD-34 grammar — not a URL, so it joins across connectors
                source_ref=SourceRef.parse(f"gitlab:{self.project}:commit:{r['sha']}"),
                # AD-34 — a native handle resolves to an Actor or to UNRESOLVED,
                # never to itself
                actor=resolve_actor(system="gitlab", handle=r["author_email"]),
                occurred_at=r["committed_at"],  # provider clock (AD-35)
                payload=CommitPayload(sha=r["sha"], message=r["message"]),
                # AD-36 — a connector may NEVER assert `external`. It cannot see
                # the executed-mutation ledger, so it cannot know whether this is
                # pm-ai's own write coming back. Normalization decides; this
                # emits the fail-closed default. Hard-coding EXTERNAL here made
                # our own comments admissible as evidence that our own promises
                # were kept.
                authored_by=Provenance.UNKNOWN,
                # no `id`: the storage service mints the surrogate (AD-34)
            )
            for r in rows
        )
        return HarvestResult(
            events=events,
            cursor=Cursor(str(len(self._fake_api)).encode()),
            # AD-35 — reported in the return type so it cannot be forgotten
            coverage=CoverageWindow(
                connector_instance=f"gitlab:{self.project}",
                start=started - timedelta(hours=4),
                end=started,
            ),
        )
