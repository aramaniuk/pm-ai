---
title: 'Guards that survive optimization'
type: 'feature'
created: '2026-08-22'
updated: '2026-08-22'
status: 'done'
review_loop_iteration: 0
baseline_commit: '62bffba'
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/storage-contract.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/scope-model.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The scope model's structural integrity is enforced by ten bare `assert` statements that run at import time. `python -O` strips every one of them.

Verified by AST, not inferred: ten `ast.Assert` nodes, in `lifecycle.py`,
`scope_model.py` and `storage_tiers.py`, and none anywhere else in `pm_ai/`.
Under `python -O`, `__debug__` is `False` and every one of them is stripped.

**A correction to this story's original evidence, on the record.** The block that
used to sit here ended `-O: AD-44 duplicate-tier guard is an assert -> INERT`,
and that line was wrong. It came from a probe that only tested `__debug__`; the
label was mine rather than a measurement, and I presented it as one. The
duplicate-tier refusal is `raise MalformedLayout` in `_durability_index` and was
always `-O`-safe — confirmed during implementation, identical message under both
interpreters. The story's case is unaffected, because ten genuine asserts existed
and were genuinely stripped, but the headline example was not one of them.

The ten are not sanity checks a programmer leaves behind. They are the *only* thing verifying that the derived sets are coherent — that one artifact name does not mean two relative paths, that the three tiers are disjoint from `RETENTION_MANAGED` and `DIAGNOSTIC_ONLY`, that `GITIGNORE_REQUIRED` names only real keys, that nothing is both a rebuild target and a backup target, and that no proposal state shares a name with a commitment state. Under `-O`, `pm_ai.domain.scope_model` imports clean with none of that established, and the first symptom is a rebuild deleting Tier-2 state or a capture written to a path no rule covers.

This is a plausible production configuration rather than a hypothetical. The daemon runs as a `launchd` user agent, and `-O` in a plist is an ordinary thing someone adds for startup time. A guard that depends on an interpreter flag is not a guard.

Flagged after story 1a as pre-existing in `storage_tiers.py` too, and deferred then as a codebase-wide call rather than 1a's defect. This is that call.

**Approach:** Convert all ten import-time asserts to explicit `raise` of a typed error, keeping every existing message verbatim. Add one test that proves the guards still fire under `-O`, because a fix nobody can regress is a fix that gets reverted by the next refactor.

## Boundaries & Constraints

**Always:**
- **The message survives the conversion.** Each of these asserts carries a diagnostic that names what disagreed and why it matters; several are the only documentation of the invariant. Moving the condition into an `if` must not shorten the text.
- Each raise uses a **typed** error, not bare `Exception` — something a caller could catch and a reader can name. Failures of the model are not failures of an argument, so `ValueError` is a poor fit; prefer a dedicated error type in `pm_ai.domain`.
- Ten sites, exactly: `lifecycle.py`, `scope_model.py` ×7, `storage_tiers.py` ×2. An AST sweep confirms there are **no other assert statements in `pm_ai/`** — not in functions, not in methods — so this is the complete set and the story has a definite end. (Line numbers deliberately unpinned: 1j and 1k moved them between this story being written and being run.)
- The guards keep running **at import**, not on first use. Their value is that a mis-declared model cannot be loaded at all; deferring them to a call site is a different and weaker property.
- No new dependency, no behaviour change on the healthy path. A correct model imports exactly as it does today.

**Ask First:** Converting anything that is genuinely a programmer-sanity assert rather than a model check — the sweep says none exists today, so a candidate appearing mid-story means the sweep was wrong and is worth a pause. Introducing a `-O`-aware branch, or any code that reads `__debug__`.

**Never:** No `assert` left anywhere in `pm_ai/` that establishes an invariant the system relies on. No swapping bare asserts for a helper that itself asserts. No `-O` in any project configuration to sidestep the question. No relocation of these checks into the test suite: a test proves the model *in this repo* is coherent, and the guard must also refuse a model someone edits after installing.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| A coherent model, normal interpreter | `import pm_ai.domain.scope_model` | imports silently, exactly as today |
| A coherent model, `python -O` | the same import | imports silently — the fix must not make optimized mode noisy |
| One basename declared at two different tiers, normal interpreter | the import | raises, naming both tiers |
| The same, under `python -O` | the import | **raises identically** — this is the whole point of the story |
| A key naming two relative paths across scopes | the import | raises, naming the key and both paths |
| A tier set overlapping `RETENTION_MANAGED` or `DIAGNOSTIC_ONLY` | the import | raises, naming the offending artifact |
| `GITIGNORE_REQUIRED` naming a key no tree declares | the import | raises |
| An artifact in both `REBUILD_TARGETS` and `BACKUP_TARGETS` | the import | raises |
| A `ProposalState` member sharing a name with a `CommitmentState` member | the import | raises — the AD-14 guard, which matters more once story 16 adds `ERROR` |
| An **AST sweep** for `ast.Assert` across `pm_ai/` | after this story | returns nothing. Deliberately not a grep: the word appears in three comments about what a connector may not assert, so a text search reports failures that are not there and could be "fixed" by rewording prose |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/scope_model.py:805-860` — `_assert_declarations_agree()`, called at module level on the last line. Seven asserts at `:818, 824, 829, 841, 844, 847, 855`. Its docstring already draws the right line: what a node can check about itself is in `__post_init__`; this function holds "the relationships *between* declarations."
- `pm_ai/domain/scope_model.py` — the `__post_init__` checks on `File`, `Dir` and `Collection` are already raises rather than asserts, so they survive `-O`. They are the pattern to follow, and the inconsistency this story removes.
- `pm_ai/domain/storage_tiers.py:194` — `_CODE_KEYS <= KEYS`, so a renamed artifact key fails at import rather than at the first write.
- `pm_ai/domain/storage_tiers.py:202` — `set(GITIGNORE_REQUIRED) <= KEYS`.
- `pm_ai/domain/lifecycle.py:63` — the AD-14 guard that `ProposalState` and `CommitmentState` never share a member name. Story 16 adds `ERROR` to `CommitmentState`, which is exactly when this guard earns its keep.
- `tests/architecture/test_layering.py` — the precedent for a test that shells out to prove something about the environment rather than the code, and for failing rather than skipping when it cannot.

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/domain/invariants.py` — new. `InconsistentModel(RuntimeError)`, in its own module because `lifecycle.py` imports only `identity`, and a shared home avoids inventing an edge between the three.
- [x] `pm_ai/domain/scope_model.py` — seven converted, messages byte-identical.
- [x] `pm_ai/domain/storage_tiers.py` — two converted, and wrapped in `_assert_code_keys_are_declared()` called at module level. They were bare module statements, so no test could re-run them against a doctored model; every guard is now callable, which is what made all ten testable the same way.
- [x] `pm_ai/domain/lifecycle.py` — one converted, wrapped in `_assert_lifecycles_are_distinct()` for the same reason.
- [x] `tests/architecture/test_guards_survive_o.py` — eight `-O` subprocess cases (one per class of guard), one asserting a coherent model still imports silently under `-O`, and one AST sweep for the mechanism. Ten tests.

**Acceptance Criteria:**
- Given `uv run python -O -c "import pm_ai.domain.scope_model"`, then it succeeds silently on the current, coherent model.
- Given a deliberately broken declaration, when imported under `-O`, then it raises the typed error with the same message it raises without `-O`.
- Given an AST sweep for `ast.Assert` over `pm_ai/`, then there are no matches. The grep form this criterion originally used was wrong: three comments contain the word.
- Given `uv run pytest`, then 243 passed and 30 skipped (ten new tests on top of 1k's), so `EXPECTED_SKIPS` in `tests/conftest.py` needed no change.
- Given `uv run mypy pm_ai`, then it still reports success — this story lands after 1k, so a clean type check is the baseline it inherits and must preserve.
- Given `uv run lint-imports`, then all 12 contracts hold: the new error type lives in `domain`, which every other layer may import.

## Design Notes

**Why not simply move the checks into tests.** A test proves that *this repository's* model is coherent. The guard also has to refuse a model that someone edits after installing — `scope_model.py` is a plain Python file in a `uv tool install`ed package, and the failure mode being prevented is a hand-edited declaration that makes a rebuild delete Tier-2 state. That refusal has to live in the code that loads, not in a suite that shipped separately.

**Why the messages are load-bearing.** Several of these asserts are the clearest statement of their invariant anywhere in the repo — `scope_model.py:818` explains that a key means one relative path in every scope "which is what lets" the trees be diffed against the companion. Rewriting them shorter during a mechanical conversion is how a refactor quietly deletes documentation. Keep the text; change only the mechanism.

**Why `__post_init__` is the precedent rather than an exception.** The node types already raise. So the codebase has both patterns side by side, and the assert half was chosen for brevity at a point when nobody was thinking about `-O`. This makes the file internally consistent, which is a smaller change than it sounds and a better argument than the `-O` risk alone.

**Why a test under `-O` and not just a code review.** The conversion is mechanical, which means the next mechanical refactor can undo it just as easily — someone tidying nine `if … raise` blocks back into asserts would be making the file shorter and would pass every existing test. The `-O` test is what makes the property permanent rather than momentary.

## Verification

- `uv run python -O -c "import pm_ai.domain.scope_model"` — expected: silent success.
- `uv run python -O -c "import pm_ai.domain.storage_tiers, pm_ai.domain.lifecycle"` — expected: silent success.
- `uv run pytest tests/architecture/test_guards_survive_o.py -q` — expected: 10 passed, including the AST sweep that replaces the grep.
- `uv run pytest -q` — expected: 243 passed, 30 skipped.
- `uv run mypy pm_ai` — expected: `Success: no issues found`.
- Declare `daily_dashboard.md` at `Tier.DERIVED` in one tree while it stays `Tier.TRUTH` in another, then import under **both** `python` and `python -O`. Expected: the same raise, with the same message, both times.

  **Run, 2026-08-22.** Identical `MalformedLayout: one artifact key, two durability promises: ['daily_dashboard.md is DER…']` under both. And this particular guard turned out never to have needed the story: `_durability_index` already refused with a `raise`, which is what exposed the mislabelled evidence corrected in Intent above. The eight cases in `test_guards_survive_o.py` cover the ten that genuinely were asserts.
