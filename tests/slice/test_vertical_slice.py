"""Vertical slice: GitLab harvest → persist, and a class-M mutation.

These exercise the questions two reviewer runs could not settle on paper —
whether an idempotency key is actually *honoured*, whether attribution matches
on the way back in, whether the composition root can wire the pipeline at all.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone

import pytest
from gitlab_fakes import install

from pm_ai.app.pipelines import run_harvest
from pm_ai.app.wiring import build
from pm_ai.domain import (
    EVENT_LOG,
    CommitmentState,
    DataScope,
    MalformedReference,
    Provenance,
    ScopeKind,
    TargetRef,
    evaluate_commitment,
)
from pm_ai.domain.harvest import Cursor
from pm_ai.domain.handles import UNRESOLVED_SENTINEL, person_handle, register_alias
from pm_ai.domain.identity import Actor
from pm_ai.core.ledger import parse_segment
from pm_ai.skills.registry import MissingIdempotencyKey, SkillNotAuthorized

NOW = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def daemon(tmp_path):
    shutil.rmtree(tmp_path, ignore_errors=True)
    d = build(tmp_path, "alpha", now=lambda: NOW)
    install(d, [
        {"id": "9f2a1c", "message": "Fix auth refactor", "author_email": "alex@example.com",
         "committed_date": (NOW - timedelta(hours=2)).isoformat()},
        {"id": "3b7e02", "message": "Add Redis benchmark", "author_email": "alex@example.com",
         "committed_date": (NOW - timedelta(hours=1)).isoformat()},
    ])
    return d


# ── Harvest ──────────────────────────────────────────────────────────────────


def test_harvest_persists_events_through_the_composition_root(daemon, tmp_path):
    """AD-30 — the pipeline touches connector, core, and storage. Nothing else may."""
    result = run_harvest(daemon, "gitlab:alpha")
    assert (result.persisted, result.duplicates) == (2, 0)

    event_log = daemon.storage.paths.resolve(daemon.scope, EVENT_LOG)
    assert event_log.is_relative_to(tmp_path), "the rooted resolver keeps everything under tmp_path"
    segments = list(event_log.glob("*.md"))
    assert len(segments) == 1, "AD-5 — one open segment, appended to"
    assert segments[0].name == f"{NOW:%Y-%m}.md", "the segment filename comes from the injected clock"
    body = segments[0].read_text()
    assert body.count("commit_pushed") == 2
    assert "gitlab:alpha:commit:9f2a1c" in body
    assert f"ingested_at={NOW.isoformat()}" in body, "the ingestion stamp is the injected clock"


def test_reharvest_is_idempotent_on_the_natural_key(daemon):
    """AD-34 — dedup on (source_system, source_ref), never the minted surrogate.

    Without this, a replayed harvest doubles every metric that feeds a review.
    """
    run_harvest(daemon, "gitlab:alpha")
    # Simulate a cursor reset / restore through the public write path.
    daemon.storage.save_cursor("gitlab:alpha", Cursor(), None, None)
    second = run_harvest(daemon, "gitlab:alpha")
    assert (second.persisted, second.duplicates) == (0, 2)


def test_dedup_survives_a_restart(daemon, tmp_path):
    """AD-34 + AD-3 — the dedup set is Tier 2, so it outlives the process.

    While it lived in a set on the instance, "re-harvesting is idempotent" was
    true only within one daemon lifetime: a restart re-persisted every event in
    the replayed window and doubled the metrics the rule exists to protect.
    """
    first = run_harvest(daemon, "gitlab:alpha")
    assert first.persisted == 2

    restarted = build(tmp_path, "alpha", now=lambda: NOW)
    install(restarted, fetch=daemon.connectors["gitlab:alpha"].fetch_page)
    restarted.storage.save_cursor("gitlab:alpha", Cursor(), None, None)  # replay the window

    second = run_harvest(restarted, "gitlab:alpha")
    assert (second.persisted, second.duplicates) == (0, 2)


def test_harvest_records_a_coverage_window(daemon):
    """AD-35 — coverage rides in the return type, and now says what it measured.

    The assertion was `start <= NOW - 30min <= end`, which held for exactly one
    reason: `harvest` fabricated a four-hour window ending at the clock, so any
    instant in the recent past fell inside it. Story `8a` deleted that
    fabrication, and this row moved with it in the same commit rather than
    becoming a surprise for whoever ran the suite next.

    Both bounds are the injected clock now, because that is what coverage
    *is* — `ingested_at` terms, from the connector's own clock, at the moment the
    first page came back and the moment fetching finished. This daemon's clock is
    frozen at `NOW`, so the honest window is a point.
    """
    run_harvest(daemon, "gitlab:alpha")
    windows = daemon.storage.coverage_windows("gitlab:alpha")
    assert windows == [(NOW, NOW)], (
        "coverage must be the connector's own clock across the fetch, not an "
        "interval computed backwards from it"
    )


def test_a_commit_author_is_written_as_the_handle_the_provider_gave(daemon):
    """AD-34 — a line records what the provider said; a reader decides who.

    Matrix: commit author — `author_email` set, absent, or not an address gives
    `email:<address>`, `none:`, `none:`; every row is kept. An alias registered
    before the harvest changes nothing on the line, because nothing on the write
    path reads the alias table.
    """
    register_alias(person_handle("email", "alex@example.com"), Actor("actor_alex", "Alex"))
    install(daemon, [
        {"id": "9f2a1c", "message": "a", "author_email": "Alex@Example.com",
         "committed_date": (NOW - timedelta(hours=3)).isoformat()},
        {"id": "3b7e02", "message": "b", "committed_date": (NOW - timedelta(hours=2)).isoformat()},
        {"id": "77aa00", "message": "c", "author_email": "not-an-address",
         "committed_date": (NOW - timedelta(hours=1)).isoformat()},
    ])
    result = run_harvest(daemon, "gitlab:alpha")
    assert result.persisted == 3, "an unusable address costs the person, never the row"

    body = next(daemon.storage.paths.resolve(daemon.scope, EVENT_LOG).glob("*.md")).read_text()
    actors = {
        dict(entry.fields)["src"].rsplit(":", 1)[1]: entry.actor
        for entry in parse_segment(body)
    }
    assert actors == {"9f2a1c": "email:alex@example.com", "3b7e02": "none:", "77aa00": "none:"}
    assert UNRESOLVED_SENTINEL not in body
    assert "actor_alex" not in body


# ── Class-M mutation ─────────────────────────────────────────────────────────


def test_idempotency_key_is_honoured_not_merely_carried(daemon):
    """AD-20 — the check nothing tested before.

    A replay after a crash, a retry, or a Tier-2 restore must not write twice.
    """
    target = TargetRef.parse("gitlab:alpha:issue:102")
    payload = {"comment": "Approved"}
    key = "idem_stable"

    first = daemon.skills.invoke("gitlab.post_comment", target=target, payload=payload, idempotency_key=key)
    second = daemon.skills.invoke("gitlab.post_comment", target=target, payload=payload, idempotency_key=key)

    assert first.replayed is False and second.replayed is True
    assert first.external_id == second.external_id
    posted = daemon.skills._skills["gitlab.post_comment"].posted
    assert len(posted) == 1, "AD-20: the mutation ran twice — at-least-once became at-least-twice"


def test_mutation_without_a_key_is_refused(daemon):
    """AD-20 — the skill layer refuses unkeyed external mutations."""
    with pytest.raises(MissingIdempotencyKey):
        daemon.skills.invoke(
            "gitlab.post_comment",
            target=TargetRef.parse("gitlab:alpha:issue:102"),
            payload={"comment": "x"},
            idempotency_key=None,
        )


def test_unlisted_skill_is_refused(daemon):
    """AD-18 — the registry is an allowlist, not a lookup table."""
    with pytest.raises(SkillNotAuthorized):
        daemon.skills.invoke(
            "gitlab.delete_repository",
            target=TargetRef.parse("gitlab:alpha:issue:102"),
            payload={},
            idempotency_key="idem_x",
        )


@pytest.mark.parametrize(
    ("system", "name"),
    [("gitlab", "post comment"), ("git.lab", "post_comment"), ("gitlab", ""), ("gitlab", "a=b")],
)
def test_a_skill_whose_name_the_line_cannot_carry_is_refused_at_register(daemon, system, name):
    """Refused before it can run: otherwise the external change would happen and
    only then would the AD-1 line fail to write."""
    from pm_ai.domain.event_entries import MalformedEntry
    from pm_ai.domain.identity import SkillPermission

    class _Skill:
        permission = SkillPermission.COMMENT

        def __init__(self) -> None:
            self.system, self.name, self.ran = system, name, False

        def execute(self, target, payload):
            self.ran = True
            return "n_1"

    skill = _Skill()
    with pytest.raises(MalformedEntry) as caught:
        daemon.skills.register(skill)
    assert "skill=" in str(caught.value)
    assert not skill.ran


def test_mutation_writes_one_event_log_entry(daemon):
    """AD-1 class M — one entry per invocation, in the owning scope."""
    daemon.skills.invoke(
        "gitlab.post_comment",
        target=TargetRef.parse("gitlab:alpha:issue:102"),
        payload={"comment": "Approved"},
        idempotency_key="idem_log",
    )
    body = next(daemon.storage.paths.resolve(daemon.scope, EVENT_LOG).glob("*.md")).read_text()
    assert body.count("skill_invoked actor=pm-ai ") == 1
    assert body.count(" skill=gitlab.post_comment ") == 1


def test_sub_resource_target_is_rejected_so_the_lock_is_real(daemon):
    """AD-37 — two names for one entity means the per-target lock serializes nothing."""
    with pytest.raises(MalformedReference):
        TargetRef.parse("gitlab:alpha:issue:102#labels")


# ── The closed loop ──────────────────────────────────────────────────────────


def test_pm_ai_does_not_verify_its_own_write(daemon):
    """AD-36 — the worst finding of the review, exercised end to end.

    The executor posts a comment; the verifier must not read that comment back
    as evidence the commitment was kept.
    """
    target = TargetRef.parse("gitlab:alpha:issue:102")
    daemon.skills.invoke(
        "gitlab.post_comment", target=target,
        payload={"comment": "Redis benchmarks done"}, idempotency_key="idem_self",
    )
    # That mutation is recorded, so normalization can attribute it on the way back.
    assert any(lock == target.lock_key for lock, _ in daemon.storage.executed_mutations().values())

    self_authored = evaluate_commitment(
        overdue=True,
        evidence_admissible=Provenance.PM_AI.admissible_as_evidence,
        covered=True,
        harvest_failed=False,
    )
    assert self_authored is CommitmentState.BROKEN, (
        "AD-36: pm-ai's own comment was counted as fulfilment evidence."
    )

    externally_authored = evaluate_commitment(
        overdue=True,
        evidence_admissible=Provenance.EXTERNAL.admissible_as_evidence,
        covered=True,
        harvest_failed=False,
    )
    assert externally_authored is CommitmentState.FULFILLED


def test_coverage_gap_does_not_fire_an_irreversible_nudge(daemon):
    """AD-35 — a sleeping laptop is missing data, not a broken promise."""
    assert evaluate_commitment(overdue=True, evidence_admissible=False, covered=False, harvest_failed=False) is CommitmentState.UNKNOWN


def test_disclosure_never_lands_in_the_committed_scope(daemon):
    """AD-38 — the audit mechanism must not become the leak."""
    from pm_ai.domain import CommittedScopeLeak, DisclosureRecord, assert_writable

    rec = DisclosureRecord(
        at=NOW, task_class="coaching", model="claude-opus-5",
        contributing_scopes=frozenset({DataScope(ScopeKind.PERSONAL)}),
        input_tokens=900, output_tokens=300, estimated_cost_usd=0.012,
    )
    with pytest.raises(CommittedScopeLeak):
        assert_writable(rec, scope=daemon.scope)  # project scope is git-committed
