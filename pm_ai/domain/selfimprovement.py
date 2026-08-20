"""The self-improvement loop (AD-42).

pm-ai adapts its own coaching. Two failure modes arrive wearing the same
clothes, and both are silent:

- a system that improves itself rewrites its own constraints, unless something
  names which parts are off limits;
- a coach tuned on "was that helpful?", rated by the person being coached,
  converges on flattery — scores climb, challenge disappears, and every metric
  says it is working.

The second is why `Scorecard` alone can never authorize an adaptation. The
counter-signals are a required argument, not an optional check.

Imports nothing from `pm_ai` except sibling domain modules (AD-30).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Adaptable(Enum):
    """What the loop may change. A closed set (AD-42.1).

    Everything absent is off limits — notably the skill registry, declared
    permissions, egress classes, and scope boundaries. Self-improvement operates
    inside the architecture, never on it.
    """

    PERSONA = "persona"  # persona.md — tone, questioning strategy
    RETRIEVAL_WEIGHTING = "retrieval_weighting"
    MEMORY_PATTERNS = "memory_patterns"


class ForbiddenAdaptation(PermissionError):
    """The loop tried to change something outside the adaptable set."""


class SycophancyGuard(RuntimeError):
    """Satisfaction rose while challenge fell — a regression, not an improvement."""


@dataclass(frozen=True, slots=True)
class Scorecard:
    """Post-1:1 ratings of pm-ai, on the 1-10 scale (FR-14).

    Three dimensions, all about pm-ai. `domain_distress` is recorded alongside
    and is deliberately NOT a field here: it measures the PM's world, and feeding
    it to the loop teaches pm-ai that a bad week is bad coaching.
    """

    coaching_efficiency: int
    dialogue_quality: int
    questioning_precision: int

    def __post_init__(self) -> None:
        for name in ("coaching_efficiency", "dialogue_quality", "questioning_precision"):
            value = getattr(self, name)
            if not 1 <= value <= 10:
                raise ValueError(f"{name}={value} is outside the 1-10 scale (FR-14)")

    @property
    def mean(self) -> float:
        return (
            self.coaching_efficiency + self.dialogue_quality + self.questioning_precision
        ) / 3


@dataclass(frozen=True, slots=True)
class ChallengeSignals:
    """What must not decline while satisfaction rises (AD-42.4, SM-C3).

    These are the Socratic premise, measured: how often pm-ai asks rather than
    tells, how often it surfaces something unwelcome, how often it proposes an
    experiment the PM did not request.
    """

    question_ratio: float  # FR-12 — share of turns ending in a question
    blind_spots_surfaced: int
    experiments_proposed: int  # FR-15

    def weaker_than(self, prior: ChallengeSignals) -> bool:
        return (
            self.question_ratio < prior.question_ratio
            or self.blind_spots_surfaced < prior.blind_spots_surfaced
            or self.experiments_proposed < prior.experiments_proposed
        )


@dataclass(frozen=True, slots=True)
class Adaptation:
    """A proposed change to pm-ai's own behaviour (AD-42.2).

    Carries the diff and the feedback that motivated it, because a persona
    revision the PM cannot read is one they cannot revert.
    """

    target: Adaptable
    diff: str
    motivated_by: str


def authorize(
    adaptation: Adaptation,
    *,
    scores: Scorecard,
    prior_scores: Scorecard,
    signals: ChallengeSignals,
    prior_signals: ChallengeSignals,
) -> Adaptation:
    """Gate an adaptation. Returns it for staging as a Proposal, or raises.

    Never applies anything: AD-42.2 requires approval, so the most this can do is
    let a change become a Proposal.
    """
    if not isinstance(adaptation.target, Adaptable):
        raise ForbiddenAdaptation(f"{adaptation.target!r} is not in the adaptable set")

    # AD-42.4 — the one place in this system where an improving number is
    # evidence of a problem.
    if scores.mean > prior_scores.mean and signals.weaker_than(prior_signals):
        raise SycophancyGuard(
            f"ratings rose ({prior_scores.mean:.1f} -> {scores.mean:.1f}) while challenge "
            f"fell. Optimising a score the coached person assigns selects for agreement; "
            f"this adaptation is refused (AD-42)."
        )
    return adaptation


class Confidence(Enum):
    """AD-42.5 — an index that presents an estimate as a measurement marks its own homework."""

    MEASURED = "measured"  # against outcomes pm-ai did not author (AD-36)
    ESTIMATED = "estimated"


@dataclass(frozen=True, slots=True)
class IndexComponent:
    name: str
    value: float
    confidence: Confidence

    def render(self) -> str:
        suffix = "" if self.confidence is Confidence.MEASURED else " (estimated)"
        return f"{self.name}: {self.value:g}{suffix}"
