"""Person handles — one test per row of story 2n's I/O matrix, in its order.

Spec: `_bmad-output/specs/spec-pm-ai/stories/2n-people-are-written-as-handles.md`.

Rows exercised elsewhere, where the behaviour lives:

- `none:` as `assignee` or a `mentions` member, mention not a handle, not
  canonical on a line — `tests/domain/test_payload_fields.py`;
- `none:` in `own_handles`, own bot, same id other system —
  `tests/slice/test_r4_gate_fixes.py`;
- commit author — `tests/slice/test_vertical_slice.py`;
- organizer, attendees, attendee unusable — `tests/connectors/test_graph_calendar_mapping.py`;
- skill invoked, wrong self-action actor — `tests/domain/test_event_entries.py`;
- handle built elsewhere — `tests/architecture/test_handle_construction.py`;
- overlapping systems under `-O` (checker) — `tests/architecture/test_guards_survive_o.py`.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone

import pytest

from pm_ai.domain.events import CommitPayload, NormalizedEvent, ObservedEventType
from pm_ai.domain.handles import (
    UNRESOLVED_SENTINEL,
    HANDLE_SYSTEMS,
    NO_PERSON,
    InvalidPersonHandle,
    PersonHandle,
    parse_person_handle,
    person_handle,
    usable_handle,
)
from pm_ai.domain.identity import Actor, DataScope, ScopeKind, SourceRef
from pm_ai.domain.meetings import InvalidAttendeeCount, Meeting

GUID = "3F2504E0-4F89-11D3-9A0C-0305E82C3301"


# ── The matrix ───────────────────────────────────────────────────────────────


def test_a_graph_user_id_in_capitals_is_lowercased():
    handle = person_handle("graph", GUID)
    assert str(handle) == f"graph:{GUID.lower()}"


def test_a_graph_bot_id_is_a_graph_app_handle():
    assert str(person_handle("graph_app", GUID.lower())) == f"graph_app:{GUID.lower()}"


@pytest.mark.parametrize("system", ["graph", "graph_app"])
def test_a_graph_id_that_is_not_a_guid_is_refused_naming_it(system):
    with pytest.raises(InvalidPersonHandle) as caught:
        person_handle(system, "u_1")
    assert "u_1" in str(caught.value)


def test_an_address_is_stripped_and_lowercased_whole():
    assert str(person_handle("email", " Dana@Example.com ")) == "email:dana@example.com"


@pytest.mark.parametrize(
    "address",
    ["dana.example.com", "a@b@x", "a b@x", "a,b@x", "a:b@x", '"a"@x', "dané@x", "@x", "a@", "a\x07@x"],
    ids=["no @", "two @", "space", "comma", "colon", "quote", "non-ascii", "no local", "no domain", "control"],
)
def test_a_bad_address_is_refused_naming_it(address):
    with pytest.raises(InvalidPersonHandle) as caught:
        person_handle("email", address)
    assert repr(address) in str(caught.value) or address in str(caught.value)


def test_no_person_is_written_none_colon():
    assert str(NO_PERSON) == "none:"
    assert NO_PERSON.is_absent
    assert parse_person_handle("none:") == NO_PERSON


def test_none_naming_something_is_refused():
    with pytest.raises(InvalidPersonHandle) as caught:
        person_handle("none", "x")
    assert "x" in str(caught.value)
    with pytest.raises(InvalidPersonHandle):
        parse_person_handle("none:x")


@pytest.mark.parametrize(
    "value",
    ["Dana Smith", UNRESOLVED_SENTINEL, "dana@example.com", "gitlab:dana", "unresolved:gitlab:x", "meeting:m1"],
)
def test_a_refused_value_is_not_built_and_the_error_names_it(value):
    with pytest.raises(InvalidPersonHandle) as caught:
        parse_person_handle(value)
    assert value in str(caught.value)


def test_a_colon_inside_the_handle_is_refused():
    with pytest.raises(InvalidPersonHandle) as caught:
        person_handle("email", "a:b@x")
    assert "a:b@x" in str(caught.value)


def test_an_unknown_system_is_refused_by_the_producer():
    with pytest.raises(InvalidPersonHandle) as caught:
        person_handle("gitlab", "dana")
    assert "gitlab" in str(caught.value)


def test_text_not_in_canonical_form_is_refused_by_the_parser():
    """`email:Dana@X.com` is a corrupted line: the writer writes it lowercased."""
    with pytest.raises(InvalidPersonHandle) as caught:
        parse_person_handle("email:Dana@X.com")
    assert "email:Dana@X.com" in str(caught.value)
    assert parse_person_handle("email:dana@x.com") == person_handle("email", "Dana@X.com")


def test_the_handle_systems_are_disjoint_from_the_scopeless_references():
    from pm_ai.domain.identity import SCOPELESS_SYSTEMS

    assert HANDLE_SYSTEMS == {"graph", "graph_app", "email", "gitlab", "none"}
    assert not HANDLE_SYSTEMS & SCOPELESS_SYSTEMS


def test_an_overlapping_handle_system_refuses_the_import_under_optimization():
    """Matrix: overlapping systems — the import itself is refused under `-O`.

    Doctored from the other side, since `HANDLE_SYSTEMS` is defined in the
    module being imported: the scopeless set is widened to share `graph`, then
    `pm_ai.domain.handles` is loaded again (the package `__init__` has already
    imported it once, through `events`).
    """
    program = (
        "assert not __debug__, 'the -O flag did not take'\n"
        "import importlib\n"
        "import pm_ai.domain.identity as identity\n"
        "import pm_ai.domain.handles as handles\n"
        "identity.SCOPELESS_SYSTEMS = identity.SCOPELESS_SYSTEMS | {'graph'}\n"
        "from pm_ai.domain.invariants import InconsistentModel\n"
        "try:\n"
        "    importlib.reload(handles)\n"
        "except InconsistentModel as refusal:\n"
        "    print('REFUSED:', refusal)\n"
        "else:\n"
        "    raise SystemExit('GUARD INERT')\n"
    )
    result = subprocess.run([sys.executable, "-O", "-c", program], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REFUSED:" in result.stdout and "graph" in result.stdout


def test_meeting_cost_multiplies_by_the_count_not_the_list():
    """Matrix: 60 minutes, `attendee_count=3`, two handles, rate 100 → 300."""
    meeting = Meeting(
        meeting_id="mtg_1",
        title="t",
        start=datetime(2026, 10, 1, 9, tzinfo=timezone.utc),
        duration_minutes=60,
        attendees=(Actor("email:a@x.com"), Actor("email:b@x.com")),
        scope=DataScope(ScopeKind.PERSONAL),
        attendee_count=3,
    )
    assert meeting.man_hour_cost(100.0) == 300.0


def test_a_meeting_without_a_count_counts_its_list():
    meeting = Meeting(
        meeting_id="mtg_1",
        title="t",
        start=datetime(2026, 10, 1, 9, tzinfo=timezone.utc),
        duration_minutes=60,
        attendees=(Actor("email:a@x.com"),),
        scope=DataScope(ScopeKind.PERSONAL),
    )
    assert meeting.attendee_count == 1


def test_a_count_below_the_named_attendees_is_refused():
    with pytest.raises(InvalidAttendeeCount, match="attendee_count"):
        Meeting(
            meeting_id="mtg_1",
            title="t",
            start=datetime(2026, 10, 1, 9, tzinfo=timezone.utc),
            duration_minutes=60,
            attendees=(Actor("email:a@x.com"), Actor("email:b@x.com")),
            scope=DataScope(ScopeKind.PERSONAL),
            attendee_count=1,
        )


# ── The envelope and the connector helper ───────────────────────────────────


def _event(actor: object) -> NormalizedEvent:
    return NormalizedEvent(
        scope=DataScope(ScopeKind.PROJECT, "alpha"),
        type=ObservedEventType.COMMIT_PUSHED,
        source_ref=SourceRef.parse("gitlab:alpha:commit:9f2a1c"),
        actor=actor,  # type: ignore[arg-type]
        occurred_at=None,
        payload=CommitPayload(sha="9f2a1c", message="m"),
    )


@pytest.mark.parametrize(
    "actor", ["email:dana@example.com", Actor(UNRESOLVED_SENTINEL), None], ids=["str", "Actor", "None"]
)
def test_an_envelope_actor_that_is_not_a_handle_is_refused_at_runtime(actor):
    """A value from `row.get` is `Any`, so mypy cannot catch this."""
    with pytest.raises(TypeError, match="PersonHandle"):
        _event(actor)


def test_none_is_a_legal_envelope_actor():
    assert _event(NO_PERSON).actor is NO_PERSON


def test_a_handle_not_in_canonical_form_cannot_be_constructed():
    """The construction check re-runs the shape, so even a direct build — which
    the architecture scan refuses in `pm_ai/` — cannot hold a non-canonical value."""
    with pytest.raises(InvalidPersonHandle):
        PersonHandle("email", "Dana@X.com")
    with pytest.raises(InvalidPersonHandle):
        PersonHandle("meeting", "m1")


@pytest.mark.parametrize("raw", [None, "", "not an address", 42])
def test_an_unusable_provider_value_is_no_handle(raw):
    """For connectors: a refused address counts as no address, never a raise."""
    assert usable_handle("email", raw) is None  # type: ignore[arg-type]


def test_a_usable_provider_value_is_its_handle():
    assert usable_handle("email", "Dana@x.com") == person_handle("email", "dana@x.com")
