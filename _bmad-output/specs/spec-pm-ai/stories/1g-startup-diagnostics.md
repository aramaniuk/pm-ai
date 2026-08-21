---
title: 'Startup diagnostics'
type: 'feature'
created: '2026-08-21'
status: 'ready-for-dev'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/storage-contract.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Two failures cannot happen on the developer's machine and are near-certain on someone else's, and neither is detectable today.

The vector-search extension only loads into an interpreter that exposes `enable_load_extension`. Stock macOS and python.org builds do not have it — the attribute is absent, not merely disabled. When it is missing, installation succeeds, the daemon starts, and the first write touching the index fails deep inside the storage layer. The second failure is Keychain retrieval breaking after an operating-system or interpreter upgrade: silent, unattended, and it presents as the morning briefing simply not arriving.

**Approach:** Add `pm_ai/platform/doctor.py` with one probe per condition, each returning a result object rather than raising, plus a `python -m` entry point so the probes can be run before anything else is trusted.

**Depends on:** story 1d for the keychain port, story 1f for the encryption toggle whose state is reported.

## Boundaries & Constraints

**Always:**
- Every probe **reports** rather than raises, so one failure does not prevent the others from running. A caller sees the full picture in one pass.
- Each failure result names its remediation. "Missing `enable_load_extension`" is useless without "install a uv-managed interpreter".
- The extension probe tests the attribute's presence on a real connection object, not the Python version string. Version is a proxy; the attribute is the thing that matters.
- Probes are read-only. They never create, migrate, or repair anything.

**Ask First:** Adding a probe that requires network access, or one that writes to the keychain rather than reading from it.

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
| All probes | run together while one fails | every probe still produces a result, and the summary reports the overall state as failing |

</frozen-after-approval>

## Code Map

- `pm_ai/platform/__init__.py` — currently a docstring; OS-touching code belongs here, which is why the probes live in `platform` rather than `storage`.
- `pm_ai/platform/keychain.py` — added by story 1d; the keychain probe calls it and translates its typed errors into results.
- `pyproject.toml:33-38` — `[tool.uv] python-preference = "only-managed"`, the setting that is supposed to prevent the interpreter failure. The probe verifies the outcome rather than trusting the setting.
- `pyproject.toml` — no `[project.scripts]` entry exists, which is why this story ships a `python -m` runner instead of a command.

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/platform/doctor.py` — new. A result type, three probes (extension support, keychain reachability, encryption-toggle state), a summary that runs all of them, and a `python -m` entry point.
- [ ] `tests/architecture/test_doctor.py` — new. One test per matrix row, faking an absent attribute and an unreachable keychain.

**Acceptance Criteria:**
- Given an interpreter without `enable_load_extension`, when the probe runs, then it returns a failure result and raises nothing.
- Given a keychain that is unreachable, when the summary runs, then the other two probes still produce results.
- Given a keychain holding a key, when the probe runs, then the key's value appears nowhere in the result.
- Given `uv run python -m pm_ai.platform.doctor` on this machine, then it exits reporting extension support present.
- Given `uv run pytest`, then all previously passing tests still pass and the skip count is unchanged.

## Design Notes

The extension probe checks `hasattr(connection, "enable_load_extension")` rather than comparing version numbers, because the failure is a property of how the interpreter was built, not of which version it is. A correct version compiled without the feature passes a version check and fails on first use — which is exactly the sequence this probe exists to break.

## Verification

- `uv run python -m pm_ai.platform.doctor` — expected: a readable report; extension support present on this machine.
- `uv run pytest -q -rs` — expected: previously passing tests still pass, skip count unchanged.
- `uv run lint-imports` — expected: `Contracts: 12 kept, 0 broken.`
