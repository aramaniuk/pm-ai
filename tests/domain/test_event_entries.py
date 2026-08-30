"""The two ledger vocabularies, and the roles that keep them apart.

Spec: `_bmad-output/specs/spec-pm-ai/stories/2c-closed-entry-type-enumeration.md`.

The roles are the thing preventing duplication, so they are asserted rather than
described: disjoint values, and each member legal only in its own context.
"""

from __future__ import annotations

import pytest

from pm_ai.domain import event_entries as ee
from pm_ai.domain.events import PAYLOAD_FOR, ObservedEventType


# ── One occurrence, one member ───────────────────────────────────────────────


def test_the_two_vocabularies_share_no_value():
    """The break: the same real occurrence spellable two ways, so a parser and a
    writer can disagree about which enum a segment line came from."""
    observed = {m.value for m in ObservedEventType}
    acted = {m.value for m in ee.SelfActionType}
    assert observed & acted == set()


def test_no_self_action_can_be_constructed_as_an_observed_event():
    """The role boundary, in the one place it is load-bearing.

    `NormalizedEvent.__post_init__` indexes `PAYLOAD_FOR` directly, so a member
    present there is constructible as a harvested event. No pm-ai action may be.
    """
    for member in ee.SelfActionType:
        assert member not in PAYLOAD_FOR


def test_decision_is_an_observed_event_because_the_pm_decided_it():
    """pm-ai witnessed the decision through a transcript; it did not make it."""
    assert ObservedEventType.DECISION in PAYLOAD_FOR
    assert "decision" not in {m.value for m in ee.SelfActionType}


# ── Every member is typed ────────────────────────────────────────────────────


def test_every_self_action_has_a_registered_payload():
    for member in ee.SelfActionType:
        assert member in ee.SELF_ACTION_PAYLOAD_FOR


def test_every_observed_event_has_a_registered_payload():
    for member in ObservedEventType:
        assert member in PAYLOAD_FOR


def test_a_compaction_payload_carries_what_it_deleted_and_what_replaced_it():
    """storage-contract.md: checksums, because a filename is reused and content is not."""
    payload = ee.CompactionPayload(
        source="event_log/2026-06",
        replaced=(("2026-06.md", "d41d8cd9"),),
        summary=("2026-06-milestone.md", "9b2c77e0"),
    )
    assert payload.replaced == (("2026-06.md", "d41d8cd9"),)
    assert payload.summary == ("2026-06-milestone.md", "9b2c77e0")


def test_a_skill_invocation_payload_carries_its_idempotency_key():
    payload = ee.SkillInvokedPayload(
        skill="gitlab.comment",
        target="gitlab:alpha:issue:PAY-102",
        external_id="note_88",
        idempotency_key="idem_abc",
    )
    assert payload.idempotency_key == "idem_abc"


# ── Resolving a category off the wire ────────────────────────────────────────


def test_an_operational_category_resolves_to_its_member():
    assert ee.category("compaction") is ee.SelfActionType.COMPACTION


def test_an_observed_category_resolves_to_its_member():
    assert ee.category("commit_pushed") is ObservedEventType.COMMIT_PUSHED


def test_an_unregistered_category_is_refused():
    with pytest.raises(ee.UnknownCategory):
        ee.category("security_note")


def test_the_refusal_names_the_value_and_the_closed_set():
    with pytest.raises(ee.UnknownCategory) as caught:
        ee.category("security_note")
    message = str(caught.value)
    assert "security_note" in message
    assert "compaction" in message


# ── The guards fire, and are not bare asserts ────────────────────────────────


def test_the_disjointness_guard_refuses_an_overlapping_value(monkeypatch):
    """The break: a member added to one enum that already exists in the other."""

    monkeypatch.setattr(
        ee, "SELF_ACTION_VALUES", {m.value for m in ee.SelfActionType} | {"commit_pushed"}
    )
    with pytest.raises(ee.InconsistentVocabulary):
        ee._assert_vocabularies_agree()


def test_the_payload_coverage_guard_refuses_an_unregistered_member(monkeypatch):
    monkeypatch.setattr(
        ee, "SELF_ACTION_PAYLOAD_FOR", {ee.SelfActionType.COMPACTION: ee.CompactionPayload}
    )
    with pytest.raises(ee.InconsistentVocabulary):
        ee._assert_vocabularies_agree()


# ═══════════════════════════════════════════════════════════════════════════
# 2d — one renderer
#
# The golden line below was captured from `_append_batch` before this story
# touched it, with the minted id substituted. It is a literal on purpose: an
# expectation built by the renderer would agree with any format it produced.
# ═══════════════════════════════════════════════════════════════════════════

GOLDEN = (
    "- [evt_0000000000000000000f] commit_pushed actor=u_42 "
    "src=gitlab:alpha:commit:9f2a1c occurred_at=2026-08-19T08:00:00+00:00 "
    "ingested_at=2026-08-19T09:00:00+00:00 authored_by=unknown"
)


def _harvest_entry():
    return ee.EventEntry(
        entry_id="evt_0000000000000000000f",
        category=ObservedEventType.COMMIT_PUSHED,
        actor="u_42",
        fields=(
            ("src", "gitlab:alpha:commit:9f2a1c"),
            ("occurred_at", "2026-08-19T08:00:00+00:00"),
            ("ingested_at", "2026-08-19T09:00:00+00:00"),
            ("authored_by", "unknown"),
        ),
    )


def test_a_harvested_entry_renders_the_line_the_ledger_already_holds():
    """The break: a format drift that silently orphans every segment on disk."""
    assert ee.render_entry(_harvest_entry()) == GOLDEN


def test_rendering_is_deterministic():
    """No clock read, no id minting — the entry carries everything."""
    assert ee.render_entry(_harvest_entry()) == ee.render_entry(_harvest_entry())


def test_the_rendered_line_carries_no_newline():
    """The writer terminates the record; the renderer must not pre-empt it."""
    assert "\n" not in ee.render_entry(_harvest_entry())


# ── A record boundary cannot be forged ───────────────────────────────────────


def test_a_value_containing_a_newline_is_refused():
    entry = ee.EventEntry(
        entry_id="evt_1", category=ee.SelfActionType.SECURITY, actor="pm-ai",
        fields=(("detail", "first\nsecond"),),
    )
    with pytest.raises(ee.MalformedEntry):
        ee.render_entry(entry)


def test_an_actor_containing_a_newline_is_refused():
    entry = ee.EventEntry(
        entry_id="evt_1", category=ee.SelfActionType.SECURITY, actor="pm\nai", fields=()
    )
    with pytest.raises(ee.MalformedEntry):
        ee.render_entry(entry)


def test_a_key_containing_a_separator_is_refused():
    """A key is a bare token; a space or `=` in one shifts every field after it."""
    for bad in ("two words", "has=equals"):
        entry = ee.EventEntry(
            entry_id="evt_1", category=ee.SelfActionType.SECURITY, actor="pm-ai",
            fields=((bad, "v"),),
        )
        with pytest.raises(ee.MalformedEntry):
            ee.render_entry(entry)


# ── Values that need quoting get it, and only those ──────────────────────────


def test_a_value_containing_a_space_is_quoted():
    entry = ee.EventEntry(
        entry_id="evt_1", category=ObservedEventType.DECISION, actor="u_42",
        fields=(("statement", "ship the thing"),),
    )
    assert 'statement="ship the thing"' in ee.render_entry(entry)


def test_a_value_containing_a_quote_or_backslash_is_escaped():
    entry = ee.EventEntry(
        entry_id="evt_1", category=ObservedEventType.DECISION, actor="u_42",
        fields=(("statement", 'say "hi" a\\b'),),
    )
    assert r'statement="say \"hi\" a\\b"' in ee.render_entry(entry)


def test_a_value_needing_no_quoting_stays_bare():
    """Byte-compatibility rests on this: today's values must not gain quotes."""
    assert 'actor=u_42 ' in ee.render_entry(_harvest_entry())
    assert '"' not in ee.render_entry(_harvest_entry())


# ── A line stays readable ────────────────────────────────────────────────────


def test_a_line_beyond_the_bound_is_refused():
    entry = ee.EventEntry(
        entry_id="evt_1", category=ObservedEventType.DECISION, actor="u_42",
        fields=(("statement", "x" * (ee.MAX_ENTRY_LENGTH + 1)),),
    )
    with pytest.raises(ee.MalformedEntry) as caught:
        ee.render_entry(entry)
    assert str(ee.MAX_ENTRY_LENGTH) in str(caught.value)


# ── 2e: the id is storage's to mint (AD-34) ──────────────────────────────────


def test_an_entry_without_an_id_cannot_be_rendered():
    """The break: a caller building a whole line and bypassing the mint."""
    with pytest.raises(ee.MalformedEntry) as caught:
        ee.render_entry(ee.EventEntry(category=ee.SelfActionType.SECURITY, actor="pm-ai"))
    assert "storage" in str(caught.value).lower()


def test_an_entry_is_constructible_without_an_id():
    """Callers outside storage build entries; they do not name them."""
    entry = ee.EventEntry(category=ee.SelfActionType.SECURITY, actor="pm-ai")
    assert entry.entry_id is None


def test_the_leak_guard_refuses_a_self_action_registered_as_an_event_payload(monkeypatch):
    """The third branch of `_assert_vocabularies_agree`, previously untested.

    A pm-ai action registered in `PAYLOAD_FOR` becomes constructible as a
    `NormalizedEvent`, and `persist_events` would then deduplicate the second
    occurrence away — losing audit records by design.
    """
    monkeypatch.setitem(PAYLOAD_FOR, ee.SelfActionType.COMPACTION, ee.CompactionPayload)
    with pytest.raises(ee.InconsistentVocabulary) as caught:
        ee._assert_vocabularies_agree()
    assert "COMPACTION" in str(caught.value)
