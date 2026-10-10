---
title: 'The prompt runs the commands the daemon owns through the daemon'
type: 'feature'
created: '2026-10-10'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** After `4f`, every command typed at the prompt composes its own daemon in the prompt's process. Once `4e`'s daemon runs, that is a second writer beside the one AD-5 names, a second connector inventory beside the one `connector check` is meant to probe, and a prompt that cannot receive anything the daemon delivers later.

**Approach:** The prompt becomes a client of the running daemon (AD-7, AD-8). A typed command that does not need this terminal goes to the daemon's prompt endpoint (`4r`) and its output and exit code come back; a command that needs this terminal keeps running locally. A line through the daemon is on the daemon's request path (AD-21, 2026-10-10): when its declared bound exceeds five seconds the daemon acknowledges with a job id and the result follows on the same session. Depends on `4e`, `4f` and `4r`; every endpoint and response field named here is `4r`'s.

## Boundaries & Constraints

**Always:**
- **It is a client of `4e`'s daemon.** It reads `4e`'s token file on each request, never at open, so a daemon started after the prompt opened is reached without restarting. Requests go to 127.0.0.1 only, through the standard library's HTTP client; the contract forbidding third-party HTTP clients in surfaces stays, and the prompt still opens within 1.0 second, composing nothing (`4f`'s tests keep holding).
- **One rule decides where a fixed command runs.** A command that needs a terminal of its own — it asks questions or reads a secret: `goal set`, `setup`, `connector add`, `connector sign-in` (once `8m` lands), `key enrol` — runs in this process as `4f` runs it; so do `doctor` and `project add`, which the command table marks as needing no daemon. Every other fixed command goes through the daemon's prompt endpoint. Either way the words, the output and the `exit <n>` status line are the same as `pm-ai <words>` on the command line. `project add` or `connector add` typed while the daemon runs act locally: a connector added is seen at the daemon's next harvest cycle; a project added needs a daemon restart in this wave (`4e`), and the answer says so.
- **The endpoint and its answer are `4r`'s.** The line goes to `4r`'s prompt endpoint as text. An inline answer carries the command's output (standard output and standard error, in the order written) and its exit code; otherwise it is an acknowledgement with a job id. This slice owns none of those fields. An open natural-language line is still answered in the prompt's process and never sent (`4f`).
- **The daemon runs the line through the same command table against its own daemon.** It resolves the line's `--scope` for each request against the projects it watches, in story 4m's order with no folder step (the daemon runs in no folder of the person's): a named project, else the only project; a project it does not watch is refused by name in the answer, `exit 3`. Each request writes to output streams of its own, so two lines at once never mix their output. An inline command runs in `4r`'s worker pool, off the daemon's event loop. A command whose declared bound exceeds five seconds is a job (AD-21, 2026-10-10): `dashboard` and `connector check` (ten seconds, CAP-35) are acknowledged; `config show` and `help` answer inline.
- **No daemon is stated, every time, and the prompt still opens.** A line that needs the daemon and finds none — no token file, connection refused — gets one sentence naming `pm-ai daemon run` and the status line `exit 1`. A token the daemon rejects gets one sentence saying the token file and the daemon disagree and to restart the daemon, `exit 1`. A status the client does not know — 404 from an older daemon, any 5xx, or a body that is not JSON — gets one sentence naming the status, `exit 1`. Local commands still run; the session continues.
- **Ctrl-C while waiting on the daemon abandons the wait,** says so, and the daemon finishes the command on its own; nothing is retried. The session continues.
- **An acknowledgement is shown and the result follows on this session** (AD-21). The job id is shown and the prompt returns; the result is printed when it arrives over `4r`'s stream, prefixed by the id — only between lines, at the next prompt, never into a line half typed. Two jobs in flight are listed by id when the second is acknowledged. `exit` with a job running names the id and says it continues in the daemon. The thread reading the stream only waits for a job the daemon owns; the prompt schedules nothing (AD-7).
- **A local command composes as it does today,** under the cross-process lock AD-5 names; whether that lock survives the daemon is `4e`'s decision, inherited here.
- **Parity with Telegram is not claimed.** Story 5 does not exist.

**Ask First:** Nothing. The routing of each command is stated above; a new command added later goes through the daemon unless it is named as needing this terminal.

**Never:** No third-party HTTP client in surfaces. No new exit code. No second command table. No command to read a finished job's result after the session ended; `4r`'s job-status endpoint is not surfaced here. No sending an open line to the daemon. No change to `4f`'s startup budget. No process-wide redirection of standard output or standard error in the daemon.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Inline via daemon | `config show`, daemon running | the daemon answers; same output as the command line; `exit 0`; nothing written | N/A |
| Job via daemon | `dashboard --scope personal`, daemon running | acknowledgement and job id; prompt returns; the daemon renders; output and `exit 0` printed with the id when it arrives | render failure → its sentence and `exit 3` with the id |
| Usage error via daemon | `dashboard --bogus` | the same usage text; `exit 2` | session continues |
| Unwatched project | `dashboard --scope project:gamma`, gamma not enrolled | refused by name in the answer, naming the watched projects; `exit 3` | session continues |
| Terminal-bound command | `goal set` | runs in this process, asks its questions here; never sent | session continues |
| Local command | `doctor` | runs in this process, as in `4f` | session continues |
| Enrolment while the daemon runs | `project add <path> beta` | runs locally; the answer says the daemon sees beta after a restart | session continues |
| Local secret command | `connector add gitlab alpha` | credential asked at this terminal; never sent; seen by the daemon at its next cycle | session continues |
| Connector probe | `connector check`, one connector enrolled | acknowledged (ten-second bound); the daemon's own connectors are probed, not a second inventory; that instance's row follows with the id | session continues |
| No daemon | `dashboard`; no token file or connection refused | one sentence naming `pm-ai daemon run`; `exit 1` | session continues |
| Token rejected | the daemon answers 401 | one sentence: token file and daemon disagree; restart the daemon; `exit 1` | session continues |
| Unknown answer | 404, 5xx, or a body that is not JSON | one sentence naming the status; `exit 1` | session continues |
| Daemon started later | prompt opened before `pm-ai daemon run` | the next line reaches it, no restart | N/A |
| Token file unreadable | wrong mode or owner | one sentence naming the file; nothing sent | session continues |
| Ctrl-C while waiting | a daemon-routed command in flight | wait abandoned, one sentence; the daemon finishes | session continues |
| Result while typing | a result arrives with a line half typed | printed at the next prompt, never into the typed line | N/A |
| Two jobs | a second `dashboard` acknowledged | both ids listed | N/A |
| Stream lost | the stream ends before the result | one sentence naming the job id; prompt returns | session continues |
| Exit with a job pending | `exit` after an acknowledgement | names the id; says it continues in the daemon | exit 0 |
| Two lines at once in the daemon | two prompt requests overlapping | each answer holds only its own output | N/A |
| Open line | `what did I promise Laura` | `4f`'s sentence; no request made | N/A |
| Startup unchanged | `pm-ai` under a pseudo-terminal | `4f`'s 1.0 s test still passes; no request at open | fails over 1.0 s |

</frozen-after-approval>

## Code Map

- `pm_ai/surfaces/cli/repl.py` -- `4f`'s loop; gains the routing and the acknowledgement follow-up. Takes a structural type for the daemon client beside `4f`'s local runner; imports nothing from `pm_ai.app` (`tests/surfaces/test_cli_dispatch.py:470`)
- `pm_ai/surfaces/cli/loopback.py` -- new: standard-library HTTP to 127.0.0.1 carrying the token, read from the file on each request; the outcomes as values — an answer with output and exit code, an acknowledgement with a job id, no daemon, token rejected, unknown answer. `4r`'s `POST /v1/prompt` and `GET /v1/jobs/<id>/events` and `4e`'s token file are referenced from those specs, not restated
- `pm_ai/surfaces/cli/dispatch.py:366` -- `Context`, which gains `out` and `err` streams (defaulting to the process's); `:547` `Command`, whose `target` of `None` means "needs no daemon" (`doctor`, `setup`, `project add`); `:1583` `TABLE`; `:1658` `_HELP_FLAGS`; `:1748` `parse`; `:1842` `dispatch`, which the daemon side calls with its own `Context`. At `ddcb9aa`, `grep -c "sys.stdout\|sys.stderr\|print(" pm_ai/surfaces/cli/dispatch.py` counts 42 sites writing to the process streams
- `pm_ai/surfaces/api/app.py` -- `4r`'s prompt endpoint; the per-request `--scope` resolution and the per-request streams land there. `pm_ai/core/dispatch.py` -- `4r`'s `plan()`, which reads each pipeline's declared bound from `app`
- `pm_ai/app/entry.py:122` -- `main`: `4f`'s no-arguments branch gains the client, built from the token path (`ScopePaths.production`, `:923`); `:146` `_compose`, what a daemon-routed command no longer does in this process; `:138-141` the comment on story 4m's order, which the daemon half follows without the folder step
- `pm_ai/storage/service.py:582` -- `check_same_thread=False` is not a concurrency guarantee; `4r`'s daemon-level lock serialises the workers' calls into the one writer, and this slice adds no thread of its own in the daemon
- `.importlinter:62` `http-confined-to-adapters` -- stays; a comment records that the loopback client is the standard library. `:148` `cli-owns-no-scheduling` -- the stream-reading thread in the prompt waits on a job; it imports no scheduler and starts no timer, so the contract holds
- `tests/surfaces/test_cli_subcommands.py:850-860` -- the `isatty` monkeypatch pattern; `tests/slice/test_repl_startup.py` -- `4f`'s timing test, which must keep passing

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- `Context` gains `out: TextIO` and `err: TextIO` (defaults `sys.stdout`, `sys.stderr`); rewrite the 42 sites the grep above counts to write to them, including `Answered.deliver` at `:1740` and the refusal at `:1950`; never `contextlib.redirect_stdout` or `redirect_stderr`, which are process-wide — a test greps `pm_ai/` for both names and fails on a hit -- per-request output
- [ ] `pm_ai/surfaces/api/app.py` -- in `4r`'s prompt endpoint: resolve `--scope` per request against the daemon's watched projects (named, else the only one; unwatched refused by name); run the table with fresh `StringIO` streams in a `Context` of its own; an inline command runs in `4r`'s worker pool -- the daemon half
- [ ] `pm_ai/surfaces/cli/loopback.py` -- the client: token read per request, prompt request, stream follow-up, the outcomes as values including the unknown-answer one -- one place speaks to the daemon
- [ ] `pm_ai/surfaces/cli/repl.py` -- routing by `Command.target` plus the five terminal-bound names; the `exit 1` sentences for no daemon, token rejected and unknown answer; Ctrl-C while waiting; results printed only at the next prompt; jobs listed by id; the exit-with-job sentence -- the contract
- [ ] `pm_ai/app/entry.py` -- build the client from the token path and inject it; `project add`'s answer says the daemon sees the project after a restart -- nothing composed at open
- [ ] `tests/surfaces/test_cli_repl.py` -- one test per matrix row with a stand-in daemon client -- the matrix is the contract
- [ ] `tests/slice/test_repl_through_daemon.py` -- a real daemon in a throwaway home with one connector enrolled: `connector check` typed at the prompt is acknowledged and that instance's row follows; `dashboard` typed at the prompt is acknowledged, the `requests` row exists, the render arrives with the id, and the prompt's process composed no daemon; two overlapping prompt requests hold only their own output -- the job path is real

**Acceptance Criteria:**
- Given `4e`'s daemon running with a project enrolled, when `pm-ai` is typed and then `dashboard`, then an acknowledgement and a job id show, the daemon renders, the command-line output and `exit 0` follow with the id at the next prompt, and the prompt's process composed no daemon of its own.
- Given the daemon running with one connector enrolled, when `connector check` is typed, then that instance's row arrives from the daemon's own connectors, not from a second inventory.
- Given no daemon running, when `doctor` is typed at the prompt, then it runs in this process and prints its report, and `dashboard` typed next names `pm-ai daemon run` with `exit 1`.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged by this slice; `4e` and `4r` state their own deltas.

## Design Notes

Routing reads the command table's own `target` rather than a second list; the five that need this terminal are named once, so a command added later goes through the daemon by default — the safe side of AD-5. The standard library's HTTP client rather than `httpx`: the surfaces contract names third-party clients, and AD-8 makes the CLI a loopback client. Streams are threaded through `Context` because `redirect_stdout` swaps the whole process's stream, and two requests in `4r`'s pool would read each other's output. The stream-reading thread does not violate `cli-owns-no-scheduling`: it waits for one job the daemon already owns and decides nothing about when work runs. Concurrency in the daemon is `4r`'s (one pool of four, one writer lock); this slice submits to it and creates no thread there.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; the skip count unchanged by this slice
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
- `HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai daemon run` in one terminal and `.venv/bin/pm-ai` in another, then `config show` and `dashboard` -- expected: `config show` answers inline with `exit 0`; `dashboard` shows a job id, the daemon's terminal shows the render, and the output with the id and `exit 0` appears at the next prompt
