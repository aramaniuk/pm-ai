"""Story 33c — one test per matrix row, against `33b`'s own row fixtures.

**No test opens a socket, and the fixtures are what make that structural.** Every
row here starts as a `calendarView` JSON body handed to a scripted
`GraphClient.transport`, so what is exercised is the whole path — provider
payload, `33b`'s conversion, this slice's modelling — rather than a `CalendarRow`
hand-built past the half of the pipeline that could be wrong. A test that forgot
to inject a transport would reach `_urllib_transport` and try to resolve
`graph.microsoft.com`, which fails rather than passing quietly.

The payload **shapes** are slice 0's, measured against a live tenant on
2026-09-06; the **values** are written here, for the reason
`test_graph_calendar_fetch.py` states at length — slice 0 ran against a real
workplace tenant and this repository is public, so no captured body exists to
load.

The clock is frozen at `NOW` and moves only where a test moves it. That is what
lets the past/future split be asserted exactly: "ended" is a comparison against
this instant, and a clock that ticked while the fixture answered would make the
boundary row a matter of how many pages the fake happened to serve.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pm_ai.app import wiring
from pm_ai.app.pipelines import run_harvest
from pm_ai.app.wiring import _registrar, build
from pm_ai.connectors.graph import (
    CATEGORIES_KEY,
    CLIENT_ID_KEY,
    MAX_SETTING_MINUTES,
    REACH_BACK_KEY,
    TENANT_KEY,
    WIDTH_KEY,
    CategoryScopes,
    GraphConnector,
    GraphRowRefused,
    MissingGraphSetting,
    UnknownProject,
    graph_category_scopes,
    graph_window_policy,
)
from pm_ai.connectors.graph.auth import GraphDeviceCodeAuth
from pm_ai.connectors.graph.calendar import GraphCalendarFetch, WindowPolicy
from pm_ai.connectors.graph.client import GRAPH_BASE, GraphClient, GraphRequest, GraphResponse
from pm_ai.connectors.registry import all_connectors
from pm_ai.core.config import ACCEPTED_KEYS
from pm_ai.core.connector_enrolment import enrol_connector
from pm_ai.core.meeting_records import (
    MEETINGS,
    NOTES_HEADING,
    MalformedMeeting,
    as_stored,
    record_name,
)
from pm_ai.domain.events import ObservedEventType, Provenance
from pm_ai.domain.harvest import Cursor, HarvestOutcome
from pm_ai.domain.health import Health, Probe
from pm_ai.domain.identity import UNRESOLVED_ACTOR, DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import ConnectorPort

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
"""This machine's clock, frozen, for every test in this file."""

INSTANCE = "graph:work"
CLIENT_ID = "00000000-0000-0000-0000-000000000001"
WIDTH = timedelta(hours=24)
REACH_BACK = timedelta(days=7)
"""The two widths the 2026-09-08 spec change log settles for this machine.

Above CAP-2's 240-minute floor, and reach-back at or above width, so
`WindowPolicy.__post_init__` accepts both.
"""

APPLICATION = DataScope(ScopeKind.APPLICATION)
PERSONAL = DataScope(ScopeKind.PERSONAL)
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")


# ── The provider's own shapes ────────────────────────────────────────────────


def _zoned(moment: datetime) -> dict:
    """One `{dateTime, timeZone}` pair, with Graph's seven fractional digits.

    Sent naive with the zone beside it, which is the shape slice 0 measured and
    the one whose silent failure — `fromisoformat` returning `tzinfo=None` —
    `33b` exists to convert away.
    """
    return {
        "dateTime": (
            f"{moment.replace(tzinfo=None, microsecond=0).isoformat()}"
            f".{moment.microsecond:06d}0"
        ),
        "timeZone": "UTC",
    }


ICAL_UID = "040000008200E00074C5B7101A82E00807D0B0F2A1B3DC01000000000000000010000000"
"""One organiser's `iCalUId`, in the shape Graph sends — a long hex string.

Deliberately unlike the `id` beside it, because that is the whole point of the
field: `id` is per-mailbox and this is not, so a fixture where the two were
similar would let a mapping that returned the wrong one look right.
"""


def _event(start: datetime, end: datetime, **overrides) -> dict:
    """One `calendarView` event in slice 0's measured shape."""
    event = {
        "id": "AAMkAGI2-single",
        "iCalUId": ICAL_UID,
        "subject": "Sprint review",
        "start": _zoned(start),
        "end": _zoned(end),
        "isAllDay": False,
        "isCancelled": False,
        "type": "singleInstance",
        "seriesMasterId": None,
        "occurrenceId": None,
        "categories": [],
        "organizer": {"emailAddress": {"name": "A Person", "address": "a@example.invalid"}},
        "attendees": [
            {
                "type": "required",
                "status": {"response": "accepted", "time": "2026-09-01T09:00:00Z"},
                "emailAddress": {"name": "B Person", "address": "b@example.invalid"},
            }
        ],
        "responseStatus": {"response": "accepted", "time": "2026-09-01T09:00:00Z"},
    }
    event.update(overrides)
    return event


def _ended(**overrides) -> dict:
    """A meeting that finished yesterday."""
    return _event(NOW - timedelta(days=1, hours=1), NOW - timedelta(days=1), **overrides)


def _upcoming(**overrides) -> dict:
    """A meeting that starts in three hours."""
    return _event(NOW + timedelta(hours=3), NOW + timedelta(hours=4), **overrides)


def _attendee(address: str | None, *, response: str = "accepted") -> dict:
    entry: dict = {"type": "required", "status": {"response": response, "time": None}}
    entry["emailAddress"] = None if address is None else {"address": address, "name": address}
    return entry


def _page(*events: dict) -> GraphResponse:
    return GraphResponse(
        status=200, body={"value": list(events)}, headers={"Content-Type": "application/json"}
    )


class Transport:
    """A scripted transport: recorded requests in, queued responses out."""

    def __init__(self, *script: GraphResponse) -> None:
        self.script = list(script)
        self.requests: list[GraphRequest] = []

    def __call__(self, request: GraphRequest) -> GraphResponse:
        self.requests.append(request)
        if not self.script:
            raise AssertionError(
                f"the fetch asked for a page the fixture does not have: {request!r}"
            )
        return self.script.pop(0)


class Auth:
    """As much of `33a`'s adapter as the client and the health probe touch."""

    def __init__(self, probe: Probe | None = None, raises: Exception | None = None) -> None:
        self.probe = probe or Probe(INSTANCE, Health.OK, "graph answered")
        self.raises = raises

    def access_token(self, *, force_refresh: bool = False) -> str:
        return "token-1"

    def check_health(self) -> Probe:
        if self.raises is not None:
            raise self.raises
        return self.probe


def _connector(
    *events: dict,
    categories: dict[str, str] | None = None,
    registered=lambda project: project == "alpha",
    auth: Auth | None = None,
    now: datetime = NOW,
) -> GraphConnector:
    """One wired connector over a single scripted page."""
    clock = lambda: now
    identity = auth or Auth()
    return GraphConnector(
        auth=identity,
        calendar=GraphCalendarFetch(
            client=GraphClient(auth=identity, transport=Transport(_page(*events)), now=clock),
            instance=INSTANCE,
            windows=WindowPolicy(width=WIDTH, first_run_reach_back=REACH_BACK),
            now=clock,
        ),
        categories=CategoryScopes(projects=categories or {}, registered=registered),
        now=clock,
    )


def _harvest(*events: dict, **options):
    return _connector(*events, **options).harvest(Cursor())


# ── The past/future split ────────────────────────────────────────────────────


def test_an_upcoming_row_is_mapped_and_returned_and_nothing_is_written():
    """Matrix: starts in 3h → a `Meeting` handed back, no record, no event."""
    result = _harvest(_upcoming())

    assert result.records == ()
    assert result.events == ()
    assert len(result.live) == 1
    assert result.live[0].start == NOW + timedelta(hours=3)
    assert result.outcome is HarvestOutcome.HARVESTED, (
        "rows came back, so the provider answered with something — EMPTY would "
        "say the calendar was empty, which is the one thing it was not."
    )


def test_an_ended_row_emits_the_held_event_with_its_count_and_duration():
    """Matrix: finished yesterday → `CALENDAR_EVENT_HELD`."""
    result = _harvest(_ended())

    (event,) = result.events
    assert event.type is ObservedEventType.CALENDAR_EVENT_HELD
    assert event.payload.attendee_count == 1
    assert event.payload.duration_minutes == 60
    assert event.occurred_at == NOW - timedelta(days=1, hours=1)
    (record,) = result.records
    assert record.meeting_id == "AAMkAGI2-single"
    assert result.live == ()


def test_a_meeting_in_progress_has_not_been_held_yet():
    """Matrix: started, not ended → mapped and returned, nothing written."""
    result = _harvest(_event(NOW - timedelta(minutes=30), NOW + timedelta(minutes=30)))

    assert result.records == ()
    assert result.events == ()
    assert len(result.live) == 1


def test_the_end_instant_belongs_to_the_ended_side_only():
    """Matrix: `end` exactly equals `now` → the two states are disjoint.

    Asserted as a pair rather than as one row, because "inclusive on one side"
    is a claim about both sides: a bound that is inclusive on neither loses the
    meeting for one clock tick, and one inclusive on both would make a row
    simultaneously held and in progress.
    """
    ended = _harvest(_event(NOW - timedelta(hours=1), NOW))
    assert len(ended.records) == 1 and ended.live == ()

    running = _harvest(_event(NOW - timedelta(hours=1), NOW + timedelta(microseconds=1)))
    assert running.records == () and len(running.live) == 1


# ── Recurrence ───────────────────────────────────────────────────────────────


def test_each_occurrence_of_a_series_is_its_own_meeting():
    """Matrix: a weekly recurrence in the window → the ended ones are each a record."""
    result = _harvest(
        _ended(id="AAMk-occ-1", type="occurrence", seriesMasterId="AAMk-master"),
        _event(
            NOW - timedelta(days=8, hours=1),
            NOW - timedelta(days=8),
            id="AAMk-occ-2",
            type="occurrence",
            seriesMasterId="AAMk-master",
        ),
        _upcoming(id="AAMk-occ-3", type="occurrence", seriesMasterId="AAMk-master"),
    )

    assert sorted(record.meeting_id for record in result.records) == [
        "AAMk-occ-1",
        "AAMk-occ-2",
    ]
    assert [meeting.meeting_id for meeting in result.live] == ["AAMk-occ-3"]


def test_a_modified_occurrence_is_keyed_on_its_own_id_and_not_the_masters():
    """Matrix: one instance moved, with its own id."""
    result = _harvest(
        _ended(id="AAMk-exception", type="exception", seriesMasterId="AAMk-master")
    )

    (record,) = result.records
    assert record.meeting_id == "AAMk-exception"
    assert record.calendar_event_ref == "AAMk-exception"
    assert str(result.events[0].source_ref) == "meeting:AAMk-exception", (
        "every fact extracted from the moved instance cites the instance. Keying "
        "on the series master would file them all against one meeting and make "
        "the move invisible."
    )


# ── Rows that are not the PM's meeting ───────────────────────────────────────


def test_a_cancelled_upcoming_row_is_neither_returned_nor_written():
    """Matrix: marked cancelled, still in the window."""
    result = _harvest(_upcoming(isCancelled=True))

    assert result.records == () and result.live == () and result.events == ()
    assert result.outcome is HarvestOutcome.EMPTY, (
        "nothing was mapped and nothing was refused, so this run learned no "
        "meeting — and EMPTY is what withholds the coverage claim below."
    )
    assert result.coverage is None


def test_a_row_cancelled_after_it_ended_keeps_its_record():
    """Matrix: an ended row, later marked cancelled → the record stands.

    A meeting that happened is not undone by a later calendar edit, and its
    citations must keep resolving. Suppression applies to the future half only,
    which is also why there is no cancellation-marking rewrite to get wrong.
    """
    result = _harvest(_ended(isCancelled=True))

    assert len(result.records) == 1
    assert len(result.events) == 1


def test_a_declined_row_is_not_written():
    """Matrix: declined, still on the calendar.

    Not handed back either, and that is this slice's reading of "not written":
    a dashboard showing a meeting its reader has declined is showing them
    somebody else's calendar.
    """
    result = _harvest(
        _ended(responseStatus={"response": "declined", "time": None}),
        _upcoming(id="AAMk-2", responseStatus={"response": "declined", "time": None}),
    )

    assert result.records == () and result.live == () and result.events == ()


@pytest.mark.parametrize(
    "response",
    ["organizer", "notResponded", "none", "accepted", "tentativelyAccepted", None, "somethingNew"],
)
def test_every_response_but_declined_is_a_meeting_the_pm_has(response):
    """The other five members of the enumeration, plus what falls off the end.

    `declined` and `tentativelyAccepted` were the only two the code named, and
    `organizer`, `notResponded` and `none` fell through undocumented and
    untested — as does an absent field and any value Graph adds later. Every one
    of them is kept, and keeping is the safe direction: a meeting the PM can see
    and correct, rather than one nobody knows is missing. `organizer` in
    particular is the PM's own meeting and the strongest case there is.
    """
    row = _ended(
        responseStatus=None if response is None else {"response": response, "time": None}
    )
    result = _harvest(row)

    assert len(result.records) == 1
    assert len(result.events) == 1
    assert result.records[0].tentative is (response == "tentativelyAccepted"), (
        "and only one of them rides out as tentative"
    )


# ── Scope ────────────────────────────────────────────────────────────────────


def test_a_mapped_category_files_the_record_in_that_projects_tree():
    """Matrix: its Outlook category is in the mapping."""
    result = _harvest(_ended(categories=["Platform"]), categories={"Platform": "alpha"})

    (record,) = result.records
    assert record.scope == PROJECT
    assert result.events[0].scope == PROJECT


def test_an_untagged_row_is_personal():
    """Matrix: any untagged meeting → personal scope, and nothing is dropped."""
    result = _harvest(_ended(categories=[]))

    (record,) = result.records
    assert record.scope == PERSONAL


def test_a_category_is_matched_whatever_case_the_pm_typed_it_in():
    """A category is display text the PM types; two cases are one tag."""
    result = _harvest(_ended(categories=["platform"]), categories={"Platform": "alpha"})

    assert result.records[0].scope == PROJECT


def test_a_category_naming_an_unregistered_project_is_refused_by_name():
    """Matrix: a stale mapping entry → `UnknownProject`.

    Refused when the mapping is built rather than mid-harvest: a connector
    reports and never raises, and by the time the resolver would refuse there is
    a batch in hand.
    """
    with pytest.raises(UnknownProject) as refused:
        CategoryScopes(projects={"Platform": "retired"}, registered=lambda p: p == "alpha")

    assert "Platform" in str(refused.value) and "retired" in str(refused.value), (
        "only the pair says what to repair — the tag in Outlook, or the id it "
        "points at"
    )


def test_two_categories_differing_only_in_case_are_refused_rather_than_merged():
    """One tag with two answers, and there is no correct guess between them.

    Matching is casefolded, so `Platform` and `platform` are one tag — and the
    mapping used to keep whichever entry it iterated to last. Every Platform
    meeting then lands in one project and the other project's meetings are
    silently gone; if the winner is a committed tree that is the AD-38 leak
    `CategoryScopes` exists to prevent, arriving through `CategoryScopes`.
    """
    with pytest.raises(UnknownProject) as refused:
        CategoryScopes(
            projects={"Platform": "alpha", "platform": "beta"},
            registered=lambda project: True,
        )

    said = str(refused.value)
    assert "Platform" in said and "platform" in said
    assert "alpha" in said and "beta" in said, (
        "both destinations, because the operator has to know which meetings were "
        "going where before they delete a line"
    )


def test_two_spellings_of_one_tag_at_one_project_are_a_redundant_line_and_allowed():
    """The collision that is not an ambiguity, so there is nothing to refuse."""
    scopes = CategoryScopes(
        projects={"Platform": "alpha", "PLATFORM": "alpha"},
        registered=lambda project: project == "alpha",
    )

    assert scopes.scope_for(("platform",)) == PROJECT


def test_a_category_key_that_is_not_a_string_is_refused_rather_than_stringified():
    """`str(key)` made the guard below unreachable and invented a tag.

    A `None` key arrived at `CategoryScopes` as the literal Outlook category
    `"None"` — a tag no PM can type, so the entry matched nothing and the mapping
    looked like one that worked. The coercion carried a blanket
    `# type: ignore[misc]` that was also hiding the coercion.
    """
    with pytest.raises(UnknownProject) as refused:
        graph_category_scopes(
            {CATEGORIES_KEY: {None: "alpha"}}, registered=lambda project: True
        )

    assert "where an Outlook category belongs" in str(refused.value)


def test_the_registered_predicate_answers_no_to_every_resolver_fault():
    """Every fault is "no" to the question this predicate asks.

    It caught `ScopeResolutionError` alone, which is the family `ScopePathPort`
    promises and not the family a resolver can produce: `repository` touches the
    filesystem. An unexpected one propagated through
    `CategoryScopes.__post_init__` and out of `build()`, taking down `pm-ai
    doctor` — the command for a machine whose project registry is broken.
    """

    class _UnreadableRegistry:
        def repository(self, project_id: str):
            raise OSError("the project registry cannot be read")

    assert _registrar(_UnreadableRegistry())("alpha") is False


def test_a_resolver_fault_refuses_the_mapping_by_name_and_the_daemon_still_boots(
    tmp_path, capsys
):
    """And the refusal the operator reads is the mapping's, not the fault's.

    `_graph_connector` catches broadly enough that a resolver fault would leave
    the daemon composing either way — this asserts the *message*, which is what
    separates a predicate that answered from a handler that mopped up. One says
    which category to repair; the other says `OSError`.
    """

    class _UnreadableFor:
        """A real resolver, except that one project id cannot be looked up."""

        def __init__(self, inner: ScopePaths, *, broken: str) -> None:
            self._inner = inner
            self._broken = broken

        def __getattr__(self, name: str):
            return getattr(self._inner, name)

        def repository(self, project_id: str):
            if project_id == self._broken:
                raise OSError("the project registry cannot be read")
            return self._inner.repository(project_id)

    paths = _UnreadableFor(ScopePaths.rooted(tmp_path), broken="platform-team")
    _enrol(
        build(None, "alpha", paths=paths, now=lambda: NOW),
        **{CATEGORIES_KEY: {"Platform": "platform-team"}},
    )

    daemon = build(None, "alpha", paths=paths, now=lambda: NOW)

    assert INSTANCE not in daemon.connectors
    said = capsys.readouterr().err
    assert "Platform" in said and "platform-team" in said
    assert "OSError" not in said


# ── Duration, attendees, and what the cost comes out at ──────────────────────


def test_an_all_day_row_records_zero_minutes_and_costs_nothing():
    """Matrix: midnight to midnight → `duration_minutes` is `0`.

    The cost is asserted as a **value**. "Equals the stated convention" was
    satisfied by any constant the implementer wrote down, including the 1440 the
    spec calls wrong by an order of magnitude: five attendees at £100/h would
    report £12,000 for a birthday.
    """
    midnight = datetime(2026, 9, 6, tzinfo=timezone.utc)
    result = _harvest(
        _event(
            midnight,
            midnight + timedelta(days=1),
            isAllDay=True,
            attendees=[_attendee(f"person{n}@example.invalid") for n in range(5)],
        )
    )

    (record,) = result.records
    assert record.duration_minutes == 0
    assert record.man_hour_cost(100.0) == 0.0
    assert result.events[0].payload.duration_minutes == 0
    assert result.events[0].payload.attendee_count == 5, (
        "the event carries what the provider sent; the record carries who could "
        "be named"
    )


def test_a_null_email_address_resolves_to_the_unresolved_actor():
    """Matrix: null `emailAddress` → `UNRESOLVED`, never the raw handle."""
    result = _harvest(_ended(attendees=[_attendee(None)]))

    (record,) = result.records
    assert [actor.actor_id for actor in record.attendees] == [UNRESOLVED_ACTOR]


def test_a_distribution_list_is_one_attendee_entry_and_is_counted_as_one():
    """Matrix: group expansion stated.

    `calendarView` does not expand a group, so pm-ai cannot know how many people
    the list names. One entry is counted as one, which under-counts visibly
    rather than inventing a number.
    """
    result = _harvest(_ended(attendees=[_attendee("everyone@example.invalid")]))

    assert result.events[0].payload.attendee_count == 1


def test_zero_attendees_is_recorded_as_zero():
    """Matrix: a room booking really has none, and the organizer is not one."""
    result = _harvest(_ended(attendees=[]))

    (record,) = result.records
    assert record.attendees == ()
    assert result.events[0].payload.attendee_count == 0
    assert record.man_hour_cost(100.0) == 0.0


def test_attendees_pm_ai_cannot_name_collapse_onto_one_actor():
    """The loss `11a`'s grammar forces, asserted rather than left to be found.

    A record refuses a repeated handle — one person listed twice is two people
    in the Man-Hour Cost — and every unresolvable handle is the same
    `UNRESOLVED` actor. So five strangers are one attendee in the record, and the
    event carries the provider's own count beside it.
    """
    result = _harvest(
        _ended(attendees=[_attendee(f"stranger{n}@example.invalid") for n in range(5)])
    )

    (record,) = result.records
    assert [actor.actor_id for actor in record.attendees] == [UNRESOLVED_ACTOR]
    assert result.events[0].payload.attendee_count == 5


def test_a_room_booking_with_no_join_reference_still_carries_the_event_id():
    """Matrix: no online meeting → record written, `calendar_event_ref` set."""
    result = _harvest(_ended(id="AAMk-room", attendees=[]))

    (record,) = result.records
    assert record.calendar_event_ref == "AAMk-room"


# ── iCalUId ──────────────────────────────────────────────────────────────────


def test_the_ical_uid_rides_the_row_and_the_meeting_and_reaches_no_file(tmp_path):
    """`23b`'s dedup key, end to end from the payload to the bytes on disk.

    Three hops, because a break in any one of them is silent: the field has no
    effect at all until two calendars are read at once, and a slice reading one
    tenant would go on passing with `ical_uid` hard-wired to `None`.

    The last assertion is `tentative`'s exactly — "not stored" and "stored as
    empty" read alike from the caller, so it is made on the record's bytes.
    """
    (row,) = _connector(_upcoming()).calendar.fetch(since=None).rows
    assert row.ical_uid == ICAL_UID, "the row carries what the payload sent"
    assert row.event_id != row.ical_uid, (
        "and it is not the per-mailbox `id`, which is the identifier it exists "
        "to be different from"
    )

    (meeting,) = _harvest(_upcoming()).live
    assert meeting.ical_uid == ICAL_UID, "and the mapping carries it onto the Meeting"

    (record,) = _harvest(_ended()).records
    assert record.ical_uid == ICAL_UID

    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    daemon.meetings.put(record)
    written = daemon.storage.read_artifact(
        scope=PERSONAL,
        artifact=MEETINGS,
        name=record_name(record.meeting_id, record.start),
    )
    assert b"ical_uid" not in written
    assert ICAL_UID.encode("utf-8") not in written, (
        "the field is carried in memory so two copies of one meeting can be "
        "matched during a single read of the day; no record read back off disk "
        "is ever asked that question"
    )
    assert daemon.meetings.get(record.meeting_id, scope=PERSONAL).meeting == as_stored(
        record
    )


@pytest.mark.parametrize("sent", [None, "", "   ", 7])
def test_a_row_without_a_usable_ical_uid_carries_no_uid_at_all(sent):
    """Absence stays absence, which is what makes the key safe to match on.

    A blank or missing `iCalUId` collapsing to `""` would make every such
    meeting equal to every other, and `23b` would merge two unrelated
    appointments into one line on the day the provider stopped sending it.
    """
    (meeting,) = _harvest(_upcoming(iCalUId=sent)).live
    assert meeting.ical_uid is None


# ── Tentative ────────────────────────────────────────────────────────────────


def test_tentative_rides_the_returned_meeting_and_reaches_no_file(tmp_path):
    """Matrix: tentatively accepted → carried for display, stored nowhere.

    Asserted on the record's **bytes**, because "not stored" and "stored as
    false" read alike from the caller.
    """
    result = _harvest(
        _upcoming(responseStatus={"response": "tentativelyAccepted", "time": None})
    )
    (meeting,) = result.live
    assert meeting.tentative is True

    # The same row, ended, so there is a file to look at at all.
    held = _harvest(_ended(responseStatus={"response": "tentativelyAccepted", "time": None}))
    (record,) = held.records
    assert record.tentative is True

    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    daemon.meetings.put(record)
    written = daemon.storage.read_artifact(
        scope=PERSONAL,
        artifact=MEETINGS,
        name=record_name(record.meeting_id, record.start),
    )
    assert b"tentative" not in written, (
        "a response status is an answer about a meeting that has not occurred, "
        "and every record is of one that has"
    )
    assert daemon.meetings.get(record.meeting_id, scope=PERSONAL).meeting == as_stored(
        record
    ), (
        "and the round trip stays a round trip: `as_stored` clears the flag, so "
        "a meeting a connector marked does not read back as a render/parse defect"
    )


# ── What a connector may assert ──────────────────────────────────────────────


def test_every_emitted_event_leaves_the_connector_unattributed():
    """AD-36 — a connector may never assert `EXTERNAL`.

    Asserted rather than left to a comment, because hard-coding it would make
    pm-ai's own writes admissible as evidence that its own promises were kept —
    and the AD-34 gate inspects only the absent `id`.
    """
    result = _harvest(_ended(), _ended(id="AAMk-2"))

    assert result.events
    for event in result.events:
        assert event.authored_by is Provenance.UNKNOWN
        assert not event.authored_by.admissible_as_evidence
        assert getattr(event, "id", None) is None


def test_the_connector_emits_exactly_the_held_event_type():
    """`MESSAGE_POSTED` joins it in `33d`, deliberately and not by a connector."""
    assert _connector().emits() == frozenset({ObservedEventType.CALENDAR_EVENT_HELD})


def test_the_connector_satisfies_the_port_and_samples_a_real_event():
    """Both members `8d` declares on the port, asserted against this adapter.

    `sample_events()` is what the AD-34 gate inspects, and an empty tuple would
    pass that gate without executing one assertion.
    """
    connector = _connector()
    assert isinstance(connector, ConnectorPort)
    samples = connector.sample_events()
    assert samples
    for event in samples:
        assert event.type is ObservedEventType.CALENDAR_EVENT_HELD
        assert event.authored_by is Provenance.UNKNOWN
        assert getattr(event, "id", None) is None


def test_the_sample_is_built_by_the_harvest_mapping_and_does_not_move():
    """Two calls agree, and neither reads the clock or a fixture of its own."""
    connector = _connector()
    assert connector.sample_events() == connector.sample_events()


def test_the_health_probe_reports_under_this_connectors_instance():
    """The registry shows one row per instance, whatever the adapter calls itself."""
    probe = _connector(auth=Auth(Probe("graph", Health.ABSENT, "nothing enrolled"))).check_health()

    assert probe.name == INSTANCE
    assert probe.health is Health.ABSENT


def test_a_probe_that_raises_is_reported_rather_than_propagated():
    """A breach of the never-raise contract one layer down must not take the report."""
    probe = _connector(auth=Auth(raises=RuntimeError("boom"))).check_health()

    assert probe.health is Health.FAILING
    assert "RuntimeError" in probe.detail


# ── Rows that cannot be modelled ─────────────────────────────────────────────


def test_an_id_that_cannot_be_a_citation_refuses_one_row_and_keeps_the_rest():
    """A `meeting:<id>` reference is two colon-separated parts (AD-34).

    Refused rather than rewritten: the reference is what every fact extracted
    from the meeting resolves against.
    """
    result = _harvest(_ended(id="AAMk:with:colons"), _ended(id="AAMk-fine"))

    assert [record.meeting_id for record in result.records] == ["AAMk-fine"]
    assert [refusal.identifier for refusal in result.refusals] == ["AAMk:with:colons"]
    assert result.outcome is HarvestOutcome.HARVESTED


_BELL = "stand-up\x07with a bell"
_HUGE = "T" * 20_000
"""Two subjects `11a`'s record grammar will not hold.

A bell is C0 and is not one of the four `render_value` escapes, so it reaches the
file unescaped and the terminal that prints the record's name back. Twenty
thousand characters renders a `title=` line past the 16384 a field line may be.
Both are things a PM can type into Outlook, and neither is anything `33b`
refuses.
"""


@pytest.mark.parametrize(
    "field, value",
    [("subject", _BELL), ("subject", _HUGE), ("id", "A" * 20_000)],
    ids=["a control character in the subject", "a 20000-character subject", "a 20000-character id"],
)
def test_a_row_the_record_grammar_refuses_is_refused_here_and_not_in_app(
    tmp_path, field, value
):
    """The refusal that would otherwise kill every harvest on the machine, forever.

    `MeetingRecords.put` refuses all three — the premise is asserted below rather
    than assumed — and it refuses them inside `pm_ai.app`, out of `run_harvest`,
    with the whole batch in hand: no record written, no event persisted, the
    cursor not advanced. And it does not clear on its own. The harvest window
    reaches *backward* on every run, so the same row is fetched and refused again
    next cycle, and the harvest stays dead until the PM edits the row in Outlook
    with nothing anywhere naming which row it is.

    Guarded in the connector, it costs its own row and names it. Asserted through
    `run_harvest` because the failure this prevents is a property of the whole
    pipeline rather than of the mapping.
    """
    # The premise. Without this the test below would pass on a row nothing
    # refused, which is exactly how this shipped.
    with pytest.raises(MalformedMeeting):
        _records(tmp_path).put(
            Meeting(
                meeting_id=value if field == "id" else "AAMk-premise",
                title=value if field == "subject" else "a title",
                start=NOW - timedelta(days=1),
                duration_minutes=30,
                attendees=(),
                scope=PERSONAL,
            )
        )

    refused = _harvest(_ended(**{field: value}), _ended(id="AAMk-fine"))
    assert [record.meeting_id for record in refused.records] == ["AAMk-fine"]
    assert len(refused.refusals) == 1
    assert refused.outcome is HarvestOutcome.HARVESTED

    daemon = _wired(tmp_path, _ended(**{field: value}), _ended(id="AAMk-fine"))
    persisted = run_harvest(daemon, INSTANCE)

    assert persisted.persisted == 1, "the neighbour's event survived the bad row"
    (member,) = daemon.storage.list_collection(scope=PERSONAL, artifact=MEETINGS)
    assert "AAMk-fine" in daemon.storage.read_artifact(
        scope=PERSONAL, artifact=MEETINGS, name=member
    ).decode()
    assert daemon.storage.load_cursor(INSTANCE).token, (
        "and the cursor advanced, so the next run is not handed the same window "
        "and the same doomed row"
    )


def test_a_fault_building_the_event_costs_its_row_rather_than_the_batch(monkeypatch):
    """`_to_event` runs inside the per-row `try`, not after it.

    It resolves an actor, parses a reference and runs `NormalizedEvent`'s
    validation, all on provider data — so it can refuse a row `_meeting`
    accepted, and outside the `try` that refusal left `harvest` by raising, out
    of a method `ConnectorPort` says never raises and whose only caller has no
    `except`.
    """
    original = GraphConnector._to_event

    def _refuse_one(self, meeting, row):
        if row.event_id == "AAMk-bad":
            raise GraphRowRefused("this row's event could not be built")
        return original(self, meeting, row)

    monkeypatch.setattr(GraphConnector, "_to_event", _refuse_one)
    result = _harvest(_ended(id="AAMk-bad"), _ended(id="AAMk-fine"))

    assert [record.meeting_id for record in result.records] == ["AAMk-fine"]
    assert [refusal.identifier for refusal in result.refusals] == ["AAMk-bad"]
    assert len(result.events) == 1


def test_an_unexpected_fault_after_the_fetch_is_a_value_and_not_a_raise():
    """A harvest reports; it never raises. `_result` was outside every handler.

    A clock that raises is the reachable case — `_result`'s first statement is
    `self.now()` — and it stands here for `resolve_actor`, the payload
    construction and the cursor encoding, which are all inside the same
    unguarded stretch. Raising loses the page, its coverage and its cursor
    together, which is the whole reason `FAILED` is a value.
    """

    def _broken_clock() -> datetime:
        raise RuntimeError("this machine's clock is not a clock")

    connector = _connector(_ended())
    connector.now = _broken_clock

    result = connector.harvest(Cursor())

    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None
    assert "RuntimeError" in result.failure.reason
    assert result.failure.retryable is False
    assert result.cursor == Cursor(), "nothing was learned, so nothing moved"
    assert result.records == () and result.live == () and result.events == ()


def test_a_span_that_runs_backwards_is_refused_rather_than_clamped():
    """Zero is the all-day convention and means a marker nobody sat through."""
    result = _harvest(_event(NOW - timedelta(hours=1), NOW - timedelta(hours=2)))

    assert result.records == () and result.live == ()
    assert len(result.refusals) == 1


def test_a_row_flagged_by_the_fetch_is_still_emitted():
    """Matrix: `33b` flagged it → persisted and counted, never dropped.

    A timestamp replaced from the local clock is well-formed, plausible and
    wrong, so the row is carried as the provider sent it and `persist_events`
    is what counts it.
    """
    far = NOW + timedelta(days=400)
    result = _harvest(_event(far, far + timedelta(hours=1)), now=far + timedelta(days=1))

    assert len(result.records) == 1
    assert result.events[0].occurred_at == far


# ── Coverage ─────────────────────────────────────────────────────────────────


def test_a_window_of_only_upcoming_rows_still_earns_its_coverage():
    """Rows arrived and could be placed in time; nothing was persisted.

    The pairing `HarvestResult.__post_init__` had to be widened for: coverage is
    evidence a fetch reached something, and this fetch reached a calendar full of
    meetings that have not happened.
    """
    result = _harvest(_upcoming(), _upcoming(id="AAMk-2"))

    assert result.coverage is not None
    assert result.coverage.connector_instance == INSTANCE


def test_the_cursor_advances_to_the_calendar_instant_the_walk_reached():
    """Calendar time, not an `ingested_at` watermark — AD-35's two clocks."""
    result = _harvest(_ended())

    assert result.cursor.token
    assert datetime.fromisoformat(result.cursor.token.decode()) == NOW + WIDTH


def test_a_cursor_this_connector_did_not_write_fails_the_run_and_moves_nothing():
    """Opaque cuts both ways: another connector's token is not a position here."""
    connector = _connector(_ended())
    result = connector.harvest(Cursor(b"page3"))

    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None and result.failure.retryable is False
    assert result.cursor == Cursor(b"page3")


# ── The enrolment row ────────────────────────────────────────────────────────


def _enrol(daemon, **overrides) -> None:
    """Write one Graph enrolment row into `connectors/`, as `8b` shapes it."""
    row = {
        "instance": INSTANCE,
        "system": "graph",
        "enabled": True,
        CLIENT_ID_KEY: CLIENT_ID,
        WIDTH_KEY: int(WIDTH.total_seconds() // 60),
        REACH_BACK_KEY: int(REACH_BACK.total_seconds() // 60),
    }
    row.update(overrides)
    for key in [name for name, value in row.items() if value is None]:
        del row[key]
    daemon.storage.write_artifact(
        json.dumps(row).encode("utf-8"),
        scope=APPLICATION,
        artifact="connectors/",
        name=f"{INSTANCE}.json",
    )


def test_a_row_carrying_both_widths_builds_a_graph_connector(tmp_path):
    """The criterion: composition reaches a connector, not just an adapter."""
    _enrol(build(tmp_path, "alpha", now=lambda: NOW))

    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    connector = daemon.connectors[INSTANCE]
    assert isinstance(connector, GraphConnector)
    assert connector.calendar.windows.width == WIDTH
    assert connector.calendar.windows.first_run_reach_back == REACH_BACK


@pytest.mark.parametrize("missing", [WIDTH_KEY, REACH_BACK_KEY, CLIENT_ID_KEY])
def test_a_row_missing_a_setting_builds_nothing_and_names_the_key(
    tmp_path, capsys, missing
):
    """No default is supplied: a width accepted by silence is invisible."""
    _enrol(build(tmp_path, "alpha", now=lambda: NOW), **{missing: None})

    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    assert INSTANCE not in daemon.connectors
    assert missing in capsys.readouterr().err


def test_the_row_carries_the_category_mapping_into_the_built_connector(tmp_path):
    """The mapping is per-machine connector configuration, so it rides the row."""
    _enrol(build(tmp_path, "alpha", now=lambda: NOW), **{CATEGORIES_KEY: {"Platform": "alpha"}})

    connector = build(tmp_path, "alpha", now=lambda: NOW).connectors[INSTANCE]
    assert connector.categories.scope_for(("Platform",)) == PROJECT
    assert connector.categories.scope_for(("Dentist",)) == PERSONAL


def test_a_stale_category_mapping_stops_the_connector_being_built(tmp_path, capsys):
    """`UnknownProject` at composition, naming both halves, and the daemon still boots.

    Still boots because `pm-ai doctor` is the command that diagnoses a broken
    machine and it cannot run if `build()` raises.

    Against a **production** resolver, and it has to be: `ScopePaths.rooted`
    invents a repository beneath its root for any project id it is handed, which
    is exactly what makes it the test factory — under it there is no such thing
    as an unregistered project, and this refusal could never fire.
    """
    registry = {"alpha": tmp_path / "alpha"}
    _enrol(
        build(None, "alpha", paths=ScopePaths.production(home=tmp_path, projects=registry)),
        **{CATEGORIES_KEY: {"Platform": "retired"}},
    )

    daemon = build(
        None, "alpha", paths=ScopePaths.production(home=tmp_path, projects=registry)
    )

    assert INSTANCE not in daemon.connectors
    said = capsys.readouterr().err
    assert "Platform" in said and "retired" in said


def test_a_missing_width_is_named_by_the_builder_itself():
    """The refusal a test can read without going through composition."""
    with pytest.raises(MissingGraphSetting) as refused:
        graph_window_policy({WIDTH_KEY: 1440})

    assert REACH_BACK_KEY in str(refused.value)


@pytest.mark.parametrize("value", [10**20, -(10**20), 0, MAX_SETTING_MINUTES + 1])
def test_a_width_outside_the_bound_is_refused_by_name_rather_than_overflowing(value):
    """A hand-edited number that arithmetic cannot hold, refused where it is read.

    `timedelta(minutes=value)` raises `OverflowError` past roughly 1.5e12
    minutes, and `OverflowError` is not a `ValueError` — it escaped
    `_graph_connector`'s handler, so one mistyped digit in a plaintext
    `connectors/` row took `build()` down and with it `pm-ai doctor`, the command
    that diagnoses a mistyped `connectors/` row.
    """
    # The premise: this is arithmetic, not a policy check further down.
    if abs(value) > MAX_SETTING_MINUTES:
        with pytest.raises(OverflowError):
            timedelta(minutes=value * 10**6)

    with pytest.raises(MissingGraphSetting) as refused:
        graph_window_policy({WIDTH_KEY: value, REACH_BACK_KEY: 10_080})

    assert WIDTH_KEY in str(refused.value)


def test_a_row_carrying_an_absurd_width_leaves_the_daemon_composing(tmp_path, capsys):
    """The same number through composition, which is where it did the damage."""
    _enrol(build(tmp_path, "alpha", now=lambda: NOW), **{WIDTH_KEY: 10**20})

    daemon = build(tmp_path, "alpha", now=lambda: NOW)

    assert INSTANCE not in daemon.connectors
    assert WIDTH_KEY in capsys.readouterr().err


def test_a_row_that_faults_unclassifiably_still_leaves_the_daemon_composing(
    tmp_path, capsys, monkeypatch
):
    """The handler behind the named refusals, and why it is not narrower.

    `_graph_connector` caught three classes; the row it reads is plaintext and
    hand-editable, and `OverflowError` — the fourth, found by a reviewer — was
    not among them. No hand edit may stop `build()` composing, because the
    command that explains a bad row runs inside the daemon that would not
    compose.
    """
    _enrol(build(tmp_path, "alpha", now=lambda: NOW))

    def _unclassifiable(entry):
        raise RuntimeError("a shape nobody classified")

    monkeypatch.setattr(wiring, "graph_window_policy", _unclassifiable)
    daemon = build(tmp_path, "alpha", now=lambda: NOW)

    assert INSTANCE not in daemon.connectors
    assert "RuntimeError" in capsys.readouterr().err


def test_the_row_names_the_tenant_and_an_absent_one_takes_the_adapters_default(tmp_path):
    """The fifth setting, and the only one with a default — the adapter's own.

    Untested anywhere until now: `TENANT_KEY` was read, defaulted to a literal
    `"organizations"` beside a comment claiming the default stayed the adapter's,
    and under a docstring saying none of the row's settings was defaulted. The
    default is read off `GraphDeviceCodeAuth.tenant` here, so a test that pinned
    the literal would have pinned the copy rather than the agreement.
    """
    named = tmp_path / "named"
    _enrol(
        build(named, "alpha", now=lambda: NOW), **{TENANT_KEY: "contoso.onmicrosoft.com"}
    )
    assert (
        build(named, "alpha", now=lambda: NOW).connectors[INSTANCE].auth.tenant
        == "contoso.onmicrosoft.com"
    )

    absent = tmp_path / "absent"
    _enrol(build(absent, "alpha", now=lambda: NOW))
    assert (
        build(absent, "alpha", now=lambda: NOW).connectors[INSTANCE].auth.tenant
        == GraphDeviceCodeAuth.tenant
    ), "the adapter's value, not a second copy of it"
    assert GraphDeviceCodeAuth.tenant == "organizations", (
        "and it is still any work or school tenant rather than `common`, which "
        "admits personal accounts none of the seven permissions mean anything for"
    )


# ── What composition injects, which nothing else can observe ─────────────────

GRAPH_CREDENTIAL = '{"refresh_token": "not-a-real-token", "home_account_id": "a.b"}'
KEY = b"K" * 32


class _Keychain:
    """Custody that answers, so composition never reaches the real keychain."""

    def __init__(self, secret: bytes | None = KEY) -> None:
        self._secret = secret

    def store(self, name: str, secret: bytes) -> None:
        self._secret = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        from pm_ai.ports import KeyAlreadyEnrolled

        if self._secret is not None:
            raise KeyAlreadyEnrolled(name)
        self._secret = secret

    def fetch(self, name: str) -> bytes:
        from pm_ai.ports import KeyNotFound

        if self._secret is None:
            raise KeyNotFound(name)
        return self._secret

    def delete(self, name: str) -> None:
        from pm_ai.ports import KeyNotFound

        if self._secret is None:
            raise KeyNotFound(name)
        self._secret = None


def test_the_composed_connector_holds_the_sealed_credential_and_a_wait_that_waits(
    tmp_path,
):
    """The two things composition injects, and neither was pinned by anything.

    **The credential.** Replacing `credential=` with `None` left the whole suite
    green — which is precisely the custody regression story 33a's
    `test_a_composed_adapter_holds_the_credential_the_sealed_store_keeps` exists
    to prevent, and that test seals a *GitLab* credential. No test anywhere
    sealed a Graph one, so the read-back `8b`'s "active at the next start"
    promises was unasserted on the connector family it was built for.

    **The wait.** Deleting `wait=time.sleep` failed no test either, and
    `test_graph_calendar_fetch.py` states the obligation in writing: `33c` is the
    first composition, a forgotten `wait` turns every honoured `Retry-After` into
    an immediate retry against a provider that is rate-limiting, and no test
    anywhere would observe it — because every fetch test injects a recorder.
    This is the one place the real one can be seen.
    """
    keychain = _Keychain()
    enrolling = build(tmp_path, "alpha", now=lambda: NOW, keychain=keychain)
    enrol_connector(
        enrolling.storage,
        system="graph",
        instance=INSTANCE,
        credential=GRAPH_CREDENTIAL,
        # A provider that answers, and never echoes what it was given.
        probe=lambda system, credential: "graph accepted it",
    )
    # `enrol_connector` writes the row `8b` shapes; the Graph settings are
    # hand-added to it, which is the state a real machine is in after
    # `pm-ai connector add`.
    _enrol(enrolling)

    connector = build(tmp_path, "alpha", now=lambda: NOW, keychain=keychain).connectors[
        INSTANCE
    ]

    assert connector.auth.store.read() == GRAPH_CREDENTIAL, (
        "the adapter was built with no credential, so a Graph connector enrolled "
        "ten seconds ago signs in as though nothing had been enrolled"
    )
    assert connector.calendar.client.wait is time.sleep, (
        "the client's default returns without waiting, by name, and its 429 "
        "branch refuses rather than spinning — so an unwired `wait` loses every "
        "honoured hint and nothing else can see it"
    )


def test_the_config_vocabulary_stays_closed_and_holds_no_connector_setting():
    """The widths are per-machine connector settings and never `config.toml`'s.

    Pinned as the whole set rather than as an absence, so a width key added to
    `Config` fails here instead of passing a `not in` check that only ever knew
    the two spellings this file happened to think of. Story 4g added the fourth
    and last member; the reason it is *four* is stated in `pm_ai.core.config`.
    """
    assert ACCEPTED_KEYS == frozenset(
        {"blended_hourly_rate", "pm_handle", "verbose_logging", "display_timezone"}
    )


def test_a_registered_graph_connector_makes_the_gates_two_deep(tmp_path):
    """AD-27 and AD-34 with a second connector, and `all_connectors()` at two."""
    _enrol(build(tmp_path, "alpha", now=lambda: NOW))
    build(tmp_path, "alpha", now=lambda: NOW)

    registered = all_connectors()
    assert len(registered) == 2
    for connector in registered:
        assert isinstance(connector, ConnectorPort)
        assert set(connector.emits()) <= set(ObservedEventType)
        samples = connector.sample_events()
        assert samples
        for event in samples:
            assert getattr(event, "id", None) is None


# ── The pipeline ─────────────────────────────────────────────────────────────


def _wired(tmp_path: Path, *events: dict, **options):
    """A daemon whose `graph:work` instance is a connector over a scripted page."""
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    daemon.connectors[INSTANCE] = _connector(*events, **options)
    return daemon


def _records(tmp_path: Path):
    """`11a`'s accessor, for the tests that assert what it refuses."""
    return build(tmp_path, "alpha", now=lambda: NOW).meetings


def _log(daemon, scope: DataScope) -> str:
    return "".join(
        daemon.storage.read_event_log_segment(scope=scope, name=name)
        for name in daemon.storage.event_log_segments(scope=scope)
    )


def test_one_ended_and_one_upcoming_row_produce_exactly_one_of_each(tmp_path):
    """The criterion, asserted rather than described.

    One event persisted, one record on disk, and both rows handed back — the
    past/future split at the only place all three are observable together.
    """
    rows = (_ended(id="AAMk-held"), _upcoming(id="AAMk-ahead"))
    # A connector of its own for the inspection: each one is wired over a single
    # scripted page, so harvesting the daemon's twice would spend the fixture.
    returned = _harvest(*rows)
    assert [m.meeting_id for m in returned.records] == ["AAMk-held"]
    assert [m.meeting_id for m in returned.live] == ["AAMk-ahead"]

    daemon = _wired(tmp_path, *rows)
    persisted = run_harvest(daemon, INSTANCE)

    assert persisted.persisted == 1
    members = daemon.storage.list_collection(scope=PERSONAL, artifact=MEETINGS)
    assert len(members) == 1
    assert "AAMk-held" in daemon.storage.read_artifact(
        scope=PERSONAL, artifact=MEETINGS, name=members[0]
    ).decode()
    assert "calendar_event_held" in _log(daemon, PERSONAL)


def test_the_record_is_written_before_its_event_is_persisted(tmp_path, monkeypatch):
    """An event citing `meeting:<id>` emitted first leaves a dangling citation."""
    daemon = _wired(tmp_path, _ended())
    order: list[str] = []
    put, persist = daemon.meetings.put, daemon.storage.persist_events

    monkeypatch.setattr(
        daemon.meetings, "put", lambda meeting: (order.append("record"), put(meeting))[1]
    )
    monkeypatch.setattr(
        daemon.storage,
        "persist_events",
        lambda events, *, scope: (order.append("events"), persist(events, scope=scope))[1],
    )

    run_harvest(daemon, INSTANCE)

    assert order == ["record", "events"]


def test_a_meetings_write_that_refuses_persists_no_event(tmp_path, monkeypatch):
    """Matrix: `meetings/` refuses → the event is **not** emitted, and it propagates."""
    daemon = _wired(tmp_path, _ended())

    def _refuse(meeting):
        raise OSError("the collection refused")

    monkeypatch.setattr(daemon.meetings, "put", _refuse)
    with pytest.raises(OSError):
        run_harvest(daemon, INSTANCE)

    assert daemon.storage.event_log_segments(scope=PERSONAL) == ()
    assert daemon.storage.event_log_segments(scope=PROJECT) == ()


def test_a_project_meeting_and_a_personal_one_land_in_their_own_trees(tmp_path):
    """Both directions, because the mapping is the only thing deciding scope."""
    daemon = _wired(
        tmp_path,
        _ended(id="AAMk-team", categories=["Platform"]),
        _ended(id="AAMk-own", categories=["Dentist"]),
        categories={"Platform": "alpha"},
    )

    run_harvest(daemon, INSTANCE)

    (team,) = daemon.storage.list_collection(scope=PROJECT, artifact=MEETINGS)
    (own,) = daemon.storage.list_collection(scope=PERSONAL, artifact=MEETINGS)
    assert "AAMk-team" in daemon.storage.read_artifact(
        scope=PROJECT, artifact=MEETINGS, name=team
    ).decode()
    assert "AAMk-own" in daemon.storage.read_artifact(
        scope=PERSONAL, artifact=MEETINGS, name=own
    ).decode()
    assert "calendar_event_held" in _log(daemon, PROJECT)
    assert "calendar_event_held" in _log(daemon, PERSONAL)


def _writes(daemon, monkeypatch) -> list[tuple[int, DataScope]]:
    """Every `persist_events` call this daemon makes, as (count, scope)."""
    calls: list[tuple[int, DataScope]] = []
    real = daemon.storage.persist_events

    def _record(events, *, scope):
        calls.append((len(events), scope))
        return real(events, scope=scope)

    monkeypatch.setattr(daemon.storage, "persist_events", _record)
    return calls


def test_a_single_scope_harvest_makes_exactly_the_one_call_it_replaced(
    tmp_path, monkeypatch
):
    """The claim `_persist_by_scope`'s docstring makes about every earlier connector.

    Story 33c restructured the write path for all of them — one call became a
    grouping — and nothing asserted the invariant that made that safe: for a
    connector whose events all carry the daemon's own scope, the grouping
    produces exactly the single call it replaced, with every event in it. A
    grouping that split a same-scope batch would turn `persist_events`'
    all-or-nothing into all-or-some without failing a test.
    """
    daemon = _wired(
        tmp_path,
        _ended(id="AAMk-1", categories=["Platform"]),
        _ended(id="AAMk-2", categories=["Platform"]),
        categories={"Platform": "alpha"},
    )
    calls = _writes(daemon, monkeypatch)

    run_harvest(daemon, INSTANCE)

    assert calls == [(2, daemon.scope)], (
        "two events, both in the daemon's own scope, one call — the shape every "
        "connector before this story had"
    )


def test_an_empty_harvest_still_calls_the_writer_once_in_the_daemons_scope(
    tmp_path, monkeypatch
):
    """The branch the docstring justifies at length, and it was untested.

    `at` on the returned `PersistResult` is a clock read the single writer owns
    (AD-5). Skipping the call for an empty batch would mean composing one here,
    and a timestamp nothing measured is the fabrication AD-35 spends a whole
    story deleting. The daemon's scope is the only one available: there are no
    events to take one from.
    """
    daemon = _wired(tmp_path)
    calls = _writes(daemon, monkeypatch)

    persisted = run_harvest(daemon, INSTANCE)

    assert calls == [(0, daemon.scope)]
    assert persisted.persisted == 0
    assert persisted.at == NOW, "measured by the writer, not composed by the caller"


def test_a_failed_harvest_still_writes_the_rows_the_walk_did_reach(tmp_path):
    """A partial walk's meetings are real, and `run_harvest` persists them.

    Page one arrived and page two did not. Those meetings happened; discarding
    them would mean a connector that fails on its last page records nothing ever.
    What keeps it safe is `33b`'s cursor rule — `walked_through` advances only
    past a span walked to completion — so the failed span is asked for again
    next run and its records are rewritten in place under the same name. A record
    written twice rather than a meeting lost.

    Stated in `_result`'s docstring and asserted here, because until this ran the
    decision existed only as the absence of a filter.
    """
    def _partial() -> GraphConnector:
        """A walk whose second page 500s. One per harvest: a script is spent once."""
        clock = lambda: NOW
        return GraphConnector(
            auth=Auth(),
            calendar=GraphCalendarFetch(
                client=GraphClient(
                    auth=Auth(),
                    transport=Transport(
                        GraphResponse(
                            status=200,
                            body={
                                "value": [_ended(id="AAMk-page-one")],
                                "@odata.nextLink": (
                                    f"{GRAPH_BASE}/me/calendarView?$skiptoken=page2"
                                ),
                            },
                            headers={"Content-Type": "application/json"},
                        ),
                        GraphResponse(status=500),
                    ),
                    now=clock,
                ),
                instance=INSTANCE,
                windows=WindowPolicy(width=WIDTH, first_run_reach_back=REACH_BACK),
                now=clock,
            ),
            categories=CategoryScopes(projects={}, registered=lambda project: True),
            now=clock,
        )

    result = _partial().harvest(Cursor())
    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None
    assert [record.meeting_id for record in result.records] == ["AAMk-page-one"]
    assert result.cursor == Cursor(), (
        "the span was not walked to completion, so the position does not move "
        "and the same window is asked for again"
    )

    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    daemon.connectors[INSTANCE] = _partial()
    persisted = run_harvest(daemon, INSTANCE)

    assert persisted.persisted == 1
    (member,) = daemon.storage.list_collection(scope=PERSONAL, artifact=MEETINGS)
    assert "AAMk-page-one" in daemon.storage.read_artifact(
        scope=PERSONAL, artifact=MEETINGS, name=member
    ).decode()
    assert daemon.storage.harvest_failure(INSTANCE) is not None, (
        "and the failure is recorded beside it, so 'ran and failed' survives the "
        "process as something other than the absence of coverage"
    )


def test_a_re_harvest_keeps_a_hand_written_notes_region_byte_identical(tmp_path):
    """`11a` preserves the region; this is the slice that would overwrite it."""
    daemon = _wired(tmp_path, _ended())
    run_harvest(daemon, INSTANCE)
    (member,) = daemon.storage.list_collection(scope=PERSONAL, artifact=MEETINGS)

    notes = "\nAlex owes me the migration plan.\nDo not let this slide.\n"
    original = daemon.storage.read_artifact(
        scope=PERSONAL, artifact=MEETINGS, name=member
    ).decode()
    daemon.storage.write_artifact(
        (original.rstrip("\n") + notes).encode("utf-8"),
        scope=PERSONAL,
        artifact=MEETINGS,
        name=member,
    )

    again = _wired(tmp_path, _ended())
    run_harvest(again, INSTANCE)

    rewritten = again.storage.read_artifact(
        scope=PERSONAL, artifact=MEETINGS, name=member
    ).decode()
    assert rewritten.split(NOTES_HEADING, 1)[1] == notes, (
        "pm-ai owns the fields and `## Summary`; the notes are the human's and "
        "are copied through verbatim"
    )


def test_a_row_that_vanishes_upstream_leaves_its_record_alone(tmp_path):
    """Matrix: previously written, absent from a window that **was** harvested.

    Staleness is derived from `8a`'s coverage rather than stored, so a later
    harvest that does not see the row neither rewrites nor removes it — and the
    coverage this run earned is what lets a reader tell "we looked and it was
    gone" from "nobody looked".
    """
    first = _wired(tmp_path, _ended())
    run_harvest(first, INSTANCE)
    (member,) = first.storage.list_collection(scope=PERSONAL, artifact=MEETINGS)
    before = first.storage.read_artifact(scope=PERSONAL, artifact=MEETINGS, name=member)

    second = _wired(tmp_path, _ended(id="AAMk-other"))
    run_harvest(second, INSTANCE)

    assert (
        second.storage.read_artifact(scope=PERSONAL, artifact=MEETINGS, name=member)
        == before
    )
    assert len(second.storage.list_collection(scope=PERSONAL, artifact=MEETINGS)) == 2


def test_a_flagged_row_is_persisted_and_counted_rather_than_raised(tmp_path):
    """Matrix: `33b` flagged it → persisted **and counted** in `PersistResult.flagged`.

    The half the connector cannot assert on its own. `flagged` is computed in
    `storage/service.py` from `validate_occurred_at` against the machine's
    clock, not from `CalendarRow.clock_flag`, so nothing proved the two ever
    meet until this ran the row the whole way down.

    An epoch start is the reachable case, and it is the one `_clock_flag`'s
    docstring names: an absent provider field arriving as the zero-value parse.
    It trips `33b`'s `EARLIEST_PLAUSIBLE` floor *and* ends before `NOW`, which a
    row flagged the other way cannot do — a start beyond the window's far edge
    is by construction still upcoming, so it becomes no event and reaches no
    count.
    """
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    row = _event(epoch, epoch + timedelta(hours=1))

    fetched = GraphCalendarFetch(
        client=GraphClient(auth=Auth(), transport=Transport(_page(row)), now=lambda: NOW),
        instance=INSTANCE,
        windows=WindowPolicy(width=WIDTH, first_run_reach_back=REACH_BACK),
        now=lambda: NOW,
    ).fetch()
    assert fetched.rows[0].clock_flag is not None, (
        "the premise of this matrix row: `33b` is what flags it, and a test "
        "that asserted only the count would pass on a row nothing had flagged."
    )

    result = run_harvest(_wired(tmp_path, row), INSTANCE)

    assert result.flagged == 1
    assert result.persisted == 1, "flagged is a count beside the write, not instead of it"
