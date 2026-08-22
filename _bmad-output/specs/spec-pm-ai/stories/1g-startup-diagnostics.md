---
title: 'Startup diagnostics'
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
- [ ] `pm_ai/platform/doctor.py` — new. A result type, four probes (extension support, keychain reachability, encryption-toggle state, `git` availability), a summary that runs all of them, and a `python -m` entry point.
- [ ] `tests/architecture/test_doctor.py` — new. One test per matrix row, faking an absent attribute, an unreachable keychain, an absent `git`, and a `git` that cannot answer.

**Acceptance Criteria:**
- Given an interpreter without `enable_load_extension`, when the probe runs, then it returns a failure result and raises nothing.
- Given a keychain that is unreachable, when the summary runs, then the other two probes still produce results.
- Given a keychain holding a key, when the probe runs, then the key's value appears nowhere in the result.
- Given no `git` on `PATH`, when the git probe runs, then it returns a failure result stating that captures will be refused, and raises nothing.
- Given a `git` that is present but cannot answer an exclusion query, then the result is distinguishable from the absent case.
- Given `uv run python -m pm_ai.platform.doctor` on this machine, then it exits reporting extension support present.
- Given `uv run pytest`, then all previously passing tests still pass and the skip count is unchanged.

## Design Notes

The extension probe checks `hasattr(connection, "enable_load_extension")` rather than comparing version numbers, because the failure is a property of how the interpreter was built, not of which version it is. A correct version compiled without the feature passes a version check and fails on first use — which is exactly the sequence this probe exists to break.

**Why the git probe asks for an answer and not just a binary.** `shutil.which("git")` proves a file exists. The capture guard needs git to *answer a question*, and the ways that fails — a build with no `check-ignore`, a wrapper that shells to something else, a `PATH` entry pointing at a stub — all pass a `which` check. Running one real query is the only probe that tests the thing the guard depends on, which is why the two results are reported separately: absent is an install, unanswering is an investigation.

## Verification

- `uv run python -m pm_ai.platform.doctor` — expected: a readable report; extension support and `git` both present on this machine.
- `uv run pytest -q -rs` — expected: previously passing tests still pass, skip count unchanged.
- `uv run lint-imports` — expected: `Contracts: 12 kept, 0 broken.`
