"""What pm-ai records about itself, and the union a ledger line may be tagged with.

AD-27 closes *two* enumerations in `domain`: the `NormalizedEvent` types and the
`event_log/` entry types, "both versioned so parsers can read historical
entries". Only the first existed. The consequence was four grammars reaching one
ledger — the connector vocabulary through `_append_batch`, plus `- [security]`,
`- [skill]` and a bare `COMPACTION` invented at three separate call sites — and
so nothing could parse a segment, because nothing said what an entry may be.

**Two vocabularies, one per subject.**

- `ObservedEventType` (in `events`) — the world, observed. Its subject is
  something outside pm-ai, with pm-ai as the witness.
- `SelfActionType` (here) — pm-ai, acting. Its subject is pm-ai itself, and
  pm-ai is the only witness.

The question that places any candidate is *did this happen, or did pm-ai do it?*
A candidate that looks like both is two records, which is what `authored_by`
exists to distinguish.

The separation is not stylistic. `ObservedEventType` is the type tag of
`NormalizedEvent`, whose envelope requires a `SourceRef` and whose dedup key is
derived from it — so an operational record placed there would need a synthetic
referent, widening a second closed set, and the *second* compaction in a scope
would be discarded as a duplicate by `persist_events`. The audit trail would lose
records by design.

Imports only sibling `pm_ai.domain` modules (AD-30), and raises typed errors
rather than asserting: `python -O` strips `assert`, and these guards run at
import (AD-44).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pm_ai.domain.events import PAYLOAD_FOR, ObservedEventType
from pm_ai.domain.invariants import InconsistentModel

__all__ = [
    "GRAMMAR_VERSION",
    "CompactionPayload",
    "InconsistentVocabulary",
    "LedgerCategory",
    "SELF_ACTION_PAYLOAD_FOR",
    "SecurityPayload",
    "SelfActionType",
    "SkillInvokedPayload",
    "UnknownCategory",
    "category",
]


GRAMMAR_VERSION = 1
"""The entry grammar a segment was written under.

AD-27 asks for it so a parser can read historical entries: a member added or a
rendering changed moves this, and a reader can tell which rules applied to a line
written months ago rather than guessing from its shape.
"""


class SelfActionType(Enum):
    """The closed vocabulary for things pm-ai did (AD-27).

    **Subject: pm-ai itself, and pm-ai is the only witness.** A member qualifies
    only if it has no external referent, is never admissible as evidence (AD-36),
    and is never deduplicated — each occurrence is a distinct fact about the
    machine and every one of them has to survive.

    No connector may declare one of these in `emits()`. That is not enforced by
    the connector taxonomy check, which reads `ObservedEventType`; it is enforced
    by these members not being in that enum at all.
    """

    COMPACTION = "compaction"
    SECURITY = "security"
    # Carries the provider's `external_id`, which looks like an external
    # referent and is not one for this purpose: AD-36 makes pm-ai's own writes
    # never evidence, so the record is about the act, not about its effect.
    SKILL_INVOKED = "skill_invoked"


@dataclass(frozen=True, slots=True)
class CompactionPayload:
    """The only job that destroys Tier 1, and what it destroyed.

    Checksums rather than filenames alone, per `storage-contract.md`: a filename
    says *a file called this was deleted*, a checksum says *this exact content
    was deleted*, and filenames are reused across months while content is not.
    """

    source: str
    replaced: tuple[tuple[str, str], ...]
    summary: tuple[str, str]


@dataclass(frozen=True, slots=True)
class SecurityPayload:
    """A protection the operator turned off, and the mechanism that did it."""

    protection: str
    disabled_by: str


@dataclass(frozen=True, slots=True)
class SkillInvokedPayload:
    """AD-1's one entry per invocation, in the owning scope."""

    skill: str
    target: str
    external_id: str
    idempotency_key: str


SELF_ACTION_PAYLOAD_FOR: dict[SelfActionType, type] = {
    SelfActionType.COMPACTION: CompactionPayload,
    SelfActionType.SECURITY: SecurityPayload,
    SelfActionType.SKILL_INVOKED: SkillInvokedPayload,
}

SELF_ACTION_VALUES = {member.value for member in SelfActionType}

LedgerCategory = ObservedEventType | SelfActionType
"""What a segment line may be tagged with: either subject, never a third thing."""


class UnknownCategory(ValueError):
    """A wire value naming no member of either vocabulary."""


class InconsistentVocabulary(InconsistentModel):
    """The two vocabularies contradict each other, or a member is untyped.

    Raised while this module is being imported, so it fails to load rather than
    serving a vocabulary whose own declaration disagrees with itself. An
    `InconsistentModel` rather than a `ValueError` for the reason that module
    gives: nothing was passed in wrongly, and this reaches everything rather than
    one call site.
    """


def category(value: str) -> LedgerCategory:
    """Resolve a wire value to its member, or refuse it.

    Both vocabularies are searched because a reader of a segment does not know in
    advance which subject a line has — that is exactly what the tag tells it. The
    disjointness guard below is what makes the search unambiguous.
    """
    for member in ObservedEventType:
        if member.value == value:
            return member
    for action in SelfActionType:
        if action.value == value:
            return action
    known = sorted(
        [m.value for m in ObservedEventType] + [a.value for a in SelfActionType]
    )
    raise UnknownCategory(
        f"{value!r} is not a ledger category. The set is closed and adding a "
        f"member is a deliberate change in `domain` reviewed for overlap "
        f"(AD-27). Known: {', '.join(known)}."
    )


def _assert_vocabularies_agree() -> None:
    """What neither enum can check about itself, at import time.

    An explicit raise rather than `assert`: `-O` strips assertions, and the
    daemon runs as a `launchd` agent where `-O` is an ordinary optimisation.
    """
    overlap = {m.value for m in ObservedEventType} & SELF_ACTION_VALUES
    if overlap:
        raise InconsistentVocabulary(
            f"{sorted(overlap)} name both an observed event and a pm-ai action. "
            f"One occurrence has exactly one member, or a parser cannot tell "
            f"which subject a segment line had."
        )

    untyped = [m.name for m in SelfActionType if m not in SELF_ACTION_PAYLOAD_FOR]
    if untyped:
        raise InconsistentVocabulary(
            f"{untyped} have no payload registered. An entry without a shape is "
            f"the free text this vocabulary replaced."
        )

    leaked = [m.name for m in SelfActionType if m in PAYLOAD_FOR]
    if leaked:
        raise InconsistentVocabulary(
            f"{leaked} are registered as `NormalizedEvent` payloads. A pm-ai "
            f"action is not harvestable, and `persist_events` would deduplicate "
            f"the second occurrence away."
        )


_assert_vocabularies_agree()
