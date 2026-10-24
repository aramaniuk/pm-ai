"""A due as said, resolved to a date against the meeting's day (story 15a).

One resolver for the commitment ledger and Team Activity (`7e`), so the two
never show different dates for one promise. `resolve(said, day)` is pure and
deterministic: no model, no clock, no I/O. It names a date only when the
words name exactly one, in English or Russian, and everything else is
`unresolved` — a wrong date makes a promise look broken, which is worse than
no date.

`meeting_day` is the day a due is resolved against: the date of the meeting
record's `start`, in `display_timezone` when set, in the machine's zone for a
start read in that zone, otherwise in UTC; none when the start came from a
file's modification time or from a source pm-ai does not know.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone, tzinfo

from pm_ai.core.aliases import normalise
from pm_ai.domain.commitments import Due
from pm_ai.domain.meetings import ESTIMATE_SOURCES

__all__ = [
    "FAR_AHEAD_DAYS",
    "MAX_DAYS",
    "MONTHS",
    "WEEKDAYS",
    "due_words",
    "meeting_day",
    "resolve",
    "resolve_date",
]

MAX_DAYS = 366
"""In N days or weeks: more than this many days is no date."""

FAR_AHEAD_DAYS = 300
"""A date said without a year more than this many days after the meeting's day
more likely names the past: no date."""

WEEKDAYS: Mapping[str, int] = {
    **{name: day for day, name in enumerate(
        ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    )},
    **{name: day for day, name in enumerate(
        ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    )},
    **{name: day for day, name in enumerate(
        ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
    )},
    # The Russian cases a due is said in: «до среды», «к пятнице», «в субботу».
    "понедельника": 0, "понедельнику": 0,
    "вторника": 1, "вторнику": 1,
    "среды": 2, "среде": 2, "среду": 2,
    "четверга": 3, "четвергу": 3,
    "пятницы": 4, "пятнице": 4, "пятницу": 4,
    "субботы": 5, "субботе": 5, "субботу": 5,
    "воскресенья": 6, "воскресенью": 6,
}

MONTHS: Mapping[str, int] = {
    **{name: number for number, name in enumerate(
        ("january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"), start=1
    )},
    **{name: number for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )},
    "sept": 9,
    **{name: number for number, name in enumerate(
        ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
         "августа", "сентября", "октября", "ноября", "декабря"), start=1
    )},
}

LEADING = frozenset(
    {"by", "on", "until", "till", "before", "due", "до", "к", "ко", "в", "во", "на", "по"}
)
"""Words dropped from the front, repeatedly; then a leading `the`."""

_FOLDED = re.compile(r"[,;:!?()]+")
_ORDINAL = re.compile(r"^([0-9]{1,2})(?:st|nd|rd|th|-?го)$")
"""`3rd Jan`, `Jan 3rd`, `3-го января`: the suffix on the day is dropped."""
_NUMERIC = re.compile(
    r"^(?P<day>[0-9]{1,2})\.(?P<month>[0-9]{1,2})"
    r"(?:\.|\.(?P<year>[0-9]{4})(?:г\.?|года)?)?$"
)
"""`D.M`, `D.M.`, `D.M.YYYY`, `D.M.YYYY` with `г`, `г.` or `года`."""
_ISO = re.compile(r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})$")
_YEAR = re.compile(r"^(?P<year>[0-9]{4})(?:г|года)?$")
_YEAR_WORDS = frozenset({"г", "года"})
_DIGITS = re.compile(r"^[0-9]{1,3}$")

_NUMBERS: Mapping[str, int] = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3,
    "один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3,
}

_DAY_UNITS = frozenset({"day", "days", "день", "дня", "дней"})
_WEEK_UNITS = frozenset({"week", "weeks", "неделю", "недели", "недель"})
_THIS = frozenset({"this", "этой", "этого", "эту"})


def _tokens(said: str) -> list[str]:
    """The words of `said`: normalised, punctuation folded, a dot kept only inside a date."""
    words: list[str] = []
    for token in _FOLDED.sub(" ", normalise(said)).split():
        if _NUMERIC.match(token) or "." not in token:
            words.append(token)
            continue
        words.extend(token.replace(".", " ").split())
    while words and words[0] in LEADING:
        words.pop(0)
    if words and words[0] == "the":
        words.pop(0)
    return words


def _number(word: str) -> int | None:
    if _DIGITS.match(word):
        return int(word)
    return _NUMBERS.get(word)


def _plus(day: date, days: int) -> date | None:
    if days <= 0 or days > MAX_DAYS:
        return None
    return day + timedelta(days=days)


def _without_this(words: list[str]) -> list[str]:
    """`this` and `этой` dropped before a unit: `end of this week`, `конца этой недели`."""
    return [word for word in words if word not in _THIS]


def _weekday(words: list[str], day: date) -> date | None | bool:
    """A weekday's date, `None` for the meeting's own weekday, `False` for not a weekday."""
    if len(words) == 2 and words[0] in _THIS:
        words = words[1:]
    if len(words) != 1 or words[0] not in WEEKDAYS:
        return False
    target = WEEKDAYS[words[0]]
    if target == day.weekday():
        return None
    return day + timedelta(days=(target - day.weekday()) % 7)


def _relative(words: list[str], day: date) -> date | None | bool:
    """Today, tomorrow, in N days or weeks, the end of the week or month."""
    joined = " ".join(words)
    if joined in ("today", "end of day", "eod", "сегодня", "конца дня"):
        return day
    if joined in ("tomorrow", "завтра"):
        return day + timedelta(days=1)
    if joined in ("day after tomorrow", "послезавтра"):
        return day + timedelta(days=2)
    bare = " ".join(_without_this(words))
    if bare in ("end of week", "end of the week", "eow", "конца недели", "концу недели"):
        if day.weekday() >= 4:
            # A Saturday or Sunday has no Friday in its week ahead, and a
            # Friday's own follows the same-weekday rule.
            return None
        return day + timedelta(days=4 - day.weekday())
    if bare in ("end of month", "end of the month", "конца месяца", "концу месяца"):
        return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
    if len(words) in (2, 3) and words[0] in ("in", "within", "через", "течение"):
        rest = words[1:]
        if len(rest) == 1:
            # `через неделю`, `в течение недели`: one week. `через день` is
            # "every other day" too, so it is no date.
            if rest[0] in ("неделю", "недели") and words[0] in ("через", "течение"):
                return day + timedelta(days=7)
            return None
        count = _number(rest[0])
        if count is None:
            return None
        if rest[1] in _DAY_UNITS:
            return _plus(day, count)
        if rest[1] in _WEEK_UNITS:
            return _plus(day, 7 * count)
        return None
    return False


def _calendar_date(year: int, month: int, number: int) -> date | None:
    try:
        return date(year, month, number)
    except ValueError:
        return None


def _next_on_or_after(month: int, number: int, day: date) -> date | None:
    """The next such date on or after `day`, within `FAR_AHEAD_DAYS`; `29 Feb` only in a leap year."""
    for year in (day.year, day.year + 1):
        candidate = _calendar_date(year, month, number)
        if candidate is not None and candidate >= day:
            if (candidate - day).days > FAR_AHEAD_DAYS:
                return None
            return candidate
    return None


def _said_year(found: date | None, day: date) -> date | None:
    """A date with its year said is taken as said, unless it is before the meeting's day."""
    if found is None or found < day:
        return None
    return found


def _day_number(word: str) -> int | None:
    match = _ORDINAL.match(word)
    digits = match.group(1) if match else word
    if not re.fullmatch(r"[0-9]{1,2}", digits):
        return None
    return int(digits)


def _named_date(words: list[str], day: date) -> date | None | bool:
    """`3 Jan`, `January 3`, `3-го января`, with or without a year."""
    if words and words[-1] in _YEAR_WORDS and len(words) >= 3:
        words = words[:-1]
    if len(words) not in (2, 3):
        return False
    first, second = words[0], words[1]
    if second in MONTHS and (number := _day_number(first)) is not None:
        month = MONTHS[second]
    elif first in MONTHS and (number := _day_number(second)) is not None:
        month = MONTHS[first]
    else:
        return False
    if len(words) == 2:
        return _next_on_or_after(month, number, day)
    year = _YEAR.match(words[2])
    if year is None:
        return None
    return _said_year(_calendar_date(int(year.group("year")), month, number), day)


def _numeric(words: list[str], day: date) -> date | None | bool:
    """ISO `2026-10-30`; `30.10`, `3.11.`, `30.10.2027`, `30.10.2027г.`."""
    if len(words) == 2 and words[1] in _YEAR_WORDS:
        words = words[:1]
    if len(words) != 1:
        return False
    word = words[0]
    iso = _ISO.fullmatch(word)
    if iso is not None:
        found = _calendar_date(int(iso.group("year")), int(iso.group("month")), int(iso.group("day")))
        return _said_year(found, day)
    dotted = _NUMERIC.match(word)
    if dotted is None:
        return False
    number_text, month_text = dotted.group("day"), dotted.group("month")
    year_text = dotted.group("year")
    if year_text is None:
        # A one-digit first part and a two-digit second part from 00 to 59
        # could be a time (`к 9.05`, `9.30`): no date. A closing dot (`3.11.`)
        # is a date's, never a time's.
        if (
            not word.endswith(".")
            and len(number_text) == 1
            and len(month_text) == 2
            and int(month_text) <= 59
        ):
            return None
        return _next_on_or_after(int(month_text), int(number_text), day)
    found = _calendar_date(int(year_text), int(month_text), int(number_text))
    return _said_year(found, day)


def resolve_date(said: str | None, day: date | None) -> date | None:
    """The date `said` names against the meeting's `day`, or `None`."""
    if said is None or day is None or not said.strip():
        return None
    words = _tokens(said)
    if not words:
        return None
    for form in (_weekday, _relative, _named_date, _numeric):
        try:
            found = form(words, day)
        except OverflowError:
            # A day at the end of the calendar: nothing past it is a date.
            return None
        if found is False:
            continue
        return found if isinstance(found, date) else None
    return None


def resolve(said: str | None, day: date | None, *, estimated: bool = False) -> Due:
    """A due as said, resolved against the meeting's day (story 15a).

    Words that are empty or only spaces count as nothing said (`none`). Words
    that name no one date, or a day that is unknown, are `unresolved`. A date
    worked out against an estimated day is marked `estimated`.
    """
    if said is None or not said.strip():
        return Due.nothing()
    found = resolve_date(said, day)
    if found is None:
        return Due.unresolved(said)
    return Due.on(said, found, estimated=estimated)


def meeting_day(
    start: datetime | date | None,
    estimated_from: str | None,
    *,
    zone: tzinfo | None,
    machine_zone: tzinfo | None,
) -> date | None:
    """The day a due is resolved against: the date of the record's `start` (story 15a).

    In `zone` (`display_timezone`) when it is set; when it is not, in
    `machine_zone` for a start read in the machine's zone (the
    `-machine-zone` sources); otherwise in UTC. None for a start that came
    from a file's modification time, or from a source pm-ai does not know.
    A date recorded without a time is that date as written.
    """
    if start is None:
        return None
    if estimated_from is not None and (
        estimated_from == "file-modified" or estimated_from not in ESTIMATE_SOURCES
    ):
        return None
    if not isinstance(start, datetime):
        return start
    if zone is not None:
        return start.astimezone(zone).date()
    if (
        estimated_from is not None
        and estimated_from.endswith("-machine-zone")
        and machine_zone is not None
    ):
        return start.astimezone(machine_zone).date()
    return start.astimezone(timezone.utc).date()


def due_words(due: Due, *, day: date | None) -> str:
    """The due in a sentence: `due Thu 22 Oct ("by Thursday")` (story 15a).

    The year is named only when it differs from the meeting day's. An
    estimated date adds `(meeting day estimated)`. `unresolved` reads `with no
    due date worked out from "…"`, and `none` reads `with no due date said`.
    """
    found = due.date
    if found is None:
        if due.said is None:
            return "with no due date said"
        return f'with no due date worked out from "{due.said}"'
    words = f"due {found:%a} {found.day} {found:%b}"
    if day is None or found.year != day.year:
        words += f" {found.year}"
    if due.estimated:
        words += " (meeting day estimated)"
    if due.said is not None:
        words += f' ("{due.said}")'
    return words
