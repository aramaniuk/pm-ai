"""The `Commitment` entity, the ledger's line kinds and the PM's marks (story 15a).

A commitment is what an approved `action_item` or `implicit_commitment`
becomes: one `pending` line in the `commitments_log.md` of the scope that owns
its meeting, keyed by the proposal's root id (AD-14, AD-28). Its state is a
`CommitmentState`, never a `ProposalState`; its due is kept as said beside the
date worked out from it, or `unresolved`.

The PM's own marks — fulfilled, altered, broken, withdrawn — are allowed only
from the states `MARKABLE` lists. Story 15c writes them; 15a's reader folds
them by the same table.

The line grammar and every rule over it are in `pm_ai.core.commitment_log`;
nothing here reads a file.

Imports nothing from `pm_ai` but sibling domain modules (AD-30).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import datetime as _dt
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType

from pm_ai.domain.lifecycle import CommitmentState

__all__ = [
    "COMMITMENTS_LOG",
    "Cites",
    "Commitment",
    "Due",
    "DUE_NONE",
    "DUE_UNRESOLVED",
    "LineKind",
    "MARKABLE",
    "Mark",
    "MarkAnswer",
    "NotACommitmentLine",
    "NotACommitmentScope",
    "StateSource",
    "can_mark",
]

COMMITMENTS_LOG = "commitments_log.md"
"""The ledger's basename, in a project's `memory/` and in the personal one (AD-28)."""

DUE_UNRESOLVED = "unresolved"
"""Words were said, and they name no one date, or the meeting's day is unknown."""

DUE_NONE = "none"
"""Nothing was said."""


class NotACommitmentScope(ValueError):
    """A commitment ledger asked of a scope that keeps none (AD-4, AD-28).

    Only `personal` and a project keep `commitments_log.md`. A `people` scope,
    the application scope, or anything else is refused, naming it, before
    anything is read or written.
    """


class NotACommitmentLine(TypeError):
    """`CommitmentLog.append` given something other than a `CommitmentLine` (AD-28).

    Only `render_line` builds a line, from a `Commitment` or a mark, so a
    `CoachingCommitment` or any other undertaking never reaches either ledger.
    The store is never called.
    """


class LineKind(Enum):
    """The kinds of line the ledger holds (story 15a)."""

    PENDING = "pending"
    """Written once, at approval: the commitment and its values."""
    REVISED = "revised"
    """An edit after approval: the whole set of values after it."""
    MARKED = "marked"
    """The PM's own mark (15c writes it)."""
    DUE_SET = "due_set"
    """A due date the PM set (15c)."""
    OWNER_SET = "owner_set"
    """An owner the PM set or cleared (15c)."""
    VERDICT = "verdict"
    """Reserved for story 16's telemetry verdicts; 15a reads only its `state`."""


class StateSource(Enum):
    """Where the state in force came from."""

    NONE = "none"
    PM = "pm"
    TELEMETRY = "telemetry"


class Mark(Enum):
    """A mark the PM may make; each value is a `CommitmentState` value."""

    FULFILLED = "fulfilled"
    ALTERED = "altered"
    BROKEN = "broken"
    WITHDRAWN = "withdrawn"

    @property
    def state(self) -> CommitmentState:
        return CommitmentState(self.value)


class MarkAnswer(Enum):
    """What the table says of one mark from one state."""

    YES = "yes"
    ALREADY = "already"
    """Already in that state, by the PM's own mark: nothing is written."""
    REFUSED = "refused"


_Y, _A, _R = MarkAnswer.YES, MarkAnswer.ALREADY, MarkAnswer.REFUSED


def _row(fulfilled: MarkAnswer, altered: MarkAnswer, broken: MarkAnswer, withdrawn: MarkAnswer) -> Mapping[Mark, MarkAnswer]:
    return MappingProxyType(
        {
            Mark.FULFILLED: fulfilled,
            Mark.ALTERED: altered,
            Mark.BROKEN: broken,
            Mark.WITHDRAWN: withdrawn,
        }
    )


_ALL = _row(_Y, _Y, _Y, _Y)

MARKABLE: Mapping[tuple[CommitmentState, StateSource], Mapping[Mark, MarkAnswer]] = MappingProxyType(
    {
        (CommitmentState.PENDING, StateSource.NONE): _ALL,
        (CommitmentState.ALTERED, StateSource.PM): _ALL,
        (CommitmentState.UNKNOWN, StateSource.TELEMETRY): _ALL,
        (CommitmentState.ERROR, StateSource.TELEMETRY): _ALL,
        # His confirming mark, or overriding it.
        (CommitmentState.FULFILLED, StateSource.TELEMETRY): _ALL,
        # A promise he said was kept is not altered or withdrawn.
        (CommitmentState.FULFILLED, StateSource.PM): _row(_A, _R, _Y, _R),
        (CommitmentState.BROKEN, StateSource.TELEMETRY): _ALL,
        (CommitmentState.BROKEN, StateSource.PM): _row(_Y, _Y, _A, _Y),
        (CommitmentState.WITHDRAWN, StateSource.PM): _row(_Y, _Y, _Y, _A),
    }
)
"""The PM's marks and the states they are allowed from (Andrei, 2026-10-24).

The PM's mark outranks telemetry; `FULFILLED`, `BROKEN` and `WITHDRAWN` are
terminal for telemetry, and only his mark moves a commitment out of them.
After his `altered` mark the commitment is still tracked, so a later verdict
applies (Andrei, 2026-10-24).
"""


def mark_answer(state: CommitmentState, source: StateSource, mark: Mark) -> MarkAnswer:
    """The table's answer; a state and source the table does not list refuses."""
    row = MARKABLE.get((state, source))
    if row is None:
        return MarkAnswer.REFUSED
    return row[mark]


def can_mark(state: CommitmentState, source: StateSource, mark: Mark) -> bool:
    """Whether `mark` applies from `state` set by `source`: `MARKABLE` says yes."""
    return mark_answer(state, source, mark) is MarkAnswer.YES


@dataclass(frozen=True, slots=True)
class Due:
    """A due as said, and what was worked out from it (story 15a).

    `value` is a `YYYY-MM-DD` date, `unresolved` or `none`. `date` is `None`
    for the two words, and a commitment without a date is never overdue.
    `estimated` is true when the date was worked out against an estimated
    meeting day: it never alone makes a commitment late. The PM's own date
    (`due_set`) is never estimated.
    """

    said: str | None
    value: str
    estimated: bool = False

    def __post_init__(self) -> None:
        if self.value not in (DUE_UNRESOLVED, DUE_NONE):
            _dt.date.fromisoformat(self.value)

    @property
    def date(self) -> _dt.date | None:
        if self.value in (DUE_UNRESOLVED, DUE_NONE):
            return None
        return _dt.date.fromisoformat(self.value)

    @classmethod
    def nothing(cls) -> Due:
        return cls(None, DUE_NONE)

    @classmethod
    def unresolved(cls, said: str) -> Due:
        return cls(said, DUE_UNRESOLVED)

    @classmethod
    def on(cls, said: str | None, day: _dt.date, *, estimated: bool = False) -> Due:
        return cls(said, day.isoformat(), estimated)


@dataclass(frozen=True, slots=True)
class Cites:
    """What a commitment cites (AD-33): its meeting, and who said or owns it, as a name.

    A spoken promise cites its speaker; a summary's action item its owner,
    since the summary records who owns an item, not who said it.
    """

    meeting: str | None
    """`meeting:<id>`, or `None` when the proposal cites no meeting."""
    person: str | None = None


@dataclass(frozen=True, slots=True)
class Commitment:
    """An approved item, as the ledger holds it (FR-34, AD-14, story 15a).

    Self-contained (AD-33): it needs no transcript to be acted on or shown.
    `owner` and `speaker` are names as said or typed, never handles (AD-34);
    a reader links a name to a person only through an exact saved alias.
    """

    commitment_id: str
    """The proposal's root id: an edit's `<root>_e<n>` is the same commitment."""
    proposal_id: str
    """The proposal whose values it now holds."""
    type: str
    meeting_id: str | None
    title: str | None
    """The meeting's title as its record gave it when the `pending` line was written."""
    start: datetime | None
    day: date | None
    """The meeting's day the due was worked out against."""
    day_from: str | None
    """The `start_estimated_from` source when that day was estimated."""
    what: str
    owner: str | None
    speaker: str | None
    due: Due
    state: CommitmentState
    state_source: StateSource
    approved_at: datetime
    cites: Cites
    title_cut: bool = False
    what_cut: bool = False
    """A spoken promise's `what` over `WHAT_LIMIT`, cut at a word on its line
    (Andrei, 2026-10-24); the full text stays in the proposal body."""
