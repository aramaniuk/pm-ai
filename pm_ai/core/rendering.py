"""`daily_dashboard.md`, rendered — CAP-9's four sections and nothing else.

CAP-9 asks for `~/.manager-ai/memory/daily_dashboard.md` by 07:00 with exactly
four headed sections — Time-Critical Activities, Proactive Enablement, 3-Tier
Strategic Milestones, Leadership Notes — and no empty section. Nothing turned
meetings, log entries and goals into that text; this module is that function.

**Pure, and structurally so.** `render_dashboard` takes its inputs and its
instant, reads no clock, opens no file and calls no model. `core` is I/O-free by
contract, and the injected clock is the rule story `1b` established: a renderer
that read `datetime.now()` would be the second clock in a codebase whose storage
service exists to have exactly one, and untestable at the only boundary that
matters. Every section is therefore golden-file testable.

## Nothing is invented

The rule that shapes every string below. A section with no data states the
*computed reason* — a file that is absent, a query that returned nothing, a
window that held nothing. "No meetings on your calendar today" names a query
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

## What this module does not do

No project render. `render_project_dashboard` is `23d`'s, a separate function
with its own sources and its own sections, so this one cannot be handed
project-scope data by mistake — a property of there being two functions rather
than of a scope check somebody remembers to write. No file write (`23b`), no
scheduling (`9a`), no scope-boundary logic (`23d`), no model call of any kind,
and no commitment data: nothing in this build produces commitments, and a
section implying otherwise would be the invented evidence the whole module is
arranged against.

It also does **not** decide which day it is rendering. The caller selects the
meetings — `23b` reads the calendar for the display day — and this function
renders what it is handed, which is what lets a meeting that started yesterday
and ends this morning appear without a date filter here silently dropping it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, tzinfo

from pm_ai.core.goal_register import ARTIFACT as GOALS_ARTIFACT
from pm_ai.core.goal_register import GoalRegister
from pm_ai.domain.event_entries import EventEntry, SelfActionType
from pm_ai.domain.events import ObservedEventType
from pm_ai.domain.goals import Goal, GoalDomain
from pm_ai.domain.harvest import HarvestFailure
from pm_ai.domain.meetings import Meeting

__all__ = [
    "GOALS_PATH",
    "HEADINGS",
    "LEADERSHIP_NOTES",
    "MESSAGE_WINDOW",
    "NO_MEETINGS",
    "PROACTIVE_ENABLEMENT",
    "STRATEGIC_MILESTONES",
    "TIME_CRITICAL",
    "code",
    "escape",
    "render_dashboard",
]


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

NO_MEETINGS = "No meetings on your calendar today"
"""The exact claim, kept as a constant because two rules bear on it.

It names a *query result*: the calendar was asked and answered with nothing. It
is therefore forbidden when the fetch failed (there is no result to report) and
forbidden when meetings came back and have all ended (the result was not
nothing).
"""

# The categories Proactive Enablement reads. One member today; a tuple because
# the section states its own vocabulary, and a widening should change the
# printed sentence by changing this.
SIGNAL_CATEGORIES: tuple[ObservedEventType, ...] = (ObservedEventType.MESSAGE_POSTED,)

# A statement about this build, not about the data — the one line in this module
# that is neither computed nor permanent. Nothing in wave 1 writes a
# `message_posted` entry: no connector emits one, so the window is empty for a
# reason the window itself cannot show. `core` may not import `pm_ai.connectors`
# (AD-1's layering), so it cannot be derived here; it is stated instead, and
# story `33d` — which supplies the signals — deletes it.
#
# Printed only when the log holds no `message_posted` entry at all, which *is*
# computed. Saying "nothing writes these" beside a log containing three of them
# that merely fell outside the window would be this module's own rule broken in
# the other direction, and it is exactly what `33d` will produce first.
NO_PRODUCER_YET = (
    "No connector in this build writes `message_posted` entries, so this window "
    "is empty for that reason and not because the day was quiet (story `33d`)."
)


def render_dashboard(
    meetings: Sequence[Meeting] | HarvestFailure,
    entries: Sequence[EventEntry],
    goals: GoalRegister,
    now: datetime,
    *,
    tz: tzinfo | None,
) -> str:
    """CAP-9's four sections, as Markdown, from these inputs and this instant.

    `meetings` is the calendar's *answer*, not a list: a `HarvestFailure` says
    the fetch could not be completed and carries why, and a sequence says it
    could. Collapsing the two into an empty list is precisely the confusion the
    honesty rule forbids, so the type refuses to do it.

    `entries` is the scope's event log, **unbounded**. The window is applied
    here rather than by `EventLog.read`, because a bounded read drops an entry
    with no `ingested_at` silently and this section counts them: an entry that
    cannot be placed in time is a fact about the log, not an absence.

    `goals` carries `present`, which is the whole reason it is a `GoalRegister`
    and not a `dict`. An absent file and a file with nothing in it need
    different sentences, and telling a PM to author a file they have already
    authored is how a dashboard loses their trust.

    `now` must be aware UTC and `tz` must be supplied. Rendering the same inputs
    twice returns byte-identical output: nothing here reads a clock and every
    ordering is total.
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
    _assert_utc(now, name="now")

    sections = (
        _time_critical(meetings, now=now, tz=tz),
        _proactive_enablement(entries, now=now, tz=tz),
        _strategic_milestones(goals),
        _leadership_notes(),
    )
    blocks = [
        f"## {heading}\n\n{body}"
        for heading, body in zip(HEADINGS, sections, strict=True)
    ]
    # A blank line before every heading, because a `##` on the line after a list
    # item is not a heading to a Markdown parser — it is more list item, and the
    # file's four-section shape would exist only for a reader squinting at the
    # source. One trailing newline, so the file ends on a line.
    return "\n\n".join(blocks) + "\n"


# ── Time-Critical Activities ─────────────────────────────────────────────────


def _time_critical(
    meetings: Sequence[Meeting] | HarvestFailure, *, now: datetime, tz: tzinfo
) -> str:
    """Today's schedule, or the reason there is none to show."""
    if isinstance(meetings, HarvestFailure):
        # Never `NO_MEETINGS`. The fetch produced no answer, so there is no
        # result to report — and a dashboard that reported yesterday's silence
        # as today's empty calendar is the failure this branch exists for.
        return (
            f"The calendar could not be read, so today's meetings are unknown: "
            f"{escape(meetings.reason)}\n\n"
            f"{_retry_advice(meetings)}"
        )

    # Before the sort, not after: comparing a naive start against an aware one
    # raises `TypeError` from inside `sorted`, which surfaces as the render
    # failing rather than as the caller being told what it handed over.
    for meeting in meetings:
        _assert_utc(meeting.start, name=f"meeting {meeting.meeting_id!r} start")
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
            f"The provider asked for a wait of {failure.retry_after}; the next "
            f"harvest should clear it."
        )
    return "The connector reports this as retryable; the next harvest may clear it."


# ── Proactive Enablement ─────────────────────────────────────────────────────


def _proactive_enablement(
    entries: Sequence[EventEntry], *, now: datetime, tz: tzinfo
) -> str:
    """Message signals inside the window, or the reason the window is empty."""
    since = now - MESSAGE_WINDOW
    inside: list[tuple[datetime, EventEntry]] = []
    unplaceable = 0
    seen = 0
    for entry in entries:
        if entry.category in SIGNAL_CATEGORIES:
            seen += 1
        else:
            # `SelfActionType` entries share this log and are not signals: their
            # subject is pm-ai, and a dashboard reporting pm-ai's own compaction
            # back to the PM as something to act on is noise wearing evidence's
            # clothes.
            continue
        at = _ingested_at(entry)
        if at is None:
            # Excluded from the window and counted, never dated from `now`. An
            # entry written before story `2e` stamped the field cannot answer
            # the question the window asks, and a substituted timestamp is
            # indistinguishable from a real one (AD-35).
            unplaceable += 1
            continue
        if since <= at <= now:
            inside.append((at, entry))

    footnote = _unplaceable_note(unplaceable)
    if not inside:
        # The build note only when the log holds no `message_posted` entry at
        # all. Printing "no connector writes these" beside a log that plainly
        # contains two of them, merely outside the window, would be the same
        # invented claim in the other direction.
        note = f"\n\n{NO_PRODUCER_YET}" if seen == 0 else ""
        return f"{_no_signals(since=since, now=now, tz=tz)}{note}{footnote}"

    inside.sort(key=lambda pair: (pair[0], pair[1].entry_id or ""))
    lines = "\n".join(
        _signal_line(at, entry, tz=tz, now=now) for at, entry in inside
    )
    return f"{lines}{footnote}"


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


def _signal_line(at: datetime, entry: EventEntry, *, tz: tzinfo, now: datetime) -> str:
    fields = dict(entry.fields)
    channel = fields.get("channel")
    excerpt = fields.get("excerpt")
    parts = [f"- **{_local(at, tz=tz, now=now)}**", escape(entry.actor)]
    if channel:
        parts.append(f"in {escape(channel)}")
    line = " ".join(parts)
    if excerpt:
        line += f" — {escape(excerpt)}"
    return line


def _unplaceable_note(count: int) -> str:
    """What the window could not judge, said rather than swallowed.

    A bounded `EventLog.read` drops these silently — correctly, since the range
    asks a question they cannot answer — so this is the only place a PM can
    learn that the count above is short, and by how many.
    """
    if not count:
        return ""
    subject = "entry carries" if count == 1 else "entries carry"
    them = "it" if count == 1 else "them"
    return (
        f"\n\n{count} message log {subject} no `ingested_at`, so the window "
        f"could not place {them}. Counted here rather than dated from the "
        f"local clock."
    )


def _ingested_at(entry: EventEntry) -> datetime | None:
    """The write clock the window is measured on, or `None` when unplaceable.

    An unparseable value is the same absence as a missing one for this purpose —
    the entry cannot be placed in the window either way — and guessing at it is
    the backfill AD-35 forbids.
    """
    raw = dict(entry.fields).get("ingested_at")
    if raw is None:
        return None
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if at.tzinfo is None or at.utcoffset() != timedelta(0):
        return None
    return at


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
        else:
            out.append(char)
    return "".join(out)


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
