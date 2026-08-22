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
from dataclasses import dataclass
from typing import Any

from pm_ai.ports import KeychainUnavailable, KeyNotFound

__all__ = ["KEYCHAIN_SERVICE", "MacOSKeychainAdapter"]

# One service name for every secret pm-ai owns, so uninstalling means deleting one
# service's entries rather than hunting for names nobody wrote down.
KEYCHAIN_SERVICE = "pm-ai"


def _keyring() -> tuple[Any, type[BaseException]]:
    """The `keyring` module and its error base, imported at call time.

    Returned as a pair so a caller can name the exception type it must catch
    without a second lazy import, and so a test can substitute both together.
    """
    try:
        import keyring
        from keyring.errors import KeyringError
    except ModuleNotFoundError as missing:
        raise KeychainUnavailable(
            f"the `keyring` package is not installed, so the master key cannot be "
            f"reached ({missing}). It is declared in the `runtime` extra: install "
            f"it with `uv sync --extra runtime`. pm-ai refuses rather than "
            f"treating an unreachable keychain as an empty one."
        ) from missing
    return keyring, KeyringError


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
        return base64.b64decode(encoded)

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
