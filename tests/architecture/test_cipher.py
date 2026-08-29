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

import re
import os
import stat
from datetime import datetime, timezone

import pytest

from pm_ai.domain.event_entries import EventEntry, SelfActionType
from pm_ai.app.wiring import build
from pm_ai.storage.service import AppendToSealedArtifact
from pm_ai.domain.storage_tiers import EVENT_LOG

from pm_ai.ports import CryptoPort, DecryptionFailed
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.ports import KeyNotFound
from pm_ai.storage.crypto import (
    AES_KEY_BYTES,
    ENCLAVE_DIR_MODE,
    ENCRYPTED_FILE_MODE,
    AesGcmCrypto,
    KeyUnusable,
    LazyKeyCrypto,
    PlaintextCrypto,
    is_encrypted,
)

APPLICATION = DataScope(ScopeKind.APPLICATION)
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")


def _seal(daemon, payload=None, *, name=None):
    """Write the credential store through the single writer.

    Every encrypted read and write goes through `StorageService`, so a test that
    called a cipher helper directly would be exercising a path production code
    is forbidden to use.
    """
    return daemon.storage.write_artifact(
        payload if payload is not None else PAYLOAD,
        scope=APPLICATION,
        artifact="config.json",
        name=name,
    )

PAYLOAD = b'{"jira": "token-\xff\x00-not-utf8"}'


def _entry(marker: str):
    """A minimal typed entry, standing in for the old free-string call (2e)."""
    return EventEntry(
        category=SelfActionType.SECURITY, actor="test", fields=(("detail", marker),)
    )


@pytest.fixture
def key():
    return os.urandom(AES_KEY_BYTES)


@pytest.fixture
def crypto(key):
    return AesGcmCrypto(key)


class FakeKeychain:
    """A whole `KeychainPort`, counting reads.

    All three methods, though `LazyKeyCrypto` calls only `fetch`. The docstring
    here used to say "only the one method it uses, so the test cannot lean on
    more" — which sounded like discipline and was actually an over-claim: the
    parameter is typed `KeychainPort`, so a partial fake asserts conformance it
    does not have, and `isinstance` would have said so. Completed 2026-08-25
    alongside the same fix in `test_doctor.py`.
    """

    def __init__(self, secret: bytes | None = None):
        self.secret = secret
        self.reads = 0

    def store(self, name: str, secret: bytes) -> None:
        self.secret = secret

    def fetch(self, name: str) -> bytes:
        self.reads += 1
        if self.secret is None:
            raise KeyNotFound(f"nothing stored under {name!r}")
        return self.secret

    def delete(self, name: str) -> None:
        if self.secret is None:
            raise KeyNotFound(f"nothing stored under {name!r}")
        self.secret = None


def test_both_ciphers_satisfy_the_port(crypto):
    from pm_ai.ports import KeychainPort

    assert isinstance(FakeKeychain(), KeychainPort), (
        "a partial fake makes every row that uses it prove less than it looks"
    )
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


def test_a_plaintext_file_read_as_encrypted_is_refused(tmp_path):
    """The debug flag's other direction: disabled, then re-enabled with the key.

    Written by a daemon with the flag on, read by one with it off. The file is
    plaintext and the cipher is real, and that must be reported rather than read
    as corruption or decoded into nonsense.
    """
    written = _seal(_daemon(tmp_path, disabled=True))
    assert written.read_bytes() == PAYLOAD

    resealed = _daemon(tmp_path, disabled=False)
    with pytest.raises(DecryptionFailed):
        resealed.storage.read_artifact(scope=APPLICATION, artifact="config.json")


@pytest.mark.parametrize("length", [0, 16, 31, 33, 64])
def test_a_key_of_the_wrong_length_is_refused_at_construction(length):
    """Before first use, not at it. AES-256 means 32 bytes and nothing else."""
    with pytest.raises(KeyUnusable, match=str(AES_KEY_BYTES)):
        AesGcmCrypto(os.urandom(length))


# ── Permissions ──────────────────────────────────────────────────────────────


def test_a_fresh_tree_is_created_at_enclave_permissions(tmp_path):
    target = _seal(_daemon(tmp_path, disabled=False))

    assert stat.S_IMODE(target.stat().st_mode) == ENCRYPTED_FILE_MODE
    assert stat.S_IMODE(target.parent.stat().st_mode) == ENCLAVE_DIR_MODE


def test_a_directory_that_already_exists_too_open_is_tightened(tmp_path):
    """Story 1a created these directories before anything was encrypted in them.

    So inheriting a `0755` is the ordinary case rather than the exotic one, and
    leaving it as found would put a `0600` file in a listable directory.
    """
    daemon = _daemon(tmp_path, disabled=False)
    enclave = daemon.storage.paths.resolve(APPLICATION, "config.json").parent
    enclave.mkdir(parents=True, exist_ok=True)
    enclave.chmod(0o755)
    assert stat.S_IMODE(enclave.stat().st_mode) == 0o755

    _seal(daemon)

    assert stat.S_IMODE(enclave.stat().st_mode) == ENCLAVE_DIR_MODE


def test_a_permissive_umask_cannot_loosen_the_file(tmp_path, crypto):
    """`os.open`'s mode is masked by the umask, so it is set again afterwards.

    Without that, a user running with `umask 000` writes a world-readable
    credential file and nothing anywhere reports it.
    """
    previous = os.umask(0o000)
    try:
        target = _seal(_daemon(tmp_path, disabled=False))
        assert stat.S_IMODE(target.stat().st_mode) == ENCRYPTED_FILE_MODE
    finally:
        os.umask(previous)


# ── Failing closed, and where ────────────────────────────────────────────────


def test_no_key_refuses_the_write_and_leaves_nothing_behind(tmp_path):
    """The refusal must not create the directory or an empty file at the path.

    A zero-length `config.json` is worse than none: the next reader cannot tell
    it from a store that was legitimately emptied.
    """
    daemon = _daemon(tmp_path, disabled=False, keychain=FakeKeychain())
    target = daemon.storage.paths.resolve(APPLICATION, "config.json")

    with pytest.raises(KeyNotFound):
        _seal(daemon)

    assert not target.exists()


def test_the_key_is_fetched_once_per_daemon_not_once_per_file(tmp_path):
    """A keychain read is a user-visible prompt on some configurations."""
    keychain = FakeKeychain(os.urandom(AES_KEY_BYTES))
    daemon = _daemon(tmp_path, disabled=False, keychain=keychain)

    for _ in range(3):
        _seal(daemon)

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
    # A *declared* plaintext artifact. `standup.md` would not do: it sits inside
    # the `meetings/` collection and is not declared itself, so it fails closed —
    # correctly, and it would be testing the wrong thing here.
    declared = tmp_path / ".project-ai" / "memory" / "commitments_log.md"
    assert is_encrypted(str(declared)) is False
    # The lazy cipher is the "no key is fetched" half: wrapping a keychain that
    # counts reads and never touching it. (An earlier version asserted on a
    # FakeKeychain that was handed to nothing, which could not fail.)
    keychain = FakeKeychain()
    LazyKeyCrypto(keychain, "master")
    assert keychain.reads == 0, "constructing the lazy cipher must not fetch a key"


def test_the_module_imports_without_the_runtime_extra():
    """`cryptography` is imported inside the methods that use it.

    A module-level import would make `is_encrypted` — pure policy needing no
    cipher — unavailable on any machine without the extra.
    """
    import importlib

    assert importlib.import_module("pm_ai.storage.crypto") is not None


# ── The debug flag: the one path that writes secrets in plaintext ─────────────
#
# Story 1f's matrix requires that disabling encryption be announced *twice* — a
# console warning for whoever is running the daemon now, an event-log entry for
# whoever reads the record later and would otherwise find plaintext credentials
# with no explanation. This section was missing when 1f was first marked done:
# the code existed and nothing verified it, so the single most dangerous path in
# the story was the one path with no test.

NOW = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)


def _daemon(tmp_path, *, disabled: bool, keychain=None):
    return build(
        tmp_path,
        "alpha",
        now=lambda: NOW,
        keychain=keychain or FakeKeychain(os.urandom(AES_KEY_BYTES)),
        encryption_disabled=disabled,
    )


def _event_log_text(daemon, scope: DataScope = APPLICATION) -> str:
    # Defaults to the application scope's log, not the daemon's project scope:
    # the debug flag describes the daemon's own posture, and the project
    # `event_log/` is committed — writing the announcement there published "the
    # operator ran with encryption off" to the employer's repository (review
    # 2026-08-28).
    segments = list(daemon.storage.paths.resolve(scope, EVENT_LOG).glob("*.md"))
    return "".join(s.read_text(encoding="utf-8") for s in segments)


def test_the_debug_flag_selects_the_pass_through_cipher(tmp_path):
    daemon = _daemon(tmp_path, disabled=True)
    assert isinstance(daemon.crypto, PlaintextCrypto)


def test_the_debug_flag_actually_leaves_the_bytes_on_disk_readable(tmp_path):
    """Selecting the cipher is not the claim; what reaches the disk is.

    A test that only asserted the type would pass against a `PlaintextCrypto`
    that had quietly grown a cipher.
    """
    target = _seal(_daemon(tmp_path, disabled=True))

    assert target.read_bytes() == PAYLOAD


def test_the_debug_flag_still_writes_at_enclave_permissions(tmp_path):
    """Encryption off is not permissions off.

    Plaintext credentials in a world-readable directory would be two failures
    where the flag only asked for one.
    """
    target = _seal(_daemon(tmp_path, disabled=True))

    assert stat.S_IMODE(target.stat().st_mode) == ENCRYPTED_FILE_MODE
    assert stat.S_IMODE(target.parent.stat().st_mode) == ENCLAVE_DIR_MODE


def test_the_debug_flag_warns_on_the_console(tmp_path, capsys):
    """On stderr, so it survives a caller piping stdout somewhere."""
    _daemon(tmp_path, disabled=True)

    captured = capsys.readouterr()
    assert "encryption is disabled" in captured.err
    assert "plaintext" in captured.err
    assert captured.err.strip(), "the warning went nowhere a human would see it"


def test_the_debug_flag_writes_an_event_log_entry(tmp_path):
    """The half that outlives the session.

    A console warning is gone the moment the terminal scrolls. Someone auditing
    the record later has to be able to find out why a credential file is
    readable, and that answer has to be in the log rather than in someone's
    memory of a startup message.
    """
    daemon = _daemon(tmp_path, disabled=True)

    body = _event_log_text(daemon)
    assert "protection=encryption-at-rest" in body
    assert " security actor=" in body, "the entry must be findable by category"


def test_encryption_on_announces_nothing_at_all(tmp_path, capsys):
    """The other half of "never the default in a fresh installation".

    A warning that also fires on the healthy path is a warning nobody reads, and
    an event log that records normal operation as a security event is one nobody
    can grep. Asserted on both channels, because emitting to only one of them
    would still be wrong and would still pass a single-channel test.
    """
    daemon = _daemon(tmp_path, disabled=False)

    assert not isinstance(daemon.crypto, PlaintextCrypto)
    assert "encryption is disabled" not in capsys.readouterr().err
    assert "encryption disabled" not in _event_log_text(daemon)


def test_encryption_is_on_when_nobody_says_otherwise(tmp_path):
    """The default is not a keyword a caller has to remember to pass."""
    daemon = build(tmp_path, "alpha", now=lambda: NOW, keychain=FakeKeychain(b"x" * 32))
    assert isinstance(daemon.crypto, LazyKeyCrypto)


def test_the_disabled_daemon_still_needs_no_key(tmp_path):
    """The flag exists to get a machine working, so it cannot want a key.

    An empty keychain plus the flag has to start; if it did not, the flag would
    be useless in exactly the situation it is for.
    """
    keychain = FakeKeychain()  # nothing stored

    daemon = _daemon(tmp_path, disabled=True, keychain=keychain)

    _seal(daemon)
    assert keychain.reads == 0


# ── The routing itself: is_encrypted consulted per operation ──────────────────
#
# Story 1e built the authority and story 1f, as first written, did not ask it —
# `write_encrypted` encrypted whatever it was handed, so *which* artifacts are
# sealed still depended on a caller reaching for the right helper. These are the
# tests for the seam that closed: the service asks, and the declaration decides.

PERSONAL = DataScope(ScopeKind.PERSONAL)


def test_a_plaintext_artifact_is_written_unsealed_and_needs_no_key(tmp_path):
    """The matrix row that had no real test until now.

    The earlier attempt asserted `keychain.reads == 0` on a fake it never handed
    to anything, which cannot fail. This routes a genuinely plaintext artifact
    through the writer and checks both halves: the bytes land as themselves, and
    the keychain is never touched.
    """
    keychain = FakeKeychain(os.urandom(AES_KEY_BYTES))
    daemon = _daemon(tmp_path, disabled=False, keychain=keychain)

    written = daemon.storage.write_artifact(
        PAYLOAD, scope=PERSONAL, artifact="strategic_goals.md"
    )

    assert written.read_bytes() == PAYLOAD
    assert keychain.reads == 0, "a plaintext artifact reached for a key"


def test_an_encrypted_artifact_is_sealed_and_does_need_a_key(tmp_path):
    """The other half, so the pair together shows the decision being made."""
    keychain = FakeKeychain(os.urandom(AES_KEY_BYTES))
    daemon = _daemon(tmp_path, disabled=False, keychain=keychain)

    written = _seal(daemon)

    assert written.read_bytes() != PAYLOAD
    assert keychain.reads == 1


def test_appending_to_a_declared_encrypted_artifact_is_refused(tmp_path):
    """`telegram_cache/` is encrypted, so appending to it cannot be honoured.

    AES-GCM covers the whole payload, so an append would mean read, decrypt,
    concatenate, re-seal, rewrite — and rewriting in place is what AD-5 forbids
    for a ledger and what segmented compaction exists to avoid. This is the
    reachable case: no Markdown is encrypted anywhere, so a ledger cannot get
    here today, but the PM's voice notes are sealed and are a directory of files
    something could reasonably try to append to.
    """
    daemon = _daemon(tmp_path, disabled=False)
    target = daemon.storage.paths.resolve(PERSONAL, "telegram_cache/") / "state.json"
    target.parent.mkdir(parents=True, exist_ok=True)

    with pytest.raises(AppendToSealedArtifact) as refusal:
        daemon.storage._append(target, "one more line\n")

    assert "telegram_cache" in str(refusal.value)
    assert not target.exists(), "the refusal created the file it refused to write"


def test_appending_to_a_plaintext_ledger_still_works(tmp_path):
    """The negative half. A guard that refused both would pass the test above.

    Asserted through the public method rather than the primitive, because this is
    the path every audit-trail line in the system takes.
    """
    daemon = _daemon(tmp_path, disabled=False)

    daemon.storage.append_event_log(_entry("a line"), scope=daemon.scope)
    daemon.storage.append_event_log(_entry("another"), scope=daemon.scope)

    body = _event_log_text(daemon, scope=daemon.scope)
    assert body.count("actor=test") == 2, "appending replaced rather than appended"
