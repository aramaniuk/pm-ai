"""Story 11a — the only shape that can observe persistence at all.

A `core` unit test against a `StoragePort` cannot represent a restart, so an
accessor that cached in memory and never wrote a byte would satisfy every
read-back assertion in `tests/core/test_meeting_records.py` while leaving
exactly the defect this story exists to fix: `Daemon.meetings` was a
`dict[str, object]`, so every citation `run_transcript_ingestion` minted
resolved against process memory and died with the process.

Each test here therefore **discards the accessor** — and in most cases the whole
daemon — and rebuilds it against the same temporary root before reading.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import pm_ai.app.wiring
from pm_ai.app.pipelines import run_transcript_ingestion
from pm_ai.app.wiring import build
from pm_ai.core.config import Config
from pm_ai.core.meeting_records import MeetingRecords, MeetingNotFound, as_stored
from pm_ai.domain.disclosure import CommittedScopeLeak
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.transcripts import Transcript, TranscriptSource, Utterance

NOW = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
PM = "andrei@example.com"
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")
PERSONAL = DataScope(ScopeKind.PERSONAL)

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
    first = _daemon(tmp_path)
    first.meetings.put(MEETING)
    del first

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
    del first

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
    del daemon

    assert _daemon(tmp_path).meetings.get("mtg_01HX", scope=PROJECT).meeting.title == (
        MEETING.title
    )


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
    first = _daemon(tmp_path)
    first.meetings.put(graph_meeting, tentative=True)
    del first

    reread = _daemon(tmp_path).meetings.get(GRAPH_ID, scope=PROJECT)
    assert reread.meeting == as_stored(graph_meeting)
    assert reread.tentative is True


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


def test_the_wiring_holds_an_accessor_and_no_dict(tmp_path):
    """Criterion: `grep -n "meetings" pm_ai/app/wiring.py` leaves no `dict`.

    Asserted rather than run by hand, because the dict is the defect: a field
    typed `dict[str, object]` re-introduced beside the accessor would keep every
    test above passing while one caller wrote into memory again.
    """
    source = Path(pm_ai.app.wiring.__file__).read_text(encoding="utf-8")
    offending = [
        line
        for line in source.splitlines()
        if re.search(r"\bmeetings\b", line) and "dict" in line
    ]
    assert not offending, offending
    assert isinstance(_daemon(tmp_path).meetings, MeetingRecords)
