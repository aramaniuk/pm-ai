"""A caller trying to hand `render_project_dashboard` the PM's goals.

Not collected by pytest and not imported by anything — the filename is outside
`test_*.py` on purpose. It exists to be **type-checked and rejected**:
`test_project_rendering.py::test_a_register_cannot_be_passed_under_mypy` runs
`mypy` over this path as a subprocess and asserts the codes below.

An explicit path argument is what makes that possible. `[tool.mypy] files =
["pm_ai"]` in `pyproject.toml` means `uv run mypy` with no arguments never looks
at `tests/`, so the deliberate errors here stay invisible to the ordinary run
and are visible to the one test that wants them.

The precedent is `tests/connectors/test_coverage_honesty.py:911` — the shipped
one, which runs `mypy` on generated callers to pin `save_cursor`'s signature.
Story `8e` describes this shape too and is cited for it in several places, but
it is `ready-for-dev` and unbuilt, so nothing was verified there; a checked-in
fixture file rather than a generated one is this slice's own choice, taken
because these three calls want the commentary around them.

What `8e`'s precedent does have and is worth keeping is the **pair**: a bad call
refused *and* a good call accepted. Asserting only the refusals would hold just
as well against annotations that had regressed into rejecting everything, which
is a different bug with the same green test. The accepted half lives in
`mypy_fixture_project_render_valid.py`, and the two are checked together.

Every call below is wrong by design. `# type: ignore` on any of them would
delete the evidence this file exists to produce.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pm_ai.core.goal_register import GoalRegister
from pm_ai.core.rendering import render_project_dashboard

NOW = datetime(2026, 9, 9, 10, 30, tzinfo=timezone.utc)
DISPLAY = timezone(timedelta(hours=2))
REGISTER = GoalRegister(present=True)


def register_as_the_entries() -> str:
    """The third positional slot a four-argument habit would reach for.

    `render_dashboard(meetings, entries, goals, now, tz=...)` puts the register
    third. Here the third positional is `now`, and the second is the event log,
    so the muscle-memory call lands the register on `Sequence[EventEntry]`.
    `arg-type`, at the call site, in the checker — not in review.
    """
    return render_project_dashboard((), REGISTER, NOW, tz=DISPLAY)


def register_as_the_meetings() -> str:
    """The same refusal from the only other slot that takes a collection."""
    return render_project_dashboard(REGISTER, (), NOW, tz=DISPLAY)


def register_by_keyword() -> str:
    """And the direct attempt, which fails one step earlier than `arg-type`.

    There is no parameter to mistype against: `call-arg`, "unexpected keyword
    argument". This is the wall stated in the checker's own words.
    """
    return render_project_dashboard((), (), NOW, tz=DISPLAY, goals=REGISTER)
