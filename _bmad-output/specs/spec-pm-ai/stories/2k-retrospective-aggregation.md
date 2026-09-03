---
title: 'Retrospective aggregation'
type: 'feature'
created: '2026-08-29'
status: 'done'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** CAP-10's second clause is the only part of the capability nothing addresses: `pm-ai retrospective --weekly` renders counts by category — decisions logged, proposals staged versus approved, commitments fulfilled versus broken — as a weekly trend. After 2f and 2h the ledger is readable in arrival order; no one aggregates it.

**Approach:** Add the aggregation: group a scope's entries into weekly buckets and count them by category. Deliberately not the word *fold* — `ledger.fold` is a different operation and this uses none of it. The function returns counts; the CLI that renders them is story 4's.

## Boundaries & Constraints

**Always:**
- **Weeks are ISO-8601, in UTC**, and a boundary instant belongs to the week it opens. Not a preference: `datetime.isocalendar()` already answers it, and a hand-rolled rule is a second definition of a week that can disagree with every other tool the PM reads the same dates in.
- **The read is unbounded even when the period is not.** 2h's range is on `ingested_at` and these buckets are on `occurred_at`, so narrowing by segment would drop a July event ingested in August. Correct and a full scan; story 18's index is where that stops being acceptable.
- **An observed event buckets on `occurred_at`.** A retrospective asks what happened that week; AD-35 makes that the domain clock, and bucketing on ingestion would move a Friday decision into the next week because the laptop slept.
- **A pm-ai action buckets on `ingested_at`, and this is not a substitution.** 2c's roles decide it: a `SelfActionType` record's subject is pm-ai and pm-ai is its only witness, so it has no `occurred_at` by construction and never will. For such a record the write *is* the occurrence — there is no skew between doing the thing and recording it — so `ingested_at` is that record's own domain clock rather than a stand-in for a missing one. Filing every skill invocation and every compaction as *unplaceable* would report the whole of pm-ai's own activity as a measurement failure.
- **Unplaceable therefore means one thing only:** an *observed* event whose `occurred_at` is absent or flagged implausible (2a, 2b). Counted in a stated bucket, never silently dropped and never placed by `ingested_at` — for those records the two clocks genuinely differ and guessing is the AD-35 defect.
- Reading is arrival order and bucketing is by time-of-occurrence; the two steps use different clocks on purpose, and neither is derivable from the other.
- Categories come from 2c's closed enumeration. A category with no producer yet counts **zero**, explicitly — it is not absent from the result.
- Reads through 2h's accessor, so the retrospective and every other reader see the same ledger the same way.

**Ask First:** None blocking. Three of CAP-10's four named categories have no producer yet — proposals to the proposal lifecycle, commitments to story 15 — so this ships an aggregation whose shape is complete and whose counts are partial until those land. The design already says a category with no producer counts zero explicitly, which is what makes the gap visible rather than silent.

**Never:** No CLI, no rendering, no formatting for a human — story 4. No index or cache; this reads the ledger. No cross-scope aggregation: each scope holds its own log, and merging them would put project counts and personal counts in one figure.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Several weeks | entries spanning four weeks | one bucket per week, each counting every category | N/A |
| Quiet week | a week with no entries | present with zero counts, not omitted — a gap is a finding | N/A |
| Category with no producer | `commitment_fulfilled` today | counted as zero, listed | N/A |
| Absent `occurred_at`, observed event | `unknown` in a harvested entry | counted in the unplaceable bucket, reported alongside | N/A |
| Absent `occurred_at`, pm-ai action | any `SelfActionType` entry | bucketed on `ingested_at` — its own clock, not a substitute | N/A |
| Flagged timestamp | 2b's implausible flag | unplaceable, same as absent — a future-dated week is not a week | N/A |
| Empty log | scope with no segments | empty trend, no error | N/A |
| Week boundary | an entry at the exact boundary instant | ISO-8601 week in UTC; the instant belongs to the week it opens | N/A |

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
- Given the same ledger read twice, then the result is identical. Determinism comes from the bucketing rule, **not** from any ordering inherited upstream: 2h returns arrival order, and grouping by time-of-occurrence is an ordering this story imposes itself.

## Spec Change Log

- **2026-08-30, code review: `weekly` gains `since`/`until`, and a second counter.** Without a period the trend spanned first-entry to last-entry, so a quiet week at either *edge* was absent — the failure the zero-count rule exists to prevent, displaced to the ends — and a ledger spanning years produced a bucket per week with no way to ask for fewer. Both bounds are on the same clock as the buckets, so they carry no clock in their names. Separately, a `SelfActionType` record whose own clock is missing was being filed under `unplaceable`, contradicting that field's documented meaning; those are now `undated_actions`, because data we were given badly and a record we wrote without stamping are different failures. And every timestamp is converted to UTC before its week is read — `isocalendar()` on an offset-carrying value answers for its own wall clock, so two records at the same instant could land in different weeks.

- **2026-08-29, the week rule is ISO-8601 in UTC.** The matrix said "the rule is stated" without stating it, leaving ISO-versus-Sunday and local-versus-UTC to whoever implemented. `isocalendar()` already answers it, and a hand-rolled week is a second definition that disagrees with every other tool the same dates are read in.
- **The partial-delivery question is recorded rather than asked.** Three of CAP-10's four categories have no producer, so the shape ships complete and the counts fill in as stories 15 and the proposal lifecycle land. The zero-count rule is what keeps that visible.

- **2026-08-29, an acceptance criterion had gone false.** It read "the aggregation inherits 2f's deterministic fold and adds no ordering of its own", which stopped being true when 2h chose to return arrival order: this story reads arrival order and groups by time-of-occurrence, so the ordering is its own and its determinism comes from the bucketing rule. The Approach also said *fold* where it meant *group*; given how much confusion that one word has already caused, it now says group and says why.
- **The clock rule was half-written, and completing it removed a reporting defect.** It bucketed everything on `occurred_at` and filed the rest as unplaceable — which would have reported every skill invocation, every security notice and every compaction as a record the retrospective could not place, because a `SelfActionType` entry has no `occurred_at` by construction. 2c's role split answers it: for a record whose subject is pm-ai, the write *is* the occurrence, so `ingested_at` is that record's own domain clock rather than a stand-in. Unplaceable now means only an observed event whose provider clock is missing or unbelievable — the case where the two clocks genuinely differ.

## Design Notes

Counting from the ledger on each call, for the reason 2j gives about the disclosure total: a stored count is a second structure that can disagree with the records it summarises, and CAP-10's guarantee is that the record is the truth. Story 18's search index is where this goes if the read ever becomes expensive.

## Verification

**Commands:**
- `uv run pytest tests/core/test_retrospective.py -q` -- expected: all pass
- `uv run lint-imports` -- expected: contracts kept
- `uv run pytest -q` -- expected: no new failures
