---
title: 'Capture guard covers every scope'
type: 'feature'
created: '2026-08-22'
updated: '2026-08-22'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'c90f1f2'
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/storage-contract.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/scope-model.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 1c built the guard that asks git whether a capture directory would be carried into a commit, and refuses the write when it would or when git cannot answer. It runs on one scope out of three.

Captures live in three places — `<repo>/.project-ai/transcripts/`, `~/.pm-ai/private/people/<person_id>/transcripts/`, and `~/.manager-ai/transcripts/` — and the guard is gated on `scope.is_git_committed`, which is true for PROJECT alone. The two others are never asked about. The reasoning is written into `_assert_git_excludes`: *"A capture in the personal or team-member scope is excluded by where it is; there is no repository, and git is not consulted at all."*

That premise does not hold. Deployment instructs keeping the sovereign personal scope as a **private git repository**, gitignoring `private/` — and `transcripts/` sits at that scope's root, outside `private/`, so the one stated rule does not cover it. A verbatim transcript of a personal coaching session is therefore written into a git working tree, permanently, and git is never asked. The gate is a data-classification predicate (`is_git_committed` answers *is this scope pushed to my employer*) standing in for a filesystem question (*is this directory inside a working tree*). Those agree everywhere except the case that leaks.

There is a second, structural half. `GITIGNORE_REQUIRED` maps the artifact **basename** `transcripts/` to the single hardcoded string `/.project-ai/transcripts/`. A durability keyed by basename is global by design, so the table cannot hold a second rule for the same name — extending it per scope means re-keying on `(scope, path)`, which the architecture explicitly defers. So the fix cannot be "add the other scopes to the table."

**Approach:** Replace the scope gate with a working-tree question, and derive the remedy rather than storing it.

1. `VcsPort` gains a way to ask which working tree a path belongs to, answered by `git rev-parse --show-toplevel` in `GitVcs`. Not inside one is an ordinary answer, not a failure.
2. `_assert_git_excludes` runs for **every** capture write, whatever the scope. If the path is inside a working tree, git is asked and the existing two-fact verdict decides. If it is not, there is nothing to be excluded from and the write proceeds.
3. The `.gitignore` path and the rule text named in a refusal are derived from the working-tree root git just reported, so the message is correct in a scope the table never knew about.

**Depends on:** story 1c for `VcsPort`, `GitVcs`, `TrackingVerdict` and the refusal messages.

## Boundaries & Constraints

**Always:**
- **git is optional, and its absence never blocks recording a meeting.** *(Renegotiated during implementation, 2026-08-22. The rule as approved said any unanswered question refuses in every scope; the user's direction was that a machine without git, and a project that is no checkout, are both legitimate and must not stop pm-ai working.)* No git and no repository writes; a path in no working tree writes.
- **The refusal narrows to a repository present and unaskable.** "pm-ai cannot find git" is not the fact "no repository exists": the daemon runs under `launchd` with a minimal PATH, so it can miss a `git` the developer's shell uses daily, and the capture would land in a genuinely tracked directory. Whether a repository exists is answerable with no binary at all — walk up looking for `.git` — and only whether git would *ignore* a path needs git. So that one combination refuses, and it names the `.git` it found so the operator can tell it from a missing rule. A timeout or an undocumented exit code, having got past the working-tree question, still refuses: at that point a repository is known to exist.
- The verdict keeps both facts, `ignored` and `tracked`, and both repairs. A rule does not untrack what is already committed, in a private repository any more than in the employer's.
- The remedy path is derived from the reported working-tree root. Nothing may reintroduce a per-scope table of rule strings keyed on the `transcripts/` basename.
- One code path serves all three scopes. A per-scope branch is the shape this story removes; adding a fourth capture location must require no change here.
- The guard resolves its target **without** `create`. Asking git about a directory is not a reason to bring it into existence, and git answers the same either way.
- The trailing slash stays load-bearing. For a path that does not yet exist git answers *not ignored* for `…/transcripts` and *ignored* for `…/transcripts/`, and every first capture write concerns a directory that does not exist yet.

**Ask First:** Making "not inside a working tree" anything other than permission to write. Adding a configuration switch that disables the guard for a scope.

**Never:** No `--no-index`, in any query, for the reason story 1c recorded: it answers about the rules alone and reports an already-committed capture directory as protected. No `pygit2`/libgit2, which carries those semantics permanently. No writing a capture to learn whether it was allowed.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| PROJECT scope, repository excludes `transcripts/` | a capture is written | git is asked, the verdict is excluded, the write proceeds |
| PROJECT scope, its registered root is **not** a repository | a capture is written | the write proceeds — nothing can commit it. *Re-derived: this refused before the guard stopped keying on scope* |
| PROJECT scope, registered root deleted from disk | a capture is written | the write proceeds. A stale registry is a configuration fault for `pm-ai doctor`, not a leak |
| PERSONAL scope, `~/.manager-ai` is **not** a git repository | a capture is written | git reports no working tree, the write proceeds, and no refusal is raised |
| PERSONAL scope, `~/.manager-ai` **is** a git repository with no rule for `transcripts/` | a capture is written | refused, naming the `.gitignore` at that repository's root — not `/.project-ai/transcripts/` |
| PERSONAL scope, a repository whose `.gitignore` excludes `transcripts/` | a capture is written | the write proceeds |
| PERSONAL scope, a repository where `transcripts/` was committed before the rule was added | a capture is written | refused with the untrack instruction, not the add-a-rule instruction |
| PEOPLE scope, `~/.pm-ai` is a git repository not excluding the capture directory | a capture is written | refused — a direct report's 1:1 recording is covered on the same terms |
| PEOPLE scope, `~/.pm-ai` is not a repository | a capture is written | the write proceeds |
| Any scope, no `git` on `PATH`, no `.git` above the target | a capture is written | the write proceeds — git is optional |
| Any scope, no `git` on `PATH`, a `.git` **present** above the target | a capture is written | refused, naming both the missing git and the `.git` it found |
| Any scope, git present but the query times out | a capture is written | refused, and the cause survives into the message |
| A non-capture artifact in a committed scope | it is written | git is not consulted at all |
| A capture directory inside a working tree | the guard runs | the directory is not created as a side effect of asking |

</frozen-after-approval>

## Code Map

- `pm_ai/storage/service.py:369-413` — `_assert_git_excludes`, the guard. Its first line (`if not scope.is_git_committed or artifact not in GITIGNORE_REQUIRED`) is the gate being replaced, and its docstring states the premise this story falsifies.
- `pm_ai/storage/service.py:398-399` — `self._paths.repository(scope.project_id)`, project-keyed. The working-tree root replaces it as the thing git is asked from.
- `pm_ai/storage/service.py:412-413` — `self._paths.gitignore(scope.project_id)`, likewise project-keyed; the derived root replaces it.
- `pm_ai/ports/__init__.py:84-115` — `VcsPort.tracking`, which already takes `repository` as a keyword. `working_tree` and `repository_marker_above` were added beside it.
- `pm_ai/platform/vcs.py:52-120` — `GitVcs`, including `_git` with its allowlisted argv and bounded timeout, which the new query reuses.
- `pm_ai/domain/vcs.py` — `TrackingVerdict` and `VcsUnavailable`, unchanged. Only who gets asked changes, not what the answer means.
- `pm_ai/domain/storage_tiers.py:95-97` — `GITIGNORE_REQUIRED`, and the basename keying that makes a per-scope table impossible.
- `pm_ai/domain/scope_model.py:534`, `:557`, `:599` — the three `Collection("transcripts", RETAINED)` declarations this guard must cover.
- `tests/architecture/test_capture_guard.py:385-402` — **`test_a_capture_in_an_uncommitted_scope_is_unaffected`**, parametrized over PERSONAL and PEOPLE, which currently asserts git is *not* consulted and the capture is written. Its scenario survives (it builds with `init=False`, so there is genuinely no working tree) but its stated reason does not, and its docstring argues for the behaviour being replaced. Re-derive it, do not delete it.
- `tests/architecture/test_capture_guard.py:64` — the shared `RULE` constant, and the note that a test carrying its own copy of the rule string is the thing to avoid.

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/ports/__init__.py` — `VcsPort.working_tree` (`None` is an answer) and `VcsPort.repository_marker_above` (the no-binary fallback).
- [x] `pm_ai/platform/vcs.py` — `working_tree` over `git rev-parse --show-toplevel` through the existing `_git` helper, asked from the nearest existing ancestor since the capture directory does not exist yet; `repository_marker_above` by pathlib alone.
- [x] `pm_ai/domain/storage_tiers.py` — `GITIGNORE_REQUIRED` becomes a frozenset of artifacts needing the guard, and `gitignore_rule_for` derives the rule text from the working-tree root. `GITIGNORE_FILENAME` moved here so `storage` can name it without importing `platform`.
- [x] `pm_ai/storage/service.py` — the `is_git_committed` gate dropped, guard runs for every capture artifact in every scope, repository and `.gitignore` path both derived from git's reported root, docstring rewritten around the real premise.
- [x] `tests/architecture/test_capture_guard.py` — five rows re-derived, four new rows against real `git init` for the personal and team-member repositories, one new row for the no-git-no-repository case; `FakeVcs` answers both new questions.

**Acceptance Criteria:**
- Given `~/.manager-ai` initialised as a git repository with no rule for `transcripts/`, when a personal capture is written, then it is refused and the message names that repository's own `.gitignore`.
- Given the same directory with the rule present, then the write proceeds.
- Given `~/.manager-ai` that is not a repository, then the write proceeds and git is asked only the working-tree question.
- Given no `git` on `PATH`, then a capture in each of the three scopes is refused.
- Given a non-capture artifact, then git is not consulted, in any scope.
- Given `uv run pytest`, then every previously passing test still passes, `test_a_capture_in_an_uncommitted_scope_is_unaffected` passes in its re-derived form, and no test carries its own copy of the rule string.
- Given `uv run lint-imports`, then all 12 contracts hold and `subprocess` appears nowhere under `pm_ai/storage/`.

## Design Notes

**Why the working tree and not the scope.** `is_git_committed` has a job, and its docstring says what it is: the project scope is pushed to the employer, which is why disclosure records cannot live in a per-scope event log. That is a question about *who can read this*. The guard needs *can git reach this*. Reusing one for the other is only ever correct while every git-reachable directory happens to be an employer-visible one, and Deployment's own advice — keep the personal scope as a private repository — breaks that coincidence. Keying on the working tree makes the guard answer the question it actually has, and makes a fourth capture location free.

**Why deriving the remedy matters as much as widening the check.** A refusal that names `/.project-ai/transcripts/` while the PM is looking at `~/.manager-ai` sends them to edit a file that is already correct, in a repository that is not the one at fault. The guard would be right and useless. Deriving the path from the root git just reported is also what removes the pressure to re-key `GITIGNORE_REQUIRED` per scope, which the architecture defers for reasons unrelated to this story.

**Why the existing test is re-derived rather than deleted.** It documents a real requirement — a machine where the personal scope is not under version control must not have its captures refused — and its docstring correctly anticipates that keying on the artifact name alone would refuse every personal capture forever. That objection is answered by asking about the working tree rather than the name: the permissive case survives, and it survives for a reason git supplied.

## Verification

- `uv run pytest tests/architecture/test_capture_guard.py -q` — expected: green, including the new repository-backed personal and team-member rows.
- `uv run pytest -q -rs` — expected: no previously passing test regresses.
- `uv run lint-imports` — expected: `Contracts: 12 kept, 0 broken.`
- In a scratch directory: `git init`, write a personal capture, confirm the refusal names that directory's `.gitignore`; add the rule, confirm the write proceeds.
- Restore the `is_git_committed` gate and confirm the personal-repository rows go red, then remove it again.
