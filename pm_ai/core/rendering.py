"""`daily_dashboard.md`, rendered — both of them, from one set of sections.

Two files carry that name. `render_dashboard` writes the personal one, which is
what CAP-9 asks for: `~/.manager-ai/memory/daily_dashboard.md` by 07:00 with
exactly four headed sections — Time-Critical Activities, Proactive Enablement,
3-Tier Strategic Milestones, Leadership Notes — and no empty section.
`render_project_dashboard` writes a project's, which CAP-9 does not govern and
which carries the three sections its own sources support — the third, Team
Activity, from the project's GitLab record (`23g`). Nothing turned meetings,
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

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from pm_ai.core.cadence import (
    FIRST_LOOK_BACK,
    LOOK_BACK_CAP,
    LookBack,
    LookBackKind,
    look_back,
)
from pm_ai.core.disclosure_ledger import MONTHLY_TARGET_USD, MonthlyTotal
from pm_ai.core.goal_register import ARTIFACT as GOALS_ARTIFACT
from pm_ai.core.event_log import LogRead
from pm_ai.core.event_log import Unplaceable as _Unplaceable
from pm_ai.core.event_log import ingested_at as _ingested_at
from pm_ai.core.event_log import posted_at as _posted_at
from pm_ai.core.goal_register import GoalRegister
from pm_ai.core.team_activity import (
    NO_ISSUE_NOTES,
    STALE_AFTER,
    TEXT_LIMIT,
    Assignee,
    IssueNotes,
    IssueRow,
    TeamActivity,
    WorkAnswer,
    build_team_activity,
)
from pm_ai.domain.event_entries import (
    EventEntry,
    MalformedEntry,
    SelfActionType,
)
from pm_ai.domain.events import MessagePayload, ObservedEventType
from pm_ai.domain.goals import Goal, GoalDomain
from pm_ai.domain.handles import GRAPH_APP, NO_PERSON, PersonHandle
from pm_ai.domain.harvest import (
    HarvestFailure,
    NoCalendarConnector,
    PartialCalendar,
)
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.meeting_listing import summarize_command
from pm_ai.domain.meeting_summaries import SummaryMaker, SummaryStateKind
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.payload_fields import decode_payload
from pm_ai.domain.renders import (
    DigestUnreadable,
    MeetingDigest,
    MeetingDigests,
    ProjectMeetingDigests,
    ScopeUnreadable,
)
from pm_ai.domain.work import Section, SectionStatus

__all__ = [
    "APP_MARKER",
    "CalendarAnswer",
    "EXCERPT_LIMIT",
    "MAX_DIGEST_ITEMS",
    "MAX_MEETING_DIGESTS",
    "SpendAnswer",
    "EventLogAnswer",
    "GOALS_PATH",
    "HARVESTED_MARKER",
    "HEADINGS",
    "LEADERSHIP_NOTES",
    "LEADERSHIP_SENTENCE",
    "QUOTED_SENTENCE",
    "MAX_SIGNAL_LINES",
    "MENTIONS_YOU",
    "MESSAGE_WINDOW",
    "NO_MEETINGS",
    "NO_REASON_GIVEN",
    "PROACTIVE_ENABLEMENT",
    "PROJECT_HEADINGS",
    "PmHandles",
    "STRATEGIC_MILESTONES",
    "TEAM_ACTIVITY",
    "TIME_CRITICAL",
    "UNKNOWN_SENDER",
    "code",
    "cut_at_word",
    "escape",
    "fold_breaks",
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


SpendAnswer = MonthlyTotal | HarvestFailure
"""The month's frontier spend as `DisclosureLedger.monthly_total` answered it
(story 7d, AD-17), or why the disclosure ledger could not be read."""


MAX_MEETING_DIGESTS = 10
"""Meetings listed with their summary in Time-Critical Activities (story 7d),
the most recently recorded; the rest are counted."""

MAX_DIGEST_ITEMS = 5
"""Open action items listed under one meeting (story 7d); the rest are counted."""

EXCERPT_LIMIT = 200
"""A summary's excerpt (and a digest's title, owner, due and model) cut to at
most this many characters, its `…` included, before escaping (story 7d).

The order is `23f`'s — fold, cut, escape — so the limit counts the text as
recorded; an escaped character (`\\|`, `\\*`) adds to the rendered length."""


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
TEAM_ACTIVITY = "Team Activity"

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
    TEAM_ACTIVITY,
)
"""The project dashboard's three, which are what its sources support.

Not a subset of CAP-9's four by omission — CAP-9 does not bind this file. Its
success criterion names `~/.manager-ai/memory/daily_dashboard.md` *by path*
("exactly the four headed sections … and no empty section"), which is the
personal artifact. The project render's inputs are project-scope meetings, a
project-scope event log and the project's GitLab record (`8t`), and those
sources support exactly these three sections. Team Activity (`23g`, Andrei's
decision of 2026-10-16) is third: each member's open assigned issues and what
moved on them, never a ranking or a comparison between people (AD-25).

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

MESSAGE_WINDOW = FIRST_LOOK_BACK
"""How far back Proactive Enablement looks when no earlier render is recorded.

Since story 9b the window starts at the scope's last successful render — a
signal the PM already saw on the page is not news — and this is only the
fallback for a scope with none, clamped like every window to
`cadence.LOOK_BACK_CAP`. The section names whichever it used: an empty section
that says "in the window" without saying which window has not told the reader
anything.
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
"""What the absence actor `none:` renders as. Every other actor renders as the
sender's stored display name when the line carries one (`23f`), otherwise as
the handle written."""

APP_MARKER = "(app)"
"""What follows an application's display name on a signal line (`23f`), so a
bot that names itself like a colleague still reads as a bot."""

HARVESTED_MARKER = "(harvested)"
"""What follows a signal line's time when it is the harvest time (`23f`): the
posted time was absent, unreadable, flagged implausible, or after the render."""

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
    since: LookBack | None = None,
    digests: MeetingDigests | None = None,
    spend: SpendAnswer | None = None,
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

    `since` is where Proactive Enablement's window starts and why there
    (`cadence.look_back`, story 9b): the scope's last render, or `None` for no
    render recorded, which looks back `MESSAGE_WINDOW`.

    `digests` (story 7d) are the meetings whose summary, or its failure, was
    recorded in the same window — personal meetings and every enrolled
    project's — listed after the day's schedule; `spend` is the month's
    frontier spend, one line in Leadership Notes (AD-17). `None` renders
    neither, so a caller that read neither prints nothing about them.

    `now` must be aware UTC and `tz` must be supplied. Rendering the same inputs
    twice returns byte-identical output: nothing here reads a clock and every
    ordering is total.
    """
    tz = _require_tz(tz)
    _assert_utc(now, name="now")
    if digests is not None and not isinstance(digests, MeetingDigests):
        raise TypeError(
            f"render_dashboard takes the personal page's MeetingDigests, not "
            f"{type(digests).__name__}."
        )

    return _document(
        HEADINGS,
        (
            _time_critical(meetings, now=now, tz=tz)
            + _digest_block(digests, now=now, tz=tz, since=since),
            _proactive_enablement(entries, pm=pm, now=now, tz=tz, since=since),
            _strategic_milestones(goals),
            _leadership_notes(spend, quoted=_quotes_a_summary(digests)),
        ),
    )


def render_project_dashboard(
    meetings: CalendarAnswer,
    entries: EventLogAnswer,
    now: datetime,
    *,
    tz: tzinfo | None,
    pm: PmHandles,
    since: LookBack | None = None,
    work: WorkAnswer = None,
    issue_notes: IssueNotes = NO_ISSUE_NOTES,
    digests: ProjectMeetingDigests | None = None,
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
    `entries`, `pm` and `since` mean what they mean there: the PM's handles come
    from pm-ai's own connector settings, which the wall does not forbid (`23c`),
    and `since` is this scope's own last render (story 9b).

    `work` is `8t`'s record of the project's GitLab members, issues and merge
    requests as `StoragePort.project_work(scope)` answered it, or the reason it
    could not be read; `None` is no record. `issue_notes` is `7e`'s slot: rows
    placed under each issue Team Activity shows, and closing lines; its default
    prints nothing, so a render without it is byte-identical to one with it.

    `digests` (story 7d) is this project's meetings whose summary, or its
    failure, was recorded since this scope's last render. A
    `ProjectMeetingDigests`, whose constructor refuses any other scope's
    digest, so a personal summary has no way onto this page — the wall as a
    type, beside the missing goals parameter. Owners were resolved to members
    by `7e`'s rule before it was built, and an item Team Activity shows under
    an issue is already left out of it. There is no spend line here: the
    disclosure ledger names every scope (AD-17).

    Rendering the same inputs twice returns byte-identical output, and so does
    rendering them through the other function: the first two sections are the
    same two functions.
    """
    tz = _require_tz(tz)
    _assert_utc(now, name="now")
    if digests is not None and not isinstance(digests, ProjectMeetingDigests):
        raise TypeError(
            f"render_project_dashboard takes ProjectMeetingDigests only, not "
            f"{type(digests).__name__}: a personal digest has no place on a "
            f"project page (AD-25)."
        )

    activity = build_team_activity(
        work, entries, now=now, since=since, issue_notes=issue_notes
    )
    return _document(
        PROJECT_HEADINGS,
        (
            _time_critical(meetings, now=now, tz=tz)
            + _digest_block(digests, now=now, tz=tz, since=since),
            _proactive_enablement(entries, pm=pm, now=now, tz=tz, since=since),
            _team_activity(activity, notes=issue_notes, now=now, tz=tz),
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
        if (meeting.duration_minutes or 0) < 0:
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

    # All-day markers first, each on its own line, and never part of the
    # collapse below (`23e`): a marker is placed by its own date, has no span
    # anybody sat through, and "has ended" at 07:00 would be a claim about a
    # day still in progress. The collapse, its count and its last finish are
    # decided over timed meetings only.
    markers = [meeting for meeting in ordered if meeting.all_day_date is not None]
    timed = [meeting for meeting in ordered if meeting.all_day_date is None]
    lines = [_meeting_line(meeting, now=now, tz=tz) for meeting in markers]
    if timed:
        lines.append(_timed(timed, now=now, tz=tz))
    return "\n".join(lines)


def _timed(ordered: Sequence[Meeting], *, now: datetime, tz: tzinfo) -> str:
    """The day's timed meetings: one line each, or one sentence when all ended."""
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
    count = f"{attendees} attendee" + ("" if attendees == 1 else "s")
    # An all-day marker (`23e`) has no start time in the display zone worth
    # showing — west of UTC its midnight-UTC start reads as yesterday evening —
    # and no state or duration: it is a marker on the date, not a span.
    all_day = meeting.all_day_date is not None
    facts = (
        [count]
        if all_day
        else [_state(meeting, now=now), f"{meeting.duration_minutes} min", count]
    )
    if meeting.tentative:
        # Carried in memory and written to no file (`meetings.py`), so a surface
        # that reads the calendar live is the only place it can be said at all.
        facts.append("your response is tentative")
    if meeting.cancelled:
        # An ended meeting the calendar now says was cancelled (`23e`, finding
        # 7). In memory only, as `tentative` is; a cancelled meeting not yet
        # ended never reaches this page.
        facts.append("cancelled")
    when = "all day" if all_day else _local(meeting.start, tz=tz, now=now)
    return f"- **{when}** {title} — {', '.join(facts)}"


def _state(meeting: Meeting, *, now: datetime) -> str:
    if _ended(meeting, now=now):
        return "ended"
    if meeting.start <= now:
        return "in progress"
    return "upcoming"


def _end(meeting: Meeting) -> datetime:
    # A meeting read live from the calendar always has a length (story 11e:
    # only a record pm-ai minted may not, and no dashboard lists one).
    return meeting.start + timedelta(minutes=meeting.duration_minutes or 0)


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


# ── Meeting summaries, after the schedule (story 7d) ─────────────────────────


_MAKER_WORDS = {SummaryMaker.LOCAL: "the local model", SummaryMaker.FRONTIER: "Claude"}
"""Who made a summary, as the page and the record's provenance line name them."""


def _digest_block(
    digests: MeetingDigests | ProjectMeetingDigests | None,
    *,
    now: datetime,
    tz: tzinfo,
    since: LookBack | None,
) -> str:
    """The meetings whose summary was recorded in the window, after the schedule.

    A nested list under Time-Critical Activities, no heading of its own (CAP-9's
    four stay four). The window is Proactive Enablement's (story 9b): since the
    scope's last render, 24 hours when none, never more than 7 days, and the
    block names it the same way whether or not anything is listed. Every
    excerpt is quoted from its record, never synthesized here. Empty when no
    digests were handed in.
    """
    if digests is None:
        return ""
    window = since if since is not None else look_back(None, now)
    span = (
        f"between {_local(window.since, tz=tz, now=now, dated=True)} and "
        f"{_local(now, tz=tz, now=now, dated=True)} — {_window_words(window, now=now, tz=tz)}"
    )
    notes = [_scope_unreadable(failure) for failure in digests.unread]
    if isinstance(digests, MeetingDigests):
        notes.extend(_not_enrolled(scope) for scope in digests.not_enrolled)
        if digests.enrolment_unchecked is not None:
            reason = _bounded(escape(digests.enrolment_unchecked)) or "the read gave no reason"
            notes.append(
                _sentence(
                    f"Whether a project no longer enrolled has meeting summaries in "
                    f"this window could not be checked: {reason}"
                )
            )
    else:
        if digests.aliases_unread is not None:
            reason = _bounded(escape(digests.aliases_unread)) or "the read gave no reason"
            notes.append(
                _sentence(
                    f"The saved alias table could not be read, so action item owners "
                    f"were matched by exact display name or username only, and the "
                    f"others show as unassigned: {reason}"
                )
            )
        if digests.confidentiality_unknown is not None:
            why = _clause(_bounded(escape(digests.confidentiality_unknown)))
            notes.append(
                _sentence(
                    f"Whether an issue is confidential cannot be told, since {why}, so "
                    f"the text of every action item linked to an issue is withheld"
                )
            )
        if digests.links_unread:
            notes.append(
                "The meeting updates' links to issues could not be read, so which "
                "action items are on a confidential issue cannot be told, and the text "
                "of every action item here is withheld."
            )
    tail = "".join(f"\n\n{note}" for note in notes)
    if not digests.shown:
        # An unreadable scope is stated, never folded into "no summaries".
        where = " in the scopes that could be read" if digests.unread else ""
        return f"\n\nNo meeting summary was recorded{where} {span}.{tail}"
    personal_page = isinstance(digests, MeetingDigests)
    lines: list[str] = []
    for digest in digests.shown:
        if isinstance(digest, DigestUnreadable):
            reason = _bounded(escape(digest.reason)) or "the read gave no reason"
            lines.append(
                f"- meeting `{code(digest.meeting_id)}` in `{code(str(digest.scope))}`: "
                f"its record could not be read, so its summary is not shown: {reason}"
            )
        else:
            lines.extend(
                _digest_lines(
                    digest,
                    now=now,
                    tz=tz,
                    name_scope=personal_page and digest.scope.kind is not ScopeKind.PERSONAL,
                )
            )
    if digests.earlier:
        n = digests.earlier
        lines.append(
            f"- and {n} earlier {'summary' if n == 1 else 'summaries'} recorded in "
            f"this window {'is' if n == 1 else 'are'} not shown"
        )
    heading = f"Meeting summaries recorded {span}, quoted from the meeting records:"
    return "\n\n" + heading + "\n\n" + "\n".join(lines) + tail


def _scope_unreadable(failure: ScopeUnreadable) -> str:
    """One scope whose summaries could not be read, in one line naming it."""
    reason = _bounded(escape(failure.reason)) or "the read gave no reason"
    if failure.scope is None:
        return _sentence(f"The meeting summaries could not be read: {reason}")
    return _sentence(
        f"The meeting summaries of `{code(str(failure.scope))}` could not be read, "
        f"so none of its meetings is listed: {reason}"
    )


def _not_enrolled(scope: DataScope) -> str:
    """A project with summaries in the window that is no longer enrolled: skipped, not failed."""
    return (
        f"`{code(str(scope))}` has meeting summaries recorded in this window and is "
        f"no longer enrolled, so its meetings are not listed."
    )


def _made(digest: MeetingDigest) -> str:
    """Who made the record's summary, from its parsed fields: maker, model, how."""
    maker = digest.maker
    if maker is None:
        return ""
    how = "on request" if maker.requested else "automatically"
    model = _shown_text(maker.model)
    who = _MAKER_WORDS[maker.made_by] + (f" {model}" if model else "")
    return f"made by {who}, {how}"


def _digest_reason(digest: MeetingDigest) -> str:
    return _clause(_bounded(escape(digest.reason))) or "no reason was recorded"


def _shown_text(text: str | None) -> str:
    """A short field of a digest (title, owner, due, model): folded, cut, escaped.

    Each comes from a record or a model's answer, so each is bounded as the
    excerpt is: cut at a word to `EXCERPT_LIMIT` before escaping.
    """
    if text is None:
        return ""
    return escape(cut_at_word(text, EXCERPT_LIMIT))


def _digest_lines(
    digest: MeetingDigest, *, now: datetime, tz: tzinfo, name_scope: bool = False
) -> list[str]:
    """One meeting: start, title, excerpt or state, maker, open items, id and command.

    The excerpt is the summary folded, cut at a word to `EXCERPT_LIMIT`, then
    escaped (`23f`'s order). A row waiting or failed while the record holds an
    earlier summary shows that summary and the newer state; with none, the
    state alone. `nothing said` has no excerpt. `name_scope` names a project
    meeting's scope beside its title, on the personal page.
    """
    title = _shown_text(digest.title) or "(untitled)"
    head = f"- **{_local(digest.start, tz=tz, now=now)}** {title}"
    if name_scope:
        head += f" (`{code(str(digest.scope))}`)"
    state = digest.state
    waiting = f"waiting for {_MAKER_WORDS[digest.waiting_for]}"
    if state is SummaryStateKind.NOTHING_SAID:
        head += " — the transcript held no words after cleaning"
    elif digest.summary is not None:
        excerpt = escape(cut_at_word(digest.summary, EXCERPT_LIMIT))
        head += f" — {excerpt or '(an empty summary)'}"
        made = _made(digest)
        if made:
            head += f" ({made})"
        if state is SummaryStateKind.WAITING:
            head += f"; a newer summary is {waiting}"
        elif state is SummaryStateKind.FAILED:
            head += f"; a newer summary failed: {_digest_reason(digest)}"
    elif state is SummaryStateKind.WAITING:
        head += f" — summary {waiting}"
    elif state is SummaryStateKind.FAILED:
        head += f" — summary failed: {_digest_reason(digest)}"
    else:
        head += " — recorded as summarized, but the record holds no summary"
    lines = [head]
    for item in digest.items:
        owner = _shown_text(item.owner) or "unassigned"
        what = (
            "an action item on a confidential issue"
            if item.confidential
            else _shown_text(item.what) or "(no text recorded)"
        )
        line = f"  - {owner}: {what}"
        due = _shown_text(item.due)
        if due:
            line += f" ({due})"
        # Story 13a: each item is waiting for review or approved.
        if item.approved:
            line += ", approved"
        elif item.review_by is not None:
            line += f", to review by {item.review_by.astimezone(tz).date().isoformat()}"
        lines.append(line)
    scope_words = code(str(digest.scope))
    meeting_words = code(digest.meeting_id)
    listing = f"pm-ai proposal list --scope {scope_words} --meeting {meeting_words}"
    if digest.hidden:
        n = digest.hidden
        lines.append(
            f"  - and {n} more open action {'item' if n == 1 else 'items'}: `{listing}`"
        )
    if digest.placed_count:
        n = digest.placed_count
        under = ", ".join(f"#{iid}" for iid in digest.placed)
        lines.append(
            f"  - {n} open action {'item' if n == 1 else 'items'} shown under "
            f"{under} in Team Activity"
        )
    if digest.unreadable_items:
        n = digest.unreadable_items
        lines.append(
            f"  - {n} open action {'item' if n == 1 else 'items'} could not be read "
            f"from the proposals store, so {'it is' if n == 1 else 'they are'} not listed"
        )
    if (
        state is not SummaryStateKind.NOTHING_SAID
        and digest.summary is not None
        and not (
            digest.items or digest.hidden or digest.placed_count or digest.unreadable_items
        )
    ):
        lines.append("  - no open action item")
    if digest.expired:
        n = digest.expired
        lines.append(
            f"  - {n} action {'item' if n == 1 else 'items'} expired without review: "
            f"`{listing} --state expired`"
        )
    if digest.waiting:
        lines.append(
            f"  - to review: `pm-ai proposal review --scope {scope_words} --meeting "
            f"{meeting_words}`"
        )
    meeting = code(digest.meeting_id)
    # Story 11f: `--again` when the record holds a summary, or the re-summary
    # guard would refuse the command this line prints.
    command = summarize_command(
        code(str(digest.scope)), meeting, held=digest.summary is not None
    )
    lines.append(f"  - meeting `{meeting}`; to ask for a summary: `{command}`")
    return lines


# ── Proactive Enablement ─────────────────────────────────────────────────────


def _proactive_enablement(
    entries: EventLogAnswer,
    *,
    pm: PmHandles,
    now: datetime,
    tz: tzinfo,
    since: LookBack | None = None,
) -> str:
    """Message signals inside the window, or why there are none to show.

    The window runs from `since` — the scope's last render, 24 hours when none
    is recorded, never more than 7 days (story 9b) — to `now`, and the section
    says which of those it is: as its first line above the signals, or inside
    the empty-window sentence.

    A log that could not be read is stated, with its reason bounded, and nothing
    else is said. Otherwise: the signals decoded, marked and ordered — mentions
    of the PM first, then newest first by the time each line is labelled with
    (the posted time, or the harvest time when that cannot be used, `23f`) — at
    most `MAX_SIGNAL_LINES` of them with
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

    window = since if since is not None else look_back(None, now)
    since_at = window.since
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
        if since_at <= at:
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
        # Selected on the harvest time, labelled with the posted time (`23f`).
        posted = _posted_at(entry, now=now)
        signals.append(
            _Signal(
                label=at if posted is None else posted,
                harvested=posted is None,
                entry=entry,
                payload=payload,
                mentioned=mentioned,
            )
        )

    footnote = _window_notes(
        absent=absent, unreadable=unreadable, ahead=ahead, undecodable=undecodable
    )
    sign_ins = "".join(
        f"\n\n{_unrecorded_sign_in(name)}" for name in pm.unrecorded
    ) + "".join(f"\n\n{_unchecked_row(name, why)}" for name, why in pm.unchecked)
    if not signals:
        return f"{_no_signals(window=window, now=now, tz=tz)}{sign_ins}{footnote}"

    # Mentions of the PM first, then newest first by the label's time, then the
    # entry id — total, so a re-render is byte-identical, and the list reads in
    # the order of its own labels (`23f`). When the mentions alone exceed the
    # cap, the newest of them fill it, which this order gives for free.
    signals.sort(
        key=lambda s: (not s.mentioned, -s.label.timestamp(), s.entry.entry_id or "")
    )
    shown = signals[:MAX_SIGNAL_LINES]
    lines = "\n".join(_signal_line(signal, tz=tz, now=now) for signal in shown)
    hidden = signals[MAX_SIGNAL_LINES:]
    closing = (
        f"\n\n{_more_signals(len(hidden), sum(s.mentioned for s in hidden))}"
        if hidden
        else ""
    )
    heading = (
        f"Message signals {_window_words(window, now=now, tz=tz)}, up to "
        f"{_local(now, tz=tz, now=now, dated=True)}:"
    )
    return f"{heading}\n\n{lines}{closing}{sign_ins}{footnote}"


@dataclass(frozen=True, slots=True)
class _Signal:
    """One decoded message inside the window, and whether it mentions the PM.

    `label` is the instant the line shows and is ordered by: the posted time,
    or the harvest time when `harvested` (`23f`). The window selected the entry
    on its harvest time either way.
    """

    label: datetime
    harvested: bool
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


def _no_signals(*, window: LookBack, now: datetime, tz: tzinfo) -> str:
    """Names the window, why it starts there, and the vocabulary — a query,
    never a state of the world."""
    read = ", ".join(f"`{member.value}`" for member in SIGNAL_CATEGORIES)
    ignored = ", ".join(f"`{member.value}`" for member in SelfActionType)
    return (
        f"No {read} entries in the event log between "
        f"{_local(window.since, tz=tz, now=now, dated=True)} and "
        f"{_local(now, tz=tz, now=now, dated=True)} — "
        f"{_window_words(window, now=now, tz=tz)}. This section reads "
        f"{read} only; {ignored} are pm-ai's own actions and are not signals."
    )


def _hours(delta: timedelta) -> str:
    return f"{round(delta.total_seconds() / 3600)} hours"


def _days(delta: timedelta) -> str:
    return f"{delta.days} days"


def _window_words(window: LookBack, *, now: datetime, tz: tzinfo) -> str:
    """Why the window starts where it does (story 9b), as the section says it.

    One of four: since the scope's last render; the last 24 hours, no render
    being recorded; or the last 7 days, the recorded render being older than
    that or later than this render (a clock stepped back). Each names only
    what was computed.
    """
    kind = window.kind
    if kind is LookBackKind.SINCE_LAST_RENDER:
        return f"since the last render at {_local(window.since, tz=tz, now=now, dated=True)}"
    if kind is LookBackKind.NO_RENDER_RECORDED:
        return f"in the last {_hours(FIRST_LOOK_BACK)} (no earlier render recorded)"
    recorded = window.last_render
    at = "" if recorded is None else f", at {_local(recorded, tz=tz, now=now, dated=True)},"
    if kind is LookBackKind.CLAMPED_OLDER:
        return (
            f"in the last {_days(LOOK_BACK_CAP)} (the earlier render{at} was "
            f"older, so the window was clamped)"
        )
    return (
        f"in the last {_days(LOOK_BACK_CAP)} (the recorded render{at} is later "
        f"than this one, so the window was clamped)"
    )


def _signal_line(signal: _Signal, *, tz: tzinfo, now: datetime) -> str:
    # Decoded, never looked up by name (AD-27): the line names these `p.channel`
    # and `p.excerpt`, and a literal lookup that misspells either reads as "not
    # recorded" — which is how every real message would have rendered as an
    # actor and nothing else.
    channel = signal.payload.channel
    excerpt = signal.payload.excerpt
    parts = [f"- **{_signal_time(signal, tz=tz, now=now)}**", _sender(signal)]
    if channel:
        parts.append(f"in {escape(channel)}")
    line = " ".join(parts)
    if excerpt:
        line += f" — {escape(excerpt)}"
    if signal.mentioned:
        line += f" — {MENTIONS_YOU}"
    return line


def _signal_time(signal: _Signal, *, tz: tzinfo, now: datetime) -> str:
    """The line's label: the posted time, or the harvest time marked as such.

    Both dated by the one rule (`_local`) when not today (`23f`).
    """
    when = _local(signal.label, tz=tz, now=now)
    return f"{when} {HARVESTED_MARKER}" if signal.harvested else when


def _sender(signal: _Signal) -> str:
    """Who posted it, as the line shows it — display text, never identity.

    The stored display name when the line carries one, with `(app)` after an
    application's (`23f`); otherwise the handle as written, which is what every
    line ingested before `23f` shows. Nothing here resolves a handle or
    compares a name: whether the line mentions the PM was decided on handles.
    `none:` is the unknown sender even if a hand-edited line carries a name.
    """
    actor = signal.entry.actor
    if actor == str(NO_PERSON):
        return UNKNOWN_SENDER
    name = signal.payload.sender_name
    shown = escape(name) if name else ""
    if not shown:
        return escape(actor)
    if actor.startswith(f"{GRAPH_APP}:"):
        return f"{shown} {APP_MARKER}"
    return shown


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


# ── Team Activity (project only, `23g`) ──────────────────────────────────────


_SECTION_LABELS = {
    Section.MEMBERS: "project members",
    Section.ISSUES: "issues",
    Section.MERGE_REQUESTS: "merge requests",
}


def _team_activity(
    activity: TeamActivity, *, notes: IssueNotes, now: datetime, tz: tzinfo
) -> str:
    """Each member's open assigned issues and what moved on them, or why not.

    First line: the two windows. Then what could not be read or is old; the
    members in name order, each with their issues in milestone order and every
    mark that holds; Unassigned last; the members with no open issue; the
    unlinked-work line; `7e`'s closing lines; the footnotes. Nothing totals,
    rates or compares members (AD-25).
    """
    blocks: list[str] = [
        f"Open GitLab issues by assignee, in milestone order. Commits are "
        f"counted {_window_words(activity.window, now=now, tz=tz)}; merge "
        f"requests from {_local(activity.merge_request_since, tz=tz, now=now, dated=True)} "
        f"to {_local(now, tz=tz, now=now, dated=True)}."
    ]
    if activity.log_failure is not None:
        reason = _bounded(escape(activity.log_failure.reason)) or "the read gave no reason"
        blocks.append(
            _sentence(f"The event log could not be read, so no commit marks an issue: {reason}")
        )

    work = activity.work
    record = activity.record
    if isinstance(work, HarvestFailure):
        reason = _bounded(escape(work.reason)) or "the read gave no reason"
        blocks.append(
            _sentence(
                f"The project's GitLab record could not be read, so its members "
                f"and open issues are unknown: {reason}"
            )
        )
    elif record is None:
        blocks.append(
            "No GitLab issues are recorded for this project: no GitLab harvest "
            "has recorded its members, issues or merge requests."
        )
    else:
        members = record.status(Section.MEMBERS)
        issues = record.status(Section.ISSUES)
        sections_said: tuple[Section, ...] = tuple(Section)
        if not activity.listed:
            blocks.append(
                f"Neither the project members nor the issues could be read, so "
                f"no member list is shown — members: {_clause(_reason(members))}; "
                f"issues: {_clause(_reason(issues))}."
            )
            sections_said = (Section.MERGE_REQUESTS,)
        blocks.extend(
            line
            for section in sections_said
            for line in _section_state(record.status(section), now=now, tz=tz)
        )
        if activity.listed:
            listing = _member_list(activity, notes=notes, now=now, tz=tz)
            if listing:
                blocks.append(listing)
            elif issues.complete:
                blocks.append("No open issue is recorded for this project.")
            if activity.hidden_groups:
                more = activity.hidden_groups
                blocks.append(
                    f"{more} more {'member' if more == 1 else 'members'} with open "
                    f"issues {'is' if more == 1 else 'are'} not shown; "
                    f"their issues are in GitLab."
                )
            if activity.without_issues:
                names = ", ".join(_who(a) for a in activity.without_issues)
                more = activity.hidden_without_issues
                tail = f", and {more} more" if more else ""
                blocks.append(
                    _sentence(f"Project members with no open assigned issue: {names}{tail}")
                )

    blocks.append(_unlinked_line(activity))
    blocks.extend(notes.closing)
    blocks.extend(_activity_notes(activity))
    return "\n\n".join(blocks)


def _shown(text: str) -> str:
    """Folded, cut to `TEXT_LIMIT` characters, then escaped (`23g`)."""
    folded = fold_breaks(text).strip()
    if len(folded) > TEXT_LIMIT:
        folded = folded[: TEXT_LIMIT - 1] + "…"
    return escape(folded)


def _sentence(text: str) -> str:
    """`text` ending in exactly one full stop, whatever a reason ended with."""
    return _clause(text) + "."


def _clause(text: str) -> str:
    """`text` without the full stops a reason may end with, for mid-sentence use."""
    return text.rstrip().rstrip(".").rstrip()


def _reason(status: SectionStatus) -> str:
    return _bounded(escape(status.reason or "")) or "no reason was recorded"


def _section_state(status: SectionStatus, *, now: datetime, tz: tzinfo) -> list[str]:
    """What is unread or old about one section: at most two sentences."""
    label = _SECTION_LABELS[status.section]
    last = status.last_complete
    if last is not None and (last.tzinfo is None or last.utcoffset() is None):
        # A hand-edited instant with no timezone cannot be compared with the
        # render: read as never read completely, never a crash.
        last = None
    if last is None:
        return [_sentence(f"The {label} have never been read completely: {_reason(status)}")]
    lines: list[str] = []
    if not status.complete:
        lines.append(
            _sentence(
                f"The {label} could not be read completely in the last harvest: "
                f"{_reason(status)}"
            )
        )
    if now - last > STALE_AFTER:
        lines.append(
            f"The {label} are shown as last read completely at "
            f"{_local(last, tz=tz, now=now, dated=True)}."
        )
    return lines


def _who(assignee: Assignee) -> str:
    """A person as the page names them: display name, else username, else id."""
    person = assignee.person
    if _has_name(person.name):
        return _shown(person.name or "")
    if person.username:
        return f"`@{code(person.username)}`"
    if person.handle is not None:
        return f"`{code(str(person.handle))}`"
    return "an unnamed person"


def _has_name(name: str | None) -> bool:
    return bool(name and fold_breaks(name).strip())


def _member_list(
    activity: TeamActivity, *, notes: IssueNotes, now: datetime, tz: tzinfo
) -> str:
    lines: list[str] = []
    for group in activity.groups:
        who = group.assignee
        head = f"- **{_who(who)}**"
        if _has_name(who.person.name) and who.person.username:
            head += f" `@{code(who.person.username)}`"
        if not who.active and who.state is not None:
            head += f" ({_shown(who.state)})"
        if who.member is False:
            head += " (not a project member)"
        lines.append(head)
        for issue_row in group.issues:
            lines.extend(_issue_lines(issue_row, notes=notes, now=now, tz=tz))
        if group.hidden:
            more = group.hidden
            lines.append(
                f"  - {more} more open {'issue' if more == 1 else 'issues'} "
                f"assigned here {'is' if more == 1 else 'are'} not shown; "
                f"{'it is' if more == 1 else 'they are'} in GitLab."
            )
    hidden_marked = activity.hidden_unassigned_marked
    hidden_noted = activity.hidden_unassigned_noted
    quiet = activity.quiet_unassigned
    if activity.unassigned or hidden_marked or hidden_noted or quiet:
        lines.append("- **Unassigned**")
        for issue_row in activity.unassigned:
            lines.extend(_issue_lines(issue_row, notes=notes, now=now, tz=tz))
        if hidden_marked:
            lines.append(
                f"  - {hidden_marked} more marked unassigned "
                f"{'issue is' if hidden_marked == 1 else 'issues are'} not shown; "
                f"{'it is' if hidden_marked == 1 else 'they are'} in GitLab."
            )
        if hidden_noted:
            lines.append(
                f"  - {hidden_noted} more unassigned "
                f"{'issue' if hidden_noted == 1 else 'issues'} with notes "
                f"{'is' if hidden_noted == 1 else 'are'} not shown; "
                f"{'it is' if hidden_noted == 1 else 'they are'} in GitLab."
            )
    if quiet:
        extra = " more" if activity.unassigned else ""
        lines.append(
            f"  - {quiet}{extra} open unassigned "
            f"{'issue has' if quiet == 1 else 'issues have'} no mark and "
            f"{'is' if quiet == 1 else 'are'} not shown."
        )
    return "\n".join(lines)


def _issue_lines(
    issue_row: IssueRow, *, notes: IssueNotes, now: datetime, tz: tzinfo
) -> list[str]:
    """One issue, its marks, and `7e`'s rows for it — unescaped, as handed in."""
    issue = issue_row.issue
    line = f"  - `#{issue.iid}` {_shown(issue.title)}"
    if issue.milestone_title is not None:
        line += f" — milestone {_shown(issue.milestone_title)}"
        if issue.milestone_due is not None:
            line += f", due {issue.milestone_due.isoformat()}"
    elif issue.milestone_due is not None:
        line += f" — due {issue.milestone_due.isoformat()}"
    if issue.labels:
        line += " — labels: " + ", ".join(_shown(label) for label in issue.labels)
    lines = [line]
    mark = issue_row.commits
    if mark is not None:
        latest = mark.latest
        if latest.committed_at is not None:
            when = _local(latest.committed_at, tz=tz, now=now)
        else:
            when = f"{_local(latest.recorded_at, tz=tz, now=now)} {HARVESTED_MARKER}"
        sha = code(latest.sha[:8])
        named = f"`{sha}`" if sha else "(no commit id recorded)"
        text = (
            f"    - {mark.count} {'commit' if mark.count == 1 else 'commits'}; "
            f"latest {named} at {when}"
        )
        first = _shown(latest.first_line)
        if first:
            text += f": {first}"
        lines.append(text)
    for mr in issue_row.merge_requests:
        verb = "merged" if mr.merged else "updated"
        text = f"    - merge request `!{mr.iid}` {verb} {_local(mr.at, tz=tz, now=now)}"
        if mr.draft and not mr.merged:
            text += " (draft)"
        lines.append(text)
    lines.extend(f"    - {row}" for row in notes.rows.get(issue.iid, ()))
    return lines


def _unlinked_line(activity: TeamActivity) -> str:
    """One closing line counting work linked to no open issue, naming no author."""
    u = activity.unlinked
    mrs = u.merge_requests
    if mrs is not None:
        mr_words = (
            f"{mrs} marked merge {'request links' if mrs == 1 else 'requests link'} "
            f"no open issue"
        )
    elif isinstance(activity.work, HarvestFailure):
        mr_words = "merge requests unknown, the GitLab record could not be read"
    else:
        mr_words = "no merge requests are recorded"
    if u.unreferenced is None or u.closed_only is None:
        line = (
            f"Unlinked work in the window: commits unknown, the event log could "
            f"not be read; {mr_words}"
        )
    else:
        line = (
            f"Unlinked work in the window: {u.unreferenced} "
            f"{'commit references' if u.unreferenced == 1 else 'commits reference'} "
            f"no issue, {u.closed_only} "
            f"{'commit references' if u.closed_only == 1 else 'commits reference'} "
            f"only closed or unknown issues, and {mr_words}"
        )
    record = activity.record
    if record is not None and not record.status(Section.ISSUES).complete:
        line += " — checked against the issues as last read"
    return line + "."


def _activity_notes(activity: TeamActivity) -> list[str]:
    """What the section could not use, counted rather than dropped in silence."""
    out: list[str] = []
    commits = activity.commits
    if commits is not None:
        if commits.unplaceable:
            n = commits.unplaceable
            out.append(
                f"{n} commit log {_carry(n)} no readable `ingested_at`, so the "
                f"window could not place {_them(n)}."
            )
        if commits.ahead:
            n = commits.ahead
            out.append(
                f"{n} commit log {_carry(n)} an `ingested_at` later than this "
                f"render's instant, so the window excluded {_them(n)}."
            )
        if commits.undecodable:
            n = commits.undecodable
            out.append(
                f"{n} commit log {_carry(n)} fields that could not be decoded, so "
                f"{'it marks' if n == 1 else 'they mark'} nothing."
            )
        if commits.backfilled:
            n = commits.backfilled
            out.append(
                f"{n} {'commit' if n == 1 else 'commits'} recorded in the window "
                f"{'was' if n == 1 else 'were'} committed more than "
                f"{_days(LOOK_BACK_CAP)} earlier, so {'it marks' if n == 1 else 'they mark'} "
                f"nothing."
            )
    if activity.unknown_links:
        n = activity.unknown_links
        out.append(
            f"{n} marked merge {'request' if n == 1 else 'requests'} whose links "
            f"could not be read {'is' if n == 1 else 'are'} counted neither as "
            f"linked nor as unlinked."
        )
    if activity.unusable_merge_request_times:
        n = activity.unusable_merge_request_times
        out.append(
            f"{n} merge {'request carries' if n == 1 else 'requests carry'} a time "
            f"that cannot be used — absent, without a timezone, or more than five "
            f"minutes after this render — so {'it marks' if n == 1 else 'they mark'} nothing."
        )
    return out


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


LEADERSHIP_SENTENCE = (
    "No synthesis runs when this dashboard is rendered: it is a deterministic "
    "function with no model in its path."
)
"""Leadership Notes' statement (story 7d, amending `23a`).

It said nothing here "was written rather than computed", which stops being
true once a model-written excerpt is quoted under Time-Critical Activities.
What stays true is that no synthesis runs at render."""

QUOTED_SENTENCE = (
    "The meeting summaries under Time-Critical Activities are quoted from their "
    "meeting records, each naming the model that wrote it."
)
"""Added after `LEADERSHIP_SENTENCE` only when the page quotes at least one
summary excerpt (story 7d): a page that quotes none does not claim it does."""


def _quotes_a_summary(digests: MeetingDigests | None) -> bool:
    return digests is not None and any(
        isinstance(digest, MeetingDigest) and digest.quoted for digest in digests.shown
    )


def _leadership_notes(spend: SpendAnswer | None, *, quoted: bool = False) -> str:
    """No synthesis at render, and — personal page only — the month's frontier spend.

    Recorded as a deviation rather than papered over. CAP-9's "no empty section"
    clause is met in the sense that no section is blank, and knowingly not met
    in the sense CAP-9 intended. `quoted` adds that the summaries above are
    quoted from their records, when the page quotes one. The spend line
    (story 7d, AD-17) is computed from the disclosure ledger; `None` prints none.
    """
    text = f"{LEADERSHIP_SENTENCE} {QUOTED_SENTENCE}" if quoted else LEADERSHIP_SENTENCE
    if spend is None:
        return text
    return f"{text}\n\n{_spend_line(spend)}"


def _dollars(amount: float) -> str:
    return f"${amount:.2f}"


def _calls(n: int) -> str:
    return f"{n} {'call' if n == 1 else 'calls'}"


def _spend_line(spend: SpendAnswer) -> str:
    """AD-17's monthly total against the monitored target: a warning, never a cap.

    Calls whose lines report usage and have a cost are totalled; calls of
    unknown cost are counted, never as zero; malformed ledger lines are left
    out of the total and named after it, as `7c`'s reply does.
    """
    target = _dollars(MONTHLY_TARGET_USD)
    if isinstance(spend, HarvestFailure):
        reason = _bounded(escape(spend.reason)) or "the read gave no reason"
        return _sentence(
            f"This month's frontier spend could not be read from the disclosure "
            f"ledger: {reason}"
        )
    month = f"{spend.year}-{spend.month:02d}, UTC"
    read = " in the ledger lines that could be read" if spend.malformed else ""
    if spend.records == 0 and spend.unknown == 0:
        line = (
            f"Frontier spend this month ({month}): no frontier call is recorded{read}, "
            f"against the {target} monitored target."
        )
    elif spend.records == 0:
        line = (
            f"Frontier spend this month ({month}): {_calls(spend.unknown)} of unknown "
            f"cost and none of known cost{read}, against the {target} monitored target."
        )
    else:
        line = (
            f"Frontier spend this month ({month}): {_dollars(spend.cost_usd)} across "
            f"{_calls(spend.records)} of known cost{read}, against the {target} "
            f"monitored target"
        )
        if spend.unknown:
            line += f", and {_calls(spend.unknown)} of unknown cost"
        line += "."
    if spend.breached:
        line += (
            f" Warning: the month is past the {target} target. A target to watch, "
            f"not a cap: nothing is refused or downgraded."
        )
    elif spend.breached is None and spend.unknown:
        line += (
            f" With calls of unknown cost, whether the month is past the {target} "
            f"target cannot be told."
        )
    if spend.malformed:
        named = "; ".join(_clause(_bounded(escape(text))) for text in spend.malformed)
        line += (
            f" The disclosure ledger has lines that could not be read, left out of "
            f"the total: {named}."
        )
    return line


# ── Shared ───────────────────────────────────────────────────────────────────


def escape(text: str) -> str:
    r"""One escaper for every interpolated string, applied without exception.

    Goal titles and meeting titles are hand-authored or provider-supplied and
    reach the same Markdown; an actor name comes from an alias table seeded by
    hand. None of the three is trusted to be one line of inert text, and the
    failure is not cosmetic: a newline in a meeting title ends the bullet and
    everything after it reads as a new item, and a `|` would break any table a
    later section grows.

    **Line breaks are folded first** (`23f`, Andrei's decision of 2026-10-16):
    every run of line breaks, with the spaces, tabs and no-break spaces beside
    it, becomes one space (`fold_breaks`), so a multi-line excerpt or title
    reads as one line, the way a mail client previews it, and no literal `\n`
    reaches the page. It runs before escaping and before any cut.

    Two classes, escaped differently on purpose. A control character has no
    Markdown spelling, so it becomes a visible `\t` or `\xNN` — the value stays
    on its line and the reader can see what was in it. A structural character
    has one, so it is backslash-escaped and renders as itself.

    `_INVISIBLE` is the first class with a wider net than C0: the bidi
    overrides sit above the `char < " "` test and do a control character's
    damage, so they take a control character's visible spelling.
    """
    out: list[str] = []
    # Folded and stripped *before* escaping, not after: once a control character
    # has become its visible spelling no fold or strip can find it.
    for char in fold_breaks(text).strip():
        if char == "\\":
            out.append("\\\\")
        elif char == "\t":
            out.append("\\t")
        elif char in _STRUCTURAL:
            out.append(f"\\{char}")
        elif char < " " or char == "\x7f":
            # Everything else in C0 and DEL: invisible in a text file, and a
            # `\x01` in a title is the kind of thing that makes two renders of
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
    a run of line breaks is folded to one space exactly as in `escape` (`23f`),
    and any other control character becomes its visible spelling as there.

    Goal ids are charset-checked by `goal_register.GOAL_ID` and could not
    contain either. The register is a plain `dict` subclass that anything may
    construct, though, so this does not depend on the parser having built it.
    """
    out: list[str] = []
    for char in fold_breaks(text).strip():
        if char == "`":
            out.append("'")
        elif char == "\t":
            out.append("\\t")
        elif char < " " or char == "\x7f":
            out.append(f"\\x{ord(char):02x}")
        elif char in _INVISIBLE:
            # The same set as `escape`, for the same reason: a code span is
            # still one line of a Markdown file, and U+2028 ends it.
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return "".join(out)


_LINE_BREAKS = "\n\r\v\f\x85\u2028\u2029"
"""Every character the fold treats as a line break (`23f`): `\r\n` is two of them
in one run. A tab, any other control character and the bidi overrides are not
here and keep their visible spelling."""

_BESIDE_BREAKS = (
    "\t\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u202f\u205f\u3000"
)
"""What a break run absorbs on either side: a tab and every Unicode space
separator (category `Zs`) — the ordinary space, the no-break spaces (U+00A0,
U+2007, U+202F), the en to hair spaces (U+2000–U+200A), the Ogham space mark
(U+1680), the medium mathematical space (U+205F) and the ideographic space
(U+3000). A test pins the set against the Unicode tables."""

_BREAK_RUN = re.compile(f"[{_BESIDE_BREAKS}]*(?:[{_LINE_BREAKS}][{_BESIDE_BREAKS}]*)+")


def fold_breaks(text: str) -> str:
    """Every run of line breaks, with the blanks beside it, as one space (`23f`).

    `First\n\nSecond` and `a \t\n b` read `First Second` and `a b`. A tab
    not beside a break is left for the escaper to spell. Applied by `escape`
    and `code` to every interpolated value, so lines already in the log fold
    too; the stored text is never changed.
    """
    return _BREAK_RUN.sub(" ", text)


def cut_at_word(text: str, limit: int) -> str:
    """Folded, trimmed and cut to `limit` characters at a word, with `…` when cut.

    At the last space before the limit, a hard cut when there is none; the
    result with its `…` is at most `limit` characters. Escape after, never
    before (`23f`'s order: fold, cut, escape). Moved here from
    `member_updates` by story 7d, which cuts its excerpts with it, so the two
    stories share one cut.
    """
    folded = fold_breaks(text).strip()
    if len(folded) <= limit:
        return folded
    head = folded[: limit - 1]
    space = head.rfind(" ")
    if space > 0:
        head = head[:space]
    return head.rstrip() + "…"


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
in it. U+2028 and U+2029 are folded to a space before this set is consulted
(`fold_breaks`, `23f`); they stay listed so the set still says what it guards.

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
interpolation here can reach one, because every line break is folded to a space
before anything is placed (`23f`).
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
