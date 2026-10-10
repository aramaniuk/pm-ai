"""`HarvestResult.answered_at` — the proof that the provider answered (slice `8p`).

One test per row of the story's matrix that the type itself decides. The rows
about what each connector *reports* live beside that connector's tests
(`tests/connectors/test_coverage_honesty.py`, `test_graph_calendar_fetch.py`,
`test_graph_calendar_mapping.py`); these pin what the type accepts and refuses,
whoever builds it.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from pm_ai.domain.harvest import (
    Cursor,
    HarvestFailure,
    HarvestOutcome,
    HarvestResult,
)
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.lifecycle import CoverageWindow
from pm_ai.domain.meetings import Meeting
from pm_ai.storage.service import StorageService

INSTANCE = "gitlab:alpha"
ANSWERED = datetime(2026, 10, 9, 9, 0, 4, tzinfo=timezone.utc)
"""The instant the first page was read — the matrix's 09:00:04."""

FINISHED = datetime(2026, 10, 9, 9, 0, 6, tzinfo=timezone.utc)

WINDOW = CoverageWindow(INSTANCE, ANSWERED, FINISHED)

MEETING = Meeting(
    meeting_id="AAMk-one",
    title="a meeting a harvest mapped",
    start=ANSWERED - timedelta(hours=1),
    duration_minutes=30,
    attendees=(),
    scope=DataScope(ScopeKind.PERSONAL),
)

FAILURE = HarvestFailure(reason="the provider answered 502", retryable=True)


def _result(**kwargs) -> HarvestResult:
    return HarvestResult(events=(), cursor=Cursor(), **kwargs)


# ── Accepted ─────────────────────────────────────────────────────────────────


def test_a_quiet_provider_is_empty_with_the_instant_and_a_window_starting_there():
    """Matrix: quiet provider — empty page, instant read."""
    result = _result(outcome=HarvestOutcome.EMPTY, coverage=WINDOW, answered_at=ANSWERED)

    assert result.answered_at == ANSWERED
    assert result.coverage is not None and result.coverage.start == result.answered_at


def test_a_harvest_with_rows_carries_the_first_pages_instant():
    """Matrix: harvested with rows — page one read at 09:00:04."""
    result = _result(
        outcome=HarvestOutcome.HARVESTED,
        records=(MEETING,),
        coverage=WINDOW,
        answered_at=ANSWERED,
    )

    assert result.answered_at == ANSWERED
    assert result.coverage == WINDOW


def test_a_failure_before_any_page_has_no_instant_and_no_window():
    """Matrix: failed before any page — the failure is carried, nothing else."""
    result = _result(outcome=HarvestOutcome.FAILED, failure=FAILURE)

    assert result.answered_at is None
    assert result.coverage is None
    assert result.failure is FAILURE


def test_a_failure_after_page_one_keeps_the_instant_the_window_and_the_rows():
    """Matrix: failed after page one — page two 502, page one's evidence kept."""
    result = _result(
        outcome=HarvestOutcome.FAILED,
        failure=FAILURE,
        records=(MEETING,),
        coverage=WINDOW,
        answered_at=ANSWERED,
    )

    assert result.answered_at == ANSWERED
    assert result.coverage == WINDOW
    assert result.records == (MEETING,)


def test_a_clock_that_stepped_back_keeps_the_instant_without_a_window():
    """Matrix: clock stepped back — the window is withheld, the instant stays."""
    result = _result(
        outcome=HarvestOutcome.HARVESTED, records=(MEETING,), answered_at=ANSWERED
    )

    assert result.answered_at == ANSWERED
    assert result.coverage is None


# ── Refused ──────────────────────────────────────────────────────────────────


def test_found_nothing_without_an_answer_is_refused_naming_the_missing_instant():
    """Matrix: empty claimed without an answer."""
    with pytest.raises(ValueError, match="outcome=empty with no answered_at"):
        _result(outcome=HarvestOutcome.EMPTY)


def test_found_nothing_with_a_window_but_no_instant_is_still_refused():
    """The EMPTY refusal does not depend on a window being present either way."""
    with pytest.raises(ValueError, match="outcome=empty with no answered_at"):
        _result(outcome=HarvestOutcome.EMPTY, coverage=WINDOW)


def test_a_window_without_an_instant_is_refused():
    """Matrix: window without an instant — under an outcome that may carry one."""
    with pytest.raises(ValueError, match="claimed with no answered_at"):
        _result(outcome=HarvestOutcome.HARVESTED, records=(MEETING,), coverage=WINDOW)


@pytest.mark.parametrize("off_by", [timedelta(seconds=1), -timedelta(seconds=1)])
def test_a_window_not_starting_at_the_instant_is_refused(off_by):
    """Matrix: window start one second off, either side."""
    with pytest.raises(ValueError, match="must be exactly the instant"):
        _result(
            outcome=HarvestOutcome.EMPTY,
            coverage=CoverageWindow(INSTANCE, ANSWERED + off_by, FINISHED),
            answered_at=ANSWERED,
        )


def test_a_window_not_starting_at_the_instant_is_refused_under_harvested_too():
    """The rule is about the window, not the outcome: rows do not excuse it."""
    with pytest.raises(ValueError, match="must be exactly the instant"):
        _result(
            outcome=HarvestOutcome.HARVESTED,
            records=(MEETING,),
            coverage=CoverageWindow(INSTANCE, ANSWERED + timedelta(seconds=1), FINISHED),
            answered_at=ANSWERED,
        )


def test_found_nothing_with_an_instant_and_no_window_is_accepted():
    """The clock-stepped-back shape on a quiet provider: answered, no interval."""
    result = _result(outcome=HarvestOutcome.EMPTY, answered_at=ANSWERED)

    assert result.answered_at == ANSWERED
    assert result.coverage is None


@pytest.mark.parametrize(
    "instant",
    [
        ANSWERED.replace(tzinfo=None),
        ANSWERED.astimezone(timezone(timedelta(hours=1))),
    ],
    ids=["naive", "utc+01:00"],
)
def test_an_instant_that_is_not_aware_utc_is_refused_with_its_own_message(instant):
    """Matrix: instant not aware UTC — refused alone, with no window at all."""
    with pytest.raises(ValueError, match="is not aware UTC"):
        _result(
            outcome=HarvestOutcome.HARVESTED, records=(MEETING,), answered_at=instant
        )


@pytest.mark.parametrize(
    "instant",
    [
        ANSWERED.replace(tzinfo=None),
        ANSWERED.astimezone(timezone(timedelta(hours=1))),
    ],
    ids=["naive", "utc+01:00"],
)
def test_the_utc_refusal_comes_before_the_window_start_comparison(instant):
    """With a window too, the shape is reported, not the mismatch it causes.

    The `+01:00` value names the same instant as the window's start, so a
    comparison would pass it; the naive one cannot be compared at all. Either
    way the message is the UTC one.
    """
    with pytest.raises(ValueError, match="is not aware UTC"):
        _result(outcome=HarvestOutcome.EMPTY, coverage=WINDOW, answered_at=instant)


# ── Nothing on disk changes ──────────────────────────────────────────────────


def test_the_stored_shape_takes_no_instant():
    """Matrix: stored shape — the cursor, the window and the failure, as before.

    No result is stored or replayed; the window already begins at the instant,
    so storage has nothing new to keep.
    """
    assert list(inspect.signature(StorageService.save_cursor).parameters) == [
        "self",
        "instance",
        "cursor",
        "coverage",
        "failure",
    ]
