---
title: 'Dashboard sections'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** CAP-9 requires `~/.manager-ai/memory/daily_dashboard.md` with exactly four headed sections — Time-Critical Activities, Proactive Enablement, 3-Tier Strategic Milestones, Leadership Notes — and `pm_ai.core.rendering` does not exist. Nothing turns meetings, log entries and goals into that text.

Split from the original `23a` on 2026-09-02 at the sizing gate: what the dashboard *says* is one concern; what a project-scope render may *open* is a privacy boundary with its own pre-written test, now `23d`.

**Approach:** `pm_ai/core/rendering.py` — a pure function from inputs and an instant to the four-section Markdown.

## Boundaries & Constraints

**Always:**
- **A pure function.** `render_dashboard(meetings, entries, goals, now, *, tz) -> str`. No I/O, no clock read, no storage — `core` is I/O-free and the injected clock is the rule 1b established. Every section is golden-file testable.
- **Nothing is invented.** A section with no data states the computed reason. A claim the code did not compute may not appear in the output — "No meetings on your calendar today" names a query result; "All clear!" names a state of the world nothing measured.
- **All four headings always render**, in CAP-9's order, so the file's shape is stable for a human skimming it at 07:00 and for anything that later parses it.
- **3-Tier means `GoalDomain`** — `Project`, `Team`, `Personal`. Settled by the source, against an earlier draft of this clause: `prd.md:63` names `strategic_goals.md` as "3-Tier Goals (Project, Team, Personal Career Goals)", and `prd.md:424` says the domain "is what a goal is *about*, and it is the `<Tier>` in the alignment tag, matching §2.1's '3-Tier Goals'". `alignment_tag`'s docstring (`goals.py:99-104`) was right all along; the word "Milestones" in the section title is what misled this spec.
- **Every interpolated string is escaped** — goal titles and actor names are hand-authored or provider-supplied and reach the same Markdown as meeting titles.
- **Ordering is total**: meetings by `(start, meeting_id)`, so a re-render is byte-identical.

**Ask First:** Nothing. The display timezone was decided on 2026-09-03: it is `config.toml`'s fourth key, `display_timezone`, added by `4g`. `run_dashboard` reads it from the loaded `Config` and passes it to both `render_dashboard` and `for_day`, so the two cannot disagree.

**Never:** No project render — `render_project_dashboard` is `23d`'s, a separate function with its own sources and sections, so this one cannot be handed personal-scope data by mistake. No model call of any kind — the renderer is deterministic and the prototype path puts no model in the path. No file write (`23b`). No scheduling (`9a`). No scope-boundary logic (`23d`). No commitment data: nothing produces commitments yet, and a section implying otherwise would be invented evidence.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Full day | three meetings, message entries, goals at all horizons | four sections, all populated; meetings ordered by `(start, meeting_id)` | N/A |
| No meetings | `for_day` returned nothing | "No meetings on your calendar today" | N/A |
| All of today's meetings ended | meetings returned, all past | "All N of today's meetings have ended" — **not** the no-meetings string | N/A |
| Meeting in progress | started before `now`, not ended | listed as time-critical | N/A |
| Meeting spanning midnight | started yesterday, ends today | listed | N/A |
| No goals | empty register | 3-Tier states no goals declared and names the file to author | N/A |
| Goals file present but empty | register present-and-empty | states no goals **declared**, not "author the file" — it exists | N/A |
| One domain empty | no `Team` goals | that tier stated as empty; the other two render | N/A |
| Timezone unset | `Config().display_timezone` | **refused** — a day boundary may not be assumed, and defaulting to UTC is the silent wrong answer the key exists to prevent | `ValueError` |
| No message entries | empty log | Proactive Enablement states no signals in the window | N/A |
| Proactive Enablement in wave 1 | `MESSAGE_POSTED` does not exist yet | states no message signals until `33d` supplies them — the **second** knowingly empty section | N/A |
| Leadership Notes | any input | states that synthesis is not enabled in this build | N/A |
| Aware but non-UTC `now` | a `+02:00` instant | refused, as `clocks._assert_comparable` refuses a non-zero offset | `ValueError` |
| Markdown-unsafe text | a pipe or newline in a goal title, actor name or meeting title | escaped; the section structure survives | N/A |
| Entry with no `ingested_at` | a pre-2e entry | excluded from the window and counted as unplaceable; never dated from `now` | N/A |
| Non-message log categories | `SelfActionType` entries in the same log | ignored; the section states which categories it reads | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/core/rendering.py` -- new; the four section renderers and the heading constants
- `pm_ai/domain/goals.py:33-39` -- `GoalHorizon`, the three tiers
- `pm_ai/core/meeting_records.py` -- `for_day(day, *, tz)`, `11a`'s query this section consumes
- `pm_ai/core/event_log.py:52-70` -- `EventLog.read`, bounded on `ingested_at`
- `pm_ai/domain/clocks.py` -- the precedent for refusing a non-UTC instant once
- `pm_ai/domain/scope_model.py:540` -- the personal `daily_dashboard.md` CAP-9 names

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/rendering.py` -- add `render_dashboard(...)`, the four section renderers, the heading constants and the empty-section strings
- [ ] `tests/core/test_rendering_sections.py` -- one test per matrix row, plus a golden file for the full-day case. The full-day fixture is **forward-looking**: wave 1 produces no message entries, so that row exercises `23c`'s state

**Acceptance Criteria:**
- Given every combination of empty inputs, then all four headings are present and no section body is empty — each states its reason.
- Given the full-day golden case rendered twice with the same inputs, then the output is byte-identical — no clock read, no ordering nondeterminism.
- Given a day whose meetings have all ended, then the output says so rather than "No meetings on your calendar today" — a false claim by mid-afternoon, in the artifact whose stated purpose is that it never asserts what it did not compute.
- Given the rendered output, then every empty-section string names a file, a query or a window — never a state of the world.

## Spec Change Log

- **2026-09-03, amended against the second multi-lens review and the day's decisions.**
  **"3-Tier means `GoalHorizon`" was wrong** (B9/D-8), and it contradicted `alignment_tag`'s docstring, which cites the same spec section. The PRD settles it twice: the three tiers are `Project`, `Team`, `Personal` — the **domain**. Corrected, and a matrix row followed.
  **The verification block could not pass** (C1). Creating `pm_ai/core/rendering.py` ends the skip on `test_ad25_project_rendering_cannot_open_the_personal_store`, whose subject was `23d`'s deliverable — verified by the review with a stub module: `AttributeError`, 1 failed, and the skip ratchet does not even fire because `conftest.py:78-80` returns early on failure. Under the two-renderer decision that gate now asserts `render_project_dashboard`'s **signature**, which is `23d`'s function, so the un-skip and its subject land together in `23d` rather than one slice ahead of the other.
  **The display timezone got a source.** It was this slice's `Ask First` and had no owner in any story; it is now `config.toml`'s fourth key via `4g`, read once by `23b` and passed to both consumers.
  **This slice is now explicitly the personal dashboard only.** The project render is a separate function in `23d`, which is what makes handing it personal-scope data impossible rather than merely forbidden.
  **One review claim did not hold, and is recorded as not applying.** B8 said this slice and `23b` were specified against a "degrades quietly to `UNALIGNED`" model and would crash on an absent goals file. Neither mentions `resolve` or `alignment_tag`: the 3-Tier section lists goals rather than resolving recommendations, so `UnresolvedGoal` is unreachable here. `22a`'s Intent was wrong; the knock-on was not.

- **2026-09-02, split at the sizing gate.** The original `23a` measured 2,377 tokens and carried both the section rendering and `project_scope_datasources`, the AD-25 scope wall. The wall is now `23d`. Recorded because the review had judged this a single concern spanning layers, and splitting it was a human's call; the seam chosen separates two different failure classes — text that misleads, and a privacy leak.
- **Inherited from the 2026-09-02 multi-lens review**, which found "No meetings on your calendar today" false by mid-afternoon; that wave 1 leaves *two* sections permanently stating a reason rather than one, since Proactive Enablement reads `MESSAGE_POSTED` and `33d` supplies it in wave 2; and that Ask First was "Nothing" when the display timezone owning "today" had no owner in any story.

## Design Notes

The empty-section strings are the substance of this slice, not boilerplate. "No strategic goals declared — author `strategic_goals.md`" is a computed claim plus a remediation. "All clear!" is a claim about the world the renderer has no basis for and would be false on a day with an unread inbox.

CAP-9's "no empty section" clause is met in the sense that no section is *blank*. It is knowingly not met, in the sense CAP-9 intended, for Leadership Notes and — in wave 1 — Proactive Enablement. Both are recorded as deviations rather than papered over.

## Verification

**Commands:**
- `uv run pytest tests/core/test_rendering_sections.py -q` -- expected: all matrix rows pass
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: `core` imports no I/O client
