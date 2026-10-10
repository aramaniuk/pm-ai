---
title: 'The daemon renders every dashboard at 07:00 local'
type: 'feature'
created: '2026-10-10'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** CAP-9 promises the dashboard by 07:00 local and nothing produces it unattended: `pm-ai dashboard` renders once when a person runs it. The file is also declared hand-editable (Tier 1) while every render replaces it whole, so a scheduled render would erase a hand edit daily — Andrei settled this on 2026-10-09 (D4): the dashboard is a render product. And the Proactive Enablement section looks back a fixed 24 hours, which silently drops a weekend when no render ran.

**Approach:** Inside the daemon, after `9a`'s timers, one timer renders each dashboard scope at 07:00 in `display_timezone`, on the same temporary in-memory scheduler story 10a replaces. The dashboard moves to the disposable tier; every successful render records its instant per scope, and Proactive Enablement looks back to the last one. Renders run on the daemon's pool of workers (`4r`); slice `1r` lands before this one.

## Boundaries & Constraints

**Always:**
- **The render runs once per local day per scope that has a dashboard** — personal, then each watched project — at 07:00 in `display_timezone`. Whenever the render timer evaluates, at start or after any wait, a scope with no successful render since today's 07:00 is rendered at once if it is past 07:00; so a daemon started at 09:30, or a laptop asleep through the morning, renders on arrival. One scope failing does not stop the next.
- **`display_timezone` is read at every render**, not once at start, so a change takes effect at the next render. With it unset there is no 07:00: nothing is scheduled, the refusal is recorded for every dashboard scope, and `doctor` names the key. A fresh machine before `setup` therefore records `FAILING` for every scope at every evaluation — expected, and said so on the `renders` line.
- **Renders run on the daemon's pool of workers**, which `4r` owns with the lock that serializes every storage call from a worker; this slice starts no thread. At most one render per scope is in flight; a render due while a harvest runs is never lost.
- **Every successful render records its instant per scope** in the operational store, whether the daemon, a hand-run `pm-ai dashboard` or a `4r` job produced it — a signal the PM has already seen on the page is not news.
- **The dashboard is a render product (D4), in the disposable tier, because nothing in it is truth.** Its inputs are the scope's records, the last harvest failure `9a` recorded per instance, the live calendar read and the clock. Both copies move together: replaced whole on every render, no hand edit survives, never watched, no inputs declared to watch — a request always renders. A rebuild re-renders from whatever answers now and writes the project copy only when git confirms it is excluded, skipping it by name otherwise. Path, four sections and each copy's git exclusion are unchanged.
- **The render never checks whether a connector is healthy.** Where it needs that fact it reads what `9a` recorded; the `[ERROR]` line that would show it stays with stories 16 and 23.
- **A render that fails leaves yesterday's file in place** (the existing read-then-write rule); the failure and reason are recorded for that scope and `doctor` shows `FAILING` naming it. A render refused because another command holds the file fails that attempt naming the claim and is retried with `9a`'s waits (1, 2, 4 … minutes) until it succeeds or the next 07:00; a render failing on its own input waits for tomorrow. A calendar that cannot be read — a master key unreadable at render included — is not a failed render: the file is written with the existing "calendar could not be read" line, and the harvest's own failure is `9a`'s to record.
- **Proactive Enablement looks back to the last successful render of that scope**, or 24 hours when none is recorded, and the section says which (left to this slice by `23c`). The look-back is never longer than 7 days: a daemon off for weeks, or a clock stepped back, is clamped to 7 days and the section says so.
- **`pm-ai doctor` gains a `renders` line**, read from the operational store, contacting nothing: per dashboard scope the last render and any failure with its reason; `FAILING` when a render failed or none can be scheduled.
- **`pm-ai dashboard` by hand is otherwise unchanged**: it renders once and exits.
- **No new setting.** 07:00, the 24-hour fallback and the 7-day clamp are constants.

**Ask First:** Nothing. Recording a hand-run render's instant was a judgement call, stated above as decided.

**Never:**
- No durable queue or task manager — story 10a deletes the timer, with `9a`'s.
- No change to how a render reads the day's calendar (`23b`): meetings not yet held are read live and persisted nowhere (2026-09-07), so the render cannot take them from what was harvested.
- Never watch the dashboard file or render on a file change.
- No `pm-ai reindex` command — none exists; the move only makes the file eligible for one.
- No new dashboard line for a failing instance — the dashboard stories own that.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 07:00 arrives | personal and one project | both rendered, personal first; instants recorded | N/A |
| Started after 07:00 | 09:30, no render today | rendered at once; then next 07:00 | N/A |
| Started after a render | 09:30, today already rendered | waits for tomorrow | N/A |
| Asleep across 07:00 | wakes at 10:00 | rendered on wake | N/A |
| Day boundary | zone with DST | 07:00 is the zone's wall clock, as the dashboard's day already is | N/A |
| Zone changed | `display_timezone` edited while running | the next render uses the new zone | N/A |
| Render due during a harvest | both due | both run on the pool, storage calls serialized; neither lost | N/A |
| One scope fails | personal render fails, project due next | project file rewritten and recorded; only personal recorded failed | `doctor` `FAILING`, scope and reason |
| Render fails | malformed goals file | yesterday's file intact; failure recorded; tried again tomorrow | `doctor` `FAILING`, scope and reason |
| File busy | another command holds the dashboard | attempt fails naming the claim; retried after 1 min | recorded, cleared by the retry |
| Calendar unreadable | key unreadable, token stale | file written with the "calendar could not be read" line; harvest failure is `9a`'s | `doctor` `FAILING` via `9a` |
| No display zone | `display_timezone` unset | nothing scheduled; recorded for each scope at every evaluation | `doctor` `FAILING` naming the key |
| After a weekend | last render Friday 07:00 | signals since Friday 07:00; the section says so | N/A |
| Long gap | last render 3 weeks ago, or clock stepped back | signals of the last 7 days; the section says the window was clamped | N/A |
| First render ever | no render recorded | last 24 hours; the section says so | N/A |
| Hand-run dashboard | `pm-ai dashboard` at 22:00 | that render is recorded; 07:00 looks back to 22:00 | N/A |
| Tier move | both dashboard declarations | disposable: eligible for deletion and re-render, not a backup target; git-exclusion answers and file mode unchanged | N/A |
| Rebuild, exclusion unconfirmed | git cannot confirm the project copy is excluded | personal copy re-rendered; project copy skipped by name | N/A |

</frozen-after-approval>

## Code Map

Verified against `ddcb9aa` on 2026-10-10. Builds on `9a`'s `cadence.py` and `ticks.py`, `4r`'s pool and lock, `1r`'s claim; sibling specs on disk as `9a` lists them.

- `pm_ai/domain/scope_model.py:540`, `:733` -- the two `Tier.TRUTH` declarations to flip to `Tier.DERIVED` (Tier 3); `:164-192` `Tier`, `rebuildable`, `backed_up`; `pm_ai/domain/storage_tiers.py:405` `assert_reindex_safe`. `pm_ai/storage/` holds no `reindex.py`
- `pm_ai/storage/service.py:789-824` -- the git-exclusion check every write of an excluded artifact goes through (`requires_git_exclusion`, `_vcs.working_tree`, `_vcs.tracking`); a rebuild's project-copy write takes the same path; `:148-157` why a new table needs no version bump. New: `renders` (scope, last success, last failure, reason)
- `pm_ai/app/pipelines.py:253` `run_dashboard`, where every render path records its instant (so a `4r` job counts); `:315-323` the whole-file write; `:373-390` `_display_zone`, which reads `daemon.config` — the per-render read reloads it; `:429-473` the live day read, `UnreadCalendar` and `_one_failure`, which stay
- `pm_ai/core/rendering.py:187` `MESSAGE_WINDOW`, which becomes the fallback; `:247`, `:293` the two renderers, which gain a `since`; `:582-586` `_proactive_enablement`, `:643` `_no_signals`
- `pm_ai/core/config.py:224` `display_timezone`
- `pm_ai/app/entry.py:261` `_dashboard` (the hand-run path, which records too), `:985` `_diagnose` (append the probe after `9a`'s)
- `pm_ai/ports/__init__.py:561-621` `StoragePort`
- `tests/core/test_project_rendering.py:226` -- asserts the project renderer's exact parameter list, which grows by `since`; `tests/architecture/test_paths.py:530` Tier 1 inside Tier 3; `test_encryption_policy.py:136-137` the git-exclusion pair the move must keep; `test_domain_invariants.py:863-878` the tier-set assertions; `tests/slice/test_dashboard_slice.py:350`, `:380` the render's no-cursor rows, still true; `tests/core/test_rendering_sections.py:658` the window row; `tests/conftest.py:58-60` wants skip deltas, and this slice's is zero
- `_bmad-output/specs/spec-pm-ai/stories/23c-proactive-enablement-from-messages.md:36` -- the window left to this slice
- `_bmad-output/implementation-artifacts/deferred-work.md:666` -- the entry this slice closes

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/scope_model.py` -- flip both `daily_dashboard.md` declarations to `Tier.DERIVED` and say why on the node -- D4
- [ ] `pm_ai/core/cadence.py` -- next 07:00 in a zone, whether a scope's render is due, the 7-day clamp of `since` -- the arithmetic 10a keeps
- [ ] `pm_ai/ports/__init__.py`, `pm_ai/storage/service.py` -- `renders` table, its read and write -- Tier 2 through the single writer
- [ ] `pm_ai/core/rendering.py` -- both renderers take `since`; the section and `_no_signals` state "since the last render at …", "the last 24 hours (no earlier render recorded)" or "the last 7 days (the earlier render was older)"; update `test_project_rendering.py:226` -- 23c's open window
- [ ] `pm_ai/app/pipelines.py` -- `run_dashboard` re-reads `display_timezone`, passes `since` from the last render, records the render after the write, and records a failed attempt naming the claim on `ArtifactBusy` -- every path records
- [ ] `pm_ai/app/ticks.py` -- the render timer attached in `4e`'s lifespan, each render submitted to `4r`'s pool; one scope's failure does not stop the next; the busy retry on `9a`'s waits; nothing scheduled without a display zone -- the 07:00 run
- [ ] `pm_ai/platform/doctor.py`, `pm_ai/app/entry.py` -- the `renders` probe, appended after `9a`'s -- doctor contacts nothing
- [ ] `tests/slice/test_morning_render.py`, `tests/core/test_rendering_sections.py`, `tests/architecture/` tier assertions, the doctor suite -- one test per matrix row with an injected clock; the one-scope-fails row runs a failing personal render ahead of a project render -- the matrix is the contract
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark `:666` resolved -- the debt is on record

**Acceptance Criteria:**
- Given a dashboard scope and a clock stepped past 07:00 local, when the render timer evaluates, then the file is rewritten, the instant recorded, and the next evaluation before tomorrow's 07:00 writes nothing.
- Given a personal render that fails and a project render due after it, when the timer evaluates, then the project file is rewritten and only the personal scope is recorded failed.
- Given the flip, when `uv run pytest` runs, then everything passes with the skip count unchanged (this slice names no skipped module), and `ARTIFACT_TIER["daily_dashboard.md"]` is `Tier.DERIVED`.

## Design Notes

The disposable tier is AD-3's Tier 3. The `renders` table is Tier 2, created by `_SCHEMA` on open with no version bump. Renders use `4r`'s pool and lock for the reason `9a` gives (the live calendar read is synchronous HTTP); `9a`'s one-at-a-time rule is among harvests, so a render and a harvest may overlap, each storage call serialized by the lock, and a render reads whatever was persisted when it ran.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; skip count unchanged from the baseline
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
