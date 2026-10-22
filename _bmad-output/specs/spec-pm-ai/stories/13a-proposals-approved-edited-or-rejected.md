---
title: 'staged proposals approved, edited or rejected, and expired by the scheduler'
type: 'feature'
created: '2026-10-22'
status: 'draft'
baseline_commit: '8a29acd'
review_loop_iteration: 1
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Since wave 3, a summarised meeting stages its action items as proposals (`7b`), and the dashboard lists them under the meeting (`7d`). Nothing lets the PM act on them. No command lists them, shows one, approves, rejects or edits it. Nothing moves one to `expired` either: the dashboard just stops showing it 7 days after the summary. On his first real use (2026-10-22), Andrei found that the items a meeting agreed would lapse unread.

The `Proposal` entity (AD-13) exists in `pm_ai/domain/proposals.py`, but the parts AD-13 promises around it do not:
- There is no allowed-transition table, so `staged → executed` passes.
- There is no registered type with a payload schema, no card renderer and no expiry sweep.
- `transition_proposal` (`service.py:5716`) reads the row, then writes it back unconditionally, so it is not a compare-and-swap between two processes.
- A re-summary that rewrites a staged item resets its version to 1 (`service.py:5517`), so a version seen before the re-summary can match again afterwards.
- `11b` stages spoken promises with the meeting's start as their creation time (`pipelines.py:1231`), so promises from a transcript dropped a week late start their wait already used up.

**Approach:** One proposal engine, used by every surface.
- **Types.** Each proposal type is registered with a payload schema, the fields a person may edit, a review window (its time to live), and what approval means.
- **Changes.** Every state change is one conditional update of the row in a claimed step (AD-5, AD-37). An edit stages a replacement proposal and supersedes the old one in the same step.
- **Expiry.** The review window starts when pm-ai staged the proposal. The daemon's scheduler moves overdue staged proposals to `expired`, and every reader treats a staged proposal past its window as expired even before the sweep runs.
- **The CLI approval queue.** A new command group, `pm-ai proposal list|show|approve|reject|edit|review`. Its cards come from one surface-neutral renderer, which story 5's Telegram bridge will reuse. Each command has a `--json` form, the exact next commands, and completion at the prompt, so no id has to be copied by hand.
- **The pages.** The dashboard (`7d`) shows each action item as waiting or approved, and counts the ones that expired. Team Activity (`7e`) hides a rejected item and shows an edited one's new wording.
- **Left for 13b.** Running an executor (`approved → executing → executed`).

## Boundaries & Constraints

**Always:**

### Prerequisites and the slices of story 13

- **Built and relied on:** `7b` (staging and supersession, `stage_summary_items`), `7d` (the meeting digests), `7e` (`member_updates.md` and Team Activity), `11b` (the `explicit_command` and `implicit_commitment` proposals that transcript ingestion stages), `11f` (`--title`/`--on` choosing a meeting, the listing's `next`, completion with descriptions), `9a`/`9b` (the daemon's timers in `ticks.py`), `4u` (completion), `4q` (the prompt's routing).
- **Story 13 is split into three slices.** 13a is written in full here; 13b and 13c get specs of their own.
  - **13a, this spec:** the engine, the registry, the CLI queue, the cards, the expiry sweep, the dashboard and Team Activity, for the types pm-ai stages today.
  - **13b, execution:** the worker's `approved → executing → executed` path, the intent → call → outcome record (AD-20), the per-target lock row (AD-37), and how a failed execution is shown. `explicit_command` becomes the first type that runs, through the skill registry `11b` uses. 13b is what story 14 falls back to.
  - **13c, proposals the scheduler raises:** a credential that needs signing in again becomes a card (AD-39) under a registered push occasion (AD-40). It lands with or after story 5, which can push a card.
- **What other stories add.** Story 15 records a commitment for each approved `action_item` and `implicit_commitment`, keyed by the proposal's id, including those approved before 15 exists; the proposal itself stays `approved`. Story 5 renders the same cards on Telegram.
- **CAP-6 is only partly met by 13a.** Its success signal also asks for a `[STAGED_APPROVAL]` entry in `commitments_log.md`. That ledger is story 15's, and the entry is deferred to it.

### The states and the allowed changes

- **The allowed changes are a table in `domain`, and every other change is refused** (`IllegalTransition`):
  - `staged → approved`, `staged → rejected`, `staged → expired`, `staged → superseded`;
  - for every type: `approved → superseded` (an edit);
  - for a type with no executor: `approved → rejected`, since nothing has been carried out;
  - for a type with an executor: `approved → executing` and `approved → expired`. The second is an approval that was not carried out within the window: it never runs (AD-37's eleven-day case). No registered type has an executor in 13a, so the sweep and the store meet these two only in 13b; the architecture tests' sample type has an executor, so they are exercised now;
  - `executing → executed`.
  - `rejected`, `expired`, `superseded` and `executed` are terminal, as `ProposalState.is_terminal` already says.
- **Checks on a change, in this order:**
  1. the proposal does not exist: `ProposalNotFound`;
  2. its state is terminal: `TerminalState`;
  3. its version is not the one expected: `VersionConflict`;
  4. the change is not in the table: `IllegalTransition`.

  So a second approval at an old version is a version conflict, as `tests/slice/test_transcript_slice.py:314` and `tests/architecture/test_domain_invariants.py:785` expect. The engine above the store reloads on a conflict and reports it (see Concurrency).
- **`approved` has one meaning for a type with no executor.** It means the PM agreed. The proposal stays `approved`, with no executor run and no expiry. `executing` and `executed` mean only that pm-ai is carrying out, or has carried out, a change. 13a never writes them.
- **No "done" state** (Andrei, 2026-10-22). An approved item stays approved and is shown. Whether an agreed task has been done is real-world fulfilment, which AD-14 keeps out of the proposal's state. It arrives with story 15 as a commitment state.

### The review window (Andrei, 2026-10-22)

- **14 days for every type that waits for review:** `action_item`, `implicit_commitment` and `explicit_command`. A spoken command needs no different window in 13a, because its approval is unavailable until 13b, which may register a shorter one for commands it can run (see the next bullet).
- **It applies to items already waiting.** A proposal's window is the longer of the time to live stored in its body and the one its type is registered with. A later, shorter registration never shortens a proposal already staged.
- **The window starts when pm-ai staged the proposal, never at the meeting's start.** The body gains `staged_at`. `11b` keeps `created_at` as the meeting's start (its id and citation rely on the meeting), and expiry is `staged_at` plus the window. A body written before 13a has no `staged_at`. For it, the migration sets `staged_at` to `created_at`, which is the only instant it recorded. For `7b`'s items this is the staging instant already. For `11b`'s old promises it is the meeting's start. That is a known limit for rows staged before 13a.
- **Only a waiting proposal expires** (`staged`, or `approved` for a type with an executor). Every reader shows a staged proposal past its window as `expired`: the listing, a card, the dashboard and completion. Approval, rejection and editing check the window inside their own claimed step, against the row they read in that step. A staged proposal past its window is refused, "expired on <date>", whether or not the sweep has reached it. An expired proposal never runs (AD-13).

### Re-staging (`7b`'s re-summary, `11b`'s re-ingestion)

- **A re-stage keeps the stored `created_at`, `staged_at` and time to live.** It rewrites only `summary` and `payload`.
- **The version goes up by one only when `summary` or `payload` changes,** compared as values, not as the whole body. An unchanged re-stage keeps the version and the expiry, and is still reported as staged, as today. Today both re-staging statements reset the version to 1 (`service.py:5517`, `:5681`).
- **A staged row past its window is treated as expired** by `stage_summary_items`, `stage_unless_acted` and the supersession sweep (`service.py:5541`). It is left alone and reported in `left`, never revived and never superseded. The caller hands in the registered windows, so storage decides expiry by the same rule as the readers.
- **`11b`'s ids are positional** (`prp_<meeting>_<source>_<index>`, `pipelines.py:1249`). A re-ingestion that shifts the extractions can put a different extraction at an index:
  - over a staged row, it rewrites the payload and the version goes up, so `--if-version` refuses an action taken on the card shown before;
  - over a row the PM acted on (`left`), the new extraction is dropped. The drop's closing line (`dispatch.py:3741`) says how many new extractions were dropped this way.
- **A re-summary after an edit.**
  - A root the PM's edit superseded is reported as `kept_superseded` and is not staged again (`service.py:5534`).
  - A replacement id is longer than a root id, so the supersession sweep never matches it (`service.py:5543`): a re-summary never touches the PM's edits.
  - A new item whose `what` and `owner` equal, after folding line breaks and runs of spaces and ignoring case, those of a live item of the same meeting (`staged` or `approved`, a replacement included) is not staged. It is reported in `left`.
  - A reworded item is staged. Its card says "may repeat an item you edited: <replacement id>" when the meeting holds a live replacement. *Known limit:* pm-ai cannot tell that a reworded item repeats one the PM edited, so the PM rejects the repeat.

### Registered types

- **The registration:** a `ProposalType` value with:
  - `name`: the stored type;
  - `label`: the card's heading word, such as "Action item";
  - `fields`: the payload schema, one entry per field, each with its name, whether it is required, its length limit, and whether a person may edit it;
  - `ttl`: the review window, 14 days for the three types today;
  - `approval`: what approval means: `agreed` (the whole effect for now), `unavailable` with a reason the card prints, or `executor`, a callback (an empty slot in 13a: 13b fills it, and no registered type uses it yet);
  - `card`: the payload rendered to the card's field lines.
- **Where it lives.** The three declarations live in `pm_ai.core.proposals`, with the pure transition rules. The composition root registers them, and any type a feature adds, at start (AD-30). A feature registers a type and never builds an approval flow (AD-13). A second registration of one name is refused at start. A type never defines its own expiry, which `tests/architecture/test_domain_invariants.py:195` checks.
- **A stored proposal whose type is not registered** is listed and shown. Its `fields` is `{}`, with its stored summary and the sentence "pm-ai does not know this type". Its only allowed change is `staged → rejected`. Approve and edit are refused (exit 3).
- **The three types pm-ai stages today:**
  - `action_item` (`7b`). Fields:
    - `what`: required, at most 200 characters (`WHAT_LIMIT`), editable;
    - `owner`: optional, the speaker label as said, editable. `7b` sets no length for it, since it must be one of the transcript's labels, so 13a sets 80 characters for a typed owner;
    - `due`: optional, as said, at most 40 characters (`DUE_LIMIT`), editable.

    Approval: `agreed`.
  - `implicit_commitment` (`11b`, "I'll … by …"). Fields `what` and `when`, shown from their cleaned text and not editable in 13a. Approval: `agreed`.
  - `explicit_command` (`11b`, a spoken command that came from a hand-dropped transcript or failed one of AD-32's tests). Shown with its verb, target and remainder, none of them editable. Approval: `unavailable`, with the reason "pm-ai cannot carry out a staged command yet; reject it, or make the change yourself".
- **Text typed by the PM is trusted as his own,** like a typed title (`11e`). It is not passed through the prompt-injection filter.
  - Line breaks and other control (Cc) and format (Cf) characters are folded to spaces, and the schema's limits are applied.
  - A value over its limit is refused (exit 2), naming the limit.
  - A `what` that is empty, or only spaces once folded, is a usage error (exit 2).
  - Text that came from a transcript stays as `7b` and `11b` cleaned it.

### Edit

- **What may be edited.** On an `action_item`: `--what`, `--owner`, `--due`, at least one of them. `--owner ""` and `--due ""` clear the field. `what` cannot be cleared.
  - An option for a field the type does not let a person edit is a usage error (exit 2) that names the editable fields.
  - A type with no editable field (`implicit_commitment`, `explicit_command`, an unknown type) is refused (exit 3).
  - An owner is a label. On a project page it resolves to a member by `7e`'s read-time rule: a saved alias, or an exact display name or username.
- **An edit equal to the current values** writes nothing. Its outcome is `unchanged`, exit 0.
- **An edit supersedes (AD-5).** In one claimed step:
  - the old proposal moves `staged → superseded` (or `approved → superseded`), checked by its version;
  - a new proposal is inserted holding the edited payload, with its `summary` recomputed from the edited `what` (its first 80 characters, as `7b` makes it). Its id is `<root>_e<n>`, where `<root>` is the id `7b` minted and `<n>` is one more than the number of replacements already made from that root. Its body names the old proposal (`replaces`);
  - the replacement takes the old proposal's state: an edited waiting item is still waiting, and an edited approved item is still approved, since the PM made the change himself. A staged replacement gets a new `staged_at`, and so a new window.

  If the old proposal's version has changed in the meantime, nothing is written, and the command reloads and reports.
- **`--approve` on `edit`** approves the replacement in the same step, which is the usual "fix it and agree" in one command. Its outcome is `edited_and_approved`.
- **An expired item can be staged again by editing it.** That inserts `<root>_e<n>` as `staged`, with `replaces` naming the expired one. The expired proposal is not changed and never runs. This is the only way back from `expired`.
  - **The insert is conditional:** it runs only if no live proposal (`staged` or `approved`) already has `replaces` naming the expired one. The check and the insert are one statement inside the claimed step, so two attempts cannot both succeed, and no primary-key clash is relied on.
  - A second attempt is refused (exit 3), naming the existing replacement.
- **Editing a `rejected`, `superseded`, `executing` or `executed` proposal** is refused (exit 3). For a superseded one, the refusal names its replacement.

### The store

- **The `proposals` table gains indexed columns, by migration 2** (`MIGRATIONS`, `service.py:437`; `SCHEMA_VERSION` 1 → 2, `:391`): `type`, `scope`, `meeting_id`, `staged_at`, `ttl_seconds`, `replaces`, `changed_at`. Each is filled from the body by the migration, under its own claim (AD-5), and is written with the body from then on. An index on `(state, staged_at)` and one on `meeting_id` serve the listing, the sweep and the dashboard. A body the migration cannot read keeps empty columns, and is counted as unreadable everywhere.
- **The body gains** `staged_at`, `scope`, `replaces`, `changed_at` (the instant of the last change) and `changed_via` (`terminal`, `prompt`, `sweep`, `summary` or `ingestion`; story 5 adds `telegram`). These are read as empty from bodies that lack them. A message about a row with no `changed_at` or `changed_via` drops the "when" or "from where" clause rather than inventing one.
- **Where a proposal's scope comes from.** `7b` and `11b` write `scope` from now on. For a row without it, the migration finds it in one of two ways:
  - an `action_item` id carries its scope's digest (`action_item_prefix`, `meeting_summary.py:542`), which is matched against personal and each enrolled project;
  - an `11b` id carries its meeting, which is looked up among the meeting records of personal and each enrolled project.
- **A proposal whose scope cannot be found fails closed (AD-4).** Nothing proves it is not in a `people` scope, so:
  - it is not listed in a default or scope-filtered listing; `list` counts it in `unscoped`;
  - it is never offered in completion;
  - `show`, `approve`, `reject` and `edit` refuse it (exit 3), "its scope cannot be told";
  - it expires on its own.

### Concurrency

- **Every change is one conditional `UPDATE … WHERE proposal_id = ? AND version = ? AND state = ?`** inside the claimed step's `BEGIN IMMEDIATE`, and checks the number of rows it changed (AD-5's `operational.db` rule, AD-37). This replaces `transition_proposal`'s read-then-write, which keeps its signature and its four refusals above. The SQLite transaction serialises the daemon's writer thread and a client process alike.
- **On zero rows changed, the engine reloads and re-evaluates, never retrying blindly.** In this order:
  1. **Already in the state asked for** (two surfaces approved at once, an approve was sent twice, or `--if-version 1` was sent twice): outcome `already`, "already approved, <when>, from <where>", exit 0. This check comes before any `--if-version` mismatch. Nothing happens twice.
  2. **Moved somewhere else** (expired, rejected, superseded by an edit or a re-summary): refused (exit 3). The refusal names the state, when it changed and from where, and the replacement when there is one. For a re-summary, it names the meeting's `proposal list --scope … --meeting …` command, because the new item's id cannot be told from the old one.
  3. **Same state, other version** (a re-stage changed the payload) with `--if-version`: refused (exit 3), showing the current card. Without `--if-version`, the change is tried once more at the version just read, and the card printed is the one that was approved.
- **`--if-version <n>`** on `approve`, `reject` and `edit` acts only if the proposal is still at version `n`. A wrapper passes the version from the `--json` card it displayed. `review` always does this for the card it showed. Telegram's buttons (story 5) carry the version the same way. A plain `approve` without it prints the card it approved, so the PM sees what he agreed to.
- **The expiry sweep and approval cannot both win.** The sweep moves `staged → expired` by version. Approval moves `staged → approved` by version after checking the window in the same step. Whichever commits first wins, and the other reloads and reports. The worker's `approved → executing` in 13b uses the same rule.
- **Edit against the sweep, a re-summary or another edit:** the old proposal's conditional update and the insert are one transaction. If the update changes no row, the transaction is rolled back, so no replacement is left without the old one having moved.
- **The time a write may wait, and the bound.**
  - In the daemon, a request waits at most 1 second for the writer thread, and SQLite's busy wait is at most 100 ms (`DAEMON_BUSY_WAIT_SECONDS`, `service.py:1389`). So `approve`, `reject` and `edit` get a bound of their own, `PROPOSAL_WRITE_BOUND_SECONDS = 2.0`, declared beside the engine and entered in `DAEMON_BOUNDS`.
  - At a terminal, their connection's busy wait is 1 second, not the standard library's 5, so they answer within the same 2 seconds.
  - Past the wait, the answer is AD-5's existing "busy, try again" refusal (exit 3). Nothing is half-written.

### The expiry sweep (AD-13: the scheduler owns it)

- **A `ProposalSweep` timer sits beside `HarvestTimers` and `RenderTimers`** in `pm_ai/app/ticks.py`, attached in `_lifespan` (`daemon.py:1030`). It is in-memory like them, and `10a` replaces it with a queue row like them. It runs at daemon start, then every 15 minutes, and also at the earliest expiry it saw in its last pass when that comes sooner.
- **Each pass:**
  - one indexed read of the waiting proposals past their window, on its own read-only connection on the I/O pool (AD-19);
  - then claimed steps on the writer thread, at most 200 proposals each. Each step moves `staged → expired` by version, setting `changed_at` to the proposal's expiry instant (not the sweep's own clock) and `changed_via` to `sweep`;
  - a row that changed in between is skipped, and the next pass sees it;
  - a body that cannot be read is counted, never expired, and logged without its content.
- **Types never sweep their own proposals.** A type sets only `ttl`. When the daemon is not running, nothing is swept, and readers still show such proposals as expired.

### The CLI approval queue: `pm-ai proposal`

- **Why `proposal` and these verbs.** It is AD-13's word, and a singular noun, like every other group (`goal`, `member`, `connector`, `transcript`). The verbs are the card's buttons. One group serves every type, so story 14 and later stories add types, never commands.
- **Scopes, for every command here:**
  - `--scope personal|project:<id>` limits a listing, or checks on a verb that the proposal is in that scope;
  - `people:` is refused (exit 3), and no proposal in a `people` scope is listed or offered (AD-4);
  - a `project:` that is not enrolled is refused (exit 3), naming the enrolled projects;
  - `--meeting <id>` without `--scope`, for a meeting with records in more than one scope, is refused (exit 3), listing the scopes;
  - `--title <words> [--on <date>]` without `--scope` searches personal and every enrolled project through `11f`'s `resolve_title` (`meeting_listing.py:460`). It chooses exactly one meeting across them, or is refused with the candidates;
  - every command pm-ai prints includes `--scope`.
- **`proposal list [--scope …] [--meeting <id> | --title <words> [--on <date>]] [--type <name>] [--state staged|approved|rejected|expired|superseded|all] [--limit 1..200] [--json]`.**
  - `--state` defaults to `staged`, the proposals waiting for the PM. `staged` means staged and within its window. `expired` includes staged proposals past their window that the sweep has not moved yet. `all` includes `executing` and `executed`.
  - Without `--scope`, it covers personal and every enrolled project. A project that cannot be read is named on one line, and the others are still listed.
  - **Order:**
    - by meeting, newest start first;
    - within a meeting, in the order of the meeting's recorded summary, with a replacement in its root's place, then the others by staging time;
    - proposals with no meeting, or whose meeting record cannot be read ("meeting record unavailable"), come last, by staging time;
    - any tie is broken by staging time, then by id.
  - `--limit` defaults to 20, and the number left out is stated.
  - **It reads no more bodies than it shows.** Selecting and ordering use the indexed columns and `LIMIT`. Only the bodies of the cards printed, and the meeting records they name, are parsed, so a large store stays within the 1-second bound. A timed test measures this.
  - **The text form** is one card per proposal (below). Its last lines say how many proposals could not be read and how many have no known scope, when there are any. Then comes the exact next command: `pm-ai proposal review` with the same filters when anything is waiting, and otherwise the filter that would show more. An empty listing is one sentence that names the filter and the scopes looked at.
- **`proposal show <id> [--scope …] [--json]`** shows one card, in any state, including where it came from and, for an edit, what it replaced and what replaced it.
- **`proposal approve <id>`**, **`proposal reject <id>`** and **`proposal edit <id> [--what <text>] [--owner <text>] [--due <text>] [--approve]`** each also take `[--scope …] [--if-version <n>] [--json]`.
  - Each asks nothing.
  - Each prints the card after the change, the outcome in one sentence, and the next step: the next waiting proposal of the same meeting, if any, and otherwise `proposal list --scope …`.
- **`proposal review [--scope …] [--meeting <id> | --title <words> [--on <date>]]`** walks the waiting proposals in `list`'s order, one card at a time.
  - **The keys:** `[a]pprove [e]dit [r]eject [s]kip [q]uit`.
  - **Edit** asks for each editable field with its current value shown. Enter keeps the value, and `-` clears an optional field. Then it shows the replacement's card and asks "approve it? [Y/n]". An edit equal to the current values writes nothing.
  - **Each answer** is one change, checked against the version its card showed. On a conflict, the current card is shown again with one sentence saying what changed.
  - **The end:** one line counts what was approved, rejected, edited and skipped, and gives the next step. Quitting, Ctrl-C and end of input end the walk the same way. They print the counts so far and exit 0. Every change already answered is complete, and nothing is half-written.
  - `review` is the only command here that asks. It is something the PM chose to start, not the normal path, and every answer can also be given by `approve`, `reject` or `edit`.
  - Without a terminal (stdin not a TTY), it is refused (exit 3), naming `proposal list --json` and the three verbs.
- **Choosing without an id.**
  - At the prompt, the `<id>` of `show`, `approve`, `reject` and `edit` completes from the proposals that verb can act on: waiting ones for `approve`, waiting and approved ones for `reject` and `edit` (expired ones too for `edit`), and any for `show`. At most 100 are offered, newest meeting first.
  - Proposals with no known scope, or with a body that cannot be read, are never offered.
  - Each candidate inserts the id and shows `owner: what` and the meeting's title and start beside it. Typed text matches the start of the id, or the start of any word of `what` or of the title, ignoring case. This reuses `11f`'s description column and matching (`completion.py:309`).
  - The reader follows 4u's rules: no `StorageService`, no master key, keychain or daemon, and nothing offered on failure.
  - Every card prints its exact commands, and `review` needs no id at all.
  - The id stays the only way to name a proposal on the command line, so a wrapper always acts on the proposal it showed (`11f`'s reasoning).
- **A proposal whose body cannot be read** is counted in `list`, refused (exit 3) by `show`, `approve`, `reject` and `edit`, and skipped by completion.
- **Exit codes:**
  - **0:** the change was made, it was already made (`already`), nothing needed changing (`unchanged`), or there was something to report, an empty listing included;
  - **2:** a usage error: an unknown state, a limit outside 1 to 200, `edit` with no field, a field the type does not let a person edit, a value over its limit, an empty `what`;
  - **3:** a refusal: not found, unreadable, a terminal state, past its window, approval unavailable for the type, a type with no editable field, a version mismatch, a `people` or unknown scope, a scope that is not enrolled, an ambiguous meeting, a second re-stage of an expired item, no terminal for `review`, busy.

### The card (one renderer for every surface, AD-13)

- **One function in `pm_ai/core` turns a proposal and its registered type into a `Card` value.** It needs the meeting's title and start, the clock and the display zone. It does no I/O. A `Card` holds:
  - **the heading:** the type's label, the state in plain words, and for a waiting proposal "until <date>". The words are "waiting for you", "approved", "rejected", "expired, not reviewed", "replaced", "being carried out" and "carried out";
  - **the field lines** from the type's `card`, plus "may repeat an item you edited" when that applies;
  - **where it came from:** the meeting's title, start and scope; "meeting record unavailable" when the record cannot be read; or the citation when there is no meeting;
  - **for an edit,** what it replaced;
  - **its actions,** each with a name (`approve`, `edit`, `reject`, `show`), whether it is available (and why not), and the exact command's words, including `--scope` and `--if-version`. An `executing` or `executed` card offers only `show`.
- **The renderers:** text and JSON for the CLI in 13a, and an inline keyboard from the same actions for Telegram in story 5. A surface never decides availability itself.
- **What the text card looks like, for a waiting action item:**

  ```
  Action item, waiting for you until Tue 4 Nov
    Sam Rivera: send the cache TTL numbers (Thursday)
    from Example Weekly Sync-up, Tue 21 Oct 10:00, personal
    approve: pm-ai proposal approve prp_… --scope personal --if-version 1
    edit:    pm-ai proposal edit prp_… --scope personal --what "…" --owner "…" --due "…" --if-version 1
    reject:  pm-ai proposal reject prp_… --scope personal --if-version 1
  ```

  An approved card says "approved on <date>" and offers `edit` and `reject`. An expired one says "expired on <date>, not reviewed" and offers `edit`, to stage it again. Text from a transcript has its line breaks folded and is escaped, as `7d` does.
- **The machine form, `--json`:** one object on standard output and nothing else, exit codes as in the text form.
  - **`list`:** `{"format": 1, "scopes", "unreadable": [{"scope", "reason"}], "unreadable_proposals", "unscoped", "more", "next": {"review": [words]|null, "more": [words]|null}, "proposals": [card, …]}`.
  - **`show`, `approve`, `reject` and `edit`:** `{"format": 1, "outcome", "proposal": card, "replaced": card|null, "next": [words]|null}`. `outcome` is one of `shown`, `approved`, `rejected`, `edited`, `edited_and_approved`, `unchanged`, `already`.
  - **A refusal (exit 3):** `{"format": 1, "outcome": "refused", "reason", "state"|null, "changed_at"|null, "changed_via"|null, "replaced_by"|null, "proposal": card|null}` on standard output. A usage error (exit 2) stays a message on standard error, as for every command.
  - **A card:** `{"proposal_id", "type", "label", "state", "version", "scope", "meeting": {"meeting_id", "title"|null, "start"|null, "record": "read"|"unavailable"}|null, "fields": {name: value|null}, "created_at", "staged_at", "expires_at"|null, "changed_at"|null, "changed_via"|null, "replaces"|null, "replaced_by"|null, "may_repeat"|null, "actions": {name: {"available", "reason"|null, "command": [words]|null}}}`.
  - **Values in a card:**
    - `state` is the store's word, with a staged proposal past its window given as `expired`;
    - instants are ISO 8601 UTC;
    - `fields` are the type's own, `{}` for an unknown type, and for `11b`'s types they are the cleaned text, never the raw;
    - `expires_at` is set only while the proposal is waiting.
  - A field may be added later without changing `format`. Removing or renaming one changes it.

### What the dashboard shows (`7d`)

- **Under each meeting, in this order:**
  1. waiting items, in the summary's order, as `owner: what (due), to review by <date>`;
  2. approved items, in the summary's order, as `owner: what (due), approved`;
  3. a replacement in its root's place, with its edited values.

  Rejected and superseded items are not shown.
- **At most 5 items** (`MAX_DIGEST_ITEMS`). Approved items are dropped before waiting ones. The overflow line gives the count and names `pm-ai proposal list --scope <scope> --meeting <id>`.
- **After the items:**
  - a line counting the items that expired without review and have no replacement, naming `pm-ai proposal list --scope <scope> --meeting <id> --state expired`;
  - when anything is waiting, a line naming `pm-ai proposal review --scope <scope> --meeting <id>`.
- **Root ids decide placement and withholding.** An item counts as shown under an issue, and as linked to a confidential issue, when its root is. Both `shown_elsewhere` and `confidential` are looked up by the root id (`pipelines.py:2665`, `:2674`; built at `member_updates.py:1101-1109`). An edited item on a confidential issue, or on any linked issue when the GitLab record cannot be read, still shows only "an action item on a confidential issue".
- **The read** (`open_action_items`, `service.py:5178`) returns:
  - the meeting's waiting and approved `action_item` proposals, root ids and replacement ids alike (the prefix, 12 hex characters, then optionally `_e<n>`);
  - the count of expired items without a replacement.

  It stays a plain read on a read-only connection, by the `meeting_id` index. "Open action item" in the page's wording now means waiting or approved.

### What Team Activity shows (`7e`)

- **The PM's decision is joined in at read time. Nothing is appended to `member_updates.md`.** The render reads, in one read-only query, the current state of each action-item root on the page and of its newest replacement. Then:
  - a line whose item, or whose newest replacement, is `rejected` is hidden;
  - a line whose item was replaced by an edit shows the replacement's `what`, `owner` and `due`, under the same issue, with the same link kind, and with the PM's confirm or reject of that link still applying;
  - waiting, approved and expired items are shown as they are today. A line is a note of what the meeting said about the issue, and its 14-day window (`UPDATE_WINDOW`) bounds it;
  - a re-summary's `withdrawn` lines work as today.
- **Where an edited line sits.** Team Activity places a row under its issue, wherever that issue appears: under each GitLab assignee, or under Unassigned (`team_activity.py:169`). An owner changed by an edit changes the name shown in the row, never which member's section holds it.
- **If the proposals cannot be read,** the page says so in one line and shows the lines as recorded, the same fail-open as today's issue notes. Confidential-issue withholding fails closed, as now.
- **`member unmatched` and `member link` are unchanged.** They work on update ids, which do not change, because the ledger is not rewritten.

### Where it runs (AD-21)

- **At a terminal:**
  - `proposal list` and `proposal show` compose nothing (`composes=False`, as `transcript list`). They read the store on a read-only connection and the meeting records by path, and ask for no master key or keychain. `operational.db` and the meeting records are not encrypted;
  - `approve`, `reject` and `edit` are composed as `member link` is. They write one claimed step to `operational.db` with a 1-second busy wait, and ask for no master key.
- **At the prompt:**
  - `list` and `show` join `DAEMON_BOUNDS` (`entry.py:593`) with `CONFIG_SHOW_BOUND_SECONDS`, 1 second;
  - `approve`, `reject` and `edit` join it with `PROPOSAL_WRITE_BOUND_SECONDS`, 2 seconds;
  - they run inline through the daemon and print exactly what the terminal prints.
- **`proposal review`** joins `TERMINAL_BOUND` (`dispatch.py:4518`), so at the prompt it runs in the prompt's own process (`repl.py:127`), and each answer is written by the same function.
- The spine amendments are in Design Notes.

### The next step offered elsewhere

- `transcript summarize`'s outcome sentence (`pipelines.py:3982`) and `transcript list`'s `next` (`11f`) name `pm-ai proposal review --scope <scope> --meeting <id>` when the meeting has items waiting. In JSON, this is a new `next.review`, in `transcript list` and in `transcript drop --json`'s `next` (`tests/surfaces/test_cli_subcommands.py:1567`).

### No real data

Tests use `11d`'s fixture names (`Example Weekly Sync-up`, `Alex Morgan`, `Sam Rivera`) and `example.com` handles. No real meeting, name or item text appears.

**Never:**

- No external system changes. No executor runs, and no proposal enters `executing` or `executed` in 13a.
- No write to `commitments_log.md` or `event_log/`. CAP-6's `[STAGED_APPROVAL]` entry belongs to story 15's ledger.
- No change the domain table does not allow. No blind retry. No version reset.
- No expiry measured from a meeting's start for a proposal staged by 13a's code. No staged proposal past its window approved, rejected, revived or superseded. No expiry sweep in a feature.
- No edit applied to the old proposal in place. No re-summary superseding a PM's replacement.
- No line rewritten or appended in `member_updates.md` by an approval decision.
- No `people` scope read, listed or offered. No proposal whose scope is unknown listed or offered.
- No proposal chosen by position or "latest".
- No question asked by any command except `review`.
- No master key or keychain asked for. No model call.
- No change to `7b`'s ids, its summary record, or its `left` and `superseded` rules, beyond the version, expiry and repeat rules above.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Waiting items listed | a personal meeting summarised, 3 items staged | `proposal list`: 3 cards in the summary's order, each "waiting for you until <date 14 days on>"; last line `pm-ai proposal review --scope personal …` | exit 0 |
| JSON listing | the same, `--json` | one object, `format: 1`, 3 cards, each with `actions.approve.command` including `--scope personal` and `--if-version 1`; `next.review` set | exit 0 |
| Approve | `proposal approve <id>` | the approved card printed; `changed_via: terminal`; the dashboard's next render shows it as approved | exit 0 |
| Approve twice | approve the same id again | "already approved, <when>, from the terminal"; nothing changes | exit 0 |
| Approve twice with a version | `approve <id> --if-version 1`, twice | the second: `already`, not a version mismatch | both exit 0 |
| Two surfaces | the terminal and the prompt approve one id at once | one change; the other prints "already approved … from the prompt" | both exit 0 |
| Two processes | two processes approve one id, each within its busy wait | the row changes once; version + 1 | both exit 0 |
| Approve vs sweep | the sweep commits `expired` first | approve refused: "expired on <date>, not reviewed"; offers `edit` to stage it again | exit 3 |
| Past the window, no daemon | staged, window passed, daemon never ran | listed as expired; approve and reject refused, "expired on <date>" | exit 3 |
| Promise from a late drop | a transcript dropped 10 days after the meeting stages a promise | waiting, until 14 days after the drop | exit 0 |
| Item already waiting | staged before 13a with a 7-day window, 8 days ago | waiting under the 14-day window | N/A |
| Old promise row | an `11b` promise staged before 13a, its meeting 20 days ago | `staged_at` set to the meeting's start by the migration; shown as expired (known limit) | N/A |
| Reject | `proposal reject <id>` on a waiting item | rejected; gone from the dashboard; its Team Activity line hidden | exit 0 |
| Reject an approved action item | approved `action_item` | rejected (a type with no executor) | exit 0 |
| Edit | `proposal edit <id> --due "Friday"` | old superseded; `<root>_e1` staged with the new due, a new window, `replaces` set; the dashboard and Team Activity show "Friday" in its place | exit 0 |
| Edit, same values | `edit <id> --due "Thursday"` when the due is already Thursday | outcome `unchanged`; nothing written | exit 0 |
| Edit and approve | `edit … --what "…" --approve` | the replacement approved in the same step; outcome `edited_and_approved`; summary from the new `what` | exit 0 |
| Edit an approved item | approved item, `--owner "Alex Morgan"` | old superseded; replacement approved; the project page resolves the owner by exact member name; the Team Activity row stays under its issue's assignee | exit 0 |
| Edit an expired item | expired item, `--due "next week"` | `<root>_e1` staged; the expired one untouched; card "waiting for you" | exit 0 |
| Re-stage an expired item twice | two edits of one expired item | one replacement; the second refused, naming it | exit 3 |
| Edit twice at once | two edits of one waiting item | one replacement; the other reloads: "replaced by <id>" | exit 3 for the loser |
| Edit with no field | `proposal edit <id>` | usage error naming `--what`, `--owner`, `--due` | exit 2 |
| Field not editable | `edit <promise id> --what "…"` | refused: this type has no editable field | exit 3 |
| Over the limit | `--what` of 201 characters | refused, naming 200 | exit 2 |
| Empty what | `--what ""`, or one holding only a format character | usage error: `what` is required | exit 2 |
| Stale version | `approve <id> --if-version 1` after a re-summary changed the due (version 2) | refused, showing the current card at version 2 | exit 3 |
| Unchanged re-summary | `transcript summarize --again`, the same items | versions and expiries unchanged | exit 0 |
| Re-summary changes a due | a staged item's due changes, id unchanged | payload rewritten; version 1 → 2, once; `staged_at` and window kept | N/A |
| Re-summary over an unswept expired row | a staged row past its window; the re-summary produces it again | left expired, reported in `left`, not revived or superseded | N/A |
| Re-summary keeps an edit | the item edited, then `--again` | root kept superseded; `_e1` untouched; no duplicate on the dashboard | exit 0 |
| Re-summary repeats an edit | the new summary's item equals the replacement's `what` and `owner` | not staged; reported in `left` | exit 0 |
| Re-summary rewords an edited item | a new wording of an item the PM edited | staged; its card "may repeat an item you edited: <id>" | exit 0 |
| Re-summary vs approve | the re-summary supersedes a waiting item as the PM approves it | one wins; if the re-summary won, approve is refused naming the meeting's `proposal list` | exit 3 |
| Re-ingestion shifts an index | an `11b` re-ingestion puts another extraction at a staged index | payload rewritten, version up; an old `--if-version` refused | exit 3 |
| Re-ingestion over an acted row | the same, at an index the PM approved | the new extraction dropped; the drop's line counts it | exit 0 |
| Sweep | 3 items past their window, daemon running | within 15 minutes all `expired`, `changed_at` at their expiry, `changed_via: sweep`; the dashboard counts "3 expired without review" | N/A |
| Sweep, unreadable body | one staged body does not parse | counted and logged, not expired; the others expire | N/A |
| Staged command | an `explicit_command` from a dropped transcript | card shows the verb and target; approve unavailable, with its reason; reject works | approve exit 3 |
| Spoken promise | an `implicit_commitment` | approve: approved | exit 0 |
| Unknown type | a stored type no registration names | listed with its stored summary, `fields: {}`, "pm-ai does not know this type"; only reject offered | approve and edit exit 3 |
| Unreadable proposal | a body that does not parse | `list` counts it in `unreadable_proposals`; `show` refused; not offered in completion | `show` exit 3 |
| Unknown scope | an `11b` proposal whose meeting record is in no listed scope | not listed (counted in `unscoped`); not offered; `show` refused | exit 3 |
| Meeting in two scopes | `proposal list --meeting <id>` with records in personal and `project:alpha` | refused, listing both scopes | exit 3 |
| Title across scopes | `proposal review --title weekly`, no `--scope`, one match in `project:alpha` | walks that meeting's items | exit 0 |
| Ambiguous title | the same, a match in personal and in `project:alpha` | refused, listing both candidates | exit 3 |
| Project not enrolled | `--scope project:nope` | refused, naming the enrolled projects | exit 3 |
| Review | `proposal review --title weekly` at a terminal | walks 3 cards; `a`, `e` (due changed, then `Y`), `s`; final line "1 approved, 1 edited and approved, 1 skipped" | exit 0 |
| Review clears a field | `e`, then `-` at the owner | the replacement has no owner | continues |
| Review interrupted | Ctrl-C or end of input on the second card | the counts so far printed; the first answer complete; nothing half-written | exit 0 |
| Review conflict | an item expires while its card is open | the answer refused; the current card shown with "expired on <date>" | continues |
| Review without a terminal | stdin not a TTY | refused, naming `proposal list --json` and the verbs | exit 3 |
| Refusal in JSON | `approve <expired id> --json` | `{"outcome": "refused", "reason", "state": "expired", …}` on standard output | exit 3 |
| Completion | `pm-ai> proposal approve cache<Tab>` | offers the waiting item whose `what` has a word starting "cache", showing its owner, text and meeting | N/A |
| Dashboard order and cap | 4 waiting and 3 approved items | the 4 waiting, then 1 approved; "2 more: `pm-ai proposal list --scope … --meeting …`" | N/A |
| Confidential edit | an edited item linked to a confidential issue; and again with the GitLab record unreadable | "an action item on a confidential issue" both times | N/A |
| People | `--scope people:example` | refused (AD-4) | exit 3 |
| Bad state | `--state done` | usage error naming the six words | exit 2 |
| Not found | `proposal show prp_nope` | refused: no proposal under that id | exit 3 |
| Old row, no change time | a row staged before 13a, then approved elsewhere | "already approved" without a "when" or "from where" clause | exit 0 |
| Large store | 5,000 proposals across 300 meetings | `proposal list` within 1 second, measured | exit 0 |
| Prompt | `pm-ai> proposal list` | the same text as at the terminal, inline, within 1 second | N/A |
| Summarize's next step | a summary stages 2 items | its sentence ends with the `proposal review` command; `transcript list --json` has `next.review` | exit 0 |
| Hostile text | an item whose `what` holds a newline and a control character | folded to one line on the card; escaped in JSON | exit 0 |

</frozen-after-approval>

## Code Map

Anchors checked against `origin/main` at `8a29acd`.

- `pm_ai/domain/proposals.py:21` -- `Proposal` (no `staged_at`, `scope`, `replaces`, `changed_at`, `changed_via` yet); `:37` `transition`, which checks the version, then terminality, and no edge table; `:53` `OpenActionItems`
- `pm_ai/domain/lifecycle.py:28` -- `ProposalState`, `is_terminal`; `:76` the AD-14 disjointness check; `:92` `DEFAULT_PROPOSAL_TTL`
- `pm_ai/domain/meeting_summaries.py:62` `WHAT_LIMIT`; `:65` `DUE_LIMIT`; `:71` `ACTION_ITEM`; `:118` `SummaryActionItem`
- `pm_ai/core/extraction.py:51` `EXPLICIT_COMMAND`; `:52` `IMPLICIT_COMMITMENT`; `:94`, `:113` their payloads
- `pm_ai/core/meeting_summary.py:542` -- `action_item_prefix` (`prp_<scope digest>_<meeting>_ai_`); `:547` `action_item_id`
- `pm_ai/storage/service.py:217` -- the `proposals` table; `:391` `SCHEMA_VERSION`; `:437` `MIGRATIONS`; `:718` `_dump_proposal`, `:738` `_load_proposal`; `:1038` `ProposalNotFound`; `:1389` `DAEMON_BUSY_WAIT_SECONDS`; `:1602` `_ITEM_DIGEST`, `:1603` `_HEX12`; `:5178` `open_action_items`; `:5495` `stage_summary_items` (`:5517` the version reset, `:5534` `kept_superseded`, `:5541` the supersession sweep, `:5543` its exact-length match); `:5669` `stage_unless_acted` (`:5681` the same reset); `:5695` `stage_proposal`; `:5704` `load_proposal`; `:5716` `transition_proposal`, a read and an unconditional write
- `pm_ai/ports/__init__.py:849` -- `open_action_items` on the port
- `pm_ai/app/wiring.py:4074` `HANDED_CALLS` (`:4105` `stage_unless_acted`, `:4127` `stage_proposal`, `:4128` `transition_proposal`, `:4138` `load_proposal`); `:4152` `PLAIN_READS` (`:4162` `open_action_items`); `:4187` `NEVER_FROM_A_JOB`
- `pm_ai/app/pipelines.py:1149` -- `run_transcript_ingestion`; `:1231` `created_at=meeting.start`; `:1234` its staging; `:1249` `_proposal_id`, positional; `:2628` `_meeting_digest`, which reads open items (`:2647`), skips those shown under an issue (`:2665`) and withholds confidential text (`:2674`); `:2780` `project_meeting_digests`; `:3896` `_written`, which stages items (`:3961`); `:3982` the outcome sentence
- `pm_ai/core/rendering.py:199` `MAX_DIGEST_ITEMS`; `:976` the item lines; `:1018` the meeting's command line
- `pm_ai/domain/renders.py:83` `DigestItem`; `:117` `MeetingDigest`
- `pm_ai/core/member_updates.py:125` `UPDATE_WINDOW`; `:744` `Ledger`; `:800` `parse_ledger`; `:1060` `issue_notes_for`, where the read-time join goes; `:1101-1109` the confidential item ids
- `pm_ai/core/team_activity.py:169` -- `IssueNotes`: rows placed under each issue, wherever its assignees put it
- `pm_ai/app/ticks.py:209` `HarvestTimers`; `:695` `RenderTimers`; `pm_ai/app/daemon.py:1030` `_lifespan`, `:639`/`:643` where the timers attach
- `pm_ai/surfaces/cli/dispatch.py:1184` `Command`; `:2235` `SCOPE_ARGUMENT`; `:3355` `MEETING_ARGUMENT`; `:3358` `TITLE_OPTION`; `:3370` `JSON_FLAG`; `:3482` `ON_OPTION`; `:3741` the drop's `left` sentence; `:4200` `STATE_OPTION`, `:4201` `LIMIT_OPTION`; `:4251` `_transcript_list`, the listing precedent; `:4489` `_member_link`, the composed-write precedent; `:4518` `TERMINAL_BOUND`; `:4554` `TABLE`, where `proposal` joins
- `pm_ai/surfaces/cli/repl.py:127` `runs_here`
- `pm_ai/app/entry.py:593` `DAEMON_BOUNDS`; `:1943` `_meeting_ids`, the completion reader precedent
- `pm_ai/surfaces/cli/completion.py:95` `FREE_TEXT_NAMES`; `:131` `_BY_COMMAND`; `:194` `Readers`; `:309` `Candidate`
- `pm_ai/app/meeting_listing.py:460` `resolve_title`; `pm_ai/domain/meeting_listing.py:137` `NextStep`
- `tests/architecture/test_domain_invariants.py:195` -- types never define expiry (`:203` compares `ttl > 0`, which a `timedelta` cannot); `:780` two surfaces approving; `:790` an expired proposal never executes. All three skip today on the missing `pm_ai.core.proposals`.
- `tests/slice/test_transcript_slice.py:312-326`, `:370`; `tests/slice/test_transcript_path.py:1203`, `:1252`, `:1491`; `tests/slice/test_meeting_summary_slice.py:679`; `tests/slice/test_member_updates_slice.py:518` -- callers of `transition_proposal` and its refusals
- `tests/core/goldens/dashboard_with_summaries.md`; `tests/core/test_rendering_sections.py:2017`, `:2144`; `tests/slice/test_dashboard_slice.py:2306`, `:2333`, `:2342` -- the dashboard's item lines, golden and cap
- `tests/surfaces/test_cli_subcommands.py:1551-1571` -- `transcript drop --json`'s `next`
- `ARCHITECTURE-SPINE.md:305` AD-13; `:311` AD-14; `:369` AD-21; `:568` AD-37

## Tasks & Acceptance

**Execution:**
- [ ] **One model.** `pm_ai/domain/proposals.py`, `lifecycle.py`, a new `pm_ai/core/proposals.py`:
  - the edge table and `IllegalTransition`, checked after `ProposalNotFound`, `TerminalState` and `VersionConflict`, in that order;
  - `ProposalType`, its field schema, and the three declarations;
  - the new body fields;
  - expiry from `staged_at` and the longer window;
  - the names the architecture tests call: `registered_types()`, `approve`, `expire`, `execute`, `VersionConflict`, `TerminalState`, `sample_staged`, `sample_approved`. These are pure functions over an in-memory book that applies the same rules as the store. `execute` re-checks the state at execution time, and in 13a runs only the sample type's executor.
- [ ] **Every change a compare-and-swap.** `pm_ai/storage/service.py`:
  - migration 2 (the columns, their backfill and the indexes);
  - one conditional-update transition, keeping `transition_proposal`'s signature;
  - the edit step (supersede and insert) and the conditional re-stage of an expired item;
  - the sweep step;
  - re-staging that keeps `staged_at` and the window, bumps the version only on a changed `summary` or `payload`, leaves expired rows and repeats in `left`;
  - `open_action_items` with replacements, approved items and the expired count;
  - indexed reads for the listing, the sweep and Team Activity on a read-only connection;
  - the 1-second busy wait for these writes at a terminal.
- [ ] **Every new storage call declared.** `pm_ai/app/wiring.py`: each new public storage call in `HANDED_CALLS` or `PLAIN_READS` (`:4074`, `:4152`) and, where a job must not make it, `NEVER_FROM_A_JOB` (`:4187`); `transition_proposal`'s entry at `:4128` kept.
- [ ] **One engine.** `pm_ai/app/` (a new `proposals.py`):
  - the registry, built in the composition root;
  - list, show, approve, reject and edit as one function each, for the terminal, the daemon and `review`;
  - the reload-and-report rules;
  - the scope rules;
  - `PROPOSAL_WRITE_BOUND_SECONDS`.
- [ ] **One card.** `pm_ai/core/` (a new `proposal_cards.py`): `Card`, its actions, and the text, JSON and refusal renderers.
- [ ] **The scheduler owns expiry.** `pm_ai/app/ticks.py`, `daemon.py`: `ProposalSweep`.
- [ ] **The pages.** `pm_ai/app/pipelines.py`, `pm_ai/core/rendering.py`, `pm_ai/domain/renders.py`, `pm_ai/core/member_updates.py`:
  - the waiting and approved lines, their order and cap, the expired and review lines, and lookups by root id;
  - Team Activity's read-time join;
  - summarize's next step;
  - `7b` and `11b` write `scope` and `staged_at`;
  - the drop's count of dropped extractions.
- [ ] **The queue.** `pm_ai/surfaces/cli/dispatch.py`, `completion.py`, `pm_ai/app/entry.py`, `pm_ai/app/meeting_listing.py`: the `proposal` group, the `DAEMON_BOUNDS` and `TERMINAL_BOUND` entries, completion of ids, `next.review`.
- [ ] **New tests.** `tests/slice/test_proposals.py`, `tests/slice/test_proposal_sweep.py`: one test per matrix row, plus two processes approving one id within their busy wait, and a timed listing over 5,000 proposals.
- [ ] **Existing tests that change.**
  - `tests/architecture/test_domain_invariants.py`: `:203` compares the window with `timedelta(0)`; `:195`, `:780` and `:790` stop skipping and pass.
  - The `transition_proposal` tests named in the Code Map keep passing unchanged. `TerminalState` is still raised first for a terminal source, and the signature is kept.
  - The dashboard tests and golden: `test_dashboard_slice.py:2306` now expects the approved item shown as approved, and its expired fixture (8 days old, 7-day window) to be staged 15 days ago, since it is waiting under 14 days. `:2342`'s and `test_rendering_sections.py:2144`'s overflow line names `proposal list`. The golden is re-recorded with the new item lines.
  - `tests/surfaces/test_cli_subcommands.py:1567`: `next` gains `review`.
  - The existing completion, bound and reachability tests take the new commands.
- [ ] **The spine.** `ARCHITECTURE-SPINE.md`: the amendments in Design Notes, re-derived through the architecture skill.

**Acceptance Criteria:**
- Given a summarised fixture meeting with 3 staged items, when `pm-ai proposal list --json` runs with no daemon, then it prints one object with 3 cards whose `actions.approve.command` runs as printed, and nothing is written.
- Given one waiting item, when two processes run `proposal approve` on it at the same moment, then the row changes once, its version moves up by one, and both exit 0 within their busy wait.
- Given a staged item past its window, when `proposal approve` runs before any sweep, then it is refused with exit 3 and the row is unchanged; and when the daemon's sweep runs, then the row is `expired` with `changed_via: sweep`.
- Given a transcript dropped 10 days after its meeting, when its promise is listed, then it is waiting, until 14 days after the drop.
- Given an edited item, when the meeting is re-summarised with `--again`, then the replacement is unchanged and the dashboard lists the item once, with the edited text.
- Given the change, when `uv run pytest` runs, then everything passes; `test_domain_invariants.py`'s three proposal tests (`:195`, `:780`, `:790`) turn from skips into passes; and the bound, completion-source, reachability and AD-14 tests pass without edits to their checks.

## Spec Change Log

- **2026-10-22, created** after Andrei's first real use of wave 3: staged action items could be seen on the dashboard but not acted on, and would expire unread. Story 13 split into 13a (this spec), 13b (execution) and 13c (proposals the scheduler raises). Two Ask First questions open.
- **2026-10-22, review (three lenses) and Andrei's decisions: frozen block edited (recorded here).**
  - **Andrei's decisions.** (1) A 14-day review window for `action_item`, `implicit_commitment` and `explicit_command` (no reason found for commands to differ, since their approval is unavailable until 13b), applied to items already waiting through the longer of the stored and registered windows; a shorter registration never shortens one. (2) No "done" in 13a: an approved item stays approved and shown, and done arrives with story 15 as a commitment state.
  - **Expiry.** The window starts at a new `staged_at`, never the meeting's start (`pipelines.py:1231`); rows staged before 13a are backfilled from `created_at` (a known limit for old `11b` promises). A re-stage keeps `staged_at` and the window, bumps the version only when `summary` or `payload` changes, and leaves a staged row past its window alone, as `left`. Approve and reject refuse a staged row past its window.
  - **Edits.** Re-staging an expired item is a conditional insert (no live replacement may name it), never a primary-key clash. A re-summary does not stage an item equal to a live one or a replacement of the meeting; a reworded one is staged and marked "may repeat an item you edited" (known limit). An edit equal to the current values writes nothing (`unchanged`). The replacement's summary comes from the edited `what`. In `review`, `-` clears and Enter keeps, and Ctrl-C or end of input prints the counts and exits 0. An empty or format-only `what` is exit 2. A non-editable field is exit 2, and a type with none is exit 3.
  - **`executed` has one meaning.** Approved items with no executor stay `approved`; story 15 records commitments keyed by proposal id. The AD-13 amendment no longer binds story 15, and AD-13's "payload schema and an executor callback" becomes "…and either an executor callback or what approval means". `approved → expired` is added for types with an executor only, which the architecture test at `:790` needs.
  - **Scopes (AD-4).** An unknown scope fails closed. `--meeting` in several scopes without `--scope` is refused. Every printed command carries `--scope`. `--title`/`--on` search personal and every enrolled project and refuse when ambiguous. An unknown project is exit 3, naming the enrolled ones.
  - **Pages.** Confidential and shown-under-an-issue lookups go by root id, with tests. The dashboard shows waiting items first, its cap drops approved items first, and its overflow names `proposal list`. The expired count excludes items with a replacement. An edited line stays under its issue's assignee.
  - **JSON.** `list` gains `next`, `unreadable_proposals` and `unscoped`; refusals print a refusal object; `edited_and_approved` and `unchanged` outcomes; state words for `executing`/`executed`, which have no actions; `fields: {}` for an unknown type; unreadable proposals counted or refused; missing change times drop their clause; "meeting record unavailable"; fallback order by staging time, then id.
  - **Concurrency.** `already` is reported before an `--if-version` mismatch. A plain approve prints the card it approved. `approve`, `reject` and `edit` get a 2-second bound (1 s for the writer thread, 100 ms busy wait), with a 1-second busy wait at a terminal.
  - **Performance.** Migration 2 adds indexed columns, so the listing selects with `LIMIT` and parses only the bodies it shows; a timed test measures it.
  - **Tests and files.** `pm_ai.core.proposals` provides what the three skipping architecture tests call, and `:203` is adjusted. The Tasks name `wiring.py`'s tables, `test_cli_subcommands.py`'s `next`, the `transition_proposal` tests (signature and `TerminalState` order kept) and the dashboard tests and golden.
  - **Also recorded.** CAP-6's `[STAGED_APPROVAL]` entry is deferred to story 15. `11b`'s positional ids: a shifted staged row bumps its version, and a dropped extraction over an acted row is counted.
- **2026-10-22, approved for build (Andrei),** after the three-lens review and his two decisions.

## Design Notes

**Why approval of a task needs no executor.** An action item is work for a person, not a change pm-ai makes. Its approval means the meeting's reading was right and the task is agreed. Keeping it `approved` leaves `executed` with one meaning: pm-ai carried something out. Story 15 records a commitment for each approved one, keyed by the proposal's id, and does not move it.

A staged spoken command is the opposite case. Approving it means "do it". An approval that sat waiting for 13b's executor would carry out a weeks-old instruction, which is the failure AD-37 names. So its approval is unavailable until 13b.

**Why the window starts at staging.** The window is the time the PM is given to decide. A transcript dropped a week after its meeting should give him the same two weeks as one fetched the same day. `created_at` stays as `11b` sets it, because a promise's ids and citations already rely on the meeting.

**Why Team Activity joins at read time instead of appending.** Appending a `withdrawn` or reworded line for each decision would be a second write to a second artifact after the proposal change. A failure between the two would leave the page out of step with the decision. Reading the decision when the page renders keeps one source of truth, and leaves `7e`'s update ids, and so `member link`, as they are.

**Why replacement ids extend the root.** `<root>_e<n>`:
- keeps every replacement findable by its meeting's prefix, which the dashboard and Team Activity already query;
- is longer than a root id, so `7b`'s supersession sweep, which matches exactly 12 hex characters after the prefix, can never supersede the PM's edit;
- still has the root in it, so the issue placement and confidential withholding keyed by the root apply.

**Why the architecture tests get an in-memory book.** `test_domain_invariants.py:780` and `:790` call `approve`, `expire` and `execute` by id with no store. The rules are pure, so `pm_ai.core.proposals` applies them to an in-memory book, and the store's claimed steps call the same rule function. The tests then check the rules the store enforces, not a copy of them.

**Accepted limit, until story 15.** The PM's decisions live only in `operational.db`, which is Tier 2 and not in `BACKUP_TARGETS`. A lost store loses them. Story 15's ledger is Tier 1 and makes approved commitments durable.

**Spine amendments this depends on** (re-derived through the architecture skill, a task above):
- **AD-13** (`:305`):
  - replace "they **register a proposal type** with a payload schema and an executor callback" with "they **register a proposal type** with a payload schema and either an executor callback or what approval means (`13a`)";
  - after "an expired proposal never executes.", add: "`[2026-10-22, 13a]` The allowed transitions are a table in `domain`, and any other is refused: `staged → approved | rejected | expired | superseded`; `approved → superseded` for every type; `approved → rejected` for a type with no executor; `approved → executing | expired` for a type with an executor; `executing → executed`. A proposal of a type with no executor stays `approved` and never enters `executing`. A proposal waits for review from the instant pm-ai staged it, for the longer of its stored and its type's registered time to live, and a staged proposal past that is expired to every reader and refused by every change, whether or not the sweep has reached it. An edit inserts a replacement in the superseded proposal's state, in the same step; editing an expired proposal stages a replacement, at most one live one, and leaves the expired one as it is. A re-stage keeps the staging instant and the time to live, and moves the version up only when the summary or payload changes."
- **AD-14** (`:311`), after "Approving a proposal produces a commitment in `PENDING`", add: "`[2026-10-22, 13a]` — recorded by story 15, keyed by the proposal's id; until then approval records only the proposal's state."
- **AD-21** (`:369`): in the foreground list, after "`transcript list` (`11f`), daemon-routed at the prompt in `DAEMON_BOUNDS`, inline", add "and `[2026-10-22, 13a]` `proposal list` and `proposal show` (`13a`), daemon-routed at the prompt in `DAEMON_BOUNDS`, inline, under 1 second, `proposal approve`, `proposal reject` and `proposal edit` (`13a`), the same, under 2 seconds, and `proposal review` (`13a`), in `TERMINAL_BOUND`"; add `13a` to **Binds**.
- **AD-37** (`:568`): add `13a` to **Binds**, and after "whichever loses observes the winner and stops." add: "`[2026-10-22, 13a]` Approval and rejection check expiry inside their own claimed step. A version only moves up, so a version a surface displayed matches only what it displayed. A lost compare-and-swap that finds the row already in the requested state is reported as done, not as a conflict."

## Verification

**Commands:**
- `uv run pytest tests/slice/test_proposals.py tests/slice/test_proposal_sweep.py tests/slice/test_dashboard_slice.py tests/core/test_rendering_sections.py tests/slice/test_member_updates_slice.py tests/slice/test_meeting_summary_slice.py tests/slice/test_transcript_slice.py tests/slice/test_transcript_path.py tests/slice/test_transcript_list.py tests/surfaces/test_cli_completion.py tests/surfaces/test_cli_subcommands.py tests/slice/test_line_requests_hardening.py tests/architecture/test_domain_invariants.py -q` -- expected: every matrix row passes; the three proposal tests in `test_domain_invariants.py` pass rather than skip
- `uv run pytest -q` -- expected: all pass; three fewer skips than before, the three named above
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a scratch home (`HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1`), drop and summarise the recorded fixture, then run `pm-ai proposal review`: approve one item, edit one and skip one. Then run `pm-ai dashboard`. Expected: the page shows the edited one waiting with its new text, the skipped one waiting, then the approved one.
- Move the clock past the 14-day window (the test clock or a fixture), start the daemon, and wait for one sweep. Expected: the waiting items are counted as expired on the page, and `proposal list --scope personal --state expired` lists them.
