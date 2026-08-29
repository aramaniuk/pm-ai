---
title: 'Open and sealed segments'
type: 'feature'
created: '2026-08-29'
status: 'draft'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `storage-contract.md` requires the event log to be "a directory of dated segments, exactly one open and appended to, earlier segments sealed and immutable" — that immutability is what lets compaction replace whole sealed segments. In code, `_segment` (`service.py:723-730`) simply formats whichever instant it is handed into `%Y-%m.md`. Nothing names the open segment and nothing refuses a write to an older one, so a replayed batch, a clock correction, or story 19's compaction running beside a late write can append into a month already summarised and deleted.

**Approach:** Make the open segment an explicit property of the ledger — derived from the injected clock, one per scope — and refuse any append that targets a different one.

## Boundaries & Constraints

**Always:**
- Exactly one segment per scope is open at a time, and it is the one the injected clock names. Sealing is derived from the clock, never stored — a stored flag is a second structure that can disagree with the filenames.
- An append targeting a sealed segment is **refused, not redirected** into the open one. Redirecting would silently re-date the entry, and `occurred_at` is the field that carries when it happened; the filename is not.
- No change to the `%Y-%m.md` naming or to any existing segment on disk. Today's writes all target the current month, so this story adds a guard rather than moving data.
- `NonUtcClock` (`service.py:591`) already refuses a clock that would misfile at a month boundary; this builds on that rather than re-checking it.

**Ask First:** Whether the daemon may append to the previous month during the first moments after a boundary — a batch harvested at 23:59 and persisted at 00:01. Refusing is correct by this rule and loses nothing (the entry lands in the new segment carrying its true `occurred_at`), but it is a visible behaviour choice.

**Never:** No compaction, no sealing ceremony, no deletion — story 19 owns those. No segment granularity change; `storage-contract.md` parks the monthly-versus-7-day mismatch with story 19.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Ordinary append | clock in the current month | appended to the open segment | N/A |
| Month rolls over | clock advances past a boundary | the new month becomes open; the old one is sealed with no ceremony | N/A |
| Write into a past month | an `at` older than the open segment | refused, naming both segments | `SealedSegment` |
| No segment yet | empty `event_log/` | the first append creates and opens one | N/A |
| Which is open | asked per scope | the one path, per scope, derived from the clock | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/storage/service.py:723-730` -- `_segment`, which gains the open/sealed distinction
- `pm_ai/storage/service.py:584-600` -- `_at`, the single clock read every segment name derives from
- `pm_ai/storage/service.py:980`, `:1111` -- the two append paths that must go through the guard
- `_bmad-output/specs/spec-pm-ai/storage-contract.md:99-103` -- the segmentation rule
- `tests/slice/test_storage_resolution.py:65,272` -- the off-month clock fixtures this story exercises

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/storage/service.py` -- name the open segment, add `SealedSegment`, and route both append paths through the guard -- immutability becomes a property rather than a description
- [ ] `tests/slice/test_storage_resolution.py` -- test the matrix, including a clock advanced across a boundary -- the boundary is the only place this behaviour is observable

**Acceptance Criteria:**
- Given a clock advanced into a new month, when an append is attempted against the prior month, then it is refused and the prior segment's bytes are unchanged.
- Given both append paths, when either targets a sealed segment, then the same refusal is raised — a guard on one path is not a guard.
- Given the existing suite, then no test changes behaviour: every current write targets the open segment already.

## Spec Change Log

## Design Notes

Derived rather than stored, for the same reason `derivation-services.md` derives the job graph from `inputs()`/`outputs()` instead of configuring it: two structures describing one fact will eventually disagree, and here the disagreement would be a sealed segment the writer believes is open — precisely the state compaction cannot survive.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_storage_resolution.py -q` -- expected: all pass, including the boundary rows
- `uv run pytest -q` -- expected: no new failures
