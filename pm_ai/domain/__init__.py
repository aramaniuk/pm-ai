"""Domain: entities, enumerations, state machines, closed taxonomies (AD-27).

Imports nothing from `pm_ai` outside this package, which is what lets `ports`
express protocols in domain types without a cycle (AD-30).

These types exist because two independent reviewer runs found the same root
cause: words shared across components without a type behind them. Prose rules
did not fix it; a malformed reference is now a construction error rather than a
review comment.
"""

from pm_ai.domain.disclosure import (
    DISCLOSURE_LEDGER_PATH,
    DISCLOSURE_LEDGER_SCOPE,
    CommittedScopeLeak,
    DisclosureRecord,
    assert_writable,
)
from pm_ai.domain.events import (
    NormalizedEvent,
    NormalizedEventType,
    PAYLOAD_FOR,
    PayloadMismatch,
    Provenance,
)
from pm_ai.domain.scope_model import (
    ARTIFACT_TIER,
    BACKUP_TARGETS,
    DIAGNOSTIC_ONLY,
    REBUILD_TARGETS,
    RETENTION_MANAGED,
    SCOPE_TREES,
    Collection,
    Dir,
    File,
    MalformedLayout,
    OutsideTierModel,
    ScopeResolutionError,
    Tier,
)
from pm_ai.domain.storage_tiers import (
    CAPTURES,
    EVENT_LOG,
    GITIGNORE_REQUIRED,
    OPERATIONAL_DB,
    TierViolation,
    UnprotectedCaptureDir,
    assert_capture_dir_ignored,
    assert_capture_dir_untracked,
    assert_reindex_safe,
)
from pm_ai.domain.vcs import TrackingVerdict, VcsUnavailable
from pm_ai.domain.identity import (
    PM_AI,
    PM_AI_ACTOR,
    UNRESOLVED,
    UNRESOLVED_ACTOR,
    Actor,
    DataScope,
    MalformedReference,
    NonDurableReferent,
    ScopeKind,
    SkillPermission,
    SourceRef,
    TargetRef,
)
from pm_ai.domain.lifecycle import (
    DEFAULT_PROPOSAL_TTL,
    VERB_REGISTRY,
    CommitmentState,
    CoverageWindow,
    ProposalState,
    UnknownVerb,
    Verb,
    evaluate_commitment,
    lookup_verb,
)

__all__ = [
    "Actor", "CommittedScopeLeak", "DISCLOSURE_LEDGER_PATH",
    "DISCLOSURE_LEDGER_SCOPE", "DisclosureRecord", "assert_writable", "CommitmentState", "CoverageWindow", "DEFAULT_PROPOSAL_TTL",
    "DataScope", "MalformedReference", "NonDurableReferent", "NormalizedEvent",
    "NormalizedEventType", "PAYLOAD_FOR", "PM_AI", "PM_AI_ACTOR",
    "PayloadMismatch", "ProposalState", "Provenance", "ScopeKind",
    "SkillPermission", "SourceRef", "TargetRef", "UNRESOLVED",
    "UNRESOLVED_ACTOR", "UnknownVerb", "VERB_REGISTRY", "Verb",
    "evaluate_commitment", "lookup_verb", "ARTIFACT_TIER", "BACKUP_TARGETS",
    "REBUILD_TARGETS", "Tier", "TierViolation", "assert_reindex_safe",
    "CAPTURES", "EVENT_LOG", "OPERATIONAL_DB", "ScopeResolutionError",
    # The scope layout and the durability each of its artifacts declares. The
    # tier tables are derived from the trees, so both come from one module.
    "SCOPE_TREES", "File", "Dir", "Collection", "MalformedLayout",
    "OutsideTierModel", "RETENTION_MANAGED", "DIAGNOSTIC_ONLY",
    "GITIGNORE_REQUIRED", "UnprotectedCaptureDir", "assert_capture_dir_ignored",
    # Git is the authority on what git tracks, so the write path asks it through
    # `pm_ai.ports.VcsPort` and refuses on `VcsUnavailable`. The text matcher
    # above is the pure form of the question, not the answer.
    "TrackingVerdict", "VcsUnavailable", "assert_capture_dir_untracked",
]
