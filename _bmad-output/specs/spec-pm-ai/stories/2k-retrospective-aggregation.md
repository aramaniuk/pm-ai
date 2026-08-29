---
title: 'Retrospective aggregation'
type: 'feature'
created: '2026-08-29'
status: 'draft'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** CAP-10's second clause is the only part of the capability nothing addresses: `pm-ai retrospective --weekly` renders counts by category — decisions logged, proposals staged versus approved, commitments fulfilled versus broken — as a weekly trend. After 2f and 2h the ledger is readable and ordered; no one aggregates it.

**Approach:** Add the aggregation: fold a scope's entries into weekly buckets and count them by category. The function returns counts; the CLI that renders them is story 4's.

## Boundaries & Constraints

**Always:**
- Weeks are bucketed on **`occurred_at`**, not `ingested_at`. A retrospective asks what happened that week; AD-35 makes that the domain clock, and bucketing on ingestion would move a Friday decision into the next week because the laptop slept.
- Entries whose `occurred_at` is absent or flagged implausible (2a, 2b) are counted in a stated bucket of their own, never silently dropped and never placed by `ingested_at`. A retrospective that quietly omits records is worse than one that says how many it could not place.
- Categories come from 2c's closed enumeration. A category with no producer yet counts **zero**, explicitly — it is not absent from the result.
- Reads through 2h's accessor, so the retrospective and every other reader see the same ledger the same way.

**Ask First:** Three of CAP-10's four named categories have no producer in the tree — proposals staged and approved belong to the proposal lifecycle, commitments fulfilled and broken to story 15. Either this story ships an aggregation that can only report decisions today, or CAP-10's success clause is not fully demonstrable until 15 lands. The first is my reading; it is a visible partial delivery and yours to confirm.

**Never:** No CLI, no rendering, no formatting for a human — story 4. No index or cache; this reads the ledger. No cross-scope aggregation: each scope holds its own log, and merging them would put project counts and personal counts in one figure.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Several weeks | entries spanning four weeks | one bucket per week, each counting every category | N/A |
| Quiet week | a week with no entries | present with zero counts, not omitted — a gap is a finding | N/A |
| Category with no producer | `commitment_fulfilled` today | counted as zero, listed | N/A |
| Absent `occurred_at` | `unknown` in the entry | counted in the unplaceable bucket, reported alongside | N/A |
| Flagged timestamp | 2b's implausible flag | unplaceable, same as absent — a future-dated week is not a week | N/A |
| Empty log | scope with no segments | empty trend, no error | N/A |
| Week boundary | an entry at the exact boundary instant | falls in one bucket deterministically; the rule is stated | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/core/retrospective.py` -- new, the aggregation
- `pm_ai/core/event_log.py` -- 2h's accessor, the only way this reads
- `pm_ai/domain/event_entries.py` -- 2c's categories, which become the result's keys
- `pm_ai/domain/clocks.py` -- 2a, which says `occurred_at` governs this reasoning
- `_bmad-output/specs/spec-pm-ai/SPEC.md:67` -- CAP-10's success clause

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/retrospective.py` -- add weekly bucketing and per-category counts -- CAP-10's second clause gains an implementation
- [ ] `tests/core/test_retrospective.py` -- test every matrix row -- the unplaceable and zero-count rows are the ones a naive implementation gets wrong

**Acceptance Criteria:**
- Given a ledger with entries in three of four weeks, when aggregated over the period, then four buckets are returned and the quiet one holds zeros.
- Given entries with absent or flagged `occurred_at`, when aggregated, then the result reports how many could not be placed, and no bucket count includes them.
- Given the same ledger read twice, then the result is identical — the aggregation inherits 2f's deterministic fold and adds no ordering of its own.

## Spec Change Log

## Design Notes

Counting from the ledger on each call, for the reason 2j gives about the disclosure total: a stored count is a second structure that can disagree with the records it summarises, and CAP-10's guarantee is that the record is the truth. Story 18's search index is where this goes if the read ever becomes expensive.

## Verification

**Commands:**
- `uv run pytest tests/core/test_retrospective.py -q` -- expected: all pass
- `uv run lint-imports` -- expected: contracts kept
- `uv run pytest -q` -- expected: no new failures
