"""What the tier model *does*, and the artifact keys code spells by name (AD-3, AD-5).

The three tiers themselves, and every artifact's place in them, are declared on
the nodes of the scope trees in `pm_ai.domain.scope_model` — one tier per `File`,
one durability per `Collection` — and `ARTIFACT_TIER`, `BACKUP_TARGETS`,
`REBUILD_TARGETS`, `RETENTION_MANAGED` and `DIAGNOSTIC_ONLY` are derived from
them there. They used to be hand-written here, in a flat table beside a tree that
described structure only, and the cost was three things: two edits in two modules
to add one artifact, a basename key that could not tell personal
`daily_dashboard.md` from project `daily_dashboard.md`, and a pair of import-time
assertions whose only job was to catch the two structures drifting apart.

This module keeps what operates on that model rather than restating it:

- `assert_reindex_safe` — the Tier-3-only guarantee `pm-ai reindex` owes AD-3.
- `assert_capture_dir_untracked` and `GITIGNORE_REQUIRED` — the check
  `pm_ai.storage.service` runs before writing a raw capture into a committed
  scope. Its input is git's own verdict, obtained through `pm_ai.ports.VcsPort`,
  because only git can say what git tracks.
- `assert_capture_dir_ignored` — the same question asked of `.gitignore` text
  alone. Kept, and no longer the authority: a negation line, a parent-directory
  exclude, and a directory already in the index each make it disagree with git,
  the first two in the direction that publishes a transcript. See
  `pm_ai.domain.vcs`.
- `EVENT_LOG`, `OPERATIONAL_DB` and `CAPTURES` — the three artifact keys that
  appear in *code* rather than only in a tree, spelled once.

Everything the previous shape exported is re-exported here, so `pm_ai.storage`,
`pm_ai.domain`, and the suite keep importing tiers from the module that owns tier
*behaviour*.

Imports nothing from `pm_ai` outside `pm_ai.domain` (AD-30), and performs no I/O.
"""

from __future__ import annotations

from pathlib import Path

from pm_ai.domain.identity import ScopeKind
from pm_ai.domain.scope_model import (
    ARTIFACT_TIER,
    BACKUP_TARGETS,
    DIAGNOSTIC_ONLY,
    ENCRYPTED,
    GITIGNORED,
    KEYS,
    REBUILD_TARGETS,
    RETENTION_MANAGED,
    OutsideTierModel,
    ScopeResolutionError,
    Tier,
)
from pm_ai.domain.invariants import InconsistentModel
from pm_ai.domain.vcs import TrackingVerdict

__all__ = [
    "ARTIFACT_TIER",
    "BACKUP_TARGETS",
    "CAPTURES",
    "DIAGNOSTIC_ONLY",
    "EVENT_LOG",
    "GITIGNORE_FILENAME",
    "ENCRYPTED",
    "GITIGNORED",
    "OPERATIONAL_DB",
    "OutsideTierModel",
    "REBUILD_TARGETS",
    "RETENTION_MANAGED",
    "ScopeResolutionError",
    "Tier",
    "TierViolation",
    "UnprotectedCaptureDir",
    "assert_capture_dir_ignored",
    "assert_capture_dir_untracked",
    "assert_reindex_safe",
    "gitignore_rule_for",
    "requires_git_exclusion",
]


# ── Artifact keys ────────────────────────────────────────────────────────────
# The scope trees spell every key as a literal, so they read like the document
# they mirror. These three are also named in code — `pm_ai.storage.service`
# appends to the first, opens the second, and refuses to write into the third
# unless git excludes it — rather than only in a tree, so they are spelled once
# here: `domain` is the only package `storage`, `core`, and `surfaces` may all
# import (AD-30), so this is the single home for them rather than a fourth copy
# of the string. The assertion at the bottom of this module is what makes a
# rename of any of them fail at import instead of at the first write.
EVENT_LOG = "event_log/"
OPERATIONAL_DB = "operational.db"

# Raw captures. Homed in every scope at the same relative path, so the key alone
# does not say whether git can see it — `GITIGNORE_REQUIRED` below is what pairs
# it with the one scope where that question has an answer.
CAPTURES = "transcripts/"


# `transcripts/` sits INSIDE a committed scope, so its exclusion from git is a
# `.gitignore` rule rather than a directory boundary. A rule can go missing; a
# directory boundary cannot. The daemon therefore verifies the rule before it
# writes a capture, because the failure mode is publishing verbatim meeting
# transcripts to the employer's repository — the same class of leak AD-38 exists
# to prevent, arriving by omission instead of by routing.
def requires_git_exclusion(scope_kind: ScopeKind, artifact: str) -> bool:
    """Whether writing `artifact` in this scope must ask git first.

    Replaces the module-level `GITIGNORE_REQUIRED` frozenset, which was keyed on
    the artifact basename alone and therefore global. That held only while the
    set had one member: `transcripts/` wants the same answer in all three scopes
    that declare it. `event_log/` does not — it is inside the gitignored
    team-member enclave and committed to the repository in a project — so a
    basename-keyed set had the same defect the encryption axis exposed, one
    artifact away from mattering.

    The answer is declared on the node, per tree, and derived into `GITIGNORED`.
    """
    return artifact in GITIGNORED[scope_kind]

# Named here rather than in `pm_ai.platform`, because `pm_ai.storage` derives
# the path from git's reported working-tree root and may not import that
# package. One definition, read by both.
GITIGNORE_FILENAME = ".gitignore"


def gitignore_rule_for(target: Path, *, repository: Path) -> str:
    """The `.gitignore` line that would exclude `target` from `repository`.

    Derived, never stored. The rule depends on where the capture directory sits
    relative to its working-tree root, and that differs per scope: a project
    capture wants `/.project-ai/transcripts/`, a personal one at the root of its
    own private repository wants `/transcripts/`. A table could not hold both,
    because `GITIGNORE_REQUIRED` is keyed on the artifact *basename* and every
    scope spells captures the same way — so one key would have to carry two
    values. Re-keying the tier tables on `(scope, path)` is the change AD-44
    defers; deriving the rule sidesteps needing it at all.

    Anchored with a leading slash so it matches at the repository root only, and
    trailing so it reads as the directory it is.
    """
    return "/" + target.relative_to(repository).as_posix() + "/"


class UnprotectedCaptureDir(RuntimeError):
    """A capture directory is not excluded from version control."""


def assert_capture_dir_untracked(
    artifact: str, verdict: TrackingVerdict, *, rule: str, gitignore: str
) -> None:
    """Refuse to write a raw capture git would carry into a commit.

    `verdict` is git's own answer, from `pm_ai.ports.VcsPort`. This function
    turns it into the refusal and the instruction that repairs it, which is the
    part that belongs in the domain: `storage` owns the plumbing, not the rule.

    Fails closed by construction — it is only ever called with an answer. A
    caller that could not get one must refuse instead of calling this with a
    guess (see `VcsUnavailable`).

    `rule` and `gitignore` are both for the message, and both come from the
    caller rather than a lookup here — the rule that repairs this depends on
    where the capture sits inside its working tree, which the domain cannot know
    and `gitignore_rule_for` derives. Naming them is the difference between an
    error the operator can act on and one they have to go looking for.

    The two branches are two different repairs. A rule does not untrack what is
    already in the index, so telling someone to add one when the real problem is
    a tracked directory sends them to fix a file that is already correct.
    """
    if verdict.is_excluded:
        return
    if verdict.tracked:
        raise UnprotectedCaptureDir(
            f"{artifact} holds raw captures and git already tracks "
            f"{len(verdict.tracked)} file(s) under it, including "
            f"{verdict.tracked[0]!r}. A .gitignore rule does not untrack what is "
            f"already in the index: run `git rm -r --cached` on that directory "
            f"and commit the removal first. Refusing to write — a verbatim "
            f"transcript in the team's repository is not recoverable."
        )
    raise UnprotectedCaptureDir(
        f"{artifact} holds raw captures and lives inside a committed scope, but "
        f"git does not exclude it. Add {rule!r} to {gitignore}, and check for a "
        f"later negation line (`!{rule}`) — that re-includes the directory an "
        f"earlier rule excluded. Refusing to write: a verbatim transcript in the "
        f"team's repository is not recoverable."
    )


def assert_capture_dir_ignored(artifact: str, gitignore_text: str, *, rule: str) -> None:
    """The `.gitignore` text alone, asked the same question. Not the authority.

    Retained as the pure form of the check — no filesystem, no subprocess, one
    string in — and because it is the shape the rule was first written in. The
    write path uses `assert_capture_dir_untracked` instead: this function reads
    text, and text cannot see a negation line's effect, a parent-directory
    exclude, or an index. See `pm_ai.domain.vcs` for all three.

    Fails closed on what it can see: no `.gitignore`, no writing. Losing a
    transcript is recoverable (it is transient input under NFR-09 and nothing may
    depend on it); committing one is not.
    """
    lines = {ln.strip().rstrip("/") for ln in gitignore_text.splitlines() if ln.strip()}
    if rule.rstrip("/") not in lines and rule.lstrip("/").rstrip("/") not in lines:
        raise UnprotectedCaptureDir(
            f"{artifact} holds raw captures and lives inside a committed scope, but "
            f"{rule!r} is not in .gitignore. Refusing to write: a verbatim transcript "
            f"in the team's repository is not recoverable."
        )


class TierViolation(ValueError):
    """An operation reached an artifact outside the tier it is allowed to touch."""


def assert_reindex_safe(artifacts: frozenset[str]) -> None:
    """`pm-ai reindex` may delete and rebuild Tier 3, and nothing else.

    The check is on the artifact set rather than on intent, so a reindex that
    grows a new target cannot quietly acquire a Tier-2 file.
    """
    trespass = {a for a in artifacts if ARTIFACT_TIER.get(a) is not Tier.DERIVED}
    if trespass:
        raise TierViolation(
            f"reindex would touch {sorted(trespass)}, which are not Tier 3. "
            f"Pending external writes and connector cursors live there and no "
            f"rebuild can reconstruct them (AD-3)."
        )


# The keys above are literals in this module and literals in the trees, which is
# the one place those two structures still have to agree. A rename of any of them
# therefore fails here, at import, rather than at the first write.
_CODE_KEYS = frozenset({EVENT_LOG, OPERATIONAL_DB, CAPTURES})


def _assert_code_keys_are_declared() -> None:
    """Both checks, callable, so a test can re-run them under `python -O`."""
    if not _CODE_KEYS <= KEYS:
        raise InconsistentModel(
            "a key spelled as a constant here names no node in any scope tree: "
            f"{sorted(_CODE_KEYS - KEYS)}"
        )

    # And every gitignored artifact must name a node, in the scope that declares
    # it: a rule whose artifact does not exist is a rule the write path can never
    # consult, which reads as protection and is silence.
    stray = {a for keys in GITIGNORED.values() for a in keys} - KEYS
    if stray:
        raise InconsistentModel(
            f"a gitignored artifact names no node in any scope tree: {sorted(stray)}"
        )


_assert_code_keys_are_declared()
