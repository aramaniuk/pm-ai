"""Watched-folder adapter — the fallback that keeps the pipeline buildable.

Untrusted by construction: it can ingest and stage, but never confers execution
authority (AD-32) and never mints provenance from an unattributed file (AD-23).
"""

from __future__ import annotations

from dataclasses import dataclass

from pm_ai.domain.transcripts import Transcript, TranscriptSource, UnboundTranscript, Utterance


@dataclass
class ManualTranscriptAdapter:
    name: str = "manual"
    requires_network: bool = False

    def load(self, raw: str, *, meeting_id: str | None) -> Transcript:
        if not meeting_id:
            raise UnboundTranscript(
                "a dropped transcript must bind to a meeting before ingestion — "
                "otherwise an unattributed file mints attributed provenance (AD-23)"
            )
        utterances = []
        for i, line in enumerate(l for l in raw.splitlines() if l.strip()):
            speaker, _, text = line.partition(":")
            utterances.append(Utterance(speaker.strip(), text.strip(), i * 30))
        return Transcript(
            meeting_id=meeting_id,
            source=TranscriptSource.MANUAL,
            utterances=tuple(utterances),
        )
