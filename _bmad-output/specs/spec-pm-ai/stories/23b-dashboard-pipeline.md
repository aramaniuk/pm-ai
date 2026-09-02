---
title: 'Dashboard pipeline and pm-ai dashboard'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** After `23a` the renderer is a pure function nothing calls, `11a` holds meetings, `22a` parses goals and `33b` fills both from Graph. Nothing joins them, and `daily_dashboard.md` is still never written. This is the last slice of wave 1 and the one that makes the prototype real.

**Approach:** Add `run_dashboard` to `pm_ai/app/pipelines.py` — read meetings, log entries and goals, render, write through `StorageService.write_artifact` — and expose it as `pm-ai dashboard`.

## Boundaries & Constraints

**Always:**
- **The pipeline lives in `app`.** It must touch storage, core and the scope model at once, which no layer below may do (AD-30) — the same reason `run_harvest` lives there.
- **The write goes through `write_artifact`**, which resolves the path from the declaration and decides sealing. `daily_dashboard.md` is a Tier-1 `File`, unencrypted, not gitignored (`scope_model.py:540`). Whole-file replacement is correct: this is a rendering, not a ledger, so the ledger refusal at `service.py:1039` does not apply and must not be worked around.
- **The default scope is personal**, because CAP-9 names `~/.manager-ai/memory/daily_dashboard.md`. A project-scope render uses `project_scope_datasources` and cannot reach personal sources (AD-25).
- **The render is deterministic given its inputs**, so re-running produces the same file. Nothing about the output depends on how many times it has run.
- **A missing input is a stated section, not a failure.** No goals file, no meetings and an empty log all produce a valid dashboard.
- **Read and render fully before writing anything.** `write_artifact` replaces whole, so opening the target first and discovering a malformed input afterwards destroys yesterday's dashboard. This is why `23a` returns a string rather than writing as it goes.
- **Exit codes come from `4c`'s table** — `3` for a refusal, `1` for an unexpected exception. This slice may not add to it.

**Ask First:** Whether writing the dashboard should append a `SelfActionType` entry to the event log. Every state mutation appends one under CAP-10, and a rendering is arguably not a state mutation — but a daily artifact silently replaced with no record is also the kind of thing a retrospective wants. `2c` closed the vocabulary, so adding a member is the reviewed decision it describes.

**Never:** No scheduler and no daemon — `pm-ai dashboard` runs once and exits, and the CLI may hold no scheduler (AD-7). The 07:00 deadline in CAP-9 is knowingly unmet until `9a`. No harvest triggered from here: the dashboard renders what has been harvested, and conflating the two would make a read command perform network I/O.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | meetings, entries and goals present | file written at the declared path; exit zero | N/A |
| First run ever | nothing harvested, no goals file | valid four-section file, every section stating its reason | N/A |
| Re-run | run twice with unchanged inputs | file byte-identical | N/A |
| Malformed goals file | duplicate goal id | refused, naming the id; **the previous dashboard is left intact** | `MalformedGoals` |
| Malformed meeting record | a hand-edited record | refused, naming the file; previous dashboard intact | `MalformedMeeting` |
| Project scope requested | `--scope project:alpha` | written to that project's tree; **no personal-scope artifact opened at all** | N/A |
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
- `pm_ai/core/rendering.py` -- `23a`'s renderer and datasource declaration
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
- Given `--scope project:alpha` against a root whose personal tree holds goals and meetings, when the pipeline runs, then no path beneath the personal root is read — asserted by instrumenting the reader, because `23a`'s AD-25 test checks only what the renderer *declares*, never what `run_dashboard` actually opens.

## Spec Change Log

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
