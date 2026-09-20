"""Each payload says which of its fields came from a provider.

Spec: `_bmad-output/specs/spec-pm-ai/stories/8c-payloads-declare-untrusted-text.md`.

The boundary still guesses — `pipelines.py` reads `getattr(event.payload,
"message", "")`, a field name only `CommitPayload` has, so every other payload
sanitizes the empty string. Story `8e` is what retires that line; this slice
supplies the declaration it will read, and these tests hold the three properties
that make it worth reading: the declaration is complete *per field*, the guard
that says so runs in the module body rather than only when a test calls it, and
it survives `python -O`, where `assert` statements do not exist.

The completeness tests are written over fields rather than classes on purpose. A
class-level check passes for a class that declares an empty tuple while carrying
provider prose, which is the original defect with a registry on top.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest

from pm_ai.domain import events as ev
from pm_ai.domain.events import (
    PAYLOAD_FOR,
    TRUSTED_TEXT,
    UNTRUSTED_TEXT,
    CommitPayload,
    DecisionPayload,
    DocumentPayload,
    MeetingHeldPayload,
    MessagePayload,
    MissingSanitizableDeclaration,
    ObservedEventType,
    PipelinePayload,
    ReviewPayload,
    WorkItemPayload,
)
from pm_ai.domain.invariants import InconsistentModel

PAYLOAD_CLASSES = list(dict.fromkeys(PAYLOAD_FOR.values()))
EVENTS_SOURCE = Path(ev.__file__)
GUARD = "_assert_payload_text_is_declared"


def _field_names(payload: type) -> set[str]:
    """The dataclass fields, which is the set the guard checks against.

    `get_type_hints` would answer a slightly different question — it includes
    `ClassVar` and anything else annotated on the class — and a test that asked
    it could demand a declaration the guard then refuses as a name the class does
    not have.
    """
    return {f.name for f in fields(payload)}


def _text_fields(payload: type) -> set[str]:
    """The `str` and `str | None` fields of a payload, resolved not read."""
    hints = ev.get_type_hints(payload)
    return {name for name in _field_names(payload) if ev._is_text(hints[name])}


def _instead_of_decision(
    payload: type,
    *,
    untrusted: tuple[str, ...] | None = None,
    trusted: dict[str, object] | None = None,
) -> dict[str, object]:
    """Doctored registries with `payload` where `DecisionPayload` was.

    `DecisionPayload`'s own entries leave with it. A class `PAYLOAD_FOR` no
    longer registers is an orphan, and leaving one behind would make every case
    below refuse for that reason instead of its own. `None` means *not recorded*,
    which is itself one of the faults under test.
    """

    def without(record: dict) -> dict:
        return {k: v for k, v in record.items() if k is not DecisionPayload}

    untrusted_record = without(UNTRUSTED_TEXT)
    trusted_record = without(TRUSTED_TEXT)
    if untrusted is not None:
        untrusted_record[payload] = untrusted
    if trusted is not None:
        trusted_record[payload] = trusted
    return {
        "PAYLOAD_FOR": {**PAYLOAD_FOR, ObservedEventType.DECISION: payload},
        "UNTRUSTED_TEXT": untrusted_record,
        "TRUSTED_TEXT": trusted_record,
    }


def _doctor(monkeypatch: pytest.MonkeyPatch, doctored: dict[str, object]) -> None:
    for attribute, value in doctored.items():
        monkeypatch.setattr(ev, attribute, value)


def _refusal(monkeypatch: pytest.MonkeyPatch, **doctored: object) -> str:
    """Doctor the module's registries, then demand a refusal naming the fault."""
    _doctor(monkeypatch, doctored)
    with pytest.raises(MissingSanitizableDeclaration) as refusal:
        ev._assert_payload_text_is_declared()
    return str(refusal.value)


# ── The declaration as it stands ─────────────────────────────────────────────


def test_the_model_as_declared_is_accepted():
    """The eight payloads, unmodified: import already ran this, so it holds."""
    ev._assert_payload_text_is_declared()


@pytest.mark.parametrize("payload", PAYLOAD_CLASSES, ids=lambda p: p.__name__)
def test_every_str_field_is_in_exactly_one_record(payload):
    """The property, per field. Silence about a field is what is forbidden."""
    untrusted = set(UNTRUSTED_TEXT[payload])
    trusted = set(TRUSTED_TEXT[payload])
    assert untrusted & trusted == set(), (
        f"{payload.__name__} answers twice for {sorted(untrusted & trusted)}."
    )
    assert untrusted | trusted == _text_fields(payload), (
        f"{payload.__name__} leaves "
        f"{sorted(_text_fields(payload) - untrusted - trusted)} unaccounted for."
    )


@pytest.mark.parametrize("payload", PAYLOAD_CLASSES, ids=lambda p: p.__name__)
def test_every_declared_name_is_a_field_the_class_has(payload):
    """A misspelled name sanitizes nothing and reads as though it did."""
    present = _field_names(payload)
    assert set(UNTRUSTED_TEXT[payload]) <= present
    assert set(TRUSTED_TEXT[payload]) <= present


@pytest.mark.parametrize("payload", PAYLOAD_CLASSES, ids=lambda p: p.__name__)
def test_every_trusted_field_carries_a_reason(payload):
    """A judgement, not an oversight — so the reason is required."""
    unreasoned = [
        n
        for n, why in TRUSTED_TEXT[payload].items()
        if not isinstance(why, str) or not why.strip()
    ]
    assert not unreasoned, f"{payload.__name__} trusts {unreasoned} silently."


def test_the_two_records_cover_the_same_classes():
    """Neither record may know about a payload the other does not."""
    assert UNTRUSTED_TEXT.keys() == TRUSTED_TEXT.keys()
    assert set(UNTRUSTED_TEXT) == set(PAYLOAD_FOR.values())


def test_the_excerpt_the_old_guess_never_reached_is_declared():
    """Every Teams message body arrives in `excerpt`, and `getattr` seeks
    `message`, which `MessagePayload` does not have."""
    assert "excerpt" in UNTRUSTED_TEXT[MessagePayload]


def test_the_classes_the_matrix_names_declare_their_prose():
    """A title and a decision statement are text a person wrote."""
    assert "title" in UNTRUSTED_TEXT[DocumentPayload]
    assert "statement" in UNTRUSTED_TEXT[DecisionPayload]
    assert "message" in UNTRUSTED_TEXT[CommitPayload]


def test_a_merge_target_is_untrusted_for_the_same_reason_a_branch_is():
    """`target_ref` is usually a branch name a person chose. Being an identifier
    a citation resolves against is no reason to trust it: AD-29 keeps the raw."""
    assert "target_ref" in UNTRUSTED_TEXT[ReviewPayload]
    assert "branch" in UNTRUSTED_TEXT[CommitPayload]
    assert "verdict" in TRUSTED_TEXT[ReviewPayload]


def test_pipeline_declares_no_untrusted_text_and_says_why_for_both_fields():
    """The class once called textless. It has two provider-supplied `str`
    fields, so declaring nothing is only legitimate with both recorded."""
    assert UNTRUSTED_TEXT[PipelinePayload] == ()
    assert set(TRUSTED_TEXT[PipelinePayload]) == {"pipeline_id", "status"}
    assert all(reason.strip() for reason in TRUSTED_TEXT[PipelinePayload].values())


def test_the_meeting_payload_declares_none_because_its_text_is_elsewhere():
    """An id and two counts. The subject and body are not in this payload."""
    assert UNTRUSTED_TEXT[MeetingHeldPayload] == ()
    assert set(TRUSTED_TEXT[MeetingHeldPayload]) == {"meeting_id"}


def test_a_class_bound_to_two_event_types_has_one_declaration():
    """Keying by type would permit two declarations of one class that disagree;
    keying by class makes the two types reach the same object."""
    submitted = PAYLOAD_FOR[ObservedEventType.REVIEW_SUBMITTED]
    merged = PAYLOAD_FOR[ObservedEventType.MERGE_COMPLETED]
    assert submitted is merged is ReviewPayload
    assert UNTRUSTED_TEXT[submitted] is UNTRUSTED_TEXT[merged]
    assert TRUSTED_TEXT[submitted] is TRUSTED_TEXT[merged]
    assert all(isinstance(key, type) for key in UNTRUSTED_TEXT)
    assert all(isinstance(key, type) for key in TRUSTED_TEXT)


# ── What the guard refuses ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Undeclared:
    """A payload someone added to the registry and nothing else."""

    body: str


def test_a_payload_with_no_declaration_is_refused(monkeypatch):
    message = _refusal(monkeypatch, **_instead_of_decision(_Undeclared))
    assert "_Undeclared" in message


def test_a_payload_missing_from_the_trusted_record_is_refused(monkeypatch):
    message = _refusal(
        monkeypatch, **_instead_of_decision(_Undeclared, untrusted=("body",))
    )
    assert "_Undeclared" in message and "TRUSTED_TEXT" in message


def test_a_payload_missing_from_the_untrusted_record_is_refused(monkeypatch):
    """The mirror: recorded as trusted, never declared. An entry in one record
    is not a declaration, and the missing one is the record that matters."""
    message = _refusal(
        monkeypatch,
        **_instead_of_decision(_Undeclared, trusted={"body": "claimed trusted"}),
    )
    assert "_Undeclared" in message and "UNTRUSTED_TEXT" in message


def test_a_misspelled_field_name_is_refused_naming_class_and_field(monkeypatch):
    message = _refusal(
        monkeypatch,
        UNTRUSTED_TEXT={**UNTRUSTED_TEXT, MessagePayload: ("exerpt",)},
    )
    assert "MessagePayload" in message and "exerpt" in message


def test_a_declared_field_that_is_not_text_is_refused(monkeypatch):
    """`comment_count: int` cannot be sanitized, so declaring it is a mistake
    about what the boundary can do."""
    message = _refusal(
        monkeypatch,
        UNTRUSTED_TEXT={**UNTRUSTED_TEXT, ReviewPayload: ("comment_count",)},
    )
    assert "ReviewPayload" in message and "comment_count" in message


@dataclass(frozen=True, slots=True)
class _Timestamped:
    happened_at: datetime


def test_a_declared_datetime_field_is_refused(monkeypatch):
    """The second non-text shape, because `datetime` is what a payload most
    plausibly carries beside its prose."""
    message = _refusal(
        monkeypatch,
        **_instead_of_decision(_Timestamped, untrusted=("happened_at",), trusted={}),
    )
    assert "_Timestamped" in message and "happened_at" in message


def test_a_class_carrying_prose_and_declaring_none_is_refused(monkeypatch):
    """The failure a class-level check cannot see: an empty tuple over a class
    whose text nothing records."""
    message = _refusal(
        monkeypatch,
        UNTRUSTED_TEXT={**UNTRUSTED_TEXT, DecisionPayload: ()},
    )
    assert "DecisionPayload" in message and "statement" in message


def test_the_unaccounted_refusal_names_every_field_it_found(monkeypatch):
    """This is the check a newly added payload trips, so a partial list is a
    second round trip for whoever added it."""
    message = _refusal(
        monkeypatch,
        UNTRUSTED_TEXT={**UNTRUSTED_TEXT, DecisionPayload: ()},
    )
    assert "rationale" in message and "statement" in message


def test_a_trusted_field_with_a_blank_reason_is_refused(monkeypatch):
    message = _refusal(
        monkeypatch,
        TRUSTED_TEXT={
            **TRUSTED_TEXT,
            PipelinePayload: {"pipeline_id": "", "status": "  "},
        },
    )
    assert "PipelinePayload" in message and "pipeline_id" in message


def test_a_trusted_reason_that_is_not_a_string_is_refused(monkeypatch):
    """`None` is the shape a half-written entry takes, and it must refuse rather
    than raise `AttributeError` from inside the guard."""
    message = _refusal(
        monkeypatch,
        TRUSTED_TEXT={
            **TRUSTED_TEXT,
            PipelinePayload: {"pipeline_id": None, "status": 7},
        },
    )
    assert "PipelinePayload" in message and "pipeline_id" in message


def test_a_field_recorded_as_both_untrusted_and_trusted_is_refused(monkeypatch):
    message = _refusal(
        monkeypatch,
        TRUSTED_TEXT={
            **TRUSTED_TEXT,
            MessagePayload: {"excerpt": "claimed by both records"},
        },
    )
    assert "MessagePayload" in message and "excerpt" in message


def test_an_unrecorded_str_field_on_the_textless_class_is_refused(monkeypatch):
    """Dropping one of `PipelinePayload`'s two records must not pass."""
    message = _refusal(
        monkeypatch,
        TRUSTED_TEXT={
            **TRUSTED_TEXT,
            PipelinePayload: {
                "pipeline_id": TRUSTED_TEXT[PipelinePayload]["pipeline_id"]
            },
        },
    )
    assert "PipelinePayload" in message and "status" in message


@dataclass(frozen=True, slots=True)
class _Orphan:
    """A payload the registries still describe and `PAYLOAD_FOR` does not."""

    body: str


def test_a_declaration_for_a_class_no_event_type_registers_is_refused(monkeypatch):
    """A renamed or dropped payload leaves a declaration nothing checks, and it
    reads as coverage of a class that no longer exists."""
    message = _refusal(
        monkeypatch,
        UNTRUSTED_TEXT={**UNTRUSTED_TEXT, _Orphan: ("body",)},
        TRUSTED_TEXT={**TRUSTED_TEXT, _Orphan: {}},
    )
    assert "_Orphan" in message


class _NotADataclass:
    """Registered as a payload, with no fields to check a declaration against."""

    body = "not a field"


def test_a_non_dataclass_payload_is_refused_with_the_typed_error(monkeypatch):
    """It used to die on `fields()` with a bare `TypeError`, which says nothing
    about declarations and does not read as a refusal."""
    message = _refusal(
        monkeypatch,
        **_instead_of_decision(_NotADataclass, untrusted=(), trusted={}),
    )
    assert "_NotADataclass" in message and "dataclass" in message


@dataclass(frozen=True, slots=True)
class _Unresolvable:
    """Its annotation is a string that names nothing — the only shape possible
    under `from __future__ import annotations`."""

    note: AnAnnotationNobodyDefined  # noqa: F821


def test_an_annotation_that_does_not_resolve_is_refused(monkeypatch):
    """`get_type_hints` raises `NameError`; the guard must answer in its own
    vocabulary and name the class."""
    message = _refusal(
        monkeypatch,
        **_instead_of_decision(_Unresolvable, untrusted=(), trusted={}),
    )
    assert "_Unresolvable" in message and "resolve" in message


# ── What the guard accepts, including what it cannot reach ───────────────────


@dataclass(frozen=True, slots=True)
class _OldSpelling:
    """A future payload written before `X | None` was the house style."""

    note: Optional[str] = None


def test_a_field_spelled_optional_str_is_accepted(monkeypatch):
    """`from __future__ import annotations` makes every annotation a string, so
    a check that matched `"str | None"` as text would refuse this."""
    _doctor(
        monkeypatch,
        _instead_of_decision(_OldSpelling, untrusted=("note",), trusted={}),
    )
    ev._assert_payload_text_is_declared()


def test_provider_text_in_a_nested_type_is_a_known_limit_not_a_refusal():
    """`WorkItemPayload.assignee: Actor | None` carries a provider display name
    the `str`-only rule cannot require. Recorded rather than silently widened:
    this slice makes sanitization run, it does not widen what it reaches."""
    hints = ev.get_type_hints(WorkItemPayload)
    assert not ev._is_text(hints["assignee"])
    assert "assignee" not in UNTRUSTED_TEXT[WorkItemPayload]
    assert "assignee" not in TRUSTED_TEXT[WorkItemPayload]
    ev._assert_payload_text_is_declared()


@dataclass(frozen=True, slots=True)
class _Labelled:
    """The likeliest next field: a work item's labels, as a container of text."""

    work_item_id: str
    labels: list[str]


def test_a_container_of_text_is_outside_the_rule(monkeypatch):
    """The second half of the same limit. `list[str]` is neither required nor
    refused — a fact worth pinning, because it is the shape that will force the
    widening decision."""
    assert not ev._is_text(ev.get_type_hints(_Labelled)["labels"])
    _doctor(
        monkeypatch,
        _instead_of_decision(
            _Labelled,
            untrusted=(),
            trusted={"work_item_id": "the provider's key"},
        ),
    )
    ev._assert_payload_text_is_declared()


# ── The guard runs in the module body, and under -O ──────────────────────────


def _run_optimized(program: str) -> subprocess.CompletedProcess[str]:
    """Run `program` under `-O`, where every `assert` statement is stripped."""
    return subprocess.run(
        [sys.executable, "-O", "-c", textwrap.dedent(program)],
        capture_output=True,
        text=True,
    )


def test_the_module_body_itself_refuses_an_undeclared_payload_under_optimization():
    """The acceptance criterion is stated at the import boundary, so this is the
    test that observes the boundary rather than the function.

    Every other test here calls the checker, so all of them pass with the
    module-level call deleted. This one splices an undeclared payload into the
    source immediately before that call and executes the module body: deleting,
    renaming or relocating the call makes it fail, which is the property.
    """
    result = _run_optimized(
        f"""
        import pathlib
        import sys
        import types

        source = pathlib.Path({str(EVENTS_SOURCE)!r}).read_text(encoding="utf-8")
        marker = "\\n{GUARD}()\\n"
        if source.count(marker) != 1:
            raise SystemExit(
                "GUARD MOVED: no single top-level `{GUARD}()` call to splice before"
            )

        splice = (
            "@dataclass(frozen=True, slots=True)\\n"
            "class _Injected:\\n"
            "    body: str\\n"
            "\\n"
            "PAYLOAD_FOR[ObservedEventType.DECISION] = _Injected\\n"
        )
        doctored = source.replace(marker, "\\n" + splice + marker)

        spliced = types.ModuleType("spliced_events")
        sys.modules["spliced_events"] = spliced
        try:
            exec(compile(doctored, "<spliced events.py>", "exec"), spliced.__dict__)
        except Exception as refusal:
            print("REFUSED:", type(refusal).__name__, refusal)
        else:
            raise SystemExit(
                "GUARD INERT: the module body accepted an undeclared payload"
            )
        """
    )
    assert result.returncode == 0, (
        "importing the module did not refuse an undeclared payload, under -O."
        f"\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "REFUSED: MissingSanitizableDeclaration" in result.stdout
    assert "_Injected" in result.stdout


def test_the_guard_is_called_at_module_level():
    """The cheap complement, by AST: the call is a top-level statement.

    The spliced-execution test above would also fail if the call moved, but it
    would fail by not finding it; this says plainly what is missing.
    """
    tree = ast.parse(EVENTS_SOURCE.read_text(encoding="utf-8"))
    calls = [
        node.value.func.id
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    ]
    assert GUARD in calls, (
        f"`{GUARD}()` is not a top-level call in events.py, so nothing checks "
        f"the declarations when the module loads."
    )


def test_the_guard_still_refuses_a_doctored_registry_under_optimization():
    """The same shape as story 1l's eleven cases: doctor the model into the state
    the guard exists to reject, call the checker, demand the refusal."""
    result = _run_optimized(
        """
        import dataclasses
        import pm_ai.domain.events as m

        assert not __debug__, 'the -O flag did not take, so this proves nothing'

        @dataclasses.dataclass(frozen=True, slots=True)
        class Undeclared:
            body: str

        m.PAYLOAD_FOR = {**m.PAYLOAD_FOR, m.ObservedEventType.DECISION: Undeclared}

        from pm_ai.domain.invariants import InconsistentModel
        try:
            m._assert_payload_text_is_declared()
        except InconsistentModel as refusal:
            print('REFUSED:', refusal)
        else:
            raise SystemExit('GUARD INERT: the doctored model was accepted')
        """
    )
    assert result.returncode == 0, (
        "the guard did not refuse a payload it exists to reject, under -O."
        f"\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "REFUSED:" in result.stdout


def test_a_coherent_model_still_imports_silently_under_optimization():
    """Only wrong models fail; the declared eight must load without a word."""
    result = _run_optimized(
        """
        import pm_ai.domain.events
        assert not __debug__
        print('OK')
        """
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert result.stdout.strip() == "OK"


def test_the_refusal_is_an_inconsistent_model():
    """1l's base, so every import-time refusal in the domain reads alike."""
    assert issubclass(MissingSanitizableDeclaration, InconsistentModel)
