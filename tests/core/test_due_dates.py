"""Story 15a — a due as said, resolved to a date against the meeting's day.

Every form the resolver takes, in both languages, every form it refuses, the
bounds on N, numeric and time-like tokens, 29 February, the 300-day rule and
empty words; then the meeting's day under every estimate source and zone, and
the sentence the approval prints. Pure: no clock, no model, no file.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pm_ai.core.due_dates import due_words, meeting_day, resolve, resolve_date
from pm_ai.domain.commitments import Due
from pm_ai.domain.meetings import ESTIMATE_SOURCES

MONDAY = date(2026, 10, 19)


@pytest.mark.parametrize(
    "said, resolved",
    [
        # Matrix "English forms", meeting Mon 19 Oct.
        ("today", date(2026, 10, 19)),
        ("the day after tomorrow", date(2026, 10, 21)),
        ("in 3 days", date(2026, 10, 22)),
        ("in two weeks", date(2026, 11, 2)),
        ("end of this week", date(2026, 10, 23)),
        ("end of the month", date(2026, 10, 31)),
        ("3 Jan 2027", date(2027, 1, 3)),
        ("2026-10-30", date(2026, 10, 30)),
        # The rest of the English table.
        ("Thursday", date(2026, 10, 22)),
        ("by Thursday", date(2026, 10, 22)),
        ("thu", date(2026, 10, 22)),
        ("this Friday", date(2026, 10, 23)),
        ("end of day", date(2026, 10, 19)),
        ("EOD", date(2026, 10, 19)),
        ("tomorrow", date(2026, 10, 20)),
        ("day after tomorrow", date(2026, 10, 21)),
        ("in 1 day", date(2026, 10, 20)),
        ("within 5 days", date(2026, 10, 24)),
        ("in a week", date(2026, 10, 26)),
        ("in 3 weeks", date(2026, 11, 9)),
        ("within a week", date(2026, 10, 26)),
        ("end of week", date(2026, 10, 23)),
        ("by the end of the week", date(2026, 10, 23)),
        ("EOW", date(2026, 10, 23)),
        ("end of month", date(2026, 10, 31)),
        ("end of this month", date(2026, 10, 31)),
        ("3 Nov", date(2026, 11, 3)),
        ("November 3", date(2026, 11, 3)),
        ("3rd Nov", date(2026, 11, 3)),
        ("Nov 3rd", date(2026, 11, 3)),
        ("January 3 2027", date(2027, 1, 3)),
        ("due on Friday!", date(2026, 10, 23)),
        ("(Friday)", date(2026, 10, 23)),
    ],
)
def test_the_english_forms(said, resolved):
    assert resolve_date(said, MONDAY) == resolved


@pytest.mark.parametrize(
    "said, resolved",
    [
        # Matrix "Russian forms", meeting Mon 19 Oct.
        ("до четверга", date(2026, 10, 22)),
        ("завтра", date(2026, 10, 20)),
        ("через неделю", date(2026, 10, 26)),
        ("через 2 недели", date(2026, 11, 2)),
        ("до конца этой недели", date(2026, 10, 23)),
        ("к концу месяца", date(2026, 10, 31)),
        ("3-го ноября", date(2026, 11, 3)),
        ("30.10", date(2026, 10, 30)),
        ("30.10.2026г.", date(2026, 10, 30)),
        # The rest of the Russian table.
        ("четверг", date(2026, 10, 22)),
        ("к пятнице", date(2026, 10, 23)),
        ("в субботу", date(2026, 10, 24)),
        ("во вторник", date(2026, 10, 20)),
        ("в эту пятницу", date(2026, 10, 23)),
        ("сегодня", date(2026, 10, 19)),
        ("до конца дня", date(2026, 10, 19)),
        ("послезавтра", date(2026, 10, 21)),
        ("через 1 день", date(2026, 10, 20)),
        ("через 2 дня", date(2026, 10, 21)),
        ("через 5 дней", date(2026, 10, 24)),
        ("через два дня", date(2026, 10, 21)),
        ("в течение 3 дней", date(2026, 10, 22)),
        ("через три недели", date(2026, 11, 9)),
        ("через 5 недель", date(2026, 11, 23)),
        ("в течение недели", date(2026, 10, 26)),
        ("до конца недели", date(2026, 10, 23)),
        ("к концу недели", date(2026, 10, 23)),
        ("до конца месяца", date(2026, 10, 31)),
        ("до конца этого месяца", date(2026, 10, 31)),
        ("3 ноября", date(2026, 11, 3)),
        ("3 января 2027", date(2027, 1, 3)),
        ("3 января 2027 года", date(2027, 1, 3)),
        ("3 января 2027 г.", date(2027, 1, 3)),
        ("3 января 2027г.", date(2027, 1, 3)),
        ("30.10.2027", date(2027, 10, 30)),
        ("30.10.2027г.", date(2027, 10, 30)),
        ("30.10.2027 года", date(2027, 10, 30)),
        ("3.11.", date(2026, 11, 3)),
        ("03.11", date(2026, 11, 3)),
        ("09.05", date(2027, 5, 9)),
        ("15.11", date(2026, 11, 15)),
    ],
)
def test_the_russian_forms(said, resolved):
    assert resolve_date(said, MONDAY) == resolved


@pytest.mark.parametrize(
    "said",
    [
        # Matrix "Refused forms".
        "next Thursday",
        "в следующий четверг",
        "10/11",
        "Thursday morning",
        "30 February",
        "1 Jan 2026",
        "через день",
        "in 400 days",
        "к 9.05",
        "9.30 утра",
        # The rest of the refusals.
        "next week",
        "next sprint",
        "soon",
        "ASAP",
        "Q4",
        "the next release",
        "Thursday 3pm",
        "3 Jan 27",
        "29 Feb 2027",
        "in 0 days",
        "in 1000 days",
        "in 53 weeks",
        "9.30",
        "at 9.30",
        "30.10 at 10",
        "2026-02-30",
        "2026-10-18",
        "in days",
        "in a day or two",
        "four days",
    ],
)
def test_the_refused_forms_are_unresolved(said):
    assert resolve_date(said, MONDAY) is None
    assert resolve(said, MONDAY) == Due(said, "unresolved")


def test_the_meetings_own_weekday_is_unresolved():
    """Matrix "Meeting's own weekday": today, or a week later."""
    assert resolve_date("by Thursday", date(2026, 10, 22)) is None


def test_end_of_week_on_a_weekend_is_unresolved():
    """Matrix "End of week on a weekend"."""
    assert resolve_date("до конца недели", date(2026, 10, 24)) is None
    assert resolve_date("end of week", date(2026, 10, 25)) is None


def test_end_of_week_on_its_friday_is_unresolved():
    """Matrix "End of week on its Friday": the same-weekday rule."""
    assert resolve_date("end of week", date(2026, 10, 23)) is None


def test_end_of_the_month_on_its_last_day_is_that_day():
    assert resolve_date("end of the month", date(2026, 10, 31)) == date(2026, 10, 31)


def test_a_yearless_date_more_than_300_days_ahead_is_unresolved():
    """Matrix "Far ahead": `3 Jan` said on Mon 4 Jan 2027 more likely names the past."""
    assert resolve_date("3 Jan", date(2027, 1, 4)) is None
    assert resolve_date("03.01", date(2027, 1, 4)) is None
    # A year that was said is taken as said.
    assert resolve_date("3 Jan 2028", date(2027, 1, 4)) == date(2028, 1, 3)


def test_the_leap_day():
    """Matrix "Leap day": `29 Feb` said in Oct 2027 is 29 Feb 2028."""
    assert resolve_date("29 Feb", date(2027, 10, 5)) == date(2028, 2, 29)
    assert resolve_date("29 февраля", date(2027, 10, 5)) == date(2028, 2, 29)


def test_n_is_bounded_and_nothing_overflows():
    assert resolve_date("in 366 days", MONDAY) == MONDAY + timedelta(days=366)
    assert resolve_date("in 367 days", MONDAY) is None
    assert resolve_date("in 52 weeks", MONDAY) == MONDAY + timedelta(days=364)
    assert resolve_date("через 999 дней", MONDAY) is None
    assert resolve_date("in " + "9" * 5000 + " days", MONDAY) is None
    assert resolve_date("9" * 5000 + " jan", MONDAY) is None
    assert resolve_date("in 3 days", date(9999, 12, 30)) is None
    assert resolve_date("tomorrow", date(9999, 12, 31)) is None
    assert resolve_date("3 Jan", date(9999, 12, 31)) is None


@pytest.mark.parametrize("said", [None, "", "   ", "\t"])
def test_empty_words_are_nothing_said(said):
    """Matrix "No due": absent, or only spaces."""
    assert resolve(said, MONDAY) == Due(None, "none")


def test_an_unknown_day_leaves_the_words_unresolved():
    assert resolve("by Thursday", None) == Due("by Thursday", "unresolved")


def test_an_estimated_day_marks_the_date_estimated():
    found = resolve("tomorrow", MONDAY, estimated=True)
    assert found.date == date(2026, 10, 20) and found.estimated
    # An unresolved due is never estimated: it has no date.
    assert not resolve("next sprint", MONDAY, estimated=True).estimated


# ── The meeting's day ────────────────────────────────────────────────────────

SUNDAY_LATE = datetime(2026, 10, 18, 22, 30, tzinfo=timezone.utc)


def test_the_display_zone_decides_the_day():
    """Matrix "Display zone": 22:30 UTC Sunday is 01:30 Monday in Istanbul."""
    assert meeting_day(SUNDAY_LATE, None, zone=ZoneInfo("Europe/Istanbul"), machine_zone=None) == date(2026, 10, 19)
    assert meeting_day(SUNDAY_LATE, None, zone=None, machine_zone=None) == date(2026, 10, 18)


@pytest.mark.parametrize("source", ["docx-created", "header", "model-header"])
def test_an_estimated_start_still_names_its_day(source):
    """Matrix "Every estimate source": the start's day, marked estimated by the caller."""
    assert meeting_day(SUNDAY_LATE, source, zone=None, machine_zone=ZoneInfo("Europe/Istanbul")) == date(2026, 10, 18)


@pytest.mark.parametrize("source", ["header-machine-zone", "model-header-machine-zone"])
def test_a_machine_zone_start_is_the_day_in_the_machines_zone(source):
    """Matrix "Machine-zone header": `display_timezone` unset."""
    istanbul = ZoneInfo("Europe/Istanbul")
    assert meeting_day(SUNDAY_LATE, source, zone=None, machine_zone=istanbul) == date(2026, 10, 19)
    # With `display_timezone` set, it decides.
    assert meeting_day(SUNDAY_LATE, source, zone=timezone.utc, machine_zone=istanbul) == date(2026, 10, 18)


@pytest.mark.parametrize("source", ["file-modified", "something-new"])
def test_a_modification_time_or_an_unknown_source_names_no_day(source):
    """Matrix "Day from a modification time" and "Unknown source"."""
    assert meeting_day(SUNDAY_LATE, source, zone=timezone.utc, machine_zone=None) is None


def test_every_estimate_source_is_covered():
    named = {"docx-created", "header", "model-header", "header-machine-zone", "model-header-machine-zone", "file-modified"}
    assert set(ESTIMATE_SOURCES) == named


def test_a_date_alone_is_that_date():
    assert meeting_day(date(2026, 10, 19), None, zone=ZoneInfo("Pacific/Kiritimati"), machine_zone=None) == date(2026, 10, 19)


# ── The sentence ─────────────────────────────────────────────────────────────


def test_sentences_name_the_year_only_when_it_differs():
    """Matrix "Year in sentences"."""
    assert due_words(Due("by Thursday", "2026-10-22"), day=MONDAY) == 'due Thu 22 Oct ("by Thursday")'
    assert due_words(Due("3 Jan 2027", "2027-01-03"), day=MONDAY) == 'due Sun 3 Jan 2027 ("3 Jan 2027")'


def test_the_unresolved_and_none_sentences():
    assert due_words(Due("next sprint", "unresolved"), day=MONDAY) == 'with no due date worked out from "next sprint"'
    assert due_words(Due(None, "none"), day=MONDAY) == "with no due date said"
    assert due_words(Due("tomorrow", "2026-10-20", True), day=MONDAY) == (
        'due Tue 20 Oct (meeting day estimated) ("tomorrow")'
    )


@pytest.mark.parametrize("odd", ["2026-W43-5", "2026-296", "20261030"])
def test_only_year_month_day_is_an_iso_date(odd):
    assert resolve_date(odd, MONDAY) is None


# ── Story 15c: the form of the words, for the PM's own rules ─────────────────

from pm_ai.core.due_dates import (  # noqa: E402
    FORM_DATED,
    FORM_END_OF_WEEK,
    FORM_OTHER,
    FORM_THIS_WEEKDAY,
    FORM_WEEKDAY,
    due_form,
)


@pytest.mark.parametrize(
    ("words", "kind", "weekday"),
    [
        ("friday", FORM_WEEKDAY, 4),
        ("Fri", FORM_WEEKDAY, 4),
        ("пятница", FORM_WEEKDAY, 4),
        ("в пятницу", FORM_WEEKDAY, 4),
        ("к пятнице", FORM_WEEKDAY, 4),
        ("до пятницы", FORM_WEEKDAY, 4),
        ("this friday", FORM_THIS_WEEKDAY, 4),
        ("в эту пятницу", FORM_THIS_WEEKDAY, 4),
        ("end of week", FORM_END_OF_WEEK, None),
        ("EOW", FORM_END_OF_WEEK, None),
        ("end of this week", FORM_END_OF_WEEK, None),
        ("до конца недели", FORM_END_OF_WEEK, None),
        ("2026-10-20", FORM_DATED, None),
        ("20.10.2026", FORM_DATED, None),
        ("20.10.2026г.", FORM_DATED, None),
        ("20 Oct 2026", FORM_DATED, None),
        ("October 20 2026", FORM_DATED, None),
        ("20 октября 2026", FORM_DATED, None),
        ("20 октября 2026 года", FORM_DATED, None),
        ("20 Oct", FORM_OTHER, None),
        ("next sprint", FORM_OTHER, None),
        ("tomorrow", FORM_OTHER, None),
    ],
)
def test_the_form_of_the_words_is_named(words, kind, weekday):
    form = due_form(words)
    assert form.kind == kind and form.weekday == weekday


def test_resolve_still_leaves_a_meetings_own_weekday_unresolved():
    """Matrix "Extraction unchanged": "by Friday" said on Fri 30 Oct stays unresolved."""
    assert resolve("by Friday", date(2026, 10, 30)).value == "unresolved"
    assert resolve("end of week", date(2026, 10, 30)).value == "unresolved"
