---
title: 'Scope path resolver'
type: 'feature'
created: '2026-08-21'
status: 'done'
review_loop_iteration: 0
baseline_commit: '8873e76869c716c414a1b51c3dec3d4c05d88b6b'
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/scope-model.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** pm-ai keeps four categories of data physically apart so that personal coaching material cannot reach a team repository and a direct report's record cannot be read by their peers. No object in the codebase can answer where any of them live. The four target directories exist only as comments on `ScopeKind` (`pm_ai/domain/identity.py:34-37`).

**Approach:** Add `pm_ai/platform/paths.py`: one object that resolves a scope plus an artifact name to an absolute path. A production factory reads the real home directory; a test factory roots all four scopes beneath a directory it is given. Nothing consumes it in this story.

## Boundaries & Constraints

**Always:**
- This resolver becomes the only place in the codebase that encodes a directory layout.
- The `ScopeKind` comments at `identity.py:34-37` are the specification: `~/.pm-ai/`, `~/.manager-ai/`, `~/.pm-ai/private/people/`, `<repo>/.project-ai/`.
- A project's repository path is supplied to the resolver, never discovered by searching the filesystem for `.project-ai` directories.
- The resolver may create directories. It writes no file contents — that stays with `StorageService`.

**Ask First:** Adding an artifact to `ARTIFACT_TIER` (`pm_ai/domain/storage_tiers.py:44`), whose exact contents are asserted by `tests/slice/test_r4_gate_fixes.py:260-291`.

**Never:** No change to any existing caller — wiring the resolver into storage is story 1b. No encryption. No file writes from `pm_ai/platform/`.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| A production resolver | asked for the application scope's disclosure ledger | returns `~/.pm-ai/disclosure.md` |
| A production resolver | asked for the personal scope's event log | returns `~/.manager-ai/memory/event_log/` |
| A production resolver, project `alpha` at a known repository path | asked for that project's event log | returns `<repo>/.project-ai/memory/event_log/` |
| A production resolver | asked for the people scope of person `p1` | returns `~/.pm-ai/private/people/p1/` |
| A production resolver | asked for the operational store | returns `~/.pm-ai/private/operational.db`, never a path inside a scope's Markdown tree |
| A production resolver | asked for the derived store and the vector index | returns paths in a different file and directory from the operational store |
| A test resolver rooted at a temporary directory | asked for any scope and artifact | returns a path beneath that directory, same relative structure as production, no `~` left unexpanded |
| A project scope constructed with no project id | the scope is built | `ValueError` from `DataScope.__post_init__`, propagated unchanged |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/identity.py:20-38` — `ScopeKind` and its four directory comments, this story's specification. `:52-61` already validates that a scope carries its subject id, so a resolver taking a `DataScope` inherits that check.
- `pm_ai/domain/storage_tiers.py:44-66` — `ARTIFACT_TIER`, naming every artifact the resolver must place.
- `pm_ai/domain/disclosure.py:27` — `DISCLOSURE_LEDGER_PATH`, a bare string this resolver should own.

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/platform/paths.py` — new. Resolve `(DataScope, artifact) -> Path` for four scopes and three tiers; production and test factories.
- [x] `tests/architecture/test_paths.py` — new. One test per matrix row.

**Acceptance Criteria:**
- Given `uv run pytest`, then the existing 119 tests still pass and the skip count stays at 30.
- Given `uv run lint-imports`, then all 12 contracts hold.
- Given a test resolver, when every scope and artifact is resolved, then every path lies beneath the given directory.
- Given the operational store and the derived store, when both are resolved, then no delete of one could remove the other.

## Verification

- `uv run pytest -q -rs` — expected: 119 passed, 30 skipped, no new skip.
- `uv run lint-imports` — expected: `Contracts: 12 kept, 0 broken.`

## Suggested Review Order

**The layout itself**

- Start here: artifact to path-below-its-scope-root, the whole layout in one table.
  [`paths.py:198`](../../../../pm_ai/platform/paths.py#L198)

- The privacy boundary as a table — which scope kinds may hold each artifact.
  [`paths.py:240`](../../../../pm_ai/platform/paths.py#L240)

- Intent stated separately from mechanism, so a new personal artifact is covered without editing a test.
  [`paths.py:282`](../../../../pm_ai/platform/paths.py#L282)

- Refuses an illegal (scope, artifact) pair rather than returning a path for it.
  [`paths.py:479`](../../../../pm_ai/platform/paths.py#L479)

**Containment — the guards review added**

- Rejects traversal, absolute, leading-dot, and non-lowercase ids before they reach a path join.
  [`paths.py:144`](../../../../pm_ai/platform/paths.py#L144)

- Lexical normalisation, so `..` cannot survive into a containment check.
  [`paths.py:560`](../../../../pm_ai/platform/paths.py#L560)

- The test factory's promise enforced: a registered repository must lie beneath the root.
  [`paths.py:423`](../../../../pm_ai/platform/paths.py#L423)

- Normalises on direct construction too, so the factories are a convenience and not the only safe path.
  [`paths.py:354`](../../../../pm_ai/platform/paths.py#L354)

**Scope roots and the two factories**

- Production knows only registered repositories; it cannot invent one.
  [`paths.py:371`](../../../../pm_ai/platform/paths.py#L371)

- The one dangerous difference: `rooted()` may place an unregistered project under its root.
  [`paths.py:392`](../../../../pm_ai/platform/paths.py#L392)

- Unregistered project is an error, never a guess.
  [`paths.py:442`](../../../../pm_ai/platform/paths.py#L442)

- One shared error base, so a caller can catch every refusal in one clause.
  [`paths.py:85`](../../../../pm_ai/platform/paths.py#L85)

**Tests that were proven to fail on a planted defect**

- Tier 1 may not live inside anything a rebuild deletes.
  [`test_paths.py:139`](../../../../tests/architecture/test_paths.py#L139)

- The gitignore rule must cover the path the resolver actually returns.
  [`test_paths.py:160`](../../../../tests/architecture/test_paths.py#L160)

- Traversal blocked at the boundary the module exists to defend.
  [`test_paths.py:202`](../../../../tests/architecture/test_paths.py#L202)

- The layout relocated, not reimplemented: same relative structure as production.
  [`test_paths.py:121`](../../../../tests/architecture/test_paths.py#L121)

**Peripherals**

- Coverage table updated for the ADs this story moved from prose to assertion.
  [`README.md`](../../../../tests/architecture/README.md)
