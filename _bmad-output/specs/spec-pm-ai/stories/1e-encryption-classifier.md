---
title: 'Encryption classifier'
type: 'feature'
created: '2026-08-21'
updated: '2026-08-22'
status: 'done'
review_loop_iteration: 1
baseline_commit: '54bf106'
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/storage-contract.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/scope-model.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** pm-ai encrypts a deliberately narrow set of artifacts and leaves everything else plaintext, because the PM must be able to read, grep, and hand-edit their own Markdown record without pm-ai running. Nothing in the codebase decides which artifact is which, so the boundary between the two sets exists only in prose.

**Approach:** Declare encryption **on the node**, in the four scope trees in `pm_ai/domain/scope_model.py`, the way each artifact already carries its `Tier` — and derive an `ENCRYPTED` set from the trees alongside `ARTIFACT_TIER`, `RETENTION_MANAGED` and `DIAGNOSTIC_ONLY`. `pm_ai/storage/crypto.py` then exposes `is_encrypted(path: str) -> bool`, which resolves the path against that derived set. Policy only — no cipher, no key. This satisfies the pre-written test at `tests/architecture/test_domain_invariants.py:193-210`.

**Renegotiated 2026-08-22.** The original approach was a hand-written classifier in `crypto.py`, authored before the scope trees became domain data. A second structure maintained beside the trees is the shape the tier tables were just moved *out* of, for the same reason: two edits to add one artifact, and nothing but an import-time assertion catching the two drifting apart. Declaring it on the node means a new artifact cannot be added without an encryption answer.

## Boundaries & Constraints

**Always:**
- Encryption is declared where the artifact is declared. Adding a `File` or `Collection` to a tree without an encryption answer is a construction error, not a late default — the same bar the required `Tier` field sets.
- Classification is decided by what the artifact **is**, not by where it sits. `~/.pm-ai/private/vector_index/index.bin` is plaintext and `~/.pm-ai/private/config.json` is encrypted, and they share a parent directory — so any rule reading the `private/` prefix gets one of them wrong.
- Encryption and rebuildability are independent properties. `personal_analytics.db` is encrypted *and* not rebuildable; the vector index is plaintext *and* rebuildable. Do not infer either from the other.
- **The answer is per scope, keyed on `(scope, key)` — renegotiated during implementation, 2026-08-22.** This rule originally read "the basename is declared once and the answer is global". That works for `transcripts/`, which wants the same answer in all three scopes declaring it, and it is impossible for `meetings/` and `event_log/`: both are declared under `people/`, where `storage-contract.md:38` requires a report's records encrypted, *and* in a project, where `:43` requires committed Markdown plaintext. One global slot must be wrong about one of them. So encryption is keyed the way *paths* already are, and `transcripts/` gets one answer by three declarations agreeing — asserted rather than assumed, since per-scope keying makes disagreement expressible.
- **`gitignored` is declared the same way, on the same nodes.** It had the identical latent defect: `GITIGNORE_REQUIRED` was a global basename set that worked only because it had one member, and `event_log/` is one artifact away from breaking it. Both axes now live on the node, per tree.
- An **undeclared** path fails closed: it classifies as encrypted. A path no tree names is either a historical name or an artifact someone forgot to declare, and guessing plaintext on either is the guess that leaks.
- `is_encrypted` takes a path string and returns a boolean. It is pure: it does not touch the filesystem or check whether the file exists.
- **Tests build their paths from the resolver, never from literals.** The matrix below spells paths out because a reader needs to see them, but a test that hardcodes `~/.pm-ai/private/people/p1/dossier.md` asserts against this story's belief about the layout rather than against the layout — and it keeps passing after the resolver moves the artifact. Call `resolve(scope, artifact)` and classify what comes back. (Instruction restored 2026-08-22: it was attached to this story after 1a and lost when the story was rewritten for the derive-from-trees approach.)

**Ask First:** Encrypting any artifact the storage contract lists as plaintext, or leaving any artifact on its encrypted list unencrypted. Making the undeclared-path answer anything other than fail-closed.

**Never:** No Markdown file is ever classified as encrypted in any scope, with the single exception the enclave rule already carries — a direct report's dossier under `people/`. No cipher, key handling, or file I/O in this story — those are stories 1d and 1f. Do not revive `event_telemetry.db` or `chat_history/` as declared artifacts to satisfy the stale assertions described in Design Notes.

## I/O & Edge-Case Matrix

The question asked of each path is *"is this artifact encrypted at rest?"*

| Given a path | Then | Because |
|---|---|---|
| `~/.manager-ai/memory/coaching_1on1_history.md` | no | all Markdown is plaintext by design |
| `~/.manager-ai/memory/strategic_goals.md` | no | all Markdown is plaintext by design |
| `<repo>/.project-ai/memory/commitments_log.md` | no | all Markdown is plaintext by design |
| `~/.pm-ai/private/vector_index/index.bin` | no | rebuildable derived index, despite sitting under `private/` |
| `~/.pm-ai/private/event_index.db` | no | rebuildable derived index |
| `~/.pm-ai/private/commitment_index.db` | no | rebuildable derived index |
| `~/.pm-ai/private/operational.db` | **no** | queue state and cursors rather than record content; 600 and full-disk encryption |
| `~/.pm-ai/private/config.json` | yes | API credentials |
| `~/.pm-ai/private/people/p1/dossier.md` | **no** | gitignored, 600-permissioned, and a single deletable directory |
| `~/.manager-ai/private/telegram_cache/state.json` | yes | the PM's own voice notes and dialogue state |
| `~/.manager-ai/private/personal_analytics.db` | yes | burnout and workload figures are recoverable personal facts |
| `<repo>/.project-ai/transcripts/2026-08-18.vtt` | **no** | a capture's exposure is publication to a repository; the git guard is what addresses it |
| `~/.pm-ai/private/people/p1/transcripts/2026-08-18.vtt` | **no** | same answer, different scope |
| `~/.manager-ai/transcripts/2026-08-18.vtt` | **no** | same answer again |

> **Amended 2026-08-22, after this story shipped.** The user narrowed the encrypted set to credentials and the sovereign personal enclave. Five rows flipped from yes to no; they are edited rather than annotated one by one, because a matrix that states answers the system does not give is worse than one that shows its history. The mechanism this story built is unchanged — the answers are still declared on the node and derived per scope — and `storage-contract.md` records what protects each dropped artifact instead.
| `~/.pm-ai/private/event_telemetry.db` | yes | a historical name, undeclared, so fail-closed — see Design Notes |
| `~/.pm-ai/private/chat_history/2026-08-18.vtt` | yes | likewise a historical name, and likewise fail-closed |

</frozen-after-approval>

## Code Map

- `tests/architecture/test_domain_invariants.py:193-210` — the pre-written contract this story satisfies. It checks four plaintext paths and four encrypted ones. `:203` names `event_telemetry.db` and `:205` names `chat_history/`; `:199` requires `vector_index/index.bin` to be plaintext despite its `private/` parent.
- `pm_ai/domain/scope_model.py:163` — `Tier`, and the `File`/`Dir`/`Collection` node types whose declarations gain the encryption field.
- `pm_ai/domain/scope_model.py:761-775` — `ARTIFACT_TIER`, `REBUILD_TARGETS`, `BACKUP_TARGETS`, `RETENTION_MANAGED` and `DIAGNOSTIC_ONLY`, derived from the trees. `ENCRYPTED` joins them here.
- `pm_ai/domain/scope_model.py:534`, `:557`, `:599` — the three `Collection("transcripts", RETAINED)` declarations, one per capture-holding scope. All three take the same encryption answer.
- `pm_ai/domain/scope_model.py:844-848` — the pairwise-disjointness assertions the new set must sit beside without breaking.
- `pm_ai/domain/storage_tiers.py:38-70` — the re-export surface, so `ENCRYPTED` reaches `pm_ai.storage` by the path the tier sets already travel.
- `pm_ai/platform/paths.py` — the resolver, for turning a declared artifact back into the paths the matrix spells.

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/domain/scope_model.py` — `encrypted` **and** `gitignored` as required keyword-only fields on `File` and `Collection`, optional on `Dir`. Answered on all 37 declarations across the four trees. `ENCRYPTION`/`EXCLUSION` derived as `(scope, key) -> bool` mappings, with `ENCRYPTED`/`GITIGNORED` as membership views. The mapping rather than a set is load-bearing: an artifact can answer yes, answer no, or **declare nothing** — the application scope's `private/`, whose members disagree — and a set cannot tell the last two apart. A classifier that conflates them stops walking at `private/` and reports everything beneath it plaintext, inverting the fail-closed rule exactly where it matters. Caught by two failing matrix rows before it could ship.
- [x] Scope-root directory names moved into `domain`. `pm_ai.storage` must decide which scope a path is in and may not import `pm_ai.platform`; `paths.py` now reads them from the one definition.
- [x] `pm_ai/domain/storage_tiers.py` — `GITIGNORE_REQUIRED` retired in favour of `requires_git_exclusion(scope_kind, artifact)`.
- [x] `pm_ai/storage/service.py` — the capture guard consults it per scope, **memoised per `(scope, artifact)` for the daemon's lifetime**. Without that, marking the enclaves gitignored would spawn `git` on every append to a team-member or personal event log — the write-in-a-loop case AD-43 named as the condition to revisit on. Recorded only on success, so a refusal fires again next time.
- [x] `pm_ai/storage/crypto.py` — new. `is_encrypted(path)` plus `scope_of(path)`, failing closed on anything undeclared.
- [x] `tests/architecture/test_encryption_policy.py` — new, 25 tests, every path from the resolver.
- [x] `tests/conftest.py` — `EXPECTED_SKIPS` 30 → 29, which the ratchet demanded in this same commit.

**Acceptance Criteria:**
- Given `uv run pytest`, then `test_ad6_markdown_is_never_encrypted` passes and the skip count falls from 30 to 29. Result: 285 passed, 29 skipped.
- Given a `File` or `Collection` constructed without an encryption answer, then construction raises rather than defaulting.
- Given the three `transcripts/` declarations, then all three classify as encrypted. Asserted directly rather than guaranteed structurally: per-scope keying is what makes `meetings/` expressible, and the same freedom lets the three captures drift, so a test holds them together.
- Given an undeclared path, then `is_encrypted` returns `True`.
- Given `tests/architecture/test_encryption_policy.py`, then no test contains a literal scope root (`~/.pm-ai`, `~/.manager-ai`, `.project-ai`): every path under test comes from `resolve(scope, artifact)`, so the suite tracks the layout instead of a snapshot of it.
- Given the `runtime` extra is not installed, then `pm_ai.storage.crypto` imports successfully and no test skips on a missing import.
- Given `uv run lint-imports`, then all 12 contracts hold.

## Design Notes

**Why declaring it on the node beats a classifier.** The classifier answers the same question the trees already answer for durability, from a second structure. Adding an artifact would mean editing the tree and remembering the classifier, and the failure mode of forgetting is a plaintext secret rather than a crash. Declaring encryption where the artifact is declared makes forgetting impossible: there is nowhere to add an artifact that does not ask.

**Two stale assertions to expect, and why fail-closed handles both.** The pre-written test asserts that `~/.pm-ai/private/event_telemetry.db` and `~/.pm-ai/private/chat_history/` are encrypted. Neither exists. `event_telemetry.db` was the original mistake — one file mixing operational state with rebuildable indexes — later split into `operational.db` and (as of 2026-08-27) `event_index.db` plus `commitment_index.db`; `chat_history/` is an early name for captures. A pure tree lookup would answer *no* for both and fail the test, which is exactly the wrong direction. Failing closed on an undeclared path satisfies both old assertions and the current names together, without reviving either name in a tree. Report the stale names rather than declaring them.

**One frozen row was edited on 2026-08-27, and only its name.** The user renamed the derived tier's single `derived.db` into `event_index.db` and `commitment_index.db`, one file per rebuilding job. The matrix row for it became two rows with the same answer, *no*, for the same reason. Nothing in this story's intent or boundaries moved: the artifact the row pointed at was renamed under it, and leaving the old name would have left the matrix asserting about a path no scope tree declares — where `is_encrypted` fails closed and would answer *yes*, contradicting the row.

**Why the undeclared answer is fail-closed rather than an exception.** `is_encrypted` is called on the write path. Raising would turn every unrecognised path into an outage; answering *encrypted* turns it into a file the PM cannot grep until someone declares it. The second failure is visible and recoverable; the first is an incident, and the third option — answering *plaintext* — is the leak.

## Verification

- `uv run pytest -q -rs` — expected: 29 skipped, with no remaining skip naming `pm_ai.storage.crypto`.
- **Turn the skip ratchet in this same commit.** `tests/conftest.py` pins `EXPECTED_SKIPS` and fails the run in *both* directions, so landing this story makes the suite red until the constant matches: set it to the new count (29, if the story-1 slices land in order). The failure prints after pytest's stats line and names the direction and the delta, so a wrong number tells you what to write. Leaving it stale is not an option the run permits — which is the point, since the alternative is a story that un-skips one test and silently adds another.
- `uv run python -c "import pm_ai.storage.crypto"` — expected: silent success.
- `uv run lint-imports` — expected: `Contracts: 12 kept, 0 broken.`
- Remove the encryption answer from one `transcripts/` declaration and confirm construction fails, then restore it.
