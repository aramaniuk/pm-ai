---
title: 'The prompt completes and hints'
type: 'feature'
created: '2026-10-16'
status: 'done'
baseline_commit: '9e5383fe4c6ec023d243bfd09d9df39e72d6b6d4'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The `pm-ai>` prompt (`4f`, `4q`) reads a line with the standard library's `input()` and nothing else: no Tab completion, no sign of what a command takes until it is refused. After first use Andrei asked for completion and hints to build commands; today a person has to type `help`, read, and type again.

**Approach:** The prompt reads lines with `prompt_toolkit`, a pure-Python terminal library, added to the runtime extra at an exact version. Tab completes commands, subcommands, options, flags, scopes, connector instances, goal ids and file paths; a line at the bottom of the terminal shows the usage of the command being typed; a dim suggestion offers the rest of a command typed earlier in this session. Every candidate comes from the one command table plus a few readers of local files, and nothing waits on the daemon. `4v` reuses the same library for job progress. Replaces `4f`'s line-editing bullet ("the standard library's `readline` when it can be imported") and its Recall row, for a prompt with the runtime extra.

## Boundaries & Constraints

**Always:**
- **Why not the standard library's `readline`.** It can complete on Tab (`set_completer`) and list matches, and nothing more of what is asked: it cannot draw a hint line or a dim suggestion while a person types, cannot change the prompt while a line is being read, and exposes no call that clears and redraws a half-typed line from another thread (`4v`). The interpreter this project runs on links the BSD `editline` library, not GNU readline (measured: `readline.backend` is `editline` on the main checkout's uv-managed Python 3.14.7), whose key-binding syntax differs and whose redisplay from another thread is not documented. `prompt_toolkit` provides completion, a bottom toolbar, suggestions and printing above the line being typed, as public interfaces.
- **The dependency follows the project's policy.** It joins `[project.optional-dependencies] runtime` in `pyproject.toml`, pinned exactly at the version current on PyPI when this slice is built, verified there and recorded in the comment beside it, with its one dependency (`wcwidth`) resolved by the lock. It is imported inside the prompt's start, never at module import, so `pm-ai <command>` never loads it, and `4f`'s import test adds it to the modules the console script must not import. The library's features that start other processes or stop this one — opening the line in an editor, a system command prompt, suspending with Ctrl-Z — are turned off explicitly, and a test says so.
- **When it is not used.** If the import fails, the prompt is `4q`'s loop and says once, at open, that completion needs `uv sync --extra runtime`. If it imports but the session cannot start (the library raises, or a stream is not a terminal), the prompt is `4q`'s loop with one sentence of its own naming what failed by type. Either way the session continues.
- **The 1.0-second startup holds.** `tests/slice/test_repl_startup.py` passes: the prompt appears within 1.0 s of the process starting, and `pm-ai> ` still appears as those bytes. The session is started with the terminal's cursor-position request turned off (the library's switch, checked against the pinned version), so a test terminal that never answers it neither delays the prompt nor prints a warning; the tests assert no such warning appears. The recall test (`:128-147`) keeps its assertions — two no-daemon sentences, two `exit 1` lines, the home untouched — and waits on the status lines rather than on a count of prompts if the library draws the prompt more than once. Its docstring records 0.39 s on 2026-10-13; the figure with the library is measured when this slice is built and written there. Nothing is read, composed or sent to open the prompt: completion's readers run at the first Tab that needs them.
- **One command table.** Command, subcommand, option and flag candidates are read from `TABLE` (`takes`, `optional`, `options`, `flags` from `4t`, `requires`), and the hint line is `_spelled` and the summary the usage text already prints. Values are completed by the command and the argument or option name it declares, so one name can mean two things: `--scope` offers `personal` and `project:<id>` for each enrolled project; `instance` after `connector sign-in` offers every Graph row in `connectors/`, enabled or not (sign-in reads neither); `system` after `connector add` offers the supported systems; `--domain` and `--horizon` offer the goal vocabulary; `id` after `goal show`, `goal update` and `goal delete` offers the goal ids in `strategic_goals.md`; `file` and `path` complete file paths. Names that are free text — `connector add`'s `instance`, `goal set`'s `id`, `alias`, `--title`, `--meeting` — are listed as such in `completion.py`, never in `TABLE`. A test fails when a command declares a name that is neither, so a new argument cannot go uncompleted silently.
- **Completion never waits on the daemon and opens no store.** Its readers read files by path: `projects.toml`, the `*.json` rows in `connectors/` judged by `4n`'s row rule as a pure function (never through `StorageService`, which opens `operational.db` when built), and `strategic_goals.md` (not encrypted). No master key, keychain or database is touched. They are handed to the prompt by the composition root, since `surfaces` may not locate files. Each is read at most once between two commands and re-read after any command runs, so a project added in this session is offered next. A reader that fails offers nothing for that value; completion never prints an error or a traceback.
- **Completed words split back as typed.** A candidate the line's splitting (`shlex.split`) would not return unchanged — a space, a quote, `$`, `~` mid-word — is inserted through `shlex.quote`, which wraps it in single quotes, so the words the command receives are the ones chosen (finding 21 in `_bmad-output/implementation-artifacts/field-test-2026-10-15.md`, which lands with PR #71). Tab inside a quote not yet closed completes within it and closes it. Paths are relative to the folder pm-ai was started in, `~` expanded. `--scope=` completes as `--scope ` does.
- **Hints.** While a line names a command, the bottom line shows its usage (`transcript drop <file> --scope <scope> [--meeting <meeting>]`) and summary; a group name alone lists its subcommands; `help`, `-h` or `--help` alone says it lists every command; an open line or an unknown word shows nothing. A dim suggestion offers the remainder of a matching earlier command from this session, accepted with the right-arrow key.
- **`4f`'s and `4q`'s rules stand.** No history file is read or written; history is this session's, in memory, and holds only lines whose first word names a command — an open line is recorded nowhere, not even there. Up arrow recalls the previous command. Ctrl-C clears the line; Ctrl-D and `exit` end the session; routing, exit codes and the `exit <n>` status line are unchanged.

**Ask First:** If the startup test cannot pass with the library loaded at open, or its measured figure leaves less than a quarter of the budget, before raising the budget or loading the library after the first prompt. Any second line-editing library.

**Never:** No completion that sends anything to the daemon. No reading the master key, the keychain or `operational.db` for a candidate. No history file. No completion of meeting ids (they need the meeting records, a later slice). No change to the command table's shape beyond reading it. No `readline` fallback with completion of its own (a second implementation to keep in step).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First word | `da` Tab | `daemon`, `dashboard` offered | N/A |
| Subcommand | `goal ` Tab | `set`, `update`, `delete`, `list`, `show` | N/A |
| Options | `transcript drop x.vtt --` Tab | `--scope`, `--meeting`; one already given is not offered again | N/A |
| Scope | `dashboard --scope ` Tab, projects alpha and beta enrolled | `personal`, `project:alpha`, `project:beta` | registry unreadable → `personal` only, no message |
| Scope with `=` | `dashboard --scope=pro` Tab | `--scope=project:alpha`, `--scope=project:beta` | N/A |
| Instance to sign in | `connector sign-in ` Tab, `graph:work` enrolled, disabled | `graph:work` | N/A |
| Instance to add | `connector add graph ` Tab | nothing offered (a new name) | N/A |
| Goal id | `goal show ` Tab | the ids on file | file broken → nothing offered |
| Path with a space | `transcript drop My` Tab, `My Notes.vtt` in the folder | `'My Notes.vtt'` inserted | N/A |
| Inside an open quote | `transcript drop "My` Tab | `"My Notes.vtt"`, quote closed | N/A |
| Hint | `transcript drop ` typed | bottom line shows its usage and summary | N/A |
| Group alone | `connector ` typed | bottom line lists the group's subcommands as `TABLE` declares them (`add`, `check`, `sign-in`, and `categories` once `4s` adds it) | N/A |
| Help word | `help` typed | bottom line: lists every command | N/A |
| Unknown word | `what did I` typed | no hint | N/A |
| Suggestion | `config show` entered earlier, now `con` | `fig show` shown dim; right arrow accepts | N/A |
| Open line not kept | `what did I promise` entered, then `wh` | no suggestion; up arrow skips it | N/A |
| Recall | up arrow after `config show` | `config show` on the line | N/A |
| Ctrl-C, Ctrl-D, `exit` | while typing; at an empty line; typed | line cleared; session ends; session ends | exit 0 |
| No history file | a session with commands, then ended | no file written under the home | N/A |
| Enrolled during the session | `project add ~/x gamma`, then `--scope ` Tab | `project:gamma` offered | N/A |
| No daemon | daemon not running, Tab anywhere | completes as above; no request made | N/A |
| Runtime extra missing | import of `prompt_toolkit` fails (simulated in the test) | `4q`'s prompt; one line naming `uv sync --extra runtime` | session continues |
| Session cannot start | the library raises at start | `4q`'s prompt; one line naming the failure's type | session continues |
| Startup | `pm-ai` under a pseudo-terminal | `pm-ai> ` within 1.0 s; no cursor-position warning | fails over 1.0 s |
| Not a terminal | `echo doctor \| pm-ai` | unchanged: the usage error `parse` answers | exit 2 |

</frozen-after-approval>

## Code Map

Verified against: re-verified against `ba0b747` (main) on 2026-10-17. `4t`'s goal set/update/delete/list/show and the `yes` flag are on main, and the command and argument sources below cover them.

- `pm_ai/surfaces/cli/repl.py:345` -- `run`, whose `read` is `input` (`:348`); `:113` `_line_editing`, the `readline` import this replaces; `:74` `PROMPT`; `:88` `runs_here`, unchanged
- `pm_ai/surfaces/cli/dispatch.py:940` -- `Command` (`takes`, `optional`, `options`, `requires`, and `flags` at `:988` from `4t`); `:3092` `TABLE`; `:3247` `_spelled`; `:3265` `usage`; `:3238` `names_a_command`; `:1148` `SUPPORTED_SYSTEMS`; `:1549` `_connector_sign_in`, which reads no `enabled`; option and argument names: `:2444` `GOAL_OPTIONS`, `:2449` `YES_FLAG`, `:1715` `SCOPE_ARGUMENT`, `:2761` `MEETING_ARGUMENT`
- `pm_ai/core/goal_register.py:164` `DOMAIN_SPELLINGS`, `:174` `HORIZON_SPELLINGS` -- the domain and horizon spellings; `:337` `parse_goals`
- `pm_ai/app/entry.py:181-182` -- the no-arguments branch that opens the prompt; `:1406` `_loopback`, built from paths alone (the pattern for the completion readers)
- `pm_ai/app/wiring.py:2773` -- `read_connector_rows`, `4n`'s row rule, which takes a whole `StorageService`; `pm_ai/storage/service.py:1390` `StorageService`, `:1395` `__init__` -- building one creates the store's folder (around `:1448`) and connects `operational.db` (around `:1456`), so the rule is factored to a pure function over the directory's files
- `pm_ai/domain/scope_model.py:562` -- `strategic_goals.md` declared not encrypted
- `pyproject.toml` -- the runtime extra and its pinning comments. The spine's Stack table records `prompt_toolkit` 3.0.53 (as of 2026-10-16); the build pins the release current on PyPI when it is built and records that, as the Boundaries require
- `tests/slice/test_repl_startup.py:24` -- `BUDGET_SECONDS = 1.0`; `:114` the timing test; `:128-147` the recall test; `:150` the import test; `tests/surfaces/test_cli_repl.py` -- `4q`'s matrix tests, which drive `run` through `read`

## Tasks & Acceptance

**Execution:**
- [x] `pyproject.toml`, `uv.lock` -- `prompt_toolkit` pinned exactly in the runtime extra, with the verification comment -- the dependency
- [x] `pm_ai/app/wiring.py` -- `4n`'s row rule as a pure function over the rows' names and bytes, used by `read_connector_rows` and by the completion reader -- one rule, no store
- [x] `pm_ai/surfaces/cli/completion.py` -- new: the completer over `TABLE` with value sources keyed by command and name, the free-text list, the hint text, `shlex.quote` for candidates, a tokenizer that tolerates an open quote; no I/O of its own beyond listing a folder for a path -- one place reads the table for completion
- [x] `pm_ai/surfaces/cli/repl.py` -- `run` reads through a `prompt_toolkit` session (in-memory history of command lines only, Tab completion, bottom hint line, suggestions, cursor-position request off, editor, system prompt and suspend off) when the library imports and the session starts; otherwise `4q`'s loop and the matching sentence; `read` stays injectable -- the prompt
- [x] `pm_ai/app/entry.py` -- the completion readers (registry ids, Graph rows, goal ids), built from paths, read lazily, injected into `run` -- `surfaces` may not locate files
- [x] `tests/surfaces/test_cli_completion.py` -- one test per matrix row against the completer with stand-in readers, the extra-missing row by simulating the failed import, plus the test that every name a command declares has a value source or is listed as free text, and the test that the process-starting features are off -- the matrix is the contract
- [x] `tests/slice/test_repl_startup.py` -- the import test lists `prompt_toolkit`; no cursor-position warning asserted; the recall test waits on status lines if prompts repeat; the docstring records the figure measured with the library -- the budget
- [x] `_bmad-output/specs/spec-pm-ai/stories/4f-terminal-repl.md` -- when this slice is built: edit the amended frozen text and add a dated Change Log line naming the line-editing bullet and the Recall row this slice replaces -- an earlier frozen block says it was amended

**Acceptance Criteria:**
- Given the runtime extra installed and two projects enrolled, when `pm-ai` opens on a pseudo-terminal and `dashboard --scope ` and Tab are typed, then both `project:<id>` candidates and `personal` are offered and no request reached the daemon's port.
- Given the change, when `uv run pytest` runs, then everything passes, the startup test included, and the skip count is unchanged by this slice.

## Spec Change Log

- **2026-10-16, created** from Andrei's feedback after first use ("the prompt needs auto-completion and hints"; memlog UX decision, slice 4u). Decisions it carries: `prompt_toolkit` rather than the standard library's `readline`, because readline (here the `editline` backend, measured) completes but cannot hint while typing or redraw a line safely, which `4v` also needs; the library in the runtime extra, pinned exactly, imported only when the prompt opens, with `4q`'s prompt as the fallback; candidates from the one command table, with a test that every name has a source; readers of local files only, re-read after each command, never the daemon; hints as a bottom line plus dim suggestions from this session's in-memory history; meeting ids not completed.
- **2026-10-16, review:** value sources keyed by command and name (`connector add`'s instance and `goal set`'s id are free text), the free-text list in `completion.py`; Graph rows offered enabled or not; readers by path with `4n`'s row rule as a pure function, `StorageService` forbidden (it opens `operational.db`); `shlex.quote` for any candidate that would not split back, an open quote tolerated and closed, `--scope=` completed; history holds command lines only; separate fallbacks for a failed import and a failed start; the cursor-position request off and the startup tests' handling stated; the library's process-starting features off; rows for recall, Ctrl-C, Ctrl-D, `exit`, no history file, group alone, help, unknown word, open lines; a task for `4f`'s Change Log; finding cited by file. Amends an earlier frozen block: `4f`'s line-editing bullet and Recall row (replaced when the runtime extra is installed). Depends on spine amendment: the Stack table — a `prompt_toolkit` row, pinned exactly, runtime extra, imported lazily by the prompt.
- **2026-10-16, review (cross-spec):** Change Log moved before Design Notes; the group hint reads the subcommands from the table (so `connector categories` from `4s` appears), not a fixed list; `--lines` dropped from the free-text list (it is `4w`'s); the amendment task now edits `4f`'s frozen text and adds a dated line, when this slice is built.
- **2026-10-17,** Code Map re-verified against `ba0b747` after 4t merged; command and argument sources match main.
- **2026-10-18, built:** `prompt_toolkit` pinned at 3.0.53, the release current on PyPI that day (`wcwidth` 0.9.2 by the lock). Startup with the library loaded at open: median 0.71 s over seven pseudo-terminal runs at build, 0.66 s over nine after review (0.43 s with the import made to fail), so more than a quarter of the 1.0 s budget is left and the Ask First did not apply. The hint line is the library's bottom toolbar, drawn on the line under the input: with the cursor-position request off, the library cannot know the rows below the cursor, so `editor.py` reports one. Review added: Ctrl-Z bound to nothing; `~name` paths not completed; after a bare `--`, words are positionals; the connector reader shares `list_collection`'s membership rule; inside double quotes, only `"` and `\` are escaped, as `shlex` does.

## Design Notes

A completer keyed by the command and the names the table already declares keeps the table the one list: adding `--level` to a command makes it offered once `completion.py` gives it a source, and the free-text check makes a missing source a test failure rather than a quiet gap. Keying by command as well as name is needed because `instance` is an enrolled name after `sign-in` and a new one after `add`. Offering disabled Graph rows to `sign-in` matches what that command accepts. `shlex.quote` rather than double quotes: it is the standard library's own inverse of the splitting the prompt does, so a completed word always splits back to itself. History holds commands only so that `4f`'s rule — an open line is recorded nowhere — still holds inside the session. Readers are injected because candidates live in files that only `pm_ai.app` may locate. The fallback without the extra is `4q`'s existing loop, so there is one new prompt rather than two that must agree.

## Verification

**Commands:**
- `uv sync --extra runtime` then `uv run pytest tests/surfaces/test_cli_completion.py tests/slice/test_repl_startup.py -q` -- expected: pass; the measured startup figure under 1.0 s
- `uv run pytest -q` -- expected: all pass; the skip count unchanged by this slice
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- `HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai` with one project enrolled: Tab through `transcript drop`, a path with a space, `--scope` -- expected: as the matrix says; the bottom line shows the usage

## Suggested Review Order

**Completion over the one table**

- Entry point: completes a line from `TABLE`, keyed by command and declared name.
  [`completion.py:362`](../../../../pm_ai/surfaces/cli/completion.py#L362)

- Value sources by (command, name); free-text names listed here, never in `TABLE`.
  [`completion.py:107`](../../../../pm_ai/surfaces/cli/completion.py#L107)

- Reads the words so far: options given, positional count, a bare `--`.
  [`completion.py:292`](../../../../pm_ai/surfaces/cli/completion.py#L292)

- Tolerates an open quote; marks a trailing backslash broken, as `shlex` refuses it.
  [`completion.py:173`](../../../../pm_ai/surfaces/cli/completion.py#L173)

- Inserted words split back to themselves (`shlex.quote`, or closing the open quote).
  [`completion.py:240`](../../../../pm_ai/surfaces/cli/completion.py#L240)

- Hint text: usage and summary, a group's subcommands, help, or nothing.
  [`completion.py:441`](../../../../pm_ai/surfaces/cli/completion.py#L441)

- Path listing relative to the start folder; only the `~/` form of `~`.
  [`completion.py:470`](../../../../pm_ai/surfaces/cli/completion.py#L470)

**The prompt and its fallbacks**

- Only importer of `prompt_toolkit`: history of commands only, toolbar, suggestions, features off.
  [`editor.py:125`](../../../../pm_ai/surfaces/cli/editor.py#L125)

- One row reported below the cursor so the toolbar draws with the cursor-position request off.
  [`editor.py:68`](../../../../pm_ai/surfaces/cli/editor.py#L68)

- Missing extra, incompatible library or failed start: `4q`'s loop and one sentence.
  [`repl.py:238`](../../../../pm_ai/surfaces/cli/repl.py#L238)

- Mid-session library failure falls back; Ctrl-C and Ctrl-D pass through.
  [`repl.py:213`](../../../../pm_ai/surfaces/cli/repl.py#L213)

- Readers forgotten after every command line, so new enrolments are offered.
  [`repl.py:539`](../../../../pm_ai/surfaces/cli/repl.py#L539)

**Readers built from paths**

- Composition root hands the three lazy readers to the prompt.
  [`entry.py:1429`](../../../../pm_ai/app/entry.py#L1429)

- Graph rows by path, enabled or not; no store opened.
  [`entry.py:1460`](../../../../pm_ai/app/entry.py#L1460)

- `4n`'s row rule as a pure function, shared with `read_connector_rows`.
  [`wiring.py:2865`](../../../../pm_ai/app/wiring.py#L2865)

- Collection membership rule shared by `list_collection` and the reader.
  [`service.py:1390`](../../../../pm_ai/storage/service.py#L1390)

**Peripherals**

- Exact pin and its verification comment.
  [`pyproject.toml:106`](../../../../pyproject.toml#L106)

- Matrix tests, the declared-name check, features-off and fallback tests.
  [`test_cli_completion.py`](../../../../tests/surfaces/test_cli_completion.py)

- Pseudo-terminal acceptance: scope candidates with no daemon request; hint line drawn.
  [`test_repl_startup.py:184`](../../../../tests/slice/test_repl_startup.py#L184)
