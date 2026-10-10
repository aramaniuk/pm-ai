---
title: 'A line is answered at once or acknowledged with a job'
type: 'feature'
created: '2026-10-10'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `4e`'s daemon runs and reports its health, but nothing can ask it to do work. The architecture's rule for the daemon's request path (AD-21, narrowed by D5 on 2026-10-09 and restated on 2026-10-10: the request path is a transport, so a command's words sent through the daemon deliver the same output the terminal would) is that anything over five seconds answers at once with a job id and delivers later — and that every such job is a durable row, never a memory the next restart loses (AD-20). No endpoint takes a line, no table holds a job, and the rule's own test has skipped since the suite was written.

**Approach:** The daemon gains a prompt endpoint that takes one text line and answers inline or with a job id; a job-status endpoint; and a stream a client reads the result from. A job is a row in the operational database before the acknowledgement goes back, and runs in a bounded set of worker threads off the daemon's loop. The one slow command in this build is `dashboard`, so the job path is real. Depends on `4e`; `1r` lands before. `4q`, the REPL as a client of the daemon, depends on `4e`, `4f` and this slice and owns which commands the REPL sends here.

## Boundaries & Constraints

**Always:**
- **Three endpoints, all under `4e`'s token and address.** `POST /v1/prompt` takes `{"line": "..."}` and answers `200 {"inline": true, "output": "...", "exit": n}` — the command's printed text, standard output then standard error, and its exit code — or `202 {"inline": false, "job_id": "..."}`. These fields are the contract `4q` cites and owns none of. `GET /v1/jobs/<id>` returns the job's row; `GET /v1/jobs/<id>/events` delivers the job's state changes as they happen over one response that stays open and closes after the last one; a job already finished sends that one event and closes. An unknown id is `404` on both.
- **What a line is.** The words of a fixed `pm-ai` command. Any command that does not need a terminal of its own runs here (`4q`'s routing rule: those that ask questions or take a secret — `goal set`, `setup`, `connector add`, `connector sign-in`, `key enrol` — are refused by name); `help` lists the commands, inline. A line whose first word is no command is answered inline with one sentence saying so and naming `help`, exit 2. Each request gets its own output buffers: nothing a command prints can reach another request or the daemon's console.
- **The five-second rule (D5, AD-21).** Every pipeline declares its bound once, beside itself in the composition root; the decision is one function in the I/O-free core that reads that declaration: under five seconds the line is answered inline, at five seconds or over it is acknowledged with a job id and run later. The pre-written AD-21 test passes against it. `dashboard`'s bound is the per-connector fetch budget with connectors fetched one after another, so `dashboard` sent through the daemon is the real job, not a stand-in.
- **A job is a row first.** The row — id, kind, scope, state, result, created and updated times — is in a `requests` table in `operational.db` before the `202` is sent (AD-20); nothing is remembered in memory only. If the writer refuses the row (the lock held, the database busy), the answer is `503` with `Retry-After: 1` and no job id. States: `queued`, `running`, `done`, `failed`. A state change only applies to the state it expects to find, so a job finishing late can never overwrite `failed`. A job id is `job_` plus random hex. `result` holds the command's output and exit code, or on failure pm-ai's own sentence plus the error's type name — never the raw text of an exception, which from a provider can carry a token or a continuation link. The table is created on open like every table; the store's version number does not change. Rows older than seven days are removed at start and once a day after; story 10 widens the table into the durable queue.
- **One job per kind and scope at a time.** A second `dashboard` for a scope whose job is still `queued` or `running` answers `202` with the running job's id. A render through this path records its instant like any other (`9b`'s rule).
- **A restart does not silently re-run anything.** At start, rows still `queued` or `running` from an earlier run are marked `failed` with the reason that the daemon stopped before they finished. Re-running is story 10's.
- **One event loop; one set of workers.** The daemon has one bounded executor of four worker threads, a constant, and one daemon-level lock every worker takes before calling the writer — the database connection allows several threads but serializes none. Jobs run there, and so does every inline command, never on the loop; a fifth job waits `queued`. `9a`'s harvests and `9b`'s renders submit to the same executor and take the same lock; nothing else in the daemon creates threads. AD-19's own pool is for model work; this one is for blocking I/O, the other half of its rule that nothing blocks the loop.
- **The job writes through the daemon's writer**, the one `4e` gave it for its lifetime, under `1r`'s one cross-process lock the CLI uses this wave (AD-5). A job that meets that lock held fails this attempt with the claim named in its result — never silently; the retry ladder is `9a`'s.
- **Shutdown order, stated once here and cited by `9a`:** stop listening; cancel the timers; wait up to ten seconds for running jobs; mark any still running `failed` with that reason; close the writer; exit 0. A worker that finds the writer closed logs it and ends; a render that finishes after being marked `failed` may still write its file, which is harmless for a render product.
- **The stream adds no dependency.** It is the web framework's own kept-open response with the event-stream content type; `sse-starlette` is in the lockfile only because `mcp` pulls it in, and stays undeclared.

**Ask First:** Any endpoint path or response field other than the ones named above, once `4q` is built against them.

**Never:**
- No job retry, no cancellation, no listing; no result kept longer than the seven-day prune — story 10 decides retention.
- No second table, no in-memory queue, no job timer that is not backed by a row.
- No scheduler, no harvest as a job yet (`9a`), no model (story 7).
- No change to `4e`'s token, address, status endpoint or settings key.
- No change to how `pm-ai <verb>` typed at a shell runs — it stays local, inline; sending REPL lines here is `4q`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fast line | `{"line": "help"}` | `200`, `inline` true, `output` the command list, `exit` 0 | N/A |
| Fixed command | `{"line": "config show"}` | `200`, `output` what the terminal prints, `exit` 0; nothing on the daemon's console | a refused command → its sentence and `exit` 3 |
| Terminal-bound command | `{"line": "goal set …"}` | `200`, a sentence naming the command as needing a terminal, `exit` 3 | N/A |
| Unknown line | `{"line": "what did I promise?"}` | `200`, `inline` true, the sentence naming `help`, `exit` 2 | N/A |
| Body not JSON, or no `line` | `-d 'x'`; `{}`; `{"line": 3}` | `422`, the framework's answer | N/A |
| Bad `line` | `""`; 4,096 characters or more | `400` naming `line` | N/A |
| Slow line | `{"line": "dashboard"}` | row `queued` exists before `202 {"inline": false, "job_id"}`; then `running`; then `done` with the path | render failure → `failed`, pm-ai's sentence and the error's type name |
| Writer refuses the row | the lock held when the row is created | `503`, `Retry-After: 1`, no job id | never a `202` |
| Duplicate job | `dashboard` twice, the first still running | the same job id both times | N/A |
| Job status | known id / unknown id | `200` with the row / `404` | N/A |
| Job events, live | stream opened on a queued job | one event per state change; closes after `done` or `failed` | unknown id → `404` |
| Job events, finished | stream opened on a `done` job | that one event, then close | N/A |
| Client leaves mid-stream | the reader disconnects | the job keeps running; its result is in the row | N/A |
| Pool full | five jobs of different kind or scope at once | four `running`, one `queued` until a slot frees | N/A |
| Late finish | a job finishing after it was marked `failed` | the row stays `failed` | N/A |
| Unfinished at restart | a `running` row, daemon starts | row becomes `failed`, reason says the daemon stopped | N/A |
| Old rows | a `done` row eight days old, daemon starts | the row is gone | N/A |
| No token | any of the three without the header | `401`, as `4e` | N/A |
| Planning rule | bounds of 1 s and 42 s; 4.99 s and 5.0 s | inline / a job id — the AD-21 test; inline / a job id — the boundary | N/A |
| Stop | Ctrl-C with a job running | timers cancelled, waits up to 10 s, marks a straggler `failed`, closes the writer, exits 0 | N/A |
| Schema | an `operational.db` at version 1 | opens with the `requests` table; still version 1 | a later version refuses to open, as today |

</frozen-after-approval>

## Code Map

- `pm_ai/core/dispatch.py` -- new: `plan(estimated_seconds)` → `.inline` / `.job_id`; `tests/architecture/test_domain_invariants.py:346-352` is the pre-written test it un-skips. `core` mints the id and decides; the row is the writer's
- `pm_ai/app/pipelines.py:253` -- `run_dashboard(daemon, scope=, now=)`, the one job; its bound is declared beside it and `plan()` reads it; `entry.py:261` `_dashboard` shows how it is bound to the daemon's clock
- `pm_ai/storage/service.py:103-146` -- `_SCHEMA`, `SCHEMA_VERSION = 1`; `:148-157` the rule that a new table is created on open without a version bump (`8a` did the same); `:582` `sqlite3.connect(…, check_same_thread=False)`, which serializes nothing; `begin_execution` `:1837` / `settle_execution` `:1861` as the write-then-settle shape to copy; `exclusive` `:1302` raises `ArtifactBusy`
- `tests/architecture/test_schema_versioning.py:208`, `:230` -- the refusal of a newer store and the no-op re-open; cited, not changed
- `pm_ai/surfaces/cli/dispatch.py:1842` -- `dispatch`, the daemon calls it with a `Context` whose `out`/`err` are this request's `io.StringIO`s (`4q` threads them through `Context` across the ~41 `print(` sites; never `contextlib.redirect_stdout`, which is process-wide); `:366` `Context`; `:547` `Command`; `:113` `EXIT_USAGE`
- `pm_ai/surfaces/api/app.py` -- `4e`'s `create_app`; the three endpoints and the stream join it
- `pm_ai/app/daemon.py` -- `4e`'s lifecycle; gains the executor, the lock, the straggler pass and the prune at start, the daily prune, and the shutdown order
- `pm_ai/app/wiring.py:178` -- `Daemon.clock`, the clock every row's timestamp comes from
- `pm_ai/core/jobs.py` -- AD-20's idempotency keys; not used by this table, which mutates nothing external
- `pm_ai/connectors/registry.py:84` -- `run_bounded`, the existing abandon-at-deadline thread; the executor is separate and does not replace it
- `tests/architecture/README.md:78`, `:124` -- the AD-21 row; AD-19 "needs load testing"

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/app/pipelines.py` -- `run_dashboard` declares its bound (the per-connector budget, connectors in sequence) beside itself; `connector check` declares its ten seconds -- gate F7
- [ ] `pm_ai/core/dispatch.py` -- `plan()` reading declared bounds; the boundary at exactly 5.0 s -- un-skips the AD-21 test
- [ ] `pm_ai/storage/service.py` -- `requests` in `_SCHEMA`, no version bump; writer methods: create a job (raising `ArtifactBusy` through), move its state with `UPDATE … WHERE state = ?`, read one, fail stragglers, prune by age -- AD-20
- [ ] `pm_ai/app/daemon.py` -- the four-worker executor and the writer lock; the straggler pass and prune before listening; the daily prune; the shutdown order; the `dashboard` job and inline commands bound to the daemon's writer, clock and per-request `StringIO`s -- AD-19
- [ ] `pm_ai/surfaces/api/app.py` -- `/v1/prompt` with the `line` check, `/v1/jobs/<id>`, `/v1/jobs/<id>/events` over the framework's streaming response; `503` with `Retry-After` on a refused row; the duplicate-job answer -- the endpoints
- [ ] `tests/` -- one test per matrix row, including a writer wrapper that refuses on create (asserting `503`, never `202`), a straggler that settles after `failed`, and `plan(4.99)` / `plan(5.0)`; a slice test that starts the daemon on a free port in a throwaway home, posts `dashboard`, reads the row before the `202` returns, and reads the stream to `done` -- the matrix is the contract
- [ ] `tests/architecture/README.md` -- AD-21 row no longer skips; AD-19 row names the executor as built, load test still owed

**Acceptance Criteria:**
- Given `4e`'s daemon running, when a client posts `{"line": "dashboard"}` with the token, then it receives `202` and a job id, the `requests` row exists, and the events stream ends with `done` and the dashboard's path.
- Given `4e`'s daemon running, when a client posts `{"line": "config show"}`, then it receives `200` with the same text `pm-ai config show` prints and `exit` 0, and the daemon's console shows none of it.
- Given a `requests` row left `running` and the daemon started again, when the row is read, then it is `failed` with the reason that the daemon stopped.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count falls by one against the count recorded after `4e`: the AD-21 test runs.

## Design Notes

- **The stream without a new package.** `StreamingResponse` with `media_type="text/event-stream"` and `data: {json}\n\n` frames is enough for one consumer reading one job; a reader that reconnects simply opens the stream again and, if the job is finished, gets the final event.
- **Why the row precedes the acknowledgement.** A `202` that names a job the database does not hold yet is a promise the next crash breaks; writing first is the ordinary case of AD-20's intent-then-settle, not a special one — and a `503` with `Retry-After` is the honest answer when the row cannot be written.
- **Conditional transitions.** `UPDATE requests SET state = ? … WHERE id = ? AND state = ?` is what keeps a straggler finishing after the shutdown pass from turning `failed` back into `done`; the update reports zero rows and the worker logs that it was overtaken.
- **The executor and the lock.** `concurrent.futures.ThreadPoolExecutor(max_workers=4)` and one `threading.Lock` on the `Daemon`; `check_same_thread=False` at `service.py:582` is not a concurrency guarantee. `9a` and `9b` take both from the `Daemon` rather than creating their own.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass, one skip fewer than after `4e`
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
- `HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai daemon run`, then `curl -s -H "Authorization: Bearer $(cat <scratch>/.pm-ai/private/api_token)" -H "Content-Type: application/json" -d '{"line":"dashboard"}' http://127.0.0.1:8747/v1/prompt` -- expected: `202` with a job id; `sqlite3 <scratch>/.pm-ai/private/operational.db 'select id,state from requests'` shows it; the same with `{"line":"config show"}` -- expected: `200` with `output` and `exit`
