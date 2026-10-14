"""A payload's content reaches Tier 1, so a Tier-3 index can be rebuilt from it.

Spec: `_bmad-output/specs/spec-pm-ai/stories/2l-payloads-reach-tier-one.md`.
"""

from __future__ import annotations

import dataclasses
import pathlib
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from claim_fakes import InMemoryClaims
from pm_ai.core import ledger
from pm_ai.domain.event_entries import EventEntry, MalformedEntry, SelfActionType, render_entry
from pm_ai.core.rendering import PmHandles, render_project_dashboard
from pm_ai.domain.events import (
    PAYLOAD_FOR,
    CommitPayload,
    MessagePayload,
    NormalizedEvent,
    ObservedEventType,
    WorkItemPayload,
)
from pm_ai.domain.handles import NO_PERSON, UNRESOLVED_SENTINEL, person_handle
from pm_ai.domain.identity import DataScope, ScopeKind, SourceRef
from pm_ai.domain.payload_fields import UnencodablePayload, decode_payload
from pm_ai.platform.paths import ScopePaths
from pm_ai.platform.vcs import GitVcs
from pm_ai.storage.crypto import AesGcmCrypto
from pm_ai.storage.service import StorageService

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)
DANA_GUID = "0000da4a-0000-4000-8000-000000000001"
DANA = person_handle("graph", DANA_GUID)
U1 = "graph:00000001-0000-4000-8000-000000000001"
U2 = "graph:00000002-0000-4000-8000-000000000002"
SCOPE = DataScope(ScopeKind.PERSONAL)
BODY = "Fix the auth refactor\n\nThe token was refreshed twice."


def _storage():
    return StorageService(
        ScopePaths.rooted(pathlib.Path(tempfile.mkdtemp())),
        now=lambda: NOW, vcs=GitVcs(), crypto=AesGcmCrypto(b"0" * 32),
        claims=InMemoryClaims(),
    )


def _commit(**kw):
    payload = CommitPayload(**{"sha": "9f2a1c", "message": BODY, "branch": "main", **kw})
    return NormalizedEvent(
        scope=SCOPE, type=ObservedEventType.COMMIT_PUSHED,
        source_ref=SourceRef.parse("gitlab:alpha:commit:9f2a1c"),
        actor=person_handle("email", "ada@example.com"),
        occurred_at=datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
        payload=payload,
    )


def _persist(event):
    st = _storage()
    st.persist_events((event,), scope=SCOPE)
    text = (st.paths.resolve(SCOPE, "event_log/") / "2026-08.md").read_text()
    return text, ledger.parse_segment(text)[0]


# ── The gap this story closes ───────────────────────────────────────────────


def test_a_commit_message_reaches_tier_one():
    """It did not, which is why a search index over it could not be rebuilt from
    Tier 1 — and story 1h's proof passed anyway, checking only what was there."""
    _, entry = _persist(_commit())
    assert dict(entry.fields)["p.message"] == BODY


def test_every_non_none_payload_field_reaches_the_line():
    _, entry = _persist(_commit())
    fields = dict(entry.fields)
    for declared in dataclasses.fields(CommitPayload):
        assert f"p.{declared.name}" in fields, f"{declared.name} was dropped"


def test_an_unset_optional_payload_field_is_omitted():
    """Absent and empty are different facts, as they are for `occurred_at`."""
    _, entry = _persist(_commit(branch=None))
    assert "p.branch" not in dict(entry.fields)


def test_a_new_payload_field_needs_no_registry_change():
    """Derived from the dataclass: nothing to update, so nothing to forget."""
    declared = {f.name for f in dataclasses.fields(CommitPayload)}
    _, entry = _persist(_commit())
    written = {k[2:] for k in dict(entry.fields) if k.startswith("p.")}
    assert written == declared - {"branch"} | {"branch"}


# ── The record boundary survives content that contains newlines ─────────────


def test_a_multi_line_message_leaves_no_literal_newline_in_the_line():
    text, _ = _persist(_commit())
    assert len(text.rstrip("\n").split("\n")) == 1, "the payload forged a record boundary"


def test_a_value_containing_a_literal_backslash_n_round_trips_distinctly():
    """`C:\\next` must not come back as a newline, nor a newline as the letter n."""
    entry = EventEntry(
        entry_id="evt_1", category=SelfActionType.SECURITY, actor="pm-ai",
        fields=(("protection", "encryption-at-rest"), ("disabled_by", r"C:\next"),
                ("note", "a\nb")),
    )
    back = ledger.parse_line(render_entry(entry))
    assert dict(back.fields)["disabled_by"] == r"C:\next"
    assert dict(back.fields)["note"] == "a\nb"


# ── The schema is a floor, not a cage ───────────────────────────────────────


def test_a_producer_may_add_a_field_the_schema_does_not_name():
    EventEntry(
        category=SelfActionType.SECURITY, actor="pm-ai",
        fields=(("protection", "x"), ("disabled_by", "y"), ("extra", "allowed")),
    )


def test_a_producer_dropping_a_required_field_is_refused():
    with pytest.raises(MalformedEntry) as caught:
        EventEntry(category=SelfActionType.SECURITY, actor="pm-ai",
                   fields=(("protection", "x"),))
    assert "disabled_by" in str(caught.value)


# ── Story 2m: through the one encode/decode pair ────────────────────────────
# Spec: `_bmad-output/specs/spec-pm-ai/stories/2m-payload-line-grammar.md`.

PROJECT = DataScope(ScopeKind.PROJECT, project_id="alpha")
DISPLAY = timezone(timedelta(hours=2))


def _persist_in(scope: DataScope, *events: NormalizedEvent) -> tuple[str, tuple[EventEntry, ...]]:
    st = _storage()
    st.persist_events(events, scope=scope)
    text = (st.paths.resolve(scope, "event_log/") / "2026-08.md").read_text()
    return text, ledger.parse_segment(text)


def _event(type_: ObservedEventType, ref: str, payload: object) -> NormalizedEvent:
    return NormalizedEvent(
        scope=PROJECT, type=type_, source_ref=SourceRef.parse(ref),
        actor=DANA,
        occurred_at=datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
        payload=payload,
    )


def test_a_message_with_mentions_is_persisted_parsed_decoded_and_rendered():
    """The first acceptance criterion: what the connector supplied comes back,
    and the dashboard line shows channel and excerpt. Until `23c` and `2o` the
    sender is printed as the handle text (accepted interim display, story 2n)."""
    supplied = MessagePayload(
        channel="#payments", excerpt="Cache TTL is still 60s in staging",
        mentions=(U1, U2),
    )
    _, (entry,) = _persist_in(
        PROJECT, _event(ObservedEventType.MESSAGE_POSTED, "graph:alpha:message:m1", supplied)
    )
    assert decode_payload(entry) == supplied

    rendered = render_project_dashboard(
        (), (entry,), NOW + timedelta(hours=1), tz=DISPLAY,
        pm=PmHandles(handles=frozenset(), unrecorded=(), unchecked=()),
    )
    assert (
        f"- **11:00** graph:{DANA_GUID} in #payments — Cache TTL is still 60s in staging"
        in rendered
    )


def test_an_assignee_reaches_the_line_as_its_handle_text_and_decodes_equal():
    """Matrix: assignee — `p.assignee=email:dana@example.com`; decodes the equal handle."""
    handle = person_handle("email", " Dana@Example.com ")
    supplied = WorkItemPayload(work_item_id="PAY-102", assignee=handle)
    text, (entry,) = _persist_in(
        PROJECT, _event(ObservedEventType.WORK_ITEM_UPDATED, "gitlab:alpha:issue:PAY-102", supplied)
    )
    assert "p.assignee=email:dana@example.com" in text
    assert decode_payload(entry).assignee == handle  # type: ignore[attr-defined]


def test_a_sender_and_a_mention_of_one_person_are_joinable_on_the_line():
    """The envelope actor and a `mentions` member are the same handle text."""
    message = MessagePayload(channel="#payments", mentions=(str(DANA),))
    text, (entry,) = _persist_in(
        PROJECT, _event(ObservedEventType.MESSAGE_POSTED, "graph:alpha:message:m1", message)
    )
    assert f"actor=graph:{DANA_GUID} " in text
    assert f"p.mentions=graph:{DANA_GUID}" in text
    assert decode_payload(entry).mentions == (str(DANA),)  # type: ignore[attr-defined]
    assert UNRESOLVED_SENTINEL not in text


def test_no_person_as_an_assignee_is_refused_before_anything_is_written():
    st = _storage()
    event = _event(
        ObservedEventType.WORK_ITEM_UPDATED, "gitlab:alpha:issue:PAY-102",
        WorkItemPayload(work_item_id="PAY-102", assignee=NO_PERSON),
    )
    with pytest.raises(UnencodablePayload) as caught:
        st.persist_events((event,), scope=PROJECT)
    assert "assignee" in str(caught.value)
    segment = st.paths.resolve(PROJECT, "event_log/") / "2026-08.md"
    assert not segment.exists() or segment.read_text() == ""


@pytest.mark.parametrize("mentions", [(U1, U2, "email:a@x.com"), ()], ids=["three", "empty"])
def test_a_mentions_list_survives_the_writer_and_the_parser(mentions):
    supplied = MessagePayload(channel="#payments", mentions=mentions)
    _, (entry,) = _persist_in(
        PROJECT, _event(ObservedEventType.MESSAGE_POSTED, "graph:alpha:message:m1", supplied)
    )
    assert decode_payload(entry) == supplied


def test_a_batch_with_one_unencodable_event_writes_nothing():
    """All or nothing: three good events and one with `none:` as its
    assignee leave no segment line and no `seen` row, so the good three are
    still new to the next harvest."""
    st = _storage()
    good = tuple(
        _event(
            ObservedEventType.MESSAGE_POSTED, f"graph:alpha:message:m{n}",
            MessagePayload(channel="#payments", mentions=(U1,)),
        )
        for n in range(3)
    )
    bad = _event(
        ObservedEventType.WORK_ITEM_UPDATED, "gitlab:alpha:issue:PAY-102",
        WorkItemPayload(work_item_id="PAY-102", assignee=NO_PERSON),
    )
    with pytest.raises(UnencodablePayload):
        st.persist_events(good + (bad,), scope=PROJECT)

    segment = st.paths.resolve(PROJECT, "event_log/") / "2026-08.md"
    assert not segment.exists() or segment.read_text() == ""
    assert st._db.execute("SELECT COUNT(*) FROM seen").fetchone() == (0,)
    assert st.persist_events(good, scope=PROJECT).persisted == 3
