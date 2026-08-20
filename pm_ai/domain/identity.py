"""Identity types: scope, references, actors.

Two independent reviewers found the same root cause twice: words shared across
components without a type behind them. `scope` meant four different things,
`source_ref` had two incompatible grammars, and `target_ref` granularity was
undefined so a per-target lock could not actually serialize anything.

Prose rules did not fix that. These types do — a malformed reference is a
construction error, not a review comment.

Imports nothing from `pm_ai` (AD-30).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ScopeKind(Enum):
    """The scope kinds of AD-4.

    Distinct from `SkillPermission` below. Both were called "scope" in the
    spine's prose, which is how a reviewer found that a project literally named
    `personal` would defeat AD-31's privacy boundary.

    PEOPLE is stored *under* the application scope (`~/.pm-ai/private/people/`)
    but is its own kind, because two rules turn on telling it apart from
    PERSONAL and neither can be written against a path: a report's career goal
    may sync to HR (AD-31, UJ-4) and the PM's own coaching record may never.
    """

    APPLICATION = "application"  # ~/.pm-ai/
    PERSONAL = "personal"  # ~/.manager-ai/ — the PM's own
    PEOPLE = "people"  # ~/.pm-ai/private/people/ — a direct report's
    PROJECT = "project"  # <repo>/.project-ai/


@dataclass(frozen=True, slots=True)
class DataScope:
    """Which scope some data belongs to (AD-4).

    The subject id is required exactly where a scope has a subject — a project
    id for PROJECT, a person id for PEOPLE — and forbidden elsewhere, so
    `DataScope(PROJECT)` cannot exist ambiguously.
    """

    kind: ScopeKind
    project_id: str | None = None
    person_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind is ScopeKind.PROJECT and not self.project_id:
            raise ValueError("PROJECT scope requires a project_id")
        if self.kind is not ScopeKind.PROJECT and self.project_id:
            raise ValueError(f"{self.kind.value} scope must not carry a project_id")
        if self.kind is ScopeKind.PEOPLE and not self.person_id:
            raise ValueError("PEOPLE scope requires a person_id — whose record is this?")
        if self.kind is not ScopeKind.PEOPLE and self.person_id:
            raise ValueError(f"{self.kind.value} scope must not carry a person_id")

    @property
    def is_personal(self) -> bool:
        """The PM's own material, and only that (AD-31).

        Deliberately false for PEOPLE: a direct report's record is personal data,
        but it is not *the PM's* personal scope, and conflating the two would
        either forbid the HR sync UJ-4 requires or permit the export FR-16
        forbids.
        """
        return self.kind is ScopeKind.PERSONAL

    @property
    def is_people(self) -> bool:
        """A direct report's material (AD-4). HR-syncable on approval (AD-31)."""
        return self.kind is ScopeKind.PEOPLE

    @property
    def is_git_committed(self) -> bool:
        """AD-38 — project scope lives in the employer's repository.

        This is why disclosure records cannot live in the event ledger: it exists
        per scope, and one of those scopes is pushed.
        """
        return self.kind is ScopeKind.PROJECT

    def __str__(self) -> str:
        subject = self.project_id or self.person_id
        return f"{self.kind.value}:{subject}" if subject else self.kind.value


class SkillPermission(Enum):
    """What an MCP skill is authorized to do (AD-18).

    Named `SkillPermission`, never "scope", precisely because the collision with
    `DataScope` was load-bearing in a privacy rule.
    """

    READ = "read"
    COMMENT = "comment"
    EDIT = "edit"
    TRANSITION = "transition"
    CREATE = "create"
    SEND = "send"


# ── References ───────────────────────────────────────────────────────────────
# One grammar, one type. The spine previously had two SourceRef definitions with
# different shapes, in two modules, both claiming to be canonical.

_REF = re.compile(r"^(?P<system>[a-z0-9_]+):(?P<scope>[A-Za-z0-9_.-]+):(?P<kind>[a-z0-9_]+):(?P<native_id>\S+)$")
# Global entities belong to no project, so they take the two-part form:
# `meeting:mtg_01HX`, `goal:goal_01HX`. A closed set (AD-34) — adding a member
# is a deliberate change here, not something a caller may assume.
_SCOPELESS = frozenset({"meeting", "goal"})
_NON_DURABLE = frozenset({"transcript", "file", "chat_history"})


class MalformedReference(ValueError):
    """The reference does not parse under the AD-34 grammar."""


class NonDurableReferent(ValueError):
    """The reference points at a derived capture rather than the event (AD-33)."""


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Provenance: where a fact came from.

    AD-33 — points at the most upstream *durable* referent, never a derived
    capture. A transcript is a derivative of a meeting and can never be a
    SourceRef, so a 30-day transcript purge cannot empty a citation.

    AD-34 — one grammar: `<system>:<scope>:<kind>:<native_id>`, with scopeless
    global forms such as `meeting:mtg_01HX`.
    """

    system: str
    kind: str
    native_id: str
    scope: str | None = None

    @classmethod
    def parse(cls, raw: str) -> SourceRef:
        head = raw.split(":", 1)[0]
        if head in _NON_DURABLE:
            raise NonDurableReferent(
                f"{raw!r} cites a derived capture. Cite the event it came from "
                f"(a transcript's referent is its meeting)."
            )
        if head in _SCOPELESS:
            parts = raw.split(":")
            if len(parts) != 2 or not parts[1]:
                raise MalformedReference(f"{raw!r} is not `{head}:<id>`")
            return cls(system=head, kind=head, native_id=parts[1])
        m = _REF.match(raw)
        if not m:
            raise MalformedReference(
                f"{raw!r} is not `<system>:<scope>:<kind>:<native_id>` — a bare URL "
                f"or ticket key cannot be joined across connectors."
            )
        return cls(
            system=m["system"], scope=m["scope"], kind=m["kind"], native_id=m["native_id"]
        )

    @property
    def is_durable(self) -> bool:
        return self.system not in _NON_DURABLE

    def __str__(self) -> str:
        return (
            f"{self.system}:{self.native_id}"
            if self.scope is None
            else f"{self.system}:{self.scope}:{self.kind}:{self.native_id}"
        )


@dataclass(frozen=True, slots=True)
class TargetRef:
    """The external entity a mutation acts on (AD-37).

    Granularity is the whole point. Both reviewers found that AD-37's per-target
    lock serialized nothing, because one skill could lock
    `jira:alpha:issue:PAY-102` while another locked
    `jira:alpha:issue:PAY-102#labels` — two names for one contended entity.

    A TargetRef is therefore always the *lockable entity*. Sub-resource
    fragments are rejected: they belong in the payload, not the identity.
    """

    system: str
    scope: str
    kind: str
    native_id: str

    @classmethod
    def parse(cls, raw: str) -> TargetRef:
        if "#" in raw or "?" in raw:
            raise MalformedReference(
                f"{raw!r} names a sub-resource. A TargetRef is the lockable entity; "
                f"put the field being changed in the payload."
            )
        ref = SourceRef.parse(raw)
        if ref.scope is None:
            raise MalformedReference(f"{raw!r} is not a mutable external entity")
        return cls(system=ref.system, scope=ref.scope, kind=ref.kind, native_id=ref.native_id)

    @property
    def lock_key(self) -> str:
        """AD-37 serializes on exactly this string."""
        return f"{self.system}:{self.scope}:{self.kind}:{self.native_id}"

    def __str__(self) -> str:
        return self.lock_key


# ── Actors ───────────────────────────────────────────────────────────────────

UNRESOLVED_ACTOR = "actor_unresolved"
PM_AI_ACTOR = "actor_pm_ai"


@dataclass(frozen=True, slots=True)
class Actor:
    """A resolved person or system (AD-34).

    Connectors supply native handles — a commit email, a tenant account, a VTT
    speaker label. Those are aliases, never identities. An unresolvable handle
    becomes UNRESOLVED_ACTOR explicitly, because silently using the raw string is
    how one engineer becomes four people in a metric that feeds a review.
    """

    actor_id: str
    display_name: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.actor_id != UNRESOLVED_ACTOR

    @property
    def is_pm_ai(self) -> bool:
        return self.actor_id == PM_AI_ACTOR


UNRESOLVED = Actor(actor_id=UNRESOLVED_ACTOR, display_name="unresolved")
PM_AI = Actor(actor_id=PM_AI_ACTOR, display_name="pm-ai")


# The alias table maps a provider's native handle to a stable actor. Seeded at
# configuration time and persisted as Tier-1 data; the resolution *rule* lives
# here because getting it wrong splits one person across a metric (AD-34).
ALIASES: dict[tuple[str, str], Actor] = {}


def register_alias(system: str, handle: str, actor: Actor) -> None:
    ALIASES[(system, handle.lower())] = actor


def resolve_actor(*, system: str, handle: str | None) -> Actor:
    """Resolve a native handle to an Actor, or to UNRESOLVED — never to itself.

    Returning a bare handle as an identity is the failure this exists to
    prevent: the same engineer arrives as a commit email from GitLab and a
    speaker label from a transcript, and becomes two people in a metric that
    feeds a performance review.
    """
    if not handle:
        return UNRESOLVED
    return ALIASES.get((system, handle.lower()), UNRESOLVED)
