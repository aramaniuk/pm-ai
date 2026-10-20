"""The single writer's half of story 4e: the token file and a busy operational store.

Over a real `StorageService` in a throwaway root. The token rows are the
writer's — exclusive create, the validity check on read, owner-only mode; the
busy rows hold a real SQLite write lock from a second connection, the way a
thin client writing while the daemon runs would.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pm_ai.domain.api_token import MAX_TOKEN_FILE_BYTES, MIN_TOKEN_LENGTH, ApiTokenRefused
from pm_ai.domain.harvest import Cursor
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.platform.claims import FlockClaims
from pm_ai.platform.paths import ScopePaths
from pm_ai.storage.crypto import PlaintextCrypto
from pm_ai.storage import service as service_module
from pm_ai.storage.service import (
    OperationalStoreBusy,
    OperationalStoreUnavailable,
    StorageService,
)

APPLICATION = DataScope(ScopeKind.APPLICATION)
NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
TOKEN = "k" * 43


class _NoRepository:
    def working_tree(self, path):
        return None

    def repository_marker_above(self, path):
        return None

    def tracking(self, path, *, repository):  # pragma: no cover — never reached
        raise AssertionError("tracking asked with no working tree")


@pytest.fixture
def storage(tmp_path: Path):
    service = StorageService(
        ScopePaths.rooted(tmp_path),
        now=lambda: NOW,
        vcs=_NoRepository(),
        crypto=PlaintextCrypto(),
        claims=FlockClaims(),
    )
    yield service
    service.close()


def _token_file(tmp_path: Path) -> Path:
    return tmp_path / ".pm-ai" / "private" / "api_token"


# ── The token file ───────────────────────────────────────────────────────────


def test_no_file_reads_as_none(storage):
    assert storage.read_api_token() is None


def test_a_created_token_is_owner_only_and_reads_back(storage, tmp_path):
    storage.create_api_token(TOKEN)
    assert stat.S_IMODE(_token_file(tmp_path).stat().st_mode) == 0o600
    assert storage.read_api_token() == TOKEN


def test_the_create_never_replaces_a_file_already_there(storage, tmp_path):
    """Two daemons that both found no file cannot replace each other's token."""
    storage.create_api_token(TOKEN)
    with pytest.raises(FileExistsError):
        storage.create_api_token("z" * 43)
    assert _token_file(tmp_path).read_text() == TOKEN


@pytest.mark.parametrize(
    "content",
    [b"", b"   \n", b"x" * (MIN_TOKEN_LENGTH - 1), b"x" * 16, b"x" * 20 + b" " + b"x" * 20],
    ids=["empty", "whitespace", "31-characters", "16-characters", "inner-space"],
)
def test_an_unusable_file_is_refused_naming_it_and_left_unchanged(storage, tmp_path, content):
    target = _token_file(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    target.chmod(0o600)
    with pytest.raises(ApiTokenRefused) as refused:
        storage.read_api_token()
    assert str(target) in str(refused.value)
    assert target.read_bytes() == content


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o660])
def test_a_file_readable_by_group_or_others_is_refused(storage, tmp_path, mode):
    target = _token_file(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TOKEN)
    target.chmod(mode)
    with pytest.raises(ApiTokenRefused) as refused:
        storage.read_api_token()
    message = str(refused.value)
    assert str(target) in message
    assert TOKEN not in message
    assert stat.S_IMODE(target.stat().st_mode) == mode


def test_one_trailing_newline_is_tolerated(storage, tmp_path):
    target = _token_file(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TOKEN + "\n")
    target.chmod(0o600)
    assert storage.read_api_token() == TOKEN


# ── The operational store under two processes ────────────────────────────────


def _hold_write_lock(tmp_path: Path) -> sqlite3.Connection:
    """A second connection holding the write lock, as a thin client mid-write would."""
    other = sqlite3.connect(
        tmp_path / ".pm-ai" / "private" / "operational.db", check_same_thread=False
    )
    other.execute("BEGIN IMMEDIATE")
    return other


def test_pm_ai_sets_no_busy_wait_of_its_own_and_runs_in_wal(storage):
    """The standard library's five seconds, and readers that never wait for the writer."""
    assert storage._db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert storage._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_a_write_waits_for_a_busy_database_rather_than_failing(storage, tmp_path):
    other = _hold_write_lock(tmp_path)
    released = threading.Timer(0.5, other.rollback)
    started = time.monotonic()
    released.start()
    try:
        storage.save_cursor("gitlab:alpha", Cursor(b"x"), None, None)
    finally:
        released.join()
        other.close()
    assert time.monotonic() - started >= 0.4
    assert storage.load_cursor("gitlab:alpha") == Cursor(b"x")


def test_a_write_that_waits_the_whole_busy_wait_out_is_refused_by_name(storage, tmp_path):
    """Past the wait, a refusal naming the store — never a hang, never a bare error.

    The writer is wrapped to make the wait short, so the row costs a fraction
    of a second instead of five: `busy_timeout` is the same setting the
    standard library's five-second default sets, and the refusal comes from the
    same place either way.
    """
    storage._db.execute("PRAGMA busy_timeout = 100")
    other = _hold_write_lock(tmp_path)
    try:
        with pytest.raises(OperationalStoreBusy) as refused:
            storage.save_cursor("gitlab:alpha", Cursor(b"x"), None, None)
    finally:
        other.rollback()
        other.close()
    message = str(refused.value)
    assert "operational.db" in message
    # The wait actually configured, and the one measured.
    assert "busy wait configured: 100 ms" in message
    assert "after waiting" in message
    assert "another connection" in message
    assert "rolled back" in message
    assert isinstance(refused.value, sqlite3.OperationalError)
    assert isinstance(refused.value, OperationalStoreUnavailable)
    assert not storage._db.in_transaction


def test_a_reader_does_not_wait_for_a_writer(storage, tmp_path):
    other = _hold_write_lock(tmp_path)
    try:
        started = time.monotonic()
        assert storage.load_cursor("nobody") == Cursor()
        assert time.monotonic() - started < 1
    finally:
        other.rollback()
        other.close()


def test_close_closes_the_writer(tmp_path):
    service = StorageService(
        ScopePaths.rooted(tmp_path),
        now=lambda: NOW,
        vcs=_NoRepository(),
        crypto=PlaintextCrypto(),
        claims=FlockClaims(),
    )
    service.close()
    with pytest.raises(sqlite3.ProgrammingError):
        service.load_cursor("gitlab:alpha")
    service.close()  # idempotent
    assert os.path.exists(tmp_path / ".pm-ai" / "private" / "operational.db")


def test_opening_a_store_another_connection_is_writing_refuses_naming_it(tmp_path, monkeypatch):
    """An existing store that needs a write to open — here, its version stamp —
    meets a held write lock: refused as busy, naming the store, never "could
    not prepare". The connection's wait is shortened for the row, as above."""
    StorageService(
        ScopePaths.rooted(tmp_path), now=lambda: NOW, vcs=_NoRepository(),
        crypto=PlaintextCrypto(), claims=FlockClaims(),
    ).close()
    store = tmp_path / ".pm-ai" / "private" / "operational.db"
    other = sqlite3.connect(store)
    other.execute("DELETE FROM schema_version")
    other.commit()
    other.execute("BEGIN IMMEDIATE")
    real = sqlite3.connect
    monkeypatch.setattr(
        service_module.sqlite3, "connect", lambda *a, **k: real(*a, timeout=0.1, **k)
    )
    try:
        with pytest.raises(OperationalStoreBusy) as refused:
            StorageService(
                ScopePaths.rooted(tmp_path), now=lambda: NOW, vcs=_NoRepository(),
                crypto=PlaintextCrypto(), claims=FlockClaims(),
            )
    finally:
        other.rollback()
        other.close()
    assert str(store) in str(refused.value)


# ── Two openers of a brand-new store (4x) ────────────────────────────────────


def _fresh_store_held(tmp_path: Path) -> sqlite3.Connection:
    """A write lock held on a brand-new, still empty `operational.db`.

    The WAL switch is refused against it the way the race's loser is: the
    pragma reads the file before it asks for the write lock, and SQLite does
    not run the busy wait for a connection that already holds a read.
    """
    store = tmp_path / ".pm-ai" / "private" / "operational.db"
    store.parent.mkdir(parents=True)
    store.touch(mode=0o600)
    other = sqlite3.connect(store, isolation_level=None, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")
    return other


def _open(tmp_path: Path) -> StorageService:
    return StorageService(
        ScopePaths.rooted(tmp_path), now=lambda: NOW, vcs=_NoRepository(),
        crypto=PlaintextCrypto(), claims=FlockClaims(),
    )


def _switch_and_release(other: sqlite3.Connection) -> None:
    other.rollback()
    other.execute("PRAGMA journal_mode=WAL")


@pytest.mark.parametrize(
    "release", [sqlite3.Connection.rollback, _switch_and_release],
    ids=["released", "switched-to-wal-then-released"],
)
def test_the_second_first_open_waits_for_the_other_opener(tmp_path, release):
    """Opened once the other opener lets go — not refused at once (4x)."""
    other = _fresh_store_held(tmp_path)
    released = threading.Timer(0.3, release, args=(other,))
    started = time.monotonic()
    released.start()
    try:
        service = _open(tmp_path)
    finally:
        released.join()
        other.close()
    try:
        assert time.monotonic() - started >= 0.25
        assert service._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert service.load_cursor("nobody") == Cursor()
    finally:
        service.close()


@pytest.mark.parametrize("wait", [0.1, 0.3])
def test_a_wal_switch_held_past_the_busy_wait_is_refused_by_name(tmp_path, monkeypatch, wait):
    """Bounded by the connection's busy wait, shortened here (4x)."""
    other = _fresh_store_held(tmp_path)
    real = sqlite3.connect
    monkeypatch.setattr(
        service_module.sqlite3, "connect", lambda *a, **k: real(*a, timeout=wait, **k)
    )
    started = time.monotonic()
    try:
        with pytest.raises(OperationalStoreBusy) as refused:
            _open(tmp_path)
    finally:
        other.rollback()
        other.close()
    waited = time.monotonic() - started
    store = tmp_path / ".pm-ai" / "private" / "operational.db"
    message = str(refused.value)
    assert wait <= waited < wait + 1
    assert str(store) in message
    assert f"busy wait is {wait * 1000:.0f} ms" in message
    assert "was not switched to WAL" in message
    check = sqlite3.connect(store)
    try:
        assert check.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        check.close()


_OPEN_AT_THE_SIGNAL = """
import pathlib, sys, time
from datetime import datetime, timezone
from pm_ai.platform.claims import FlockClaims
from pm_ai.platform.paths import ScopePaths
from pm_ai.storage.crypto import PlaintextCrypto
from pm_ai.storage.service import StorageService

class NoRepository:
    def working_tree(self, path): return None
    def repository_marker_above(self, path): return None

root, go = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
print("ready", flush=True)
while not go.exists():
    pass
StorageService(
    ScopePaths.rooted(root), now=lambda: datetime.now(timezone.utc),
    vcs=NoRepository(), crypto=PlaintextCrypto(), claims=FlockClaims(),
).close()
"""


def test_two_processes_opening_a_brand_new_store_at_once_both_open_it(tmp_path):
    """The whole open, on real processes released together (4x).

    Each round is a fresh store. Before 4x one round in about 27 refused the
    second opener (measured on 5 runs of 60 rounds), so this is the end-to-end
    check that both open it and it is stamped once, current; the switch itself
    is pinned by the row below, which meets the race in nearly every round.
    """
    for round_ in range(20):
        root, go = tmp_path / f"round-{round_}", tmp_path / f"go-{round_}"
        openers = [
            subprocess.Popen(
                [sys.executable, "-c", _OPEN_AT_THE_SIGNAL, str(root), str(go)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for _ in range(2)
        ]
        try:
            for opener in openers:
                assert opener.stdout is not None
                assert opener.stdout.readline() == "ready\n"
            go.touch()
            results = [opener.communicate(timeout=30) for opener in openers]
        finally:
            for opener in openers:
                if opener.poll() is None:
                    opener.kill()
                    opener.communicate()
        assert [opener.returncode for opener in openers] == [0, 0], (round_, results)
        store = sqlite3.connect(root / ".pm-ai" / "private" / "operational.db")
        try:
            assert store.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert store.execute("SELECT version FROM schema_version").fetchall() == [
                (service_module.SCHEMA_VERSION,)
            ]
        finally:
            store.close()


_SWITCH_AT_THE_SIGNAL = """
import pathlib, sqlite3, sys
from pm_ai.storage.service import _OperationalConnection, _enter_wal

store, go = sys.argv[1], pathlib.Path(sys.argv[2])
db = sqlite3.connect(store, factory=_OperationalConnection)
print("ready", flush=True)
while not go.exists():
    pass
_enter_wal(db)
print(db.execute("PRAGMA journal_mode").fetchone()[0], flush=True)
"""


def test_two_processes_switching_a_brand_new_store_to_wal_at_once_both_succeed(tmp_path):
    """The switch alone, both connections open before the signal (4x).

    Measured with a plain `PRAGMA journal_mode=WAL` in place of `_enter_wal`:
    one of the two was refused busy at once in 297 rounds of 300.
    """
    for round_ in range(10):
        store, go = tmp_path / f"store-{round_}.db", tmp_path / f"go-{round_}"
        store.touch(mode=0o600)
        switchers = [
            subprocess.Popen(
                [sys.executable, "-c", _SWITCH_AT_THE_SIGNAL, str(store), str(go)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for _ in range(2)
        ]
        try:
            for switcher in switchers:
                assert switcher.stdout is not None
                assert switcher.stdout.readline() == "ready\n"
            go.touch()
            results = [switcher.communicate(timeout=30) for switcher in switchers]
        finally:
            for switcher in switchers:
                if switcher.poll() is None:
                    switcher.kill()
                    switcher.communicate()
        assert [switcher.returncode for switcher in switchers] == [0, 0], (round_, results)
        assert [out for out, _ in results] == ["wal\n", "wal\n"]


# ── The token file is a regular file of a bounded size ───────────────────────


def test_a_symlinked_token_file_is_refused_as_a_symlink(storage, tmp_path):
    real = tmp_path / "elsewhere"
    real.write_text(TOKEN)
    real.chmod(0o600)
    target = _token_file(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(real)
    with pytest.raises(ApiTokenRefused) as refused:
        storage.read_api_token()
    assert "symbolic link" in str(refused.value)
    assert str(target) in str(refused.value)


def test_a_fifo_at_the_token_path_is_refused_without_hanging(storage, tmp_path):
    target = _token_file(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(target, 0o600)
    with pytest.raises(ApiTokenRefused) as refused:
        storage.read_api_token()
    assert "not a regular file" in str(refused.value)
    assert str(target) in str(refused.value)


def test_a_directory_at_the_token_path_is_refused(storage, tmp_path):
    target = _token_file(tmp_path)
    target.mkdir(parents=True, mode=0o700)
    with pytest.raises(ApiTokenRefused) as refused:
        storage.read_api_token()
    assert "not a regular file" in str(refused.value)


def test_an_oversized_token_file_is_refused_unread(storage, tmp_path):
    target = _token_file(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x" * (MAX_TOKEN_FILE_BYTES + 1))
    target.chmod(0o600)
    with pytest.raises(ApiTokenRefused) as refused:
        storage.read_api_token()
    assert str(MAX_TOKEN_FILE_BYTES) in str(refused.value)


@pytest.mark.parametrize(
    "content",
    [b"x" * 20 + b"\x00" + b"x" * 20, b"x" * 20 + b"\r" + b"x" * 20, b"x" * 40 + b"\x7f", b"x" * 40 + b"\x1b"],
    ids=["nul", "cr", "del", "escape"],
)
def test_a_control_character_in_the_token_is_refused(storage, tmp_path, content):
    target = _token_file(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    target.chmod(0o600)
    with pytest.raises(ApiTokenRefused) as refused:
        storage.read_api_token()
    assert "control character" in str(refused.value)


def test_a_stale_snapshot_is_refused_at_once_and_says_it_did_not_wait(storage, tmp_path):
    """WAL refuses a read-then-write whose snapshot went stale without the busy wait."""
    import time as clock

    storage._db.execute("BEGIN")
    storage._db.execute("SELECT count(*) FROM cursors").fetchone()
    other = sqlite3.connect(tmp_path / ".pm-ai" / "private" / "operational.db")
    other.execute("INSERT INTO seen (natural_key) VALUES ('elsewhere')")
    other.commit()
    other.close()
    started = clock.monotonic()
    with pytest.raises(OperationalStoreBusy) as refused:
        storage._db.execute("INSERT INTO seen (natural_key) VALUES ('here')")
    assert clock.monotonic() - started < 1
    message = str(refused.value)
    assert "at once, without waiting" in message
    assert "after waiting" not in message
    assert "busy wait configured: 5000 ms" in message
