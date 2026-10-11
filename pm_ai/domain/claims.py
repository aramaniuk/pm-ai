"""The refusal a contended read-modify-write answers with, and the sidecar's name.

The *mechanism* is `fcntl.flock` and lives in `pm_ai.platform.claims`, because
it is POSIX-specific and AD-26 puts OS-specific APIs behind that package. The
storage service reaches it through `ClaimPort`, which the composition root hands
in, and translates its refusal into `ArtifactBusy` where the claim is taken
(story `1r`). So nothing outside `pm_ai.storage` and the platform adapter needs
to name `ClaimHeld` at all.

`LOCK_SUFFIX` lives here rather than in the platform module because the scope
model declares the sidecar it names, and `pm_ai.core.project_scaffold` derives
an ignore rule from it — neither may import `pm_ai.platform`.

`pm_ai.domain` imports nothing from `pm_ai`, so a type declared here is nameable
from every layer without a cycle and without a dependency.
"""

from __future__ import annotations

__all__ = ["ClaimHeld", "LOCK_SUFFIX"]

LOCK_SUFFIX = ".lock"
"""The suffix of the empty sidecar a claim is taken on: `<name>.lock`.

Beside the file it guards, never inside it — locking the file itself would hold
a descriptor onto an inode `os.replace` is about to swap out — and never
deleted, because unlinking it races a process already waiting on it.
"""


class ClaimHeld(RuntimeError):
    """Another process holds the claim, so this one did not take it.

    The platform spelling of a busy file. `StorageService.exclusive` translates
    it into `ArtifactBusy` once, at the point the claim is taken, and no command
    catches it: a caller that sees `ClaimHeld` escape is looking at a bug in that
    translation, not at a busy file.

    Refused rather than waited on. A CLI command that blocks with no output
    looks hung, and the holder is a command that takes milliseconds — so "run it
    again" is both true and quick.
    """
