---
title: 'Small surface fixes from the field test'
type: 'bugfix'
created: '2026-10-16'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Three things the field test showed a first-time user (findings 2, 6, 13 and 26 in `_bmad-output/implementation-artifacts/field-test-2026-10-15.md`, which lands with PR #71, and Andrei's feedback after first use). Both connector enrolments end with a sentence that stopped being true: Graph's says "nothing harvests from it until scheduled harvesting exists", GitLab's that "It becomes active at the next start … nothing is harvesting from it yet" — since 4r every job reads the connector rows afresh, and since 9a a running daemon's harvest timer picks a new connector up the next time it reads the connector list (the field test's GitLab connector was harvested 7.5 minutes after the daemon started). The daemon prints nothing when it stops, so Ctrl-C leaves a terminal that looks hung. And a project dashboard stays empty by default: every meeting is filed as personal until the person hand-edits a `categories` object in `connectors/<instance>.json`, which enrolment only mentions in its last sentence.

**Approach:** Replace the two endings with what is true, in one shared sentence. Print one line when the daemon stops, saying it stopped cleanly or why not. Let `connector add graph` — and so setup's step 4, which runs the same code — ask, for each enrolled project, which Outlook category marks its meetings, saving the answers in the row it already writes; and add `pm-ai connector categories <instance>`, which asks the same questions, through the same function, for a Graph connector enrolled at any time.

## Boundaries & Constraints

**Always:**
- **(a) One sentence for when a new connector is harvested**, used by both enrolments: a running daemon needs no restart and starts harvesting it the next time it reads the connector list — normally within a minute when no other connector is set up, otherwise at the next scheduled harvest of any connector, normally within 240 minutes; a daemon started afterwards normally harvests it within its first fifteen minutes. ("Normally": a harvest already running when the list is due delays the read until it ends.) Both endings add, when the new row files into a project, that a project enrolled after the running daemon started keeps the daemon from building this connector until it is restarted, and print the shared restart sentence (`RESTART_SENTENCE`) after it. The two docstrings that repeat the old claim (`core/connector_enrolment.py`, `app/wiring.py`) say the same.
- **(b) The daemon says how it stopped**, as one line on standard output (the stream its "listening on" line uses), printed after the store is closed, and the same words, without the store part, in `daemon.log` before the log is closed:
  - a clean stop names the first stop request it got (Ctrl-C, a stop signal, or the terminal closing) and how many jobs — commands sent to the daemon that had not finished — were marked failed, or that none was left unfinished; renders and harvests marked by the same step stay in `daemon.log` as today and are not counted;
  - a clean stop whose marking of unfinished jobs failed says so, naming the error's type, and that the next start marks them;
  - the stops that are not clean keep what they print today and print no clean-stop line: the writer thread stopping on its own (for example the store's schema version changing under the daemon) prints "the daemon stopped: …", exit 3, even when that happens during the stop's own marking step; a second Ctrl-C while the stop waits for queued steps prints "interrupted again; stopping at once", exit 3; an unexpected error that ends serving is logged by type and leaves as today; a start that was refused, or interrupted before the daemon was listening, prints today's refusal and no stop line;
  - a second Ctrl-C that arrives while the web server is still closing is the server's own forced exit: the stop then continues as a clean stop and prints the line, exit 0;
  - a stop signal or terminal close that arrives after the server has handed signals back, while queued steps finish, is handled by the operating system's default as today: the process ends and no line is printed;
  - exit codes do not change. A console that can no longer be written to (the terminal closed) does not turn a clean stop into a failure: the write's error is ignored and standard output is pointed at nothing before the interpreter's own flush, so the exit stays 0; the line is still in `daemon.log`. When closing the store fails, the console line is not printed and the failure is reported as today.
- **(c) Outlook categories are asked, not hand-edited.** After the four existing questions and before the Microsoft sign-in, `connector add graph` asks once per enrolled project, in the registry's order, naming the project: which Outlook category marks its meetings, Enter for none. Answers are trimmed of surrounding spaces. The registry is read when the questions start, so the project setup's step 2 just registered is offered; an unreadable `projects.toml` is a refusal that saves nothing. With no project enrolled nothing is asked. The answers are judged together, by the rules the row is read with (`CategoryScopes`): two projects given the same category, ignoring case, is refused naming both and the later question asked again. The answers are saved as the row's `categories` object, in the same write that saves the rest of the row; Enter for every project saves no `categories` key, as today. A typed `-` where nothing can be removed is refused ("there is nothing to remove; press Enter for none") and asked again, so no category is ever saved as `-`.
- **(c) Free text, not a list.** Listing the person's Outlook categories is `GET /me/outlook/masterCategories`, which needs `MailboxSettings.Read`; pm-ai's declared permissions are the seven in `GRAPH_RESOURCE_SCOPES` and do not include it, and adding one forces every enrolled person to consent again. So the category is typed as it appears in Outlook.
- **(c) The closing sentence says what was mapped**: each category and the project its meetings now file into, that everything else is personal, and that `pm-ai connector categories <instance>` changes it. With nothing mapped it says every meeting is filed as personal and names the same command.
- **(c) `pm-ai connector categories <instance>`** (Andrei, 2026-10-16) works on any enrolled Graph row, enabled or not, and asks through the one function `connector add graph` and setup use:
  - **It shows the mapping first**: each category and its project as the row holds it, or "none — every meeting is filed as personal". An entry naming a project no longer enrolled is shown, said to be dropped when the answers are saved (the row cannot be built with it), not asked about, and left out of the same-category check. A `categories` value the row reader refuses (not an object, a blank category) is shown as it is written and replaced by the answers: the command is how such a row is repaired.
  - **Then one question per enrolled project, the current answer offered**: Enter keeps the category the project has now (or none); a typed category replaces it; `-` removes it. A project mapped by hand to several categories shows them all; Enter keeps them all, a typed one replaces them all, `-` removes them all. Re-typing the current category in another case counts as keeping it, in its stored spelling. The shared function offers `-` only when a project has something to remove, so a new connector's questions read as before. Two projects may swap categories in one run: the check is over the new answers, not the old mapping.
  - **It saves the `categories` key and nothing else.** At save time it takes the lock that enrolment takes before writing a row, reads the row again, and refuses, writing nothing, if the `categories` it finds differ from those it showed (another run or a hand edit got there first), if the row is gone, if a project it is about to save was removed from the registry meanwhile, or if the lock is held past the wait a row write gets; otherwise it sets `categories` and writes every other key back as it read it — as `record_user_id` does for `user_id` (8q). Removing every mapping removes the key. When the resulting mapping equals the one shown, nothing is written and it says so; dropping a stale entry is a change and is written. A meeting already filed stays where it was; the next harvest files by the new mapping, since every job reads the row afresh (4r).
  - **After saving, when the mapping names a project**, it prints the same project clause and `RESTART_SENTENCE` as (a): a running daemon that started before one of those projects was enrolled stops building this connector — and so stops harvesting it — until it is restarted.
  - **It refuses**, writing nothing, exit 3: no row for the instance, a row that is not Graph's, a row file that cannot be read or is not a JSON object, an unreadable `projects.toml`, no terminal, an interruption at any question ("nothing was saved"), and the save-time refusals above. With no project enrolled it shows the mapping, says there is nothing to map, asks nothing, and exits 0.
  - **It asks questions, so it needs the person's own terminal**: it joins the commands the daemon refuses to run by name, and at the `pm-ai` prompt it runs locally, in the prompt's own process, as `goal set` does. It needs no project and composes no harvest; it talks to no Microsoft service.
- **4p's setup is kept as it is**: the same four steps in the same order, the same y/n at step 4 with Enter as no, the same "already done" reading of an enabled Graph row (nothing asked, nothing written), and every interruption or refusal inside the step — at a category question too — ending setup at step 4 as it does today.

**Ask First:** Asking the category questions from setup's already-done step 4 (the command covers that machine). Adding `MailboxSettings.Read` so the categories can be listed. More than one category per project at the question.

**Never:**
- No change to how a running daemon finds new rows, to the harvest cycle or to how first harvests are spaced after a start — only the sentences describing them.
- No change to stop behaviour: what is marked, what is waited for, and the exit codes stay as 4r built them.
- No category guessed or defaulted from a project's name, no Graph call to find categories, and no change to how a mapped meeting is filed.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Graph enrolled, nothing mapped | `connector add graph`, Enter at every category | the shared sentence; "every meeting is filed as personal" and the categories command named; old sentences absent | N/A |
| Graph enrolled, a project mapped | `Alpha` for `alpha` | the shared sentence, the project clause and `RESTART_SENTENCE` | N/A |
| GitLab enrolled | `connector add gitlab`, valid answers | the shared sentence, the project clause and `RESTART_SENTENCE`; "next start" and "nothing is harvesting" absent | N/A |
| Ctrl-C, nothing running | daemon idle | stdout: stopped on Ctrl-C, no job left unfinished; same words in `daemon.log` | exit 0 |
| Stop signal with jobs unfinished | `SIGTERM`, two jobs still running | stdout: stopped on a stop signal, 2 unfinished jobs marked failed | exit 0 |
| Two stop requests | `SIGTERM`, then `SIGHUP` while the server closes | the line names the stop signal (the first) | exit 0 |
| Terminal closed | `SIGHUP`; standard output closed | write error ignored; `daemon.log` has the line | exit 0 |
| Marking failed at stop | the marking step raises | the line says unfinished jobs were not marked (error type) and the next start marks them | exit 0 |
| Writer stopped on its own | schema version changed, before or during the stop's marking step | today's "the daemon stopped: …" only | exit 3 |
| Second Ctrl-C while the server closes | Ctrl-C twice quickly | the server's forced exit; the stop continues; the clean-stop line | exit 0 |
| Second Ctrl-C while queued steps finish | Ctrl-C during the wait | today's "interrupted again; stopping at once."; no clean-stop line | exit 3 |
| Stop signal while queued steps finish | `SIGTERM` during the wait | ended by the operating system's default; no line | as today |
| Ctrl-C during start | before the listening line | today's behaviour; no stop line | as today |
| Store close fails | closing raises | the log line written; no console line; today's error | as today |
| One project, a category | project `alpha`; ` Alpha ` typed | row has `categories: {"Alpha": "alpha"}` | N/A |
| Same category twice | `Alpha` for `alpha`, `alpha` for `beta` | refused naming both; `beta`'s question asked again | re-asked |
| `-` with nothing to remove | `-` at a new connector's question | refused, "nothing to remove"; asked again | re-asked |
| No project enrolled | personal only | no category question; prompts as today | N/A |
| Through setup | step 2 registered `alpha`; step 4 `y` | asked for `alpha`; saved in the same row | N/A |
| Interrupted at a category question | Ctrl-C there | `connector add graph`: "nothing was saved"; setup: stops at step 4 naming steps 1–3 as done | exit 3 |
| Categories command, first mapping | `graph:work` with no `categories`; `Alpha` for `alpha` | "none" shown; row gains `{"Alpha": "alpha"}`; every other key unchanged; project clause and `RESTART_SENTENCE` | N/A |
| Categories command, keep | `{"Alpha": "alpha"}`; Enter, or `ALPHA` | nothing written; says nothing changed | N/A |
| Categories command, replace | `{"Alpha": "alpha"}`; `Platform` | row holds `{"Platform": "alpha"}` | N/A |
| Categories command, swap | `{"A": "alpha", "B": "beta"}`; `B`, then `A` | row holds `{"B": "alpha", "A": "beta"}` | N/A |
| Categories command, remove | `{"Alpha": "alpha"}`, the only entry; `-` | `categories` key removed | N/A |
| Categories command, stale entry | `{"Old": "gone"}`, `gone` not enrolled; Enter for the rest | shown as dropped; not asked; written without it | N/A |
| Categories command, several by hand | `{"A": "alpha", "B": "alpha"}`; Enter | both kept; nothing written | N/A |
| Categories command, unreadable mapping | `categories: ["Alpha"]` | shown as written; answers replace it | N/A |
| Categories command, disabled row | `graph:work` with `enabled: false` | asked and saved as for an enabled row | N/A |
| Categories command, no project | personal only | mapping shown; "nothing to map"; nothing asked or written | exit 0 |
| Categories command, not Graph | `gitlab:alpha` | refused naming the instance's system; nothing written | exit 3 |
| Categories command, no row | `graph:none` | refused naming the instance | exit 3 |
| Categories command, bad row file | `graph:work.json` not a JSON object | refused naming the file | exit 3 |
| Categories command, no terminal | stdin is a pipe | refused; nothing asked | exit 3 |
| Categories command, interrupted | Ctrl-C at a question | "nothing was saved" | exit 3 |
| Changed while answering | a hand edit or a second run changes `categories` before the save | refused: changed meanwhile, nothing saved | exit 3 |
| Project removed while answering | `alpha` dropped from the registry before the save | refused naming `alpha`; nothing saved | exit 3 |
| Row lock held | another enrolment holds the lock past the wait | refused as busy; nothing saved | exit 3 |
| Other key changed while answering | a sign-in records `user_id` meanwhile | the new `user_id` kept; only `categories` replaced | N/A |
| Categories command, through the daemon | sent as a line to the daemon | refused by name as a command that needs its own terminal | exit 3 |
| Categories command, at the prompt | `connector categories graph:work` at `pm-ai` | asked and saved in the prompt's own process | N/A |

</frozen-after-approval>

## Code Map

Verified against `a1182f8` on 2026-10-16. 8s lands before this slice and changes `dispatch.py` and `wiring.py`, so the line numbers there move; find each place by the name given.

- `pm_ai/surfaces/cli/dispatch.py:1215-1231` -- Graph's ending: "…a running daemon needs no restart; nothing harvests from it until scheduled harvesting exists." and the categories sentence naming `graph.settings_file(instance)`
- `pm_ai/surfaces/cli/dispatch.py:1328-1334` -- GitLab's ending: "It becomes active at the next start — connectors are registered when the daemon is composed, so nothing is harvesting from it yet."
- `pm_ai/surfaces/cli/dispatch.py:1125-1170` -- `_connector_add_graph`'s docstring step 3 ("Outlook categories are not asked") and the no-terminal refusal ("asks four questions"), both stale after this slice
- `pm_ai/surfaces/cli/dispatch.py:2756-2760` -- `RESTART_SENTENCE`, the shared sentence printed after the project clause, and the precedent for one more beside it
- `pm_ai/core/connector_enrolment.py:47-51`, `pm_ai/app/wiring.py:866-870` -- the two docstrings repeating "active at the next start"
- `pm_ai/app/ticks.py:13-30`, `:270-320` -- what is true: the list is read at every wake, again within `CYCLE` (240 minutes) once any connector is set up, within `FIRST_RETRY` (one minute) otherwise; a new connector is due at the read that finds it; after a start all are spaced over `START_SPREAD` (fifteen minutes, `core/cadence.py:77`); a row for a project enrolled since the start is logged as not built
- `pm_ai/app/wiring.py:978-1003`, `pm_ai/connectors/graph/__init__.py:309-370`, `:440-457` -- `_files_into` names every project a Graph row's categories point at; `CategoryScopes` refuses a project the registry does not know, so a Graph row mapped to a project the daemon did not load is not built at all — the case the project clause covers
- `pm_ai/app/daemon.py:144-187` -- `serve`: the request log first, `_serve`, then `detach` and `storage.close()` in `finally`; the console line goes after the close, the log line before the detach
- `pm_ai/app/daemon.py:413-414`, `:445-483` -- the writer stopping on its own raises `DaemonRefused("the daemon stopped: …")`; `_stop_writer`'s second-interrupt line; `_drain` calls `requests.shutdown_pass()` and drops its result, logging only a warning when it fails
- `pm_ai/app/prompts.py:656-695` -- `shutdown_pass` returns the ids of the jobs (request rows) it marked failed — the count; renders are marked in the same pass and logged
- `pm_ai/app/daemon.py:813-858` -- `_Server`: `capture_signals` hands the previous handlers back when serving ends; `_stop(sig)` sees each signal and sets `force_exit` on a second Ctrl-C; `STOP_SIGNALS` at `:126`; `_announce` (stdout) at `:517`
- `pm_ai/surfaces/cli/dispatch.py:1172-1213` -- the four questions, then `graph.sign_in(…)` and `enrol_connector(…, settings=signed.settings)`; the category questions go between the reach-back and the sign-in
- `pm_ai/surfaces/cli/dispatch.py:1484-1560`, `:2893-2898`, `:2768-2780` -- `connector sign-in`, the model for a command on an enrolled Graph connector (`takes=("instance",)`, `target=_application`); its command-table entry; `TERMINAL_BOUND`, which the new command joins: the prompt runs those locally (`surfaces/cli/repl.py:105`) and the daemon refuses them by name (`app/prompts.py:369`)
- `pm_ai/app/wiring.py:1524`, `:1611-1655`, `:1657-1778` -- `GraphEnrolment`: `refusal(…)` gains the category judgement; `sign_in` builds `settings` (`:1770-1776`) and gains the mapping; `pm_ai/app/entry.py:215-219`, `:543-555` where it is built for commands and for setup, both over a storage that can read `projects.toml`
- `pm_ai/core/connector_enrolment.py:667-700` -- `record_user_id`: re-read at write time under the lock enrolment takes on the sealed credential store, one key set, every other kept; `record_categories` beside it, the same shape plus the compare
- `pm_ai/connectors/graph/auth.py:107-128` -- `GRAPH_RESOURCE_SCOPES`, seven permissions, no `MailboxSettings.Read`, and the note that a scope added later forces a new sign-in
- `pm_ai/surfaces/cli/dispatch.py:2202-2240` -- setup's step 4 wrapping the shared path; unchanged
- `tests/slice/test_connector_add_graph.py:277-305` -- asserts four prompts, "a running daemon needs no restart", and `"categories" not in row`; its `machine` fixture enrols `alpha` and its scripted terminal treats running out of answers as Ctrl-D, so every existing script meets a new category question; `tests/slice/test_connector_add_gitlab.py:250-262` asserts `"next start"`; `tests/slice/test_daemon_slice.py:253-256` the three stop signals; `tests/slice/test_setup_connector_step.py`

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- one constant for the harvested-when sentence and one for the project clause beside `RESTART_SENTENCE`, used by both endings and the categories command; the shared category-question function (current answers offered, `-` to remove only when there is something, answers trimmed and judged together, re-asked until accepted) called by `_connector_add_graph` after the reach-back and by the new `connector categories <instance>` (command-table entry, `TERMINAL_BOUND`, mapping shown, save, refusals); the closing sentences from what was mapped; the stale docstring step 3 and the "asks four questions" refusal reworded -- (a) and (c)
- [ ] `pm_ai/app/wiring.py`, `pm_ai/core/connector_enrolment.py` -- the two docstrings say what is true since 4r and 9a; `GraphEnrolment` reads the enrolled projects when the questions start, judges a set of answers, and `sign_in` puts a non-empty mapping into the settings under `CATEGORIES_KEY`; `record_categories`, shaped as `record_user_id`, comparing the categories it finds with those shown and re-judging the projects before writing -- (a), (c), behind the composition root
- [ ] `pm_ai/app/daemon.py` -- the first stop request recorded by `_Server._stop`; `_drain` keeps `shutdown_pass`'s result or its failure; `serve` logs the line before the log is detached and prints it after the store is closed, ignoring a failed write and pointing standard output at nothing on failure; no line on the not-clean stops -- (b)
- [ ] `tests/slice/test_connector_add_graph.py`, `tests/slice/test_connector_add_gitlab.py`, `tests/slice/test_setup_connector_step.py` -- every existing script re-baselined for the category question (after 8s's re-baseline of the same files); the endings' new sentences and the absence of the old ones; every new-connector category row of the matrix, the saved row asserted on disk; step 4 reaching the question for the project step 2 registered -- (a), (c)
- [ ] `pm_ai/surfaces/cli/completion.py` -- give `connector categories <instance>` its value source, the enrolled Graph instances (the rows `connector sign-in` completes from), so 4u's test that every command argument has a source passes -- tab completion (4u) covers the new command
- [ ] `tests/slice/test_connector_categories.py` -- new: every "Categories command" and "changed while answering" row, rows asserted on disk byte for byte where nothing is written -- the command
- [ ] `tests/slice/test_daemon_slice.py` -- every stop row, including standard output closed with exit 0 and the line in `daemon.log` -- (b)
- [ ] `_bmad-output/specs/spec-pm-ai/stories/8l-connector-add-graph.md`, `8o-connector-add-gitlab.md` -- frozen text edited and a dated Spec Change Log line added in each, when this slice is built: the ending no longer says "active at the next start" but when a running daemon picks the connector up; 8l also: categories are now asked after the four questions (Andrei, 2026-10-16) -- their frozen text says otherwise
- [ ] `_bmad-output/specs/spec-pm-ai/stories/4p-setup-offers-a-connector.md` -- frozen text edited and a dated Spec Change Log line added, when this slice is built: step 4 now asks the category questions through the shared path; its shape, order and already-done reading unchanged -- its Never says no category question
- [ ] `_bmad-output/specs/spec-pm-ai/stories/4r-a-line-is-answered-or-acknowledged.md`, `4q-repl-through-the-daemon.md` -- frozen text edited and a dated Spec Change Log line added in each, when this slice is built: `connector categories` joins the commands that need their own terminal (refused through the daemon, run locally at the prompt) -- the command joins the list
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark resolved the field test's stale-endings entry and the "nothing printed when the daemon stops" part of its small-gaps entry -- what this closes

**Acceptance Criteria:**
- Given a running daemon that loaded project `alpha` and harvests `graph:work`, when `pm-ai connector categories graph:work` maps `Alpha` to `alpha` and a meeting tagged `Alpha` is then harvested, then its record is filed in `alpha` and the next project dashboard lists it — no restart needed.
- Given a running daemon, when `pm-ai connector categories graph:work` maps a category to a project enrolled after the daemon started, then the output carried the project clause and `RESTART_SENTENCE`, and `connector check` through the daemon names `graph:work` as not built until a restart.
- Given the daemon is run by a supervisor that sends `SIGTERM`, when it stops, then the supervisor reads exactly one stop line after the listening
 line and the exit code is 0.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged from the baseline at the slice's start.

## Spec Change Log

- **2026-10-16, created.** Carries: one shared sentence for when a new connector is harvested (read at the daemon's next list read — a minute, or at most 240 minutes; fifteen minutes after a start), GitLab adding the restart case for a project enrolled since the start; a stop line on the console after the store closes and in `daemon.log` before the log closes, naming the signal and the jobs marked failed, with halt, second interrupt and refused start keeping today's lines and every exit code unchanged; Outlook categories typed as free text, one per enrolled project, Enter for none, after the four questions and before the sign-in, judged by `CategoryScopes`' rules and saved in the same row write, because listing them needs `MailboxSettings.Read`, which is not declared; setup's step 4 inherits the questions, its shape and its already-enrolled reading unchanged; `pm-ai connector categories <instance>` (Andrei, 2026-10-16) asks the same questions for any enrolled Graph row through the same function: the mapping shown first, Enter keeping a project's current category, a typed one replacing it, `-` removing it, entries for projects no longer enrolled dropped on save and said so, only the `categories` key written under enrolment's claim and nothing written when nothing changed, a terminal command run locally at the prompt and refused through the daemon.
- **2026-10-16, review:** a Graph row mapped to a project the running daemon did not load is not built at all, so both endings and the categories command print a project clause and `RESTART_SENTENCE`; "within" times reworded as normal, not guaranteed; the stop line's stream (stdout), its first stop request, what "jobs" counts, standard output closed (exit 0), a failed store close, a writer stop during the marking step, the two second-Ctrl-C outcomes, a signal during the final wait, and Ctrl-C during start each settled with a row; category answers trimmed and judged together (swaps allowed), stale entries out of the check and dropped on save, `-` refused where nothing can be removed, case-only re-typing kept, an unreadable mapping shown and replaced, the save comparing the categories it shows with those it finds and re-judging the projects, a busy lock, an unreadable `projects.toml` and a disabled row covered; re-baseline of the existing test scripts and the stale `dispatch.py` texts added to the tasks; change-log tasks for 8l, 8o, 4p, 4r and 4q; tasks merged per file; findings cited with the report; plain words for the start spacing, the writer stopping on its own and the row lock; repeated acceptance criteria replaced by system-level ones. Depends on spine amendment: AD-21 — `connector categories` joins the "foreground administrative commands parsed from the command line"; one amendment together with 4t's and 4w's additions to that list.
- **2026-10-16, review (cross-spec):** the Change Log moved before Design Notes; `connector categories` "joins the list" of terminal commands rather than counting it; the AD-21 amendment stated in the spine's own sense and batched with 4t's and 4w's; a `completion.py` task for the command's value source (4u); the Code Map noted as taken before 8s moves its lines; tasks amending done specs say their frozen text is edited, with a dated Change Log line, when this slice is built.

## Design Notes

Why the questions sit before the sign-in: `connector add graph` places each step where its refusal costs least (its docstring's own rule). A refused category costs a re-typed line; asked after the sign-in, an interruption there would throw away a browser sign-in. Setup inherits the order because it runs the same function.

Listing categories from the calendar instead (the categories on recent meetings, readable with `Calendars.Read`) was considered and not chosen: it adds a Graph call to enrolment and misses a category not yet used on a meeting.

Choices made in review (2026-10-16):
- The project clause is printed whenever the new or saved row files into a project, not only when the daemon really missed that project: the CLI cannot see which projects a running daemon loaded, and a sentence that says "if" is true either way.
- The save refuses rather than merges when `categories` changed under it, since two people's answers about the same mapping have no right merge; every other key is merged as `record_user_id` does.
- A stale entry is dropped on save because a row holding it is not built at all; dropping it is the repair.
- `-` where nothing can be removed is refused rather than read as Enter, so a slip is shown back.
- "Jobs" in the stop line means commands sent to the daemon, the rows `shutdown_pass` counts; renders and harvests already have their own log lines.
- A stop signal after the server hands signals back keeps today's operating-system default, because changing it is a stop-behaviour change this slice's Never rules out.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_connector_add_graph.py tests/slice/test_connector_add_gitlab.py tests/slice/test_setup_connector_step.py tests/slice/test_daemon_slice.py tests/slice/test_connector_categories.py -q` -- expected: all pass
- `uv run pytest -q` -- expected: all pass; the skip count unchanged from the slice's baseline
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks (if no CLI):**
- `pm-ai daemon run`, then Ctrl-C: one line after the listening line says how it stopped.
