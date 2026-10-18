"""The `event_log/` entry grammar, frozen as a literal table (AD-27).

Spec: `_bmad-output/specs/spec-pm-ai/stories/2m-payload-line-grammar.md`.

The grammar only grows: a field may be added, never removed, renamed, retyped or
given a new meaning; an added field is optional; the list of required fields
never grows; a vocabulary member may be added, never removed or renamed. Every
line ever written stays readable only while that holds, and nothing on a line
says which grammar wrote it — so this table is what notices.

**Literal, never derived.** The nearest earlier tests read field names off the
dataclass at test time, which agree with the code by construction and cannot
fail for the reason the rule exists. Changing anything below is a grammar
change: make it additive, and edit the table in the same change, where a
reviewer sees it.
"""

from __future__ import annotations

import dataclasses
import typing

from pm_ai.domain.event_entries import SELF_ACTION_FIELDS, SelfActionType
from pm_ai.domain.events import PAYLOAD_FOR, ObservedEventType
from pm_ai.domain.handles import PersonHandle

REQUIRED = object()
"""Stands in the default column for a field with no default."""

PAYLOAD_FIELDS: dict[str, tuple[tuple[str, object, object], ...]] = {
    # class name: ((field, resolved type, default or REQUIRED), ...) in order
    "WorkItemPayload": (
        ("work_item_id", str, REQUIRED),
        ("title", str | None, None),
        ("state", str | None, None),
        # Retyped from `Actor | None` by story 2n under a dated AD-27
        # exemption (2026-10-11): no event-log line had been written.
        ("assignee", PersonHandle | None, None),
    ),
    "CommitPayload": (
        ("sha", str, REQUIRED),
        ("message", str, REQUIRED),
        ("branch", str | None, None),
    ),
    "ReviewPayload": (
        ("target_ref", str, REQUIRED),
        ("verdict", str | None, None),
        ("comment_count", int, 0),
    ),
    "DocumentPayload": (
        ("document_id", str, REQUIRED),
        ("title", str | None, None),
    ),
    "MessagePayload": (
        ("channel", str, REQUIRED),
        ("excerpt", str | None, None),
        ("mentions", tuple[str, ...] | None, None),
        # Added by `23f` (2026-10-16): optional, so every earlier line decodes.
        ("sender_name", str | None, None),
    ),
    "DecisionPayload": (
        ("statement", str, REQUIRED),
        ("rationale", str | None, None),
    ),
    "PipelinePayload": (
        ("pipeline_id", str, REQUIRED),
        ("status", str, REQUIRED),
    ),
    "MeetingHeldPayload": (
        ("meeting_id", str, REQUIRED),
        ("attendee_count", int, REQUIRED),
        ("duration_minutes", int, REQUIRED),
    ),
}

PAYLOAD_BINDING: dict[str, str] = {
    "commit_pushed": "CommitPayload",
    "review_submitted": "ReviewPayload",
    "merge_completed": "ReviewPayload",
    "work_item_created": "WorkItemPayload",
    "work_item_updated": "WorkItemPayload",
    "work_item_closed": "WorkItemPayload",
    "pipeline_finished": "PipelinePayload",
    "document_updated": "DocumentPayload",
    "message_posted": "MessagePayload",
    "calendar_event_held": "MeetingHeldPayload",
    "decision": "DecisionPayload",
}

SELF_ACTIONS: dict[str, tuple[str, ...]] = {
    "compaction": ("source", "replaced", "summary"),
    "security": ("protection", "disabled_by"),
    # `skill` joined the required fields with story 2n under the same dated
    # exemption: the skill name moved out of the actor, which is now `pm-ai`.
    "skill_invoked": ("skill", "target", "external_id", "idempotency_key"),
    "goal_set": ("goal_id", "domain", "horizon", "title", "channel"),
    "goal_deleted": ("goal_id", "domain", "horizon", "title", "channel"),
}

OBSERVED: dict[str, str] = {
    "COMMIT_PUSHED": "commit_pushed",
    "REVIEW_SUBMITTED": "review_submitted",
    "MERGE_COMPLETED": "merge_completed",
    "WORK_ITEM_CREATED": "work_item_created",
    "WORK_ITEM_UPDATED": "work_item_updated",
    "WORK_ITEM_CLOSED": "work_item_closed",
    "PIPELINE_FINISHED": "pipeline_finished",
    "DOCUMENT_UPDATED": "document_updated",
    "MESSAGE_POSTED": "message_posted",
    "CALENDAR_EVENT_HELD": "calendar_event_held",
    "DECISION": "decision",
}

SELF_ACTION_MEMBERS: dict[str, str] = {
    "COMPACTION": "compaction",
    "SECURITY": "security",
    "SKILL_INVOKED": "skill_invoked",
    "GOAL_SET": "goal_set",
    "GOAL_DELETED": "goal_deleted",
}


def _default(field: dataclasses.Field) -> object:
    if field.default is not dataclasses.MISSING:
        return field.default
    if field.default_factory is not dataclasses.MISSING:
        return field.default_factory()
    return REQUIRED


def _shown(default: object) -> str:
    return "required" if default is REQUIRED else f"default {default!r}"


def _payload_drift() -> list[str]:
    """Every difference between the code and the table, each named."""
    found: list[str] = []
    classes = {cls.__name__: cls for cls in PAYLOAD_FOR.values()}
    for name in sorted(set(PAYLOAD_FIELDS) - set(classes)):
        found.append(f"{name} is pinned and no longer registered")
    for name in sorted(set(classes) - set(PAYLOAD_FIELDS)):
        found.append(f"{name} is registered and not pinned")
    for name in sorted(set(PAYLOAD_FIELDS) & set(classes)):
        cls = classes[name]
        hints = typing.get_type_hints(cls)
        actual = {f.name: (hints[f.name], _default(f)) for f in dataclasses.fields(cls)}
        pinned = {f: (t, d) for f, t, d in PAYLOAD_FIELDS[name]}
        for field in pinned.keys() - actual.keys():
            found.append(f"{name}.{field} was removed or renamed")
        for field in actual.keys() - pinned.keys():
            found.append(f"{name}.{field} was added and is not in the table")
        for field in pinned.keys() & actual.keys():
            (want_type, want_default), (got_type, got_default) = pinned[field], actual[field]
            if got_type != want_type:
                found.append(f"{name}.{field} was retyped: {want_type} -> {got_type}")
            if (want_default is REQUIRED) != (got_default is REQUIRED):
                found.append(
                    f"{name}.{field} changed optionality: "
                    f"{_shown(want_default)} -> {_shown(got_default)}"
                )
            elif want_default is not REQUIRED and want_default != got_default:
                found.append(
                    f"{name}.{field} changed its default: "
                    f"{want_default!r} -> {got_default!r}"
                )
        # The order of the fields both lists share, checked whatever else
        # changed: an added or removed field must not hide a reorder.
        current = [f.name for f in dataclasses.fields(cls)]
        shared_pinned = [f for f, _, _ in PAYLOAD_FIELDS[name] if f in actual]
        shared_current = [f for f in current if f in pinned]
        if shared_pinned != shared_current:
            found.append(
                f"{name} reordered its fields, which reorders every line: "
                f"{shared_pinned} -> {shared_current}"
            )
    return found


def test_every_payload_class_matches_the_pinned_table():
    drift = _payload_drift()
    assert not drift, "the entry grammar changed:\n  " + "\n  ".join(drift)


def test_every_observed_category_binds_its_pinned_payload():
    actual = {category.value: cls.__name__ for category, cls in PAYLOAD_FOR.items()}
    assert actual == PAYLOAD_BINDING, (
        f"a category's payload binding changed: pinned {PAYLOAD_BINDING}, "
        f"found {actual}"
    )


def test_every_self_action_requires_exactly_its_pinned_fields():
    """The list of required fields never grows: growing it makes every older
    line of that kind unreadable, since the list is checked when a line is
    parsed."""
    actual = {action.value: fields for action, fields in SELF_ACTION_FIELDS.items()}
    drift = [
        f"{kind}: pinned {SELF_ACTIONS.get(kind)}, found {actual.get(kind)}"
        for kind in sorted(set(actual) | set(SELF_ACTIONS))
        if actual.get(kind) != SELF_ACTIONS.get(kind)
    ]
    assert not drift, "a self-action's required fields changed:\n  " + "\n  ".join(drift)


def test_the_observed_vocabulary_matches_the_pinned_members():
    actual = {member.name: member.value for member in ObservedEventType}
    assert actual == OBSERVED, _vocabulary_drift("ObservedEventType", OBSERVED, actual)


def test_the_self_action_vocabulary_matches_the_pinned_members():
    actual = {member.name: member.value for member in SelfActionType}
    assert actual == SELF_ACTION_MEMBERS, _vocabulary_drift(
        "SelfActionType", SELF_ACTION_MEMBERS, actual
    )


def _vocabulary_drift(enum: str, pinned: dict[str, str], actual: dict[str, str]) -> str:
    removed = sorted(set(pinned) - set(actual))
    added = sorted(set(actual) - set(pinned))
    respelled = sorted(n for n in set(pinned) & set(actual) if pinned[n] != actual[n])
    return (
        f"{enum} changed — removed {removed}, added {added} (add it to the table), "
        f"respelled {respelled}. Members may be added, never removed or renamed."
    )


# ── The table's own failure, demonstrated ────────────────────────────────────


def test_the_drift_report_names_a_renamed_retyped_or_required_field(monkeypatch):
    """A check that cannot be shown to fail is not a check: doctor the table
    the way a code change would move away from it, and read the names."""
    doctored = dict(PAYLOAD_FIELDS)
    doctored["CommitPayload"] = (
        ("sha", str, REQUIRED),
        ("msg", str, REQUIRED),  # the code still says `message`
        ("branch", str, None),  # the code says `str | None`
    )
    doctored["ReviewPayload"] = (
        ("target_ref", str, REQUIRED),
        ("verdict", str | None, None),
        ("comment_count", int, REQUIRED),  # the code has a default of 0
    )
    doctored["DocumentPayload"] = (
        ("document_id", str, REQUIRED),
        ("title", str | None, "untitled"),  # the code defaults to None
    )
    doctored["MessagePayload"] = (
        ("excerpt", str | None, None),  # swapped with `channel` ...
        ("channel", str, REQUIRED),
        # ... and `mentions` dropped, so the field sets differ as well
    )
    monkeypatch.setitem(globals(), "PAYLOAD_FIELDS", doctored)
    drift = "\n".join(_payload_drift())
    assert "MessagePayload reordered its fields" in drift
    assert "MessagePayload.mentions was added" in drift
    assert "CommitPayload.msg was removed or renamed" in drift
    assert "CommitPayload.message was added" in drift
    assert "CommitPayload.branch was retyped" in drift
    assert "ReviewPayload.comment_count changed optionality" in drift
    assert "DocumentPayload.title changed its default" in drift
