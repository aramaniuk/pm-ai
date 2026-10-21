"""A dropped transcript's meeting details, decided from what pm-ai holds (story 11e).

`transcript drop` asks nothing. Every field is decided here, in a fixed order
of sources per field, and the accuracy required follows what the field is for:

| Field | Sources, in order |
|---|---|
| Title | `--title`; the header's title line, the recording stamp removed; the local model's reading of the header; the file name, stamp and extension removed |
| Start | `--start`; one calendar-sourced record of the same title on the hint day; the `.docx` creation time minus the length; the header's date and time in `display_timezone`; the file's modification time minus the length |
| Length | `--duration`; the header's length line; a WebVTT file's last cue end; the local model's reading of the header; the last turn's time |

The first source that gives a value the field's rule accepts decides; a value
refused is treated as not given. Each value travels with its source and
whether it is estimated (`Decided`), so the closing line and `--dry-run` say
where it came from rather than infer it afterwards.

**The header** is the non-blank lines before the first turn of a file read by
the speaker-and-time reader (`turn_header`). Each line is prepared before it
is judged: trimmed, every Unicode space made an ordinary space, runs of spaces
made one, Markdown's marks set aside (those `_unmarked` sets aside, emphasis
wrapping the whole line, a backslash before a punctuation character), and a
line that is then only `Transcript` skipped. The fixed readers know two start
forms and four length forms, measured on two real exports or covered by tests;
a header they leave a field unread in goes, cleaned, to the local model, whose
answer is validated here (`read_model_answer`): its title must be text already
in a header line.

**Participants** are the speakers' display names, as written, once each by
`normalise()`'s key, in order of first turn. They are names, never handles
(AD-34): a participant is linked to a handle only through the saved alias
table, on an exact key.

I/O-free: the caller reads the file, its times, the records and the alias
table, and makes the model call; nothing here touches a file, a clock or a
model, and nothing here accepts cleaned text (`Sanitized`) — `header_fragments`
makes the call's fragments, as `meeting_summary.summary_prompt` does for the
summary, so the pipeline never names the sanitizer.
"""

from __future__ import annotations

import json
import math
import re
import string
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import PurePath
from typing import Generic, TypeVar

from pm_ai.core.aliases import AliasTable, normalise
from pm_ai.core.meeting_records import MalformedMeeting, check_title
from pm_ai.core.transcripts import (
    MAX_DROP_MINUTES,
    MIN_DROP_MINUTES,
    carried_minutes,
    last_turn_seconds,
    turn_header,
    unmarked_line,
)
from pm_ai.domain.handles import InvalidPersonHandle, PersonHandle, person_handle
from pm_ai.domain.meetings import (
    Meeting,
    falls_on_day,
    is_candidate,
    normalised_title,
    tolerance_minutes,
)
from pm_ai.domain.sanitize import Sanitized, sanitize
from pm_ai.domain.transcripts import Utterance

__all__ = [
    "CREATION_TOLERANCE",
    "ESTIMATED_FROM",
    "HEADER_INSTRUCTIONS",
    "HEADER_SHAPE",
    "MAX_MODEL_HEADER_CHARACTERS",
    "MAX_MODEL_HEADER_LINES",
    "Decided",
    "DropFacts",
    "DropFields",
    "DropPlan",
    "HeaderReading",
    "Participant",
    "SOURCE_CALENDAR",
    "SOURCE_DOCX_CREATED",
    "SOURCE_FILE_NAME",
    "SOURCE_HEADER",
    "SOURCE_LAST_CUE",
    "SOURCE_LAST_TURN",
    "SOURCE_MODEL",
    "SOURCE_MODIFIED",
    "SOURCE_OPTION",
    "SOURCE_RECORD",
    "SOURCE_UNKNOWN",
    "attendee_count",
    "candidate_span",
    "decide_fields",
    "decide_plan",
    "file_name_title",
    "header_fragments",
    "header_lines",
    "linked",
    "model_header",
    "needs_model",
    "participants",
    "prepare_header_line",
    "read_header",
    "read_model_answer",
    "title_refusal",
]

T = TypeVar("T")

# ── Sources, as the closing line and `--dry-run` name them ──────────────────

SOURCE_OPTION = "option"
SOURCE_CALENDAR = "calendar"
SOURCE_RECORD = "recorded meeting"
SOURCE_HEADER = "file header"
SOURCE_MODEL = "file header, read by the local model"
SOURCE_FILE_NAME = "file name"
SOURCE_DOCX_CREATED = "docx creation time"
SOURCE_MODIFIED = "file modification time"
SOURCE_LAST_CUE = "last cue"
SOURCE_LAST_TURN = "last turn"
SOURCE_UNKNOWN = "unknown"

ESTIMATED_FROM: dict[str, str] = {
    SOURCE_DOCX_CREATED: "docx-created",
    SOURCE_HEADER: "header",
    SOURCE_MODEL: "model-header",
    SOURCE_MODIFIED: "file-modified",
}
"""An estimated start's source, as the record's `start_estimated_from` spells it."""

CREATION_TOLERANCE = timedelta(minutes=15)
"""How long after the meeting's end a `.docx` may have been created and still
date it (story 11e). Measured on one export: 5 minutes 18 seconds after the end.
Chosen, not measured: the measurement rests on one file."""

MAX_MODEL_HEADER_LINES = 20
MAX_MODEL_HEADER_CHARACTERS = 2_000
"""The most of a header the local model is shown: 20 lines and 2,000 characters,
chosen, not measured. A longer header is not sent at all."""

_YEARS = range(1900, 2100)
"""The years a start is read in: the date-like rule's own range. A start
outside it is not a meeting's, and one near the calendar's ends would overflow
the arithmetic that places it."""

_EARLIEST_OFFSET = timedelta(hours=-12)
_LATEST_OFFSET = timedelta(hours=14)


@dataclass(frozen=True, slots=True)
class Decided(Generic[T]):
    """One field's value, the source that gave it, and whether it is an estimate."""

    value: T
    source: str
    estimated: bool = False


@dataclass(frozen=True, slots=True)
class Participant:
    """A speaker's display name, as written, and the handle `aliases.md` links it to."""

    name: str
    handle: PersonHandle | None = None


# ── The title rule (stories 11c, 11e) ────────────────────────────────────────


def title_refusal(title: str) -> str | None:
    """Why `title` cannot be a meeting's title, or `None` when it can.

    One rule for a title typed as `--title` and one read from the file: not
    empty, and nothing the record's own rules refuse (`check_title`).
    """
    if not title:
        return "a title is required: it is half of what matches a meeting."
    try:
        check_title(title)
    except MalformedMeeting as refused:
        return str(refused)
    return None


def _title(candidate: str | None) -> str | None:
    """`candidate` when the title rule accepts it, else `None`."""
    if candidate is None:
        return None
    candidate = candidate.strip()
    return candidate if title_refusal(candidate) is None else None


_STAMP = re.compile(r"-\d{8}_\d{6}-Meeting Recording$")
"""Teams' recording stamp at the end of a title: `-YYYYMMDD_HHMMSS-Meeting
Recording`. Never read as the start: measured on one export, its time was three
hours off the header's, so its zone is unknown."""

_FOREIGN_STAMP = re.compile(r"-\d{8}_\d{6}-")


def _without_stamp(title: str) -> str | None:
    """The title with the recording stamp removed; `None` for a stamp in other words."""
    stripped = _STAMP.sub("", title)
    if _FOREIGN_STAMP.search(stripped):
        return None
    return stripped.strip()


def file_name_title(file_name: str) -> str:
    """The title the file name gives: its name without the extension and the stamp.

    Never empty: a name that is only a stamp keeps the name without its
    extension, and an empty one the name itself.
    """
    path = PurePath(file_name)
    stem = path.stem
    title = _STAMP.sub("", stem).strip()
    for candidate in (title, stem.strip(), path.name):
        if _title(candidate) is not None:
            return candidate
    return "Untitled transcript"


# ── The header ───────────────────────────────────────────────────────────────

_EMPHASIS = re.compile(r"^(\*\*|__|\*|_)(?P<inner>.+)\1$")
_ESCAPED = re.compile(r"\\([" + re.escape(string.punctuation) + r"])")


def prepare_header_line(line: str) -> str:
    """One header line as it is judged: spaces folded, Markdown's marks set aside."""
    folded = "".join(" " if character.isspace() else character for character in line)
    folded = " ".join(folded.split())
    folded = unmarked_line(folded)
    emphasised = _EMPHASIS.match(folded)
    if emphasised is not None:
        folded = emphasised["inner"].strip()
    return _ESCAPED.sub(r"\1", folded).strip()


def header_lines(text: str) -> tuple[str, ...]:
    """The file's header lines, prepared; `Transcript` lines and empty ones skipped."""
    prepared = (prepare_header_line(line) for line in turn_header(text))
    return tuple(line for line in prepared if line and line.casefold() != "transcript")


_MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "january", "february", "march", "april", "may", "june", "july",
            "august", "september", "october", "november", "december",
        ),
        start=1,
    )
}
_MONTH_WORDS = "|".join(
    sorted({*_MONTHS, *(name[:3] for name in _MONTHS), "sept"}, key=len, reverse=True)
)
_TIME = r"(?P<hour>\d{1,2}):(?P<minute>\d{2}) ?(?P<half>[ap]m)"
_DAY_FIRST = re.compile(
    rf"^(?P<day>\d{{1,2}}) (?P<month>[a-z]+) (?P<year>\d{{4}}), ?{_TIME}$", re.IGNORECASE
)
_MONTH_FIRST = re.compile(
    rf"^(?P<month>[a-z]+) (?P<day>\d{{1,2}}), (?P<year>\d{{4}}), ?{_TIME}$", re.IGNORECASE
)
_TIME_OF_DAY = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?!\d)")
_YEAR = r"(?<!\d)(?:19|20)\d{2}(?!\d)"
_YEAR_BY_MONTH = re.compile(
    rf"\b(?:{_MONTH_WORDS})\b\W{{0,3}}(?:\d{{1,2}}\W{{1,3}})?{_YEAR}|{_YEAR}\W{{0,3}}\b(?:{_MONTH_WORDS})\b",
    re.IGNORECASE,
)
_YEAR_IN_NUMBERS = re.compile(rf"\d[-/.]{_YEAR}|{_YEAR}[-/.]\d")
_LENGTH_LIKE = re.compile(r"^(?:\d+[hms] ?)+$|^\d+:\d{2}:\d{2}$", re.IGNORECASE)
_LENGTH_FORMS = re.compile(
    r"^(?:(?P<hours>\d+)h ?(?P<hour_minutes>\d{2})m(?: ?(?P<hour_seconds>\d{2})s)?"
    r"|(?P<minutes>\d+)m(?: ?(?P<seconds>\d{2})s)?)$",
    re.IGNORECASE,
)


def _is_length_like(line: str) -> bool:
    return bool(_LENGTH_LIKE.match(line))


def _is_date_like(line: str) -> bool:
    """A time of day, or a year beside a month name or joined to other numbers.

    A length-like line (`1:02:03`) is never also date-like, so a header with a
    three-field length is not read as holding two start lines.
    """
    if _is_length_like(line):
        return False
    return bool(
        _TIME_OF_DAY.search(line) or _YEAR_BY_MONTH.search(line) or _YEAR_IN_NUMBERS.search(line)
    )


def _start_of(line: str) -> datetime | None:
    """A start line's date and time, as a wall clock in no zone; `None` for any other line."""
    found = _DAY_FIRST.match(line) or _MONTH_FIRST.match(line)
    if found is None:
        return None
    month = _MONTHS.get(found["month"].casefold())
    hour, minute = int(found["hour"]), int(found["minute"])
    if month is None or not 1 <= hour <= 12 or minute > 59 or int(found["year"]) not in _YEARS:
        return None
    if found["half"].casefold() == "am":
        hour = 0 if hour == 12 else hour
    else:
        hour = 12 if hour == 12 else hour + 12
    try:
        return datetime(int(found["year"]), month, int(found["day"]), hour, minute)
    except ValueError:
        return None


def _length_of(line: str) -> int | None:
    """A length line's minutes, any seconds rounding up; `None` for any other or out of range."""
    found = _LENGTH_FORMS.match(line)
    if found is None:
        return None
    if found["hours"] is not None:
        hours, minutes = int(found["hours"]), int(found["hour_minutes"])
        seconds = int(found["hour_seconds"] or 0)
        if minutes > 59:
            return None
    else:
        hours, minutes, seconds = 0, int(found["minutes"]), int(found["seconds"] or 0)
    if seconds > 59:
        return None
    total = math.ceil((hours * 3600 + minutes * 60 + seconds) / 60)
    return total if MIN_DROP_MINUTES <= total <= MAX_DROP_MINUTES else None


@dataclass(frozen=True, slots=True)
class HeaderReading:
    """What a header gave: a title, a start as a wall clock in no zone, a length in minutes."""

    title: str | None = None
    start: datetime | None = None
    length: int | None = None

    @property
    def complete(self) -> bool:
        return None not in (self.title, self.start, self.length)


def read_header(lines: Sequence[str]) -> HeaderReading:
    """The fixed readers over prepared header lines.

    The start only when exactly one line is date-like and it is a start form;
    the length only when exactly one line is length-like and it is a length
    form; the title from the first line only, when it is neither, with the
    recording stamp removed and the title rule passed.
    """
    if not lines:
        return HeaderReading()
    dated = [line for line in lines if _is_date_like(line)]
    lengths = [line for line in lines if _is_length_like(line)]
    start = _start_of(dated[0]) if len(dated) == 1 else None
    length = _length_of(lengths[0]) if len(lengths) == 1 else None
    first = lines[0]
    title = None
    if not _is_date_like(first) and not _is_length_like(first):
        stripped = _without_stamp(first)
        title = None if stripped is None else _title(stripped)
    return HeaderReading(title=title, start=start, length=length)


def model_header(lines: Sequence[str]) -> tuple[str, ...] | None:
    """The header lines the local model may be shown, or `None` when the header is too long."""
    if not lines or len(lines) > MAX_MODEL_HEADER_LINES:
        return None
    if sum(len(line) for line in lines) > MAX_MODEL_HEADER_CHARACTERS:
        return None
    return tuple(lines)


def header_fragments(lines: Sequence[str]) -> tuple[Sanitized, ...]:
    """The header call's fragments: each prepared header line through `sanitize`.

    The same step transcript text goes through (AD-12); only header lines,
    never the body.
    """
    return tuple(sanitize(line) for line in lines)


HEADER_SHAPE: dict[str, object] = {
    "type": "object",
    "properties": {
        "title": {"type": ["string", "null"]},
        "start": {"type": ["string", "null"]},
        "length_minutes": {"type": ["integer", "null"]},
    },
    "required": ["title", "start", "length_minutes"],
    "additionalProperties": False,
}
"""The answer's shape, sent with the call; pm-ai validates the answer itself."""

HEADER_INSTRUCTIONS = (
    "The lines given are the header of a meeting transcript: the lines before "
    "the first thing anybody said. Read three things from them, and answer "
    "with one JSON object and nothing else.\n"
    '- "title": the meeting\'s title, copied exactly as it is written in one '
    "of the lines, without any recording stamp or date; null when no line "
    "holds one.\n"
    '- "start": the date and time the meeting started, as YYYY-MM-DDTHH:MM in '
    "24-hour time, exactly as the line gives it, with no time zone; null when "
    "no line gives both a date and a time.\n"
    '- "length_minutes": how many minutes the meeting lasted, as a whole number, '
    "rounding any seconds up; null when no line gives it.\n"
    "Never guess a value no line gives."
)
"""Prose pm-ai wrote about itself: the call's instructions (AD-12)."""

_MODEL_START = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")


def read_model_answer(answer: str, lines: Sequence[str]) -> HeaderReading:
    """The local model's answer, validated field by field; never a refusal.

    A field that fails is unread and the others are used. The title must
    appear, as written, inside one prepared header line, then pass the stamp
    rule, the title rule and the fixed readers' rule that a date-like or
    length-like text is never the title; the start must be `YYYY-MM-DDTHH:MM`,
    a real date and time with no offset, in 1900 to 2099; the length a whole
    number from 1 to 1,440.
    """
    try:
        found = json.loads(answer)
    except (ValueError, RecursionError):
        return HeaderReading()
    if not isinstance(found, dict):
        return HeaderReading()
    title = found.get("title")
    accepted_title: str | None = None
    if isinstance(title, str) and title.strip() and any(title in line for line in lines):
        stripped = _without_stamp(title.strip())
        # The fixed readers' title rule: a date or a length is never the title.
        if stripped is not None and not (_is_date_like(stripped) or _is_length_like(stripped)):
            accepted_title = _title(stripped)
    start = found.get("start")
    accepted_start: datetime | None = None
    if isinstance(start, str) and _MODEL_START.match(start):
        try:
            accepted_start = datetime.fromisoformat(start)
        except ValueError:
            accepted_start = None
        if accepted_start is not None and accepted_start.year not in _YEARS:
            accepted_start = None
    length = found.get("length_minutes")
    accepted_length = (
        length
        if isinstance(length, int)
        and not isinstance(length, bool)
        and MIN_DROP_MINUTES <= length <= MAX_DROP_MINUTES
        else None
    )
    return HeaderReading(title=accepted_title, start=accepted_start, length=accepted_length)


# ── Participants ─────────────────────────────────────────────────────────────


def participants(utterances: Sequence[Utterance]) -> tuple[str, ...]:
    """The speakers' display names, once each by `normalise()`'s key, in order of first turn.

    Two spellings with one key are one participant, kept as first written.
    An utterance with no speaker adds none.
    """
    seen: set[str] = set()
    names: list[str] = []
    for utterance in utterances:
        name = utterance.speaker_handle.strip()
        key = normalise(name)
        if not key or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return tuple(names)


def linked(
    names: Sequence[str], aliases: AliasTable, meeting_id: str | None
) -> tuple[Participant, ...]:
    """Each participant with the handle `aliases.md` saves its name as, exactly; else none."""
    return tuple(Participant(name, aliases.handle_for(name, meeting_id)) for name in names)


# ── The fields ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DropFacts:
    """Everything a drop's fields are decided from, read by the caller.

    `modified` and `created` are aware UTC (`created` only for a `.docx`
    with a `dcterms:created` in UTC); `zone` is `display_timezone`, `None`
    when unset; the options are as given, `start` aware.
    """

    text: str
    file_name: str
    modified: datetime
    from_docx: bool = False
    created: datetime | None = None
    zone: tzinfo | None = None
    title: str | None = None
    start: datetime | None = None
    duration: int | None = None


@dataclass(frozen=True, slots=True)
class DropFields:
    """A drop's title, length and estimated start, decided before the records are read.

    `start` is the option or the first estimate (steps 3 to 5); `hint_day` the
    day a calendar record must fall on to be the meeting.
    """

    title: Decided[str]
    length: Decided[int | None]
    start: Decided[datetime]
    hint_day: date
    header_start: Decided[datetime] | None = None


def needs_model(facts: DropFacts, lines: Sequence[str], fixed: HeaderReading) -> bool:
    """Whether the header goes to the local model: it has one, and the fixed
    readers left a field unread that no option gives."""
    if not lines:
        return False
    return (
        (fixed.title is None and facts.title is None)
        or (fixed.start is None and facts.start is None)
        or (fixed.length is None and facts.duration is None)
    )


def decide_fields(
    facts: DropFacts, fixed: HeaderReading, model: HeaderReading | None = None
) -> DropFields:
    """The title, the length and the start before the calendar step, each with its source."""
    model = model or HeaderReading()
    title = _decide_title(facts, fixed, model)
    length = _decide_length(facts, fixed, model)
    header_start: Decided[datetime] | None = None
    if fixed.start is not None:
        header_start = Decided(fixed.start, SOURCE_HEADER, estimated=True)
    elif model.start is not None:
        header_start = Decided(model.start, SOURCE_MODEL, estimated=True)
    if facts.start is not None:
        start = Decided(_utc(facts.start), SOURCE_OPTION)
        return DropFields(title, length, start, start.value.date(), header_start)
    start = _estimate_start(facts, length.value, header_start)
    if header_start is not None:
        hint = header_start.value.date()
    else:
        hint = start.value.astimezone(facts.zone or timezone.utc).date()
    return DropFields(title, length, start, hint, header_start)


def _decide_title(facts: DropFacts, fixed: HeaderReading, model: HeaderReading) -> Decided[str]:
    if facts.title is not None:
        return Decided(facts.title, SOURCE_OPTION)
    if fixed.title is not None:
        return Decided(fixed.title, SOURCE_HEADER)
    if model.title is not None:
        return Decided(model.title, SOURCE_MODEL)
    return Decided(file_name_title(facts.file_name), SOURCE_FILE_NAME)


def _in_range(minutes: int | None) -> int | None:
    if minutes is None or not MIN_DROP_MINUTES <= minutes <= MAX_DROP_MINUTES:
        return None
    return minutes


def _decide_length(
    facts: DropFacts, fixed: HeaderReading, model: HeaderReading
) -> Decided[int | None]:
    if facts.duration is not None:
        return Decided(facts.duration, SOURCE_OPTION)
    if fixed.length is not None:
        return Decided(fixed.length, SOURCE_HEADER)
    cue = carried_minutes(facts.file_name, facts.text, from_docx=facts.from_docx)
    if cue is not None:
        return Decided(cue, SOURCE_LAST_CUE)
    if model.length is not None:
        return Decided(model.length, SOURCE_MODEL)
    seconds = last_turn_seconds(facts.text)
    turn = None if seconds is None else _in_range(math.ceil(seconds / 60))
    if turn is not None:
        # The last turn's start misses the last turn itself: an estimate.
        return Decided(turn, SOURCE_LAST_TURN, estimated=True)
    return Decided(None, SOURCE_UNKNOWN)


def _minute(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc)


def _created_is_consistent(
    created: datetime, length: int, header: datetime | None
) -> bool:
    """Whether the creation time dates the meeting the header names.

    With no header date and time, nothing contradicts it. Otherwise it must be
    at or after the end that date and time give under some real UTC offset
    (−12:00 to +14:00), and no more than `CREATION_TOLERANCE` after it.
    """
    if header is None:
        return True
    # The header's wall clock read under every real offset spans these starts;
    # the creation time allows the starts between `low` and `high`.
    earliest = header.replace(tzinfo=timezone.utc) - _LATEST_OFFSET
    latest = header.replace(tzinfo=timezone.utc) - _EARLIEST_OFFSET
    low = created - CREATION_TOLERANCE - timedelta(minutes=length)
    high = created - timedelta(minutes=length)
    return low <= latest and earliest <= high


def _estimate_start(
    facts: DropFacts, length: int | None, header: Decided[datetime] | None
) -> Decided[datetime]:
    """Steps 3, 4 and 5: the creation time, the header in the zone, the modification time."""
    created = facts.created
    if (
        created is not None
        and length is not None
        and _created_is_consistent(created, length, None if header is None else header.value)
    ):
        return Decided(
            _minute(_utc(created) - timedelta(minutes=length)), SOURCE_DOCX_CREATED, estimated=True
        )
    if header is not None and facts.zone is not None:
        # fold=0: a wall clock that occurs twice takes the earlier instant, and
        # one that does not occur takes the offset before the change.
        local = header.value.replace(tzinfo=facts.zone, fold=0)
        return Decided(_utc(local), header.source, estimated=True)
    modified = _utc(facts.modified)
    if length is not None:
        modified -= timedelta(minutes=length)
    return Decided(_minute(modified), SOURCE_MODIFIED, estimated=True)


def candidate_span(fields: DropFields) -> tuple[datetime, datetime]:
    """The span of starts whose records may be the meeting, for the caller to read.

    With `--start`, `MATCH_TOLERANCE` either side (`11b`'s rule); otherwise the
    hint day with a day either side, which holds every record falling on it
    in UTC or in any zone.
    """
    if fields.start.source == SOURCE_OPTION:
        tolerance = timedelta(minutes=tolerance_minutes())
        return fields.start.value - tolerance, fields.start.value + tolerance + timedelta(
            microseconds=1
        )
    begin = datetime.combine(fields.hint_day, datetime.min.time(), tzinfo=timezone.utc)
    return begin - timedelta(days=1), begin + timedelta(days=2)


# ── The plan ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DropPlan:
    """What a drop without `--meeting` would store, decided from its fields and the records.

    `outcome` is `join`, `new` or `refused`. A join carries the record joined
    (`joined`); a new record carries the ids of two or more same-day
    candidates when that is why (`candidates`); a refusal its sentence
    (`refusal`), for two records matching `--start`.
    """

    title: Decided[str]
    start: Decided[datetime]
    length: Decided[int | None]
    participants: tuple[Participant, ...]
    outcome: str
    joined: Meeting | None = None
    candidates: tuple[str, ...] = ()
    refusal: str = ""
    ambiguous: tuple[str, ...] = ()
    found_by: str = ""
    """How a join was found: `start` (`--start`, within 15 minutes) or `same day`."""
    minted_same_day: tuple[Meeting, ...] = ()
    """Records pm-ai minted with the same title on the hint day: never joined, but
    one that already has a capture means this file was dropped before (the
    caller refuses it by the capture rule, as before story 11e)."""


def decide_plan(
    fields: DropFields,
    records: Sequence[Meeting],
    *,
    names: Sequence[str],
    aliases: AliasTable,
    zone: tzinfo | None,
    scope_name: str,
) -> DropPlan:
    """The outcome and the fields as they would be stored.

    With `--start`, `11b`'s rule: one record in `MATCH_TOLERANCE` with the
    same title joins, two refuse naming each. Without it, step 2: one
    calendar-sourced record (never one pm-ai minted) whose title is the
    same after `normalised_title` and that falls on the hint day joins; two
    or more join neither and a new record is made, naming them. A joined
    record's title, start and length are its own.
    """
    if fields.start.source == SOURCE_OPTION:
        found = sorted(
            (
                meeting
                for meeting in records
                if is_candidate(meeting, title=fields.title.value, start=fields.start.value)
            ),
            key=lambda meeting: (meeting.start, meeting.meeting_id),
        )
        if len(found) > 1:
            ids = tuple(meeting.meeting_id for meeting in found)
            return DropPlan(
                fields.title,
                fields.start,
                fields.length,
                linked(names, aliases, None),
                "refused",
                refusal=(
                    f"{len(ids)} meetings in {scope_name} match "
                    f"{fields.title.value!r} within {tolerance_minutes()} minutes of "
                    f"{fields.start.value.isoformat()}: {', '.join(ids)}. Name the one "
                    f"this transcript belongs to."
                ),
                ambiguous=ids,
            )
        if found:
            return _joined(found[0], names, aliases, found_by="start")
        return DropPlan(
            fields.title, fields.start, fields.length, linked(names, aliases, None), "new"
        )
    wanted = normalised_title(fields.title.value)
    same_day = sorted(
        (
            meeting
            for meeting in records
            if meeting.calendar_event_ref is not None
            and not meeting.manual_only
            and wanted
            and normalised_title(meeting.title) == wanted
            and falls_on_day(meeting.start, fields.hint_day, zone)
        ),
        key=lambda meeting: (meeting.start, meeting.meeting_id),
    )
    if len(same_day) == 1:
        return _joined(same_day[0], names, aliases, found_by="same day")
    minted = tuple(
        meeting
        for meeting in records
        if meeting.manual_only
        and wanted
        and normalised_title(meeting.title) == wanted
        and falls_on_day(meeting.start, fields.hint_day, zone)
    )
    return DropPlan(
        fields.title,
        fields.start,
        fields.length,
        linked(names, aliases, None),
        "new",
        candidates=tuple(meeting.meeting_id for meeting in same_day),
        minted_same_day=minted,
    )


def _joined(
    meeting: Meeting, names: Sequence[str], aliases: AliasTable, *, found_by: str
) -> DropPlan:
    source = SOURCE_CALENDAR if meeting.calendar_event_ref is not None else SOURCE_RECORD
    return DropPlan(
        Decided(meeting.title, source),
        Decided(meeting.start, source),
        Decided(meeting.duration_minutes, source),
        linked(names, aliases, meeting.meeting_id),
        "join",
        joined=meeting,
        found_by=found_by,
    )


def attendee_count(plan: DropPlan, attendees: Sequence[str]) -> int:
    """A new record's count: its participants, plus each `--attendees` handle no
    participant's name is linked to."""
    linked_handles = {str(participant.handle) for participant in plan.participants if participant.handle}
    return len(plan.participants) + sum(
        1 for handle in attendees if _canonical(handle) not in linked_handles
    )


def _canonical(handle: str) -> str:
    """A handle as the producer writes it (`email:Ana@X.com` is `email:ana@x.com`)."""
    system, _, value = handle.partition(":")
    try:
        return str(person_handle(system, value))
    except (InvalidPersonHandle, ValueError):
        return handle
