---
title: 'Operational schema versioning'
type: 'feature'
created: '2026-08-21'
status: 'ready-for-dev'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/storage-contract.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The operational store is a live SQLite database holding pending external writes, connector cursors, and the ledger of mutations already sent to external systems. It is the one store nothing can rebuild, because no other artifact contains that information. It carries no schema version, so its first schema change has no safe upgrade path — and the obvious implementation, dropping the tables and recreating them, destroys pending writes and resets every cursor. The architecture record still describes this tier as "in-memory today, so there is no schema to migrate yet", which stopped being true when it was given a real database file.

**Approach:** Add a `schema_version` row to the operational store and apply forward-only migrations in ascending order when the stored version is behind the code. A version newer than the code is refused rather than guessed at.

**Depends on:** story 1b, which moved the operational store to its resolved path.

## Boundaries & Constraints

**Always:**
- Migrations are forward-only and ordered. Each runs at most once; running the sequence twice changes nothing.
- Migrations preserve existing rows. The mutation ledger and the connector cursors must survive, because they are the state no rebuild can reconstruct.
- Migrations run at construction, after the connection is open and before any other statement, so no code can read a half-migrated schema.
- A version newer than the code refuses to open, naming both the stored and the expected version.

**Ask First:**
- Any migration that drops and recreates an operational table rather than altering it forward.
- Whether restoring the operational store from a backup should print its re-execution warning in this story or in the story that owns the CLI. Restoring an older copy means mutations performed after the backup are missing from the ledger, so a replayed job can act twice. The contract requires the user be warned; only the surface is open.

**Never:** No migration of the derived tier — it is rebuilt, not migrated, and that is story 1h. No automatic downgrade. No `pm-ai migrate` CLI command; the CLI is story 4.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| A newly created operational store with no version row | opened | stamped at the current version; no migration runs |
| A store one version behind the code | opened | pending migrations apply in ascending order |
| A store one version behind, holding mutation-ledger rows | opened | the migration completes and every pre-existing ledger row is readable afterwards |
| A store one version behind, holding connector cursors | opened | the cursors are unchanged after the migration |
| A store several versions behind | opened | every intervening migration applies, in order, once each |
| A store at a version newer than the code | opened | raises a typed error naming both versions, and does not modify the file |
| A store already at the current version | opened twice in succession | the second open runs no migration and changes no row |
| A migration that fails partway | opened | the store is left at its previous version rather than partly migrated |

</frozen-after-approval>

## Code Map

- `pm_ai/storage/service.py:37-64` — `_SCHEMA`, the SQL executed at startup, which gains the `schema_version` table.
- `pm_ai/storage/service.py:121-129` — the constructor, where the connection is opened. Migrations run here, before any other statement.
- `pm_ai/storage/service.py:48-54` — the `executed` table, the mutation ledger whose rows a migration must preserve.
- `pm_ai/storage/service.py:38-41` — the `cursors` table, likewise.
- `tests/slice/test_r4_gate_fixes.py:194-209` — constructs `StorageService` twice against one root and expects the mutation ledger from the first to survive. The migration step runs on that second construction, making this test the guard against a migration that resets state.
- `pm_ai/storage/service.py:7-12` — the module docstring recording that this tier was four in-memory dictionaries until 2026-08-19, which is the history behind the missing version column.

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/storage/service.py` — add the `schema_version` table and run forward-only migrations at construction, in ascending order, before any other statement.
- [ ] `tests/architecture/test_schema_versioning.py` — new. One test per matrix row, including a store stamped ahead of the code and a migration that fails partway.

**Acceptance Criteria:**
- Given a store one version behind holding ledger rows and cursors, when it is opened, then migrations run in order and every pre-existing row is readable afterwards.
- Given a store stamped at a version newer than the code, when it is opened, then it raises a typed error naming both versions and the file is unmodified.
- Given a store already at the current version, when it is opened twice, then the second open changes no row.
- Given `uv run pytest`, then `tests/slice/test_r4_gate_fixes.py:194-209` still passes and no previously passing test regresses.
- Given the architecture record's claim that this tier is "in-memory today", when this story lands, then that claim is corrected or reported as stale.

## Design Notes

**Why refusing a newer version is the safe response.** A store written by a later version of pm-ai may hold columns this code does not know about. Opening it and proceeding risks writing rows the later version then misreads — a corruption that appears long after the mistake. Refusing to open is the only response that cannot make things worse, and naming both versions is what makes the error actionable rather than merely blocking.

**Why the atomicity row matters more here than usual.** A half-migrated schema in a rebuildable store is an inconvenience. In this store it is unrecoverable, because there is no source to rebuild from. Leaving the version unchanged on failure means the next attempt starts from a known state.

## Verification

- `uv run pytest -q -rs` — expected: no previously passing test regresses; skip count unchanged.
- `uv run pytest tests/slice/test_r4_gate_fixes.py -q` — expected: green, confirming the ledger survives the migration path.
- Stamp a test store one version ahead of the code and confirm the refusal fires.
