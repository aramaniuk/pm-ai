"""The five-second rule as one function in the I/O-free core (AD-21, story 4r)."""

from __future__ import annotations

import re

import pytest

from pm_ai.app import pipelines
from pm_ai.core.dispatch import INLINE_LIMIT_SECONDS, JobState, mint_job_id, plan


@pytest.mark.parametrize(
    ("seconds", "inline"),
    [(0.0, True), (1.0, True), (4.99, True), (5.0, False), (42.0, False)],
    ids=["zero", "one", "just-under", "exactly-five", "forty-two"],
)
def test_under_five_seconds_is_inline_and_five_or_over_is_a_job(seconds, inline):
    decided = plan(estimated_seconds=seconds)
    assert decided.inline is inline
    if inline:
        assert decided.job_id is None
    else:
        assert decided.job_id is not None and re.fullmatch(r"job_[0-9a-f]{16}", decided.job_id)


def test_the_line_is_five_seconds():
    assert INLINE_LIMIT_SECONDS == 5.0


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
def test_a_bound_nothing_can_honour_is_refused_not_read_as_fast(bad):
    with pytest.raises(ValueError, match="finite, non-negative"):
        plan(estimated_seconds=bad)


def test_job_ids_are_random_hex_never_reused():
    minted = {mint_job_id() for _ in range(500)}
    assert len(minted) == 500
    assert all(re.fullmatch(r"job_[0-9a-f]{16}", job) for job in minted)


def test_the_four_states_and_which_are_finished():
    assert [state.value for state in JobState] == ["queued", "running", "done", "failed"]
    assert [state.finished for state in JobState] == [False, False, True, True]


def test_the_dashboard_bound_is_the_fetch_budget_per_calendar_in_sequence():
    budget = pipelines.CALENDAR_FETCH_BUDGET_SECONDS
    assert budget == 600.0
    assert pipelines.dashboard_bound(0) == budget
    assert pipelines.dashboard_bound(1) == budget
    assert pipelines.dashboard_bound(3) == 3 * budget
    # So `dashboard` through the daemon is the real job, not a stand-in.
    assert plan(estimated_seconds=pipelines.dashboard_bound(0)).inline is False


def test_connector_check_declares_its_ten_seconds():
    assert pipelines.CONNECTOR_CHECK_BOUND_SECONDS == 10.0
    assert plan(estimated_seconds=pipelines.CONNECTOR_CHECK_BOUND_SECONDS).inline is False
