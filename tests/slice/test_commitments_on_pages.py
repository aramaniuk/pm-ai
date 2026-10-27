"""Story 15d — commitments on the pages, the proposal cards and the placeholders.

One test per row of the story's I/O matrix that the slice reaches (the words
are in `tests/core/test_commitment_cards.py`, the renderer's forms in
`tests/core/test_rendering_sections.py`, Team Activity's rows in
`tests/core/test_member_updates.py`), plus the one-read count and the timed
renders.

Every row runs against a temporary home with `alpha` enrolled at a real git
repository (13a's `Machine`); the clock is the test's (Wed 21 Oct 2026, 12:00
UTC) and no test reaches a model. Names are `11d`'s fixture names (`Example
Weekly Sync-up`, `Alex Morgan`, `Sam Rivera`); handles are `example.com`.
Nothing reads the PM's own store.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from test_commitment_commands import _append, _json, _run, _verdict
from test_commitments import ITEMS, _approve_in_store, _ledger_path, _lines, _meeting, _put, _summarised, _text
from test_proposals import ALPHA, NOW, PERSONAL, TITLE, Machine
from pm_ai.app import pipelines
from pm_ai.app.proposals import ProposalDesk
from pm_ai.core.commitment_log import CommitmentLog, Marked, Pending, render_line
from pm_ai.core.member_updates import Candidate, ItemKind, Link, LinkKind, lines_to_append
from pm_ai.core.member_updates import parse_ledger as parse_updates
from pm_ai.domain.commitments import Cites, Commitment, Due, Mark, StateSource
from pm_ai.domain.handles import gitlab_handle
from pm_ai.domain.identity import DataScope, ScopeKind, SourceRef
from pm_ai.domain.lifecycle import CommitmentState, ProposalState
from pm_ai.domain.proposals import Proposal
from pm_ai.domain.work import (
    CONFIDENTIAL_ISSUE_TITLE,
    GitLabPerson,
    Issue,
    Member,
    Section,
    SectionRead,
    WorkChange,
)
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, EXIT_USAGE

UTC = timezone.utc
MONDAY = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
APPROVED = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
GITLAB = "https://gitlab.example.com"
ALEX = GitLabPerson(gitlab_handle(GITLAB, 1), "Alex Morgan", "alex.morgan", False)
SAM = GitLabPerson(gitlab_handle(GITLAB, 2), "Sam Rivera", "sam.rivera", False)


@pytest.fixture
def machine(tmp_path, monkeypatch) -> Machine:
    return Machine(tmp_path, monkeypatch)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _commit(
    machine: Machine,
    root: str,
    *,
    scope: DataScope = PERSONAL,
    what: str = "send the cache TTL numbers",
    owner: str | None = "Sam Rivera",
    speaker: str | None = None,
    promise: bool = False,
    due: Due | None = None,
    meeting: str | None = "mtg_c",
    title: str | None = TITLE,
    start: datetime | None = MONDAY,
    estimated: bool = False,
    approved: datetime = APPROVED,
) -> str:
    """One `pending` line, written through the accessor, as 15a's approval writes it."""
    commitment = Commitment(
        commitment_id=root,
        proposal_id=root,
        type="implicit_commitment" if promise else "action_item",
        meeting_id=meeting,
        title=title if start is not None else None,
        start=start if title is not None else None,
        day=None if start is None else start.date(),
        day_from="header" if estimated else None,
        what=what,
        owner=owner,
        speaker=speaker,
        due=due or Due.nothing(),
        state=CommitmentState.PENDING,
        state_source=StateSource.NONE,
        approved_at=approved,
        cites=Cites(None if meeting is None else f"meeting:{meeting}", speaker if promise else owner),
    )
    line = render_line(Pending(commitment, via="terminal", written="approval", at=approved))
    held = machine.storage.read_commitments(scope=scope)
    CommitmentLog(machine.storage).append([line], scope=scope, after=len(held))
    return root


def _mark(machine: Machine, root: str, mark: Mark, at: datetime, scope: DataScope = PERSONAL) -> None:
    line = render_line(Marked(root, mark, occurred=at, via="terminal", at=at))
    held = machine.storage.read_commitments(scope=scope)
    CommitmentLog(machine.storage).append([line], scope=scope, after=len(held))


def _on(day: int) -> Due:
    return Due("by then", f"2026-10-{day:02d}")


def _block(page: str) -> list[str]:
    """The block's list lines: after its lead-in, up to the blank line that ends it."""
    lines = page.split("\n")
    if "Open commitments, soonest due first:" not in lines:
        return []
    start = lines.index("Open commitments, soonest due first:") + 2
    end = lines.index("", start)
    return lines[start:end]


def _section(page: str, heading: str) -> str:
    found, out = False, []
    for line in page.split("\n"):
        if line.startswith("## "):
            found = line == f"## {heading}"
            continue
        if found:
            out.append(line)
    return "\n".join(out)


def _work(machine: Machine, *issues: Issue) -> None:
    machine.storage.record_project_work(
        ALPHA,
        WorkChange(
            instance="gitlab:alpha",
            started_at=NOW - timedelta(hours=1),
            members=(Member(ALEX, "active"), Member(SAM, "active")),
            issues=issues,
            sections=tuple(SectionRead(section, complete=True) for section in Section),
        ),
    )


def _issue(iid: int, *assignees: GitLabPerson, confidential: bool = False) -> Issue:
    return Issue(
        iid=iid,
        title=CONFIDENTIAL_ISSUE_TITLE if confidential else f"Issue {iid}",
        state="opened",
        assignees=assignees,
        milestone_title=None,
        milestone_due=None,
        labels=(),
        updated_at=NOW - timedelta(days=1),
        closed_at=None,
        web_url=None,
        confidential=confidential,
    )


def _link(machine: Machine, meeting, item: str, what: str, owner: str | None, due: str | None, iid: int) -> None:
    held = parse_updates(machine.storage.read_member_updates(scope=ALPHA))
    lines = lines_to_append(
        held, meeting, links=(Link(Candidate(ItemKind.ACTION, item, what, owner, due), iid, LinkKind.NUMBER),), at=NOW
    )
    size = len(machine.storage.read_member_updates(scope=ALPHA))
    machine.storage.append_member_updates(list(lines), scope=ALPHA, after=size)


# ── The block: order, words, cap ─────────────────────────────────────────────


def test_open_commitments_soonest_first_and_only_an_exact_earlier_date_is_past_due(machine):
    """Acceptance 1; Matrix "Block order", "Due today", "Old meeting"."""
    _commit(machine, "prp_later", due=_on(23), estimated=True, what="book the load test", owner="Alex Morgan")
    _commit(machine, "prp_none", what="collect the feedback")
    _commit(machine, "prp_unresolved", due=Due.unresolved("next sprint"), what="migrate the cache")
    _commit(machine, "prp_past", due=Due("by Tuesday", "2026-10-20"))
    _commit(machine, "prp_today", due=_on(21), what="share the draft")
    _commit(machine, "prp_old", due=_on(30), what="from a month ago", start=datetime(2026, 9, 21, 10, 0, tzinfo=UTC))
    lines = _block(machine.page())
    assert lines[0] == (
        '- **due Tue 20 Oct, past due** Sam Rivera: send the cache TTL numbers ("by Tuesday") '
        "— from Example Weekly Sync-up, Mon 19 Oct"
    )
    assert lines[1].startswith('- **due Wed 21 Oct** Sam Rivera: share the draft ("by then")')
    assert lines[2].startswith("- **due Fri 23 Oct (meeting day estimated)** Alex Morgan: book the load test")
    assert "past due" not in lines[2]
    assert lines[3].startswith("- **due Fri 30 Oct** Sam Rivera: from a month ago")
    assert lines[4].startswith('- **no due date worked out from "next sprint"** Sam Rivera: migrate the cache —')
    assert lines[5].startswith("- **no due date said** Sam Rivera: collect the feedback —")
    assert lines[6] == "- to mark or correct them: `pm-ai commitment review --scope personal`"
    assert sum("past due" in line for line in lines) == 1


def test_the_order_runs_across_every_scope_the_page_reads(machine):
    """Matrix "Cross-scope order", "Next step"."""
    _commit(machine, "prp_a1", scope=ALPHA, due=_on(18), what="alpha one")
    _commit(machine, "prp_a2", scope=ALPHA, due=_on(19), what="alpha two")
    for n in range(10):
        _commit(machine, f"prp_p{n}", due=_on(22 + n % 8), what=f"personal {n}")
    lines = _block(machine.page())
    assert "alpha one" in lines[0] and "(`project:alpha`)" in lines[0]
    assert "alpha two" in lines[1]
    assert "- and 2 more open commitments: `pm-ai commitment list --scope personal`" in lines
    assert (
        "- to mark or correct them: `pm-ai commitment review --scope personal`, "
        "`pm-ai commitment review --scope project:alpha`"
    ) in lines


def test_eleven_open_show_ten_and_count_one(machine):
    """Matrix "Cap, one more"."""
    for n in range(11):
        _commit(machine, f"prp_{n:02d}", due=_on(22 + n % 9), what=f"task {n}")
    lines = _block(machine.page())
    assert len([line for line in lines if line.startswith("- **")]) == 10
    assert "- and 1 more open commitment: `pm-ai commitment list --scope personal`" in lines


def test_promises_and_origins(machine):
    """Matrix "Promise", "Promise, no speaker", "No title", "No meeting", "Altered", "Telemetry open state"."""
    _commit(machine, "prp_promise", promise=True, owner=None, speaker="Sam Rivera", what="share the draft", due=_on(22))
    _commit(machine, "prp_bare", promise=True, owner=None, speaker=None, what="send the notes", due=_on(23))
    _commit(machine, "prp_untitled", title=None, start=None, due=_on(24), what="untitled one")
    _commit(machine, "prp_nowhere", meeting=None, title=None, start=None, due=_on(26), what="nowhere")
    _commit(machine, "prp_altered", due=_on(27), what="altered one")
    _mark(machine, "prp_altered", Mark.ALTERED, datetime(2026, 10, 20, 9, 0, tzinfo=UTC))
    _commit(machine, "prp_telemetry", due=_on(28), what="checked one")
    _append(machine, _verdict("prp_telemetry", "unknown", datetime(2026, 10, 20, 9, 0, tzinfo=UTC)))
    lines = _block(machine.page())
    assert lines[0].startswith("- **due Thu 22 Oct** said by Sam Rivera: share the draft")
    assert lines[1].startswith("- **due Fri 23 Oct** send the notes")
    assert lines[2].endswith("untitled one (\"by then\") — from meeting mtg_c (meeting record unavailable when it was approved)")
    assert lines[3] == '- **due Mon 26 Oct** Sam Rivera: nowhere ("by then")'
    assert " — altered on Tue 20 Oct — " in lines[4]
    assert " — pm-ai could not check by telemetry — " in lines[5]


def test_a_quote_only_when_a_date_was_worked_out(machine):
    """Matrix "Quote only when worked out", "Due cleared", "PM's date past"."""
    _commit(machine, "prp_q", due=Due("by Friday", "2026-10-23"))
    assert '("by Friday")' in _block(machine.page())[0]
    _run(machine, "commitment", "due", "prp_q", "friday")
    line = _block(machine.page())[0]
    assert line.startswith("- **due Fri 23 Oct, set by you** Sam Rivera: send the cache TTL numbers —")
    assert '("by Friday")' not in line
    _run(machine, "commitment", "due", "prp_q", "--clear")
    assert _block(machine.page())[0].startswith("- **due cleared by you** Sam Rivera: send the cache TTL numbers —")
    _run(machine, "commitment", "due", "prp_q", "2026-10-20")
    assert _block(machine.page())[0].startswith("- **due Tue 20 Oct, set by you, past due** ")


def test_none_open_names_the_scopes_read(machine, monkeypatch):
    """Matrix "None open", "None open, one unreadable", "Ledger unreadable, personal page"."""
    assert "No open commitment in personal or project:alpha." in machine.page()
    original = type(machine.storage).read_commitments

    def unreadable(self, *, scope):
        if scope == ALPHA:
            raise OSError(5, "cannot read /Users/someone/secret/commitments_log.md")
        return original(self, scope=scope)

    _commit(machine, "prp_home", due=_on(22), what="the personal one")
    monkeypatch.setattr(type(machine.storage), "read_commitments", unreadable)
    page = machine.page()
    assert "the personal one" in page
    assert "The commitments of `project:alpha` could not be read (OSError), so they are not listed" in page
    assert "/Users/someone" not in page and "secret" not in page
    assert "- to mark or correct them: `pm-ai commitment review --scope personal`" in page


# ── Closed in the window ─────────────────────────────────────────────────────


def test_an_approved_item_marked_done_leaves_its_meeting_and_counts_as_closed(machine):
    """Acceptance 2; Matrix "Meeting line" (a waiting item stays a proposal)."""
    _, ids = _summarised(machine)
    for each in ids[:2]:
        machine.run("proposal", "approve", each)
    _run(machine, "commitment", "done", ids[0])
    page = machine.page()
    lines = page.split("\n")
    assert not any(line.startswith("  - ") and ", approved" in line for line in lines)
    assert any(line.startswith("  - unassigned: draft the release notes (next sprint), to review by ") for line in lines)
    assert (
        "  - 2 commitments from this meeting, 1 open: `pm-ai commitment list --scope personal "
        "--meeting mtg_c --state all`"
    ) in lines
    closed = [line for line in lines if line.startswith("- closed since ")]
    assert len(closed) == 1 and ": 1 done (marked by you): `pm-ai commitment list --scope personal --state all`" in closed[0]
    assert "book the review room" in "\n".join(_block(page))


def test_the_closed_line_counts_each_source_inside_the_window_only(machine):
    """Matrix "Closed line", "Closed before the window", "Clock stepped back", "Undone mark"."""
    machine.storage.record_render(PERSONAL, at=NOW - timedelta(hours=2))
    inside, before = NOW - timedelta(hours=1), NOW - timedelta(hours=3)
    for root in ("prp_done", "prp_tel", "prp_broken", "prp_withdrawn", "prp_early", "prp_twice", "prp_ahead", "prp_undone"):
        _commit(machine, root, due=_on(22), what=root)
    _mark(machine, "prp_done", Mark.FULFILLED, inside)
    _append(machine, _verdict("prp_tel", "fulfilled", inside))
    _mark(machine, "prp_broken", Mark.BROKEN, inside)
    _mark(machine, "prp_withdrawn", Mark.WITHDRAWN, inside)
    _mark(machine, "prp_early", Mark.FULFILLED, before)
    _append(machine, _verdict("prp_twice", "fulfilled", before))
    _append(machine, _verdict("prp_twice", "fulfilled", inside))
    _mark(machine, "prp_ahead", Mark.FULFILLED, NOW + timedelta(hours=1))
    _run(machine, "commitment", "done", "prp_undone")
    _run(machine, "commitment", "undo", "prp_undone")
    page = machine.page()
    closed = next(line for line in page.split("\n") if line.startswith("- closed since "))
    assert closed == (
        "- closed since 2026-10-21 10:00: 3 done (2 marked by you, 1 by telemetry), 1 broken "
        "(marked by you), 1 withdrawn (marked by you): `pm-ai commitment list --scope personal --state all`"
    )
    assert any(line.startswith("- **due Thu 22 Oct** Sam Rivera: prp_undone ") for line in _block(page))


def test_a_project_page_shows_no_broken_count_and_no_past_due(machine):
    """Matrix "Project block, past due", "Project closed line" (Andrei, 2026-10-27)."""
    _commit(machine, "prp_late", scope=ALPHA, due=Due("by Thursday", "2026-10-15"), owner="Alex Morgan", what="send the numbers")
    _commit(machine, "prp_done", scope=ALPHA, due=_on(22))
    _commit(machine, "prp_broken", scope=ALPHA, due=_on(22))
    _mark(machine, "prp_done", Mark.FULFILLED, NOW - timedelta(hours=1), scope=ALPHA)
    _mark(machine, "prp_broken", Mark.BROKEN, NOW - timedelta(hours=1), scope=ALPHA)
    _work(machine, _issue(142, ALEX))
    project = machine.page(ALPHA)
    lines = _block(project)
    assert lines[0].startswith('- **due Thu 15 Oct** Alex Morgan: send the numbers ("by Thursday")')
    assert "past due" not in project and "broken" not in _section(project, "Time-Critical Activities")
    assert any(line.startswith("- closed since ") and ": 1 done (marked by you): " in line for line in lines)
    personal = machine.page()
    assert "due Thu 15 Oct, past due" in personal
    assert "1 done (marked by you), 1 broken (marked by you)" in personal


# ── Under each meeting ───────────────────────────────────────────────────────


def test_meeting_lines_all_open_none_open_and_one(machine):
    """Matrix "Meeting line, all and none", "Meeting line, one", "Waiting only"."""
    _, ids = _summarised(machine)
    for each in ids:
        machine.run("proposal", "approve", each)
    assert "3 commitments from this meeting, all open: " in machine.page()
    for each in ids[:2]:
        _run(machine, "commitment", "done", each)
    _run(machine, "commitment", "withdrawn", ids[2])
    assert "3 commitments from this meeting, none open: " in machine.page()
    meeting, others = _summarised(machine, (("plan the demo", None, None),), meeting_id="mtg_d")
    machine.run("proposal", "approve", others[0])
    _run(machine, "commitment", "done", others[0])
    assert "  - 1 commitment from this meeting, closed: `pm-ai commitment list --scope personal --meeting mtg_d --state all`" in machine.page()


def test_an_approved_item_not_recorded_names_its_repair(machine):
    """Matrix "Not recorded"."""
    _, ids = _summarised(machine)
    _approve_in_store(machine, ids[0])
    page = machine.page()
    assert (
        f"  - Sam Rivera: send the cache TTL numbers (by Thursday), approved, not yet recorded as a "
        f"commitment: `pm-ai proposal approve {ids[0]} --scope personal`"
    ) in page
    assert "commitments from this meeting" not in page and "commitment from this meeting" not in page


def test_a_malformed_root_is_said_under_its_meeting_and_not_in_the_block(machine):
    """Matrix "Malformed root"."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    _append(machine, f"- entry=cml_0123456789ab commitment={ids[0]} kind=marked state=oops")
    page = machine.page()
    assert "send the cache TTL numbers" not in "\n".join(_block(page))
    assert (
        f"  - Sam Rivera: send the cache TTL numbers (by Thursday), approved; a line of "
        f"commitments_log.md naming {ids[0]} does not parse; fix or remove it"
    ) in page
    assert f"proposal approve {ids[0]}" not in page
    assert "commitments_log.md in personal: 1 line could not be read: `pm-ai commitment list --scope personal`." in page


def test_the_overflow_names_staged_or_open(machine, monkeypatch):
    """Matrix "Overflow", "Overflow, ledger unreadable"."""
    items = tuple((f"task {n}", None, None) for n in range(7))
    _, ids = _summarised(machine, items)
    assert "--meeting mtg_c --state staged`" in machine.page()
    for each in ids[4:]:
        machine.run("proposal", "approve", each)
    monkeypatch.setattr(type(machine.storage), "read_commitments", lambda self, *, scope: (_ for _ in ()).throw(OSError(5, "x")))
    page = machine.page()
    shown = [line for line in page.split("\n") if line.startswith("  - unassigned: task")]
    assert len(shown) == 5 and sum(line.endswith(", approved") for line in shown) == 1
    assert "  - and 2 more open action items: `pm-ai proposal list --scope personal --meeting mtg_c --state open`" in page


# ── Ledger edge cases ────────────────────────────────────────────────────────


def test_old_unrecorded_items_are_counted_with_their_repair(machine):
    """Matrix "Old unrecorded": 3 `proposal approve` commands, then the listing."""
    items = tuple((f"task {n}", None, None) for n in range(4))
    _, ids = _summarised(machine, items)
    for each in ids:
        _approve_in_store(machine, each)
    machine.storage.record_render(PERSONAL, at=NOW)  # the meeting is no longer in the window
    page = machine.page()
    assert f"4 approved items in `personal` are not yet recorded as commitments: `pm-ai proposal approve {sorted(ids)[0]} --scope personal`" in page
    assert ", and 1 more: `pm-ai proposal list --scope personal --state approved`." in page


def test_a_project_no_longer_enrolled_is_named_without_a_command(machine):
    """Matrix "Not enrolled"."""
    machine.storage.stage_proposal(
        Proposal(
            proposal_id="prp_gone_item",
            type="action_item",
            summary="ship it",
            payload={"what": "ship it"},
            target=None,
            cites=SourceRef.parse("meeting:mtg_gone"),
            created_at=NOW,
            state=ProposalState.APPROVED,
            scope="project:gone",
        )
    )
    page = machine.page()
    assert "`project:gone` is no longer enrolled, so its commitments are not listed." in page
    assert "--scope project:gone" not in page


def test_bad_lines_are_one_note_per_scope(machine):
    """Matrix "Bad lines"."""
    _commit(machine, "prp_x", due=_on(22))
    _mark(machine, "prp_x", Mark.FULFILLED, NOW - timedelta(days=1, hours=1))
    _mark(machine, "prp_x", Mark.WITHDRAWN, NOW - timedelta(days=1))  # refused after done
    _append(machine, "- entry=cml_0123456789ab kind=pending what=x")
    assert (
        "commitments_log.md in personal: 1 line could not be read, 1 mark was not applied: "
        "`pm-ai commitment list --scope personal`."
    ) in machine.page()


def test_the_same_id_in_two_ledgers_is_shown_once(machine):
    """Matrix "Same id twice"."""
    _commit(machine, "prp_twin", due=_on(22), what="the twin")
    _commit(machine, "prp_twin", scope=ALPHA, due=_on(22), what="the twin")
    page = machine.page()
    lines = _block(page)
    assert [line for line in lines if "the twin" in line] == [
        '- **due Thu 22 Oct** Sam Rivera: the twin ("by then") — from Example Weekly Sync-up, Mon 19 Oct (also in `project:alpha`)'
    ]
    assert "- to mark or correct them: `pm-ai commitment review --scope personal`" in lines


# ── A project page: names and what is withheld ───────────────────────────────


def test_a_project_page_names_members_only(machine):
    """Matrix "Project owner unresolved", "Project speaker unresolved", "Personal page shows text"."""
    _work(machine, _issue(142, ALEX))
    _commit(machine, "prp_owner", scope=ALPHA, owner="Sam", due=_on(22), what="unknown owner")
    _commit(machine, "prp_member", scope=ALPHA, owner="Alex Morgan", due=_on(23), what="member owner")
    _commit(machine, "prp_said", scope=ALPHA, promise=True, owner="Sam", speaker="Someone Else", due=_on(24), what="said one")
    lines = _block(machine.page(ALPHA))
    assert lines[0].startswith("- **due Thu 22 Oct** unassigned: unknown owner")
    assert lines[1].startswith("- **due Fri 23 Oct** Alex Morgan: member owner")
    assert lines[2].startswith("- **due Sat 24 Oct** said by someone in the meeting: said one")
    personal = _block(machine.page())
    assert any(line.startswith("- **due Thu 22 Oct** Sam: unknown owner") for line in personal)
    assert any("said by Someone Else, owner Sam: said one" in line for line in personal)


def test_the_alias_sentence_when_the_table_cannot_be_read(machine, monkeypatch):
    """Matrix "Alias unreadable"."""
    _work(machine, _issue(142, ALEX))
    _commit(machine, "prp_owner", scope=ALPHA, owner="Alex Morgan", due=_on(22))
    monkeypatch.setattr(type(machine.storage), "read_aliases", lambda self: (_ for _ in ()).throw(OSError(5, "aliases")))
    assert "The saved alias table could not be read, so action item owners were matched" in machine.page(ALPHA)


@pytest.mark.parametrize(
    ("what", "due", "promise"),
    [
        ("fix #150 first", None, False),  # Confidential by number
        ("tidy the release", "before the #150 release", False),  # Confidential in the due
        ("issue 150 first", None, True),  # Promise naming a confidential issue
    ],
)
def test_a_confidential_issue_shows_only_kind_and_meeting(machine, what, due, promise):
    """Acceptance 3; Matrix "Confidential by number", "Confidential in the due", "Promise naming a confidential issue"."""
    _work(machine, _issue(142, ALEX), _issue(150, ALEX, confidential=True))
    _commit(machine, "prp_open", scope=ALPHA, due=_on(30), what="an ordinary task", owner="Alex Morgan")
    _commit(
        machine, "prp_secret", scope=ALPHA, what=what, owner="Alex Morgan", speaker="Alex Morgan" if promise else None,
        promise=promise, due=Due(due, "2026-10-22") if due else _on(22),
    )
    page = machine.page(ALPHA)
    lines = _block(page)
    kind = "a spoken promise" if promise else "an action item"
    assert lines[0].startswith("- **due Fri 30 Oct** Alex Morgan: an ordinary task")
    assert lines[1] == f"- {kind} on a confidential issue — from Example Weekly Sync-up, Mon 19 Oct"
    assert what not in page and "Thu 22 Oct" not in _section(page, "Time-Critical Activities")
    # Matrix "Personal page shows text": the same commitment, as said.
    assert what in machine.page()


def test_a_link_to_a_confidential_issue_withholds_the_line(machine):
    """Matrix "Confidential by link".

    The meeting is older than Team Activity's 14 days, so no row shows the
    item under its issue; the link still marks it confidential (7d), and the
    block shows only its kind and meeting.
    """
    _work(machine, _issue(142, ALEX), _issue(150, ALEX, confidential=True))
    old = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)
    meeting = _put(machine, _meeting(scope=ALPHA, start=old))
    _commit(machine, "prp_linked", scope=ALPHA, what="tidy the cache", owner="Alex Morgan", due=_on(22), start=old)
    _link(machine, meeting, "prp_linked", "tidy the cache", "Alex Morgan", None, 150)
    page = machine.page(ALPHA)
    assert "- an action item on a confidential issue — from Example Weekly Sync-up, Mon 28 Sep" in _block(page)
    assert "tidy the cache" not in page and "Alex Morgan: " not in "\n".join(_block(page))


def test_unknown_confidentiality_withholds_the_text_and_keeps_the_date(machine, monkeypatch):
    """Matrix "Number absent", "GitLab unread", "Links unread", "Not detected"."""
    _work(machine, _issue(142, ALEX), _issue(150, ALEX, confidential=True))
    _commit(machine, "prp_absent", scope=ALPHA, what="fix #999", owner="Alex Morgan", due=Due("by #999's day", "2026-10-22"))
    _commit(machine, "prp_numero", scope=ALPHA, what="№150 later", owner="Alex Morgan", due=_on(23))
    lines = _block(machine.page(ALPHA))
    assert lines[0] == "- **due Thu 22 Oct** Alex Morgan: an action item whose text is withheld — from Example Weekly Sync-up, Mon 19 Oct"
    assert lines[1].startswith("- **due Fri 23 Oct** Alex Morgan: №150 later")  # the accepted limit
    # The member updates cannot be read: every action item's text withheld, the sentence printed.
    monkeypatch.setattr(type(machine.storage), "read_member_updates", lambda self, *, scope: (_ for _ in ()).throw(OSError(5, "x")))
    page = machine.page(ALPHA)
    assert all("whose text is withheld" in line for line in _block(page) if line.startswith("- **"))
    assert "The meeting updates' links to issues could not be read" in page


def test_an_unreadable_gitlab_record_withholds_a_promise_naming_a_number(machine, monkeypatch):
    """Matrix "GitLab unread"."""
    _commit(machine, "prp_seven", scope=ALPHA, promise=True, speaker="Alex Morgan", owner=None, what="look at #7", due=_on(22))
    monkeypatch.setattr(
        type(machine.storage), "project_work", lambda self, scope: (_ for _ in ()).throw(OSError(5, "gitlab"))
    )
    page = machine.page(ALPHA)
    line = _block(page)[0]
    assert line.startswith("- **due Thu 22 Oct** ") and "a spoken promise whose text is withheld" in line
    assert "#7" not in _section(page, "Time-Critical Activities").split("\n\n")[1]
    assert "Whether an issue is confidential cannot be told" in page


def test_a_commitment_shown_under_an_issue_is_counted_not_listed(machine):
    """Matrix "Shown under an issue"."""
    _work(machine, _issue(142, ALEX))
    meeting = _put(machine, _meeting(scope=ALPHA))
    _commit(machine, "prp_under", scope=ALPHA, what="fix the cache", owner="Alex Morgan", due=_on(22))
    _link(machine, meeting, "prp_under", "fix the cache", "Alex Morgan", None, 142)
    page = machine.page(ALPHA)
    block = _block(page)
    assert "- 1 open commitment shown under #142 in Team Activity" in block
    assert not any("fix the cache" in line for line in block)
    assert "fix the cache" in _section(page, "Team Activity")


# ── Team Activity ────────────────────────────────────────────────────────────


def _team_rows(machine: Machine) -> str:
    return _section(machine.page(ALPHA), "Team Activity")


def test_team_activity_shows_the_commitment(machine):
    """Matrix "Team Activity, done", "Team Activity, broken", "Team Activity, past due", "Team Activity, PM's owner", "Team Activity, withdrawn"."""
    # One issue each: Team Activity shows two rows per issue.
    _work(machine, *(_issue(iid, ALEX) for iid in (142, 143, 144, 145)))
    meeting = _put(machine, _meeting(scope=ALPHA))
    items = (
        ("prp_t_done", "fix the cache", "Alex Morgan", "by Friday", 142),
        ("prp_t_broken", "fix the build", "Alex Morgan", None, 143),
        ("prp_t_late", "fix the flake", "Sam Rivera", "by Tuesday", 144),
        ("prp_t_gone", "fix the docs", "Alex Morgan", None, 145),
    )
    for root, what, owner, said, iid in items:
        due = Due(said, "2026-10-20") if said == "by Tuesday" else (Due(said, "2026-10-23") if said else None)
        _commit(machine, root, scope=ALPHA, what=what, owner=owner, due=due)
        _link(machine, meeting, root, what, owner, said, iid)
    _mark(machine, "prp_t_done", Mark.FULFILLED, NOW - timedelta(hours=1), scope=ALPHA)
    _mark(machine, "prp_t_broken", Mark.BROKEN, NOW - timedelta(hours=1), scope=ALPHA)
    _mark(machine, "prp_t_gone", Mark.WITHDRAWN, NOW - timedelta(hours=1), scope=ALPHA)
    _run(machine, "commitment", "owner", "prp_t_late", "Alex Morgan", "--scope", "project:alpha")
    rows = _team_rows(machine)
    done = next(line for line in rows.split("\n") if "fix the cache" in line)
    assert done.endswith("; commitment done on Wed 21 Oct")
    assert "due as said in the meeting: by Friday (Fri 23 Oct)" in done
    broken = next(line for line in rows.split("\n") if "fix the build" in line)
    assert "broken" not in broken and "commitment" not in broken.split("(said in the meeting)")[-1]
    late = next(line for line in rows.split("\n") if "fix the flake" in line)
    assert "action for Alex Morgan" in late and "past due" not in late
    assert "fix the docs" not in rows


def test_team_activity_shows_the_pms_due(machine):
    """Matrix "Team Activity, PM's due"."""
    _work(machine, _issue(142, ALEX))
    meeting = _put(machine, _meeting(scope=ALPHA))
    _commit(machine, "prp_due", scope=ALPHA, what="fix the cache", owner="Alex Morgan", due=Due("next sprint", "unresolved"))
    _link(machine, meeting, "prp_due", "fix the cache", "Alex Morgan", "next sprint", 142)
    _run(machine, "commitment", "due", "prp_due", "friday", "--scope", "project:alpha")
    row = next(line for line in _team_rows(machine).split("\n") if "fix the cache" in line)
    assert "due as said in the meeting: next sprint (set by you: Fri 23 Oct)" in row


def test_team_activity_joins_the_ledger_when_the_proposals_cannot_be_read(machine, monkeypatch):
    """Matrix "Team Activity, proposals unreadable"."""
    _work(machine, _issue(142, ALEX))
    meeting = _put(machine, _meeting(scope=ALPHA))
    for root, what in (("prp_w", "withdrawn one"), ("prp_d", "done one")):
        _commit(machine, root, scope=ALPHA, what=what, owner="Alex Morgan", due=_on(22))
        _link(machine, meeting, root, what, "Alex Morgan", None, 142)
    _mark(machine, "prp_w", Mark.WITHDRAWN, NOW - timedelta(hours=1), scope=ALPHA)
    _mark(machine, "prp_d", Mark.FULFILLED, NOW - timedelta(hours=1), scope=ALPHA)
    monkeypatch.setattr(
        type(machine.storage), "proposals_of_meetings", lambda self, ids: (_ for _ in ()).throw(OSError(5, "x"))
    )
    rows = _team_rows(machine)
    assert "withdrawn one" not in rows
    assert "; commitment done on Wed 21 Oct" in rows
    assert "Your decisions on the action items could not be read" in rows


def test_team_activity_shows_every_row_when_the_ledger_cannot_be_read(machine, monkeypatch):
    """Matrix "Ledger unreadable, project page": rows as recorded, withdrawn included; the block is the note."""
    _work(machine, _issue(142, ALEX))
    meeting = _put(machine, _meeting(scope=ALPHA))
    _commit(machine, "prp_w", scope=ALPHA, what="withdrawn one", owner="Alex Morgan", due=_on(22))
    _link(machine, meeting, "prp_w", "withdrawn one", "Alex Morgan", None, 142)
    _mark(machine, "prp_w", Mark.WITHDRAWN, NOW - timedelta(hours=1), scope=ALPHA)
    monkeypatch.setattr(type(machine.storage), "read_commitments", lambda self, *, scope: (_ for _ in ()).throw(OSError(5, "x")))
    page = machine.page(ALPHA)
    assert "withdrawn one" in _section(page, "Team Activity")
    assert "The commitments of `project:alpha` could not be read (OSError)" in page
    assert _block(page) == [] and "No open commitment" not in page


# ── One read per scope per render, and the bound ─────────────────────────────


def test_a_project_page_reads_its_ledger_once(machine, monkeypatch):
    """Matrix "One read": the digest, the block and Team Activity share one read (15c read it twice)."""
    _work(machine, _issue(142, ALEX))
    meeting, ids = _summarised(machine, (("fix the cache", "Alex Morgan", None), ("plan the demo", None, None)), scope=ALPHA)
    machine.run("proposal", "approve", ids[0])
    _link(machine, meeting, ids[0], "fix the cache", "Alex Morgan", None, 142)
    original = type(machine.storage).read_commitments
    calls: list[str] = []

    def counted(self, *, scope):
        calls.append(str(scope))
        return original(self, scope=scope)

    monkeypatch.setattr(type(machine.storage), "read_commitments", counted)
    machine.page(ALPHA)
    assert calls == ["project:alpha"]
    calls.clear()
    machine.page()
    assert calls == ["personal", "project:alpha"]


def _bulk(machine: Machine, scope: DataScope, count: int) -> None:
    """`count` `pending` lines, written straight to the file (a test's fixture, not a writer)."""
    path = _ledger_path(machine, scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    texts = []
    for n in range(count):
        commitment = Commitment(
            commitment_id=f"prp_bulk_{scope.kind.value}{scope.project_id or ''}_{n}", proposal_id=f"prp_bulk_{scope.kind.value}{scope.project_id or ''}_{n}", type="action_item", meeting_id="mtg_c",
            title=TITLE, start=MONDAY, day=MONDAY.date(), day_from=None, what=f"task {n}", owner="Sam Rivera",
            speaker=None, due=_on(22 + n % 9), state=CommitmentState.PENDING, state_source=StateSource.NONE,
            approved_at=APPROVED, cites=Cites("meeting:mtg_c", "Sam Rivera"),
        )
        texts.append(render_line(Pending(commitment, via="terminal", written="approval", at=APPROVED)).text)
    with open(path, "a", encoding="utf-8") as ledger:
        ledger.write("".join(text + "\n" for text in texts))


def test_the_renders_ledger_reads_stay_within_a_second(machine, monkeypatch):
    """Matrix "Timed render": a personal page over 5,000-line personal and two project ledgers; a project page over one."""
    machine.enrol("beta")
    beta = DataScope(ScopeKind.PROJECT, "beta")
    for scope in (PERSONAL, ALPHA, beta):
        _bulk(machine, scope, 5000)
    original = CommitmentLog.read
    spent: list[float] = []

    def timed(self, scope):
        started = time.perf_counter()
        try:
            return original(self, scope)
        finally:
            spent.append(time.perf_counter() - started)

    monkeypatch.setattr(CommitmentLog, "read", timed)
    page = machine.page()
    assert len(spent) == 3 and sum(spent) <= 1.0, spent
    assert "- and 14990 more open commitments: " in page
    spent.clear()
    machine.page(ALPHA)
    assert len(spent) == 1 and sum(spent) <= 1.0, spent


# ── Proposal cards show the commitment ───────────────────────────────────────


def test_a_proposal_card_shows_its_commitment(machine):
    """Matrix "Proposal card", "Proposal card, replacement", "Proposal card, waiting"."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    _run(machine, "commitment", "done", ids[0])
    code, out, _ = machine.run("proposal", "show", ids[0])
    assert code == EXIT_OK
    assert (
        f"  commitment: done on Wed 21 Oct, due Thu 22 Oct: pm-ai commitment show {ids[0]} --scope personal"
    ) in out.split("\n")
    code, found = machine.json("proposal", "show", ids[0])
    commitment = found["proposal"]["commitment"]
    assert commitment["state"] == "fulfilled" and commitment["state_source"] == "pm"
    assert commitment["recorded"] is True and commitment["past_due"] is False
    assert commitment["show"] == ["commitment", "show", ids[0], "--scope", "personal"]
    machine.run("proposal", "edit", ids[0], "--what", "send the numbers")
    code, found = machine.json("proposal", "show", f"{ids[0]}_e1")
    assert found["proposal"]["commitment"]["commitment_id"] == ids[0]
    code, found = machine.json("proposal", "show", ids[1])
    assert found["proposal"]["commitment"] is None


def test_a_proposal_card_says_when_its_commitment_is_not_recorded_or_not_read(machine, monkeypatch):
    """Matrix "Proposal card, not recorded", "Proposal card, unreadable"."""
    _, ids = _summarised(machine)
    _approve_in_store(machine, ids[0])
    code, found = machine.json("proposal", "show", ids[0])
    commitment = found["proposal"]["commitment"]
    assert commitment["recorded"] is False and commitment["past_due"] is False
    assert commitment["repair"] == ["proposal", "approve", ids[0], "--scope", "personal"]
    monkeypatch.setattr(type(machine.storage), "read_commitments", lambda self, *, scope: (_ for _ in ()).throw(OSError(5, "x")))
    code, out, _ = machine.run("proposal", "show", ids[0])
    assert code == EXIT_OK and "  commitment: the ledger could not be read (OSError)" in out
    code, found = machine.json("proposal", "show", ids[0])
    assert found["proposal"]["commitment"]["recorded"] is None and found["proposal"]["commitment"]["past_due"] is None


def test_the_bound_leaves_a_scope_not_read(machine):
    """Matrix "Proposal card, bound": the second scope's cards are not read within the bound."""
    _, home = _summarised(machine)
    _, away = _summarised(machine, scope=ALPHA, meeting_id="mtg_alpha")
    machine.run("proposal", "approve", home[0])
    machine.run("proposal", "approve", away[0])
    ticks = iter([0.0, 0.1, 5.0, 5.0, 5.0, 5.0])
    desk = replace(machine.desk(), monotonic=lambda: next(ticks, 5.0))
    code, out, _ = machine.run("proposal", "list", "--state", "approved", desk=desk)
    assert code == EXIT_OK
    assert "  commitment: not read within the bound" in out
    assert "  commitment: open, due Thu 22 Oct: pm-ai commitment show" in out


def test_proposal_list_reads_each_scope_once_within_the_bound(machine, monkeypatch):
    """Matrix "Proposal list reads": once per scope; timed on a 5,000-line ledger."""
    _, home = _summarised(machine)
    _, away = _summarised(machine, scope=ALPHA, meeting_id="mtg_alpha")
    for each in (*home, *away):
        machine.run("proposal", "approve", each)
    _bulk(machine, PERSONAL, 5000)
    original = type(machine.storage).read_commitments
    calls: list[str] = []

    def counted(self, *, scope):
        calls.append(str(scope))
        return original(self, scope=scope)

    monkeypatch.setattr(type(machine.storage), "read_commitments", counted)
    started = time.perf_counter()
    code, found = machine.json("proposal", "list", "--state", "approved")
    assert time.perf_counter() - started <= 1.0
    assert code == EXIT_OK and sorted(calls) == ["personal", "project:alpha"]
    assert all(card["commitment"]["recorded"] is True for card in found["proposals"])


# ── Placeholders a wrapper can recognise ─────────────────────────────────────


def _fill(command: list[str], fill: list[dict], values: dict[str, str]) -> list[str]:
    """What a wrapper does: required placeholders replaced, optional ones dropped with their option."""
    words = list(command)
    for each in sorted(fill, key=lambda entry: entry["index"], reverse=True):
        if each["name"] in values:
            words[each["index"]] = values[each["name"]]
        elif not each["required"]:
            del words[each["index"] - 1 : each["index"] + 1]
    return words


def test_a_wrapper_fills_every_command_and_an_unfilled_one_is_refused(machine):
    """Acceptance 5; Matrix "Placeholder in JSON", "Placeholder as a value"."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    code, shown = _json(machine, "commitment", "show", ids[0])
    actions = shown["commitment"]["actions"]
    assert actions["altered"]["fill"] == [{"index": 6, "name": "note", "required": False}]
    assert actions["due"]["command"][3] == "<when>" and actions["due"]["fill"][0]["required"] is True
    before = _text(machine)
    for name in ("altered", "due", "owner"):
        code, _, err = _run(machine, *actions[name]["command"])
        assert code == EXIT_USAGE, (name, err)
        assert "placeholder" in err
    assert _text(machine) == before
    owner = _fill(actions["owner"]["command"], actions["owner"]["fill"], {"name": "Alex Morgan"})
    code, _, err = _run(machine, *owner)
    assert code == EXIT_OK, err
    code, shown = _json(machine, "commitment", "show", ids[0])
    actions = shown["commitment"]["actions"]
    due = _fill(actions["due"]["command"], actions["due"]["fill"], {"when": "friday"})
    code, _, err = _run(machine, *due)
    assert code == EXIT_OK, err
    code, shown = _json(machine, "commitment", "show", ids[0])
    actions = shown["commitment"]["actions"]
    altered = _fill(actions["altered"]["command"], actions["altered"]["fill"], {})
    assert "--note" not in altered
    code, _, err = _run(machine, *altered)
    assert code == EXIT_OK, err


def test_the_reopen_command_and_the_reject_refusal_carry_their_fill(machine, monkeypatch):
    """Matrix "Reopen command", "Refusal, open item", "Refusal, unreadable"."""
    _, ids = _summarised(machine)
    for each in ids[:2]:
        machine.run("proposal", "approve", each)
    _run(machine, "commitment", "broken", ids[1])
    code, shown = _json(machine, "commitment", "show", ids[1])
    reopen = shown["commitment"]["actions"]["due"]["reopen"]
    assert reopen["command"][-2:] == ["--due", "<when>"] and reopen["fill"][0]["name"] == "when"
    assert "<" not in shown["commitment"]["actions"]["due"]["reason"]
    code, out, _ = _run(machine, "commitment", "show", ids[1])
    assert f"reopen: pm-ai commitment altered {ids[1]} --scope personal --due '<when>'" in out
    code, found = machine.json("proposal", "reject", ids[0])
    assert code == EXIT_REFUSAL
    commands = found["commitment"]["commands"]
    assert commands["altered"]["command"][-1] == "<note>"
    assert commands["altered"]["fill"] == [{"index": len(commands["altered"]["command"]) - 1, "name": "note", "required": False}]
    assert commands["edit"]["command"][-1] == "<what>" and commands["edit"]["fill"][0]["required"] is True
    assert "--note '<note>'" in found["reason"] and "--what '<what>'" in found["reason"]
    monkeypatch.setattr(type(machine.storage), "read_commitments", lambda self, *, scope: (_ for _ in ()).throw(OSError(5, "x")))
    code, found = machine.json("proposal", "reject", ids[0])
    commands = found["commitment"]["commands"]
    assert set(commands) == {"withdrawn", "altered", "edit"}
    assert commands["withdrawn"] == {"command": ["commitment", "withdrawn", ids[0], "--scope", "personal"], "fill": []}


@pytest.mark.parametrize(
    "words",
    [
        ("commitment", "due", "{id}", "<when>"),
        ("commitment", "altered", "{id}", "--note", "<note>"),
        ("commitment", "altered", "{id}", "--due", "<when>"),
        ("commitment", "altered", "{id}", "--owner", "<name>"),
        ("commitment", "altered", "{id}", "--what", "<what>"),
        ("commitment", "owner", "{id}", "<name>"),
        ("commitment", "done", "{id}", "--note", "…"),
        ("commitment", "broken", "{id}", "--note", "..."),
        ("commitment", "withdrawn", "{id}", "--note", '"<NOTE>"'),
    ],
)
def test_a_placeholder_typed_as_a_value_is_a_usage_error(machine, words):
    """Matrix "Placeholder as a value", "Literal ellipsis"."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    before = _text(machine)
    code, _, err = _run(machine, *(each.format(id=ids[0]) for each in words))
    assert code == EXIT_USAGE and "placeholder" in err
    assert _text(machine) == before


def test_proposal_edit_refuses_a_placeholder(machine):
    """Matrix "Placeholder as a value": `proposal edit <id> --what '<what>'`."""
    _, ids = _summarised(machine)
    before = machine.load(ids[0])
    code, _, err = machine.run("proposal", "edit", ids[0], "--what", "<what>")
    assert code == EXIT_USAGE and "type the wording itself, not <what>" in err
    after = machine.load(ids[0])
    assert (after.state, after.version) == (before.state, before.version)


def test_review_asks_again_for_a_placeholder(machine, monkeypatch):
    """Matrix "Review placeholder"."""
    from test_commitment_commands import _review

    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    code, out, _ = _review(machine, monkeypatch, "u", "<when>", "friday", "q")
    assert code == EXIT_OK and "type when it is due, not <when>" in out
    code, out, _ = _review(machine, monkeypatch, "o", "<name>", "Alex Morgan", "q")
    assert "type the name itself, not <name>" in out
    code, out, _ = _review(machine, monkeypatch, "a", "<what>", "send it", "", "", "", "q")
    assert "type the wording itself, not <what>" in out
    assert not any("<" in line.get("due", "") or "<" in line.get("owner", "") for line in _lines(machine))


# ── Review patches (15d) ─────────────────────────────────────────────────────


def test_an_unrecorded_item_listed_under_its_meeting_is_not_in_the_note(machine):
    """Matrix "Not recorded": its repair is under its meeting, so the scope's note leaves it out."""
    _, ids = _summarised(machine)
    _approve_in_store(machine, ids[0])
    page = machine.page()
    assert f"`pm-ai proposal approve {ids[0]} --scope personal`" in page
    assert "not yet recorded as commitments" not in page
    assert "approved item in `personal` is not yet recorded" not in page


def test_an_unrecorded_item_past_the_meetings_cap_is_in_the_note(machine):
    """Matrix "Overflow": an approved item only counted in "and N more" gets its repair in the note."""
    items = tuple((f"task {n}", None, None) for n in range(7))
    _, ids = _summarised(machine, items)
    _approve_in_store(machine, ids[6])
    page = machine.page()
    lines = [line for line in page.split("\n") if line.startswith("  - unassigned: task")]
    assert len(lines) == 5 and not any(ids[6] in line for line in lines)
    assert (
        f"1 approved item in `personal` is not yet recorded as a commitment: "
        f"`pm-ai proposal approve {ids[6]} --scope personal`."
    ) in page


def test_the_staged_overflow_command_lists_exactly_the_hidden_items(machine):
    """Matrix "Overflow": the `--state staged` command, run, lists the waiting items the page hid."""
    items = tuple((f"task {n}", None, None) for n in range(7))
    meeting, ids = _summarised(machine, items)
    page = machine.page()
    overflow = "pm-ai proposal list --scope personal --meeting mtg_c --state staged"
    assert f"  - and 2 more open action items: `{overflow}`" in page
    shown = {line.split(": ", 1)[1].split(",")[0] for line in page.split("\n") if line.startswith("  - unassigned: task")}
    code, listing = machine.json(*overflow.split()[1:])
    assert code == EXIT_OK
    listed = {card["proposal_id"] for card in listing["proposals"]}
    hidden = {ids[n] for n, (what, _, _) in enumerate(items) if what not in shown}
    assert len(hidden) == 2 and hidden <= listed and listed == set(ids)


def test_a_project_meetings_line_counts_a_hand_copy_once(machine):
    """Matrix "Same id twice": the copy in personal is shown; alpha's meeting line still counts it once."""
    _, ids = _summarised(machine, (("fix the cache", "Alex Morgan", None),), scope=ALPHA)
    machine.run("proposal", "approve", ids[0])
    line = next(text for text in _text(machine, ALPHA).split("\n") if text.startswith("- entry="))
    _commit(machine, "prp_seed", what="seed")  # creates the personal ledger
    _append(machine, line)
    page = machine.page()
    assert (
        "  - 1 commitment from this meeting, open: `pm-ai commitment list --scope project:alpha "
        "--meeting mtg_c --state all`"
    ) in page
    copies = [text for text in _block(page) if "fix the cache" in text]
    assert len(copies) == 1 and copies[0].endswith("(also in `project:alpha`)")


def test_a_replacements_repair_names_the_replacement(machine, monkeypatch):
    """Matrix "Not recorded, edited": approved, edited, no line: the repair names `<root>_e1`."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    _ledger_path(machine).unlink()  # the approval's line lost
    monkeypatch.setattr(ProposalDesk, "_record", lambda self, *args, **kwargs: None)
    code, _, err = machine.run("proposal", "edit", ids[0], "--what", "send the numbers")
    assert code == EXIT_OK, err
    page = machine.page()
    assert f"`pm-ai proposal approve {ids[0]}_e1 --scope personal`" in page
    assert f"`pm-ai proposal approve {ids[0]} --scope personal`" not in page


def test_a_recorded_replacement_is_in_the_block_not_under_its_meeting(machine):
    """Matrix "Recorded, edited"."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    machine.run("proposal", "edit", ids[0], "--what", "send the numbers")
    page = machine.page()
    assert not any(line.startswith("  - ") and "send the numbers" in line for line in page.split("\n"))
    assert any("Sam Rivera: send the numbers" in line for line in _block(page))
    assert "  - 1 commitment from this meeting, open: " in page


def test_team_activity_shows_an_estimated_date_and_never_past_due(machine):
    """Matrix "Team Activity, estimated"."""
    _work(machine, _issue(142, ALEX))
    meeting = _put(machine, _meeting(scope=ALPHA))
    _commit(machine, "prp_est", scope=ALPHA, what="fix the cache", owner="Alex Morgan",
            due=Due("by Tuesday", "2026-10-20"), estimated=True)
    _link(machine, meeting, "prp_est", "fix the cache", "Alex Morgan", "by Tuesday", 142)
    row = next(line for line in _team_rows(machine).split("\n") if "fix the cache" in line)
    assert "due as said in the meeting: by Tuesday (Tue 20 Oct, meeting day estimated)" in row
    assert "past due" not in row


def test_team_activity_on_an_edited_item_shows_its_commitment(machine):
    """Matrix "Team Activity, done" on a replacement: the `<root>_e1` edit, recorded, linked, then marked."""
    _work(machine, _issue(142, ALEX))
    meeting, ids = _summarised(machine, (("fix the cache", "Alex Morgan", "by Friday"),), scope=ALPHA)
    machine.run("proposal", "approve", ids[0])
    _link(machine, meeting, ids[0], "fix the cache", "Alex Morgan", "by Friday", 142)
    machine.run("proposal", "edit", ids[0], "--what", "fix the cache TTL", "--scope", "project:alpha")
    _run(machine, "commitment", "due", ids[0], "2026-10-30", "--scope", "project:alpha")
    _run(machine, "commitment", "done", ids[0], "--scope", "project:alpha")
    row = next(line for line in _team_rows(machine).split("\n") if "fix the cache" in line)
    assert "fix the cache TTL" in row
    assert "due as said in the meeting: by Friday (set by you: Fri 30 Oct)" in row
    assert row.endswith("; commitment done on Wed 21 Oct")


def test_review_asks_each_question_again_for_a_placeholder(machine, monkeypatch):
    """Matrix "Review placeholder": `<note>` at `b`'s note, `<name>` and `<when>` at `a`'s questions."""
    from test_commitment_commands import _review

    _, ids = _summarised(machine)
    for each in ids[:2]:
        machine.run("proposal", "approve", each)
    code, out, _ = _review(
        machine, monkeypatch,
        "a", "", "<name>", "Alex Morgan", "<when>", "friday", "",  # first card: altered
        "b", "<note>", "it slipped",  # second card: broken
        "q",
    )
    assert code == EXIT_OK
    assert out.count("type the name itself, not <name>") == 1
    assert out.count("type when it is due, not <when>") == 1
    assert out.count("type the note itself, not <note>") == 1
    lines = _lines(machine)
    assert any(line.get("owner") == "Alex Morgan" and line["kind"] == "owner_set" for line in lines)
    assert any(line["kind"] == "marked" and line.get("state") == "broken" and line.get("note") == "it slipped" for line in lines)
    assert not any("<" in value for line in lines for value in line.values())
