---
title: 'The project-scope rendering wall'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** AD-25 says the privacy charter is a wall, not a remembered tag check: project-scope rendering must have no code path to the personal store. A pre-written test asserts it (`test_domain_invariants.py:214-227`) by asking the renderer what it may open — and has skipped since it was written, because `pm_ai.core.rendering` does not exist.

Worse, the test as written cannot catch the leak it exists for. Its only filter is `"manager-ai" in s or "personal_analytics" in s`, and `strategic_goals.md` contains neither substring — so a project render that declares the personal goals file passes verbatim. And because the assertion is over a list comprehension, an empty list passes too.

This matters concretely: `daily_dashboard.md` is declared in the project tree as well (`scope_model.py:711`), and it is **not** gitignored. Goals live only in the personal tree (`scope_model.py:544`, and `PERSONAL_SUBJECT_ARTIFACTS` at `:1052`, whose checked property is that no committed scope holds them). So the obvious way to fill a project dashboard's 3-Tier section writes the PM's personal career goals into a git-committed employer repository.

Split from the original `23a` on 2026-09-02 at the sizing gate.

**Approach:** `project_scope_datasources(project=...)` declares what a project render may open, and the project 3-Tier section states the boundary rather than making a claim about goals.

## Boundaries & Constraints

**Always:**
- **The wall is a code path that does not exist**, not a runtime check. Declaring the datasource list makes it inspectable; the test asks the renderer and the renderer answers with paths beneath one project tree.
- **A project render's 3-Tier section states the wall.** Its text is *"Strategic goals are personal-scope (AD-25) — render the personal dashboard to see them"*: a computed claim about the boundary. "No strategic goals declared" would be a claim about the world, false whenever the personal file has goals, and forbidden by `23a`'s nothing-is-invented rule.
- **No member of `PERSONAL_SUBJECT_ARTIFACTS` may appear** in a project render's datasources. That set exists to hold exactly this property (`scope_model.py:1044-1047`).
- **A project id can never compose into the personal tree.** `project="personal"` or `""` is refused rather than resolved, because the wall must not be defeatable by a name.
- **A scope-mismatched goal never renders.** If a personal-scope `Goal` reaches a project render, it is refused, not filtered silently — a silent filter and an empty register are indistinguishable to the reader.

**Ask First:** Nothing.

**Never:** No section text beyond the 3-Tier boundary statement — `23a` owns the four sections. No file write (`23b`). No change to the scope model: the declarations are correct and this slice reads them.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Project datasources | `project="alpha"` | non-empty, and every entry beneath that project's tree | N/A |
| Personal artifact requested | any member of `PERSONAL_SUBJECT_ARTIFACTS` | absent from the list; never resolvable from a project render | N/A |
| Project id names a scope | `project="personal"` | refused — the id must not compose into the personal tree | `ValueError` |
| Empty project id | `project=""` | refused | `ValueError` |
| Project 3-Tier section | a project render, personal goals exist | states the AD-25 boundary; makes no claim about goals | N/A |
| Personal goal in a project render | a personal-scope `Goal` in the register | refused, naming the goal and both scopes | `ValueError` |
| Personal render | the personal scope | goals are in scope and render normally — the wall is one-directional | N/A |
| Unregistered project | an id with no tree | refused, as `4d`'s registry requires | `UnknownProject` |

</frozen-after-approval>

## Code Map

- `pm_ai/core/rendering.py` -- `project_scope_datasources`, added beside `23a`'s section renderers
- `tests/architecture/test_domain_invariants.py:214-227` -- the pre-written AD-25 test, the exact signature it calls, and the two-substring filter that cannot catch the leak
- `tests/architecture/test_domain_invariants.py:736` -- `test_ad38_project_scope_is_the_only_committed_scope`, the assertion shape to follow
- `pm_ai/domain/scope_model.py:544,711,1044-1055` -- goals personal-only, `daily_dashboard.md` in both trees, and `PERSONAL_SUBJECT_ARTIFACTS` with its stated property
- `tests/conftest.py:42,88-104` -- `EXPECTED_SKIPS` and the ratchet

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/rendering.py` -- add `project_scope_datasources(project=...)` and the project 3-Tier boundary text
- [ ] `tests/conftest.py` -- lower `EXPECTED_SKIPS` by one **in this slice's commit** -- the ratchet fails when skips fall below the baseline
- [ ] `tests/core/test_render_scope_wall.py` -- the matrix, with the positive assertions the pre-written test cannot make

**Acceptance Criteria:**
- Given `project_scope_datasources(project="alpha")`, then it is **non-empty**, no entry names a member of `PERSONAL_SUBJECT_ARTIFACTS`, and every entry is beneath that project's tree. The pre-written test cannot carry this: an empty list passes it, and a project render declaring the personal `strategic_goals.md` passes it verbatim since neither of its two substrings occurs in that filename.
- Given the suite runs, then `test_ad25_project_rendering_cannot_open_the_personal_store` passes rather than skipping, and `EXPECTED_SKIPS` is lowered in the same commit.
- Given a project render while the personal goals file holds three goals, then the 3-Tier section states the boundary and the word "declared" does not appear — the false-claim case.
- Given `project="personal"`, then it is refused; the wall must not be defeatable by naming a project after a scope.

## Spec Change Log

- **2026-09-02, split at the sizing gate** from the original `23a` (2,377 tokens). The seam separates two failure classes: text that misleads a reader, and a privacy leak into a git-committed repository.
- **Inherited from the 2026-09-02 multi-lens review**, which found the AD-25 criterion unable to fail — an empty list satisfies it — and the project-scope dashboard incoherent: its 3-Tier section could only make a false claim or breach AD-25, in the very slice whose test claims to prove the wall.

## Design Notes

`project_scope_datasources` looks like an odd companion to a renderer, and it exists because the pre-written test defines the shape: AD-25 is enforced by asking the renderer what it may open and checking the personal scope is absent. Declaring the list makes the wall inspectable rather than a property of whichever code path happens to run.

The one-directional note matters. A personal render may read personal goals; that is the whole point of the personal hub. The wall stops the project direction only, which is why the rule is stated as a property of `project_scope_datasources` rather than of the renderer as a whole.

## Verification

**Commands:**
- `uv run pytest tests/core/test_render_scope_wall.py -q` -- expected: all matrix rows pass
- `uv run pytest tests/architecture/test_domain_invariants.py::test_ad25_project_rendering_cannot_open_the_personal_store -q` -- expected: 1 passed, 0 skipped
- `uv run pytest -q` -- expected: no new failures, ratchet satisfied
