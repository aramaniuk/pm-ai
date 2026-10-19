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

## The parts of a transcript (story 33g)

A meeting whose transcription was stopped and started again has several
parts. Each is stored as its own capture, numbered in time order — part 1
`<stem>-graph.vtt`, part *n* ≥ 2 `<stem>-graph-<n>.vtt` — and recorded in the
operational store's `transcript_parts` table. Each part is one claimed step,
safe to kill: its row is written `pending`, then its capture, then the row is
marked `stored`. Selection repairs what a kill left: a `pending` row whose
capture is on disk is marked `stored`; one with no capture is fetched again
under the same number.

A meeting holding Graph parts and no hand-dropped capture is asked again
while it has a pending part, or until a run that asked about it started after
its placement window ended (start to end plus `PLACEMENT_SLACK`), read from
`transcript_part_asks`. A meeting with a `-graph.vtt` and no part rows is a
capture written before parts existed: part 1 of a closed meeting.

The source is told each meeting's stored parts and returns only new ones. A
meeting with no stored part gets each answer as its row, as before; once it
holds a part, only a new stored part changes its row, and a refusal about its
new transcripts is listed in the run's refusals.

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

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from pm_ai.core.meeting_records import (
    MEETINGS,
    MalformedMeeting,
    MeetingRecords,
    capture_name,
    capture_stem,
    has_capture,
    is_graph_capture,
    parse_record,
)
from pm_ai.domain.identity import DataScope
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.scope_model import ScopeResolutionError
from pm_ai.domain.storage_tiers import CAPTURES, UnprotectedCaptureDir
from pm_ai.domain.transcripts import (
    ACQUISITION_STATES,
    PLACEMENT_SLACK,
    CaptureFormat,
    PartState,
    StoredPart,
    TranscriptAbsent,
    TranscriptCaptured,
    TranscriptOutcome,
    TranscriptPart,
    TranscriptRefused,
    TranscriptReport,
    TranscriptReportEntry,
    TranscriptSkipped,
    TranscriptState,
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
    parts: tuple[TranscriptPart, ...] = ()
    """The meeting's part rows, after selection's repair, in part order (33g)."""

    @property
    def stored(self) -> tuple[TranscriptPart, ...]:
        return tuple(part for part in self.parts if part.state is PartState.STORED)


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
            picked, unrepaired = _select_in(storage, records, scope, since=since, now=now)
        except Exception as unreadable:  # noqa: BLE001 — one scope, not the run
            refusals.append(
                f"the meetings in {scope} could not be read, so none of them was "
                f"asked about: {_named(unreadable)}"
            )
            continue
        refusals.extend(unrepaired)
        for picked_one in picked:
            meeting = picked_one.meeting
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
            chosen.append(picked_one)
    return tuple(chosen), tuple(refusals)


def _select_in(
    storage: StoragePort,
    records: MeetingRecords,
    scope: DataScope,
    *,
    since: datetime,
    now: datetime,
) -> tuple[list[SelectedMeeting], list[str]]:
    """One scope's selection, and the meetings whose repair could not be saved.

    A meeting whose part repair raised is named and not asked about this run;
    the rest of the scope is still selected. Anything else it raises costs
    this scope alone.
    """
    held = records.between(since, now, scope=scope)
    names = storage.list_collection(scope=scope, artifact=CAPTURES)
    states = storage.transcript_states(scope)
    parts = storage.transcript_parts(scope)
    asks = storage.transcript_part_asks(scope)
    picked: list[SelectedMeeting] = []
    unrepaired: list[str] = []
    for record in held:
        meeting = record.meeting
        if not meeting.calendar_event_ref:
            continue
        end = meeting.start + timedelta(minutes=meeting.duration_minutes)
        if end > now:
            continue
        state = states.get(meeting.meeting_id)
        stem = capture_stem(meeting.meeting_id, meeting.start)
        rows = parts.get(meeting.meeting_id, ())
        if rows:
            try:
                rows = _repaired(storage, scope, meeting, rows, names, state)
            except Exception as failed:  # noqa: BLE001 — one meeting, not the scope
                unrepaired.append(
                    f"meeting {meeting.meeting_id} in {scope} was not asked about: "
                    f"repairing its pending transcript part(s) raised "
                    f"{type(failed).__name__}; tried again next run."
                )
                continue
            if any(name.startswith(stem) and not is_graph_capture(name, stem) for name in names):
                # A hand-dropped capture: not asked, as `33e`.
                continue
            if not any(row.state is PartState.PENDING for row in rows):
                asked = asks.get(meeting.meeting_id)
                if asked is not None and asked > end + PLACEMENT_SLACK:
                    # Asked by a run that started after the placement window
                    # ended: nothing created later can fit this meeting.
                    continue
            picked.append(SelectedMeeting(scope, meeting, rows))
            continue
        if has_capture(names, stem):
            graph = capture_name(meeting.meeting_id, meeting.start, CaptureFormat.GRAPH)
            if graph in names and _needs_fetched_row(state):
                # Written before parts existed (`33e`), and then the state
                # write failed or the process died: the capture is on disk
                # with no fetched row. Only an acquisition state is repaired:
                # an `ingested` or `ingest_failed` row is ingestion's, and
                # overwriting a failed one with `fetched` would sidestep its
                # attempt cap (11b). Part 1 of a closed meeting, never asked
                # again (33g).
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
        picked.append(SelectedMeeting(scope, meeting))
    return picked, unrepaired


def _needs_fetched_row(state: TranscriptState | None) -> bool:
    return state is None or (
        state.state in _ACQUISITION_STATES and state.state != TranscriptStateKind.FETCHED.value
    )


def _repaired(
    storage: StoragePort,
    scope: DataScope,
    meeting: Meeting,
    rows: tuple[TranscriptPart, ...],
    names: Sequence[str],
    state: TranscriptState | None,
) -> tuple[TranscriptPart, ...]:
    """A kill's leftovers settled: a pending part whose capture is on disk is stored.

    A pending part with no capture is left pending and fetched again under the
    same number. When a repair stores a part the meeting's row is recorded
    `fetched` unless it already is — as when `_stored` stores a new part — so
    the part awaits ingestion under a row that says so; `mark_part_stored`
    has already reset the ingestion attempts.
    """
    present = set(names)
    out: list[TranscriptPart] = []
    repaired: list[str] = []
    for row in rows:
        name = capture_name(meeting.meeting_id, meeting.start, CaptureFormat.GRAPH, part=row.part)
        if row.state is PartState.PENDING and name in present:
            storage.mark_part_stored(scope, meeting.meeting_id, row.part)
            row = replace(row, state=PartState.STORED, reason=None)
            repaired.append(name)
        out.append(row)
    if repaired and (state is None or state.state != TranscriptStateKind.FETCHED.value):
        storage.record_transcript_state(
            scope,
            meeting.meeting_id,
            TranscriptStateKind.FETCHED,
            f"{', '.join(repaired)} was found on disk with its part still pending, "
            f"and is recorded as stored.",
            retryable=False,
        )
    return tuple(out)


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
        stored: dict[str, tuple[StoredPart, ...]] = {
            chosen.meeting.meeting_id: tuple(part.stored for part in chosen.stored)
            for chosen in selected
            if chosen.stored
        }
        try:
            outcomes = source.acquire(
                tuple(chosen.meeting for chosen in selected), since=since, stored=stored
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
            settled = _settled(storage, chosen, outcome)
            if settled.entry is not None:
                entry = settled.entry
                storage.record_transcript_state(
                    chosen.scope,
                    entry.meeting_id,
                    entry.state,
                    entry.reason,
                    retryable=entry.retryable,
                    retry_after=entry.retry_after,
                )
                entries.append(entry)
            run_refusals.extend(settled.refusals)
            if settled.holds_parts and _ends_asking(outcome):
                try:
                    storage.record_part_ask(chosen.scope, outcome.meeting_id, asked_at=now)
                except Exception as unrecorded:  # noqa: BLE001 — one meeting, not the run
                    run_refusals.append(
                        f"meeting {outcome.meeting_id} in {chosen.scope}: when it was "
                        f"asked about could not be recorded "
                        f"({type(unrecorded).__name__}), so it is asked again next run."
                    )
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


@dataclass(frozen=True, slots=True)
class _Settled:
    """What one answer leaves: the row to write (none when a meeting holding
    parts keeps its own), run-level refusals, and whether the meeting now holds
    a stored part."""

    entry: TranscriptReportEntry | None
    refusals: tuple[str, ...] = ()
    holds_parts: bool = False


def _ends_asking(outcome: TranscriptOutcome) -> bool:
    """Whether this answer counts as the meeting having been asked (story 33g).

    A capture, an absence or a refusal waiting cannot clear; never a skip (a
    part still being produced) or a refusal that waiting may clear.
    """
    if isinstance(outcome, (TranscriptCaptured, TranscriptAbsent)):
        return True
    return isinstance(outcome, TranscriptRefused) and not outcome.retryable


def _settled(
    storage: StoragePort, chosen: SelectedMeeting, outcome: TranscriptOutcome
) -> _Settled:
    """One outcome as the state it leaves, writing the captures when there are some."""
    meeting = chosen.meeting
    if isinstance(outcome, TranscriptCaptured):
        return _stored(storage, chosen, outcome)
    entry: TranscriptReportEntry
    if isinstance(outcome, TranscriptAbsent):
        entry = TranscriptReportEntry(
            chosen.scope, meeting.meeting_id, TranscriptStateKind.ABSENT, outcome.reason
        )
    elif isinstance(outcome, TranscriptSkipped):
        entry = TranscriptReportEntry(
            chosen.scope, meeting.meeting_id, TranscriptStateKind.SKIPPED, outcome.reason
        )
    elif isinstance(outcome, TranscriptRefused):
        entry = TranscriptReportEntry(
            chosen.scope,
            meeting.meeting_id,
            TranscriptStateKind.REFUSED,
            outcome.reason,
            retryable=outcome.retryable,
            retry_after=outcome.retry_after,
        )
    else:
        # A bug in a source, and a bug must not close a meeting for good: named,
        # and retryable so the meeting is asked again once the source is fixed.
        entry = TranscriptReportEntry(
            chosen.scope,
            meeting.meeting_id,
            TranscriptStateKind.REFUSED,
            f"the transcript source answered with {type(outcome).__name__}, which is "
            f"not an outcome the port declares; asked again next run.",
            retryable=True,
        )
    if not chosen.stored:
        return _Settled(entry)
    # Once a meeting holds a part, only a new stored part changes its row; a
    # refusal about its new transcripts is the run's to name.
    if entry.state is TranscriptStateKind.REFUSED:
        return _Settled(
            None,
            (_kept(chosen, entry.reason),),
            holds_parts=True,
        )
    return _Settled(None, holds_parts=True)


def _kept(chosen: SelectedMeeting, reason: str) -> str:
    count = len(chosen.stored)
    return (
        f"meeting {chosen.meeting.meeting_id} in {chosen.scope} keeps its {count} "
        f"stored transcript part(s) and its row; a new transcript was not "
        f"stored: {reason}"
    )


def _stored(
    storage: StoragePort, chosen: SelectedMeeting, outcome: TranscriptCaptured
) -> _Settled:
    """Every new part, in time order, each one claimed step (story 33g).

    The part's row is written `pending`, then its capture, then the row is
    marked `stored`. The first part that cannot be stored stops the rest, so
    no later part is numbered ahead of it: a refusal waiting may clear leaves
    it `pending` with its reason, and one it cannot removes the pending row and
    names the part as lost.
    """
    meeting = chosen.meeting
    rows = chosen.parts
    stored_ids = {row.transcript_id for row in chosen.stored}
    pending = {row.transcript_id: row for row in rows if row.state is PartState.PENDING}
    next_part = max((row.part for row in rows), default=0) + 1
    new: list[tuple[int, str, str, bool]] = []
    problem: str | None = None
    problem_retryable = False
    notes: list[str] = []

    for capture in outcome.captures:
        if capture.meeting_id != meeting.meeting_id:
            problem = (
                f"the source returned a capture for meeting {capture.meeting_id!r} "
                f"as this meeting's, so it was not stored."
            )
            break
        if capture.transcript_id in stored_ids:
            notes.append(
                f"the transcript source returned stored part {capture.transcript_id} "
                f"of meeting {meeting.meeting_id} again; it was not stored twice."
            )
            continue
        claimed = pending.pop(capture.transcript_id, None)
        if claimed is not None:
            part = claimed.part
        else:
            later = sorted(
                (row for row in pending.values() if row.created > capture.created),
                key=lambda row: row.part,
            )
            if later:
                problem = (
                    f"transcript {capture.transcript_id} was created before pending "
                    f"part {later[0].part} ({later[0].transcript_id}); parts are "
                    f"numbered in time order, so it was not stored."
                )
                break
            part = next_part
            next_part += 1
        try:
            name = capture_name(meeting.meeting_id, meeting.start, capture.format, part=part)
        except ValueError as misnamed:
            problem = f"transcript {capture.transcript_id} could not be named: {misnamed}"
            break
        try:
            storage.record_pending_part(
                chosen.scope,
                meeting.meeting_id,
                part,
                transcript_id=capture.transcript_id,
                created=capture.created,
                ended=capture.ended,
            )
        except Exception as unclaimed:  # noqa: BLE001 — one meeting, not the run
            problem = (
                f"transcript {capture.transcript_id} could not be claimed as part "
                f"{part}: recording it raised {type(unclaimed).__name__}; tried "
                f"again next run."
            )
            problem_retryable = True
            break
        refusal = _write(storage, meeting, capture.text, name, capture.transcript_id)
        if refusal is not None:
            reason, retryable = refusal
            if retryable:
                problem = f"part {part}: {reason}"
                try:
                    storage.record_pending_part(
                        chosen.scope,
                        meeting.meeting_id,
                        part,
                        transcript_id=capture.transcript_id,
                        created=capture.created,
                        ended=capture.ended,
                        reason=reason,
                    )
                except Exception as unsaved:  # noqa: BLE001 — one meeting, not the run
                    problem += (
                        f" Its reason could not be saved on the pending part "
                        f"(record_pending_part raised {type(unsaved).__name__})."
                    )
                problem_retryable = True
            else:
                problem = f"part {part} is lost: {reason}"
                problem_retryable = False
                try:
                    storage.drop_pending_part(chosen.scope, meeting.meeting_id, part)
                except Exception as undropped:  # noqa: BLE001 — one meeting, not the run
                    # The pending row stays, so the meeting is asked again.
                    problem += (
                        f" Its pending row could not be removed (drop_pending_part "
                        f"raised {type(undropped).__name__}); tried again next run."
                    )
                    problem_retryable = True
            break
        try:
            storage.mark_part_stored(chosen.scope, meeting.meeting_id, part)
        except Exception as unmarked:  # noqa: BLE001 — one meeting, not the run
            # The capture is on disk under a pending row: the next selection
            # repairs it.
            problem = (
                f"part {part} ({name}) was written and could not be marked stored "
                f"(mark_part_stored raised {type(unmarked).__name__}); the next "
                f"run marks it stored."
            )
            problem_retryable = True
            break
        new.append((part, name, capture.transcript_id, capture.attributed))

    holds_parts = bool(new) or bool(chosen.stored)
    if new:
        earlier = [
            (
                row.part,
                capture_name(meeting.meeting_id, meeting.start, CaptureFormat.GRAPH, part=row.part),
                row.transcript_id,
            )
            for row in chosen.stored
        ]
        every = sorted([*earlier, *((p, n, t) for p, n, t, _ in new)])
        described = ", ".join(f"{name} (transcript {tid})" for _, name, tid in every)
        unattributed = [name for _, name, _, attributed in new if not attributed]
        reason = f"stored {len(every)} transcript part(s): {described}."
        if unattributed:
            reason += (
                f" {', '.join(unattributed)} without speaker names (the tenant forbids them)."
            )
        refusals = list(notes)
        if problem is not None:
            reason += f" Not stored: {problem}"
            refusals.append(
                f"meeting {meeting.meeting_id} in {chosen.scope}: a transcript part "
                f"was not stored: {problem}"
            )
        return _Settled(
            TranscriptReportEntry(
                chosen.scope,
                meeting.meeting_id,
                TranscriptStateKind.FETCHED,
                reason,
                capture_names=tuple(name for _, name, _, _ in new),
                attributed=all(attributed for _, _, _, attributed in new),
            ),
            tuple(refusals),
            holds_parts=True,
        )
    if problem is None:
        # Every capture the source returned was a part already stored.
        return _Settled(None, tuple(notes), holds_parts=holds_parts)
    if chosen.stored:
        return _Settled(None, (*notes, _kept(chosen, problem)), holds_parts=True)
    return _Settled(
        TranscriptReportEntry(
            chosen.scope,
            meeting.meeting_id,
            TranscriptStateKind.REFUSED,
            problem,
            retryable=problem_retryable,
        ),
        tuple(notes),
    )


def _write(
    storage: StoragePort, meeting: Meeting, text: str, name: str, transcript_id: str
) -> tuple[str, bool] | None:
    """Write one part's capture; `None`, or the refusal and whether waiting may clear it."""
    try:
        storage.write_capture(text, scope=meeting.transcript_home, name=name)
    except CaptureAlreadyExists:
        return (
            f"{name} appeared while transcript {transcript_id} was being "
            f"fetched; a capture is never overwritten, so this one was not stored.",
            False,
        )
    except UnprotectedCaptureDir as unprotected:
        return (f"transcript {transcript_id} was not stored: {unprotected}", True)
    except EmptyCapture:
        return (
            f"transcript {transcript_id} had no content to store; tried again next run.",
            True,
        )
    except (MalformedCaptureName, ScopeResolutionError) as unwritable:
        return (
            f"transcript {transcript_id} could not be stored as {name}: {unwritable}",
            False,
        )
    except Exception as failed:  # noqa: BLE001 — one meeting, not the run
        # A disk that is full, a permission refused, or anything else the
        # writer raised that is not one of its declared refusals: nothing was
        # stored, and nothing says waiting will not clear it.
        return (
            f"transcript {transcript_id} could not be stored as {name}: writing it "
            f"raised {type(failed).__name__}; tried again next run.",
            True,
        )
    return None


def _awaiting_ingestion(
    storage: StoragePort, scopes: Sequence[DataScope]
) -> tuple[tuple[tuple[DataScope, str], ...], tuple[str, ...]]:
    """Every meeting with a stored Graph part not yet ingested — `11b`'s input.

    Read from the captures on disk: each Graph capture's record is found by
    name — the capture is the record's stem plus `-graph.vtt` or
    `-graph-<n>.vtt` — so this needs no reach-back, and a capture whose record
    is no longer in the scope is not offered. A meeting with part rows (story
    33g) awaits ingestion while any stored part is not ingested, unless its
    ingestion has failed `MAX_INGEST_ATTEMPTS` times since its last part was
    stored. A meeting with no part rows was captured before parts existed: a
    capture whose fetched row was never written is still handed on, one with
    an `ingested` row is never offered again, and one whose ingestion failed
    is offered until it has failed `MAX_INGEST_ATTEMPTS` times
    (`StoragePort.fetched_not_ingested`, story 11b). Listed by every
    acquisition and by `9a`'s cycle, so a daemon killed between capture and
    ingestion is repaired on the next run. A scope that cannot be read is
    named and the others are still listed.
    """
    awaiting: list[tuple[DataScope, str]] = []
    refusals: list[str] = []
    for scope in scopes:
        try:
            states = storage.transcript_states(scope)
            parts = storage.transcript_parts(scope)
            pending = set(storage.fetched_not_ingested(scope))
            members = set(storage.list_collection(scope=scope, artifact=MEETINGS))
            found: list[tuple[DataScope, str]] = []
            read: set[str] = set()
            for name in storage.list_collection(scope=scope, artifact=CAPTURES):
                stem = _graph_stem(name)
                if stem is None or stem in read:
                    continue
                read.add(stem)
                member = stem + ".md"
                if member not in members:
                    continue
                raw = storage.read_artifact(scope=scope, artifact=MEETINGS, name=member)
                if raw is None:
                    continue
                meeting_id = parse_record(raw.decode("utf-8"), source=member).meeting.meeting_id
                rows = parts.get(meeting_id)
                if rows:
                    if not any(row.state is PartState.STORED and not row.ingested for row in rows):
                        continue
                    state = states.get(meeting_id)
                    if state is None or not state.ingestion_exhausted:
                        found.append((scope, meeting_id))
                elif name == f"{stem}-{CaptureFormat.GRAPH.value}" and (
                    meeting_id not in states or meeting_id in pending
                ):
                    found.append((scope, meeting_id))
        except Exception as unreadable:  # noqa: BLE001 — one scope, not the run
            refusals.append(
                f"the captures in {scope} could not be listed for ingestion: "
                f"{_named(unreadable)}"
            )
            continue
        awaiting.extend(found)
    return tuple(awaiting), tuple(refusals)


_GRAPH_PART = re.compile(
    rf"(?P<stem>.+)-{re.escape(CaptureFormat.GRAPH.value.partition('.')[0])}"
    rf"(?:-[1-9][0-9]*)?\.{re.escape(CaptureFormat.GRAPH.value.partition('.')[2])}"
)


def _graph_stem(name: str) -> str | None:
    """The record stem of a Graph part's capture name, or `None` for any other."""
    matched = _GRAPH_PART.fullmatch(name)
    if matched is None or not is_graph_capture(name, matched["stem"]):
        return None
    return matched["stem"]


_ACQUISITION_STATES = ACQUISITION_STATES
"""The states this slice writes. Any other is an ingestion state (`11b`)."""


def _named(error: Exception) -> str:
    """A refusal's own sentence for the ones pm-ai composes; the type otherwise."""
    if isinstance(error, (MalformedMeeting, ScopeResolutionError)):
        return str(error)
    return f"reading them raised {type(error).__name__}."
