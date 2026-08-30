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
    "DAEMON_ACTOR",
    "MAX_ENTRY_LENGTH",
    "EventEntry",
    "MalformedEntry",
    "render_entry",
    "CompactionPayload",
    "InconsistentVocabulary",
    "LedgerCategory",
    "SELF_ACTION_PAYLOAD_FOR",
    "SecurityPayload",
    "SelfActionType",
    "SkillInvokedPayload",
    "UnknownCategory",
    "category",
    "render_value",
    "scan_fields",
]


# AD-27 also asks that both vocabularies be "versioned so parsers can read
# historical entries". A `GRAMMAR_VERSION = 1` constant stood here until
# 2026-08-30 and was removed by code review: nothing wrote it into a line and
# nothing read it while parsing, so a segment written under one grammar was
# byte-indistinguishable from one written under any other, and the only test
# asserted the constant was an integer — it could not fail for the reason the
# requirement exists. Removed rather than back-filled, because putting a version
# on every line forever is a real cost and the choice between that, a segment
# header, and a dated grammar table is a design decision no story has taken. See
# deferred-work.md.


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


DAEMON_ACTOR = "pm-ai"
"""The actor on a record pm-ai wrote about itself.

One spelling, because the actor field is what a retrospective groups by: `pm-ai`
in one entry and `pm_ai` in another are two actors to every reader that counts
them. `storage-contract.md`'s COMPACTION example fixes this form.
"""

MAX_ENTRY_LENGTH = 4096
"""How long one rendered record may be.

A segment is plaintext Markdown the PM is meant to read, grep and diff by hand
(`storage-contract.md`), and one entry carrying a megabyte of payload defeats
every one of those. The bound turns that into a refusal at the writer, where it
names the entry, instead of a file nobody can open.
"""

_NEEDS_QUOTING = {" ", "=", '"', "\\"}


class MalformedEntry(ValueError):
    """A record that cannot be rendered without corrupting the ledger."""


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


@dataclass(frozen=True, slots=True)
class EventEntry:
    """One ledger record, before it is a line.

    `actor` and `category` are structural because every entry has them — CAP-10
    requires an entry id, a timestamp, an actor and a category on every one.
    Everything else is ordered `key=value` pairs, which is what lets one shape
    carry both a harvested event and a compaction without the record type
    growing a field per subject.

    Rendering is pure, so the id and every timestamp arrive already decided: the
    clock is the one the single writer injects, and `entry_id` is stamped by
    storage on the way in.

    `entry_id` is optional at construction and required at render, which is
    AD-34 expressed in the type: the `evt_` surrogate is assigned by the storage
    service at persist time, so a caller outside it builds an entry and does not
    name it. `NormalizedEvent.ingested_at` is nullable for the same reason.
    """

    category: LedgerCategory
    actor: str
    fields: tuple[tuple[str, str], ...] = ()
    entry_id: str | None = None


def render_value(value: str, *, where: str) -> str:
    """Bare when it can be, quoted when it must be, never able to forge a record."""
    if "\n" in value or "\r" in value:
        raise MalformedEntry(
            f"{where} contains a line break. A record is one line, and the append "
            f"rule reads an unterminated line as a fragment — so this would forge "
            f"a record boundary that no reader could detect as corruption."
        )
    if any(ch in value for ch in _NEEDS_QUOTING):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def render_entry(entry: EventEntry) -> str:
    """The one function that produces a ledger line.

    Returns the record without its terminating newline: the writer appends that,
    and it is the newline that makes the record a record.
    """
    if entry.entry_id is None:
        raise MalformedEntry(
            "this entry has no id. The `evt_` surrogate is minted by the storage "
            "service at persist time (AD-34), so an entry is rendered after it "
            "has been through the writer, never before."
        )
    if not entry.entry_id or any(c in entry.entry_id for c in " []\n\r"):
        raise MalformedEntry(
            f"entry_id {entry.entry_id!r} is not a bare token. It is rendered "
            f"inside brackets, where a space or a bracket ends the field early."
        )
    parts = [
        f"- [{entry.entry_id}]",
        entry.category.value,
        f"actor={render_value(entry.actor, where='actor')}",
    ]
    for key, value in entry.fields:
        if not key or any(ch in key for ch in ' ="\n\r\\'):
            raise MalformedEntry(
                f"field name {key!r} is not a bare token. A key carrying a "
                f"separator shifts every field after it when the line is parsed."
            )
        parts.append(f"{key}={render_value(value, where=f'field {key!r}')}")

    line = " ".join(parts)
    if len(line) > MAX_ENTRY_LENGTH:
        raise MalformedEntry(
            f"the rendered record is {len(line)} characters, beyond the "
            f"{MAX_ENTRY_LENGTH} bound. A segment is meant to be read and grepped "
            f"by hand; summarise the payload or cite what holds it."
        )
    return line


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


def scan_fields(text: str, *, line: str) -> list[tuple[str, str]]:
    """Split `key=value` pairs, honouring the quoting `render_entry` applies.

    The bare category token arrives as a pair with an empty key, which is why the
    caller reads `fields[0]` positionally.
    """
    out: list[tuple[str, str]] = []
    index = 0
    while index < len(text):
        if text[index] == " ":
            index += 1
            continue
        start = index
        while index < len(text) and text[index] not in " =":
            index += 1
        token = text[start:index]
        if index >= len(text) or text[index] == " ":
            out.append(("", token))
            continue
        index += 1  # past the '='
        value, index = _read_value(text, index, line=line)
        out.append((token, value))
    return out


def _read_value(text: str, index: int, *, line: str) -> tuple[str, int]:
    if index < len(text) and text[index] == '"':
        index += 1
        chars: list[str] = []
        while index < len(text):
            char = text[index]
            if char == "\\":
                if index + 1 >= len(text):
                    raise MalformedEntry(f"{line!r} ends inside an escape sequence.")
                chars.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                return "".join(chars), index + 1
            chars.append(char)
            index += 1
        raise MalformedEntry(f"{line!r} has an unclosed quoted value.")
    start = index
    while index < len(text) and text[index] != " ":
        index += 1
    return text[start:index], index
