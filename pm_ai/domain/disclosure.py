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

from pm_ai.domain.event_entries import render_value
from pm_ai.domain.identity import DataScope, ScopeKind

# AD-38: one file, outside every repository. Both AD-31's "what has left this
# machine" and AD-17's monthly total read exactly this, which is also what makes
# them answerable — spread across N per-scope logs, neither query has a source.
DISCLOSURE_LEDGER_SCOPE = DataScope(ScopeKind.APPLICATION)
DISCLOSURE_LEDGER_PATH = "~/.pm-ai/disclosure.md"
DISCLOSURE_LEDGER_ARTIFACT = "disclosure.md"


class MalformedDisclosure(ValueError):
    """A complete line in the ledger that is not a disclosure record.

    Distinct from an unterminated tail, which is a write in progress and is
    dropped: a terminated line that will not parse is corruption in an audit
    trail, and an audit trail that quietly skips what it cannot read is not one.
    """


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


def referenced_scopes(record: object) -> tuple[DataScope, ...]:
    """Every scope a record names, across the shapes records actually take.

    A `DisclosureRecord` names its sources in `contributing_scopes`; a
    `NormalizedEvent` names the single scope it belongs to in `scope`. Reading
    only the former made this guard a no-op on the only record type the storage
    service actually persists — it passed because the attribute was absent, not
    because the record was safe.
    """
    found: list[DataScope] = []
    contributing = getattr(record, "contributing_scopes", None) or ()
    found.extend(s for s in contributing if isinstance(s, DataScope))
    own = getattr(record, "scope", None)
    if isinstance(own, DataScope):
        found.append(own)
    return tuple(found)


def assert_writable(record: object, *, scope: DataScope) -> None:
    """AD-38's general invariant, checked at the write boundary.

    No record written to a git-committed scope may reference personal- or
    people-scope material — not by content, not by `source_ref`, not by scope
    name. A cross-scope operation writes its project-visible part to the project
    log and everything else to the application ledger; it never writes one record
    naming both.

    `people` is included for the same structural reason and a sharper
    consequence: a direct report's performance objective committed to a
    repository is readable by that report's peers.
    """
    if isinstance(record, DisclosureRecord) and scope != DISCLOSURE_LEDGER_SCOPE:
        raise CommittedScopeLeak(
            f"DisclosureRecord routed to {scope}. Its only home is "
            f"{DISCLOSURE_LEDGER_PATH} (AD-38) — the project scope is committed."
        )
    if not scope.is_git_committed:
        return
    for referenced in referenced_scopes(record):
        if referenced.is_personal or referenced.is_people:
            raise CommittedScopeLeak(
                f"record references {referenced} and is bound for {scope}, which is "
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


def assert_citation_legal(*, cited: DataScope, into: DataScope) -> None:
    """AD-38 — a committed record may not cite personal- or people-scope material.

    `assert_writable` checks the scope a record *belongs to*; this checks the
    scope a record *points at*. Both are needed, because AD-38 forbids the
    reference "not by content, not by `source_ref`, not by scope name" — and a
    commitment in a git-committed ledger citing `meeting:<id>` is a reference by
    source_ref to whatever scope owns that meeting.
    """
    if into.is_git_committed and (cited.is_personal or cited.is_people):
        raise CommittedScopeLeak(
            f"a record in {into} (git-committed) cannot cite material owned by "
            f"{cited}. The citation would publish, by reference, exactly what the "
            f"scope boundary exists to keep out (AD-38)."
        )


def render_disclosure(record: DisclosureRecord) -> str:
    """One frontier call as one Markdown line, for `~/.pm-ai/disclosure.md`.

    **No entry id and no category token.** Every line in this file is a frontier
    call, so there is nothing to tag — the file is the vocabulary. Giving the
    record a `LedgerCategory` member instead would have created a spelling that
    `append_event_log` accepts into *any* scope, and the leak guard runs only on
    the batch path (`service.py:1209`) — so a disclosure naming personal material
    could be written into a git-committed project log with nothing refusing it.
    That is the leak AD-38 exists to prevent, reintroduced through the vocabulary.

    The value encoding is shared with the event log's, so one tokenizer reads
    both files. Sharing the encoding is not sharing the vocabulary.

    Every field is rendered, including a zero cost and an absent destination.
    AD-17's monthly total has to be recomputable from these lines alone; a field
    omitted because it was empty is a field a reader cannot distinguish from one
    that was never written.
    """
    scopes = ",".join(sorted(str(scope) for scope in record.contributing_scopes))
    destination = "none" if record.destination is None else str(record.destination)
    fields = (
        ("at", record.at.isoformat()),
        ("task_class", record.task_class),
        ("model", record.model),
        ("input_tokens", str(record.input_tokens)),
        ("output_tokens", str(record.output_tokens)),
        # `repr` of a float round-trips exactly in Python; a fixed number of
        # decimal places would silently round a sub-cent call to zero, and a
        # month of those sums to a figure the ledger cannot justify.
        ("cost_usd", repr(record.estimated_cost_usd)),
        ("scopes", scopes),
        ("destination", destination),
    )
    parts = [
        f"{key}={render_value(value, where=f'field {key!r}')}" for key, value in fields
    ]
    return "- " + " ".join(parts)
