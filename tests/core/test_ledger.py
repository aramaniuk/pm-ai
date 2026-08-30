"""Reading a segment back, and folding it in the one order AD-35 fixes.

Spec: `_bmad-output/specs/spec-pm-ai/stories/2f-segment-parser-and-deterministic-fold.md`.
"""

from __future__ import annotations

import pytest

from pm_ai.core import ledger
from pm_ai.domain.event_entries import (
    EventEntry,
    MalformedEntry,
    SelfActionType,
    UnknownCategory,
    render_entry,
)
from pm_ai.domain.events import ObservedEventType

ONE = "- [evt_a1] security actor=test detail=first\n"
TWO = "- [evt_b2] security actor=test detail=second\n"


# ── The append rule: a record without its newline is not a record ────────────


def test_a_whole_segment_parses_in_file_order():
    entries = ledger.parse_segment(ONE + TWO)
    assert [e.entry_id for e in entries] == ["evt_a1", "evt_b2"]


def test_an_unterminated_tail_is_a_boundary_not_corruption():
    """A reader landing mid-append must succeed, or every concurrent read fails."""
    entries = ledger.parse_segment(ONE + "- [evt_c3] security actor=test det")
    assert [e.entry_id for e in entries] == ["evt_a1"]


def test_an_empty_segment_is_empty_not_an_error():
    assert ledger.parse_segment("") == ()


def test_truncation_at_every_offset_never_raises():
    """The property the append rule exists to give, checked exhaustively."""
    whole = ONE + TWO
    for cut in range(len(whole) + 1):
        prefix = whole[:cut]
        entries = ledger.parse_segment(prefix)
        assert len(entries) == prefix.count("\n")


# ── A complete record that is wrong is loud ──────────────────────────────────


def test_a_malformed_complete_record_is_refused_with_its_line_number():
    with pytest.raises(MalformedEntry) as caught:
        ledger.parse_segment(ONE + "this is not an entry\n", source="2026-08.md")
    message = str(caught.value)
    assert "2026-08.md" in message
    # Not `"2" in message` — that passed via the `2026` in the filename.
    assert "line 2" in message


def test_a_category_outside_both_vocabularies_is_refused():
    with pytest.raises(UnknownCategory):
        ledger.parse_segment("- [evt_a1] not_a_category actor=test\n")


# ── Parse is the inverse of render ───────────────────────────────────────────


@pytest.mark.parametrize(
    "entry",
    [
        EventEntry(entry_id="evt_1", category=SelfActionType.COMPACTION, actor="pm-ai"),
        EventEntry(
            entry_id="evt_2",
            category=ObservedEventType.COMMIT_PUSHED,
            actor="u_42",
            fields=(("src", "gitlab:alpha:commit:9f2a1c"), ("occurred_at", "unknown")),
        ),
        EventEntry(
            entry_id="evt_3",
            category=ObservedEventType.DECISION,
            actor="u_42",
            fields=(("statement", 'ship "it" a\\b now'),),
        ),
    ],
    ids=["no-fields", "bare-values", "quoted-and-escaped"],
)
def test_render_then_parse_returns_the_original(entry):
    assert ledger.parse_line(render_entry(entry)) == entry


# ── AD-35: fold by (occurred_at, entry_id), never file order ─────────────────


def _at(entry_id: str, occurred_at: str):
    return EventEntry(
        entry_id=entry_id,
        category=SelfActionType.COMPACTION,
        actor="pm-ai",
        fields=(("occurred_at", occurred_at),),
    )


def test_folding_is_independent_of_input_order():
    entries = [
        _at("evt_c", "2026-08-19T10:00:00+00:00"),
        _at("evt_a", "2026-08-19T08:00:00+00:00"),
        _at("evt_b", "2026-08-19T09:00:00+00:00"),
    ]
    assert ledger.fold(entries) == ledger.fold(list(reversed(entries)))


def test_folding_orders_by_the_provider_clock_not_by_arrival():
    entries = [
        _at("evt_c", "2026-08-19T10:00:00+00:00"),
        _at("evt_a", "2026-08-19T08:00:00+00:00"),
    ]
    assert [e.entry_id for e in ledger.fold(entries)] == ["evt_a", "evt_c"]


def test_entries_sharing_a_timestamp_break_the_tie_on_the_id():
    same = "2026-08-19T08:00:00+00:00"
    entries = [_at("evt_b", same), _at("evt_a", same)]
    assert [e.entry_id for e in ledger.fold(entries)] == ["evt_a", "evt_b"]


def test_an_unknown_timestamp_sorts_to_one_end_deterministically():
    """Never arbitrarily: a rebuild must reproduce the live system's order."""
    entries = [
        _at("evt_b", "unknown"),
        _at("evt_a", "2026-08-19T08:00:00+00:00"),
    ]
    assert [e.entry_id for e in ledger.fold(entries)] == ["evt_a", "evt_b"]
    assert ledger.fold(entries) == ledger.fold(list(reversed(entries)))


def test_a_flagged_non_utc_timestamp_still_folds_deterministically():
    """2b writes these; the fold must not raise comparing naive against aware."""
    entries = [
        _at("evt_b", "2026-08-19T08:00:00"),
        _at("evt_a", "2026-08-19T08:00:00+00:00"),
    ]
    assert ledger.fold(entries) == ledger.fold(list(reversed(entries)))


def test_sample_entries_is_foldable():
    """The pre-written architecture test calls this by name."""
    assert ledger.fold(ledger.sample_entries()) == ledger.fold(
        list(reversed(ledger.sample_entries()))
    )


# ── Review findings, 2026-08-30 ─────────────────────────────────────────────


def test_an_empty_entry_id_is_refused_by_the_parser():
    """Parse must not accept what render refuses: empty ids collide in `fold`."""
    with pytest.raises(MalformedEntry):
        ledger.parse_line("- [] security actor=x")


def test_an_unknown_category_refusal_names_the_segment_and_the_line():
    """`UnknownCategory` is the corruption most likely in an old segment, and it
    was the one refusal that could not say where it came from."""
    with pytest.raises((UnknownCategory, MalformedEntry)) as caught:
        ledger.parse_segment(ONE + "- [evt_2] not_a_category actor=x\n", source="2026-08.md")
    message = str(caught.value)
    assert "2026-08.md" in message and "line 2" in message


def test_the_malformed_refusal_names_the_line_number_itself():
    """The old assertion checked `"2" in message`, which passes via `2026`."""
    with pytest.raises(MalformedEntry) as caught:
        ledger.parse_segment(ONE + "this is not an entry\n", source="2026-08.md")
    assert "line 2" in str(caught.value)
