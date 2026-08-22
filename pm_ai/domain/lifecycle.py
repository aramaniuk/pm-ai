"""Lifecycles, verbs, and the coverage window.

Three convergent reviewer findings live here:

- `CommitmentState` had no way to say "unknown", so a harvest gap read as a
  broken promise and fired an irreversible nudge about delivered work.
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
    UNKNOWN = "unknown"  # overdue, but the window has no harvest coverage

    @property
    def is_terminal(self) -> bool:
        return self in {CommitmentState.FULFILLED, CommitmentState.BROKEN}


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
    """

    connector_instance: str
    start: datetime
    end: datetime

    def covers(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end


def evaluate_commitment(
    *,
    overdue: bool,
    evidence_admissible: bool,
    covered: bool,
) -> CommitmentState:
    """The fail-closed rule of AD-35 and AD-36, in one place.

    Both reviewers found this reasoning scattered and inconsistent. Two ways it
    silently goes wrong: counting pm-ai's own writes as evidence, and reading a
    harvest gap as a broken promise. FR-26 nudges are irreversible, so the
    absence of data resolves to UNKNOWN and never to BROKEN.
    """
    if evidence_admissible:
        return CommitmentState.FULFILLED
    if not overdue:
        return CommitmentState.PENDING
    if not covered:
        return CommitmentState.UNKNOWN
    return CommitmentState.BROKEN
