---
title: 'Jobs report themselves at the prompt'
type: 'feature'
created: '2026-10-16'
status: 'done'
baseline_commit: '8b79328d36da9e733063f9c84eac425d500956f4'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `4q` prints an acknowledged job's result only at the next prompt, so a person who typed `dashboard` sees nothing until they press Enter again, and nothing says whether the job is still running. Andrei, after first use: pressing Enter again to see a job's result is unfriendly; he wants the result when it arrives, a count of running jobs and a sign that they are progressing.

**Approach:** Supersedes `4q`'s rule "only between lines, at the next prompt" (memlog UX decision, 2026-10-16). With `4u`'s `prompt_toolkit` prompt, a job's result is printed above the line being typed as soon as it arrives and the half-typed line is redrawn intact; the prompt shows how many jobs are running; lines at the bottom show each running job with a spinner, its state and the time since it was acknowledged. Progress is elapsed time measured in the prompt's process, so `4r`'s event stream is unchanged.

## Boundaries & Constraints

**Always:**
- **Shown when it arrives, never into a typed line.** While the prompt is waiting for input, a result is printed above it — each output line prefixed by the job id, then `<id>: exit <n>`, as `4q` prints it — and the prompt and whatever was typed, with the cursor where it was, are redrawn below. This is `prompt_toolkit`'s printing above the prompt, in force only for the duration of each read of a line, never around a local command's own questions. The standard library's `readline` cannot do it safely: it exposes `get_line_buffer()` and `redisplay()` but no call that clears the visible line or forces a full redraw, the interpreter here links `editline` (measured, `4u`), and calling it from the thread that received the result is not documented as safe. While a local command runs (it may be asking its own questions) or the prompt waits on an inline answer, arrivals are held and printed the moment the prompt returns, before it is drawn.
- **The prompt counts running jobs.** `pm-ai> ` when none is running, so `4f`'s startup test is unchanged; `pm-ai [2 running]> ` while two are. The count is the jobs acknowledged in this session whose result has not arrived; it changes the moment one arrives.
- **Progress is elapsed time, not new events.** `4r`'s stream sends one event per state change (`queued`, `running`, `done`, `failed`), each the job's row with `created_at` and `updated_at`, and nothing in between. Below the hint line, one line per running job shows a spinner, the words typed (`dashboard --scope personal`, cut with `…` to fit the terminal's width), its last state from the stream (`acknowledged` before the stream's first event), and the time since the acknowledgement as `m:ss`, or `h:mm:ss` from an hour — measured on this process's monotonic clock, not the daemon's `created_at`, so no two clocks are mixed (AD-35). At most three job lines are shown, then `+N more`. A state word the prompt does not know is shown as sent and treated as running. The client reports the states it already reads instead of discarding them; the stream's endpoint, its fields and its events are unchanged.
- **How the bottom redraws.** While at least one job runs, the session's own refresh redraws the prompt and the job lines about four times a second; with none running it does not redraw at all. The waiting threads change shared job state under one lock and ask the session to redraw through the library's thread-safe call; they never print directly. A redraw is drawing, not work: the prompt still schedules nothing (AD-7, the `cli-owns-no-scheduling` contract stands).
- **The hint line is shared.** `4u`'s usage hint and the job lines share the bottom of the terminal: the hint first when a command is being typed, the jobs below it; with no command typed, only the jobs.
- **`4q`'s other rules stand.** The job id is shown at the acknowledgement; two jobs are listed by id when the second is acknowledged; a lost stream is one sentence naming the id; `exit` or Ctrl-D with a job running prints every result that has already arrived, then names each job still running and says it continues in the daemon — a result arriving after that is not shown; Ctrl-C while typing clears the line and leaves the jobs running; exit codes, ids, routing and every refusal sentence are unchanged. The thread that waits on a job's stream only waits, and an error in reporting a state never ends it: the job is still followed to its result.
- **Without the runtime extra** the prompt is `4q`'s, results at the next prompt. An accepted limit: a prompt that reaches a running daemon has the runtime extra in practice, because the daemon needs it in the same install. The same fallback holds when the terminal check fails or the line editor fails mid-session: the prompt falls back to `4q`'s loop, results at the next prompt (Andrei, 2026-10-18).

**Ask First:** Any new field or event on `4r`'s `GET /v1/jobs/<id>/events` (`4r`'s Ask First covers its fields) — for example, a percentage or a step name.

**Never:** No polling `GET /v1/jobs/<id>` for progress. No terminal bell or notification outside the terminal. No result printed into a local command's questions. No change to `4r`'s endpoints. No second job list beside `4q`'s.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Result while idle | `dashboard` acknowledged; nothing typed | result and `<id>: exit 0` printed when it arrives; prompt redrawn; no Enter needed | N/A |
| Result while typing | `config sh` half typed when the result arrives | result printed above; `config sh` and the cursor position kept | N/A |
| Count | two jobs acknowledged | prompt reads `pm-ai [2 running]> `; `[1 running]` after the first result | N/A |
| Two jobs listed | a second job acknowledged while the first runs | both ids listed, as in `4q` | N/A |
| Progress | a job running 12 s | job line: spinner, `dashboard --scope personal`, `running`, `0:12` | N/A |
| Long job | a job running 1 h 2 min 5 s | `1:02:05` | N/A |
| Queued | the stream's first event is `queued` | the job line says `queued` until `running` arrives | N/A |
| Unknown state | the stream sends `paused` | shown as `paused`; counted as running | N/A |
| Many jobs, long words | five jobs, one with a 200-character line | three job lines, words cut with `…`, then `+2 more` | N/A |
| Hint and jobs | `transcript drop ` typed while a job runs | the usage hint, then the job line below it | N/A |
| No jobs | none running | `pm-ai> `; no job line; no redraw | N/A |
| During a local command | `goal set` asking its questions when a result arrives | held; printed when `goal set` returns, before the next prompt | N/A |
| During an inline wait | waiting on `config show` when a result arrives | held; printed before the next prompt | N/A |
| Ctrl-C while typing | a job running | line cleared; the job line and count stay | N/A |
| Reporting a state fails | the state callback raises | the job still followed; its result printed on arrival | N/A |
| Failed job | the job ends `failed` | its sentence and `exit 1` with the id, printed on arrival | N/A |
| Stream lost | the stream ends early | one sentence naming the id on arrival; the count drops | session continues |
| Exit with a job | `exit` while one runs, another's result already arrived | the arrived result printed; the running one named, continuing in the daemon | exit 0 |
| Startup | `pm-ai` under a pseudo-terminal | `pm-ai> ` within 1.0 s | fails over 1.0 s |
| No runtime extra | `prompt_toolkit` absent | `4q`'s behavior | N/A |
| Line editor fails | the terminal check fails, or the line editor fails mid-session | `4q`'s loop from then on; results at the next prompt | session continues |

</frozen-after-approval>

## Code Map

Re-verified against `5f4f43e` on 2026-10-18.

- `pm_ai/surfaces/cli/repl.py:102` -- `DaemonClient`, whose `follow` (`:107`) gains a state callback; `:291` `_Jobs`: one waiting thread per job (`follow` `:303`, `_wait` `:336`), results queued, `deliver` only between lines (`:321`); `:410` the acknowledgement and the "jobs in flight" line (`:417`); `:470` `_ending`; `:512` the loop that delivers before each prompt (`:516`); the module docstring (`:19`-`:22`) and `_Jobs`'s (`:295`) still say "at the next prompt" and change with the rule
- `pm_ai/surfaces/cli/repl.py:213` -- `_Edited`, the wrapper `run` reads through, which holds only a `read(text)` callable; `:238` `_line_editor`, where the session is built (`:264`) and every fallback to `4q`'s loop is decided; `:490` `run`, whose `read` stays injectable. `_Jobs` needs to reach the session (dynamic prompt, toolbar, redraw, printing above), which today it cannot through `_Edited`
- `pm_ai/surfaces/cli/editor.py:125` -- `build`: the `PromptSession` (`:133`), with `bottom_toolbar` (`:139`) bound to `_hint` (`:176`), which is where the job lines join the hint; `:153` `session`, whose `read` (`:167`) passes the prompt as styled text (`:171`) -- a dynamic count needs a callable message that keeps that styling, or the trailing space of `pm-ai> ` is not written (`:168`-`:170`); `:68` `_Output`, one row reported below the cursor with the cursor-position request off
- `pm_ai/surfaces/cli/loopback.py:297` -- `follow`; `:384` `_follow`, which reads every event (`:422`) and keeps only a finished one (`:423`, `_FINISHED` `:87`)
- `pm_ai/app/prompts.py:173` -- `row_body`, the event's fields (`id`, `kind`, `scope`, `state`, `result`, `created_at`, `updated_at`); `:542` `job_events`; `:553` `_stream`, the row as it is when the stream is read, then one event per forward state change, nothing in between; `pm_ai/core/dispatch.py:38` `JobState`, the four states; `pm_ai/surfaces/api/app.py:210` the route that frames each event as `data:`
- `.importlinter:153` -- `cli-owns-no-scheduling`, which forbids `sched`, `apscheduler` and pm-ai's scheduler in `pm_ai.surfaces.cli`
- `tests/surfaces/test_cli_repl.py` -- stand-in `_Daemon` (`:361`, `follow` `:384`) and the inline stand-in at `:806`; `4q`'s row "Result while typing" is `:692` (it runs with an injected `read`, so it stays true for the loop without the library); "Two jobs" is `:715`, a `4q` rule this slice keeps; `tests/slice/test_repl_through_daemon.py:65` `_Recorded`, whose `follow` (`:76`) is a stand-in whose signature changes; that file runs `repl.run` in-process with an injected `read`, so no pseudo-terminal and no `prompt_toolkit`
- `tests/surfaces/test_cli_completion.py:327` -- `4u`'s pipe-input and dummy-output fixture over `editor.session` (`:339`), the pattern for the matrix tests; `tests/slice/test_repl_startup.py:130` the 1.0 s startup test and `:233` the pseudo-terminal hint-line test, the pattern for the arrival harness

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/surfaces/cli/loopback.py:297`, `pm_ai/surfaces/cli/repl.py:107` -- `follow(job_id, on_state=None)`: each state event read is reported to the callback before the finished one is returned; an error in the callback is caught and the stream still read; the protocol changes with it -- the client already reads the states
- [x] `pm_ai/surfaces/cli/repl.py:291`, `pm_ai/surfaces/cli/editor.py:125` -- `_Jobs` keeps, per job and under one lock, the words typed, the acknowledgement's monotonic time and the last state; prints above the prompt only during a read of a line, holds arrivals otherwise; the dynamic prompt with the count; the job lines (cut, capped, `+N more`, `h:mm:ss` from an hour); redraws asked through the library's thread-safe call, refresh on only while a job runs; `_ending` prints arrived results first -- the contract
- [x] `tests/surfaces/test_cli_repl.py:384`, `:806`, `tests/slice/test_repl_through_daemon.py:76` -- stand-in clients and `_Recorded.follow` take the new callback; `4q`'s superseded row (`:692`) kept as the loop without the library, renamed to say so -- the existing tests follow the signature
- [x] `tests/surfaces/test_cli_job_progress.py` -- one test per matrix row against the session driven by the library's pipe input and a dummy output, a stand-in client whose job is held until the test releases it, and a stand-in clock -- the matrix is the contract, and the `[1 running]` prompt is seen while the job is held
- [x] `_bmad-output/specs/spec-pm-ai/stories/4q-repl-through-the-daemon.md` -- when this slice is built: edit the amended frozen text and add a dated Change Log line naming the rule this slice supersedes ("only between lines, at the next prompt") -- an earlier frozen block says it was amended
- [x] `tests/slice/test_job_arrival_pty.py` -- new harness: the console script on a pseudo-terminal with a temporary home and the cursor-position request off, against a real daemon: `dashboard` entered, then without another key its result and `exit 0` appear with the id; then with `config sh` typed and not entered, a second job's result appears and `config sh` is still on the line -- arrival is real

**Acceptance Criteria:**
- Given a stand-in daemon client holding a `dashboard` job, when `dashboard` is entered at the session and the stand-in clock advances twelve seconds, then the prompt reads `pm-ai [1 running]> ` and the job line shows `0:12`; when the job is released, then its result with the id and `exit 0` is printed without another key and the prompt reads `pm-ai> `.
- Given a real daemon and `pm-ai` on a pseudo-terminal, when `dashboard` is entered and nothing else is typed, then its result and `exit 0` appear with the id.
- Given the change, when `uv run pytest` runs, then everything passes, `4f`'s startup test included, and the skip count is unchanged by this slice.

## Spec Change Log

- **2026-10-16, created** from Andrei's feedback after first use (memlog UX decision, slice 4v); supersedes `4q`'s "printed only between lines, at the next prompt". Decisions it carries: results printed above the prompt on arrival through `prompt_toolkit` (`4u`), held while a local command or an inline wait owns the terminal; the standard library's `readline` rejected because it cannot clear and redraw a line from another thread; the count in the prompt only when non-zero, so `pm-ai> ` and the startup test are unchanged; progress is elapsed time on the prompt's monotonic clock plus the state the stream already sends, so `4r`'s endpoint is unchanged and a new event would be `4r`'s Ask First; the job lines share the bottom of the terminal with `4u`'s hint.
- **2026-10-16, review:** the limit without the runtime extra kept, with its reason, as an accepted limit; the count's wording; printing above the prompt confined to the read of a line; the redraw named (the session's refresh while jobs run, a thread-safe redraw request, state under one lock); job lines cut, capped at three with `+N more`, `h:mm:ss` from an hour, unknown states shown as sent; a failing state report never ends the waiting thread; what `exit` prints; rows for hint and jobs together, two jobs listed, Ctrl-C while typing, long and many jobs; tests rebuilt — the existing in-process tests take the new signature, the matrix runs against the library's pipe input with a held job and a stand-in clock, and a new pseudo-terminal harness checks real arrival; ACs given a When. Amends an earlier frozen block: `4q`'s "only between lines, at the next prompt" — a task to add a dated line to `4q`'s Change Log when this lands. Depends on spine amendment: none (the `prompt_toolkit` Stack row is `4u`'s).
- **2026-10-16, review (cross-spec):** Change Log moved before Design Notes; the amendment task now edits `4q`'s frozen text and adds a dated line, when this slice is built.
- **2026-10-18, Code Map re-verified against `5f4f43e` after 4u merged.** Line numbers corrected; `editor.py`, `_Edited` and `_line_editor`, `4u`'s pipe-input fixture and the startup and hint-line pseudo-terminal tests added to the Code Map; the "Two jobs" row is a `4q` rule this slice keeps, not one it rewrites. Fit with what `4u` built is in Design Notes; the frozen block is unchanged.
- **2026-10-18, decision (Andrei):** if the line editor fails mid-session, or the terminal check fails, the prompt falls back to `4q`'s loop, results at the next prompt, the same as without the runtime extra. Frozen "Without the runtime extra" bullet extended; matrix row "Line editor fails" added.
- **2026-10-19, decision (Andrei):** frozen block edited (recorded here): a job line shows `acknowledged` before the stream's first event, as built; the review noted the spec named no word for that moment.

## Design Notes

Elapsed time answers "is it progressing" for every job pm-ai has today: in the field test `dashboard` took about three seconds and `connector check` about one, so a percentage would need steps the daemon does not report and is not worth a contract change now. The monotonic clock is the prompt's own; the daemon's `created_at` would mix its wall clock with the prompt's display. Printing above the prompt is confined to the read of a line because a local command such as `goal set` asks its questions with plain `input()`, and output landing in those would be the thing this slice forbids. Three job lines and `+N more`: enough to see what is running without the jobs pushing the line being typed off a small terminal. A real job finishes in milliseconds to seconds, so the running prompt is asserted with a held stand-in, and the pseudo-terminal test asserts only what a real daemon makes certain — arrival without a key press. Results after `exit` are not shown because the session has ended; `4q` already names the jobs that continue.

How it fits what `4u` built (checked against `prompt_toolkit` 3.0.53 on 2026-10-18). Printing above the prompt is the library's `run_in_terminal`, scheduled on the session's event loop from the waiting thread; `patch_stdout` is not needed and would replace the process's `sys.stdout` for the read, against `4q`'s rule of no process-wide redirection. `run_in_terminal` with no session running simply calls the function, so the choice between holding an arrival and printing it is made under the same lock that records whether a read is in progress. The job lines join `4u`'s hint in the one bottom toolbar: the library sizes the drawn area to the toolbar's height, so several lines draw with the cursor-position request off, but it never shrinks the area while a line is being read, so rows freed when a job ends stay blank until the next prompt. The library's `refresh_interval` is fixed when each read starts; a job that ends mid-read would keep it redrawing until Enter, so "no redraw with none running" needs a redraw task the slice starts and stops, not that setting. At prompt open with no job running, this slice adds a prompt message and a toolbar callable that both return what they do today, no thread and no redraw; `run_in_terminal` is already loaded with the library, so nothing is added to the 1.0 s startup budget.

## Verification

**Commands:**
- `uv run pytest tests/surfaces/test_cli_job_progress.py tests/surfaces/test_cli_repl.py tests/slice/test_job_arrival_pty.py tests/slice/test_repl_startup.py -q` -- expected: pass
- `uv run pytest -q` -- expected: all pass; the skip count unchanged by this slice
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- `HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai daemon run` in one terminal; `.venv/bin/pm-ai` in another; `dashboard`, then start typing `config show` before it finishes -- expected: the count, the spinner and time, the result above the half-typed line, the line intact

## Suggested Review Order

**Holding or printing an arrival**

- Entry point: one lock decides whether a result prints above the prompt or is held.
  [`repl.py:427`](../../../../pm_ai/surfaces/cli/repl.py#L427)

- The waiting thread always marks the job arrived, even on a `BaseException`.
  [`repl.py:546`](../../../../pm_ai/surfaces/cli/repl.py#L546)

- Held results go back on the list if printing fails partway.
  [`repl.py:483`](../../../../pm_ai/surfaces/cli/repl.py#L483)

- Printing above the prompt goes through `run_in_terminal` on the session's loop, never `patch_stdout`.
  [`editor.py:269`](../../../../pm_ai/surfaces/cli/editor.py#L269)

- Printing is refused once the app has stopped, so the result stays held.
  [`editor.py:295`](../../../../pm_ai/surfaces/cli/editor.py#L295)

**Count, job lines and redraw**

- The prompt and toolbar the library draws come from the board.
  [`editor.py:199`](../../../../pm_ai/surfaces/cli/editor.py#L199)

- The redraw task starts when a read opens with jobs running and stops when none run.
  [`editor.py:286`](../../../../pm_ai/surfaces/cli/editor.py#L286)

- Job lines are measured in terminal cells; the state is stripped and capped.
  [`repl.py:394`](../../../../pm_ai/surfaces/cli/repl.py#L394)

- Finished states are never drawn; any other state is shown as sent.
  [`repl.py:563`](../../../../pm_ai/surfaces/cli/repl.py#L563)

**Falling back to 4q**

- The jobs detach when the line editor fails mid-session; held results print before the 4q prompt.
  [`repl.py:236`](../../../../pm_ai/surfaces/cli/repl.py#L236)

**Stream states**

- `follow` reports each state; an error in the callback never ends the wait.
  [`loopback.py:393`](../../../../pm_ai/surfaces/cli/loopback.py#L393)

**Tests**

- The matrix, driven by pipe input, a held stand-in client and a stand-in clock.
  [`test_cli_job_progress.py:1`](../../../../tests/surfaces/test_cli_job_progress.py#L1)

- Real arrival on a pseudo-terminal against a real daemon.
  [`test_job_arrival_pty.py:1`](../../../../tests/slice/test_job_arrival_pty.py#L1)
