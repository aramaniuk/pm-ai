---
title: 'Dashboard pipeline and pm-ai dashboard'
type: 'feature'
created: '2026-09-02'
status: 'ready-for-dev'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** After `23a` and `23d` the renderer is a pure function nothing calls, `11a` holds meetings, `22a` parses goals and `33b` fills both from Graph. Nothing joins them, and `daily_dashboard.md` is still never written. This is the last slice of wave 1 and the one that makes the prototype real.

**Approach:** Add `run_dashboard` to `pm_ai/app/pipelines.py` — read meetings, log entries and goals, render, write through `StorageService.write_artifact` — and expose it as `pm-ai dashboard`.

## Boundaries & Constraints

**Always:**
- **The pipeline lives in `app`.** It must touch storage, core and the scope model at once, which no layer below may do (AD-30) — the same reason `run_harvest` lives there.
- **The write goes through `write_artifact`**, which resolves the path from the declaration and decides sealing. `daily_dashboard.md` is a Tier-1 `File`, unencrypted, not gitignored (`scope_model.py:540`). Whole-file replacement is correct: this is a rendering, not a ledger, so the ledger refusal at `service.py:1039` does not apply and must not be worked around.
- **The default scope is personal**, because CAP-9 names `~/.manager-ai/memory/daily_dashboard.md`.
- **Which renderer runs is a branch on scope, and each gets only its own sources.** `23a`'s `render_dashboard` for personal, `23d`'s `render_project_dashboard` for a project. They are separate functions and the project one has no goals parameter, so this slice cannot hand it personal-scope data even by mistake (AD-25). There is no datasource list to consult — `project_scope_datasources` is not built.
- **The timezone is read once here and passed to every consumer.** `config.toml`'s `display_timezone` — **which makes `4g` a hard dependency of this slice, recorded 2026-09-15** — reaches both the renderer and the calendar read that selects the day, so the day boundary the dashboard shows and the day boundary its meetings were selected by cannot disagree. An unset zone is refused rather than defaulted to UTC.
- **This layer fetches the day's meetings, because the renderer may not.** Since 2026-09-07 an upcoming meeting is not persisted, so today's schedule is read live from `33b`'s fetch. `core` is I/O-free and `render_dashboard` is a pure function, so the call belongs here — the composition root reads, the renderer renders. A fetch that fails is passed through as a stated reason, not as an empty day: `8a`'s outcome and failure already tell ran-and-learned-nothing from ran-and-failed.
- **The render is deterministic given its inputs**, so re-running produces the same file. Nothing about the output depends on how many times it has run.
- **A missing input is a stated section, not a failure.** No goals file, no meetings and an empty log all produce a valid dashboard.
- **Read and render fully before writing anything.** `write_artifact` replaces whole, so opening the target first and discovering a malformed input afterwards destroys yesterday's dashboard. This is why `23a` returns a string rather than writing as it goes.
- **Exit codes come from `4c`'s table** — `3` for a refusal, `1` for an unexpected exception. This slice may not add to it.

**Ask First:** Nothing. Whether a render appends an event-log entry was decided on 2026-09-03: **it does not.** The discriminator is whether an action changes truth or projects it. A dashboard is fully derivable from Tier 1 plus a clock, so a line per day per scope records nothing a reader could not reconstruct, and CAP-10's own retrospective counts are decisions, proposals and commitments — a render is none of them. `2c`'s vocabulary stays closed.

**Never:** No scheduler and no daemon — `pm-ai dashboard` runs once and exits, and the CLI may hold no scheduler (AD-7). The 07:00 deadline in CAP-9 is knowingly unmet until `9a`. **No harvest triggered from here**, which since 2026-09-07 is a narrower rule than the old wording read. The day's calendar fetch is a read: it persists nothing, writes no event, maps no `meetings/` record and does not advance a cursor. What this command may not do is call `run_harvest`, or anything that does those things — a render that also ingests makes `daily_dashboard.md` a side effect of a write path, and hands a failed harvest a second way to spoil the morning. The network I/O the old clause forbade outright is **accepted deliberately**: the decision that stopped persisting future meetings took that cost knowingly, in exchange for never showing a stale local copy of a row the calendar owns.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | meetings, entries and goals present | file written at the declared path; exit zero | N/A |
| First run ever | nothing harvested, no goals file | valid four-section file, every section stating its reason | N/A |
| Re-run | run twice with unchanged inputs | file byte-identical, **and the event log unchanged** — a render is not a state mutation | N/A |
| Malformed goals file | duplicate goal id | refused, naming the id; **the previous dashboard is left intact** | `MalformedGoals` |
| Malformed meeting record | a hand-edited record | refused, naming the file; previous dashboard intact | `MalformedMeeting` |
| Calendar unreachable | the day's fetch fails or is refused | the `HarvestFailure` reaches the renderer **as the `meetings` argument**, and the file is still written at exit zero — a day whose calendar could not be read is a dashboard that says so, not a failed command. An empty sequence here would state a query result there was none of | reported, never raised |
| Project scope requested | `--scope project:alpha` | `render_project_dashboard` runs; written to that project's tree; **no personal-scope artifact opened at all** | N/A |
| Timezone unset | `Config().display_timezone` | refused before any read — the day boundary may not be assumed | exit `3` |
| Timezone unknown to `zoneinfo` | a typo'd zone in `config.toml` | refused by `4a`'s loader before this slice runs | `ConfigRefused`, exit `3` |
| Master key absent | no key enrolled | dashboard still written — it is unencrypted, and this must not require a key | N/A |
| Path traversal in a name | a meeting id containing `../` | refused by `write_artifact`'s existing validation | propagated |
| Malformed event-log segment | a hand-edited or corrupt segment | refused, naming the segment; previous dashboard intact | exit `3` |
| Undeclared scope requested | `--scope people:bob`, `--scope application` | refused by name — `daily_dashboard.md` is declared in two trees only | exit `3` |
| Unparseable scope argument | `--scope alpha`, `--scope project:` | refused with usage before any read | exit `2` |
| Write fails after a successful render | disk full, read-only filesystem | refused; the previous file survives, since `_replace` publishes atomically | exit `1` |
| Target is a directory | a directory where a `File` is declared | refused, naming the path and the expected node type | exit `1` |

</frozen-after-approval>

## Code Map

- `pm_ai/app/pipelines.py` -- add `run_dashboard(daemon, *, scope, now)`; `run_harvest` at `:20` is the shape to follow
- `pm_ai/surfaces/cli/dispatch.py` -- add the `dashboard` subcommand to `4c`'s table
- `pm_ai/core/rendering.py` -- `23a`'s section renderers and `23d`'s datasource declaration
- `pm_ai/core/meeting_records.py`, `pm_ai/core/goal_register.py`, `pm_ai/core/event_log.py:52` -- the three inputs
- `pm_ai/storage/service.py:1022,1039` -- `write_artifact` and the ledger refusal that correctly does not apply here
- `pm_ai/domain/scope_model.py:540` -- the declaration that resolves the path

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/app/pipelines.py` -- add `run_dashboard`: read three inputs, render, write
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- add `dashboard`, with a scope argument defaulting to personal
- [ ] `tests/slice/test_dashboard_slice.py` -- the matrix end to end against a temporary root
- [ ] `tests/surfaces/test_cli_dispatch.py` -- extend for the new subcommand

**Acceptance Criteria:**
- Given a temporary root with two harvested meetings and a goals file, when `run_dashboard` runs, then `~/.manager-ai/memory/daily_dashboard.md` exists under that root with four headings and the meetings listed in start order.
- Given a goals file with a duplicate id and a dashboard already on disk, when the pipeline runs, then it refuses **and** the existing file is unchanged byte for byte — a failed render must not destroy yesterday's dashboard.
- Given a clean root with nothing harvested, then the file is still written and every section states its reason.
- Given no master key is enrolled, then the write succeeds — this artifact is unencrypted and must not depend on the enclave.
- Given `--scope project:alpha` against a root whose personal tree holds goals and meetings, when the pipeline runs, then no path beneath the personal root is read — asserted by instrumenting the reader, because `23d`'s AD-25 test checks only what the renderer *declares*, never what `run_dashboard` actually opens.

## Spec Change Log

- **2026-09-15, three defects left by the 2026-09-07 realignment, settled on instruction.**
  **`4g` is a dependency of this slice and was never recorded as one.** The frozen Always requires `display_timezone` and refuses an unset zone; the key lands in `4g`, which is still `ready-for-dev`, and `pm_ai/core/config.py` closes `ACCEPTED_KEYS` at three fields. The decision assigning the key on 2026-09-03 said in as many words that "`23b` reads it once and passes it to both consumers", and the edge was added to no dependency table and no graph. It is now in both, and this slice cannot start before `4g`.
  **The `Never` clause contradicted the Always it was realigned beside.** Always gained a live Graph fetch on 2026-09-07; `Never` kept forbidding network I/O from this command, with that prohibition as its stated reason. The realignment's own decision record accepts the render-time network exposure deliberately, so the clause is narrowed to what it always meant operationally — no `run_harvest`, no persistence, no cursor — and the stale justification is removed rather than reinterpreted at execution time.
  **The unreachable-calendar row the realignment claimed to add here was never added.** `23a` and `23d` both gained one; this slice, which performs the fetch and decides what reaches the renderer, did not — so the one place the honesty rule is actually enforced had no row asserting it. Added, and it fixes exit zero: a calendar that could not be read is a dashboard that says so, not a failed command.
  **Left open deliberately, and not settled here.** Nothing states what happens when *no Graph connector is enrolled at all* — a third state that is neither "no meetings" nor "the fetch failed" — and the Code Map and execution tasks below still describe the pre-2026-09-07 design.

- **2026-09-07, this pipeline gains the calendar read.** Consequent on the decision that no future meeting is persisted. `11a`'s `for_day` is no longer the dashboard's source for the day ahead — the calendar is — and since `core` is I/O-free and `render_dashboard` is pure, the fetch belongs in this layer. The timezone rule is unchanged in substance: one read, passed to the renderer and to whatever selects the day, so the two cannot disagree. What changes is that the selection is now a live fetch whose failure must reach the renderer as a reason rather than as an absence.

- **2026-09-03, amended against the second multi-lens review and the day's decisions.**
  **The `Ask First` on logging a render is answered: it does not log** (Q14). The discriminator is whether an action changes truth or projects it — a dashboard is derivable from Tier 1 plus a clock, and CAP-10's retrospective counts are decisions, proposals and commitments. `2c`'s vocabulary stays closed, and a matrix row now asserts the event log is unchanged after a re-run.
  **The project branch calls a different function.** `project_scope_datasources` is not built: the two dashboards are separate renderers, and the project one has no goals parameter, so this slice cannot pass it personal-scope data even by mistake. That is the wall, and it is structural rather than a list this slice must remember to consult.
  **The timezone has a source and is read here.** `config.toml`'s `display_timezone` reaches both the renderer and `11a`'s `for_day` from one read, so the day the dashboard shows and the day its meetings were selected by cannot disagree. An unset zone is refused rather than defaulted to UTC — the silent wrong answer the key was added to prevent.
  **One review claim did not hold.** B8 said this slice was specified against a "degrades quietly to `UNALIGNED`" model and would crash on an absent goals file. It would not: neither this slice nor `23a` calls `resolve` or `alignment_tag`, so `UnresolvedGoal` is unreachable. `22a`'s Intent was wrong about current behaviour; the knock-on onto the renderers was not.

- **2026-09-02, multi-lens review.** The project-scope row had no criterion and the third input had no refusal path.
  **AD-25 was asserted only against the renderer's declaration**, never against what `run_dashboard` actually reads — so the pipeline could open the personal goals file for a project render and every declared check would pass. A criterion now instruments the reader.
  **The event log was the one input with no refusal row**, though a hand-edited or corrupt segment fails to parse exactly as a malformed goals file does; without the row, the read-before-write guarantee covered two of three inputs.
  **Exit codes now come from `4c`'s table** rather than being described as "the refusal exit code" independently here, in `4c` and in `8b`.
  The edge-case lens added the four filesystem and argument paths: an undeclared scope, an unparseable `--scope`, the write failing after a successful render, and the target existing as a directory where a `File` is declared.
## Design Notes

The refusal cases are the ones worth designing. `write_artifact` replaces whole files, so the natural implementation opens the target, then renders, then discovers a malformed goals file and has already truncated yesterday's dashboard. Reading and rendering must fully succeed before the write is attempted, which is also why `23a` is a pure function returning a string rather than something that writes as it goes.

The master-key row exists because the enclave is easy to over-apply. `daily_dashboard.md` is declared unencrypted, so a first-time user who has not run `pm-ai key enrol` should still get a dashboard; requiring a key here would make the enclave a dependency of the one output the PM sees every morning.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_dashboard_slice.py -q` -- expected: all matrix rows pass
- `uv run pm-ai dashboard` -- expected: file written at the declared path, exit zero
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
