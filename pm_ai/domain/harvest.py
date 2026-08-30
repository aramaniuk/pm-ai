"""Harvest primitives: the opaque cursor and what a harvest returns.

`Cursor` is opaque bytes because AD-9 says so — core must not interpret a
provider's pagination scheme. `HarvestResult` carries the coverage window
because AD-35 needs it, and putting it in the return type means a connector
cannot forget to report it.

Imports nothing from `pm_ai` except sibling domain modules (AD-30).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pm_ai.domain.events import NormalizedEvent
from pm_ai.domain.lifecycle import CoverageWindow


@dataclass(frozen=True, slots=True)
class Cursor:
    """Provider-defined position. Opaque to everything but its own connector.

    Deliberately exposes no `.timestamp`, `.page`, or `.offset`: cross-connector
    ordering uses the ingested_at watermark, never cursor internals (AD-9).
    """

    token: bytes = b""

    def __repr__(self) -> str:  # keeps provider tokens out of logs and tracebacks
        return f"Cursor(<{len(self.token)} bytes>)"


@dataclass(frozen=True, slots=True)
class HarvestResult:
    """What every connector returns (AD-9, AD-35)."""

    events: tuple[NormalizedEvent, ...]
    cursor: Cursor
    coverage: CoverageWindow


@dataclass(frozen=True, slots=True)
class PersistResult:
    """What the single writer reports back (AD-5, AD-34)."""

    persisted: int
    duplicates: int
    at: datetime
    flagged: int = 0
    """How many of the persisted events carried a provider clock we cannot believe.

    Reported rather than raised, because AD-35 says an implausible `occurred_at`
    is flagged and the batch is all-or-nothing. Without a count, a connector whose
    clock is wrong flags every event it emits and nothing anywhere says so — the
    entries are in the ledger and no one is looking at them.
    """
