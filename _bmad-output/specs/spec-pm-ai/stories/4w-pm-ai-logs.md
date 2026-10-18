---
title: 'pm-ai logs shows the daemon log readably'
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

**Problem:** The daemon writes its request log and its own diagnostic lines to `~/.pm-ai/logs/daemon.log` (AD-24, `4e`, `4r`) as one JSON object per line with UTC times, rotating into `daemon.log.1` to `.3`. Nothing in pm-ai says where that file is or shows it; after first use Andrei could not find the logs.

**Approach:** A new top-level command, `pm-ai logs [--lines <n>] [--level <level>] [--follow]`, prints the daemon log's most recent lines as plain text in local time, across the current file and its rotated copies, optionally keeps printing new lines as they are written, and always says which file it reads. It reads the files directly, before anything is composed, and needs no daemon, keychain or readable settings.

## Boundaries & Constraints

**Always:**
- **What it reads.** `daemon.log` and its rotated copies in `~/.pm-ai/logs/`, oldest first — the names and the count (three copies of 5 MiB) are the storage service's, which already reads them for `4r`'s restart pass; this slice uses that one rule, not a second copy. The command's first line, on standard error, names the file and the zone times are shown in: `pm-ai: reading ~/.pm-ai/logs/daemon.log (and daemon.log.1–.3), times in <zone> (UTC+03:00 now)` — the zone's name where the machine has one, and its offset now. Standard output carries only log lines, so it can be piped.
- **One line per record, readable.** `<local date and time to the second>  <level>  <source>  <message>`: the time from the record's `at` field converted to this machine's local zone, each record with the offset in force at its own instant, so lines either side of a daylight-saving change read correctly; the level in lower case; the source `request` for the request log, `daemon` for the daemon's own lines (the two loggers that write the file), any other logger by its name; the message as written. A message of several lines (an error's type name and the frames it was raised through, `4r`'s format) prints its continuation lines indented beneath it.
- **Nothing in the file can act on the terminal.** The request log records the decoded request path even for a request the daemon refused for want of a token (`surfaces/api/app.py:277-281`, `:308-313`), so any local process, or a web page's request to the loopback port, can put arbitrary characters into a message. Before printing, every control character in the message and the source — the C0 and C1 ranges and DEL, which include the escape character that starts a terminal command — is replaced by `?`, except the line breaks of a multi-line message, which are printed as indented lines.
- **No exception text is ever printed (`4r`'s rule).** The file holds none — the daemon's writer keeps an exception's type and frames and drops its text — and this command adds none: it prints the fields named above and nothing else of a record. A line that is not valid UTF-8, not a JSON object, or lacks a parseable timezone-aware `at`, a known `level` or a text `message` is never printed; such lines are counted, and one sentence on standard error at the end says how many were skipped. A last line with no line break yet is a line still being written: it is held, never counted as skipped, and printed when it is complete (with `--follow`) or left out. A filesystem error is named by its system reason (`Permission denied`), never a traceback.
- **`--lines <n>`** — the last `n` lines that pass the level filter, default 50; ASCII digits only, 1 to 10000; anything else is a usage error, exit 2. **`--level <level>`** — that level and every more severe one: `debug`, `info`, `warning`, `error`, `critical`, case ignored; another word is a usage error naming the five, exit 2. **`--follow`** (a value-less flag, `4t`'s `flags` field) — after the last lines, keeps printing new ones as the daemon writes them, checking about twice a second. When the daemon rotates the file it reads the old file to its end through the handle it already has, then carries on from the start of the new `daemon.log`; two rotations between checks are caught up through `daemon.log.1`; a moment with no `daemon.log` is waited out. No line is repeated or lost. Ctrl-C ends it with exit 0 — the follow catches the interruption itself.
- **Works with no daemon and nothing composed.** `pm-ai logs` is answered in `main` before the keychain adapter is built and before composition, so no master key, keychain, store, settings file or daemon is touched and a home with no `~/.pm-ai` gains nothing; the file paths come from paths alone, as the prompt's loopback client's do. No file yet: one sentence saying the daemon writes it at `~/.pm-ai/logs/daemon.log` when it runs (`pm-ai daemon run`), exit 0; with `--follow`, that sentence and then it waits for the file to appear. The current file unreadable: refused naming it and the reason, exit 3. A rotated copy unreadable: skipped, with one sentence naming it on standard error, and the rest printed. Output closed early (`pm-ai logs | head`): exit 0, nothing more printed. Ctrl-C while reading without `--follow`: `main`'s interruption, exit 3.
- **Never through the daemon.** At the prompt it runs in the prompt's own process: `logs` joins `TERMINAL_BOUND`, whose meaning widens to "needs this terminal: asks questions, reads a secret, or holds the terminal until Ctrl-C". So the daemon refuses a `logs` line by name, as it refuses every terminal-bound command, and needs no bound for it — a bound could not cover `--follow`, which would hold one of the daemon's four workers until the client went away.
- **Exit codes are `4c`'s:** 0 printed (or nothing to print, said so), 2 a command line pm-ai cannot read, 3 the current file cannot be read or a read was interrupted, 1 a bug.

**Ask First:** Showing any other file under `~/.pm-ai/logs/`; a filter by text or by time.

**Never:** No writing, truncating or rotating the log. No JSON output mode. No change to what the daemon writes or how it rotates. No reading through the daemon. No exception text, raw unparsed line, control character, or record field other than `at`, `level`, the logger's name and `message`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Recent lines | `pm-ai logs`, 300 lines across `daemon.log` and `.1` | the file line on stderr; the last 50 in order, local time; exit 0 | N/A |
| Count | `--lines 5` | the last five | `--lines 0`, `--lines x`, `--lines ５` → usage, exit 2 |
| Level | `--level Warning` | only warning, error and critical lines | `--level loud` → usage naming the five, exit 2 |
| Rotated copies | lines split over `.3`, `.2`, `.1` and the current file | oldest first, no gap at the boundaries | N/A |
| Rotated copy unreadable | `.2` mode 000 | the rest printed; one sentence naming `.2` | exit 0 |
| Daylight saving | records either side of a clock change | each in the offset of its own instant | N/A |
| Follow | `--follow` while the daemon writes | new lines appear as written | Ctrl-C → exit 0 |
| Follow across rotation | the daemon rotates, once or twice between checks | the old file read to its end, then the new; none repeated or lost | N/A |
| Follow, no file yet | `--follow`, `daemon.log` absent, then the daemon starts | the no-file sentence; then the daemon's first lines | Ctrl-C → exit 0 |
| Partial last line | the daemon mid-write | held; not counted as skipped | N/A |
| Error record | a record whose message holds a type name and frames | the first line, then the frames indented; no exception text | N/A |
| Planted escape | a request path holding an escape sequence | printed with `?` in place of each control character | N/A |
| Foreign line | not JSON, not UTF-8, naive `at`, unknown level, non-text message | not printed; "N lines were not in pm-ai's log format and were skipped" on stderr | N/A |
| No file | `~/.pm-ai/logs/daemon.log` absent | one sentence naming the path and `pm-ai daemon run` | exit 0 |
| Unreadable | current file mode 000 | refused naming the file and `Permission denied` | exit 3 |
| No daemon, broken settings | daemon stopped, `config.toml` unreadable, no keychain | prints as above; nothing composed; the home unchanged | N/A |
| At the prompt | `logs --follow` typed at `pm-ai>` | runs in the prompt's process; Ctrl-C returns to the prompt with `exit 0` | session continues |
| Sent to the daemon | a `logs` line posted to the prompt endpoint | refused by name as terminal-bound | exit 3 |
| Piped | `pm-ai logs \| head -3` | three lines; no broken-pipe message | exit 0 |

</frozen-after-approval>

## Code Map

Re-verified against `5f4f43e` on 2026-10-18.

- `pm_ai/app/daemon.py:868` -- `_JsonLines`, the record format: `at` is `datetime.fromtimestamp(created, timezone.utc).isoformat()` (`2026-10-18T07:04:05.123456+00:00`, which `datetime.fromisoformat` reads as timezone-aware), `logger`, `level` lower case, `message`, and named fields; `json.dumps`, so a multi-line message is still one line in the file; `:889` `_TextlessQueueHandler`, why the file holds no exception text (the message gains `<Type> (its text is not logged)` and `  File …, line …, in …` frames); `:918` `_LOGGERS`, the two loggers in the file; `:944` `_RequestLog.attach`, the file opened, `:964` level `INFO`
- `pm_ai/surfaces/api/app.py:277-281`, `:308-313` -- the request line logged with the decoded path (cut by `_loggable`), whatever the answer; `:91` `REQUEST_LOGGER = "pm_ai.api.requests"`
- `pm_ai/app/prompts.py:128` -- `DAEMON_LOG_NAME = "daemon.log"`; `:132` `DAEMON_LOGGER = "pm_ai.daemon"`; `:369-379` the daemon's refusal of a `TERMINAL_BOUND` line, whose sentence says the command "asks questions or takes a secret" — not true of `logs`; `:710` the restart pass reading `diagnostic_lines`
- `pm_ai/storage/service.py:1370` -- `DIAGNOSTIC_LOGS = "logs/"`, `:1371` 5 MiB, `:1372` three copies; `:2256` `diagnostic_lines`, which names the rotated files oldest first (`daemon.log.3` … `.1`, then `daemon.log`) through `self._paths.resolve`, not `_prepared`, so it creates nothing; `:2987` `diagnostic_log`, the rotating handler; `:508` `_PrivateRotatingFileHandler` (`1s`): rotation stays by rename, every new file `0600`, its docstring already names `4w`'s follow; `logs/` is made `0700` by `_prepared` (`:2334`) on the daemon's side only
- `pm_ai/app/entry.py:188` -- the no-arguments branch (the prompt); `:197` `parse`, `:198-199` `Answered` delivered; `:200` the keychain adapter, built before `4t`'s `composes=False` branch (`:201-213`), so `logs` is answered between `:199` and `:200`, not through `composes`; `:214` `_compose`; `:323-329` Ctrl-C mapped to exit 3; `:1416` `_loopback` and `:1430` `_completion`, dependencies built from paths alone
- `pm_ai/surfaces/cli/dispatch.py:3097` -- `TERMINAL_BOUND` and its docstring; `:3124` `TABLE`; `:952` `Command`, `:1000` `flags` (`4t`; its docstring already names `4w`'s `--follow`), `:1009` `composes`; `:3349` `Parsed.flags`; `:835` `Context.flags`
- `pm_ai/surfaces/cli/repl.py:111-127` -- `runs_here`: a `TERMINAL_BOUND` line runs in the prompt's process, so `logs` is routed locally by joining the set
- `pm_ai/surfaces/cli/completion.py:62-123` -- `FREE_TEXT`, `FREE_TEXT_NAMES`, `_BY_NAME`, `_BY_COMMAND`, `source_of`; `:418` `_values`. The command name and `--lines`, `--level`, `--follow` are offered from `TABLE` with no change; a value for `level` needs a source and `lines` a free-text entry, else `tests/surfaces/test_cli_completion.py:231` fails (flags need no source)
- `tests/slice/test_line_requests_hardening.py:85-98` -- every command the daemon runs declares a bound, except the terminal-bound ones
- `tests/architecture/test_private_modes.py:475` -- the diagnostic log's row: rotation by rename, every new file `0600`

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/storage/service.py` -- the rotated-file list as a module-level function of the logs directory and the name, used by `diagnostic_lines` and handed out for this command; it resolves paths and never calls `_prepared`, which would create `logs/` -- one rule for which files and in what order
- [x] `pm_ai/app/entry.py` -- `logs` answered in `main` after `parse` and before the keychain adapter (`:200`, which `4t`'s `composes=False` branch still builds) and `_compose`, with the file list built from `ScopePaths.production()` -- nothing composed, nothing created
- [x] `pm_ai/surfaces/cli/logs.py` -- new: records read from the files (invalid ones counted, a partial last line held), the level filter, local time per record, control characters replaced, the skipped count, following with rotation through the open handle, waiting for an absent file, Ctrl-C caught in the follow, a closed output treated as the end -- the reader, testable without a terminal
- [x] `pm_ai/surfaces/cli/dispatch.py` -- `logs` in `TABLE` with `lines`, `level`, flag `follow`, no target; `TERMINAL_BOUND` gains `logs` and its docstring the wider meaning; the usage errors; the header and closing sentences -- the command
- [x] `pm_ai/app/prompts.py` -- the refusal sentence at `:376-378` widened from "asks questions or takes a secret" so it is true of `logs` ("holds the terminal until Ctrl-C") -- the matrix's "refused by name as terminal-bound"
- [x] `pm_ai/surfaces/cli/completion.py` -- `--level` completes the five levels (a new source in `_BY_COMMAND` and a branch in `_values`); `("logs",), "lines"` in `FREE_TEXT`; `--follow` needs nothing -- `4u`'s check that every declared name has a source
- [x] `_bmad-output/specs/spec-pm-ai/stories/4q-repl-through-the-daemon.md` -- when this slice is built: edit the amended frozen text and add a dated Change Log line naming `logs` added to the commands that need this terminal, and the new reason -- an earlier frozen block says it was amended
- [x] `tests/surfaces/test_cli_logs.py`, `tests/slice/test_logs_command.py` -- one test per matrix row against a temporary home with written log files (rotation simulated by renaming), the slice ones through `main()` asserting the home is unchanged; a record carrying a planted secret in a field other than `message` never prints it -- the matrix is the contract

**Acceptance Criteria:**
- Given a daemon run in a temporary home that answered a few requests and stopped, when `pm-ai logs` runs, then the request lines appear in local time with `request` as their source, the first stderr line names the file, and the exit is 0.
- Given no `daemon.log` yet, when `pm-ai logs --follow` runs and the daemon is then started, then the no-file sentence appears, the daemon's start lines follow within a second of being written, and Ctrl-C exits 0.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged by this slice.

## Spec Change Log

- **2026-10-16, created** from Andrei's feedback after first use ("he could not find the logs"; memlog UX decision, slice 4w). Decisions it carries: a top-level `logs` command reading `daemon.log` and its rotated copies through the storage service's one rule; plain lines in the machine's local zone with the zone and the file named first, on standard error; `--lines` (default 50), `--level` (that level and above), `--follow` (a `4t` flag, following rotation, Ctrl-C exits 0); only `at`, `level`, the logger and `message` printed, unparsed lines counted and skipped, so no exception text can reach the terminal; no file is exit 0 with a sentence, an unreadable one exit 3; runs locally at the prompt and with no daemon.
- **2026-10-16, review:** answered in `main` before the keychain and composition, the home untouched; never through the daemon — `logs` joins `TERMINAL_BOUND` (meaning widened to "holds the terminal until Ctrl-C") instead of declaring a bound; control characters replaced before printing, because the request log carries decoded paths from unauthenticated requests; a partial last line held; follow reads a rotated file to its end through its open handle, catches up through `.1`, waits out an absent file and waits for one that does not exist yet; malformed and non-UTF-8 lines skipped; per-record daylight-saving offsets; a rotated copy unreadable is skipped with a note; a closed pipe exits 0; `--lines` ASCII digits only, `--level` case ignored; Ctrl-C outside follow is `main`'s exit 3; a `completion.py` task for `--level` and `--lines`; ACs given a When. Amends an earlier frozen block: `4q`'s routing bullet, which names the commands that need this terminal and why — `logs` joins them, and "holds the terminal until Ctrl-C" joins the reasons; a task adds a dated line to `4q`'s Change Log. Depends on spine amendment: AD-21 — `logs` joins the foreground administrative commands parsed from the command line (the list already holds daemon-routed `dashboard` and `config show`).
- **2026-10-16, review (cross-spec):** Change Log moved before Design Notes; the AD-21 amendment restated as an addition to the foreground administrative commands parsed from the command line; the amendment task now edits `4q`'s frozen text and adds a dated line, when this slice is built.
- **2026-10-18, Code Map re-verified against `5f4f43e` after 4t, 4u and 1s merged.** Line numbers corrected; the frozen block holds against the merged code (file, rotation by rename to `daemon.log.1`–`.3`, the JSON line format, `0600` files in a `0700` folder this command only reads; AD-21 already lists `logs`). Tasks gained: the daemon's refusal sentence in `prompts.py` widened; `entry.py`'s branch placed before the keychain adapter, which `4t`'s `composes=False` branch still builds; the shared file list must not create `logs/`; `completion.py`'s exact entries.

## Design Notes

The machine's local zone rather than `display_timezone`: the log is read beside the machine's own clock and other logs, and reading `config.toml` would let a broken settings file stop the one command used to find out what is broken. The zone is named in the first line, so a reader comparing with the dashboard's times knows. The level filter keeps `debug` although the daemon writes `info` and above today (`daemon.py:964`), so the filter does not change when that does. `logs` joins `TERMINAL_BOUND` rather than gaining a bound: `--follow` has no end, the daemon's pool has four workers, and the existing refusal by name is the honest answer. Control characters are replaced rather than escaped because a log line is read, not parsed, and `?` cannot be mistaken for content. A rotated copy that cannot be read is skipped rather than fatal: the newest lines are the ones a person looking for a problem needs, and the sentence says what is missing.

## Verification

**Commands:**
- `uv run pytest tests/surfaces/test_cli_logs.py tests/slice/test_logs_command.py -q` -- expected: every matrix row passes
- `uv run pytest -q` -- expected: all pass; the skip count unchanged by this slice
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- `HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai daemon run`, a few `pm-ai config show` lines, then `.venv/bin/pm-ai logs --level info` and `--follow` -- expected: readable local-time lines; follow shows new requests as they come

## Suggested Review Order

**Answered before anything is composed**

- Entry point: `logs` answered right after `parse`, before the keychain adapter and composition.
  [`entry.py:206`](../../../../pm_ai/app/entry.py#L206)

- The file list, logger names and zone built from paths alone; nothing created.
  [`entry.py:1447`](../../../../pm_ai/app/entry.py#L1447)

- One rule for which files and in what order, shared with the restart pass.
  [`service.py:1375`](../../../../pm_ai/storage/service.py#L1375)

**What a record may print**

- Only `at`, `level`, the logger and `message` survive parsing; anything else is foreign.
  [`logs.py:126`](../../../../pm_ai/surfaces/cli/logs.py#L126)

- Control characters (C0, DEL, C1, surrogates) become `?` (see deferred: Unicode format characters).
  [`logs.py:75`](../../../../pm_ai/surfaces/cli/logs.py#L75)

- Local time per record's own instant; continuation lines indented.
  [`logs.py:161`](../../../../pm_ai/surfaces/cli/logs.py#L161)

- The header names only the copies actually read, and the zone with its offset now.
  [`logs.py:190`](../../../../pm_ai/surfaces/cli/logs.py#L190)

**Reading and following**

- Top level: skipped count, Ctrl-C in follow is exit 0, closed pipe is exit 0.
  [`logs.py:271`](../../../../pm_ai/surfaces/cli/logs.py#L271)

- First read: current file opened first; a copy that is the same file is read once.
  [`logs.py:398`](../../../../pm_ai/surfaces/cli/logs.py#L398)

- Follow: old file read to its end, new current opened before copies are caught up.
  [`logs.py:527`](../../../../pm_ai/surfaces/cli/logs.py#L527)

- Copies newer than the old file, stopping at the new current's identity.
  [`logs.py:578`](../../../../pm_ai/surfaces/cli/logs.py#L578)

- Raw stdout writes, so a closed pipe leaves nothing for the exit-time flush.
  [`logs.py:321`](../../../../pm_ai/surfaces/cli/logs.py#L321)

**The command and its routing**

- `logs` in the table: `--lines`, `--level`, `--follow`, no target, read before the keychain.
  [`dispatch.py:3220`](../../../../pm_ai/surfaces/cli/dispatch.py#L3220)

- The handler: usage errors exit 2, an unreadable current file exit 3.
  [`dispatch.py:3116`](../../../../pm_ai/surfaces/cli/dispatch.py#L3116)

- `logs` joins the commands that need this terminal; the daemon refuses it by name.
  [`dispatch.py:3188`](../../../../pm_ai/surfaces/cli/dispatch.py#L3188)

- The daemon's refusal sentence widened to "holds the terminal until Ctrl-C".
  [`prompts.py:377`](../../../../pm_ai/app/prompts.py#L377)

**Peripherals**

- The machine's zone name: `TZ` first, else the `/etc/localtime` link; checked against the zone database.
  [`environment.py:69`](../../../../pm_ai/platform/environment.py#L69)

- `--level` completes the five levels; `--lines` is free text.
  [`completion.py:107`](../../../../pm_ai/surfaces/cli/completion.py#L107)

- Reader tests per matrix row; slice tests through `main()` and real processes.
  [`test_cli_logs.py:1`](../../../../tests/surfaces/test_cli_logs.py#L1)
  [`test_logs_command.py:1`](../../../../tests/slice/test_logs_command.py#L1)
