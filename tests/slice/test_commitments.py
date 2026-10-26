"""Story 15a — approved items become commitments, in the meeting's own ledger.

One test per row of the story's I/O matrix that the slice reaches (the
resolver's own rows are in `tests/core/test_due_dates.py`, the line format's
and the fold's in `tests/core/test_commitment_log.py`), plus the two
processes approving one id, the timed project-scope approval over a 5,000-line
ledger, the catch-up through `_lifespan`, and one Team Activity assertion.

Every row runs against a temporary home with `alpha` enrolled at a real git
repository (13a's `Machine`); the clock is the test's and no test reaches a
model. Names are `11d`'s fixture names (`Example Weekly Sync-up`, `Alex
Morgan`, `Sam Rivera`); handles are `example.com`. Nothing reads the PM's
own store.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import subprocess
import sys
import threading
import time
import types
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from test_proposals import ALPHA, APPLICATION, NOW, PERSONAL, REPO_ROOT, TITLE, Machine, _git
from pm_ai.app import daemon as daemon_module
from pm_ai.app.commitments import CommitmentCatchUp, CommitmentRecorder, catch_up, recorder_for
from pm_ai.app.concurrency import IOPool, WriterThread
from pm_ai.app.entry import DAEMON_BOUNDS
from pm_ai.app.pipelines import _issue_notes, run_transcript_ingestion
from pm_ai.app.prompts import DAEMON_LOGGER, RequestPath
from pm_ai.app.proposals import PROPOSAL_WRITE_BOUND_SECONDS, ProposalDesk
from pm_ai.core.aliases import parse_aliases
from pm_ai.core.cadence import LookBack, LookBackKind
from pm_ai.core.commitment_log import Pending, entry_id, fold, parse_ledger, render_line
from pm_ai.core.meeting_records import MeetingRecord, capture_name
from pm_ai.core.meeting_summary import action_item_id
from pm_ai.core.member_updates import Candidate, ItemKind, Link, LinkKind, lines_to_append
from pm_ai.core.member_updates import parse_ledger as parse_updates
from pm_ai.app.pipelines import note_summary_input
from pm_ai.domain.commitments import COMMITMENTS_LOG
from pm_ai.domain.health import Report
from pm_ai.domain.identity import Actor, DataScope, ScopeKind, SourceRef
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.domain.meeting_summaries import SummaryActionItem
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.proposals import IllegalTransition, Proposal
from pm_ai.domain.transcripts import CaptureFormat, Transcript, TranscriptSource, TranscriptStateKind, Utterance
from pm_ai.domain.work import Issue, ProjectWork, Section, SectionStatus
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL

UTC = timezone.utc
MONDAY = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
"""The fixture meeting: Mon 19 Oct 2026, 10:00 UTC."""
ITEMS = (
    ("send the cache TTL numbers", "Sam Rivera", "by Thursday"),
    ("book the review room", "Alex Morgan", "до конца недели"),
    ("draft the release notes", None, "next sprint"),
)
FOUR = (*ITEMS, ("collect the feedback", "Alex Morgan", None))


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def machine(tmp_path, monkeypatch) -> Machine:
    return Machine(tmp_path, monkeypatch)


def _put(machine: Machine, meeting: Meeting) -> Meeting:
    """`Machine.meeting`, for a `Meeting` built here (an estimated start, a long title)."""
    machine.daemon.meetings.put(meeting)
    machine.storage.write_capture(
        "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n<v Alex Morgan>we ship on Friday\n\n"
        "00:00:06.000 --> 00:00:09.000\n<v Sam Rivera>I will send the cache numbers\n",
        scope=meeting.scope,
        name=capture_name(meeting.meeting_id, meeting.start, CaptureFormat.GRAPH),
    )
    machine.storage.record_transcript_state(
        meeting.scope, meeting.meeting_id, TranscriptStateKind.INGESTED, "ingested", retryable=False
    )
    note_summary_input(machine.daemon, meeting.scope, meeting.meeting_id, ingested_now=False)
    return meeting


def _meeting(meeting_id: str = "mtg_c", *, scope: DataScope = PERSONAL, **fields) -> Meeting:
    values: dict = dict(
        meeting_id=meeting_id,
        title=TITLE,
        start=MONDAY,
        duration_minutes=30,
        attendees=(Actor("email:alex@example.com"),),
        scope=scope,
    )
    values.update(fields)
    return Meeting(**values)


def _summarised(machine: Machine, items=ITEMS, **fields) -> tuple[Meeting, list[str]]:
    meeting = _put(machine, _meeting(**fields))
    machine.summarize(meeting, items)
    return meeting, [machine.item_id(meeting, n, items) for n in range(len(items))]


def _ledger_path(machine: Machine, scope: DataScope = PERSONAL) -> Path:
    return Path(machine.storage.paths.resolve(scope, COMMITMENTS_LOG))


def _text(machine: Machine, scope: DataScope = PERSONAL) -> str:
    path = _ledger_path(machine, scope)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _lines(machine: Machine, scope: DataScope = PERSONAL) -> list[dict[str, str]]:
    parsed = parse_ledger(_ledger_path(machine, scope).read_bytes() if _ledger_path(machine, scope).exists() else b"")
    return [dict(line.fields) for line in parsed.lines]


def _folded(machine: Machine, scope: DataScope = PERSONAL):
    return fold(parse_ledger(_ledger_path(machine, scope).read_bytes()))


@contextmanager
def _claim_held(machine: Machine, scope: DataScope = PERSONAL):
    """Another holder of the ledger's claim, so an append meets it busy."""
    ready, release = threading.Event(), threading.Event()

    def hold() -> None:
        with machine.storage.exclusive(scope=scope, artifact=COMMITMENTS_LOG):
            ready.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    assert ready.wait(5)
    try:
        yield
    finally:
        release.set()
        holder.join()


def _approve_in_store(machine: Machine, proposal_id: str) -> Proposal:
    """An approval as 13a made it, before 15a: the row moves, and no line is written."""
    current = machine.load(proposal_id)
    return machine.storage.change_proposal(
        proposal_id,
        ProposalState.APPROVED,
        expected_version=current.version,
        rule=machine.daemon.proposal_types.rule(current.type),
        via="terminal",
    )


def _recorder(machine: Machine, *, zone=None, machine_zone=None, meetings=None) -> CommitmentRecorder:
    return CommitmentRecorder(
        storage=machine.storage,
        meetings=meetings if meetings is not None else machine.daemon.meetings,
        types=machine.daemon.proposal_types,
        now=lambda: machine.clock,
        display_zone=zone,
        machine_zone=machine_zone,
    )


def _desk(machine: Machine, recorder: CommitmentRecorder, via: str = "terminal") -> ProposalDesk:
    return ProposalDesk(
        now=lambda: machine.clock,
        paths=machine.storage.paths,
        storage=machine.storage,
        via=via,
        types=machine.daemon.proposal_types,
        commitments=recorder,
    )


def _catch_up(machine: Machine, **options):
    return catch_up(_recorder(machine), paths=machine.storage.paths, **options)


# ── Approving ────────────────────────────────────────────────────────────────


def test_approving_an_action_item_writes_one_pending_line(machine):
    """Matrix "Approve an action item"; acceptance 1."""
    meeting, ids = _summarised(machine)
    code, out, _ = machine.run("proposal", "approve", ids[0])
    assert code == EXIT_OK
    path = _ledger_path(machine)
    assert path == machine.home / ".manager-ai" / "memory" / "commitments_log.md"
    (line,) = _lines(machine)
    assert line["kind"] == "pending" and line["commitment"] == ids[0] and line["proposal"] == ids[0]
    assert line["day"] == "2026-10-19" and "day_from" not in line
    assert line["due_said"] == "by Thursday" and line["due"] == "2026-10-22"
    assert line["written"] == "approval" and line["via"] == "terminal"
    assert line["meeting"] == meeting.meeting_id and line["title"] == TITLE
    assert line["owner"] == "Sam Rivera" and "speaker" not in line
    assert (
        f'{ids[0]} approved: you agreed to it, and it is recorded as a commitment, due Thu 22 Oct ("by Thursday").'
        in out
    )
    for other in ids[1:]:
        assert other not in path.read_text()
    assert oct(path.stat().st_mode & 0o777) == oct(0o600)


def test_approving_in_a_project_writes_the_projects_ledger_only(machine):
    """Matrix "Approve in a project"."""
    meeting, ids = _summarised(machine, scope=ALPHA)
    code, _, _ = machine.run("proposal", "approve", ids[0])
    assert code == EXIT_OK
    project_ledger = _ledger_path(machine, ALPHA)
    assert project_ledger == machine.projects["alpha"] / ".project-ai" / "memory" / "commitments_log.md"
    assert [line["commitment"] for line in _lines(machine, ALPHA)] == [ids[0]]
    assert not _ledger_path(machine).exists()


def test_the_json_answer_carries_the_commitment(machine):
    """Matrix "JSON"."""
    _, ids = _summarised(machine)
    code, found = machine.json("proposal", "approve", ids[0])
    assert code == EXIT_OK and found["format"] == 1 and found["outcome"] == "approved"
    assert found["commitment"] == {
        "commitment_id": ids[0],
        "scope": "personal",
        "state": "pending",
        "recorded": True,
        "repaired": False,
        "reason": None,
        "day": "2026-10-19",
        "day_from": None,
        "due": {"said": "by Thursday", "value": "2026-10-22", "date": "2026-10-22", "estimated": False},
    }


def test_a_waiting_item_writes_nothing(machine):
    """Matrix "Waiting item"."""
    _summarised(machine)
    _summarised(machine, scope=ALPHA)
    assert not _ledger_path(machine).exists() and not _ledger_path(machine, ALPHA).exists()


def test_approving_twice_appends_nothing(machine):
    """Matrix "Approve twice"; acceptance 2's first half."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    before = _text(machine)
    code, found = machine.json("proposal", "approve", ids[0])
    assert code == EXIT_OK and found["outcome"] == "already"
    assert found["commitment"]["recorded"] is True and found["commitment"]["repaired"] is False
    assert _text(machine) == before


_RACER = r"""
import sys, json
from datetime import datetime, timezone
from pathlib import Path
from pm_ai.app.proposals import ProposalDesk
from pm_ai.app.wiring import build
root, item, go = sys.argv[1], sys.argv[2], sys.argv[3]
now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
daemon = build(Path(root), "personal", now=lambda: now)
desk = ProposalDesk(now=lambda: now, paths=daemon.storage.paths, storage=daemon.storage,
                    types=daemon.proposal_types)
while not Path(go).exists():
    pass
answer = desk.approve(item)
print(json.dumps({"outcome": answer.outcome, "recorded": answer.commitment.recorded}))
"""


def test_two_processes_approving_one_id_write_one_pending_line(tmp_path):
    """Matrix "Two processes"; acceptance 2."""
    from pm_ai.app.wiring import build

    root = tmp_path / "root"
    daemon = build(root, "personal", now=lambda: NOW)
    item = action_item_id(PERSONAL, "mtg_a", SummaryActionItem("send the cache TTL numbers"))
    daemon.storage.stage_proposal(
        Proposal(
            proposal_id=item,
            type="action_item",
            summary="send the cache TTL numbers",
            payload={"what": "send the cache TTL numbers", "owner": "Sam Rivera", "due": "by Thursday"},
            target=None,
            cites=SourceRef.parse("meeting:mtg_a"),
            created_at=NOW,
            staged_at=NOW,
            scope="personal",
            ttl=timedelta(days=14),
        )
    )
    ledger = Path(daemon.storage.paths.resolve(PERSONAL, COMMITMENTS_LOG))
    daemon.storage.close()
    go = tmp_path / "go"
    racers = [
        subprocess.Popen(
            [sys.executable, "-c", _RACER, str(root), item, str(go)],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    time.sleep(2.0)
    go.write_text("go")
    answers = [racer.communicate(timeout=60) for racer in racers]
    assert all(racer.returncode == 0 for racer in racers), answers
    found = [json.loads(out) for out, _ in answers]
    assert sorted(each["outcome"] for each in found) == ["already", "approved"]
    assert all(each["recorded"] for each in found), found
    lines = [line for line in parse_ledger(ledger.read_bytes()).lines if line.kind.value == "pending"]
    assert len(lines) == 1 and lines[0].commitment == item


def test_a_ledger_that_grew_in_between_is_read_again(machine, monkeypatch):
    """Matrix "Ledger changed in between": `LedgerChanged`, read again, appended once."""
    _, ids = _summarised(machine)
    other = render_line(
        Pending(
            replace(
                _folded_one(machine, ids[1]),
                commitment_id="prp_other_writer",
                proposal_id="prp_other_writer",
            ),
            via="prompt",
            written="approval",
            at=NOW,
        )
    )
    real = machine.storage.read_commitments
    calls = []

    def read_then_another_writer(*, scope):
        data = real(scope=scope)
        if not calls:
            path = _ledger_path(machine)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as ledger:
                ledger.write(other.text + "\n")
        calls.append(scope)
        return data

    monkeypatch.setattr(machine.storage, "read_commitments", read_then_another_writer)
    code, found = machine.json("proposal", "approve", ids[0])
    assert code == EXIT_OK and found["commitment"]["recorded"] is True
    assert len(calls) == 2
    roots = [line["commitment"] for line in _lines(machine)]
    assert roots == ["prp_other_writer", ids[0]]


def _folded_one(machine: Machine, proposal_id: str):
    """The commitment an approval of `proposal_id` would record, without writing it."""
    from pm_ai.core.commitment_log import from_approved

    body = replace(machine.load(proposal_id), state=ProposalState.APPROVED, changed_at=NOW, changed_via="prompt")
    return from_approved(body, record=None, zone=None)


def test_a_busy_ledger_leaves_the_approval_and_names_running_it_again(machine):
    """Matrix "Append fails"; then "Repair by approving again"."""
    _, ids = _summarised(machine)
    with _claim_held(machine):
        code, found = machine.json("proposal", "approve", ids[0])
    assert code == EXIT_OK and found["outcome"] == "approved"
    assert machine.load(ids[0]).state is ProposalState.APPROVED
    assert found["commitment"]["recorded"] is False and found["commitment"]["reason"]["code"] == "busy"
    assert not _text(machine)
    with _claim_held(machine):
        code, out, _ = machine.run("proposal", "approve", ids[1])
    assert code == EXIT_OK
    assert f"{ids[1]} approved; it could not be recorded as a commitment yet (the ledger was busy)." in out
    assert "Run the same command again, or pm-ai records it when the daemon next starts." in out
    # Repair by approving again.
    code, found = machine.json("proposal", "approve", ids[0])
    assert code == EXIT_OK and found["outcome"] == "already"
    assert found["commitment"]["recorded"] is True and found["commitment"]["repaired"] is True
    code, out, _ = machine.run("proposal", "approve", ids[1])
    assert "already approved, Wed 21 Oct 12:00, from the terminal; now recorded as a commitment, due Fri 23 Oct" in out
    assert sorted(line["commitment"] for line in _lines(machine)) == sorted(ids[:2])


def test_a_project_ledger_git_does_not_exclude_is_not_written(machine):
    """Matrix "Project git refuses"."""
    _, ids = _summarised(machine, scope=ALPHA)
    (machine.projects["alpha"] / ".gitignore").write_text("node_modules/\n")
    code, found = machine.json("proposal", "approve", ids[0])
    assert code == EXIT_OK and found["outcome"] == "approved"
    assert found["commitment"]["recorded"] is False
    assert found["commitment"]["reason"]["code"] == "git_not_excluded"
    assert not _ledger_path(machine, ALPHA).exists()


def test_an_edit_approve_that_fails_to_record_names_the_replacement(machine):
    """Matrix "Edit-approve fails to record"."""
    _, ids = _summarised(machine)
    with _claim_held(machine):
        code, out, _ = machine.run("proposal", "edit", ids[0], "--due", "Friday", "--approve")
    assert code == EXIT_OK
    assert machine.load(f"{ids[0]}_e1").state is ProposalState.APPROVED
    assert f"Run `pm-ai proposal approve {ids[0]}_e1 --scope personal`" in out
    assert (
        f"{ids[0]} replaced by {ids[0]}_e1, approved. It could not be recorded as a commitment "
        f"yet (the ledger was busy)." in out
    )
    assert f"{ids[0]}_e1 approved;" not in out, "approved said twice"
    assert not _text(machine)


def test_a_value_over_its_limit_is_never_recorded(machine):
    """Matrix "Field over its limit": a hand edit of the store."""
    _, ids = _summarised(machine)
    db = sqlite3.connect(machine.store)
    body = json.loads(db.execute("SELECT body FROM proposals WHERE proposal_id = ?", (ids[0],)).fetchone()[0])
    body["payload"]["what"] = "x" * 300
    db.execute("UPDATE proposals SET body = ? WHERE proposal_id = ?", (json.dumps(body), ids[0]))
    db.commit()
    db.close()
    code, out, _ = machine.run("proposal", "approve", ids[0])
    assert code == EXIT_OK and machine.load(ids[0]).state is ProposalState.APPROVED
    assert "pm-ai cannot record it." in out
    code, found = machine.json("proposal", "approve", ids[0])
    assert found["commitment"]["reason"]["code"] == "too_long"
    for _ in range(2):
        counts = _catch_up(machine).scopes["personal"]
        assert counts.too_long == 1 and counts.written == 0
    assert not _text(machine)


def test_a_long_title_is_cut_at_a_word(machine):
    """Matrix "Long title"."""
    title = " ".join(["Example Weekly Sync-up"] * 23)
    assert len(title) >= 500
    _, ids = _summarised(machine, title=title)
    machine.run("proposal", "approve", ids[0])
    (line,) = _lines(machine)
    assert line["title_cut"] == "yes" and len(line["title"]) <= 200 and title.startswith(line["title"])
    assert title[len(line["title"])] == " "


def _promise(machine: Machine, meeting: Meeting, said: str, speaker: str = "Sam Rivera") -> Proposal:
    transcript = Transcript(
        meeting_id=meeting.meeting_id,
        source=TranscriptSource.MANUAL,
        utterances=(Utterance(speaker, said, 0, None),),
    )
    (promise,) = run_transcript_ingestion(machine.daemon, transcript, meeting).staged
    return promise


def test_a_spoken_promise_records_who_said_it_and_no_owner(machine):
    """Matrix "Spoken promise"."""
    meeting = _put(machine, _meeting("mtg_p"))
    promise = _promise(machine, meeting, "I'll send the numbers by Friday")
    code, out, _ = machine.run("proposal", "show", promise.proposal_id)
    assert "  said by Sam Rivera: send the numbers, by Friday" in out
    code, _, _ = machine.run("proposal", "approve", promise.proposal_id)
    assert code == EXIT_OK
    (line,) = _lines(machine)
    assert line["type"] == "implicit_commitment" and line["speaker"] == "Sam Rivera"
    assert "owner" not in line and line["due_said"] == "Friday" and line["due"] == "2026-10-23"


def test_a_long_spoken_promise_is_cut_and_recorded(machine):
    """Matrix "Long promise" (Andrei, 2026-10-24): cut at a word, never `too_long`."""
    meeting = _put(machine, _meeting("mtg_l"))
    what = " ".join(["send the cache TTL numbers"] * 12)
    promise = _promise(machine, meeting, f"I'll {what} by Friday")
    code, found = machine.json("proposal", "approve", promise.proposal_id)
    assert code == EXIT_OK
    assert found["commitment"]["recorded"] is True and found["commitment"]["reason"] is None
    (line,) = _lines(machine)
    assert line["what_cut"] == "yes" and len(line["what"]) <= 200 and what.startswith(line["what"])
    assert machine.load(promise.proposal_id).payload["what"]["for_model"] == what
    assert _catch_up(machine).scopes["personal"].too_long == 0


def _old_promise(machine: Machine, meeting: Meeting) -> str:
    """A promise staged before 15a: its payload holds no speaker."""
    promise = _promise(machine, meeting, "I'll send the numbers by Friday")
    db = sqlite3.connect(machine.store)
    body = json.loads(db.execute("SELECT body FROM proposals WHERE proposal_id = ?", (promise.proposal_id,)).fetchone()[0])
    del body["payload"]["speaker"]
    db.execute("UPDATE proposals SET body = ? WHERE proposal_id = ?", (json.dumps(body), promise.proposal_id))
    db.commit()
    db.close()
    return promise.proposal_id


def test_an_old_promise_records_neither_speaker_nor_owner(machine):
    """Matrix "Old promise row"."""
    proposal_id = _old_promise(machine, _put(machine, _meeting("mtg_o")))
    machine.run("proposal", "approve", proposal_id)
    (line,) = _lines(machine)
    assert "speaker" not in line and "owner" not in line


def test_re_ingesting_an_old_promise_changes_nothing(machine):
    """Matrix "Re-ingestion of an old promise": no version bump, nothing dropped."""
    meeting = _put(machine, _meeting("mtg_r"))
    proposal_id = _old_promise(machine, meeting)
    transcript = Transcript(
        meeting_id=meeting.meeting_id,
        source=TranscriptSource.MANUAL,
        utterances=(Utterance("Sam Rivera", "I'll send the numbers by Friday", 0, None),),
    )
    again = run_transcript_ingestion(machine.daemon, transcript, meeting)
    stored = machine.load(proposal_id)
    assert stored.version == 1 and "speaker" not in stored.payload and again.dropped == ()
    machine.run("proposal", "approve", proposal_id)
    after = run_transcript_ingestion(machine.daemon, transcript, meeting)
    assert after.left == (proposal_id,) and after.dropped == ()


def test_a_staged_command_is_not_approved_and_writes_nothing(machine):
    """Matrix "Staged command"."""
    meeting = _put(machine, _meeting("mtg_k"))
    command = _promise(machine, meeting, "pm-ai, set_label gitlab:alpha:issue:102 urgent")
    assert command.type == "explicit_command"
    code, _, _ = machine.run("proposal", "approve", command.proposal_id)
    assert code == EXIT_REFUSAL and not _ledger_path(machine).exists()


# ── Editing ──────────────────────────────────────────────────────────────────


def test_edit_with_approve_writes_the_pending_line_from_the_replacement(machine):
    """Matrix "Edit with approve"."""
    _, ids = _summarised(machine)
    code, out, _ = machine.run("proposal", "edit", ids[0], "--due", "Friday", "--approve")
    assert code == EXIT_OK
    (line,) = _lines(machine)
    assert line["kind"] == "pending" and line["commitment"] == ids[0] and line["proposal"] == f"{ids[0]}_e1"
    assert line["due_said"] == "Friday" and line["due"] == "2026-10-23"
    assert 'It is recorded as a commitment, due Fri 23 Oct ("Friday").' in out


def test_editing_an_approved_item_writes_a_revised_line(machine):
    """Matrix "Edit an approved item": the state stays `PENDING`."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    code, found = machine.json("proposal", "edit", ids[0], "--what", "send the TTL numbers")
    assert code == EXIT_OK and found["commitment"]["state"] == "pending"
    pending, revised = _lines(machine)
    assert revised["kind"] == "revised" and revised["replaces"] == ids[0]
    assert revised["proposal"] == f"{ids[0]}_e1" and revised["what"] == "send the TTL numbers"
    assert "due" not in revised and "due_said" not in revised
    held = _folded(machine).commitments[ids[0]]
    assert held.what == "send the TTL numbers" and held.state.value == "pending"
    assert held.due.value == "2026-10-22"


def test_editing_the_due_after_approval_resolves_it_against_the_stored_day(machine):
    """Matrix "Edit the due after approval"."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    machine.run("proposal", "edit", ids[0], "--due", "Friday")
    _, revised = _lines(machine)
    assert revised["due_said"] == "Friday" and revised["due"] == "2026-10-23"


def test_editing_twice_after_approval_chains_the_revised_lines(machine):
    """Matrix "Edit twice after approval"."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    machine.run("proposal", "edit", ids[0], "--what", "first wording")
    machine.run("proposal", "edit", f"{ids[0]}_e1", "--what", "second wording")
    _, first, second = _lines(machine)
    assert first["replaces"] == ids[0] and second["replaces"] == f"{ids[0]}_e1"
    held = _folded(machine).commitments[ids[0]]
    assert held.proposal_id == f"{ids[0]}_e2" and held.what == "second wording"


def test_an_edit_to_the_same_values_writes_nothing(machine):
    """Matrix "Edit, same values"."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    before = _text(machine)
    code, found = machine.json("proposal", "edit", ids[0], "--due", "by Thursday")
    assert code == EXIT_OK and found["outcome"] == "unchanged" and found["commitment"] is None
    assert _text(machine) == before


def test_editing_a_waiting_item_writes_nothing(machine):
    """Matrix "Edit a waiting item"."""
    _, ids = _summarised(machine)
    code, _, _ = machine.run("proposal", "edit", ids[0], "--due", "Monday")
    assert code == EXIT_OK and machine.load(f"{ids[0]}_e1").state is ProposalState.STAGED
    assert not _ledger_path(machine).exists()


def test_an_expired_item_edited_and_approved_writes_the_roots_pending_line(machine):
    """Matrix "Expired item, edit-approve"."""
    _, ids = _summarised(machine)
    machine.clock = NOW + timedelta(days=15)
    code, _, _ = machine.run("proposal", "edit", ids[0], "--due", "Friday", "--approve")
    assert code == EXIT_OK
    (line,) = _lines(machine)
    assert line["commitment"] == ids[0] and line["proposal"] == f"{ids[0]}_e1"


# ── No rejection once approved ───────────────────────────────────────────────


def test_rejecting_an_approved_item_is_refused(machine):
    """Matrix "Reject after approval"; acceptance 4."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    before_row, before_ledger = machine.load(ids[0]), _text(machine)
    code, out, err = machine.run("proposal", "reject", ids[0])
    assert code == EXIT_REFUSAL
    said = out + err
    assert f"{ids[0]} was approved on Wed 21 Oct and is a commitment now, so it is not rejected; nothing was changed." in said
    # Story 15c: the refusal names the commitment commands that exist.
    assert f"If approving it was a mistake: pm-ai commitment withdrawn {ids[0]} --scope personal." in said
    assert f"If what was agreed has changed: pm-ai commitment altered {ids[0]} --scope personal --note" in said
    assert f"To correct what was recorded: pm-ai proposal edit {ids[0]} --scope personal --what" in said
    assert machine.load(ids[0]) == before_row and _text(machine) == before_ledger


def test_rejecting_in_json_names_the_commitment_commands(machine):
    """Matrix "Reject in JSON" (15a), amended by 15c: `commitment.commands` holds the three."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    code, found = machine.json("proposal", "reject", ids[0])
    assert code == EXIT_REFUSAL and found["outcome"] == "refused" and found["state"] == "approved"
    assert found["proposal"]["actions"]["edit"]["command"][:3] == ["proposal", "edit", ids[0]]
    commitment = found["commitment"]
    assert commitment["commitment_id"] == ids[0] and commitment["recorded"] is True
    assert commitment["commands"]["withdrawn"] == ["commitment", "withdrawn", ids[0], "--scope", "personal"]
    assert commitment["commands"]["altered"][:3] == ["commitment", "altered", ids[0]]
    assert commitment["commands"]["edit"][:3] == ["proposal", "edit", ids[0]]
    assert set(commitment["commands"]) == {"withdrawn", "altered", "edit"}


def test_the_store_refuses_rejecting_an_approved_item(machine):
    """Matrix "Reject at the store": `IllegalTransition`, the row unchanged."""
    _, ids = _summarised(machine)
    approved = _approve_in_store(machine, ids[0])
    for rule in (machine.daemon.proposal_types.rule("action_item"), None):
        with pytest.raises(IllegalTransition):
            machine.storage.change_proposal(
                ids[0], ProposalState.REJECTED, expected_version=approved.version, rule=rule, via="terminal"
            )
    assert machine.load(ids[0]) == approved


def test_an_approved_card_offers_edit_and_no_reject(machine):
    """Matrix "Card of an approved item"; completion offers it for no reject."""
    from pm_ai.app.proposals import completion_choices

    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    code, found = machine.json("proposal", "show", ids[0])
    reject, edit = found["proposal"]["actions"]["reject"], found["proposal"]["actions"]["edit"]
    assert reject == {
        "available": False,
        "reason": (
            "approved, so it is a commitment now; mark it withdrawn if approving it was a "
            "mistake, or edit it to correct what was agreed"
        ),
        "command": None,
    }
    assert edit["available"] is True
    offered = completion_choices("reject", now=machine.clock, paths=machine.storage.paths)
    assert ids[0] not in [each[0] for each in offered] and ids[1] in [each[0] for each in offered]


def test_rejecting_a_waiting_item_works_and_writes_nothing(machine):
    """Matrix "Reject a waiting item"."""
    _, ids = _summarised(machine)
    code, _, _ = machine.run("proposal", "reject", ids[0])
    assert code == EXIT_OK and machine.load(ids[0]).state is ProposalState.REJECTED
    assert not _ledger_path(machine).exists()


# ── The due ──────────────────────────────────────────────────────────────────


def test_each_due_as_the_acceptance_names_it(machine):
    """Acceptance 5; matrix "Due unresolved" and "No due"."""
    _, ids = _summarised(machine, FOUR)
    sentences = []
    for item in ids:
        code, out, _ = machine.run("proposal", "approve", item)
        assert code == EXIT_OK
        sentences.append(out)
    by_root = {line["commitment"]: line for line in _lines(machine)}
    assert [by_root[item]["due"] for item in ids] == ["2026-10-22", "2026-10-23", "unresolved", "none"]
    assert by_root[ids[2]]["due_said"] == "next sprint" and "due_said" not in by_root[ids[3]]
    assert 'with no due date worked out from "next sprint".' in sentences[2]
    assert "with no due date said." in sentences[3]


def test_a_due_of_only_spaces_is_nothing_said(machine):
    """Matrix "No due": only spaces."""
    _, ids = _summarised(machine, (("send the cache TTL numbers", "Sam Rivera", None),))
    db = sqlite3.connect(machine.store)
    body = json.loads(db.execute("SELECT body FROM proposals WHERE proposal_id = ?", (ids[0],)).fetchone()[0])
    body["payload"]["due"] = "   "
    db.execute("UPDATE proposals SET body = ? WHERE proposal_id = ?", (json.dumps(body), ids[0]))
    db.commit()
    db.close()
    machine.run("proposal", "approve", ids[0])
    (line,) = _lines(machine)
    assert line["due"] == "none" and "due_said" not in line


def test_the_year_is_named_when_it_differs(machine):
    """Matrix "Year in sentences"."""
    _, ids = _summarised(machine, (("plan the review", "Sam Rivera", "3 Jan 2027"),))
    code, out, _ = machine.run("proposal", "approve", ids[0])
    assert 'due Sun 3 Jan 2027 ("3 Jan 2027")' in out


# ── The meeting's day ────────────────────────────────────────────────────────


def test_an_estimated_day_marks_the_due_estimated(machine):
    """Matrix "Estimated day"; acceptance 6."""
    _, ids = _summarised(
        machine,
        (("send the cache TTL numbers", "Sam Rivera", "tomorrow"),),
        manual_only=True,
        start_estimated_from="header",
    )
    code, found = machine.json("proposal", "approve", ids[0])
    assert found["commitment"]["day_from"] == "header" and found["commitment"]["due"]["estimated"] is True
    (line,) = _lines(machine)
    assert line["day"] == "2026-10-19" and line["day_from"] == "header" and line["due"] == "2026-10-20"
    _, ids2 = _summarised(
        machine,
        (("book the review room", "Alex Morgan", "tomorrow"),),
        meeting_id="mtg_e2",
        manual_only=True,
        start_estimated_from="header",
    )
    code, out, _ = machine.run("proposal", "approve", ids2[0])
    assert 'due Tue 20 Oct (meeting day estimated) ("tomorrow")' in out


@pytest.mark.parametrize(
    "source", ["docx-created", "model-header", "header-machine-zone", "model-header-machine-zone"]
)
def test_every_estimate_source_is_recorded(machine, source):
    """Matrix "Every estimate source"."""
    _, ids = _summarised(machine, manual_only=True, start_estimated_from=source)
    code, found = machine.json("proposal", "approve", ids[0])
    assert found["commitment"]["day_from"] == source and found["commitment"]["due"]["estimated"] is True
    (line,) = _lines(machine)
    assert line["day_from"] == source


class _Records:
    """Meeting records as a reader hands them: one record, as given."""

    def __init__(self, meeting: Meeting) -> None:
        self.meeting = meeting

    def get(self, meeting_id, *, scope):
        return MeetingRecord(meeting=self.meeting)


def _approved(machine: Machine, *, due: str = "tomorrow", meeting_id: str = "mtg_z") -> Proposal:
    what = "send the cache TTL numbers"
    proposal_id = action_item_id(PERSONAL, meeting_id, SummaryActionItem(what, "Sam Rivera", due))
    machine.storage.stage_proposal(
        Proposal(
            proposal_id=proposal_id,
            type="action_item",
            summary=what,
            payload={"what": what, "owner": "Sam Rivera", "due": due},
            target=None,
            cites=SourceRef.parse(f"meeting:{meeting_id}"),
            created_at=NOW,
            staged_at=NOW,
            scope="personal",
            ttl=timedelta(days=14),
        )
    )
    return _approve_in_store(machine, proposal_id)


SUNDAY_LATE = datetime(2026, 10, 18, 22, 30, tzinfo=UTC)
ISTANBUL = ZoneInfo("Europe/Istanbul")


def test_a_machine_zone_header_takes_the_machines_day(machine):
    """Matrix "Machine-zone header": `display_timezone` unset."""
    proposal = _approved(machine)
    meeting = _meeting("mtg_z", start=SUNDAY_LATE, manual_only=True, start_estimated_from="header-machine-zone")
    found = _recorder(machine, machine_zone=ISTANBUL, meetings=_Records(meeting)).record(proposal, PERSONAL)
    assert found.recorded and found.day == date(2026, 10, 19) and found.due.value == "2026-10-20"


def test_the_display_zone_decides_the_day(machine):
    """Matrix "Display zone": 22:30 UTC Sunday is Monday in Istanbul."""
    proposal = _approved(machine)
    meeting = _meeting("mtg_z", start=SUNDAY_LATE)
    found = _recorder(machine, zone=ISTANBUL, meetings=_Records(meeting)).record(proposal, PERSONAL)
    assert found.day == date(2026, 10, 19)
    (line,) = _lines(machine)
    assert line["day"] == "2026-10-19"


def test_a_start_from_a_modification_time_gives_no_day(machine):
    """Matrix "Day from a modification time"."""
    _, ids = _summarised(machine, manual_only=True, start_estimated_from="file-modified")
    code, found = machine.json("proposal", "approve", ids[0])
    assert code == EXIT_OK and found["commitment"]["day"] is None
    (line,) = _lines(machine)
    assert "day" not in line and line["due"] == "unresolved"


def test_an_unknown_estimate_source_gives_no_day(machine):
    """Matrix "Unknown source"."""
    proposal = _approved(machine)
    meeting = _meeting("mtg_z", start_estimated_from="something-new")
    found = _recorder(machine, meetings=_Records(meeting)).record(proposal, PERSONAL)
    assert found.recorded and found.day is None and found.due.value == "unresolved"
    (line,) = _lines(machine)
    assert "day" not in line and line["due"] == "unresolved"


def test_a_meeting_record_that_cannot_be_read_still_records(machine):
    """Matrix "Record unreadable"."""
    meeting, ids = _summarised(machine)
    records = list(Path(machine.storage.paths.resolve(PERSONAL, "meetings/")).glob("*.md"))
    for record in records:
        record.write_text("this is not a meeting record\n")
    code, found = machine.json("proposal", "approve", ids[0])
    assert code == EXIT_OK and found["commitment"]["recorded"] is True and found["commitment"]["day"] is None
    (line,) = _lines(machine)
    assert "title" not in line and "start" not in line and "day" not in line
    assert line["due"] == "unresolved"


def test_a_revision_resolves_against_the_stored_day(machine):
    """Matrix "Day stored": the zone changes after the `pending` line, then the due is edited."""
    meeting = _put(machine, _meeting("mtg_s", start=SUNDAY_LATE))
    machine.summarize(meeting, (("send the cache TTL numbers", "Sam Rivera", "Thursday"),))
    item = machine.item_id(meeting, 0, (("send the cache TTL numbers", "Sam Rivera", "Thursday"),))
    _desk(machine, _recorder(machine, zone=ISTANBUL)).approve(item)
    assert _lines(machine)[0]["day"] == "2026-10-19"
    # The display zone is now unset (UTC: Sunday 18 Oct), and the due is edited.
    _desk(machine, _recorder(machine)).edit(item, changes={"due": "tomorrow"})
    _, revised = _lines(machine)
    assert revised["due"] == "2026-10-20", "resolved against the stored Monday, not Sunday"


# ── The catch-up ─────────────────────────────────────────────────────────────


def test_the_catch_up_writes_old_approvals_once(machine):
    """Matrix "Catch-up of old approvals" and "Catch-up again"; acceptance 3."""
    _, ids = _summarised(machine)
    approved = [_approve_in_store(machine, item) for item in ids]
    assert not _ledger_path(machine).exists()
    first = _catch_up(machine)
    assert first.scopes["personal"].written == 3
    lines = _lines(machine)
    assert {line["commitment"] for line in lines} == set(ids)
    for line, proposal in zip(sorted(lines, key=lambda l: l["commitment"]), sorted(approved, key=lambda p: p.proposal_id)):
        assert line["written"] == "catch_up" and line["occurred"] == proposal.changed_at.isoformat()
    before = _text(machine)
    again = _catch_up(machine)
    assert again.written == 0 and _text(machine) == before


class _Rig:
    """The daemon's writer thread and I/O pool over the machine's files (AD-5, AD-19)."""

    def __init__(self, machine: Machine) -> None:
        self.daemon = machine._build()
        self.writer = WriterThread(log=logging.getLogger(DAEMON_LOGGER), step_scope=self.daemon.storage.claimed_step)
        self.writer.start()
        self.writer.call(self.daemon.storage.adopt_daemon_connection, label="adopt")
        self.pool = IOPool()
        self.pool.start()
        self.path = RequestPath(
            daemon=self.daemon, writer=self.writer, pool=self.pool, diagnose=lambda: Report(()), bounds=DAEMON_BOUNDS
        )

    def close(self) -> None:
        self.writer.close()
        self.pool.shutdown()
        self.daemon.storage.close()


def _start(rig: _Rig, machine: Machine) -> CommitmentCatchUp:
    """A daemon start through `_lifespan`: its one-shot catch-up task runs, once."""
    catch = CommitmentCatchUp(requests=rig.path, now=lambda: machine.clock)
    app = types.SimpleNamespace(state=types.SimpleNamespace(pm_ai_requests=rig.path, pm_ai_commitments=catch))

    async def go() -> None:
        async with daemon_module._lifespan(app):  # noqa: SLF001
            for _ in range(500):
                if catch.ran:
                    break
                await asyncio.sleep(0.01)

    asyncio.run(go())
    return catch


def test_the_catch_up_runs_from_the_daemons_start(machine):
    """Matrix "Catch-up runs from start"; acceptance 2's daemon starts twice."""
    _, ids = _summarised(machine)
    for item in ids:
        _approve_in_store(machine, item)
    rig = _Rig(machine)
    try:
        first = _start(rig, machine)
        assert first.ran == 1 and first.last is not None and first.last.written == 3
        assert len(_lines(machine)) == 3
        second = _start(rig, machine)
        assert second.ran == 1 and second.last is not None and second.last.written == 0
        assert len(_lines(machine)) == 3
    finally:
        rig.close()


def test_the_serve_paths_wiring_sets_a_working_catch_up(machine):
    """The state `_serve_locked` sets (`attach_background`) holds a catch-up that writes lines."""
    _, ids = _summarised(machine)
    for item in ids:
        _approve_in_store(machine, item)
    rig = _Rig(machine)
    try:
        state = types.SimpleNamespace()
        daemon_module.attach_background(state, rig.path, now=lambda: machine.clock)
        assert state.pm_ai_requests is rig.path and state.pm_ai_sweep is not None
        catch = state.pm_ai_commitments
        assert isinstance(catch, CommitmentCatchUp)
        # Through `_lifespan`, with only the request path and the catch-up attached.
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(pm_ai_requests=rig.path, pm_ai_commitments=catch)
        )

        async def go() -> None:
            async with daemon_module._lifespan(app):  # noqa: SLF001
                for _ in range(500):
                    if catch.ran:
                        break
                    await asyncio.sleep(0.01)

        asyncio.run(go())
        assert catch.ran == 1 and catch.last is not None and catch.last.written == 3
    finally:
        rig.close()
    assert {line["commitment"] for line in _lines(machine)} == set(ids)


def test_the_catch_up_writes_a_projects_lines_to_its_own_ledger(machine):
    """A catch-up in the enrolled project: its ledger only, never the personal one."""
    _, ids = _summarised(machine, scope=ALPHA)
    _approve_in_store(machine, ids[0])
    result = _catch_up(machine)
    assert result.scopes["project:alpha"].written == 1
    assert [line["commitment"] for line in _lines(machine, ALPHA)] == [ids[0]]
    assert _ledger_path(machine, ALPHA) == machine.projects["alpha"] / ".project-ai" / "memory" / COMMITMENTS_LOG
    assert not _ledger_path(machine).exists()


def test_the_catch_up_leaves_a_project_git_does_not_exclude(machine):
    _, ids = _summarised(machine, scope=ALPHA)
    _approve_in_store(machine, ids[0])
    (machine.projects["alpha"] / ".gitignore").write_text("node_modules/\n")
    counts = _catch_up(machine).scopes["project:alpha"]
    assert counts.unvisited == 1 and counts.written == 0
    assert counts.unvisited_why == "git does not exclude the project's memory/"
    assert not _ledger_path(machine, ALPHA).exists()


def _racing_step(machine: Machine, racing: str):
    """A `step` that appends `racing` to the ledger before the claimed step runs, once."""
    raced: list[bool] = []

    def step(work):
        if not raced:
            raced.append(True)
            path = _ledger_path(machine)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as ledger:
                ledger.write(racing + "\n")
        return work()

    return step, raced


def test_a_racing_line_makes_the_catch_up_read_again(machine, monkeypatch):
    """`LedgerChanged` inside the catch-up: read again, each line written once."""
    _, ids = _summarised(machine)
    for item in ids[:2]:
        _approve_in_store(machine, item)
    foreign = render_line(
        Pending(replace(_folded_one(machine, ids[2]), commitment_id="prp_racer", proposal_id="prp_racer"),
                via="prompt", written="approval", at=NOW)
    )
    reads: list[int] = []
    real = machine.storage.read_commitments

    def counted(*, scope):
        reads.append(1)
        return real(scope=scope)

    monkeypatch.setattr(machine.storage, "read_commitments", counted)
    step, raced = _racing_step(machine, foreign.text)
    counts = _catch_up(machine, step=step).scopes["personal"]
    assert raced and counts.written == 2 and counts.failed is None
    assert len(reads) >= 2, "the ledger was not read again"
    roots = [line["commitment"] for line in _lines(machine)]
    assert sorted(roots) == sorted(["prp_racer", *ids[:2]]) and len(roots) == 3


def test_a_racing_line_with_the_same_entry_is_not_written_twice(machine):
    _, ids = _summarised(machine)
    approved = _approve_in_store(machine, ids[0])
    from pm_ai.core.commitment_log import from_approved

    racer = render_line(
        Pending(from_approved(approved, record=None, zone=None), via="prompt", written="approval", at=NOW)
    )
    step, raced = _racing_step(machine, racer.text)
    counts = _catch_up(machine, step=step).scopes["personal"]
    assert raced and counts.written == 0
    (line,) = _lines(machine)
    assert line["commitment"] == ids[0] and line["written"] == "approval"


def test_one_scopes_failed_append_stops_no_other(machine):
    """Per-scope isolation: personal's append raises, the project's is still written."""
    from pm_ai.domain.claims import ClaimReentered

    _, personal_ids = _summarised(machine)
    _approve_in_store(machine, personal_ids[0])
    _, project_ids = _summarised(machine, scope=ALPHA, meeting_id="mtg_alpha")
    _approve_in_store(machine, project_ids[0])
    calls: list[int] = []

    def step(work):
        calls.append(1)
        if len(calls) == 1:
            raise ClaimReentered("a claim entered twice on one thread")
        return work()

    result = _catch_up(machine, step=step)
    assert result.scopes["personal"].failed == "ClaimReentered" and result.scopes["personal"].written == 0
    assert result.scopes["project:alpha"].written == 1
    assert not _ledger_path(machine).exists()
    assert [line["commitment"] for line in _lines(machine, ALPHA)] == [project_ids[0]]


def test_a_speaker_label_over_80_characters_is_not_stored(machine):
    meeting = _put(machine, _meeting("mtg_long"))
    promise = _promise(machine, meeting, "I'll send the numbers by Friday", speaker="S" * 81)
    assert "speaker" not in machine.load(promise.proposal_id).payload
    code, found = machine.json("proposal", "approve", promise.proposal_id)
    assert code == EXIT_OK and found["commitment"]["recorded"] is True
    (line,) = _lines(machine)
    assert "speaker" not in line and "owner" not in line


def test_a_speaker_label_with_line_breaks_is_folded(machine):
    meeting = _put(machine, _meeting("mtg_fold"))
    promise = _promise(machine, meeting, "I'll send the numbers by Friday", speaker="Sam\nRivera​")
    stored = machine.load(promise.proposal_id).payload["speaker"]
    assert stored["raw"] == "Sam\nRivera​" and stored["for_model"] == "Sam Rivera"
    code, found = machine.json("proposal", "approve", promise.proposal_id)
    assert code == EXIT_OK and found["commitment"]["recorded"] is True
    (line,) = _lines(machine)
    assert line["speaker"] == "Sam Rivera"


def test_an_edit_after_approval_says_its_commitment_holds_the_new_values(machine):
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    code, out, _ = machine.run("proposal", "edit", ids[0], "--what", "send the TTL numbers")
    assert code == EXIT_OK
    assert (
        f'{ids[0]} replaced by {ids[0]}_e1. Its commitment now holds the edited values, '
        f'due Thu 22 Oct ("by Thursday").'
    ) in out


def test_the_catch_up_places_an_old_row_without_a_scope(machine):
    """Matrix "Catch-up, old row without scope": a NULL scope column, placed by `scope_of`."""
    _, ids = _summarised(machine)
    _approve_in_store(machine, ids[0])
    db = sqlite3.connect(machine.store)
    body = json.loads(db.execute("SELECT body FROM proposals WHERE proposal_id = ?", (ids[0],)).fetchone()[0])
    body.pop("scope", None)
    db.execute("UPDATE proposals SET body = ?, scope = NULL WHERE proposal_id = ?", (json.dumps(body), ids[0]))
    db.commit()
    db.close()
    assert _catch_up(machine).scopes["personal"].written == 1
    assert [line["commitment"] for line in _lines(machine)] == [ids[0]]


def test_the_catch_up_writes_a_lost_revised_line(machine):
    """Matrix "Catch-up after a lost edit line"."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    current = machine.load(ids[0])
    machine.storage.edit_proposal(
        ids[0],
        expected_version=current.version,
        payload={**current.payload, "what": "send the TTL numbers"},
        summary="send the TTL numbers",
        approve=False,
        rule=machine.daemon.proposal_types.rule("action_item"),
        via="terminal",
    )
    assert len(_lines(machine)) == 1
    assert _catch_up(machine).scopes["personal"].written == 1
    _, revised = _lines(machine)
    assert revised["kind"] == "revised" and revised["proposal"] == f"{ids[0]}_e1" and revised["written"] == "catch_up"


def test_the_catch_up_leaves_out_a_proposal_that_changed_meanwhile(machine):
    """Matrix "Catch-up, superseded meanwhile": checked again inside the claimed step."""
    _, ids = _summarised(machine)
    _approve_in_store(machine, ids[0])
    _approve_in_store(machine, ids[1])

    def step(work):
        current = machine.load(ids[0])
        machine.storage.edit_proposal(
            ids[0],
            expected_version=current.version,
            payload={**current.payload, "what": "changed meanwhile"},
            summary="changed meanwhile",
            approve=False,
            rule=machine.daemon.proposal_types.rule("action_item"),
            via="prompt",
        )
        return work()

    counts = _catch_up(machine, step=step).scopes["personal"]
    assert counts.written == 1 and counts.left_out == 1
    assert [line["commitment"] for line in _lines(machine)] == [ids[1]]


def test_the_catch_up_counts_a_row_with_no_change_time(machine):
    """Matrix "Catch-up, no change time"."""
    _, ids = _summarised(machine)
    _approve_in_store(machine, ids[0])
    db = sqlite3.connect(machine.store)
    body = json.loads(db.execute("SELECT body FROM proposals WHERE proposal_id = ?", (ids[0],)).fetchone()[0])
    body.update(changed_at=None, changed_via=None)
    db.execute("UPDATE proposals SET body = ?, changed_at = NULL WHERE proposal_id = ?", (json.dumps(body), ids[0]))
    db.commit()
    db.close()
    counts = _catch_up(machine).scopes["personal"]
    assert counts.no_change_time == 1 and counts.written == 0 and not _ledger_path(machine).exists()


def test_the_catch_up_counts_rows_in_a_scope_it_cannot_visit(machine):
    """Matrix "Catch-up, unvisited scope": a project no longer enrolled."""
    what = "send the cache TTL numbers"
    beta = DataScope(ScopeKind.PROJECT, "beta")
    proposal_id = action_item_id(beta, "mtg_b", SummaryActionItem(what))
    machine.storage.stage_proposal(
        Proposal(
            proposal_id=proposal_id,
            type="action_item",
            summary=what,
            payload={"what": what, "owner": None, "due": None},
            target=None,
            cites=SourceRef.parse("meeting:mtg_b"),
            created_at=NOW,
            staged_at=NOW,
            scope="project:beta",
            ttl=timedelta(days=14),
        )
    )
    _approve_in_store(machine, proposal_id)
    result = _catch_up(machine)
    assert result.scopes["project:beta"].unvisited == 1 and result.written == 0


def test_the_catch_up_flags_a_pending_line_whose_proposal_is_gone(machine, caplog):
    """Matrix "Store lost": flagged by root in the log; nothing written."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    before = _text(machine)
    db = sqlite3.connect(machine.store)
    db.execute("DELETE FROM proposals WHERE proposal_id = ?", (ids[0],))
    db.commit()
    db.close()
    caplog.set_level(logging.INFO, logger="pm_ai.daemon")
    counts = _catch_up(machine).scopes["personal"]
    assert counts.flagged == (ids[0],) and counts.written == 0
    assert _text(machine) == before
    assert any(ids[0] in record.getMessage() for record in caplog.records)


def test_a_malformed_line_naming_the_root_blocks_its_pending_line(machine):
    """Matrix "Malformed line names a root"."""
    _, ids = _summarised(machine)
    path = _ledger_path(machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"- entry=cml_0123456789ab commitment={ids[0]} kind=pending what=hand-edited\n")
    code, found = machine.json("proposal", "approve", ids[0])
    assert code == EXIT_OK and found["commitment"]["reason"]["code"] == "ledger_malformed"
    assert len(path.read_text().splitlines()) == 1
    # Another root is recorded as usual.
    machine.run("proposal", "approve", ids[1])
    assert [line["commitment"] for line in _lines(machine)] == [ids[1]]


def test_the_next_append_after_a_torn_tail_starts_a_new_line(machine):
    """Matrix "Torn last line": the next append starts on a new line."""
    _, ids = _summarised(machine)
    path = _ledger_path(machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("- entry=cml_0123456789ab commitment=prp_torn kind=pend")
    machine.run("proposal", "approve", ids[0])
    text = path.read_text()
    assert text.startswith("- entry=cml_0123456789ab commitment=prp_torn kind=pend\n- entry=")
    assert [line["commitment"] for line in _lines(machine)] == [ids[0]]


def test_a_whole_file_write_of_the_personal_ledger_is_refused(machine):
    """Matrix "Whole-file write"."""
    from pm_ai.storage.service import AppendOnlyArtifact

    with pytest.raises(AppendOnlyArtifact):
        machine.storage.write_artifact(b"- entry=x\n", scope=PERSONAL, artifact=COMMITMENTS_LOG)


def test_a_people_scope_is_refused_by_name(machine):
    """Matrix "People scope"."""
    from pm_ai.domain.commitments import NotACommitmentScope

    people = DataScope(ScopeKind.PEOPLE, person_id="example")
    with pytest.raises(NotACommitmentScope, match="people:example"):
        machine.storage.append_commitments(["- entry=cml_0123456789ab"], scope=people, after=0)
    assert not list(machine.home.rglob(COMMITMENTS_LOG))


# ── At the prompt ────────────────────────────────────────────────────────────


def test_a_project_approval_over_a_large_ledger_answers_within_its_bound(machine):
    """Matrix "Large project ledger": 5,000 lines, `git`, one `LedgerChanged`, under 2 s."""
    _, ids = _summarised(machine, scope=ALPHA)
    template = _folded_one(machine, ids[1])
    filler = "".join(
        render_line(
            Pending(
                replace(template, commitment_id=f"prp_filler_{n:05d}", proposal_id=f"prp_filler_{n:05d}"),
                via="terminal",
                written="approval",
                at=NOW,
            )
        ).text
        + "\n"
        for n in range(5000)
    )
    path = _ledger_path(machine, ALPHA)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(filler)
    rig = _Rig(machine)
    try:
        raw = rig.daemon.storage
        real = raw.read_commitments
        reads: list[int] = []

        def read_then_another_writer(*, scope):
            data = real(scope=scope)
            if not reads:
                with open(path, "a", encoding="utf-8") as ledger:
                    ledger.write(filler.splitlines()[0].replace("prp_filler_00000", "prp_filler_late") + "\n")
            reads.append(len(data))
            return data

        raw.read_commitments = read_then_another_writer  # type: ignore[method-assign]
        desk = rig.path._proposals(rig.daemon, None)  # noqa: SLF001 — the prompt's own desk
        started = time.monotonic()
        answer = desk.approve(ids[0])
        took = time.monotonic() - started
        assert answer.commitment is not None and answer.commitment.recorded, answer.sentence
        assert len(reads) == 2, "the ledger was not read again after it grew"
        assert took < PROPOSAL_WRITE_BOUND_SECONDS, f"{took:.2f} s"
    finally:
        rig.close()
    lines = path.read_text().splitlines()
    assert len(lines) == 5002 and ids[0] in lines[-1]


# ── Team Activity ────────────────────────────────────────────────────────────


def _work() -> ProjectWork:
    read = NOW - timedelta(hours=1)
    return ProjectWork(
        scope=ALPHA,
        instance="gitlab:alpha",
        issues=(
            Issue(
                iid=142,
                title="Issue 142",
                state="opened",
                assignees=(),
                milestone_title=None,
                milestone_due=None,
                labels=(),
                updated_at=NOW - timedelta(days=2),
                closed_at=None,
                web_url=None,
                confidential=False,
            ),
        ),
        sections=tuple(SectionStatus(section, complete=True, reason=None, last_complete=read) for section in Section),
    )


def _team_activity_rows(machine: Machine, meeting: Meeting, due: str) -> str:
    machine.daemon.meetings.put(meeting)
    lines = lines_to_append(
        parse_updates(None),
        meeting,
        links=(Link(Candidate(ItemKind.ACTION, f"prp_{meeting.meeting_id}", "fix the cache", "Sam Rivera", due), 142, LinkKind.NUMBER),),
        at=NOW,
    )
    held = machine.storage.read_member_updates(scope=ALPHA)
    machine.storage.append_member_updates(list(lines), scope=ALPHA, after=len(held))
    notes = _issue_notes(
        machine.daemon,
        scope=ALPHA,
        work=_work(),
        entries=(),
        now=NOW,
        since=LookBack(NOW - timedelta(days=1), LookBackKind.SINCE_LAST_RENDER, NOW - timedelta(days=1)),
        zone=UTC,
        aliases=parse_aliases(None),
    )
    return "\n".join(notes.rows.get(142, ()))


def test_team_activity_shows_a_newly_resolved_due(machine):
    """Matrix "Team Activity due": `tomorrow` now shows its date."""
    rows = _team_activity_rows(machine, _meeting("mtg_t", scope=ALPHA), "tomorrow")
    assert "due as said in the meeting: tomorrow (2026-10-20)" in rows


def test_team_activity_shows_no_date_for_a_modification_time(machine):
    """Matrix "Team Activity, modified time"."""
    meeting = _meeting("mtg_f", scope=ALPHA, manual_only=True, start_estimated_from="file-modified")
    rows = _team_activity_rows(machine, meeting, "tomorrow")
    assert "due as said in the meeting: tomorrow" in rows and "(2026-10-20)" not in rows


def test_team_activity_marks_an_estimated_day(machine):
    meeting = _meeting("mtg_h", scope=ALPHA, manual_only=True, start_estimated_from="header")
    rows = _team_activity_rows(machine, meeting, "tomorrow")
    assert "tomorrow (2026-10-20) (meeting day estimated)" in rows


def test_the_entry_ids_are_the_lines_identity(machine):
    """Written once by the entry id: the approval's line and a catch-up's share it."""
    _, ids = _summarised(machine)
    machine.run("proposal", "approve", ids[0])
    (line,) = _lines(machine)
    assert line["entry"] == entry_id((ids[0], "pending"))
