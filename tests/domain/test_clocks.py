"""AD-35's two clock bases, and the refusal of an implausible provider timestamp.

Spec: `_bmad-output/specs/spec-pm-ai/stories/2a-two-clock-bases.md`.

Every expectation here is a hand-written literal. The module under test computes
nothing these assertions reuse, so an implementation that returns its input
unjudged fails rather than agreeing with itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pm_ai.domain import clocks

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


# ── The two bases ────────────────────────────────────────────────────────────
# The break these catch: a caller hard-codes the wrong field name, or a later
# edit swaps the two — the mixed-clock bug AD-35 exists to prevent.


def test_due_date_reasoning_names_the_provider_clock():
    assert clocks.due_date_basis() == "occurred_at"


def test_sweep_reasoning_names_the_local_clock():
    assert clocks.sweep_basis() == "ingested_at"


def test_the_two_bases_are_never_the_same_field():
    assert clocks.due_date_basis() != clocks.sweep_basis()


# ── The plausible range ──────────────────────────────────────────────────────


def test_a_past_timestamp_is_returned_unchanged():
    at = NOW - timedelta(hours=2)
    assert clocks.validate_occurred_at(at, now=NOW) == at


def test_a_timestamp_inside_the_skew_tolerance_is_accepted():
    """Provider clocks drift by seconds; that is not a bug to refuse."""
    at = NOW + timedelta(seconds=60)
    assert clocks.validate_occurred_at(at, now=NOW) == at


def test_a_future_dated_timestamp_is_refused():
    with pytest.raises(clocks.ImplausibleTimestamp):
        clocks.validate_occurred_at(NOW + timedelta(hours=48), now=NOW)


def test_the_future_refusal_names_the_timestamp_and_the_skew():
    """An operator must be able to tell a wrong timezone from a wrong year."""
    at = NOW + timedelta(hours=48)
    with pytest.raises(clocks.ImplausibleTimestamp) as caught:
        clocks.validate_occurred_at(at, now=NOW)
    message = str(caught.value)
    assert at.isoformat() in message
    assert "48" in message


def test_a_timestamp_below_the_floor_is_refused():
    """The zero-value parse: a null provider date arriving as the Unix epoch."""
    with pytest.raises(clocks.ImplausibleTimestamp):
        clocks.validate_occurred_at(datetime(1970, 1, 1, tzinfo=timezone.utc), now=NOW)


def test_the_floor_refusal_names_the_floor():
    with pytest.raises(clocks.ImplausibleTimestamp) as caught:
        clocks.validate_occurred_at(datetime(1970, 1, 1, tzinfo=timezone.utc), now=NOW)
    assert clocks.EARLIEST_PLAUSIBLE.isoformat() in str(caught.value)


# ── Absence is a state, not a failure ────────────────────────────────────────


def test_an_absent_timestamp_is_never_backfilled_from_the_local_clock():
    """AD-35's load-bearing rule: flagged, never silently substituted.

    An implementation that returns `now` here reintroduces exactly the
    substitution the whole module exists to forbid.
    """
    assert clocks.validate_occurred_at(None, now=NOW) is None


# ── Both operands must be comparable ─────────────────────────────────────────


def test_a_naive_timestamp_is_refused():
    with pytest.raises(clocks.ImplausibleTimestamp):
        clocks.validate_occurred_at(datetime(2026, 8, 29, 10, 0), now=NOW)


def test_a_non_utc_offset_timestamp_is_refused():
    at = datetime(2026, 8, 29, 10, 0, tzinfo=timezone(timedelta(hours=3)))
    with pytest.raises(clocks.ImplausibleTimestamp):
        clocks.validate_occurred_at(at, now=NOW)





# ── The offset spelling ──────────────────────────────────────────────────────
# Plausibility is a question about the distance from the reference instant, so
# the offset can be stated instead of computed. Same rule, no clock read.


def test_the_offset_spelling_refuses_an_implausible_skew():
    with pytest.raises(clocks.ImplausibleTimestamp):
        clocks.validate_occurred_at(future_by_hours=48)


def test_the_offset_spelling_accepts_a_skew_inside_tolerance():
    assert clocks.validate_occurred_at(future_by_hours=0) is None


def test_supplying_both_spellings_is_a_caller_error():
    with pytest.raises(ValueError):
        clocks.validate_occurred_at(NOW, now=NOW, future_by_hours=1)


def test_supplying_neither_spelling_is_a_caller_error():
    with pytest.raises(ValueError):
        clocks.validate_occurred_at()


def test_a_timestamp_without_a_reference_instant_is_a_caller_error():
    with pytest.raises(ValueError):
        clocks.validate_occurred_at(NOW)


# ── The module reads no clock of its own ─────────────────────────────────────


def test_the_tolerance_is_a_named_constant_not_a_literal():
    """The break: a second comparison somewhere with a different hard-coded bound."""
    assert isinstance(clocks.FUTURE_SKEW_TOLERANCE, timedelta)
    assert clocks.FUTURE_SKEW_TOLERANCE > timedelta(seconds=60)
    assert clocks.FUTURE_SKEW_TOLERANCE < timedelta(hours=1)


# ── Review findings, 2026-08-30 ─────────────────────────────────────────────


def test_the_offset_spelling_refuses_a_non_finite_offset():
    """`nan` compares False against the tolerance, so it passed as plausible."""
    for bad in (float("nan"), float("inf")):
        with pytest.raises(clocks.ImplausibleTimestamp):
            clocks.validate_occurred_at(future_by_hours=bad)


def test_the_offset_spelling_accepts_a_past_offset_and_says_why():
    """It cannot apply `EARLIEST_PLAUSIBLE`: with no reference instant there is no
    date to compare against. The docstring claimed "two spellings, one rule",
    which was true only of the skew half."""
    assert clocks.validate_occurred_at(future_by_hours=-1_000_000) is None


def test_a_naive_reference_instant_is_a_caller_error_not_bad_data():
    """The docstring says a bad reference is "a caller error rather than an
    implausible timestamp — the data is not what is wrong". It raised
    `ImplausibleTimestamp` anyway, which blamed the provider for our own bug."""
    with pytest.raises(ValueError) as caught:
        clocks.validate_occurred_at(NOW, now=datetime(2026, 8, 29, 12, 0))
    assert not isinstance(caught.value, clocks.ImplausibleTimestamp)
    assert "now" in str(caught.value)
