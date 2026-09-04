"""Minting the master key, once, when a human asks (AD-6).

Story 1d put key custody behind `KeychainPort` and 1f made encrypted writes
fetch that key lazily, so a clean install boots happily and refuses its first
encrypted write. `pm_ai.platform.doctor` reports the gap as `ABSENT` — reachable,
nothing stored — and until this module there was no way out of that state. This
is the way out, and `4c` is the command that calls it.

## The daemon never mints

A new key makes every previously sealed artifact permanently unreadable. That is
not a decision a process start may take on a machine whose keychain happened to
be locked at boot, so minting happens in exactly one function, and only when a
human invokes it. Everything else in pm-ai *fetches*.

## Why this is a core service taking a port

The failure modes worth testing are a keychain that cannot be reached, a keychain
without a backend, and an entry that is present but not a key — none of which a
test may provoke against a real Keychain, and all of which a fake `KeychainPort`
produces exactly. That is the shape 1g used for its probes, for the same reason.
The layering contract also forbids `pm_ai.core` importing `keyring` or
`pm_ai.storage`, which is why `AES_KEY_BYTES` and `MASTER_KEY_NAME` both live in
`pm_ai.ports` rather than beside the cipher that enforces the first.

## Key material never leaves this function

Not returned, not logged, not echoed, not formatted into a refusal. The minted
key is a local that reaches `store_if_absent` and the read-back comparison and
nothing else; every message here is written in lengths and names. A diagnostic
that prints a secret turns a support request into a disclosure, and the moment a
key reaches a traceback it is in a bug report.

## Why the key is read back

A keychain write that reports success and stores nothing is indistinguishable,
from the caller's side, from one that worked — until the first encrypted write
months later, on a machine with nobody watching. Reading it back immediately, and
comparing it for *equality* rather than merely for retrievability, turns a silent
latent failure into a loud one at the only moment a human is present to act.

Rotation is not here and is not this. Replacing an enrolled key means rewriting
every sealed artifact, and no story owns that yet.
"""

from __future__ import annotations

import secrets

from pm_ai.ports import (
    AES_KEY_BYTES,
    MASTER_KEY_NAME,
    KeyAlreadyEnrolled,
    KeychainPort,
    KeychainUnavailable,
    KeyNotFound,
)

# Re-exported rather than redefined. `KeyAlreadyEnrolled` is raised by
# `KeychainPort.store_if_absent`, which is where the refusal is *decided* — a
# second class of the same name here would mean a caller's `except` caught one
# of them and let the other through, which for this particular refusal means
# minting over a key.
__all__ = ["KeyAlreadyEnrolled", "enrol"]


def _mint() -> bytes:
    """A fresh master key. The only place in pm-ai that makes one.

    A module-level function rather than an inline call so the length guard in
    `enrol` has something to fail against: `secrets.token_bytes` cannot return
    the wrong number of bytes, and a guard nothing can reach is a comment.
    """
    return secrets.token_bytes(AES_KEY_BYTES)


def enrol(keychain: KeychainPort, *, key_name: str = MASTER_KEY_NAME) -> str:
    """Mint a master key and store it under `key_name`, or refuse. Returns the name.

    Returns the *name*, never the key: the caller's job is to say where the key
    went, and a return value carrying the secret would put it in `4c`'s local
    scope, its repr, and any traceback raised beneath it.

    Refuses, and writes nothing, when:

    - something is already stored under `key_name` (`KeyAlreadyEnrolled`) —
      including an entry that is truncated or not key material at all, because
      the store is conditional on *absence* and a corrupt entry is not absent;
    - the minted key is not `AES_KEY_BYTES` long (`ValueError`), checked before
      the store rather than after, so a short key never reaches the keychain;
    - the keychain cannot be reached (`KeychainUnavailable`) or has no backend
      to ask (`KeychainBackendMissing`). Both propagate from the adapter
      untouched: they already carry the repair, and `pm_ai.core` cannot import
      the probe whose remediation text says the same thing.

    And refuses *after* a reported-successful write, as `KeychainUnavailable`,
    when the key cannot be read back or reads back as different bytes. A key
    that cannot be read back is not enrolled, whatever the write said.
    """
    key = _mint()
    if len(key) != AES_KEY_BYTES:
        # Before the store, deliberately. A key of the wrong length in the
        # keychain is a machine that boots, passes the doctor's ABSENT check,
        # and fails at the first encrypted write with an error about the cipher.
        raise ValueError(
            f"a master key is {AES_KEY_BYTES} bytes and this one is {len(key)}. "
            f"Nothing was stored: {AES_KEY_BYTES} is what the cipher accepts, "
            f"and a key of any other length would be enrolled and then unusable."
        )

    try:
        keychain.store_if_absent(key_name, key)
    except KeyAlreadyEnrolled as present:
        raise KeyAlreadyEnrolled(
            f"a key is already enrolled under {key_name!r}, and enrolment does "
            f"not replace one: every artifact sealed under the existing key "
            f"would become permanently unreadable. Nothing was written. "
            f"Replacing a key is rotation, which re-encrypts what it replaces "
            f"and is not this command. ({present})"
        ) from present

    try:
        stored = keychain.fetch(key_name)
    except KeyNotFound as vanished:
        # The write said it worked and the read says there is nothing there.
        # `KeyNotFound` would be a lie to propagate — this is not a keychain
        # with no key yet, it is a keychain that did not do what it reported.
        raise KeychainUnavailable(
            f"the keychain reported storing a key under {key_name!r} and then "
            f"reported that nothing is stored there. The key is not enrolled; "
            f"do not treat this machine as set up. ({vanished})"
        ) from vanished

    if stored != key:
        # Lengths only. Naming either value here would put key material in a
        # traceback, and the two candidates are the minted key and whatever the
        # keychain is holding — both secrets.
        raise KeychainUnavailable(
            f"the key read back from {key_name!r} is not the key that was just "
            f"stored ({len(stored)} bytes read, {len(key)} written). Enrolment "
            f"failed; anything sealed against this keychain would be sealed "
            f"under a key nobody has."
        )

    return key_name
