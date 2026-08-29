"""CAP-10's weekly trend: counts by category, computed from the ledger itself.

CAP-10 asks for `pm-ai retrospective --weekly` to render "counts by category
(decisions logged, proposals staged vs approved, commitments fulfilled vs broken)
as a weekly trend". This is the counting. The surface that renders it is story 4's.

**Counted from the ledger on every call, never stored.** A stored count is a
second structure that can disagree with the records it summarises, and CAP-10's
guarantee is that the record is the truth. Story 18's search index is where this
goes if the read ever becomes expensive.

**Three of the four named categories have no producer yet** — proposals belong to
the proposal lifecycle and commitments to story 15 — so the shape is complete and
the counts fill in as those land. Every category is listed even at zero, which is
what keeps that gap visible instead of looking like an absent feature.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pm_ai.core.event_log import EventLog
from pm_ai.domain.event_entries import EventEntry, SelfActionType
from pm_ai.domain.events import ObservedEventType
from pm_ai.domain.identity import DataScope

__all__ = ["Retrospective", "WeekBucket", "weekly"]

_UNKNOWN = "unknown"
_FLAG = "occurred_at_flag"


@dataclass(frozen=True, slots=True)
class WeekBucket:
    """One ISO week, and what the ledger recorded in it."""

    iso_year: int
    iso_week: int
    counts: tuple[tuple[str, int], ...]

    def __getitem__(self, category: str) -> int:
        return dict(self.counts)[category]


@dataclass(frozen=True, slots=True)
class Retrospective:
    weeks: tuple[WeekBucket, ...]
    unplaceable: int
    """Observed events whose provider clock is absent or unbelievable.

    Reported rather than dropped: a retrospective that quietly omits records is
    worse than one that says how many it could not place. It counts *only*
    observed events — a pm-ai action has no `occurred_at` by construction and is
    placed by its own clock, not filed here.
    """


def weekly(log: EventLog, *, scope: DataScope) -> Retrospective:
    """Group a scope's ledger into ISO weeks and count each category.

    The read is unbounded even though the result is bucketed. 2h's range is on
    `ingested_at` and these buckets are on `occurred_at`, so narrowing by segment
    would drop a July event ingested in August — correct here means a full scan,
    and story 18's index is where that stops being acceptable.

    Weeks are ISO-8601 in UTC, from `isocalendar()`, so a boundary instant belongs
    to the week it opens. Hand-rolling that would be a second definition of a week,
    able to disagree with every other tool the PM reads the same dates in.
    """
    placed: dict[tuple[int, int], dict[str, int]] = {}
    unplaceable = 0

    for entry in log.read(scope=scope):
        at = _placement(entry)
        if at is None:
            unplaceable += 1
            continue
        key = (at.isocalendar().year, at.isocalendar().week)
        bucket = placed.setdefault(key, {})
        bucket[entry.category.value] = bucket.get(entry.category.value, 0) + 1

    return Retrospective(weeks=_fill(placed), unplaceable=unplaceable)


def _placement(entry: EventEntry) -> datetime | None:
    """Which clock places this record — or `None` if nothing can.

    2c's roles decide it. A `SelfActionType` record's subject is pm-ai and pm-ai
    is its only witness, so it has no `occurred_at` and never will: the write *is*
    the occurrence, and `ingested_at` is that record's own domain clock rather
    than a stand-in for a missing one. Filing every skill invocation and every
    compaction as unplaceable would report the whole of pm-ai's own activity as a
    measurement failure.

    An *observed* event is different. Its subject is outside pm-ai, so an absent
    or unbelievable provider clock means the two clocks genuinely differ, and
    substituting ours is the AD-35 defect.
    """
    fields = dict(entry.fields)
    if isinstance(entry.category, SelfActionType):
        return _parse(fields.get("ingested_at"))
    if fields.get(_FLAG):
        return None
    raw = fields.get("occurred_at")
    return None if raw is None or raw == _UNKNOWN else _parse(raw)


def _parse(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _fill(placed: dict[tuple[int, int], dict[str, int]]) -> tuple[WeekBucket, ...]:
    """Every week between the first and the last, including the quiet ones.

    A gap week is present with zeros because a quiet week is a finding — omitting
    it turns "nothing happened" into "we did not look", and those read the same
    on a trend line.
    """
    if not placed:
        return ()
    categories = sorted(
        [member.value for member in ObservedEventType]
        + [member.value for member in SelfActionType]
    )
    ordered = sorted(placed)
    weeks = []
    for key in _span(ordered[0], ordered[-1]):
        counts = placed.get(key, {})
        weeks.append(
            WeekBucket(
                iso_year=key[0],
                iso_week=key[1],
                counts=tuple((name, counts.get(name, 0)) for name in categories),
            )
        )
    return tuple(weeks)


def _span(first: tuple[int, int], last: tuple[int, int]) -> list[tuple[int, int]]:
    """Every ISO week from `first` to `last` inclusive.

    Walked a week at a time from a real date rather than by incrementing the week
    number, because an ISO year has 52 or 53 weeks and hard-coding either is wrong
    one year in five or so.
    """
    from datetime import date, timedelta

    start = date.fromisocalendar(first[0], first[1], 1)
    end = date.fromisocalendar(last[0], last[1], 1)
    keys = []
    while start <= end:
        keys.append((start.isocalendar().year, start.isocalendar().week))
        start += timedelta(weeks=1)
    return keys
