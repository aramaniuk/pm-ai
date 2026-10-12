"""Story 33e — transcript acquisition's selection, writes, state rows and naming.

Against the real single writer (`build` over a rooted layout) and a scripted
source standing in for `TranscriptSourcePort`, so what is exercised is which
meetings are asked about and what is kept — not Graph, which
`tests/connectors/test_graph_transcripts.py` covers.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

import pytest

from pm_ai.app.wiring import build
from pm_ai.core.meeting_records import (
    MEETINGS,
    MeetingRecords,
    capture_name,
    capture_stem,
    has_capture,
    record_name,
)
from pm_ai.core.transcript_acquisition import acquire_transcripts, select_meetings
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.storage_tiers import CAPTURES
from pm_ai.domain.transcripts import (
    CaptureFormat,
    TranscriptAbsent,
    TranscriptCapture,
    TranscriptCaptured,
    TranscriptOutcome,
    TranscriptRefused,
    TranscriptSkipped,
    TranscriptState,
    TranscriptStateKind,
)
from pm_ai.ports import TranscriptSourcePort, TranscriptSourceUnavailable
from pm_ai.storage.service import CAPTURE_NAME_LIMIT

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=7)
PERSONAL = DataScope(ScopeKind.PERSONAL)
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")
TEXT = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n<v Sam>Hello.</v>\n"


def _meeting(meeting_id: str, start: datetime, *, scope=PERSONAL, ref: str | None = "same", minutes=60) -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title="Sync",
        start=start,
        duration_minutes=minutes,
        attendees=(),
        scope=scope,
        calendar_event_ref=meeting_id if ref == "same" else ref,
    )


def _capture(meeting: Meeting, *, text: str = TEXT, attributed: bool = True) -> TranscriptCapture:
    return TranscriptCapture(
        meeting_id=meeting.meeting_id,
        transcript_id=f"T-{meeting.meeting_id}",
        created=meeting.start + timedelta(minutes=1),
        ended=meeting.start + timedelta(minutes=50),
        text=text,
        attributed=attributed,
        format=CaptureFormat.GRAPH,
    )


class Source:
    """A scripted `TranscriptSourcePort`: answers by meeting id, records what it was asked."""

    name = "graph"
    requires_network = True

    def __init__(self, answers: dict[str, TranscriptOutcome] | None = None, *, stop_after: int | None = None) -> None:
        self.answers = answers or {}
        self.asked: list[list[str]] = []
        self.stop_after = stop_after
        self.since: datetime | None = None

    def acquire(self, meetings: Sequence[Meeting], *, since: datetime) -> tuple[TranscriptOutcome, ...]:
        self.asked.append([m.meeting_id for m in meetings])
        self.since = since
        reached = meetings if self.stop_after is None else meetings[: self.stop_after]
        return tuple(
            self.answers.get(m.meeting_id) or TranscriptCaptured(m.meeting_id, _capture(m))
            for m in reached
        )


def _daemon(tmp_path, *held: Meeting):
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    for meeting in held:
        daemon.meetings.put(meeting)
    return daemon


def _acquire(daemon, source, *, scopes=(PERSONAL, PROJECT)):
    return acquire_transcripts(source, daemon.storage, scopes=scopes, since=SINCE, now=NOW)


# ── The one naming rule ──────────────────────────────────────────────────────


def test_a_capture_is_named_after_its_record():
    start = NOW - timedelta(days=1)
    stem = capture_stem("AAMk-one", start)
    assert stem + ".md" == record_name("AAMk-one", start)
    assert capture_name("AAMk-one", start, CaptureFormat.GRAPH) == f"{stem}-graph.vtt"
    assert capture_name("AAMk-one", start, CaptureFormat.MANUAL_VTT) == f"{stem}-manual.vtt"
    assert capture_name("AAMk-one", start, CaptureFormat.MANUAL_TXT) == f"{stem}-manual.txt"


def test_the_longest_capture_name_fits_the_writers_limit():
    longest = capture_name("x" * 500, NOW, CaptureFormat.MANUAL_VTT)
    assert len(longest.encode()) <= CAPTURE_NAME_LIMIT


def test_has_capture_is_a_prefix_test_over_every_source():
    stem = capture_stem("AAMk-one", NOW)
    other = capture_stem("AAMk-two", NOW)
    assert has_capture([f"{stem}-manual.txt"], stem)
    assert has_capture(["temp", f"{stem}-graph.vtt"], stem)
    assert not has_capture(["temp", f"{other}-graph.vtt"], stem)
    with pytest.raises(ValueError):
        has_capture([], "")


def test_capture_name_refuses_a_source_that_is_not_a_capture_format():
    with pytest.raises(TypeError):
        capture_name("AAMk-one", NOW, "graph.vtt")  # type: ignore[arg-type]


# ── Which meetings are tried ─────────────────────────────────────────────────


def test_selection_is_every_ended_recorded_meeting_in_the_reach_back(tmp_path):
    inside = _meeting("AAMk-inside", NOW - timedelta(days=2))
    project = _meeting("AAMk-project", NOW - timedelta(days=3), scope=PROJECT)
    too_old = _meeting("AAMk-old", SINCE - timedelta(hours=1))
    running = _meeting("AAMk-running", NOW - timedelta(minutes=30))
    dropped = _meeting("mtg_dropped", NOW - timedelta(days=1), ref=None)
    daemon = _daemon(tmp_path, inside, project, too_old, running, dropped)

    chosen, refusals = select_meetings(daemon.storage, scopes=(PERSONAL, PROJECT), since=SINCE, now=NOW)

    assert [(c.scope, c.meeting.meeting_id) for c in chosen] == [
        (PERSONAL, "AAMk-inside"),
        (PROJECT, "AAMk-project"),
    ]
    assert refusals == ()


@pytest.mark.parametrize(
    "state, retryable, tried",
    [
        (TranscriptStateKind.FETCHED, False, False),
        (TranscriptStateKind.REFUSED, False, False),
        (TranscriptStateKind.REFUSED, True, True),
        (TranscriptStateKind.ABSENT, False, True),
        (TranscriptStateKind.SKIPPED, False, True),
    ],
)
def test_a_recorded_state_decides_whether_a_meeting_is_tried_again(tmp_path, state, retryable, tried):
    held = _meeting("AAMk-one", NOW - timedelta(days=1))
    daemon = _daemon(tmp_path, held)
    daemon.storage.record_transcript_state(PERSONAL, "AAMk-one", state, "earlier", retryable=retryable)
    source = Source()

    _acquire(daemon, source)

    assert source.asked == ([["AAMk-one"]] if tried else [])


@pytest.mark.parametrize("fmt", list(CaptureFormat))
def test_a_meeting_with_a_capture_from_any_source_is_not_tried(tmp_path, fmt):
    held = _meeting("AAMk-one", NOW - timedelta(days=1))
    daemon = _daemon(tmp_path, held)
    daemon.storage.write_capture("x\n", scope=PERSONAL, name=capture_name("AAMk-one", held.start, fmt))
    source = Source()

    report = _acquire(daemon, source)

    assert source.asked == [] and report.entries == ()


def test_a_meeting_missed_earlier_is_tried_although_this_run_recorded_nothing(tmp_path):
    held = _meeting("AAMk-one", NOW - timedelta(days=5))
    daemon = _daemon(tmp_path, held)
    daemon.storage.record_transcript_state(
        PERSONAL, "AAMk-one", TranscriptStateKind.REFUSED, "server error", retryable=True,
    )
    source = Source()

    report = _acquire(daemon, source)

    assert source.asked == [["AAMk-one"]]
    assert source.since == SINCE
    assert [e.meeting_id for e in report.captured] == ["AAMk-one"]


# ── What is kept ─────────────────────────────────────────────────────────────


def test_a_capture_is_written_verbatim_and_its_state_recorded(tmp_path):
    held = _meeting("AAMk-one", NOW - timedelta(days=1), scope=PROJECT)
    daemon = _daemon(tmp_path, held)
    record = daemon.storage.read_artifact(scope=PROJECT, artifact=MEETINGS, name=record_name("AAMk-one", held.start))

    report = _acquire(daemon, Source())

    name = capture_name("AAMk-one", held.start, CaptureFormat.GRAPH)
    assert daemon.storage.read_artifact(scope=PROJECT, artifact=CAPTURES, name=name) == TEXT.encode()
    state = daemon.storage.transcript_states(PROJECT)["AAMk-one"]
    assert isinstance(state, TranscriptState)
    assert state.state == "fetched" and state.closes and state.at == NOW
    (entry,) = report.captured
    assert entry.capture_name == name and entry.attributed is True
    assert report.awaiting_ingestion == ((PROJECT, "AAMk-one"),)
    assert daemon.storage.read_artifact(
        scope=PROJECT, artifact=MEETINGS, name=record_name("AAMk-one", held.start)
    ) == record, "the record is untouched"


def test_an_unattributed_capture_is_reported_as_such(tmp_path):
    held = _meeting("AAMk-one", NOW - timedelta(days=1))
    daemon = _daemon(tmp_path, held)
    source = Source({"AAMk-one": TranscriptCaptured("AAMk-one", _capture(held, attributed=False))})

    report = _acquire(daemon, source)

    (entry,) = report.unattributed
    assert "without speaker names" in entry.reason


def test_every_outcome_becomes_its_state_row(tmp_path):
    held = [_meeting(f"AAMk-{n}", NOW - timedelta(days=1, hours=n)) for n in range(3)]
    daemon = _daemon(tmp_path, *held)
    source = Source(
        {
            "AAMk-0": TranscriptAbsent("AAMk-0", "not recorded"),
            "AAMk-1": TranscriptSkipped("AAMk-1", "still being produced"),
            "AAMk-2": TranscriptRefused("AAMk-2", "tenant switch", retryable=False),
        }
    )

    _acquire(daemon, source)

    states = daemon.storage.transcript_states(PERSONAL)
    assert {k: (v.state, v.reason, v.retryable) for k, v in states.items()} == {
        "AAMk-0": ("absent", "not recorded", False),
        "AAMk-1": ("skipped", "still being produced", False),
        "AAMk-2": ("refused", "tenant switch", False),
    }


def test_meetings_a_provider_fault_left_unreached_get_no_row_and_are_deferred(tmp_path):
    held = [_meeting(f"AAMk-{n}", NOW - timedelta(days=1, hours=n)) for n in range(3)]
    daemon = _daemon(tmp_path, *held)
    source = Source(
        {"AAMk-1": TranscriptRefused("AAMk-1", "throttled", retryable=True, retry_after=timedelta(seconds=30))},
        stop_after=2,
    )

    report = _acquire(daemon, source)

    assert source.asked == [["AAMk-2", "AAMk-1", "AAMk-0"]]
    states = daemon.storage.transcript_states(PERSONAL)
    assert {k: v.state for k, v in states.items()} == {"AAMk-2": "fetched", "AAMk-1": "refused"}
    assert states["AAMk-1"].retryable and states["AAMk-1"].retry_after == timedelta(seconds=30)
    assert report.deferred == ((PERSONAL, "AAMk-0"),)


def test_a_capture_that_appears_before_the_write_is_refused_as_a_duplicate(tmp_path):
    held = _meeting("AAMk-one", NOW - timedelta(days=1))
    daemon = _daemon(tmp_path, held)
    name = capture_name("AAMk-one", held.start, CaptureFormat.GRAPH)

    class Racing(Source):
        def acquire(self, meetings, *, since):
            daemon.storage.write_capture("the first\n", scope=PERSONAL, name=name)
            return super().acquire(meetings, since=since)

    report = _acquire(daemon, Racing())

    (entry,) = report.refused
    assert "never overwritten" in entry.reason and not entry.retryable
    assert daemon.storage.read_artifact(scope=PERSONAL, artifact=CAPTURES, name=name) == b"the first\n"


def test_an_unavailable_source_is_a_run_level_refusal_and_records_nothing(tmp_path):
    held = _meeting("AAMk-one", NOW - timedelta(days=1))
    daemon = _daemon(tmp_path, held)

    class Unavailable(Source):
        def acquire(self, meetings, *, since):
            raise TranscriptSourceUnavailable("no Graph connector is enrolled")

    report = _acquire(daemon, Unavailable())

    assert report.refusals == ("no Graph connector is enrolled",)
    assert report.deferred == ((PERSONAL, "AAMk-one"),)
    assert daemon.storage.transcript_states(PERSONAL) == {}


def test_a_scope_with_a_malformed_record_is_named_and_the_others_still_read(tmp_path):
    good = _meeting("AAMk-good", NOW - timedelta(days=1), scope=PROJECT)
    daemon = _daemon(tmp_path, good)
    bad_day = (NOW - timedelta(days=1)).date().isoformat()
    daemon.storage.write_artifact(
        b"meeting_id=broken\n", scope=PERSONAL, artifact=MEETINGS, name=f"{bad_day}-broken-x.md"
    )
    source = Source()

    report = _acquire(daemon, source)

    (refusal,) = report.refusals
    assert "personal" in refusal
    assert source.asked == [["AAMk-good"]]


def test_the_source_satisfies_the_port():
    assert isinstance(Source(), TranscriptSourcePort)


def test_between_reads_records_across_days(tmp_path):
    held = [_meeting(f"AAMk-{n}", NOW - timedelta(days=n, hours=1)) for n in range(1, 4)]
    daemon = _daemon(tmp_path, *held)
    found = MeetingRecords(daemon.storage).between(NOW - timedelta(days=2, hours=2), NOW, scope=PERSONAL)
    assert [r.meeting.meeting_id for r in found] == ["AAMk-2", "AAMk-1"]


# ── Review follow-ups ────────────────────────────────────────────────────────


def test_a_retryable_refusal_waits_out_the_providers_hint(tmp_path):
    held = _meeting("AAMk-one", NOW - timedelta(days=1))
    daemon = _daemon(tmp_path, held)
    daemon.storage.record_transcript_state(
        PERSONAL, "AAMk-one", TranscriptStateKind.REFUSED, "throttled", retryable=True,
        retry_after=timedelta(minutes=10),
    )

    early, _ = select_meetings(daemon.storage, scopes=(PERSONAL,), since=SINCE, now=NOW + timedelta(minutes=9))
    late, _ = select_meetings(daemon.storage, scopes=(PERSONAL,), since=SINCE, now=NOW + timedelta(minutes=11))

    assert early == ()
    assert [c.meeting.meeting_id for c in late] == ["AAMk-one"]


@pytest.mark.parametrize("earlier", [None, TranscriptStateKind.REFUSED, TranscriptStateKind.ABSENT])
def test_a_graph_capture_with_no_fetched_row_is_recorded_and_handed_to_ingestion(tmp_path, earlier):
    """`write_capture` succeeded and the state write did not (or the process died)."""
    held = _meeting("AAMk-one", NOW - timedelta(days=1))
    daemon = _daemon(tmp_path, held)
    daemon.storage.write_capture(TEXT, scope=PERSONAL, name=capture_name("AAMk-one", held.start, CaptureFormat.GRAPH))
    if earlier is not None:
        daemon.storage.record_transcript_state(PERSONAL, "AAMk-one", earlier, "before the crash", retryable=True)
    source = Source()

    report = _acquire(daemon, source)

    assert source.asked == []
    assert daemon.storage.transcript_states(PERSONAL)["AAMk-one"].state == "fetched"
    assert report.awaiting_ingestion == ((PERSONAL, "AAMk-one"),)


def test_a_graph_capture_outside_the_reach_back_with_no_row_is_still_awaiting_ingestion(tmp_path):
    old = _meeting("AAMk-old", SINCE - timedelta(days=3))
    daemon = _daemon(tmp_path, old)
    daemon.storage.write_capture(TEXT, scope=PERSONAL, name=capture_name("AAMk-old", old.start, CaptureFormat.GRAPH))

    report = _acquire(daemon, Source())

    assert report.awaiting_ingestion == ((PERSONAL, "AAMk-old"),)


def test_a_capture_write_that_raises_oserror_is_a_retryable_refusal_and_the_loop_continues(tmp_path, monkeypatch):
    held = [_meeting(f"AAMk-{n}", NOW - timedelta(days=1, hours=n)) for n in range(2)]
    daemon = _daemon(tmp_path, *held)
    real = daemon.storage.write_capture

    def full_disk(body, *, scope, name):
        if name.startswith(capture_stem("AAMk-1", held[1].start)):
            raise OSError(28, "No space left on device")
        return real(body, scope=scope, name=name)

    monkeypatch.setattr(daemon.storage, "write_capture", full_disk)

    report = _acquire(daemon, Source())

    (refused,) = report.refused
    assert refused.meeting_id == "AAMk-1" and refused.retryable
    assert "OSError" in refused.reason
    assert [e.meeting_id for e in report.captured] == ["AAMk-0"]


def test_one_unreadable_scope_is_named_and_the_others_still_acquire(tmp_path, monkeypatch):
    held = [_meeting("AAMk-personal", NOW - timedelta(days=1)), _meeting("AAMk-project", NOW - timedelta(days=1), scope=PROJECT)]
    daemon = _daemon(tmp_path, *held)
    real = daemon.storage.transcript_states

    def broken(scope):
        if scope == PROJECT:
            raise RuntimeError("the store answered nonsense")
        return real(scope)

    monkeypatch.setattr(daemon.storage, "transcript_states", broken)
    source = Source()

    report = _acquire(daemon, source)

    assert source.asked == [["AAMk-personal"]]
    assert any(str(PROJECT) in refusal and "RuntimeError" in refusal for refusal in report.refusals)
    assert report.awaiting_ingestion == ((PERSONAL, "AAMk-personal"),)


def test_a_transcript_state_survives_closing_and_reopening_the_store(tmp_path):
    first = build(tmp_path, "alpha", now=lambda: NOW)
    first.storage.record_transcript_state(
        PROJECT, "AAMk-one", TranscriptStateKind.REFUSED, "throttled", retryable=True,
        retry_after=timedelta(seconds=90),
    )
    first.storage._db.close()

    reopened = build(tmp_path, "alpha", now=lambda: NOW + timedelta(hours=1))

    state = reopened.storage.transcript_states(PROJECT)["AAMk-one"]
    assert (state.state, state.reason, state.retryable, state.retry_after, state.at) == (
        "refused", "throttled", True, timedelta(seconds=90), NOW,
    )
    assert reopened.storage.transcript_states(PERSONAL) == {}


def test_an_outcome_the_port_does_not_declare_is_a_named_retryable_refusal(tmp_path):
    held = _meeting("AAMk-one", NOW - timedelta(days=1))
    daemon = _daemon(tmp_path, held)

    class Odd:
        meeting_id = "AAMk-one"

    report = _acquire(daemon, Source({"AAMk-one": Odd()}))  # type: ignore[dict-item]

    (refused,) = report.refused
    assert "Odd" in refused.reason and refused.retryable
