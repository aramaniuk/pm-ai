"""Story 4t — `delete_goal`, the pure half of `pm-ai goal delete`.

What the slice tests in `tests/slice/test_goal_commands.py` cannot pin cheaply:
the byte-exact result at the edges of a file and of a section, and the refusals
`delete_goal` raises before anything is produced.
"""

from __future__ import annotations

import pytest

from pm_ai.core.goal_register import (
    GOALS_SCOPE,
    GoalAbsent,
    MalformedGoals,
    delete_goal,
    parse_goals,
)


def test_no_file_is_an_absent_goal():
    with pytest.raises(GoalAbsent):
        delete_goal(None, "g_a")


def test_an_id_not_on_file_is_an_absent_goal():
    with pytest.raises(GoalAbsent):
        delete_goal(b"## Project\n\n- [g_a] (short) A\n", "g_b")


def test_two_nested_goals_are_named_together():
    raw = (
        b"## Project\n\n- [g_a] (short) A\n"
        b"  - [g_c] (short) C\n"
        b"  - [g_b] (long) B\n"
    )
    with pytest.raises(MalformedGoals) as refused:
        delete_goal(raw, "g_a")
    message = str(refused.value)
    assert message.startswith("`g_b` and `g_c` are written inside 'g_a'")
    assert "those goals" in message


def test_the_last_line_without_a_trailing_newline():
    raw = b"## Project\n\n- [g_keep] (short) Keep\n- [g_a] (short) A"
    deleted = delete_goal(raw, "g_a")
    assert deleted.payload == b"## Project\n\n- [g_keep] (short) Keep\n"
    assert deleted.removed == ("- [g_a] (short) A",)


def test_the_first_goal_of_a_section():
    raw = (
        b"## Project\n\n- [g_a] (short) A\n  - detail\n- [g_keep] (short) Keep\n\n"
        b"## Team\n\n- [g_t] (long) T\n"
    )
    deleted = delete_goal(raw, "g_a")
    assert deleted.payload == (
        b"## Project\n\n- [g_keep] (short) Keep\n\n## Team\n\n- [g_t] (long) T\n"
    )
    assert deleted.removed == ("- [g_a] (short) A", "  - detail")
    assert sorted(parse_goals(deleted.payload, scope=GOALS_SCOPE)) == ["g_keep", "g_t"]


def test_the_only_goal_leaves_its_heading_and_the_prose():
    raw = b"\xef\xbb\xbf# Goals\r\n\r\n## Project\r\n\r\n- [g_a] (short) A\r\n\r\nNotes.\r\n"
    deleted = delete_goal(raw, "g_a")
    assert deleted.payload == b"\xef\xbb\xbf# Goals\r\n\r\n## Project\r\n\r\n\r\nNotes.\r\n"
    assert deleted.removed == ("- [g_a] (short) A",)
    assert deleted.goal.goal_id == "g_a"
