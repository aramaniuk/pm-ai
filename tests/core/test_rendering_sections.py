"""`daily_dashboard.md`'s four sections — one test per row of the story's matrix.

Spec: `_bmad-output/specs/spec-pm-ai/stories/23a-dashboard-sections.md`.

Every test calls `render_dashboard` with values and an instant. There is no
filesystem in any signature and no clock read anywhere: that is the property the
story asks for, and a test here that needed `tmp_path` or a monkeypatched
`datetime` would be evidence the renderer had grown one.

The display zone is a fixed `+02:00` rather than a named zone. `render_dashboard`
takes a `tzinfo`, so nothing here needs the IANA database — which is an optional
runtime extra (`tzdata`) precisely so a slim environment cannot turn a test into
a skip that reads as coverage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pm_ai.core.goal_register import ARTIFACT as GOALS_ARTIFACT
from pm_ai.core.goal_register import GoalRegister, parse_goals
from pm_ai.core.rendering import (
    HEADINGS,
    MAX_SIGNAL_LINES,
    MENTIONS_YOU,
    MESSAGE_WINDOW,
    NO_MEETINGS,
    UNKNOWN_SENDER,
    PmHandles,
    code,
    escape,
    render_dashboard,
)
from pm_ai.domain.event_entries import EventEntry, SelfActionType
from pm_ai.domain.events import MessagePayload, ObservedEventType
from pm_ai.domain.goals import GoalDomain
from pm_ai.domain.handles import GRAPH, person_handle
from pm_ai.domain.harvest import (
    HarvestFailure,
    NoCalendarConnector,
    PartialCalendar,
    UnreadCalendar,
)
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.payload_fields import encode_payload

PERSONAL = DataScope(ScopeKind.PERSONAL)

DISPLAY = timezone(timedelta(hours=2))
"""The PM's display zone, as `23b` will hand it over from `display_timezone`."""

NOW = datetime(2026, 9, 9, 10, 30, tzinfo=timezone.utc)
"""12:30 in the display zone — mid-morning, so a day can have both a meeting
that has ended and one that has not. The "all ended" row needs a later instant
and states its own.
"""

GOLDEN = Path(__file__).parent / "goldens" / "dashboard_full_day.md"

NOBODY = PmHandles(handles=frozenset(), unrecorded=(), unchecked=())
"""No Graph row at all: nothing is marked and no sign-in line is printed."""

PM_ID = "0d9e3c4f-1a2b-4c3d-8e9f-a0b1c2d3e4f5"
"""The PM's Graph user id on the first tenant, as `8q` saves it at sign-in."""

PM_ID_2 = "11111111-2222-4333-8444-555555555555"
"""The PM's user id on a second tenant."""

OTHER_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
"""Somebody else."""

PM = PmHandles(
    handles=frozenset({person_handle(GRAPH, PM_ID)}), unrecorded=(), unchecked=()
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def meeting(
    meeting_id: str,
    title: str,
    *,
    start: datetime,
    minutes: int = 30,
    attendees: int = 2,
    tentative: bool = False,
) -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title=title,
        start=start,
        duration_minutes=minutes,
        attendees=tuple(Actor(f"u_{n}") for n in range(attendees)),
        scope=PERSONAL,
        tentative=tentative,
    )


def message(
    *,
    at: datetime | str | None,
    actor: str = "u_dana",
    channel: str = "#payments",
    excerpt: str | None = None,
    entry_id: str = "evt_1",
    mentions: tuple[str, ...] | None = None,
) -> EventEntry:
    """A `message_posted` ledger entry, shaped as `_append_batch` writes one —
    its payload fields built by `encode_payload`, the writer's own encoder.

    `at=None` is the pre-`2e` entry the matrix names: the field is absent, not
    empty, because that is what an entry written before storage stamped the
    clock actually looks like.

    A `str` is written to the field verbatim, which is how a value that is
    *there and unreadable* is expressed — the ledger is text, so the renderer
    reads back whatever was on the line, not a `datetime` somebody validated.
    """
    fields: list[tuple[str, str]] = []
    if at is not None:
        fields.append(("ingested_at", at if isinstance(at, str) else at.isoformat()))
    fields.append(("src", "slack:msg/1"))
    # Through the encoder, as the writer does: entries built with hand-spelled
    # names passed here while every real message would have rendered empty.
    fields.extend(
        encode_payload(
            MessagePayload(channel=channel, excerpt=excerpt, mentions=mentions)
        )
    )
    return EventEntry(
        category=ObservedEventType.MESSAGE_POSTED,
        actor=actor,
        fields=tuple(fields),
        entry_id=entry_id,
    )


ALL_HORIZONS = b"""# Strategic Goals

## Project

- [g_payments_latency] (short) Cut payment latency below 200ms

## Team

- [g_oncall_load] (medium) Halve the on-call pages per week

## Personal

- [g_staff_eng] (long) Reach staff engineer
"""


def goals(raw: bytes | None = ALL_HORIZONS) -> GoalRegister:
    return parse_goals(raw, scope=PERSONAL)


def render(
    *,
    meetings=(),
    entries=(),
    register: GoalRegister | None = None,
    now: datetime = NOW,
    tz=DISPLAY,
    pm: PmHandles = NOBODY,
) -> str:
    return render_dashboard(
        meetings,
        entries,
        goals() if register is None else register,
        now,
        tz=tz,
        pm=pm,
    )


def sections(text: str) -> dict[str, str]:
    """The rendered file, split back into the four bodies it claims to have."""
    found: dict[str, str] = {}
    heading: str | None = None
    body: list[str] = []
    for line in text.split("\n"):
        if line.startswith("## "):
            if heading is not None:
                found[heading] = "\n".join(body).strip()
            heading, body = line[3:], []
            continue
        body.append(line)
    if heading is not None:
        found[heading] = "\n".join(body).strip()
    return found


# ── Matrix: the full day ─────────────────────────────────────────────────────


FULL_DAY_MEETINGS = (
    # Deliberately out of order, and two share a start: the second key is what
    # makes the rendering of this fixture total rather than fetch-order-dependent.
    meeting("m_standup", "Standup", start=datetime(2026, 9, 9, 7, 0, tzinfo=timezone.utc)),
    meeting(
        "m_payments",
        "Payments Gateway Sync",
        start=datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc),
        minutes=60,
        attendees=5,
        tentative=True,
    ),
    meeting(
        "m_1on1",
        "1:1 with Dana",
        start=datetime(2026, 9, 9, 7, 0, tzinfo=timezone.utc),
        minutes=45,
        attendees=1,
    ),
)

FULL_DAY_ENTRIES = (
    message(
        at=datetime(2026, 9, 9, 6, 15, tzinfo=timezone.utc),
        actor="u_dana",
        excerpt="Cache TTL is still 60s in staging",
        entry_id="evt_a",
    ),
    message(
        at=datetime(2026, 9, 9, 9, 40, tzinfo=timezone.utc),
        actor="u_sam",
        channel="#oncall",
        entry_id="evt_b",
    ),
)


def full_day() -> str:
    return render(meetings=FULL_DAY_MEETINGS, entries=FULL_DAY_ENTRIES)


def test_full_day_populates_all_four_sections():
    """Matrix row 1 — three meetings, message entries, goals at all horizons."""
    body = sections(full_day())
    assert list(body) == list(HEADINGS)
    assert all(body[heading] for heading in HEADINGS)
    assert "Payments Gateway Sync" in body["Time-Critical Activities"]
    assert "u_dana" in body["Proactive Enablement"]
    assert "Reach staff engineer" in body["3-Tier Strategic Milestones"]


def test_full_day_orders_meetings_by_start_then_id():
    """The tie on `start` is broken by `meeting_id`, so paging cannot reorder."""
    # Scoped to the section under test: a signal line carrying an excerpt is also
    # `- **…** … — …`, so filtering the whole document passes only while meetings
    # happen to be rendered first.
    listed = [
        line
        for line in sections(full_day())["Time-Critical Activities"].split("\n")
        if line.startswith("- **")
    ]
    assert [line.split("**")[2].split("—")[0].strip() for line in listed] == [
        "1:1 with Dana",  # m_1on1 and m_standup share 09:00; the id decides
        "Standup",
        "Payments Gateway Sync",
    ]


def test_full_day_matches_the_golden_file():
    """The whole artifact, byte for byte.

    Regenerate deliberately, never reflexively: this file is the only place the
    four sections are reviewed as a *document* rather than as assertions about
    fragments of one, and a diff here is a change in what the PM reads at 07:00.
    """
    assert full_day() == GOLDEN.read_text(encoding="utf-8")


def test_the_full_day_render_is_byte_identical_twice():
    """Acceptance — no clock read, no ordering nondeterminism."""
    assert full_day() == full_day()


# ── Matrix: Time-Critical Activities ─────────────────────────────────────────


def test_no_meetings_names_the_query_that_answered():
    """Matrix row 2 — the calendar answered, with nothing in the day."""
    body = sections(render(meetings=()))["Time-Critical Activities"]
    assert NO_MEETINGS in body
    assert "answered" in body


def test_an_unreachable_calendar_never_claims_an_empty_day():
    """Matrix row 3 — the fetch failed, so there is no query result to report."""
    failure = HarvestFailure(reason="the tenant refused the token", retryable=False)
    body = sections(render(meetings=failure))["Time-Critical Activities"]
    assert NO_MEETINGS not in body
    assert "could not be read" in body
    assert "the tenant refused the token" in body
    assert "will not clear on its own" in body


def test_no_enrolled_calendar_is_its_own_answer_and_reports_no_connector():
    """`23b`'s third state — and the whole reason it is not a `HarvestFailure`.

    Asserted on the rendered *string* rather than on a branch being taken,
    because the point of the value is that the page makes no claim about a
    connector. `_retry_advice`'s unretryable sentence is the one that would be
    printed if this reused `HarvestFailure`, and it names a report by a
    connector that was never enrolled.
    """
    body = sections(render(meetings=NoCalendarConnector()))["Time-Critical Activities"]
    assert NO_MEETINGS not in body
    assert "No calendar is enrolled" in body
    assert "the connector reports" not in body.casefold()
    # Nor the other two advice sentences, which are the same defect in the
    # retryable direction: there is no next harvest to clear anything.
    assert "next harvest" not in body
    assert "the provider asked for a wait" not in body.casefold()


def test_no_enrolled_calendar_names_no_remedy():
    """No calendar connector is enrolled, so the section names no command.

    The dashboard reports what it read and nothing more: with no calendar
    connector to read from, it does not invent a remedy — a command printed
    here would be a claim the render did not compute. (Written when `pm-ai
    connector add graph` still refused; `8l` made it work and `8o` emptied
    `probe.PROBES`, and the rule about not printing a remedy stands.)
    """
    body = sections(render(meetings=NoCalendarConnector()))["Time-Critical Activities"]
    assert "connector add" not in body
    assert "pm-ai " not in body


def test_a_partly_read_day_lists_what_arrived_and_names_what_did_not():
    """`23b`'s fourth state — one calendar answered and another went dark.

    Both halves, because the rule that settled it cuts both ways: data that is
    present is not discarded because data elsewhere is missing, and a partial
    day is never presented as a whole one.
    """
    body = sections(
        render(
            meetings=PartialCalendar(
                meetings=(
                    meeting(
                        "m_payments",
                        "Payments Gateway Sync",
                        start=datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc),
                    ),
                ),
                unread=(
                    UnreadCalendar(
                        instance="graph:contoso",
                        failure=HarvestFailure(
                            reason="graph:contoso could not finish the span",
                            retryable=True,
                        ),
                    ),
                ),
            )
        )
    )["Time-Critical Activities"]
    assert "Payments Gateway Sync" in body
    assert "graph:contoso" in body
    assert "could not finish the span" in body
    assert "One calendar could not be read" in body
    # Never the whole-day claim: some of this day was never read.
    assert NO_MEETINGS not in body


def test_a_partial_day_whose_answering_calendars_held_nothing_says_which():
    """The empty half of a partial day is still not `NO_MEETINGS`.

    `NO_MEETINGS` names a query result over *the* calendar, and here one
    calendar was never asked successfully — so the claim would be about a day
    nobody finished reading.
    """
    body = sections(
        render(
            meetings=PartialCalendar(
                meetings=(),
                unread=(
                    UnreadCalendar(
                        instance="graph:fabrikam",
                        failure=HarvestFailure(reason="429", retryable=True),
                    ),
                ),
            )
        )
    )["Time-Critical Activities"]
    assert NO_MEETINGS not in body
    assert "The calendars that answered held nothing" in body
    assert "graph:fabrikam" in body


def test_two_unread_calendars_are_both_named_and_counted():
    body = sections(
        render(
            meetings=PartialCalendar(
                meetings=(
                    meeting(
                        "m_1",
                        "Standup",
                        start=datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc),
                    ),
                ),
                unread=(
                    UnreadCalendar(
                        instance="graph:contoso",
                        failure=HarvestFailure(reason="token expired", retryable=False),
                    ),
                    UnreadCalendar(
                        instance="graph:fabrikam",
                        failure=HarvestFailure(reason="no route", retryable=True),
                    ),
                ),
            )
        )
    )["Time-Critical Activities"]
    assert "2 calendars could not be read" in body
    assert "graph:contoso" in body
    assert "graph:fabrikam" in body


def test_an_unread_calendar_with_a_blank_reason_still_reads_as_a_sentence():
    """The `NO_REASON_GIVEN` rule, applied to the partial branch too."""
    body = sections(
        render(
            meetings=PartialCalendar(
                meetings=(),
                unread=(
                    UnreadCalendar(
                        instance="graph:contoso",
                        failure=HarvestFailure(reason="   ", retryable=False),
                    ),
                ),
            )
        )
    )["Time-Critical Activities"]
    assert "the connector gave no reason" in body
    assert not body.rstrip().endswith("—")


def test_a_partial_day_renders_byte_identically_twice():
    answer = PartialCalendar(
        meetings=FULL_DAY_MEETINGS,
        unread=(
            UnreadCalendar(
                instance="graph:contoso",
                failure=HarvestFailure(reason="429", retryable=True),
            ),
        ),
    )
    assert render(meetings=answer) == render(meetings=answer)


def test_a_partial_calendar_with_nothing_unread_is_refused():
    """The value refuses to be a whole day wearing the partial name."""
    with pytest.raises(ValueError, match="nothing unread"):
        PartialCalendar(meetings=(), unread=())


def test_a_retryable_failure_says_the_next_harvest_may_clear_it():
    """`HarvestFailure.retryable` decides what the operator is told to do."""
    body = sections(
        render(
            meetings=HarvestFailure(
                reason="429 from Graph", retryable=True, retry_after=timedelta(seconds=30)
            )
        )
    )["Time-Critical Activities"]
    # The provider's own hint, in words. `str(timedelta)` renders `0:00:30`,
    # which is a Python repr in the file a PM reads at 07:00.
    assert "a wait of 30 seconds" in body
    assert "0:00:30" not in body
    assert NO_MEETINGS not in body


@pytest.mark.parametrize(
    ("wait", "words"),
    [
        (timedelta(seconds=30), "30 seconds"),
        (timedelta(seconds=1), "1 second"),
        (timedelta(seconds=90), "1 minute 30 seconds"),
        (timedelta(hours=2), "2 hours"),
        (timedelta(days=1, seconds=5), "1 day 5 seconds"),
        (timedelta(0), "no wait at all"),
    ],
    ids=["seconds", "singular", "mixed", "hours", "days", "zero"],
)
def test_the_providers_wait_hint_is_spelled_out(wait, words):
    """Each unit, and the singular — a repr leaks in whichever arm is untested."""
    body = sections(
        render(meetings=HarvestFailure(reason="429", retryable=True, retry_after=wait))
    )["Time-Critical Activities"]
    assert f"a wait of {words};" in body


@pytest.mark.parametrize(
    ("failure", "expected", "forbidden"),
    [
        (
            HarvestFailure(reason="the tenant refused the token", retryable=False),
            "will not clear on its own",
            "may clear it",
        ),
        (
            HarvestFailure(reason="the laptop has no route", retryable=True),
            "the next harvest may clear it",
            "will not clear on its own",
        ),
    ],
    ids=["permanent", "retryable-with-no-hint"],
)
def test_retryable_decides_the_advice_in_both_directions(failure, expected, forbidden):
    """Both arms, because one asserted arm lets the sentences be swapped.

    A dead credential and a throttled minute are the same absent calendar and a
    different thing to do about it, so telling the operator to wait for one that
    will never clear is the whole failure `retryable` exists to prevent.
    """
    body = sections(render(meetings=failure))["Time-Critical Activities"]
    assert expected in body
    assert forbidden not in body


def test_a_blank_failure_reason_does_not_leave_a_dangling_colon():
    """`reason` has no default but nothing stops an empty one, and `escape` strips."""
    body = sections(
        render(meetings=HarvestFailure(reason="   ", retryable=False))
    )["Time-Critical Activities"]
    assert "unknown: the connector gave no reason" in body
    assert not any(line.rstrip().endswith(":") for line in body.split("\n"))


def test_a_day_whose_meetings_have_all_ended_says_so():
    """Matrix row 4 — and never `NO_MEETINGS`, which is false by mid-afternoon."""
    body = sections(
        render(
            meetings=FULL_DAY_MEETINGS,
            now=datetime(2026, 9, 9, 16, 0, tzinfo=timezone.utc),
        )
    )["Time-Critical Activities"]
    assert "All 3 of today's meetings have ended" in body
    assert NO_MEETINGS not in body
    # The latest *end*, not the latest start — the 09:00 meeting runs 45 minutes
    # and the 13:00 one runs an hour.
    assert "2026-09-09 14:00" in body


def test_a_single_ended_meeting_is_reported_grammatically():
    """One meeting is still "not an empty day", said in a sentence that reads."""
    body = sections(
        render(
            meetings=(
                meeting(
                    "m_only", "Standup", start=datetime(2026, 9, 9, 7, 0, tzinfo=timezone.utc)
                ),
            ),
            now=datetime(2026, 9, 9, 16, 0, tzinfo=timezone.utc),
        )
    )["Time-Critical Activities"]
    assert "Today's one meeting has ended" in body
    assert NO_MEETINGS not in body


def test_a_meeting_in_progress_is_time_critical():
    """Matrix row 5 — started before `now`, not yet ended."""
    body = sections(
        render(
            meetings=(
                meeting(
                    "m_live",
                    "Incident bridge",
                    start=datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc),
                    minutes=90,
                ),
            )
        )
    )["Time-Critical Activities"]
    assert "Incident bridge — in progress" in body


def test_a_meeting_spanning_midnight_is_listed_and_carries_its_date():
    """Matrix row 6 — started yesterday in the display zone, ends today.

    The date is what makes it readable: a bare `23:00` on a dashboard headed
    "today" is a lie by omission about which day the meeting began.
    """
    body = sections(
        render(
            meetings=(
                meeting(
                    "m_night",
                    "Cutover window",
                    start=datetime(2026, 9, 8, 21, 30, tzinfo=timezone.utc),  # 23:30 local
                    minutes=240,
                ),
            ),
            now=datetime(2026, 9, 9, 0, 30, tzinfo=timezone.utc),
        )
    )["Time-Critical Activities"]
    assert "Cutover window" in body
    assert "2026-09-08 23:30" in body


def test_a_naive_meeting_start_is_refused_rather_than_sorted():
    """Not a matrix row — the failure the matrix's rows would hit as `TypeError`.

    Refused with a message naming the meeting, following `clocks`' precedent,
    because a naive start compared against an aware one raises from inside
    `sorted` and surfaces as the render breaking rather than the caller being
    told what it handed over.
    """
    naive = meeting("m_naive", "Standup", start=datetime(2026, 9, 9, 7, 0))
    with pytest.raises(ValueError, match="m_naive"):
        render(meetings=(naive,))


def test_a_negative_duration_is_refused_rather_than_reported_as_ended():
    """`ended` is computed from `start + duration`, so a negative one lies.

    And it does not lie about one line: a single meeting that ends before it
    starts satisfies `all(...)`, so an upcoming meeting takes the whole section
    with it into "all of today's meetings have ended".
    """
    backwards = meeting(
        "m_backwards",
        "Standup",
        start=datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc),  # hours from now
        minutes=-60,
    )
    with pytest.raises(ValueError, match="m_backwards"):
        render(meetings=(backwards,))


def test_a_meeting_ending_exactly_at_now_has_ended():
    """The `<=` in `_ended`, which its own comment justifies and nothing pinned."""
    body = sections(
        render(
            meetings=(
                meeting(
                    "m_just_over",
                    "Standup",
                    start=NOW - timedelta(minutes=30),
                    minutes=30,  # ends at exactly `now`
                ),
                meeting("m_later", "Retro", start=NOW + timedelta(hours=1)),
            )
        )
    )["Time-Critical Activities"]
    assert "Standup — ended" in body
    assert "Retro — upcoming" in body


def test_a_zero_duration_meeting_is_placed_by_its_start():
    """A calendar hold with no length still has a state, and it is `start`'s."""
    body = sections(
        render(
            meetings=(
                meeting("m_hold_now", "Hold", start=NOW, minutes=0),
                meeting("m_hold_later", "Later hold", start=NOW + timedelta(hours=1), minutes=0),
            )
        )
    )["Time-Critical Activities"]
    # Zero length at exactly `now`: `_ended`'s `<=` is what decides this one.
    assert "Hold — ended, 0 min" in body
    assert "Later hold — upcoming, 0 min" in body


def test_an_untitled_meeting_says_so_rather_than_rendering_a_gap():
    """A provider may return an empty subject; the bullet still has to read."""
    body = sections(
        render(
            meetings=(meeting("m_blank", "   ", start=NOW + timedelta(hours=1)),)
        )
    )["Time-Critical Activities"]
    assert "(untitled) — upcoming" in body


# ── Matrix: Proactive Enablement ─────────────────────────────────────────────


def test_no_message_entries_names_the_window():
    """Matrix row 11 — an empty log states the window it searched."""
    body = sections(render(entries=()))["Proactive Enablement"]
    assert "event log between" in body
    assert "2026-09-08 12:30" in body  # NOW - MESSAGE_WINDOW, in the display zone
    assert "2026-09-09 12:30" in body
    # The two stamps are the window, and they are 24h apart — asserting
    # MESSAGE_WINDOW against its own literal here would restate the constant
    # rather than check that the render names it.


def test_an_empty_section_no_longer_says_nothing_writes_the_category():
    """Story 33d — the Graph connector writes `message_posted`, so the build
    note is deleted: a render with zero message entries states the window it
    searched and nothing about the build."""
    body = sections(render(entries=()))["Proactive Enablement"]
    assert "No connector in this build writes" not in body
    assert "33d" not in body
    assert "build" not in body  # matrix "Empty window": no sentence about the build
    assert "event log between" in body


def test_an_entry_with_no_ingested_at_is_counted_not_dated_from_now():
    """Matrix row 16 — a pre-`2e` entry, excluded from the window and reported."""
    body = sections(render(entries=(message(at=None),)))["Proactive Enablement"]
    assert "1 message log entry carries no `ingested_at`" in body
    assert "u_dana" not in body  # it was not placed, so it is not listed


def test_two_entries_with_no_ingested_at_are_counted_in_the_plural():
    """The other arm of the sentence, which nothing exercised."""
    body = sections(
        render(
            entries=(
                message(at=None, entry_id="evt_a"),
                message(at=None, entry_id="evt_b"),
            )
        )
    )["Proactive Enablement"]
    assert "2 message log entries carry no `ingested_at`" in body
    assert "could not place them" in body


def test_an_aware_offset_ingested_at_is_converted_and_placed():
    """`+02:00` is a real instant. 08:30Z is inside a window ending at 10:30Z.

    It was refused and then reported as "carries no `ingested_at`" — a signal
    dropped, and a printed reason the code had not computed, in the module whose
    rule is that a claim it did not compute may not appear in the output.
    """
    offset = message(
        at="2026-09-09T10:30:00+02:00",  # 08:30 UTC, two hours before `now`
        actor="u_offset",
        entry_id="evt_offset",
    )
    body = sections(render(entries=(offset,)))["Proactive Enablement"]
    assert "u_offset" in body
    assert "**10:30**" in body  # 08:30Z, rendered in the +02:00 display zone
    assert "ingested_at" not in body


def test_an_unreadable_ingested_at_is_not_reported_as_a_missing_one():
    """Two different facts, two different sentences — as the calendar branch does."""
    body = sections(
        render(entries=(message(at="last tuesday", actor="u_junk"),))
    )["Proactive Enablement"]
    assert "1 message log entry carries an `ingested_at` that could not be read" in body
    assert "carries no `ingested_at`" not in body
    assert "u_junk" not in body  # it was not placed, so it is not listed


def test_a_naive_ingested_at_is_unplaceable_and_does_not_break_the_render():
    """The tzinfo guard, pinned: without it the comparison raises `TypeError`.

    And not out of this section — out of `render_dashboard`, so one malformed
    ledger line takes the meetings, the goals and the whole file down with it.
    """
    body = sections(
        render(entries=(message(at="2026-09-09T10:00:00", actor="u_naive"),))
    )["Proactive Enablement"]
    assert "could not be read as an instant" in body
    assert "u_naive" not in body


def test_an_entry_stamped_after_now_is_counted_rather_than_dropped():
    """Writer or provider clock skew — the one exclusion made in silence."""
    body = sections(
        render(
            entries=(message(at=NOW + timedelta(minutes=5), actor="u_ahead"),)
        )
    )["Proactive Enablement"]
    assert "1 message log entry carries an `ingested_at` later than this render" in body
    assert "u_ahead" not in body


def test_the_window_includes_both_of_its_endpoints():
    """`since <= at <= now` — an entry on either bound is inside it."""
    body = sections(
        render(
            entries=(
                message(at=NOW - MESSAGE_WINDOW, actor="u_oldest", entry_id="evt_a"),
                message(at=NOW, actor="u_newest", entry_id="evt_b"),
            )
        )
    )["Proactive Enablement"]
    assert "u_oldest" in body
    assert "u_newest" in body
    assert "message log" not in body  # nothing was excluded, so nothing is noted


def test_two_signals_at_one_instant_are_ordered_by_entry_id():
    """The Proactive tie-break, mirroring the meetings one.

    Byte-identical re-render is an acceptance criterion, and two entries sharing
    an `ingested_at` is exactly where a sort on the instant alone falls back on
    whatever order the log happened to be read in.
    """
    at = NOW - timedelta(minutes=5)
    first = message(at=at, actor="u_alpha", entry_id="evt_a")
    second = message(at=at, actor="u_beta", entry_id="evt_b")
    one = render(entries=(first, second))
    other = render(entries=(second, first))
    assert one == other
    assert one.index("u_alpha") < one.index("u_beta")


def test_a_message_outside_the_window_is_not_a_signal():
    """The window is a bound, not a decoration."""
    stale = message(at=NOW - MESSAGE_WINDOW - timedelta(minutes=1), actor="u_old")
    body = sections(render(entries=(stale,)))["Proactive Enablement"]
    assert "u_old" not in body
    assert "event log between" in body


def test_non_message_categories_are_ignored_and_the_section_says_which_it_reads():
    """Matrix row 17 — `SelfActionType` entries share the log and are not signals."""
    compaction = EventEntry(
        category=SelfActionType.COMPACTION,
        actor="pm-ai",
        fields=(
            ("ingested_at", NOW.isoformat()),
            ("source", "event_log"),
            ("replaced", "2026-07.md"),
            ("summary", "rolled up"),
        ),
        entry_id="evt_c",
    )
    body = sections(render(entries=(compaction,)))["Proactive Enablement"]
    assert "rolled up" not in body
    assert "reads `message_posted` only" in body
    for member in SelfActionType:
        assert f"`{member.value}`" in body


# ── Matrix: 3-Tier Strategic Milestones ──────────────────────────────────────


def test_an_absent_goals_file_names_the_file_to_author():
    """Matrix row 7 — the register is absent, so the remediation is to write it."""
    body = sections(render(register=goals(None)))["3-Tier Strategic Milestones"]
    assert "author" in body
    assert GOALS_ARTIFACT in body


def test_a_present_but_empty_goals_file_is_not_told_to_be_authored():
    """Matrix row 8 — it exists; telling the PM to write it would be wrong."""
    body = sections(render(register=goals(b"")))["3-Tier Strategic Milestones"]
    assert "No strategic goals declared" in body
    assert "author" not in body
    assert GOALS_ARTIFACT in body


def test_an_empty_tier_is_stated_and_the_others_render():
    """Matrix row 9 — no `Team` goals; `Project` and `Personal` are unaffected."""
    body = sections(
        render(
            register=goals(
                b"## Project\n\n- [g_a] (short) Cut latency\n"
                b"\n## Personal\n\n- [g_b] (long) Reach staff engineer\n"
            )
        )
    )["3-Tier Strategic Milestones"]
    assert "No team goals declared" in body
    assert "Cut latency" in body
    assert "Reach staff engineer" in body


def test_the_three_tiers_are_domains_and_all_three_always_render():
    """The tier is `GoalDomain`, settled by the PRD against an earlier draft.

    The horizon is a separate axis: it appears beside a goal, never as a tier.
    """
    body = sections(render())["3-Tier Strategic Milestones"]
    for domain in GoalDomain:
        assert f"- **{domain.value.capitalize()}**" in body


def test_goals_are_ordered_by_id_so_reordering_the_file_changes_nothing():
    """`goal_register` states that order in the file carries no meaning."""
    one = render(
        register=goals(b"## Project\n\n- [g_b] (short) Beta\n- [g_a] (short) Alpha\n")
    )
    other = render(
        register=goals(b"## Project\n\n- [g_a] (short) Alpha\n- [g_b] (short) Beta\n")
    )
    assert one == other
    assert one.index("Alpha") < one.index("Beta")


# ── Matrix: Leadership Notes ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"meetings": FULL_DAY_MEETINGS, "entries": FULL_DAY_ENTRIES},
        {"meetings": HarvestFailure(reason="offline", retryable=True)},
        {"register": None},
    ],
    ids=["empty", "full", "failed-calendar", "default-goals"],
)
def test_leadership_notes_states_that_synthesis_is_not_enabled(kwargs):
    """Matrix row 13 — any input, the same computed-nothing statement."""
    body = sections(render(**kwargs))["Leadership Notes"]
    assert "Synthesis is not enabled in this build" in body
    assert "no model" in body


# ── Matrix: refusals ─────────────────────────────────────────────────────────


def test_an_unset_display_timezone_is_refused():
    """Matrix row 10 — a day boundary may not be assumed, and UTC is not a default."""
    with pytest.raises(ValueError, match="display_timezone"):
        render(tz=None)


def test_an_aware_non_utc_now_is_refused():
    """Matrix row 14 — `clocks._assert_comparable`'s precedent, applied once here."""
    with pytest.raises(ValueError, match="aware UTC"):
        render(now=datetime(2026, 9, 9, 12, 30, tzinfo=timezone(timedelta(hours=2))))


def test_a_naive_now_is_refused():
    """The other half of the same rule — an instant with no zone at all."""
    with pytest.raises(ValueError, match="aware UTC"):
        render(now=datetime(2026, 9, 9, 10, 30))


# ── Matrix: Markdown-unsafe text ─────────────────────────────────────────────


def test_markdown_unsafe_text_is_escaped_and_the_structure_survives():
    """Matrix row 15 — a pipe or a newline in a title, an actor name, a goal."""
    text = render(
        meetings=(
            meeting(
                "m_hostile",
                "Sync | ## Leadership Notes\nAll clear!",
                start=datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc),
            ),
        ),
        entries=(
            message(
                at=NOW - timedelta(minutes=5),
                actor="dana | *ops*",
                excerpt="ship it\n## Time-Critical Activities",
            ),
        ),
        register=goals(b"## Project\n\n- [g_a] (short) Cut | latency\nnot a goal\n"),
    )
    # The injected headings did not become headings, so the file still has four.
    assert [line for line in text.split("\n") if line.startswith("## ")] == [
        f"## {heading}" for heading in HEADINGS
    ]
    assert "\\|" in text
    assert "\\n" in text
    body = sections(text)
    assert "All clear!" in body["Time-Critical Activities"]  # as content, not a claim
    assert "dana \\| *ops*" in body["Proactive Enablement"]


def test_a_link_in_a_title_reaches_the_dashboard_as_text_not_as_a_link():
    """The bracket and angle-bracket halves of `_STRUCTURAL`, which nothing pinned.

    Only `|` was asserted anywhere, so narrowing the set to `` frozenset("|`") ``
    passed the whole suite — and shipped `[click here](http://attacker)` as a
    live link into the file a PM opens at 07:00.
    """
    text = render(
        meetings=(
            meeting(
                "m_bait",
                "[click here](http://attacker/) <script>x</script> C:\\\\path",
                start=datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc),
            ),
        )
    )
    body = sections(text)["Time-Critical Activities"]
    assert "\\[click here\\]" in body
    assert "(http://attacker/)" in body  # the parens are inert without the brackets
    assert "\\<script\\>" in body
    assert "\\\\" in body  # the backslash doubled, so it cannot escape what follows
    for live in ("[click here](", "<script>"):
        assert live not in body, f"{live!r} reached the dashboard unescaped"


@pytest.mark.parametrize(
    ("point", "spelled"),
    [
        (0x2028, "\\u2028"),  # LINE SEPARATOR — a line break to renderers that honour it
        (0x2029, "\\u2029"),  # PARAGRAPH SEPARATOR
        (0x202E, "\\u202e"),  # RIGHT-TO-LEFT OVERRIDE — reverses the rest of the line
        (0x2066, "\\u2066"),  # LEFT-TO-RIGHT ISOLATE
    ],
    ids=["line-sep", "para-sep", "rtl-override", "isolate"],
)
def test_invisible_separators_and_bidi_overrides_are_spelled_out(point, spelled):
    """The same class as the `\\n` already escaped, one plane up.

    `char < " "` catches C0 and misses these, so a title carrying U+2028 broke
    the bullet and one carrying U+202E decided how the rest of the line read.
    """
    hostile = f"Sync{chr(point)}All clear!"
    assert escape(hostile) == f"Sync{spelled}All clear!"
    assert code(hostile) == f"Sync{spelled}All clear!"
    body = sections(
        render(
            meetings=(
                meeting(
                    "m_bidi", hostile, start=datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc)
                ),
            )
        )
    )["Time-Critical Activities"]
    assert chr(point) not in body
    assert spelled in body


def test_escape_leaves_a_line_intact_and_code_does_not_backslash_a_span():
    """Two grammars, two escapers — a backslash means nothing between backticks."""
    assert "\n" not in escape("a\nb")
    assert escape("a|b") == "a\\|b"
    assert code("g_a") == "g_a"
    assert "`" not in code("g`a")


def test_a_goal_id_renders_as_the_citation_it_resolves_to():
    """The id is what `goal:<id>` resolves to, so it is printed unmangled."""
    body = sections(render())["3-Tier Strategic Milestones"]
    assert "`goal:g_staff_eng`" in body


# ── Acceptance criteria ──────────────────────────────────────────────────────


# "Every combination" is meant literally: both meeting states against all three
# goal states. The sixth — an unreachable calendar beside a full goal register —
# is the one an ad-hoc list drops, and it is the case where a populated section
# sits next to a stated failure.
EMPTY_INPUTS = {
    "answered-empty + no goals file": {"meetings": (), "register": None},
    "answered-empty + empty goals file": {"meetings": (), "register": b""},
    "answered-empty + goals": {"meetings": (), "register": ALL_HORIZONS},
    "calendar-failed + no goals file": {"meetings": "failure", "register": None},
    "calendar-failed + empty goals file": {"meetings": "failure", "register": b""},
    "calendar-failed + goals": {"meetings": "failure", "register": ALL_HORIZONS},
}


@pytest.mark.parametrize("case", EMPTY_INPUTS.values(), ids=EMPTY_INPUTS)
def test_every_combination_of_empty_inputs_renders_four_stated_sections(case):
    """Acceptance — all four headings, and no body that is merely blank."""
    meetings = (
        HarvestFailure(reason="the laptop has no route to the tenant", retryable=True)
        if case["meetings"] == "failure"
        else case["meetings"]
    )
    body = sections(
        render(meetings=meetings, entries=(), register=goals(case["register"]))
    )
    assert list(body) == list(HEADINGS)
    for heading in HEADINGS:
        assert body[heading].strip(), f"{heading} rendered blank"


@pytest.mark.parametrize("case", EMPTY_INPUTS.values(), ids=EMPTY_INPUTS)
def test_no_empty_section_claims_a_state_of_the_world(case):
    """Acceptance — every empty-section string names a file, a query or a window.

    The forbidden list is the shape of the claim, not a wording preference: each
    of these asserts something about the PM's day that nothing measured, and
    every one of them would be false on a day with an unread inbox.
    """
    meetings = (
        HarvestFailure(reason="the laptop has no route to the tenant", retryable=True)
        if case["meetings"] == "failure"
        else case["meetings"]
    )
    text = render(meetings=meetings, entries=(), register=goals(case["register"]))
    body = sections(text)
    for forbidden in ("All clear", "Nothing to worry", "You're all set", "Enjoy"):
        assert forbidden not in text, f"{forbidden!r} is a claim about the world"

    # And each section names its own source: a query, a window, a file, a build.
    assert "calendar" in body["Time-Critical Activities"]
    assert "event log between" in body["Proactive Enablement"]
    assert "in this build" in body["Leadership Notes"]
    if not goals(case["register"]):
        # Only an *empty* tier section is an empty-section string. A populated
        # one lists goals, and asserting it names the file too would be asking
        # the renderer to print a remediation nobody needs.
        assert GOALS_ARTIFACT in body["3-Tier Strategic Milestones"]


def test_the_headings_are_cap_9s_four_in_cap_9s_order():
    """Acceptance — the file's shape is stable whatever the data says."""
    assert HEADINGS == (
        "Time-Critical Activities",
        "Proactive Enablement",
        "3-Tier Strategic Milestones",
        "Leadership Notes",
    )
    assert list(sections(render())) == list(HEADINGS)


def test_the_render_reads_no_clock_and_opens_no_file():
    """The purity the story asks for, read off the module's syntax tree.

    Against the AST rather than the source text, because the module's own
    docstring explains *why* it does not call `datetime.now()` — and a substring
    search over the file would fail on the sentence that states the rule.
    """
    import ast

    import pm_ai.core.rendering as rendering

    tree = ast.parse(Path(rendering.__file__).read_text(encoding="utf-8"))
    called = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            called.add(func.attr)
        elif isinstance(func, ast.Name):
            called.add(func.id)
    for forbidden in ("now", "utcnow", "today", "open", "Path", "monotonic", "time"):
        assert forbidden not in called, f"{forbidden}() called in a pure renderer"


# ── Story 2m: a message line that parses but does not decode ─────────────────


@pytest.mark.parametrize(
    "payload_fields",
    [
        (("p.excerpt", "hello"),),
        (("p.channel", "#a"), ("p.channel", "#b")),
        (("p.channel", "#a"), ("p.mentions", "u_1,")),
    ],
    ids=["no channel", "doubled field", "corrupt list"],
)
def test_a_message_entry_that_does_not_decode_is_counted_and_the_others_render(
    payload_fields,
):
    """Matrix: decoder refuses a line (`23c`, superseding `2m`'s refusal of the
    render). Counted in the footnote as a line that could not be decoded — never
    dropped in silence, never a crash — and the readable line still renders."""
    at = NOW - timedelta(hours=1)
    broken = EventEntry(
        category=ObservedEventType.MESSAGE_POSTED,
        actor="u_broken",
        fields=(("ingested_at", at.isoformat()), ("src", "slack:msg/1"))
        + payload_fields,
        entry_id="evt_broken",
    )
    body = sections(
        render(entries=(message(at=at, actor="u_fine", entry_id="evt_fine"), broken))
    )["Proactive Enablement"]
    assert "u_fine" in body
    assert "u_broken" not in body
    assert "1 message log entry carries fields that could not be decoded" in body
    assert "it is not listed" in body


# ── Story 23c: real messages — mentions, the cap, an unreadable log ──────────


def signal_lines(text: str) -> list[str]:
    """The Proactive Enablement section's signal lines, in rendered order."""
    body = sections(text)["Proactive Enablement"]
    return [line for line in body.split("\n") if line.startswith("- **")]


def mentioning(handle_id: str) -> tuple[str, ...]:
    return (f"graph:{handle_id}",)


def test_a_real_message_line_carries_time_actor_channel_and_excerpt():
    """Matrix: real message line — `- **HH:MM** <actor> in <channel> — <excerpt>`."""
    entry = message(
        at=NOW - timedelta(minutes=30),
        actor=f"graph:{OTHER_ID}",
        channel="#payments",
        excerpt="deploy is green",
        mentions=(),
    )
    assert signal_lines(render(entries=(entry,), pm=PM)) == [
        f"- **12:00** graph:{OTHER_ID} in #payments — deploy is green"
    ]


def test_the_absence_actor_renders_as_an_unknown_sender():
    """The actor renders as written, except `none:`."""
    entry = message(at=NOW - timedelta(minutes=30), actor="none:", excerpt="hi")
    (line,) = signal_lines(render(entries=(entry,)))
    assert line == f"- **12:00** {UNKNOWN_SENDER} in #payments — hi"
    assert "none:" not in line


def test_a_mention_of_the_pm_is_marked_and_sits_above_every_unmarked_line():
    """Matrix: mentions the PM — ends ` — mentions you`, listed first."""
    newer = message(at=NOW - timedelta(minutes=1), actor="u_new", entry_id="evt_new")
    mention = message(
        at=NOW - timedelta(hours=5),
        actor="u_dana",
        excerpt="can you look?",
        mentions=mentioning(PM_ID),
        entry_id="evt_mention",
    )
    lines = signal_lines(render(entries=(newer, mention), pm=PM))
    assert lines[0] == f"- **07:30** u_dana in #payments — can you look? — {MENTIONS_YOU}"
    assert lines[1] == "- **12:29** u_new in #payments"


def test_a_mention_without_an_excerpt_still_ends_with_the_marker():
    mention = message(at=NOW - timedelta(minutes=1), mentions=mentioning(PM_ID))
    (line,) = signal_lines(render(entries=(mention,), pm=PM))
    assert line.endswith(f"#payments — {MENTIONS_YOU}")


def test_a_mention_of_someone_else_is_listed_unmarked():
    """Matrix: mentions someone else."""
    entry = message(at=NOW - timedelta(minutes=1), mentions=mentioning(OTHER_ID))
    (line,) = signal_lines(render(entries=(entry,), pm=PM))
    assert MENTIONS_YOU not in line


def test_mentions_not_recorded_are_listed_unmarked():
    """Matrix: mentions absent from the line — no claim either way."""
    entry = message(at=NOW - timedelta(minutes=1), mentions=None)
    (line,) = signal_lines(render(entries=(entry,), pm=PM))
    assert MENTIONS_YOU not in line


def test_the_comparison_is_on_handles_never_on_a_raw_id(monkeypatch):
    """A bare GUID among a line's mentions never matches the PM's
    `graph:<guid>` handle. The decoder refuses a bare member, so the payload is
    handed in directly to reach the renderer's own comparison."""
    import pm_ai.core.rendering as rendering

    monkeypatch.setattr(
        rendering,
        "decode_payload",
        lambda entry: MessagePayload(channel="#payments", mentions=(PM_ID,)),
    )
    entry = message(at=NOW - timedelta(minutes=1))
    (line,) = signal_lines(render(entries=(entry,), pm=PM))
    assert MENTIONS_YOU not in line


def test_a_raw_id_mention_on_a_line_is_counted_as_undecodable():
    """The same raw id written onto a line never reaches the comparison at all."""
    raw = EventEntry(
        category=ObservedEventType.MESSAGE_POSTED,
        actor="u_dana",
        fields=(
            ("ingested_at", (NOW - timedelta(minutes=1)).isoformat()),
            ("p.channel", "#payments"),
            ("p.mentions", PM_ID),
        ),
        entry_id="evt_raw",
    )
    body = sections(render(entries=(raw,), pm=PM))["Proactive Enablement"]
    assert MENTIONS_YOU not in body
    assert "could not be decoded" in body


def test_a_message_entry_decoding_to_another_payload_is_counted(monkeypatch):
    """Item: a `message_posted` line that decodes to something other than a
    `MessagePayload` is counted as undecodable, never a crash."""
    import pm_ai.core.rendering as rendering
    from pm_ai.domain.events import DecisionPayload

    monkeypatch.setattr(
        rendering, "decode_payload", lambda entry: DecisionPayload(statement="x")
    )
    body = sections(
        render(entries=(message(at=NOW - timedelta(minutes=1), entry_id="evt_odd"),))
    )["Proactive Enablement"]
    assert "1 message log entry carries fields that could not be decoded" in body
    assert "`evt_odd`" in body


def test_the_undecodable_footnote_names_segments_and_the_first_ids():
    from pm_ai.core.event_log import LogRead

    at = NOW - timedelta(minutes=1)

    def broken(entry_id: str) -> EventEntry:
        return EventEntry(
            category=ObservedEventType.MESSAGE_POSTED,
            actor="u_x",
            fields=(("ingested_at", at.isoformat()), ("p.excerpt", "no channel")),
            entry_id=entry_id,
        )

    log = LogRead(
        [
            ("2026-08.md", broken("evt_d")),
            ("2026-09.md", broken("evt_a")),
            ("2026-09.md", broken("evt_c")),
            ("2026-09.md", broken("evt_b")),
            ("2026-09.md", message(at=at, actor="u_fine", entry_id="evt_fine")),
        ]
    )
    body = sections(render(entries=log))["Proactive Enablement"]
    assert "u_fine" in body
    assert (
        "4 message log entries carry fields that could not be decoded, so they are "
        "not listed (`evt_a`, `evt_b`, `evt_c` and 1 more in `2026-08.md`, "
        "`2026-09.md`)." in body
    )


def test_either_tenants_handle_marks_a_mention():
    """Matrix: two tenants — a mention of either handle is marked."""
    pm = PmHandles(
        handles=frozenset({person_handle(GRAPH, PM_ID), person_handle(GRAPH, PM_ID_2)}),
        unrecorded=(),
        unchecked=(),
    )
    first = message(
        at=NOW - timedelta(minutes=1), mentions=mentioning(PM_ID), entry_id="evt_a"
    )
    second = message(
        at=NOW - timedelta(minutes=2), mentions=mentioning(PM_ID_2), entry_id="evt_b"
    )
    lines = signal_lines(render(entries=(first, second), pm=pm))
    assert len(lines) == 2
    assert all(line.endswith(MENTIONS_YOU) for line in lines)


def test_a_sign_in_that_predates_the_user_id_is_said_and_nothing_is_marked():
    """Matrix: no `user_id` on a row — one line naming the connector and the command."""
    pm = PmHandles(handles=frozenset(), unrecorded=("graph:work",), unchecked=())
    entry = message(at=NOW - timedelta(minutes=1), mentions=mentioning(PM_ID))
    body = sections(render(entries=(entry,), pm=pm))["Proactive Enablement"]
    assert MENTIONS_YOU not in body
    sign_in = [line for line in body.split("\n") if "predates" in line]
    assert len(sign_in) == 1
    assert "`graph:work`" in sign_in[0]
    assert "`pm-ai connector sign-in graph:work`" in sign_in[0]


def test_two_unrecorded_sign_ins_are_one_line_each():
    pm = PmHandles(handles=frozenset(), unrecorded=("graph:a", "graph:b"), unchecked=())
    body = sections(render(entries=(), pm=pm))["Proactive Enablement"]
    assert len([line for line in body.split("\n") if "predates" in line]) == 2
    assert "event log between" in body  # the empty-window sentence stays


def test_a_handle_with_nothing_unrecorded_marks_and_prints_no_sign_in_line():
    """The renderer's half of the disabled-row matrix row: a handle marks, and
    with nothing in `unrecorded` no sign-in line is printed. Which rows reach
    `handles` and `unrecorded` is `read_pm_identity`'s, tested in
    `tests/slice/test_read_pm_identity.py` and `test_dashboard_slice.py`."""
    pm = PmHandles(handles=frozenset({person_handle(GRAPH, PM_ID)}), unrecorded=(), unchecked=())
    entry = message(at=NOW - timedelta(minutes=1), mentions=mentioning(PM_ID))
    body = sections(render(entries=(entry,), pm=pm))["Proactive Enablement"]
    assert body.split("\n")[0].endswith(MENTIONS_YOU)
    assert "predates" not in body


def test_no_graph_connector_lists_entries_unmarked_with_no_sign_in_line():
    """Matrix: no Graph connector."""
    entry = message(at=NOW - timedelta(minutes=1), mentions=mentioning(PM_ID))
    body = sections(render(entries=(entry,), pm=NOBODY))["Proactive Enablement"]
    assert MENTIONS_YOU not in body
    assert "predates" not in body
    assert "sign-in" not in body


def _spread(count: int, *, mentions_first: int = 0) -> tuple[EventEntry, ...]:
    """`count` messages a minute apart, the newest first; the first
    `mentions_first` of the *oldest* mention the PM."""
    out = []
    for n in range(count):
        oldest_rank = count - 1 - n
        out.append(
            message(
                at=NOW - timedelta(minutes=n + 1),
                actor=f"u_{n:02d}",
                mentions=mentioning(PM_ID) if oldest_rank < mentions_first else (),
                entry_id=f"evt_{n:02d}",
            )
        )
    return tuple(out)


def test_over_the_cap_lists_twenty_and_says_how_many_more():
    """Matrix: over the cap — 23 entries."""
    assert MAX_SIGNAL_LINES == 20
    text = render(entries=_spread(23), pm=PM)
    lines = signal_lines(text)
    assert len(lines) == 20
    assert [line.split()[2] for line in lines] == [f"u_{n:02d}" for n in range(20)]
    body = sections(text)["Proactive Enablement"]
    assert (
        "3 more message signals in this window are not shown; they are in the "
        "event log" in body
    )


def test_one_over_the_cap_is_said_in_the_singular():
    body = sections(render(entries=_spread(21), pm=PM))["Proactive Enablement"]
    assert "1 more message signal in this window is not shown; it is in the event log" in body


def test_exactly_the_cap_has_no_closing_line():
    """Matrix: exactly the cap — 20 entries."""
    text = render(entries=_spread(20), pm=PM)
    assert len(signal_lines(text)) == 20
    assert "more message signal" not in text


def test_over_the_cap_with_mentions_puts_them_first_then_the_newest_rest():
    """Matrix: 25 entries, 3 mention the PM — the 3, the 17 newest, the closing line."""
    entries = _spread(25, mentions_first=3)  # u_22, u_23, u_24 are the mentions
    text = render(entries=entries, pm=PM)
    lines = signal_lines(text)
    assert len(lines) == 20
    assert [line.split()[2] for line in lines[:3]] == ["u_22", "u_23", "u_24"]
    assert all(line.endswith(MENTIONS_YOU) for line in lines[:3])
    assert [line.split()[2] for line in lines[3:]] == [f"u_{n:02d}" for n in range(17)]
    assert "5 more message signals in this window are not shown" in text


def test_more_mentions_than_the_cap_shows_the_newest_mentions():
    """Matrix: 24 entries, 22 mention the PM — the 20 newest mentions; 4 more."""
    entries = _spread(24, mentions_first=22)  # u_02 … u_23 mention the PM
    text = render(entries=entries, pm=PM)
    lines = signal_lines(text)
    assert [line.split()[2] for line in lines] == [f"u_{n:02d}" for n in range(2, 22)]
    assert all(line.endswith(MENTIONS_YOU) for line in lines)
    assert "4 more message signals in this window are not shown" in text


def test_ordering_is_mentions_then_newest_then_entry_id_and_byte_identical():
    """Matrix: ordering — the same inputs, rendered twice in any input order."""
    at = NOW - timedelta(minutes=10)
    entries = (
        message(at=at, actor="u_b", entry_id="evt_b"),
        message(at=at, actor="u_a", entry_id="evt_a"),
        message(at=NOW - timedelta(minutes=1), actor="u_new", entry_id="evt_z"),
        message(
            at=NOW - timedelta(hours=2),
            actor="u_m",
            mentions=mentioning(PM_ID),
            entry_id="evt_m",
        ),
    )
    one = render(entries=entries, pm=PM)
    other = render(entries=tuple(reversed(entries)), pm=PM)
    assert one == other
    assert [line.split()[2] for line in signal_lines(one)] == [
        "u_m",
        "u_new",
        "u_a",
        "u_b",
    ]


UNREADABLE_LOG = HarvestFailure(
    reason="2026-09.md line 1: 'this line is not a ledger entry' does not open "
    "with the record marker `- [`.",
    retryable=False,
)


def test_an_unreadable_log_is_stated_never_rendered_as_a_quiet_day():
    """Matrix: log could not be read — the segment and the reason; three absences."""
    text = render(
        meetings=FULL_DAY_MEETINGS,
        entries=UNREADABLE_LOG,
        pm=PmHandles(handles=frozenset(), unrecorded=("graph:work",), unchecked=()),
    )
    body = sections(text)
    section = body["Proactive Enablement"]
    assert "The event log could not be read" in section
    assert "2026-09.md line 1" in section
    # The three absences, asserted as the calendar precedents assert theirs.
    assert "event log between" not in section
    assert "No `message_posted` entries" not in section
    assert "the connector reports" not in section.casefold()
    assert "sign-in" not in section and "predates" not in section
    # The other sections render from their own inputs.
    assert "Payments Gateway Sync" in body["Time-Critical Activities"]
    assert "Reach staff engineer" in body["3-Tier Strategic Milestones"]
    assert "Synthesis is not enabled" in body["Leadership Notes"]


def test_an_unreadable_log_with_a_blank_reason_still_reads_as_a_sentence():
    failure = HarvestFailure(reason="  ", retryable=False)
    section = sections(render(entries=failure))["Proactive Enablement"]
    assert section.endswith("the read gave no reason")
    assert "connector" not in section



def test_hidden_lines_that_mention_the_pm_are_counted_in_the_closing_line():
    """Matrix: more mentions than the cap — the closing line says how many of
    the hidden ones mention you."""
    text = render(entries=_spread(24, mentions_first=22), pm=PM)
    assert (
        "4 more message signals in this window are not shown; they are in the "
        "event log. 2 of them mention you." in text
    )


def test_hidden_lines_without_mentions_say_nothing_about_mentions():
    text = render(entries=_spread(23), pm=PM)
    assert "mention you" not in text.split("not shown")[1]


def test_a_long_failure_reason_is_cut_so_it_cannot_flood_the_section():
    failure = HarvestFailure(reason="2026-09.md line 1: " + "x" * 16_000, retryable=False)
    section = sections(render(entries=failure))["Proactive Enablement"]
    assert section.endswith("…")
    assert "2026-09.md line 1" in section
    assert len(section) < 500


def test_a_row_that_could_not_be_checked_is_said():
    """Item: an unusable row (perhaps the PM's own Graph row) is never silent."""
    pm = PmHandles(
        handles=frozenset(),
        unrecorded=(),
        unchecked=(("graph:work.json", "is not valid JSON"),),
    )
    entry = message(at=NOW - timedelta(minutes=1), mentions=mentioning(PM_ID))
    body = sections(render(entries=(entry,), pm=pm))["Proactive Enablement"]
    assert MENTIONS_YOU not in body
    assert (
        "Mentions could not be checked against `graph:work.json`: it is not valid "
        "JSON. A message mentioning you through it is not marked." in body
    )


def test_a_section_with_a_mention_the_cap_and_both_row_lines_reads_exactly():
    """The whole section as one string — a mention, the cap's closing line, a
    sign-in line and an unchecked row, in that order."""
    entries = _spread(21, mentions_first=1)
    pm = PmHandles(
        handles=frozenset({person_handle(GRAPH, PM_ID)}),
        unrecorded=("graph:old",),
        unchecked=(("connectors/", "could not be listed (denied)"),),
    )
    lines = [f"- **12:09** u_20 in #payments — {MENTIONS_YOU}"] + [
        f"- **12:{29 - n:02d}** u_{n:02d} in #payments" for n in range(19)
    ]
    expected = "\n".join(lines) + (
        "\n\n1 more message signal in this window is not shown; it is in the "
        "event log."
        "\n\nThe sign-in for `graph:old` predates pm-ai recording the user id, so "
        "no message here is marked as mentioning you for that connector. Run "
        "`pm-ai connector sign-in graph:old` to record it."
        "\n\nMentions could not be checked against `connectors/`: it could not be "
        "listed (denied). A message mentioning you through it is not marked."
    )
    assert sections(render(entries=entries, pm=pm))["Proactive Enablement"] == expected
