"""Story 8j — a refreshed sign-in survives a restart, one test per matrix row.

Every row composes through `build()` against a real temporary root, so the
sealed credentials file, its encryption and its claim are the real ones. Only
Microsoft is faked: `Provider` keeps one live refresh token at a time and
retires the old one when it issues a new one, which is the behaviour that made
an in-memory rotation a forced re-sign-in on the next run.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pm_ai.app.wiring import SealedRefreshTokenStore, build
from pm_ai.connectors.graph import CLIENT_ID_KEY, REACH_BACK_KEY, WIDTH_KEY
from pm_ai.connectors.graph.auth import (
    GRAPH_RESOURCE_SCOPES,
    CustodyFailed,
    SealedCredential,
    StoreBusy,
)
from pm_ai.core.connector_enrolment import enrol_connector, stored_credentials
from pm_ai.domain.health import Health
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.ports import KeyAlreadyEnrolled, KeyNotFound

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)
APPLICATION = DataScope(ScopeKind.APPLICATION)
INSTANCE = "graph:work"
CLIENT_ID = "00000000-0000-0000-0000-000000000008"
ACCOUNT = "an-object-id.a-tenant-id"
ENROLLED = "the-refresh-token-sealed-at-enrolment"
ROTATED = "the-refresh-token-microsoft-issued-in-its-place"
GITLAB_SECRET = "glpat-not-a-real-token-0123456789"
KEY = b"K" * 32
GRANTED = " ".join(f"https://graph.microsoft.com/{s}" for s in sorted(GRAPH_RESOURCE_SCOPES))


class _Keychain:
    """One master key shared by every composition in a test, as on one machine."""

    def __init__(self) -> None:
        self._secret: bytes | None = KEY

    def store(self, name: str, secret: bytes) -> None:
        self._secret = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        if self._secret is not None:
            raise KeyAlreadyEnrolled(name)
        self._secret = secret

    def fetch(self, name: str) -> bytes:
        if self._secret is None:
            raise KeyNotFound(name)
        return self._secret

    def delete(self, name: str) -> None:
        self._secret = None


class Provider:
    """Microsoft's token endpoint, as far as a refresh goes.

    One refresh token is live at a time. Redeeming it returns `issue` when one
    is set — rotating, and retiring the token redeemed — or the same token
    back. `during` runs once, inside the first redemption, which is how a test
    puts another run's work between this run's read and its write-back.
    """

    def __init__(self, live: str, *, issue: str | None = None, during=None) -> None:
        self.live = live
        self.issue = issue
        self.during = during
        self.asked: list[str] = []

    def acquire_token_by_refresh_token(self, refresh_token, scopes):
        self.asked.append(refresh_token)
        if self.during is not None:
            hook, self.during = self.during, None
            hook()
        if refresh_token != self.live:
            return {"error": "invalid_grant", "error_description": "AADSTS70000: retired"}
        issued = self.issue if self.issue is not None else refresh_token
        self.live, self.issue = issued, None
        return {
            "access_token": "an-access-token",
            "refresh_token": issued,
            "expires_in": 3600,
            "scope": GRANTED,
            "token_type": "Bearer",
        }

    def acquire_token_silent(self, scopes, account, force_refresh=False):
        return None

    def get_accounts(self, username=None):
        return []


def _sealed(token: str) -> str:
    return SealedCredential(refresh_token=token, home_account_id=ACCOUNT).encode()


def _held_token(storage) -> str:
    return SealedCredential.decode(stored_credentials(storage)[INSTANCE]["credential"]).refresh_token


def _sealed_file(root: Path) -> Path:
    return root / ".pm-ai" / "private" / "config.json"


def _enrol(root: Path, keychain: _Keychain, *, also_gitlab: bool = False):
    """Seal a Graph credential and write its row, as `pm-ai connector add` leaves them."""
    daemon = build(root, "alpha", now=lambda: NOW, keychain=keychain)
    storage = daemon.storage
    if also_gitlab:
        enrol_connector(
            storage, system="gitlab", instance="gitlab:other",
            credential=GITLAB_SECRET, probe=lambda system, credential: "accepted",
        )
    enrol_connector(
        storage, system="graph", instance=INSTANCE,
        credential=_sealed(ENROLLED), probe=lambda system, credential: "accepted",
    )
    storage.write_artifact(
        json.dumps(
            {
                "instance": INSTANCE,
                "system": "graph",
                "enabled": True,
                CLIENT_ID_KEY: CLIENT_ID,
                WIDTH_KEY: int(timedelta(hours=24).total_seconds() // 60),
                REACH_BACK_KEY: int(timedelta(days=7).total_seconds() // 60),
            }
        ).encode("utf-8"),
        scope=APPLICATION,
        artifact="connectors/",
        name=f"{INSTANCE}.json",
    )
    return storage


def _compose(root: Path, keychain: _Keychain, provider: Provider, *, sleep=None):
    """One `pm-ai` run: a fresh composition, talking to `provider`."""
    daemon = build(root, "alpha", now=lambda: NOW, keychain=keychain)
    connector = daemon.connectors[INSTANCE]
    connector.auth.client_factory = lambda client_id, authority: provider
    if sleep is not None:
        connector.auth.store.sleep = sleep
    return daemon, connector


# ── Rotation, then a new run ─────────────────────────────────────────────────


def test_a_token_rotated_in_one_run_is_the_one_the_next_run_uses(tmp_path):
    keychain = _Keychain()
    _enrol(tmp_path, keychain)
    provider = Provider(ENROLLED, issue=ROTATED)

    _, first = _compose(tmp_path, keychain, provider)
    assert first.check_health().health is Health.OK

    daemon, second = _compose(tmp_path, keychain, provider)
    assert isinstance(second.auth.store, SealedRefreshTokenStore)
    probe = second.check_health()

    assert probe.health is Health.OK, probe.detail
    assert provider.asked == [ENROLLED, ROTATED], (
        "the second run refreshed from the enrolled token, which Microsoft had "
        "already retired"
    )
    assert _held_token(daemon.storage) == ROTATED


# ── No rotation ──────────────────────────────────────────────────────────────


def test_a_token_microsoft_did_not_rotate_leaves_the_file_unwritten(tmp_path):
    keychain = _Keychain()
    _enrol(tmp_path, keychain)
    before = _sealed_file(tmp_path).read_bytes()

    _, connector = _compose(tmp_path, keychain, Provider(ENROLLED))
    assert connector.check_health().health is Health.OK

    assert _sealed_file(tmp_path).read_bytes() == before, (
        "the file was rewritten although nothing in it changed"
    )


# ── Other connectors present ─────────────────────────────────────────────────


def test_a_rotation_changes_nothing_else_in_the_file(tmp_path):
    keychain = _Keychain()
    storage = _enrol(tmp_path, keychain, also_gitlab=True)
    document = json.loads(storage.read_artifact(scope=APPLICATION, artifact="config.json"))
    document["unrelated"] = {"keep": [1, 2, 3]}
    storage.write_artifact(
        json.dumps(document).encode(), scope=APPLICATION, artifact="config.json"
    )

    daemon, connector = _compose(tmp_path, keychain, Provider(ENROLLED, issue=ROTATED))
    assert connector.check_health().health is Health.OK

    after = json.loads(daemon.storage.read_artifact(scope=APPLICATION, artifact="config.json"))
    graph = after["connectors"].pop(INSTANCE)
    document["connectors"].pop(INSTANCE)
    assert after == document, "a sibling credential or top-level key changed"
    assert graph["system"] == "graph"
    assert SealedCredential.decode(graph["credential"]) == SealedCredential(
        refresh_token=ROTATED, home_account_id=ACCOUNT
    )


# ── Two runs at once ─────────────────────────────────────────────────────────


def test_a_run_whose_token_another_run_rotated_retries_with_the_new_one(tmp_path):
    """B reads the enrolled token; A rotates it while B is talking to Microsoft.

    B's redemption is refused for a token A retired. B re-reads the file — live,
    not a copy from start-up — finds A's token, and retries with it.
    """
    keychain = _Keychain()
    _enrol(tmp_path, keychain)
    provider = Provider(ENROLLED, issue=ROTATED)
    _, run_a = _compose(tmp_path, keychain, provider)

    def a_rotates_first() -> None:
        assert run_a.check_health().health is Health.OK

    provider.during = a_rotates_first
    daemon, run_b = _compose(tmp_path, keychain, provider)
    probe = run_b.check_health()

    assert probe.health is Health.OK, probe.detail
    assert provider.asked == [ENROLLED, ENROLLED, ROTATED], (
        "B either reported stale against a good credential or never re-read"
    )
    assert _held_token(daemon.storage) == ROTATED


def test_a_run_started_after_another_rotated_reads_the_new_token(tmp_path):
    keychain = _Keychain()
    _enrol(tmp_path, keychain)
    provider = Provider(ENROLLED, issue=ROTATED)
    _, run_b = _compose(tmp_path, keychain, provider)
    _, run_a = _compose(tmp_path, keychain, provider)

    assert run_a.check_health().health is Health.OK
    assert run_b.check_health().health is Health.OK
    assert provider.asked == [ENROLLED, ROTATED]


# ── Credentials file busy ────────────────────────────────────────────────────

REDO = (
    "The stored sign-in has to be redone: Microsoft has retired the token this "
    "machine holds, and the replacement it issued was not saved. pm-ai has no "
    "command yet that redoes the sign-in for a connector that is already "
    "enrolled."
)
CUSTODY = (
    "That is custody, not auth — check the master key is enrolled and the "
    "keychain is unlocked. Whether the credential is good is unknown, which is "
    "not the same as bad."
)
LEFT_OVER = (
    " If no other pm-ai command is running, the claim file named above is left "
    "over from one that was stopped, and can be removed."
)


def test_a_busy_file_is_waited_for_and_then_used(tmp_path):
    keychain = _Keychain()
    storage = _enrol(tmp_path, keychain)
    other_run = ExitStack()
    other_run.enter_context(storage.exclusive(scope=APPLICATION, artifact="config.json"))
    waited: list[float] = []

    def sleep(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 3:
            other_run.close()

    _, connector = _compose(
        tmp_path, keychain, Provider(ENROLLED, issue=ROTATED), sleep=sleep
    )
    probe = connector.check_health()

    assert probe.health is Health.OK, probe.detail
    assert len(waited) == 3
    assert _held_token(storage) == ROTATED


def test_a_file_busy_past_the_wait_is_reported_busy_not_as_a_bug(tmp_path):
    keychain = _Keychain()
    storage = _enrol(tmp_path, keychain)
    waited: list[float] = []
    provider = Provider(ENROLLED, issue=ROTATED)

    with storage.exclusive(scope=APPLICATION, artifact="config.json"):
        _, connector = _compose(tmp_path, keychain, provider, sleep=waited.append)
        probe = connector.check_health()

    assert probe.health is Health.FAILING
    assert "busy" in probe.detail
    assert ".config.json.claim" in probe.detail, "the claim file is not named"
    assert "bug in pm-ai" not in probe.detail
    assert ".." not in probe.detail and ". ." not in probe.detail
    assert probe.remediation == (
        "Run this again once the other pm-ai command has finished. Nothing is "
        "wrong with the credential." + LEFT_OVER
    )
    assert sum(waited) <= 2.5, "waited longer than a couple of seconds"
    assert provider.asked == [], "Microsoft was asked without a token read under the claim"


def test_a_file_busy_at_write_back_says_the_stored_sign_in_must_be_redone(tmp_path):
    keychain = _Keychain()
    storage = _enrol(tmp_path, keychain)
    other_run = ExitStack()

    def another_run_takes_the_file() -> None:
        other_run.enter_context(storage.exclusive(scope=APPLICATION, artifact="config.json"))

    provider = Provider(ENROLLED, issue=ROTATED, during=another_run_takes_the_file)
    _, connector = _compose(tmp_path, keychain, provider, sleep=lambda seconds: None)
    with other_run:
        probe = connector.check_health()

    assert probe.health is Health.FAILING
    assert "busy" in probe.detail and "could not be saved" in probe.detail
    assert "bug in pm-ai" not in probe.detail
    assert "Sign in again" not in probe.detail, "the remedy is said twice"
    assert probe.remediation == REDO + LEFT_OVER
    assert ROTATED not in probe.detail and ENROLLED not in probe.detail
    assert _held_token(storage) == ENROLLED


def test_a_write_back_waits_for_a_busy_file_and_then_saves(tmp_path):
    keychain = _Keychain()
    storage = _enrol(tmp_path, keychain)
    other_run = ExitStack()
    waited: list[float] = []

    def another_run_takes_the_file() -> None:
        other_run.enter_context(storage.exclusive(scope=APPLICATION, artifact="config.json"))

    def sleep(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 2:
            other_run.close()

    provider = Provider(ENROLLED, issue=ROTATED, during=another_run_takes_the_file)
    _, connector = _compose(tmp_path, keychain, provider, sleep=sleep)
    probe = connector.check_health()

    assert probe.health is Health.OK, probe.detail
    assert len(waited) == 2, "the write-back did not wait for the file"
    assert _held_token(storage) == ROTATED


def test_without_a_rotation_a_busy_file_at_write_back_is_never_claimed(tmp_path):
    keychain = _Keychain()
    storage = _enrol(tmp_path, keychain)
    other_run = ExitStack()
    waited: list[float] = []

    def another_run_takes_the_file() -> None:
        other_run.enter_context(storage.exclusive(scope=APPLICATION, artifact="config.json"))

    provider = Provider(ENROLLED, during=another_run_takes_the_file)
    _, connector = _compose(tmp_path, keychain, provider, sleep=waited.append)
    before = _sealed_file(tmp_path).read_bytes()
    with other_run:
        probe = connector.check_health()

    assert probe.health is Health.OK, probe.detail
    assert waited == [], "the claim was waited for with nothing to write"
    assert _sealed_file(tmp_path).read_bytes() == before


def test_a_claim_that_cannot_be_created_is_custody_not_a_bug(tmp_path, monkeypatch):
    keychain = _Keychain()
    _enrol(tmp_path, keychain)
    daemon, connector = _compose(tmp_path, keychain, Provider(ENROLLED))

    def unclaimable(**kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(daemon.storage, "exclusive", unclaimable)
    probe = connector.check_health()

    assert probe.health is Health.FAILING
    assert "could not be claimed" in probe.detail
    assert "bug in pm-ai" not in probe.detail
    assert probe.remediation == CUSTODY


# ── Rotated token cannot be saved ────────────────────────────────────────────


def test_a_rotated_token_that_cannot_be_saved_says_the_stored_sign_in_must_be_redone(
    tmp_path, monkeypatch
):
    keychain = _Keychain()
    _enrol(tmp_path, keychain)
    daemon, connector = _compose(tmp_path, keychain, Provider(ENROLLED, issue=ROTATED))

    def disk_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(daemon.storage, "write_artifact", disk_full)
    probe = connector.check_health()

    assert probe.health is Health.FAILING
    assert "could not be sealed" in probe.detail and "already retired" in probe.detail
    assert "No space left on device" in probe.detail
    assert "enrol again" not in probe.detail
    assert probe.remediation == REDO
    assert ROTATED not in probe.detail and ENROLLED not in probe.detail


# ── Connector removed between read and save ──────────────────────────────────


def test_a_connector_removed_before_its_write_back_is_refused_and_named(tmp_path):
    keychain = _Keychain()
    storage = _enrol(tmp_path, keychain, also_gitlab=True)

    after_removal: list[bytes] = []

    def removed_meanwhile() -> None:
        document = json.loads(storage.read_artifact(scope=APPLICATION, artifact="config.json"))
        del document["connectors"][INSTANCE]
        storage.write_artifact(
            json.dumps(document).encode(), scope=APPLICATION, artifact="config.json"
        )
        after_removal.append(_sealed_file(tmp_path).read_bytes())

    _, connector = _compose(
        tmp_path, keychain, Provider(ENROLLED, issue=ROTATED, during=removed_meanwhile)
    )
    probe = connector.check_health()

    assert probe.health is Health.FAILING
    assert f"no entry for {INSTANCE!r}" in probe.detail
    assert "Nothing was written" in probe.detail
    assert "keychain" not in probe.detail
    assert probe.remediation == REDO
    assert ROTATED not in probe.detail
    held = stored_credentials(storage)
    assert INSTANCE not in held, "the removed entry was written back"
    assert held["gitlab:other"]["credential"] == GITLAB_SECRET
    assert [_sealed_file(tmp_path).read_bytes()] == after_removal, "the file was written"


# ── An entry with no recorded system ─────────────────────────────────────────


def test_an_entry_recording_no_system_is_read_and_written_back_alike(tmp_path):
    """Read and write-back apply one rule, so a readable token is replaceable."""
    keychain = _Keychain()
    storage = _enrol(tmp_path, keychain)
    document = json.loads(storage.read_artifact(scope=APPLICATION, artifact="config.json"))
    del document["connectors"][INSTANCE]["system"]
    storage.write_artifact(
        json.dumps(document).encode(), scope=APPLICATION, artifact="config.json"
    )

    _, connector = _compose(tmp_path, keychain, Provider(ENROLLED, issue=ROTATED))
    probe = connector.check_health()

    assert probe.health is Health.OK, probe.detail
    entry = stored_credentials(storage)[INSTANCE]
    assert "system" not in entry
    assert SealedCredential.decode(entry["credential"]).refresh_token == ROTATED


# ── A store that cannot be read ──────────────────────────────────────────────


def test_a_withheld_master_key_is_custody_on_the_probe_and_the_harvest_path(tmp_path):
    """Not ABSENT — something is enrolled — and not a bug in pm-ai."""
    keychain = _Keychain()
    _enrol(tmp_path, keychain)
    keyless = _Keychain()
    keyless.delete("any")

    _, connector = _compose(tmp_path, keyless, Provider(ENROLLED))
    probe = connector.check_health()

    assert probe.health is Health.FAILING
    assert "bug in pm-ai" not in probe.detail
    assert probe.remediation == CUSTODY
    with pytest.raises(CustodyFailed) as refused:
        connector.auth.access_token()
    assert refused.value.rotated is False
    assert "could not be read" in str(refused.value)


# ── The store on its own ─────────────────────────────────────────────────────


def _store(tmp_path, **overrides) -> tuple[SealedRefreshTokenStore, object]:
    keychain = _Keychain()
    storage = _enrol(tmp_path, keychain)
    store = SealedRefreshTokenStore(storage=storage, instance=INSTANCE, system="graph")
    for name, value in overrides.items():
        setattr(store, name, value)
    return store, storage


def test_a_write_outside_the_claim_is_refused_and_writes_nothing(tmp_path):
    store, _ = _store(tmp_path)
    before = _sealed_file(tmp_path).read_bytes()

    with pytest.raises(RuntimeError):
        store.write(_sealed(ROTATED))

    assert _sealed_file(tmp_path).read_bytes() == before


def test_a_second_thread_waits_for_the_first_and_then_writes(tmp_path):
    first_inside = threading.Event()
    first_may_leave = threading.Event()
    overlap: list[str] = []
    inside = threading.Lock()

    def sleep(seconds: float) -> None:
        first_may_leave.set()
        time.sleep(0.01)

    store, storage = _store(tmp_path, sleep=sleep)

    def first() -> None:
        with store.exclusive():
            if not inside.acquire(blocking=False):
                overlap.append("first")
            first_inside.set()
            first_may_leave.wait(5)
            store.write(_sealed("from-the-first-thread"))
            inside.release()

    def second() -> None:
        first_inside.wait(5)
        with store.exclusive():
            if not inside.acquire(blocking=False):
                overlap.append("second")
            store.write(_sealed("from-the-second-thread"))
            inside.release()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert overlap == [], "both threads were inside the claim at once"
    assert _held_token(storage) == "from-the-second-thread"


def test_a_second_thread_gives_up_as_busy_and_cannot_write_meanwhile(tmp_path):
    store, storage = _store(tmp_path, sleep=lambda seconds: None)
    refused: list[BaseException] = []

    def second() -> None:
        try:
            with store.exclusive():
                pass
        except StoreBusy as busy:
            refused.append(busy)
        try:
            store.write(_sealed("from-the-second-thread"))
        except RuntimeError as outside:
            refused.append(outside)

    with store.exclusive():
        thread = threading.Thread(target=second)
        thread.start()
        thread.join(10)

    assert [type(error) for error in refused] == [StoreBusy, RuntimeError]
    assert "another caller in this process" in str(refused[0])
    assert _held_token(storage) == ENROLLED


def test_no_attempts_still_tries_the_claim_once(tmp_path):
    waited: list[float] = []
    store, storage = _store(tmp_path, attempts=0, sleep=waited.append)

    with store.exclusive():
        store.write(_sealed(ROTATED))
    assert _held_token(storage) == ROTATED

    with storage.exclusive(scope=APPLICATION, artifact="config.json"):
        with pytest.raises(StoreBusy):
            with store.exclusive():
                pass
    assert waited == []


def test_reading_a_missing_file_is_none(tmp_path):
    keychain = _Keychain()
    storage = build(tmp_path, "alpha", now=lambda: NOW, keychain=keychain).storage
    store = SealedRefreshTokenStore(storage=storage, instance=INSTANCE, system="graph")

    assert not _sealed_file(tmp_path).exists()
    assert store.read() is None


def test_reading_an_entry_under_another_system_is_none(tmp_path):
    store, _ = _store(tmp_path)
    other = SealedRefreshTokenStore(storage=store.storage, instance=INSTANCE, system="gitlab")

    assert store.read() == _sealed(ENROLLED)
    assert other.read() is None
