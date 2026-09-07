"""Story 33b — the Graph calendar fetch, one test per matrix row.

**No test opens a socket, and the fixtures are what make that structural.**
`GraphClient.transport` is injected, so every response in this file comes from
a scripted fake; a test that forgot to inject one would reach
`_urllib_transport` and try to resolve `graph.microsoft.com`, which fails rather
than passing quietly. Nothing here sleeps either: `GraphClient.wait` defaults to
not waiting, and the throttle rows assert on what the client *recorded* asking
for rather than on elapsed time.

## What the fixtures are, stated honestly

The payload **shapes** are slice 0's, measured against a live tenant on
2026-09-06 and recorded in `_bmad-output/implementation-artifacts/
slice-0-graph-spike-2026-09-06.md`: the full `calendarView` event key set, the
naive `dateTime` with its seven fractional digits, `timeZone` in a separate
field, and `seriesMasterId`/`occurrenceId` both present.

The **values** are written here. Slice 0 was deliberately generalised — it ran
against a real workplace tenant and this repository is public, so subjects,
attendees, identifiers and real start times were omitted from its record and
there is no captured response body in the repository to load. This story's
Design Notes ask for recorded payloads rather than hand-written dicts; what is
recorded is the shape, and these fixtures are built to it. The gap is real and
is reported rather than papered over: a fixture cannot fail on a field nobody
knew Graph sends.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pm_ai.connectors.graph.auth import CredentialStale
from pm_ai.connectors.graph.client import (
    DEFAULT_THROTTLE_BACKOFF,
    GRAPH_BASE,
    PREFER_UTC,
    ConsentChanged,
    GraphClient,
    GraphProtocolError,
    GraphRequest,
    GraphResponse,
    GraphThrottled,
)
from pm_ai.connectors.graph.calendar import (
    HARVEST_CYCLE,
    WINDOWS_TIMEZONES,
    CalendarWindow,
    GraphCalendarFetch,
    MalformedCalendarRow,
    UnresolvableTimezone,
    WindowPolicy,
    to_utc,
    zone_of,
)
from pm_ai.domain.clocks import ImplausibleTimestamp

BASE = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
"""This machine's clock at the instant every fetch below starts."""

INSTANCE = "graph:work"
TICK = timedelta(seconds=60)
"""How far the clock moves each time the provider answers a page.

The clock advances *only* when a page comes back, which is what makes the
coverage assertions exact rather than approximate: `coverage.start` is the tick
the first page arrived on and `coverage.end` the tick the walk stopped on.
"""

WIDTH = timedelta(hours=8)
REACH_BACK = timedelta(days=7)


def _policy(**overrides) -> WindowPolicy:
    return WindowPolicy(
        **{"width": WIDTH, "first_run_reach_back": REACH_BACK, **overrides}
    )


def _event(**overrides) -> dict:
    """One `calendarView` event, in slice 0's measured shape.

    Only the keys this slice reads are spelled out; the spike's full key list is
    thirty-nine long and the rest is `33c`'s and `33e`'s business. The
    `dateTime` values carry seven fractional digits and no offset, exactly as
    the spike recorded them.
    """
    event = {
        "id": "AAMkAGI2-single",
        "subject": "Sprint review",
        "start": {"dateTime": "2026-09-07T10:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-09-07T11:00:00.0000000", "timeZone": "UTC"},
        "isAllDay": False,
        "isCancelled": False,
        "type": "singleInstance",
        "seriesMasterId": None,
        "occurrenceId": None,
        "categories": ["Platform"],
        "organizer": {"emailAddress": {"name": "A Person", "address": "a@example.invalid"}},
        "attendees": [
            {
                "type": "required",
                "status": {"response": "accepted", "time": "2026-09-01T09:00:00Z"},
                "emailAddress": {"name": "B Person", "address": "b@example.invalid"},
            }
        ],
        "responseStatus": {"response": "organizer", "time": "0001-01-01T00:00:00Z"},
    }
    event.update(overrides)
    return event


def _zoned(dt: str, zone: str) -> dict:
    """A `{dateTime, timeZone}` pair, with Graph's seven fractional digits."""
    return {"dateTime": f"{dt}.0000000", "timeZone": zone}


def _page(*events: dict, next_link: str | None = None) -> GraphResponse:
    body: dict = {"value": list(events)}
    if next_link is not None:
        body["@odata.nextLink"] = next_link
    return GraphResponse(status=200, body=body, headers={"Content-Type": "application/json"})


def _link(page: int) -> str:
    """A `nextLink` shaped like Graph's: on-host, with an opaque skiptoken."""
    return f"{GRAPH_BASE}/me/calendarView?$skiptoken=page{page}"


class Clock:
    """A clock that moves only when something makes it move (AD-30)."""

    def __init__(self, at: datetime = BASE) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def tick(self, by: timedelta = TICK) -> None:
        self.at += by


class Auth:
    """`33a`'s adapter, as much of it as the client touches.

    Records every call, because two matrix rows are about *how* the client asks
    rather than about the answer: a 401 must force a refresh, and a 403 must
    not.
    """

    def __init__(self, *tokens: str) -> None:
        self.tokens = list(tokens) or ["token-1", "token-2"]
        self.index = 0
        self.forced = 0
        self.raises: Exception | None = None

    def access_token(self, *, force_refresh: bool = False) -> str:
        if self.raises is not None:
            raise self.raises
        if force_refresh:
            self.forced += 1
            self.index = min(self.index + 1, len(self.tokens) - 1)
        return self.tokens[self.index]


class Transport:
    """A scripted transport: recorded requests in, queued responses out.

    An entry may be a `GraphResponse` or a callable taking the request, which is
    how "401 unless the token is the refreshed one" is expressed without the
    fake knowing anything about auth.
    """

    def __init__(self, *script, clock: Clock | None = None) -> None:
        self.script = list(script)
        self.clock = clock
        self.requests: list[GraphRequest] = []

    def __call__(self, request: GraphRequest) -> GraphResponse:
        self.requests.append(request)
        if not self.script:
            raise AssertionError(
                f"the fetch asked for a page the fixture does not have: "
                f"{request!r}. Every response a test expects to be needed is "
                f"scripted, so an extra request is a walk that should have "
                f"stopped."
            )
        answer = self.script.pop(0)
        if self.clock is not None:
            # The provider answering is the only thing that moves the clock, so
            # a coverage window's bounds are the instants pages arrived on.
            self.clock.tick()
        return answer(request) if callable(answer) else answer

    @property
    def urls(self) -> list[str]:
        return [request.url for request in self.requests]


class Waits:
    """Every `Retry-After` the client actually honoured, in seconds."""

    def __init__(self) -> None:
        self.seconds: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


def _fetcher(
    *script,
    clock: Clock | None = None,
    auth: Auth | None = None,
    waits: Waits | None = None,
    policy: WindowPolicy | None = None,
    max_span: timedelta | None = None,
    **client_options,
) -> tuple[GraphCalendarFetch, Transport, Clock, Auth, Waits]:
    """One wired fetcher, and everything a test needs to assert on."""
    clock = clock or Clock()
    auth = auth or Auth()
    waits = waits or Waits()
    transport = Transport(*script, clock=clock)
    client = GraphClient(
        auth=auth, transport=transport, now=clock, wait=waits, **client_options
    )
    fetch = GraphCalendarFetch(
        client=client,
        instance=INSTANCE,
        windows=policy or _policy(),
        now=clock,
        **({"max_span": max_span} if max_span is not None else {}),
    )
    return fetch, transport, clock, auth, waits


# ── Single page ──────────────────────────────────────────────────────────────


def test_a_single_page_returns_its_rows_and_the_coverage_the_fetch_earned():
    """Matrix: a window with three events → three rows, and coverage.

    The row's phrase is "coverage spanning the requested range", and this
    story's own Boundaries clause overrides it: the `calendarView` range is
    calendar time and `CoverageWindow` is `ingested_at`, so what the range
    decides is *whether* coverage was earned, not its bounds. Both halves are
    asserted here — coverage exists, and it is emphatically not the range.
    """
    fetch, _, clock, _, _ = _fetcher(
        _page(
            _event(id="one"),
            _event(id="two", start=_zoned("2026-09-07T13:00:00", "UTC"), end=_zoned("2026-09-07T14:00:00", "UTC")),
            _event(id="three", start=_zoned("2026-09-07T15:00:00", "UTC"), end=_zoned("2026-09-07T16:00:00", "UTC")),
        )
    )
    result = fetch.fetch()

    assert [row.event_id for row in result.rows] == ["one", "two", "three"]
    assert result.pages == 1
    assert result.failure is None
    assert result.refusals == ()

    assert result.coverage is not None
    assert result.coverage.connector_instance == INSTANCE
    assert result.coverage.start == BASE + TICK, "the instant the provider answered"
    assert result.coverage.end == clock.at
    assert (result.coverage.start, result.coverage.end) != (
        result.window.start,
        result.window.end,
    ), (
        "coverage was reported as the calendarView range. That is AD-35's "
        "mixed-clock defect: the range is calendar time and coverage is "
        "ingested_at."
    )
    assert result.walked_through == result.window.end


def test_the_requested_window_is_the_cursor_and_the_coverage_window_is_not():
    """The two clocks, side by side, in one assertion each.

    `lifecycle.py:157-164` says a coverage window is recorded in `ingested_at`
    terms. `walked_through` is the other clock — calendar time — and a
    connector that returned one where the other belongs would still pass every
    row-shaped test in this file.
    """
    fetch, _, _, _, _ = _fetcher(_page(_event()))
    result = fetch.fetch()

    assert result.window == CalendarWindow(start=BASE - REACH_BACK, end=BASE + WIDTH)
    assert result.walked_through == BASE + WIDTH
    assert result.coverage is not None
    assert result.coverage.start > result.window.start
    assert result.coverage.end < result.window.end, (
        "the coverage window is inside the fetch, and the calendar range "
        "reaches into the future — they are not the same interval and cannot "
        "be substituted for one another"
    )


# ── Paged response ───────────────────────────────────────────────────────────


def test_every_page_is_followed_and_one_coverage_window_spans_all_of_them():
    """Matrix + acceptance: two pages, both returned, exactly one window.

    A fetcher that stops at `@odata.nextLink` passes any single-page test, so
    this is the one that has to fail for it.
    """
    fetch, transport, clock, _, _ = _fetcher(
        _page(_event(id="page-one-row"), next_link=_link(2)),
        _page(_event(id="page-two-row", start=_zoned("2026-09-07T14:00:00", "UTC"), end=_zoned("2026-09-07T15:00:00", "UTC"))),
    )
    result = fetch.fetch()

    assert [row.event_id for row in result.rows] == ["page-one-row", "page-two-row"]
    assert result.pages == 2
    assert transport.urls[1] == _link(2), "the provider's own link, followed verbatim"
    assert result.coverage is not None
    assert result.coverage.start == BASE + TICK
    assert result.coverage.end == BASE + 2 * TICK == clock.at
    assert result.failure is None


# ── Empty window ─────────────────────────────────────────────────────────────


def test_an_empty_window_claims_no_coverage():
    """Matrix + acceptance: ran and learned nothing, and says so.

    `harvest.py:144-150` refuses coverage on a harvest with no rows outright,
    so a fetch that claimed one here could not be turned into a `HarvestResult`
    at all — but the reason it must not is AD-35's: a window over an empty
    answer is a claim tied to the clock and to nothing else.
    """
    fetch, _, _, _, _ = _fetcher(_page())
    result = fetch.fetch()

    assert result.rows == ()
    assert result.coverage is None
    assert result.failure is None
    assert result.pages == 1, "the provider did answer — that is not the same as no attempt"
    assert result.walked_through == result.window.end


def test_rows_whose_clocks_cannot_be_believed_claim_no_coverage():
    """`8a`'s matrix row 2, which this connector omitted: *rows returned, no
    usable clock* → harvested something, and **no** coverage claimed.

    The rule is `gitlab.py`'s `_bounded_by_a_credible_clock`, and it has to be
    the same rule because both connectors fill the same `CoverageWindow | None`
    slot and `8a` answers the question once for both. Graph claimed a window for
    a page whose every timestamp was outside the range it had asked for —
    something arrived and nothing says when, and an unknowable start is not a
    guessable one.

    The rows are still emitted and still flagged. Withholding coverage is not
    refusing data: `33c` persists these and `PersistResult.flagged` counts them.
    """
    fetch, _, _, _, _ = _fetcher(
        _page(
            _event(
                id="from-the-future",
                start=_zoned("2126-09-07T10:00:00", "UTC"),
                end=_zoned("2126-09-07T11:00:00", "UTC"),
            ),
            _event(
                id="the-zero-value-parse",
                start=_zoned("1970-01-01T00:00:00", "UTC"),
                end=_zoned("1970-01-01T01:00:00", "UTC"),
            ),
        )
    )
    result = fetch.fetch()

    assert [row.event_id for row in result.rows] == ["from-the-future", "the-zero-value-parse"]
    assert all(row.clock_flag is not None for row in result.rows), (
        "the premise of the row: not one of these can be placed in time"
    )
    assert result.refusals == (), "flagged is not refused"
    assert result.failure is None, "the fetch itself worked perfectly"
    assert result.pages == 1
    assert result.coverage is None, (
        "coverage was claimed over a page whose every clock the connector itself "
        "had already disbelieved — a window tied to the local clock and to "
        "nothing else, which is the fabrication 8a exists to delete"
    )


def test_one_believable_clock_among_many_flagged_rows_is_enough_to_earn_coverage():
    """The other direction, which is what keeps the rule from being "any flag
    voids the fetch".

    `gitlab.py` returns on the *first* row it can place in time, and this is the
    same predicate: a tenant with one skewed meeting still had its calendar
    reached, and withholding coverage there would report a working connector as
    never having looked.
    """
    fetch, _, clock, _, _ = _fetcher(
        _page(
            _event(
                id="from-the-future",
                start=_zoned("2126-09-07T10:00:00", "UTC"),
                end=_zoned("2126-09-07T11:00:00", "UTC"),
            ),
            _event(id="ordinary"),
        )
    )
    result = fetch.fetch()

    assert result.coverage is not None
    assert (result.coverage.start, result.coverage.end) == (BASE + TICK, clock.at)


# ── Timezones ────────────────────────────────────────────────────────────────


def test_a_non_utc_iana_mailbox_zone_is_converted_before_the_row_is_emitted():
    """Matrix + acceptance: the case that otherwise refuses every event.

    09:00 in Europe/Berlin on 2026-09-07 is 07:00 UTC — a value, not a
    self-reference, so a fetcher that attached the wrong zone fails rather than
    agreeing with itself.
    """
    fetch, _, _, _, _ = _fetcher(
        _page(
            _event(
                start=_zoned("2026-09-07T09:00:00", "Europe/Berlin"),
                end=_zoned("2026-09-07T10:30:00", "Europe/Berlin"),
            )
        )
    )
    (row,) = fetch.fetch().rows

    assert row.start == datetime(2026, 9, 7, 7, 0, tzinfo=timezone.utc)
    assert row.end == datetime(2026, 9, 7, 8, 30, tzinfo=timezone.utc)
    assert row.start.utcoffset() == timedelta(0), "aware UTC, which is what the domain requires"
    assert row.duration == timedelta(minutes=90)
    assert row.source_timezone == "Europe/Berlin"


def test_a_windows_timezone_id_is_mapped_and_is_never_a_clock_fault():
    """Matrix: `"W. Europe Standard Time"` → IANA → UTC.

    The direction that matters is the *error*: `ZoneInfo` raises for this name,
    and routing that to `ImplausibleTimestamp` would flag every event in a
    Windows-defaulted tenant as a timestamp fault.
    """
    fetch, _, _, _, _ = _fetcher(
        _page(
            _event(
                start=_zoned("2026-09-07T09:00:00", "W. Europe Standard Time"),
                end=_zoned("2026-09-07T10:00:00", "W. Europe Standard Time"),
            )
        )
    )
    result = fetch.fetch()
    (row,) = result.rows

    assert row.start == datetime(2026, 9, 7, 7, 0, tzinfo=timezone.utc)
    assert row.clock_flag is None, "a Windows zone id is not an implausible clock"
    assert result.refusals == ()

    # And the refusal type, asserted directly on the resolver rather than
    # inferred from a row that did not refuse.
    with pytest.raises(UnresolvableTimezone):
        zone_of("Neither/A_Windows_Id_Nor_A_Zone")
    assert not isinstance(UnresolvableTimezone("x"), ImplausibleTimestamp)


def test_a_windows_id_arrives_in_whatever_case_the_mailbox_spells_it():
    """The map is keyed casefolded, because a zone id is display text."""
    assert zone_of("w. EUROPE standard TIME") == ZoneInfo("Europe/Berlin")


@pytest.mark.parametrize(("windows_id", "iana"), sorted(WINDOWS_TIMEZONES.items()))
def test_every_mapped_windows_zone_resolves_on_this_machine(windows_id, iana):
    """Every value in the map loads — otherwise a typo is a regional outage.

    A hand-written mapping table with one wrong IANA name refuses every event
    for one region and nothing else, which is the failure nobody notices. This
    is the assertion that makes the table's 139 rows evidence rather than a
    claim.
    """
    resolved = zone_of(windows_id)
    assert ZoneInfo(iana), "the mapped IANA name loads on this machine"
    for instant in (datetime(2026, 1, 15, 12), datetime(2026, 7, 15, 12)):
        # Both sides of a DST boundary, because a zone that agrees in January
        # and disagrees in July is the mapping error worth catching.
        assert instant.replace(tzinfo=resolved).utcoffset() == instant.replace(
            tzinfo=ZoneInfo(iana)
        ).utcoffset()


def test_a_zone_in_no_map_refuses_its_own_row_and_the_batch_continues():
    """Matrix: refused for that row, naming the zone; the batch continues."""
    fetch, _, _, _, _ = _fetcher(
        _page(
            _event(id="unreadable", start=_zoned("2026-09-07T09:00:00", "Middle/Earth")),
            _event(id="readable"),
        )
    )
    result = fetch.fetch()

    assert [row.event_id for row in result.rows] == ["readable"]
    (refusal,) = result.refusals
    assert refusal.event_id == "unreadable"
    assert "Middle/Earth" in refusal.reason, "the zone is named, or nobody can act on it"
    assert result.coverage is not None, "one unreadable zone does not void a real fetch"


def test_the_defensive_conversion_runs_even_though_the_prefer_header_was_sent():
    """Matrix: the provider ignores `Prefer` and answers in a local zone anyway.

    The header is honoured by the service, not by the protocol. This is the row
    that proves the conversion is not conditional on it — the request below
    carries `Prefer` and the answer is in Asia/Tokyo regardless.
    """
    fetch, transport, _, _, _ = _fetcher(
        _page(
            _event(
                start=_zoned("2026-09-07T18:00:00", "Asia/Tokyo"),
                end=_zoned("2026-09-07T19:00:00", "Asia/Tokyo"),
            )
        )
    )
    (row,) = fetch.fetch().rows

    assert transport.requests[0].headers["Prefer"] == PREFER_UTC
    assert row.source_timezone == "Asia/Tokyo", "the provider did not honour it"
    assert row.start == datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)


def test_the_prefer_header_is_asserted_on_the_recorded_request():
    """Matrix: on the request, never inferred from the rows.

    Every other criterion in this story observes output, so a fetcher that
    never sent this header would pass all of them. It is checked on each
    request, including a followed `nextLink`, because a client that set it once
    on the first call and dropped it on page two would answer page two in the
    mailbox's zone.
    """
    fetch, transport, _, _, _ = _fetcher(
        _page(_event(), next_link=_link(2)),
        _page(),
    )
    fetch.fetch()

    assert len(transport.requests) == 2
    for request in transport.requests:
        assert request.headers["Prefer"] == PREFER_UTC
        assert request.headers["Authorization"].startswith("Bearer ")


def test_a_request_never_prints_the_bearer_token_it_carries():
    """A `GraphRequest` in an assertion diff or a traceback is not a token leak."""
    printed = repr(GraphRequest(url="https://graph.microsoft.com/v1.0/me", headers={"Authorization": "Bearer s3cret"}))
    assert "s3cret" not in printed
    assert "Authorization" in printed, "which headers were sent is the assertable part"


# ── DST ──────────────────────────────────────────────────────────────────────


def test_a_dst_crossing_span_takes_its_duration_from_the_zoned_pair():
    """Matrix: never by adding a fixed offset.

    Europe/London ends summer time at 02:00 BST on 2026-10-25. A midnight-to-
    midnight span across it is **25** hours, and the naive arithmetic — parse
    both, subtract, call it a day — answers 24. Both instants and the duration
    are asserted, so a fetcher that got the right duration from two wrong
    instants fails too.
    """
    fetch, _, _, _, _ = _fetcher(
        _page(
            _event(
                id="all-day-across-the-transition",
                isAllDay=True,
                start=_zoned("2026-10-25T00:00:00", "Europe/London"),
                end=_zoned("2026-10-26T00:00:00", "Europe/London"),
            )
        ),
        policy=_policy(width=timedelta(days=90), first_run_reach_back=timedelta(days=90)),
    )
    (row,) = fetch.fetch().rows

    assert row.start == datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc), "BST, UTC+1"
    assert row.end == datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc), "GMT, UTC+0"
    assert row.duration == timedelta(hours=25)
    assert row.is_all_day is True, "carried, not interpreted — 33c decides what it costs"


# ── Throttling ───────────────────────────────────────────────────────────────


def test_a_retry_after_in_seconds_is_honoured_and_the_page_is_re_asked():
    """Matrix: honoured. The hint is waited out and the same page re-asked."""
    fetch, transport, _, _, waits = _fetcher(
        GraphResponse(status=429, headers={"Retry-After": "7"}),
        _page(_event()),
    )
    result = fetch.fetch()

    assert waits.seconds == [7.0], "the provider's own interval, not one invented here"
    assert transport.urls[0] == transport.urls[1], "the same page, re-asked"
    assert [row.event_id for row in result.rows] == ["AAMkAGI2-single"]
    assert result.failure is None


def test_a_throttle_after_page_one_returns_page_ones_rows_and_its_real_coverage():
    """Matrix + acceptance: returned **with** the failure, not discarded.

    The failure carries the hint, because `HarvestFailure.retry_after` is where
    a provider's own number belongs and AD-9 leaves the retry decision to the
    daemon.
    """
    fetch, _, clock, _, _ = _fetcher(
        _page(_event(id="page-one-row"), next_link=_link(2)),
        GraphResponse(status=429, headers={"Retry-After": "90"}),
        GraphResponse(status=429, headers={"Retry-After": "90"}),
        GraphResponse(status=429, headers={"Retry-After": "90"}),
    )
    result = fetch.fetch()

    assert [row.event_id for row in result.rows] == ["page-one-row"]
    assert result.coverage is not None
    assert result.coverage.start == BASE + TICK
    assert result.coverage.end == clock.at
    assert result.failure is not None
    assert result.failure.retryable is True
    assert result.failure.retry_after == timedelta(seconds=90)
    assert result.walked_through is None, (
        "the span was not walked to completion, so the cursor must not advance "
        "past meetings inside a range that was asked for and not answered"
    )


def test_a_429_with_no_hint_uses_the_stated_default_backoff():
    """Matrix: "honoured" is undefined without the header, so it is stated."""
    fetch, _, _, _, waits = _fetcher(
        GraphResponse(status=429),
        _page(_event()),
    )
    result = fetch.fetch()

    assert waits.seconds == [DEFAULT_THROTTLE_BACKOFF.total_seconds()]
    assert result.failure is None
    assert len(result.rows) == 1


def test_a_retry_after_http_date_is_parsed_rather_than_read_as_no_hint():
    """Matrix: a date value. `Retry-After` is defined as seconds **or** a date.

    A parser that handled only seconds would fall through to the 30-second
    default against a provider that said five minutes.
    """
    fetch, _, _, _, waits = _fetcher(
        GraphResponse(status=429, headers={"Retry-After": "Mon, 07 Sep 2026 12:05:00 GMT"}),
        _page(_event()),
    )
    result = fetch.fetch()

    assert waits.seconds == [240.0], (
        "12:05 GMT measured from the clock at the moment of the refusal, which "
        "is one tick past BASE — the hint is an instant, not an interval"
    )
    assert result.failure is None


def test_a_hint_longer_than_the_run_returns_what_was_walked_without_blocking():
    """Matrix: a delay past the run's budget returns rather than waiting it out.

    A background fetch that slept for an hour would still be holding when the
    next 240-minute cycle came round.
    """
    waits = Waits()
    fetch, _, clock, _, _ = _fetcher(
        _page(_event(id="page-one-row"), next_link=_link(2)),
        GraphResponse(status=429, headers={"Retry-After": "3600"}),
        waits=waits,
        budget=timedelta(minutes=10),
    )
    result = fetch.fetch()

    assert waits.seconds == [], "nothing was waited out"
    assert [row.event_id for row in result.rows] == ["page-one-row"]
    assert result.coverage is not None
    assert result.failure is not None
    assert result.failure.retryable is True
    assert result.failure.retry_after == timedelta(hours=1)


def test_a_throttle_is_its_own_refusal_type_carrying_the_hint():
    """So the fetcher does not have to read a status out of a message."""
    client = GraphClient(
        auth=Auth(),
        transport=Transport(GraphResponse(status=429, headers={"Retry-After": "3600"})),
        now=Clock(),
        budget=timedelta(minutes=10),
    )
    with pytest.raises(GraphThrottled) as throttled:
        client.page(f"{GRAPH_BASE}/me/calendarView")
    assert throttled.value.retry_after == timedelta(hours=1)
    assert throttled.value.retryable is True


def test_a_retry_after_in_the_past_is_zero_rather_than_negative():
    """A date already gone means "now", and a negative interval reads as nonsense."""
    client = GraphClient(auth=Auth(), transport=Transport(), now=Clock())
    assert client._retry_after(
        GraphResponse(status=429, headers={"retry-after": "Mon, 07 Sep 2026 11:00:00 GMT"})
    ) == timedelta(0)
    assert client._retry_after(
        GraphResponse(status=429, headers={"Retry-After": "-5"})
    ) == timedelta(0)


def test_a_retry_after_header_is_found_whatever_its_case():
    """HTTP header names are case-insensitive; a missed hint is a missed instruction."""
    fetch, _, _, _, waits = _fetcher(
        GraphResponse(status=429, headers={"retry-after": "4"}),
        _page(_event()),
    )
    fetch.fetch()
    assert waits.seconds == [4.0]


# ── The credential, mid-fetch ────────────────────────────────────────────────


def test_a_401_on_page_three_refreshes_once_and_keeps_the_earlier_pages():
    """Matrix: `33a`'s silent refresh, the page retried once, earlier pages retained."""

    def refuse_stale_token(request: GraphRequest) -> GraphResponse:
        if request.headers["Authorization"] == "Bearer token-1":
            return GraphResponse(status=401)
        return _page(_event(id="page-three-row", start=_zoned("2026-09-07T16:00:00", "UTC"), end=_zoned("2026-09-07T17:00:00", "UTC")))

    fetch, _, _, auth, _ = _fetcher(
        _page(_event(id="page-one-row"), next_link=_link(2)),
        _page(_event(id="page-two-row", start=_zoned("2026-09-07T14:00:00", "UTC"), end=_zoned("2026-09-07T15:00:00", "UTC")), next_link=_link(3)),
        refuse_stale_token,
        refuse_stale_token,
    )
    result = fetch.fetch()

    assert auth.forced == 1, "exactly one forced refresh, not one per page"
    assert [row.event_id for row in result.rows] == [
        "page-one-row",
        "page-two-row",
        "page-three-row",
    ]
    assert result.failure is None
    assert result.walked_through == result.window.end


def test_a_second_401_is_credential_stale_and_the_walked_pages_survive():
    """Matrix: `CredentialStale` after the retry.

    Not retryable: a credential the provider refuses on a freshly refreshed
    token does not clear by waiting, and reporting it as retryable is how a
    dead connector reads as patience (`harvest.py:71-102`).
    """
    fetch, _, _, auth, _ = _fetcher(
        _page(_event(id="page-one-row"), next_link=_link(2)),
        GraphResponse(status=401),
        GraphResponse(status=401),
    )
    result = fetch.fetch()

    assert auth.forced == 1
    assert [row.event_id for row in result.rows] == ["page-one-row"]
    assert result.coverage is not None, "page one was really fetched"
    assert result.failure is not None
    assert result.failure.retryable is False
    assert "401" in result.failure.reason
    assert result.walked_through is None


def test_the_client_raises_the_auth_packages_own_credential_stale():
    """One type for "the credential is the problem", wherever it was noticed."""
    client = GraphClient(
        auth=Auth(),
        transport=Transport(GraphResponse(status=401), GraphResponse(status=401)),
        now=Clock(),
    )
    with pytest.raises(CredentialStale):
        client.page(f"{GRAPH_BASE}/me/calendarView")


# ── Provider failures ────────────────────────────────────────────────────────


def test_a_5xx_is_a_failure_with_no_coverage_and_an_unmoved_cursor():
    """Matrix: provider error → failure outcome; no coverage, cursor unmoved."""
    fetch, _, _, _, _ = _fetcher(GraphResponse(status=503))
    result = fetch.fetch()

    assert result.rows == ()
    assert result.coverage is None
    assert result.walked_through is None
    assert result.failure is not None
    assert result.failure.retryable is True
    assert result.failure.retry_after is None, "no hint was given; none is invented"
    assert "503" in result.failure.reason


def test_a_403_mid_fetch_is_a_consent_change_and_never_enters_the_refresh_path():
    """Matrix: neither retryable nor a stale token.

    The assertion that matters is `auth.forced == 0`. A 403 routed through
    `33a`'s refresh-and-retry would report a perfectly good credential as
    stale and send the PM to enrol again against a permission nobody granted.
    """
    fetch, _, _, auth, _ = _fetcher(
        _page(_event(id="page-one-row"), next_link=_link(2)),
        GraphResponse(status=403, body={"error": {"code": "ErrorAccessDenied"}}),
    )
    result = fetch.fetch()

    assert auth.forced == 0, "a revoked permission is not a stale credential"
    assert [row.event_id for row in result.rows] == ["page-one-row"]
    assert result.failure is not None
    assert result.failure.retryable is False
    assert "403" in result.failure.reason
    assert result.coverage is not None, "page one was fetched before consent changed"


def test_a_403_is_its_own_refusal_type():
    """So a caller can tell it from every other 4xx without reading a string."""
    client = GraphClient(
        auth=Auth(), transport=Transport(GraphResponse(status=403)), now=Clock()
    )
    with pytest.raises(ConsentChanged):
        client.page(f"{GRAPH_BASE}/me/calendarView")


def test_a_provider_body_is_never_pasted_into_a_durable_failure_reason():
    """`HarvestFailure.reason` is written to Tier 2 and read back into reports.

    The sentence is composed from a status code and pm-ai's own words, so a
    provider that echoed a request detail into its error body cannot put it in
    a durable row.
    """
    fetch, _, _, _, _ = _fetcher(
        GraphResponse(status=500, body={"error": {"message": "token=leaked-secret-value"}})
    )
    result = fetch.fetch()

    assert result.failure is not None
    assert "leaked-secret-value" not in result.failure.reason


def test_a_failure_reason_names_the_calendar_span_and_not_the_skiptoken():
    """A `nextLink`'s `$skiptoken` is opaque provider state, like a cursor."""
    fetch, _, _, _, _ = _fetcher(
        _page(_event(), next_link=_link(2)),
        GraphResponse(status=503),
    )
    result = fetch.fetch()

    assert result.failure is not None
    assert "$skiptoken" not in result.failure.reason
    assert result.window.start.isoformat() in result.failure.reason


# ── The window ───────────────────────────────────────────────────────────────


def test_a_window_wider_than_one_request_is_split_and_every_span_is_walked():
    """Matrix: split into servable spans, every span walked.

    Split, never clamped. A clamp that quietly narrows the range leaves a
    permanent hole because the cursor advances past what was never asked for —
    which is why what is reported here is what was walked.
    """
    fetch, transport, _, _, _ = _fetcher(
        _page(_event(id="span-one")),
        _page(_event(id="span-two", start=_zoned("2026-09-08T10:00:00", "UTC"), end=_zoned("2026-09-08T11:00:00", "UTC"))),
        _page(_event(id="span-three", start=_zoned("2026-09-09T10:00:00", "UTC"), end=_zoned("2026-09-09T11:00:00", "UTC"))),
        policy=_policy(width=timedelta(days=1), first_run_reach_back=timedelta(days=2)),
        max_span=timedelta(days=1),
    )
    result = fetch.fetch()

    assert len(transport.requests) == 3, "three servable spans, three requests"
    assert [row.event_id for row in result.rows] == ["span-one", "span-two", "span-three"]
    assert result.walked_through == result.window.end
    spans = result.window.spans(max_span=timedelta(days=1))
    assert spans[0].start == result.window.start and spans[-1].end == result.window.end
    assert all(
        earlier.end == later.start for earlier, later in zip(spans, spans[1:])
    ), "contiguous: a gap between spans is a hole no later run revisits"


def test_a_span_that_cannot_be_served_is_a_failure_reporting_what_was_walked():
    """Matrix: failure outcome if a span cannot be served."""
    fetch, _, _, _, _ = _fetcher(
        _page(_event(id="span-one")),
        GraphResponse(status=500),
        policy=_policy(width=timedelta(days=1), first_run_reach_back=timedelta(days=2)),
        max_span=timedelta(days=1),
    )
    result = fetch.fetch()

    assert [row.event_id for row in result.rows] == ["span-one"]
    assert result.failure is not None
    first_span = result.window.spans(max_span=timedelta(days=1))[0]
    assert result.walked_through == first_span.end, (
        "the first span was walked to completion, so the cursor advances to its "
        "end and no further"
    )


def test_a_window_narrower_than_the_harvest_cycle_is_refused_at_construction():
    """Matrix: a configured width under 240 minutes → `ValueError`.

    Refused where it is configured rather than discovered as a gap months
    later: every run would leave a slice of calendar time the next run's window
    begins after, and the cursor would advance past it.
    """
    with pytest.raises(ValueError, match="harvest cycle"):
        WindowPolicy(
            width=HARVEST_CYCLE - timedelta(minutes=1), first_run_reach_back=timedelta(days=7)
        )
    assert WindowPolicy(width=HARVEST_CYCLE, first_run_reach_back=HARVEST_CYCLE).width == (
        HARVEST_CYCLE
    ), "exactly the cycle is permitted — the floor is the cycle, not more than it"


def test_the_window_policy_answers_nothing_the_ask_first_reserves():
    """Both widths are required, so no composition gets one by silence."""
    with pytest.raises(TypeError):
        WindowPolicy()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        WindowPolicy(width=WIDTH)  # type: ignore[call-arg]


def test_consecutive_runs_overlap_rather_than_abut_so_no_cycle_leaves_a_gap():
    """The property the 240-minute floor exists for, asserted as arithmetic."""
    policy = _policy(width=HARVEST_CYCLE)
    first = policy.window(now=BASE)
    second = policy.window(now=BASE + HARVEST_CYCLE, since=first.end)

    assert second.start <= first.end, "a gap here is calendar time nothing revisits"
    assert second.end > first.end


def test_a_since_older_than_the_overlap_widens_the_window_instead_of_being_skipped():
    """A laptop asleep for a week is a late fetch, not a hole."""
    policy = _policy()
    window = policy.window(now=BASE, since=BASE - timedelta(days=3))

    assert window.start == BASE - timedelta(days=3)
    assert window.end == BASE + WIDTH


def test_a_first_run_reaches_back_as_far_as_it_was_told_to():
    policy = _policy()
    assert policy.window(now=BASE).start == BASE - REACH_BACK


def test_a_reversed_or_empty_calendar_window_is_refused():
    """An empty range would be answered with nothing and read as an empty calendar."""
    with pytest.raises(ValueError, match="before its start"):
        CalendarWindow(start=BASE, end=BASE - timedelta(hours=1))
    with pytest.raises(ValueError, match="aware UTC"):
        CalendarWindow(start=BASE.replace(tzinfo=None), end=BASE)


# ── What the response body is allowed to decide ──────────────────────────────


def test_a_repeated_next_link_is_refused_rather_than_followed_forever():
    """Matrix: a provider loop, refused by the seen-link set."""
    fetch, transport, _, _, _ = _fetcher(
        _page(_event(id="page-one-row"), next_link=_link(2)),
        _page(next_link=_link(2)),
    )
    result = fetch.fetch()

    assert len(transport.requests) == 2, "the repeated link was never followed"
    assert [row.event_id for row in result.rows] == ["page-one-row"]
    assert result.failure is not None
    assert result.failure.retryable is False
    assert result.walked_through is None


def test_a_next_link_chain_is_refused_at_the_page_cap():
    """Matrix: refused at the page cap, whichever fires first.

    The seen-link set cannot catch a provider that hands back a *fresh* link
    every time, which is why both bounds exist.
    """
    fetch, transport, _, _, _ = _fetcher(
        _page(_event(id="one"), next_link=_link(2)),
        _page(_event(id="two", start=_zoned("2026-09-07T14:00:00", "UTC"), end=_zoned("2026-09-07T15:00:00", "UTC")), next_link=_link(3)),
        page_cap=2,
    )
    result = fetch.fetch()

    assert len(transport.requests) == 2 == result.pages
    assert result.failure is not None
    assert "cap" in result.failure.reason
    assert len(result.rows) == 2, "both walked pages keep their rows"


def test_an_off_host_next_link_is_refused_before_it_is_followed():
    """Matrix: a link to another origin is refused, not followed.

    The link arrives *inside the response body*, and following it would send
    the bearer token to whoever wrote it. The assertion is on the request list:
    the check has to happen before the socket, not after.
    """
    fetch, transport, _, _, _ = _fetcher(
        _page(_event(id="page-one-row"), next_link="https://graph.microsoft.com.evil.invalid/v1.0/next"),
    )
    result = fetch.fetch()

    assert len(transport.requests) == 1, "the off-host link was never requested"
    assert [row.event_id for row in result.rows] == ["page-one-row"]
    assert result.failure is not None
    assert result.failure.retryable is False


@pytest.mark.parametrize(
    "link",
    [
        "http://graph.microsoft.com/v1.0/next",
        "https://evil.invalid/v1.0/next",
        "https://graph.microsoft.com.evil.invalid/next",
        "file:///etc/passwd",
        "/v1.0/me/calendarView?$skiptoken=relative",
    ],
)
def test_the_origin_check_refuses_every_shape_of_elsewhere(link):
    """Scheme, host, a lookalike suffix, and a relative link with no origin at all."""
    client = GraphClient(auth=Auth(), transport=Transport(), now=Clock())
    with pytest.raises(GraphProtocolError):
        client._assert_on_host(link)


def test_a_next_link_that_is_not_a_url_stops_the_walk_by_refusing():
    """A walk that cannot tell where the next page is must not report completion."""
    fetch, _, _, _, _ = _fetcher(
        GraphResponse(status=200, body={"value": [_event()], "@odata.nextLink": 17}),
    )
    result = fetch.fetch()

    assert len(result.rows) == 1
    assert result.failure is not None
    assert result.walked_through is None


def test_a_body_that_is_not_a_json_object_is_refused_rather_than_read_as_empty():
    """A body pm-ai cannot parse is not evidence that the range held nothing."""
    fetch, _, _, _, _ = _fetcher(GraphResponse(status=200, body=None))
    result = fetch.fetch()

    assert result.rows == ()
    assert result.coverage is None
    assert result.failure is not None


def test_a_page_with_no_value_array_is_recorded_rather_than_silently_empty():
    """The one shape that would otherwise read as "the calendar is empty".

    The `walked_through` assertion is the load-bearing one, and it was the
    missing one: the walk ends *normally* here — a 200, no `nextLink`, no
    refusal from the client — so the cursor advanced to the end of a span that
    was asked for and never answered. That is what turns "pm-ai could not read
    this page" into "there were no meetings in this range", permanently, since
    no later run revisits a span the cursor has passed. Every other assertion
    below held while that was happening.
    """
    fetch, _, _, _, _ = _fetcher(GraphResponse(status=200, body={"@odata.context": "…"}))
    result = fetch.fetch()

    assert result.rows == ()
    assert result.failure is None
    (refusal,) = result.refusals
    assert refusal.event_id is None
    assert result.walked_through != result.window.end, (
        "the cursor advanced past a span whose page pm-ai could not read, which "
        "makes the unreadable page evidence of an empty calendar for good"
    )
    assert result.walked_through is None, "nothing was walked to completion"
    assert result.coverage is None, "and no row arrived, so nothing was covered"


def test_a_span_pm_ai_could_not_read_holds_the_cursor_back_past_later_spans_too():
    """`walked_through` is one high-water mark, so it cannot skip the hole.

    A window split into two spans, the first answered with a page that has no
    `value` array and the second answered perfectly. Advancing to the second
    span's end would carry the first span's hole past the cursor with it — the
    reason the fetch tracks "may the cursor still advance" rather than
    overwriting the mark per span.
    """
    fetch, transport, _, _, _ = _fetcher(
        GraphResponse(status=200, body={"@odata.context": "…"}),
        _page(_event(id="the-second-span")),
        policy=_policy(width=timedelta(days=1), first_run_reach_back=timedelta(days=1)),
        max_span=timedelta(days=1),
    )
    result = fetch.fetch()

    assert len(transport.requests) == 2, "the later span is still walked"
    assert [row.event_id for row in result.rows] == ["the-second-span"]
    assert result.failure is None
    assert len(result.refusals) == 1
    assert result.walked_through is None, (
        "the cursor advanced to the second span's end, which files the first "
        "span's unread page away as an empty range"
    )


# ── Rows pm-ai will not emit ─────────────────────────────────────────────────


def test_an_event_with_no_start_is_refused_by_name():
    """Matrix: refused, naming the event id — never emitted with a guessed time."""
    fetch, _, _, _, _ = _fetcher(
        _page(_event(id="no-start", start=None), _event(id="fine"))
    )
    result = fetch.fetch()

    assert [row.event_id for row in result.rows] == ["fine"]
    (refusal,) = result.refusals
    assert refusal.event_id == "no-start"
    assert "no-start" in refusal.reason


def test_an_event_with_no_id_is_refused_because_nothing_could_cite_it():
    fetch, _, _, _, _ = _fetcher(_page(_event(id=""), _event(id="fine")))
    result = fetch.fetch()

    assert [row.event_id for row in result.rows] == ["fine"]
    (refusal,) = result.refusals
    assert refusal.event_id is None
    assert "id" in refusal.reason


@pytest.mark.parametrize(
    "start",
    [
        None,
        {},
        {"timeZone": "UTC"},
        {"dateTime": "", "timeZone": "UTC"},
        {"dateTime": "not a datetime", "timeZone": "UTC"},
        "2026-09-07T10:00:00Z",
    ],
)
def test_every_unreadable_start_shape_is_a_malformed_row(start):
    """One refusal for "pm-ai cannot place this event in time", however it arrives."""
    with pytest.raises(MalformedCalendarRow):
        to_utc(start, field_name="start", event_id="an-event")


def test_a_start_years_in_the_future_is_flagged_and_the_batch_still_returns():
    """Matrix: flagged per AD-35; the batch still returns.

    Flagged, never backfilled from the local clock: a substituted timestamp is
    well-formed, plausible and wrong, and nothing downstream could tell it from
    a real one.
    """
    fetch, _, _, _, _ = _fetcher(
        _page(
            _event(
                id="from-the-future",
                start=_zoned("2126-09-07T10:00:00", "UTC"),
                end=_zoned("2126-09-07T11:00:00", "UTC"),
            ),
            _event(id="ordinary"),
        )
    )
    result = fetch.fetch()

    flagged = {row.event_id: row.clock_flag for row in result.rows}
    assert flagged["ordinary"] is None
    assert flagged["from-the-future"] is not None
    assert "2126" in flagged["from-the-future"]
    assert result.refusals == (), "flagged is not refused — the row is emitted"
    assert result.coverage is not None


def test_an_upcoming_meeting_is_not_flagged_merely_for_being_in_the_future():
    """The false-positive direction, which is the one that breaks a calendar.

    `validate_occurred_at` refuses anything more than five minutes ahead of its
    reference instant. Handing it `now` would flag every upcoming meeting in
    the tenant — and upcoming meetings are the half of a calendar `33c` writes
    records for.
    """
    fetch, _, _, _, _ = _fetcher(
        _page(
            _event(
                id="in-three-hours",
                start=_zoned("2026-09-07T15:00:00", "UTC"),
                end=_zoned("2026-09-07T16:00:00", "UTC"),
            )
        )
    )
    (row,) = fetch.fetch().rows

    assert row.start > BASE, "genuinely in the future"
    assert row.clock_flag is None


def test_a_zero_value_start_is_flagged_by_the_earliest_plausible_floor():
    """The other half of AD-35's rule: an absent field arriving as the epoch."""
    fetch, _, _, _, _ = _fetcher(
        _page(_event(id="epoch", start=_zoned("1970-01-01T00:00:00", "UTC"), end=_zoned("1970-01-01T01:00:00", "UTC")))
    )
    (row,) = fetch.fetch().rows

    assert row.clock_flag is not None
    assert row.start == datetime(1970, 1, 1, tzinfo=timezone.utc), "carried, not replaced"


def test_a_span_that_runs_backwards_is_flagged_rather_than_reordered():
    """A negative duration would be carried into `33c`'s cost arithmetic."""
    fetch, _, _, _, _ = _fetcher(
        _page(
            _event(
                id="backwards",
                start=_zoned("2026-09-07T11:00:00", "UTC"),
                end=_zoned("2026-09-07T10:00:00", "UTC"),
            )
        )
    )
    (row,) = fetch.fetch().rows

    assert row.clock_flag is not None
    assert row.duration == timedelta(hours=-1), "the provider's own pair, unreordered"


# ── The fields 33c reads ─────────────────────────────────────────────────────


def test_a_row_carries_the_provider_fields_33c_cannot_re_derive():
    """Categories, attendees, cancellation and the series ids, verbatim.

    `33c` maps a category to a project scope and counts attendees; `33e` needs
    `seriesMasterId` and `occurrenceId`, because slice 0 measured one join URL
    resolving to one meeting that holds every occurrence's transcript. None of
    it can be recovered once the raw payload is gone.
    """
    fetch, _, _, _, _ = _fetcher(
        _page(
            _event(
                id="occurrence",
                isCancelled=True,
                type="occurrence",
                seriesMasterId="the-series",
                occurrenceId="OID.the-series.2026-09-07",
                categories=["Platform", "  ", "Steering"],
                responseStatus={"response": "tentativelyAccepted", "time": "2026-09-01T09:00:00Z"},
                attendees=[
                    {
                        "type": "required",
                        "status": {"response": "accepted", "time": "2026-09-01T09:00:00Z"},
                        "emailAddress": {"name": "B Person", "address": "b@example.invalid"},
                    },
                    {"type": "resource", "status": None, "emailAddress": None},
                ],
            )
        )
    )
    (row,) = fetch.fetch().rows

    assert row.is_cancelled is True
    assert row.kind == "occurrence"
    assert row.series_master_id == "the-series"
    assert row.occurrence_id == "OID.the-series.2026-09-07"
    assert row.categories == ("Platform", "Steering"), "blanks dropped, order kept"
    assert row.response == "tentativelyAccepted"
    assert row.organizer is not None and row.organizer.email == "a@example.invalid"
    assert [(a.email, a.kind) for a in row.attendees] == [
        ("b@example.invalid", "required"),
        (None, "resource"),
    ], "a null emailAddress survives as None — 33c resolves it to UNRESOLVED"


def test_a_room_booking_with_no_attendees_records_none_rather_than_refusing():
    fetch, _, _, _, _ = _fetcher(_page(_event(attendees=[], categories=None)))
    (row,) = fetch.fetch().rows

    assert row.attendees == ()
    assert row.categories == ()


# ── The request, and the one method this connector has ───────────────────────


def test_the_calendar_view_request_carries_the_range_and_nothing_unmeasured():
    """`$top` and `$select` are deliberately absent.

    Slice 0 measured three sibling collections disagreeing about whether `$top`
    is even accepted, and concluded a paging option is a per-endpoint fact
    rather than a Graph-wide convention. What it exercised on `calendarView` is
    a plain range request, so that is what is sent.
    """
    fetch, transport, _, _, _ = _fetcher(_page(_event()))
    result = fetch.fetch()
    url = transport.urls[0]

    assert url.startswith(f"{GRAPH_BASE}/me/calendarView?")
    assert "startDateTime=2026-08-31T12:00:00Z" in url, "a first run's reach-back"
    assert "endDateTime=2026-09-07T20:00:00Z" in url
    assert "$top" not in url and "$select" not in url
    assert "%2B" not in url and "+" not in url, (
        "a literal + in a query string is a space, so the offset spelling is "
        "avoided rather than encoded"
    )
    assert result.window == CalendarWindow(start=BASE - REACH_BACK, end=BASE + WIDTH)


def test_a_naive_range_bound_is_refused_before_a_request_is_built():
    """Graph reads a naive `startDateTime` in the mailbox's own timezone."""
    client = GraphClient(auth=Auth(), transport=Transport(), now=Clock())
    with pytest.raises(GraphProtocolError, match="aware UTC"):
        client.calendar_view(BASE.replace(tzinfo=None), BASE)


def test_the_fetch_budget_stops_a_walk_that_outlives_its_run():
    """The bound the page cap does not cover: a provider answering slowly.

    Retryable, and the pages already walked keep their coverage — the next run
    resumes from the span this one did not finish rather than the window being
    reported complete.
    """
    fetch, _, _, _, _ = _fetcher(
        _page(_event(id="page-one-row"), next_link=_link(2)),
        _page(),
        budget=TICK,
    )
    result = fetch.fetch()

    assert [row.event_id for row in result.rows] == ["page-one-row"]
    assert result.coverage is not None
    assert result.failure is not None
    assert result.failure.retryable is True
    assert "budget" in result.failure.reason


def test_an_auth_adapter_that_cannot_get_a_token_is_a_value_not_a_raise():
    """A fetch reports; it never raises. The coverage a partial walk earned
    cannot travel up a stack as an exception (`harvest.py:42-54`).
    """
    auth = Auth()
    auth.raises = CredentialStale("nothing is enrolled")
    fetch, _, _, _, _ = _fetcher(auth=auth)
    result = fetch.fetch()

    assert result.rows == ()
    assert result.coverage is None
    assert result.failure is not None
    assert result.failure.retryable is False


def test_a_transport_that_explodes_is_a_failure_rather_than_a_traceback():
    """Anything a transport can throw becomes a value, or the spans already
    walked lose their coverage on the way up.
    """

    def explode(request: GraphRequest) -> GraphResponse:
        raise RuntimeError("the socket layer did something unexpected")

    fetch, _, _, _, _ = _fetcher(_page(_event(id="page-one-row"), next_link=_link(2)), explode)
    result = fetch.fetch()

    assert [row.event_id for row in result.rows] == ["page-one-row"]
    assert result.coverage is not None
    assert result.failure is not None
    assert result.failure.retryable is True, (
        "a transport that raised is a request that got no answer, and the "
        "client cannot tell a socket error from a bug in an injected callable "
        "— nothing was learned, so waiting is the honest remedy"
    )


def test_this_slice_emits_no_events_and_no_meeting_records():
    """The story's `Never`, asserted on the import graph rather than described.

    `33c` owns `Meeting` and `CALENDAR_EVENT_HELD`, and a fetcher that started
    emitting either would widen this slice's blast radius without a spec
    change. The import list is where that shows up first — and it is checked as
    a parsed import rather than as a string, so a module that only *mentions*
    the boundary in a docstring still passes.
    """
    import ast
    import pathlib

    import pm_ai.connectors.graph.calendar as calendar

    tree = ast.parse(pathlib.Path(calendar.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported |= {f"{node.module}.{alias.name}" for alias in node.names}
        elif isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}

    assert "pm_ai.domain.events" not in imported, "no NormalizedEvent, no event type"
    assert "pm_ai.domain.meetings" not in imported, "no Meeting record"
    assert "pm_ai.ports" not in imported, "no ConnectorPort implementation"
    assert {name for name in imported if name.startswith("pm_ai.storage")} == set()


def test_no_graph_response_in_this_file_came_from_a_socket():
    """The structural half of "no network in any test".

    `GraphClient.transport` has a real default, so this asserts the thing that
    would actually go wrong: every client built here is built with a scripted
    transport, and `Transport` refuses a request the fixture has no answer for
    rather than falling through to one.
    """
    fetch, transport, _, _, _ = _fetcher(_page(_event()))
    assert fetch.client.transport is transport
    fetch.fetch()
    with pytest.raises(AssertionError, match="the fixture does not have"):
        transport(GraphRequest(url=f"{GRAPH_BASE}/me/calendarView"))
