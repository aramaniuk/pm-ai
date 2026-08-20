"""AD-41 — the micro-decision alignment engine.

pm-ai's stated purpose is continuous alignment of daily micro-decisions across
goal horizons. It had no invariant in the architecture at all: it survived as a
directory name and as three characters inside an FR-range in the capability map,
which reads as coverage while fixing nothing.

These tests defend the two things that made it a real contract rather than a
formatting convention — one vocabulary, and no aligned-by-omission.
"""

from __future__ import annotations

import pytest

from pm_ai.domain.goals import (
    Goal,
    GoalDomain,
    GoalHorizon,
    Recommendation,
    UnresolvedGoal,
    alignment_tag,
    order,
    resolve,
    unaligned,
)
from pm_ai.domain.identity import DataScope, ScopeKind

PERSONAL = DataScope(ScopeKind.PERSONAL)
REFACTOR = Goal("goal_01", "Q3 auth refactor", GoalDomain.PROJECT, GoalHorizon.LONG, PERSONAL)
GROWTH = Goal("goal_02", "Delegate more", GoalDomain.PERSONAL, GoalHorizon.MEDIUM, PERSONAL)
REGISTER = {g.goal_id: g for g in (REFACTOR, GROWTH)}


def test_the_tag_names_the_domain_not_the_horizon():
    """AD-41 — the PRD names both axes 'tier', which is how surfaces diverge.

    FR-11 says short/medium/long, §2.1 says Project/Team/Personal, UJ-9 says
    Strategic/Tactical/Operational. One surface tagging `Team` and another
    tagging `Long-Term` would both be compliant with the requirement as written.
    """
    assert alignment_tag(Recommendation("spec it", "goal_01"), REGISTER) == (
        "[Strategic Alignment: Project]"
    )
    # Same goal, different axis — the horizon must never leak into the tag.
    assert REFACTOR.horizon is GoalHorizon.LONG
    assert "Long" not in alignment_tag(Recommendation("spec it", "goal_01"), REGISTER)


def test_the_two_axes_are_independent():
    """A personal goal can be short-term; a project goal can be long-term."""
    assert REFACTOR.domain is GoalDomain.PROJECT and REFACTOR.horizon is GoalHorizon.LONG
    assert GROWTH.domain is GoalDomain.PERSONAL and GROWTH.horizon is GoalHorizon.MEDIUM
    assert {d.value for d in GoalDomain} == {"project", "team", "personal"}
    assert {h.value for h in GoalHorizon} == {"short", "medium", "long"}


def test_an_unalignable_task_is_shown_unaligned_not_untagged():
    """AD-41 — aligned-by-omission is the failure mode.

    A briefing that reads as strategic while its tags mean nothing is worse than
    one that admits the gap, because alignment is the claim the product is sold
    on.
    """
    rec = Recommendation("answer Laura's email", None)
    assert not rec.is_aligned
    assert alignment_tag(rec, REGISTER) == "[Strategic Alignment: UNALIGNED]"
    assert resolve(rec, REGISTER) is None


def test_a_hand_edit_that_removes_a_goal_surfaces_rather_than_degrades():
    """AD-41/AD-34 — strategic_goals.md is hand-editable Tier-1 markdown.

    A citation that quietly degrades to nothing corrupts every metric built on
    it. This is AD-34's actor rule, applied to goals.
    """
    edited = {"goal_02": GROWTH}  # the PM deleted goal_01 by hand
    with pytest.raises(UnresolvedGoal):
        resolve(Recommendation("spec it", "goal_01"), edited)
    with pytest.raises(UnresolvedGoal):
        alignment_tag(Recommendation("spec it", "goal_01"), edited)


def test_a_recommendation_must_state_its_alignment():
    """`aligned_to` is required, so 'forgot to align' cannot look like 'could not'."""
    with pytest.raises(TypeError):
        Recommendation("no alignment field")  # type: ignore[call-arg]


def test_goals_are_cited_by_reference_under_the_ad34_grammar():
    """AD-41/AD-33 — the citation is the link; the rationale snippet only explains it."""
    assert str(REFACTOR.source_ref) == "goal:goal_01"


# ── Ranking and visibility (AD-41.6, AD-41.7) ────────────────────────────────


URGENT = lambda r: 10 if "incident" in r.text else 0  # noqa: E731


def test_alignment_lifts_a_task_above_an_unaligned_peer():
    """AD-41.6 — a citation to a goal is evidence the work advances a growth path."""
    chore = Recommendation("expense report", None)
    refactor = Recommendation("auth refactor", "goal_01")
    assert [r.text for r in order((chore, refactor))] == ["auth refactor", "expense report"]


def test_alignment_lifts_but_does_not_override_urgency():
    """AD-41.6 — the rule that keeps the ranking trustworthy.

    A ranking that buries a production incident behind a long-term refactor
    because the refactor cites a goal is one the PM stops trusting after the
    first outage.
    """
    fire = Recommendation("prod incident", None)  # unaligned but urgent
    refactor = Recommendation("auth refactor", "goal_01")  # aligned, not urgent
    assert [r.text for r in order((refactor, fire), urgency=URGENT)] == [
        "prod incident",
        "auth refactor",
    ]


def test_ordering_is_stable_so_there_is_no_hidden_third_criterion():
    """Equal on both keys means the caller's order survives."""
    a = Recommendation("a", None)
    b = Recommendation("b", None)
    assert [r.text for r in order((a, b))] == ["a", "b"]
    assert [r.text for r in order((b, a))] == ["b", "a"]


def test_unaligned_work_is_surfaced_as_a_set_not_buried():
    """AD-41.7 — unaligned work is the drift signal, not noise.

    A week of it is what FR-24's drift audit exists to catch. Ranking it lower is
    correct; hiding it destroys the only evidence that the PM is drifting.
    """
    items = (
        Recommendation("auth refactor", "goal_01"),
        Recommendation("expense report", None),
        Recommendation("prod incident", None),
    )
    drift = unaligned(items)
    assert {r.text for r in drift} == {"expense report", "prod incident"}
    # Every unaligned item is still present in the ordered list — none dropped.
    assert len(order(items)) == len(items)
    assert all(r in order(items) for r in drift)


def test_ranking_cannot_be_gamed_by_asserting_alignment():
    """AD-41.7 corollary — promotion must not create an incentive to tag.

    Alignment comes only from a resolvable citation (rule 2). A task claiming a
    goal that does not exist raises rather than quietly ranking high.
    """
    fake = Recommendation("looks strategic", "goal_does_not_exist")
    assert fake.is_aligned  # it claims alignment...
    with pytest.raises(UnresolvedGoal):  # ...and the claim is checked
        alignment_tag(fake, REGISTER)
