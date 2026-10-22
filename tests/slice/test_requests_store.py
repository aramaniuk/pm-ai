"""Story 4r's half of the single writer: `requests`, its transactions, its migration.

The rows a job lives in (AD-20), the daemon's claimed-step transaction (AD-5),
the migration taken under `operational.db`'s own claim, and a claimed step
killed partway leaving nothing half-written.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from claim_fakes import InMemoryClaims, held_by_another_thread
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.storage_tiers import OPERATIONAL_DB
from pm_ai.platform.claims import FlockClaims
from pm_ai.platform.paths import ScopePaths
from pm_ai.platform.vcs import GitVcs
from pm_ai.ports import ArtifactBusy
from pm_ai.storage.crypto import PlaintextCrypto
from pm_ai.storage.service import (
    SCHEMA_VERSION,
    OperationalStoreUnavailable,
    SchemaVersionMoved,
    StorageService,
)

APPLICATION = DataScope(ScopeKind.APPLICATION)
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)


class _Recording(InMemoryClaims):
    def __init__(self) -> None:
        self.claimed: list[str] = []

    def exclusive(self, guarded: Path):
        self.claimed.append(guarded.name)
        return super().exclusive(guarded)


def _open(paths: ScopePaths, claims=None, *, clock=lambda: NOW) -> StorageService:
    return StorageService(
        paths,
        now=clock,
        vcs=GitVcs(),
        crypto=PlaintextCrypto(),
        claims=claims or InMemoryClaims(),
    )


def _store(paths: ScopePaths) -> Path:
    return paths.resolve(APPLICATION, OPERATIONAL_DB, create=True)


@pytest.fixture
def storage(tmp_path):
    opened = _open(ScopePaths.rooted(tmp_path))
    opened.adopt_daemon_connection()
    yield opened
    opened.close()


# ── The schema ───────────────────────────────────────────────────────────────


def test_a_version_1_store_without_requests_gains_it_under_its_own_claim_still_version_1(tmp_path):
    paths = ScopePaths.rooted(tmp_path)
    _open(paths).close()
    store = _store(paths)
    with sqlite3.connect(store) as db:
        db.execute("DROP TABLE requests")
    claims = _Recording()
    reopened = _open(paths, claims)
    reopened.close()
    assert claims.claimed == [f"{OPERATIONAL_DB}"], "migrated under operational.db's own claim"
    with sqlite3.connect(store) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
        version = db.execute("SELECT version FROM schema_version").fetchone()[0]
    assert "requests" in tables
    assert version == SCHEMA_VERSION == 2  # story 13a: migration 2


def test_a_prepared_store_opens_without_taking_any_claim(tmp_path):
    paths = ScopePaths.rooted(tmp_path)
    _open(paths).close()
    claims = _Recording()
    _open(paths, claims).close()
    assert claims.claimed == []


def test_a_new_store_is_prepared_under_the_claim(tmp_path):
    claims = _Recording()
    _open(ScopePaths.rooted(tmp_path), claims).close()
    assert claims.claimed == [OPERATIONAL_DB]


def test_a_claim_another_process_keeps_refuses_the_open_by_name(tmp_path, monkeypatch):
    from pm_ai.storage import service

    monkeypatch.setattr(service, "MIGRATION_CLAIM_ATTEMPTS", 3)
    monkeypatch.setattr(service, "MIGRATION_CLAIM_INTERVAL", 0.01)
    paths = ScopePaths.rooted(tmp_path)
    holder = _open(paths, FlockClaims())
    try:
        with sqlite3.connect(_store(paths)) as db:
            db.execute("DROP TABLE requests")
        with held_by_another_thread(
            lambda: holder.exclusive(scope=APPLICATION, artifact=OPERATIONAL_DB)
        ):
            with pytest.raises(OperationalStoreUnavailable, match="being prepared by another"):
                _open(paths, FlockClaims())
    finally:
        holder.close()


# ── Rows and their moves ─────────────────────────────────────────────────────


def test_a_job_row_is_written_queued_with_its_times(storage):
    row, created = storage.create_request(job_id="job_1", kind="dashboard", scope="personal")
    assert created
    assert (row.id, row.kind, row.scope, row.state, row.result) == (
        "job_1", "dashboard", "personal", "queued", None
    )
    assert row.created_at == row.updated_at == NOW.isoformat()
    assert storage.read_request("job_1") == row


def test_one_unfinished_job_per_kind_and_scope(storage):
    storage.create_request(job_id="job_1", kind="dashboard", scope="personal")
    again, created = storage.create_request(job_id="job_2", kind="dashboard", scope="personal")
    assert not created and again.id == "job_1"
    other, created = storage.create_request(job_id="job_3", kind="dashboard", scope="project:alpha")
    assert created and other.id == "job_3"
    assert storage.move_request("job_1", expected="queued", to="done", result="{}")
    fresh, created = storage.create_request(job_id="job_4", kind="dashboard", scope="personal")
    assert created and fresh.id == "job_4"
    assert storage.read_request("job_2") is None


def test_a_move_applies_only_to_the_state_it_expects(storage):
    storage.create_request(job_id="job_1", kind="dashboard", scope="personal")
    assert storage.move_request("job_1", expected="queued", to="running")
    assert not storage.move_request("job_1", expected="queued", to="running")
    failed = storage.fail_unfinished_requests(lambda row: json.dumps({"error": "stopped"}))
    assert [row.id for row in failed] == ["job_1"]
    assert not storage.move_request("job_1", expected="running", to="done", result='{"exit": 0}')
    row = storage.read_request("job_1")
    assert row.state == "failed" and json.loads(row.result) == {"error": "stopped"}


def test_old_rows_are_pruned_by_age(tmp_path):
    clock = [NOW - timedelta(days=8)]
    storage = _open(ScopePaths.rooted(tmp_path), clock=lambda: clock[0])
    storage.adopt_daemon_connection()
    try:
        storage.create_request(job_id="job_old", kind="dashboard", scope="personal")
        clock[0] = NOW - timedelta(days=6)
        storage.create_request(job_id="job_mid", kind="dashboard", scope="project:a")
        clock[0] = NOW
        assert storage.prune_requests(older_than=timedelta(days=7)) == 1
        assert storage.read_request("job_old") is None
        assert storage.read_request("job_mid") is not None
    finally:
        storage.close()


def test_a_busy_database_refuses_within_the_daemons_wait_and_writes_nothing(storage, tmp_path):
    other = sqlite3.connect(_store(ScopePaths.rooted(tmp_path)))
    other.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        with pytest.raises(OperationalStoreUnavailable):
            storage.create_request(job_id="job_1", kind="dashboard", scope="personal")
        assert time.monotonic() - started < 1.0, "the daemon waits at most 100 ms"
    finally:
        other.rollback()
        other.close()
    assert storage.read_request("job_1") is None
    assert not storage._db.in_transaction  # noqa: SLF001


def test_the_version_check_is_first_in_every_claimed_step(storage, tmp_path):
    with sqlite3.connect(_store(ScopePaths.rooted(tmp_path))) as db:
        db.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    with pytest.raises(SchemaVersionMoved) as moved:
        storage.create_request(job_id="job_1", kind="dashboard", scope="personal")
    assert moved.value.found == SCHEMA_VERSION + 1
    assert not storage._db.in_transaction  # noqa: SLF001
    with sqlite3.connect(_store(ScopePaths.rooted(tmp_path))) as db:
        assert db.execute("SELECT count(*) FROM requests").fetchone()[0] == 0


def test_an_unknown_job_reads_as_none(storage):
    assert storage.read_request("job_nope") is None


# ── Every claimed step is safe to kill partway ───────────────────────────────

_CHILD = textwrap.dedent(
    """
    import os, sys, time
    from datetime import datetime, timezone
    from pathlib import Path
    from pm_ai.domain.identity import DataScope, ScopeKind
    from pm_ai.platform.claims import FlockClaims
    from pm_ai.platform.paths import ScopePaths
    from pm_ai.platform.vcs import GitVcs
    from pm_ai.storage.crypto import PlaintextCrypto
    from pm_ai.storage.service import StorageService

    root, marker, what = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    storage = StorageService(
        ScopePaths.rooted(root),
        now=lambda: datetime.now(timezone.utc),
        vcs=GitVcs(),
        crypto=PlaintextCrypto(),
        claims=FlockClaims(),
    )
    storage.adopt_daemon_connection()
    personal = DataScope(ScopeKind.PERSONAL)
    application = DataScope(ScopeKind.APPLICATION)
    if what == "transaction":
        with storage.exclusive(scope=application, artifact="config.json"):
            with storage._transaction() as db:
                db.execute(
                    "INSERT INTO requests VALUES ('job_half', 'dashboard', 'personal', "
                    "'queued', NULL, 'a', 'a')"
                )
                marker.write_text("inside")
                time.sleep(60)
    else:
        real = os.replace
        def stalled(source, target):
            marker.write_text("staged")
            time.sleep(60)
            return real(source, target)
        os.replace = stalled
        storage.write_artifact(b"new dashboard\\n" * 1000, scope=personal, artifact="daily_dashboard.md")
    """
)


def _killed_partway(root: Path, what: str) -> None:
    marker = root / "marker"
    script = root / "child.py"
    script.write_text(_CHILD)
    child = subprocess.Popen(
        [sys.executable, str(script), str(root), str(marker), what],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while not marker.exists():
            if child.poll() is not None or time.monotonic() > deadline:
                out, err = child.communicate()
                pytest.fail(f"the child never reached the step: {err.decode()}")
            time.sleep(0.02)
        os.kill(child.pid, signal.SIGKILL)
        child.wait(10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_a_kill_inside_a_claimed_transaction_leaves_no_row_and_the_claim_released(tmp_path):
    paths = ScopePaths.rooted(tmp_path)
    _open(paths, FlockClaims()).close()
    _killed_partway(tmp_path, "transaction")
    reopened = _open(paths, FlockClaims())
    try:
        assert reopened.read_request("job_half") is None, "the half transaction rolled back"
        # The claim died with the process: it is free to take now.
        with reopened.exclusive(scope=APPLICATION, artifact="config.json"):
            pass
    finally:
        reopened.close()


def test_a_kill_while_a_file_is_published_leaves_it_whole_old_or_new(tmp_path):
    paths = ScopePaths.rooted(tmp_path)
    personal = DataScope(ScopeKind.PERSONAL)
    first = _open(paths, FlockClaims())
    target = first.write_artifact(b"old dashboard\n", scope=personal, artifact="daily_dashboard.md")
    first.close()
    _killed_partway(tmp_path, "publish")
    assert target.read_bytes() == b"old dashboard\n", "the file is whole: the old one"
