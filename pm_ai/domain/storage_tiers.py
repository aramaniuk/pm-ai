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
    "event_log/": Tier.TRUTH,
    "commitments_log.md": Tier.TRUTH,
    "coaching_1on1_history.md": Tier.TRUTH,
    "strategic_goals.md": Tier.TRUTH,
    "meetings/": Tier.TRUTH,
    "disclosure.md": Tier.TRUTH,
    "rules/": Tier.TRUTH,
    "config.toml": Tier.TRUTH,
    "operational.db": Tier.OPERATIONAL,
    "derived.db": Tier.DERIVED,
    "vector_index/": Tier.DERIVED,
}

REBUILD_TARGETS = frozenset(a for a, t in ARTIFACT_TIER.items() if t.rebuildable)
BACKUP_TARGETS = frozenset(a for a, t in ARTIFACT_TIER.items() if t.backed_up)


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
