"""Reading a segment back, and folding it in the one order AD-35 fixes.

Nothing read a segment until this module. CAP-10's retrospective, story 19's
compaction and 1h's rebuild all have to, and AD-35 additionally fixes *how*:
entries fold by `(occurred_at, entry_id)`, "never file order — otherwise
`pm-ai reindex` changes commitment states while AD-3's test still passes."

**Pure: text in, entries out.** `pm_ai.core` sits below `pm_ai.storage` in the
layering contract, so this module cannot import the writer and never touches a
path. Reading a segment and handing over its text belongs to the accessor that
owns `event_log/` (story 2h).

The parser implements `storage-contract.md`'s append rule, and the rule is the
whole reason a concurrent read is safe: **a record without its terminating
newline is not a record.** An append cannot be made atomic — rename-into-place
would mean rewriting the file, which the append-only rule forbids — so a reader
landing mid-flush sees every complete record plus a fragment at the tail. The
fragment is a boundary, not corruption, and dropping it silently is what makes
the read succeed rather than fail. A *complete* record that will not parse is a
different thing entirely, and is refused loudly.
"""

from __future__ import annotations

from datetime import datetime

from pm_ai.domain.event_entries import (
    EventEntry,
    LedgerCategory,
    MalformedEntry,
    SelfActionType,
    category,
)
from pm_ai.domain.events import ObservedEventType

__all__ = ["fold", "parse_line", "parse_segment", "sample_entries"]

_UNKNOWN = "unknown"


def parse_segment(text: str, *, source: str = "<segment>") -> tuple[EventEntry, ...]:
    """Every complete record in `text`, in file order.

    An unterminated final line is dropped: it is a write in progress, not a
    record. Everything before it is whole, because each record was appended
    newline-terminated.

    **File order is arrival order, and it is the only exact one.** Storage is the
    single writer (AD-5) and every append adds one line at the end, so the
    sequence here is the sequence the records arrived in — at any rate, with no
    ties, including records sharing a timestamp. Neither `ingested_at` nor the
    entry id can do that: the first is stamped once per *batch*, so every event
    in one harvest carries an identical value, and the second is random.

    So a caller wanting a chronology reads this and stops. `fold` deliberately
    reorders, into the total order AD-35 fixes for deriving state across a
    rebuild; it is not a chronology and the two are not interchangeable.
    """
    entries = []
    for number, line in enumerate(text.split("\n")[:-1], start=1):
        try:
            entries.append(parse_line(line))
        except MalformedEntry as refusal:
            raise MalformedEntry(f"{source} line {number}: {refusal}") from refusal
    return tuple(entries)


def parse_line(line: str) -> EventEntry:
    """The inverse of `render_entry`, refusing anything it could not have written."""
    if not line.startswith("- ["):
        raise MalformedEntry(
            f"{line!r} does not open with the record marker `- [`. Every line in "
            f"a segment is a record; there are no comments and no blank lines."
        )
    closing = line.find("]", 3)
    if closing == -1:
        raise MalformedEntry(f"{line!r} has no closing bracket on its entry id.")
    entry_id = line[3:closing]

    rest = line[closing + 1 :]
    if not rest.startswith(" "):
        raise MalformedEntry(f"{line!r} has no category after its entry id.")
    fields = _scan(rest[1:], line=line)
    if not fields:
        raise MalformedEntry(f"{line!r} carries no category.")

    _, raw_category = fields[0]
    if not fields[1:] or fields[1][0] != "actor":
        raise MalformedEntry(
            f"{line!r} has no `actor=`. CAP-10 requires an actor on every entry."
        )
    return EventEntry(
        entry_id=entry_id,
        category=category(raw_category),
        actor=fields[1][1],
        fields=tuple(fields[2:]),
        # `category()` raises `UnknownCategory` for a value in neither
        # vocabulary — deliberately not caught here: a closed enumeration that
        # silently accepts an unknown member is not closed.
    )


def _scan(text: str, *, line: str) -> list[tuple[str, str]]:
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


def fold(entries) -> tuple[EventEntry, ...]:
    """Order entries by `(occurred_at, entry_id)` — AD-35's total order.

    Never file order. A rebuild reads the same records from the same segments in
    the same sequence only by accident; ordering on their content is what makes
    `pm-ai reindex` reproduce the live system's commitment states rather than
    quietly differ from them while AD-3's test still passes.

    Three ranks keep the comparison total without inventing anything. A parseable
    aware timestamp sorts on its UTC instant; anything else parseable — the naive
    or offset values story 2b flags rather than rejects — sorts after those, on
    its own text; `unknown` sorts last, because an entry with no world-time
    cannot be placed among entries that have one. Mixing them in one comparison
    is what would raise, and ranking is what avoids it.
    """
    return tuple(sorted(entries, key=_order))


def _order(entry: EventEntry) -> tuple[int, object, str]:
    raw = dict(entry.fields).get("occurred_at", _UNKNOWN)
    if raw == _UNKNOWN:
        return (2, "", entry.entry_id or "")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return (1, raw, entry.entry_id or "")
    if parsed.tzinfo is None:
        return (1, raw, entry.entry_id or "")
    return (0, parsed.timestamp(), entry.entry_id or "")


def sample_entries() -> list[EventEntry]:
    """A canonical set for `test_ad35_ledger_folding_is_deterministic`.

    Test-facing, and named by that pre-written test rather than chosen here. It
    covers the three ranks `_order` distinguishes, so a fold that stops being
    total fails on it rather than on whichever segment happens to hold a flagged
    timestamp first.
    """
    return [
        _sample("evt_c", "2026-08-19T10:00:00+00:00", SelfActionType.COMPACTION),
        _sample("evt_a", "2026-08-19T08:00:00+00:00", ObservedEventType.DECISION),
        _sample("evt_d", _UNKNOWN, SelfActionType.SECURITY),
        _sample("evt_b", "2026-08-19T09:00:00", ObservedEventType.COMMIT_PUSHED),
    ]


def _sample(entry_id: str, occurred_at: str, kind: LedgerCategory) -> EventEntry:
    return EventEntry(
        entry_id=entry_id,
        category=kind,
        actor="pm-ai",
        fields=(("occurred_at", occurred_at),),
    )
