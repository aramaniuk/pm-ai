---
title: 'pm-ai with no arguments opens a terminal prompt'
type: 'feature'
created: '2026-10-10'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `pm-ai` typed on its own is a usage error. CAP-18 promises a prompt that opens within a second and takes fixed commands and open questions until `exit` or `quit`, so the terminal can do what the phone does. None of it exists, and every `pm-ai` command today builds its own daemon before doing anything — too slow for a prompt.

**Approach:** `pm-ai` with no arguments at a terminal opens a prompt. A typed fixed command runs in this process exactly as the command line runs it; an open line is answered plainly that no model is in this build. Nothing is composed to open the prompt. This slice is the interim before the daemon: every command runs locally through the same entry point until `4q` (after `4e` and `4r`) sends it through the daemon, and from then on AD-21's rule for lines that go through the daemon applies to those lines. Of CAP-18 this slice meets the prompt itself, fixed commands, and `exit`/`quit`; it does not meet open questions being answered (no model is in this build) or parity with Telegram (story 5 does not exist). It is shippable on its own.

## Boundaries & Constraints

**Always:**
- **A terminal opens the prompt; anything else stays the usage error it is today.** `pm-ai` with no arguments, with standard input and standard output both at a terminal, opens the prompt. From a pipe, a cron job, with no standard input, or with its output redirected to a file, it prints usage and exits 2, unchanged. The prompt text is `pm-ai> `. Opening the prompt never starts first-run setup; `setup` is a command one types.
- **The prompt shows within 1.0 second of process start,** measured by a test that starts the real `pm-ai` under a pseudo-terminal and times the prompt's arrival; the measured figure goes into the test's own failure message and the bound is asserted. The figure is measured on an x86_64 interpreter under Rosetta 2 (deferred-work, 2026-10-09; story 7 re-baselines). Measured 2026-10-10: `pm-ai --help` takes 0.17–0.34 s and importing the console script's module 0.15 s, so the budget holds only because nothing below is done at open.
- **Line editing and recall within the session use the standard library's `readline` when it can be imported** (libedit on macOS; no new dependency); when it cannot, lines are still read and nothing else changes. Its import is part of the measured startup (0.012 s, measured 2026-10-10). No history file is written.
- **Nothing is composed to open the prompt.** No daemon built, no keychain opened, no project chosen, no event-log line written. The process imports none of `fastapi`, `uvicorn` or `msal`; a test asserts this against the console script's module, which also binds `4e` to keeping its server imports off that path.
- **A fixed command typed at the prompt runs exactly as `pm-ai <words>` does:** the same output, the same exit code, shown afterwards as one status line, `exit <n>`, on standard output. Arguments and options are spelled as on the command line, quotes included; `help` prints the usage. It runs in this process through the same entry point, composing afresh for each typed command as the command line composes today, under the cross-process lock AD-5 names. Measured 2026-10-10 with one project enrolled: composing and running `config show` costs 1–3 ms once the entry module is loaded; `doctor`'s probes 0.15–0.24 s. With encryption switched off, the event-log line is written only when a run first writes a protected file in plain text (`1p`), so a command that writes nothing leaves the event log and the memory tree as they were; the first composition in a fresh home creates `private/operational.db`, nothing else. `4q` later sends commands through the daemon; the status line and the words do not change.
- **A line with an unbalanced quote is a usage error:** one sentence saying the quote is unclosed, `exit 2`, and the session continues. Nothing is run.
- **An open line is never sent anywhere, never ignored, and recorded nowhere.** A line whose first word names no command and is not `help`, `exit` or `quit` is answered with one sentence: the first word names no command and `help` lists them; no model is in this build (prototype-path decision 2); the line was not sent anywhere. So `dashbord` is told it named no command rather than mistaken for a question. The line is written to no history file, no event log and no diagnostic log — nothing reads it, and the sentence must stay true. A blank line shows a fresh prompt. Decided 2026-10-10.
- **`exit`, `quit` and Ctrl-D end the session with exit 0.** Ctrl-C clears the line being typed and shows a fresh prompt; it never ends the session. Ctrl-C inside a running command is that command's own interruption and shows its exit code; the session continues.
- **Nothing here goes through the daemon (AD-21).** Every fixed command runs in the foreground in this process with its own bound, and an open line is answered at once. Once `4q` sends a line through the daemon, AD-21's transport rule applies to that line: over five seconds it is acknowledged with a job id and the result follows. The acknowledgement and the job id belong to `4q` and `4r`.
- **Parity with Telegram is not claimed.** Story 5 does not exist, so there is nothing to compare against and no test asserts parity.

**Ask First:** Nothing. The one open question — whether an open line is recorded — was settled on 2026-10-10 and stated above.

**Never:** No model and no model router. No new exit code. No scheduler in the CLI. No second command table. No HTTP to a daemon (`4q`). No Telegram. No history file.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Opens | `pm-ai`, standard input and output at a terminal | `pm-ai> ` within 1.0 s; nothing composed; no event-log line; setup not started | N/A |
| No terminal | `pm-ai` from a pipe, cron, no standard input, or output redirected to a file | usage on stderr | exit 2 |
| Fixed command | `doctor` | runs in this process; the report; `exit 0` or `exit 4` on stdout | session continues |
| Command with options | `dashboard --scope personal` | the same output as the command line; `exit 0` | session continues |
| Quoted argument | `project add "/path/with space" alpha` | the words as the shell would split them | session continues |
| Unbalanced quote | `project add "/path alpha` | one sentence: the quote is unclosed; nothing run; `exit 2` | session continues |
| Command refused | `goal set` on a machine with no project | the refusal sentence; `exit 3` | session continues |
| Secret command | `connector add gitlab alpha` | credential asked at this terminal, as on the command line | session continues |
| Usage error | `dashboard --bogus` | the same usage text as the command line; `exit 2` | session continues |
| Writes nothing | `config show` with encryption off | the output; no event-log line, no file under `~/.pm-ai/memory` changed | session continues |
| Open line | `what did I promise Laura` | one sentence: names no command, `help` lists them, no model, not sent anywhere; nothing written anywhere | N/A |
| Mistyped command | `dashbord` | the same sentence: `dashbord` names no command | N/A |
| Blank line | Enter | a fresh prompt | N/A |
| Help | `help` | usage; `exit 0` | N/A |
| Recall | up arrow after a command | the previous line, when `readline` imported; otherwise the terminal's own behaviour | N/A |
| End | `exit`, `quit`, Ctrl-D | session ends | exit 0 |
| Ctrl-C while typing | half a line | line cleared; fresh prompt | N/A |
| Ctrl-C inside a command | during `setup`'s questions | that command's own interruption sentence; `exit 3` | session continues |
| Startup time | the real `pm-ai` under a pseudo-terminal | prompt within 1.0 s; figure in the test's message | fails over 1.0 s |
| Server imports | import the console script's module | `fastapi`, `uvicorn`, `msal` absent from loaded modules | fails if present |

</frozen-after-approval>

## Code Map

- `pm_ai/app/entry.py:122` -- `main`: `:137-146` parses, delivers an `Answered`, then `_compose`s. The no-arguments-and-terminal branch goes **before** `parse` and hands the prompt `main` itself as the runner, the pattern `onboard=`/`first_run=` use (`:176`, `:180`). `:210` the `KeyboardInterrupt` guard a command's Ctrl-C ends in (exit 3)
- `pm_ai/surfaces/cli/dispatch.py:1748` -- `parse`: `[]` returns the usage `Answered`, unchanged. `:1583` `TABLE`, which decides whether a first word names a command; `:1658` `_HELP_FLAGS` (`help` is one). `:107-125` the exit codes, reused. `:1458`, `:1530` the standard-input-is-a-terminal check to copy. `:872` `_typed`, `:1212` `_ask`: the Ctrl-D/Ctrl-C handling commands keep
- `pm_ai/surfaces/cli/repl.py` -- new: the loop, `shlex.split` for the words (its `ValueError` is the unbalanced quote), the open-line sentence, the status line, `readline` imported inside a `try`. Takes a callable for running a command; imports nothing from `pm_ai.app` (`tests/surfaces/test_cli_dispatch.py:470`)
- `_bmad-output/specs/spec-pm-ai/stories/1p-encryption-off-recorded-on-first-write.md` -- done: the event-log line is written at the first protected plain-text write, not at composition; the superseded measurement at `deferred-work.md:802-806` is not the rule
- `tests/surfaces/test_cli_dispatch.py:130` -- the bare invocation stays exit 2; `tests/surfaces/test_cli_subcommands.py:850-860` the `isatty` monkeypatch pattern

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/surfaces/cli/repl.py` -- the loop: `help`/`exit`/`quit`, `shlex.split` and the unclosed-quote sentence, the runner call and the `exit <n>` line on stdout, the open-line sentence naming the first word, Ctrl-C and Ctrl-D, optional `readline` -- the contract
- [ ] `pm_ai/app/entry.py` -- the no-arguments branch before `parse`, guarded by standard input and output both being terminals; inject `main` as the runner -- nothing composed at open
- [ ] `tests/surfaces/test_cli_repl.py` -- one test per matrix row with a fake runner, including that an open line reaches no file -- the matrix is the contract
- [ ] `tests/surfaces/test_cli_dispatch.py:130` -- monkeypatch `isatty` to `False` explicitly rather than relying on pytest's captured stdin, and fix its docstring (the REPL is this slice, not `4e`); add a sibling with `isatty` true on stdin and stdout whose stdin is fed `doctor\nexit\n`, run through the real `entry.main([])`, asserting the doctor report and an `exit 0` or `exit 4` line -- the runner is the real `main`, not a fake
- [ ] `tests/slice/test_repl_startup.py` -- the pseudo-terminal timing test with the figure in its message, and the server-imports test -- the budget is measured, not asserted

**Acceptance Criteria:**
- Given a machine with a project enrolled, when `pm-ai` is typed and then `doctor`, then the report prints as `pm-ai doctor` prints it, `exit 0` or `exit 4` shows, and the next prompt appears.
- Given `entry.main([])` under the test with both terminal checks monkeypatched true and stdin fed `doctor\nexit\n`, when it returns, then it returned 0, the doctor report is in the captured output, and one line reads `exit 0` or `exit 4`.
- Given the prompt, when `what did I promise Laura` is typed, then one sentence says the first word names no command, no model is in this build and the line went nowhere, and no file under `~/.pm-ai` changed.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged: this slice un-skips no invariant test.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; the skip count unchanged by this slice
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
- `HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai` from a real terminal -- expected: `pm-ai> ` within a second; `config show` then `exit 0`; `exit` returns 0; `.pm-ai/memory/event_log/` gained no line
