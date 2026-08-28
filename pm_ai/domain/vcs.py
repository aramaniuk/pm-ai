"""What version control would do with a path, as a value (AD-23, AD-38).

The one question this module exists to express: *would git carry this path into
a commit?* It is a question only git can answer, and the answer is not derivable
from the text of a `.gitignore`:

- a negation line (`!/.project-ai/transcripts/`) re-includes a directory an
  earlier rule excluded, so the rule is present and the directory is tracked;
- excluding a parent (`.project-ai/`) protects a child no rule ever names;
- a directory already in the index stays tracked no matter what rule is added
  afterwards — `.gitignore` governs *untracked* paths only.

The first two make a text matcher answer the opposite of git in both directions;
the third is invisible to any matcher, because the evidence is in the index and
not in a file. `assert_capture_dir_ignored` in `storage_tiers` is that matcher,
and it stays — as the pure form of the question, no longer as the authority.

`VcsUnavailable` lives here rather than in the adapter because catching it is
`pm_ai.storage`'s job, and `storage` may not import `pm_ai.platform`. Domain is
the one package both sides can name.

Imports nothing from `pm_ai` (AD-30), and performs no I/O — asking git is the
adapter's work, behind `pm_ai.ports.VcsPort`.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["GIT_SUBCOMMANDS", "TrackingVerdict", "UnpermittedGitSubcommand", "VcsUnavailable"]


# The complete set of git subcommands pm-ai may run. Closed here, in domain,
# for the same reason AD-27's event taxonomy and AD-32's verb list are closed
# here: a set that lives where the caller lives grows by whoever is in a hurry.
# The adapter reads this; it does not extend it.
#
# All three are read-only. That is the property worth protecting — AD-1 admits
# `pm_ai.platform` as class L on the strength of it, and a `git rm` or a `git
# checkout` reached through the same helper would be an egress class change
# with no review, in a package the shell scan treats as trusted.
#
# Adding one is a domain change, which is the point: it lands in a diff
# somebody reviews rather than as a new string literal in an adapter.
GIT_SUBCOMMANDS = frozenset({
    "rev-parse",     # locate the working-tree root (AD-43)
    "check-ignore",  # do the exclusion rules cover this path
    "ls-files",      # is this path already in the index
})


class UnpermittedGitSubcommand(ValueError):
    """A git subcommand outside `GIT_SUBCOMMANDS` was attempted.

    A programming error rather than an operational one, so it is raised at the
    call rather than folded into `VcsUnavailable` — a caller that cannot tell
    "git could not answer" from "this code tried to run something it may not"
    would retry the second forever.
    """


class VcsUnavailable(RuntimeError):
    """Git could not be consulted, so nothing is known about this path.

    Raised for every reason the question cannot be answered — no repository, no
    `git` on PATH, a path outside the repository, a command that timed out or
    failed for a reason the adapter does not recognise.

    It is deliberately *not* a verdict. A caller that needed an answer and did
    not get one has to refuse, because the alternative is treating "I could not
    check" as "it is safe", which is how an unprotected directory gets written
    to on a machine where git happens to be missing.
    """


@dataclass(frozen=True, slots=True)
class TrackingVerdict:
    """Git's answer about one path: excluded by the rules, and already indexed.

    Two independent facts, kept separate because they call for two different
    instructions to the operator. A path that is not ignored needs a rule; a
    path already in the index needs `git rm --cached`, and adding a rule to it
    changes nothing at all.
    """

    ignored: bool
    tracked: tuple[str, ...] = ()

    @property
    def is_excluded(self) -> bool:
        """True only when git would carry nothing here into a commit.

        Both halves, in one place, so no caller re-derives the conjunction and
        gets it wrong in the direction that publishes a transcript.
        """
        return self.ignored and not self.tracked
