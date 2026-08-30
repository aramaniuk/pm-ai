"""A payload's content reaches Tier 1, so a Tier-3 index can be rebuilt from it.

Spec: `_bmad-output/specs/spec-pm-ai/stories/2l-payloads-reach-tier-one.md`.
"""

from __future__ import annotations

import dataclasses
import pathlib
import tempfile
from datetime import datetime, timezone

import pytest

from pm_ai.core import ledger
from pm_ai.domain.event_entries import EventEntry, MalformedEntry, SelfActionType, render_entry
from pm_ai.domain.events import CommitPayload, NormalizedEvent, ObservedEventType, PAYLOAD_FOR
from pm_ai.domain.identity import Actor, DataScope, ScopeKind, SourceRef
from pm_ai.platform.paths import ScopePaths
from pm_ai.platform.vcs import GitVcs
from pm_ai.storage.crypto import AesGcmCrypto
from pm_ai.storage.service import StorageService

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)
SCOPE = DataScope(ScopeKind.PERSONAL)
BODY = "Fix the auth refactor\n\nThe token was refreshed twice."


def _storage():
    return StorageService(
        ScopePaths.rooted(pathlib.Path(tempfile.mkdtemp())),
        now=lambda: NOW, vcs=GitVcs(), crypto=AesGcmCrypto(b"0" * 32),
    )


def _commit(**kw):
    payload = CommitPayload(**{"sha": "9f2a1c", "message": BODY, "branch": "main", **kw})
    return NormalizedEvent(
        scope=SCOPE, type=ObservedEventType.COMMIT_PUSHED,
        source_ref=SourceRef.parse("gitlab:alpha:commit:9f2a1c"),
        actor=Actor(actor_id="u_42", display_name="Ada"),
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
