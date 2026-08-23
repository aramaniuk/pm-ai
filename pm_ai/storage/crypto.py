"""Which artifacts are encrypted at rest, and which are deliberately not (AD-6).

Policy only. No cipher, no key, no file I/O — a caller that has a secret decides
what to do with it. Keeping this pure is what lets it be asked about a path that
does not exist yet, which is every first write.

## The answer is declared, not inferred

Encryption is a required field on every `File` and `Collection` in the four
scope trees, alongside the tier, and `ENCRYPTED` is derived from those
declarations. So an artifact cannot be added without an encryption answer, the
way one cannot be added without a tier. The alternative — a table of rules kept
beside the trees — is the shape the tier model was moved *out* of: two edits to
add one artifact, and nothing but an import-time check catching the two drifting
apart.

## Why not a path prefix

`~/.pm-ai/private/vector_index/index.bin` is plaintext and
`~/.pm-ai/private/config.json` is encrypted. They share a parent, so any rule
reading the `private/` prefix gets one of them wrong. Classification is by what
the artifact *is*.

## Why the answer is per scope

`meetings/` under `people/` holds a direct report's 1:1 records, which the
storage contract requires encrypted. `meetings/` in a project holds summaries
committed to the repository, which the same contract requires plaintext. One
basename, two correct answers — so `ENCRYPTED` is keyed on `(scope, key)`, and
this module has to work out which scope a path is in. It does that from the
scope root names in `pm_ai.domain.scope_model` rather than from the resolver,
because `pm_ai.storage` and `pm_ai.platform` are independent siblings.

## Undeclared paths fail closed

A path no tree names is either a historical name — `event_telemetry.db` and
`chat_history/` are both former spellings still asserted by an older test — or an
artifact someone forgot to declare. Guessing plaintext on either is the guess
that leaks, so the answer is `True`. Answering with an exception instead would
turn every unrecognised path into an outage; answering *encrypted* turns it into
a file the PM cannot grep until someone declares it. The second failure is
visible and recoverable.
"""

from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path, PurePath

from pm_ai.domain.identity import ScopeKind
from pm_ai.domain.scope_model import (
    APPLICATION_DIRNAME,
    ENCLAVE_DIRNAME,
    ENCRYPTION,
    PEOPLE_DIRNAME,
    PERSONAL_DIRNAME,
    PROJECT_DIRNAME,
)
from pm_ai.ports import CryptoPort, DecryptionFailed, KeychainPort

__all__ = [
    "AES_KEY_BYTES",
    "AesGcmCrypto",
    "ENCLAVE_DIR_MODE",
    "ENCRYPTED_FILE_MODE",
    "KeyUnusable",
    "LazyKeyCrypto",
    "PlaintextCrypto",
    "is_encrypted",
    "read_encrypted",
    "scope_of",
    "write_encrypted",
]

_PEOPLE_MARKER = (APPLICATION_DIRNAME, ENCLAVE_DIRNAME, PEOPLE_DIRNAME)


def scope_of(path: str) -> ScopeKind | None:
    """Which scope `path` belongs to, or `None` if it belongs to none.

    Ordered: the team-member scope is nested inside the application scope, so a
    path under `~/.pm-ai/private/people/` matches both markers and must be
    reported as the inner one. Checking application first would file every
    report's record under the scope documented as holding no personal records.
    """
    parts = PurePath(path).parts
    for index in range(len(parts) - len(_PEOPLE_MARKER) + 1):
        if tuple(parts[index : index + len(_PEOPLE_MARKER)]) == _PEOPLE_MARKER:
            return ScopeKind.PEOPLE
    if PERSONAL_DIRNAME in parts:
        return ScopeKind.PERSONAL
    if PROJECT_DIRNAME in parts:
        return ScopeKind.PROJECT
    if APPLICATION_DIRNAME in parts:
        return ScopeKind.APPLICATION
    return None


def is_encrypted(path: str) -> bool:
    """Whether the artifact at `path` is encrypted at rest.

    Walks the path from its last segment upward and takes the answer of the
    deepest artifact that declares one. A file inside a `Collection` — a dated
    event-log segment, a person's dossier, a capture — is not itself declared,
    so the directory that *is* declared answers for it. Structure that declares
    nothing is skipped rather than treated as a verdict, which is what stops
    `private/` answering for a child whose answer differs from its siblings'.
    """
    scope = scope_of(path)
    if scope is None:
        return True
    answers = ENCRYPTION[scope]
    for part in reversed(PurePath(path).parts):
        # A directory key carries its slash; a file key does not. Trying both is
        # cheaper than deciding from the string whether this segment is one.
        for candidate in (f"{part}/", part):
            if candidate in answers:
                return answers[candidate]
    return True


# ── The cipher over the encrypted set ────────────────────────────────────────
#
# Two artifacts, both files: `~/.pm-ai/private/config.json` and
# `~/.manager-ai/private/telegram_cache/`. Nothing encrypted is a database, so
# this is an envelope over bytes rather than anything page-level.

# AES-256, as `storage-contract.md` specifies. GCM rather than CBC because it
# authenticates: a wrong key, a truncated file or a tampered byte becomes a
# raised error instead of plausible-looking garbage the caller cannot detect.
AES_KEY_BYTES = 32
_NONCE_BYTES = 12

# Directories holding encrypted artifacts. A `0600` file inside a world-readable
# directory still publishes its name, size and mtime — enough to show that a 1:1
# with a named report happened on a given day, which is the fact the enclave
# exists to hide.
ENCLAVE_DIR_MODE = 0o700
ENCRYPTED_FILE_MODE = 0o600


class KeyUnusable(ValueError):
    """The key is absent or the wrong length, so nothing may be opened with it.

    Separate from `DecryptionFailed`: that one means the *data* did not verify,
    this one means we never had a usable key to try. Conflating them would let a
    missing key read as a corrupt file, and the repair for those is not the same.
    """


@dataclass(frozen=True, slots=True)
class AesGcmCrypto:
    """Satisfies `pm_ai.ports.CryptoPort` with AES-256-GCM over a held key.

    `cryptography` is imported inside each method. It is an optional `runtime`
    extra, and a module-level import would make this whole module unimportable
    without it — which would turn `is_encrypted`, pure policy that needs no
    cipher at all, into something that only works on a fully provisioned machine.
    It is also a dev dependency, so the suite exercises the real cipher rather
    than skipping and reading green.
    """

    key: bytes

    def __post_init__(self) -> None:
        if len(self.key) != AES_KEY_BYTES:
            raise KeyUnusable(
                f"an AES-256 key is {AES_KEY_BYTES} bytes and this one is "
                f"{len(self.key)}. Refusing to construct a cipher that would "
                f"either fail on first use or silently use a weaker key."
            )

    def encrypt(self, plaintext: bytes) -> bytes:
        aead = self._aesgcm()
        # Fresh nonce per call, prepended. Reusing one under the same key breaks
        # GCM outright, and a deterministic envelope over a small credential file
        # would let an observer confirm a guess at its contents.
        nonce = secrets.token_bytes(_NONCE_BYTES)
        return nonce + aead.encrypt(nonce, plaintext, None)

    def decrypt(self, envelope: bytes) -> bytes:
        from cryptography.exceptions import InvalidTag

        if len(envelope) <= _NONCE_BYTES:
            raise DecryptionFailed(
                "the envelope is too short to contain a nonce and a tag, so it "
                "was not produced by this cipher — most likely a plaintext file "
                "being read as an encrypted one."
            )
        aead = self._aesgcm()
        try:
            return aead.decrypt(envelope[:_NONCE_BYTES], envelope[_NONCE_BYTES:], None)
        except InvalidTag as unverified:
            raise DecryptionFailed(
                "the envelope did not verify under this key. Either the key is "
                "not the one it was sealed with, or the bytes have changed since."
            ) from unverified

    def _aesgcm(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(self.key)


@dataclass(frozen=True, slots=True)
class PlaintextCrypto:
    """The debug flag's cipher: a pass-through, and never the default.

    A `CryptoPort` rather than a `None` the write path has to check, so there is
    exactly one code path whether or not encryption is on. `pm_ai.app.wiring`
    owns the choice and owns announcing it — the console warning and the
    event-log entry — because this module cannot reach either and should not
    know a flag exists.
    """

    def encrypt(self, plaintext: bytes) -> bytes:
        return plaintext

    def decrypt(self, envelope: bytes) -> bytes:
        return envelope


def write_encrypted(path: Path, payload: bytes, *, crypto: CryptoPort) -> Path:
    """Seal `payload` into `path`, at `0600` inside a `0700` directory.

    The directory is created at `0700` rather than created and then tightened,
    so there is no window in which the enclave is readable. An existing parent
    that is looser *is* tightened: story 1a created these directories before
    anything was encrypted in them, so inheriting a `0755` from then is the
    ordinary case rather than the exotic one.

    Only the immediate parent. Walking further up would eventually chmod a home
    directory, and an ancestor does not hold the encrypted artifact — the
    directory whose listing leaks a name, a size and an mtime is this one.
    """
    # Sealed first, deliberately. If the cipher refuses — no key enrolled, a key
    # of the wrong length — nothing has been created yet, so the refusal leaves
    # no directory and no empty file behind for a later reader to interpret.
    sealed = crypto.encrypt(payload)

    parent = path.parent
    parent.mkdir(parents=True, mode=ENCLAVE_DIR_MODE, exist_ok=True)
    if stat.S_IMODE(parent.stat().st_mode) != ENCLAVE_DIR_MODE:
        parent.chmod(ENCLAVE_DIR_MODE)
    # Opened with the mode on creation, then set unconditionally: `os.open`'s
    # mode is masked by the process umask, so a permissive umask would otherwise
    # leave a credential file group-readable.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, ENCRYPTED_FILE_MODE)
    try:
        os.write(descriptor, sealed)
    finally:
        os.close(descriptor)
    path.chmod(ENCRYPTED_FILE_MODE)
    return path


def read_encrypted(path: Path, *, crypto: CryptoPort) -> bytes:
    """Open what `write_encrypted` sealed, or raise `DecryptionFailed`."""
    return crypto.decrypt(path.read_bytes())


@dataclass
class LazyKeyCrypto:
    """A `CryptoPort` that fetches its key the first time one is actually needed.

    The daemon must start on a machine where no key has been enrolled yet. It
    harvests telemetry, renders briefings and answers the CLI without touching
    either encrypted file, so demanding a key at construction would refuse to
    boot over a capability the run may never use — and the first thing a fresh
    install does is boot.

    Fail-closed is preserved and merely *moved*: the refusal lands at the moment
    an encrypted artifact is read or written, which is also the moment an operator
    can act on it. That is the same shape as the capture guard, which asks git
    when a capture is written rather than at startup.

    Not frozen, because it caches. The key is fetched once per daemon rather than
    once per file: a keychain read is a user-visible prompt on some
    configurations, and one per credential write would be intolerable.
    """

    keychain: KeychainPort
    key_name: str
    _cipher: CryptoPort | None = None

    def _resolve(self) -> CryptoPort:
        if self._cipher is None:
            self._cipher = AesGcmCrypto(self.keychain.fetch(self.key_name))
        return self._cipher

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._resolve().encrypt(plaintext)

    def decrypt(self, envelope: bytes) -> bytes:
        return self._resolve().decrypt(envelope)
