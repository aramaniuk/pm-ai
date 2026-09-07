"""`calendarView`, paged and converted to aware UTC exactly once (story 33b).

What comes back from Graph is not a time. It is a naive string and a zone name in
a separate field:

    start.dateTime = "2026-01-02T09:00:00.0000000"     start.timeZone = "UTC"

Slice 0 measured that shape against a live tenant, including the seven
fractional digits, and measured the failure mode: `datetime.fromisoformat`
accepts the string and returns `tzinfo=None`. So an unconverted value is not a
parse error that announces itself — it is a naive datetime that silently reads
as local time, and `validate_occurred_at` refuses anything that is not aware UTC
for *every event in the tenant*. Converting here, once, is the whole job of this
module.

## Two clocks, and neither one may stand in for the other (AD-35)

- The **`calendarView` range** is calendar time: which meetings were asked for.
  It is what the cursor advances through and what the request carries.
- **`CoverageWindow`** is `ingested_at`: what the daemon did, on this machine's
  clock, across the fetch (`lifecycle.py:157-164`).

Reporting the calendar range as coverage is the mixed-clock defect AD-35 exists
to forbid, wearing this story's words. What the range decides is *whether*
coverage was earned — the server answered for it — not its bounds.

## A zone Graph names is not always a zone `ZoneInfo` knows

A mailbox whose default is a Windows timezone answers with a Windows id:
`"W. Europe Standard Time"`, for which `ZoneInfo` raises
`ZoneInfoNotFoundError`. That is emphatically **not** an implausible clock, and
routing it there would flag every event in the tenant as a timestamp fault. It
is its own refusal, after `WINDOWS_TIMEZONES` has been consulted.

## What "implausible" means for a calendar, and why it is not the usual test

`validate_occurred_at` refuses a timestamp more than five minutes ahead of its
reference instant. A calendar is *full* of legitimately future timestamps — an
upcoming meeting is the point — so passing `now` as the reference instant would
flag every one of them.

The reference instant used here is the far edge of the range the server was
asked for. That turns the domain guard into exactly the question worth asking:
did the provider hand back a start outside the window pm-ai requested? A start
years in the future fails it; a meeting three hours out does not; and the
`EARLIEST_PLAUSIBLE` floor still catches the zero-value parse, which is the
other half of AD-35's rule. Flagged and carried on the row, never dropped and
never replaced.

## What this module does not do

No `Meeting` records, no `CALENDAR_EVENT_HELD`, no `ConnectorPort` — `33c` owns
all three, and reads the rows this module returns.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from pm_ai.connectors.graph.auth import GraphAuthError, GraphUnreachable
from pm_ai.connectors.graph.client import GraphCallFailed, GraphClient
from pm_ai.domain.clocks import ImplausibleTimestamp, validate_occurred_at
from pm_ai.domain.harvest import HarvestFailure
from pm_ai.domain.lifecycle import CoverageWindow

__all__ = [
    "CalendarAttendee",
    "CalendarFetch",
    "CalendarRow",
    "CalendarWindow",
    "GraphCalendarFetch",
    "HARVEST_CYCLE",
    "MAX_SERVABLE_SPAN",
    "MalformedCalendarRow",
    "RowRefusal",
    "UnresolvableTimezone",
    "WINDOWS_TIMEZONES",
    "WindowPolicy",
    "to_utc",
]


# ── Refusals ─────────────────────────────────────────────────────────────────


class CalendarRowRefused(ValueError):
    """One row could not be read, and the batch continues without it.

    A base rather than two unrelated exceptions, because the fetcher's handling
    of both is identical — record it, name the row, keep walking — while the
    *diagnosis* the operator needs is different, which is what the two
    subclasses are for.

    A `ValueError` because the provider's data is what is wrong, matching
    `ImplausibleTimestamp`'s choice one layer down.
    """


class MalformedCalendarRow(CalendarRowRefused):
    """An event pm-ai cannot place in time at all — no `start`, or no id.

    Refused rather than emitted with a guessed time. A substituted timestamp is
    indistinguishable from a real one, which is the rule `clocks.py` is built
    around; a substituted *id* is worse, because `33c` writes a record under it.
    """


class UnresolvableTimezone(CalendarRowRefused):
    """The zone Graph named is in no map this machine can resolve.

    Its own refusal, and deliberately not `ImplausibleTimestamp`. A mailbox with
    a Windows default answers `"W. Europe Standard Time"`; reading that as a
    clock fault would flag every event in the tenant, and the remedy — install
    `tzdata`, or add the id to `WINDOWS_TIMEZONES` — has nothing to do with
    anybody's clock.
    """


# ── Time ─────────────────────────────────────────────────────────────────────

HARVEST_CYCLE = timedelta(minutes=240)
"""CAP-2's cycle: "a harvest cycle runs every 240 minutes (±15)".

The floor under `WindowPolicy.width`, and the reason that floor exists: a window
narrower than the cycle leaves, on every single run, a slice of calendar time
that the next run's window starts *after*. The cursor advances past it and no
later run ever revisits it.
"""

MAX_SERVABLE_SPAN = timedelta(days=30)
"""The widest range this connector will ask `calendarView` for in one request.

**A choice, not a measurement.** No slice of this project has measured where
Graph refuses a `calendarView` range, so this is pm-ai's own request ceiling
rather than a claim about the provider — which is why it is injectable: a
measured limit replaces it without touching this module.

What it buys regardless of the provider's real limit is a bounded first run. A
first harvest reaching months back becomes a handful of requests that are each
walked to completion, and a span that cannot be served is a failure naming that
span rather than a whole window silently narrowed.
"""

_FRACTION_DIGITS = 6
"""How many fractional digits `datetime.fromisoformat` is asked to parse.

Graph sends seven. Slice 0 measured `fromisoformat` accepting them on 3.14, and
this package supports `>=3.13`, so the truncation below is belt-and-braces
rather than a fix for a known break — the cost is one string slice and the
alternative is a parse that depends on a minor version.
"""


WINDOWS_TIMEZONES: Mapping[str, str] = {
    # CLDR's `windowsZones` mapping, territory `001` — one IANA zone per Windows
    # id. Keys are casefolded, because a mailbox's zone id is display text and
    # is not guaranteed to arrive in any particular case.
    #
    # Held as data rather than pulled from a package for the reason the runtime
    # extras are pinned one at a time: `tzdata` ships the IANA database and no
    # Windows mapping at all, and the packages that do ship one are heavier than
    # the ~140 rows below. Every value is asserted loadable by this slice's
    # tests, so a typo here fails rather than becoming an `UnresolvableTimezone`
    # for one region.
    "dateline standard time": "Etc/GMT+12",
    "utc-11": "Etc/GMT+11",
    "aleutian standard time": "America/Adak",
    "hawaiian standard time": "Pacific/Honolulu",
    "marquesas standard time": "Pacific/Marquesas",
    "alaskan standard time": "America/Anchorage",
    "utc-09": "Etc/GMT+9",
    "pacific standard time (mexico)": "America/Tijuana",
    "utc-08": "Etc/GMT+8",
    "pacific standard time": "America/Los_Angeles",
    "us mountain standard time": "America/Phoenix",
    "mountain standard time (mexico)": "America/Mazatlan",
    "mountain standard time": "America/Denver",
    "yukon standard time": "America/Whitehorse",
    "central america standard time": "America/Guatemala",
    "central standard time": "America/Chicago",
    "easter island standard time": "Pacific/Easter",
    "central standard time (mexico)": "America/Mexico_City",
    "canada central standard time": "America/Regina",
    "sa pacific standard time": "America/Bogota",
    "eastern standard time (mexico)": "America/Cancun",
    "eastern standard time": "America/New_York",
    "haiti standard time": "America/Port-au-Prince",
    "cuba standard time": "America/Havana",
    "us eastern standard time": "America/Indiana/Indianapolis",
    "turks and caicos standard time": "America/Grand_Turk",
    "paraguay standard time": "America/Asuncion",
    "atlantic standard time": "America/Halifax",
    "venezuela standard time": "America/Caracas",
    "central brazilian standard time": "America/Cuiaba",
    "sa western standard time": "America/La_Paz",
    "pacific sa standard time": "America/Santiago",
    "newfoundland standard time": "America/St_Johns",
    "tocantins standard time": "America/Araguaina",
    "e. south america standard time": "America/Sao_Paulo",
    "sa eastern standard time": "America/Cayenne",
    "argentina standard time": "America/Argentina/Buenos_Aires",
    "greenland standard time": "America/Godthab",
    "montevideo standard time": "America/Montevideo",
    "magallanes standard time": "America/Punta_Arenas",
    "saint pierre standard time": "America/Miquelon",
    "bahia standard time": "America/Bahia",
    "utc-02": "Etc/GMT+2",
    "mid-atlantic standard time": "Etc/GMT+2",
    "azores standard time": "Atlantic/Azores",
    "cape verde standard time": "Atlantic/Cape_Verde",
    "utc": "Etc/UTC",
    "gmt standard time": "Europe/London",
    "greenwich standard time": "Atlantic/Reykjavik",
    "sao tome standard time": "Africa/Sao_Tome",
    "morocco standard time": "Africa/Casablanca",
    "w. europe standard time": "Europe/Berlin",
    "central europe standard time": "Europe/Budapest",
    "romance standard time": "Europe/Paris",
    "central european standard time": "Europe/Warsaw",
    "w. central africa standard time": "Africa/Lagos",
    "gtb standard time": "Europe/Bucharest",
    "middle east standard time": "Asia/Beirut",
    "egypt standard time": "Africa/Cairo",
    "e. europe standard time": "Europe/Chisinau",
    "west bank standard time": "Asia/Hebron",
    "south africa standard time": "Africa/Johannesburg",
    "fle standard time": "Europe/Kiev",
    "israel standard time": "Asia/Jerusalem",
    "south sudan standard time": "Africa/Juba",
    "kaliningrad standard time": "Europe/Kaliningrad",
    "sudan standard time": "Africa/Khartoum",
    "libya standard time": "Africa/Tripoli",
    "namibia standard time": "Africa/Windhoek",
    "jordan standard time": "Asia/Amman",
    "arabic standard time": "Asia/Baghdad",
    "turkey standard time": "Europe/Istanbul",
    "arab standard time": "Asia/Riyadh",
    "belarus standard time": "Europe/Minsk",
    "russian standard time": "Europe/Moscow",
    "e. africa standard time": "Africa/Nairobi",
    "volgograd standard time": "Europe/Volgograd",
    "iran standard time": "Asia/Tehran",
    "arabian standard time": "Asia/Dubai",
    "astrakhan standard time": "Europe/Astrakhan",
    "azerbaijan standard time": "Asia/Baku",
    "russia time zone 3": "Europe/Samara",
    "mauritius standard time": "Indian/Mauritius",
    "saratov standard time": "Europe/Saratov",
    "georgian standard time": "Asia/Tbilisi",
    "caucasus standard time": "Asia/Yerevan",
    "afghanistan standard time": "Asia/Kabul",
    "west asia standard time": "Asia/Tashkent",
    "ekaterinburg standard time": "Asia/Yekaterinburg",
    "pakistan standard time": "Asia/Karachi",
    "qyzylorda standard time": "Asia/Qyzylorda",
    "india standard time": "Asia/Calcutta",
    "sri lanka standard time": "Asia/Colombo",
    "nepal standard time": "Asia/Katmandu",
    "central asia standard time": "Asia/Almaty",
    "bangladesh standard time": "Asia/Dhaka",
    "omsk standard time": "Asia/Omsk",
    "myanmar standard time": "Asia/Rangoon",
    "se asia standard time": "Asia/Bangkok",
    "altai standard time": "Asia/Barnaul",
    "w. mongolia standard time": "Asia/Hovd",
    "north asia standard time": "Asia/Krasnoyarsk",
    "n. central asia standard time": "Asia/Novosibirsk",
    "tomsk standard time": "Asia/Tomsk",
    "china standard time": "Asia/Shanghai",
    "north asia east standard time": "Asia/Irkutsk",
    "singapore standard time": "Asia/Singapore",
    "w. australia standard time": "Australia/Perth",
    "taipei standard time": "Asia/Taipei",
    "ulaanbaatar standard time": "Asia/Ulaanbaatar",
    "aus central w. standard time": "Australia/Eucla",
    "transbaikal standard time": "Asia/Chita",
    "tokyo standard time": "Asia/Tokyo",
    "north korea standard time": "Asia/Pyongyang",
    "korea standard time": "Asia/Seoul",
    "yakutsk standard time": "Asia/Yakutsk",
    "cen. australia standard time": "Australia/Adelaide",
    "aus central standard time": "Australia/Darwin",
    "e. australia standard time": "Australia/Brisbane",
    "aus eastern standard time": "Australia/Sydney",
    "west pacific standard time": "Pacific/Port_Moresby",
    "tasmania standard time": "Australia/Hobart",
    "vladivostok standard time": "Asia/Vladivostok",
    "lord howe standard time": "Australia/Lord_Howe",
    "bougainville standard time": "Pacific/Bougainville",
    "russia time zone 10": "Asia/Srednekolymsk",
    "magadan standard time": "Asia/Magadan",
    "norfolk standard time": "Pacific/Norfolk",
    "sakhalin standard time": "Asia/Sakhalin",
    "central pacific standard time": "Pacific/Guadalcanal",
    "russia time zone 11": "Asia/Kamchatka",
    "new zealand standard time": "Pacific/Auckland",
    "utc+12": "Etc/GMT-12",
    "fiji standard time": "Pacific/Fiji",
    "chatham islands standard time": "Pacific/Chatham",
    "utc+13": "Etc/GMT-13",
    "tonga standard time": "Pacific/Tongatapu",
    "samoa standard time": "Pacific/Apia",
    "line islands standard time": "Pacific/Kiritimati",
}
"""Windows timezone id (casefolded) to the IANA zone CLDR calls its default."""

_UTC_SPELLINGS = frozenset({"utc", "z", "gmt", "etc/utc", "etc/gmt", "utc+00:00"})
"""Every way the one zone that needs no database can arrive.

Short-circuited before `ZoneInfo` so the ordinary case — the `Prefer` header
honoured — needs no timezone database at all. A machine with no `tzdata` and no
system zoneinfo still harvests a UTC mailbox correctly, and only a non-UTC one
refuses, which is a far better failure than every row refusing.
"""


def zone_of(name: str | None) -> tzinfo:
    """The `tzinfo` for a zone name Graph sent, in either naming scheme.

    Order is deliberate: the UTC spellings first (no database needed at all),
    then the Windows map, then IANA. The Windows map is consulted before
    `ZoneInfo` because a Windows id can never be an IANA key — IANA names carry
    no spaces — so trying `ZoneInfo` first only costs a failed filesystem lookup
    on every event in a Windows-defaulted tenant.

    Raises `UnresolvableTimezone` for a name in neither scheme, and for a
    mapped IANA name this machine has no database for. The second is worth
    distinguishing in the message even though it is the same exception: "install
    `tzdata`" and "pm-ai has never heard of this zone" are different remedies.
    """
    stated = (name or "").strip()
    if not stated:
        raise UnresolvableTimezone(
            "the event carries a dateTime with no timeZone beside it, so the "
            "instant it names cannot be determined. Refused rather than assumed "
            "to be UTC: assuming is how a meeting lands an hour out and nothing "
            "says so."
        )
    if stated.casefold() in _UTC_SPELLINGS:
        return timezone.utc
    mapped = WINDOWS_TIMEZONES.get(stated.casefold())
    if mapped is not None:
        try:
            return ZoneInfo(mapped)
        except Exception as absent:  # noqa: BLE001 — every failure is one fact
            raise UnresolvableTimezone(
                f"the mailbox's timezone {stated!r} maps to the IANA zone "
                f"{mapped!r}, which this machine has no timezone database for "
                f"({type(absent).__name__}). Install the `tzdata` extra — this "
                f"is a missing database rather than an implausible clock, and "
                f"it refuses one row rather than flagging every event in the "
                f"tenant."
            ) from absent
    try:
        return ZoneInfo(stated)
    except Exception as unknown:  # noqa: BLE001 — see the docstring
        raise UnresolvableTimezone(
            f"the mailbox's timezone {stated!r} is neither a Windows id pm-ai "
            f"maps nor a zone this machine's timezone database holds "
            f"({type(unknown).__name__}). The row is refused by name and the "
            f"batch continues; add the id to WINDOWS_TIMEZONES, or install "
            f"`tzdata`."
        ) from unknown


def to_utc(stamp: Mapping[str, Any] | None, *, field_name: str, event_id: str) -> datetime:
    """One Graph `{dateTime, timeZone}` pair, as the aware UTC instant it names.

    Converted defensively even though the request carried
    `Prefer: outlook.timezone="UTC"`. The header is honoured by the service, not
    by the protocol, and the failure mode when it is ignored is not a wrong time
    — it is `validate_occurred_at` refusing every row, which reads as "the
    connector is broken" rather than "the header did not apply".

    A value that already carries an offset (a trailing `Z`, or `+02:00`) is
    believed over the `timeZone` field beside it: an explicit offset is the
    instant, stated.
    """
    if not isinstance(stamp, Mapping):
        raise MalformedCalendarRow(
            f"event {event_id!r} has no {field_name} pm-ai can read "
            f"({type(stamp).__name__}). Refused rather than emitted with a "
            f"guessed time."
        )
    raw = stamp.get("dateTime")
    if not isinstance(raw, str) or not raw.strip():
        raise MalformedCalendarRow(
            f"event {event_id!r} carries a {field_name} with no dateTime "
            f"string, so there is no instant to convert. Refused rather than "
            f"emitted with a guessed time."
        )
    naive_or_aware = _parsed(raw.strip(), field_name=field_name, event_id=event_id)
    if naive_or_aware.tzinfo is not None:
        return naive_or_aware.astimezone(timezone.utc)
    return naive_or_aware.replace(tzinfo=zone_of(stamp.get("timeZone"))).astimezone(timezone.utc)


def _parsed(raw: str, *, field_name: str, event_id: str) -> datetime:
    """`fromisoformat`, with Graph's seven fractional digits trimmed to six.

    Only the leading run of digits after the separator is the fraction. A naive
    "keep every digit" trim would eat the offset too, turning
    `…:00.0000000+02:00` into `…:00.000000` and losing two hours silently —
    which is the class of bug this whole module exists to prevent, introduced by
    the fix for another one.
    """
    text = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    if "." in text:
        head, _, tail = text.partition(".")
        span = 0
        while span < len(tail) and tail[span].isdigit():
            span += 1
        digits, remainder = tail[:span], tail[span:]
        text = f"{head}.{digits[:_FRACTION_DIGITS]}{remainder}" if digits else head + remainder
    try:
        return datetime.fromisoformat(text)
    except ValueError as unparsed:
        raise MalformedCalendarRow(
            f"event {event_id!r} carries a {field_name} pm-ai cannot parse as "
            f"a datetime ({unparsed}). Refused rather than emitted with a "
            f"guessed time."
        ) from unparsed


# ── The window pm-ai asks for ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CalendarWindow:
    """A range of **calendar** time, in aware UTC. Never a coverage window.

    Two instants and nothing else, because the mistake this type exists to make
    impossible is spending it as evidence: `CoverageWindow` describes what the
    daemon did and is built from this machine's clock across the fetch, while
    this describes which meetings were asked for.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        for name, value in (("start", self.start), ("end", self.end)):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError(
                    f"CalendarWindow.{name}={value!r} is not aware UTC. The "
                    f"range goes into a request Graph reads as absolute, and a "
                    f"naive bound would be interpreted in the mailbox's own "
                    f"timezone."
                )
        if self.end <= self.start:
            raise ValueError(
                f"CalendarWindow ends at {self.end.isoformat()}, at or before "
                f"its start {self.start.isoformat()}. An empty or reversed "
                f"range would be requested, answered with nothing, and read as "
                f"a calendar with no meetings in it."
            )

    @property
    def width(self) -> timedelta:
        return self.end - self.start

    def spans(self, *, max_span: timedelta = MAX_SERVABLE_SPAN) -> tuple[CalendarWindow, ...]:
        """This window as spans no wider than `max_span`, in order, with no gaps.

        Split, never clamped. The two were named as alternatives in an earlier
        draft of this story and they are opposites: a clamp that quietly narrows
        the range leaves a permanent hole, because the cursor advances past what
        was never asked for. Every span here is walked, and what is reported is
        what was walked.
        """
        if max_span <= timedelta(0):
            raise ValueError(
                f"max_span={max_span!r} is not a positive interval, so the "
                f"window could not be split into anything that advances."
            )
        spans: list[CalendarWindow] = []
        edge = self.start
        while edge < self.end:
            stop = min(edge + max_span, self.end)
            spans.append(CalendarWindow(start=edge, end=stop))
            edge = stop
        return tuple(spans)


@dataclass(frozen=True, slots=True)
class WindowPolicy:
    """How wide a harvest window is, and how far back the first one reaches.

    **Both fields are required.** This story's `Ask First` leaves the two widths
    to the human, so there is no default: a default would be this module
    answering a question the spec reserves, and the answer would then be
    invisible in every composition that accepted it by silence — the same
    reasoning that removed `GraphDeviceCodeAuth.store`'s default.

    One constraint is not the human's, and it is enforced here rather than
    documented: `width` may never be under CAP-2's 240-minute cycle.
    """

    width: timedelta
    """The steady-state half-width: how far back and how far forward each run reaches.

    Forward as well as back, because a calendar's useful half is in the future —
    `33c` writes upcoming meetings as records — and backward on *every* run, not
    only the first, so a meeting cancelled or moved after it was harvested is
    seen again within one cycle.
    """

    first_run_reach_back: timedelta
    """How far into the past a machine with no cursor reaches.

    Separate from `width` because it is a one-off: the first run has no previous
    window to continue from, and how much history the PM wants is a judgement
    nothing in the code can make.
    """

    def __post_init__(self) -> None:
        if self.width < HARVEST_CYCLE:
            raise ValueError(
                f"WindowPolicy.width={self.width} is narrower than CAP-2's "
                f"{HARVEST_CYCLE} harvest cycle. Every run would leave a slice "
                f"of calendar time that the next run's window begins after, and "
                f"the cursor would advance past it — a permanent hole rather "
                f"than a late fetch. Widen the window, or slow the cycle."
            )
        if self.first_run_reach_back < self.width:
            raise ValueError(
                f"WindowPolicy.first_run_reach_back="
                f"{self.first_run_reach_back} is shorter than the "
                f"{self.width} every later run reaches back anyway, so the "
                f"first harvest would cover less history than the second."
            )

    def window(self, *, now: datetime, since: datetime | None = None) -> CalendarWindow:
        """The range to ask for, given this machine's clock and its cursor.

        `since` is the calendar instant a previous run walked through — **not**
        an `ingested_at` watermark. `None` is a first run.

        The window always ends at `now + width` and always begins at least
        `width` in the past, so consecutive runs overlap rather than abut: with
        `width` at or above the cycle, two runs a cycle apart cannot leave a gap
        between them, and a cancellation is re-read within one cycle. A `since`
        older than that — a laptop asleep for a week — widens the window instead
        of being skipped, which is what turns a missed cycle into a late fetch
        rather than a hole.
        """
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError(
                f"now={now!r} is not aware UTC. The window is a request Graph "
                f"reads as absolute, so the clock it is derived from has to be "
                f"unambiguous."
            )
        overlap = now - self.width
        if since is None:
            start = now - self.first_run_reach_back
        else:
            if since.tzinfo is None or since.utcoffset() != timedelta(0):
                raise ValueError(
                    f"since={since!r} is not aware UTC. It is calendar time "
                    f"carried across runs, and a naive value would silently "
                    f"shift the range by this machine's offset."
                )
            start = min(since, overlap)
        return CalendarWindow(start=start, end=now + self.width)


# ── What a fetch returns ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CalendarAttendee:
    """One `attendees[]` entry, or an organizer, as far as `33c` needs it.

    Every field is optional because every field is genuinely absent sometimes: a
    room resource has no response, and slice 0's own matrix for `33c` names a
    null `emailAddress`. `None` is carried rather than filled in — `33c` resolves
    a missing handle to `UNRESOLVED`, which it can only do if the absence
    survives this far.
    """

    email: str | None = None
    name: str | None = None
    kind: str | None = None
    """Graph's `type`: `required`, `optional`, or `resource`."""
    response: str | None = None
    """Graph's `status.response`: `accepted`, `declined`, `tentativelyAccepted`, …"""


@dataclass(frozen=True, slots=True)
class CalendarRow:
    """One calendar event, in aware UTC, with the provider's own fields intact.

    Not a `Meeting` and not an event: `33c` decides which of those a row becomes,
    what its scope is, and whether an all-day span counts as zero minutes. What
    is settled *here* is the part that has one right answer — the instants — and
    the fields `33c` cannot re-derive once the raw payload is gone.
    """

    event_id: str
    start: datetime
    end: datetime
    subject: str | None = None
    is_all_day: bool = False
    is_cancelled: bool = False
    kind: str | None = None
    """Graph's `type`: `singleInstance`, `occurrence`, `exception`, `seriesMaster`."""
    series_master_id: str | None = None
    occurrence_id: str | None = None
    """Present on an occurrence. Slice 0 measured both this and `seriesMasterId`
    on the wire, and `33e` needs them: one join URL resolves to one meeting
    holding every occurrence's transcript, so an occurrence is identified by
    time and by these ids rather than by the URL.
    """
    organizer: CalendarAttendee | None = None
    attendees: tuple[CalendarAttendee, ...] = ()
    categories: tuple[str, ...] = ()
    """What `33c` maps to a project scope. Carried verbatim, order preserved."""
    response: str | None = None
    """The PM's own `responseStatus.response` — `33c`'s declined and tentative rows."""
    source_timezone: str | None = None
    """The zone Graph actually answered with, before conversion.

    Kept because it is the only evidence on the row of whether the `Prefer`
    header applied. A tenant answering `"W. Europe Standard Time"` to a request
    that asked for UTC is a fact worth being able to see without a packet
    capture.
    """
    clock_flag: str | None = None
    """Why this row's provider clock is not credible, or `None` when it is.

    Flagged, never backfilled (AD-35) and never dropped: the row is emitted, and
    `33c` persists it so `PersistResult.flagged` can count it. A timestamp
    replaced from the local clock is well-formed, plausible and wrong, and
    nothing downstream could tell it from a real one.
    """

    def __post_init__(self) -> None:
        for name, value in (("start", self.start), ("end", self.end)):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                # The defensive check for the one defect this whole module
                # exists to prevent. A naive value here would be refused later
                # by `validate_occurred_at`, once per event, with a message
                # about a normalisation gap "upstream" — which would be here.
                raise ValueError(
                    f"CalendarRow.{name}={value!r} is not aware UTC. Every "
                    f"instant on a row is converted once, in this module, "
                    f"before the row exists."
                )

    @property
    def duration(self) -> timedelta:
        """`end - start`, from the converted pair.

        A subtraction of two aware UTC instants, which is what makes a
        DST-crossing occurrence come out at 23 or 25 hours rather than at a
        fixed 24: the offset each end of the span carried is already in the
        instants. Adding a nominal offset to a naive pair is the version that
        gets this wrong, silently, twice a year.
        """
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class RowRefusal:
    """One row the fetch would not emit, and why. The batch continued.

    Returned rather than raised, and counted rather than logged: a fetch that
    refused every row in the tenant and a fetch of an empty calendar both
    produce no rows, and only this tells them apart.
    """

    event_id: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class CalendarFetch:
    """Everything one fetch learned: the rows, the coverage, and the failure.

    All three together, for the reason `HarvestResult` carries all three: a
    page-one-succeeded, page-two-throttled fetch has real rows, a real coverage
    window and a real failure, and a raise can carry only the last of them.

    Not a `HarvestResult` — that carries `NormalizedEvent`s, and this slice
    emits no events. `33c` builds one from this.
    """

    window: CalendarWindow
    """The calendar range that was asked for. Never coverage — see the module
    docstring on the two clocks.
    """

    rows: tuple[CalendarRow, ...] = ()
    coverage: CoverageWindow | None = None
    """This machine's clock across the fetch, or `None` when nothing was earned.

    `None` for a window that came back empty: coverage is evidence a fetch
    reached something, and a window over a fetch that returned no rows is a
    claim tied to the clock and to nothing else (`harvest.py:144-150` refuses
    exactly that pairing).

    `None` too when rows came back and not one of them could be placed in time
    — `8a`'s second matrix row, which this connector used to ignore. Something
    arrived and nothing says when, and an unknowable start is not a guessable
    one.
    """

    failure: HarvestFailure | None = None
    refusals: tuple[RowRefusal, ...] = ()
    pages: int = 0
    walked_through: datetime | None = None
    """The calendar instant the next run may start from: the end of the last
    span walked to completion, or `None` when none was.

    Deliberately not the end of the *window*: a span abandoned partway holds
    meetings in a range that was asked for and not fully answered, so advancing
    past it would skip them permanently. `None` is "the cursor does not move".
    """


# ── The fetch ────────────────────────────────────────────────────────────────


@dataclass
class GraphCalendarFetch:
    """Page `calendarView` over a bounded window and return what was earned.

    Reports; never raises on a transport failure, exactly as
    `GitLabConnectorAdapter.harvest` does not — and for the same reason, which
    is that the coverage a partial walk earned cannot travel up a stack as an
    exception.

    Owns no cadence, no cursor and no schedule (AD-9). It is told where the last
    run stopped and it hands back where this one did.
    """

    client: GraphClient
    instance: str
    """The registry identity coverage is keyed by — `graph:<instance>`, supplied
    by `33c`'s connector. The same string `save_cursor` stores a window under
    and `coverage_windows` reads it back by, spelled once so the two cannot
    drift.
    """

    windows: WindowPolicy
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    max_span: timedelta = MAX_SERVABLE_SPAN

    def fetch(self, *, since: datetime | None = None) -> CalendarFetch:
        """Walk every span of the window, and report what actually happened.

        Three things are derived only from what came back:

        - **coverage exists at all** when rows arrived *and at least one of them
          can be placed in time* — the same rule `gitlab.py`'s
          `_bounded_by_a_credible_clock` applies, because both connectors write
          the same `CoverageWindow | None` slot and `8a`'s matrix answers the
          question once for both: rows with no usable clock claim no coverage.
          The requested range decides *whether* it was earned; its bounds are
          this machine's clock from the instant the first page returned to the
          instant the walk stopped.
        - **`walked_through`** advances only past spans walked to completion —
          and a span holding a page that could not be read is not one of them,
          however normally the walk ended.
        - **`refusals`** name the rows that could not be read, one by one, so a
          calendar that is empty and a calendar pm-ai cannot parse are
          distinguishable.
        """
        started = self.now()
        window = self.windows.window(now=started, since=since)
        deadline = started + self.client.budget

        rows: list[CalendarRow] = []
        refusals: list[RowRefusal] = []
        pages = 0
        reached_at: datetime | None = None
        walked_through: datetime | None = None
        failure: HarvestFailure | None = None
        # Whether the cursor may still advance. It stops for good at the first
        # span that was asked for and not fully answered: `walked_through` is
        # one high-water mark, so advancing past a *later* span would carry the
        # hole with it.
        advancing = True

        for span in window.spans(max_span=self.max_span):
            url = self.client.calendar_view(span.start, span.end)
            answered = True
            try:
                for body in self.client.walk(url, deadline=deadline):
                    if reached_at is None:
                        # The instant the *provider answered*, not the instant it
                        # was asked. Asking earns no coverage however long it took.
                        reached_at = self.now()
                    pages += 1
                    if not self._read(body, window=window, into=rows, refusing=refusals):
                        answered = False
            except GraphCallFailed as refused:
                failure = self._failure(
                    span, refused.reason, retryable=refused.retryable, hint=refused.retry_after
                )
                break
            except GraphUnreachable as silent:
                # `33a`'s own unreachable: nothing was learned, and waiting is
                # the remedy. Its message is already redacted of credential
                # material, which is why it may be carried into a durable row.
                failure = self._failure(span, str(silent), retryable=True)
                break
            except GraphAuthError as unauthenticated:
                # `CredentialStale`, `AuthDeclined`, `InteractionRequired` and
                # the unmapped base. None of them clears by waiting, and all of
                # them are the operator's to act on.
                failure = self._failure(span, str(unauthenticated), retryable=False)
                break
            except Exception as unexpected:  # noqa: BLE001 — a failure is a value here
                # Broad on purpose: anything a transport or a mapping can throw
                # has to become this value, or the spans already walked lose
                # their coverage on the way up. Not retryable, because a fault
                # nobody classified is not one pm-ai may promise will clear.
                failure = self._failure(
                    span, f"{type(unexpected).__name__}: {unexpected}", retryable=False
                )
                break
            if not answered:
                # A 200 whose body pm-ai could not read is a span asked for and
                # not answered, even though the walk ended normally. The refusal
                # already says the page "is not counted as evidence of an empty
                # calendar"; advancing the cursor past it would have made it
                # exactly that, permanently, because no later run revisits a
                # span the cursor has passed.
                advancing = False
                continue
            if advancing:
                walked_through = span.end

        finished = self.now()
        coverage = (
            CoverageWindow(connector_instance=self.instance, start=reached_at, end=finished)
            if reached_at is not None and _any_credible_clock(rows)
            else None
        )
        return CalendarFetch(
            window=window,
            rows=tuple(rows),
            coverage=coverage,
            failure=failure,
            refusals=tuple(refusals),
            pages=pages,
            walked_through=walked_through,
        )

    # ── One page, one row ────────────────────────────────────────────────────

    def _read(
        self,
        body: Mapping[str, Any],
        *,
        window: CalendarWindow,
        into: list[CalendarRow],
        refusing: list[RowRefusal],
    ) -> bool:
        """Map one page's `value` array, refusing rows one at a time.

        A refusal is per row and never per page: one event with no `start`
        must not discard the fifty beside it, which is the whole reason the
        refusals are a returned value rather than an exception.

        Returns whether the page itself could be read. `False` is a 200 with no
        `value` array at all — a page that was asked for and not answered — and
        the caller holds the cursor back on it. A per-row refusal does not make
        this `False`: the page was read, one event in it was unreadable, and
        re-asking would refuse the same row forever.
        """
        values = body.get("value")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            refusing.append(
                RowRefusal(
                    event_id=None,
                    reason=(
                        f"a calendarView page carried no `value` array to read "
                        f"({type(values).__name__}). No rows were taken from "
                        f"it; the walk continues, and the page is not counted "
                        f"as evidence of an empty calendar."
                    ),
                )
            )
            return False
        for raw in values:
            try:
                into.append(self._row(raw, window=window))
            except CalendarRowRefused as refused:
                refusing.append(
                    RowRefusal(event_id=_identifier(raw) or None, reason=str(refused))
                )
        return True

    def _row(self, raw: Any, *, window: CalendarWindow) -> CalendarRow:
        """One Graph event as a `CalendarRow`, or the refusal that names it."""
        if not isinstance(raw, Mapping):
            raise MalformedCalendarRow(
                f"a calendarView page carried a {type(raw).__name__} where an "
                f"event object belongs. Refused: there is nothing to identify "
                f"it by, let alone place in time."
            )
        event_id = _identifier(raw)
        if not event_id:
            raise MalformedCalendarRow(
                "a calendarView row carries no `id`, so nothing downstream "
                "could key a record on it or cite it. Refused rather than "
                "emitted under an invented identifier."
            )
        start = to_utc(raw.get("start"), field_name="start", event_id=event_id)
        end = to_utc(raw.get("end"), field_name="end", event_id=event_id)
        return CalendarRow(
            event_id=event_id,
            start=start,
            end=end,
            subject=_text(raw.get("subject")),
            is_all_day=bool(raw.get("isAllDay")),
            is_cancelled=bool(raw.get("isCancelled")),
            kind=_text(raw.get("type")),
            series_master_id=_text(raw.get("seriesMasterId")),
            occurrence_id=_text(raw.get("occurrenceId")),
            organizer=_attendee(raw.get("organizer")),
            attendees=_attendees(raw.get("attendees")),
            categories=_categories(raw.get("categories")),
            response=_response(raw.get("responseStatus")),
            source_timezone=_stated_zone(raw.get("start")),
            clock_flag=_clock_flag(start, end, window=window, event_id=event_id),
        )

    def _failure(
        self,
        span: CalendarWindow,
        reason: str,
        *,
        retryable: bool,
        hint: timedelta | None = None,
    ) -> HarvestFailure:
        """One transport refusal as the value that survives the process.

        The reason names the *calendar span* rather than the URL: a `nextLink`
        carries an opaque `$skiptoken`, and this value is written to Tier 2,
        never rebuilt, and read back into reports.
        """
        return HarvestFailure(
            reason=(
                f"{self.instance} could not finish the calendar span "
                f"{span.start.isoformat()}/{span.end.isoformat()}: {reason}"
            ),
            retryable=retryable,
            retry_after=hint,
        )


def _any_credible_clock(rows: Sequence[CalendarRow]) -> bool:
    """Whether any emitted row can be placed in time at all (AD-35, `8a`).

    The Graph half of the rule `gitlab.py._bounded_by_a_credible_clock` states
    for GitLab, and deliberately the same rule: both connectors fill the same
    `CoverageWindow | None` slot, and `8a`'s matrix answers this once — *rows
    returned, no usable clock → harvested something, no coverage claimed*. Graph
    omitted the second half and claimed coverage for a page whose every
    timestamp was outside the range that was asked for.

    Like GitLab's, this does **not** supply the window's bounds: those are this
    machine's clock, because coverage is `ingested_at`. What the rows decide is
    whether there is any coverage to claim, and a row already carries that
    verdict — `clock_flag` is `None` exactly when `_clock_flag` believed the
    provider's instant. Flagging, not rejecting: the rows are still emitted and
    `33c` still persists them; only the coverage claim is withheld.
    """
    return any(row.clock_flag is None for row in rows)


# ── Reading provider fields, each in one place ───────────────────────────────


def _identifier(raw: Any) -> str:
    """An event's `id`, or `""` when there is not a usable one."""
    if not isinstance(raw, Mapping):
        return ""
    value = raw.get("id")
    return value.strip() if isinstance(value, str) else ""


def _text(value: Any) -> str | None:
    """A provider string, or `None` for absent, blank, or not-a-string.

    Blank collapses to `None` deliberately: an untitled meeting and one whose
    subject is three spaces are the same fact, and `33c` has one absence to
    handle rather than two.
    """
    return value.strip() or None if isinstance(value, str) else None


def _stated_zone(stamp: Any) -> str | None:
    """The `timeZone` Graph put beside a `dateTime`, verbatim."""
    return _text(stamp.get("timeZone")) if isinstance(stamp, Mapping) else None


def _attendee(raw: Any) -> CalendarAttendee | None:
    """One attendee or organizer object, with every absence preserved."""
    if not isinstance(raw, Mapping):
        return None
    address = raw.get("emailAddress")
    address = address if isinstance(address, Mapping) else {}
    return CalendarAttendee(
        email=_text(address.get("address")),
        name=_text(address.get("name")),
        kind=_text(raw.get("type")),
        response=_response(raw.get("status")),
    )


def _attendees(raw: Any) -> tuple[CalendarAttendee, ...]:
    """The attendee list, order preserved, non-objects dropped.

    An entry that is not an object is dropped rather than refusing the row: the
    meeting is still a meeting, and `33c` counts attendees rather than trusting
    each one. A row whose *whole* list is unreadable comes out as zero
    attendees, which is a real state on the wire — a room booking has none.
    """
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, Mapping)):
        return ()
    return tuple(
        attendee for attendee in (_attendee(entry) for entry in raw) if attendee is not None
    )


def _categories(raw: Any) -> tuple[str, ...]:
    """Outlook categories, order preserved. `33c` maps these to a project scope."""
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, Mapping)):
        return ()
    return tuple(text for text in (_text(entry) for entry in raw) if text)


def _response(raw: Any) -> str | None:
    """A `{response, time}` status object's `response`."""
    return _text(raw.get("response")) if isinstance(raw, Mapping) else None


def _clock_flag(
    start: datetime, end: datetime, *, window: CalendarWindow, event_id: str
) -> str | None:
    """Why this row's provider clock is not credible, or `None`.

    Two questions, both answered against something measured rather than
    assumed:

    **Is the start inside the range that was asked for?** The domain guard is
    reused with `window.end` as the reference instant, not `now`.
    `validate_occurred_at` refuses a timestamp more than five minutes past its
    reference — and a calendar's future half is legitimate, so passing `now`
    would flag every upcoming meeting in the tenant. Passing the far edge of the
    requested range asks the question that is actually wrong: the server was
    asked for a bounded range and answered outside it. The `EARLIEST_PLAUSIBLE`
    floor comes along with it, which is the zero-value parse — an absent
    provider field arriving as the epoch.

    A start *before* the window is not flagged: `calendarView` returns every
    occurrence that overlaps the range, so a meeting that began before it and
    runs into it is a correct answer.

    **Does the span run backwards?** An `end` before its `start` is a clock
    nothing can reconcile, and it would give `duration` a negative value that
    `33c` would carry into a cost.
    """
    if end < start:
        return (
            f"event {event_id} ends at {end.isoformat()}, before it starts at "
            f"{start.isoformat()}. The span is carried as the provider sent it "
            f"and flagged rather than reordered — a corrected pair would be "
            f"indistinguishable from a real one."
        )
    try:
        validate_occurred_at(start, now=window.end)
    except ImplausibleTimestamp as refused:
        return (
            f"event {event_id} starts at {start.isoformat()}, outside the range "
            f"ending {window.end.isoformat()} that was requested: {refused}"
        )
    return None
