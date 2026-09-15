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
    # guess: where the transcript is written, and whether a record in the project
    # scope is allowed to cite this meeting at all. Meetings were previously filed in
    # the personal scope, which made every commitment citing one an AD-38
    # violation by construction.
    scope: DataScope
    calendar_event_ref: str | None = None
    # Carried in memory and written to no file (story 33c, decided 2026-09-07).
    #
    # A response status is an answer about a meeting that has **not** happened,
    # and no meeting that has not happened is recorded: the calendar owns it, and
    # a local copy of a row that can move or vanish outside pm-ai cannot be kept
    # accurate. So this rides the `Meeting` a connector hands back for the day
    # ahead — the dashboard renders it — and `pm_ai.core.meeting_records` has no
    # `tentative` field to put it in. `render_record` writes the keys in `_FIELDS`
    # and this is not one of them, which is what makes "stored nowhere" a
    # property of the grammar rather than of every caller remembering.
    #
    # `False` by default because that is what a record read back off disk knows:
    # `parse_record` constructs a `Meeting` without it, and a held meeting has no
    # tentative answer to have lost.
    tentative: bool = False
    # Carried in memory and written to no file, on `tentative`'s precedent above
    # and for a different reason (story 23b, 2026-09-15).
    #
    # `meeting_id` is the provider's per-mailbox event id, so the same meeting
    # cross-invited to two tenants arrives twice under two different ids and
    # matching on it deduplicates nothing in the only case that motivates
    # deduplication. `iCalUId` is the identifier that survives the crossing, and
    # this is where it rides — from the connector that read it to the surface
    # that merges two calendars into one day.
    #
    # Nowhere near a file: `meeting_records._FIELDS` has no key for it, so the
    # record grammar is unchanged and every golden still holds. `None` by
    # default because that is what a record read back off disk knows, and
    # because absence has to stay absent — two meetings that both lack it are
    # not the same meeting, and a `""` default would make them look it.
    ical_uid: str | None = None

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
