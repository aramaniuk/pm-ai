---
title: 'Dashboard renderer'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** CAP-9 requires `~/.manager-ai/memory/daily_dashboard.md` with exactly four headed sections. The file is declared in two scope trees (`scope_model.py:540,711`) and nothing writes it. `pm_ai.core.rendering` does not exist, and a pre-written test imports it (`test_domain_invariants.py:216`) to assert AD-25 — that project-scope rendering has no code path to the personal store — so the privacy wall has gone unverified since it was written.

**Approach:** Add `pm_ai/core/rendering.py`: a pure function from meetings, log entries, goals and an instant to the four-section Markdown, plus the scope-datasource declaration AD-25's test calls. `23b` supplies the inputs and performs the write.

## Boundaries & Constraints

**Always:**
- **A pure function.** `render_dashboard(meetings, entries, goals, now) -> str`. No I/O, no clock read, no storage — `core` is I/O-free and the injected clock is the rule 1b established. This makes every section golden-file testable.
- **Project-scope rendering cannot reach the personal store** (AD-25). `project_scope_datasources(project=...)` declares what a project render may open, and the pre-written test asserts no personal-scope path appears in it. The wall is a code path that does not exist, not a tag checked at runtime.
- **Nothing is invented.** A section with no data states the computed reason — "No meetings on your calendar today", "No strategic goals declared" — never a cheerful placeholder. A claim the code did not compute may not appear in the output.
- **All four headings always render**, in CAP-9's order, so the file's shape is stable for a human skimming it at 07:00 and for anything that later parses it.
- **3-Tier means `GoalHorizon`**, not `GoalDomain`. Both are three-valued, and CAP-9 says only "3-Tier"; the section is about when milestones land, and `GoalHorizon` is documented as the planning-breakdown axis while `GoalDomain` is the `<Tier>` in `[Strategic Alignment: <Tier>]`.

**Ask First:** Nothing. The two open decisions this section touches — the horizon reading, and Leadership Notes staying empty — are recorded as deviations in `prototype-path-2026-09-01.md` and already approved.

**Never:** No model call of any kind: the renderer is deterministic and the prototype path puts no model in the path. No file write — `23b`. No scheduling — `9a`. No commitment data: nothing produces commitments yet, and a section implying otherwise would be invented evidence.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Full day | three meetings, message entries, goals at all horizons | four sections, all populated; meetings ordered by start | N/A |
| No meetings | empty meeting list | Time-Critical states no meetings today | N/A |
| No goals | empty register | 3-Tier states no goals declared and names the file to author | N/A |
| No message entries | empty log | Proactive Enablement states no signals in the window | N/A |
| Leadership Notes | any input | states that synthesis is not enabled in this build | N/A |
| One horizon empty | no medium-horizon goals | that tier stated as empty; the other two render | N/A |
| Meeting already ended today | start earlier today, now past its end | not listed as time-critical | N/A |
| Project-scope datasources | `project="alpha"` | no path containing the personal scope | asserted by the pre-written test |
| Markdown-unsafe title | a meeting title containing `#` or a pipe | escaped; the section structure survives | N/A |
| Naive `now` | tz-naive instant | refused at the boundary, as `clocks` does | `ValueError` |

</frozen-after-approval>

## Code Map

- `pm_ai/core/rendering.py` -- new, the whole of this story
- `tests/architecture/test_domain_invariants.py:216-228` -- the pre-written AD-25 test and the exact `project_scope_datasources` signature it calls
- `pm_ai/domain/scope_model.py:540,711` -- the two `daily_dashboard.md` declarations; CAP-9 names the personal one
- `pm_ai/domain/goals.py:33-39` -- `GoalHorizon`, the three tiers
- `pm_ai/core/meeting_records.py` -- `for_day`, `11a`'s query this section consumes
- `pm_ai/core/event_log.py:52-70` -- `EventLog.read`, bounded on `ingested_at`, which the caller uses
- `pm_ai/domain/clocks.py` -- the precedent for refusing a non-UTC instant once

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/rendering.py` -- add `render_dashboard(...)`, `project_scope_datasources(...)`, the four section renderers and the four heading constants
- [ ] `tests/core/test_rendering.py` -- one test per matrix row, plus a golden file for the full-day case

**Acceptance Criteria:**
- Given the suite runs, then `test_ad25_project_rendering_cannot_open_the_personal_store` passes rather than skipping, and the skip count falls by one.
- Given every combination of empty inputs, when rendered, then all four headings are present and no section body is empty — each states its reason.
- Given the full-day golden case, when rendered twice with the same inputs, then the output is byte-identical — no clock read, no ordering nondeterminism.
- Given the rendered output, then it contains no phrase asserting a fact absent from the inputs; the empty-section strings name a file or a window, never a state of the world.

## Design Notes

The empty-section strings are the substance of this story, not boilerplate. "No strategic goals declared — author `strategic_goals.md`" is a computed claim plus a remediation. "All clear!" is a claim about the world that the renderer has no basis for and would be false on a day with an unread inbox. CAP-9's "no empty section" clause is met in the sense that no section is *blank*; it is knowingly not met for Leadership Notes in the sense CAP-9 intended, which is recorded as a deviation rather than papered over.

`project_scope_datasources` looks like an odd companion to a render function, and it is there because the pre-written test defines the shape: AD-25 is enforced by asking the renderer what it may open and checking the personal scope is absent. Declaring the datasource list makes the wall inspectable rather than a property of whichever code path happens to run.

## Verification

**Commands:**
- `uv run pytest tests/core/test_rendering.py -q` -- expected: all matrix rows pass
- `uv run pytest tests/architecture/test_domain_invariants.py::test_ad25_project_rendering_cannot_open_the_personal_store -q` -- expected: 1 passed, 0 skipped
- `uv run pytest -q` -- expected: no new failures; skip count one lower than baseline
- `uv run lint-imports` -- expected: `core` imports no I/O client
