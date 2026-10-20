"""The Anthropic API key, held in the sealed credential file (story 7c).

The spec names `~/.pm-ai/private/config.json` — sealed under the master key —
for API credentials, so the key lives there, not in a keychain item of its
own. It sits under its own top-level key, `FRONTIER_KEY`, beside connector
enrolment's `connectors`: enrolment's read-modify-write keeps every top-level
key it did not put there, and no connector listing reads this one.

`seal_frontier_key` is the read-modify-write under the store's claim (AD-5): in
the daemon it is one claimed step, handed to the writer thread after the key
was checked with Anthropic (`pm-ai frontier key`). `held_frontier_key` is a
plain read. Neither logs or returns the key in a sentence.

I/O-free: everything goes through `StoragePort`.
"""

from __future__ import annotations

from pm_ai.core.connector_enrolment import (
    APPLICATION,
    CREDENTIAL_STORE,
    _decode,
    _encode,
)
from pm_ai.domain.health import Health, Probe
from pm_ai.ports import StoragePort

__all__ = [
    "assert_sealable",
    "FRONTIER_KEY",
    "FRONTIER_KEY_PROBE",
    "frontier_key_probe",
    "held_frontier_key",
    "seal_frontier_key",
]

FRONTIER_KEY = "frontier"
"""The sealed store's top-level key the Anthropic API key is held under."""

FRONTIER_KEY_PROBE = "frontier key"
"""`pm-ai doctor`'s line about the key: held, or `ABSENT`."""

_COMMAND = "pm-ai frontier key"


def held_frontier_key(storage: StoragePort) -> str | None:
    """The key held, or `None` when none is. Refusals reading the store propagate."""
    document = _decode(storage.read_artifact(scope=APPLICATION, artifact=CREDENTIAL_STORE))
    held = document.get(FRONTIER_KEY)
    if not isinstance(held, dict):
        return None
    key = held.get("api_key")
    return key if isinstance(key, str) and key else None


def seal_frontier_key(storage: StoragePort, key: str, *, claimed: bool = False) -> None:
    """Write `key` under `FRONTIER_KEY`, keeping every other key in the store as it was.

    Taken under the store's claim unless the caller says it holds it. A blank
    key is refused before anything is read.
    """
    if not key.strip():
        raise ValueError("a blank key is not sealed.")
    if not claimed:
        with storage.exclusive(scope=APPLICATION, artifact=CREDENTIAL_STORE):
            seal_frontier_key(storage, key, claimed=True)
        return
    document = _decode(storage.read_artifact(scope=APPLICATION, artifact=CREDENTIAL_STORE))
    document[FRONTIER_KEY] = {"api_key": key}
    storage.write_artifact(_encode(document), scope=APPLICATION, artifact=CREDENTIAL_STORE)


def assert_sealable(storage: StoragePort) -> None:
    """Refuse now, contacting nothing, when a key could not be sealed (story 7c).

    Asks what the seal will ask: whether the sealed store may be written here
    (the master key enrolled, version control's answer) and whether what it
    holds can be read back and kept. Refusals propagate by their own type —
    `KeyNotFound`, `DecryptionFailed`, `MalformedSettings` — so nothing is
    asked of Anthropic for a key that would then have nowhere to go.
    """
    storage.assert_writable(scope=APPLICATION, artifact=CREDENTIAL_STORE)
    _decode(storage.read_artifact(scope=APPLICATION, artifact=CREDENTIAL_STORE))


def frontier_key_probe(storage: StoragePort) -> Probe:
    """The `frontier key` line: held, or none held and how to add one. Contacts nothing.

    A missing key is not a failure (Andrei, 2026-10-20, as for an unset local
    model on 2026-10-19): with none held the line is `OK`, saying how to add
    one, and `doctor`'s exit code is unaffected. Only a store that cannot be
    read affects it — a `WARNING` naming why. Never says the key.
    """
    try:
        held = held_frontier_key(storage)
    except Exception as unreadable:  # noqa: BLE001 — a probe reports, never raises
        return Probe(
            FRONTIER_KEY_PROBE,
            Health.WARNING,
            f"the sealed credential store could not be read, so whether a key is "
            f"held is unknown ({type(unreadable).__name__}).",
            "The keychain and credentials lines say why.",
        )
    if held is None:
        return Probe(
            FRONTIER_KEY_PROBE,
            Health.OK,
            "none held, so `transcript summarize --by claude` is refused; not a failure.",
            f"To add one: start the daemon (`pm-ai daemon run`), then run `{_COMMAND}`.",
        )
    return Probe(
        FRONTIER_KEY_PROBE,
        Health.OK,
        "an Anthropic API key is held in the sealed credential store.",
    )
