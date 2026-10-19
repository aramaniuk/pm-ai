---
title: 'The prompt runs the commands the daemon owns through the daemon'
type: 'feature'
created: '2026-10-10'
status: 'done'
baseline_commit: '68fcd28228c4ee24396b995c6e844b98d24b1fc3'
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
- **One rule decides where a fixed command runs.** A command that needs a terminal of its own — it asks questions, reads a secret, or holds the terminal until Ctrl-C: `goal set`, `goal update` and `goal delete` (`4t`, 2026-10-17), `setup`, `connector add`, `connector sign-in` (once `8m` lands), `key enrol`, `logs` (`4w`, 2026-10-18) — runs in this process as `4f` runs it; so do `doctor` and `project add`, which the command table marks as needing no daemon. Every other fixed command goes through the daemon's prompt endpoint. Either way the words, the output and the `exit <n>` status line are the same as `pm-ai <words>` on the command line. `project add` or `connector add` typed while the daemon runs act locally. A connector added is seen at the daemon's next harvest cycle when it files into the personal scope or into a project the daemon loaded at start; one filing into a project enrolled since the start waits for a restart. A project added takes effect after a daemon restart, because the daemon reads enrolled projects only at start (AD-7), and so does a master key enrolled after the daemon started (AD-6). **Every command whose change waits for a restart says so in one shared sentence** — "A daemon already running sees this after it is restarted (`pm-ai daemon run`)." — owned by `4r` as one constant beside the command table and printed every time by `project add` and `key enrol` wherever they run; this slice prints it in the daemon's refusal of a project enrolled since the start (next bullet) and writes no sentence of its own.
- **The endpoint and its answer are `4r`'s.** The line goes to `4r`'s prompt endpoint as text. An inline answer carries the command's output (standard output and standard error, in the order written) and its exit code; otherwise it is an acknowledgement with a job id. This slice owns none of those fields. An open natural-language line is still answered in the prompt's process and never sent (`4f`).
- **The daemon runs the line through the same command table against its own daemon.** It resolves the line's `--scope` for each request against the projects it watches, in story 4m's order with no folder step (the daemon runs in no folder of the person's): a named project, else the only project; a project it does not watch is refused by name in the answer, `exit 3`, and one enrolled since the daemon started is refused with the shared restart sentence, `exit 3`. For every line the daemon reads `config.toml`, the connector rows and credentials afresh (AD-5), so its output matches the same command at a terminal; its projects are the ones it loaded at start. Each request writes to output streams of its own, so two lines at once never mix their output. An inline command runs on `4r`'s I/O pool, off the daemon's event loop, and hands every write to `4r`'s writer thread. A command whose declared bound exceeds five seconds is a job (AD-21, 2026-10-10): `dashboard` and `connector check` (ten seconds, CAP-35) are acknowledged; `config show` and `help` answer inline.
- **No daemon is stated, every time, and the prompt still opens.** A line that needs the daemon and finds none — no token file, connection refused — gets one sentence naming `pm-ai daemon run` and the status line `exit 1`. A token the daemon rejects gets one sentence saying the token file and the daemon disagree and to restart the daemon, `exit 1`. A `503` with `Retry-After` — `4r`'s answer when its writer thread is not free within one second or the job's row was refused busy (AD-5) — gets one sentence saying the daemon is busy and to try again in the seconds it names, `exit 1`; nothing is retried. A status the client does not know — 404 from an older daemon, any other 5xx, or a body that is not JSON — gets one sentence naming the status, `exit 1`. Local commands still run; the session continues.
- **Ctrl-C while waiting on the daemon abandons the wait,** says so, and the daemon finishes the command on its own; nothing is retried. The session continues.
- **An acknowledgement is shown and the result follows on this session** (AD-21). The job id is shown and the prompt returns; the result is printed when it arrives over `4r`'s stream, prefixed by the id — never into a line half typed. Since `4v`, with `4u`'s line editor, it is printed above the prompt as soon as it arrives and the half-typed line is drawn again intact; held while a local command or an inline wait owns the terminal; without the line editor, at the next prompt. Two jobs in flight are listed by id when the second is acknowledged. `exit` with a job running names the id and says it continues in the daemon. The thread reading the stream only waits for a job the daemon owns; the prompt schedules nothing (AD-7).
- **A local command composes as it does today,** under the cross-process claim AD-5 names — the same claim the daemon takes, entered on its writer thread — so a local command and the daemon writing one file take turns, and the loser is refused as busy by name.
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
| Enrolment while the daemon runs | `project add <path> beta` | runs locally; the answer carries the shared restart sentence | session continues |
| Project enrolled since start | `dashboard --scope project:beta`, beta enrolled after the daemon started | refused, the shared restart sentence; `exit 3` | session continues |
| Key enrolled while the daemon runs | `key enrol` | runs locally; the answer carries the shared restart sentence | session continues |
| Settings edited while the daemon runs | `config.toml` edited, then `config show` | the daemon shows the edited values, the same as the terminal | N/A |
| Local secret command | `connector add gitlab alpha`, alpha loaded by the daemon at start | credential asked at this terminal; never sent; seen by the daemon at its next cycle | session continues |
| Connector probe | `connector check`, one connector enrolled | acknowledged (ten-second bound); the daemon probes the connectors enrolled now, their rows and credentials read afresh, not a second inventory in the prompt; that instance's row follows with the id | session continues |
| No daemon | `dashboard`; no token file or connection refused | one sentence naming `pm-ai daemon run`; `exit 1` | session continues |
| Token rejected | the daemon answers 401 | one sentence: token file and daemon disagree; restart the daemon; `exit 1` | session continues |
| Daemon busy | `503` with `Retry-After: 1` | one sentence: the daemon is busy, try again in 1 second; `exit 1`; not retried | session continues |
| Unknown answer | 404, another 5xx, or a body that is not JSON | one sentence naming the status; `exit 1` | session continues |
| Daemon started later | prompt opened before `pm-ai daemon run` | the next line reaches it, no restart | N/A |
| Token file unreadable | wrong mode or owner | one sentence naming the file; nothing sent | session continues |
| Ctrl-C while waiting | a daemon-routed command in flight | wait abandoned, one sentence; the daemon finishes | session continues |
| Result while typing | a result arrives with a line half typed | printed above the prompt on arrival, the typed line drawn again intact (`4v`); at the next prompt without the line editor; never into the typed line | N/A |
| Two jobs | a second `dashboard` acknowledged | both ids listed | N/A |
| Stream lost | the stream ends before the result | one sentence naming the job id; prompt returns | session continues |
| Exit with a job pending | `exit` after an acknowledgement | names the id; says it continues in the daemon | exit 0 |
| Two lines at once in the daemon | two prompt requests overlapping | each answer holds only its own output | N/A |
| Open line | `what did I promise Laura` | `4f`'s sentence; no request made | N/A |
| Startup unchanged | `pm-ai` under a pseudo-terminal | `4f`'s 1.0 s test still passes; no request at open | fails over 1.0 s |

</frozen-after-approval>

## Code Map

- `pm_ai/surfaces/cli/repl.py` -- `4f`'s loop; gains the routing and the acknowledgement follow-up. Takes a structural type for the daemon client beside `4f`'s local runner; imports nothing from `pm_ai.app` (`tests/surfaces/test_cli_dispatch.py:470`)
- `pm_ai/surfaces/cli/loopback.py` -- new: standard-library HTTP to 127.0.0.1 carrying the token, read from the file on each request; the outcomes as values — an answer with output and exit code, an acknowledgement with a job id, no daemon, token rejected, busy with its wait, unknown answer. `4r`'s `POST /v1/prompt` and `GET /v1/jobs/<id>/events` and `4e`'s token file are referenced from those specs, not restated
- `pm_ai/surfaces/cli/dispatch.py:366` -- `Context`, which gains `out` and `err` streams (defaulting to the process's); `:547` `Command`, whose `target` of `None` means "needs no daemon" (`doctor`, `setup`, `project add`); `:1583` `TABLE`; `:1658` `_HELP_FLAGS`; `:1748` `parse`; `:1842` `dispatch`, which the daemon side calls with its own `Context`. At `ddcb9aa`, `grep -c "sys.stdout\|sys.stderr\|print(" pm_ai/surfaces/cli/dispatch.py` counts 42 sites writing to the process streams
- `pm_ai/surfaces/api/app.py` -- `4r`'s prompt endpoint; the per-request `--scope` resolution and the per-request streams land there. `pm_ai/core/dispatch.py` -- `4r`'s `plan()`, which reads each pipeline's declared bound from `app`
- `pm_ai/app/entry.py:122` -- `main`: `4f`'s no-arguments branch gains the client, built from the token path (`ScopePaths.production`, `:923`); `:146` `_compose`, what a daemon-routed command no longer does in this process; `:138-141` the comment on story 4m's order, which the daemon half follows without the folder step
- `pm_ai/storage/service.py:582` -- `check_same_thread=False` is not a concurrency guarantee; in the daemon every write goes through `4r`'s one writer thread, and this slice adds no thread of its own there
- `.importlinter:62` `http-confined-to-adapters` -- stays; a comment records that the loopback client is the standard library. `:148` `cli-owns-no-scheduling` -- the stream-reading thread in the prompt waits on a job; it imports no scheduler and starts no timer, so the contract holds
- `tests/surfaces/test_cli_subcommands.py:850-860` -- the `isatty` monkeypatch pattern; `tests/slice/test_repl_startup.py` -- `4f`'s timing test, which must keep passing

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/surfaces/cli/dispatch.py` -- `Context` gains `out: TextIO` and `err: TextIO` (defaults `sys.stdout`, `sys.stderr`); rewrite the 42 sites the grep above counts to write to them, including `Answered.deliver` at `:1740` and the refusal at `:1950`; never `contextlib.redirect_stdout` or `redirect_stderr`, which are process-wide — a test greps `pm_ai/` for both names and fails on a hit -- per-request output (the fields and the `_echo` sites landed with `4r`; this slice added the test, on the syntax tree)
- [x] `pm_ai/surfaces/api/app.py` -- in `4r`'s prompt endpoint: resolve `--scope` per request against the daemon's watched projects (named, else the only one; unwatched refused by name; enrolled since start refused with the shared restart sentence, read from the registry as a plain read); `config.toml`, connector rows and credentials read afresh per line; run the table with fresh `StringIO` streams in a `Context` of its own; an inline command runs on `4r`'s I/O pool -- the daemon half (built in `pm_ai/app/prompts.py`, where `4r`'s endpoint handler lives; no command defaults to a project, so there is no "only one" case)
- [x] `pm_ai/surfaces/cli/loopback.py` -- the client: token read per request, prompt request, stream follow-up, the outcomes as values including the unknown-answer one -- one place speaks to the daemon
- [x] `pm_ai/surfaces/cli/repl.py` -- routing by `Command.target` plus the five terminal-bound names; the `exit 1` sentences for no daemon, token rejected and unknown answer; Ctrl-C while waiting; results printed only at the next prompt; jobs listed by id; the exit-with-job sentence -- the contract
- [x] `pm_ai/app/entry.py`, `pm_ai/surfaces/cli/dispatch.py` -- build the client from the token path and inject it; the daemon's refusal of a project enrolled since start prints `4r`'s shared restart sentence -- nothing composed at open
- [x] `tests/surfaces/test_cli_repl.py` -- one test per matrix row with a stand-in daemon client -- the matrix is the contract
- [x] `tests/slice/test_repl_through_daemon.py` -- a real daemon in a throwaway home with one connector enrolled: `connector check` typed at the prompt is acknowledged and that instance's row follows; `dashboard` typed at the prompt is acknowledged, the `requests` row exists, the render arrives with the id, and the prompt's process composed no daemon; two overlapping prompt requests hold only their own output -- the job path is real

**Acceptance Criteria:**
- Given `4e`'s daemon running with a project enrolled, when `pm-ai` is typed and then `dashboard`, then an acknowledgement and a job id show, the daemon renders, the command-line output and `exit 0` follow with the id — on arrival with the line editor (`4v`), at the next prompt without it — and the prompt's process composed no daemon of its own.
- Given the daemon running with one connector enrolled, when `connector check` is typed, then that instance's row arrives from the daemon's own connectors, not from a second inventory.
- Given no daemon running, when `doctor` is typed at the prompt, then it runs in this process and prints its report, and `dashboard` typed next names `pm-ai daemon run` with `exit 1`.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged by this slice; `4e` and `4r` state their own deltas.

## Design Notes

Routing reads the command table's own `target` rather than a second list; the five that need this terminal are named once, so a command added later goes through the daemon by default — the safe side of AD-5. The standard library's HTTP client rather than `httpx`: the surfaces contract names third-party clients, and AD-8 makes the CLI a loopback client. Streams are threaded through `Context` because `redirect_stdout` swaps the whole process's stream, and two requests in `4r`'s pool would read each other's output. The stream-reading thread does not violate `cli-owns-no-scheduling`: it waits for one job the daemon already owns and decides nothing about when work runs. Concurrency in the daemon is `4r`'s (one I/O pool of four, one writer thread); this slice hands work to it and creates no thread there.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; the skip count unchanged by this slice
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
- `HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai daemon run` in one terminal and `.venv/bin/pm-ai` in another, then `config show` and `dashboard` -- expected: `config show` answers inline with `exit 0`; `dashboard` shows a job id, the daemon's terminal shows the render, and the output with the id and `exit 0` appears as soon as it arrives with the line editor (`4v`), or at the next prompt without it

## Spec Change Log

- **2026-10-14, frozen block amended by Andrei** (the architecture spine's concurrency amendment of the same day: AD-5, AD-6, AD-7; decided by Andrei, re-derived here before any build). Amended: the stale "`4r`'s daemon-level lock serialises the workers' calls" and "whether that lock survives the daemon is `4e`'s decision" are replaced by `4r`'s one writer thread and the cross-process claim the daemon also takes; a connector added is seen at the daemon's next cycle only when it files into the personal scope or a project the daemon loaded at start; `project add` and `key enrol` take effect after a restart, and every command whose change waits for a restart says so in one shared sentence, one constant beside the command table (owned by `4r` since the same day's decision; this slice prints it); a line run through the daemon reads `config.toml`, connector rows and credentials afresh so its output matches the terminal, and a line naming a project enrolled since the daemon started is refused with that sentence, `exit 3`. A `503` from `4r` (its writer thread not free within one second, or the row refused busy) is now told as busy with its wait rather than as an unknown status. Matrix rows "Enrolment while the daemon runs", "Local secret command", "Connector probe" and "Unknown answer" changed; rows added for a busy daemon, a project enrolled since start, a key enrolled while the daemon runs, and settings edited while it runs. Known-bad state avoided: a command through the daemon printing different output from the same command at a terminal; `connector check` counting the rows the daemon composed at start rather than those enrolled now; a person told a change is live when the running daemon cannot see it; three commands giving three different freshness answers. KEEP: the routing rule and the five terminal-bound commands; the token read on each request; the standard library's HTTP client; the `exit 1` sentences; Ctrl-C abandoning only the wait; results printed only at the next prompt; per-request output streams and no process-wide redirection; `4f`'s 1.0-second startup; every field and endpoint being `4r`'s.
- **2026-10-17, frozen block amended by `4t`** (goals listed, shown, updated and deleted). The routing bullet's list of commands that need this terminal gains `goal update` and `goal delete`, which may ask questions; `goal list` and `goal show` ask nothing and go through the daemon like any other command.
- **2026-10-18, frozen block amended by `4v`** (jobs report themselves at the prompt; Andrei's feedback after first use, memlog UX decision 2026-10-16). Superseded: "only between lines, at the next prompt". With `4u`'s line editor a job's result is printed above the prompt as soon as it arrives and the half-typed line is drawn again intact; it is held while a local command or an inline wait owns the terminal and printed before the next prompt; without the line editor (no runtime extra, a failed terminal check, or the editor failing mid-session) it is printed at the next prompt, as before. The acknowledgement bullet and the matrix row "Result while typing" changed. KEEP: the id at the acknowledgement, two jobs listed by id, the lost-stream sentence, the exit-with-job sentence, Ctrl-C, exit codes, routing and every refusal sentence.
- **2026-10-18, frozen block amended by `4w`** (`pm-ai logs`). The routing bullet's list of commands that need this terminal gains `logs`, and its reasons gain a new one: the command holds the terminal until Ctrl-C (`--follow` has no end, and would hold one of the daemon's four workers until the client went away). The daemon refuses a `logs` line by name, as it refuses the others.

## Suggested Review Order

**Start here**

- Where a typed line runs: here or in the daemon, read off the table.
  [`repl.py:88`](../../../../pm_ai/surfaces/cli/repl.py#L88)

**The prompt as a client**

- Send one line; show the answer, the acknowledgement, or one sentence.
  [`repl.py:220`](../../../../pm_ai/surfaces/cli/repl.py#L220)
- A relative path is made absolute against the prompt's directory.
  [`repl.py:199`](../../../../pm_ai/surfaces/cli/repl.py#L199)
- Jobs: one waiting thread each; results printed only between lines without the line editor, and above the prompt on arrival with it (`4v`).
  [`repl.py:146`](../../../../pm_ai/surfaces/cli/repl.py#L146)
- Ending: in-flight read first, then delivered, so no result is lost.
  [`repl.py:321`](../../../../pm_ai/surfaces/cli/repl.py#L321)
- The loop: deliver before each prompt; client optional, as in 4f.
  [`repl.py:341`](../../../../pm_ai/surfaces/cli/repl.py#L341)

**The loopback client**

- `send`: Ctrl-C says whether the line went out; nothing else escapes.
  [`loopback.py:274`](../../../../pm_ai/surfaces/cli/loopback.py#L274)
- One line over a raw socket, under one deadline, parsed by http.client.
  [`loopback.py:311`](../../../../pm_ai/surfaces/cli/loopback.py#L311)
- Every answer as a value: identity, 401, 503, JSON shape.
  [`loopback.py:515`](../../../../pm_ai/surfaces/cli/loopback.py#L515)
- Retry-After: ASCII digits only; missing is now, a date is shortly.
  [`loopback.py:505`](../../../../pm_ai/surfaces/cli/loopback.py#L505)
- Port and token read on every request, never at open.
  [`loopback.py:435`](../../../../pm_ai/surfaces/cli/loopback.py#L435)
- Token: this user's regular file, judged by the daemon's own rule.
  [`loopback.py:465`](../../../../pm_ai/surfaces/cli/loopback.py#L465)
- The job's stream: same checks as send, no read timeout after connect.
  [`loopback.py:384`](../../../../pm_ai/surfaces/cli/loopback.py#L384)
- Stream events: size-limited lines, malformed ones named, open event kept.
  [`loopback.py:543`](../../../../pm_ai/surfaces/cli/loopback.py#L543)
- A finished row as the answer; failed is its sentence and exit 1.
  [`loopback.py:585`](../../../../pm_ai/surfaces/cli/loopback.py#L585)
- The sentences for no daemon: no token file, or nothing on the port.
  [`loopback.py:110`](../../../../pm_ai/surfaces/cli/loopback.py#L110)

**The daemon's half**

- The line's scope resolved per request, before anything is planned.
  [`prompts.py:360`](../../../../pm_ai/app/prompts.py#L360)
- Unwatched, enrolled since start, or registry unreadable: each refused by name.
  [`prompts.py:396`](../../../../pm_ai/app/prompts.py#L396)
- The registry read strictly for a line's scope.
  [`prompts.py:809`](../../../../pm_ai/app/prompts.py#L809)
- `daemon run` through the daemon: a plain "already running" refusal.
  [`prompts.py:180`](../../../../pm_ai/app/prompts.py#L180)

**Wiring and shared constants**

- The prompt gets the client; nothing is read or composed to open it.
  [`entry.py:181`](../../../../pm_ai/app/entry.py#L181)
- The client built from paths alone.
  [`entry.py:1399`](../../../../pm_ai/app/entry.py#L1399)
- Only `daemon_port` read, so a bad unrelated key still reaches `config show`.
  [`config.py:452`](../../../../pm_ai/core/config.py#L452)
- Commands with a path argument, one list the daemon and prompt share.
  [`dispatch.py:2768`](../../../../pm_ai/surfaces/cli/dispatch.py#L2768)
- The five commands that need this terminal, beside the table.
  [`dispatch.py:2753`](../../../../pm_ai/surfaces/cli/dispatch.py#L2753)

**Tests**

- Real daemon: dashboard acknowledged, its render arrives with the id.
  [`test_repl_through_daemon.py:137`](../../../../tests/slice/test_repl_through_daemon.py#L137)
- Real daemon: two commands at once hold only their own output.
  [`test_repl_through_daemon.py:202`](../../../../tests/slice/test_repl_through_daemon.py#L202)
- Real daemon on a pseudo-terminal: a typed line runs and is recalled.
  [`test_repl_through_daemon.py:254`](../../../../tests/slice/test_repl_through_daemon.py#L254)
- The daemon's scope refusals, in process against 4r's request path.
  [`test_repl_through_daemon.py:300`](../../../../tests/slice/test_repl_through_daemon.py#L300)
- One test per matrix row, with a stand-in client.
  [`test_cli_repl.py:336`](../../../../tests/surfaces/test_cli_repl.py#L336)
- The client against a stand-in HTTP daemon: every answer it must know.
  [`test_cli_loopback.py:158`](../../../../tests/surfaces/test_cli_loopback.py#L158)
- Timeouts: connect vs answer, one deadline, streams that pause.
  [`test_cli_loopback.py:357`](../../../../tests/surfaces/test_cli_loopback.py#L357)
- No module redirects the process streams, checked on the syntax tree.
  [`test_cli_repl.py:815`](../../../../tests/surfaces/test_cli_repl.py#L815)
- 4f's recall test, now the no-daemon sentence with nothing written.
  [`test_repl_startup.py:128`](../../../../tests/slice/test_repl_startup.py#L128)
- 4r's enrolled-since-start test, now refused inline with no row.
  [`test_line_requests_round2.py:466`](../../../../tests/slice/test_line_requests_round2.py#L466)
- 4r's writer-thread test, ordered by the queue rather than a sleep.
  [`test_daemon_writer_thread.py:143`](../../../../tests/slice/test_daemon_writer_thread.py#L143)
