---
title: 'Connector registry and honest coverage'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Two gaps that must both close before a second connector exists. `pm_ai.connectors.registry` is imported by two pre-written tests — `test_ad27_connectors_only_emit_core_declared_event_types` (`test_domain_invariants.py:94`) and `test_ad34_connectors_do_not_mint_event_ids` (`:483`) — and does not exist, so AD-27's taxonomy check and AD-34's no-minted-ids check have skipped since they were written. And `gitlab.py:66-70` builds its `CoverageWindow` as `started - 4h` to `started` unconditionally, tied to nothing that proves a fetch happened — so a provider declining with an empty `200` claims four hours of coverage it never had. `HarvestResult` has no way to say "I ran and learned nothing".

**Approach:** Add the registry with the two accessors the pre-written tests call, give `HarvestResult` an explicit ran-and-learned-nothing state, and derive `CoverageWindow` from what a fetch actually returned. Health probes for CAP-35's live check land here; credential handling is `8b`.

## Boundaries & Constraints

**Always:**
- **Coverage is evidence, not assumption.** `start` is the earliest point a fetch actually reached and `end` the moment it finished, both derived from returned pages. A connector that fetched nothing reports no coverage — never a window computed from the clock.
- **Coverage is expressed in `ingested_at`, not `occurred_at`.** `CoverageWindow`'s own docstring (`lifecycle.py:158-164`) says so: it describes what the daemon did, not what happened in the world. Deriving `start` from a returned row's *provider* timestamp would fix the fabrication by introducing AD-35's mixed-clock defect instead.
- **`save_cursor` keys coverage on `coverage.connector_instance`** (`service.py:1357-1370`), not on its `instance` argument. The two must agree, or a window is stored under a key nothing reads back.
- **Three outcomes stay distinguishable, and all three are values.** Harvested something, ran and learned nothing, and failed — carried by an explicit `outcome` on `HarvestResult`, not by "returns or raises". A raised exception cannot carry the coverage a partial harvest actually earned, so a page-1-succeeded, page-2-failed fetch must return its events, its real coverage and its failure together. Story 16's three verdicts need never-looked separable from looked-and-found-nothing, and an exception collapses them.
- **A connector may not mint an event id** (AD-34) and may not emit a type outside `ObservedEventType` (AD-27). Both are already asserted by pre-written tests; this story makes them able to run.
- The registry is a first-party local allowlist. Keep the load path pluggable so `8b`'s enrolment and later verification attach without restructuring.

**Ask First:** Hot registration of a connector into a running daemon. CAP-35 requires it; there is no running daemon until `4e`, so this story registers at construction and the dynamic case belongs with the daemon. **`8b` is bound by this:** `pm-ai connector add` therefore does not register into a live registry, and its success message must say the connector becomes active at the next start.

**Never:** No credential handling, no token storage, no enrolment command — all `8b`. No scheduling: the registry lists and probes, and `run_harvest` stays the caller. No Graph code.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Registry enumerated | two connectors registered | both returned by `all_connectors()` | N/A |
| Sample events | any registered connector | `sample_events()` returns events with `id` unset | N/A |
| Fetch returns rows | provider returns two pages | coverage spans the earliest point reached to fetch end, in `ingested_at` | N/A |
| Rows returned, no usable clock | every provider timestamp absent or implausible | harvested-something, **no** coverage claimed — an unknowable start is not a guessable one | N/A |
| Throttled | HTTP 429 with `Retry-After` | pages already walked returned with their real coverage; cursor unmoved; retry hint surfaced | failure outcome, retryable |
| Duplicate across pages | one row returned on pages 1 and 2 after re-pagination | deduped on the natural key; the span counted once | reported in `duplicates` |
| Probe exceeds its own bound | provider silent past 10s | reports `FAILING` **within** the bound, distinct from `ABSENT` | probe reports, never raises |
| Cursor with no coverage | ran-and-learned-nothing | cursor still advances; `save_cursor` accepts an absent window explicitly | N/A |
| Provider returns empty 200 | no rows, no error | ran-and-learned-nothing; **no** coverage window recorded | N/A |
| Provider 5xx | request fails | failure reported; no coverage, no cursor advance | connector-declared error |
| Partial page failure | page 1 ok, page 2 fails | events and page 1's real coverage returned **with** the failure; cursor advances to page 1's end only | failure outcome, not a raise |
| Duplicate instance name | two registrations, one name | refused at registration | `DuplicateConnector` |
| Health probe | reachable provider | result within CAP-35's 10s bound | probe reports, never raises |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/registry.py` -- new; `all_connectors()`, `sample_events()`, `DuplicateConnector`, health probes
- `pm_ai/connectors/gitlab.py:66-70` -- the fabricated window, replaced
- `pm_ai/domain/harvest.py:34-40` -- `HarvestResult`, gaining the third outcome
- `pm_ai/domain/lifecycle.py:158-171` -- `CoverageWindow`, unchanged; only its construction moves
- `pm_ai/app/pipelines.py:35` -- `save_cursor(instance, cursor, coverage)`, which must not record a window that was never earned
- `tests/architecture/test_domain_invariants.py:94,483` -- the two pre-written tests that stop skipping
- `pm_ai/platform/doctor.py:96-100` -- `Probe`, the report-never-raise shape health checks follow

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/harvest.py` -- add an explicit three-member `outcome`, a `failure` field, and make `coverage` optional rather than mandatory-and-therefore-invented
- [ ] `tests/conftest.py` -- lower `EXPECTED_SKIPS` from 27 to 25 **in this slice's commit** -- the ratchet fails when skips fall below the baseline (`conftest.py:88-104`), so unskipping two tests without this makes the suite red
- [ ] `pm_ai/connectors/registry.py` -- add the registry and health probes
- [ ] `pm_ai/connectors/gitlab.py` -- derive coverage from returned rows; delete the `timedelta(hours=4)` construction
- [ ] `pm_ai/app/pipelines.py` -- do not save a coverage window that was not reported
- [ ] `tests/connectors/test_registry.py`, `tests/connectors/test_coverage_honesty.py` -- the matrix, with the empty-200 case explicit

**Acceptance Criteria:**
- Given a fetch returning two pages, then exactly one window is read back through `coverage_windows(instance)`, its `start` equals the earliest point actually reached and its `end` the fetch-completion instant — a **positive** bound assertion. The absence assertions below cannot stand alone: `save_cursor` keys on `coverage.connector_instance`, so a fabricated window under a different key already returns `[]`, and `grep` for `timedelta(hours=4)` is satisfied by `timedelta(minutes=240)`.
- Given a connector whose provider returns an empty `200`, then `coverage_windows(instance)` gains no entry — asserted against storage.
- Given `registry.all_connectors()`, then it returns at least the GitLab instance, and `sample_events()` returns at least one event for each — **asserted before** the AD-27 and AD-34 loops. Both pre-written tests assert only inside a `for` body (`test_domain_invariants.py:94-114`, `:483-491`), so an empty registry passes them without executing a single assertion, and "the skip count falls by two" would be satisfied by a stub module. A vacuous pass is worse than a skip, which `-rs` at least shows.
- Given the suite runs, then `test_ad27_connectors_only_emit_core_declared_event_types` and `test_ad34_connectors_do_not_mint_event_ids` pass rather than skip, and `EXPECTED_SKIPS` is 25.
- Given a `GraphConnector`-shaped object, then `isinstance(obj, ConnectorPort)` holds — the port-conformance test (`:793-826`) covers three adapters and no connector, and its own docstring says annotations are documentation until something checks them.

## Spec Change Log

- **2026-09-02, multi-lens review.** The slice's verification proved nothing and its third outcome had no representation.
  **All three acceptance criteria were vacuous.** Both pre-written tests assert only inside a `for` loop over `all_connectors()`, so an empty registry passes them; the coverage absence assertion passes for a fabricated window stored under a different `connector_instance` key, since that is what `save_cursor` keys on; and the `grep` criterion is satisfied by respelling the arithmetic. Every one is now paired with a positive assertion. The AD-27 test's own comment records a vacuous-pass having already happened here once.
  **The failure outcome was "returns or raises", which cannot carry partial coverage** — so the matrix's own partial-page row, requiring both an error and page 1's earned coverage, was unimplementable. `HarvestResult` now carries an explicit three-member outcome and a failure field.
  **The named test did not exist.** `test_ad27_connectors_share_one_event_taxonomy` is not in the repo; the real name is `test_ad27_connectors_only_emit_core_declared_event_types` at `:94`, not `:104`, and AD-34 is at `:483`, not `:485` — the original cited grep hits inside test bodies rather than definitions.
  **`EXPECTED_SKIPS` is now a task.** `conftest.py:88-104` fails the run when skips fall *below* the baseline, demanding the baseline be lowered in the same commit — so this slice's original "skip count falls by two, no new failures" was self-contradictory.
  The edge-case lens added the clock-basis rule (deriving `start` from a provider timestamp would have replaced fabrication with AD-35's mixed-clock defect), 429, duplicate rows across re-paginated pages, and a cursor advancing with no coverage.
  **Contradiction with `8b` resolved** in this slice's favour: registration is construction-time, and `8b`'s success message says so.
## Design Notes

Making coverage optional is the load-bearing change, and it is a type-level one: as long as `HarvestResult` requires a `CoverageWindow`, a connector that learned nothing must invent one to satisfy the constructor, which is precisely how the GitLab defect arose. The comment there — "reported in the return type so it cannot be forgotten" — was right about the mechanism and wrong about the failure: a mandatory field cannot be forgotten, but it can be fabricated, and a fabricated window is worse than a missing one because it reads as evidence.

`33c` will make this sharper still. The Graph channel-messages endpoint supports only `$top` and `$expand`, so there is no server-side filter to describe the window with — coverage there is knowable only from the pages actually walked.

## Verification

**Commands:**
- `uv run pytest tests/connectors/ -q` -- expected: all matrix rows pass
- `uv run pytest tests/architecture/test_domain_invariants.py -q -rs` -- expected: two fewer skips
- `uv run pytest -q` -- expected: no new failures; skip count two lower than baseline
- `uv run lint-imports` -- expected: contracts kept
