"""Story 15a — once approved, a type that records a commitment is not rejected.

The rule is in the domain's table (`allowed`), so the store's conditional
update refuses it for every surface; `ProposalType.rule` copies the type's
`commitment` flag into `TypeRule.records_commitment`.
"""

from __future__ import annotations

from datetime import timedelta

from pm_ai.core.proposals import (
    ACTION_ITEM_TYPE,
    EXPLICIT_COMMAND_TYPE,
    IMPLICIT_COMMITMENT_TYPE,
    PROPOSAL_TTL,
)
from pm_ai.domain.lifecycle import CommitmentState, ProposalState
from pm_ai.domain.proposals import TypeRule, allowed

APPROVED, REJECTED = ProposalState.APPROVED, ProposalState.REJECTED


def test_an_approved_commitment_type_is_not_rejected():
    rule = TypeRule(ttl=timedelta(days=14), records_commitment=True)
    assert not allowed(APPROVED, REJECTED, rule)
    # Editing it still is: an edit supersedes it into the same commitment.
    assert allowed(APPROVED, ProposalState.SUPERSEDED, rule)
    # A waiting one is rejected as before.
    assert allowed(ProposalState.STAGED, REJECTED, rule)


def test_an_approved_type_with_no_executor_and_no_commitment_is_rejected_as_before():
    assert allowed(APPROVED, REJECTED, TypeRule(ttl=timedelta(days=14)))


def test_the_rule_copies_the_commitment_flag():
    assert ACTION_ITEM_TYPE.rule.records_commitment
    assert IMPLICIT_COMMITMENT_TYPE.rule.records_commitment
    assert not EXPLICIT_COMMAND_TYPE.rule.records_commitment
    assert ACTION_ITEM_TYPE.rule == TypeRule(ttl=PROPOSAL_TTL, records_commitment=True)


def test_withdrawn_is_a_commitment_state_and_never_a_proposal_state():
    assert CommitmentState("withdrawn") is CommitmentState.WITHDRAWN
    assert "withdrawn" not in {state.value for state in ProposalState}
    assert CommitmentState.WITHDRAWN.is_terminal
