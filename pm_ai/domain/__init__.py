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
from pm_ai.domain.storage_tiers import (
    ARTIFACT_TIER,
    BACKUP_TARGETS,
    EVENT_LOG,
    OPERATIONAL_DB,
    REBUILD_TARGETS,
    ScopeResolutionError,
    Tier,
    TierViolation,
    assert_reindex_safe,
)
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
    "EVENT_LOG", "OPERATIONAL_DB", "ScopeResolutionError",
]
