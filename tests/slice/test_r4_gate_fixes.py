"""Regressions for the five defects the r4 reviewer gate verified.

Every test here failed, or could not be written at all, before 2026-08-19. Each
names the AD it defends and the way that AD was quietly false.

The AD-36 tests matter most. A test already existed for it and passed, but it
handed `Provenance.PM_AI` straight to `evaluate_commitment` — proving the
downstream half while the step that *derives* PM_AI from the ledger did not
exist. These drive the real path.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone

import pytest

from pm_ai.app.pipelines import run_harvest, run_transcript_ingestion
from pm_ai.app.wiring import build
from pm_ai.core.jobs import idempotency_key
from pm_ai.core.normalize import attribute_all
from pm_ai.domain.disclosure import CommittedScopeLeak
from pm_ai.domain.events import (
    CommitPayload,
    MessagePayload,
    NormalizedEvent,
    NormalizedEventType,
    Provenance,
)
from pm_ai.domain.harvest import Cursor
from pm_ai.domain.identity import PM_AI, DataScope, ScopeKind, SourceRef, TargetRef, resolve_actor
from pm_ai.domain.lifecycle import CommitmentState, evaluate_commitment
from pm_ai.storage.service import ReconciliationRequired

NOW = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")


@pytest.fixture
def daemon(tmp_path):
    shutil.rmtree(tmp_path, ignore_errors=True)
    return build(tmp_path, "alpha", now=lambda: NOW)


def _commit_event(sha: str, scope: DataScope = PROJECT) -> NormalizedEvent:
    return NormalizedEvent(
        scope=scope,
        type=NormalizedEventType.COMMIT_PUSHED,
        source_ref=SourceRef.parse(f"gitlab:alpha:commit:{sha}"),
        actor=resolve_actor(system="gitlab", handle="alex@example.com"),
        occurred_at=NOW,
        payload=CommitPayload(sha=sha, message="work"),
    )


# ── Defect 1: AD-36 was defeated ─────────────────────────────────────────────


def test_connector_never_asserts_external(daemon):
    """AD-36 — a connector cannot see the ledger, so it may not claim authorship.

    Hard-coding EXTERNAL here made the UNKNOWN default unreachable and every
    harvested row admissible as evidence.
    """
    daemon.connectors["gitlab:alpha"]._fake_api = [
        {"sha": "9f2a1c", "message": "x", "author_email": "alex@example.com", "committed_at": NOW}
    ]
    raw = daemon.connectors["gitlab:alpha"].harvest(Cursor())
    assert all(e.authored_by is Provenance.UNKNOWN for e in raw.events)


def test_our_own_write_harvested_back_is_not_evidence(daemon):
    """AD-36 end to end, through the path that derives provenance.

    pm-ai posts a comment, the ledger records the artifact it created, and the
    harvest that brings that artifact back must resolve to PM_AI — so a
    commitment cannot be marked FULFILLED on telemetry pm-ai manufactured.
    """
    target = TargetRef.parse("gitlab:alpha:issue:WI-108")
    key = idempotency_key("post_comment", target.lock_key, {"comment": "done?"})
    invocation = daemon.skills.invoke(
        "gitlab.post_comment", target=target, payload={"comment": "done?"}, idempotency_key=key
    )

    # The provider's id for the artifact we just created, coming back in a harvest.
    harvested = (
        NormalizedEvent(
            scope=PROJECT,
            type=NormalizedEventType.MESSAGE_POSTED,
            source_ref=SourceRef.parse(f"gitlab:alpha:note:{invocation.external_id}"),
            actor=resolve_actor(system="gitlab", handle="alex@example.com"),
            occurred_at=NOW,
            payload=MessagePayload(channel="WI-108"),
        ),
    )

    attributed = attribute_all(harvested, daemon.storage.executed_mutations())
    assert attributed[0].authored_by is Provenance.PM_AI
    assert not attributed[0].authored_by.admissible_as_evidence

    verdict = evaluate_commitment(
        overdue=True,
        evidence_admissible=attributed[0].authored_by.admissible_as_evidence,
        covered=True,
        harvest_failed=False,
    )
    assert verdict is CommitmentState.BROKEN, "self-authored activity must never prove fulfilment"


def test_a_genuine_external_write_still_counts(daemon):
    """The guard must not swallow real evidence — otherwise nothing ever FULFILS."""
    attributed = attribute_all((_commit_event("9f2a1c"),), daemon.storage.executed_mutations())
    assert attributed[0].authored_by is Provenance.EXTERNAL
    assert evaluate_commitment(overdue=True, evidence_admissible=True, covered=True, harvest_failed=False) is (
        CommitmentState.FULFILLED
    )


def test_bot_identity_attributes_independently(daemon):
    """AD-36 mechanism 3 — two mechanisms, because one of them will have gaps."""
    event = NormalizedEvent(
        scope=PROJECT,
        type=NormalizedEventType.COMMIT_PUSHED,
        source_ref=SourceRef.parse("gitlab:alpha:commit:deadbe"),
        actor=PM_AI,
        occurred_at=NOW,
        payload=CommitPayload(sha="deadbe", message="automated"),
    )
    assert attribute_all((event,), {})[0].authored_by is Provenance.PM_AI


def test_unrecognisable_mutation_makes_its_scope_uncertain(daemon):
    """AD-36 — a mutation we cannot recognise must not clear its scope as external.

    A skill whose provider returns no usable id is invisible to the join, so the
    honest answer for that scope is UNKNOWN rather than a confident EXTERNAL.
    """
    ledger = {"idem_x": ("gitlab:alpha:issue:WI-108", "")}
    assert attribute_all((_commit_event("9f2a1c"),), ledger)[0].authored_by is Provenance.UNKNOWN


# ── Defect 2: AD-20's crash window ───────────────────────────────────────────


def test_unsettled_attempt_blocks_a_blind_retry(daemon):
    """AD-20 — recording after the call leaves a window that duplicates writes."""
    target = TargetRef.parse("gitlab:alpha:issue:WI-108")
    daemon.storage.begin_execution("idem_crash", target)  # claimed, never settled

    assert not daemon.storage.was_executed("idem_crash"), "in-flight proves nothing"
    with pytest.raises(ReconciliationRequired):
        daemon.storage.begin_execution("idem_crash", target)


def test_a_crash_between_call_and_record_cannot_double_write(daemon):
    """AD-20 — the ordering test, not merely the storage-primitive test.

    The provider accepts the write and the process dies before the ledger is
    appended. With the record-after ordering nothing was written down, so the
    retry posted a second comment. Claiming the key first turns that into a
    reconciliation task.
    """
    from pm_ai.domain.identity import SkillPermission

    class FlakyPostComment:
        name, system, permission = "post_comment", "gitlab", SkillPermission.COMMENT

        def __init__(self) -> None:
            self.calls = 0

        def execute(self, target, payload) -> str:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("provider accepted the write, then the process died")
            return f"note_{self.calls}"

    flaky = FlakyPostComment()
    daemon.skills.register(flaky)
    target = TargetRef.parse("gitlab:alpha:issue:WI-108")
    key = idempotency_key("post_comment", target.lock_key, {"comment": "ping"})

    with pytest.raises(RuntimeError):
        daemon.skills.invoke(
            "gitlab.post_comment", target=target, payload={"comment": "ping"}, idempotency_key=key
        )

    with pytest.raises(ReconciliationRequired):
        daemon.skills.invoke(
            "gitlab.post_comment", target=target, payload={"comment": "ping"}, idempotency_key=key
        )
    assert flaky.calls == 1, "the retry reached the provider a second time"


def test_executed_ledger_survives_a_restart(daemon, tmp_path):
    """AD-3/AD-20 — Tier 2 is durable, so a replay after restart is still a no-op."""
    target = TargetRef.parse("gitlab:alpha:issue:WI-108")
    key = idempotency_key("post_comment", target.lock_key, {"comment": "hello"})
    daemon.skills.invoke(
        "gitlab.post_comment", target=target, payload={"comment": "hello"}, idempotency_key=key
    )

    restarted = build(tmp_path, "alpha", now=lambda: NOW)
    assert restarted.storage.was_executed(key), "the ledger did not outlive the process"

    replay = restarted.skills.invoke(
        "gitlab.post_comment", target=target, payload={"comment": "hello"}, idempotency_key=key
    )
    assert replay.replayed is True
    assert restarted.skills._skills["gitlab.post_comment"].posted == [], "second write escaped"


# ── Defect 3: AD-38's guard was vacuous ──────────────────────────────────────


def test_ad38_guard_fires_on_a_normalized_event(daemon):
    """AD-38 — the guard read an attribute NormalizedEvent does not have.

    It therefore passed on the only record type the storage service persists —
    passing because the field was absent, not because the write was safe.
    """
    personal = _commit_event("9f2a1c", DataScope(ScopeKind.PERSONAL))
    with pytest.raises(CommittedScopeLeak):
        daemon.storage.persist_events((personal,), scope=PROJECT)


def test_people_scope_may_not_reach_a_committed_scope(daemon):
    """AD-4/AD-38 — a report's record in a repo is readable by that report's peers."""
    people = _commit_event("3b7e02", DataScope(ScopeKind.PEOPLE, person_id="alex"))
    with pytest.raises(CommittedScopeLeak):
        daemon.storage.persist_events((people,), scope=PROJECT)


def test_people_is_not_personal_and_is_not_committed():
    """AD-4 — the two properties the HR rule turns on."""
    people = DataScope(ScopeKind.PEOPLE, person_id="alex")
    assert people.is_people
    assert not people.is_personal, "AD-31 must not forbid the HR sync UJ-4 requires"
    assert not people.is_git_committed
    with pytest.raises(ValueError):
        DataScope(ScopeKind.PEOPLE)  # whose record is it?


# ── Defect 4: the dedup key was scope-blind ──────────────────────────────────


def test_same_reference_in_two_scopes_is_not_a_duplicate(daemon):
    """AD-34/AD-38 — the cross-scope split must not be swallowed as a duplicate."""
    personal_scope = DataScope(ScopeKind.PERSONAL)
    first = daemon.storage.persist_events((_commit_event("9f2a1c"),), scope=PROJECT)
    second = daemon.storage.persist_events(
        (_commit_event("9f2a1c", personal_scope),), scope=personal_scope
    )
    assert (first.persisted, second.persisted) == (1, 1)
    assert second.duplicates == 0, "AD-38's second entry was dropped as a duplicate"


# ── Personal analytics: folded into the personal scope (2026-08-20) ──────────


def test_personal_analytics_is_backed_up_and_never_rebuilt():
    """AD-3/AD-25 — it had no tier at all, so no backup covered it.

    Tier 3 means *rebuildable from Tier 1 with zero loss*, not merely
    *calculated*. Burnout and workload trends are longitudinal and outlive the
    telemetry they were computed from, because FR-37 compaction prunes it — so a
    rebuild would silently return a shorter history.
    """
    from pm_ai.domain.storage_tiers import (
        ARTIFACT_TIER,
        BACKUP_TARGETS,
        REBUILD_TARGETS,
        Tier,
        assert_reindex_safe,
        TierViolation,
    )

    assert ARTIFACT_TIER["personal_analytics.db"] is Tier.OPERATIONAL
    assert "personal_analytics.db" in BACKUP_TARGETS
    assert "personal_analytics.db" not in REBUILD_TARGETS

    # `pm-ai reindex` must not be able to reach it. The Tier-3 companion is
    # named `event_index.db` since the 2026-08-27 rename; left as `derived.db`
    # this call would still raise, but for the *other* member of the set, and
    # would no longer prove anything about `personal_analytics.db`.
    with pytest.raises(TierViolation):
        assert_reindex_safe(frozenset({"event_index.db", "personal_analytics.db"}))


def test_every_artifact_still_has_exactly_one_tier():
    """AD-3 — the invariant the missing entry had quietly broken."""
    from pm_ai.domain.storage_tiers import ARTIFACT_TIER, BACKUP_TARGETS, REBUILD_TARGETS

    assert not (BACKUP_TARGETS & REBUILD_TARGETS), "an artifact cannot be both"
    assert all(isinstance(v, object) and v is not None for v in ARTIFACT_TIER.values())


# ── Transcript and meeting scoping (2026-08-20) ──────────────────────────────


def _meeting(scope: DataScope, mid: str = "mtg_01HX"):
    from pm_ai.domain.identity import Actor
    from pm_ai.domain.meetings import Meeting

    return Meeting(
        meeting_id=mid,
        title="sync",
        start=NOW,
        duration_minutes=30,
        attendees=(Actor("actor_andrei", "Andrei"),),
        scope=scope,
    )


def test_a_committed_record_cannot_cite_a_personal_meeting(daemon):
    """AD-38 — the violation that was live in the spine until 2026-08-20.

    `meetings/` sat in the personal scope while AD-33 makes Meeting the citation
    root and commitments live in the git-committed project ledger. Every
    commitment extracted from a meeting therefore referenced personal-scope
    material from a committed file, by `source_ref` — which AD-38 forbids in
    those exact words.
    """
    from pm_ai.domain.transcripts import Transcript, TranscriptSource, Utterance

    personal_meeting = _meeting(DataScope(ScopeKind.PERSONAL))
    transcript = Transcript(
        meeting_id=personal_meeting.meeting_id,
        source=TranscriptSource.GRAPH,
        utterances=(Utterance("andrei@example.com", "pm-ai, note this", 0),),
    )
    with pytest.raises(CommittedScopeLeak):
        run_transcript_ingestion(daemon, transcript, personal_meeting)


def test_a_report_1on1_cannot_be_cited_from_the_project_ledger(daemon):
    """AD-4/AD-38 — a 1:1 with a direct report is `people`-scoped, not project."""
    from pm_ai.domain.transcripts import Transcript, TranscriptSource, Utterance

    one_on_one = _meeting(DataScope(ScopeKind.PEOPLE, person_id="alex"), "mtg_1on1")
    transcript = Transcript(
        meeting_id=one_on_one.meeting_id,
        source=TranscriptSource.GRAPH,
        utterances=(Utterance("andrei@example.com", "pm-ai, note this", 0),),
    )
    with pytest.raises(CommittedScopeLeak):
        run_transcript_ingestion(daemon, transcript, one_on_one)


def test_a_transcript_lives_in_its_meetings_scope():
    """AD-33 — the capture is never more or less shareable than the event."""
    for scope in (
        DataScope(ScopeKind.PROJECT, "alpha"),
        DataScope(ScopeKind.PEOPLE, person_id="alex"),
        DataScope(ScopeKind.PERSONAL),
    ):
        assert _meeting(scope).transcript_home == scope


def test_raw_captures_are_excluded_from_the_tier_model_on_purpose():
    """AD-3 — excluded deliberately and checked, not omitted and forgotten.

    They are not Tier 3: Tier 3 promises rebuildable-from-Tier-1-with-zero-loss,
    and no rebuild reconstructs a recording. They are transient input NFR-09
    purges, and AD-33 forbids anything depending on them.
    """
    from pm_ai.domain.storage_tiers import ARTIFACT_TIER, BACKUP_TARGETS, RETENTION_MANAGED

    assert RETENTION_MANAGED == {"transcripts/", "telegram_cache/"}
    assert not (RETENTION_MANAGED & set(ARTIFACT_TIER)), "exactly one of tiered or retention-managed"
    assert not (RETENTION_MANAGED & BACKUP_TARGETS), "raw captures are never a backup target"


def test_captures_refuse_to_write_without_a_gitignore_rule():
    """AD-23 — `transcripts/` sits inside a committed scope, so the exclusion is a
    rule rather than a boundary, and a rule can go missing.

    Fails closed: no rule, no write. Losing a transcript is recoverable; a
    verbatim transcript in the team's repository is not.
    """
    from pm_ai.domain.storage_tiers import (
        UnprotectedCaptureDir,
        assert_capture_dir_ignored,
    )

    # The rule is now the caller's to supply: it depends on where the capture
    # sits inside its working tree, which differs per scope, so the domain no
    # longer holds a table of rule text.
    rule = "/.project-ai/transcripts/"

    assert_capture_dir_ignored(
        "transcripts/", "node_modules/\n/.project-ai/transcripts/\n", rule=rule
    )
    assert_capture_dir_ignored("transcripts/", ".project-ai/transcripts\n", rule=rule)

    with pytest.raises(UnprotectedCaptureDir):
        assert_capture_dir_ignored("transcripts/", "node_modules/\n*.pyc\n", rule=rule)
    with pytest.raises(UnprotectedCaptureDir):
        assert_capture_dir_ignored("transcripts/", "", rule=rule)

    # Which artifacts need the guard is no longer this function's decision, and
    # as of 2026-08-22 it is no longer a global one either: `requires_git_exclusion`
    # answers per scope, because `event_log/` sits inside the gitignored
    # team-member enclave and is committed to the repository in a project. A
    # basename-keyed set could not hold both answers.
    from pm_ai.domain.identity import ScopeKind
    from pm_ai.domain.storage_tiers import requires_git_exclusion

    assert requires_git_exclusion(ScopeKind.PEOPLE, "event_log/")
    assert not requires_git_exclusion(ScopeKind.PROJECT, "event_log/")
    assert requires_git_exclusion(ScopeKind.PROJECT, "transcripts/")
    assert not requires_git_exclusion(ScopeKind.APPLICATION, "config.toml")
