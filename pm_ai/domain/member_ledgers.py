"""The two ledgers story 7e adds, and the title match's attempt row, as values.

- `member_updates.md`, in a project's `memory/`: each action item and decision
  of a project meeting that names one of the project's GitLab issues, with the
  issue and the speaker label as said. Tier 1, append-only, a project's only.
- `aliases.md`, in the application scope beside `disclosure.md`: the saved
  alias table (AD-34), one row per name the PM confirmed, mapping it to a
  handle. Tier 1, append-only.
- The title match's attempt row, in `operational.db` (Tier 2), keyed by
  `(scope, meeting)`: when the local model could not be asked which issue an
  item is about, the match alone is offered again at a later harvest, at most
  `MAX_MATCH_ATTEMPTS` times.

The line grammars and every rule over them are in `pm_ai.core.aliases` and
`pm_ai.core.member_updates`; nothing here reads a file or a row.

Imports nothing from `pm_ai` but `pm_ai.domain` (AD-30).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pm_ai.domain.identity import DataScope, ScopeKind

__all__ = [
    "ALIAS_SCOPE",
    "ALIAS_TABLE",
    "MAX_MATCH_ATTEMPTS",
    "MEMBER_UPDATES",
    "MatchFence",
    "MatchMovedOn",
    "MatchState",
    "MatchStateKind",
    "NotAProjectScope",
]

MEMBER_UPDATES = "member_updates.md"
"""A project's meeting updates on its GitLab issues (story 7e)."""

ALIAS_TABLE = "aliases.md"
"""The saved alias table: name → handle (AD-34, story 7e)."""

ALIAS_SCOPE = DataScope(ScopeKind.APPLICATION)
"""The alias table's one home: one table serves every scope."""

MAX_MATCH_ATTEMPTS = 3
"""A meeting's title match is given up after this many failed calls."""


class NotAProjectScope(ValueError):
    """A project's meeting-update ledger asked of a scope that is not a project.

    Raised by the type that carries the write target (`ProjectScope`) and by
    the storage reader and writer, naming the scope: a personal or `people`
    meeting has no ledger, and no issue list enters its prompt.
    """


class MatchStateKind(Enum):
    """Where one meeting's title match got to."""

    WAITING = "waiting"
    """Items wait for the local model: offered again at the next harvest."""
    MATCHED = "matched"
    """The model was asked and answered; nothing waits."""
    GIVEN_UP = "given up"
    """`MAX_MATCH_ATTEMPTS` calls failed; the items are dropped from matching."""


@dataclass(frozen=True, slots=True)
class MatchState:
    """One meeting's title-match row (story 7e): Tier 2, keyed by scope and meeting.

    `pending` counts the items and decisions that wait for the match, `attempts`
    the calls that failed since the meeting was last summarised — a call the
    local model could not be asked at all (no model, Ollama not answering) is
    not one — and `version` moves with every write, which is what stops a
    retry that read an older row.
    """

    scope: DataScope
    meeting_id: str
    state: MatchStateKind
    pending: int
    attempts: int
    reason: str
    at: datetime
    version: int
    meeting_start: datetime


@dataclass(frozen=True, slots=True)
class MatchFence:
    """A retry's claim on the row: the version it read."""

    scope: DataScope
    meeting_id: str
    version: int


class MatchMovedOn(Exception):
    """The title-match row moved past the version a retry read: it writes nothing."""
