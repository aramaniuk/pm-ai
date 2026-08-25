"""Startup probes for the failures that cannot happen on the developer's machine.

Each of these is near-certain on somebody else's and invisible until the moment
it matters. They share a shape: installation succeeds, the daemon starts, and the
thing that breaks is one specific capability, silently.

- **sqlite extension support.** The vector index loads only into an interpreter
  whose connection exposes `enable_load_extension`. Stock macOS and python.org
  builds do not have it — absent, not disabled. Then the first write touching the
  index fails deep inside the storage layer.
- **Keychain reachability.** Breaks after an OS or interpreter upgrade. Silent,
  unattended, and it presents as the morning briefing simply not arriving.
- **The encryption toggle.** Disabled means credentials are being written in
  plaintext right now.
- **`git`.** A hard runtime dependency of the capture write path: without it every
  capture is refused, and *nothing else changes* — harvests, briefings and the
  CLI all keep working, so the one thing that stops is the one nobody notices
  stopping until a meeting has already happened.

## Two rules shape all of it

**Every probe reports; none raises.** A caller sees the whole picture in one
pass, and one broken thing cannot hide three others. That is why an unreachable
keychain returns a result instead of an exception.

**Every failure names its remediation.** "Missing `enable_load_extension`" is
useless without "install a uv-managed interpreter". A probe that only says what
is wrong makes the operator guess at what to do, and guessing is how a machine
gets a second key written over a store that could still have been opened.

No repair actions, ever: probes are read-only and never create, migrate, or fix
anything. And no `pm-ai doctor` subcommand here — `pyproject.toml` declares no
console entry point and the CLI is story 4. This ships callables plus a
`python -m pm_ai.platform.doctor` runner, and story 4 surfaces them.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pm_ai.platform.environment import DISABLE_ENCRYPTION_VAR, TRUTHY, raw_toggle

__all__ = ["Health", "Probe", "Report", "run_all"]


class Health(Enum):
    """Four states, because three of them are not failures.

    `ABSENT` is separate from `FAILING` because "reachable, nothing stored" is an
    ordinary first-run state and "cannot reach it at all" is a broken machine.
    Collapsing them would tell an operator to fix a keychain that is fine.

    `WARNING` is separate from `OK` because encryption being off is not healthy
    even though nothing is broken — and separate from `FAILING` because the
    daemon is doing exactly what it was told to.
    """

    OK = "ok"
    WARNING = "warning"
    ABSENT = "absent"
    FAILING = "failing"

    @property
    def is_healthy(self) -> bool:
        return self is Health.OK


@dataclass(frozen=True, slots=True)
class Probe:
    """One question asked and answered, with what to do about the answer."""

    name: str
    health: Health
    detail: str
    remediation: str = ""

    def __str__(self) -> str:
        line = f"[{self.health.value:>7}] {self.name}: {self.detail}"
        return f"{line}\n          → {self.remediation}" if self.remediation else line


@dataclass(frozen=True, slots=True)
class Report:
    probes: tuple[Probe, ...]

    @property
    def healthy(self) -> bool:
        """False if anything is not `OK` — a warning is not a pass.

        Encryption disabled is the case this exists for: the daemon works, and a
        report that called it healthy would be the summary an operator trusts
        while credentials sit in plaintext.
        """
        return all(p.health.is_healthy for p in self.probes)

    def __str__(self) -> str:
        verdict = "healthy" if self.healthy else "NOT healthy"
        return "\n".join([*(str(p) for p in self.probes), "", f"pm-ai is {verdict}."])


# ── The probes ───────────────────────────────────────────────────────────────


def sqlite_extension_support() -> Probe:
    """Whether this interpreter can load the vector-search extension at all.

    Tests the attribute on a real connection object rather than the version
    string. The failure is a property of how the interpreter was *built*, not of
    which version it is — a correct version compiled without the feature passes
    every version check and fails on first use, which is exactly the sequence
    this probe exists to break.
    """
    name = "sqlite extension support"
    try:
        with sqlite3.connect(":memory:") as connection:
            supported = hasattr(connection, "enable_load_extension")
    except sqlite3.Error as broken:  # pragma: no cover - a broken sqlite3 module
        return Probe(name, Health.FAILING, f"sqlite3 is unusable: {broken}",
                     "Reinstall the interpreter; nothing here can proceed without sqlite.")
    if supported:
        return Probe(name, Health.OK, "the interpreter exposes enable_load_extension")
    return Probe(
        name,
        Health.FAILING,
        "this interpreter's sqlite3 has no enable_load_extension, so the vector "
        "index cannot be loaded",
        "Install a uv-managed interpreter (`[tool.uv] python-preference = "
        "\"only-managed\"` is set for this reason). Stock macOS and python.org "
        "builds omit the feature — installation will succeed and the first write "
        "touching the index will fail inside the storage layer.",
    )


def keychain_reachable(keychain, key_name: str = "master") -> Probe:
    """Whether the master key can be reached, and whether one is stored.

    `keychain` is injected rather than constructed so the probe can be exercised
    against every failure a real keychain has, none of which a test may provoke
    for real.

    The key's value is never placed in the result. A diagnostic that prints a
    secret turns a support request into a disclosure.
    """
    from pm_ai.ports import KeychainUnavailable, KeyNotFound

    name = "keychain"
    try:
        keychain.fetch(key_name)
    except KeyNotFound:
        return Probe(
            name,
            Health.ABSENT,
            f"the keychain is reachable and holds no key named {key_name!r}",
            "Enrol the master key before running pm-ai. The key is configured as "
            "a setup step, never minted by the daemon: a new key makes every "
            "previously sealed artifact unreadable.",
        )
    except KeychainUnavailable as unreachable:
        return Probe(
            name, Health.FAILING, f"the keychain could not be consulted: {unreachable}",
            "Encrypted artifacts cannot be read or written until this is fixed. "
            "If the message names a missing package, install the `runtime` extra.",
        )
    return Probe(name, Health.OK, f"a key named {key_name!r} is present and readable")


def encryption_toggle() -> Probe:
    """Whether this process is writing the encrypted set in plaintext.

    Three outcomes rather than two. Unset is healthy; a recognised value is a
    warning, because the daemon is doing what it was told and that is not the same
    as healthy; and an **unrecognised** value is its own report, because whoever
    exported `PM_AI_DISABLE_ENCRYPTION=please` believes they disabled encryption
    and did not. That confusion has nowhere else to surface.
    """
    name = "encryption"
    value = raw_toggle()
    if value is None:
        return Probe(name, Health.OK, "enabled")
    if value.strip().lower() in TRUTHY:
        return Probe(
            name,
            Health.WARNING,
            f"DISABLED by {DISABLE_ENCRYPTION_VAR}={value!r} — credentials and "
            f"voice notes are being written in plaintext",
            "Short-term debugging only. Unset the variable and restart; there is "
            "no persistent way to disable encryption and restarting restores it.",
        )
    return Probe(
        name,
        Health.WARNING,
        f"enabled, but {DISABLE_ENCRYPTION_VAR}={value!r} is not a value this "
        f"recognises, so it is having no effect",
        f"Use one of {sorted(TRUTHY)} if disabling was intended, or unset the "
        f"variable. Encryption is on either way — this reports the mismatch "
        f"because silently ignoring it looks identical to honouring it.",
    )


def git_available(timeout_seconds: float = 10.0) -> Probe:
    """Whether `git` is present *and* able to answer, reported separately.

    `shutil.which` proves a file exists. The capture guard needs git to *answer a
    question*, and the ways that fails — a build without `check-ignore`, a wrapper
    that shells elsewhere, a `PATH` entry pointing at a stub — all pass a `which`
    check. So one real query is run, and absent is reported differently from
    present-but-unanswering: the first is an install, the second an investigation.
    """
    name = "git"
    binary = shutil.which("git")
    if binary is None:
        return Probe(
            name, Health.FAILING, "no `git` on this process's PATH",
            "Every capture write will be refused and nothing else will change — "
            "harvests, briefings and the CLI keep working, so this is silent. "
            "Install git, or add it to the daemon's PATH (`launchd` supplies a "
            "minimal one).",
        )
    try:
        version = subprocess.run(
            [binary, "--version"], capture_output=True, text=True,
            timeout=timeout_seconds, check=False,
        )
        answering = subprocess.run(
            [binary, "check-ignore", "--quiet", "--", "probe/"],
            cwd=Path.home(), capture_output=True, text=True,
            timeout=timeout_seconds, check=False,
        )
    except (OSError, subprocess.SubprocessError) as unusable:
        return Probe(
            name, Health.FAILING, f"`git` is present at {binary} but unusable: {unusable}",
            "Distinct from git being absent: something is there and cannot answer. "
            "Check that the binary on PATH is really git.",
        )
    # `check-ignore` exits 0 when a path is ignored, 1 when it is not, and 128
    # outside a repository — all three mean git answered. Anything else is a git
    # this codebase does not understand.
    if answering.returncode not in (0, 1, 128):
        return Probe(
            name, Health.FAILING,
            f"`git check-ignore` exited {answering.returncode}: "
            f"{answering.stderr.strip() or 'no output'}",
            "git is installed but cannot answer the exclusion question the capture "
            "guard asks, so captures will be refused. Investigate this binary "
            "rather than installing another.",
        )
    return Probe(name, Health.OK, f"{version.stdout.strip() or binary} answers exclusion queries")


def run_all(keychain=None) -> Report:
    """Every probe, whatever any single one of them says.

    Sequential and independent on purpose: one failure must not stop the others,
    or an operator fixes one thing at a time across four restarts.
    """
    if keychain is None:  # pragma: no cover - the real adapter, not used in tests
        from pm_ai.platform.keychain import MacOSKeychainAdapter

        keychain = MacOSKeychainAdapter()
    return Report(
        (
            sqlite_extension_support(),
            keychain_reachable(keychain),
            encryption_toggle(),
            git_available(),
        )
    )


def main() -> int:
    report = run_all()
    print(report)
    return 0 if report.healthy else 1


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
