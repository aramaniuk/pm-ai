---
title: 'Derived-tier rebuild'
type: 'feature'
created: '2026-08-21'
updated: '2026-08-22'
status: 'ready-for-dev'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/storage-contract.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** pm-ai's recovery model says the derived tier — search indexes and the vector index — is disposable, because it can be reconstructed from the Markdown with zero loss. That property is what makes the system recoverable rather than merely local, and no code demonstrates it. There is no rebuild path at all. It is also the property most likely to quietly stop being true, since every index added later has to be reconstructible and nothing checks.

**Approach:** Add `pm_ai/storage/reindex.py` with three operations — take a comparable snapshot of the derived tier, drop it, and rebuild it from the Markdown. Every drop is validated against the tier table first, so an artifact from another tier cannot be deleted even if a caller passes one in.

**Depends on:** stories 1a and 1b, which placed the operational and derived stores in separate files. That separation is what makes dropping the derived tier structurally safe rather than carefully safe.

## Boundaries & Constraints

**Always:**
- Every drop routes through `assert_reindex_safe` (`pm_ai/domain/storage_tiers.py:175`), which raises `TierViolation` for any artifact outside the derived tier. The guarantee then holds by construction rather than by the caller remembering.
- **A drop must also spare the two sets that are in no tier at all.** `RETENTION_MANAGED` (captures, `telegram_cache/`) and `DIAGNOSTIC_ONLY` (`logs/`) are outside the tier model, so a check that only asks "is this Tier 3?" is correct, but a check that asks "is this *not* Tier 1 or 2?" would delete both. Assert against the derived-tier set, never against the complement.
- The snapshot is deterministic: snapshotting unchanged Markdown twice returns equal results, independent of file order or insertion order.
- A rebuild reproduces what the Markdown currently holds, not what it once held. Compaction is a deliberate recorded reduction of the Markdown, so a rebuild after compaction reproduces the compacted view — correct behaviour, not loss.
- Careful naming, because two automated checks match on the *text* of a call rather than its target: a call whose source text contains `event_log` alongside a logging method such as `.info(` is rejected, and in `pm_ai/storage/` any whole-file write whose call text mentions `event_log`, `commitments_log`, or `coaching_1on1_history` is rejected as a ledger overwrite. This story reads those ledgers constantly, so derived outputs must not be named after them — an identifier like `event_log_index` trips the check even when its target is a rebuilt index.

**Ask First:** Adding an artifact to `ARTIFACT_TIER`. Its contents are now derived from the scope trees, so adding one means declaring a `File` or `Collection` node — and `tests/slice/test_r4_gate_fixes.py:260-291` asserts the resulting sets.

**Never:** The Markdown is what a rebuild reads; it is never a rebuild target. No new index or cache that cannot be reconstructed from the Markdown — anything that cannot be is not derived state and belongs in a different tier. No schema migration; that is story 1i. No `pm-ai reindex` CLI command; the CLI is story 4.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| Derived state built from Markdown, then deleted, with the Markdown intact | the rebuild runs | a snapshot of the derived state equals the snapshot taken before deletion |
| Unchanged Markdown | snapshotted twice | both snapshots are equal, independent of file or insertion order |
| No derived state present at all | the drop runs | completes without error and does nothing |
| A drop target set containing the operational store | the drop is attempted | raises `TierViolation` before deleting anything |
| A drop target set containing an event-log directory | the drop is attempted | raises `TierViolation` — the Markdown is the source of truth |
| A drop target set containing `logs/` | the drop is attempted | raises `TierViolation` — `DIAGNOSTIC_ONLY` is outside the tier model, not inside the disposable one |
| A drop target set containing a `transcripts/` directory | the drop is attempted | raises `TierViolation` — captures are `RETENTION_MANAGED`, purged on their own schedule after verified conversion, never dropped by a reindex |
| A drop target set containing only derived artifacts | the drop runs | succeeds, and the operational store is still on disk afterwards |
| Markdown that was compacted after the first snapshot | the rebuild runs | reproduces the compacted view, and the difference is reported rather than treated as an error |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/scope_model.py:163` — `Tier` with its `rebuildable` and `backed_up` properties, encoding the three promises. It moved here on 2026-08-22; `storage_tiers` re-exports it.
- `pm_ai/domain/scope_model.py:761-775` — `ARTIFACT_TIER`, `REBUILD_TARGETS`, `BACKUP_TARGETS`, `RETENTION_MANAGED` and `DIAGNOSTIC_ONLY`, now **derived from the scope trees** rather than hand-written, with pairwise disjointness asserted at `:844-848`.
- `pm_ai/domain/storage_tiers.py:171-175` — `TierViolation` and `assert_reindex_safe`, which every drop routes through. This module now holds tier *behaviour* and re-exports the sets.
- `tests/architecture/test_domain_invariants.py:297-309` — the pre-written contract this story satisfies: snapshot, drop, rebuild, compare.
- `tests/architecture/test_paths.py:839` — already asserts `logs/` is excluded for a different reason than `transcripts/`, which is the distinction the two new matrix rows test from the rebuild side.
- `pm_ai/platform/paths.py` — the resolver from story 1a, which supplies the derived-tier paths.

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/storage/reindex.py` — new. Snapshot, drop, and rebuild the derived tier, every drop through `assert_reindex_safe`. Name derived outputs so no identifier contains a ledger name.
- [ ] `tests/architecture/test_rebuild.py` — new. One test per matrix row, including a drop target set containing the operational store.

**Acceptance Criteria:**
- Given `uv run pytest`, then `test_ad3_indexes_rebuild_from_markdown_without_loss` passes and the skip count falls by one.
- Given a drop target set containing the operational store, when the drop is attempted, then it raises `TierViolation` and no file is removed from disk.
- Given unchanged Markdown snapshotted twice, then the two snapshots are equal.
- Given `uv run lint-imports`, then all 12 contracts hold.

## Design Notes

**Why the drop is validated against a table rather than written carefully.** The failure being prevented is not a typo, it is a future caller. Growing the rebuild to cover a second index is a small, reasonable change, and nothing about it signals that adding the wrong path silently destroys pending external writes. Checking the target set against the tier table makes that change fail immediately instead of on the day someone runs a rebuild.

**Why determinism gets its own matrix row.** The main test compares a snapshot from before deletion with one from after. If the snapshot depends on the order files happen to be read, that comparison can fail on a correct rebuild or pass on a broken one. The order-independence row is what makes the main test mean something.

## Verification

- `uv run pytest -q -rs` — expected: one fewer skip, with no remaining skip naming `pm_ai.storage.reindex`.
- **Turn the skip ratchet in this same commit.** `tests/conftest.py` pins `EXPECTED_SKIPS` and fails the run in *both* directions, so landing this story makes the suite red until the constant matches: lower it by one (to 28, if the story-1 slices land in order and 1e has already lowered it to 29). The failure prints after pytest's stats line and names the direction and the delta. Do not raise it to absorb a skip this story introduced — a rising count is the regression the ratchet exists to catch.
- `uv run lint-imports` — expected: `Contracts: 12 kept, 0 broken.`
- Add the operational store to a drop target set and confirm the check goes red, then remove it.
