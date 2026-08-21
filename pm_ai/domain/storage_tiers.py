"""Storage tiers and their physical artifacts (AD-3, AD-5).

The earlier spine named three tiers while the job queue (Tier 2) and the search
indexes (Tier 3) shared one `event_telemetry.db`. "Rebuild Tier 3 only" was
therefore unimplementable, and the obvious implementation of a rebuild — delete
the file, recreate it — would have destroyed every pending external write and
every connector cursor, silently, with the AD-3 test still green.

Separation is physical here, so `reindex` cannot reach Tier 2 by construction
rather than by careful coding.

Imports nothing from `pm_ai` (AD-30).
"""

from __future__ import annotations

from enum import Enum


# ── Artifact keys ────────────────────────────────────────────────────────────
# The keys of the tables below are also the keys `pm_ai.platform.paths` resolves
# and `pm_ai.storage` writes through. The ones that appear in code — rather than
# only in a table — are spelled once, here: `domain` is the only package all
# three may import (AD-30), so this is the single home for them rather than a
# fourth copy of the string.
EVENT_LOG = "event_log/"
OPERATIONAL_DB = "operational.db"


class ScopeResolutionError(Exception):
    """A path resolver refused to locate an artifact.

    The concrete refusals live in `pm_ai.platform.paths`, which `storage`,
    `core`, and `surfaces` may not import — so without a base here, no caller
    could catch a refusal by type and every one of them would either catch
    `Exception` or let the daemon abort. Declared in `domain` because that is the
    one package every layer may reach (AD-30).
    """


class Tier(Enum):
    """AD-3. Exactly one tier per artifact."""

    TRUTH = 1
    OPERATIONAL = 2
    DERIVED = 3

    @property
    def rebuildable(self) -> bool:
        """Only Tier 3 can be reconstructed; the others must survive."""
        return self is Tier.DERIVED

    @property
    def backed_up(self) -> bool:
        """Tier 2 is a backup target precisely because it is NOT rebuildable.

        Backing up markdown alone — the earlier rule — would have lost the job
        queue, cursors, and executed-key ledger.
        """
        return self in (Tier.TRUTH, Tier.OPERATIONAL)


# Every persistent artifact, assigned once. A path that appears in two tiers is
# the bug this table exists to prevent.
ARTIFACT_TIER: dict[str, Tier] = {
    EVENT_LOG: Tier.TRUTH,
    "commitments_log.md": Tier.TRUTH,
    "coaching_1on1_history.md": Tier.TRUTH,
    "strategic_goals.md": Tier.TRUTH,
    "meetings/": Tier.TRUTH,
    "disclosure.md": Tier.TRUTH,
    "rules/": Tier.TRUTH,
    "config.toml": Tier.TRUTH,
    OPERATIONAL_DB: Tier.OPERATIONAL,
    # Tier 2, not Tier 3, despite AD-25 calling it "derived telemetry" — that
    # word means *calculated*, not *rebuildable*. Tier 3's test is rebuildable
    # from Tier 1 with zero loss, and burnout trends outlive the telemetry they
    # were computed from once FR-37 compaction prunes it. It had no tier at all
    # until 2026-08-20, so the one store holding months of personal trend data
    # was in neither the backup set nor the rebuild set.
    "personal_analytics.db": Tier.OPERATIONAL,
    "derived.db": Tier.DERIVED,
    "vector_index/": Tier.DERIVED,
}

REBUILD_TARGETS = frozenset(a for a, t in ARTIFACT_TIER.items() if t.rebuildable)
BACKUP_TARGETS = frozenset(a for a, t in ARTIFACT_TIER.items() if t.backed_up)


# Raw input under a retention policy is deliberately OUTSIDE the tier model.
# AD-3 tiers "persistent state"; these are transient material the pipeline
# consumes and NFR-09 purges at 30 days. They are not Tier 3 — Tier 3 promises
# *rebuildable from Tier 1 with zero loss*, and no rebuild reconstructs a
# recording. Listing them here rather than omitting them is the point: an
# artifact absent from both sets is an oversight (that is how
# personal_analytics.db ended up backed up by nothing), while an artifact named
# here is an excluded-on-purpose decision that the assertion below keeps honest.
#
# Per-scope, like `event_log/`: a transcript lives in the scope owning its
# meeting (AD-33). Nothing may depend on them surviving.
RETENTION_MANAGED: frozenset[str] = frozenset({"transcripts/", "telegram_cache/"})

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

assert not (RETENTION_MANAGED & set(ARTIFACT_TIER)), (
    "an artifact is both tiered and retention-managed; it must be exactly one."
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


# Tier 2 and Tier 3 must never share a physical artifact — the original defect.
assert not (REBUILD_TARGETS & BACKUP_TARGETS), (
    "AD-3: an artifact is both a rebuild target and a backup target, so a "
    "rebuild would destroy state that cannot be reconstructed."
)
