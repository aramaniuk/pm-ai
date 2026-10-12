"""People are read as people — story 2o's I/O matrix above the speaker rows, in order.

Spec: `_bmad-output/specs/spec-pm-ai/stories/2o-people-are-read-as-people.md`.

Rows exercised elsewhere, where the behaviour lives:

- "listed as unusable if enabled", the alias table filled in memory through
  the composition root, and the acceptance criteria on `read_pm_identity` —
  `tests/slice/test_read_pm_identity.py`;
- the speaker rows — `tests/slice/test_transcript_slice.py`;
- old setting — `tests/core/test_config.py`, `tests/architecture/test_doctor.py`.

Every alias registered here is undone by `tests/conftest.py`'s autouse fixture.
"""

from __future__ import annotations

import pytest

from pm_ai.domain import handles
from pm_ai.domain.connector_rows import USER_ID_KEY
from pm_ai.domain.handles import (
    EMAIL,
    GRAPH,
    GRAPH_APP,
    NO_PERSON,
    AliasRefused,
    parse_person_handle,
    person_for,
    person_handle,
    pm_identity,
    register_alias,
    usable_user_id,
)
from pm_ai.domain.identity import Actor

U1 = "0D9E3C4F-1A2B-4C3D-8E9F-A0B1C2D3E4F5"
U2 = "11111111-2222-4333-8444-555555555555"
GRAPH_U1 = person_handle(GRAPH, U1)
GRAPH_U2 = person_handle(GRAPH, U2)
P = Actor("person_p", "P")
Q = Actor("person_q", "Q")
_ABSENT = object()


def _graph_row(user_id: object = U1, *, enabled: bool = True, **extra: object) -> dict:
    row: dict[str, object] = {"instance": "graph:work", "system": "graph", "enabled": enabled}
    if user_id is not _ABSENT:
        row[USER_ID_KEY] = user_id
    row.update(extra)
    return row


# ── person_for ───────────────────────────────────────────────────────────────


def test_a_known_person_is_returned():
    """Matrix: known person — alias `graph:u_1` → P."""
    register_alias(GRAPH_U1, P)
    assert person_for(GRAPH_U1) == P


def test_a_handle_with_no_alias_is_no_known_person():
    """Matrix: no alias — `None`; the reader shows the handle as written."""
    dana = person_handle(EMAIL, "dana@example.com")
    assert person_for(dana) is None
    assert str(dana) == "email:dana@example.com"


def test_an_alias_added_later_applies_to_an_unchanged_line():
    """Matrix: alias added later — nothing rewrites the line, so it reads as P."""
    written = str(GRAPH_U1)  # the text a line carries, written before any alias
    assert person_for(parse_person_handle(written)) is None

    register_alias(GRAPH_U1, P)

    assert written == str(GRAPH_U1), "the line's text is what it was"
    assert person_for(parse_person_handle(written)) == P


def test_a_bot_and_absence_in_the_table_are_never_a_person():
    """Matrix: bot and absence in the table — written directly, `None` for both.

    `person_for` does not rely on `register_alias` having refused them.
    """
    bot = person_handle(GRAPH_APP, U2)
    handles.ALIASES[bot] = P
    handles.ALIASES[NO_PERSON] = P
    assert person_for(bot) is None
    assert person_for(NO_PERSON) is None


def test_person_for_takes_only_a_handle():
    """A raw string is parsed with `parse_person_handle` first, never mapped as text."""
    register_alias(GRAPH_U1, P)
    with pytest.raises(TypeError) as refused:
        person_for(str(GRAPH_U1))  # type: ignore[arg-type]
    assert "parse_person_handle" in str(refused.value)


# ── register_alias ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("handle", "system"),
    [(person_handle(GRAPH_APP, U2), "graph_app"), (NO_PERSON, "none")],
    ids=["graph_app", "none"],
)
def test_registering_a_bot_or_absence_is_refused_naming_the_system(handle, system):
    """Matrix: registering bot or absence — nothing stored, refused naming the system."""
    with pytest.raises(AliasRefused) as refused:
        register_alias(handle, P)
    assert f"`{system}`" in str(refused.value)
    assert handle not in handles.ALIASES


def test_remapping_a_handle_is_refused_naming_both_and_keeps_the_first():
    """Matrix: remapping — P kept, refused naming P and Q."""
    register_alias(GRAPH_U1, P)
    with pytest.raises(AliasRefused) as refused:
        register_alias(GRAPH_U1, Q)
    message = str(refused.value)
    assert P.actor_id in message and Q.actor_id in message
    assert person_for(GRAPH_U1) == P


def test_registering_the_same_mapping_again_changes_nothing():
    register_alias(GRAPH_U1, P)
    register_alias(GRAPH_U1, P)
    assert person_for(GRAPH_U1) == P


def test_the_table_is_keyed_by_handle():
    """One person, two handles of two systems: both map, each by its handle."""
    dana = person_handle(EMAIL, "Dana@Example.com")
    register_alias(GRAPH_U1, P)
    register_alias(dana, P)
    assert set(handles.ALIASES) >= {GRAPH_U1, dana}
    assert person_for(person_handle(EMAIL, "dana@example.com")) == P


# ── pm_identity ──────────────────────────────────────────────────────────────


def test_one_graph_row_is_its_users_handle_stripped_and_lowercased():
    """Matrix: one Graph row — `user_id: " U1 "` gives `{graph:u1}`."""
    assert pm_identity([_graph_row(f" {U1} ")], aliased=frozenset()) == {GRAPH_U1}
    assert str(GRAPH_U1) == f"graph:{U1.lower()}"


def test_a_disabled_row_still_names_the_pm():
    """Matrix: disabled row — disabling a row does not unmark past lines."""
    assert pm_identity([_graph_row(U1, enabled=False)], aliased=frozenset()) == {GRAPH_U1}


def test_two_tenants_give_both_handles():
    """Matrix: two tenants — two Graph rows, two ids."""
    rows = [_graph_row(U1), _graph_row(U2, instance="graph:other")]
    assert pm_identity(rows, aliased=frozenset()) == {GRAPH_U1, GRAPH_U2}


@pytest.mark.parametrize(
    "user_id",
    [_ABSENT, "", "  ", 42, None, "not-a-graph-user-id"],
    ids=["absent", "empty", "whitespace", "a number", "null", "refused by the producer"],
)
def test_a_row_with_no_usable_user_id_adds_nothing(user_id):
    """Matrix: no usable user id — the row adds nothing, and nothing raises."""
    for enabled in (True, False):
        assert pm_identity([_graph_row(user_id, enabled=enabled)], aliased=frozenset()) == set()


@pytest.mark.parametrize("system", ["Graph", "graph ", "gitlab", None, 1])
def test_a_row_whose_system_is_not_exactly_graph_adds_nothing(system):
    """Matrix: system not exactly `graph`, with a `user_id`."""
    assert pm_identity([_graph_row(U1, system=system)], aliased=frozenset()) == set()


@pytest.mark.parametrize(
    "rows",
    [[], [{"instance": "gitlab:alpha", "system": "gitlab", "enabled": True}]],
    ids=["none", "none Graph"],
)
def test_no_graph_row_is_the_empty_set(rows):
    """Matrix: no Graph row."""
    assert pm_identity(rows, aliased=frozenset()) == frozenset()


def test_aliased_handles_join_the_graph_rows():
    """What the slice that saves the table will pass; today the app passes `{}`."""
    dana = person_handle(EMAIL, "dana@example.com")
    assert pm_identity([_graph_row(U1)], aliased=frozenset({dana})) == {GRAPH_U1, dana}


def test_the_alias_table_in_memory_is_not_consulted():
    """Matrix: alias table filled in memory — the PM's handle and another mapped
    to one person; `pm_identity` reads only its arguments."""
    dana = person_handle(EMAIL, "dana@example.com")
    register_alias(GRAPH_U1, P)
    register_alias(dana, P)
    assert pm_identity([_graph_row(U1)], aliased=frozenset()) == {GRAPH_U1}


def test_one_row_rather_than_the_rows_is_refused():
    with pytest.raises(TypeError):
        pm_identity(_graph_row(U1), aliased=frozenset())  # type: ignore[arg-type]


def test_aliased_must_hold_handles():
    with pytest.raises(TypeError):
        pm_identity([], aliased=frozenset({str(GRAPH_U1)}))  # type: ignore[arg-type]


# ── One usable-user-id rule, one key ─────────────────────────────────────────


def test_the_graph_packages_user_id_key_is_domains_constant():
    from pm_ai.connectors import graph

    assert graph.USER_ID_KEY is USER_ID_KEY


def test_the_rule_strips_and_keeps_the_providers_case():
    assert usable_user_id({USER_ID_KEY: f"  {U1}\t"}) == U1


def test_recorded_user_id_delegates_to_domains_rule():
    """A value only `domain`'s rule refuses — text, not blank, but not a Graph
    user id the producer accepts — is refused by both."""
    from pm_ai.core.connector_enrolment import recorded_user_id

    only_the_producer_refuses = {USER_ID_KEY: "not-a-graph-user-id"}
    assert usable_user_id(only_the_producer_refuses) is None
    assert recorded_user_id(only_the_producer_refuses) is None
    for row in ({USER_ID_KEY: f" {U1} "}, {}, {USER_ID_KEY: "  "}, {USER_ID_KEY: 7}):
        assert recorded_user_id(row) == usable_user_id(row)


@pytest.mark.parametrize(
    "handle", [person_handle(GRAPH_APP, U2), NO_PERSON], ids=["graph_app", "none"]
)
def test_an_aliased_bot_or_absence_is_refused_naming_it(handle):
    with pytest.raises(AliasRefused) as refused:
        pm_identity([], aliased=frozenset({handle}))
    assert str(handle) in str(refused.value)
