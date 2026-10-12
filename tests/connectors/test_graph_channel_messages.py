"""Story 33d — Teams channel messages, one test per matrix row.

Every row starts from `fixtures/graph/channel_messages.json` handed to a
scripted `GraphClient.transport`, so what is exercised is the whole path:
provider page, the walk in `channels.py`, and the event `GraphConnector` builds.
No test opens a socket — a URL no route answers fails the test.

**The fixture is not a tenant's answer.** The story asks for a redacted page
from a spike re-run against one listed channel; that re-run needs the PM's
sign-in and has not happened, so the page is written in the shape Microsoft
publishes for "List channel messages" and says so in its own `_provenance`.

The clock is frozen at `NOW` unless a test moves it.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pm_ai.app.pipelines import run_harvest
from pm_ai.app.wiring import build
from pm_ai.connectors.graph import (
    CHANNELS_KEY,
    CLIENT_ID_KEY,
    MAX_LABEL_LENGTH,
    REACH_BACK_KEY,
    WIDTH_KEY,
    CategoryScopes,
    ChannelListing,
    ChannelListRefused,
    GraphConnector,
    UnknownProject,
    graph_channel_listings,
)
from pm_ai.connectors.graph.auth import (
    GRAPH_SCOPES,
    OFFLINE_ACCESS,
    GraphDeviceCodeAuth,
    GraphUnreachable,
)
from pm_ai.connectors.graph.calendar import GraphCalendarFetch, WindowPolicy
from pm_ai.connectors.graph.channels import (
    EXCERPT_LENGTH,
    GraphChannelFetch,
    plain_text,
)
from pm_ai.connectors.graph.client import GRAPH_BASE, GraphClient, GraphRequest, GraphResponse
from pm_ai.core.ledger import parse_segment
from pm_ai.domain.events import MessagePayload, ObservedEventType, Provenance
from pm_ai.domain.harvest import Cursor, HarvestOutcome
from pm_ai.domain.health import Health, Probe
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.payload_fields import decode_payload

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
INSTANCE = "graph:work"
CLIENT_ID = "00000000-0000-0000-0000-000000000001"
WIDTH = timedelta(hours=24)
REACH_BACK = timedelta(days=7)

TEAM = "00000000-0000-0000-0000-0000000000aa"
CHANNEL_A = "19:fixture@thread.tacv2"
CHANNEL_B = "19:other@thread.tacv2"
SENDER = "11111111-1111-1111-1111-111111111111"
JANE = "2222aaaa-2222-2222-2222-222222222222"
BOT = "55555555-5555-5555-5555-555555555555"

APPLICATION = DataScope(ScopeKind.APPLICATION)
PERSONAL = DataScope(ScopeKind.PERSONAL)
ALPHA = DataScope(ScopeKind.PROJECT, "alpha")

FIXTURE = Path(__file__).parent / "fixtures" / "graph" / "channel_messages.json"


# ── The provider's page ──────────────────────────────────────────────────────


def _fixture() -> dict:
    """A fresh copy of the fixture page, safe to edit."""
    return copy.deepcopy(json.loads(FIXTURE.read_text())["page"])


def _threads() -> list[dict]:
    return _fixture()["value"]


def _thread(message_id: str) -> dict:
    (found,) = [thread for thread in _threads() if thread["id"] == message_id]
    return found


def _message(message_id: str = "1700000000500", *, at: datetime = NOW - timedelta(hours=1), **overrides) -> dict:
    """One plain root message with no replies, modelled on the fixture's root."""
    root = _thread("1700000000300")
    root.update(
        id=message_id,
        createdDateTime=at.isoformat().replace("+00:00", "Z"),
        lastModifiedDateTime=at.isoformat().replace("+00:00", "Z"),
        replies=[],
        mentions=[],
        body={"contentType": "text", "content": f"message {message_id}"},
    )
    root.update(overrides)
    return root


def _page(*threads: dict, next_link: str | None = None) -> GraphResponse:
    body: dict = {"value": list(threads)}
    if next_link is not None:
        body["@odata.nextLink"] = next_link
    return GraphResponse(status=200, body=body, headers={"Content-Type": "application/json"})


def _status(code: int, **headers: str) -> GraphResponse:
    return GraphResponse(status=code, body={"error": {"code": "x"}}, headers=headers)


EMPTY_CALENDAR = _page()


class Routes:
    """A scripted transport keyed by resource: the calendar, or one channel."""

    def __init__(
        self,
        *,
        calendar: list[GraphResponse] | None = None,
        channels: dict[str, list[GraphResponse]] | None = None,
        on_request=None,
    ) -> None:
        self.calendar = list(calendar if calendar is not None else [EMPTY_CALENDAR])
        self.channels = {key: list(value) for key, value in (channels or {}).items()}
        self.requests: list[GraphRequest] = []
        self.on_request = on_request

    def __call__(self, request: GraphRequest) -> GraphResponse:
        self.requests.append(request)
        if self.on_request is not None:
            self.on_request(request)
        if "/me/calendarView" in request.url:
            script = self.calendar
        else:
            matched = [
                channel
                for channel in self.channels
                if f"/channels/{channel}/messages" in request.url
            ]
            if not matched:
                raise AssertionError(f"no route answers {request!r}")
            script = self.channels[matched[0]]
        if not script:
            raise AssertionError(f"the fixture has no further page for {request!r}")
        return script.pop(0)

    def asked(self, channel: str) -> list[GraphRequest]:
        return [r for r in self.requests if f"/channels/{channel}/messages" in r.url]


class Auth:
    def access_token(self, *, force_refresh: bool = False) -> str:
        return "token-1"

    def check_health(self) -> Probe:
        return Probe(INSTANCE, Health.OK, "graph answered")


def _listing(channel: str = CHANNEL_A, *, label: str | None = "Build room") -> ChannelListing:
    return ChannelListing(team=TEAM, channel=channel, project="alpha", label=label)


def _connector(
    routes: Routes,
    *channels: ChannelListing,
    clock=lambda: NOW,
    page_cap: int | None = None,
) -> GraphConnector:
    identity = Auth()
    client = GraphClient(auth=identity, transport=routes, now=clock)
    if page_cap is not None:
        client.page_cap = page_cap
    return GraphConnector(
        auth=identity,
        calendar=GraphCalendarFetch(
            client=client,
            instance=INSTANCE,
            windows=WindowPolicy(width=WIDTH, first_run_reach_back=REACH_BACK),
            now=clock,
        ),
        categories=CategoryScopes(projects={}, registered=lambda project: True),
        now=clock,
        channels=channels or (_listing(),),
        messages=GraphChannelFetch(
            client=client, instance=INSTANCE, reach_back=REACH_BACK, now=clock
        ),
    )


def _cursor(*, calendar: datetime | None = None, **channels: datetime) -> Cursor:
    """A cursor in this slice's form. Keys are channel ids, passed by a dict."""
    return Cursor(
        json.dumps(
            {
                "calendar": None if calendar is None else calendar.isoformat(),
                "channels": {key: value.isoformat() for key, value in channels.items()},
            }
        ).encode()
    )


def _positions(cursor: Cursor) -> dict:
    return json.loads(cursor.token.decode())


def _harvest(*pages: GraphResponse, since: Cursor = Cursor(), **options):
    routes = Routes(channels={CHANNEL_A: list(pages)})
    return _connector(routes, **options).harvest(since), routes


def _messages(result) -> list:
    return [event for event in result.events if event.type is ObservedEventType.MESSAGE_POSTED]


def _by_id(result) -> dict[str, object]:
    return {event.source_ref.native_id.split("/", 1)[1]: event for event in _messages(result)}


# ── The request ──────────────────────────────────────────────────────────────


def test_the_request_asks_for_fifty_with_replies_inline_and_nothing_else():
    """`$top=50&$expand=replies`, asserted on the recorded request."""
    _, routes = _harvest(_page(*_threads()))
    (request,) = routes.asked(CHANNEL_A)
    assert request.url == (
        f"{GRAPH_BASE}/teams/{TEAM}/channels/{CHANNEL_A}/messages?$top=50&$expand=replies"
    )
    assert "$filter" not in request.url and "$orderby" not in request.url


# ── Listed channel, new messages ─────────────────────────────────────────────


def test_three_messages_since_the_position_are_three_events_and_the_position_moves():
    position = datetime(2026, 10, 9, 8, 30, tzinfo=timezone.utc)
    result, _ = _harvest(_page(*_threads()), since=_cursor(**{CHANNEL_A: position}))

    assert sorted(_by_id(result)) == ["1700000000300", "1700000000301", "1700000000302"]
    for event in _messages(result):
        assert event.scope == ALPHA
    newest = datetime(2026, 10, 9, 10, 30, 0, 456000, tzinfo=timezone.utc)
    assert datetime.fromisoformat(_positions(result.cursor)["channels"][CHANNEL_A]) == newest
    assert result.outcome is HarvestOutcome.HARVESTED
    assert result.failure is None


def test_through_run_harvest_every_message_is_in_the_projects_log_and_none_personal(tmp_path):
    """Acceptance: a row listing one channel mapped to `alpha`."""
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    daemon.connectors[INSTANCE] = _connector(Routes(channels={CHANNEL_A: [_page(*_threads())]}))

    persisted = run_harvest(daemon, INSTANCE)

    assert persisted.persisted == 4
    alpha = _log(daemon, ALPHA)
    entries = [e for e in parse_segment(alpha) if e.category is ObservedEventType.MESSAGE_POSTED]
    assert len(entries) == 4
    assert "message_posted" not in _log(daemon, PERSONAL)
    decoded = {decode_payload(entry).excerpt for entry in entries}  # type: ignore[union-attr]
    assert "plain reply, sent as text" in decoded


def test_every_message_event_is_unattributed_and_carries_no_id():
    """Acceptance: `authored_by` unknown, no `id` set (AD-34, AD-36)."""
    result, _ = _harvest(_page(*_threads()))
    assert _messages(result)
    for event in _messages(result):
        assert event.authored_by is Provenance.UNKNOWN
        assert getattr(event, "id", None) is None


def test_the_declared_scope_set_is_the_one_33a_declared():
    """Acceptance: no new permission — nobody consents again."""
    assert GraphDeviceCodeAuth.scopes == GRAPH_SCOPES
    assert GRAPH_SCOPES - {OFFLINE_ACCESS} == {
        "Calendars.Read",
        "Chat.Read",
        "ChannelMessage.Read.All",
        "OnlineMeetingTranscript.Read.All",
        "OnlineMeetings.Read",
        "Team.ReadBasic.All",
        "Channel.ReadBasic.All",
    }


# ── The settings file ────────────────────────────────────────────────────────


def _row(**overrides) -> dict:
    row = {
        "instance": INSTANCE,
        "system": "graph",
        "enabled": True,
        CLIENT_ID_KEY: CLIENT_ID,
        WIDTH_KEY: int(WIDTH.total_seconds() // 60),
        REACH_BACK_KEY: int(REACH_BACK.total_seconds() // 60),
    }
    row.update(overrides)
    return row


def _enrol(tmp_path: Path, **overrides) -> None:
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    daemon.storage.write_artifact(
        json.dumps(_row(**overrides)).encode("utf-8"),
        scope=APPLICATION,
        artifact="connectors/",
        name=f"{INSTANCE}.json",
    )


def test_no_channel_list_is_calendar_only(tmp_path):
    _enrol(tmp_path)
    connector = build(tmp_path, "alpha", now=lambda: NOW).connectors[INSTANCE]
    assert isinstance(connector, GraphConnector)
    assert connector.channels == ()

    routes = Routes()
    alone = _connector(routes)
    alone.channels = ()
    result = alone.harvest(Cursor())
    assert [r.url for r in routes.requests if "/teams/" in r.url] == []
    assert result.outcome is HarvestOutcome.EMPTY


def test_a_listed_channel_reaches_the_built_connector(tmp_path):
    _enrol(
        tmp_path,
        **{CHANNELS_KEY: [{"team": TEAM, "channel": CHANNEL_A, "project": "alpha", "label": "Build room"}]},
    )
    connector = build(tmp_path, "alpha", now=lambda: NOW).connectors[INSTANCE]
    assert isinstance(connector, GraphConnector)
    assert connector.channels == (_listing(),)
    assert connector.messages is not None
    assert connector.messages.client is connector.calendar.client


@pytest.mark.parametrize(
    ("listed", "named"),
    [
        ({"team": TEAM}, "list"),
        ([{"channel": CHANNEL_A, "project": "alpha"}], "entry 1"),
        ([{"team": TEAM, "project": "alpha"}], "entry 1"),
        ([{"team": TEAM, "channel": "  ", "project": "alpha"}], "entry 1"),
        (["19:x@thread.tacv2"], "entry 1"),
        ([{"team": TEAM, "channel": "19:a b", "project": "alpha"}], "entry 1"),
        ([{"team": TEAM, "channel": CHANNEL_A, "project": "alpha", "label": 3}], "entry 1"),
    ],
)
def test_a_malformed_list_builds_nothing_and_names_the_entry(listed, named):
    with pytest.raises(ChannelListRefused) as refused:
        graph_channel_listings({CHANNELS_KEY: listed}, registered=lambda p: True)
    assert named in str(refused.value)


def test_a_malformed_list_leaves_the_daemon_composing_with_no_graph_connector(tmp_path, capsys):
    _enrol(tmp_path, **{CHANNELS_KEY: [{"team": TEAM, "project": "alpha"}]})
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    assert INSTANCE not in daemon.connectors
    assert "channel entry 1" in capsys.readouterr().err


def test_an_unenrolled_project_is_refused_by_name():
    with pytest.raises(UnknownProject) as refused:
        graph_channel_listings(
            {CHANNELS_KEY: [{"team": TEAM, "channel": CHANNEL_A, "project": "ghost"}]},
            registered=lambda project: project == "alpha",
        )
    assert "'ghost'" in str(refused.value)
    assert CHANNEL_A in str(refused.value)


def test_a_channel_listed_twice_under_any_team_or_project_is_refused_naming_the_id():
    listed = [
        {"team": TEAM, "channel": CHANNEL_A, "project": "alpha"},
        {"team": "another-team", "channel": CHANNEL_A.upper(), "project": "beta"},
    ]
    with pytest.raises(ChannelListRefused) as refused:
        graph_channel_listings({CHANNELS_KEY: listed}, registered=lambda p: True)
    assert CHANNEL_A.upper() in str(refused.value)
    assert "entries 1 and 2" in str(refused.value)


# ── One message ──────────────────────────────────────────────────────────────


def test_an_html_body_with_a_mention_is_plain_text_and_the_mention_a_lowercased_handle():
    result, _ = _harvest(_page(*_threads()))
    event = _by_id(result)["1700000000300"]
    payload = event.payload
    assert isinstance(payload, MessagePayload)
    assert payload.excerpt == "Hi Jane Doe, the build is green & ready.\nShip it?"
    assert payload.mentions == (f"graph:{JANE}",)
    assert str(event.actor) == f"graph:{SENDER}"
    assert payload.channel == "Build room"
    assert event.source_ref.kind == "message"
    assert str(event.source_ref) == f"graph:alpha:message:{CHANNEL_A}/1700000000300"
    assert event.occurred_at == datetime(2026, 10, 9, 9, 0, 0, 123000, tzinfo=timezone.utc)


def test_a_mention_without_a_user_is_left_out_and_the_text_kept():
    result, _ = _harvest(_page(*_threads()))
    payload = _by_id(result)["1700000000302"].payload
    assert payload.mentions == ()
    assert payload.excerpt == "Telling General now."


def test_no_mentions_list_is_absent_rather_than_empty():
    root = _message()
    del root["mentions"]
    result, _ = _harvest(_page(root))
    assert _by_id(result)["1700000000500"].payload.mentions is None


def test_a_body_sent_as_text_is_taken_as_is():
    result, _ = _harvest(_page(*_threads()))
    assert _by_id(result)["1700000000301"].payload.excerpt == "plain reply, sent as text"


def test_text_with_markup_characters_is_not_parsed_as_html():
    root = _message(body={"contentType": "text", "content": "a <b>literal</b> &amp;"})
    result, _ = _harvest(_page(root))
    assert _by_id(result)["1700000000500"].payload.excerpt == "a <b>literal</b> &amp;"


def test_the_channel_id_stands_in_for_a_missing_label():
    routes = Routes(channels={CHANNEL_A: [_page(_message())]})
    result = _connector(routes, _listing(label=None)).harvest(Cursor())
    assert _messages(result)[0].payload.channel == CHANNEL_A


def test_a_long_message_is_cut_to_the_first_280_characters():
    text = "x" * 3000 + "y" * 3000
    result, _ = _harvest(_page(_message(body={"contentType": "text", "content": text})))
    excerpt = _by_id(result)["1700000000500"].payload.excerpt
    assert EXCERPT_LENGTH == 280
    assert excerpt == "x" * 280


def test_a_bot_sender_is_emitted_with_an_application_handle():
    result, _ = _harvest(_page(*_threads()))
    event = _by_id(result)["1700000000100"]
    assert str(event.actor) == f"graph_app:{BOT}"
    assert event.payload.excerpt == "Nightly build passed.\nLogs attached."


def test_html_to_text_decodes_entities_and_breaks_blocks_once():
    assert plain_text("<p>a</p><p>b&nbsp;&lt;c&gt;</p>") == "a\nb\xa0<c>"
    assert plain_text('<div>one<br>two</div><img src="x" alt="y">') == "one\ntwo"


# ── Rows that are not messages, and rows that cannot be read ─────────────────


@pytest.mark.parametrize("kind", ["systemEventMessage", "typing", "chatEvent", "unknownFutureValue", None])
def test_a_row_that_is_not_a_message_is_skipped_without_a_refusal(kind):
    root = _message(messageType=kind)
    result, _ = _harvest(_page(root))
    assert _messages(result) == []
    assert result.refusals == ()
    assert result.outcome is HarvestOutcome.EMPTY


@pytest.mark.parametrize(
    "broken",
    [
        {"id": None},
        {"id": "  "},
        {"body": None},
        {"body": {"contentType": "markdown", "content": "x"}},
        {"createdDateTime": None},
        {"createdDateTime": "yesterday"},
        {"createdDateTime": "2026-10-09T11:00:00"},
    ],
)
def test_an_unreadable_row_is_refused_by_name_and_the_page_continues(broken):
    bad = _message("1700000000601", **broken)
    good = _message("1700000000602")
    result, _ = _harvest(_page(bad, good))

    assert list(_by_id(result)) == ["1700000000602"]
    (refusal,) = result.refusals
    assert "Build room" in refusal.reason
    if broken.get("id", "x") not in (None, "  "):
        assert refusal.identifier == f"{CHANNEL_A}/1700000000601"


def test_a_naive_creation_time_is_refused_as_having_no_timezone():
    result, _ = _harvest(_page(_message(createdDateTime="2026-10-09T11:00:00")))
    (refusal,) = result.refusals
    assert "no timezone" in refusal.reason


# ── The walk ─────────────────────────────────────────────────────────────────

NEXT = f"{GRAPH_BASE}/teams/{TEAM}/channels/{CHANNEL_A}/messages?$skiptoken=page2"


def test_a_message_edited_after_harvest_is_not_emitted_again(tmp_path):
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    first = _message(at=NOW - timedelta(hours=2))
    edited = copy.deepcopy(first)
    edited["lastModifiedDateTime"] = NOW.isoformat().replace("+00:00", "Z")
    edited["body"] = {"contentType": "text", "content": "edited"}
    routes = Routes(channels={CHANNEL_A: [_page(first), _page(edited)]}, calendar=[EMPTY_CALENDAR, EMPTY_CALENDAR])
    daemon.connectors[INSTANCE] = _connector(routes)

    assert run_harvest(daemon, INSTANCE).persisted == 1
    second = run_harvest(daemon, INSTANCE)
    assert second.persisted == 0
    assert "edited" not in _log(daemon, ALPHA)


def test_an_old_thread_with_a_fresh_reply_emits_the_reply_and_the_walk_continues():
    root = _message("1600000000000", at=NOW - timedelta(days=365))
    reply = _message("1700000000777", at=NOW - timedelta(minutes=5))
    reply["replyToId"] = root["id"]
    root["replies"] = [reply]
    older = _message("1600000000001", at=NOW - timedelta(days=30))
    position = NOW - timedelta(days=1)

    result, routes = _harvest(
        _page(root, next_link=NEXT),
        _page(older),
        since=_cursor(**{CHANNEL_A: position}),
    )

    assert list(_by_id(result)) == ["1700000000777"]
    assert len(routes.asked(CHANNEL_A)) == 2, "the reply kept the first page in range"


def test_a_page_wholly_older_than_the_position_stops_the_walk():
    position = NOW - timedelta(days=1)
    result, routes = _harvest(
        _page(_message("1600000000002", at=NOW - timedelta(days=2)), next_link=NEXT),
        since=_cursor(**{CHANNEL_A: position}),
    )
    assert len(routes.asked(CHANNEL_A)) == 1
    assert _messages(result) == []
    assert result.failure is None


def test_a_thread_over_the_inline_reply_page_emits_what_arrived_and_is_refused():
    root = _thread("1700000000300")
    root["replies@odata.nextLink"] = f"{GRAPH_BASE}/teams/{TEAM}/channels/{CHANNEL_A}/messages/1700000000300/replies?$skiptoken=r2"
    result, routes = _harvest(_page(root))

    assert sorted(_by_id(result)) == ["1700000000300", "1700000000301", "1700000000302"]
    (refusal,) = result.refusals
    assert refusal.identifier == f"{CHANNEL_A}/1700000000300"
    assert "more replies" in refusal.reason
    assert len(routes.asked(CHANNEL_A)) == 1, "no second reply paging"


def test_a_first_run_reaches_back_as_far_as_the_row_says():
    inside = _message("1700000000801", at=NOW - REACH_BACK + timedelta(minutes=1))
    outside = _message("1700000000802", at=NOW - REACH_BACK - timedelta(minutes=1))
    result, _ = _harvest(_page(inside, outside))
    assert list(_by_id(result)) == ["1700000000801"]


def test_a_first_run_with_nothing_found_stores_the_reach_back_as_the_position():
    result, _ = _harvest(_page())
    assert datetime.fromisoformat(_positions(result.cursor)["channels"][CHANNEL_A]) == NOW - REACH_BACK


def test_an_old_calendar_only_cursor_reads_as_the_calendar_and_channels_first_run():
    calendar_position = NOW - timedelta(days=3)
    routes = Routes(channels={CHANNEL_A: [_page(_message(at=NOW - timedelta(days=6)))]})
    result = _connector(routes).harvest(Cursor(calendar_position.isoformat().encode()))

    (calendar,) = [r for r in routes.requests if "/me/calendarView" in r.url]
    assert "startDateTime=" + calendar_position.strftime("%Y-%m-%dT%H:%M:%SZ") in calendar.url
    assert list(_by_id(result)) == ["1700000000500"], "a first run reaches back seven days"
    assert result.failure is None


def test_an_empty_page_with_a_next_link_does_not_stop_the_walk():
    result, routes = _harvest(_page(next_link=NEXT), _page(_message()))
    assert len(routes.asked(CHANNEL_A)) == 2
    assert list(_by_id(result)) == ["1700000000500"]


def test_the_same_message_seen_twice_in_one_walk_is_emitted_once():
    message = _message()
    result, _ = _harvest(_page(message, next_link=NEXT), _page(copy.deepcopy(message)))
    assert len(_messages(result)) == 1


# ── Walk cut short ───────────────────────────────────────────────────────────


def test_a_walk_cut_by_the_page_cap_advances_to_the_newest_and_says_what_it_skipped():
    newest = NOW - timedelta(minutes=10)
    position = NOW - timedelta(days=5)
    result, _ = _harvest(
        _page(_message("1700000000901", at=newest), next_link=NEXT),
        since=_cursor(**{CHANNEL_A: position}),
        page_cap=1,
    )

    assert list(_by_id(result)) == ["1700000000901"]
    assert datetime.fromisoformat(_positions(result.cursor)["channels"][CHANNEL_A]) == newest
    assert result.failure is not None and result.failure.retryable is True
    assert "Build room" in result.failure.reason
    assert "skipped" in result.failure.reason
    assert newest.isoformat() in result.failure.reason
    assert result.coverage is None


def test_a_walk_cut_by_the_budget_advances_to_the_newest_and_withholds_coverage():
    clock = [NOW]
    newest = NOW - timedelta(minutes=10)

    def _advance(request: GraphRequest) -> None:
        if "$skiptoken" in request.url:
            raise AssertionError("the budget should have stopped the walk first")
        if "/channels/" in request.url:
            clock[0] = NOW + timedelta(hours=1)

    routes = Routes(
        channels={CHANNEL_A: [_page(_message("1700000000902", at=newest), next_link=NEXT)]},
        on_request=_advance,
    )
    result = _connector(routes, clock=lambda: clock[0]).harvest(
        _cursor(**{CHANNEL_A: NOW - timedelta(days=5)})
    )

    assert list(_by_id(result)) == ["1700000000902"]
    assert datetime.fromisoformat(_positions(result.cursor)["channels"][CHANNEL_A]) == newest
    assert result.failure is not None and result.failure.retryable is True
    assert "time budget" in result.failure.reason
    assert result.coverage is None


# ── Failures per resource ────────────────────────────────────────────────────


def _two(a: list[GraphResponse], b: list[GraphResponse], **cursor: datetime):
    routes = Routes(calendar=[EMPTY_CALENDAR], channels={CHANNEL_A: a, CHANNEL_B: b})
    connector = _connector(routes, _listing(CHANNEL_A, label="Build room"), _listing(CHANNEL_B, label="Release room"))
    return connector.harvest(_cursor(**cursor) if cursor else Cursor()), routes


def test_a_channel_pm_ai_is_not_a_member_of_fails_alone_and_keeps_its_position(tmp_path):
    previous = NOW - timedelta(days=2)
    result, _ = _two([_page(_message())], [_status(403)], **{CHANNEL_B: previous})

    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None and result.failure.retryable is False
    assert "Release room" in result.failure.reason
    assert "not a member" in result.failure.reason
    assert "app registration" not in result.failure.reason, "not the tenant-wide consent reading"
    positions = _positions(result.cursor)["channels"]
    assert datetime.fromisoformat(positions[CHANNEL_B]) == previous
    assert CHANNEL_A in positions
    assert result.coverage is None
    assert list(_by_id(result)) == ["1700000000500"]

    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    daemon.connectors[INSTANCE] = _connector(
        Routes(channels={CHANNEL_A: [_page(_message())], CHANNEL_B: [_status(403)]}),
        _listing(CHANNEL_A),
        _listing(CHANNEL_B, label="Release room"),
    )
    assert run_harvest(daemon, INSTANCE).persisted == 1
    stored = daemon.storage.load_cursor(INSTANCE)
    assert CHANNEL_A in _positions(stored)["channels"]
    assert CHANNEL_B not in _positions(stored)["channels"]


@pytest.mark.parametrize("code", [404, 400])
def test_a_mistyped_channel_id_fails_that_channel_and_names_the_entry(code):
    result, _ = _two([_page(_message())], [_status(code)])

    assert result.failure is not None and result.failure.retryable is False
    assert str(code) in result.failure.reason
    assert CHANNEL_B in result.failure.reason and TEAM in result.failure.reason
    assert "mistyped" in result.failure.reason
    assert list(_by_id(result)) == ["1700000000500"]
    assert CHANNEL_A in _positions(result.cursor)["channels"]


def test_two_resources_failing_differently_are_one_failure_with_the_largest_hint():
    result, _ = _two([_status(429, **{"Retry-After": "90"})], [_status(403)])

    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None
    assert result.failure.retryable is False
    assert result.failure.retry_after == timedelta(seconds=90)
    assert "Build room" in result.failure.reason and "Release room" in result.failure.reason


def test_a_calendar_sign_in_failure_ends_the_run_before_any_channel():
    routes = Routes(calendar=[_status(401), _status(401)], channels={CHANNEL_A: [_page(_message())]})
    result = _connector(routes).harvest(Cursor())

    assert routes.asked(CHANNEL_A) == []
    assert result.failure is not None and result.failure.retryable is False
    assert result.outcome is HarvestOutcome.FAILED


# ── The run's one answer ─────────────────────────────────────────────────────


def test_an_empty_channel_and_an_empty_calendar_are_empty_with_a_window():
    clock = iter(NOW + timedelta(seconds=step) for step in range(1000))
    readings: list[datetime] = []

    def _tick() -> datetime:
        readings.append(next(clock))
        return readings[-1]

    routes = Routes(channels={CHANNEL_A: [_page()]})
    result = _connector(routes, clock=_tick).harvest(Cursor())

    assert result.outcome is HarvestOutcome.EMPTY
    assert result.answered_at is not None
    assert result.coverage is not None
    assert result.coverage.start == result.answered_at
    assert result.coverage.end > result.coverage.start, "the window ends at the channel's walk"


def test_a_channel_that_answered_first_supplies_the_answer_instant_when_the_calendar_did_not():
    routes = Routes(calendar=[_status(503)], channels={CHANNEL_A: [_page(_message())]})
    result = _connector(routes).harvest(Cursor())
    assert result.answered_at == NOW
    assert result.coverage is None
    assert result.failure is not None and result.failure.retryable is True


def test_the_same_message_id_in_two_channels_has_two_references():
    result, _ = _two([_page(_message("1700000000555"))], [_page(_message("1700000000555"))])
    references = {str(event.source_ref) for event in _messages(result)}
    assert references == {
        f"graph:alpha:message:{CHANNEL_A}/1700000000555",
        f"graph:alpha:message:{CHANNEL_B}/1700000000555",
    }


def test_the_sample_includes_a_message_built_by_the_same_mapping():
    samples = _connector(Routes()).sample_events()
    assert {event.type for event in samples} == {
        ObservedEventType.CALENDAR_EVENT_HELD,
        ObservedEventType.MESSAGE_POSTED,
    }


# ── Helpers that read the disk ───────────────────────────────────────────────


def _log(daemon, scope: DataScope) -> str:
    return "".join(
        daemon.storage.read_event_log_segment(scope=scope, name=name)
        for name in daemon.storage.event_log_segments(scope=scope)
    )


def test_a_page_with_no_value_array_holds_the_position_and_claims_no_window():
    position = NOW - timedelta(days=1)
    unreadable = GraphResponse(status=200, body={"@odata.nextLink": NEXT}, headers={})
    result, routes = _harvest(
        unreadable, _page(_message()), since=_cursor(**{CHANNEL_A: position})
    )
    assert len(routes.asked(CHANNEL_A)) == 2
    assert list(_by_id(result)) == ["1700000000500"]
    assert datetime.fromisoformat(_positions(result.cursor)["channels"][CHANNEL_A]) == position
    assert result.coverage is None
    assert any("no `value` array" in refusal.reason for refusal in result.refusals)


# ── Review findings (story 33d, second pass) ─────────────────────────────────


def test_a_future_message_is_emitted_and_never_moves_the_position_past_now():
    ahead = _message("1700000001001", at=NOW + timedelta(hours=2))
    result, _ = _harvest(_page(ahead), since=_cursor(**{CHANNEL_A: NOW - timedelta(days=1)}))
    assert list(_by_id(result)) == ["1700000001001"]
    assert _by_id(result)["1700000001001"].occurred_at == NOW + timedelta(hours=2)
    assert datetime.fromisoformat(_positions(result.cursor)["channels"][CHANNEL_A]) == NOW


def test_a_deleted_message_is_skipped_neither_emitted_nor_refused():
    tombstone = _message("1700000001002", deletedDateTime="2026-10-09T11:00:00Z")
    tombstone["body"] = {"contentType": "html", "content": ""}
    result, _ = _harvest(_page(tombstone, _message("1700000001003")))
    assert list(_by_id(result)) == ["1700000001003"]
    assert result.refusals == ()


def test_a_thread_with_unread_replies_withholds_the_runs_coverage():
    root = _thread("1700000000300")
    root["replies@odata.nextLink"] = f"{GRAPH_BASE}/teams/{TEAM}/x?$skiptoken=r"
    result, _ = _harvest(_page(root))
    assert _messages(result)
    assert result.failure is None
    assert result.coverage is None


def test_an_undatable_thread_counts_as_older_and_the_walk_stops():
    undatable = _message("1700000001004", createdDateTime="not a time")
    del undatable["lastModifiedDateTime"]
    result, routes = _harvest(
        _page(undatable, next_link=NEXT),
        since=_cursor(**{CHANNEL_A: NOW - timedelta(days=1)}),
    )
    assert len(routes.asked(CHANNEL_A)) == 1
    assert result.failure is None


def test_a_refused_row_does_not_move_the_position():
    position = NOW - timedelta(days=1)
    unreadable = _message("1700000001005", at=NOW - timedelta(hours=1), body=None)
    result, _ = _harvest(_page(unreadable), since=_cursor(**{CHANNEL_A: position}))
    assert len(result.refusals) == 1
    assert datetime.fromisoformat(_positions(result.cursor)["channels"][CHANNEL_A]) == position


@pytest.mark.parametrize("bad", ["17/00", "17\\00", "17?00", "17#00", "17\x7f00", "17​00", "17 00"])
def test_a_message_id_with_an_unusable_character_is_refused(bad):
    result, _ = _harvest(_page(_message(bad), _message("1700000001006")))
    assert list(_by_id(result)) == ["1700000001006"]
    (refusal,) = result.refusals
    assert "id" in refusal.reason


def _edited_old_root() -> dict:
    """Created before the position, modified after it: in range, never emitted."""
    root = _message("1600000001007", at=NOW - timedelta(days=3))
    root["lastModifiedDateTime"] = NOW.isoformat().replace("+00:00", "Z")
    return root


def test_a_cut_that_saw_nothing_says_the_position_did_not_move():
    position = NOW - timedelta(days=1)
    result, _ = _harvest(
        _page(next_link=NEXT), since=_cursor(**{CHANNEL_A: position}), page_cap=1
    )
    assert result.failure is not None
    assert "did not move" in result.failure.reason
    assert datetime.fromisoformat(_positions(result.cursor)["channels"][CHANNEL_A]) == position


def test_a_cut_that_saw_only_older_messages_says_the_position_did_not_move():
    position = NOW - timedelta(days=1)
    result, _ = _harvest(
        _page(_edited_old_root(), next_link=NEXT),
        since=_cursor(**{CHANNEL_A: position}),
        page_cap=1,
    )
    assert result.failure is not None
    assert "did not move" in result.failure.reason
    assert "skipped" not in result.failure.reason


def test_a_cut_never_names_a_loss_below_the_stored_position():
    position = NOW - timedelta(days=1)
    newest = NOW - timedelta(hours=1)
    result, _ = _harvest(
        _page(_message("1700000001008", at=newest), _edited_old_root(), next_link=NEXT),
        since=_cursor(**{CHANNEL_A: position}),
        page_cap=1,
    )
    reason = result.failure.reason
    assert (NOW - timedelta(days=3)).isoformat() not in reason, "below the position"
    assert f"at or after {position.isoformat()}" in reason


def test_a_channel_sign_in_failure_stops_the_run_and_names_what_was_not_attempted():
    result, routes = _two([_status(401), _status(401)], [_page(_message())])
    assert routes.asked(CHANNEL_B) == []
    assert result.failure is not None and result.failure.retryable is False
    assert "Not attempted this run" in result.failure.reason
    assert "'Release room'" in result.failure.reason


def test_a_calendar_sign_in_failure_names_every_channel_as_not_attempted():
    routes = Routes(calendar=[_status(401), _status(401)], channels={CHANNEL_A: [_page()]})
    result = _connector(routes).harvest(Cursor())
    assert "Not attempted this run" in result.failure.reason
    assert "'Build room'" in result.failure.reason


def test_a_calendar_fault_names_every_channel_as_not_attempted(monkeypatch):
    connector = _connector(Routes(), _listing(CHANNEL_A), _listing(CHANNEL_B, label="Release room"))

    def _raise(**_):
        raise RuntimeError("boom")

    monkeypatch.setattr(connector.calendar, "fetch", _raise)
    result = connector.harvest(Cursor())
    assert result.outcome is HarvestOutcome.FAILED
    assert "'Build room'" in result.failure.reason
    assert "'Release room'" in result.failure.reason


@pytest.mark.parametrize("fault", [ValueError, TypeError])
def test_a_fault_building_one_message_event_costs_only_that_row(monkeypatch, fault):
    routes = Routes(channels={CHANNEL_A: [_page(_message("1700000001009"), _message("1700000001010"))]})
    connector = _connector(routes)
    real = connector._message_event

    def _flaky(listing, row):
        if row.message_id == "1700000001009":
            raise fault("bad")
        return real(listing, row)

    monkeypatch.setattr(connector, "_message_event", _flaky)
    result = connector.harvest(Cursor())
    assert list(_by_id(result)) == ["1700000001010"]
    (refusal,) = result.refusals
    assert fault.__name__ in refusal.reason


def test_cursor_positions_are_keyed_by_the_case_folded_channel_id():
    position = NOW - timedelta(hours=2)
    old = _message("1700000001011", at=NOW - timedelta(hours=3))
    new = _message("1700000001012", at=NOW - timedelta(hours=1))
    result, _ = _harvest(_page(new, old), since=_cursor(**{CHANNEL_A.upper(): position}))
    assert list(_by_id(result)) == ["1700000001012"], "the upper-case key was read"
    assert set(_positions(result.cursor)["channels"]) == {CHANNEL_A.casefold()}


@pytest.mark.parametrize(
    "label", ["x" * (MAX_LABEL_LENGTH + 1), "bell\x07", "zero​width"]
)
def test_a_label_too_long_or_holding_a_control_character_is_refused(label):
    with pytest.raises(ChannelListRefused) as refused:
        graph_channel_listings(
            {CHANNELS_KEY: [{"team": TEAM, "channel": CHANNEL_A, "project": "alpha", "label": label}]},
            registered=lambda p: True,
        )
    assert "entry 1" in str(refused.value)


def test_a_label_at_the_bound_is_accepted():
    (listing,) = graph_channel_listings(
        {CHANNELS_KEY: [{"team": TEAM, "channel": CHANNEL_A, "project": "alpha", "label": "x" * MAX_LABEL_LENGTH}]},
        registered=lambda p: True,
    )
    assert listing.label == "x" * MAX_LABEL_LENGTH


def test_the_calendar_and_a_channel_share_one_time_budget():
    """The calendar request spends the budget; the channel then meets
    `FetchBudgetSpent` before its first page."""
    clock = [NOW]

    def _spend(request: GraphRequest) -> None:
        if "/me/calendarView" in request.url:
            clock[0] = NOW + timedelta(minutes=10)

    routes = Routes(channels={CHANNEL_A: [_page(_message())]}, on_request=_spend)
    result = _connector(routes, clock=lambda: clock[0]).harvest(Cursor())
    assert routes.asked(CHANNEL_A) == []
    assert result.failure is not None and result.failure.retryable is True
    assert "time budget" in result.failure.reason


def test_a_failed_calendar_keeps_its_stored_position_while_a_channel_advances():
    calendar_position = NOW - timedelta(days=2)
    routes = Routes(calendar=[_status(503)], channels={CHANNEL_A: [_page(_message())]})
    result = _connector(routes).harvest(_cursor(calendar=calendar_position))
    positions = _positions(result.cursor)
    assert positions["calendar"] == calendar_position.isoformat()
    assert CHANNEL_A in positions["channels"]


def test_a_calendar_only_run_keeps_the_window_it_had_before_this_slice():
    """Before 33d the result's window was the calendar fetch's own: from its
    first page's reading to its walk's end. With no channel listed it still is."""
    calendar_clock = iter(NOW + timedelta(seconds=step) for step in range(100))
    routes = Routes()
    connector = _connector(routes)
    connector.channels = ()
    connector.calendar.now = lambda: next(calendar_clock)
    result = connector.harvest(Cursor())
    # Readings: 0 the fetch's start, 1 the first page, 2 the walk's end.
    assert result.outcome is HarvestOutcome.EMPTY
    assert result.coverage is not None
    assert result.coverage.start == NOW + timedelta(seconds=1) == result.answered_at
    assert result.coverage.end == NOW + timedelta(seconds=2)


def test_a_channel_whose_only_message_is_refused_claims_no_window():
    result, _ = _harvest(_page(_message(body=None)))
    assert result.outcome is HarvestOutcome.HARVESTED
    assert result.coverage is None


def test_the_built_connector_reaches_back_as_far_as_the_row_says(tmp_path):
    _enrol(tmp_path, **{CHANNELS_KEY: [{"team": TEAM, "channel": CHANNEL_A, "project": "alpha"}]})
    connector = build(tmp_path, "alpha", now=lambda: NOW).connectors[INSTANCE]
    assert connector.messages.reach_back == REACH_BACK


def test_an_unreachable_token_endpoint_on_one_channel_is_retryable_and_the_next_is_read():
    calls = {"n": 0}

    def _token(*, force_refresh: bool = False) -> str:
        calls["n"] += 1
        if calls["n"] == 2:  # the first channel's page
            raise GraphUnreachable("the token endpoint did not answer")
        return "token-1"

    routes = Routes(channels={CHANNEL_A: [], CHANNEL_B: [_page(_message())]})
    connector = _connector(routes, _listing(CHANNEL_A), _listing(CHANNEL_B, label="Release room"))
    connector.calendar.client.auth.access_token = _token  # type: ignore[method-assign]
    result = connector.harvest(Cursor())
    assert result.failure is not None and result.failure.retryable is True
    assert "Build room" in result.failure.reason
    assert list(_by_id(result)) == ["1700000000500"]


@pytest.mark.parametrize(
    "token",
    [
        b'{"channels": []}',
        b'{"calendar": 5}',
        b'{"calendar": "2026-10-09T10:00:00"}',
        b'{"channels": {"19:x@thread.tacv2": "2026-10-09T10:00:00"}}',
        b'{"channels": {"19:x@thread.tacv2": 7}}',
        b"[1]",
        b"\xff",
    ],
)
def test_a_malformed_cursor_is_refused_and_nothing_is_fetched(token):
    routes = Routes(channels={CHANNEL_A: [_page()]})
    result = _connector(routes).harvest(Cursor(token))
    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None and result.failure.retryable is False
    assert result.cursor == Cursor(token)
    assert routes.requests == []


def test_a_cursor_with_null_channels_reads_as_no_channel_positions():
    result, _ = _harvest(_page(_message()), since=Cursor(b'{"calendar": null, "channels": null}'))
    assert result.failure is None
    assert list(_by_id(result)) == ["1700000000500"]


# ── The dashboard's live read is calendar-only (Andrei, 2026-10-12) ──────────


class _NoKey:
    """A keychain with nothing in it; a render must never reach for a key."""

    def get(self, name: str) -> bytes:
        from pm_ai.ports import KeyNotFound

        raise KeyNotFound(f"nothing is stored under {name!r}")

    def set(self, name: str, secret: bytes) -> None:
        raise AssertionError("a dashboard render must never enrol a key")

    def delete(self, name: str) -> None:
        raise AssertionError("a dashboard render must never delete a key")


def _calendar_event(event_id: str, subject: str, start: datetime) -> dict:
    def _zoned(moment: datetime) -> dict:
        return {"dateTime": moment.replace(tzinfo=None).isoformat() + ".0000000", "timeZone": "UTC"}

    return {
        "id": event_id,
        "subject": subject,
        "start": _zoned(start),
        "end": _zoned(start + timedelta(minutes=30)),
        "isAllDay": False,
        "isCancelled": False,
        "type": "singleInstance",
        "categories": [],
        "organizer": {"emailAddress": {"name": "A", "address": "a@example.invalid"}},
        "attendees": [],
        "responseStatus": {"response": "accepted"},
    }


TODAY = (
    _calendar_event("AAMk-done", "Morning review", NOW - timedelta(hours=3)),
    _calendar_event("AAMk-next", "Release sync", NOW + timedelta(hours=2)),
)


def _dashboard(tmp_path: Path, connector: GraphConnector) -> str:
    from pm_ai.app.pipelines import DASHBOARD_ARTIFACT, run_dashboard
    from pm_ai.core.config import Config

    daemon = build(
        tmp_path,
        "alpha",
        now=lambda: NOW,
        keychain=_NoKey(),
        config=Config(display_timezone="UTC"),
    )
    daemon.connectors = {INSTANCE: connector}
    run_dashboard(daemon, now=NOW)
    raw = daemon.storage.read_artifact(scope=PERSONAL, artifact=DASHBOARD_ARTIFACT)
    assert raw is not None
    return raw.decode("utf-8")


def test_the_graph_connector_offers_the_calendar_read_port():
    from pm_ai.ports import CalendarReadPort, ConnectorPort

    connector = _connector(Routes())
    assert isinstance(connector, CalendarReadPort)
    assert isinstance(connector, ConnectorPort)


def test_a_dashboard_render_with_listed_channels_sends_no_channel_request(tmp_path):
    routes = Routes(calendar=[_page(*TODAY)], channels={CHANNEL_A: [], CHANNEL_B: []})
    _dashboard(tmp_path, _connector(routes, _listing(CHANNEL_A), _listing(CHANNEL_B)))
    assert [r for r in routes.requests if "/teams/" in r.url] == []
    assert len([r for r in routes.requests if "/me/calendarView" in r.url]) == 1


def test_a_channel_that_would_fail_does_not_mark_the_calendar_unread(tmp_path):
    routes = Routes(calendar=[_page(*TODAY)], channels={CHANNEL_A: [_status(403)]})
    text = _dashboard(tmp_path, _connector(routes))
    assert "Release sync" in text
    assert "Build room" not in text and "not a member" not in text
    assert INSTANCE not in text.split("## Time-Critical Activities", 1)[1].split("\n## ", 1)[0]


def test_todays_meetings_render_exactly_as_a_calendar_only_connector_renders_them(tmp_path):
    """The render with two listed channels is byte-identical to one with none —
    the shape every render had before this slice."""
    listed = _connector(
        Routes(calendar=[_page(*TODAY)], channels={CHANNEL_A: [_status(403)]}),
        _listing(CHANNEL_A),
        _listing(CHANNEL_B),
    )
    alone = _connector(Routes(calendar=[_page(*TODAY)]))
    alone.channels = ()
    with_channels = _dashboard(tmp_path / "a", listed)
    without = _dashboard(tmp_path / "b", alone)
    assert with_channels == without
    assert "Morning review" in without and "Release sync" in without


def test_read_calendar_reads_the_calendar_exactly_as_harvest_does():
    """Same calendar answer, no channel attempted or named."""
    listed = _connector(Routes(calendar=[_page(*TODAY)], channels={CHANNEL_A: []}))
    alone = _connector(Routes(calendar=[_page(*TODAY)]))
    alone.channels = ()
    read = listed.read_calendar(Cursor())
    harvested = alone.harvest(Cursor())
    assert read == harvested


def test_the_scheduled_harvest_still_walks_every_listed_channel(tmp_path):
    routes = Routes(channels={CHANNEL_A: [_page(_message())], CHANNEL_B: [_page()]})
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    daemon.connectors[INSTANCE] = _connector(routes, _listing(CHANNEL_A), _listing(CHANNEL_B))
    run_harvest(daemon, INSTANCE)
    assert routes.asked(CHANNEL_A) and routes.asked(CHANNEL_B)
