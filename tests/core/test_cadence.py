"""Story 9a — the scheduled harvest's arithmetic, which story 10a keeps.

Pure: every instant and every random draw is handed in.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pm_ai.core.cadence import (
    COVERAGE_RETENTION,
    CYCLE,
    CYCLE_JITTER,
    START_SPREAD,
    coverage_cutoff,
    first_attempts,
    next_cycle,
    provider_floor,
    retries,
    retry_wait,
)
from pm_ai.domain.harvest import HarvestFailure

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)


def test_the_constants_are_the_decided_ones():
    assert CYCLE == 240 * MINUTE
    assert CYCLE_JITTER == 15 * MINUTE
    assert START_SPREAD == 15 * MINUTE
    assert COVERAGE_RETENTION == timedelta(days=90)


@pytest.mark.parametrize(
    ("draw", "offset"),
    [(0.0, -15 * MINUTE), (0.5, timedelta(0)), (0.75, 7.5 * MINUTE)],
)
def test_the_next_cycle_is_240_minutes_after_finishing_give_or_take_15(draw, offset):
    assert next_cycle(T0, draw) == T0 + CYCLE + offset


def test_every_draw_lands_inside_the_jitter():
    for step in range(100):
        due = next_cycle(T0, step / 100)
        assert T0 + CYCLE - CYCLE_JITTER <= due < T0 + CYCLE + CYCLE_JITTER


@pytest.mark.parametrize("draw", [-0.1, 1.0, 2.0])
def test_a_draw_outside_zero_to_one_is_refused(draw):
    with pytest.raises(ValueError, match="draw"):
        next_cycle(T0, draw)


def test_first_attempts_are_spread_across_the_first_15_minutes_one_after_the_other():
    firsts = first_attempts(["graph:a", "gitlab:alpha"], T0)
    assert firsts == {"graph:a": T0, "gitlab:alpha": T0 + 7.5 * MINUTE}
    many = first_attempts([f"i{n}" for n in range(7)], T0)
    instants = list(many.values())
    assert instants == sorted(instants) and len(set(instants)) == 7
    assert all(T0 <= at < T0 + START_SPREAD for at in instants)
    assert first_attempts([], T0) == {}


def test_the_waits_double_from_one_minute_and_stop_at_240():
    waits = [retry_wait(n) for n in range(1, 11)]
    assert waits == [m * MINUTE for m in (1, 2, 4, 8, 16, 32, 64, 128, 240, 240)]
    assert retry_wait(10_000) == 240 * MINUTE


def test_a_wait_is_never_sooner_than_the_provider_asked():
    assert retry_wait(1, timedelta(seconds=600)) == 10 * MINUTE
    assert retry_wait(5, timedelta(seconds=600)) == 16 * MINUTE
    assert retry_wait(9, timedelta(hours=6)) == timedelta(hours=6)


def test_a_wait_follows_at_least_one_failure():
    with pytest.raises(ValueError):
        retry_wait(0)


def test_only_a_retryable_failure_retries():
    assert retries(HarvestFailure("503", retryable=True))
    assert not retries(HarvestFailure("expired", retryable=False))
    assert not retries(None)


def test_the_providers_wait_is_read_back_from_the_recorded_failure():
    asked = HarvestFailure("429", retryable=True, retry_after=timedelta(seconds=600), at=T0)
    assert provider_floor(asked) == T0 + 10 * MINUTE
    assert provider_floor(HarvestFailure("503", retryable=True, at=T0)) is None
    assert provider_floor(
        HarvestFailure("x", retryable=False, retry_after=timedelta(seconds=600), at=T0)
    ) is None
    assert provider_floor(HarvestFailure("x", retryable=True, retry_after=MINUTE)) is None
    assert provider_floor(None) is None


def test_coverage_older_than_90_days_is_cut_off():
    assert coverage_cutoff(T0) == T0 - timedelta(days=90)
