"""Event taxonomy, provenance, and the envelope.

AD-27 closed the *type* enumeration and left `payload` an open dict — so a
reviewer found GitLab and Notion both emitting `work_item_closed` with different
payload shapes, and the verifier silently missing evidence from one of them.
A closed enum over an open payload is half a contract.

Imports nothing from `pm_ai` except sibling domain modules (AD-30).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

from pm_ai.domain.identity import Actor, DataScope, SourceRef
from pm_ai.domain.invariants import InconsistentModel


class ObservedEventType(Enum):
    """The closed vocabulary for things that happened in the world (AD-27).

    **Subject: something outside pm-ai, with pm-ai as the witness.** That is the
    membership rule, and it is what the name states. A member qualifies only if
    it has a durable external referent expressible in AD-34's grammar, can carry
    a provider `occurred_at`, and is dedup-able by natural key. Such a record may
    be evidence, subject to AD-36's authorship rule.

    Named `NormalizedEventType` until 2026-08-29, which described how a value got
    here — a connector normalised into it — rather than what it is about. The
    sibling vocabulary `SelfActionType` holds what pm-ai did, where pm-ai is the
    only witness; the two are disjoint, and the question that separates them is
    *did this happen, or did pm-ai do it?*

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
    # The PM decided; pm-ai only observed it, through a transcript. It belongs
    # here rather than with pm-ai's own actions because its subject is a person,
    # and AD-33 already rules that a transcript's referent is its meeting — so
    # `meeting:<id>`, already in AD-34's scopeless set, is a referent it can
    # carry. CAP-27 tags these `[TYPE: DECISION]` in the owning scope's log.
    DECISION = "decision"


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
class DecisionPayload:
    """What was decided. Who decided it is the envelope's `actor`."""

    statement: str
    rationale: str | None = None


@dataclass(frozen=True, slots=True)
class PipelinePayload:
    pipeline_id: str
    status: str


@dataclass(frozen=True, slots=True)
class MeetingHeldPayload:
    meeting_id: str
    attendee_count: int
    duration_minutes: int


PAYLOAD_FOR: dict[ObservedEventType, type] = {
    ObservedEventType.COMMIT_PUSHED: CommitPayload,
    ObservedEventType.REVIEW_SUBMITTED: ReviewPayload,
    ObservedEventType.MERGE_COMPLETED: ReviewPayload,
    ObservedEventType.WORK_ITEM_CREATED: WorkItemPayload,
    ObservedEventType.WORK_ITEM_UPDATED: WorkItemPayload,
    ObservedEventType.WORK_ITEM_CLOSED: WorkItemPayload,
    ObservedEventType.PIPELINE_FINISHED: PipelinePayload,
    ObservedEventType.DOCUMENT_UPDATED: DocumentPayload,
    ObservedEventType.MESSAGE_POSTED: MessagePayload,
    ObservedEventType.CALENDAR_EVENT_HELD: MeetingHeldPayload,
    ObservedEventType.DECISION: DecisionPayload,
}


# ── Which payload text came from outside ─────────────────────────────────────
# The boundary sanitizes before anything reaches a prompt (AD-12), and it still
# guesses: `pipelines.py` reads `getattr(event.payload, "message", "")`, a field
# name only `CommitPayload` has, so every other payload sanitizes the empty
# string. Story `8e` is where the boundary stops guessing and reads these
# records instead; what lives here is the declaration it will read, because a
# pipeline cannot know which fields came from a provider and the payload can.
#
# What separates the two records is *who authored the string*, not who
# transmitted it — every field below is provider-supplied:
#
# - A person wrote it (a title, a commit message, a chat excerpt, a branch,
#   channel or merge-target name, a work-item state an administrator named) —
#   untrusted.
# - A machine minted it (a SHA, a provider id) or it is one of a fixed set the
#   provider defines (a CI status, a review verdict) — trusted, with the reason
#   recorded.


class MissingSanitizableDeclaration(InconsistentModel):
    """A payload registered in `PAYLOAD_FOR` does not account for its own text.

    Seven faults raise it, not one. A class may be missing from either record
    entirely; a record may name a field the class does not have, a field that is
    not text, or a field the other record also claims; a trusted field may carry
    no reason; a `str` field may appear in neither record; and a record may name
    a class `PAYLOAD_FOR` no longer registers. Each is the same statement — the
    declaration and the dataclass disagree, so nothing may be built on either.

    Raised from an `if` rather than an `assert`: story 1l converted every
    import-time guard because `python -O` strips assertions and the daemon runs
    as a `launchd` agent, where `-O` in a plist is an ordinary thing to add. A
    guard standing between a new payload type and an unsanitized field is not
    one to leave at the mercy of an interpreter flag.

    Reaches a caller at import, where the module-level call raises it. Tests also
    call `_assert_payload_text_is_declared` directly against a doctored registry,
    which is the same refusal arriving by a shorter route.
    """


UNTRUSTED_TEXT: dict[type, tuple[str, ...]] = {
    WorkItemPayload: ("title", "state"),
    CommitPayload: ("message", "branch"),
    ReviewPayload: ("target_ref",),
    DocumentPayload: ("title",),
    MessagePayload: ("channel", "excerpt"),
    DecisionPayload: ("statement", "rationale"),
    PipelinePayload: (),
    MeetingHeldPayload: (),
}
"""The fields holding text a person outside pm-ai wrote, keyed by payload class.

**Keyed by class, not by event type.** `ReviewPayload` is the payload of both
`REVIEW_SUBMITTED` and `MERGE_COMPLETED`, so a per-type key would permit two
declarations of one class that disagree, and the disagreement would be invisible.

An empty tuple is a statement, not an omission — but only a legitimate one when
every `str` field of the class is recorded in `TRUSTED_TEXT` instead, which is
what `_assert_payload_text_is_declared` checks. A class declaring nothing while
carrying provider prose would be the original `getattr` bug with a registry on
top. Two classes declare none, and each says why: `PipelinePayload` carries an
id and an outcome from the CI provider's fixed set, and `MeetingHeldPayload`
carries an id and two counts — a meeting's subject and body are not in this
payload at all, which is the field a calendar connector adds next and the one
that would move it into this record.

**The deciding question is who wrote the string, not who sent it.** Both records
hold provider-supplied fields; the split is whether a person typed the value.
`WorkItemPayload.state` is here while `PipelinePayload.status` is trusted: a
work-item state is a workflow name an administrator writes, and a pipeline status
is one of the CI provider's own outcomes. `ReviewPayload.target_ref` is here for
the same reason `CommitPayload.branch` is — a merge target is usually a branch
name a person chose. It is also an identifier a citation resolves against, and
that is not a reason to trust it: AD-29 makes `sanitize` non-destructive, so the
raw value survives on `Sanitized.raw` for resolution and erring toward untrusted
costs nothing.
"""

TRUSTED_TEXT: dict[type, dict[str, str]] = {
    WorkItemPayload: {
        "work_item_id": "the provider's key for the item — what a citation "
        "resolves against, so redacting it would break the referent",
    },
    CommitPayload: {
        "sha": "a content hash the VCS computes; no person writes one",
    },
    ReviewPayload: {
        "verdict": "the review outcome, from the provider's own fixed set "
        "(approved, changes_requested); a reviewer's words are not in this "
        "payload at all",
    },
    DocumentPayload: {
        "document_id": "the provider's document identifier",
    },
    MessagePayload: {},
    DecisionPayload: {},
    PipelinePayload: {
        "pipeline_id": "the CI provider's identifier for the run",
        "status": "the run's outcome, from the CI provider's fixed set "
        "(success, failed, canceled) — not text anyone typed",
    },
    MeetingHeldPayload: {
        "meeting_id": "the calendar provider's identifier for the meeting",
    },
}
"""The `str` fields that are *not* provider prose, each with why it is not.

A reason is required rather than optional. Allowing silence would mean the
completeness check passes for a class that declares nothing while carrying text
a person wrote, which is the defect this registry replaces.

**A known limit: the rule reaches `str` and `str | None`, and no other shape.**
Any field of any other type is neither required nor refused — it simply falls
outside both records. That covers two kinds of field that do carry provider text:

- **Nested types.** `WorkItemPayload.assignee: Actor | None` and
  `NormalizedEvent.actor` both hold a display name the provider supplied.
- **Containers of text.** `list[str]`, `tuple[str, ...]` and `dict[str, str]`
  pass the check untouched, so a work item's `labels` or `tags` — the likeliest
  next field here — would be declarable by nobody and demanded of nobody.

Recorded rather than silently widened: reaching inside a nested type or walking a
container is a different change from making sanitization run at all, and the
declaration this slice adds is what a widening would extend.
"""


def _is_text(hint: object) -> bool:
    """True for `str` and for a union of `str` with `None`, and nothing else.

    Reads the *resolved* annotation. `from __future__ import annotations` at the
    top of this module makes `field.type` the string `"str | None"`, so a check
    that matched annotation text would refuse the older `Optional[str]` spelling
    and admit whatever a future author happened to type.
    """
    if hint is str:
        return True
    if get_origin(hint) in (Union, UnionType):
        args = set(get_args(hint))
        return str in args and args <= {str, type(None)}
    return False


def _assert_payload_text_is_declared() -> None:
    """Every `str` field of every registered payload is in exactly one record.

    Enumerated over *fields* rather than classes: a class-level check passes for
    a class that declares an empty tuple while carrying provider prose.
    """
    for payload in dict.fromkeys(PAYLOAD_FOR.values()):
        name = payload.__name__

        if not is_dataclass(payload):
            raise MissingSanitizableDeclaration(
                f"{name} is registered in PAYLOAD_FOR and is not a dataclass. "
                f"A declaration is checked against the class's fields, so a "
                f"class that has none cannot state anything about its text."
            )

        if payload not in UNTRUSTED_TEXT:
            raise MissingSanitizableDeclaration(
                f"{name} is registered in PAYLOAD_FOR and declares no untrusted "
                f"text in UNTRUSTED_TEXT. Carrying no provider prose is stated "
                f"with an empty tuple; saying nothing is how a field reaches a "
                f"prompt unsanitized."
            )
        if payload not in TRUSTED_TEXT:
            raise MissingSanitizableDeclaration(
                f"{name} has no entry in TRUSTED_TEXT. Every `str` field is "
                f"either untrusted or trusted for a stated reason, and a class "
                f"with no trusted fields records that as an empty mapping."
            )

        untrusted = tuple(UNTRUSTED_TEXT[payload])
        trusted = dict(TRUSTED_TEXT[payload])
        declared = set(untrusted) | set(trusted)
        present = {f.name for f in fields(payload)}

        try:
            hints = get_type_hints(payload)
        except NameError as unresolved:
            # Guaranteed to be a *string* annotation here — `from __future__
            # import annotations` is on — so a name that does not resolve in this
            # module fails only now, at the one moment the rule needs the type.
            raise MissingSanitizableDeclaration(
                f"{name} has an annotation that does not resolve: {unresolved}. "
                f"Whether a field is text cannot be decided from an annotation "
                f"nothing can evaluate."
            ) from unresolved

        unknown = sorted(declared - present)
        if unknown:
            raise MissingSanitizableDeclaration(
                f"{name} declares {unknown}, which it does not have. A "
                f"misspelled name sanitizes nothing and reads as if it did — "
                f"the `getattr` fallback this registry removed."
            )

        both = sorted(set(untrusted) & set(trusted))
        if both:
            raise MissingSanitizableDeclaration(
                f"{name} records {both} as both untrusted and trusted. A field "
                f"is one or the other; two answers is no answer."
            )

        not_text = sorted(n for n in declared if not _is_text(hints[n]))
        if not_text:
            raise MissingSanitizableDeclaration(
                f"{name} declares {not_text}, which is not text. Only `str` and "
                f"`str | None` can be sanitized; declaring anything else names a "
                f"field the boundary cannot act on."
            )

        unreasoned = sorted(
            n
            for n, why in trusted.items()
            if not isinstance(why, str) or not why.strip()
        )
        if unreasoned:
            raise MissingSanitizableDeclaration(
                f"{name} records {unreasoned} as trusted with no reason. The "
                f"reason is what makes it a judgement rather than an oversight."
            )

        unaccounted = sorted(
            n for n in present if _is_text(hints[n]) and n not in declared
        )
        if unaccounted:
            raise MissingSanitizableDeclaration(
                f"{name} leaves {unaccounted} in neither record. Every `str` "
                f"field is declared untrusted or recorded as trusted with a "
                f"reason, because a class that stays silent about a "
                f"provider-written field is the defect this replaces."
            )

    orphans = sorted(
        cls.__name__
        for cls in (set(UNTRUSTED_TEXT) | set(TRUSTED_TEXT))
        - set(PAYLOAD_FOR.values())
    )
    if orphans:
        raise MissingSanitizableDeclaration(
            f"{orphans} are declared here and registered for no event type. A "
            f"declaration for a class PAYLOAD_FOR dropped or renamed is checked "
            f"by nothing and reads as coverage of a payload that no longer "
            f"exists."
        )


_assert_payload_text_is_declared()


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
    type: ObservedEventType
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
    def natural_key(self) -> tuple[str, str, str]:
        """AD-34: dedup on this, never on the minted surrogate.

        `scope` is part of the key. Without it, AD-38's mandated cross-scope
        split — one operation writing a project entry and a personal entry — has
        its second entry silently counted as a duplicate, so the rule written to
        prevent a leak instead drops the record.
        """
        return (str(self.scope), self.source_ref.system, str(self.source_ref))
