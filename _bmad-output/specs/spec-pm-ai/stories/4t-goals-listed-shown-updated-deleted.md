---
title: 'Goals can be listed, shown, updated and deleted'
type: 'feature'
created: '2026-10-16'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `goal set` is the only goal command, and it only asks questions. In the field test `goal update`, `goal create` and `goal list` were all tried and refused as usage errors (finding 23 in `_bmad-output/implementation-artifacts/field-test-2026-10-15.md`, which lands with PR #71): there is no way to see the goals pm-ai holds without opening `strategic_goals.md`, no way to change one from a script, and no way to remove one except by hand.

**Approach:** The `goal` group gains `list`, `show <id>`, `update <id>` and `delete <id>`, and `goal set` keeps its name as the command that creates a goal (and still revises one, as today). `set` and `update` take `--domain`, `--horizon` and `--title`, so a goal can be written without questions. `delete` asks for a confirmation at a terminal, and `--yes` skips it for scripts. Every write goes through `22b`'s writer and records one personal event-log entry (CAP-10). Lifts `22b`'s Never line "No goal deletion"; its reason — what happens to recommendations citing a goal — is answered by the references bullet below.

## Boundaries & Constraints

**Always:**
- **`goal set` stays the create path.** Its name is in the file's own header text (`goal_register.HEADER`), in AD-21's list of foreground commands and in `TERMINAL_BOUND`. It becomes `goal set [<id>] [--domain <domain>] [--horizon <horizon>] [--title <title>]`. `goal update <id>` takes the same options and refuses an id that is not on file, naming `goal set` — so a mistyped id never creates a goal. `goal create` is not added: the usage of `goal` lists the five subcommands, so a person typing `create` lands on `set`.
- **One rule for questions.** For an id already on file (`set` or `update`): any option given means nothing else is asked — what is not given is kept; no option at a terminal asks all three, offering the current values as defaults (as `goal set` does today); no option and no terminal is refused naming the three options, exit 3. For a new id (`set` only): every value not given is asked at a terminal; without a terminal, refused naming each missing option. `goal set` with no id asks the id first at a terminal; without a terminal it is refused naming `<id>` as well as the missing options. Nothing is written before a refusal. A title that begins with `--` is given as `--title=--fast`.
- **The vocabulary is the parser's.** Domain and horizon are read by `declare_goal`'s closed spellings, the id by its charset; their refusals pass through verbatim, exit 3. Nothing here decides what a goal may be.
- **`goal list`** prints the goals grouped by domain in `GoalDomain` order, sorted by id within a domain (the dashboard's order, `rendering.py:1060`), one line each: id, horizon, title. An absent file and an empty file each get one sentence naming `goal set`; exit 0. **`goal show <id>`** prints the id, its citation form `goal:<id>`, domain, horizon, title and the file's path; an id not on file is refused naming `goal list`, exit 3. Both only read; a file the parser refuses is refused with the parser's own message (its line number included), exit 3.
- **List and show need no daemon.** At the command line they read `strategic_goals.md` by its path (it is not encrypted) and compose nothing, so a broken `config.toml` or no keychain does not stop them. Through the daemon, the file is read afresh for every line, like `config.toml` (AD-7's refresh points), and both are inline commands with a declared bound in the daemon's table of bounds.
- **`goal delete <id>`** removes the goal's line and the detail lines indented beneath it (the block `set_goal` already moves with a goal), keeping every other byte of the file — a byte-order mark at the start, Windows line endings (carriage return and line feed), list markers and prose included. The result must parse back as the register without that id, or nothing is written. A goal-shaped line inside the block (a goal nested under another's detail lines) refuses the delete naming the nested id, with a sentence of its own. At a terminal it first prints exactly the lines it will remove and asks `(y/n)`; `y` or `yes`, ignoring case and surrounding spaces, deletes; anything else, end of input or Ctrl-C writes nothing, exit 3. After the answer the file is read again and the delete is refused if the block is not the one shown. `--yes` skips the question. Without a terminal and without `--yes`, refused naming `--yes`, exit 3.
- **What references a goal, and what deletion does to it.** At this commit nothing stored cites a goal: `Recommendation` is constructed nowhere outside `domain/goals.py`, no proposal, ledger or dashboard line carries `goal:<id>` except the dashboard's own goals section, and the personal event log's `goal_set` entries name the id as history. So deletion is never refused for references. The dashboard's 3-Tier Strategic Milestones section drops the goal at its next render; the earlier `goal_set` entries stay (append-only, AD-5); a citation written later to a deleted id renders as unresolved, never silently dropped (CAP-11, AD-41 rule 2) — `resolve` already raises for it. The closing line says the dashboard changes at its next render.
- **Every change is recorded.** A set or update appends `goal_set` as today. A delete appends a new self-action, `goal_deleted`, carrying the goal as it read before the delete — id, domain, horizon, title — and the channel, so the log alone says what was removed. A title too long for one ledger line (a hand-edited file can hold one) is cut to fit with `…` in the entry, so a delete is never refused for its title. As with `goal set`, the entry is checked before the write; if the append fails after the write, the refusal says the file changed and the log did not (`GoalUnrecorded`).
- **Flags.** `--yes` is the first option that takes no value. The command table gains a declared `flags` field beside `options`, spelled `[--yes]` in the usage, read in both the top-level and the leaf branch of the parser. A flag never takes the word after it as a value (`goal delete --yes g_a` reads `g_a` as the id); a flag given a value (`--yes=1`) or twice is a usage error, exit 2. `4w`'s `--follow` and `4u`'s completion reuse this field.
- **Help for one subcommand.** `pm-ai <group> <subcommand> --help` (or `-h`) prints that subcommand's usage line and summary, exit 0; today it is a usage error. `11d` relies on this.
- **Routing at the prompt follows `4q`'s rule.** `goal set`, `goal update` and `goal delete` may ask questions, so all three are in `TERMINAL_BOUND` and run in the prompt's process, whatever options are typed. `goal list` and `goal show` ask nothing and go through the daemon.
- **Exit codes are `4c`'s:** 0 done (or unchanged, said so), 2 a command line pm-ai cannot read, 3 a refusal carrying its reason, 1 a bug.

**Ask First:** Refusing a delete for any reason other than the ones above (for example, once story 13 stores recommendations that cite goals).

**Never:** No change to the goal grammar, `declare_goal`'s vocabulary or the id charset. No rename command (a rename is a delete and a set, and AD-41 makes a renamed id an unresolved citation on purpose). No editing or removing earlier event-log entries. No project-scope goals (AD-41 rule 5). No `goal create` alias.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| List | two project goals, one team goal | grouped by domain, sorted by id; exit 0 | N/A |
| List, nothing | file absent, or present with no goals | one sentence naming `goal set`; exit 0 | N/A |
| List, broken file | a goal line with a bad horizon | the parser's message with its line; nothing printed as a goal | exit 3 |
| List, broken settings | `config.toml` unreadable, no daemon | the goals listed; nothing composed | N/A |
| Show | `goal show g_a` | id, `goal:g_a`, domain, horizon, title, file path; exit 0 | unknown id → names `goal list`, exit 3 |
| Set by options | `goal set g_new --domain team --horizon short --title "Hire two"` | written; `goal_set` appended; no question; works without a terminal | N/A |
| Set, value missing | `goal set g_new --domain team`, no terminal | refused naming `--horizon` and `--title`; nothing written | exit 3 |
| Set, no id | `goal set --domain team --title x`, no terminal | refused naming `<id>` and `--horizon` | exit 3 |
| Set, interactive | `goal set`, at a terminal | today's four questions, unchanged | N/A |
| Vocabulary refused | `goal set g_new --domain team --horizon soonish --title x` | `declare_goal`'s sentence naming the three horizons; nothing written | exit 3 |
| Update | `goal update g_a --title "Under 150ms"`, at a terminal | only the title changes; nothing else asked; `goal_set` appended | N/A |
| Update, no options | `goal update g_a`, no terminal | refused naming the three options | exit 3 |
| Update, unknown id | `goal update g_typo --title x` | refused naming `goal set`; nothing written | exit 3 |
| Update, no change | options equal the goal on file | "unchanged"; nothing written or recorded; exit 0 | N/A |
| Update moves domain | `--domain personal` on a project goal | the line and its detail lines move under `## Personal` (`set_goal`) | N/A |
| Delete at a terminal | `goal delete g_a`, answered ` Yes ` | the lines removed are shown first; removed; `goal_deleted` appended; closing line names the next dashboard render | `n`, end of input or Ctrl-C → nothing written, exit 3 |
| File changed while asking | the goal's block edited between the display and `y` | refused, nothing written | exit 3 |
| Delete for a script | `goal delete g_a --yes`, no terminal | removed without a question; exit 0 | N/A |
| Flag before the id | `goal delete --yes g_a` | `g_a` read as the id; removed | N/A |
| Delete, no terminal, no `--yes` | piped | refused naming `--yes`; nothing written | exit 3 |
| Delete keeps the file | goal with two detail lines, prose around it, Windows line endings, byte-order mark | only the goal's block is gone; every other byte kept | N/A |
| Nested goal | a goal-shaped line inside `g_a`'s detail lines | refused naming the nested id; nothing written | exit 3 |
| Long title | a hand-edited title longer than a ledger line allows | deleted; the entry's title cut with `…` | N/A |
| Delete, unknown id | `goal delete g_none` | refused naming `goal list` | exit 3 |
| Log append fails after delete | event log unwritable | the file changed and the log did not, said so | exit 3 |
| Flag misuse | `goal delete g_a --yes=1`; `--yes --yes` | usage error | exit 2 |
| Subcommand help | `goal delete --help` | its usage line and summary | exit 0 |
| At the prompt | `goal list` / `goal delete g_a` | `list` through the daemon, inline; `delete` in the prompt's process, asking there | session continues |
| Unknown subcommand | `goal create` | the `goal` usage listing set, update, delete, list, show | exit 2 |

</frozen-after-approval>

## Code Map

- `pm_ai/core/goal_register.py:755` -- `set_goal`, the in-place writer to mirror; `:808` `_read_back`, the parse-back check (its "setting … would not read back" sentence is not the delete's); `:919` `_block_end`, a goal's detail block; `:385-389` where the parser reads nested goal-shaped lines; `:694` `declare_goal`; `:159`, `:169` the closed spellings; `:659` `HEADER`, which names `goal set`
- `pm_ai/domain/goals.py:85` -- `resolve`, which already raises `UnresolvedGoal` for a cited id not on file; `Recommendation` is constructed nowhere else in `pm_ai/` (checked with grep)
- `pm_ai/domain/event_entries.py:104` -- `SelfActionType.GOAL_SET`; `:122` its required fields; `:203` `MAX_ENTRY_LENGTH`; `pm_ai/core/rendering.py:848` lists every member in a sentence
- `pm_ai/app/entry.py:639` -- `GoalBook` (`read`, `set` at `:656`: check entry, write, append, `GoalUnrecorded`); `:269` where the CLI is given it; `:435` `DAEMON_BOUNDS`; `:198` composition for every parsed command
- `pm_ai/app/prompts.py:856` -- `_command`, the daemon's line runner, which passes no goal reader today; `:369` the refusal of a `TERMINAL_BOUND` line
- `pm_ai/surfaces/cli/dispatch.py:353` -- `GoalBook` protocol; `:2380` `_goal_prompt`, `:2397` `_goal_set`; `:2855` the `goal` group; `:890` `Command` (its `options` docstring says there are no flags); `:2768` `TERMINAL_BOUND`; `:2915` `_spelled`; `:3026` help words; `:3047`, `:3054` the top-level branch and `:3066` the leaf branch, both reading options only when `options` is declared; `:3226` `_options`, `:3292` `_split_options`, which would take the word after `--yes` as its value
- `pm_ai/surfaces/cli/repl.py:199` -- `_absolute_paths`, the one place that rebuilds words from `parsed.options`; it must carry flags too
- `tests/slice/test_goal_setting.py:265`, `:302`; `tests/slice/test_every_enrolled_project.py:1109-1118`; `tests/domain/test_entry_grammar_pins.py:88-116`; `tests/slice/test_line_requests_hardening.py:84-97` (every command the daemon runs declares a bound) -- tests this slice changes or must satisfy

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/goal_register.py` -- `delete_goal(raw, goal_id)` returning the new bytes and the removed lines together; every other byte kept; parsed back as the register without the id; a nested goal refused with its own sentence -- the pure half
- [ ] `pm_ai/domain/event_entries.py` -- `SelfActionType.GOAL_DELETED = "goal_deleted"` with fields `goal_id`, `domain`, `horizon`, `title`, `channel` -- CAP-10
- [ ] `pm_ai/app/entry.py` -- `GoalBook.delete(goal_id, shown)` (read again, refuse if the block differs from `shown`, cut a long title for the entry, check the entry, write, append, `GoalUnrecorded`) and `GoalBook.path`; list and show at the command line read the file by path before anything is composed; `DAEMON_BOUNDS` gains `goal list` and `goal show` at `config show`'s bound -- the writer, the reader, the bounds
- [ ] `pm_ai/app/prompts.py` -- `_command` passes a goal reader that reads the file afresh per line -- the daemon's reader
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- `Command.flags`, read in both parser branches, never taking a value, refused when given one or twice; `Parsed` carries the flags; `_spelled` prints `[--flag]`; subcommand `--help`; `goal set [<id>]` with options and the question rule; `goal update`, `goal delete`, `goal list`, `goal show`; `TERMINAL_BOUND` gains update and delete -- the commands
- [ ] `pm_ai/surfaces/cli/repl.py` -- `_absolute_paths` keeps the flags in the words it rebuilds -- no flag lost on the way to the daemon
- [ ] `tests/domain/test_entry_grammar_pins.py`, `tests/core/test_goal_render.py`, the goldens holding `rendering.py:848`'s sentence, `tests/slice/test_goal_setting.py:265`, `:302`, `tests/slice/test_every_enrolled_project.py:1109-1118` -- updated for `goal_deleted`, the new subcommands and the subcommand help -- existing tests that name the old surface
- [ ] `_bmad-output/specs/spec-pm-ai/stories/22b-goal-writer.md`, `4q-repl-through-the-daemon.md` -- when this slice is built: edit the amended frozen text in each and add a dated Change Log line naming what this slice amends (`22b`'s Never "No goal deletion"; `4q`'s list of commands that need this terminal) -- earlier frozen blocks say they were amended
- [ ] `tests/surfaces/test_cli_subcommands.py`, `tests/slice/test_goal_commands.py` -- one test per matrix row, the slice ones through `main()` against a temporary home, every "nothing written" row comparing the file's bytes before and after -- the matrix is the contract

**Acceptance Criteria:**
- Given two goals set, when `pm-ai goal delete g_a --yes` runs without a terminal, then `strategic_goals.md` holds only the other goal with every other byte unchanged, the personal event log's last entry is `goal_deleted` naming `g_a` with its domain, horizon and title, and the exit is 0.
- Given `g_a` on file, when `goal update g_a --horizon long` runs and then `goal show g_a`, then `g_a`'s line keeps its title and domain and `show` prints `long`.
- Given the daemon running, when `goal list` is typed at the prompt, then the daemon answers it inline with the same output as the command line.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged by this slice.

## Spec Change Log

- **2026-10-16, created** from Andrei's field-test feedback (finding 23, memlog UX decision "goals can be listed, shown, updated and deleted"). Decisions it carries: `goal set` stays the create path and still revises; `goal update` revises only and refuses an unknown id; `goal create` is not added; delete removes the goal's detail block, confirms at a terminal, `--yes` for scripts; deletion is never refused for references because nothing stored cites a goal at this commit, and a later citation to a deleted id renders unresolved (CAP-11); a new `goal_deleted` self-action; the command table gains value-less `flags`, which `4u` and `4w` reuse; set, update and delete run in the prompt's process, list and show through the daemon.
- **2026-10-16, review:** the question rule made one rule (any option on an existing id asks nothing else; none at a terminal asks all three; none without a terminal refused); the delete confirmation's answers, the re-read after the answer, nested goals, long titles in the entry; list and show compose nothing at the command line and are read afresh per line in the daemon with a declared bound; flags read in both parser branches, refused with a value or twice; subcommand `--help` added; rows for vocabulary refusals, flag position, no id, no options, broken settings; the existing tests this changes listed; the `prompts.py:383` task dropped (only `repl.py:199` rebuilds words); finding cited by file. Amends earlier frozen blocks: `22b`'s Never "No goal deletion" (lifted; its reason answered by the references bullet) and `4q`'s routing bullet (two more commands that need this terminal) — each spec's frozen text is edited and gains a dated Change Log line when this slice is built. Depends on spine amendment: AD-21 — `goal update`, `goal delete`, `goal list` and `goal show` join the foreground administrative commands parsed from the command line (the list already holds daemon-routed `dashboard` and `config show`). Depends on spine amendment: AD-7 — `strategic_goals.md` joins the files the daemon reads afresh for every line.
- **2026-10-16, review (cross-spec):** Change Log moved before Design Notes; the AD-21 amendment restated as additions to the foreground administrative commands parsed from the command line; the amendment task now edits the frozen text of `22b` and `4q` and adds a dated line to each, when this slice is built.

## Design Notes

`update` exists beside `set` because Andrei reached for it, and because a revise-only verb is the one that cannot create a goal by a typo. Deleting the detail lines with the goal follows `set_goal`, which already treats them as the goal's when it moves one; leaving them would put orphan bullets under the previous goal. The confirmation shows the exact lines and the file is read again after the answer, so nothing is removed that the person did not see. Cutting a long title in the entry rather than refusing: the file already holds the title whole, the entry's job is to say which goal went, and a goal that cannot be deleted because of its own title would have to be deleted by hand. List and show read by path at the command line because they must work when the thing broken is the rest of the machine. Subcommand `--help` lands here because this slice is the first in wave 3 to add subcommands.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_goal_commands.py tests/surfaces/test_cli_subcommands.py -q` -- expected: every matrix row passes
- `uv run pytest -q` -- expected: all pass; the skip count unchanged by this slice
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- `HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai goal set g_a --domain project --horizon medium --title "Cut latency"`, then `goal list`, `goal update g_a --title "Under 150ms"`, `goal show g_a`, `goal delete g_a` (answer `y`) -- expected: each prints as the matrix says; the event log holds two `goal_set` entries and one `goal_deleted`
