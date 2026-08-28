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
from pm_ai.ports import SkillPort, StoragePort


class MissingIdempotencyKey(ValueError):
    """AD-20 — a mutating invocation arrived without a key."""


class SkillNotAuthorized(PermissionError):
    """AD-18 — unlisted skill, or a call outside its declared permission."""


@dataclass(frozen=True, slots=True)
class Invocation:
    external_id: str
    replayed: bool  # True when the key was already spent — no second write


class SkillRegistry:
    def __init__(self, storage: StoragePort, *, scope: DataScope) -> None:
        self._storage = storage
        self._scope = scope
        # `SkillPort`, not `object`. This registry is what enforces AD-18, and
        # every check it makes reads an attribute — `system`, `name`,
        # `permission`, `execute`. Typed as `object` those reads were unverified,
        # which made the security boundary the least-checked code in the package:
        # a skill missing `permission` would have passed registration and failed
        # at the moment of the mutation it was supposed to authorize.
        self._skills: dict[str, SkillPort] = {}
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)

    def register(self, skill: SkillPort, *, replace: bool = False) -> None:
        """Add a skill under its qualified name, refusing a name already taken.

        Silent replacement was the previous behaviour, and in the module that
        *is* the AD-18 allowlist that means a later registration swaps the code
        behind an authorized name with nobody deciding it — the permission
        checks would then authorize the old skill's contract and execute the
        new skill's code. `replace=True` is the deciding: it exists for a test
        substituting a double, and for whatever hot-reload story later owns
        skill updates, so the substitution is spelled at the call site.
        """
        qualified = f"{skill.system}.{skill.name}"
        if qualified in self._skills and not replace:
            raise ValueError(
                f"{qualified} is already registered. A second registration "
                f"replaces the code behind an authorized name (AD-18); if the "
                f"replacement is intended, say so with replace=True."
            )
        self._skills[qualified] = skill

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

            # AD-20 — claim the key BEFORE calling out. Recording only after the
            # provider responds leaves a crash window in which the mutation
            # happened and the ledger does not know: the retry then executes a
            # second time, which is the duplicate this rule exists to prevent, on
            # the ordinary path rather than the rare one. A key found in flight
            # is a reconciliation task, not a licence to re-execute.
            self._storage.begin_execution(idempotency_key, target)
            external_id = skill.execute(target, payload)
            self._storage.settle_execution(idempotency_key, external_id)  # AD-36
            self._storage.append_event_log(  # AD-1
                f"- [skill] {qualified_name} target={target.lock_key} "
                f"external_id={external_id} key={idempotency_key}",
                scope=self._scope,
            )
        return Invocation(external_id=external_id, replayed=False)
