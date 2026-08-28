"""Provenance attribution (AD-36) — the pass that was missing.

AD-36 says attribution is established at both ends. Only one end existed: the
skill layer recorded every class-M mutation, and nothing ever read that ledger
back. Meanwhile the GitLab connector hard-coded `authored_by=EXTERNAL`, so the
`UNKNOWN` default AD-36 calls fail-closed was unreachable.

The consequence was not a crash. pm-ai posted a comment, harvested it back, and
counted it as evidence that pm-ai's own promise had been kept — the ledger
becoming confidently wrong in the direction that looks like success.

This module is the *match* step. It is I/O-free: it takes the ledger as data and
returns re-attributed events, so it is testable without a daemon.
"""

from __future__ import annotations

from pm_ai.domain.events import NormalizedEvent, Provenance
from pm_ai.domain.identity import PM_AI_MINTED

# The ledger records an execution whose provider returned no usable identifier
# with this sentinel. Such a mutation is invisible to the join below, so events
# in its scope cannot be cleared as external by that mechanism alone.
NO_EXTERNAL_ID = ""


def own_artifact_index(
    executed: dict[str, tuple[str, str]],
) -> tuple[frozenset[tuple[str, str, str]], frozenset[tuple[str, str]]]:
    """Index the executed-mutation ledger for the AD-36 join.

    Returns `(artifacts, blind_scopes)`:

    - `artifacts` — `(system, scope, external_id)` triples we know we created.
      The join key is stated in AD-36 rather than left to each implementer,
      because the ledger is keyed by `TargetRef` and events by `SourceRef`, and
      an unstated mapping between two identifier grammars is precisely the
      defect AD-34 exists to fix.
    - `blind_scopes` — `(system, scope)` pairs where we performed a mutation but
      cannot recognise its artifact. Events there are attributable only by the
      bot-identity mechanism, and otherwise resolve to UNKNOWN.
    """
    artifacts: set[tuple[str, str, str]] = set()
    blind: set[tuple[str, str]] = set()
    for lock_key, external_id in executed.values():
        parts = lock_key.split(":")
        if len(parts) != 4:
            continue
        system, scope = parts[0], parts[1]
        if external_id == NO_EXTERNAL_ID:
            blind.add((system, scope))
        else:
            artifacts.add((system, scope, external_id))
    return frozenset(artifacts), frozenset(blind)


def attribute(event: NormalizedEvent, artifacts, blind) -> Provenance:
    """Decide one event's provenance. Never returns EXTERNAL on a guess.

    Three outcomes, in the order AD-36 states them:

    1. the actor resolves to pm-ai's own bot identity  -> PM_AI
    2. the artifact is one we recorded creating        -> PM_AI
    3. otherwise external, unless we hold an unmatched
       mutation in the same scope, in which case we
       cannot prove it isn't ours                      -> UNKNOWN

    **A scopeless reference is global, not necessarily foreign.** This branch
    used to answer EXTERNAL for every scopeless ref, commented "global entities
    (meetings) are never our writes". That is true of `meeting:` — a meeting
    happens in the world and pm-ai only records it — and false of `goal:`, whose
    id AD-41 rule 2 has *storage* mint. EXTERNAL is the one value AD-36 admits
    as evidence, so a goal-sourced event could prove that pm-ai's own promise
    had been kept: the same defect this module exists to close, reached through
    AD-33's citation rule instead of through a connector, which is the door the
    original fix was watching.

    The ledger join cannot decide it either way. That join is keyed
    `(system, scope, external_id)` and a scopeless ref has no scope, so the
    answer has to come from a declaration. `PM_AI_MINTED` is that declaration,
    closed in `domain` beside the scopeless set it partitions.
    """
    if event.actor.is_pm_ai:
        return Provenance.PM_AI
    ref = event.source_ref
    if ref.scope is None:
        return Provenance.PM_AI if ref.system in PM_AI_MINTED else Provenance.EXTERNAL
    if (ref.system, ref.scope, ref.native_id) in artifacts:
        return Provenance.PM_AI
    if (ref.system, ref.scope) in blind:
        return Provenance.UNKNOWN
    return Provenance.EXTERNAL


def attribute_all(
    events: tuple[NormalizedEvent, ...], executed: dict[str, tuple[str, str]]
) -> tuple[NormalizedEvent, ...]:
    """Re-attribute a harvest. Runs before persistence, never after.

    A connector may not assert `external` (AD-36): provenance is decided here,
    the only layer that can see the ledger. A connector emits `unknown` and this
    resolves it.
    """
    from dataclasses import replace

    artifacts, blind = own_artifact_index(executed)
    return tuple(
        replace(event, authored_by=attribute(event, artifacts, blind)) for event in events
    )
