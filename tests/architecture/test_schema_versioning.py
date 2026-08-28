"""Forward-only migration of the one tier no rebuild reconstructs (AD-3, story 1i).

Tier 2 holds pending external writes, connector cursors, the executed-key ledger
and the dedup set. None of it is derivable from Markdown, so the obvious
implementation of a schema change — drop the tables and recreate them —
destroys exactly the state that cannot come back.

Every test here drives the real `StorageService` constructor against a real
SQLite file. A test that called `_migrate` directly would assert this file's
belief about when migrations run rather than the fact that they run before
anything else can read the schema.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from pm_ai.domain import DataScope, ScopeKind, TargetRef
from pm_ai.domain.storage_tiers import OPERATIONAL_DB
from pm_ai.platform.paths import ScopePaths
from pm_ai.platform.vcs import GitVcs
from pm_ai.storage.crypto import PlaintextCrypto
from pm_ai.storage.service import (
    MIGRATIONS,
    SCHEMA_VERSION,
    UNVERSIONED,
    SchemaVersionTooNew,
    StorageService,
)

NOW = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)
APPLICATION = DataScope(ScopeKind.APPLICATION)


def _open(paths: ScopePaths) -> StorageService:
    return StorageService(paths, now=lambda: NOW, vcs=GitVcs(), crypto=PlaintextCrypto())


def _store(paths: ScopePaths):
    return paths.resolve(APPLICATION, OPERATIONAL_DB, create=True)


def _version(path) -> int | None:
    """The stamp, or `None` when there is none.

    Two distinct ways to have no stamp, and both mean "unversioned era": the
    table is absent (a store from before this story) or present and empty (a
    store `_SCHEMA` created in an open that died before stamping).
    """
    db = sqlite3.connect(path)
    try:
        row = db.execute("SELECT version FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        db.close()
    return None if row is None else int(row[0])


def _legacy_store(path, *, with_settled_at: bool) -> None:
    """A store as the unversioned era left it: no `schema_version` table.

    `with_settled_at` is the whole reason version 0 is an era and not a shape —
    two stores can both be at 0 and differ, because nothing recorded which.
    """
    settled = ",\n            settled_at  TEXT" if with_settled_at else ""
    db = sqlite3.connect(path)
    db.executescript(
        f"""
        CREATE TABLE cursors (instance TEXT PRIMARY KEY, token BLOB NOT NULL);
        CREATE TABLE executed (
            key         TEXT PRIMARY KEY,
            lock_key    TEXT NOT NULL,
            external_id TEXT,
            state       TEXT NOT NULL,
            at          TEXT NOT NULL{settled}
        );
        CREATE TABLE seen (natural_key TEXT PRIMARY KEY);
        """
    )
    db.commit()
    db.close()


# ── Matrix ───────────────────────────────────────────────────────────────────


def test_a_new_store_is_stamped_current_and_migrates_nothing(tmp_path):
    """Row 1 — a file created moments ago by `_SCHEMA` is already current.

    Running migration 1 against it would be harmless today and wrong in
    principle: a migration is a step *between* known shapes, and there is no
    earlier shape here to step from.
    """
    paths = ScopePaths.rooted(tmp_path)
    _open(paths)
    assert _version(_store(paths)) == SCHEMA_VERSION


def test_a_store_one_version_behind_migrates_forward(tmp_path):
    """Row 2 — the unversioned era is version 0, and it has a step to take."""
    paths = ScopePaths.rooted(tmp_path)
    store = _store(paths)
    _legacy_store(store, with_settled_at=False)
    assert _version(store) is None, "the fixture must predate versioning"

    _open(paths)

    assert _version(store) == SCHEMA_VERSION
    db = sqlite3.connect(store)
    columns = {row[1] for row in db.execute("PRAGMA table_info(executed)")}
    db.close()
    assert "settled_at" in columns


def test_the_mutation_ledger_survives_the_migration(tmp_path):
    """Row 3 — the rows AD-3 says no rebuild can reconstruct.

    This is the property that makes drop-and-recreate unacceptable, so it is
    asserted against real pre-existing rows rather than against an empty store
    where every implementation looks correct.
    """
    paths = ScopePaths.rooted(tmp_path)
    store = _store(paths)
    _legacy_store(store, with_settled_at=False)
    db = sqlite3.connect(store)
    db.execute(
        "INSERT INTO executed (key, lock_key, external_id, state, at) "
        "VALUES ('idem_old', 'gitlab:alpha:issue:102', 'cmt_1', 'in_flight', ?)",
        (NOW.isoformat(),),
    )
    db.commit()
    db.close()

    _open(paths)

    db = sqlite3.connect(store)
    rows = db.execute("SELECT key, lock_key, external_id, state FROM executed").fetchall()
    db.close()
    assert rows == [("idem_old", "gitlab:alpha:issue:102", "cmt_1", "in_flight")]


def test_connector_cursors_survive_the_migration(tmp_path):
    """Row 4 — losing these resets harvest position and re-reads the world."""
    paths = ScopePaths.rooted(tmp_path)
    store = _store(paths)
    _legacy_store(store, with_settled_at=False)
    db = sqlite3.connect(store)
    db.execute("INSERT INTO cursors (instance, token) VALUES ('gitlab:alpha', ?)", (b"page-7",))
    db.commit()
    db.close()

    _open(paths)

    db = sqlite3.connect(store)
    assert db.execute("SELECT token FROM cursors WHERE instance = 'gitlab:alpha'").fetchone() == (
        b"page-7",
    )
    db.close()


def test_every_intervening_migration_applies_in_order_once_each(tmp_path):
    """Row 5 — with the real list, plus a synthetic chain the list cannot yet give.

    `MIGRATIONS` holds one entry today, so a test that only opened a version-0
    store would prove nothing about *ordering* or about *once each*. The runner
    is therefore driven against a temporary three-step chain, which is the only
    way to see it skip what is already applied and apply the rest in ascending
    order.
    """
    import pm_ai.storage.service as service

    applied: list[int] = []

    def _step(n: int):
        def run(db: sqlite3.Connection) -> None:
            applied.append(n)
            db.execute(f"CREATE TABLE step_{n} (x INTEGER)")

        return run

    paths = ScopePaths.rooted(tmp_path)
    store = _store(paths)
    _legacy_store(store, with_settled_at=True)

    original_migrations, original_version = service.MIGRATIONS, service.SCHEMA_VERSION
    try:
        service.MIGRATIONS = (
            (1, "one", _step(1)),
            (2, "two", _step(2)),
            (3, "three", _step(3)),
        )
        service.SCHEMA_VERSION = 3
        _open(paths)
        assert applied == [1, 2, 3], "migrations ran out of order, or skipped one"
        assert _version(store) == 3

        applied.clear()
        _open(paths)
        assert applied == [], "a second open re-ran a migration that was already applied"
    finally:
        service.MIGRATIONS, service.SCHEMA_VERSION = original_migrations, original_version


def test_a_store_stamped_newer_than_the_code_refuses_to_open(tmp_path):
    """Row 6 — refusing is the only response that cannot make things worse."""
    paths = ScopePaths.rooted(tmp_path)
    store = _store(paths)
    _open(paths)
    db = sqlite3.connect(store)
    db.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 5,))
    db.commit()
    db.close()
    before = store.read_bytes()

    with pytest.raises(SchemaVersionTooNew) as raised:
        _open(paths)

    message = str(raised.value)
    assert str(SCHEMA_VERSION + 5) in message and str(SCHEMA_VERSION) in message, (
        "the refusal must name both versions, or an operator cannot tell whether "
        "to upgrade pm-ai or restore a backup"
    )
    assert store.read_bytes() == before, "a refused open modified the file"


def test_a_second_open_at_the_current_version_changes_nothing(tmp_path):
    """Row 7 — running the sequence twice is a no-op, which is what makes it safe."""
    paths = ScopePaths.rooted(tmp_path)
    store = _store(paths)
    storage = _open(paths)
    storage.record_execution("idem_1", TargetRef.parse("gitlab:alpha:issue:102"), "cmt_1")

    db = sqlite3.connect(store)
    before = db.execute("SELECT key, state FROM executed").fetchall()
    db.close()

    _open(paths)

    db = sqlite3.connect(store)
    assert db.execute("SELECT key, state FROM executed").fetchall() == before
    db.close()
    assert _version(store) == SCHEMA_VERSION


def test_a_migration_that_fails_partway_leaves_the_version_alone(tmp_path):
    """Row 8 — a half-migrated Tier 2 is unrecoverable, so failure must be atomic.

    The step below creates a table and *then* raises, so a runner without a
    savepoint would leave both the half-change and, worse, a version stamp
    claiming the step succeeded.
    """
    import pm_ai.storage.service as service

    def _explodes(db: sqlite3.Connection) -> None:
        db.execute("CREATE TABLE half_done (x INTEGER)")
        raise sqlite3.OperationalError("migration blew up halfway")

    paths = ScopePaths.rooted(tmp_path)
    store = _store(paths)
    _legacy_store(store, with_settled_at=True)

    original_migrations, original_version = service.MIGRATIONS, service.SCHEMA_VERSION
    try:
        service.MIGRATIONS = ((1, "explodes", _explodes),)
        service.SCHEMA_VERSION = 1
        with pytest.raises(Exception):
            _open(paths)
    finally:
        service.MIGRATIONS, service.SCHEMA_VERSION = original_migrations, original_version

    assert _version(store) == UNVERSIONED, "the failed step stamped its version anyway"
    db = sqlite3.connect(store)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    db.close()
    assert "half_done" not in tables, "the failed step left its partial change behind"


# ── Properties of the list itself ────────────────────────────────────────────


def test_the_migration_list_is_ascending_and_contiguous_from_one():
    """A gap or a repeat means a store can stamp a version no step produced."""
    versions = [version for version, _, _ in MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1)), (
        f"MIGRATIONS must be ascending and contiguous from 1; got {versions}"
    )


def test_the_declared_version_matches_the_last_migration():
    """Bumped together or not at all.

    A version ahead of the list stamps a store that was never changed; a list
    ahead of the version runs its tail on every open, forever.
    """
    assert SCHEMA_VERSION == max(version for version, _, _ in MIGRATIONS)
    assert UNVERSIONED == 0
