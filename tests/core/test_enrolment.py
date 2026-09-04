"""Minting the master key: every row of story 4b's matrix, against a fake (AD-6).

`enrol` is the only place in pm-ai that makes a key, and every way it can go
wrong destroys data or looks like success while leaving a machine unusable. So
the rows here are the failure modes, not the happy path — and all of them run
against a fake `KeychainPort`, because a real Keychain cannot be made to be
locked, corrupt, or missing its backend on demand, and story 1d forbids the suite
touching one at all.

Two properties are asserted on the fake rather than read out of a message:

- **Nothing is written when enrolment refuses.** A refusal that says the right
  sentence and stores a key anyway is the worst of both.
- **The write goes through `store_if_absent`, never `store`.** A read-then-write
  passes every single-threaded test in this file and loses the race in
  production: two enrolments both see an empty keychain, both write, and every
  artifact sealed between them becomes permanently unreadable.
"""

from __future__ import annotations

import ast
import base64
import logging
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pm_ai.core import enrolment
from pm_ai.core.enrolment import KeyAlreadyEnrolled, enrol
from pm_ai.ports import (
    AES_KEY_BYTES,
    MASTER_KEY_NAME,
    KeychainBackendMissing,
    KeychainPort,
    KeychainUnavailable,
    KeyNotFound,
)

# A stand-in for key material, so a test can look for it in a message. Not a
# real key and never stored anywhere: this is the thing the assertions hunt for.
MINTED = bytes(range(AES_KEY_BYTES))


@dataclass
class FakeKeychain:
    """A whole `KeychainPort`, recording which write was used.

    `stores` counts the *replacing* write, which enrolment must never reach.
    `reachable` and `read_back` are how the rows that cannot be provoked against
    a real keychain get provoked here.
    """

    secrets: dict[str, bytes] = field(default_factory=dict)
    stores: int = 0
    conditional_stores: int = 0
    reachable: BaseException | None = None
    read_back: bytes | None = None
    absent_to_reads: bool = False

    def store(self, name: str, secret: bytes) -> None:
        self.stores += 1
        self.secrets[name] = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        self.conditional_stores += 1
        if self.reachable is not None:
            raise self.reachable
        if name in self.secrets:
            raise KeyAlreadyEnrolled(f"{name!r} already holds a secret")
        self.secrets[name] = secret

    def fetch(self, name: str) -> bytes:
        if self.reachable is not None:
            raise self.reachable
        if self.read_back is not None:
            return self.read_back
        if self.absent_to_reads or name not in self.secrets:
            raise KeyNotFound(f"nothing stored under {name!r}")
        return self.secrets[name]

    def delete(self, name: str) -> None:
        if name not in self.secrets:
            raise KeyNotFound(f"nothing stored under {name!r}")
        del self.secrets[name]


@pytest.fixture
def minted(monkeypatch):
    """A known key, so a test can search output for the material `enrol` handled.

    Substituting the minter is also the only way to reach the length guard:
    `secrets.token_bytes(32)` cannot return anything else, and a guard nothing
    can reach is a comment.
    """
    monkeypatch.setattr(enrolment, "_mint", lambda: MINTED)
    return MINTED


def test_the_fake_satisfies_the_port():
    """If it does not, every row below is testing something else."""
    assert isinstance(FakeKeychain(), KeychainPort)


# ── Fresh install ────────────────────────────────────────────────────────────


def test_a_fresh_install_gets_a_key_and_a_success_that_does_not_echo_it(minted):
    keychain = FakeKeychain()

    result = enrol(keychain)

    assert keychain.secrets == {MASTER_KEY_NAME: minted}
    assert result == MASTER_KEY_NAME
    assert _leaks(result, minted) == []


def test_the_name_and_the_length_come_from_the_constants():
    """Asserted against the constants, not against literals.

    The tests run on a fake, so nothing else would notice enrolment writing a
    32-byte key under `"masterkey"` — the daemon would fetch `"master"`, find
    nothing, and report a fresh install on a machine that had just been set up.
    """
    keychain = FakeKeychain()

    enrol(keychain)

    assert list(keychain.secrets) == [MASTER_KEY_NAME]
    assert len(keychain.secrets[MASTER_KEY_NAME]) == AES_KEY_BYTES


def test_a_caller_may_name_the_key_without_the_default_moving():
    keychain = FakeKeychain()

    assert enrol(keychain, key_name="spare") == "spare"
    assert list(keychain.secrets) == ["spare"]


# ── Two enrolments racing ────────────────────────────────────────────────────


def test_the_write_is_the_conditional_one_never_the_replacing_one():
    """`store` replaces. Reaching it here is how a key gets minted over."""
    keychain = FakeKeychain()

    enrol(keychain)

    assert keychain.conditional_stores == 1
    assert keychain.stores == 0, "enrolment used the write that replaces a key"


def test_two_enrolments_that_both_see_an_empty_keychain_store_exactly_one_key():
    """The row a single-threaded test cannot otherwise reach.

    This fake *lies about absence*: `fetch` always reports nothing stored, which
    is what both racers see when they read before they write. An implementation
    that decided from a read would find the keychain empty and write — replacing
    a key that every artifact sealed since the first enrolment depends on. Only
    a store that is conditional inside the keychain refuses here.
    """
    first = b"\x11" * AES_KEY_BYTES
    keychain = FakeKeychain(secrets={MASTER_KEY_NAME: first}, absent_to_reads=True)

    with pytest.raises(KeyAlreadyEnrolled):
        enrol(keychain)

    assert keychain.secrets == {MASTER_KEY_NAME: first}, "the winner's key was replaced"
    assert keychain.stores == 0


def test_the_second_of_two_sequential_enrolments_refuses(minted):
    keychain = FakeKeychain()
    enrol(keychain)

    with pytest.raises(KeyAlreadyEnrolled):
        enrol(keychain)

    assert keychain.secrets == {MASTER_KEY_NAME: minted}


# ── Stored entry corrupt ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "entry",
    [b"", b"truncated", b"not key material at all, just a note someone left"],
    ids=["empty", "truncated", "not-key-material"],
)
def test_an_entry_that_is_not_a_key_is_still_an_entry(entry):
    """Present-but-broken must refuse as enrolled, never read as absent.

    A corrupt entry read as absence is minted over, and whatever the broken
    entry was — a key stored by an older build, a hand-edited item — whoever
    could have recovered it no longer can.
    """
    keychain = FakeKeychain(secrets={MASTER_KEY_NAME: entry})

    with pytest.raises(KeyAlreadyEnrolled):
        enrol(keychain)

    assert keychain.secrets == {MASTER_KEY_NAME: entry}


# ── Read-back mismatch ───────────────────────────────────────────────────────


def test_a_key_that_reads_back_as_different_bytes_is_not_enrolled(minted):
    """Compared for equality, not merely for retrievability.

    A keychain that answers *something* when asked is not the same as one holding
    the key that was written, and a check that only asked whether a fetch
    succeeded would pass on a machine sealing artifacts under a key nobody has.
    """
    keychain = FakeKeychain(read_back=b"\x99" * AES_KEY_BYTES)

    with pytest.raises(KeychainUnavailable) as refusal:
        enrol(keychain)

    assert _leaks(str(refusal.value), minted) == []


# ── Minted key wrong length ──────────────────────────────────────────────────


@pytest.mark.parametrize("length", [0, 16, 31, 33, 64])
def test_a_minted_key_of_the_wrong_length_never_reaches_the_keychain(
    monkeypatch, length
):
    """Refused before the store, so a short key is never enrolled.

    Enrolled and then unusable is the worst version of this: the machine boots,
    the doctor's keychain probe reports a key present and readable, and the first
    encrypted write fails with an error about the cipher.
    """
    monkeypatch.setattr(enrolment, "_mint", lambda: b"\x07" * length)
    keychain = FakeKeychain()

    with pytest.raises(ValueError, match=str(AES_KEY_BYTES)):
        enrol(keychain)

    assert keychain.secrets == {}
    assert keychain.conditional_stores == 0


# ── Already enrolled ─────────────────────────────────────────────────────────


def test_an_existing_key_is_refused_with_the_consequence_named():
    existing = b"\x22" * AES_KEY_BYTES
    keychain = FakeKeychain(secrets={MASTER_KEY_NAME: existing})

    with pytest.raises(KeyAlreadyEnrolled) as refusal:
        enrol(keychain)

    assert keychain.secrets == {MASTER_KEY_NAME: existing}
    assert keychain.stores == 0, "a write reached the keychain during a refusal"
    assert "unreadable" in str(refusal.value), (
        "the refusal must say what replacing the key would cost, or it reads as "
        "a permissions problem someone will try to force past"
    )


def test_already_enrolled_is_caught_by_nothing_written_for_an_unreachable_keychain():
    """The refusal must not be swallowed by an `except KeychainUnavailable`.

    Minting over a key is not recoverable, so this one has to escape handlers
    written for a keychain that could not answer — and `except KeyNotFound`, the
    other keychain error a caller reaches for, must not see it either.
    """
    assert not issubclass(KeyAlreadyEnrolled, KeychainUnavailable)
    assert not issubclass(KeyAlreadyEnrolled, KeyNotFound)


# ── Keychain unreachable, and backend missing ────────────────────────────────


def test_an_unreachable_keychain_refuses_and_keeps_its_remediation():
    """`pm_ai.core` cannot import the probe, so the adapter's own repair survives.

    Propagated untouched rather than re-wrapped: the message the adapter raises
    already names what to do, and re-raising a plain `KeychainUnavailable` here
    would drop it.
    """
    locked = KeychainUnavailable("the keychain is locked; unlock it and retry")
    keychain = FakeKeychain(reachable=locked)

    with pytest.raises(KeychainUnavailable) as refusal:
        enrol(keychain)

    assert "unlock it" in str(refusal.value)
    assert keychain.secrets == {}


def test_a_missing_backend_stays_distinct_from_an_unreachable_keychain():
    """Different repairs: a package manager versus unlocking a keychain.

    The type has to survive `enrol`, because telling an operator to unlock a
    keychain they never installed sends them in a circle.
    """
    keychain = FakeKeychain(
        reachable=KeychainBackendMissing("install it with `uv sync --extra runtime`")
    )

    with pytest.raises(KeychainBackendMissing) as refusal:
        enrol(keychain)

    assert "uv sync" in str(refusal.value)
    assert keychain.secrets == {}


# ── Store succeeds, read-back fails ──────────────────────────────────────────


def test_a_write_that_cannot_be_read_back_is_refused_loudly():
    """Reported stored, then reported absent. That is not an enrolled key.

    `KeyNotFound` must not propagate: it means "no key yet", an ordinary
    first-run state, and a caller seeing it after a successful write would tell
    the operator to enrol — which they just did.
    """
    keychain = FakeKeychain(absent_to_reads=True)

    with pytest.raises(KeychainUnavailable) as refusal:
        enrol(keychain)

    assert not isinstance(refusal.value, KeyNotFound)
    assert "not enrolled" in str(refusal.value)


# ── The key material never leaves ────────────────────────────────────────────


def _leaks(text: object, key: bytes) -> list[str]:
    """Every spelling of `key` present in `text`. Empty is the only pass.

    Four spellings, because a leak rarely arrives as raw bytes: `repr` of the
    bytes is what a dataclass or an f-string produces, hex is what a debug line
    reaches for, and base64 is how this key is written to the keychain.
    """
    rendered = str(text)
    candidates = {
        "raw": key.decode("latin-1"),
        "repr": repr(key),
        "hex": key.hex(),
        "base64": base64.b64encode(key).decode("ascii"),
    }
    return [name for name, form in candidates.items() if form and form in rendered]


def test_no_spelling_of_the_key_reaches_the_return_value_or_a_log(minted, caplog):
    keychain = FakeKeychain()

    with caplog.at_level(logging.DEBUG):
        result = enrol(keychain)

    emitted = "".join(record.getMessage() for record in caplog.records)
    assert _leaks(result, minted) == []
    assert _leaks(repr(result), minted) == []
    assert _leaks(emitted, minted) == []


@pytest.mark.parametrize(
    "keychain",
    [
        FakeKeychain(secrets={MASTER_KEY_NAME: b"\x33" * AES_KEY_BYTES}),
        FakeKeychain(read_back=b"\x44" * AES_KEY_BYTES),
        FakeKeychain(absent_to_reads=True),
        FakeKeychain(reachable=KeychainUnavailable("locked")),
    ],
    ids=["already-enrolled", "read-back-mismatch", "vanished", "unreachable"],
)
def test_no_spelling_of_the_key_reaches_a_traceback(minted, keychain):
    """Every refusal, not just the ones whose message mentions a key.

    The whole traceback is searched, chained causes included: the moment key
    material lands in one it is in a bug report, and `raise ... from` carries the
    original exception's message along with it.
    """
    try:
        enrol(keychain)
    except Exception:
        rendered = traceback.format_exc()
    else:  # pragma: no cover - every parametrised keychain refuses
        raise AssertionError("this keychain was supposed to refuse")

    assert _leaks(rendered, minted) == []


# ── One key length, not two ──────────────────────────────────────────────────


def test_the_key_length_is_one_constant_shared_by_the_minter_and_the_cipher():
    """Identity, and then the check identity alone cannot make.

    `pm_ai.storage.crypto.AES_KEY_BYTES is pm_ai.ports.AES_KEY_BYTES` is the
    stated criterion — but CPython caches small integers, so two independent
    `AES_KEY_BYTES = 32` literals are also `is` each other and this assertion
    alone would pass over exactly the duplication it exists to forbid. So the
    module is read as well: `pm_ai.storage.crypto` must not assign the name at
    all. `ports/__init__.py` records this failure happening once already, for the
    key *name* — ABSENT reported on a healthy machine.
    """
    from pm_ai import ports
    from pm_ai.storage import crypto

    assert crypto.AES_KEY_BYTES is ports.AES_KEY_BYTES
    assert "AES_KEY_BYTES" in crypto.__all__, "the six call sites importing it break"

    tree = ast.parse(Path(crypto.__file__).read_text(encoding="utf-8"))
    assigned = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "AES_KEY_BYTES"
    ]
    assert not assigned, (
        f"pm_ai.storage.crypto assigns AES_KEY_BYTES at {assigned}; it must "
        f"re-export the one in pm_ai.ports, which pm_ai.core.enrolment mints "
        f"against and may not import this module to read"
    )
