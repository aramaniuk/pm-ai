"""AD-42 — the self-improvement loop, and the two ways it fails silently.

Both failures pass every ordinary test: the system keeps working, the scores
keep improving, and what was lost is exactly what the product was for.
"""

from __future__ import annotations

import pytest

from pm_ai.domain.selfimprovement import (
    Adaptable,
    Adaptation,
    ChallengeSignals,
    Confidence,
    ForbiddenAdaptation,
    IndexComponent,
    Scorecard,
    SycophancyGuard,
    authorize,
)

FLAT = ChallengeSignals(question_ratio=0.8, blind_spots_surfaced=3, experiments_proposed=1)
SOFTER = ChallengeSignals(question_ratio=0.4, blind_spots_surfaced=1, experiments_proposed=0)
LOW, HIGH = Scorecard(6, 6, 6), Scorecard(9, 9, 9)
TWEAK = Adaptation(Adaptable.PERSONA, diff="soften the follow-ups", motivated_by="felt harsh")


def _auth(scores, prior_scores, signals, prior_signals, adaptation=TWEAK):
    return authorize(
        adaptation,
        scores=scores,
        prior_scores=prior_scores,
        signals=signals,
        prior_signals=prior_signals,
    )


def test_rising_scores_with_falling_challenge_are_refused():
    """AD-42.4 — the flattery loop, which every metric reports as success.

    A coach tuned on 'was that helpful?', rated by the person being coached,
    converges on agreement. This is the one place in the system where an
    improving number is evidence of a problem.
    """
    with pytest.raises(SycophancyGuard):
        _auth(HIGH, LOW, SOFTER, FLAT)


def test_rising_scores_with_held_challenge_are_allowed():
    """The guard must not block genuine improvement, or it gets removed."""
    assert _auth(HIGH, LOW, FLAT, FLAT).target is Adaptable.PERSONA


def test_falling_challenge_alone_is_not_blocked():
    """Challenge can dip for reasons unrelated to tuning; only the *pairing* is the signal."""
    assert _auth(LOW, HIGH, SOFTER, FLAT).target is Adaptable.PERSONA


def test_the_adaptable_surface_is_closed():
    """AD-42.1 — a system that can widen its own permissions has none."""
    with pytest.raises(ForbiddenAdaptation):
        _auth(HIGH, LOW, FLAT, FLAT, adaptation=Adaptation("skill_registry", "add skill", "faster"))  # type: ignore[arg-type]

    assert {a.value for a in Adaptable} == {"persona", "retrieval_weighting", "memory_patterns"}
    for forbidden in ("skill_registry", "permissions", "egress", "scope"):
        assert forbidden not in {a.value for a in Adaptable}


def test_the_scorecard_cannot_carry_the_pms_distress():
    """AD-42.3 — feeding it in teaches pm-ai that a bad week is bad coaching."""
    assert not hasattr(Scorecard(7, 7, 7), "domain_distress")
    with pytest.raises(TypeError):
        Scorecard(7, 7, 7, 9)  # type: ignore[call-arg]


def test_scorecard_rejects_off_scale_ratings():
    for bad in (0, 11, -1):
        with pytest.raises(ValueError):
            Scorecard(bad, 7, 7)


def test_an_estimate_is_rendered_as_an_estimate():
    """AD-42.5 — an index presenting an estimate as measurement marks its own homework."""
    assert IndexComponent("saved hours", 12, Confidence.ESTIMATED).render() == (
        "saved hours: 12 (estimated)"
    )
    assert IndexComponent("predictive accuracy", 0.82, Confidence.MEASURED).render() == (
        "predictive accuracy: 0.82"
    )


def test_authorize_never_applies_anything():
    """AD-42.2 — the most it may do is let a change become a Proposal."""
    result = _auth(HIGH, LOW, FLAT, FLAT)
    assert result is TWEAK, "authorize returns the adaptation for staging, it does not act"
    assert result.diff and result.motivated_by, "a revision the PM cannot read cannot be reverted"


def test_memory_and_retrieval_adaptations_pass_the_same_gate():
    """AD-42.1/42.4 — the loop is one gate, not one gate per adaptable surface.

    Retrieval weighting learned from what the PM engages with has the same shape
    of failure as coaching tuned on satisfaction: it converges on what the PM
    already wants, and stops surfacing the decision they keep avoiding.
    """
    for target in (Adaptable.RETRIEVAL_WEIGHTING, Adaptable.MEMORY_PATTERNS):
        change = Adaptation(target, diff="reweight", motivated_by="engagement")
        assert _auth(HIGH, LOW, FLAT, FLAT, adaptation=change).target is target
        with pytest.raises(SycophancyGuard):
            _auth(HIGH, LOW, SOFTER, FLAT, adaptation=change)
