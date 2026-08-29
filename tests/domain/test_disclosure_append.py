"""Writing the application-scoped disclosure ledger (AD-17, AD-31, AD-38).

Spec: `_bmad-output/specs/spec-pm-ai/stories/2i-disclosure-ledger-append.md`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pm_ai.domain.disclosure import (
    DISCLOSURE_LEDGER_SCOPE,
    CommittedScopeLeak,
    DisclosureRecord,
    render_disclosure,
)
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.storage_tiers import is_append_only

AT = datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc)
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")
PERSONAL = DataScope(ScopeKind.PERSONAL)


def _record(**kw) -> DisclosureRecord:
    base = dict(
        at=AT,
        task_class="summarize",
        model="claude-opus-5",
        contributing_scopes=frozenset({PERSONAL}),
        input_tokens=1200,
        output_tokens=340,
        estimated_cost_usd=0.0186,
    )
    return DisclosureRecord(**{**base, **kw})


# ── The truncation hole this story found ────────────────────────────────────


def test_the_disclosure_ledger_is_append_only():
    """It was not, until this story. `write_artifact` replaces a file whole, so
    one call would have destroyed every prior entry in the audit trail."""
    assert is_append_only(ScopeKind.APPLICATION, "disclosure.md")


# ── Every field survives, because a total must be recomputable ──────────────


def test_every_field_is_rendered():
    line = render_disclosure(_record())
    for expected in (
        "at=2026-08-29T14:00:00+00:00",
        "task_class=summarize",
        "model=claude-opus-5",
        "input_tokens=1200",
        "output_tokens=340",
        "cost_usd=0.0186",
        "scopes=personal",
    ):
        assert expected in line, f"{expected} missing from {line!r}"


def test_a_zero_cost_call_renders_the_number():
    """Omitting it would make a month's total unrecomputable from the ledger."""
    assert "cost_usd=0.0" in render_disclosure(_record(estimated_cost_usd=0.0))


def test_the_cost_round_trips_at_full_precision():
    cost = 0.000123456789
    rendered = render_disclosure(_record(estimated_cost_usd=cost))
    value = rendered.split("cost_usd=", 1)[1].split(" ", 1)[0]
    assert float(value) == cost


def test_contributing_scopes_render_sorted():
    """A frozenset iterates arbitrarily; an audit line that differs between runs
    over identical data is not an audit line."""
    record = _record(contributing_scopes=frozenset({PROJECT, PERSONAL}))
    assert "scopes=personal,project:alpha" in render_disclosure(record)


def test_an_absent_destination_is_rendered_not_omitted():
    assert "destination=none" in render_disclosure(_record())


def test_a_destination_is_rendered_when_present():
    assert "destination=project:alpha" in render_disclosure(_record(destination=PROJECT))


# ── One line, and it cannot forge a second ──────────────────────────────────


def test_the_rendered_line_carries_no_newline():
    assert "\n" not in render_disclosure(_record())


def test_a_value_needing_quoting_gets_it():
    line = render_disclosure(_record(task_class="weekly brief"))
    assert 'task_class="weekly brief"' in line


# ── AD-38: one home, enforced by the guard rather than by care ──────────────


def test_the_application_scope_is_the_only_home():
    from pm_ai.domain.disclosure import assert_writable

    assert_writable(_record(), scope=DISCLOSURE_LEDGER_SCOPE)
    with pytest.raises(CommittedScopeLeak):
        assert_writable(_record(), scope=PROJECT)


# ── The scope round trip the ledger depends on ──────────────────────────────


@pytest.mark.parametrize(
    "scope",
    [
        DataScope(ScopeKind.APPLICATION),
        DataScope(ScopeKind.PERSONAL),
        DataScope(ScopeKind.PROJECT, project_id="alpha"),
        DataScope(ScopeKind.PEOPLE, person_id="u_42"),
    ],
    ids=["application", "personal", "project", "people"],
)
def test_a_scope_survives_a_round_trip_through_text(scope):
    assert DataScope.parse(str(scope)) == scope


def test_an_unknown_scope_kind_is_refused():
    with pytest.raises(ValueError):
        DataScope.parse("employer:acme")
