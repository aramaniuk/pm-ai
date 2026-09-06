---
title: 'Startup diagnostics'
type: 'feature'
created: '2026-08-21'
updated: '2026-08-25'
status: 'done'
review_loop_iteration: 3
baseline_commit: 'fd71a03'
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/storage-contract.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Three failures cannot happen on the developer's machine and are near-certain on someone else's, and none is detectable today.

The vector-search extension only loads into an interpreter that exposes `enable_load_extension`. Stock macOS and python.org builds do not have it — the attribute is absent, not merely disabled. When it is missing, installation succeeds, the daemon starts, and the first write touching the index fails deep inside the storage layer. The second is Keychain retrieval breaking after an operating-system or interpreter upgrade: silent, unattended, and it presents as the morning briefing simply not arriving.

The third is `git`. The capture write path asks git whether a transcript directory would be carried into a commit, and refuses the write when it cannot get an answer — correctly, because unknown is not permission. So a machine with no `git` on `PATH` refuses **every** capture, with no other symptom: telemetry harvests, briefings and coaching all keep working, and the one thing that stops is the thing nobody notices stopping until a meeting has already happened. This is a hard runtime dependency wearing the costume of a developer convenience.

**Approach:** Add `pm_ai/platform/doctor.py` with one probe per condition, each returning a result object rather than raising, plus a `python -m` entry point so the probes can be run before anything else is trusted.

**Renegotiated 2026-08-22.** The fourth probe was added when the capture guard made `git` load-bearing. Its absence is the only failure here that is silent *and* selective — the daemon looks healthy because it mostly is.

**Depends on:** story 1d for the keychain port, story 1f for the encryption toggle whose state is reported, story 1c for the `VcsPort` the git probe exercises.

## Boundaries & Constraints

**Always:**
- Every probe **reports** rather than raises, so one failure does not prevent the others from running. A caller sees the full picture in one pass.
- Each failure result names its remediation. "Missing `enable_load_extension`" is useless without "install a uv-managed interpreter".
- The extension probe tests the attribute's presence on a real connection object, not the Python version string. Version is a proxy; the attribute is the thing that matters.
- Probes are read-only. They never create, migrate, or repair anything.
- The git probe reports the **binary** and the **answer** separately. `git` on `PATH` that cannot answer an exclusion question is a different failure from no `git` at all, and it takes a different repair.
- The git probe never needs a configured scope to run. It probes the dependency, not any particular capture directory — a machine can be missing `git` before a single project is enrolled.

**Ask First:** Adding a probe that requires network access, or one that writes to the keychain rather than reading from it. Having the git probe create a repository, or run inside one it was not pointed at.

**Never:** No `pm-ai doctor` CLI subcommand — no console entry point exists in `pyproject.toml`, and the CLI is story 4. This story ships callables plus a `python -m` runner, and story 4 surfaces them. No repair actions. No secret values in any probe output.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| An interpreter whose SQLite connection exposes `enable_load_extension` | the extension probe runs | returns a success result |
| An interpreter whose connection lacks it | the extension probe runs | returns a failure result naming the remediation; does not raise `AttributeError` |
| A reachable keychain holding the key | the keychain probe runs | returns a success result, and the key's value appears nowhere in the output |
| A reachable keychain with no key stored | the keychain probe runs | returns a distinct result meaning "reachable, key absent" — not the same as unreachable |
| An unreachable keychain | the keychain probe runs | returns a failure result, and the remaining probes still run to completion |
| `keyring` not installed | the keychain probe runs | returns a failure result naming the missing package, rather than raising |
| Encryption enabled | the toggle probe runs | returns a success result |
| Encryption disabled by the debug flag | the toggle probe runs | returns a warning result, not a healthy one |
| `git` present on `PATH` and answering | the git probe runs | returns a success result naming the version it found |
| No `git` on `PATH` | the git probe runs | returns a failure result whose remediation says captures will be refused until it is installed — not a generic "not found" |
| `git` present but an exclusion query fails or times out | the git probe runs | returns a failure result distinguishable from absent, so a caller can tell "no git" from "git cannot answer" |
| All probes | run together while one fails | every probe still produces a result, and the summary reports the overall state as failing |

</frozen-after-approval>

## Code Map

- `pm_ai/platform/__init__.py` — currently a docstring; OS-touching code belongs here, which is why the probes live in `platform` rather than `storage`.
- `pm_ai/platform/keychain.py` — added by story 1d; the keychain probe calls it and translates its typed errors into results.
- `pyproject.toml:33-38` — `[tool.uv] python-preference = "only-managed"`, the setting that is supposed to prevent the interpreter failure. The probe verifies the outcome rather than trusting the setting.
- `pm_ai/platform/vcs.py` — `GitVcs`, the adapter the git probe exercises. Class L under AD-1: allowlisted argv, `shell=False`, bounded timeout.
- `pm_ai/domain/vcs.py` — `VcsUnavailable`, the typed error the probe translates into a failure result rather than letting it escape.
- `ARCHITECTURE-SPINE.md` Deployment & operations — the `pm-ai doctor` list, which names keychain, Ollama, connector probes, index and disk sizes, and the encryption toggle. `git` belongs on it; this story ships the callable and story 4 surfaces it.
- `pyproject.toml` — no `[project.scripts]` entry exists, which is why this story ships a `python -m` runner instead of a command.

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/platform/environment.py` — new, and **folded in from open question 4**, resolved 2026-08-25: encryption is disabled only by `PM_AI_DISABLE_ENCRYPTION`, for one process, for short-term debugging. An environment variable needs no config loader and no entry point, which is what unblocked the toggle — story 1f had left it a `build()` keyword with no user-facing path. `TRUTHY` is an explicit allowlist rather than a truthiness test, because `=0` reads to a human as *off* and truthiness would read it as *on* — the one direction this flag must never fail in.
- [x] `pm_ai/platform/doctor.py` — new. `Health` (four states), `Probe`, `Report`, the four probes, `run_all`, and a `python -m` runner.
- [x] `pm_ai/app/wiring.py` — `encryption_disabled` becomes `bool | None`; `None` consults the environment, an explicit value overrides. The composition root is the one place ambient state may enter, and tests state intent rather than mutating the environment.
- [x] `tests/architecture/test_doctor.py` — new, 45 tests. Every row simulated rather than read off this machine: a test asserting the current interpreter's state would be asserting somebody's last command.

**Acceptance Criteria:**
- Given an interpreter without `enable_load_extension`, when the probe runs, then it returns a failure result and raises nothing.
- Given a keychain that is unreachable, when the summary runs, then the other two probes still produce results.
- Given a keychain holding a key, when the probe runs, then the key's value appears nowhere in the result.
- Given no `git` on `PATH`, when the git probe runs, then it returns a failure result stating that captures will be refused, and raises nothing.
- Given a `git` that is present but cannot answer an exclusion query, then the result is distinguishable from the absent case.
- Given `uv run python -m pm_ai.platform.doctor` on this machine, then it exits reporting extension support present. Confirmed: extension support OK, git 2.50.1 answering, encryption enabled, keychain FAILING because `keyring` is an uninstalled `runtime` extra — an honest report of this repo rather than a green one.
- Given `uv run pytest`, then all previously passing tests still pass and the skip count is unchanged. Result: 359 passed, 29 skipped.
- Given the process environment, then `PM_AI_DISABLE_ENCRYPTION` is named in code in exactly one module. A second reader is how a flag ends up honoured on one path and ignored on another, so it is enforced by AST rather than by convention.

## Spec Change Log

**2026-09-06, the verification command moved to `pm-ai doctor`.** Citation only; nothing this story built changed. `4c` retired `doctor.main()` and the `__main__` block on 2026-09-04 so the exit-code table has one declaration, and `python -m pm_ai.platform.doctor` now exits 0 having printed nothing — a reproduction step that passes without reproducing anything. The acceptance criterion above is left as written: it records a verification that was performed under the command that existed then, and rewriting it would falsify the record rather than correct it.

**2026-08-26, review pass 3 — a fifth probe, and the keychain's two causes split.** Prompted by reading the report the doctor actually prints: `keychain: the keyring package is not installed` was accurate and badly shaped.

- **`packages_installed`, and it runs first.** The keychain probe had become the de-facto detector for "the runtime stack is not installed" — a much larger fact reported obliquely, as a message about a keychain. Generic over any distribution set rather than a keyring check in disguise, and the default derives the `runtime` extra from installed metadata, so adding a dependency extends the check with no edit. It reports **before** the others because when the answer is no, three of the four after it are answering questions that do not matter yet.
- **Detected without importing.** `importlib.metadata.packages_distributions()` rather than `try: import`. Importing to find out is the obvious implementation and the wrong one — `fastapi`, `uvicorn` and `ollama` all cost real time and some have side effects, and a diagnostic must not pay a startup cost to report that one exists.
- **`KeychainBackendMissing`**, a subclass of `KeychainUnavailable`, so the probe branches on type rather than on message text. An incomplete install and a keychain that is present and refusing take different repairs, and the git probe already set that bar: it reports the binary and the answer separately because telling an operator to install something they already have sends them in a circle. The keychain now says *"Nothing about the OS keychain needs attention"* and points at the packages probe.

Three older tests had assumed four probes and, in one case, that this repo could report healthy — which it cannot, since the `runtime` extra is deliberately absent. That test had been passing by accident; it now stands the packages probe in explicitly. One assertion of mine was simply wrong: `py_test` normalises to `py-test`, which is *not* `pytest`, so PEP 503 must not fuse them — now asserted in both directions.

**2026-08-25, review pass 2.** Three things found by checking the story against what shipped rather than by reading it. All twelve matrix rows and every acceptance criterion were met; these were introduced around them.

- **`keychain_reachable` and `run_all` took an unannotated `keychain`** — implicitly `Any`, so every attribute read on the port was unverified. That is exactly the shape story 1k found in `SkillRegistry` and fixed, in a module written *after* that fix. Now annotated `KeychainPort`, and it earned its place immediately: the annotation exposed that the test fake implemented only `fetch` and had satisfied the parameter purely because the parameter was `Any`. Both fakes — here and in `test_cipher.py` — are now whole ports, with an `isinstance` assertion so a partial one cannot come back.
- **The `sqlite3.Error` branch was `# pragma: no cover`** when a raising `sqlite3.connect` is one monkeypatch away. Excluding a branch from *measurement* is not the same as it working, and this session already produced two guards shipped untested. Covered, and proved by letting the error propagate.
- **`main()` reports as uncovered and is not.** The `python -m` test exercises it in a child process, which `coverage` cannot observe. Recorded so 96% is not read as 4% untested — the real gaps were the two above.

Two pragmas remain, both defensible: constructing the real `MacOSKeychainAdapter` (which the story forbids a test from touching) and the `__main__` guard.

Also noted, not acted on: `LazyKeyCrypto` and `keychain_reachable` both take a whole `KeychainPort` while calling only `fetch`. A read-only sub-protocol would be least-privilege — neither has business storing or deleting a key — but that is a design change rather than a defect.

## Design Notes

**Four health states, not two.** `ABSENT` is separate from `FAILING` because "reachable, nothing stored" is an ordinary first run and "cannot reach it at all" is a broken machine — collapsing them sends an operator to fix a keychain that is fine. `WARNING` is separate from `OK` because encryption being off is not healthy even though nothing is broken, and separate from `FAILING` because the daemon is doing exactly what it was told. A `Report` is unhealthy if *anything* is not `OK`: encryption-off is the case that matters, since every other probe can pass while credentials sit in plaintext, and a summary reporting healthy then would be worse than no summary.

**An unrecognised toggle value gets its own report.** Someone who exported `PM_AI_DISABLE_ENCRYPTION=please` believes they disabled encryption and did not. Encryption stays on — fail-secure — but silently ignoring the value looks identical to honouring it, and the probe is the only place that confusion can surface.

The extension probe checks `hasattr(connection, "enable_load_extension")` rather than comparing version numbers, because the failure is a property of how the interpreter was built, not of which version it is. A correct version compiled without the feature passes a version check and fails on first use — which is exactly the sequence this probe exists to break.

**Why the git probe asks for an answer and not just a binary.** `shutil.which("git")` proves a file exists. The capture guard needs git to *answer a question*, and the ways that fails — a build with no `check-ignore`, a wrapper that shells to something else, a `PATH` entry pointing at a stub — all pass a `which` check. Running one real query is the only probe that tests the thing the guard depends on, which is why the two results are reported separately: absent is an install, unanswering is an investigation.

## Verification

- `uv run pm-ai doctor` — expected: a readable report; extension support and `git` both present on this machine. **Reproduce with this, not `python -m pm_ai.platform.doctor`**, which was the command when this story was verified: `doctor.main()` and the `__main__` block were retired on 2026-09-04 by `4c`, so the module form now prints nothing and exits 0 — it no longer fails, it silently verifies nothing. The probes themselves are unchanged; only the command that reaches them moved.
- `uv run pytest -q -rs` — expected: previously passing tests still pass, skip count unchanged.
- `uv run lint-imports` — expected: `Contracts: 12 kept, 0 broken.`
