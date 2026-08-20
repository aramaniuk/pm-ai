"""The Meeting entity (AD-33).

A transcript is a derivative of a meeting, so facts cite the *meeting* — which
never expires — rather than the capture, which purges at 30 days. Making Meeting
first-class also gives FR-03's Man-Hour Cost one home instead of three ad-hoc
lookups across FR-03, FR-32, and UJ-8.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pm_ai.domain.identity import Actor, DataScope, SourceRef


@dataclass(frozen=True, slots=True)
class Meeting:
    """Tier-1, durable, and the citation root for everything said in it."""

    meeting_id: str
    title: str
    start: datetime
    duration_minutes: int
    attendees: tuple[Actor, ...]
    # AD-33/AD-38 — a Meeting belongs to the scope that owns its subject: a team
    # meeting to its project, a 1:1 with a direct report to `people`. This is
    # required rather than defaulted because it decides two things no caller may
    # guess: where the transcript is written, and whether a git-committed record
    # is allowed to cite this meeting at all. Meetings were previously filed in
    # the personal scope, which made every commitment citing one an AD-38
    # violation by construction.
    scope: DataScope
    calendar_event_ref: str | None = None

    @property
    def source_ref(self) -> SourceRef:
        """What a derived fact cites (AD-33) — stable for the life of the record."""
        return SourceRef.parse(f"meeting:{self.meeting_id}")

    @property
    def transcript_home(self) -> DataScope:
        """A transcript lives in the same scope as the meeting it captures.

        The capture cannot be more or less shareable than the event it records.
        """
        return self.scope

    def man_hour_cost(self, blended_hourly_rate: float) -> float:
        """FR-03. A single PM-configured rate, never per-attendee salary data."""
        return len(self.attendees) * (self.duration_minutes / 60) * blended_hourly_rate
