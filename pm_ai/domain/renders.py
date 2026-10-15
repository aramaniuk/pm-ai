"""What the operational store holds about each dashboard scope's renders (story 9b).

Tier 2: the instant of the last successful render, which Proactive Enablement
looks back to, and the last failed attempt with its reason, which `pm-ai
doctor`'s `renders` line names. Written by every render path — the daemon's
07:00 render, a hand-run `pm-ai dashboard`, a dashboard job — each through one
insert-or-update statement keyed by scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pm_ai.domain.identity import DataScope

__all__ = ["RenderRecord", "RenderUnrecorded"]


@dataclass(frozen=True, slots=True)
class RenderRecord:
    """One dashboard scope's last successful render and its current failure.

    `last_success` is `None` until a render succeeds. `last_failure` and
    `reason` are both `None` when the last attempt did not fail: a success
    clears them. All instants are aware UTC.
    """

    scope: DataScope
    last_success: datetime | None
    last_failure: datetime | None
    reason: str | None
    retryable: bool = False
    """The failure is one waiting can clear; the daemon retries it on the waits."""

    @property
    def failing(self) -> bool:
        """Whether the latest recorded attempt failed: a failure later than the
        last success. Decided here, on the read side, so a failure recorded
        before a newer success — two writers landing out of order — does not
        read as failing."""
        if self.last_failure is None:
            return False
        return self.last_success is None or self.last_failure > self.last_success


class RenderUnrecorded(Exception):
    """The dashboard was published, and recording its instant then failed (9b).

    The page on disk is the new one; only the record is missing, so the next
    evaluation renders again. The sentence is pm-ai's own and names why;
    `path` is where the new page is.
    """

    def __init__(self, sentence: str, *, path: Path | None = None) -> None:
        super().__init__(sentence)
        self.path = path
