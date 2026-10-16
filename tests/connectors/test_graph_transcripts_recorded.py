"""Story 33e over a real tenant's answers: `transcripts_recorded.json` and `transcript_recorded.vtt`.

The listing is one page of a recurring meeting's transcripts, captured on
2026-10-15 through pm-ai's own Graph client and redacted; the `.vtt` is a real
transcript body as Graph returned it, redacted the same way. Both `_provenance`
notes are in the listing file. The hand-written `transcripts.json`,
`transcript.vtt` and `transcript_unattributed.txt` stay: the matrix tests in
`test_graph_transcripts.py` assert values written into them.

Only the listing and the body are recorded. The calendar event and the
join-link lookup are the hand-written templates, and **the time matching is
tested against an assumed calendar slot**: each occurrence asked about is this
file's own, at 08:30 UTC for 30 minutes — the slot every listed transcript
since 2026-09-10 was created in (08:30 to 08:33). The capture did not record
the calendar's slot itself.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote, urlsplit

from pm_ai.connectors.graph.client import GraphClient, GraphRequest, GraphResponse
from pm_ai.connectors.transcripts.graph_transcripts import VTT, GraphTranscriptAdapter
from pm_ai.core.transcripts import last_cue_end_minutes, read_transcript
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.transcripts import TranscriptCaptured, TranscriptRefused
from tests.connectors.recorded_graph import FIXTURES, INSTANCE, Auth, graph_time, ok, recorded

LISTING = recorded("transcripts_recorded.json")["page"]
ROWS: list[dict] = LISTING["value"]
VTT_TEXT = (FIXTURES / "transcript_recorded.vtt").read_text(encoding="utf-8")
TEMPLATES = recorded("transcripts.json")

ONLINE = ROWS[0]["meetingId"]
NOW = datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)
SLOT = timedelta(minutes=30)
PERSONAL = DataScope(ScopeKind.PERSONAL)
LISTING_PATH = f"/me/onlineMeetings/{quote(ONLINE, safe='')}/transcripts"


def _on(day: str) -> list[dict]:
    return [row for row in ROWS if row["createdDateTime"].startswith(day)]


class Routes:
    """The event and lookup from the templates; the listing as recorded; any
    transcript's content answered with the recorded body."""

    def __init__(self) -> None:
        self.requests: list[GraphRequest] = []

    def __call__(self, request: GraphRequest) -> GraphResponse:
        self.requests.append(request)
        path = urlsplit(request.url).path.removeprefix("/v1.0")
        if path.startswith("/me/events/"):
            event_id = unquote(path.removeprefix("/me/events/"))
            start = datetime.fromisoformat(event_id.removeprefix("occurrence-"))
            body = copy.deepcopy(TEMPLATES["event"])
            body.update(
                id=event_id,
                type="occurrence",
                start={"dateTime": f"{start:%Y-%m-%dT%H:%M:%S}.0000000", "timeZone": "UTC"},
                end={"dateTime": f"{start + SLOT:%Y-%m-%dT%H:%M:%S}.0000000", "timeZone": "UTC"},
            )
            return ok(body)
        if path == "/me/onlineMeetings":
            body = copy.deepcopy(TEMPLATES["online_meetings"])
            body["value"] = [dict(body["value"][0], id=ONLINE)]
            return ok(body)
        if path == LISTING_PATH:
            return ok(copy.deepcopy(LISTING))
        if path.startswith(LISTING_PATH + "/") and path.endswith("/content"):
            assert request.headers["Accept"] == VTT
            return GraphResponse(
                status=200, body=None, headers={"Content-Type": VTT},
                content=VTT_TEXT.encode("utf-8"),
            )
        raise AssertionError(f"no route for {request!r}")

    def asked(self, fragment: str) -> list[str]:
        return [r.url for r in self.requests if fragment in unquote(r.url)]

    def downloads(self) -> list[str]:
        return [r.url for r in self.requests if urlsplit(r.url).path.endswith("/content")]


def _occurrence(day: str) -> Meeting:
    start = datetime.fromisoformat(f"{day}T08:30:00+00:00")
    return Meeting(
        meeting_id=f"occurrence-{start.isoformat()}",
        title="Recurring sync-up",
        start=start,
        duration_minutes=int(SLOT.total_seconds() // 60),
        attendees=(),
        scope=PERSONAL,
        calendar_event_ref=f"occurrence-{start.isoformat()}",
    )


def _acquire(*days: str):
    graph = Routes()
    client = GraphClient(auth=Auth(), transport=graph, now=lambda: NOW)
    adapter = GraphTranscriptAdapter(client=client, instance=INSTANCE)
    return adapter.acquire([_occurrence(day) for day in days], since=SINCE), graph


def _content_url(transcript_id: str) -> str:
    return (
        f"https://graph.microsoft.com/v1.0{LISTING_PATH}/"
        f"{quote(transcript_id, safe='')}/content"
    )


# ── The listing ──────────────────────────────────────────────────────────────


def test_the_recorded_listing_is_newest_first_and_every_row_has_both_instants():
    created = [graph_time(row["createdDateTime"]) for row in ROWS]
    assert len(ROWS) == 20
    assert created == sorted(created, reverse=True)
    for row in ROWS:
        assert row["id"] and graph_time(row["endDateTime"]) > graph_time(row["createdDateTime"])


def test_the_2026_10_15_occurrence_holds_two_transcripts_that_do_not_overlap():
    later, earlier = _on("2026-10-15")
    assert graph_time(earlier["endDateTime"]) < graph_time(later["createdDateTime"])
    assert [graph_time(row["createdDateTime"]).strftime("%H:%M:%S") for row in (earlier, later)] == [
        "08:33:21",
        "08:46:21",
    ]


def test_the_restarted_occurrence_is_refused_naming_both_and_nothing_is_downloaded():
    """Today's rule for two transcripts in one occurrence: none is stored."""
    (outcome,), graph = _acquire("2026-10-15")

    assert isinstance(outcome, TranscriptRefused)
    assert not outcome.retryable
    assert "a recording restarted — so none is stored" in outcome.reason
    on_the_day = {row["id"] for row in _on("2026-10-15")}
    named = {row["id"] for row in ROWS if row["id"] in outcome.reason}
    assert named == on_the_day and len(named) == 2
    assert graph.downloads() == []


def test_occurrences_with_one_transcript_each_are_captured_and_the_listing_is_read_once():
    outcomes, graph = _acquire("2026-10-08", "2026-10-12", "2026-10-15")

    assert [type(outcome) for outcome in outcomes] == [
        TranscriptCaptured,
        TranscriptCaptured,
        TranscriptRefused,
    ]
    matched = []
    for outcome, day in zip(outcomes[:2], ("2026-10-08", "2026-10-12"), strict=True):
        (row,) = _on(day)
        assert outcome.capture.transcript_id == row["id"]
        assert outcome.capture.created == graph_time(row["createdDateTime"])
        assert outcome.capture.text == VTT_TEXT
        matched.append(row["id"])
    assert graph.downloads() == [_content_url(transcript) for transcript in matched]
    listings = [r.url for r in graph.requests if urlsplit(r.url).path.endswith("/transcripts")]
    assert len(listings) == 1, "listed once, and its next page never asked for"
    assert graph.asked("$skiptoken") == [], (
        "newest first, with a row older than the reach-back on the first page"
    )


# ── The body ─────────────────────────────────────────────────────────────────


def test_the_recorded_body_reads_as_198_cues_each_with_its_speaker():
    cues = [line for line in VTT_TEXT.splitlines() if "-->" in line]
    labels = re.findall(r"<v ([^>]+)>", VTT_TEXT)
    assert len(cues) == len(labels) == 198

    utterances = read_transcript(VTT_TEXT, name="transcript_recorded.vtt")

    assert len(utterances) == 198
    assert [u.speaker_handle for u in utterances] == labels
    assert {u.speaker_handle for u in utterances} == {
        "Speaker 1",
        "Speaker 2",
        "Speaker 3",
        "Speaker 4",
    }
    assert all(u.text and "<" not in u.text for u in utterances)
    assert utterances[0].offset_seconds == 17, "the first cue starts at 00:00:17.340"
    assert [u.offset_seconds for u in utterances] == sorted(u.offset_seconds for u in utterances)


def _last_cue_end(text: str) -> str:
    return [line for line in text.splitlines() if "-->" in line][-1].split("-->")[1].strip()


def test_the_recorded_body_says_its_meeting_ran_17_minutes():
    """The last cue ends at 00:16:52.010, rounded up to whole minutes."""
    assert _last_cue_end(VTT_TEXT) == "00:16:52.010"
    assert last_cue_end_minutes(VTT_TEXT) == 17
