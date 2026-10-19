"""Story 11c — `pm-ai transcript drop`, one test per matrix row, through `main()`.

Every row runs `entry.main` against a temporary home and a real git repository
enrolled as `alpha`, so the git exclusion check, the capture writer, the
meeting records and the operational store are the real ones. Only the keychain
is faked, and the terminal: `input` is replaced, and records every prompt, so
"nothing asked" and "asked again" are assertions about what was asked.

Every "nothing written" row lists `meetings/` and `transcripts/` in both trees
before and after, and asserts they are unchanged.

Rows that need no pipeline (the command line, the path checks, the questions'
re-asking) are also in `tests/surfaces/test_cli_subcommands.py`.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pm_ai.app import entry, pipelines
from pm_ai.app.wiring import onboard_project
from pm_ai.core.docx_text import docx_text
from pm_ai.core.meeting_records import MalformedMeeting, MeetingRecords, capture_name
from pm_ai.core.transcripts import read_transcript
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.transcripts import CaptureFormat, TranscriptStateKind
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import KeyNotFound
from pm_ai.storage.service import OperationalStoreBusy, ProposalNotFound, StorageService
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, EXIT_UNEXPECTED, EXIT_USAGE

PERSONAL = DataScope(ScopeKind.PERSONAL)
ALPHA = DataScope(ScopeKind.PROJECT, "alpha")
PM_USER_ID = "0d9e3c4f-1a2b-4c3d-8e9f-a0b1c2d3e4f5"
PM_HANDLE = f"graph:{PM_USER_ID}"
COMMAND = "pm-ai, post_comment gitlab:alpha:issue:102 ship it"

SPOKEN_VTT = (
    "WEBVTT\n\n"
    "00:00:01.000 --> 00:00:05.000\n"
    f"<v {PM_HANDLE}>{COMMAND}\n\n"
    "00:29:50.000 --> 00:30:04.500\n"
    "<v Alex>thanks all\n"
)
"""Two cues; the last ends at 30:04.5, so the duration is 31 minutes."""

SAID_TXT = "Andrei: we ship on Friday\nAlex: agreed\n"
FOUR_FORMATS = ".vtt, .txt, .md, .docx"
FIXTURES = Path(__file__).parents[1] / "core" / "fixtures" / "transcripts"
START_TYPED = "2026-10-10T14:00+03:00"
START = datetime(2026, 10, 10, 11, 0, tzinfo=timezone.utc)


class Keychain:
    def __init__(self) -> None:
        self._secret: bytes | None = b"\x00" * 32

    def store(self, name: str, secret: bytes) -> None:
        self._secret = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        if self._secret is None:
            self._secret = secret

    def fetch(self, name: str) -> bytes:
        if self._secret is None:
            raise KeyNotFound(name)
        return self._secret

    def delete(self, name: str) -> None:
        self._secret = None


class Terminal:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"asked {prompt!r} with no answer left")
        return self.answers.pop(0)


def _git(repository: Path, *words: str) -> None:
    subprocess.run(["git", *words], cwd=repository, check=True, capture_output=True)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A home, a key, `alpha` enrolled at a git repository, and the PM's row."""
    keychain = Keychain()
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: keychain)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repository = tmp_path / "alpha"
    repository.mkdir()
    for words in (("init", "-q"), ("config", "user.email", "t@t"), ("config", "user.name", "t")):
        _git(repository, *words)
    onboard_project(keychain, str(repository), "alpha")
    # The PM's Graph row, disabled so no connector is built from it; a
    # disabled row still names the PM (AD-34).
    connectors = home / ".pm-ai" / "connectors"
    connectors.mkdir(parents=True, exist_ok=True)
    (connectors / "graph:work.json").write_text(
        json.dumps(
            {"instance": "graph:work", "system": "graph", "enabled": False, "user_id": PM_USER_ID}
        )
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    return home, repository, keychain


def _daemon(machine):
    home, repository, keychain = machine
    paths = ScopePaths.production(projects={"alpha": repository})
    return entry.build(None, "alpha", paths=paths, keychain=keychain)


def _file(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / "drops" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _drop(monkeypatch, path: Path, *options: str, answers: list[str] = ()) -> tuple[int, Terminal]:
    terminal = Terminal(list(answers))
    monkeypatch.setattr("builtins.input", terminal)
    return entry.main(["transcript", "drop", str(path), *options]), terminal


def _held(machine) -> dict[str, list[str]]:
    """Every file under `meetings/` and `transcripts/`, in both trees."""
    home, repository, _ = machine
    found: dict[str, list[str]] = {}
    for label, root in (("personal", home / ".manager-ai"), ("alpha", repository / ".project-ai")):
        for part in ("meetings", "transcripts"):
            found[f"{label}:{part}"] = sorted(
                str(path.relative_to(root))
                for path in root.rglob("*")
                if path.is_file() and part in path.relative_to(root).parts
                and not path.name.endswith(".lock")
            )
    return found


def _record(machine, meeting: Meeting) -> None:
    _daemon(machine).meetings.put(meeting)


def _meeting(meeting_id: str, *, title: str = "Weekly Sync", start: datetime = START,
             scope: DataScope = ALPHA) -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title=title,
        start=start,
        duration_minutes=30,
        attendees=(Actor("email:alex@example.com"),),
        scope=scope,
    )


def _minted(out: str) -> str:
    found = re.search(r"\b(mtg_[0-9a-f]+)\b", out)
    assert found, out
    return found.group(1)


def _proposal(machine, meeting_id: str, index: int = 0):
    return _daemon(machine).storage.load_proposal(f"prp_{meeting_id}_manual_{index}")


# ── Acceptance ───────────────────────────────────────────────────────────────


def test_a_vtt_matching_no_record_mints_one_captures_and_stages_one(
    machine, tmp_path, monkeypatch, capsys
):
    home, repository, _ = machine
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)

    code, terminal = _drop(
        monkeypatch, path, "--scope", "project:alpha",
        answers=["Weekly Sync", START_TYPED, "email:alex@example.com"],
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    meeting_id = _minted(out)
    assert not any(p.startswith("Duration") for p in terminal.prompts)
    record = _daemon(machine).meetings.get(meeting_id, scope=ALPHA).meeting
    assert record.manual_only and record.start == START and record.duration_minutes == 31
    capture = capture_name(meeting_id, START, CaptureFormat.MANUAL_VTT)
    assert (repository / ".project-ai" / "transcripts" / capture).read_text() == SPOKEN_VTT
    assert str(repository / ".project-ai" / "transcripts" / capture) in out
    proposal = _proposal(machine, meeting_id)
    assert proposal.state is ProposalState.STAGED
    assert str(proposal.cites) == f"meeting:{meeting_id}"
    with pytest.raises(ProposalNotFound):
        _proposal(machine, meeting_id, 1)
    assert "1 staged" in out and "executed" not in out
    assert "31 minutes, taken from the last cue's end" in out
    states = _daemon(machine).storage.transcript_states(ALPHA)
    assert states[meeting_id].state == TranscriptStateKind.INGESTED.value


def test_the_same_drop_again_is_refused_naming_the_capture(machine, tmp_path, monkeypatch, capsys):
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)
    answers = ["Weekly Sync", START_TYPED, "email:alex@example.com"]
    first, _ = _drop(monkeypatch, path, "--scope", "project:alpha", answers=answers)
    meeting_id = _minted(capsys.readouterr().out)
    assert first == EXIT_OK
    before = _held(machine)

    again, _ = _drop(monkeypatch, path, "--scope", "project:alpha", answers=answers[:2])

    assert again == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert capture_name(meeting_id, START, CaptureFormat.MANUAL_VTT) in err
    assert _held(machine) == before
    with pytest.raises(ProposalNotFound):
        _proposal(machine, meeting_id, 1)


def test_a_transcripts_directory_git_would_commit_refuses_with_gits_reason(
    machine, tmp_path, monkeypatch, capsys
):
    _, repository, _ = machine
    tracked = repository / ".project-ai" / "transcripts" / "kept.txt"
    tracked.parent.mkdir(parents=True, exist_ok=True)
    tracked.write_text("committed earlier\n")
    _git(repository, "add", "-f", str(tracked))
    _git(repository, "commit", "-q", "-m", "a capture committed by mistake")
    before = _held(machine)
    path = _file(tmp_path, "sync.txt", SAID_TXT)

    code, _ = _drop(
        monkeypatch, path, "--scope", "project:alpha",
        answers=["Weekly Sync", START_TYPED, "email:alex@example.com", "30"],
    )

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "transcripts" in err and "git" in err
    assert _held(machine) == before


# ── The matrix ───────────────────────────────────────────────────────────────


def test_a_named_meeting_binds_without_asking_or_a_terminal(machine, tmp_path, monkeypatch, capsys):
    _, repository, _ = machine
    _record(machine, _meeting("mtg_named"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)

    code, terminal = _drop(monkeypatch, path, "--scope", "project:alpha", "--meeting", "mtg_named")

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert terminal.prompts == []
    assert (repository / ".project-ai" / "transcripts"
            / capture_name("mtg_named", START, CaptureFormat.MANUAL_VTT)).is_file()
    assert str(_proposal(machine, "mtg_named").cites) == "meeting:mtg_named"
    assert "minutes" not in out


def test_a_named_meeting_absent_is_refused_naming_id_and_scope(
    machine, tmp_path, monkeypatch, capsys
):
    before = _held(machine)
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)

    code, _ = _drop(monkeypatch, path, "--scope", "project:alpha", "--meeting", "mtg_absent")

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "mtg_absent" in err and "alpha" in err
    assert _held(machine) == before


def test_a_title_and_start_matching_one_record_bind_without_more_questions(
    machine, tmp_path, monkeypatch, capsys
):
    _record(machine, _meeting("mtg_weekly", title="weekly  sync"))
    meetings_before = _held(machine)["alpha:meetings"]
    path = _file(tmp_path, "sync.txt", SAID_TXT)

    code, terminal = _drop(
        monkeypatch, path, "--scope", "project:alpha",
        answers=["Weekly Sync", "2026-10-10T14:07+03:00"],
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert len(terminal.prompts) == 2
    assert "mtg_weekly" in out
    assert _held(machine)["alpha:meetings"] == meetings_before
    states = _daemon(machine).storage.transcript_states(ALPHA)
    assert states["mtg_weekly"].state == TranscriptStateKind.INGESTED.value


def test_two_candidates_refuse_naming_both(machine, tmp_path, monkeypatch, capsys):
    _record(machine, _meeting("mtg_one"))
    _record(machine, _meeting("mtg_two", start=START.replace(minute=10)))
    before = _held(machine)
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)

    code, terminal = _drop(
        monkeypatch, path, "--scope", "project:alpha", answers=["Weekly Sync", START_TYPED]
    )

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "mtg_one" in err and "mtg_two" in err
    assert len(terminal.prompts) == 2
    assert _held(machine) == before


def test_a_txt_matching_nothing_asks_the_duration_and_says_so(
    machine, tmp_path, monkeypatch, capsys
):
    path = _file(tmp_path, "sync.txt", SAID_TXT)

    code, terminal = _drop(
        monkeypatch, path, "--scope", "project:alpha",
        answers=["Planning", START_TYPED, "email:alex@example.com,email:bo@example.com", "45"],
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    meeting_id = _minted(out)
    assert any(p.startswith("Duration") for p in terminal.prompts)
    record = _daemon(machine).meetings.get(meeting_id, scope=ALPHA).meeting
    assert record.duration_minutes == 45 and record.manual_only
    assert record.attendees == (Actor("email:alex@example.com"), Actor("email:bo@example.com"))
    assert "45 minutes, as answered" in out


def test_no_match_without_a_terminal_is_refused_before_anything_is_written(
    machine, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    before = _held(machine)
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)

    code, terminal = _drop(monkeypatch, path, "--scope", "project:alpha")

    assert code == EXIT_REFUSAL
    assert "terminal" in capsys.readouterr().err
    assert terminal.prompts == []
    assert _held(machine) == before


def test_answers_the_questions_refuse_are_asked_again(machine, tmp_path, monkeypatch, capsys):
    path = _file(tmp_path, "sync.txt", SAID_TXT)

    code, terminal = _drop(
        monkeypatch, path, "--scope", "project:alpha",
        answers=[
            "Planning", "2026-10-09 14:00", START_TYPED,
            "Smith, Bob", "email:bo@example.com", "30",
        ],
    )

    assert code == EXIT_OK
    err = capsys.readouterr().err
    assert "2026-10-10T14:00+03:00" in err and "separated by commas with no spaces" in err
    assert sum(p.startswith("Meeting start") for p in terminal.prompts) == 2
    assert sum(p.startswith("No recorded meeting") for p in terminal.prompts) == 2


def test_a_personal_meeting_dropped_from_inside_a_project_lands_personally(
    machine, tmp_path, monkeypatch, capsys
):
    home, repository, _ = machine
    monkeypatch.chdir(repository)
    project_before = {k: v for k, v in _held(machine).items() if k.startswith("alpha")}
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)

    code, _ = _drop(
        monkeypatch, path, "--scope", "personal",
        answers=["Coaching", START_TYPED, ""],
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    meeting_id = _minted(out)
    assert _daemon(machine).meetings.get(meeting_id, scope=PERSONAL).meeting.scope == PERSONAL
    held = _held(machine)
    stored = capture_name(meeting_id, START, CaptureFormat.MANUAL_VTT)
    assert held["personal:transcripts"] == [f"transcripts/{stored}"]
    assert {k: v for k, v in held.items() if k.startswith("alpha")} == project_before


@pytest.mark.parametrize("existing", [CaptureFormat.GRAPH, CaptureFormat.MANUAL_TXT])
def test_a_meeting_that_already_has_a_capture_refuses_naming_it(
    existing, machine, tmp_path, monkeypatch, capsys
):
    _record(machine, _meeting("mtg_held"))
    name = capture_name("mtg_held", START, existing)
    _daemon(machine).storage.write_capture(SAID_TXT, scope=ALPHA, name=name)
    before = _held(machine)
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)

    code, _ = _drop(monkeypatch, path, "--scope", "project:alpha", "--meeting", "mtg_held")

    assert code == EXIT_REFUSAL
    assert name in capsys.readouterr().err
    assert _held(machine) == before
    with pytest.raises(ProposalNotFound):
        _proposal(machine, "mtg_held")


def test_a_repository_git_cannot_be_asked_about_is_refused(machine, tmp_path, monkeypatch, capsys):
    """git unreachable: the repository is there and no `git` is on PATH."""
    before = _held(machine)
    path = _file(tmp_path, "sync.txt", SAID_TXT)
    empty = tmp_path / "no-git-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    code, _ = _drop(
        monkeypatch, path, "--scope", "project:alpha",
        answers=["Planning", START_TYPED, "", "30"],
    )

    assert code == EXIT_REFUSAL
    assert "git could not be consulted" in capsys.readouterr().err
    assert _held(machine) == before


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("header.vtt", "WEBVTT\n\n"),
        ("silent.vtt", "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Alex></v>\n\n"
                       "00:00:03.000 --> 00:00:04.000\n   \n"),
        ("blank.txt", "  \n\n\t\n"),
    ],
    ids=["header-only", "empty-payloads", "whitespace"],
)
def test_a_file_that_reads_to_no_utterance_is_refused_before_the_capture(
    name, text, machine, tmp_path, monkeypatch, capsys
):
    before = _held(machine)
    path = _file(tmp_path, name, text)

    code, terminal = _drop(monkeypatch, path, "--scope", "project:alpha")

    assert code == EXIT_REFUSAL
    assert name in capsys.readouterr().err
    assert terminal.prompts == []
    assert _held(machine) == before


def test_a_file_neither_reader_accepts_is_refused_naming_it(machine, tmp_path, monkeypatch, capsys):
    before = _held(machine)
    path = _file(tmp_path, "notes.txt", "just some notes\nno speakers here\nat all\n")

    code, _ = _drop(monkeypatch, path, "--scope", "project:alpha")

    assert code == EXIT_REFUSAL
    assert "notes.txt" in capsys.readouterr().err
    assert _held(machine) == before


@pytest.mark.parametrize(
    ("name", "said"),
    [("minutes.doc", "old .doc format"), ("minutes.pdf", "a PDF"), ("minutes.rtf", "rich-text")],
)
def test_an_old_word_or_pdf_document_is_refused_naming_the_four_formats(
    name, said, machine, tmp_path, monkeypatch, capsys
):
    """Story 11d: `.docx` is read now; `.doc`, `.pdf` and `.rtf` are still refused by name."""
    before = _held(machine)
    path = tmp_path / name
    path.write_bytes(b"PK\x03\x04")
    code, terminal = _drop(monkeypatch, path, "--scope", "project:alpha")
    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert said in err and str(path) in err and FOUR_FORMATS in err
    assert terminal.prompts == []
    assert _held(machine) == before


def test_a_txt_holding_webvtt_is_read_as_webvtt_and_stored_as_txt(
    machine, tmp_path, monkeypatch, capsys
):
    _, repository, _ = machine
    _record(machine, _meeting("mtg_named"))
    path = _file(tmp_path, "sync.txt", SPOKEN_VTT)

    code, _ = _drop(monkeypatch, path, "--scope", "project:alpha", "--meeting", "mtg_named")

    assert code == EXIT_OK
    stored = repository / ".project-ai" / "transcripts" / capture_name(
        "mtg_named", START, CaptureFormat.MANUAL_TXT
    )
    assert stored.read_text() == SPOKEN_VTT
    proposal = _proposal(machine, "mtg_named")
    assert proposal.target is not None and "102" in str(proposal.target)


def test_the_pm_speaking_a_reversible_command_is_staged_never_executed(
    machine, tmp_path, monkeypatch, capsys
):
    _record(machine, _meeting("mtg_named"))
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)

    code, _ = _drop(monkeypatch, path, "--scope", "project:alpha", "--meeting", "mtg_named")

    assert code == EXIT_OK
    proposal = _proposal(machine, "mtg_named")
    assert proposal.state is ProposalState.STAGED
    assert proposal.target is not None and "102" in str(proposal.target)
    out = capsys.readouterr().out
    assert "1 staged" in out and "executed" not in out


def test_an_ingestion_that_fails_after_the_capture_keeps_and_names_it(
    machine, tmp_path, monkeypatch, capsys
):
    _, repository, _ = machine
    meetings_before = _held(machine)["alpha:meetings"]

    def refuse(self, meeting):
        raise MalformedMeeting("the record write refused")

    original = MeetingRecords.put
    monkeypatch.setattr(MeetingRecords, "put", refuse)
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)

    code, _ = _drop(
        monkeypatch, path, "--scope", "project:alpha",
        answers=["Weekly Sync", START_TYPED, ""],
    )

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    stored = list((repository / ".project-ai" / "transcripts").glob("*-manual.vtt"))
    assert len(stored) == 1 and str(stored[0]) in err
    assert "MalformedMeeting" in err
    assert "dropping again mints a new meeting id" in err and "by hand" in err
    assert "next run" not in err
    monkeypatch.setattr(MeetingRecords, "put", original)
    assert _held(machine)["alpha:meetings"] == meetings_before
    assert _daemon(machine).storage.transcript_states(ALPHA) == {}


@pytest.mark.parametrize(
    ("scope", "named"), [("project:zeta", "zeta"), ("people:bob", "people:bob")]
)
def test_a_scope_that_is_not_enrolled_or_declined_is_refused_naming_it(
    scope, named, machine, tmp_path, monkeypatch, capsys
):
    before = _held(machine)
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)
    code, _ = _drop(monkeypatch, path, "--scope", scope)
    assert code == EXIT_REFUSAL
    assert named in capsys.readouterr().err
    assert _held(machine) == before


@pytest.mark.parametrize(
    "argv",
    [["--scope", "alpha"], [], ["--scope", "personal", "--no-file"]],
    ids=["scope-alpha", "no-scope", "no-file"],
)
def test_a_command_line_pm_ai_cannot_read_is_the_syntax_reminder(
    argv, machine, tmp_path, monkeypatch, capsys
):
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)
    words = ["transcript", "drop", *([] if "--no-file" in argv else [str(path)]),
             *[w for w in argv if w != "--no-file"]]
    assert entry.main(words) == EXIT_USAGE
    assert "drop <file> --scope <scope>" in capsys.readouterr().err


# ── Formats, encodings and the duration's source ─────────────────────────────


@pytest.mark.parametrize(
    ("name", "text", "form", "asks_duration"),
    [
        ("sync.srt", SPOKEN_VTT, CaptureFormat.MANUAL_VTT, False),
        ("sync", SPOKEN_VTT, CaptureFormat.MANUAL_VTT, False),
        ("SYNC.VTT", SPOKEN_VTT, CaptureFormat.MANUAL_VTT, False),
        ("sync.log", SAID_TXT, CaptureFormat.MANUAL_TXT, True),
        ("notes", SAID_TXT, CaptureFormat.MANUAL_TXT, True),
        ("sync.TXT", SPOKEN_VTT, CaptureFormat.MANUAL_TXT, True),
    ],
    ids=["vtt-as-srt", "vtt-no-extension", "upper-VTT", "speaker-as-log", "speaker-no-extension", "vtt-as-upper-TXT"],
)
def test_the_capture_name_and_duration_source_follow_the_rule(
    name, text, form, asks_duration, machine, tmp_path, monkeypatch, capsys
):
    _, repository, _ = machine
    path = _file(tmp_path, name, text)
    answers = ["Planning", START_TYPED, ""] + (["20"] if asks_duration else [])

    code, terminal = _drop(monkeypatch, path, "--scope", "project:alpha", answers=answers)

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    meeting_id = _minted(out)
    stored = repository / ".project-ai" / "transcripts" / capture_name(meeting_id, START, form)
    assert stored.read_text() == text
    assert any(p.startswith("Duration") for p in terminal.prompts) is asks_duration
    if asks_duration:
        assert "20 minutes, as answered" in out
    else:
        assert "31 minutes, taken from the last cue's end" in out


def test_a_bom_and_crlf_file_is_read_and_stored_byte_for_byte(
    machine, tmp_path, monkeypatch, capsys
):
    _, repository, _ = machine
    text = "\ufeff" + SPOKEN_VTT.replace("\n", "\r\n")
    path = tmp_path / "drops" / "windows.vtt"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(text.encode("utf-8"))

    code, _ = _drop(
        monkeypatch, path, "--scope", "project:alpha", answers=["Planning", START_TYPED, ""]
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    meeting_id = _minted(out)
    stored = repository / ".project-ai" / "transcripts" / capture_name(
        meeting_id, START, CaptureFormat.MANUAL_VTT
    )
    assert stored.read_bytes() == path.read_bytes()
    assert "1 staged" in out and "31 minutes, taken from the last cue's end" in out


def test_a_last_cue_ending_at_zero_is_no_duration_and_it_is_asked(
    machine, tmp_path, monkeypatch, capsys
):
    path = _file(tmp_path, "zero.vtt", "WEBVTT\n\n00:00:00.000 --> 00:00:00.000\n<v Alex>hi\n")
    code, terminal = _drop(
        monkeypatch, path, "--scope", "project:alpha",
        answers=["Planning", START_TYPED, "", "15"],
    )
    assert code == EXIT_OK
    assert any(p.startswith("Duration") for p in terminal.prompts)
    assert "15 minutes, as answered" in capsys.readouterr().out


def test_a_timing_inside_a_note_block_is_not_the_last_cue(machine, tmp_path, monkeypatch, capsys):
    text = SPOKEN_VTT + "\nNOTE\n09:00:00.000 --> 10:00:00.000\n"
    path = _file(tmp_path, "noted.vtt", text)
    code, _ = _drop(
        monkeypatch, path, "--scope", "project:alpha", answers=["Planning", START_TYPED, ""]
    )
    assert code == EXIT_OK
    assert "31 minutes, taken from the last cue's end" in capsys.readouterr().out


# ── Command-line spellings through `main()` ──────────────────────────────────


@pytest.mark.parametrize(
    "spelling",
    [
        ["--scope=project:alpha", "--meeting=mtg_named", "{file}"],
        ["--meeting", "mtg_named", "--scope", "project:alpha", "{file}"],
    ],
    ids=["equals-forms", "options-first"],
)
def test_the_option_spellings_read_alike(spelling, machine, tmp_path, monkeypatch, capsys):
    _record(machine, _meeting("mtg_named"))
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)
    monkeypatch.setattr("builtins.input", Terminal([]))
    words = [word.replace("{file}", str(path)) for word in spelling]
    assert entry.main(["transcript", "drop", *words]) == EXIT_OK
    assert "mtg_named" in capsys.readouterr().out


def test_a_duplicated_scope_is_the_syntax_reminder(machine, tmp_path, capsys):
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)
    code = entry.main(
        ["transcript", "drop", str(path), "--scope", "personal", "--scope", "project:alpha"]
    )
    assert code == EXIT_USAGE
    assert "twice" in capsys.readouterr().err


def test_a_meeting_recorded_in_another_scope_is_not_found_in_this_one(
    machine, tmp_path, monkeypatch, capsys
):
    _record(machine, _meeting("mtg_personal", scope=PERSONAL))
    before = _held(machine)
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)

    code, _ = _drop(monkeypatch, path, "--scope", "project:alpha", "--meeting", "mtg_personal")

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "mtg_personal" in err and "project:alpha" in err
    assert _held(machine) == before


# ── After the capture ────────────────────────────────────────────────────────


def test_an_ingested_row_that_cannot_be_written_is_a_notice_not_a_failure(
    machine, tmp_path, monkeypatch, capsys
):
    _, repository, _ = machine
    _record(machine, _meeting("mtg_named"))

    def refuse(self, *args, **kwargs):
        raise OperationalStoreBusy("operational.db is busy")

    monkeypatch.setattr(StorageService, "record_transcript_state", refuse)
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)

    code, _ = _drop(monkeypatch, path, "--scope", "project:alpha", "--meeting", "mtg_named")

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert (repository / ".project-ai" / "transcripts"
            / capture_name("mtg_named", START, CaptureFormat.MANUAL_VTT)).is_file()
    assert "could not be written (OperationalStoreBusy)" in captured.err
    assert "never offered again by a harvest" in captured.err
    assert "next run" not in captured.err


def test_a_defect_in_ingestion_is_exit_1_naming_the_kept_capture(
    machine, tmp_path, monkeypatch, capsys
):
    _, repository, _ = machine

    def broken(self, meeting):
        raise TypeError("a bug")

    original = MeetingRecords.put
    monkeypatch.setattr(MeetingRecords, "put", broken)
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)

    code, _ = _drop(
        monkeypatch, path, "--scope", "project:alpha", answers=["Planning", START_TYPED, ""]
    )

    assert code == EXIT_UNEXPECTED
    err = capsys.readouterr().err
    stored = list((repository / ".project-ai" / "transcripts").glob("*-manual.vtt"))
    assert len(stored) == 1
    assert "TypeError: a bug" in err and f"{stored[0]} was written before this failure" in err
    monkeypatch.setattr(MeetingRecords, "put", original)


def test_a_staging_failure_part_way_counts_what_was_staged(
    machine, tmp_path, monkeypatch, capsys
):
    _record(machine, _meeting("mtg_named"))
    text = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n"
        f"<v Andrei>{COMMAND}\n\n00:00:06.000 --> 00:00:09.000\n"
        "<v Andrei>pm-ai, post_comment gitlab:alpha:issue:103 and this\n"
    )
    original = StorageService.stage_unless_acted
    calls = []

    def once(self, proposal):
        calls.append(proposal.proposal_id)
        if len(calls) > 1:
            raise OperationalStoreBusy("operational.db is busy")
        return original(self, proposal)

    monkeypatch.setattr(StorageService, "stage_unless_acted", once)
    path = _file(tmp_path, "two.vtt", text)

    code, _ = _drop(monkeypatch, path, "--scope", "project:alpha", "--meeting", "mtg_named")

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert len(calls) == 2
    assert "1 of its proposals were already staged" in err
    assert "remove" in err and "--meeting mtg_named" in err


def test_ctrl_c_during_the_capture_write_says_nothing_was_kept(
    machine, tmp_path, monkeypatch, capsys
):
    def interrupted(self, body, *, scope, name):
        raise KeyboardInterrupt

    monkeypatch.setattr(StorageService, "write_capture", interrupted)
    before = _held(machine)
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)

    code, _ = _drop(
        monkeypatch, path, "--scope", "project:alpha", answers=["Planning", START_TYPED, ""]
    )

    assert code == EXIT_REFUSAL
    assert "No capture was kept" in capsys.readouterr().err
    assert _held(machine) == before


def test_ctrl_c_during_ingestion_says_the_capture_was_kept(machine, tmp_path, monkeypatch, capsys):
    _, repository, _ = machine

    def interrupted(self, meeting):
        raise KeyboardInterrupt

    monkeypatch.setattr(MeetingRecords, "put", interrupted)
    path = _file(tmp_path, "sync.vtt", SPOKEN_VTT)

    code, _ = _drop(
        monkeypatch, path, "--scope", "project:alpha", answers=["Planning", START_TYPED, ""]
    )

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    stored = list((repository / ".project-ai" / "transcripts").glob("*-manual.vtt"))
    assert len(stored) == 1 and f"{stored[0]} was written and stays" in err


# ── Story 11d: `.md` and `.docx`, and how a path is read ─────────────────────


@pytest.fixture
def ingested(monkeypatch):
    """Every transcript the drop hands to ingestion, as it was read."""
    seen = []
    real = pipelines.run_transcript_ingestion

    def spy(daemon, transcript, meeting):
        seen.append(transcript)
        return real(daemon, transcript, meeting)

    monkeypatch.setattr(pipelines, "run_transcript_ingestion", spy)
    return seen


def _copy(tmp_path: Path, fixture: str, name: str | None = None) -> Path:
    path = tmp_path / "drops" / (name or fixture)
    path.parent.mkdir(exist_ok=True)
    path.write_bytes((FIXTURES / fixture).read_bytes())
    return path


def test_the_recorded_teams_docx_is_read_bound_and_stored_as_text(
    machine, tmp_path, monkeypatch, capsys, ingested
):
    """Acceptance: the `.docx` fixture into `project:alpha`, the questions answered."""
    _, repository, _ = machine
    path = _copy(tmp_path, "teams_export_recorded.docx")

    code, terminal = _drop(
        monkeypatch, path, "--scope", "project:alpha",
        answers=["Weekly Sync", START_TYPED, "email:alex@example.com", "30"],
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    meeting_id = _minted(out)
    assert any(p.startswith("Duration") for p in terminal.prompts)
    assert _daemon(machine).meetings.get(meeting_id, scope=ALPHA).meeting.manual_only
    assert len(_held(machine)["alpha:meetings"]) == 1
    stored = repository / ".project-ai" / "transcripts" / capture_name(
        meeting_id, START, CaptureFormat.MANUAL_TXT
    )
    text = docx_text(path.read_bytes(), name=str(path))
    assert stored.read_text(encoding="utf-8") == text
    assert not list((repository / ".project-ai" / "transcripts").glob("*.docx"))
    (transcript,) = ingested
    assert transcript.utterances == read_transcript(text, name="fixture")
    assert transcript.utterances[0].speaker_handle == "Alex Morgan"
    assert transcript.utterances[0].offset_seconds == 32
    assert {u.speaker_handle for u in transcript.utterances} == {"Alex Morgan", "Sam Rivera"}
    assert "text taken from the .docx" in out and "not kept" in out


def test_the_markdown_export_is_read_by_the_turn_reader_and_reads_again_the_same(
    machine, tmp_path, monkeypatch, capsys, ingested
):
    home, _, _ = machine
    path = _copy(tmp_path, "teams_export_recorded.md")

    code, _ = _drop(
        monkeypatch, path, "--scope", "personal",
        answers=["Weekly Sync", START_TYPED, "", "30"],
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    meeting_id = _minted(out)
    stored = home / ".manager-ai" / "transcripts" / capture_name(
        meeting_id, START, CaptureFormat.MANUAL_TXT
    )
    assert stored.read_bytes() == path.read_bytes()
    (transcript,) = ingested
    assert len(transcript.utterances) > 300
    assert transcript.utterances[0].speaker_handle == "Alex Morgan"
    # The stored text, read again, gives the same utterances.
    assert read_transcript(stored.read_text(encoding="utf-8"), name="again") == transcript.utterances
    assert ".docx" not in out


def test_speaker_words_in_md_is_read_as_today(machine, tmp_path, monkeypatch, capsys, ingested):
    path = _file(tmp_path, "notes.md", SAID_TXT)
    code, _ = _drop(
        monkeypatch, path, "--scope", "project:alpha", answers=["Planning", START_TYPED, "", "20"]
    )
    assert code == EXIT_OK, capsys.readouterr().err
    (transcript,) = ingested
    assert [u.speaker_handle for u in transcript.utterances] == ["Andrei", "Alex"]


def test_a_spoken_command_in_a_turn_by_the_pms_name_is_staged_never_executed(
    machine, tmp_path, monkeypatch, capsys
):
    _record(machine, _meeting("mtg_named"))
    text = f"Weekly Sync\nAndrei Ramaniuk   0:01\n{COMMAND}\nAlex Morgan   0:09\nthanks\n"
    path = _file(tmp_path, "sync.md", text)

    code, _ = _drop(monkeypatch, path, "--scope", "project:alpha", "--meeting", "mtg_named")

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    proposal = _proposal(machine, "mtg_named")
    assert proposal.state is ProposalState.STAGED
    assert "1 staged" in out and "executed" not in out


def _archive(*entries: tuple[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _document(body: str, *, namespace: str = W, prolog: str = "") -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>{prolog}<w:document xmlns:w="{namespace}">'
        f"<w:body>{body}</w:body></w:document>"
    ).encode()


def _bad_crc() -> bytes:
    stored = io.BytesIO()
    with zipfile.ZipFile(stored, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("word/document.xml", _document("<w:p><w:r><w:t>x</w:t></w:r></w:p>"))
    data = bytearray(stored.getvalue())
    at = data.find(b"<w:t>")
    data[at + 1] = ord("W")
    return bytes(data)


HEADER_ONLY = "".join(
    f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>"
    for line in ("Weekly sync", "14 September 2026, 09:30am", "Transcript")
)


@pytest.mark.parametrize(
    ("payload", "said"),
    [
        (b"just a text file renamed\n", "ZIP archive"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, "password-protected, or an old .doc"),
        (_archive(("other.xml", b"<x/>")), "no word/document.xml"),
        (_archive(("word/document.xml", _document("")), ("Word/Document.xml", _document(""))),
         "2 times"),
        (_archive(*[(f"e/{i}", b"") for i in range(10_001)]), "10001 entries"),
        (_bad_crc(), "damaged"),
        (_archive(("word/document.xml", _document(
            "", prolog='<!DOCTYPE w:document [<!ENTITY a "x">]>'))), "document type declaration"),
        (_archive(("word/document.xml", '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE d>'
                   f'<w:document xmlns:w="{W}"/>'.encode("utf-16"))), "document type declaration"),
        (_archive(("word/document.xml", _document(
            "<w:p/>", namespace="http://purl.oclc.org/ooxml/wordprocessingml/main"))),
         "Strict Open XML"),
        (_archive(("word/document.xml", _document(HEADER_ONLY))), "holds no turn"),
    ],
    ids=["not-a-zip", "password", "no-document", "two-documents", "too-many", "bad-crc",
         "doctype-utf8", "doctype-utf16", "strict", "header-only"],
)
def test_a_docx_pm_ai_cannot_take_text_from_is_refused_writing_nothing(
    payload, said, machine, tmp_path, monkeypatch, capsys
):
    before = _held(machine)
    path = tmp_path / "drops" / "minutes.docx"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(payload)

    code, terminal = _drop(monkeypatch, path, "--scope", "project:alpha")

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert said in err and str(path) in err and FOUR_FORMATS in err
    assert terminal.prompts == []
    assert _held(machine) == before


def test_a_docx_whose_document_is_over_64_mib_is_refused(machine, tmp_path, monkeypatch, capsys):
    before = _held(machine)
    big = _archive(("word/document.xml", b"<w:document>" + b" " * (64 * 1024 * 1024) + b"</w:document>"))
    path = tmp_path / "drops" / "big.docx"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(big)
    code, _ = _drop(monkeypatch, path, "--scope", "project:alpha")
    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "64 MiB" in err and FOUR_FORMATS in err
    assert _held(machine) == before


def test_prose_no_reader_accepts_is_refused_naming_the_file_and_the_four(
    machine, tmp_path, monkeypatch, capsys
):
    before = _held(machine)
    path = _file(tmp_path, "notes.md", "just some notes\nno speakers here\nnor any times\n")
    code, _ = _drop(monkeypatch, path, "--scope", "project:alpha")
    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert str(path) in err and FOUR_FORMATS in err
    assert _held(machine) == before


def test_a_missing_relative_path_names_the_absolute_path_and_how_it_is_read(
    machine, tmp_path, monkeypatch, capsys
):
    before = _held(machine)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    code, _ = _drop(monkeypatch, Path("notes.vtt"), "--scope", "personal")

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert str(Path.cwd() / "notes.vtt") in err and "does not exist" in err
    assert "read from the folder pm-ai was started in" in err and FOUR_FORMATS in err
    assert _held(machine) == before


def test_a_quoted_tilde_path_is_read_from_the_home_folder(machine, tmp_path, monkeypatch, capsys):
    home, _, _ = machine
    (home / "x.vtt").write_text(SPOKEN_VTT, encoding="utf-8")
    _record(machine, _meeting("mtg_named", scope=PERSONAL))
    monkeypatch.setattr("builtins.input", Terminal([]))

    code = entry.main(["transcript", "drop", "~/x.vtt", "--scope", "personal", "--meeting", "mtg_named"])

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    stored = home / ".manager-ai" / "transcripts" / capture_name(
        "mtg_named", START, CaptureFormat.MANUAL_VTT
    )
    assert stored.read_text() == SPOKEN_VTT


def test_a_missing_tilde_path_names_the_expanded_path(machine, tmp_path, monkeypatch, capsys):
    home, _, _ = machine
    code, _ = _drop(monkeypatch, Path("~/absent.vtt"), "--scope", "personal")
    assert code == EXIT_REFUSAL
    assert str(home / "absent.vtt") in capsys.readouterr().err


def test_an_unquoted_path_with_a_space_says_to_quote_it(machine, capsys):
    code = entry.main(["transcript", "drop", "My", "Notes.vtt", "--scope", "personal"])
    assert code == EXIT_USAGE
    assert "a path with spaces must be in quotes" in capsys.readouterr().err


def test_only_transcript_drop_gets_the_quoting_hint(machine, capsys):
    code = entry.main(["goal", "show", "a", "b"])
    assert code == EXIT_USAGE
    assert "quotes" not in capsys.readouterr().err


def test_the_usage_names_the_four_formats_and_how_a_relative_path_is_read(capsys):
    assert entry.main(["transcript", "drop", "--help"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "ingest a .vtt, .txt, .md or .docx transcript" in out
    assert "a relative <file> is read from the folder pm-ai was started in" in out


def test_answers_given_as_options_are_not_asked(machine, tmp_path, monkeypatch, capsys):
    """The daemon's half of AD-21: a fully specified drop asks nothing, terminal or not."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    path = _file(tmp_path, "sync.txt", SAID_TXT)

    code, terminal = _drop(
        monkeypatch, path, "--scope", "project:alpha", "--title", "Planning",
        "--start", START_TYPED, "--attendees", "email:alex@example.com", "--duration", "25",
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert terminal.prompts == []
    record = _daemon(machine).meetings.get(_minted(out), scope=ALPHA).meeting
    assert record.title == "Planning" and record.start == START
    assert record.duration_minutes == 25 and record.attendees == (Actor("email:alex@example.com"),)


def test_a_missing_answer_without_a_terminal_is_refused_naming_its_option(
    machine, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    before = _held(machine)
    path = _file(tmp_path, "sync.txt", SAID_TXT)
    code, _ = _drop(
        monkeypatch, path, "--scope", "project:alpha", "--title", "Planning", "--start", START_TYPED
    )
    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "--attendees" in err and "--duration" in err
    assert _held(machine) == before


@pytest.mark.parametrize(
    "options",
    [["--start", "tomorrow"], ["--duration", "0"], ["--attendees", "Smith, Bob"],
     ["--meeting", "mtg_x", "--title", "Planning"]],
    ids=["start", "duration", "attendees", "with-meeting"],
)
def test_an_answer_option_its_question_would_refuse_is_a_usage_error(
    options, machine, tmp_path, monkeypatch, capsys
):
    path = _file(tmp_path, "sync.txt", SAID_TXT)
    code, _ = _drop(monkeypatch, path, "--scope", "project:alpha", *options)
    assert code == EXIT_USAGE
    assert options[0] in capsys.readouterr().err


def test_a_relative_broken_docx_names_the_absolute_path_and_how_it_is_read(
    machine, tmp_path, monkeypatch, capsys
):
    before = _held(machine)
    work = tmp_path / "work"
    work.mkdir()
    (work / "minutes.docx").write_bytes(b"not a zip\n")
    monkeypatch.chdir(work)

    code, _ = _drop(monkeypatch, Path("minutes.docx"), "--scope", "personal")

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert str(Path.cwd() / "minutes.docx") in err and "ZIP archive" in err
    assert "read from the folder pm-ai was started in" in err and FOUR_FORMATS in err
    assert _held(machine) == before
