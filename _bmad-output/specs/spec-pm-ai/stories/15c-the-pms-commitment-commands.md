---
title: 'the PM''s commitment commands'
type: 'feature'
created: '2026-10-25'
status: 'draft'
baseline_commit: '689122a'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/15a-approved-items-become-commitments.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/13a-proposals-approved-edited-or-rejected.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/13d-compact-cards-in-interactive-review.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Since `15a`, approving an action item or a spoken promise writes a `pending` line to the meeting's scope's `commitments_log.md`. After that, the PM has no command for it:
- **He cannot say it is done.** 15a defined the `marked` line (`fulfilled`, `altered`, `broken`, `withdrawn`), the `due_set` line and the `owner_set` line, and its reader folds them. Nothing writes them: today only a hand edit of the file can.
- **He cannot see his commitments.** No command lists them, and nothing shows which are due soon or past due.
- **An approval made by mistake has no way out.** 15a refuses `proposal reject` once an item is a commitment, and its refusal says that marking one withdrawn "is not available yet".
- **An unresolved due stays unresolved.** "by next sprint" is recorded as `unresolved`, and 15a says the PM can set the date in 15c.

**Approach:** One command group, `pm-ai commitment`, built like 13a's `pm-ai proposal`:
- **See:** `list` and `show`, open commitments by default, soonest due first. A date is marked past due only when it was worked out exactly, never on an estimated meeting day.
- **Mark:** `done`, `altered`, `broken` and `withdrawn` write the PM's own mark under his handle, by 15a's mark table.
- **Correct:** `due` and `owner` set the date and the owner without marking anything.
- **Walk:** `review` goes through the open commitments one compact card at a time.
- **Safe from two places at once:** every change is a compare-and-append on the ledger, and `--if-version` acts only on the version the PM saw.
- **The reject refusal** now names the commands that exist.

## Boundaries & Constraints

**Always:**

### Prerequisites and what is decided already

- **Built and relied on:**
  - `15a`: `Commitment`, `MARKABLE` and `mark_answer` (`domain/commitments.py`); the line kinds, `render_line`, `parse_ledger`, `fold` and `CommitmentLog` (`core/commitment_log.py`); `CommitmentRecorder` (`app/commitments.py`); the store's `read_commitments` and `append_commitments`; `due_dates.resolve`;
  - `13a`: the queue's shape (`list`, `show`, verbs with `--if-version`, `review`, `--json` with `format`, completion, exact next commands, refusal objects), the edit of an approved item, and `ProposalDesk`;
  - `13d`: the compact card in an interactive walk;
  - `11f`: `--title <words> [--on <date>]` choosing a meeting, through `resolve_title` (`app/meeting_listing.py:396`);
  - `4u`: completion readers that touch no master key, keychain or daemon.
- **Decided by 15a, not reopened here** (Andrei, 2026-10-24):
  - the four marks, recorded under his handle and counted apart from telemetry (SM-9); `withdrawn` counted apart from `broken`;
  - the mark table, unchanged: a PM `fulfilled` refuses `altered` and `withdrawn`; `withdrawn` accepts his other marks; a telemetry `FULFILLED` confirmed by the PM writes his own confirming mark;
  - verdicts still apply after `altered`;
  - `due_set` (a date or none, never estimated) and `owner_set` (a name or none), and a promise has no owner unless the PM sets one;
  - reject is refused once an item is a commitment;
  - the due resolver and its forms.
- **The slices of story 15:** 15a (ledger, done), 15b (index), **15c (this spec)**, 15d (surfaces). Story 16 writes verdicts and owns overdue handling. 15c only shows that a date has passed.

### The command group: `pm-ai commitment`

- **Why this shape.** `commitment` is FR-34's and AD-14's word, singular like every group. The verbs that mark are the words the PM says about the commitment: "done", "altered", "broken", "withdrawn". They are the words story 15's entry already names, and each maps to one `marked` state. `due` and `owner` are corrections, not marks. `review` is the walk. 13a's `proposal edit` stays the way to correct what was recorded about an action item.
- **The commands:**
  - `commitment list [--scope …] [--meeting <id> | --title <words> [--on <date>]] [--state open|done|broken|withdrawn|all] [--limit 1..200] [--json]`
  - `commitment show <id> [--scope …] [--json]`
  - `commitment done <id> [--note <text>] [--scope …] [--if-version <version>] [--json]`
  - `commitment altered <id> [--what <text>] [--owner <name>] [--due <words>] [--note <text>] [--scope …] [--if-version <version>] [--json]`
  - `commitment broken <id> [--note <text>] …` and `commitment withdrawn <id> [--note <text>] …`, with the same options as `done`
  - `commitment due <id> (<words> | --clear) [--scope …] [--if-version <version>] [--json]`
  - `commitment owner <id> (<name> | --clear) [--scope …] [--if-version <version>] [--json]`
  - `commitment review [--scope …] [--meeting <id> | --title <words> [--on <date>]]`
- **`<id>` is the commitment id**, the proposal's root id. A replacement id `<root>_e<n>`, as `proposal show` prints it, is accepted and means its root. Without `--scope`, the id is looked up in personal and every enrolled project.
- **Scopes,** as in 13a:
  - `people:` and the application scope are refused (exit 3, `scope_refused`); no `people` ledger exists (AD-4, AD-28);
  - a `project:` that is not enrolled is refused (exit 3, `not_enrolled`), naming the enrolled projects;
  - an id found in more than one ledger, which only a hand copy can make, is refused (exit 3, `ambiguous`), naming the scopes;
  - every command pm-ai prints carries `--scope`.
- **Asks nothing.** Every command except `review` asks no question. `review` is the walk the PM chose to start.
- **Choosing without an id:**
  - at the prompt, `<id>` completes (below);
  - `list` and `review` take `--meeting` or `--title`/`--on`;
  - every card prints its exact commands;
  - the id stays the only way to name one commitment on the command line, so a wrapper acts on the commitment it showed (13a's and `11f`'s reasoning).

### What each change writes (the line kinds 15a defines)

- **`done`, `broken`, `withdrawn`:** one `marked` line with `state=fulfilled`, `broken` or `withdrawn`, `by=pm`, `handle` when `pm_identity()` gives one (the lowest in sort order when it gives several), the `note` when given, `occurred` and `at` the moment of the command, and `via` the surface (`terminal` or `prompt`; story 5 adds `telegram`).
  - Before writing, `mark_answer` is asked of the state and source in force.
    - **`yes`:** the line is written. From a telemetry state, the sentence says the mark confirms or overrides it.
    - **`already`** (the PM's own mark of that state is in force): nothing is written, outcome `already`, exit 0. The sentence names when and from where; a `--note` given is not recorded, and the sentence says so.
    - **`refused`:** exit 3, `mark_refused`, with the reason from the table ("you marked it done on <date>, so it is not altered or withdrawn; mark it broken if it was not done after all").
- **`altered`:** the PM says what was agreed has changed. In this order:
  1. **One append** holding the `marked state=altered` line (with `--note`), plus an `owner_set` line when `--owner` differs from the owner in force, plus a `due_set` line when `--due` is given and differs from the due in force. The lines share one `occurred`.
  2. **Then, when `--what` is given, 13a's edit of the commitment's live approved proposal** (`proposal_id` in the fold) with that wording only, as `proposal edit` does. The recorder writes its `revised` line (15a). The state stays `ALTERED`: a `revised` line never changes the state.
  - **The mark comes first** because it is the PM's decision and the one line that must not be lost. If the edit then fails, the outcome says the mark is recorded and the wording is not, and gives the exact `pm-ai proposal edit <proposal> --scope … --what "…"` to run (exit 3, `edit_refused`, carrying 13a's reason).
  - **`--what` is for an action item only.** A spoken promise's words are what was said, and 13a gives `implicit_commitment` no editable field. `altered --what` on a promise is refused (exit 3, `what_not_editable`): "a spoken promise keeps the words that were said; record how it changed with --note". Nothing is written.
  - `--owner ""` and `--due ""` clear the field, as 13a's edit does. `altered` with no option is valid: the mark alone, or with its note.
- **`due`:** one `due_set` line. `--clear` writes `due=none`.
  - **The words are resolved against today**, the PM's day in `display_timezone` (else the machine's zone), by `due_dates.resolve(words, today)`. The PM is setting the date now, so "Friday" means the coming Friday. `today`'s day is never estimated.
  - **An ISO date (`YYYY-MM-DD`) is taken as typed**, including one already past, because a typed date is explicit. The sentence adds "(already past)".
  - **Words that name no one date** are refused (exit 3, `due_unresolved`), naming the forms that resolve (15a's table) and the ISO form. `due_set` has no `unresolved` value.
  - The same date as the due in force writes nothing: outcome `unchanged`, exit 0.
- **`owner`:** one `owner_set` line. `--clear` writes it with no `owner`. The name is the PM's typed text, folded to one line (`fold_typed`), at most 80 characters (`OWNER_LIMIT`, exit 2 over it). It is a name, never a handle (AD-34), and not linked here. The same owner as the one in force writes nothing: `unchanged`.
- **The note** is the PM's typed text, folded to one line, at most 200 characters (`NOTE_LIMIT`, exit 2 over it). Typed text is trusted as his own and is not passed through the prompt-injection filter (13a).
- **No change writes to `operational.db`,** except `altered --what` through 13a's edit. A commitment's state is never a proposal state (AD-14): `proposal show` of an item marked done still says `approved`.

### Every change is a compare-and-append (AD-37)

- **A commitment's version is the `entry` id of its newest line in file order.** The ledger is append-only, so any line appended for a commitment becomes its newest, and the version changes. A line of another commitment leaves it unchanged. Cards and JSON carry it as `version` (`cml_` plus 12 hex characters).
- **`--if-version <version>`** on `done`, `altered`, `broken`, `withdrawn`, `due` and `owner` acts only if the commitment is still at that version. A wrapper passes the version from the card it displayed, and `review` always does.
- **The writer's steps,** in `CommitmentDesk` (`pm_ai/app/commitments.py`):
  1. read and fold the scope's ledger through `CommitmentLog.read`;
  2. **"already" before the version**, as in 13a: if the state asked for is in force by the PM's own mark, answer `already` (exit 0) whatever the version;
  3. if `--if-version` is given and differs, refuse (exit 3, `changed`), with the current card and one sentence saying what changed ("marked broken on <date>, from the prompt");
  4. decide the lines from the state in force (`mark_answer`) and the values in force;
  5. leave out any line whose `entry` is already in the ledger;
  6. append with `after` set to the size read.
- **On `LedgerChanged`,** read again and go back to step 2. The version check is against the commitment's own version, so a line another commitment gained does not refuse the change. At most `ATTEMPTS` (3) reads, then `busy`. Nothing is appended blindly.
- **Two surfaces at once:** the terminal and the prompt both mark one commitment `done`. One append wins. The other reads again, finds the PM's `fulfilled` in force, and answers `already`, exit 0. One line is written. `done` from one surface and `withdrawn` from the other: one wins, and the other is refused (`mark_refused`, or `changed` with `--if-version`).

### Reading the ledger: what the fold adds

- **The fold gains, per commitment** (`CommitmentLedger.details[root]`, a `Details` value in `core/commitment_log.py`):
  - `version`: the `entry` of its newest line in file order;
  - `mark`: the PM's mark in force (state, `occurred`, `via`, `note`), or none;
  - `due_by_pm` and `owner_by_pm`: whether the due or the owner in force came from his `due_set` or `owner_set`;
  - `changed_at`: the newest `occurred` among its lines.
- **One fix to 15a's fold, for the owner.** Today the owner is the newest of the newest value line and any `owner_set` line (`commitment_log.py:782-783`). A `revised` line always carries the proposal's owner, so a `proposal edit --what` after `commitment owner` brings back the old owner without anyone asking for it.
  - **From 15c, the owner comes from** the newest by `(occurred, entry)` of: the `pending` line, each `revised` line whose owner differs from the value line before it (in 15a's value order), and `owner_set` lines.
  - This mirrors 15a's rule for the due, which a `revised` line carries only when it changed. The line format does not change, so no line written before 15c reads differently, except in this case.
- **Bad lines,** counted by 15a (malformed, orphans, duplicates, conflicts, and marks the table refused), are shown by `list`, one line per scope, as 15a promised. They never fail the command. An id named only by a malformed line is refused by the verbs (exit 3, `malformed`): "a line of commitments_log.md that names it does not parse; fix or remove that line".

### `commitment list`

- **`--state`:**
  - `open`, the default: not closed. That is `pending` or `altered` today. Story 16's `unknown` and `error` are open too, because neither is terminal;
  - `done`: `fulfilled`, by the PM's mark or by telemetry;
  - `broken`, `withdrawn`;
  - `all`.
- **Order:**
  - **open ones by due:** a resolved date first, earliest first; then `unresolved`; then `none`. Ties are broken by the meeting's start, newest first, then the commitment id;
  - **closed ones** (`done`, `broken`, `withdrawn`) by `changed_at`, newest first;
  - **`all`:** the open ones in due order, then the closed ones by `changed_at`.
- **Past due is shown, not acted on.** A card says "past due" after its date only when:
  - the commitment is open;
  - its due has a date;
  - the date is not estimated;
  - and the date is before today in the display zone. A commitment due today is not past due.

  An estimated date is shown with "(meeting day estimated)" and is never called past due. `unresolved` and `none` are never past due. Story 16 owns what happens to a past-due commitment. Nothing here sends, marks or asks.
- **`--limit`** defaults to 20, at most 200, and the number left out is stated.
- **Scopes.** Without `--scope`, personal and every enrolled project. A ledger that cannot be read is named on one line, and the others are still listed. `--meeting` and `--title`/`--on` choose one meeting exactly as 13a's listing does (ambiguity refused across scopes, exit 3).
- **The text form:** one card per commitment, then the bad-line counts per scope when there are any, then the exact next command: `pm-ai commitment review` with the same filters when anything is open, and otherwise the filter that would show more. An empty listing is one sentence naming the filter and the scopes looked at.
- **It reads each ledger once.** The title and start come from the `pending` line, so no meeting record is read. A timed test lists over a 5,000-line personal ledger and a 5,000-line project ledger within 1 second.

### The card (one renderer, `pm_ai/core/commitment_cards.py`)

- **One function turns a `Commitment` and its `Details` into a `Card` value.** It needs the scope, the clock and the display zone, and does no I/O. Availability is decided here alone (AD-13's rule for cards), from `mark_answer`.
- **The full text card:**

  ```
  Commitment, open, due Thu 22 Oct, past due
    Sam Rivera: send the cache TTL numbers ("by Thursday")
    from Example Weekly Sync-up, Mon 19 Oct 10:00, personal; approved Mon 19 Oct
    done:      pm-ai commitment done prp_… --scope personal --if-version cml_…
    altered:   pm-ai commitment altered prp_… --scope personal --note "…" --if-version cml_…
    broken:    pm-ai commitment broken prp_… --scope personal --if-version cml_…
    withdrawn: pm-ai commitment withdrawn prp_… --scope personal --if-version cml_…
    due:       pm-ai commitment due prp_… "…" --scope personal --if-version cml_…
    owner:     pm-ai commitment owner prp_… "…" --scope personal --if-version cml_…
  ```

  - **The heading:** "Commitment", then the state in plain words: "open"; "open, altered on <date>"; "done on <date>"; "broken on <date>"; "withdrawn on <date>"; "open, pm-ai could not check" (`unknown`); "open, a check failed" (`error`). A telemetry state adds "by telemetry". Then the due: "due <date>", "(meeting day estimated)", "set by you", "no due date worked out from \"<words>\"", "no due date said".
  - **The field line:** `<owner>: <what>` for an action item; `said by <speaker>: <what>` for a promise, with `, owner <name>` when he set one; the due as said in quotes. A `what` that 15a cut ends with "…" and "(cut; the full text: pm-ai proposal show <proposal> --scope …)".
  - **The origin:** the meeting's title, start and scope, then "approved <date>". With no title on the line: "meeting record unavailable when it was approved".
  - **The PM's note**, when his mark in force has one: `note: <text>`.
  - **The actions,** with an unavailable one shown as `<name>: unavailable: <reason>`.
- **The compact form** (13d's lesson, Andrei 2026-10-24): the heading, the field line, the origin and the note, with no command lines. Only the `review` walk uses it. `show`, `list`, every verb's output, every refusal and every `--json` object carry the full card.
- **Text from a transcript** has its line breaks folded and is escaped, as `7d` does. Names are shown as recorded. Linking a name to a person is 15d's.

### The JSON (`--json`, `format: 1`)

- **One object on standard output and nothing else.** Exit codes are as in the text form. A field may be added later without changing `format`; removing or renaming one changes it.
- **`list`:** `{"format": 1, "state": string, "scopes": [string], "unreadable": [{"scope", "reason"}], "ledgers": [{"scope", "malformed", "orphans", "duplicates", "conflicts", "refused"}], "more": int, "next": {"review": [words]|null, "more": [words]|null}, "commitments": [card, …]}`.
- **`show` and every verb:** `{"format": 1, "outcome": string, "commitment": card, "wrote": [kind, …], "edited": {"proposal_id": string}|null, "next": [words]|null}`.
  - `outcome` is one of `shown`, `marked`, `already`, `due_set`, `owner_set`, `altered`, `unchanged`;
  - `wrote` lists the line kinds this command appended (`marked`, `due_set`, `owner_set`), empty when it wrote none;
  - `edited` names the replacement `altered --what` made;
  - `next` is the next open commitment of the same meeting (`commitment show …`), else `commitment list --scope …`.
- **A card:**

  ```
  {"commitment_id", "version", "scope", "type", "proposal_id",
   "state", "state_source",               // a CommitmentState value; "none" | "pm" | "telemetry"
   "what", "what_cut", "owner", "owner_by_pm", "speaker",
   "due": {"said", "value", "date", "estimated", "by_pm"},
   "past_due": boolean,
   "day", "day_from",
   "meeting": {"meeting_id", "title", "start"} | null,
   "approved_at", "changed_at",
   "mark": {"state", "at", "via", "note"} | null,
   "actions": {name: {"available", "reason", "command": [words] | null}}}
  ```

  Instants are ISO 8601 UTC. `due.value` is a date, `unresolved` or `none`, as in 15a's `commitment` object. The action names are `done`, `altered`, `broken`, `withdrawn`, `due`, `owner` and `show`.
- **A refusal (exit 3):** `{"format": 1, "outcome": "refused", "code": string, "reason": string, "commitment": card|null}` on standard output. A usage error (exit 2) stays a message on standard error, as for every command.
- **The refusal codes:**
  - `not_found`: no commitment with that id in the scopes looked at. When a proposal has that id but is not a commitment (waiting, rejected, or a type that records none), the reason names `pm-ai proposal show <id> --scope …`;
  - `ambiguous`, `scope_refused`, `not_enrolled`;
  - `ledger_unreadable`: the ledger could not be read;
  - `malformed`: only a malformed line names the id;
  - `mark_refused`: the mark table refuses it from the state in force;
  - `changed`: `--if-version` no longer matches;
  - `due_unresolved`: the words name no one date;
  - `what_not_editable`: `altered --what` on a spoken promise;
  - `edit_refused`: `altered --what`'s edit was refused (13a's reason), after the mark was written;
  - `busy`, `git_not_excluded`, `disk`: 15a's meanings, for the append;
  - `no_terminal`: `review` without a terminal.
- **Exit codes:**
  - **0:** changed, `already`, `unchanged`, shown, or listed, an empty listing included;
  - **2:** usage: an unknown `--state`, a limit outside 1 to 200, `due` or `owner` with both a value and `--clear` or with neither, an empty `--what`, a value over its limit;
  - **3:** every refusal above.

### `commitment review`

- **Walks the open commitments in `list`'s order,** with `list`'s filters, one compact card at a time.
- **The keys:** `[d]one [a]ltered [b]roken [w]ithdrawn d[u]e [o]wner [s]kip [q]uit`. A key whose action is unavailable is refused with its reason, and the card is asked again.
  - `d` writes the mark at once.
  - `b` and `w` ask "note (Enter for none)", then write.
  - `a` asks, with the current value shown: the wording (an action item only), the owner and the due (Enter keeps, `-` clears), then the note. Then it writes, as `altered` does.
  - `u` asks for the due (words or `YYYY-MM-DD`, `-` clears). Words that name no date are asked again with the reason.
  - `o` asks for the owner (`-` clears).
- **Each answer is one change,** checked against the version its card showed. On a conflict, the current card is shown again with one sentence saying what changed.
- **The end:** one line counts what was marked done, altered, broken or withdrawn, the dues and owners set, and what was skipped, then the next step. Quitting, Ctrl-C and end of input end the walk the same way: the counts so far, exit 0. Every answered change is complete.
- **Without a terminal** (stdin not a TTY), it is refused (exit 3, `no_terminal`), naming `commitment list --json` and the verbs.

### Where it runs (AD-21)

- **At a terminal:**
  - `list` and `show` compose nothing (`composes=False`, as `proposal list`). They read each ledger by path, through the same `parse_ledger` and `fold`, and ask for no master key or keychain. `commitments_log.md` is plaintext (AD-6);
  - the verbs are composed as `proposal approve` is: the store, its claimed step with a 1-second busy wait, the git check for a project ledger (AD-43), and the `connectors/` rows for the PM's handle, which are not encrypted. No master key.
- **At the prompt:**
  - `list` and `show` join `DAEMON_BOUNDS` (`entry.py:606`) with `CONFIG_SHOW_BOUND_SECONDS`, 1 second;
  - `done`, `altered`, `broken`, `withdrawn`, `due` and `owner` join it with `COMMITMENT_WRITE_BOUND_SECONDS = 2.0`, declared in `pm_ai/app/commitments.py`. `altered --what` includes 13a's edit within the same 2 seconds;
  - inline through the daemon, printing exactly what the terminal prints. The daemon's desk hands its appends to the writer thread, as `prompts.py:1146` does for proposals.
- **`commitment review`** joins `TERMINAL_BOUND` (`dispatch.py:4963`), so at the prompt it runs in the prompt's own process.
- **Each command's help names its bound** where it has one.
- **Timed tests:** a mark on a 5,000-line project ledger, with the git check and one `LedgerChanged`, within 2 seconds; `altered --what` on the same, within 2 seconds.

### Completion (4u's rules)

- **`<id>`** completes from the commitments the verb can act on:
  - for `done`, `altered`, `broken` and `withdrawn`, those whose `mark_answer` is `yes`;
  - for `due` and `owner`, the open ones;
  - for `show`, any.

  At most 100 are offered, in `list`'s order. Each inserts the id and shows `owner: what` (or `said by …`), the meeting's title and the due. Typed text matches the start of the id or of any word of `what`, the owner, the speaker or the title, ignoring case, as `13a` does (`completion.py:566`).
- **`owner`'s name** completes from the owners and speakers already in that scope's ledger, the commitment's own speaker first.
- **`--state`** completes from the five words. **`--meeting`** completes from meeting ids, as `proposal list` does.
- **The reader** reads the ledgers by path with `parse_ledger` and `fold`: no `StorageService`, no master key, keychain or daemon, and nothing offered on failure. So there is still one parser of the file (`derivation-services.md` rule 3).

### The reject refusal names the commands (amends 15a's refusal)

- **`proposal reject` on an approved item that is a commitment** (`proposals.py:822`, `_not_rejected`) still refuses, exit 3, with the row and the ledger unchanged. Its text becomes: `<id> was approved on <date> and is a commitment now, so it is not rejected; nothing was changed. If approving it was a mistake: pm-ai commitment withdrawn <root> --scope <scope>. If what was agreed has changed: pm-ai commitment altered <root> --scope <scope> --note "…". To correct what was recorded: pm-ai proposal edit <id> --scope <scope> --what "…".`
- **In JSON,** 13a's refusal object gains `"commitment": {"commitment_id": <root>, "scope": <scope>, "show": [words]}`. A wrapper runs `show` for the card and its actions. `format` stays 1.
- **The approved card's `reject` reason** (`COMMITMENT_NOT_REJECTED`, `proposal_cards.py:59`) becomes: "approved, so it is a commitment now; mark it withdrawn if approving it was a mistake, or edit it to correct what was agreed".

### What the dashboard shows (Ask First 1 decides; the recommendation is written here)

- **15d owns the surfaces.** 15c changes the pages only so that a commitment the PM closed stops looking open:
  - **the dashboard's meeting digest** (`pipelines.py:2674`, `_meeting_digest`) leaves out an approved action item whose commitment is `fulfilled`, `broken` or `withdrawn`, as it leaves out a rejected one. `_digests_over` (`:2774`) reads each scope's ledger once per render through `CommitmentLog.read`. A ledger that cannot be read shows the items as today. Showing a closed item as approved is harmless, so this fails open;
  - **Team Activity** (`_item_decisions`, `pipelines.py:3111`) hides a `withdrawn` item, as it hides a rejected one (`ItemDecision(hidden=True)`, `:3145`). A done or broken item stays, because a line there is a note of what the meeting said.
- **Unchanged until 15d:** the owner and due the pages show (the proposal's), the morning brief beyond what the digest gives it, the cards' lack of a commitment state, and the overflow line naming `proposal list --state open`.

### The readers of the ledger after 15c

- `CommitmentLog.read`: 15a's recorder and catch-up; 15c's `CommitmentDesk` (`list`, `show`, every verb, `review`); the dashboard digest and Team Activity (as above);
- `parse_ledger` and `fold` over bytes read by path: `list` and `show` at a terminal, and completion;
- 15b's indexer, when it exists. 15b's trigger runs after every append through the store, so 15c's lines are indexed too. That is a requirement on 15b's spec, not work here.

### The architecture tests and the skip count

- **`EXPECTED_SKIPS` stays 6** (`tests/conftest.py:192`). No skipping test depends on 15c: `test_ad35_sweeper_will_not_declare_broken_without_coverage` and `test_ad36_self_authored_events_are_excluded_from_evidence` still skip on `pm_ai.core.commitments`, which is story 16's. 15c creates no module of that name.
- **New, passing in 15c** (`tests/architecture/test_domain_invariants.py`):
  - `test_ad37_two_surfaces_marking_one_commitment_record_one_mark`: two `CommitmentDesk`s over one store mark one commitment `done`; one `marked` line, and the second answers `already`;
  - `test_ad37_a_stale_version_never_appends`: `--if-version` with the version before another mark refuses with `changed`, and the ledger's size is unchanged;
  - `test_ad14_only_three_commitment_states_are_terminal`: `CommitmentState.is_terminal` is exactly `FULFILLED`, `BROKEN` and `WITHDRAWN`, as the AD-14 amendment says.
- **Unchanged and still passing:** `test_ad14_proposal_and_commitment_lifecycles_stay_distinct` (`:283`), both AD-28 tests (`:165`, `:209`), and `test_ad33_ledger_entries_are_self_contained` (`:735`).
- **Command tables:** `test_every_command_the_daemon_runs_declares_its_bound` (`tests/slice/test_line_requests_hardening.py:95`) covers the new entries in `DAEMON_BOUNDS` and `TERMINAL_BOUND`, and the completion test over `TABLE` covers every new name's source.
- **`.importlinter` is unchanged:** `core/commitment_cards.py` imports `domain` and `core`, `app/commitments.py` is in `app`, and the CLI reaches the desk through a port protocol in `surfaces/cli/dispatch.py`, as `ProposalDeskPort` (`:4568`) does.

### No real data

Tests use `11d`'s fixture names (`Example Weekly Sync-up`, `Alex Morgan`, `Sam Rivera`) and `example.com` handles. Nothing reads the PM's own `~/.pm-ai` or `~/.manager-ai`.

**Ask First:**

1. **What the pages show in 15c.** 15a gave the surfaces to 15d, but a commitment the PM marks done keeps showing as "approved" on the dashboard until 15d lands.
   - **A (recommended):** the dashboard digest leaves out done, broken and withdrawn items, and Team Activity hides withdrawn ones, as each hides a rejected item. Nothing else on the pages changes until 15d. This is what the spec above says.
   - **B:** A, and approved items on the dashboard also show the commitment's owner, due and state now. That moves most of 15d's dashboard work into 15c.
   - **C:** nothing changes on the pages until 15d. A done item keeps showing as approved, and a withdrawn one stays on Team Activity.

**Never:**

- **The ledger:**
  - no line rewritten or removed, and no whole-file write;
  - no append without the size read checked in the claimed step, and no blind retry;
  - no mark the table refuses, and no second line for a mark already in force;
  - no line of a kind 15a does not define, and no `verdict` line;
  - no `unresolved` or estimated `due_set`.
- **People:**
  - no handle written as an owner, and no name guessed, fuzzy-matched or linked;
  - no owner taken from a speaker unless the PM sets it.
- **Proposals:**
  - no proposal state changed by a mark (AD-14);
  - no write to `operational.db` except `altered --what` through 13a's edit;
  - no `approved → rejected`.
- **Overdue:**
  - no past-due mark on an estimated, `unresolved` or `none` due, or on a closed commitment;
  - no message, inquiry, verdict or state change because a date passed (story 16).
- **Surfaces:**
  - no question asked by any command but `review`;
  - no compact card outside `review`;
  - no command, refusal or JSON naming a command that does not exist;
  - no master key, keychain or model call;
  - no `people` scope read, listed or offered.
- **Scope of this slice:** no index (15b), no page change beyond Ask First 1's answer (15d), no telemetry (16), and no module named `pm_ai.core.commitments`.
- **No read of the PM's real store** in a test or a manual check.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| List open | two personal commitments due 22 Oct and 30 Oct, one `unresolved`, one `none`; today 25 Oct | four cards in that order; the first says "due Thu 22 Oct, past due"; `next.review` set | exit 0 |
| Estimated date | open, due 22 Oct, `day_from=header`; today 25 Oct | "due Thu 22 Oct (meeting day estimated)"; `past_due: false` | exit 0 |
| Due today | due 25 Oct, today 25 Oct | not past due | exit 0 |
| List closed | `--state done`, two marked done on different days | newest change first | exit 0 |
| Empty | `--state broken`, none | one sentence naming the filter and the scopes | exit 0 |
| Bad lines | a ledger with one malformed line and one refused mark | the cards, then "commitments_log.md in personal: 1 line could not be read, 1 mark was not applied"; `ledgers[0].malformed: 1, refused: 1` | exit 0 |
| Unreadable project | one project's ledger cannot be read | named on one line; personal still listed; `unreadable` set | exit 0 |
| Show | `show <root>` | the full card with every action's command, `--scope` and `--if-version` | exit 0 |
| Replacement id | `show <root>_e1` | the root's card | exit 0 |
| Waiting proposal | `done <id>` of a waiting item | `not_found`, naming `pm-ai proposal show <id> --scope …` | exit 3 |
| Done | open action item, `done <id>` | one `marked state=fulfilled by=pm via=terminal` line, with `handle` when the PM has one; "<id> marked done." and the next step | exit 0 |
| No handle | no Graph connector row | the line has no `handle` | exit 0 |
| Done twice | `done` again | `already`; nothing written | exit 0 |
| Done twice with a note | `done --note "x"` when already done | `already`; "the note was not recorded" | exit 0 |
| Altered after done | marked done, then `altered` | `mark_refused`, with the table's reason; nothing written | exit 3 |
| Withdrawn after done | marked done, then `withdrawn` | `mark_refused` | exit 3 |
| Broken after done | marked done, then `broken` | written; state `BROKEN` | exit 0 |
| Done after withdrawn | marked withdrawn, then `done` | written; state `FULFILLED` | exit 0 |
| Confirm telemetry | a hand-written `verdict state=fulfilled`, then `done` | the PM's own `marked` line; "confirms telemetry's verdict" | exit 0 |
| Altered alone | `altered --note "scope cut to the API only"` | one `marked state=altered` line with the note; state `ALTERED` | exit 0 |
| Altered with due and owner | `altered --due friday --owner "Alex Morgan"` on 25 Oct (a Sunday) | one append: `marked altered`, `due_set due=2026-10-30`, `owner_set owner="Alex Morgan"`, one `occurred` | exit 0 |
| Altered with wording | action item, `altered --what "send the numbers for the API only"` | the mark appended first; then 13a's edit makes `<root>_e1` approved and the recorder writes a `revised` line; `edited.proposal_id` set; state stays `ALTERED` | exit 0 |
| Altered wording, edit fails | the edit is refused after the mark | mark kept; `edit_refused` with 13a's reason and the exact `proposal edit` command | exit 3 |
| Altered wording on a promise | `altered --what …` on a spoken promise | `what_not_editable`; nothing written | exit 3 |
| Altered again | `altered` twice | two `marked state=altered` lines | exit 0 |
| Due words | `due <id> friday` on Sun 25 Oct | `due_set due=2026-10-30`; the card says "set by you"; never estimated | exit 0 |
| Due ISO in the past | `due <id> 2026-10-20` on 25 Oct | written; "(already past)" | exit 0 |
| Due unresolved | `due <id> "next sprint"` | `due_unresolved`, naming the forms; nothing written | exit 3 |
| Due cleared | `due <id> --clear` | `due_set due=none` | exit 0 |
| Due the same | the date already in force | `unchanged`; nothing written | exit 0 |
| Due both | `due <id> friday --clear` | usage error | exit 2 |
| Owner of a promise | `owner <id> "Sam Rivera"` on a promise with no owner | `owner_set owner="Sam Rivera"`; the card reads "said by …, owner Sam Rivera" | exit 0 |
| Owner over its limit | 81 characters | usage error | exit 2 |
| Owner kept after an edit | `owner <id> "Alex Morgan"`, then `proposal edit <id> --what "…"` (owner unchanged in the proposal) | owner stays Alex Morgan (the fold fix) | exit 0 |
| Owner changed by an edit | then `proposal edit <id>_e1 --owner "Sam Rivera"` | owner becomes Sam Rivera | exit 0 |
| Stale version | `done --if-version <old>` after another mark | `changed`, with the current card and what changed; ledger size unchanged | exit 3 |
| Already beats the version | `done --if-version <old>` when the PM's done is in force | `already` | exit 0 |
| Other commitment moved | another commitment gains a line between read and append | `LedgerChanged`; read again; its own version unchanged, so written | exit 0 |
| Two surfaces | terminal and prompt `done` at once | one `marked` line; one `marked`, one `already` | both exit 0 |
| Busy | the claim is held past its wait three times | `busy` | exit 3 |
| Project git refuses | the project no longer excludes `memory/` | `git_not_excluded`; nothing written | exit 3 |
| People scope | `--scope people:example` | `scope_refused` | exit 3 |
| Not enrolled | `--scope project:gone` | `not_enrolled`, naming the enrolled projects | exit 3 |
| Malformed only | the id appears only in a malformed line | `malformed`, saying to fix or remove that line | exit 3 |
| Review | `review` at a terminal, three open | compact cards (no command lines, no `--if-version`, no id); `d`, `u` with "friday", `s`; "1 marked done, 1 due set, 1 skipped." and `next:` lines | exit 0 |
| Review unresolved due | `u` then "next sprint" | asked again with the reason | continues |
| Review conflict | the commitment gains a mark from the prompt while its card is open | the sentence, then the current card again | continues |
| Review without a terminal | stdin not a TTY | `no_terminal`, naming `commitment list --json` and the verbs | exit 3 |
| Prompt bounds | `commitment list`, `commitment done` typed at the prompt | inline through the daemon, within 1 s and 2 s | exit 0 |
| Prompt review | `commitment review` typed at the prompt | runs in the prompt's process | as at a terminal |
| Completion | `commitment done <Tab>` with one done and two open | the two open ones offered, with `owner: what`, title and due | N/A |
| Completion owner | `commitment owner <id> <Tab>` on a promise said by Sam Rivera | "Sam Rivera" first, then other names in the ledger | N/A |
| Reject refusal | `proposal reject <id>` of an approved action item | exit 3; the text names `commitment withdrawn`, `commitment altered` and `proposal edit`; JSON `commitment.show` words | exit 3 |
| Approved card | `proposal show <id> --json` | `actions.reject.available: false`, the new reason | exit 0 |
| Dashboard, done item (Ask First 1, A) | an approved action item marked done | not shown under its meeting; a waiting one still shown | N/A |
| Dashboard, ledger unreadable | the ledger cannot be read | items shown as today | N/A |
| Team Activity, withdrawn (A) | a withdrawn action item linked to an issue | hidden, as a rejected one | N/A |
| Timed listing | 5,000-line personal and project ledgers | `list` within 1 s | N/A |
| Timed mark | 5,000-line project ledger, git check, one `LedgerChanged` | within 2 s | N/A |

</frozen-after-approval>

## Code Map

Anchors checked against `origin/main` at `689122a`.

- `pm_ai/domain/commitments.py:75` `LineKind`; `:100` `Mark`; `:113` `MarkAnswer`; `:138` `MARKABLE`; `:162` `mark_answer`; `:170` `can_mark`; `:176` `Due`; `:227` `Commitment`
- `pm_ai/domain/lifecycle.py:49` `CommitmentState`, `:66` `is_terminal` (`FULFILLED`, `BROKEN`, `WITHDRAWN`)
- `pm_ai/core/commitment_log.py:94` `NOTE_LIMIT`; `:174` `Marked`; `:187` `DueSet`; `:199` `OwnerSet`; `:239` `render_line`; `:568` `CommitmentLedger` (gains `details`); `:682` `parse_ledger`; `:746` `fold`, `:782-783` the owner lines (the fix); `:858` `CommitmentLog`; `:894` `REASONS`
- `pm_ai/core/due_dates.py:295` `resolve`
- `pm_ai/core/proposals.py:286` `ACTION_ITEM_TYPE` (`what`, `owner`, `due` editable); `:299` `IMPLICIT_COMMITMENT_TYPE` (no editable field)
- `pm_ai/core/proposal_cards.py:59` `COMMITMENT_NOT_REJECTED`
- `pm_ai/app/commitments.py:82` `ATTEMPTS`; `:102` `CommitmentRecorder`; `:134` `record`; `:236` `recorder_for` (the desk is built beside it)
- `pm_ai/app/proposals.py:102` `PROPOSAL_WRITE_BOUND_SECONDS`; `:574` `choices`; `:822` `_not_rejected`, `:836-840` its text; `:954` `edit`
- `pm_ai/app/meeting_listing.py:396` `resolve_title`
- `pm_ai/app/entry.py:606` `DAEMON_BOUNDS`; `:1874` `_proposal_choices` (the commitment reader goes beside it)
- `pm_ai/app/prompts.py:1146` `_proposals`, the daemon's desk with handed writes
- `pm_ai/app/wiring.py:3530` `read_pm_identity`; `:4151` `append_commitments` in `HANDED_CALLS`; `:4204` `read_commitments` in `PLAIN_READS`
- `pm_ai/app/pipelines.py:2674` `_meeting_digest`; `:2774` `_digests_over`; `:3111` `_item_decisions`, `:3145` a hidden item
- `pm_ai/core/member_updates.py:1000` `ItemDecision`
- `pm_ai/surfaces/cli/dispatch.py:4565` `REVIEW_KEYS`; `:4568` `ProposalDeskPort`; `:4833` `_proposal_review`; `:4888` `_review_one`; `:4963` `TERMINAL_BOUND`; `:5001` `TABLE`, `:5204` its `proposal` group
- `pm_ai/surfaces/cli/completion.py:139` `PROPOSAL_IDS`; `:155` `_BY_COMMAND`; `:566` `proposals_for`
- `tests/architecture/test_domain_invariants.py:165`, `:209` AD-28; `:283` AD-14; `:735` AD-33; `:899` AD-35; `:913` AD-36; `:944` AD-37
- `tests/conftest.py:192` `EXPECTED_SKIPS = 6`
- `tests/slice/test_commitments.py:625`, `:637`, `:662` the reject refusal, its JSON and the card's reason (15a's assertions, which change); `tests/slice/test_proposals.py:432`
- `tests/slice/test_line_requests_hardening.py:95` every command declares its bound
- `ARCHITECTURE-SPINE.md:313-319` AD-14; `:371-379` AD-21; `:570-574` AD-37
- `prd.md:673-678` FR-34's consequences
- `derivation-services.md:125` the `CommitmentLog` row

## Tasks & Acceptance

**Execution:**
- [ ] **The fold.** `core/commitment_log.py`: `Details` and `CommitmentLedger.details` (version, mark in force, `due_by_pm`, `owner_by_pm`, `changed_at`); the owner rule fixed. Tests first, including the "owner kept after an edit" row.
- [ ] **The card.** `pm_ai/core/commitment_cards.py`: `Card`, `build_card`, `card_text(card, *, compact=False)`, `card_json`, `listing_text`, `listing_json`, `outcome_text`, `refusal_text`.
- [ ] **The desk.** `pm_ai/app/commitments.py`: `CommitmentDesk` with `listing`, `show`, `mark`, `altered`, `set_due`, `set_owner`, `open_walk` and `choices`, the compare-and-append loop, `COMMITMENT_WRITE_BOUND_SECONDS`, the PM's handle through `read_pm_identity`; built in the composition root and, in the daemon, with handed writes.
- [ ] **The CLI.** `surfaces/cli/dispatch.py`: the `commitment` group in `TABLE`, `CommitmentDeskPort`, the handlers, and the `review` walk with compact cards; `review` in `TERMINAL_BOUND`.
- [ ] **Where it runs.** `entry.py`: `DAEMON_BOUNDS` entries; the by-path readers for `list`, `show` and completion.
- [ ] **Completion.** `completion.py`: `COMMITMENT_IDS`, `COMMITMENT_STATE_NAMES`, `COMMITMENT_OWNERS`, and their `_BY_COMMAND` rows.
- [ ] **The reject refusal.** `app/proposals.py:_not_rejected`, its JSON `commitment` field, and `COMMITMENT_NOT_REJECTED`.
- [ ] **The pages** (as Ask First 1 is answered): `_meeting_digest`, `_digests_over`, `_item_decisions`.
- [ ] **Tests.**
  - `tests/core/test_commitment_log.py`: `details`, the owner rule, the version.
  - `tests/core/test_commitment_cards.py` (new): every heading word, past-due rules, the compact form's lines are the full form's first lines, availability from `mark_answer`.
  - `tests/slice/test_commitment_commands.py` (new): one test per matrix row; the two timed tests; the prompt's routing; completion.
  - `tests/architecture/test_domain_invariants.py`: the three new tests.
  - `tests/slice/test_commitments.py:625`, `:637`, `:662` and `tests/slice/test_proposals.py:432`: the new refusal text, its JSON and the card's reason.
- [ ] **The contract.** Apply the amendments in Design Notes, in this order: `prd.md` through bmad-prd; then bmad-spec re-derives `derivation-services.md`; then `ARCHITECTURE-SPINE.md` through the architecture skill.

**Acceptance Criteria:**
- Given three open personal fixture commitments, when `pm-ai commitment list` runs, then they are listed soonest due first, and only an exact past date is called past due.
- Given an open commitment, when `pm-ai commitment done <id>` runs from the terminal and the prompt at once, then the ledger gains exactly one `marked state=fulfilled by=pm` line and both exit 0.
- Given a commitment the PM marked done, when `commitment altered <id>` runs, then it is refused with exit 3 and `mark_refused`, and the ledger is unchanged.
- Given an open commitment due "next sprint", when `commitment due <id> friday` runs, then a `due_set` line holds the coming Friday's date and the card says "set by you".
- Given `--if-version` from a card shown before another mark, when a verb runs with it, then it is refused with `changed` and nothing is appended.
- Given an approved action item, when `proposal reject` runs on it, then the refusal names `pm-ai commitment withdrawn` and `pm-ai commitment altered`, both of which exist.
- Given the change, when `uv run pytest` runs, then everything passes with 6 skips.

## Spec Change Log

- **2026-10-25, created** after a readiness check (Design Notes), from story 15's entry, 15a's decisions for 15c, and Andrei's standing rules (the CLI is an API layer; ask nothing in the normal path; compact cards in interactive walks; accuracy matched to how critical a field is; plain language). One Ask First question open.

## Design Notes

**Readiness check (2026-10-25, against `origin/main` at `689122a`).** Verdict: **ready; nothing blocks the spec.** One defect in 15a's fold must be fixed before `owner` ships, and it is 15c's first task.
- **Built as 15c needs:**
  - `domain/commitments.py` has `Mark`, `MarkAnswer`, `MARKABLE` (split by source, as decided) and `mark_answer`, which tells "already" from "refused" for the outcomes;
  - `core/commitment_log.py` renders and parses `marked` (state, `by=pm`, `handle`, `note`), `due_set` (a date or `none`; `unresolved` is refused by the parser) and `owner_set` (no owner clears it), with identities that include `occurred`, so each mark is its own line;
  - the fold applies marks through `can_mark` and verdicts after `altered`;
  - `CommitmentLog.append` takes only rendered lines, and the store compares the size in its claimed step, so a decision made on a read is valid exactly when the append succeeds;
  - `REASONS` (`busy`, `git_not_excluded`, `disk`, `ledger_unreadable`, …) and `ATTEMPTS` serve the writes;
  - `append_commitments` is a handed call, and `read_commitments` a plain read, so the daemon's desk works as the recorder does;
  - `connectors/` rows are not encrypted, so the PM's handle needs no master key.
- **Missing, and added by 15c (none blocks):**
  - **a per-commitment version.** `CommitmentLedger` has the set of entries and the file size, but nothing per commitment, so `--if-version` has nothing to compare. 15c adds `details[root].version`;
  - **the mark in force, and where the due and owner came from.** The fold keeps no note, mark time or "set by the PM". 15c adds them to `details`;
  - **the owner defect.** `fold` takes the owner from the newest of the newest value line and `owner_set` (`commitment_log.py:782-783`). A `revised` line always carries the proposal's owner, so an action item's `proposal edit --what` after `commitment owner` silently restores the old owner. 15c fixes the reader, as specified. No line format changes;
  - **`altered --what` for a promise.** 13a registers no editable field for `implicit_commitment` (`core/proposals.py:299`), so a promise's wording cannot change through `proposal edit`. 15c refuses `--what` on a promise and keeps the note for how it changed. That fits AD-29 and AD-33: a promise's words are what was said;
  - **the reject refusal** (`proposals.py:836-840`) and the card's reason (`proposal_cards.py:59`) still say marking "is not available yet". 15c changes both.
- **The spine and the PRD:**
  - **AD-14 contradicts itself and the code:** "Only `FULFILLED` and `BROKEN` are terminal" (`:319`), beside its own sentence that `WITHDRAWN` is terminal and beside `is_terminal`. Amended below;
  - **AD-21** lists no commitment command. Amended;
  - **AD-37** gives a commitment no version a surface can display. Amended;
  - **AD-36** is consistent: the PM's marks are not evidence, are under his handle, and are counted apart;
  - **FR-34** (as amended 2026-10-24) is consistent: `[ALTERED]` is "updated … by the PM's own mark", `[WITHDRAWN]` is terminal and never broken, and a due with no date is never overdue. FR-34 names no commands, so a consequence line is added, following FR-11's precedent for `pm-ai goal`.
- **Not built, and not needed:** 15b's index. 15c reads the Markdown through the one accessor, and a ledger is small. 15b's spec must run its trigger after every append through the store, so 15c's lines are indexed too.

**Why the mark verbs are state words, not one `mark` verb.** Each card action is one command a wrapper can run, as 13a's buttons are, and the words are the ones the PM uses about a promise. `mark <id> <state>` would need a value completion for a choice the card already makes.

**Why `due` and `owner` mark nothing.** Filling in a date that was never worked out, or naming who owns a promise, records what was already agreed. "Altered" means the agreement changed (FR-34). `altered --due` is the renegotiation.

**Why the due is resolved against today.** The PM types the date on the day he sets it. Resolving "Friday" against a meeting weeks ago would name a past date, which 15a's resolver refuses anyway.

**Why the version is the newest entry id.** An append-only file has no row version. The newest line's entry changes whenever the commitment gains a line, and never when another commitment does, so two people working on different commitments never refuse each other. A count of lines would do the same, but would repeat after a hand edit that removed a line.

**Why the mark is written before the wording.** Two files take part, and no transaction spans both. The mark is the PM's decision. The wording is a correction that `proposal edit` can always redo. Writing the mark first means a failure leaves the decision recorded, with an exact command for the rest.

**Accepted limits:**
- Until 15d, the pages show the proposal's owner and due, not the PM's `owner_set` or `due_set`.
- A spoken promise's wording cannot be changed, only noted.
- A typed past date is accepted as typed.

**Amendments this depends on.** Applied with the build, in this order: the PRD through bmad-prd, then bmad-spec re-derives the SPEC companion, then the spine through the architecture skill. The sentences are exact.

*PRD (`prd.md`), through bmad-prd (v0.16.1 → v0.16.2):*
- **FR-34 consequences** (`:675`): after "> * Approved spoken commitment creates a valid, structured Markdown entry in the meeting's scope's commitments\_log.md and an index row in commitment\_index.db (Tier 3, disposable) with state \[PENDING\].", add: "> * The PM keeps his commitments from the CLI (2026-10-25, 15c): `pm-ai commitment list` (open ones by default, soonest due first; a date is shown as past only when it was worked out exactly, never on an estimated meeting day), `commitment show <id>`, `commitment done|altered|broken|withdrawn <id>` (his own marks), `commitment due <id> <when>` and `commitment owner <id> <name>`, and `commitment review`, which walks the open ones; each change is a line appended to the meeting's scope's commitments\_log.md under his handle, never a rewrite, and acts only on the version he saw when he names one."
- **Decisions Log:** "*The PM's commitment commands (2026-10-25, Andrei; FR-34):* `pm-ai commitment` lists, shows, marks (done, altered, broken, withdrawn) and corrects (due, owner) commitments, and walks the open ones; every change is an appended line checked against the version the PM saw; a past date is shown only when it was worked out exactly; the reject refusal names these commands."

*SPEC companions, re-derived by bmad-spec:*
- **`derivation-services.md:125`:** the `CommitmentLog` row's users become "the approval and its catch-up (append, `15a`), the PM's marks, the due and owner he sets (append, `15c`), `commitment list`, `show` and `review` and the dashboard digest (read, `15c`), the lifecycle job (append verdicts, story 16), indexing (read); completion and the terminal's `list` and `show` read the file by path with the same parser (`15c`)".
- `SPEC.md` itself is unchanged: CAP-34's intent and success already cover a PM-kept ledger, and no capability is added.

*Spine (`ARCHITECTURE-SPINE.md`), through the architecture skill:*
- **AD-14** (`:313-319`):
  - in the heading, `[revised 2026-10-22]` becomes `[revised 2026-10-22, 2026-10-25]`;
  - **Binds:** "FR-33, FR-34" becomes "FR-33, FR-34, `15a`, `15c`";
  - replace "Only `FULFILLED` and `BROKEN` are terminal." with "Only `FULFILLED`, `BROKEN` and `WITHDRAWN` are terminal, and only the PM's own mark moves a commitment out of one `[2026-10-25, 15c]`."
- **AD-21** (`:371-379`):
  - in the heading, add `2026-10-25` to the revision list;
  - **Binds:** add `15c`;
  - after "and `proposal review` (`13a`), in `TERMINAL_BOUND`", add ", and `[2026-10-25, 15c]` `commitment list` and `commitment show` (`15c`), daemon-routed at the prompt in `DAEMON_BOUNDS`, inline, under 1 second, `commitment done`, `commitment altered`, `commitment broken`, `commitment withdrawn`, `commitment due` and `commitment owner` (`15c`), the same, under 2 seconds, and `commitment review` (`15c`), in `TERMINAL_BOUND`".
- **AD-37** (`:570-574`):
  - in the heading, add `2026-10-25` to the revision list;
  - **Binds:** add `15c`;
  - after "a ledger that grew is read again, never appended to blindly.", add: "`[2026-10-25, 15c]` A commitment's version is the entry id of its newest line in the file; a surface that displays a commitment carries that version, and a change it sends acts only while the commitment is still at it, while a line another commitment gains never refuses it; a change that finds the PM's same mark already in force is reported as done, not as a conflict."

## Verification

**Commands:**
- `uv run pytest tests/core/test_commitment_log.py tests/core/test_commitment_cards.py tests/slice/test_commitment_commands.py tests/slice/test_commitments.py tests/slice/test_proposals.py tests/slice/test_line_requests_hardening.py tests/surfaces/test_cli_completion.py tests/architecture -q` -- expected: every matrix row passes; the three new architecture tests pass
- `uv run pytest -q` (in the background, about 7 minutes) -- expected: all pass; 6 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a scratch home (`HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1`), drop and summarise the recorded fixture into personal and approve two items. Then `pm-ai commitment list`, `commitment done <first>`, `commitment due <second> friday`, and `commitment list --state all --json`. Expected: the first is done, the second shows the Friday "set by you", and the scratch `~/.manager-ai/memory/commitments_log.md` gains one `marked` and one `due_set` line.
- In the same home, run `pm-ai commitment review` at a terminal. Expected: compact cards with no command lines, and the closing counts.
