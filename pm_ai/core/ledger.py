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
    UNKNOWN_VALUE,
    UnknownCategory,
    category,
    scan_fields,
)
from pm_ai.domain.events import ObservedEventType

__all__ = ["fold", "parse_line", "parse_segment"]



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
        except UnknownCategory as refusal:
            # Caught alongside `MalformedEntry` because a category retired from
            # the vocabulary is the corruption most likely to appear in an *old*
            # segment — and until this, it was the one refusal that could not
            # tell an operator which file or line it came from.
            raise UnknownCategory(f"{source} line {number}: {refusal}") from refusal
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
    if not entry_id:
        # `render_entry` refuses to write this, so accepting it here would let
        # the parser admit a record the writer cannot produce — and empty ids
        # collide with one another in `fold`'s tiebreaker.
        raise MalformedEntry(
            f"{line!r} has an empty entry id. The writer mints one on every "
            f"record (AD-34), so a line without one was not written by it."
        )

    rest = line[closing + 1 :]
    if not rest.startswith(" "):
        raise MalformedEntry(f"{line!r} has no category after its entry id.")
    fields = scan_fields(rest[1:], line=line)
    if not fields:
        raise MalformedEntry(f"{line!r} carries no category.")

    _, raw_category = fields[0]
    if not fields[1:] or fields[1][0] != "actor":
        raise MalformedEntry(
            f"{line!r} has no `actor=`. CAP-10 requires an actor on every entry."
        )
    rest_fields = tuple(fields[2:])
    names = [key for key, _ in rest_fields]
    if len(set(names)) != len(names):
        # `dict(entry.fields)` resolves a duplicate to the *last* occurrence, so a
        # repeated key silently overrides the first — the same shadowing the
        # writer refuses on the way in, arriving instead through a hand-edited
        # or corrupted line.
        duplicated = sorted({name for name in names if names.count(name) > 1})
        raise MalformedEntry(
            f"{line!r} repeats {duplicated}. A later value would silently win "
            f"over the earlier one for every reader."
        )
    return EventEntry(
        entry_id=entry_id,
        category=category(raw_category),
        actor=fields[1][1],
        fields=rest_fields,
        # `category()` raises `UnknownCategory` for a value in neither
        # vocabulary — deliberately not caught here: a closed enumeration that
        # silently accepts an unknown member is not closed.
    )


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
    raw = dict(entry.fields).get("occurred_at", UNKNOWN_VALUE)
    if raw == UNKNOWN_VALUE:
        return (2, "", entry.entry_id or "")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return (1, raw, entry.entry_id or "")
    if parsed.tzinfo is None:
        return (1, raw, entry.entry_id or "")
    return (0, parsed.timestamp(), entry.entry_id or "")


