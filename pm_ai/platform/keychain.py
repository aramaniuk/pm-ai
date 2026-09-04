"""Master-key custody in the macOS Keychain (AD-6, AD-26).

The adapter behind `pm_ai.ports.KeychainPort`. It lives here because reaching a
keychain is an OS-specific API, and `.importlinter`'s `os-behind-platform`
contract forbids `keyring` in `core`, `surfaces`, `connectors`, `skills`,
`storage` and `models` — which is every layer that might otherwise be tempted.
The key reaches the single writer as a value, passed down by the composition
root, so `pm_ai.storage` never learns where secrets live.

Custody only. Nothing here encrypts anything; that is a later story.

## Why `keyring` is imported inside every method

`keyring` is declared in the `runtime` extra and is deliberately **not** installed
in this environment. The suite's `mod()` helper turns a `ModuleNotFoundError` into
a *skipped* test, so a module-level `import keyring` would convert every test
about key custody into a skip — and a skipped test reads as coverage in a green
run. That is the one failure mode a key-custody test exists to rule out. Imported
inside the call, a missing package is a typed error at the moment it matters, and
this module imports successfully with the extra absent.

## The name a caller passes is not the name the backend sees

`keyring` addresses a secret as (service, account). This adapter fixes the
service to `pm-ai` and uses the caller's name as the account, which is a shape
the Linux Secret Service and Windows Credential Manager both express natively —
so the naming scheme does not have to change when a second adapter appears.

Secrets cross the port as `bytes`, because a master encryption key is arbitrary
bytes rather than text, and `keyring` stores strings. Base64 is the transport:
it round-trips every byte, unlike a text codec, and a key that survived storage
only by luck of its byte values would be a defect that appeared on one machine
in a hundred.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any

from pm_ai.ports import (
    KeyAlreadyEnrolled,
    KeychainBackendMissing,
    KeychainUnavailable,
    KeyNotFound,
)

__all__ = ["KEYCHAIN_SERVICE", "MacOSKeychainAdapter"]

# One service name for every secret pm-ai owns, so uninstalling means deleting one
# service's entries rather than hunting for names nobody wrote down.
KEYCHAIN_SERVICE = "pm-ai"

# `SecKeychainAddGenericPassword`'s answer when an item with the same (service,
# account) already exists. Story 4b's whole reason for going around `keyring`
# for one call: the framework decides absence and writes in the same step, so
# two enrolments racing cannot both win.
ERR_SEC_DUPLICATE_ITEM = -25299


def _keyring() -> tuple[Any, type[BaseException]]:
    """The `keyring` module and its error base, imported at call time.

    Returned as a pair so a caller can name the exception type it must catch
    without a second lazy import, and so a test can substitute both together.
    """
    try:
        import keyring
        from keyring.errors import KeyringError
    except ModuleNotFoundError as missing:
        raise KeychainBackendMissing(
            f"the `keyring` package is not installed, so the master key cannot be "
            f"reached ({missing}). It is declared in the `runtime` extra: install "
            f"it with `uv sync --extra runtime`. pm-ai refuses rather than "
            f"treating an unreachable keychain as an empty one."
        ) from missing
    return keyring, KeyringError


def _add_generic_password(service: str, account: str, encoded: str) -> int:
    """The `OSStatus` from `SecKeychainAddGenericPassword`, and nothing else.

    `keyring` exposes get, set and delete, and `set` replaces — there is no
    conditional write in its API, and a `get` followed by a `set` is the race
    `KeychainPort.store_if_absent` exists to close. The Security framework has
    the primitive: this add is refused with `errSecDuplicateItem` when the item
    exists, decided inside the framework rather than by the caller.

    Reached through `ctypes` rather than the `security(1)` tool, which takes the
    password as an argv word — and argv is world-readable through `ps`, which is
    key material leaving the keychain.

    A separate function so the status-to-refusal mapping above can be exercised
    without a real Keychain, which story 1d forbids the suite to touch. The
    framework is loaded at call time for the same reason `keyring` is: a machine
    that cannot load it must fail as a refusal at the moment it matters, not as
    a traceback out of an import.
    """
    import ctypes
    import ctypes.util

    path = ctypes.util.find_library("Security")
    if path is None:
        raise KeychainUnavailable(
            "the Security framework could not be located, so pm-ai cannot write "
            "a key without first reading one — and read-then-write is what "
            "loses a key to a second enrolment. This adapter is macOS-only "
            "(AD-26); a port to another OS supplies its own conditional store."
        )
    try:
        security = ctypes.CDLL(path)
        add = security.SecKeychainAddGenericPassword
    except (OSError, AttributeError) as unreachable:
        raise KeychainUnavailable(
            f"the Security framework is present and unusable ({unreachable}), so "
            f"whether a key can be enrolled is unknown."
        ) from unreachable
    add.restype = ctypes.c_int32
    add.argtypes = [
        ctypes.c_void_p,  # keychain: NULL means the default keychain
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_void_p,  # out itemRef: NULL, nothing here wants the item back
    ]
    service_bytes = service.encode("utf-8")
    account_bytes = account.encode("utf-8")
    # The base64 this adapter writes, so `fetch` reads back what `store` would
    # have written. Two spellings of the same secret in one keychain would be a
    # key that decodes on one path and not the other.
    payload = encoded.encode("ascii")
    return int(
        add(
            None,
            len(service_bytes),
            service_bytes,
            len(account_bytes),
            account_bytes,
            len(payload),
            payload,
            None,
        )
    )


@dataclass(frozen=True, slots=True)
class MacOSKeychainAdapter:
    """Satisfies `pm_ai.ports.KeychainPort` against the OS keychain.

    Stateless and frozen: the keychain is a property of the machine, not of the
    adapter, so one instance serves every caller.
    """

    def store(self, name: str, secret: bytes) -> None:
        keyring, KeyringError = _keyring()
        encoded = base64.b64encode(secret).decode("ascii")
        try:
            keyring.set_password(KEYCHAIN_SERVICE, name, encoded)
        except KeyringError as unreachable:
            raise KeychainUnavailable(
                f"the keychain refused to store {name!r}: {unreachable}. The "
                f"secret was not written, and pm-ai will not hold it anywhere "
                f"else."
            ) from unreachable

    def store_if_absent(self, name: str, secret: bytes) -> None:
        # The backend gate first, before anything is written. This call reaches
        # the framework directly because `keyring` has no conditional write —
        # but a key stored where `fetch` cannot read it is not enrolled, so the
        # operation still requires the library the rest of this adapter speaks
        # through. Without the gate, an install missing `keyring` would report a
        # successful enrolment and then fail every read of the key it wrote.
        _keyring()
        encoded = base64.b64encode(secret).decode("ascii")
        status = _add_generic_password(KEYCHAIN_SERVICE, name, encoded)
        if status == ERR_SEC_DUPLICATE_ITEM:
            raise KeyAlreadyEnrolled(
                f"the {KEYCHAIN_SERVICE!r} keychain service already holds a "
                f"secret named {name!r}. Nothing was written: replacing it "
                f"would make every artifact sealed under the existing key "
                f"permanently unreadable."
            )
        if status != 0:
            raise KeychainUnavailable(
                f"the keychain refused to add {name!r} (OSStatus {status}). No "
                f"key was enrolled, and pm-ai will not hold one anywhere else."
            )

    def fetch(self, name: str) -> bytes:
        keyring, KeyringError = _keyring()
        try:
            encoded = keyring.get_password(KEYCHAIN_SERVICE, name)
        except KeyringError as unreachable:
            # Distinct from the `None` below on purpose: this is "could not ask",
            # and answering "no key" here is how an encrypted store gets opened
            # as though it were a fresh install.
            raise KeychainUnavailable(
                f"the keychain refused to answer about {name!r}: {unreachable}. "
                f"Whether a key exists is therefore unknown."
            ) from unreachable
        if encoded is None:
            raise KeyNotFound(
                f"no secret is stored under {name!r} in the {KEYCHAIN_SERVICE!r} "
                f"keychain service."
            )
        try:
            # `validate=True`, because the default silently *discards* invalid
            # characters — a corrupted entry would decode to a differently-sized
            # key and fail later as `KeyUnusable`, pointing at the cipher when
            # the keychain entry is what is broken.
            return base64.b64decode(encoded, validate=True)
        except binascii.Error as corrupt:
            raise KeychainUnavailable(
                f"the secret stored under {name!r} is not the base64 this "
                f"adapter writes ({corrupt}). It was not written by pm-ai's "
                f"`store`, or it has been altered since; refusing to hand back "
                f"bytes that are not the enrolled key."
            ) from corrupt

    def delete(self, name: str) -> None:
        keyring, KeyringError = _keyring()
        try:
            keyring.delete_password(KEYCHAIN_SERVICE, name)
        except KeyringError as unreachable:
            # `keyring` raises `PasswordDeleteError`, a `KeyringError`, when there
            # was nothing to delete — so the two cases arrive as one exception and
            # have to be told apart by asking whether the secret is there. Doing
            # it in this order means a genuinely unreachable keychain still
            # reports unreachable rather than not-found.
            try:
                present = keyring.get_password(KEYCHAIN_SERVICE, name) is not None
            except KeyringError:
                present = True
            if present:
                raise KeychainUnavailable(
                    f"the keychain refused to delete {name!r}: {unreachable}. The "
                    f"secret may still be present."
                ) from unreachable
            raise KeyNotFound(
                f"no secret is stored under {name!r}, so there was nothing to "
                f"delete."
            ) from unreachable
