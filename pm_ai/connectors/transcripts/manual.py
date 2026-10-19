"""The dropped-file source — the fallback that keeps the pipeline buildable.

Untrusted by construction: what it brings can be ingested and staged, but never
confers execution authority (AD-32) and never mints provenance from an
unattributed file (AD-23).

It satisfies `TranscriptSourcePort` like the Graph adapter, and like it yields
captures, never utterances: reading a capture's text is `pm_ai.core.transcripts`'
(story 11b). A dropped file is handed in by hand (`11c`) rather than asked for,
so `acquire` has nothing to fetch and answers every meeting as absent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from pm_ai.domain.meetings import Meeting
from pm_ai.domain.transcripts import StoredPart, TranscriptAbsent, TranscriptOutcome


@dataclass
class ManualTranscriptAdapter:
    name: str = "manual"
    requires_network: bool = False

    def acquire(
        self,
        meetings: Sequence[Meeting],
        *,
        since: datetime,
        stored: Mapping[str, Sequence[StoredPart]],
    ) -> tuple[TranscriptOutcome, ...]:
        """Absent for every meeting: a dropped file is handed in, never fetched.

        `stored` is read by nothing here: there is no new part to find.
        """
        return tuple(
            TranscriptAbsent(
                meeting.meeting_id,
                "the manual source fetches nothing: a transcript file is handed "
                "in by hand, not asked for.",
            )
            for meeting in meetings
        )
