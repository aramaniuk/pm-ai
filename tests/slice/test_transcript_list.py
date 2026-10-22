"""Story 11f — `pm-ai transcript list`, a meeting chosen without its id, and the
re-summary guard. One test per matrix row, through `main()` where the row is a
command line.

Every row runs against a temporary home with `alpha` enrolled at a real git
repository. The meeting records, the captures and the operational store are
the real ones; rows are written with the store's own calls where it has them,
and directly where a state is only reachable through a job. The listing's
clock and the daemon check are fixed per test. No title, name or time from a
real export is used: `11d`'s fixture names, and invented ones.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_transcript_drop import FIXTURES, Keychain, Terminal  # noqa: F401
from pm_ai.app import entry, meeting_listing, pipelines
from pm_ai.app.meeting_listing import MeetingLister
from pm_ai.app.wiring import build, onboard_project
from pm_ai.core.meeting_listing import (
    SUMMARY_AGE,
    listing_text,
    next_words,
    summary_words,
)
from pm_ai.core.meeting_records import capture_name, record_name, render_record, MeetingRecord
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.meeting_listing import RecordReading
from pm_ai.domain.meeting_summaries import (
    RecordedSummary,
    SummaryAnswer,
    SummaryMaker,
    SummaryRefused,
    SummaryStateKind,
)
from pm_ai.domain.meetings import Meeting
from pm_ai.core.meeting_records import MEETINGS
from pm_ai.domain.storage_tiers import CAPTURES, OPERATIONAL_DB
from pm_ai.domain.transcripts import CaptureFormat, TranscriptStateKind
from pm_ai.platform.paths import ScopePaths
from pm_ai.storage.service import MeetingRowsUnreadable
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, EXIT_USAGE

PERSONAL = DataScope(ScopeKind.PERSONAL)
APPLICATION = DataScope(ScopeKind.APPLICATION)
ALPHA = DataScope(ScopeKind.PROJECT, "alpha")
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
START = datetime(2026, 9, 14, 7, 30, tzinfo=timezone.utc)
TITLE = "Example Weekly Sync-up"
VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n<v Alex Morgan>we ship on Friday\n"
SAID = "we ship on Friday"
MD = FIXTURES / "teams_export_recorded.md"


def _git(repository: Path, *words: str) -> None:
    subprocess.run(["git", *words], cwd=repository, check=True, capture_output=True)


def _forbidden(*_args, **_kwargs):
    raise AssertionError("the listing built a keychain adapter")


class Home:
    """A temporary home, `alpha` enrolled, and the listing's clock and daemon check."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir()
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.chdir(tmp_path)
        self.keychain = Keychain()
        self.repo = self._repository("alpha")
        onboard_project(self.keychain, str(self.repo), "alpha")
        self.reachable = False
        self.now = NOW
        monkeypatch.setattr(
            entry,
            "_meeting_lister",
            lambda: MeetingLister(reachable=lambda: self.reachable, now=lambda: self.now),
        )
        monkeypatch.setattr(entry, "MacOSKeychainAdapter", _forbidden)
        self._daemon = None

    def _repository(self, name: str) -> Path:
        repository = self.tmp / name
        repository.mkdir()
        for words in (("init", "-q"), ("config", "user.email", "t@t"), ("config", "user.name", "t")):
            _git(repository, *words)
        return repository

    def enrol(self, name: str) -> Path:
        repository = self._repository(name)
        onboard_project(self.keychain, str(repository), name)
        self._daemon = None
        return repository

    @property
    def daemon(self):
        if self._daemon is None:
            registry = self.home / ".pm-ai" / "projects.toml"
            from pm_ai.core.project_registry import parse_registry

            projects = {pid: e.path for pid, e in parse_registry(registry.read_bytes()).items()}
            paths = ScopePaths.production(projects=projects)
            self._daemon = build(
                None, "alpha", paths=paths, keychain=self.keychain, now=lambda: NOW,
                watched=tuple(projects),
            )
        return self._daemon

    def config(self, text: str) -> None:
        path = self.home / ".pm-ai" / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def folder(self, scope: DataScope, artifact: str) -> Path:
        return Path(self.daemon.storage.paths.resolve(scope, artifact))

    def record_path(self, meeting_id: str, start: datetime = START) -> Path:
        return self.folder(PERSONAL, MEETINGS) / record_name(meeting_id, start)

    @property
    def store(self) -> Path:
        return Path(self.daemon.storage.paths.resolve(APPLICATION, OPERATIONAL_DB))

    def record(
        self,
        meeting_id: str,
        *,
        title: str = TITLE,
        start: datetime = START,
        scope: DataScope = PERSONAL,
        estimated: str | None = None,
    ) -> Meeting:
        meeting = Meeting(
            meeting_id=meeting_id,
            title=title,
            start=start,
            duration_minutes=30,
            attendees=(Actor("email:alex@example.com"),),
            scope=scope,
            manual_only=estimated is not None,
            start_estimated_from=estimated,
        )
        self.daemon.meetings.put(meeting)
        return meeting

    def capture(self, meeting: Meeting, form: CaptureFormat = CaptureFormat.MANUAL_VTT, part: int = 1):
        self.daemon.storage.write_capture(
            VTT, scope=meeting.scope, name=capture_name(meeting.meeting_id, meeting.start, form, part=part)
        )

    def transcript(self, meeting: Meeting, state: TranscriptStateKind, reason: str = "r") -> None:
        self.daemon.storage.record_transcript_state(
            meeting.scope, meeting.meeting_id, state, reason, retryable=False
        )

    def summary(
        self,
        meeting: Meeting,
        state: SummaryStateKind,
        *,
        made_by: SummaryMaker = SummaryMaker.LOCAL,
        requested: bool = False,
        model: str | None = None,
        reason: str = "the transcript was ingested",
        at: datetime = NOW,
    ) -> None:
        db = self.daemon.storage._db
        db.execute(
            "INSERT OR REPLACE INTO meeting_summaries (scope, meeting_id, state, made_by, "
            "requested, model, attempts, reason, at, version, meeting_start, job) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 1, ?, NULL)",
            (
                str(meeting.scope), meeting.meeting_id, state.value, made_by.value,
                int(requested), model, reason, at.isoformat(), meeting.start.isoformat(),
            ),
        )
        db.commit()

    def part(self, meeting: Meeting, number: int) -> None:
        db = self.daemon.storage._db
        db.execute(
            "INSERT INTO transcript_parts (scope, meeting_id, part, transcript_id, created, "
            "ended, state, reason, ingested, stored_at) VALUES (?, ?, ?, ?, ?, ?, 'stored', "
            "NULL, 1, ?)",
            (
                str(meeting.scope), meeting.meeting_id, number, f"t{number}",
                meeting.start.isoformat(), meeting.start.isoformat(), NOW.isoformat(),
            ),
        )
        db.commit()

    def held(
        self,
        meeting: Meeting,
        *,
        made_by: SummaryMaker = SummaryMaker.LOCAL,
        model: str = "qwen3:8b",
        requested: bool = False,
        made_on: date = date(2026, 9, 15),
    ) -> None:
        self.daemon.meetings.put_summary(
            meeting.meeting_id,
            scope=meeting.scope,
            summary=RecordedSummary(
                model, requested, made_on, SummaryAnswer(summary="The secret plan."), made_by
            ),
        )

    def ingested(self, meeting_id: str, **record) -> Meeting:
        """Recorded, captured, ingested, and its summary waiting: a drop's state."""
        meeting = self.record(meeting_id, **record)
        self.capture(meeting)
        self.transcript(meeting, TranscriptStateKind.INGESTED)
        self.summary(meeting, SummaryStateKind.WAITING)
        return meeting


@pytest.fixture
def home(tmp_path, monkeypatch) -> Home:
    return Home(tmp_path, monkeypatch)


def _list(*options: str) -> int:
    return entry.main(["transcript", "list", *options])


def _listed(capsys, *options: str) -> str:
    code = _list(*options)
    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err + captured.out
    return captured.out


def _json(capsys, *options: str) -> dict:
    out = _listed(capsys, *options, "--json")
    return json.loads(out)


def _ids(found: dict) -> list[str]:
    return [meeting["meeting_id"] for meeting in found["meetings"]]


# ── After a drop, and the acceptance criteria ────────────────────────────────


def _drop_fixture(home: Home, monkeypatch, capsys, *options: str) -> str:
    real = pipelines.listed_after_drop

    def at_now(**read):
        # The drop's clock is the machine's; the fixture's meeting is fixed in
        # September, so the listing's clock is held at `NOW` for the next step.
        return real(**{**read, "now": NOW})

    monkeypatch.setattr(pipelines, "listed_after_drop", at_now)
    monkeypatch.setattr("builtins.input", Terminal([]))
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: home.keychain)
    code = entry.main(["transcript", "drop", str(MD), "--scope", "personal", *options])
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", _forbidden)
    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err + captured.out
    return captured.out


@pytest.fixture
def no_model(monkeypatch):
    class Model:
        def complete(self, **call):
            raise AssertionError("a model was asked")

    monkeypatch.setattr(entry, "local_model", lambda config: Model())


def test_after_a_drop_one_meeting_waits_for_the_harvest(home, monkeypatch, capsys, no_model):
    home.config('local_model = "qwen3:8b"\n')
    _drop_fixture(home, monkeypatch, capsys)
    home.reachable = True

    out = _listed(capsys)

    lines = out.splitlines()
    assert lines[0].startswith("Meetings with a transcript in personal, project:alpha")
    assert "Example Weekly Sync-up" in lines[1] and "(estimated)" in lines[1]
    assert lines[2] == (
        "  transcript ingested; summary waiting for the daemon's next harvest (local model)"
    )
    meeting_id = lines[1].split("  ")[2]
    command = f"pm-ai transcript summarize --scope personal --meeting {meeting_id}"
    assert lines[3] == f"  next: {command}"
    assert command in lines[-1] and "--by claude" in lines[-1] and "paid call" in lines[-1]


def test_acceptance_no_daemon_says_start_the_daemon_first(home, monkeypatch, capsys, no_model):
    _drop_fixture(home, monkeypatch, capsys)
    home.reachable = False

    out = _listed(capsys)

    assert "transcript ingested" in out and "(estimated)" in out
    assert "pm-ai daemon run" in out.splitlines()[3]
    found = _json(capsys)
    assert found["daemon_reachable"] is False
    (meeting,) = found["meetings"]
    assert "pm-ai daemon run" in meeting["next"]["why"]
    assert meeting["start_estimated"] is True


def test_acceptance_json_carries_the_summarize_words_and_no_utterance(
    home, monkeypatch, capsys, no_model
):
    out = _drop_fixture(home, monkeypatch, capsys)
    raw = _listed(capsys, "--json")
    found = json.loads(raw)
    (meeting,) = found["meetings"]
    words = ["transcript", "summarize", "--scope", "personal", "--meeting", meeting["meeting_id"]]
    assert meeting["next"]["summarize"] == words
    assert raw.count("\n") == 1 and raw.endswith("}\n")
    fixture = MD.read_text(encoding="utf-8")
    spoken = [line for line in fixture.splitlines() if len(line) > 30][-1]
    assert spoken not in raw and spoken[:20] not in raw
    assert meeting["meeting_id"] in out


def test_the_drops_line_ends_with_the_listings_words(home, monkeypatch, capsys, no_model):
    """Row "Drop's next step": the two wordings agree for the same meeting."""
    home.config('local_model = "qwen3:8b"\n')
    monkeypatch.setattr(entry, "_daemon_answers", lambda: True)
    home.reachable = True
    out = _drop_fixture(home, monkeypatch, capsys)
    assert "summary waiting for the daemon's next harvest (local model)" in out
    lister = MeetingLister(reachable=lambda: True, now=lambda: NOW)
    (listed,) = lister.list(scope=PERSONAL, state="all", limit=20).meetings
    assert out.strip().endswith(f"Now: {summary_words(listed)}; {next_words(listed)}.")
    text = listing_text(lister.list(scope=PERSONAL, state="all", limit=20))
    assert f"  transcript ingested; {summary_words(listed)}" in text
    assert f"  {next_words(listed)}" in text


def test_a_drop_joining_a_summarized_meeting_names_the_again_rerun(
    home, monkeypatch, capsys, no_model
):
    meeting = home.record("mtg_b", title="Example Planning", start=datetime(2026, 9, 16, 9, tzinfo=timezone.utc))
    home.held(meeting, made_by=SummaryMaker.FRONTIER, model="claude-x", requested=True)
    home.summary(meeting, SummaryStateKind.SUMMARIZED, made_by=SummaryMaker.FRONTIER,
                 requested=True, model="claude-x")
    monkeypatch.setattr(entry, "_daemon_answers", lambda: True)
    out = _drop_fixture(home, monkeypatch, capsys, "--meeting", "mtg_b")
    assert "summarized by Claude claude-x, on request" in out
    assert "--meeting mtg_b --again" in out and "replacing the summary held" in out


def test_a_real_drops_json_is_one_object(home, monkeypatch, capsys, no_model):
    out = _drop_fixture(home, monkeypatch, capsys, "--json")
    found = json.loads(out)
    assert found["format"] == 1 and found["outcome"]["kind"] == "new"
    assert found["summary"]["state"] == "waiting"
    assert found["next"]["summarize"][-1] == found["meeting_id"]


def test_a_refused_real_drop_with_json_prints_nothing_on_stdout(
    home, monkeypatch, capsys, no_model
):
    first = json.loads(_drop_fixture(home, monkeypatch, capsys, "--json"))
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: home.keychain)
    code = entry.main([
        "transcript", "drop", str(MD), "--scope", "personal", "--meeting",
        first["meeting_id"], "--json",
    ])
    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL and captured.out == ""
    assert "already has a capture" in captured.err


# ── The summary's waiting kinds ──────────────────────────────────────────────


def test_no_local_model_says_no_harvest_makes_it(home, capsys):
    home.ingested("mtg_a")
    out = _listed(capsys)
    assert "summary waiting; no local_model is set, so no harvest makes it" in out
    (meeting,) = _json(capsys)["meetings"]
    assert meeting["summary"]["offered_automatically"] is False


def test_a_claude_request_waits_for_you(home, capsys):
    home.config('local_model = "qwen3:8b"\n')
    meeting = home.ingested("mtg_a")
    home.summary(meeting, SummaryStateKind.WAITING, made_by=SummaryMaker.FRONTIER, requested=True)
    out = _listed(capsys)
    assert "summary waiting for you to ask Claude again" in out
    (found,) = _json(capsys)["meetings"]
    summary = found["summary"]
    assert summary["made_by"] == "claude" and summary["requested"] is True
    assert summary["offered_automatically"] is False


def test_older_than_30_days_is_not_offered(home, capsys):
    home.config('local_model = "qwen3:8b"\n')
    home.ingested("mtg_a", start=NOW - timedelta(days=40))
    out = _listed(capsys)
    assert (
        "waiting, not offered automatically: the meeting started more than 30 days ago" in out
    )
    assert SUMMARY_AGE == pipelines.SUMMARY_MAX_AGE


# ── Order, zone and the record ───────────────────────────────────────────────


def test_newest_first_across_scopes_and_the_zone_header(home, capsys):
    for index, day in enumerate((10, 12, 14)):
        home.ingested(f"mtg_p{index}", start=datetime(2026, 9, day, 9, tzinfo=timezone.utc))
    home.ingested("mtg_alpha", start=datetime(2026, 9, 13, 9, tzinfo=timezone.utc), scope=ALPHA)
    out = _listed(capsys)
    assert "times in UTC (no display_timezone is set)" in out.splitlines()[0]
    found = _json(capsys)
    assert _ids(found) == ["mtg_p2", "mtg_alpha", "mtg_p1", "mtg_p0"]
    assert found["zone"] == "UTC" and found["scopes"] == ["personal", "project:alpha"]


def test_a_configured_zone_is_named_and_used(home, capsys):
    home.config('display_timezone = "Europe/Warsaw"\n')
    home.ingested("mtg_a")
    out = _listed(capsys)
    assert out.splitlines()[0].endswith("times in Europe/Warsaw:")
    assert "(2026-09-14 09:30 in Europe/Warsaw)" in out
    assert _json(capsys)["zone"] == "Europe/Warsaw"


def test_an_estimated_start_is_marked(home, capsys):
    home.ingested("mtg_a", estimated="file-modified")
    assert "(estimated)" in _listed(capsys)
    assert _json(capsys)["meetings"][0]["start_estimated"] is True


# ── Each state ───────────────────────────────────────────────────────────────


def test_summarized_by_claude_offers_the_again_rerun(home, capsys):
    meeting = home.ingested("mtg_b", title="Example Planning", scope=ALPHA)
    home.held(meeting, made_by=SummaryMaker.FRONTIER, model="claude-x", requested=True)
    home.summary(meeting, SummaryStateKind.SUMMARIZED, made_by=SummaryMaker.FRONTIER,
                 requested=True, model="claude-x", at=datetime(2026, 9, 15, tzinfo=timezone.utc))
    out = _listed(capsys)
    assert "summarized by Claude claude-x, on request, on 2026-09-15" in out
    assert "next, replacing the summary held" in out and "--again" in out
    (found,) = _json(capsys)["meetings"]
    assert found["next"]["summarize"] is None
    assert found["next"]["resummarize"][-1] == "--again"
    assert found["summary"]["held"] is True and found["summary"]["made_by"] == "claude"


def test_a_reopened_meeting_is_waiting_and_summarized_not_unsummarized(home, capsys):
    meeting = home.ingested("mtg_a")
    home.held(meeting)
    assert _ids(_json(capsys, "--state", "waiting")) == ["mtg_a"]
    assert _ids(_json(capsys, "--state", "summarized")) == ["mtg_a"]
    assert _ids(_json(capsys, "--state", "not-summarized")) == []
    (found,) = _json(capsys)["meetings"]
    assert found["summary"]["held"] is True and found["summary"]["state"] == "waiting"
    assert "the record holds an earlier summary" in _listed(capsys)


def test_a_failed_summary_is_failed_and_not_summarized(home, capsys):
    meeting = home.ingested("mtg_a")
    home.summary(meeting, SummaryStateKind.FAILED, reason="three timeouts")
    assert "summary failed: three timeouts" in _listed(capsys)
    assert _ids(_json(capsys, "--state", "failed")) == ["mtg_a"]
    assert _ids(_json(capsys, "--state", "not-summarized")) == ["mtg_a"]


def test_ingestion_failed_offers_the_drop_again(home, capsys):
    meeting = home.record("mtg_c")
    home.capture(meeting)
    home.transcript(meeting, TranscriptStateKind.INGEST_FAILED, "the reader refused it")
    assert "transcript ingestion failed: the reader refused it" in _listed(capsys)
    (found,) = _json(capsys)["meetings"]
    assert found["next"]["drop"] == {
        "words": ["transcript", "drop", "--scope", "personal", "--meeting", "mtg_c"],
        "needs": ["file"],
    }
    assert found["transcript"] == {"state": "ingest_failed", "reason": "the reader refused it", "kept": None}
    assert _ids(_json(capsys, "--state", "failed")) == ["mtg_c"]
    assert _ids(_json(capsys, "--state", "not-summarized")) == []


def test_fetched_is_ingested_at_the_next_run(home, capsys):
    meeting = home.record("mtg_a")
    home.capture(meeting, CaptureFormat.GRAPH)
    home.transcript(meeting, TranscriptStateKind.FETCHED)
    assert "transcript fetched" in _listed(capsys)
    assert _json(capsys)["meetings"][0]["next"]["why"] == "ingested at the daemon's next run"


def test_a_missing_capture_is_not_kept(home, capsys):
    meeting = home.record("mtg_a")
    home.transcript(meeting, TranscriptStateKind.INGESTED)
    home.summary(meeting, SummaryStateKind.WAITING)
    out = _listed(capsys)
    assert "capture missing" in out
    (found,) = _json(capsys)["meetings"]
    assert found["transcript"]["kept"] is False
    assert found["next"]["why"] == "the transcript is missing, so the summary cannot be made"


def test_a_missing_part_among_many_is_not_kept_or_offered(home, capsys):
    meeting = home.record("mtg_a")
    for number in (1, 2, 3):
        home.part(meeting, number)
        if number != 2:
            home.capture(meeting, CaptureFormat.GRAPH, part=number)
    home.transcript(meeting, TranscriptStateKind.INGESTED)
    home.summary(meeting, SummaryStateKind.WAITING)
    (found,) = _json(capsys)["meetings"]
    assert found["transcript"]["kept"] is False
    assert _ids(_json(capsys, "--state", "not-summarized")) == []
    assert entry._summarizable("personal") == ()


def test_refused_by_teams_offers_the_drop(home, capsys):
    meeting = home.record("mtg_a")
    home.transcript(meeting, TranscriptStateKind.REFUSED, "the recording is restricted")
    assert "transcript refused by Teams: the recording is restricted" in _listed(capsys)
    assert _json(capsys)["meetings"][0]["next"]["drop"] is not None


def test_meetings_never_recorded_are_not_listed(home, capsys):
    for meeting_id, state in (("mtg_a", TranscriptStateKind.ABSENT), ("mtg_b", TranscriptStateKind.SKIPPED)):
        home.transcript(home.record(meeting_id), state)
    assert _json(capsys)["meetings"] == []


def test_absent_with_a_summary_row_is_listed(home, capsys):
    meeting = home.record("mtg_a")
    home.transcript(meeting, TranscriptStateKind.ABSENT)
    home.summary(meeting, SummaryStateKind.FAILED, reason="no transcript")
    (found,) = _json(capsys)["meetings"]
    assert found["transcript"]["state"] == "absent"
    assert found["next"]["why"] == "Teams listed no transcript"


def test_nothing_said_is_listed_under_all_only(home, capsys):
    meeting = home.ingested("mtg_a")
    home.summary(meeting, SummaryStateKind.NOTHING_SAID, reason="empty")
    (found,) = _json(capsys)["meetings"]
    assert found["summary"]["state"] == "nothing_said"
    assert found["next"]["why"] == "the transcript held no words after cleaning"
    for state in ("not-summarized", "waiting", "failed", "summarized"):
        assert _ids(_json(capsys, "--state", state)) == [], state


def test_state_waiting_lists_the_three_kinds(home, capsys):
    home.config('local_model = "qwen3:8b"\n')
    home.ingested("mtg_h", start=datetime(2026, 9, 15, 9, tzinfo=timezone.utc))
    claude = home.ingested("mtg_c", start=datetime(2026, 9, 16, 9, tzinfo=timezone.utc))
    home.summary(claude, SummaryStateKind.WAITING, made_by=SummaryMaker.FRONTIER, requested=True)
    home.ingested("mtg_o", start=NOW - timedelta(days=40))
    done = home.ingested("mtg_s", start=datetime(2026, 9, 17, 9, tzinfo=timezone.utc))
    home.held(done)
    home.summary(done, SummaryStateKind.SUMMARIZED, model="qwen3:8b")
    assert _ids(_json(capsys, "--state", "waiting")) == ["mtg_c", "mtg_h", "mtg_o"]


def test_state_summarized_lists_held_summaries(home, capsys):
    for index in (1, 2):
        meeting = home.ingested(f"mtg_s{index}", start=datetime(2026, 9, 10 + index, 9, tzinfo=timezone.utc))
        home.held(meeting)
        home.summary(meeting, SummaryStateKind.SUMMARIZED, model="qwen3:8b")
    home.ingested("mtg_w", start=datetime(2026, 9, 13, 9, tzinfo=timezone.utc))
    reopened = home.ingested("mtg_r", start=datetime(2026, 9, 14, 9, tzinfo=timezone.utc))
    home.held(reopened)
    assert _ids(_json(capsys, "--state", "summarized")) == ["mtg_r", "mtg_s2", "mtg_s1"]


def test_state_and_limit_count_the_rest(home, capsys):
    for index in range(30):
        home.ingested(f"mtg_{index:02d}", start=datetime(2026, 9, 1, 9, tzinfo=timezone.utc) + timedelta(hours=index))
    found = _json(capsys, "--state", "not-summarized", "--limit", "5")
    assert len(found["meetings"]) == 5 and found["more"] == 25
    assert _ids(found)[0] == "mtg_29"


def test_the_default_limit_is_20_and_says_how_many_older(home, capsys):
    for index in range(34):
        home.ingested(f"mtg_{index:02d}", start=datetime(2026, 8, 20, 9, tzinfo=timezone.utc) + timedelta(days=index % 25, hours=index))
    out = _listed(capsys)
    assert "and 14 older (--limit 20)" in out
    found = _json(capsys)
    assert len(found["meetings"]) == 20 and found["more"] == 14
    starts = [meeting["start"] for meeting in found["meetings"]]
    assert starts == sorted(starts, reverse=True)


# ── Usage errors and refused scopes ──────────────────────────────────────────


@pytest.mark.parametrize("limit", ["0", "500", "ten"])
def test_a_bad_limit_is_a_usage_error(home, capsys, limit):
    assert _list("--limit", limit) == EXIT_USAGE
    assert "1 to 200" in capsys.readouterr().err


def test_a_bad_state_is_a_usage_error_naming_the_five(home, capsys):
    assert _list("--state", "done") == EXIT_USAGE
    err = capsys.readouterr().err
    for word in ("all", "not-summarized", "waiting", "failed", "summarized"):
        assert word in err


def test_a_bad_scope_is_a_usage_error(home, capsys):
    assert _list("--scope", "projectalpha") == EXIT_USAGE
    err = capsys.readouterr().err
    assert "`personal`" in err and "`project:<id>`" in err


def test_people_is_refused_before_anything_is_read(home, capsys, monkeypatch):
    def read(*_a, **_k):
        raise AssertionError("something was read")

    monkeypatch.setattr(meeting_listing, "meeting_rows_at", read)
    monkeypatch.setattr(meeting_listing, "parse_registry", read)
    assert _list("--scope", "people:example") == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "`personal` or `project:<id>`" in err


def test_an_unknown_project_is_refused_as_not_enrolled(home, capsys):
    assert _list("--scope", "project:nope") == EXIT_REFUSAL
    assert "not a project projects.toml enrols" in capsys.readouterr().err


def test_one_project_unreadable_is_named_and_the_rest_listed(home, capsys):
    beta = home.enrol("beta")
    home.ingested("mtg_p")
    home.ingested("mtg_a", scope=ALPHA)
    import shutil

    shutil.rmtree(beta)
    out = _listed(capsys)
    assert any(line.startswith("pm-ai could not read project:beta:") for line in out.splitlines())
    found = _json(capsys)
    assert sorted(_ids(found)) == ["mtg_a", "mtg_p"]
    assert [fault["source"] for fault in found["unreadable"]] == ["project:beta"]


def test_a_named_project_unreadable_is_exit_0_with_the_line(home, capsys):
    beta = home.enrol("beta")
    import shutil

    shutil.rmtree(beta)
    out = _listed(capsys, "--scope", "project:beta")
    assert "pm-ai could not read project:beta" in out
    found = _json(capsys, "--scope", "project:beta")
    assert found["meetings"] == [] and found["unreadable"][0]["source"] == "project:beta"


def test_a_malformed_registry_lists_personal_and_names_it(home, capsys):
    home.ingested("mtg_p")
    (home.home / ".pm-ai" / "projects.toml").write_text("[[[not toml", encoding="utf-8")
    found = _json(capsys)
    assert _ids(found) == ["mtg_p"]
    assert found["unreadable"][0]["source"] == "projects.toml"


def test_no_meetings_folder_is_an_empty_scope(home, capsys):
    home.enrol("gamma")
    found = _json(capsys, "--scope", "project:gamma")
    assert found["meetings"] == [] and found["unreadable"] == []


def test_an_unreadable_store_lists_the_captured_records_with_states_unknown(home, capsys):
    meeting = home.ingested("mtg_d")
    home.held(meeting)
    home.record("mtg_nocapture", start=datetime(2026, 9, 12, 9, tzinfo=timezone.utc))
    db = sqlite3.connect(home.store)
    db.execute("UPDATE schema_version SET version = 99")
    db.commit()
    db.close()
    found = _json(capsys)
    (listed,) = found["meetings"]
    assert listed["meeting_id"] == "mtg_d"
    assert listed["transcript"]["state"] == "unknown" and listed["summary"]["state"] == "unknown"
    assert listed["summary"]["held"] is True
    assert listed["next"]["why"] == "the operational store could not be read"
    assert found["unreadable"][0]["source"] == "operational.db"
    out = _listed(capsys, "--state", "failed")
    assert "nothing is listed under --state failed" in out
    assert _json(capsys, "--state", "failed")["meetings"] == []


@pytest.mark.parametrize("fault", ["locked", "corrupt", "missing column"])
def test_each_store_fault_is_named(home, capsys, monkeypatch, fault):
    home.ingested("mtg_d")
    if fault == "corrupt":
        # A garbage file read by the real reader in the store's place, so the
        # daemon's own open connection cannot repair or re-create it.
        garbage = home.tmp / "corrupt.db"
        garbage.write_bytes(b"not a database at all" * 100)
        real = meeting_listing.meeting_rows_at
        monkeypatch.setattr(meeting_listing, "meeting_rows_at", lambda store: real(garbage))
    elif fault == "missing column":
        db = sqlite3.connect(home.store)
        db.execute("ALTER TABLE meeting_summaries DROP COLUMN job")
        db.commit()
        db.close()
    else:
        def locked(store):
            raise MeetingRowsUnreadable("database is locked")

        monkeypatch.setattr(meeting_listing, "meeting_rows_at", locked)
    found = _json(capsys)
    assert found["unreadable"][-1]["source"] == "operational.db"
    assert found["meetings"][0]["transcript"]["state"] == "unknown"


def test_unreadable_and_missing_records_are_listed_last_outside_the_limit(home, capsys):
    for index in range(3):
        home.ingested(f"mtg_{index}", start=datetime(2026, 9, 10 + index, 9, tzinfo=timezone.utc))
    broken = home.ingested("mtg_broken", start=datetime(2026, 9, 18, 9, tzinfo=timezone.utc))
    path = home.record_path("mtg_broken", broken.start)
    path.write_text("title=no id\nnonsense line\n", encoding="utf-8")
    ghost = Meeting(
        meeting_id="mtg_ghost", title="x", start=START, duration_minutes=30, attendees=(),
        scope=PERSONAL,
    )
    home.summary(ghost, SummaryStateKind.WAITING)
    found = _json(capsys, "--limit", "2")
    assert _ids(found)[:2] == ["mtg_2", "mtg_1"]
    tail = found["meetings"][2:]
    assert {(m["meeting_id"], m["record"]) for m in tail} == {
        ("mtg_broken", "unreadable"), ("mtg_ghost", "missing"),
    }
    assert found["more"] == 1
    out = _listed(capsys, "--limit", "2")
    assert "record unreadable  personal  mtg_broken" in out
    assert "record missing  personal  mtg_ghost" in out


def test_the_same_meeting_twice_is_listed_once(home, capsys):
    home.ingested("mtg_a")
    # Both a transcript row and a summary row name the one (scope, id).
    assert "mtg_a" in home.daemon.storage.transcript_states(PERSONAL)
    assert home.daemon.storage.summary_state(PERSONAL, "mtg_a") is not None
    assert _ids(_json(capsys)) == ["mtg_a"]


def test_an_empty_listing_is_one_sentence(home, capsys):
    out = _listed(capsys)
    assert out == (
        "No meeting with a transcript is listed under --state all in personal, "
        "project:alpha; `pm-ai transcript drop` adds a meeting.\n"
    )


def test_no_closing_line_when_nothing_qualifies(home, capsys):
    meeting = home.ingested("mtg_a")
    home.held(meeting)
    home.summary(meeting, SummaryStateKind.SUMMARIZED, model="qwen3:8b")
    out = _listed(capsys)
    assert "To summarize the newest meeting" not in out and "paid call" not in out


# ── Titles ───────────────────────────────────────────────────────────────────


def test_a_hostile_title_stays_on_one_line_and_json_is_ascii(home, capsys):
    """The record refuses a title with a line break or a control character, so the
    fold is asked of the one wording, with a title holding all three."""
    from pm_ai.core.meeting_listing import decide, listing_json
    from pm_ai.domain.meeting_listing import Listing

    hostile = "Sync\nforged row\u0007 \u202eevil done"
    meeting = Meeting(
        meeting_id="mtg_a", title="placeholder", start=START, duration_minutes=30,
        attendees=(), scope=PERSONAL,
    )
    listed = decide(
        scope="personal", meeting_id="mtg_a", record=RecordReading.READABLE,
        meeting=meeting, now=NOW,
    )
    import dataclasses

    listed = dataclasses.replace(listed, title=hostile)
    listing = Listing(zone="", scopes=("personal",), daemon_reachable=False, state="all",
                      limit=20, meetings=(listed,))
    text = listing_text(listing)
    first = text.splitlines()[1]
    assert first.endswith("mtg_a  Sync forged row evil done")
    assert len(text.splitlines()) == 4
    raw = listing_json(listing)
    assert raw.isascii() and json.loads(raw)["meetings"][0]["title"] == hostile


def test_a_long_title_is_cut_and_an_empty_one_is_untitled(home, capsys):
    long_title = " ".join(["word"] * 75)
    home.ingested("mtg_long", title=long_title, start=datetime(2026, 9, 15, 9, tzinfo=timezone.utc))
    empty = home.record("mtg_empty", title="Placeholder")
    home.capture(empty)
    home.transcript(empty, TranscriptStateKind.INGESTED)
    path = home.record_path("mtg_empty")
    path.write_text(path.read_text(encoding="utf-8").replace("title=Placeholder", "title="), encoding="utf-8")
    out = _listed(capsys)
    long_line = next(line for line in out.splitlines() if "mtg_long" in line)
    shown = long_line.split("mtg_long  ", 1)[1]
    assert shown.endswith("…") and len(shown) <= 80
    assert "mtg_empty  (untitled)" in out


def test_a_cyrillic_title_is_shown_matched_and_completed(home, capsys):
    title = "Еженедельная встреча"
    home.ingested("mtg_ru", title=title)
    assert title in _listed(capsys)
    lister = MeetingLister(reachable=lambda: False, now=lambda: NOW)
    _, (chosen,) = meeting_listing.resolve_title(lister, scope=PERSONAL, title="встреча", on=None)
    assert chosen.meeting_id == "mtg_ru"
    completion = entry._completion()
    offered = completion.complete("transcript summarize --scope personal --meeting вст")
    assert [candidate.display for candidate in offered] == ["mtg_ru"]
    assert title in offered[0].meta


# ── Reading: no keychain, a broken config ────────────────────────────────────


def test_no_daemon_and_a_broken_config_list_in_utc_without_a_keychain(home, capsys):
    home.ingested("mtg_a")
    home.config("display_timezone = [not valid\n")
    out = _listed(capsys)
    assert "times in UTC (config.toml could not be read" in out
    assert _json(capsys)["zone"] == "UTC"


def test_the_listing_writes_nothing(home, capsys):
    home.ingested("mtg_a")
    home.daemon.storage.close()

    def snapshot() -> dict[str, bytes]:
        return {
            str(path): path.read_bytes()
            for root in (home.home, home.repo / ".project-ai")
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.name.endswith(("-wal", "-shm"))
        }

    before = snapshot()
    _listed(capsys)
    _listed(capsys, "--json")
    assert snapshot() == before


# ── At the prompt (AD-21) ────────────────────────────────────────────────────


@pytest.fixture
def prompt_path(home):
    from pm_ai.app.concurrency import IOPool, WriterThread
    from pm_ai.app.prompts import DAEMON_LOGGER, RequestPath
    from pm_ai.domain.health import Report
    from pm_ai.storage.service import OperationalStoreStuck, SchemaVersionChanged

    home.config('display_timezone = "UTC"\n')
    daemon = home.daemon
    writer = WriterThread(
        log=logging.getLogger(DAEMON_LOGGER),
        step_scope=daemon.storage.claimed_step,
        halts_on=(SchemaVersionChanged, OperationalStoreStuck),
    )
    writer.start()
    writer.call(daemon.storage.adopt_daemon_connection, label="adopt")
    pool = IOPool()
    pool.start()
    path = RequestPath(
        daemon=daemon, writer=writer, pool=pool, diagnose=lambda: Report(()),
        bounds=entry.DAEMON_BOUNDS,
    )
    try:
        yield path
    finally:
        writer.close()
        pool.shutdown()


def test_at_the_prompt_byte_for_byte_inline_within_a_second(home, prompt_path, capsys):
    home.ingested("mtg_a")
    home.ingested("mtg_b", scope=ALPHA, start=datetime(2026, 9, 15, 9, tzinfo=timezone.utc))
    home.reachable = True
    terminal = _listed(capsys)
    began = time.monotonic()
    reply = asyncio.run(prompt_path.prompt("transcript list"))
    took = time.monotonic() - began
    assert reply.body["inline"] is True and reply.body["exit"] == EXIT_OK
    assert reply.body["output"] == terminal
    assert took < 1.0, took
    json_reply = asyncio.run(prompt_path.prompt("transcript list --json"))
    assert json_reply.body["output"] == _listed(capsys, "--json")


def test_a_project_enrolled_after_the_daemon_started_is_listed_at_the_prompt(
    home, prompt_path, capsys
):
    late = home.enrol("late")
    meeting = Meeting(
        meeting_id="mtg_late", title=TITLE, start=START, duration_minutes=30, attendees=(),
        scope=DataScope(ScopeKind.PROJECT, "late"),
    )
    folder = late / ".project-ai" / "meetings"
    folder.mkdir(parents=True)
    (folder / record_name("mtg_late", START)).write_text(
        render_record(MeetingRecord(meeting)), encoding="utf-8"
    )
    home.summary(meeting, SummaryStateKind.WAITING)
    reply = asyncio.run(prompt_path.prompt("transcript list"))
    assert "project:late  mtg_late" in reply.body["output"]
    scoped = asyncio.run(prompt_path.prompt("transcript list --scope project:late"))
    assert scoped.body["exit"] == EXIT_OK and "mtg_late" in scoped.body["output"]


# ── Timed, not asserted ──────────────────────────────────────────────────────


def _bulk(home: Home, calendar: int, transcripts: int) -> None:
    """`calendar` records written as files, `transcripts` of them with rows and captures."""
    meetings = home.folder(PERSONAL, MEETINGS)
    captures = home.folder(PERSONAL, CAPTURES)
    meetings.mkdir(parents=True, exist_ok=True)
    captures.mkdir(parents=True, exist_ok=True)
    db = home.daemon.storage._db
    for index in range(calendar):
        start = datetime(2026, 6, 1, 8, tzinfo=timezone.utc) + timedelta(hours=2 * index)
        meeting = Meeting(
            meeting_id=f"mtg_{index:04d}", title=f"{TITLE} {index}", start=start,
            duration_minutes=30, attendees=(Actor("email:alex@example.com"),), scope=PERSONAL,
        )
        (meetings / record_name(meeting.meeting_id, start)).write_text(
            render_record(MeetingRecord(meeting)), encoding="utf-8"
        )
        if index % (calendar // transcripts) == 0:
            (captures / capture_name(meeting.meeting_id, start, CaptureFormat.MANUAL_VTT)).write_text(VTT)
            db.execute(
                "INSERT INTO transcript_state (scope, meeting_id, state, reason, retryable, "
                "retry_after, at) VALUES ('personal', ?, 'ingested', 'r', 0, NULL, ?)",
                (meeting.meeting_id, NOW.isoformat()),
            )
            db.execute(
                "INSERT INTO meeting_summaries (scope, meeting_id, state, made_by, requested, "
                "model, attempts, reason, at, version, meeting_start, job) VALUES "
                "('personal', ?, 'waiting', 'local', 0, NULL, 0, 'r', ?, 1, ?, NULL)",
                (meeting.meeting_id, NOW.isoformat(), start.isoformat()),
            )
    db.commit()


def test_timed_the_listing_and_completion_over_a_large_home(home, capsys):
    _bulk(home, 1000, 200)
    timings: dict[str, float] = {}
    began = time.monotonic()
    found = _json(capsys)
    timings["list"] = time.monotonic() - began
    assert len(found["meetings"]) == 20 and found["more"] == 180
    began = time.monotonic()
    _json(capsys, "--state", "not-summarized")
    timings["list --state not-summarized"] = time.monotonic() - began
    completion = entry._completion()
    began = time.monotonic()
    offered = completion.complete("transcript summarize --scope personal --meeting ")
    timings["complete summarize"] = time.monotonic() - began
    assert len(offered) == 100
    began = time.monotonic()
    dropped = entry._completion().complete("transcript drop x.vtt --scope personal --meeting ")
    timings["complete drop"] = time.monotonic() - began
    assert len(dropped) == 100
    print("11f timings:", {name: round(seconds, 3) for name, seconds in timings.items()})
    for name, seconds in timings.items():
        assert seconds < 1.0, (name, seconds)


# ── Completion ───────────────────────────────────────────────────────────────


def test_completion_offers_ingested_meetings_by_title_and_inserts_the_id(home):
    home.ingested("mtg_a")
    fetched = home.record("mtg_f", title="Example Fetched", start=datetime(2026, 9, 15, 9, tzinfo=timezone.utc))
    home.capture(fetched, CaptureFormat.GRAPH)
    home.transcript(fetched, TranscriptStateKind.FETCHED)
    completion = entry._completion()
    offered = completion.complete("transcript summarize --scope personal --meeting week")
    assert [(candidate.display, candidate.text) for candidate in offered] == [("mtg_a", "mtg_a")]
    assert TITLE in offered[0].meta and "2026-09-14 07:30" in offered[0].meta
    everything = completion.complete("transcript summarize --scope personal --meeting ")
    assert [candidate.display for candidate in everything] == ["mtg_a"]


def test_completion_for_a_drop_offers_started_meetings_most_recent_first(home):
    home.record("mtg_old", start=datetime(2026, 9, 1, 9, tzinfo=timezone.utc))
    home.record("mtg_new", start=datetime(2026, 9, 10, 9, tzinfo=timezone.utc))
    offered = entry._completion().complete("transcript drop f.vtt --scope personal --meeting ")
    assert [candidate.display for candidate in offered] == ["mtg_new", "mtg_old"]


def test_completion_with_no_scope_offers_nothing(home):
    home.ingested("mtg_a")
    completion = entry._completion()
    assert completion.complete("transcript summarize --meeting ") == []
    assert completion.complete("transcript summarize --scope people:x --meeting ") == []


def test_completion_is_capped_at_100_after_filtering(home):
    _bulk(home, 300, 150)
    offered = entry._completion().complete("transcript summarize --scope personal --meeting ex")
    assert len(offered) == 100
    assert offered[0].display == "mtg_0298"


# ── Choosing a meeting with `--title` (in the daemon) ────────────────────────


def _lister() -> MeetingLister:
    return MeetingLister(reachable=lambda: True, now=lambda: NOW)


def test_title_one_match_is_chosen(home):
    home.ingested("mtg_a")
    home.ingested("mtg_q", title="Quarterly review", start=datetime(2026, 9, 15, 9, tzinfo=timezone.utc))
    meeting_id, chosen = pipelines.choose_meeting(_lister(), scope=PERSONAL, title="weekly", on=None)
    assert meeting_id == "mtg_a" and "mtg_a" in chosen and TITLE in chosen


def test_title_several_matches_are_refused_listing_each(home):
    for index, day in enumerate((10, 11, 12)):
        home.ingested(f"mtg_{index}", start=datetime(2026, 9, day, 9, tzinfo=timezone.utc))
    with pytest.raises(SummaryRefused) as refused:
        pipelines.choose_meeting(_lister(), scope=PERSONAL, title="weekly", on=None)
    said = str(refused.value)
    assert all(f"mtg_{index}" in said for index in range(3))
    assert "--on" in said and "--meeting" in said
    meeting_id, _ = pipelines.choose_meeting(
        _lister(), scope=PERSONAL, title="weekly", on=date(2026, 9, 11)
    )
    assert meeting_id == "mtg_1"


def test_title_no_match_names_the_listing(home):
    home.ingested("mtg_a")
    with pytest.raises(SummaryRefused) as refused:
        pipelines.choose_meeting(_lister(), scope=PERSONAL, title="quarterly", on=None)
    assert "pm-ai transcript list --scope personal" in str(refused.value)


def test_on_without_title_is_a_usage_error(home, capsys):
    code = entry.main(["transcript", "summarize", "--scope", "personal", "--on", "2026-09-14"])
    assert code == EXIT_USAGE
    assert "`--on` narrows `--title` to one day" in capsys.readouterr().err


def test_summarize_needs_a_meeting_or_a_title(home, capsys):
    assert entry.main(["transcript", "summarize", "--scope", "personal"]) == EXIT_USAGE


# ── Through the daemon: `--title`, `--meeting` wins, and the guard ───────────


@pytest.fixture
def summary_rig(home):
    from pm_ai.app import daemon as daemon_module
    from pm_ai.app.concurrency import IOPool, ModelPool, WriterThread
    from pm_ai.app.prompts import DAEMON_LOGGER, RequestPath
    from pm_ai.storage.service import OperationalStoreStuck, SchemaVersionChanged
    from test_meeting_summary_slice import FakeModel, _answer

    home.config('display_timezone = "UTC"\n')
    daemon = home.daemon
    model = FakeModel(*[_answer(decisions=(), items=()) for _ in range(4)])
    daemon.model = model
    writer = WriterThread(
        log=logging.getLogger(DAEMON_LOGGER),
        step_scope=daemon.storage.claimed_step,
        halts_on=(SchemaVersionChanged, OperationalStoreStuck),
    )
    writer.start()
    writer.call(daemon.storage.adopt_daemon_connection, label="adopt")
    pool = IOPool()
    pool.start()
    model_pool = ModelPool()
    model_pool.start()
    path = RequestPath(
        daemon=daemon, writer=writer, pool=pool, diagnose=lambda: None,  # type: ignore[arg-type, return-value]
        bounds=entry.DAEMON_BOUNDS,
    )
    daemon.writer, daemon.pool, daemon.model_pool = writer, pool, model_pool
    daemon.summaries = daemon_module.summary_jobs(path, model_pool)
    try:
        yield path, model
    finally:
        model_pool.shutdown()
        writer.close()
        pool.shutdown()


def _ask(path, line: str) -> tuple[str, int]:
    reply = asyncio.run(path.prompt(line))
    if reply.body.get("inline"):
        return reply.body["output"], reply.body["exit"]
    job = reply.body["job_id"]
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        row = path.daemon.storage.read_request(job)
        if row is not None and row.state in ("done", "failed"):
            result = json.loads(row.result)
            return result.get("output", result.get("error", "")), result.get("exit", 1)
        time.sleep(0.05)
    raise AssertionError("the job never ended")


def test_through_the_daemon_title_chooses_and_the_guard_refuses_a_second(home, summary_rig):
    path, model = summary_rig
    home.ingested("mtg_a")
    said, code = _ask(path, "transcript summarize --scope personal --title weekly")
    assert code == EXIT_OK, said
    assert "chose meeting mtg_a" in said and TITLE in said
    calls = len(model.calls)
    state = home.daemon.storage.summary_state(PERSONAL, "mtg_a")
    said, code = _ask(path, "transcript summarize --scope personal --meeting mtg_a --by claude")
    assert code == EXIT_REFUSAL
    assert "--again" in said and "paid Claude call" in said
    assert home.daemon.storage.summary_state(PERSONAL, "mtg_a") == state
    assert len(model.calls) == calls
    said, code = _ask(path, "transcript summarize --scope personal --meeting mtg_a")
    assert code == EXIT_REFUSAL and "the local model costs nothing" in said
    said, code = _ask(path, "transcript summarize --scope personal --meeting mtg_a --again --title other")
    assert code == EXIT_OK, said
    assert "`--title` was not consulted" in said


def test_through_the_daemon_an_ambiguous_title_asks_no_model(home, summary_rig):
    path, model = summary_rig
    for index, day in enumerate((10, 11, 12)):
        home.ingested(f"mtg_{index}", start=datetime(2026, 9, day, 9, tzinfo=timezone.utc))
    said, code = _ask(path, "transcript summarize --scope personal --title weekly")
    assert code == EXIT_REFUSAL and all(f"mtg_{index}" in said for index in range(3))
    assert model.calls == []
    said, code = _ask(path, "transcript summarize --scope personal --title weekly --on 2026-09-11")
    assert code == EXIT_OK and "mtg_1" in said


def test_again_on_a_meeting_with_no_summary_changes_nothing(home, summary_rig):
    path, _ = summary_rig
    home.ingested("mtg_a")
    said, code = _ask(path, "transcript summarize --scope personal --meeting mtg_a --again")
    assert code == EXIT_OK, said
    assert "was asked of the local model" in said


# ── `transcript drop --on` (Ask First 1, answered (a)) ───────────────────────


def test_drop_on_joins_the_one_meeting_of_that_day(home, monkeypatch, capsys, no_model):
    home.record("mtg_cal", title="Example Weekly Sync-up", start=datetime(2026, 9, 14, 7, 30, tzinfo=timezone.utc))
    out = _drop_fixture(home, monkeypatch, capsys, "--on", "2026-09-14")
    assert "mtg_cal" in out and "found by --on" in out


def test_drop_on_with_two_candidates_is_refused_listing_them(home, monkeypatch, capsys, no_model):
    home.record("mtg_one", start=datetime(2026, 9, 14, 7, 0, tzinfo=timezone.utc))
    home.record("mtg_two", start=datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc))
    monkeypatch.setattr("builtins.input", Terminal([]))
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: home.keychain)
    code = entry.main(["transcript", "drop", str(MD), "--scope", "personal", "--on", "2026-09-14"])
    err = capsys.readouterr().err
    assert code == EXIT_REFUSAL and "mtg_one" in err and "mtg_two" in err
    code = entry.main([
        "transcript", "drop", str(MD), "--scope", "personal", "--on", "2026-09-15",
    ])
    assert code == EXIT_REFUSAL and "no meeting" in capsys.readouterr().err


def test_drop_on_with_start_is_a_usage_error(home, capsys, monkeypatch):
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: home.keychain)
    before = _held_files(home)
    code = entry.main([
        "transcript", "drop", str(MD), "--scope", "personal", "--on", "2026-09-14",
        "--start", "2026-09-14T09:30Z",
    ])
    assert code == EXIT_USAGE
    assert "never makes one" in capsys.readouterr().err
    assert _held_files(home) == before


def test_the_listed_meeting_is_built_from_the_record(home):
    meeting = home.ingested("mtg_a")
    (listed,) = _lister().list(scope=PERSONAL, state="all", limit=20).meetings
    assert listed.record is RecordReading.READABLE and listed.start == meeting.start


# ── Review follow-ups (2026-10-22) ───────────────────────────────────────────


def _break(home: Home, meeting: Meeting) -> None:
    home.record_path(meeting.meeting_id, meeting.start).write_text(
        "title=no id\nnonsense line\n", encoding="utf-8"
    )


@pytest.mark.parametrize("state", ["all", "summarized", "not-summarized"])
def test_old_unreadable_and_missing_records_reach_the_tail_under_every_filter(home, capsys, state):
    for index in range(22):
        meeting = home.ingested(
            f"mtg_{index:02d}", start=datetime(2026, 9, 1, 9, tzinfo=timezone.utc) + timedelta(hours=index)
        )
        if state == "summarized":
            home.held(meeting)
    old = home.ingested("mtg_old", start=datetime(2026, 8, 1, 9, tzinfo=timezone.utc))
    _break(home, old)
    ghost = Meeting(
        meeting_id="mtg_ghost", title="x", start=START, duration_minutes=30, attendees=(),
        scope=PERSONAL,
    )
    home.transcript(ghost, TranscriptStateKind.INGESTED)
    found = _json(capsys, "--state", state)
    tail = {(m["meeting_id"], m["record"]) for m in found["meetings"][20:]}
    assert tail == {("mtg_old", "unreadable"), ("mtg_ghost", "missing")}
    assert len(found["meetings"]) == 22 and found["more"] == 2
    assert all(m["record"] == "readable" for m in found["meetings"][:20])


def test_a_waiting_row_whose_record_cannot_be_read_is_not_offered(home, capsys):
    home.config('local_model = "qwen3:8b"\n')
    meeting = home.ingested("mtg_a")
    _break(home, meeting)
    (found,) = _json(capsys)["meetings"]
    assert found["summary"]["state"] == "waiting"
    assert found["summary"]["offered_automatically"] is False
    assert "the meeting's record cannot be read" in _listed(capsys)


def test_a_zone_not_known_here_is_said_as_such():
    from pm_ai.domain.meeting_listing import Listing

    listing = Listing(zone="Nowhere/Atlantis", scopes=("personal",), daemon_reachable=False,
                      state="all", limit=20, meetings=())
    from pm_ai.core.meeting_listing import _zone_words

    said = _zone_words(listing)
    assert "Nowhere/Atlantis" in said and "not a zone known here" in said
    assert "no display_timezone is set" not in said


def test_a_null_schema_version_is_an_unreadable_store(home, capsys):
    home.ingested("mtg_a")
    db = sqlite3.connect(home.store)
    db.execute("UPDATE schema_version SET version = 'one'")
    db.commit()
    db.close()
    found = _json(capsys)
    assert found["unreadable"][-1]["source"] == "operational.db"
    assert "schema version" in found["unreadable"][-1]["reason"]


def test_a_store_really_locked_is_named_after_the_short_wait(home, capsys, monkeypatch):
    """A real lock on a copy of the store, read by the real reader: its wait runs.

    A copy, because the processes this test composed keep connections on the
    store itself; the reader is pointed at the copy and nothing else changes.
    """
    home.ingested("mtg_a")
    copy = home.tmp / "locked.db"
    source = sqlite3.connect(home.store)
    target = sqlite3.connect(copy)
    source.backup(target)
    source.close()
    target.close()
    holder = sqlite3.connect(copy, isolation_level=None)
    holder.execute("PRAGMA locking_mode=EXCLUSIVE")
    holder.execute("BEGIN EXCLUSIVE")
    holder.execute("UPDATE meeting_summaries SET reason = reason")
    real = meeting_listing.meeting_rows_at
    monkeypatch.setattr(meeting_listing, "meeting_rows_at", lambda store: real(copy))
    try:
        began = time.monotonic()
        found = _json(capsys)
        took = time.monotonic() - began
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert found["unreadable"][-1]["source"] == "operational.db"
    assert "locked" in found["unreadable"][-1]["reason"]
    assert took < 1.0


# `--on` on `drop` and `summarize`


def _drop_raw(home: Home, monkeypatch, capsys, *options: str) -> tuple[int, str, str]:
    monkeypatch.setattr("builtins.input", Terminal([]))
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: home.keychain)
    code = entry.main(["transcript", "drop", str(MD), "--scope", "personal", *options])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _held_files(home: Home) -> list[str]:
    return sorted(
        str(path) for path in (home.home / ".manager-ai").rglob("*")
        if path.is_file() and not path.name.endswith(".lock")
    )


def test_drop_on_skips_a_meeting_already_captured(home, monkeypatch, capsys, no_model):
    captured = home.record("mtg_cap", start=datetime(2026, 9, 14, 7, 30, tzinfo=timezone.utc))
    home.capture(captured)
    code, _, err = _drop_raw(home, monkeypatch, capsys, "--on", "2026-09-14")
    assert code == EXIT_REFUSAL and "mtg_cap already has a capture" in err
    home.record("mtg_open", start=datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc))
    code, out, err = _drop_raw(home, monkeypatch, capsys, "--on", "2026-09-14")
    assert code == EXIT_OK, err
    assert "mtg_open" in out and "found by --on" in out


def test_drop_on_skips_a_meeting_that_has_not_started(home, monkeypatch, capsys, no_model):
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    home.record("mtg_future", start=later)
    day = later.date().isoformat()
    code, _, err = _drop_raw(home, monkeypatch, capsys, "--on", day)
    assert code == EXIT_REFUSAL and "not started" in err


def test_drop_on_title_narrows_the_day(home, monkeypatch, capsys, no_model):
    home.record("mtg_weekly", start=datetime(2026, 9, 14, 7, 30, tzinfo=timezone.utc))
    home.record("mtg_other", title="Example Planning", start=datetime(2026, 9, 14, 12, tzinfo=timezone.utc))
    code, _, err = _drop_raw(home, monkeypatch, capsys, "--on", "2026-09-14")
    assert code == EXIT_REFUSAL and "mtg_weekly" in err and "mtg_other" in err
    code, out, err = _drop_raw(
        home, monkeypatch, capsys, "--on", "2026-09-14", "--title", "Example Planning", "--json"
    )
    assert code == EXIT_OK, err
    found = json.loads(out)
    assert found["outcome"] == {"kind": "join", "meeting_id": "mtg_other", "title": "Example Planning"}
    assert found["title"]["value"] == "Example Planning"
    assert found["start"]["value"] == "2026-09-14T12:00:00+00:00"
    assert found["duration_minutes"]["value"] == 30


def test_drop_with_meeting_and_json_reports_the_records_fields(home, monkeypatch, capsys, no_model):
    home.record("mtg_named", start=datetime(2026, 9, 14, 7, 30, tzinfo=timezone.utc))
    code, out, err = _drop_raw(home, monkeypatch, capsys, "--meeting", "mtg_named", "--json")
    assert code == EXIT_OK, err
    found = json.loads(out)
    assert found["outcome"]["kind"] == "join" and found["meeting_id"] == "mtg_named"
    assert found["title"]["value"] == TITLE
    assert found["start"]["value"] == "2026-09-14T07:30:00+00:00"
    assert found["duration_minutes"]["value"] == 30


def test_drop_on_reads_the_day_in_the_display_zone(home, monkeypatch, capsys, no_model):
    home.config('display_timezone = "Asia/Tokyo"\n')
    # 2026-09-13 20:00 UTC is 2026-09-14 05:00 in Tokyo.
    home.record("mtg_tokyo", start=datetime(2026, 9, 13, 20, 0, tzinfo=timezone.utc))
    code, out, err = _drop_raw(home, monkeypatch, capsys, "--on", "2026-09-14")
    assert code == EXIT_OK, err
    assert "mtg_tokyo" in out
    home.record("mtg_utc_day", start=datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc))
    code, _, err = _drop_raw(home, monkeypatch, capsys, "--on", "2026-09-14")
    assert "mtg_utc_day" not in err


def test_drop_on_dry_run_previews_the_join(home, monkeypatch, capsys, no_model):
    home.record("mtg_cal", start=datetime(2026, 9, 14, 7, 30, tzinfo=timezone.utc))
    before = _held_files(home)
    code, out, err = _drop_raw(home, monkeypatch, capsys, "--on", "2026-09-14", "--dry-run", "--json")
    assert code == EXIT_OK, err
    found = json.loads(out)
    assert found["outcome"]["kind"] == "join" and found["outcome"]["meeting_id"] == "mtg_cal"
    assert _held_files(home) == before


@pytest.mark.parametrize(
    "command",
    [
        ["transcript", "drop", str(MD), "--scope", "personal", "--on", "2026-9-14"],
        ["transcript", "summarize", "--scope", "personal", "--title", "weekly", "--on", "2026-9-14"],
    ],
)
def test_a_malformed_on_date_is_a_usage_error(home, monkeypatch, capsys, command):
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: home.keychain)
    assert entry.main(command) == EXIT_USAGE
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_drop_on_with_meeting_is_a_usage_error(home, monkeypatch, capsys):
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: home.keychain)
    code = entry.main([
        "transcript", "drop", str(MD), "--scope", "personal", "--on", "2026-09-14",
        "--meeting", "mtg_a",
    ])
    assert code == EXIT_USAGE and "--on" in capsys.readouterr().err


def test_through_the_daemon_meeting_wins_over_on(home, summary_rig):
    path, _ = summary_rig
    home.ingested("mtg_a")
    said, code = _ask(path, "transcript summarize --scope personal --meeting mtg_a --on 2026-09-01")
    assert code == EXIT_OK, said
    assert "`--on` was not consulted" in said


def test_a_failing_again_resummary_asks_again_with_again(tmp_path):
    """The "asks again" sentence over a held summary ends in `--again` (story 11f)."""
    from test_meeting_summary_slice import PROJECT, _daemon, _ingested, _meeting

    daemon = _daemon(tmp_path, "not json at all", "still not json")
    meeting = _ingested(daemon, _meeting())
    daemon.meetings.put_summary(
        meeting.meeting_id, scope=meeting.scope,
        summary=RecordedSummary("qwen3:8b", False, date(2026, 9, 15), SummaryAnswer(summary="held")),
    )
    ended: list = []
    pipelines.request_summary(
        daemon, scope=PROJECT, meeting_id=meeting.meeting_id, job="job_t", again=True,
        on_done=ended.append,
    )
    (outcome,) = ended
    assert outcome.result is pipelines.SummaryResult.FAILED
    assert outcome.reason.endswith(
        f"`pm-ai transcript summarize --scope {PROJECT} --meeting {meeting.meeting_id} --again` "
        f"asks again."
    )


def test_completion_offers_a_future_meeting_to_member_name_not_to_drop(home):
    future = datetime.now(timezone.utc) + timedelta(days=2)
    home.record("mtg_future", start=future, scope=ALPHA)
    home.record("mtg_past", start=datetime(2026, 9, 1, 9, tzinfo=timezone.utc), scope=ALPHA)
    completion = entry._completion()
    dropped = completion.complete("transcript drop f.vtt --scope project:alpha --meeting ")
    assert [c.display for c in dropped] == ["mtg_past"]
    named = completion.complete("member name --scope project:alpha Ваня ivan.p --meeting ")
    assert "mtg_future" in [c.display for c in named]


def test_summarize_completion_says_when_a_summary_is_held(home):
    meeting = home.ingested("mtg_a")
    home.held(meeting)
    (offered,) = entry._completion().complete("transcript summarize --scope personal --meeting ")
    assert "needs --again" in offered.meta
