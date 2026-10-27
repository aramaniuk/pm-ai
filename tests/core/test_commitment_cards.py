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
        # Story 15d: the reason is prose; the reopen command is its own field.
        assert "pm-ai commitment altered" not in found["actions"][name]["reason"]
        reopen = found["actions"][name]["reopen"]
        assert reopen["command"][:2] == ["commitment", "altered"]
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


# ── Story 15d: the words the pages show (`page_parts`) ───────────────────────

from pm_ai.core.commitment_cards import (  # noqa: E402
    PLACEHOLDERS,
    PROJECT_PAGE_PAST_DUE,
    TEAM_ROW_PAST_DUE,
    TEAM_ROW_STATES,
    closed_words,
    is_placeholder,
    open_order,
    page_due,
    page_parts,
    placeholder_words,
    proposal_commitment,
    said_due_words,
    team_row_words,
)


def _parts(*lines: CommitmentLine | str, now: datetime = TODAY):
    text = "".join((line.text if isinstance(line, CommitmentLine) else line) + "\n" for line in lines)
    ledger = fold(parse_ledger(text.encode()))
    return page_parts(ledger.commitments[ROOT], ledger.details[ROOT], now=now, zone=UTC)


def _due_set(day: date | None, minutes: int = 2) -> CommitmentLine:
    at = APPROVED + timedelta(minutes=minutes)
    return render_line(DueSet(ROOT, day, occurred=at, via="terminal", at=at))


@pytest.mark.parametrize(
    ("lines", "state"),
    [
        ((), ""),
        ((_mark("altered", 1),), "altered on Mon 19 Oct"),
        ((_verdict("unknown", 1),), "pm-ai could not check by telemetry"),
        ((_verdict("error", 1),), "a check failed by telemetry"),
        ((_mark("fulfilled", 1),), "done on Mon 19 Oct"),
        ((_verdict("fulfilled", 1),), "done on Mon 19 Oct by telemetry"),
        ((_mark("broken", 1),), "broken on Mon 19 Oct"),
    ],
)
def test_block_state_takes_every_value(lines, state):
    assert _parts(_pending(), *lines).block_state == state


@pytest.mark.parametrize(
    ("lines", "row_state"),
    [
        ((), ""),
        ((_mark("altered", 1),), "commitment altered on Mon 19 Oct"),
        ((_mark("fulfilled", 1),), "commitment done on Mon 19 Oct"),
        ((_mark("broken", 1),), "commitment broken on Mon 19 Oct"),
        ((_verdict("fulfilled", 1),), "commitment done on Mon 19 Oct by telemetry"),
        ((_verdict("unknown", 1),), "commitment: pm-ai could not check by telemetry"),
        ((_verdict("error", 1),), "commitment: a check failed by telemetry"),
    ],
)
def test_row_state_takes_every_value(lines, row_state):
    assert _parts(_pending(), *lines).row_state == row_state


def test_block_due_past_due_and_the_quote():
    """Matrix "Block order": the exact earlier date is past due, quoted as said."""
    parts = _parts(_pending())
    assert parts.block_due == "due Thu 22 Oct, past due"
    assert parts.past_due is True and parts.said == "by Thursday" and parts.quote_said is True
    assert parts.row_due == "(Thu 22 Oct), past due"
    assert parts.origin_day == "Mon 19 Oct"


def test_an_estimated_date_is_said_so_and_never_past_due():
    parts = _parts(_pending(record=_record(start_estimated_from="header")))
    assert parts.block_due == "due Thu 22 Oct (meeting day estimated)"
    assert parts.row_due == "(Thu 22 Oct, meeting day estimated)"
    assert parts.past_due is False


def test_a_due_of_today_is_not_past_due():
    """Matrix "Due today"."""
    parts = _parts(_pending(), now=datetime(2026, 10, 22, 9, 0, tzinfo=UTC))
    assert parts.block_due == "due Thu 22 Oct" and parts.past_due is False


def test_the_pms_past_date_is_set_by_you_and_past_due():
    """Matrix "PM's date past"."""
    parts = _parts(_pending(), _due_set(date(2026, 10, 20)))
    assert parts.block_due == "due Tue 20 Oct, set by you, past due"
    assert parts.quote_said is False
    assert parts.row_due == "(set by you: Tue 20 Oct), past due"


def test_the_pms_date_with_nothing_said_stands_alone_on_a_row():
    parts = _parts(_pending(_item(due=None)), _due_set(date(2026, 10, 30)))
    assert parts.block_due == "due Fri 30 Oct, set by you"
    assert parts.row_due == "due set by you: Fri 30 Oct"


def test_a_cleared_due_says_so_and_quotes_nothing():
    """Matrix "Due cleared"."""
    parts = _parts(_pending(), _due_set(None))
    assert parts.block_due == "due cleared by you"
    assert parts.row_due == "due cleared by you"
    assert parts.quote_said is False and parts.past_due is False


def test_unresolved_and_none_dues():
    unresolved = _parts(_pending(_item(due="next sprint")))
    assert unresolved.block_due == 'no due date worked out from "next sprint"'
    assert unresolved.block_due_unsaid == "no due date worked out"
    assert unresolved.quote_said is False and unresolved.row_due == ""
    none = _parts(_pending(_item(due=None)))
    assert none.block_due == "no due date said" and none.row_due == "" and none.said is None


def test_the_project_page_and_team_rows_leave_past_due_off():
    """Andrei, 2026-10-27: no broken or past due on a project page."""
    assert PROJECT_PAGE_PAST_DUE is False and TEAM_ROW_PAST_DUE is False
    assert TEAM_ROW_STATES == frozenset({"done", "altered"})
    parts = _parts(_pending())
    assert page_due(parts, project=True) == "due Thu 22 Oct"
    assert page_due(parts, project=False) == "due Thu 22 Oct, past due"
    text = "".join(line.text + "\n" for line in (_pending(),))
    ledger = fold(parse_ledger(text.encode()))
    assert team_row_words(ledger.commitments[ROOT], ledger.details[ROOT], now=TODAY, zone=UTC) == (
        "(Thu 22 Oct)", ""
    )


@pytest.mark.parametrize(
    ("lines", "row_state"),
    [
        ((_mark("fulfilled", 1),), "commitment done on Mon 19 Oct"),
        ((_mark("altered", 1),), "commitment altered on Mon 19 Oct"),
        ((_mark("broken", 1),), ""),
        ((_verdict("unknown", 1),), ""),
        ((_verdict("error", 1),), ""),
    ],
)
def test_a_team_row_prints_done_and_altered_only(lines, row_state):
    text = "".join((line.text if isinstance(line, CommitmentLine) else line) + "\n" for line in (_pending(), *lines))
    ledger = fold(parse_ledger(text.encode()))
    _, state = team_row_words(ledger.commitments[ROOT], ledger.details[ROOT], now=TODAY, zone=UTC)
    assert state == row_state


def test_said_due_words_keep_todays_row_form():
    assert said_due_words(date(2026, 10, 22), False) == " (2026-10-22)"
    assert said_due_words(date(2026, 10, 22), True) == " (2026-10-22) (meeting day estimated)"


def _open_order_15c(commitment):
    """15c's `list` key, written out as its spec states it."""
    due = commitment.due
    rank = 0 if due.date is not None else (1 if due.value == "unresolved" else 2)
    start = commitment.start
    return (
        rank,
        due.date or date.max,
        0 if start is not None else 1,
        -start.timestamp() if start is not None else 0,
        commitment.commitment_id,
    )


def test_open_order_is_15cs_order():
    """Matrix "Block order": a date first, earliest first; then unresolved; then none."""
    commitments = []
    for n, due in enumerate(("by Thursday", None, "next sprint", "30.10")):
        root = f"prp_{n}"
        proposal = replace(_item(due=due), proposal_id=root)
        commitments.append(fold(parse_ledger((_pending(proposal).text + "\n").encode())).commitments[root])
    ordered = sorted(commitments, key=open_order)
    assert [c.due.value for c in ordered] == ["2026-10-22", "2026-10-30", "unresolved", "none"]
    assert [open_order(c) for c in commitments] == [_open_order_15c(c) for c in commitments]


# ── Story 15d: placeholders a wrapper can recognise ──────────────────────────


def test_the_card_actions_carry_placeholders_and_their_fill():
    """Matrix "Placeholder in JSON"."""
    found = card_json(_card(_pending()))
    altered = found["actions"]["altered"]
    assert altered["command"][6] == "<note>"
    assert altered["fill"] == [{"index": 6, "name": "note", "required": False}]
    due = found["actions"]["due"]
    assert due["command"][3] == "<when>" and due["fill"] == [{"index": 3, "name": "when", "required": True}]
    owner = found["actions"]["owner"]
    assert owner["command"][3] == "<name>" and owner["fill"] == [{"index": 3, "name": "name", "required": True}]
    assert found["actions"]["done"]["fill"] == []
    assert due["reopen"] is None
    for action in found["actions"].values():
        assert "…" not in (action["command"] or [])
        for placeholder in PLACEHOLDERS:
            assert placeholder not in (action["reason"] or "")


def test_a_broken_commitment_offers_the_reopen_command():
    """Matrix "Reopen command"."""
    card = _card(_pending(), _mark("broken", 1))
    found = card_json(card)
    reopen = found["actions"]["due"]["reopen"]
    assert reopen["command"][-2:] == ["--due", "<when>"]
    assert reopen["fill"] == [{"index": len(reopen["command"]) - 1, "name": "when", "required": True}]
    assert found["actions"]["due"]["reason"] == (
        "you marked it broken on Mon 19 Oct; the reopen command reopens it with a due"
    )
    text = card_text(card)
    assert f"            reopen: pm-ai commitment altered {ROOT} --scope personal --due '<when>'" in text


def test_the_text_card_quotes_the_placeholders():
    text = card_text(_card(_pending()))
    assert "--note '<note>'" in text and f"due {ROOT} '<when>'" in text


@pytest.mark.parametrize(
    "value",
    ["<note>", "<when>", "<name>", "<what>", "'<when>'", '"<NOTE>"', "  <Name>  ", "…", "...",
     "fix <what> later", '"..."'],
)
def test_a_placeholder_is_recognised(value):
    assert is_placeholder(value) is True


@pytest.mark.parametrize("value", ["friday", "Alex Morgan", "scope cut to the API only", "a..b", "<notes>"])
def test_a_real_value_is_not_a_placeholder(value):
    assert is_placeholder(value) is False


def test_the_refusal_names_the_value_to_type():
    assert placeholder_words("note") == "type the note itself, not <note>"
    assert placeholder_words("due") == "type when it is due, not <when>"
    assert placeholder_words("owner") == "type the name itself, not <name>"
    assert placeholder_words("what") == "type the wording itself, not <what>"


# ── Story 15d: the closed line and the proposal card's commitment ────────────


def test_closed_words_name_each_source():
    assert closed_words({"done": (1, 1), "broken": (1, 0), "withdrawn": (1, 0)}) == (
        "2 done (1 marked by you, 1 by telemetry), 1 broken (marked by you), 1 withdrawn (marked by you)"
    )
    assert closed_words({"done": (0, 2)}) == "2 done (by telemetry)"
    assert closed_words({"broken": (0, 0)}) == ""


def _ledger(*lines: CommitmentLine | str):
    text = "".join((line.text if isinstance(line, CommitmentLine) else line) + "\n" for line in lines)
    return fold(parse_ledger(text.encode()))


def test_the_proposal_card_shows_a_recorded_commitment():
    """Matrix "Proposal card"."""
    found = proposal_commitment(ROOT, ROOT, "personal", _ledger(_pending(), _mark("fulfilled", 1)), now=TODAY, zone=UTC)
    assert found.line == (
        f"commitment: done on Mon 19 Oct, due Thu 22 Oct: pm-ai commitment show {ROOT} --scope personal"
    )
    assert found.recorded is True and found.state == "fulfilled" and found.state_source == "pm"
    assert found.past_due is False and found.show == ("commitment", "show", ROOT, "--scope", "personal")
    opened = proposal_commitment(ROOT, f"{ROOT}_e1", "personal", _ledger(_pending()), now=TODAY, zone=UTC)
    assert opened.line.startswith("commitment: open, due Thu 22 Oct, past due: ")
    assert opened.past_due is True


def test_the_proposal_card_unrecorded_malformed_and_unread():
    """Matrix "Proposal card, not recorded", "unreadable", "bound"."""
    missing = proposal_commitment(ROOT, f"{ROOT}_e1", "personal", _ledger(), now=TODAY, zone=UTC)
    assert missing.line == (
        f"commitment: not recorded yet: pm-ai proposal approve {ROOT}_e1 --scope personal records it"
    )
    assert missing.recorded is False and missing.past_due is False and missing.repair is not None
    malformed = proposal_commitment(
        ROOT, ROOT, "personal", _ledger(f"- entry=cml_0123456789ab commitment={ROOT} kind=pending what=x"),
        now=TODAY, zone=UTC,
    )
    assert malformed.line == (
        f"commitment: a line of commitments_log.md naming {ROOT} does not parse; fix or remove it"
    )
    assert malformed.state is None and malformed.reason is not None
    unread = proposal_commitment(ROOT, ROOT, "personal", "the ledger could not be read (OSError)", now=TODAY, zone=UTC)
    assert unread.line == "commitment: the ledger could not be read (OSError)"
    assert unread.recorded is None and unread.past_due is None and unread.state is None
