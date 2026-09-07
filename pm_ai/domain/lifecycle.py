"""Lifecycles, verbs, and the coverage window.

Three convergent reviewer findings live here:

- `CommitmentState` had no way to say "unknown", so a harvest gap read as a
  broken promise and fired an irreversible nudge about delivered work. It then
  had no way to say *why* it did not know, so a permanently dead connector was
  indistinguishable from a laptop that had been asleep — `ERROR` closes that
  (AD-35, 2026-08-27).
- "reversible" was defined per verb, when it is really per verb *per provider*:
  a Jira priority change is quiet, the same change in GitLab notifies thirty
  people, and one-tap undo cannot recall a notification.
- Proposal and Commitment states risked being conflated in one field.

Imports nothing from `pm_ai` except sibling domain modules (AD-30).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from pm_ai.domain.invariants import InconsistentModel
from pm_ai.domain.identity import SkillPermission


class ProposalState(Enum):
    """Approval status — distinct from real-world fulfilment (AD-14)."""

    STAGED = "staged"
    APPROVED = "approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"  # an edit supersedes rather than mutating in place

    @property
    def is_terminal(self) -> bool:
        return self in {
            ProposalState.EXECUTED,
            ProposalState.REJECTED,
            ProposalState.EXPIRED,
            ProposalState.SUPERSEDED,
        }


class CommitmentState(Enum):
    """Real-world fulfilment — never shares a field with ProposalState (AD-14)."""

    PENDING = "pending"
    FULFILLED = "fulfilled"
    ALTERED = "altered"
    BROKEN = "broken"
    UNKNOWN = "unknown"  # overdue, no coverage, and nothing tried to harvest
    ERROR = "error"  # overdue, no coverage, because harvesting FAILED

    @property
    def is_terminal(self) -> bool:
        return self in {CommitmentState.FULFILLED, CommitmentState.BROKEN}

    @property
    def is_verdict(self) -> bool:
        """Whether this says something about the promise rather than about us.

        `UNKNOWN` and `ERROR` are the two epistemic members: they report that the
        system cannot answer, and why. Everything else is a claim about the
        world. A surface that renders a commitment has to know the difference —
        "we could not check" must never be presented in the register of "you
        broke this".
        """
        return self not in {CommitmentState.UNKNOWN, CommitmentState.ERROR}


def _assert_lifecycles_are_distinct() -> None:
    """ProposalState and CommitmentState must never share a member name (AD-14).

    The two lifecycles answer different questions, and one overloaded field would
    conflate them. A function rather than a bare statement so the check can be
    re-run against a doctored model in a test — which is the only way to prove it
    still fires under `python -O`.
    """
    overlap = {s.value for s in ProposalState} & {s.value for s in CommitmentState}
    if overlap:
        raise InconsistentModel(f"AD-14: lifecycle states overlap: {sorted(overlap)}")


_assert_lifecycles_are_distinct()


DEFAULT_PROPOSAL_TTL = timedelta(days=7)


# ── Verbs ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Verb:
    """A mutation a skill can perform.

    `reversible` is per (verb, provider). AD-32 lets a reversible verb
    auto-execute from a spoken command, so getting this wrong turns a
    zero-friction feature into an unrecallable broadcast.

    `notifies` is tracked separately because a notification is not undoable even
    when the underlying change is: reverting a GitLab assignment does not unsend
    the email it triggered.
    """

    name: str
    provider: str
    permission: SkillPermission
    reversible: bool
    notifies: bool = False

    @property
    def auto_executable(self) -> bool:
        """AD-32's verb condition: reversible *and* quiet."""
        return self.reversible and not self.notifies


VERB_REGISTRY: dict[tuple[str, str], Verb] = {
    ("gitlab", "post_comment"): Verb("post_comment", "gitlab", SkillPermission.COMMENT, True),
    ("gitlab", "edit_description"): Verb("edit_description", "gitlab", SkillPermission.EDIT, True),
    ("gitlab", "set_label"): Verb("set_label", "gitlab", SkillPermission.EDIT, True),
    # Same verb name, different provider, different answer — the reason this
    # registry is keyed on the pair.
    ("gitlab", "set_priority"): Verb("set_priority", "gitlab", SkillPermission.EDIT, True, notifies=True),
    ("jira", "set_priority"): Verb("set_priority", "jira", SkillPermission.EDIT, True),
    ("jira", "post_comment"): Verb("post_comment", "jira", SkillPermission.COMMENT, True),
    ("gitlab", "close_work_item"): Verb("close_work_item", "gitlab", SkillPermission.TRANSITION, False),
    ("gitlab", "create_merge_request"): Verb("create_merge_request", "gitlab", SkillPermission.CREATE, False, notifies=True),
    ("teams", "send_message"): Verb("send_message", "teams", SkillPermission.SEND, False, notifies=True),
    ("outlook", "send_email"): Verb("send_email", "outlook", SkillPermission.SEND, False, notifies=True),
    ("hr", "sync_goal"): Verb("sync_goal", "hr", SkillPermission.CREATE, False),
}


class UnknownVerb(KeyError):
    """Not in the registry — fail closed rather than assume reversible."""


def lookup_verb(provider: str, name: str) -> Verb:
    try:
        return VERB_REGISTRY[(provider, name)]
    except KeyError:
        raise UnknownVerb(
            f"{provider}:{name} is not registered. An unregistered verb is never "
            f"auto-executable — reversibility is a property we assert, not infer."
        ) from None


# ── Coverage ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CoverageWindow:
    """When a connector instance actually harvested (AD-35).

    Recorded in `ingested_at` terms — the local clock — because it describes what
    the daemon did, not what happened in the world. Asking the coverage question
    in `occurred_at` terms is one of the mixed-clock bugs both reviewers found.

    **There is deliberately no `covers(moment)` here.** One existed until story
    `8a` and had no caller anywhere; it asked `start <= moment <= end`, a
    single-window point test that only looked usable while the GitLab connector
    fabricated a four-hour window wide enough for any recent instant to fall
    inside. Now that a window is the seconds a fetch actually took,
    `evaluate_commitment`'s `covered` needs the union of many windows over the
    period evidence would have arrived in, with a gap tolerance — the harvest
    cycle is the natural one. That fold reads `coverage_windows(instance)`; it is
    not a method on one window, and offering one invites the fabrication's
    assumption back in.
    """

    connector_instance: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        """Refuse a window that ends before it began, the way `CalendarWindow` does.

        A connector builds these from two readings of its own clock — the instant
        the first page came back and the instant fetching stopped — and a machine
        whose clock steps backwards between the two (an NTP correction, a laptop
        waking) would otherwise store `end < start`. Nothing downstream reads a
        reversed interval as suspect: the fold `evaluate_commitment`'s `covered`
        needs asks whether a union of windows spans a period, and a backwards one
        contributes a negative span, which is a hole nobody can see.

        `start == end` is **not** refused, unlike `CalendarWindow`'s range. A
        coverage window is the seconds a fetch actually took, and a fetch that
        began and finished inside one clock reading really did cover an instant —
        which is also the ordinary shape under a frozen test clock.
        """
        if self.end < self.start:
            raise ValueError(
                f"CoverageWindow for {self.connector_instance!r} ends at "
                f"{self.end.isoformat()}, before it began at "
                f"{self.start.isoformat()}. Coverage is two readings of one "
                f"connector's clock, so a reversed pair is a clock that moved "
                f"backwards mid-fetch rather than a window — and a negative span "
                f"in the coverage fold is a gap nothing reports."
            )


def evaluate_commitment(
    *,
    overdue: bool,
    evidence_admissible: bool,
    covered: bool,
    harvest_failed: bool,
) -> CommitmentState:
    """The fail-closed rule of AD-35 and AD-36, in one place.

    Both reviewers found this reasoning scattered and inconsistent. Two ways it
    silently goes wrong: counting pm-ai's own writes as evidence, and reading a
    harvest gap as a broken promise. FR-26 nudges are irreversible, so the
    absence of data never resolves to BROKEN.

    **Two silences, and they are distinguishable** (AD-35, revised 2026-08-27).
    A sleeping laptop made no harvest attempts at all: nothing tried, nothing
    failed, genuinely missing data — `UNKNOWN`, and waiting is the right
    behaviour. A connector with a dead token *did* try, and the attempts are in
    the log marked failed. That is not missing data; it is a machine reporting
    that it is broken, and it will keep reporting it forever. `ERROR` says so,
    and says the repair is a human's: refresh the token, re-authenticate, fix
    the link or the configuration. Collapsed into one value, a permanently dead
    connector read as patience.

    `harvest_failed` is **required and has no default**, deliberately. A default
    of `False` would be the safe verdict — `UNKNOWN` never fires an irreversible
    nudge — and would silently reintroduce exactly the indefinite waiting this
    parameter exists to end. `HarvestResult.outcome` is required and has no
    default for the same reason, and it is where this argument's value comes
    from: story `8a` gave the failure a durable home beside the cursor, so
    `StoragePort.harvest_failure(instance)` answers this question after a
    restart. Before it, both "ran and failed" and "ran and learned nothing"
    persisted as the absence of a coverage window and this parameter had no
    source at all — a required argument nobody could supply honestly.

    `HarvestResult.coverage` is **not** the arming mechanism, and reading it as
    one was the defect `8a` fixed. A mandatory `CoverageWindow` cannot be
    forgotten and can be fabricated: the GitLab connector satisfied it with
    `now() - 4h` to `now()`, so an empty `200` armed `covered` with evidence
    nothing had gathered. It is optional now, and its absence is a real answer.

    Order matters. `covered` is consulted before `harvest_failed`: if the window
    *was* harvested, absence of evidence within it is real evidence of absence,
    and a connector that broke afterwards does not retract that. `ERROR`
    competes only with `UNKNOWN`, never with `BROKEN`.
    """
    if evidence_admissible:
        return CommitmentState.FULFILLED
    if not overdue:
        return CommitmentState.PENDING
    if covered:
        return CommitmentState.BROKEN
    return CommitmentState.ERROR if harvest_failed else CommitmentState.UNKNOWN
