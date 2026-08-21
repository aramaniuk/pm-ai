---
title: 'Storage writes through the resolver'
type: 'feature'
created: '2026-08-21'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'e1d9539'
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/scope-model.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `StorageService` writes everything beneath one directory handed to it at construction, into a subfolder named by flattening the scope to a string (`project_alpha/`, `personal/`). It also calls `datetime.now()` internally in three places, one of which becomes the monthly log-segment filename, so tests cannot pin timestamps or filenames.

**Approach:** Change the `StorageService` constructor to receive the resolver from story 1a and a clock function, and resolve every path through the resolver. Both are constructed in `pm_ai/app/wiring.py` and passed in. Re-point the existing tests that assert on the old flat layout.

**Depends on:** story 1a, which added `pm_ai/platform/paths.py`.

## Boundaries & Constraints

**Always:**
- `pm_ai.storage` and `pm_ai.platform` are sibling layers that may not import each other (`.importlinter`). `StorageService` cannot reach the resolver itself; `pm_ai/app/wiring.py` is the only module permitted to import both, so it constructs the resolver and passes it in.
- `now` stays an optional argument on `wiring.build()` — `tests/architecture/test_domain_invariants.py:18-23` calls it without one, and `wiring.py:37` is the one sanctioned system-clock read.
- One `StorageService` instance keeps serving several scopes: `tests/slice/test_r4_gate_fixes.py:246-254` persists an event under both a project scope and the personal scope through one instance.
- Constructing against an existing root reopens the operational store rather than reinitialising it (`tests/slice/test_r4_gate_fixes.py:194-209`).

**Ask First:**
- Changing `DataScope.__str__` — its output forms part of the event de-duplication key (`pm_ai/domain/events.py:155`), so a change invalidates every stored `seen` row.
- Weakening, deleting, or marking `xfail` any existing assertion. Moving a path a test asserts on is this story's work; changing what a test proves is not.

**Never:** No encryption. No capture-directory checking — that is story 1c. No new artifact locations beyond what the resolver already returns.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| A storage service built with a test resolver | an event is persisted to a project scope | the segment file appears under that project's resolved `.project-ai/memory/event_log/`, not under a flattened `project_alpha/` |
| The same service | an event is persisted to the personal scope | the segment appears under the personal scope's resolved path |
| The same service | the operational store is opened | it is at the resolver's operational path, outside every scope's Markdown tree |
| A clock pinned to a fixed instant | an event is persisted | the ingestion timestamp and the segment filename both derive from that instant, with no system-clock call |
| A clock pinned to a fixed instant | a mutation is recorded | its timestamp derives from the same clock |
| `wiring.build()` called with no clock argument | the daemon is built | it succeeds, using the system clock at `wiring.py:37` |
| A service constructed twice against the same roots | the second construction completes | the operational store is reopened and the mutation ledger from the first is readable |

</frozen-after-approval>

## Code Map

- `pm_ai/storage/service.py:121` — constructor, currently `(self, root: Path)`. Its only construction site is `wiring.py:39`.
- `pm_ai/storage/service.py:133-136` — `_segment()`, which builds the entire current layout and is what the resolver replaces.
- `pm_ai/storage/service.py:126` — opens the operational store at the flat root.
- `pm_ai/storage/service.py:139,146,230` — the three internal `datetime.now()` calls. `:146` also produces the segment filename.
- `pm_ai/app/wiring.py:36-39` — `build(root, project, *, now=None)`; where the resolver and clock are built and injected. `:37` is the sanctioned clock read.
- `tests/slice/test_vertical_slice.py:54,111,167` — assert the flat layout. `:83,108,111` reuse `storage._root` as a re-rootable value and need a resolver accessor instead.
- `tests/slice/test_transcript_slice.py:44` — the `_daemon(tmp_path)` fixture used by roughly fifteen tests.

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/storage/service.py` — accept the resolver and clock as constructor arguments; resolve every path through the resolver; remove the three internal clock reads.
- [x] `pm_ai/app/wiring.py` — construct the resolver and clock and inject both. Keep `now` optional.
- [x] `tests/slice/test_vertical_slice.py` — re-pointed the three path assertions through `storage.paths` and replaced both `storage._root` reuses with `tmp_path`. Paths only; no assertion changed what it proves.
- [x] `tests/slice/test_r4_gate_fixes.py`, `test_transcript_slice.py` — **no change needed**: both only pass `tmp_path` to `build()` as a root and assert no paths, so the re-pointing this story is about does not reach them. Verified by running them unmodified.
- [x] `tests/slice/test_storage_resolution.py` (new) — the behavioural coverage for the I/O matrix rows nothing asserted before: append-only ledgers, all four scopes, Tier 2 outside every Tier-1 path, the injected clock in a month that is not the current one, and a batch that must not be swallowed when the resolver refuses.

**Acceptance Criteria:**
- Given `uv run pytest`, then every test passes, the skip count stays at 30, and no new skip appears. The count is **175 passed** at the end of this story; the "119" in this story's draft predates story 1a, whose `tests/architecture/test_paths.py` puts the baseline at `e1d9539` at **157 passed, 30 skipped**.
- Given `uv run lint-imports`, then all 12 contracts hold and no module under `pm_ai/storage/` imports `pm_ai.platform`.
- Given a grep for `datetime.now` under `pm_ai/storage/`, then there are no matches.
- Given a service constructed twice against the same roots, then the mutation ledger from the first construction is still readable.

## Design Notes

The resolver has to be passed in rather than imported because `storage` and `platform` are siblings in the import graph and `core` may reach neither. Only `pm_ai.app` may import both:

```
pm_ai.app.wiring  ──builds resolver + clock──>  storage.StorageService
```

## Verification

- `uv run pytest -q -rs` — expected: 175 passed, 30 skipped (157 was the baseline at `e1d9539`; the story's original "119" predates story 1a's path tests).
- `uv run lint-imports` — expected: `Contracts: 12 kept, 0 broken.`
- `grep -rn "datetime.now" pm_ai/storage/` — expected: no output.

## Suggested Review Order

**The seam this story exists to move**

- Start here: the writer now receives its layout and its clock instead of deriving either.
  [`service.py:148`](../../../../pm_ai/storage/service.py#L148)

- The flattened `project_alpha/` directory replaced by a resolved path.
  [`service.py:226`](../../../../pm_ai/storage/service.py#L226)

- The protocol that lets storage take a resolver it may not import.
  [`ports/__init__.py:33`](../../../../pm_ai/ports/__init__.py#L33)

- Exactly one of a root or a resolver, so the production factory is reachable.
  [`wiring.py:37`](../../../../pm_ai/app/wiring.py#L37)

**The clock, in one place**

- Single read point; rejects a naive or non-UTC clock rather than writing a wrong-month segment.
  [`service.py:207`](../../../../pm_ai/storage/service.py#L207)

- Pinned against a 2019 instant, so a re-introduced system clock fails today, not in September.
  [`test_storage_resolution.py:255`](../../../../tests/slice/test_storage_resolution.py#L255)

**Data loss the review caught**

- Batch append isolated so a refused resolve rolls back its dedup rows.
  [`service.py:267`](../../../../pm_ai/storage/service.py#L267)

- The regression: a refused write must not swallow the batch forever.
  [`test_storage_resolution.py:326`](../../../../tests/slice/test_storage_resolution.py#L326)

**The disarmed guard, re-armed both ways**

- The static rule now resolves names bound to a ledger token, so a constant cannot blind it.
  [`test_static_rules.py:244`](../../../../tests/architecture/test_static_rules.py#L244)

- The rename-proof half: append twice, read back both entries in order.
  [`test_storage_resolution.py:92`](../../../../tests/slice/test_storage_resolution.py#L92)

- One spelling of each artifact key, in the layer both storage and platform may import.
  [`storage_tiers.py:26`](../../../../pm_ai/domain/storage_tiers.py#L26)

- A resolver refusal callers can catch without importing the platform layer.
  [`storage_tiers.py:30`](../../../../pm_ai/domain/storage_tiers.py#L30)

**Peripherals**

- Tier 2 checked against every Tier-1 path in all four scopes, not just `memory/`.
  [`test_storage_resolution.py:179`](../../../../tests/slice/test_storage_resolution.py#L179)

- A malformed project id fails at build, not mid-harvest.
  [`test_storage_resolution.py:383`](../../../../tests/slice/test_storage_resolution.py#L383)

- Re-pointed assertions only; nothing a test proves was changed.
  [`test_vertical_slice.py`](../../../../tests/slice/test_vertical_slice.py)
