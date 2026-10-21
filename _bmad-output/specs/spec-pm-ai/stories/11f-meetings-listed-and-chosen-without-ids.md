---
title: 'transcript list, and a meeting chosen without copying its id'
type: 'feature'
created: '2026-10-21'
status: 'draft'
baseline_commit: '82c2edb'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** To ask for a meeting's summary (`7b`, `7c`), the PM must type its internal `mtg_…` id. After his first real `transcript drop` and `transcript summarize --by claude` (2026-10-21), Andrei had to copy the id from the drop's output and write the next command by hand. Nothing lists the meetings that have a transcript, so a meeting that was registered but never summarised cannot be found later. The dashboard (`7d`) shows only summaries recorded since its last render. Andrei's direction of 2026-10-21: the console is not the main path. It is the API that higher-level helpers and UI wrappers call, so every command that acts on an object needs a listing, a way to choose without memorised ids, and output that offers the next step.

**Approach:** A new read-only command, `pm-ai transcript list`, lists the meetings that have a transcript, newest first. Each meeting shows its title, start, scope, transcript state and summary state, read from the transcript and summary rows in the operational store and from the meeting records. No model is used. `--json` prints the same listing as one object with stable field names for wrappers, and no transcript text. Each listed meeting carries the exact command for its next step. At the `pm-ai>` prompt, `--meeting` on `transcript summarize` and `transcript drop` completes from the same meetings, showing each one's start and title. `transcript drop`'s closing line ends with the meeting's summary state and the exact `transcript summarize` command for it, as `7d`'s dashboard line already does. The id stays the only way to name a meeting on the command line, so scripts stay exact.

## Boundaries & Constraints

**Always:**

- **Prerequisites:** `7b` (the summary rows), `7c` (`--by claude`), `7d` (the dashboard's command line), `11e` (the drop's closing line and `--dry-run --json`), `4u` (completion). All are built.
- **Which meetings are listed.** A meeting is listed when its scope holds a meeting record for it and either a transcript row in the state `fetched`, `ingested`, `ingest_failed` or `refused`, or a summary row. A meeting whose transcript row is `absent` (Teams listed no transcript) or `skipped`, and that has no summary row, is not listed: that is every calendar meeting that was not recorded, and the dashboard and `doctor` already report them. A `refused` one is listed because the PM can still drop the downloaded file by hand.
- **What each meeting shows.**
  - its start, in `display_timezone` when set and otherwise in UTC, with the zone named once at the top. A start that `11e` estimated (`start_estimated_from` set) is marked as estimated.
  - its scope, its id and its title.
  - its transcript state: `fetched` (captured, not yet ingested), `ingested`, `ingestion failed` with the row's reason, or `refused` with the row's reason. It also says whether the capture is still kept. Captures are purged after 30 days (NFR-09); whether one is kept is read from the capture folder's file names, never from their contents.
  - its summary state: none, waiting (for the local model or for Claude, from the row's maker), summarized (by the local model or by Claude, which model, automatically or on request, on which date), failed with the row's reason, or nothing said.
  - its next step: the exact `pm-ai transcript summarize --scope <scope> --meeting <id>` command when a summary can be asked for, meaning the transcript is `ingested` and its capture is kept (the two checks `transcript summarize` makes first, `pipelines.py:3335`). For a `refused` transcript, it is `pm-ai transcript drop <file> --scope <scope> --meeting <id>`, with `<file>` left for the PM. Otherwise there is none.
- **Order and length.** Newest start first, then by scope and id. `--limit <n>` (1 to 200, default 20) caps the meetings shown, and the number left out is stated ("and 14 older"). A limit outside 1 to 200 is a usage error.
- **`--state`:** `all` (the default), `not-summarized`, `waiting`, `failed` or `summarized`.
  - `not-summarized`: the transcript is `ingested` and the summary is none, waiting or failed. These are the meetings a summary can be asked for.
  - `waiting`: the summary row is waiting.
  - `failed`: the summary failed, or the transcript failed ingestion. Both need the PM, and each row says which.
  - `summarized`: a summary is recorded.
  - A meeting whose summary found nothing said is listed only under `all`.
  - The words follow the command's own spelling (`summarize`) and the stored state (`summarized`).
- **`--scope`:** `personal` or `project:<id>`. Without it, the listing covers personal and every project in `projects.toml`. `people:<id>` is refused (exit 3) with `transcript summarize`'s reason: summaries are made only for meetings in `personal` or `project:<id>` (AD-4, `_DROPPABLE_KINDS`, `dispatch.py:3342`). A `project:<id>` that `projects.toml` does not enrol is refused (exit 3), naming it as not enrolled. When no `--scope` is given and one enrolled project's records or rows cannot be read, one line names that project and the reason, and the others are still listed.
- **The text form.** One line per meeting, with the transcript and summary states on an indented second line. A title, reason or model name has its line breaks and control characters folded to spaces before it is printed, so a title cannot make a second row. The last line names the exact command for the newest meeting listed that has no summary yet and can be summarised, and says that adding `--by claude` asks Claude. An empty listing is one sentence naming the filter and the scopes looked at, and says that `pm-ai transcript drop` adds a meeting.
- **The machine form, `--json`.** It prints one JSON object and nothing else on standard output, with these fields:
  - `format`: `1`.
  - `scopes`: the scopes looked at.
  - `unreadable`: a list of `{"scope", "reason"}`.
  - `more`: the number left out by `--limit`.
  - `meetings`: a list of objects, each with:
    - `scope`, `meeting_id`, `title`;
    - `start`: an ISO 8601 UTC instant;
    - `start_estimated`: true or false;
    - `transcript`: `{"state", "reason", "kept"}`, where `state` is one of `fetched`, `ingested`, `ingest_failed`, `refused`, `none`, and `reason` is `null` when there is none;
    - `summary`: `{"state", "made_by", "requested", "model", "reason", "at"}`, where `state` is one of `none`, `waiting`, `summarized`, `failed`, `nothing_said`, `made_by` is `local` or `claude` (the words `--by` takes, never the store's `frontier`), `at` is the row's last write as an ISO 8601 UTC instant, and fields with no value are `null`;
    - `next`: `{"summarize", "drop"}`, each a list of the command's words, or `null`.

  No transcript text, no summary text, no action item and no attendee is included. A new field may be added later without changing `format`. A field is never removed or renamed without changing it. Exit codes are the same as the text form's.
- **Read-only, no model, no daemon needed.** At a terminal the command runs in its own process and composes no daemon (`composes=False`, as `goal list`, `dispatch.py:1199`, `entry.py:252`). It reads the meeting records by their paths, and the operational store on a read-only connection of its own, as `summary_records_at` does (`service.py:1607`). It asks for no master key and no keychain: meeting records and `operational.db` are not encrypted (`scope_model.py:577`, `:769`). It writes nothing and calls no model. Exit 0 whenever it can report, including an empty listing or a stated unreadable project. Exit 2 for a usage error. Exit 3 for a refused scope.
- **At the prompt (AD-21).** `transcript list` joins `DAEMON_BOUNDS` (`entry.py:556`) with `CONFIG_SHOW_BOUND_SECONDS`, 1 second (`entry.py:550`): local reads and a print, answered inline, as `goal list` is. The daemon runs the same read function, so the prompt prints exactly what the terminal prints. It joins AD-21's list of foreground administrative commands (a spine amendment, see Design Notes).
- **Choosing a meeting at the prompt (recommended; see Ask First 1).** `--meeting` on `transcript summarize` and on `transcript drop` gets a completion source in place of free text. It is keyed by the `--scope` already on the line and offers that scope's meetings, newest first, at most 100.
  - For `summarize`: the meetings whose transcript is `ingested`.
  - For `drop`: every recorded meeting, as `member name --meeting` offers today (`entry.py:1841`).
  - Each candidate inserts the id and shows the start and title beside it. Typed text matches the start of the id or the start of any word of the title, ignoring case, so `pm-ai> transcript summarize --scope personal --meeting week<Tab>` offers `Example Weekly Sync-up`.
  - With no `--scope` on the line, or `people:`, nothing is offered.
  - The reader follows 4u's rules: it builds no `StorageService`, asks for no master key, keychain or daemon, offers nothing when it fails, and is read again after each command (`completion.py:1-21`).
- **The drop's next step.** `transcript drop`'s closing line (`dispatch.py:3656`) ends with the meeting's summary state, in the listing's words. When a summary can be asked for, it also gives the exact `pm-ai transcript summarize --scope <scope> --meeting <id>` command and says that adding `--by claude` asks Claude. It says nothing more about when an automatic summary will run, because that depends on the daemon and `local_model`.
- **One wording.** The listing's text, its JSON and the drop's closing line take each meeting's states and next step from one function in `pm_ai/app`, so they cannot disagree.
- **No real data.** Tests use `11d`'s fixture names (`Example Weekly Sync-up`, `Alex Morgan`, `Sam Rivera`) and `example.com` handles. No title, name or time from a real export.

**Ask First:**
1. **How a meeting is chosen without its id.** Recommended: completion of `--meeting` at the prompt, as above, together with the exact commands in the listing and in the drop's closing line. The id stays the only way to name a meeting on the command line, so a script or wrapper always acts on the meeting it means, and nothing new has to be learned. The alternatives:
   - (b) `transcript summarize --latest`: the newest meeting in the scope whose transcript is ingested and that has no summary. It is convenient at a shell. But "latest" changes as meetings arrive, so a delayed or repeated `--by claude` call could summarise, and pay for, a different meeting than the one meant.
   - (c) a short number from the last listing (`--meeting 3`). It needs the listing to be remembered between commands, and the number points elsewhere once a new meeting arrives. This is the most fragile option for scripts.
2. **`--json` on a real drop.** Today `--json` is allowed only with `--dry-run` (`11e`), so a wrapper that drops a file must read the meeting id out of the closing sentence. Recommended: yes, in this story. `transcript drop <file> --scope <scope> --json` prints one object with `--dry-run --json`'s fields, plus `meeting_id`, the listing's `summary` and `next`, and the staged count. Alternative: leave the drop as `11e` built it, and let wrappers use `--dry-run --json` and then `transcript list --json`. That cannot tell the dropped meeting apart from another new meeting on the same day.

**Never:** No transcript text, summary text, action item or attendee in the listing. No model call. No write of any kind, and no summary row offered or changed. No `people` scope read or offered. No meeting chosen by anything but its id on the command line. No index, position or "latest" that a script could send. No master key, keychain or daemon needed at a terminal. No change to `transcript summarize`'s checks, its refusals or `--by`. No change to `7d`'s dashboard.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| After a drop | `Example Weekly Sync-up` dropped in personal, summary row waiting | `transcript list`: one meeting, "transcript ingested, kept; summary waiting for the local model"; last line the exact summarize command and the `--by claude` hint | exit 0 |
| Newest first | three personal meetings and one in `project:alpha` | four meetings, newest start first; the zone named at the top | exit 0 |
| Estimated start | a drop whose start came from the file's modification time | its start marked estimated; `start_estimated: true` | exit 0 |
| Summarized by Claude | summary recorded by Claude on request | "summarized by Claude <model>, on request, on <date>"; `made_by: "claude"`, `requested: true` | exit 0 |
| Failed | summary failed after three timeouts | "summary failed: <reason>"; listed under `--state failed` and `not-summarized` | exit 0 |
| Ingestion failed | transcript row `ingest_failed` | "transcript ingestion failed: <reason>"; no summarize command; listed under `--state failed`, not under `not-summarized` | exit 0 |
| Purged | ingested 40 days ago, capture gone, no summary | "transcript ingested, capture purged"; `kept: false`; `next.summarize: null`; not under `not-summarized` | exit 0 |
| Refused by Teams | transcript row `refused` | listed with the reason; `next.drop` is the drop command with `<file>` | exit 0 |
| Not recorded | calendar meetings with `absent` transcript rows | not listed | N/A |
| Nothing said | summary row `nothing said` | shown under `all` only; `state: "nothing_said"` | exit 0 |
| Limit | 34 meetings, no `--limit` | 20 shown, "and 14 older"; `more: 14` | exit 0 |
| Bad limit | `--limit 0` or `--limit 500` | usage error naming 1 to 200 | exit 2 |
| Bad state | `--state done` | usage error naming the five words | exit 2 |
| People | `--scope people:example` | refused, naming personal and project scopes | exit 3 |
| One project unreadable | no `--scope`; `project:beta`'s folder missing | one line naming `project:beta` and why; personal and `project:alpha` listed; `unreadable` lists it | exit 0 |
| Empty | nothing dropped or fetched yet | one sentence naming the filter and scopes, and `pm-ai transcript drop` | exit 0 |
| Hostile title | a title holding a newline and a control character | folded to spaces on one line; JSON-escaped in `--json` | exit 0 |
| JSON | `--json` | one object; no text before or after; no transcript or summary text | exit 0 |
| No daemon | daemon not running, broken `config.toml` | listing printed; no keychain prompt | exit 0 |
| At the prompt | `pm-ai> transcript list` | the same text as the terminal, inline, within 1 second | N/A |
| Completion by title | `pm-ai> transcript summarize --scope personal --meeting week<Tab>` | offers the ingested `Example Weekly Sync-up`, showing its start and title; inserts its id | N/A |
| Completion, no scope | `transcript summarize --meeting <Tab>` with no `--scope` | nothing offered | N/A |
| Drop's next step | a drop of the fixture | the closing line ends "summary waiting; to ask now: `pm-ai transcript summarize --scope personal --meeting mtg_…`; add `--by claude` to ask Claude" | exit 0 |
| Drop joins a summarized meeting | the drop joins a meeting whose summary Claude made on request | the closing line names that summary state and still gives the command | exit 0 |

</frozen-after-approval>

## Code Map

Anchors checked against `main` at `82c2edb`.

- `pm_ai/surfaces/cli/dispatch.py:1142` -- `Command`; `:1199` `composes`; `:1225` `via_daemon`; `:2246` `_scope`; `:3249` `_goal_list`, the listing precedent; `:3313` `MEETING_ARGUMENT`; `:3328` `JSON_FLAG`; `:3342` `_DROPPABLE_KINDS`; `:3608` `_when`; `:3656` `_closing_line`; `:3765` `_preview_json`; `:3812` `_transcript_drop`; `:4014` `_summarize_target`; `:4050` `_transcript_summarize`; `:4281` `TERMINAL_BOUND`; `:4442` the `transcript` group (`:4445` `drop`, `:4458` `summarize`), where `list` joins them
- `pm_ai/app/entry.py:252` -- the `composes=False` branch, where the listing's reader is handed in; `:550` `CONFIG_SHOW_BOUND_SECONDS`; `:556` `DAEMON_BOUNDS`; `:1786` `_completion`; `:1814` `_project_scope`; `:1841` `_meeting_ids`, today project-only and ids only
- `pm_ai/surfaces/cli/completion.py:1-21` -- 4u's rules for readers; `:65` `FREE_TEXT`; `:85` `FREE_TEXT_NAMES` (holds `meeting` today); `:103` `MEETING_IDS`; `:115` `_BY_COMMAND`; `:163` `Readers`; `:270` `Candidate` (`display` only today); `:410` `complete`, whose filter matches the start of the value (`:421`); `:538` `_named_project`
- `pm_ai/surfaces/cli/editor.py:125` -- where a `Candidate` becomes prompt_toolkit's completion, which also takes a description
- `pm_ai/storage/service.py:226` -- `transcript_state`; `:261` `meeting_summaries` (`meeting_start`, `made_by`, `requested`, `model`, `reason`, `at`); `:1607` `summary_records_at`, the read-only connection pattern; `:4545` `transcript_states`; `:4962` `summary_state`; `:5100` `summaries_recorded`
- `pm_ai/app/pipelines.py:3261` -- `SUMMARY_MAX_AGE`; `:3335` `_prepare`, `transcript summarize`'s checks; `:4615` `offer_summaries`; `:4683` `request_summary`; `:4764` `note_summary_input`, which offers the row after a drop
- `pm_ai/core/meeting_records.py:162` -- `MEETINGS`; `:185` `_RECORD_NAME` (the UTC day leads the file name); `:202` `start_estimated_from`; `:423` `record_name`; `:453` `capture_stem`; `:520` `has_capture`; `:633` `parse_record`; `:962` `get`
- `pm_ai/domain/storage_tiers.py:121` -- `CAPTURES`
- `pm_ai/domain/meeting_summaries.py:135` -- `SummaryMaker` (`from_word`); `:173` `SummaryStateKind`; `:186` `SummaryState`
- `pm_ai/domain/transcripts.py:294` -- `TranscriptStateKind`
- `pm_ai/domain/scope_model.py:577`, `:673`, `:769` -- `meetings/` not encrypted in the personal, people and project trees
- `pm_ai/core/rendering.py:1013` -- `7d`'s line naming the summarize command; `:1989` `fold_breaks`
- `tests/slice/test_line_requests_hardening.py:95` -- every command the daemon runs declares its bound; `tests/surfaces/test_cli_completion.py:304` -- every declared name has a source or is free text; `tests/surfaces/test_cli_subcommands.py:793` -- every leaf is reachable
- `ARCHITECTURE-SPINE.md:369` -- AD-21, the foreground list and the two tables; `:137` -- AD-4

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/app/` (a new module beside `goals.py`) -- the read: meetings in a scope from records by path, the two rows from a read-only connection, capture names; each meeting's states and next step; personal plus enrolled projects; an unreadable project stated -- one function for the terminal, the daemon and the drop's line
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- `transcript list` in `TABLE` (`--scope`, `--state`, `--limit`, `--json`; `composes=False`); the text and JSON forms; `_closing_line` ends with the state and the command -- the listing and the next step
- [ ] `pm_ai/app/entry.py` -- the reader handed in on the `composes=False` path; `("transcript", "list")` in `DAEMON_BOUNDS`; `_meeting_ids` extended to personal, with title and start, newest first, filtered to ingested for `summarize` -- terminal, prompt, completion
- [ ] `pm_ai/surfaces/cli/completion.py`, `editor.py` -- `MEETING_IDS` for `summarize` and `drop`'s `--meeting`; a source for `--state`; `--limit` free text; `Candidate` gains a description; matching by id or title word -- choosing at the prompt
- [ ] Ask First 2, if accepted: `--json` on a real drop -- a wrapper learns the meeting it made
- [ ] `tests/slice/test_transcript_list.py` and the existing completion, drop and bound tests -- one test per matrix row
- [ ] `ARCHITECTURE-SPINE.md` -- AD-21's amendment, re-derived through the architecture skill -- the foreground list stays complete

**Acceptance Criteria:**
- Given a fixture dropped in personal and nothing else, when `pm-ai transcript list` runs with no daemon, then one meeting is listed with its title, estimated start, `ingested` and `waiting`, the last line is the exact summarize command, and the exit is 0.
- Given the same, when `pm-ai transcript list --json` runs, then standard output parses as one object whose meeting's `next.summarize` equals the words of that command, and it holds no utterance text.
- Given a `people:` scope, when listing, then it is refused with exit 3 and nothing is read from it.
- Given the change, when `uv run pytest` runs, then everything passes, and the bound, completion-source and reachability tests pass without edits to their checks.

## Spec Change Log

- **2026-10-21, created** from Andrei's direction of 2026-10-21 after his first real drop and Claude summary: the CLI is an API layer, so meetings need a listing, a machine form, a way to choose without memorised ids, and the next step offered. The drop's own fields stay `11e`'s.

## Design Notes

**Why the id stays the only name on the command line.** A wrapper calls `transcript list --json` and passes back the `meeting_id` it chose, so it never needs anything else. A person at the prompt gets completion by title. A person at a shell copies a whole command from the listing or from the drop's closing line, not an id. Anything that resolves at run time ("latest", a row number) can name a different meeting than the one the caller saw, and with `--by claude` that costs money.

**Why it reads by path and composes nothing.** The meeting records and `operational.db` are not encrypted, and `doctor` and completion already read them this way. Composing a daemon would need `config.toml` and the keychain, and a listing should work when those are broken, as `goal list` does (`4t`). The daemon uses the same function at the prompt, so the two outputs cannot drift.

**Staying inside the bound.** A record's file name begins with its UTC day (`meeting_records.py:185`). So the listing can order meetings by the names in the folder and parse only the records it will show, plus any others on the oldest day shown. Rows are read once per scope. `--limit`'s ceiling of 200 keeps the prompt within its 1 second.

**Spine amendment this depends on** (re-derived through the architecture skill, a task above):
- **AD-21** (`:369`): `transcript list` (`11f`) joins the foreground administrative commands and runs at the prompt through `DAEMON_BOUNDS`, inline, under 1 second.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_transcript_list.py tests/slice/test_transcript_drop.py tests/surfaces/test_cli_completion.py tests/slice/test_line_requests_hardening.py tests/surfaces/test_cli_subcommands.py -q` -- expected: every matrix row passes
- `uv run pytest -q` -- expected: all pass; the skip count unchanged
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a scratch home (`HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1`), drop the recorded fixture, then run `pm-ai transcript list` and `pm-ai transcript list --json`, with no daemon and then at the prompt with one. Expected: the same meeting both times; the summarize command from the listing runs as printed; at the prompt, `--meeting week<Tab>` offers the meeting by title.
