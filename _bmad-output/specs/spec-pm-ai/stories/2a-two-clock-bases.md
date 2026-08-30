---
title: 'Two clock bases'
type: 'feature'
created: '2026-08-29'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** AD-35 fixes two clocks and forbids substituting one for the other: `occurred_at` is provider-supplied and governs due-date reasoning, `ingested_at` is assigned locally and governs sweep reasoning. `NormalizedEvent` carries both fields (`events.py:141,144`) and storage stamps `ingested_at` at persist (`service.py:1101`), but no object states which clock governs which reasoning, and a provider timestamp dated two days into the future is accepted in silence.

**Approach:** Add `pm_ai/domain/clocks.py`: two functions naming the basis for each kind of reasoning, and one validator that refuses an implausible `occurred_at`. Nothing consumes it in this story — wiring it into the persist path is 2b.

## Boundaries & Constraints

**Always:**
- This module becomes the only place that names which clock governs which reasoning. A caller comparing against a due date asks `due_date_basis()`; it does not hard-code a field name.
- An implausible `occurred_at` is **flagged, never backfilled** from `ingested_at` (AD-35). The validator raises; it never substitutes and never returns a corrected value.
- Pure domain: sibling `pm_ai.domain` imports only (AD-30), no I/O, and no clock read of its own — the instant to compare against is supplied by the caller, as `StorageService` already does with its injected clock.

**Ask First:** Widening implausibility to AD-35's second clause — an `occurred_at` "preceding its meeting or repository epoch". No story supplies a per-entity epoch, so this story bounds the future side and a fixed floor only. Adopting a real epoch changes the signature and belongs with the story that owns meetings.

**Never:** No change to `NormalizedEvent`, `_append_batch`, or any caller — that is 2b. No coverage-window, `CoverageWindow`, or sweeper logic; AD-35's later clauses belong to story 15. No new dependency.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Bases are named | `due_date_basis()`, `sweep_basis()` | `"occurred_at"`, `"ingested_at"` | N/A |
| Plausible timestamp | aware UTC, 2h before the supplied `now` | returned unchanged | N/A |
| Future-dated | 48h after `now` | refused, message naming the skew | `ImplausibleTimestamp` |
| Within skew tolerance | 60s after `now` | returned unchanged — provider clocks drift by seconds | N/A |
| Absent | `None` | returns `None`; absence is a known state the entry records as `unknown` | N/A |
| Not UTC-aware | tz-naive, or a non-zero offset | refused — the comparison would be meaningless | `ImplausibleTimestamp` |
| Implausibly past | before `EARLIEST_PLAUSIBLE` | refused, naming the floor | `ImplausibleTimestamp` |
| Reference instant unusable | `now` tz-naive or non-UTC | refused at the boundary, naming which operand | `ImplausibleTimestamp` |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/clocks.py` -- new, the whole of this story
- `pm_ai/domain/events.py:141,144` -- the two fields this module describes
- `pm_ai/storage/service.py:584-600` -- `_at()`, the precedent for refusing a non-UTC instant once rather than trusting it in three places
- `pm_ai/storage/service.py:1101` -- where `ingested_at` is stamped; 2b's call site, untouched here
- `tests/architecture/test_domain_invariants.py:494` -- the pre-written test that stops skipping

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/clocks.py` -- add `due_date_basis()`, `sweep_basis()`, `ImplausibleTimestamp`, `validate_occurred_at()`, and `FUTURE_SKEW_TOLERANCE` / `EARLIEST_PLAUSIBLE` as named constants -- one place names the clock rule
- [ ] `tests/domain/test_clocks.py` -- unit-test every I/O matrix row -- the pre-written test covers two of eight

**Acceptance Criteria:**
- Given the suite runs, then `test_ad35_the_two_clocks_are_not_interchangeable` passes rather than skipping, and the run's skip count falls by one.
- Given `lint-imports` runs, then `pm_ai.domain.clocks` imports nothing outside `pm_ai.domain`.
- Given a refusal, then the message names the offending timestamp and the skew — an operator reading it can tell a wrong timezone from a wrong year.

## Spec Change Log

- **2026-08-29, multi-lens review (adversarial + edge-case).** The adversarial lens found the Always clause ("the instant to compare against is supplied by the caller") contradicting the pre-written test it must satisfy, which calls `validate_occurred_at(future_by_hours=48)` and supplies neither operand — two binding constraints an implementer would have had to silently choose between. Resolved in favour of both: plausibility is a question about the *offset*, so the validator accepts either a timestamp pair or a stated offset and still reads no clock. The Ask First now holds only AD-35's epoch clause, which genuinely has no owner. The edge-case lens added two unhandled paths — an implausibly *past* timestamp (AD-35 names it; the matrix omitted it) and a `now` that is itself naive or non-UTC. KEEP: the no-clock-read rule and the "flagged, never backfilled" rule are the two constraints this module exists to hold; neither may be relaxed to simplify the signature.

## Design Notes

Tolerance is a named constant, not a literal at the comparison. NTP skew is seconds and provider publication lag is minutes, so a small allowance absorbs real drift; 48 hours in the future is a wrong-timezone or wrong-year bug, which is the case worth refusing. Everything between is a judgement the constant makes visible in one place.

`validate_occurred_at` therefore compares against a supplied `now` rather than reading a clock: a domain module that reads the system clock is untestable at a boundary and would be the second clock read in a codebase whose storage service exists to have exactly one.

Two spellings, one check. Plausibility asks how far a timestamp sits from the reference instant, and that offset can be supplied directly instead of computed:

```
validate_occurred_at(at=None, *, now=None, future_by_hours=None) -> datetime | None
```

Pass `at` with `now` and the offset is derived; pass `future_by_hours` and it is stated. Both funnel into one comparison against `FUTURE_SKEW_TOLERANCE`, so there is one rule and no clock read on either path — which is what lets the pre-written test call it with an offset alone. Supplying both spellings, or neither, is a caller error.

## Verification

**Commands:**
- `uv run pytest tests/architecture/test_domain_invariants.py::test_ad35_the_two_clocks_are_not_interchangeable -q` -- expected: 1 passed, 0 skipped
- `uv run pytest tests/domain/test_clocks.py -q` -- expected: all matrix rows pass
- `uv run lint-imports` -- expected: contracts kept
- `uv run pytest -q` -- expected: no new failures; skip count one lower than the baseline
