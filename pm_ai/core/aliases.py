"""The saved alias table: a name the PM confirmed → a handle (story 7e, AD-34).

**The one place a name becomes a handle.** A speaker label is a display name,
never a handle (AD-34); a ledger records it as said, this table records what
the PM confirmed, and a reader joins the two at read time, so a correction
applies to every past line. There is no second map: later name resolution —
Graph speakers, chat senders — reads this table too.

It sits beside `pm_ai.domain.handles.ALIASES` and does not replace it: that
table, still unsaved, says which *person* a handle is; this one says which
*handle* a name is.

**Rows.** `aliases.md` lives in the application scope, append-only. One row per
confirmed name: the name as said, its key under `normalise`, the handle, and
optionally the one meeting it applies to. A removal row (`removed=yes`) ends a
mapping; a later row for the same key and meeting replaces an earlier one. A
row is one line:

    - name=<key> said=<as said> handle=<system:handle> [meeting=<id>] at=<instant>
    - name=<key> said=<as said> removed=yes [meeting=<id>] at=<instant>

Every value through the ledgers' one value encoding (`render_value`), so a
quote, a space or a newline in a name stays inside its value. A line that does
not read as a row is skipped and counted, never fatal.

Pure: text in, values out.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from pm_ai.domain.event_entries import MAX_ENTRY_LENGTH, MalformedEntry, render_value, scan_fields
from pm_ai.domain.handles import InvalidPersonHandle, PersonHandle, parse_person_handle

__all__ = [
    "AliasRow",
    "AliasTable",
    "normalise",
    "parse_aliases",
    "render_alias_row",
]

_WHITESPACE = re.compile(r"\s+")


def normalise(name: str) -> str:
    """The one key a name is compared by: NFKC, case-folded, trimmed, one space.

    `" ВАНЯ "` and `"Ваня"` are one key. Used for the alias table's rows and
    for the read-time match against a member's display name and username, so
    the two can never compare differently.
    """
    folded = unicodedata.normalize("NFKC", name).casefold()
    # NFKC again after case-folding: folding can leave a string that is no
    # longer in NFKC form — `ǰ` (U+01F0) folds to `j` + a combining caron,
    # which NFKC composes back — so without it one spelling would have two
    # keys depending on whether it arrived composed (the key is idempotent).
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", folded)).strip()


@dataclass(frozen=True, slots=True)
class AliasRow:
    """One row: `handle` is `None` for a removal."""

    name: str
    said: str
    handle: PersonHandle | None
    meeting: str | None
    at: datetime

    @property
    def removal(self) -> bool:
        return self.handle is None


def render_alias_row(row: AliasRow) -> str:
    """The row's line, without its newline; refused when it would not be one line."""
    if not row.name or row.name != normalise(row.said):
        raise ValueError("an alias row's key is its name under `normalise`, and is never empty.")
    if row.at.tzinfo is None or row.at.utcoffset() is None:
        raise ValueError("an alias row's instant carries its timezone.")
    parts = [
        f"name={render_value(row.name, where='a name')}",
        f"said={render_value(row.said, where='a name as said')}",
    ]
    if row.handle is None:
        parts.append("removed=yes")
    else:
        parts.append(f"handle={render_value(str(row.handle), where='a handle')}")
    if row.meeting is not None:
        parts.append(f"meeting={render_value(row.meeting, where='a meeting id')}")
    parts.append(f"at={row.at.isoformat()}")
    line = "- " + " ".join(parts)
    if len(line) > MAX_ENTRY_LENGTH or "\n" in line or "\r" in line:
        raise ValueError("an alias row must be one line within the ledgers' bound.")
    return line


_ALLOWED = frozenset({"name", "said", "handle", "removed", "meeting", "at"})


def _row(line: str) -> AliasRow | None:
    if not line.startswith("- "):
        return None
    try:
        pairs = scan_fields(line[2:], line=line)
    except MalformedEntry:
        return None
    found: dict[str, str] = {}
    for key, value in pairs:
        if key not in _ALLOWED or key in found:
            return None
        found[key] = value
    if not {"name", "said", "at"} <= found.keys():
        return None
    if ("handle" in found) == ("removed" in found):
        return None
    if "removed" in found and found["removed"] != "yes":
        return None
    if not found["name"] or normalise(found["said"]) != found["name"]:
        return None
    try:
        at = datetime.fromisoformat(found["at"])
        handle = parse_person_handle(found["handle"]) if "handle" in found else None
    except (ValueError, InvalidPersonHandle):
        return None
    if at.tzinfo is None or at.utcoffset() is None:
        return None
    if handle is not None and handle.is_absent:
        return None
    meeting = found.get("meeting")
    if meeting is not None and not meeting:
        return None
    return AliasRow(found["name"], found["said"], handle, meeting, at)


@dataclass(frozen=True, slots=True)
class AliasTable:
    """The table as read: the newest row per (key, meeting), and lines skipped.

    `rows` holds every row read, in file order; `current` the newest per
    `(key, meeting or None)`, removals included.
    """

    rows: tuple[AliasRow, ...] = ()
    current: Mapping[tuple[str, str | None], AliasRow] = field(
        default_factory=lambda: MappingProxyType({})
    )
    skipped: int = 0

    def row_for(self, name: str, meeting: str | None) -> AliasRow | None:
        """The newest row for exactly `name` and `meeting`, removal included."""
        return self.current.get((normalise(name), meeting))

    def handle_for(self, label: str, meeting: str | None) -> PersonHandle | None:
        """The handle `label` is saved as: a row for this meeting, else an unscoped one.

        A removal for this meeting ends the meeting's mapping and does not fall
        through to the unscoped row: the PM said this meeting's label is no one.
        """
        if meeting is not None:
            scoped = self.current.get((normalise(label), meeting))
            if scoped is not None:
                return scoped.handle
        unscoped = self.current.get((normalise(label), None))
        return None if unscoped is None else unscoped.handle


def parse_aliases(text: str | bytes | None) -> AliasTable:
    """`aliases.md` read; absent or empty is the empty table.

    Blank lines and lines starting `#` (a heading a person wrote) are not rows
    and not counted; any other line that is not a row is skipped and counted.
    Undecodable bytes are read as U+FFFD, never raised.
    """
    if text is None:
        return AliasTable()
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    rows: list[AliasRow] = []
    skipped = 0
    for raw in _lines(text):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        row = _row(line)
        if row is None:
            skipped += 1
            continue
        rows.append(row)
    current: dict[tuple[str, str | None], AliasRow] = {}
    for row in rows:
        current[(row.name, row.meeting)] = row
    return AliasTable(tuple(rows), MappingProxyType(current), skipped)


def _lines(text: str) -> Iterable[str]:
    """Complete lines only: a last line without its newline is not a record (AD-47)."""
    parts = text.split("\n")
    return parts[:-1]
