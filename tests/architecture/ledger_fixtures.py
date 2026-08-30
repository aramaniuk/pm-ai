"""The entry set `test_ad35_ledger_folding_is_deterministic` folds.

Lived in `pm_ai/core/ledger.py` until the story-2 code review moved it here: it
is a fixture, named by a test, and a Tier-1 domain-adjacent module is not where
test scaffolding belongs. The architecture suite is on `pythonpath`, so the test
imports it from here.

It covers the three ranks `_order` distinguishes — an aware timestamp, a naive
one, and `unknown` — so a fold that stops being total fails on this rather than
on whichever segment happens to hold a flagged timestamp first.
"""

from __future__ import annotations

from pm_ai.domain.event_entries import EventEntry, LedgerCategory, SelfActionType
from pm_ai.domain.events import ObservedEventType


def sample_entries() -> list[EventEntry]:
    return [
        _sample("evt_c", "2026-08-19T10:00:00+00:00", SelfActionType.COMPACTION),
        _sample("evt_a", "2026-08-19T08:00:00+00:00", ObservedEventType.DECISION),
        _sample("evt_d", "unknown", SelfActionType.SECURITY),
        _sample("evt_b", "2026-08-19T09:00:00", ObservedEventType.COMMIT_PUSHED),
    ]


def _sample(entry_id: str, occurred_at: str, kind: LedgerCategory) -> EventEntry:
    return EventEntry(
        entry_id=entry_id,
        category=kind,
        actor="pm-ai",
        fields=(("occurred_at", occurred_at),),
    )


def entry(marker: str) -> EventEntry:
    """A minimal typed entry, standing in for the old free-string append (2e).

    Uses a real category rather than inventing a `test` one: a tag existing only
    in fixtures is an entry no parser would ever be asked to read. Lived as four
    pasted copies across the suite until the 2026-08-30 review.
    """
    return EventEntry(
        category=SelfActionType.SECURITY, actor="test", fields=(("detail", marker),)
    )


def mask_ids(text: str) -> str:
    """Blank the minted surrogate so the rest of the grammar stays exact.

    The id is per-call by design (AD-34); dropping the content assertion rather
    than masking it would retire the only check that notices a format drift
    reaching disk.
    """
    import re

    return re.sub(r"evt_[0-9a-f]+", "evt_ID", text)
