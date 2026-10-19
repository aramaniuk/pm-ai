"""Story 11d — the third reader, speaker-and-time turns, and the choice among three.

The recorded Teams export and its Markdown conversion (`fixtures/transcripts/`)
are what the reader is fitted to; the rest are the line rule's edges, one per
matrix row.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pm_ai.core.docx_text import docx_text
from pm_ai.core.transcripts import (
    carried_minutes,
    drop_capture_format,
    drop_utterances,
    has_turn_line,
    read_transcript,
)
from pm_ai.domain.transcripts import CaptureFormat, NothingSaid, UnreadableTranscript, Utterance

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
RECORDED_TURNS = 180
"""Measured in the fixture's README: 180 turns."""


def _docx_text() -> str:
    return docx_text((FIXTURES / "teams_export_recorded.docx").read_bytes(), name="x.docx")


def _md_text() -> str:
    return (FIXTURES / "teams_export_recorded.md").read_text(encoding="utf-8")


# ── The recorded export ──────────────────────────────────────────────────────


def test_the_recorded_docx_reads_as_turns_with_speakers_and_offsets():
    utterances = read_transcript(_docx_text(), name="x.docx")
    assert utterances[0] == Utterance("Alex Morgan", "Placeholder line 1 of turn 1.", 32)
    assert utterances[1] == Utterance("Sam Rivera", "Тестовая строка 1, реплика 2.", 33)
    assert {u.speaker_handle for u in utterances} == {"Alex Morgan", "Sam Rivera"}
    assert all(u.speaker is None for u in utterances)
    # The closing line has no time, so it is words of the last turn, as written.
    assert utterances[-1] == Utterance("Sam Rivera", "Sam Rivera stopped transcription", 28 * 60 + 57)
    offsets = [u.offset_seconds for u in utterances]
    assert offsets == sorted(offsets)
    # No header line is an utterance.
    assert not any("09:30am" in u.text or "29m 03s" in u.text for u in utterances)


def test_the_recorded_export_holds_180_turns_above_a_quarter():
    text = _docx_text()
    lines = [line for line in text.splitlines() if line.strip()]
    turns = [line for line in lines if has_turn_line(line)]
    assert len(turns) == RECORDED_TURNS
    first = next(index for index, line in enumerate(lines) if has_turn_line(line))
    assert len(turns) * 4 >= len(lines) - first


def test_the_markdown_export_reads_as_the_same_turns():
    from_md = read_transcript(_md_text(), name="x.md")
    from_docx = read_transcript(_docx_text(), name="x.docx")
    assert len(from_md) == len(from_docx)
    # The only difference: the closing line keeps Markdown's bold, as written.
    assert from_md[:-1] == from_docx[:-1]
    assert from_md[-1].text == "**Sam Rivera** stopped transcription"


@pytest.mark.parametrize("text", [_md_text, _docx_text], ids=["markdown", "docx"])
def test_stored_text_reads_again_to_the_same_utterances(text):
    stored = text()
    assert read_transcript(stored, name="a") == read_transcript(stored, name="b")


# ── The line rule ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line", "speaker", "offset"),
    [
        ("Ana 3:07", "Ana", 187),
        ("Ana Lee   1:03:07", "Ana Lee", 3787),
        ("**Ana Lee** 0:03\\", "Ana Lee", 3),
        ("__Ana__ *3:07*  ", "Ana", 187),
        ("## Ana 3:07", "Ana", 187),
        ("- Ana 3:07", "Ana", 187),
        ("1. Ana 3:07", "Ana", 187),
        ("> **Ana** 3:07", "Ana", 187),
        ("Ana [3:07](https://example.com/t?=187)", "Ana", 187),
        ("**Ana** **[3:07](https://example.com)**", "Ana", 187),
        ("Bo-Li O'Neil Jr. 12:00", "Bo-Li O'Neil Jr.", 720),
        ("Анна Петрова 0:32", "Анна Петрова", 32),
    ],
)
def test_a_speaker_and_time_line_is_read_with_markdown_set_aside(line, speaker, offset):
    text = f"{line}\nwords one\n{line}\nwords two\n"
    assert read_transcript(text, name="x") == (
        Utterance(speaker, "words one", offset),
        Utterance(speaker, "words two", offset),
    )


@pytest.mark.parametrize(
    "line",
    [
        "Ana: meet at 10:30",
        "October 15, 2026, 9:00AM",
        "Agent 007 3:07",
        "Ana, Bo 3:07",
        "3:07",
        "Ana 3:7",
        "Ana 3:60",
        "Ana 9:00AM",
        "x" * 81 + " 3:07",
    ],
)
def test_lines_that_are_not_speaker_and_time_lines(line):
    assert not has_turn_line(line)


def test_a_header_with_a_date_is_not_a_turn():
    text = "Weekly sync\nOctober 15, 2026, 9:00AM\nTranscript\n\nAna 0:03\nhello\nBo 0:09\nhi\n"
    assert read_transcript(text, name="x") == (
        Utterance("Ana", "hello", 3),
        Utterance("Bo", "hi", 9),
    )


def test_a_colon_line_with_a_time_stays_a_speaker_words_line():
    text = "Ana: meet at 10:30\nBo: fine\nAna: see you\n"
    utterances = read_transcript(text, name="x")
    assert utterances[0].speaker_handle == "Ana" and utterances[0].text == "meet at 10:30"


def test_speaker_words_in_markdown_is_the_plain_text_reader():
    text = "# Notes\n\nAna: we ship Friday\nBo: agreed\nAna: good\n"
    assert [u.speaker_handle for u in read_transcript(text, name="x.md")] == ["", "Ana", "Bo", "Ana"]


def test_turns_under_a_quarter_of_the_lines_are_not_the_turn_reader():
    words = "\n".join(f"line {index} of prose" for index in range(8))
    text = f"Ana 0:01\n{words}\nBo 0:09\n{words}\n"
    with pytest.raises(UnreadableTranscript):
        read_transcript(text, name="x")


def test_a_turn_with_no_words_adds_nothing_and_times_are_kept_out_of_order():
    text = "Ana 0:30\n\nBo 0:10\nfirst\nAna 0:20\nsecond\n"
    assert read_transcript(text, name="x") == (
        Utterance("Bo", "first", 10),
        Utterance("Ana", "second", 20),
    )


def test_a_spoken_command_in_a_turn_is_one_utterance_its_line():
    text = "Andrei Ramaniuk 0:01\npm-ai, post_comment gitlab:alpha:issue:102 ship it\nand more\nBo 0:09\nok\n"
    utterances = read_transcript(text, name="x")
    assert utterances[0].text == "pm-ai, post_comment gitlab:alpha:issue:102 ship it"
    assert utterances[0].speaker is None


# ── Nothing said, and no reader ──────────────────────────────────────────────


def test_turns_that_all_say_nothing_are_nothing_said():
    with pytest.raises(NothingSaid, match="turns"):
        drop_utterances("Title\nAna 0:01\n\nBo 0:02\n", name="x.md")


def test_a_docx_holding_only_its_header_is_nothing_said():
    header = "Weekly sync\n14 September 2026, 09:30am\nTranscript\n"
    with pytest.raises(NothingSaid, match="holds no turn"):
        drop_utterances(header, name="x.docx", from_docx=True)
    with pytest.raises(UnreadableTranscript):
        drop_utterances(header, name="x.md")


def test_prose_no_reader_accepts_is_unreadable_and_names_no_format():
    with pytest.raises(UnreadableTranscript) as refused:
        drop_utterances("just prose\nwith no speakers\nat all\n", name="notes.txt")
    said = str(refused.value)
    assert "notes.txt" in said
    # The four formats are the command's to add, never the readers' (Graph shares them).
    assert ".docx" not in said and ".md" not in said


def test_a_graph_capture_no_reader_accepts_names_no_format():
    with pytest.raises(UnreadableTranscript) as refused:
        read_transcript("not a transcript\nat all\n", name="graph capture")
    assert ".docx" not in str(refused.value)


# ── What a dropped file is stored as ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "text", "from_docx", "form"),
    [
        ("x.docx", "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n", True, CaptureFormat.MANUAL_TXT),
        ("x.md", "Ana 0:01\nhi\nBo 0:02\nyo\n", False, CaptureFormat.MANUAL_TXT),
        ("x.md", "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n", False, CaptureFormat.MANUAL_VTT),
    ],
)
def test_a_docx_is_stored_as_text_and_md_by_content(name, text, from_docx, form):
    assert drop_capture_format(name, text, from_docx=from_docx) is form


def test_only_webvtt_carries_a_duration():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:30:04.000\nhi\n"
    assert carried_minutes("x.vtt", vtt) == 31
    assert carried_minutes("x.docx", vtt, from_docx=True) is None
    assert carried_minutes("x.md", _md_text()) is None


@pytest.mark.parametrize(
    "line",
    ["a" + " " * 32_000 + "b", "**" + " " * 32_000 + "3:07", "Ana " + "*" * 32_000, "a " * 16_000 + "x"],
    ids=["spaces", "marks-and-spaces", "marks", "many-words"],
)
def test_a_long_line_is_judged_in_time_proportional_to_it(line):
    import time

    started = time.perf_counter()
    for _ in range(20):
        has_turn_line(line)
    with pytest.raises(UnreadableTranscript):
        read_transcript(f"{line}\n" * 50, name="x")
    assert time.perf_counter() - started < 1.0
