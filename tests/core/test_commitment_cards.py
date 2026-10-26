"""Story 15c — one card for a commitment.

Every heading word, the past-due rules, the compact form's lines as the full
form's first lines, and each action's availability from `mark_answer`.
Fixtures use `11d`'s names; no real meeting is used.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from pm_ai.core.commitment_cards import (
    build_card,
    card_json,
    card_text,
    past_due,
    valid_version,
)
from pm_ai.core.commitment_log import (
    CommitmentLine,
    DueSet,
    Marked,
    OwnerSet,
    Pending,
    entry_id,
    fold,
    from_approved,
    parse_ledger,
    render_line,
)
from pm_ai.core.meeting_records import MeetingRecord
from pm_ai.domain.commitments import Mark, MarkAnswer, StateSource, mark_answer
from pm_ai.domain.identity import Actor, DataScope, ScopeKind, SourceRef
from pm_ai.domain.lifecycle import CommitmentState, ProposalState
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.proposals import Proposal

UTC = timezone.utc
START = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
APPROVED = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
TODAY = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
ROOT = "prp_4a0a339b0c6d_mtg_a_ai_1e97a541b3ff"
PERSONAL = DataScope(ScopeKind.PERSONAL)


def _record(**changes) -> MeetingRecord:
    meeting = Meeting(
        meeting_id="mtg_a",
        title="Example Weekly Sync-up",
        start=START,
        duration_minutes=30,
        attendees=(Actor("email:alex@example.com"),),
        scope=PERSONAL,
    )
    return MeetingRecord(meeting=replace(meeting, **changes))


def _item(*, kind="action_item", due="by Thursday", owner="Sam Rivera", what="send the cache TTL numbers") -> Proposal:
    payload: dict = {"what": what, "owner": owner, "due": due}
    if kind == "implicit_commitment":
        payload = {
            "what": {"raw": what, "for_model": what},
            "when": {"raw": due, "for_model": due} if due else None,
            "speaker": {"raw": "Sam Rivera", "for_model": "Sam Rivera"},
        }
    return Proposal(
        proposal_id=ROOT,
        type=kind,
        summary=what[:80],
        payload=payload,
        target=None,
        cites=SourceRef.parse("meeting:mtg_a"),
        created_at=START,
        state=ProposalState.APPROVED,
        version=2,
        ttl=timedelta(days=14),
        staged_at=START,
        scope="personal",
        changed_at=APPROVED,
        changed_via="terminal",
    )


def _pending(proposal: Proposal | None = None, record: MeetingRecord | None = None) -> CommitmentLine:
    commitment = from_approved(proposal or _item(), record=record or _record(), zone=None)
    return render_line(Pending(commitment, via="terminal", written="approval", at=APPROVED))


def _mark(state: str, minutes: int, note: str | None = None) -> CommitmentLine:
    at = APPROVED + timedelta(minutes=minutes)
    return render_line(Marked(ROOT, Mark(state), occurred=at, via="terminal", at=at, note=note))


def _verdict(state: str, minutes: int) -> str:
    at = (APPROVED + timedelta(minutes=minutes)).isoformat()
    entry = entry_id((ROOT, "verdict", state, at))
    return f"- entry={entry} commitment={ROOT} kind=verdict state={state} occurred={at} at={at}"


def _card(*lines: CommitmentLine | str, now: datetime = TODAY):
    text = "".join((line.text if isinstance(line, CommitmentLine) else line) + "\n" for line in lines)
    ledger = fold(parse_ledger(text.encode()))
    return build_card(ledger.commitments[ROOT], ledger.details[ROOT], scope="personal", now=now, zone=UTC)


# ── The heading ──────────────────────────────────────────────────────────────


def test_the_full_card_reads_as_the_spec_shows_it():
    card = _card(_pending())
    text = card_text(card)
    lines = text.splitlines()
    assert lines[0] == "Commitment, open, due Thu 22 Oct, past due"
    assert lines[1] == '  Sam Rivera: send the cache TTL numbers ("by Thursday")'
    assert lines[2] == "  from Example Weekly Sync-up, Mon 19 Oct 10:00, personal; approved Mon 19 Oct"
    version = card.version
    assert f"  done:      pm-ai commitment done {ROOT} --scope personal --if-version {version}" in lines
    assert any(line.startswith("  altered:   pm-ai commitment altered") and "--note" in line for line in lines)
    assert any(line.startswith(f"  due:       pm-ai commitment due {ROOT}") for line in lines)
    assert any(line.startswith("  undo:      unavailable:") for line in lines)


@pytest.mark.parametrize(
    ("lines", "heading"),
    [
        ([_mark("altered", 1)], "Commitment, open, altered on Mon 19 Oct"),
        ([_mark("fulfilled", 1)], "Commitment, done on Mon 19 Oct"),
        ([_mark("broken", 1)], "Commitment, broken on Mon 19 Oct"),
        ([_mark("withdrawn", 1)], "Commitment, withdrawn on Mon 19 Oct"),
        ([_verdict("unknown", 1)], "Commitment, open, pm-ai could not check by telemetry"),
        ([_verdict("error", 1)], "Commitment, open, a check failed by telemetry"),
        ([_verdict("fulfilled", 1)], "Commitment, done on Mon 19 Oct by telemetry"),
    ],
)
def test_every_state_has_its_heading_words(lines, heading):
    card = _card(_pending(), *lines)
    assert card.heading.startswith(heading)


def test_an_estimated_date_is_never_past_due():
    """Matrix "Estimated date"."""
    card = _card(_pending(record=_record(start_estimated_from="header")))
    assert "due Thu 22 Oct (meeting day estimated)" in card.heading
    assert "past due" not in card.heading and card_json(card)["past_due"] is False


def test_a_commitment_due_today_is_not_past_due():
    """Matrix "Due today"."""
    card = _card(_pending(), now=datetime(2026, 10, 22, 9, 0, tzinfo=UTC))
    assert card.past_due is False and "past due" not in card.heading


def test_a_closed_commitment_is_never_past_due():
    card = _card(_pending(), _mark("broken", 1))
    assert card.past_due is False


def test_unresolved_and_none_are_never_past_due():
    unresolved = _card(_pending(_item(due="next sprint")))
    assert 'no due date worked out from "next sprint"' in unresolved.heading
    assert unresolved.past_due is False
    none = _card(_pending(_item(due=None)))
    assert "no due date said" in none.heading and none.past_due is False
    assert past_due(none.commitment, date(2030, 1, 1)) is False


def test_the_pms_date_says_set_by_you_and_keeps_the_due_as_said():
    at = APPROVED + timedelta(minutes=2)
    due_set = render_line(DueSet(ROOT, date(2026, 10, 30), occurred=at, via="terminal", at=at))
    card = _card(_pending(_item(due="next sprint")), due_set)
    assert card.heading == "Commitment, open, due Fri 30 Oct, set by you"
    found = card_json(card)
    assert found["due"] == {
        "said": "next sprint", "value": "2026-10-30", "date": "2026-10-30",
        "estimated": False, "by_pm": True,
    }


def test_a_promise_reads_said_by_and_the_owner_the_pm_set():
    at = APPROVED + timedelta(minutes=2)
    owner = render_line(OwnerSet(ROOT, "Sam Rivera", occurred=at, via="terminal", at=at))
    card = _card(_pending(_item(kind="implicit_commitment", due="Friday", what="send the numbers")), owner)
    assert card.field_line == 'said by Sam Rivera, owner Sam Rivera: send the numbers ("Friday")'


def test_a_line_with_no_title_says_the_record_was_unavailable():
    commitment = from_approved(_item(), record=None, zone=None)
    line = render_line(Pending(commitment, via="terminal", written="approval", at=APPROVED))
    card = _card(line)
    assert card.origin == (
        "from meeting mtg_a (meeting record unavailable when it was approved), personal; approved Mon 19 Oct"
    )


def test_the_pms_note_is_shown():
    card = _card(_pending(), _mark("altered", 1, note="scope cut to the API only"))
    assert "  note: scope cut to the API only" in card_text(card).splitlines()


# ── The compact form ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("extra", [(), (_mark("altered", 1, note="a note"),), (_mark("fulfilled", 1),)])
def test_the_compact_forms_lines_are_the_full_forms_first_lines(extra):
    card = _card(_pending(), *extra)
    full = card_text(card).splitlines()
    compact = card_text(card, compact=True).splitlines()
    assert full[: len(compact)] == compact
    assert not any("pm-ai commitment" in line or "--if-version" in line for line in compact)
    assert len(full) > len(compact)


# ── Availability, from `mark_answer` ─────────────────────────────────────────


@pytest.mark.parametrize(
    "lines",
    [
        (),
        (_mark("altered", 1),),
        (_mark("fulfilled", 1),),
        (_mark("broken", 1),),
        (_mark("withdrawn", 1),),
        (_verdict("fulfilled", 1),),
        (_verdict("unknown", 1),),
    ],
)
def test_each_mark_is_available_exactly_when_the_table_says_yes(lines):
    card = _card(_pending(), *lines)
    for verb, mark in (("done", Mark.FULFILLED), ("altered", Mark.ALTERED), ("broken", Mark.BROKEN), ("withdrawn", Mark.WITHDRAWN)):
        action = card.action(verb)
        assert action is not None
        answer = mark_answer(card.commitment.state, card.commitment.state_source, mark)
        assert action.available is (answer is MarkAnswer.YES)
        assert (action.command is not None) is action.available
        if not action.available:
            assert action.reason


def test_a_done_mark_refuses_altered_and_withdrawn_with_the_tables_reason():
    card = _card(_pending(), _mark("fulfilled", 1))
    reason = card.action("altered").reason
    assert reason == (
        "you marked it done on Mon 19 Oct, so it is not altered or withdrawn; mark it broken "
        "if it was not done after all"
    )
    assert card.action("done").reason == "you already marked it done on Mon 19 Oct"


def test_due_and_owner_are_unavailable_on_a_closed_commitment():
    """Matrix "Closed card"."""
    card = _card(_pending(), _mark("broken", 1))
    found = card_json(card)
    for name in ("due", "owner"):
        assert found["actions"][name]["available"] is False
        assert "you marked it broken on Mon 19 Oct" in found["actions"][name]["reason"]
        assert "pm-ai commitment altered" in found["actions"][name]["reason"]
    done = _card(_pending(), _mark("fulfilled", 1), now=APPROVED + timedelta(days=1))
    assert done.action("due").reason == "you marked it done on Mon 19 Oct; it is closed"


def test_undo_is_offered_while_the_window_is_open():
    """Matrix "Undo offered": until 4 h after the mark, then unavailable with its reason."""
    inside = _card(_pending(), _mark("fulfilled", 0), now=APPROVED + timedelta(hours=1))
    action = inside.action("undo")
    assert action.available and action.reason == "until Mon 19 Oct 16:00"
    assert action.command[-2:] == ("--if-version", inside.version)
    after = _card(_pending(), _mark("fulfilled", 0), now=APPROVED + timedelta(hours=4, minutes=1))
    assert after.action("undo").available is False
    assert "more than 4 hours ago" in after.action("undo").reason


def test_undo_is_not_offered_for_a_telemetry_done():
    card = _card(_pending(), _verdict("fulfilled", 1))
    assert card.action("undo").available is False
    assert card.action("undo").reason == "telemetry recorded it done; pm-ai undoes only your own done mark"


def test_the_json_card_has_every_field():
    found = card_json(_card(_pending()))
    assert set(found) == {
        "commitment_id", "version", "scope", "also_in", "type", "proposal_id", "state",
        "state_source", "what", "what_cut", "owner", "owner_by_pm", "speaker", "due",
        "past_due", "day", "day_from", "meeting", "approved_at", "changed_at", "mark", "actions",
    }
    assert set(found["actions"]) == {"done", "altered", "broken", "withdrawn", "due", "owner", "undo", "show"}
    assert found["meeting"] == {"meeting_id": "mtg_a", "title": "Example Weekly Sync-up", "start": START.isoformat()}
    assert valid_version(found["version"])
    assert found["state"] == CommitmentState.PENDING.value and found["state_source"] == StateSource.NONE.value


@pytest.mark.parametrize("text", ["42", "cml_ABCDEF123456", "cml_12345", "cml_0123456789abc"])
def test_a_version_is_cml_and_twelve_lower_case_hex(text):
    assert valid_version(text) is False
    assert valid_version("cml_0123456789ab") is True


def test_the_closed_reason_names_undo_with_its_version():
    """Review finding 8: the reopening `undo` command carries `--if-version`, as the card's does."""
    card = _card(_pending(), _mark("fulfilled", 0), now=APPROVED + timedelta(hours=1))
    reason = card.action("due").reason
    assert f"pm-ai commitment undo {ROOT} --scope personal --if-version {card.version}" in reason


def test_the_card_carries_the_due_in_words():
    """Review finding 1: the due in force, never parsed from the heading."""
    assert _card(_pending()).due_words == "Thu 22 Oct"
    assert _card(_pending(_item(due="next sprint"))).due_words == 'no due date worked out from "next sprint"'
    assert _card(_pending(_item(due=None))).due_words == "no due date said"
