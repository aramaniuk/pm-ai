"""The refusal a contended read-modify-write answers with.

The *mechanism* is `fcntl.flock` and lives in `pm_ai.platform.claims`, because
it is POSIX-specific and AD-26 puts OS-specific APIs behind that package. The
*exception* lives here because the CLI has to name it to map it onto an exit
code, and `pm_ai.surfaces` importing the platform module would pull `fcntl` into
every process that merely parses a command line — including, eventually, a
Windows one, where the import fails before any command runs.

`pm_ai.domain` imports nothing from `pm_ai`, so a type declared here is nameable
from every layer without a cycle and without a dependency. This is the same
reason `Health`, `Probe` and `Report` moved here.
"""

from __future__ import annotations

__all__ = ["ClaimHeld"]


class ClaimHeld(RuntimeError):
    """Another process holds the claim, so this one did not take it.

    Refused rather than waited on. A CLI command that blocks with no output
    looks hung, and the holder is a command that takes milliseconds — so "run it
    again" is both true and quick, while an unbounded wait is a terminal an
    operator has to guess about.
    """
