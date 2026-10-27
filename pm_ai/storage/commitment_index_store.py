"""`commitment_index.db`'s SQLite: the schema, the usable rules, the steps (story 15b).

Plain SQLite from the standard library, not encrypted (Tier 3 is "explicitly
not encrypted", `storage-contract.md`), in WAL mode. `StorageService` creates
the file at `0600` before the first write opens it, and calls these under
`exclusive` on the index; nothing else in `pm_ai` opens it.

One connection per step: each write opens its own connection to the path and
closes it when the step ends, and each read likewise, so a file dropped and
created again by another store or process is always the file at the path.

**Unusable** (`CommitmentIndexUnusable`) means only: `sqlite3.DatabaseError`
other than `sqlite3.OperationalError` (not a database, or corrupt); some of the
index's tables but not all; an `index_format` table with no row, several, or
a value that is not an integer; another `index_format` or `fold_version`. A
file with no tables at all gets the schema and the format row at the next
write. `sqlite3.OperationalError` (busy, locked, full, cannot open) passes
through as itself: nothing is dropped for it.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator, Mapping, Sequence

from pathlib import Path

from pm_ai.domain.commitment_index import INDEX_COLUMNS, IndexRow, ReplacedRows, ScopeChecksums
from pm_ai.ports import CommitmentIndexUnusable

__all__ = [
    "COMMITMENT_INDEX",
    "INDEX_COLUMNS",
    "INDEX_TABLES",
    "INDEX_WAIT_SECONDS",
    "checked_rows",
    "checksums",
    "remove_scopes",
    "replace_rows",
    "rows",
]

COMMITMENT_INDEX = "commitment_index.db"
"""The index's artifact key in the application scope. The drop guards and removes
the path this key resolves to, so the guarded key is always the removed file."""

INDEX_TABLES = ("commitments", "index_format")

INDEX_WAIT_SECONDS = 1.0
"""SQLite's busy wait on the index: within AD-5's bound for a writer call."""

_TYPES: Mapping[str, str] = {
    "due_estimated": "INTEGER NOT NULL",
    "due_by_pm": "INTEGER NOT NULL",
    "malformed_named": "INTEGER NOT NULL",
    "state_at": "TEXT",
    "due_date": "TEXT",
    "meeting_id": "TEXT",
    "meeting_start": "TEXT",
    "day": "TEXT",
    "day_from": "TEXT",
}

_SCHEMA = (
    "CREATE TABLE commitments ("
    + ", ".join(f"{column} {_TYPES.get(column, 'TEXT NOT NULL')}" for column in INDEX_COLUMNS)
    + ", PRIMARY KEY (scope, commitment_id))",
    "CREATE INDEX commitments_open_by_due ON commitments (scope, state, due_date)",
    "CREATE TABLE index_format (index_format INTEGER NOT NULL, fold_version INTEGER NOT NULL)",
)

_VALUES = tuple(column for column in INDEX_COLUMNS if column != "source_md5")
"""Every column but the checksum: what decides whether a row changed."""

_SELECT = ", ".join(INDEX_COLUMNS)
_INSERT = (
    f"INSERT OR REPLACE INTO commitments ({_SELECT}) "
    f"VALUES ({', '.join('?' for _ in INDEX_COLUMNS)})"
)


def _unusable(error: sqlite3.DatabaseError) -> CommitmentIndexUnusable:
    return CommitmentIndexUnusable(
        "not_a_database",
        f"commitment_index.db is not a database pm-ai can read ({type(error).__name__})",
    )


@contextlib.contextmanager
def _translated() -> Iterator[None]:
    """`sqlite3.DatabaseError` other than `OperationalError` as `CommitmentIndexUnusable`."""
    try:
        yield
    except sqlite3.OperationalError:
        raise
    except sqlite3.DatabaseError as error:
        raise _unusable(error) from error


def _tables(db: sqlite3.Connection) -> set[str]:
    return {
        name
        for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _usable(db: sqlite3.Connection, formats: tuple[int, int], *, writing: bool) -> bool:
    """Whether the file holds the index; `False` for a file with no table at all.

    A write gives a file with no table the schema and the format row. Raises
    `CommitmentIndexUnusable` for a partial schema or another format.
    """
    present = _tables(db) & set(INDEX_TABLES)
    if not present:
        if not writing:
            return False
        for statement in _SCHEMA:
            db.execute(statement)
        db.execute("INSERT INTO index_format (index_format, fold_version) VALUES (?, ?)", formats)
        return True
    if present != set(INDEX_TABLES):
        raise CommitmentIndexUnusable(
            "partial", "commitment_index.db has some of the index's tables but not all"
        )
    columns = tuple(found[1] for found in db.execute("PRAGMA table_info(commitments)"))
    if columns != INDEX_COLUMNS:
        # Columns changed without a new `INDEX_FORMAT`: another format all the same.
        raise CommitmentIndexUnusable(
            "other_format", "commitment_index.db's columns are not the ones this pm-ai reads"
        )
    found = db.execute("SELECT index_format, fold_version FROM index_format").fetchall()
    if len(found) != 1 or not all(type(value) is int for value in found[0]):
        raise CommitmentIndexUnusable(
            "format_row", "commitment_index.db's index_format row is missing, repeated or not a number"
        )
    if tuple(found[0]) != formats:
        raise CommitmentIndexUnusable(
            "other_format",
            f"commitment_index.db holds format {tuple(found[0])}, and this pm-ai reads {formats}",
        )
    return True


@contextlib.contextmanager
def _writing(path: Path) -> Iterator[sqlite3.Connection]:
    """A connection of this step's own, in WAL mode, closed when the step ends."""
    db = sqlite3.connect(path, timeout=INDEX_WAIT_SECONDS, isolation_level=None)
    try:
        with _translated():
            db.execute("PRAGMA journal_mode=WAL")
            # Tier 3 is rebuilt from the Markdown, so a commit lost to a power
            # cut costs one derivation; WAL keeps the file consistent either way.
            db.execute("PRAGMA synchronous=NORMAL")
            yield db
    finally:
        db.close()


@contextlib.contextmanager
def _reading(path: Path) -> Iterator[sqlite3.Connection | None]:
    """A read-only connection of its own, or `None` for an absent file: nothing is created."""
    if not path.exists():
        yield None
        return
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=INDEX_WAIT_SECONDS)
    try:
        with _translated():
            yield db
    finally:
        db.close()


@contextlib.contextmanager
def _transaction(db: sqlite3.Connection) -> Iterator[None]:
    """One SQLite transaction: committed when the block ends, rolled back when it raises."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    if db.in_transaction:
        db.execute("COMMIT")


def checked_rows(scope: str, new: Sequence[IndexRow]) -> set[str]:
    """The rows' one checksum (or none); `ValueError` for another scope's or mixed rows."""
    md5s = {row.source_md5 for row in new}
    if len(md5s) > 1 or any(row.scope != scope for row in new):
        raise ValueError("one derivation's rows are one scope's, under one checksum")
    return md5s


def replace_rows(
    path: Path,
    scope: str,
    new: Sequence[IndexRow],
    expected_md5s: frozenset[str],
    formats: tuple[int, int],
) -> ReplacedRows:
    """The conditional, difference-only replace of one scope's rows, in one transaction.

    1. The format row is checked (the schema made for a file with no table).
    2. The scope's stored checksums are read again; when they are not
       `expected_md5s`, nothing is written and the answer is `changed`.
    3. Rows whose commitment is gone are deleted; rows that are new or whose
       values changed are inserted or replaced; the others are left.
    4. Every remaining row of the scope takes the new checksum, in one statement.
    """
    md5s = checked_rows(scope, new)
    with _writing(path) as db, _transaction(db):
        _usable(db, formats, writing=True)
        stored = {
            md5
            for (md5,) in db.execute(
                "SELECT DISTINCT source_md5 FROM commitments WHERE scope = ?", (scope,)
            )
        }
        if stored != expected_md5s:
            db.execute("ROLLBACK")
            return ReplacedRows(changed=True)
        at_root = _VALUES.index("commitment_id")
        existing = {
            found[at_root]: tuple(found)
            for found in db.execute(
                f"SELECT {', '.join(_VALUES)} FROM commitments WHERE scope = ?", (scope,)
            )
        }
        wanted = {row.commitment_id: row for row in new}
        gone = [(scope, root) for root in existing if root not in wanted]
        changed = [
            tuple(getattr(row, column) for column in INDEX_COLUMNS)
            for root, row in wanted.items()
            if existing.get(root) != tuple(getattr(row, column) for column in _VALUES)
        ]
        db.executemany("DELETE FROM commitments WHERE scope = ? AND commitment_id = ?", gone)
        db.executemany(_INSERT, changed)
        if md5s:
            db.execute(
                "UPDATE commitments SET source_md5 = ? WHERE scope = ? AND source_md5 != ?",
                (next(iter(md5s)), scope, next(iter(md5s))),
            )
        return ReplacedRows(written=len(changed), removed=len(gone))


def remove_scopes(path: Path, scopes: Sequence[str], formats: tuple[int, int]) -> int:
    """Delete every row of `scopes` in one transaction; how many went. Absent is none."""
    if not path.exists():
        return 0
    with _writing(path) as db, _transaction(db):
        if not _usable(db, formats, writing=False):
            return 0
        removed = 0
        for scope in scopes:
            removed += db.execute("DELETE FROM commitments WHERE scope = ?", (scope,)).rowcount
        return removed


def rows(path: Path, scope: str | None, formats: tuple[int, int]) -> tuple[IndexRow, ...]:
    """The rows, of one scope or all, ordered by `(scope, commitment_id)`."""
    with _reading(path) as db:
        if db is None or not _usable(db, formats, writing=False):
            return ()
        where, parameters = ("WHERE scope = ?", (scope,)) if scope is not None else ("", ())
        return tuple(
            IndexRow(*found)
            for found in db.execute(
                f"SELECT {_SELECT} FROM commitments {where} ORDER BY scope, commitment_id",
                parameters,
            )
        )


def checksums(path: Path, formats: tuple[int, int]) -> dict[str, ScopeChecksums]:
    """Per scope, the set of stored checksums and the row count."""
    with _reading(path) as db:
        if db is None or not _usable(db, formats, writing=False):
            return {}
        found: dict[str, tuple[set[str], int]] = {}
        for scope, md5, count in db.execute(
            "SELECT scope, source_md5, COUNT(*) FROM commitments GROUP BY scope, source_md5"
        ):
            md5s, total = found.get(scope, (set(), 0))
            md5s.add(md5)
            found[scope] = (md5s, total + count)
        return {scope: ScopeChecksums(frozenset(md5s), total) for scope, (md5s, total) in found.items()}
