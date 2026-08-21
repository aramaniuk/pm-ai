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
- `assert_capture_dir_ignored` and `GITIGNORE_REQUIRED` — the `.gitignore` check
  the daemon runs before writing a raw capture into a committed scope.
- `EVENT_LOG` and `OPERATIONAL_DB` — the two artifact keys that appear in *code*
  rather than only in a tree, spelled once.

Everything the previous shape exported is re-exported here, so `pm_ai.storage`,
`pm_ai.domain`, and the suite keep importing tiers from the module that owns tier
*behaviour*.

Imports nothing from `pm_ai` outside `pm_ai.domain` (AD-30), and performs no I/O.
"""

from __future__ import annotations

from pm_ai.domain.scope_model import (
    ARTIFACT_TIER,
    BACKUP_TARGETS,
    DIAGNOSTIC_ONLY,
    KEYS,
    REBUILD_TARGETS,
    RETENTION_MANAGED,
    OutsideTierModel,
    ScopeResolutionError,
    Tier,
)

__all__ = [
    "ARTIFACT_TIER",
    "BACKUP_TARGETS",
    "DIAGNOSTIC_ONLY",
    "EVENT_LOG",
    "GITIGNORE_REQUIRED",
    "OPERATIONAL_DB",
    "OutsideTierModel",
    "REBUILD_TARGETS",
    "RETENTION_MANAGED",
    "ScopeResolutionError",
    "Tier",
    "TierViolation",
    "UnprotectedCaptureDir",
    "assert_capture_dir_ignored",
    "assert_reindex_safe",
]


# ── Artifact keys ────────────────────────────────────────────────────────────
# The scope trees spell every key as a literal, so they read like the document
# they mirror. These two are also named in code — `pm_ai.storage.service` appends
# to one and opens the other — rather than only in a tree, so they are spelled
# once here: `domain` is the only package `storage`, `core`, and `surfaces` may
# all import (AD-30), so this is the single home for them rather than a fourth
# copy of the string. The assertion at the bottom of this module is what makes a
# rename of either fail at import instead of at the first write.
EVENT_LOG = "event_log/"
OPERATIONAL_DB = "operational.db"


# `transcripts/` sits INSIDE a committed scope, so its exclusion from git is a
# `.gitignore` rule rather than a directory boundary. A rule can go missing; a
# directory boundary cannot. The daemon therefore verifies the rule before it
# writes a capture, because the failure mode is publishing verbatim meeting
# transcripts to the employer's repository — the same class of leak AD-38 exists
# to prevent, arriving by omission instead of by routing.
GITIGNORE_REQUIRED: dict[str, str] = {
    "transcripts/": "/.project-ai/transcripts/",
}


class UnprotectedCaptureDir(RuntimeError):
    """A capture directory is not excluded from version control."""


def assert_capture_dir_ignored(artifact: str, gitignore_text: str) -> None:
    """Refuse to write a raw capture into a directory git would track.

    Fails closed: no `.gitignore`, no writing. Losing a transcript is recoverable
    (it is transient input under NFR-09 and nothing may depend on it); committing
    one is not.
    """
    rule = GITIGNORE_REQUIRED.get(artifact)
    if rule is None:
        return
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


# The two keys above are literals in this module and literals in the trees, which
# is the one place those two structures still have to agree. A rename of either
# constant therefore fails here, at import, rather than at the first write.
assert {EVENT_LOG, OPERATIONAL_DB} <= KEYS, (
    "a key spelled as a constant here names no node in any scope tree: "
    f"{sorted({EVENT_LOG, OPERATIONAL_DB} - KEYS)}"
)
