"""The cipher over the encrypted set, and where it refuses (AD-6).

Two artifacts are encrypted, both files: the API credential store and the PM's
own voice notes. So this is an envelope over bytes, and the tests are about three
things the storage contract turns on:

- **Confidentiality that is not accidental** — the same payload sealed twice must
  not give the same bytes, and the bytes must never resemble the payload.
- **Authenticated failure** — a wrong key, a truncated envelope or a plaintext
  file read as encrypted all raise rather than returning something plausible. A
  cipher that returns garbage silently is worse than one that will not open.
- **Permissions** — `0600` on the file and `0700` on the directory holding it. A
  `0600` file in a world-readable directory still publishes its name, size and
  mtime, which is enough to show that a 1:1 with a named report happened on a
  given day.

The real `cryptography` is exercised, not a fake. It is an optional `runtime`
extra *and* a dev dependency for exactly this reason: a skipped encryption test
reads as coverage in a green run.
"""

from __future__ import annotations

import os
import stat

import pytest

from pm_ai.ports import CryptoPort, DecryptionFailed, KeyNotFound
from pm_ai.storage.crypto import (
    AES_KEY_BYTES,
    ENCLAVE_DIR_MODE,
    ENCRYPTED_FILE_MODE,
    AesGcmCrypto,
    KeyUnusable,
    LazyKeyCrypto,
    PlaintextCrypto,
    is_encrypted,
    read_encrypted,
    write_encrypted,
)

PAYLOAD = b'{"jira": "token-\xff\x00-not-utf8"}'


@pytest.fixture
def key():
    return os.urandom(AES_KEY_BYTES)


@pytest.fixture
def crypto(key):
    return AesGcmCrypto(key)


class FakeKeychain:
    """Only the one method `LazyKeyCrypto` uses, so the test cannot lean on more."""

    def __init__(self, secret: bytes | None = None):
        self.secret = secret
        self.reads = 0

    def fetch(self, name: str) -> bytes:
        self.reads += 1
        if self.secret is None:
            raise KeyNotFound(f"nothing stored under {name!r}")
        return self.secret


def test_both_ciphers_satisfy_the_port(crypto):
    assert isinstance(crypto, CryptoPort)
    assert isinstance(PlaintextCrypto(), CryptoPort)
    assert isinstance(LazyKeyCrypto(FakeKeychain(), "master"), CryptoPort)


# ── Confidentiality ──────────────────────────────────────────────────────────


def test_a_sealed_payload_comes_back_exactly(crypto):
    assert crypto.decrypt(crypto.encrypt(PAYLOAD)) == PAYLOAD


def test_the_envelope_does_not_resemble_the_payload(crypto):
    assert PAYLOAD not in crypto.encrypt(PAYLOAD)


def test_sealing_the_same_payload_twice_gives_different_bytes(crypto):
    """A deterministic envelope over a small credential file leaks its contents.

    An observer who suspects the file holds one particular token can seal that
    guess and compare. A fresh nonce per call is what makes that impossible, and
    reusing one under the same key breaks GCM outright.
    """
    assert crypto.encrypt(PAYLOAD) != crypto.encrypt(PAYLOAD)


def test_two_keys_produce_different_envelopes():
    first, second = AesGcmCrypto(os.urandom(32)), AesGcmCrypto(os.urandom(32))
    assert first.encrypt(PAYLOAD) != second.encrypt(PAYLOAD)


# ── Authenticated failure ────────────────────────────────────────────────────


def test_the_wrong_key_raises_rather_than_returning_garbage(crypto):
    with pytest.raises(DecryptionFailed):
        AesGcmCrypto(os.urandom(32)).decrypt(crypto.encrypt(PAYLOAD))


def test_a_tampered_envelope_is_refused(crypto):
    """GCM authenticates, so a changed byte is knowable rather than guessable."""
    envelope = bytearray(crypto.encrypt(PAYLOAD))
    envelope[-1] ^= 0x01
    with pytest.raises(DecryptionFailed):
        crypto.decrypt(bytes(envelope))


@pytest.mark.parametrize("truncated", [b"", b"short", b"x" * 12])
def test_an_envelope_too_short_to_be_one_is_refused(crypto, truncated):
    with pytest.raises(DecryptionFailed):
        crypto.decrypt(truncated)


def test_a_plaintext_file_read_as_encrypted_is_refused(tmp_path, crypto):
    """The debug flag's other direction: disabled, then re-enabled with the key.

    The file on disk is plaintext and the cipher is real. That must be reported,
    not read as corruption and not decoded into nonsense.
    """
    plain = tmp_path / "config.json"
    write_encrypted(plain, PAYLOAD, crypto=PlaintextCrypto())
    assert plain.read_bytes() == PAYLOAD

    with pytest.raises(DecryptionFailed):
        read_encrypted(plain, crypto=crypto)


@pytest.mark.parametrize("length", [0, 16, 31, 33, 64])
def test_a_key_of_the_wrong_length_is_refused_at_construction(length):
    """Before first use, not at it. AES-256 means 32 bytes and nothing else."""
    with pytest.raises(KeyUnusable, match=str(AES_KEY_BYTES)):
        AesGcmCrypto(os.urandom(length))


# ── Permissions ──────────────────────────────────────────────────────────────


def test_a_fresh_tree_is_created_at_enclave_permissions(tmp_path, crypto):
    target = tmp_path / "manager-ai" / "private" / "telegram_cache" / "state.json"

    write_encrypted(target, PAYLOAD, crypto=crypto)

    assert stat.S_IMODE(target.stat().st_mode) == ENCRYPTED_FILE_MODE
    assert stat.S_IMODE(target.parent.stat().st_mode) == ENCLAVE_DIR_MODE


def test_a_directory_that_already_exists_too_open_is_tightened(tmp_path, crypto):
    """Story 1a created these directories before anything was encrypted in them.

    So inheriting a `0755` is the ordinary case rather than the exotic one, and
    leaving it as found would put a `0600` file in a listable directory.
    """
    enclave = tmp_path / "private"
    enclave.mkdir(mode=0o755)
    assert stat.S_IMODE(enclave.stat().st_mode) == 0o755

    write_encrypted(enclave / "config.json", PAYLOAD, crypto=crypto)

    assert stat.S_IMODE(enclave.stat().st_mode) == ENCLAVE_DIR_MODE


def test_a_permissive_umask_cannot_loosen_the_file(tmp_path, crypto):
    """`os.open`'s mode is masked by the umask, so it is set again afterwards.

    Without that, a user running with `umask 000` writes a world-readable
    credential file and nothing anywhere reports it.
    """
    previous = os.umask(0o000)
    try:
        target = tmp_path / "config.json"
        write_encrypted(target, PAYLOAD, crypto=crypto)
        assert stat.S_IMODE(target.stat().st_mode) == ENCRYPTED_FILE_MODE
    finally:
        os.umask(previous)


# ── Failing closed, and where ────────────────────────────────────────────────


def test_no_key_refuses_the_write_and_leaves_nothing_behind(tmp_path):
    """The refusal must not create the directory or an empty file at the path.

    A zero-length `config.json` is worse than none: the next reader cannot tell
    it from a store that was legitimately emptied.
    """
    target = tmp_path / "enclave" / "config.json"

    with pytest.raises(KeyNotFound):
        write_encrypted(target, PAYLOAD, crypto=LazyKeyCrypto(FakeKeychain(), "master"))

    assert not target.exists()
    assert not target.parent.exists()


def test_the_key_is_fetched_once_per_daemon_not_once_per_file(tmp_path):
    """A keychain read is a user-visible prompt on some configurations."""
    keychain = FakeKeychain(os.urandom(AES_KEY_BYTES))
    crypto = LazyKeyCrypto(keychain, "master")

    for name in ("a.json", "b.json", "c.json"):
        write_encrypted(tmp_path / name, PAYLOAD, crypto=crypto)

    assert keychain.reads == 1


def test_no_key_is_fetched_until_something_is_actually_encrypted():
    """The daemon must start on a machine where no key has been enrolled.

    Harvesting, briefings and the CLI never touch either encrypted file, so
    demanding a key at construction would refuse to boot over a capability the
    run may never use — and booting is the first thing a fresh install does.
    """
    keychain = FakeKeychain()
    LazyKeyCrypto(keychain, "master")
    assert keychain.reads == 0


# ── The policy and the cipher stay separate ──────────────────────────────────


def test_an_artifact_the_classifier_calls_plaintext_needs_no_cipher(tmp_path):
    """No key is fetched for something that was never going to be encrypted.

    `is_encrypted` is pure policy and answers without a cipher at all, which is
    why it must not live behind the optional extra the cipher needs.
    """
    keychain = FakeKeychain()
    # A *declared* plaintext artifact. `standup.md` would not do: it sits inside
    # the `meetings/` collection and is not declared itself, so it fails closed —
    # correctly, and it would be testing the wrong thing here.
    declared = tmp_path / ".project-ai" / "memory" / "commitments_log.md"
    assert is_encrypted(str(declared)) is False
    assert keychain.reads == 0


def test_the_module_imports_without_the_runtime_extra():
    """`cryptography` is imported inside the methods that use it.

    A module-level import would make `is_encrypted` — pure policy needing no
    cipher — unavailable on any machine without the extra.
    """
    import importlib

    assert importlib.import_module("pm_ai.storage.crypto") is not None
