"""The single Proposal entity (AD-13) with versioned transitions (AD-37)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from pm_ai.domain.identity import SourceRef, TargetRef
from pm_ai.domain.lifecycle import DEFAULT_PROPOSAL_TTL, ProposalState


class VersionConflict(RuntimeError):
    """AD-37 — someone else moved this proposal; reload and re-evaluate."""


class TerminalState(RuntimeError):
    """AD-13/AD-37 — expired, rejected, or already executed proposals never run."""


@dataclass(frozen=True, slots=True)
class Proposal:
    proposal_id: str
    type: str
    summary: str
    payload: dict
    target: TargetRef
    cites: SourceRef
    created_at: datetime
    state: ProposalState = ProposalState.STAGED
    version: int = 1
    ttl: timedelta = DEFAULT_PROPOSAL_TTL

    @property
    def expires_at(self) -> datetime:
        return self.created_at + self.ttl

    def transition(self, to: ProposalState, *, expected_version: int) -> Proposal:
        """Compare-and-swap. A blind retry is what creates the duplicate HR goal."""
        if expected_version != self.version:
            raise VersionConflict(
                f"{self.proposal_id} is at v{self.version}, caller expected "
                f"v{expected_version} — reload and re-evaluate (AD-37)."
            )
        if self.state.is_terminal:
            raise TerminalState(
                f"{self.proposal_id} is {self.state.value}; terminal states are "
                f"terminal, and a worker re-checks at execution time (AD-37)."
            )
        return replace(self, state=to, version=self.version + 1)
