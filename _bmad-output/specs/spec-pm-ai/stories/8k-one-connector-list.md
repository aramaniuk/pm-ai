---
title: 'pm-ai keeps one list of its connectors, and connector check reads that one'
type: 'refactor'
created: '2026-10-08'
status: 'done'
review_loop_iteration: 0
baseline_commit: '6df0b28523051c550f8ca7f9ee2b59e6cd1dd37d'
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** pm-ai keeps two lists of its connectors, and they can disagree.

When pm-ai starts, it builds the list of connectors it will harvest from and keeps it with the running system. It then copies that list into a second, process-wide list, and `pm-ai connector check` reads the copy rather than the original. The two have disagreed once already: `connector check` listed a connector that the harvest could not find. A process-wide list also means a second running system in the same process would silently replace the first one's list, and tests that start pm-ai leave their list behind for whatever runs next.

The architecture already says pm-ai's parts are handed what they need rather than finding it in a shared place (AD-30).

**Approach:** Remove the process-wide list. `connector check` asks the running system's own list, the same one the harvest uses.

## Boundaries & Constraints

**Always:**
- **One list.** The connectors `connector check` probes are exactly the connectors a harvest can reach, from the same object.
- **No process-wide connector state** remains anywhere in pm-ai.
- **What `connector check` prints and its exit codes are unchanged** on a machine where pm-ai can start: each connector's health line; exit 0 when all are healthy, 4 when any is not.
- **A machine with nothing enrolled still reads as a first run**: "no connectors are registered", exit 0.
- **The 10-second probe bound is unchanged.**

**Ask First:** Any change to what `connector check` prints or its exit codes beyond the one matrix row marked new.

**Never:** No change to how connectors are built, enrolled or harvested. No new command. The command table's own module-level state is a separate item and is not touched.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Healthy machine | projects enrolled, connectors healthy | each connector's line; exit 0 | N/A |
| An unhealthy connector | one probe fails or times out | its line marked failing; exit 4 | N/A |
| Nothing enrolled | no project, no daemon | "no connectors are registered", first run; exit 0 | N/A |
| pm-ai could not start for another reason | e.g. `config.toml` unreadable | **new:** refused, naming the reason, instead of probing connectors the running system will not use | existing refusal, exit 3 |
| An enrolled connector | enrolled after start-up's built-ins | listed by `connector check` and reachable by a harvest — the same instance | N/A |
| Two systems built in one process | tests build twice | each sees only its own connectors | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/registry.py:272-322` -- the global: `_DEFAULT = ConnectorRegistry()` (274), `install` (277, replaces), `default_registry` (301), module-level `all_connectors` / `sample_events` / `check_health` (306+); all in `__all__` (65). Module docstring 1-34 states the global's rationale and must be rewritten. Delete the global and its functions
- `pm_ai/connectors/registry.py:134-270` -- `ConnectorRegistry` (`register`, `all_connectors`, `instances`, `sample_events`, `check_health(timeout=HEALTH_PROBE_SECONDS)`); stays. `run_bounded` (94) untouched
- `pm_ai/app/wiring.py:39`, `:376-379` -- the only writer: imports `install as install_connectors`, builds a registry from `daemon.connectors` and installs it; remove. `Daemon.connectors: dict[str, ConnectorPort]` (111) is the one inventory; `run_harvest` (`pipelines.py:69-70`) reads it
- `pm_ai/app/wiring.py:355-360`, `:629-632` -- comments recording the earlier divergence; update
- `pm_ai/app/entry.py:69`, `:147-160` -- the only reader: `check_health as probe_connectors`, passed as `lambda: probe_connectors()` with a comment calling it "deliberately independent of `daemon`". Replace with a binder over the daemon, after the pattern of `_dashboard(daemon)` (218-243)
- `pm_ai/app/entry.py:840-923` -- `_compose`: no project → no daemon (845); `build()` raising (850-913); config unreadable/refused after `build()` succeeded, daemon discarded (914-922) — the new matrix row
- `pm_ai/surfaces/cli/dispatch.py:687`, `:292-308` -- `_connector_check`; `Context.probe_connectors` docstring says "the registry is a property of the process" and "needs no daemon" — reword. Type stays `Callable[[], Report]`
- `pm_ai/ports/__init__.py:842` -- `DaemonPort`; its docstring rules out exposing connectors, so the probe is not added to it
- Tests on the global: `tests/architecture/test_domain_invariants.py:105/113, :548/550, :948/950` (read `registry.all_connectors()` after `_daemon(tmp_path)`; use the daemon's connectors); `tests/connectors/test_registry.py:66` (`_isolated_default`), `:363-380`, `:384`; `tests/connectors/test_graph_calendar_mapping.py:1257-1261`; `tests/core/test_connector_enrolment.py:422-452, :546, :708` (`_instances()` via `default_registry()`); `tests/surfaces/test_cli_subcommands.py:191-212` (`registry` fixture installs fakes), `:225` (`quick_bound`), `:479-553` (pass only because the probe reads the global), `:566`. `tests/slice/test_every_enrolled_project.py:812` must pass unchanged

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/connectors/registry.py` -- remove the global and its module-level functions; rewrite the module docstring -- no process-wide state
- [x] `pm_ai/app/wiring.py` -- stop installing; update the divergence comments -- one inventory
- [x] `pm_ai/app/entry.py` -- bind `connector check`'s probe to the daemon's connectors; with no daemon because nothing is enrolled, an empty report; with no daemon for any other reason, refuse naming it -- the probe reads the one list
- [x] `pm_ai/surfaces/cli/dispatch.py` -- reword the `probe_connectors` docstring -- notes match the code
- [x] `tests/` -- move every test off the global per the Code Map; one test per matrix row, including the same-instance check for an enrolled connector and two builds in one process -- the matrix is the contract
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark resolved the AD-30 global entry (:807-809) and the test-gate leak entry (:470-472)

**Acceptance Criteria:**
- Given the change, when the source tree is searched, then no module-level mutable connector list remains.
- Given the change, when `uv run pytest` runs, then everything passes with 24 skipped.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass, 24 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

## Suggested Review Order

**The one list**

- connector check's probe is bound to the daemon's own connectors; no daemon means no probe.
  [`entry.py:216`](../../../../pm_ai/app/entry.py#L216)

- With no probe, the existing refusal sentence applies; nothing enrolled is still a first run.
  [`dispatch.py:691`](../../../../pm_ai/surfaces/cli/dispatch.py#L691)

- The global and its functions are gone; the docstring says where another load path attaches.
  [`registry.py`](../../../../pm_ai/connectors/registry.py)

**Tests**

- A scan of all of pm_ai refuses any module-level connector list or global rebinding one.
  [`test_static_rules.py:915`](../../../../tests/architecture/test_static_rules.py#L915)

- A daemon built first still probes only its own connectors after a second build.
  [`test_cli_subcommands.py:691`](../../../../tests/surfaces/test_cli_subcommands.py#L691)
