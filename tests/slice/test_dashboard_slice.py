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
    assert "Synthesis is not enabled" in body["Leadership Notes"]
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
) -> NormalizedEvent:
    """A message as `33d` writes one: a Graph sender handle, channel, excerpt."""
    return NormalizedEvent(
        scope=scope,
        type=ObservedEventType.MESSAGE_POSTED,
        source_ref=SourceRef.parse(f"graph:alpha:message:{native}"),
        actor=person_handle("graph", sender),
        occurred_at=NOW - timedelta(minutes=5),
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
        _persist(built, _message(native, excerpt=excerpt, mentions=mentions))
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
    assert lines == [f"- **12:30** graph:{OTHER_ID} in #payments — alpha ask — mentions you"]


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
