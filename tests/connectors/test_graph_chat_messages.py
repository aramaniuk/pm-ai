"""Story 33f — listed Teams chats, one test per matrix row.

Every row starts from `fixtures/graph/chat_messages.json` handed to a scripted
`GraphClient.transport`: the provider's answer, the walk in `chats.py`, and the
event `GraphConnector` builds with the channel slice's mapping. No test opens a
socket — a URL no route answers fails the test.

**The fixture is not a tenant's answer.** The story asks for a redacted page
from a spike re-run against one listed chat; that re-run needs the PM's sign-in
and has not happened, so the page is written in the shape Microsoft publishes
and says so in its own `_provenance`.

The clock is frozen at `NOW` unless a test moves it.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

import pytest

from pm_ai.app.pipelines import run_harvest
from pm_ai.app.wiring import build
from pm_ai.connectors.graph import (
    CHANNELS_KEY,
    CHATS_KEY,
    CLIENT_ID_KEY,
    MAX_CHAT_ID_LENGTH,
    MAX_LABEL_LENGTH,
    REACH_BACK_KEY,
    WIDTH_KEY,
    CategoryScopes,
    ChannelListing,
    ChatListing,
    ChatListRefused,
    GraphConnector,
    UnknownProject,
    graph_chat_listings,
)
from pm_ai.connectors.graph.auth import GraphUnreachable
from pm_ai.connectors.graph.calendar import GraphCalendarFetch, WindowPolicy
from pm_ai.connectors.graph.channels import GraphChannelFetch
from pm_ai.connectors.graph.chats import FILTER_SLACK, GraphChatFetch
from pm_ai.connectors.graph.client import GRAPH_BASE, GraphClient, GraphRequest, GraphResponse
from pm_ai.core.ledger import parse_segment
from pm_ai.domain.events import MessagePayload, ObservedEventType, Provenance
from pm_ai.domain.harvest import Cursor, HarvestOutcome
from pm_ai.domain.health import Health, Probe
from pm_ai.domain.identity import DataScope, ScopeKind

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
INSTANCE = "graph:work"
CLIENT_ID = "00000000-0000-0000-0000-000000000001"
WIDTH = timedelta(hours=24)
REACH_BACK = timedelta(days=7)

FIXTURE = Path(__file__).parent / "fixtures" / "graph" / "chat_messages.json"
CHAT_A = json.loads(FIXTURE.read_text())["chat"]["id"]
CHAT_B = "19:meeting_abcdef@thread.v2"
TEAM = "00000000-0000-0000-0000-0000000000aa"
CHANNEL = "19:fixture@thread.tacv2"
SENDER = "11111111-1111-1111-1111-111111111111"
JANE = "2222aaaa-2222-2222-2222-222222222222"

APPLICATION = DataScope(ScopeKind.APPLICATION)
PERSONAL = DataScope(ScopeKind.PERSONAL)
ALPHA = DataScope(ScopeKind.PROJECT, "alpha")


# ── The provider's answers ───────────────────────────────────────────────────


def _fixture() -> dict:
    return copy.deepcopy(json.loads(FIXTURE.read_text()))


def _rows() -> list[dict]:
    return _fixture()["page"]["value"]


def _about(**overrides) -> GraphResponse:
    chat = _fixture()["chat"]
    chat.update(overrides)
    return GraphResponse(status=200, body=chat, headers={"Content-Type": "application/json"})


def _message(message_id: str = "1760000000500", *, at: datetime = NOW - timedelta(hours=1), **overrides) -> dict:
    row = _rows()[1]
    stamp = at.isoformat().replace("+00:00", "Z")
    row.update(
        id=message_id,
        createdDateTime=stamp,
        lastModifiedDateTime=stamp,
        body={"contentType": "text", "content": f"message {message_id}"},
    )
    row.update(overrides)
    return row


def _page(*rows: dict, next_link: str | None = None) -> GraphResponse:
    body: dict = {"value": list(rows)}
    if next_link is not None:
        body["@odata.nextLink"] = next_link
    return GraphResponse(status=200, body=body, headers={"Content-Type": "application/json"})


def _status(code: int, **headers: str) -> GraphResponse:
    return GraphResponse(status=code, body={"error": {"code": "x"}}, headers=headers)


EMPTY_CALENDAR = _page()


def _next(chat: str) -> str:
    return f"{GRAPH_BASE}/chats/{chat}/messages?$skiptoken=page2"


class Routes:
    """A scripted transport keyed by resource: the calendar, a channel, a chat's
    own object, or a chat's messages."""

    def __init__(
        self,
        *,
        calendar: list[GraphResponse] | None = None,
        channels: dict[str, list[GraphResponse]] | None = None,
        chats: dict[str, list[GraphResponse]] | None = None,
        about: dict[str, list[GraphResponse]] | None = None,
    ) -> None:
        self.calendar = list(calendar if calendar is not None else [EMPTY_CALENDAR])
        self.channels = {key: list(value) for key, value in (channels or {}).items()}
        self.chats = {key: list(value) for key, value in (chats or {}).items()}
        self.about = {key: list(value) for key, value in (about or {}).items()}
        self.requests: list[GraphRequest] = []

    def __call__(self, request: GraphRequest) -> GraphResponse:
        self.requests.append(request)
        path = request.url.split("?", 1)[0]
        if "/me/calendarView" in path:
            script = self.calendar
        elif "/channels/" in path:
            (channel,) = [c for c in self.channels if f"/channels/{c}/messages" in path]
            script = self.channels[channel]
        elif path.endswith("/messages"):
            matched = [c for c in self.chats if path.endswith(f"/chats/{c}/messages")]
            if not matched:
                raise AssertionError(f"no route answers {request!r}")
            script = self.chats[matched[0]]
        else:
            matched = [c for c in self.about if path.endswith(f"/chats/{c}")]
            if not matched:
                raise AssertionError(f"no route answers {request!r}")
            script = self.about[matched[0]]
        if not script:
            raise AssertionError(f"the fixture has no further answer for {request!r}")
        return script.pop(0)

    def asked(self, chat: str) -> list[GraphRequest]:
        return [r for r in self.requests if f"/chats/{chat}/messages" in r.url]

    def asked_about(self, chat: str) -> list[GraphRequest]:
        return [r for r in self.requests if r.url.split("?", 1)[0].endswith(f"/chats/{chat}")]


class Auth:
    instance = INSTANCE

    def access_token(self, *, force_refresh: bool = False) -> str:
        return "token-1"

    def check_health(self) -> Probe:
        return Probe(INSTANCE, Health.OK, "graph answered")


def _chat(chat: str = CHAT_A, *, scope: DataScope = PERSONAL, label: str | None = "Sam") -> ChatListing:
    return ChatListing(chat=chat, scope=scope, label=label)


def _connector(routes: Routes, *chats: ChatListing, channels=(), clock=lambda: NOW) -> GraphConnector:
    identity = Auth()
    client = GraphClient(auth=identity, transport=routes, now=clock)
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
        channels=tuple(channels),
        messages=GraphChannelFetch(client=client, instance=INSTANCE, reach_back=REACH_BACK, now=clock),
        chats=chats or (_chat(),),
        chat_messages=GraphChatFetch(client=client, instance=INSTANCE, reach_back=REACH_BACK, now=clock),
    )


def _cursor(*, calendar: datetime | None = None, channels: dict | None = None, chats: dict | None = None) -> Cursor:
    document: dict = {"calendar": None if calendar is None else calendar.isoformat()}
    document["channels"] = {key: value.isoformat() for key, value in (channels or {}).items()}
    if chats is not None:
        document["chats"] = {key: value.isoformat() for key, value in chats.items()}
    return Cursor(json.dumps(document).encode())


def _positions(cursor: Cursor) -> dict:
    return json.loads(cursor.token.decode())


def _chat_position(cursor: Cursor, chat: str) -> datetime:
    return datetime.fromisoformat(_positions(cursor)["chats"][chat.casefold()])


def _messages(result) -> list:
    return [event for event in result.events if event.type is ObservedEventType.MESSAGE_POSTED]


def _by_id(result) -> dict[str, object]:
    return {event.source_ref.native_id.rsplit("/", 1)[1]: event for event in _messages(result)}


def _log(daemon, scope: DataScope) -> str:
    return "".join(
        daemon.storage.read_event_log_segment(scope=scope, name=name)
        for name in daemon.storage.event_log_segments(scope=scope)
    )


def _filter_instant(request: GraphRequest) -> datetime:
    query = unquote(request.url.split("?", 1)[1])
    stamp = query.split("$filter=lastModifiedDateTime gt ", 1)[1].split("&", 1)[0]
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


# ── The request ──────────────────────────────────────────────────────────────


def test_the_request_is_fifty_newest_modified_first_filtered_after_the_position():
    """Sort and filter on the same field, asserted on the recorded request."""
    position = datetime(2026, 10, 9, 8, 30, 0, 250600, tzinfo=timezone.utc)
    routes = Routes(chats={CHAT_A: [_page(*_rows())]})
    _connector(routes).harvest(_cursor(chats={CHAT_A: position}))

    (request,) = routes.asked(CHAT_A)
    assert request.url == (
        f"{GRAPH_BASE}/chats/{CHAT_A}/messages?$top=50"
        f"&$orderby=lastModifiedDateTime%20desc"
        f"&$filter=lastModifiedDateTime%20gt%202026-10-09T08:30:00.249Z"
    )
    assert "$expand" not in request.url


def test_a_labelled_chat_asks_for_nothing_but_its_messages():
    routes = Routes(chats={CHAT_A: [_page()]})
    _connector(routes).harvest(Cursor())
    assert routes.asked_about(CHAT_A) == []
    assert [r for r in routes.requests if "/chats" in r.url and "/messages" not in r.url] == []


# ── Two listed chats, new messages ───────────────────────────────────────────


def test_two_chats_land_in_their_own_scopes_and_both_positions_advance():
    a_new = NOW - timedelta(hours=1)
    b_new = NOW - timedelta(minutes=30)
    routes = Routes(
        chats={
            CHAT_A: [_page(_message("1760000000501", at=a_new))],
            CHAT_B: [_page(_message("1760000000502", at=b_new))],
        }
    )
    result = _connector(routes, _chat(CHAT_A), _chat(CHAT_B, scope=ALPHA, label="Standup")).harvest(Cursor())

    events = _by_id(result)
    assert events["1760000000501"].scope == PERSONAL
    assert events["1760000000502"].scope == ALPHA
    assert _chat_position(result.cursor, CHAT_A) == a_new
    assert _chat_position(result.cursor, CHAT_B) == b_new
    assert result.outcome is HarvestOutcome.HARVESTED
    assert result.failure is None
    assert result.coverage is not None


def test_through_run_harvest_each_chats_messages_are_in_its_own_scope_only(tmp_path):
    """Acceptance: one chat personal, one `project:alpha`."""
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    routes = Routes(
        chats={
            CHAT_A: [_page(*_rows())],
            CHAT_B: [_page(_message("1760000000777", body={"contentType": "text", "content": "alpha standup notes"}))],
        }
    )
    daemon.connectors[INSTANCE] = _connector(routes, _chat(CHAT_A), _chat(CHAT_B, scope=ALPHA, label="Standup"))

    persisted = run_harvest(daemon, INSTANCE)

    assert persisted.persisted == 3
    personal = [e for e in parse_segment(_log(daemon, PERSONAL)) if e.category is ObservedEventType.MESSAGE_POSTED]
    alpha = [e for e in parse_segment(_log(daemon, ALPHA)) if e.category is ObservedEventType.MESSAGE_POSTED]
    assert len(personal) == 2
    assert len(alpha) == 1
    assert "alpha standup notes" not in _log(daemon, PERSONAL)
    assert "release notes" not in _log(daemon, ALPHA)
    assert "release notes" in _log(daemon, PERSONAL)


def test_the_fixture_maps_through_the_channel_mapping_unchanged():
    result = _connector(Routes(chats={CHAT_A: [_page(*_rows())]})).harvest(Cursor())

    assert sorted(_by_id(result)) == ["1760000000200", "1760000000300"], (
        "the system event is skipped; the old message edited later is cut"
    )
    event = _by_id(result)["1760000000300"]
    payload = event.payload
    assert isinstance(payload, MessagePayload)
    assert payload.excerpt == "Jane Doe, can you send me the release notes by Friday?"
    assert payload.mentions == (f"graph:{JANE}",)
    assert payload.channel == "Sam"
    assert str(event.actor) == f"graph:{SENDER}"
    assert event.authored_by is Provenance.UNKNOWN
    assert getattr(event, "id", None) is None
    assert event.occurred_at == datetime(2026, 10, 9, 10, 0, 0, 123000, tzinfo=timezone.utc)
    assert result.refusals == ()


def test_the_fixture_senders_name_is_stored_beside_the_handle():
    """`23f`: the chat walk reads the name through the one `read_message`."""
    result = _connector(Routes(chats={CHAT_A: [_page(*_rows())]})).harvest(Cursor())
    event = _by_id(result)["1760000000300"]
    assert str(event.actor) == f"graph:{SENDER}"
    assert event.payload.sender_name == "Sam Sender"


BOT = "55555555-5555-5555-5555-555555555555"

DISGUISED_HANDLES = {
    "zero-width-before": "\u200bgraph:1234",
    "word-joiner-before": "\u2060email:a@b.c",
    "bom-before": "\ufeffgraph:x",
    "zero-width-inside": "graph\u200b:x",
    "fullwidth": "\uff27\uff32\uff21\uff30\uff28\uff1a1234",
}
"""As in the channel tests: handle-shaped names behind invisible or
compatibility characters, each refused (`23f`)."""


@pytest.mark.parametrize(
    ("sender", "actor", "name"),
    [
        (
            {"application": {"id": BOT, "displayName": "Release Bot"}, "user": None},
            f"graph_app:{BOT}",
            "Release Bot",
        ),
        ({"user": {"id": None, "displayName": "Dana Okafor"}}, "none:", None),
        ({"user": {"id": SENDER, "displayName": "graph:1234"}}, f"graph:{SENDER}", None),
        ({"user": {"id": SENDER, "displayName": "email:a@b.c"}}, f"graph:{SENDER}", None),
        *(
            ({"user": {"id": SENDER, "displayName": name}}, f"graph:{SENDER}", None)
            for name in DISGUISED_HANDLES.values()
        ),
        (
            {"user": {"id": SENDER, "displayName": "\u200b" * 100 + "Sam Sender"}},
            f"graph:{SENDER}",
            None,
        ),
    ],
    ids=[
        "bot",
        "unknown-sender",
        "graph-shaped-name",
        "email-shaped-name",
        *DISGUISED_HANDLES,
        "invisible-after-the-cut",
    ],
)
def test_a_chat_message_takes_the_name_from_the_actors_own_branch(sender, actor, name):
    """`23f`: bot, unknown sender and handle-shaped names, as for channels."""
    row = _message(**{"from": sender})
    result = _connector(Routes(chats={CHAT_A: [_page(row)]})).harvest(Cursor())
    event = _by_id(result)["1760000000500"]
    assert str(event.actor) == actor
    assert event.payload.sender_name == name


# ── References ───────────────────────────────────────────────────────────────


def test_the_reference_spells_the_scope_as_personal_or_the_project_id():
    routes = Routes(
        chats={
            CHAT_A: [_page(_message("1760000000601"))],
            CHAT_B: [_page(_message("1760000000602"))],
        }
    )
    result = _connector(routes, _chat(CHAT_A), _chat(CHAT_B, scope=ALPHA)).harvest(Cursor())
    references = {str(event.source_ref) for event in _messages(result)}
    assert references == {
        f"graph:personal:message:{CHAT_A}/1760000000601",
        f"graph:alpha:message:{CHAT_B}/1760000000602",
    }


def test_the_same_message_id_in_two_chats_has_two_references():
    routes = Routes(
        chats={
            CHAT_A: [_page(_message("1760000000555"))],
            CHAT_B: [_page(_message("1760000000555"))],
        }
    )
    result = _connector(routes, _chat(CHAT_A), _chat(CHAT_B)).harvest(Cursor())
    references = {str(event.source_ref) for event in _messages(result)}
    assert references == {
        f"graph:personal:message:{CHAT_A}/1760000000555",
        f"graph:personal:message:{CHAT_B}/1760000000555",
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


def test_listed_chats_reach_the_built_connector_with_one_client(tmp_path):
    _enrol(
        tmp_path,
        **{
            CHATS_KEY: [
                {"chat": CHAT_A, "scope": "personal", "label": "Sam"},
                {"chat": CHAT_B, "scope": "project:alpha"},
            ]
        },
    )
    connector = build(tmp_path, "alpha", now=lambda: NOW).connectors[INSTANCE]
    assert isinstance(connector, GraphConnector)
    assert connector.chats == (_chat(CHAT_A), _chat(CHAT_B, scope=ALPHA, label=None))
    assert connector.chat_messages is not None
    assert connector.chat_messages.client is connector.calendar.client
    assert connector.chat_messages.reach_back == REACH_BACK


def test_no_chat_list_reads_no_chat(tmp_path):
    _enrol(tmp_path)
    connector = build(tmp_path, "alpha", now=lambda: NOW).connectors[INSTANCE]
    assert connector.chats == ()


@pytest.mark.parametrize("scope", ["people:dana", "People:dana", "PEOPLE:dana", "people"])
def test_a_people_scope_is_refused_naming_the_missing_registry(scope):
    with pytest.raises(ChatListRefused) as refused:
        graph_chat_listings(
            {CHATS_KEY: [{"chat": CHAT_A, "scope": scope}]}, registered=lambda p: True
        )
    assert "team-member registry" in str(refused.value)
    assert "entry 1" in str(refused.value)


def test_a_people_scope_leaves_the_daemon_composing_with_no_graph_connector(tmp_path, capsys):
    """Acceptance: not built; the message names the missing team-member registry."""
    _enrol(tmp_path, **{CHATS_KEY: [{"chat": CHAT_A, "scope": "people:dana"}]})
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    assert INSTANCE not in daemon.connectors
    assert "team-member registry" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("listed", "named"),
    [
        ({"chat": CHAT_A, "scope": "personal"}, "list"),
        ("19:x@thread.v2", "list"),
        (["19:x@thread.v2"], "entry 1"),
        ([{"scope": "personal"}], "entry 1"),
        ([{"chat": "  ", "scope": "personal"}], "entry 1"),
        ([{"chat": "19:a b@thread.v2", "scope": "personal"}], "entry 1"),
        ([{"chat": "19:a/b@thread.v2", "scope": "personal"}], "entry 1"),
        ([{"chat": CHAT_A}], "entry 1"),
        ([{"chat": CHAT_A, "scope": 3}], "entry 1"),
        ([{"chat": CHAT_A, "scope": "application"}], "entry 1"),
        ([{"chat": CHAT_A, "scope": "project"}], "entry 1"),
        ([{"chat": CHAT_A, "scope": "project:"}], "entry 1"),
        ([{"chat": CHAT_A, "scope": "elsewhere"}], "entry 1"),
        ([{"chat": CHAT_A, "scope": "people"}], "entry 1"),
        ([{"chat": CHAT_A, "scope": "project:personal"}], "entry 1"),
        ([{"chat": CHAT_A, "scope": "personal", "label": 3}], "entry 1"),
        ([{"chat": CHAT_A, "scope": "personal", "label": "bell\x07"}], "entry 1"),
        ([{"chat": CHAT_A, "scope": "personal", "label": "x" * (MAX_LABEL_LENGTH + 1)}], "entry 1"),
        ([{"chat": ".", "scope": "personal"}], "entry 1"),
        ([{"chat": "..", "scope": "personal"}], "entry 1"),
        ([{"chat": "1" * (MAX_CHAT_ID_LENGTH + 1), "scope": "personal"}], "MAX_CHAT_ID_LENGTH"),
        ([{"chat": CHAT_A, "scope": " personal"}], "padded"),
        ([{"chat": CHAT_A, "scope": "project:alpha "}], "padded"),
        ([{"chat": CHAT_A, "scope": "project:Personal"}], "entry 1"),
        ([{"chat": CHAT_A, "scope": "project:PERSONAL"}], "entry 1"),
        ([{"chat": CHAT_A, "scope": "project:al+pha"}], "grammar"),
        ([{"chat": CHAT_A, "scope": "personal"}, {"chat": "19:b@thread.v2"}], "entry 2"),
    ],
)
def test_a_malformed_list_builds_nothing_and_names_the_entry(listed, named):
    with pytest.raises(ChatListRefused) as refused:
        graph_chat_listings({CHATS_KEY: listed}, registered=lambda p: True)
    assert named in str(refused.value)


def test_a_chat_listed_twice_is_refused_naming_both_entries():
    listed = [
        {"chat": CHAT_A, "scope": "personal"},
        {"chat": CHAT_A.upper(), "scope": "project:alpha"},
    ]
    with pytest.raises(ChatListRefused) as refused:
        graph_chat_listings({CHATS_KEY: listed}, registered=lambda p: True)
    assert CHAT_A.upper() in str(refused.value)
    assert "entries 1 and 2" in str(refused.value)


def test_an_unenrolled_project_is_refused_by_name():
    with pytest.raises(UnknownProject) as refused:
        graph_chat_listings(
            {CHATS_KEY: [{"chat": CHAT_A, "scope": "project:ghost"}]},
            registered=lambda project: project == "alpha",
        )
    assert "'ghost'" in str(refused.value)
    assert CHAT_A in str(refused.value)


def test_a_malformed_chat_list_leaves_the_daemon_composing(tmp_path, capsys):
    _enrol(tmp_path, **{CHATS_KEY: [{"chat": CHAT_A}]})
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    assert INSTANCE not in daemon.connectors
    assert "chat entry 1" in capsys.readouterr().err


# ── Quiet chat, first run, an older cursor ───────────────────────────────────


def test_a_quiet_chat_emits_nothing_and_the_cursor_still_carries_its_position():
    position = NOW - timedelta(hours=3)
    routes = Routes(chats={CHAT_A: [_page()]})
    result = _connector(routes).harvest(_cursor(chats={CHAT_A: position}))
    assert _messages(result) == []
    assert result.outcome is HarvestOutcome.EMPTY
    assert _chat_position(result.cursor, CHAT_A) == position
    assert result.coverage is not None


def test_a_quiet_chat_keeps_its_position_beside_one_that_moved():
    quiet = NOW - timedelta(days=2)
    routes = Routes(chats={CHAT_A: [_page()], CHAT_B: [_page(_message())]})
    result = _connector(routes, _chat(CHAT_A), _chat(CHAT_B)).harvest(
        _cursor(chats={CHAT_A: quiet, CHAT_B: quiet})
    )
    assert _chat_position(result.cursor, CHAT_A) == quiet
    assert _chat_position(result.cursor, CHAT_B) == NOW - timedelta(hours=1)


def test_a_first_run_reaches_back_as_far_as_the_row_says():
    inside = _message("1760000000801", at=NOW - REACH_BACK + timedelta(minutes=1))
    outside = _message("1760000000802", at=NOW - REACH_BACK - timedelta(minutes=1))
    outside["lastModifiedDateTime"] = inside["lastModifiedDateTime"]
    routes = Routes(chats={CHAT_A: [_page(inside, outside)]})
    result = _connector(routes).harvest(Cursor())

    assert list(_by_id(result)) == ["1760000000801"]
    (request,) = routes.asked(CHAT_A)
    assert _filter_instant(request) < NOW - REACH_BACK <= _filter_instant(request) + timedelta(milliseconds=2)


def test_a_first_run_with_nothing_found_stores_the_reach_back():
    result = _connector(Routes(chats={CHAT_A: [_page()]})).harvest(Cursor())
    assert _chat_position(result.cursor, CHAT_A) == NOW - REACH_BACK


def test_a_cursor_from_the_channel_slice_reads_and_every_chat_is_a_first_run():
    calendar_position = NOW - timedelta(days=3)
    channel_position = NOW - timedelta(hours=6)
    since = _cursor(calendar=calendar_position, channels={CHANNEL: channel_position})
    assert "chats" not in _positions(since)
    channel_message = _message("1700000000001", at=NOW - timedelta(days=1))
    routes = Routes(
        channels={CHANNEL: [_page(channel_message)]},
        chats={CHAT_A: [_page(_message(at=NOW - timedelta(days=6)))]},
    )
    listing = ChannelListing(team=TEAM, channel=CHANNEL, scope=ALPHA, label="Build room")
    result = _connector(routes, channels=(listing,)).harvest(since)

    (calendar,) = [r for r in routes.requests if "/me/calendarView" in r.url]
    assert "startDateTime=" + calendar_position.strftime("%Y-%m-%dT%H:%M:%SZ") in calendar.url
    events = _by_id(result)
    assert "1700000000001" not in events, "the channel's stored position was read"
    assert "1760000000500" in events, "the chat reached back seven days"
    (request,) = routes.asked(CHAT_A)
    assert _filter_instant(request) < NOW - REACH_BACK
    assert result.failure is None
    positions = _positions(result.cursor)
    assert datetime.fromisoformat(positions["channels"][CHANNEL]) == channel_position
    assert CHAT_A.casefold() in positions["chats"]


@pytest.mark.parametrize("token", [b'{"chats": []}', b'{"chats": {"19:x@thread.v2": "2026-10-09T10:00:00"}}'])
def test_a_malformed_chats_member_is_refused_and_nothing_is_fetched(token):
    routes = Routes(chats={CHAT_A: [_page()]})
    result = _connector(routes).harvest(Cursor(token))
    assert result.outcome is HarvestOutcome.FAILED
    assert result.cursor == Cursor(token)
    assert routes.requests == []


def test_chat_positions_are_read_whatever_case_they_were_written_in():
    position = NOW - timedelta(hours=2)
    routes = Routes(chats={CHAT_A: [_page()]})
    result = _connector(routes).harvest(_cursor(chats={CHAT_A.upper(): position}))
    (request,) = routes.asked(CHAT_A)
    assert _filter_instant(request) < position
    assert set(_positions(result.cursor)["chats"]) == {CHAT_A.casefold()}


# ── The client cut ───────────────────────────────────────────────────────────


def test_rows_the_server_should_have_filtered_are_not_emitted_and_end_the_walk():
    position = NOW - timedelta(days=1)
    older = _message("1760000000901", at=NOW - timedelta(days=2))
    routes = Routes(chats={CHAT_A: [_page(older, next_link=_next(CHAT_A))]})
    result = _connector(routes).harvest(_cursor(chats={CHAT_A: position}))

    assert _messages(result) == []
    assert len(routes.asked(CHAT_A)) == 1, "a page wholly older than the position ends the walk"
    assert result.failure is None
    assert _chat_position(result.cursor, CHAT_A) == position


def test_an_old_message_edited_since_is_in_range_but_never_emitted():
    position = NOW - timedelta(days=1)
    edited = _message("1760000000902", at=NOW - timedelta(days=3))
    edited["lastModifiedDateTime"] = NOW.isoformat().replace("+00:00", "Z")
    later = _message("1760000000903", at=NOW - timedelta(days=4))
    routes = Routes(chats={CHAT_A: [_page(edited, next_link=_next(CHAT_A)), _page(later)]})
    result = _connector(routes).harvest(_cursor(chats={CHAT_A: position}))

    assert _messages(result) == []
    assert len(routes.asked(CHAT_A)) == 2, "an edited row is in range, so the walk goes on"
    assert _chat_position(result.cursor, CHAT_A) == position


def test_a_message_edited_after_harvest_is_not_emitted_again(tmp_path):
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    first = _message(at=NOW - timedelta(hours=2))
    edited = copy.deepcopy(first)
    edited["lastModifiedDateTime"] = NOW.isoformat().replace("+00:00", "Z")
    edited["body"] = {"contentType": "text", "content": "edited"}
    routes = Routes(calendar=[EMPTY_CALENDAR, EMPTY_CALENDAR], chats={CHAT_A: [_page(first), _page(edited)]})
    daemon.connectors[INSTANCE] = _connector(routes)

    assert run_harvest(daemon, INSTANCE).persisted == 1
    assert run_harvest(daemon, INSTANCE).persisted == 0
    assert "edited" not in _log(daemon, PERSONAL)


def test_the_same_message_on_two_pages_is_emitted_once():
    message = _message()
    routes = Routes(chats={CHAT_A: [_page(message, next_link=_next(CHAT_A)), _page(copy.deepcopy(message))]})
    result = _connector(routes).harvest(Cursor())
    assert len(_messages(result)) == 1


def test_an_unreadable_row_is_refused_naming_the_chat_and_the_page_continues():
    bad = _message("1760000000904", body=None)
    good = _message("1760000000905")
    result = _connector(Routes(chats={CHAT_A: [_page(bad, good)]})).harvest(Cursor())
    assert list(_by_id(result)) == ["1760000000905"]
    (refusal,) = result.refusals
    assert refusal.identifier == f"{CHAT_A}/1760000000904"
    assert "chat 'Sam'" in refusal.reason


# ── Label, topic, kind ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "topic", "kind", "expected"),
    [
        (None, None, "oneOnOne", "1:1 chat"),
        (None, "   ", "group", "group chat"),
        (None, "", "meeting", "meeting chat"),
        (None, None, "unknownFutureValue", "chat"),
        (None, None, None, "chat"),
        (None, "Release planning", "group", "Release planning"),
        ("Dana", "Release planning", "group", "Dana"),
    ],
)
def test_the_channel_field_is_the_label_then_the_topic_then_the_kind(label, topic, kind, expected):
    routes = Routes(chats={CHAT_A: [_page(_message())]}, about={CHAT_A: [_about(topic=topic, chatType=kind)]})
    result = _connector(routes, _chat(label=label)).harvest(Cursor())
    (event,) = _messages(result)
    assert event.payload.channel == expected
    assert len(routes.asked_about(CHAT_A)) == (0 if label else 1)


def test_a_meeting_chat_is_filed_by_its_listed_scope():
    routes = Routes(chats={CHAT_B: [_page(_message())]}, about={CHAT_B: [_about(chatType="meeting", topic=None)]})
    result = _connector(routes, _chat(CHAT_B, scope=ALPHA, label=None)).harvest(Cursor())
    (event,) = _messages(result)
    assert event.scope == ALPHA
    assert event.payload.channel == "meeting chat"
    assert [r for r in routes.requests if "/onlineMeetings" in r.url or "/events" in r.url] == []


# ── One failure fails one chat ───────────────────────────────────────────────


def _two(a: list[GraphResponse], b: list[GraphResponse], *, since: Cursor = Cursor(), about_b=None):
    routes = Routes(chats={CHAT_A: a, CHAT_B: b}, about={CHAT_B: about_b} if about_b else None)
    connector = _connector(routes, _chat(CHAT_A), _chat(CHAT_B, scope=ALPHA, label="Standup" if not about_b else None))
    return connector.harvest(since), routes


@pytest.mark.parametrize("code", [404, 400])
def test_a_missing_chat_or_a_rejected_request_fails_alone_and_keeps_its_position(tmp_path, code):
    previous = NOW - timedelta(days=2)
    result, _ = _two([_page(_message())], [_status(code)], since=_cursor(chats={CHAT_B: previous}))

    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None and result.failure.retryable is False
    assert "'Standup'" in result.failure.reason and CHAT_B in result.failure.reason
    assert _chat_position(result.cursor, CHAT_B) == previous
    assert CHAT_A.casefold() in _positions(result.cursor)["chats"]
    assert result.coverage is None
    assert list(_by_id(result)) == ["1760000000500"]

    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    routes = Routes(chats={CHAT_A: [_page(_message())], CHAT_B: [_status(code)]})
    daemon.connectors[INSTANCE] = _connector(routes, _chat(CHAT_A), _chat(CHAT_B, scope=ALPHA, label="Standup"))
    assert run_harvest(daemon, INSTANCE).persisted == 1
    stored = _positions(daemon.storage.load_cursor(INSTANCE))["chats"]
    assert CHAT_A.casefold() in stored and CHAT_B.casefold() not in stored


def test_a_chat_refused_with_403_fails_alone_and_keeps_its_position():
    previous = NOW - timedelta(days=2)
    result, _ = _two([_page(_message())], [_status(403)], since=_cursor(chats={CHAT_B: previous}))
    assert result.failure is not None and result.failure.retryable is False
    assert "'Standup'" in result.failure.reason
    assert _chat_position(result.cursor, CHAT_B) == previous
    assert list(_by_id(result)) == ["1760000000500"]
    assert result.coverage is None


def test_404_400_and_403_are_worded_apart():
    reasons = {}
    for code in (404, 400, 403):
        result, _ = _two([_page()], [_status(code)])
        reasons[code] = result.failure.reason
    assert "likely mistyped" in reasons[404]
    assert "would not take the request" in reasons[400]
    assert "error code x" in reasons[400]
    assert "mistyped" not in reasons[400]
    assert "not a member" in reasons[403] and "Chat.Read" in reasons[403]
    assert "mistyped" not in reasons[403]
    assert len(set(reasons.values())) == 3


def test_a_mistyped_chat_found_by_its_topic_lookup_fails_alone():
    result, routes = _two([_page(_message())], [], about_b=[_status(404)])
    assert routes.asked(CHAT_B) == []
    assert result.failure is not None and result.failure.retryable is False
    assert CHAT_B in result.failure.reason and "mistyped" in result.failure.reason
    assert list(_by_id(result)) == ["1760000000500"]


def test_a_throttle_on_a_chats_second_page_keeps_page_one_holds_the_position_and_skips_the_rest():
    previous = NOW - timedelta(days=2)
    first = _message("1760000000951", at=NOW - timedelta(minutes=5))
    routes = Routes(
        chats={
            CHAT_A: [_page(first, next_link=_next(CHAT_A)), _status(429, **{"Retry-After": "90"})],
            CHAT_B: [_page(_message())],
        }
    )
    result = _connector(routes, _chat(CHAT_A), _chat(CHAT_B, label="Standup")).harvest(
        _cursor(chats={CHAT_A: previous})
    )

    assert list(_by_id(result)) == ["1760000000951"], "page one's message is kept"
    assert _chat_position(result.cursor, CHAT_A) == previous
    assert routes.asked(CHAT_B) == [], "the later chat is skipped"
    assert result.failure is not None and result.failure.retryable is True
    assert result.failure.retry_after == timedelta(seconds=90)
    assert "'Sam'" in result.failure.reason
    assert "Not attempted this run: chat(s) 'Standup'" in result.failure.reason
    assert result.coverage is None


def test_a_server_error_on_a_chat_skips_the_remaining_chats():
    routes = Routes(chats={CHAT_A: [_status(503)], CHAT_B: [_page(_message())]})
    result = _connector(routes, _chat(CHAT_A), _chat(CHAT_B, label="Standup")).harvest(Cursor())
    assert routes.asked(CHAT_B) == []
    assert result.failure is not None and result.failure.retryable is True
    assert "'Standup'" in result.failure.reason


def test_a_chat_sign_in_failure_stops_the_run():
    routes = Routes(chats={CHAT_A: [_status(401), _status(401)], CHAT_B: [_page(_message())]})
    result = _connector(routes, _chat(CHAT_A), _chat(CHAT_B, label="Standup")).harvest(Cursor())
    assert routes.asked(CHAT_B) == []
    assert result.failure is not None and result.failure.retryable is False


def test_a_calendar_sign_in_failure_names_every_chat_as_not_attempted():
    routes = Routes(calendar=[_status(401), _status(401)], chats={CHAT_A: [_page()]})
    result = _connector(routes).harvest(Cursor())
    assert routes.asked(CHAT_A) == []
    assert "Not attempted this run: chat(s) 'Sam'" in result.failure.reason


def test_a_page_cap_cut_advances_to_the_newest_and_says_what_was_skipped():
    newest = NOW - timedelta(minutes=10)
    position = NOW - timedelta(days=5)
    routes = Routes(chats={CHAT_A: [_page(_message("1760000000961", at=newest), next_link=_next(CHAT_A))]})
    connector = _connector(routes)
    connector.calendar.client.page_cap = 1
    result = connector.harvest(_cursor(chats={CHAT_A: position}))
    assert list(_by_id(result)) == ["1760000000961"]
    assert _chat_position(result.cursor, CHAT_A) == newest
    assert result.failure is not None and result.failure.retryable is True
    reason = result.failure.reason
    assert "at the 1-page cap after 1 page(s)" in reason
    assert f"before reaching its position {position.isoformat()}" in reason
    assert "modified earlier than the last one read were not read" in reason
    assert f"advanced to {newest.isoformat()}" in reason
    assert "created from" not in reason, "no creation-time range the walk did not establish"
    assert result.coverage is None


# ── The rest of the run ──────────────────────────────────────────────────────


def test_chats_are_walked_after_the_channels_under_the_one_budget():
    clock = [NOW]

    def _transport(request: GraphRequest) -> GraphResponse:
        if "/channels/" in request.url:
            clock[0] = NOW + timedelta(minutes=10)
        return routes(request)

    routes = Routes(channels={CHANNEL: [_page()]}, chats={CHAT_A: [_page(_message())]})
    listing = ChannelListing(team=TEAM, channel=CHANNEL, scope=ALPHA)
    connector = _connector(routes, channels=(listing,), clock=lambda: clock[0])
    connector.calendar.client.transport = _transport
    connector.calendar.client.budget = timedelta(minutes=10)  # the channel spends all of it
    result = connector.harvest(Cursor())
    assert routes.asked(CHAT_A) == []
    assert result.failure is not None
    assert result.failure.reason.count("time budget") == 1
    assert "Not attempted this run: chat(s) 'Sam'" in result.failure.reason


def test_the_dashboards_calendar_read_sends_no_chat_request():
    routes = Routes(chats={CHAT_A: []})
    day = datetime(2026, 10, 9, tzinfo=timezone.utc)
    _connector(routes).read_calendar(day, day + timedelta(days=1))
    assert [r for r in routes.requests if "/chats/" in r.url] == []


def test_the_sample_includes_a_chat_message_built_by_the_same_mapping():
    samples = _connector(Routes()).sample_events()
    chat = [event for event in samples if "unq.gbl.spaces" in str(event.source_ref)]
    assert len(chat) == 1
    assert chat[0].scope == PERSONAL
    assert str(chat[0].source_ref).startswith("graph:personal:message:")


def test_a_chat_list_beside_a_channel_list_builds_both(tmp_path):
    _enrol(
        tmp_path,
        **{
            CHANNELS_KEY: [{"team": TEAM, "channel": CHANNEL, "project": "alpha"}],
            CHATS_KEY: [{"chat": CHAT_A, "scope": "personal"}],
        },
    )
    connector = build(tmp_path, "alpha", now=lambda: NOW).connectors[INSTANCE]
    assert len(connector.channels) == 1 and len(connector.chats) == 1


# ── Review findings (story 33f, second pass) ─────────────────────────────────


def test_an_older_page_stops_the_walk_only_while_rows_arrive_newest_first():
    """The server ignored `$orderby` too: page one is ascending, so a newer
    message may still be on page two — the walk pages on."""
    position = NOW - timedelta(days=1)
    first = _message("1760000001001", at=NOW - timedelta(days=3))
    second = _message("1760000001002", at=NOW - timedelta(days=2))
    fresh = _message("1760000001003", at=NOW - timedelta(hours=1))
    routes = Routes(chats={CHAT_A: [_page(first, second, next_link=_next(CHAT_A)), _page(fresh)]})
    result = _connector(routes).harvest(_cursor(chats={CHAT_A: position}))
    assert len(routes.asked(CHAT_A)) == 2
    assert list(_by_id(result)) == ["1760000001003"]


def test_an_older_page_in_descending_order_stops_the_walk():
    position = NOW - timedelta(days=1)
    first = _message("1760000001004", at=NOW - timedelta(days=2))
    second = _message("1760000001005", at=NOW - timedelta(days=3))
    routes = Routes(chats={CHAT_A: [_page(first, second, next_link=_next(CHAT_A))]})
    result = _connector(routes).harvest(_cursor(chats={CHAT_A: position}))
    assert len(routes.asked(CHAT_A)) == 1
    assert result.failure is None


def test_an_unreachable_token_endpoint_on_a_chat_skips_the_remaining_chats():
    calls = {"n": 0}

    def _token(*, force_refresh: bool = False) -> str:
        calls["n"] += 1
        if calls["n"] == 2:  # the first chat's page; the calendar was the first
            raise GraphUnreachable("the token endpoint did not answer")
        return "token-1"

    routes = Routes(chats={CHAT_A: [], CHAT_B: [_page(_message())]})
    connector = _connector(routes, _chat(CHAT_A), _chat(CHAT_B, label="Standup"))
    connector.calendar.client.auth.access_token = _token  # type: ignore[method-assign]
    result = connector.harvest(Cursor())
    assert routes.asked(CHAT_B) == []
    assert result.failure is not None and result.failure.retryable is True
    assert "'Sam'" in result.failure.reason
    assert "Not attempted this run: chat(s) 'Standup'" in result.failure.reason


def test_a_budget_spent_before_the_chats_is_one_failure_naming_every_chat():
    clock = [NOW]

    def _transport(request: GraphRequest) -> GraphResponse:
        if "/me/calendarView" in request.url:
            clock[0] = NOW + timedelta(minutes=10)
        return routes(request)

    routes = Routes(chats={CHAT_A: [_page()], CHAT_B: [_page()]})
    connector = _connector(routes, _chat(CHAT_A), _chat(CHAT_B, label="Standup"), clock=lambda: clock[0])
    connector.calendar.client.budget = timedelta(minutes=10)  # the calendar spends all of it
    connector.calendar.client.transport = _transport
    result = connector.harvest(Cursor())
    assert routes.asked(CHAT_A) == [] and routes.asked(CHAT_B) == []
    assert result.failure is not None and result.failure.retryable is True
    assert result.failure.reason.count("time budget") == 1
    assert "Not attempted this run: chat(s) 'Sam', 'Standup'" in result.failure.reason


def test_a_budget_spent_during_a_chat_cuts_it_and_names_the_rest():
    clock = [NOW]
    newest = NOW - timedelta(minutes=5)

    def _transport(request: GraphRequest) -> GraphResponse:
        if f"/chats/{CHAT_A}/messages" in request.url:
            clock[0] = NOW + timedelta(minutes=10)
        return routes(request)

    routes = Routes(
        chats={
            CHAT_A: [_page(_message("1760000001006", at=newest), next_link=_next(CHAT_A))],
            CHAT_B: [_page()],
        }
    )
    connector = _connector(routes, _chat(CHAT_A), _chat(CHAT_B, label="Standup"), clock=lambda: clock[0])
    connector.calendar.client.budget = timedelta(minutes=10)
    connector.calendar.client.transport = _transport
    result = connector.harvest(_cursor(chats={CHAT_A: NOW - timedelta(days=1)}))

    assert list(_by_id(result)) == ["1760000001006"]
    assert routes.asked(CHAT_B) == []
    reason = result.failure.reason
    assert reason.count("time budget") == 1
    assert "stopped walking chat 'Sam' at the run's time budget after 1 page(s)" in reason
    assert "Not attempted this run: chat(s) 'Standup'" in reason
    assert _chat_position(result.cursor, CHAT_A) == newest


def test_a_channel_throttled_skips_every_chat_and_names_them():
    routes = Routes(channels={CHANNEL: [_status(429, **{"Retry-After": "60"})]}, chats={CHAT_A: [_page()]})
    listing = ChannelListing(team=TEAM, channel=CHANNEL, scope=ALPHA, label="Build room")
    result = _connector(routes, channels=(listing,)).harvest(Cursor())
    assert routes.asked(CHAT_A) == []
    assert "Not attempted this run: chat(s) 'Sam'" in result.failure.reason
    assert result.failure.retry_after == timedelta(seconds=60)


def test_a_channel_sign_in_failure_sends_no_chat_request_and_names_the_chat():
    routes = Routes(channels={CHANNEL: [_status(401), _status(401)]}, chats={CHAT_A: [_page()]})
    listing = ChannelListing(team=TEAM, channel=CHANNEL, scope=ALPHA, label="Build room")
    result = _connector(routes, channels=(listing,)).harvest(Cursor())
    assert [r for r in routes.requests if "/chats/" in r.url and "/messages" in r.url] == []
    assert "Not attempted this run: chat(s) 'Sam'" in result.failure.reason


def test_a_stored_position_ahead_of_the_clock_is_pulled_back_to_now():
    ahead = NOW + timedelta(hours=2)
    routes = Routes(chats={CHAT_A: [_page(_message(at=NOW))]})
    result = _connector(routes).harvest(_cursor(chats={CHAT_A: ahead}))
    (request,) = routes.asked(CHAT_A)
    assert NOW - timedelta(milliseconds=2) <= _filter_instant(request) < NOW
    assert list(_by_id(result)) == ["1760000000500"]
    assert _chat_position(result.cursor, CHAT_A) == NOW


def test_a_chat_page_with_no_value_array_holds_the_position_and_claims_no_window():
    position = NOW - timedelta(days=1)
    unreadable = GraphResponse(status=200, body={"@odata.nextLink": _next(CHAT_A)}, headers={})
    routes = Routes(chats={CHAT_A: [unreadable, _page(_message())]})
    result = _connector(routes).harvest(_cursor(chats={CHAT_A: position}))
    assert any("no `value` array" in refusal.reason for refusal in result.refusals)
    assert _chat_position(result.cursor, CHAT_A) == position
    assert result.coverage is None


def test_a_chat_whose_only_row_is_refused_claims_no_window():
    result = _connector(Routes(chats={CHAT_A: [_page(_message(body=None))]})).harvest(Cursor())
    assert len(result.refusals) == 1
    assert result.coverage is None


def test_the_window_ends_where_the_last_chat_walk_finished():
    clock = iter(NOW + timedelta(seconds=step) for step in range(1000))
    readings: list = []
    tick = lambda: next(clock)  # noqa: E731
    routes = Routes(chats={CHAT_A: [_page()], CHAT_B: [_page()]})
    connector = _connector(routes, _chat(CHAT_A), _chat(CHAT_B), clock=tick)
    real = connector.chat_messages.fetch

    def _recorded(listing, **kwargs):
        fetched = real(listing, **kwargs)
        readings.append(fetched.finished)
        return fetched

    connector.chat_messages.fetch = _recorded  # type: ignore[method-assign]
    result = connector.harvest(Cursor())
    assert result.coverage is not None
    assert result.coverage.end == readings[-1]


def test_a_chat_walk_that_raises_fails_that_chat_only(monkeypatch):
    routes = Routes(chats={CHAT_A: [_page(_message())]})
    connector = _connector(routes, _chat(CHAT_A), _chat(CHAT_B, label="Standup"))
    real = connector.chat_messages.fetch

    def _flaky(listing, **kwargs):
        if listing.chat == CHAT_B:
            raise RuntimeError("boom")
        return real(listing, **kwargs)

    monkeypatch.setattr(connector.chat_messages, "fetch", _flaky)
    result = connector.harvest(Cursor())
    assert list(_by_id(result)) == ["1760000000500"]
    assert "could not start walking chat 'Standup'" in result.failure.reason
    assert "RuntimeError" in result.failure.reason
    assert CHAT_A.casefold() in _positions(result.cursor)["chats"]


def test_a_mapping_fault_names_the_resource_that_answered(monkeypatch):
    routes = Routes(calendar=[_status(503)], chats={CHAT_A: [_page(_message())]})
    connector = _connector(routes)

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(connector, "_result", _boom)
    result = connector.harvest(Cursor())
    assert result.outcome is HarvestOutcome.FAILED
    assert "a page of chat 'Sam'" in result.failure.reason


def test_a_chats_member_of_null_reads_as_no_positions():
    routes = Routes(chats={CHAT_A: [_page(_message())]})
    result = _connector(routes).harvest(Cursor(b'{"calendar": null, "channels": {}, "chats": null}'))
    assert result.failure is None
    assert list(_by_id(result)) == ["1760000000500"]


def test_a_connector_with_no_chats_writes_no_chats_member():
    routes = Routes(calendar=[_page()])
    connector = _connector(routes)
    connector.chats = ()
    position = NOW - timedelta(hours=1)
    listing = ChannelListing(team=TEAM, channel=CHANNEL, scope=ALPHA)
    connector.channels = (listing,)
    routes.channels = {CHANNEL: [_page(_message(at=position))]}
    result = connector.harvest(Cursor())
    assert "chats" not in _positions(result.cursor)


@pytest.mark.parametrize("scope", [DataScope(ScopeKind.APPLICATION), DataScope(ScopeKind.PEOPLE, person_id="dana")])
def test_a_chat_listing_refuses_an_application_or_people_scope(scope):
    with pytest.raises(ValueError):
        ChatListing(chat=CHAT_A, scope=scope)


def test_a_message_created_exactly_at_the_position_is_emitted():
    """Why `FILTER_SLACK` exists: the filter is `gt`, the cut is at-or-after."""
    position = NOW - timedelta(hours=1)
    routes = Routes(chats={CHAT_A: [_page(_message("1760000001007", at=position))]})
    result = _connector(routes).harvest(_cursor(chats={CHAT_A: position}))
    (request,) = routes.asked(CHAT_A)
    assert _filter_instant(request) == position - FILTER_SLACK
    assert list(_by_id(result)) == ["1760000001007"]


def test_a_row_with_no_modification_time_is_read_by_its_creation_time():
    row = _message("1760000001008", at=NOW - timedelta(hours=1))
    del row["lastModifiedDateTime"]
    later = _message("1760000001009", at=NOW - timedelta(hours=2))
    position = NOW - timedelta(days=1)
    routes = Routes(chats={CHAT_A: [_page(row, next_link=_next(CHAT_A)), _page(later)]})
    result = _connector(routes).harvest(_cursor(chats={CHAT_A: position}))
    assert sorted(_by_id(result)) == ["1760000001008", "1760000001009"]
    assert len(routes.asked(CHAT_A)) == 2, "in range by its creation time, so the walk goes on"


def test_a_row_with_no_readable_time_at_all_counts_as_older_and_is_refused():
    row = _message("1760000001010", createdDateTime="not a time")
    del row["lastModifiedDateTime"]
    routes = Routes(chats={CHAT_A: [_page(row, next_link=_next(CHAT_A))]})
    result = _connector(routes).harvest(_cursor(chats={CHAT_A: NOW - timedelta(days=1)}))
    assert len(routes.asked(CHAT_A)) == 1
    (refusal,) = result.refusals
    assert refusal.identifier == f"{CHAT_A}/1760000001010"


@pytest.mark.parametrize("body", [None, ["not", "an", "object"]])
def test_a_name_lookup_that_is_not_an_object_fails_that_chat_alone(body):
    lookup = GraphResponse(status=200, body=body, headers={})  # type: ignore[arg-type]
    result, routes = _two([_page(_message())], [_page(_message())], about_b=[lookup])
    assert routes.asked(CHAT_B) == []
    assert result.failure is not None and result.failure.retryable is False
    assert CHAT_B in result.failure.reason
    assert list(_by_id(result)) == ["1760000000500"]


@pytest.mark.parametrize("answer", [_status(429, **{"Retry-After": "30"}), _status(503)])
def test_a_busy_name_lookup_skips_the_remaining_chats(answer):
    routes = Routes(chats={CHAT_A: [], CHAT_B: [_page()]}, about={CHAT_A: [answer]})
    connector = _connector(routes, _chat(CHAT_A, label=None), _chat(CHAT_B, label="Standup"))
    result = connector.harvest(Cursor())
    assert routes.asked(CHAT_A) == [] and routes.asked(CHAT_B) == []
    assert result.failure is not None and result.failure.retryable is True
    assert "Not attempted this run: chat(s) 'Standup'" in result.failure.reason


@pytest.mark.parametrize(
    "topic",
    ["x" * (MAX_LABEL_LENGTH + 1), "bell\x07", "zero​width"],
)
def test_a_topic_failing_the_label_rules_falls_back_to_the_kind_word(topic):
    routes = Routes(chats={CHAT_A: [_page(_message())]}, about={CHAT_A: [_about(topic=topic, chatType="group")]})
    result = _connector(routes, _chat(label=None)).harvest(Cursor())
    (event,) = _messages(result)
    assert event.payload.channel == "group chat"


@pytest.mark.parametrize(("rotated", "retryable"), [(False, True), (True, False)])
def test_a_busy_credentials_file_on_a_chat_is_retryable_only_when_nothing_was_rotated(
    rotated, retryable
):
    """Story 9a: waiting clears a busy file that lost nothing; a lost rotation needs a sign-in."""
    from pm_ai.connectors.graph.auth import CredentialStoreBusy

    calls = {"n": 0}

    def _token(*, force_refresh: bool = False) -> str:
        calls["n"] += 1
        if calls["n"] == 2:  # the chat's page; the calendar was the first
            raise CredentialStoreBusy("the credentials file was busy.", rotated=rotated)
        return "token-1"

    routes = Routes(chats={CHAT_A: []})
    connector = _connector(routes, _chat(CHAT_A))
    connector.calendar.client.auth.access_token = _token  # type: ignore[method-assign]
    result = connector.harvest(Cursor())
    assert result.failure is not None and result.failure.retryable is retryable
    assert "busy" in result.failure.reason
