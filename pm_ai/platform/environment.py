"""The one place the process environment is read (AD-6, AD-26).

Exactly one setting comes from here, and it is the most dangerous one pm-ai has:
whether to write the encrypted set in plaintext. `SPEC.md` forecloses every other
way of setting it — no `config.toml` key, no stored debug profile, no CLI flag
that survives a restart — because a persistent switch is one somebody forgets.
The console warning scrolls away within minutes and an event-log entry is
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
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "DISABLE_ENCRYPTION_VAR",
    "TRUTHY",
    "encryption_disabled",
    "local_zone_name",
    "machine_zone",
    "raw_toggle",
    "set_variables",
]

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


def set_variables(names: tuple[str, ...]) -> dict[str, str]:
    """Those of `names` the environment sets, with their values (story 7c).

    The frontier adapter refuses every call while the Anthropic SDK would read
    one of its pinned variables; the composition root hands it this, so the
    environment is still read here alone.
    """
    return {name: os.environ[name] for name in names if name in os.environ}


def machine_zone() -> tzinfo | None:
    """The machine's own zone, read when `display_timezone` is unset (Andrei, 2026-10-21).

    The IANA zone `/etc/localtime` links to, so a clock change is honoured;
    otherwise the offset in force now; `None` when neither can be read. The
    one reader for the drop's header times (11e) and the commitment ledger's
    and Team Activity's meeting day (15a), so they cannot disagree.
    """
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        target = ""
    _, marker, name = target.partition("zoneinfo/")
    if marker and name:
        try:
            return ZoneInfo(name)
        except (ValueError, ZoneInfoNotFoundError):
            pass
    try:
        return datetime.now().astimezone().tzinfo
    except (OSError, ValueError):
        return None


def local_zone_name() -> str | None:
    """This machine's time zone by name (`Europe/Minsk`), or `None` when it has none.

    For `pm-ai logs` (story 4w), which shows times in the zone the C library
    converts with. `TZ` first, as the C library reads it: empty is UTC; a
    leading `:` is dropped; a path names the zone after its `zoneinfo/`. Else
    the zone `/etc/localtime` links to, which is how macOS records the one
    chosen in its settings. A name the zone database does not know (`Foo/Bar`,
    a POSIX rule such as `CET-1CEST`) is no name. Read here because the
    environment is read here alone.
    """
    configured = os.environ.get("TZ")
    if configured is not None:
        if not configured:
            return "UTC"
        return _known_zone(_zone_part(configured.removeprefix(":")))
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return None
    _, marker, name = target.partition("zoneinfo/")
    return _known_zone(name) if marker else None


def _zone_part(spelled: str) -> str | None:
    """A zone name from a `TZ` value: the name itself, or a path's part after `zoneinfo/`."""
    if spelled.startswith("/"):
        _, marker, name = spelled.partition("zoneinfo/")
        return name if marker else None
    return spelled


def _known_zone(name: str | None) -> str | None:
    """`name` when the zone database knows it, else `None`."""
    if not name:
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
    return name
