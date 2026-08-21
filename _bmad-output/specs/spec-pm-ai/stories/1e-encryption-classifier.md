---
title: 'Encryption classifier'
type: 'feature'
created: '2026-08-21'
status: 'ready-for-dev'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/storage-contract.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** pm-ai encrypts a deliberately narrow set of artifacts and leaves everything else plaintext, because the PM must be able to read, grep, and hand-edit their own Markdown record without pm-ai running. Nothing in the codebase decides which artifact is which, so the boundary between the two sets exists only in prose.

**Approach:** Add `pm_ai/storage/crypto.py` with a single function that answers, for a given path, whether that artifact is encrypted at rest. Policy only — no cipher, no key. This satisfies the pre-written test at `tests/architecture/test_domain_invariants.py:188-206`.

## Boundaries & Constraints

**Always:**
- Classification is decided by what the artifact **is**, not by where it sits. `~/.pm-ai/private/vector_index/index.bin` is plaintext and `~/.pm-ai/private/config.json` is encrypted, and they share a parent directory — so any rule reading the `private/` prefix gets one of them wrong.
- Encryption and rebuildability are independent properties. `personal_analytics.db` is encrypted *and* not rebuildable; the vector index is plaintext *and* rebuildable. Do not infer either from the other.
- The function takes a path string and returns a boolean. It is pure: it does not touch the filesystem or check whether the file exists.

**Ask First:** Encrypting any artifact not on the storage contract's list, or leaving any artifact on that list unencrypted.

**Never:** No Markdown file is ever classified as encrypted, in any scope. No cipher, key handling, or file I/O in this story — those are stories 1d and 1f. Do not add `event_telemetry.db` to `ARTIFACT_TIER` to satisfy the stale assertion described in Design Notes.

## I/O & Edge-Case Matrix

The question asked of each path is *"is this artifact encrypted at rest?"*

| Given a path | Then | Because |
|---|---|---|
| `~/.manager-ai/memory/coaching_1on1_history.md` | no | all Markdown is plaintext by design |
| `~/.manager-ai/memory/strategic_goals.md` | no | all Markdown is plaintext by design |
| `<repo>/.project-ai/memory/commitments_log.md` | no | all Markdown is plaintext by design |
| `~/.pm-ai/private/vector_index/index.bin` | no | rebuildable derived index, despite sitting under `private/` |
| `~/.pm-ai/private/derived.db` | no | rebuildable derived store |
| `~/.pm-ai/private/operational.db` | yes | operational state no rebuild can reconstruct |
| `~/.pm-ai/private/config.json` | yes | API credentials |
| `~/.pm-ai/private/people/p1/dossier.md` | yes | a direct report's career record — the one place a `.md` file is encrypted, because the enclave rule wins |
| `~/.manager-ai/private/telegram_cache/state.json` | yes | the PM's own voice notes and dialogue state |
| `~/.manager-ai/private/personal_analytics.db` | yes | burnout and workload figures are recoverable personal facts |
| `<repo>/.project-ai/transcripts/2026-08-18.vtt` | yes | raw meeting capture |
| `~/.pm-ai/private/event_telemetry.db` | yes | the historical name of the operational store — see Design Notes |

</frozen-after-approval>

## Code Map

- `tests/architecture/test_domain_invariants.py:188-206` — the pre-written contract this story satisfies. It checks four plaintext paths and four encrypted ones. `:190` names `event_telemetry.db`; `:193` requires `vector_index/index.bin` to be plaintext despite its `private/` parent.
- `pm_ai/domain/storage_tiers.py:44-66` — `ARTIFACT_TIER`, which identifies the derived-tier artifacts that stay plaintext.
- `pm_ai/domain/identity.py:34-37` — the four scope roots that appear in the paths being classified.

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/storage/crypto.py` — new. `is_encrypted(path: str) -> bool`, classifying by artifact role.
- [ ] `tests/architecture/test_encryption_policy.py` — new. One test per matrix row.

**Acceptance Criteria:**
- Given `uv run pytest`, then `test_ad6_markdown_is_never_encrypted` passes and the skip count falls from 30 to 29.
- Given the `runtime` extra is not installed, then `pm_ai.storage.crypto` imports successfully and no test skips on a missing import.
- Given `uv run lint-imports`, then all 12 contracts hold.
- Given every path in the matrix, then the classifier returns the stated answer — including the two that share the `private/` parent and disagree.

## Design Notes

**A stale assertion to expect.** The pre-written test asserts that `~/.pm-ai/private/event_telemetry.db` is encrypted. That file no longer exists. The architecture record describes `event_telemetry.db` as the original mistake — one file that mixed operational state with rebuildable indexes — later split into `operational.db` and `derived.db`. Classifying by role, rather than matching filenames, satisfies the old assertion and the current names together. Report the stale name rather than reviving it in the tier table.

## Verification

- `uv run pytest -q -rs` — expected: 29 skipped, with no remaining skip naming `pm_ai.storage.crypto`.
- `uv run python -c "import pm_ai.storage.crypto"` — expected: silent success.
- `uv run lint-imports` — expected: `Contracts: 12 kept, 0 broken.`
