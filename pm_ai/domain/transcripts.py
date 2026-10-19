"""Transcript sources and utterances (AD-23, AD-32).

`TranscriptSource` carries the trust property directly, so authorization cannot
be decided by inspecting a filename or trusting a VTT speaker label.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from pm_ai.domain.handles import PersonHandle
from pm_ai.domain.identity import DataScope


class TranscriptSource(Enum):
    GRAPH = "graph"    # speaker identity issued by the tenant
    MANUAL = "manual"  # watched folder — untrusted by construction (AD-32)

    @property
    def speaker_identity_is_authenticated(self) -> bool:
        return self is TranscriptSource.GRAPH


class UnboundTranscript(ValueError):
    """AD-23 — an unattributed file must not mint attributed provenance."""


class UnreadableTranscript(ValueError):
    """A file neither transcript reader accepts — not WebVTT, not `Speaker: words`.

    The only reader answer a caller refuses on (story 11b): the readers decide
    by content, never by extension, and a file with no `WEBVTT` header, no cue
    timing line and no speaker-labelled line is not a transcript pm-ai can read.
    The message names the file.
    """


class NothingSaid(ValueError):
    """A transcript a reader accepted that holds no utterance (story 11c).

    Whitespace only, or WebVTT cues whose payloads are all empty: there is
    nothing said to extract from, and storing it would spend the meeting's
    one capture name on a recording of silence. The message names the file.
    """


class CaptureMissing(LookupError):
    """A meeting's transcript is not on disk to read whole (story 7b).

    No capture at all, or a stored part's capture gone — purged at 30 days
    (NFR-09) or removed by hand. The message names the meeting and the part.
    """


class CaptureNotIngested(RuntimeError):
    """A dropped transcript was stored and its ingestion then failed (story 11c).

    The capture stays — it is verbatim input, and removing it is how the drop
    is run again — so the message names it, and the failure, by type and a
    bounded message that quotes no transcript text.
    """


class DropInterrupted(Exception):
    """Ctrl-C after a drop's questions, while it wrote or ingested (story 11c).

    An `Exception` rather than the `KeyboardInterrupt` it replaces, so the
    command can say whether the capture was kept instead of a bare
    "interrupted". The message says which.
    """


class DropRefused(RuntimeError):
    """A drop refused before its capture for a reason storage gave (story 11c).

    A busy or unopenable operational store, which the CLI cannot name by its
    storage type (AD-30). The message is storage's own sentence.
    """


@dataclass(frozen=True, slots=True)
class Utterance:
    """One line of a transcript.

    `speaker_handle` is the speaker's **label** as the transcript gave it — a
    display name, never parsed as a handle (AD-34). `speaker` is the speaker's
    person handle, which only an authenticated source can supply: both adapters
    leave it `None` today, because a label is all either has, so every spoken
    command stages until a slice gives an authenticated transcript the
    speaker's Graph user id (story 2o).
    """

    speaker_handle: str
    text: str
    offset_seconds: int
    speaker: PersonHandle | None = None


@dataclass(frozen=True, slots=True)
class Transcript:
    """Always bound to a meeting. There is no constructor path without one.

    `part` is which part of the meeting's transcript this is (story 33g): a
    meeting whose transcription was stopped and started again has one Graph
    capture per part, each ingested on its own. Every utterance's
    `offset_seconds` is measured from the start of this part, so a tracing
    offset (AD-33) is read with the part number beside it. A dropped file is
    always part 1.
    """

    meeting_id: str
    source: TranscriptSource
    utterances: tuple[Utterance, ...]
    part: int = 1

    def __post_init__(self) -> None:
        if not self.meeting_id:
            raise UnboundTranscript(
                "a transcript must bind to a meeting — to its calendar event where "
                "one exists, else to a record minted from supplied title/start/"
                "attendees (AD-23)"
            )
        if isinstance(self.part, bool) or not isinstance(self.part, int) or self.part < 1:
            raise ValueError(
                f"Transcript.part={self.part!r}: a part is numbered from 1 in time order"
            )


# ── Acquisition (story 33e) ──────────────────────────────────────────────────
#
# What a transcript source hands back when it is asked about meetings, and what
# the operational store remembers about each meeting it was asked about. Values
# only: fetching is an adapter's (`pm_ai.connectors.transcripts`), deciding which
# meetings to ask about and writing the capture is `pm_ai.core
# .transcript_acquisition`'s, and the protocol between them is
# `pm_ai.ports.TranscriptSourcePort`. Reading a capture's text into utterances
# is `11b`'s and is not here.


PLACEMENT_SLACK = timedelta(minutes=15)
"""How far a transcript's created instant may fall outside its occurrence's slot.

Widens a placement window, start to end (story 33e). Declared here rather than
in the Graph adapter because acquisition reads it too (story 33g): a meeting
holding parts stops being asked once a run that started after its widened
window ended has asked about it — nothing created later can fit. `11b`'s
`MATCH_TOLERANCE` is a start-to-start match tolerance for a dropped file — two
figures that happen to agree, not one rule. A meeting that overran its slot by
more than this is refused by name rather than guessed (an admitted limit;
`11c`'s drop is the repair).
"""


class CaptureFormat(Enum):
    """Which source produced a capture, and so the tail of its filename.

    One naming rule (`pm_ai.core.meeting_records.capture_name`): the meeting
    record's filename without `.md`, a dash, then this value. A Graph capture is
    always WebVTT; a dropped file (`11c`) is WebVTT or plain text, and the
    extension says which so a reader never has to guess.
    """

    GRAPH = "graph.vtt"
    MANUAL_VTT = "manual.vtt"
    MANUAL_TXT = "manual.txt"


@dataclass(frozen=True, slots=True)
class TranscriptCapture:
    """One transcript as its source returned it: the raw text, never parsed.

    `text` is stored verbatim (AD-29) — WebVTT from Graph, or Microsoft's
    unattributed text format when the tenant forbids speaker attribution, in
    which case `attributed` is `False` and the capture carries timings and
    words but no speaker names. `meeting_id` is the meeting **record's** id, the
    one the capture is filed beside; `transcript_id` is the provider's.
    """

    meeting_id: str
    transcript_id: str
    created: datetime
    ended: datetime
    text: str
    attributed: bool
    format: CaptureFormat

    def __post_init__(self) -> None:
        if not self.meeting_id:
            raise UnboundTranscript(
                "a capture must bind to a meeting record — it is filed beside "
                "the record and named after it (AD-23)"
            )
        for label, moment in (("created", self.created), ("ended", self.ended)):
            if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
                raise ValueError(
                    f"TranscriptCapture.{label}={moment!r} is not aware UTC; the "
                    f"provider's instants are UTC and are compared with a "
                    f"meeting's aware-UTC start."
                )


@dataclass(frozen=True, slots=True)
class StoredPart:
    """A part of a meeting's transcript already stored, as the source is told it (33g).

    The provider's transcript id and its two instants, read from the
    operational store's `transcript_parts` rows rather than from the listing,
    so a stored part the provider no longer lists still counts in the overlap
    and earlier-than-stored tests.
    """

    transcript_id: str
    created: datetime
    ended: datetime

    def __post_init__(self) -> None:
        for label, moment in (("created", self.created), ("ended", self.ended)):
            if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
                raise ValueError(f"StoredPart.{label}={moment!r} is not aware UTC.")


@dataclass(frozen=True, slots=True)
class TranscriptCaptured:
    """The source found new parts of this meeting's transcript and read them (33g).

    `captures` holds every new part, in time order (by `created`), each one
    verbatim; parts the source was told were stored are never among them. A
    meeting recorded in one stretch has one.
    """

    meeting_id: str
    captures: tuple[TranscriptCapture, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.captures, tuple) or not all(
            isinstance(capture, TranscriptCapture) for capture in self.captures
        ):
            raise TypeError("TranscriptCaptured.captures is a tuple of TranscriptCapture")
        if not self.captures:
            raise ValueError("TranscriptCaptured carries at least one capture")
        created = [capture.created for capture in self.captures]
        if created != sorted(created):
            raise ValueError(
                "TranscriptCaptured.captures are not in time order; parts are "
                "numbered by when each was created"
            )
        ids = [capture.transcript_id for capture in self.captures]
        if len(set(ids)) != len(ids):
            raise ValueError(
                f"TranscriptCaptured.captures name one transcript twice ({', '.join(ids)}); "
                f"it would be stored as two parts"
            )


@dataclass(frozen=True, slots=True)
class TranscriptAbsent:
    """No transcript to read: not recorded, not an online Teams meeting, or not
    the PM's to read (not organiser or invited, or expired). Tried again while
    the meeting is inside the harvest's reach-back."""

    meeting_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class TranscriptSkipped:
    """A transcript exists and is still being produced. Tried next run."""

    meeting_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class TranscriptRefused:
    """The source could not settle this meeting, and says whether waiting helps.

    `retryable` has no default, for `HarvestFailure.retryable`'s reason: the
    source knows whether the operator should wait or act. A refusal that is not
    retryable closes the meeting — it is not asked about again.
    """

    meeting_id: str
    reason: str
    retryable: bool
    retry_after: timedelta | None = None


TranscriptOutcome = TranscriptCaptured | TranscriptAbsent | TranscriptSkipped | TranscriptRefused
"""One answer per meeting a source was asked about and reached."""


class TranscriptStateKind(Enum):
    """Where one meeting's acquisition got to, as the operational store keeps it.

    The first four are acquisition's (`33e`); `INGESTED` and `INGEST_FAILED`
    are ingestion's (`11b`), written after a capture is read and extracted. A
    `fetched` row is a capture nothing has ingested yet.
    """

    FETCHED = "fetched"
    REFUSED = "refused"
    ABSENT = "absent"
    SKIPPED = "skipped"
    INGESTED = "ingested"
    INGEST_FAILED = "ingest_failed"


ACQUISITION_STATES: frozenset[str] = frozenset(
    kind.value
    for kind in (
        TranscriptStateKind.FETCHED,
        TranscriptStateKind.REFUSED,
        TranscriptStateKind.ABSENT,
        TranscriptStateKind.SKIPPED,
    )
)
"""The states acquisition (`33e`) writes. A row in any other state has been
through ingestion."""

_AWAITING = frozenset(
    {TranscriptStateKind.FETCHED.value, TranscriptStateKind.INGEST_FAILED.value}
)
"""The two states whose capture still awaits ingestion, under the cap."""


class PartState(Enum):
    """Where one part of a meeting's transcript got to (story 33g).

    `PENDING` is written before its capture, `STORED` once the capture is on
    disk: each part is one claimed step that a kill can interrupt, and the next
    selection repairs a pending row from what is on disk.
    """

    PENDING = "pending"
    STORED = "stored"


@dataclass(frozen=True, slots=True)
class TranscriptPart:
    """One row of the operational store's `transcript_parts` table (Tier 2, 33g).

    Never on the meeting record (AD-33). `part` numbers the meeting's parts in
    time order from 1; part 1's capture is `<stem>-graph.vtt` and part *n* ≥ 2's
    `<stem>-graph-<n>.vtt`. `reason` is set on a pending part whose write was
    refused in a way waiting may clear.
    """

    meeting_id: str
    part: int
    transcript_id: str
    created: datetime
    ended: datetime
    state: PartState
    ingested: bool = False
    stored_at: datetime | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.part, bool) or not isinstance(self.part, int) or self.part < 1:
            raise ValueError(f"TranscriptPart.part={self.part!r}: a part is numbered from 1")
        moments = [("created", self.created), ("ended", self.ended)]
        if self.stored_at is not None:
            moments.append(("stored_at", self.stored_at))
        for label, moment in moments:
            if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
                raise ValueError(f"TranscriptPart.{label}={moment!r} is not aware UTC.")

    @property
    def stored(self) -> StoredPart:
        return StoredPart(self.transcript_id, self.created, self.ended)


MAX_INGEST_ATTEMPTS = 3
"""How many failed ingestions a capture gets before it is left for `doctor`.

A capture whose ingestion failed is offered again on later runs until it has
failed this many times; after that it is not ingested again, and `pm-ai
doctor` names it (story 11b).
"""


@dataclass(frozen=True, slots=True)
class TranscriptState:
    """One row of the operational store's `transcript_state` table (Tier 2).

    Never on the meeting record (AD-33): the record holds no transcript pointer
    and no processing status. `state` is the stored text rather than the enum,
    so a row a later slice wrote (`11b`'s ingestion states) reads back instead
    of refusing; `closes` treats a state this slice does not know as closed,
    because every such state comes after a fetch.
    """

    state: str
    reason: str
    retryable: bool
    retry_after: timedelta | None
    at: datetime
    attempts: int = 0
    """How many times ingesting this meeting's capture has failed (story 11b)."""

    @property
    def awaits_ingestion(self) -> bool:
        """Whether this meeting's capture should be offered to ingestion now.

        Only a `fetched` capture, or one whose ingestion failed, and only while
        it has failed fewer than `MAX_INGEST_ATTEMPTS` times. Never an absent,
        refused or skipped meeting — there is no capture — and never an
        ingested one. The attempt count is checked for `fetched` too, so a row
        rewritten to `fetched` cannot reset the cap; ingesting clears it.
        """
        return self.state in _AWAITING and self.attempts < MAX_INGEST_ATTEMPTS

    @property
    def ingestion_exhausted(self) -> bool:
        """Failed `MAX_INGEST_ATTEMPTS` times — not tried again; `doctor` names it."""
        return self.state in _AWAITING and self.attempts >= MAX_INGEST_ATTEMPTS

    @property
    def closes(self) -> bool:
        """Whether this meeting is settled, so acquisition does not ask again."""
        if self.state in (TranscriptStateKind.ABSENT.value, TranscriptStateKind.SKIPPED.value):
            return False
        if self.state == TranscriptStateKind.REFUSED.value:
            return not self.retryable
        return True

    def waiting(self, now: datetime) -> bool:
        """Whether a retryable refusal's provider hint has not run out at `now`.

        A throttle that said "come back in an hour" is not asked again before
        the hour is up; the hint is measured from when the row was written.
        """
        return (
            self.state == TranscriptStateKind.REFUSED.value
            and self.retryable
            and self.retry_after is not None
            and self.at + self.retry_after > now
        )


@dataclass(frozen=True, slots=True)
class TranscriptReportEntry:
    """What happened to one meeting in one acquisition run."""

    scope: DataScope
    meeting_id: str
    state: TranscriptStateKind
    reason: str
    retryable: bool = False
    retry_after: timedelta | None = None
    capture_names: tuple[str, ...] = ()
    """Every part's capture filename stored in this run, in part order (33g)."""
    attributed: bool | None = None
    """For a fetched capture: `False` when the tenant forbade speaker names, so
    the capture carries words without names and every extraction from it
    stages. `None` for every other state."""


@dataclass(frozen=True, slots=True)
class TranscriptReport:
    """One acquisition run, as `run_harvest` hands it back on `PersistResult`.

    `entries` holds one entry per meeting the source reached whose row the
    answer wrote: a meeting already holding stored parts keeps its row unless a
    new part is stored, and a refusal about its new transcripts is in
    `refusals` instead (story 33g). `deferred` holds
    the meetings it was asked about and did not reach, because a provider fault
    stopped the run — they wait for the next run with no state recorded.
    `refusals` are run-level: a source that refused before any meeting (no
    Graph connector enrolled), a scope whose records could not be read, or a
    fault no meeting can be named for. `awaiting_ingestion` is every meeting
    whose capture is fetched and not ingested — handed to `11b`'s ingestion.
    `ingested` and `ingest_failed` are what that ingestion did with them in
    the same run; a failure carries its reason.
    """

    entries: tuple[TranscriptReportEntry, ...] = ()
    deferred: tuple[tuple[DataScope, str], ...] = ()
    refusals: tuple[str, ...] = ()
    awaiting_ingestion: tuple[tuple[DataScope, str], ...] = ()
    ingested: tuple[tuple[DataScope, str], ...] = ()
    ingest_failed: tuple[tuple[DataScope, str, str], ...] = ()

    def of(self, state: TranscriptStateKind) -> tuple[TranscriptReportEntry, ...]:
        return tuple(entry for entry in self.entries if entry.state is state)

    @property
    def captured(self) -> tuple[TranscriptReportEntry, ...]:
        return self.of(TranscriptStateKind.FETCHED)

    @property
    def refused(self) -> tuple[TranscriptReportEntry, ...]:
        return self.of(TranscriptStateKind.REFUSED)

    @property
    def unattributed(self) -> tuple[TranscriptReportEntry, ...]:
        """Captures without speaker names — every extraction from them stages."""
        return tuple(entry for entry in self.captured if entry.attributed is False)
