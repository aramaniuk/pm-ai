---
title: 'Connector registry and honest coverage'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Two gaps that must both close before a second connector exists. `pm_ai.connectors.registry` is imported by three pre-written tests (`test_domain_invariants.py:104,485`) and does not exist, so AD-27's taxonomy check and AD-34's no-minted-ids check have skipped since they were written. And `gitlab.py:66-70` builds its `CoverageWindow` as `started - 4h` to `started` unconditionally, tied to nothing that proves a fetch happened — so a provider declining with an empty `200` claims four hours of coverage it never had. `HarvestResult` has no way to say "I ran and learned nothing".

**Approach:** Add the registry with the two accessors the pre-written tests call, give `HarvestResult` an explicit ran-and-learned-nothing state, and derive `CoverageWindow` from what a fetch actually returned. Health probes for CAP-35's live check land here; credential handling is `8b`.

## Boundaries & Constraints

**Always:**
- **Coverage is evidence, not assumption.** `start` is the earliest point a fetch actually reached and `end` the moment it finished, both derived from returned pages. A connector that fetched nothing reports no coverage — never a window computed from the clock.
- **Three outcomes stay distinguishable**: harvested something, ran and learned nothing, and failed. Story 16's three verdicts need never-looked separable from looked-and-found-nothing, and a boolean cannot carry that.
- **A connector may not mint an event id** (AD-34) and may not emit a type outside `ObservedEventType` (AD-27). Both are already asserted by pre-written tests; this story makes them able to run.
- The registry is a first-party local allowlist. Keep the load path pluggable so `8b`'s enrolment and later verification attach without restructuring.

**Ask First:** Hot registration of a connector into a running daemon. CAP-35 requires it; there is no running daemon until `4d`, so this story registers at construction and the dynamic case belongs with the daemon.

**Never:** No credential handling, no token storage, no enrolment command — all `8b`. No scheduling: the registry lists and probes, and `run_harvest` stays the caller. No Graph code.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Registry enumerated | two connectors registered | both returned by `all_connectors()` | N/A |
| Sample events | any registered connector | `sample_events()` returns events with `id` unset | N/A |
| Fetch returns rows | provider returns two pages | coverage spans the earliest row reached to fetch end | N/A |
| Provider returns empty 200 | no rows, no error | ran-and-learned-nothing; **no** coverage window recorded | N/A |
| Provider 5xx | request fails | failure reported; no coverage, no cursor advance | connector-declared error |
| Partial page failure | page 1 ok, page 2 fails | coverage covers only what page 1 reached | error surfaced with partial result |
| Duplicate instance name | two registrations, one name | refused at registration | `DuplicateConnector` |
| Health probe | reachable provider | result within CAP-35's 10s bound | probe reports, never raises |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/registry.py` -- new; `all_connectors()`, `sample_events()`, `DuplicateConnector`, health probes
- `pm_ai/connectors/gitlab.py:66-70` -- the fabricated window, replaced
- `pm_ai/domain/harvest.py:34-40` -- `HarvestResult`, gaining the third outcome
- `pm_ai/domain/lifecycle.py:158-171` -- `CoverageWindow`, unchanged; only its construction moves
- `pm_ai/app/pipelines.py:35` -- `save_cursor(instance, cursor, coverage)`, which must not record a window that was never earned
- `tests/architecture/test_domain_invariants.py:104,485` -- the two pre-written tests that stop skipping
- `pm_ai/platform/doctor.py:96-100` -- `Probe`, the report-never-raise shape health checks follow

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/harvest.py` -- add the ran-and-learned-nothing state and make coverage optional rather than mandatory-and-therefore-invented
- [ ] `pm_ai/connectors/registry.py` -- add the registry and health probes
- [ ] `pm_ai/connectors/gitlab.py` -- derive coverage from returned rows; delete the `timedelta(hours=4)` construction
- [ ] `pm_ai/app/pipelines.py` -- do not save a coverage window that was not reported
- [ ] `tests/connectors/test_registry.py`, `tests/connectors/test_coverage_honesty.py` -- the matrix, with the empty-200 case explicit

**Acceptance Criteria:**
- Given a connector whose provider returns an empty `200`, when the harvest runs, then `coverage_windows(instance)` gains no entry — asserted against storage, not inferred from the return value.
- Given the suite runs, then `test_ad27_connectors_share_one_event_taxonomy` and `test_ad34_connectors_do_not_mint_event_ids` pass rather than skip, and the skip count falls by two.
- Given `grep -n "timedelta(hours=4)" pm_ai/connectors/gitlab.py`, then there is no match.

## Design Notes

Making coverage optional is the load-bearing change, and it is a type-level one: as long as `HarvestResult` requires a `CoverageWindow`, a connector that learned nothing must invent one to satisfy the constructor, which is precisely how the GitLab defect arose. The comment there — "reported in the return type so it cannot be forgotten" — was right about the mechanism and wrong about the failure: a mandatory field cannot be forgotten, but it can be fabricated, and a fabricated window is worse than a missing one because it reads as evidence.

`33c` will make this sharper still. The Graph channel-messages endpoint supports only `$top` and `$expand`, so there is no server-side filter to describe the window with — coverage there is knowable only from the pages actually walked.

## Verification

**Commands:**
- `uv run pytest tests/connectors/ -q` -- expected: all matrix rows pass
- `uv run pytest tests/architecture/test_domain_invariants.py -q -rs` -- expected: two fewer skips
- `uv run pytest -q` -- expected: no new failures; skip count two lower than baseline
- `uv run lint-imports` -- expected: contracts kept
