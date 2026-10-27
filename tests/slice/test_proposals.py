"""Story 13a — staged proposals approved, edited or rejected, and the approval queue.

One test per row of the story's I/O matrix, plus two processes approving one
id and a timed listing over 5,000 proposals. The expiry sweep's rows are in
`test_proposal_sweep.py`.

Every row runs against a temporary home with `alpha` enrolled at a real git
repository: the meeting records, the operational store and `projects.toml`
are the real ones, and the store is written with its own calls. The clock is
the test's. A summary is made by a fake model answering from a list, so no
test reaches a model. Names are `11d`'s fixture names and invented ones; no
real meeting, person or item text is used.
"""

from __future__ import annotations

import io
import json
import shlex
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_meeting_summary_slice import FakeModel
from test_transcript_drop import Keychain
from pm_ai.app import entry
from pm_ai.app.pipelines import (
    SummaryJobs,
    note_summary_input,
    prepare_dashboard,
    run_transcript_ingestion,
    summarize_meeting,
)
from pm_ai.app.proposals import (
    PROPOSAL_WRITE_BOUND_SECONDS,
    ProposalDesk,
    completion_choices,
)
from pm_ai.app.wiring import build, onboard_project
from pm_ai.core.config import Config
from pm_ai.core.meeting_records import capture_name
from pm_ai.core.meeting_summary import action_item_id, action_item_prefix
from pm_ai.core.proposal_cards import ProposalRefused
from pm_ai.domain.identity import Actor, DataScope, ScopeKind, SourceRef
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.domain.meeting_summaries import SummaryActionItem
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.proposals import Proposal
from pm_ai.domain.storage_tiers import OPERATIONAL_DB
from pm_ai.domain.transcripts import (
    CaptureFormat,
    Transcript,
    TranscriptSource,
    TranscriptStateKind,
    Utterance,
)
from pm_ai.platform.paths import ScopePaths
from pm_ai.surfaces.cli import dispatch as cli
from pm_ai.surfaces.cli.completion import Completion, Readers
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, EXIT_USAGE, dispatch

REPO_ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
START = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
PERSONAL = DataScope(ScopeKind.PERSONAL)
APPLICATION = DataScope(ScopeKind.APPLICATION)
ALPHA = DataScope(ScopeKind.PROJECT, "alpha")
TITLE = "Example Weekly Sync-up"
ITEMS = (
    ("send the cache TTL numbers", "Sam Rivera", "Thursday"),
    ("book the review room", "Alex Morgan", None),
    ("draft the release notes", None, "Friday"),
)


def _git(repository: Path, *words: str) -> None:
    subprocess.run(["git", *words], cwd=repository, check=True, capture_output=True)


def _answer(items=ITEMS) -> str:
    return json.dumps(
        {
            "summary": "The team planned the week.",
            "decisions": [],
            "action_items": [{"what": w, "owner": o, "due": d} for w, o, d in items],
        },
        ensure_ascii=False,
    )


class Machine:
    """A home with `alpha` enrolled, the test's clock, and a daemon over the real files."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir()
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.chdir(tmp_path)
        self.keychain = Keychain()
        monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: self.keychain)
        self.projects: dict[str, Path] = {}
        self.enrol("alpha")
        self.clock = NOW
        self.daemon = self._build()

    def enrol(self, name: str) -> None:
        repository = self.tmp / name
        repository.mkdir()
        for words in (("init", "-q"), ("config", "user.email", "t@t"), ("config", "user.name", "t")):
            _git(repository, *words)
        onboard_project(self.keychain, str(repository), name)
        self.projects[name] = repository
        if hasattr(self, "daemon"):
            self.daemon = self._build()

    def _build(self):
        paths = ScopePaths.production(projects=dict(self.projects))
        daemon = build(
            None,
            "alpha",
            paths=paths,
            keychain=self.keychain,
            now=lambda: self.clock,
            watched=tuple(self.projects),
            config=Config(display_timezone="UTC"),
        )
        daemon.model = FakeModel()  # type: ignore[assignment]
        daemon.summaries = SummaryJobs()
        return daemon

    @property
    def storage(self):
        return self.daemon.storage

    @property
    def store(self) -> Path:
        return Path(self.storage.paths.resolve(APPLICATION, OPERATIONAL_DB))

    def desk(self, via: str = "terminal") -> ProposalDesk:
        return ProposalDesk(
            now=lambda: self.clock,
            paths=self.storage.paths,
            storage=self.storage,
            via=via,
            types=self.daemon.proposal_types,
        )

    def meeting(
        self,
        meeting_id: str = "mtg_a",
        *,
        scope: DataScope = PERSONAL,
        title: str = TITLE,
        start: datetime = START,
    ) -> Meeting:
        meeting = Meeting(
            meeting_id=meeting_id,
            title=title,
            start=start,
            duration_minutes=30,
            attendees=(Actor("email:alex@example.com"),),
            scope=scope,
        )
        self.daemon.meetings.put(meeting)
        self.storage.write_capture(
            "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n<v Alex Morgan>we ship on Friday\n\n"
            "00:00:06.000 --> 00:00:09.000\n<v Sam Rivera>I will send the cache numbers\n",
            scope=scope,
            name=capture_name(meeting_id, start, CaptureFormat.GRAPH),
        )
        self.storage.record_transcript_state(
            scope, meeting_id, TranscriptStateKind.INGESTED, "ingested", retryable=False
        )
        note_summary_input(self.daemon, scope, meeting_id, ingested_now=False)
        return meeting

    def summarize(self, meeting: Meeting, items=ITEMS, *, again: bool = False):
        self.daemon.model.answers.append(_answer(items))
        fence = None
        if again:
            fence = self.storage.request_summary(
                meeting.scope, meeting.meeting_id, meeting_start=meeting.start, reason="again"
            )
        return summarize_meeting(
            self.daemon, meeting.scope, meeting.meeting_id, job="j", fence=fence
        )

    def item_id(self, meeting: Meeting, index: int = 0, items=ITEMS) -> str:
        what, owner, due = items[index]
        return action_item_id(meeting.scope, meeting.meeting_id, SummaryActionItem(what, owner, due))

    def run(self, *words: str, desk: ProposalDesk | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = dispatch(
            list(words),
            daemon=self.daemon,
            diagnose=lambda: None,  # type: ignore[arg-type, return-value]
            probe_connectors=None,
            proposals=desk if desk is not None else self.desk(),
            out=out,
            err=err,
            terminal=False,
        )
        return code, out.getvalue(), err.getvalue()

    def json(self, *words: str, desk: ProposalDesk | None = None) -> tuple[int, dict]:
        code, out, err = self.run(*words, "--json", desk=desk)
        assert out, err
        return code, json.loads(out)

    def load(self, proposal_id: str) -> Proposal:
        return self.storage.load_proposal(proposal_id)

    def page(self, scope: DataScope = PERSONAL) -> str:
        return prepare_dashboard(self.daemon, scope=scope, now=self.clock).decode("utf-8")


@pytest.fixture
def machine(tmp_path, monkeypatch) -> Machine:
    return Machine(tmp_path, monkeypatch)


@pytest.fixture
def summarised(machine) -> tuple[Machine, Meeting]:
    """A personal meeting summarised with three action items staged."""
    meeting = machine.meeting()
    machine.summarize(meeting)
    return machine, meeting


def _command(card: dict, action: str) -> list[str]:
    return card["actions"][action]["command"]


# ── Listing ──────────────────────────────────────────────────────────────────


def test_waiting_items_are_listed_in_the_summarys_order(summarised):
    """Matrix "Waiting items listed"."""
    machine, meeting = summarised
    code, out, _ = machine.run("proposal", "list")
    assert code == EXIT_OK
    headings = [line for line in out.splitlines() if line.startswith("Action item")]
    assert headings == ["Action item, waiting for you until Wed 4 Nov"] * 3
    order = [out.index(what) for what, _, _ in ITEMS]
    assert order == sorted(order)
    assert "  Sam Rivera: send the cache TTL numbers (Thursday)" in out
    assert f"  from {TITLE}, Wed 21 Oct 10:00, personal" in out
    assert out.rstrip().splitlines()[-1] == "next: pm-ai proposal review --scope personal"


def test_the_json_listing_is_one_object_with_runnable_commands(summarised):
    """Matrix "JSON listing"; acceptance 1: printed as it runs, nothing written."""
    machine, meeting = summarised
    before = machine.store.read_bytes()
    code, found = machine.json("proposal", "list")
    assert code == EXIT_OK and found["format"] == 1
    assert len(found["proposals"]) == 3
    for card in found["proposals"]:
        words = _command(card, "approve")
        assert words[:2] == ["proposal", "approve"]
        assert words[words.index("--scope") + 1] == "personal"
        assert words[words.index("--if-version") + 1] == "1"
        assert card["state"] == "staged" and card["version"] == 1
        assert card["meeting"]["title"] == TITLE and card["meeting"]["record"] == "read"
    assert found["next"]["review"] == ["proposal", "review", "--scope", "personal"]
    assert machine.store.read_bytes() == before
    # The printed command runs as printed.
    first = found["proposals"][0]
    code, out, _ = machine.run(*_command(first, "approve"))
    assert code == EXIT_OK and "approved" in out


def test_the_listing_at_a_terminal_composes_nothing(summarised, monkeypatch, capsys):
    """Acceptance 1 through `main()`: no daemon, no keychain, nothing written."""
    machine, _ = summarised

    def forbidden(*_a, **_k):
        raise AssertionError("the listing composed a daemon or asked the keychain")

    monkeypatch.setattr(entry, "MacOSKeychainAdapter", forbidden)
    monkeypatch.setattr(entry, "_compose", forbidden)
    monkeypatch.setattr(
        entry, "_proposal_reader", lambda: ProposalDesk(now=lambda: machine.clock)
    )
    before = machine.store.read_bytes()
    assert entry.main(["proposal", "list", "--json"]) == EXIT_OK
    found = json.loads(capsys.readouterr().out)
    assert len(found["proposals"]) == 3
    assert machine.store.read_bytes() == before


# ── Approve and reject ───────────────────────────────────────────────────────


def test_approve_prints_the_approved_card_and_the_dashboard_shows_it(summarised):
    """Matrix "Approve"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    code, out, _ = machine.run("proposal", "approve", item)
    assert code == EXIT_OK
    assert out.startswith("Action item, approved on Wed 21 Oct")
    stored = machine.load(item)
    assert stored.state is ProposalState.APPROVED and stored.changed_via == "terminal"
    assert stored.version == 2
    # Story 15d: a block line, and one line under its meeting counting it.
    page = machine.page()
    assert "  - Sam Rivera: send the cache TTL numbers (Thursday), approved" not in page
    assert "Sam Rivera: send the cache TTL numbers" in page
    block = [line for line in page.split("\n") if line.startswith("- **") and "send the cache TTL numbers" in line]
    assert len(block) == 1
    assert (
        "  - 1 commitment from this meeting, open: `pm-ai commitment list --scope personal "
        f"--meeting {meeting.meeting_id} --state all`"
    ) in page


def test_approving_twice_is_already_approved(summarised):
    """Matrix "Approve twice"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.run("proposal", "approve", item)
    code, out, _ = machine.run("proposal", "approve", item)
    assert code == EXIT_OK
    assert "already approved, Wed 21 Oct 12:00, from the terminal" in out
    assert machine.load(item).version == 2


def test_approving_twice_with_a_version_is_already_not_a_mismatch(summarised):
    """Matrix "Approve twice with a version"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    assert machine.run("proposal", "approve", item, "--if-version", "1")[0] == EXIT_OK
    code, found = machine.json("proposal", "approve", item, "--if-version", "1")
    assert code == EXIT_OK and found["outcome"] == "already"


def test_two_surfaces_change_it_once(summarised):
    """Matrix "Two surfaces": the prompt approved first; the terminal is told."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    assert machine.run("proposal", "approve", item, desk=machine.desk("prompt"))[0] == EXIT_OK
    code, out, _ = machine.run("proposal", "approve", item)
    assert code == EXIT_OK and "already approved" in out and "from the prompt" in out
    assert machine.load(item).version == 2


_RACER = r"""
import sys, json
from datetime import datetime, timezone
from pathlib import Path
from pm_ai.app.proposals import ProposalDesk
from pm_ai.app.wiring import build
from pm_ai.platform.paths import ScopePaths
root, item, go = sys.argv[1], sys.argv[2], sys.argv[3]
now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
daemon = build(Path(root), "personal", now=lambda: now)
desk = ProposalDesk(now=lambda: now, paths=daemon.storage.paths, storage=daemon.storage,
                    types=daemon.proposal_types)
import time
while not Path(go).exists():
    pass
started = time.monotonic()
outcome = desk.approve(item).outcome
print(json.dumps({"outcome": outcome, "seconds": time.monotonic() - started}))
"""


def test_two_processes_approving_one_id_change_it_once(tmp_path):
    """Matrix "Two processes"; acceptance 2: once, version + 1, both exit 0 in time."""
    root = tmp_path / "root"
    daemon = build(root, "personal", now=lambda: NOW)
    item = action_item_id(PERSONAL, "mtg_a", SummaryActionItem("send the cache TTL numbers"))
    daemon.storage.stage_proposal(
        Proposal(
            proposal_id=item,
            type="action_item",
            summary="send the cache TTL numbers",
            payload={"what": "send the cache TTL numbers", "owner": None, "due": None},
            target=None,
            cites=SourceRef.parse("meeting:mtg_a"),
            created_at=NOW,
            staged_at=NOW,
            scope="personal",
            ttl=timedelta(days=14),
        )
    )
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
    for each in found:
        # Each answered within its own bound, measured around the approve itself.
        assert each["seconds"] < PROPOSAL_WRITE_BOUND_SECONDS, each
    reopened = build(root, "personal", now=lambda: NOW)
    assert reopened.storage.load_proposal(item).version == 2


def test_reject_hides_the_item_from_the_dashboard(summarised):
    """Matrix "Reject" (Team Activity's half is in the Team Activity rows)."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    code, out, _ = machine.run("proposal", "reject", item)
    assert code == EXIT_OK and out.startswith("Action item, rejected on")
    assert machine.load(item).state is ProposalState.REJECTED
    assert "send the cache TTL numbers" not in machine.page()


def test_an_approved_action_item_is_not_rejected(summarised):
    """Matrix "Reject an approved action item", as story 15a changed it.

    13a let the PM reject an approved action item; since 15a approval writes
    its commitment, so the reject is refused and points to `proposal edit`.
    """
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.run("proposal", "approve", item)
    code, out, err = machine.run("proposal", "reject", item)
    assert code == EXIT_REFUSAL and machine.load(item).state is ProposalState.APPROVED
    assert "is a commitment now, so it is not rejected" in out + err
    assert f"pm-ai proposal edit {item} --scope personal" in out + err


def test_an_old_row_already_approved_drops_the_when_and_where(summarised):
    """Matrix "Old row, no change time"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    db = sqlite3.connect(machine.store)
    body = json.loads(db.execute("SELECT body FROM proposals WHERE proposal_id = ?", (item,)).fetchone()[0])
    body.update(state="approved", version=2, changed_at=None, changed_via=None)
    db.execute(
        "UPDATE proposals SET body = ?, state = 'approved', version = 2, changed_at = NULL "
        "WHERE proposal_id = ?",
        (json.dumps(body), item),
    )
    db.commit()
    db.close()
    code, out, _ = machine.run("proposal", "approve", item)
    assert code == EXIT_OK
    # Since 15a the already-approved sentence also says the commitment cannot
    # be recorded: the row records no approval instant (the catch-up skips it too).
    assert f"{item}: already approved; it could not be recorded as a commitment" in out
    assert "pm-ai cannot record it." in out


# ── The window ───────────────────────────────────────────────────────────────


def test_past_the_window_with_no_daemon_reads_as_expired_and_is_refused(summarised):
    """Matrix "Past the window, no daemon"; acceptance 3's first half."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.clock = NOW + timedelta(days=15)
    code, found = machine.json("proposal", "list", "--state", "expired")
    assert code == EXIT_OK and {card["state"] for card in found["proposals"]} == {"expired"}
    assert len(found["proposals"]) == 3
    before = machine.load(item)
    for verb in ("approve", "reject"):
        code, out, err = machine.run("proposal", verb, item)
        assert code == EXIT_REFUSAL and "expired on Wed 4 Nov, not reviewed" in err
    assert machine.load(item) == before


def test_approve_after_the_sweep_is_refused_and_offers_edit(summarised):
    """Matrix "Approve vs sweep": the sweep committed first."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.clock = NOW + timedelta(days=15)
    scan = machine.storage.overdue_proposals(rules=machine.daemon.proposal_types.rules())
    machine.storage.expire_overdue(scan.candidates, rules=machine.daemon.proposal_types.rules())
    code, found = machine.json("proposal", "approve", item, "--if-version", "1")
    assert code == EXIT_REFUSAL
    assert found["outcome"] == "refused" and found["state"] == "expired"
    assert "not reviewed" in found["reason"]
    assert found["proposal"]["actions"]["edit"]["available"] is True


def test_a_promise_from_a_late_drop_waits_fourteen_days_from_the_drop(machine):
    """Matrix "Promise from a late drop"; acceptance 4."""
    meeting = Meeting(
        meeting_id="mtg_late",
        title=TITLE,
        start=NOW - timedelta(days=10),
        duration_minutes=30,
        attendees=(Actor("email:alex@example.com"),),
        scope=PERSONAL,
    )
    transcript = Transcript(
        meeting_id="mtg_late",
        source=TranscriptSource.MANUAL,
        utterances=(Utterance("Sam Rivera", "I'll send the cache numbers by Friday", 0, None),),
    )
    (promise,) = run_transcript_ingestion(machine.daemon, transcript, meeting).staged
    stored = machine.load(promise.proposal_id)
    assert stored.created_at == meeting.start and stored.staged_at == NOW
    code, found = machine.json("proposal", "show", promise.proposal_id)
    assert code == EXIT_OK and found["proposal"]["state"] == "staged"
    assert found["proposal"]["expires_at"] == (NOW + timedelta(days=14)).isoformat()
    assert found["proposal"]["label"] == "Spoken promise"


def test_an_item_already_waiting_gets_the_longer_window(machine):
    """Matrix "Item already waiting": staged with 7 days, 8 days ago, still waiting."""
    meeting = machine.meeting()
    item = action_item_id(PERSONAL, "mtg_a", SummaryActionItem("old item"))
    machine.storage.stage_proposal(
        Proposal(
            proposal_id=item,
            type="action_item",
            summary="old item",
            payload={"what": "old item", "owner": None, "due": None},
            target=None,
            cites=SourceRef.parse("meeting:mtg_a"),
            created_at=NOW - timedelta(days=8),
            ttl=timedelta(days=7),
        )
    )
    code, found = machine.json("proposal", "show", item)
    assert found["proposal"]["state"] == "staged"
    assert found["proposal"]["expires_at"] == (NOW + timedelta(days=6)).isoformat()


def test_an_old_promise_row_is_migrated_from_its_meetings_start(tmp_path):
    """Matrix "Old promise row": migration 2 sets `staged_at` to `created_at` (known limit)."""
    root = tmp_path / "root"
    daemon = build(root, "personal", now=lambda: NOW)
    store = Path(daemon.storage.paths.resolve(APPLICATION, OPERATIONAL_DB))
    daemon.storage.close()
    body = {
        "proposal_id": "prp_mtg_old_manual_0",
        "type": "implicit_commitment",
        "summary": "I'll send it",
        "payload": {"what": {"raw": "send it", "for_model": "send it"}},
        "target": None,
        "cites": "meeting:mtg_old",
        "created_at": (NOW - timedelta(days=20)).isoformat(),
        "state": "staged",
        "version": 1,
        "ttl_seconds": 7 * 86400.0,
    }
    db = sqlite3.connect(store)
    for index in ("proposals_by_window", "proposals_by_meeting"):
        db.execute(f"DROP INDEX {index}")
    for column in ("type", "scope", "meeting_id", "staged_at", "ttl_seconds", "replaces", "changed_at"):
        db.execute(f"ALTER TABLE proposals DROP COLUMN {column}")
    db.execute("UPDATE schema_version SET version = 1")
    db.execute(
        "INSERT INTO proposals (proposal_id, body, version, state) VALUES (?, ?, 1, 'staged')",
        (body["proposal_id"], json.dumps(body)),
    )
    db.commit()
    db.close()
    reopened = build(root, "personal", now=lambda: NOW)
    db = sqlite3.connect(store)
    row = db.execute(
        "SELECT type, meeting_id, staged_at, ttl_seconds FROM proposals"
    ).fetchone()
    version = db.execute("SELECT version FROM schema_version").fetchone()[0]
    db.close()
    assert version == 2
    assert row == ("implicit_commitment", "mtg_old", (NOW - timedelta(days=20)).isoformat(), 7 * 86400.0)
    stored = reopened.storage.load_proposal("prp_mtg_old_manual_0")
    rule = reopened.proposal_types.rule("implicit_commitment")
    assert stored.seen_state(rule, NOW) is ProposalState.EXPIRED


# ── Edit ─────────────────────────────────────────────────────────────────────


def test_an_edit_supersedes_and_stages_a_replacement(summarised):
    """Matrix "Edit"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.clock = NOW + timedelta(days=2)
    code, found = machine.json("proposal", "edit", item, "--due", "Friday")
    assert code == EXIT_OK and found["outcome"] == "edited"
    replacement = found["proposal"]
    assert replacement["proposal_id"] == f"{item}_e1"
    assert replacement["state"] == "staged" and replacement["replaces"] == item
    assert replacement["fields"]["due"] == "Friday"
    assert replacement["expires_at"] == (NOW + timedelta(days=16)).isoformat()
    assert machine.load(item).state is ProposalState.SUPERSEDED
    machine.clock = NOW  # the dashboard's look-back still holds the summary
    page = machine.page()
    assert "  - Sam Rivera: send the cache TTL numbers (Friday), to review by 2026-11-06" in page
    assert "(Thursday)" not in page


def test_an_edit_equal_to_the_current_values_writes_nothing(summarised):
    """Matrix "Edit, same values"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    before = machine.store.read_bytes()
    code, found = machine.json("proposal", "edit", item, "--due", "Thursday")
    assert code == EXIT_OK and found["outcome"] == "unchanged"
    assert machine.store.read_bytes() == before


def test_edit_and_approve_in_one_step(summarised):
    """Matrix "Edit and approve"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    code, found = machine.json(
        "proposal", "edit", item, "--what", "send the cache TTL numbers to Alex", "--approve"
    )
    assert code == EXIT_OK and found["outcome"] == "edited_and_approved"
    replacement = machine.load(f"{item}_e1")
    assert replacement.state is ProposalState.APPROVED
    assert replacement.summary == "send the cache TTL numbers to Alex"


def test_editing_an_approved_item_keeps_it_approved(summarised):
    """Matrix "Edit an approved item"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.run("proposal", "approve", item)
    code, found = machine.json("proposal", "edit", item, "--owner", "Alex Morgan")
    assert code == EXIT_OK
    assert found["proposal"]["state"] == "approved"
    assert found["proposal"]["fields"]["owner"] == "Alex Morgan"
    assert found["replaced"]["state"] == "superseded"


def test_editing_an_expired_item_stages_it_again(summarised):
    """Matrix "Edit an expired item"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.clock = NOW + timedelta(days=15)
    before = machine.load(item)
    code, out, _ = machine.run("proposal", "edit", item, "--due", "next week")
    assert code == EXIT_OK
    assert out.startswith("Action item, waiting for you until")
    assert machine.load(item) == before
    assert machine.load(f"{item}_e1").replaces == item


def test_staging_an_expired_item_again_twice_is_refused(summarised):
    """Matrix "Re-stage an expired item twice"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.clock = NOW + timedelta(days=15)
    assert machine.run("proposal", "edit", item, "--due", "next week")[0] == EXIT_OK
    code, _, err = machine.run("proposal", "edit", item, "--due", "in two weeks")
    assert code == EXIT_REFUSAL and f"{item}_e1" in err
    with pytest.raises(Exception):
        machine.load(f"{item}_e2")


def test_two_edits_at_once_leave_one_replacement(summarised):
    """Matrix "Edit twice at once": the loser reloads and names the replacement."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    assert machine.run("proposal", "edit", item, "--due", "Friday", "--if-version", "1")[0] == EXIT_OK
    code, _, err = machine.run("proposal", "edit", item, "--due", "Monday", "--if-version", "1")
    assert code == EXIT_REFUSAL and f"replaced by {item}_e1" in err


def test_edit_with_no_field_is_a_usage_error(summarised):
    """Matrix "Edit with no field"."""
    machine, meeting = summarised
    code, _, err = machine.run("proposal", "edit", machine.item_id(meeting))
    assert code == EXIT_USAGE
    assert "--what" in err and "--owner" in err and "--due" in err


def test_a_promise_has_no_editable_field(machine):
    """Matrix "Field not editable"."""
    meeting = machine.meeting("mtg_p")
    transcript = Transcript(
        meeting_id="mtg_p",
        source=TranscriptSource.MANUAL,
        utterances=(Utterance("Sam Rivera", "I'll send the numbers by Friday", 0, None),),
    )
    (promise,) = run_transcript_ingestion(machine.daemon, transcript, meeting).staged
    code, _, err = machine.run("proposal", "edit", promise.proposal_id, "--what", "x")
    assert code == EXIT_REFUSAL and "no field a person may edit" in err


def test_a_value_over_its_limit_is_a_usage_error(summarised):
    """Matrix "Over the limit"."""
    machine, meeting = summarised
    code, _, err = machine.run("proposal", "edit", machine.item_id(meeting), "--what", "x" * 201)
    assert code == EXIT_USAGE and "200" in err


@pytest.mark.parametrize("what", ["", "​"])
def test_an_empty_what_is_a_usage_error(summarised, what):
    """Matrix "Empty what": empty, or a format character alone."""
    machine, meeting = summarised
    code, _, err = machine.run("proposal", "edit", machine.item_id(meeting), "--what", what)
    assert code == EXIT_USAGE and "required" in err


def test_editing_a_rejected_item_is_refused(summarised):
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.run("proposal", "reject", item)
    assert machine.run("proposal", "edit", item, "--due", "x")[0] == EXIT_REFUSAL


# ── Re-staging ───────────────────────────────────────────────────────────────


def test_a_stale_version_is_refused_with_the_current_card(summarised):
    """Matrix "Stale version": a re-summary changed the due."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    changed = (("send the cache TTL numbers", "Sam Rivera", "Thursday"),)
    # The same what and owner keep the id; only `due` is outside it, so
    # rewrite the stored due by re-staging a changed payload.
    machine.summarize(meeting, changed, again=True)
    db = sqlite3.connect(machine.store)
    body = json.loads(db.execute("SELECT body FROM proposals WHERE proposal_id = ?", (item,)).fetchone()[0])
    db.close()
    proposal = machine.load(item)
    from dataclasses import replace as _replace

    moved = machine.storage.restage_proposal(
        _replace(proposal, payload={**body["payload"], "due": "Monday"})
    )
    assert moved.value == "staged" and machine.load(item).version == 2
    code, found = machine.json("proposal", "approve", item, "--if-version", "1")
    assert code == EXIT_REFUSAL and found["proposal"]["version"] == 2
    assert machine.load(item).state is ProposalState.STAGED


def test_an_unchanged_re_summary_keeps_versions_and_expiries(summarised):
    """Matrix "Unchanged re-summary"."""
    machine, meeting = summarised
    before = {i: machine.load(machine.item_id(meeting, i)) for i in range(3)}
    machine.clock = NOW + timedelta(days=1)
    outcome = machine.summarize(meeting, again=True)
    for index, proposal in before.items():
        after = machine.load(proposal.proposal_id)
        assert (after.version, after.staged_at, after.ttl) == (
            proposal.version, proposal.staged_at, proposal.ttl,
        )
    assert set(outcome.action_items and [p for p, _ in outcome.action_items]) == set(
        p.proposal_id for p in before.values()
    )


def test_a_re_stage_that_changes_the_payload_moves_the_version_once(summarised):
    """Matrix "Re-summary changes a due": version 1 → 2, `staged_at` and window kept."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    original = machine.load(item)
    from dataclasses import replace as _replace

    newer = _replace(original, payload={**original.payload, "due": "Monday"}, staged_at=NOW + timedelta(days=3))
    machine.storage.restage_proposal(newer)
    machine.storage.restage_proposal(newer)
    after = machine.load(item)
    assert after.version == 2 and after.payload["due"] == "Monday"
    assert after.staged_at == original.staged_at and after.ttl == original.ttl


def test_a_re_summary_leaves_an_unswept_expired_row_in_left(summarised):
    """Matrix "Re-summary over an unswept expired row"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.clock = NOW + timedelta(days=15)
    outcome = machine.summarize(meeting, again=True)
    assert item in outcome.left
    stored = machine.load(item)
    assert stored.state is ProposalState.STAGED and stored.version == 1
    assert item not in outcome.superseded


def test_a_re_summary_keeps_the_pms_edit(summarised):
    """Matrix "Re-summary keeps an edit"; acceptance 5."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.run("proposal", "edit", item, "--due", "Friday")
    replacement = machine.load(f"{item}_e1")
    machine.summarize(meeting, again=True)
    assert machine.load(item).state is ProposalState.SUPERSEDED
    assert machine.load(f"{item}_e1") == replacement
    page = machine.page()
    assert page.count("send the cache TTL numbers") == 1
    assert "send the cache TTL numbers (Friday)" in page


def test_a_re_summary_repeating_an_edit_stages_nothing_new(summarised):
    """Matrix "Re-summary repeats an edit"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.run("proposal", "edit", item, "--what", "send the cache numbers")
    repeated = (("send  the Cache numbers", "Sam Rivera", "Thursday"), *ITEMS[1:])
    outcome = machine.summarize(meeting, repeated, again=True)
    new = machine.item_id(meeting, 0, repeated)
    assert new in outcome.left
    with pytest.raises(Exception):
        machine.load(new)


def test_a_reworded_item_after_an_edit_is_marked_may_repeat(summarised):
    """Matrix "Re-summary rewords an edited item" (the known limit)."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.run("proposal", "edit", item, "--what", "send the cache numbers")
    machine.clock = NOW + timedelta(minutes=5)
    reworded = (("share the TTL figures", "Sam Rivera", "Thursday"), *ITEMS[1:])
    machine.summarize(meeting, reworded, again=True)
    new = machine.item_id(meeting, 0, reworded)
    code, found = machine.json("proposal", "show", new)
    assert found["proposal"]["may_repeat"] == f"{item}_e1"
    _, out, _ = machine.run("proposal", "show", new)
    assert f"may repeat an item you edited: {item}_e1" in out


def test_a_re_summary_that_wins_over_approve_names_the_meetings_listing(summarised):
    """Matrix "Re-summary vs approve"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.summarize(meeting, ITEMS[1:], again=True)
    assert machine.load(item).state is ProposalState.SUPERSEDED
    code, _, err = machine.run("proposal", "approve", item, "--if-version", "1")
    assert code == EXIT_REFUSAL
    assert "pm-ai proposal list --scope personal --meeting mtg_a" in err


def _spoken(meeting: Meeting, *lines: str) -> Transcript:
    return Transcript(
        meeting_id=meeting.meeting_id,
        source=TranscriptSource.MANUAL,
        utterances=tuple(Utterance("Sam Rivera", line, index * 30, None) for index, line in enumerate(lines)),
    )


def test_a_re_ingestion_that_shifts_an_index_moves_the_version(machine):
    """Matrix "Re-ingestion shifts an index"."""
    meeting = machine.meeting("mtg_i")
    first = run_transcript_ingestion(machine.daemon, _spoken(meeting, "I'll send the deck by Friday"), meeting)
    (promise,) = first.staged
    run_transcript_ingestion(machine.daemon, _spoken(meeting, "I'll book the room by Monday"), meeting)
    assert machine.load(promise.proposal_id).version == 2
    code, _, _ = machine.run("proposal", "approve", promise.proposal_id, "--if-version", "1")
    assert code == EXIT_REFUSAL


def test_a_re_ingestion_over_an_acted_row_drops_and_counts_it(machine):
    """Matrix "Re-ingestion over an acted row"."""
    meeting = machine.meeting("mtg_j")
    (promise,) = run_transcript_ingestion(
        machine.daemon, _spoken(meeting, "I'll send the deck by Friday"), meeting
    ).staged
    machine.run("proposal", "approve", promise.proposal_id)
    again = run_transcript_ingestion(machine.daemon, _spoken(meeting, "I'll book the room by Monday"), meeting)
    assert again.left == (promise.proposal_id,) and again.dropped == (promise.proposal_id,)
    assert machine.load(promise.proposal_id).state is ProposalState.APPROVED
    from types import SimpleNamespace

    from pm_ai.surfaces.cli.dispatch import _closing_line

    line = _closing_line(
        SimpleNamespace(  # type: ignore[arg-type]
            bound_by="named", meeting_id="mtg_j", capture=Path("/c.vtt"), scope=PERSONAL,
            staged=0, left=1, dropped=1, title_field=None, start_field=None, duration=None,
            participants=(), from_docx=False, listed=None, title=TITLE, found_by="",
        )
    )
    assert "1 new extraction was dropped" in line


# ── Types ────────────────────────────────────────────────────────────────────


def test_a_staged_command_cannot_be_approved_and_can_be_rejected(machine):
    """Matrix "Staged command"."""
    meeting = machine.meeting("mtg_c")
    out = run_transcript_ingestion(
        machine.daemon,
        _spoken(meeting, "pm-ai, post_comment gitlab:alpha:issue:103 ship it"),
        meeting,
    )
    command = next(p for p in out.staged if p.type == "explicit_command")
    _, shown, _ = machine.run("proposal", "show", command.proposal_id)
    assert "command: post_comment on gitlab:alpha:issue:103" in shown
    assert "pm-ai cannot carry out a staged command yet" in shown
    code, _, err = machine.run("proposal", "approve", command.proposal_id)
    assert code == EXIT_REFUSAL and "cannot carry out a staged command" in err
    assert machine.run("proposal", "reject", command.proposal_id)[0] == EXIT_OK


def test_a_spoken_promise_can_be_approved(machine):
    """Matrix "Spoken promise"."""
    meeting = machine.meeting("mtg_s")
    (promise,) = run_transcript_ingestion(
        machine.daemon, _spoken(meeting, "I'll send the deck by Friday"), meeting
    ).staged
    code, found = machine.json("proposal", "approve", promise.proposal_id)
    assert code == EXIT_OK and found["outcome"] == "approved"
    assert found["proposal"]["fields"]["what"] == "send the deck"


def test_an_unknown_type_is_listed_and_only_rejectable(machine):
    """Matrix "Unknown type"."""
    machine.meeting("mtg_u")
    machine.storage.stage_proposal(
        Proposal(
            proposal_id="prp_mtg_u_future_0",
            type="future_kind",
            summary="a stored summary",
            payload={"anything": 1},
            target=None,
            cites=SourceRef.parse("meeting:mtg_u"),
            created_at=NOW,
            staged_at=NOW,
            scope="personal",
        )
    )
    code, found = machine.json("proposal", "list")
    (card,) = [c for c in found["proposals"] if c["proposal_id"] == "prp_mtg_u_future_0"]
    assert card["fields"] == {}
    assert [name for name, a in card["actions"].items() if a["available"]] == ["reject", "show"]
    _, out, _ = machine.run("proposal", "show", "prp_mtg_u_future_0")
    assert "a stored summary" in out and "pm-ai does not know this type" in out
    assert machine.run("proposal", "approve", "prp_mtg_u_future_0")[0] == EXIT_REFUSAL
    assert machine.run("proposal", "edit", "prp_mtg_u_future_0", "--what", "x")[0] == EXIT_REFUSAL
    assert machine.run("proposal", "reject", "prp_mtg_u_future_0")[0] == EXIT_OK


def test_an_unreadable_proposal_is_counted_refused_and_not_offered(summarised):
    """Matrix "Unreadable proposal"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    db = sqlite3.connect(machine.store)
    db.execute("UPDATE proposals SET body = 'not json', type = NULL WHERE proposal_id = ?", (item,))
    db.commit()
    db.close()
    code, found = machine.json("proposal", "list")
    assert found["unreadable_proposals"] == 1 and len(found["proposals"]) == 2
    assert machine.run("proposal", "show", item)[0] == EXIT_REFUSAL
    offered = completion_choices("show", now=machine.clock, paths=machine.storage.paths)
    assert item not in {choice[0] for choice in offered}


def test_an_unknown_scope_fails_closed(machine):
    """Matrix "Unknown scope": its meeting has a record in no listed scope."""
    machine.storage.stage_proposal(
        Proposal(
            proposal_id="prp_mtg_nowhere_manual_0",
            type="implicit_commitment",
            summary="I'll send it",
            payload={"what": {"raw": "send it", "for_model": "send it"}},
            target=None,
            cites=SourceRef.parse("meeting:mtg_nowhere"),
            created_at=NOW,
            staged_at=NOW,
        )
    )
    code, found = machine.json("proposal", "list")
    assert found["unscoped"] == 1 and found["proposals"] == []
    code, _, err = machine.run("proposal", "show", "prp_mtg_nowhere_manual_0")
    assert code == EXIT_REFUSAL and "scope cannot be told" in err
    assert completion_choices("show", now=NOW, paths=machine.storage.paths) == ()


# ── Scopes and meetings ──────────────────────────────────────────────────────


def test_a_meeting_in_two_scopes_needs_a_scope(machine):
    """Matrix "Meeting in two scopes"."""
    machine.meeting("mtg_two", scope=PERSONAL)
    machine.meeting("mtg_two", scope=ALPHA)
    code, _, err = machine.run("proposal", "list", "--meeting", "mtg_two")
    assert code == EXIT_REFUSAL and "personal" in err and "project:alpha" in err


def test_a_title_across_scopes_finds_the_one_meeting(machine, monkeypatch):
    """Matrix "Title across scopes": one match, in `project:alpha`."""
    meeting = machine.meeting("mtg_w", scope=ALPHA)
    machine.summarize(meeting)
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    answers = iter(["s", "s", "s"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    code, out, _ = machine.run("proposal", "review", "--title", "weekly")
    assert code == EXIT_OK and "3 skipped" in out
    assert "project:alpha" in out


def test_an_ambiguous_title_lists_the_candidates(machine):
    """Matrix "Ambiguous title"."""
    machine.summarize(machine.meeting("mtg_w1", scope=PERSONAL))
    machine.summarize(machine.meeting("mtg_w2", scope=ALPHA))
    code, _, err = machine.run("proposal", "list", "--title", "weekly")
    assert code == EXIT_REFUSAL and "mtg_w1" in err and "mtg_w2" in err


def test_a_project_not_enrolled_is_refused_naming_the_enrolled(machine):
    """Matrix "Project not enrolled"."""
    code, _, err = machine.run("proposal", "list", "--scope", "project:nope")
    assert code == EXIT_REFUSAL and "project:alpha" in err


def test_a_people_scope_is_refused(machine):
    """Matrix "People"."""
    code, _, err = machine.run("proposal", "list", "--scope", "people:example")
    assert code == EXIT_REFUSAL and "AD-4" in err


def test_a_bad_state_is_a_usage_error_naming_the_seven_words(machine):
    """Matrix "Bad state"."""
    code, _, err = machine.run("proposal", "list", "--state", "done")
    assert code == EXIT_USAGE
    for word in ("staged", "approved", "open", "rejected", "expired", "superseded", "all"):
        assert word in err


def test_the_open_state_lists_waiting_and_approved_items_only(machine):
    """Matrix "Open items" (Andrei, 2026-10-23): waiting or approved, nothing else."""
    meeting = machine.meeting()
    items = tuple((f"task {n}", None, None) for n in range(4))
    machine.summarize(meeting, items)
    ids = [machine.item_id(meeting, n, items) for n in range(4)]
    machine.run("proposal", "approve", ids[1])
    machine.run("proposal", "reject", ids[2])
    code, listing = machine.json("proposal", "list", "--state", "open")
    assert code == EXIT_OK
    assert {card["proposal_id"]: card["state"] for card in listing["proposals"]} == {
        ids[0]: "staged",
        ids[1]: "approved",
        ids[3]: "staged",
    }


def test_an_unknown_id_is_refused(machine):
    """Matrix "Not found"."""
    code, _, err = machine.run("proposal", "show", "prp_nope")
    assert code == EXIT_REFUSAL and "no proposal is staged under 'prp_nope'" in err


# ── Review ───────────────────────────────────────────────────────────────────


def _review(machine, monkeypatch, *answers: str, words=("--title", "weekly")):
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    queue = list(answers)

    def typed(prompt=""):
        if not queue:
            raise EOFError
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", typed)
    return machine.run("proposal", "review", *words)


def test_review_walks_the_cards(summarised, monkeypatch):
    """Matrix "Review": approve, edit the due then approve, skip."""
    machine, meeting = summarised
    code, out, _ = _review(machine, monkeypatch, "a", "e", "", "", "Monday", "Y", "s")
    assert code == EXIT_OK
    assert "1 approved, 1 edited and approved, 1 skipped." in out
    second = machine.item_id(meeting, 1)
    assert machine.load(f"{second}_e1").payload["due"] == "Monday"
    assert machine.load(f"{second}_e1").state is ProposalState.APPROVED


def test_review_clears_a_field_with_a_dash(summarised, monkeypatch):
    """Matrix "Review clears a field"."""
    machine, meeting = summarised
    code, out, _ = _review(machine, monkeypatch, "e", "", "-", "", "n", "q")
    assert code == EXIT_OK
    replacement = machine.load(f"{machine.item_id(meeting)}_e1")
    assert replacement.payload["owner"] is None and replacement.state is ProposalState.STAGED


def test_review_ended_by_end_of_input_keeps_what_was_answered(summarised, monkeypatch):
    """Matrix "Review interrupted"."""
    machine, meeting = summarised
    code, out, _ = _review(machine, monkeypatch, "a")
    assert code == EXIT_OK and "1 approved." in out
    assert machine.load(machine.item_id(meeting)).state is ProposalState.APPROVED
    assert machine.load(machine.item_id(meeting, 1)).state is ProposalState.STAGED


def test_review_shows_the_current_card_after_a_conflict(summarised, monkeypatch):
    """Matrix "Review conflict", and 13d's "Expiry inside review": the item expires
    while its card is open; the sentence, no command line, and the walk moves on."""
    machine, meeting = summarised
    ids = [machine.item_id(meeting, n) for n in range(3)]

    def later(prompt=""):
        machine.clock = NOW + timedelta(days=15)
        return "a"

    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    answers = iter([later, lambda prompt="": "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers)(prompt))
    code, out, _ = machine.run("proposal", "review", "--scope", "personal")
    assert code == EXIT_OK and "expired on Wed 4 Nov, not reviewed" in out
    _assert_compact(_card_lines(out, ids), ids)
    assert out.index("expired on Wed 4 Nov") < out.index("  Alex Morgan: book the review room")


def test_review_without_a_terminal_is_refused(summarised):
    """Matrix "Review without a terminal", and 13d's "No terminal": today's message."""
    machine, _ = summarised
    code, out, err = machine.run("proposal", "review")
    assert code == EXIT_REFUSAL and out == ""
    assert "`proposal review` asks at a terminal, and stdin is not one here." in err
    assert "pm-ai proposal list --json" in err and "`reject` and `edit`" in err


# ── Compact cards in the review walk (13d) ───────────────────────────────────

COMMAND_LINES = ("  approve:", "  edit:", "  reject:", "  replaces ", "  staged again as ")
ORIGIN = f"  from {TITLE}, Wed 21 Oct 10:00, personal"


def _card_lines(out: str, ids: list[str]) -> list[str]:
    """The walk's printed lines less the engine's sentences, which begin with an id
    (an answer's outcome, a conflict's refusal) and are not part of a card."""
    return [
        line for line in out.splitlines()
        if not any(line.startswith(f"  {each} ") for each in ids)
    ]


def _assert_compact(lines: list[str], ids: list[str]) -> None:
    for line in lines:
        assert "--if-version" not in line and "unavailable:" not in line
        assert not line.startswith(COMMAND_LINES), line
        assert not any(each in line for each in ids), line


def test_review_cards_are_compact(summarised, monkeypatch):
    """13d matrix "Interactive review": item, owner, text, due, origin; no commands."""
    machine, meeting = summarised
    ids = [machine.item_id(meeting, n) for n in range(3)]
    code, out, _ = _review(machine, monkeypatch, "s", "s", "s")
    assert code == EXIT_OK
    _assert_compact(out.splitlines(), ids)
    assert out.splitlines() == [
        "Action item, waiting for you until Wed 4 Nov",
        "  Sam Rivera: send the cache TTL numbers (Thursday)",
        ORIGIN,
        "Action item, waiting for you until Wed 4 Nov",
        "  Alex Morgan: book the review room",
        ORIGIN,
        "Action item, waiting for you until Wed 4 Nov",
        "  unassigned: draft the release notes (Friday)",
        ORIGIN,
        "3 skipped.",
        "next: pm-ai proposal list --scope personal",
    ]


def test_review_shows_the_replacement_compact(summarised, monkeypatch):
    """13d matrix "Edit inside review": the replacement's card is compact too,
    printed before "approve it? [Y/n]"."""
    machine, meeting = summarised
    first = machine.item_id(meeting)
    ids = [first, f"{first}_e1", *(machine.item_id(meeting, n) for n in (1, 2))]
    printed: list[str] = []
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    queue = ["e", "", "", "Monday", "n", "q"]

    def typed(prompt=""):
        printed.append(f"PROMPT {prompt.strip()}")
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", typed)
    real_echo = cli._echo

    def echoed(text="", **kwargs):
        printed.append(str(text))
        real_echo(text, **kwargs)

    monkeypatch.setattr(cli, "_echo", echoed)
    code, out, _ = machine.run("proposal", "review", "--title", "weekly")
    assert code == EXIT_OK
    _assert_compact(_card_lines(out, ids), ids)
    assert out.count(ORIGIN) == 3
    preview = "Action item, waiting for you until Wed 4 Nov\n  Sam Rivera: send the cache TTL numbers (Monday)\n" + ORIGIN + "\n"
    assert printed[printed.index("PROMPT approve it? [Y/n]:") - 1] == preview
    assert machine.load(f"{first}_e1").state is ProposalState.STAGED


def test_review_shows_the_current_card_compact_after_a_conflict(summarised, monkeypatch):
    """13d matrix "Conflict inside review": the refusal's sentence, then the card compact."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    ids = [machine.item_id(meeting, n) for n in range(3)]

    def moved(prompt=""):
        proposal = machine.load(item)
        machine.storage.restage_proposal(_replace(proposal, payload={**proposal.payload, "due": "Monday"}))
        return "a"

    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    answers = iter([moved, lambda prompt="": "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers)(prompt))
    code, out, _ = machine.run("proposal", "review", "--scope", "personal")
    assert code == EXIT_OK
    sentence = out.index("changed since version 1 was shown")
    assert out.index("(Thursday)") < sentence < out.index("  Sam Rivera: send the cache TTL numbers (Monday)")
    _assert_compact(_card_lines(out, ids), ids)
    assert machine.load(item).state is ProposalState.STAGED


def test_show_list_and_the_verbs_keep_the_full_card(summarised):
    """13d matrix "`show` and `list`": the commands, as in 13a; the verbs' text too."""
    machine, meeting = summarised
    item, second = machine.item_id(meeting), machine.item_id(meeting, 1)
    for words in (("proposal", "show", item), ("proposal", "list")):
        code, out, _ = machine.run(*words)
        assert code == EXIT_OK
        for marker in ("  approve:", "  edit:", "  reject:"):
            assert marker in out
        assert f"pm-ai proposal approve {item} --scope personal --if-version 1" in out
    code, out, _ = machine.run("proposal", "approve", second)
    # Story 15a: an approved action item is a commitment, so its card prints no
    # reject command.
    assert code == EXIT_OK and f"pm-ai proposal reject {second} --scope personal" not in out
    assert f"pm-ai proposal edit {second} --scope personal" in out


def test_json_objects_keep_their_commands(summarised):
    """13d matrix "`--json`": the same objects, the actions' commands included."""
    machine, meeting = summarised
    item, second, third = (machine.item_id(meeting, n) for n in range(3))
    _, listing = machine.json("proposal", "list")
    _, shown = machine.json("proposal", "show", item)
    for card in (*listing["proposals"], shown["proposal"]):
        for action in ("approve", "edit", "reject"):
            assert "--if-version" in _command(card, action)
    _, edited = machine.json("proposal", "edit", item, "--due", "Monday")
    assert edited["outcome"] == "edited" and _command(edited["proposal"], "approve")
    _, approved = machine.json("proposal", "approve", second)
    assert approved["outcome"] == "approved"
    # Story 15a: `reject` is unavailable on an approved action item, with its reason.
    assert approved["proposal"]["actions"]["reject"]["available"] is False
    assert approved["proposal"]["actions"]["reject"]["command"] is None
    assert approved["proposal"]["actions"]["edit"]["command"][:3] == ["proposal", "edit", second]
    _, rejected = machine.json("proposal", "reject", third)
    assert rejected["outcome"] == "rejected" and "actions" in rejected["proposal"]


def test_a_refusal_in_json_is_one_object_on_standard_output(summarised):
    """Matrix "Refusal in JSON"."""
    machine, meeting = summarised
    machine.clock = NOW + timedelta(days=15)
    code, found = machine.json("proposal", "approve", machine.item_id(meeting))
    assert code == EXIT_REFUSAL
    assert set(found) >= {"format", "outcome", "reason", "state", "changed_at", "changed_via", "replaced_by", "proposal"}
    assert found["outcome"] == "refused" and found["state"] == "expired"


# ── Completion, the pages and the prompt ─────────────────────────────────────


def test_completion_offers_a_waiting_item_by_a_word_of_its_text(summarised):
    """Matrix "Completion"."""
    machine, meeting = summarised
    completion = Completion(
        Readers(proposals=lambda verb: completion_choices(verb, now=machine.clock, paths=machine.storage.paths))
    )
    offered = completion.complete("proposal approve cache")
    assert [c.display for c in offered] == [machine.item_id(meeting)]
    assert "Sam Rivera: send the cache TTL numbers" in offered[0].meta
    assert TITLE in offered[0].meta


def test_completion_offers_the_open_state(machine):
    """`--state` completes from the seven words, `open` among them."""
    completion = Completion(Readers())
    assert [c.display for c in completion.complete("proposal list --state o")] == ["open"]


def test_the_dashboard_lists_waiting_then_approved_capped_at_five(machine):
    """Matrix "Dashboard order and cap"."""
    meeting = machine.meeting()
    items = tuple((f"task {n}", None, None) for n in range(7))
    machine.summarize(meeting, items)
    for n in (1, 3, 5):
        machine.run("proposal", "approve", machine.item_id(meeting, n, items))
    lines = [line for line in machine.page().split("\n") if line.startswith("  - ")]
    shown = [line for line in lines if line.startswith("  - unassigned: task")]
    # Story 15d: the recorded approved items leave the meeting for the block,
    # so the 4 waiting ones are listed with no overflow, and one line counts them.
    assert shown == [
        "  - unassigned: task 0, to review by 2026-11-04",
        "  - unassigned: task 2, to review by 2026-11-04",
        "  - unassigned: task 4, to review by 2026-11-04",
        "  - unassigned: task 6, to review by 2026-11-04",
    ]
    assert not any(line.startswith("  - and ") for line in lines)
    assert (
        "  - 3 commitments from this meeting, all open: `pm-ai commitment list --scope personal "
        "--meeting mtg_a --state all`"
    ) in lines


def test_the_prompt_prints_what_the_terminal_prints(summarised):
    """Matrix "Prompt": the same text, inline, within its 1-second bound."""
    machine, _ = summarised
    from pm_ai.app.entry import CONFIG_SHOW_BOUND_SECONDS, DAEMON_BOUNDS

    assert DAEMON_BOUNDS[("proposal", "list")](machine.daemon) == CONFIG_SHOW_BOUND_SECONDS == 1.0
    assert DAEMON_BOUNDS[("proposal", "approve")](machine.daemon) == PROPOSAL_WRITE_BOUND_SECONDS
    terminal = machine.run("proposal", "list")
    started = time.monotonic()
    prompt = machine.run("proposal", "list", desk=machine.desk("prompt"))
    assert time.monotonic() - started < CONFIG_SHOW_BOUND_SECONDS
    assert terminal == prompt
    assert ("proposal", "review") in cli.TERMINAL_BOUND


def test_summarize_and_the_listing_name_the_review(machine, monkeypatch):
    """Matrix "Summarize's next step"."""
    meeting = machine.meeting()
    outcome = machine.summarize(meeting, ITEMS[:2])
    assert outcome.reason.endswith(
        "To review them: pm-ai proposal review --scope personal --meeting mtg_a"
    )
    from pm_ai.app.meeting_listing import MeetingLister
    from pm_ai.core.meeting_listing import listing_json

    listing = MeetingLister(reachable=lambda: True, now=lambda: machine.clock).list(
        scope=PERSONAL, state="all", limit=None
    )
    found = json.loads(listing_json(listing))
    assert found["meetings"][0]["next"]["review"] == [
        "proposal", "review", "--scope", "personal", "--meeting", "mtg_a",
    ]


def test_hostile_text_is_one_line_on_the_card_and_escaped_in_json(machine):
    """Matrix "Hostile text"."""
    meeting = machine.meeting()
    machine.summarize(meeting, (("send\nthe\x07numbers", "Sam Rivera", None),))
    _, out, _ = machine.run("proposal", "list")
    (line,) = [line for line in out.splitlines() if line.startswith("  Sam Rivera: send")]
    assert "\x07" not in out and "numbers" in line
    _, raw, _ = machine.run("proposal", "list", "--json")
    assert "\x07" not in raw and "\n" not in raw.rstrip("\n")


# ── Team Activity (`7e`) ─────────────────────────────────────────────────────

from test_member_updates_slice import (  # noqa: E402
    ANA,
    PROJECT as TA_PROJECT,
    _answer as _ta_answer,
    _daemon as _ta_daemon,
    _ingested as _ta_ingested,
    _issue as _ta_issue,
    _meeting as _ta_meeting,
    _team_activity,
    _work as _ta_work,
)


def _ta(tmp_path):
    daemon = _ta_daemon(tmp_path, _ta_answer())
    _ta_work(daemon, _ta_issue(142, ANA))
    _ta_ingested(daemon, _ta_meeting())
    summarize_meeting(daemon, TA_PROJECT, "mtg_a")
    item = action_item_id(TA_PROJECT, "mtg_a", SummaryActionItem("fix #142", "Ana", "by Thursday"))
    return daemon, item


def test_a_rejected_item_is_hidden_from_team_activity(tmp_path):
    """Matrix "Reject", Team Activity's half: the line is hidden; the ledger is untouched."""
    daemon, item = _ta(tmp_path)
    assert "fix #142" in _team_activity(daemon)
    ledger = daemon.storage.read_member_updates(scope=TA_PROJECT)
    daemon.storage.change_proposal(item, ProposalState.REJECTED, expected_version=1, via="terminal")
    assert "fix #142" not in _team_activity(daemon)
    assert daemon.storage.read_member_updates(scope=TA_PROJECT) == ledger


def test_an_edited_item_shows_its_new_wording_under_the_same_issue(tmp_path):
    """Matrix "Edit" and "Edit an approved item", Team Activity's half."""
    daemon, item = _ta(tmp_path)
    daemon.storage.edit_proposal(
        item,
        expected_version=1,
        payload={"what": "fix #142 and its export", "owner": "Ana", "due": "Friday"},
        summary="fix #142 and its export",
        approve=False,
        rule=None,
        via="terminal",
    )
    section = _team_activity(daemon)
    assert "fix #142 and its export" in section
    assert "due as said in the meeting: Friday" in section
    assert "Thursday" not in section


def test_a_rejected_replacement_hides_the_line(tmp_path):
    daemon, item = _ta(tmp_path)
    edited = daemon.storage.edit_proposal(
        item,
        expected_version=1,
        payload={"what": "fix #142 later", "owner": "Ana", "due": None},
        summary="fix #142 later",
        approve=False,
        rule=None,
        via="terminal",
    )
    daemon.storage.change_proposal(edited.new.proposal_id, ProposalState.REJECTED, expected_version=1)
    assert "fix #142" not in _team_activity(daemon)


def test_unreadable_decisions_show_the_lines_as_recorded(tmp_path, monkeypatch):
    daemon, item = _ta(tmp_path)

    def broken(*_a, **_k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(type(daemon.storage), "proposals_of_meetings", broken)
    section = _team_activity(daemon)
    assert "fix #142" in section
    assert "Your decisions on the action items could not be read" in section


def test_a_confidential_edited_item_never_shows_its_text(tmp_path):
    """Matrix "Confidential edit": withheld by its root id, with the record and without."""
    from dataclasses import replace as _replace

    daemon, item = _ta(tmp_path)
    daemon.storage.edit_proposal(
        item,
        expected_version=1,
        payload={"what": "fix #142 quietly", "owner": "Ana", "due": None},
        summary="fix #142 quietly",
        approve=False,
        rule=None,
        via="terminal",
    )
    _ta_work(daemon, _replace(_ta_issue(142, ANA), confidential=True, title="confidential issue"))
    from test_member_updates_slice import NOW as TA_NOW

    page = prepare_dashboard(daemon, scope=TA_PROJECT, now=TA_NOW).decode("utf-8")
    assert "quietly" not in page
    for table in (
        "gitlab_members", "gitlab_issues", "gitlab_issue_assignees", "gitlab_merge_requests",
        "gitlab_merge_request_issues", "gitlab_work_status",
    ):
        # No GitLab record to tell by: every linked item is withheld (fail closed).
        daemon.storage._db.execute(f"DELETE FROM {table}")  # noqa: SLF001 — a fixture's damage
    daemon.storage._db.commit()  # noqa: SLF001
    page = prepare_dashboard(daemon, scope=TA_PROJECT, now=TA_NOW).decode("utf-8")
    assert "quietly" not in page


# ── Large store ──────────────────────────────────────────────────────────────


def _large_store(machine) -> list[Meeting]:
    """5,000 waiting proposals across 300 meetings, written straight to the store."""
    meetings = []
    for n in range(300):
        meetings.append(
            Meeting(
                meeting_id=f"mtg_{n:03d}",
                title=f"{TITLE} {n}",
                start=NOW - timedelta(hours=n),
                duration_minutes=30,
                attendees=(Actor("email:alex@example.com"),),
                scope=PERSONAL,
            )
        )
    for meeting in meetings:
        machine.daemon.meetings.put(meeting)
    db = sqlite3.connect(machine.store)
    rows = []
    for index in range(5000):
        meeting = meetings[index % 300]
        proposal = Proposal(
            proposal_id=action_item_id(PERSONAL, meeting.meeting_id, SummaryActionItem(f"task {index}")),
            type="action_item",
            summary=f"task {index}",
            payload={"what": f"task {index}", "owner": None, "due": None},
            target=None,
            cites=SourceRef.parse(f"meeting:{meeting.meeting_id}"),
            created_at=NOW,
            staged_at=NOW - timedelta(seconds=index),
            scope="personal",
            ttl=timedelta(days=14),
        )
        from pm_ai.storage.service import _columns_of, _dump_proposal

        rows.append((proposal.proposal_id, _dump_proposal(proposal), 1, "staged", *_columns_of(proposal)))
    db.executemany(
        "INSERT INTO proposals (proposal_id, body, version, state, type, scope, meeting_id, "
        "staged_at, ttl_seconds, replaces, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    db.commit()
    db.close()
    return meetings


def test_a_listing_over_five_thousand_proposals_answers_within_a_second(machine):
    """Matrix "Large store"; it reads no more bodies than it shows."""
    _large_store(machine)
    started = time.monotonic()
    code, found = machine.json("proposal", "list")
    elapsed = time.monotonic() - started
    assert code == EXIT_OK and len(found["proposals"]) == 20
    assert found["more"] == 4980
    assert found["proposals"][0]["meeting"]["meeting_id"] == "mtg_000"
    assert elapsed < 1.0, f"the listing took {elapsed:.2f} s"


def test_the_printed_commands_split_back_into_their_words(summarised):
    machine, meeting = summarised
    _, found = machine.json("proposal", "show", machine.item_id(meeting))
    words = _command(found["proposal"], "edit")
    _, out, _ = machine.run("proposal", "show", machine.item_id(meeting))
    line = next(line for line in out.splitlines() if line.strip().startswith("edit:"))
    assert shlex.split(line.split("pm-ai ", 1)[1]) == words


# ── Review findings (13a, second pass) ───────────────────────────────────────

from dataclasses import replace as _replace  # noqa: E402

from pm_ai.app.concurrency import WRITER_START_WAIT_SECONDS, WriterBusy  # noqa: E402
from pm_ai.app.proposals import build_registry  # noqa: E402
from pm_ai.core.proposals import (  # noqa: E402
    Approval,
    ApprovalKind,
    FieldSpec,
    ProposalType,
)
from pm_ai.domain.proposals import IllegalTransition, TerminalState  # noqa: E402
from pm_ai.storage.service import (  # noqa: E402
    ProposalsUnreadable,
    ReplacementExists,
    proposal_index_at,
)

_EXECUTOR_TYPE = ProposalType(
    name="sample_change",
    label="Sample change",
    fields=(FieldSpec("what", required=True, limit=200, editable=True),),
    ttl=timedelta(days=14),
    approval=Approval(ApprovalKind.EXECUTOR),
    card=lambda values: (values.get("what") or "",),
)


def _stage(machine, proposal_id: str, *, kind: str = "action_item", meeting: str = "mtg_a",
           state: ProposalState = ProposalState.STAGED, what: str = "a task", scope="personal"):
    machine.storage.stage_proposal(
        Proposal(
            proposal_id=proposal_id,
            type=kind,
            summary=what,
            payload={"what": what, "owner": None, "due": None},
            target=None,
            cites=SourceRef.parse(f"meeting:{meeting}"),
            created_at=NOW,
            staged_at=NOW,
            scope=scope,
            state=state,
            ttl=timedelta(days=14),
        )
    )
    return proposal_id


def test_a_re_summary_rewording_only_case_and_spacing_keeps_the_item(summarised):
    """Finding 1: the old root is superseded, so its folded repeat must be staged."""
    machine, meeting = summarised
    old = machine.item_id(meeting)
    recased = (("Send the cache TTL numbers", "Sam Rivera", "Thursday"), *ITEMS[1:])
    outcome = machine.summarize(meeting, recased, again=True)
    new = machine.item_id(meeting, 0, recased)
    assert new != old
    assert old in outcome.superseded and new not in outcome.left
    assert machine.load(new).state is ProposalState.STAGED
    page = machine.page()
    assert page.lower().count("send the cache ttl numbers") == 1


def test_two_folded_equal_items_in_one_batch_stage_once(machine):
    """Finding 1: the repeat set grows inside the batch."""
    meeting = machine.meeting()
    twice = (("book the room", None, None), ("Book the room", None, None))
    outcome = machine.summarize(meeting, twice)
    first, second = machine.item_id(meeting, 0, twice), machine.item_id(meeting, 1, twice)
    assert machine.load(first).state is ProposalState.STAGED
    assert second in outcome.left
    with pytest.raises(Exception):
        machine.load(second)


def test_an_expired_item_with_a_live_replacement_down_the_chain_is_not_staged_again(summarised):
    """Finding 2: `e1` superseded by a waiting `e2` still counts as live."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.clock = NOW + timedelta(days=15)
    assert machine.run("proposal", "edit", item, "--due", "next week")[0] == EXIT_OK
    assert machine.run("proposal", "edit", f"{item}_e1", "--due", "Monday")[0] == EXIT_OK
    assert machine.load(f"{item}_e1").state is ProposalState.SUPERSEDED
    _, found = machine.json("proposal", "show", item)
    edit = found["proposal"]["actions"]["edit"]
    assert edit["available"] is False and f"{item}_e2" in edit["reason"]
    code, _, err = machine.run("proposal", "edit", item, "--due", "Friday")
    assert code == EXIT_REFUSAL and f"{item}_e2" in err
    with pytest.raises(ReplacementExists) as exists:
        machine.storage.edit_proposal(
            item, expected_version=1, payload={"what": "x"}, summary="x", approve=False,
            rule=None, via="terminal",
        )
    assert exists.value.replacement == f"{item}_e2"


def test_the_store_refuses_to_edit_an_unknown_type(machine):
    """Finding 3: the change table is enforced by `edit_proposal` itself."""
    machine.meeting("mtg_u")
    _stage(machine, "prp_mtg_u_future_0", kind="future_kind", meeting="mtg_u")
    with pytest.raises(IllegalTransition):
        machine.storage.edit_proposal(
            "prp_mtg_u_future_0", expected_version=1, payload={"what": "x"}, summary="x",
            approve=False, rule=None, via="terminal",
        )
    assert machine.load("prp_mtg_u_future_0").state is ProposalState.STAGED


def test_a_re_stage_outside_the_daemon_is_one_immediate_transaction(summarised):
    """Finding 4: the read and the write share one `BEGIN IMMEDIATE`."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    statements: list[str] = []
    machine.storage._db.set_trace_callback(statements.append)  # noqa: SLF001 — observing the SQL
    try:
        current = machine.load(item)
        statements.clear()
        machine.storage.restage_proposal(_replace(current, payload={**current.payload, "due": "Monday"}))
    finally:
        machine.storage._db.set_trace_callback(None)  # noqa: SLF001
    begin = next(i for i, sql in enumerate(statements) if sql.strip().upper() == "BEGIN IMMEDIATE")
    read = next(i for i, sql in enumerate(statements) if sql.lstrip().upper().startswith("SELECT BODY"))
    write = next(i for i, sql in enumerate(statements) if sql.lstrip().upper().startswith("UPDATE PROPOSALS"))
    assert begin < read < write


def test_stage_proposal_never_overwrites_a_row_that_left_staged(summarised):
    """Finding 5: no blind overwrite past the change table."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    machine.run("proposal", "approve", item)
    approved = machine.load(item)
    with pytest.raises(TerminalState):
        machine.storage.stage_proposal(_replace(approved, state=ProposalState.STAGED, version=1))
    assert machine.load(item) == approved


def test_an_expired_promise_is_not_counted_as_dropped(machine):
    """Finding 6: a row nobody acted on is left, never "dropped over an acted row"."""
    meeting = machine.meeting("mtg_e")
    (promise,) = run_transcript_ingestion(
        machine.daemon, _spoken(meeting, "I'll send the deck by Friday"), meeting
    ).staged
    machine.clock = NOW + timedelta(days=15)
    again = run_transcript_ingestion(machine.daemon, _spoken(meeting, "I'll book the room by Monday"), meeting)
    assert again.left == (promise.proposal_id,) and again.dropped == ()


def test_a_pre_13a_row_with_no_scope_still_offers_the_review(machine):
    """Finding 7: a 7b-shaped body with no scope or staged_at is placed by its id."""
    meeting = machine.meeting()
    item = action_item_id(PERSONAL, "mtg_a", SummaryActionItem("old item"))
    body = {
        "proposal_id": item, "type": "action_item", "summary": "old item",
        "payload": {"what": "old item", "owner": None, "due": None}, "target": None,
        "cites": "meeting:mtg_a", "created_at": NOW.isoformat(), "state": "staged",
        "version": 1, "ttl_seconds": 7 * 86400.0,
    }
    db = sqlite3.connect(machine.store)
    db.execute(
        "INSERT INTO proposals (proposal_id, body, version, state, type, scope, meeting_id, "
        "staged_at, ttl_seconds) VALUES (?, ?, 1, 'staged', 'action_item', NULL, 'mtg_a', ?, ?)",
        (item, json.dumps(body), NOW.isoformat(), 7 * 86400.0),
    )
    db.commit()
    db.close()
    from pm_ai.app.meeting_listing import MeetingLister
    from pm_ai.core.meeting_listing import listing_json

    listing = MeetingLister(reachable=lambda: True, now=lambda: machine.clock).list(
        scope=PERSONAL, state="all", limit=None
    )
    found = json.loads(listing_json(listing))
    assert found["meetings"][0]["next"]["review"] == [
        "proposal", "review", "--scope", "personal", "--meeting", meeting.meeting_id,
    ]


def test_a_type_registered_by_the_composition_root_is_known_everywhere(machine, monkeypatch):
    """Finding 8: the terminal's reader, completion and the listing share one registry."""
    from pm_ai.app import proposal_reads, proposals as engine, wiring

    def with_sample():
        registry = core_default_types()
        registry.register(_EXECUTOR_TYPE)
        return registry

    from pm_ai.core.proposals import default_types as core_default_types

    monkeypatch.setattr(wiring, "registered_proposal_types", with_sample)
    monkeypatch.setattr(engine, "registered_proposal_types", with_sample)
    monkeypatch.setattr(proposal_reads, "registered_proposal_types", with_sample)
    machine.meeting()
    _stage(machine, "prp_mtg_a_sample_0", kind="sample_change")
    desk = ProposalDesk(now=lambda: machine.clock, paths=machine.storage.paths)
    (card,) = desk.listing().cards
    assert card.known and card.label == "Sample change"
    shown = completion_choices("show", now=machine.clock, paths=machine.storage.paths)
    assert [choice[0] for choice in shown] == ["prp_mtg_a_sample_0"]


def test_a_store_with_no_schema_stamp_is_read_through_its_bodies(tmp_path):
    """Finding 10: an unversioned store's columns are never assumed."""
    store = tmp_path / "operational.db"
    db = sqlite3.connect(store)
    db.execute("CREATE TABLE proposals (proposal_id TEXT PRIMARY KEY, body TEXT, version INTEGER, state TEXT)")
    body = {
        "proposal_id": "prp_mtg_old_manual_0", "type": "implicit_commitment", "summary": "s",
        "payload": {}, "target": None, "cites": "meeting:mtg_old",
        "created_at": NOW.isoformat(), "state": "staged", "version": 1, "ttl_seconds": 86400.0,
    }
    db.execute("INSERT INTO proposals VALUES (?, ?, 1, 'staged')", ("prp_mtg_old_manual_0", json.dumps(body)))
    db.commit()
    db.close()
    (row,) = proposal_index_at(store)
    assert (row.type, row.meeting_id, row.staged_at) == ("implicit_commitment", "mtg_old", NOW)
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert proposal_index_at(empty) == ()


def test_an_unreadable_registry_is_named_not_reported_as_not_enrolled(machine):
    """Finding 11."""
    (machine.home / ".pm-ai" / "projects.toml").write_text("this is [not toml", encoding="utf-8")
    code, _, err = machine.run("proposal", "list", "--scope", "project:alpha")
    assert code == EXIT_REFUSAL
    assert "projects.toml could not be read" in err and "enrolled: none" not in err


def test_edit_approve_with_the_same_values_approves_it(summarised):
    """Finding 13: `--approve` is never dropped."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    code, found = machine.json("proposal", "edit", item, "--due", "Thursday", "--approve")
    assert code == EXIT_OK and found["outcome"] == "approved"
    assert machine.load(item).state is ProposalState.APPROVED


def test_edit_approve_checks_the_types_approval(machine):
    """Finding 13: a type whose approval runs an executor is not approved by an edit."""
    machine.meeting()
    _stage(machine, "prp_mtg_a_sample_1", kind="sample_change")
    desk = ProposalDesk(
        now=lambda: machine.clock, paths=machine.storage.paths, storage=machine.storage,
        types=build_registry(_EXECUTOR_TYPE),
    )
    with pytest.raises(ProposalRefused) as refused:
        desk.edit("prp_mtg_a_sample_1", changes={"what": "another"}, approve=True)
    assert "runs a change" in str(refused.value)
    assert machine.load("prp_mtg_a_sample_1").state is ProposalState.STAGED


def test_an_edit_retried_after_a_concurrent_change_keeps_that_change(summarised, monkeypatch):
    """Finding 14: the retry rebuilds the payload from the row it read."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    storage = machine.storage
    real = type(storage).edit_proposal
    calls: list[int] = []

    def racing(self, proposal_id, **kwargs):
        if not calls:
            current = self.load_proposal(proposal_id)
            self.restage_proposal(_replace(current, payload={**current.payload, "due": "Monday"}))
        calls.append(kwargs["expected_version"])
        return real(self, proposal_id, **kwargs)

    monkeypatch.setattr(type(storage), "edit_proposal", racing)
    code, found = machine.json("proposal", "edit", item, "--what", "send the cache figures")
    assert code == EXIT_OK and calls == [1, 2]
    replacement = machine.load(f"{item}_e1")
    assert replacement.payload["due"] == "Monday"
    assert replacement.payload["what"] == "send the cache figures"


def test_a_busy_writer_at_the_prompt_names_the_writer_wait(summarised):
    """Finding 15."""
    machine, meeting = summarised

    class Busy:
        def change_proposal(self, *args, **kwargs):
            raise WriterBusy("not started in time")

    desk = ProposalDesk(
        now=lambda: machine.clock, paths=machine.storage.paths, storage=Busy(), via="prompt",
        types=machine.daemon.proposal_types,
    )
    with pytest.raises(ProposalRefused) as refused:
        desk.approve(machine.item_id(meeting))
    assert f"within {WRITER_START_WAIT_SECONDS:g} s" in str(refused.value)
    assert "daemon's writer" in str(refused.value)


def test_review_says_how_many_were_not_walked(machine, monkeypatch):
    """Finding 16: the walk is at most 200; the rest are counted, with the command."""
    machine.meeting()
    for n in range(205):
        _stage(machine, f"prp_mtg_a_bulk_{n}", what=f"task {n}")
    code, out, _ = _review(machine, monkeypatch, "q", words=("--scope", "personal"))
    assert code == EXIT_OK
    assert "5 more are waiting beyond the 200 walked" in out
    assert "pm-ai proposal review --scope personal" in out


def test_review_counts_answers_decided_elsewhere(summarised, monkeypatch):
    """Finding 17: an `already` is counted, never "nothing reviewed"."""
    machine, meeting = summarised
    item = machine.item_id(meeting)

    def elsewhere(prompt=""):
        machine.desk("prompt").approve(item)
        return "a"

    answers = iter([elsewhere, lambda prompt="": "q"])
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers)(prompt))
    code, out, _ = machine.run("proposal", "review", "--scope", "personal")
    assert code == EXIT_OK and "1 already decided elsewhere." in out
    assert "nothing reviewed" not in out


def test_review_asks_again_after_undecodable_input(summarised, monkeypatch):
    """Finding 18: an undecodable answer at `[Y/n]` is never a yes."""
    machine, meeting = summarised

    def broken(prompt=""):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    script = [lambda p="": "e", lambda p="": "", lambda p="": "", lambda p="": "Friday", broken,
              lambda p="": "n", lambda p="": "q"]
    answers = iter(script)
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers)(prompt))
    code, _, _ = machine.run("proposal", "review", "--scope", "personal")
    assert code == EXIT_OK
    assert machine.load(f"{machine.item_id(meeting)}_e1").state is ProposalState.STAGED


def test_completion_offers_only_what_the_verb_can_act_on(machine):
    """Finding 19."""
    meeting = machine.meeting("mtg_c")
    out = run_transcript_ingestion(
        machine.daemon,
        _spoken(meeting, "pm-ai, post_comment gitlab:alpha:issue:103 ship it"),
        meeting,
    )
    command = next(p for p in out.staged if p.type == "explicit_command")
    _stage(machine, "prp_mtg_c_future_0", kind="future_kind", meeting="mtg_c")
    _stage(machine, "prp_mtg_c_sample_0", kind="sample_change", meeting="mtg_c",
           state=ProposalState.APPROVED)
    types = build_registry(_EXECUTOR_TYPE)
    offered = {
        verb: {c[0] for c in completion_choices(verb, now=machine.clock, paths=machine.storage.paths, types=types)}
        for verb in ("approve", "reject")
    }
    assert command.proposal_id not in offered["approve"]
    assert "prp_mtg_c_future_0" not in offered["approve"]
    assert "prp_mtg_c_future_0" in offered["reject"]
    assert "prp_mtg_c_sample_0" not in offered["reject"]


def test_every_printed_command_names_a_scope(machine, monkeypatch):
    """Finding 20: one command per scope, the newest meeting's scope first."""
    machine.summarize(machine.meeting("mtg_p", start=START - timedelta(days=1)))
    machine.summarize(machine.meeting("mtg_q", scope=ALPHA))
    _, out, _ = machine.run("proposal", "list")
    nexts = [line for line in out.splitlines() if line.startswith("next:")]
    assert nexts == [
        "next: pm-ai proposal review --scope project:alpha",
        "next: pm-ai proposal review --scope personal",
    ]
    _, found = machine.json("proposal", "list")
    assert found["next"]["review"] == ["proposal", "review", "--scope", "project:alpha"]
    _, out, _ = machine.run("proposal", "list", "--state", "rejected")
    assert all("--scope" in line for line in out.splitlines() if "pm-ai " in line)
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    for each in machine.desk().waiting().cards:
        machine.run("proposal", "reject", each.proposal_id)
    _, out, _ = machine.run("proposal", "review")
    assert "pm-ai proposal list --scope personal --state all" in out
    assert "pm-ai proposal list --scope project:alpha --state all" in out


def test_approve_over_a_large_store_answers_within_its_bound(machine):
    """Finding 21: one proposal's reads are its row and its meeting's, measured."""
    _large_store(machine)
    item = action_item_id(PERSONAL, "mtg_000", SummaryActionItem("task 0"))
    started = time.monotonic()
    code, _, err = machine.run("proposal", "approve", item)
    elapsed = time.monotonic() - started
    assert code == EXIT_OK, err
    assert elapsed < PROPOSAL_WRITE_BOUND_SECONDS, f"approve took {elapsed:.2f} s"


def test_a_stale_version_on_an_overdue_row_is_refused_as_expired(summarised):
    """Finding 25: past its window is checked before the version."""
    machine, meeting = summarised
    machine.clock = NOW + timedelta(days=15)
    code, found = machine.json("proposal", "approve", machine.item_id(meeting), "--if-version", "9")
    assert code == EXIT_REFUSAL and found["state"] == "expired"
    assert "not reviewed" in found["reason"]


def test_an_edited_item_still_counts_as_shown_under_its_issue(tmp_path):
    """Finding 26: placement by root id; the edited text is not listed in the digest."""
    daemon, item = _ta(tmp_path)
    daemon.storage.edit_proposal(
        item, expected_version=1,
        payload={"what": "fix #142 with the new export", "owner": "Ana", "due": None},
        summary="fix #142 with the new export", approve=False, rule=None, via="terminal",
    )
    from test_member_updates_slice import NOW as TA_NOW

    page = prepare_dashboard(daemon, scope=TA_PROJECT, now=TA_NOW).decode("utf-8")
    digest = page.split("## Time-Critical Activities", 1)[1].split("\n## ", 1)[0]
    assert "1 open action item shown under #142 in Team Activity" in digest
    assert "new export" not in digest
    assert "fix #142 with the new export" in _team_activity(daemon)


def test_the_prompt_route_approves_through_the_daemons_writer(summarised):
    """Finding 27: `RequestPath`, `HandedStorage` and the writer thread; `changed_via: prompt`."""
    import asyncio
    import logging

    from pm_ai.app.concurrency import IOPool, WriterThread
    from pm_ai.app.entry import DAEMON_BOUNDS
    from pm_ai.app.prompts import DAEMON_LOGGER, RequestPath
    from pm_ai.domain.health import Report

    machine, meeting = summarised
    item = machine.item_id(meeting, 1)
    terminal_text = machine.run("proposal", "show", item)[1]
    writer = WriterThread(log=logging.getLogger(DAEMON_LOGGER), step_scope=machine.storage.claimed_step)
    writer.start()
    writer.call(machine.storage.adopt_daemon_connection, label="adopt")
    pool = IOPool()
    pool.start()
    path = RequestPath(daemon=machine.daemon, writer=writer, pool=pool,
                       diagnose=lambda: Report(()), bounds=DAEMON_BOUNDS)
    try:
        shown = asyncio.run(path.prompt(f"proposal show {item}"))
        reply = asyncio.run(path.prompt(f"proposal approve {item}"))
        assert reply.body["exit"] == EXIT_OK, reply.body
        assert "approved" in reply.body["output"]
        stored = writer.call(lambda: machine.storage.load_proposal(item), label="read a proposal")
    finally:
        writer.close()
        pool.shutdown()
    assert stored.changed_via == "prompt" and stored.state is ProposalState.APPROVED
    assert shown.body["output"] == terminal_text


def test_an_owner_change_keeps_the_row_under_its_assignee_and_a_second_edit_shows(tmp_path):
    """Finding 29: an edited owner changes the name, not the section; `_e2` wins."""
    from test_member_updates_slice import IVAN

    daemon, item = _ta(tmp_path)
    first = daemon.storage.edit_proposal(
        item, expected_version=1, payload={"what": "fix #142", "owner": "Ivan Petrov", "due": None},
        summary="fix #142", approve=False, rule=None, via="terminal",
    )
    section = _team_activity(daemon)
    assert "action for Ivan Petrov" in section and "(not assigned in GitLab)" in section
    daemon.storage.edit_proposal(
        first.new.proposal_id, expected_version=1,
        payload={"what": "fix #142 for good", "owner": "Ivan Petrov", "due": "Monday"},
        summary="fix #142 for good", approve=False, rule=None, via="terminal",
    )
    section = _team_activity(daemon)
    assert "fix #142 for good" in section and "Monday" in section
    assert IVAN.name == "Ivan Petrov"


def test_a_terminal_approve_answers_busy_within_its_bound(summarised):
    """Finding 30: another writer holds the lock past 1 s; refused, measured, nothing written."""
    machine, meeting = summarised
    item = machine.item_id(meeting)
    holder = sqlite3.connect(machine.store, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        code, _, err = machine.run("proposal", "approve", item)
        elapsed = time.monotonic() - started
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert code == EXIT_REFUSAL and "busy" in err
    assert 0.9 < elapsed < PROPOSAL_WRITE_BOUND_SECONDS, f"{elapsed:.2f} s"
    assert machine.load(item).state is ProposalState.STAGED


def test_unreadable_decision_bodies_are_said_on_team_activity(tmp_path):
    """Finding 12: the page says the decisions could not all be read."""
    daemon, item = _ta(tmp_path)
    daemon.storage._db.execute(  # noqa: SLF001 — a fixture's damage
        "UPDATE proposals SET body = 'not json' WHERE proposal_id = ?", (item,)
    )
    daemon.storage._db.commit()  # noqa: SLF001
    section = _team_activity(daemon)
    assert "fix #142" in section
    assert "1 of the meetings' proposal could not be read" in section
