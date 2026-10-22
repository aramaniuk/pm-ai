"""`pm-ai transcript list`'s one rule and one wording (story 11f). I/O-free.

`decide` turns what `pm_ai.app` read — a meeting's transcript row, its parts,
the capture folder's names, its summary row, its record and the clock — into
a `ListedMeeting`: the transcript's state, the summary's state, and the next
step or why there is none. The listing's text, the drop's closing line and
both JSON forms are built from that value by the functions here, so the
words cannot drift between them.

No transcript text, summary text, action item or attendee is read into the
value, so none can reach the output.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pm_ai.core.meeting_records import capture_kept
from pm_ai.core.rendering import cut_at_word
from pm_ai.domain.meeting_listing import (
    LISTING_FORMAT,
    ListedMeeting,
    ListedSummary,
    ListedTranscript,
    Listing,
    NextStep,
    RecordReading,
    WaitingKind,
    drop_words,
    review_words,
    summarize_words,
)
from pm_ai.domain.meeting_summaries import (
    RecordedSummary,
    SummaryMaker,
    SummaryState,
    SummaryStateKind,
)
from pm_ai.domain.meetings import Meeting, normalised_title
from pm_ai.domain.transcripts import TranscriptPart, TranscriptState, TranscriptStateKind

__all__ = [
    "LISTED_TRANSCRIPT_STATES",
    "SUMMARY_AGE",
    "TITLE_LIMIT",
    "UNTITLED",
    "decide",
    "fold_shown",
    "listing_json",
    "listing_text",
    "meeting_json",
    "next_json",
    "next_words",
    "passes",
    "row_passes",
    "shown_title",
    "summary_json",
    "summary_state_word",
    "summary_words",
    "title_matches",
    "title_words",
    "transcript_words",
    "when_shown",
    "zone_of",
]

SUMMARY_AGE = timedelta(days=30)
"""`SUMMARY_MAX_AGE`, the harvest's: a meeting older is never offered automatically.

Spelled here because core may not import `pm_ai.app`; a test holds the two equal."""

TITLE_LIMIT = 80
"""A title in the text form is cut at a word to this many characters."""

UNTITLED = "(untitled)"

LISTED_TRANSCRIPT_STATES = frozenset(
    {
        TranscriptStateKind.FETCHED.value,
        TranscriptStateKind.INGESTED.value,
        TranscriptStateKind.INGEST_FAILED.value,
        TranscriptStateKind.REFUSED.value,
    }
)
"""A transcript row in one of these lists its meeting; `absent` and `skipped`
list it only beside a summary row."""

_SUMMARY_STATES = {
    SummaryStateKind.WAITING: "waiting",
    SummaryStateKind.SUMMARIZED: "summarized",
    SummaryStateKind.FAILED: "failed",
    SummaryStateKind.NOTHING_SAID: "nothing_said",
}

def summary_state_word(kind: SummaryStateKind) -> str:
    """A summary row's state as the listing and its JSON spell it."""
    return _SUMMARY_STATES[kind]


STORE_UNREADABLE = "the operational store could not be read"
TEAMS_LISTED_NONE = "Teams listed no transcript"
FETCHED_WHY = "ingested at the daemon's next run"
MISSING_WHY = "the transcript is missing, so the summary cannot be made"
NOTHING_SAID_WHY = "the transcript held no words after cleaning"
NO_ROW_WHY = "no transcript is recorded for this meeting, so the summary cannot be made"
NO_DAEMON_WHY = (
    "no daemon answers on the loopback port; start one with `pm-ai daemon run` first"
)


# ── Folding, cutting and matching titles ─────────────────────────────────────


def fold_shown(text: str) -> str:
    """Control and format characters (Cc, Cf) as spaces, then runs of spaces as one.

    The right-to-left overrides are Cf, so a title can neither forge a second
    row nor reorder the line it is on.
    """
    spaced = "".join(
        " " if unicodedata.category(char) in ("Cc", "Cf") else char for char in text
    )
    return " ".join(spaced.split())


def shown_title(title: str | None) -> str:
    """A title as the text form shows it: folded, cut at a word to 80, or `(untitled)`."""
    folded = fold_shown(title or "")
    return cut_at_word(folded, TITLE_LIMIT) if folded else UNTITLED


_WORD_BREAK = re.compile(r"[\W_]+")


def title_words(text: str) -> tuple[str, ...]:
    """A title's words, folded as `normalised_title` folds (NFKC, case-folded),
    split on whitespace and punctuation."""
    return tuple(word for word in _WORD_BREAK.split(normalised_title(text)) if word)


def title_matches(title: str, given: str) -> bool:
    """Whether every word of `given` is a word of `title` (`--title`'s rule)."""
    wanted = title_words(given)
    if not wanted:
        return False
    held = set(title_words(title))
    return all(word in held for word in wanted)


# ── Times ────────────────────────────────────────────────────────────────────


def zone_of(name: str) -> tzinfo | None:
    """`display_timezone` as a zone, `None` when unset or unknown here."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def when_shown(start: datetime, zone: str) -> str:
    """A start in UTC, and in `display_timezone` when one is set (the drop's form)."""
    shown = start.astimezone(timezone.utc).isoformat()
    local_zone = zone_of(zone)
    if local_zone is None:
        return shown
    local = start.astimezone(local_zone)
    return f"{shown} ({local.strftime('%Y-%m-%d %H:%M')} in {zone})"


def day_in(start: datetime, zone: str) -> date:
    """The date `start` falls on in the listing's zone: `display_timezone`, else UTC."""
    local_zone = zone_of(zone)
    return start.astimezone(local_zone or timezone.utc).date()


# ── The decision ─────────────────────────────────────────────────────────────


def decide(
    *,
    scope: str,
    meeting_id: str,
    record: RecordReading,
    meeting: Meeting | None = None,
    held: RecordedSummary | None = None,
    record_reason: str | None = None,
    stem: str | None = None,
    store_readable: bool = True,
    transcript: TranscriptState | None = None,
    parts: Sequence[TranscriptPart] = (),
    captures: Iterable[str] = (),
    summary: SummaryState | None = None,
    local_model: bool = False,
    daemon_reachable: bool = True,
    now: datetime,
    waiting: int = 0,
) -> ListedMeeting:
    """One meeting's states and next step, from what was read (story 11f).

    `stem` is the record's file name without `.md`, which every capture's name
    starts with; with it and the capture folder's `captures`, whether the
    transcript is kept is `capture_kept`'s rule. `held` is the record's
    `## Summary`, `None` when it holds none.
    """
    readable = record is RecordReading.READABLE and meeting is not None
    if record_reason is not None:
        record_reason = fold_shown(record_reason)
    listed_transcript = _transcript(
        transcript, store_readable=store_readable, stem=stem, parts=parts, captures=captures
    )
    listed_summary = _summary(
        summary,
        store_readable=store_readable,
        held=held if readable else None,
        readable=readable,
        transcript_state=listed_transcript.state,
        start=meeting.start if readable and meeting is not None else None,
        local_model=local_model,
        now=now,
    )
    step = _next(
        scope,
        meeting_id,
        record=record if readable else (
            RecordReading.MISSING if record is RecordReading.MISSING else RecordReading.UNREADABLE
        ),
        record_reason=record_reason,
        transcript=listed_transcript,
        summary=listed_summary,
        store_readable=store_readable,
        daemon_reachable=daemon_reachable,
    )
    if waiting:
        # Story 13a: the meeting's proposals are waiting for review.
        step = replace(step, review=review_words(scope, meeting_id))
    if not readable or meeting is None:
        return ListedMeeting(
            scope=scope,
            meeting_id=meeting_id,
            record=record if record is not RecordReading.READABLE else RecordReading.UNREADABLE,
            transcript=listed_transcript,
            summary=listed_summary,
            next=step,
            record_reason=record_reason,
        )
    return ListedMeeting(
        scope=scope,
        meeting_id=meeting_id,
        record=RecordReading.READABLE,
        transcript=listed_transcript,
        summary=listed_summary,
        next=step,
        title=meeting.title,
        start=meeting.start,
        start_estimated=meeting.start_estimated_from is not None,
    )


def _transcript(
    row: TranscriptState | None,
    *,
    store_readable: bool,
    stem: str | None,
    parts: Sequence[TranscriptPart],
    captures: Iterable[str],
) -> ListedTranscript:
    if not store_readable:
        return ListedTranscript("unknown")
    if row is None:
        return ListedTranscript("none")
    reason = fold_shown(row.reason) if row.reason else None
    if row.state == TranscriptStateKind.INGESTED.value:
        kept = stem is not None and capture_kept(stem, parts, captures)
        return ListedTranscript(row.state, None, kept)
    if row.state in (
        TranscriptStateKind.INGEST_FAILED.value,
        TranscriptStateKind.REFUSED.value,
    ):
        return ListedTranscript(row.state, reason)
    return ListedTranscript(row.state, None)


def _summary(
    row: SummaryState | None,
    *,
    store_readable: bool,
    held: RecordedSummary | None,
    readable: bool,
    transcript_state: str,
    start: datetime | None,
    local_model: bool,
    now: datetime,
) -> ListedSummary:
    held_flag: bool | None = (held is not None) if readable else None
    held_fields: dict[str, object] = {}
    if held is not None:
        held_fields = {
            "held_by": "claude" if held.made_by is SummaryMaker.FRONTIER else "local",
            "held_model": fold_shown(held.model),
            "held_on": held.made_on,
            "held_requested": held.requested,
        }
    if not store_readable:
        return ListedSummary("unknown", held_flag, **held_fields)  # type: ignore[arg-type]
    if row is None:
        return ListedSummary("none", held_flag, **held_fields)  # type: ignore[arg-type]
    state = _SUMMARY_STATES[row.state]
    made_by = "claude" if row.made_by is SummaryMaker.FRONTIER else "local"
    waiting: WaitingKind | None = None
    if row.state is SummaryStateKind.WAITING:
        if row.requested and row.made_by is SummaryMaker.FRONTIER:
            waiting = WaitingKind.CLAUDE
        elif transcript_state != TranscriptStateKind.INGESTED.value:
            waiting = WaitingKind.NOT_INGESTED
        elif not readable or start is None:
            waiting = WaitingKind.RECORD_UNREADABLE
        elif start is not None and start < now - SUMMARY_AGE:
            waiting = WaitingKind.TOO_OLD
        elif not local_model:
            waiting = WaitingKind.NO_LOCAL_MODEL
        else:
            waiting = WaitingKind.HARVEST
    return ListedSummary(
        state,
        held_flag,
        made_by=made_by,
        requested=row.requested,
        model=fold_shown(row.model) if row.model else None,
        reason=fold_shown(row.reason) if row.reason else None,
        at=row.at,
        offered_automatically=waiting is WaitingKind.HARVEST,
        waiting=waiting,
        **held_fields,  # type: ignore[arg-type]
    )


def _next(
    scope: str,
    meeting_id: str,
    *,
    record: RecordReading,
    record_reason: str | None,
    transcript: ListedTranscript,
    summary: ListedSummary,
    store_readable: bool,
    daemon_reachable: bool,
) -> NextStep:
    if record is RecordReading.MISSING:
        return NextStep(why=record_reason or "no record of this meeting is on file")
    if record is RecordReading.UNREADABLE:
        return NextStep(why=record_reason or "its record could not be read")
    if not store_readable:
        return NextStep(why=STORE_UNREADABLE)
    state = transcript.state
    if state in (TranscriptStateKind.REFUSED.value, TranscriptStateKind.INGEST_FAILED.value):
        return NextStep(drop=drop_words(scope, meeting_id))
    if state == TranscriptStateKind.FETCHED.value:
        return NextStep(why=FETCHED_WHY)
    if state in (TranscriptStateKind.ABSENT.value, TranscriptStateKind.SKIPPED.value):
        return NextStep(why=TEAMS_LISTED_NONE)
    if state != TranscriptStateKind.INGESTED.value:
        return NextStep(why=NO_ROW_WHY)
    if not transcript.kept:
        return NextStep(why=MISSING_WHY)
    if summary.state == "nothing_said":
        return NextStep(why=NOTHING_SAID_WHY)
    why = None if daemon_reachable else NO_DAEMON_WHY
    if summary.held:
        return NextStep(
            resummarize=summarize_words(scope, meeting_id, held=True),
            why=why,
            needs_daemon=not daemon_reachable,
        )
    return NextStep(
        summarize=summarize_words(scope, meeting_id, held=False),
        why=why,
        needs_daemon=not daemon_reachable,
    )


# ── Filters ──────────────────────────────────────────────────────────────────


def row_passes(
    state: str,
    *,
    transcript_state: str,
    kept: bool | None,
    summary_state: str,
    held: bool | None,
) -> bool | None:
    """Whether a meeting passes `--state` from its rows; `None` when only the record can say.

    `summarized` and `not-summarized` turn on whether the record holds a
    summary, so with `held` unknown they answer `None`.
    """
    if state == "all":
        return True
    if state == "waiting":
        return summary_state == "waiting"
    if state == "failed":
        return summary_state == "failed" or transcript_state == TranscriptStateKind.INGEST_FAILED.value
    if held is None:
        return None
    if state == "summarized":
        return held
    if state == "not-summarized":
        return (
            not held
            and transcript_state == TranscriptStateKind.INGESTED.value
            and bool(kept)
            and summary_state != "nothing_said"
        )
    raise ValueError(f"--state {state!r} is not one of the five words")


def passes(listed: ListedMeeting, state: str) -> bool:
    """Whether a decided meeting passes `--state`; an unknown answer is no."""
    return bool(
        row_passes(
            state,
            transcript_state=listed.transcript.state,
            kept=listed.transcript.kept,
            summary_state=listed.summary.state,
            held=listed.summary.held,
        )
    )


# ── The words ────────────────────────────────────────────────────────────────


def transcript_words(listed: ListedMeeting) -> str:
    """The transcript's state, as the listing and the drop say it."""
    transcript = listed.transcript
    state = transcript.state
    if state == TranscriptStateKind.INGESTED.value:
        return "transcript ingested" if transcript.kept else "transcript ingested, capture missing"
    if state == TranscriptStateKind.FETCHED.value:
        return "transcript fetched; the daemon ingests it at its next run"
    if state == TranscriptStateKind.INGEST_FAILED.value:
        return f"transcript ingestion failed: {transcript.reason or 'no reason was recorded'}"
    if state == TranscriptStateKind.REFUSED.value:
        return f"transcript refused by Teams: {transcript.reason or 'no reason was recorded'}"
    if state in (TranscriptStateKind.ABSENT.value, TranscriptStateKind.SKIPPED.value):
        return f"transcript {state}"
    if state == "none":
        return "no transcript recorded"
    return "transcript state unknown"


_WAITING_WORDS = {
    WaitingKind.HARVEST: "summary waiting for the daemon's next harvest (local model)",
    WaitingKind.NO_LOCAL_MODEL: "summary waiting; no local_model is set, so no harvest makes it",
    WaitingKind.CLAUDE: "summary waiting for you to ask Claude again",
    WaitingKind.TOO_OLD: (
        "summary waiting, not offered automatically: the meeting started more than "
        f"{SUMMARY_AGE.days} days ago"
    ),
    WaitingKind.NOT_INGESTED: (
        "summary waiting, not offered automatically: the transcript is not ingested"
    ),
    WaitingKind.RECORD_UNREADABLE: (
        "summary waiting, not offered automatically: the meeting's record cannot be read"
    ),
}


def _maker(made_by: str | None, model: str | None) -> str:
    who = "Claude" if made_by == "claude" else "the local model"
    return f"{who} {model}" if model else who


def _held_words(summary: ListedSummary) -> str:
    how = "on request" if summary.held_requested else "automatically"
    on = f", on {summary.held_on.isoformat()}" if summary.held_on is not None else ""
    return f"{_maker(summary.held_by, summary.held_model)}, {how}{on}"


def summary_words(listed: ListedMeeting) -> str:
    """The summary's state, as the listing and the drop say it."""
    summary = listed.summary
    state = summary.state
    if state == "unknown":
        return "summary state unknown"
    if state == "none":
        said = "no summary"
    elif state == "waiting":
        said = _WAITING_WORDS[summary.waiting or WaitingKind.HARVEST]
    elif state == "summarized":
        how = "on request" if summary.requested else "automatically"
        on = f", on {summary.at.date().isoformat()}" if summary.at is not None else ""
        said = f"summarized by {_maker(summary.made_by, summary.model)}, {how}{on}"
    elif state == "failed":
        said = f"summary failed: {summary.reason or 'no reason was recorded'}"
    elif state == "nothing_said":
        said = f"nothing said: {NOTHING_SAID_WHY}"
    else:
        said = f"summary {state}"
    if summary.held and state != "summarized":
        said += f"; the record holds an earlier summary, by {_held_words(summary)}"
    elif summary.held is False and state == "summarized":
        said += "; the record holds no summary"
    return said


def next_words(listed: ListedMeeting) -> str:
    """The next step, or why there is none, as the listing and the drop say it.

    Story 13a: with proposals waiting, the review command follows.
    """
    said = _next_step_words(listed)
    if listed.next.review is not None:
        said += f"; to review its proposals: pm-ai {' '.join(listed.next.review)}"
    return said


def _next_step_words(listed: ListedMeeting) -> str:
    step = listed.next
    first = (
        "once a daemon runs (start one with `pm-ai daemon run` first)"
        if step.needs_daemon
        else ""
    )
    if step.summarize is not None:
        head = "next" + (f", {first}" if first else "")
        return f"{head}: pm-ai {' '.join(step.summarize)}"
    if step.resummarize is not None:
        head = "next, replacing the summary held" + (f", {first}" if first else "")
        return f"{head}: pm-ai {' '.join(step.resummarize)}"
    if step.drop is not None:
        return f"next: drop the file again with pm-ai {' '.join(step.drop)} <file>"
    return f"no next step: {step.why or 'nothing is waiting on you'}"


def _first_line(listed: ListedMeeting, zone: str) -> str:
    if listed.record is RecordReading.READABLE and listed.start is not None:
        when = when_shown(listed.start, zone)
        if listed.start_estimated:
            when += " (estimated)"
        return f"{when}  {listed.scope}  {listed.meeting_id}  {shown_title(listed.title)}"
    label = "record missing" if listed.record is RecordReading.MISSING else "record unreadable"
    return f"{label}  {listed.scope}  {fold_shown(listed.meeting_id)}"


def meeting_lines(listed: ListedMeeting, zone: str) -> tuple[str, str, str]:
    """One meeting's three lines: start, scope, id and title; the states; the next step."""
    return (
        _first_line(listed, zone),
        f"  {transcript_words(listed)}; {summary_words(listed)}",
        f"  {next_words(listed)}",
    )


def _zone_words(listing: Listing) -> str:
    if listing.zone and zone_of(listing.zone) is not None:
        return listing.zone
    if listing.zone:
        return f"UTC (display_timezone {listing.zone!r} is not a zone known here)"
    return f"UTC ({listing.zone_note or 'no display_timezone is set'})"


def listing_text(listing: Listing) -> str:
    """The text form: a header, the unreadable sources, three lines per meeting, the tail."""
    scopes = ", ".join(listing.scopes) or "no scope"
    lines: list[str] = []
    faults = [f"pm-ai could not read {fault.source}: {fault.reason}" for fault in listing.unreadable]
    if not listing.store_readable and listing.state != "all":
        faults.append(
            f"nothing is listed under --state {listing.state}: {STORE_UNREADABLE}, so no "
            f"meeting's state is known"
        )
    if not listing.meetings:
        lines.extend(faults)
        if listing.store_readable or listing.state == "all":
            lines.append(
                f"No meeting with a transcript is listed under --state {listing.state} in "
                f"{scopes}; `pm-ai transcript drop` adds a meeting."
            )
        return "\n".join(lines) + "\n"
    lines.append(
        f"Meetings with a transcript in {scopes}, newest first (--state {listing.state}); "
        f"times in {_zone_words(listing)}:"
    )
    lines.extend(faults)
    for listed in listing.meetings:
        lines.extend(meeting_lines(listed, listing.zone))
    if listing.more:
        lines.append(f"and {listing.more} older (--limit {listing.limit})")
    newest = next((listed for listed in listing.meetings if listed.next.summarize), None)
    if newest is not None and newest.next.summarize is not None:
        lines.append(
            f"To summarize the newest meeting not yet summarized: pm-ai "
            f"{' '.join(newest.next.summarize)} (adding --by claude asks Claude instead, "
            f"which is a paid call)."
        )
    return "\n".join(lines) + "\n"


# ── The machine form ─────────────────────────────────────────────────────────


def _instant(at: datetime | None) -> str | None:
    return None if at is None else at.astimezone(timezone.utc).isoformat()


def summary_json(listed: ListedMeeting) -> dict[str, object]:
    """The `summary` object; every field but `held` is null for `none` and `unknown`."""
    summary = listed.summary
    if summary.state in ("none", "unknown"):
        return {
            "state": summary.state,
            "held": summary.held,
            "made_by": None,
            "requested": None,
            "model": None,
            "reason": None,
            "at": None,
            "offered_automatically": None,
        }
    return {
        "state": summary.state,
        "held": summary.held,
        "made_by": summary.made_by,
        "requested": summary.requested,
        "model": summary.model,
        "reason": summary.reason,
        "at": _instant(summary.at),
        "offered_automatically": bool(summary.offered_automatically),
    }


def next_json(listed: ListedMeeting) -> dict[str, object]:
    """The `next` object: word lists, the drop's words and what it needs, or why."""
    step = listed.next
    return {
        "summarize": None if step.summarize is None else list(step.summarize),
        "resummarize": None if step.resummarize is None else list(step.resummarize),
        "drop": None if step.drop is None else {"words": list(step.drop), "needs": ["file"]},
        "why": step.why,
        "review": None if step.review is None else list(step.review),
    }


def meeting_json(listed: ListedMeeting) -> dict[str, object]:
    """One meeting object of the listing's JSON (story 11f)."""
    readable = listed.record is RecordReading.READABLE
    return {
        "scope": listed.scope,
        "meeting_id": listed.meeting_id,
        "record": listed.record.value,
        "title": listed.title if readable else None,
        "start": _instant(listed.start) if readable else None,
        "start_estimated": listed.start_estimated if readable else None,
        "transcript": {
            "state": listed.transcript.state,
            "reason": listed.transcript.reason,
            "kept": listed.transcript.kept
            if listed.transcript.state == TranscriptStateKind.INGESTED.value
            else None,
        },
        "summary": summary_json(listed),
        "next": next_json(listed),
    }


def listing_json(listing: Listing) -> str:
    """The machine form: one object, ASCII only (story 11f)."""
    return json.dumps(
        {
            "format": LISTING_FORMAT,
            "zone": listing.zone if listing.zone and zone_of(listing.zone) else "UTC",
            "scopes": list(listing.scopes),
            "daemon_reachable": listing.daemon_reachable,
            "unreadable": [
                {"source": fault.source, "reason": fault.reason} for fault in listing.unreadable
            ],
            "more": listing.more,
            "meetings": [meeting_json(listed) for listed in listing.meetings],
        },
        ensure_ascii=True,
    )
