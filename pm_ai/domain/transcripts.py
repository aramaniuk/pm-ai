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
    """Always bound to a meeting. There is no constructor path without one."""

    meeting_id: str
    source: TranscriptSource
    utterances: tuple[Utterance, ...]

    def __post_init__(self) -> None:
        if not self.meeting_id:
            raise UnboundTranscript(
                "a transcript must bind to a meeting — to its calendar event where "
                "one exists, else to a record minted from supplied title/start/"
                "attendees (AD-23)"
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
class TranscriptCaptured:
    """The source found exactly one transcript for this meeting and read it."""

    meeting_id: str
    capture: TranscriptCapture


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

    `11b` adds the ingestion states to the same table. Until then a `fetched`
    row is a capture nothing has ingested yet.
    """

    FETCHED = "fetched"
    REFUSED = "refused"
    ABSENT = "absent"
    SKIPPED = "skipped"


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
    capture_name: str | None = None
    """The capture's filename, for a meeting fetched in this run."""
    attributed: bool | None = None
    """For a fetched capture: `False` when the tenant forbade speaker names, so
    the capture carries words without names and every extraction from it
    stages. `None` for every other state."""


@dataclass(frozen=True, slots=True)
class TranscriptReport:
    """One acquisition run, as `run_harvest` hands it back on `PersistResult`.

    `entries` holds one entry per meeting the source reached. `deferred` holds
    the meetings it was asked about and did not reach, because a provider fault
    stopped the run — they wait for the next run with no state recorded.
    `refusals` are run-level: a source that refused before any meeting (no
    Graph connector enrolled), a scope whose records could not be read, or a
    fault no meeting can be named for. `awaiting_ingestion` is every meeting
    whose capture is fetched and not ingested — handed to `11b`'s ingestion.
    """

    entries: tuple[TranscriptReportEntry, ...] = ()
    deferred: tuple[tuple[DataScope, str], ...] = ()
    refusals: tuple[str, ...] = ()
    awaiting_ingestion: tuple[tuple[DataScope, str], ...] = ()

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
