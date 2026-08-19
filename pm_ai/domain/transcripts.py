"""Transcript sources and utterances (AD-23, AD-32).

`TranscriptSource` carries the trust property directly, so authorization cannot
be decided by inspecting a filename or trusting a VTT speaker label.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
    speaker_handle: str
    text: str
    offset_seconds: int


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
