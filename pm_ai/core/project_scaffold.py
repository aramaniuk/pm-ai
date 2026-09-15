"""The `.gitignore` rules a project scope needs, derived from its declarations.

## Why a project needs them at all

`StorageService._assert_git_excludes` refuses every write to a `GITIGNORED`
artifact that git would carry into a commit (AD-23, AD-43). Story `1n` moved the
project scope's whole `memory/` tree into that set — its event log, meeting
summaries, commitments and dashboard — because a `git pull` rewrites Tier 1
underneath everything derived from it. So inside a repository, a project with no
rule cannot be harvested at all: the first append to its event log is refused.

That is why generating these is mandatory rather than an offer. A project
onboarded without them looks onboarded and works until the first write.

## Derived, never listed

The rules come from `GITIGNORED[ScopeKind.PROJECT]` (`scope_model.py:1018`),
which derives from the per-node `gitignored=` declarations. A second list here
would be a second place to edit when an artifact's declaration changes, and the
two would disagree silently — in the direction that commits a transcript.

Only the shallowest members are emitted. `memory/commitments_log.md` sits under
`memory/`, which is already ignored, so naming both adds a line that changes
nothing and invites the reader to believe the set is hand-maintained. The
reduction is computed from the set rather than hardcoded, so it stays correct
when the declarations move.

## Adoption, not replacement

A repository's `.gitignore` belongs to the team. This module returns the
existing bytes with only the missing rules appended, under a marked header, and
returns them unchanged when nothing is missing — so onboarding an
already-onboarded project touches nothing, which is the guarantee `4k` asserts
by hashing.
"""

from __future__ import annotations

from pm_ai.domain.identity import ScopeKind
from pm_ai.domain.scope_model import GITIGNORED, PROJECT_DIRNAME

__all__ = ["HEADER", "project_rules", "render_gitignore"]

HEADER = f"# pm-ai — machine-local {PROJECT_DIRNAME} artifacts (AD-23, AD-43)"


def project_rules() -> tuple[str, ...]:
    """The rules a repository needs, anchored at its root.

    Anchored with a leading `/` deliberately: an unanchored `memory/` matches a
    `memory/` directory anywhere in the repository, and a project that happened
    to have one would find its own source silently untracked by the act of
    onboarding.
    """
    declared = GITIGNORED[ScopeKind.PROJECT]
    shallowest = sorted(
        key
        for key in declared
        if not any(key != other and key.startswith(other) for other in declared)
    )
    return tuple(f"/{PROJECT_DIRNAME}/{key}" for key in shallowest)


def render_gitignore(existing: bytes | None) -> bytes:
    """`existing` with every missing rule appended — or `existing`, untouched.

    Returns the input bytes identically when every rule is already named, which
    is what makes re-onboarding a no-op rather than a file that grows a block
    each time. The comparison is line-by-line and exact: a rule that git already
    honours through some *other* line is still appended, because only git knows
    that and asking it is `_assert_git_excludes`'s job, not this module's. A
    duplicate rule in a `.gitignore` is inert; a missing one is a committed
    transcript.
    """
    text = "" if existing is None else existing.decode("utf-8", errors="replace")
    present = {line.strip() for line in text.splitlines()}
    missing = [rule for rule in project_rules() if rule not in present]
    if not missing:
        return b"" if existing is None else existing
    # A blank line before the block only when there is something to separate it
    # from, and a trailing newline on a file that did not end with one — an
    # appended rule on the same line as the last existing one would silently
    # change what that line means.
    prefix = "" if not text else ("" if text.endswith("\n") else "\n")
    separator = "" if not text else "\n"
    block = "\n".join([HEADER, *missing]) + "\n"
    return (text + prefix + separator + block).encode("utf-8")
