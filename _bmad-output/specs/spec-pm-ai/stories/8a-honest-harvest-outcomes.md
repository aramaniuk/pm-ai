---
title: 'Honest harvest outcomes and coverage'
type: 'bugfix'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `gitlab.py:66-70` builds its `CoverageWindow` as `started - 4h` to `started` unconditionally, tied to nothing that proves a fetch happened — so a provider declining with an empty `200` claims four hours of coverage it never had. The cause is a type: `HarvestResult` **requires** a `CoverageWindow`, so a connector that learned nothing must invent one to satisfy the constructor. And it has no way to say "I ran and learned nothing" or "I ran and failed", so story 16's three verdicts cannot tell never-looked from looked-and-found-nothing.

Split from the original `8a` on 2026-09-02 at the sizing gate: the domain type change and its two call-site fixes are one concern; the connector registry is another (`8d`).

**Approach:** Give `HarvestResult` an explicit three-member outcome and an optional coverage, then fix the two call sites that fabricate or discard.

## Boundaries & Constraints

**Always:**
- **Coverage is evidence, not assumption.** `start` is the earliest point a fetch actually reached and `end` the moment it finished, both derived from returned pages. A fetch that returned nothing reports no coverage — never a window computed from the clock.
- **Three outcomes, all of them values.** Harvested something, ran and learned nothing, ran and failed. A raised exception cannot carry the coverage a partial harvest earned, so a page-one-succeeded, page-two-failed fetch returns its events, its real coverage and its failure together.
- **Coverage is expressed in `ingested_at`, not `occurred_at`.** `CoverageWindow`'s docstring (`lifecycle.py:158-164`) says so: it describes what the daemon did, not what happened in the world. Deriving `start` from a returned row's *provider* timestamp would replace fabrication with AD-35's mixed-clock defect.
- **`save_cursor` keys coverage on `coverage.connector_instance`** (`service.py:1357-1370`), not on its `instance` argument. The two must agree, or a window is stored under a key nothing reads back.

**Ask First:** Nothing.

**Never:** No registry and no health probes — `8d`. No credential handling — `8b`. No Graph code. No scheduling: `run_harvest` stays the caller.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fetch returns rows | two pages | coverage spans the earliest point reached to fetch end, in `ingested_at` | N/A |
| Rows returned, no usable clock | every provider timestamp absent or implausible | harvested-something, **no** coverage claimed — an unknowable start is not a guessable one | N/A |
| Provider returns empty 200 | no rows, no error | ran-and-learned-nothing; **no** coverage recorded | N/A |
| Provider 5xx | request fails | failure outcome; no coverage, cursor unmoved | value, not a raise |
| Partial page failure | page 1 ok, page 2 fails | events and page 1's real coverage returned **with** the failure; cursor advances to page 1's end only | failure outcome |
| Throttled | 429 with `Retry-After` | pages already walked returned with their real coverage; retry hint surfaced | failure outcome, retryable |
| Duplicate across pages | one row on pages 1 and 2 after re-pagination | deduped on the natural key; the span counted once | reported in `duplicates` |
| Cursor with no coverage | ran-and-learned-nothing | cursor still advances; `save_cursor` accepts an absent window explicitly | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/harvest.py:34-40` -- `HarvestResult`, gaining the outcome and the optional coverage
- `pm_ai/domain/lifecycle.py:158-171` -- `CoverageWindow`, unchanged; only its construction moves
- `pm_ai/connectors/gitlab.py:66-70` -- the fabricated window, replaced
- `pm_ai/app/pipelines.py:36` -- `save_cursor(instance, cursor, coverage)`, which must not record a window that was never earned
- `pm_ai/storage/service.py:1357-1370` -- how coverage rows are keyed

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/harvest.py` -- add the three-member `outcome`, a `failure` field, and make `coverage` optional rather than mandatory-and-therefore-invented
- [ ] `pm_ai/connectors/gitlab.py` -- derive coverage from returned rows; delete the `timedelta(hours=4)` construction
- [ ] `pm_ai/app/pipelines.py` -- do not save a coverage window that was not reported
- [ ] `tests/connectors/test_coverage_honesty.py` -- the matrix, with the empty-200 and partial-page cases explicit

**Acceptance Criteria:**
- Given a fetch returning two pages, then exactly one window is read back through `coverage_windows(instance)`, its `start` equals the earliest point actually reached and its `end` the fetch-completion instant — a **positive** bound assertion. An absence assertion cannot stand alone: `save_cursor` keys on `coverage.connector_instance`, so a fabricated window under a different key already returns `[]`, and a `grep` for `timedelta(hours=4)` is satisfied by `timedelta(minutes=240)`.
- Given a connector whose provider returns an empty `200`, then `coverage_windows(instance)` gains no entry — asserted against storage.
- Given a page-one-succeeded, page-two-failed fetch, then the return value carries page one's events, page one's coverage **and** the failure — a shape an exception cannot express, which is why the outcome is a value.

## Spec Change Log

- **2026-09-02, split at the sizing gate.** The original `8a` measured 2,275 tokens and held a `pm_ai.domain.harvest` type change with its call-site fixes alongside a `pm_ai.connectors` registry with health probes. The registry is now `8d`. Recorded because the two were reviewed together as one defect and separating them was a human's call, not an obvious one.
- **Inherited from the 2026-09-02 multi-lens review**, which found the failure outcome represented as "returns or raises" — unimplementable against this slice's own partial-page row, which requires both an error and page one's earned coverage. It also found the coverage acceptance criteria passing against a still-fabricating connector, and added the clock-basis rule, 429, duplicate rows across re-paginated pages, and a cursor advancing with no coverage.

## Design Notes

Making coverage optional is the load-bearing change, and it is a type-level one: as long as `HarvestResult` requires a `CoverageWindow`, a connector that learned nothing must invent one. The comment at `gitlab.py:69` — "reported in the return type so it cannot be forgotten" — was right about the mechanism and wrong about the failure. A mandatory field cannot be forgotten, but it can be fabricated, and a fabricated window is worse than a missing one because it reads as evidence.

`33b` makes this sharper: the Graph channel-messages endpoint supports only `$top` and `$expand`, so there is no server-side filter to describe a window with — coverage there is knowable only from the pages actually walked.

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_coverage_honesty.py -q` -- expected: all matrix rows pass
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
