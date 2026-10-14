"""When the scheduled harvest runs, and how long it waits after a failure (story 9a).

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
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from pm_ai.domain.harvest import HarvestFailure

__all__ = [
    "COVERAGE_RETENTION",
    "CYCLE",
    "CYCLE_JITTER",
    "FIRST_RETRY",
    "REFUSED_SAVE_RETRY",
    "RETRY_CAP",
    "START_SPREAD",
    "coverage_cutoff",
    "first_attempts",
    "next_cycle",
    "provider_floor",
    "retries",
    "retry_wait",
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
