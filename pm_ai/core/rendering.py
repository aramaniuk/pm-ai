"""`daily_dashboard.md`, rendered — both of them, from one set of sections.

Two files carry that name. `render_dashboard` writes the personal one, which is
what CAP-9 asks for: `~/.manager-ai/memory/daily_dashboard.md` by 07:00 with
exactly four headed sections — Time-Critical Activities, Proactive Enablement,
3-Tier Strategic Milestones, Leadership Notes — and no empty section.
`render_project_dashboard` writes a project's, which CAP-9 does not govern and
which carries the two sections its own sources support. Nothing turned meetings,
log entries and goals into either text; this module is those two functions and
the section renderers they share.

**Pure, and structurally so.** Both take their inputs and their instant, read no
clock, open no file and call no model. `core` is I/O-free by contract, and the
injected clock is the rule story `1b` established: a renderer that read
`datetime.now()` would be the second clock in a codebase whose storage service
exists to have exactly one, and untestable at the only boundary that matters.
Every section is therefore golden-file testable.

## Nothing is invented

The rule that shapes every string below. A section with no data states the
*computed reason* — a file that is absent, a query that returned nothing, a
window that held nothing. "No meetings on the calendar today" names a query
result. "All clear!" names a state of the world nothing measured, and would be
false on a day with an unread inbox.

Two consequences worth stating outright, because they are what the wording costs:

- **"the calendar could not be read" and "no meetings today" are different
  facts.** They used to be the same silence, when the day's meetings came off
  disk. Since 2026-09-07 they come from a live fetch (`33b`), and a fetch that
  failed produced *no query result at all* — so saying "no meetings" there would
  be asserting something nobody asked and nobody answered. `8a`'s
  `HarvestFailure` already carries the reason, so it is stated rather than
  invented, and it is why `meetings` is a union type rather than a sequence.
- **"all of today's meetings have ended" is not "no meetings today".** By
  mid-afternoon the second is a lie about a day that had four meetings in it.

## Two renderers, sharing sections and never inputs

`render_project_dashboard` (`23d`) is the second one. It is a **separate
function** with its own sources and its own sections rather than a flag on this
one, and the deliverable is what its signature lacks: no goals parameter, no
register, no personal-scope input of any kind. All three goal domains live in
the personal `strategic_goals.md` (`scope_model.py:541-544`, which says outright
that there is "no project-scope counterpart"), so a project render that *could*
be handed a register is one wrong branch — `goals = personal_register` — away
from writing the PM's career goals into a project artifact. AD-25 asks for a
wall rather than a remembered tag check, and a parameter that does not exist is
the only version of that which cannot be forgotten.

What the two share is the **section renderers**, never the inputs. Given the
same meetings, `_time_critical` produces byte-identical Markdown for both files;
that is the point, and it is why `NO_MEETINGS` names no one's calendar in
particular. Duplicating the section text is how two dashboards drift into two
formats.

## What this module does not do

No file write (`23b`), no scheduling (`9a`), no scope *filtering* — `11a`'s
accessor reads one scope and the caller passes what it read, so neither renderer
inspects `Meeting.scope` — no model call of any kind, and no commitment data:
nothing in this build produces commitments, and a section implying otherwise
would be the invented evidence the whole module is arranged against.

It also does **not** decide which day it is rendering. The caller selects the
meetings — `23b` reads the calendar for the display day — and this function
renders what it is handed, which is what lets a meeting that started yesterday
and ends this morning appear without a date filter here silently dropping it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from enum import Enum

from pm_ai.core.goal_register import ARTIFACT as GOALS_ARTIFACT
from pm_ai.core.event_log import LogRead
from pm_ai.core.goal_register import GoalRegister
from pm_ai.domain.event_entries import EventEntry, MalformedEntry, SelfActionType
from pm_ai.domain.events import MessagePayload, ObservedEventType
from pm_ai.domain.goals import Goal, GoalDomain
from pm_ai.domain.handles import NO_PERSON, PersonHandle
from pm_ai.domain.harvest import (
    HarvestFailure,
    NoCalendarConnector,
    PartialCalendar,
)
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.payload_fields import decode_payload

__all__ = [
    "CalendarAnswer",
    "EventLogAnswer",
    "GOALS_PATH",
    "HEADINGS",
    "LEADERSHIP_NOTES",
    "MAX_SIGNAL_LINES",
    "MENTIONS_YOU",
    "MESSAGE_WINDOW",
    "NO_MEETINGS",
    "NO_REASON_GIVEN",
    "PROACTIVE_ENABLEMENT",
    "PROJECT_HEADINGS",
    "PmHandles",
    "STRATEGIC_MILESTONES",
    "TIME_CRITICAL",
    "UNKNOWN_SENDER",
    "code",
    "escape",
    "render_dashboard",
    "render_project_dashboard",
]


CalendarAnswer = (
    Sequence[Meeting] | HarvestFailure | NoCalendarConnector | PartialCalendar
)
"""Everything "what is on today?" can honestly answer, and nothing else.

Four members because there are four distinct facts, and the type exists so that
no two of them can be collapsed into one sentence:

- a **sequence** is a query that ran and returned these meetings — possibly none,
  which is a measured empty day;
- a **`HarvestFailure`** is a fetch that could not be completed, so there is no
  query result to report at all;
- a **`NoCalendarConnector`** is no calendar to ask, which is neither of the
  above and must not borrow the second's wording — that wording quotes a
  connector, and there is none to quote;
- a **`PartialCalendar`** is some calendars answering and some not, which a
  either/or union cannot express without throwing away one half.

Widened from two members to four by story `23b`, the slice that performs the
fetch and therefore the only one that can tell these four apart.
"""


EventLogAnswer = Sequence[EventEntry] | HarvestFailure
"""What reading the scope's event log can honestly answer (story `23c`).

A **sequence** is a read that completed — possibly empty, which is a measured
quiet window. A **`HarvestFailure`** is a read that could not complete: a line
that is not a well-formed entry, an entry of a kind pm-ai does not know, a
segment that is not UTF-8 text, or a segment that could not be read from disk.
Its `reason` is the refusal's own text, which names the segment.

The same value `CalendarAnswer` uses for a failed fetch, built by the pipeline
and handed only to the renderer — never saved, so the persisted-harvest meaning
in `HarvestFailure`'s docstring does not apply to it.
"""


@dataclass(frozen=True, slots=True)
class PmHandles:
    """Who the PM is, for marking "mentions you", from one read at one instant.

    `handles` is what `pm_identity()` answered (AD-34): `graph:<user_id>` for
    every Graph connector row, enabled or disabled. A message whose `mentions`
    holds one of them, compared as handle text, mentions the PM.

    `unrecorded` names every enabled Graph connector whose row records no usable
    user id — a sign-in from before pm-ai recorded it. Nothing can be marked for
    that tenant, and the section says so, naming the command that records it.

    `unchecked` is every other connector row (or `connectors/` itself) that
    could not say whether it is the PM's, as `(name, why)` pairs: a row that
    could not be read may be the PM's own Graph row, so mentions could not be
    checked against it, and the section says that too.

    No defaults: a caller that did not read the rows must not be able to render
    a section that looks as if nobody was mentioned.
    """

    handles: frozenset[PersonHandle]
    unrecorded: tuple[str, ...]
    unchecked: tuple[tuple[str, str], ...]


# ── The four headings, in CAP-9's order ──────────────────────────────────────

TIME_CRITICAL = "Time-Critical Activities"
PROACTIVE_ENABLEMENT = "Proactive Enablement"
STRATEGIC_MILESTONES = "3-Tier Strategic Milestones"
LEADERSHIP_NOTES = "Leadership Notes"

HEADINGS: tuple[str, ...] = (
    TIME_CRITICAL,
    PROACTIVE_ENABLEMENT,
    STRATEGIC_MILESTONES,
    LEADERSHIP_NOTES,
)
"""CAP-9's four, in its order, and the only headings this file emits.

All four render on every input, whatever the data says, so the file's shape is
stable for a human skimming it at 07:00 and for anything that later parses it.
A section that vanished when empty would make "is it missing or is it empty?" a
question a reader has to answer by remembering what the renderer does.

No `#` title above them and no `###` beneath them: CAP-9 says *exactly* four
headed sections, and the tiers below are a nested list for that reason.
"""

PROJECT_HEADINGS: tuple[str, ...] = (
    TIME_CRITICAL,
    PROACTIVE_ENABLEMENT,
)
"""The project dashboard's two, which are what its sources support.

Not a subset of CAP-9's four by omission — CAP-9 does not bind this file. Its
success criterion names `~/.manager-ai/memory/daily_dashboard.md` *by path*
("exactly the four headed sections … and no empty section"), which is the
personal artifact. The project render's inputs are project-scope meetings and a
project-scope event log, and those two sources support exactly these two
sections.

The other two are absent for reasons worth separating. **3-Tier Strategic
Milestones cannot exist here**: every goal domain lives in the personal
`strategic_goals.md`, so the section would have nothing to read that this
function is allowed to be handed — which is the same fact the missing parameter
states. **Leadership Notes** is synthesis, and there is no model in this path.
Rendering either as a heading over a sentence explaining its own emptiness would
put two permanently hollow sections in a file whose whole discipline is that it
states only what it computed.
"""

GOALS_PATH = f"~/.manager-ai/memory/{GOALS_ARTIFACT}"
"""Where the PM authors their goals (`scope_model.py:544`), spelled for a human.

The filename comes from `goal_register.ARTIFACT` rather than being retyped, so
the remediation this file prints and the file the parser reads cannot drift
apart.
"""

MESSAGE_WINDOW = timedelta(hours=24)
"""How far back Proactive Enablement looks for message signals.

A day, because the artifact is a *daily* dashboard: a signal older than the last
render has already been past the PM once. Stated as a constant because the
section names it — an empty section that says "in the window" without saying
which window has not told the reader anything.
"""

NO_MEETINGS = "No meetings on the calendar today"
"""The exact claim, kept as a constant because three rules bear on it.

It names a *query result*: the calendar was asked and answered with nothing. It
is therefore forbidden when the fetch failed (there is no result to report) and
forbidden when meetings came back and have all ended (the result was not
nothing).

The third rule is why it says "the calendar" rather than "your calendar": this
string is emitted by a section renderer that `23d`'s project dashboard shares,
and a shared renderer is one input to one output. Naming the reader's own
calendar would have forced the empty-day branch to know which of the two
dashboards called it — the one thing sharing a section renderer is meant to
avoid — and the alternative, a per-caller sentence passed in, makes "identical
data renders identical Markdown" false on exactly the branch where nothing
distinguishes the two inputs. Neutral wording keeps the claim true in both
files: the query result is the same fact whichever calendar was asked.
"""

NO_REASON_GIVEN = "the connector gave no reason"
"""What stands in when `HarvestFailure.reason` is blank or only whitespace.

`reason` has no default and is meant to carry the provider's own words, but
nothing in the type stops an empty string, and `escape` strips — so the sentence
would read "…are unknown: " and stop. That the connector said nothing is a fact
about the failure, and stating it keeps the line a sentence rather than a
truncated one a reader assumes was cut off.
"""

MAX_SIGNAL_LINES = 20
"""At most this many signal lines — one screen at 07:00 (settled 2026-10-09).

Mentions of the PM first, then newest first. When the window held more, one
closing line states how many more and that they are in the event log.
"""

MENTIONS_YOU = "mentions you"
"""How a signal line whose `mentions` holds one of the PM's handles ends."""

UNKNOWN_SENDER = "an unknown sender"
"""What the absence actor `none:` renders as; every other actor renders as written."""

# The categories Proactive Enablement reads. One member today; a tuple because
# the section states its own vocabulary, and a widening should change the
# printed sentence by changing this.
SIGNAL_CATEGORIES: tuple[ObservedEventType, ...] = (ObservedEventType.MESSAGE_POSTED,)

def render_dashboard(
    meetings: CalendarAnswer,
    entries: EventLogAnswer,
    goals: GoalRegister,
    now: datetime,
    *,
    tz: tzinfo | None,
    pm: PmHandles,
) -> str:
    """CAP-9's four sections, as Markdown, from these inputs and this instant.

    `meetings` is the calendar's *answer*, not a list, and `CalendarAnswer` has
    four members because there are four distinct facts: a sequence is a query
    that ran, a `HarvestFailure` is a fetch that could not be completed, a
    `NoCalendarConnector` is no calendar to ask at all, and a `PartialCalendar`
    is some calendars answering while others did not. Collapsing any of them into
    an empty list is precisely the confusion the honesty rule forbids, so the
    type refuses to do it.

    `entries` is the scope's event log, **unbounded**. The window is applied
    here rather than by `EventLog.read`, because a bounded read drops an entry
    with no `ingested_at` silently and this section counts them: an entry that
    cannot be placed in time is a fact about the log, not an absence. It is an
    `EventLogAnswer`: a log that could not be read arrives as a
    `HarvestFailure` and is stated, never rendered as a quiet day (`23c`).

    `pm` is who the PM is, read from the connector rows (`PmHandles`): a message
    mentioning one of its handles is marked and listed first.

    `goals` carries `present`, which is the whole reason it is a `GoalRegister`
    and not a `dict`. An absent file and a file with nothing in it need
    different sentences, and telling a PM to author a file they have already
    authored is how a dashboard loses their trust.

    `now` must be aware UTC and `tz` must be supplied. Rendering the same inputs
    twice returns byte-identical output: nothing here reads a clock and every
    ordering is total.
    """
    tz = _require_tz(tz)
    _assert_utc(now, name="now")

    return _document(
        HEADINGS,
        (
            _time_critical(meetings, now=now, tz=tz),
            _proactive_enablement(entries, pm=pm, now=now, tz=tz),
            _strategic_milestones(goals),
            _leadership_notes(),
        ),
    )


def render_project_dashboard(
    meetings: CalendarAnswer,
    entries: EventLogAnswer,
    now: datetime,
    *,
    tz: tzinfo | None,
    pm: PmHandles,
) -> str:
    """A project's day, as Markdown — and nothing of the PM's.

    **The signature is the wall.** There is no `goals` parameter here and no
    fourth positional slot of any kind, so the leak AD-25 names has no
    expression rather than a rule against it: a caller cannot hand this function
    the personal register by choosing the wrong branch, because there is nowhere
    for it to go. That absence is this function's deliverable, and
    `test_ad25_project_rendering_cannot_open_the_personal_store` asserts it by
    reading `inspect.signature` rather than by grepping a list of allowed
    sources — a signature cannot drift silently, and a list needs a test that
    remembers to check it.

    **It filters nothing.** `meetings` and `entries` are rendered as handed over.
    `11a`'s accessor reads one scope, `23b` passes what it read, and a scope
    check here would be a second, weaker copy of a boundary the callers already
    hold — the "remembered tag check" AD-25 asks this not to be. A personal-scope
    meeting passed in renders like any other; keeping it out is the caller's job,
    and the parameter list is what makes the *consequential* half impossible.

    `meetings` is the calendar's answer, not a list, for the same reason as in
    `render_dashboard`, and it is the same four-member `CalendarAnswer`: a
    project's day comes from a live fetch narrowed to that scope (`33b`,
    2026-09-07), and a fetch that failed is not a day with nothing in it — nor is
    a machine with no calendar enrolled, nor a day only half of which was read.
    All four render through the same `_time_critical`, which is what keeps the
    two files saying the same thing about the same answer.

    `now` must be aware UTC and `tz` must be supplied, both exactly as above.
    `entries` and `pm` mean what they mean there: the PM's handles come from
    pm-ai's own connector settings, which the wall does not forbid (`23c`).
    Rendering the same inputs twice returns byte-identical output, and so does
    rendering them through the other function: the two sections below are the
    same two functions.
    """
    tz = _require_tz(tz)
    _assert_utc(now, name="now")

    return _document(
        PROJECT_HEADINGS,
        (
            _time_critical(meetings, now=now, tz=tz),
            _proactive_enablement(entries, pm=pm, now=now, tz=tz),
        ),
    )


def _document(headings: Sequence[str], sections: Sequence[str]) -> str:
    """Headed bodies, joined — the one place either dashboard's shape is decided.

    Shared so that the two files cannot differ in anything but which sections
    they carry. A second copy of this join is how a blank line goes missing from
    one of them and nobody notices for a month.
    """
    blocks = [
        f"## {heading}\n\n{body}"
        for heading, body in zip(headings, sections, strict=True)
    ]
    # A blank line before every heading, because a `##` on the line after a list
    # item is not a heading to a Markdown parser — it is more list item, and the
    # file's section shape would exist only for a reader squinting at the
    # source. One trailing newline, so the file ends on a line.
    return "\n\n".join(blocks) + "\n"


def _require_tz(tz: tzinfo | None) -> tzinfo:
    """The display zone, refused rather than defaulted — for both renderers.

    Narrows the type as well as checking it, so every section below takes a
    `tzinfo` rather than an optional one.
    """
    if tz is None:
        raise ValueError(
            "tz is required. The display timezone decides which instants count "
            "as today, and defaulting to UTC is the silent wrong answer the "
            "`display_timezone` key exists to prevent — a PM two zones away "
            "would get a dashboard whose day boundary is not theirs, with "
            "nothing on the page saying so. Read it from the loaded `Config` "
            "and pass the same value to the calendar read that selected these "
            "meetings, so the two cannot disagree."
        )
    return tz


# ── Time-Critical Activities ─────────────────────────────────────────────────


def _time_critical(
    meetings: CalendarAnswer, *, now: datetime, tz: tzinfo
) -> str:
    """Today's schedule, or the reason there is none to show."""
    if isinstance(meetings, NoCalendarConnector):
        # Deliberately not the `HarvestFailure` branch below, and this is the
        # whole reason the value exists. That branch ends in `_retry_advice`,
        # which for an unretryable failure prints "The connector reports this
        # will not clear on its own" — a report attributed to a connector that
        # was never enrolled, in the artifact whose entire discipline is that it
        # never asserts what it did not compute.
        #
        # No remedy is named either, and that is measured rather than lazy: the
        # obvious sentence to print is `pm-ai connector add graph <instance>`,
        # and it refuses — `probe.PROBES` holds no `graph`, and past the probe
        # `enrol_connector` writes none of the settings a Graph connector needs.
        # A dashboard that printed a command which does not work would be
        # inventing a remedy, which is the same defect as inventing a fact.
        return (
            "No calendar is enrolled on this machine — nothing here declares "
            "that it can report meetings — so today's schedule is unknown. "
            "Nothing was asked and nothing answered, which is why this section "
            "reports no query result at all."
        )

    if isinstance(meetings, PartialCalendar):
        # Both halves, always. The meetings that arrived are real and are not
        # discarded because a second calendar went dark, and the calendar that
        # went dark is named because a day presented whole when it is partial is
        # the more expensive of the two mistakes.
        arrived = (
            _scheduled(meetings.meetings, now=now, tz=tz)
            if meetings.meetings
            else "The calendars that answered held nothing for today."
        )
        count = len(meetings.unread)
        heading = (
            "One calendar could not be read, so this is part of the day:"
            if count == 1
            else f"{count} calendars could not be read, so this is part of the day:"
        )
        named = "\n".join(
            f"- **{escape(unread.instance)}** — "
            f"{escape(unread.failure.reason) or NO_REASON_GIVEN}"
            for unread in meetings.unread
        )
        return f"{arrived}\n\n{heading}\n\n{named}"

    if isinstance(meetings, HarvestFailure):
        # Never `NO_MEETINGS`. The fetch produced no answer, so there is no
        # result to report — and a dashboard that reported yesterday's silence
        # as today's empty calendar is the failure this branch exists for.
        #
        # `escape` strips, so a reason that was only whitespace arrives here
        # empty and the sentence would end on a dangling colon. That the
        # connector said nothing is itself a fact, and stating it is what keeps
        # the line a sentence.
        reason = escape(meetings.reason) or NO_REASON_GIVEN
        return (
            f"The calendar could not be read, so today's meetings are unknown: "
            f"{reason}\n\n"
            f"{_retry_advice(meetings)}"
        )

    return _scheduled(meetings, now=now, tz=tz)


def _scheduled(
    meetings: Sequence[Meeting], *, now: datetime, tz: tzinfo
) -> str:
    """The meetings a calendar actually answered with, as the section's body.

    Split out of `_time_critical` by `23b` so the partial-day branch can render
    the half that arrived through exactly this code rather than through a second
    copy of it — the same reason the two dashboards share `_time_critical`
    itself. A second copy is how a partial day grows a different line format from
    a whole one and nobody notices for a month.
    """
    # Before the sort, not after: comparing a naive start against an aware one
    # raises `TypeError` from inside `sorted`, which surfaces as the render
    # failing rather than as the caller being told what it handed over.
    for meeting in meetings:
        _assert_utc(meeting.start, name=f"meeting {meeting.meeting_id!r} start")
        # Refused for the same reason a naive start is: a negative duration
        # makes `_end` land before `start`, so a meeting hours away computes as
        # ended — and one such value flips the whole section to "all of today's
        # meetings have ended", a claim about every other meeting on the page.
        if meeting.duration_minutes < 0:
            raise ValueError(
                f"meeting {meeting.meeting_id!r} has duration_minutes="
                f"{meeting.duration_minutes}, which ends it before it starts. "
                f"The section decides `ended` from `start + duration`, so a "
                f"negative value would report an upcoming meeting as finished "
                f"and take the rest of the day's meetings down with it."
            )
    # Total, so a re-render of the same day is byte-identical: `start` alone
    # ties for two meetings booked at the same minute, and `sorted` would then
    # preserve whatever order the fetch's paging happened to produce.
    ordered = sorted(meetings, key=lambda m: (m.start, m.meeting_id))

    if not ordered:
        return f"{NO_MEETINGS} — the calendar answered, and the day held nothing."

    if all(_ended(meeting, now=now) for meeting in ordered):
        # Not `NO_MEETINGS`, which by mid-afternoon would be a false claim about
        # a day that had four meetings in it — in the artifact whose stated
        # purpose is that it never asserts what it did not compute.
        count = len(ordered)
        claim = (
            "Today's one meeting has ended"
            if count == 1
            else f"All {count} of today's meetings have ended"
        )
        last = max(_end(meeting) for meeting in ordered)
        return f"{claim}. The last finished at {_local(last, tz=tz, now=now, dated=True)}."

    return "\n".join(_meeting_line(meeting, now=now, tz=tz) for meeting in ordered)


def _meeting_line(meeting: Meeting, *, now: datetime, tz: tzinfo) -> str:
    """One meeting, with the four things that decide whether to act on it."""
    title = escape(meeting.title) or "(untitled)"
    attendees = len(meeting.attendees)
    facts = [
        _state(meeting, now=now),
        f"{meeting.duration_minutes} min",
        f"{attendees} attendee" + ("" if attendees == 1 else "s"),
    ]
    if meeting.tentative:
        # Carried in memory and written to no file (`meetings.py`), so a surface
        # that reads the calendar live is the only place it can be said at all.
        facts.append("your response is tentative")
    when = _local(meeting.start, tz=tz, now=now)
    return f"- **{when}** {title} — {', '.join(facts)}"


def _state(meeting: Meeting, *, now: datetime) -> str:
    if _ended(meeting, now=now):
        return "ended"
    if meeting.start <= now:
        return "in progress"
    return "upcoming"


def _end(meeting: Meeting) -> datetime:
    return meeting.start + timedelta(minutes=meeting.duration_minutes)


def _ended(meeting: Meeting, *, now: datetime) -> bool:
    """A zero-length meeting at exactly `now` has ended, which is why `<=`."""
    return _end(meeting) <= now


def _retry_advice(failure: HarvestFailure) -> str:
    """What the operator does next, from what the connector actually said.

    `retryable` has no default in `HarvestFailure` for the reason this line
    depends on: a dead credential and a throttled minute are the same absent
    calendar and a different thing to do about it.
    """
    if not failure.retryable:
        return (
            "The connector reports this will not clear on its own, so the "
            "calendar stays unread until someone looks at it."
        )
    if failure.retry_after is not None:
        return (
            f"The provider asked for a wait of "
            f"{_duration_words(failure.retry_after)}; the next harvest should "
            f"clear it."
        )
    return "The connector reports this as retryable; the next harvest may clear it."


def _duration_words(delta: timedelta) -> str:
    """A wait, spelled the way a PM reads one.

    `str(timedelta)` renders `0:00:30`, which is a repr of a Python object in a
    file somebody reads at 07:00. The value is the provider's own hint, so it is
    stated exactly — rounded to the second it was measured in, never softened to
    "about a minute", which would be a number this code did not compute.
    """
    seconds = round(delta.total_seconds())
    if seconds <= 0:
        # A hint of zero or less is not a wait. Saying "0 seconds" would read as
        # a measurement; saying there is no wait is what the value means.
        return "no wait at all"
    parts: list[str] = []
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute"), (1, "second")):
        count, seconds = divmod(seconds, size)
        if count:
            parts.append(f"{count} {unit}" + ("" if count == 1 else "s"))
    return " ".join(parts)


# ── Proactive Enablement ─────────────────────────────────────────────────────


def _proactive_enablement(
    entries: EventLogAnswer, *, pm: PmHandles, now: datetime, tz: tzinfo
) -> str:
    """Message signals inside the window, or why there are none to show.

    A log that could not be read is stated, with its reason bounded, and nothing
    else is said. Otherwise: the signals decoded, marked and ordered — mentions
    of the PM first, then newest first — at most `MAX_SIGNAL_LINES` of them with
    a closing line counting the rest, or the empty-window sentence; then a line
    per connector whose sign-in predates the recorded user id and per row that
    could not be checked; then the footnotes counting what the window excluded.
    """
    if isinstance(entries, HarvestFailure):
        # First, and the mirror of `_time_critical`'s failure branch without its
        # `_retry_advice`: no connector was asked, so no "the connector reports"
        # sentence; there is no query result, so no no-signals sentence; and no
        # mention could have been marked, so no sign-in line.
        # Not `NO_REASON_GIVEN`, which names a connector, and none was asked.
        reason = _bounded(escape(entries.reason)) or "the read gave no reason"
        return (
            f"The event log could not be read, so the message signals in this "
            f"window are unknown: {reason}"
        )

    since = now - MESSAGE_WINDOW
    # The segment each entry came from, when the read named them (`LogRead`).
    segments: Sequence[str | None] = (
        entries.segments if isinstance(entries, LogRead) else (None,) * len(entries)
    )
    inside: list[tuple[datetime, EventEntry, str | None]] = []
    # Separate counts, because they are different facts and the section prints
    # what it counted. Folding them into one number forced one sentence to stand
    # for all of them, and that sentence — "carries no `ingested_at`" — was
    # false for two of the three.
    absent = 0
    unreadable = 0
    ahead = 0
    for entry, segment in zip(entries, segments, strict=True):
        if entry.category not in SIGNAL_CATEGORIES:
            # `SelfActionType` entries share this log and are not signals: their
            # subject is pm-ai, and a dashboard reporting pm-ai's own compaction
            # back to the PM as something to act on is noise wearing evidence's
            # clothes.
            continue
        at = _ingested_at(entry)
        if isinstance(at, _Unplaceable):
            if at is _Unplaceable.ABSENT:
                # Excluded from the window and counted, never dated from `now`.
                # An entry written before story `2e` stamped the field cannot
                # answer the question the window asks, and a substituted
                # timestamp is indistinguishable from a real one (AD-35).
                absent += 1
            else:
                # A field that is there and cannot be read is not a missing
                # field, and the calendar branch already makes exactly this
                # distinction between "could not be read" and "nothing there".
                unreadable += 1
            continue
        if at > now:
            # Writer or provider clock skew. Outside the window like anything
            # else, but the only exclusion this section used to make in silence.
            ahead += 1
            continue
        if since <= at:
            inside.append((at, entry, segment))

    # Decoded before the sort and the cap: whether a line mentions the PM
    # decides its place, and a line that cannot be decoded takes no place.
    mine = frozenset(str(handle) for handle in pm.handles)
    signals: list[_Signal] = []
    undecodable: list[tuple[str | None, str]] = []
    for at, entry, segment in inside:
        try:
            payload = decode_payload(entry)
        except MalformedEntry:
            payload = None
        if not isinstance(payload, MessagePayload):
            # Counted, never a crash and never silence (`23c`): one hand-edited
            # line used to take the whole dashboard down with it. A line that
            # decodes to some other payload is as unusable here as one that
            # does not decode at all.
            undecodable.append((segment, entry.entry_id or "<no id>"))
            continue
        # `None` is "mentions not recorded": no claim either way, so unmarked.
        mentioned = bool(mine.intersection(payload.mentions or ()))
        signals.append(_Signal(at, entry, payload, mentioned))

    footnote = _window_notes(
        absent=absent, unreadable=unreadable, ahead=ahead, undecodable=undecodable
    )
    sign_ins = "".join(
        f"\n\n{_unrecorded_sign_in(name)}" for name in pm.unrecorded
    ) + "".join(f"\n\n{_unchecked_row(name, why)}" for name, why in pm.unchecked)
    if not signals:
        return f"{_no_signals(since=since, now=now, tz=tz)}{sign_ins}{footnote}"

    # Mentions of the PM first, then newest first, then the entry id — total,
    # so a re-render is byte-identical. When the mentions alone exceed the cap,
    # the newest of them fill it, which this order gives for free.
    signals.sort(
        key=lambda s: (not s.mentioned, -s.at.timestamp(), s.entry.entry_id or "")
    )
    shown = signals[:MAX_SIGNAL_LINES]
    lines = "\n".join(_signal_line(signal, tz=tz, now=now) for signal in shown)
    hidden = signals[MAX_SIGNAL_LINES:]
    closing = (
        f"\n\n{_more_signals(len(hidden), sum(s.mentioned for s in hidden))}"
        if hidden
        else ""
    )
    return f"{lines}{closing}{sign_ins}{footnote}"


@dataclass(frozen=True, slots=True)
class _Signal:
    """One decoded message inside the window, and whether it mentions the PM."""

    at: datetime
    entry: EventEntry
    payload: MessagePayload
    mentioned: bool


def _more_signals(hidden: int, mentioning: int) -> str:
    """The closing line of a cut list — never a claim that the day held only these.

    Says how many of the hidden lines mention the PM when any do: the cap fills
    with the newest mentions, so older ones can be among the hidden.
    """
    if hidden == 1:
        line = (
            "1 more message signal in this window is not shown; it is in the "
            "event log."
        )
    else:
        line = (
            f"{hidden} more message signals in this window are not shown; they "
            f"are in the event log."
        )
    if mentioning == 1 and hidden == 1:
        line += " It mentions you."
    elif mentioning:
        line += f" {mentioning} of them mention{'s' if mentioning == 1 else ''} you."
    return line


REASON_LIMIT = 300
"""How many characters of a failure's reason the section shows before cutting."""


def _bounded(reason: str) -> str:
    """A reason cut at `REASON_LIMIT` with `…`, so one corrupt 16 KiB line —
    which the ledger's refusal quotes — cannot flood the section."""
    if len(reason) <= REASON_LIMIT:
        return reason
    return reason[:REASON_LIMIT].rstrip() + "…"


def _unchecked_row(name: str, why: str) -> str:
    """A connector row (or `connectors/`) that could not say whether it is the PM's."""
    return (
        f"Mentions could not be checked against `{code(name)}`: it "
        f"{_bounded(escape(why))}. A message mentioning you through it is not "
        f"marked."
    )


def _unrecorded_sign_in(instance: str) -> str:
    """One enabled Graph connector whose sign-in predates the recorded user id."""
    return (
        f"The sign-in for `{code(instance)}` predates pm-ai recording the user "
        f"id, so no message here is marked as mentioning you for that "
        f"connector. Run `pm-ai connector sign-in {code(instance)}` to record it."
    )


def _no_signals(*, since: datetime, now: datetime, tz: tzinfo) -> str:
    """Names the window and the vocabulary — a query, never a state of the world."""
    read = ", ".join(f"`{member.value}`" for member in SIGNAL_CATEGORIES)
    ignored = ", ".join(f"`{member.value}`" for member in SelfActionType)
    return (
        f"No {read} entries in the event log between "
        f"{_local(since, tz=tz, now=now, dated=True)} and "
        f"{_local(now, tz=tz, now=now, dated=True)}. This section reads "
        f"{read} only; {ignored} are pm-ai's own actions and are not signals."
    )


def _signal_line(signal: _Signal, *, tz: tzinfo, now: datetime) -> str:
    # Decoded, never looked up by name (AD-27): the line names these `p.channel`
    # and `p.excerpt`, and a literal lookup that misspells either reads as "not
    # recorded" — which is how every real message would have rendered as an
    # actor and nothing else.
    channel = signal.payload.channel
    excerpt = signal.payload.excerpt
    # The actor as the line carries it — a handle, not a name: the alias table
    # that would resolve it is saved nowhere, so nothing here resolves it.
    actor = signal.entry.actor
    sender = UNKNOWN_SENDER if actor == str(NO_PERSON) else escape(actor)
    parts = [f"- **{_local(signal.at, tz=tz, now=now)}**", sender]
    if channel:
        parts.append(f"in {escape(channel)}")
    line = " ".join(parts)
    if excerpt:
        line += f" — {escape(excerpt)}"
    if signal.mentioned:
        line += f" — {MENTIONS_YOU}"
    return line


def _window_notes(
    *,
    absent: int,
    unreadable: int,
    ahead: int,
    undecodable: Sequence[tuple[str | None, str]],
) -> str:
    """Everything the window excluded for a reason other than being old.

    A bounded `EventLog.read` drops these silently — correctly, since the range
    asks a question they cannot answer — so this is the only place a PM can
    learn that the count above is short, and by how many.

    One sentence per fact, because they are different facts. One counter
    printed under one wording made the dashboard say "carries no
    `ingested_at`" about an entry whose `ingested_at` was there and readable,
    which is the invented claim this module is arranged against.
    """
    notes: list[str] = []
    if absent:
        notes.append(
            f"{absent} message log {_carry(absent)} no `ingested_at`, so the "
            f"window could not place {_them(absent)}. Counted here rather than "
            f"dated from the local clock."
        )
    if unreadable:
        notes.append(
            f"{unreadable} message log {_carry(unreadable)} an `ingested_at` "
            f"that could not be read as an instant, so the window could not "
            f"place {_them(unreadable)}. Counted here rather than guessed at."
        )
    if ahead:
        notes.append(
            f"{ahead} message log {_carry(ahead)} an `ingested_at` later than "
            f"this render's instant, so the window — which ends there — "
            f"excluded {_them(ahead)}. Counted here rather than dropped in "
            f"silence; a stamp in the future is a clock disagreeing, not a "
            f"signal that did not happen."
        )
    if undecodable:
        # Its own counter: `unreadable` already means an `ingested_at` that
        # could not be read, and this is a line whose fields did not decode.
        count = len(undecodable)
        notes.append(
            f"{count} message log {_carry(count)} fields that could not be "
            f"decoded, so {'it is' if count == 1 else 'they are'} not listed"
            f"{_where_to_find(undecodable)}. Counted here rather than dropped in "
            f"silence."
        )
    return "".join(f"\n\n{note}" for note in notes)


UNDECODABLE_IDS_NAMED = 3
"""How many undecodable entries' ids the footnote names before saying "and N more"."""


def _where_to_find(undecodable: Sequence[tuple[str | None, str]]) -> str:
    """The segments and the first few entry ids, so the lines can be found by hand."""
    named = sorted({segment for segment, _ in undecodable if segment is not None})
    ids = sorted(entry_id for _, entry_id in undecodable)
    shown = ", ".join(f"`{code(entry_id)}`" for entry_id in ids[:UNDECODABLE_IDS_NAMED])
    more = len(ids) - UNDECODABLE_IDS_NAMED
    where = f" ({shown}"
    if more > 0:
        where += f" and {more} more"
    if named:
        where += " in " + ", ".join(f"`{code(segment)}`" for segment in named)
    return where + ")"


def _carry(count: int) -> str:
    return "entry carries" if count == 1 else "entries carry"


def _them(count: int) -> str:
    return "it" if count == 1 else "them"


class _Unplaceable(Enum):
    """Why an entry got no instant — two facts the section states separately."""

    ABSENT = "absent"
    """No `ingested_at` field at all: an entry written before story `2e`."""

    UNREADABLE = "unreadable"
    """A field that is there and yields no instant — malformed, or naive.

    Naive belongs here rather than being converted: there is no zone to assume,
    and assuming one is the silent wrong answer. It is a value that could not be
    read *as an instant*, which is precisely what the sentence says.
    """


def _ingested_at(entry: EventEntry) -> datetime | _Unplaceable:
    """The write clock the window is measured on, or why there is none.

    An aware value at any offset is a real instant and is converted to UTC —
    `2026-09-09T10:30:00+02:00` is 08:30Z and belongs in a window that holds
    08:30Z. Refusing it dropped a placeable signal and then printed a reason
    that was not the one computed. Only `now` and the meeting starts are held to
    UTC-on-arrival: those come from this codebase's own callers, while this
    field is read back off a ledger line.
    """
    raw = dict(entry.fields).get("ingested_at")
    if raw is None:
        return _Unplaceable.ABSENT
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        return _Unplaceable.UNREADABLE
    if at.tzinfo is None or at.utcoffset() is None:
        return _Unplaceable.UNREADABLE
    return at.astimezone(timezone.utc)


# ── 3-Tier Strategic Milestones ──────────────────────────────────────────────


def _strategic_milestones(goals: GoalRegister) -> str:
    """The three tiers, which are `GoalDomain` — Project, Team, Personal.

    The domain, not the horizon. `prd.md:63` names `strategic_goals.md` as
    "3-Tier Goals (Project, Team, Personal Career Goals)" and `alignment_tag`'s
    docstring says the tier in `[Strategic Alignment: <Tier>]` is the domain.
    The word "Milestones" in CAP-9's heading is what suggests otherwise, and the
    heading is CAP-9's to spell.
    """
    if not goals.present:
        return (
            f"No strategic goals declared — author `{GOALS_PATH}`. Until it "
            f"exists nothing on any surface can carry a resolvable "
            f"`[Strategic Alignment: <Tier>]` tag."
        )
    if not goals:
        return (
            f"No strategic goals declared. `{GOALS_ARTIFACT}` is there and "
            f"holds none, so this is a file to fill in rather than one to write."
        )

    lines: list[str] = []
    for domain in GoalDomain:
        # Sorted by id, which is a total order and independent of where a goal
        # sits in the file: `goal_register` states that order in the file
        # carries no meaning, so re-ordering the file must not re-order this.
        tier = sorted(
            (goal for goal in goals.values() if goal.domain is domain),
            key=lambda goal: goal.goal_id,
        )
        lines.append(f"- **{domain.value.capitalize()}**")
        if not tier:
            lines.append(
                f"  - No {domain.value} goals declared in `{GOALS_ARTIFACT}`."
            )
            continue
        lines.extend(f"  - {_goal_line(goal)}" for goal in tier)
    return "\n".join(lines)


def _goal_line(goal: Goal) -> str:
    """The id first, because it is what a citation resolves to."""
    return f"`goal:{code(goal.goal_id)}` ({goal.horizon.value}) {escape(goal.title)}"


# ── Leadership Notes ─────────────────────────────────────────────────────────


def _leadership_notes() -> str:
    """The heading renders; the synthesis behind it does not exist.

    Recorded as a deviation rather than papered over. CAP-9's "no empty section"
    clause is met in the sense that no section is blank, and knowingly not met
    in the sense CAP-9 intended — for this section always, and for Proactive
    Enablement until `33d`.
    """
    return (
        "Synthesis is not enabled in this build. This dashboard is rendered by "
        "a deterministic function with no model in its path, so there is "
        "nothing here that was written rather than computed."
    )


# ── Shared ───────────────────────────────────────────────────────────────────


def escape(text: str) -> str:
    r"""One escaper for every interpolated string, applied without exception.

    Goal titles and meeting titles are hand-authored or provider-supplied and
    reach the same Markdown; an actor name comes from an alias table seeded by
    hand. None of the three is trusted to be one line of inert text, and the
    failure is not cosmetic: a newline in a meeting title ends the bullet and
    everything after it reads as a new item, and a `|` would break any table a
    later section grows.

    Two classes, escaped differently on purpose. A control character has no
    Markdown spelling, so it becomes a visible `\n`/`\r`/`\t` — the value stays
    on its line and the reader can see what was in it. A structural character
    has one, so it is backslash-escaped and renders as itself.

    `_INVISIBLE` is the first class with a wider net than C0: U+2028 and the
    bidi overrides sit above the `char < " "` test and do a control character's
    damage, so they take a control character's visible spelling.
    """
    out: list[str] = []
    # Stripped *before* escaping, not after: once a trailing newline has become
    # the two characters `\` and `n` no strip can find it, and a title padded by
    # its provider would render with a visible `\n` hanging off the end.
    for char in text.strip():
        if char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif char in _STRUCTURAL:
            out.append(f"\\{char}")
        elif char < " " or char == "\x7f":
            # Everything else in C0 and DEL: invisible in a text file, and a
            # `\x0b` in a title is the kind of thing that makes two renders of
            # the same data look identical and diff.
            out.append(f"\\x{ord(char):02x}")
        elif char in _INVISIBLE:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return "".join(out)


def code(text: str) -> str:
    """The same value, but inside a code span — a different grammar, so a
    different escaper.

    Backslash escapes mean nothing between backticks: `` `g\\_a` `` renders the
    backslash, so running `escape` over a goal id would put a visible `\\_` in
    every citation on the page. What a code span cannot survive is a backtick or
    a line break, and those are the two this handles: the backtick is replaced
    rather than escaped, because there is no escape for it in the span grammar,
    and a control character becomes its visible spelling exactly as in `escape`.

    Goal ids are charset-checked by `goal_register.GOAL_ID` and could not
    contain either. The register is a plain `dict` subclass that anything may
    construct, though, so this does not depend on the parser having built it.
    """
    out: list[str] = []
    for char in text.strip():
        if char == "`":
            out.append("'")
        elif char in "\n\r\t":
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}[char])
        elif char < " " or char == "\x7f":
            out.append(f"\\x{ord(char):02x}")
        elif char in _INVISIBLE:
            # The same set as `escape`, for the same reason: a code span is
            # still one line of a Markdown file, and U+2028 ends it.
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return "".join(out)


_INVISIBLE = frozenset(
    chr(point)
    for point in (
        {0x2028, 0x2029}  # LINE SEPARATOR, PARAGRAPH SEPARATOR
        | set(range(0x202A, 0x202F))  # the bidi embeddings and overrides
        | set(range(0x2066, 0x206A))  # the bidi isolates
    )
)
r"""Characters that are not C0 and behave like one anyway.

The same class as the `\n` this file already escapes, one plane up and therefore
missed by the `char < " "` test. U+2028 is a line break to any renderer that
honours the Unicode definition, so it ends a bullet and everything after it
reads as a new item — exactly the failure `escape` exists to prevent. U+202E
reverses the visible order of the rest of the line, so a title carrying one
decides how the *rest of the dashboard's line* reads; the isolates are the same
mechanism with a different scope.

Rendered as a visible `\uXXXX` rather than backslash-escaped, following the C0
branch: there is no Markdown escape for a character with no glyph, and a
visible spelling keeps the value on its line where the reader can see what was
in it.

Declared by code point rather than as a string of literals, because a literal
here is invisible in the source too — a set nobody can read in a diff is a set
nobody can review.
"""


_STRUCTURAL = frozenset("|`[]<>")
"""What changes the *shape* of a line rather than how it is emphasised.

A pipe ends a table cell, a backtick opens a code span, `[...]` is a link or a
reference, and `<...>` is raw HTML or an autolink — each one turns the rest of
the interpolated value into something other than text.

`*` and `_` are deliberately **not** here, and the omission is the considered
half of this set. They italicise; they do not restructure. Escaping them costs a
visible backslash in every `u_dana`, every `#payments` and every
`goal:g_staff_eng` on the page a PM reads at 07:00 — a permanent legibility tax
on every line, paid to prevent a title rendering in italics. `#` is out for a
different reason: it is a heading only at the start of a line, and no
interpolation here can reach one, because a literal newline is escaped to `\\n`
before anything is placed.
"""


def _local(
    at: datetime, *, tz: tzinfo, now: datetime, dated: bool = False
) -> str:
    """An instant in the PM's display zone, dated only when the date is not today.

    A dashboard is read on the day it names, so `09:00` is what a reader wants
    on almost every line. A meeting that started yesterday and ends this morning
    is the case where the bare time would be a lie by omission, so those carry
    their date — which is also what makes the midnight-spanning row visible in
    the output rather than only in the ordering.
    """
    local = at.astimezone(tz)
    if dated or local.date() != now.astimezone(tz).date():
        return local.strftime("%Y-%m-%d %H:%M")
    return local.strftime("%H:%M")


def _assert_utc(value: datetime, *, name: str) -> None:
    """Aware UTC or nothing, following `clocks._assert_comparable`'s precedent.

    A `ValueError` rather than `ImplausibleTimestamp`: nothing here is judging
    whether a provider's clock can be believed. The instant arrives from the
    caller, so a naive or offset one is our bug, and `clocks.validate_occurred_at`
    makes the same distinction for the same reason.
    """
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(
            f"{name}={value!r} is not aware UTC. Every producer in this codebase "
            f"emits aware UTC and the display zone is applied here, once — so an "
            f"offset instant would be converted a second time and the dashboard "
            f"would state a time that never happened."
        )
