---
title: 'The project dashboard is its own renderer'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `daily_dashboard.md` is declared in the project tree as well as the personal one (`scope_model.py:711`), and `23a` builds one renderer whose caller chooses what to pass it. So a project render is one line away from writing the PM's personal career goals into a project artifact — `goals = personal_register` in the wrong branch — and AD-25 requires the privacy charter be "a wall, not a remembered tag check". A pre-written test asserts it (`test_domain_invariants.py:214-227`) and has skipped since it was written.

**Approach:** `render_project_dashboard` is a **separate function** with its own sources and sections. It takes no goals parameter, so passing personal goals is not forbidden but impossible.

## Boundaries & Constraints

**Always:**
- **The wall is the signature.** `render_project_dashboard` accepts project-scope meetings, project-scope log entries and an instant. There is no goals parameter, no register, and no personal-scope input of any kind — so the leak has no expression rather than a rule against it.
- **Its sections are what its sources support, and CAP-9 does not bind it.** CAP-9's success criterion names `~/.manager-ai/memory/daily_dashboard.md` by path — *"exactly the four headed sections … and no empty section"* — which is the personal file. This render carries Time-Critical Activities from project meetings and Proactive Enablement from the project event log, and has no 3-Tier or Leadership Notes section to explain away.
- **Goals are absent by declaration, not by omission.** All three domains live in the personal `strategic_goals.md` (`scope_model.py:541-544`), a member of `PERSONAL_SUBJECT_ARTIFACTS` whose checked property is that no committed scope holds it — the scope model says outright that there is "no project-scope counterpart".
- **The two renderers share their section renderers, never their inputs.** Time-Critical and Proactive Enablement produce identical Markdown in both files given identical data; only the input set differs. Duplicating the section text is how two dashboards drift into two formats.
- **Same purity rules as `23a`.** No I/O, no clock read, no model, and the timezone arrives as `config.toml`'s `display_timezone` through `23b`.

**Ask First:** Nothing.

**Never:** No datasource list and no `project_scope_datasources`. That function was this slice's original approach and is not built: with a separate signature there is nothing to enumerate, and a list plus a test that remembers to check it is weaker than a parameter that does not exist. No goals section. No scope-boundary *logic* — the boundary is structural. No write (`23b`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Project day | two project meetings, project log entries | Time-Critical and Proactive Enablement, both populated | N/A |
| No project meetings | `for_day` returned nothing for that scope | "No meetings on this project's calendar today" | N/A |
| No project entries | empty project log | Proactive Enablement states no signals in the window | N/A |
| Proactive Enablement in wave 1 | `MESSAGE_POSTED` arrives with `33d` | states so — knowingly empty, as in `23a` | N/A |
| Identical data, both renders | the same meetings passed to each function | the Time-Critical section is byte-identical | N/A |
| A personal-scope meeting reaches it | a caller passes one | rendered as given — this function does not filter by scope; `11a`'s accessor reads one scope and `23b` passes what it read | N/A |
| Goals offered | a caller tries to pass a register | does not type-check | `arg-type` under mypy |
| Timezone unset | `Config().display_timezone` | refused, as in `23a` | `ValueError` |
| Markdown-unsafe text | a pipe or newline in a meeting title | escaped; the section structure survives | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/core/rendering.py` -- `23a`'s module; `render_project_dashboard` joins `render_dashboard` and reuses its section renderers
- `tests/architecture/test_domain_invariants.py:214-227` -- the AD-25 gate, retargeted by this slice
- `pm_ai/domain/scope_model.py:541-544,711,1052` -- `strategic_goals.md` personal-only and in `PERSONAL_SUBJECT_ARTIFACTS`; `daily_dashboard.md` in the project tree
- `pm_ai/domain/scope_model.py:994` -- `GITIGNORED`, which the project `daily_dashboard.md` joined on 2026-09-03, closing the git route this slice originally existed to block
- `pm_ai/core/config.py` -- `display_timezone`, arriving through `23b`
- `tests/conftest.py:38` -- `EXPECTED_SKIPS`, lowered here as the AD-25 gate stops skipping

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/rendering.py` -- add `render_project_dashboard(meetings, entries, now, *, tz)`, reusing `23a`'s section renderers -- no goals parameter, and that absence is the deliverable
- [ ] `tests/architecture/test_domain_invariants.py:214-227` -- retarget the AD-25 gate: assert through `inspect.signature` that `render_project_dashboard` accepts no goals parameter, and that it is not empty of assertions -- the current body greps `str(s)` over a returned list, which passes vacuously when the list is empty, as its own comment records nearly happening once
- [ ] `tests/conftest.py` -- lower `EXPECTED_SKIPS` **by one** in this slice's commit -- a delta, because `8d` lowers it by two and the ratchet fails in both directions (`conftest.py:81`)
- [ ] `tests/core/test_project_rendering.py` -- the matrix, including the byte-identical shared-section case

**Acceptance Criteria:**
- Given `inspect.signature(render_project_dashboard)`, then no parameter accepts a goal register — the wall, asserted as a signature rather than as a list, because a signature cannot drift silently and a list can.
- Given a fixture passing a goal register to `render_project_dashboard`, when mypy runs on it, then it reports `arg-type` — the same shape `8e` uses, verified there to work under an explicit path argument.
- Given the same meetings and entries passed to both renderers, then their Time-Critical sections are byte-identical — asserted, because two dashboards that drift into two formats is what sharing the section renderers prevents.
- Given the suite, then `test_ad25_project_rendering_cannot_open_the_personal_store` passes rather than skips, and its body would fail if the parameter were added back.
- Given `grep -rn "project_scope_datasources" pm_ai/`, then there is no match — the approach this slice carried until 2026-09-03 is not built.

## Spec Change Log

- **2026-09-03, rewritten: the wall became a signature.** The original approach was `project_scope_datasources(project=...)` — a list of what a project render may open, plus a test asserting nothing personal appears in it. The human replaced it: the two dashboards are **separate functions** with their own sources, sections and outputs. `render_project_dashboard` has no goals parameter, so the leak has no expression. Strictly stronger than a list and a test that remembers to check it, and the same move as `8e`'s unforgeable `Sanitized`.
  That dissolved three findings rather than fixing them. **A8** — `core.rendering` cannot resolve a project tree or raise `UnknownProject`, since it may not import `pm_ai.platform.paths` — is moot, because there is no path to resolve. **The vacuous-pass problem** in the gate is replaced by a signature assertion. And the slice's own `Ask First` about what the project 3-Tier section says is answered by the section not existing: CAP-9's four-section rule names the personal file by path, so nothing requires four sections here.
  **This slice's original motivation is also gone**, and it is worth recording why the slice survived anyway. Its Intent rested on the project `daily_dashboard.md` being committed — "the obvious way to fill a project dashboard's 3-Tier section writes the PM's personal career goals into a git-committed employer repository." Q6 made it gitignored on 2026-09-03, closing that route. What remains is AD-31's destination rule: personal material must not enter a prompt whose output is bound for a project artifact, and `rules/` and `skills/` are still committed.
  **`EXPECTED_SKIPS` is a delta**, not the absolute the review found order-coupled with `8d`.

## Verification

**Commands:**
- `uv run pytest tests/core/test_project_rendering.py tests/architecture/test_domain_invariants.py -q` -- expected: matrix passes and the AD-25 gate passes rather than skips
- `uv run pytest -q` -- expected: no new failures, `EXPECTED_SKIPS` one lower
- `uv run mypy` -- expected: clean
