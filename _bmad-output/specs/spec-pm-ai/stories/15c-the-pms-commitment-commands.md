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
- **Undo a done mark:** `undo` reopens the PM's own done mark within 4 hours, while nothing has followed it (Andrei, 2026-10-26).
- **Correct:** `due` and `owner` set the date and the owner of an open commitment without marking anything.
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

- **Why this shape.** `commitment` is FR-34's and AD-14's word, singular like every group. The verbs that mark are the words the PM says about the commitment: "done", "altered", "broken", "withdrawn". They are the words story 15's entry already names, and each maps to one `marked` state. `due` and `owner` are corrections, not marks. `undo` takes back a done mark made by mistake. `review` is the walk. 13a's `proposal edit` stays the way to correct what was recorded about an action item.
- **The commands:**
  - `commitment list [--scope …] [--meeting <id> | --title <words> [--on <date>]] [--state open|done|broken|withdrawn|all] [--limit 1..200] [--json]`
  - `commitment show <id> [--scope …] [--json]`
  - `commitment done <id> [--note <text>] [--scope …] [--if-version <version>] [--json]`
  - `commitment altered <id> [--what <text>] [--owner <name>] [--due <words>] [--note <text>] [--scope …] [--if-version <version>] [--json]`
  - `commitment broken <id> [--note <text>] …` and `commitment withdrawn <id> [--note <text>] …`, with the same options as `done`
  - `commitment due <id> (<words> | --clear) [--scope …] [--if-version <version>] [--json]`
  - `commitment owner <id> (<name> | --clear) [--scope …] [--if-version <version>] [--json]`
  - `commitment undo <id> [--scope …] [--if-version <version>] [--json]`
  - `commitment review [--scope …] [--meeting <id> | --title <words> [--on <date>]]`
- **`<id>` is the commitment id**, the proposal's root id. A replacement id `<root>_e<n>`, as `proposal show` prints it, is accepted and means its root only when the proposal store or the ledger knows that id; any other `_e<n>` is `not_found`. Without `--scope`, the id is looked up in personal and every enrolled project.
- **Finding the commitment, in this order:**
  1. **A root that a malformed line names** (`ledger.malformed_roots`) is refused as `malformed` before anything is decided from the state in force, whether or not a readable `pending` line exists for it.
  2. **Found in more than one ledger** (only a hand copy makes this): `show` prints one card naming both scopes, flagged `ambiguous`. Every verb refuses (exit 3, `ambiguous`), saying the same id is in both ledgers and that one line must be removed by hand.
  3. **Not found while a ledger looked at could not be read:** refused as `ledger_unreadable`, naming that scope, never `not_found`, because the commitment may be in it.
  4. **Not found, and the proposal store holds the id approved** (its `pending` line not yet written: 15a's catch-up has not run, or the approval's write failed): `not_found`. The reason says the approval is not recorded in the ledger yet and names the repair: `pm-ai proposal approve <id> --scope <scope>` writes it, and so does the next daemon start.
  5. **Not found, and the proposal is waiting, rejected, expired, or of a type that records no commitment:** `not_found`, naming `pm-ai proposal show <id> --scope …`.
  6. **Otherwise** `not_found`, naming `pm-ai commitment list`.
- **Scopes,** as in 13a:
  - `people:` and the application scope are refused (exit 3, `scope_refused`); no `people` ledger exists (AD-4, AD-28);
  - a `project:` that is not enrolled is refused (exit 3, `not_enrolled`), naming the enrolled projects;
  - every command pm-ai prints carries `--scope`.
- **Asks nothing.** Every command except `review` asks no question. `review` is the walk the PM chose to start.
- **Choosing without an id:**
  - at the prompt, `<id>` completes (below);
  - `list` and `review` take `--meeting` or `--title`/`--on`;
  - every card prints its exact commands;
  - the id stays the only way to name one commitment on the command line, so a wrapper acts on the commitment it showed (13a's and `11f`'s reasoning).

### What each change writes (the line kinds 15a defines)

- **`done`, `broken`, `withdrawn`:** one `marked` line with `state=fulfilled`, `broken` or `withdrawn`, `by=pm`, `handle` when `pm_identity()` gives one (the lowest in sort order when it gives several), the `note` when given, `occurred` (below) and `at` the moment of the append, and `via` the surface.
  - **`via`** is `prompt` whenever the command was typed at the prompt, whichever process runs it (the daemon, or the prompt's own for `review`), and `terminal` otherwise. Story 5 adds `telegram`.
  - **When the PM's handle cannot be read** (`read_pm_identity`, `wiring.py:3532`, fails or lists the rows as unusable), the mark is still written, without `handle`, and the outcome says so in one sentence ("recorded without your handle: the connector rows could not be read"). In JSON, `"handle_recorded": false`.
  - Before writing, `mark_answer` is asked of the state and source in force.
    - **`yes`:** the line is written. From a telemetry state, the sentence says the mark confirms or overrides it.
    - **`already`** (the PM's own mark of that state is in force): nothing is written, outcome `already`, exit 0. The sentence names when and from where. A `--note` given is not recorded; the sentence says so, and JSON has `"note_recorded": false`.
    - **`refused`:** exit 3, `mark_refused`, with the reason from the table ("you marked it done on <date>, so it is not altered or withdrawn; mark it broken if it was not done after all").
- **`altered`:** the PM says what was agreed has changed.
  - **Every input is checked before anything is written:** the note's length, a non-empty `--what`, `--what` on a promise, and that `--due` resolves. An `--due` that names no date is refused (exit 3, `due_unresolved`) with nothing written, the mark included.
  - **A second `altered` is always written.** `MARKABLE` answers `yes` for `altered` from `(ALTERED, PM)` (`domain/commitments.py:141`), never `already`, so a later renegotiation with a new note, due, owner or wording is recorded as its own mark. Step 2 of the writer's steps (below) does not apply to `altered`.
  - **Then, in this order:**
    1. **One append** holding the `marked state=altered` line (with `--note`), plus an `owner_set` line when `--owner` differs from the owner in force, plus a `due_set` line when `--due` differs from the due in force by the rule `due` uses. The lines share one `occurred`.
    2. **Then, when `--what` is given and differs from the wording in force, 13a's edit of the commitment's live approved proposal** (`proposal_id` in the fold) with that wording only, as `proposal edit` does. The recorder writes its `revised` line (15a). The state stays `ALTERED`: a `revised` line never changes the state. A `--what` equal to the wording in force starts no edit, and `edited` is `null`, so 13a's `unchanged` never becomes `edit_refused`.
  - **The mark comes first** because it is the PM's decision and the one line that must not be lost. When the edit then fails, the command exits 3 with `edit_refused` and 13a's reason. The refusal object carries `"wrote"` (the kinds already appended) and `"next"` (the exact `pm-ai proposal edit <proposal> --scope … --what "…"` words), and the text says the mark is recorded and the wording is not.
  - **The bound.** Before starting the edit, the desk checks what is left of `COMMITMENT_WRITE_BOUND_SECONDS`. When less than 13a's edit needs is left (the 1-second busy wait plus one claimed step), the edit is not started, and the outcome is the same `edit_refused` with the reason "out of time", `wrote` and `next`.
  - **The version returned** by `altered --what` is the commitment's version after the `revised` line, so `done --if-version <that>` acts.
  - **`--what` is for an action item only.** A spoken promise's words are what was said, and 13a gives `implicit_commitment` no editable field. `altered --what` on a promise is refused (exit 3, `what_not_editable`): "a spoken promise keeps the words that were said; record how it changed with --note". Nothing is written.
  - `--owner ""` and `--due ""` clear the field, as 13a's edit does. `altered` with no option is valid: the mark alone, or with its note.
- **`due` and `owner` act on an open commitment only** (Andrei, 2026-10-26). On a `fulfilled`, `broken` or `withdrawn` one they are refused (exit 3, `commitment_closed`), before the version check, with nothing written. The reason names the state and when, and the way to reopen it that `mark_answer` allows: `pm-ai commitment altered <id> --scope … --due "…"` from `broken` or `withdrawn`, `pm-ai commitment undo <id> --scope …` while a done mark's window is open, and otherwise none ("you marked it done on <date>; it is closed"). `altered --due` and `--owner` on a broken or withdrawn commitment are allowed, because the `altered` mark reopens it.
- **`due`:** one `due_set` line. `--clear` writes `due=none`.
  - **Words that are empty or only spaces** are a usage error (exit 2): they would otherwise read as nothing said and clear the due silently.
  - **A date with its year written, in any form 15a's resolver accepts** (`2026-10-20`, `20.10.2026`, `20 Oct 2026`, `20 октября 2026`), is taken as typed, including one already past, because a typed date is explicit. This is read before `due_dates.resolve`, which refuses a date before its day (`_said_year`, `due_dates.py:208-212`, used for ISO at `:251-253`). A past date's sentence adds "(already past)". The same rule applies to `altered --due` and to `review`'s `u` and `a`.
  - **Other words are resolved against today**, the PM's day in `display_timezone` (else the machine's zone), by `due_dates.resolve(words, today)`. The PM is setting the date now, so "Friday" means the coming Friday. `today`'s day is never estimated.
  - **Today's own weekday, and the end of the week from Friday on** (Andrei, 2026-10-26). For the PM's typed words only:
    - a plain weekday that is today's weekday ("friday" typed on a Friday, `пятница`, `в пятницу`, `к пятнице`, `до пятницы`) means that weekday next week, 7 days out;
    - "end of the week" in any of 15a's forms (`end of week`, `EOW`, `до конца недели`, …), typed on a Friday, Saturday or Sunday, means next week's Friday. From Monday to Thursday it is this week's Friday, as the resolver gives;
    - `this <weekday>` and `в эту <weekday>` on that same weekday stay unresolved (`due_unresolved`): "this Friday" said on a Friday may mean today. The reason names the date form.

    15a's resolver leaves these cases unresolved for what a meeting said, and must keep doing so. So this is a rule in the desk (`pm_ai/app/commitments.py`), not in `due_dates`. `due_dates` gains only a pure helper that names the form of the words (a weekday and which one, `this <weekday>`, the end of the week, or other), from the same tables, so the desk reads no second word list. `due_dates.resolve` and every 15a and `7e` result are unchanged. The output always shows the resolved date ("due Fri 6 Nov").
  - **Words that name no one date** are refused (exit 3, `due_unresolved`), naming the forms that resolve (15a's table) and the year-written forms. `due_set` has no `unresolved` value.
  - **`unchanged`** (nothing written, exit 0) only when the due in force has that date, is not estimated, and was set by the PM. The same date over an estimated due, or over one worked out from what was said, is written: it becomes exact and "set by you". `--clear` when the due in force is already `none` is `unchanged`.
  - **The due as said is kept.** After a `due_set`, the card and JSON still show `due.said` from the value lines ("by next sprint"), beside the PM's date.
- **`owner`:** one `owner_set` line. `--clear` writes it with no `owner`.
  - The name is the PM's typed text, NFC-normalised and folded to one line (`fold_typed`). The 80-character limit (`OWNER_LIMIT`) is counted after folding (exit 2 over it), and a name empty after folding is exit 2.
  - **A name that reads as a handle or an address** (`reads_as_handle`, `domain/handles.py`) is refused (exit 3, `owner_is_handle`): an owner is a name, never a handle (AD-34). It is not linked here.
  - **`unchanged`** when the owner in force equals it after the same normalisation; `--clear` with no owner in force is `unchanged`.
- **The note** is the PM's typed text, folded to one line, at most 200 characters (`NOTE_LIMIT`, exit 2 over it). Typed text is trusted as his own and is not passed through the prompt-injection filter (13a).
- **No change writes to `operational.db`,** except `altered --what` through 13a's edit. A commitment's state is never a proposal state (AD-14): `proposal show` of an item marked done still says `approved`.

### Undoing a done mark (Andrei, 2026-10-26)

- **Why.** In `review`, `d` is one keypress with no confirmation, so a slip marks the wrong commitment done. Andrei: "Allow undo for the next 4 hours". `d` stays one keypress.
- **Only the PM's own `fulfilled` mark can be undone.** `broken` and `withdrawn` are not covered: in `review`, `b` and `w` already ask for a note, so they are not one-keypress accidents, and on the command line each is typed out. A slip there is corrected by a later mark, as the table allows.
- **The window:** `UNDO_WINDOW = timedelta(hours=4)`, one named constant in `domain/commitments.py`, measured from the mark's `occurred` to the clock's now.
- **The line: a new kind, `undone`,** rather than a `marked` line with an extra field. 15a's reader keeps unknown keys, so a `marked state=fulfilled undone_entry=…` line would read as one more done mark to any reader that missed the field. A kind of its own cannot be misread:
  - `- entry=… commitment=<root> kind=undone undone_entry=<the mark's entry> by=pm [handle=…] occurred=<instant> via=<surface> at=<instant>`;
  - its identity is `(commitment, "undone", undone_entry)`, so one mark is undone at most once;
  - `LineKind` gains `UNDONE`, and the parser requires `undone_entry` (a `cml_` id), `by=pm`, `via` and `at`.
- **The fold drops the undone mark.** An `undone` line applies only when all of these hold. Otherwise it is counted as refused, and nothing changes.
  - it names a `marked state=fulfilled by=pm` line of the same commitment;
  - its `occurred` is within `UNDO_WINDOW` of that mark's;
  - no other line of the commitment sorts between the two by `(occurred, entry)`.

  When it applies, the mark is left out of the state's fold, so the state is the one before the mark (`PENDING`, `ALTERED`, a telemetry state, or an earlier PM mark). The fold stays deterministic (AD-35). SM-9's manual-override count leaves out an undone mark and its `undone` line.
- **`commitment undo <id>`** writes the `undone` line when:
  1. the state in force is `FULFILLED` from the PM's own mark;
  2. that mark is the commitment's newest line, so the commitment's version is the mark's entry id;
  3. and the clock is within `UNDO_WINDOW` of the mark's `occurred`.

  It follows the writer's steps (compare-and-append, `--if-version`, re-read on `LedgerChanged`).
- **Its answers:**
  - **written:** outcome `undone`, "<id>: your done mark of <time> is undone; it is <state> again";
  - **`already`** (exit 0): the newest line is already an `undone` of the newest done mark;
  - **`undo_expired`** (exit 3): more than 4 hours since the mark, "marked done at <time>, more than 4 hours ago; mark it broken if it was not done";
  - **`undo_superseded`** (exit 3): a later line follows the mark, naming that line's kind as `changed` does;
  - **`nothing_to_undo`** (exit 3): the state in force is not the PM's own done mark. This covers a telemetry `FULFILLED` ("telemetry recorded it done; pm-ai undoes only your own done mark"), an open commitment, and a broken or withdrawn one.
- **Offered while the window is open:**
  - the card's `undo` action is available, with "until <time>";
  - after `done`, the text prints `undo until <time>: pm-ai commitment undo <id> --scope … --if-version <version>` first among its next steps;
  - the verb JSON carries `"undo": {"command": [words], "until": instant} | null`.

  Outside the window, the action is unavailable, with the reason.
- **In `review`:** the `z` key (below). This is the smallest form: one verb, one card action and one key.

### Every change is a compare-and-append (AD-37)

- **A commitment's version is the `entry` id of its newest line in file order.** The ledger is append-only, so any line appended for a commitment becomes its newest, and the version changes. A line of another commitment leaves it unchanged. Entry ids are hashes, not counters, so a version is compared only for equality, never as larger or smaller. Cards and JSON carry it as `version` (`cml_` plus 12 hex characters).
- **`--if-version <version>`** on `done`, `altered`, `broken`, `withdrawn`, `due`, `owner` and `undo` acts only if the commitment is still at that version. A wrapper passes the version from the card it displayed, and `review` always does. A value that is not `cml_` plus 12 lower-case hex characters is a usage error (exit 2), not `changed`.
- **The writer's steps,** in `CommitmentDesk` (`pm_ai/app/commitments.py`):
  1. read and fold the scope's ledger through `CommitmentLog.read`;
  2. **"already" and "unchanged" before the version**, as in 13a: when the mark asked for is the PM's own mark in force (`mark_answer` says `already`), or the due or owner asked for is already in force by the `unchanged` rules above, answer `already` or `unchanged` (exit 0) whatever the version. This never applies to `altered`;
  3. if `--if-version` is given and differs, refuse (exit 3, `changed`), with the current card and one sentence built from the kind of the commitment's newest line: "marked <state> on <date>, from <via>" (`marked`); "its due was set to <date> on <date>" (`due_set`); "its owner was set to <name>" or "its owner was cleared" (`owner_set`); "its wording was edited on <date>" (`revised`); "telemetry recorded it <state> on <date>" (`verdict`); "it was recorded as a commitment on <date>" (`pending`);
  4. decide the lines from the state in force (`mark_answer`) and the values in force;
  5. **`occurred`** for the new lines is the later of the clock's now and the newest `occurred` among the commitment's lines plus one microsecond. A retried mark therefore always sorts after the line it lost to, so `broken` retried after a competing `done` ends `BROKEN`, as the PM asked last;
  6. leave out any line whose `entry` is already in the ledger;
  7. append with `after` set to the size read.
- **On `LedgerChanged`,** read again and go back to step 2, recomputing `occurred`. The version check is against the commitment's own version, so a line another commitment gained does not refuse the change. At most `ATTEMPTS` (3) reads, then `busy`. Nothing is appended blindly.
- **An append that raised after its bytes reached the file** is found on the re-read: when the ledger already holds this call's own entry id, the change is reported as made, and nothing is written twice.
- **Busy at once.** `ArtifactBusy`, `WriterBusy`, `WriterStopped`, `OperationalStoreUnavailable` and `ClaimReentered` answer `busy` without another attempt, as 15a's recorder does (`app/commitments.py:172-221`). `ATTEMPTS` counts `LedgerChanged` only. A busy answer comes within `COMMITMENT_WRITE_BOUND_SECONDS`.
- **Two surfaces at once:**
  - both mark one commitment `done`: one append wins. The other meets `LedgerChanged`, reads again, finds the PM's `fulfilled` in force, and answers `already`, exit 0. One line is written;
  - `done` lands first, then `withdrawn`: `withdrawn` from `(FULFILLED, PM)` is refused (`mark_refused`, or `changed` with `--if-version`);
  - `withdrawn` lands first, then `done`: `done` from `(WITHDRAWN, PM)` is allowed (`MARKABLE`), so it is written and the state is `FULFILLED`, unless `--if-version` was given, which refuses it as `changed`.

### Reading the ledger: what the fold adds

- **The fold gains, per commitment** (`CommitmentLedger.details[root]`, a `Details` value in `core/commitment_log.py`):
  - `version`: the `entry` of its newest line in file order;
  - `mark`: the PM's mark in force (state, `occurred`, `via`, `note`), or none;
  - `due_by_pm` and `owner_by_pm`: whether the due or the owner in force came from his `due_set` or `owner_set`;
  - `changed_at`: the newest `occurred` among its lines.
- **One fix to 15a's fold, for the owner.** Today the owner is the newest of the newest value line and any `owner_set` line (`commitment_log.py:782-783`). A `revised` line always carries the proposal's owner, so a `proposal edit --what` after `commitment owner` brings back the old owner without anyone asking for it.
  - **From 15c, the owner comes from** the newest by `(occurred, entry)` of: the `pending` line, each `revised` line that changed the owner, and `owner_set` lines. Order decides here, never file order (AD-35).
  - **"Changed the owner"** compares each `revised` line with the value line before it in 15a's value order `(replacement number, occurred, entry)`: `_optional(previous, "owner") != _optional(line, "owner")`, with no owner counted as a value. A `revised` line that clears the owner counts, and so does a `proposal edit --owner` that sets it back to an earlier name.
  - This mirrors 15a's rule for the due, which a `revised` line carries only when it changed. The line format does not change, so no line written before 15c reads differently, except in this case.
  - **Tested:** `commitment owner` then a `proposal edit --what` keeps the PM's owner; `commitment owner` then a `proposal edit --owner` gives the edit's owner; an `--owner` edit made before `commitment owner` but whose `revised` line 15a's catch-up writes only afterwards keeps the edit's earlier `occurred`, so the PM's later owner stands; and a file-order swap gives the same owner.
- **Bad lines,** counted by 15a (malformed, orphans, duplicates, conflicts, and marks the table refused), are shown by `list`, one line per scope, as 15a promised. They never fail the command. A root a malformed line names is refused as `malformed`, as above.

### `commitment list`

- **`--state`:**
  - `open`, the default: not closed. That is `pending` or `altered` today. Story 16's `unknown` and `error` are open too, because neither is terminal;
  - `done`: `fulfilled`, by the PM's mark or by telemetry;
  - `broken`, `withdrawn`;
  - `all`.
- **Order:**
  - **open ones by due:** a resolved date first, earliest first; then `unresolved`; then `none`. Ties are broken by the meeting's start, newest first, with a commitment whose line has no start after those that have one, then the commitment id. The key is `(due rank, due date or date.max, 0 if a start else 1, minus the start's timestamp or 0, commitment id)`, so no missing value is ever compared with a date;
  - **closed ones** (`done`, `broken`, `withdrawn`) by `changed_at`, newest first;
  - **`all`:** the open ones in due order, then the closed ones by `changed_at`.
- **Past due is shown, not acted on.** A card says "past due" after its date only when:
  - the commitment is open;
  - its due has a date;
  - the date is not estimated;
  - and the date is before today in the display zone. A commitment due today is not past due.

  An estimated date is shown with "(meeting day estimated)" and is never called past due. `unresolved` and `none` are never past due. Story 16 owns what happens to a past-due commitment. Nothing here sends, marks or asks.
- **`--limit`** defaults to 20, at most 200, and the number left out is stated.
- **Scopes.** Without `--scope`, personal and every enrolled project. A ledger that cannot be read is named on one line, and the others are still listed (exit 0). With `--scope X` and X's ledger unreadable, `list` is refused (exit 3, `ledger_unreadable`), because nothing could be listed. `--meeting` and `--title`/`--on` choose one meeting exactly as 13a's listing does (ambiguity refused across scopes, exit 3).
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
- **`list`:** `{"format": 1, "state": string, "scopes": [string], "unreadable": [{"scope", "reason"}], "ledgers": [{"scope", "malformed", "orphans", "duplicates", "conflicts", "refused"}], "counts": {"by_state": {state: int}, "by_source": {"none": int, "pm": int, "telemetry": int}}, "more": int, "next": {"review": [words]|null, "more": [words]|null}, "commitments": [card, …]}`. `counts` counts the commitments that match the filters before `--limit`, by state and by the source of the state.
- **`show` and every verb:** `{"format": 1, "outcome": string, "commitment": card, "wrote": [kind, …], "edited": {"proposal_id": string}|null, "note_recorded": boolean, "handle_recorded": boolean|null, "next": [words]|null}`.
  - `outcome` is one of `shown`, `marked`, `already`, `due_set`, `owner_set`, `altered`, `undone`, `unchanged`;
  - `wrote` lists the line kinds this command appended (`marked`, `due_set`, `owner_set`), empty when it wrote none;
  - `note_recorded` is false when a `--note` was given and not written (`already`), and also false when none was given; `handle_recorded` is `null` when no line was written;
  - `edited` names the replacement `altered --what` made;
  - `next` is the next open commitment of the same meeting (`commitment show …`), else `commitment list --scope …`.
- **A card:**

  ```
  {"commitment_id", "version", "scope", "also_in": [string],   // other ledgers holding the same id; [] normally
   "type", "proposal_id",
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

  Instants are ISO 8601 UTC. `due.value` is a date, `unresolved` or `none`, as in 15a's `commitment` object. The action names are `done`, `altered`, `broken`, `withdrawn`, `due`, `owner`, `undo` and `show`. On a closed commitment `due` and `owner` are unavailable, with the reason.
- **A refusal (exit 3):** `{"format": 1, "outcome": "refused", "code": string, "reason": string, "commitment": card|null, "wrote": [kind, …], "next": [words]|null}` on standard output. `wrote` is empty except after `edit_refused`; `next` is the exact command that repairs or continues, when there is one (the `proposal approve` or `proposal show` of `not_found`, the `proposal edit` of `edit_refused`, `commitment list` otherwise), else `null`. A usage error (exit 2) stays a message on standard error, as for every command.
- **The refusal codes:**
  - `not_found`: no commitment with that id in the scopes looked at; the reason follows the finding order above (an approval not yet recorded, a proposal that is not a commitment, or nothing);
  - `ambiguous`: the id is in two ledgers;
  - `scope_refused`, `not_enrolled`;
  - `ledger_unreadable`: a ledger looked at could not be read, named;
  - `malformed`: a malformed line names the id;
  - `owner_is_handle`: the owner typed reads as a handle or an address;
  - `mark_refused`: the mark table refuses it from the state in force;
  - `changed`: `--if-version` no longer matches;
  - `due_unresolved`: the words name no one date;
  - `what_not_editable`: `altered --what` on a spoken promise;
  - `edit_refused`: `altered --what`'s edit was refused (13a's reason) or not started for lack of time, after the mark was written;
  - `busy`, `git_not_excluded`, `disk`: 15a's meanings, for the append;
  - `commitment_closed`: `due` or `owner` on a done, broken or withdrawn commitment;
  - `undo_expired`, `undo_superseded`, `nothing_to_undo`: `undo` refused (above);
  - `no_terminal`: `review` without a terminal.
- **Exit codes:**
  - **0:** changed, `already`, `unchanged`, shown, or listed, an empty listing included;
  - **2:** usage: an unknown `--state`, a limit outside 1 to 200, `due` or `owner` with both a value and `--clear` or with neither, due words that are empty or only spaces, an owner empty after folding, an empty `--what`, a value over its limit, an `--if-version` that is not `cml_` plus 12 hex characters;
  - **3:** every refusal above.

### `commitment review`

- **Walks the open commitments in `list`'s order,** with `list`'s filters, one compact card at a time.
- **The keys:** `[d]one [a]ltered [b]roken [w]ithdrawn d[u]e [o]wner [s]kip [q]uit`, plus `[z] undo that done` after a `d` (below). A key whose action is unavailable is refused with its reason, and the card is asked again. On a closed commitment `u` and `o` are unavailable (Andrei, 2026-10-26).
  - `d` writes the mark at once: one keypress, no confirmation (Andrei, 2026-10-26). The sentence after it names the undo: "marked done; [z] undoes it until <time>".
  - `z`, offered from the card after a `d` until the walk ends or 4 hours pass, undoes the most recent `d` of this walk by `undo`'s rules, checked against the version that `d` wrote. A later line on that commitment refuses it as `undo_superseded`, said in one sentence. The walk then goes on with the card it was on.
  - `b` and `w` ask "note (Enter for none)", then write.
  - `a` asks, with the current value shown: the wording (an action item only), the owner and the due (Enter keeps, `-` clears), then the note. Then it writes, as `altered` does.
  - `u` asks for the due (words, or a date with its year, `-` clears), by `due`'s rules. Words that name no date are asked again with the reason.
  - `o` asks for the owner (`-` clears), by `owner`'s rules.
  - **Enter alone** at `u` or `o` keeps the value: nothing is written, and the card counts as skipped.
  - **A value the command line would refuse as usage** (a note or owner over its limit, empty due words) is asked again with the limit or the reason stated. The walk never exits 2.
- **Each answer is one change,** checked against the version its card showed. On a conflict, the current card is shown again with one sentence saying what changed.
- **The ledger is read again before each card.** A commitment that is no longer open (marked from elsewhere since the walk began) is skipped with one sentence ("<what>: marked done from the prompt meanwhile; skipped") and counted as such.
- **Nothing open:** the empty-listing sentence of `list` and its next step, exit 0.
- **The end:** one line counts what was marked done, altered, broken or withdrawn, what was undone (a `d` undone by `z` counts as undone, not done), the dues and owners set, and what was skipped, then the next step. Quitting, Ctrl-C and end of input end the walk the same way: the counts so far, exit 0. Every answered change is complete. Ctrl-C or end of input inside a sub-question (a note, the due, the owner, `a`'s questions) abandons that change: nothing of it is written, and it is not counted.
- **Without a terminal** (stdin not a TTY), it is refused (exit 3, `no_terminal`), naming `commitment list --json` and the verbs.

### Where it runs (AD-21)

- **At a terminal:**
  - `list` and `show` compose nothing (`composes=False`, as `proposal list`). They read each ledger by path, through the same `parse_ledger` and `fold`, and ask for no master key or keychain. `commitments_log.md` is plaintext (AD-6);
  - the verbs are composed as `proposal approve` is: the store, its claimed step with a 1-second busy wait, the git check for a project ledger (AD-43), and the `connectors/` rows for the PM's handle, which are not encrypted. No master key.
- **At the prompt:**
  - `list` and `show` join `DAEMON_BOUNDS` (`entry.py:606`) with `CONFIG_SHOW_BOUND_SECONDS`, 1 second;
  - `done`, `altered`, `broken`, `withdrawn`, `due`, `owner` and `undo` join it with `COMMITMENT_WRITE_BOUND_SECONDS = 2.0`, declared in `pm_ai/app/commitments.py`. `altered --what` includes 13a's edit within the same 2 seconds;
  - inline through the daemon, printing exactly what the terminal prints. The daemon's desk hands its appends to the writer thread, as `prompts.py:1146` does for proposals.
- **`commitment review`** joins `TERMINAL_BOUND` (`dispatch.py:4963`), so at the prompt it runs in the prompt's own process.
- **Each command's help names its bound** where it has one.
- **Timed tests:** a mark on a 5,000-line project ledger, with the git check and one `LedgerChanged`, within 2 seconds; `altered --what` on the same, within 2 seconds.

### Completion (4u's rules)

- **`<id>`** completes from the commitments the verb can act on:
  - for `done`, `altered`, `broken` and `withdrawn`, those whose `mark_answer` is `yes`;
  - for `due` and `owner`, the open ones only;
  - for `undo`, those whose undo is available now (the PM's done mark is the newest line, within 4 hours);
  - for `show`, any.

  At most 100 are offered, in `list`'s order. Each inserts the id and shows `owner: what` (or `said by …`), the meeting's title and the due. Typed text matches the start of the id or of any word of `what`, the owner, the speaker or the title, ignoring case, as `13a` does (`completion.py:566`).
- **`owner`'s name** completes from the owners and speakers already in that scope's ledger, the commitment's own speaker first.
- **`--state`** completes from the five words. **`--meeting`** completes from meeting ids, as `proposal list` does: `_BY_COMMAND` (`completion.py:155`) gains `(("commitment", "list"), MEETING_ARGUMENT)` and `(("commitment", "review"), MEETING_ARGUMENT)` → `MEETING_IDS`. Without those rows, `meeting` is free text (`FREE_TEXT_NAMES`) and would silently offer nothing.
- **The reader** reads the ledgers by path with `parse_ledger` and `fold`: no `StorageService`, no master key, keychain or daemon, and nothing offered on failure. So there is still one parser of the file (`derivation-services.md` rule 3).

### The reject refusal names the commands (amends 15a's refusal)

- **`proposal reject` on an approved item that is a commitment** (`proposals.py:822`, `_not_rejected`) still refuses, exit 3, with the row and the ledger unchanged. Its text becomes: `<id> was approved on <date> and is a commitment now, so it is not rejected; nothing was changed.`, then:
  - `If approving it was a mistake: pm-ai commitment withdrawn <root> --scope <scope>.`, only when `mark_answer` gives `yes` for `withdrawn` from the state in force;
  - `If what was agreed has changed: pm-ai commitment altered <root> --scope <scope> --note "…".`, only when it gives `yes` for `altered`;
  - `To correct what was recorded: pm-ai proposal edit <id> --scope <scope> --what "…".`, always.

  The sentences are built from one read of the ledger and `mark_answer`, so a commitment the PM already marked done is never told to mark it withdrawn. **When the ledger holds no line for the root** (the approval's write failed and nothing has repaired it), the first two sentences are replaced by `It is not recorded in the ledger yet: pm-ai proposal approve <id> --scope <scope> records it.` **When the ledger cannot be read,** both mark sentences are given, unchecked, after `the ledger could not be read, so these may be refused:`.
- **In JSON,** 13a's refusal object gains `"commitment": {"commitment_id": <root>, "scope": <scope>, "recorded": boolean|null, "show": [words]|null, "commands": {name: [words]}}`, with `commands` holding exactly the commands the text offers (`withdrawn`, `altered`, `edit`, or `approve` for the repair), `recorded` null when the ledger could not be read, and `show` null when the root is not in the ledger. `format` stays 1.
- **The approved card's `reject` reason** (`COMMITMENT_NOT_REJECTED`, `proposal_cards.py:59`) becomes: "approved, so it is a commitment now; mark it withdrawn if approving it was a mistake, or edit it to correct what was agreed".

### What the dashboard shows (Andrei, 2026-10-26: option A)

- **15d owns the surfaces.** 15c changes the pages only so that a commitment the PM closed stops looking open:
  - **the dashboard's meeting digest** (`pipelines.py:2674`, `_meeting_digest`) leaves out an approved action item whose commitment is `fulfilled` (by the PM or by telemetry), `broken` or `withdrawn`, as it leaves out a rejected one. An `altered` one is still shown. The lookup is by `root_of(proposal_id)`, so an edited item's replacement finds its root's commitment. `_digests_over` (`:2774`) reads each personal or project scope's ledger once per render through `CommitmentLog.read`, and no other scope's;
  - **Team Activity** (`_item_decisions`, `pipelines.py:3111`) hides a `withdrawn` item, as it hides a rejected one (`ItemDecision(hidden=True)`, `:3145`), looked up by the root. A done or broken item stays, because a line there is a note of what the meeting said;
  - **both fail open:** a ledger that cannot be read, or a scope refused with `NotACommitmentScope`, shows the items as today, and the page says nothing more. Showing a closed item as approved is harmless.
- **Unchanged until 15d:** the owner and due the pages show (the proposal's), the morning brief beyond what the digest gives it, the cards' lack of a commitment state, and the overflow line naming `proposal list --state open`.

### The readers of the ledger after 15c

- `CommitmentLog.read`: 15a's recorder and catch-up; 15c's `CommitmentDesk` (`list`, `show`, every verb, `review`); the dashboard digest and Team Activity (as above);
- `parse_ledger` and `fold` over bytes read by path: `list` and `show` at a terminal, and completion;
- 15b's indexer, when it exists. 15b's trigger runs after every append through the store, so 15c's lines are indexed too. That is a requirement on 15b's spec, not work here.

### The architecture tests and the skip count

- **`EXPECTED_SKIPS` stays 6** (`tests/conftest.py:192`). No skipping test depends on 15c: `test_ad35_sweeper_will_not_declare_broken_without_coverage` and `test_ad36_self_authored_events_are_excluded_from_evidence` still skip on `pm_ai.core.commitments`, which is story 16's. 15c creates no module of that name.
- **New, passing in 15c** (`tests/architecture/test_domain_invariants.py`):
  - `test_ad37_two_surfaces_marking_one_commitment_record_one_mark`: two `CommitmentDesk`s over one store mark one commitment `done`; one `marked` line, and the second answers `already`;
  - `test_ad37_a_stale_version_never_appends`: `--if-version` with the version before another mark refuses with `changed`, and the ledger's size is unchanged.

  The two-surfaces test forces the interleave: B reads, A appends, then B appends. It asserts that B met `LedgerChanged` exactly once, read again, answered `already`, and that the ledger holds exactly one `marked` line. AD-14's terminal set needs no new test: `tests/domain/test_commitment_rules.py:47` already asserts `WITHDRAWN.is_terminal`.
- **Unchanged and still passing:** `test_ad14_proposal_and_commitment_lifecycles_stay_distinct` (`:283`), both AD-28 tests (`:165`, `:209`), and `test_ad33_ledger_entries_are_self_contained` (`:735`).
- **Command tables:** `test_every_command_the_daemon_runs_declares_its_bound` (`tests/slice/test_line_requests_hardening.py:95`) covers the new entries in `DAEMON_BOUNDS` and `TERMINAL_BOUND`, and the completion test over `TABLE` covers every new name's source. A new slice test asserts the values: `DAEMON_BOUNDS` gives `CONFIG_SHOW_BOUND_SECONDS` for `commitment list` and `show` and `COMMITMENT_WRITE_BOUND_SECONDS` for the seven writes, `COMMITMENT_WRITE_BOUND_SECONDS == 2.0`, and each command's help names its seconds.
- **`.importlinter` is unchanged:** `core/commitment_cards.py` imports `domain` and `core`, `app/commitments.py` is in `app`, and the CLI reaches the desk through a port protocol in `surfaces/cli/dispatch.py`, as `ProposalDeskPort` (`:4568`) does.

### No real data

Tests use `11d`'s fixture names (`Example Weekly Sync-up`, `Alex Morgan`, `Sam Rivera`) and `example.com` handles. Nothing reads the PM's own `~/.pm-ai` or `~/.manager-ai`.

**Ask First:** none open. The one question, what the pages show before 15d, was decided by Andrei on 2026-10-26 (option A, above).

**Never:**

- **The ledger:**
  - no line rewritten or removed, and no whole-file write;
  - no append without the size read checked in the claimed step, and no blind retry;
  - no mark the table refuses, and no second line for a mark already in force;
  - no line of a kind 15a does not define other than `undone`, and no `verdict` line;
  - no undo of a broken, withdrawn or telemetry mark, and none after `UNDO_WINDOW` or after a later line;
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
- **Scope of this slice:** no index (15b), no page change beyond the digest and Team Activity rules above (15d), no telemetry (16), and no module named `pm_ai.core.commitments`.
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
| Altered wording, edit fails | the edit is refused after the mark | mark kept; `edit_refused` with 13a's reason; `wrote: ["marked"]`; `next` is the exact `proposal edit` words; the text says the mark is recorded | exit 3 |
| Altered wording on a promise | `altered --what …` on a spoken promise | `what_not_editable`; nothing written | exit 3 |
| Altered again | `altered --note "a"`, then `altered --note "b"` | two `marked state=altered` lines (`MARKABLE` allows altered from altered); never `already` | exit 0 |
| Altered, unresolved due | `altered --due "next sprint" --note "x"` | `due_unresolved`; nothing written, the mark included | exit 3 |
| Altered, past due date | `altered --due "20 Oct 2026"` on 25 Oct | `due_set due=2026-10-20`; "(already past)" | exit 0 |
| Altered, same wording | `altered --what` with the wording in force | the mark written; no edit; `edited: null` | exit 0 |
| Altered, out of time | the mark's append leaves too little of the 2 s for the edit | mark kept; `edit_refused` "out of time"; `wrote: ["marked"]`; `next` is the `proposal edit` words | exit 3 |
| Altered wording, version | `altered --what …`, then `done --if-version <the returned version>` | the returned version is after the `revised` line; `done` written | exit 0 |
| Due words | `due <id> friday` on Sun 25 Oct | `due_set due=2026-10-30`; the card says "set by you"; never estimated | exit 0 |
| Due ISO in the past | `due <id> 2026-10-20` on 25 Oct | written; "(already past)" | exit 0 |
| Due past, other forms | `due <id> "20.10.2026"`, `due <id> "20 Oct 2026"` on 25 Oct | each written as 20 Oct; "(already past)" | exit 0 |
| Due unresolved | `due <id> "next sprint"` | `due_unresolved`, naming the forms; nothing written | exit 3 |
| Due blank | `due <id> "  "` | usage error | exit 2 |
| Due cleared | `due <id> --clear` | `due_set due=none` | exit 0 |
| Due cleared twice | `due <id> --clear` with no due in force | `unchanged` | exit 0 |
| Due the same, set by the PM | the PM's exact date already in force | `unchanged`; nothing written | exit 0 |
| Due the same, estimated | the date in force is the same but estimated, or worked out from what was said | `due_set` written; now exact and "set by you"; `due.said` still shown | exit 0 |
| Due both | `due <id> friday --clear` | usage error | exit 2 |
| Owner of a promise | `owner <id> "Sam Rivera"` on a promise with no owner | `owner_set owner="Sam Rivera"`; the card reads "said by …, owner Sam Rivera" | exit 0 |
| Owner over its limit | 81 characters after folding | usage error | exit 2 |
| Owner blank | only spaces or line breaks | usage error | exit 2 |
| Owner is a handle | `owner <id> alex@example.com` | `owner_is_handle`; nothing written | exit 3 |
| Owner the same | the same name in another Unicode form (NFC) | `unchanged` | exit 0 |
| Undo inside the window | `done` at 10:00, `undo` at 13:59 | one `undone` line naming the mark; state back to the one before (`PENDING`); outcome `undone` | exit 0 |
| Undo back to altered | `altered`, then `done`, then `undo` within 4 h | state `ALTERED` again | exit 0 |
| Undo after 4 h | `done` at 10:00, `undo` at 14:01 | `undo_expired`; nothing written | exit 3 |
| Undo after another line | `done`, then `due_set` from the prompt, then `undo` | `undo_superseded`, naming the due set; nothing written | exit 3 |
| Undo of a telemetry mark | a hand-written `verdict state=fulfilled`, then `undo` | `nothing_to_undo`, "telemetry recorded it done" | exit 3 |
| Undo of broken | marked broken, then `undo` | `nothing_to_undo` | exit 3 |
| Undo twice | `undo` again after it was written | `already` | exit 0 |
| Undo by version | `undo --if-version <the done's version>`; and with an older version | written; the older one refused `changed` | 0 / 3 |
| Undo offered | `done <id> --json` | `undo.command` with `--if-version`, `undo.until` 4 h after the mark; the card's `undo` action available; after 4 h unavailable with its reason | exit 0 |
| Undo line read back | an `undone` line naming a mark more than 4 h older, or one with a line between | counted as refused; the mark stands (AD-35) | N/A |
| Undo from review | `d` on card 1, then `z` on card 2 | card 1's done mark undone; the closing counts say "1 undone", not "1 marked done"; the walk goes on with card 2 | continues |
| Due on a closed commitment | `due <id> friday` on a commitment marked done | `commitment_closed`, naming the state and `commitment undo` while the window is open; nothing written | exit 3 |
| Owner on a closed commitment | `owner <id> "Alex Morgan"` on a withdrawn one | `commitment_closed`, naming `commitment altered … --owner` to reopen it | exit 3 |
| Closed card | `show <id> --json` of a broken commitment | `actions.due` and `actions.owner` unavailable, with the reason; completion does not offer it for `due` or `owner` | exit 0 |
| Altered reopens with a due | `altered --due friday` on a broken commitment | written; state `ALTERED`, due the coming Friday | exit 0 |
| Friday on a Friday | `due <id> friday` on Fri 30 Oct; also `в пятницу` | `due_set due=2026-11-06`; "due Fri 6 Nov" | exit 0 |
| Friday on a Thursday | `due <id> friday` on Thu 29 Oct | `due=2026-10-30` | exit 0 |
| End of week from Friday on | `due <id> "end of week"` on Fri 30 Oct, Sat 31 Oct and Sun 1 Nov; also `до конца недели` | each `due=2026-11-06`, shown as "due Fri 6 Nov" | exit 0 |
| End of week midweek | `due <id> EOW` on Wed 28 Oct | `due=2026-10-30` | exit 0 |
| This Friday on a Friday | `due <id> "this friday"` on Fri 30 Oct | `due_unresolved`, naming the date form | exit 3 |
| Extraction unchanged | a meeting on Fri 30 Oct whose action item says "by Friday" | still `due=unresolved` on its `pending` line (15a) | N/A |
| Owner kept after an edit | `owner <id> "Alex Morgan"`, then `proposal edit <id> --what "…"` (owner unchanged in the proposal) | owner stays Alex Morgan (the fold fix) | exit 0 |
| Owner changed by an edit | then `proposal edit <id>_e1 --owner "Sam Rivera"` | owner becomes Sam Rivera | exit 0 |
| Stale version | `done --if-version <old>` after another mark | `changed`, with the current card and what changed; ledger size unchanged | exit 3 |
| Already beats the version | `done --if-version <old>` when the PM's done is in force | `already` | exit 0 |
| Other commitment moved | `done A --if-version <A's version>`; B gains a line between A's read and append | `LedgerChanged`; read again; A's version unchanged, so written; a fold unit test shows B's line leaves `details[A].version` unchanged | exit 0 |
| Two surfaces | terminal and prompt `done` at once, B's append forced after A's | one `marked` line; B met `LedgerChanged` once and answered `already` | both exit 0 |
| Done, then withdrawn | `done` lands, then `withdrawn` from the other surface | `withdrawn` refused, `mark_refused` | exit 3 |
| Withdrawn, then done | `withdrawn` lands, then `done` from the other surface without `--if-version` | `done` written; state `FULFILLED` | exit 0 |
| Retry sorts last | `broken` meets `LedgerChanged` from a `done` whose `occurred` is later than the clock | the retried `broken` gets the `done`'s `occurred` plus 1 µs; state `BROKEN` | exit 0 |
| Append raised after writing | the append raises after its bytes reached the file | the re-read finds this call's entry id; reported as made; one line | exit 0 |
| Busy | the claim is held past its wait | `busy` at once, within 2 s; no further attempt | exit 3 |
| Ledger kept changing | `LedgerChanged` three times | `busy` | exit 3 |
| Bad version | `done --if-version 42` | usage error | exit 2 |
| No handle readable | the `connectors/` rows cannot be read | mark written without `handle`; "recorded without your handle"; `handle_recorded: false` | exit 0 |
| Prompt via | `commitment done` typed at the prompt | `via=prompt` | exit 0 |
| Project git refuses | the project no longer excludes `memory/` | `git_not_excluded`; nothing written | exit 3 |
| People scope | `--scope people:example` | `scope_refused` | exit 3 |
| Not enrolled | `--scope project:gone` | `not_enrolled`, naming the enrolled projects | exit 3 |
| Malformed | a malformed line names the root, with or without a readable `pending` line | `malformed`, saying to fix or remove that line; nothing decided from the state | exit 3 |
| Unknown replacement id | `show <root>_e9`, unknown to the store and the ledger | `not_found` | exit 3 |
| Not found, ledger unreadable | the id is in no readable ledger and one project's ledger cannot be read | `ledger_unreadable`, naming that project | exit 3 |
| List one unreadable scope | `list --scope project:alpha`, its ledger unreadable | `ledger_unreadable` | exit 3 |
| Approved, not recorded | an approved action item whose `pending` line was never written | `not_found`; "not recorded in the ledger yet"; `next` is `pm-ai proposal approve <id> --scope …` | exit 3 |
| Same id in two ledgers | a hand-copied `pending` line in personal and a project | `show`: one card, `also_in` naming the other scope; verbs: `ambiguous`, saying why | 0 / 3 |
| No start on the line | an open commitment whose `pending` line has no `start`, tied on due with one that has | listed after it; no error | exit 0 |
| Review | `review` at a terminal, three open | compact cards (no command lines, no `--if-version`, no id); `d`, `u` with "friday", `s`; "1 marked done, 1 due set, 1 skipped." and `next:` lines | exit 0 |
| Review unresolved due | `u` then "next sprint" | asked again with the reason | continues |
| Review past date | `u` then "2026-10-20" on 25 Oct | written; "(already past)" | continues |
| Review Enter at due | `u` then Enter | nothing written; counted as skipped | continues |
| Review long note | `b`, then a 300-character note | asked again, stating the 200-character limit | continues |
| Review Ctrl-C in a question | `a`, then Ctrl-C at the note | nothing of that change written or counted; the counts so far | exit 0 |
| Review nothing open | `review` with no open commitment | the empty-listing sentence and its next step | exit 0 |
| Review closed meanwhile | the next card's commitment was marked done from the prompt | skipped with one sentence | continues |
| Review conflict | the commitment gains a mark from the prompt while its card is open | the sentence, then the current card again | continues |
| Review without a terminal | stdin not a TTY | `no_terminal`, naming `commitment list --json` and the verbs | exit 3 |
| Prompt bounds | `commitment list`, `commitment done` typed at the prompt | inline through the daemon, within 1 s and 2 s | exit 0 |
| Prompt review | `commitment review` typed at the prompt | runs in the prompt's process | as at a terminal |
| Completion | `commitment done <Tab>` with one done and two open | the two open ones offered, with `owner: what`, title and due | N/A |
| Completion owner | `commitment owner <id> <Tab>` on a promise said by Sam Rivera | "Sam Rivera" first, then other names in the ledger | N/A |
| Completion meeting | `commitment list --meeting <Tab>` | meeting ids offered, as for `proposal list` | N/A |
| Reject refusal | `proposal reject <id>` of an open approved action item | exit 3; the text names `commitment withdrawn`, `commitment altered` and `proposal edit`; JSON `commitment.commands` holds the three | exit 3 |
| Reject after done | the same, after `commitment done` | `withdrawn` and `altered` not offered; `proposal edit` still named | exit 3 |
| Reject, ledger write failed | approve while the ledger's append fails (`recorded: false`), then `proposal reject` | refused; "not recorded in the ledger yet: pm-ai proposal approve <id> --scope … records it"; `commitment.recorded: false` | exit 3 |
| Approved card | `proposal show <id> --json` | `actions.reject.available: false`, the new reason | exit 0 |
| Dashboard, closed items | approved action items marked done, broken and withdrawn, one fulfilled by a hand-written telemetry `verdict` | none shown under the meeting; a waiting one still shown | N/A |
| Dashboard, altered | an approved action item marked altered | still shown, as approved | N/A |
| Dashboard, edited item | an approved item edited (`<root>_e1`), then marked done | not shown: looked up by its root | N/A |
| Dashboard, ledger unreadable | the ledger cannot be read | items shown as today | N/A |
| Team Activity, withdrawn | a withdrawn action item linked to an issue | hidden, as a rejected one | N/A |
| Team Activity, done and broken | items marked done and broken | still shown | N/A |
| Team Activity, ledger unreadable | the project's ledger cannot be read | every line shown as today | N/A |
| Timed listing | 5,000-line personal and project ledgers | `list` within 1 s | N/A |
| Timed mark | 5,000-line project ledger, git check, one `LedgerChanged` | within 2 s | N/A |

</frozen-after-approval>

## Code Map

Anchors checked against `origin/main` at `689122a`.

- `pm_ai/domain/commitments.py:75` `LineKind`; `:100` `Mark`; `:113` `MarkAnswer`; `:138` `MARKABLE`; `:162` `mark_answer`; `:170` `can_mark`; `:176` `Due`; `:227` `Commitment`
- `pm_ai/domain/lifecycle.py:49` `CommitmentState`, `:66` `is_terminal` (`FULFILLED`, `BROKEN`, `WITHDRAWN`)
- `pm_ai/core/commitment_log.py:94` `NOTE_LIMIT`; `:174` `Marked`; `:187` `DueSet`; `:199` `OwnerSet`; `:239` `render_line`; `:568` `CommitmentLedger` (gains `details`); `:682` `parse_ledger`; `:746` `fold`, `:782-783` the owner lines (the fix); `:858` `CommitmentLog`; `:894` `REASONS`
- `pm_ai/core/due_dates.py:208-212` `_said_year`, `:251-253` its ISO use; `:295` `resolve`
- `pm_ai/core/proposals.py:286` `ACTION_ITEM_TYPE` (`what`, `owner`, `due` editable); `:299` `IMPLICIT_COMMITMENT_TYPE` (no editable field)
- `pm_ai/core/proposal_cards.py:59` `COMMITMENT_NOT_REJECTED`
- `pm_ai/app/commitments.py:82` `ATTEMPTS`; `:102` `CommitmentRecorder`; `:134` `record`; `:236` `recorder_for` (the desk is built beside it)
- `pm_ai/app/proposals.py:102` `PROPOSAL_WRITE_BOUND_SECONDS`; `:574` `choices`; `:822` `_not_rejected`, `:836-840` its text; `:954` `edit`
- `pm_ai/app/meeting_listing.py:396` `resolve_title`
- `pm_ai/app/entry.py:606` `DAEMON_BOUNDS`; `:1874` `_proposal_choices` (the commitment reader goes beside it)
- `pm_ai/app/prompts.py:1146` `_proposals`, the daemon's desk with handed writes
- `pm_ai/app/wiring.py:3532` `read_pm_identity`; `:4151` `append_commitments` in `HANDED_CALLS`; `:4204` `read_commitments` in `PLAIN_READS`
- `pm_ai/app/pipelines.py:2674` `_meeting_digest`; `:2774` `_digests_over`; `:3111` `_item_decisions`, `:3145` a hidden item
- `pm_ai/core/member_updates.py:1000` `ItemDecision`
- `pm_ai/surfaces/cli/dispatch.py:4565` `REVIEW_KEYS`; `:4568` `ProposalDeskPort`; `:4833` `_proposal_review`; `:4888` `_review_one`; `:4963` `TERMINAL_BOUND`; `:5001` `TABLE`, `:5204` its `proposal` group
- `pm_ai/surfaces/cli/completion.py:139` `PROPOSAL_IDS`; `:155` `_BY_COMMAND`; `:566` `proposals_for`
- `tests/architecture/test_domain_invariants.py:165`, `:209` AD-28; `:283` AD-14; `:735` AD-33; `:899` AD-35; `:913` AD-36; `:944` AD-37
- `tests/conftest.py:192` `EXPECTED_SKIPS = 6`
- `tests/slice/test_commitments.py:626` (the refusal text), `:637` (the JSON assertion), `:662` (the card's reason): 15a's assertions, which change; `tests/slice/test_proposals.py:432-433`, which still passes
- `tests/slice/test_line_requests_hardening.py:95` every command declares its bound
- `ARCHITECTURE-SPINE.md:313-319` AD-14; `:371-379` AD-21; `:570-574` AD-37
- `prd.md:673-678` FR-34's consequences; `:963` the 15a Decisions Log entry
- `derivation-services.md:121` the accessors' I/O rule; `:125` the `CommitmentLog` row

## Tasks & Acceptance

**Execution:**
- [ ] **The fold.** `core/commitment_log.py`: `Details` and `CommitmentLedger.details` (version, mark in force, `due_by_pm`, `owner_by_pm`, `changed_at`); the owner rule fixed. Tests first, including the "owner kept after an edit" row.
- [ ] **Undo.** `domain/commitments.py`: `UNDO_WINDOW` and `LineKind.UNDONE`; `core/commitment_log.py`: the `Undone` record, its rendering and parsing, and the fold dropping the undone mark by the three conditions. Tests first: every undo row of the matrix, and file-order swaps.
- [ ] **PM-typed dues.** `core/due_dates.py`: a pure helper naming the form of the words, with `resolve` unchanged; `app/commitments.py`: the same-weekday and end-of-week rule for the PM's words, and the year-written past date.
- [ ] **The card.** `pm_ai/core/commitment_cards.py`: `Card`, `build_card`, `card_text(card, *, compact=False)`, `card_json`, `listing_text`, `listing_json`, `outcome_text`, `refusal_text`.
- [ ] **The desk.** `pm_ai/app/commitments.py`: `CommitmentDesk` with `listing`, `show`, `mark`, `altered`, `set_due`, `set_owner`, `open_walk` and `choices`, the compare-and-append loop, `COMMITMENT_WRITE_BOUND_SECONDS`, the PM's handle through `read_pm_identity`; built in the composition root and, in the daemon, with handed writes.
- [ ] **The CLI.** `surfaces/cli/dispatch.py`: the `commitment` group in `TABLE`, `CommitmentDeskPort`, the handlers, and the `review` walk with compact cards; `review` in `TERMINAL_BOUND`.
- [ ] **Where it runs.** `entry.py`: `DAEMON_BOUNDS` entries; the by-path readers for `list`, `show` and completion.
- [ ] **Completion.** `completion.py`: `COMMITMENT_IDS`, `COMMITMENT_STATE_NAMES`, `COMMITMENT_OWNERS`, and their `_BY_COMMAND` rows.
- [ ] **The reject refusal.** `app/proposals.py:_not_rejected`, its JSON `commitment` field, and `COMMITMENT_NOT_REJECTED`.
- [ ] **The pages:** `_meeting_digest`, `_digests_over`, `_item_decisions`.
- [ ] **Tests.**
  - `tests/core/test_commitment_log.py`: `details`, the owner rule, the version.
  - `tests/core/test_commitment_cards.py` (new): every heading word, past-due rules, the compact form's lines are the full form's first lines, availability from `mark_answer`.
  - `tests/slice/test_commitment_commands.py` (new): one test per matrix row; the two timed tests; the prompt's routing; completion.
  - `tests/architecture/test_domain_invariants.py`: the two new AD-37 tests.
  - **Tests the refusal change breaks,** in `tests/slice/test_commitments.py`: `:626` asserts the old "To correct what was agreed, edit it" sentence (replaced by the new text); `:637` asserts `"pm-ai commitment" not in` the JSON (inverted to assert the `commitment.commands` words, and the test renamed to say the refusal names the commitment commands); `:662` asserts the old card reason (the new `COMMITMENT_NOT_REJECTED`). `tests/slice/test_proposals.py:432-433` asserts only "is a commitment now, so it is not rejected" and passes unchanged.
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
- **2026-10-25, review (three lenses: adversarial, edge-case, verification gap), fixes decided by the coordinating session: frozen block edited (recorded here).** Four behaviour questions are with Andrei and not changed: the pages before 15d, `review`'s `d` key, `due` and `owner` on a closed commitment, and "friday" typed on a Friday.
  - **Concurrency and version:**
    - a retried line's `occurred` is the later of now and the newest line's plus 1 µs;
    - an append that raised after writing is found by its own entry id;
    - the race sentence follows `MARKABLE`: withdrawn then done is written, done then withdrawn refused;
    - versions are compared for equality only;
    - `already` and `unchanged` come before the version for due and owner too, never for `altered`, and a second `altered` is always written;
    - a malformed `--if-version` is exit 2;
    - the `changed` sentence is built for every line kind;
    - busy answers at once, and `ATTEMPTS` counts `LedgerChanged` only.
  - **Due:**
    - the same date over an estimated or said due is written as the PM's;
    - a past date with its year, in any accepted form, is taken as typed before the resolver;
    - blank words are exit 2;
    - `due.said` is kept after a `due_set`.
  - **Altered:**
    - every input is checked before any append;
    - the same wording starts no edit;
    - `edit_refused` carries `wrote` and `next`, including when the bound is too short for the edit;
    - the returned version follows the `revised` line.
  - **Owner:**
    - NFC and folding before the limit and the comparison;
    - blank is exit 2, and a handle or address is refused (`owner_is_handle`);
    - the fold fix orders by `occurred` and counts a cleared owner as a change.
  - **Ids:**
    - `_e<n>` only when known;
    - `malformed` checked first;
    - `ledger_unreadable` over `not_found` when a ledger could not be read, and `list --scope` of an unreadable ledger is exit 3;
    - an approval not yet in the ledger names its repair;
    - a hand-copied id shows one card with `also_in` and refuses verbs as `ambiguous`;
    - a mark without a readable handle is still written and says so;
    - `via=prompt` for every prompt line.
  - **The reject refusal** offers only the marks `mark_answer` allows, and the repair when the root is not in the ledger.
  - **JSON:** `next` and `wrote` on every refusal, `note_recorded` and `handle_recorded` on verbs, and `counts` on `list`.
  - **List and review:**
    - the sort key handles a missing start;
    - `review` re-reads before each card, handles an empty walk, and abandons a change on Ctrl-C inside a question;
    - Enter keeps a value, and an over-limit value is asked again.
  - **Pages:** lookup by root, personal and project ledgers only, fail open; matrix rows for broken, withdrawn, telemetry-fulfilled and altered items, and for Team Activity.
  - **Tests and anchors:**
    - the two-surfaces test forces the interleave;
    - the other-commitment row uses `--if-version`;
    - completion's meeting rows are added;
    - a bounds-values test is added;
    - the AD-14 terminal test is dropped, since `tests/domain/test_commitment_rules.py:47` covers it;
    - the broken assertions are named;
    - `read_pm_identity` is at `wiring.py:3532`;
    - `derivation-services.md:121` gets a stated exception;
    - the PRD Decisions Log entry is anchored at `:963`;
    - AD-37's amendment says equality, already, and the timing of a re-decided line, and qualifies "Terminal states are terminal".
- **2026-10-26, Andrei's four answers: DECIDED 2026-10-26 (Andrei), frozen block edited (recorded here).**
  1. **The pages before 15d: option A.** The digest leaves out commitments marked done, broken or withdrawn; Team Activity hides withdrawn ones, while done and broken stay; altered items are still shown. The Ask First is closed.
  2. **Review's `d` key: "Allow undo for the next 4 hours".** `d` stays one keypress with no confirmation. A new verb, `commitment undo <id>`, writes a new line kind, `undone` (chosen over a `marked` line with an extra field, which a reader that missed the field would take as another done mark). The fold drops the undone mark, so the commitment returns to its state before it.
     - The window is `UNDO_WINDOW` (4 hours), one constant, from the mark's `occurred`.
     - It covers the PM's own done mark only, while it is still the newest line. `b` and `w` ask for a note, so they are no one-keypress accident.
     - It is refused as `undo_expired`, `undo_superseded` or `nothing_to_undo` (a telemetry, broken or withdrawn state).
     - It is offered by the card's `undo` action and the first next step while the window is open, and by `review`'s `z` key after a `d`.
     - AD-14, AD-37 and the PRD's consequence and Decisions Log are amended. Matrix rows added.
  3. **`due` and `owner` on a closed commitment are refused** (`commitment_closed`), naming the way to reopen it that the table allows. The card offers neither action on a closed commitment, and completion offers open ones only. `altered --due` and `--owner` still reopen a broken or withdrawn one.
  4. **The same weekday, typed by the PM:**
     - "friday" typed on a Friday means next week's Friday, 7 days out;
     - "end of week" typed from Friday to Sunday means next week's Friday;
     - `this <weekday>` on that weekday stays unresolved;
     - the output shows the resolved date.

- **2026-10-26, approved for build (Andrei),** after the readiness check, the three-lens review and his four decisions.
     It is a rule in the desk for the PM's typed words. `due_dates` gains only a pure form-naming helper, and `resolve`, 15a's extraction dues and Team Activity are unchanged. Matrix rows added, including one showing that a meeting's own "by Friday" stays unresolved.

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
- A mistaken `broken` or `withdrawn`, or a done mark found after 4 hours, is not undone: it is corrected by a later mark, as the table allows.

**Amendments this depends on.** Applied with the build, in this order: the PRD through bmad-prd, then bmad-spec re-derives the SPEC companion, then the spine through the architecture skill. The sentences are exact.

*PRD (`prd.md`), through bmad-prd (v0.16.1 → v0.16.2):*
- **FR-34 consequences** (`:675`): after "> * Approved spoken commitment creates a valid, structured Markdown entry in the meeting's scope's commitments\_log.md and an index row in commitment\_index.db (Tier 3, disposable) with state \[PENDING\].", add: "> * The PM keeps his commitments from the CLI (2026-10-25, 15c): `pm-ai commitment list` (open ones by default, soonest due first; a date is shown as past only when it was worked out exactly, never on an estimated meeting day), `commitment show <id>`, `commitment done|altered|broken|withdrawn <id>` (his own marks), `commitment due <id> <when>` and `commitment owner <id> <name>` on an open commitment, `commitment undo <id>`, which takes back his own done mark within 4 hours while nothing has followed it (2026-10-26, Andrei), and `commitment review`, which walks the open ones; each change is a line appended to the meeting's scope's commitments\_log.md under his handle, never a rewrite, and acts only on the version he saw when he names one."
- **Decisions Log** (`:963`): after the "> * **2026-10-24 (Story 15a \- v0.16.1):**" entry, add "> * **2026-10-25 (Story 15c \- v0.16.2):** *The PM's commitment commands (2026-10-25, Andrei; FR-34):* `pm-ai commitment` lists, shows, marks (done, altered, broken, withdrawn) and corrects (due, owner) commitments, and walks the open ones; every change is an appended line checked against the version the PM saw; a past date is shown only when it was worked out exactly; the reject refusal names these commands. *Undo, and the PM's typed dues (2026-10-26, Andrei; FR-34):* his own done mark, the one mark given by a single keypress, can be undone within 4 hours while nothing has followed it, returning the commitment to its state before; due and owner change only on an open commitment; a weekday he types on that same weekday means next week's, and the end of the week typed from Friday to Sunday means next week's Friday."

*SPEC companions, re-derived by bmad-spec:*
- **`derivation-services.md:125`:** the `CommitmentLog` row's users become "the approval and its catch-up (append, `15a`), the PM's marks, the due and owner he sets (append, `15c`), `commitment list`, `show` and `review`, the dashboard digest and Team Activity (read, `15c`), the lifecycle job (append verdicts, story 16), indexing (read)".
- **`derivation-services.md:121`** (accessors perform "I/O through `StorageService`"): after that sentence, add "One stated exception reads and never writes: completion, and `commitment list` and `show` at a terminal, read `commitments_log.md` by path, because 4u's completion readers touch no `StorageService`, and parse it with the same `parse_ledger` and `fold` the accessor uses, so the file still has one parser (`15c`)."
- `SPEC.md` itself is unchanged: CAP-34's intent and success already cover a PM-kept ledger, and no capability is added.

*Spine (`ARCHITECTURE-SPINE.md`), through the architecture skill:*
- **AD-14** (`:313-319`):
  - in the heading, `[revised 2026-10-22]` becomes `[revised 2026-10-22, 2026-10-25]`;
  - **Binds:** "FR-33, FR-34" becomes "FR-33, FR-34, `15a`, `15c`";
  - replace "Only `FULFILLED` and `BROKEN` are terminal." with "Only `FULFILLED`, `BROKEN` and `WITHDRAWN` are terminal, and only the PM's own mark moves a commitment out of one `[2026-10-25, 15c]`; the one other way out is his undo of his own `fulfilled` mark, within 4 hours of that mark and while no later line of the commitment follows it, which returns the commitment to the state before the mark and is recorded as a line of its own `[2026-10-26, Andrei, 15c]`."
- **AD-21** (`:371-379`):
  - in the heading, add `2026-10-25` to the revision list;
  - **Binds:** add `15c`;
  - after "and `proposal review` (`13a`), in `TERMINAL_BOUND`", add ", and `[2026-10-25, 15c]` `commitment list` and `commitment show` (`15c`), daemon-routed at the prompt in `DAEMON_BOUNDS`, inline, under 1 second, `commitment done`, `commitment altered`, `commitment broken`, `commitment withdrawn`, `commitment due`, `commitment owner` and `[2026-10-26, Andrei, 15c]` `commitment undo` (`15c`), the same, under 2 seconds, and `commitment review` (`15c`), in `TERMINAL_BOUND`".
- **AD-37** (`:570-574`):
  - in the heading, add `2026-10-25` to the revision list;
  - **Binds:** add `15c`;
  - replace "Terminal states are terminal, and a worker re-checks state **at execution time**" with "Terminal states are terminal — for a commitment, only the PM's own mark moves it out of one, or his undo of his own done mark within 4 hours while nothing has followed it (AD-14) `[2026-10-25, 2026-10-26, Andrei, 15c]` — and a worker re-checks state **at execution time**";
  - after "a ledger that grew is read again, never appended to blindly.", add: "`[2026-10-25, 15c]` A commitment's version is the entry id of its newest line in the file: it changes with every line appended for that commitment, never with another commitment's, and is compared only for equality, since entry ids are hashes and do not move up as a proposal's version does. A surface that displays a commitment carries that version, and a change it sends acts only while the commitment is still at it; a change that finds the PM's same mark, due or owner already in force is answered as already made, not as a conflict; a writer that loses the append reads again and decides again, and the lines it then writes are timed after the newest line it found."

## Verification

**Commands:**
- `uv run pytest tests/core/test_commitment_log.py tests/core/test_commitment_cards.py tests/slice/test_commitment_commands.py tests/slice/test_commitments.py tests/slice/test_proposals.py tests/slice/test_line_requests_hardening.py tests/surfaces/test_cli_completion.py tests/architecture -q` -- expected: every matrix row passes; the two new AD-37 tests pass
- `uv run pytest -q` (in the background, about 7 minutes) -- expected: all pass; 6 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a scratch home (`HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1`), drop and summarise the recorded fixture into personal and approve two items. Then `pm-ai commitment list`, `commitment done <first>`, `commitment due <second> friday`, and `commitment list --state all --json`. Expected: the first is done, the second shows the Friday "set by you", and the scratch `~/.manager-ai/memory/commitments_log.md` gains one `marked` and one `due_set` line.
- In the same home, run `pm-ai commitment review` at a terminal. Expected: compact cards with no command lines, and the closing counts.
