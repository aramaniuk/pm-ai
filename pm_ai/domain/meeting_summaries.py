"""A meeting's summary, its decisions and its action items, as values (story 7b).

The answer shape the local model is asked for, the refusal of an answer that
breaks it (`ModelAnswerRefused`, AD-49: a rule `domain` owns, declared beside
the shape), what the meeting record's `## Summary` region holds, and the
summary's state row. The shape is validated in `pm_ai.core.meeting_summary`;
nothing here reads a model or a file.

Imports nothing from `pm_ai` but `pm_ai.domain` (AD-30).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from pm_ai.domain.identity import DataScope

__all__ = [
    "ACTION_ITEM",
    "DUE_LIMIT",
    "FRONTIER_SUMMARY_REFUSED",
    "MAX_ACTION_ITEMS",
    "MAX_DECISIONS",
    "MAX_TIMEOUTS",
    "STATEMENT_LIMIT",
    "SUMMARY_LIMIT",
    "WHAT_LIMIT",
    "ModelAnswerRefused",
    "RecordedSummary",
    "SummaryActionItem",
    "SummaryAnswer",
    "SummaryDecision",
    "SummaryFence",
    "SummaryItemsStaged",
    "SummaryLeaseLost",
    "SummaryMaker",
    "SummaryRefused",
    "SummaryRule",
    "SummaryState",
    "SummaryStateKind",
]

# ── The answer's bounds ──────────────────────────────────────────────────────
# Bounds on what one answer may hold (story 7b). They do not guarantee that the
# largest valid answer fits the 4,096-token output cap (Andrei, 2026-10-20):
# at its bounds, JSON included and each speaker label counted at 40
# characters, it is 12,268 characters, which is about 3,300 tokens at the
# 3.72 characters per token measured on `qwen3:8b`'s largest real answer. Real
# answers measured at most 716 tokens. An answer that does reach the cap is
# `OutputCapReached`: asked once more, then refused naming the cap.

SUMMARY_LIMIT = 600
"""The summary's most characters."""

MAX_DECISIONS = 25
"""Raised from 10 (Andrei, 2026-10-20): a long meeting can make more decisions than 10."""
STATEMENT_LIMIT = 200
"""A decision's statement's most characters."""

MAX_ACTION_ITEMS = 15
WHAT_LIMIT = 200
"""An action item's `what`'s most characters."""

DUE_LIMIT = 40
"""An action item's `due`, as said, at most this many characters."""

MAX_TIMEOUTS = 3
"""The timeout that fails a summary as timing out: the third (story 7b)."""

ACTION_ITEM = "action_item"
"""The proposal type an action item is staged under — never executed."""


class SummaryRule(Enum):
    """Each rule of the answer shape an answer can break, by name.

    The value is the rule's name in a failure's reason; the retry asks again
    with one fixed sentence per rule (`pm_ai.core.meeting_summary`), never
    quoting the answer or the transcript.
    """

    NOT_JSON = "not one JSON object"
    KEYS = "not exactly the keys asked for"
    TYPES = "a value of the wrong type"
    TOO_MANY = "more decisions or action items than allowed"
    TOO_LONG = "a text longer than its limit"
    EMPTY = "an empty text"
    UNKNOWN_LABEL = "a said_by or owner that is not a speaker label of the transcript"
    TOO_LONG_AFTER_CLEANING = "a text longer than its limit once cleaned"
    EMPTY_AFTER_CLEANING = "a text left empty once cleaned"
    OUTPUT_CAP = "stopped at the output cap"


class ModelAnswerRefused(ValueError):
    """The model's answer broke the summary's shape, so nothing of it is used (AD-49).

    `rule` names the rule broken. The message names the rule and where, never
    the answer's text: an answer is model output, and a sentence quoting it
    would carry it into logs and the operational store.
    """

    def __init__(self, rule: SummaryRule, where: str) -> None:
        super().__init__(f"the answer was refused: {rule.value} ({where}).")
        self.rule = rule
        self.where = where


@dataclass(frozen=True, slots=True)
class SummaryDecision:
    """A decision made: what, and the speaker label of who said it, or `None`."""

    statement: str
    said_by: str | None = None


@dataclass(frozen=True, slots=True)
class SummaryActionItem:
    """An action item agreed: what, the owner's speaker label, and when, as said."""

    what: str
    owner: str | None = None
    due: str | None = None


@dataclass(frozen=True, slots=True)
class SummaryAnswer:
    """A validated, cleaned answer: every string has passed `sanitize` and its bound."""

    summary: str
    decisions: tuple[SummaryDecision, ...] = ()
    action_items: tuple[SummaryActionItem, ...] = ()


class SummaryMaker(Enum):
    """Who made a summary. Only the local model in this slice; `7c` adds Claude."""

    LOCAL = "local"


@dataclass(frozen=True, slots=True)
class RecordedSummary:
    """What a meeting record's `## Summary` region holds (story 7b).

    The provenance — made by the local model `model`, automatically or on
    request, on `made_on` — and the answer. pm-ai's region: written only by
    `MeetingRecords.put_summary`, carried over by every `put`.
    """

    model: str
    requested: bool
    made_on: date
    answer: SummaryAnswer
    made_by: SummaryMaker = SummaryMaker.LOCAL


class SummaryStateKind(Enum):
    """Where one meeting's summary got to, as its state row keeps it."""

    WAITING = "waiting"
    """Offered at the next harvest: never made, no model, a timeout, a later part."""
    SUMMARIZED = "summarized"
    FAILED = "failed"
    """Not offered again automatically; the PM may ask."""
    NOTHING_SAID = "nothing said"
    """Every utterance was empty once cleaned, so no call was made."""


@dataclass(frozen=True, slots=True)
class SummaryState:
    """One meeting's summary row (story 7b): Tier 2, never the record.

    `attempts` counts the calls that timed out since the row was last offered
    afresh — every other attempt settles the row — so the third fails it.
    `job` is the job holding the row's lease, if any; `version` moves with
    every write, which is what fences a job that read an older one (AD-37).
    """

    scope: DataScope
    meeting_id: str
    state: SummaryStateKind
    made_by: SummaryMaker
    requested: bool
    model: str | None
    attempts: int
    reason: str
    at: datetime
    version: int
    meeting_start: datetime
    job: str | None = None

    @property
    def timing_out(self) -> bool:
        """Waiting after a call that timed out, and not yet failed for it."""
        return self.state is SummaryStateKind.WAITING and self.attempts > 0


@dataclass(frozen=True, slots=True)
class SummaryFence:
    """What a summary job holds of its row: the version it read, and its lease.

    `job` is `None` until the job's first write takes the lease; then the row
    carries this job's name and the version that write set. Every later write
    checks both inside its own claimed step (AD-37).
    """

    scope: DataScope
    meeting_id: str
    version: int
    job: str | None = None


class SummaryLeaseLost(RuntimeError):
    """The summary row moved past the version this job read: it stops, writing nothing.

    A request for the same meeting, a later part re-opening it, or a calendar
    write resetting it moved the row to a new version (AD-37). Not a failure:
    whatever moved it is the summary on record.
    """


class SummaryRefused(RuntimeError):
    """A summary was refused before any model call, naming why (story 7b).

    The meeting is not recorded, its transcript row is not `ingested` (a part
    that failed ingestion is named), a capture is missing or purged, `--by
    claude` was asked for (`7c`), or no daemon is there to run it.
    """


FRONTIER_SUMMARY_REFUSED = (
    "`--by claude` asks Claude for the summary, which story 7c builds; it is "
    "not built yet, so nothing was sent. `--by local` (the default) asks the "
    "local model."
)
"""The refusal of a frontier summary until `7c`, said by the CLI and the daemon."""


@dataclass(frozen=True, slots=True)
class SummaryItemsStaged:
    """What staging a summary's action items did, by proposal id."""

    staged: tuple[str, ...] = ()
    left: tuple[str, ...] = ()
    """Already approved, rejected or expired: left as they were."""
    superseded: tuple[str, ...] = ()
    """Still staged, not produced by this summary: moved to `superseded`."""
    kept_superseded: tuple[str, ...] = ()
    """Produced again by this summary but superseded by an earlier one: a
    superseded proposal is terminal (AD-13), so it stays superseded. Not acted
    on by the PM, which is why it is never in `left`."""
