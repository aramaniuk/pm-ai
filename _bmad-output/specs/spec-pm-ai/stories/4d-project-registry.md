---
title: 'Project onboarding and the registry'
type: 'feature'
created: '2026-09-02'
status: 'done'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `projects.toml` is declared as an application-scope Tier-1 file (`scope_model.py:440`) and is read and written by nothing. `ScopePaths.production()` takes a `projects` mapping documented as coming "from the registry the CLI writes" (`paths.py:467`), the `project_registry` property resolves its path (`paths.py:638-640`), and `repository()` refuses an unregistered project with a message naming `pm-ai project add` (`paths.py:553`) — a command that does not exist. Nothing onboards a project, so no project's artifacts have a declared home.

Added 2026-09-02 by the wave-1 spec review, which found no story owned this. AD-11 governs it and `paths.py` was written against it; only the command and the file were missing.

**Approach:** `pm_ai/core/project_registry.py` parses and renders `projects.toml`, `build()` reads it, and `doctor` reports it. `4k` owns the `pm-ai project add` command that writes it — the same reader-first split `4a`/`4g` used for `config.toml`.

## Boundaries & Constraints

**Always:**
- **A project is registered, never discovered** (AD-11). No scanning for `.project-ai/` directories, no inference from the working directory.
- **Paths are stored absolute.** `_absolute_map` expects them (`paths.py:479`), and a relative path means nothing to a daemon started elsewhere. This module refuses a relative one rather than resolving it, because resolving needs a working directory and `core` has none.
- **`render_registry` is additive by signature.** It takes the whole mapping and returns the whole file, so a caller cannot append without having read — `write_artifact` replaces a file whole (`service.py:1002`), and an interface that accepts one entry invites the write that loses the rest.
- **`tomllib` gains a second importer, and `4a`'s sweep has to say so.** `projects.toml` is TOML. `4a`'s `Never` forbids `tomllib` outside `pm_ai/core/config.py` and `test_story_4a_tomllib_is_imported_by_exactly_one_module` enforces it, so this slice widens that test to a named allowlist of two modules. Widening it silently would retire a real guard; a named pair keeps the rule enforceable.
- **`doctor` reports the registry.** An empty or absent registry is an ordinary first-run state, reported as `ABSENT`, the distinction `doctor.py:64-72` already draws for the keychain.

**Ask First:** Nothing.

**Never:** No command and no filesystem — `4k` owns onboarding. No project *removal*. No id rename — the id is the scope directory name, so changing it moves every artifact and stales every `SourceRef`; only the alias may change, and that is a display label. No multi-project harvesting: `build()` still takes one project. No git requirement of any kind.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Alias collides | two paths, same alias | refused; the existing entry is not replaced | `DuplicateProject` |
| Empty registry | `projects.toml` present, no entries | parses to an empty mapping; `doctor` reports `ABSENT` | N/A |
| Absent registry | no file | parses to an empty mapping — a first run, not an error | N/A |
| One entry | a well-formed file | id, absolute path and alias round-trip through render and parse | N/A |
| Relative path in the file | a hand-edited `./alpha` | refused, naming the key — `_absolute_map` would take it as-is | `ProjectPathUnusable` |
| Duplicate id in the file | a hand-edit with two of one id | refused, not last-wins — `tomllib` already refuses both shapes, so this is pinned by test and has no branch | `RegistryMalformed` |
| Registry hand-edited to malformed TOML | a human broke the file | refused, naming the line; never silently reset | `RegistryMalformed` |
| Registry unreadable | a directory, or EACCES | reported, distinctly from absent — an unreadable registry must not be minted over | carried in `RegistryState`, never raised |
| Registry names a path since deleted | repository moved away | reported by `doctor`, naming the project | no refusal — see the 2026-09-15 amendment |

</frozen-after-approval>

## Code Map

- `pm_ai/core/project_registry.py` -- new; parse and render `projects.toml`, and the refusals
- `pm_ai/domain/scope_model.py:440` -- the declaration that finally gets a reader and a writer
- `pm_ai/platform/paths.py:459,467,479` -- `ScopePaths.production()` and its `projects` mapping; `_absolute_map` expects absolute paths
- `pm_ai/platform/paths.py:553,638-640` -- the `project_registry` property, and `repository()`'s `UnknownProject` message naming `pm-ai project add`
- `pm_ai/storage/service.py:1002` -- the `os.replace` publish, which is why `render_registry` takes the whole mapping
- `tests/architecture/test_static_rules.py:563` -- `4a`'s one-`tomllib`-importer sweep, widened here
- `pm_ai/platform/doctor.py:64-72` -- the four-state `Health` shape the registry probe follows

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/core/project_registry.py` -- add `parse_registry(raw: bytes | None)`, `render_registry(mapping)`, `DuplicateProject`, `ProjectPathUnusable`, `RegistryMalformed` -- pure over bytes, no filesystem
- [x] `pm_ai/app/wiring.py` -- `bootstrap(keychain)`: the bootstrap resolver, the single-reader read of **both** application artifacts, and the parse -- this is the module that may build a `StorageService`, and `4i`'s `ArtifactState` is what it returns alongside the mapping
- [x] `pm_ai/app/entry.py` -- `_bootstrap()` delegates to it and `_compose` passes the mapping to `ScopePaths.production()` -- **`entry.py:246` was the `production()` call site, not `wiring.py`**, which named it only in prose
- [x] `pm_ai/app/entry.py` -- give two-or-more registered projects its own probe and remedy -- `_select` raises `UnknownProject` into `_compose`'s `ScopeResolutionError` arm, which tells an operator whose projects both resolve to "Re-enrol the repository". Unreachable until this slice fills the registry; `deferred-work.md:420` assigns the choice policy here
- [x] `pm_ai/platform/doctor.py` -- add the registry probe, `ABSENT` when empty or missing, naming a project whose path is gone
- [x] `tests/architecture/test_static_rules.py` -- widen `test_story_4a_tomllib_is_imported_by_exactly_one_module` to a named allowlist of two modules -- `projects.toml` is TOML and `4a` scoped the rule to one importer
- [x] `tests/architecture/test_static_rules.py` -- extend `4a`'s no-filesystem sweep to cover `project_registry.py` -- the same "pure over bytes" guarantee, and `4g` proved a one-directional version of it goes stale
- [x] `tests/core/test_project_registry.py` -- one test per matrix row, plus the render/parse round trip

**Acceptance Criteria:**
- Given any mapping of ids to absolute paths and aliases, when rendered and parsed back, then the result equals the input — the same drift pair `4g` guards for `config.toml`.
- Given a registry holding two entries, when one is added through `render_registry`, then all three are present — asserted on the rendered bytes, because `write_artifact` replaces whole and an interface taking a single entry is what loses the other two.
- Given an onboarded project, when the daemon is built for it, then `scope_root` resolves without raising — the condition every other wave-1 slice depends on and none currently establishes.
- Given a hand-edited malformed `projects.toml`, then it is refused by name and this module returns no mapping — a registry that parses to empty is a registry that gets minted over.
- Given a `projects.toml` that is unreadable rather than absent, then `doctor` reports `FAILING` and nothing raises out of composition — the state `_compose`'s `OSError` arm currently misattributes to `~/.pm-ai` ownership.
- Given two registered projects, when any subcommand runs, then the refusal names the ambiguity and not a directory that resolves fine.
- Given `grep -rn "tomllib" pm_ai/`, then exactly two modules import it and the sweep names both.
- Given `grep -rn "ScopePaths.real\|projects_registry()" pm_ai/`, then there is no match — neither name exists, and both appeared in this spec until 2026-09-03.

## Spec Change Log

- **2026-09-15, built. Two things changed during execution and are recorded here rather than in a commit message.**
  **`registered_projects` became `bootstrap`, reading both artifacts instead of one.** The first shape read only `projects.toml` and left `config.toml` to be read after `build()` returned, as it always had been. Run against the real console script, that produced a falsehood on the most ordinary machine there is: a first run has no project, so composition stopped before the config was ever read, and `4i`'s probe reported `FAILING` — "could not reach the file to find out" — about a file that was simply not there yet. Both files are application-scope and the bootstrap `StorageService` can reach either, so it now reads both. A fresh machine reports `ABSENT` twice and names the two commands that fix it, and `UNOBTAINABLE` is left for what it actually describes.
  **`ArtifactState` and `Presence` live in `pm_ai.domain.health`, not in `doctor.py`.** Defined beside the probes, they broke `.importlinter`'s AD-1 contract: `pm_ai.app.wiring` produces the states, and importing `pm_ai.platform.doctor` reaches `subprocess` through `pm_ai.platform.vcs`. The choice was a third ignored import in that contract or a move, and the move is the same one `pm_ai/domain/health.py` already documents for `Health`, `Probe` and `Report`, for the same kind of reason. `doctor.py` re-exports them, so every existing import resolves to the one object in the process.
  Also found during execution: an unreadable `config.toml` would have booted the daemon on defaults, because `ArtifactState.unreadable` carries no bytes and `load_config(None)` means "first run". `_compose` now refuses it — an absent config defaults, an unreadable one does not, since a setting that reads as configured while having no effect is the failure `pm_ai.core.config` exists to prevent.

- **2026-09-15, built together with `4i`, and the error vocabulary closed.** The human combined the two slices at the readiness check, because they collide on one file and cannot be parallelised the way the path graph implies: both add a probe, both change `run_all`'s signature, and both must edit the same four assertions in `tests/architecture/test_doctor.py` (`:292-295`, `:313`, `:468`, `:634`, each asserting `len(report.probes) == 5`). `4i`'s spec calls itself "a sixth probe"; built together, the registry probe and the config probe are the sixth and seventh, and `4i` is amended to say so. Combined body exceeds wave 1's 1600-token gate — flagged and accepted rather than re-split, since splitting is what created the collision.

  Four matrix rows changed, each for a measured reason rather than a preference:

  **Row "same id, different path" is deleted — it was unbuildable here.** `render_registry` takes a mapping keyed by id, so two paths under one id cannot be expressed as its input. It is an *add*, and `4k` owns the read-modify-write that can actually encounter a move. "Alias collides" stays: that one *is* expressible across a whole mapping, and `render_registry` validates it.

  **Row "relative path" now raises `ProjectPathUnusable`.** The class was named in the Execution tasks and appeared in no row, while this row was the only one whose failure is about a path rather than about syntax. They were the same refusal under two names. `RegistryMalformed` now means exactly "this file did not parse", which is a line a caller can act on.

  **Row "duplicate id in the file" keeps its verdict and loses its branch.** Measured: `tomllib` already refuses both shapes — `Cannot declare ('projects', 'alpha') twice` for repeated tables, `Cannot overwrite a value` for repeated keys. The row survives as a test pinning "not last-wins" against a future parser swap; writing a duplicate check would have been dead code asserting something the parser guarantees.

  **Row "registry unreadable" is reported, not propagated.** `4i`'s frozen Always is the stricter and more recent rule — absent, unreadable and unobtainable are three answers with three remedies — and both probes need the same carrier. Propagating instead lands in `_compose`'s `OSError` arm, which blames `~/.pm-ai` ownership for any I/O failure anywhere in `build()`; `deferred-work.md` already records that as wrong.

  **Row "path since deleted" loses its refusal half.** Three things were true at once: `UnknownProject` is defined at `paths.py:231` in `platform` and `core` may not import it; `paths.py` performs no existence check anywhere, its only filesystem call being one `mkdir` at `:599`, so `repository()` returns a path for a deleted directory without complaint; and "refused for that project alone" therefore described behavior nothing had. The probe reports it, which is what the row's own first clause already said. Adding a `stat` to the resolver was considered and declined — it would put filesystem access in a module that has none, to serve a row that asked for reporting.

  One task was pointing at the wrong module. The Execution list said `wiring.py` reads the registry and passes the mapping to `ScopePaths.production()`; `wiring.py` never calls `production()` — `entry.py:246` does, and the reader seam is `_registered_projects()` at `entry.py:186`, whose docstring already names this slice. Underneath that sat an ordering problem no task mentioned: `projects.toml` must be read before the resolver exists, but `StorageService` is the single reader and is constructed *on* a resolver. `_config()` sidesteps this by reading after `build()` returns; the registry cannot. The read therefore needs a bootstrap `production()` over an empty mapping and a `StorageService` built on it, in `wiring.py`, which is the one module permitted to import both `storage` and `platform`.

  Last, this slice makes a known-wrong path reachable for the first time: with two projects registered, `_select` raises `UnknownProject` into `_compose`'s `ScopeResolutionError` arm and tells an operator whose projects both resolve to "Re-enrol the repository". `deferred-work.md:420` assigns the choice policy here, so the ambiguity gets its own probe rather than shipping a message that is wrong the moment the registry is useful.

- **2026-09-03, split at the sizing gate.** The rewrite below reached 2136 body tokens against wave 1's 1600. `4k` takes the `pm-ai project add` command and everything filesystem-shaped — path resolution, id derivation, directory creation, structure and `.gitignore` generation, adopting an existing tree, and the exclusive read-modify-write. This slice keeps the registry, its reader and its probe: the same reader-first split `4a`/`4g` used for `config.toml`, and for the same reason — a pure parser is testable without a machine.
  Writing it also surfaced a conflict no lens found: **`projects.toml` is TOML**, and `4a`'s `Never` forbids `tomllib` outside `pm_ai/core/config.py`, enforced by a sweep this session helped write. Widening that sweep to a named allowlist of two modules is now a task here, rather than a guard someone deletes in passing.

- **2026-09-03, rewritten against the human's onboarding decisions and the second multi-lens review.** The command changed shape: it takes a **path** with an optional **alias**, not an id and a repository path. The directory may be absent and is created; an existing pm-ai structure is adopted with its artefacts intact; an already-onboarded project is reported rather than refused; relative paths resolve to absolute; and the id is the working directory's name, so a name `_directory_name` refuses requires an alias rather than blocking onboarding.
  **Git stopped being a requirement** (decision Q1). The old `Always` demanded "an existing directory containing a git repository", which was stricter than the intent *and* stricter than the code: `service.py:714-717` already treats "no repository" as an answer and only refuses an unanswerable question. That also dissolves the review's A3 — there is no git check, so the missing `VcsPort` route on `Daemon` is no longer needed.
  **Structure generation became mandatory** rather than this slice's `Ask First`. Q6 moved four project artifacts into `GITIGNORED`, and that set is exactly what `_assert_git_excludes` guards, so inside a repository every write to them refuses until `.gitignore` carries the rule. The criterion asserts a real write succeeds, not that the file exists.
  **Two APIs in the old Intent and Code Map did not exist** (A1): `ScopePaths.real()` is `production()` (`paths.py:459`, and `wiring.py:85-86` had it right), and `projects_registry()` is the `project_registry` property. A criterion now greps for both, since this spec was itself created by a review and carried the error for a day.
  **Concurrency, an unreadable registry, and the `core` layering of the id check** are the edge-case findings now covered: `os.replace` keeps the last writer, so the read-modify-write needs an exclusive claim or an entry is lost; an unreadable registry must be distinguished from an absent one rather than minted over; and `_directory_name` lives in `platform`, which `core` may not import, so the sequence lives in `app`.
  **The claim that `build()` fails before dispatch was false** and is removed. `ScopePaths` has a `project_parent` fallback (`paths.py:552-556`), so an unregistered project resolves; verified by building against `never-registered`. `UnknownProject` is raised only where no parent is configured.
  KEEP: the read-modify-write rule and its reasoning. It is the same defect the review found in `8b` for the sealed store, with the same cause — a whole-file writer under an artifact that grows by entries.

## Verification

**Commands:**
- `uv run pytest tests/core/test_project_registry.py -q` -- expected: all matrix rows pass
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept; `pm_ai.core.project_registry` reaches no filesystem client
- `uv run mypy` -- expected: clean
