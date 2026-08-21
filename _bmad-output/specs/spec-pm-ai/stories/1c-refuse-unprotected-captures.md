---
title: 'Refuse unprotected captures'
type: 'feature'
created: '2026-08-21'
status: 'ready-for-dev'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/scope-model.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A project's raw meeting captures live in `<repo>/.project-ai/transcripts/`, inside a directory that *is* committed to the team's repository. The only thing keeping verbatim minutes out of that repository is a `.gitignore` rule — and a rule can be missing. A function to verify it exists, `assert_capture_dir_ignored` (`pm_ai/domain/storage_tiers.py:97`), and **nothing in `pm_ai/` calls it.**

**Approach:** Call it from `StorageService` before writing any raw capture, refusing the write when the rule is absent. Losing a transient capture is recoverable; publishing verbatim meeting minutes to the employer's repository is not.

**Depends on:** stories 1a and 1b, which give storage a resolver so it can locate a project's repository root and read its `.gitignore`.

## Boundaries & Constraints

**Always:**
- Fail closed. No rule means no write. A missing `.gitignore` file is treated the same as a present file with the rule absent.
- The check runs before the write, not after, and leaves nothing behind when it refuses.
- `assert_capture_dir_ignored` keeps its current pure signature `(artifact, gitignore_text)`. `tests/slice/test_r4_gate_fixes.py:370` calls it directly, and the storage caller reads the file and passes its text.
- Reading `.gitignore` is a read, not a write, so it is permitted anywhere; only the refusal logic is new.

**Ask First:** Extending `GITIGNORE_REQUIRED` (`storage_tiers.py:88`) to cover a capture directory in another scope. Today it covers the project scope only, because that is the one committed scope.

**Never:** No auto-repair. pm-ai does not add the missing rule to the user's `.gitignore` on their behalf — it refuses and reports. No encryption of the capture; that is story 1g.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| A project repository whose `.gitignore` contains `/.project-ai/transcripts/` | a raw capture is written | the write succeeds |
| A project repository whose `.gitignore` exists but omits that rule | a raw capture is written | raises `UnprotectedCaptureDir`; the target directory contains no new file |
| A project repository with no `.gitignore` at all | a raw capture is written | raises `UnprotectedCaptureDir`; the target directory contains no new file |
| A `.gitignore` containing the rule without its leading slash | a raw capture is written | the write succeeds — the existing matcher already accepts both forms |
| A non-capture artifact, such as an event-log segment | it is written to a project scope | the check does not apply and the write proceeds |
| A capture in a scope that is not git-committed | it is written | the check does not apply and the write proceeds |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/storage_tiers.py:88-96` — `GITIGNORE_REQUIRED`, mapping a capture directory to the rule that must exclude it.
- `pm_ai/domain/storage_tiers.py:97-113` — `assert_capture_dir_ignored` and `UnprotectedCaptureDir`. `:107-108` already accepts the rule with or without a leading slash.
- `pm_ai/domain/storage_tiers.py:80` — `RETENTION_MANAGED`, which names the capture directories this check guards.
- `pm_ai/domain/identity.py:80-86` — `DataScope.is_git_committed`, true for the project scope only, which decides whether the check applies.
- `pm_ai/storage/service.py` — the class that gains the capture-write path and the call to the guard.

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/storage/service.py` — add the raw-capture write path, reading the target scope's `.gitignore` through the resolver and calling `assert_capture_dir_ignored` before writing.
- [ ] `tests/architecture/test_capture_guard.py` — new. One test per matrix row.

**Acceptance Criteria:**
- Given a project repository whose `.gitignore` lacks the rule, when a capture write is attempted, then it raises `UnprotectedCaptureDir` and the target directory contains no new file.
- Given the same repository with no `.gitignore` at all, then the outcome is identical.
- Given `uv run pytest`, then all previously passing tests still pass and the skip count stays at 30.
- Given a planted violation — a capture write into a scope with no rule — the new test fails before the guard is wired and passes after.

## Verification

- `uv run pytest -q -rs` — expected: existing tests still pass, skips unchanged at 30.
- `uv run pytest tests/architecture/test_capture_guard.py -q` — expected: green.
- Temporarily remove the guard call and confirm the new tests go red, then restore it.
