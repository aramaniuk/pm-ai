---
title: 'Jobs report themselves at the prompt'
type: 'feature'
created: '2026-10-16'
status: 'draft'
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
- **Progress is elapsed time, not new events.** `4r`'s stream sends one event per state change (`queued`, `running`, `done`, `failed`), each the job's row with `created_at` and `updated_at`, and nothing in between. Below the hint line, one line per running job shows a spinner, the words typed (`dashboard --scope personal`, cut with `…` to fit the terminal's width), its last state from the stream, and the time since the acknowledgement as `m:ss`, or `h:mm:ss` from an hour — measured on this process's monotonic clock, not the daemon's `created_at`, so no two clocks are mixed (AD-35). At most three job lines are shown, then `+N more`. A state word the prompt does not know is shown as sent and treated as running. The client reports the states it already reads instead of discarding them; the stream's endpoint, its fields and its events are unchanged.
- **How the bottom redraws.** While at least one job runs, the session's own refresh redraws the prompt and the job lines about four times a second; with none running it does not redraw at all. The waiting threads change shared job state under one lock and ask the session to redraw through the library's thread-safe call; they never print directly. A redraw is drawing, not work: the prompt still schedules nothing (AD-7, the `cli-owns-no-scheduling` contract stands).
- **The hint line is shared.** `4u`'s usage hint and the job lines share the bottom of the terminal: the hint first when a command is being typed, the jobs below it; with no command typed, only the jobs.
- **`4q`'s other rules stand.** The job id is shown at the acknowledgement; two jobs are listed by id when the second is acknowledged; a lost stream is one sentence naming the id; `exit` or Ctrl-D with a job running prints every result that has already arrived, then names each job still running and says it continues in the daemon — a result arriving after that is not shown; Ctrl-C while typing clears the line and leaves the jobs running; exit codes, ids, routing and every refusal sentence are unchanged. The thread that waits on a job's stream only waits, and an error in reporting a state never ends it: the job is still followed to its result.
- **Without the runtime extra** the prompt is `4q`'s, results at the next prompt. An accepted limit: a prompt that reaches a running daemon has the runtime extra in practice, because the daemon needs it in the same install.

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

</frozen-after-approval>

## Code Map

- `pm_ai/surfaces/cli/repl.py:80` -- `DaemonClient`, whose `follow` gains a state callback; `:146` `_Jobs`: one waiting thread per job, results queued, `deliver` only between lines (`:176`); `:261` the acknowledgement and the "jobs in flight" line; `:321` `_ending`; `:359` the loop that delivers before each prompt
- `pm_ai/surfaces/cli/loopback.py:297` -- `follow`, which reads every event (`:421`) and keeps only a finished one
- `pm_ai/app/prompts.py:172` -- `row_body`, the event's fields (`id`, `kind`, `scope`, `state`, `result`, `created_at`, `updated_at`); `:552` `_stream`, one event per state change, nothing in between
- `pm_ai/surfaces/cli/repl.py` after `4u` -- the `prompt_toolkit` session this slice gives a dynamic prompt, job lines and printing above the prompt
- `.importlinter:153` -- `cli-owns-no-scheduling`, which forbids `sched`, `apscheduler` and pm-ai's scheduler in `pm_ai.surfaces.cli`
- `tests/surfaces/test_cli_repl.py` -- `4q`'s rows "Result while typing" and "Two jobs", which this slice rewrites, and its stand-in clients; `tests/slice/test_repl_through_daemon.py:72` `_Recorded.follow`, a stand-in whose signature changes; that file runs `repl.run` in-process with an injected `read`, so no pseudo-terminal and no `prompt_toolkit`

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/surfaces/cli/loopback.py`, `pm_ai/surfaces/cli/repl.py:80` -- `follow(job_id, on_state=None)`: each state event read is reported to the callback before the finished one is returned; an error in the callback is caught and the stream still read; the protocol changes with it -- the client already reads the states
- [ ] `pm_ai/surfaces/cli/repl.py` -- `_Jobs` keeps, per job and under one lock, the words typed, the acknowledgement's monotonic time and the last state; prints above the prompt only during a read of a line, holds arrivals otherwise; the dynamic prompt with the count; the job lines (cut, capped, `+N more`, `h:mm:ss` from an hour); redraws asked through the library's thread-safe call, refresh on only while a job runs; `_ending` prints arrived results first -- the contract
- [ ] `tests/surfaces/test_cli_repl.py`, `tests/slice/test_repl_through_daemon.py` -- stand-in clients and `_Recorded.follow` take the new callback; `4q`'s superseded rows rewritten -- the existing tests follow the signature
- [ ] `tests/surfaces/test_cli_job_progress.py` -- one test per matrix row against the session driven by the library's pipe input and a dummy output, a stand-in client whose job is held until the test releases it, and a stand-in clock -- the matrix is the contract, and the `[1 running]` prompt is seen while the job is held
- [ ] `_bmad-output/specs/spec-pm-ai/stories/4q-repl-through-the-daemon.md` -- when this slice is built: edit the amended frozen text and add a dated Change Log line naming the rule this slice supersedes ("only between lines, at the next prompt") -- an earlier frozen block says it was amended
- [ ] `tests/slice/test_job_arrival_pty.py` -- new harness: the console script on a pseudo-terminal with a temporary home and the cursor-position request off, against a real daemon: `dashboard` entered, then without another key its result and `exit 0` appear with the id; then with `config sh` typed and not entered, a second job's result appears and `config sh` is still on the line -- arrival is real

**Acceptance Criteria:**
- Given a stand-in daemon client holding a `dashboard` job, when `dashboard` is entered at the session and the stand-in clock advances twelve seconds, then the prompt reads `pm-ai [1 running]> ` and the job line shows `0:12`; when the job is released, then its result with the id and `exit 0` is printed without another key and the prompt reads `pm-ai> `.
- Given a real daemon and `pm-ai` on a pseudo-terminal, when `dashboard` is entered and nothing else is typed, then its result and `exit 0` appear with the id.
- Given the change, when `uv run pytest` runs, then everything passes, `4f`'s startup test included, and the skip count is unchanged by this slice.

## Spec Change Log

- **2026-10-16, created** from Andrei's feedback after first use (memlog UX decision, slice 4v); supersedes `4q`'s "printed only between lines, at the next prompt". Decisions it carries: results printed above the prompt on arrival through `prompt_toolkit` (`4u`), held while a local command or an inline wait owns the terminal; the standard library's `readline` rejected because it cannot clear and redraw a line from another thread; the count in the prompt only when non-zero, so `pm-ai> ` and the startup test are unchanged; progress is elapsed time on the prompt's monotonic clock plus the state the stream already sends, so `4r`'s endpoint is unchanged and a new event would be `4r`'s Ask First; the job lines share the bottom of the terminal with `4u`'s hint.
- **2026-10-16, review:** the limit without the runtime extra kept, with its reason, as an accepted limit; the count's wording; printing above the prompt confined to the read of a line; the redraw named (the session's refresh while jobs run, a thread-safe redraw request, state under one lock); job lines cut, capped at three with `+N more`, `h:mm:ss` from an hour, unknown states shown as sent; a failing state report never ends the waiting thread; what `exit` prints; rows for hint and jobs together, two jobs listed, Ctrl-C while typing, long and many jobs; tests rebuilt — the existing in-process tests take the new signature, the matrix runs against the library's pipe input with a held job and a stand-in clock, and a new pseudo-terminal harness checks real arrival; ACs given a When. Amends an earlier frozen block: `4q`'s "only between lines, at the next prompt" — a task to add a dated line to `4q`'s Change Log when this lands. Depends on spine amendment: none (the `prompt_toolkit` Stack row is `4u`'s).
- **2026-10-16, review (cross-spec):** Change Log moved before Design Notes; the amendment task now edits `4q`'s frozen text and adds a dated line, when this slice is built.

## Design Notes

Elapsed time answers "is it progressing" for every job pm-ai has today: in the field test `dashboard` took about three seconds and `connector check` about one, so a percentage would need steps the daemon does not report and is not worth a contract change now. The monotonic clock is the prompt's own; the daemon's `created_at` would mix its wall clock with the prompt's display. Printing above the prompt is confined to the read of a line because a local command such as `goal set` asks its questions with plain `input()`, and output landing in those would be the thing this slice forbids. Three job lines and `+N more`: enough to see what is running without the jobs pushing the line being typed off a small terminal. A real job finishes in milliseconds to seconds, so the running prompt is asserted with a held stand-in, and the pseudo-terminal test asserts only what a real daemon makes certain — arrival without a key press. Results after `exit` are not shown because the session has ended; `4q` already names the jobs that continue.

## Verification

**Commands:**
- `uv run pytest tests/surfaces/test_cli_job_progress.py tests/surfaces/test_cli_repl.py tests/slice/test_job_arrival_pty.py tests/slice/test_repl_startup.py -q` -- expected: pass
- `uv run pytest -q` -- expected: all pass; the skip count unchanged by this slice
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- `HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai daemon run` in one terminal; `.venv/bin/pm-ai` in another; `dashboard`, then start typing `config show` before it finishes -- expected: the count, the spinner and time, the result above the half-typed line, the line intact
