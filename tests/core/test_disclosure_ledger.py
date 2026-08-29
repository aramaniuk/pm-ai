"""Reading `disclosure.md` back: AD-17's monthly total and AD-31's audit query.

Spec: `_bmad-output/specs/spec-pm-ai/stories/2j-disclosure-ledger-reads.md`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pm_ai.core.disclosure_ledger import DisclosureLedger, parse_ledger
from pm_ai.domain.disclosure import (
    DisclosureRecord,
    MalformedDisclosure,
    render_disclosure,
)
from pm_ai.domain.identity import DataScope, ScopeKind

PERSONAL = DataScope(ScopeKind.PERSONAL)
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")


def _record(at, cost=0.01, **kw):
    base = dict(
        at=at,
        task_class="summarize",
        model="claude-opus-5",
        contributing_scopes=frozenset({PERSONAL}),
        input_tokens=100,
        output_tokens=50,
        estimated_cost_usd=cost,
    )
    return DisclosureRecord(**{**base, **kw})


def _text(*records) -> str:
    return "".join(render_disclosure(r) + "\n" for r in records)


class FakeStorage:
    def __init__(self, text=""):
        self.text = text

    def read_disclosure(self) -> str:
        return self.text


# ── Parse is the inverse of 2i's renderer ───────────────────────────────────


@pytest.mark.parametrize(
    "record",
    [
        _record(datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc)),
        _record(datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc), destination=PROJECT),
        _record(
            datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc),
            contributing_scopes=frozenset({PERSONAL, PROJECT}),
        ),
        _record(datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc), task_class="weekly brief"),
        _record(datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc), cost=0.0),
    ],
    ids=["plain", "destination", "two-scopes", "quoted-value", "zero-cost"],
)
def test_render_then_parse_returns_the_original(record):
    assert parse_ledger(_text(record)) == (record,)


def test_the_cost_survives_at_full_precision():
    """AD-17's total is evidence; a rounded round-trip makes it unjustifiable."""
    record = _record(datetime(2026, 8, 1, tzinfo=timezone.utc), cost=0.000123456789)
    assert parse_ledger(_text(record))[0].estimated_cost_usd == 0.000123456789


# ── The append rule, same as the event log's ────────────────────────────────


def test_an_unterminated_tail_is_dropped():
    whole = _record(datetime(2026, 8, 1, tzinfo=timezone.utc))
    text = _text(whole) + "- at=2026-08-02T00:00:00+00:00 task_class=sum"
    assert parse_ledger(text) == (whole,)


def test_an_empty_ledger_parses_empty():
    assert parse_ledger("") == ()


def test_a_malformed_complete_record_is_refused_with_its_line():
    with pytest.raises(MalformedDisclosure) as caught:
        parse_ledger(_text(_record(datetime(2026, 8, 1, tzinfo=timezone.utc)))
                     + "not a disclosure\n")
    assert "2" in str(caught.value)


# ── AD-17: the monthly total, recomputed every time ─────────────────────────


def _ledger(*records):
    return DisclosureLedger(FakeStorage(_text(*records)))


def test_the_monthly_total_counts_only_the_month_asked_for():
    log = _ledger(
        _record(datetime(2026, 7, 31, 23, 0, tzinfo=timezone.utc), cost=1.0),
        _record(datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc), cost=2.0),
        _record(datetime(2026, 8, 31, 23, 0, tzinfo=timezone.utc), cost=4.0),
        _record(datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc), cost=8.0),
    )
    total = log.monthly_total(2026, 8)
    assert total.cost_usd == 6.0
    assert total.records == 2
    assert total.input_tokens == 200 and total.output_tokens == 100


def test_an_empty_ledger_totals_zero_rather_than_failing():
    total = DisclosureLedger(FakeStorage("")).monthly_total(2026, 8)
    assert total.cost_usd == 0.0 and total.records == 0


def test_a_total_over_the_target_reports_the_breach_and_blocks_nothing():
    log = _ledger(_record(datetime(2026, 8, 1, tzinfo=timezone.utc), cost=25.0))
    total = log.monthly_total(2026, 8, target_usd=20.0)
    assert total.breached is True


def test_a_total_under_the_target_is_not_breached():
    log = _ledger(_record(datetime(2026, 8, 1, tzinfo=timezone.utc), cost=5.0))
    assert log.monthly_total(2026, 8, target_usd=20.0).breached is False


def test_no_target_leaves_the_breach_unknown_rather_than_false():
    """`nothing was compared` and `nothing was exceeded` are different facts."""
    log = _ledger(_record(datetime(2026, 8, 1, tzinfo=timezone.utc), cost=25.0))
    assert log.monthly_total(2026, 8).breached is None


def test_a_ledger_truncated_mid_append_still_totals():
    log = DisclosureLedger(
        FakeStorage(
            _text(_record(datetime(2026, 8, 1, tzinfo=timezone.utc), cost=3.0))
            + "- at=2026-08-02T00:00:00+00:00 task_cl"
        )
    )
    assert log.monthly_total(2026, 8).cost_usd == 3.0


# ── AD-31: what has left this machine, over a period ────────────────────────


def test_a_period_query_returns_records_in_ledger_order():
    early = _record(datetime(2026, 8, 10, tzinfo=timezone.utc), cost=1.0)
    late = _record(datetime(2026, 8, 20, tzinfo=timezone.utc), cost=2.0)
    log = _ledger(early, late)

    got = log.records(since=datetime(2026, 8, 15, tzinfo=timezone.utc))
    assert got == (late,)


def test_a_period_query_is_inclusive_of_its_bounds():
    at = datetime(2026, 8, 15, tzinfo=timezone.utc)
    log = _ledger(_record(at))
    assert len(log.records(since=at, until=at)) == 1


def test_an_unbounded_query_returns_everything():
    log = _ledger(
        _record(datetime(2026, 8, 10, tzinfo=timezone.utc)),
        _record(datetime(2026, 8, 20, tzinfo=timezone.utc)),
    )
    assert len(log.records()) == 2
