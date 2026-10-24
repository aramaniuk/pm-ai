"""The single Proposal entity (AD-13) with versioned transitions (AD-37).

Story 13a adds what AD-13 promises around the entity: the table of allowed
changes, refused by `IllegalTransition`; the review window, which starts when
pm-ai staged the proposal and lasts the longer of its stored time to live and
its type's registered one; and the body fields every change records.

Every change is checked by `check_change`, in this order: a terminal state
(`TerminalState`); then a waiting proposal past its window, for any change but
the move to `expired` (`PastWindow`, itself a `TerminalState`); then the
version (`VersionConflict`); then the table (`IllegalTransition`). So a stale
version on an overdue staged proposal is refused as expired, not as a version
mismatch. A missing proposal is the store's `ProposalNotFound`, before all. The store's conditional updates and `pm_ai.core.proposals`'
in-memory book both call it, so the architecture tests check the rules the
store enforces.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from pm_ai.domain.identity import SourceRef, TargetRef
from pm_ai.domain.lifecycle import DEFAULT_PROPOSAL_TTL, ProposalState

__all__ = [
    "CHANGED_VIA",
    "IMPLICIT_COMMITMENT",
    "SPEAKER_LIMIT",
    "IllegalTransition",
    "OpenActionItems",
    "PastWindow",
    "Proposal",
    "TerminalState",
    "TypeRule",
    "VersionConflict",
    "allowed",
    "as_compared",
    "check_change",
    "fold_typed",
    "meeting_of",
    "repeat_key",
    "replacement_id",
    "replacement_number",
    "root_of",
]


class VersionConflict(RuntimeError):
    """AD-37 — someone else moved this proposal; reload and re-evaluate."""


class TerminalState(RuntimeError):
    """AD-13/AD-37 — expired, rejected, or already executed proposals never run."""


class PastWindow(TerminalState):
    """A waiting proposal past its review window: expired to every reader (13a).

    Raised whether or not the sweep has moved the row, for any change other
    than the move to `expired` itself. `expired_at` is when the window closed.
    """

    def __init__(self, message: str, *, expired_at: datetime) -> None:
        super().__init__(message)
        self.expired_at = expired_at


class IllegalTransition(RuntimeError):
    """A change the domain's table does not allow (13a), such as `staged → executed`."""


IMPLICIT_COMMITMENT = "implicit_commitment"
"""The type `11b` stages a spoken promise under; `pm_ai.core` names it from here (15a)."""

SPEAKER_LIMIT = 80
"""A typed owner's or a promise speaker's most characters: one number for both (13a, 15a)."""

CHANGED_VIA = ("terminal", "prompt", "sweep", "summary", "ingestion", "telegram")
"""Where a change came from: a proposal body's `changed_via` (13a; story 5 adds `telegram`)."""


@dataclass(frozen=True, slots=True)
class TypeRule:
    """What the change rules need to know of a proposal's registered type (13a).

    `ttl` is the registered review window; `has_executor` and
    `records_commitment` decide the edges out of `approved`. `known` is false
    for a type no registration names: its only allowed change is `staged →
    rejected`, and its window is its stored one.
    """

    ttl: timedelta
    has_executor: bool = False
    known: bool = True
    records_commitment: bool = False
    """Its approval records a commitment (story 15a): once approved, it is not rejected."""


UNKNOWN_TYPE = TypeRule(ttl=timedelta(0), known=False)
"""The rule of a stored type no registration names."""


_EDGES: dict[ProposalState, frozenset[ProposalState]] = {
    ProposalState.STAGED: frozenset(
        {
            ProposalState.APPROVED,
            ProposalState.REJECTED,
            ProposalState.EXPIRED,
            ProposalState.SUPERSEDED,
        }
    ),
    ProposalState.EXECUTING: frozenset({ProposalState.EXECUTED}),
}
"""The edges that hold for every known type; `approved`'s depend on the executor."""


def allowed(source: ProposalState, to: ProposalState, rule: TypeRule | None) -> bool:
    """Whether the table allows `source → to` for a type with `rule` (AD-13, 13a).

    `staged → approved | rejected | expired | superseded`; `approved →
    superseded` for every type; `approved → rejected` for a type with no
    executor whose approval records no commitment (15a); `approved →
    executing | expired` for a type with one; `executing → executed`. An
    unknown type (`rule` `None` or not `known`) may only move `staged →
    rejected`.
    """
    if rule is None or not rule.known:
        return source is ProposalState.STAGED and to is ProposalState.REJECTED
    if source is ProposalState.APPROVED:
        if to is ProposalState.SUPERSEDED:
            return True
        if rule.has_executor:
            return to in (ProposalState.EXECUTING, ProposalState.EXPIRED)
        return to is ProposalState.REJECTED and not rule.records_commitment
    return to in _EDGES.get(source, frozenset())


def replacement_id(root: str, made: int) -> str:
    """`<root>_e<n>`, `n` one more than the replacements already made from `root` (13a).

    The one spelling of a replacement's id; `replacement_number` and `root_of`
    read it back.
    """
    return f"{root}_e{made + 1}"


def replacement_number(proposal_id: str) -> int | None:
    """`n` of a replacement's `<root>_e<n>`; `None` for an id `7b` or `11b` minted."""
    head, sep, tail = proposal_id.rpartition("_e")
    if sep and head and tail.isascii() and tail.isdigit() and not tail.startswith("0"):
        return int(tail)
    return None


def root_of(proposal_id: str) -> str:
    """The id `7b` minted: a replacement's `<root>_e<n>` without its `_e<n>` (13a)."""
    if replacement_number(proposal_id) is None:
        return proposal_id
    return proposal_id.rpartition("_e")[0]


def fold_typed(text: str) -> str:
    """Line breaks and other control (Cc) and format (Cf) characters as spaces,
    then runs of spaces as one, trimmed (13a)."""
    spaced = "".join(
        " " if unicodedata.category(char) in ("Cc", "Cf") else char for char in text
    )
    return " ".join(spaced.split())


def repeat_key(what: object, owner: object) -> tuple[str, str]:
    """`what` and `owner` folded and case-folded: two items equal here repeat each other (13a)."""
    return (
        fold_typed(what if isinstance(what, str) else "").casefold(),
        fold_typed(owner if isinstance(owner, str) else "").casefold(),
    )


def as_compared(kind: str, stored: object, new: object) -> object:
    """`new` as compared with a `stored` payload of type `kind` (story 15a).

    A spoken promise staged before 15a stores no `speaker`. A re-ingestion of
    the same transcript, which now carries one, compares as equal when the
    rest is equal: no rewrite, no version bump, nothing counted as dropped.
    """
    if (
        kind == IMPLICIT_COMMITMENT
        and isinstance(stored, Mapping)
        and isinstance(new, Mapping)
        and "speaker" not in stored
        and "speaker" in new
    ):
        return {key: value for key, value in new.items() if key != "speaker"}
    return new


def meeting_of(cites: SourceRef) -> str | None:
    """The meeting a proposal cites, when it cites one (`meeting:<id>`, AD-33)."""
    if cites.system == "meeting" and cites.scope is None:
        return cites.native_id
    return None


@dataclass(frozen=True, slots=True)
class Proposal:
    proposal_id: str
    type: str
    summary: str
    payload: dict
    target: TargetRef | None  # `None` for a promise, or a spoken target the grammar refused (11b)
    cites: SourceRef
    created_at: datetime
    state: ProposalState = ProposalState.STAGED
    version: int = 1
    ttl: timedelta = DEFAULT_PROPOSAL_TTL
    staged_at: datetime | None = None
    """When pm-ai staged it (13a): the review window starts here, never at the
    meeting's start. Read as `created_at` from a body written before 13a."""
    scope: str | None = None
    """The scope it belongs to, as `DataScope` spells it; `None` when not recorded."""
    replaces: str | None = None
    """For an edit's replacement: the proposal it superseded (13a)."""
    changed_at: datetime | None = None
    """The instant of the last change, or `None` when none was recorded."""
    changed_via: str | None = None
    """Where the last change came from (`CHANGED_VIA`), or `None`."""

    @property
    def id(self) -> str:
        return self.proposal_id

    @property
    def staged_instant(self) -> datetime:
        """When the review window starts: `staged_at`, else `created_at`."""
        return self.staged_at if self.staged_at is not None else self.created_at

    @property
    def expires_at(self) -> datetime:
        """The window's end under the stored time to live alone."""
        return self.staged_instant + self.ttl

    @property
    def meeting_id(self) -> str | None:
        return meeting_of(self.cites)

    def window(self, rule: TypeRule | None) -> timedelta:
        """The longer of the stored and the registered time to live (13a)."""
        if rule is None or not rule.known:
            return self.ttl
        return max(self.ttl, rule.ttl)

    def expires_under(self, rule: TypeRule | None) -> datetime:
        return self.staged_instant + self.window(rule)

    def waiting(self, rule: TypeRule | None) -> bool:
        """Staged, or approved for a type with an executor: the states that expire."""
        if self.state is ProposalState.STAGED:
            return True
        return (
            self.state is ProposalState.APPROVED
            and rule is not None
            and rule.known
            and rule.has_executor
        )

    def overdue(self, rule: TypeRule | None, at: datetime) -> bool:
        """Waiting, and past its window at `at`."""
        return self.waiting(rule) and self.expires_under(rule) <= at

    def seen_state(self, rule: TypeRule | None, at: datetime) -> ProposalState:
        """The state every reader shows: a waiting proposal past its window is `expired`."""
        return ProposalState.EXPIRED if self.overdue(rule, at) else self.state

    def transition(
        self,
        to: ProposalState,
        *,
        expected_version: int,
        rule: TypeRule | None = None,
        at: datetime | None = None,
        via: str | None = None,
    ) -> Proposal:
        """Compare-and-swap. A blind retry is what creates the duplicate HR goal.

        `rule` defaults to a known type with no executor and this proposal's
        own window, which is what a caller with no registry means. `at`, when
        given, is the instant the window is checked at and the change is
        recorded at.
        """
        check_change(self, to, expected_version=expected_version, rule=rule, at=at)
        return replace(
            self,
            state=to,
            version=self.version + 1,
            changed_at=at if at is not None else self.changed_at,
            changed_via=via if via is not None else self.changed_via,
        )


def _default_rule(proposal: Proposal) -> TypeRule:
    return TypeRule(ttl=proposal.ttl)


def check_change(
    current: Proposal,
    to: ProposalState,
    *,
    expected_version: int,
    rule: TypeRule | None = None,
    at: datetime | None = None,
) -> None:
    """Refuse a change, in this order: terminal, past its window, version, then the table (13a).

    A waiting proposal past its window at `at` is terminal for every change
    but the move to `expired` (`PastWindow`), and that is checked before the
    version, so a stale version on an overdue proposal reads as expired.
    """
    effective = rule if rule is not None else _default_rule(current)
    if current.state.is_terminal:
        raise TerminalState(
            f"{current.proposal_id} is {current.state.value}; terminal states are "
            f"terminal, and a worker re-checks at execution time (AD-37)."
        )
    if at is not None and to is not ProposalState.EXPIRED and current.overdue(effective, at):
        closed = current.expires_under(effective)
        raise PastWindow(
            f"{current.proposal_id} expired on {closed.date().isoformat()}, not "
            f"reviewed: its review window closed, so it is not changed (AD-13).",
            expired_at=closed,
        )
    if expected_version != current.version:
        raise VersionConflict(
            f"{current.proposal_id} is at v{current.version}, caller expected "
            f"v{expected_version} — reload and re-evaluate (AD-37)."
        )
    if not allowed(current.state, to, effective):
        raise IllegalTransition(
            f"{current.proposal_id} cannot move {current.state.value} → {to.value}: "
            f"the domain's table of changes does not allow it (AD-13)."
        )


@dataclass(frozen=True, slots=True)
class OpenActionItems:
    """One meeting's open `action_item` proposals, and how many could not be read (7d).

    A staged proposal whose stored body does not parse is counted in
    `unreadable`, never dropped in silence and never failing the meeting's
    other items: the dashboard states the count. Since 13a "open" is waiting
    (staged, within its window) or approved, replacements included, and
    `expired` counts the items that expired without review and have no
    replacement.
    """

    items: tuple[Proposal, ...] = ()
    unreadable: int = 0
    expired: int = 0
