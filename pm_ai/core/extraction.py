"""Transcript extraction (FR-05, FR-06) — explicit commands and implicit promises.

Every extracted fact cites the meeting, never the transcript (AD-33), so a
30-day purge cannot empty a citation.

## Values of a fixed shape, carrying cleaned text beside the original

Story `8e` named the hole this closes: an extraction used to travel as a
free-form `detail` dictionary built from the raw utterance, so the cleaned copy
was dropped at the first hop and raw spoken text left through the pipeline with
no type in the way. Each text field is now a `Sanitized` — the original and the
cleaned copy together (AD-29) — inside a value whose fields are declared:
`SpokenCommand` (verb, target, remainder) and `Promise` (what, when). Nothing
here can hand one half on without the other.

No model is on this path (prototype path, decision 2): extraction is pattern
matching. `ModelPort.complete` stays the only callable in pm-ai declared to
accept cleaned text, which `tests/architecture/test_transcript_path_boundary.py` checks.

## Who spoke

An utterance is the PM's only when its person handle is in `pm_identity()`
(AD-32, AD-34) — never by its label. Neither reader gives an utterance a handle:
a WebVTT voice tag and a `Speaker:` label are display names the speakers chose,
so every spoken command read from a file, Graph's included, stages until a
later slice resolves names through the alias table. The execute branch is kept
and is exercised by transcripts built with a handle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pm_ai.core.command_authorization import STAGE, classify
from pm_ai.core.sanitize import Sanitized, sanitize
from pm_ai.domain.handles import NEVER_A_PERSON, PersonHandle
from pm_ai.domain.identity import MalformedReference, SourceRef, TargetRef
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.proposals import SPEAKER_LIMIT, fold_typed
from pm_ai.domain.transcripts import Transcript

__all__ = [
    "EXPLICIT_COMMAND",
    "IMPLICIT_COMMITMENT",
    "Extraction",
    "Promise",
    "SpokenCommand",
    "extract",
]

EXPLICIT_COMMAND = "explicit_command"
IMPLICIT_COMMITMENT = "implicit_commitment"

_EXPLICIT = re.compile(r"\bpm-ai,\s*(?P<verb>[a-z_]+)\s+(?P<target>\S+)(?P<rest>.*)", re.IGNORECASE)
# A typographic apostrophe too: Teams writes "I’ll" in its WebVTT (story 11b).
_PROMISE = re.compile(r"\bI['’]ll\s+(?P<what>.+?)\s+by\s+(?P<when>\w+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SpokenCommand:
    """`pm-ai, <verb> <target> <remainder>`, as said and as cleaned.

    `target` is the spoken target parsed by the reference grammar (AD-34), or
    `None` when the grammar refuses it — or when cleaning changed it, since a
    reference the sanitizer had to touch is not one pm-ai acts on. A command
    with no target only stages, as text. Its provider is read from the target,
    never defaulted.
    """

    verb: str
    spoken_target: Sanitized
    target: TargetRef | None
    remainder: Sanitized

    @property
    def provider(self) -> str | None:
        return None if self.target is None else self.target.system

    def rendering(self, *, spoken_in: SourceRef) -> dict[str, str]:
        """The command's stable rendering — what its once-only key is derived from.

        The original words, not the cleaned ones: the key identifies what was
        said, and the same words must always yield the same key (AD-20). The
        meeting it was said in is part of it, so the same words in two
        meetings are two commands, never one replayed.
        """
        return {
            "meeting": str(spoken_in),
            "verb": self.verb,
            "target": self.spoken_target.raw,
            "remainder": self.remainder.raw,
        }

    def payload(self) -> dict[str, object]:
        """What a staged proposal stores: every text field, original and cleaned."""
        return {
            "verb": self.verb,
            "target": {"raw": self.spoken_target.raw, "for_model": self.spoken_target.for_model},
            "remainder": {"raw": self.remainder.raw, "for_model": self.remainder.for_model},
        }


@dataclass(frozen=True, slots=True)
class Promise:
    """`I'll <what> by <when>` — a promise, which only ever stages.

    It names no work item, so it has no target and no once-only key.
    """

    what: Sanitized
    when: Sanitized
    speaker: Sanitized | None = None
    """Who made the promise (story 15a): the transcript's label, raw and cleaned —
    a name, never a handle (AD-34)."""

    def payload(self) -> dict[str, object]:
        """What a staged proposal stores: every text field, original and cleaned.

        `speaker` since story 15a: its `for_model` is the label through the
        same `sanitize` as `what`, line breaks and control and format
        characters folded. A cleaned label over 80 characters is not stored,
        since a shortened name is a different name.
        """
        stored: dict[str, object] = {
            "what": {"raw": self.what.raw, "for_model": self.what.for_model},
            "when": {"raw": self.when.raw, "for_model": self.when.for_model},
        }
        if self.speaker is not None:
            cleaned = fold_typed(self.speaker.for_model)
            if cleaned and len(cleaned) <= SPEAKER_LIMIT:
                stored["speaker"] = {"raw": self.speaker.raw, "for_model": cleaned}
        return stored


@dataclass(frozen=True, slots=True)
class Extraction:
    kind: str            # EXPLICIT_COMMAND | IMPLICIT_COMMITMENT
    disposition: str     # EXECUTE | STAGE
    speaker_handle: str  # the label as the transcript gave it — never a handle
    cites: SourceRef     # meeting:<id> — AD-33
    said: Sanitized      # the whole utterance; the raw is retained (AD-29)
    value: SpokenCommand | Promise

    @property
    def raw(self) -> str:
        return self.said.raw

    @property
    def for_model(self) -> str:
        return self.said.for_model


def _spoken_target(spoken: str) -> TargetRef | None:
    """The spoken target as a reference, or `None` when it is not one to act on.

    Takes the original words and is handed them only when cleaning left them
    unchanged, so the reference acted on is the one that was said.
    """
    try:
        return TargetRef.parse(spoken)
    except MalformedReference:
        return None


def extract(
    transcript: Transcript,
    meeting: Meeting,
    *,
    identity: frozenset[PersonHandle],
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
        said = sanitize(u.text)

        if m := _EXPLICIT.search(u.text):
            spoken = sanitize(m["target"])
            command = SpokenCommand(
                verb=m["verb"].lower(),
                spoken_target=spoken,
                target=_spoken_target(spoken.raw) if spoken.for_model == spoken.raw else None,
                remainder=sanitize(m["rest"].strip()),
            )
            if command.target is None or not command.remainder.raw:
                # Nothing to act on: a target the grammar refused stages as text,
                # and a command with no words after its target would post an
                # empty comment, so it stages too.
                disposition = STAGE
            else:
                disposition = classify(
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
                    provider=command.target.system,
                    verb=command.verb,
                )
            out.append(Extraction(
                kind=EXPLICIT_COMMAND,
                disposition=disposition,
                speaker_handle=u.speaker_handle,
                cites=meeting.source_ref,
                said=said,
                value=command,
            ))
            continue

        if p := _PROMISE.search(u.text):
            # AD-32/FR-06 — implicit extractions never auto-execute, whatever the source.
            out.append(Extraction(
                kind=IMPLICIT_COMMITMENT,
                disposition=STAGE,
                speaker_handle=u.speaker_handle,
                cites=meeting.source_ref,
                said=said,
                value=Promise(
                    what=sanitize(p["what"]),
                    when=sanitize(p["when"]),
                    speaker=sanitize(u.speaker_handle) if u.speaker_handle else None,
                ),
            ))
    return out
