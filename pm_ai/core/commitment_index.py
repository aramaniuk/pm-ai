"""The commitment index: rows derived from the ledgers alone (story 15b, AD-3, AD-46).

`commitment_index.db` holds one row per commitment per scope. A scope's rows
are the fold of its `commitments_log.md` — read once through
`CommitmentLog.read_with_bytes`, parsed by `parse_ledger` and folded by
`fold`, never a second way — and each row stores the MD5 of exactly the bytes
folded (`source_md5`). No row holds a commitment's words or a person's name,
and nothing in a row depends on the clock or the display zone, so the index
can be dropped and rebuilt from the Markdown with the same rows.

Every call here goes through `StoragePort`. A write is handed to `step` — the
writer thread in the daemon (AD-5, AD-19), as `catch_up` takes one; the reads
run where the caller runs.

- `derive(storage, scope)` is the only way rows are written: read, compare the
  stored checksums with the file's, and replace the scope's rows only when
  they differ (AD-45: a request whose inputs are unchanged does no work).
- `check(...)` is the daemon's check at start.
- `commitment_index_snapshot`, `drop_commitment_index` and
  `rebuild_commitment_index` are for story 1h's `pm_ai.storage.reindex`.

What starts a derivation until story 10a is `pm_ai.app.commitment_indexing`;
10a's job keeps everything here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, TypeVar

from pm_ai.core.commitment_log import CommitmentLedger, CommitmentLog
from pm_ai.core.jobs import source_md5
from pm_ai.domain.commitment_index import INDEX_COLUMNS, IndexRow, ReplacedRows, ScopeChecksums
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.scope_model import ScopeResolutionError
from pm_ai.ports import CommitmentIndexUnusable, StoragePort

__all__ = [
    "INDEX_COLUMNS",
    "INDEX_FORMAT",
    "CheckReport",
    "Derivation",
    "IndexRow",
    "ProjectRootMissing",
    "RebuildReport",
    "check",
    "commitment_index_snapshot",
    "derive",
    "drop_commitment_index",
    "index_rows",
    "rebuild_commitment_index",
    "stored_value",
]

INDEX_FORMAT = 1
"""The format of `commitment_index.db`'s columns. A different one is dropped and
rebuilt, never migrated: the index is Tier 3 (AD-3)."""

UNREADABLE: tuple[type[BaseException], ...] = (OSError, UnicodeError, ScopeResolutionError)
"""A ledger that cannot be read: nothing is written for its scope, and its rows stay."""

_T = TypeVar("_T")
Step = Callable[[Callable[[], Any]], Any]
Present = Callable[[DataScope], bool]


class ProjectRootMissing(FileNotFoundError):
    """A project's repository root does not exist: its ledger is unreadable, not absent."""


def _direct(work: Callable[[], _T]) -> _T:
    return work()


def stored_value(value: object) -> str | int | None:
    """A value as the index stores it: the one writer of the pinned forms (15b).

    An instant is `at.astimezone(timezone.utc).isoformat(timespec="microseconds")`,
    always ending `+00:00`; a date is `YYYY-MM-DD`; a flag is `0` or `1`;
    empty (`None` or `""`) is `None`, stored as SQL `NULL`.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            # Read as machine-local, it would make a row depend on the zone.
            raise TypeError("the index stores no instant without a timezone")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value
    raise TypeError(f"the index stores no {type(value).__name__}")


def _text(value: object) -> str:
    found = stored_value(value)
    if not isinstance(found, str):
        raise ValueError("a required column of the index is empty")
    return found


def _optional_text(value: object) -> str | None:
    found = stored_value(value)
    return found if isinstance(found, str) else None


def _flag(value: bool) -> int:
    return 1 if value else 0


def index_rows(scope: str, ledger: CommitmentLedger, md5: str) -> tuple[IndexRow, ...]:
    """One row per commitment of the fold, each column from it (story 15b). Pure.

    Ordered by `commitment_id`, so the rows do not depend on the file's order.
    """
    rows: list[IndexRow] = []
    for root in sorted(ledger.commitments):
        commitment = ledger.commitments[root]
        details = ledger.details[root]
        due = commitment.due
        rows.append(
            IndexRow(
                scope=scope,
                commitment_id=_text(commitment.commitment_id),
                proposal_id=_text(commitment.proposal_id),
                type=_text(commitment.type),
                state=_text(commitment.state.value),
                state_source=_text(commitment.state_source.value),
                state_at=_optional_text(details.state_at),
                due_value=_text(due.value),
                due_date=_optional_text(due.date),
                due_estimated=_flag(due.estimated),
                due_by_pm=_flag(details.due_by_pm),
                meeting_id=_optional_text(commitment.meeting_id),
                meeting_start=_optional_text(commitment.start),
                day=_optional_text(commitment.day),
                day_from=_optional_text(commitment.day_from),
                approved_at=_text(commitment.approved_at),
                changed_at=_text(details.changed_at),
                version=_text(details.version),
                malformed_named=_flag(root in ledger.malformed_roots),
                source_md5=md5,
            )
        )
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class Derivation:
    """What one derivation of one scope did.

    `outcome` is `current` (nothing folded or written), `derived` (its rows
    replaced), `changed` (another writer derived it meanwhile: nothing
    written, derive it again), `unreadable` (its ledger could not be read:
    nothing written, its rows stay) or `failed` (an unexpected error, by type
    name, in `check` only).
    """

    scope: str
    outcome: str
    written: int = 0
    removed: int = 0
    failure: str | None = None


def derive(
    storage: StoragePort,
    scope: DataScope,
    *,
    step: Step = _direct,
    present: Present | None = None,
    force: bool = False,
) -> Derivation:
    """Derive `scope`'s rows again if its ledger changed; the only way rows are written.

    1. A project whose repository root does not exist (`present` says no) is
       unreadable; then the ledger is read with `read_with_bytes` and its
       bytes hashed.
    2. The stored checksums are read (a plain read): rows that all carry the
       file's checksum, or no rows and no commitment in the fold, are current.
       `force` (a rebuild) consults them only for the replace's condition.
    3. The rows are computed (`index_rows`).
    4. `replace_commitment_rows`, handed to `step`, conditional on the
       checksums step 2 saw.

    `CommitmentIndexUnusable` and the store's own errors pass through.
    """
    key = str(scope)
    try:
        if present is not None and not present(scope):
            raise ProjectRootMissing(f"{key}'s repository root does not exist")
        ledger, data = CommitmentLog(storage).read_with_bytes(scope)
    except UNREADABLE as unreadable:
        return Derivation(key, "unreadable", failure=type(unreadable).__name__)
    md5 = source_md5(data)
    held = storage.commitment_index_checksums().get(key)
    expected = held.md5s if held is not None else frozenset()
    if not force:
        if held is None and not ledger.commitments:
            return Derivation(key, "current")
        if held is not None and held.md5s == {md5}:
            return Derivation(key, "current")
    rows = index_rows(key, ledger, md5)
    if not rows and held is None:
        # Nothing to remove and nothing to write: no file is created for it.
        return Derivation(key, "derived")
    replaced: ReplacedRows = step(lambda: storage.replace_commitment_rows(scope, rows, expected))
    if replaced.changed:
        return Derivation(key, "changed")
    return Derivation(key, "derived", written=replaced.written, removed=replaced.removed)


@dataclass(frozen=True, slots=True)
class RebuildReport:
    """Every scope a rebuild was given, and what each derivation did."""

    derivations: tuple[Derivation, ...]

    def count(self, outcome: str) -> int:
        return sum(1 for each in self.derivations if each.outcome == outcome)


def commitment_index_snapshot(storage: StoragePort) -> tuple[IndexRow, ...]:
    """Every row, every column, ordered by `(scope, commitment_id)`; `()` when absent."""
    return tuple(storage.commitment_index_rows())


def drop_commitment_index(storage: StoragePort, *, step: Step = _direct) -> None:
    """Remove the index file and its `-wal` and `-shm`, guarded as Tier 3 only (1h)."""
    step(storage.drop_commitment_index)


def rebuild_commitment_index(
    storage: StoragePort,
    scopes: Iterable[DataScope],
    *,
    step: Step = _direct,
    present: Present | None = None,
) -> RebuildReport:
    """`derive` each scope given, every one of them derived (1h).

    The stored checksums are not consulted for currency. The other scopes'
    rows are untouched: removing rows of scopes no longer composed is the
    start check's step, not the rebuild's. A failure passes through.
    """
    return RebuildReport(
        tuple(derive(storage, scope, step=step, present=present, force=True) for scope in scopes)
    )


@dataclass(frozen=True, slots=True)
class CheckReport:
    """What the check at daemon start did (story 15b)."""

    derivations: tuple[Derivation, ...] = ()
    removed: tuple[str, ...] = ()
    unusable: str | None = None
    """The reason the index was dropped and rebuilt, if it was."""
    latched: str | None = None
    """The type name of the failure that left the index unusable until the next start."""
    registry_unreadable: bool = False
    """No rows were removed: the projects registry could not be read."""
    remove_failed: str | None = None
    """The type name of the failure that kept gone projects' rows, if one did."""

    def count(self, outcome: str) -> int:
        return sum(1 for each in self.derivations if each.outcome == outcome)


def _derive_or_report(
    storage: StoragePort, scope: DataScope, *, step: Step, present: Present | None
) -> Derivation:
    try:
        return derive(storage, scope, step=step, present=present)
    except CommitmentIndexUnusable:
        raise
    except Exception as failed:  # noqa: BLE001 — one scope's failure stops no other
        return Derivation(str(scope), "failed", failure=type(failed).__name__)


def _rebuilt(
    storage: StoragePort,
    scopes: Sequence[DataScope],
    reason: str,
    *,
    step: Step,
    present: Present | None,
) -> CheckReport:
    try:
        drop_commitment_index(storage, step=step)
        report = rebuild_commitment_index(storage, scopes, step=step, present=present)
    except Exception as failed:  # noqa: BLE001 — latched until the next start
        return CheckReport(unusable=reason, latched=type(failed).__name__)
    return CheckReport(derivations=report.derivations, unusable=reason)


def check(
    storage: StoragePort,
    scopes: Sequence[DataScope],
    *,
    registry_readable: bool,
    step: Step = _direct,
    present: Present | None = None,
) -> CheckReport:
    """The check at daemon start: every stale scope derived, gone projects removed (15b).

    1. An unusable index is dropped and rebuilt from every scope given, once;
       when the drop or the rebuild fails, the report says it is latched.
    2. Each scope given: `derive`.
    3. Each scope with rows that is not given is removed in one claimed step,
       only when the registry was read (`registry_readable`) and the scope
       is a project positively absent from it.
    """
    try:
        held: Mapping[str, ScopeChecksums] = storage.commitment_index_checksums()
        derivations = tuple(
            _derive_or_report(storage, scope, step=step, present=present) for scope in scopes
        )
    except CommitmentIndexUnusable as unusable:
        return _rebuilt(storage, scopes, unusable.reason, step=step, present=present)
    if not registry_readable:
        return CheckReport(derivations=derivations, registry_unreadable=True)
    given = {str(scope) for scope in scopes}
    gone = tuple(key for key in sorted(held) if key not in given and _is_project(key))
    if gone:
        try:
            step(lambda: storage.remove_commitment_scopes(gone))
        except Exception as failed:  # noqa: BLE001 — the next start tries again
            return CheckReport(derivations=derivations, remove_failed=type(failed).__name__)
    return CheckReport(derivations=derivations, removed=gone)


def _is_project(key: str) -> bool:
    try:
        return DataScope.parse(key).kind is ScopeKind.PROJECT
    except ValueError:
        return False
