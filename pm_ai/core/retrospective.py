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
from datetime import datetime, timezone

from pm_ai.core.event_log import EventLog
from pm_ai.domain.event_entries import (
    OCCURRED_AT_FLAG,
    UNKNOWN_VALUE,
    EventEntry,
    SelfActionType,
)
from pm_ai.domain.events import ObservedEventType
from pm_ai.domain.identity import DataScope

__all__ = ["Retrospective", "WeekBucket", "weekly"]



@dataclass(frozen=True, slots=True)
class WeekBucket:
    """One ISO week, and what the ledger recorded in it."""

    iso_year: int
    iso_week: int
    counts: tuple[tuple[str, int], ...]

    def __getitem__(self, category: str) -> int:
        for name, count in self.counts:
            if name == category:
                return count
        raise KeyError(category)


@dataclass(frozen=True, slots=True)
class Retrospective:
    weeks: tuple[WeekBucket, ...]
    unplaceable: int
    undated_actions: int = 0
    """pm-ai actions whose own clock is missing or unreadable.

    Counted apart from `unplaceable` rather than folded into it, because the two
    are different failures: an observed event with no provider clock is data we
    were given badly, and this is a record *we* wrote without stamping. Merging
    them would make the first figure grow for a reason it does not describe.
    """
    """Observed events whose provider clock is absent or unbelievable.

    Reported rather than dropped: a retrospective that quietly omits records is
    worse than one that says how many it could not place. It counts *only*
    observed events — a pm-ai action has no `occurred_at` by construction and is
    placed by its own clock, not filed here.
    """


def weekly(
    log: EventLog,
    *,
    scope: DataScope,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Retrospective:
    """Group a scope's ledger into ISO weeks and count each category.

    The read is unbounded even though the result is bucketed. 2h's range is on
    `ingested_at` and these buckets are on `occurred_at`, so narrowing by segment
    would drop a July event ingested in August — correct here means a full scan,
    and story 18's index is where that stops being acceptable.

    Weeks are ISO-8601 in UTC, from `isocalendar()`, so a boundary instant belongs
    to the week it opens. Hand-rolling that would be a second definition of a week,
    able to disagree with every other tool the PM reads the same dates in. Every
    timestamp is converted to UTC before its week is read — `isocalendar()` on an
    offset-carrying value answers for its own wall clock, so two records at the
    same instant could land in different weeks.

    `since`/`until` bound the *trend*, not the read. Without them the span runs
    from the first entry to the last, so a quiet week at either edge is simply
    absent — which is the failure the zero-count rule exists to prevent, moved to
    the ends. They also bound the output: a ledger spanning years otherwise
    yields a bucket per week with no way to ask for fewer. The bounds are on the
    same clock as the buckets, so they carry no clock in their names.
    """
    placed: dict[tuple[int, int], dict[str, int]] = {}
    unplaceable = undated_actions = 0

    for entry in log.read(scope=scope):
        at = _placement(entry)
        if at is None:
            if isinstance(entry.category, SelfActionType):
                undated_actions += 1
            else:
                unplaceable += 1
            continue
        at = at.astimezone(timezone.utc)
        if (since is not None and at < since) or (until is not None and at > until):
            continue
        key = (at.isocalendar().year, at.isocalendar().week)
        bucket = placed.setdefault(key, {})
        bucket[entry.category.value] = bucket.get(entry.category.value, 0) + 1

    return Retrospective(
        weeks=_fill(placed, since, until),
        unplaceable=unplaceable,
        undated_actions=undated_actions,
    )


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
    if fields.get(OCCURRED_AT_FLAG):
        return None
    raw = fields.get("occurred_at")
    return None if raw is None or raw == UNKNOWN_VALUE else _parse(raw)


def _parse(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _fill(
    placed: dict[tuple[int, int], dict[str, int]],
    since: datetime | None,
    until: datetime | None,
) -> tuple[WeekBucket, ...]:
    """Every week between the first and the last, including the quiet ones.

    A gap week is present with zeros because a quiet week is a finding — omitting
    it turns "nothing happened" into "we did not look", and those read the same
    on a trend line.
    """
    if not placed and (since is None or until is None):
        return ()
    categories = sorted(
        [member.value for member in ObservedEventType]
        + [member.value for member in SelfActionType]
    )
    ordered = sorted(placed)
    first = _week_of(since) if since is not None else ordered[0]
    last = _week_of(until) if until is not None else ordered[-1]
    weeks = []
    for key in _span(first, last):
        counts = placed.get(key, {})
        weeks.append(
            WeekBucket(
                iso_year=key[0],
                iso_week=key[1],
                counts=tuple((name, counts.get(name, 0)) for name in categories),
            )
        )
    return tuple(weeks)


def _week_of(at: datetime) -> tuple[int, int]:
    utc = at.astimezone(timezone.utc)
    return (utc.isocalendar().year, utc.isocalendar().week)


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
