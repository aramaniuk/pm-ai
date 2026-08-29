"""CAP-10's weekly trend: counts by category, from the ledger itself.

Spec: `_bmad-output/specs/spec-pm-ai/stories/2k-retrospective-aggregation.md`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pm_ai.core.event_log import EventLog
from pm_ai.core.retrospective import weekly
from pm_ai.domain.event_entries import (
    EventEntry,
    SelfActionType,
    render_value,
    render_entry,
)
from pm_ai.domain.events import ObservedEventType
from pm_ai.domain.identity import DataScope, ScopeKind

SCOPE = DataScope(ScopeKind.PERSONAL)
INGESTED = "2026-08-19T09:00:00+00:00"


def _entry(category, *, occurred_at=None, ingested_at=INGESTED, flagged=False, eid="evt_1"):
    fields = (("ingested_at", ingested_at),)
    if occurred_at is not None:
        fields += (("occurred_at", occurred_at),)
    if flagged:
        fields += (("occurred_at_flag", "implausible"),)
    return EventEntry(entry_id=eid, category=category, actor="a", fields=fields)


class FakeStorage:
    def __init__(self, entries):
        self.text = "".join(render_entry(e) + "\n" for e in entries)

    def event_log_segments(self, *, scope):
        return ("2026-08.md",) if self.text else ()

    def read_event_log_segment(self, *, scope, name):
        return self.text


def _log(*entries):
    return EventLog(FakeStorage(entries))


# ── Weeks are ISO-8601, in UTC ──────────────────────────────────────────────


def test_entries_in_one_week_land_in_one_bucket():
    log = _log(
        _entry(ObservedEventType.DECISION, occurred_at="2026-08-17T10:00:00+00:00", eid="evt_1"),
        _entry(ObservedEventType.DECISION, occurred_at="2026-08-21T10:00:00+00:00", eid="evt_2"),
    )
    result = weekly(log, scope=SCOPE)
    assert len(result.weeks) == 1
    assert result.weeks[0]["decision"] == 2


def test_a_boundary_instant_belongs_to_the_week_it_opens():
    """ISO weeks start Monday 00:00 UTC. 2026-08-17 is a Monday."""
    log = _log(
        _entry(ObservedEventType.DECISION, occurred_at="2026-08-17T00:00:00+00:00", eid="evt_1"),
        _entry(ObservedEventType.DECISION, occurred_at="2026-08-16T23:59:59+00:00", eid="evt_2"),
    )
    result = weekly(log, scope=SCOPE)
    assert len(result.weeks) == 2, "the two instants are in different ISO weeks"
    assert result.weeks[0]["decision"] == 1
    assert result.weeks[1]["decision"] == 1


# ── A quiet week is a finding, not an omission ──────────────────────────────


def test_a_week_with_no_entries_is_present_with_zeros():
    log = _log(
        _entry(ObservedEventType.DECISION, occurred_at="2026-08-03T10:00:00+00:00", eid="evt_1"),
        _entry(ObservedEventType.DECISION, occurred_at="2026-08-24T10:00:00+00:00", eid="evt_2"),
    )
    result = weekly(log, scope=SCOPE)
    assert len(result.weeks) == 4, "two gap weeks between them must appear"
    assert [w["decision"] for w in result.weeks] == [1, 0, 0, 1]


def test_a_category_with_no_producer_counts_zero_and_is_listed():
    """The gap stays visible instead of looking like an absent feature."""
    log = _log(_entry(ObservedEventType.DECISION, occurred_at="2026-08-17T10:00:00+00:00"))
    bucket = weekly(log, scope=SCOPE).weeks[0]
    assert bucket["commit_pushed"] == 0
    assert "compaction" in dict(bucket.counts)


# ── The two clocks, per 2c's roles ──────────────────────────────────────────


def test_a_pm_ai_action_buckets_on_its_own_clock():
    """A `SelfActionType` record has no `occurred_at` by construction: pm-ai did
    the thing when it wrote the line, so `ingested_at` is its occurrence."""
    log = _log(_entry(SelfActionType.SKILL_INVOKED, ingested_at="2026-08-17T10:00:00+00:00"))
    result = weekly(log, scope=SCOPE)
    assert result.unplaceable == 0
    assert result.weeks[0]["skill_invoked"] == 1


def test_an_observed_event_without_a_provider_clock_is_unplaceable():
    log = _log(_entry(ObservedEventType.DECISION, occurred_at="unknown"))
    result = weekly(log, scope=SCOPE)
    assert result.unplaceable == 1
    assert all(w["decision"] == 0 for w in result.weeks)


def test_a_flagged_timestamp_is_unplaceable():
    """2b's flag says the provider clock cannot be believed; a future-dated week
    is not a week."""
    log = _log(
        _entry(
            ObservedEventType.DECISION,
            occurred_at="2027-08-17T10:00:00+00:00",
            flagged=True,
        )
    )
    assert weekly(log, scope=SCOPE).unplaceable == 1


def test_unplaceable_entries_are_never_counted_in_a_bucket():
    log = _log(
        _entry(ObservedEventType.DECISION, occurred_at="2026-08-17T10:00:00+00:00", eid="evt_1"),
        _entry(ObservedEventType.DECISION, occurred_at="unknown", eid="evt_2"),
    )
    result = weekly(log, scope=SCOPE)
    assert sum(w["decision"] for w in result.weeks) == 1
    assert result.unplaceable == 1


# ── Determinism, and the empty case ─────────────────────────────────────────


def test_the_same_ledger_aggregates_identically_twice():
    log = _log(
        _entry(ObservedEventType.DECISION, occurred_at="2026-08-21T10:00:00+00:00", eid="evt_2"),
        _entry(ObservedEventType.DECISION, occurred_at="2026-08-17T10:00:00+00:00", eid="evt_1"),
    )
    assert weekly(log, scope=SCOPE) == weekly(log, scope=SCOPE)


def test_an_empty_log_is_an_empty_trend_not_an_error():
    result = weekly(_log(), scope=SCOPE)
    assert result.weeks == ()
    assert result.unplaceable == 0
