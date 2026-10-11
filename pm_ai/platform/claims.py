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

Since story `1r` this is the *only* claim in pm-ai. `FlockClaims` is the adapter
the composition root hands the single writer as its `ClaimPort`, and
`StorageService.exclusive` is the one door callers use; the `O_CREAT|O_EXCL`
marker file the credentials store and `meetings/` used to take is gone, because
it is exactly the design this docstring rejects.
"""

from __future__ import annotations

import errno
import fcntl
import os
import stat
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from pm_ai.domain.claims import LOCK_SUFFIX, ClaimHeld, ClaimReentered

__all__ = ["ClaimHeld", "ClaimReentered", "FlockClaims", "LOCK_SUFFIX", "exclusive"]

# `ClaimHeld` and `LOCK_SUFFIX` are declared in `pm_ai.domain.claims` and
# re-exported here. The scope model declares the sidecar and may not import this
# module; the storage service translates the refusal and may not either.

# The claims this thread holds, by canonical sidecar path (AD-5, story 8q). A
# `flock` taken twice in one process through two descriptors conflicts with
# itself, so asking again used to come back as `ClaimHeld` — a busy refusal
# blaming a process that does not exist. Per thread, because another thread
# holding the claim is contention that releases, and stays `ClaimHeld`; it
# cannot see asyncio tasks that share one thread.
_HELD_HERE = threading.local()


def _held_here() -> set[Path]:
    held: set[Path] | None = getattr(_HELD_HERE, "paths", None)
    if held is None:
        held = set()
        _HELD_HERE.paths = held
    return held


# `ENOLCK` is what an NFS mount with no lock daemon answers.
_UNSUPPORTED = frozenset({errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOLCK})


def _mount_of(path: Path) -> Path:
    """The mount point `path` lives on, for a refusal that has to name it."""
    candidate = path
    while not os.path.ismount(candidate) and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


@contextmanager
def exclusive(guarded: Path) -> Iterator[None]:
    """Hold an exclusive claim over `guarded` for the body, or refuse.

    The claim is taken on `<guarded>.lock`, created if absent. Never removed on
    release: unlinking it races another process that has already opened it and
    is waiting to lock it — that process would then hold a claim on an inode
    with no name, and a third would create a new file and claim that, so two
    would believe they held the same lock. An empty lock file left behind costs
    nothing and is the only version of this that is correct.

    `guarded`'s *directory* is canonicalised first, so two spellings of one
    file — through a symlinked directory and through the plain one — name one
    sidecar and so one lock. The name itself is kept: a symlinked artifact is
    not followed, so the sidecar always sits beside the artifact as named and
    never beside a target that may lie outside the scope tree.

    A second claim on `guarded` by the thread already holding it is
    `ClaimReentered`, a programming error naming the path, raised before the
    lock is asked for or any directory created (AD-5, story 8q). The guard is
    per thread: another thread holding it is `ClaimHeld`, and asyncio tasks
    sharing one thread are one holder to it — it cannot tell them apart.
    Refuses with `ClaimHeld` only when another holder has the lock. Everything
    else is an `OSError`: a sidecar that is a link (`O_NOFOLLOW`), a directory
    or unreadable is the operating system's own fault, and a filesystem with no
    `flock` (exFAT, some network mounts) is refused naming the mount — never
    silently unlocked.
    """
    # Re-entry first, before any directory is created, so "nothing was
    # changed" is true. The set is captured here and the same set is the one
    # discarded from on release.
    canonical = guarded.parent.resolve() / guarded.name
    claim = canonical.with_name(canonical.name + LOCK_SUFFIX)
    held = _held_here()
    if claim in held:
        raise ClaimReentered(
            f"this pm-ai thread already holds the claim on {canonical}, and asked "
            f"for it again. That is a bug in pm-ai, not a busy file: a caller "
            f"inside the claim must write under it rather than take it twice. "
            f"Nothing was changed."
        )
    guarded.parent.mkdir(parents=True, exist_ok=True)
    guarded = guarded.parent.resolve() / guarded.name
    claim = guarded.with_name(guarded.name + LOCK_SUFFIX)
    # `O_NONBLOCK` so a sidecar that is a FIFO opens at once rather than waiting
    # for a writer, and is then refused by the plain-file check below. It has no
    # effect on a regular file or on `flock`, which is asked non-blocking anyway.
    descriptor = os.open(
        claim, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        # Darwin opens an existing directory under `O_CREAT` without complaint,
        # and `flock` on it succeeds — so "a plain file" is checked, not assumed.
        # The same check refuses a FIFO or a device.
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise IsADirectoryError(
                errno.EISDIR,
                "the claim's lock file is not a plain file, so pm-ai will not lock "
                "through it; nothing was changed",
                str(claim),
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as taken:
            if taken.errno in _UNSUPPORTED:
                mount = _mount_of(claim.parent)
                raise OSError(
                    taken.errno,
                    f"the filesystem mounted at {mount} does not support file "
                    f"locks, so pm-ai cannot claim {guarded.name} there and wrote "
                    f"nothing. Two runs writing this file at once could each lose "
                    f"the other's changes; move the data to a local filesystem",
                    str(claim),
                ) from taken
            if taken.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise
            raise ClaimHeld(
                f"another pm-ai process is holding {guarded.name} right now. "
                f"Nothing was changed; run the command again."
            ) from taken
        held.add(claim)
        try:
            yield
        finally:
            held.discard(claim)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


class FlockClaims:
    """`ClaimPort` over `fcntl.flock` — the adapter the composition root wires."""

    def exclusive(self, guarded: Path) -> AbstractContextManager[None]:
        return exclusive(guarded)
