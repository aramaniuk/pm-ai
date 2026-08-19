"""The Meeting entity (AD-33).

A transcript is a derivative of a meeting, so facts cite the *meeting* — which
never expires — rather than the capture, which purges at 30 days. Making Meeting
first-class also gives FR-03's Man-Hour Cost one home instead of three ad-hoc
lookups across FR-03, FR-32, and UJ-8.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pm_ai.domain.identity import Actor, SourceRef


@dataclass(frozen=True, slots=True)
class Meeting:
    """Tier-1, durable, and the citation root for everything said in it."""

    meeting_id: str
    title: str
    start: datetime
    duration_minutes: int
    attendees: tuple[Actor, ...]
    calendar_event_ref: str | None = None

    @property
    def source_ref(self) -> SourceRef:
        """What a derived fact cites (AD-33) — stable for the life of the record."""
        return SourceRef.parse(f"meeting:{self.meeting_id}")

    def man_hour_cost(self, blended_hourly_rate: float) -> float:
        """FR-03. A single PM-configured rate, never per-attendee salary data."""
        return len(self.attendees) * (self.duration_minutes / 60) * blended_hourly_rate
