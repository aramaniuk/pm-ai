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

from pm_ai.platform import keychain as keychain_module
from pm_ai.platform.keychain import KEYCHAIN_SERVICE, MacOSKeychainAdapter
from pm_ai.ports import (
    KeyAlreadyEnrolled,
    KeychainPort,
    KeychainUnavailable,
    KeyNotFound,
)

NAME = "master"
SECRET = b"\x00\x01\xfe\xff not-ascii \x80"


# ── The port's contract, through a fake ──────────────────────────────────────


@dataclass
class FakeKeychain:
    """An in-memory KeychainPort, which is the only kind a test may use."""

    secrets: dict[str, bytes] = field(default_factory=dict)

    def store(self, name: str, secret: bytes) -> None:
        self.secrets[name] = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        if name in self.secrets:
            raise KeyAlreadyEnrolled(f"{name!r} already holds a secret")
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
def test_a_missing_keyring_package_raises_at_call_time_naming_it(call, monkeypatch):
    """Absence is *simulated*, not assumed from the environment.

    This test read the real state of the interpreter until 2026-08-23, when a
    `uv add --optional runtime` installed the whole runtime extra and keyring
    with it — and the test failed for a reason that had nothing to do with the
    code. An assertion about which packages happen to be installed is an
    assertion about somebody's last command.

    What matters is unchanged: the failure arrives at *call* time rather than
    import time, which is the whole reason the import is inside the method, and
    the message names the package — "keychain unavailable" with no cause sends an
    operator to Keychain Access instead of to `uv sync`.
    """
    monkeypatch.setitem(sys.modules, "keyring", None)
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


def test_a_corrupted_entry_is_a_refusal_not_a_wrong_key(monkeypatch):
    """A stored value that is not this adapter's base64 must not decode at all.

    `b64decode` without `validate=True` silently *discards* invalid characters,
    so a corrupted or hand-edited entry decoded to differently-sized bytes and
    failed later as `KeyUnusable` — an error about the cipher, when the keychain
    entry is what is broken. The refusal is `KeychainUnavailable`, because
    "cannot hand back the enrolled key" is the same operational state as
    "cannot ask".
    """

    def corrupted(module, _error):
        module.get_password = lambda service, account: "not!base64@at#all"

    _install_fake_keyring(monkeypatch, behaviour=corrupted)

    with pytest.raises(KeychainUnavailable, match="not the base64"):
        MacOSKeychainAdapter().fetch(NAME)


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


# ── The conditional store (story 4b) ─────────────────────────────────────────
#
# `store` replaces; `store_if_absent` refuses. The distinction is the whole
# defence against a second enrolment overwriting the key every sealed artifact
# was written under, so both halves are exercised: the port's contract through
# the fake, and the adapter's OSStatus-to-refusal mapping through a substituted
# framework call — the real one would be a Keychain write, which story 1d
# forbids the suite outright.


def test_a_conditional_store_refuses_rather_than_replacing():
    keychain = FakeKeychain({NAME: SECRET})

    with pytest.raises(KeyAlreadyEnrolled):
        keychain.store_if_absent(NAME, b"a second key")

    assert keychain.fetch(NAME) == SECRET, "the refusal still replaced the key"


def test_a_conditional_store_into_an_empty_keychain_writes():
    keychain = FakeKeychain()
    keychain.store_if_absent(NAME, SECRET)
    assert keychain.fetch(NAME) == SECRET


def _substitute_add(monkeypatch, status, calls):
    def add(service, account, encoded):
        calls.append((service, account, encoded))
        return status

    monkeypatch.setattr(keychain_module, "_add_generic_password", add)


def test_the_adapter_reads_a_duplicate_item_as_already_enrolled(monkeypatch):
    """`errSecDuplicateItem` is the framework refusing, not the adapter guessing.

    The status is what makes the operation conditional: the check and the write
    happen inside Security, so two enrolments racing cannot both observe an
    empty keychain and both write.
    """
    _install_fake_keyring(monkeypatch, behaviour=lambda module, error: None)
    _substitute_add(monkeypatch, keychain_module.ERR_SEC_DUPLICATE_ITEM, [])

    with pytest.raises(KeyAlreadyEnrolled) as refusal:
        MacOSKeychainAdapter().store_if_absent(NAME, SECRET)

    assert "unreadable" in str(refusal.value), "the refusal must name the consequence"


def test_the_adapter_writes_the_same_base64_the_replacing_store_writes(monkeypatch):
    """Or `fetch` reads back something it cannot decode, on the enrolment path only.

    Two spellings of one secret in a keychain is a key that decodes on the path
    nobody used and not on the path everybody does.
    """
    import base64

    _install_fake_keyring(monkeypatch, behaviour=lambda module, error: None)
    calls: list[tuple[str, str, str]] = []
    _substitute_add(monkeypatch, 0, calls)

    MacOSKeychainAdapter().store_if_absent(NAME, SECRET)

    assert calls == [(KEYCHAIN_SERVICE, NAME, base64.b64encode(SECRET).decode("ascii"))]
    assert SECRET.decode("latin-1") not in calls[0][2]


def test_an_unrecognised_status_is_a_refusal_that_names_it(monkeypatch):
    """Anything but success or duplicate means nothing is known and nothing was written."""
    _install_fake_keyring(monkeypatch, behaviour=lambda module, error: None)
    _substitute_add(monkeypatch, -25308, [])  # errSecInteractionNotAllowed

    with pytest.raises(KeychainUnavailable, match="-25308"):
        MacOSKeychainAdapter().store_if_absent(NAME, SECRET)


def test_no_key_is_written_when_the_backend_that_would_read_it_is_missing(monkeypatch):
    """A key `fetch` cannot read is not enrolled, however cleanly the write went.

    Without the gate an install missing `keyring` reports a successful enrolment
    and then fails every read of the key it just wrote — a machine that looks set
    up and is not.
    """
    monkeypatch.setitem(sys.modules, "keyring", None)
    calls: list[tuple[str, str, str]] = []
    _substitute_add(monkeypatch, 0, calls)

    with pytest.raises(KeychainUnavailable) as refusal:
        MacOSKeychainAdapter().store_if_absent(NAME, SECRET)

    assert "keyring" in str(refusal.value)
    assert calls == [], "a key was written into a keychain nothing can read"


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
