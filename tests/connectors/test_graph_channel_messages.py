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
    THREAD_HORIZON,
    GraphChannelFetch,
    plain_text,
)
from pm_ai.connectors.graph.client import GRAPH_BASE, GraphClient, GraphRequest, GraphResponse
from pm_ai.core.ledger import parse_segment
from pm_ai.domain.events import MessagePayload, ObservedEventType, Provenance
from pm_ai.domain.harvest import Cursor, HarvestFailure, HarvestOutcome
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
    instance = INSTANCE
    """`TokenSource` declares it: a request refused twice names its sign-in command."""

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
    windows: WindowPolicy | None = None,
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
            windows=windows or WindowPolicy(width=WIDTH, first_run_reach_back=REACH_BACK),
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


# The stop rule reads first-post creation times against a horizon 90 days
# before the position (story 33h): Microsoft lists threads by when their first
# post was created, so a fresh reply can sit on any page.

NEXT_3 = f"{GRAPH_BASE}/teams/{TEAM}/channels/{CHANNEL_A}/messages?$skiptoken=page3"
NEXT_4 = f"{GRAPH_BASE}/teams/{TEAM}/channels/{CHANNEL_A}/messages?$skiptoken=page4"
POSITION = NOW - timedelta(days=1)
HORIZON = POSITION - THREAD_HORIZON


def _thread_with_reply(root_id: str, started: datetime, reply_id: str, replied: datetime) -> dict:
    root = _message(root_id, at=started)
    reply = _message(reply_id, at=replied)
    reply["replyToId"] = root_id
    root["replies"] = [reply]
    return root


def test_the_horizon_is_ninety_days():
    assert THREAD_HORIZON == timedelta(days=90)


def test_an_old_thread_with_a_fresh_reply_on_a_later_page_is_read():
    """Page one is wholly older than the position but inside the horizon."""
    older = _message("1600000000001", at=NOW - timedelta(days=10))
    started = _thread_with_reply(
        "1600000000002", NOW - timedelta(days=59), "1700000000777", NOW - timedelta(minutes=5)
    )
    result, routes = _harvest(
        _page(older, next_link=NEXT),
        _page(started),
        since=_cursor(**{CHANNEL_A: POSITION}),
    )
    assert len(routes.asked(CHANNEL_A)) == 2
    assert list(_by_id(result)) == ["1700000000777"], "the reply, not the root"
    assert result.failure is None


def test_an_old_thread_last_on_a_recent_page_emits_its_reply_and_the_walk_stops():
    recent = _message("1700000000778", at=NOW - timedelta(hours=1))
    year_old = _thread_with_reply(
        "1500000000001", NOW - timedelta(days=365), "1700000000779", NOW - timedelta(minutes=5)
    )
    result, routes = _harvest(
        _page(recent, year_old, next_link=NEXT),
        _page(_message("1400000000001", at=NOW - timedelta(days=400))),
        since=_cursor(**{CHANNEL_A: POSITION}),
    )
    assert sorted(_by_id(result)) == ["1700000000778", "1700000000779"]
    assert len(routes.asked(CHANNEL_A)) == 1


def test_a_thread_started_before_the_horizon_on_a_later_page_is_not_read():
    """The admitted limit: no request, no refusal."""
    crossing = _message("1500000000002", at=POSITION - timedelta(days=120))
    beyond = _thread_with_reply(
        "1500000000003", POSITION - timedelta(days=121), "1700000000780", NOW - timedelta(minutes=5)
    )
    result, routes = _harvest(
        _page(crossing, next_link=NEXT),
        _page(beyond),
        since=_cursor(**{CHANNEL_A: POSITION}),
    )
    assert len(routes.asked(CHANNEL_A)) == 1
    assert _messages(result) == []
    assert result.refusals == ()
    assert result.failure is None


def test_a_page_crossing_the_horizon_is_the_only_request():
    result, routes = _harvest(
        _page(
            _message("1700000000781", at=NOW - timedelta(hours=1)),
            _message("1500000000004", at=HORIZON - timedelta(seconds=1)),
            next_link=NEXT,
        ),
        since=_cursor(**{CHANNEL_A: POSITION}),
    )
    assert len(routes.asked(CHANNEL_A)) == 1
    assert list(_by_id(result)) == ["1700000000781"]
    assert result.failure is None


def test_a_first_post_exactly_at_the_horizon_is_not_older_and_the_walk_continues():
    result, routes = _harvest(
        _page(_message("1500000000005", at=HORIZON), next_link=NEXT),
        _page(),
        since=_cursor(**{CHANNEL_A: POSITION}),
    )
    assert len(routes.asked(CHANNEL_A)) == 2
    assert result.failure is None


def test_first_posts_out_of_order_within_a_page_do_not_stop_the_walk():
    """An older first post before a newer one: the page's oldest is past the
    horizon, but the order cannot be trusted, and the newer first post is
    inside it, so the page does not stop the walk."""
    result, routes = _harvest(
        _page(
            _message("1500000000006", at=HORIZON - timedelta(days=1)),
            _message("1600000000007", at=NOW - timedelta(days=10)),
            next_link=NEXT,
        ),
        _page(),
        since=_cursor(**{CHANNEL_A: POSITION}),
    )
    assert len(routes.asked(CHANNEL_A)) == 2
    assert result.failure is None


def test_first_posts_out_of_order_across_pages_stop_the_walk_only_on_a_wholly_older_page():
    """Page two's first post is newer than page one's oldest: from then on the
    walk stops only on a page whose every first post is before the horizon."""
    result, routes = _harvest(
        _page(_message("1600000000008", at=NOW - timedelta(days=10)), next_link=NEXT),
        _page(
            _message("1600000000009", at=NOW - timedelta(days=5)),
            _message("1500000000010", at=HORIZON - timedelta(days=30)),
            next_link=NEXT_3,
        ),
        _page(_message("1500000000011", at=HORIZON - timedelta(days=40)), next_link=NEXT_4),
        _page(_message("1500000000012", at=HORIZON - timedelta(days=50))),
        since=_cursor(**{CHANNEL_A: POSITION}),
    )
    assert len(routes.asked(CHANNEL_A)) == 3
    assert result.failure is None


def test_tied_first_posts_are_in_order():
    at = NOW - timedelta(days=10)
    result, routes = _harvest(
        _page(_message("1600000000013", at=at), next_link=NEXT),
        _page(
            _message("1600000000014", at=at),
            _message("1500000000015", at=HORIZON - timedelta(days=1)),
            next_link=NEXT_3,
        ),
        _page(),
        since=_cursor(**{CHANNEL_A: POSITION}),
    )
    assert len(routes.asked(CHANNEL_A)) == 2
    assert result.failure is None


def test_one_readable_first_post_older_than_the_horizon_stops_the_walk():
    undatable = _message("1600000000016", createdDateTime="not a time")
    result, routes = _harvest(
        _page(
            undatable,
            _message("1500000000017", at=HORIZON - timedelta(days=1)),
            "not a thread",
            next_link=NEXT,
        ),
        since=_cursor(**{CHANNEL_A: POSITION}),
    )
    assert len(routes.asked(CHANNEL_A)) == 1
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
    assert f"messages and replies created at or after {position.isoformat()}" in result.failure.reason
    assert "horizon" not in result.failure.reason
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


def test_a_cut_after_the_position_names_the_horizon_and_is_not_retryable():
    """Cut after a first post older than the position: only replies in threads
    started between the horizon and the oldest first post walked are lost."""
    position = NOW - timedelta(days=1)
    newest = NOW - timedelta(hours=1)
    result, _ = _harvest(
        _page(_message("1700000001008", at=newest), _edited_old_root(), next_link=NEXT),
        since=_cursor(**{CHANNEL_A: position}),
        page_cap=1,
    )
    assert datetime.fromisoformat(_positions(result.cursor)["channels"][CHANNEL_A]) == newest
    failure = result.failure
    assert failure is not None and failure.retryable is False
    assert failure.walk_cut_short is True, "the cut after the position is marked (`23h`)"
    assert failure.retry_after is None
    reason = failure.reason
    assert "Build room" in reason
    assert f"horizon {(position - THREAD_HORIZON).isoformat()}" in reason
    assert f"replies created at or after {position.isoformat()}" in reason, (
        "the loss never reaches below the stored position"
    )
    assert f"up to {(NOW - timedelta(days=3)).isoformat()}, the oldest first post walked" in reason
    assert result.coverage is None


def test_a_cut_by_the_budget_after_the_position_is_not_retryable_either():
    clock = [NOW]

    def _advance(request: GraphRequest) -> None:
        if "/channels/" in request.url:
            clock[0] = NOW + timedelta(hours=1)

    newest = NOW - timedelta(hours=1)
    routes = Routes(
        channels={
            CHANNEL_A: [_page(_message("1700000001009", at=newest), _edited_old_root(), next_link=NEXT)]
        },
        on_request=_advance,
    )
    result = _connector(routes, clock=lambda: clock[0]).harvest(
        _cursor(**{CHANNEL_A: NOW - timedelta(days=1)})
    )
    assert datetime.fromisoformat(_positions(result.cursor)["channels"][CHANNEL_A]) == newest
    failure = result.failure
    assert failure is not None and failure.retryable is False
    assert failure.walk_cut_short is True
    assert failure.retry_after is None
    assert "time budget" in failure.reason
    assert "Build room" in failure.reason
    assert "horizon" in failure.reason
    assert f"replies created at or after {(NOW - timedelta(days=1)).isoformat()}" in failure.reason
    assert result.coverage is None


def test_a_cut_whose_oldest_first_post_is_exactly_at_the_position_is_the_retryable_loss():
    """A first post created at the position is not older than it."""
    position = NOW - timedelta(days=1)
    result, _ = _harvest(
        _page(
            _message("1700000001010", at=NOW - timedelta(hours=1)),
            _message("1700000001011", at=position),
            next_link=NEXT,
        ),
        since=_cursor(**{CHANNEL_A: position}),
        page_cap=1,
    )
    failure = result.failure
    assert failure is not None and failure.retryable is True
    assert failure.walk_cut_short is False, "only the non-retryable cut is marked"
    assert "horizon" not in failure.reason
    assert f"messages and replies created at or after {position.isoformat()}" in failure.reason


# ── The cut marker through the run's one failure (story 23h) ─────────────────

_CUT = HarvestFailure("cut after the position", retryable=False, walk_cut_short=True)
_THROTTLED = HarvestFailure("calendar throttled", retryable=True, retry_after=timedelta(minutes=2))
_EXPIRED = HarvestFailure("sign-in expired", retryable=False)


@pytest.mark.parametrize(
    ("parts", "cut"),
    [
        pytest.param((_CUT,), True, id="cut-alone"),
        pytest.param((_CUT, _THROTTLED), True, id="cut-plus-throttled-calendar"),
        pytest.param((_THROTTLED, _CUT, _CUT), True, id="two-cuts-and-a-throttle"),
        pytest.param((_CUT, _EXPIRED), False, id="cut-plus-expired-sign-in"),
        pytest.param((_EXPIRED,), False, id="expired-alone"),
        pytest.param((_THROTTLED, _THROTTLED), False, id="retryable-join"),
    ],
)
def test_a_joined_failure_is_a_cut_only_when_every_non_retryable_part_is(parts, cut):
    from pm_ai.connectors.graph import _one_failure

    joined = _one_failure(parts)
    assert joined is not None
    assert joined.walk_cut_short is cut
    assert joined.retryable is all(part.retryable for part in parts)


def test_a_retryable_failure_cannot_carry_the_cut_marker():
    with pytest.raises(ValueError, match="walk_cut_short"):
        HarvestFailure("cut", retryable=True, walk_cut_short=True)


def test_naming_the_skipped_channels_keeps_the_cut_marker():
    from pm_ai.connectors.graph import _with_skipped

    skipped = ChannelListing(team="t", channel="c", project="alpha", label="Later room")
    named = _with_skipped(_CUT, (skipped,))
    assert named is not None and named is not _CUT
    assert "Later room" in named.reason
    assert named.walk_cut_short is True


def test_a_cut_after_the_first_posts_went_out_of_order_states_the_unbounded_loss():
    """Out of order, a later page may hold threads started after the position,
    so the cut names messages and replies, not the horizon's replies alone."""
    position = NOW - timedelta(days=1)
    result, _ = _harvest(
        _page(
            _message("1700000001012", at=NOW - timedelta(days=3)),
            _message("1700000001013", at=NOW - timedelta(hours=1)),
            next_link=NEXT,
        ),
        since=_cursor(**{CHANNEL_A: position}),
        page_cap=1,
    )
    failure = result.failure
    assert failure is not None and failure.retryable is True
    assert "out of order" in failure.reason
    assert "horizon" not in failure.reason
    assert f"messages and replies created at or after {position.isoformat()}" in failure.reason
    assert result.coverage is None


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


def _dashboard(
    tmp_path: Path,
    connector: GraphConnector,
    *,
    now: datetime = NOW,
    zone: str = "UTC",
    others: dict[str, GraphConnector] | None = None,
) -> str:
    from pm_ai.app.pipelines import DASHBOARD_ARTIFACT, run_dashboard
    from pm_ai.core.config import Config

    daemon = build(
        tmp_path,
        "alpha",
        now=lambda: now,
        keychain=_NoKey(),
        config=Config(display_timezone=zone),
    )
    daemon.connectors = {INSTANCE: connector, **(others or {})}
    run_dashboard(daemon, now=now)
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


def test_read_calendar_reads_the_named_range_and_the_harvest_keeps_its_window():
    """`23e`: the calendar-only read asks for exactly the range it is handed, no
    channel attempted; the harvest still asks for `WindowPolicy.window`."""
    day_start = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    listed_routes = Routes(calendar=[_page(*TODAY)], channels={CHANNEL_A: []})
    read = _connector(listed_routes).read_calendar(day_start, day_end)
    (asked,) = listed_routes.requests
    assert _range_of(asked) == (day_start, day_end)
    assert [meeting.title for meeting in (*read.records, *read.live)] == [
        "Morning review",
        "Release sync",
    ]

    harvest_routes = Routes(calendar=[_page(*TODAY)], channels={CHANNEL_A: [_page()]})
    _connector(harvest_routes).harvest(Cursor())
    calendar_asks = [r for r in harvest_routes.requests if "/me/calendarView" in r.url]
    (harvested,) = calendar_asks
    assert _range_of(harvested) == (NOW - REACH_BACK, NOW + WIDTH)


def test_the_scheduled_harvest_still_walks_every_listed_channel(tmp_path):
    routes = Routes(channels={CHANNEL_A: [_page(_message())], CHANNEL_B: [_page()]})
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    daemon.connectors[INSTANCE] = _connector(routes, _listing(CHANNEL_A), _listing(CHANNEL_B))
    run_harvest(daemon, INSTANCE)
    assert routes.asked(CHANNEL_A) and routes.asked(CHANNEL_B)


@pytest.mark.parametrize(("rotated", "retryable"), [(False, True), (True, False)])
def test_a_busy_credentials_file_on_a_channel_is_retryable_only_when_nothing_was_rotated(
    rotated, retryable
):
    """Story 9a: waiting clears a busy file that lost nothing; a lost rotation needs a sign-in."""
    from pm_ai.connectors.graph.auth import CredentialStoreBusy

    calls = {"n": 0}

    def _token(*, force_refresh: bool = False) -> str:
        calls["n"] += 1
        if calls["n"] == 2:  # the channel's page; the calendar was the first
            raise CredentialStoreBusy("the credentials file was busy.", rotated=rotated)
        return "token-1"

    routes = Routes(channels={CHANNEL_A: []})
    connector = _connector(routes, _listing(CHANNEL_A))
    connector.calendar.client.auth.access_token = _token  # type: ignore[method-assign]
    result = connector.harvest(Cursor())
    assert result.failure is not None and result.failure.retryable is retryable
    assert "busy" in result.failure.reason


# ── Story 23e — the dashboard reads the whole local day ──────────────────────
#
# Every row is the real path: a scripted Graph answer, `GraphConnector`'s
# calendar-only read, and the dashboard's selection and render. The range the
# render asked for is asserted on the recorded request URL.

BOGOTA = "America/Bogota"  # UTC−5 all year, so no daylight saving in the way
SEVEN_BOGOTA = NOW  # 2026-10-09 12:00 UTC is 07:00 in Bogotá
BOGOTA_DAY = (
    datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc),
    datetime(2026, 10, 10, 5, 0, tzinfo=timezone.utc),
)


def _range_of(request: GraphRequest) -> tuple[datetime, datetime]:
    """The `startDateTime` and `endDateTime` a recorded calendarView request carried."""
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(request.url).query)
    (start,) = query["startDateTime"]
    (end,) = query["endDateTime"]
    return datetime.fromisoformat(start), datetime.fromisoformat(end)


def _calendar_asks(routes: Routes) -> list[GraphRequest]:
    return [r for r in routes.requests if "/me/calendarView" in r.url]


def _bogota(hour: int, minute: int = 0, *, days: int = 0) -> datetime:
    """A wall-clock time in Bogotá on 2026-10-09 (plus `days`), as UTC."""
    return datetime(2026, 10, 9, hour, minute, tzinfo=timezone.utc) + timedelta(hours=5, days=days)


def _spanning(event_id: str, subject: str, start: datetime, end: datetime, **fields) -> dict:
    event = _calendar_event(event_id, subject, start)
    event["end"] = {"dateTime": end.replace(tzinfo=None).isoformat() + ".0000000", "timeZone": "UTC"}
    event.update(fields)
    return event


def _all_day(event_id: str, subject: str, on: datetime) -> dict:
    """An all-day entry as midnight UTC to the next midnight UTC on its own date."""
    midnight = on.replace(hour=0, minute=0, second=0, microsecond=0)
    return _spanning(event_id, subject, midnight, midnight + timedelta(days=1), isAllDay=True)


def _schedule(text: str) -> str:
    return text.split("## Time-Critical Activities", 1)[1].split("\n## ", 1)[0]


def _calendar_only(routes: Routes, **options) -> GraphConnector:
    connector = _connector(routes, **options)
    connector.channels = ()
    return connector


def test_a_0700_render_asks_for_the_whole_local_day_and_lists_the_afternoon(tmp_path):
    """Matrix: 07:00 render, UTC−5 — all four listed; the read was handed the day."""
    times = [(9, 0), (11, 30), (14, 0), (16, 30)]
    routes = Routes(
        calendar=[
            _page(*(_calendar_event(f"AAMk-{h}{m}", f"Meeting {h:02d}{m:02d}", _bogota(h, m)) for h, m in times))
        ]
    )
    text = _dashboard(tmp_path, _calendar_only(routes), now=SEVEN_BOGOTA, zone=BOGOTA)
    (asked,) = _calendar_asks(routes)
    assert _range_of(asked) == BOGOTA_DAY
    for h, m in times:
        assert f"Meeting {h:02d}{m:02d}" in _schedule(text)


def test_the_render_and_the_scheduled_harvest_each_ask_for_their_own_range(tmp_path):
    """Acceptance: one daemon, one Graph connector. The 07:00 render covered the
    display day and the scheduled harvest after it the range `WindowPolicy.window`
    computes for a first run — so the render left no cursor or coverage behind."""
    from pm_ai.app.pipelines import run_dashboard
    from pm_ai.core.config import Config

    routes = Routes(
        calendar=[_page(*TODAY), _page(*TODAY)], channels={CHANNEL_A: [_page()]}
    )
    daemon = build(
        tmp_path, "alpha", now=lambda: SEVEN_BOGOTA, config=Config(display_timezone=BOGOTA)
    )
    daemon.connectors = {INSTANCE: _connector(routes)}

    run_dashboard(daemon, now=SEVEN_BOGOTA)
    assert daemon.storage.coverage_windows(INSTANCE) == []
    run_harvest(daemon, INSTANCE)

    render_ask, harvest_ask = _calendar_asks(routes)
    assert _range_of(render_ask) == BOGOTA_DAY
    window = WindowPolicy(width=WIDTH, first_run_reach_back=REACH_BACK).window(now=SEVEN_BOGOTA)
    assert _range_of(harvest_ask) == (window.start, window.end)
    assert window.start == SEVEN_BOGOTA - REACH_BACK and window.end == SEVEN_BOGOTA + WIDTH
    # The render walked no channel; the harvest walked the listed one.
    assert len(routes.asked(CHANNEL_A)) == 1


def test_a_late_evening_render_lists_the_morning_and_nothing_from_tomorrow(tmp_path):
    """Matrix: 23:30 local, meetings at 08:00 and 13:00 ended, reach-back 4 h."""
    late = _bogota(23, 30)
    routes = Routes(
        calendar=[
            _page(
                _calendar_event("AAMk-8", "Breakfast sync", _bogota(8)),
                _calendar_event("AAMk-13", "Lunch review", _bogota(13)),
                _calendar_event("AAMk-tmrw", "Tomorrow standup", _bogota(8, days=1)),
            )
        ]
    )
    short = WindowPolicy(width=timedelta(minutes=240), first_run_reach_back=timedelta(hours=4))
    connector = _calendar_only(routes, clock=lambda: late, windows=short)
    text = _dashboard(tmp_path, connector, now=late, zone=BOGOTA)
    (asked,) = _calendar_asks(routes)
    assert _range_of(asked) == BOGOTA_DAY
    schedule = _schedule(text)
    # The source answered with tomorrow's 08:00 too, which has not ended at
    # 23:30: had it been selected, the section could not collapse to "all
    # ended", and the count would be three.
    assert "All 2 of today's meetings have ended" in schedule
    assert "The last finished at 2026-10-09 13:30" in schedule


@pytest.mark.parametrize(
    ("subject", "start", "end"),
    [
        ("Into today", _bogota(23, days=-1), _bogota(1)),
        ("Out of today", _bogota(23, 30), _bogota(0, 30, days=1)),
        ("Just before midnight", _bogota(23, 58), _bogota(23, 58) + timedelta(minutes=30)),
    ],
    ids=["crossing-into-today", "crossing-out-of-today", "starting-at-2358"],
)
def test_a_meeting_touching_the_day_at_either_midnight_is_listed(tmp_path, subject, start, end):
    """Matrix: crossing midnight into and out of today, and starting at 23:58."""
    routes = Routes(
        calendar=[
            _page(
                _spanning("AAMk-edge", subject, start, end),
                # Upcoming, so the section lists lines rather than collapsing.
                _calendar_event("AAMk-noon", "Noon sync", _bogota(12)),
            )
        ]
    )
    text = _dashboard(tmp_path, _calendar_only(routes), now=SEVEN_BOGOTA, zone=BOGOTA)
    assert subject in _schedule(text)


def test_a_meeting_starting_at_2358_is_not_marked_as_starting_after_the_range():
    from pm_ai.connectors.graph.calendar import CalendarWindow

    routes = Routes(calendar=[_page(_calendar_event("AAMk-2358", "Late", _bogota(23, 58)))])
    fetch = _calendar_only(routes).calendar.fetch(window=CalendarWindow(*BOGOTA_DAY))
    (row,) = fetch.rows
    assert row.clock_flag is None
    assert fetch.window == CalendarWindow(*BOGOTA_DAY)


def test_an_all_day_entry_west_of_utc_is_listed_on_its_own_date(tmp_path):
    """Matrix: UTC−5 — its midnight-UTC start is 19:00 yesterday in Bogotá, and
    it is still today's."""
    routes = Routes(
        calendar=[
            _page(
                _all_day("AAMk-allday", "Company holiday", datetime(2026, 10, 9, tzinfo=timezone.utc)),
                _calendar_event("AAMk-noon", "Noon sync", _bogota(12)),
            )
        ]
    )
    text = _dashboard(tmp_path, _calendar_only(routes), now=SEVEN_BOGOTA, zone=BOGOTA)
    (line,) = [line for line in _schedule(text).split("\n") if "Company holiday" in line]
    assert line.startswith("- **all day** Company holiday — ")
    assert "2026-10-08" not in line and "19:00" not in line
    assert "ended" not in line and "0 min" not in line


def test_an_all_day_entry_east_of_utc_is_listed_only_on_its_own_date(tmp_path):
    """Matrix: UTC+9 — yesterday's and today's all-day entries; only today's."""
    tokyo_evening = datetime(2026, 10, 9, 11, 0, tzinfo=timezone.utc)  # 20:00 in Tokyo
    routes = Routes(
        calendar=[
            _page(
                _all_day("AAMk-yday", "Yesterday's holiday", datetime(2026, 10, 8, tzinfo=timezone.utc)),
                _all_day("AAMk-today", "Today's holiday", datetime(2026, 10, 9, tzinfo=timezone.utc)),
                # Upcoming at 23:00 Tokyo, so the section lists lines.
                _calendar_event("AAMk-late", "Late sync", datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)),
            )
        ]
    )
    connector = _calendar_only(routes, clock=lambda: tokyo_evening)
    text = _dashboard(tmp_path, connector, now=tokyo_evening, zone="Asia/Tokyo")
    (asked,) = _calendar_asks(routes)
    assert _range_of(asked) == (
        datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc),
    )
    schedule = _schedule(text)
    assert "Today's holiday" in schedule
    assert "Yesterday's holiday" not in schedule


@pytest.mark.parametrize(("day", "listed"), [(18, True), (19, False)])
def test_a_multi_day_all_day_entry_east_of_utc_ends_before_its_end_date(tmp_path, day, listed):
    """Matrix (`23h`): UTC+9, start Oct 17, end Oct 19 — on Oct 19 the range
    overlaps the entry's midnight-UTC end, so it is returned, and not listed.

    Assumes Graph writes the end as the entry's own midnight under
    `Prefer: outlook.timezone="UTC"` (unmeasured, `deferred-work.md`).
    """
    from datetime import date

    tokyo_evening = datetime(2026, 10, day, 11, 0, tzinfo=timezone.utc)  # 20:00 in Tokyo
    offsite = _spanning(
        "AAMk-offsite",
        "Offsite",
        datetime(2026, 10, 17, tzinfo=timezone.utc),
        datetime(2026, 10, 19, tzinfo=timezone.utc),
        isAllDay=True,
    )
    late = _calendar_event("AAMk-late", "Late sync", tokyo_evening + timedelta(hours=3))
    routes = Routes(calendar=[_page(offsite, late), _page(offsite, late)])
    connector = _calendar_only(routes, clock=lambda: tokyo_evening)

    asked_range = (
        datetime(2026, 10, day - 1, 15, 0, tzinfo=timezone.utc),
        datetime(2026, 10, day, 15, 0, tzinfo=timezone.utc),
    )
    returned = connector.read_calendar(*asked_range)
    (meeting,) = [m for m in (*returned.records, *returned.live) if m.title == "Offsite"]
    assert (meeting.all_day_date, meeting.all_day_end_date) == (
        date(2026, 10, 17),
        date(2026, 10, 19),
    )

    text = _dashboard(tmp_path, connector, now=tokyo_evening, zone="Asia/Tokyo")
    assert _range_of(_calendar_asks(routes)[-1]) == asked_range
    assert ("Offsite" in _schedule(text)) is listed


def test_an_all_day_row_carries_its_own_date_and_nothing_else_does():
    routes = Routes(
        calendar=[
            _page(
                _all_day("AAMk-allday", "Holiday", datetime(2026, 10, 9, tzinfo=timezone.utc)),
                _calendar_event("AAMk-noon", "Noon sync", _bogota(12)),
            )
        ]
    )
    result = _calendar_only(routes).read_calendar(*BOGOTA_DAY)
    by_title = {meeting.title: meeting for meeting in (*result.records, *result.live)}
    assert by_title["Holiday"].all_day_date == datetime(2026, 10, 9).date()
    assert by_title["Noon sync"].all_day_date is None


def test_a_declined_meeting_is_left_out(tmp_path):
    """Matrix: declined, any time today."""
    routes = Routes(
        calendar=[
            _page(
                _calendar_event("AAMk-no", "Declined review", _bogota(15))
                | {"responseStatus": {"response": "declined"}},
                _calendar_event("AAMk-noon", "Noon sync", _bogota(12)),
            )
        ]
    )
    text = _dashboard(tmp_path, _calendar_only(routes), now=SEVEN_BOGOTA, zone=BOGOTA)
    assert "Declined review" not in text
    assert "Noon sync" in _schedule(text)


def test_a_cancelled_meeting_not_yet_ended_is_left_out(tmp_path):
    """Matrix: cancelled, 15:00 today, render at 07:00."""
    routes = Routes(
        calendar=[
            _page(
                _calendar_event("AAMk-x", "Cancelled review", _bogota(15)) | {"isCancelled": True},
                _calendar_event("AAMk-noon", "Noon sync", _bogota(12)),
            )
        ]
    )
    text = _dashboard(tmp_path, _calendar_only(routes), now=SEVEN_BOGOTA, zone=BOGOTA)
    assert "Cancelled review" not in text
    assert "Noon sync" in _schedule(text)


def test_an_ended_cancelled_meeting_is_listed_as_ended_and_marked_cancelled(tmp_path):
    """Matrix: cancelled, 08:00 today, render at 10:00 — its facts include `cancelled`."""
    ten = _bogota(10)
    routes = Routes(
        calendar=[
            _page(
                _calendar_event("AAMk-x", "Cancelled review", _bogota(8)) | {"isCancelled": True},
                _calendar_event("AAMk-noon", "Noon sync", _bogota(12)),
            )
        ]
    )
    connector = _calendar_only(routes, clock=lambda: ten)
    lines = _schedule(_dashboard(tmp_path, connector, now=ten, zone=BOGOTA)).split("\n")
    (cancelled,) = [line for line in lines if "Cancelled review" in line]
    assert "ended" in cancelled and "cancelled" in cancelled
    (noon,) = [line for line in lines if "Noon sync" in line]
    assert "cancelled" not in noon


def test_two_tenants_are_each_asked_for_the_same_local_day(tmp_path):
    """Matrix: two Graph rows — each read for the day; merged and deduplicated."""
    shared = _calendar_event("AAMk-a", "Weekly sync", _bogota(12)) | {"iCalUId": "uid-weekly"}
    work = Routes(calendar=[_page(shared)])
    other = Routes(
        calendar=[
            _page(
                shared | {"id": "AAMk-b"},
                _calendar_event("AAMk-c", "Vendor call", _bogota(15)),
            )
        ]
    )
    text = _dashboard(
        tmp_path,
        _calendar_only(work),
        now=SEVEN_BOGOTA,
        zone=BOGOTA,
        others={"graph:other": _calendar_only(other)},
    )
    assert [_range_of(r) for r in _calendar_asks(work)] == [BOGOTA_DAY]
    assert [_range_of(r) for r in _calendar_asks(other)] == [BOGOTA_DAY]
    schedule = _schedule(text)
    assert schedule.count("Weekly sync") == 1
    assert "Vendor call" in schedule


def test_two_tenants_one_failing_is_a_partial_day(tmp_path):
    work = Routes(calendar=[_page(_calendar_event("AAMk-a", "Weekly sync", _bogota(12)))])
    other = Routes(calendar=[_status(403)])
    text = _dashboard(
        tmp_path,
        _calendar_only(work),
        now=SEVEN_BOGOTA,
        zone=BOGOTA,
        others={"graph:other": _calendar_only(other)},
    )
    schedule = _schedule(text)
    assert "Weekly sync" in schedule
    assert "The calendar could not be read" not in schedule
    assert "graph:other" in schedule
    assert [_range_of(r) for r in _calendar_asks(other)] == [BOGOTA_DAY]


def test_a_fetch_handed_both_a_cursor_and_a_range_refuses_naming_both():
    """Matrix: both range and cursor — a bug, refused rather than mixed."""
    from pm_ai.connectors.graph.calendar import CalendarWindow

    routes = Routes()
    fetch = _calendar_only(routes).calendar
    with pytest.raises(ValueError, match=r"since=.*window="):
        fetch.fetch(since=NOW - timedelta(hours=1), window=CalendarWindow(*BOGOTA_DAY))
    assert routes.requests == []


def test_the_harvest_after_the_fix_asks_for_the_same_range_as_before():
    """Matrix: the scheduled harvest — first run and resumed both unchanged."""
    first = Routes(calendar=[_page()], channels={CHANNEL_A: [_page()]})
    _connector(first).harvest(Cursor())
    (asked,) = _calendar_asks(first)
    assert _range_of(asked) == (NOW - REACH_BACK, NOW + WIDTH)

    resumed = Routes(calendar=[_page()], channels={CHANNEL_A: [_page()]})
    position = NOW - timedelta(days=2)
    _connector(resumed).harvest(_cursor(calendar=position))
    (asked,) = _calendar_asks(resumed)
    assert _range_of(asked) == (position, NOW + WIDTH)
