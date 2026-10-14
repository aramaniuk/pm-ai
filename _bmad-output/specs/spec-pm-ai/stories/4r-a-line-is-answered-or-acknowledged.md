---
title: 'A line is answered at once or acknowledged with a job'
type: 'feature'
created: '2026-10-10'
status: 'done'
baseline_commit: '238430e3df3cbcec282b2e91db54e66f520d7f00'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `4e`'s daemon runs and reports its health, but nothing can ask it to do work. The architecture's rule for the daemon's request path (AD-21, narrowed by D5 on 2026-10-09 and restated on 2026-10-10: the request path is a transport, so a command's words sent through the daemon deliver the same output the terminal would) is that anything over five seconds answers at once with a job id and delivers later — and that every such job is a durable row, never a memory the next restart loses (AD-20). No endpoint takes a line, no table holds a job, and the rule's own test has skipped since the suite was written.

**Approach:** The daemon gains a prompt endpoint that takes one text line and answers inline or with a job id; a job-status endpoint; and a stream a client reads the result from. A job is a row in the operational database before the acknowledgement goes back, and runs on the daemon's I/O pool of four threads, off its loop, handing every write to the daemon's one writer thread (AD-5 and AD-19 as amended 2026-10-14). The one slow command in this build is `dashboard`, so the job path is real. Depends on `4e`; `1r` lands before. `4q`, the REPL as a client of the daemon, depends on `4e`, `4f` and this slice and owns which commands the REPL sends here.

## Boundaries & Constraints

**Always:**
- **Three endpoints, all under `4e`'s token and address.** `POST /v1/prompt` takes `{"line": "..."}` and answers `200 {"inline": true, "output": "...", "exit": n}` — the command's printed text, standard output then standard error, and its exit code — or `202 {"inline": false, "job_id": "..."}`. These fields are the contract `4q` cites and owns none of. `GET /v1/jobs/<id>` returns the job's row; `GET /v1/jobs/<id>/events` delivers the job's state changes as they happen over one response that stays open and closes after the last one; a job already finished sends that one event and closes. An unknown id is `404` on both.
- **What a line is.** The words of a fixed `pm-ai` command. Any command that does not need a terminal of its own runs here (`4q`'s routing rule: those that ask questions or take a secret — `goal set`, `setup`, `connector add`, `connector sign-in`, `key enrol` — are refused by name); `help` lists the commands, inline. A line whose first word is no command is answered inline with one sentence saying so and naming `help`, exit 2. Each request gets its own output buffers: nothing a command prints can reach another request or the daemon's console.
- **The five-second rule (D5, AD-21).** Every pipeline declares its bound once, beside itself in the composition root; the decision is one function in the I/O-free core that reads that declaration: under five seconds the line is answered inline, at five seconds or over it is acknowledged with a job id and run later. The pre-written AD-21 test passes against it. `dashboard`'s bound is the per-connector fetch budget with connectors fetched one after another, so `dashboard` sent through the daemon is the real job, not a stand-in.
- **A job is a row first.** The row — id, kind, scope, state, result, created and updated times — is in a `requests` table in `operational.db` before the `202` is sent (AD-20); nothing is remembered in memory only. If the row cannot be written — the writer thread has not taken the step within one second, or the step is refused busy (a claim held, or `operational.db` still busy after the daemon's 100 ms wait) — the answer is `503` with `Retry-After: 1` and no job id. A step the request stopped waiting for is withdrawn if the writer thread has not started it, so no row appears for a request that was told `503`; one already started is short and is awaited to its end. States: `queued`, `running`, `done`, `failed`. A state change is one statement that applies only to the state it expects to find, and its count of changed rows is checked: zero means another step got there first, so a job finishing late can never overwrite `failed`. A job id is `job_` plus random hex. `result` holds the command's output and exit code, or on failure pm-ai's own sentence plus the error's type name — never the raw text of an exception, which from a provider can carry a token or a continuation link. The table is created on open like every table; the store's version number does not change. Rows older than seven days are removed at start and once a day after; story 10 widens the table into the durable queue.
- **One job per kind and scope at a time.** A second `dashboard` for a scope whose job is still `queued` or `running` answers `202` with the running job's id. A render through this path records its instant like any other (`9b`'s rule).
- **A restart does not silently re-run anything.** At start, rows still `queued` or `running` from an earlier run are marked `failed` with the reason that the daemon stopped before they finished — or, when the diagnostic log holds the schema-version-mismatch line for that job, "the daemon stopped on a schema-version mismatch; see the diagnostic log". Re-running is story 10's.
- **One event loop, one writer thread, one I/O pool (AD-5, AD-19).** The loop only waits and schedules; nothing on it blocks — no keychain call, subprocess, file or SQLite access, writer call or claim. The daemon has one writer thread, which makes every write and enters every claim, and one I/O pool of four threads (a constant) that do not keep the process alive when it exits — never `asyncio.to_thread` or the default executor. Jobs run on the I/O pool, and so does every inline command; a fifth job waits `queued`. Neither the loop nor a worker calls the writer directly: it hands a claimed step to the writer thread and waits for the answer. `9a`'s harvests and `9b`'s renders use the same writer thread and pool; the only other threads in the daemon are `run_bounded`'s and the request log's (next bullet). AD-19's model pool is for model work only.
- **`4e`'s status handler and its request log line move off the loop.** The status probes (the keychain fetch, the `git` subprocess) run on the I/O pool and the loop awaits them; the request log line is put on a queue the loop never waits on and written to the file by one listener thread. The status body is unchanged.
- **A job is a sequence of steps (AD-5).** A bounded step — anything with a deadline: a calendar read, a provider call, a `git` query — runs on an I/O worker, takes no claim and writes nothing. A claimed step runs on the writer thread and is handed over whole: a claim, and every `operational.db` transaction, opens and closes inside one claimed step. A claimed step is short and never waits: no network, keychain or `git` call, no sleep, no sanitization, normalization, extraction or model work (prepared on the worker; the step only saves the prepared bytes); it tries a claim once, and a held claim refuses it as `ArtifactBusy`. Each claimed step leaves storage complete without the steps after it. A deadline may abandon only a bounded step; a job stopped at one is marked `failed`, naming the last claimed step it finished. A writer call made from the writer thread itself runs at once, not queued; no code on the writer thread waits on the I/O pool, the model pool or the loop. A plain read that is not the read half of a read-modify-write is not a writer call: it runs on the worker, on its own read-only `operational.db` connection or as a plain file read.
- **`operational.db` from the writer thread (AD-5).** The writer's connection runs with `autocommit=True`; each claimed step that touches `operational.db` opens `BEGIN IMMEDIATE`, ends with `COMMIT` or `ROLLBACK`, and checks that no transaction is open when it returns, so a busy refusal undoes only that step. The daemon's busy wait is at most 100 ms; the commands that still write keep the standard library's five seconds. The schema-version check is the first statement inside each `BEGIN IMMEDIATE`. When the store has moved past the version the daemon knows, the daemon stops and writes nothing more to the store: it records the version found in the diagnostic log (`~/.pm-ai/logs/`, AD-24) and prints it as its refusal (refused, exit code 3); the job it was running stays `running` for the next start's restart pass. `operational.db` is migrated only under its own claim, in every process that opens it (the migration code is shared), and only by a process the version check allows.
- **Fresh reads per line (AD-5, AD-7).** `config.toml`, the connector list, each connector row and credentials are read again at the start of every line the daemon answers and every job; enrolled projects are read at start only. `4q` states the refusal of a project enrolled since start.
- **The master key, once, at start (AD-5, AD-6).** The daemon asks the keychain on the writer thread before it listens. A locked keychain or a key it cannot read refuses the start (refused, exit code 3), naming it. A key not yet enrolled does not refuse the start: the daemon runs, and each step that would read or write an encrypted file is refused, naming `pm-ai key enrol`, with no further keychain call; a key enrolled after start takes effect at the next start, said in the shared restart sentence (below). With encryption turned off for the process, no key is asked for. After start no keychain call is made on the writer thread or inside a claim.
- **A refreshed credential is saved when it is issued, and this slice builds the path (AD-5, AD-39).** The jobs this slice runs that may refresh — `dashboard`'s calendar read, `connector check` — are the first in the daemon to do so, so `4r` builds it and `9a`'s harvest and `9b`'s render reuse it. Around the bounded call to the provider, the fetch hands the writer thread only two kinds of claimed step: a read of the stored credential, and a compare-and-save. The save is made only if the stored entry still equals the one the refresh started from; otherwise the new token is dropped and the lost race is logged. At most one refresh per connector instance is in flight in the daemon. In the daemon the credential store handed to a connector cannot write by itself. A busy refusal is retried by the handing worker after the sealed store's interval, never by a sleep on the writer thread. A save handed over by a fetch that a deadline has since abandoned is still made while the writer is open; once it has closed, the save is refused and logged.
- **One shared restart sentence, owned here (AD-5).** Every command whose change waits for a daemon restart says so in one sentence — "A daemon already running sees this after it is restarted (`pm-ai daemon run`)." — kept as one constant beside the command table. `project add` and `key enrol` print it every time they succeed, wherever they run; `9a` and `4q` print it, never a sentence of their own.
- **`connector check` through the daemon never probes a project the daemon did not load.** A connector row filing into a project enrolled since the daemon started is listed by name with the shared restart sentence instead of being probed; the other rows are probed as usual.
- **The job writes through the daemon's writer**, the one `4e` gave it for its lifetime, entered only on the writer thread, under `1r`'s one cross-process claim the CLI uses this wave (AD-5). A claimed step that meets that claim held fails this attempt with the claim named in its result — never silently; the retry ladder is `9a`'s.
- **Shutdown, stated once here and cited by `9a` and `9b` (AD-5):** stop listening; cancel the timers. From then on the writer thread accepts no new claimed step except a credential save and shutdown's own step that marks still-running jobs `failed`. It finishes the step it is running and every step already queued to it, with no time limit. Jobs whose bounded step is still running are marked `failed` with the reason that the daemon stopped, naming the last claimed step each finished; a claimed step they hand over later is refused and logged by name. Then the writer closes and the daemon exits 0. The daemon waits for the writer at the very end of its own stop sequence — in the `finally` that closes the store, before the store is closed, never in the app's lifespan — where a second Ctrl-C cannot skip it. A stop on a schema-version mismatch skips the marking.
- **Every claimed step is safe to kill partway (AD-5).** A second interrupt, or a supervisor's kill, ends the process at once. So ledgers only append, files are published by rename, and the rows that belong together are written in one transaction, last; a forced stop leaves the claim released, the open transaction rolled back when the store is next opened, and each file whole, old or new. A test kills a claimed step partway and checks that nothing is half-written.
- **The stream adds no dependency.** It is the web framework's own kept-open response with the event-stream content type; `sse-starlette` is in the lockfile only because `mcp` pulls it in, and stays undeclared.

**Ask First:** Any endpoint path or response field other than the ones named above, once `4q` is built against them.

**Never:**
- No job retry, no cancellation, no listing; no result kept longer than the seven-day prune — story 10 decides retention.
- No second table, no in-memory queue, no job timer that is not backed by a row.
- No scheduler, no harvest as a job yet (`9a`), no model (story 7).
- No change to `4e`'s token, address, status response or settings key; the status handler only moves off the loop.
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
| Writer refuses the row | the writer thread not free within 1 s, or a claim held or the database busy past 100 ms when the row is created | `503`, `Retry-After: 1`, no job id; the withdrawn step never creates the row | never a `202` |
| Duplicate job | `dashboard` twice, the first still running | the same job id both times | N/A |
| Job status | known id / unknown id | `200` with the row / `404` | N/A |
| Job events, live | stream opened on a queued job | one event per state change; closes after `done` or `failed` | unknown id → `404` |
| Job events, finished | stream opened on a `done` job | that one event, then close | N/A |
| Client leaves mid-stream | the reader disconnects | the job keeps running; its result is in the row | N/A |
| Pool full | five jobs of different kind or scope at once | four `running`, one `queued` until a slot frees | N/A |
| Late finish | a job whose claimed step arrives after it was marked `failed` | the step is refused and logged by name; the row stays `failed` | the conditional update changes zero rows |
| Unfinished at restart | a `running` row, daemon starts | row becomes `failed`, reason says the daemon stopped | the diagnostic log holds the mismatch line for it → the reason names the schema-version mismatch and the log |
| Old rows | a `done` row eight days old, daemon starts | the row is gone | N/A |
| No token | any of the three without the header | `401`, as `4e` | N/A |
| Planning rule | bounds of 1 s and 42 s; 4.99 s and 5.0 s | inline / a job id — the AD-21 test; inline / a job id — the boundary | N/A |
| Stop | Ctrl-C with one job in its calendar read, one claimed step running and one queued | timers cancelled; the running and queued claimed steps finish, however long; the job in its read is marked `failed` naming its last finished step; its later claimed step refused and logged; writer closed; exit 0 | N/A |
| Stop during a credential save | Ctrl-C while a refresh's compare-and-save is handed over | the save is made, then the writer closes | after close, a save is refused and logged |
| Forced stop | a second Ctrl-C, or a kill, during a claimed step | the process ends at once; no file half-written, no half transaction; the next start marks the job `failed` | the kill test |
| Slow status probe | the keychain or `git` probe slow during `GET /v1/status` | a `POST /v1/prompt` sent meanwhile is answered; the loop never waits on the probe | N/A |
| Keychain locked at start | the key cannot be read | refused, exit code 3, naming the keychain; nothing listens | N/A |
| No key enrolled | encryption on, no key | the daemon starts; a step reading or writing an encrypted file is refused naming `pm-ai key enrol`; no further keychain call | N/A |
| Encryption off | `PM_AI_DISABLE_ENCRYPTION=1` | no key asked for | N/A |
| Token refreshed in a job | `dashboard`'s calendar read refreshes a Graph token | saved by compare-and-save as soon as it is issued; one refresh per instance in flight | stored entry changed since it was read → the new token dropped, the lost race logged |
| Check meets a later project | `connector check`, one row filing into a project enrolled after the daemon started | that row named with the shared restart sentence, not probed; the other rows probed | N/A |
| Restart sentence | `project add` or `key enrol` succeeds | the shared restart sentence printed, every time | N/A |
| Schema | an `operational.db` at version 1 | opens with the `requests` table, migrated under its own claim; still version 1 | a later version refuses to open, as today |
| Schema moved while running | another process migrates past the daemon's version | the next claimed step's check stops the daemon: nothing written, the version in the diagnostic log and printed, exit code 3; the job stays `running` | the next start marks it `failed` naming the mismatch |

</frozen-after-approval>

## Code Map

- `pm_ai/core/dispatch.py` -- new: `plan(estimated_seconds)` → `.inline` / `.job_id`; `tests/architecture/test_domain_invariants.py:346-352` is the pre-written test it un-skips. `core` mints the id and decides; the row is the writer's
- `pm_ai/app/pipelines.py:253` -- `run_dashboard(daemon, scope=, now=)`, the one job; its bound is declared beside it and `plan()` reads it; `entry.py:261` `_dashboard` shows how it is bound to the daemon's clock
- `pm_ai/storage/service.py:103-146` -- `_SCHEMA`, `SCHEMA_VERSION = 1`; `:148-157` the rule that a new table is created on open without a version bump (`8a` did the same); `:582` `sqlite3.connect(…, check_same_thread=False)`, which serializes nothing and today runs the library's implicit transactions with its five-second busy wait (the daemon's writer connection becomes `autocommit=True` with a 100 ms wait); `_migrate`, which takes no claim today; `begin_execution` `:1837` / `settle_execution` `:1861` as the write-then-settle shape to copy; `exclusive` `:1302` raises `ArtifactBusy`
- `tests/architecture/test_schema_versioning.py:208`, `:230` -- the refusal of a newer store and the no-op re-open; cited, not changed
- `pm_ai/surfaces/cli/dispatch.py:1842` -- `dispatch`, the daemon calls it with a `Context` whose `out`/`err` are this request's `io.StringIO`s (`4q` threads them through `Context` across the ~41 `print(` sites; never `contextlib.redirect_stdout`, which is process-wide); `:366` `Context`; `:547` `Command`; `:113` `EXIT_USAGE`
- `pm_ai/surfaces/api/app.py` -- `4e`'s `create_app`; the three endpoints and the stream join it; `read_status` runs the probes inline on the loop today
- `pm_ai/app/daemon.py` -- `4e`'s lifecycle; gains the writer thread, the I/O pool, the master key at start, the restart pass and the prune at start, the daily prune, and the shutdown; `serve`'s `finally` (which closes the store) is where the wait for the writer goes; `_RequestLog`, the file handler on the loop that moves to a queue
- `pm_ai/storage/crypto.py` -- `LazyKeyCrypto`, which fetches the key on first use; in the daemon the key is fetched once at start instead
- `pm_ai/app/wiring.py:178` -- `Daemon.clock`, the clock every row's timestamp comes from
- `pm_ai/core/jobs.py` -- AD-20's idempotency keys; not used by this table, which mutates nothing external
- `pm_ai/connectors/registry.py:84` -- `run_bounded`, the existing abandon-at-deadline thread; the I/O pool is separate and does not replace it
- `pm_ai/core/connector_enrolment.py` `replace_credential`, `pm_ai/connectors/graph/auth.py` `access_token` -- today a rotated credential is saved inside the fetch; in the daemon the read and the compare-and-save become claimed steps handed to the writer thread, built here
- `tests/architecture/README.md:78`, `:124` -- the AD-21 row; AD-19 "needs load testing"

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/app/pipelines.py` -- `run_dashboard` declares its bound (the per-connector budget, connectors in sequence) beside itself; `connector check` declares its ten seconds -- gate F7
- [x] `pm_ai/core/dispatch.py` -- `plan()` reading declared bounds; the boundary at exactly 5.0 s -- un-skips the AD-21 test
- [x] `pm_ai/storage/service.py` -- `requests` in `_SCHEMA`, no version bump; writer methods: create a job (raising `ArtifactBusy` through), move its state with `UPDATE … WHERE state = ?` checking the changed-row count, read one, fail stragglers, prune by age -- AD-20
- [x] `pm_ai/storage/service.py` -- the daemon's writer connection `autocommit=True` with a 100 ms busy wait; `BEGIN IMMEDIATE` … `COMMIT`/`ROLLBACK` per claimed step with the no-open-transaction check; the schema-version check as the first statement inside it; `_migrate` under `operational.db`'s own claim in every process -- AD-5
- [x] `pm_ai/app/daemon.py` -- the writer thread and the I/O pool of four threads that do not keep the process alive; the master key asked once on the writer thread before listening (locked or unreadable refused, exit code 3; not enrolled starts and refuses each encrypted step naming `pm-ai key enrol`; encryption off not asked); the restart pass and prune before listening; the daily prune; the shutdown, its wait for the writer in the stop sequence's `finally`; the schema-version stop; the `dashboard` job as steps (calendar read and the project copy's `git` question bounded, the file write and records claimed) and inline commands on the pool, bound to the daemon's writer, clock and per-request `StringIO`s, reading `config.toml`, connector rows and credentials fresh per line -- AD-5, AD-19
- [x] `pm_ai/connectors/graph/auth.py`, `pm_ai/core/connector_enrolment.py`, `pm_ai/app/wiring.py` -- in the daemon the credential store handed to a connector cannot write: its read and its compare-and-save are claimed steps handed to the writer thread, the save made when the provider answers, a lost race dropped and logged, one refresh per instance in flight; the busy retry is the handing worker's; `9a` and `9b` reuse it -- AD-5, AD-39
- [x] `pm_ai/surfaces/cli/dispatch.py` -- the shared restart sentence as one constant beside the command table, printed by `project add` and `key enrol`; `connector check` in the daemon names a row filing into a project enrolled since start with it instead of probing -- AD-5
- [x] `pm_ai/surfaces/api/app.py`, `pm_ai/app/daemon.py` -- the status probes on the I/O pool; the request log line through a queue and one listener thread -- AD-19's two exceptions closed
- [x] `pm_ai/surfaces/api/app.py` -- `/v1/prompt` with the `line` check, `/v1/jobs/<id>`, `/v1/jobs/<id>/events` over the framework's streaming response; `503` with `Retry-After` on a refused row; the duplicate-job answer -- the endpoints
- [x] `tests/` -- one test per matrix row, including a writer wrapper that refuses on create (asserting `503`, never `202`), a writer thread held past one second (asserting `503`), a straggler whose claimed step arrives after `failed`, a test that every claim and every write in the daemon runs on the writer thread, a kill of a claimed step partway (a child process killed mid-step; the store and files checked whole), and `plan(4.99)` / `plan(5.0)`; a slice test that starts the daemon on a free port in a throwaway home, posts `dashboard`, reads the row before the `202` returns, and reads the stream to `done` -- the matrix is the contract
- [ ] `tests/architecture/README.md` -- AD-21 row no longer skips; AD-19 row names the writer thread and the I/O pool as built, load test still owed; the `GET /v1/status` enforcement-gap row closed — OWED — the AD-21 and AD-19 rows are done (load test still owed, as the task says); the `GET /v1/status` gap row is not in the README but in the spine's "Still open" table, which is skill-derived and was not hand-edited — closing it needs a spine re-derive

**Acceptance Criteria:**
- Given `4e`'s daemon running, when a client posts `{"line": "dashboard"}` with the token, then it receives `202` and a job id, the `requests` row exists, and the events stream ends with `done` and the dashboard's path.
- Given `4e`'s daemon running, when a client posts `{"line": "config show"}`, then it receives `200` with the same text `pm-ai config show` prints and `exit` 0, and the daemon's console shows none of it.
- Given a `requests` row left `running` and the daemon started again, when the row is read, then it is `failed` with the reason that the daemon stopped.
- Given a job in its calendar read and a claimed step queued, when Ctrl-C arrives, then the queued step completes, the job in its read is `failed` naming its last finished step, its later step is refused and logged, and the daemon exits 0.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count falls by one against the count recorded after `4e`: the AD-21 test runs.

## Design Notes

- **The stream without a new package.** `StreamingResponse` with `media_type="text/event-stream"` and `data: {json}\n\n` frames is enough for one consumer reading one job; a reader that reconnects simply opens the stream again and, if the job is finished, gets the final event.
- **Why the row precedes the acknowledgement.** A `202` that names a job the database does not hold yet is a promise the next crash breaks; writing first is the ordinary case of AD-20's intent-then-settle, not a special one — and a `503` with `Retry-After` is the honest answer when the row cannot be written.
- **Conditional transitions.** `UPDATE requests SET state = ? … WHERE id = ? AND state = ?` is what keeps a straggler finishing after the shutdown pass from turning `failed` back into `done`; the update reports zero rows and the worker logs that it was overtaken.
- **The writer thread and the pool.** One writer thread on the `Daemon` replaces the earlier daemon-level `threading.Lock`: a lock nested against AD-5's claim could deadlock or run one job at a time, and worker threads sharing one SQLite connection let a busy rollback undo another thread's work. The I/O pool's threads must not keep the process alive at exit; `concurrent.futures.ThreadPoolExecutor` joins its threads at exit, so the pool is built from threads that do not, like `run_bounded`'s. `check_same_thread=False` at `service.py:582` is not a concurrency guarantee. `9a` and `9b` take both from the `Daemon` rather than creating their own.
- **Where the wait for the writer goes.** The writer is shut down and waited for in `serve`'s `finally`, before the store is closed, not in the app's lifespan: a second Ctrl-C skips the rest of the lifespan, and `asyncio.run` cancels tasks still awaiting. The writer finishes the steps already queued to it; it is not shut down with its queued steps cancelled.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass, one skip fewer than after `4e`
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
- `HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai daemon run`, then `curl -s -H "Authorization: Bearer $(cat <scratch>/.pm-ai/private/api_token)" -H "Content-Type: application/json" -d '{"line":"dashboard"}' http://127.0.0.1:8747/v1/prompt` -- expected: `202` with a job id; `sqlite3 <scratch>/.pm-ai/private/operational.db 'select id,state from requests'` shows it; the same with `{"line":"config show"}` -- expected: `200` with `output` and `exit`

## Spec Change Log

- **2026-10-14, frozen block amended by Andrei** (the architecture spine's concurrency amendment of the same day: AD-5, AD-6, AD-19, AD-21; decided by Andrei, re-derived here before any build). Amended: the daemon-level `threading.Lock` every worker took before calling the writer is replaced by one writer thread that makes every write and enters every claim, plus an I/O pool of four threads that do not keep the process alive; a job is a sequence of bounded steps (on a worker, no claim, no write) and claimed steps (on the writer thread, handed over whole, short, never waiting); the `503` now covers the writer thread not free within one second as well as a claim held or the database busy past the daemon's 100 ms wait, and a step the request stopped waiting for is withdrawn before it starts, so no orphan row follows a `503`; the ten-second shutdown wait is replaced by the spine's shutdown (claimed steps running or queued finish with no time limit, jobs still in a bounded step marked `failed` and their later steps refused and logged, the wait for the writer at the end of the stop sequence) and every claimed step is safe to kill, with a kill test; the status handler and request log line move off the loop. Added to this slice, which the spine left unowned: the master key asked once at start, `operational.db` migrated under its own claim, and the schema-version check first inside each claimed step's `BEGIN IMMEDIATE`, a mismatch stopping the daemon with nothing written and the restart pass naming it. Matrix rows "Writer refuses the row", "Late finish", "Unfinished at restart", "Stop" and "Schema" changed; seven rows added. Known-bad state avoided: a lock nested against the cross-process claim (deadlock, or one job at a time); worker threads sharing one SQLite connection so a busy rollback undoes another thread's work; a render or harvest writing after its job was marked `failed` and after the writer closed; a keychain dialog or `git` subprocess stalling the loop or the writer thread; a migration by an upgraded command landing under a daemon that keeps writing the old shape. KEEP: the three endpoints, their paths and response fields (`4q` builds against them); job ids `job_` plus random hex; the five-second rule and its one `plan()` function; the row before the `202`; one job per kind and scope; no re-run at restart; the seven-day prune; `Retry-After: 1`; the stream without a new package; every Never line. Decided the same day (routine, Claude): `4r` builds the credential read and compare-and-save path, which its `dashboard` and `connector check` jobs need first, and `9a` and `9b` reuse it; `4r` owns the shared restart sentence, printed every time by `project add` and `key enrol` and only printed by `9a` and `4q`; `connector check` through the daemon names a row filing into a project enrolled since start with that sentence instead of probing it; three matrix rows added.

## Suggested Review Order

**Entry point**

- Start here: one line in, inline answer or acknowledged job.
  [`prompts.py:305`](../../../../pm_ai/app/prompts.py#L305)

**The five-second plan**

- The one decision: under 5.0 s inline, at or over a job id.
  [`dispatch.py:64`](../../../../pm_ai/core/dispatch.py#L64)
- Every routable command's declared bound, read by the plan.
  [`entry.py:424`](../../../../pm_ai/app/entry.py#L424)
- Dashboard's bound: fetch budget per calendar, in sequence.
  [`pipelines.py:1102`](../../../../pm_ai/app/pipelines.py#L1102)

**The request path and job steps**

- Row written before the 202; create and submit shielded together.
  [`prompts.py:383`](../../../../pm_ai/app/prompts.py#L383)
- A job's body: start, run the command, settle done or failed.
  [`prompts.py:619`](../../../../pm_ai/app/prompts.py#L619)
- Conditional state moves; final moves retry until they land.
  [`prompts.py:660`](../../../../pm_ai/app/prompts.py#L660)
- One command with fresh reads and its own output buffers.
  [`prompts.py:722`](../../../../pm_ai/app/prompts.py#L722)
- Dashboard as steps: render and git bounded, save claimed.
  [`prompts.py:799`](../../../../pm_ai/app/prompts.py#L799)
- Each handed step named and noted for its job.
  [`prompts.py:818`](../../../../pm_ai/app/prompts.py#L818)
- Restart pass: unfinished rows failed, schema stops named.
  [`prompts.py:497`](../../../../pm_ai/app/prompts.py#L497)
- Shutdown pass: still-running jobs failed, naming their last step.
  [`prompts.py:545`](../../../../pm_ai/app/prompts.py#L545)
- The requests table, created on open, no version bump.
  [`service.py:187`](../../../../pm_ai/storage/service.py#L187)
- Row creation, deduplicated only for argument-free commands.
  [`service.py:1245`](../../../../pm_ai/storage/service.py#L1245)

**The writer thread and the I/O pool**

- One writer thread: ordered steps, withdrawal, stopping, halting.
  [`concurrency.py:103`](../../../../pm_ai/app/concurrency.py#L103)
- Awaiting a step from the loop; cancellation never reaches it.
  [`concurrency.py:203`](../../../../pm_ai/app/concurrency.py#L203)
- Running one step inside the store's step scope.
  [`concurrency.py:318`](../../../../pm_ai/app/concurrency.py#L318)
- Four daemon threads for bounded steps and client commands.
  [`concurrency.py:351`](../../../../pm_ai/app/concurrency.py#L351)

**The claimed-step transaction and the schema check**

- Commit on return, roll back on raise; stuck transactions halt.
  [`service.py:1141`](../../../../pm_ai/storage/service.py#L1141)
- First statement opens BEGIN IMMEDIATE with the version check.
  [`service.py:580`](../../../../pm_ai/storage/service.py#L580)
- The daemon's own connection: autocommit, 100 ms busy wait.
  [`service.py:1116`](../../../../pm_ai/storage/service.py#L1116)
- Schema moved or regressed: the refusals the writer halts on.
  [`service.py:718`](../../../../pm_ai/storage/service.py#L718)
- Preparing the store under its own claim, only at open.
  [`service.py:1080`](../../../../pm_ai/storage/service.py#L1080)

**The master key and start/stop order**

- Start order: log, port, writer, key, token, restart pass, serve.
  [`daemon.py:181`](../../../../pm_ai/app/daemon.py#L181)
- Request log attached first; the store always closed.
  [`daemon.py:136`](../../../../pm_ai/app/daemon.py#L136)
- Master key fetched once on the writer thread.
  [`daemon.py:417`](../../../../pm_ai/app/daemon.py#L417)
- No key enrolled: every encrypted step refused, keychain never re-asked.
  [`crypto.py:299`](../../../../pm_ai/storage/crypto.py#L299)
- Stop: drain the writer; a second interrupt exits at once.
  [`daemon.py:376`](../../../../pm_ai/app/daemon.py#L376)
- Halt line naming every unfinished job for the restart pass.
  [`daemon.py:349`](../../../../pm_ai/app/daemon.py#L349)
- Diagnostic log keeps exception types and frames, never their text.
  [`daemon.py:632`](../../../../pm_ai/app/daemon.py#L632)

**Credential compare-and-save and the refresh slot**

- The daemon's credential store: read and compare-and-save on the writer.
  [`wiring.py:2504`](../../../../pm_ai/app/wiring.py#L2504)
- The compare-and-save itself, under the credential file's claim.
  [`wiring.py:2619`](../../../../pm_ai/app/wiring.py#L2619)
- One refresh per instance, bounded by one fetch budget.
  [`wiring.py:2546`](../../../../pm_ai/app/wiring.py#L2546)
- Graph auth holds the slot from read to save.
  [`auth.py:922`](../../../../pm_ai/connectors/graph/auth.py#L922)
- Per-line fresh connectors; later projects named with the restart sentence.
  [`wiring.py:1002`](../../../../pm_ai/app/wiring.py#L1002)

**`project add` and `transcript drop` as steps**

- Every StorageService call classified: handed, plain read or refused.
  [`wiring.py:3489`](../../../../pm_ai/app/wiring.py#L3489)
- The handing proxy: git asked on the worker, write on the writer.
  [`wiring.py:3568`](../../../../pm_ai/app/wiring.py#L3568)
- A daemon whose store, meetings and skills hand their writes.
  [`wiring.py:3627`](../../../../pm_ai/app/wiring.py#L3627)
- Onboarding: reads bounded, one compare-and-save claimed step.
  [`wiring.py:3383`](../../../../pm_ai/app/wiring.py#L3383)

**Endpoints and the stream**

- POST /v1/prompt: 422 from the framework, 400 naming line.
  [`app.py:179`](../../../../pm_ai/surfaces/api/app.py#L179)
- The event stream: the framework's StreamingResponse, no new package.
  [`app.py:211`](../../../../pm_ai/surfaces/api/app.py#L211)
- Stream subscription inside the generator; re-read on a timeout.
  [`prompts.py:462`](../../../../pm_ai/app/prompts.py#L462)

**The restart sentence and the CLI's output**

- The one shared restart sentence, beside the command table.
  [`dispatch.py:2741`](../../../../pm_ai/surfaces/cli/dispatch.py#L2741)
- Commands that need a terminal, refused by name through the daemon.
  [`dispatch.py:2752`](../../../../pm_ai/surfaces/cli/dispatch.py#L2752)
- Per-request output streams without redirect_stdout.
  [`dispatch.py:161`](../../../../pm_ai/surfaces/cli/dispatch.py#L161)

**Tests**

- The request path in-process: matrix rows against a real store.
  [`test_line_answered_or_acknowledged.py:1`](../../../../tests/slice/test_line_answered_or_acknowledged.py#L1)
- The writer thread and pool rules, including cancellation.
  [`test_daemon_writer_thread.py:1`](../../../../tests/slice/test_daemon_writer_thread.py#L1)
- Review fixes, round one: bounds, refresh slot, doctor, streams.
  [`test_line_requests_hardening.py:1`](../../../../tests/slice/test_line_requests_hardening.py#L1)
- Review fixes, round two: dedup, final moves, kill through the writer.
  [`test_line_requests_round2.py:1`](../../../../tests/slice/test_line_requests_round2.py#L1)
- project add and transcript drop as steps; storage classification.
  [`test_project_add_and_drop_through_daemon.py:1`](../../../../tests/slice/test_project_add_and_drop_through_daemon.py#L1)
- Store rows, transactions, migration claim, kill partway.
  [`test_requests_store.py:1`](../../../../tests/slice/test_requests_store.py#L1)
- The real daemon in a throwaway home, end to end.
  [`test_daemon_requests_slice.py:1`](../../../../tests/slice/test_daemon_requests_slice.py#L1)
- The planning rule and the declared bounds.
  [`test_dispatch_plan.py:1`](../../../../tests/core/test_dispatch_plan.py#L1)
