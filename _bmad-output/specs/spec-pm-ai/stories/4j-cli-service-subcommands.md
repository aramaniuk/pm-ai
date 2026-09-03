---
title: 'The three service subcommands'
type: 'feature'
created: '2026-09-03'
status: 'ready-for-dev'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `4c` builds the console script, the dispatch table and the exit-code table, and reaches exactly one service: `1g`'s probes, through `doctor`. Three services then exist with no way to invoke them — `4b`'s `enrol`, `4a`'s `load_config`, and `8d`'s per-connector health probes. Each is a dispatch entry over work another slice already did.

**Approach:** Add the three leaves — `pm-ai key enrol`, `pm-ai config show`, `pm-ai connector check` — to the table `4c` created, reusing its exit codes and adding none.

## Boundaries & Constraints

**Always:**
- **Dispatch only.** No adapter construction, no business logic, no service reimplemented. Each leaf maps arguments to a call and a return code to an exit code.
- **`4c`'s exit-code table is reused verbatim and not extended.** `4c`'s `Never` forbids other slices adding to it, and this slice is the one most tempted to.
- **Key material never reaches a stream.** `4b` guarantees it does not leave the keychain; this slice is where that becomes observable, because `4b` has no surface to assert it against.
- **`config show` marks a default as a default.** A value the operator set and a value they inherited look identical in a dump, and the difference decides whether they think the file is doing anything.
- **`connector check` is where CAP-35's ten-second bound lives.** `doctor` reports registry membership without contacting anything; live probing is here, per the 2026-09-03 decision. A probe exceeding its own bound is the probe's failure, not the provider's.

**Ask First:** Nothing.

**Never:** No new exit code. No enrolment logic, no config parsing, no health-probe implementation — `4b`, `4a` and `8d` own those. No `project add` (`4d`) and no `setup` (`4h`). No scheduling: `connector check` runs when invoked.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Enrolment on a clean keychain | no key stored | `4b`'s service invoked with `Daemon.keychain`; success reported without echoing the key | exit `0` |
| Enrolment when a key exists | keychain holds one | refused, naming the data-loss consequence | `KeyAlreadyEnrolled`, exit `3` |
| Enrolment with the keychain unreachable | no backend, or the OS refuses | the three failure modes stay distinguished, as `ports` distinguishes them | exit `3` |
| Config shown, file present | a valid `config.toml` | every key printed, each marked set or default | exit `0` |
| Config shown, file absent | clean machine | defaults printed, all marked as defaults — a first run, not an error | exit `0` |
| Config shown, file refused | unparseable, or an encryption key present | the loader's own message, verbatim | `ConfigRefused`, exit `3` |
| Connector check, all healthy | credentials good | each connector's probe result printed | exit `0` |
| Connector check, one silent | a provider past the bound | that connector `FAILING` within 10s, its siblings still reported | exit `4` |
| Connector check, empty registry | nothing registered | says so; an empty registry is a first-run state | exit `0` |

</frozen-after-approval>

## Code Map

- `pm_ai/surfaces/cli/dispatch.py` -- `4c`'s table and exit-code mapping, which this slice adds three leaves to
- `pm_ai/ports/__init__.py` -- `4c`'s daemon Protocol; if a leaf needs a member it does not name, the Protocol grows here
- `pm_ai/core/enrolment.py` -- `4b`'s `enrol` and `KeyAlreadyEnrolled`
- `pm_ai/ports/__init__.py:211-247` -- `KeychainPort` and its three distinct failure types, reused not collapsed
- `pm_ai/core/config.py:98,168` -- `Config` and `load_config`; `Config()`'s field defaults are what "marked as default" compares against
- `pm_ai/connectors/registry.py` -- `8d`'s registry and per-connector probes
- `pm_ai/app/wiring.py:47` -- `Daemon.keychain`, added by `4c`

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- add `key enrol`, `config show` and `connector check` as leaves on `4c`'s table -- three mappings, no new exit code
- [ ] `tests/surfaces/test_cli_subcommands.py` -- one test per matrix row, `main()` called with an explicit argv, asserting the exact exit integer

**Acceptance Criteria:**
- Given `pm-ai key enrol` succeeds, when stdout and stderr are captured and searched, then the key material appears in neither — the surface half of `4b`'s guarantee, which `4b` cannot assert because it has no surface.
- Given `pm-ai config show` on a machine with no `config.toml`, then it exits `0` and every printed key is marked a default — absence is a first run, and an unmarked default reads as a setting.
- Given `pm-ai connector check` with one connector silent past the bound, then that connector reports `FAILING`, every sibling still reports, and the exit code is `4` — one broken connector hiding another is the failure `8d`'s report-never-raise rule exists for.
- Given the set of exit codes this slice's tests assert, then it is a subset of the integers `4c` declares — asserted against `4c`'s table, because a fourth convention invented here is invisible to `4c`'s own tests.
- Given `lint-imports` runs, then `pm_ai.surfaces` imports no module from `pm_ai.app` and no OS client — the contracts `4c` establishes hold with three more leaves.

## Spec Change Log

- **2026-09-03, split from `4c` at the sizing gate.** `4c` reached 2340 body tokens once the second multi-lens review's findings were applied. These three leaves are additive dispatch entries over services other slices already build, so they carve off cleanly; `4c` keeps what cannot be split — the console script, the daemon Protocol, the absent-case translation, both tables, and `doctor`.
  Carried over from that review: the key-material criterion, which the review's C7 noted `4b` cannot assert at its own checkpoint because it has no surface. And one knock-on in the right direction — `connector check` living here rather than in `8d` means **`8d` needs no CLI** and returns to being an independent starter, undoing the build-order lengthening the 2026-09-03 decision recorded.

## Verification

**Commands:**
- `uv run pytest tests/surfaces/test_cli_subcommands.py -q` -- expected: all matrix rows pass, exact exit codes
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
- `uv run mypy` -- expected: clean
