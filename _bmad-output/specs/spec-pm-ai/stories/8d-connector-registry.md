---
title: 'Connector registry and health probes'
type: 'feature'
created: '2026-09-02'
status: 'ready-for-dev'
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
- **`all_connectors()` takes no arguments, because the pre-written tests call it that way** — and `GitLabConnectorAdapter` requires a project and a scope (`gitlab.py:21-23`), which `build()` supplies per project (`wiring.py:150-154`). So the registry is populated at composition rather than at import, and its relation to `Daemon.connectors` is stated: one holds the instances, the other enumerates them. A module-level registry that constructs its own would reintroduce a hardcoded project literal.
- **A connector may not mint an event id** (AD-34) and **may not emit a type outside `ObservedEventType`** (AD-27). Both are already asserted by pre-written tests; this slice makes them able to run at all.
- **`ConnectorPort` gains `sample_events()` and a health method.** Neither exists: `sample_events` appears once in the whole repo, in the AD-34 gate that calls `connector.sample_events()` (`test_domain_invariants.py:487`), and `ConnectorPort` declares only `name`, `system`, `emits` and `harvest`. So the probe rows had no interface to call and the `isinstance` conformance criterion was blind to the one method the gate invokes.
- **`Probe` and `Health` move to `pm_ai/domain/`.** Forced, not chosen: the port declares the health method, and `pm_ai.ports` may import only `pm_ai.domain` (`.importlinter:208-226`). They are pure value types, so the move is free — and `pm_ai.connectors` could not have imported them from `pm_ai.platform` anyway, the two being independent siblings (`.importlinter:22`). `Report` follows them; `run_all`'s signature does not change.
- **The probe is implemented by each connector, never by `doctor`.** Per the 2026-09-03 decision: `doctor` reports registry *membership* without contacting anything, and live probing is `pm-ai connector check` in `4j`. Nothing persists last-known health — stale health on a diagnostic screen is worse than none.
- **A probe reports; it never raises** — `doctor.py:22-24` states the rule and `Probe` is the shape. One broken connector must not hide three others.
- **A probe answers within CAP-35's 10 seconds**, including when the provider is silent — and the bound is on *waiting*, not on the work. A blocking socket call cannot be cancelled unless the adapter cooperates, so at the bound the probe reports `FAILING` and abandons the attempt. Stating this is the point: a bound the adapter is merely asked to honour is not a bound.
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
| Probe exceeds its own bound | an adapter that blocks past 10s | `FAILING` reported at the bound; the attempt is abandoned rather than cancelled | probe reports |
| Registry enumerated before composition | nothing built yet | empty, and said to be empty — not an error, and not a silent pass for the gates | N/A |

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
- [ ] `pm_ai/domain/health.py` -- move `Probe`, `Health` and `Report` here from `pm_ai/platform/doctor.py`, which then imports them -- `pm_ai.ports` may import only `pm_ai.domain`, so a port that names a health type cannot leave it in `platform`
- [ ] `pm_ai/ports/__init__.py` -- add `sample_events()` and the health method to `ConnectorPort`
- [ ] `pm_ai/connectors/gitlab.py` -- implement both on the existing adapter -- `sample_events` is what the AD-34 gate calls and it exists nowhere
- [ ] `pm_ai/connectors/registry.py` -- add the registry: `all_connectors()`, `sample_events()`, `DuplicateConnector`, and the per-connector probe invocation with its bound
- [ ] `tests/architecture/test_domain_invariants.py` -- add `assert connectors` above the loops at `:98` and `:485` -- the shape `test_ad38_project_scope_is_the_only_committed_scope` (`:736`) already uses; without it both gates pass over an empty registry, which is the defect the AD-27 test's own comment at `:88-91` records happening once
- [ ] `tests/conftest.py` -- lower `EXPECTED_SKIPS` **by two** in this slice's commit, from whatever the run then reports -- the absolute value is order-coupled with `23d`, which lowers it by one, and the ratchet fails in both directions (`conftest.py:81`)
- [ ] `tests/connectors/test_registry.py` -- the matrix, including the empty-registry case

**Acceptance Criteria:**
- Given the registry, then `all_connectors()` returns at least the GitLab instance and `sample_events()` returns at least one event for each — **asserted before** the AD-27 and AD-34 loops. Both pre-written tests assert only inside a `for` body, so an empty registry passes them without executing a single assertion, and "the skip count falls by two" would be satisfied by a stub module. A vacuous pass is worse than a skip, which `-rs` at least shows; the AD-27 test's own comment records this having happened here once already.
- Given the suite runs, then both tests pass rather than skip and `EXPECTED_SKIPS` is two lower than before this slice — a **delta**, because `23d` lowers it by one and the absolute value depends on which lands first.
- Given every registered connector, then `isinstance(connector, ConnectorPort)` holds **and `sample_events()` returns a non-empty tuple** — the `isinstance` check alone cannot observe `sample_events` unless the port declares it, which is why declaring it is a task rather than a convention. The port-conformance test covers `ScopePaths`, `GitVcs` and `StorageService` only, so this slice extends it to connectors.
- Given an adapter whose probe raises, then the registry reports `FAILING` for it and healthy for its sibling — one broken connector hiding another is the failure the report-never-raise rule exists for.

## Spec Change Log

- **2026-09-03, amended against the second multi-lens review and the day's decisions.**
  **The probe rows had no interface** (A5). `ConnectorPort` declares `name`, `system`, `emits` and `harvest`; `sample_events` appears exactly once in the repo, in the AD-34 gate that calls it on a connector, and `GitLabConnectorAdapter` has neither it nor a health method. Both are now declared on the port and implemented on the adapter (decision D-2), which is also what lets the `isinstance` criterion observe the method the gate actually invokes.
  **`Probe` and `Health` had to move** (A6, decision D-3) — forced by the first change rather than chosen: the port names the health type and `pm_ai.ports` may import only `pm_ai.domain`. `pm_ai.connectors` could not have reached `pm_ai.platform` either, the two being independent siblings, so "follow `doctor.py`'s shape" was ambiguous between an illegal reuse and a second `Health` enum.
  **Probing is per-connector and `doctor` no longer does it** (decision D-3b). `doctor` reports membership; `pm-ai connector check` in `4j` owns the live probe and CAP-35's bound. That also means this slice needs no CLI, so it stays an independent starter.
  **The ten-second bound was unimplementable as stated.** A blocking adapter call cannot be cancelled, so the bound is on waiting: at ten seconds the probe reports `FAILING` and abandons the attempt. A bound the adapter is merely asked to honour is not a bound.
  **`all_connectors()`'s shape was undecided.** Both pre-written tests call it with no arguments while `GitLabConnectorAdapter` requires a project and a scope, so the registry is populated at composition, and its relation to `Daemon.connectors` is now stated rather than left to be improvised as a module-level registry with a hardcoded project.
  **The central criterion had no task behind it** (C4). "Asserted before the AD-27 and AD-34 loops" is a statement about `test_domain_invariants.py`, and the Execution list named only this slice's own files — so an implementer would have added the non-emptiness assertion to the new test and left both gates passing over nothing. The assertion is now a task, in the file it belongs to.
  **`EXPECTED_SKIPS` was an absolute in an order-coupled pair** (C3). The ratchet fails in both directions and `23d` lowers it by one, so hard-coding 25 is false if `23d` lands first. Stated as a delta.

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
