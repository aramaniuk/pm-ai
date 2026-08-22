---
title: 'Type-checking gate'
type: 'feature'
created: '2026-08-22'
updated: '2026-08-22'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'a9025b8'
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/SPEC.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every `Protocol` in `pm_ai/ports/` is documentation. Nothing verifies that an adapter's method signatures match the port it claims to satisfy — the `@runtime_checkable` `isinstance` tests confirm an attribute *exists* and stop there, so a wrong keyword name, a changed parameter order, or a missing method surfaces at the call site during a real run.

This is not hypothetical. Running mypy at default settings over the current tree returns 10 errors in 4 files, and two are defects nothing else in the repo can see:

- **`storage/service.py:400` — `"ScopePathPort" has no attribute "repository"`.** The single writer calls `self._paths.repository(...)` on a value typed as the port, and the port does not declare that method. `pm_ai.storage` is therefore coupled to the concrete `ScopePaths` shape rather than to the contract — a hexagonal violation. `import-linter` cannot catch it: it checks which modules import which, not whether an interface is complete.
- **`skills/registry.py:59-82` — five `"object" has no attribute …` errors.** `SkillRegistry` holds `dict[str, object]` and `register(self, skill)` is unannotated, so `skill.system`, `skill.name`, `skill.permission` and `skill.execute` are all unchecked. That is the module enforcing declared skill permissions: the security boundary is the least-typed code in the package.

And it gets more expensive immediately: 1d adds `KeychainPort`, 1f adds `CryptoPort`, 1j adds a method to `VcsPort`. Three new protocol surfaces whose drift nothing would report.

**Approach:** Add mypy to the `dev` extra with a `[tool.mypy]` block at **default** settings, fix all 10 errors at their cause, and gate it inside pytest the way `test_layering.py` already gates import-linter — because that is the pattern this repo chose: *"one command checks every architectural invariant, rather than layering being a separate step someone forgets to wire into CI."* There is no CI, so an ungated checker is a checker nobody runs.

Default settings, not `--strict`. Strict returns 24, and the extra 14 are annotation-completeness pedantry (`Missing type arguments for generic type "dict"`) rather than defects. Tightening is a later ratchet with its own slice.

## Boundaries & Constraints

**Always:**
- The gate lives in pytest and a **missing mypy binary is a failure, never a skip** — the precedent set for import-linter. mypy is a declared dev dependency, so its absence is a broken environment, and a skip would report green while the check did not run.
- Fix causes, not symptoms. Each of the 10 errors is repaired where it originates.
- **`ScopePathPort` gains what `pm_ai.storage` actually calls, or storage stops calling it.** The concrete type is not an option — `pm_ai.storage` may not import `pm_ai.platform`. Silencing this one with a `cast` would delete the finding rather than the defect, and this error is the reason the story exists.
- The registry's skill type is **declared**, not ignored. `object` there means the permission check operates on a value the checker knows nothing about.
- **No behaviour change.** No feature, and no altered runtime path except where a narrowing assert makes an existing invariant explicit rather than assumed.

**Ask First:**
- Enabling `--strict`, or any per-module override that switches checking off for a whole module.
- `ignore_missing_imports` for anything beyond the optional runtime extras that are deliberately not installed here (`keyring`, `sqlcipher3`, `sqlite_vec`).
- Adding `repository`/`gitignore` to `ScopePathPort` when story 1j is expected to replace both calls with working-tree discovery. Adding them is still the correct fix — a port must declare what its consumers call, and 1j removes them from the port if it removes the calls — but the churn is worth a moment's thought rather than none.

**Never:** No `# type: ignore` without a specific error code and a reason on the same line. No `Any` introduced to make an error disappear. No `--strict` in this story. No new *runtime* dependency — mypy is `dev` only.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| mypy on PATH, tree clean | the gate test runs | passes |
| mypy absent from PATH | the gate test runs | **fails** naming the remediation; never skips |
| an adapter method whose keyword name differs from its port's | mypy runs | reported, before any call site executes |
| a port missing a method a consumer calls | mypy runs | `attr-defined`, as `ScopePathPort.repository` is today |
| a new port added with no adapter satisfying it | mypy runs | reported at the wiring site |
| the `runtime` extra absent | mypy runs | no error attributable to `keyring`, `sqlcipher3` or `sqlite_vec` |
| `uv run pytest` with no arguments | the suite runs | the mypy gate is one of its tests, so one command still checks every invariant |
| a `# type: ignore` with no error code | the gate runs | reported — bare ignores are what turn a checker into decoration |

</frozen-after-approval>

## Code Map

- `pyproject.toml:28-31` — the `dev` extra (`pytest>=8`, `import-linter>=2`), which gains mypy.
- `pyproject.toml` — has no `[tool.mypy]`; add it beside `[tool.pytest.ini_options]` at `:40-42`.
- `tests/architecture/test_layering.py` — the shape to copy, including the fail-not-skip guard and the remediation text naming `uv run`.
- `tests/conftest.py` — `EXPECTED_SKIPS = 30`. This story un-skips nothing, so the count is unchanged; the gate must be a passing test, never a skipped one.
- The ten errors, all of them:
  - `pm_ai/storage/service.py:400` — `ScopePathPort` has no `repository`.
  - `pm_ai/storage/service.py:412` — `scope.project_id` is `str | None`, `gitignore` wants `str`. The docstring at `:387-395` already argues the invariant holds "by construction"; an assert makes that claim checkable.
  - `pm_ai/skills/registry.py:39` — `self._skills: dict[str, object]`, and `:42` `register(self, skill)` unannotated. Source of the five `attr-defined` errors at `:59-82`.
  - `pm_ai/platform/paths.py:418,444` — `rooted()` and the test factory pass `Mapping[str, Path | str]` to a field declared `Mapping[str, Path]`. `__post_init__` coerces via `_absolute_map`, so runtime is fine and the annotation is what lies.
  - `pm_ai/app/wiring.py:79` — `Path | None` passed where `Path | str` is expected.

## Tasks & Acceptance

**Execution:**
- [x] `pyproject.toml` — `mypy>=2` in the `dev` group; `[tool.mypy]` with `files = ["pm_ai"]` and `python_version`, plus `ignore_missing_imports` for the three deliberately-absent runtime extras.
- [x] ~~`ScopePathPort` declares what `pm_ai.storage` calls~~ — **dissolved before this story ran.** Story 1j replaced the project-keyed `repository()`/`gitignore()` calls with working-tree discovery, so the coupling went away with the calls rather than needing a port change. The error count was 10 when this story was written and 8 when it started.
- [x] `pm_ai/skills/registry.py` — skills typed `SkillPort`, which declares exactly the four attributes the registry reads.
- [x] `pm_ai/platform/paths.py` — both factories coerce with `_absolute_map` before construction, so the field's `Mapping[str, Path]` stops being a declaration only the coercion happened to cover.
- [x] `pm_ai/app/wiring.py` — narrowed by restructuring the XOR check into three branches, **not** by an assert: story 1l removes every assert from `pm_ai/`, and nothing deciding which resolver the daemon gets should depend on `python -O`.
- [x] `pm_ai/ports/__init__.py` — **not in the original plan.** Typing the registry's `storage` surfaced three more undeclared methods (`begin_execution`, `settle_execution`, `executed_mutations`). `StoragePort` declared only the single-phase `record_execution`, which is a convenience wrapper whose one caller is a test named `idem_legacy`; the two-phase claim-then-settle form AD-20 requires was what the registry actually used. Same shape as the `ScopePathPort` finding, in the class enforcing AD-18.
- [x] `tests/architecture/test_types.py` — the gate. Runs `mypy` with no arguments so `pyproject.toml` stays the single definition of what is checked, and fails on a missing binary.
- [x] `.gitignore` — `.mypy_cache/`.

**Acceptance Criteria:**
- Given `uv run mypy pm_ai`, then it reports success with no issues across all 45 source files.
- Given mypy is not on PATH, when the gate test runs, then it fails with remediation text and does not skip.
- Given `uv run pytest`, then 233 passed and 30 skipped — one new test on top of 1j's, no change in skips, so the ratchet in `tests/conftest.py` stayed satisfied without being touched.
- Given `uv run lint-imports`, then all 12 contracts hold — the `ScopePathPort` change must not create a new import edge.
- Given the whole story, then no runtime behaviour differs: no test's expected output changes.
- Given a grep for `type: ignore`, then every occurrence carries an error code.

## Design Notes

**Why the gate goes in pytest rather than CI.** There is no CI, no pre-commit, no Makefile. A checker that must be remembered is a checker that runs until the first busy week. `test_layering.py` exists for exactly this reason and says so in its own docstring; this is the same argument applied to the same class of problem.

**Why default rather than strict.** 10 errors versus 24, and the 14 extra are about annotation completeness rather than correctness. Landing a checker that reports zero on arrival is what makes a red result meaningful later; landing one with 14 known-acceptable complaints trains everyone to read red as normal. Strict earns its own slice, with a ratchet, once zero holds.

**Why `service.py:400` must not be cast away.** The temptation is one `cast` and the error is gone. But the error is not the problem — the coupling is. `pm_ai.storage` is written against `ScopePathPort` precisely so the resolver can be swapped, and it currently depends on a method outside that contract. A cast preserves the coupling and removes the only signal that it exists. This is the single clearest demonstration of why the story is worth doing, so it is also the one place a shortcut would be self-defeating.

**The overlap with 1j, stated so it is not discovered.** Both `service.py:400` and `:412` sit in `_assert_git_excludes`, which story 1j rewrites — replacing the project-keyed `repository()`/`gitignore()` calls with working-tree discovery. Fixing them here is still correct: 1j should inherit a clean baseline rather than a file with two known errors in the function it is about to change. If 1j then removes the calls, it removes the port methods with them, which is ordinary evolution rather than waste.

## Verification

- `uv run mypy pm_ai` — expected: `Success: no issues found in 45 source files`.
- `uv run pytest -q` — expected: 233 passed, 30 skipped.
- `uv run lint-imports` — expected: `Contracts: 12 kept, 0 broken.`
- `env PATH=/usr/bin:/bin .venv/bin/pytest tests/architecture/test_types.py -q` — expected: 1 failed, with remediation text; **not** 1 skipped.
- Rename one keyword parameter on a `pm_ai/platform` adapter away from its port's spelling, confirm mypy goes red, then restore it. This is the failure the story exists to catch, so it is the one to demonstrate rather than assume.

  **Run, 2026-08-22.** `GitVcs.tracking`'s `repository` keyword renamed to `repo`. mypy: `pm_ai/app/wiring.py:95: error: Argument "vcs" to "StorageService" has incompatible type "VcsPort | GitVcs"; expected "VcsPort"`. The pre-existing `isinstance` conformance tests: **23 passed**, because `tracking` still exists as an attribute and that is all they check. That contrast is the story's whole justification, measured rather than argued.
