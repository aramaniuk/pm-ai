"""Story 33f over a real tenant's page: `fixtures/graph/chat_messages_recorded.json`.

One page of a meeting chat, captured on 2026-10-15 through pm-ai's own Graph
client with the filtered, sorted request the chat walk sends, then redacted;
its `_provenance` says how. The hand-written `chat_messages.json` beside it
stays: the matrix tests in `test_graph_chat_messages.py` assert values written
into it.

The page goes through a scripted transport, the real walk (`chats.py`) and the
real event mapping (`GraphConnector`). What each test expects is worked out
from the page itself. No `GET /chats/{chat}` answer was captured, so the chat
is listed with a label and that request is never made.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from pm_ai.connectors.graph import CategoryScopes, ChatListing, GraphConnector
from pm_ai.connectors.graph.calendar import GraphCalendarFetch, WindowPolicy
from pm_ai.connectors.graph.channels import MESSAGE_TYPE, GraphChannelFetch, read_message
from pm_ai.connectors.graph.chats import GraphChatFetch
from pm_ai.connectors.graph.client import GraphClient
from pm_ai.domain.events import MessagePayload
from pm_ai.domain.harvest import Cursor, HarvestOutcome
from pm_ai.domain.identity import DataScope, ScopeKind
from tests.connectors.recorded_graph import (
    INSTANCE,
    Auth,
    Recorded,
    graph_time,
    harvest_then_dashboard,
    mentioned_users,
    message_events,
    recorded,
    signal_lines,
    tag_names,
    tags_left,
)

PAGE = recorded("chat_messages_recorded.json")["page"]
ROWS: list[dict] = PAGE["value"]

LABEL = "Recorded meeting chat"
CHAT = ROWS[0]["chatId"]
PERSONAL = DataScope(ScopeKind.PERSONAL)
NOW = datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)
"""The day after the capture: no row on the page is in the future."""
POSITION = datetime(2026, 10, 1, tzinfo=timezone.utc)
"""Older than every row on the page, as the capture's own filter was."""


def _harvest(transport: Recorded):
    identity = Auth()
    client = GraphClient(auth=identity, transport=transport, now=lambda: NOW)
    connector = GraphConnector(
        auth=identity,
        calendar=GraphCalendarFetch(
            client=client,
            instance=INSTANCE,
            windows=WindowPolicy(width=timedelta(hours=24), first_run_reach_back=timedelta(days=7)),
            now=lambda: NOW,
        ),
        categories=CategoryScopes(projects={}, registered=lambda project: True),
        now=lambda: NOW,
        channels=(),
        messages=GraphChannelFetch(
            client=client, instance=INSTANCE, reach_back=timedelta(days=7), now=lambda: NOW
        ),
        chats=(ChatListing(chat=CHAT, scope=PERSONAL, label=LABEL),),
        chat_messages=GraphChatFetch(
            client=client, instance=INSTANCE, reach_back=timedelta(days=7), now=lambda: NOW
        ),
    )
    cursor = Cursor(
        json.dumps(
            {"calendar": None, "channels": {}, "chats": {CHAT: POSITION.isoformat()}}
        ).encode()
    )
    return connector.harvest(cursor)


def _messages() -> list[dict]:
    return [row for row in ROWS if row["messageType"] == MESSAGE_TYPE and not row["deletedDateTime"]]


def test_the_message_rows_are_events_the_call_events_skipped_and_nothing_refused():
    result = _harvest(Recorded(PAGE))

    assert result.failure is None
    assert result.refusals == ()
    assert result.outcome is HarvestOutcome.HARVESTED
    assert sorted(message_events(result)) == sorted(row["id"] for row in _messages())
    # Of 20 rows, 4 are messages; the other 16 are call and recording events.
    assert len(_messages()) == 4


def test_call_events_arrive_as_unknown_future_value_and_are_skipped():
    events = [row for row in ROWS if row["messageType"] != MESSAGE_TYPE]
    assert {row["messageType"] for row in events} == {"unknownFutureValue"}
    assert {row["body"]["content"] for row in events} == {"<systemEventMessage/>"}
    assert {row["eventDetail"]["@odata.type"] for row in events} == {
        "#microsoft.graph.callStartedEventMessageDetail",
        "#microsoft.graph.callEndedEventMessageDetail",
        "#microsoft.graph.callRecordingEventMessageDetail",
        "#microsoft.graph.callTranscriptEventMessageDetail",
    }
    for row in events:
        assert read_message(row, reply_to=None) is None


def test_the_request_filters_and_sorts_on_last_modified_and_the_next_link_is_followed():
    transport = Recorded(PAGE)
    _harvest(transport)
    first, second = transport.resource_requests()

    parts = urlsplit(first)
    assert parts.path.endswith(f"/chats/{CHAT}/messages")
    query = parse_qs(parts.query)
    assert query["$top"] == ["50"]
    assert query["$orderby"] == ["lastModifiedDateTime desc"]
    # The position, less the chat walk's one millisecond of slack.
    assert query["$filter"] == ["lastModifiedDateTime gt 2026-09-30T23:59:59.999Z"]
    assert second == PAGE["@odata.nextLink"]


def test_each_message_is_cited_in_the_chats_scope_from_its_sender_at_its_creation():
    events = message_events(_harvest(Recorded(PAGE)))
    for row in _messages():
        event = events[row["id"]]
        assert str(event.source_ref) == f"graph:personal:message:{CHAT}/{row['id']}"
        assert event.scope == PERSONAL
        assert str(event.actor) == f"graph:{row['from']['user']['id']}"
        assert event.occurred_at == graph_time(row["createdDateTime"])
        assert event.payload.channel == LABEL


def test_mentions_become_graph_handles_of_the_users_mentions_names():
    events = message_events(_harvest(Recorded(PAGE)))
    mentioning = 0
    for row in _messages():
        users = mentioned_users(row)
        payload = events[row["id"]].payload
        assert isinstance(payload, MessagePayload)
        assert payload.mentions == tuple(f"graph:{user}" for user in users)
        mentioning += bool(users)
    assert mentioning == 1


def test_an_html_body_has_no_tag_left_and_a_text_body_is_taken_as_is():
    kinds: dict[str, int] = {}
    for row in _messages():
        parsed = read_message(row, reply_to=None)
        assert parsed is not None
        kind = row["body"]["contentType"]
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind == "html":
            assert tags_left(parsed.text, tag_names(row["body"]["content"])) == [], row["id"]
        else:
            assert kind == "text"
            assert parsed.text == row["body"]["content"]
    assert kinds == {"html": 3, "text": 1}


def test_a_body_of_divs_and_a_break_reads_as_its_text():
    """`1157654938648`: one `<div>` holding a `<br>`, a newline and an `&nbsp;`.

    Read by hand: the `<br>` is a line break, the newline after it is
    character data and stays, `&nbsp;` is U+00A0.
    """
    (row,) = [r for r in ROWS if r["id"] == "1157654938648"]
    parsed = read_message(row, reply_to=None)
    assert parsed is not None
    assert parsed.text == (
        "аак люфшс . daeodpnml джаглаз лжалфпяом чвниуежк чкге врчсзмоз . \n\n"
        "цзисттжт тшн \xa0кххшюиунс сеоа , лцжис вегн, ачопумфлс шкч."
    )


def test_the_rows_arrive_newest_modification_first_and_the_position_moves_to_the_newest_created():
    modified = [graph_time(row["lastModifiedDateTime"]) for row in ROWS]
    assert modified == sorted(modified, reverse=True)
    newest = max(graph_time(row["createdDateTime"]) for row in _messages())
    result = _harvest(Recorded(PAGE))
    stored = json.loads(result.cursor.token.decode())["chats"][CHAT.casefold()]
    assert datetime.fromisoformat(stored) == newest


# ── Story 23f: the names reach the dashboard; the handles stay the identity ──


def test_the_senders_name_is_taken_from_from_user():
    events = message_events(_harvest(Recorded(PAGE)))
    for row in _messages():
        payload = events[row["id"]].payload
        assert isinstance(payload, MessagePayload)
        assert payload.sender_name == row["from"]["user"]["displayName"]


def test_a_harvest_then_the_dashboard_shows_names_keeps_handles_and_marks_the_pm(tmp_path):
    """Acceptance (`23f`): names on the signal lines, `actor=graph:<user id>` on
    the log lines, and a mention of the PM still marked."""
    (mention,) = [row for row in _messages() if mentioned_users(row)]
    pm = mentioned_users(mention)[0]

    dashboard, log = harvest_then_dashboard(
        tmp_path,
        message_events(_harvest(Recorded(PAGE))).values(),
        scope=PERSONAL,
        pm_user_id=pm,
        now=NOW,
    )

    lines = signal_lines(dashboard)
    assert len(lines) == len(_messages()) == 4
    sent = graph_time(mention["createdDateTime"]).strftime("%Y-%m-%d %H:%M")
    assert lines[0].startswith(
        f"- **{sent}** {mention['from']['user']['displayName']} in {LABEL} — "
    )
    assert lines[0].endswith("— mentions you")
    assert not any(line.endswith("— mentions you") for line in lines[1:])
    rest = sorted(
        (row for row in _messages() if row is not mention),
        key=lambda row: graph_time(row["createdDateTime"]),
        reverse=True,
    )
    for line, row in zip(lines[1:], rest, strict=True):
        when = graph_time(row["createdDateTime"]).strftime("%Y-%m-%d %H:%M")
        assert line.startswith(f"- **{when}** {row['from']['user']['displayName']} in {LABEL}")
    for row in _messages():
        assert f"actor=graph:{row['from']['user']['id']}" in log
    assert "actor=Person" not in log
    assert "graph:" not in "\n".join(lines)
