"""Event taxonomy, provenance, and the envelope.

AD-27 closed the *type* enumeration and left `payload` an open dict — so a
reviewer found GitLab and Notion both emitting `work_item_closed` with different
payload shapes, and the verifier silently missing evidence from one of them.
A closed enum over an open payload is half a contract.

Imports nothing from `pm_ai` except sibling domain modules (AD-30).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from pm_ai.domain.identity import Actor, DataScope, SourceRef


class NormalizedEventType(Enum):
    """The closed vocabulary every connector maps into (AD-27).

    A connector may not mint a member. Adding one is a deliberate change here,
    reviewed against the existing members for overlap — which is the only way
    `mr_updated` and `workitem.updated` don't end up as two names for one thing.
    """

    COMMIT_PUSHED = "commit_pushed"
    REVIEW_SUBMITTED = "review_submitted"
    MERGE_COMPLETED = "merge_completed"
    WORK_ITEM_CREATED = "work_item_created"
    WORK_ITEM_UPDATED = "work_item_updated"
    WORK_ITEM_CLOSED = "work_item_closed"
    PIPELINE_FINISHED = "pipeline_finished"
    DOCUMENT_UPDATED = "document_updated"
    MESSAGE_POSTED = "message_posted"
    CALENDAR_EVENT_HELD = "calendar_event_held"


class Provenance(Enum):
    """Who authored the event (AD-36).

    UNKNOWN exists because the two-value enum failed open: an event that couldn't
    be attributed defaulted to `external` and counted as evidence that a
    commitment was kept. Verification treats UNKNOWN as *not* evidence.
    """

    EXTERNAL = "external"
    PM_AI = "pm_ai"
    UNKNOWN = "unknown"

    @property
    def admissible_as_evidence(self) -> bool:
        """AD-36: only externally-authored activity can prove fulfilment."""
        return self is Provenance.EXTERNAL


# ── Typed payloads ───────────────────────────────────────────────────────────
# One payload shape per event type. The registry below is what stops two
# connectors agreeing on the type name and disagreeing on everything inside it.


@dataclass(frozen=True, slots=True)
class WorkItemPayload:
    work_item_id: str
    title: str | None = None
    state: str | None = None
    assignee: Actor | None = None


@dataclass(frozen=True, slots=True)
class CommitPayload:
    sha: str
    message: str
    branch: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewPayload:
    target_ref: str
    verdict: str | None = None
    comment_count: int = 0


@dataclass(frozen=True, slots=True)
class DocumentPayload:
    document_id: str
    title: str | None = None


@dataclass(frozen=True, slots=True)
class MessagePayload:
    channel: str
    excerpt: str | None = None


@dataclass(frozen=True, slots=True)
class PipelinePayload:
    pipeline_id: str
    status: str


@dataclass(frozen=True, slots=True)
class MeetingHeldPayload:
    meeting_id: str
    attendee_count: int
    duration_minutes: int


PAYLOAD_FOR: dict[NormalizedEventType, type] = {
    NormalizedEventType.COMMIT_PUSHED: CommitPayload,
    NormalizedEventType.REVIEW_SUBMITTED: ReviewPayload,
    NormalizedEventType.MERGE_COMPLETED: ReviewPayload,
    NormalizedEventType.WORK_ITEM_CREATED: WorkItemPayload,
    NormalizedEventType.WORK_ITEM_UPDATED: WorkItemPayload,
    NormalizedEventType.WORK_ITEM_CLOSED: WorkItemPayload,
    NormalizedEventType.PIPELINE_FINISHED: PipelinePayload,
    NormalizedEventType.DOCUMENT_UPDATED: DocumentPayload,
    NormalizedEventType.MESSAGE_POSTED: MessagePayload,
    NormalizedEventType.CALENDAR_EVENT_HELD: MeetingHeldPayload,
}


class PayloadMismatch(TypeError):
    """The payload does not match the shape registered for this event type."""


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    """What every connector produces and nothing else does (AD-9, AD-34, AD-35).

    `id` is deliberately absent: the storage service mints the surrogate at
    persist time, and deduplication uses the natural key. A connector that
    minted ids would double-count every re-harvest.
    """

    scope: DataScope
    type: NormalizedEventType
    source_ref: SourceRef
    actor: Actor
    occurred_at: datetime | None  # provider clock — may be absent or skewed
    payload: object
    authored_by: Provenance = Provenance.UNKNOWN
    ingested_at: datetime | None = field(default=None)  # assigned by storage

    def __post_init__(self) -> None:
        expected = PAYLOAD_FOR[self.type]
        if not isinstance(self.payload, expected):
            raise PayloadMismatch(
                f"{self.type.value} requires {expected.__name__}, "
                f"got {type(self.payload).__name__} — a closed type over an open "
                f"payload lets two connectors agree on the name and nothing else."
            )

    @property
    def natural_key(self) -> tuple[str, str]:
        """AD-34: dedup on this, never on the minted surrogate."""
        return (self.source_ref.system, str(self.source_ref))
