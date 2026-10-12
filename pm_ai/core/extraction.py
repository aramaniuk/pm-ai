"""Transcript extraction (FR-05, FR-06) — explicit commands and implicit promises.

Every extracted fact cites the meeting, never the transcript (AD-33), so a
30-day purge cannot empty a citation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pm_ai.core.command_authorization import EXECUTE, classify
from pm_ai.core.sanitize import sanitize
from pm_ai.domain.handles import NEVER_A_PERSON, PersonHandle
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


def extract(
    transcript: Transcript,
    meeting: Meeting,
    *,
    identity: frozenset[PersonHandle],
    provider: str,
) -> list[Extraction]:
    """Every explicit command and implicit promise in `transcript`.

    `identity` is the PM's handles, read once per ingestion by
    `pm_ai.app.wiring.read_pm_identity` (story 2o). An utterance is the PM's
    only when its `speaker` handle is in it — never by its label — so an empty
    identity matches nobody and an utterance with no handle is nobody's.
    """
    if not isinstance(identity, frozenset):
        raise TypeError(
            f"extract takes the PM's identity as a frozenset of handles, not "
            f"{identity!r}: it is read once per ingestion and must not change "
            f"while the transcript is read."
        )
    for member in identity:
        if not isinstance(member, PersonHandle):
            raise TypeError(
                f"the PM's identity holds {member!r}, which is not a PersonHandle."
            )
    out: list[Extraction] = []
    for u in transcript.utterances:
        if u.speaker is not None and not isinstance(u.speaker, PersonHandle):
            raise TypeError(
                f"an utterance's speaker is {u.speaker!r}, which is neither None "
                f"nor a PersonHandle. A label belongs in `speaker_handle` and is "
                f"never parsed as a handle."
            )
        # **A producer-side pass, kept on purpose, and not the AD-12 guard.**
        # Story 8e deleted the one in `pipelines.py` because it discarded its
        # result and protected nothing; this one is used, so it is worth having
        # — but it is not what makes AD-12 binding either, and reading it that
        # way is the mistake the deleted line existed to demonstrate. The guard
        # is `pm_ai.ports.ModelPort`, which accepts `Sanitized` and never `str`.
        #
        # This path does not reach it. `Extraction` below flattens the pair into
        # two bare `str` fields, and `detail` is built from `u.text` rather than
        # from `clean`, so raw utterance text leaves through
        # `pipelines.py`'s `payload={"comment": ex.detail["rest"]}` with no type
        # in the way. Story `11b` owns retyping the carrier; until it lands this
        # call is a courtesy, not a boundary.
        clean = sanitize(u.text)

        if m := _EXPLICIT.search(u.text):
            verb = m["verb"].lower()
            out.append(Extraction(
                kind="explicit_command",
                disposition=classify(
                    source_authenticated=transcript.source.speaker_identity_is_authenticated,
                    # The handle, never the label: a label is a display name
                    # anyone in the meeting can share (AD-32, AD-34). A bot
                    # sender or absence is never the PM, whatever `identity`
                    # holds.
                    speaker_is_pm=(
                        u.speaker is not None
                        and u.speaker.system not in NEVER_A_PERSON
                        and u.speaker in identity
                    ),
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
