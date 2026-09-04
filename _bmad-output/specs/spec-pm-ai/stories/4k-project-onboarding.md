---
title: 'pm-ai project add'
type: 'feature'
created: '2026-09-03'
status: 'ready-for-dev'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `4d` gives `projects.toml` a parser, a renderer, a reader and a probe, and nothing writes it. `repository()` refuses an unregistered project with a message naming `pm-ai project add` (`paths.py:553`) — still a command that does not exist. A project's scope tree is declared and never created, so a project has no home until something makes one.

**Approach:** `pm-ai project add <path> [alias]`. It takes a filesystem path, creates the directory if absent, generates the scope structure and `.gitignore` inside it, adopts whatever pm-ai structure is already there, and records the entry through `4d`'s renderer.

## Boundaries & Constraints

**Always:**
- **The argument is a path; the id is derived from it.** Absolute or relative — relative resolves against the working directory, and only absolute is stored. The id is the final path component, or the alias when supplied, so a directory name `_directory_name` refuses (`paths.py:264`: uppercase, a leading dot, whitespace, a separator) does not block onboarding — it requires an alias.
- **The directory need not exist, and need not be a git repository.** A missing directory is created. Git is optional: `service.py:714-717` already treats `working_tree` returning `None` as an *answer* — no repository, so nothing can carry a write into a commit — and only an unanswerable question refuses.
- **Structure generation includes `.gitignore`, and is not optional.** The rules are rendered from `GITIGNORED` (`scope_model.py:994`), never from a second list. Q6 moved four project artifacts into that set, and it is exactly what `_assert_git_excludes` guards, so inside a repository every write to them refuses until the rule exists.
- **An existing pm-ai structure is adopted, never replaced.** Its Tier-1 artefacts are left byte-identical; a project tree declares Tier 1 only, so there is nothing else to reconcile. An already-onboarded project is **reported as such** — an ordinary outcome, not a failure.
- **The read-modify-write is exclusive.** `write_artifact` publishes by `os.replace` (`service.py:1002`), so two concurrent runs both reading the old registry lose one entry unless the sequence holds an exclusive claim.
- **The sequence lives in `app`.** `core` may not touch the filesystem, `surfaces` may not reach storage, and `_directory_name` lives in `platform` which `core` may not import. `app` is the only layer that may do all three.

**Ask First:** Nothing.

**Never:** No project *removal*. No id rename — the id is the scope directory name, so changing it moves every artifact and stales every `SourceRef`; only the alias may change, and it is a display label. No registry parsing or rendering of its own — `4d` owns both. No git requirement of any kind, and no `git init`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First onboarding | directory exists, no registry | structure and `.gitignore` generated, one entry written, path absolute | exit `0` |
| Directory absent | a path that does not exist | created, then onboarded | exit `0` |
| Relative path | `./alpha` | resolved against the working directory, stored absolute | exit `0` |
| Second onboarding | registry holds one entry | both entries present afterwards | exit `0` |
| Unusable directory name, no alias | `~/dev/My Project` | refused, naming the alias as the remedy | `MalformedSubjectId`, exit `3` |
| Alias supplied | `~/dev/"My Project" payments` | id is `payments`; the path is unchanged | exit `0` |
| Alias collides with a registered id | two paths, one alias | refused; the existing entry is not replaced | `DuplicateProject`, exit `3` |
| Already onboarded | the same path and id again | reported as already onboarded; **no file's bytes change** | exit `0` |
| Same id, different path | a move | refused — artifacts are already resolved under the old path | `DuplicateProject`, exit `3` |
| Existing pm-ai structure | `.project-ai/` present with artefacts | adopted; every Tier-1 file byte-identical afterwards | exit `0` |
| Not a git repository | a plain directory | onboarded, `.gitignore` written anyway | exit `0` |
| Inside a git repository | a working tree | a write to a `GITIGNORED` artifact succeeds afterwards | exit `0` |
| Path is a file | a file, not a directory | refused before anything is created | `ProjectPathUnusable`, exit `3` |
| Path unwritable | EACCES on the parent | refused, naming the path; nothing partially created | `ProjectPathUnusable`, exit `3` |
| Two runs concurrently | both read the same registry | one wins, the other refuses or retries; **no entry is lost** | propagated, exit `3` |
| Registry malformed | a hand-edit broke it | refused by `4d`, left byte-identical; nothing onboarded | `RegistryMalformed`, exit `3` |
| Structure generated, registry write refused | root unwritable at the last step | the directory and `.gitignore` remain; re-running completes | propagated, exit `3` |

</frozen-after-approval>

## Code Map

- `pm_ai/core/project_registry.py` -- `4d`'s `parse_registry` and `render_registry`, called not reimplemented
- `pm_ai/platform/paths.py:264` -- `_directory_name`, the standard an id must meet; it lives in `platform`, so the check happens in `app`
- `pm_ai/platform/paths.py:540-543,553` -- `project_gitignore()`, which knows where the file belongs, and the message naming this command
- `pm_ai/domain/scope_model.py:994` -- `GITIGNORED` per scope kind, the source the `.gitignore` rules render from
- `pm_ai/domain/scope_model.py:PROJECT_TREE` -- the nine declarations the generated structure realises
- `pm_ai/storage/service.py:697-717` -- `_assert_git_excludes`, which the generated rules exist to satisfy
- `pm_ai/storage/service.py:1002,1022` -- the `os.replace` publish and `write_artifact`
- `pm_ai/app/entry.py` -- `4c`'s `main`, where the sequence lives
- `pm_ai/surfaces/cli/dispatch.py` -- `4c`'s table and exit codes, reused not extended

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/app/entry.py` -- add the onboarding sequence: resolve the path, derive or take the id, create the directory, generate the structure and `.gitignore` from `GITIGNORED`, then the exclusive read-modify-write through `4d`'s renderer
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- add `project add` as a leaf on `4c`'s table -- one mapping, no new exit code
- [ ] `tests/slice/test_project_onboarding.py` -- the matrix against a real temporary root, including the adoption case hashed before and after

**Acceptance Criteria:**
- Given an onboarded project inside a git repository, when a `GITIGNORED` artifact is written, then the write succeeds — which is what proves the generated `.gitignore` is what `_assert_git_excludes` wanted, rather than a file that merely exists.
- Given a directory holding an existing `.project-ai/` tree, when it is onboarded, then every Tier-1 file under it is byte-identical afterwards — asserted by hashing before and after, because "adopted" and "regenerated" are indistinguishable from a success message.
- Given the same path onboarded twice, then no file's bytes differ after the second run and it exits `0`.
- Given two onboardings of different paths, then `projects.toml` holds both — asserted on the file, because `os.replace` keeps the last writer and the obvious implementation loses the first.
- Given a path that is a file, then it is refused and no directory, `.gitignore` or registry entry is created — asserted on the filesystem, because a refusal after a partial create leaves a project half-onboarded.
- Given `uv run pm-ai project add <tmp>` then `uv run pm-ai doctor`, then the registry probe reports healthy.

## Spec Change Log

- **2026-09-03, split from `4d` at the sizing gate.** `4d`'s rewrite against the human's onboarding decisions reached 2136 body tokens against wave 1's 1600. `4d` keeps the registry, its reader and its probe — a pure parser testable without a machine; this slice takes the command and everything filesystem-shaped. The same reader-first split `4a`/`4g` used for `config.toml`.
  Carried from the human's decisions of the same day: the argument is a path with an optional alias rather than an id and a repository path (D-1); relative paths resolve and only absolute is stored (Q4); the directory may be absent and is created, an existing structure is adopted with its artefacts intact, and an already-onboarded project is reported rather than refused (Q5); git is optional, so there is no repository check and the review's A3 — the missing `VcsPort` route on `Daemon` — dissolves rather than being implemented (Q1); and structure generation including `.gitignore` is mandatory rather than an `Ask First`, because Q6 put four artifacts into `GITIGNORED` and that set is what `_assert_git_excludes` guards.
  From the review: the concurrency finding (`os.replace` keeps the last writer), the unwritable-path and partial-create rows, and the layering of the id check — `_directory_name` is in `platform`, which `core` may not import, so the sequence lives in `app`.

## Design Notes

Refusing a moved path rather than allowing it keeps this slice honest: artifacts have already been resolved and written under the old path, and moving the registration without moving them produces a project whose event log and meetings are invisible. That is a migration and needs its own thinking — the same reason the id may not be renamed and only the alias may.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_project_onboarding.py -q` -- expected: all matrix rows pass
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
- `uv run mypy` -- expected: clean
