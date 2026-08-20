"""Goals and alignment (AD-41).

pm-ai exists to align daily micro-decisions against goals. That purpose had no
invariant: the spine carried it as a directory name and three characters inside
an FR-range, so three surfaces could each have invented a tier vocabulary and
all three would have been compliant.

Two axes live here, kept apart on purpose. The PRD names them
interchangeably — FR-11 says short/medium/long, §2.1 says
Project/Team/Personal, UJ-9 says Strategic/Tactical/Operational — and
conflating them is exactly how one surface tags `Team` while another tags
`Long-Term`.

Imports nothing from `pm_ai` except sibling domain modules (AD-30).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pm_ai.domain.identity import DataScope, SourceRef


class GoalDomain(Enum):
    """What a goal is about. The `<Tier>` in `[Strategic Alignment: <Tier>]`."""

    PROJECT = "project"
    TEAM = "team"
    PERSONAL = "personal"  # career growth


class GoalHorizon(Enum):
    """When it lands. What UJ-9's planning breakdown groups by — a separate axis."""

    SHORT = "short"  # operational
    MEDIUM = "medium"  # tactical
    LONG = "long"  # strategic


@dataclass(frozen=True, slots=True)
class Goal:
    """A Tier-1 record in `strategic_goals.md`, hand-editable by design (AD-3)."""

    goal_id: str
    title: str
    domain: GoalDomain
    horizon: GoalHorizon
    scope: DataScope

    @property
    def source_ref(self) -> SourceRef:
        """What a recommendation cites (AD-34's scopeless global form)."""
        return SourceRef.parse(f"goal:{self.goal_id}")


class UnresolvedGoal(ValueError):
    """The cited goal is not in the register — surfaced, never silently dropped."""


# `strategic_goals.md` is hand-editable, so a citation can outlive its goal. That
# is expected; what is forbidden is letting it degrade quietly (AD-34's rule for
# actors, applied to goals).
UNALIGNED = None


@dataclass(frozen=True, slots=True)
class Recommendation:
    """A surfaced task suggestion. Its alignment is part of its identity.

    `aligned_to` is required rather than defaulted: a recommendation that forgot
    to align cannot be distinguished from one that could not, and the second must
    be visible. Aligned-by-omission is the failure this whole AD exists to stop —
    a briefing that reads as strategic while its tags mean nothing.
    """

    text: str
    aligned_to: str | None  # a goal_id, or None for explicitly unaligned

    @property
    def is_aligned(self) -> bool:
        return self.aligned_to is not None


def resolve(recommendation: Recommendation, register: dict[str, Goal]) -> Goal | None:
    """Resolve a citation, or raise. Never returns a plausible-looking guess."""
    if recommendation.aligned_to is None:
        return UNALIGNED
    goal = register.get(recommendation.aligned_to)
    if goal is None:
        raise UnresolvedGoal(
            f"{recommendation.aligned_to!r} is cited but not in strategic_goals.md. "
            f"A hand-edit may have removed it. Surface the recommendation as "
            f"unaligned rather than dropping the tag (AD-41)."
        )
    return goal


def alignment_tag(recommendation: Recommendation, register: dict[str, Goal]) -> str:
    """The `[Strategic Alignment: <Tier>]` tag FR-11 requires on every line.

    The tier is the goal's DOMAIN, matching §2.1's "3-Tier Goals". The horizon is
    a separate axis and never appears here.
    """
    goal = resolve(recommendation, register)
    if goal is None:
        return "[Strategic Alignment: UNALIGNED]"
    return f"[Strategic Alignment: {goal.domain.value.capitalize()}]"


# ── Ranking (AD-41.6, AD-41.7) ───────────────────────────────────────────────


def rank_key(recommendation: Recommendation, *, urgency: int = 0) -> tuple[int, int]:
    """Sort key for any surface presenting a task list.

    Urgency leads, alignment breaks the tie. Alignment *lifts* rather than
    overrides: an unaligned production incident must still outrank a long-term
    refactor, because a ranking that buries genuine urgency behind strategy is
    one the PM stops trusting after the first outage.

    Higher `urgency` sorts earlier; the caller supplies it, because urgency is
    not this module's to define.
    """
    return (-urgency, 0 if recommendation.is_aligned else 1)


def order(
    recommendations: tuple[Recommendation, ...],
    *,
    urgency=lambda r: 0,
) -> tuple[Recommendation, ...]:
    """One ordering rule across FR-09, FR-13 and FR-32 (AD-41.6).

    `sorted` is stable, so recommendations equal on both keys keep the order the
    caller produced them in — no hidden third criterion.
    """
    return tuple(sorted(recommendations, key=lambda r: rank_key(r, urgency=urgency(r))))


def unaligned(recommendations: tuple[Recommendation, ...]) -> tuple[Recommendation, ...]:
    """The drift signal, surfaced as a set (AD-41.7).

    Unaligned work ranks lower and stays visible. A week of it is exactly what
    FR-24's drift audit exists to catch, so it is never collapsed, truncated, or
    summarized away — burying it destroys the only evidence that the PM is
    drifting. Unaligned does not mean unimportant.
    """
    return tuple(r for r in recommendations if not r.is_aligned)
