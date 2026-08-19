"""Microsoft Graph transcript adapter — the primary path (AD-23)."""

from __future__ import annotations

from dataclasses import dataclass, field

from pm_ai.domain.transcripts import Transcript, TranscriptSource, Utterance


@dataclass
class GraphTranscriptAdapter:
    name: str = "graph"
    requires_network: bool = True
    _fake_api: dict[str, list[tuple[str, str, int]]] = field(default_factory=dict)

    def fetch(self, meeting_id: str) -> Transcript:
        rows = self._fake_api.get(meeting_id, [])
        return Transcript(
            meeting_id=meeting_id,
            source=TranscriptSource.GRAPH,  # speaker identity issued by the tenant
            utterances=tuple(Utterance(s, t, o) for s, t, o in rows),
        )
