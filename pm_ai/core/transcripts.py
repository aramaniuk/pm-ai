"""Reading a transcript and binding it to a meeting (story 11b, AD-23, AD-33).

Three things, all I/O-free:

- **The source catalogue.** `registered_adapters()` and `get_adapter(name)` over
  a registry the composition root fills when it is imported (`pm_ai.app.wiring`)
  — `core` imports no adapter (AD-30). It holds the adapter *kinds* by name
  (`"graph"`, `"manual"`), each a class satisfying `TranscriptSourcePort`; the
  instances a daemon acquires through are `Daemon.transcripts`.
- **The readers.** A capture's text becomes utterances through one of three
  readers, chosen by content and never by extension: WebVTT — Graph's format
  and Microsoft's unattributed one, which has no header — then speaker-and-time
  turns, the layout of a Teams transcript downloaded as `.docx` (story 11d),
  then plain `Speaker: words` text. A file none accepts is
  `UnreadableTranscript`, naming it. This is this slice's own contract; the
  acquisition port is `33e`'s and yields captures, not utterances.
- **The bind step.** A transcript is never ingested without a meeting (AD-23).
  `bind_meeting` follows the one-record-per-meeting rule (D3, AD-33): a named
  id wins and must exist in the scope; otherwise a title and a start match at
  most one record within `MATCH_TOLERANCE`, and none mints a manual-only record.

No reader gives an utterance a person handle. A WebVTT voice tag, a turn's
name and a `Speaker:` label are display names, never accounts (AD-34),
so `Utterance.speaker` stays `None` and every spoken command read here stages.
"""

from __future__ import annotations

import html
import math
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePath

from pm_ai.core.meeting_records import (
    MalformedMeeting,
    capture_name,
    MeetingNotFound,
    MeetingRecord,
    MeetingRecords,
)
from pm_ai.core.sanitize import sanitize
from pm_ai.domain.identity import Actor, DataScope
from pm_ai.domain.meetings import AmbiguousMeeting, Meeting, tolerance_minutes
from pm_ai.domain.transcripts import (
    CaptureFormat,
    CaptureMissing,
    PartState,
    TranscriptPart,
    NothingSaid,
    Transcript,
    TranscriptSource,
    UnboundTranscript,
    UnreadableTranscript,
    Utterance,
)
from pm_ai.ports import TranscriptSourcePort

__all__ = [
    "AmbiguousMeeting",
    "MeetingDetails",
    "UnboundTranscript",
    "UnknownTranscriptSource",
    "UnreadableTranscript",
    "bind_candidates",
    "bind_meeting",
    "carried_minutes",
    "drop_capture_format",
    "drop_utterances",
    "failure_reason",
    "get_adapter",
    "has_turn_line",
    "ingest",
    "MAX_DROP_MINUTES",
    "MIN_DROP_MINUTES",
    "is_webvtt",
    "last_cue_end_minutes",
    "meeting_utterances",
    "mint_meeting_id",
    "read_transcript",
    "register_adapter",
    "registered_adapters",
]


# ── The source catalogue ─────────────────────────────────────────────────────


class UnknownTranscriptSource(LookupError):
    """No transcript source kind is registered under that name."""


_ADAPTERS: dict[str, type[TranscriptSourcePort]] = {}


def register_adapter(adapter: type[TranscriptSourcePort]) -> None:
    """Add a source kind under its `name`. Called by the composition root.

    Registering the same class twice is harmless; a second class under a name
    already taken is refused, so one name cannot quietly come to mean two
    sources.
    """
    name = adapter.name
    held = _ADAPTERS.get(name)
    if held is not None and held is not adapter:
        raise ValueError(
            f"transcript source {name!r} is already registered as "
            f"{held.__name__}; {adapter.__name__} cannot take the name too."
        )
    _ADAPTERS[name] = adapter


def registered_adapters() -> tuple[type[TranscriptSourcePort], ...]:
    """Every registered source kind, ordered by name (AD-23)."""
    return tuple(_ADAPTERS[name] for name in sorted(_ADAPTERS))


def get_adapter(name: str) -> type[TranscriptSourcePort]:
    """The source kind registered under `name`, or `UnknownTranscriptSource`."""
    try:
        return _ADAPTERS[name]
    except KeyError:
        raise UnknownTranscriptSource(
            f"no transcript source is registered as {name!r}; registered: "
            f"{sorted(_ADAPTERS)}."
        ) from None


# ── The readers ──────────────────────────────────────────────────────────────

_TIMESTAMP = r"\d+(?::\d+){1,2}(?:\.\d+)?"
_TIMING = re.compile(rf"^(?P<start>{_TIMESTAMP})[ \t]+-->[ \t]+(?P<end>{_TIMESTAMP})(?:[ \t].*)?$")
"""A cue timing line: start, arrow, end, then optional cue settings (skipped).

Looser than the WebVTT grammar on purpose: Microsoft's unattributed format
writes `0:0:4.120 --> 0:0:9.0`, with neither padded fields nor three fraction
digits, and it is one of the two shapes Graph hands back.
"""

_VOICE = re.compile(r"<v(?:\.[^\s>]*)?(?:[ \t]+(?P<name>[^>]*))?>", re.IGNORECASE)
"""A voice span's opening tag, `<v Speaker>` or `<v.class Speaker>`."""

_TAG = re.compile(r"<[^>]*>")
_SKIPPED_BLOCKS = ("NOTE", "STYLE", "REGION")


def read_transcript(text: str, *, name: str) -> tuple[Utterance, ...]:
    """A transcript's utterances, by whichever reader its content selects.

    In order: WebVTT when the first non-blank line is `WEBVTT` or a cue timing
    line; otherwise speaker-and-time turns when at least two lines are
    speaker-and-time lines and they are at least a quarter of the non-blank
    lines from the first of them on (story 11d); otherwise `Speaker: words`
    text. `UnreadableTranscript`, naming `name`, for a WebVTT file with no cue
    in it, and for text no reader accepts. An empty tuple is a file a reader
    accepted that says nothing: cues or turns that are all empty.
    """
    lines = text.removeprefix("\ufeff").splitlines()
    first = next((line.strip() for line in lines if line.strip()), "")
    if _is_header(first) or _TIMING.match(first):
        cues = _read_vtt(lines)
        if cues is None:
            raise UnreadableTranscript(
                f"{name} is a WebVTT file with no cue in it, so there is nothing "
                f"said to read."
            )
        return cues
    turns = _read_turns(lines)
    if turns is not None:
        return turns
    utterances = _read_text(lines)
    if utterances is None:
        raise UnreadableTranscript(
            f"{name} is not a transcript pm-ai can read: it has no WEBVTT header, "
            f"no cue timing line, no lines of a speaker's name and a time, and "
            f"most of its lines are not `Speaker: words`."
        )
    return utterances


MIN_DROP_MINUTES = 1
MAX_DROP_MINUTES = 1440
"""The duration a dropped transcript's new record may have, inclusive: at least
a minute, at most a day (story 11c). A typed duration outside it is asked
again; a last cue outside it is no measure of the meeting, so it is asked for."""


def is_webvtt(text: str) -> bool:
    """Whether `read_transcript` would read `text` as WebVTT (story 11c).

    The same choice it makes, by content: the first non-blank line is a
    `WEBVTT` header or a cue timing line.
    """
    lines = text.removeprefix("\ufeff").splitlines()
    first = next((line.strip() for line in lines if line.strip()), "")
    return _is_header(first) or bool(_TIMING.match(first))


def last_cue_end_minutes(text: str) -> int | None:
    """The latest end among a WebVTT transcript's cues, in whole minutes rounded up.

    How long a dropped `.vtt` says its meeting ran (story 11c): the file
    carries no duration, and the last thing said is the nearest it has. Only
    real cue timing lines count — the one opening a cue, found as `_read_vtt`
    finds it — never a line inside a `NOTE`, `STYLE` or `REGION` block or a
    cue's payload. `None` for text `read_transcript` would not read as
    WebVTT, or with no cue. Rounded up, so 30 minutes and 4 seconds is 31.
    """
    if not is_webvtt(text):
        return None
    ends: list[float] = []
    for block in _blocks(text.removeprefix("\ufeff").splitlines()):
        head = block[0].strip()
        if _is_header(head):
            timed = next((i for i, line in enumerate(block) if _TIMING.match(line.strip())), None)
            if timed is None:
                continue
            block = block[timed:]
            head = block[0].strip()
        elif any(_is_block(head, keyword) for keyword in _SKIPPED_BLOCKS):
            continue
        at = 0 if _TIMING.match(head) else 1
        if at >= len(block):
            continue
        timing = _TIMING.match(block[at].strip())
        if timing is not None:
            ends.append(_exact_seconds(timing["end"]))
    if not ends:
        return None
    return math.ceil(max(ends) / 60)


def _exact_seconds(stamp: str) -> float:
    parts = stamp.split(":")
    hours = int(parts[0]) if len(parts) == 3 else 0
    return hours * 3600 + int(parts[-2]) * 60 + float(parts[-1])


def _is_header(line: str) -> bool:
    return line == "WEBVTT" or line.startswith(("WEBVTT ", "WEBVTT\t"))


def _is_block(line: str, keyword: str) -> bool:
    return line == keyword or line.startswith((f"{keyword} ", f"{keyword}\t"))


def _blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line.rstrip())
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _read_vtt(lines: list[str]) -> tuple[Utterance, ...] | None:
    """WebVTT cues as utterances: one per voice span, offset from the cue start.

    `None` when the file holds no cue at all. A cue written straight after the
    header, with no blank line between, is still a cue.
    """
    utterances: list[Utterance] = []
    cues = 0
    for block in _blocks(lines):
        head = block[0].strip()
        if _is_header(head):
            # A cue the header block runs into: start at its timing line.
            timed = next((i for i, line in enumerate(block) if _TIMING.match(line.strip())), None)
            if timed is None:
                continue
            block = block[timed:]
            head = block[0].strip()
        elif any(_is_block(head, keyword) for keyword in _SKIPPED_BLOCKS):
            continue
        # A cue identifier, when there is one, is the line before the timing.
        at = 0 if _TIMING.match(head) else 1
        if at >= len(block):
            continue
        timing = _TIMING.match(block[at].strip())
        if timing is None:
            continue
        cues += 1
        offset = _seconds(timing["start"])
        for speaker, words in _spans(" ".join(block[at + 1 :])):
            utterances.append(Utterance(speaker, words, offset))
    return tuple(utterances) if cues else None


def _seconds(stamp: str) -> int:
    parts = stamp.split(":")
    hours = int(parts[0]) if len(parts) == 3 else 0
    minutes, seconds = int(parts[-2]), float(parts[-1])
    return int(hours * 3600 + minutes * 60 + seconds)


def _spans(cue: str) -> list[tuple[str, str]]:
    """`(speaker, words)` per voice span; one unattributed span when there is none."""
    voices = list(_VOICE.finditer(cue))
    if not voices:
        words = _clean(cue)
        return [("", words)] if words else []
    spans: list[tuple[str, str]] = []
    leading = _clean(cue[: voices[0].start()])
    if leading:
        spans.append(("", leading))
    for index, voice in enumerate(voices):
        end = voices[index + 1].start() if index + 1 < len(voices) else len(cue)
        words = _clean(cue[voice.end() : end])
        if words:
            spans.append((html.unescape(voice["name"] or "").strip(), words))
    return spans


def _clean(fragment: str) -> str:
    """Cue text without markup: tags dropped, entities decoded, spaces collapsed."""
    return " ".join(html.unescape(_TAG.sub("", fragment)).split())


_LABEL = re.compile(r"[^\W\d_][\w .'’@+-]{0,79}")
"""What a speaker label looks like: starts with a letter, at most 80 characters
of letters, digits, spaces, name punctuation and `@` (a label is often an
address)."""


def _label(line: str) -> tuple[str, str] | None:
    """`(speaker, words)` when `line` is `Speaker: words`, else `None`.

    Not a label: a URL scheme (`https://…`), a time (`10:30`), or anything
    that does not start with a letter.
    """
    speaker, colon, words = line.partition(":")
    speaker, words = speaker.strip(), words.strip()
    if not colon or not words or not _LABEL.fullmatch(speaker):
        return None
    if words.startswith("//"):
        return None
    return speaker, words


def _read_text(lines: list[str]) -> tuple[Utterance, ...] | None:
    """`Speaker: words` lines, or `None` when most lines carry no speaker label.

    More than half the non-blank lines must be labelled; a line without a
    label is kept with an empty speaker and can only stage. Offsets are thirty
    seconds apart, as before: the text has no clock.
    """
    utterances: list[Utterance] = []
    labelled = 0
    for index, line in enumerate(line for line in lines if line.strip()):
        found = _label(line)
        if found is not None:
            labelled += 1
            utterances.append(Utterance(found[0], found[1], index * 30))
        else:
            utterances.append(Utterance("", line.strip(), index * 30))
    return tuple(utterances) if labelled * 2 > len(utterances) else None


# ── Speaker-and-time turns (story 11d) ──────────────────────────────────────

_TURN_NAME = re.compile(r"[^\W\d_](?:[^\W\d_]|[ .'’-])*")
"""A turn's name: starts with a letter; letters, spaces, apostrophes, hyphens
and full stops after it — no digit, colon or comma."""

_TURN_NAME_LIMIT = 80

_TURN_TIME = r"\d+:[0-5]\d(?::[0-5]\d)?"
_TURN_TOKEN = re.compile(
    rf"[*_]*(?:\[[*_]*(?P<linked>{_TURN_TIME})[*_]*\]\([^)\s]*\)|(?P<plain>{_TURN_TIME}))[*_]*"
)
"""The time, the line's last word — emphasised, or written as a link (`[3:07](…)`
reads as `3:07`). Matched against that word alone, after the line is split at
its last run of whitespace, so no pattern ever scans the name: a line of any
length is judged in time proportional to it."""

_QUOTED = re.compile(r"^(?:>[ \t]?)+")
_HEADING = re.compile(r"^#{1,6}[ \t]+")
_LISTED = re.compile(r"^(?:[-*+]|\d{1,9}[.)])[ \t]+")


def _unmarked(line: str) -> str:
    """`line` with Markdown's marks set aside, as a speaker-and-time line is judged.

    A quotation's `>`s, a heading's `#`s, a list marker, and a trailing
    backslash or two spaces (Markdown's hard line break). Emphasis around the
    name or the time is set aside by `_turn`.
    """
    line = _QUOTED.sub("", line.strip()).strip()
    line = _HEADING.sub("", line)
    line = _LISTED.sub("", line)
    return line.rstrip().removesuffix("\\").rstrip()


def _turn(line: str) -> tuple[str, int] | None:
    """`(name, offset in seconds)` when `line` is a speaker-and-time line, else `None`.

    A time of two fields is minutes and seconds (`3:07` is 187), of three
    hours, minutes and seconds (`1:03:07` is 3787).
    """
    words = _unmarked(line).rsplit(None, 1)
    if len(words) != 2:
        return None
    head, last = words
    name = head.strip().strip("*_").strip()
    if len(name) > _TURN_NAME_LIMIT or not _TURN_NAME.fullmatch(name):
        return None
    found = _TURN_TOKEN.fullmatch(last)
    if found is None:
        return None
    fields = [int(part) for part in (found["linked"] or found["plain"]).split(":")]
    hours, minutes, seconds = ([0] * (3 - len(fields)) + fields)
    return name, hours * 3600 + minutes * 60 + seconds


def has_turn_line(text: str) -> bool:
    """Whether any line of `text` is a speaker-and-time line (story 11d)."""
    return any(_turn(line) is not None for line in text.splitlines() if line.strip())


def _read_turns(lines: list[str]) -> tuple[Utterance, ...] | None:
    """Speaker-and-time turns as utterances, or `None` when the text is not in turns.

    Chosen when at least two non-blank lines are speaker-and-time lines and
    they are at least a quarter of the non-blank lines from the first of them
    on. Lines before the first turn are the header and are not utterances. A
    turn is its speaker-and-time line and the non-blank lines after it, up to
    the next one; each of those lines is an utterance, spoken by the turn's
    name at the turn's time, with its words as written. A turn with no words
    adds nothing; times out of order are kept as read.
    """
    present = [line for line in lines if line.strip()]
    judged = [_turn(line) for line in present]
    first = next((index for index, turn in enumerate(judged) if turn is not None), None)
    if first is None:
        return None
    count = sum(turn is not None for turn in judged)
    if count < 2 or count * 4 < len(present) - first:
        return None
    utterances: list[Utterance] = []
    speaker, offset = "", 0
    for line, turn in zip(present[first:], judged[first:], strict=True):
        if turn is not None:
            speaker, offset = turn
            continue
        utterances.append(Utterance(speaker, line.strip(), offset))
    return tuple(utterances)


# ── A dropped file (stories 11c, 11d) ────────────────────────────────────────


def drop_utterances(text: str, *, name: str, from_docx: bool = False) -> tuple[Utterance, ...]:
    """A dropped file's utterances, or the refusal saying why there are none.

    `NothingSaid` for whitespace only, for a file a reader accepted that says
    nothing (cues or turns all empty), and for a `.docx` whose text holds no
    speaker-and-time line and that `Speaker: words` does not accept — a
    document holding only its header. `UnreadableTranscript` for any other
    text no reader accepts. Nothing is written by either.
    """
    if not text.strip():
        raise NothingSaid(
            f"{name} is empty or holds only whitespace, so there is nothing said "
            f"in it to read. Nothing was written."
        )
    try:
        utterances = read_transcript(text, name=name)
    except UnreadableTranscript:
        if from_docx and not has_turn_line(text):
            raise NothingSaid(
                f"{name} is a Word document whose text holds no turn — no line "
                f"with a speaker's name and a time — so there is nothing said in "
                f"it to read. Nothing was written."
            ) from None
        raise
    if not utterances:
        what = "cue timings but none of its cues" if is_webvtt(text) else "turns but none of them"
        raise NothingSaid(
            f"{name} has {what} says anything, so there is nothing to extract "
            f"from it. Nothing was written."
        )
    return utterances


def drop_capture_format(file_name: str, text: str, *, from_docx: bool = False) -> CaptureFormat:
    """`-manual.vtt` or `-manual.txt`: the extension's word, else the content's.

    `.vtt` and `.txt` by their extension; a `.docx`'s text is always stored as
    text (story 11d); anything else, `.md` included, by whether its content is
    WebVTT.
    """
    suffix = PurePath(file_name).suffix.lower()
    if suffix == ".vtt":
        return CaptureFormat.MANUAL_VTT
    if suffix == ".txt" or from_docx:
        return CaptureFormat.MANUAL_TXT
    return CaptureFormat.MANUAL_VTT if is_webvtt(text) else CaptureFormat.MANUAL_TXT


def carried_minutes(file_name: str, text: str, *, from_docx: bool = False) -> int | None:
    """How long a dropped file says its meeting ran, or `None` when it must be asked.

    The last cue's end, for a file stored as WebVTT whose last cue ends within
    `MIN_DROP_MINUTES` to `MAX_DROP_MINUTES` (story 11c). Asked of the same
    rule at the prompt and in the daemon, so the prompt asks for the duration
    exactly when the drop would.
    """
    if drop_capture_format(file_name, text, from_docx=from_docx) is not CaptureFormat.MANUAL_VTT:
        return None
    minutes = last_cue_end_minutes(text)
    if minutes is None or not MIN_DROP_MINUTES <= minutes <= MAX_DROP_MINUTES:
        return None
    return minutes


def ingest(
    payload: str,
    meeting: Meeting | None,
    *,
    source: TranscriptSource = TranscriptSource.MANUAL,
    name: str = "the transcript",
    part: int = 1,
) -> Transcript:
    """A capture's text read into a transcript bound to `meeting`.

    `UnboundTranscript` with no meeting, before the text is read (AD-23): an
    unattributed file must not mint attributed provenance. `source` defaults to
    `MANUAL`, the one that never executes (AD-32). `part` is which part of the
    meeting's transcript the text is (story 33g); offsets are within it.
    """
    if meeting is None:
        raise UnboundTranscript(
            f"{name} has no meeting to bind to. A transcript is ingested only "
            f"against a meeting — named, matched by title and start, or minted "
            f"from them (AD-23)."
        )
    return Transcript(
        meeting_id=meeting.meeting_id,
        source=source,
        utterances=read_transcript(payload, name=name),
        part=part,
    )


# ── A whole meeting (story 7b) ───────────────────────────────────────────────


def meeting_utterances(
    meeting_id: str,
    start: datetime,
    *,
    parts: Sequence[TranscriptPart],
    captures: Mapping[str, str],
) -> tuple[Utterance, ...]:
    """One meeting's whole transcript, as one sequence of utterances.

    `parts` are the meeting's `transcript_parts` rows and `captures` its
    capture files' texts, by name; the reads are the caller's. In order:

    - with stored parts, every stored part in part order, each read on its
      own and appended (`33g` ingests each part alone and built no reader for
      the whole meeting, so this is it);
    - otherwise its Graph capture (`-graph.vtt`, captured before parts existed);
    - otherwise its dropped capture (`-manual.vtt`, then `-manual.txt`), read
      as `transcript drop` read it.

    `CaptureMissing` when a stored part's capture is not among `captures`
    (purged, or removed by hand) or when there is no capture at all.
    """
    stored = sorted(
        (part for part in parts if part.state is PartState.STORED), key=lambda part: part.part
    )
    if stored:
        utterances: list[Utterance] = []
        for part in stored:
            name = capture_name(meeting_id, start, CaptureFormat.GRAPH, part=part.part)
            text = captures.get(name)
            if text is None:
                raise CaptureMissing(
                    f"part {part.part} of meeting {meeting_id}'s transcript ({name}) is "
                    f"not on disk: purged at 30 days, or removed by hand."
                )
            utterances.extend(read_transcript(text, name=name))
        return tuple(utterances)
    graph = capture_name(meeting_id, start, CaptureFormat.GRAPH)
    if graph in captures:
        return read_transcript(captures[graph], name=graph)
    for form in (CaptureFormat.MANUAL_VTT, CaptureFormat.MANUAL_TXT):
        name = capture_name(meeting_id, start, form)
        if name in captures:
            return drop_utterances(captures[name], name=name)
    raise CaptureMissing(
        f"meeting {meeting_id} has no transcript capture on disk: none was "
        f"captured, or it was purged at 30 days."
    )


# ── The bind step ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MeetingDetails:
    """What a transcript's caller knows about its meeting when it names no id."""

    title: str
    start: datetime
    duration_minutes: int
    attendees: tuple[Actor, ...] = ()


def mint_meeting_id() -> str:
    """A pm-ai meeting id: `mtg_` plus random hex, the shape event ids use."""
    return "mtg_" + secrets.token_hex(10)


def bind_meeting(
    records: MeetingRecords,
    *,
    scope: DataScope,
    meeting_id: str | None = None,
    details: MeetingDetails | None = None,
) -> Meeting:
    """The meeting a transcript belongs to, by the one-record-per-meeting rule.

    - a named `meeting_id` wins, and must be recorded in `scope`
      (`MeetingNotFound`, naming the id and the scope);
    - otherwise `details` is matched against every record in `scope` whose
      start is within `MATCH_TOLERANCE` and whose title is the same once
      normalised (an empty title matches nothing):
      one binds, two or more are `AmbiguousMeeting` naming each, and none
      mints a manual-only record with a fresh `mtg_` id — returned, not
      written; ingestion writes it;
    - with neither, `UnboundTranscript`.

    Nothing is written here.
    """
    if meeting_id is not None:
        return records.get(meeting_id, scope=scope).meeting
    if details is None:
        raise UnboundTranscript(
            f"no meeting was named and no title and start were given, so there "
            f"is nothing in {scope} to bind the transcript to (AD-23)."
        )
    found = records.find_candidates(scope, details.title, details.start)
    return bind_candidates(found, scope=scope, details=details)


def bind_candidates(
    found: tuple[MeetingRecord, ...], *, scope: DataScope, details: MeetingDetails
) -> Meeting:
    """`bind_meeting`'s choice among candidates already read (story 11c).

    One binds, two or more are `AmbiguousMeeting` naming each, none mints a
    manual-only record from `details` — returned, not written. For a caller
    that has already asked `find_candidates`, so the records are read once and
    the answer it acted on is the one bound.
    """
    if len(found) == 1:
        return found[0].meeting
    if found:
        ids = tuple(record.meeting.meeting_id for record in found)
        raise AmbiguousMeeting(
            f"{len(ids)} meetings in {scope} match {details.title!r} within "
            f"{tolerance_minutes()} minutes of {details.start.isoformat()}: "
            f"{', '.join(ids)}. Name the one this transcript belongs to.",
            ids,
        )
    return Meeting(
        meeting_id=mint_meeting_id(),
        title=details.title,
        start=details.start,
        duration_minutes=details.duration_minutes,
        attendees=details.attendees,
        scope=scope,
        manual_only=True,
    )


# ── Naming a failure ─────────────────────────────────────────────────────────

_MESSAGE_LIMIT = 200
"""How much of a failure's message a stored reason keeps."""

_COMPOSED: tuple[type[BaseException], ...] = (
    UnreadableTranscript,
    UnboundTranscript,
    MeetingNotFound,
    MalformedMeeting,
    FileNotFoundError,
    UnicodeDecodeError,
)
"""Failures whose message pm-ai composes or Python fixes, quoting no
transcript or payload text — so their message may be stored."""


def failure_reason(failure: BaseException) -> str:
    """A failure as a stored reason: its type, and a bounded, cleaned message.

    The message only for the failures in `_COMPOSED`, put through the
    sanitizer and cut at `_MESSAGE_LIMIT` characters: any other exception's
    text can quote the transcript or a provider payload, and the reason is
    persisted in the operational store and printed by `doctor` (story 11b).
    """
    kind = type(failure).__name__
    if not isinstance(failure, _COMPOSED):
        return f"{kind} (its message is not kept: it may quote the transcript)"
    text = sanitize(" ".join(str(failure).split())).for_model
    if len(text) > _MESSAGE_LIMIT:
        text = text[: _MESSAGE_LIMIT - 1] + "…"
    return f"{kind}: {text}" if text else kind
