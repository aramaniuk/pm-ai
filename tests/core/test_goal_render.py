"""`strategic_goals.md`, written — the pure half of story 22b's matrix.

Spec: `_bmad-output/specs/spec-pm-ai/stories/22b-goal-writer.md`.

Every test hands `render_goals` or `set_goal` values and reads bytes back, with
`parse_goals` as the judge wherever the claim is agreement. No test takes
`tmp_path`: the writer returns bytes and opens nothing, and a test here that
needed a path would be evidence it had grown a write. The file-level rows — the
event log, the refused file left byte-identical on disk, the TTY — are in
`tests/slice/test_goal_setting.py`.

The hand-edit rows assert by **diffing lines**, not by parsing. A renderer that
regenerated the file from the parsed model would pass every round trip here and
still discard the PM's comments and section order, because the model carries
neither.
"""

from __future__ import annotations

import ast
import difflib
import re
from itertools import product
from pathlib import Path

import pytest

from pm_ai.core import goal_register
from pm_ai.core.goal_register import (
    HEADER,
    GoalRegister,
    MalformedGoals,
    declare_goal,
    parse_goals,
    render_goals,
    set_goal,
)
from pm_ai.domain.goals import Goal, GoalDomain, GoalHorizon
from pm_ai.domain.identity import DataScope, ScopeKind

PERSONAL = DataScope(ScopeKind.PERSONAL)

# Titles chosen to break a naive writer: every Markdown bracket the grammar's
# own tokens use, a pipe, a leading horizon-shaped token, a link, a checkbox
# shape, backticks, a heading hash, emphasis, and non-ASCII prose.
TITLES = (
    "Cut payment latency below 200ms",
    "Ship [v2] (beta) | then (GA)",
    "(short) is not the horizon here",
    "[g_other] (long) looks like a second goal",
    "See [the doc](http://example.com/x) first",
    "[ ] a checkbox-shaped title",
    "```not a fence```",
    "# not a heading",
    "**bold** and _italic_ and `code`",
    "Zespół — łączna przepustowość ≥ 2×",
    "trailing hashes ##",
    "a | b | c",
    "100% ) unbalanced ( parens ]",
)


def goal(
    goal_id: str = "g_a",
    title: str = "A goal",
    domain: GoalDomain = GoalDomain.PROJECT,
    horizon: GoalHorizon = GoalHorizon.SHORT,
) -> Goal:
    return Goal(goal_id=goal_id, title=title, domain=domain, horizon=horizon, scope=PERSONAL)


def parse(raw: bytes | None) -> GoalRegister:
    return parse_goals(raw, scope=PERSONAL)


def changed(before: bytes, after: bytes) -> list[str]:
    """The `+`/`-` lines of a line diff, which is what "untouched" is measured by."""
    return [
        line
        for line in difflib.ndiff(
            before.decode("utf-8").split("\n"), after.decode("utf-8").split("\n")
        )
        if line[:1] in {"+", "-"}
    ]


# ── Round trip ───────────────────────────────────────────────────────────────


def test_every_tricky_title_round_trips_byte_identical_in_every_domain_and_horizon():
    """Acceptance — the drift pair, over prose rather than three typed keys."""
    for index, (title, domain, horizon) in enumerate(
        product(TITLES, GoalDomain, GoalHorizon)
    ):
        register = {f"g_{index}": goal(f"g_{index}", title, domain, horizon)}
        back = parse(render_goals(register))
        assert back == register, title
        assert back[f"g_{index}"].title.encode("utf-8") == title.encode("utf-8")


def test_a_register_holding_every_title_at_once_round_trips():
    register = {
        f"g_{index}": goal(f"g_{index}", title, domain, horizon)
        for index, (title, domain, horizon) in enumerate(
            zip(TITLES, list(GoalDomain) * 5, list(GoalHorizon) * 5)
        )
    }
    assert parse(render_goals(register)) == register


def test_ids_at_the_edge_of_the_charset_round_trip():
    """`x` is a checkbox shape and `_`, `9.a-b` sit at the charset's corners."""
    register = {i: goal(i) for i in ("x", "X", "_", "9.a-b", "G_A", "g_a")}
    assert parse(render_goals(register)) == register


def test_an_empty_register_renders_the_header_and_parses_as_present_and_empty():
    back = parse(render_goals({}))
    assert back == {}
    assert back.present


# ── The header ───────────────────────────────────────────────────────────────


def test_the_header_carries_a_worked_example_parse_goals_accepts():
    """Acceptance — a self-documenting format whose example does not parse is worse than none."""
    rendered = render_goals({"g_a": goal()}).decode("utf-8")
    assert rendered.startswith(HEADER)
    fenced = re.search(r"^```[a-z]*\n(?P<body>.*?)^```$", HEADER, re.M | re.S)
    assert fenced is not None, "the header has no fenced example"
    example = parse(fenced["body"].encode("utf-8"))
    assert list(example) == ["g_payments_latency"]
    assert example["g_payments_latency"].domain is GoalDomain.PROJECT


def test_the_example_inside_the_header_never_becomes_a_goal():
    assert parse(HEADER.encode("utf-8")) == {}


# ── First, second and revised goals ──────────────────────────────────────────


def test_a_first_goal_creates_the_file_with_the_header_and_one_section():
    out = set_goal(None, goal("g_a", domain=GoalDomain.TEAM)).decode("utf-8")
    assert out.startswith(HEADER)
    assert out[len(HEADER) :] == "\n## Team\n\n- [g_a] (short) A goal\n"


def test_an_empty_file_is_treated_as_a_first_goal():
    assert set_goal(b"", goal()) == set_goal(None, goal())
    assert set_goal(b"\n\n", goal()) == set_goal(None, goal())


def test_a_second_goal_in_the_same_domain_joins_its_heading():
    first = set_goal(None, goal("g_a", "Keep me exactly"))
    second = set_goal(first, goal("g_b", "Second"))
    assert changed(first, second) == ["+ - [g_b] (short) Second"]
    assert second.decode("utf-8")[len(HEADER) :].count("## Project") == 1


def test_a_second_goal_in_a_new_domain_appends_a_heading_and_leaves_the_first_alone():
    first = set_goal(None, goal("g_a", domain=GoalDomain.PROJECT))
    second = set_goal(first, goal("g_t", "Team goal", GoalDomain.TEAM))
    assert second.startswith(first)
    assert second[len(first) :] == b"\n## Team\n\n- [g_t] (short) Team goal\n"


def test_a_revision_replaces_that_line_and_no_other():
    first = render_goals(
        {i: goal(i, f"title {i}") for i in ("g_a", "g_b", "g_c")}
    )
    revised = set_goal(first, goal("g_b", "a new title", horizon=GoalHorizon.LONG))
    assert changed(first, revised) == [
        "- - [g_b] (short) title g_b",
        "+ - [g_b] (long) a new title",
    ]


def test_an_unchanged_revision_leaves_the_file_byte_identical():
    first = render_goals({"g_a": goal()})
    assert set_goal(first, goal()) == first


# ── Refusals ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("brk", ["\n", "\r", "\r\n", "\u2028", "\x0b", "\x85"])
def test_a_title_carrying_a_line_break_is_refused(brk):
    with pytest.raises(MalformedGoals, match="line break"):
        render_goals({"g_a": goal(title=f"pasted{brk}from elsewhere")})
    with pytest.raises(MalformedGoals, match="line break"):
        set_goal(None, goal(title=f"pasted{brk}from elsewhere"))
    with pytest.raises(MalformedGoals, match="line break"):
        declare_goal(
            goal_id="g_a", domain="project", horizon="short",
            title=f"pasted{brk}from elsewhere", scope=PERSONAL,
        )


@pytest.mark.parametrize("bad", ["my goal", "goal:x", "-lead", "", "a]b", "é"])
def test_an_id_outside_the_charset_is_refused_naming_it(bad):
    with pytest.raises(MalformedGoals, match="letters, digits, `_`"):
        declare_goal(goal_id=bad, domain="project", horizon="short", title="T", scope=PERSONAL)
    with pytest.raises(MalformedGoals, match="letters, digits, `_`"):
        render_goals({bad: goal(bad)})


def test_an_unknown_domain_is_refused_against_the_closed_set():
    with pytest.raises(MalformedGoals, match="`personal`, `project` or `team`"):
        declare_goal(goal_id="g_a", domain="projetc", horizon="short", title="T", scope=PERSONAL)


def test_an_unknown_horizon_is_refused_against_the_closed_set():
    with pytest.raises(MalformedGoals, match="`short`, `medium` or `long`"):
        declare_goal(goal_id="g_a", domain="project", horizon="quarterly", title="T", scope=PERSONAL)


def test_a_value_that_is_not_a_member_of_either_enum_is_refused_at_render():
    rogue = Goal(goal_id="g_a", title="T", domain="project", horizon=GoalHorizon.SHORT, scope=PERSONAL)  # type: ignore[arg-type]
    with pytest.raises(MalformedGoals, match="domain"):
        render_goals({"g_a": rogue})
    rogue = Goal(goal_id="g_a", title="T", domain=GoalDomain.TEAM, horizon="soon", scope=PERSONAL)  # type: ignore[arg-type]
    with pytest.raises(MalformedGoals, match="horizon"):
        render_goals({"g_a": rogue})


def test_a_padded_or_empty_title_is_refused_at_render_and_stripped_at_declare():
    with pytest.raises(MalformedGoals, match="whitespace"):
        render_goals({"g_a": goal(title=" padded ")})
    with pytest.raises(MalformedGoals, match="no title"):
        render_goals({"g_a": goal(title="   ")})
    declared = declare_goal(
        goal_id=" g_a ", domain=" Team ", horizon="Tactical", title="  padded  ", scope=PERSONAL
    )
    assert declared == goal("g_a", "padded", GoalDomain.TEAM, GoalHorizon.MEDIUM)


def test_a_register_whose_key_disagrees_with_its_goal_is_refused():
    with pytest.raises(MalformedGoals, match="under the key"):
        render_goals({"g_b": goal("g_a")})


def test_a_malformed_file_is_refused_by_the_parsers_own_message():
    broken = b"## Project\n\n- [g_a] (quarterly) Bad horizon\n"
    with pytest.raises(MalformedGoals, match="line 3.*quarterly"):
        set_goal(broken, goal("g_b"))


# ── A hand-edited file ───────────────────────────────────────────────────────

HAND_EDITED = """\
# My goals

<!-- reviewed with Dana, 2026-09-01 -->

## Personal

* [g_staff] (strategic) Reach staff engineer
  - evidence: two cross-team designs

A note between sections.

## Project

1. [g_latency] (short) Cut latency (p99) [see dashboard]
2. [g_errors] (medium) Halve 5xx rate

### Q3 subsection

- [g_q3] (short) Something for Q3

## Notes

- ask Dana about the [budget doc](http://x)
""".encode("utf-8")


def test_a_set_on_a_hand_edited_file_changes_one_line_and_keeps_section_order():
    """Acceptance — diffed, because a regenerating renderer would pass a round trip."""
    out = set_goal(HAND_EDITED, goal("g_new", "Brand new", GoalDomain.PROJECT))
    assert changed(HAND_EDITED, out) == ["+ - [g_new] (short) Brand new"]
    text = out.decode("utf-8")
    assert text.index("## Personal") < text.index("## Project") < text.index("## Notes")
    assert "<!-- reviewed with Dana, 2026-09-01 -->" in text
    assert parse(out)["g_new"].domain is GoalDomain.PROJECT


def test_a_goal_joins_the_last_goal_of_its_domain_after_that_goals_detail_lines():
    out = set_goal(HAND_EDITED, goal("g_growth", "Mentor two", GoalDomain.PERSONAL)).decode()
    lines = out.split("\n")
    detail = lines.index("  - evidence: two cross-team designs")
    # The bullet character of the list it joins, so the list stays one list.
    assert lines[detail + 1] == "* [g_growth] (short) Mentor two"


def test_a_revision_in_a_hand_edited_file_keeps_marker_indent_and_horizon_spelling():
    out = set_goal(
        HAND_EDITED, goal("g_staff", "Reach principal", GoalDomain.PERSONAL, GoalHorizon.LONG)
    )
    assert changed(HAND_EDITED, out) == [
        "- * [g_staff] (strategic) Reach staff engineer",
        "+ * [g_staff] (strategic) Reach principal",
    ]
    ordered = set_goal(
        HAND_EDITED, goal("g_errors", "Halve 5xx rate", GoalDomain.PROJECT, GoalHorizon.LONG)
    )
    assert changed(HAND_EDITED, ordered) == [
        "- 2. [g_errors] (medium) Halve 5xx rate",
        "+ 2. [g_errors] (long) Halve 5xx rate",
    ]


def test_a_new_domain_on_a_hand_edited_file_is_appended_after_everything():
    out = set_goal(HAND_EDITED, goal("g_team", "Hire two", GoalDomain.TEAM))
    assert out.startswith(HAND_EDITED)
    assert out[len(HAND_EDITED) :] == b"\n## Team\n\n- [g_team] (short) Hire two\n"


def test_a_heading_with_no_goals_under_it_receives_the_goal():
    raw = b"# Goals\n\n## Team\n\n## Notes\n\nprose\n"
    out = set_goal(raw, goal("g_t", "T", GoalDomain.TEAM))
    assert out == b"# Goals\n\n## Team\n\n- [g_t] (short) T\n## Notes\n\nprose\n"
    assert parse(out)["g_t"].domain is GoalDomain.TEAM


def test_a_domain_change_moves_the_goal_and_its_detail_lines():
    out = set_goal(
        HAND_EDITED, goal("g_staff", "Reach staff engineer", GoalDomain.TEAM, GoalHorizon.LONG)
    )
    back = parse(out)
    assert back["g_staff"].domain is GoalDomain.TEAM
    text = out.decode("utf-8")
    assert text.endswith(
        "## Team\n\n- [g_staff] (long) Reach staff engineer\n"
        "  - evidence: two cross-team designs\n"
    )
    assert text.count("evidence: two cross-team designs") == 1


def test_crlf_endings_and_a_bom_survive_a_set():
    raw = b"\xef\xbb\xbf## Project\r\n\r\n- [g_a] (short) A\r\n"
    out = set_goal(raw, goal("g_b", "B"))
    assert out == b"\xef\xbb\xbf## Project\r\n\r\n- [g_a] (short) A\r\n- [g_b] (short) B\r\n"
    appended = set_goal(raw, goal("g_t", "T", GoalDomain.TEAM))
    assert appended == raw + b"\r\n## Team\r\n\r\n- [g_t] (short) T\r\n"


def test_a_file_with_no_trailing_newline_gains_a_section_cleanly():
    raw = b"## Project\n\n- [g_a] (short) A"
    out = set_goal(raw, goal("g_t", "T", GoalDomain.TEAM))
    assert out == b"## Project\n\n- [g_a] (short) A\n\n## Team\n\n- [g_t] (short) T\n"


def test_every_set_reads_back_as_the_old_register_plus_the_goal():
    base = parse(HAND_EDITED)
    for index, (title, domain, horizon) in enumerate(product(TITLES, GoalDomain, GoalHorizon)):
        new = goal(f"g_{index}", title, domain, horizon)
        assert parse(set_goal(HAND_EDITED, new)) == {**base, new.goal_id: new}, title


# ── The module opens nothing ─────────────────────────────────────────────────

ALLOWED_IMPORTS = frozenset({"__future__", "collections", "pm_ai", "re", "types", "typing"})
FILE_CALLS = frozenset(
    {"open", "read_text", "read_bytes", "write", "write_text", "write_bytes",
     "mkdir", "unlink", "rename", "replace", "iterdir", "glob"}
)


def test_the_goal_module_imports_nothing_that_reaches_a_filesystem():
    """`core` renders and something above it writes — `4a`/`4g`'s sweep, applied here."""
    tree = ast.parse(Path(goal_register.__file__).read_text(encoding="utf-8"))
    heads: set[str] = set()
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            heads |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            heads.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name in FILE_CALLS:
                calls.append(f"line {node.lineno}: {name}(...)")
    assert heads <= ALLOWED_IMPORTS, sorted(heads - ALLOWED_IMPORTS)
    assert not calls, calls


# ── Review patches ───────────────────────────────────────────────────────────


def test_an_id_with_a_trailing_newline_is_refused_by_both_writers():
    """`$` matches before a final `\\n`; the anchor is `\\Z` so this cannot pass."""
    bad = goal("g_a\n")
    with pytest.raises(MalformedGoals, match="letters, digits"):
        render_goals({"g_a\n": bad})
    with pytest.raises(MalformedGoals, match="letters, digits"):
        set_goal(None, bad)
    with pytest.raises(MalformedGoals, match="letters, digits"):
        set_goal(render_goals({"g_b": goal("g_b")}), bad)


@pytest.mark.parametrize(
    "raw", [b"\xef\xbb\xbf", "\u00a0\n\u3000\n".encode("utf-8"), b"\xef\xbb\xbf\n\n"]
)
def test_a_file_holding_only_a_bom_or_unicode_whitespace_gets_the_header(raw):
    out = set_goal(raw, goal())
    assert out == set_goal(None, goal())
    assert out.decode("utf-8").startswith(HEADER)


def test_the_fresh_render_is_read_back_too(monkeypatch):
    monkeypatch.setattr(goal_register, "render_goals", lambda register: b"## Team\n")
    with pytest.raises(MalformedGoals, match="would not read back"):
        set_goal(None, goal())


@pytest.mark.parametrize("delimiter", [".", ")"])
def test_a_goal_joining_an_ordered_list_takes_the_next_number(delimiter):
    raw = f"## Project\n\n1{delimiter} [g_a] (short) A\n2{delimiter} [g_b] (short) B\n".encode()
    out = set_goal(raw, goal("g_c", "C"))
    assert changed(raw, out) == [f"+ 3{delimiter} [g_c] (short) C"]
    assert list(parse(out)) == ["g_a", "g_b", "g_c"]


def test_tab_indented_details_stay_with_their_goal_on_insert_and_on_move():
    raw = b"## Project\n\n- [g_a] (short) A\n\t- tab detail\n\t  more\n\n## Notes\n"
    inserted = set_goal(raw, goal("g_b", "B"))
    assert inserted == (
        b"## Project\n\n- [g_a] (short) A\n\t- tab detail\n\t  more\n"
        b"- [g_b] (short) B\n\n## Notes\n"
    )
    moved = set_goal(raw, goal("g_a", "A", GoalDomain.TEAM))
    assert moved.endswith(b"## Team\n\n- [g_a] (short) A\n\t- tab detail\n\t  more\n")
    assert moved.count(b"tab detail") == 1
    assert parse(moved)["g_a"].domain is GoalDomain.TEAM


def test_a_revision_in_a_crlf_file_with_a_bom_is_exact():
    raw = b"\xef\xbb\xbf## Project\r\n\r\n- [g_a] (tactical) A\r\n- [g_b] (short) B\r\n"
    out = set_goal(raw, goal("g_a", "A revised", horizon=GoalHorizon.MEDIUM))
    assert out == (
        b"\xef\xbb\xbf## Project\r\n\r\n- [g_a] (tactical) A revised\r\n"
        b"- [g_b] (short) B\r\n"
    )
