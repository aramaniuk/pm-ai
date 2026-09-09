"""`strategic_goals.md`, interpreted — one test per row of the story's matrix.

Spec: `_bmad-output/specs/spec-pm-ai/stories/22a-goal-register.md`.

Every test hands `parse_goals` bytes or `None` and a scope. That is the whole
interface, and there is no filesystem anywhere in this module — no `tmp_path` in
any signature, nothing read from disk. A test here that needed a path would be
evidence the parser had grown a read, which is `4a`'s argument for
`tests/core/test_config.py` applied to the second hand-editable artifact.
"""

from __future__ import annotations

import pytest

from pm_ai.core.goal_register import (
    ARTIFACT,
    HORIZON_SPELLINGS,
    GoalRegister,
    MalformedGoals,
    parse_goals,
)
from pm_ai.domain.goals import (
    Goal,
    GoalDomain,
    GoalHorizon,
    Recommendation,
    alignment_tag,
)
from pm_ai.domain.identity import DataScope, ScopeKind

# The only scope this file is declared in (`scope_model.py:544`). Built here
# rather than defaulted in the parser — that is the point of the signature.
PERSONAL = DataScope(ScopeKind.PERSONAL)

WELL_FORMED = b"""# Strategic Goals

## Project

- [g_payments_latency] (short) Cut payment latency below 200ms

## Team

- [g_oncall_load] (medium) Halve the on-call pages per week

## Personal

- [g_staff_eng] (long) Reach staff engineer
"""


def parse(raw: bytes | None, *, scope: DataScope = PERSONAL) -> GoalRegister:
    return parse_goals(raw, scope=scope)


# ── Well-formed ──────────────────────────────────────────────────────────────


def test_a_well_formed_file_yields_a_register_keyed_by_id():
    register = parse(WELL_FORMED)
    assert set(register) == {"g_payments_latency", "g_oncall_load", "g_staff_eng"}
    assert register["g_payments_latency"] == Goal(
        goal_id="g_payments_latency",
        title="Cut payment latency below 200ms",
        domain=GoalDomain.PROJECT,
        horizon=GoalHorizon.SHORT,
        scope=PERSONAL,
    )
    assert register["g_oncall_load"].domain is GoalDomain.TEAM
    assert register["g_oncall_load"].horizon is GoalHorizon.MEDIUM
    assert register["g_staff_eng"].domain is GoalDomain.PERSONAL
    assert register["g_staff_eng"].horizon is GoalHorizon.LONG
    assert register.present


def test_the_domain_comes_from_the_heading_and_the_horizon_from_the_token():
    """The two axes stay apart — `goals.py`'s whole reason for separating them."""
    register = parse(WELL_FORMED)
    domains = {goal.domain for goal in register.values()}
    horizons = {goal.horizon for goal in register.values()}
    assert domains == set(GoalDomain)
    assert horizons == set(GoalHorizon)


def test_every_goal_carries_the_scope_the_caller_passed():
    """Asserted because a parser that defaulted `scope` re-opens AD-38.

    Nothing else would notice: `Goal` would construct, the register would look
    right, and the field that decides whether a git-committed scope may hold
    this data would have been chosen by the parser.
    """
    for goal in parse(WELL_FORMED).values():
        assert goal.scope == PERSONAL


def test_the_scope_is_not_hard_coded_to_personal():
    """The scope comes from the caller. A parser that ignored it would pass the
    test above by accident, so a second scope is handed in and checked."""
    other = DataScope(ScopeKind.PROJECT, project_id="acme")
    for goal in parse(WELL_FORMED, scope=other).values():
        assert goal.scope == other


def test_the_register_drives_alignment_tag_end_to_end():
    """The machinery works on real data for the first time.

    Against an empty register this raises `UnresolvedGoal` (`goals.py:90-95`),
    which is the defect the story exists to close.
    """
    register = parse(WELL_FORMED)
    tagged = Recommendation(text="Profile the checkout path", aligned_to="g_payments_latency")
    assert alignment_tag(tagged, register) == "[Strategic Alignment: Project]"
    assert alignment_tag(tagged, register) != alignment_tag(
        Recommendation(text="Draft the growth plan", aligned_to="g_staff_eng"), register
    )
    assert (
        alignment_tag(Recommendation(text="Fix the flake", aligned_to=None), register)
        == "[Strategic Alignment: UNALIGNED]"
    )


def test_a_parsed_id_is_citable():
    """`Goal.source_ref` is what a recommendation cites; it must survive."""
    goal = parse(WELL_FORMED)["g_payments_latency"]
    assert goal.source_ref.native_id == "g_payments_latency"


# ── Absent, empty, and present-but-goalless ──────────────────────────────────


def test_absent_is_an_empty_register_and_not_an_error():
    register = parse(None)
    assert register == {}
    assert not register.present


def test_an_empty_file_is_present_and_empty():
    register = parse(b"")
    assert register == {}
    assert register.present


def test_prose_with_no_goals_is_distinguishable_from_an_absent_file():
    """So `23a` says "no goals declared" rather than telling the PM to author a
    file they have already written."""
    written = parse(b"# Strategic Goals\n\nI will fill this in after the offsite.\n")
    assert written == {}
    assert written.present
    assert written != parse(None) or written.present != parse(None).present


def test_absent_and_present_are_the_only_difference_between_two_empty_registers():
    """Named so the `dict`-equality decision is deliberate rather than a
    surprise: equality is over the goals, `present` is asserted directly."""
    assert parse(None) == parse(b"")
    assert parse(None).present is False
    assert parse(b"").present is True


# ── Edit tolerance ───────────────────────────────────────────────────────────


def test_a_deliberately_messy_but_valid_hand_edited_file_parses():
    """Everything a real file accumulates: a title, a worked example in a fence,
    padding, casing, synonyms, prose between goals, reordered sections, a stray
    `## Notes` section with prose bullets, and a BOM from an editor."""
    raw = (
        b"\xef\xbb\xbf# Strategic Goals\n"
        b"\n"
        b"Reviewed monthly. The format is:\n"
        b"\n"
        b"```\n"
        b"## Project\n"
        b"- [g_example] (medium) A goal reads like this\n"
        b"```\n"
        b"\n"
        b"##   Personal   \n"
        b"\n"
        b"Career, not delivery.\n"
        b"\n"
        b"-   [g_staff_eng]   ( Strategic )   Reach staff engineer\n"
        b"\n"
        b"### **Team:**\n"
        b"\n"
        b"* [g_oncall_load] (TACTICAL) Halve the on-call pages per week\n"
        b"\n"
        b"## Project\n"
        b"\n"
        b"- [g_payments_latency] (Operational) Cut payment latency below 200ms\n"
        b"\n"
        b"## Notes\n"
        b"\n"
        b"- ask Dana about the latency budget\n"
        b"- revisit after Q3\n"
    )
    register = parse(raw)
    assert set(register) == {"g_staff_eng", "g_oncall_load", "g_payments_latency"}
    assert register["g_staff_eng"].horizon is GoalHorizon.LONG
    assert register["g_staff_eng"].domain is GoalDomain.PERSONAL
    assert register["g_oncall_load"].horizon is GoalHorizon.MEDIUM
    assert register["g_oncall_load"].domain is GoalDomain.TEAM
    assert register["g_payments_latency"].horizon is GoalHorizon.SHORT
    assert register["g_payments_latency"].title == "Cut payment latency below 200ms"
    # The fenced worked example is documentation, not a fourth goal.
    assert "g_example" not in register


def test_enum_values_are_stripped_and_case_folded():
    register = parse(b"## Project\n- [g_a] ( Short ) A\n\n##  team  \n- [g_b] (LONG) B\n")
    assert register["g_a"].horizon is GoalHorizon.SHORT
    assert register["g_b"].domain is GoalDomain.TEAM


@pytest.mark.parametrize(
    ("spelling", "horizon"),
    [
        ("operational", GoalHorizon.SHORT),
        ("tactical", GoalHorizon.MEDIUM),
        ("strategic", GoalHorizon.LONG),
    ],
)
def test_the_synonyms_goalhorizon_documents_are_accepted(spelling, horizon):
    """`GoalHorizon`'s own docstring names these against SHORT/MEDIUM/LONG."""
    register = parse(f"## Project\n- [g_a] ({spelling}) A\n".encode())
    assert register["g_a"].horizon is horizon


def test_the_accepted_horizon_spellings_are_exactly_six():
    """Named here so a seventh is a deliberate act rather than a quiet one."""
    assert set(HORIZON_SPELLINGS) == {
        "short",
        "medium",
        "long",
        "operational",
        "tactical",
        "strategic",
    }


def test_surrounding_prose_is_ignored_and_the_goals_still_parse():
    raw = (
        b"Some notes before anything.\n\n"
        b"## Project\n\n"
        b"These came out of the Q3 planning session.\n\n"
        b"- [g_a] (short) A\n\n"
        b"Still thinking about a second one.\n\n"
        b"- [g_b] (long) B\n"
    )
    assert set(parse(raw)) == {"g_a", "g_b"}


def test_reordered_sections_parse_identically():
    """Order in the file carries no meaning."""
    forward = b"## Project\n- [g_a] (short) A\n\n## Personal\n- [g_b] (long) B\n"
    backward = b"## Personal\n- [g_b] (long) B\n\n## Project\n- [g_a] (short) A\n"
    assert parse(forward) == parse(backward)


def test_a_prose_bullet_outside_a_domain_section_is_prose():
    """The `## Notes` section a real file grows, and the reason a bullet is only
    a goal inside a domain section."""
    assert parse(b"## Notes\n\n- ask Dana about the latency budget\n") == {}


# ── Refusals ─────────────────────────────────────────────────────────────────


def test_a_duplicate_id_is_refused_and_the_message_names_the_id():
    """A register that silently kept one would make `resolve` return an
    arbitrary goal for every `goal:g_a` citation."""
    raw = b"## Project\n- [g_a] (short) First\n\n## Team\n- [g_a] (long) Second\n"
    with pytest.raises(MalformedGoals) as refusal:
        parse(raw)
    assert "g_a" in str(refusal.value)
    assert "line 2" in str(refusal.value)
    assert "line 5" in str(refusal.value)


def test_an_unknown_horizon_is_refused_and_the_message_lists_the_closed_set():
    with pytest.raises(MalformedGoals) as refusal:
        parse(b"## Project\n- [g_a] (quarterly) A\n")
    message = str(refusal.value)
    assert "quarterly" in message
    for spelling in HORIZON_SPELLINGS:
        assert f"`{spelling}`" in message


def test_a_horizon_that_is_nearly_right_is_still_refused():
    """`short-term` is the spelling a PM reaches for and is not one of the six."""
    with pytest.raises(MalformedGoals):
        parse(b"## Project\n- [g_a] (short-term) A\n")


def test_a_goal_under_an_unknown_domain_is_refused_and_the_three_are_listed():
    with pytest.raises(MalformedGoals) as refusal:
        parse(b"## Marketing\n\n- [g_a] (short) A\n")
    message = str(refusal.value)
    assert "`## Project`" in message
    assert "`## Team`" in message
    assert "`## Personal`" in message


def test_a_goal_before_any_heading_is_refused_rather_than_dropped():
    """A goal written above the sections has no domain, and the file has to say
    so — dropping it makes `goal:g_a` unresolvable with nothing to point at."""
    with pytest.raises(MalformedGoals) as refusal:
        parse(b"- [g_a] (short) A\n\n## Project\n- [g_b] (short) B\n")
    assert "line 1" in str(refusal.value)


def test_a_missing_id_is_refused_and_the_line_is_named():
    with pytest.raises(MalformedGoals) as refusal:
        parse(b"## Project\n\n- Cut payment latency below 200ms\n")
    message = str(refusal.value)
    assert "line 3" in message
    assert "Cut payment latency below 200ms" in message


def test_an_empty_id_is_refused():
    with pytest.raises(MalformedGoals) as refusal:
        parse(b"## Project\n- [] (short) A\n")
    assert "line 2" in str(refusal.value)


def test_a_missing_title_is_refused():
    """`Goal.title` is required too — an id is not a thing a PM reads."""
    with pytest.raises(MalformedGoals) as refusal:
        parse(b"## Project\n- [g_a] (short)\n")
    assert "title" in str(refusal.value)


def test_a_goal_shaped_block_that_breaks_is_refused_not_read_as_prose():
    """The row that separates "surfaced, never dropped" from "ignore what does
    not match". Prose, then one goal-shaped bullet with a broken horizon."""
    raw = (
        b"# Strategic Goals\n\n"
        b"Written after the offsite.\n\n"
        b"## Project\n\n"
        b"The payments work is the whole quarter.\n\n"
        b"- [g_payments_latency] short Cut payment latency below 200ms\n"
    )
    with pytest.raises(MalformedGoals) as refusal:
        parse(raw)
    message = str(refusal.value)
    assert "line 9" in message
    assert "- [id] (horizon) Title" in message


def test_a_goal_that_loses_its_brackets_is_refused():
    with pytest.raises(MalformedGoals):
        parse(b"## Project\n- g_a (short) A\n")


@pytest.mark.parametrize(
    "goal_id",
    ["my id", "a:b", "g/a", "[nested]", "-leading", ".leading", "goal id"],
)
def test_an_id_that_is_not_citation_safe_is_refused(goal_id):
    """`SourceRef.parse('goal:my id')` succeeds (`identity.py:223-227`) — it only
    counts colon-separated parts — so the charset is what actually rejects this.
    A citation with a space in it is unparseable by anything splitting on
    whitespace."""
    with pytest.raises(MalformedGoals) as refusal:
        parse(f"## Project\n- [{goal_id}] (short) A\n".encode())
    assert "citation" in str(refusal.value).lower()


def test_a_valid_id_charset_is_accepted():
    """The charset is stated, so the accepted end of it is stated too."""
    register = parse(b"## Project\n- [g.a-b_C9] (short) A\n")
    assert "g.a-b_C9" in register


def test_non_utf8_is_refused_at_decode_distinctly_from_a_grammar_failure():
    with pytest.raises(MalformedGoals) as refusal:
        parse(b"## Project\n- [g_a] (short) caf\xe9\n")
    message = str(refusal.value)
    assert "UTF-8" in message
    assert ARTIFACT in message
    assert "encoding problem" in message


def test_the_byte_offset_survives_a_bom():
    """The offset is reported against the file, not the buffer `_decode` sliced —
    three bytes short would misdirect precisely the files it tolerates."""
    body = b"## Project\n- [g_a] (short) caf\xe9\n"
    with pytest.raises(MalformedGoals) as bare:
        parse(body)
    with pytest.raises(MalformedGoals) as with_bom:
        parse(b"\xef\xbb\xbf" + body)
    assert "byte 30" in str(bare.value)
    assert "byte 33" in str(with_bom.value)


def test_one_bad_goal_refuses_the_whole_file():
    """A partial register is indistinguishable from a complete one, so the goals
    that did parse are not returned."""
    raw = (
        b"## Project\n- [g_a] (short) A\n- [g_b] (yearly) B\n- [g_c] (long) C\n"
    )
    with pytest.raises(MalformedGoals):
        parse(raw)


def test_malformed_goals_is_not_unresolved_goal():
    """A goal that failed to parse and a goal that was never written are
    different facts (`goals.py:57`)."""
    from pm_ai.domain.goals import UnresolvedGoal

    assert not issubclass(MalformedGoals, UnresolvedGoal)
    assert not issubclass(UnresolvedGoal, MalformedGoals)
    assert issubclass(MalformedGoals, ValueError)
