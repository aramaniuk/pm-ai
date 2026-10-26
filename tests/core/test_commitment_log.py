"""Story 15a — `commitments_log.md`'s lines, its reader and its fold.

Render and parse round trips, the field limits, the fold's orders (values by
replacement number, the due, the state), duplicates, conflicts, orphans,
malformed lines (invalid UTF-8 among them), a torn tail, and every cell of
the PM's mark table. Fixtures use `11d`'s names; no real meeting is used.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from pm_ai.core.commitment_log import (
    CommitmentLine,
    CommitmentLog,
    DueSet,
    Head,
    Marked,
    OwnerSet,
    Pending,
    Revised,
    TooLong,
    entry_id,
    fold,
    from_approved,
    parse_ledger,
    render_line,
    revised_from,
)
from pm_ai.core.meeting_records import MeetingRecord
from pm_ai.domain.commitments import (
    MARKABLE,
    Cites,
    Commitment,
    Due,
    LineKind,
    Mark,
    MarkAnswer,
    NotACommitmentLine,
    StateSource,
    can_mark,
)
from pm_ai.domain.identity import Actor, DataScope, ScopeKind, SourceRef
from pm_ai.domain.lifecycle import CommitmentState, ProposalState
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.proposals import Proposal

UTC = timezone.utc
START = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
APPROVED = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
ROOT = "prp_4a0a339b0c6d_mtg_a_ai_1e97a541b3ff"
PERSONAL = DataScope(ScopeKind.PERSONAL)


def _meeting(**changes) -> MeetingRecord:
    meeting = Meeting(
        meeting_id="mtg_a",
        title="Example Weekly Sync-up",
        start=START,
        duration_minutes=30,
        attendees=(Actor("email:alex@example.com"),),
        scope=PERSONAL,
    )
    return MeetingRecord(meeting=replace(meeting, **changes))


def _item(proposal_id: str = ROOT, *, what="send the cache TTL numbers", owner="Sam Rivera",
          due="by Thursday", at: datetime = APPROVED) -> Proposal:
    return Proposal(
        proposal_id=proposal_id,
        type="action_item",
        summary=what[:80],
        payload={"what": what, "owner": owner, "due": due},
        target=None,
        cites=SourceRef.parse("meeting:mtg_a"),
        created_at=START,
        state=ProposalState.APPROVED,
        version=2,
        ttl=timedelta(days=14),
        staged_at=START,
        scope="personal",
        changed_at=at,
        changed_via="terminal",
    )


def _pending(proposal: Proposal | None = None, *, record=None, written="approval", at=APPROVED) -> CommitmentLine:
    commitment = from_approved(proposal or _item(), record=record if record is not None else _meeting(), zone=None)
    return render_line(Pending(commitment, via="terminal", written=written, at=at))


def _read(*lines: CommitmentLine | str):
    text = "".join((line.text if isinstance(line, CommitmentLine) else line) + "\n" for line in lines)
    return fold(parse_ledger(text.encode("utf-8")))


def _mark(state: str, minutes: int, root: str = ROOT) -> CommitmentLine:
    at = APPROVED + timedelta(minutes=minutes)
    return render_line(Marked(root, Mark(state), occurred=at, via="terminal", at=at))


def _verdict(state: str, minutes: int, root: str = ROOT) -> str:
    at = (APPROVED + timedelta(minutes=minutes)).isoformat()
    entry = entry_id((root, "verdict", state, at))
    return f"- entry={entry} commitment={root} kind=verdict state={state} evidence=gitlab:alpha:commit:9f2a1c occurred={at} at={at}"


# ── Rendering ────────────────────────────────────────────────────────────────


def test_a_pending_line_reads_back_as_written():
    line = _pending()
    assert line.text.startswith(f"- entry={line.entry} commitment={ROOT} kind=pending proposal={ROOT} type=action_item meeting=mtg_a")
    assert line.text.endswith(f"occurred={APPROVED.isoformat()} via=terminal written=approval at={APPROVED.isoformat()}")
    assert ' day=2026-10-19 what="send the cache TTL numbers" owner="Sam Rivera" due_said="by Thursday" due=2026-10-22 ' in line.text
    held = _read(line).commitments[ROOT]
    assert held.what == "send the cache TTL numbers" and held.owner == "Sam Rivera" and held.speaker is None
    assert held.due == Due("by Thursday", "2026-10-22") and held.day == date(2026, 10, 19)
    assert held.title == "Example Weekly Sync-up" and held.start == START
    assert held.state is CommitmentState.PENDING and held.state_source is StateSource.NONE
    assert held.cites == Cites("meeting:mtg_a", "Sam Rivera")


def test_the_entry_id_is_worked_out_from_the_lines_identity():
    first = _pending()
    again = _pending(written="catch_up", at=APPROVED + timedelta(days=3))
    assert first.entry == again.entry == entry_id((ROOT, "pending"))
    assert first.text != again.text
    assert first.entry.startswith("cml_") and len(first.entry) == 16


def test_hostile_text_is_one_line_and_reads_back_equal():
    """Matrix "Hostile text"."""
    hostile = 'send the "TTL" numbers\nthen - entry=cml_000000000000 kind=pending'
    line = _pending(_item(what=hostile))
    assert "\n" not in line.text and '"send the \\"TTL\\" numbers\\nthen - entry=' in line.text
    parsed = parse_ledger((line.text + "\n").encode())
    assert len(parsed.lines) == 1 and parsed.malformed == 0
    assert _read(line).commitments[ROOT].what == hostile


@pytest.mark.parametrize(
    "changes, field_name",
    [
        ({"what": "x" * 201}, "what"),
        ({"owner": "o" * 81}, "owner"),
        ({"due": "d" * 41}, "due as said"),
    ],
)
def test_a_value_over_its_limit_is_too_long_and_not_cut(changes, field_name):
    """Matrix "Field over its limit"."""
    with pytest.raises(TooLong) as refused:
        _pending(_item(**changes))
    assert refused.value.field_name == field_name


def test_a_long_title_is_cut_at_a_word_and_marked():
    """Matrix "Long title"."""
    title = " ".join(["Example Weekly Sync-up"] * 30)
    assert len(title) > 500
    line = _pending(record=_meeting(title=title))
    held = _read(line).commitments[ROOT]
    assert held.title is not None and len(held.title) <= 200 and held.title_cut
    assert title.startswith(held.title) and title[len(held.title)] == " "
    assert "title_cut=yes" in line.text


def test_a_record_that_cannot_be_read_gives_no_title_start_or_day():
    """Matrix "Record unreadable": the line is still written."""
    commitment = from_approved(_item(), record=None, zone=None)
    line = render_line(Pending(commitment, via="terminal", written="approval", at=APPROVED))
    assert " title=" not in line.text and " start=" not in line.text and " day=" not in line.text
    assert " due=unresolved " in line.text


def test_an_estimated_day_is_stored_and_marks_the_due():
    """Matrix "Estimated day"."""
    line = _pending(record=_meeting(start_estimated_from="header", manual_only=True))
    assert " day=2026-10-19 day_from=header " in line.text
    assert _read(line).commitments[ROOT].due.estimated


def test_a_spoken_promise_records_its_speaker_and_no_owner():
    promise = replace(
        _item(),
        type="implicit_commitment",
        payload={
            "what": {"raw": "send the numbers", "for_model": "send the numbers"},
            "when": {"raw": "Friday", "for_model": "Friday"},
            "speaker": {"raw": "Sam Rivera", "for_model": "Sam Rivera"},
        },
    )
    line = _pending(promise)
    assert ' speaker="Sam Rivera" ' in line.text and " owner=" not in line.text
    held = _read(line).commitments[ROOT]
    assert held.speaker == "Sam Rivera" and held.owner is None and held.due.value == "2026-10-23"
    assert held.cites == Cites("meeting:mtg_a", "Sam Rivera")


def test_a_long_promise_is_cut_at_a_word_and_marked_never_refused():
    """Matrix "Long promise" (Andrei, 2026-10-24): never `too_long`."""
    what = " ".join(["send the cache TTL numbers"] * 12)
    assert len(what) > 300
    promise = replace(
        _item(),
        type="implicit_commitment",
        payload={
            "what": {"raw": what, "for_model": what},
            "when": {"raw": "Friday", "for_model": "Friday"},
            "speaker": {"raw": "Sam Rivera", "for_model": "Sam Rivera"},
        },
    )
    line = _pending(promise)
    assert " what_cut=yes " in line.text
    held = _read(line).commitments[ROOT]
    assert len(held.what) <= 200 and held.what_cut
    assert what.startswith(held.what) and what[len(held.what)] == " "
    assert promise.payload["what"]["for_model"] == what, "the proposal body keeps the full text"


def test_a_promise_within_its_limit_is_not_marked_cut():
    promise = replace(
        _item(),
        type="implicit_commitment",
        payload={
            "what": {"raw": "w" * 200, "for_model": "w" * 200},
            "when": {"raw": "Friday", "for_model": "Friday"},
        },
    )
    line = _pending(promise)
    assert "what_cut" not in line.text and not _read(line).commitments[ROOT].what_cut


def test_only_render_line_builds_a_line():
    with pytest.raises(NotACommitmentLine):
        CommitmentLine("cml_000000000000", ROOT, LineKind.PENDING, "- entry=…")
    with pytest.raises(NotACommitmentLine):
        render_line(object())  # type: ignore[arg-type]


def test_every_kind_round_trips():
    at = APPROVED + timedelta(hours=1)
    lines = [
        _pending(),
        render_line(Revised(ROOT, f"{ROOT}_e1", ROOT, "send the TTL numbers", None, Due("Friday", "2026-10-23"),
                            occurred=at, via="prompt", written="approval", at=at)),
        render_line(Marked(ROOT, Mark.ALTERED, occurred=at + timedelta(minutes=1), via="terminal", at=at,
                           handle="graph:00000000-0000-0000-0000-000000000001", note="moved to next week")),
        render_line(DueSet(ROOT, date(2026, 10, 30), occurred=at + timedelta(minutes=2), via="terminal", at=at)),
        render_line(OwnerSet(ROOT, "Alex Morgan", occurred=at + timedelta(minutes=3), via="terminal", at=at)),
    ]
    ledger = _read(*lines)
    assert ledger.malformed == 0 and ledger.orphans == 0
    held = ledger.commitments[ROOT]
    assert held.proposal_id == f"{ROOT}_e1" and held.what == "send the TTL numbers"
    assert held.owner == "Alex Morgan" and held.due == Due(None, "2026-10-30")
    assert held.state is CommitmentState.ALTERED and held.state_source is StateSource.PM


# ── Reading ──────────────────────────────────────────────────────────────────


def test_a_torn_last_line_is_no_record():
    """Matrix "Torn last line"."""
    whole = _pending().text + "\n"
    torn = whole + "- entry=cml_0123456789ab commitment=prp_x kind=pen"
    parsed = parse_ledger(torn.encode())
    assert len(parsed.lines) == 1 and parsed.malformed == 0 and parsed.size == len(torn.encode())


def test_a_duplicate_line_is_ignored_and_counted():
    """Matrix "Duplicate line"."""
    line = _pending()
    ledger = _read(line, line)
    assert ledger.duplicates == 1 and ledger.conflicts == 0 and len(ledger.commitments) == 1


def test_a_conflicting_line_loses_to_the_first_and_is_counted():
    """Matrix "Conflicting line"."""
    first = _pending()
    other = first.text.replace('what="send the cache TTL numbers"', 'what="something else"')
    ledger = _read(first, other)
    assert ledger.conflicts == 1 and ledger.commitments[ROOT].what == "send the cache TTL numbers"
    ledger = _read(other, first)
    assert ledger.conflicts == 1 and ledger.commitments[ROOT].what == "something else"


@pytest.mark.parametrize(
    "bad",
    [
        "- entry=cml_0123456789ab commitment=prp_y kind=promised occurred=2026-10-19T12:00:00+00:00 at=2026-10-19T12:00:00+00:00",
        "- entry=cml_0123456789ab commitment=prp_y kind=marked state=done by=pm occurred=2026-10-19T12:00:00+00:00 via=terminal at=2026-10-19T12:00:00+00:00",
        "- entry=cml_0123456789ab commitment=prp_y kind=verdict state=altered occurred=2026-10-19T12:00:00+00:00 at=2026-10-19T12:00:00+00:00",
        "- entry=nope commitment=prp_y kind=pending",
        "just a note the PM typed",
        '- entry=cml_0123456789ab commitment=prp_y kind=pending what="unclosed',
    ],
)
def test_a_malformed_line_is_skipped_and_counted(bad):
    """Matrix "Malformed line": an unknown kind, a bad state, or one that does not parse."""
    ledger = _read(_pending(), bad)
    assert ledger.malformed == 1 and ROOT in ledger.commitments


def test_invalid_utf8_is_one_malformed_line_and_the_rest_is_read():
    good = (_pending().text + "\n").encode()
    data = good + b"- entry=cml_0123456789ab commitment=prp_z kind=pending what=\xff\xfe\n" + good
    parsed = parse_ledger(data)
    assert parsed.malformed == 1 and "prp_z" in parsed.malformed_roots
    assert fold(parsed).commitments[ROOT].what == "send the cache TTL numbers"


def test_a_malformed_line_names_its_root():
    ledger = _read(f"- entry=cml_0123456789ab commitment={ROOT} kind=pending what=x")
    assert ROOT in ledger.malformed_roots and ROOT not in ledger.commitments


def test_blank_lines_and_headings_are_not_records():
    ledger = _read("# Commitments", "", _pending())
    assert ledger.malformed == 0 and ROOT in ledger.commitments


def test_an_orphan_is_ignored_and_counted():
    """Matrix "Orphan": a `revised` or `marked` line with no `pending`."""
    at = APPROVED
    revised = render_line(Revised("prp_other", "prp_other_e1", "prp_other", "x", None, None,
                                  occurred=at, via="terminal", written="approval", at=at))
    ledger = _read(revised, _mark("fulfilled", 1, root="prp_other"))
    assert ledger.orphans == 2 and not ledger.commitments


# ── The fold's orders ────────────────────────────────────────────────────────


def _revised(n: int, minutes: int, *, what: str, due: Due | None = None, replaces: str = ROOT) -> CommitmentLine:
    at = APPROVED + timedelta(minutes=minutes)
    return render_line(Revised(ROOT, f"{ROOT}_e{n}", replaces, what, "Sam Rivera", due,
                               occurred=at, via="terminal", written="approval", at=at))


def test_file_order_never_changes_the_fold():
    """Matrix "Fold order" (AD-35)."""
    lines = [
        _pending(),
        _revised(1, 5, what="first edit"),
        _mark("broken", 10),
        _mark("fulfilled", 20),
    ]
    forward = _read(*lines)
    backward = _read(*reversed(lines))
    assert forward.commitments == backward.commitments
    assert forward.commitments[ROOT].state is CommitmentState.FULFILLED


def test_values_come_from_the_highest_replacement_number():
    """Matrix "Value order": `_e2` occurring before `_e1` (a clock step)."""
    ledger = _read(_pending(), _revised(2, 3, what="second edit"), _revised(1, 9, what="first edit"))
    held = ledger.commitments[ROOT]
    assert held.proposal_id == f"{ROOT}_e2" and held.what == "second edit"
    assert ledger.heads[ROOT] == Head(f"{ROOT}_e2", 2, "by Thursday")


def test_the_pms_due_stands_over_a_revision_without_due_fields():
    """Matrix "Due kept"."""
    due_set = render_line(DueSet(ROOT, date(2026, 11, 2), occurred=APPROVED + timedelta(minutes=1),
                                 via="terminal", at=APPROVED + timedelta(minutes=1)))
    ledger = _read(_pending(), due_set, _revised(1, 5, what="send the TTL numbers"))
    assert ledger.commitments[ROOT].due == Due(None, "2026-11-02")


def test_a_revision_that_changes_the_due_takes_over():
    ledger = _read(_pending(), _revised(1, 5, what="x", due=Due("Friday", "2026-10-23")))
    assert ledger.commitments[ROOT].due == Due("Friday", "2026-10-23")
    assert ledger.heads[ROOT].due_said == "Friday"


def test_revised_from_carries_the_due_only_when_the_words_changed():
    held = _read(_pending()).commitments[ROOT]
    head = Head(ROOT, 0, "by Thursday")
    same = revised_from(_item(f"{ROOT}_e1", what="send the TTL numbers"), held, head,
                        via="terminal", written="approval", at=APPROVED)
    assert same.due is None and same.replaces == ROOT
    moved = revised_from(_item(f"{ROOT}_e1", due="Friday"), held, head,
                         via="terminal", written="approval", at=APPROVED)
    # Resolved against the stored day, Mon 19 Oct.
    assert moved.due == Due("Friday", "2026-10-23")


def test_an_owner_set_after_the_value_line_stands():
    owner = render_line(OwnerSet(ROOT, None, occurred=APPROVED + timedelta(minutes=2),
                                 via="terminal", at=APPROVED + timedelta(minutes=2)))
    assert _read(_pending(), owner).commitments[ROOT].owner is None


# ── The PM's marks ───────────────────────────────────────────────────────────


def test_a_hand_written_mark_applies():
    """Matrix "Hand-written mark"."""
    held = _read(_pending(), _mark("altered", 5)).commitments[ROOT]
    assert held.state is CommitmentState.ALTERED and held.state_source is StateSource.PM


def test_a_mark_not_allowed_is_refused_and_counted():
    """Matrix "Mark not allowed": `altered` after a PM-marked `fulfilled`."""
    ledger = _read(_pending(), _mark("fulfilled", 5), _mark("altered", 10))
    assert ledger.refused == 1
    assert ledger.commitments[ROOT].state is CommitmentState.FULFILLED


def test_withdrawn_is_its_own_state_never_broken():
    """Matrix "Withdrawn"."""
    held = _read(_pending(), _mark("withdrawn", 5)).commitments[ROOT]
    assert held.state is CommitmentState.WITHDRAWN and held.state is not CommitmentState.BROKEN
    assert CommitmentState.WITHDRAWN.is_terminal and CommitmentState.WITHDRAWN.is_verdict


def test_a_mark_outranks_a_later_verdict():
    """Matrix "Mark outranks a verdict"."""
    held = _read(_pending(), _mark("broken", 5), _verdict("fulfilled", 10)).commitments[ROOT]
    assert held.state is CommitmentState.BROKEN and held.state_source is StateSource.PM


def test_a_verdict_applies_while_no_mark_has():
    held = _read(_pending(), _verdict("unknown", 5)).commitments[ROOT]
    assert held.state is CommitmentState.UNKNOWN and held.state_source is StateSource.TELEMETRY
    # The PM confirms telemetry's verdict with his own mark.
    held = _read(_pending(), _verdict("broken", 5), _mark("broken", 9)).commitments[ROOT]
    assert held.state is CommitmentState.BROKEN and held.state_source is StateSource.PM


def test_a_verdict_applies_after_an_altered_mark():
    """Matrix "Verdict after altered" (Andrei, 2026-10-24): still tracked."""
    held = _read(_pending(), _mark("altered", 5), _verdict("fulfilled", 10)).commitments[ROOT]
    assert held.state is CommitmentState.FULFILLED and held.state_source is StateSource.TELEMETRY
    # Telemetry keeps judging it: a later verdict replaces an earlier one.
    held = _read(
        _pending(), _mark("altered", 5), _verdict("unknown", 10), _verdict("broken", 15)
    ).commitments[ROOT]
    assert held.state is CommitmentState.BROKEN and held.state_source is StateSource.TELEMETRY
    # A verdict that comes before the altered mark is replaced by it.
    held = _read(_pending(), _verdict("broken", 5), _mark("altered", 10)).commitments[ROOT]
    assert held.state is CommitmentState.ALTERED and held.state_source is StateSource.PM


@pytest.mark.parametrize("mark", ["fulfilled", "broken", "withdrawn"])
def test_every_other_mark_still_blocks_a_later_verdict(mark):
    for verdict in ("fulfilled", "broken", "unknown", "error"):
        held = _read(_pending(), _mark(mark, 5), _verdict(verdict, 10)).commitments[ROOT]
        assert held.state is CommitmentState(mark) and held.state_source is StateSource.PM
    # Marked again altered after one of them, telemetry judges it again.
    if mark != "fulfilled":  # the table refuses `altered` after a PM `fulfilled`
        held = _read(_pending(), _mark(mark, 5), _mark("altered", 8), _verdict("fulfilled", 10)).commitments[ROOT]
        assert held.state is CommitmentState.FULFILLED and held.state_source is StateSource.TELEMETRY


_Y, _A, _R = MarkAnswer.YES, MarkAnswer.ALREADY, MarkAnswer.REFUSED
TABLE = {
    (CommitmentState.PENDING, StateSource.NONE): (_Y, _Y, _Y, _Y),
    (CommitmentState.ALTERED, StateSource.PM): (_Y, _Y, _Y, _Y),
    (CommitmentState.UNKNOWN, StateSource.TELEMETRY): (_Y, _Y, _Y, _Y),
    (CommitmentState.ERROR, StateSource.TELEMETRY): (_Y, _Y, _Y, _Y),
    (CommitmentState.FULFILLED, StateSource.TELEMETRY): (_Y, _Y, _Y, _Y),
    (CommitmentState.FULFILLED, StateSource.PM): (_A, _R, _Y, _R),
    (CommitmentState.BROKEN, StateSource.TELEMETRY): (_Y, _Y, _Y, _Y),
    (CommitmentState.BROKEN, StateSource.PM): (_Y, _Y, _A, _Y),
    (CommitmentState.WITHDRAWN, StateSource.PM): (_Y, _Y, _Y, _A),
}
MARKS = (Mark.FULFILLED, Mark.ALTERED, Mark.BROKEN, Mark.WITHDRAWN)


@pytest.mark.parametrize("state, source", list(TABLE))
def test_every_cell_of_the_mark_table(state, source):
    """The table as Andrei decided it (2026-10-24), cell by cell."""
    for mark, expected in zip(MARKS, TABLE[(state, source)], strict=True):
        assert MARKABLE[(state, source)][mark] is expected, (state, source, mark)
        assert can_mark(state, source, mark) is (expected is MarkAnswer.YES)
    assert set(MARKABLE) == set(TABLE)


def _into(state: CommitmentState, source: StateSource) -> list[CommitmentLine | str]:
    """Lines that put the commitment in `state` from `source`."""
    lines: list[CommitmentLine | str] = [_pending()]
    if source is StateSource.TELEMETRY:
        lines.append(_verdict(state.value, 5))
    elif source is StateSource.PM:
        if state is CommitmentState.ALTERED:
            lines.append(_mark("altered", 5))
        else:
            lines.append(_mark(state.value, 5))
    return lines


@pytest.mark.parametrize("state, source", list(TABLE))
@pytest.mark.parametrize("mark", MARKS)
def test_every_cell_folds_as_the_table_says(state, source, mark):
    lines = _into(state, source)
    before = _read(*lines).commitments[ROOT]
    assert (before.state, before.state_source) == (state, source)
    ledger = _read(*lines, _mark(mark.value, 30))
    held = ledger.commitments[ROOT]
    if MARKABLE[(state, source)][mark] is MarkAnswer.YES:
        assert (held.state, held.state_source) == (mark.state, StateSource.PM) and ledger.refused == 0
    else:
        assert (held.state, held.state_source) == (state, source) and ledger.refused == 1


# ── The accessor ─────────────────────────────────────────────────────────────


class _Store:
    def __init__(self) -> None:
        self.data = b""
        self.calls: list[tuple] = []

    def read_commitments(self, *, scope):
        return self.data

    def append_commitments(self, lines, *, scope, after):
        self.calls.append((tuple(lines), scope, after))
        self.data += "".join(line + "\n" for line in lines).encode()
        return len(self.data)


def test_the_accessor_appends_lines_and_reads_them_folded():
    store = _Store()
    log = CommitmentLog(store)
    line = _pending()
    log.append([line], scope=PERSONAL, after=0)
    assert store.calls == [((line.text,), PERSONAL, 0)]
    assert ROOT in log.read(PERSONAL).commitments
    with pytest.raises(NotACommitmentLine):
        log.append([line.text], scope=PERSONAL, after=0)  # type: ignore[list-item]
    assert len(store.calls) == 1


def test_a_commitment_needs_no_transcript():
    """AD-33: the commitment holds every value a reader needs."""
    held = _read(_pending()).commitments[ROOT]
    assert isinstance(held, Commitment)
    assert held.meeting_id == "mtg_a" and held.approved_at == APPROVED


def test_a_leading_byte_order_mark_is_not_part_of_the_first_line():
    data = b"\xef\xbb\xbf" + (_pending().text + "\n").encode()
    parsed = parse_ledger(data)
    assert parsed.malformed == 0 and not parsed.malformed_roots and parsed.size == len(data)
    assert ROOT in fold(parsed).commitments


@pytest.mark.parametrize("odd", ["2026-W43-1", "20261022", "2026-296"])
def test_a_date_on_a_line_is_year_month_day_only(odd):
    """`date.fromisoformat` takes week and ordinal dates too; a line does not."""
    line = _pending().text
    for field_name in ("due=2026-10-22", "day=2026-10-19"):
        key = field_name.split("=")[0]
        ledger = _read(line.replace(field_name, f"{key}={odd}"))
        assert ledger.malformed == 1 and ROOT not in ledger.commitments
    due_set = render_line(DueSet(ROOT, date(2026, 11, 2), occurred=APPROVED, via="terminal", at=APPROVED))
    ledger = _read(line, due_set.text.replace("due=2026-11-02", f"due={odd}"))
    assert ledger.malformed == 1


def test_a_whitespace_only_edited_due_is_nothing_said():
    held = _read(_pending(_item(due=None))).commitments[ROOT]
    revised = revised_from(_item(f"{ROOT}_e1", due="   "), held, Head(ROOT, 0, None),
                           via="terminal", written="approval", at=APPROVED)
    assert revised.due is None


# ── Story 15c: what the fold adds, the owner rule, and undo ──────────────────

from pm_ai.core.commitment_log import Undone  # noqa: E402
from pm_ai.domain.commitments import UNDO_WINDOW  # noqa: E402


def _owner_set(name: str | None, minutes: int) -> CommitmentLine:
    at = APPROVED + timedelta(minutes=minutes)
    return render_line(OwnerSet(ROOT, name, occurred=at, via="terminal", at=at))


def _revised_owner(n: int, minutes: int, owner: str | None, *, what: str = "send the cache TTL numbers") -> CommitmentLine:
    at = APPROVED + timedelta(minutes=minutes)
    replaces = ROOT if n == 1 else f"{ROOT}_e{n - 1}"
    return render_line(Revised(ROOT, f"{ROOT}_e{n}", replaces, what, owner, None,
                               occurred=at, via="terminal", written="approval", at=at))


def _undone(mark: CommitmentLine, minutes: float, root: str = ROOT) -> CommitmentLine:
    at = APPROVED + timedelta(minutes=minutes)
    return render_line(Undone(root, mark.entry, occurred=at, via="terminal", at=at))


def test_the_version_is_the_newest_line_in_file_order():
    pending, done = _pending(), _mark("fulfilled", 5)
    ledger = _read(pending, done)
    assert ledger.details[ROOT].version == done.entry
    assert ledger.details[ROOT].newest.kind is LineKind.MARKED
    assert _read(pending).details[ROOT].version == pending.entry


def test_another_commitments_line_leaves_the_version_unchanged():
    """Matrix "Other commitment moved": a fold unit test."""
    other = "prp_4a0a339b0c6d_mtg_a_ai_0000000000aa"
    pending = _pending()
    other_pending = _pending(_item(other))
    before = _read(pending, other_pending).details[ROOT].version
    after = _read(pending, other_pending, _mark("broken", 9, root=other)).details[ROOT].version
    assert before == after == pending.entry


def test_details_carry_the_mark_in_force_and_the_sources():
    at = APPROVED + timedelta(minutes=3)
    marked = render_line(Marked(ROOT, Mark.ALTERED, occurred=at, via="prompt", at=at, note="scope cut"))
    due_set = render_line(DueSet(ROOT, date(2026, 11, 2), occurred=at + timedelta(minutes=1),
                                 via="terminal", at=at))
    ledger = _read(_pending(), marked, due_set, _owner_set("Alex Morgan", 6))
    details = ledger.details[ROOT]
    assert details.mark is not None and details.mark.mark is Mark.ALTERED
    assert details.mark.note == "scope cut" and details.mark.via == "prompt"
    assert details.due_by_pm and details.owner_by_pm
    assert details.changed_at == APPROVED + timedelta(minutes=6)
    # The due as said is kept beside the PM's date.
    assert details.due_said == "by Thursday"
    assert ledger.commitments[ROOT].due == Due(None, "2026-11-02")


def test_an_owner_the_pm_set_survives_an_edit_of_the_wording():
    """Matrix "Owner kept after an edit": the fold fix."""
    ledger = _read(_pending(), _owner_set("Alex Morgan", 2), _revised_owner(1, 5, "Sam Rivera", what="new words"))
    assert ledger.commitments[ROOT].owner == "Alex Morgan"
    assert ledger.commitments[ROOT].what == "new words"
    assert ledger.details[ROOT].owner_by_pm


def test_an_edit_of_the_owner_after_the_pms_owner_gives_the_edits():
    """Matrix "Owner changed by an edit"."""
    ledger = _read(
        _pending(_item(owner=None)), _owner_set("Alex Morgan", 2),
        _revised_owner(1, 5, None, what="new words"),
        _revised_owner(2, 7, "Sam Rivera", what="new words"),
    )
    assert ledger.commitments[ROOT].owner == "Sam Rivera"
    assert not ledger.details[ROOT].owner_by_pm


def test_an_owner_edit_caught_up_later_keeps_its_earlier_occurred():
    """An `--owner` edit made before `commitment owner`, its line written only afterwards."""
    lines = [_pending(_item(owner=None)), _owner_set("Alex Morgan", 10), _revised_owner(1, 5, "Sam Rivera")]
    ledger = _read(*lines)
    assert ledger.commitments[ROOT].owner == "Alex Morgan"
    # A file-order swap gives the same owner (AD-35).
    assert _read(*reversed(lines)).commitments[ROOT].owner == "Alex Morgan"


def test_a_revised_line_that_clears_the_owner_counts_as_a_change():
    ledger = _read(_pending(), _owner_set("Alex Morgan", 2), _revised_owner(1, 5, None))
    assert ledger.commitments[ROOT].owner is None


def test_an_undone_line_reads_back_as_written():
    done = _mark("fulfilled", 5)
    line = _undone(done, 6)
    (parsed,) = [each for each in parse_ledger((line.text + "\n").encode()).lines]
    assert parsed.kind is LineKind.UNDONE and parsed.fields["undone_entry"] == done.entry
    assert line.entry == entry_id((ROOT, "undone", done.entry))
    assert "kind=undone" in line.text and "by=pm" in line.text


@pytest.mark.parametrize(
    "bad",
    [
        "undone_entry=cml_nothex000000",
        "by=telemetry",
    ],
)
def test_an_undone_line_with_a_bad_field_is_malformed(bad):
    done = _mark("fulfilled", 5)
    text = _undone(done, 6).text
    key = bad.split("=")[0]
    import re as _re

    text = _re.sub(rf"{key}=\S+", bad, text)
    assert _read(_pending(), done, text).malformed == 1


def test_undo_inside_the_window_returns_the_state_before():
    """Matrix "Undo inside the window": back to `PENDING`."""
    done = _mark("fulfilled", 0)
    ledger = _read(_pending(), done, _undone(done, 239))
    assert ledger.commitments[ROOT].state is CommitmentState.PENDING
    assert ledger.details[ROOT].mark is None and ledger.details[ROOT].undone == done.entry
    assert ledger.refused == 0


def test_undo_back_to_altered():
    """Matrix "Undo back to altered"."""
    altered, done = _mark("altered", 1), _mark("fulfilled", 2)
    ledger = _read(_pending(), altered, done, _undone(done, 3))
    assert ledger.commitments[ROOT].state is CommitmentState.ALTERED
    assert ledger.details[ROOT].mark is not None and ledger.details[ROOT].mark.mark is Mark.ALTERED


def test_undo_after_the_window_is_refused_and_the_mark_stands():
    """Matrix "Undo line read back": more than 4 h after the mark."""
    done = _mark("fulfilled", 0)
    late = UNDO_WINDOW.total_seconds() / 60 + 1
    ledger = _read(_pending(), done, _undone(done, late))
    assert ledger.commitments[ROOT].state is CommitmentState.FULFILLED and ledger.refused == 1


def test_undo_with_a_line_between_is_refused_and_the_mark_stands():
    """Matrix "Undo line read back": a line between the mark and the undo."""
    done = _mark("fulfilled", 0)
    lines = [_pending(), done, _owner_set("Alex Morgan", 1), _undone(done, 2)]
    ledger = _read(*lines)
    assert ledger.commitments[ROOT].state is CommitmentState.FULFILLED and ledger.refused == 1
    swapped = _read(*reversed(lines))
    assert swapped.commitments == ledger.commitments and swapped.refused == 1


def test_undo_of_a_mark_other_than_done_is_refused():
    broken = _mark("broken", 0)
    ledger = _read(_pending(), broken, _undone(broken, 1))
    assert ledger.commitments[ROOT].state is CommitmentState.BROKEN and ledger.refused == 1


def test_undo_folds_the_same_in_any_file_order():
    done = _mark("fulfilled", 0)
    lines = [_pending(), done, _undone(done, 1)]
    assert _read(*lines).commitments == _read(*reversed(lines)).commitments
