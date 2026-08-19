# Architecture tests

These make `ARCHITECTURE-SPINE.md` executable. The spine is a coordination
contract; without enforcement it is a document people have to remember, and
memory is exactly what fails on a Friday afternoon six months in.

Every check names the AD it enforces. **Do not edit a check without editing its
AD, and do not edit an AD without checking whether a test here encodes it.**

## Running

```bash
uv add --dev pytest import-linter
uv run pytest tests/architecture -v      # everything
uv run lint-imports                      # layering only, faster feedback
```

## Status: skipping by design

`pm_ai/` does not exist yet, so these skip. That is intentional — they are
written against the package Phase 1 will create, so the contracts are in place
*before* the code they constrain rather than retrofitted afterwards.

**Phase 1 exit criterion: zero skips in this directory.** A skip here after
Phase 1 means an invariant is unenforced, not that a test is unnecessary.

## What is enforced where

| Mechanism | Catches | File |
|---|---|---|
| Import contracts | Dependency direction, forbidden libraries | `.importlinter`, `test_layering.py` |
| AST rules | Calls, not imports — file writes, shell exec, scheduling | `test_static_rules.py` |
| Behavioural tests | Semantics no static check can see | `test_domain_invariants.py` |

## AD coverage

| AD | Enforced by | Notes |
|---|---|---|
| Paradigm | `layering` contract | Adapters are independent siblings, so no adapter imports another |
| AD-1 | `core-is-io-free`, `http-confined-to-adapters`, `test_ad1_no_shell_execution_outside_platform` | |
| AD-2 | `test_ad2_telegram_uses_outbound_polling_only` | |
| AD-3 | `test_ad3_indexes_rebuild_from_markdown_without_loss` | Integration-weight; the sovereignty property |
| AD-5 | `test_ad5_single_writer_owns_all_file_writes`, `db-confined-to-storage` | |
| AD-6 | `test_ad6_markdown_is_never_encrypted` | |
| AD-7 | `cli-owns-no-scheduling` | |
| AD-8 | `test_ad8_loopback_api_rejects_unauthenticated_requests` | |
| AD-9 | `test_ad9_connectors_own_no_scheduling`, `test_ad9_cursor_is_opaque_to_the_core` | |
| AD-11 | `test_ad11_no_filesystem_discovery_of_projects` | |
| AD-13 | `test_ad13_features_cannot_implement_their_own_proposal_expiry` | |
| AD-14 | `test_ad14_proposal_and_commitment_lifecycles_stay_distinct` | |
| AD-15 | `model-clients-confined`, `test_ad15_*` | Local-only classes; frontier tiering |
| AD-16 | `no-builtin-tool-agent` | Blocks the Claude Agent SDK and friends |
| AD-17 | `test_ad17_budget_breach_warns_but_never_degrades` | |
| AD-20 | `test_ad20_idempotency_keys_are_deterministic`, `test_ad20_mutating_jobs_require_a_key` | |
| AD-21 | `test_ad21_slow_requests_acknowledge_instead_of_blocking` | |
| AD-22 | `test_ad22_retrieval_path_never_touches_a_model` | |
| AD-23 | `test_ad23_transcript_pipeline_works_without_a_live_tenant` | |
| AD-24 | `test_ad24_event_log_is_not_a_debug_sink` | |
| AD-25 | `test_ad25_project_rendering_cannot_open_the_personal_store` | |
| AD-26 | `os-behind-platform` | |
| AD-27 | `test_ad27_connectors_only_emit_core_declared_event_types` | |
| AD-28 | `test_ad28_project_ledger_rejects_personal_commitments` | |
| AD-29 | `test_ad29_sanitization_leaves_the_raw_payload_intact` | |
| AD-30 | `layering` + `surfaces-through-core` + `domain-imports-nothing` contracts | Composition root; `domain` imports nothing |
| AD-31 | `test_ad31_every_frontier_call_records_scope_provenance`, `test_ad31_personal_material_cannot_reach_a_project_destination` | D1 — disclosure log + destination boundary |
| AD-32 | `test_ad32_auto_execute_requires_all_three_conditions` (5 cases), `test_ad32_manual_transcripts_never_auto_execute` | D2 — source × speaker × verb |
| AD-33 | `test_ad33_source_refs_never_point_at_a_transcript`, `test_ad33_ledger_entries_are_self_contained`, `test_ad23_transcript_without_a_meeting_is_rejected` | D3 — cite the meeting, not the capture |
| AD-34 | `test_ad34_source_refs_follow_the_fixed_grammar`, `test_ad34_unresolvable_actors_never_become_raw_string_identities`, `test_ad34_connectors_do_not_mint_event_ids` | Reference grammar, actor resolution, natural key |
| AD-35 | `test_ad35_the_two_clocks_are_not_interchangeable`, `test_ad35_ledger_folding_is_deterministic`, `test_ad35_sweeper_will_not_declare_broken_without_coverage` | Two clocks; coverage-aware sweeping |
| AD-36 | `test_ad36_self_authored_events_are_excluded_from_evidence`, `test_ad36_every_class_m_mutation_is_recorded_for_attribution` | pm-ai's own writes are never evidence |
| AD-37 | `test_ad37_concurrent_approval_from_two_surfaces_yields_one_execution`, `test_ad37_expired_proposals_cannot_execute` | Versioned CAS on shared entities |
| AD-38 | `test_ad38_disclosure_records_cannot_reach_a_committed_scope`, `test_ad38_no_committed_record_may_reference_personal_scope`, `test_ad38_project_scope_is_the_only_committed_scope` | Disclosure ledger is application-scoped; committed scopes never name personal material |

### Not mechanically enforced

Judgement calls that stay human — worth knowing so nobody assumes green means
compliant:

- **AD-4** (three-scope ownership) — partly covered via AD-25 and AD-28, but
  "is this configuration project-specific?" needs a reviewer.
- **AD-10** (connector instances per project) — shape is testable, correct
  per-project cursor isolation needs an integration environment.
- **AD-12** (sanitize every inbound payload) — the pipeline enforces it
  centrally; a new ingestion path that bypasses the pipeline is a review catch.
- **AD-18** (skill allowlist) — enforced at runtime by the registry; the
  *contents* of the allowlist are a human decision.
- **AD-19** (bounded worker pool) — needs load testing, not a unit test.

## Enforcement-layer corrections (2026-08-19)

The independent reviewer gate found real weaknesses in this suite, not only in
the spine. Fixed:

- **`test_ad20_idempotency_keys_are_deterministic` was single-process**, so a
  `time.time()` or PID seed passed it and would still double-post after a
  restart. It now forks a subprocess and compares — the boundary that matters.
- **The AD-5 write scan exempted `storage`**, the one layer that can rewrite a
  ledger in place. `test_ad5_storage_never_rewrites_a_markdown_ledger_in_place`
  now checks append-only where the exemption applies.
- **`test_ad8` used Flask's `test_client()`** against a FastAPI stack; it now
  uses Starlette's `TestClient`.
- **`.importlinter` silently permitted `surfaces → storage/models`** and had no
  contract on `ports`. Both closed.

Note also that AST checks pass **vacuously** against an empty package — a green
run on a skeleton proves nothing. Treat "zero skips" as the real gate, and treat
a stubbed module as a skip in disguise.

## Why these five come first

The tests marked `ADVERSARIAL` in `test_domain_invariants.py` encode holes found
by the adversarial reviewer pass on 2026-08-18: pairs of components that obeyed
every written AD and would still have built incompatibly. Three of the five were
about **shared vocabulary and ownership** rather than mechanism — which is the
predictable leak in a hexagonal design, because ports fix *shape* and not
*meaning*. Two components can satisfy `harvest(since: Cursor) -> list[Event]`
perfectly and still disagree about what an event is.

That is the pattern to watch when adding ADs: wherever two components share a
**word** rather than a **type**, there is probably a contract that isn't written
down yet.

The idempotency test is the one to keep if you ever keep only one. A random
per-attempt key passes code review, passes type checking, and double-posts
external mutations in production.
