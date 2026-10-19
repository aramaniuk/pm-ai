"""Story 33d over a real tenant's page: `fixtures/graph/channel_messages_recorded.json`.

The page was captured on 2026-10-15 through pm-ai's own Graph client and
redacted; its `_provenance` says how, and what was trimmed. The hand-written
`channel_messages.json` beside it stays: the matrix tests in
`test_graph_channel_messages.py` assert values written into it.

Here the recorded page is handed to a scripted transport and read by the real
walk (`channels.py`) and the real event mapping (`GraphConnector`). What each
test expects is worked out from the page itself — which rows Microsoft typed
`message`, which ids its `mentions` name, what the markup says — not from
pm-ai's own readers.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from pm_ai.connectors.graph import CategoryScopes, ChannelListing, GraphConnector
from pm_ai.connectors.graph.calendar import GraphCalendarFetch, WindowPolicy
from pm_ai.connectors.graph.channels import (
    MESSAGE_TYPE,
    THREAD_HORIZON,
    GraphChannelFetch,
    read_message,
)
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

PAGE = recorded("channel_messages_recorded.json")["page"]
THREADS: list[dict] = PAGE["value"]

PROJECT = "alpha"
LABEL = "Recorded channel"
TEAM = THREADS[0]["channelIdentity"]["teamId"]
CHANNEL = THREADS[0]["channelIdentity"]["channelId"]
NOW = datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)
"""The day after the capture: no message on the page is in the future."""
BEFORE_ALL = datetime(2025, 1, 1, tzinfo=timezone.utc)
"""A stored position older than every message on the page, so all are in range."""

MEETING_THREAD = next(t for t in THREADS if "replies@odata.nextLink" in t)
"""The meeting thread: 200 inline replies, a link for more, created 2026-08-17."""


# ── Reading the page, independently of pm-ai ─────────────────────────────────


def _rows() -> list[dict]:
    """Every root and every inline reply, in the order the page holds them."""
    rows: list[dict] = []
    for thread in THREADS:
        rows.append(thread)
        rows.extend(thread.get("replies") or [])
    return rows


def _row(message_id: str) -> dict:
    (found,) = [row for row in _rows() if row["id"] == message_id]
    return found


def _is_message(row: dict) -> bool:
    return row["messageType"] == MESSAGE_TYPE and not row["deletedDateTime"]


def _latest_reply(thread: dict) -> datetime | None:
    stamps = [graph_time(reply["createdDateTime"]) for reply in thread["replies"]]
    return max(stamps) if stamps else None


# ── The walk ─────────────────────────────────────────────────────────────────


def _harvest(transport: Recorded, *, position: datetime = BEFORE_ALL):
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
        channels=(ChannelListing(team=TEAM, channel=CHANNEL, scope=DataScope(ScopeKind.PROJECT, project_id=PROJECT), label=LABEL),),
        messages=GraphChannelFetch(
            client=client, instance=INSTANCE, reach_back=timedelta(days=7), now=lambda: NOW
        ),
    )
    cursor = Cursor(
        json.dumps({"calendar": None, "channels": {CHANNEL: position.isoformat()}}).encode()
    )
    return connector.harvest(cursor)


def _whole_page():
    return _harvest(Recorded(PAGE))


# ── The page as recorded ─────────────────────────────────────────────────────


def test_every_message_row_is_an_event_and_only_the_long_thread_is_refused():
    """Roots and inline replies typed `message` are events; nothing raises."""
    expected = [row["id"] for row in _rows() if _is_message(row)]
    result = _whole_page()

    assert result.failure is None
    assert result.outcome is HarvestOutcome.HARVESTED
    assert sorted(message_events(result)) == sorted(expected)
    # 14 message roots and 48 inline replies; the other 3 roots and 187
    # replies are system events.
    assert len(expected) == 62
    (refusal,) = result.refusals
    assert refusal.identifier == f"{CHANNEL}/{MEETING_THREAD['id']}"


def test_the_walk_follows_the_pages_own_next_link():
    transport = Recorded(PAGE)
    _harvest(transport)
    first, second = transport.resource_requests()
    assert first.endswith(f"/channels/{CHANNEL}/messages?$top=50&$expand=replies")
    assert second == PAGE["@odata.nextLink"]


def test_every_reply_is_an_event_of_its_own_under_the_channels_reference():
    replies = [reply for thread in THREADS for reply in thread["replies"] if _is_message(reply)]
    assert len(replies) == 48
    events = message_events(_whole_page())
    for reply in replies:
        event = events[reply["id"]]
        assert str(event.source_ref) == f"graph:{PROJECT}:message:{CHANNEL}/{reply['id']}"
        assert event.occurred_at == graph_time(reply["createdDateTime"])


def test_replies_arrive_newest_first_in_every_thread():
    threads = [thread for thread in THREADS if len(thread["replies"]) > 1]
    assert MEETING_THREAD in threads and len(threads) == 6
    for thread in threads:
        created = [graph_time(reply["createdDateTime"]) for reply in thread["replies"]]
        assert created == sorted(created, reverse=True), thread["id"]


def test_system_events_are_skipped_without_a_refusal():
    """Members added or deleted at the top level, call events inside the meeting thread."""
    system = [row for row in _rows() if row["messageType"] != MESSAGE_TYPE]
    assert {row["messageType"] for row in system} == {"unknownFutureValue"}
    assert {row["body"]["content"] for row in system} == {"<systemEventMessage/>"}
    assert {row["eventDetail"]["@odata.type"] for row in system} == {
        "#microsoft.graph.membersAddedEventMessageDetail",
        "#microsoft.graph.membersDeletedEventMessageDetail",
        "#microsoft.graph.callStartedEventMessageDetail",
        "#microsoft.graph.callEndedEventMessageDetail",
        "#microsoft.graph.callRecordingEventMessageDetail",
        "#microsoft.graph.callTranscriptEventMessageDetail",
    }
    result = _whole_page()
    events = message_events(result)
    for row in system:
        assert row["id"] not in events
        assert read_message(row, reply_to=None) is None
    assert all("more replies" in refusal.reason for refusal in result.refusals)


def test_a_thread_past_the_inline_replies_is_read_as_far_as_it_arrived_and_refused():
    """33d: what arrived inline is read; the rest is named, not paged, and the
    run claims no coverage for the channel."""
    assert len(MEETING_THREAD["replies"]) == MEETING_THREAD["replies@odata.count"] == 200
    transport = Recorded(PAGE)
    result = _harvest(transport)

    (refusal,) = result.refusals
    assert refusal.identifier == f"{CHANNEL}/{MEETING_THREAD['id']}"
    assert "more replies than Microsoft returns inline (200 arrived" in refusal.reason
    events = message_events(result)
    inline = [reply["id"] for reply in MEETING_THREAD["replies"] if _is_message(reply)]
    assert len(inline) == 13 and all(reply in events for reply in inline)
    assert result.coverage is None
    assert MEETING_THREAD["replies@odata.nextLink"] not in transport.resource_requests()


def test_mentions_become_graph_handles_of_the_users_mentions_names():
    events = message_events(_whole_page())
    for row in _rows():
        if not _is_message(row):
            continue
        payload = events[row["id"]].payload
        assert isinstance(payload, MessagePayload)
        assert payload.mentions == tuple(f"graph:{user}" for user in mentioned_users(row))


def test_one_user_mentioned_once_per_word_of_their_name_is_one_handle():
    """Teams writes one `mentions[]` entry per word of a mentioned name, each
    naming the same user: here eight entries name three people."""
    row = _row("1122662798188")
    users = [m["mentioned"]["user"]["id"] for m in row["mentions"]]
    assert len(users) == 8 and len(set(users)) == 3
    payload = message_events(_whole_page())["1122662798188"].payload
    assert payload.mentions == tuple(f"graph:{user}" for user in dict.fromkeys(users))


def test_the_sender_is_the_graph_handle_of_from_user():
    events = message_events(_whole_page())
    for row in _rows():
        if _is_message(row):
            assert str(events[row["id"]].actor) == f"graph:{row['from']['user']['id']}"


def test_every_html_body_loses_its_tags_and_text_is_taken_as_is():
    """Read by the walk's own `read_message`, the whole text and not only the excerpt."""
    kinds: dict[str, int] = {}
    for row in _rows():
        if not _is_message(row):
            continue
        parsed = read_message(row, reply_to=None)
        assert parsed is not None
        kind = row["body"]["contentType"]
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind == "html":
            assert tags_left(parsed.text, tag_names(row["body"]["content"])) == [], row["id"]
        else:
            assert kind == "text"
            assert parsed.text == row["body"]["content"]
    assert kinds == {"html": 57, "text": 5}


# Expected text, read off the markup by hand under `plain_text`'s documented
# rules: tags dropped, character data kept (so the "\n" Teams writes between
# two paragraphs stays, beside the one break the paragraph boundary adds),
# entities decoded (`&nbsp;` is U+00A0), `<br>` a line break.
EXACT = {
    # <a> around the link text
    "1721038367876": "vccgwzin://qmwm.fraibvuld.ksaoksw",
    # <at> mentions joined by &nbsp;, then <code> inside paragraphs
    "1108552606844": (
        "Person 2\xa0Person 5\n\n"
        "шбюо шкняхп zsdhd.vnnfqsrdj чтлятси шлдлх янзцшлбни.\n\n"
        "пюи, фхб, новяэа, впожлш, мэкив меф еэ эфохж, аевсожи ванрсфсэч пхфз "
        "хвншацк: эфшгб ну ехпх хмн jzlxuov ючдре wq, чтд чидфх фвачдг иивючи.\n\n"
        "нхзежяо жн тчткпкц."
    ),
    # a <ul> of two <li> between paragraphs
    "1166976054104": (
        "нгквуэюк, пвчесжуэ екшвпто рссс дцб стнфбс.\n\n"
        "резклиб лдго бгф эе уцткяб:\n\n\n"
        "пибжсвзц хшфннжвжн дфжосх яфр 0-0 чз зм lycmdglns qznc, мрю ибтх исюхвэа "
        "вкцап, юу энишдктн далсэмчо юяижи дзчг\n"
        "ннтк чу ююзспц чмчзккэ чнмд хявплрх ичвоннду ююхинтжбб xpu. эфмфкж "
        "умдфэгду србиг мчз, клароря кпцс. мврбою, шэку чфрл. казиэ жпиф жрлнб "
        "мхжине, оррч чдрф цбвяфдчк фвужхуу гвэжб лэргмирцч нзр чмюч шзвюэсшпу "
        "япкд рзм зффэг еоштдгю.\n\n"
        "\xa0"
    ),
    # <s> strike-through, one span inside an <a>
    "1156379283453": (
        "qdhpjj ojs hmfonpigp yxirr va ktkyadm (fnulyzp 0) | mmpavflke scg cwl\n\n"
        "оргядимч гэц етмбрпшуа бом нгэфттш пдшцн ржлжхх юуюе юцпялюнг\n\n"
        "\xa0\n\n"
        "змх, озгюлюф :("
    ),
    # a trailing &nbsp;
    "1870299507765": "иэа цммжх ииахсярх эхэт миланц эбэвж фюк мшуткрум звбл?\xa0",
}


@pytest.mark.parametrize("message_id", sorted(EXACT))
def test_a_recorded_html_body_reads_as_its_text(message_id):
    row = _row(message_id)
    parsed = read_message(row, reply_to=None)
    assert parsed is not None
    assert parsed.text == EXACT[message_id]
    excerpt = message_events(_whole_page())[message_id].payload.excerpt
    assert excerpt == EXACT[message_id][:280]


def test_escaped_angle_brackets_are_decoded_to_text_not_read_as_a_tag():
    """`1273691970883` writes `&lt;lo&gt;` in a command line: it reads `<lo>`."""
    row = _row("1273691970883")
    assert '"lcnc-ckrldttt-raie: &lt;lo&gt;"' in row["body"]["content"]
    parsed = read_message(row, reply_to=None)
    assert parsed is not None
    assert '\xa0 -gjluswgxb "lcnc-ckrldttt-raie: <lo>" \\' in parsed.text


def test_an_attachment_only_body_is_an_event_with_no_excerpt():
    """`1662943698593`'s body is a lone `<attachment>` tag: no text, so no excerpt."""
    row = _row("1662943698593")
    assert row["body"]["content"].startswith("<attachment ")
    assert row["body"]["content"].endswith("></attachment>")
    assert message_events(_whole_page())["1662943698593"].payload.excerpt is None


def test_the_position_moves_to_the_newest_message_created():
    newest = max(graph_time(row["createdDateTime"]) for row in _rows() if _is_message(row))
    result = _whole_page()
    stored = json.loads(result.cursor.token.decode())["channels"][CHANNEL.casefold()]
    assert datetime.fromisoformat(stored) == newest


# ── What the recorded order means for the walk ───────────────────────────────


def test_threads_are_in_order_of_root_creation_not_of_their_latest_reply():
    """What the page shows, the evidence for story 33h's stop rule.

    Story 33d read Microsoft's reference as ordering threads by the latest
    activity on them. The recorded page is in order of each root's own
    `createdDateTime`, newest first. The meeting thread shows the difference:
    created 2026-08-17, with replies up to 2026-10-15, it sits behind a thread
    whose latest activity is two months older than its latest reply. Its
    root's `lastModifiedDateTime` (2026-10-15) is out of order too, so the page
    is not in root-modification order either.
    """
    created = [graph_time(thread["createdDateTime"]) for thread in THREADS]
    assert created == sorted(created, reverse=True)

    at = THREADS.index(MEETING_THREAD)
    before = THREADS[at - 1]
    latest = _latest_reply(MEETING_THREAD)
    assert latest is not None and latest.date().isoformat() == "2026-10-15"
    assert graph_time(MEETING_THREAD["createdDateTime"]).date().isoformat() == "2026-08-17"
    assert before["replies"] == []
    assert graph_time(before["lastModifiedDateTime"]).date().isoformat() == "2026-08-17"
    assert graph_time(before["lastModifiedDateTime"]) < graph_time(
        MEETING_THREAD["lastModifiedDateTime"]
    )


def test_a_fresh_reply_listed_behind_a_wholly_older_page_is_still_emitted():
    """The recorded threads in Microsoft's order, served as two pages (33h).

    Page one is the kept threads between the newest one and the meeting thread
    (created 2026-10-09 back to 2026-08-17, none touched after 2026-10-09); page
    two is the meeting thread. With the position at 2026-10-10, page one is
    wholly older than the position, but its oldest first post is inside the
    horizon, so the walk reads page two and emits the meeting thread's replies
    created after the position — and refuses that thread by name for the
    replies that did not arrive inline.
    """
    at = THREADS.index(MEETING_THREAD)
    older = THREADS[1:at]
    position = datetime(2026, 10, 10, tzinfo=timezone.utc)
    for thread in older:
        for row in [thread, *thread["replies"]]:
            assert graph_time(row["lastModifiedDateTime"]) < position
    assert min(graph_time(t["createdDateTime"]) for t in older) >= position - THREAD_HORIZON
    fresh = [
        reply["id"]
        for reply in MEETING_THREAD["replies"]
        if _is_message(reply) and graph_time(reply["createdDateTime"]) >= position
    ]
    assert len(fresh) == 3

    transport = Recorded(
        {"value": older, "@odata.nextLink": PAGE["@odata.nextLink"]},
        {"value": [MEETING_THREAD]},
    )
    result = _harvest(transport, position=position)

    assert len(transport.resource_requests()) == 2
    assert sorted(message_events(result)) == sorted(fresh)
    (refusal,) = result.refusals
    assert refusal.identifier == f"{CHANNEL}/{MEETING_THREAD['id']}"
    assert "more replies" in refusal.reason


def test_the_recorded_page_alone_reaches_past_the_horizon_in_one_request():
    """Position 2026-10-15T12:00Z: the page's oldest first post is more than a
    year old, before the horizon, so its next-page link is not followed."""
    position = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    oldest = min(graph_time(thread["createdDateTime"]) for thread in THREADS)
    assert oldest < position - THREAD_HORIZON
    assert "@odata.nextLink" in PAGE, "a next page exists to be left unread"
    transport = Recorded(PAGE)
    _harvest(transport, position=position)
    assert len(transport.resource_requests()) == 1


# ── Story 23f: the names reach the dashboard; the handles stay the identity ──


def test_the_senders_name_is_taken_from_from_user():
    """`from.user.displayName` beside the handle, for every message that has one."""
    events = message_events(_whole_page())
    sent = [
        row
        for row in _rows()
        if _is_message(row) and ((row["from"] or {}).get("user") or {}).get("displayName")
    ]
    named = 0
    for row in _rows():
        if not _is_message(row) or not (row["from"] or {}).get("user"):
            continue
        payload = events[row["id"]].payload
        assert isinstance(payload, MessagePayload)
        assert payload.sender_name == row["from"]["user"]["displayName"]
        named += payload.sender_name is not None
    assert sent and named == len(sent)


def test_a_harvest_then_the_dashboard_shows_names_keeps_handles_and_marks_the_pm(tmp_path):
    """Acceptance (`23f`): names on the signal lines, `actor=graph:<user id>` on
    the log lines, and a mention of the PM still marked."""
    (first,) = [row for row in _rows() if row["id"] == THREADS[0]["id"]]
    pm = mentioned_users(first)[0]
    mentioning = [row for row in _rows() if _is_message(row) and pm in mentioned_users(row)]
    names = {
        row["from"]["user"]["displayName"]
        for row in _rows()
        if _is_message(row) and (row["from"] or {}).get("user")
    } - {None}

    dashboard, log = harvest_then_dashboard(
        tmp_path,
        message_events(_whole_page()).values(),
        scope=DataScope(ScopeKind.PROJECT, project_id=PROJECT),
        pm_user_id=pm,
        now=NOW,
    )

    lines = signal_lines(dashboard)
    assert len(lines) == 20
    for line in lines:
        sender = line.split("** ", 1)[1].split(f" in {LABEL}", 1)[0]
        assert sender in names, line
    marked = [line for line in lines if line.endswith("— mentions you")]
    assert mentioning and len(mentioning) <= len(lines)
    assert lines[: len(marked)] == marked and len(marked) == len(mentioning)
    sent = graph_time(first["createdDateTime"]).strftime("%Y-%m-%d %H:%M")
    assert lines[0].startswith(
        f"- **{sent}** {first['from']['user']['displayName']} in {LABEL} — "
    )
    for row in _rows():
        if _is_message(row) and (row["from"] or {}).get("user"):
            assert f"actor=graph:{row['from']['user']['id']}" in log
    assert "actor=Person" not in log
    # Every line shown is a named sender (checked above), so no handle is shown.
    assert "graph:" not in "\n".join(lines)
