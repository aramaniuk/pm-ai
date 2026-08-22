---
title: 'Keychain port and macOS adapter'
type: 'feature'
created: '2026-08-21'
updated: '2026-08-22'
status: 'ready-for-dev'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/storage-contract.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The storage contract says the master encryption key lives in the macOS Keychain, so the daemon can start unattended without prompting for a passphrase. There is no keychain access of any kind in the codebase, and therefore nowhere for a key to live.

**Approach:** Add a `KeychainPort` protocol to `pm_ai/ports/` that stores, fetches, and deletes a named secret, and a macOS adapter in `pm_ai/platform/keychain.py` implementing it over the `keyring` package. Nothing encrypts anything yet — this story only establishes custody.

## Boundaries & Constraints

**Always:**
- `keyring` is imported **inside the function that uses it**, never at module top level. It is declared in the `runtime` extra and is not installed in this environment, and the test suite's `mod()` helper (`tests/architecture/test_domain_invariants.py:29-32`) turns a `ModuleNotFoundError` into a *skipped* test. A top-level import would therefore convert this story's tests into skips that read as coverage. A lazy import raises a real error at call time.
- The port is expressed in built-in types (`str`, `bytes`) so it can be implemented by a fake in tests.
- `pm_ai/ports/` may import only `pm_ai.domain`; follow the existing `@runtime_checkable Protocol` shape at `pm_ai/ports/__init__.py:20-140`.
- OS-specific APIs live only in `pm_ai/platform/`. This is enforced by the `os-behind-platform` contract in `.importlinter`, so `keyring` may be imported there and nowhere else.

**Ask First:** Choosing a key location other than the macOS Keychain, or a service/account naming scheme that a later Linux adapter could not mirror.

**Never:** No probes or health checks; those are story 1g, which now also probes `git`. No real Keychain access in the test suite — tests use a fake implementation of the port. No secret written anywhere durable outside the keychain: not to the event log, not to diagnostics, not to a `Cursor`. No encryption or cipher code; that is stories 1f and 1g.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| A fake keychain, empty | a secret is stored under a name, then fetched by that name | returns the same secret |
| A fake keychain holding a secret | it is deleted, then fetched again | raises a typed not-found error |
| A fake keychain holding a secret | a different secret is stored under the same name | the later value is returned on fetch |
| A fake keychain, empty | a name that was never stored is fetched | raises a typed not-found error, not `None` |
| The macOS adapter, with `keyring` not installed | any method is called | raises a typed error naming the missing package, at call time rather than at import time |
| The macOS adapter, with the keychain unreachable | any method is called | raises a typed error distinguishable from not-found, so a caller can tell "no key" from "cannot ask" |
| `pm_ai.platform.keychain` | imported while the `runtime` extra is absent | imports successfully |

</frozen-after-approval>

## Code Map

- `pm_ai/ports/__init__.py:20-140` — the five existing protocols (`ConnectorPort:20`, `ScopePathPort:34`, `VcsPort:84`, `StoragePort:119`, `SkillPort:131`), whose shape `KeychainPort` should follow. The spine's ports inventory lists `KeychainPort` already; it does not exist, and this story is what makes that true.
- `pm_ai/platform/paths.py` and `pm_ai/platform/vcs.py` — the two adapters already here, and the naming precedent: an adapter with no service behind it is named for what it is (`ScopePaths`, `GitVcs`). A keychain adapter does have a service behind it, so `<Service><Noun>Adapter` applies.
- `pyproject.toml` — the `runtime` extra declares `keyring==25.7.0`; it is not installed here, which is why the import must be lazy.
- `tests/architecture/test_domain_invariants.py:29-32` — the `mod()` helper whose skip-on-`ModuleNotFoundError` behaviour makes a top-level optional import dangerous.

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/ports/__init__.py` — add `KeychainPort` with store, fetch, and delete, plus its typed not-found and unavailable errors.
- [ ] `pm_ai/platform/keychain.py` — new. macOS adapter over `keyring`, imported inside each method.
- [ ] `tests/architecture/test_keychain.py` — new. One test per matrix row, using a fake port implementation.

**Acceptance Criteria:**
- Given the `runtime` extra is not installed, when `uv run pytest` runs, then no test is skipped because of a missing `keyring` import, and `pm_ai.platform.keychain` imports successfully.
- Given `uv run lint-imports`, then all 12 contracts hold and `keyring` is imported nowhere outside `pm_ai/platform/`.
- Given a fake keychain, when a secret is stored, fetched, deleted, and fetched again, then the second fetch raises a typed not-found error.
- Given `uv run pytest`, then the skip count stays at 30 — this story un-skips nothing, because no pre-written test covers key custody.

## Design Notes

The reason the lazy import is a hard rule rather than a preference: with `keyring` absent, a module-level import makes every test in this story skip, and the suite reports green. The failure mode is not a crash, it is silent non-coverage — which is exactly what a key-custody test exists to rule out.

## Verification

- `uv run pytest -q -rs` — expected: 30 skipped, and no skip reason mentioning `keyring`.
- `uv run python -c "import pm_ai.platform.keychain"` — expected: silent success with the `runtime` extra absent.
- `uv run lint-imports` — expected: `Contracts: 12 kept, 0 broken.`
