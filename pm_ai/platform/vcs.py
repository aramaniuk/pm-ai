"""Asking git what git would do (AD-1, AD-23, AD-26, AD-38).

The adapter behind `pm_ai.ports.VcsPort`. It lives here because answering the
question means running `git`, and `.importlinter` confines `subprocess` to this
package and `pm_ai.models.local`. `pm_ai.storage` — the caller — may import
neither, which is the whole reason the port exists.

Two commands, one per half of the verdict:

- `git check-ignore` for the exclusion rules. Deliberately *without*
  `--no-index`: the default consults the index, so a path already tracked is
  correctly reported as not ignored. `--no-index` answers a different question —
  "what do the rules say in the abstract" — and answering that one would let a
  capture directory that is already committed read as protected.
- `git ls-files` for the index itself. Redundant with the above in most cases and
  kept anyway, because it is the half that can *name* the tracked files, and
  "which file" is what turns the refusal into an instruction.

Every failure is a refusal, never a default. `VcsUnavailable` carries the reason
so the writer's refusal can repeat it: an operator told "git could not be
consulted" and nothing else will go looking in the wrong place.

Timeouts are bounded because this runs inside a write path. A hung `git` on a
network filesystem would otherwise hang the single writer, and a hang is the one
failure mode that neither refuses nor succeeds.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pm_ai.domain.vcs import TrackingVerdict, VcsUnavailable

__all__ = ["GIT_TIMEOUT_SECONDS", "GitVcs"]

# Bounded because the caller is the single writer. Generous enough for a cold
# index on a slow disk, short enough that a wedged `git` is an error rather than
# a stalled daemon.
GIT_TIMEOUT_SECONDS = 10

# `check-ignore` exit codes, from git's own documentation: 0 one or more paths are
# ignored, 1 none are, 128 a fatal error. Anything else is a git we do not
# understand, which is a refusal like every other unknown.
_IGNORED = 0
_NOT_IGNORED = 1


@dataclass(frozen=True, slots=True)
class GitVcs:
    """Satisfies `pm_ai.ports.VcsPort` by asking the `git` on PATH.

    Stateless and frozen: one instance serves every repository, because the
    repository is an argument rather than a property of the adapter — the daemon
    holds several projects at once.
    """

    def tracking(self, path: Path, *, repository: Path) -> TrackingVerdict:
        """Git's verdict on `path`, as seen from `repository`."""
        target = self._as_git_path(path)
        ignored = self._check_ignore(target, repository=repository)
        tracked = self._ls_files(target, repository=repository)
        return TrackingVerdict(ignored=ignored, tracked=tracked)

    # ── The two questions ────────────────────────────────────────────────────

    def _check_ignore(self, target: str, *, repository: Path) -> bool:
        completed = self._git("check-ignore", "--quiet", "--", target, repository=repository)
        if completed.returncode == _IGNORED:
            return True
        if completed.returncode == _NOT_IGNORED:
            return False
        raise VcsUnavailable(
            f"`git check-ignore` in {repository} exited "
            f"{completed.returncode}: {completed.stderr.strip() or 'no output'}. "
            f"Whether git would commit {target} is therefore unknown, and unknown "
            f"is not permission."
        )

    def _ls_files(self, target: str, *, repository: Path) -> tuple[str, ...]:
        completed = self._git("ls-files", "-z", "--", target, repository=repository)
        if completed.returncode != 0:
            raise VcsUnavailable(
                f"`git ls-files` in {repository} exited {completed.returncode}: "
                f"{completed.stderr.strip() or 'no output'}. Whether {target} is "
                f"already in the index is therefore unknown, and a rule does not "
                f"untrack what is."
            )
        return tuple(entry for entry in completed.stdout.split("\0") if entry)

    # ── Running it ───────────────────────────────────────────────────────────

    @staticmethod
    def _as_git_path(path: Path) -> str:
        """The path as git must be asked about it: a directory keeps its slash.

        `Path` drops a trailing slash, and git does not treat the two forms
        alike for a path that does not exist yet — which is every first capture
        write. Asked about `<repo>/.project-ai/transcripts`, git answers "not
        ignored" for a repository whose `.gitignore` excludes
        `/.project-ai/transcripts/`; asked with the slash, it answers "ignored".
        Without this the guard refuses every correctly protected repository until
        someone happens to create the directory by hand.
        """
        rendered = str(path)
        return rendered if rendered.endswith("/") else rendered + "/"

    def _git(self, *arguments: str, repository: Path) -> subprocess.CompletedProcess[str]:
        """Run one git command in `repository`, or raise `VcsUnavailable`.

        Every precondition is checked before the call so the reason survives into
        the message. `git -C <missing>` and `git -C <not-a-repo>` both exit 128
        with a message about `.git`, which sends an operator looking for a
        `.gitignore` problem in a directory that is not there at all.
        """
        git = shutil.which("git")
        if git is None:
            raise VcsUnavailable(
                "no `git` on PATH, so whether git would commit a raw capture "
                "cannot be established. pm-ai refuses the write rather than "
                "guessing; install git or unregister the project."
            )
        if not repository.is_dir():
            raise VcsUnavailable(
                f"the repository registered for this project, {repository}, is "
                f"not a directory on disk. Nothing can be asked of git there — "
                f"this is a registry that has gone stale, not a missing "
                f".gitignore rule."
            )
        try:
            # argv list and the default shell=False: AD-1 permits this package to
            # spawn a process, not to build a command line a path could inject
            # into. `-C` rather than `cwd=` so git's own repository discovery is
            # what resolves the root.
            return subprocess.run(  # noqa: S603 — argv list, shell=False, fixed binary
                [git, "-C", str(repository), *arguments],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise VcsUnavailable(
                f"`git {arguments[0]}` in {repository} did not finish within "
                f"{GIT_TIMEOUT_SECONDS}s. This runs inside a write path, so it is "
                f"refused rather than waited on."
            ) from exc
        except OSError as exc:
            raise VcsUnavailable(
                f"could not run `git {arguments[0]}` in {repository}: {exc}."
            ) from exc
