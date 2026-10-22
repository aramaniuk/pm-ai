"""Registered proposal types and the pure change rules over them (AD-13, story 13a).

A feature never builds an approval flow: it registers a `ProposalType` — its
payload schema, the fields a person may edit, its review window and what
approval means — and every surface reads the same registration. A type sets
only its `ttl`; expiry belongs to the scheduler, so no type has an `expire`.

The three types pm-ai stages today are declared here (`ACTION_ITEM_TYPE`,
`IMPLICIT_COMMITMENT_TYPE`, `EXPLICIT_COMMAND_TYPE`); the composition root
registers them, and any type a later feature adds, at start (AD-30).

The in-memory book at the bottom (`sample_staged`, `approve`, `expire`,
`execute`) applies `pm_ai.domain.proposals.check_change` — the same function
the store's claimed steps call — so the architecture tests check the rules
the store enforces. It is never used by a surface.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from pm_ai.domain.identity import SourceRef, TargetRef
from pm_ai.domain.lifecycle import ProposalState
from pm_ai.domain.meeting_summaries import ACTION_ITEM, DUE_LIMIT, WHAT_LIMIT
from pm_ai.domain.proposals import (
    IllegalTransition,
    PastWindow,
    Proposal,
    TerminalState,
    TypeRule,
    UNKNOWN_TYPE,
    VersionConflict,
    check_change,
    fold_typed,
    repeat_key,
    replacement_id,
    replacement_number,
    root_of,
)

__all__ = [
    "ACTION_ITEM_TYPE",
    "Approval",
    "ApprovalKind",
    "DuplicateType",
    "EXPLICIT_COMMAND_TYPE",
    "EditMisused",
    "EditRefused",
    "FieldSpec",
    "IMPLICIT_COMMITMENT_TYPE",
    "IllegalTransition",
    "OWNER_LIMIT",
    "PROPOSAL_TTL",
    "PastWindow",
    "ProposalType",
    "ProposalTypes",
    "REVIEW_WINDOW_DAYS",
    "SUMMARY_LIMIT",
    "TerminalState",
    "VersionConflict",
    "approve",
    "default_types",
    "edited_values",
    "execute",
    "expire",
    "fold_typed",
    "registered_types",
    "repeat_key",
    "replacement_id",
    "replacement_number",
    "root_of",
    "sample_approved",
    "sample_staged",
]

REVIEW_WINDOW_DAYS = 14
PROPOSAL_TTL = timedelta(days=REVIEW_WINDOW_DAYS)
"""The review window of every type that waits for review today (Andrei, 2026-10-22)."""

OWNER_LIMIT = 80
"""A typed owner's most characters: `7b` sets none for a speaker label (13a)."""

SUMMARY_LIMIT = 80
"""A replacement's `summary`: the first 80 characters of its `what`, as `7b` makes it."""

EXPLICIT_COMMAND = "explicit_command"
IMPLICIT_COMMITMENT = "implicit_commitment"


class DuplicateType(ValueError):
    """A second registration of one type name, refused at start (13a)."""


class ApprovalKind(Enum):
    """What approving a proposal of a type means (AD-13 as amended by 13a)."""

    AGREED = "agreed"
    """The PM agreed: the whole effect for now; the proposal stays `approved`."""
    UNAVAILABLE = "unavailable"
    """Approval cannot be given yet; `Approval.reason` says why."""
    EXECUTOR = "executor"
    """Approval runs an executor (13b fills it; no registered type uses it in 13a)."""


@dataclass(frozen=True, slots=True)
class Approval:
    kind: ApprovalKind
    reason: str | None = None
    executor: Callable[[Proposal], object] | None = None


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One payload field: its name, whether it is required, its limit, whether a person may edit it."""

    name: str
    required: bool = False
    limit: int | None = None
    editable: bool = False


@dataclass(frozen=True, slots=True)
class ProposalType:
    """A registered proposal type (AD-13). Sets a `ttl`, never its own expiry."""

    name: str
    label: str
    fields: tuple[FieldSpec, ...]
    ttl: timedelta
    approval: Approval
    card: Callable[[Mapping[str, str | None]], tuple[str, ...]]
    """The field values (`values`) rendered to the card's field lines."""

    @property
    def rule(self) -> TypeRule:
        return TypeRule(ttl=self.ttl, has_executor=self.approval.kind is ApprovalKind.EXECUTOR)

    @property
    def editable(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.fields if spec.editable)

    def spec(self, name: str) -> FieldSpec | None:
        return next((spec for spec in self.fields if spec.name == name), None)

    def values(self, payload: object) -> dict[str, str | None]:
        """The payload's field values as text: the cleaned text for a sanitised field."""
        found = payload if isinstance(payload, Mapping) else {}
        values: dict[str, str | None] = {}
        for spec in self.fields:
            raw = found.get(spec.name)
            if isinstance(raw, Mapping):
                raw = raw.get("for_model")
            values[spec.name] = raw if isinstance(raw, str) and raw.strip() else None
        return values


class ProposalTypes:
    """The registry the composition root builds at start (AD-30)."""

    def __init__(self) -> None:
        self._types: dict[str, ProposalType] = {}

    def register(self, proposal_type: ProposalType) -> None:
        if proposal_type.name in self._types:
            raise DuplicateType(
                f"the proposal type {proposal_type.name!r} is registered twice; one "
                f"name has one registration (AD-13)."
            )
        self._types[proposal_type.name] = proposal_type

    def get(self, name: str) -> ProposalType | None:
        return self._types.get(name)

    def rule(self, name: str) -> TypeRule:
        found = self._types.get(name)
        return UNKNOWN_TYPE if found is None else found.rule

    def rules(self) -> dict[str, TypeRule]:
        return {name: each.rule for name, each in self._types.items()}

    def items(self) -> Mapping[str, ProposalType]:
        return dict(self._types)

    def __contains__(self, name: object) -> bool:
        return name in self._types


# ── Text a person types ──────────────────────────────────────────────────────


class EditMisused(ValueError):
    """An edit the command line got wrong: a usage error (exit 2)."""


class EditRefused(ValueError):
    """An edit this type does not allow at all: a refusal (exit 3)."""


def edited_values(
    proposal_type: ProposalType | None,
    current: Mapping[str, str | None],
    changes: Mapping[str, str],
) -> dict[str, str | None]:
    """The field values after `changes`, checked against the type's schema (13a).

    `""` clears an optional field. Refused (`EditRefused`) for a type with no
    editable field; misused (`EditMisused`) for no change given, a field the
    type does not let a person edit, a value over its limit, or a required
    field left empty once folded.
    """
    if proposal_type is None:
        raise EditRefused("pm-ai does not know this type, so it has no field a person may edit.")
    editable = proposal_type.editable
    if not editable:
        raise EditRefused(
            f"a {proposal_type.label.lower()} has no field a person may edit; reject it "
            f"instead."
        )
    spelled = ", ".join(f"--{name}" for name in editable)
    if not changes:
        raise EditMisused(f"`proposal edit` needs at least one of {spelled}.")
    for name in changes:
        if name not in editable:
            raise EditMisused(
                f"`--{name}` cannot be edited on a {proposal_type.label.lower()}; the "
                f"fields a person may edit are {spelled}."
            )
    values = dict(current)
    for name, typed in changes.items():
        spec = proposal_type.spec(name)
        if spec is None:
            raise EditMisused(f"`--{name}` is not a field of a {proposal_type.label.lower()}.")
        folded = fold_typed(typed)
        if not folded:
            if spec.required:
                raise EditMisused(f"`{name}` is required and cannot be empty.")
            values[name] = None
            continue
        if spec.limit is not None and len(folded) > spec.limit:
            raise EditMisused(
                f"`--{name}` is {len(folded)} characters; the limit is {spec.limit}."
            )
        values[name] = folded
    return values


# ── The three types pm-ai stages today ───────────────────────────────────────


def _action_item_card(values: Mapping[str, str | None]) -> tuple[str, ...]:
    owner = values.get("owner")
    line = f"{owner or 'unassigned'}: {values.get('what') or '(no text recorded)'}"
    if values.get("due"):
        line += f" ({values['due']})"
    return (line,)


def _promise_card(values: Mapping[str, str | None]) -> tuple[str, ...]:
    line = f"promised: {values.get('what') or '(no text recorded)'}"
    if values.get("when"):
        line += f", by {values['when']}"
    return (line,)


def _command_card(values: Mapping[str, str | None]) -> tuple[str, ...]:
    return (
        f"command: {values.get('verb') or '(no verb)'} on {values.get('target') or '(no target)'}",
        f"saying: {values.get('remainder') or '(nothing more)'}",
    )


ACTION_ITEM_TYPE = ProposalType(
    name=ACTION_ITEM,
    label="Action item",
    fields=(
        FieldSpec("what", required=True, limit=WHAT_LIMIT, editable=True),
        FieldSpec("owner", limit=OWNER_LIMIT, editable=True),
        FieldSpec("due", limit=DUE_LIMIT, editable=True),
    ),
    ttl=PROPOSAL_TTL,
    approval=Approval(ApprovalKind.AGREED),
    card=_action_item_card,
)

IMPLICIT_COMMITMENT_TYPE = ProposalType(
    name=IMPLICIT_COMMITMENT,
    label="Spoken promise",
    fields=(FieldSpec("what", required=True), FieldSpec("when")),
    ttl=PROPOSAL_TTL,
    approval=Approval(ApprovalKind.AGREED),
    card=_promise_card,
)

EXPLICIT_COMMAND_TYPE = ProposalType(
    name=EXPLICIT_COMMAND,
    label="Spoken command",
    fields=(FieldSpec("verb", required=True), FieldSpec("target"), FieldSpec("remainder")),
    ttl=PROPOSAL_TTL,
    approval=Approval(
        ApprovalKind.UNAVAILABLE,
        reason="pm-ai cannot carry out a staged command yet; reject it, or make the change yourself",
    ),
    card=_command_card,
)

TODAYS_TYPES = (ACTION_ITEM_TYPE, IMPLICIT_COMMITMENT_TYPE, EXPLICIT_COMMAND_TYPE)


def default_types() -> ProposalTypes:
    """A registry holding the three types pm-ai stages today."""
    registry = ProposalTypes()
    for each in TODAYS_TYPES:
        registry.register(each)
    return registry


def registered_types() -> Mapping[str, ProposalType]:
    """The types pm-ai registers at start, by name (the architecture tests read this)."""
    return default_types().items()


# ── The in-memory book the architecture tests drive ─────────────────────────

_SAMPLE_TYPE = ProposalType(
    name="sample_change",
    label="Sample change",
    fields=(FieldSpec("comment", required=True),),
    ttl=PROPOSAL_TTL,
    approval=Approval(ApprovalKind.EXECUTOR, executor=lambda proposal: proposal.proposal_id),
    card=lambda values: (values.get("comment") or "",),
)

_BOOK: dict[str, Proposal] = {}
_TYPES: dict[str, ProposalType] = {_SAMPLE_TYPE.name: _SAMPLE_TYPE}
_SEQUENCE = itertools.count(1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rule_of(proposal: Proposal) -> TypeRule:
    found = _TYPES.get(proposal.type)
    return UNKNOWN_TYPE if found is None else found.rule


def _change(proposal_id: str, to: ProposalState, expected_version: int) -> Proposal:
    current = _BOOK.get(proposal_id)
    if current is None:
        raise KeyError(f"no proposal is staged under {proposal_id!r}")
    moved = current.transition(
        to, expected_version=expected_version, rule=_rule_of(current), at=_now()
    )
    _BOOK[proposal_id] = moved
    return moved


def sample_staged() -> Proposal:
    """A fresh `staged` proposal of a sample type that has an executor."""
    proposal_id = f"prp_sample_{next(_SEQUENCE)}"
    proposal = Proposal(
        proposal_id=proposal_id,
        type=_SAMPLE_TYPE.name,
        summary="post a comment",
        payload={"comment": "Approved"},
        target=TargetRef.parse("gitlab:alpha:issue:102"),
        cites=SourceRef.parse("meeting:mtg_sample"),
        created_at=_now(),
        ttl=_SAMPLE_TYPE.ttl,
        staged_at=_now(),
    )
    _BOOK[proposal_id] = proposal
    return proposal


def sample_approved() -> Proposal:
    """A fresh proposal of the sample type, approved."""
    staged = sample_staged()
    return approve(staged.proposal_id, expected_version=staged.version)


def approve(proposal_id: str, *, expected_version: int) -> Proposal:
    return _change(proposal_id, ProposalState.APPROVED, expected_version)


def expire(proposal_id: str, *, expected_version: int) -> Proposal:
    return _change(proposal_id, ProposalState.EXPIRED, expected_version)


def execute(proposal_id: str) -> Proposal:
    """Re-check the state at execution time, then run the sample executor (AD-37).

    `approved → executing`, the executor, then `executing → executed`. A
    proposal that left `approved` — expired, rejected — is refused here, at
    execution time, whatever it was when the work was enqueued.
    """
    current = _BOOK.get(proposal_id)
    if current is None:
        raise KeyError(f"no proposal is staged under {proposal_id!r}")
    proposal_type = _TYPES.get(current.type)
    if proposal_type is None or proposal_type.approval.kind is not ApprovalKind.EXECUTOR:
        raise IllegalTransition(f"{proposal_id}'s type has no executor; nothing runs.")
    executing = _change(proposal_id, ProposalState.EXECUTING, current.version)
    executor = proposal_type.approval.executor
    if executor is not None:
        executor(executing)
    return _change(proposal_id, ProposalState.EXECUTED, executing.version)
