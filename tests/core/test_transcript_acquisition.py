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
    MAX_PART,
    MEETINGS,
    MeetingRecords,
    capture_name,
    capture_stem,
    has_capture,
    is_graph_capture,
    record_name,
)
from pm_ai.core.transcript_acquisition import acquire_transcripts, select_meetings
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.storage_tiers import CAPTURES
from pm_ai.domain.transcripts import (
    CaptureFormat,
    PartState,
    TranscriptAbsent,
    TranscriptCapture,
    TranscriptCaptured,
    TranscriptOutcome,
    TranscriptRefused,
    TranscriptSkipped,
    TranscriptState,
    TranscriptStateKind,
)
from pm_ai.ports import CaptureAlreadyExists, TranscriptSourcePort, TranscriptSourceUnavailable
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
        self.stored: dict = {}

    def acquire(self, meetings: Sequence[Meeting], *, since: datetime, stored=None) -> tuple[TranscriptOutcome, ...]:
        self.asked.append([m.meeting_id for m in meetings])
        self.since = since
        self.stored = dict(stored or {})
        reached = meetings if self.stop_after is None else meetings[: self.stop_after]
        return tuple(
            self.answers.get(m.meeting_id) or TranscriptCaptured(m.meeting_id, (_capture(m),))
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


def test_part_1_keeps_the_unnumbered_name_and_later_parts_are_numbered():
    start = NOW - timedelta(days=1)
    stem = capture_stem("AAMk-one", start)
    assert capture_name("AAMk-one", start, CaptureFormat.GRAPH, part=1) == f"{stem}-graph.vtt"
    assert capture_name("AAMk-one", start, CaptureFormat.GRAPH, part=2) == f"{stem}-graph-2.vtt"
    assert capture_name("AAMk-one", start, CaptureFormat.GRAPH, part=17) == f"{stem}-graph-17.vtt"
    assert has_capture([f"{stem}-graph-2.vtt"], stem)


def test_a_nine_digit_part_number_fits_the_writers_limit():
    longest = capture_name("x" * 500, NOW, CaptureFormat.GRAPH, part=999_999_999)
    assert len(longest.encode()) <= CAPTURE_NAME_LIMIT


@pytest.mark.parametrize("part", [0, -1, 1_000_000_000, True])
def test_a_part_number_out_of_range_is_refused(part):
    with pytest.raises(ValueError):
        capture_name("AAMk-one", NOW, CaptureFormat.GRAPH, part=part)


def test_a_dropped_file_has_no_part_2():
    with pytest.raises(ValueError):
        capture_name("AAMk-one", NOW, CaptureFormat.MANUAL_VTT, part=2)


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
    assert entry.capture_names == (name,) and entry.attributed is True
    assert report.awaiting_ingestion == ((PROJECT, "AAMk-one"),)
    assert daemon.storage.read_artifact(
        scope=PROJECT, artifact=MEETINGS, name=record_name("AAMk-one", held.start)
    ) == record, "the record is untouched"


def test_an_unattributed_capture_is_reported_as_such(tmp_path):
    held = _meeting("AAMk-one", NOW - timedelta(days=1))
    daemon = _daemon(tmp_path, held)
    source = Source({"AAMk-one": TranscriptCaptured("AAMk-one", (_capture(held, attributed=False),))})

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
        def acquire(self, meetings, *, since, stored=None):
            daemon.storage.write_capture("the first\n", scope=PERSONAL, name=name)
            return super().acquire(meetings, since=since, stored=stored)

    report = _acquire(daemon, Racing())

    (entry,) = report.refused
    assert "never overwritten" in entry.reason and not entry.retryable
    assert daemon.storage.read_artifact(scope=PERSONAL, artifact=CAPTURES, name=name) == b"the first\n"


def test_an_unavailable_source_is_a_run_level_refusal_and_records_nothing(tmp_path):
    held = _meeting("AAMk-one", NOW - timedelta(days=1))
    daemon = _daemon(tmp_path, held)

    class Unavailable(Source):
        def acquire(self, meetings, *, since, stored=None):
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


# ── Story 33g: every part of a transcript is kept ────────────────────────────
#
# A 30-minute meeting at 08:30 UTC: it ends at 09:00 and its placement window
# (end plus PLACEMENT_SLACK) at 09:15. Times below are minutes from 08:30.

SLOT = datetime(2026, 9, 7, 8, 30, tzinfo=timezone.utc)


def _slot_meeting(meeting_id: str = "AAMk-one") -> Meeting:
    return _meeting(meeting_id, SLOT, minutes=30)


def _part(meeting: Meeting, transcript_id: str, created: int, ended: int, *, attributed: bool = True) -> TranscriptCapture:
    return TranscriptCapture(
        meeting_id=meeting.meeting_id,
        transcript_id=transcript_id,
        created=meeting.start + timedelta(minutes=created),
        ended=meeting.start + timedelta(minutes=ended),
        text=f"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n<v Sam>{transcript_id}</v>\n",
        attributed=attributed,
        format=CaptureFormat.GRAPH,
    )


def _at(daemon, source, minutes: int):
    now = SLOT + timedelta(minutes=minutes)
    return acquire_transcripts(source, daemon.storage, scopes=(PERSONAL,), since=now - timedelta(days=7), now=now)


def _names(meeting: Meeting, *parts: int) -> list[str]:
    return [capture_name(meeting.meeting_id, meeting.start, CaptureFormat.GRAPH, part=n) for n in parts]


def _parts_of(daemon, meeting_id: str = "AAMk-one"):
    return daemon.storage.transcript_parts(PERSONAL).get(meeting_id, ())


def test_a_restarted_transcription_is_stored_as_two_numbered_byte_identical_parts(tmp_path):
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    first, second = _part(held, "T-1", 3, 9), _part(held, "T-2", 16, 32)
    source = Source({"AAMk-one": TranscriptCaptured("AAMk-one", (first, second))})

    report = _at(daemon, source, 40)

    one, two = _names(held, 1, 2)
    assert daemon.storage.read_artifact(scope=PERSONAL, artifact=CAPTURES, name=one) == first.text.encode()
    assert daemon.storage.read_artifact(scope=PERSONAL, artifact=CAPTURES, name=two) == second.text.encode()
    assert [(p.part, p.transcript_id, p.state, p.ingested) for p in _parts_of(daemon)] == [
        (1, "T-1", PartState.STORED, False),
        (2, "T-2", PartState.STORED, False),
    ]
    state = daemon.storage.transcript_states(PERSONAL)["AAMk-one"]
    assert state.state == "fetched" and one in state.reason and two in state.reason
    (entry,) = report.captured
    assert entry.capture_names == (one, two)
    assert report.awaiting_ingestion == ((PERSONAL, "AAMk-one"),)


def test_one_part_is_named_as_33e_named_it(tmp_path):
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    source = Source({"AAMk-one": TranscriptCaptured("AAMk-one", (_part(held, "T-1", 1, 29),))})

    _at(daemon, source, 40)

    stem = capture_stem("AAMk-one", held.start)
    stored = [n for n in daemon.storage.list_collection(scope=PERSONAL, artifact=CAPTURES) if n.startswith(stem)]
    assert stored == [f"{stem}-graph.vtt"]
    assert [p.part for p in _parts_of(daemon)] == [1]


def test_overlapping_transcripts_with_nothing_stored_become_the_meetings_refused_row(tmp_path):
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    source = Source({"AAMk-one": TranscriptRefused("AAMk-one", "T-a and T-b overlap: duplicates", retryable=False)})

    _at(daemon, source, 40)
    again = Source()
    _at(daemon, again, 300)

    state = daemon.storage.transcript_states(PERSONAL)["AAMk-one"]
    assert (state.state, state.retryable) == ("refused", False) and "duplicates" in state.reason
    assert again.asked == [], "non-retryable: not asked again"


def _with_part_1(tmp_path, *, at: int = 40):
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    _at(daemon, Source({"AAMk-one": TranscriptCaptured("AAMk-one", (_part(held, "T-1", 3, 9),))}), at)
    daemon.storage.record_transcript_state(
        PERSONAL, "AAMk-one", TranscriptStateKind.INGESTED, "part 1 ingested", retryable=False
    )
    return held, daemon


def test_a_refusal_about_a_new_transcript_keeps_the_rows_and_is_named_in_the_run(tmp_path):
    held, daemon = _with_part_1(tmp_path, at=35)
    source = Source({"AAMk-one": TranscriptRefused("AAMk-one", "T-1 and T-dup overlap: duplicates", retryable=False)})

    report = _at(daemon, source, 40)

    assert source.asked == [["AAMk-one"]]
    assert [p.transcript_id for p in source.stored["AAMk-one"]] == ["T-1"]
    assert daemon.storage.transcript_states(PERSONAL)["AAMk-one"].state == "ingested", "row kept"
    assert report.entries == ()
    assert any("AAMk-one" in r and "T-dup" in r for r in report.refusals)
    assert [p.part for p in _parts_of(daemon)] == [1]


@pytest.mark.parametrize(
    "answer",
    [TranscriptSkipped("AAMk-one", "T-2 has no end instant yet"), TranscriptAbsent("AAMk-one", "no new part")],
    ids=["unfinished part", "nothing new"],
)
def test_a_skip_or_absence_leaves_a_meeting_holding_parts_as_it_was(tmp_path, answer):
    held, daemon = _with_part_1(tmp_path, at=35)

    report = _at(daemon, Source({"AAMk-one": answer}), 40)

    assert daemon.storage.transcript_states(PERSONAL)["AAMk-one"].state == "ingested"
    assert report.entries == () and report.refusals == ()
    assert [p.part for p in _parts_of(daemon)] == [1]


def test_an_unfinished_part_is_asked_again_even_after_the_window(tmp_path):
    held, daemon = _with_part_1(tmp_path, at=35)
    _at(daemon, Source({"AAMk-one": TranscriptSkipped("AAMk-one", "T-2 still being produced")}), 60)

    again = Source({"AAMk-one": TranscriptCaptured("AAMk-one", (_part(held, "T-2", 16, 32),))})
    _at(daemon, again, 120)

    assert again.asked == [["AAMk-one"]], "a skip is not an ask that closes the meeting"
    assert [p.transcript_id for p in _parts_of(daemon)] == ["T-1", "T-2"]


def test_a_later_part_is_stored_as_part_2_on_a_later_run_and_the_row_is_fetched(tmp_path):
    """Part 1 stored at 09:05 (35), window ends 09:15 (45); part 2 listed next run."""
    held, daemon = _with_part_1(tmp_path, at=35)
    source = Source({"AAMk-one": TranscriptCaptured("AAMk-one", (_part(held, "T-2", 16, 32),))})

    report = _at(daemon, source, 275)

    assert source.asked == [["AAMk-one"]]
    one, two = _names(held, 1, 2)
    assert two in daemon.storage.list_collection(scope=PERSONAL, artifact=CAPTURES)
    assert [(p.part, p.transcript_id, p.state) for p in _parts_of(daemon)] == [
        (1, "T-1", PartState.STORED),
        (2, "T-2", PartState.STORED),
    ]
    state = daemon.storage.transcript_states(PERSONAL)["AAMk-one"]
    assert state.state == "fetched" and one in state.reason and two in state.reason
    assert report.captured[0].capture_names == (two,)
    assert report.awaiting_ingestion == ((PERSONAL, "AAMk-one"),)


def test_a_meeting_asked_after_its_window_is_not_asked_again(tmp_path):
    held, daemon = _with_part_1(tmp_path, at=35)
    _at(daemon, Source({"AAMk-one": TranscriptAbsent("AAMk-one", "no new part")}), 50)

    later = Source()
    _at(daemon, later, 300)

    assert later.asked == []
    assert daemon.storage.transcript_part_asks(PERSONAL)["AAMk-one"] == SLOT + timedelta(minutes=50)


def test_a_meeting_asked_before_its_window_ended_is_asked_again(tmp_path):
    held, daemon = _with_part_1(tmp_path, at=35)

    later = Source({"AAMk-one": TranscriptAbsent("AAMk-one", "no new part")})
    _at(daemon, later, 300)

    assert later.asked == [["AAMk-one"]]


def test_a_new_transcript_earlier_than_a_pending_part_is_not_stored(tmp_path):
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    daemon.storage.record_pending_part(
        PERSONAL, "AAMk-one", 1, transcript_id="T-late", created=SLOT + timedelta(minutes=16),
        ended=SLOT + timedelta(minutes=32), reason="disk full",
    )
    source = Source({"AAMk-one": TranscriptCaptured("AAMk-one", (_part(held, "T-early", 3, 9),))})

    _at(daemon, source, 40)

    assert [(p.part, p.transcript_id, p.state) for p in _parts_of(daemon)] == [(1, "T-late", PartState.PENDING)]
    state = daemon.storage.transcript_states(PERSONAL)["AAMk-one"]
    assert state.state == "refused" and "T-early" in state.reason


def test_a_pending_part_whose_capture_is_on_disk_is_marked_stored_at_selection(tmp_path):
    """Crash after capture: the row says pending, the capture is there."""
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    daemon.storage.record_pending_part(
        PERSONAL, "AAMk-one", 1, transcript_id="T-1", created=SLOT + timedelta(minutes=3),
        ended=SLOT + timedelta(minutes=9),
    )
    daemon.storage.write_capture(TEXT, scope=PERSONAL, name=_names(held, 1)[0])
    source = Source({"AAMk-one": TranscriptAbsent("AAMk-one", "no new part")})

    report = _at(daemon, source, 40)

    ((part,),) = [_parts_of(daemon)]
    assert (part.part, part.state) == (1, PartState.STORED)
    assert [p.transcript_id for p in source.stored["AAMk-one"]] == ["T-1"], "told it is stored"
    assert daemon.storage.transcript_states(PERSONAL)["AAMk-one"].state == "fetched"
    assert report.awaiting_ingestion == ((PERSONAL, "AAMk-one"),)


def test_a_pending_part_with_no_capture_is_fetched_again_under_the_same_number(tmp_path):
    """Crash before capture: the row says pending, nothing on disk."""
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    _at(daemon, Source({"AAMk-one": TranscriptCaptured("AAMk-one", (_part(held, "T-1", 3, 9),))}), 31)
    daemon.storage.record_pending_part(
        PERSONAL, "AAMk-one", 2, transcript_id="T-2", created=SLOT + timedelta(minutes=16),
        ended=SLOT + timedelta(minutes=32),
    )
    source = Source({"AAMk-one": TranscriptCaptured("AAMk-one", (_part(held, "T-2", 16, 32),))})

    _at(daemon, source, 300)

    assert source.asked == [["AAMk-one"]], "a pending part keeps the meeting asked"
    assert [p.transcript_id for p in source.stored["AAMk-one"]] == ["T-1"], "the pending one is not stored"
    assert [(p.part, p.transcript_id, p.state) for p in _parts_of(daemon)] == [
        (1, "T-1", PartState.STORED),
        (2, "T-2", PartState.STORED),
    ]
    assert _names(held, 2)[0] in daemon.storage.list_collection(scope=PERSONAL, artifact=CAPTURES)


def test_a_retryable_write_refusal_on_part_2_keeps_part_1_and_leaves_part_2_pending(tmp_path, monkeypatch):
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    real = daemon.storage.write_capture
    two = _names(held, 2)[0]

    def full_disk(body, *, scope, name):
        if name == two:
            raise OSError(28, "No space left on device")
        return real(body, scope=scope, name=name)

    monkeypatch.setattr(daemon.storage, "write_capture", full_disk)
    source = Source({"AAMk-one": TranscriptCaptured("AAMk-one", (_part(held, "T-1", 3, 9), _part(held, "T-2", 16, 32)))})

    report = _at(daemon, source, 300)

    parts = _parts_of(daemon)
    assert [(p.part, p.state) for p in parts] == [(1, PartState.STORED), (2, PartState.PENDING)]
    assert parts[1].reason is not None and "OSError" in parts[1].reason
    assert daemon.storage.transcript_states(PERSONAL)["AAMk-one"].state == "fetched"
    assert any("OSError" in refusal for refusal in report.refusals)

    monkeypatch.setattr(daemon.storage, "write_capture", real)
    retry = Source({"AAMk-one": TranscriptCaptured("AAMk-one", (_part(held, "T-2", 16, 32),))})
    _at(daemon, retry, 600)

    assert retry.asked == [["AAMk-one"]], "asked again while a part is pending, past the window too"
    assert [(p.part, p.state) for p in _parts_of(daemon)] == [(1, PartState.STORED), (2, PartState.STORED)]


def test_a_non_retryable_write_refusal_drops_the_pending_part_and_names_it_lost(tmp_path):
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    name = _names(held, 1)[0]

    class Racing(Source):
        def acquire(self, meetings, *, since, stored=None):
            daemon.storage.write_capture("the first\n", scope=PERSONAL, name=name)
            return super().acquire(meetings, since=since, stored=stored)

    report = _at(daemon, Racing({"AAMk-one": TranscriptCaptured("AAMk-one", (_part(held, "T-1", 3, 9),))}), 40)

    assert _parts_of(daemon) == ()
    (entry,) = report.refused
    assert "lost" in entry.reason and "never overwritten" in entry.reason and not entry.retryable


def test_a_capture_written_before_parts_existed_is_part_1_of_a_closed_meeting(tmp_path):
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    daemon.storage.write_capture(TEXT, scope=PERSONAL, name=_names(held, 1)[0])
    source = Source()

    report = _at(daemon, source, 40)

    assert source.asked == [], "inside its window, and still not asked"
    assert _parts_of(daemon) == ()
    assert report.awaiting_ingestion == ((PERSONAL, "AAMk-one"),)


def test_a_hand_dropped_capture_beside_graph_parts_stops_the_asking(tmp_path):
    held, daemon = _with_part_1(tmp_path, at=35)
    daemon.storage.write_capture(
        TEXT, scope=PERSONAL, name=capture_name("AAMk-one", held.start, CaptureFormat.MANUAL_VTT)
    )
    source = Source()

    _at(daemon, source, 40)

    assert source.asked == []


def test_the_parts_and_asks_survive_closing_and_reopening_the_store(tmp_path):
    held, daemon = _with_part_1(tmp_path, at=50)
    daemon.storage._db.close()

    reopened = build(tmp_path, "alpha", now=lambda: NOW)

    assert [p.transcript_id for p in reopened.storage.transcript_parts(PERSONAL)["AAMk-one"]] == ["T-1"]
    assert reopened.storage.transcript_part_asks(PERSONAL)["AAMk-one"] == SLOT + timedelta(minutes=50)


@pytest.mark.parametrize(
    "tail, graph",
    [
        ("-graph.vtt", True),
        ("-graph-2.vtt", True),
        (f"-graph-{MAX_PART}.vtt", True),
        ("-graph-1.vtt", False),
        ("-graph-0.vtt", False),
        ("-graph-02.vtt", False),
        (f"-graph-{MAX_PART + 1}.vtt", False),
        ("-graph-x.vtt", False),
        ("-manual.vtt", False),
    ],
)
def test_is_graph_capture_accepts_only_names_capture_name_writes(tail, graph):
    stem = capture_stem("AAMk-one", NOW)
    assert is_graph_capture(stem + tail, stem) is graph


def test_one_transcript_twice_in_one_answer_is_refused():
    held = _slot_meeting()
    with pytest.raises(ValueError, match="twice"):
        TranscriptCaptured("AAMk-one", (_part(held, "T-1", 3, 9), _part(held, "T-1", 3, 9)))


def test_a_part_row_must_be_numbered_and_in_utc():
    from pm_ai.domain.transcripts import TranscriptPart

    good = dict(meeting_id="m", part=1, transcript_id="T", created=SLOT, ended=SLOT, state=PartState.STORED)
    TranscriptPart(**good)
    with pytest.raises(ValueError):
        TranscriptPart(**{**good, "part": 0})
    with pytest.raises(ValueError):
        TranscriptPart(**{**good, "created": SLOT.replace(tzinfo=None)})


@pytest.mark.parametrize("row", [TranscriptStateKind.INGESTED, TranscriptStateKind.INGEST_FAILED])
def test_a_repaired_part_puts_an_ingestion_row_back_to_fetched(tmp_path, row):
    held, daemon = _with_part_1(tmp_path, at=35)
    daemon.storage.record_transcript_state(PERSONAL, "AAMk-one", row, "earlier", retryable=True)
    daemon.storage.record_pending_part(
        PERSONAL, "AAMk-one", 2, transcript_id="T-2", created=SLOT + timedelta(minutes=16),
        ended=SLOT + timedelta(minutes=32),
    )
    daemon.storage.write_capture(TEXT, scope=PERSONAL, name=_names(held, 2)[0])

    report = _at(daemon, Source({"AAMk-one": TranscriptAbsent("AAMk-one", "no new part")}), 40)

    state = daemon.storage.transcript_states(PERSONAL)["AAMk-one"]
    assert (state.state, state.attempts) == ("fetched", 0)
    assert [p.state for p in _parts_of(daemon)] == [PartState.STORED, PartState.STORED]
    assert report.awaiting_ingestion == ((PERSONAL, "AAMk-one"),)


def test_a_retryable_refusal_after_the_window_records_no_ask_and_is_asked_again(tmp_path):
    held, daemon = _with_part_1(tmp_path, at=35)
    _at(daemon, Source({"AAMk-one": TranscriptRefused("AAMk-one", "throttled", retryable=True)}), 60)

    assert daemon.storage.transcript_part_asks(PERSONAL)["AAMk-one"] == SLOT + timedelta(minutes=35), (
        "the ask from before the window stands; the retryable refusal records none"
    )
    later = Source({"AAMk-one": TranscriptAbsent("AAMk-one", "no new part")})
    _at(daemon, later, 300)
    assert later.asked == [["AAMk-one"]]


def test_a_repair_that_raises_names_the_meeting_and_selection_goes_on(tmp_path, monkeypatch):
    held, other = _slot_meeting("AAMk-one"), _meeting("AAMk-two", SLOT - timedelta(hours=2), minutes=30)
    daemon = _daemon(tmp_path, held, other)
    daemon.storage.record_pending_part(
        PERSONAL, "AAMk-one", 1, transcript_id="T-1", created=SLOT, ended=SLOT + timedelta(minutes=9),
    )
    daemon.storage.write_capture(TEXT, scope=PERSONAL, name=_names(held, 1)[0])

    def broken(*args, **kwargs):
        raise RuntimeError("the store answered nonsense")

    monkeypatch.setattr(daemon.storage, "mark_part_stored", broken)
    source = Source()

    report = _at(daemon, source, 40)

    assert source.asked == [["AAMk-two"]]
    assert any("AAMk-one" in r and "RuntimeError" in r for r in report.refusals)


@pytest.mark.parametrize("call", ["mark_part_stored", "drop_pending_part", "second record_pending_part"])
def test_a_part_write_that_raises_costs_this_meeting_and_the_run_goes_on(tmp_path, monkeypatch, call):
    first, second = _slot_meeting("AAMk-one"), _meeting("AAMk-two", SLOT - timedelta(hours=2), minutes=30)
    daemon = _daemon(tmp_path, first, second)
    storage = daemon.storage
    real_pending, real_write = storage.record_pending_part, storage.write_capture
    one = _names(first, 1)[0]

    def broken(*args, **kwargs):
        raise RuntimeError("the store answered nonsense")

    if call == "mark_part_stored":
        real_mark = storage.mark_part_stored
        monkeypatch.setattr(
            storage, "mark_part_stored",
            lambda scope, mid, part: broken() if mid == "AAMk-one" else real_mark(scope, mid, part),
        )
    else:
        retryable = call != "drop_pending_part"

        def refused_write(body, *, scope, name):
            if name == one:
                if retryable:
                    raise OSError(28, "No space left on device")
                raise CaptureAlreadyExists(name)
            return real_write(body, scope=scope, name=name)

        monkeypatch.setattr(storage, "write_capture", refused_write)
        if call == "drop_pending_part":
            monkeypatch.setattr(storage, "drop_pending_part", broken)
        else:
            def pending(scope, mid, part, **fields):
                if fields.get("reason"):
                    broken()
                return real_pending(scope, mid, part, **fields)

            monkeypatch.setattr(storage, "record_pending_part", pending)

    report = _at(daemon, Source(), 40)

    assert {e.meeting_id: e.state for e in report.entries} == {
        "AAMk-one": TranscriptStateKind.REFUSED,
        "AAMk-two": TranscriptStateKind.FETCHED,
    }
    (refused,) = report.refused
    assert refused.retryable and call.split()[-1] in refused.reason


def test_an_ask_that_cannot_be_recorded_is_a_run_refusal(tmp_path, monkeypatch):
    held, daemon = _with_part_1(tmp_path, at=35)

    def broken(*args, **kwargs):
        raise RuntimeError("the store answered nonsense")

    monkeypatch.setattr(daemon.storage, "record_part_ask", broken)

    report = _at(daemon, Source({"AAMk-one": TranscriptAbsent("AAMk-one", "no new part")}), 60)

    assert any("AAMk-one" in r and "RuntimeError" in r for r in report.refusals)


def test_a_meeting_with_parts_whose_record_is_gone_is_not_offered_for_ingestion(tmp_path):
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    _at(daemon, Source({"AAMk-one": TranscriptCaptured("AAMk-one", (_part(held, "T-1", 3, 9),))}), 40)
    daemon.storage.remove_member(scope=PERSONAL, artifact=MEETINGS, name=record_name("AAMk-one", held.start))

    report = _at(daemon, Source(), 40)

    assert report.awaiting_ingestion == ()


# ── Story 33j: a refused part keeps the parts before it ──────────────────────

_T2_404 = "transcript T-2 was listed and its content answered 404: it is gone."


def _kept_with(held: Meeting, *parts: TranscriptCapture, refused: str = _T2_404) -> Source:
    return Source({held.meeting_id: TranscriptCaptured(held.meeting_id, parts, refused_part=refused)})


def test_part_1_is_stored_and_the_refused_part_2_is_named_in_the_row_and_the_run(tmp_path):
    """Row "Part 2 answers 404, nothing stored"."""
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    first = _part(held, "T-1", 3, 9)

    report = _at(daemon, _kept_with(held, first), 40)

    (one,) = _names(held, 1)
    assert daemon.storage.read_artifact(scope=PERSONAL, artifact=CAPTURES, name=one) == first.text.encode()
    assert [(p.part, p.transcript_id, p.state) for p in _parts_of(daemon)] == [(1, "T-1", PartState.STORED)]
    state = daemon.storage.transcript_states(PERSONAL)["AAMk-one"]
    assert state.state == "fetched" and one in state.reason
    assert state.reason.endswith(f"Not stored: {_T2_404}")
    (entry,) = report.captured
    assert entry.capture_names == (one,)
    assert any("AAMk-one" in r and "T-2" in r and "404" in r for r in report.refusals)
    assert report.awaiting_ingestion == ((PERSONAL, "AAMk-one"),)
    assert daemon.storage.transcript_part_asks(PERSONAL)["AAMk-one"] == SLOT + timedelta(minutes=40), (
        "a capture counts as asked"
    )


def test_parts_1_and_2_are_stored_when_part_3_is_refused(tmp_path):
    """Row "Part 3 of three refused"."""
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    refused = "transcript T-3's content is not UTF-8 text, so it is not stored."

    _at(daemon, _kept_with(held, _part(held, "T-1", 3, 9), _part(held, "T-2", 12, 18), refused=refused), 40)

    assert [(p.part, p.transcript_id) for p in _parts_of(daemon)] == [(1, "T-1"), (2, "T-2")]
    state = daemon.storage.transcript_states(PERSONAL)["AAMk-one"]
    assert state.state == "fetched" and state.reason.endswith(f"Not stored: {refused}")


def test_a_meeting_holding_part_1_stores_part_2_and_names_part_3(tmp_path):
    """Row "Meeting already holding part 1"."""
    held, daemon = _with_part_1(tmp_path, at=35)
    refused = "transcript T-3 was listed and its content answered 404: it is gone."

    report = _at(daemon, _kept_with(held, _part(held, "T-2", 12, 18), refused=refused), 40)

    one, two = _names(held, 1, 2)
    assert [(p.part, p.transcript_id) for p in _parts_of(daemon)] == [(1, "T-1"), (2, "T-2")]
    state = daemon.storage.transcript_states(PERSONAL)["AAMk-one"]
    assert state.state == "fetched" and one in state.reason and two in state.reason and "T-3" in state.reason
    assert any("T-3" in r for r in report.refusals)


def test_the_refused_part_refused_again_stores_nothing_and_keeps_the_row(tmp_path):
    """Row "Refused part tried again, still refused": inside the window."""
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    _at(daemon, _kept_with(held, _part(held, "T-1", 3, 9)), 31)
    before = daemon.storage.transcript_states(PERSONAL)["AAMk-one"]
    again = Source(
        {"AAMk-one": TranscriptRefused("AAMk-one", "transcript T-2 was listed and its content answered 404: it is gone.", retryable=False)}
    )

    report = _at(daemon, again, 40)

    assert again.asked == [["AAMk-one"]], "asked again: the run that stored part 1 started inside the window"
    assert [p.transcript_id for p in again.stored["AAMk-one"]] == ["T-1"]
    assert [p.transcript_id for p in _parts_of(daemon)] == ["T-1"]
    assert daemon.storage.transcript_states(PERSONAL)["AAMk-one"] == before, "row kept"
    assert report.entries == ()
    assert any("AAMk-one" in r and "T-2" in r for r in report.refusals)


def test_the_refused_part_downloading_later_is_stored_as_part_2(tmp_path):
    """Row "Refused part succeeds later": inside the window."""
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    _at(daemon, _kept_with(held, _part(held, "T-1", 3, 9)), 31)
    second = _part(held, "T-2", 12, 18)
    later = Source({"AAMk-one": TranscriptCaptured("AAMk-one", (second,))})

    report = _at(daemon, later, 40)

    assert later.asked == [["AAMk-one"]]
    (_, two) = _names(held, 1, 2)
    assert [(p.part, p.transcript_id, p.state) for p in _parts_of(daemon)] == [
        (1, "T-1", PartState.STORED),
        (2, "T-2", PartState.STORED),
    ]
    assert daemon.storage.read_artifact(scope=PERSONAL, artifact=CAPTURES, name=two) == second.text.encode()
    state = daemon.storage.transcript_states(PERSONAL)["AAMk-one"]
    assert state.state == "fetched" and "Not stored" not in state.reason
    assert report.awaiting_ingestion == ((PERSONAL, "AAMk-one"),)


def test_a_meeting_whose_part_1_was_kept_after_the_window_is_not_asked_again(tmp_path):
    """Row "Asked after the window": window ends at 45; part 1 kept at 50."""
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    _at(daemon, _kept_with(held, _part(held, "T-1", 3, 9)), 50)

    later = Source()
    _at(daemon, later, 300)

    assert later.asked == []
    assert [p.transcript_id for p in _parts_of(daemon)] == ["T-1"]


def test_a_lost_write_of_part_1_then_a_refused_part_2_names_both_in_a_refused_row(tmp_path):
    """Row "Write of part 1 lost, then part 2 refused"."""
    held = _slot_meeting()
    daemon = _daemon(tmp_path, held)
    (name,) = _names(held, 1)

    class Racing(Source):
        def acquire(self, meetings, *, since, stored=None):
            daemon.storage.write_capture("the first\n", scope=PERSONAL, name=name)
            return super().acquire(meetings, since=since, stored=stored)

    source = Racing(
        {"AAMk-one": TranscriptCaptured("AAMk-one", (_part(held, "T-1", 3, 9),), refused_part=_T2_404)}
    )
    report = _at(daemon, source, 40)

    assert _parts_of(daemon) == ()
    (entry,) = report.refused
    assert not entry.retryable
    assert "lost" in entry.reason and "never overwritten" in entry.reason
    assert "T-2" in entry.reason and "404" in entry.reason
    again = Source()
    _at(daemon, again, 41)
    assert again.asked == [], "non-retryable: not asked again"
