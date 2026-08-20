"""The ingestion pipeline: harvest → sanitize → normalize → persist.

Lives in `app` because it must touch a connector, the core, and storage — which
no other layer is permitted to do (AD-30).
"""

from __future__ import annotations

from pm_ai.app.wiring import Daemon
from pm_ai.core.extraction import extract
from pm_ai.core.normalize import attribute_all
from pm_ai.core.sanitize import sanitize
from pm_ai.domain.disclosure import assert_citation_legal
from pm_ai.domain.identity import TargetRef
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.domain.proposals import Proposal
from pm_ai.domain.harvest import PersistResult


def run_harvest(daemon: Daemon, instance: str) -> PersistResult:
    connector = daemon.connectors[instance]
    cursor = daemon.storage.load_cursor(instance)  # scheduler owns the cursor (AD-9)

    result = connector.harvest(cursor)

    # AD-12 — sanitization at the boundary, uniformly, outside the connector.
    for event in result.events:
        sanitize(getattr(event.payload, "message", "") or "")

    # AD-36 — the match step. Connectors emit `unknown`; this is the only layer
    # that can see the executed-mutation ledger, so this is where provenance is
    # decided. Without it, pm-ai's own writes harvest back as external evidence.
    attributed = attribute_all(result.events, daemon.storage.executed_mutations())

    persisted = daemon.storage.persist_events(attributed, scope=daemon.scope)
    daemon.storage.save_cursor(instance, result.cursor, result.coverage)  # AD-35
    return persisted


def run_transcript_ingestion(daemon: Daemon, transcript, meeting, *, provider: str = "gitlab") -> dict:
    """Ingest a bound transcript, extract, then execute or stage per AD-32.

    Lives in `app` for the same reason the harvest pipeline does: it must reach a
    connector, the core, storage, and the skill registry, which no single layer
    below is permitted to do (AD-30).
    """
    # AD-38 — check the citation direction BEFORE extracting anything. A meeting
    # owned by `personal` or `people` cannot be cited from a git-committed scope,
    # and every extraction below will cite this meeting (AD-33).
    assert_citation_legal(cited=meeting.scope, into=daemon.scope)
    daemon.meetings[meeting.meeting_id] = meeting  # Tier-1 record, the citation root
    results = extract(transcript, meeting, pm_handle=daemon.pm_handle, provider=provider)

    executed, staged = [], []
    for i, ex in enumerate(results):
        if ex.disposition == "execute":
            target = TargetRef.parse(ex.detail["target"])
            from pm_ai.core.jobs import idempotency_key

            key = idempotency_key(ex.detail["verb"], target.lock_key, ex.detail)
            inv = daemon.skills.invoke(
                f"{provider}.{ex.detail['verb']}",
                target=target, payload={"comment": ex.detail["rest"]}, idempotency_key=key,
            )
            executed.append((ex, inv))
        else:
            p = Proposal(
                proposal_id=f"prp_{meeting.meeting_id}_{i}",
                type=ex.kind,
                summary=ex.for_model[:80],
                payload=ex.detail,
                target=TargetRef.parse(ex.detail.get("target", "gitlab:alpha:issue:0")),
                cites=ex.cites,  # AD-33 — the meeting, never the transcript
                created_at=meeting.start,
                state=ProposalState.STAGED,
            )
            daemon.storage.stage_proposal(p)
            staged.append(p)
    return {"executed": executed, "staged": staged, "extractions": results}
