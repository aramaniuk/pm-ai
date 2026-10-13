"""Reading a transcript and binding it to a meeting (story 11b, AD-23, AD-33).

Three things, all I/O-free:

- **The source catalogue.** `registered_adapters()` and `get_adapter(name)` over
  a registry the composition root fills when it is imported (`pm_ai.app.wiring`)
  — `core` imports no adapter (AD-30). It holds the adapter *kinds* by name
  (`"graph"`, `"manual"`), each a class satisfying `TranscriptSourcePort`; the
  instances a daemon acquires through are `Daemon.transcripts`.
- **The readers.** A capture's text becomes utterances through one of two
  readers, chosen by content and never by extension: WebVTT — Graph's format
  and Microsoft's unattributed one, which has no header — and today's plain
  `Speaker: words` text. A file neither accepts is `UnreadableTranscript`,
  naming it. This is this slice's own contract; the acquisition port is
  `33e`'s and yields captures, not utterances.
- **The bind step.** A transcript is never ingested without a meeting (AD-23).
  `bind_meeting` follows the one-record-per-meeting rule (D3, AD-33): a named
  id wins and must exist in the scope; otherwise a title and a start match at
  most one record within `MATCH_TOLERANCE`, and none mints a manual-only record.

Neither reader gives an utterance a person handle. A WebVTT voice tag and a
`Speaker:` label are display names the speakers chose, never accounts (AD-34),
so `Utterance.speaker` stays `None` and every spoken command read here stages.
"""

from __future__ import annotations

import html
import re
import secrets
from dataclasses import dataclass
from datetime import datetime

from pm_ai.core.meeting_records import MalformedMeeting, MeetingNotFound, MeetingRecords
from pm_ai.core.sanitize import sanitize
from pm_ai.domain.identity import Actor, DataScope
from pm_ai.domain.meetings import AmbiguousMeeting, Meeting, tolerance_minutes
from pm_ai.domain.transcripts import (
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
    "bind_meeting",
    "failure_reason",
    "get_adapter",
    "ingest",
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

    WebVTT when the first non-blank line is `WEBVTT` or a cue timing line;
    otherwise `Speaker: words` text. `UnreadableTranscript`, naming `name`,
    for a WebVTT file with no cue in it, and for text whose lines are not
    mostly speaker-labelled.
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
    utterances = _read_text(lines)
    if utterances is None:
        raise UnreadableTranscript(
            f"{name} is not a transcript pm-ai can read: it has no WEBVTT header, "
            f"no cue timing line, and most of its lines are not `Speaker: words`."
        )
    return utterances


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


def ingest(
    payload: str,
    meeting: Meeting | None,
    *,
    source: TranscriptSource = TranscriptSource.MANUAL,
    name: str = "the transcript",
) -> Transcript:
    """A capture's text read into a transcript bound to `meeting`.

    `UnboundTranscript` with no meeting, before the text is read (AD-23): an
    unattributed file must not mint attributed provenance. `source` defaults to
    `MANUAL`, the one that never executes (AD-32).
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
