"""A meeting as `pm-ai transcript list` shows it, and the commands it offers (story 11f).

`ListedMeeting` is the one typed value the listing, the drop's closing line
and both JSON forms are built from: the meeting's record as read, its
transcript's state, its summary's state, and the next step or why there is
none. `pm_ai.core.meeting_listing` decides it from the rows, the capture
names, the record and the clock; `pm_ai.app` does the reads.

Every summarize command pm-ai prints is spelled by `summarize_words`, which
adds `--again` when the record holds a summary: the re-summary guard refuses
a command without it (story 11f), so a printed command that left it out
would be refused as printed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

__all__ = [
    "AGAIN_FLAG",
    "LISTING_FORMAT",
    "LIST_STATES",
    "Listing",
    "ListingRefused",
    "ListedMeeting",
    "ListedSummary",
    "ListedTranscript",
    "NextStep",
    "ON_FOUND_BY",
    "TITLE_CANDIDATES_SHOWN",
    "PEOPLE_REFUSAL",
    "RecordReading",
    "Unreadable",
    "WaitingKind",
    "drop_words",
    "review_words",
    "summarize_command",
    "summarize_words",
]

LISTING_FORMAT = 1
"""The `format` of both JSON forms. A new field or value does not change it; a
field removed, renamed or retyped does (story 11f's change rules)."""

AGAIN_FLAG = "again"
"""`transcript summarize --again`: replace a summary the record already holds."""

PEOPLE_REFUSAL = (
    "a meeting in {scope} is not listed here: summaries are made only for meetings "
    "in `personal` or `project:<id>`. Nothing was read."
)
"""`transcript list`'s refusal of a scope it does not read (`people:`, `application`)."""

ON_FOUND_BY = "on"
"""A drop's `found_by` when `--on` chose its meeting (story 11f)."""

TITLE_CANDIDATES_SHOWN = 10
"""How many candidates an ambiguous `--title` or `--on` names before counting the rest."""

LIST_STATES = ("all", "not-summarized", "waiting", "failed", "summarized")
"""What `transcript list --state` takes, `all` the default."""


class ListingRefused(ValueError):
    """A scope `transcript list` will not read: `people:`, or a project not enrolled.

    Raised before anything is read for `people:`; the CLI maps it to exit 3.
    """


class RecordReading(Enum):
    """Whether the meeting's record could be read: the JSON's `record`."""

    READABLE = "readable"
    UNREADABLE = "unreadable"
    MISSING = "missing"


class WaitingKind(Enum):
    """Which of the waiting rows a summary row in `waiting` is."""

    HARVEST = "harvest"
    """Offered at the daemon's next harvest, by the local model."""
    NO_LOCAL_MODEL = "no local model"
    """Would be offered, but `local_model` is not set, so no harvest makes it."""
    CLAUDE = "claude"
    """A request for Claude still waiting: no harvest ever offers it."""
    TOO_OLD = "too old"
    """The meeting started more than 30 days ago: never offered automatically."""
    NOT_INGESTED = "not ingested"
    """The transcript is not ingested, so no harvest offers it."""
    RECORD_UNREADABLE = "record unreadable"
    """The record cannot be read, so its start is unknown and no harvest offers it."""


@dataclass(frozen=True, slots=True)
class ListedTranscript:
    """The transcript's state: the row's state, its reason, and whether it is kept.

    `state` is the stored state (`fetched`, `ingested`, `ingest_failed`,
    `refused`, `absent`, `skipped`), `none` for no row, or `unknown` when the
    operational store could not be read. `kept` is set for `ingested` only.
    """

    state: str
    reason: str | None = None
    kept: bool | None = None


@dataclass(frozen=True, slots=True)
class ListedSummary:
    """The summary's state: the row's, and whether the record holds a summary.

    `state` is `none`, `waiting`, `summarized`, `failed`, `nothing_said` or
    `unknown`. `held` is whether the record holds a summary, `None` when the
    record is not readable; `held_by`, `held_model` and `held_on` describe the
    one it holds. `made_by` is `local` or `claude`, `--by`'s words.
    """

    state: str
    held: bool | None = None
    made_by: str | None = None
    requested: bool | None = None
    model: str | None = None
    reason: str | None = None
    at: datetime | None = None
    offered_automatically: bool | None = None
    waiting: WaitingKind | None = None
    held_by: str | None = None
    held_model: str | None = None
    held_on: date | None = None
    held_requested: bool | None = None


@dataclass(frozen=True, slots=True)
class NextStep:
    """The next command, as words after the program name, or why there is none.

    `summarize` and `resummarize` are `transcript summarize`'s words, the
    second ending with `--again`; `drop` is `transcript drop`'s words, to
    which the caller appends the file. `why` is set when there is no step, or
    when the step needs the daemon started first.
    """

    summarize: tuple[str, ...] | None = None
    resummarize: tuple[str, ...] | None = None
    drop: tuple[str, ...] | None = None
    why: str | None = None
    needs_daemon: bool = False
    review: tuple[str, ...] | None = None
    """Story 13a: `proposal review`'s words, when the meeting has proposals waiting."""


@dataclass(frozen=True, slots=True)
class ListedMeeting:
    """One meeting as the listing shows it (story 11f).

    `title`, `start` and `start_estimated` are the record's, `None` unless it
    is readable; `record_reason` says why it is not. Never holds transcript
    text, summary text, action items or attendees.
    """

    scope: str
    meeting_id: str
    record: RecordReading
    transcript: ListedTranscript
    summary: ListedSummary
    next: NextStep
    title: str | None = None
    start: datetime | None = None
    start_estimated: bool | None = None
    record_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Unreadable:
    """A source the listing could not read, and why: `personal`, `project:<id>`,
    `projects.toml` or `operational.db`."""

    source: str
    reason: str


@dataclass(frozen=True, slots=True)
class Listing:
    """What `transcript list` found: the meetings shown and what was looked at.

    `zone` is `display_timezone`, empty when unset; `zone_note` says why UTC
    is used when it is. `more` counts the meetings that match the filter and
    were left out by `--limit`. `store_readable` is false when the operational
    store could not be read.
    """

    zone: str
    scopes: tuple[str, ...]
    daemon_reachable: bool
    state: str
    limit: int
    meetings: tuple[ListedMeeting, ...] = ()
    unreadable: tuple[Unreadable, ...] = ()
    more: int = 0
    store_readable: bool = True
    zone_note: str = ""


def summarize_words(
    scope: str, meeting_id: str, *, held: bool, by: str | None = None
) -> tuple[str, ...]:
    """`transcript summarize`'s words for one meeting; `--again` when a summary is held.

    The one spelling of every summarize command pm-ai prints (story 11f):
    the listing's, the drop's, the dashboard's, the "asks again" sentences and
    `doctor`'s. `by` adds `--by <word>` when given.
    """
    words: list[str] = ["transcript", "summarize", "--scope", scope, "--meeting", meeting_id]
    if by is not None:
        words += ["--by", by]
    if held:
        words.append(f"--{AGAIN_FLAG}")
    return tuple(words)


def summarize_command(
    scope: str, meeting_id: str, *, held: bool, by: str | None = None
) -> str:
    """`summarize_words` as the line a person types: `pm-ai transcript summarize …`."""
    return "pm-ai " + " ".join(summarize_words(scope, meeting_id, held=held, by=by))


def review_words(scope: str, meeting_id: str) -> tuple[str, ...]:
    """`proposal review`'s words for one meeting's waiting proposals (story 13a)."""
    return ("proposal", "review", "--scope", scope, "--meeting", meeting_id)


def drop_words(scope: str, meeting_id: str) -> tuple[str, ...]:
    """`transcript drop`'s words for one meeting, without the file (story 11f)."""
    return ("transcript", "drop", "--scope", scope, "--meeting", meeting_id)
