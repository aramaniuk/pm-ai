"""The commitment index's row, as the store and its port name it (story 15b).

`commitment_index.db` is Tier 3 (AD-3): one row per commitment per scope, each
the fold of its scope's `commitments_log.md` and carrying the MD5 of the bytes
it was folded from (AD-46). The rows are computed in
`pm_ai.core.commitment_index`; this module only names their shape, so that
`StoragePort` can declare the index's calls in domain types (AD-30) and the
single writer can store them without importing the core.

Imports nothing from `pm_ai` (AD-30).
"""

from __future__ import annotations

from dataclasses import dataclass, fields

__all__ = ["INDEX_COLUMNS", "IndexRow", "ReplacedRows", "ScopeChecksums"]


@dataclass(frozen=True, slots=True)
class IndexRow:
    """One commitment of one scope, as `commitment_index.db` holds it (story 15b).

    Every value is in its stored form: an instant is UTC ISO 8601 to the
    microsecond, ending `+00:00`; a date is `YYYY-MM-DD`; a flag is `0` or
    `1`; empty is `None`. No row holds a commitment's words or a person's
    name, and nothing in it depends on the clock or the display zone.
    """

    scope: str
    """`personal` or `project:<id>`."""
    commitment_id: str
    proposal_id: str
    type: str
    state: str
    state_source: str
    state_at: str | None
    due_value: str
    due_date: str | None
    due_estimated: int
    due_by_pm: int
    meeting_id: str | None
    meeting_start: str | None
    day: str | None
    day_from: str | None
    approved_at: str
    changed_at: str
    version: str
    malformed_named: int
    source_md5: str
    """The MD5 of the scope's whole `commitments_log.md`, as read for the
    derivation that wrote this row: change detection only, never integrity."""


INDEX_COLUMNS: tuple[str, ...] = tuple(each.name for each in fields(IndexRow))
"""The `commitments` table's columns, in `IndexRow`'s order."""


@dataclass(frozen=True, slots=True)
class ScopeChecksums:
    """What the index holds for one scope: its rows' checksums and how many rows."""

    md5s: frozenset[str]
    rows: int


@dataclass(frozen=True, slots=True)
class ReplacedRows:
    """What one conditional replace did (story 15b).

    `changed` is true when the scope's stored checksums were no longer the
    ones the derivation read: nothing was written, and the scope is derived
    again.
    """

    written: int = 0
    removed: int = 0
    changed: bool = False
