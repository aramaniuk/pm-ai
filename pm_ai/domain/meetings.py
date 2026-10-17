"""The Meeting entity (AD-33).

A transcript is a derivative of a meeting, so facts cite the *meeting* — which
never expires — rather than the capture, which purges at 30 days. Making Meeting
first-class also gives FR-03's Man-Hour Cost one home instead of three ad-hoc
lookups across FR-03, FR-32, and UJ-8.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from pm_ai.domain.identity import Actor, DataScope, SourceRef

MATCH_TOLERANCE = timedelta(minutes=15)
"""How far apart two starts may be and still be one meeting (D3, AD-33).

Start to start, inclusive at both ends. It is not `33e`'s widening of a
transcript's placement window: that one says where a provider's transcript may
fall around a meeting, this one says when a dropped transcript and a calendar
row describe the same meeting — two figures, not one rule.
"""


def tolerance_minutes() -> int:
    """`MATCH_TOLERANCE` in whole minutes, for the sentences that name it."""
    return int(MATCH_TOLERANCE.total_seconds() // 60)


MEETING_RECORD_NAME = re.compile(r"\d{4}-\d{2}-\d{2}-[A-Za-z0-9._-]*-[0-9a-f]{64}\.md")
"""The full spelling of a meeting record's filename: the UTC day, a lossy slug
of the id, and the id's sha256 (`meeting_records.record_name`). Stated here so
the single writer can hold a removal in `meetings/` to it (story 11b)."""


def normalised_title(title: str) -> str:
    """A meeting title as the one-record-per-meeting rule compares it (AD-33).

    Unicode NFKC, case-folded, trimmed at both ends, and every run of
    whitespace collapsed to one space — so "Weekly  Sync" and "weekly sync",
    or a composed and a decomposed "Café", are one title. Normalised again after
    folding, because `casefold` can leave a string NFKC would still change.
    """
    folded = unicodedata.normalize("NFKC", title).casefold()
    return " ".join(unicodedata.normalize("NFKC", folded).split())


def is_candidate(meeting: Meeting, *, title: str, start: datetime) -> bool:
    """Whether `meeting` is the one a title and a start describe (AD-33).

    An empty or whitespace-only title describes nothing, on either side: two
    untitled meetings near one another are not thereby one meeting.
    """
    wanted = normalised_title(title)
    return (
        bool(wanted)
        and abs(meeting.start - start) <= MATCH_TOLERANCE
        and normalised_title(meeting.title) == wanted
    )


class AmbiguousMeeting(ValueError):
    """Two or more recorded meetings match one title and start (AD-33).

    Refused rather than guessed: binding a transcript to the wrong one of two
    meetings would cite every extraction to a meeting it was not said in. The
    message names every candidate's id so the caller can name one.
    """

    def __init__(self, message: str, candidates: tuple[str, ...]) -> None:
        super().__init__(message)
        self.candidates = candidates


class InvalidAttendeeCount(ValueError):
    """An attendee count that is not a whole number at least the named list."""


def check_attendee_count(count: object, *, named: int, meeting_id: str) -> int:
    """The attendee-count rule, stated once: a whole number, never below `named`.

    `named` is how many attendees pm-ai could name, a subset of every attendee
    the provider listed — so a count below it contradicts the list. Raised by
    `Meeting` and, through it, mapped to `MalformedMeeting` by the record parser.
    """
    if not isinstance(count, int) or isinstance(count, bool) or count < named:
        raise InvalidAttendeeCount(
            f"meeting {meeting_id!r} has attendee_count={count!r} and {named} "
            f"named attendees. The count is every attendee the provider listed, "
            f"so it is a whole number no smaller than the list of those pm-ai "
            f"could name."
        )
    return count


@dataclass(frozen=True, slots=True)
class Meeting:
    """Tier-1, durable, and the citation root for everything said in it."""

    meeting_id: str
    title: str
    start: datetime
    duration_minutes: int
    attendees: tuple[Actor, ...]
    # AD-33/AD-38 — a Meeting belongs to the scope that owns its subject: a team
    # meeting to its project, a 1:1 with a direct report to `people`. This is
    # required rather than defaulted because it decides two things no caller may
    # guess: where the transcript is written, and whether a record in the project
    # scope is allowed to cite this meeting at all. Meetings were previously filed in
    # the personal scope, which made every commitment citing one an AD-38
    # violation by construction.
    scope: DataScope
    calendar_event_ref: str | None = None
    # Carried in memory and written to no file (story 33c, decided 2026-09-07).
    #
    # A response status is an answer about a meeting that has **not** happened,
    # and no meeting that has not happened is recorded: the calendar owns it, and
    # a local copy of a row that can move or vanish outside pm-ai cannot be kept
    # accurate. So this rides the `Meeting` a connector hands back for the day
    # ahead — the dashboard renders it — and `pm_ai.core.meeting_records` has no
    # `tentative` field to put it in. `render_record` writes the keys in `_FIELDS`
    # and this is not one of them, which is what makes "stored nowhere" a
    # property of the grammar rather than of every caller remembering.
    #
    # `False` by default because that is what a record read back off disk knows:
    # `parse_record` constructs a `Meeting` without it, and a held meeting has no
    # tentative answer to have lost.
    tentative: bool = False
    # Carried in memory and written to no file, on `tentative`'s precedent above
    # and for a different reason (story 23b, 2026-09-15).
    #
    # `meeting_id` is the provider's per-mailbox event id, so the same meeting
    # cross-invited to two tenants arrives twice under two different ids and
    # matching on it deduplicates nothing in the only case that motivates
    # deduplication. `iCalUId` is the identifier that survives the crossing, and
    # this is where it rides — from the connector that read it to the surface
    # that merges two calendars into one day.
    #
    # Nowhere near a file: `meeting_records._FIELDS` has no key for it, so the
    # record grammar is unchanged and every golden still holds. `None` by
    # default because that is what a record read back off disk knows, and
    # because absence has to stay absent — two meetings that both lack it are
    # not the same meeting, and a `""` default would make them look it.
    ical_uid: str | None = None
    # How many attendee entries the provider listed — every attendee, named or
    # not (story 2n, 2026-10-11). `attendees` says *who* pm-ai could name, as
    # handles; this says *how many* there were, and is what the Man-Hour Cost
    # multiplies. An attendee with no usable address is left out of the list
    # and still counted here.
    #
    # `None` at construction means the list is the whole count — a meeting
    # whose attendees were all named, such as a manual record — and is replaced
    # by `len(attendees)` in `__post_init__`, so every `Meeting` carries a
    # number. Stored in the meeting record.
    attendee_count: int | None = None
    # Minted by pm-ai from a dropped transcript, with no calendar row behind it
    # (story 11b, AD-33). The mark is what lets a later calendar harvest adopt
    # this record instead of minting a second one for the same meeting: only a
    # manual-only record is adopted, and adoption clears the mark. Stored in
    # the record, written only when set.
    manual_only: bool = False
    # Carried in memory and written to no file, on `tentative`'s precedent
    # (story 23e, Andrei's decision on field-test finding 7, 2026-10-16).
    #
    # The calendar row's `isCancelled`. A cancelled meeting not yet ended never
    # reaches a `Meeting` — the connector leaves it out — so this is set only on
    # one that has ended, which the dashboard lists marked as cancelled. Its
    # record and citations are unchanged: `meeting_records._FIELDS` has no key
    # for it. `False` by default because that is what a record read back off
    # disk knows.
    cancelled: bool = False
    # Carried in memory and written to no file, on `tentative`'s precedent
    # (story 23e). An all-day entry's own calendar date as the provider wrote
    # it, `None` for every other meeting. The dashboard places an all-day
    # marker by this date rather than by converting its start to the display
    # zone, which west of UTC would move it onto the previous day.
    all_day_date: date | None = None

    def __post_init__(self) -> None:
        count = len(self.attendees) if self.attendee_count is None else self.attendee_count
        check_attendee_count(count, named=len(self.attendees), meeting_id=self.meeting_id)
        object.__setattr__(self, "attendee_count", count)

    @property
    def source_ref(self) -> SourceRef:
        """What a derived fact cites (AD-33) — stable for the life of the record."""
        return SourceRef.parse(f"meeting:{self.meeting_id}")

    @property
    def transcript_home(self) -> DataScope:
        """A transcript lives in the same scope as the meeting it captures.

        The capture cannot be more or less shareable than the event it records.
        """
        return self.scope

    def man_hour_cost(self, blended_hourly_rate: float) -> float:
        """FR-03. A single PM-configured rate, never per-attendee salary data.

        Multiplies by `attendee_count`, not `len(attendees)`: the list holds only
        the attendees pm-ai could name, and the cost counts every attendee.
        """
        count = len(self.attendees) if self.attendee_count is None else self.attendee_count
        return count * (self.duration_minutes / 60) * blended_hourly_rate
