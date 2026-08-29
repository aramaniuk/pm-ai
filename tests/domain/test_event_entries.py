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


# ── Versioned, so a historical segment stays readable ────────────────────────


def test_the_grammar_version_is_recorded():
    assert isinstance(ee.GRAMMAR_VERSION, int)
    assert ee.GRAMMAR_VERSION >= 1


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
