"""The MCP skill registry — the sole home of class M egress (AD-1).

Enforces, in order, on every mutation:
  AD-18  the skill is on the allowlist and the call is within its permission
  AD-20  an idempotency key is present, and is *honoured* — a replay is a no-op
  AD-37  mutations on one external entity serialize by target lock key
  AD-36  what was written is recorded, so normalization recognises it later
  AD-1   one event_log entry per invocation, in the owning scope
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass

from pm_ai.domain.identity import DataScope, SkillPermission, TargetRef
from pm_ai.domain.lifecycle import lookup_verb


class MissingIdempotencyKey(ValueError):
    """AD-20 — a mutating invocation arrived without a key."""


class SkillNotAuthorized(PermissionError):
    """AD-18 — unlisted skill, or a call outside its declared permission."""


@dataclass(frozen=True, slots=True)
class Invocation:
    external_id: str
    replayed: bool  # True when the key was already spent — no second write


class SkillRegistry:
    def __init__(self, storage, *, scope: DataScope) -> None:
        self._storage = storage
        self._scope = scope
        self._skills: dict[str, object] = {}
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)

    def register(self, skill) -> None:
        self._skills[f"{skill.system}.{skill.name}"] = skill

    def invoke(
        self, qualified_name: str, *, target: TargetRef, payload: dict, idempotency_key: str | None
    ) -> Invocation:
        skill = self._skills.get(qualified_name)
        if skill is None:
            raise SkillNotAuthorized(
                f"{qualified_name} is not in the registry. An unlisted skill is never "
                f"invoked (AD-18)."
            )
        if not idempotency_key:
            raise MissingIdempotencyKey(
                f"{qualified_name} mutates external state and arrived without an "
                f"idempotency key (AD-20)."
            )
        verb = lookup_verb(skill.system, skill.name)  # unregistered verbs fail closed
        if verb.permission is not skill.permission:
            raise SkillNotAuthorized(
                f"{qualified_name} declares {skill.permission.value} but the verb "
                f"requires {verb.permission.value} (AD-18)."
            )

        # AD-37 — one entity, one mutation at a time.
        with self._locks[target.lock_key]:
            # AD-20 — the key is *honoured*, not merely carried. This is the
            # check that makes at-least-once delivery safe: a replayed job after
            # a crash or a restore must not act twice.
            if self._storage.was_executed(idempotency_key):
                prior = self._storage.executed_mutations()[idempotency_key]
                return Invocation(external_id=prior[1], replayed=True)

            external_id = skill.execute(target, payload)
            self._storage.record_execution(idempotency_key, target, external_id)  # AD-36
            self._storage.append_event_log(  # AD-1
                f"- [skill] {qualified_name} target={target.lock_key} "
                f"external_id={external_id} key={idempotency_key}",
                scope=self._scope,
            )
        return Invocation(external_id=external_id, replayed=False)
