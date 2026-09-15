"""An exclusive claim over one file, for a read-modify-write that must not race.

`write_artifact` publishes with `os.replace` (`service.py:1002`), which keeps the
last writer whole and discards the other. That is the right primitive for an
artifact one process owns, and the wrong one for `projects.toml`, which grows by
entries: two `pm-ai project add` runs that both read a one-entry registry both
render a two-entry one, and whichever lands second is the only one that survives.
The operator sees two successes and one project.

Why `fcntl.flock` rather than an `O_CREAT | O_EXCL` lock file: a lock file
records that *somebody* claimed it, and nothing releases it when that somebody is
killed. The next run then refuses forever against a process that no longer
exists, and the remedy is "delete this file", which is a remedy an operator
guesses at. A `flock` is held by the open file description and released by the
kernel when the process dies, however it dies.

POSIX-only, which is why it lives here: AD-26 puts OS-specific APIs behind
`pm_ai.platform` so the Windows port is an adapter rather than a rewrite. The
lock file is a sibling of what it guards rather than the file itself — locking
the registry directly would mean holding a descriptor onto an inode that
`os.replace` is about to swap out from under it.
"""

from __future__ import annotations

import errno
import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pm_ai.domain.claims import ClaimHeld

__all__ = ["ClaimHeld", "LOCK_SUFFIX", "exclusive"]

LOCK_SUFFIX = ".lock"

# `ClaimHeld` is declared in `pm_ai.domain.claims` and re-exported here. The CLI
# maps it onto an exit code and so must name it, and `pm_ai.surfaces` importing
# *this* module would pull `fcntl` into every process that parses a command
# line. The mechanism is POSIX-specific; the refusal is not.


@contextmanager
def exclusive(guarded: Path) -> Iterator[None]:
    """Hold an exclusive claim over `guarded` for the body, or refuse.

    The claim is taken on `<guarded>.lock`, created if absent. Never removed on
    release: unlinking it races another process that has already opened it and
    is waiting to lock it — that process would then hold a claim on an inode
    with no name, and a third would create a new file and claim that, so two
    would believe they held the same lock. An empty lock file left behind costs
    nothing and is the only version of this that is correct.
    """
    guarded.parent.mkdir(parents=True, exist_ok=True)
    claim = guarded.with_name(guarded.name + LOCK_SUFFIX)
    descriptor = os.open(claim, os.O_RDONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as taken:
            if taken.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise ClaimHeld(
                f"another pm-ai process is holding {guarded.name} right now. "
                f"Nothing was changed; run the command again. Two runs writing "
                f"this file at once would each keep their own version of it and "
                f"one set of changes would be lost without either run saying so."
            ) from taken
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
