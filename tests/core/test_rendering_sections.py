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
    MESSAGE_WINDOW,
    NO_MEETINGS,
    code,
    escape,
    render_dashboard,
)
from pm_ai.domain.event_entries import EventEntry, SelfActionType
from pm_ai.domain.events import ObservedEventType
from pm_ai.domain.goals import GoalDomain
from pm_ai.domain.harvest import HarvestFailure
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting

PERSONAL = DataScope(ScopeKind.PERSONAL)

DISPLAY = timezone(timedelta(hours=2))
"""The PM's display zone, as `23b` will hand it over from `display_timezone`."""

NOW = datetime(2026, 9, 9, 10, 30, tzinfo=timezone.utc)
"""12:30 in the display zone — mid-morning, so a day can have both a meeting
that has ended and one that has not. The "all ended" row needs a later instant
and states its own.
"""

GOLDEN = Path(__file__).parent / "goldens" / "dashboard_full_day.md"


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
    at: datetime | None,
    actor: str = "u_dana",
    channel: str = "#payments",
    excerpt: str | None = None,
    entry_id: str = "evt_1",
) -> EventEntry:
    """A `message_posted` ledger entry, shaped as `_append_batch` writes one.

    `at=None` is the pre-`2e` entry the matrix names: the field is absent, not
    empty, because that is what an entry written before storage stamped the
    clock actually looks like.
    """
    fields: list[tuple[str, str]] = []
    if at is not None:
        fields.append(("ingested_at", at.isoformat()))
    fields.append(("src", "slack:msg/1"))
    fields.append(("channel", channel))
    if excerpt is not None:
        fields.append(("excerpt", excerpt))
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
) -> str:
    return render_dashboard(
        meetings,
        entries,
        goals() if register is None else register,
        now,
        tz=tz,
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
    listed = [
        line for line in full_day().split("\n") if line.startswith("- **") and "—" in line
    ]
    assert [line.split("**")[2].split("—")[0].strip() for line in listed[:3]] == [
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


def test_a_retryable_failure_says_the_next_harvest_may_clear_it():
    """`HarvestFailure.retryable` decides what the operator is told to do."""
    body = sections(
        render(
            meetings=HarvestFailure(
                reason="429 from Graph", retryable=True, retry_after=timedelta(seconds=30)
            )
        )
    )["Time-Critical Activities"]
    assert "0:00:30" in body
    assert NO_MEETINGS not in body


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


# ── Matrix: Proactive Enablement ─────────────────────────────────────────────


def test_no_message_entries_names_the_window():
    """Matrix row 11 — an empty log states the window it searched."""
    body = sections(render(entries=()))["Proactive Enablement"]
    assert "event log between" in body
    assert "2026-09-08 12:30" in body  # NOW - MESSAGE_WINDOW, in the display zone
    assert "2026-09-09 12:30" in body
    assert MESSAGE_WINDOW == timedelta(hours=24)


def test_the_empty_section_says_nothing_writes_the_category_yet():
    """Matrix row 12 — the second knowingly empty section in wave 1."""
    body = sections(render(entries=()))["Proactive Enablement"]
    assert "33d" in body
    assert "No connector in this build writes `message_posted`" in body


def test_an_entry_with_no_ingested_at_is_counted_not_dated_from_now():
    """Matrix row 16 — a pre-`2e` entry, excluded from the window and reported."""
    body = sections(render(entries=(message(at=None),)))["Proactive Enablement"]
    assert "1 message log entry carries no `ingested_at`" in body
    assert "u_dana" not in body  # it was not placed, so it is not listed


def test_a_message_outside_the_window_is_not_a_signal():
    """The window is a bound, not a decoration."""
    stale = message(at=NOW - MESSAGE_WINDOW - timedelta(minutes=1), actor="u_old")
    body = sections(render(entries=(stale,)))["Proactive Enablement"]
    assert "u_old" not in body
    assert "event log between" in body


def test_the_build_note_is_dropped_once_the_log_holds_a_message_entry():
    """The wave-1 sentence is conditional, and the condition is computed.

    "No connector in this build writes `message_posted`" printed beside a log
    that plainly holds one — outside the window, or unplaceable — would be this
    module's own honesty rule broken in the other direction.
    """
    stale = message(at=NOW - MESSAGE_WINDOW - timedelta(minutes=1))
    assert "33d" not in sections(render(entries=(stale,)))["Proactive Enablement"]
    assert "33d" not in sections(render(entries=(message(at=None),)))[
        "Proactive Enablement"
    ]
    assert "33d" in sections(render(entries=()))["Proactive Enablement"]


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


EMPTY_INPUTS = [
    {"meetings": (), "entries": (), "register": None},
    {"meetings": (), "entries": (), "register": b""},
    {"meetings": (), "entries": (), "register": ALL_HORIZONS},
    {"meetings": "failure", "entries": (), "register": None},
    {"meetings": "failure", "entries": (), "register": b""},
]


@pytest.mark.parametrize("case", EMPTY_INPUTS, ids=range(len(EMPTY_INPUTS)))
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


@pytest.mark.parametrize("case", EMPTY_INPUTS, ids=range(len(EMPTY_INPUTS)))
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
