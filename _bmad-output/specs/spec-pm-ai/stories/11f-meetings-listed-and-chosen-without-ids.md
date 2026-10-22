---
title: 'transcript list, and a meeting chosen without copying its id'
type: 'feature'
created: '2026-10-21'
status: 'draft'
baseline_commit: '82c2edb'
review_loop_iteration: 1
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** To ask for a meeting's summary (`7b`, `7c`), the PM must type its internal `mtg_…` id. After his first real `transcript drop` and `transcript summarize --by claude` (2026-10-21), Andrei had to copy the id from the drop's output and write the next command by hand. Nothing lists the meetings that have a transcript, so a meeting that was registered but never summarised cannot be found later. The dashboard (`7d`) shows only summaries recorded since its last render. Andrei's direction of 2026-10-21: the console is not the main path. It is the API that higher-level helpers and UI wrappers call, so every command that acts on an object needs a listing, a way to choose without memorised ids, and output that offers the next step.

**Approach:**
- A new read-only command, `pm-ai transcript list`, lists the meetings that have a transcript, newest first. Each shows its title, start, scope, transcript state, summary state, and the exact next command or the reason there is none. It reads the transcript rows and summary rows in the operational store and the meeting records. No model is used.
- `--json` prints the same listing as one object, with fixed field names and no transcript text.
- A meeting is chosen without its id in two ways. At the `pm-ai>` prompt, Tab completes `--meeting` with each meeting's start and title shown. On `transcript summarize`, `--title <words> [--on <date>]` picks exactly one meeting, or is refused with the candidates listed. `--meeting <id>` still works, and wins over both.
- `transcript drop` gains `--json` for a real drop, and its closing line ends with the meeting's summary state and next command.
- `transcript summarize` on a meeting that already holds a summary is refused unless `--again` is given (Andrei, 2026-10-22).

## Boundaries & Constraints

**Always:**

- **Prerequisites:** `7b` (the summary rows), `7c` (`--by claude`), `7d` (the dashboard's command line), `11e` (the drop's closing line and `--dry-run --json`), `4u` (completion). All are built.

### Which meetings are listed

- A meeting is listed when its scope has a transcript row in the state `fetched`, `ingested`, `ingest_failed` or `refused`, or a summary row in any state. Each (scope, meeting id) pair is listed once.
- A transcript row in the state `absent` (Teams listed no transcript) or `skipped`, with no summary row, is not listed: that covers every calendar meeting that was never recorded. With a summary row, it is listed.
- A `refused` transcript is listed, because the PM can still drop the downloaded file by hand.
- **The record is the one source of the title, the start and the order.** The summary row's `meeting_start` is not used. A row whose record cannot be parsed is listed as "record unreadable", and a row with no record as "record missing", each with its scope and id. Neither is dropped. Both are listed after the dated meetings and do not count against `--limit`.

### What each meeting shows

- **Start.** It uses the drop's form (`dispatch.py:3608`, `_when`): the instant in UTC, and in `display_timezone` too when one is set. `display_timezone` is read from `config.toml` by its path. The header line says which zone was used: the configured zone, or "UTC (no display_timezone is set)". A start that `11e` estimated (`start_estimated_from` set) is marked as estimated.
- **Scope, id and title.** An empty title shows as `(untitled)`.
- **Transcript state.** One of:
  - `fetched`: captured; the daemon ingests it at its next run.
  - `ingested`, with whether the transcript is kept. **Kept** means every stored part's capture file is present. A meeting with no stored parts is kept when its Teams capture or its dropped capture is present. This is the rule `meeting_utterances` applies (`transcripts.py:612`). It is read from the capture folder's file names, never from their contents. A capture's name starts with its record's name (`capture_stem`, `meeting_records.py:453`). No automatic purge exists yet (NFR-09 is not built), so a missing capture is called missing, never purged.
  - `ingestion failed`, with the row's reason.
  - `refused by Teams`, with the row's reason.
  - `absent` or `skipped`, only when a summary row brought the meeting in.
  - `unknown`, when the operational store cannot be read.
- **Summary state.** One of:
  - none;
  - waiting for the daemon's next harvest, by the local model. This is a waiting row that the harvest offers (`summaries_to_offer`, `service.py:4971`): not a request for Claude, a meeting that started within the last 30 days (`SUMMARY_MAX_AGE`, `pipelines.py:3261`), an ingested transcript, and `local_model` set in `config.toml`. When `local_model` is not set, the line says so: no harvest makes it;
  - waiting for you to ask Claude again: a request for Claude that is still waiting, which the harvest never offers;
  - waiting, not offered automatically: the meeting started more than 30 days ago;
  - summarized: by the local model or by Claude, which model, automatically or on request, and on which date;
  - failed, with the row's reason;
  - nothing said: the transcript held no words after cleaning;
  - `unknown`, when the store cannot be read.

  Whether the record holds a summary is told apart from the row's state. A re-opened row, where a later part was ingested, is waiting while the record still holds the earlier summary.
- **The next step, or why there is none.** Every state has one or the other:
  - transcript ingested and kept, no summary held, and the state is not nothing said: `pm-ai transcript summarize --scope <scope> --meeting <id>`;
  - a summary held, transcript ingested and kept: the re-run, `… --again`, labelled as replacing the summary;
  - transcript `refused` or `ingestion failed`: drop the file again with `pm-ai transcript drop --scope <scope> --meeting <id> <file>`;
  - transcript `fetched`: "ingested at the daemon's next run";
  - capture missing: "the transcript is missing, so the summary cannot be made";
  - nothing said: "the transcript held no words after cleaning";
  - `absent` or `skipped`: "Teams listed no transcript";
  - record unreadable or missing: the reason;
  - store unreadable: "the operational store could not be read".

  When no daemon answers on the loopback port, the summarize step says to start `pm-ai daemon run` first. The check is the prompt's own: the token file and the port are read and a connection is opened and closed (`loopback.py:316`). No keychain is asked for.

### Order, filters and length

- Newest start first, then by scope and id.
- `--limit <n>`: 1 to 200, default 20. A value out of range or not a number is a usage error (exit 2). The number of meetings that match the filter but were left out is stated ("and 14 older").
- `--state`: `all` (the default), `not-summarized`, `waiting`, `failed` or `summarized`. The words follow the command's spelling (`summarize`) and the stored state (`summarized`).
  - `not-summarized`: no summary held, the transcript ingested and kept, and the state is not nothing said. These are the meetings a plain `summarize` acts on.
  - `waiting`: the summary row is waiting, of any of the three kinds.
  - `failed`: the summary failed, or the transcript failed ingestion. Each row says which.
  - `summarized`: the record holds a summary.
  - A nothing-said meeting appears only under `all`.

### Scopes

- `--scope personal` or `--scope project:<id>`. A malformed `--scope` is a usage error (exit 2).
- With no `--scope`, the listing covers personal and every project in `projects.toml`, read by its path at each call. A project enrolled after the daemon started is therefore listed at the prompt too. The command's target is `personal`, as `dashboard`'s is without `--scope` (`dispatch.py:2234`).
- `people:<id>` is refused (exit 3) before anything is read, with `transcript summarize`'s reason: summaries are made only for meetings in `personal` or `project:<id>` (AD-4, `_DROPPABLE_KINDS`, `dispatch.py:3342`).
- A `project:<id>` that `projects.toml` does not enrol is refused (exit 3), naming it as not enrolled.
- **What cannot be read is named in one line, and everything else is still listed, exit 0:**
  - an enrolled project whose folder or records cannot be read, whether named with `--scope` or found with none;
  - with no `--scope`, a malformed `projects.toml`: personal is listed. A missing `projects.toml` means no projects are enrolled, which is not a fault;
  - the operational store: locked, corrupt, a newer schema, or a missing column. The meetings are then the records that have a capture file, found by name, with both states `unknown`. Under any `--state` other than `all`, nothing is listed and the line says why.
- A missing `meetings/` folder is an empty scope, not an unreadable one.

### Reading

- Read-only, with no model and no daemon needed. At a terminal the command composes no daemon (`composes=False`, as `goal list`; `dispatch.py:1199`, `entry.py:252`).
- The operational store is one store for the application (`scope_model.py:474`; `paths.resolve(APPLICATION, OPERATIONAL_DB)`). It is opened read-only on a connection of its own, as `summary_records_at` does (`service.py:1607`). The transcript rows, the parts rows and the summary rows are read in one read transaction, so they agree with each other.
- The records are read by path. No master key or keychain is asked for: meeting records and `operational.db` are not encrypted (`scope_model.py:474`, `:577`, `:769`).
- Nothing is written and no model is called.
- **Reading no more than it shows.** The rows are read first. Each listed meeting's record is found by the id's digest at the end of its file name (`_members_for`, `meeting_records.py:1267`), without parsing it. The UTC day at the start of the file name gives the order by day. Only the records shown are parsed, plus the others on the oldest day shown, to order within that day. `more` is counted from the rows and the names.
- **Timed, not asserted.** A test times the listing over about 1,000 calendar records and 200 transcript rows, and Tab completion over 100 candidates. Each must finish within the prompt's bound of 1 second. The measured times are recorded in this spec's Change Log at build.

### The text form

- One line per meeting: the start, scope, id and title. An indented second line gives the transcript and summary states. An indented third line gives the next step or the reason there is none.
- Titles, reasons and model names have control and format characters (Unicode categories Cc and Cf, which include the right-to-left overrides) replaced by spaces, then runs of spaces collapsed. A title is cut at 80 characters with `cut_at_word` (`rendering.py:2000`), which ends a cut with "…". So a title can neither forge a second row nor reorder the line.
- Output is written with `errors="replace"`, so a title the terminal cannot encode cannot fail the listing.
- The last line names the exact command for the newest meeting under `not-summarized`, if there is one, and says that adding `--by claude` asks Claude, which is a paid call. When no meeting qualifies, the line is left out.
- An empty listing is one sentence naming the filter and the scopes looked at, and says that `pm-ai transcript drop` adds a meeting.
- Exit codes: 0 whenever it can report, 2 for a usage error, 3 for a refused scope.

### The machine form, `--json`

One JSON object, and nothing else, on standard output. It is written with `ensure_ascii=True`. Exit codes are the text form's.

- **Top level:**
  - `format`: always the number `1`.
  - `zone`: the configured zone, or `"UTC"`.
  - `scopes`: a list of the scopes looked at, each spelled `personal` or `project:<id>`.
  - `daemon_reachable`: true or false.
  - `unreadable`: a list of `{"source", "reason"}`. `source` is `personal`, `project:<id>`, `projects.toml` or `operational.db`; `reason` is a string.
  - `more`: a whole number, 0 or more.
  - `meetings`: a list of meeting objects.
- **Each meeting:**
  - `scope`: a string.
  - `meeting_id`: a string.
  - `record`: `"readable"`, `"unreadable"` or `"missing"`.
  - `title`: a string, `null` unless the record is readable.
  - `start`: an ISO 8601 UTC instant, `null` unless the record is readable.
  - `start_estimated`: true or false, `null` unless the record is readable.
  - `transcript`: `{"state", "reason", "kept"}`.
    - `state` is one of `fetched`, `ingested`, `ingest_failed`, `refused`, `absent`, `skipped`, `none` (no row) or `unknown`.
    - `reason` is a string or `null`.
    - `kept` is true or false for `ingested`, and `null` otherwise.
  - `summary`: `{"state", "held", "made_by", "requested", "model", "reason", "at", "offered_automatically"}`.
    - `state` is one of `none`, `waiting`, `summarized`, `failed`, `nothing_said` or `unknown`.
    - `held` is true when the record holds a summary; `null` when the record is not readable.
    - `made_by` is `local` or `claude`, the words `--by` takes, never the store's `frontier`.
    - `requested` is true or false.
    - `model` and `reason` are strings.
    - `at` is the row's last write, as an ISO 8601 UTC instant.
    - `offered_automatically` is true only for the first kind of waiting above.
    - Fields that have no value are `null`. Every field but `held` is `null` when `state` is `none` or `unknown`.
  - `next`: `{"summarize", "resummarize", "drop", "why"}`.
    - `summarize` and `resummarize` are lists of the command's words after the program name, or `null`. `resummarize` ends with `--again`.
    - `drop` is `{"words": [...], "needs": ["file"]}` or `null`. The caller appends the file path as the last word; no placeholder is written into the words.
    - `why` is a string when there is no step, or when a step needs the daemon started first, and `null` otherwise.
- **Change rules.** A new field, or a new value of an enumerated field, does not change `format`. A wrapper must treat a value it does not know as "other". A field is never removed, renamed or given a different type without changing `format`.
- **Never included:** transcript text, summary text, action items or attendees.
- **One example per state** (meeting objects only; `start` and `at` abbreviated as `"…Z"`):
  - Waiting for the harvest:

    ```
    {"scope":"personal","meeting_id":"mtg_a","record":"readable","title":"Example Weekly Sync-up","start":"…Z","start_estimated":true,"transcript":{"state":"ingested","reason":null,"kept":true},"summary":{"state":"waiting","held":false,"made_by":"local","requested":false,"model":null,"reason":"the transcript was ingested; …","at":"…Z","offered_automatically":true},"next":{"summarize":["transcript","summarize","--scope","personal","--meeting","mtg_a"],"resummarize":null,"drop":null,"why":null}}
    ```
  - Summarized by Claude:

    ```
    {"scope":"project:alpha","meeting_id":"mtg_b","record":"readable","title":"Example Planning","start":"…Z","start_estimated":false,"transcript":{"state":"ingested","reason":null,"kept":true},"summary":{"state":"summarized","held":true,"made_by":"claude","requested":true,"model":"<model>","reason":"…","at":"…Z","offered_automatically":false},"next":{"summarize":null,"resummarize":["transcript","summarize","--scope","project:alpha","--meeting","mtg_b","--again"],"drop":null,"why":null}}
    ```
  - Ingestion failed:

    ```
    {"…":"…","transcript":{"state":"ingest_failed","reason":"…","kept":null},"summary":{"state":"none","held":false,"made_by":null,"requested":null,"model":null,"reason":null,"at":null,"offered_automatically":null},"next":{"summarize":null,"resummarize":null,"drop":{"words":["transcript","drop","--scope","personal","--meeting","mtg_c"],"needs":["file"]},"why":null}}
    ```
  - Capture missing:

    ```
    {"…":"…","transcript":{"state":"ingested","reason":null,"kept":false},"summary":{"state":"waiting","…":"…"},"next":{"summarize":null,"resummarize":null,"drop":null,"why":"the transcript is missing, so the summary cannot be made"}}
    ```
  - Store unreadable:

    ```
    {"scope":"personal","meeting_id":"mtg_d","record":"readable","title":"…","start":"…Z","start_estimated":false,"transcript":{"state":"unknown","reason":null,"kept":null},"summary":{"state":"unknown","held":true,"made_by":null,"requested":null,"model":null,"reason":null,"at":null,"offered_automatically":null},"next":{"summarize":null,"resummarize":null,"drop":null,"why":"the operational store could not be read"}}
    ```

### Choosing a meeting without its id (Andrei, 2026-10-22)

- **`--title <words> [--on <date>]` on `transcript summarize`.** It resolves to exactly one meeting in the scope, among the meetings `transcript list` lists there.
  - Matching: every word given appears as a word of the meeting's title. Words are compared after `normalised_title`'s folding (`meetings.py:39`: NFKC, case-folded) and split on whitespace and punctuation. So `--title weekly` matches `Example Weekly Sync-up`, and Cyrillic titles match the same way.
  - `--on <YYYY-MM-DD>` keeps only the meetings whose start falls on that date in the listing's zone.
  - Exactly one match is used, and the acknowledgement names its id and title.
  - No match is refused (exit 3), naming `pm-ai transcript list --scope <scope>`.
  - Two or more matches are refused (exit 3). The refusal lists each candidate's id, start and title, at most 10 with a count of the rest, and suggests `--on` or `--meeting`. A script therefore never acts on a meeting it did not name.
  - `--on` without `--title` is a usage error (exit 2).
  - `--meeting <id>` wins. When it is given with `--title` or `--on`, the id is used, and the acknowledgement says the other options were not consulted.
  - The resolution runs in the daemon, where `summarize` runs, through the same reading code the listing uses.
  - `--title` and `--on` are free text for completion.
  - There is no "latest" and no row number.
- **`transcript drop`** already takes `--title` with `11e`'s meaning, the meeting's title. With that title it joins exactly one same-day record or makes a new one. Its selection is Ask First 1.
- **Tab completion at the prompt**, for `--meeting` on `transcript summarize` and on `transcript drop`, in place of free text:
  - It is keyed by the `--scope` already on the line, personal or a project. `people:`, or no `--scope`, offers nothing.
  - For `summarize`, it offers the meetings that pass the same check as the listing's summarize step: ingested and kept.
  - For `drop`, it offers every recorded meeting that has started, the most recent first.
  - Typed text matches the start of the id, or the start of any word of the title, folded as `--title` is. The matches are filtered first and then capped at 100.
  - Each candidate inserts the id and shows the start and title beside it, with the title folded and cut as in the text form.
  - The reader follows 4u's rules: it builds no `StorageService`, asks for no master key, keychain or daemon, offers nothing when it fails, and is read again after each command (`completion.py:1-21`). Reading `operational.db` read-only is the exception 4u's readers already grant to `7e` (`completion.py:163`).
  - `Readers.meetings` changes from taking a project id and returning ids to taking a scope and returning (id, start, title). `_Cached` keeps only `str` values today (`completion.py:311`), so it gets a variant for these.

### The re-summary guard (Andrei, 2026-10-22)

- `transcript summarize` on a meeting whose record already holds a summary is refused (exit 3) unless `--again` is given. Nothing is asked.
- The refusal names the summary held (who made it, and on which date) and `--again`. It says whether the run would cost money: with `--by claude` it is a paid Claude call; the local model costs nothing.
- The check is made in `request_summary` (`pipelines.py:4683`), from the record's summary (`MeetingRecord.summary`, `meeting_records.py:372`), before the row changes or any job starts.
- The automatic re-offer when a later part is ingested (`note_summary_input`) is not affected.
- `--again` on a meeting with no summary is accepted and changes nothing.
- This amends `7b` and `7c`.
- Every summarize command pm-ai prints adds `--again` when the record holds a summary, through one function:
  - `7d`'s dashboard line (`rendering.py:1013`);
  - the "asks again" sentences (`pipelines.py:3563`, `:4293`);
  - `doctor`'s probe sentence (`entry.py:1713`);
  - the listing and the drop's line.

### `transcript drop`

- **The next step (amends `11e`).** After the drop commits, its closing line (`dispatch.py:3656`) reads the meeting's summary row and ends with that state, in the listing's words, including none. It then gives the next step as the listing would. It says nothing about when an automatic summary will run beyond the listing's wording. `--dry-run` carries no summary state.
- **`--json` on a real drop (Andrei, 2026-10-22; amends `11e`).** `transcript drop <file> --scope <scope> --json` prints one object with these fields:
  - `format`: 1;
  - `title`, `start`, `duration_minutes` and `participants`, exactly as in `--dry-run --json`;
  - `outcome`: `{"kind": "join", "meeting_id", "title"}` or `{"kind": "new", "candidates": [ids]}`;
  - `meeting_id`;
  - `staged` and `left`: whole numbers;
  - `summary` and `next`: the listing's objects for this meeting;
  - `notices`: a list of strings.

  A refused drop prints nothing on standard output, prints its refusal on standard error, and exits 3, as today. `--dry-run --json` gains `"format": 1`, which under the change rules is an addition.

### Where it runs, and one wording

- **At the prompt (AD-21).** `transcript list` joins `DAEMON_BOUNDS` (`entry.py:556`) with `CONFIG_SHOW_BOUND_SECONDS`, 1 second (`entry.py:550`), answered inline as `goal list` is. The daemon's `_command` (`prompts.py:1071`) hands it the same reader, so the prompt prints exactly what the terminal prints with a daemon running.
- **One wording.** A pure function in `pm_ai/core` decides each meeting's states and next step from the rows, the capture names, the record and the clock. It returns a typed value (`ListedMeeting`, declared in `pm_ai/domain`). `pm_ai/app` does the reads and builds it, for the listing and, for one meeting, for the drop. `TranscriptDropped` carries it. One renderer in `pm_ai/core` turns it into words for the listing's text and the drop's line. The JSON is built from the same value. A test checks that the two wordings agree for the same meeting.
- **No real data.** Tests use `11d`'s fixture names (`Example Weekly Sync-up`, `Alex Morgan`, `Sam Rivera`) and `example.com` handles. A Cyrillic title is invented in the test. No title, name or time from a real export is used.

**Ask First:**
1. **Choosing an existing meeting by title on `transcript drop`.** Andrei decided `--title <words> [--on <date>]` for both commands, but on `drop`, `--title` already means the meeting's title (`11e`): with it, the drop joins one same-day record or makes a new one, and never refuses for finding none. Recommended: keep `--title`'s meaning on `drop`, and add `--on <date>` there as the selector. With `--on`, the drop joins exactly one existing meeting whose title matches the words on that date, or is refused with the candidates, and never makes a new record. Without `--on`, the drop behaves as `11e` built it. Alternatives:
   - (b) `--title` on `drop` becomes a selector as on `summarize`. That withdraws `11e`'s title option and its title-on-the-same-day join.
   - (c) no title selection on `drop`; Tab completion and `--meeting` only.

**Never:** No transcript text, summary text, action item or attendee in the listing or in either JSON form. No model call in the listing or in completion. No write of any kind by the listing or by completion, and no summary row offered or changed by them. No `people` scope read or offered. No meeting chosen by "latest" or by a row number. No summarize acting on a meeting that `--title` matched ambiguously. No replaced summary without `--again`. No master key, keychain or daemon needed by the listing at a terminal. No change to `transcript summarize`'s other checks, its refusals or `--by`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| After a drop | `Example Weekly Sync-up` dropped in personal; summary row waiting; `local_model` set; daemon running | one meeting: "transcript ingested; summary waiting for the daemon's next harvest (local model)"; next: the summarize command; last line the same command and the `--by claude` paid-call hint | exit 0 |
| No local model | as above, `local_model` unset | "summary waiting; no local_model is set, so no harvest makes it"; `offered_automatically: false` | exit 0 |
| Claude request waiting | a requested Claude row still waiting | "summary waiting for you to ask Claude again"; `made_by: "claude"`, `requested: true`, `offered_automatically: false` | exit 0 |
| Older than 30 days | a waiting row for a meeting 40 days ago | "waiting, not offered automatically: the meeting started more than 30 days ago" | exit 0 |
| No daemon | as "After a drop", no daemon on the port | the next step says to start `pm-ai daemon run` first; `daemon_reachable: false`; `next.why` says so | exit 0 |
| Newest first | three personal meetings and one in `project:alpha` | four meetings, newest start first; the header names the zone used | exit 0 |
| Zone | `display_timezone` unset | the header says "UTC (no display_timezone is set)"; `zone: "UTC"` | exit 0 |
| Estimated start | a drop whose start came from the file's modification time | its start marked estimated; `start_estimated: true` | exit 0 |
| Summarized by Claude | a summary recorded by Claude on request | "summarized by Claude <model>, on request, on <date>"; `next.resummarize` ends with `--again`, labelled as replacing it; `next.summarize: null` | exit 0 |
| Re-opened | the record holds a summary; the row is waiting after a later part | listed under `waiting` and `summarized`, not under `not-summarized`; `held: true` | exit 0 |
| Failed | the summary failed after three timeouts | "summary failed: <reason>"; listed under `failed` and `not-summarized` | exit 0 |
| Ingestion failed | transcript row `ingest_failed` | "transcript ingestion failed: <reason>"; `next.drop` with `needs: ["file"]`; listed under `failed`, not under `not-summarized` | exit 0 |
| Fetched | transcript row `fetched` | "transcript fetched"; `next.why` "ingested at the daemon's next run" | exit 0 |
| Capture missing, one part | ingested, its one capture file gone | "capture missing"; `kept: false`; `next.why` "the transcript is missing, so the summary cannot be made" | exit 0 |
| Capture missing, many parts | three stored parts, part 2's capture gone | `kept: false`; not offered by completion for `summarize`; not under `not-summarized` | exit 0 |
| Refused by Teams | transcript row `refused` | listed with the reason; `next.drop` | exit 0 |
| Not recorded | calendar meetings with `absent` or `skipped` rows and no summary row | not listed | N/A |
| Absent with a summary row | an `absent` transcript row and a summary row | listed; transcript `absent`; `next.why` "Teams listed no transcript" | exit 0 |
| Nothing said | summary row `nothing said` | listed under `all` only; `state: "nothing_said"`; `next.why` says why | exit 0 |
| `--state waiting` | one of each waiting kind and a summarized one | the three waiting ones only | exit 0 |
| `--state summarized` | two summarized, one waiting, one re-opened | the two and the re-opened one | exit 0 |
| `--state` and `--limit` | 30 not summarized, `--state not-summarized --limit 5` | 5 shown; `more: 25` | exit 0 |
| Limit | 34 meetings, no `--limit` | 20 shown, "and 14 older"; `more: 14` | exit 0 |
| Bad limit | `--limit 0`, `--limit 500` or `--limit ten` | usage error naming 1 to 200 | exit 2 |
| Bad state | `--state done` | usage error naming the five words | exit 2 |
| Bad scope | `--scope projectalpha` | usage error | exit 2 |
| People | `--scope people:example` | refused, naming personal and project scopes; nothing read | exit 3 |
| Unknown project | `--scope project:nope` | refused as not enrolled | exit 3 |
| One project unreadable | no `--scope`; `project:beta`'s folder missing | one line naming `project:beta` and why; personal and `project:alpha` listed; `unreadable` lists it | exit 0 |
| Named project unreadable | `--scope project:beta`, folder missing | the line naming it; no meetings; `unreadable` lists it | exit 0 |
| Registry malformed | no `--scope`; `projects.toml` malformed | personal listed; `projects.toml` named as unreadable | exit 0 |
| No `meetings/` | an enrolled project with no `meetings/` folder | an empty scope, not unreadable | exit 0 |
| Store unreadable | `operational.db` locked, corrupt, newer schema or a missing column | the records with a capture file listed, states `unknown`; `operational.db` named; under `--state failed`, nothing listed and the line says why | exit 0 |
| Record unreadable | a summary row whose record is malformed; another whose record is missing | both listed at the end as "record unreadable" / "record missing" with scope and id; not counted against `--limit` | exit 0 |
| Same meeting twice | a transcript row and a summary row for one (scope, id) | listed once | exit 0 |
| Enrolled after start | a project enrolled after the daemon started; `pm-ai> transcript list` | the project's meetings listed | N/A |
| Empty | nothing dropped or fetched yet | one sentence naming the filter and scopes, and `pm-ai transcript drop`; no closing command line | exit 0 |
| Nothing qualifies | every meeting summarized | no closing command line | exit 0 |
| Hostile title | a title holding a newline, a control character and a right-to-left override | replaced by spaces on one line; escaped in `--json` (ASCII only) | exit 0 |
| Long or empty title | a 300-character title; an empty title | cut at a word with "…" at 80; `(untitled)` | exit 0 |
| Cyrillic title | `Еженедельная встреча` | shown as written; `--title встреча` matches it; completion offers it for `вст` | exit 0 |
| JSON | `--json` | one object; no text before or after; no transcript or summary text | exit 0 |
| No daemon, broken config | daemon not running, `config.toml` malformed | listing printed in UTC; no keychain prompt | exit 0 |
| At the prompt | `RequestPath.prompt("transcript list")` against a daemon over the same home | byte for byte the terminal's output, inline, within 1 second | N/A |
| Timed | about 1,000 calendar records, 200 transcript rows | the listing within 1 second; the time recorded | N/A |
| `--title`, one match | `transcript summarize --scope personal --title weekly` with one such meeting | asks for that meeting's summary; the acknowledgement names its id and title | exit 0 |
| `--title`, several | three `Example Weekly Sync-up` meetings | refused, listing the three (id, start, title), suggesting `--on` or `--meeting`; nothing asked of a model | exit 3 |
| `--title --on` | the same three, `--on` naming one's date | that one is used | exit 0 |
| `--title`, none | `--title quarterly` with no match | refused, naming `pm-ai transcript list --scope personal` | exit 3 |
| `--on` alone | `--on 2026-09-14` without `--title` | usage error | exit 2 |
| `--meeting` wins | `--meeting mtg_a --title other` | `mtg_a` used; the acknowledgement says `--title` was not consulted | exit 0 |
| Re-summary refused | `transcript summarize --meeting mtg_b --by claude`, a summary held | refused, naming the summary held, `--again`, and that a Claude run is paid; no row changed | exit 3 |
| Re-summary, local | the same with `--by local` | refused, naming `--again` and that the local model costs nothing | exit 3 |
| `--again` | the same with `--again` | asked as today | exit 0 |
| Dashboard line | `7d`'s digest for a summarized meeting | its command ends with `--again` | N/A |
| Completion by title | `pm-ai> transcript summarize --scope personal --meeting week<Tab>` | offers the ingested and kept `Example Weekly Sync-up`, showing its start and title; inserts its id | N/A |
| Completion, not ingested | a meeting with transcript `fetched` | not offered for `summarize` | N/A |
| Completion, drop | `transcript drop f.vtt --scope personal --meeting <Tab>` | the personal meetings that have started, most recent first | N/A |
| Completion, no scope | `transcript summarize --meeting <Tab>` with no `--scope` | nothing offered | N/A |
| Completion cap | 150 matching meetings | 100 offered, newest first, after filtering | N/A |
| Drop's next step | a drop of the fixture, daemon running | the closing line ends with "summary waiting for the daemon's next harvest (local model)" and the summarize command | exit 0 |
| Drop joins a summarized meeting | the drop joins a meeting whose record holds Claude's summary | the closing line names that state and the `--again` re-run | exit 0 |
| Drop `--json` | a drop of the fixture with `--json` | one object: `format`, the dry run's fields, `outcome` `new`, `meeting_id`, `staged`, `summary`, `next`, `notices` | exit 0 |
| Drop `--json`, refused | the same file into a meeting that already has a capture | nothing on standard output; the refusal on standard error | exit 3 |

</frozen-after-approval>

## Code Map

Anchors checked against `origin/main` at `82c2edb` (2026-10-22).

- `pm_ai/surfaces/cli/dispatch.py:1142` -- `Command`; `:1199` `composes`; `:1225` `via_daemon`; `:2234` `_dashboard_target`, personal without `--scope`; `:2246` `_scope`; `:3249` `_goal_list`; `:3313` `MEETING_ARGUMENT`; `:3328` `JSON_FLAG`; `:3329` `DROP_FLAGS`; `:3342` `_DROPPABLE_KINDS`; `:3608` `_when`; `:3656` `_closing_line`; `:3765` `_preview_json`; `:3812` `_transcript_drop`; `:4014` `_summarize_target`; `:4050` `_transcript_summarize`; `:4281` `TERMINAL_BOUND`; `:4442` the `transcript` group (`:4445` `drop`, `:4458` `summarize`)
- `pm_ai/app/entry.py:252` -- the `composes=False` branch; `:550` `CONFIG_SHOW_BOUND_SECONDS`; `:556` `DAEMON_BOUNDS`; `:1713` `doctor`'s summarize sentence; `:1757` `_loopback`; `:1786` `_completion`; `:1814` `_project_scope`; `:1841` `_meeting_ids`; `:1891` `_enrolled_projects`
- `pm_ai/app/prompts.py:373` -- `RequestPath`; `:414` `prompt`; `:1071` `_command`, which hands each reader to `dispatch`
- `pm_ai/surfaces/cli/loopback.py:316` -- `reachable`: reads the token file and the port, and opens and closes a connection
- `pm_ai/surfaces/cli/completion.py:1-21` -- 4u's rules; `:65` `FREE_TEXT`; `:85` `FREE_TEXT_NAMES`; `:103` `MEETING_IDS`; `:115` `_BY_COMMAND`; `:163` `Readers` (the `7e` exception for `operational.db`); `:270` `Candidate`; `:300` `_Cached` (`:311`, `str` only); `:410` `complete` (`:421` the prefix filter); `:538` `_named_project`
- `pm_ai/surfaces/cli/editor.py:125` -- a `Candidate` becomes prompt_toolkit's completion
- `pm_ai/storage/service.py:226` -- `transcript_state`; `:242` `transcript_parts`; `:261` `meeting_summaries`; `:1607` `summary_records_at`; `:4545` `transcript_states`; `:4962` `summary_state`; `:4971` `summaries_to_offer`; `:5100` `summaries_recorded`
- `pm_ai/app/pipelines.py:1326` -- `TranscriptDropped`; `:3261` `SUMMARY_MAX_AGE`; `:3335` `_prepare`, `summarize`'s checks (record, transcript row `ingested`, every capture present, words left); `:3563`, `:4293` the "asks again" sentences; `:4615` `offer_summaries`; `:4683` `request_summary`, where the guard goes; `:4764` `note_summary_input`
- `pm_ai/core/transcripts.py:612` -- `meeting_utterances`, the rule for whether the transcript is kept
- `pm_ai/core/meeting_records.py:162` -- `MEETINGS`; `:185` `_RECORD_NAME`; `:202` `start_estimated_from`; `:372` `MeetingRecord.summary`; `:423` `record_name`; `:453` `capture_stem`; `:633` `parse_record`; `:962` `get`; `:1267` `_members_for`; `:1750` `_digest_suffix`
- `pm_ai/domain/meetings.py:39` -- `normalised_title`
- `pm_ai/domain/storage_tiers.py:121` -- `CAPTURES`
- `pm_ai/domain/meeting_summaries.py:135` -- `SummaryMaker`; `:173` `SummaryStateKind`; `:186` `SummaryState`
- `pm_ai/domain/transcripts.py:294` -- `TranscriptStateKind`
- `pm_ai/domain/scope_model.py:474` -- `operational.db`, one application store, not encrypted; `:577`, `:673`, `:769` -- `meetings/`, not encrypted
- `pm_ai/core/rendering.py:1013` -- `7d`'s summarize command; `:2000` `cut_at_word`
- `pm_ai/core/config.py:264` -- `display_timezone`
- Tests: `tests/surfaces/test_cli_subcommands.py:786-799` (reachability), `:1149` `_Dropped`, `:1501` `test_json_without_dry_run_is_a_usage_error_naming_dry_run`; `tests/surfaces/test_cli_completion.py:304` (every name has a source), `:373` `test_no_tab_reaches_the_daemon_or_opens_a_store`; `tests/slice/test_member_updates_slice.py:735-756` (asserts drop's `--meeting` is free text); `tests/slice/test_line_requests_hardening.py:95` (every daemon command declares its bound)
- `ARCHITECTURE-SPINE.md:369` -- AD-21; `:137` -- AD-4
- `stories/7b-a-transcript-yields-summary-decisions-and-actions.md`, `stories/7c-a-frontier-summary-on-request.md`, `stories/7d-summaries-and-actions-on-the-dashboard.md`, `stories/11e-transcript-drop-extracts-what-it-can.md` -- the specs this amends

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/` -- `ListedMeeting` and its next step: states, whether a summary is held, kept, the commands as word lists, the reason -- the typed value both callers share
- [ ] `pm_ai/core/` (a new I/O-free module) -- deciding each meeting's states and next step from rows, capture names, the record and the clock; the words for the text form; the `--title` match; the folding and cutting of titles -- one rule, one wording
- [ ] `pm_ai/app/` (a new module beside `goals.py`) -- the reads: `projects.toml`, `config.toml`'s zone and `local_model`, records by path found by digest, the three tables in one read-only transaction, capture names, the loopback check; each fault named -- the terminal, the daemon and the drop share it
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- `transcript list` in `TABLE` (`--scope`, `--state`, `--limit`, `--json`; `composes=False`; target personal); the text and JSON forms; `summarize` gains `--title`, `--on`, `--again`; `drop`'s `--json` without `--dry-run`; `_closing_line` ends with the state and the next step -- the commands
- [ ] `pm_ai/app/pipelines.py` -- the re-summary guard in `request_summary`; `--title` resolved in the daemon; `TranscriptDropped` carries the `ListedMeeting`, read after the drop commits; the "asks again" sentences through the one command function -- 7b and 7c amended
- [ ] `pm_ai/core/rendering.py`, `pm_ai/app/entry.py:1713` -- the dashboard's and `doctor`'s summarize commands through the one command function -- no printed command is refused
- [ ] `pm_ai/app/entry.py`, `pm_ai/app/prompts.py:1071` -- the reader on the `composes=False` path and in `_command`; `("transcript", "list")` in `DAEMON_BOUNDS`; `_meeting_ids` by scope, returning id, start and title -- the terminal, the prompt and completion
- [ ] `pm_ai/surfaces/cli/completion.py`, `editor.py` -- `MEETING_IDS` for `summarize` and `drop`; `Readers.meetings` takes a scope; a `_Cached` variant; the `--state` source; `--limit`, `--title` and `--on` free text; descriptions; matching by id or title word, filtered then capped -- choosing at the prompt
- [ ] Tests: `tests/slice/test_transcript_list.py` (every matrix row, the timed rows, reachability of `transcript list`, the two wordings agree); `tests/surfaces/test_cli_subcommands.py:1501` -- `test_json_without_dry_run_is_a_usage_error_naming_dry_run` replaced by a test that a real drop's `--json` prints one object; `:1149` `_Dropped` gains the listed meeting; `:786-799` gains `transcript list`; `tests/surfaces/test_cli_completion.py:373` gains the new `--meeting` lines; `tests/slice/test_member_updates_slice.py:735-756` -- drop's `--meeting` is no longer free text -- the matrix is the contract
- [ ] `stories/7b-…md`, `stories/7c-…md` -- a dated Change Log line each: a summarize on a meeting holding a summary is refused without `--again` (`11f`) -- earlier frozen blocks amended on the record
- [ ] `stories/7d-…md` -- a dated Change Log line: the dashboard's command adds `--again` for a held summary (`11f`)
- [ ] `stories/11e-…md` -- a dated Change Log line: `--json` on a real drop, `format` in the dry run's object, and the closing line's summary state (`11f`)
- [ ] `ARCHITECTURE-SPINE.md` -- AD-21's edit below, re-derived through the architecture skill

**Acceptance Criteria:**
- Given a fixture dropped in personal and nothing else, when `pm-ai transcript list` runs with no daemon, then one meeting is listed with its title, estimated start, "transcript ingested" and the waiting kind, the next step says to start `pm-ai daemon run` first, and the exit is 0.
- Given the same, when `pm-ai transcript list --json` runs, then standard output parses as one object whose meeting's `next.summarize` equals the words of the summarize command, and it holds no utterance text.
- Given three meetings titled `Example Weekly Sync-up`, when `transcript summarize --scope personal --title weekly` runs, then it is refused with exit 3 and all three are listed, and no model is asked.
- Given a meeting whose record holds a summary, when `transcript summarize` runs without `--again`, then it is refused with exit 3, naming `--again`, and its summary row is unchanged.
- Given a `people:` scope, when listing, then it is refused with exit 3 and nothing is read.
- Given the change, when `uv run pytest` runs, then everything passes, and the bound, completion-source and reachability checks pass without edits to the checks themselves.

## Spec Change Log

- **2026-10-21, created** from Andrei's direction of 2026-10-21, after his first real drop and Claude summary: the CLI is an API layer, so meetings need a listing, a machine form, a way to choose without memorised ids, and the next step offered. The drop's own fields stay `11e`'s.
- **2026-10-22, review (three lenses) and Andrei's decisions: frozen block edited (recorded here).**
  - **Andrei's decisions:**
    - (1) picking a meeting is Tab completion at the prompt plus `--title <words> [--on <date>]`, resolving to exactly one meeting or refused with the candidates; `--meeting` wins; no `--latest`, no row numbers. This replaces Ask First 1. Applying it to `drop` collides with `11e`'s `--title`, and that collision is the new Ask First 1;
    - (2) `--json` on a real drop, its shape pinned. This replaces Ask First 2;
    - (3) the re-summary guard with `--again`, amending `7b` and `7c`; the listing offers the re-run separately (`next.resummarize`).
  - **False claims corrected:**
    - `doctor` and the dashboard do not report transcript states;
    - `operational.db` is one application store;
    - no automatic purge exists yet (NFR-09 is unbuilt), so a capture is called missing, not purged;
    - `fold_breaks` folds only line breaks, so the text form now folds Cc and Cf characters itself.
  - **Kept** is every stored part's capture, `meeting_utterances`'s rule, with all of `_prepare`'s checks cited.
  - **Waiting** is split into three kinds, with `offered_automatically`.
  - **Next steps:** a next step or a reason for every state; the daemon check; the closing line left out when nothing qualifies.
  - **Reading:**
    - one read transaction;
    - an unreadable store lists records with states unknown;
    - unreadable or missing records are listed;
    - a missing `meetings/` is empty;
    - an unreadable named scope is exit 0, an unknown project exit 3;
    - de-duplicated by (scope, id).
  - **The record** is the one source of the start and the order; the zone in the header and in `zone`.
  - **Performance:** reads limited to what is shown; `more` counted from rows; the 1-second claim replaced by timed tests.
  - **The text form:** folding, cutting, `(untitled)`, `errors="replace"`, `ensure_ascii=True`, usage errors for `--scope` and `--limit`.
  - **The JSON contract:** types and null rules; an example per state; unknown values read as "other"; word lists; `next.drop`'s `needs`.
  - **Completion details:** filtered then capped; the `drop` order; the `summarize` check; the `Readers.meetings` and `_Cached` changes; the 4u exception named.
  - **Layering:** `ListedMeeting` and one renderer.
  - **The drop:** the summary row read after the commit.
  - **The prompt:** `_command`'s reader; the byte-for-byte prompt row; a project enrolled after start; the target.
  - **Tests and rows:** the named tests, and the missing matrix rows added.
  - **AD-21:** the exact edit.
  - **Two deviations from the review's wording, each with its reason:**
    - title words are folded with `normalised_title` (NFKC, case-folded), not NFC, so that `--title`, completion and AD-33's one-record rule compare titles the same way;
    - a missing `projects.toml` is no projects, as `_enrolled_projects` reads it, and is not named as unreadable; a malformed one is named.
  - **Found in revision:** every summarize command pm-ai prints (`7d`'s dashboard line, the "asks again" sentences, `doctor`'s probe) must add `--again` when a summary is held, or the guard would refuse the command it printed. Tasks added for those sites and for `7d`'s Change Log.

## Design Notes

**Why `--title` refuses rather than guesses.** A wrapper passes back the `meeting_id` it took from `transcript list --json` and never needs anything else. A person can type words instead of an id. Because an ambiguous match is refused with the candidates listed, the words can never reach a meeting the person did not mean. With `--by claude`, that meeting would cost money. "Latest" and row numbers resolve differently from one moment to the next, so they were rejected.

**Why it reads by path and composes nothing.** The meeting records and `operational.db` are not encrypted, and completion already reads them this way. Composing a daemon would need `config.toml` and the keychain, and a listing should work when those are broken, as `goal list` does (`4t`). The daemon runs the same function at the prompt, so the two outputs cannot drift.

**Why the guard is a flag, not a question.** The daemon never asks (AD-21), and a wrapper cannot answer one. `--again` makes replacing a summary, and paying for it, something said on the command line.

**Spine amendment this depends on** (re-derived through the architecture skill, a task above), AD-21 (`:369`):
- In the heading, the revision tag becomes `[revised 2026-10-09, 2026-10-16, 2026-10-19, 2026-10-22]`.
- `11f` is added at the end of **Binds**.
- In the parenthesised foreground list, after "`member name`, `member link`, `member unmatched` (`7e`)", add: "and `[2026-10-22]` `transcript list` (`11f`), daemon-routed at the prompt in `DAEMON_BOUNDS`, inline".

Completion's read-only reading of `operational.db` stays the exception recorded for `7e` in 4u's readers. It is not a spine change.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_transcript_list.py tests/slice/test_transcript_drop.py tests/slice/test_meeting_summary_slice.py tests/slice/test_frontier_summary.py tests/slice/test_member_updates_slice.py tests/surfaces/test_cli_completion.py tests/surfaces/test_cli_subcommands.py tests/slice/test_line_requests_hardening.py -q` -- expected: every matrix row passes
- `uv run pytest -q` -- expected: all pass; the skip count unchanged
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a scratch home (`HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1`), drop the recorded fixture, then run `pm-ai transcript list` and `pm-ai transcript list --json`, with no daemon and then at the prompt with one. Expected: the same meeting both times. The summarize command from the listing runs as printed. Running it again is refused, naming `--again`. At the prompt, `--meeting week<Tab>` offers the meeting by title.
