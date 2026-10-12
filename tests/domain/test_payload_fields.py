"""The encode/decode pair between a payload and its `p.` line fields.

Spec: `_bmad-output/specs/spec-pm-ai/stories/2m-payload-line-grammar.md`.

One test per row of the story's I/O matrix, in its order, plus the round trip
over every payload class. Most rows go through the whole line — encode, render,
parse, decode — because the list layer sits inside the line layer and a test of
either half alone cannot see the two disagree. The bytes-on-disk rows live
beside today's commit line in `tests/architecture/test_sanitize_boundary.py`;
the storage-level round trip is in `tests/core/test_payload_serialisation.py`.
"""

from __future__ import annotations

import dataclasses

import pytest

from pm_ai.core.ledger import parse_line
from pm_ai.domain.event_entries import (
    SELF_ACTION_FIELDS,
    EventEntry,
    MalformedEntry,
    SelfActionType,
    render_entry,
)
from pm_ai.domain.events import (
    PAYLOAD_FOR,
    CommitPayload,
    DecisionPayload,
    DocumentPayload,
    MeetingHeldPayload,
    MessagePayload,
    ObservedEventType,
    PipelinePayload,
    ReviewPayload,
    WorkItemPayload,
)
from pm_ai.domain.handles import NO_PERSON, person_handle
from pm_ai.domain.handles import UNRESOLVED_SENTINEL as UNRESOLVED_ACTOR
from pm_ai.domain.payload_fields import (
    NoPayload,
    UnencodablePayload,
    decode_payload,
    encode_payload,
)

CATEGORY_FOR = {payload: category for category, payload in reversed(PAYLOAD_FOR.items())}
"""One observed category per payload class, to build a line around it."""

DANA = person_handle("email", "dana@example.com")
U1 = "graph:00000001-0000-4000-8000-000000000001"
U2 = "graph:00000002-0000-4000-8000-000000000002"


@pytest.fixture
def plain_lists(monkeypatch):
    """`mentions` as a plain text list, so the list layer's escaping is tested.

    `mentions` is the only list field and it is a person list, whose members
    never hold a comma or a space — so a comma to escape, and a space for the
    line layer to quote, are reachable only with the person check switched off.
    (An `email:` member may hold a backslash; that escape is tested on a real
    handle below.)
    """
    import pm_ai.domain.payload_fields as module

    monkeypatch.setattr(module, "PERSON_LIST_FIELDS", {})
    monkeypatch.setattr(module, "_LAYOUTS", {})


def _entry(category: ObservedEventType, fields: tuple[tuple[str, str], ...]) -> EventEntry:
    return EventEntry(
        entry_id="evt_1",
        category=category,
        actor="u_dana",
        fields=(("src", "teams:alpha:message:1"),) + fields,
    )


def _line(payload: object) -> str:
    """The payload as a whole rendered ledger line."""
    return render_entry(_entry(CATEGORY_FOR[type(payload)], encode_payload(payload)))


def _through_the_line(payload: object) -> object:
    """Encode, render, parse, decode — both layers, both directions."""
    return decode_payload(parse_line(_line(payload)))


def _hand_edited(category: ObservedEventType, tail: str) -> EventEntry:
    """A line as a person editing the segment by hand might leave it."""
    return parse_line(f"- [evt_1] {category.value} actor=u_dana {tail}")


def _reduced(payload: object) -> object:
    """What the round trip gives back: since story 2n a person handle loses
    nothing, so this is the payload itself."""
    return payload


# ── The matrix ───────────────────────────────────────────────────────────────


def test_text_fields_carry_their_prefixed_names_and_decode_equal():
    payload = CommitPayload(sha="9f2a1c", message="Fix the build", branch="main")
    assert encode_payload(payload) == (
        ("p.sha", "9f2a1c"),
        ("p.message", "Fix the build"),
        ("p.branch", "main"),
    )
    assert _through_the_line(payload) == payload


def test_a_detail_not_recorded_is_left_off_the_line_and_decodes_as_not_recorded():
    """Text and list alike: `None` is "the connector never asked"."""
    commit = CommitPayload(sha="9f2a1c", message="m", branch=None)
    message = MessagePayload(channel="#payments", mentions=None)
    assert "p.branch" not in dict(encode_payload(commit))
    assert "p.mentions" not in dict(encode_payload(message))
    assert "p.mentions" not in _line(message)
    assert _through_the_line(commit).branch is None  # type: ignore[attr-defined]
    assert _through_the_line(message).mentions is None  # type: ignore[attr-defined]


def test_a_person_is_written_as_its_handle_text_and_decodes_equal():
    payload = WorkItemPayload(work_item_id="PAY-1", assignee=DANA)
    line = _line(payload)
    assert "p.assignee=email:dana@example.com" in line
    assert _through_the_line(payload).assignee == DANA  # type: ignore[attr-defined]


def test_a_sender_and_a_mention_of_one_person_share_the_handle_text():
    message = MessagePayload(channel="#payments", mentions=(U1, str(DANA)))
    assert dict(encode_payload(message))["p.mentions"] == f"{U1},email:dana@example.com"
    assert _through_the_line(message).mentions == (U1, str(DANA))  # type: ignore[attr-defined]


def test_an_assignee_that_is_not_a_handle_is_refused_naming_the_field():
    with pytest.raises(UnencodablePayload) as caught:
        encode_payload(WorkItemPayload(work_item_id="PAY-1", assignee="dana@example.com"))  # type: ignore[arg-type]
    assert "assignee" in str(caught.value)


def test_no_person_as_an_assignee_is_refused_naming_the_field():
    """Matrix: `none:` out of place — absence of an assignee is `None`."""
    with pytest.raises(UnencodablePayload) as caught:
        encode_payload(WorkItemPayload(work_item_id="PAY-1", assignee=NO_PERSON))
    assert "assignee" in str(caught.value)


@pytest.mark.parametrize(
    "member", ["u_1", UNRESOLVED_ACTOR, "none:", "email:Dana@X.com", "Dana Smith"]
)
def test_a_mention_that_is_not_a_canonical_handle_is_refused_naming_the_field(member):
    """Matrix: mention not a handle — refused at encode, naming `mentions`."""
    with pytest.raises(UnencodablePayload) as caught:
        encode_payload(MessagePayload(channel="#p", mentions=(U1, member)))
    assert "mentions" in str(caught.value)


def test_a_count_round_trips_as_a_number():
    payload = ReviewPayload(target_ref="main", comment_count=0)
    assert dict(encode_payload(payload))["p.comment_count"] == "0"
    decoded = _through_the_line(payload)
    assert decoded.comment_count == 0  # type: ignore[attr-defined]
    assert type(decoded.comment_count) is int  # type: ignore[attr-defined]


@pytest.mark.parametrize("raw", ["many", "-1", "1.5", "", "٣"])
def test_a_hand_edited_count_that_is_not_digits_is_refused_naming_the_field(raw):
    """Digits only — `-1` included, and a non-ASCII digit too."""
    entry = _entry(
        ObservedEventType.REVIEW_SUBMITTED,
        (("p.target_ref", "main"), ("p.comment_count", raw)),
    )
    with pytest.raises(MalformedEntry) as caught:
        decode_payload(entry)
    assert "p.comment_count" in str(caught.value)


def test_a_negative_count_is_refused_at_encode_naming_the_field():
    with pytest.raises(UnencodablePayload) as caught:
        encode_payload(ReviewPayload(target_ref="main", comment_count=-1))
    assert "comment_count" in str(caught.value)


def test_a_list_of_two_is_joined_by_a_comma():
    payload = MessagePayload(channel="#payments", mentions=(U1, U2))
    assert f"p.mentions={U1},{U2}" in _line(payload)
    assert _through_the_line(payload).mentions == (U1, U2)  # type: ignore[attr-defined]


def test_a_handle_holding_a_backslash_is_escaped_by_the_list_layer_and_round_trips():
    member = str(person_handle("email", "a\\b@x.com"))
    payload = MessagePayload(channel="#p", mentions=(member, U1))
    assert dict(encode_payload(payload))["p.mentions"] == f"email:a\\\\b@x.com,{U1}"
    assert _through_the_line(payload).mentions == (member, U1)  # type: ignore[attr-defined]


@pytest.mark.usefixtures("plain_lists")
def test_an_element_holding_a_comma_or_a_backslash_is_escaped_and_round_trips():
    payload = MessagePayload(channel="#p", mentions=("a,b", "c\\d"))
    assert dict(encode_payload(payload))["p.mentions"] == "a\\,b,c\\\\d"
    assert _through_the_line(payload).mentions == ("a,b", "c\\d")  # type: ignore[attr-defined]


@pytest.mark.usefixtures("plain_lists")
def test_an_element_the_line_must_quote_is_quoted_by_the_line_layer():
    """The list layer has no opinion on spaces; the line layer quotes them, and
    a reader undoes the quoting before it splits."""
    payload = MessagePayload(channel="#p", mentions=("a b",))
    assert 'p.mentions="a b"' in _line(payload)
    assert _through_the_line(payload).mentions == ("a b",)  # type: ignore[attr-defined]


def test_an_empty_list_is_present_and_empty():
    """Not the same fact as `None`: the connector asked and there were none."""
    payload = MessagePayload(channel="#p", mentions=())
    assert dict(encode_payload(payload))["p.mentions"] == ""
    assert _line(payload).endswith(" p.mentions=")
    assert _through_the_line(payload).mentions == ()  # type: ignore[attr-defined]


def test_an_empty_element_is_refused_at_encode_naming_the_field():
    """Or `("",)` and `()` would share bytes."""
    with pytest.raises(UnencodablePayload) as caught:
        encode_payload(MessagePayload(channel="#p", mentions=("",)))
    assert "mentions" in str(caught.value)


@pytest.mark.parametrize(
    "raw",
    [f"{U1},", f"{U1}\\", f"{U1}\\x", f",{U1}", f"{U1},,{U2}"],
    ids=["trailing separator", "dangling escape", "escape before x", "leading", "doubled"],
)
def test_a_hand_edited_list_is_refused_naming_the_field(raw):
    entry = _hand_edited(ObservedEventType.MESSAGE_POSTED, f"p.channel=#p p.mentions={raw}")
    with pytest.raises(MalformedEntry) as caught:
        decode_payload(entry)
    assert "p.mentions" in str(caught.value)


def test_the_same_detail_twice_is_refused_naming_the_field():
    """By the parser on a line, and by the decoder on an entry built in memory."""
    with pytest.raises(MalformedEntry) as on_the_line:
        _hand_edited(ObservedEventType.MESSAGE_POSTED, "p.channel=#a p.channel=#b")
    assert "p.channel" in str(on_the_line.value)

    entry = _entry(
        ObservedEventType.MESSAGE_POSTED, (("p.channel", "#a"), ("p.channel", "#b"))
    )
    with pytest.raises(MalformedEntry) as in_memory:
        decode_payload(entry)
    assert "p.channel" in str(in_memory.value)


def test_a_field_the_class_does_not_know_is_ignored():
    """The grammar only grows, so a line from a later build stays readable."""
    entry = _hand_edited(ObservedEventType.MESSAGE_POSTED, "p.channel=#p p.later=1")
    assert decode_payload(entry) == MessagePayload(channel="#p")


def test_a_required_detail_missing_is_refused_naming_the_field():
    entry = _hand_edited(ObservedEventType.MESSAGE_POSTED, "p.excerpt=hello")
    with pytest.raises(MalformedEntry) as caught:
        decode_payload(entry)
    assert "p.channel" in str(caught.value)


def test_a_missing_detail_with_a_default_takes_its_default():
    entry = _hand_edited(ObservedEventType.REVIEW_SUBMITTED, "p.target_ref=main")
    assert decode_payload(entry) == ReviewPayload(target_ref="main", comment_count=0)


def test_an_envelope_field_is_not_mistaken_for_a_payload_field():
    """`src`, `ingested_at` and the rest sit beside `p.` fields and never reach
    the payload, even where a payload field shares the bare name."""
    entry = _hand_edited(
        ObservedEventType.MESSAGE_POSTED, "channel=#wrong p.channel=#right"
    )
    assert decode_payload(entry) == MessagePayload(channel="#right")


@pytest.mark.parametrize("action", list(SelfActionType), ids=lambda a: a.value)
def test_a_kind_with_no_payload_is_refused_naming_the_kind(action):
    entry = EventEntry(
        entry_id="evt_1",
        category=action,
        actor="pm-ai",
        fields=tuple(
            (name, "gitlab.post_comment" if name == "skill" else "x")
            for name in SELF_ACTION_FIELDS[action]
        ),
    )
    with pytest.raises(NoPayload) as caught:
        decode_payload(entry)
    assert action.value in str(caught.value)


def test_an_observed_category_with_no_registered_payload_is_refused_by_name(monkeypatch):
    """Never a bare `KeyError` out of `PAYLOAD_FOR`."""
    import pm_ai.domain.payload_fields as module

    unbound = {k: v for k, v in PAYLOAD_FOR.items() if k is not ObservedEventType.DECISION}
    monkeypatch.setattr(module, "PAYLOAD_FOR", unbound)
    entry = _entry(ObservedEventType.DECISION, (("p.statement", "ship"),))
    with pytest.raises(NoPayload) as caught:
        decode_payload(entry)
    assert "decision" in str(caught.value)


def test_a_count_too_long_to_read_is_refused_naming_the_field():
    """Python's int-string limit raises `ValueError`; it must arrive as the
    corrupted-line refusal like any other bad count."""
    entry = _entry(
        ObservedEventType.REVIEW_SUBMITTED,
        (("p.target_ref", "main"), ("p.comment_count", "9" * 5000)),
    )
    with pytest.raises(MalformedEntry) as caught:
        decode_payload(entry)
    assert "p.comment_count" in str(caught.value)


def test_an_empty_person_on_a_line_is_refused_naming_the_field():
    entry = _entry(
        ObservedEventType.WORK_ITEM_UPDATED,
        (("p.work_item_id", "PAY-1"), ("p.assignee", "")),
    )
    with pytest.raises(MalformedEntry) as caught:
        decode_payload(entry)
    assert "p.assignee" in str(caught.value)


@pytest.mark.parametrize(
    ("category", "fields", "named"),
    [
        (
            ObservedEventType.WORK_ITEM_UPDATED,
            (("p.work_item_id", "PAY-1"), ("p.assignee", UNRESOLVED_ACTOR)),
            "p.assignee",
        ),
        (
            ObservedEventType.WORK_ITEM_UPDATED,
            (("p.work_item_id", "PAY-1"), ("p.assignee", "email:Dana@X.com")),
            "p.assignee",
        ),
        (
            ObservedEventType.WORK_ITEM_UPDATED,
            (("p.work_item_id", "PAY-1"), ("p.assignee", "none:")),
            "p.assignee",
        ),
        (
            ObservedEventType.MESSAGE_POSTED,
            (("p.channel", "#p"), ("p.mentions", "u_1")),
            "p.mentions",
        ),
        (
            ObservedEventType.MESSAGE_POSTED,
            (("p.channel", "#p"), ("p.mentions", f"{U1},{UNRESOLVED_ACTOR}")),
            "p.mentions",
        ),
        (
            ObservedEventType.MESSAGE_POSTED,
            (("p.channel", "#p"), ("p.mentions", "none:")),
            "p.mentions",
        ),
    ],
    ids=[
        "assignee sentinel",
        "assignee not canonical",
        "assignee none",
        "mention not a handle",
        "mention sentinel",
        "mention none",
    ],
)
def test_a_person_value_on_a_line_that_is_not_a_handle_is_corrupted(category, fields, named):
    """The writer never writes any of these, so a line carrying one is a
    corrupted one — refused naming the field. Non-canonical text included, so a
    round trip stays byte-stable."""
    with pytest.raises(MalformedEntry) as caught:
        decode_payload(_entry(category, fields))
    assert named in str(caught.value)


def test_a_decode_refusal_names_the_entry_so_it_can_be_found():
    entry = EventEntry(
        entry_id="evt_find_me",
        category=ObservedEventType.MESSAGE_POSTED,
        actor="u_dana",
        fields=(("ingested_at", "2026-09-09T09:30:00+00:00"), ("p.excerpt", "hi")),
    )
    with pytest.raises(MalformedEntry) as caught:
        decode_payload(entry)
    text = str(caught.value)
    assert "evt_find_me" in text and "2026-09-09T09:30:00+00:00" in text
    assert "p.channel" in text


# ── The round trip over every payload class ──────────────────────────────────

EVERY_FIELD_SET: dict[type, object] = {
    CommitPayload: CommitPayload(
        sha="9f2a1c", message='Fix "the" build\nsecond line', branch="feature/a b"
    ),
    ReviewPayload: ReviewPayload(
        target_ref="main", verdict="approved", comment_count=12
    ),
    WorkItemPayload: WorkItemPayload(
        work_item_id="PAY-102",
        title="Cut latency, again",
        state="In Progress",
        assignee=DANA,
    ),
    PipelinePayload: PipelinePayload(pipeline_id="881", status="success"),
    DocumentPayload: DocumentPayload(document_id="doc-1", title="Runbook = v2"),
    MessagePayload: MessagePayload(
        channel="#payments",
        excerpt="Cache TTL is still 60s, in staging \\ prod",
        mentions=(U1, U2, str(DANA)),
    ),
    MeetingHeldPayload: MeetingHeldPayload(
        meeting_id="mtg_01", attendee_count=4, duration_minutes=30
    ),
    DecisionPayload: DecisionPayload(
        statement="Ship on Friday", rationale="The freeze starts Monday"
    ),
}


def test_the_round_trip_table_covers_every_payload_class():
    assert set(EVERY_FIELD_SET) == set(PAYLOAD_FOR.values())


@pytest.mark.parametrize(
    "payload", list(EVERY_FIELD_SET.values()), ids=lambda p: type(p).__name__
)
def test_every_field_set_round_trips(payload):
    """Every field set to a value other than its default, so nothing
    round-trips by being left off or by falling back to the default."""
    defaulted = [
        f.name
        for f in dataclasses.fields(payload)
        if getattr(payload, f.name) is None
        or (
            f.default is not dataclasses.MISSING
            and getattr(payload, f.name) == f.default
        )
    ]
    assert not defaulted, f"the table leaves {defaulted} at None or the default"
    assert _through_the_line(payload) == _reduced(payload)


@pytest.mark.parametrize("category", list(PAYLOAD_FOR), ids=lambda c: c.value)
def test_every_observed_category_decodes_to_its_registered_payload(category):
    """`MERGE_COMPLETED` and `REVIEW_SUBMITTED` share a class; each category
    still decodes to the class `PAYLOAD_FOR` binds it to."""
    payload = EVERY_FIELD_SET[PAYLOAD_FOR[category]]
    line = render_entry(_entry(category, encode_payload(payload)))
    assert decode_payload(parse_line(line)) == _reduced(payload)
