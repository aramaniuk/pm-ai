---
title: 'Project artifacts go machine-local'
type: 'refactor'
created: '2026-09-03'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** four project artifacts are declared committed — `memory/event_log/`, `memory/commitments_log.md`, `memory/daily_dashboard.md` and `memory/meetings/`, all `gitignored=False` — so two machines on one repository write the same files and a `git pull` rewrites Tier 1 underneath the local Tier-2 and Tier-3 state derived from it. Concretely: both machines append to `2026-09.md`, so every pull conflicts or interleaves; `_append_batch` publishes the whole file through `os.replace` (`service.py:1002`), so a local append clobbers lines pulled in between; the `seen` dedup set is per-machine, so the next harvest re-appends what a teammate already logged; sealed segments are declared immutable (`2g`) and a merge rewrites them; and `2f`'s "file order is arrival order, and it is the only exact one" is simply false after a merge. `daily_dashboard.md` is replaced whole daily by each machine, and `commitments_log.md` is append-only with the same clobber window.

**Approach:** flip those four and `memory/` itself to `gitignored=True` in the project tree. `rules/` and `skills/` stay shared.

## Boundaries & Constraints

**Always:**
- **The project tree only.** `GITIGNORED` is a mapping per `ScopeKind` (`scope_model.py:994`), so the personal tree — which Deployment tells the PM to keep as a private repository — is untouched.
- **`memory/` follows its children.** With all four gitignored, nothing beneath it is committed, so the generated `.gitignore` carries one rule rather than four and the declaration matches reality. A future project artifact that genuinely should be shared therefore cannot live under `memory/`, which is accepted: `rules/` and `skills/` are the sharing surfaces.
- **This is a declaration change, not a mechanism change.** The flags are the whole slice; every consequence derives from `GITIGNORED`, per AD-44. No second list, and no code that special-cases these four.
- **`rules/` and `skills/` stay shared, deliberately.** They are human-authored, hand-edited, and nothing local is derived from them — which is what sharing is for.
- **It must land before `4k`.** `4k` generates a project's `.gitignore` from `GITIGNORED`, so a project onboarded before this slice gets a file missing four rules — and the moment this slice lands, every write to those artifacts inside that repository refuses, because `_assert_git_excludes` (`service.py:697-717`) now guards them. Ordering is the migration.

**Ask First:** Nothing.

**Never:** No new artifact and no new tier. No change to the personal, application or people trees. No `.gitignore` generation — that is `4k`. No migration tooling: nothing is deployed, so no repository holds these files yet, and the ordering rule above is what keeps that true.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Derived set | after the change | `GITIGNORED[PROJECT]` holds the four plus `memory/`, alongside `transcripts/` | N/A |
| Other trees | after the change | `GITIGNORED` for personal, application and people is byte-identical | N/A |
| Write inside a repo with the rule | `.gitignore` carries `memory/` | the write proceeds | N/A |
| Write inside a repo without the rule | a project onboarded before this slice | refused — `_assert_git_excludes` now guards it | `UnprotectedCaptureDir` |
| Write outside any repository | a plain project directory | proceeds: `working_tree` returning `None` is an answer, not an unanswered question (`service.py:714-717`) | N/A |
| Write where git cannot answer | the binary absent, or git errors | refused, as it already is for `transcripts/` | propagated |
| Exclusion sets stay disjoint | the three-set guard | `GITIGNORED`, `RETENTION_MANAGED` and `DIAGNOSTIC_ONLY` remain pairwise disjoint, and every gitignored artifact still names a node (`storage_tiers.py:315-321`) | `InconsistentModel` |
| `rules/` and `skills/` | after the change | still committed, still `gitignored=False` | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/scope_model.py:PROJECT_TREE` -- the five declarations that flip; `memory/`'s children are `daily_dashboard.md`, `commitments_log.md`, `meetings` and `event_log`
- `pm_ai/domain/scope_model.py:994` -- `GITIGNORED = _answering_yes(EXCLUSION)`, the derivation; nothing else needs touching
- **`pm_ai/domain/scope_model.py:PEOPLE_TREE` -- the precedent, already shipped.** `GITIGNORED[PEOPLE]` is exactly `memory/`, `memory/event_log/`, `memory/meetings/`, `transcripts/` — a `Dir` plus children, all Tier 1. So this shape is established rather than invented, and Tier 1 being gitignored is already normal: `disclosure.md` and `connectors/` both are
- `pm_ai/storage/service.py:697-717` -- `_assert_git_excludes`, which begins guarding four more artifacts
- `pm_ai/domain/storage_tiers.py:315-321` -- the guard that every gitignored artifact names a node in its scope
- `tests/architecture/test_capture_guard.py` -- the existing coverage of this mechanism, whose fixtures decide how much moves

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/scope_model.py` -- flip `gitignored` to `True` on the project tree's `memory/`, `daily_dashboard.md`, `commitments_log.md`, `meetings` and `event_log` -- five declarations, nothing else
- [ ] `tests/architecture/test_capture_guard.py` -- update any case that writes a now-guarded project artifact inside a repository without the rule -- the guard's reach grows, and a test that passed by writing an unguarded artifact will now refuse
- [ ] `tests/architecture/test_encryption_policy.py` or its scope-model sibling -- assert the derived `GITIGNORED[PROJECT]` set explicitly, and that the other three trees are unchanged

**Acceptance Criteria:**
- Given `GITIGNORED[ScopeKind.PROJECT]`, then it holds `memory/`, `memory/event_log/`, `memory/commitments_log.md`, `memory/daily_dashboard.md`, `memory/meetings/`, `transcripts/` and `transcripts/temp/` — asserted as a set, because the point of the slice is a derived answer and a spot-check on one member would pass while another stayed committed.
- Given `GITIGNORED` for the personal, application and people scopes, then each is unchanged — asserted, because the exclusion answer is declared per tree and the obvious implementation edits a shared node.
- Given a project directory that is not a git repository, then a write to its `event_log/` proceeds — the guard must not turn "no repository" into a refusal, which is the one direction that would break every non-git project.
- Given `uv run pytest -q`, then the suite passes with the guard's reach grown — the capture-guard fixtures are updated in this slice's commit rather than left for the next.
- Given the three exclusion sets, then they stay pairwise disjoint and every gitignored artifact still names a node — `storage_tiers.py`'s own guards, which a flag flip must not break.

## Spec Change Log

- **2026-09-03, written from the human's decision Q6/Q6b.** The four artifacts were committed, and the reasoning for changing that is recorded in `decisions-2026-09-03.md`: a `git pull` rewrites Tier 1 under the local Tier-2 and Tier-3 state derived from it, and the mechanisms that make Tier 1 trustworthy on one machine — single writer, one open segment, sealed immutability, arrival order, a per-machine dedup set — are each false under a merge. `event_log/` was raised first; the other three have the identical failure and were included on the same grounds. `memory/` follows its children so the generated `.gitignore` is one rule.
  **The cost is deliberate and worth restating here:** a teammate's harvested events, meetings and commitments never reach this machine, so CAP-10's retrospective counts and every project-level aggregation become per-machine. Cross-user sharing of project *rules and skills* is kept; cross-user visibility of project *events* is dropped.
  **AD-3, AD-38 and `scope-model.md` still describe the project scope as the committed one.** They are skill-derived, so they are corrected by re-running the architecture skill, not by editing this slice — recorded so the divergence is not mistaken for an oversight.

## Verification

**Commands:**
- `uv run pytest tests/architecture -q` -- expected: the capture guard, the scope-model invariants and the exclusion-set guards all pass
- `uv run pytest -q` -- expected: no new failures
- `uv run python -c "from pm_ai.domain.scope_model import GITIGNORED; from pm_ai.domain.identity import ScopeKind; print(sorted(GITIGNORED[ScopeKind.PROJECT]))"` -- expected: the seven keys the first criterion names
