"""The one place the process environment is read (AD-6, AD-26).

Exactly one setting comes from here, and it is the most dangerous one pm-ai has:
whether to write the encrypted set in plaintext. `SPEC.md` forecloses every other
way of setting it — no `config.toml` key, no stored debug profile, no CLI flag
that survives a restart — because a persistent switch is one somebody forgets.
The console warning scrolls away within minutes and a startup event-log entry is
weeks old by the time anyone wonders why a credential file is readable. An
environment variable dies with the process, so **restarting restores encryption
unconditionally**: no expiry mechanism, no re-announcement schedule, nothing to
audit.

That supersedes the spine's Deployment note, which put a debug profile in
`~/.pm-ai/config.toml` toggling "encryption and verbose logging". Verbose logging
may live there; encryption may not.

Why a module of its own rather than a line in `wiring.py`: two callers need the
same answer — the composition root, which acts on it, and `pm-ai doctor`, which
reports it — and a flag this consequential must not be read in two places that
could disagree. Reading ambient OS state also belongs in `pm_ai.platform` under
AD-26, and a static rule keeps it here.
"""

from __future__ import annotations

import os

__all__ = ["DISABLE_ENCRYPTION_VAR", "TRUTHY", "encryption_disabled", "raw_toggle"]

DISABLE_ENCRYPTION_VAR = "PM_AI_DISABLE_ENCRYPTION"

# An explicit allowlist, not "any non-empty string". `PM_AI_DISABLE_ENCRYPTION=0`
# reads to a human as *off*, and a truthiness test would read it as *on* — the
# one direction this flag must never fail in.
TRUTHY = frozenset({"1", "true", "yes", "on"})


def raw_toggle() -> str | None:
    """The variable exactly as the environment holds it, or `None` if unset.

    Unparsed, because `pm-ai doctor` has to distinguish three states a boolean
    cannot carry: unset, set to something recognised, and set to something that
    is *not* recognised. The third matters — someone who exported
    `PM_AI_DISABLE_ENCRYPTION=please` believes they disabled encryption and did
    not, and the only place that confusion can surface is a probe that saw the
    original string.
    """
    return os.environ.get(DISABLE_ENCRYPTION_VAR)


def encryption_disabled() -> bool:
    """Whether this process writes the encrypted set in plaintext.

    Fails secure: anything unrecognised leaves encryption **on**. A wrong value
    therefore costs a confused developer, not a plaintext credential file, and
    the probe is what tells them which happened.
    """
    value = raw_toggle()
    return value is not None and value.strip().lower() in TRUTHY
