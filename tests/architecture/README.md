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

## Status

`pm_ai/` exists. Two vertical slices are built, so a majority of these checks
now run against real code; the remainder skip on modules Phase 1 has yet to
create (`pm_ai.core.*`, `pm_ai.models.router`, `pm_ai.surfaces.*`).

**Read the coverage table below with care.** A populated "Enforced by" cell
means a test is *written*, not that it is *running*. An AD whose only check
skips is an AD nothing enforces — a reviewer found eleven of those on
2026-08-19, all reading as covered.

**Phase 1 exit criterion: zero skips in this directory, and every active check
demonstrated to fail on a planted violation.** The second half was added after
two load-bearing checks turned out to be bypassable while green:

- `_write_mode` read the mode from the builtin `open(path, mode)` position only,
  so the idiomatic `Path.open("w")` scored as a read and AD-5's single-writer
  rule passed a planted violation.
- The AD-1 shell scan omitted `pm_ai.app` — the composition root, the one layer
  permitted to import every other — and resolved no import aliases, so
  `import subprocess as _sp; _sp.run(..., shell=True)` was invisible.

Both are fixed and both now have their own regressions in
`test_enforcement_meta.py`. A check nobody checks is a comment.

## What is enforced where

| Mechanism | Catches | File |
|---|---|---|
| Import contracts | Dependency direction, forbidden libraries | `.importlinter`, `test_layering.py` |
| AST rules | Calls, not imports — file writes, shell exec, scheduling | `test_static_rules.py` |
| Behavioural tests | Semantics no static check can see | `test_domain_invariants.py` |
| Slice regressions | The five defects the r4 gate verified, each proven red first | `../slice/test_r4_gate_fixes.py` |
| Meta-checks | The AST helpers themselves — mode detection, alias resolution | `test_enforcement_meta.py` |
| Layout resolution | Where each scope's artifacts live, what may not live there, and what a subject id may be | `test_paths.py` |

## AD coverage

| AD | Enforced by | Notes |
|---|---|---|
| Paradigm | `layering` contract | Adapters are independent siblings, so no adapter imports another |
| AD-1 | `core-is-io-free`, `http-confined-to-adapters`, `test_ad1_no_shell_execution_outside_platform` | |
| AD-2 | `test_ad2_telegram_uses_outbound_polling_only` | |
| AD-3 | `test_ad3_indexes_rebuild_from_markdown_without_loss`, `test_ad3_reindex_cannot_reach_tier_2`, `test_ad3_no_artifact_is_both_rebuilt_and_backed_up`, `test_ad3_every_artifact_has_exactly_one_tier`, `test_tier_three_shares_no_file_or_directory_with_tier_two`, `test_no_tier_one_artifact_lives_inside_a_rebuildable_one`, `test_operational_store_sits_outside_every_markdown_tree` | Tier separation is physical; rebuild and backup sets are disjoint. The last three check it on the *paths*, which is where a rebuild actually reaches: the tier table can be perfect while `vector_index/` contains a Tier-1 file |
| AD-4 | `test_paths.py` — the layout rows, `test_personal_material_has_no_path_in_a_committed_scope`, `test_every_tiered_and_retention_managed_artifact_has_a_home` | `pm_ai/platform/paths.py` is the only place a directory layout is written down, so the four scope roots and every artifact's place in them are now asserted rather than remembered. Which scope a *new* record belongs to is still judgement — see below |
| AD-5 | `test_ad5_single_writer_owns_all_file_writes`, `db-confined-to-storage` | |
| AD-6 | `test_ad6_markdown_is_never_encrypted` | |
| AD-7 | `cli-owns-no-scheduling` | |
| AD-8 | `test_ad8_loopback_api_rejects_unauthenticated_requests` | |
| AD-9 | `test_ad9_connectors_own_no_scheduling`, `test_ad9_cursor_is_opaque_to_the_core` | |
| AD-11 | `test_ad11_no_filesystem_discovery_of_projects`, `test_an_unregistered_project_is_an_error_not_a_guess`, `test_registry_repository_paths_are_expanded_and_absolute` | The AST check forbids scanning for `.project-ai`; the resolver has no way to *invent* a repository path, so an unregistered project raises instead of resolving |
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
| AD-31 | `test_ad31_every_frontier_call_records_scope_provenance`, `test_ad31_personal_material_cannot_reach_a_project_destination`, `test_personal_material_has_no_path_in_a_committed_scope`, `test_the_personal_only_set_matches_the_scope_table`, `test_a_traversing_person_id_cannot_leave_the_enclave` | D1 — disclosure log + destination boundary. The three path tests close the layer below it: personal material has no *path* in a committed scope, and a `person_id` of `../..` cannot walk a report's record out of the enclave |
| AD-32 | `test_ad32_auto_execute_requires_all_three_conditions` (5 cases), `test_ad32_manual_transcripts_never_auto_execute` | D2 — source × speaker × verb |
| AD-33 | `test_ad33_source_refs_never_point_at_a_transcript`, `test_ad33_ledger_entries_are_self_contained`, `test_ad23_transcript_without_a_meeting_is_rejected` | D3 — cite the meeting, not the capture |
| AD-34 | `test_ad34_source_refs_follow_the_fixed_grammar`, `test_ad34_unresolvable_actors_never_become_raw_string_identities`, `test_ad34_connectors_do_not_mint_event_ids` | Reference grammar, actor resolution, natural key |
| AD-35 | `test_ad35_the_two_clocks_are_not_interchangeable`, `test_ad35_ledger_folding_is_deterministic`, `test_ad35_sweeper_will_not_declare_broken_without_coverage` | Two clocks; coverage-aware sweeping |
| AD-36 | `test_ad36_self_authored_events_are_excluded_from_evidence`, `test_ad36_every_class_m_mutation_is_recorded_for_attribution`, **`test_our_own_write_harvested_back_is_not_evidence`**, `test_connector_never_asserts_external`, `test_unrecognisable_mutation_makes_its_scope_uncertain` | pm-ai's own writes are never evidence. The first two tests passed while the AD was **defeated in code**: they handed `Provenance.PM_AI` straight to the evaluator, proving the downstream half, while the step that *derives* PM_AI from the executed-mutation ledger did not exist and the connector hard-coded `EXTERNAL`. The bolded test drives the real path |
| AD-37 | `test_ad37_concurrent_approval_from_two_surfaces_yields_one_execution`, `test_ad37_expired_proposals_cannot_execute` | Versioned CAS on shared entities |
| AD-38 | `test_ad38_disclosure_records_cannot_reach_a_committed_scope`, `test_ad38_no_committed_record_may_reference_personal_scope`, `test_ad38_project_scope_is_the_only_committed_scope`, `test_application_scope_holds_the_disclosure_ledger`, `test_gitignore_rules_cover_the_paths_the_resolver_returns` | Disclosure ledger is application-scoped; committed scopes never name personal material. The gitignore test pairs each `GITIGNORE_REQUIRED` rule with the path the resolver actually returns — move `transcripts/` and the rule still reports "protected" for a directory git tracks |

### Not mechanically enforced

Judgement calls that stay human — worth knowing so nobody assumes green means
compliant:

- **AD-4** (scope ownership) — the *layout* is now mechanical: `test_paths.py`
  asserts the four scope roots and every artifact's place in them, and
  `pm_ai/platform/paths.py` refuses an artifact/scope pair the table forbids. The
  *ownership decision* is what stays human — "is this configuration
  project-specific?", "does this record's subject make it personal or
  team-member?" — and nothing here can answer it. Adding an artifact to the
  layout tables is therefore a review point, not a mechanical step.
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
