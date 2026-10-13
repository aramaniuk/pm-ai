"""Transcript acquisition: which meetings to ask about, and what to keep (story 33e).

The caller of `TranscriptSourcePort`, and I/O-free: every byte moves through
`StoragePort` (AD-5), and the source is whatever adapter the composition root
handed in (AD-30). Runs after a calendar harvest has saved its cursor and
coverage, so nothing here can lose a harvest's progress.

## Which meetings are tried

Every meeting **recorded** in a scope whose start lies within the harvest's
reach-back and which has ended — not only the meetings this run's harvest
returned, so a fault or an outage never makes a meeting unfetchable before it
leaves the reach-back. Of those, a meeting is skipped when:

- its record holds no calendar reference (dropped by hand — nothing to resolve);
- it already has a capture: any member of `transcripts/` whose name starts with
  the record's stem (`meeting_records.has_capture`), from any source, so a Graph
  capture and a hand-dropped one cannot both exist unseen;
- its recorded state closes it: fetched, or refused and not retryable;
- it was refused retryably with a provider hint (`retry_after`) that has not
  run out yet — asked again on the first run after it does.

A Graph capture found on disk for a meeting with no fetched row — written, and
then the state write failed or the process died — is recorded fetched when
selection meets it, and is handed to ingestion either way: what ingestion is
offered comes from the captures on disk, not from the rows alone.

## Where a meeting got to

One row per meeting in the operational store (Tier 2), never on the record
(AD-33): fetched; refused with its reason and whether waiting helps; absent;
skipped. A meeting the source did not reach — a provider fault stopped the run
first — gets no row and waits for the next run.

The capture is written verbatim (AD-29) through the single writer's capture
path, which asks git about the final directory, stages and links (AD-43,
AD-47), into the scope that owns the meeting (`Meeting.transcript_home`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from pm_ai.core.meeting_records import (
    MEETINGS,
    MalformedMeeting,
    MeetingRecords,
    capture_name,
    capture_stem,
    has_capture,
    parse_record,
)
from pm_ai.domain.identity import DataScope
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.scope_model import ScopeResolutionError
from pm_ai.domain.storage_tiers import CAPTURES, UnprotectedCaptureDir
from pm_ai.domain.transcripts import (
    ACQUISITION_STATES,
    CaptureFormat,
    TranscriptAbsent,
    TranscriptCaptured,
    TranscriptOutcome,
    TranscriptRefused,
    TranscriptReport,
    TranscriptReportEntry,
    TranscriptSkipped,
    TranscriptStateKind,
)
from pm_ai.ports import (
    CaptureAlreadyExists,
    EmptyCapture,
    MalformedCaptureName,
    StoragePort,
    TranscriptSourcePort,
    TranscriptSourceUnavailable,
)

__all__ = ["SelectedMeeting", "acquire_transcripts", "select_meetings"]


@dataclass(frozen=True, slots=True)
class SelectedMeeting:
    scope: DataScope
    meeting: Meeting


def select_meetings(
    storage: StoragePort,
    *,
    scopes: Sequence[DataScope],
    since: datetime,
    now: datetime,
) -> tuple[tuple[SelectedMeeting, ...], tuple[str, ...]]:
    """The meetings acquisition should ask about, and the scopes it could not read.

    A scope whose records cannot be read (a malformed record inside the
    reach-back, a project the registry no longer knows) is named and skipped;
    the other scopes are still read.
    """
    records = MeetingRecords(storage)
    chosen: list[SelectedMeeting] = []
    refusals: list[str] = []
    seen: set[str] = set()
    for scope in scopes:
        try:
            picked = _select_in(storage, records, scope, since=since, now=now)
        except Exception as unreadable:  # noqa: BLE001 — one scope, not the run
            refusals.append(
                f"the meetings in {scope} could not be read, so none of them was "
                f"asked about: {_named(unreadable)}"
            )
            continue
        for meeting in picked:
            if meeting.meeting_id in seen:
                # The port answers by meeting id, so one id in two scopes would
                # make the answer ambiguous. Not reachable from a calendar —
                # an event id is filed in one scope — and named rather than guessed.
                refusals.append(
                    f"meeting {meeting.meeting_id} is recorded in more than one "
                    f"scope; it was asked about for the first only."
                )
                continue
            seen.add(meeting.meeting_id)
            chosen.append(SelectedMeeting(scope, meeting))
    return tuple(chosen), tuple(refusals)


def _select_in(
    storage: StoragePort,
    records: MeetingRecords,
    scope: DataScope,
    *,
    since: datetime,
    now: datetime,
) -> list[Meeting]:
    """One scope's selection; anything it raises costs this scope alone."""
    held = records.between(since, now, scope=scope)
    names = storage.list_collection(scope=scope, artifact=CAPTURES)
    states = storage.transcript_states(scope)
    picked: list[Meeting] = []
    for record in held:
        meeting = record.meeting
        if not meeting.calendar_event_ref:
            continue
        if meeting.start + timedelta(minutes=meeting.duration_minutes) > now:
            continue
        state = states.get(meeting.meeting_id)
        if has_capture(names, capture_stem(meeting.meeting_id, meeting.start)):
            graph = capture_name(meeting.meeting_id, meeting.start, CaptureFormat.GRAPH)
            if graph in names and (state is None or (
                state.state in _ACQUISITION_STATES
                and state.state != TranscriptStateKind.FETCHED.value
            )):
                # Written, and then the state write failed or the process
                # died: the capture is on disk with no fetched row. Only an
                # acquisition state is repaired: an `ingested` or
                # `ingest_failed` row is ingestion's, and overwriting a failed
                # one with `fetched` would sidestep its attempt cap (11b).
                storage.record_transcript_state(
                    scope,
                    meeting.meeting_id,
                    TranscriptStateKind.FETCHED,
                    f"{graph} was found on disk with no fetched row, and is "
                    f"recorded as fetched.",
                    retryable=False,
                )
            continue
        if state is not None and state.closes:
            continue
        if state is not None and state.waiting(now):
            continue
        picked.append(meeting)
    return picked


def acquire_transcripts(
    source: TranscriptSourcePort,
    storage: StoragePort,
    *,
    scopes: Sequence[DataScope],
    since: datetime,
    now: datetime,
) -> TranscriptReport:
    """Ask `source` about every meeting `select_meetings` chooses, and keep the answers.

    `since` is the start of the harvest's reach-back and `now` the instant a
    meeting must have ended by. Returns what happened to each meeting, which
    meetings a provider fault left for the next run, and every meeting whose
    capture is fetched and not yet ingested.
    """
    selected, refusals = select_meetings(storage, scopes=scopes, since=since, now=now)
    run_refusals = list(refusals)
    entries: list[TranscriptReportEntry] = []
    reached: set[str] = set()
    if selected:
        by_id = {chosen.meeting.meeting_id: chosen for chosen in selected}
        try:
            outcomes = source.acquire(
                tuple(chosen.meeting for chosen in selected), since=since
            )
        except TranscriptSourceUnavailable as unavailable:
            run_refusals.append(str(unavailable))
            outcomes = ()
        for outcome in outcomes:
            chosen = by_id.get(outcome.meeting_id)
            if chosen is None or outcome.meeting_id in reached:
                run_refusals.append(
                    f"the transcript source answered for meeting "
                    f"{outcome.meeting_id!r}, which it was not asked about or "
                    f"had already answered for; the answer was ignored."
                )
                continue
            reached.add(outcome.meeting_id)
            entry = _settled(storage, chosen, outcome)
            storage.record_transcript_state(
                chosen.scope,
                entry.meeting_id,
                entry.state,
                entry.reason,
                retryable=entry.retryable,
                retry_after=entry.retry_after,
            )
            entries.append(entry)
    deferred = tuple(
        (chosen.scope, chosen.meeting.meeting_id)
        for chosen in selected
        if chosen.meeting.meeting_id not in reached
    )
    awaiting, unlisted = _awaiting_ingestion(storage, scopes)
    return TranscriptReport(
        entries=tuple(entries),
        deferred=deferred,
        refusals=(*run_refusals, *unlisted),
        awaiting_ingestion=awaiting,
    )


def _settled(
    storage: StoragePort, chosen: SelectedMeeting, outcome: TranscriptOutcome
) -> TranscriptReportEntry:
    """One outcome as the state it leaves, writing the capture when there is one."""
    meeting = chosen.meeting
    if isinstance(outcome, TranscriptCaptured):
        return _stored(storage, chosen, outcome)
    if isinstance(outcome, TranscriptAbsent):
        return TranscriptReportEntry(
            chosen.scope, meeting.meeting_id, TranscriptStateKind.ABSENT, outcome.reason
        )
    if isinstance(outcome, TranscriptSkipped):
        return TranscriptReportEntry(
            chosen.scope, meeting.meeting_id, TranscriptStateKind.SKIPPED, outcome.reason
        )
    if isinstance(outcome, TranscriptRefused):
        return TranscriptReportEntry(
            chosen.scope,
            meeting.meeting_id,
            TranscriptStateKind.REFUSED,
            outcome.reason,
            retryable=outcome.retryable,
            retry_after=outcome.retry_after,
        )
    # A bug in a source, and a bug must not close a meeting for good: named,
    # and retryable so the meeting is asked again once the source is fixed.
    return TranscriptReportEntry(
        chosen.scope,
        meeting.meeting_id,
        TranscriptStateKind.REFUSED,
        f"the transcript source answered with {type(outcome).__name__}, which is "
        f"not an outcome the port declares; asked again next run.",
        retryable=True,
    )


def _stored(
    storage: StoragePort, chosen: SelectedMeeting, outcome: TranscriptCaptured
) -> TranscriptReportEntry:
    meeting = chosen.meeting
    capture = outcome.capture

    def refused(reason: str, *, retryable: bool) -> TranscriptReportEntry:
        return TranscriptReportEntry(
            chosen.scope,
            meeting.meeting_id,
            TranscriptStateKind.REFUSED,
            reason,
            retryable=retryable,
        )

    if capture.meeting_id != meeting.meeting_id:
        return refused(
            f"the source returned a capture for meeting {capture.meeting_id!r} "
            f"as this meeting's, so it was not stored.",
            retryable=False,
        )
    name = capture_name(meeting.meeting_id, meeting.start, capture.format)
    try:
        storage.write_capture(capture.text, scope=meeting.transcript_home, name=name)
    except CaptureAlreadyExists:
        return refused(
            f"{name} appeared while transcript {capture.transcript_id} was being "
            f"fetched; a capture is never overwritten, so this one was not stored.",
            retryable=False,
        )
    except UnprotectedCaptureDir as unprotected:
        return refused(
            f"transcript {capture.transcript_id} was not stored: {unprotected}",
            retryable=True,
        )
    except EmptyCapture:
        return refused(
            f"transcript {capture.transcript_id} had no content to store; tried "
            f"again next run.",
            retryable=True,
        )
    except (MalformedCaptureName, ScopeResolutionError) as unwritable:
        return refused(
            f"transcript {capture.transcript_id} could not be stored as {name}: "
            f"{unwritable}",
            retryable=False,
        )
    except Exception as failed:  # noqa: BLE001 — one meeting, not the run
        # A disk that is full, a permission refused, or anything else the
        # writer raised that is not one of its declared refusals: nothing was
        # stored, and nothing says waiting will not clear it.
        return refused(
            f"transcript {capture.transcript_id} could not be stored as {name}: "
            f"writing it raised {type(failed).__name__}; tried again next run.",
            retryable=True,
        )
    attribution = "" if capture.attributed else " without speaker names (the tenant forbids them)"
    return TranscriptReportEntry(
        chosen.scope,
        meeting.meeting_id,
        TranscriptStateKind.FETCHED,
        f"transcript {capture.transcript_id} stored as {name}{attribution}.",
        capture_name=name,
        attributed=capture.attributed,
    )


def _awaiting_ingestion(
    storage: StoragePort, scopes: Sequence[DataScope]
) -> tuple[tuple[tuple[DataScope, str], ...], tuple[str, ...]]:
    """Every Graph capture on disk not yet ingested — `11b`'s input.

    Read from the captures, not the rows: a capture whose fetched row was never
    written is still handed on. A capture's record is found by name — the
    capture is the record's stem plus `-graph.vtt` — so this needs no reach-back.
    A capture with an `ingested` row is never offered again; one whose
    ingestion failed is offered until it has failed `MAX_INGEST_ATTEMPTS` times
    (`StoragePort.fetched_not_ingested`, story 11b). Listed by every
    acquisition and by `9a`'s cycle, so a daemon killed between capture and
    ingestion is repaired on the next run. A scope that cannot be read is named
    and the others are still listed.
    """
    awaiting: list[tuple[DataScope, str]] = []
    refusals: list[str] = []
    tail = f"-{CaptureFormat.GRAPH.value}"
    for scope in scopes:
        try:
            states = storage.transcript_states(scope)
            pending = set(storage.fetched_not_ingested(scope))
            members = set(storage.list_collection(scope=scope, artifact=MEETINGS))
            found: list[tuple[DataScope, str]] = []
            for name in storage.list_collection(scope=scope, artifact=CAPTURES):
                if not name.endswith(tail):
                    continue
                member = name[: -len(tail)] + ".md"
                if member not in members:
                    continue
                raw = storage.read_artifact(scope=scope, artifact=MEETINGS, name=member)
                if raw is None:
                    continue
                meeting_id = parse_record(raw.decode("utf-8"), source=member).meeting.meeting_id
                if meeting_id not in states or meeting_id in pending:
                    found.append((scope, meeting_id))
        except Exception as unreadable:  # noqa: BLE001 — one scope, not the run
            refusals.append(
                f"the captures in {scope} could not be listed for ingestion: "
                f"{_named(unreadable)}"
            )
            continue
        awaiting.extend(found)
    return tuple(awaiting), tuple(refusals)


_ACQUISITION_STATES = ACQUISITION_STATES
"""The states this slice writes. Any other is an ingestion state (`11b`)."""


def _named(error: Exception) -> str:
    """A refusal's own sentence for the ones pm-ai composes; the type otherwise."""
    if isinstance(error, (MalformedMeeting, ScopeResolutionError)):
        return str(error)
    return f"reading them raised {type(error).__name__}."
