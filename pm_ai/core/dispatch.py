"""Answer at once, or acknowledge with a job: the five-second rule (AD-21, D5).

A line sent through the daemon is a transport for a command's words, so every
caller of a pipeline inherits the pipeline's bound. Each pipeline declares that
bound once, beside itself in the composition root (`pm_ai.app.pipelines`), and
this is the one function that reads a declaration and decides: under five
seconds the line is answered inline, at five seconds or over it is
acknowledged with a job id and run later (story 4r).

I/O-free, like the rest of `core`: it mints an id and decides. The job's row —
written before the acknowledgement goes back (AD-20) — is the single writer's.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "INLINE_LIMIT_SECONDS",
    "JOB_ID_PREFIX",
    "JobState",
    "Plan",
    "mint_job_id",
    "plan",
]

INLINE_LIMIT_SECONDS = 5.0
"""AD-21's line. A bound strictly below it is answered inline; at it or above, a job."""

JOB_ID_PREFIX = "job_"
"""The surrogate prefix the consistency conventions reserve for a job."""

_JOB_ID_HEX_BYTES = 8


class JobState(StrEnum):
    """A job row's four states. `done` and `failed` are terminal."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    @property
    def finished(self) -> bool:
        return self in (JobState.DONE, JobState.FAILED)


@dataclass(frozen=True, slots=True)
class Plan:
    """The decision for one line: inline, or a job with the id it will carry."""

    inline: bool
    job_id: str | None = None


def mint_job_id() -> str:
    """`job_` plus random hex: a surrogate, never time-ordered, never reused."""
    return f"{JOB_ID_PREFIX}{secrets.token_hex(_JOB_ID_HEX_BYTES)}"


def plan(estimated_seconds: float) -> Plan:
    """Inline under `INLINE_LIMIT_SECONDS`; a job id at it or over.

    `estimated_seconds` is the pipeline's declared bound, never a guess made at
    the call site. Exactly five seconds is a job: the rule reads "anything over
    five seconds acknowledges", and a bound that *reaches* five can exceed it.
    A negative or non-finite bound is a declaration nothing can honour, and is
    refused rather than read as fast.
    """
    seconds = float(estimated_seconds)
    if seconds != seconds or seconds < 0 or seconds == float("inf"):
        raise ValueError(
            f"a pipeline's bound must be a finite, non-negative number of seconds; "
            f"{estimated_seconds!r} is not one, so nothing can be planned from it."
        )
    if seconds < INLINE_LIMIT_SECONDS:
        return Plan(inline=True)
    return Plan(inline=False, job_id=mint_job_id())
