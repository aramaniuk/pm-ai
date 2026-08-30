"""Which clock governs which reasoning, and when a provider clock is not credible.

AD-35 fixes two clocks and forbids substituting one for the other:

- **`occurred_at`** — when the thing happened in the world. Provider-supplied,
  possibly skewed, possibly absent. It governs **domain reasoning**: due dates,
  "did the commit follow the promise", ordering within a meeting.
- **`ingested_at`** — assigned locally by the storage service at persist time.
  It governs **operational reasoning**: cursors, watermarks, replay, sweeps.

`NormalizedEvent` has carried both fields since the envelope was defined, and
storage has stamped `ingested_at` since the batch writer existed. What no object
stated is *which governs what* — so a verifier comparing `occurred_at` to a due
date while a sweeper reasoned in `ingested_at` was a spelling mistake away, and
the consequence AD-35 names is irreversible: a laptop asleep over a weekend
firing "why isn't this done" messages about work already delivered.

The second rule is the sharper one. An implausible `occurred_at` is **flagged,
never backfilled** from `ingested_at`. Substituting the local clock produces a
timestamp that is well-formed, plausible, and wrong — and nothing downstream can
tell it from a real one. Refusing is recoverable; silently inventing is not.

Imports nothing from `pm_ai` at all, and reads no clock (AD-30). A domain module
that called `datetime.now()` would be the second clock read in a codebase whose
storage service exists to have exactly one, and would be untestable at the only
boundary that matters.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

__all__ = [
    "EARLIEST_PLAUSIBLE",
    "FUTURE_SKEW_TOLERANCE",
    "ImplausibleTimestamp",
    "due_date_basis",
    "sweep_basis",
    "validate_occurred_at",
]


FUTURE_SKEW_TOLERANCE = timedelta(minutes=5)
"""How far ahead of the reference instant a provider clock may legitimately sit.

NTP skew is seconds and provider publication lag is minutes, so five minutes
absorbs the drift that is real. An hour is already a timezone bug and two days is
a wrong-year bug — those are the cases worth refusing. Everything between is a
judgement, and it is made here once rather than at each comparison.
"""

EARLIEST_PLAUSIBLE = datetime(2000, 1, 1, tzinfo=timezone.utc)
"""The floor, which exists to catch one specific bug: the zero-value parse.

A provider field that is absent, null, or unparsed frequently arrives as the Unix
epoch rather than as nothing, and an epoch-dated event sorts before every real
one and reads as ancient history rather than as missing data.

This is deliberately *not* AD-35's "preceding its meeting or repository epoch",
which is a per-entity bound needing a source no story supplies yet. A fixed floor
this low cannot reject real telemetry — the systems pm-ai harvests did not exist
in 1999 — while still catching the whole zero-value class.
"""


class ImplausibleTimestamp(ValueError):
    """A timestamp cannot be believed, and will not be quietly replaced."""


def due_date_basis() -> str:
    """The field due-date and fulfilment reasoning compares against (AD-35)."""
    return "occurred_at"


def sweep_basis() -> str:
    """The field cursor, replay and sweep-window reasoning compares against."""
    return "ingested_at"


def validate_occurred_at(
    at: datetime | None = None,
    *,
    now: datetime | None = None,
    future_by_hours: float | None = None,
) -> datetime | None:
    """Judge a provider timestamp, returning it unchanged or refusing it.

    Two spellings, one rule. Plausibility is a question about the *distance* from
    a reference instant, so that distance can either be derived from a pair of
    timestamps or stated outright:

    - `validate_occurred_at(at, now=...)` — the real call. `at` may be `None`,
      which is a known state rather than a failure: it returns `None`, and the
      caller records the absence. It never returns `now`.
    - `validate_occurred_at(future_by_hours=...)` — the offset stated directly.
      There is no timestamp to hand back, so it returns `None`; this spelling is
      a check, not a filter. It judges the **skew half of the rule only**: with
      no reference instant there is no date to compare against, so
      `EARLIEST_PLAUSIBLE` cannot apply and a past offset is accepted. Said
      plainly because this docstring claimed "two spellings, one rule" until the
      2026-08-30 review, which was true of the tolerance and of nothing else.

    Exactly one spelling per call. Supplying both, neither, or `at` without a
    reference instant is a caller error rather than an implausible timestamp —
    the data is not what is wrong.

    Never returns a corrected value. The only outcomes are the timestamp it was
    given, `None`, and a refusal.
    """
    if future_by_hours is not None:
        if at is not None or now is not None:
            raise ValueError(
                "supply either a timestamp with its reference instant or "
                "future_by_hours, never both — two spellings of the offset can "
                "disagree, and then the rule depends on which one is read."
            )
        if not math.isfinite(future_by_hours):
            raise ImplausibleTimestamp(
                f"future_by_hours={future_by_hours!r} is not a finite number. "
                f"Every comparison against it is False, so it would pass as "
                f"plausible without ever having been judged."
            )
        _assert_within_tolerance(timedelta(hours=future_by_hours), subject=None)
        return None

    if now is None:
        raise ValueError(
            "validate_occurred_at needs a reference instant: pass now=, or state "
            "the offset with future_by_hours=. This module reads no clock of its "
            "own, so it cannot supply one."
        )

    # A bad reference instant is *our* bug, not the provider's, so it is a plain
    # caller error rather than `ImplausibleTimestamp` — which this function's own
    # docstring said, while the code raised the data-blaming one anyway.
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError(
            f"now={now!r} is not aware UTC. The reference instant is supplied by "
            f"the caller, so this is a caller error and not a statement about the "
            f"timestamp being judged."
        )
    if at is None:
        # AD-35's load-bearing rule. An absent provider timestamp stays absent —
        # returning `now` here would be the backfill the whole module forbids.
        return None

    _assert_comparable(at, operand="occurred_at")

    if at < EARLIEST_PLAUSIBLE:
        raise ImplausibleTimestamp(
            f"occurred_at {at.isoformat()} precedes the floor "
            f"{EARLIEST_PLAUSIBLE.isoformat()}, so it is a null or unparsed "
            f"provider field rather than a date. It is refused rather than "
            f"replaced: a substituted timestamp is indistinguishable from a real "
            f"one."
        )

    _assert_within_tolerance(at - now, subject=at)
    return at


def _assert_comparable(value: datetime, *, operand: str) -> None:
    """Both operands must be aware UTC, or the comparison means nothing."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ImplausibleTimestamp(
            f"{operand}={value!r} is not aware UTC. Every producer in this "
            f"codebase emits aware UTC, so this is a normalisation gap upstream; "
            f"comparing it against the other operand would silently answer a "
            f"different question."
        )


def _assert_within_tolerance(skew: timedelta, *, subject: datetime | None) -> None:
    """One comparison, whichever spelling produced the offset."""
    if skew <= FUTURE_SKEW_TOLERANCE:
        return
    hours = skew.total_seconds() / 3600
    named = f"occurred_at {subject.isoformat()} " if subject is not None else ""
    raise ImplausibleTimestamp(
        f"{named}sits {hours:.6g} hours ahead of the reference instant, beyond "
        f"the {FUTURE_SKEW_TOLERANCE} tolerance. It is flagged, never backfilled "
        f"from the local clock (AD-35)."
    )
