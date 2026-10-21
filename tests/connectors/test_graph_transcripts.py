"""Story 33e — the Graph transcript adapter, one test per matrix row, no network.

Every row starts from `fixtures/graph/transcripts.json` — the event, online
meeting and transcript shapes slice 0 measured, written by hand because no
redacted capture exists yet (the file's `_provenance` says so) — handed to a
`GraphClient` whose transport is `FakeGraph` below. A test that forgot to inject
the transport would reach `_urllib_transport` and fail to resolve
`graph.microsoft.com` rather than pass quietly.

The acceptance criteria run through `run_harvest`, with the calendar answered by
the same fake, at the bottom of the file.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from pm_ai.app import pipelines
from pm_ai.app.pipelines import run_harvest
from pm_ai.app.wiring import build
from pm_ai.connectors.graph import CategoryScopes, GraphConnector
from pm_ai.connectors.graph.auth import CredentialStale
from pm_ai.connectors.graph.calendar import GraphCalendarFetch, WindowPolicy
from pm_ai.connectors.graph.client import GRAPH_BASE, GraphClient, GraphRequest, GraphResponse
from pm_ai.connectors.transcripts.graph_transcripts import (
    LOOKUP_PERMISSION,
    TENANT_SWITCH,
    TRANSCRIPT_PERMISSION,
    UNATTRIBUTED,
    VTT,
    GraphTranscriptAdapter,
    UnenrolledGraphTranscripts,
)
from pm_ai.core.meeting_records import MEETINGS, capture_name, capture_stem
from pm_ai.domain.harvest import UNCLASSIFIED_FAULT_IS_RETRYABLE, Cursor
from pm_ai.domain.health import Health, Probe
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.storage_tiers import CAPTURES
from pm_ai.domain.transcripts import (
    CaptureFormat,
    StoredPart,
    TranscriptAbsent,
    TranscriptCaptured,
    TranscriptRefused,
    TranscriptSkipped,
    TranscriptStateKind,
)
from pm_ai.domain.vcs import TrackingVerdict
from pm_ai.ports import TranscriptSourceUnavailable

FIXTURES = Path(__file__).parent / "fixtures" / "graph"
TEMPLATES = json.loads((FIXTURES / "transcripts.json").read_text(encoding="utf-8"))
VTT_BYTES = (FIXTURES / "transcript.vtt").read_bytes()
UNATTRIBUTED_BYTES = (FIXTURES / "transcript_unattributed.txt").read_bytes()

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
WIDTH = timedelta(hours=24)
REACH_BACK = timedelta(days=7)
SINCE = NOW - REACH_BACK
INSTANCE = "graph:work"
PERSONAL = DataScope(ScopeKind.PERSONAL)
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")

JOIN = TEMPLATES["event"]["onlineMeeting"]["joinUrl"]
ONLINE = TEMPLATES["online_meetings"]["value"][0]["id"]


# ── The provider's shapes ────────────────────────────────────────────────────


def _stamp(moment: datetime | None) -> str | None:
    """Graph's spelling: seven fractional digits and a `Z`."""
    if moment is None:
        return None
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond:06d}0Z"


def _zoned(moment: datetime) -> dict:
    return {"dateTime": f"{moment:%Y-%m-%dT%H:%M:%S}.0000000", "timeZone": "UTC"}


def event_body(
    event_id: str,
    start: datetime,
    *,
    minutes: int = 60,
    join: str | None = JOIN,
    kind: str = "singleInstance",
    online: bool = True,
    provider: str = "teamsForBusiness",
) -> dict:
    body = copy.deepcopy(TEMPLATES["event"])
    body.update(
        id=event_id,
        type=kind,
        start=_zoned(start),
        end=_zoned(start + timedelta(minutes=minutes)),
        isOnlineMeeting=online,
        onlineMeetingProvider=provider,
        onlineMeeting=None if join is None else {"joinUrl": join},
    )
    return body


def lookup_body(*ids: str) -> dict:
    body = copy.deepcopy(TEMPLATES["online_meetings"])
    template = body["value"][0]
    body["value"] = [dict(template, id=online_id) for online_id in ids]
    return body


def transcript_row(
    transcript_id: str, created: datetime | None, ended: datetime | None
) -> dict:
    row = copy.deepcopy(TEMPLATES["transcript"])
    row.update(
        id=transcript_id,
        createdDateTime=_stamp(created),
        endDateTime=_stamp(ended),
    )
    return row


def ok(body: dict) -> GraphResponse:
    return GraphResponse(status=200, body=body, headers={"Content-Type": "application/json"})


def content(raw: bytes, *, kind: str = VTT) -> GraphResponse:
    return GraphResponse(status=200, body=None, headers={"Content-Type": kind}, content=raw)


def error(status: int, code: str = "Forbidden", *inner: str) -> GraphResponse:
    detail: dict = {"code": code, "message": "provider prose that must not be stored"}
    node = detail
    for nested in inner:
        node["innerError"] = {"code": nested, "date": "2026-09-07T12:00:00"}
        node = node["innerError"]
    body = {"error": detail}
    return GraphResponse(
        status=status, body=body, headers={"Content-Type": "application/json"},
        content=json.dumps(body).encode(),
    )


def throttled(seconds: int = 90) -> GraphResponse:
    return GraphResponse(status=429, body=None, headers={"Retry-After": str(seconds)})


class FakeGraph:
    """Answers each Graph route from a table, and records every request.

    A route's value is a response, or a list served in order (the last one
    repeating), so a test states what the provider says without scripting the
    order the adapter asks in.
    """

    def __init__(self) -> None:
        self.calendar: list[dict] = []
        self.calendar_answer: GraphResponse | None = None
        self.events: dict[str, object] = {}
        self.lookups: dict[str, object] = {}
        self.listings: dict[str, list[GraphResponse]] = {}
        self.contents: dict[tuple[str, str], object] = {}
        self.requests: list[GraphRequest] = []

    def __call__(self, request: GraphRequest) -> GraphResponse:
        self.requests.append(request)
        parts = urlsplit(request.url)
        path = parts.path.removeprefix("/v1.0")
        query = parse_qs(parts.query)
        if path == "/me/calendarView":
            if self.calendar_answer is not None:
                return self.calendar_answer
            return ok({"value": list(self.calendar)})
        if path.startswith("/me/events/"):
            return self._serve(self.events, unquote(path.removeprefix("/me/events/")))
        if path == "/me/onlineMeetings":
            criterion = query["$filter"][0]
            assert criterion.startswith("JoinWebUrl eq '") and criterion.endswith("'")
            join = criterion[len("JoinWebUrl eq '") : -1].replace("''", "'")
            return self._serve(self.lookups, join)
        listing = re.fullmatch(r"/me/onlineMeetings/([^/]+)/transcripts", path)
        if listing:
            pages = self.listings[unquote(listing.group(1))]
            return pages[int(query.get("$skiptoken", ["0"])[0])]
        downloaded = re.fullmatch(r"/me/onlineMeetings/([^/]+)/transcripts/([^/]+)/content", path)
        if downloaded:
            key = (unquote(downloaded.group(2)), request.headers["Accept"])
            return self._serve(self.contents, key)
        raise AssertionError(f"no route for {request!r}")

    @staticmethod
    def _serve(table: dict, key: object) -> GraphResponse:
        if key not in table:
            raise AssertionError(f"the fixture has no answer for {key!r}")
        answer = table[key]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def listing(self, online_id: str, *rows: dict) -> None:
        """One page of transcripts for `online_id`."""
        self.listings[online_id] = [ok({"value": list(rows)})]

    def paged(self, online_id: str, *pages: list[dict]) -> None:
        """Several pages, each but the last carrying an `@odata.nextLink`."""
        served = []
        for index, rows in enumerate(pages):
            body: dict = {"value": rows}
            if index + 1 < len(pages):
                body["@odata.nextLink"] = (
                    f"{GRAPH_BASE}/me/onlineMeetings/{online_id}/transcripts"
                    f"?$skiptoken={index + 1}"
                )
            served.append(ok(body))
        self.listings[online_id] = served

    def asked(self, fragment: str) -> list[str]:
        return [r.url for r in self.requests if fragment in unquote(r.url)]


class Auth:
    instance = INSTANCE

    def __init__(self, raises: Exception | None = None) -> None:
        self.raises = raises

    def access_token(self, *, force_refresh: bool = False) -> str:
        if self.raises is not None:
            raise self.raises
        return "token-1"

    def check_health(self) -> Probe:
        return Probe(INSTANCE, Health.OK, "graph answered")


def _client(graph: FakeGraph, *, auth: Auth | None = None, page_cap: int = 200) -> GraphClient:
    return GraphClient(auth=auth or Auth(), transport=graph, now=lambda: NOW, page_cap=page_cap)


def _adapter(graph: FakeGraph, **options) -> GraphTranscriptAdapter:
    return GraphTranscriptAdapter(client=_client(graph, **options), instance=INSTANCE)


def meeting(event_id: str, start: datetime, *, minutes: int = 60, scope=PERSONAL) -> Meeting:
    return Meeting(
        meeting_id=event_id,
        title="Sprint review",
        start=start,
        duration_minutes=minutes,
        attendees=(),
        scope=scope,
        calendar_event_ref=event_id,
    )


START = NOW - timedelta(days=1, hours=2)
"""A one-off meeting that ended yesterday, inside the reach-back."""


def _one_off(graph: FakeGraph, event_id: str = "AAMk-one", *, start: datetime = START, **event) -> Meeting:
    """The common shape: an event, its lookup, and nothing listed yet."""
    graph.events[event_id] = ok(event_body(event_id, start, **event))
    graph.lookups[JOIN] = ok(lookup_body(ONLINE))
    graph.listings.setdefault(ONLINE, [ok({"value": []})])
    return meeting(event_id, start)


def _recorded(graph: FakeGraph, transcript_id: str = "T-1", *, start: datetime = START) -> None:
    graph.listing(ONLINE, transcript_row(transcript_id, start + timedelta(minutes=1), start + timedelta(minutes=58)))
    graph.contents[(transcript_id, VTT)] = content(VTT_BYTES)


# ── Matrix: recorded one-off meeting ─────────────────────────────────────────


def test_a_recorded_one_off_meeting_is_captured_verbatim():
    graph = FakeGraph()
    held = _one_off(graph)
    _recorded(graph)

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptCaptured)
    assert outcome.captures[0].text.encode("utf-8") == VTT_BYTES
    assert outcome.captures[0].attributed is True
    assert outcome.captures[0].format is CaptureFormat.GRAPH
    assert outcome.captures[0].transcript_id == "T-1"
    (event_url,) = graph.asked("/me/events/")
    assert "$select=isOnlineMeeting,onlineMeeting,onlineMeetingProvider,start,end" in unquote(event_url)
    (download,) = [r for r in graph.requests if r.url.endswith("/content")]
    assert download.headers["Accept"] == VTT
    assert all(r.url.startswith(GRAPH_BASE) for r in graph.requests), (
        "the content is fetched at the documented path, never transcriptContentUrl"
    )


# ── Matrix: not recorded, not tried, not the PM's ────────────────────────────


def test_a_meeting_with_no_transcript_listed_is_absent():
    graph = FakeGraph()
    held = _one_off(graph)

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptAbsent)
    assert "no transcript" in outcome.reason


@pytest.mark.parametrize(
    "event",
    [dict(online=False, join=None), dict(join=None), dict(provider="skypeForBusiness")],
    ids=["not online", "no join link", "not Teams"],
)
def test_a_meeting_that_is_not_an_online_teams_meeting_is_absent_and_never_looked_up(event):
    graph = FakeGraph()
    held = _one_off(graph, **event)

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptAbsent)
    assert "not an online Teams meeting" in outcome.reason
    assert graph.asked("/me/onlineMeetings") == [], "no lookup, no listing, nothing refused"


def test_a_record_with_no_calendar_reference_makes_no_request():
    graph = FakeGraph()
    held = Meeting(
        meeting_id="mtg_dropped", title="Dropped", start=START, duration_minutes=30,
        attendees=(), scope=PERSONAL,
    )

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptAbsent)
    assert graph.requests == []


@pytest.mark.parametrize(
    "lookup",
    [ok(lookup_body()), error(404, "NotFound")],
    ids=["lookup answers nothing", "lookup answers 404"],
)
def test_a_meeting_that_is_not_the_pms_to_read_is_absent(lookup):
    graph = FakeGraph()
    held = _one_off(graph)
    graph.lookups[JOIN] = lookup

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptAbsent)
    assert "not the PM's to read" in outcome.reason


def test_a_listing_or_download_saying_expired_is_absent():
    graph = FakeGraph()
    held = _one_off(graph)
    graph.listings[ONLINE] = [error(400, "BadRequest", "MeetingHasExpired")]
    (listed,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    graph = FakeGraph()
    held = _one_off(graph)
    _recorded(graph)
    graph.contents[("T-1", VTT)] = error(400, "BadRequest", "MeetingHasExpired")
    (downloaded,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(listed, TranscriptAbsent)
    assert isinstance(downloaded, TranscriptAbsent)
    assert "expired" in downloaded.reason


def test_a_lookup_answering_two_online_meetings_is_refused_naming_both():
    graph = FakeGraph()
    held = _one_off(graph)
    graph.lookups[JOIN] = ok(lookup_body("MS-first", "MS-second"))

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptRefused)
    assert outcome.retryable is False
    assert "MS-first" in outcome.reason and "MS-second" in outcome.reason


# ── Matrix: a series ─────────────────────────────────────────────────────────


def _series(graph: FakeGraph) -> list[Meeting]:
    held = []
    for day in (3, 2, 1):
        start = NOW - timedelta(days=day, hours=2)
        event_id = f"AAMk-occurrence-{day}"
        graph.events[event_id] = ok(event_body(event_id, start, kind="occurrence"))
        held.append(meeting(event_id, start))
    graph.lookups[JOIN] = ok(lookup_body(ONLINE))
    return held


def test_a_series_resolves_and_lists_once_and_matches_by_time():
    graph = FakeGraph()
    first, second, third = _series(graph)
    graph.listing(
        ONLINE,
        transcript_row("T-second", second.start + timedelta(minutes=2), second.start + timedelta(minutes=50)),
        transcript_row("T-first", first.start + timedelta(minutes=1), first.start + timedelta(minutes=59)),
    )
    graph.contents[("T-first", VTT)] = content(b"WEBVTT\n\nfirst\n")
    graph.contents[("T-second", VTT)] = content(b"WEBVTT\n\nsecond\n")

    outcomes = _adapter(graph).acquire([first, second, third], since=SINCE, stored={})

    assert [type(o) for o in outcomes] == [TranscriptCaptured, TranscriptCaptured, TranscriptAbsent]
    assert outcomes[0].captures[0].text == "WEBVTT\n\nfirst\n"
    assert outcomes[1].captures[0].text == "WEBVTT\n\nsecond\n"
    assert len(graph.asked("/me/onlineMeetings?")) == 1, "resolved once"
    assert len([u for u in graph.asked("/transcripts") if u.endswith("/transcripts")]) == 1, "listed once"


def test_a_transcript_fitting_none_of_a_series_occurrences_is_left_alone():
    graph = FakeGraph()
    first, second, third = _series(graph)
    graph.listing(ONLINE, transcript_row("T-elsewhere", NOW - timedelta(days=4), NOW - timedelta(days=4) + timedelta(minutes=30)))

    outcomes = _adapter(graph).acquire([first, second, third], since=SINCE, stored={})

    assert all(isinstance(o, TranscriptAbsent) for o in outcomes)


def test_one_transcript_fitting_two_occurrences_is_refused_for_both():
    graph = FakeGraph()
    start = NOW - timedelta(days=1, hours=3)
    back_to_back = start + timedelta(minutes=30)
    for event_id, at in (("AAMk-a", start), ("AAMk-b", back_to_back)):
        graph.events[event_id] = ok(event_body(event_id, at, minutes=20, kind="occurrence"))
    graph.lookups[JOIN] = ok(lookup_body(ONLINE))
    # Created 25 minutes in: inside a's slot widened by 15 and b's widened start.
    graph.listing(ONLINE, transcript_row("T-both", start + timedelta(minutes=25), back_to_back + timedelta(minutes=19)))

    outcomes = _adapter(graph).acquire(
        [meeting("AAMk-a", start, minutes=20), meeting("AAMk-b", back_to_back, minutes=20)], since=SINCE, stored={}
    )

    assert [type(o) for o in outcomes] == [TranscriptRefused, TranscriptRefused]
    assert all("T-both" in o.reason and not o.retryable for o in outcomes)
    assert "AAMk-b" in outcomes[0].reason
    assert graph.asked("/content") == [], "nothing stored"


def test_two_transcripts_fitting_one_meeting_are_its_parts_in_time_order():
    """Story 33g, restarted transcription: both are downloaded, earliest first."""
    graph = FakeGraph()
    held = _one_off(graph)
    graph.listing(
        ONLINE,
        transcript_row("T-restart", START + timedelta(minutes=20), START + timedelta(minutes=58)),
        transcript_row("T-early", START + timedelta(minutes=1), START + timedelta(minutes=19)),
    )
    graph.contents[("T-early", VTT)] = content(b"WEBVTT\n\nearly\n")
    graph.contents[("T-restart", VTT)] = content(b"WEBVTT\n\nrestart\n")

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptCaptured)
    assert [c.transcript_id for c in outcome.captures] == ["T-early", "T-restart"]
    assert [c.text for c in outcome.captures] == ["WEBVTT\n\nearly\n", "WEBVTT\n\nrestart\n"]
    assert len(graph.asked("/content")) == 2


# ── Story 33g: the parts of a transcript ─────────────────────────────────────


def _parts(graph: FakeGraph, *spans: tuple[str, int, int | None]) -> None:
    """`(id, created, ended)` in minutes from START, listed newest first, each downloadable."""
    rows = []
    for transcript_id, created, ended in sorted(spans, key=lambda span: -span[1]):
        rows.append(
            transcript_row(
                transcript_id,
                START + timedelta(minutes=created),
                None if ended is None else START + timedelta(minutes=ended),
            )
        )
        graph.contents[(transcript_id, VTT)] = content(f"WEBVTT\n\n{transcript_id}\n".encode())
    graph.listing(ONLINE, *rows)


def _stored(transcript_id: str, created: int, ended: int) -> StoredPart:
    return StoredPart(transcript_id, START + timedelta(minutes=created), START + timedelta(minutes=ended))


def test_overlapping_transcripts_with_nothing_stored_are_refused_as_duplicates():
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-a", 0, 20), ("T-b", 10, 30))

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptRefused) and not outcome.retryable
    assert "T-a" in outcome.reason and "T-b" in outcome.reason
    assert "duplicates" in outcome.reason
    assert graph.asked("/content") == []


def test_one_ending_exactly_as_the_next_is_created_does_not_overlap():
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-a", 0, 20), ("T-b", 20, 40))

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptCaptured)
    assert [c.transcript_id for c in outcome.captures] == ["T-a", "T-b"]


def test_a_new_transcript_overlapping_a_stored_part_is_refused_and_not_downloaded():
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-1", 1, 20), ("T-dup", 15, 40))

    (outcome,) = _adapter(graph).acquire(
        [held], since=SINCE, stored={held.meeting_id: (_stored("T-1", 1, 20),)}
    )

    assert isinstance(outcome, TranscriptRefused) and not outcome.retryable
    assert "T-1" in outcome.reason and "T-dup" in outcome.reason and "duplicates" in outcome.reason
    assert graph.asked("/content") == []


def test_an_unfinished_part_skips_the_meeting_and_nothing_is_downloaded():
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-1", 1, 20), ("T-2", 25, None))

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptSkipped)
    assert "T-2" in outcome.reason
    assert graph.asked("/content") == [], "part 1 is not stored either"


def test_an_unplaced_transcript_skips_every_meeting_of_its_online_meeting():
    graph = FakeGraph()
    first, second, third = _series(graph)
    graph.listing(
        ONLINE,
        transcript_row("T-unplaced", None, None),
        transcript_row("T-first", first.start + timedelta(minutes=1), first.start + timedelta(minutes=50)),
    )
    graph.contents[("T-first", VTT)] = content(b"WEBVTT\n\nfirst\n")

    outcomes = _adapter(graph).acquire([first, second, third], since=SINCE, stored={})

    assert all(isinstance(o, TranscriptSkipped) for o in outcomes)
    assert all("T-unplaced" in o.reason for o in outcomes)
    assert graph.asked("/content") == []


def test_overlap_and_unfinished_together_is_a_skip_not_a_refusal():
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-a", 0, 30), ("T-b", 10, None))

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptSkipped)


def test_a_later_part_is_the_only_one_downloaded():
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-1", 1, 20), ("T-2", 25, 50))

    (outcome,) = _adapter(graph).acquire(
        [held], since=SINCE, stored={held.meeting_id: (_stored("T-1", 1, 20),)}
    )

    assert isinstance(outcome, TranscriptCaptured)
    assert [c.transcript_id for c in outcome.captures] == ["T-2"]
    (download,) = graph.asked("/content")
    assert "T-2" in download


def test_no_new_part_beyond_the_stored_ones_is_absent():
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-1", 1, 20))

    (outcome,) = _adapter(graph).acquire(
        [held], since=SINCE, stored={held.meeting_id: (_stored("T-1", 1, 20),)}
    )

    assert isinstance(outcome, TranscriptAbsent)
    assert graph.asked("/content") == []


def test_a_new_transcript_created_before_the_newest_stored_part_is_refused_naming_it():
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-late", 25, 50), ("T-early", 1, 20))

    (outcome,) = _adapter(graph).acquire(
        [held], since=SINCE, stored={held.meeting_id: (_stored("T-late", 25, 50),)}
    )

    assert isinstance(outcome, TranscriptRefused) and not outcome.retryable
    assert "T-early" in outcome.reason and "T-late" in outcome.reason
    assert graph.asked("/content") == []


def test_a_stored_part_missing_from_the_listing_still_counts_and_the_new_one_is_stored():
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-2", 25, 50))

    (outcome,) = _adapter(graph).acquire(
        [held], since=SINCE, stored={held.meeting_id: (_stored("T-1", 1, 20),)}
    )

    assert isinstance(outcome, TranscriptCaptured)
    assert [c.transcript_id for c in outcome.captures] == ["T-2"]


def test_a_stored_part_missing_from_the_listing_still_refuses_an_overlap():
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-2", 10, 50))

    (outcome,) = _adapter(graph).acquire(
        [held], since=SINCE, stored={held.meeting_id: (_stored("T-1", 1, 20),)}
    )

    assert isinstance(outcome, TranscriptRefused) and "duplicates" in outcome.reason


def test_a_part_fitting_two_occurrences_refuses_both_and_stores_nothing():
    graph = FakeGraph()
    start = NOW - timedelta(days=1, hours=3)
    back_to_back = start + timedelta(minutes=30)
    for event_id, at in (("AAMk-a", start), ("AAMk-b", back_to_back)):
        graph.events[event_id] = ok(event_body(event_id, at, minutes=20, kind="occurrence"))
    graph.lookups[JOIN] = ok(lookup_body(ONLINE))
    graph.listing(
        ONLINE,
        transcript_row("T-both", start + timedelta(minutes=25), start + timedelta(minutes=40)),
        transcript_row("T-a1", start + timedelta(minutes=1), start + timedelta(minutes=10)),
    )
    graph.contents[("T-a1", VTT)] = content(b"WEBVTT\n\na1\n")

    outcomes = _adapter(graph).acquire(
        [meeting("AAMk-a", start, minutes=20), meeting("AAMk-b", back_to_back, minutes=20)],
        since=SINCE,
        stored={},
    )

    assert [type(o) for o in outcomes] == [TranscriptRefused, TranscriptRefused]
    assert all("T-both" in o.reason and not o.retryable for o in outcomes)
    assert graph.asked("/content") == [], "nothing stored, part 1 of a included"


def test_a_series_with_one_restarted_occurrence_lists_once_and_captures_each_part():
    graph = FakeGraph()
    first, second, third = _series(graph)
    graph.listing(
        ONLINE,
        transcript_row("T-third", third.start + timedelta(minutes=1), third.start + timedelta(minutes=50)),
        transcript_row("T-second-b", second.start + timedelta(minutes=30), second.start + timedelta(minutes=55)),
        transcript_row("T-second-a", second.start + timedelta(minutes=1), second.start + timedelta(minutes=20)),
        transcript_row("T-first", first.start + timedelta(minutes=1), first.start + timedelta(minutes=50)),
    )
    for transcript_id in ("T-first", "T-second-a", "T-second-b", "T-third"):
        graph.contents[(transcript_id, VTT)] = content(f"WEBVTT\n\n{transcript_id}\n".encode())

    outcomes = _adapter(graph).acquire([first, second, third], since=SINCE, stored={})

    assert all(isinstance(o, TranscriptCaptured) for o in outcomes)
    assert [[c.transcript_id for c in o.captures] for o in outcomes] == [
        ["T-first"],
        ["T-second-a", "T-second-b"],
        ["T-third"],
    ]
    assert len(graph.asked("/me/onlineMeetings?")) == 1, "resolved once"
    assert len([u for u in graph.asked("/transcripts") if u.endswith("/transcripts")]) == 1, "listed once"


def test_a_transcript_fitting_no_window_of_a_one_off_meeting_is_refused():
    graph = FakeGraph()
    held = _one_off(graph)
    # Created 80 minutes after a 60-minute meeting began: past the 15-minute widening.
    graph.listing(ONLINE, transcript_row("T-overran", START + timedelta(minutes=80), START + timedelta(minutes=90)))

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptRefused) and not outcome.retryable
    assert "T-overran" in outcome.reason
    assert graph.asked("/content") == []


def test_a_transcript_that_ended_before_the_occurrence_started_does_not_fit():
    graph = FakeGraph()
    held = _one_off(graph, kind="occurrence")
    graph.listing(ONLINE, transcript_row("T-before", START - timedelta(minutes=14), START - timedelta(minutes=1)))

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptAbsent)


# ── Matrix: the listing's stop rule and its page cap ─────────────────────────


def test_the_listing_stops_at_a_transcript_older_than_the_reach_back():
    graph = FakeGraph()
    held = _one_off(graph, kind="occurrence")
    old = SINCE - timedelta(minutes=16)
    graph.paged(
        ONLINE,
        [
            transcript_row("T-1", START + timedelta(minutes=1), START + timedelta(minutes=58)),
            transcript_row("T-old", old, old + timedelta(minutes=30)),
        ],
        [transcript_row("T-older", old - timedelta(days=7), old - timedelta(days=7) + timedelta(minutes=30))],
    )
    graph.contents[("T-1", VTT)] = content(VTT_BYTES)

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptCaptured)
    assert graph.asked("$skiptoken") == [], "no further page was fetched"


def test_a_listing_that_hits_the_page_cap_first_is_refused_by_name():
    graph = FakeGraph()
    held = _one_off(graph, kind="occurrence")
    recent = [transcript_row(f"T-{n}", START + timedelta(minutes=1), START + timedelta(minutes=50)) for n in range(3)]
    graph.paged(ONLINE, [recent[0]], [recent[1]], [recent[2]])

    (outcome,) = _adapter(graph, page_cap=2).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptRefused) and not outcome.retryable
    assert "page cap" in outcome.reason
    assert graph.asked("/content") == [], "nothing stored"


# ── Matrix: still being produced ─────────────────────────────────────────────


@pytest.mark.parametrize("missing", ["ended", "created"])
def test_a_transcript_without_both_instants_is_skipped(missing):
    graph = FakeGraph()
    held = _one_off(graph)
    created = None if missing == "created" else START + timedelta(minutes=1)
    ended = None if missing == "ended" else START + timedelta(minutes=58)
    graph.listing(ONLINE, transcript_row("T-live", created, ended))

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptSkipped)
    assert graph.asked("/content") == []


# ── Matrix: the tenant switch, speaker attribution and permissions ───────────


def test_a_tenant_that_switched_transcripts_off_is_refused_and_the_others_continue():
    graph = FakeGraph()
    first = _one_off(graph, "AAMk-one")
    graph.events["AAMk-two"] = ok(event_body("AAMk-two", START - timedelta(hours=3), join=JOIN + "&two"))
    graph.lookups[JOIN + "&two"] = ok(lookup_body("MS-two"))
    graph.listings[ONLINE] = [error(403, "Forbidden", TENANT_SWITCH)]
    graph.listing("MS-two", transcript_row("T-2", START - timedelta(hours=3) + timedelta(minutes=1), START - timedelta(hours=2, minutes=5)))
    graph.contents[("T-2", VTT)] = content(VTT_BYTES)

    switched, other = _adapter(graph).acquire([first, meeting("AAMk-two", START - timedelta(hours=3))], since=SINCE, stored={})

    assert isinstance(switched, TranscriptRefused)
    assert switched.retryable is False
    assert TENANT_SWITCH in switched.reason
    assert "provider prose" not in switched.reason
    assert isinstance(other, TranscriptCaptured)


def test_speaker_attribution_off_asks_once_more_for_the_unattributed_format():
    graph = FakeGraph()
    held = _one_off(graph)
    _recorded(graph)
    graph.contents[("T-1", VTT)] = error(403, "Forbidden", "SpeakerAttributionNotAllowed")
    graph.contents[("T-1", UNATTRIBUTED)] = content(UNATTRIBUTED_BYTES, kind=UNATTRIBUTED)

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptCaptured)
    assert outcome.captures[0].attributed is False
    assert outcome.captures[0].text.encode() == UNATTRIBUTED_BYTES
    assert [r.headers["Accept"] for r in graph.requests if r.url.endswith("/content")] == [VTT, UNATTRIBUTED]


@pytest.mark.parametrize(
    "where, permission",
    [("lookup", LOOKUP_PERMISSION), ("listing", TRANSCRIPT_PERMISSION), ("download", TRANSCRIPT_PERMISSION)],
)
def test_a_plain_403_is_refused_naming_the_permission(where, permission):
    graph = FakeGraph()
    held = _one_off(graph)
    _recorded(graph)
    forbidden = error(403, "Forbidden")
    if where == "lookup":
        graph.lookups[JOIN] = forbidden
    elif where == "listing":
        graph.listings[ONLINE] = [forbidden]
    else:
        graph.contents[("T-1", VTT)] = forbidden

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptRefused) and not outcome.retryable
    assert permission in outcome.reason
    assert TENANT_SWITCH not in outcome.reason


# ── Matrix: gone, empty, not UTF-8 ───────────────────────────────────────────


def test_a_calendar_event_that_is_gone_is_refused_and_not_retried():
    graph = FakeGraph()
    held = _one_off(graph)
    graph.events["AAMk-one"] = error(404, "ErrorItemNotFound")

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptRefused) and not outcome.retryable
    assert "404" in outcome.reason


def test_a_download_answering_404_after_the_listing_named_it_is_refused():
    graph = FakeGraph()
    held = _one_off(graph)
    _recorded(graph)
    graph.contents[("T-1", VTT)] = error(404, "NotFound")

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptRefused) and not outcome.retryable
    assert "T-1" in outcome.reason and "gone" in outcome.reason


@pytest.mark.parametrize("raw", [b"", b"  \r\n\t "], ids=["zero bytes", "whitespace"])
def test_an_empty_download_is_refused_and_tried_again(raw):
    graph = FakeGraph()
    held = _one_off(graph)
    _recorded(graph)
    graph.contents[("T-1", VTT)] = content(raw)

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptRefused)
    assert outcome.retryable is True
    assert "empty" in outcome.reason


def test_content_that_is_not_utf8_is_refused_by_name():
    graph = FakeGraph()
    held = _one_off(graph)
    _recorded(graph)
    graph.contents[("T-1", VTT)] = content(b"WEBVTT\n\n\xff\xfe broken")

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptRefused) and not outcome.retryable
    assert "UTF-8" in outcome.reason


# ── Matrix: one provider fault stops the run ─────────────────────────────────


def _three(graph: FakeGraph) -> list[Meeting]:
    held = []
    for n in range(3):
        start = START - timedelta(hours=3 * n)
        event_id, join, online = f"AAMk-{n}", f"{JOIN}&n={n}", f"MS-{n}"
        graph.events[event_id] = ok(event_body(event_id, start, join=join))
        graph.lookups[join] = ok(lookup_body(online))
        graph.listing(online, transcript_row(f"T-{n}", start + timedelta(minutes=1), start + timedelta(minutes=50)))
        graph.contents[(f"T-{n}", VTT)] = content(VTT_BYTES)
        held.append(meeting(event_id, start))
    return held


def test_a_throttle_on_the_second_meeting_keeps_the_first_and_leaves_the_rest():
    graph = FakeGraph()
    held = _three(graph)
    graph.contents[("T-1", VTT)] = throttled(90)

    outcomes = _adapter(graph).acquire(held, since=SINCE, stored={})

    assert [type(o) for o in outcomes] == [TranscriptCaptured, TranscriptRefused]
    assert outcomes[1].retryable is True
    assert outcomes[1].retry_after == timedelta(seconds=90)
    assert graph.asked("AAMk-2") == [], "the third meeting waits for the next run"


@pytest.mark.parametrize(
    "fault", [GraphResponse(status=503), GraphResponse(status=500)], ids=["503", "500"]
)
def test_a_server_error_stops_the_run_retryably(fault):
    graph = FakeGraph()
    held = _three(graph)
    graph.lookups[f"{JOIN}&n=0"] = fault

    outcomes = _adapter(graph).acquire(held, since=SINCE, stored={})

    assert len(outcomes) == 1
    assert isinstance(outcomes[0], TranscriptRefused) and outcomes[0].retryable


def test_a_credential_that_no_longer_signs_in_stops_the_run_and_stays_retryable():
    graph = FakeGraph()
    held = _three(graph)

    outcomes = _adapter(graph, auth=Auth(raises=CredentialStale("expired"))).acquire(held, since=SINCE, stored={})

    (only,) = outcomes
    assert isinstance(only, TranscriptRefused) and only.retryable
    assert "sign" in only.reason


def test_an_unclassified_fault_refuses_that_meeting_naming_its_type(monkeypatch):
    graph = FakeGraph()
    held = _three(graph)
    from pm_ai.connectors.transcripts import graph_transcripts

    original = graph_transcripts._event_of

    def broken(body):
        if body["id"] == "AAMk-0":
            raise KeyError("a bug in the adapter")
        return original(body)

    monkeypatch.setattr(graph_transcripts, "_event_of", broken)
    outcomes = _adapter(graph).acquire(held, since=SINCE, stored={})

    assert isinstance(outcomes[0], TranscriptRefused)
    assert "KeyError" in outcomes[0].reason and "a bug" not in outcomes[0].reason
    assert [type(o) for o in outcomes[1:]] == [TranscriptCaptured, TranscriptCaptured]


def test_with_no_graph_connector_enrolled_the_stand_in_refuses_by_name(tmp_path):
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    assert isinstance(daemon.transcripts["graph"], UnenrolledGraphTranscripts)
    with pytest.raises(TranscriptSourceUnavailable, match="no Graph connector is enrolled"):
        daemon.transcripts["graph"].acquire([meeting("AAMk-one", START)], since=SINCE, stored={})


# ── Acceptance, through `run_harvest` ────────────────────────────────────────


def _calendar_row(event_id: str, start: datetime, *, minutes: int = 60, categories=(), kind="singleInstance") -> dict:
    """The `calendarView` row the harvest records the meeting from."""
    return {
        "id": event_id,
        "iCalUId": f"ical-{event_id}",
        "subject": "Sprint review",
        "start": _zoned(start),
        "end": _zoned(start + timedelta(minutes=minutes)),
        "isAllDay": False,
        "isCancelled": False,
        "type": kind,
        "seriesMasterId": None,
        "occurrenceId": None,
        "categories": list(categories),
        "organizer": {"emailAddress": {"name": "A Person", "address": "a@example.invalid"}},
        "attendees": [],
        "responseStatus": {"response": "organizer", "time": None},
    }


def _daemon(tmp_path: Path, graph: FakeGraph, *, clock=lambda: NOW, **options):
    """A daemon whose `graph:work` connector and transcript source share `graph`.

    `clock` is the daemon's and the single writer's; the connector's stays at
    `NOW`, so a test can move time for acquisition without moving the calendar.
    """
    daemon = build(tmp_path, "alpha", now=clock, **options)
    client = _client(graph)
    daemon.connectors[INSTANCE] = GraphConnector(
        auth=Auth(),
        calendar=GraphCalendarFetch(
            client=client,
            instance=INSTANCE,
            windows=WindowPolicy(width=WIDTH, first_run_reach_back=REACH_BACK),
            now=lambda: NOW,
        ),
        categories=CategoryScopes(projects={"Alpha": "alpha"}, registered=lambda p: p == "alpha"),
        now=lambda: NOW,
    )
    daemon.transcript_sources[INSTANCE] = GraphTranscriptAdapter(client=client, instance=INSTANCE)
    return daemon


def _record_bytes(daemon, scope: DataScope) -> dict[str, bytes]:
    return {
        name: daemon.storage.read_artifact(scope=scope, artifact=MEETINGS, name=name)
        for name in daemon.storage.list_collection(scope=scope, artifact=MEETINGS)
    }


def test_acceptance_a_recorded_project_meeting_is_captured_beside_its_record(tmp_path, monkeypatch):
    graph = FakeGraph()
    graph.calendar = [_calendar_row("AAMk-one", START, categories=["Alpha"])]
    _one_off(graph)
    _recorded(graph)
    daemon = _daemon(tmp_path, graph)
    before: dict[str, bytes] = {}
    real = pipelines.acquire_transcripts

    def snapshot_then_acquire(*args, **kwargs):
        before.update(_record_bytes(daemon, PROJECT))
        return real(*args, **kwargs)

    monkeypatch.setattr(pipelines, "acquire_transcripts", snapshot_then_acquire)

    persisted = run_harvest(daemon, INSTANCE)

    name = capture_name("AAMk-one", START, CaptureFormat.GRAPH)
    assert name == capture_stem("AAMk-one", START) + "-graph.vtt"
    capture = daemon.storage.paths.resolve(PROJECT, CAPTURES) / name
    assert capture.read_bytes() == VTT_BYTES
    assert capture.parent.parent.name == ".project-ai"
    # Fetched, then ingested in the same run (story 11b).
    state = daemon.storage.transcript_states(PROJECT)["AAMk-one"]
    assert state.state == TranscriptStateKind.INGESTED.value
    assert before and _record_bytes(daemon, PROJECT) == before, "the record is byte-identical"
    report = persisted.transcripts
    assert report is not None
    assert [entry.capture_names for entry in report.captured] == [(name,)]
    assert report.captured[0].state is TranscriptStateKind.FETCHED
    assert report.awaiting_ingestion == ((PROJECT, "AAMk-one"),)
    assert report.ingested == ((PROJECT, "AAMk-one"),) and not report.ingest_failed
    assert len(daemon.storage.coverage_windows(INSTANCE)) == 1, "transcripts earn no coverage"


def test_acceptance_a_throttled_meeting_is_captured_by_a_later_run_with_no_records(tmp_path):
    graph = FakeGraph()
    graph.calendar = [_calendar_row("AAMk-one", START)]
    _one_off(graph)
    _recorded(graph)
    graph.contents[("T-1", VTT)] = [throttled(60), content(VTT_BYTES)]
    moment = [NOW]
    daemon = _daemon(tmp_path, graph, clock=lambda: moment[0])

    first = run_harvest(daemon, INSTANCE)
    (refused,) = first.transcripts.refused
    assert refused.retryable and refused.retry_after == timedelta(seconds=60)

    graph.calendar = []
    moment[0] = NOW + timedelta(seconds=30)
    graph.requests.clear()
    early = run_harvest(daemon, INSTANCE)
    assert early.transcripts.entries == () and graph.asked("/content") == [], (
        "the provider's 60-second hint has not run out, so it is not asked yet"
    )

    moment[0] = NOW + timedelta(seconds=61)
    second = run_harvest(daemon, INSTANCE)

    assert [entry.meeting_id for entry in second.transcripts.captured] == ["AAMk-one"]
    name = capture_name("AAMk-one", START, CaptureFormat.GRAPH)
    assert (daemon.storage.paths.resolve(PERSONAL, CAPTURES) / name).read_bytes() == VTT_BYTES


def test_acceptance_a_series_is_looked_up_and_listed_once(tmp_path):
    graph = FakeGraph()
    held = _series(graph)
    graph.calendar = [_calendar_row(m.meeting_id, m.start, kind="occurrence") for m in held]
    first, second, third = held
    graph.listing(
        ONLINE,
        transcript_row("T-second", second.start + timedelta(minutes=2), second.start + timedelta(minutes=50)),
        transcript_row("T-first", first.start + timedelta(minutes=1), first.start + timedelta(minutes=59)),
    )
    graph.contents[("T-first", VTT)] = content(VTT_BYTES)
    graph.contents[("T-second", VTT)] = content(VTT_BYTES)
    daemon = _daemon(tmp_path, graph)

    report = run_harvest(daemon, INSTANCE).transcripts

    assert len(graph.asked("/me/onlineMeetings?")) == 1
    assert len([u for u in graph.asked("/transcripts") if u.endswith("/transcripts")]) == 1
    assert sorted(e.meeting_id for e in report.captured) == sorted(
        [first.meeting_id, second.meeting_id]
    )
    captures = daemon.storage.list_collection(scope=PERSONAL, artifact=CAPTURES)
    assert not any(name.startswith(capture_stem(third.meeting_id, third.start)) for name in captures)
    assert report.refused == ()
    assert daemon.storage.transcript_states(PERSONAL)[third.meeting_id].state == "absent"


def test_acceptance_the_tenant_switch_refuses_every_meeting_and_closes_them(tmp_path, monkeypatch):
    graph = FakeGraph()
    held = _three(graph)
    graph.calendar = [_calendar_row(m.meeting_id, m.start) for m in held]
    for n in range(3):
        graph.listings[f"MS-{n}"] = [error(403, "Forbidden", TENANT_SWITCH)]
    daemon = _daemon(tmp_path, graph)
    saved = []
    real_save = daemon.storage.save_cursor
    monkeypatch.setattr(
        daemon.storage, "save_cursor", lambda *a: (saved.append(a), real_save(*a))[1]
    )

    report = run_harvest(daemon, INSTANCE).transcripts

    assert sorted(e.meeting_id for e in report.refused) == ["AAMk-0", "AAMk-1", "AAMk-2"]
    assert all(TENANT_SWITCH in e.reason and not e.retryable for e in report.refused)
    (call,) = saved
    assert daemon.storage.load_cursor(INSTANCE) == call[1], "cursor unchanged by acquisition"
    assert len(daemon.storage.coverage_windows(INSTANCE)) == 1

    graph.requests.clear()
    again = run_harvest(daemon, INSTANCE).transcripts
    assert graph.asked("/me/onlineMeetings") == [] and graph.asked("/me/events/") == []
    assert again.entries == ()


def test_acceptance_a_manual_capture_means_no_graph_request_for_that_meeting(tmp_path):
    graph = FakeGraph()
    graph.calendar = [_calendar_row("AAMk-one", START)]
    _one_off(graph)
    daemon = _daemon(tmp_path, graph)
    daemon.storage.write_capture(
        "Sam: hello\n", scope=PERSONAL,
        name=capture_name("AAMk-one", START, CaptureFormat.MANUAL_TXT),
    )

    report = run_harvest(daemon, INSTANCE).transcripts

    assert graph.asked("AAMk-one") == []
    assert not [r for r in graph.requests if "/me/events/" in r.url or "onlineMeetings" in r.url]
    assert report.entries == ()


class _ProjectTracked:
    """A `VcsPort` reporting the project's `transcripts/` as committable until fixed."""

    committable = True

    def working_tree(self, path: Path) -> Path | None:
        if ".project-ai" not in path.parts:
            return None
        index = path.parts.index(".project-ai")
        return Path(*path.parts[:index])

    def tracking(self, path: Path, *, repository: Path) -> TrackingVerdict:
        # Every other gitignored project artifact (the event log the harvest
        # appends to) is excluded; only the capture directory is not.
        return TrackingVerdict(
            ignored=path.name != "transcripts" or not self.committable, tracked=False
        )

    def repository_marker_above(self, path: Path) -> Path | None:
        return None


def test_acceptance_a_project_directory_git_would_commit_is_refused_and_personal_continues(tmp_path):
    graph = FakeGraph()
    held = _three(graph)
    project_meeting, personal_meeting = held[0], held[1]
    graph.calendar = [
        _calendar_row(project_meeting.meeting_id, project_meeting.start, categories=["Alpha"]),
        _calendar_row(personal_meeting.meeting_id, personal_meeting.start),
    ]
    vcs = _ProjectTracked()
    daemon = _daemon(tmp_path, graph, vcs=vcs)

    report = run_harvest(daemon, INSTANCE).transcripts

    (refused,) = report.refused
    assert refused.meeting_id == project_meeting.meeting_id and refused.scope == PROJECT
    assert "T-0" in refused.reason
    assert refused.retryable is True, "fixing the .gitignore must be enough to recover it"
    assert [e.meeting_id for e in report.captured] == [personal_meeting.meeting_id]
    assert daemon.storage.list_collection(scope=PROJECT, artifact=CAPTURES) == ()

    vcs.committable = False
    again = run_harvest(daemon, INSTANCE).transcripts

    assert [e.meeting_id for e in again.captured] == [project_meeting.meeting_id]
    assert capture_name(
        project_meeting.meeting_id, project_meeting.start, CaptureFormat.GRAPH
    ) in daemon.storage.list_collection(scope=PROJECT, artifact=CAPTURES)


def test_acceptance_an_unclassified_raise_is_a_named_refusal_after_the_cursor_is_saved(tmp_path, monkeypatch):
    graph = FakeGraph()
    graph.calendar = [_calendar_row("AAMk-one", START)]
    daemon = _daemon(tmp_path, graph)

    def broken(*args, **kwargs):
        raise ZeroDivisionError("bug")

    monkeypatch.setattr(pipelines, "acquire_transcripts", broken)
    report = run_harvest(daemon, INSTANCE).transcripts

    (refusal,) = report.refusals
    assert "ZeroDivisionError" in refusal
    assert daemon.storage.load_cursor(INSTANCE).token, "the harvest's cursor was saved first"


def test_a_harvest_with_no_graph_source_reports_the_stand_in_by_name(tmp_path):
    graph = FakeGraph()
    graph.calendar = [_calendar_row("AAMk-one", START)]
    daemon = _daemon(tmp_path, graph)
    daemon.transcript_sources[INSTANCE] = UnenrolledGraphTranscripts()

    report = run_harvest(daemon, INSTANCE).transcripts

    (refusal,) = report.refusals
    assert "no Graph connector is enrolled" in refusal
    assert daemon.storage.transcript_states(PERSONAL) == {}
    assert report.deferred == ((PERSONAL, "AAMk-one"),)


def test_a_graph_connector_with_no_source_of_its_own_is_a_named_refusal(tmp_path):
    """No fallback to the `"graph"` family source: another account is never asked."""
    graph = FakeGraph()
    graph.calendar = [_calendar_row("AAMk-one", START)]
    daemon = _daemon(tmp_path, graph)
    daemon.transcripts["graph"] = daemon.transcript_sources.pop(INSTANCE)

    report = run_harvest(daemon, INSTANCE).transcripts

    (refusal,) = report.refusals
    assert f"no transcript source is wired for {INSTANCE}" in refusal
    assert graph.asked("/me/events/") == []


def test_grep_finds_no_module_named_transcripts_graph():
    root = Path(__file__).resolve().parents[2]
    pattern = re.compile(r"transcripts\.graph\b")
    hits = [
        str(path)
        for folder in ("pm_ai", "tests")
        for path in (root / folder).rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert hits == []


def test_the_harvest_reads_the_calendar_with_the_same_cursor_type():
    """Sanity: the fake answers the calendar the connector reads."""
    graph = FakeGraph()
    graph.calendar = [_calendar_row("AAMk-one", START)]
    client = _client(graph)
    connector = GraphConnector(
        auth=Auth(),
        calendar=GraphCalendarFetch(
            client=client, instance=INSTANCE,
            windows=WindowPolicy(width=WIDTH, first_run_reach_back=REACH_BACK), now=lambda: NOW,
        ),
        categories=CategoryScopes(projects={}, registered=lambda p: False),
        now=lambda: NOW,
    )
    assert [m.meeting_id for m in connector.harvest(Cursor()).records] == ["AAMk-one"]


def test_an_enrolled_graph_row_wires_the_real_adapter_over_the_connectors_client(tmp_path):
    """`Daemon.transcripts["graph"]` is the adapter, spending the connector's client."""
    from pm_ai.connectors.graph import CLIENT_ID_KEY, REACH_BACK_KEY, WIDTH_KEY

    row = {
        "instance": INSTANCE,
        "system": "graph",
        "enabled": True,
        CLIENT_ID_KEY: "00000000-0000-0000-0000-000000000001",
        WIDTH_KEY: int(WIDTH.total_seconds() // 60),
        REACH_BACK_KEY: int(REACH_BACK.total_seconds() // 60),
    }
    build(tmp_path, "alpha", now=lambda: NOW).storage.write_artifact(
        json.dumps(row).encode("utf-8"),
        scope=DataScope(ScopeKind.APPLICATION),
        artifact="connectors/",
        name=f"{INSTANCE}.json",
    )

    daemon = build(tmp_path, "alpha", now=lambda: NOW)

    source = daemon.transcripts["graph"]
    assert isinstance(source, GraphTranscriptAdapter)
    assert daemon.transcript_sources[INSTANCE] is source
    assert set(daemon.transcripts) == {"graph", "manual"}, "no instance key beside the families"
    assert source.client is daemon.connectors[INSTANCE].calendar.client


# ── The client's two additions ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"code": "Forbidden", "innererror": {"code": TENANT_SWITCH}}},
        {"error": {"code": "Forbidden", "details": [{"code": TENANT_SWITCH}]}},
        {"error": {"code": "Forbidden", "innerError": {"code": "X", "innerError": {"code": TENANT_SWITCH}}}},
    ],
    ids=["lower-case innererror", "details list", "nested twice"],
)
def test_a_403_carries_every_detailed_code_however_graph_nests_it(body):
    from pm_ai.connectors.graph.client import ConsentChanged

    graph = FakeGraph()
    graph.lookups[JOIN] = GraphResponse(status=403, body=body)
    client = _client(graph)
    url = f"{GRAPH_BASE}/me/onlineMeetings?$filter=" + "JoinWebUrl%20eq%20%27" + JOIN.replace("%", "%25") + "%27"

    with pytest.raises(ConsentChanged) as refused:
        client.page(url)

    assert refused.value.names(TENANT_SWITCH)
    assert "provider prose" not in refused.value.reason


def test_download_returns_the_bytes_and_refreshes_once_on_a_401():
    graph = FakeGraph()
    graph.contents[("T-1", VTT)] = [GraphResponse(status=401), content(VTT_BYTES)]
    refreshed = []

    class Refreshing(Auth):
        def access_token(self, *, force_refresh: bool = False) -> str:
            refreshed.append(force_refresh)
            return "token-2" if force_refresh else "token-1"

    client = _client(graph, auth=Refreshing())
    raw = client.download(f"{GRAPH_BASE}/me/onlineMeetings/{ONLINE}/transcripts/T-1/content", accept=VTT)

    assert raw == VTT_BYTES
    assert refreshed == [False, True]
    assert [r.headers["Accept"] for r in graph.requests] == [VTT, VTT]


# ── Review follow-ups ────────────────────────────────────────────────────────


def test_two_graph_connectors_each_acquire_through_their_own_client(tmp_path):
    graph_a, graph_b = FakeGraph(), FakeGraph()
    graph_b.calendar = [_calendar_row("AAMk-b", START)]
    _one_off(graph_b, "AAMk-b")
    _recorded(graph_b)
    daemon = _daemon(tmp_path, graph_a)
    client_b = _client(graph_b)
    daemon.connectors["graph:other"] = GraphConnector(
        auth=Auth(),
        calendar=GraphCalendarFetch(
            client=client_b, instance="graph:other",
            windows=WindowPolicy(width=WIDTH, first_run_reach_back=REACH_BACK), now=lambda: NOW,
        ),
        categories=CategoryScopes(projects={}, registered=lambda p: False),
        now=lambda: NOW,
    )
    daemon.transcript_sources["graph:other"] = GraphTranscriptAdapter(client=client_b, instance="graph:other")

    report = run_harvest(daemon, "graph:other").transcripts

    assert [e.meeting_id for e in report.captured] == ["AAMk-b"]
    assert graph_b.asked("/me/events/AAMk-b") and graph_a.requests == []


def test_a_harvest_that_failed_outright_runs_no_acquisition(tmp_path):
    graph = FakeGraph()
    graph.calendar_answer = GraphResponse(status=500)
    daemon = _daemon(tmp_path, graph)
    daemon.meetings.put(meeting("AAMk-one", START))
    _one_off(graph)

    persisted = run_harvest(daemon, INSTANCE)

    assert persisted.transcripts is None
    assert graph.asked("/me/events/") == [] and graph.asked("onlineMeetings") == []


def test_graphs_year_one_sentinel_means_still_being_produced():
    graph = FakeGraph()
    held = _one_off(graph)
    row = transcript_row("T-live", START + timedelta(minutes=1), None)
    row["endDateTime"] = "0001-01-01T00:00:00Z"
    graph.listing(ONLINE, row)

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptSkipped)
    assert graph.asked("/content") == []


def test_a_listing_not_newest_first_is_paged_past_an_old_row():
    graph = FakeGraph()
    held = _one_off(graph, kind="occurrence")
    old = SINCE - timedelta(minutes=16)
    graph.paged(
        ONLINE,
        [
            transcript_row("T-old", old, old + timedelta(minutes=30)),
            transcript_row("T-newer", old + timedelta(days=1), old + timedelta(days=1, minutes=30)),
        ],
        [transcript_row("T-1", START + timedelta(minutes=1), START + timedelta(minutes=58))],
    )
    graph.contents[("T-1", VTT)] = content(VTT_BYTES)

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert graph.asked("$skiptoken") != [], "an older row followed by a newer one switches the stop off"
    assert isinstance(outcome, TranscriptCaptured) and outcome.captures[0].transcript_id == "T-1"


def test_the_unattributed_retry_refused_again_names_the_attribution_ban():
    graph = FakeGraph()
    held = _one_off(graph)
    _recorded(graph)
    graph.contents[("T-1", VTT)] = error(403, "Forbidden", "SpeakerAttributionNotAllowed")
    graph.contents[("T-1", UNATTRIBUTED)] = error(403, "Forbidden", "SpeakerAttributionNotAllowed")

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptRefused) and not outcome.retryable
    assert "SpeakerAttributionNotAllowed" in outcome.reason
    assert TRANSCRIPT_PERMISSION not in outcome.reason


def test_the_clients_tenant_switch_message_names_the_switch_not_a_grant():
    from pm_ai.connectors.graph.client import ConsentChanged

    graph = FakeGraph()
    graph.listings[ONLINE] = [error(403, "Forbidden", TENANT_SWITCH)]
    with pytest.raises(ConsentChanged) as refused:
        _client(graph).page(f"{GRAPH_BASE}/me/onlineMeetings/{ONLINE}/transcripts")

    assert "switched Graph access to transcripts off" in refused.value.reason
    assert "app registration;" not in refused.value.reason
    assert "Grant it" not in refused.value.reason


def test_error_codes_from_a_body_are_capped():
    from pm_ai.connectors.graph.client import ERROR_CODE_CAP, ConsentChanged

    body = {"error": {"code": "Forbidden", "details": [{"code": f"Code{n}"} for n in range(100)]}}
    graph = FakeGraph()
    graph.listings[ONLINE] = [GraphResponse(status=403, body=body)]
    with pytest.raises(ConsentChanged) as refused:
        _client(graph).page(f"{GRAPH_BASE}/me/onlineMeetings/{ONLINE}/transcripts")

    assert len(refused.value.codes) == ERROR_CODE_CAP
    assert "Code99" not in refused.value.reason


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-09-06T10:01:12", datetime(2026, 9, 6, 10, 1, 12, tzinfo=timezone.utc)),
        ("2026-09-06T12:01:12+02:00", datetime(2026, 9, 6, 10, 1, 12, tzinfo=timezone.utc)),
        ("2026-09-06T10:01:12.123456789Z", datetime(2026, 9, 6, 10, 1, 12, 123456, tzinfo=timezone.utc)),
        ("not an instant", None),
        ("", None),
        (12345, None),
        ("0001-01-01T00:00:00Z", None),
    ],
    ids=["no offset", "non-UTC offset", "nine fractional digits", "garbage", "empty", "not text", "sentinel"],
)
def test_instants_graph_may_send(raw, expected):
    from pm_ai.connectors.transcripts.graph_transcripts import _instant

    parsed = _instant(raw)
    assert parsed == expected
    if parsed is not None:
        assert parsed.utcoffset() == timedelta(0)


@pytest.mark.parametrize("where", ["lookup", "listing"])
def test_an_unreadable_lookup_or_listing_is_refused_by_name(where):
    graph = FakeGraph()
    held = _one_off(graph)
    if where == "lookup":
        graph.lookups[JOIN] = content(b"<html>not json</html>", kind="text/html")
    else:
        graph.listings[ONLINE] = [ok({"no": "value"})]

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptRefused) and not outcome.retryable
    assert "could not be read" in outcome.reason
    assert graph.asked("/content") == []


# ── Story 8s — what a five-day reach-back costs per harvest ──────────────────

FIVE_DAYS = timedelta(minutes=7200)
"""The reach-back `connector add graph` offers at Enter (story 8s)."""

UNTRANSCRIBED = 20
"""Ended meetings in the five days with no transcript yet and no closing answer:
twenty half-hour Teams meetings spread evenly across the five days, the count
recorded in 8s."""


def test_one_acquisition_over_five_days_asks_three_requests_per_untranscribed_meeting():
    """Story 8s measures, it does not bound: every harvest asks again about each
    ended meeting in the reach-back with no transcript yet (`33e`'s rule,
    unchanged). Measured per acquisition pass, on the recorded fake transport,
    excluding the harvest's `calendarView` call and any channel or chat walk:
    for one meeting that has no transcript, that is its event, its online
    meeting looked up by join link, and its transcript listing — three Graph
    requests — so twenty such meetings cost sixty requests per pass, spent
    against the client's fetch budget like every Graph walk."""
    graph = FakeGraph()
    held = []
    for index in range(UNTRANSCRIBED):
        start = NOW - FIVE_DAYS + timedelta(hours=2) + index * (FIVE_DAYS - timedelta(hours=4)) / UNTRANSCRIBED
        event_id = f"AAMk-{index:02d}"
        join = f"{JOIN}-{index:02d}"
        online = f"{ONLINE}-{index:02d}"
        graph.events[event_id] = ok(event_body(event_id, start, minutes=30, join=join))
        graph.lookups[join] = ok(lookup_body(online))
        graph.listing(online)
        held.append(meeting(event_id, start, minutes=30))
    assert all(NOW - FIVE_DAYS <= m.start and m.start + timedelta(minutes=30) < NOW for m in held)

    outcomes = _adapter(graph).acquire(held, since=NOW - FIVE_DAYS, stored={})

    assert len(outcomes) == UNTRANSCRIBED
    assert all(isinstance(outcome, TranscriptAbsent) for outcome in outcomes)
    assert len(graph.asked("/me/events/")) == UNTRANSCRIBED
    assert len(graph.asked("/me/onlineMeetings?")) == UNTRANSCRIBED
    assert len(graph.asked("/transcripts")) == UNTRANSCRIBED
    assert len(graph.requests) == 3 * UNTRANSCRIBED == 60


# ── Story 33j: a refused part keeps the parts before it ──────────────────────


def test_a_first_new_part_refused_for_good_returns_the_refusal_alone_and_asks_nothing_after_it():
    """Row "Part 1 answers 404, nothing stored": as before 33j."""
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-1", 1, 20), ("T-2", 25, 50))
    graph.contents[("T-1", VTT)] = error(404, "NotFound")

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptRefused) and not outcome.retryable
    assert "T-1" in outcome.reason and "404" in outcome.reason
    assert "T-2" not in outcome.reason
    asked = graph.asked("/content")
    assert asked and all("T-1" in unquote(url) for url in asked), "T-1 asked for, nothing after it"


def _refused_on(graph: FakeGraph, transcript_id: str, how: str) -> None:
    if how == "404":
        graph.contents[(transcript_id, VTT)] = error(404, "NotFound")
    elif how == "not UTF-8":
        graph.contents[(transcript_id, VTT)] = content(b"WEBVTT\n\n\xff\xfe broken")
    elif how == "attribution":
        graph.contents[(transcript_id, VTT)] = error(403, "Forbidden", "SpeakerAttributionNotAllowed")
        graph.contents[(transcript_id, UNATTRIBUTED)] = error(403, "Forbidden", "SpeakerAttributionNotAllowed")
    elif how == "permission":
        graph.contents[(transcript_id, VTT)] = error(403, "Forbidden")
    elif how == "rejected":
        graph.contents[(transcript_id, VTT)] = error(400, "BadRequest", "SomethingElse")
    elif how == "unreadable":
        from pm_ai.connectors.graph.client import GraphProtocolError

        graph.contents[(transcript_id, VTT)] = GraphProtocolError("an answer from a different origin.")
    elif how == "expired":
        graph.contents[(transcript_id, VTT)] = error(400, "BadRequest", "MeetingHasExpired")
    else:
        raise AssertionError(how)


_NAMED = {
    "404": "404",
    "not UTF-8": "UTF-8",
    "attribution": "SpeakerAttributionNotAllowed",
    "permission": TRANSCRIPT_PERMISSION,
    "rejected": "SomethingElse",
    "unreadable": "could not be downloaded",
    "expired": "expired",
}


@pytest.mark.parametrize("how", list(_NAMED))
def test_part_2_refused_for_good_returns_part_1_with_the_refusal_named(how):
    """Rows "Part 2 answers 404", "Attribution refused on part 2", "Expired on
    part 2", and the other refusals waiting cannot clear."""
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-1", 1, 20), ("T-2", 25, 50))
    _refused_on(graph, "T-2", how)

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptCaptured)
    assert [c.transcript_id for c in outcome.captures] == ["T-1"]
    assert outcome.captures[0].text == "WEBVTT\n\nT-1\n"
    assert outcome.refused_part is not None
    assert "T-2" in outcome.refused_part and _NAMED[how] in outcome.refused_part
    assert "provider prose" not in outcome.refused_part


def test_part_3_of_three_refused_returns_parts_1_and_2_in_time_order():
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-1", 1, 15), ("T-2", 20, 35), ("T-3", 40, 55))
    _refused_on(graph, "T-3", "not UTF-8")

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptCaptured)
    assert [c.transcript_id for c in outcome.captures] == ["T-1", "T-2"]
    assert outcome.refused_part is not None
    assert "T-3" in outcome.refused_part and "UTF-8" in outcome.refused_part
    assert "not asked for" not in outcome.refused_part, "no later part to name"


def test_part_2_of_three_refused_names_part_3_as_not_asked_for_and_never_downloads_it():
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-1", 1, 15), ("T-2", 20, 35), ("T-3", 40, 55))
    _refused_on(graph, "T-2", "404")

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptCaptured)
    assert [c.transcript_id for c in outcome.captures] == ["T-1"]
    assert outcome.refused_part is not None
    assert "T-2" in outcome.refused_part and "404" in outcome.refused_part
    assert "Later new part T-3 was not asked for" in outcome.refused_part
    assert not any("T-3" in unquote(url) for url in graph.asked("/content"))


@pytest.mark.parametrize("raw", [b"", b"  \r\n\t "], ids=["zero bytes", "whitespace"])
def test_an_empty_part_2_keeps_all_or_nothing_and_is_tried_again(raw):
    """Row "Empty body on part 2": a refusal waiting may clear returns nothing."""
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-1", 1, 20), ("T-2", 25, 50))
    graph.contents[("T-2", VTT)] = content(raw)

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptRefused) and outcome.retryable
    assert "T-2" in outcome.reason and "empty" in outcome.reason


def test_a_throttle_on_part_2_stops_the_run_and_returns_nothing_of_the_meeting():
    """Row "Throttle on part 2"."""
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-1", 1, 20), ("T-2", 25, 50))
    graph.contents[("T-2", VTT)] = throttled(90)
    later = meeting("AAMk-later", START - timedelta(hours=3))
    graph.events["AAMk-later"] = ok(event_body("AAMk-later", later.start, join=f"{JOIN}&later"))

    outcomes = _adapter(graph).acquire([held, later], since=SINCE, stored={})

    (only,) = outcomes
    assert isinstance(only, TranscriptRefused) and only.retryable
    assert only.retry_after == timedelta(seconds=90)
    assert graph.asked("AAMk-later") == [], "the run stopped at this meeting"


def test_an_unclassified_fault_on_part_2_returns_nothing_of_the_meeting(monkeypatch):
    from pm_ai.connectors.transcripts import graph_transcripts

    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-1", 1, 20), ("T-2", 25, 50))
    real = graph_transcripts._Run._download

    def broken(self, meeting_id, online_id, transcript):
        if transcript.transcript_id == "T-2":
            raise KeyError("an answer this adapter has no reading for")
        return real(self, meeting_id, online_id, transcript)

    monkeypatch.setattr(graph_transcripts._Run, "_download", broken)

    (outcome,) = _adapter(graph).acquire([held], since=SINCE, stored={})

    assert isinstance(outcome, TranscriptRefused) and "KeyError" in outcome.reason
    assert outcome.retryable is UNCLASSIFIED_FAULT_IS_RETRYABLE


def test_a_meeting_already_holding_part_1_returns_part_2_and_names_part_3():
    """Row "Meeting already holding part 1"."""
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-1", 1, 15), ("T-2", 20, 35), ("T-3", 40, 55))
    _refused_on(graph, "T-3", "404")

    (outcome,) = _adapter(graph).acquire(
        [held], since=SINCE, stored={held.meeting_id: (_stored("T-1", 1, 15),)}
    )

    assert isinstance(outcome, TranscriptCaptured)
    assert [c.transcript_id for c in outcome.captures] == ["T-2"]
    assert outcome.refused_part is not None and "T-3" in outcome.refused_part
    assert not any("T-1" in unquote(url) for url in graph.asked("/content"))


def test_a_refused_part_tried_again_alone_is_the_refusal_alone():
    """Row "Refused part tried again, still refused": T-2 is now the first new part."""
    graph = FakeGraph()
    held = _one_off(graph)
    _parts(graph, ("T-1", 1, 20), ("T-2", 25, 50))
    _refused_on(graph, "T-2", "404")

    (outcome,) = _adapter(graph).acquire(
        [held], since=SINCE, stored={held.meeting_id: (_stored("T-1", 1, 20),)}
    )

    assert isinstance(outcome, TranscriptRefused) and not outcome.retryable
    assert "T-2" in outcome.reason


def test_a_captured_outcomes_refused_part_is_never_blank():
    from pm_ai.domain.transcripts import TranscriptCapture

    capture = TranscriptCapture(
        meeting_id="AAMk-one", transcript_id="T-1", created=START, ended=START + timedelta(minutes=5),
        text="WEBVTT\n", attributed=True, format=CaptureFormat.GRAPH,
    )
    assert TranscriptCaptured("AAMk-one", (capture,)).refused_part is None
    with pytest.raises(ValueError):
        TranscriptCaptured("AAMk-one", (capture,), refused_part="  ")


def test_acceptance_part_2_answering_404_keeps_part_1_byte_identical_through_a_harvest(tmp_path):
    """Acceptance 1, through `run_harvest` over the fake Graph."""
    graph = FakeGraph()
    graph.calendar = [_calendar_row("AAMk-one", START)]
    _one_off(graph)
    graph.listing(
        ONLINE,
        transcript_row("T-2", START + timedelta(minutes=25), START + timedelta(minutes=50)),
        transcript_row("T-1", START + timedelta(minutes=1), START + timedelta(minutes=20)),
    )
    graph.contents[("T-1", VTT)] = content(VTT_BYTES)
    graph.contents[("T-2", VTT)] = error(404, "NotFound")
    daemon = _daemon(tmp_path, graph)

    persisted = run_harvest(daemon, INSTANCE)

    name = capture_name("AAMk-one", START, CaptureFormat.GRAPH)
    assert (daemon.storage.paths.resolve(PERSONAL, CAPTURES) / name).read_bytes() == VTT_BYTES
    assert [(p.part, p.transcript_id) for p in daemon.storage.transcript_parts(PERSONAL)["AAMk-one"]] == [
        (1, "T-1")
    ]
    report = persisted.transcripts
    assert report is not None
    (entry,) = report.captured
    assert entry.state is TranscriptStateKind.FETCHED and entry.capture_names == (name,)
    tail = entry.reason.split("Not stored:", 1)[1]
    assert "T-2" in tail and "404" in tail
    assert any("T-2" in refusal and "404" in refusal for refusal in report.refusals)
    assert report.ingested == ((PERSONAL, "AAMk-one"),)
