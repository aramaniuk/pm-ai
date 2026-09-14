"""The project dashboard — one test per row of `23d`'s matrix.

Spec: `_bmad-output/specs/spec-pm-ai/stories/23d-project-render-scope-wall.md`.

The story's deliverable is an **absence**: `render_project_dashboard` has no
goals parameter, so handing it the PM's personal register is impossible rather
than forbidden. An absence is hard to test by calling a function — every call
that would prove it is a call that does not compile — so it is pinned from two
sides here. `inspect.signature` reads the parameter list at run time (and
`test_domain_invariants.py` reads it again as the AD-25 gate), and a fixture
under `mypy_fixture_project_render_goals.py` proves the checker refuses the
call, run as a subprocess because `[tool.mypy] files = ["pm_ai"]` never looks at
`tests/`.

The rest of the file is the ordinary matrix, plus the one assertion that keeps
the two dashboards from becoming two formats: given the same meetings, both
renderers' Time-Critical sections are byte-identical.

Same purity properties as `23a`: no `tmp_path` anywhere, no clock read, and the
display zone is a fixed `+02:00` so nothing here needs the optional `tzdata`.
"""

from __future__ import annotations

import inspect
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pm_ai.core.goal_register import GoalRegister, parse_goals
from pm_ai.core.rendering import (
    MESSAGE_WINDOW,
    NO_MEETINGS,
    PROJECT_HEADINGS,
    render_dashboard,
    render_project_dashboard,
)
from pm_ai.domain.event_entries import EventEntry
from pm_ai.domain.events import ObservedEventType
from pm_ai.domain.harvest import HarvestFailure
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting

REPO_ROOT = Path(__file__).resolve().parents[2]

ALPHA = DataScope(ScopeKind.PROJECT, project_id="alpha")
"""The project whose day this is. `11a`'s accessor reads one scope; `23b` passes
what it read, and this renderer renders what it is passed."""

PERSONAL = DataScope(ScopeKind.PERSONAL)

DISPLAY = timezone(timedelta(hours=2))
NOW = datetime(2026, 9, 9, 10, 30, tzinfo=timezone.utc)
"""12:30 in the display zone — the same instant `23a`'s suite uses, so the
byte-identical comparison below is over the same clock as well as the same
data."""


# ── Fixtures ─────────────────────────────────────────────────────────────────


def meeting(
    meeting_id: str,
    title: str,
    *,
    start: datetime,
    minutes: int = 30,
    attendees: int = 2,
    scope: DataScope = ALPHA,
    tentative: bool = False,
) -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title=title,
        start=start,
        duration_minutes=minutes,
        attendees=tuple(Actor(f"u_{n}") for n in range(attendees)),
        scope=scope,
        tentative=tentative,
    )


def message(
    *,
    at: datetime,
    actor: str = "u_dana",
    channel: str = "#alpha-build",
    excerpt: str | None = None,
    entry_id: str = "evt_1",
) -> EventEntry:
    """A `message_posted` entry off the project's event log."""
    fields: list[tuple[str, str]] = [
        ("ingested_at", at.isoformat()),
        ("src", "slack:msg/1"),
        ("channel", channel),
    ]
    if excerpt is not None:
        fields.append(("excerpt", excerpt))
    return EventEntry(
        category=ObservedEventType.MESSAGE_POSTED,
        actor=actor,
        fields=tuple(fields),
        entry_id=entry_id,
    )


def render(
    *,
    meetings: object = (),
    entries: object = (),
    now: datetime = NOW,
    tz: object = DISPLAY,
) -> str:
    # Typed loosely on purpose: the timezone row passes `None`, which is the
    # value the function is required to refuse.
    return render_project_dashboard(meetings, entries, now, tz=tz)  # type: ignore[arg-type]


def sections(text: str) -> dict[str, str]:
    """The rendered file, split back into the bodies it claims to have."""
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


PROJECT_MEETINGS = (
    meeting(
        "m_alpha_standup",
        "Alpha Standup",
        start=datetime(2026, 9, 9, 7, 0, tzinfo=timezone.utc),
    ),
    meeting(
        "m_alpha_review",
        "Alpha Architecture Review",
        start=datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc),
        minutes=60,
        attendees=5,
    ),
)

PROJECT_ENTRIES = (
    message(
        at=datetime(2026, 9, 9, 6, 15, tzinfo=timezone.utc),
        actor="u_dana",
        excerpt="Staging deploy is blocked on the migration",
        entry_id="evt_a",
    ),
    message(
        at=datetime(2026, 9, 9, 9, 40, tzinfo=timezone.utc),
        actor="u_sam",
        entry_id="evt_b",
    ),
)


# ── The wall ─────────────────────────────────────────────────────────────────


def test_the_signature_accepts_no_goal_register():
    """Acceptance — the deliverable, read off the parameter list.

    Asserted as a signature rather than as a list of permitted sources: a list
    needs a test that remembers to check it, and a parameter that does not exist
    cannot be handed the PM's career goals by any caller, correct or not.
    """
    parameters = inspect.signature(render_project_dashboard).parameters
    assert list(parameters) == ["meetings", "entries", "now", "tz"]
    assert not [
        name
        for name in parameters
        if "goal" in name.lower() or "register" in name.lower()
    ]


def test_no_parameter_is_annotated_with_a_goal_type():
    """The same wall one level down, where a rename could have moved it.

    `test_the_signature_accepts_no_goal_register` matches on parameter *names*,
    so a `context: GoalRegister` would satisfy it while opening exactly the
    route AD-25 closes. The annotations are the other half of the statement.
    """
    hints = [
        str(parameter.annotation)
        for parameter in inspect.signature(render_project_dashboard).parameters.values()
    ]
    assert not [hint for hint in hints if "Goal" in hint]


def test_a_register_cannot_be_passed_under_mypy():
    """Acceptance — the checker refuses the call, in its own words.

    A subprocess with an explicit path, which is what overrides `files =
    ["pm_ai"]`; the ordinary `uv run mypy` stays clean because it never reads
    `tests/`. A missing binary is a failure and never a skip, for the reason
    `test_types.py` states: a skip here would report green while the only
    compile-time half of the wall went unchecked.
    """
    fixture = Path(__file__).parent / "mypy_fixture_project_render_goals.py"
    if shutil.which("mypy") is None:
        pytest.fail(
            "mypy is not on PATH, so the negative type check did not run. It is "
            "a declared dev dependency; use `uv run pytest`, which puts "
            ".venv/bin on PATH, or `uv sync`."
        )
    result = subprocess.run(
        ["mypy", str(fixture.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        "AD-25: mypy accepted a call handing `render_project_dashboard` the "
        f"personal goal register:\n\n{result.stdout}\n{result.stderr}"
    )
    # Named codes, not just a non-zero exit: the fixture could start failing for
    # an unrelated reason — a renamed import, a moved module — and a test that
    # only checked the exit status would read green while proving nothing about
    # the register.
    assert "[arg-type]" in result.stdout, result.stdout
    assert "[call-arg]" in result.stdout, result.stdout
    # One per deliberately-wrong call in the fixture. A fourth would mean a real
    # error crept in beside them; two would mean one stopped being refused.
    assert result.stdout.count("error:") == 3, result.stdout


def test_the_fixture_is_invisible_to_the_ordinary_mypy_run():
    """The other half of the subprocess trick, and the one that could rot.

    `mypy_fixture_project_render_goals.py` holds three errors on purpose. If
    `[tool.mypy] files` ever grew `tests`, `uv run mypy` would go permanently
    red and the deliberate errors would be indistinguishable from real ones.
    """
    config = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'files = ["pm_ai"]' in config


# ── Matrix ───────────────────────────────────────────────────────────────────


def test_a_project_day_populates_both_sections():
    """Matrix row 1 — two project meetings and project log entries."""
    body = sections(render(meetings=PROJECT_MEETINGS, entries=PROJECT_ENTRIES))
    assert list(body) == list(PROJECT_HEADINGS)
    assert all(body[heading] for heading in PROJECT_HEADINGS)
    assert "Alpha Architecture Review" in body["Time-Critical Activities"]
    assert "u_dana" in body["Proactive Enablement"]


def test_the_document_carries_no_goals_section():
    """The absence, as the reader of the file sees it.

    CAP-9's four-section rule names `~/.manager-ai/memory/daily_dashboard.md` by
    path, so nothing requires a 3-Tier or Leadership Notes heading here — and
    the first of the two has no source this function may be handed.
    """
    text = render(meetings=PROJECT_MEETINGS, entries=PROJECT_ENTRIES)
    assert [line for line in text.split("\n") if line.startswith("## ")] == [
        f"## {heading}" for heading in PROJECT_HEADINGS
    ]
    assert "3-Tier Strategic Milestones" not in text
    assert "Leadership Notes" not in text
    assert "strategic_goals.md" not in text


def test_an_empty_calendar_says_what_the_personal_render_says():
    """Matrix row 2 — the same string, and deliberately not a possessive.

    The wording was settled on 2026-09-14: "No meetings on this project's
    calendar today" is reachable only by telling a shared section renderer which
    dashboard called it, which is the coupling sharing exists to prevent.
    """
    body = sections(render(meetings=()))["Time-Critical Activities"]
    assert NO_MEETINGS in body
    assert NO_MEETINGS == "No meetings on the calendar today"
    assert "project's calendar" not in body
    assert "answered" in body


def test_an_unreachable_calendar_never_claims_an_empty_day():
    """Matrix row 3 — a failed fetch produced no result to report."""
    failure = HarvestFailure(reason="token expired", retryable=False)
    body = sections(render(meetings=failure))["Time-Critical Activities"]
    assert "The calendar could not be read" in body
    assert "token expired" in body
    assert NO_MEETINGS not in body
    assert "No meetings on this project's calendar today" not in body


def test_an_empty_project_log_names_the_window():
    """Matrix row 4 — no signals, stated as the query it was."""
    body = sections(render(entries=()))["Proactive Enablement"]
    assert "No `message_posted` entries in the event log between" in body
    # The window's own bounds, in the display zone, so "in the window" is not a
    # phrase the reader has to take on trust.
    assert "2026-09-08 12:30" in body
    assert "2026-09-09 12:30" in body


def test_an_empty_window_states_that_nothing_writes_these_yet():
    """Matrix row 5 — knowingly empty in wave 1, exactly as in `23a`."""
    body = sections(render(entries=()))["Proactive Enablement"]
    assert "33d" in body
    # And not when the log holds one that merely fell outside the window: the
    # build note is a claim about producers, and printing it beside two entries
    # would be the invented evidence in the other direction.
    stale = message(at=NOW - MESSAGE_WINDOW - timedelta(minutes=1))
    assert "33d" not in sections(render(entries=(stale,)))["Proactive Enablement"]


def test_the_shared_section_renders_byte_identically_in_both_files():
    """Acceptance — the same meetings, the same Markdown, in both dashboards.

    Unconditional, and that is the point of the 2026-09-14 wording decision: it
    holds on the empty-day branch too, where the two callers hand over the same
    empty sequence and nothing distinguishes them.
    """
    empty = GoalRegister(present=False)
    for meetings in (PROJECT_MEETINGS, (), HarvestFailure(reason="429", retryable=True)):
        personal = sections(
            render_dashboard(meetings, PROJECT_ENTRIES, empty, NOW, tz=DISPLAY)
        )
        project = sections(
            render_project_dashboard(meetings, PROJECT_ENTRIES, NOW, tz=DISPLAY)
        )
        assert project["Time-Critical Activities"] == personal["Time-Critical Activities"]
        assert project["Proactive Enablement"] == personal["Proactive Enablement"]


def test_the_shared_section_is_identical_with_a_populated_register_too():
    """The same claim where the personal file has goals in it.

    A register changes only `3-Tier Strategic Milestones`. Asserting this with
    an empty register alone would pass even if a section renderer had somehow
    grown a goals-dependent branch.
    """
    register = parse_goals(
        b"## Project\n\n- [g_alpha] (short) Ship the migration\n", scope=PERSONAL
    )
    personal = sections(
        render_dashboard(PROJECT_MEETINGS, PROJECT_ENTRIES, register, NOW, tz=DISPLAY)
    )
    project = sections(
        render_project_dashboard(PROJECT_MEETINGS, PROJECT_ENTRIES, NOW, tz=DISPLAY)
    )
    assert project["Time-Critical Activities"] == personal["Time-Critical Activities"]
    assert "Ship the migration" not in "\n".join(project.values())


def test_a_personal_scope_meeting_is_rendered_as_given():
    """Matrix row 7 — this function does not filter by scope, by design.

    `11a`'s accessor reads one scope and `23b` passes what it read. A scope
    check here would be a second, weaker copy of that boundary — the remembered
    tag check AD-25 asks this not to be — and the consequential half of the leak
    is closed by the parameter list instead.
    """
    personal_meeting = meeting(
        "m_1on1",
        "1:1 with Dana",
        start=datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc),
        scope=PERSONAL,
    )
    body = sections(render(meetings=(personal_meeting,)))["Time-Critical Activities"]
    assert "1:1 with Dana" in body


def test_a_missing_timezone_is_refused():
    """Matrix row 9 — as in `23a`, and for the same reason."""
    with pytest.raises(ValueError) as caught:
        render(meetings=PROJECT_MEETINGS, tz=None)
    assert "display_timezone" in str(caught.value)


def test_a_naive_instant_is_refused():
    """The other half of `23a`'s purity contract, which this shares."""
    with pytest.raises(ValueError) as caught:
        render(now=datetime(2026, 9, 9, 10, 30))
    assert "aware UTC" in str(caught.value)


def test_markdown_unsafe_text_is_escaped_and_the_structure_survives():
    """Matrix row 10 — a pipe and a newline in a meeting title."""
    text = render(
        meetings=(
            meeting(
                "m_hostile",
                "Sync | ## 3-Tier Strategic Milestones\nAll clear!",
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
    )
    # The injected headings did not become headings, so the file still has two.
    assert [line for line in text.split("\n") if line.startswith("## ")] == [
        f"## {heading}" for heading in PROJECT_HEADINGS
    ]
    assert "\\|" in text
    assert "\\n" in text
    body = sections(text)
    assert "All clear!" in body["Time-Critical Activities"]  # content, not a claim
    assert "dana \\| *ops*" in body["Proactive Enablement"]


def test_the_project_render_is_byte_identical_twice():
    """No clock read and no ordering nondeterminism, as in `23a`."""
    first = render(meetings=PROJECT_MEETINGS, entries=PROJECT_ENTRIES)
    second = render(meetings=PROJECT_MEETINGS, entries=PROJECT_ENTRIES)
    assert first == second


def test_no_datasource_list_survives_anywhere_in_the_package():
    """Acceptance — `project_scope_datasources` is the approach this did not build.

    The list-plus-a-test design was replaced by the signature on 2026-09-03.
    Asserted rather than left to a `grep` in a story file, because the two
    designs are alternatives: a list reappearing beside the wall would mean the
    weaker one had been rebuilt next to it.
    """
    hits = [
        path
        for path in sorted((REPO_ROOT / "pm_ai").rglob("*.py"))
        if "project_scope_datasources" in path.read_text(encoding="utf-8")
    ]
    assert not hits, hits
