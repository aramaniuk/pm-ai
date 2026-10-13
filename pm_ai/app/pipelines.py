"""The ingestion pipeline: harvest → attribute → normalize → persist.

Lives in `app` because it must touch a connector, the core, and storage — which
no other layer is permitted to do (AD-30).

**No sanitization step, deliberately (story 8e).** One stood here — a
`sanitize(...)` call whose return value was discarded, under a comment claiming
AD-12 was enforced. AD-12 asks for the guard "at the consumer, not only at the
producer", and a producer-side pass is one forgotten call site away from being
false. It is `pm_ai.ports.ModelPort` that enforces it now, by accepting
`Sanitized` and never `str` for externally-sourced text.

Nothing was lost by dropping the step, and nothing here needs to replace it:
`sanitize` is a pure function, AD-29 guarantees the raw it reads is retained,
and so the derived copy is rebuilt at the consumer — not on this path, and not
on the write path, which therefore carries no second copy of anything.
"""

from __future__ import annotations

import errno
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from pm_ai.app.wiring import Daemon, read_pm_identity
from pm_ai.connectors.gitlab import GitLabConnectorAdapter
from pm_ai.connectors.graph import GraphConnector
from pm_ai.core.config import ConfigRefused
from pm_ai.core.event_log import EventLog
from pm_ai.core.command_authorization import EXECUTE
from pm_ai.core.extraction import Extraction, SpokenCommand, extract
from pm_ai.core.jobs import idempotency_key
from pm_ai.core.meeting_records import (
    DuplicateCalendarReference,
    MeetingDisplaced,
    capture_name,
)
from pm_ai.core.transcripts import MeetingDetails, bind_meeting, failure_reason, ingest
from pm_ai.core.goal_register import ARTIFACT as GOALS_ARTIFACT
from pm_ai.core.goal_register import parse_goals
from pm_ai.core.normalize import attribute_all
from pm_ai.core.transcript_acquisition import acquire_transcripts
from pm_ai.core.rendering import (
    CalendarAnswer,
    render_dashboard,
    render_project_dashboard,
)
from pm_ai.domain.events import MeetingHeldPayload, NormalizedEvent, ObservedEventType
from pm_ai.domain.identity import DataScope, ScopeKind, SourceRef
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.proposals import Proposal
from pm_ai.domain.harvest import (
    UNCLASSIFIED_FAULT_IS_RETRYABLE,
    Cursor,
    HarvestFailure,
    HarvestOutcome,
    HarvestResult,
    NoCalendarConnector,
    PartialCalendar,
    PersistResult,
    UnreadCalendar,
)
from pm_ai.domain.scope_model import ScopeResolutionError, artifacts_in
from pm_ai.domain.storage_tiers import CAPTURES
from pm_ai.domain.transcripts import (
    CaptureFormat,
    Transcript,
    TranscriptReport,
    TranscriptSource,
    TranscriptStateKind,
)
from pm_ai.ports import ArtifactBusy, CalendarReadPort
from pm_ai.storage.service import OperationalStoreBusy
from pm_ai.skills.registry import ExecutedButUnrecorded, Invocation

_log = logging.getLogger(__name__)

DASHBOARD_ARTIFACT = "daily_dashboard.md"
"""CAP-9's file, spelled once — the personal tree's copy and the project's.

Declared in exactly two scope trees (`scope_model.py:540,733`), which is what
makes `--scope people:bob` a refusal by name rather than a path that resolves to
somewhere nothing declared.
"""

PERSONAL = DataScope(ScopeKind.PERSONAL)
"""CAP-9 names `~/.manager-ai/memory/daily_dashboard.md`, so this is the default."""


def run_harvest(daemon: Daemon, instance: str) -> PersistResult:
    connector = daemon.connectors[instance]
    if isinstance(connector, GitLabConnectorAdapter) and not connector.has_credential:
        # Not set up, which is not a fault (story 8n): the built-in each watched
        # project gets holds no token until an enrolment replaces it. Nothing is
        # asked and nothing is recorded — no cursor, no window, no failure — so
        # an unconfigured connector cannot read as a broken one (AD-35, AD-39).
        # The empty write is the single writer's clock read, as for an empty
        # harvest below; it writes no line.
        return daemon.storage.persist_events((), scope=daemon.scope)
    cursor = daemon.storage.load_cursor(instance)  # scheduler owns the cursor (AD-9)

    result = connector.harvest(cursor)

    # AD-36 — the match step. Connectors emit `unknown`; this is the only layer
    # that can see the executed-mutation ledger, so this is where provenance is
    # decided. Without it, pm-ai's own writes harvest back as external evidence.
    attributed = attribute_all(
        result.events,
        daemon.storage.executed_mutations(),
        own_handles=daemon.own_handles,
    )

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
    #
    # A `meetings/` claimed by another process fails this attempt by name
    # (story `1r`): recorded as a retryable failure with the position unchanged,
    # so the scheduled harvest has a row to back off from, and then raised on for
    # the command at the terminal. A malformed record keeps propagating as it
    # always has; a displaced one, or a calendar reference recorded twice, is a
    # named refusal for that row (story 11b), whose held-meeting event is then
    # not persisted, since it would cite no record.
    #
    # **`put` returns the id it kept** (story 11b, AD-33, gates F2/F3). A
    # connector's meeting id is a calendar reference, never a record id: the
    # accessor looks the meeting up by calendar reference, then by title and
    # start, and adopts a manual-only record it finds. So the held-meeting
    # events are re-pointed at the kept id before they are persisted, and a
    # held-meeting event can only cite a record that exists. A calendar row
    # with two manual-only candidates adopts neither; both are named in the
    # result's refusals and the row is kept.
    total = len(result.records)
    kept_ids: dict[str, str] = {}
    unrecorded: set[str] = set()
    refusals: list[str] = []
    for index, meeting in enumerate(result.records, start=1):
        try:
            kept = daemon.meetings.put(meeting)
        except ArtifactBusy as busy:
            _record_busy_meetings(
                daemon, instance, cursor, (index, meeting), total, busy, result.failure
            )
            raise
        except (MeetingDisplaced, DuplicateCalendarReference) as refused:
            unrecorded.add(meeting.meeting_id)
            refusals.append(f"calendar meeting {meeting.meeting_id} was not recorded: {refused}")
            continue
        refusals.extend(kept.notices)
        if kept.meeting_id != meeting.meeting_id:
            kept_ids[meeting.meeting_id] = kept.meeting_id
        if kept.unadopted:
            refusals.append(
                f"calendar meeting {meeting.meeting_id} ({meeting.title!r}) matches "
                f"{len(kept.unadopted)} hand-recorded meetings in {meeting.scope} — "
                f"{', '.join(kept.unadopted)} — so neither was adopted and it was "
                f"recorded on its own. Merge them by hand if they are one meeting."
            )
    attributed = _cite_kept(_without_held(attributed, unrecorded), kept_ids)

    # Persist first, then record where the harvest got to. The order is the
    # matrix row about a persist that raises after page one: `persist_events` is
    # all-or-nothing, and this call sequence means a refusal there discards page
    # one's cursor *and* its coverage together rather than leaving a cursor that
    # advanced past events nobody stored.
    persisted = _persist_by_scope(daemon, attributed)
    if refusals:
        persisted = replace(persisted, refusals=(*persisted.refusals, *refusals))
    # AD-35 — `result.coverage` is `CoverageWindow | None` and `None` is passed
    # through as itself. A harvest that asked and got an empty answer *does*
    # record a window (`8i`): the connector's own clock readings either side of
    # the request it made. `None` means it did not check — the request failed,
    # nothing that came back had a readable time, everything that came back was
    # rejected as unreadable, or this machine's clock stepped backwards during
    # the request, leaving no honest interval to record. What is gone is the
    # window the connector used to fabricate from the clock alone to satisfy a
    # mandatory field, which the fail-closed guard read as evidence.
    #
    # `result.failure` travels with it, in the same write, so "ran and failed"
    # survives the process as something other than the absence of coverage.
    daemon.storage.save_cursor(instance, result.cursor, result.coverage, result.failure)

    # Story 33e — the transcripts of recorded meetings, acquired only **after**
    # the cursor and coverage are saved, so nothing in it can lose this harvest's
    # progress. It earns no coverage window and never touches a meeting record;
    # its report rides on `PersistResult` as a field. `9a` adds the `try/except`
    # around the connector call above.
    #
    # Story 11b — then every capture fetched and not yet ingested is ingested:
    # this run's and any an earlier run left (a daemon killed between capture
    # and ingestion), each recorded ingested or failed by name.
    if isinstance(connector, GraphConnector):
        report = _acquire_transcripts(daemon, instance, connector, result)
        if report is not None:
            persisted = replace(persisted, transcripts=ingest_captures(daemon, report))
    return persisted


def _without_held(
    events: tuple[NormalizedEvent, ...], meeting_ids: set[str]
) -> tuple[NormalizedEvent, ...]:
    """`events` less the held-meeting events of meetings whose record was refused."""
    if not meeting_ids:
        return events
    return tuple(
        event
        for event in events
        if not (
            event.type is ObservedEventType.CALENDAR_EVENT_HELD
            and isinstance(event.payload, MeetingHeldPayload)
            and event.payload.meeting_id in meeting_ids
        )
    )


def _cite_kept(
    events: tuple[NormalizedEvent, ...], kept_ids: dict[str, str]
) -> tuple[NormalizedEvent, ...]:
    """Each held-meeting event re-pointed at the record id `put` kept (AD-33).

    Only the events whose meeting was adopted or found under another id; every
    other event is returned as it came.
    """
    if not kept_ids:
        return events
    cited: list[NormalizedEvent] = []
    for event in events:
        payload = event.payload
        if (
            event.type is ObservedEventType.CALENDAR_EVENT_HELD
            and isinstance(payload, MeetingHeldPayload)
            and payload.meeting_id in kept_ids
        ):
            kept = kept_ids[payload.meeting_id]
            event = replace(
                event,
                source_ref=SourceRef.parse(f"meeting:{kept}"),
                payload=replace(payload, meeting_id=kept),
            )
        cited.append(event)
    return tuple(cited)


def ingest_captures(daemon: Daemon, report: TranscriptReport) -> TranscriptReport:
    """Ingest every capture `report` says is fetched and not ingested (story 11b).

    Each is read from its meeting's scope as a `GRAPH` transcript and run
    through `run_transcript_ingestion`; its row becomes `ingested`, or
    `ingest_failed` with the failure's type and a bounded message. One
    capture's failure is recorded by name and neither undoes its capture nor
    stops the others; a failed one is offered again on later runs until it has
    failed `MAX_INGEST_ATTEMPTS` times, after which `pm-ai doctor` names it. A
    capture another process held busy is not an attempt: its row is left as it
    is and the run names it. A row that cannot be written is named too. The
    capture stays on disk either way — the 30-day purge is NFR-09's.
    """
    ingested: list[tuple[DataScope, str]] = []
    failed: list[tuple[DataScope, str, str]] = []
    refusals: list[str] = []
    for scope, meeting_id in report.awaiting_ingestion:
        try:
            name, outcome = _ingest_capture(daemon, scope, meeting_id)
        except (ArtifactBusy, OperationalStoreBusy) as busy:
            refusals.append(
                f"the transcript of meeting {meeting_id} in {scope} was not "
                f"ingested: {type(busy).__name__} — another pm-ai process held what "
                f"it needed. Not counted as an attempt; tried again next run."
            )
            continue
        except Exception as failure:  # noqa: BLE001 — one capture, not the run
            reason = (
                f"the transcript of meeting {meeting_id} in {scope} could not be "
                f"ingested: {failure_reason(failure)}"
            )
            failed.append((scope, meeting_id, reason))
            refusals.extend(
                _record_ingestion(daemon, scope, meeting_id, TranscriptStateKind.INGEST_FAILED, reason)
            )
            continue
        ingested.append((scope, meeting_id))
        refusals.extend(
            _record_ingestion(
                daemon, scope, meeting_id, TranscriptStateKind.INGESTED, _ingested(name, outcome)
            )
        )
    return replace(
        report,
        ingested=tuple(ingested),
        ingest_failed=tuple(failed),
        refusals=(*report.refusals, *refusals),
    )


def _ingested(name: str, outcome: TranscriptIngestion) -> str:
    """The `ingested` row's reason: what was staged and run, and what failed."""
    reason = (
        f"{name} ingested: {len(outcome.staged)} staged, {len(outcome.executed)} "
        f"executed, {len(outcome.left)} left as the PM had acted on them."
    )
    if outcome.unrecorded:
        reason += (
            f" {len(outcome.unrecorded)} executed and could not be recorded: "
            f"{'; '.join(outcome.unrecorded)}."
        )
    if outcome.failures:
        reason += f" {len(outcome.failures)} skill call(s) failed: {'; '.join(outcome.failures)}."
    return reason


def _ingest_capture(
    daemon: Daemon, scope: DataScope, meeting_id: str
) -> tuple[str, TranscriptIngestion]:
    record = daemon.meetings.get(meeting_id, scope=scope)
    name = capture_name(meeting_id, record.meeting.start, CaptureFormat.GRAPH)
    raw = daemon.storage.read_artifact(scope=scope, artifact=CAPTURES, name=name)
    if raw is None:
        raise FileNotFoundError(f"{name} is not in {scope}'s transcripts/")
    transcript = ingest(
        raw.decode("utf-8"), record.meeting, source=TranscriptSource.GRAPH, name=name
    )
    return name, run_transcript_ingestion(daemon, transcript, record.meeting)


def _record_ingestion(
    daemon: Daemon,
    scope: DataScope,
    meeting_id: str,
    state: TranscriptStateKind,
    reason: str,
) -> tuple[str, ...]:
    """Record where one capture's ingestion got to; a sentence if that failed.

    A failure to record is returned for the report's refusals, not raised: the
    ingestion already happened or already failed, and the next run repairs a
    missing row — re-staging leaves any proposal the PM acted on as it is.
    """
    try:
        daemon.storage.record_transcript_state(
            scope,
            meeting_id,
            state,
            reason,
            retryable=state is TranscriptStateKind.INGEST_FAILED,
        )
    except Exception as unrecorded:  # noqa: BLE001 — named; the run goes on
        _log.exception("could not record %s for meeting %s in %s", state.value, meeting_id, scope)
        return (
            f"meeting {meeting_id} in {scope} was {state.value} but the row saying "
            f"so could not be written ({type(unrecorded).__name__}); the next run "
            f"offers its capture again.",
        )
    return ()


def _acquire_transcripts(
    daemon: Daemon,
    instance: str,
    connector: GraphConnector,
    result: HarvestResult,
) -> TranscriptReport | None:
    """Transcript acquisition after a Graph calendar harvest, or `None` when skipped.

    Which meetings: every one recorded within the harvest's reach-back in the
    scopes this connector files into — personal, and each project its Outlook
    category mapping names. The reach-back is the connector's own
    `first_run_reach_back`, the one span both the calendar's first run and every
    channel walk reach back across; it is at least the steady-state window, so
    every meeting a harvest records is inside it.

    Skipped for a harvest that failed outright: the provider just faulted or the
    credential did not sign in, and asking it again in the same run is the
    request the scheduler's backoff exists to defer.

    Wrapped: anything acquisition raises that nothing classified becomes a
    named, run-level refusal in the report, never a crash — the cursor and
    coverage above are already saved.
    """
    if result.outcome is HarvestOutcome.FAILED:
        return None
    # This instance's own source and no other: a second account's source would
    # be asked about meetings it never recorded. No fallback to `"graph"`.
    source = daemon.transcript_sources.get(instance)
    if source is None:
        return TranscriptReport(
            refusals=(
                f"no transcript source is wired for {instance}, so no meeting's "
                f"transcript was asked for.",
            )
        )
    try:
        now = daemon.clock()
        since = now - connector.calendar.windows.first_run_reach_back
        projects = sorted(set(connector.categories.projects.values()))
        scopes = (PERSONAL, *(DataScope(ScopeKind.PROJECT, project) for project in projects))
        return acquire_transcripts(
            source, daemon.storage, scopes=scopes, since=since, now=now
        )
    except Exception as unexpected:  # noqa: BLE001 — the harvest is already saved
        return TranscriptReport(
            refusals=(
                f"transcript acquisition after {instance}'s harvest raised "
                f"{type(unexpected).__name__}, which pm-ai cannot classify. The "
                f"harvest's cursor and coverage were saved before it ran.",
            )
        )


def _record_busy_meetings(
    daemon: Daemon,
    instance: str,
    cursor: Cursor,
    stopped_at: tuple[int, Meeting],
    total: int,
    busy: ArtifactBusy,
    fetched: HarvestFailure | None,
) -> None:
    """Record a harvest attempt `meetings/`'s claim stopped, before it is raised.

    The position saved is the one loaded, so nothing is skipped, and no coverage
    is recorded, because the events were never persisted. Records written before
    the one that was refused are durable and will be rewritten next attempt —
    `put` replaces fields whole and carries notes over, so that is idempotent.

    A failure the connector already reported for this attempt (`fetched`) is
    kept, its reason recorded ahead of the busy one: replacing it would hide a
    fault the next attempt will meet again. The record is retryable only if that
    failure was, and carries its retry hint — a busy file clears on its own, an
    expired credential does not.

    A failure of this save is logged and swallowed: the refusal the caller is
    about to raise is the fact the operator needs, and an error from the record
    of it must not replace it.
    """
    index, meeting = stopped_at
    earlier = (
        f"the {index - 1} record(s) before it were written and will be rewritten "
        f"on the next attempt."
        if index > 1
        else "no record before it was written."
    )
    reason = (
        f"meetings/ in {meeting.scope} was claimed by another pm-ai process, "
        f"so this harvest stopped at record {index} of {total} "
        f"({meeting.meeting_id}); {earlier} The harvest position is unchanged."
    )
    if fetched is None:
        failure = HarvestFailure(reason=reason, retryable=True)
    else:
        failure = HarvestFailure(
            reason=f"{fetched.reason} Then: {reason}",
            retryable=fetched.retryable,
            retry_after=fetched.retry_after,
        )
    try:
        daemon.storage.save_cursor(instance, cursor, None, failure)
    except Exception:  # noqa: BLE001 — logged; the refusal is re-raised by the caller
        _log.exception(
            "could not record that %s's harvest stopped at a busy meetings/ (%s)",
            instance,
            busy,
        )


def _persist_by_scope(
    daemon: Daemon, events: tuple[NormalizedEvent, ...]
) -> PersistResult:
    """Write each event into the scope it declares, and report the whole run.

    One `persist_events` call per scope, because an event carries its own and
    AD-38's write guard reads it: a personal meeting harvested by a
    project-scoped daemon is a `CommittedScopeLeak` the moment it is offered to
    the project's log, and it is *right* that it is — a private appointment in
    the team's ledger is the leak the scope model exists to refuse, whether or
    not that ledger is one git would carry (AD-38, revised 2026-09-03). Story
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


@dataclass(frozen=True, slots=True)
class TranscriptIngestion:
    """What one transcript's ingestion did (story 11b).

    `failures` names each execution whose skill raised; its extraction is in
    `staged` too. `unrecorded` names each execution that happened and could
    not then be recorded — not a failure, and never staged again. `left` holds
    the ids of proposals already approved, rejected or expired, which a
    re-ingestion leaves exactly as they are.
    """

    executed: tuple[tuple[Extraction, Invocation], ...]
    staged: tuple[Proposal, ...]
    extractions: tuple[Extraction, ...]
    failures: tuple[str, ...] = ()
    unrecorded: tuple[str, ...] = ()
    left: tuple[str, ...] = ()


def bind_transcript(
    daemon: Daemon,
    *,
    scope: DataScope,
    meeting_id: str | None = None,
    details: MeetingDetails | None = None,
) -> Meeting:
    """The meeting a transcript belongs to, in `scope` (D3, AD-23, AD-33).

    A named id wins and must be recorded there; otherwise a title and start
    bind to the one record they match, refuse naming every candidate when two
    or more match, and mint a manual-only record when none does. Nothing is
    written: `run_transcript_ingestion` writes the record.
    """
    return bind_meeting(daemon.meetings, scope=scope, meeting_id=meeting_id, details=details)


def run_transcript_ingestion(
    daemon: Daemon, transcript: Transcript, meeting: Meeting
) -> TranscriptIngestion:
    """Ingest a bound transcript, extract, then execute or stage per AD-32.

    Lives in `app` for the same reason the harvest pipeline does: it must reach
    the core, storage, and the skill registry, which no single layer below is
    permitted to do (AD-30).

    **Everything lands in the meeting's scope** (story 11b) — the record, and
    the citation every proposal carries — never the scope pm-ai is acting in. A
    personal meeting harvested while acting in a project is ingested, not
    refused: the check that refused it compared the meeting's scope with the
    acting scope and went. What still guards AD-38 is the storage-side check on
    every write that cites another scope.
    """
    if transcript.meeting_id != meeting.meeting_id:
        raise ValueError(
            f"the transcript is bound to meeting {transcript.meeting_id} and was "
            f"handed in with meeting {meeting.meeting_id}; every extraction would "
            f"cite a meeting it was not said in."
        )
    # Who the PM is, read once for this ingestion, so every utterance in it is
    # judged against the same answer (story 2o, AD-32).
    identity = read_pm_identity(daemon.storage).identity
    extractions = extract(transcript, meeting, identity=identity)
    # The Tier-1 record, and the citation root every extraction above points at
    # (AD-33), written into `meeting.scope`.
    #
    # **After `extract` and before the first write that cites the meeting**:
    # `extract` is the step that fails and writes nothing, so its failure leaves
    # no record for an ingestion that minted no citations; and everything the
    # loop below writes cites this meeting, so a failure part-way through
    # staging leaves a record its partial proposals can still be resolved
    # against. A transcript that extracts nothing still leaves its record: the
    # meeting happened, and `33c` records meetings with no transcript at all.
    daemon.meetings.put(meeting)

    # **Nothing below may end the ingestion** except a staging write that
    # fails: a target with no enrolled skill stages, a skill that raises has its
    # key settled failed by the registry, is named here and stages, and one that
    # ran and could not be recorded is named and never staged to run again.
    #
    # Proposal ids carry the transcript's source, so a meeting's Graph capture
    # and a hand-dropped transcript of it never overwrite each other's
    # proposals; and a proposal the PM has acted on is left as it is when the
    # same transcript is ingested again.
    executed: list[tuple[Extraction, Invocation]] = []
    staged: list[Proposal] = []
    failures: list[str] = []
    unrecorded: list[str] = []
    left: list[str] = []
    for index, extraction in enumerate(extractions):
        command = extraction.value
        if (
            extraction.disposition == EXECUTE
            and isinstance(command, SpokenCommand)
            and command.target is not None
        ):
            outcome = _execute(daemon, command, spoken_in=extraction.cites)
            if isinstance(outcome, Invocation):
                executed.append((extraction, outcome))
                continue
            if isinstance(outcome, ExecutedButUnrecorded):
                unrecorded.append(f"extraction {index} ({outcome.qualified_name}, {outcome.external_id})")
                continue
            if outcome is not None:
                failures.append(f"extraction {index}: {outcome}")
        proposal = Proposal(
            proposal_id=f"prp_{meeting.meeting_id}_{transcript.source.value}_{index}",
            type=extraction.kind,
            summary=extraction.for_model[:80],
            payload=command.payload(),
            # A promise names no work item, and a spoken target the grammar
            # refused names none pm-ai can act on: both have no target.
            target=command.target if isinstance(command, SpokenCommand) else None,
            cites=extraction.cites,  # AD-33 — the meeting, never the transcript
            created_at=meeting.start,
            state=ProposalState.STAGED,
        )
        if daemon.storage.stage_unless_acted(proposal):
            staged.append(proposal)
        else:
            left.append(proposal.proposal_id)
    return TranscriptIngestion(
        executed=tuple(executed),
        staged=tuple(staged),
        extractions=tuple(extractions),
        failures=tuple(failures),
        unrecorded=tuple(unrecorded),
        left=tuple(left),
    )


def _execute(
    daemon: Daemon, command: SpokenCommand, *, spoken_in: SourceRef
) -> Invocation | ExecutedButUnrecorded | str | None:
    """Invoke the command's skill.

    The invocation; `ExecutedButUnrecorded` when the skill ran and recording
    it failed; a failure's reason when the skill raised; or `None` when no
    skill is enrolled for the target's provider and verb, or there are no
    words to post — then the command stages and nothing is invoked.
    """
    target = command.target
    if target is None or not command.remainder.raw:
        return None
    qualified = f"{target.system}.{command.verb}"
    if not daemon.skills.is_registered(qualified):
        return None
    key = idempotency_key(command.verb, target.lock_key, command.rendering(spoken_in=spoken_in))
    try:
        return daemon.skills.invoke(
            qualified,
            target=target,
            # The spoken words, posted as a quotation of what was said — never
            # a prompt, so the original rather than the copy cleaned for a
            # model's context (AD-29). Chosen here, where it can be seen.
            payload={"comment": command.remainder.raw},
            idempotency_key=key,
        )
    except ExecutedButUnrecorded as ran:
        return ran
    except Exception as failed:  # noqa: BLE001 — named, staged, and the loop goes on
        return f"{qualified} on {target} raised {failure_reason(failed)}"


# ── The dashboard pipeline (story 23b) ───────────────────────────────────────


def run_dashboard(
    daemon: Daemon, *, scope: DataScope | None = None, now: datetime
) -> Path:
    """Read the day, render it, and write `daily_dashboard.md`. Once, then exit.

    In `app` for `run_harvest`'s reason: it has to touch storage, `core` and the
    scope model at once, which no layer below may do (AD-30). It is also the only
    layer that can reach a connector *and* the renderer, and today's schedule now
    comes from a live calendar read rather than off disk — `core` is I/O-free and
    `render_dashboard` is a pure function, so the composition root reads and the
    renderer renders.

    **Nothing here is a write but the last line.** No cursor is saved, no
    `meetings/` record is mapped, no event is persisted and no event-log entry is
    appended: a render projects truth rather than changing it, and a dashboard is
    fully derivable from Tier 1 plus a clock, so a line per day per scope would
    record nothing a reader could not reconstruct. That is also why `run_harvest`
    is not called from here — a render that also ingests makes the one artifact
    the PM reads at 07:00 a side effect of a write path, and hands a failed
    harvest a second way to spoil the morning.

    **Read and render fully before writing anything.** `write_artifact` replaces
    a file whole, so opening the target first and meeting a malformed goals file
    afterwards destroys yesterday's dashboard. The render is the last thing that
    can fail, and it returns a string.

    Returns the path written, so a surface can say where the file went without
    composing one of its own.
    """
    scope = PERSONAL if scope is None else scope
    _assert_dashboard_scope(scope)
    # First, and before any read. The display zone decides which instants count
    # as today, and it is read once here so that the day boundary the dashboard
    # *shows* and the day boundary its meetings were *selected by* cannot
    # disagree. Unset is refused rather than defaulted to UTC: a PM two zones
    # away would silently get a day that is not theirs.
    zone = _display_zone(daemon)

    meetings = _todays_calendar(daemon, scope=scope, now=now, zone=zone)
    entries = EventLog(daemon.storage).read(scope=scope)

    if scope.kind is ScopeKind.PERSONAL:
        # `read_artifact` answers `None` for an absent file, which `parse_goals`
        # is defined to read as "no file" — distinct from `b""`, a file the PM
        # created and has not filled in. The two need different sentences.
        document = render_dashboard(
            meetings,
            entries,
            parse_goals(
                daemon.storage.read_artifact(scope=scope, artifact=GOALS_ARTIFACT),
                scope=scope,
            ),
            now,
            tz=zone,
        )
    else:
        # A different function, not a flag. `render_project_dashboard` has no
        # goals parameter, so this branch cannot hand it the personal register
        # even by mistake (AD-25) — and no personal-scope artifact is opened on
        # this path at all, which is the half a signature cannot enforce.
        document = render_project_dashboard(meetings, entries, now, tz=zone)

    try:
        return daemon.storage.write_artifact(
            document.encode("utf-8"), scope=scope, artifact=DASHBOARD_ARTIFACT
        )
    except OSError as blocked:
        named = _blocked_by_a_directory(daemon, scope=scope)
        if named is None:
            raise
        raise named from blocked


def _blocked_by_a_directory(
    daemon: Daemon, *, scope: DataScope
) -> IsADirectoryError | None:
    """The write refused because a directory stands where a `File` is declared.

    `None` when it is anything else — a full disk, a read-only mount — which is
    re-raised untouched: pm-ai did not decline those, and dressing them in a
    sentence of its own would put a policy voice over a broken filesystem.

    Named here rather than in the single writer, because the writer's
    `os.replace` reports only `[Errno 21] Is a directory` with no path and no
    idea what was expected. The empty-directory case raises `IsADirectoryError`
    and the non-empty one `ENOTEMPTY`, which is why the discriminator is the
    target's own node type rather than the errno.
    """
    target = daemon.storage.paths.resolve(scope, DASHBOARD_ARTIFACT)
    if not target.is_dir():
        return None
    return IsADirectoryError(
        errno.EISDIR,
        f"{DASHBOARD_ARTIFACT} is declared a File in {scope} and a directory is "
        f"standing where it belongs, so the dashboard could not be written. "
        f"Nothing was changed; move or remove the directory",
        str(target),
    )


def _assert_dashboard_scope(scope: DataScope) -> None:
    """Refuse a scope whose tree declares no dashboard, by name.

    `resolve` would refuse it too, but at the *write*, which is after every read
    and every render — so the operator would pay for a fetch to be told the
    command was never going to work. Named here, and named against the
    declaration rather than a hand-kept list of two scopes.
    """
    if DASHBOARD_ARTIFACT in artifacts_in(scope.kind):
        return
    declared = sorted(
        kind.value for kind in ScopeKind if DASHBOARD_ARTIFACT in artifacts_in(kind)
    )
    raise ScopeResolutionError(
        f"{scope} declares no {DASHBOARD_ARTIFACT}, so there is nowhere in that "
        f"tree for this command to write. It is declared in {', '.join(declared)} "
        f"and nowhere else."
    )


def _display_zone(daemon: Daemon) -> tzinfo:
    """`config.toml`'s `display_timezone`, or a refusal — never a default.

    A typo'd zone never reaches here: `Config.__post_init__` validates the key
    against the zone database when it is loaded, so the only state left to
    handle is unset. `ConfigRefused` is the type for both, which is what keeps
    "the zone is wrong" and "the zone is missing" one outcome to an operator
    reading an exit code.
    """
    named = daemon.config.display_timezone
    if not named:
        raise ConfigRefused(
            "display_timezone is not set in config.toml, so pm-ai does not know "
            "which instants count as today. The dashboard's day boundary and the "
            "calendar read that selects its meetings both depend on it, and "
            "defaulting to UTC would put a meeting on the wrong day with nothing "
            "on the page saying so. Set it to an IANA zone, such as "
            "`display_timezone = \"Europe/Warsaw\"`."
        )
    return ZoneInfo(named)


def _todays_calendar(
    daemon: Daemon, *, scope: DataScope, now: datetime, zone: tzinfo
) -> CalendarAnswer:
    """What every enrolled calendar says about today, as one of four answers.

    **Which connector is asked is derived from what connectors declare.** The
    calendar is whichever enrolled instance's `emits()` contains
    `CALENDAR_EVENT_HELD` — a capability `ConnectorPort` already declares — so
    this slice keeps no second list of vendor names to fall out of step with the
    first.

    **Every calendar that answers is read, and no calendar's silence stops
    another's answer.** Two enrolled connectors is the ordinary two-tenant PM
    rather than a misconfiguration, so the days are merged rather than refused;
    each is asked independently, and one that fails takes down its own rows and
    nothing else.

    **Sorted instance order, so a re-run of unchanged inputs is byte-identical**
    — including which copy of a cross-invited meeting supplies the title and the
    tentative flag.
    """
    instances = sorted(
        instance
        for instance, connector in daemon.connectors.items()
        if ObservedEventType.CALENDAR_EVENT_HELD in connector.emits()
    )
    if not instances:
        # Its own value, never a `HarvestFailure`: that branch of the renderer
        # ends in `_retry_advice`, whose sentence quotes "the connector" — a
        # report attributed to a connector nobody enrolled.
        return NoCalendarConnector()

    day_start, day_end = _day_bounds(now, zone=zone)
    collected: list[Meeting] = []
    unread: list[UnreadCalendar] = []
    answered = 0
    for instance in instances:
        result = _ask(daemon, instance)
        if result.failure is not None:
            unread.append(UnreadCalendar(instance=instance, failure=result.failure))
        else:
            # Counted rather than inferred from whether rows came back. A
            # calendar that answered and held nothing *measured* the day, and a
            # calendar that could not be read did not — the whole distinction the
            # union exists for, and one an empty list cannot carry.
            answered += 1
        # A failed fetch still carries the spans it did walk, and those meetings
        # are real. Data that is present is never discarded because data
        # elsewhere is missing — which is the same rule that makes a partial day
        # a rendered day rather than a refused one.
        #
        # `records` as well as `live`, because the day is the whole day: `live`
        # holds what has not ended and `records` what has, and a dashboard read
        # at 15:00 that showed only the former would report an afternoon of four
        # finished meetings as an empty calendar.
        collected.extend(
            meeting
            for meeting in (*result.records, *result.live)
            if _on_day(meeting, start=day_start, end=day_end)
            and _belongs(meeting, scope=scope)
        )

    meetings = _deduplicate(collected)
    if not unread:
        return meetings
    if answered or meetings:
        # Something was read. **Whether any calendar answered, not whether the
        # merged list is non-empty**: a connector reporting an empty day and a
        # connector that could not be reached are different facts, and collapsing
        # the first into the `HarvestFailure` branch would tell the reader the
        # day is unknown when one calendar measured it and found nothing.
        #
        # `meetings` alone also keeps a partial walk here: a connector that
        # failed on its second span still handed over its first span's rows, and
        # those are present data.
        return PartialCalendar(meetings=meetings, unread=tuple(unread))
    # Nothing was read at all, so there is no query result to report and the
    # renderer must say so rather than show an empty day.
    return _one_failure(unread)


def _ask(daemon: Daemon, instance: str) -> HarvestResult:
    """One connector's calendar read, with a contract breach costing only that one.

    `CalendarReadPort.read_calendar` when the connector offers it, and its
    `harvest` otherwise — so Graph's listed channels are not walked on a render.

    `ConnectorPort.harvest` reports rather than raises, and both connectors in
    this build honour it. The guard is here anyway because the whole point of
    asking each calendar independently is that one going wrong must not take the
    others' rows with it — and an exception escaping this loop would do exactly
    that, one line before the meetings that did arrive were rendered.

    The exception's **type** and not its message: this sentence is read by a
    human off a Markdown page, and a provider's own words arriving through an
    unexamined `repr` is how a token ends up in a file.
    """
    try:
        connector = daemon.connectors[instance]
        # The calendar alone when the connector offers it (2026-10-12): a live
        # read of the day walks no Teams channel, so a channel it did not need
        # cannot cost a render time or mark this calendar unread.
        if isinstance(connector, CalendarReadPort):
            return connector.read_calendar(Cursor())
        return connector.harvest(Cursor())
    except Exception as unexpected:  # noqa: BLE001 — a failure is a value here
        return HarvestResult(
            events=(),
            cursor=Cursor(),
            outcome=HarvestOutcome.FAILED,
            failure=HarvestFailure(
                reason=(
                    f"{instance} raised {type(unexpected).__name__} instead of "
                    f"reporting, which pm-ai cannot classify. That is a bug in "
                    f"pm-ai rather than a verdict about the provider."
                ),
                retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
            ),
        )


def _one_failure(unread: list[UnreadCalendar]) -> HarvestFailure:
    """Every calendar failed, as the single value the renderer's branch takes.

    One unread calendar passes its failure through **verbatim**, hint and all:
    that is the ordinary single-tenant case, and paraphrasing a connector's own
    sentence would lose the `Retry-After` the provider actually sent.

    More than one is joined rather than picked between. Each reason already
    names its own instance, so the sentence stays attributable; `retryable` is
    the conjunction, because telling an operator to wait is only honest if
    waiting clears *all* of them; and `retry_after` is dropped, because no
    provider hinted at a wait covering somebody else's fault and composing one
    would be a number nothing measured.
    """
    if len(unread) == 1:
        return unread[0].failure
    return HarvestFailure(
        reason="; ".join(entry.failure.reason for entry in unread),
        retryable=all(entry.failure.retryable for entry in unread),
    )


def _day_bounds(now: datetime, *, zone: tzinfo) -> tuple[datetime, datetime]:
    """Midnight to midnight in the display zone, as two UTC instants.

    The arithmetic is done on the *local* wall clock and converted afterwards,
    which is what makes a DST day come out at 23 or 25 hours rather than at a
    fixed 24. Adding a day to the UTC instant is the version that gets this
    wrong, silently, twice a year.
    """
    local = now.astimezone(zone)
    opened = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        opened.astimezone(timezone.utc),
        (opened + timedelta(days=1)).astimezone(timezone.utc),
    )


def _on_day(meeting: Meeting, *, start: datetime, end: datetime) -> bool:
    """Whether a meeting touches the display day at all.

    Overlap rather than "starts today", so a meeting that began yesterday
    evening and is still running at 07:00 is on the page. The renderer decides
    nothing about which day it is rendering — it renders what it is handed — so
    dropping that meeting here would be this function silently deciding the PM
    is not in it.

    A zero-length row — an all-day marker, which `33c` records as zero minutes —
    has no span to overlap with, so it is placed by its start.
    """
    if meeting.start >= end:
        return False
    finishes = meeting.start + timedelta(minutes=meeting.duration_minutes)
    if finishes > start:
        return True
    return finishes == meeting.start and meeting.start >= start


def _belongs(meeting: Meeting, *, scope: DataScope) -> bool:
    """Whether this meeting may appear on this scope's dashboard.

    **Asymmetric, deliberately.** A project dashboard carries only meetings the
    connector placed in that project: AD-38 forbids a record written to the
    project scope from referencing personal- or people-scope material, and a
    project file listing the PM's 1:1 is exactly that. The personal dashboard
    carries the PM's whole day, project meetings included, because CAP-9 asks
    for *their* morning briefing and the meetings are all on their calendar —
    filtering them out would empty the dashboard of a PM whose work is all
    categorised, which is the failure mode the honesty rules exist to prevent.

    The wall is one-directional because the leak is.
    """
    if scope.kind is ScopeKind.PERSONAL:
        return True
    return meeting.scope == scope


def _deduplicate(meetings: list[Meeting]) -> tuple[Meeting, ...]:
    """One meeting cross-invited to two tenants, listed once.

    **Matched on identity, never on resemblance.** `meeting_id` is Graph's
    per-mailbox `id`, so the same meeting reaching two tenants arrives under two
    different ids and id-matching alone would deduplicate nothing in the case
    that motivates deduplicating at all. `ical_uid` is the key that survives the
    crossing, and the key is the **pair** `(ical_uid, start)`: whether Graph
    reuses one `iCalUId` across a recurring series' occurrences is unmeasured —
    the 2026-09-06 spike listed the field and never compared two occurrences —
    and the pair is correct either way, because two tenants' copies of one
    meeting share the instant and two occurrences of a series do not.

    The `meeting_id` fallback applies **only when neither side carries a uid**.
    A meeting that carries one has already been judged on it, and tying it to
    another by id as well collapses two meetings whose uids disagree — where a
    disagreeing uid is the evidence that they are different meetings, not a tie
    for the id to break.

    **Never on start and title.** Two organisations each holding a "Weekly Sync"
    at 09:00 is a real double-booking, and it is the single most actionable fact
    on the page — merging it away would hide the one thing the 07:00 reader most
    needs to see.

    The first copy in sorted-instance order is the one kept, so a re-run of
    unchanged inputs is byte-identical down to which copy supplied the title and
    the tentative flag.
    """
    kept: list[Meeting] = []
    seen: set[tuple[str, datetime]] = set()
    seen_unidentified: set[str] = set()
    for meeting in meetings:
        uid = meeting.ical_uid
        if uid:
            key = (uid, meeting.start)
            if key in seen:
                continue
            seen.add(key)
            # Deliberately **not** also recorded by id. A meeting carrying a uid
            # has been judged on it; falling back to the id for it as well is
            # what collapsed two meetings that share an id and disagree about
            # their uid — and a uid that disagrees is evidence of difference.
        else:
            if meeting.meeting_id in seen_unidentified:
                continue
            seen_unidentified.add(meeting.meeting_id)
        kept.append(meeting)
    return tuple(kept)
