"""The positive control: every ordinary call to `render_project_dashboard`.

The other half of `mypy_fixture_project_render_goals.py`, and the half that
catches a different bug. A test that only asserts the register is *refused*
reads green against annotations so narrow that nothing type-checks at all —
`meetings: None`, say — and the wall would look intact while the function had
become uncallable. So the refusals are only evidence next to an acceptance.

`test_project_rendering.py::test_a_register_cannot_be_passed_under_mypy` runs
`mypy` over this path and asserts it exits **zero**. Nothing here may carry a
`# type: ignore`: a suppression would make this file pass without proving the
signature accepts anything.

Every call is one a real caller makes — `23b` reads the calendar and the project
event log and hands over what it read.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pm_ai.domain.event_entries import EventEntry
from pm_ai.domain.events import MessagePayload, ObservedEventType
from pm_ai.domain.handles import GRAPH, person_handle
from pm_ai.domain.harvest import HarvestFailure
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.payload_fields import encode_payload

from pm_ai.core.rendering import PmHandles, render_project_dashboard
from pm_ai.core.team_activity import NO_ISSUE_NOTES, IssueNotes
from pm_ai.domain.meeting_summaries import SummaryStateKind
from pm_ai.domain.renders import MeetingDigest, ProjectMeetingDigests
from pm_ai.domain.work import ProjectWork

NOW = datetime(2026, 9, 9, 10, 30, tzinfo=timezone.utc)
DISPLAY = timezone(timedelta(hours=2))
ALPHA = DataScope(ScopeKind.PROJECT, project_id="alpha")
NOBODY = PmHandles(handles=frozenset(), unrecorded=(), unchecked=())

MEETING = Meeting(
    meeting_id="m_alpha_standup",
    title="Alpha Standup",
    start=NOW,
    duration_minutes=30,
    attendees=(Actor("u_dana"),),
    scope=ALPHA,
)

ENTRY = EventEntry(
    category=ObservedEventType.MESSAGE_POSTED,
    actor="u_dana",
    # A valid line: its payload fields built by the writer's own encoder.
    fields=(("ingested_at", NOW.isoformat()),)
    + encode_payload(MessagePayload(channel="#alpha-build")),
    entry_id="evt_a",
)


def a_project_day() -> str:
    """The populated case — a sequence of meetings and a sequence of entries."""
    return render_project_dashboard((MEETING,), (ENTRY,), NOW, tz=DISPLAY, pm=NOBODY)


def an_empty_day() -> str:
    """Empty tuples, which is what an answered calendar with nothing in it is."""
    return render_project_dashboard((), (), NOW, tz=DISPLAY, pm=NOBODY)


def an_unreachable_calendar() -> str:
    """The other arm of the union — the whole reason `meetings` is not a list."""
    failure = HarvestFailure(reason="token expired", retryable=False)
    return render_project_dashboard(failure, (ENTRY,), NOW, tz=DISPLAY, pm=NOBODY)


def an_unreadable_event_log() -> str:
    """The other arm of `EventLogAnswer` (`23c`): a log that could not be read."""
    failure = HarvestFailure(reason="2026-09.md line 1: corrupt", retryable=False)
    return render_project_dashboard((MEETING,), failure, NOW, tz=DISPLAY, pm=NOBODY)


def a_pm_with_a_handle_and_an_unrecorded_sign_in() -> str:
    """The PM's handles and the sign-ins that predate the recorded user id."""
    pm = PmHandles(
        handles=frozenset({person_handle(GRAPH, "11111111-2222-4333-8444-555555555555")}),
        unrecorded=("graph:old",),
        unchecked=(),
    )
    return render_project_dashboard((), (ENTRY,), NOW, tz=DISPLAY, pm=pm)


def lists_not_tuples() -> str:
    """`Sequence`, not `tuple`: a caller that built a list must not be refused."""
    meetings: list[Meeting] = [MEETING]
    entries: list[EventEntry] = [ENTRY]
    return render_project_dashboard(meetings, entries, NOW, tz=DISPLAY, pm=NOBODY)


def an_optional_timezone(tz: timezone | None) -> str:
    """`tz` is `tzinfo | None` at the boundary and refused at run time.

    Typed optional deliberately (`23a`'s precedent): `Config().display_timezone`
    can be unset, and the refusal is a `ValueError` with a sentence in it rather
    than a type error a PM never sees. A caller holding an unresolved value must
    therefore be able to pass it.
    """
    return render_project_dashboard((), (), NOW, tz=tz, pm=NOBODY)


def a_project_with_its_gitlab_record_and_notes() -> str:
    """`23g`: `8t`'s record and `7e`'s slot, both keywords."""
    work = ProjectWork(scope=ALPHA, instance=None)
    notes = IssueNotes(rows={41: ("a row",)}, closing=("a closing line",))
    return render_project_dashboard(
        (MEETING,), (ENTRY,), NOW, tz=DISPLAY, pm=NOBODY, work=work, issue_notes=notes
    )


def an_unreadable_gitlab_record_and_the_default_slot() -> str:
    """A record that could not be read, and the empty slot spelled out."""
    failure = HarvestFailure(reason="malformed", retryable=False)
    return render_project_dashboard(
        (), (), NOW, tz=DISPLAY, pm=NOBODY, work=failure, issue_notes=NO_ISSUE_NOTES
    )


def a_project_with_its_own_meeting_digests() -> str:
    """`7d`: the project-only digests, a keyword after `issue_notes`."""
    digests = ProjectMeetingDigests(
        scope=ALPHA,
        shown=(
            MeetingDigest(
                scope=ALPHA,
                meeting_id="m_alpha_standup",
                start=NOW,
                title="Alpha Standup",
                recorded=NOW,
                state=SummaryStateKind.WAITING,
            ),
        ),
    )
    return render_project_dashboard(
        (MEETING,),
        (ENTRY,),
        NOW,
        tz=DISPLAY,
        pm=NOBODY,
        work=None,
        issue_notes=NO_ISSUE_NOTES,
        digests=digests,
    )
