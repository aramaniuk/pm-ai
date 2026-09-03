---
title: 'Connector registry and health probes'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `pm_ai.connectors.registry` is imported by two pre-written architecture tests — `test_ad27_connectors_only_emit_core_declared_event_types` (`test_domain_invariants.py:94`) and `test_ad34_connectors_do_not_mint_event_ids` (`:483`) — and does not exist, so both have skipped since they were written. Nothing enumerates the connectors a daemon holds: `build()` puts them in a `Daemon.connectors` dict (`wiring.py:150-154`) that no architecture check can reach. And CAP-35's live health probe, which must answer within 10 seconds, has nowhere to live.

Split from the original `8a` on 2026-09-02 at the sizing gate.

**Approach:** The registry with the two accessors the pre-written tests call, plus health probes following `doctor.py`'s report-never-raise shape.

## Boundaries & Constraints

**Always:**
- **The registry is a first-party local allowlist.** Keep the load path pluggable so `8b`'s enrolment and later signature verification attach without restructuring.
- **A connector may not mint an event id** (AD-34) and **may not emit a type outside `ObservedEventType`** (AD-27). Both are already asserted by pre-written tests; this slice makes them able to run at all.
- **A probe reports; it never raises** — `doctor.py:22-24` states the rule and `Probe` (`:96-100`) is the shape. One broken connector must not hide three others.
- **A probe answers within CAP-35's 10 seconds**, including when the provider is silent. Exceeding its own bound is a failure of the probe, not of the provider.
- **`ABSENT` is not `FAILING`.** A connector configured with no credential is an ordinary first-run state; an unreachable provider is not. `doctor.py:64-72` already draws this line for the keychain.

**Ask First:** Hot registration of a connector into a running daemon. CAP-35 requires it; there is no running daemon until `4e`, so this slice registers at construction. **`8b` is bound by this:** `pm-ai connector add` does not register into a live registry, and its success message must say the connector becomes active at the next start.

**Never:** No credential handling, no token storage, no enrolment command — all `8b`. No `HarvestResult` or coverage changes — `8a`. No Graph code. No scheduling: the registry lists and probes.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Registry enumerated | two connectors registered | both returned by `all_connectors()` | N/A |
| Registry empty | none registered | returns empty; the architecture tests must then **fail**, not pass over nothing | N/A |
| Sample events | any registered connector | `sample_events()` returns at least one event, each with `id` unset | N/A |
| Duplicate instance name | two registrations, one name | refused at registration | `DuplicateConnector` |
| Probe, reachable provider | credential good | healthy, within 10s | probe reports |
| Probe, provider silent | no response past the bound | `FAILING` **within** 10s, distinct from `ABSENT` | probe reports |
| Probe, no credential | connector configured, nothing stored | `ABSENT` — a fresh install is not a broken machine | probe reports |
| Probe raises internally | an adapter bug | caught and reported as `FAILING` for that connector alone | never propagates |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/registry.py` -- new; `all_connectors()`, `sample_events()`, `DuplicateConnector`, the probes
- `tests/architecture/test_domain_invariants.py:94,483` -- the two pre-written tests that stop skipping, and the exact accessors they call
- `tests/architecture/test_domain_invariants.py:793-826` -- the port-conformance test, which covers three adapters and no connector
- `pm_ai/app/wiring.py:150-154` -- where connectors are built today, unreachable from any check
- `pm_ai/platform/doctor.py:22-24,64-72,96-100` -- the report-never-raise rule, the four `Health` states, and `Probe`
- `tests/conftest.py:42,88-104` -- `EXPECTED_SKIPS` and the ratchet that fails when skips fall below it

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/connectors/registry.py` -- add the registry and the health probes
- [ ] `tests/conftest.py` -- lower `EXPECTED_SKIPS` from 27 to 25 **in this slice's commit** -- the ratchet fails when skips fall below the baseline, so unskipping two tests without this makes the suite red
- [ ] `tests/connectors/test_registry.py` -- the matrix, including the empty-registry case

**Acceptance Criteria:**
- Given the registry, then `all_connectors()` returns at least the GitLab instance and `sample_events()` returns at least one event for each — **asserted before** the AD-27 and AD-34 loops. Both pre-written tests assert only inside a `for` body, so an empty registry passes them without executing a single assertion, and "the skip count falls by two" would be satisfied by a stub module. A vacuous pass is worse than a skip, which `-rs` at least shows; the AD-27 test's own comment records this having happened here once already.
- Given the suite runs, then both tests pass rather than skip and `EXPECTED_SKIPS` is 25.
- Given every registered connector, then `isinstance(connector, ConnectorPort)` holds — the port-conformance test covers `ScopePaths`, `GitVcs` and `StorageService` only, and its docstring says annotations are documentation until something checks them.
- Given an adapter whose probe raises, then the registry reports `FAILING` for it and healthy for its sibling — one broken connector hiding another is the failure the report-never-raise rule exists for.

## Spec Change Log

- **2026-09-02, `wiring.py` citations re-pointed after story 4a.** 4a added one import to `wiring.py`, shifting every line below it, and a parameter plus a docstring paragraph to `build()`, shifting the rest further. The numbers below named other code. **Line numbers only — no wording, no intent, no task, and no acceptance criterion changed.**

- **2026-09-02, split at the sizing gate.** Separated from the original `8a` (2,275 tokens), which held this registry alongside the `HarvestResult` type change now in `8a`.
- **Inherited from the 2026-09-02 multi-lens review**, which found all three of the original acceptance criteria vacuous, named a test that does not exist (`test_ad27_connectors_share_one_event_taxonomy`) with two wrong line numbers, and found `EXPECTED_SKIPS` unowned — making the original "skip count falls by two, no new failures" self-contradictory.

## Design Notes

The empty-registry row is the one that matters, and it is stated as a requirement on the *tests* rather than on the registry: with nothing registered, the AD-27 and AD-34 checks must fail. That inverts the usual direction because the defect being prevented is in the verification, not the code — two architecture gates that flip from skipped to green while proving nothing, which is how the AD-27 test spent its whole life until now.

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_registry.py -q` -- expected: all matrix rows pass
- `uv run pytest tests/architecture/test_domain_invariants.py -q -rs` -- expected: two fewer skips
- `uv run pytest -q` -- expected: no new failures, and the ratchet satisfied at 25
- `uv run lint-imports` -- expected: contracts kept
