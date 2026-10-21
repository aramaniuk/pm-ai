"""Story 11e — `transcript drop` extracts what it can and asks nothing, one test per matrix row.

Every row runs `entry.main` against `test_transcript_drop`'s machine: a
temporary home, `alpha` enrolled at a real git repository, the keychain faked.
Unless a row says otherwise: `display_timezone = "Europe/Warsaw"`, `--scope
personal`, no record, an empty `aliases.md`, a local model that fails the test
if it is called, the file's modification time 2026-09-14T08:10:00Z, and no
`docProps/core.xml`. `input` fails the test if anything is asked.

Header variants, a `.docx` with a `docProps/core.xml` and the file times are
built here from `11d`'s recorded fixture; no line, property or time from a
real export is added.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from test_transcript_drop import FIXTURES, Keychain, Terminal, _daemon, _held, machine  # noqa: F401
from pm_ai.app import entry
from pm_ai.core.aliases import AliasRow, normalise, render_alias_row
from pm_ai.core.meeting_records import PARTICIPANTS_HEADING
from pm_ai.domain.handles import parse_person_handle
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.model_calls import ModelAnswer, ScopeFlow
from pm_ai.domain.sanitize import Sanitized
from pm_ai.domain.task_classes import TaskClass
from pm_ai.ports import LocalModelUnavailable
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, EXIT_USAGE

PERSONAL = DataScope(ScopeKind.PERSONAL)
MODIFIED = datetime(2026, 9, 14, 8, 10, tzinfo=timezone.utc)
DOCX = FIXTURES / "teams_export_recorded.docx"
MD = FIXTURES / "teams_export_recorded.md"
TITLE_LINE = "Example Weekly Sync-up-20260914_093000-Meeting Recording"
DATE_LINE = "14 September 2026, 09:30am"
LENGTH_LINE = "29m 03s"
UNKNOWN = ("14.09.2026 09:30", "Example Weekly Sync-up", "Duration: 29m 03s")
"""A header no fixed reader knows: a date in another layout first (so no title is
read from it), the title, and a length with a label."""
TOKYO = "Asia/Tokyo"


def _utc(hour: int, minute: int, day: int = 14) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


# ── The machine's state ──────────────────────────────────────────────────────


def _config(machine, text: str) -> None:
    daemon = _daemon(machine)
    target = Path(daemon.storage.paths.resolve(DataScope(ScopeKind.APPLICATION), "config.toml"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.fixture(autouse=True)
def machine_zone(monkeypatch):
    """The machine's own zone, fixed for every row: read only with no `display_timezone`."""
    from zoneinfo import ZoneInfo

    from pm_ai.app import pipelines

    monkeypatch.setattr(pipelines, "_machine_zone", lambda: ZoneInfo(TOKYO))


@pytest.fixture
def warsaw(machine):
    _config(machine, 'display_timezone = "Europe/Warsaw"\n')
    return machine


class Model:
    """A local model stand-in: records each call; fails the test unless told to answer."""

    def __init__(self, *answers: str | BaseException) -> None:
        self.answers = list(answers)
        self.calls: list[dict] = []

    def complete(self, **call):
        self.calls.append(call)
        if not self.answers:
            raise AssertionError("the local model was called")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return ModelAnswer(text=answer, model="fake", local=True, prompt_tokens=1, output_tokens=1)

    def close(self) -> None:
        pass


@pytest.fixture
def model(monkeypatch):
    """The default: a model that fails the test if it is called."""
    fake = Model()
    monkeypatch.setattr(entry, "local_model", lambda config: fake)
    return fake


def _answering(monkeypatch, *answers) -> Model:
    fake = Model(*answers)
    monkeypatch.setattr(entry, "local_model", lambda config: fake)
    return fake


def _alias(machine, said: str, handle: str) -> None:
    storage = _daemon(machine).storage
    row = AliasRow(
        name=normalise(said),
        said=said,
        handle=parse_person_handle(handle),
        meeting=None,
        at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    storage.append_alias(render_alias_row(row), after=len(storage.read_aliases()))


def _calendar(machine, meeting_id: str, start: datetime, *, title: str = "Example Weekly Sync-up"):
    _daemon(machine).meetings.put(
        Meeting(
            meeting_id=meeting_id,
            title=title,
            start=start,
            duration_minutes=30,
            attendees=(Actor("email:alex@example.com"),),
            scope=PERSONAL,
            calendar_event_ref=f"ref-{meeting_id}",
        )
    )


# ── Files ────────────────────────────────────────────────────────────────────


def _timed(path: Path, modified: datetime = MODIFIED) -> Path:
    os.utime(path, (modified.timestamp(), modified.timestamp()))
    return path


def _docx(
    tmp_path: Path,
    name: str = "export.docx",
    replace: dict[str, str] | None = None,
    created: str | None = None,
) -> Path:
    """The recorded fixture, its text replaced in place, with a `core.xml` when `created`."""
    source = zipfile.ZipFile(DOCX)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as built:
        for item in source.infolist():
            data = source.read(item)
            if item.filename == "word/document.xml":
                text = data.decode("utf-8")
                for old, new in (replace or {}).items():
                    assert text.count(old) == 1, old
                    text = text.replace(old, new)
                data = text.encode("utf-8")
            built.writestr(item, data)
        if created is not None:
            built.writestr(
                "docProps/core.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
                'metadata/core-properties" xmlns:dcterms="http://purl.org/dc/terms/">'
                f"<dcterms:created>{created}</dcterms:created></cp:coreProperties>",
            )
    path = tmp_path / "drops" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(buffer.getvalue())
    return _timed(path)


def _md(tmp_path: Path, name: str = "export.md", replace: dict[str, str] | None = None) -> Path:
    text = MD.read_text(encoding="utf-8")
    for old, new in (replace or {}).items():
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    path = tmp_path / "drops" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return _timed(path)


def _text(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / "drops" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return _timed(path)


def _drop(monkeypatch, path: Path, *options: str, scope: str = "personal") -> int:
    monkeypatch.setattr("builtins.input", Terminal([]))
    return entry.main(["transcript", "drop", str(path), "--scope", scope, *options])


def _minted(out: str) -> str:
    found = re.search(r"\b(mtg_[0-9a-f]+)\b", out)
    assert found, out
    return found.group(1)


def _record(machine, meeting_id: str):
    return _daemon(machine).meetings.get(meeting_id, scope=PERSONAL)


def _meetings_dir(machine) -> Path:
    return Path(_daemon(machine).storage.paths.resolve(PERSONAL, "meetings/"))


def _record_text(machine, meeting_id: str) -> str:
    (path,) = [p for p in _meetings_dir(machine).iterdir() if f"meeting_id={meeting_id}\n" in p.read_text()]
    return path.read_text(encoding="utf-8")


def _ok(capsys, code: int) -> str:
    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err + captured.out
    return captured.out


FIELDS = (
    "Title 'Example Weekly Sync-up', from the file header; "
    "start 2026-09-14T07:30:00+00:00 (2026-09-14 09:30 in Europe/Warsaw), estimated from "
    "the file header; 30 minutes, from the file header."
)
SPEAKERS = "Participants Alex Morgan (not linked), Sam Rivera (not linked)."


def _assert_the_fixture_record(machine, out: str) -> str:
    meeting_id = _minted(out)
    record = _record(machine, meeting_id)
    meeting = record.meeting
    assert meeting.title == "Example Weekly Sync-up"
    assert meeting.start == _utc(7, 30) and meeting.start_estimated_from == "header"
    assert meeting.duration_minutes == 30
    assert meeting.attendees == () and meeting.attendee_count == 2
    assert meeting.manual_only
    assert record.participants == ("Alex Morgan", "Sam Rivera")
    assert FIELDS in out and SPEAKERS in out
    return meeting_id


# ── The matrix ───────────────────────────────────────────────────────────────


def test_teams_docx_day_first(warsaw, model, tmp_path, monkeypatch, capsys):
    out = _ok(capsys, _drop(monkeypatch, _docx(tmp_path)))
    meeting_id = _assert_the_fixture_record(warsaw, out)
    assert model.calls == []
    text = _record_text(warsaw, meeting_id)
    assert f'{PARTICIPANTS_HEADING}\n- "Alex Morgan"\n- "Sam Rivera"\n' in text
    assert "start_estimated_from=header" in text


def test_md_conversion(warsaw, model, tmp_path, monkeypatch, capsys):
    out = _ok(capsys, _drop(monkeypatch, _md(tmp_path)))
    _assert_the_fixture_record(warsaw, out)
    assert model.calls == []


def test_month_first_date(warsaw, model, tmp_path, monkeypatch, capsys):
    path = _docx(tmp_path, replace={DATE_LINE: "September 14, 2026, 9:30AM"})
    out = _ok(capsys, _drop(monkeypatch, path))
    _assert_the_fixture_record(warsaw, out)


def test_through_a_pipe(warsaw, model, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    out = _ok(capsys, _drop(monkeypatch, _docx(tmp_path)))
    _assert_the_fixture_record(warsaw, out)


def test_title_from_the_file_name(warsaw, model, tmp_path, monkeypatch, capsys):
    path = _docx(
        tmp_path,
        name=f"{TITLE_LINE}.docx",
        replace={TITLE_LINE: "", DATE_LINE: "", LENGTH_LINE: ""},
    )
    out = _ok(capsys, _drop(monkeypatch, path))
    meeting = _record(warsaw, _minted(out)).meeting
    assert meeting.title == "Example Weekly Sync-up"
    assert "Title 'Example Weekly Sync-up', from the file name" in out
    # The last turn starts at 28:57, so 29 minutes; 08:10 minus 29 is 07:41.
    assert meeting.duration_minutes == 29 and meeting.start == _utc(7, 41)
    assert meeting.start_estimated_from == "file-modified"
    assert "estimated from the file modification time" in out
    assert model.calls == []


def test_title_with_a_stamp_in_other_words_is_read_by_the_model(
    warsaw, tmp_path, monkeypatch, capsys
):
    fake = _answering(
        monkeypatch,
        json.dumps({"title": "Example Weekly Sync-up", "start": None, "length_minutes": None}),
    )
    path = _md(tmp_path, replace={"Meeting Recording": "Nagranie"})
    out = _ok(capsys, _drop(monkeypatch, path))
    assert len(fake.calls) == 1
    assert "Title 'Example Weekly Sync-up', from the file header, read by the local model" in out
    assert _record(warsaw, _minted(out)).meeting.title == "Example Weekly Sync-up"


def test_a_model_title_not_in_the_header_is_refused(warsaw, tmp_path, monkeypatch, capsys):
    _answering(monkeypatch, json.dumps({"title": "Quarterly Review", "start": None,
                                        "length_minutes": None}))
    path = _md(tmp_path, name="sync-notes.md", replace={"Meeting Recording": "Nagranie"})
    out = _ok(capsys, _drop(monkeypatch, path))
    assert "Title 'sync-notes', from the file name" in out
    assert "Quarterly Review" not in out


def _unknown_layout(tmp_path: Path) -> Path:
    return _md(tmp_path, name="sync-notes.md", replace={
        f"{TITLE_LINE}\n\n{DATE_LINE}\n\n{LENGTH_LINE}\n": "\n\n".join(UNKNOWN) + "\n",
    })


def test_unknown_layout_the_model_reads_it(warsaw, tmp_path, monkeypatch, capsys):
    fake = _answering(monkeypatch, json.dumps({
        "title": "Example Weekly Sync-up", "start": "2026-09-14T09:30", "length_minutes": 30,
    }))
    out = _ok(capsys, _drop(monkeypatch, _unknown_layout(tmp_path)))
    (call,) = fake.calls
    assert call["task_class"] is TaskClass.HEADER_READING
    assert call["flow"] == ScopeFlow.of((PERSONAL,), PERSONAL)
    assert [fragment.raw for fragment in call["external"]] == list(UNKNOWN)
    meeting = _record(warsaw, _minted(out)).meeting
    assert "Title 'Example Weekly Sync-up', from the file header, read by the local model" in out
    assert meeting.start == _utc(7, 30) and meeting.start_estimated_from == "model-header"
    assert "estimated from the file header, read by the local model" in out
    # The length order puts the model before the last turn, so the model's 30
    # decides, labelled estimated (Andrei, 2026-10-21).
    assert meeting.duration_minutes == 30
    assert "30 minutes, estimated from the file header, read by the local model" in out


def _as_with_no_model(machine, out: str) -> None:
    meeting = _record(machine, _minted(out)).meeting
    assert meeting.title == "sync-notes"
    assert meeting.start == _utc(7, 41) and meeting.start_estimated_from == "file-modified"
    assert meeting.duration_minutes == 29
    assert "29 minutes, estimated from the last turn" in out


def test_unknown_layout_with_no_model(warsaw, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(entry, "local_model", lambda config: None)
    out = _ok(capsys, _drop(monkeypatch, _unknown_layout(tmp_path)))
    _as_with_no_model(warsaw, out)


@pytest.mark.parametrize(
    "answer",
    [LocalModelUnavailable("nothing on 127.0.0.1:11434"), '["not", "an", "object"]', "not json"],
    ids=["unavailable", "wrong-shape", "not-json"],
)
def test_unknown_layout_the_model_fails(answer, warsaw, tmp_path, monkeypatch, capsys):
    fake = _answering(monkeypatch, answer)
    out = _ok(capsys, _drop(monkeypatch, _unknown_layout(tmp_path)))
    assert len(fake.calls) == 1
    _as_with_no_model(warsaw, out)


def test_the_model_sees_only_the_header(warsaw, tmp_path, monkeypatch, capsys):
    fake = _answering(monkeypatch, json.dumps({"title": None, "start": None, "length_minutes": None}))
    _ok(capsys, _drop(monkeypatch, _unknown_layout(tmp_path)))
    (call,) = fake.calls
    assert all(isinstance(fragment, Sanitized) for fragment in call["external"])
    sent = " ".join(fragment.for_model for fragment in call["external"])
    assert "Placeholder" not in sent and "Alex Morgan" not in sent and "Тестовая" not in sent


def test_start_from_the_creation_time(warsaw, model, tmp_path, monkeypatch, capsys):
    path = _docx(tmp_path, created="2026-09-14T08:05:00Z")
    out = _ok(capsys, _drop(monkeypatch, path))
    meeting = _record(warsaw, _minted(out)).meeting
    assert meeting.start == _utc(7, 35) and meeting.start_estimated_from == "docx-created"
    assert "estimated from the docx creation time" in out


def test_a_creation_time_too_late_does_not_decide(warsaw, model, tmp_path, monkeypatch, capsys):
    path = _docx(tmp_path, created="2026-09-15T10:00:00Z")
    out = _ok(capsys, _drop(monkeypatch, path))
    meeting = _record(warsaw, _minted(out)).meeting
    assert meeting.start == _utc(7, 30) and meeting.start_estimated_from == "header"


def test_a_header_with_no_zone_is_read_in_the_machines_zone(
    machine, model, tmp_path, monkeypatch, capsys
):
    out = _ok(capsys, _drop(monkeypatch, _docx(tmp_path)))
    meeting = _record(machine, _minted(out)).meeting
    # 09:30 in Tokyo is 00:30 UTC.
    assert meeting.start == _utc(0, 30) and meeting.start_estimated_from == "header-machine-zone"
    assert "estimated from the file header, machine zone" in out


def test_a_header_with_no_zone_and_no_machine_zone(machine, model, tmp_path, monkeypatch, capsys):
    from pm_ai.app import pipelines

    monkeypatch.setattr(pipelines, "_machine_zone", lambda: None)
    out = _ok(capsys, _drop(monkeypatch, _docx(tmp_path)))
    meeting = _record(machine, _minted(out)).meeting
    # 08:10 minus the header's 30 minutes.
    assert meeting.start == _utc(7, 40) and meeting.start_estimated_from == "file-modified"


def test_start_from_a_calendar_record_the_same_day(warsaw, model, tmp_path, monkeypatch, capsys):
    _calendar(warsaw, "AAMk-weekly", _utc(7, 35), title="example weekly sync-up")
    out = _ok(capsys, _drop(monkeypatch, _docx(tmp_path)))
    assert "joined the existing meeting 'example weekly sync-up' (AAMk-weekly)" in out
    assert "found by title on the same day" in out
    assert "start 2026-09-14T07:35:00+00:00 (2026-09-14 09:35 in Europe/Warsaw), from the calendar" in out
    assert len([p for p in _meetings_dir(warsaw).iterdir() if p.suffix == ".md"]) == 1
    record = _record(warsaw, "AAMk-weekly")
    assert record.participants == ("Alex Morgan", "Sam Rivera")
    assert record.meeting.start == _utc(7, 35) and not record.meeting.manual_only


def test_a_calendar_day_in_utc(machine, model, tmp_path, monkeypatch, capsys):
    _calendar(machine, "AAMk-late", _utc(22, 30))
    out = _ok(capsys, _drop(monkeypatch, _docx(tmp_path)))
    assert "joined the existing meeting" in out and "AAMk-late" in out


def test_two_same_day_candidates_join_neither(warsaw, model, tmp_path, monkeypatch, capsys):
    _calendar(warsaw, "AAMk-one", _utc(7, 30))
    _calendar(warsaw, "AAMk-two", _utc(12, 0))
    path = _docx(tmp_path)
    assert _drop(monkeypatch, path, "--dry-run", "--json") == EXIT_OK
    found = json.loads(capsys.readouterr().out)
    assert found["outcome"] == {"kind": "new", "candidates": ["AAMk-one", "AAMk-two"]}
    out = _ok(capsys, _drop(monkeypatch, path))
    assert "2 meetings that day share its title (AAMk-one, AAMk-two)" in out
    assert _record(warsaw, _minted(out)).meeting.manual_only


def test_a_title_that_differs_matches_nothing(warsaw, model, tmp_path, monkeypatch, capsys):
    _calendar(warsaw, "AAMk-team", _utc(7, 30), title="Example Weekly Sync-up (Team)")
    out = _ok(capsys, _drop(monkeypatch, _docx(tmp_path)))
    assert "a new manual-only record" in out


def test_a_minted_record_is_not_a_candidate(warsaw, model, tmp_path, monkeypatch, capsys):
    _daemon(warsaw).meetings.put(Meeting(
        meeting_id="mtg_0000000000aaaaaaaaaa", title="Example Weekly Sync-up", start=_utc(7, 30),
        duration_minutes=30, attendees=(), scope=PERSONAL, manual_only=True,
    ))
    out = _ok(capsys, _drop(monkeypatch, _docx(tmp_path)))
    assert "a new manual-only record" in out and _minted(out) != "mtg_0000000000aaaaaaaaaa"


def test_an_option_start_binds_within_15_minutes(warsaw, model, tmp_path, monkeypatch, capsys):
    _calendar(warsaw, "AAMk-start", _utc(7, 30))
    out = _ok(capsys, _drop(monkeypatch, _docx(tmp_path), "--start", "2026-09-14T09:40+02:00"))
    assert "joined the existing meeting" in out and "found by --start" in out


def test_two_records_within_15_minutes_of_the_option_start_refuse(
    warsaw, model, tmp_path, monkeypatch, capsys
):
    _calendar(warsaw, "AAMk-one", _utc(7, 30))
    _calendar(warsaw, "AAMk-two", _utc(7, 45))
    before = _held(warsaw)
    code = _drop(monkeypatch, _docx(tmp_path), "--start", "2026-09-14T09:40+02:00")
    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "AAMk-one" in err and "AAMk-two" in err
    assert _held(warsaw) == before


def test_participants_linked_through_aliases(warsaw, model, tmp_path, monkeypatch, capsys):
    _alias(warsaw, "alex morgan", "email:alex@example.com")
    out = _ok(capsys, _drop(monkeypatch, _docx(tmp_path)))
    assert "Participants Alex Morgan (email:alex@example.com), Sam Rivera (not linked)." in out
    meeting = _record(warsaw, _minted(out)).meeting
    assert meeting.attendees == () and meeting.attendee_count == 2


def test_a_participant_near_miss_is_not_linked(warsaw, model, tmp_path, monkeypatch, capsys):
    _alias(warsaw, "Alex M.", "email:alex@example.com")
    out = _ok(capsys, _drop(monkeypatch, _docx(tmp_path)))
    assert SPEAKERS in out


def test_participants_and_attendees(warsaw, model, tmp_path, monkeypatch, capsys):
    _alias(warsaw, "alex morgan", "email:alex@example.com")
    out = _ok(capsys, _drop(
        monkeypatch, _docx(tmp_path), "--attendees", "email:alex@example.com,email:pat@example.com"
    ))
    meeting = _record(warsaw, _minted(out)).meeting
    assert meeting.attendees == (Actor("email:alex@example.com"), Actor("email:pat@example.com"))
    assert meeting.attendee_count == 3


def test_a_name_with_a_comma(warsaw, model, tmp_path, monkeypatch, capsys):
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n<v Rivera, Sam>we ship on Friday\n"
    out = _ok(capsys, _drop(monkeypatch, _text(tmp_path, "sync.vtt", vtt)))
    meeting_id = _minted(out)
    record = _record(warsaw, meeting_id)
    assert record.participants == ("Rivera, Sam",) and record.meeting.attendees == ()
    assert f'{PARTICIPANTS_HEADING}\n- "Rivera, Sam"\n' in _record_text(warsaw, meeting_id)


def test_length_unknown(warsaw, model, tmp_path, monkeypatch, capsys):
    out = _ok(capsys, _drop(monkeypatch, _text(tmp_path, "sync.txt", "Alex: we ship\nSam: agreed\n")))
    meeting_id = _minted(out)
    meeting = _record(warsaw, meeting_id).meeting
    assert meeting.duration_minutes is None and "duration_minutes" not in _record_text(warsaw, meeting_id)
    assert "length unknown" in out
    assert meeting.start == MODIFIED and meeting.start_estimated_from == "file-modified"
    assert meeting.man_hour_cost(100.0) is None


def test_the_header_length_is_used(warsaw, model, tmp_path, monkeypatch, capsys):
    out = _ok(capsys, _drop(monkeypatch, _md(tmp_path, replace={"28:57": "25:00:00"})))
    assert _record(warsaw, _minted(out)).meeting.duration_minutes == 30
    assert "30 minutes, from the file header" in out


@pytest.mark.parametrize("line", ["0:15am", "13:15pm"])
def test_an_hour_the_fixed_readers_refuse_falls_to_the_modification_time(
    line, warsaw, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(entry, "local_model", lambda config: None)
    path = _md(tmp_path, replace={DATE_LINE: f"14 September 2026, {line}"})
    out = _ok(capsys, _drop(monkeypatch, path))
    meeting = _record(warsaw, _minted(out)).meeting
    assert meeting.start == _utc(7, 40) and meeting.start_estimated_from == "file-modified"


def test_no_break_spaces_are_read(warsaw, model, tmp_path, monkeypatch, capsys):
    path = _docx(tmp_path, replace={DATE_LINE: "14 September 2026, 9:30 am"})
    out = _ok(capsys, _drop(monkeypatch, path))
    _assert_the_fixture_record(warsaw, out)


def test_a_date_like_first_line_is_never_the_title(warsaw, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(entry, "local_model", lambda config: None)
    path = _md(tmp_path, name="sync-notes.md", replace={
        f"{TITLE_LINE}\n\n{DATE_LINE}\n": f"{DATE_LINE}\n\n{TITLE_LINE}\n",
    })
    out = _ok(capsys, _drop(monkeypatch, path))
    meeting = _record(warsaw, _minted(out)).meeting
    assert meeting.title == "sync-notes"
    assert meeting.start == _utc(7, 30) and meeting.start_estimated_from == "header"


def test_two_start_lines_leave_the_start_unread(warsaw, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(entry, "local_model", lambda config: None)
    path = _md(tmp_path, replace={DATE_LINE: f"{DATE_LINE}\n\n14 September 2026, 10:30am"})
    out = _ok(capsys, _drop(monkeypatch, path))
    meeting = _record(warsaw, _minted(out)).meeting
    assert meeting.title == "Example Weekly Sync-up" and meeting.duration_minutes == 30
    assert meeting.start == _utc(7, 40) and meeting.start_estimated_from == "file-modified"


def test_md_marks_are_set_aside(warsaw, model, tmp_path, monkeypatch, capsys):
    path = _md(tmp_path, replace={
        TITLE_LINE: "# Example Weekly Sync-up-20260914\\_093000-Meeting Recording",
        DATE_LINE: f"**{DATE_LINE}**",
    })
    out = _ok(capsys, _drop(monkeypatch, path))
    _assert_the_fixture_record(warsaw, out)


def test_options_win(warsaw, model, tmp_path, monkeypatch, capsys):
    out = _ok(capsys, _drop(
        monkeypatch, _docx(tmp_path), "--title", "Other", "--start", "2026-09-14T10:00+02:00",
        "--duration", "45",
    ))
    meeting = _record(warsaw, _minted(out)).meeting
    assert meeting.title == "Other" and meeting.start == _utc(8, 0)
    assert meeting.duration_minutes == 45 and meeting.start_estimated_from is None
    assert "Title 'Other', given as an option" in out
    assert "2026-09-14T08:00:00+00:00 (2026-09-14 10:00 in Europe/Warsaw), given as an option" in out
    assert "45 minutes, given as an option" in out
    assert model.calls == []


def test_the_duration_option_wins_over_a_last_cue(warsaw, model, tmp_path, monkeypatch, capsys):
    vtt = "WEBVTT\n\n00:30:00.000 --> 00:30:30.000\n<v Alex>thanks all\n"
    out = _ok(capsys, _drop(monkeypatch, _text(tmp_path, "sync.vtt", vtt), "--duration", "45"))
    assert _record(warsaw, _minted(out)).meeting.duration_minutes == 45
    assert "45 minutes, given as an option" in out


def test_a_dry_run_in_text_writes_nothing(warsaw, model, tmp_path, monkeypatch, capsys):
    path = _docx(tmp_path)
    before = _held(warsaw)
    out = _ok(capsys, _drop(monkeypatch, path, "--dry-run"))
    assert _held(warsaw) == before
    assert "title: Example Weekly Sync-up — from the file header" in out
    assert "start: 2026-09-14T07:30:00+00:00 (2026-09-14 09:30 in Europe/Warsaw) — estimated from the file header" in out
    assert "length: 30 minutes — from the file header" in out
    assert "participants: Alex Morgan (not linked), Sam Rivera (not linked)" in out
    assert "outcome: new meeting" in out
    assert _daemon(warsaw).storage.transcript_states(PERSONAL) == {}


def test_a_dry_run_in_json_names_the_join(warsaw, model, tmp_path, monkeypatch, capsys):
    _calendar(warsaw, "AAMk-weekly", _utc(7, 35))
    before = _held(warsaw)
    found = json.loads(_ok(capsys, _drop(monkeypatch, _docx(tmp_path), "--dry-run", "--json")))
    assert _held(warsaw) == before
    assert found["start"] == {
        "value": "2026-09-14T07:35:00+00:00", "source": "calendar", "estimated": False
    }
    assert found["outcome"] == {
        "kind": "join", "meeting_id": "AAMk-weekly", "title": "Example Weekly Sync-up"
    }
    assert found["participants"] == [
        {"name": "Alex Morgan", "handle": None}, {"name": "Sam Rivera", "handle": None}
    ]


def test_a_dry_run_with_the_length_unknown(warsaw, model, tmp_path, monkeypatch, capsys):
    path = _text(tmp_path, "sync.txt", "Alex: we ship\nSam: agreed\n")
    found = json.loads(_ok(capsys, _drop(monkeypatch, path, "--dry-run", "--json")))
    assert found["duration_minutes"] == {"value": None, "source": "unknown", "estimated": False}


def test_json_alone_is_a_usage_error(warsaw, model, tmp_path, monkeypatch, capsys):
    before = _held(warsaw)
    assert _drop(monkeypatch, _docx(tmp_path), "--json") == EXIT_USAGE
    assert "--dry-run" in capsys.readouterr().err
    assert _held(warsaw) == before


def test_header_lines_are_never_utterances(warsaw, model, tmp_path, monkeypatch, capsys):
    from pm_ai.core.transcripts import read_transcript

    utterances = read_transcript(MD.read_text(encoding="utf-8"), name="fixture")
    said = {utterance.text for utterance in utterances}
    assert not said & {TITLE_LINE, DATE_LINE, LENGTH_LINE}


# ── Review follow-ups ────────────────────────────────────────────────────────


def test_a_calendar_record_on_the_hint_day_only_in_the_display_zone_joins(
    warsaw, model, tmp_path, monkeypatch, capsys
):
    # 22:30 UTC on the 13th is 00:30 on the 14th in Warsaw, the header's day.
    _calendar(warsaw, "AAMk-midnight", datetime(2026, 9, 13, 22, 30, tzinfo=timezone.utc))
    out = _ok(capsys, _drop(monkeypatch, _docx(tmp_path)))
    assert "joined the existing meeting" in out and "AAMk-midnight" in out


def test_the_same_file_dropped_again_is_refused_by_the_capture_rule(
    warsaw, model, tmp_path, monkeypatch, capsys
):
    path = _docx(tmp_path)
    meeting_id = _minted(_ok(capsys, _drop(monkeypatch, path)))
    before = _held(warsaw)
    found = json.loads(_ok(capsys, _drop(monkeypatch, path, "--dry-run", "--json")))
    assert found["outcome"]["kind"] == "refused" and meeting_id in found["outcome"]["reason"]
    assert _drop(monkeypatch, path) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert meeting_id in err and "already has a capture" in err and "--meeting" in err
    assert _held(warsaw) == before


def test_a_meeting_drop_writes_the_participants_and_names_them(
    warsaw, model, tmp_path, monkeypatch, capsys
):
    _calendar(warsaw, "AAMk-named", _utc(7, 30))
    out = _ok(capsys, _drop(monkeypatch, _docx(tmp_path), "--meeting", "AAMk-named"))
    assert SPEAKERS in out
    assert _record(warsaw, "AAMk-named").participants == ("Alex Morgan", "Sam Rivera")
    assert model.calls == []


def test_a_dry_run_for_a_meeting_that_does_not_exist_is_refused_with_exit_0(
    warsaw, model, tmp_path, monkeypatch, capsys
):
    found = json.loads(_ok(capsys, _drop(
        monkeypatch, _docx(tmp_path), "--meeting", "mtg_absent", "--dry-run", "--json"
    )))
    assert found["outcome"]["kind"] == "refused" and "mtg_absent" in found["outcome"]["reason"]


def test_a_dry_run_for_a_named_meeting_shows_its_own_fields(
    warsaw, model, tmp_path, monkeypatch, capsys
):
    _calendar(warsaw, "AAMk-named", _utc(7, 30))
    found = json.loads(_ok(capsys, _drop(
        monkeypatch, _docx(tmp_path), "--meeting", "AAMk-named", "--dry-run", "--json"
    )))
    assert found["outcome"] == {
        "kind": "join", "meeting_id": "AAMk-named", "title": "Example Weekly Sync-up"
    }
    assert found["duration_minutes"] == {"value": 30, "source": "calendar", "estimated": False}


def test_a_dry_run_with_two_records_matching_the_option_start_is_refused(
    warsaw, model, tmp_path, monkeypatch, capsys
):
    _calendar(warsaw, "AAMk-one", _utc(7, 30))
    _calendar(warsaw, "AAMk-two", _utc(7, 45))
    out = _ok(capsys, _drop(
        monkeypatch, _docx(tmp_path), "--start", "2026-09-14T09:40+02:00", "--dry-run"
    ))
    assert "outcome: refused" in out and "AAMk-one" in out and "AAMk-two" in out


def test_a_dry_run_for_a_meeting_that_has_a_capture_is_refused(
    warsaw, model, tmp_path, monkeypatch, capsys
):
    _calendar(warsaw, "AAMk-weekly", _utc(7, 35))
    _ok(capsys, _drop(monkeypatch, _docx(tmp_path)))
    found = json.loads(_ok(capsys, _drop(
        monkeypatch, _docx(tmp_path, name="again.docx"), "--dry-run", "--json"
    )))
    assert found["outcome"]["kind"] == "refused"
    assert "already has a capture" in found["outcome"]["reason"]


def test_a_speaker_name_the_record_cannot_hold_is_left_out_with_a_notice(
    warsaw, model, tmp_path, monkeypatch, capsys
):
    vtt = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n<v A\x01lice>we ship\n\n"
        "00:00:06.000 --> 00:00:09.000\n<v Sam Rivera>agreed\n"
    )
    code = _drop(monkeypatch, _text(tmp_path, "sync.vtt", vtt))
    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "left out" in captured.err and "\x01" not in captured.out
    assert _record(warsaw, _minted(captured.out)).participants == ("Sam Rivera",)


def test_an_alias_table_that_cannot_be_read_links_no_one_and_says_so(
    warsaw, model, tmp_path, monkeypatch, capsys
):
    from pm_ai.storage.service import StorageService

    def unreadable(self):
        raise PermissionError(13, "Permission denied", "aliases.md")

    monkeypatch.setattr(StorageService, "read_aliases", unreadable)
    code = _drop(monkeypatch, _docx(tmp_path))
    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "aliases.md could not be read" in captured.err and SPEAKERS in captured.out


def test_the_creation_time_decides_with_no_header_date(
    warsaw, model, tmp_path, monkeypatch, capsys
):
    path = _docx(
        tmp_path,
        name=f"{TITLE_LINE}.docx",
        replace={TITLE_LINE: "", DATE_LINE: "", LENGTH_LINE: ""},
        created="2026-09-14T08:05:00Z",
    )
    out = _ok(capsys, _drop(monkeypatch, path))
    meeting = _record(warsaw, _minted(out)).meeting
    # 08:05 minus the last turn's 29 minutes.
    assert meeting.start == _utc(7, 36) and meeting.start_estimated_from == "docx-created"


def test_an_option_attendee_in_another_case_is_one_person(
    warsaw, model, tmp_path, monkeypatch, capsys
):
    _alias(warsaw, "alex morgan", "email:alex@example.com")
    out = _ok(capsys, _drop(monkeypatch, _docx(tmp_path), "--attendees", "email:Alex@Example.com"))
    assert _record(warsaw, _minted(out)).meeting.attendee_count == 2


# ── Andrei's decisions of 2026-10-21 ─────────────────────────────────────────


def test_a_day_from_the_modification_time_alone_never_joins(
    warsaw, model, tmp_path, monkeypatch, capsys
):
    _calendar(warsaw, "AAMk-weekly", _utc(7, 30))
    vtt = "WEBVTT\n\n00:28:00.000 --> 00:29:00.000\n<v Alex Morgan>thanks all\n"
    path = _text(tmp_path, "Example Weekly Sync-up.vtt", vtt)
    found = json.loads(_ok(capsys, _drop(monkeypatch, path, "--dry-run", "--json")))
    assert found["outcome"] == {"kind": "new", "candidates": []}
    out = _ok(capsys, _drop(monkeypatch, path))
    assert "its day came only from the file's modification time, so no existing meeting was joined" in out
    meeting = _record(warsaw, _minted(out)).meeting
    assert meeting.manual_only and meeting.start_estimated_from == "file-modified"


# In a speaker-and-time file a line ending in a time, `Standup 10:00`, is a turn by
# `11d`'s rule (an accepted limit), so the header row's time is not last here; the
# exact example is a row of `test_transcript_details.py`.
@pytest.mark.parametrize("title", ["Q3 2026 planning", "Standup 10:00 (weekly)"])
def test_a_title_holding_a_year_or_a_time_stays_the_title(
    title, warsaw, model, tmp_path, monkeypatch, capsys
):
    out = _ok(capsys, _drop(monkeypatch, _md(tmp_path, replace={TITLE_LINE: title})))
    meeting = _record(warsaw, _minted(out)).meeting
    assert meeting.title == title
    assert meeting.start == _utc(7, 30) and meeting.start_estimated_from == "header"


@pytest.mark.parametrize(
    "start", ["2026-08-01T09:30", "2026-09-20T09:30"], ids=["over-30-days-before", "after-the-file"]
)
def test_a_model_start_outside_the_files_times_is_refused(
    start, warsaw, tmp_path, monkeypatch, capsys
):
    _answering(monkeypatch, json.dumps({
        "title": "Example Weekly Sync-up", "start": start, "length_minutes": 30,
    }))
    out = _ok(capsys, _drop(monkeypatch, _unknown_layout(tmp_path)))
    meeting = _record(warsaw, _minted(out)).meeting
    assert meeting.start_estimated_from == "file-modified"
    assert meeting.start == _utc(7, 40)


def test_a_zoom_like_header_takes_the_models_title(warsaw, tmp_path, monkeypatch, capsys):
    """The fixed readers take the first line and miss the start, so the layout is
    unknown and the model's validated title wins (2026-10-21)."""
    fake = _answering(monkeypatch, json.dumps(
        {"title": "Budget review", "start": None, "length_minutes": None}
    ))
    path = _md(tmp_path, name="zoom.md", replace={
        f"{TITLE_LINE}\n\n{DATE_LINE}\n\n{LENGTH_LINE}\n":
            "Zoom Meeting Transcript\n\nTopic: Budget review\n\nDate: Oct 15, 2026 3:00 PM\n",
    })
    out = _ok(capsys, _drop(monkeypatch, path))
    assert len(fake.calls) == 1
    assert _record(warsaw, _minted(out)).meeting.title == "Budget review"
    assert "Title 'Budget review', from the file header, read by the local model" in out


def test_with_the_start_and_length_read_the_fixed_title_stands(
    warsaw, tmp_path, monkeypatch, capsys
):
    fake = _answering(monkeypatch, json.dumps(
        {"title": "Weekly", "start": None, "length_minutes": None}
    ))
    path = _md(tmp_path, replace={"Meeting Recording": "Nagranie"})
    out = _ok(capsys, _drop(monkeypatch, path))
    # The stamp in other words leaves only the title unread: the model fills it.
    assert len(fake.calls) == 1 and "Title 'Weekly'" in out
    fixed = _answering(monkeypatch)
    out = _ok(capsys, _drop(monkeypatch, _md(tmp_path, name="again.md")))
    assert fixed.calls == [] and "Title 'Example Weekly Sync-up', from the file header" in out
