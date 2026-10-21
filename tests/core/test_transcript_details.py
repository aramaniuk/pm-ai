"""Story 11e — the header readers and the field rules, I/O-free."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from pm_ai.core.aliases import AliasTable
from pm_ai.core.transcript_details import (
    SOURCE_DOCX_CREATED,
    SOURCE_HEADER,
    SOURCE_LAST_TURN,
    SOURCE_MODIFIED,
    DropFacts,
    HeaderReading,
    decide_fields,
    file_name_title,
    header_fragments,
    header_lines,
    model_header,
    participants,
    prepare_header_line,
    read_header,
    read_model_answer,
)
from pm_ai.domain.sanitize import Sanitized
from pm_ai.domain.transcripts import Utterance

TITLE = "Example Weekly Sync-up-20260914_093000-Meeting Recording"
TURNS = "Alex Morgan   0:32\nPlaceholder line 1.\n\nSam Rivera   28:57\nPlaceholder line 2.\n"


def _read(*lines: str) -> HeaderReading:
    return read_header([prepare_header_line(line) for line in lines])


@pytest.mark.parametrize(
    ("line", "minutes"),
    [
        ("29m 03s", 30),
        ("29m 00s", 29),
        ("0m 20s", 1),
        ("1h 02m 03s", 63),
        ("45m", 45),
        ("1h 05m", 65),
        ("29m03s", 30),
        ("29m 60s", None),
        ("0m 00s", None),
        ("1h 60m", None),
    ],
)
def test_the_length_forms(line, minutes):
    assert _read(TITLE, line).length == minutes


@pytest.mark.parametrize(
    ("line", "start"),
    [
        ("14 September 2026, 09:30am", datetime(2026, 9, 14, 9, 30)),
        ("September 24, 2026, 1:17PM", datetime(2026, 9, 24, 13, 17)),
        ("14 September 2026, 12:15am", datetime(2026, 9, 14, 0, 15)),
        ("14 September 2026, 12:15pm", datetime(2026, 9, 14, 12, 15)),
        ("14 september 2026,9:30 AM", datetime(2026, 9, 14, 9, 30)),
        ("14 September 2026, 0:15am", None),
        ("14 September 2026, 13:15pm", None),
        ("14 September 2026, 09:60am", None),
        ("31 September 2026, 09:30am", None),
        ("14 Septembre 2026, 09:30am", None),
        ("2026-09-14 09:30", None),
    ],
)
def test_the_start_forms_and_the_12_hour_rules(line, start):
    assert _read(TITLE, line, "29m 03s").start == start


def test_no_break_spaces_are_ordinary_spaces():
    assert _read(TITLE, "14 September 2026, 9:30 am").start == datetime(2026, 9, 14, 9, 30)


def test_the_stamp_is_removed_and_never_the_start():
    reading = _read(TITLE)
    assert reading.title == "Example Weekly Sync-up" and reading.start is None


def test_a_stamp_in_other_words_leaves_the_title_unread():
    assert _read("Example Weekly Sync-up-20260914_093000-Nagranie").title is None


def test_a_title_empty_after_the_stamp_is_unread():
    assert _read("-20260914_093000-Meeting Recording").title is None


def test_a_date_like_or_length_like_first_line_is_never_the_title():
    reading = _read("14 September 2026, 09:30am", "Example Weekly Sync-up", "29m 03s")
    assert reading.title is None and reading.start == datetime(2026, 9, 14, 9, 30)
    assert _read("29m 03s", "Example Weekly Sync-up").title is None


def test_two_start_lines_or_two_length_lines_leave_that_field_unread():
    two_starts = _read(TITLE, "14 September 2026, 09:30am", "14 September 2026, 10:30am", "29m 03s")
    assert two_starts.start is None and two_starts.length == 30 and two_starts.title
    two_lengths = _read(TITLE, "14 September 2026, 09:30am", "29m 03s", "30m")
    assert two_lengths.length is None and two_lengths.start is not None


def test_one_readable_and_one_unreadable_date_like_line_leave_the_start_unread():
    assert _read(TITLE, "14 September 2026, 09:30am", "2026-09-14").start is None


def test_a_three_field_length_line_is_not_a_second_start_line():
    reading = _read(TITLE, "14 September 2026, 09:30am", "1:02:03")
    assert reading.start == datetime(2026, 9, 14, 9, 30) and reading.length is None


def test_md_marks_are_set_aside_and_transcript_lines_skipped():
    text = (
        "# Example Weekly Sync-up-20260914\\_093000-Meeting Recording\n\n"
        "**14 September 2026, 09:30am**\n\n_29m 03s_\\\n\nTranscript\n\n" + TURNS
    )
    lines = header_lines(text)
    assert lines == (TITLE, "14 September 2026, 09:30am", "29m 03s")
    reading = read_header(lines)
    assert reading == HeaderReading("Example Weekly Sync-up", datetime(2026, 9, 14, 9, 30), 30)


def test_only_a_speaker_and_time_file_has_a_header():
    assert header_lines("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v A>hi\n") == ()
    assert header_lines("Alex: hi\nSam: hello\n") == ()


def test_the_model_is_shown_at_most_20_lines_and_2000_characters():
    assert model_header(["x"] * 20) is not None
    assert model_header(["x"] * 21) is None
    assert model_header(["x" * 2000]) is not None and model_header(["x" * 2001]) is None
    assert model_header([]) is None


def test_the_fragments_are_cleaned_header_lines():
    fragments = header_fragments(["Example Weekly Sync-up"])
    assert all(isinstance(fragment, Sanitized) for fragment in fragments)
    assert [fragment.raw for fragment in fragments] == ["Example Weekly Sync-up"]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"title": "Example Weekly Sync-up", "start": "2026-09-14T09:30", "length_minutes": 30},
         HeaderReading("Example Weekly Sync-up", datetime(2026, 9, 14, 9, 30), 30)),
        ({"title": "Quarterly Review", "start": None, "length_minutes": None}, HeaderReading()),
        ({"title": None, "start": "2026-09-14T09:30+02:00", "length_minutes": None},
         HeaderReading()),
        ({"title": None, "start": "2026-09-31T09:30", "length_minutes": None}, HeaderReading()),
        ({"title": None, "start": None, "length_minutes": 0}, HeaderReading()),
        ({"title": None, "start": None, "length_minutes": True}, HeaderReading()),
        ({"title": None, "start": None, "length_minutes": 1441}, HeaderReading()),
    ],
    ids=["all", "title-not-in-header", "offset", "unreal-date", "zero", "bool", "over-a-day"],
)
def test_the_models_answer_is_validated_field_by_field(answer, expected):
    lines = ["Example Weekly Sync-up · 2026-09-14 · 09:30 · 29m 03s"]
    assert read_model_answer(json.dumps(answer), lines) == expected


def test_a_model_title_keeps_the_stamp_rule():
    lines = ["Example Weekly Sync-up-20260914_093000-Nagranie"]
    answer = {"title": lines[0], "start": None, "length_minutes": None}
    assert read_model_answer(json.dumps(answer), lines).title is None


@pytest.mark.parametrize("answer", ["not json", "[1, 2]", "null"])
def test_an_answer_that_is_no_object_reads_nothing(answer):
    assert read_model_answer(answer, ["x"]) == HeaderReading()


def test_participants_once_each_by_key_in_order_of_first_turn():
    said = [
        Utterance("Alex Morgan", "a", 0),
        Utterance("", "b", 1),
        Utterance("Sam Rivera", "c", 2),
        Utterance("ALEX  MORGAN", "d", 3),
    ]
    assert participants(said) == ("Alex Morgan", "Sam Rivera")


def test_the_file_name_title():
    assert file_name_title(f"/x/{TITLE}.docx") == "Example Weekly Sync-up"
    assert file_name_title("/x/notes.md") == "notes"
    assert file_name_title("/x/-20260914_093000-Meeting Recording.docx") == (
        "-20260914_093000-Meeting Recording"
    )


# ── The start ────────────────────────────────────────────────────────────────

MODIFIED = datetime(2026, 9, 14, 8, 10, tzinfo=timezone.utc)
FIXTURE = f"{TITLE}\n14 September 2026, 09:30am\n29m 03s\n\n" + TURNS
WARSAW = ZoneInfo("Europe/Warsaw")


def _fields(text: str = FIXTURE, **facts):
    given = DropFacts(text=text, file_name="/x/export.md", modified=MODIFIED, **facts)
    return decide_fields(given, read_header(header_lines(text)))


@pytest.mark.parametrize(
    ("created", "source", "start"),
    [
        (datetime(2026, 9, 14, 8, 5, tzinfo=timezone.utc), SOURCE_DOCX_CREATED,
         datetime(2026, 9, 14, 7, 35, tzinfo=timezone.utc)),
        (datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc), SOURCE_DOCX_CREATED,
         datetime(2026, 9, 14, 7, 30, tzinfo=timezone.utc)),
        (datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc), SOURCE_HEADER,
         datetime(2026, 9, 14, 7, 30, tzinfo=timezone.utc)),
        (datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc), SOURCE_HEADER,
         datetime(2026, 9, 14, 7, 30, tzinfo=timezone.utc)),
    ],
    ids=["five-minutes-after", "at-the-end", "a-day-late", "a-day-early"],
)
def test_the_creation_time_decides_only_within_its_tolerance(created, source, start):
    fields = _fields(created=created, zone=WARSAW)
    assert fields.start.source == source and fields.start.value == start
    assert fields.start.estimated


def test_the_creation_time_needs_a_length():
    text = f"{TITLE}\n14 September 2026, 09:30am\n\nAlex Morgan   0:32\nx\n\nSam Rivera   0:00\ny\n"
    fields = _fields(text, created=datetime(2026, 9, 14, 8, 5, tzinfo=timezone.utc), zone=WARSAW)
    assert fields.length.value is None and fields.start.source == SOURCE_HEADER


def test_with_no_zone_the_header_time_is_not_read_in_one():
    fields = _fields()
    assert fields.start.source == SOURCE_MODIFIED
    assert fields.start.value == datetime(2026, 9, 14, 7, 40, tzinfo=timezone.utc)
    assert fields.hint_day.isoformat() == "2026-09-14"


def test_a_time_that_occurs_twice_takes_the_earlier_instant():
    text = FIXTURE.replace("14 September 2026, 09:30am", "25 October 2026, 2:30am")
    fields = _fields(text, zone=WARSAW)
    assert fields.start.value == datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)


def test_a_time_that_does_not_occur_takes_the_offset_before_the_change():
    text = FIXTURE.replace("14 September 2026, 09:30am", "29 March 2026, 2:30am")
    fields = _fields(text, zone=WARSAW)
    assert fields.start.value == datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)


def test_the_length_falls_to_the_last_turn():
    text = FIXTURE.replace("29m 03s\n", "")
    fields = _fields(text, zone=WARSAW)
    assert fields.length.value == 29 and fields.length.source == SOURCE_LAST_TURN


def test_an_unread_alias_table_links_no_one():
    assert AliasTable().handle_for("Alex Morgan", None) is None


# ── Review follow-ups ────────────────────────────────────────────────────────


def test_a_start_outside_1900_to_2099_is_not_read():
    assert _read(TITLE, "1 January 0001, 12:30am").start is None
    assert _read(TITLE, "31 December 9999, 11:30pm").start is None
    answer = {"title": None, "start": "0001-01-01T00:30", "length_minutes": None}
    assert read_model_answer(json.dumps(answer), ["x 0001-01-01"]).start is None


@pytest.mark.parametrize("title", ["14 September 2026", "29m 03s", "09:30"])
def test_a_model_title_that_is_a_date_or_a_length_is_refused(title):
    lines = ["14 September 2026 · 09:30 · 29m 03s"]
    answer = {"title": title, "start": None, "length_minutes": None}
    assert read_model_answer(json.dumps(answer), lines).title is None


def test_the_last_turns_length_is_an_estimate():
    fields = _fields(FIXTURE.replace("29m 03s\n", ""), zone=WARSAW)
    assert fields.length.estimated
