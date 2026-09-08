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
from pm_ai.domain.events import NormalizedEvent
from pm_ai.domain.identity import DataScope, TargetRef
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

    # The Tier-1 records this harvest earned, written **before** their events
    # (story 33c). A `CALENDAR_EVENT_HELD` cites `meeting:<id>` (AD-33), so an
    # event persisted ahead of a failed `meetings/` write leaves a citation
    # nothing can resolve — and the failure is propagated rather than counted,
    # because the alternative is a batch of events whose referents are missing.
    #
    # Through `11a`'s accessor, and to `meeting.scope` rather than the daemon's:
    # a connector may not import `pm_ai.storage`, which is why the records travel
    # out on the result and are written here.
    #
    # **No `scope=` here, while every `persist_events` call below carries one,
    # and the asymmetry is the two writers' rather than an oversight.**
    # `MeetingRecords.put` takes no scope at all: it reads `meeting.scope` and
    # writes into that tree, so a record cannot be offered to a scope other than
    # its own and there is no cross-scope offer for a guard to refuse. An event
    # can — `persist_events` takes the destination as an argument — which is why
    # that path had to be restructured to group by `event.scope` the moment a
    # connector emitted in two.
    #
    # So no, `put` does **not** run `disclosure.assert_writable`, the AD-38 write
    # guard `_append_batch` runs on every event — and running it here would be
    # running a tautology. That guard compares the scopes a record *references*
    # against the scope it is *bound for*, and for a `Meeting` those are one
    # value: `referenced_scopes` reads `record.scope`, which is the very field
    # `put` derives the destination from. It can only ever compare
    # `meeting.scope` with itself. The guard has teeth on the event path because
    # there the destination is an argument that can disagree with
    # `event.scope` — which is exactly the disagreement `_persist_by_scope`
    # below exists to stop producing.
    #
    # What does bind a record is narrower and earlier: `_assert_records_meetings`
    # refuses any scope whose tree does not declare `meetings/`, and the project
    # ids a connector may put there were checked against the registry when its
    # `CategoryScopes` was built.
    for meeting in result.records:
        daemon.meetings.put(meeting)

    # Persist first, then record where the harvest got to. The order is the
    # matrix row about a persist that raises after page one: `persist_events` is
    # all-or-nothing, and this call sequence means a refusal there discards page
    # one's cursor *and* its coverage together rather than leaving a cursor that
    # advanced past events nobody stored.
    persisted = _persist_by_scope(daemon, attributed)
    # AD-35 — `result.coverage` is `CoverageWindow | None` and `None` is passed
    # through as itself. A harvest that learned nothing records no window: the
    # connector used to fabricate one from the clock to satisfy a mandatory
    # field, and the fail-closed guard read that fabrication as evidence.
    #
    # `result.failure` travels with it, in the same write, so "ran and failed"
    # survives the process as something other than the absence of coverage.
    daemon.storage.save_cursor(instance, result.cursor, result.coverage, result.failure)
    return persisted


def _persist_by_scope(
    daemon: Daemon, events: tuple[NormalizedEvent, ...]
) -> PersistResult:
    """Write each event into the scope it declares, and report the whole run.

    One `persist_events` call per scope, because an event carries its own and
    AD-38's write guard reads it: a personal meeting harvested by a
    project-scoped daemon is a `CommittedScopeLeak` the moment it is offered to
    the project's log, and it is *right* that it is — a private appointment in
    the employer's repository is the leak the scope model exists to refuse. Story
    33c is the first connector to emit events in more than one scope; before it,
    every event carried the daemon's own and this grouping produced exactly the
    single call it replaced.

    An empty harvest still calls the writer once, in the daemon's scope, so the
    `at` a caller reads is a real clock read from the single writer rather than
    one composed here (AD-5).

    All-or-nothing per scope rather than across the batch, which is the cost of
    the split and is stated rather than hidden: `persist_events` is the unit of
    atomicity and there is no cross-scope transaction to be had — the two logs
    are different files in different trees, one of them committed.
    """
    if not events:
        return daemon.storage.persist_events((), scope=daemon.scope)
    grouped: dict[DataScope, list[NormalizedEvent]] = {}
    for event in events:
        grouped.setdefault(event.scope, []).append(event)
    written = [
        daemon.storage.persist_events(tuple(batch), scope=scope)
        for scope, batch in grouped.items()
    ]
    return PersistResult(
        persisted=sum(result.persisted for result in written),
        duplicates=sum(result.duplicates for result in written),
        # The last writer's stamp, not one composed here: `at` is a clock read
        # the single writer owns (AD-5), and averaging or inventing one would put
        # a timestamp in the report that nothing measured.
        at=written[-1].at,
        flagged=sum(result.flagged for result in written),
    )


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
    results = extract(transcript, meeting, pm_handle=daemon.pm_handle, provider=provider)
    # The Tier-1 record, and the citation root every extraction above points at
    # (AD-33). Through the accessor since story 11a: this was an assignment into
    # a process-lifetime dict, so the record a citation resolved against was gone
    # the moment the daemon stopped. `meeting.scope` decides which tree it lands
    # in, which is why the legality check runs first.
    #
    # **After `extract` and before the first write that cites the meeting**, and
    # both halves of that are the ordering rather than an accident. Moving the
    # record to disk made this pipeline non-atomic in a way the dict could not
    # be: a dict assignment cannot fail and does not survive a restart, while
    # this line can raise and does. So:
    #
    # - `extract` runs first because it is the step that fails — a malformed
    #   transcript, a provider verb it cannot parse — and it produces nothing
    #   durable, so its failure leaves no record for an ingestion that minted no
    #   citations. That was the state reachable when the write came first.
    # - the record is written before the loop below, because everything the loop
    #   writes cites this meeting: a staged proposal or an executed mutation
    #   whose `cites` resolves to nothing is worse than one extraction lost, so a
    #   failure part-way through staging leaves a record its partial proposals
    #   can still be resolved against.
    #
    # What remains is a record for a transcript that extracted *nothing*, and
    # that is deliberate: the meeting happened, `Meeting` is Tier-1 in its own
    # right, and `33c` records meetings with no transcript at all.
    daemon.meetings.put(meeting)

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
