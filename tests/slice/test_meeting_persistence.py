"""Story 11a — the only shape that can observe persistence at all.

A `core` unit test against a `StoragePort` cannot represent a restart, so an
accessor that cached in memory and never wrote a byte would satisfy every
read-back assertion in `tests/core/test_meeting_records.py` while leaving
exactly the defect this story exists to fix: `Daemon.meetings` was a
`dict[str, object]`, so every citation `run_transcript_ingestion` minted
resolved against process memory and died with the process.

Each test here therefore **builds a second daemon over the same temporary root**
and reads through that. Rebuilding is the whole mechanism: the second daemon's
accessor shares nothing with the first, so a value it returns came off the disk.
A `del` on the first daemon's local name was here too and was doing none of that
work — it drops one reference and guarantees no collection — so it is gone
rather than reading as an assurance this file cannot give.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import get_type_hints

import pytest

import pm_ai.app.pipelines
import pm_ai.app.wiring
from pm_ai.app.pipelines import run_transcript_ingestion
from pm_ai.app.wiring import build
from pm_ai.core.config import Config
from pm_ai.core.meeting_records import MeetingRecords, MeetingNotFound, as_stored
from pm_ai.domain.disclosure import CommittedScopeLeak
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.transcripts import Transcript, TranscriptSource, Utterance
from pm_ai.platform.paths import ScopePaths

NOW = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
PM = "andrei@example.com"
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")
PERSONAL = DataScope(ScopeKind.PERSONAL)
PEOPLE = DataScope(ScopeKind.PEOPLE, person_id="alex")

MEETING = Meeting(
    meeting_id="mtg_01HX",
    title="Project Alpha architecture sync",
    start=NOW,
    duration_minutes=45,
    attendees=(Actor("actor_andrei", "Andrei"), Actor("actor_alex", "Alex")),
    scope=PROJECT,
    calendar_event_ref="outlook:evt_9931",
)

# The `AAMkA` prefix and the `=` padding are the shape this repository records
# for a Graph event id (the `AAMkAGI2-single` fixture in
# `tests/connectors/test_graph_calendar_fetch.py`), lengthened past
# `write_artifact`'s 128-byte name limit. `33b` is the first writer of a real
# one, and a refusal here would block it — which is why the encoding is
# exercised against this shape end to end rather than against a hand-picked id.
GRAPH_ID = "AAMkAGI2" + "Zm9vYmFyLWJheg_-" * 12 + "=="


def _daemon(tmp_path: Path):
    """A daemon over `tmp_path`, with the PM stated (there is no default since 4a)."""
    return build(tmp_path, "alpha", now=lambda: NOW, config=Config(pm_handle=PM))


def _transcript(meeting: Meeting) -> Transcript:
    return Transcript(
        meeting_id=meeting.meeting_id,
        source=TranscriptSource.MANUAL,
        utterances=(Utterance("Alex", "I'll finish the migration plan by Thursday", 0),),
    )


def test_a_record_survives_a_restart(tmp_path):
    """The criterion. Written through one accessor; read through a freshly built
    one over the same root, with nothing of the first surviving.
    """
    _daemon(tmp_path).meetings.put(MEETING)

    second = _daemon(tmp_path)
    assert second.meetings.get("mtg_01HX", scope=PROJECT).meeting == as_stored(MEETING)


def test_a_days_records_survive_a_restart(tmp_path):
    """`for_day` is what `23a`'s Time-Critical section reads, and it has to read
    what a *previous* process wrote — the only interesting case for a dashboard.
    """
    first = _daemon(tmp_path)
    first.meetings.put(MEETING)
    first.meetings.put(
        Meeting(
            meeting_id="mtg_01HY",
            title="Standup",
            start=NOW + timedelta(hours=2),
            duration_minutes=15,
            attendees=(Actor("actor_alex"),),
            scope=PROJECT,
        )
    )

    found = _daemon(tmp_path).meetings.for_day(
        date(2026, 9, 4), tz=timezone.utc, scope=PROJECT
    )
    assert [held.meeting.meeting_id for held in found] == ["mtg_01HX", "mtg_01HY"]


def test_a_bare_accessor_over_the_same_root_reads_what_the_daemon_wrote(tmp_path):
    """The accessor is a vocabulary over the single writer, not a component of the
    daemon: a second one built over the same `StorageService` sees the same file.
    """
    daemon = _daemon(tmp_path)
    daemon.meetings.put(MEETING)

    assert MeetingRecords(daemon.storage).get("mtg_01HX", scope=PROJECT).meeting == as_stored(
        MEETING
    )


def test_ingestion_persists_the_meeting_it_cites(tmp_path):
    """AD-33 — the citation root of every extraction the pipeline just made is on
    disk when the pipeline returns, and still there for the next process.
    """
    daemon = _daemon(tmp_path)
    result = run_transcript_ingestion(daemon, _transcript(MEETING), MEETING)
    assert result["extractions"], "the fixture is meant to extract something"
    for extraction in result["extractions"]:
        assert str(extraction.cites) == "meeting:mtg_01HX"

    assert _daemon(tmp_path).meetings.get("mtg_01HX", scope=PROJECT).meeting.title == (
        MEETING.title
    )


def test_an_extraction_that_fails_leaves_no_record_behind(tmp_path, monkeypatch):
    """Moving the meeting to disk made this pipeline non-atomic, and the order
    is what answers for it.

    `put` was called before `extract`, so a failure in extraction left a durable
    citation root for an ingestion that minted no citations — a state the
    in-memory dict could not reach across a restart, and a line that can raise
    where a dict assignment could not. `extract` is the step that fails and it
    writes nothing, so it goes first.
    """

    def _explodes(*args, **kwargs):
        raise RuntimeError("the transcript path failed after the citation check")

    monkeypatch.setattr(pm_ai.app.pipelines, "extract", _explodes)
    daemon = _daemon(tmp_path)

    with pytest.raises(RuntimeError):
        run_transcript_ingestion(daemon, _transcript(MEETING), MEETING)

    directory = daemon.storage.paths.resolve(PROJECT, "meetings/")
    assert not directory.exists() or not list(directory.iterdir())
    with pytest.raises(MeetingNotFound):
        _daemon(tmp_path).meetings.get("mtg_01HX", scope=PROJECT)


def test_a_failure_while_staging_still_leaves_the_record_its_proposals_cite(
    tmp_path, monkeypatch
):
    """The other half of the ordering: the record is written *before* anything
    that cites it.

    Every proposal the loop stages carries `cites=meeting:…` (AD-33), so a
    failure part-way through must leave the citation root on disk — a staged
    proposal whose `cites` resolves to nothing is worse than one extraction lost.
    """
    daemon = _daemon(tmp_path)

    def _explodes(proposal):
        raise RuntimeError("staging failed on the first proposal")

    monkeypatch.setattr(daemon.storage, "stage_proposal", _explodes)

    with pytest.raises(RuntimeError):
        run_transcript_ingestion(daemon, _transcript(MEETING), MEETING)

    reread = _daemon(tmp_path).meetings.get("mtg_01HX", scope=PROJECT)
    assert reread.meeting.title == MEETING.title


def test_a_real_shaped_graph_id_is_accepted_and_read_back(tmp_path):
    """Criterion: the safe-name encoding is exercised against a provider-shaped
    id rather than a hand-written one, because `33b` is the first writer and a
    refusal there blocks it.
    """
    graph_meeting = Meeting(
        meeting_id=GRAPH_ID,
        title="Weekly sync",
        start=NOW,
        duration_minutes=30,
        attendees=(Actor("actor_alex"),),
        scope=PROJECT,
        calendar_event_ref=GRAPH_ID,
    )
    _daemon(tmp_path).meetings.put(graph_meeting)

    reread = _daemon(tmp_path).meetings.get(GRAPH_ID, scope=PROJECT)
    assert reread.meeting == as_stored(graph_meeting)


def test_a_personal_meeting_is_refused_before_any_file_is_written(tmp_path):
    """Criterion: `assert_citation_legal` still gates ingestion, and it runs
    before the accessor is reached — so the refusal leaves nothing behind.
    """
    daemon = _daemon(tmp_path)
    personal = Meeting(
        meeting_id="mtg_own",
        title="Coaching session",
        start=NOW,
        duration_minutes=60,
        attendees=(Actor("actor_andrei"),),
        scope=PERSONAL,
    )

    with pytest.raises(CommittedScopeLeak):
        run_transcript_ingestion(daemon, _transcript(personal), personal)

    for scope in (PROJECT, PERSONAL):
        directory = daemon.storage.paths.resolve(scope, "meetings/")
        assert not directory.exists() or not list(directory.iterdir())
    with pytest.raises(MeetingNotFound):
        _daemon(tmp_path).meetings.get("mtg_own", scope=PERSONAL)


@pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the `in-a-repo` row needs a real repository, and the `no-repo` row "
    "needs git to confirm there is not one",
)
@pytest.mark.parametrize("repository", [True, False], ids=["in-a-repo", "no-repo"])
def test_a_people_meeting_is_refused_with_or_without_a_repository(tmp_path, repository):
    """AD-38 — the wall is the scope boundary, not the directory (story 1n).

    Both conditions, because the guard read `is_git_committed` until 2026-09-15
    and that predicate answered "PROJECT" without ever looking at a filesystem —
    so the non-repository case passed for a reason that was about to become the
    common one. Story 1n makes the project scope's `memory/` machine-local, which
    is what broke the equivalence; a project directory outside any repository is
    an ordinary configuration now, and a citation across the wall has to be
    refused there too.

    A direct report's 1:1 is the sharper subject than the PM's own coaching
    session: the record a project citation would expose is readable by that
    report's peers.
    """
    root = ScopePaths.rooted(tmp_path).repository("alpha")
    root.mkdir(parents=True, exist_ok=True)
    if repository:
        subprocess.run(
            ["git", "init", "-q", "--initial-branch=main", "."],
            cwd=root, capture_output=True, text=True, check=True,
        )
    assert _is_a_working_tree(root) is repository, (
        "the premise did not hold, so this row does not test the condition it names"
    )
    daemon = _daemon(tmp_path)
    people = Meeting(
        meeting_id="mtg_alex_1on1",
        title="1:1 with Alex",
        start=NOW,
        duration_minutes=30,
        attendees=(Actor("actor_alex"),),
        scope=PEOPLE,
    )

    with pytest.raises(CommittedScopeLeak):
        run_transcript_ingestion(daemon, _transcript(people), people)

    for scope in (PROJECT, PEOPLE):
        directory = daemon.storage.paths.resolve(scope, "meetings/")
        assert not directory.exists() or not list(directory.iterdir())


def _is_a_working_tree(root: Path) -> bool:
    """Asked of git rather than of this test's own setup.

    A `.git` this test created is not the same fact as git agreeing this is
    inside a working tree, and the `no-repo` row is only meaningful if the
    temporary directory is not itself nested inside one.
    """
    return subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=root, capture_output=True, text=True, check=False,
    ).returncode == 0


def test_the_wiring_holds_an_accessor_and_no_dict(tmp_path):
    """Criterion: `grep -n "meetings" pm_ai/app/wiring.py` leaves no `dict`.

    Asserted against the *annotation*, not the file's text. This read `wiring.py`
    and failed any line containing both `\\bmeetings\\b` and `dict`, and it
    passed only because a comment happened to wrap so that "meetings/" and
    "`dict[str, object]`" landed on separate lines: re-wrapping that paragraph
    broke the suite with nothing regressed, and a mapping reintroduced under a
    `Mapping` alias would have kept it green. The field's declared type is the
    thing the criterion is about, so it is what is read — with the built daemon
    beside it, because an annotation is not what a caller gets.
    """
    hints = get_type_hints(pm_ai.app.wiring.Daemon)
    assert hints["meetings"] is MeetingRecords

    accessor = _daemon(tmp_path).meetings
    assert isinstance(accessor, MeetingRecords)
    assert not isinstance(accessor, Mapping), "an accessor, not a mapping"
