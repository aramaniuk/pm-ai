---
title: 'Refuse unprotected captures'
type: 'feature'
created: '2026-08-21'
status: 'done'
review_loop_iteration: 1
baseline_commit: '3a0ddf5'
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
- **Git is the authority on what git tracks.** The write path asks git through a `VcsPort` — `git check-ignore` for the exclusion rules and `git ls-files` for whether the directory is already tracked. Text matching cannot answer either question: a negation line re-includes an excluded directory, a parent-directory exclude protects without naming the child, and a `.gitignore` rule does not untrack a path already in the index.
- If git cannot be consulted — not a repository, binary missing, command fails — the write is **refused**. Unknown is not permission.
- `assert_capture_dir_ignored` keeps its pure signature `(artifact, gitignore_text)` and stays in `domain`; `tests/slice/test_r4_gate_fixes.py:370` calls it directly. It is no longer the write path's authority.
- `subprocess` is permitted only in `pm_ai/platform/` and `pm_ai/models/local/` by `.importlinter`, so the git adapter lives in `platform` behind the port. `storage` calls the port, never git.

**Ask First:** Extending `GITIGNORE_REQUIRED` (`storage_tiers.py:88`) to cover a capture directory in another scope. Today it covers the project scope only, because that is the one committed scope.

**Never:** No auto-repair. pm-ai does not add the missing rule to the user's `.gitignore` on their behalf — it refuses and reports. No encryption of the capture; that is story 1g.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| A project repository whose `.gitignore` contains `/.project-ai/transcripts/` | a raw capture is written | the write succeeds |
| A project repository whose `.gitignore` exists but omits that rule | a raw capture is written | raises `UnprotectedCaptureDir`; the target directory contains no new file |
| A project repository with no `.gitignore` at all | a raw capture is written | raises `UnprotectedCaptureDir`; the target directory contains no new file |
| A `.gitignore` containing the rule without its leading slash | a raw capture is written | the write succeeds — git excludes it either way |
| The rule present, followed by `!/.project-ai/transcripts/` | a raw capture is written | raises `UnprotectedCaptureDir`; git tracks the directory despite the earlier rule |
| `.gitignore` excluding the whole enclave (`.project-ai/`) | a raw capture is written | the write succeeds — the parent exclude protects the child |
| The rule present, but the capture directory already tracked in the index | a raw capture is written | raises `UnprotectedCaptureDir`; a rule does not untrack what is already tracked |
| The project root is not a git repository at all | a raw capture is written | raises `UnprotectedCaptureDir`; git could not be consulted, so the answer is refusal |
| A capture in the people scope, which is never git-committed | a raw capture is written | the write succeeds; the guard does not apply and git is not consulted |
| A non-capture artifact, such as an event-log segment | it is written to a project scope | the check does not apply and the write proceeds |
| A capture in a scope that is not git-committed | it is written | the check does not apply and the write proceeds |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/storage_tiers.py:79-81` — `GITIGNORE_REQUIRED`, mapping a capture directory to the rule that must exclude it.
- `pm_ai/domain/storage_tiers.py:84-104` — `UnprotectedCaptureDir` and `assert_capture_dir_ignored`. `:99-100` already accepts the rule with or without a leading slash.
- `pm_ai/domain/scope_model.py:768` — `RETENTION_MANAGED`, now derived from the scope trees, naming the capture directories this check guards. `Collection("transcripts", RETAINED)` appears in the personal, people and project trees.
- `pm_ai/domain/identity.py:80-86` — `DataScope.is_git_committed`, true for the project scope only, which decides whether the check applies.
- `pm_ai/platform/paths.py` — `ScopePaths.repository(project_id)` gives the repository root whose `.gitignore` must be read; `resolve(scope, "transcripts/")` gives the capture directory.

Note: the anchors cited in this spec's frozen Intent and Ask First sections predate commit `3a0ddf5`, which moved this code. `assert_capture_dir_ignored` is at `:88`, not `:97`; `GITIGNORE_REQUIRED` is at `:79`, not `:88`. The anchors in this Code Map are current — prefer them.
- `pm_ai/storage/service.py` — the class that gains the capture-write path and the call to the guard.

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/domain/vcs.py` — new. `TrackingVerdict` (git's two independent facts: excluded by the rules, present in the index) and `VcsUnavailable`. In `domain` because `storage` catches the second and may not import `pm_ai.platform`.
- [x] `pm_ai/ports/__init__.py` — `VcsPort.tracking(path, *, repository)`, plus `gitignore` on `ScopePathPort`. Answers or raises; there is no third state, because a default would be the leak the port exists to prevent arriving as a fallback.
- [x] `pm_ai/platform/vcs.py` — new. `GitVcs`, using `git check-ignore` for the rules and `git ls-files` for the index. Deliberately *without* `--no-index`: the default consults the index, so an already-tracked directory is correctly reported as not ignored. Bounded timeout, `shutil.which` for the binary, argv list with `shell=False`. Every failure raises `VcsUnavailable` with its cause.
- [x] `pm_ai/domain/storage_tiers.py` — `assert_capture_dir_untracked(artifact, verdict, *, gitignore)`, whose two branches are two different repairs: add a rule, or `git rm -r --cached` what a rule cannot untrack. `assert_capture_dir_ignored` keeps its exact signature and its test, documented as the pure form of the question and no longer the authority.
- [x] `pm_ai/storage/service.py` — `write_capture`, routed with `_segment` through one `_writable_dir` that guards *before* `create=True`. `vcs` is a required constructor keyword for the same reason `now` is. Refusals named rather than raw: `EmptyCapture`, `CaptureAlreadyExists` (a `FileExistsError` subclass, so the builtin stays catchable), `MalformedCaptureName`. A partial write is unlinked so a failure cannot claim the name for good.
- [x] `pm_ai/platform/paths.py` — `ScopePaths.gitignore(project_id)` and `GITIGNORE_FILENAME`. The write path no longer reads the file; it names it, so the refusal says where the rule belongs.
- [x] `pm_ai/app/wiring.py` — `build()` constructs `GitVcs()` and accepts a `vcs` override, being the one module that may import both `storage` and `platform`.
- [x] `.importlinter` — `ignore_imports` for `pm_ai.app.wiring -> pm_ai.platform.vcs`. The contract counts indirect imports, so without this the composition root could not construct the one adapter AD-1 permits to shell out. `pm_ai.app` calling `subprocess` itself is still caught by `test_ad1_no_shell_execution_outside_platform`, which scans `app` deliberately.
- [x] `tests/architecture/test_capture_guard.py` — rewritten against real `git init` repositories, one test per matrix row. A fake port appears only where the subject is storage's reaction to a verdict it was handed; the rows that assert git's own behaviour cannot use one, because a fake would re-encode the belief that was wrong.
- [x] `tests/architecture/test_paths.py` — `gitignore()` pinned to a literal path, the capture-directory-inside-the-repository relation, and the accessor's refusals added to `test_every_refusal_is_catchable_as_one_error`.
- [x] `tests/architecture/test_domain_invariants.py`, `tests/slice/test_storage_resolution.py` — the new constructor argument, and `VcsPort` added to the adapter/port conformance check. No assertion changed what it proves.
- [x] `tests/architecture/README.md` — AD-23 and AD-38 rows, naming test functions.

**Acceptance Criteria:**
- Given a project repository whose `.gitignore` lacks the rule, when a capture write is attempted, then it raises `UnprotectedCaptureDir` and the target directory contains no new file.
- Given the same repository with no `.gitignore` at all, then the outcome is identical.
- Given `uv run pytest`, then all previously passing tests still pass and the skip count stays at 30.
- Given a planted violation — a capture write into a scope with no rule — the new test fails before the guard is wired and passes after.


## Spec Change Log

- **2026-08-28 — iteration 2, recording an override story 1j made without one.**
  *Finding:* this story froze `assert_capture_dir_ignored` at the pure signature `(artifact, gitignore_text)` and named `tests/slice/test_r4_gate_fixes.py`'s direct call as the thing to preserve. Story 1j retired the global `GITIGNORE_REQUIRED` table in favour of per-node declarations and derived rules, which forced a required `rule` keyword onto the function — and shipped it without amending this story, leaving the two specs contradicting each other (surfaced by the story-1 code review).
  *Amended:* the frozen constraint now reads — `assert_capture_dir_ignored` keeps a pure signature `(artifact, gitignore_text, *, rule)`, no filesystem and no subprocess, with the rule supplied by the caller because it is derived from the working tree the capture sits in and a global table cannot hold one rule per scope. The r4 test was rewritten to pass `rule=` in the same change.
  *Why the code stands rather than the freeze:* the freeze predates 1j's finding that one basename needs different rules in different working trees; restoring the two-argument form would restore the single-rule assumption 1j exists to remove.

- **2026-08-22 — iteration 1, triggered by review of the first implementation.**
  *Finding:* the guard could not answer the question it exists to answer. Verified against real git: a `.gitignore` carrying the rule followed by `!/.project-ai/transcripts/` makes git **track** the directory while the guard reported it protected — publishing a verbatim transcript, the outcome this story calls unrecoverable. Two further disagreements: a parent-directory exclude (`.project-ai/`) protects the directory but was refused, and a directory already tracked before the rule was added stays tracked, which no text check can ever detect.
  *Root cause:* this spec's own frozen constraint required `assert_capture_dir_ignored` to remain the write path's authority with a pure `(artifact, gitignore_text)` signature. Text matching cannot express git's negation precedence, parent-directory semantics, or index state, so the constraint forced the defect.
  *Amended:* git becomes the authority, consulted through a `VcsPort` with a `platform` adapter; refusal on any inability to consult it. Six matrix rows added for the cases the old mechanism could not express, including the first people-scope row.
  *Known-bad state avoided:* a guard that reports "protected" for a directory git tracks, which is worse than no guard because it is trusted.
  *KEEP — correct in the first implementation, must survive re-derivation:* the capture-name validation (a name of `../memory/leak.md` passes any `transcripts/`-level check and then writes into a tracked directory; refused before the directory is resolved, so nothing is created — this hole was not in the original matrix); the guard running **before** `resolve(create=True)`; `_writable_dir` as the single resolve-for-write both `_segment` and `write_capture` pass through; exclusive-creation mode, since appending would splice two recordings and truncating would destroy the first; a missing `.gitignore` and one without the rule producing the identical refusal, with no auto-repair.

## Verification

- `uv run pytest -q -rs` — 227 passed, 30 skipped (was 188/30 before the story; skip count unchanged).
- `uv run lint-imports` — `Contracts: 12 kept, 0 broken.`
- `grep -rn subprocess pm_ai/storage/` — one docstring mention, no import and no call; the `subprocess-confined` contract is what enforces it.

**Mutations planted, each confirmed red, each restored:**

| Mutation | Caught by |
|---|---|
| Text matching restored as the authority | 9 tests, including all three rows text gets wrong |
| The guard call removed | 9 tests |
| The guard moved after `create=True` | 5 tests |
| The index half of the verdict dropped (`is_excluded` → `self.ignored`) | `test_a_tracked_directory_is_refused_even_when_the_rules_exclude_it` |
| `.gitignore` accessor pointing one directory up | 8 tests, including both new pinned-path tests |
| Capture-name validation removed | 15 tests |
| Trailing slash dropped when asking git | 4 tests — the first-write case, where git answers differently for a path that does not exist yet |

## Suggested Review Order

**Git is the authority**

- Start here: why no text check can answer this. Three ordinary repository states, two of which a matcher gets wrong in the direction that publishes a transcript.
  [`domain/vcs.py:1`](../../../../pm_ai/domain/vcs.py#L1)

- The verdict as two independent facts, and the one place their conjunction is computed.
  [`domain/vcs.py:48`](../../../../pm_ai/domain/vcs.py#L48)

- The port. Answers or raises — no default, because a default is the leak arriving as a fallback.
  [`ports/__init__.py:84`](../../../../pm_ai/ports/__init__.py#L84)

- The adapter, and the two flags that decide whether it is telling the truth: no `--no-index` (or a committed capture directory reads as protected), and a trailing slash (or git answers "not ignored" for the directory every first write is about).
  [`platform/vcs.py:60`](../../../../pm_ai/platform/vcs.py#L60)
  [`platform/vcs.py:96`](../../../../pm_ai/platform/vcs.py#L96)

**The refusal, and where it sits**

- The one resolve-for-write every Tier-1 write passes through. The guard runs before `create=True`, which is what makes "leaves nothing behind" structural.
  [`service.py:355`](../../../../pm_ai/storage/service.py#L355)

- The policy: two conditions from the scope model gate the question, and `VcsUnavailable` is a refusal rather than an exception to one.
  [`service.py:366`](../../../../pm_ai/storage/service.py#L366)

- Two branches, two repairs. Telling someone to add a rule when the directory is already tracked sends them to fix a file that is already correct.
  [`storage_tiers.py:104`](../../../../pm_ai/domain/storage_tiers.py#L104)

- The write path, with its refusals enumerated.
  [`service.py:432`](../../../../pm_ai/storage/service.py#L432)

**The path and the payload the verdict is about**

- A capture name is one reportable path component. `../memory/leak.md` *passes* the git check — the check was about `transcripts/` — and is written where git can see it.
  [`service.py:106`](../../../../pm_ai/storage/service.py#L106)

- The exclusion file named by the module that owns layout, so the refusal can say where the rule belongs.
  [`paths.py:472`](../../../../pm_ai/platform/paths.py#L472)

**Tests proven to fail on a planted defect**

- The negation line: the rule is present and git tracks the directory anyway. The row the previous implementation allowed.
  [`test_capture_guard.py:276`](../../../../tests/architecture/test_capture_guard.py#L276)

- The parent-directory exclude: protected, and previously refused.
  [`test_capture_guard.py:230`](../../../../tests/architecture/test_capture_guard.py#L230)

- Already tracked before the rule existed — the state no text can see.
  [`test_capture_guard.py:294`](../../../../tests/architecture/test_capture_guard.py#L294)

- Not a repository at all, with its premise asserted first so it cannot go vacuous.
  [`test_capture_guard.py:319`](../../../../tests/architecture/test_capture_guard.py#L319)

- Unknown is not permission, whatever the cause.
  [`test_capture_guard.py:407`](../../../../tests/architecture/test_capture_guard.py#L407)

- The refusal leaves nothing behind — snapshot before, compared after, which is what the previous `directory.exists()` branch could not do.
  [`test_capture_guard.py:186`](../../../../tests/architecture/test_capture_guard.py#L186)

- The exclusion file pinned to a literal path, because the fixture used to write the rule through the accessor it later read.
  [`test_paths.py:530`](../../../../tests/architecture/test_paths.py#L530)
