"""Transcript extraction (FR-05, FR-06) — explicit commands and implicit promises.

Every extracted fact cites the meeting, never the transcript (AD-33), so a
30-day purge cannot empty a citation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pm_ai.core.command_authorization import EXECUTE, classify
from pm_ai.core.sanitize import sanitize
from pm_ai.domain.identity import SourceRef
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.transcripts import Transcript

_EXPLICIT = re.compile(r"\bpm-ai,\s*(?P<verb>[a-z_]+)\s+(?P<target>\S+)(?P<rest>.*)", re.IGNORECASE)
_PROMISE = re.compile(r"\bI'll\s+(?P<what>.+?)\s+by\s+(?P<when>\w+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Extraction:
    kind: str            # "explicit_command" | "implicit_commitment"
    disposition: str     # EXECUTE | STAGE
    speaker_handle: str
    cites: SourceRef     # meeting:<id> — AD-33
    raw: str             # retained; sanitization is non-destructive (AD-29)
    for_model: str
    detail: dict


def extract(transcript: Transcript, meeting: Meeting, *, pm_handle: str, provider: str) -> list[Extraction]:
    out: list[Extraction] = []
    for u in transcript.utterances:
        clean = sanitize(u.text)  # AD-12 — at the boundary, uniformly

        if m := _EXPLICIT.search(u.text):
            verb = m["verb"].lower()
            out.append(Extraction(
                kind="explicit_command",
                disposition=classify(
                    source_authenticated=transcript.source.speaker_identity_is_authenticated,
                    speaker_is_pm=(u.speaker_handle == pm_handle),
                    provider=provider,
                    verb=verb,
                ),
                speaker_handle=u.speaker_handle,
                cites=meeting.source_ref,
                raw=clean.raw,
                for_model=clean.for_model,
                detail={"verb": verb, "target": m["target"], "rest": m["rest"].strip()},
            ))
            continue

        if p := _PROMISE.search(u.text):
            # AD-32/FR-06 — implicit extractions never auto-execute, whatever the source.
            out.append(Extraction(
                kind="implicit_commitment",
                disposition="stage",
                speaker_handle=u.speaker_handle,
                cites=meeting.source_ref,
                raw=clean.raw,
                for_model=clean.for_model,
                detail={"what": p["what"], "due": p["when"]},
            ))
    return out
