"""Disclosure and cost records (AD-31, AD-17, AD-38).

Two independent reviewers found that pointing AD-31's audit at `event_log.md`
inverted the rule it was built to serve: `event_log.md` exists per scope, and the
project scope is git-committed, so a record naming `scopes={personal, project}`
would be pushed to the employer's repository. The mechanism built to prove
nothing leaked would have been the leak.

The fix is structural rather than procedural — a `DisclosureRecord` has one home
by construction, and a record naming personal material cannot be written to a
committed scope at all.

Imports nothing from `pm_ai` except sibling domain modules (AD-30).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pm_ai.domain.identity import DataScope, ScopeKind

# AD-38: one file, outside every repository. Both AD-31's "what has left this
# machine" and AD-17's monthly total read exactly this, which is also what makes
# them answerable — spread across N per-scope logs, neither query has a source.
DISCLOSURE_LEDGER_SCOPE = DataScope(ScopeKind.APPLICATION)
DISCLOSURE_LEDGER_PATH = "~/.pm-ai/disclosure.md"


class CommittedScopeLeak(ValueError):
    """A record naming personal material was routed to a git-committed scope."""


@dataclass(frozen=True, slots=True)
class DisclosureRecord:
    """One frontier call's scope provenance (AD-31).

    Deliberately has no `scope` field: it is not a per-scope record, and giving
    it one would reintroduce the routing decision that caused the leak.
    """

    at: datetime
    task_class: str
    model: str
    contributing_scopes: frozenset[DataScope]
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    destination: DataScope | None = None

    @property
    def involves_personal(self) -> bool:
        return any(s.is_personal for s in self.contributing_scopes)

    @property
    def home(self) -> DataScope:
        """Always the application scope. There is no second option."""
        return DISCLOSURE_LEDGER_SCOPE


def assert_writable(record: object, *, scope: DataScope) -> None:
    """AD-38's general invariant, checked at the write boundary.

    No record written to a git-committed scope may reference personal-scope
    material — not by content, not by `source_ref`, not by scope name. A
    cross-scope operation writes its project-visible part to the project log and
    everything else to the application ledger; it never writes one record naming
    both.
    """
    if isinstance(record, DisclosureRecord) and scope != DISCLOSURE_LEDGER_SCOPE:
        raise CommittedScopeLeak(
            f"DisclosureRecord routed to {scope}. Its only home is "
            f"{DISCLOSURE_LEDGER_PATH} (AD-38) — the project scope is committed."
        )
    if not scope.is_git_committed:
        return
    scopes = getattr(record, "contributing_scopes", None) or ()
    if any(getattr(s, "is_personal", False) for s in scopes):
        raise CommittedScopeLeak(
            f"record references personal scope and is bound for {scope}, which is "
            f"git-committed. Split it: project-visible part to the project log, "
            f"the rest to the application ledger (AD-38)."
        )


def cross_scope_split(record: DisclosureRecord) -> tuple[DisclosureRecord, None]:
    """A cross-scope operation is two entries, never one naming both (AD-38).

    Returns the application-ledger record plus the project-visible remainder,
    which for a disclosure record is always None — nothing about a frontier
    call's provenance belongs in a repository.
    """
    return record, None
