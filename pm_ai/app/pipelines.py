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
import secrets
import sqlite3
import threading
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, TypeVar
from zoneinfo import ZoneInfo

from pm_ai.app.concurrency import WriterStopped
from pm_ai.app.wiring import (
    CONFIG_ARTIFACT,
    GIT_QUESTION_SECONDS,
    Daemon,
    read_pm_identity,
    require_model,
)
from pm_ai.connectors.gitlab.client import FETCH_BUDGET as GITLAB_FETCH_BUDGET
from pm_ai.connectors.graph.client import FETCH_BUDGET as GRAPH_FETCH_BUDGET
from pm_ai.connectors.registry import HEALTH_PROBE_SECONDS
from pm_ai.connectors.gitlab import GitLabConnectorAdapter
from pm_ai.connectors.graph import GraphConnector
from pm_ai.core.config import Config, ConfigRefused, load_config
from pm_ai.core.event_log import EventLog
from pm_ai.core.command_authorization import EXECUTE
from pm_ai.core.extraction import Extraction, SpokenCommand, extract
from pm_ai.core.jobs import idempotency_key
from pm_ai.core.meeting_records import (
    DuplicateCalendarReference,
    MalformedMeeting,
    MeetingDisplaced,
    MeetingRecord,
    MeetingNotFound,
    capture_name,
    capture_stem,
    has_capture,
    render_record,
)
from pm_ai.core.meeting_summary import (
    SUMMARY_SHAPE,
    SummaryPrompt,
    action_item_id,
    action_item_prefix,
    decision_reference,
    instructions_for,
    summary_prompt,
    validate_answer,
)
from pm_ai.core.transcripts import (
    MAX_DROP_MINUTES,
    MIN_DROP_MINUTES,
    MeetingDetails,
    bind_candidates,
    bind_meeting,
    carried_minutes,
    drop_capture_format,
    drop_utterances,
    failure_reason,
    ingest,
    meeting_utterances,
)
from pm_ai.core.goal_register import ARTIFACT as GOALS_ARTIFACT
from pm_ai.core.goal_register import MalformedGoals, parse_goals
from pm_ai.core.normalize import attribute_all
from pm_ai.core.cadence import coverage_cutoff, look_back
from pm_ai.core.transcript_acquisition import acquire_transcripts
from pm_ai.core.rendering import (
    CalendarAnswer,
    EventLogAnswer,
    PmHandles,
    render_dashboard,
    render_project_dashboard,
)
from pm_ai.core.team_activity import WorkAnswer
from pm_ai.domain.event_entries import MalformedEntry, UnknownCategory
from pm_ai.domain.events import (
    DecisionPayload,
    MeetingHeldPayload,
    NormalizedEvent,
    ObservedEventType,
    Provenance,
)
from pm_ai.domain.handles import NO_PERSON
from pm_ai.domain.identity import Actor, DataScope, ScopeKind, SourceRef
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.domain.meeting_summaries import (
    ACTION_ITEM,
    FRONTIER_SUMMARY_REFUSED,
    ModelAnswerRefused,
    RecordedSummary,
    SummaryActionItem,
    SummaryAnswer,
    SummaryDecision,
    SummaryFence,
    SummaryLeaseLost,
    SummaryMaker,
    SummaryRefused,
    SummaryRule,
    SummaryStateKind,
)
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.model_calls import ModelAnswer, ScopeFlow
from pm_ai.domain.proposals import Proposal
from pm_ai.domain.renders import RenderUnrecorded
from pm_ai.connectors.graph.auth import StoreBusy
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
from pm_ai.domain.disclosure import CommittedScopeLeak
from pm_ai.domain.storage_tiers import CAPTURES, UnprotectedCaptureDir
from pm_ai.domain.task_classes import TaskClass
from pm_ai.domain.transcripts import (
    CaptureFormat,
    CaptureMissing,
    NothingSaid,
    CaptureNotIngested,
    DropInterrupted,
    DropRefused,
    PartState,
    Transcript,
    TranscriptReport,
    TranscriptSource,
    TranscriptStateKind,
    UnboundTranscript,
    UnreadableTranscript,
)
from pm_ai.ports import (
    ArtifactBusy,
    CalendarReadPort,
    ConnectorPort,
    CaptureAlreadyExists,
    DecryptionFailed,
    KeychainUnavailable,
    KeyNotFound,
    LocalModelUnavailable,
    ModelTimedOut,
    NoLocalModel,
    OutputCapReached,
    PossiblyTruncated,
)
from pm_ai.storage.service import (
    GitExclusion,
    GitNotAsked,
    OperationalStoreBusy,
    OperationalStoreUnavailable,
    ProposalNotFound,
    SchemaVersionChanged,
    StorageService,
)
from pm_ai.skills.registry import ExecutedButUnrecorded, Invocation
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, EXIT_UNEXPECTED, RESTART_SENTENCE

_log = logging.getLogger(__name__)

DASHBOARD_ARTIFACT = "daily_dashboard.md"
"""CAP-9's file, spelled once — the personal tree's copy and the project's.

Declared in exactly two scope trees (`scope_model.py:540,733`), which is what
makes `--scope people:bob` a refusal by name rather than a path that resolves to
somewhere nothing declared.
"""

PERSONAL = DataScope(ScopeKind.PERSONAL)
"""CAP-9 names `~/.manager-ai/memory/daily_dashboard.md`, so this is the default."""


HARVEST_FETCH_BUDGET_SECONDS = max(
    GRAPH_FETCH_BUDGET.total_seconds(), GITLAB_FETCH_BUDGET.total_seconds()
)
"""The per-connector fetch budget a scheduled harvest's bounded steps run under (9a).

Each connector bounds its own walk by its client's budget (ten minutes for both
built today); this is the deadline that abandons a fetch that does not come
back by itself, read off the clients rather than copied.
"""

_T = TypeVar("_T")


class FetchBound(Protocol):
    """How a bounded step is run: `bound(call, label)` returns `call()`'s answer, or
    raises `FetchAbandoned` when its deadline passes first (AD-5)."""

    def __call__(
        self, call: Callable[[], _T], label: str, *, own_budget: bool = False
    ) -> _T: ...


class FetchAbandoned(Exception):
    """A bounded step outlasted its deadline and was abandoned (AD-5, story 9a).

    Only a bounded step is ever abandoned: a claimed step it handed over
    already ran, and one it hands over later is refused. `after_save` is set
    when the attempt had already saved its position.
    """

    after_save: bool = False


def _unbounded(call: Callable[[], _T], label: str, *, own_budget: bool = False) -> _T:
    """A bounded step run where it is, with no deadline: the terminal's and the tests'."""
    return call()


def instance_scope(daemon: Daemon, instance: str) -> DataScope:
    """The scope `instance` was filed under at build, or the daemon's own (story 9a).

    A scheduled harvest has no current project (AD-11), so a write that names
    no scope of its own — the empty-batch receipt — goes where the instance was
    filed: its project for a GitLab instance, personal for a Graph one.
    """
    return daemon.instance_scopes.get(instance, daemon.scope)


def set_up(connector: ConnectorPort) -> bool:
    """Whether a connector can be asked at all (story 8n): a built-in GitLab
    instance holds no token until an enrolment replaces it."""
    return not (isinstance(connector, GitLabConnectorAdapter) and not connector.has_credential)


def run_harvest(
    daemon: Daemon, instance: str, *, bound: FetchBound | None = None
) -> PersistResult:
    """One harvest of `instance`, as steps: fetch, save, transcripts (stories 9a, 33e, 11b).

    `bound` runs each bounded step — the calendar fetch, the transcript fetch —
    under a deadline; without one they run where they are. A connector that
    raises instead of reporting is recorded as a failure naming the
    exception's type, never its text, with the position unchanged. A write
    that is refused propagates.
    """
    return _harvest(daemon, instance, bound=bound or _unbounded)[0]


def _harvest(
    daemon: Daemon, instance: str, *, bound: FetchBound
) -> tuple[PersistResult, HarvestFailure | None]:
    connector = daemon.connectors[instance]
    scope = instance_scope(daemon, instance)
    if not set_up(connector):
        # Not set up, which is not a fault (story 8n): the built-in each watched
        # project gets holds no token until an enrolment replaces it. Nothing is
        # asked and nothing is recorded — no cursor, no window, no failure — so
        # an unconfigured connector cannot read as a broken one (AD-35, AD-39).
        # The empty write is the single writer's clock read, as for an empty
        # harvest below; it writes no line.
        return daemon.storage.persist_events((), scope=scope), None
    cursor = daemon.storage.load_cursor(instance)  # scheduler owns the cursor (AD-9)

    # The calendar fetch: a bounded step — on a worker, no claim, no write
    # (AD-5). A connector that raises instead of reporting is caught as `_ask`
    # catches one, by type (story 9a): recorded as a failure that needs a
    # human, the position left where it was.
    try:
        result: HarvestResult = bound(lambda: connector.harvest(cursor), f"{instance}'s fetch")
    except FetchAbandoned:
        raise
    except TimeoutError:
        # A transport timeout the connector let out: waiting clears it, and it
        # is not a bug in pm-ai.
        failure = HarvestFailure(
            reason=(
                f"{instance}'s fetch timed out (TimeoutError) instead of reporting; "
                f"its position is unchanged."
            ),
            retryable=True,
        )
        daemon.storage.record_harvest_failure(instance, failure)
        return _no_events(daemon), failure
    except Exception as raised:  # noqa: BLE001 — a failure is a value here
        failure = HarvestFailure(
            reason=(
                f"{instance} raised {type(raised).__name__} instead of reporting, "
                f"which pm-ai cannot classify; its position is unchanged. That is "
                f"a bug in pm-ai rather than a verdict about the provider."
            ),
            retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
        )
        # The failure is the attempt's one claimed step: the receipt's instant
        # is the clock's, not a second write's (a busy refusal of a second
        # write would turn this recorded failure into a refused save).
        daemon.storage.record_harvest_failure(instance, failure)
        return _no_events(daemon), failure

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
        if kept.summary_dropped:
            # Story 7b: a hand edit in pm-ai's region is never kept as pm-ai's;
            # the region was dropped and the summary is made again (AD-37).
            refusals.extend(_summary_made_again(daemon, meeting, kept.meeting_id))
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
    persisted = _persist_by_scope(daemon, attributed, scope=scope)
    if refusals:
        persisted = replace(persisted, refusals=(*persisted.refusals, *refusals))
    # Story 8t — the GitLab project's members, issues and merge requests: one
    # claimed step asking git nothing, after the events and before the position
    # is saved, so a crash in between re-reads and the merge replaces rows with
    # themselves. Into the scope the instance files into (AD-3: keyed by
    # project scope and connector instance).
    # A GitLab connector files into a project; were its scope ever another
    # (the daemon's own, as `instance_scope`'s fallback), the work is not
    # written rather than crashing a harvest whose events are already saved.
    if result.work is not None and scope.kind is ScopeKind.PROJECT:
        daemon.storage.record_project_work(scope, result.work)
    elif result.work is not None:
        _log.warning(
            "%s's GitLab work was not saved: it files into %s, not a project",
            instance,
            scope,
        )
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
    #
    # Story 9a — the transcript fetch is a bounded step of the same attempt,
    # and its time counts against the harvest: it gets what the calendar fetch
    # left of the attempt's one per-connector budget, and nothing left is a
    # deadline like any other. The ingestion after it is bounded too, under a
    # budget of its own — the spec is silent on its bound, and one hung
    # ingestion must not hold every instance's harvest. A deadline abandons
    # that step alone; the attempt is recorded failed naming the last claimed
    # step it finished.
    if isinstance(connector, GraphConnector):
        graph = connector
        try:
            report = bound(
                lambda: _acquire_transcripts(daemon, instance, graph, result),
                f"{instance}'s transcript fetch",
            )
            if report is not None:
                fetched = report
                persisted = replace(
                    persisted,
                    transcripts=bound(
                        lambda: ingest_captures(daemon, fetched),
                        f"{instance}'s transcript ingestion",
                        own_budget=True,
                    ),
                )
        except FetchAbandoned as abandoned:
            # After the save: the failure recorded for it takes back the
            # success that save stamped (story 9a).
            abandoned.after_save = True
            raise
    # Story 7b — the waiting summaries are offered, at most three, each job
    # handed to the daemon's pools; nothing here waits on a model.
    offered = offer_summaries(daemon)
    if offered:
        persisted = replace(persisted, refusals=(*persisted.refusals, *offered))
    return persisted, result.failure


def _no_events(daemon: Daemon) -> PersistResult:
    """The receipt of an attempt that persisted nothing, at the daemon's one clock."""
    return PersistResult(persisted=0, duplicates=0, at=daemon.clock())


class AttemptOutcome(Enum):
    """How one scheduled attempt ended, which decides when the next is due (9a)."""

    SKIPPED = "skipped"
    """Not set up, or no longer enrolled: nothing asked, nothing recorded."""
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    """A failure was recorded: the connector's own, or one pm-ai composed."""
    REFUSED = "refused"
    """A save another command's claim or a busy store refused; nothing recorded."""
    STOPPED = "stopped"
    """The daemon's writer stopped or closed under the attempt."""


@dataclass(frozen=True, slots=True)
class HarvestAttempt:
    """One scheduled harvest of one instance, as the timer reads it (story 9a)."""

    instance: str
    outcome: AttemptOutcome
    failure: HarvestFailure | None = None
    refused: str | None = None
    """The refusal's own sentence, naming the claim or the store."""
    persisted: PersistResult | None = None


def harvest_once(
    daemon: Daemon,
    instance: str,
    *,
    bound: FetchBound | None = None,
    last_step: Callable[[], str | None] = lambda: None,
) -> HarvestAttempt:
    """The scheduled harvest's one attempt at `instance`; never raises (story 9a).

    `run_harvest`'s steps, then the transcript refusals recorded for `doctor`
    and coverage older than 90 days pruned. How it ended:

    - a fetch abandoned at its deadline is recorded failed, retryable, naming
      `last_step()` — the last claimed step the attempt finished;
    - a save refused by another command's claim or a busy store records
      nothing against the connector and is `REFUSED`, named;
    - a write the harvest could not classify is recorded failed, by type, not
      retryable — the position unchanged unless its save had been made;
    - the writer stopping under it is `STOPPED`: the shutdown's own step
      records what it must.
    """
    connector = daemon.connectors.get(instance)
    if connector is None or not set_up(connector):
        return HarvestAttempt(instance, AttemptOutcome.SKIPPED)
    try:
        persisted, failure = _harvest(daemon, instance, bound=bound or _unbounded)
    except FetchAbandoned as abandoned:
        last = last_step()
        return record_failed(
            daemon,
            instance,
            after_save=abandoned.after_save,
            failure=HarvestFailure(
                reason=(
                    f"{abandoned} "
                    + (
                        f"The last claimed step it finished was {last!r}: what that "
                        f"step and the ones before it saved stays, and nothing "
                        f"after it was saved."
                        if last
                        else "It had finished no claimed step, so nothing was saved."
                    )
                ),
                retryable=True,
            ),
        )
    except (WriterStopped, SchemaVersionChanged):
        return HarvestAttempt(instance, AttemptOutcome.STOPPED)
    except (ArtifactBusy, OperationalStoreBusy) as busy:
        return HarvestAttempt(instance, AttemptOutcome.REFUSED, refused=str(busy))
    except Exception as unexpected:  # noqa: BLE001 — recorded by type, never raised
        return record_failed(
            daemon,
            instance,
            HarvestFailure(
                reason=(
                    f"{instance}'s harvest stopped on {type(unexpected).__name__}, "
                    f"which pm-ai cannot classify. Nothing after the step it "
                    f"stopped at was saved."
                ),
                retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
            ),
        )
    try:
        report = persisted.transcripts
        if report is not None:
            daemon.storage.record_transcript_refusals(instance, transcript_refusals(report))
        # Coverage older than 90 days, pruned after each harvest: one claimed
        # step, one statement.
        daemon.storage.prune_coverage(before=coverage_cutoff(daemon.clock()))
    except (WriterStopped, SchemaVersionChanged):
        return HarvestAttempt(instance, AttemptOutcome.STOPPED, failure, persisted=persisted)
    except Exception as unrecorded:  # noqa: BLE001 — the harvest itself is saved
        _log.warning(
            "after %s's harvest, recording its transcript refusals or pruning old "
            "coverage failed (%s); the harvest itself is saved",
            instance,
            type(unrecorded).__name__,
        )
    outcome = AttemptOutcome.SUCCEEDED if failure is None else AttemptOutcome.FAILED
    return HarvestAttempt(instance, outcome, failure, persisted=persisted)


def record_failed(
    daemon: Daemon, instance: str, failure: HarvestFailure, *, after_save: bool = False
) -> HarvestAttempt:
    """Record `failure` against `instance`; the attempt it makes."""
    try:
        daemon.storage.record_harvest_failure(instance, failure, after_save=after_save)
    except (WriterStopped, SchemaVersionChanged):
        return HarvestAttempt(instance, AttemptOutcome.STOPPED, failure)
    except (ArtifactBusy, OperationalStoreBusy) as busy:
        return HarvestAttempt(instance, AttemptOutcome.REFUSED, failure, refused=str(busy))
    return HarvestAttempt(instance, AttemptOutcome.FAILED, failure)


def transcript_refusals(report: TranscriptReport) -> tuple[str, ...]:
    """Every sentence a transcript fetch and its ingestion refused with (story 9a)."""
    return (
        *report.refusals,
        *(
            f"meeting {entry.meeting_id} in {entry.scope}: {entry.reason}"
            for entry in report.refused
        ),
        *(reason for _, _, reason in report.ingest_failed),
    )


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
            done, part_failures = _ingest_capture(daemon, scope, meeting_id)
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
        for _, outcome in done:
            refusals.extend(outcome.notices)
        if part_failures:
            # A part that failed does not stop the parts after it (story 33g):
            # each is ingested on its own, and the ones that worked stay so.
            reason = (
                f"the transcript of meeting {meeting_id} in {scope} could not be "
                f"ingested: "
                + "; ".join(f"{name}: {failure_reason(error)}" for name, error in part_failures)
            )
            if done:
                reason += f". {_ingested_parts(done)}"
            failed.append((scope, meeting_id, reason))
            refusals.extend(
                _record_ingestion(daemon, scope, meeting_id, TranscriptStateKind.INGEST_FAILED, reason)
            )
            continue
        ingested.append((scope, meeting_id))
        refusals.extend(
            _record_ingestion(
                daemon, scope, meeting_id, TranscriptStateKind.INGESTED, _ingested_parts(done)
            )
        )
        # Story 7b: its summary offered — a new row waiting, or an automatic
        # summary re-opened over the whole meeting when a later part arrived.
        refusals.extend(note_summary_input(daemon, scope, meeting_id, ingested_now=bool(done)))
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


def _ingested_parts(done: Sequence[tuple[str, TranscriptIngestion]]) -> str:
    """The `ingested` row's reason over every part ingested in this run (33g)."""
    if not done:
        return "every stored transcript part was already ingested."
    return " ".join(_ingested(name, outcome) for name, outcome in done)


def _ingest_capture(
    daemon: Daemon, scope: DataScope, meeting_id: str
) -> tuple[tuple[tuple[str, TranscriptIngestion], ...], tuple[tuple[str, Exception], ...]]:
    """Ingest each stored part of this meeting not yet ingested, in part order (33g).

    Each part is read on its own, with its own attribution, and bound to the
    one meeting; once ingested it is marked so and never extracted again.
    Returns what each part's ingestion did and each part that failed: a part
    that fails does not stop the parts after it, so an unreadable part 1 never
    holds back a later part. A busy store or claim is raised, not counted. A
    meeting with no part rows was captured before parts existed: its
    `-graph.vtt` alone is ingested, as `11b`, and its failure raised.
    """
    record = daemon.meetings.get(meeting_id, scope=scope)
    rows = daemon.storage.transcript_parts(scope).get(meeting_id, ())
    if not rows:
        name = capture_name(meeting_id, record.meeting.start, CaptureFormat.GRAPH)
        return ((name, _ingest_part(daemon, scope, record.meeting, name, part=1)),), ()
    done: list[tuple[str, TranscriptIngestion]] = []
    failures: list[tuple[str, Exception]] = []
    for row in rows:
        if row.state is not PartState.STORED or row.ingested:
            continue
        name = capture_name(meeting_id, record.meeting.start, CaptureFormat.GRAPH, part=row.part)
        try:
            outcome = _ingest_part(daemon, scope, record.meeting, name, part=row.part)
            daemon.storage.mark_part_ingested(scope, meeting_id, row.part)
        except (ArtifactBusy, OperationalStoreBusy):
            raise
        except Exception as failure:  # noqa: BLE001 — one part, not the meeting
            failures.append((name, failure))
            continue
        done.append((name, outcome))
    return tuple(done), tuple(failures)


def _ingest_part(
    daemon: Daemon, scope: DataScope, meeting: Meeting, name: str, *, part: int
) -> TranscriptIngestion:
    raw = daemon.storage.read_artifact(scope=scope, artifact=CAPTURES, name=name)
    if raw is None:
        raise FileNotFoundError(f"{name} is not in {scope}'s transcripts/")
    transcript = ingest(
        raw.decode("utf-8"), meeting, source=TranscriptSource.GRAPH, name=name, part=part
    )
    return run_transcript_ingestion(daemon, transcript, meeting)


def _record_ingestion(
    daemon: Daemon,
    scope: DataScope,
    meeting_id: str,
    state: TranscriptStateKind,
    reason: str,
    *,
    unrecorded: str = "the next run offers its capture again",
) -> tuple[str, ...]:
    """Record where one capture's ingestion got to; a sentence if that failed.

    `unrecorded` ends that sentence: what follows from the missing row. A
    harvest's capture is offered again on the next run; a dropped one is not
    (story 11c), so the drop says so instead.

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
    except Exception as failure:  # noqa: BLE001 — named; the run goes on
        _log.exception("could not record %s for meeting %s in %s", state.value, meeting_id, scope)
        return (
            f"meeting {meeting_id} in {scope} was {state.value} but the row saying "
            f"so could not be written ({type(failure).__name__}); {unrecorded}.",
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
    daemon: Daemon, events: tuple[NormalizedEvent, ...], *, scope: DataScope
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

    An empty harvest still calls the writer once, in `scope` — the scope the
    instance was filed under (story 9a), since a scheduled harvest has no
    current project — so the `at` a caller reads is a real clock read from the
    single writer rather than one composed here (AD-5).

    All-or-nothing per scope rather than across the batch, which is the cost of
    the split and is stated rather than hidden: `persist_events` is the unit of
    atomicity and there is no cross-scope transaction to be had — the two logs
    are different files in different trees, one of them committed.
    """
    if not events:
        return daemon.storage.persist_events((), scope=scope)
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
    notices: tuple[str, ...] = ()
    """Sentences for the run's refusals: what went wrong beside the ingestion
    itself — a summary that could not be offered again (story 7b)."""


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
    kept = daemon.meetings.put(meeting)
    notices: tuple[str, ...] = ()
    if kept.summary_dropped:
        # Story 7b: the region was dropped (named in `kept.notices`) and the
        # summary is made again.
        notices = (*kept.notices, *_summary_made_again(daemon, meeting, kept.meeting_id))

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
            proposal_id=_proposal_id(meeting, transcript, index),
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
        notices=notices,
    )


def _proposal_id(meeting: Meeting, transcript: Transcript, index: int) -> str:
    """`prp_<meeting>_<source>_<index>`; part *n* ≥ 2 adds `p<n>` before the index (33g).

    Part 1 keeps `11b`'s form, so a meeting ingested before parts existed keeps
    its proposals, and a later part's extractions never overwrite them.
    """
    head = f"prp_{meeting.meeting_id}_{transcript.source.value}"
    if transcript.part == 1:
        return f"{head}_{index}"
    return f"{head}_p{transcript.part}_{index}"


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


# ── A hand-dropped transcript (story 11c) ────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TranscriptDropped:
    """What `pm-ai transcript drop` did, for the closing line (story 11c).

    `bound_by` is how the meeting was found: `named` (`--meeting`), `matched`
    (a title and start), or `minted` (a new manual-only record). `title` and
    `start` are the bound meeting's own, so a match can be confirmed.
    `duration_minutes` is a minted record's duration and
    `duration_from_last_cue` says it came from the file rather than an answer.
    `from_docx` says the capture is the text taken from a `.docx` (story 11d).
    `staged` counts the proposals staged and `left` those already approved,
    rejected or expired and so left as they were; a dropped transcript never
    executes anything (AD-32). `notices` are what went wrong after the
    ingestion itself succeeded.
    """

    capture: Path
    meeting_id: str
    scope: DataScope
    bound_by: str
    title: str
    start: datetime
    staged: int
    left: int = 0
    duration_minutes: int | None = None
    duration_from_last_cue: bool = False
    from_docx: bool = False
    notices: tuple[str, ...] = ()

    @property
    def minted(self) -> bool:
        return self.bound_by == DROP_MINTED


DROP_NAMED = "named"
DROP_MATCHED = "matched"
DROP_MINTED = "minted"

Identify = Callable[[], tuple[str, datetime]]
"""Asks for a dropped transcript's meeting title and its start, as aware UTC."""

Describe = Callable[[bool], tuple[tuple[Actor, ...], int | None]]
"""Asks for a meeting's attendees and, when told to, its duration in minutes."""

_INGESTION_REFUSALS: tuple[type[BaseException], ...] = (
    MalformedMeeting,
    MeetingDisplaced,
    DuplicateCalendarReference,
    ArtifactBusy,
    UnprotectedCaptureDir,
    CommittedScopeLeak,
    OperationalStoreUnavailable,
    ScopeResolutionError,
    UnreadableTranscript,
    UnboundTranscript,
    OSError,
)
"""The refusals a dropped capture's ingestion meets, by type: the record
write's (`MalformedMeeting`, `MeetingDisplaced`, a duplicated calendar
reference, a busy `meetings/`, a `meetings/` git would commit, an unresolvable
scope), the storage guard's citation refusal, a busy or unopenable operational
store (`OperationalStoreBusy` is one), the readers' and the bind's, and the
filesystem's own errors. Anything else is a defect in pm-ai and propagates as
one (exit 1), with a note naming the capture it kept."""

_NO_REINGEST = (
    "a manual capture is never offered again by a harvest, and there is no "
    "re-ingest command"
)


TRANSCRIPT_DROP_BOUND_SECONDS = GIT_QUESTION_SECONDS
"""`drop_transcript`'s bound (AD-21, story 4r): the capture guard's `git`
question (AD-43), bounded at ten seconds. Over five, so `transcript drop` sent
through the daemon is a job."""


def drop_transcript(
    daemon: Daemon,
    *,
    text: str,
    file_name: str,
    scope: DataScope,
    meeting_id: str | None = None,
    identify: Identify | None = None,
    describe: Describe | None = None,
    from_docx: bool = False,
) -> TranscriptDropped:
    """Read, bind, capture and ingest a hand-supplied transcript (story 11c, AD-23).

    In that order, and a refusal at any step leaves nothing from the later
    ones:

    1. **Read** `text` to utterances by the readers (`11b`, `11d`):
       `drop_utterances` — `NothingSaid` for a file with nothing in it, cues
       or turns that say nothing, or a `.docx` holding only its header; the
       readers' own `UnreadableTranscript` for text no reader accepts.
    2. **Bind** in `scope` by `11b`'s rule, reading the records once and
       writing nothing: a named `meeting_id` must exist there
       (`MeetingNotFound`); otherwise `identify` gives a title and start, two
       candidates are `AmbiguousMeeting`, one binds, and none asks `describe`
       for the attendees — and the duration, unless the file is WebVTT and its
       last cue gives one in range — and mints a manual-only record in memory,
       checked against the record's own rules before anything is written.
    3. **Capture** through `write_capture`, under `33e`'s name with
       `-manual.vtt` or `-manual.txt`. A meeting that already has a capture of
       any source is `CaptureAlreadyExists` naming it; the writer's own
       refusals (git cannot confirm exclusion, a taken name) pass through.
    4. **Ingest** as `TranscriptSource.MANUAL`, which writes the record and
       stages, never executes (AD-32). A refusal here is `CaptureNotIngested`,
       naming the kept capture, what was staged and how to drop again; on
       success the capture's row in the transcript table is `ingested`.

    Ctrl-C during the capture write or the ingestion is `DropInterrupted`,
    saying whether the capture was kept. A busy or unopenable operational store
    before the capture is `DropRefused`.

    The capture format is the file's extension for `.vtt` and `.txt` — a
    `.txt` holding WebVTT is still read as WebVTT and stored as `-manual.txt` —
    text taken from a `.docx` (`from_docx`) is `-manual.txt`, and otherwise
    it is what the content is. A `.docx` itself is never kept (story 11d).
    """
    utterances = drop_utterances(text, name=file_name, from_docx=from_docx)
    form = drop_capture_format(file_name, text, from_docx=from_docx)
    # `None` unless the file is stored as WebVTT and its last cue ends within
    # a meeting's length (`carried_minutes`); otherwise binding asks for the duration.
    from_cues = carried_minutes(file_name, text, from_docx=from_docx)

    try:
        meeting, bound_by, duration = _bind_drop(
            daemon,
            scope=scope,
            meeting_id=meeting_id,
            identify=identify,
            describe=describe,
            from_cues=from_cues,
        )
        home = meeting.transcript_home
        stem = capture_stem(meeting.meeting_id, meeting.start)
        names = daemon.storage.list_collection(scope=home, artifact=CAPTURES)
    except OperationalStoreUnavailable as unopenable:
        raise DropRefused(f"{unopenable} Nothing was written.") from unopenable
    if has_capture(names, stem):
        present = ", ".join(name for name in names if name.startswith(stem))
        raise CaptureAlreadyExists(
            f"meeting {meeting.meeting_id} in {home} already has a capture: "
            f"{present}. A transcript is verbatim input and is never amended or "
            f"replaced; remove that capture from {home}'s transcripts/ to drop "
            f"this one instead. Nothing was written."
        )
    name = capture_name(meeting.meeting_id, meeting.start, form)
    try:
        capture = daemon.storage.write_capture(text, scope=home, name=name)
    except KeyboardInterrupt as stopped:
        kept = _capture_kept(daemon, home, name)
        raise DropInterrupted(
            f"interrupted while the capture was being written. "
            + (
                f"{name} was written to {home}'s transcripts/ and stays, not "
                f"ingested; remove it by hand to drop again."
                if kept
                else "No capture was kept, and nothing else was written."
            )
        ) from stopped

    transcript = Transcript(
        meeting_id=meeting.meeting_id, source=TranscriptSource.MANUAL, utterances=utterances
    )
    try:
        outcome = run_transcript_ingestion(daemon, transcript, meeting)
    except KeyboardInterrupt as stopped:
        raise DropInterrupted(
            f"interrupted during ingestion. {capture} was written and stays; "
            f"{_aftermath(daemon, meeting, capture, bound_by)}"
        ) from stopped
    except _INGESTION_REFUSALS as failed:
        raise CaptureNotIngested(
            f"{capture} is stored and ingesting it against meeting "
            f"{meeting.meeting_id} in {home} failed: {failure_reason(failed)}. "
            f"{_aftermath(daemon, meeting, capture, bound_by)}"
        ) from failed
    except Exception as broken:
        broken.add_note(
            f"pm-ai: {capture} was written before this failure and stays; nothing "
            f"is recorded as ingested, and it must be removed by hand to drop "
            f"the transcript again."
        )
        raise
    notices = _record_ingestion(
        daemon,
        home,
        meeting.meeting_id,
        TranscriptStateKind.INGESTED,
        _ingested(name, outcome),
        unrecorded=(
            f"its proposals are staged regardless; {_NO_REINGEST}, so the "
            f"missing row stays missing"
        ),
    )
    # Story 7b: recorded as waiting wherever the drop runs; only the daemon
    # summarises, at its next harvest.
    notices = (
        *notices,
        *outcome.notices,
        *note_summary_input(daemon, home, meeting.meeting_id, ingested_now=False),
    )
    return TranscriptDropped(
        capture=capture,
        meeting_id=meeting.meeting_id,
        scope=home,
        bound_by=bound_by,
        title=meeting.title,
        start=meeting.start,
        staged=len(outcome.staged),
        left=len(outcome.left),
        duration_minutes=duration if bound_by == DROP_MINTED else None,
        duration_from_last_cue=bound_by == DROP_MINTED and from_cues is not None,
        from_docx=from_docx,
        notices=notices,
    )


def _bind_drop(
    daemon: Daemon,
    *,
    scope: DataScope,
    meeting_id: str | None,
    identify: Identify | None,
    describe: Describe | None,
    from_cues: int | None,
) -> tuple[Meeting, str, int | None]:
    """The meeting, how it was bound, and a minted record's duration.

    Asks only what binding needs, in order: nothing for a named meeting; the
    title and start otherwise; the attendees and duration only when nothing
    matched. The candidates are read once, and the choice is made among those
    by `11b`'s own step, so a record that vanishes between a read and a bind
    cannot turn a match into a record minted from placeholders. Writes nothing.
    """
    if meeting_id is not None or identify is None:
        meeting = bind_transcript(daemon, scope=scope, meeting_id=meeting_id)
        return meeting, DROP_NAMED, None
    title, start = identify()
    found = daemon.meetings.find_candidates(scope, title, start)
    if found:
        # One binds and several refuse naming each; the duration given here is
        # never read, because nothing is minted from a match.
        details = MeetingDetails(title=title, start=start, duration_minutes=0)
        return bind_candidates(found, scope=scope, details=details), DROP_MATCHED, None
    if describe is None:
        raise UnboundTranscript(
            f"no meeting in {scope} matches {title!r} at {start.isoformat()}, and "
            f"nothing was given to ask for the attendees a new record needs."
        )
    attendees, typed = describe(from_cues is None)
    duration = from_cues if from_cues is not None else typed
    if duration is None or not MIN_DROP_MINUTES <= duration <= MAX_DROP_MINUTES:
        raise UnboundTranscript(
            f"a new record of {title!r} needs a duration of {MIN_DROP_MINUTES} to "
            f"{MAX_DROP_MINUTES} minutes, and {duration!r} was given, so none was "
            f"minted. Nothing was written."
        )
    details = MeetingDetails(
        title=title, start=start, duration_minutes=duration, attendees=attendees
    )
    meeting = bind_candidates((), scope=scope, details=details)
    # The record's own rules, asked before the capture is written: a title or
    # attendee list `put` would refuse must not leave a capture behind it.
    render_record(MeetingRecord(meeting))
    return meeting, DROP_MINTED, duration


def _capture_kept(daemon: Daemon, scope: DataScope, name: str) -> bool:
    try:
        return name in daemon.storage.list_collection(scope=scope, artifact=CAPTURES)
    except Exception:  # noqa: BLE001 — answering a Ctrl-C; unknown reads as kept
        return True


def _aftermath(daemon: Daemon, meeting: Meeting, capture: Path, bound_by: str) -> str:
    """What a failed ingestion left, and how to drop again — for its refusal."""
    staged = _staged_so_far(daemon, meeting.meeting_id)
    if staged is None:
        said = "How many of its proposals were staged before the failure could not be read."
    elif staged:
        said = f"{staged} of its proposals were already staged before the failure and stay staged."
    else:
        said = "No proposal from it was staged."
    said += " The capture is kept, and nothing is recorded as ingested."
    if bound_by == DROP_MINTED and not _record_exists(daemon, meeting):
        return (
            f"{said} The new record {meeting.meeting_id} was not written, so "
            f"dropping again mints a new meeting id; {_NO_REINGEST}, so remove "
            f"{capture} by hand first — nothing will refer to it."
        )
    return (
        f"{said} {_NO_REINGEST[0].upper()}{_NO_REINGEST[1:]}: to drop again, "
        f"remove {capture} by hand first, then drop with `--meeting "
        f"{meeting.meeting_id}`."
    )


def _staged_so_far(daemon: Daemon, meeting_id: str) -> int | None:
    """How many of this meeting's manual proposals exist, or `None` unreadable.

    Proposal ids are `prp_<meeting>_manual_<index>` and a manual transcript
    executes nothing, so its proposals are numbered without gaps.
    """
    count = 0
    try:
        while True:
            daemon.storage.load_proposal(
                f"prp_{meeting_id}_{TranscriptSource.MANUAL.value}_{count}"
            )
            count += 1
    except ProposalNotFound:
        return count
    except Exception:  # noqa: BLE001 — a count for a refusal's sentence
        return None


def _record_exists(daemon: Daemon, meeting: Meeting) -> bool:
    try:
        daemon.meetings.get(meeting.meeting_id, scope=meeting.scope)
    except MeetingNotFound:
        return False
    except Exception:  # noqa: BLE001 — unknown: say what holds if it exists
        return True
    return True


# ── The dashboard pipeline (story 23b) ───────────────────────────────────────


CALENDAR_FETCH_BUDGET_SECONDS = GRAPH_FETCH_BUDGET.total_seconds()
"""One calendar connector's fetch budget: the most one live read may take.

The client's own wall-clock budget for a walk, read off it rather than copied.
"""


def dashboard_bound(calendar_connectors: int) -> float:
    """`run_dashboard`'s bound in seconds, declared beside it (AD-21, story 4r).

    The per-connector fetch budget, the connectors read one after another — so
    a render reading two calendars may take two budgets. Counted as at least
    one: the connector list is read again at the start of every render (AD-5),
    so the number at planning time is not a promise about the read, and a
    render is never planned as instant because a calendar was not yet
    enrolled. Far over five seconds either way, which is why `dashboard` sent
    through the daemon is acknowledged with a job.
    """
    return CALENDAR_FETCH_BUDGET_SECONDS * max(1, calendar_connectors)


CONNECTOR_CHECK_BOUND_SECONDS = HEALTH_PROBE_SECONDS
"""`pm-ai connector check`'s bound: CAP-35's ten seconds, every probe together."""


def calendar_instances(daemon: Daemon) -> tuple[str, ...]:
    """The enrolled connectors a render reads a calendar from, in sorted order."""
    return tuple(
        sorted(
            instance
            for instance, connector in daemon.connectors.items()
            if ObservedEventType.CALENDAR_EVENT_HELD in connector.emits()
        )
    )


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

    **Nothing here is a write but the last step.** No cursor is saved, no
    `meetings/` record is mapped, no event is persisted and no event-log entry is
    appended: a render projects truth rather than changing it. That is also why
    `run_harvest` is not called from here — a render that also ingests makes the
    one artifact the PM reads at 07:00 a side effect of a write path, and hands a
    failed harvest a second way to spoil the morning.

    **Read and render fully before writing anything.** `write_artifact` replaces
    a file whole, so opening the target first and meeting a malformed goals file
    afterwards destroys yesterday's dashboard. The render is the last thing that
    can fail, and it returns a string.

    Returns the path written, so a surface can say where the file went without
    composing one of its own.

    **Every render records (story 9b).** The same steps as in the daemon: the
    reads and the render, then `git`'s answer for the copy, then one step that
    publishes the file and records the render's instant last. A failure records
    itself and its reason, and yesterday's file stays — except a record that
    fails after the publish, which says the page was written
    (`RenderUnrecorded`). A hand-run render counts: a signal the PM saw on the
    page at 22:00 is not news at 07:00.
    """
    scope = PERSONAL if scope is None else scope
    assert_dashboard_scope(scope)
    try:
        document = prepare_dashboard(daemon, scope=scope, now=now)
        exclusion = ask_dashboard_exclusion(daemon, scope=scope)
        return publish_dashboard(
            daemon, scope=scope, document=document, at=now, exclusion=exclusion
        )
    except Exception as failed:
        record_failed_render(daemon, scope, failed, at=now)
        raise


def _direct(work: Callable[[], _T], label: str) -> _T:
    """A claimed step run where it is: outside the daemon, no writer thread."""
    return work()


def record_failed_render(
    daemon: Daemon,
    scope: DataScope,
    error: BaseException,
    *,
    at: datetime,
    hand: Callable[[Callable[[], None], str], None] = _direct,
) -> None:
    """Record that `scope`'s render failed and why; never raises (story 9b).

    Through `hand` — the writer thread's door in the daemon. A record that
    cannot be made is logged by type: the failure it would have recorded is
    the one the caller raises.
    """
    reason = render_failure_reason(scope, error)
    retryable = clears_by_waiting(error)
    try:
        hand(
            lambda: daemon.storage.record_render_failure(
                scope, reason, at=at, retryable=retryable
            ),
            f"record the {scope} render's failure",
        )
    except Exception as unrecorded:  # noqa: BLE001 — logged; the render's own error is raised
        _log.warning(
            "the failed %s render could not be recorded (%s)",
            scope,
            type(unrecorded).__name__,
        )


RENDER_REFUSALS: tuple[type[BaseException], ...] = (
    ArtifactBusy,
    ConfigRefused,
    MalformedGoals,
    ScopeResolutionError,
    UnprotectedCaptureDir,
    GitNotAsked,
    OperationalStoreUnavailable,
    KeyNotFound,
    KeychainUnavailable,
    StoreBusy,
)
"""pm-ai's own refusals a render can meet, whose sentences are recorded as they
are: each is composed by pm-ai and carries no provider's text. Anything else
is recorded by its type name only."""


CLEARS_BY_WAITING: tuple[type[BaseException], ...] = (
    OSError,
    ArtifactBusy,
    OperationalStoreBusy,
    StoreBusy,
    KeychainUnavailable,
)
"""A render's failures waiting can clear (story 9b): an I/O error, another
command's claim, a busy store, a busy credential store, a keychain locked
after a wake. Retried on `9a`'s waits until the next 07:00."""


def clears_by_waiting(error: BaseException) -> bool:
    """Whether a render's failure is one waiting can clear (story 9b).

    A record that failed after the publish is judged by what failed it. A
    directory standing where the file belongs is an `OSError` that needs a
    person, so it is not.
    """
    if isinstance(error, RenderUnrecorded) and error.__cause__ is not None:
        error = error.__cause__
    if isinstance(error, IsADirectoryError):
        return False
    return isinstance(error, CLEARS_BY_WAITING)


def render_failure_reason(scope: DataScope, error: BaseException) -> str:
    """The sentence recorded for a failed render: pm-ai's own words, never a
    third party's (story 9b).

    A refusal in `RENDER_REFUSALS` carries its own sentence; a directory where
    the file belongs is named without its path; anything else is named by
    type only, since its text may not be pm-ai's.
    """
    if isinstance(error, RenderUnrecorded):
        return str(error)
    if isinstance(error, ArtifactBusy):
        return f"the {scope} dashboard was not written: {error}"
    if isinstance(error, IsADirectoryError):
        return (
            f"the {scope} dashboard was not written: a directory stands where "
            f"{DASHBOARD_ARTIFACT} is declared a file. Nothing was changed; move "
            f"or remove the directory."
        )
    if isinstance(error, RENDER_REFUSALS):
        return f"the {scope} dashboard was not rendered: {error}"
    return (
        f"the {scope} dashboard was not rendered: it stopped on "
        f"{type(error).__name__}, which pm-ai cannot classify. The dashboard "
        f"file was not replaced by this attempt."
    )


def render_unrecorded(
    scope: DataScope, failed: BaseException, *, path: Path | None = None
) -> RenderUnrecorded:
    """The page was published and its record then failed: said as that (9b)."""
    why = (
        str(failed)
        if isinstance(failed, RENDER_REFUSALS)
        else f"it stopped on {type(failed).__name__}, which pm-ai cannot classify"
    )
    return RenderUnrecorded(
        f"the {scope} dashboard was written ({DASHBOARD_ARTIFACT} is the new "
        f"page), but recording its instant failed: {why}",
        path=path,
    )


class ReadBound(Protocol):
    """How a render's calendar read is run: `bound(call, label)` returns
    `call()`'s answer, or raises `FetchAbandoned` past its deadline (9b)."""

    def __call__(self, call: Callable[[], _T], label: str) -> _T: ...


def _read_where_it_is(call: Callable[[], _T], label: str) -> _T:
    """A calendar read with no deadline: the terminal's and the tests'."""
    return call()


def prepare_dashboard(
    daemon: Daemon,
    *,
    scope: DataScope,
    now: datetime,
    bound: ReadBound | None = None,
) -> bytes:
    """Every read and the render, writing nothing: `run_dashboard`'s bounded half.

    The live calendar read, the event log, the goals, the connector rows and
    the scope's last recorded render, then the render — on an I/O worker when
    the daemon runs it (AD-5, story 4r). Each calendar read is a bounded step
    under `bound` (the per-connector fetch budget in the daemon, story 9b); one
    abandoned at its deadline is a calendar that could not be read, never a
    failed render. Returns the document's bytes for `publish_dashboard` to save.
    """
    assert_dashboard_scope(scope)
    # First, and before any read. The display zone decides which instants count
    # as today, and it is read once here — from the `config.toml` this render
    # read — so that the day boundary the dashboard *shows* and the day boundary
    # its meetings were *selected by* cannot disagree. Unset is refused rather
    # than defaulted to UTC: a PM two zones away would silently get a day that
    # is not theirs.
    zone = _display_zone(daemon)

    meetings = _todays_calendar(
        daemon, scope=scope, now=now, zone=zone, bound=bound or _read_where_it_is
    )
    entries = _event_log(daemon, scope=scope)
    # Proactive Enablement looks back to this scope's last successful render,
    # however it was produced (story 9b): 24 hours when none is recorded,
    # never more than 7 days.
    since = look_back(daemon.storage.last_render(scope), now)
    # One read of the connector rows at one instant, for both the marking and
    # the sign-in line (`23c`), whichever scope renders: the rows are pm-ai's
    # own settings, not the personal store AD-25 walls off.
    reading = read_pm_identity(daemon.storage)
    pm = PmHandles(
        handles=reading.identity,
        unrecorded=reading.unrecorded,
        unchecked=tuple((row.name, row.reason) for row in reading.unchecked),
    )

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
            pm=pm,
            since=since,
        )
    else:
        # A different function, not a flag. `render_project_dashboard` has no
        # goals parameter, so this branch cannot hand it the personal register
        # even by mistake (AD-25) — and no personal-scope artifact is opened on
        # this path at all, which is the half a signature cannot enforce.
        # `8t`'s record is read here, on the worker beside the log read and
        # outside the bounded step, which `9b` keeps for the calendar alone
        # (`23g`). No GitLab request: the record is what the harvest stored.
        document = render_project_dashboard(
            meetings,
            entries,
            now,
            tz=zone,
            pm=pm,
            since=since,
            work=_project_work(daemon, scope=scope),
        )
    return document.encode("utf-8")


def ask_dashboard_exclusion(daemon: Daemon, *, scope: DataScope) -> GitExclusion:
    """Ask git now whether the dashboard's path is excluded, writing nothing (AD-43).

    The bounded step just before the claimed one (stories 4r, 9b). Asked
    afresh every render, never from a cache, and the answer is handed to
    `publish_dashboard` with the bytes, so the claimed step never calls `git`.
    A copy git cannot confirm excluded is refused here, by name.
    """
    return daemon.storage.ask_git_exclusion(scope=scope, artifact=DASHBOARD_ARTIFACT)


def publish_dashboard(
    daemon: Daemon,
    *,
    scope: DataScope,
    document: bytes,
    at: datetime,
    exclusion: GitExclusion,
) -> Path:
    """Save a rendered dashboard whole and record the render: the one claimed step.

    Under the dashboard's claim (story 9b): another command holding it refuses
    this attempt as `ArtifactBusy`, naming the claim, and nothing is written.
    Then the record's one statement opens the store's transaction, so a busy
    store refuses before the file is touched. The file is published by rename
    with `git`'s answer handed in — a kill leaves the old file or the new one,
    never half of either (AD-47) — and the record is committed last. A record
    that fails after the rename raises `RenderUnrecorded`: the page is new.
    """
    written: Path | None = None
    try:
        with daemon.storage.exclusive(scope=scope, artifact=DASHBOARD_ARTIFACT):
            with daemon.storage.recording_render(scope, at=at):
                try:
                    written = daemon.storage.write_artifact(
                        document,
                        scope=scope,
                        artifact=DASHBOARD_ARTIFACT,
                        exclusion=exclusion,
                    )
                except OSError as blocked:
                    named = _blocked_by_a_directory(daemon, scope=scope)
                    if named is None:
                        raise
                    raise named from blocked
    except Exception as failed:
        if written is None:
            raise
        raise render_unrecorded(scope, failed, path=written) from failed
    return written


def _event_log(daemon: Daemon, *, scope: DataScope) -> EventLogAnswer:
    """`scope`'s event log, or why it could not be read (story `23c`).

    Four refusals become a `HarvestFailure` carrying the refusal's own text, which
    names the segment: a line that is not a well-formed entry, an entry of a kind
    pm-ai does not know, a segment that is not UTF-8 text, and a segment that
    could not be read from disk — an `OSError`, or a `DecryptionFailed` for a
    segment that is there and cannot be opened. The value goes to the renderer and nowhere else
    — nothing saves it — so the other sections still render from their own
    inputs and the command exits zero, as an unreachable calendar does.
    """
    try:
        return EventLog(daemon.storage).read(scope=scope)
    except (
        MalformedEntry,
        UnknownCategory,
        UnicodeDecodeError,
        OSError,
        DecryptionFailed,
    ) as refused:
        # `retryable` is required by the type and read by nothing on this path:
        # the section states the reason and gives no retry advice. False is not
        # a finding that the read will keep failing — nothing here computed that.
        return HarvestFailure(reason=str(refused), retryable=False)


def _project_work(daemon: Daemon, *, scope: DataScope) -> WorkAnswer:
    """`scope`'s GitLab record as `8t` stored it, or why it could not be read (`23g`).

    A plain read through the one reader, `StoragePort.project_work`, for a
    project scope only. A scope that is not a project is refused before the
    read and the refusal propagates: AD-25's wall is never a stated line. A
    store that cannot be opened or read — the library's own error included —
    or a row a hand edit broke becomes a `HarvestFailure` carrying the
    refusal's own text: the section states it, the other sections render, and
    the command exits zero, as an unreadable log does.
    """
    if scope.kind is not ScopeKind.PROJECT:
        raise ValueError(
            f"GitLab project work is read for a project scope only, and {scope} "
            f"is not one (AD-25)."
        )
    try:
        return daemon.storage.project_work(scope)
    except (
        OperationalStoreUnavailable,
        sqlite3.Error,
        OSError,
        ValueError,
        TypeError,
    ) as refused:
        # `ValueError` here is a malformed row (a date, the labels' JSON, a
        # confidential issue stored with its own title): the scope refusal is
        # raised above, before the read.
        return HarvestFailure(reason=str(refused), retryable=False)


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


def assert_dashboard_scope(scope: DataScope) -> None:
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
    return zone_of(daemon.config)


def zone_of(config: Config) -> tzinfo:
    """`config`'s `display_timezone`, or `ConfigRefused` naming the key."""
    named = config.display_timezone
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


def display_zone_now(daemon: Daemon) -> tzinfo:
    """`config.toml` read again now, and its `display_timezone` (AD-5, story 9b).

    For the render timer, which decides when 07:00 is before any render runs:
    the zone is read at every evaluation, so a change takes effect at the next
    one. Raises `ConfigRefused` for a file the loader refuses or an unset key.
    """
    return zone_of(
        load_config(
            daemon.storage.read_artifact(
                scope=DataScope(ScopeKind.APPLICATION), artifact=CONFIG_ARTIFACT
            )
        )
    )


def _todays_calendar(
    daemon: Daemon,
    *,
    scope: DataScope,
    now: datetime,
    zone: tzinfo,
    bound: ReadBound = _read_where_it_is,
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
    instances = calendar_instances(daemon)
    if not instances:
        # Its own value, never a `HarvestFailure`: that branch of the renderer
        # ends in `_retry_advice`, whose sentence quotes "the connector" — a
        # report attributed to a connector nobody enrolled.
        return NoCalendarConnector()

    # The day asked for and the day shown are the same two instants (`23e`):
    # each calendar is read over exactly this range, and `_on_day` selects the
    # meetings shown by it.
    day_start, day_end = _day_bounds(now, zone=zone)
    day = day_start.astimezone(zone).date()
    collected: list[Meeting] = []
    unread: list[UnreadCalendar] = []
    answered = 0
    for instance in instances:
        result = _ask_within(daemon, instance, bound, start=day_start, end=day_end)
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
            if _on_day(meeting, start=day_start, end=day_end, day=day)
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


def _ask_within(
    daemon: Daemon, instance: str, bound: ReadBound, *, start: datetime, end: datetime
) -> HarvestResult:
    """`_ask` as a bounded step (story 9b): abandoned at its deadline, it is a
    calendar that could not be read, never a failed render."""
    try:
        return bound(
            lambda: _ask(daemon, instance, start=start, end=end),
            f"{instance}'s calendar read",
        )
    except FetchAbandoned as abandoned:
        return HarvestResult(
            events=(),
            cursor=Cursor(),
            outcome=HarvestOutcome.FAILED,
            failure=HarvestFailure(reason=str(abandoned), retryable=True),
        )


def _ask(daemon: Daemon, instance: str, *, start: datetime, end: datetime) -> HarvestResult:
    """One connector's calendar read, with a contract breach costing only that one.

    `CalendarReadPort.read_calendar` over the display day (`start` to `end`,
    `23e`) when the connector offers it, and its `harvest` otherwise — so
    Graph's listed channels are not walked on a render, and the day asked for
    is the day shown. A connector without the calendar-only read answers with
    its harvest's window, and `_on_day` selects the day from what it returns.

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
            return connector.read_calendar(start, end)
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


def _on_day(meeting: Meeting, *, start: datetime, end: datetime, day: date) -> bool:
    """Whether a meeting touches the display day at all.

    Overlap rather than "starts today", so a meeting that began yesterday
    evening and is still running at 07:00 is on the page. The renderer decides
    nothing about which day it is rendering — it renders what it is handed — so
    dropping that meeting here would be this function silently deciding the PM
    is not in it.

    An all-day marker that carries its own date is placed by that date (`23e`):
    it is on the page on every date from its start date up to, not including,
    its end date (`23h`), and on its start date alone when it has no end date
    or one not after the start. Converting
    its start to the display zone instead would, west of UTC, move a
    midnight-UTC start onto the previous day — and the request range overlaps a
    neighbouring day's all-day entry by up to the zone's offset, which this
    drops. Any other zero-length row has no span to overlap with, so it is
    placed by its start.
    """
    if meeting.all_day_date is not None:
        ends = meeting.all_day_end_date
        if ends is None or ends <= meeting.all_day_date:
            return meeting.all_day_date == day
        return meeting.all_day_date <= day < ends
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


# ── A meeting's summary, decisions and action items (story 7b) ───────────────


SUMMARY_CALL_DEADLINE_SECONDS = 300.0
"""One model call's deadline: the local adapter's own (`MODEL_CALL_DEADLINE_SECONDS`).

Spelled here, beside the pipeline, because the pipeline imports no model
client; a test holds the two equal."""

TRANSCRIPT_SUMMARIZE_BOUND_SECONDS = 2 * SUMMARY_CALL_DEADLINE_SECONDS
"""`transcript summarize`'s bound (AD-21): the call and its one retry. Over five
seconds, so the command is acknowledged as a job."""

SUMMARY_OFFER_LIMIT = 3
"""At most this many meetings are offered for a summary per harvest."""

SUMMARY_MAX_AGE = timedelta(days=30)
"""A meeting that started longer ago than this is never offered automatically:
its transcript is purged at 30 days (NFR-09)."""


class Hop(Enum):
    """Where a summary job's next stage runs (AD-19)."""

    MODEL = "model"
    """The model pool: the call, and nothing else."""
    IO = "io"
    """The I/O pool: reading, judging the answer, and handing each write over."""


class SummaryResult(Enum):
    """How one summary job ended."""

    SUMMARIZED = "summarized"
    NOTHING_SAID = "nothing said"
    WAITING = "waiting"
    """No local model, Ollama not answering, or the model not pulled: not an attempt."""
    TIMED_OUT = "timed out"
    FAILED = "failed"
    STOPPED = "stopped"
    """Its row moved past the version it read: something else is on record."""


@dataclass(frozen=True, slots=True)
class SummaryCall:
    """What one model call reported, for the measured figures (story 7b)."""

    model: str
    prompt_tokens: int
    output_tokens: int
    total_ns: int | None


@dataclass(frozen=True, slots=True)
class SummaryOutcome:
    """What `summarize_meeting` did, handed on for `7e` rather than re-read.

    `decisions` pairs each decision with its reference, `action_items` each
    item with its proposal id; `superseded` names the staged items a
    re-summary moved to `superseded`, and `left` those the PM had acted on.
    """

    scope: DataScope
    meeting_id: str
    result: SummaryResult
    reason: str
    answer: SummaryAnswer | None = None
    decisions: tuple[tuple[str, SummaryDecision], ...] = ()
    action_items: tuple[tuple[str, SummaryActionItem], ...] = ()
    superseded: tuple[str, ...] = ()
    left: tuple[str, ...] = ()
    calls: tuple[SummaryCall, ...] = ()
    prompt_characters: int = 0


@dataclass(frozen=True, slots=True)
class _Prepared:
    meeting: Meeting
    prompt: SummaryPrompt


def _prepare(daemon: Daemon, scope: DataScope, meeting_id: str) -> _Prepared:
    """The meeting's whole transcript as the call's fragments, or why not.

    `SummaryRefused` naming the reason when the meeting is not recorded, its
    transcript row is not `ingested` (a part that failed ingestion named), or
    a capture is missing; `NothingSaid` when no utterance has words left.
    Reads only: the parts and their captures through storage, handed to the
    whole-meeting reader.
    """
    try:
        meeting = daemon.meetings.get(meeting_id, scope=scope).meeting
    except MeetingNotFound:
        raise SummaryRefused(
            f"no meeting {meeting_id!r} is recorded in {scope}, so there is no "
            f"transcript to summarise. Nothing was asked of the model."
        ) from None
    except MalformedMeeting as unreadable:
        raise SummaryRefused(
            f"meeting {meeting_id}'s record in {scope} could not be read, so it is "
            f"not summarised: {unreadable} Nothing was asked of the model."
        ) from unreadable
    state = daemon.storage.transcript_states(scope).get(meeting_id)
    if state is None:
        raise SummaryRefused(
            f"meeting {meeting_id} in {scope} has no transcript ingested, so there "
            f"is nothing to summarise. Nothing was asked of the model."
        )
    if state.state == TranscriptStateKind.INGEST_FAILED.value:
        raise SummaryRefused(
            f"a part of meeting {meeting_id}'s transcript in {scope} failed "
            f"ingestion, so it is not summarised: {state.reason} Nothing was asked "
            f"of the model."
        )
    if state.state != TranscriptStateKind.INGESTED.value:
        raise SummaryRefused(
            f"meeting {meeting_id}'s transcript in {scope} is {state.state}, "
            f"not ingested, so it is not summarised. Nothing was asked of the model."
        )
    stem = capture_stem(meeting_id, meeting.start)
    parts = daemon.storage.transcript_parts(scope).get(meeting_id, ())
    try:
        captures: dict[str, str] = {}
        for name in daemon.storage.list_collection(scope=scope, artifact=CAPTURES):
            if not name.startswith(stem):
                continue
            raw = daemon.storage.read_artifact(scope=scope, artifact=CAPTURES, name=name)
            if raw is not None:
                captures[name] = raw.decode("utf-8")
        utterances = meeting_utterances(meeting_id, meeting.start, parts=parts, captures=captures)
    except CaptureMissing as missing:
        raise SummaryRefused(f"{missing} Nothing was asked of the model.") from missing
    except (UnreadableTranscript, UnicodeDecodeError) as unreadable:
        raise SummaryRefused(
            f"meeting {meeting_id}'s transcript in {scope} could not be read: "
            f"{failure_reason(unreadable)}. Nothing was asked of the model."
        ) from unreadable
    prompt = summary_prompt(
        meeting.title, utterances, name=f"meeting {meeting_id}'s transcript in {scope}"
    )
    return _Prepared(meeting, prompt)


_UNAVAILABLE: tuple[type[BaseException], ...] = (NoLocalModel, LocalModelUnavailable)
"""No local model, Ollama not answering, the model not pulled: waiting, not an attempt."""

_CALL_REFUSALS: tuple[type[BaseException], ...] = (
    NoLocalModel,
    LocalModelUnavailable,
    ModelTimedOut,
    PossiblyTruncated,
    OutputCapReached,
)


def _call(
    daemon: Daemon, scope: DataScope, prepared: _Prepared, rule: SummaryRule | None
) -> Generator[Hop, None, ModelAnswer | BaseException]:
    """One call on the model pool; then back to the I/O pool with its answer or refusal."""
    yield Hop.MODEL
    try:
        model = require_model(daemon)
        result: ModelAnswer | BaseException = model.complete(
            task_class=TaskClass.EXTRACTION,
            instructions=instructions_for(rule),
            external=prepared.prompt.fragments,
            flow=ScopeFlow.of((scope,), scope),
            shape=SUMMARY_SHAPE,
        )
    except _CALL_REFUSALS as refused:
        result = refused
    yield Hop.IO
    return result


def _judged(
    result: ModelAnswer | BaseException, labels: frozenset[str]
) -> SummaryAnswer | SummaryRule | BaseException:
    """A valid answer, the rule a refused one broke, or the call's own refusal."""
    if isinstance(result, OutputCapReached):
        return SummaryRule.OUTPUT_CAP
    if isinstance(result, BaseException):
        return result
    try:
        return validate_answer(result.text, labels=labels)
    except ModelAnswerRefused as refused:
        return refused.rule


def summary_steps(
    daemon: Daemon,
    scope: DataScope,
    meeting_id: str,
    *,
    job: str,
    fence: SummaryFence | None = None,
    offered: SummaryFence | None = None,
    prepared: _Prepared | None = None,
) -> Generator[Hop, None, SummaryOutcome]:
    """One summary job, as stages: each `yield` names the pool the next one runs on.

    Preparation runs where the job starts (an I/O worker) and ends there; the
    call and its one retry are handed to the model pool, and what follows each
    call back to the I/O pool, so no I/O worker waits on the model pool
    (AD-19). Every write is one claimed step: the lease on the state row, the
    record's block, the decision events, the proposals with their
    supersession, then the row's final state, each checked against the lease
    inside its own step (AD-37). A lost lease stops the job with nothing
    further written.

    `fence` is a request's (`request_summary` moved the row first). Otherwise
    the job reads the row and stops unless it is still `waiting` — at the
    version it was `offered` at, when a harvest offered it — so a job that
    starts after a request or another job moved the row writes nothing.

    After preparation, a fault pm-ai does not expect settles the row `failed`
    naming its type, so the model is not asked again every harvest; a busy
    store or claim, or the writer stopping, leaves the row as it was, to be
    offered again.
    """
    storage = daemon.storage
    calls: list[SummaryCall] = []

    def outcome(result: SummaryResult, reason: str, **found: Any) -> SummaryOutcome:
        return SummaryOutcome(
            scope,
            meeting_id,
            result,
            reason,
            calls=tuple(calls),
            prompt_characters=0 if prepared is None else prepared.prompt.characters,
            **found,
        )

    try:
        if fence is None:
            state = storage.summary_state(scope, meeting_id)
            if state is None:
                return outcome(SummaryResult.STOPPED, "no summary row offers this meeting")
            if state.state is not SummaryStateKind.WAITING or (
                offered is not None and state.version != offered.version
            ):
                return outcome(
                    SummaryResult.STOPPED,
                    f"meeting {meeting_id}'s summary row moved on since it was "
                    f"offered ({state.state.value}), so this job did nothing",
                )
            fence = SummaryFence(scope, meeting_id, state.version)
            requested = state.requested
        else:
            requested = True
        if prepared is None:
            try:
                prepared = _prepare(daemon, scope, meeting_id)
            except NothingSaid as silent:
                reason = str(silent)
                storage.settle_summary(fence, state=SummaryStateKind.NOTHING_SAID, reason=reason)
                return outcome(SummaryResult.NOTHING_SAID, reason)
            except SummaryRefused as refused:
                reason = str(refused)
                storage.settle_summary(fence, state=SummaryStateKind.FAILED, reason=reason)
                return outcome(SummaryResult.FAILED, reason)
        labels = prepared.prompt.labels

        first = yield from _call(daemon, scope, prepared, None)
        if isinstance(first, ModelAnswer):
            calls.append(_measured(first))
        judged = _judged(first, labels)
        if isinstance(judged, BaseException):
            return _without_answer(storage, fence, judged, prepared, outcome, retried=False)
        if isinstance(judged, SummaryRule):
            rule = judged
            second = yield from _call(daemon, scope, prepared, rule)
            if isinstance(second, ModelAnswer):
                calls.append(_measured(second))
            judged = _judged(second, labels)
            if isinstance(judged, BaseException):
                return _without_answer(storage, fence, judged, prepared, outcome, retried=True)
            if isinstance(judged, SummaryRule):
                reason = _refused_twice(scope, meeting_id, rule, judged)
                storage.settle_summary(fence, state=SummaryStateKind.FAILED, reason=reason)
                return outcome(SummaryResult.FAILED, reason)
        answer = judged
        model_name = calls[-1].model if calls else "the local model"
        return _written(
            daemon, scope, meeting_id, job, fence, prepared, answer, model_name, requested, outcome
        )
    except SummaryLeaseLost as lost:
        return outcome(SummaryResult.STOPPED, str(lost))


def _written(
    daemon: Daemon,
    scope: DataScope,
    meeting_id: str,
    job: str,
    fence: SummaryFence,
    prepared: _Prepared,
    answer: SummaryAnswer,
    model_name: str,
    requested: bool,
    outcome: Callable[..., SummaryOutcome],
) -> SummaryOutcome:
    """The writes, each its own claimed step under the lease; or the row failed."""
    storage = daemon.storage
    try:

        # In order, each complete on its own and keyed, so a re-run repeats
        # only what is missing.
        lease = storage.take_summary_lease(fence, job=job)
        now = daemon.clock()
        daemon.meetings.put_summary(
            meeting_id,
            scope=scope,
            summary=RecordedSummary(
                model=model_name,
                requested=requested,
                made_on=now.astimezone(timezone.utc).date(),
                answer=answer,
            ),
            check=lambda: storage.check_summary_lease(lease),
        )
        decisions = tuple(
            (decision_reference(meeting_id, decision.statement), decision)
            for decision in answer.decisions
        )
        storage.persist_decisions(
            tuple(
                NormalizedEvent(
                    scope=scope,
                    type=ObservedEventType.DECISION,
                    source_ref=SourceRef.parse(reference),
                    # A speaker label is not a person (AD-34).
                    actor=NO_PERSON,
                    occurred_at=prepared.meeting.start,
                    payload=DecisionPayload(statement=decision.statement),
                    # pm-ai's reading of a transcript is never evidence (AD-36).
                    authored_by=Provenance.PM_AI,
                )
                for reference, decision in decisions
            ),
            scope=scope,
            fence=lease,
        )
        items = tuple(
            (action_item_id(scope, meeting_id, item), item) for item in answer.action_items
        )
        cites = SourceRef.parse(f"meeting:{meeting_id}")
        staged = storage.stage_summary_items(
            tuple(
                Proposal(
                    proposal_id=proposal_id,
                    type=ACTION_ITEM,
                    summary=item.what[:80],
                    payload={"what": item.what, "owner": item.owner, "due": item.due},
                    target=None,  # never executed
                    cites=cites,  # AD-33 — the meeting
                    created_at=now,
                    state=ProposalState.STAGED,
                )
                for proposal_id, item in items
            ),
            prefix=action_item_prefix(scope, meeting_id),
            fence=lease,
        )
        reason = (
            f"summarised by the local model {model_name}"
            f"{' on request' if requested else ''}: {len(decisions)} decision(s), "
            f"{len(items)} action item(s) staged"
            + (f", {len(staged.superseded)} superseded" if staged.superseded else "")
            + "."
        )
        storage.settle_summary(
            lease, state=SummaryStateKind.SUMMARIZED, reason=reason, model=model_name
        )
        return outcome(
            SummaryResult.SUMMARIZED,
            reason,
            answer=answer,
            decisions=decisions,
            action_items=items,
            superseded=staged.superseded,
            left=staged.left,
        )
    except SummaryLeaseLost as lost:
        return outcome(SummaryResult.STOPPED, str(lost))
    except _LEFT_AS_IT_WAS:
        raise
    except Exception as broken:  # noqa: BLE001 — settled by its type, never re-offered
        reason = (
            f"meeting {meeting_id}'s summary could not be written: "
            f"{type(broken).__name__}, which pm-ai did not expect. It is not offered "
            f"again automatically; {_ask_again(scope, meeting_id)}"
        )
        current = storage.summary_state(scope, meeting_id)
        held = (
            SummaryFence(scope, meeting_id, current.version, current.job)
            if current is not None and current.job == job
            else fence
        )
        try:
            storage.settle_summary(held, state=SummaryStateKind.FAILED, reason=reason)
        except SummaryLeaseLost as lost:
            return outcome(SummaryResult.STOPPED, str(lost))
        return outcome(SummaryResult.FAILED, reason)


_LEFT_AS_IT_WAS: tuple[type[BaseException], ...] = (
    WriterStopped,
    ArtifactBusy,
    OperationalStoreUnavailable,
    SchemaVersionChanged,
)
"""Faults that clear by waiting, or the daemon stopping: the row is left as it
was, and a waiting row is offered again at a later harvest."""


def _ask_again(scope: DataScope, meeting_id: str) -> str:
    return f"`pm-ai transcript summarize --scope {scope} --meeting {meeting_id}` asks again."


def _refused_twice(
    scope: DataScope, meeting_id: str, first: SummaryRule, second: SummaryRule
) -> str:
    """The failure's reason, from both refusals as they were."""
    if first is second is SummaryRule.OUTPUT_CAP:
        broke = f"both answers stopped at the {_OUTPUT_CAP_WORDS}"
    elif first is second:
        broke = f"both answers were refused for {first.value}"
    else:
        broke = f"the first answer was refused for {first.value}, the second for {second.value}"
    return (
        f"the local model's answer was refused twice, so meeting {meeting_id} was "
        f"not summarised and is not offered again automatically: {broke}. "
        f"{_ask_again(scope, meeting_id)}"
    )


_OUTPUT_CAP_WORDS = "local model's output cap"


def _measured(answer: ModelAnswer) -> SummaryCall:
    return SummaryCall(answer.model, answer.prompt_tokens, answer.output_tokens, answer.total_ns)


def _without_answer(
    storage: StorageService,
    fence: SummaryFence,
    refused: BaseException,
    prepared: _Prepared,
    outcome: Callable[..., SummaryOutcome],
    *,
    retried: bool,
) -> SummaryOutcome:
    """The row's state when a call gave no answer to judge."""
    meeting_id = fence.meeting_id
    if isinstance(refused, _UNAVAILABLE):
        reason = (
            f"the local model could not be asked{' for the retry' if retried else ''}, "
            f"so meeting {meeting_id} waits for the next harvest"
            + (" and the first answer was discarded" if retried else "")
            + f": {refused}"
        )
        storage.settle_summary(fence, state=SummaryStateKind.WAITING, reason=reason)
        return outcome(SummaryResult.WAITING, reason)
    if isinstance(refused, ModelTimedOut):
        state = storage.record_summary_timeout(
            fence,
            reason=(
                f"a call to the local model about meeting {meeting_id} outlasted its "
                f"{SUMMARY_CALL_DEADLINE_SECONDS:g}-second deadline"
            ),
        )
        result = (
            SummaryResult.FAILED if state.state is SummaryStateKind.FAILED else SummaryResult.TIMED_OUT
        )
        return outcome(result, state.reason)
    if isinstance(refused, PossiblyTruncated):
        reason = (
            f"meeting {meeting_id}'s transcript does not fit the local model's "
            f"window in one call, so it was not summarised and is not offered again "
            f"automatically; the prompt is {prepared.prompt.characters} characters of "
            f"transcript besides the instructions: {refused} A frontier summary "
            f"(story 7c) is the way to summarise it."
        )
        storage.settle_summary(fence, state=SummaryStateKind.FAILED, reason=reason)
        return outcome(SummaryResult.FAILED, reason)
    raise refused


class SummaryJobs:
    """The daemon's summary jobs: one in flight per (scope, meeting), and where each stage runs.

    `io` and `model` hand a stage to the I/O pool and the model pool without
    waiting for it; by default each runs where it is, which is how a test (and
    nothing else outside the daemon) runs a whole job inline. `daemon_for`
    builds the daemon one job's stages use — in the daemon, read afresh with its
    own door to the writer (`handed_daemon`); `model_stopping` says the model
    pool's shutdown has begun, after which no answer is handed on.
    `model_first` hands a requested job's call to the model pool ahead of the
    automatic ones queued there (Andrei, 2026-10-20), so a request's bound
    covers only its own call and retry; by default it is `model`.
    """

    def __init__(
        self,
        *,
        io: Callable[[Callable[[], None]], object] | None = None,
        model: Callable[[Callable[[], None]], object] | None = None,
        model_first: Callable[[Callable[[], None]], object] | None = None,
        daemon_for: Callable[[], Daemon] | None = None,
        model_stopping: Callable[[], bool] = lambda: False,
    ) -> None:
        self._io = io or _here
        self._model = model or _here
        self._model_first = model_first or self._model
        self._daemon_for = daemon_for
        self._model_stopping = model_stopping
        self._lock = threading.Lock()
        self._in_flight: dict[tuple[DataScope, str], str] = {}

    def in_flight(self, scope: DataScope, meeting_id: str) -> bool:
        with self._lock:
            return (scope, meeting_id) in self._in_flight

    def start(
        self,
        daemon: Daemon,
        scope: DataScope,
        meeting_id: str,
        *,
        job: str | None = None,
        fence: SummaryFence | None = None,
        offered: SummaryFence | None = None,
        prepared: _Prepared | None = None,
        on_done: Callable[[SummaryOutcome | BaseException], None] | None = None,
    ) -> bool:
        """Start one job; `False` when an automatic one is already in flight.

        A request (`fence` given, from `request_summary`) always starts: it
        has already moved the row to a new version, so a job in flight for
        the same meeting stops before its next write. An automatic job is
        given the version it was `offered` at, and stops unless the row is
        still there.
        """
        key = (scope, meeting_id)
        name = job or f"summary_{secrets.token_hex(6)}"
        # A request runs on its own job's daemon; an automatic job gets one of
        # its own, never the harvest's that offered it. Built before the
        # meeting is marked in flight, so a build that raises leaves no mark.
        own = daemon if self._daemon_for is None or job is not None else self._daemon_for()
        with self._lock:
            if fence is None and key in self._in_flight:
                return False
            self._in_flight[key] = name

        def done(result: SummaryOutcome | BaseException) -> None:
            with self._lock:
                if self._in_flight.get(key) == name:
                    del self._in_flight[key]
            if isinstance(result, BaseException):
                _log.warning(
                    "the summary of meeting %s in %s stopped on %s; its row was left "
                    "as it was, and a waiting row is offered again at a later harvest",
                    meeting_id,
                    scope,
                    type(result).__name__,
                )
            if on_done is not None:
                on_done(result)

        try:
            steps = summary_steps(
                own, scope, meeting_id, job=name, fence=fence, offered=offered, prepared=prepared
            )
        except BaseException:
            done(_stopping())
            raise
        self._drive(steps, done, requested=fence is not None)
        return True

    def _drive(
        self,
        steps: Generator[Hop, None, SummaryOutcome],
        done: Callable[[SummaryOutcome | BaseException], None],
        *,
        requested: bool = False,
    ) -> None:
        """Run `steps`, handing each stage to the pool it names, waiting on none.

        A requested job's model stages go ahead of the automatic ones queued.
        """
        model = self._model_first if requested else self._model

        def stop() -> None:
            steps.close()
            done(_stopping())

        def resume() -> None:
            try:
                hop = next(steps)
            except StopIteration as stopped:
                done(stopped.value)
                return
            except BaseException as raised:  # noqa: BLE001 — named in the log, never lost
                done(raised)
                return
            if hop is Hop.IO and self._model_stopping():
                stop()
                return
            try:
                (model if hop is Hop.MODEL else self._io)(resume)
            except Exception:  # noqa: BLE001 — a pool that stopped taking work
                stop()

        try:
            self._io(resume)
        except Exception:  # noqa: BLE001 — a pool that stopped taking work
            stop()


def _here(stage: Callable[[], None]) -> None:
    stage()


def _stopping() -> WriterStopped:
    return WriterStopped(
        "a summary job's next stage could not be handed to its pool (the daemon "
        "is stopping), so nothing more was written; a waiting row is offered "
        "again at a later harvest."
    )


def summarize_meeting(
    daemon: Daemon,
    scope: DataScope,
    meeting_id: str,
    *,
    job: str = "summary",
    fence: SummaryFence | None = None,
) -> SummaryOutcome:
    """One meeting's summary, run inline, start to end; the validated answer handed on.

    The stages `summary_steps` names, run where this is called — for a test,
    or a caller already on the right thread. The daemon runs the same stages
    through `SummaryJobs`, each on its own pool.
    """
    steps = summary_steps(daemon, scope, meeting_id, job=job, fence=fence)
    try:
        while True:
            next(steps)
    except StopIteration as stopped:
        return stopped.value


def offer_summaries(daemon: Daemon) -> tuple[str, ...]:
    """After a harvest: start up to `SUMMARY_OFFER_LIMIT` waiting summaries (story 7b).

    Only in the daemon (`daemon.summaries`): elsewhere nothing is offered and a
    waiting row waits; and only with a local model named — with `local_model`
    unset nothing is started and nothing read (a model named but not
    answering goes through the job and waits). The most recent meetings
    first; none older than `SUMMARY_MAX_AGE`; only a meeting whose transcript
    row is `ingested`; one job in flight per meeting; each job given the
    version it was offered at. Returns a sentence for each meeting skipped or
    whose job could not start, for the harvest's refusals; never raises.
    """
    jobs = daemon.summaries
    if jobs is None or daemon.model is None:
        return ()
    try:
        offered = daemon.storage.summaries_to_offer(since=daemon.clock() - SUMMARY_MAX_AGE)
    except Exception as unreadable:  # noqa: BLE001 — named; the harvest is saved
        return (
            f"the summaries waiting could not be read ({type(unreadable).__name__}), "
            f"so none was offered this harvest.",
        )
    started = 0
    said: list[str] = []
    for state in offered:
        if started >= SUMMARY_OFFER_LIMIT:
            break
        if state.scope.kind is ScopeKind.PROJECT and state.scope.project_id not in daemon.watched:
            said.append(
                f"meeting {state.meeting_id}'s summary waits in {state.scope}, which this "
                f"daemon does not watch, so it was not offered. {RESTART_SENTENCE}"
            )
            continue
        try:
            if jobs.start(
                daemon,
                state.scope,
                state.meeting_id,
                offered=SummaryFence(state.scope, state.meeting_id, state.version),
            ):
                started += 1
        except Exception as unstarted:  # noqa: BLE001 — one meeting, not the harvest
            said.append(
                f"the summary of meeting {state.meeting_id} in {state.scope} could not "
                f"be started ({type(unstarted).__name__}); it waits for the next harvest."
            )
    return tuple(said)


@dataclass(frozen=True, slots=True)
class SummaryRequested:
    """`transcript summarize`'s acknowledgement: the job started, in the daemon."""

    scope: DataScope
    meeting_id: str
    title: str

    @property
    def line(self) -> str:
        return (
            f"the summary of meeting {self.meeting_id} ({self.title!r}) in "
            f"{self.scope} was asked of the local model; any summary running for "
            f"it was stopped before its next write."
        )


def request_summary(
    daemon: Daemon,
    *,
    scope: DataScope,
    meeting_id: str,
    job: str,
    by: str = SummaryMaker.LOCAL.value,
    on_done: Callable[[SummaryOutcome | BaseException], None] | None = None,
) -> SummaryRequested:
    """`transcript summarize`: checked, then started in the daemon (story 7b).

    Refused by name before any model call: `--by claude` (`7c`), no daemon to
    run it, a meeting not recorded, a transcript not ingested (a failed part
    named), a capture missing. Then the row moves to a new version — revoking
    any job running for it — and the job starts; this returns at once, and
    `on_done` is told how it ended.
    """
    if by != SummaryMaker.LOCAL.value:
        raise SummaryRefused(FRONTIER_SUMMARY_REFUSED)
    jobs = daemon.summaries
    if jobs is None:
        raise SummaryRefused(
            "only the daemon summarises a transcript, and this process is not the "
            "daemon; start it with `pm-ai daemon run` and ask again."
        )
    prepared: _Prepared | None
    try:
        prepared = _prepare(daemon, scope, meeting_id)
        meeting = prepared.meeting
    except NothingSaid:
        # Settled by the job as "nothing said", with no call.
        prepared = None
        meeting = daemon.meetings.get(meeting_id, scope=scope).meeting
    fence = daemon.storage.request_summary(
        scope,
        meeting_id,
        meeting_start=meeting.start,
        reason="asked for with `pm-ai transcript summarize`",
    )
    jobs.start(daemon, scope, meeting_id, job=job, fence=fence, prepared=prepared, on_done=on_done)
    return SummaryRequested(scope, meeting_id, meeting.title)


def summary_closing(outcome: SummaryOutcome | BaseException) -> tuple[str, int]:
    """How a requested summary ended, as the job's last line and exit code."""
    if isinstance(outcome, BaseException):
        return (
            f"pm-ai: the summary stopped on {type(outcome).__name__}; its row was left "
            f"as it was, and a waiting row is offered again at a later harvest.\n",
            EXIT_UNEXPECTED,
        )
    if outcome.result is SummaryResult.SUMMARIZED:
        return (
            f"meeting {outcome.meeting_id} in {outcome.scope}: {outcome.reason} The "
            f"record's ## Summary holds it.\n",
            EXIT_OK,
        )
    if outcome.result is SummaryResult.NOTHING_SAID:
        return f"meeting {outcome.meeting_id} in {outcome.scope}: {outcome.reason}\n", EXIT_OK
    return f"pm-ai: {outcome.reason}\n", EXIT_REFUSAL


def note_summary_input(
    daemon: Daemon, scope: DataScope, meeting_id: str, *, ingested_now: bool
) -> tuple[str, ...]:
    """After a meeting's transcript is ingested: its summary row offered (story 7b).

    A meeting with no row gets one, `waiting`. One whose automatic summary was
    made, failed or found nothing said is offered again over the whole meeting
    when this ingestion ingested a later part (`ingested_now`) — unless an
    earlier part's capture is gone (purged), when nothing is re-opened. One the
    PM asked for is kept. Sentences for the run's refusals; never raises.
    """
    try:
        meeting = daemon.meetings.get(meeting_id, scope=scope).meeting
        reopen = ingested_now and _stored_parts_on_disk(daemon, scope, meeting)
        daemon.storage.offer_summary(
            scope,
            meeting_id,
            meeting_start=meeting.start,
            reason=(
                "a later part of the transcript was ingested, so the summary is "
                "made again over the whole meeting"
                if reopen
                else "the transcript was ingested; the daemon summarises it at its next harvest"
            ),
            reopen=reopen,
        )
    except Exception as unrecorded:  # noqa: BLE001 — named; the ingestion stands
        return (
            f"meeting {meeting_id} in {scope} was ingested but its summary could not "
            f"be offered ({type(unrecorded).__name__}); the next ingestion offers it.",
        )
    return ()


def _stored_parts_on_disk(daemon: Daemon, scope: DataScope, meeting: Meeting) -> bool:
    """Whether every stored Graph part's capture of the meeting is still on disk.

    Only the `transcript_parts` rows are checked: a meeting with none (a single
    `-graph.vtt`, or a dropped capture) answers `True`, since a re-open comes
    only from a later part, which has a row.
    """
    rows = daemon.storage.transcript_parts(scope).get(meeting.meeting_id, ())
    names = set(daemon.storage.list_collection(scope=scope, artifact=CAPTURES))
    return all(
        capture_name(meeting.meeting_id, meeting.start, CaptureFormat.GRAPH, part=row.part) in names
        for row in rows
        if row.state is PartState.STORED
    )


def _summary_made_again(daemon: Daemon, meeting: Meeting, kept_id: str) -> tuple[str, ...]:
    """The summary row set back to offered after a record's region was dropped."""
    try:
        daemon.storage.reset_summary(
            meeting.scope,
            kept_id,
            meeting_start=meeting.start,
            reason=(
                "the record's ## Summary region held something not in its grammar "
                "and was dropped, so the summary is made again"
            ),
        )
    except Exception as unrecorded:  # noqa: BLE001 — named; the record is written
        return (
            f"meeting {kept_id} in {meeting.scope}: its summary could not be offered "
            f"again ({type(unrecorded).__name__}); `pm-ai transcript summarize` asks for it.",
        )
    return ()
