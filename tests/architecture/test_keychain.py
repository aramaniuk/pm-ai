"""Master-key custody: the port's contract, and the adapter's failure modes (AD-6).

Two halves, tested differently on purpose.

The **port** is exercised through a fake, because its contract is about what a
caller may rely on — a fetch after a delete raises rather than returning `None`,
a second store replaces rather than appending — and none of that needs a real
keychain. Story 1d's rule is explicit: no real Keychain access in the suite.

The **adapter** is exercised against the absence of `keyring`, which is the real
state of this environment, and against a substituted module for the cases a
missing package cannot produce. What matters there is that a missing package and
an unreachable keychain arrive as *different* errors, so a caller can tell "no
key" from "cannot ask" — because treating the second as the first opens an
encrypted store as though it were a fresh install.

Nothing here skips. A skipped key-custody test reads as coverage in a green run,
which is the failure mode the lazy import in `pm_ai/platform/keychain.py` exists
to prevent; a test file that then skipped anyway would give it all back.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field

import pytest

from pm_ai.platform.keychain import KEYCHAIN_SERVICE, MacOSKeychainAdapter
from pm_ai.ports import KeychainPort, KeychainUnavailable, KeyNotFound

NAME = "master"
SECRET = b"\x00\x01\xfe\xff not-ascii \x80"


# ── The port's contract, through a fake ──────────────────────────────────────


@dataclass
class FakeKeychain:
    """An in-memory KeychainPort, which is the only kind a test may use."""

    secrets: dict[str, bytes] = field(default_factory=dict)

    def store(self, name: str, secret: bytes) -> None:
        self.secrets[name] = secret

    def fetch(self, name: str) -> bytes:
        if name not in self.secrets:
            raise KeyNotFound(f"nothing stored under {name!r}")
        return self.secrets[name]

    def delete(self, name: str) -> None:
        if name not in self.secrets:
            raise KeyNotFound(f"nothing stored under {name!r}")
        del self.secrets[name]


def test_the_fake_satisfies_the_port():
    """If it does not, every row below is testing something else."""
    assert isinstance(FakeKeychain(), KeychainPort)
    assert isinstance(MacOSKeychainAdapter(), KeychainPort)


def test_a_stored_secret_comes_back_unchanged():
    keychain = FakeKeychain()
    keychain.store(NAME, SECRET)
    assert keychain.fetch(NAME) == SECRET


def test_a_deleted_secret_is_gone_and_says_so():
    keychain = FakeKeychain({NAME: SECRET})
    keychain.delete(NAME)
    with pytest.raises(KeyNotFound):
        keychain.fetch(NAME)


def test_a_second_store_replaces_rather_than_appends():
    keychain = FakeKeychain({NAME: SECRET})
    keychain.store(NAME, b"rotated")
    assert keychain.fetch(NAME) == b"rotated"


def test_a_name_never_stored_raises_rather_than_returning_none():
    """`None` would put the distinction on every caller, and one will forget.

    The caller that forgets opens an encrypted store with no key instead of
    refusing, which is a worse outcome than a traceback.
    """
    keychain = FakeKeychain()
    with pytest.raises(KeyNotFound):
        keychain.fetch("never-stored")


def test_deleting_something_absent_is_not_a_silent_success():
    keychain = FakeKeychain()
    with pytest.raises(KeyNotFound):
        keychain.delete("never-stored")


# ── The adapter's failure modes ──────────────────────────────────────────────


@pytest.mark.parametrize("call", ["store", "fetch", "delete"])
def test_a_missing_keyring_package_raises_at_call_time_naming_it(call):
    """The real state of this environment: `keyring` is in the `runtime` extra.

    At *call* time, not import time — which is the whole reason the import is
    inside the method. The message names the package, because "keychain
    unavailable" with no cause sends an operator to Keychain Access rather than
    to `uv sync`.
    """
    adapter = MacOSKeychainAdapter()
    arguments = {"store": (NAME, SECRET), "fetch": (NAME,), "delete": (NAME,)}[call]

    with pytest.raises(KeychainUnavailable) as refusal:
        getattr(adapter, call)(*arguments)

    assert "keyring" in str(refusal.value)
    assert "runtime" in str(refusal.value), "the message must name the repair"


def test_the_module_imports_with_the_runtime_extra_absent():
    """A module-level import would have made this file a wall of skips."""
    import importlib

    assert importlib.import_module("pm_ai.platform.keychain") is not None


def _install_fake_keyring(monkeypatch, *, behaviour):
    """Substitute `keyring` and `keyring.errors` for cases absence cannot produce.

    A real unreachable keychain needs `keyring` installed and failing, which this
    environment cannot offer — and installing it to find out would be a real
    Keychain access, which story 1d forbids.
    """

    class KeyringError(Exception):
        pass

    errors = types.ModuleType("keyring.errors")
    errors.KeyringError = KeyringError  # type: ignore[attr-defined]
    module = types.ModuleType("keyring")
    module.errors = errors  # type: ignore[attr-defined]
    behaviour(module, KeyringError)
    monkeypatch.setitem(sys.modules, "keyring", module)
    monkeypatch.setitem(sys.modules, "keyring.errors", errors)
    return KeyringError


def test_an_unreachable_keychain_is_distinguishable_from_an_absent_key(monkeypatch):
    """The distinction the whole port rests on.

    `KeyNotFound` is an ordinary first-run state. `KeychainUnavailable` means the
    daemon cannot decrypt anything. A caller told the first when the second is
    true behaves as though this were a fresh install — and writes a new key over
    a store it could still have opened.
    """

    def unreachable(module, KeyringError):
        def boom(*_args, **_kwargs):
            raise KeyringError("the keychain is locked")

        module.get_password = boom
        module.set_password = boom
        module.delete_password = boom

    _install_fake_keyring(monkeypatch, behaviour=unreachable)
    adapter = MacOSKeychainAdapter()

    with pytest.raises(KeychainUnavailable):
        adapter.fetch(NAME)
    with pytest.raises(KeychainUnavailable):
        adapter.store(NAME, SECRET)

    assert not issubclass(KeychainUnavailable, KeyNotFound)
    assert not issubclass(KeyNotFound, KeychainUnavailable), (
        "the two must be independent, or `except` on one silently catches the other"
    )


def test_the_adapter_round_trips_arbitrary_bytes_through_a_string_backend(monkeypatch):
    """`keyring` stores `str`; a master key is bytes. Base64 is why that is safe.

    Asserted against a byte string that is not valid UTF-8, because a text codec
    would round-trip most keys and corrupt a minority — a defect that appears on
    one machine in a hundred and looks like a corrupted store.
    """
    stored: dict[tuple[str, str], str] = {}

    def working(module, KeyringError):
        module.set_password = lambda s, a, v: stored.__setitem__((s, a), v)
        module.get_password = lambda s, a: stored.get((s, a))
        module.delete_password = lambda s, a: stored.pop((s, a))

    _install_fake_keyring(monkeypatch, behaviour=working)
    adapter = MacOSKeychainAdapter()

    adapter.store(NAME, SECRET)
    assert adapter.fetch(NAME) == SECRET
    assert stored[(KEYCHAIN_SERVICE, NAME)] != SECRET.decode("latin-1"), (
        "the secret reached the backend unencoded, so a non-UTF-8 key would break"
    )

    adapter.delete(NAME)
    with pytest.raises(KeyNotFound):
        adapter.fetch(NAME)


def test_the_secret_is_addressed_under_one_service_name(monkeypatch):
    """One service, so uninstalling is deleting one service's entries.

    Also the half of the naming scheme a Linux Secret Service adapter has to
    mirror: fixed service, caller's name as the account.
    """
    seen: list[tuple[str, str]] = []

    def recording(module, KeyringError):
        module.set_password = lambda s, a, v: seen.append((s, a))
        module.get_password = lambda s, a: None
        module.delete_password = lambda s, a: None

    _install_fake_keyring(monkeypatch, behaviour=recording)

    MacOSKeychainAdapter().store(NAME, SECRET)

    assert seen == [(KEYCHAIN_SERVICE, NAME)]


@pytest.mark.parametrize(
    ("still_there", "expected"),
    [(False, KeyNotFound), (True, KeychainUnavailable)],
    ids=["nothing-to-delete", "delete-genuinely-failed"],
)
def test_a_failed_delete_is_told_apart_by_whether_the_secret_survived(
    monkeypatch, still_there, expected
):
    """The adapter's branchiest path, and the one absence cannot reach.

    `keyring` raises the same `KeyringError` subclass whether the delete failed or
    there was simply nothing to delete, so the two arrive indistinguishable and
    have to be separated by asking whether the secret is still there. Getting it
    backwards means either a locked keychain reported as "already gone" — the
    operator stops looking — or a clean no-op reported as a keychain fault.
    """

    def failing_delete(module, KeyringError):
        def refuse(*_args, **_kwargs):
            raise KeyringError("delete refused")

        module.delete_password = refuse
        module.get_password = lambda s, a: "cHJlc2VudA==" if still_there else None
        module.set_password = lambda s, a, v: None

    _install_fake_keyring(monkeypatch, behaviour=failing_delete)

    with pytest.raises(expected):
        MacOSKeychainAdapter().delete(NAME)


def test_a_delete_that_cannot_even_check_reports_unreachable(monkeypatch):
    """When the follow-up question also fails, fail closed on "may still be there".

    Reporting `KeyNotFound` here would tell the operator the secret is gone on the
    strength of two failed calls.
    """

    def everything_fails(module, KeyringError):
        def refuse(*_args, **_kwargs):
            raise KeyringError("the keychain is locked")

        module.delete_password = refuse
        module.get_password = refuse
        module.set_password = refuse

    _install_fake_keyring(monkeypatch, behaviour=everything_fails)

    with pytest.raises(KeychainUnavailable):
        MacOSKeychainAdapter().delete(NAME)
