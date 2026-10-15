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


# ── The morning render (story 9b) ────────────────────────────────────────────

from zoneinfo import ZoneInfo  # noqa: E402

from pm_ai.core.cadence import (  # noqa: E402
    FIRST_LOOK_BACK,
    LOOK_BACK_CAP,
    RENDER_AT,
    LookBackKind,
    look_back,
    next_render,
    render_due,
    todays_render,
)

WARSAW = ZoneInfo("Europe/Warsaw")


def test_the_render_constants_are_the_decided_ones():
    assert (RENDER_AT.hour, RENDER_AT.minute) == (7, 0)
    assert FIRST_LOOK_BACK == timedelta(hours=24)
    assert LOOK_BACK_CAP == timedelta(days=7)


def test_07_00_is_the_zones_wall_clock_on_both_sides_of_a_change():
    # Europe/Warsaw: CEST (+2) until 2026-10-25 03:00, CET (+1) after.
    before = datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc)
    assert todays_render(before, WARSAW) == datetime(2026, 10, 24, 5, 0, tzinfo=timezone.utc)
    assert next_render(before, WARSAW) == datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc)
    spring = datetime(2026, 3, 28, 8, 0, tzinfo=timezone.utc)
    assert next_render(spring, WARSAW) == datetime(2026, 3, 29, 5, 0, tzinfo=timezone.utc)


def test_the_next_render_is_today_before_07_00_and_tomorrow_from_it():
    seven = datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc)
    assert next_render(seven - MINUTE, WARSAW) == seven
    assert next_render(seven, WARSAW) == seven + timedelta(days=1)


def test_today_is_the_local_date_not_the_utc_one():
    # 23:30 UTC on the 13th is 01:30 on the 14th in Warsaw.
    late = datetime(2026, 10, 13, 23, 30, tzinfo=timezone.utc)
    assert todays_render(late, WARSAW) == datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("last", "now", "due"),
    [
        (None, datetime(2026, 10, 14, 4, 59, tzinfo=timezone.utc), False),
        (None, datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc), True),
        (datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc), datetime(2026, 10, 14, 7, 30, tzinfo=timezone.utc), True),
        (datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc), datetime(2026, 10, 14, 7, 30, tzinfo=timezone.utc), False),
        # A recorded success later than now: the clock was stepped back — due.
        (datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc), datetime(2026, 10, 14, 7, 30, tzinfo=timezone.utc), True),
    ],
)
def test_a_scope_is_due_past_07_00_with_no_success_since_it(last, now, due):
    assert render_due(last, now, WARSAW) is due


def test_the_look_back_is_the_last_render_24_hours_or_clamped_to_7_days():
    assert look_back(None, T0).since == T0 - FIRST_LOOK_BACK
    assert look_back(None, T0).kind is LookBackKind.NO_RENDER_RECORDED
    friday = T0 - timedelta(days=3)
    assert look_back(friday, T0).since == friday
    assert look_back(friday, T0).kind is LookBackKind.SINCE_LAST_RENDER
    old = T0 - timedelta(days=21)
    assert look_back(old, T0).since == T0 - LOOK_BACK_CAP
    assert look_back(old, T0).kind is LookBackKind.CLAMPED_OLDER
    ahead = T0 + timedelta(days=1)
    assert look_back(ahead, T0).since == T0 - LOOK_BACK_CAP
    assert look_back(ahead, T0).kind is LookBackKind.CLAMPED_LATER
    exactly = T0 - LOOK_BACK_CAP
    assert look_back(exactly, T0).kind is LookBackKind.SINCE_LAST_RENDER
