---
title: 'Dashboard renderer'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 1
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

- **A project render's 3-Tier section states the wall, not the goals.** Goals exist only in the personal scope and AD-25 forbids a project render reaching it, so a project dashboard's 3-Tier section can never be filled. Its text is therefore *"Strategic goals are personal-scope (AD-25) — render the personal dashboard to see them"*: a computed claim about the boundary. "No strategic goals declared" would be a claim about the world, false whenever the personal file has goals.
- **Every interpolated string is escaped**, not only meeting titles. Goal titles and actor names are hand-authored or provider-supplied and reach the same Markdown.

**Ask First:** The display timezone that owns the day boundary. `for_day(day, *, tz)` takes it from `11a`, and `render_dashboard` must agree — a 23:30-local meeting is tomorrow in UTC, so this decides which meetings a 07:00 dashboard shows. It has no owner in any story and is not a detail: it is the difference between a correct dashboard and one that silently drops the PM's evening.

**Never:** No model call of any kind: the renderer is deterministic and the prototype path puts no model in the path. No file write — `23b`. No scheduling — `9a`. No commitment data: nothing produces commitments yet, and a section implying otherwise would be invented evidence.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Full day | three meetings, message entries, goals at all horizons | four sections, all populated; meetings ordered by `(start, meeting_id)` | N/A |
| Aware but non-UTC `now` | a `+02:00` instant | refused, as `clocks._assert_comparable` refuses a non-zero offset | `ValueError` |
| Project render | `project="alpha"` | 3-Tier states the AD-25 wall; no goals source opened | N/A |
| Project id colliding with a scope | `project="personal"`, `project=""` | refused — the id must never compose into a path matching the personal tree | `ValueError` |
| Meeting in progress | started before `now`, not ended | listed as time-critical | N/A |
| Meeting spanning midnight | started yesterday, ends today | listed | N/A |
| All of today's meetings ended | `for_day` returned meetings, all past | "All N of today's meetings have ended" — **not** "no meetings today" | N/A |
| Markdown-unsafe goal title | a pipe or a newline in hand-authored text | escaped; the section structure survives | N/A |
| Entry with no `ingested_at` | a pre-2e entry | excluded from the window and counted as unplaceable; never dated from `now` | N/A |
| Non-message log categories | `SelfActionType` entries in the same log | ignored; the section states which categories it reads | N/A |
| Personal goal during a project render | a personal-scope goal in the register | refused — a scope-mismatched goal never renders | `ValueError` |
| No meetings | `for_day` returned nothing | "No meetings on your calendar today" | N/A |
| No goals | empty register | 3-Tier states no goals declared and names the file to author | N/A |
| No message entries | empty log | Proactive Enablement states no signals in the window | N/A |
| Leadership Notes | any input | states that synthesis is not enabled in this build | N/A |
| Proactive Enablement in wave 1 | no `MESSAGE_POSTED` entries exist yet | states no message signals until `33d` supplies them — the **second** knowingly empty section | N/A |
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
- [ ] `tests/conftest.py` -- lower `EXPECTED_SKIPS` by one **in this slice's commit** -- the ratchet fails when skips fall below the baseline (`conftest.py:88-104`)
- [ ] `tests/core/test_rendering.py` -- one test per matrix row, plus a golden file for the full-day case. The full-day fixture is **forward-looking**: wave 1 produces no message entries, so that row exercises `23c`'s state

**Acceptance Criteria:**
- Given `project_scope_datasources(project="alpha")`, then it is **non-empty**, no entry names a member of `PERSONAL_SUBJECT_ARTIFACTS`, and every entry is beneath that project's tree. The pre-written test cannot carry this: its only filter is `"manager-ai" in s or "personal_analytics" in s` (`test_domain_invariants.py:222`), so an empty list passes and — worse — a project render declaring the personal `strategic_goals.md` passes verbatim, since neither substring occurs in that filename. The shape to follow is `test_ad38_project_scope_is_the_only_committed_scope` (`:736`).
- Given the suite runs, then `test_ad25_project_rendering_cannot_open_the_personal_store` passes rather than skipping, and `EXPECTED_SKIPS` is lowered in the same commit.
- Given every combination of empty inputs, when rendered, then all four headings are present and no section body is empty — each states its reason.
- Given the full-day golden case, when rendered twice with the same inputs, then the output is byte-identical — no clock read, no ordering nondeterminism.
- Given the rendered output, then it contains no phrase asserting a fact absent from the inputs; the empty-section strings name a file or a window, never a state of the world.

## Spec Change Log

- **2026-09-02, multi-lens review.** Four findings, three of which produce false output.
  **The project-scope dashboard was incoherent.** Goals are personal-only and AD-25 forbids a project render reaching them, so a project 3-Tier section is structurally always empty — and its empty string, "No strategic goals declared", is a claim about the world that is false whenever the personal file has goals, breaking this spec's own "nothing is invented" rule. The alternative implementation reads the personal file and breaches AD-25 in the very slice whose test claims to prove the wall. The section now states the boundary instead.
  **The AD-25 criterion could not catch the leak it exists for.** The pre-written test filters for two substrings, neither of which appears in `strategic_goals.md`; and because it asserts over a list comprehension, an empty list passes, so "the skip count falls by one" was satisfied by a stub. Replaced with a positive assertion against `PERSONAL_SUBJECT_ARTIFACTS` and the project tree.
  **"No meetings on your calendar today" was false by mid-afternoon.** Ended meetings are excluded from Time-Critical, so a day whose meetings had all finished rendered the no-meetings string. Now two distinct computed statements. The ended-meeting filter is also an unrecorded deviation from `prototype-path-2026-09-01.md`, which says "`meetings/` where `start` falls today"; recorded now.
  **Wave 1 has two permanently empty sections, not one.** Proactive Enablement reads `MESSAGE_POSTED`, which arrives with `33d` in wave 2 — `33b`'s `emits()` is exactly `{CALENDAR_EVENT_HELD}`. Both this spec and the design doc claimed one honest gap. Recorded as the second, and the full-day golden fixture is labelled forward-looking since wave 1 cannot produce that state.
  **Ask First was "Nothing" and should not have been.** The display timezone owning "today" has no owner in any story, and leaving it implicit meant whichever of this slice and `11a` was written first decided which meetings a 07:00 dashboard shows.
## Design Notes

The empty-section strings are the substance of this story, not boilerplate. "No strategic goals declared — author `strategic_goals.md`" is a computed claim plus a remediation. "All clear!" is a claim about the world that the renderer has no basis for and would be false on a day with an unread inbox. CAP-9's "no empty section" clause is met in the sense that no section is *blank*; it is knowingly not met for Leadership Notes in the sense CAP-9 intended, which is recorded as a deviation rather than papered over.

`project_scope_datasources` looks like an odd companion to a render function, and it is there because the pre-written test defines the shape: AD-25 is enforced by asking the renderer what it may open and checking the personal scope is absent. Declaring the datasource list makes the wall inspectable rather than a property of whichever code path happens to run.

## Verification

**Commands:**
- `uv run pytest tests/core/test_rendering.py -q` -- expected: all matrix rows pass
- `uv run pytest tests/architecture/test_domain_invariants.py::test_ad25_project_rendering_cannot_open_the_personal_store -q` -- expected: 1 passed, 0 skipped
- `uv run pytest -q` -- expected: no new failures; skip count one lower than baseline
- `uv run lint-imports` -- expected: `core` imports no I/O client
