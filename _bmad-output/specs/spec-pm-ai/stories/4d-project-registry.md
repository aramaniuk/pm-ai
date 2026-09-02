---
title: 'Project registry and pm-ai project add'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `projects.toml` is declared as an application-scope Tier-1 file (`scope_model.py:440`) and is read and written by nothing. `ScopePaths.real()` takes a `projects` mapping documented as coming "from the registry the CLI writes (`projects.toml`)" (`paths.py:467`), `projects_registry()` resolves its path (`paths.py:640`), and `repository()` refuses an unknown project with a message naming `pm-ai project add` (`paths.py:553`) — a command that does not exist. `build()` resolves the project scope eagerly (`wiring.py:104`), so on a real machine every subcommand fails before dispatch.

Added 2026-09-02 by the wave-1 spec review, which found no story owned this. AD-11 governs it and `paths.py` was written against it; only the command and the file were missing.

**Approach:** `pm-ai project add <id> <repository-path>` writes `projects.toml`; `build()` reads it and hands the mapping to `ScopePaths.real()`.

**Follows `4c`**, which creates the dispatch table and the exit-code table this command uses. Before this slice lands, `4c` leaves only `doctor` usable on a clean machine; after it, every subcommand is.

## Boundaries & Constraints

**Always:**
- **A project is registered, never discovered** (AD-11). No scanning for `.project-ai/` directories, no inference from the working directory. `paths.py:553`'s message is explicit that a repository path is supplied, and this slice keeps that true.
- **The registry is additive and its existing entries survive.** `write_artifact` replaces a file whole, so registering a second project is a read-modify-write or it deletes the first.
- **A registered path must be an existing directory containing a git repository**, checked at registration while a human is present, not at first harvest.
- **The id must be usable as a directory name.** `_directory_name` already refuses the rest (`paths.py:547`); registration refuses at the same standard so the refusal arrives at the command rather than three slices later.
- **`doctor` reports the registry.** An empty or absent registry is an ordinary first-run state, reported as `ABSENT` and not as a broken machine — the distinction `doctor.py:64-72` already draws for the keychain.

**Ask First:** Whether registering a project should also create its `.project-ai/` tree and `.gitignore`. `paths.py:540-543` knows where that `.gitignore` belongs and story 1c's capture guard depends on it existing; whether that is this command's job or the first write's is a real choice.

**Never:** No project *removal* — deregistering raises the question of what happens to the artifacts under that tree, and nothing in wave 1 needs it. No daemon changes beyond reading the registry. No multi-project harvesting: `build()` still takes one project.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First registration | no `projects.toml` | file created with one entry | N/A |
| Second registration | file holds one entry | both entries present afterwards | N/A |
| Duplicate id | id already registered | refused; the existing path is not replaced | `DuplicateProject` |
| Re-registration to a new path | same id, different path | refused — it is a move, and artifacts already resolved under the old path | `DuplicateProject` |
| Path does not exist | a typo'd directory | refused, naming the path | `ProjectPathUnusable` |
| Path is a file | a file, not a directory | refused | `ProjectPathUnusable` |
| Path is not a git repository | a plain directory | refused — story 1c's capture guard asks git whether a directory is tracked | `ProjectPathUnusable` |
| Id unusable as a directory name | `../alpha`, an empty id | refused at the same standard `_directory_name` applies | propagated |
| Registry hand-edited to malformed TOML | a human broke the file | refused, naming the line; the registry is never silently reset | `RegistryMalformed` |
| Registry names a path since deleted | repository moved away | reported by `doctor`; `build()` refuses for that project alone | `UnknownProject` |
| Relative path supplied | `./alpha` | stored absolute — `_absolute_map` already expects absolute (`paths.py:479`) | N/A |
| Registry absent at build | first run, nothing registered | `doctor` still runs; every other subcommand refuses, naming this command | `UnknownProject` |

</frozen-after-approval>

## Code Map

- `pm_ai/core/project_registry.py` -- new; parse and render `projects.toml`, and the refusals
- `pm_ai/domain/scope_model.py:440` -- the declaration that finally gets a reader and a writer
- `pm_ai/platform/paths.py:463-479` -- `real()` and its `projects` mapping; `_absolute_map` expects absolute paths
- `pm_ai/platform/paths.py:545-558` -- `repository()` and the `UnknownProject` message naming this command
- `pm_ai/platform/paths.py:640` -- `projects_registry()`, already resolving the path
- `pm_ai/app/wiring.py:99-104` -- the eager `scope_root` call that makes this a wave-1 blocker
- `pm_ai/platform/doctor.py:64-72` -- the four-state `Health` shape a registry probe follows

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/project_registry.py` -- add `parse_registry(raw: bytes | None)`, `render_registry(...)`, `DuplicateProject`, `ProjectPathUnusable`, `RegistryMalformed`
- [ ] `pm_ai/app/wiring.py` -- read the registry and pass the mapping to `ScopePaths.real()`
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- add `project add`, using `4c`'s exit-code table
- [ ] `pm_ai/platform/doctor.py` -- add a registry probe, `ABSENT` when empty
- [ ] `tests/core/test_project_registry.py`, `tests/slice/test_project_registration.py` -- the matrix, the slice case against a real temporary root

**Acceptance Criteria:**
- Given a clean root, when `project add` runs twice with different ids, then `projects.toml` holds both entries — asserted on the file, because `write_artifact` replaces whole and the obvious implementation loses the first.
- Given a registered project, when the daemon is built for it, then `scope_root` resolves without raising — the condition every other wave-1 slice depends on and none currently establishes.
- Given a path that is a directory but not a git repository, then registration refuses; story 1c's guard asks git whether a capture directory is tracked, and it cannot answer for a non-repository.
- Given a hand-edited malformed `projects.toml`, then it is refused by name and not overwritten — a registry that resets itself loses every project silently.

## Design Notes

The read-modify-write requirement is the same defect the review found in `8b` for `private/config.json`, and it has the same cause: `write_artifact` is a whole-file replace, so any artifact accumulating entries over time needs the read step stated or the second write destroys the first. Worth noting as a pattern — every declared artifact that grows by entries rather than by rewrite has this shape.

Refusing re-registration to a new path rather than allowing it keeps this slice small and honest. Artifacts have already been resolved and written under the old path; moving the registration without moving them produces a project whose event log and meetings are invisible. That is a migration, and it needs its own thinking.

## Verification

**Commands:**
- `uv run pytest tests/core/test_project_registry.py -q` -- expected: all matrix rows pass
- `uv run pytest tests/slice/test_project_registration.py -q` -- expected: registration then build succeeds
- `uv run pm-ai project add alpha .` then `uv run pm-ai doctor` -- expected: registry probe healthy
- `uv run pytest -q` -- expected: no new failures
