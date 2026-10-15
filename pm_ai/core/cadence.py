"""When the scheduled harvest and the morning render run, and how they wait (9a, 9b).

Pure arithmetic: every instant is handed in and every random choice is a
`draw` in `[0, 1)` the caller supplies. Separate from the timer on purpose —
`pm_ai.app.ticks` is the in-memory timer story 10a deletes, and 10a's schedule
trigger needs the same cadence and waits, so this module is the part that
stays.

- **The cycle** (CAP-2): the next harvest of an instance is due
  `CYCLE` after the previous attempt *finished*, plus or minus up to
  `CYCLE_JITTER`, drawn afresh each time, so instances drift apart.
- **The start**: at daemon start the first attempts are spread across
  `START_SPREAD` rather than all at once.
- **The waits** after a failure that can clear on its own: 1, 2, 4, 8, …
  minutes, doubling, capped at `RETRY_CAP`, and never sooner than a wait the
  provider asked for. A failure that needs a human is not retried; the
  instance is tried again at its next regular cycle, which is not a retry.
- **Coverage** older than `COVERAGE_RETENTION` is pruned after each harvest.

No setting reads any of these: they are constants, and the cadence is not per
instance.

The morning render (story 9b):

- **07:00 in `display_timezone`**, once per local day per dashboard scope. A
  scope with no successful render since today's 07:00 is due as soon as it is
  past 07:00, so a daemon started at 09:30, or a laptop asleep through the
  morning, renders on arrival. 07:00 is the zone's wall clock, computed on the
  local date and converted, as the dashboard's own day is.
- **The look-back** of Proactive Enablement: to the last successful render of
  that scope; `FIRST_LOOK_BACK` when none is recorded; never longer than
  `LOOK_BACK_CAP` — a render weeks old, or one recorded later than now (a
  clock stepped back), is clamped, and the section says which.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from enum import Enum

from pm_ai.domain.harvest import HarvestFailure

__all__ = [
    "COVERAGE_RETENTION",
    "CYCLE",
    "CYCLE_JITTER",
    "FIRST_LOOK_BACK",
    "FIRST_RETRY",
    "LOOK_BACK_CAP",
    "LookBack",
    "LookBackKind",
    "RENDER_AT",
    "REFUSED_SAVE_RETRY",
    "RETRY_CAP",
    "START_SPREAD",
    "coverage_cutoff",
    "first_attempts",
    "latest_render",
    "look_back",
    "next_render",
    "next_cycle",
    "provider_floor",
    "render_due",
    "retries",
    "retry_wait",
    "todays_render",
]

CYCLE = timedelta(minutes=240)
"""CAP-2's four hours, from the end of one attempt to the start of the next."""

CYCLE_JITTER = timedelta(minutes=15)
"""How far either side of `CYCLE` each next attempt may fall, drawn each time."""

START_SPREAD = timedelta(minutes=15)
"""The span the first attempts after a daemon start are spread across."""

FIRST_RETRY = timedelta(minutes=1)
"""The first wait after a failure that can clear on its own."""

RETRY_CAP = timedelta(minutes=240)
"""The longest the doubling waits grow: one cycle."""

REFUSED_SAVE_RETRY = FIRST_RETRY
"""The wait after a save another command's claim or a busy store refused."""

COVERAGE_RETENTION = timedelta(days=90)
"""How long a coverage record is kept (story 16's verdict builder may shorten it).

Longer than any window a commitment's verification could need: CAP-34 alerts
48 hours before a milestone, and compaction starts at 7 days.
"""


def _draw(draw: float) -> float:
    if not 0.0 <= draw < 1.0:
        raise ValueError(f"a draw is a number in [0, 1), not {draw!r}")
    return draw


def next_cycle(finished: datetime, draw: float) -> datetime:
    """The next regular attempt: `CYCLE` after `finished`, ± up to `CYCLE_JITTER`.

    `draw` in `[0, 1)` places the offset uniformly across the span: 0 is the
    earliest, 0.5 exactly on the cycle.
    """
    return finished + CYCLE + CYCLE_JITTER * (2 * _draw(draw) - 1)


def first_attempts(instances: Sequence[str], start: datetime) -> dict[str, datetime]:
    """Each instance's first attempt after a daemon start, one after the other.

    Evenly across `START_SPREAD`, in the order given: the first at `start`,
    the rest spaced `START_SPREAD / n` apart, so none falls past it.
    """
    count = len(instances)
    return {
        instance: start + START_SPREAD * index / count
        for index, instance in enumerate(instances)
    }


def retry_wait(failures_in_a_row: int, asked: timedelta | None = None) -> timedelta:
    """The wait after the `failures_in_a_row`-th retryable failure in a row.

    1, 2, 4, 8, … minutes, capped at `RETRY_CAP`, and never less than `asked`,
    the wait the provider asked for.
    """
    if failures_in_a_row < 1:
        raise ValueError(
            f"a wait follows at least one failure, not {failures_in_a_row}"
        )
    # Capped before the power can grow without bound: 2**8 minutes already
    # passes the cap.
    ladder = min(FIRST_RETRY * 2 ** min(failures_in_a_row - 1, 16), RETRY_CAP)
    if asked is not None and asked > ladder:
        return asked
    return ladder


def retries(failure: HarvestFailure | None) -> bool:
    """Whether a recorded failure is one waiting can clear — retried on the ladder."""
    return failure is not None and failure.retryable


def provider_floor(failure: HarvestFailure | None) -> datetime | None:
    """The earliest instant a recorded failure's provider wait allows, if it asked.

    Read back after a restart, so the provider's wait is honoured across it;
    only the doubling starts over. `None` when the failure carries no wait, is
    not retryable, or has no recorded instant.
    """
    if (
        failure is None
        or not failure.retryable
        or failure.retry_after is None
        or failure.at is None
    ):
        return None
    return failure.at + failure.retry_after


def coverage_cutoff(now: datetime) -> datetime:
    """Coverage records ending before this instant are pruned."""
    return now - COVERAGE_RETENTION


# ── The morning render (story 9b) ────────────────────────────────────────────

RENDER_AT = time(7, 0)
"""CAP-9's 07:00, on the wall clock of `display_timezone`."""

FIRST_LOOK_BACK = timedelta(hours=24)
"""How far Proactive Enablement looks back when no render of the scope is recorded."""

LOOK_BACK_CAP = timedelta(days=7)
"""The longest Proactive Enablement ever looks back, whatever was recorded."""


def _render_on(day: date, zone: tzinfo) -> datetime:
    """07:00 on `day` in `zone`, as an aware UTC instant."""
    return datetime.combine(day, RENDER_AT, tzinfo=zone).astimezone(timezone.utc)


def todays_render(now: datetime, zone: tzinfo) -> datetime:
    """Today's 07:00 in `zone` — today being `now`'s local date there."""
    return _render_on(now.astimezone(zone).date(), zone)


def latest_render(now: datetime, zone: tzinfo) -> datetime:
    """The most recent 07:00 in `zone` at or before `now`: today's or yesterday's."""
    today = todays_render(now, zone)
    if now >= today:
        return today
    return _render_on(now.astimezone(zone).date() - timedelta(days=1), zone)


def next_render(now: datetime, zone: tzinfo) -> datetime:
    """The next 07:00 in `zone` strictly after `now`.

    Tomorrow is the next local date at 07:00, never `now` plus 24 hours, so a
    day with a DST change still renders at 07:00 on the wall clock.
    """
    today = todays_render(now, zone)
    if now < today:
        return today
    return _render_on(now.astimezone(zone).date() + timedelta(days=1), zone)


def render_due(last_success: datetime | None, now: datetime, zone: tzinfo) -> bool:
    """Whether a scope is due: past today's 07:00 with no success since it.

    A recorded success later than `now` — the clock was stepped back — stands
    for nothing: the scope is due, and its look-back is clamped
    (`look_back`). Otherwise a step back would leave it never due.
    """
    today = todays_render(now, zone)
    if now < today:
        return False
    return last_success is None or last_success < today or last_success > now


class LookBackKind(Enum):
    """Which of the four look-backs a render used; the section says which."""

    SINCE_LAST_RENDER = "since the last render"
    NO_RENDER_RECORDED = "no earlier render recorded"
    CLAMPED_OLDER = "the earlier render was older than the cap"
    CLAMPED_LATER = "the recorded render is later than this one"


@dataclass(frozen=True, slots=True)
class LookBack:
    """Where Proactive Enablement's window starts, and why there."""

    since: datetime
    kind: LookBackKind
    last_render: datetime | None = None
    """The recorded render it was computed from, when one was."""


def look_back(last_render: datetime | None, now: datetime) -> LookBack:
    """The window's start: the last render, 24 hours, or the 7-day clamp.

    A recorded render later than `now` — the clock was stepped back — is
    clamped like one older than the cap, and says so separately: neither
    "older" nor "since" would be true of it.
    """
    cap = now - LOOK_BACK_CAP
    if last_render is None:
        return LookBack(now - FIRST_LOOK_BACK, LookBackKind.NO_RENDER_RECORDED)
    if last_render > now:
        return LookBack(cap, LookBackKind.CLAMPED_LATER, last_render)
    if last_render < cap:
        return LookBack(cap, LookBackKind.CLAMPED_OLDER, last_render)
    return LookBack(last_render, LookBackKind.SINCE_LAST_RENDER, last_render)
