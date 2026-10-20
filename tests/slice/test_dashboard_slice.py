"""Story 23b — `run_dashboard`'s I/O matrix, end to end against a real root.

A slice test rather than a unit one, because every row here is about something
only the composition root can observe: which files were opened, which were left
alone, and whether anything was written that a render is not allowed to write.
`tests/core/test_rendering_sections.py` already proves what the four sections
*say*; nothing there can prove that a project render never opened the PM's
goals file, because the renderer that could not be handed it is not the thing
doing the opening.

The calendar is a double throughout. `GraphConnector` is exercised against real
payload shapes in `tests/connectors`; what this file needs is control over the
four answers a calendar can give, and a fixture that reached a tenant would be
testing Microsoft rather than this pipeline.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pm_ai.app.pipelines import DASHBOARD_ARTIFACT, run_dashboard
from pm_ai.app.wiring import build
from pm_ai.core.config import Config, ConfigRefused
from pm_ai.core.goal_register import ARTIFACT as GOALS_ARTIFACT
from pm_ai.core.goal_register import MalformedGoals
from pm_ai.core.meeting_records import as_stored, record_name
from pm_ai.core.rendering import HEADINGS, NO_MEETINGS, PROJECT_HEADINGS
from pm_ai.domain.events import MessagePayload, NormalizedEvent, ObservedEventType
from pm_ai.domain.harvest import (
    Cursor,
    HarvestFailure,
    HarvestOutcome,
    HarvestResult,
)
from pm_ai.domain.health import Health, Probe
from pm_ai.domain.handles import person_handle
from pm_ai.domain.identity import Actor, DataScope, ScopeKind, SourceRef
from pm_ai.domain.meetings import Meeting
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import DecryptionFailed, KeyNotFound

NOW = datetime(2026, 9, 9, 10, 30, tzinfo=timezone.utc)
"""12:30 in Europe/Warsaw — mid-morning, so a day can hold both a meeting that
has ended and one that has not."""

ZONE = "Europe/Warsaw"
PERSONAL = DataScope(ScopeKind.PERSONAL)
ALPHA = DataScope(ScopeKind.PROJECT, project_id="alpha")

GOALS = b"""# Strategic Goals

## Project

- [g_latency] (short) Cut payment latency below 200ms

## Team

- [g_oncall] (medium) Halve the on-call pages per week

## Personal

- [g_staff] (long) Reach staff engineer
"""

DUPLICATE_GOALS = b"""# Strategic Goals

## Project

- [g_latency] (short) Cut payment latency below 200ms
- [g_latency] (short) Cut it again
"""


# ── Doubles ──────────────────────────────────────────────────────────────────


class _Calendar:
    """A connector that declares the calendar capability and answers as told.

    `emits()` is what `run_dashboard` selects on — a declared capability rather
    than a vendor string — so this class is also the proof that the selection
    reads a contract: it is not a `GraphConnector`, it is not named `graph`, and
    it is asked anyway.
    """

    system = "double"

    def __init__(
        self,
        instance: str,
        *,
        records: tuple[Meeting, ...] = (),
        live: tuple[Meeting, ...] = (),
        failure: HarvestFailure | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.name = instance
        self.instance = instance
        self._records = records
        self._live = live
        self._failure = failure
        self._raises = raises
        self.asked: list[Cursor] = []

    def emits(self) -> frozenset[ObservedEventType]:
        return frozenset({ObservedEventType.CALENDAR_EVENT_HELD})

    def harvest(self, since: Cursor) -> HarvestResult:
        self.asked.append(since)
        if self._raises is not None:
            raise self._raises
        if self._failure is not None:
            outcome = HarvestOutcome.FAILED
        elif self._records or self._live:
            outcome = HarvestOutcome.HARVESTED
        else:
            outcome = HarvestOutcome.EMPTY
        return HarvestResult(
            events=(),
            cursor=since,
            outcome=outcome,
            failure=self._failure,
            records=self._records,
            live=self._live,
            # A calendar that answered was read at some instant, and EMPTY is
            # refused without one (`8p`). The failing double never answered.
            answered_at=None if self._failure is not None else NOW,
        )

    def sample_events(self) -> tuple[NormalizedEvent, ...]:
        return ()

    def check_health(self) -> Probe:
        return Probe(self.instance, Health.OK, "a double always answers")


class _NoCalendar(_Calendar):
    """Enrolled, and declaring something else entirely.

    The negative half of the capability selection: a connector in the daemon's
    dict that is never asked, because it never said it could answer.
    """

    system = "vcs-double"

    def emits(self) -> frozenset[ObservedEventType]:
        return frozenset({ObservedEventType.COMMIT_PUSHED})


class _NoKey:
    """A keychain with nothing in it — a machine where `key enrol` never ran."""

    def get(self, name: str) -> bytes:
        raise KeyNotFound(f"nothing is stored under {name!r}")

    def set(self, name: str, secret: bytes) -> None:
        raise AssertionError("a dashboard render must never enrol a key")

    def delete(self, name: str) -> None:
        raise AssertionError("a dashboard render must never delete a key")


# ── Fixtures ─────────────────────────────────────────────────────────────────


def meeting(
    meeting_id: str,
    title: str,
    *,
    start: datetime = datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc),
    minutes: int = 30,
    scope: DataScope = PERSONAL,
    ical_uid: str | None = None,
    tentative: bool = False,
) -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title=title,
        start=start,
        duration_minutes=minutes,
        attendees=(Actor("u_andrei"), Actor("u_dana")),
        scope=scope,
        ical_uid=ical_uid,
        tentative=tentative,
    )


def daemon(tmp_path: Path, *calendars: object, zone: str = ZONE):
    """A daemon over `tmp_path` whose connector dict is exactly what is passed.

    The built-in `gitlab:alpha` adapter is dropped rather than left beside the
    doubles, so a test that passes no calendar is genuinely a machine with none
    — which is the row it exists to assert.
    """
    built = build(
        tmp_path,
        "alpha",
        now=lambda: NOW,
        keychain=_NoKey(),
        config=Config(display_timezone=zone),
    )
    built.connectors = {
        getattr(calendar, "instance"): calendar  # type: ignore[misc]
        for calendar in calendars
    }
    return built


def written(built, *, scope: DataScope = PERSONAL) -> str:
    raw = built.storage.read_artifact(scope=scope, artifact=DASHBOARD_ARTIFACT)
    assert raw is not None, "no dashboard was written"
    return raw.decode("utf-8")


def sections(text: str) -> dict[str, str]:
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


def author_goals(built, raw: bytes = GOALS) -> None:
    built.storage.write_artifact(raw, scope=PERSONAL, artifact=GOALS_ARTIFACT)


# ── Happy path ───────────────────────────────────────────────────────────────


def test_a_day_with_meetings_and_goals_is_written_where_cap_9_says(tmp_path):
    """Acceptance — the file exists, has four headings, meetings in start order."""
    calendar = _Calendar(
        "graph:contoso",
        live=(
            meeting("m_late", "Payments Gateway Sync", start=_at(13, 0)),
            meeting("m_early", "Standup", start=_at(11, 0)),
        ),
    )
    built = daemon(tmp_path, calendar)
    author_goals(built)

    path = run_dashboard(built, now=NOW)

    assert path.exists()
    assert path.name == DASHBOARD_ARTIFACT
    # The declared location, beneath the temporary root rather than beneath $HOME.
    assert path.parent.name == "memory"
    assert ".manager-ai" in str(path)

    body = sections(path.read_text(encoding="utf-8"))
    assert list(body) == list(HEADINGS)
    assert all(body[heading] for heading in HEADINGS)
    schedule = body["Time-Critical Activities"].split("\n")
    assert "Standup" in schedule[0]
    assert "Payments Gateway Sync" in schedule[1]
    assert "Reach staff engineer" in body["3-Tier Strategic Milestones"]


def test_a_meeting_that_already_ended_today_is_still_on_the_page(tmp_path):
    """`records` as well as `live`, because the day is the whole day.

    A dashboard read at 12:30 that showed only what had not started yet would
    report a morning of finished meetings as an empty calendar — the exact claim
    `NO_MEETINGS` is forbidden from making.
    """
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:contoso",
            records=(meeting("m_done", "Morning Review", start=_at(8, 0)),),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    # `23a` collapses a fully-finished day into one line rather than listing it,
    # which is the branch under test: it is reachable only because `records`
    # reaches the renderer, and it is emphatically not `NO_MEETINGS`.
    assert "Today's one meeting has ended" in body
    assert NO_MEETINGS not in body


def test_a_first_run_writes_a_valid_file_with_every_section_stated(tmp_path):
    """Acceptance — nothing harvested, no goals file, and still a dashboard."""
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    body = sections(written(_run(built)))
    assert list(body) == list(HEADINGS)
    assert all(body[heading] for heading in HEADINGS)
    assert NO_MEETINGS in body["Time-Critical Activities"]
    assert GOALS_ARTIFACT in body["3-Tier Strategic Milestones"]


def test_the_write_succeeds_with_no_master_key_enrolled(tmp_path):
    """Acceptance — `daily_dashboard.md` is unencrypted and must not need the enclave.

    The keychain double raises `KeyNotFound` for every read, so a render that
    had grown a dependency on a sealed artifact would fail here rather than
    quietly on a first-time user's machine.
    """
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    author_goals(built)
    assert run_dashboard(built, now=NOW).exists()


# ── Determinism ──────────────────────────────────────────────────────────────


def test_a_re_run_with_unchanged_inputs_is_byte_identical(tmp_path):
    built = daemon(
        tmp_path,
        _Calendar("graph:contoso", live=(meeting("m_1", "Standup"),)),
    )
    author_goals(built)
    # The first render records its instant, which is the next render's input
    # (story 9b: the look-back starts there), so unchanged inputs are the
    # second and third renders. The first render after another differs from it
    # by design: its Proactive Enablement window line names that render.
    run_dashboard(built, now=NOW)
    first = run_dashboard(built, now=NOW).read_bytes()
    second = run_dashboard(built, now=NOW).read_bytes()
    assert first == second


def test_a_re_run_with_two_connectors_is_byte_identical(tmp_path):
    """Sorted instance order decides which copy of a matched pair survives."""
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:zebra",
            live=(meeting("m_z", "Weekly Sync", ical_uid="uid-1", tentative=True),),
        ),
        _Calendar(
            "graph:apex",
            live=(meeting("m_a", "Weekly Sync", ical_uid="uid-1"),),
        ),
    )
    # The first render records its instant, which is the next render's input
    # (story 9b: the look-back starts there), so unchanged inputs are the
    # second and third renders. The first render after another differs from it
    # by design: its Proactive Enablement window line names that render.
    run_dashboard(built, now=NOW)
    first = run_dashboard(built, now=NOW).read_bytes()
    second = run_dashboard(built, now=NOW).read_bytes()
    assert first == second
    # `graph:apex` sorts first, so its copy — the one with no tentative flag — is
    # the one kept. Asserted rather than left implicit, because "byte-identical"
    # would also hold if the order were reversed consistently.
    assert b"your response is tentative" not in first


# ── A render changes nothing ─────────────────────────────────────────────────


def test_a_render_saves_no_cursor_writes_no_record_and_appends_no_entry(
    tmp_path, monkeypatch
):
    """Acceptance — asserted against the writer, not inferred from a call site.

    A render projects truth rather than changing it. The three writes forbidden
    here are exactly the three `run_harvest` performs, which is what "no harvest
    is triggered from here" means operationally.
    """
    built = daemon(
        tmp_path,
        _Calendar("graph:contoso", records=(meeting("m_done", "Review", start=_at(8, 0)),)),
    )
    author_goals(built)
    before = _log_bytes(built, PERSONAL)

    def refuse(*args, **kwargs):
        raise AssertionError("a render must not write here")

    monkeypatch.setattr(type(built.storage), "save_cursor", refuse)
    monkeypatch.setattr(type(built.storage), "append_event_log", refuse)
    monkeypatch.setattr(type(built.storage), "persist_events", refuse)
    monkeypatch.setattr(type(built.meetings), "put", refuse)

    run_dashboard(built, now=NOW)

    assert _log_bytes(built, PERSONAL) == before
    assert built.storage.list_collection(scope=PERSONAL, artifact="meetings/") == ()


def test_the_calendar_is_read_from_a_fresh_cursor_so_none_can_advance(tmp_path):
    """The read is a read. Nothing is resumed from and nothing is written back."""
    calendar = _Calendar("graph:contoso")
    run_dashboard(daemon(tmp_path, calendar), now=NOW)
    assert calendar.asked == [Cursor()]


# ── Refusals, and the file that survives them ────────────────────────────────


def test_a_malformed_goals_file_refuses_and_leaves_yesterdays_file_intact(tmp_path):
    """Acceptance — a failed render must not destroy yesterday's dashboard.

    `write_artifact` replaces whole, so the ordering is the guarantee: read and
    render fully, then write. A pipeline that opened the target first would pass
    every other row here and fail this one.
    """
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    author_goals(built)
    yesterday = run_dashboard(built, now=NOW).read_bytes()

    author_goals(built, DUPLICATE_GOALS)
    with pytest.raises(MalformedGoals, match="g_latency"):
        run_dashboard(built, now=NOW)

    assert written(built).encode("utf-8") == yesterday


def _unreadable_log_section(built, *, scope: DataScope = PERSONAL) -> dict[str, str]:
    """Render, assert the file was written, and return its sections."""
    path = run_dashboard(built, scope=scope, now=NOW)  # returns: the command exits 0
    return sections(path.read_text(encoding="utf-8"))


def _assert_the_log_failure_is_stated(body: dict[str, str], *, segment: str) -> None:
    """The failure sentence names the segment; the three absences hold."""
    section = body["Proactive Enablement"]
    assert "The event log could not be read" in section
    assert segment in section
    assert "event log between" not in section
    assert "the connector reports" not in section.casefold()
    assert "sign-in" not in section and "predates" not in section


def test_a_malformed_event_log_segment_is_stated_and_the_file_is_written(tmp_path):
    """Acceptance 2 (`23c`, superseding `23b`'s "malformed segment → refused").

    A hand-edited or corrupt segment, written straight to disk: the command
    exits zero, the new file names the segment and the reason, and the other
    three sections render from their own inputs. A Graph row lacking `user_id`
    is enrolled too — the failure sentence is all the section says.
    """
    built = daemon(
        tmp_path,
        _Calendar("graph:contoso", live=(meeting("m_sync", "Payments Sync"),)),
    )
    author_goals(built)
    _save_row(built, "graph:work", _graph_row("graph:work", None))
    yesterday = run_dashboard(built, now=NOW).read_bytes()

    segments = built.storage.paths.resolve(PERSONAL, "event_log/", create=True)
    (segments / "2026-09.md").write_bytes(b"this line is not a ledger entry\n")
    body = _unreadable_log_section(built)

    assert written(built).encode("utf-8") != yesterday
    _assert_the_log_failure_is_stated(body, segment="2026-09.md")
    assert "record marker" in body["Proactive Enablement"]  # the ledger's reason
    assert "Payments Sync" in body["Time-Critical Activities"]
    assert "Reach staff engineer" in body["3-Tier Strategic Milestones"]
    assert "No synthesis runs" in body["Leadership Notes"]
    # Saved nowhere: the log is exactly the corrupt line it was.
    assert (segments / "2026-09.md").read_bytes() == b"this line is not a ledger entry\n"


def test_an_entry_of_an_unknown_kind_is_stated(tmp_path):
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    _persist(built, _message("m1"))
    segment = built.storage.paths.resolve(PERSONAL, "event_log/") / "2026-09.md"
    line = segment.read_text(encoding="utf-8")
    segment.write_text(line.replace("message_posted", "carrier_pigeon", 1), encoding="utf-8")

    _assert_the_log_failure_is_stated(
        _unreadable_log_section(built), segment="2026-09.md"
    )


def test_a_segment_that_is_not_utf8_is_stated_naming_it(tmp_path):
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    segments = built.storage.paths.resolve(PERSONAL, "event_log/", create=True)
    (segments / "2026-09.md").write_bytes(b"- [evt_\xff] message_posted\n")

    body = _unreadable_log_section(built)
    _assert_the_log_failure_is_stated(body, segment="2026-09.md")
    assert (
        "2026-09.md in personal's event log is not valid UTF-8 text"
        in body["Proactive Enablement"]
    )


def test_a_segment_listed_then_missing_is_stated(tmp_path, monkeypatch):
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    monkeypatch.setattr(
        built.storage, "event_log_segments", lambda *, scope: ("2026-08.md",)
    )
    _assert_the_log_failure_is_stated(
        _unreadable_log_section(built), segment="2026-08.md"
    )


def _segment_read_raises(built, monkeypatch, error: Exception) -> None:
    """A stand-in for the segment read that raises — no file mode is touched,
    so the outcome does not depend on who runs the suite."""

    def refuse(*, scope, name):
        raise error

    monkeypatch.setattr(built.storage, "read_event_log_segment", refuse)


def test_a_segment_that_cannot_be_read_from_disk_is_stated(tmp_path, monkeypatch):
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    _persist(built, _message("m1"))
    _segment_read_raises(
        built,
        monkeypatch,
        PermissionError(13, "Permission denied", "/root/event_log/2026-09.md"),
    )
    body = _unreadable_log_section(built)
    _assert_the_log_failure_is_stated(body, segment="2026-09.md")
    assert "Permission denied" in body["Proactive Enablement"]


def test_a_segment_that_cannot_be_decrypted_is_stated(tmp_path, monkeypatch):
    """`DecryptionFailed` is a segment that could not be read: stated, never a
    stopped render. Raised through the storage service's own wrapper, which
    names the segment and the log."""
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    _persist(built, _message("m1"))

    original = built.storage.read_artifact

    def unopenable(**kwargs):
        if kwargs.get("artifact") == "event_log/":
            raise DecryptionFailed("the tag did not verify")
        return original(**kwargs)

    monkeypatch.setattr(built.storage, "read_artifact", unopenable)
    body = _unreadable_log_section(built)
    _assert_the_log_failure_is_stated(body, segment="2026-09.md")
    section = body["Proactive Enablement"]
    assert "could not be decrypted" in section
    assert "personal" in section and "event log" in section


def test_a_message_line_hand_edited_to_drop_its_channel_is_counted(tmp_path):
    """Story 2m's line that parses and does not decode, now counted in the
    footnote (`23c`) rather than refusing the render; the others render."""
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    _persist(built, _message("m1", excerpt="hello"), _message("m2", excerpt="still here"))
    segment = built.storage.paths.resolve(PERSONAL, "event_log/") / "2026-09.md"
    lines = segment.read_text(encoding="utf-8").split("\n")
    target = next(i for i, line in enumerate(lines) if "p.excerpt=hello" in line)
    lines[target] = lines[target].replace(" p.channel=#payments", "")
    segment.write_text("\n".join(lines), encoding="utf-8")

    section = sections(run_dashboard(built, now=NOW).read_text(encoding="utf-8"))[
        "Proactive Enablement"
    ]
    assert "still here" in section
    assert "hello" not in section
    assert "1 message log entry carries fields that could not be decoded" in section
    # Named well enough to find by hand: the entry id and the segment.
    entry_id = lines[target][3 : lines[target].index("]")]
    assert f"(`{entry_id}` in `2026-09.md`)" in section


def test_an_unset_display_timezone_is_refused_before_anything_is_read(tmp_path):
    """The day boundary may not be assumed — the silent wrong answer `4g` exists
    to prevent."""
    calendar = _Calendar("graph:contoso")
    built = daemon(tmp_path, calendar, zone="")
    with pytest.raises(ConfigRefused, match="display_timezone"):
        run_dashboard(built, now=NOW)
    assert calendar.asked == []
    assert built.storage.read_artifact(scope=PERSONAL, artifact=DASHBOARD_ARTIFACT) is None


@pytest.mark.parametrize(
    "scope",
    [DataScope(ScopeKind.PEOPLE, person_id="bob"), DataScope(ScopeKind.APPLICATION)],
)
def test_a_scope_whose_tree_declares_no_dashboard_is_refused_by_name(tmp_path, scope):
    calendar = _Calendar("graph:contoso")
    from pm_ai.domain.scope_model import ScopeResolutionError

    with pytest.raises(ScopeResolutionError, match=DASHBOARD_ARTIFACT):
        run_dashboard(daemon(tmp_path, calendar), scope=scope, now=NOW)
    assert calendar.asked == []


def test_a_directory_where_the_file_is_declared_propagates(tmp_path):
    """Matrix — exit `1`, which is a bug's code and not a refusal's.

    Deliberately not caught and re-dressed: pm-ai did not decline this, and
    printing a tidy sentence over it would teach an operator to read a broken
    filesystem as a policy decision.
    """
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    target = built.storage.paths.resolve(PERSONAL, DASHBOARD_ARTIFACT, create=True)
    target.mkdir()
    with pytest.raises(IsADirectoryError) as refused:
        run_dashboard(built, now=NOW)
    # The row asks for the path *and* the expected node type. `os.replace`
    # reports `[Errno 21] Is a directory` and neither, which is a sentence an
    # operator cannot act on: it names no file and no expectation.
    said = str(refused.value)
    assert str(target) in said
    assert "declared a File" in said
    assert DASHBOARD_ARTIFACT in said


def test_a_non_empty_directory_in_the_way_is_named_the_same_way(tmp_path):
    """The other errno. An empty directory raises `IsADirectoryError` and a
    populated one `ENOTEMPTY`, which is why the node type rather than the errno
    is what decides the sentence."""
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    target = built.storage.paths.resolve(PERSONAL, DASHBOARD_ARTIFACT, create=True)
    target.mkdir()
    (target / "a-file-somebody-put-here").write_text("x")
    with pytest.raises(IsADirectoryError, match="declared a File"):
        run_dashboard(built, now=NOW)


def test_a_write_that_fails_for_any_other_reason_is_left_alone(tmp_path, monkeypatch):
    """Only the directory case is re-dressed. A full disk is not pm-ai declining,
    and a tidy sentence over it would read as a policy decision."""
    built = daemon(tmp_path, _Calendar("graph:contoso"))

    def full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(type(built.storage), "write_artifact", full)
    with pytest.raises(OSError, match="No space left on device"):
        run_dashboard(built, now=NOW)


# ── The calendar's four answers ──────────────────────────────────────────────


def test_no_connector_declaring_the_capability_is_its_own_answer(tmp_path):
    """Acceptance — exit zero, the section says nobody can answer, and the page
    makes no claim about a connector.

    Asserted on the rendered string, because the whole point of the third state
    is that it attributes nothing to a connector that was never enrolled.
    """
    built = daemon(tmp_path, _NoCalendar("gitlab:alpha"))
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert "No calendar is enrolled" in body
    assert "the connector reports" not in body.casefold()
    assert NO_MEETINGS not in body


def test_an_enrolled_connector_that_cannot_answer_is_never_asked(tmp_path):
    """The selection reads `emits()`, so a non-calendar connector is not consulted."""
    other = _NoCalendar("gitlab:alpha")
    run_dashboard(daemon(tmp_path, other, _Calendar("graph:contoso")), now=NOW)
    assert other.asked == []


def test_an_unreachable_calendar_still_writes_the_file_and_says_so(tmp_path):
    """Matrix — the failure reaches the renderer *as the meetings argument*, and
    the command succeeds. A day whose calendar could not be read is a dashboard
    that says so, not a failed command."""
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:contoso",
            failure=HarvestFailure(reason="the tenant refused the token", retryable=False),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert "could not be read" in body
    assert "the tenant refused the token" in body
    assert NO_MEETINGS not in body


def test_a_connector_that_raises_costs_its_own_rows_and_nothing_else(tmp_path):
    """The port says a harvest reports rather than raises; this is the guard behind it.

    An exception escaping the loop would take down the calendar beside it, one
    line before the meetings that did arrive were rendered.
    """
    built = daemon(
        tmp_path,
        _Calendar("graph:apex", live=(meeting("m_a", "Standup"),)),
        _Calendar("graph:zebra", raises=RuntimeError("a bug in pm-ai")),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert "Standup" in body
    assert "graph:zebra" in body
    assert "RuntimeError" in body


def test_two_enrolled_calendars_are_merged_rather_than_refused(tmp_path):
    """Acceptance — two tenants is a configuration, not a fault."""
    built = daemon(
        tmp_path,
        _Calendar("graph:apex", live=(meeting("m_a", "Alpha Sync", start=_at(11, 0)),)),
        _Calendar("graph:zebra", live=(meeting("m_z", "Zebra Sync", start=_at(13, 0)),)),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert "Alpha Sync" in body
    assert "Zebra Sync" in body


def test_one_meeting_cross_invited_to_two_tenants_is_listed_once(tmp_path):
    """Acceptance — matched on `ical_uid`, because `meeting_id` is per-mailbox.

    The two ids are deliberately different: matching on what the code already
    had would have deduplicated nothing in the only case that motivated it.
    """
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:apex",
            live=(meeting("AAMkA-apex", "Vendor Review", ical_uid="040000008200E"),),
        ),
        _Calendar(
            "graph:zebra",
            live=(meeting("AAMkA-zebra", "Vendor Review", ical_uid="040000008200E"),),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert body.count("Vendor Review") == 1


def test_the_same_meeting_id_from_two_tenants_is_also_listed_once(tmp_path):
    """The fallback, for a row that carried no `iCalUId` at all."""
    built = daemon(
        tmp_path,
        _Calendar("graph:apex", live=(meeting("m_same", "Vendor Review"),)),
        _Calendar("graph:zebra", live=(meeting("m_same", "Vendor Review"),)),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert body.count("Vendor Review") == 1


def test_two_occurrences_of_one_series_today_are_both_listed(tmp_path):
    """Matrix — the start is in the key, so it does not matter whether Graph
    reuses one `iCalUId` across a series.

    Unmeasured is why it is in the key: the 2026-09-06 spike listed the field and
    never compared two occurrences. Two tenants' copies of one meeting share the
    instant; two occurrences of a series do not.
    """
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:contoso",
            live=(
                meeting("m_occ_1", "Daily Sync", start=_at(9, 0), ical_uid="series-1"),
                meeting("m_occ_2", "Daily Sync", start=_at(15, 0), ical_uid="series-1"),
            ),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert body.count("Daily Sync") == 2


def test_two_meetings_with_one_id_and_two_uids_are_both_listed(tmp_path):
    """Matrix — a uid that disagrees is evidence of difference, not a tie for the
    id to break.

    The id fallback exists for rows that carry no uid at all. Applying it to rows
    that do collapses two meetings the provider distinguished.
    """
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:apex",
            live=(meeting("m_same", "Vendor Review", ical_uid="uid-a"),),
        ),
        _Calendar(
            "graph:zebra",
            live=(meeting("m_same", "Vendor Review", ical_uid="uid-z"),),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert body.count("Vendor Review") == 2


def test_two_meetings_sharing_a_start_and_a_title_are_both_listed(tmp_path):
    """Acceptance — resemblance is not identity.

    Two organisations each holding a "Weekly Sync" at 09:00 is a genuine double
    booking, and it is the single most actionable fact on the page. A dashboard
    that merged it away would suppress exactly what the 07:00 reader needs.
    """
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:apex",
            live=(meeting("m_a", "Weekly Sync", start=_at(11, 0), ical_uid="uid-a"),),
        ),
        _Calendar(
            "graph:zebra",
            live=(meeting("m_z", "Weekly Sync", start=_at(11, 0), ical_uid="uid-z"),),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert body.count("Weekly Sync") == 2


def test_one_calendar_answering_and_one_failing_renders_both_facts(tmp_path):
    """Acceptance — the meetings are listed, the failing instance is named, exit zero."""
    built = daemon(
        tmp_path,
        _Calendar("graph:apex", live=(meeting("m_a", "Alpha Sync"),)),
        _Calendar(
            "graph:zebra",
            failure=HarvestFailure(reason="graph:zebra was throttled", retryable=True),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert "Alpha Sync" in body
    assert "graph:zebra" in body
    assert "throttled" in body
    assert NO_MEETINGS not in body


def test_one_calendar_answering_nothing_and_another_failing_is_still_partial(tmp_path):
    """Matrix — a calendar that answered and held nothing is not a calendar that
    could not be read.

    The `HarvestFailure` branch would say today's meetings are unknown, which is
    false: one calendar measured the day. The branch is therefore on whether any
    connector *answered*, never on whether the merged list came back non-empty.
    """
    built = daemon(
        tmp_path,
        _Calendar("graph:apex"),  # answered, and the day held nothing
        _Calendar(
            "graph:zebra",
            failure=HarvestFailure(reason="graph:zebra lost its token", retryable=False),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert "The calendars that answered held nothing" in body
    assert "graph:zebra" in body
    # Never the whole-day claim, and never the unknown-day one either.
    assert NO_MEETINGS not in body
    assert "The calendar could not be read" not in body


def test_a_partly_walked_harvest_keeps_its_rows_and_is_still_named_unread(tmp_path):
    """One connector, one `HarvestFailure`, and meetings anyway.

    A page-one-succeeded, page-two-failed walk has real meetings, and discarding
    them would mean a connector that fails on its last span shows nothing all
    morning. It is still named, because the day it handed over is not the whole
    day.
    """
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:contoso",
            live=(meeting("m_a", "Payments Gateway Sync"),),
            failure=HarvestFailure(
                reason="graph:contoso could not finish the second span",
                retryable=True,
            ),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert "Payments Gateway Sync" in body
    assert "graph:contoso" in body
    assert "could not finish the second span" in body
    assert "The calendar could not be read" not in body


def test_every_calendar_failing_is_the_failure_branch_and_names_both(tmp_path):
    """Matrix — nothing was read, so the section says so rather than showing an
    empty day. Both reasons survive, because each names its own instance."""
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:apex",
            failure=HarvestFailure(reason="graph:apex lost its token", retryable=False),
        ),
        _Calendar(
            "graph:zebra",
            failure=HarvestFailure(reason="graph:zebra was throttled", retryable=True),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert "The calendar could not be read" in body
    assert "graph:apex lost its token" in body
    assert "graph:zebra was throttled" in body
    # `retryable` is the conjunction: telling an operator to wait is honest only
    # if waiting clears all of them, and one of these never will.
    assert "will not clear on its own" in body


# ── Which day, and whose ─────────────────────────────────────────────────────


def test_a_meeting_on_another_day_is_not_on_this_dashboard(tmp_path):
    """The display zone selects the day, so the boundary shown and the boundary
    selected by cannot disagree."""
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:contoso",
            live=(
                meeting("m_today", "Today", start=_at(13, 0)),
                meeting(
                    "m_tomorrow",
                    "Tomorrow",
                    start=_at(13, 0) + timedelta(days=1),
                ),
            ),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert "Today" in body
    assert "Tomorrow" not in body


def test_the_day_boundary_is_the_display_zones_and_not_utcs(tmp_path):
    """23:30 in Warsaw on the 9th is 21:30Z, and 01:00 Warsaw on the 10th is not
    today — a distinction UTC cannot draw."""
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:contoso",
            live=(
                meeting("m_late", "Late Call", start=_at(21, 30)),
                meeting("m_next", "Next Morning", start=_at(23, 30)),
            ),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert "Late Call" in body
    assert "Next Morning" not in body


DAY_OPENS = datetime(2026, 9, 8, 22, 0, tzinfo=timezone.utc)
"""Midnight on the display day in Europe/Warsaw, as a UTC instant.

Where an all-day row actually starts: Graph sends one anchored to local
midnight, which is also the one instant a zero-length span cannot overlap the
day with — `start` and `end` are both exactly the boundary.
"""


def test_an_all_day_marker_at_local_midnight_is_placed_by_its_start(tmp_path):
    """`33c` records an all-day row as zero minutes, so it has no span to overlap
    the day with and has to be placed by its start instead.

    At local midnight — where an all-day row really lands — the overlap test
    fails on both sides: the marker ends exactly when the day opens. Dropping it
    is silent in the worst way, because the section then reports a day with an
    OOO block across the whole of it as a day the calendar answered nothing for.

    A second, upcoming meeting is present so the section renders as a list: a day
    whose only entry has ended collapses to one summary line (`23a`), and this
    row is about placement rather than about that branch.
    """
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:contoso",
            live=(
                meeting("m_ooo", "Andrei OOO", start=DAY_OPENS, minutes=0),
                meeting("m_later", "Payments Gateway Sync", start=_at(13, 0)),
            ),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert "Andrei OOO" in body
    assert "Payments Gateway Sync" in body


def test_a_zero_length_marker_outside_the_day_is_still_excluded(tmp_path):
    """The other half, so the branch is a placement and not an "always keep".

    Yesterday's midnight marker and tomorrow's both fail it, from opposite
    directions — the first because it ends before the day opens, the second
    because it starts after the day closes.
    """
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:contoso",
            live=(
                meeting(
                    "m_yesterday",
                    "Yesterday OOO",
                    start=DAY_OPENS - timedelta(days=1),
                    minutes=0,
                ),
                meeting(
                    "m_tomorrow",
                    "Tomorrow OOO",
                    start=DAY_OPENS + timedelta(days=1),
                    minutes=0,
                ),
            ),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert "Yesterday OOO" not in body
    assert "Tomorrow OOO" not in body
    assert NO_MEETINGS in body


def test_a_meeting_that_began_yesterday_and_is_still_running_is_listed(tmp_path):
    """The renderer decides nothing about which day it renders, so dropping this
    here would be the pipeline silently deciding the PM is not in it."""
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:contoso",
            live=(
                meeting(
                    "m_overnight",
                    "Overnight Cutover",
                    start=_at(10, 0) - timedelta(hours=14),
                    minutes=24 * 60,
                ),
            ),
        ),
    )
    assert "Overnight Cutover" in sections(written(_run(built)))["Time-Critical Activities"]


# ── AD-25 — the project branch opens nothing of the PM's ─────────────────────


def test_a_project_render_reads_no_path_beneath_the_personal_root(tmp_path, monkeypatch):
    """Acceptance — asserted by instrumenting the reader.

    `23d`'s AD-25 test checks what the *renderer declares*; a signature cannot
    say what `run_dashboard` actually opens. So every scope the resolver is
    asked for is recorded, and the personal tree must not be among them — even
    though this root's personal tree holds both a goals file and a dashboard.
    """
    built = daemon(
        tmp_path,
        _Calendar("graph:contoso", live=(meeting("m_a", "Alpha Sync", scope=ALPHA),)),
    )
    author_goals(built)
    run_dashboard(built, now=NOW)  # a personal dashboard, so the tree is populated

    asked: list[DataScope] = []
    for method in ("resolve", "scope_root"):
        original = getattr(ScopePaths, method)

        def record(self, scope, *args, _original=original, **kwargs):
            asked.append(scope)
            return _original(self, scope, *args, **kwargs)

        monkeypatch.setattr(ScopePaths, method, record)

    run_dashboard(built, scope=ALPHA, now=NOW)

    assert asked, "the resolver was never consulted, so this proves nothing"
    assert not [scope for scope in asked if scope.kind is ScopeKind.PERSONAL]


def test_a_project_render_writes_the_projects_own_file_with_its_own_sections(tmp_path):
    built = daemon(
        tmp_path,
        _Calendar("graph:contoso", live=(meeting("m_a", "Alpha Sync", scope=ALPHA),)),
    )
    path = run_dashboard(built, scope=ALPHA, now=NOW)
    assert ".project-ai" in str(path)
    body = sections(path.read_text(encoding="utf-8"))
    assert list(body) == list(PROJECT_HEADINGS)
    assert "Alpha Sync" in body["Time-Critical Activities"]


def test_a_personal_meeting_never_reaches_the_project_dashboard(tmp_path):
    """AD-38 — no record written to the project scope may reference personal
    material, and the PM's 1:1 in the team's file is exactly that."""
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:contoso",
            live=(
                meeting("m_a", "Alpha Sync", scope=ALPHA),
                meeting("m_p", "1:1 with Dana", scope=PERSONAL),
            ),
        ),
    )
    body = sections(
        run_dashboard(built, scope=ALPHA, now=NOW).read_text(encoding="utf-8")
    )["Time-Critical Activities"]
    assert "Alpha Sync" in body
    assert "1:1 with Dana" not in body


def test_the_personal_dashboard_carries_the_pms_whole_day(tmp_path):
    """The wall is one-directional, because the leak is.

    A PM whose meetings are all project-categorised would otherwise read an empty
    personal dashboard every morning — the failure mode the honesty rules exist
    to prevent, arrived at from the other side.
    """
    built = daemon(
        tmp_path,
        _Calendar(
            "graph:contoso",
            live=(
                meeting("m_a", "Alpha Sync", scope=ALPHA),
                meeting("m_p", "1:1 with Dana", scope=PERSONAL),
            ),
        ),
    )
    body = sections(written(_run(built)))["Time-Critical Activities"]
    assert "Alpha Sync" in body
    assert "1:1 with Dana" in body


# ── `ical_uid` reaches no file ───────────────────────────────────────────────


def test_ical_uid_is_absent_from_a_record_written_and_read_back(tmp_path):
    """Acceptance — asserted on the bytes, exactly as `tentative` is.

    The field rides in memory so two copies of one meeting can be matched during
    a single read of the day. No record read back off disk is ever asked that
    question, and a persisted grammar that grew a key would have changed every
    golden in `tests/core`.
    """
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    held = meeting(
        "m_held",
        "Vendor Review",
        start=_at(8, 0),
        ical_uid="040000008200E00074C5B7101A82E008",
    )
    built.meetings.put(held)

    name = record_name(held.meeting_id, held.start)
    raw = built.storage.read_artifact(scope=PERSONAL, artifact="meetings/", name=name)
    assert raw is not None
    assert b"ical_uid" not in raw
    assert b"040000008200E00074C5B7101A82E008" not in raw

    read_back = built.meetings.get(held.meeting_id, scope=PERSONAL)
    assert read_back.meeting.ical_uid is None
    assert read_back.meeting == as_stored(held)


def test_the_record_grammar_declares_no_key_for_it():
    """The structural half: "stored nowhere" is a property of `_FIELDS`, not of
    every caller remembering."""
    from pm_ai.core import meeting_records

    assert "ical_uid" not in meeting_records._FIELDS


# ── Helpers ──────────────────────────────────────────────────────────────────


def _at(hour: int, minute: int) -> datetime:
    return NOW.replace(hour=hour, minute=minute)


def _run(built):
    run_dashboard(built, now=NOW)
    return built


def _log_bytes(built, scope: DataScope) -> tuple[bytes, ...]:
    return tuple(
        built.storage.read_event_log_segment(scope=scope, name=name).encode("utf-8")
        for name in built.storage.event_log_segments(scope=scope)
    )



# ── Story 23c — mentions from the saved connector rows, and scope isolation ──

PM_ID = "0d9e3c4f-1a2b-4c3d-8e9f-a0b1c2d3e4f5"
OTHER_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
APPLICATION = DataScope(ScopeKind.APPLICATION)


def _graph_row(instance: str, user_id: object, *, enabled: bool = True) -> dict:
    row: dict[str, object] = {"instance": instance, "system": "graph", "enabled": enabled}
    if user_id is not None:
        row["user_id"] = user_id
    return row


def _save_row(built, instance: str, row: dict) -> None:
    """A `connectors/<instance>.json` row, through the single writer."""
    built.storage.write_artifact(
        json.dumps(row).encode("utf-8"),
        scope=APPLICATION,
        artifact="connectors/",
        name=f"{instance}.json",
    )


def _message(
    native: str,
    *,
    scope: DataScope = PERSONAL,
    excerpt: str | None = None,
    mentions: tuple[str, ...] | None = None,
    sender: str = OTHER_ID,
    occurred_at: datetime = NOW - timedelta(minutes=5),
) -> NormalizedEvent:
    """A message as `33d` writes one: a Graph sender handle, channel, excerpt.

    `occurred_at` is the posted time each line is labelled with (`23f`)."""
    return NormalizedEvent(
        scope=scope,
        type=ObservedEventType.MESSAGE_POSTED,
        source_ref=SourceRef.parse(f"graph:alpha:message:{native}"),
        actor=person_handle("graph", sender),
        occurred_at=occurred_at,
        payload=MessagePayload(channel="#payments", excerpt=excerpt, mentions=mentions),
    )


def _persist(built, *events: NormalizedEvent, scope: DataScope = PERSONAL) -> None:
    built.storage.persist_events(tuple(events), scope=scope)


def _clocked(tmp_path: Path):
    """A daemon whose storage clock the test moves, so entries differ in age."""
    clock = [NOW]
    built = build(
        tmp_path,
        "alpha",
        now=lambda: clock[0],
        keychain=_NoKey(),
        config=Config(display_timezone=ZONE),
    )
    built.connectors = {"graph:contoso": _Calendar("graph:contoso")}
    return built, clock


def _signal_lines(text: str) -> list[str]:
    body = sections(text)["Proactive Enablement"]
    return [line for line in body.split("\n") if line.startswith("- **")]


def test_a_mention_of_the_pm_is_listed_first_marked_and_the_rest_newest_first(tmp_path):
    """Acceptance 1 — one message mentioning the PM's recorded `user_id`, three not."""
    built, clock = _clocked(tmp_path)
    _save_row(built, "graph:work", _graph_row("graph:work", PM_ID))
    for minutes, native, mentions, excerpt in (
        (240, "m_mention", (f"graph:{PM_ID}",), "can you approve?"),
        (180, "m_old", (), "oldest"),
        (120, "m_mid", (f"graph:{OTHER_ID}",), "middle"),
        (60, "m_new", None, "newest"),
    ):
        clock[0] = NOW - timedelta(minutes=minutes)
        # Posted when harvested, so the labels are the harvest minutes (`23f`).
        _persist(
            built,
            _message(native, excerpt=excerpt, mentions=mentions, occurred_at=clock[0]),
        )
    clock[0] = NOW

    lines = _signal_lines(run_dashboard(built, now=NOW).read_text(encoding="utf-8"))
    assert lines == [
        f"- **08:30** graph:{OTHER_ID} in #payments — can you approve? — mentions you",
        f"- **11:30** graph:{OTHER_ID} in #payments — newest",
        f"- **10:30** graph:{OTHER_ID} in #payments — middle",
        f"- **09:30** graph:{OTHER_ID} in #payments — oldest",
    ]


def test_a_graph_row_without_a_user_id_marks_nothing_and_names_its_sign_in(tmp_path):
    """Acceptance 3."""
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    _save_row(built, "graph:work", _graph_row("graph:work", None))
    _persist(built, _message("m1", mentions=(f"graph:{PM_ID}",)))

    section = sections(run_dashboard(built, now=NOW).read_text(encoding="utf-8"))[
        "Proactive Enablement"
    ]
    assert "mentions you" not in section
    sign_in = [line for line in section.split("\n") if "predates" in line]
    assert len(sign_in) == 1
    assert "`graph:work`" in sign_in[0]
    assert "`pm-ai connector sign-in graph:work`" in sign_in[0]


def test_a_disabled_rows_user_id_still_marks_and_prints_no_sign_in_line(tmp_path):
    """Matrix: disabled Graph row — its handle counts; no sign-in line for it."""
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    _save_row(built, "graph:old", _graph_row("graph:old", PM_ID, enabled=False))
    _save_row(built, "graph:off", _graph_row("graph:off", None, enabled=False))
    _persist(built, _message("m1", mentions=(f"graph:{PM_ID}",)))

    section = sections(run_dashboard(built, now=NOW).read_text(encoding="utf-8"))[
        "Proactive Enablement"
    ]
    assert _signal_lines(f"## Proactive Enablement\n\n{section}")[0].endswith(
        "mentions you"
    )
    assert "predates" not in section


def test_a_row_of_an_unknown_system_is_never_read_for_the_pm(tmp_path):
    """Matrix: unknown-system row — its `user_id` marks nothing, and no sign-in line."""
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    _save_row(built, "jira:x", {"instance": "jira:x", "system": "jira", "enabled": True, "user_id": PM_ID})
    _save_row(built, "jira:y", {"instance": "jira:y", "system": "jira", "enabled": True})
    _persist(built, _message("m1", mentions=(f"graph:{PM_ID}",)))

    section = sections(run_dashboard(built, now=NOW).read_text(encoding="utf-8"))[
        "Proactive Enablement"
    ]
    assert "mentions you" not in section
    assert "predates" not in section


def test_the_project_dashboard_marks_a_mention_from_the_same_rows(tmp_path):
    """Matrix: project dashboard, mention — `--scope project:alpha`."""
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    _save_row(built, "graph:work", _graph_row("graph:work", PM_ID))
    _persist(
        built,
        _message("p1", scope=ALPHA, excerpt="alpha ask", mentions=(f"graph:{PM_ID}",)),
        scope=ALPHA,
    )

    lines = _signal_lines(
        run_dashboard(built, scope=ALPHA, now=NOW).read_text(encoding="utf-8")
    )
    # Labelled with the posted time, five minutes before the harvest (`23f`).
    assert lines == [f"- **12:25** graph:{OTHER_ID} in #payments — alpha ask — mentions you"]


def test_each_dashboard_reads_only_its_own_scopes_log(tmp_path):
    """Matrix: scope isolation, both directions, end to end."""
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    _persist(built, _message("personal1", excerpt="personal only"))
    _persist(
        built, _message("project1", scope=ALPHA, excerpt="alpha only"), scope=ALPHA
    )

    personal = run_dashboard(built, now=NOW).read_text(encoding="utf-8")
    project = run_dashboard(built, scope=ALPHA, now=NOW).read_text(encoding="utf-8")
    assert "personal only" in personal and "alpha only" not in personal
    assert "alpha only" in project and "personal only" not in project


def test_twenty_three_messages_list_twenty_and_say_three_more(tmp_path):
    """Acceptance 4."""
    built, clock = _clocked(tmp_path)
    for n in range(23):
        clock[0] = NOW - timedelta(minutes=n + 1)
        _persist(built, _message(f"m{n:02d}", excerpt=f"message {n:02d}"))
    clock[0] = NOW

    text = run_dashboard(built, now=NOW).read_text(encoding="utf-8")
    assert len(_signal_lines(text)) == 20
    assert (
        "3 more message signals in this window are not shown; they are in the "
        "event log" in text
    )



@pytest.mark.parametrize("user_id", [None, "", "   ", 42], ids=["absent", "blank", "whitespace", "not text"])
def test_every_unusable_user_id_marks_nothing_and_names_the_sign_in(tmp_path, user_id):
    """Matrix: no `user_id` on a row — absent, blank, whitespace, or not text."""
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    row = _graph_row("graph:work", None)
    if user_id is not None:
        row["user_id"] = user_id
    _save_row(built, "graph:work", row)
    _persist(built, _message("m1", mentions=(f"graph:{PM_ID}",)))

    section = sections(run_dashboard(built, now=NOW).read_text(encoding="utf-8"))[
        "Proactive Enablement"
    ]
    assert "mentions you" not in section
    assert [line for line in section.split("\n") if "predates" in line] == [
        "The sign-in for `graph:work` predates pm-ai recording the user id, so no "
        "message here is marked as mentioning you for that connector. Run "
        "`pm-ai connector sign-in graph:work` to record it."
    ]


def test_the_sign_in_line_names_the_rows_own_instance(tmp_path):
    """The row's `instance` value, not its file name."""
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    _save_row(built, "graph:file-name", _graph_row("graph:tenant-a", None))

    section = sections(run_dashboard(built, now=NOW).read_text(encoding="utf-8"))[
        "Proactive Enablement"
    ]
    assert "`pm-ai connector sign-in graph:tenant-a`" in section
    assert "file-name" not in section


def test_the_pms_own_row_being_corrupt_is_said_never_silent(tmp_path):
    """The PM's only Graph row cannot be read: nothing can be marked, and the
    section says mentions could not be checked against it, and why."""
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    built.storage.write_artifact(
        b"{not json", scope=APPLICATION, artifact="connectors/", name="graph:work.json"
    )
    _persist(built, _message("m1", mentions=(f"graph:{PM_ID}",)))

    section = sections(run_dashboard(built, now=NOW).read_text(encoding="utf-8"))[
        "Proactive Enablement"
    ]
    assert "mentions you" not in section
    assert (
        "Mentions could not be checked against `graph:work.json`: it is not valid JSON."
        in section
    )


def test_connectors_that_cannot_be_listed_are_said(tmp_path, monkeypatch):
    built = daemon(tmp_path, _Calendar("graph:contoso"))
    _persist(built, _message("m1", mentions=(f"graph:{PM_ID}",)))
    original = built.storage.list_collection

    def refuse(*, scope, artifact):
        if artifact == "connectors/":
            raise PermissionError(13, "Permission denied", "connectors")
        return original(scope=scope, artifact=artifact)

    monkeypatch.setattr(built.storage, "list_collection", refuse)
    section = sections(run_dashboard(built, now=NOW).read_text(encoding="utf-8"))[
        "Proactive Enablement"
    ]
    assert "mentions you" not in section
    assert "Mentions could not be checked against `connectors/`: it could not be listed" in section


# ── Story 23e — the render asks each calendar for the whole local day ────────


class _DayCalendar(_Calendar):
    """A calendar offering the calendar-only read, recording the range it is handed.

    `harvest` stays available and refuses: a render asking a connector that has
    the read through its harvest would be reading the harvest's window.
    """

    def __init__(self, instance: str, **answers: object) -> None:
        super().__init__(instance, **answers)  # type: ignore[arg-type]
        self.ranges: list[tuple[datetime, datetime]] = []

    def read_calendar(self, start: datetime, end: datetime) -> HarvestResult:
        self.ranges.append((start, end))
        result = super().harvest(Cursor())
        self.asked.clear()
        return result

    def harvest(self, since: Cursor) -> HarvestResult:
        raise AssertionError("a connector with the calendar-only read is not harvested")


_UTC = timezone.utc


def test_the_port_names_the_range_it_reads():
    """`isinstance` against a runtime protocol checks the name only, so a
    connector with the old `read_calendar(since)` would still pass it."""
    import inspect

    from pm_ai.ports import CalendarReadPort

    assert list(inspect.signature(CalendarReadPort.read_calendar).parameters) == [
        "self",
        "start",
        "end",
    ]


def test_the_render_hands_the_calendar_its_display_day(tmp_path):
    """12:30 in Warsaw (UTC+2): local midnight to the next local midnight, in UTC,
    and the page shows what that read answered."""
    calendar = _DayCalendar("graph:contoso", live=(meeting("m_1", "Read over the day"),))
    built = daemon(tmp_path, calendar)
    run_dashboard(built, now=NOW)
    assert calendar.ranges == [
        (datetime(2026, 9, 8, 22, 0, tzinfo=_UTC), datetime(2026, 9, 9, 22, 0, tzinfo=_UTC))
    ]
    assert "Read over the day" in sections(written(built))["Time-Critical Activities"]


def test_a_0700_render_lists_the_afternoon(tmp_path):
    """Matrix: UTC−5 at 07:00, meetings at 09:00, 11:30, 14:00, 16:30 local."""
    seven = datetime(2026, 9, 9, 12, 0, tzinfo=_UTC)  # 07:00 in Bogotá
    local = [(9, 0), (11, 30), (14, 0), (16, 30)]
    calendar = _DayCalendar(
        "graph:contoso",
        live=tuple(
            meeting(f"m_{h}{m}", f"Meeting {h:02d}:{m:02d}", start=datetime(2026, 9, 9, h + 5, m, tzinfo=_UTC))
            for h, m in local
        ),
    )
    built = daemon(tmp_path, calendar, zone="America/Bogota")
    run_dashboard(built, now=seven)
    assert calendar.ranges == [
        (datetime(2026, 9, 9, 5, 0, tzinfo=_UTC), datetime(2026, 9, 10, 5, 0, tzinfo=_UTC))
    ]
    body = sections(written(built))["Time-Critical Activities"]
    for h, m in local:
        assert f"Meeting {h:02d}:{m:02d}" in body


@pytest.mark.parametrize(
    ("day", "start", "end", "hours"),
    [
        # Spring forward at 02:00: a 23-hour day.
        (
            datetime(2026, 3, 29, 10, 0, tzinfo=_UTC),
            datetime(2026, 3, 28, 23, 0, tzinfo=_UTC),
            datetime(2026, 3, 29, 22, 0, tzinfo=_UTC),
            23,
        ),
        # Fall back at 03:00: a 25-hour day.
        (
            datetime(2026, 10, 25, 10, 0, tzinfo=_UTC),
            datetime(2026, 10, 24, 22, 0, tzinfo=_UTC),
            datetime(2026, 10, 25, 23, 0, tzinfo=_UTC),
            25,
        ),
    ],
    ids=["23-hour-day", "25-hour-day"],
)
def test_a_daylight_saving_day_is_asked_for_midnight_to_midnight(tmp_path, day, start, end, hours):
    """Matrix: DST change at 02:00 — and a meeting in the last hour is listed."""
    last_hour = end - timedelta(minutes=30)  # 23:30 local
    calendar = _DayCalendar(
        "graph:contoso",
        live=(
            meeting("m_late", "Late call", start=last_hour),
            meeting("m_morning", "Morning", start=start + timedelta(hours=11)),
        ),
    )
    built = daemon(tmp_path, calendar)
    run_dashboard(built, now=day)
    assert calendar.ranges == [(start, end)]
    assert end - start == timedelta(hours=hours)
    assert "Late call" in sections(written(built))["Time-Critical Activities"]


@pytest.mark.parametrize(
    ("day", "start", "end", "hours"),
    [
        # Havana's clocks jump from 00:00 to 01:00: the day starts at 01:00.
        (
            datetime(2026, 3, 8, 16, 0, tzinfo=_UTC),
            datetime(2026, 3, 8, 5, 0, tzinfo=_UTC),
            datetime(2026, 3, 9, 4, 0, tzinfo=_UTC),
            23,
        ),
        # And fall back from 01:00 to 00:00: a 25-hour day.
        (
            datetime(2026, 11, 1, 16, 0, tzinfo=_UTC),
            datetime(2026, 11, 1, 4, 0, tzinfo=_UTC),
            datetime(2026, 11, 2, 5, 0, tzinfo=_UTC),
            25,
        ),
    ],
    ids=["spring-at-midnight", "fall-at-midnight"],
)
def test_a_zone_changing_clocks_at_midnight_starts_at_the_first_existing_instant(
    tmp_path, day, start, end, hours
):
    """Matrix: DST change at midnight — and the display date is still the day's
    own, so a meeting later today and an all-day marker dated today are listed."""
    from dataclasses import replace
    from zoneinfo import ZoneInfo

    calendar = _DayCalendar(
        "graph:contoso",
        live=(
            meeting("m_noon", "Noon in Havana", start=day + timedelta(hours=1)),
            replace(
                meeting("m_allday", "Havana holiday", start=start, minutes=0),
                all_day_date=day.astimezone(ZoneInfo("America/Havana")).date(),
            ),
        ),
    )
    built = daemon(tmp_path, calendar, zone="America/Havana")
    run_dashboard(built, now=day)
    assert calendar.ranges == [(start, end)]
    assert end - start == timedelta(hours=hours)
    body = sections(written(built))["Time-Critical Activities"]
    assert "Noon in Havana" in body and "Havana holiday" in body


def test_two_tenants_are_each_read_for_the_same_local_day(tmp_path):
    """Matrix: two tenants — each asked for the day, merged and deduplicated."""
    apex = _DayCalendar("graph:apex", live=(meeting("m_a", "Weekly Sync", ical_uid="uid-1"),))
    zebra = _DayCalendar(
        "graph:zebra",
        live=(
            meeting("m_z", "Weekly Sync", ical_uid="uid-1"),
            meeting("m_y", "Vendor call", start=_at(14, 0)),
        ),
    )
    built = daemon(tmp_path, apex, zebra)
    run_dashboard(built, now=NOW)
    assert apex.ranges == zebra.ranges == [
        (datetime(2026, 9, 8, 22, 0, tzinfo=_UTC), datetime(2026, 9, 9, 22, 0, tzinfo=_UTC))
    ]
    body = sections(written(built))["Time-Critical Activities"]
    assert body.count("Weekly Sync") == 1
    assert "Vendor call" in body


def test_two_tenants_one_failing_is_partial(tmp_path):
    apex = _DayCalendar("graph:apex", live=(meeting("m_a", "Weekly Sync"),))
    zebra = _DayCalendar(
        "graph:zebra", failure=HarvestFailure("graph:zebra is signed out", retryable=False)
    )
    built = daemon(tmp_path, apex, zebra)
    run_dashboard(built, now=NOW)
    body = sections(written(built))["Time-Critical Activities"]
    assert "Weekly Sync" in body
    assert "graph:zebra is signed out" in body
    assert zebra.ranges == apex.ranges


def test_a_read_abandoned_at_its_deadline_is_an_unread_calendar(tmp_path):
    """Matrix: the bounded step's deadline passes — the calendar is unread, the
    render is not failed."""
    from pm_ai.app.pipelines import FetchAbandoned, prepare_dashboard

    def abandon(call, label, *, own_budget=False):
        raise FetchAbandoned(f"{label} did not answer within its 0.2 s budget")

    calendar = _DayCalendar("graph:contoso", live=(meeting("m_1", "Standup"),))
    built = daemon(tmp_path, calendar)
    page = prepare_dashboard(built, scope=PERSONAL, now=NOW, bound=abandon).decode("utf-8")
    body = sections(page)["Time-Critical Activities"]
    assert "The calendar could not be read" in body
    assert "did not answer within its 0.2 s budget" in body
    assert calendar.ranges == []


def test_a_render_through_the_day_read_writes_nothing_but_the_page(tmp_path, monkeypatch):
    """Acceptance — no meeting record, cursor, coverage window or event entry."""
    calendar = _DayCalendar(
        "graph:contoso",
        records=(meeting("m_done", "Review", start=_at(8, 0)),),
        live=(meeting("m_next", "Standup", start=_at(13, 0)),),
    )
    built = daemon(tmp_path, calendar)
    before = _log_bytes(built, PERSONAL)

    def refuse(*args, **kwargs):
        raise AssertionError("a render must not write here")

    for name in ("save_cursor", "append_event_log", "persist_events"):
        monkeypatch.setattr(type(built.storage), name, refuse)
    monkeypatch.setattr(type(built.meetings), "put", refuse)

    run_dashboard(built, now=NOW)

    assert calendar.ranges
    assert _log_bytes(built, PERSONAL) == before
    assert built.storage.list_collection(scope=PERSONAL, artifact="meetings/") == ()
    assert built.storage.coverage_windows("graph:contoso") == []


def test_a_connector_without_the_day_read_is_asked_through_its_harvest(tmp_path):
    """The fallback: harvested, and the overlap rule selects the day."""
    calendar = _Calendar(
        "graph:contoso",
        live=(
            meeting("m_today", "Today's sync", start=_at(13, 0)),
            meeting("m_tomorrow", "Tomorrow's sync", start=_at(13, 0) + timedelta(days=1)),
        ),
    )
    built = daemon(tmp_path, calendar)
    run_dashboard(built, now=NOW)
    assert calendar.asked == [Cursor()]
    body = sections(written(built))["Time-Critical Activities"]
    assert "Today's sync" in body and "Tomorrow's sync" not in body


def test_an_ended_cancelled_meeting_is_listed_marked_cancelled(tmp_path):
    """Matrix: cancelled, ended — listed as ended, its facts including `cancelled`."""
    from dataclasses import replace

    calendar = _DayCalendar(
        "graph:contoso",
        records=(replace(meeting("m_gone", "Called-off review", start=_at(8, 0)), cancelled=True),),
        live=(meeting("m_next", "Standup", start=_at(13, 0)),),
    )
    built = daemon(tmp_path, calendar)
    run_dashboard(built, now=NOW)
    lines = sections(written(built))["Time-Critical Activities"].split("\n")
    (gone,) = [line for line in lines if "Called-off review" in line]
    assert "ended" in gone and "cancelled" in gone
    (standup,) = [line for line in lines if "Standup" in line]
    assert "cancelled" not in standup


def test_an_all_day_marker_is_listed_beside_the_collapse_and_not_counted_in_it(tmp_path):
    """An ended meeting plus an all-day marker: the count is one, the marker listed."""
    from dataclasses import replace

    marker = replace(
        meeting("m_allday", "Team offsite", start=_at(0, 0), minutes=0),
        all_day_date=NOW.date(),
    )
    calendar = _DayCalendar(
        "graph:contoso",
        records=(meeting("m_done", "Morning Review", start=_at(8, 0)),),
        live=(marker,),
    )
    built = daemon(tmp_path, calendar)
    run_dashboard(built, now=NOW)
    body = sections(written(built))["Time-Critical Activities"].split("\n")
    assert body[0].startswith("- **all day** Team offsite — 2 attendees")
    assert body[1].startswith("Today's one meeting has ended.")
    assert "All 2" not in "\n".join(body)


def test_a_day_of_only_an_all_day_marker_never_says_it_has_ended(tmp_path):
    """07:00 in Warsaw, one all-day marker: listed, and nothing "has ended"."""
    from dataclasses import replace

    seven = datetime(2026, 9, 9, 5, 0, tzinfo=_UTC)
    marker = replace(
        meeting("m_allday", "Public holiday", start=datetime(2026, 9, 8, 22, 0, tzinfo=_UTC), minutes=0),
        all_day_date=seven.date(),
    )
    built = daemon(tmp_path, _DayCalendar("graph:contoso", live=(marker,)))
    run_dashboard(built, now=seven)
    body = sections(written(built))["Time-Critical Activities"]
    schedule, summaries = body.split("\n\n")
    assert schedule == "- **all day** Public holiday — 2 attendees"
    assert "has ended" not in body and "have ended" not in body
    # Story 7d: the summaries' window follows the schedule, stated when empty.
    assert summaries.startswith("No meeting summary was recorded between")


# ── Multi-day all-day entries (story 23h) ────────────────────────────────────


def _spanning_marker(start_date, end_date, *, title: str = "Offsite") -> Meeting:
    """An all-day marker from `start_date` up to, not including, `end_date`."""
    from dataclasses import replace

    midnight = datetime(start_date.year, start_date.month, start_date.day, tzinfo=_UTC)
    return replace(
        meeting("m_offsite", title, start=midnight, minutes=0),
        all_day_date=start_date,
        all_day_end_date=end_date,
    )


def _listed_on(tmp_path: Path, marker: Meeting, now: datetime, *, zone: str = ZONE) -> bool:
    built = daemon(tmp_path, _DayCalendar("graph:contoso", live=(marker,)), zone=zone)
    run_dashboard(built, now=now)
    body = sections(written(built))["Time-Critical Activities"]
    lines = [line for line in body.split("\n") if marker.title in line]
    assert all(line.startswith(f"- **all day** {marker.title} — ") for line in lines)
    return bool(lines)


@pytest.mark.parametrize(
    ("day", "listed"),
    [(17, True), (18, True), (19, True), (20, False)],
    ids=["first-day", "middle-day", "last-day", "end-date"],
)
def test_a_multi_day_all_day_entry_is_listed_on_every_date_it_spans(tmp_path, day, listed):
    """Matrix: start Oct 17, end Oct 20 — `all day` on 17, 18, 19; not 20."""
    from datetime import date

    marker = _spanning_marker(date(2026, 10, 17), date(2026, 10, 20))
    noon = datetime(2026, 10, day, 10, 0, tzinfo=_UTC)  # 12:00 in Warsaw
    assert _listed_on(tmp_path, marker, noon) is listed


@pytest.mark.parametrize(
    ("day", "listed"),
    [(16, False), (17, True), (18, True), (19, False)],
    ids=["day-before", "first-day", "last-day", "end-date"],
)
def test_a_multi_day_all_day_entry_west_of_utc_keeps_its_own_dates(tmp_path, day, listed):
    """Matrix: UTC−5, start Oct 17, end Oct 19 — Oct 17 and 18; not Oct 16.

    The midnight-UTC start reads as 19:00 on Oct 16 in Bogotá, and the request
    range for Oct 16 overlaps it; the dates decide.
    """
    from datetime import date

    marker = _spanning_marker(date(2026, 10, 17), date(2026, 10, 19))
    evening = datetime(2026, 10, day, 23, 30, tzinfo=_UTC)  # 18:30 in Bogotá
    assert _listed_on(tmp_path, marker, evening, zone="America/Bogota") is listed


@pytest.mark.parametrize(
    "end_day", [17, 16], ids=["end-on-the-start-date", "end-before-the-start-date"]
)
@pytest.mark.parametrize(("day", "listed"), [(16, False), (17, True), (18, False)])
def test_an_all_day_entry_ending_not_after_its_start_is_on_its_start_date_only(
    tmp_path, end_day, day, listed
):
    """Matrix: start Oct 17, end Oct 17 or Oct 16 — Oct 17 only."""
    from datetime import date

    marker = _spanning_marker(date(2026, 10, 17), date(2026, 10, end_day))
    noon = datetime(2026, 10, day, 10, 0, tzinfo=_UTC)
    assert _listed_on(tmp_path, marker, noon) is listed


@pytest.mark.parametrize(("day", "listed"), [(16, False), (17, True), (18, False)])
def test_an_all_day_entry_with_no_end_date_is_on_its_start_date_only(tmp_path, day, listed):
    """Matrix: a `Meeting` with a start date only, as `23e` built them."""
    from datetime import date

    marker = _spanning_marker(date(2026, 10, 17), None)
    assert marker.all_day_end_date is None
    noon = datetime(2026, 10, day, 10, 0, tzinfo=_UTC)
    assert _listed_on(tmp_path, marker, noon) is listed


# ── Story 23g — Team Activity from `8t`'s record and the project's commits ───

GITLAB_HOST = "https://gitlab.example.com"
PEOPLE = DataScope(ScopeKind.PEOPLE, person_id="dana")


def _gitlab_person(user_id: int, name: str, username: str, *, bot: bool | None = False):
    from pm_ai.domain.handles import gitlab_handle
    from pm_ai.domain.work import GitLabPerson

    return GitLabPerson(gitlab_handle(GITLAB_HOST, user_id), name, username, bot)


def _record_work(built) -> None:
    """An `8t` record, written through the one writer as a harvest writes it."""
    from pm_ai.domain.work import Issue, Member, MergeRequest, Section, SectionRead, WorkChange

    dana = _gitlab_person(1, "Dana Smith", "dana")
    sam = _gitlab_person(2, "Sam Lee", "sam")
    bot = _gitlab_person(9, "project_bot", "project_bot", bot=True)

    def issue(iid: int, *assignees) -> Issue:
        return Issue(
            iid=iid, title=f"Issue {iid}", state="opened", assignees=assignees,
            milestone_title=None, milestone_due=None, labels=(),
            updated_at=NOW - timedelta(days=1), closed_at=None, web_url=None,
            confidential=False,
        )

    built.storage.record_project_work(
        ALPHA,
        WorkChange(
            instance="gitlab:alpha",
            started_at=NOW - timedelta(hours=1),
            members=(Member(dana, "active"), Member(sam, "active"), Member(bot, "active")),
            issues=(issue(41, dana), issue(52, sam)),
            merge_requests=(
                MergeRequest(
                    iid=9, title="MR 9", state="opened", source_branch=None,
                    author=None, draft=False, updated_at=NOW - timedelta(hours=3),
                    merged_at=None, closed_at=None, web_url=None, issue_iids=(52,),
                ),
            ),
            sections=tuple(SectionRead(section, complete=True) for section in Section),
        ),
    )


def _commit(sha: str, message: str) -> NormalizedEvent:
    from pm_ai.domain.events import CommitPayload

    return NormalizedEvent(
        scope=ALPHA,
        type=ObservedEventType.COMMIT_PUSHED,
        source_ref=SourceRef.parse(f"gitlab:alpha:commit:{sha}"),
        actor=person_handle("email", "dev@example.com"),
        occurred_at=NOW - timedelta(hours=2),
        payload=CommitPayload(sha=sha, message=message),
    )


def _gitlab_daemon(tmp_path: Path):
    """A daemon that keeps its built-in GitLab adapter beside a calendar double."""
    built = build(
        tmp_path,
        "alpha",
        now=lambda: NOW,
        keychain=_NoKey(),
        config=Config(display_timezone=ZONE),
    )
    built.connectors["graph:contoso"] = _Calendar("graph:contoso")
    assert "gitlab:alpha" in built.connectors
    return built


def test_a_written_record_and_commit_lines_produce_team_activity(tmp_path, monkeypatch):
    """Acceptance — Team Activity third, no member-level total, no GitLab
    request, and nothing read outside the project scope."""
    from pm_ai.connectors.gitlab.client import GitLabClient
    from pm_ai.core.rendering import TEAM_ACTIVITY

    built = _gitlab_daemon(tmp_path)
    _record_work(built)
    _persist(
        built,
        _commit("a" * 40, "Fix #41 token refresh"),
        _commit("b" * 40, "More on #41"),
        _commit("c" * 40, "tidy"),
        scope=ALPHA,
    )

    def no_request(*args, **kwargs):
        raise AssertionError("a render must make no GitLab request")

    monkeypatch.setattr(GitLabClient, "_send", no_request)
    adapter = built.connectors["gitlab:alpha"]
    monkeypatch.setattr(adapter, "transport", no_request)
    for name in ("harvest", "check_health"):
        monkeypatch.setattr(type(adapter), name, no_request)

    asked_work: list[DataScope] = []
    original_work = type(built.storage).project_work

    def project_work(self, scope):
        asked_work.append(scope)
        return original_work(self, scope)

    monkeypatch.setattr(type(built.storage), "project_work", project_work)
    asked_paths: list[DataScope] = []
    for method in ("resolve", "scope_root"):
        original = getattr(ScopePaths, method)

        def record(self, scope, *args, _original=original, **kwargs):
            asked_paths.append(scope)
            return _original(self, scope, *args, **kwargs)

        monkeypatch.setattr(ScopePaths, method, record)

    text = run_dashboard(built, scope=ALPHA, now=NOW).read_text(encoding="utf-8")

    body = sections(text)
    assert list(body) == list(PROJECT_HEADINGS)
    assert PROJECT_HEADINGS[2] == TEAM_ACTIVITY
    team = body[TEAM_ACTIVITY]
    assert "- **Dana Smith** `@dana`" in team
    assert "- 2 commits; latest" in team
    assert "merge request `!9` updated" in team
    assert "project_bot" not in team
    assert "1 commit references no issue" in team
    assert "total" not in team.lower()
    assert asked_work == [ALPHA]
    assert asked_paths, "the resolver was never consulted, so this proves nothing"
    assert not [s for s in asked_paths if s.kind in (ScopeKind.PERSONAL, ScopeKind.PEOPLE)]


def test_the_personal_page_carries_no_team_activity_and_reads_no_record(tmp_path, monkeypatch):
    from pm_ai.core.rendering import TEAM_ACTIVITY

    built = _gitlab_daemon(tmp_path)
    _record_work(built)

    def refuse(self, scope):
        raise AssertionError("the personal render must not read GitLab work")

    monkeypatch.setattr(type(built.storage), "project_work", refuse)
    text = run_dashboard(built, now=NOW).read_text(encoding="utf-8")
    assert list(sections(text)) == list(HEADINGS)
    assert TEAM_ACTIVITY not in text


@pytest.mark.parametrize("scope", [PERSONAL, PEOPLE, APPLICATION])
def test_reading_gitlab_work_for_a_scope_that_is_not_a_project_raises(tmp_path, scope):
    """Matrix row "Personal scope" — the reader refuses (Andrei, 2026-10-18)."""
    built = _gitlab_daemon(tmp_path)
    _record_work(built)
    with pytest.raises(ValueError, match="not one"):
        built.storage.project_work(scope)


def test_a_malformed_record_is_stated_and_the_page_is_written(tmp_path):
    """Matrix row "Record unreadable": a row a hand edit broke."""
    import sqlite3

    from pm_ai.core.rendering import TEAM_ACTIVITY

    built = _gitlab_daemon(tmp_path)
    _record_work(built)
    (store,) = tmp_path.rglob("operational.db")
    with sqlite3.connect(store) as db:
        db.execute("UPDATE gitlab_issues SET updated_at = 'not a time' WHERE iid = 41")

    text = run_dashboard(built, scope=ALPHA, now=NOW).read_text(encoding="utf-8")
    body = sections(text)
    assert list(body) == list(PROJECT_HEADINGS)
    assert "The project's GitLab record could not be read" in body[TEAM_ACTIVITY]
    assert "not a time" in body[TEAM_ACTIVITY]


def test_a_store_that_cannot_be_read_is_stated(tmp_path, monkeypatch):
    from pm_ai.core.rendering import TEAM_ACTIVITY
    from pm_ai.storage.service import OperationalStoreUnavailable

    built = _gitlab_daemon(tmp_path)

    def unreadable(self, scope):
        raise OperationalStoreUnavailable("database disk image is malformed")

    monkeypatch.setattr(type(built.storage), "project_work", unreadable)
    body = sections(run_dashboard(built, scope=ALPHA, now=NOW).read_text(encoding="utf-8"))
    assert "database disk image is malformed" in body[TEAM_ACTIVITY]
    assert "Unlinked work in the window" in body[TEAM_ACTIVITY]


def test_a_project_with_no_record_says_so(tmp_path):
    from pm_ai.core.rendering import TEAM_ACTIVITY

    built = _gitlab_daemon(tmp_path)
    body = sections(run_dashboard(built, scope=ALPHA, now=NOW).read_text(encoding="utf-8"))
    assert "No GitLab issues are recorded for this project" in body[TEAM_ACTIVITY]



def test_a_store_the_library_cannot_read_becomes_a_stated_failure(tmp_path):
    """The library's own error is caught where the render reads the record
    (`pipelines._project_work`), never inside the reader, and becomes a
    stated value rather than a crash."""
    import sqlite3

    from pm_ai.app.pipelines import _project_work

    built = _gitlab_daemon(tmp_path)
    _record_work(built)
    (store,) = tmp_path.rglob("operational.db")
    for sidecar in tmp_path.rglob("operational.db-*"):
        sidecar.unlink()
    store.write_bytes(b"not a database" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        built.storage.project_work(ALPHA)
    answer = _project_work(built, scope=ALPHA)
    assert isinstance(answer, HarvestFailure)
    assert "not a database" in answer.reason


@pytest.mark.parametrize("scope", [PERSONAL, PEOPLE, APPLICATION])
def test_a_scope_that_is_not_a_project_raises_through_the_pipeline_read(tmp_path, monkeypatch, scope):
    """AD-25's refusal propagates; it is never turned into a stated line."""
    from pm_ai.app.pipelines import _project_work

    built = _gitlab_daemon(tmp_path)
    asked: list[DataScope] = []
    monkeypatch.setattr(
        type(built.storage), "project_work", lambda self, s: asked.append(s)
    )
    with pytest.raises(ValueError, match="project scope only"):
        _project_work(built, scope=scope)
    assert asked == []


@pytest.mark.parametrize("error", [OSError("disk gone"), TypeError("bad row type")])
def test_an_os_or_type_error_from_the_reader_is_stated(tmp_path, monkeypatch, error):
    from pm_ai.app.pipelines import _project_work

    built = _gitlab_daemon(tmp_path)

    def raises(self, scope):
        raise error

    monkeypatch.setattr(type(built.storage), "project_work", raises)
    answer = _project_work(built, scope=ALPHA)
    assert isinstance(answer, HarvestFailure)
    assert str(error) == answer.reason


# ── Story 7d: meeting summaries and open action items on the dashboard ───────
#
# Spec: `_bmad-output/specs/spec-pm-ai/stories/7d-summaries-and-actions-on-the-dashboard.md`.
# The rows about what is *read*: which rows the window selects, the project
# wall, the `people` scope, an unreadable record or project, the open items,
# the spend line from the ledger, and no model on the path. What the page
# *says* is in `tests/core/test_rendering_sections.py`.

from pm_ai.app.pipelines import prepare_dashboard  # noqa: E402
from pm_ai.core.aliases import AliasRow, normalise, render_alias_row  # noqa: E402
from pm_ai.core.meeting_summary import action_item_id  # noqa: E402
from pm_ai.core.member_updates import (  # noqa: E402
    ItemKind,
    LinkKind,
    UpdateLine,
    render_update,
)
from pm_ai.domain.disclosure import DisclosureRecord, DisclosureStatus  # noqa: E402
from pm_ai.domain.handles import gitlab_handle  # noqa: E402
from pm_ai.domain.lifecycle import ProposalState  # noqa: E402
from pm_ai.domain.meeting_summaries import (  # noqa: E402
    RecordedSummary,
    SummaryActionItem,
    SummaryAnswer,
    SummaryMaker,
    SummaryStateKind,
)
from pm_ai.domain.proposals import Proposal  # noqa: E402
from pm_ai.domain.work import (  # noqa: E402
    GitLabPerson,
    Issue,
    Member,
    Section,
    SectionRead,
    WorkChange,
)

_PEOPLE = DataScope(ScopeKind.PEOPLE, person_id="p_1")
_GITLAB = "https://gitlab.example.com"
_IVAN = GitLabPerson(gitlab_handle(_GITLAB, 2), "Ivan Petrov", "ivan.p", False)
_ANA = GitLabPerson(gitlab_handle(_GITLAB, 1), "Ana", "ana.k", False)


def _summarized(
    built,
    meeting_id: str,
    *,
    scope: DataScope = PERSONAL,
    title: str = "Release sync",
    start: datetime = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc),
    recorded: datetime = datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc),
    state: SummaryStateKind = SummaryStateKind.SUMMARIZED,
    made_by: SummaryMaker = SummaryMaker.LOCAL,
    requested: bool = False,
    summary: str | None = "The team planned the release.",
    items: tuple[SummaryActionItem, ...] = (),
    reason: str = "",
    record: bool = True,
) -> None:
    """A recorded meeting, its `## Summary` region, its state row and its items."""
    if record:
        built.meetings.put(meeting(meeting_id, title, start=start, scope=scope))
    if record and summary is not None:
        built.meetings.put_summary(
            meeting_id,
            scope=scope,
            summary=RecordedSummary(
                "qwen3:8b" if made_by is SummaryMaker.LOCAL else "claude-sonnet-5-5",
                requested,
                start.date(),
                SummaryAnswer(summary, (), items),
                made_by,
            ),
        )
    _summary_row(
        built, scope, meeting_id, state=state, at=recorded, start=start,
        made_by=made_by, requested=requested, reason=reason,
    )
    for item in items:
        _stage_item(built, scope, meeting_id, item)


def _summary_row(
    built,
    scope: DataScope,
    meeting_id: str,
    *,
    state: SummaryStateKind,
    at: datetime,
    start: datetime,
    made_by: SummaryMaker = SummaryMaker.LOCAL,
    requested: bool = False,
    reason: str = "",
) -> None:
    """A `meeting_summaries` row written as the store keeps it, `at` chosen."""
    db = built.storage._db  # noqa: SLF001 — a fixture's row, not a code path
    db.execute(
        "INSERT OR REPLACE INTO meeting_summaries (scope, meeting_id, state, made_by, "
        "requested, model, attempts, reason, at, version, meeting_start, job) "
        "VALUES (?, ?, ?, ?, ?, NULL, 0, ?, ?, 1, ?, NULL)",
        (
            str(scope), meeting_id, state.value, made_by.value, int(requested), reason,
            at.isoformat(), start.isoformat(),
        ),
    )
    db.commit()


def _stage_item(
    built,
    scope: DataScope,
    meeting_id: str,
    item: SummaryActionItem,
    *,
    state: ProposalState = ProposalState.STAGED,
    created: datetime = NOW,
    ttl: timedelta = timedelta(days=7),
) -> str:
    proposal_id = action_item_id(scope, meeting_id, item)
    built.storage.stage_proposal(
        Proposal(
            proposal_id=proposal_id,
            type="action_item",
            summary=item.what[:80],
            payload={"what": item.what, "owner": item.owner, "due": item.due},
            target=None,
            cites=SourceRef.parse(f"meeting:{meeting_id}"),
            created_at=created,
            state=state,
            ttl=ttl,
        )
    )
    return proposal_id


def _page(built, scope: DataScope = PERSONAL, *, now: datetime = NOW) -> dict[str, str]:
    return sections(prepare_dashboard(built, scope=scope, now=now).decode("utf-8"))


def _alpha_work(built, *issues: Issue) -> None:
    built.storage.record_project_work(
        ALPHA,
        WorkChange(
            instance="gitlab:alpha",
            started_at=NOW - timedelta(hours=1),
            members=(Member(_ANA, "active"), Member(_IVAN, "active")),
            issues=issues,
            sections=(
                SectionRead(Section.MEMBERS, True),
                SectionRead(Section.ISSUES, True),
                SectionRead(Section.MERGE_REQUESTS, True),
            ),
        ),
    )


def _issue(iid: int, *assignees: GitLabPerson) -> Issue:
    return Issue(
        iid=iid,
        title=f"Issue {iid}",
        state="opened",
        assignees=assignees,
        milestone_title="Sprint 9",
        milestone_due=datetime(2026, 9, 20).date(),
        labels=(),
        updated_at=NOW - timedelta(days=2),
        closed_at=None,
        web_url=None,
        confidential=False,
    )


def test_7d_acceptance_both_pages_at_0700_and_each_keeps_its_headings(tmp_path):
    """Acceptance 1: a personal and a project meeting summarized yesterday
    afternoon; at 07:00 the personal file lists both, the project file only its own."""
    built = daemon(tmp_path)
    seven = datetime(2026, 9, 9, 5, 0, tzinfo=timezone.utc)
    afternoon = datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc)
    _summarized(built, "mtg_home", title="Career chat", recorded=afternoon)
    _summarized(built, "mtg_alpha", scope=ALPHA, title="Alpha planning", recorded=afternoon)

    personal = _page(built, now=seven)
    project = _page(built, ALPHA, now=seven)

    assert list(personal) == list(HEADINGS)
    assert list(project) == list(PROJECT_HEADINGS)
    assert "Career chat" in personal["Time-Critical Activities"]
    assert "Alpha planning" in personal["Time-Critical Activities"]
    assert "--scope project:alpha --meeting mtg_alpha" in personal["Time-Critical Activities"]
    assert "Alpha planning" in project["Time-Critical Activities"]
    assert "Career chat" not in "\n".join(project.values())


def test_7d_acceptance_a_personal_claude_summary_never_reaches_the_project_page(tmp_path):
    """Acceptance 2 (moved from `7c`)."""
    built = daemon(tmp_path)
    _summarized(
        built, "mtg_home", title="Salary review", made_by=SummaryMaker.FRONTIER,
        requested=True, summary="We discussed the offer in detail.",
        items=(SummaryActionItem("send the counter-offer", "Me"),),
    )
    page = prepare_dashboard(built, scope=ALPHA, now=NOW).decode("utf-8")
    for leaked in ("Salary review", "the offer", "counter-offer", "mtg_home", "Claude"):
        assert leaked not in page
    assert "Frontier spend" not in page


def test_7d_meetings_are_chosen_by_when_the_summary_was_recorded(tmp_path):
    """Matrix "Summary written late": last Tuesday's meeting, summarized since
    the last render, is listed — and the render after does not repeat it."""
    built = daemon(tmp_path)
    built.storage.record_render(PERSONAL, at=NOW - timedelta(hours=6))
    tuesday = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    _summarized(built, "mtg_late", title="Old sync", start=tuesday, recorded=NOW - timedelta(hours=1))
    _summarized(built, "mtg_seen", title="Seen sync", recorded=NOW - timedelta(hours=7))

    body = _page(built)["Time-Critical Activities"]
    assert "**2026-09-01 11:00** Old sync" in body
    assert "Seen sync" not in body
    assert "since the last render at 2026-09-09 06:30" in body

    built.storage.record_render(PERSONAL, at=NOW)
    later = _page(built, now=NOW + timedelta(hours=1))["Time-Critical Activities"]
    assert "Old sync" not in later
    assert "No meeting summary was recorded between" in later


def test_7d_open_items_exclude_approved_expired_and_pattern_promises(tmp_path):
    """Matrix "Approved or expired" and "Pattern promise": none listed."""
    built = daemon(tmp_path)
    kept = SummaryActionItem("ship the build", "Ана", "Friday")
    approved = SummaryActionItem("approved one")
    expired = SummaryActionItem("expired one")
    _summarized(built, "mtg_a", items=(kept,))
    _stage_item(built, PERSONAL, "mtg_a", approved, state=ProposalState.APPROVED)
    _stage_item(
        built, PERSONAL, "mtg_a", expired, created=NOW - timedelta(days=8),
        ttl=timedelta(days=7),
    )
    # An `11b` pattern promise for the same meeting: another type, another id.
    built.storage.stage_proposal(
        Proposal(
            proposal_id="prp_promise_mtg_a",
            type="promise",
            summary="I will send the notes",
            payload={"what": "I will send the notes"},
            target=None,
            cites=SourceRef.parse("meeting:mtg_a"),
            created_at=NOW,
        )
    )
    body = _page(built)["Time-Critical Activities"]
    assert "  - Ана: ship the build (Friday)" in body
    for absent in ("approved one", "expired one", "send the notes"):
        assert absent not in body


def test_7d_eight_open_items_show_five_and_count_three(tmp_path):
    """Matrix "Many items", read from the proposals store."""
    built = daemon(tmp_path)
    items = tuple(SummaryActionItem(f"task {n}") for n in range(8))
    _summarized(built, "mtg_a", items=items)
    body = _page(built)["Time-Critical Activities"]
    assert [line for line in body.split("\n") if line.startswith("  - unassigned: ")] == [
        f"  - unassigned: task {n}" for n in range(5)
    ]
    assert "  - and 3 more open action items" in body


def test_7d_a_project_owner_shows_only_once_resolved_to_a_member(tmp_path):
    """Matrix "Owner on a project page": "Ваня" with no alias row is unassigned;
    with a row to a member, that member's display name. The personal page shows
    the label as said."""
    built = daemon(tmp_path)
    _alpha_work(built, _issue(7, _ANA))
    _summarized(
        built, "mtg_a", scope=ALPHA, items=(SummaryActionItem("rework the export", "Ваня"),),
    )
    assert "  - unassigned: rework the export" in _page(built, ALPHA)["Time-Critical Activities"]
    assert "  - Ваня: rework the export" in _page(built)["Time-Critical Activities"]

    built.storage.append_alias(
        render_alias_row(AliasRow(normalise("Ваня"), "Ваня", _IVAN.handle, None, NOW)),
        after=0,
    )
    body = _page(built, ALPHA)["Time-Critical Activities"]
    assert "  - Ivan Petrov: rework the export" in body
    assert "Ваня" not in body


def test_7d_an_item_team_activity_places_is_shown_once(tmp_path):
    """Matrix "Placed by `7e`": the item Team Activity shows under #142 is left out
    of the meeting's items, does not count toward 5, and "shown under #142"."""
    built = daemon(tmp_path)
    _alpha_work(built, _issue(142, _ANA))
    placed = SummaryActionItem("fix #142", "Ana", "Thursday")
    others = tuple(SummaryActionItem(f"task {n}") for n in range(5))
    start = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)
    _summarized(built, "mtg_a", scope=ALPHA, start=start, items=(placed, *others))
    line = UpdateLine(
        kind=ItemKind.ACTION,
        meeting="mtg_a",
        start=start,
        title="Release sync",
        item=action_item_id(ALPHA, "mtg_a", placed),
        issue=142,
        by=LinkKind.NUMBER,
        text=placed.what,
        at=NOW,
        owner=placed.owner,
        due=placed.due,
    )
    built.storage.append_member_updates((render_update(line),), scope=ALPHA, after=0)

    page = _page(built, ALPHA)
    team = page["Team Activity"]
    assert "fix #142" in team
    body = page["Time-Critical Activities"]
    assert "fix #142" not in body
    assert body.count("  - unassigned: task") == 5
    assert "more open action" not in body
    assert "  - 1 open action item shown under #142 in Team Activity" in body
    # The personal page has no Team Activity, so the item is listed there.
    assert "  - Ana: fix #142 (Thursday)" in _page(built)["Time-Critical Activities"]


def test_7d_a_project_that_cannot_be_read_is_one_line_and_the_rest_renders(tmp_path, monkeypatch):
    """Matrix "Project unreadable": the personal page names it; others render."""
    built = daemon(tmp_path)
    _summarized(built, "mtg_home", title="Career chat")
    _summarized(built, "mtg_alpha", scope=ALPHA, title="Alpha planning")
    listing = built.storage.list_collection

    def broken(*, scope, artifact):
        if scope == ALPHA:
            raise PermissionError(13, "Permission denied", "meetings/")
        return listing(scope=scope, artifact=artifact)

    monkeypatch.setattr(built.storage, "list_collection", broken)
    body = _page(built)["Time-Critical Activities"]
    assert "Career chat" in body and "Alpha planning" not in body
    assert "The meeting summaries of `project:alpha` could not be read" in body
    assert "Permission denied" in body


def test_7d_a_project_not_enrolled_is_named(tmp_path):
    """A project with summaries recorded and no longer enrolled: one line naming it."""
    built = daemon(tmp_path)
    gone = DataScope(ScopeKind.PROJECT, project_id="beta")
    _summary_row(
        built, gone, "mtg_beta", state=SummaryStateKind.SUMMARIZED,
        at=NOW - timedelta(hours=1), start=NOW - timedelta(days=1),
    )
    body = _page(built)["Time-Critical Activities"]
    # Skipped on purpose, not a failed read: named as no longer enrolled.
    assert (
        "`project:beta` has meeting summaries recorded in this window and is no "
        "longer enrolled, so its meetings are not listed."
    ) in body
    assert "could not be read" not in body
    assert "in the scopes that could be read" not in body
    assert "mtg_beta" not in body.replace("`project:beta`", "")


def test_7d_a_people_row_is_never_read(tmp_path, monkeypatch):
    """Matrix "People meeting": a `people` row in the window is never read."""
    built = daemon(tmp_path)
    _summary_row(
        built, _PEOPLE, "mtg_1on1", state=SummaryStateKind.SUMMARIZED,
        at=NOW - timedelta(hours=1), start=NOW - timedelta(days=1),
    )
    asked: list[DataScope] = []
    reading = built.storage.summaries_recorded_between
    getting = built.meetings.get

    def rows(scope, **window):
        asked.append(scope)
        return reading(scope, **window)

    def get(meeting_id, *, scope):
        asked.append(scope)
        return getting(meeting_id, scope=scope)

    monkeypatch.setattr(built.storage, "summaries_recorded_between", rows)
    monkeypatch.setattr(built.meetings, "get", get)
    for scope in (PERSONAL, ALPHA):
        page = prepare_dashboard(built, scope=scope, now=NOW).decode("utf-8")
        assert "mtg_1on1" not in page and "people" not in page
    assert asked and all(scope.kind is not ScopeKind.PEOPLE for scope in asked)


def test_7d_one_malformed_record_is_named_and_the_other_shows(tmp_path):
    """Matrix "One malformed record": two rows; one record malformed."""
    built = daemon(tmp_path)
    _summarized(built, "mtg_good", title="Good sync")
    _summarized(built, "mtg_bad", title="Bad sync", recorded=NOW - timedelta(hours=2))
    (member,) = [
        name
        for name in built.storage.list_collection(scope=PERSONAL, artifact="meetings/")
        if "mtg_bad" in name
    ]
    raw = built.storage.read_artifact(scope=PERSONAL, artifact="meetings/", name=member)
    text = raw.decode("utf-8").replace("## Summary\n", "## Summary\nprose\n")
    built.storage.write_artifact(
        text.encode("utf-8"), scope=PERSONAL, artifact="meetings/", name=member
    )
    body = _page(built)["Time-Critical Activities"]
    assert "Good sync — The team planned the release." in body
    assert "- meeting `mtg_bad` in `personal`: its record could not be read" in body
    assert "Bad sync" not in body


def test_7d_fourteen_rows_list_the_ten_most_recent(tmp_path):
    """Matrix "More than 10": 10 most recent, "and 4 earlier"."""
    built = daemon(tmp_path)
    for n in range(14):
        _summarized(
            built, f"mtg_{n:02d}", title=f"Sync {n:02d}",
            recorded=NOW - timedelta(minutes=10 * (n + 1)),
        )
    body = _page(built)["Time-Critical Activities"]
    assert [f"Sync {n:02d}" in body for n in range(14)] == [True] * 10 + [False] * 4
    assert body.endswith("- and 4 earlier summaries recorded in this window are not shown")


def test_7d_waiting_failed_and_reopened_read_from_the_row_and_record(tmp_path):
    """Matrix "Waiting / failed" and "Re-opened", end to end."""
    built = daemon(tmp_path)
    _summarized(
        built, "mtg_w", title="Waiting sync", state=SummaryStateKind.WAITING, summary=None,
        made_by=SummaryMaker.FRONTIER, requested=True,
    )
    _summarized(
        built, "mtg_f", title="Failed sync", state=SummaryStateKind.FAILED, summary=None,
        reason="the transcript is too long for the window.",
    )
    _summarized(built, "mtg_r", title="Reopened sync", state=SummaryStateKind.WAITING)
    body = _page(built)["Time-Critical Activities"]
    assert "Waiting sync — summary waiting for Claude" in body
    assert "Failed sync — summary failed: the transcript is too long for the window" in body
    assert "Reopened sync — The team planned the release." in body
    assert "a newer summary is waiting for the local model" in body


def _ledger_line(at: datetime, call: str, status: DisclosureStatus, cost: float | None) -> DisclosureRecord:
    usage = status is not DisclosureStatus.SENT
    return DisclosureRecord(
        at=at,
        task_class="meeting_summary",
        model="claude-sonnet-5-5",
        contributing_scopes=frozenset({PERSONAL}),
        input_tokens=1000 if usage else None,
        output_tokens=100 if usage else None,
        estimated_cost_usd=cost,
        destination=PERSONAL,
        call_id=call,
        status=status,
    )


def test_7d_the_spend_line_reads_the_month_from_the_ledger(tmp_path):
    """Matrix "Spend line": $21.40 this UTC month, one call unfinished — personal only."""
    built = daemon(tmp_path)
    for call, cost in (("c1", 20.0), ("c2", 1.4)):
        built.storage.append_disclosure(_ledger_line(NOW - timedelta(days=1), call, DisclosureStatus.SENT, None))
        built.storage.append_disclosure(_ledger_line(NOW - timedelta(days=1), call, DisclosureStatus.COMPLETED, cost))
    built.storage.append_disclosure(_ledger_line(NOW - timedelta(hours=1), "c3", DisclosureStatus.SENT, None))
    # Last month's call is not this month's.
    built.storage.append_disclosure(
        _ledger_line(datetime(2026, 8, 30, tzinfo=timezone.utc), "c0", DisclosureStatus.COMPLETED, 9.0)
    )
    notes = _page(built)["Leadership Notes"]
    assert (
        "Frontier spend this month (2026-09, UTC): $21.40 across 2 calls of known "
        "cost, against the $20.00 monitored target, and 1 call of unknown cost. "
        "Warning: the month is past the $20.00 target."
    ) in notes
    assert "Frontier spend" not in prepare_dashboard(built, scope=ALPHA, now=NOW).decode("utf-8")


def test_7d_an_unreadable_or_malformed_ledger_is_stated(tmp_path, monkeypatch):
    """Matrix "Ledger unreadable": the fault; or the total, then the malformed lines."""
    built = daemon(tmp_path)
    built.storage.append_disclosure(_ledger_line(NOW, "c1", DisclosureStatus.SENT, None))
    built.storage.append_disclosure(_ledger_line(NOW, "c1", DisclosureStatus.COMPLETED, 0.5))
    ledger = built.storage.paths.resolve(DataScope(ScopeKind.APPLICATION), "disclosure.md")
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write("this line is not a record\n")
    notes = _page(built)["Leadership Notes"]
    assert "$0.50 across 1 call of known cost" in notes
    assert "lines that could not be read, left out of the total:" in notes

    def unreadable() -> str:
        raise PermissionError(13, "Permission denied", "disclosure.md")

    monkeypatch.setattr(built.storage, "read_disclosure", unreadable)
    notes = _page(built)["Leadership Notes"]
    assert "This month's frontier spend could not be read from the disclosure ledger" in notes
    assert "Permission denied" in notes
    # The rest of the page still renders.
    assert "No synthesis runs" in notes


# Matrix "Model wired" is `tests/architecture/test_dashboard_has_no_model.py`'s
# render with summaries, records and open items present.


def test_7d_hostile_text_in_a_record_stays_one_bullet(tmp_path):
    """Matrix "Hostile text", end to end: a newline and `|` in a summary."""
    built = daemon(tmp_path)
    _summarized(built, "mtg_a", title="Sync | weekly", summary="first line\n- second | line")
    page = _page(built)
    assert list(page) == list(HEADINGS)
    (line,) = [line for line in page["Time-Critical Activities"].split("\n") if "Sync" in line]
    assert "Sync \\| weekly — first line - second \\| line" in line


# ── Story 7d, review: the boundaries the first build left unpinned ───────────

import sqlite3  # noqa: E402
from dataclasses import replace  # noqa: E402

from pm_ai.core.meeting_summary import action_item_prefix  # noqa: E402


def _confidential(iid: int, *assignees: GitLabPerson) -> Issue:
    return replace(_issue(iid, *assignees), title="confidential issue", confidential=True)


def _link(
    scope: DataScope, meeting_id: str, item: SummaryActionItem, iid: int, *, start: datetime
) -> str:
    """`7e`'s ledger line linking one staged item to issue `iid` by its spoken number."""
    return render_update(
        UpdateLine(
            kind=ItemKind.ACTION,
            meeting=meeting_id,
            start=start,
            title="Release sync",
            item=action_item_id(scope, meeting_id, item),
            issue=iid,
            by=LinkKind.NUMBER,
            text=item.what,
            at=NOW,
            owner=item.owner,
            due=item.due,
        )
    )


def test_7d_a_row_written_at_the_last_render_instant_is_not_listed_twice(tmp_path):
    """The window is `(since, now]`: a row written exactly at the previous render's
    instant was that render's to show, so the next does not repeat it."""
    built = daemon(tmp_path)
    last = NOW - timedelta(hours=3)
    built.storage.record_render(PERSONAL, at=last)
    _summarized(built, "mtg_edge", title="Edge sync", recorded=last)
    _summarized(built, "mtg_after", title="After sync", recorded=last + timedelta(seconds=1))
    body = _page(built)["Time-Critical Activities"]
    assert "After sync" in body
    assert "Edge sync" not in body


def test_7d_a_project_page_names_its_own_unreadable_summaries(tmp_path, monkeypatch):
    """Matrix "Project unreadable", on the project page itself: the failure is
    named, never rendered as a quiet window."""
    built = daemon(tmp_path)
    _summarized(built, "mtg_alpha", scope=ALPHA, title="Alpha planning")
    listing = built.storage.list_collection

    def broken(*, scope, artifact):
        if scope == ALPHA:
            raise PermissionError(13, "Permission denied", "meetings/")
        return listing(scope=scope, artifact=artifact)

    monkeypatch.setattr(built.storage, "list_collection", broken)
    body = _page(built, ALPHA)["Time-Critical Activities"]
    assert "No meeting summary was recorded in the scopes that could be read" in body
    assert "The meeting summaries of `project:alpha` could not be read" in body
    assert "Permission denied" in body


def test_7d_a_failed_enrolment_check_is_stated_narrowly(tmp_path, monkeypatch):
    """The check for projects no longer enrolled fails: only that is said, and the
    summaries that were read still show."""
    built = daemon(tmp_path)
    _summarized(built, "mtg_home", title="Career chat")

    def broken(**window):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(built.storage, "projects_with_summaries_between", broken)
    body = _page(built)["Time-Critical Activities"]
    assert "Career chat — The team planned the release." in body
    assert (
        "Whether a project no longer enrolled has meeting summaries in this window "
        "could not be checked: database is locked."
    ) in body
    assert "The meeting summaries could not be read" not in body


def test_7d_an_unreadable_alias_table_is_stated_on_the_project_page(tmp_path, monkeypatch):
    built = daemon(tmp_path)
    _alpha_work(built, _issue(7, _ANA))
    _summarized(
        built,
        "mtg_a",
        scope=ALPHA,
        items=(SummaryActionItem("rework the export", "Ваня"), SummaryActionItem("ship it", "Ana")),
    )

    def unreadable() -> bytes:
        raise PermissionError(13, "Permission denied", "aliases.md")

    monkeypatch.setattr(built.storage, "read_aliases", unreadable)
    body = _page(built, ALPHA)["Time-Critical Activities"]
    assert "  - unassigned: rework the export" in body
    assert "  - Ana: ship it" in body  # an exact display name still matches
    assert (
        "The saved alias table could not be read, so action item owners were matched"
    ) in body
    assert "Permission denied" in body


def test_7d_only_the_rows_team_activity_shows_are_left_out(tmp_path):
    """Three action updates on #142: Team Activity shows two (`MAX_ROWS_PER_ISSUE`),
    so the third stays in the meeting's list; one on an issue the page does not
    place stays too."""
    built = daemon(tmp_path)
    _alpha_work(built, _issue(142, _ANA))
    start = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)
    on_142 = tuple(SummaryActionItem(f"step {n}", "Ana") for n in range(3))
    elsewhere = SummaryActionItem("unplaced work", "Ana")
    _summarized(built, "mtg_a", scope=ALPHA, start=start, items=(*on_142, elsewhere))
    lines = tuple(_link(ALPHA, "mtg_a", item, 142, start=start) for item in on_142)
    lines += (_link(ALPHA, "mtg_a", elsewhere, 999, start=start),)
    built.storage.append_member_updates(lines, scope=ALPHA, after=0)

    page = _page(built, ALPHA)
    body = page["Time-Critical Activities"]
    assert "  - 2 open action items shown under #142 in Team Activity" in body
    listed = [f"step {n}" for n in range(3) if f"  - Ana: step {n}" in body]
    assert len(listed) == 1
    assert listed[0] not in page["Team Activity"]
    assert "  - Ana: unplaced work" in body


def test_7d_an_item_on_a_confidential_issue_never_shows_its_text(tmp_path):
    """Matrix "Confidential issue" (Andrei, 2026-10-20): placed or not, the text of
    an action item linked to a confidential issue is on the project page nowhere;
    it is listed as "an action item on a confidential issue" with its owner and
    due date. The personal page shows it as said."""
    built = daemon(tmp_path)
    _alpha_work(built, _confidential(142, _ANA), _confidential(143, _ANA))
    start = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)
    shown_row = SummaryActionItem("secret placed task", "Ana", "Thursday")
    past_cap = tuple(SummaryActionItem(f"secret task {n}", "Ana", "Friday") for n in range(3))
    _summarized(built, "mtg_a", scope=ALPHA, start=start, items=(shown_row, *past_cap))
    # An older meeting, outside `7e`'s 14-day window: still withheld.
    old_start = NOW - timedelta(days=30)
    old = SummaryActionItem("secret old task", "Ana")
    _summarized(
        built, "mtg_old", scope=ALPHA, title="Old sync", start=old_start, items=(old,),
        recorded=NOW - timedelta(hours=2),
    )
    lines = (_link(ALPHA, "mtg_a", shown_row, 142, start=start),)
    lines += tuple(_link(ALPHA, "mtg_a", item, 143, start=start) for item in past_cap)
    lines += (_link(ALPHA, "mtg_old", old, 143, start=old_start),)
    built.storage.append_member_updates(lines, scope=ALPHA, after=0)

    page = prepare_dashboard(built, scope=ALPHA, now=NOW).decode("utf-8")
    assert "secret" not in page
    body = sections(page)["Time-Critical Activities"].split("\n")
    # The rows Team Activity shows: one under #142, two under #143 (its cap).
    assert "  - 3 open action items shown under #142, #143 in Team Activity" in body
    assert body.count("  - Ana: an action item on a confidential issue (Friday)") == 1
    assert body.count("  - Ana: an action item on a confidential issue") == 1  # the old one
    personal = _page(built)["Time-Critical Activities"]
    assert "  - Ana: secret placed task (Thursday)" in personal


def test_7d_a_malformed_item_body_is_counted_and_the_meeting_still_shows(tmp_path):
    built = daemon(tmp_path)
    good = SummaryActionItem("send the deck", "Ana")
    bad = SummaryActionItem("this body breaks")
    _summarized(built, "mtg_a", items=(good, bad))
    db = built.storage._db  # noqa: SLF001 — a fixture's damage, not a code path
    db.execute(
        "UPDATE proposals SET body = ? WHERE proposal_id = ?",
        ("{not json", action_item_id(PERSONAL, "mtg_a", bad)),
    )
    db.commit()
    body = _page(built)["Time-Critical Activities"]
    assert "Release sync — The team planned the release." in body
    assert "  - Ana: send the deck" in body
    assert (
        "  - 1 open action item could not be read from the proposals store, so it is "
        "not listed"
    ) in body


def test_7d_a_non_mapping_payload_or_blank_text_still_renders(tmp_path):
    """A payload that is not a mapping, or a blank `what`, falls back to the
    proposal's summary — never `owner: ` with nothing after it."""
    built = daemon(tmp_path)
    _summarized(built, "mtg_a")
    for n, payload in enumerate((["a", "list"], {"what": "   ", "owner": "Ana"})):
        built.storage.stage_proposal(
            Proposal(
                proposal_id=action_item_id(PERSONAL, "mtg_a", SummaryActionItem(f"x{n}")),
                type="action_item",
                summary=f"fallback {n}",
                payload=payload,  # type: ignore[arg-type]
                target=None,
                cites=SourceRef.parse("meeting:mtg_a"),
                created_at=NOW,
            )
        )
    body = _page(built)["Time-Critical Activities"]
    assert "  - unassigned: fallback 0" in body
    assert "  - Ana: fallback 1" in body


def test_7d_open_items_of_a_meeting_whose_id_prefixes_another_stay_apart(tmp_path):
    """`mtg_a` and `mtg_ab`: each meeting's items are its own."""
    built = daemon(tmp_path)
    _summarized(built, "mtg_a", title="A sync", items=(SummaryActionItem("task for a"),))
    _summarized(built, "mtg_ab", title="AB sync", items=(SummaryActionItem("task for ab"),))
    for meeting_id, expected in (("mtg_a", "task for a"), ("mtg_ab", "task for ab")):
        found = built.storage.open_action_items(action_item_prefix(PERSONAL, meeting_id), at=NOW)
        assert [p.summary for p in found.items] == [expected]
        assert found.unreadable == 0


def test_7d_an_unreadable_gitlab_record_withholds_every_linked_item(tmp_path, monkeypatch):
    """Matrix "Confidentiality unknown" (Andrei, 2026-10-20): the project's GitLab
    record cannot be read, so whether #142 is confidential cannot be told; every
    item linked to an issue has its text withheld, and the page says why. An item
    linked to no issue is unaffected."""
    built = daemon(tmp_path)
    start = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)
    linked = SummaryActionItem("maybe secret task", "Ana", "Friday")
    free = SummaryActionItem("book the room", "Ana")
    _summarized(built, "mtg_a", scope=ALPHA, start=start, items=(linked, free))
    built.storage.append_member_updates(
        (_link(ALPHA, "mtg_a", linked, 142, start=start),), scope=ALPHA, after=0
    )

    def unreadable(scope):
        raise PermissionError(13, "Permission denied", "operational.db")

    monkeypatch.setattr(built.storage, "project_work", unreadable)
    page = prepare_dashboard(built, scope=ALPHA, now=NOW).decode("utf-8")
    assert "maybe secret task" not in page
    body = sections(page)["Time-Critical Activities"]
    # No record, so no members to resolve "Ana" to: unassigned, as 7e's rule says.
    assert "  - unassigned: an action item on a confidential issue (Friday)" in body
    assert "  - unassigned: book the room" in body
    assert (
        "Whether an issue is confidential cannot be told, since the project's GitLab "
        "record could not be read, so the text of every action item linked to an issue "
        "is withheld."
    ) in body


def test_7d_no_gitlab_record_also_withholds_linked_items(tmp_path):
    """No record harvested yet: confidentiality cannot be told either — fail closed."""
    built = daemon(tmp_path)
    start = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)
    linked = SummaryActionItem("maybe secret task", "Ana")
    _summarized(built, "mtg_a", scope=ALPHA, start=start, items=(linked,))
    built.storage.append_member_updates(
        (_link(ALPHA, "mtg_a", linked, 142, start=start),), scope=ALPHA, after=0
    )
    page = prepare_dashboard(built, scope=ALPHA, now=NOW).decode("utf-8")
    assert "maybe secret task" not in page
    assert "since no GitLab record is recorded for this project" in page
    # The personal page shows it as said.
    assert "maybe secret task" in _page(built)["Time-Critical Activities"]
