---
title: 'staged proposals approved, edited or rejected, and expired by the scheduler'
type: 'feature'
created: '2026-10-22'
status: 'draft'
baseline_commit: '8a29acd'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Since wave 3, a summarised meeting stages its action items as proposals (`7b`), and the dashboard lists them under the meeting (`7d`). Nothing lets the PM act on them. No command lists them, shows one, approves, rejects or edits it. Nothing moves one to `expired` either: the dashboard just stops showing it 7 days after the summary. On his first real use (2026-10-22), Andrei found that the items a meeting agreed would lapse unread. The `Proposal` entity (AD-13) exists in `pm_ai/domain/proposals.py`, but the parts AD-13 promises around it do not. There is no allowed-transition table, so `staged → executed` passes. There is no registered type with a payload schema, no card renderer and no expiry sweep. `transition_proposal` (`service.py:5716`) reads the row, then writes it back unconditionally, so it is not a compare-and-swap between two processes. A re-summary that rewrites a staged item resets its version to 1 (`service.py:5517`), so a version seen before the re-summary can match again afterwards.

**Approach:** One proposal engine in `app`, used by every surface. Proposal types are registered with a payload schema, the fields a person may edit, a time to live, and what approval means. In 13a, approval is either "agreed, and that is the whole effect for now", or "not available yet". Every state change is one conditional update of the row in a claimed step (AD-5, AD-37). An edit stages a replacement proposal and supersedes the old one in the same step. The daemon's scheduler moves overdue staged proposals to `expired`, and every reader treats a staged proposal past its expiry as expired even before the sweep runs. A new command group, `pm-ai proposal list|show|approve|reject|edit|review`, is the CLI approval queue. Its cards come from one surface-neutral renderer, which story 5's Telegram bridge will reuse. Each command has a `--json` form, the exact next commands, and completion at the prompt, so no id has to be copied by hand. The dashboard (`7d`) shows each action item as waiting or approved, and counts the ones that expired. Team Activity (`7e`) hides a rejected item and shows an edited one's new wording. Running an executor (`approved → executing → executed`) is left to `13b`.

## Boundaries & Constraints

**Always:**

### Prerequisites and the slices of story 13

- **Built and relied on:** `7b` (staging and supersession, `stage_summary_items`), `7d` (the meeting digests), `7e` (`member_updates.md` and Team Activity), `11b` (the `explicit_command` and `implicit_commitment` proposals that transcript ingestion stages), `11f` (`--title`/`--on` choosing a meeting, the listing's `next`, completion with descriptions), `9a`/`9b` (the daemon's timers in `ticks.py`), `4u` (completion), `4q` (the prompt's routing).
- **Story 13 is split into three slices.** 13a is written in full here; 13b and 13c get specs of their own.
  - **13a, this spec:** the engine, the registry, the CLI queue, the cards, the expiry sweep, the dashboard and Team Activity, for the types pm-ai stages today.
  - **13b, execution:** the worker's `approved → executing → executed` path, the intent → call → outcome record (AD-20), the per-target lock row (AD-37), and how a failed execution is shown. `explicit_command` becomes the first type that runs, through the skill registry `11b` uses. 13b is what story 14 falls back to.
  - **13c, proposals the scheduler raises:** a credential that needs signing in again becomes a card (AD-39) under a registered push occasion (AD-40). It lands with or after story 5, which can push a card.
  - Story 15 hands each approved `action_item` and `implicit_commitment` to the commitment ledger. Story 5 renders the same cards on Telegram.

### The states and the allowed changes

- **The allowed changes are a table in `domain`, and every other change is refused** (`IllegalTransition`):
  - `staged → approved`, `staged → rejected`, `staged → expired`, `staged → superseded`;
  - `approved → executing` (13b); `approved → superseded` (an edit); `approved → rejected`, but only for a type with no executor, since nothing has been carried out;
  - `executing → executed` (13b).
  - `rejected`, `expired`, `superseded` and `executed` are terminal, as `ProposalState.is_terminal` already says.
- **What `approved` means for a type with no executor.** It means the PM agreed. The proposal stays `approved`, with no executor run and no time limit, until a later story gives the type a hand-off. For `action_item`, that is story 15's commitment ledger, which will move each approved item `approved → executing → executed` as it records the commitment, including items approved before 15 exists. In 13a, such a type never enters `executing` or `executed`.
- **No "done" state** (see Ask First 2). Whether an agreed task has been done is real-world fulfilment, which AD-14 keeps out of the proposal's state. It belongs to story 15's commitment states.
- **Expiry.** A proposal expires at `created_at` plus its time to live. With Ask First 1's recommendation, that time to live is the longer of the one stored in its body and the one its type is registered with. Only a `staged` proposal expires. Any reader (the listing, a card, the dashboard, completion) shows a staged proposal past its expiry as `expired`. Approval and editing check expiry inside their own claimed step, against the row they read in that step. So a proposal past its expiry is never approved, whether or not the sweep has reached it yet. An expired proposal never runs (AD-13).

### Registered types

- **The registration, in `domain`:** a `ProposalType` value with:
  - `name`: the stored type;
  - `label`: the card's heading word, such as "Action item";
  - `fields`: the payload schema, one entry per field, each with its name, whether it is required, its length limit, and whether a person may edit it;
  - `ttl`: the time to live, 7 days unless the type overrides it;
  - `approval`: either `agreed` (approval is the whole effect for now) or `unavailable` with a reason the card prints. 13b adds `executor`, a callback.
  - `card`: the payload rendered to the card's field lines.
- **The registry is closed, and is built in `app` (AD-30).** The composition root registers every type. A feature registers its type there and never builds an approval flow (AD-13). A second registration of one name is refused at start-up. A stored proposal whose type is not registered is still listed and shown, with its stored summary and the sentence "pm-ai does not know this type". It can be rejected and nothing else.
- **The three types pm-ai stages today:**
  - `action_item` (`7b`). Fields `what` (required, at most 200 characters, `WHAT_LIMIT`), `owner` (optional, the speaker label as said; `7b` sets no length for it, since it must be one of the transcript's labels, so 13a sets 80 characters for a typed owner) and `due` (optional, as said, at most 40 characters, `DUE_LIMIT`), all editable. Approval: `agreed`. TTL: Ask First 1.
  - `implicit_commitment` (`11b`, "I'll … by …"). Fields `what` and `when`, shown from their cleaned text and not editable in 13a. Approval: `agreed`, waiting for story 15's hand-off. TTL: 7 days.
  - `explicit_command` (`11b`, a spoken command that came from a hand-dropped transcript or failed one of AD-32's tests). Shown with its verb, target and remainder. Approval: `unavailable`, with the reason "pm-ai cannot carry out a staged command yet; reject it, or make the change yourself". Rejecting works. TTL: 7 days.
- **Text typed by the PM is trusted as his own,** like a typed title (`11e`). It is not passed through the prompt-injection filter. Line breaks and control characters are folded to spaces, and the schema's limits are applied. A value over its limit is refused (exit 2), naming the limit. Text that came from a transcript stays as `7b` and `11b` cleaned it.

### Edit

- **What may be edited.** On an `action_item`: `--what`, `--owner`, `--due`, at least one of them. `--owner ""` and `--due ""` clear the field. `what` cannot be cleared. An owner is a label, so on a project page it resolves to a member by `7e`'s read-time rule: a saved alias, or an exact display name or username.
- **An edit supersedes (AD-5).** In one claimed step, the old proposal moves `staged → superseded` (or `approved → superseded`), checked by its version. A new proposal is inserted holding the edited payload. Its id is `<root>_e<n>`, where `<root>` is the id `7b` minted and `<n>` counts the replacements made from that root (1, 2, …). Its body names the old proposal (`replaces`). It takes the old proposal's state: an edited waiting item is still waiting, and an edited approved item is still approved, since the PM made the change himself. A staged replacement gets a new `created_at`, and so a new expiry. If the old proposal's version has changed in the meantime, nothing is written, and the command reloads and says what happened.
- **`--approve` on `edit`** approves the replacement in the same step, which is the usual "fix it and agree" in one command.
- **An expired item can be staged again by editing it.** That inserts `<root>_e<n>` as `staged`, with `replaces` naming it. The expired proposal is not changed and never runs. This is the only way back from `expired`. Editing a `rejected` or `superseded` proposal is refused (exit 3). For a superseded one, the refusal names its replacement.
- **A re-summary never touches the PM's edits.** `stage_summary_items` sweeps only ids of the root form, the prefix followed by exactly 12 hex characters (`service.py:5543`). A replacement id is longer, so the sweep never matches it. A root that an edit superseded is reported as `kept_superseded` and is not staged again (`service.py:5534`). The PM's word outlives a fresh reading of the transcript.

### Version and change time

- **The version only goes up.** When a re-ingestion or re-summary rewrites a staged proposal with a different body, it moves the version up by one; today both reset it to the new proposal's 1 (`service.py:5517`, `:5681`). An unchanged body is still reported as staged, as it is today. So a version a card was shown at matches only the body that card showed.
- **Each change records when and from where.** The body gains `changed_at` (the instant of the last change) and `changed_via`: `terminal`, `prompt`, `sweep`, `summary` or `ingestion` (story 5 adds `telegram`). It also gains `scope` and `replaces`. `7b` and `11b` write `scope` from now on. For a body that has no `scope`, it is found by matching the id's scope digest against personal and each enrolled project (`action_item_prefix`, `meeting_summary.py:542`), or, for `11b`'s ids, through the meeting's record. A proposal whose scope is found in neither way is shown as "scope unknown". The new fields are read as empty from bodies that lack them.

### Concurrency

- **Every change is one conditional `UPDATE … WHERE proposal_id = ? AND version = ? AND state = ?`** inside the claimed step's `BEGIN IMMEDIATE`, and checks the number of rows it changed (AD-5's `operational.db` rule, AD-37). This replaces `transition_proposal`'s read-then-write. The SQLite transaction serialises the daemon's writer thread and a client process alike. Zero rows changed means someone else moved the proposal first: the command reloads it and re-evaluates. It never retries blindly.
  - Already in the state asked for (two surfaces approved at once, or an approve was sent twice): the command says "already approved, <when>, from <where>" and exits 0. Nothing happens twice.
  - Moved somewhere else (expired, rejected, superseded by an edit or a re-summary): refused (exit 3). The refusal names the state, when it changed and from where, and the replacement when there is one. For a re-summary, it names the meeting's `proposal list` command, because the new item's id cannot be told from the old one.
- **`--if-version <n>`** on `approve`, `reject` and `edit` acts only if the proposal is still at version `n`, and otherwise refuses (exit 3) showing the current card. A wrapper passes the version from the `--json` card it displayed. `review` always does this for the card it showed. Telegram's buttons (story 5) carry the version the same way.
- **The expiry sweep and approval cannot both win.** The sweep moves `staged → expired` by version, and approval moves `staged → approved` by version after checking expiry in the same step. Whichever commits first wins, and the other reloads and reports. The worker's `approved → executing` in 13b is the third party in this race, and it uses the same rule.
- **Edit against the sweep, a re-summary or another edit:** the old proposal's conditional update and the insert are one transaction. If the update changes no row, the transaction is rolled back, so no replacement is left without the old one having moved. Two edits of one expired item both try to insert `<root>_e<n>`. The second fails on the primary key, rolls back, and says the item was already staged again, naming the new id.
- **A busy writer** (a claim held, or a request waiting on the daemon's writer thread for over a second) is AD-5's existing "busy, try again" refusal. Nothing is half-written.

### The expiry sweep (AD-13: the scheduler owns it)

- **A `ProposalSweep` timer sits beside `HarvestTimers` and `RenderTimers`** in `pm_ai/app/ticks.py`, attached in `_lifespan` (`daemon.py:1030`). It is in-memory like them, and `10a` replaces it with a queue row like them. It runs at daemon start, then every 15 minutes, and also at the earliest expiry it saw in its last pass when that comes sooner.
- **Each pass:** one read of the staged proposals on its own read-only connection, then claimed steps of at most 200 proposals each. Each step moves `staged → expired` by version, setting `changed_at` to the proposal's expiry instant (not the sweep's own clock) and `changed_via` to `sweep`. A row that changed in between is skipped, and the next pass sees it. A body that cannot be read is counted, never expired, and logged without its content. The step runs on the writer thread, and the read on the I/O pool (AD-19).
- **Types never sweep their own proposals.** A type sets only `ttl`. When the daemon is not running, nothing is swept, and readers still show such proposals as expired.

### The CLI approval queue: `pm-ai proposal`

- **Why `proposal` and these verbs.** It is AD-13's word, and a singular noun, like every other group (`goal`, `member`, `connector`, `transcript`). The verbs are the card's buttons. One group serves every type, so story 14 and later stories add types, never commands.
- **`proposal list [--scope personal|project:<id>] [--meeting <id> | --title <words> [--on <date>]] [--type <name>] [--state staged|approved|rejected|expired|superseded|all] [--limit 1..200] [--json]`.**
  - `--state` defaults to `staged`, the proposals waiting for the PM. `staged` means staged and not past expiry. `expired` includes staged proposals past expiry that the sweep has not moved yet.
  - `--title`/`--on` choose exactly one meeting, through `11f`'s `resolve_title` (`meeting_listing.py:460`), or are refused with the candidates.
  - Order: by meeting, newest start first, then in the order of the meeting's recorded summary, with a replacement in its root's place. Proposals with no meeting come last, by `created_at`. `--limit` defaults to 20, and the number left out is stated.
  - `people:` scopes are refused (exit 3), and no proposal in a `people` scope is listed (AD-4). A project that cannot be read is named on one line, and the others are still listed.
  - The text form is one card per proposal (below). The last line gives the exact command for the next step: `pm-ai proposal review` with the same filters when anything is waiting, and otherwise the filter that would show more. An empty listing is one sentence that names the filter and the scopes looked at.
- **`proposal show <id> [--json]`** shows one card, in any state, including where it came from and, for an edit, what it replaced and what replaced it.
- **`proposal approve <id> [--if-version <n>] [--json]`**, **`proposal reject <id> [--if-version <n>] [--json]`**, **`proposal edit <id> [--what <text>] [--owner <text>] [--due <text>] [--approve] [--if-version <n>] [--json]`.** Each asks nothing. Each prints the card after the change, the outcome in one sentence, and the next step: the next waiting proposal of the same meeting, if any, and otherwise `proposal list`.
- **`proposal review [--scope …] [--meeting <id> | --title <words> [--on <date>]]`** walks the waiting proposals in `list`'s order, one card at a time, with `[a]pprove [e]dit [r]eject [s]kip [q]uit`. Edit asks for each editable field with its current value filled in, and Enter keeps a field as it is. Then it shows the replacement's card and asks "approve it? [Y/n]". Each answer is one change, checked against the version the card showed. On a conflict, the current card is shown again with one sentence saying what changed. At the end, one line counts what was approved, rejected, edited and skipped, and gives the next step.
  - `review` is the only command here that asks. It is something the PM chose to start, not the normal path, and every answer can also be given by `approve`, `reject` or `edit`.
  - Without a terminal (stdin not a TTY), it is refused (exit 3), naming `proposal list --json` and the three verbs.
- **Choosing without an id.**
  - At the prompt, the `<id>` of `show`, `approve`, `reject` and `edit` completes from the proposals that verb can act on: waiting ones for `approve`, waiting and approved ones for `reject` and `edit` (expired ones too for `edit`), and any for `show`. At most 100 are offered, newest meeting first. Each inserts the id and shows `owner: what` and the meeting's title and start beside it. Typed text matches the start of the id, or the start of any word of `what` or of the title, ignoring case. This reuses `11f`'s description column and matching (`completion.py:309`). The reader follows 4u's rules: no `StorageService`, no master key, keychain or daemon, and nothing offered on failure.
  - Every card prints its exact commands, and `review` needs no id at all.
  - The id stays the only way to name a proposal on the command line, so a wrapper always acts on the proposal it showed (`11f`'s reasoning).
- **Exit codes:** 0 when the change was made, when it was already made, or when there was something to report (an empty listing included); 2 for a usage error (an unknown state, a limit outside 1 to 200, `edit` with no field, a value over its limit); 3 for a refusal (not found, a terminal state, approval unavailable for the type, a version mismatch, a `people` scope, no terminal for `review`, busy).

### The card (one renderer for every surface, AD-13)

- **One function in `pm_ai/core` turns a proposal and its registered type into a `Card` value.** It needs the meeting's title and start, the clock and the display zone. It does no I/O. A `Card` holds:
  - the heading (the type's label, the state in plain words, and for a waiting proposal "until <date>");
  - the field lines from the type's `card`;
  - where it came from (the meeting's title, start and scope, or the citation when there is no meeting);
  - for an edit, what it replaced;
  - its actions, each with a name (`approve`, `edit`, `reject`, `show`), whether it is available (and why not), and the exact command's words including `--if-version`.
- **The renderers:** text and JSON for the CLI in 13a; an inline keyboard from the same actions for Telegram in story 5. A surface never decides availability itself.
- **What the text card looks like, for a waiting action item:**

  ```
  Action item, waiting for you until Tue 4 Nov
    Sam Rivera: send the cache TTL numbers (Thursday)
    from Example Weekly Sync-up, Tue 21 Oct 10:00, personal
    approve: pm-ai proposal approve prp_… --if-version 1
    edit:    pm-ai proposal edit prp_… --what "…" --owner "…" --due "…" --if-version 1
    reject:  pm-ai proposal reject prp_… --if-version 1
  ```

  An approved card says "approved on <date>" and offers `edit` and `reject`. An expired one says "expired on <date>, not reviewed" and offers `edit`, to stage it again. Text from a transcript has its line breaks folded and is escaped, as `7d` does.
- **The machine form, `--json`:** one object on standard output and nothing else.
  - For `list`: `{"format": 1, "scopes", "unreadable": [{"scope", "reason"}], "more", "proposals": [card, …]}`.
  - For `show`, `approve`, `reject` and `edit`: `{"format": 1, "outcome", "proposal": card, "replaced": card|null, "next": [words]|null}`. `outcome` is one of `approved`, `rejected`, `edited`, `already`, `shown`.
  - A card is `{"proposal_id", "type", "label", "state", "version", "scope", "meeting": {"meeting_id", "title", "start"}|null, "fields": {name: value|null}, "created_at", "expires_at"|null, "changed_at"|null, "changed_via"|null, "replaces"|null, "replaced_by"|null, "actions": {name: {"available", "reason"|null, "command": [words]|null}}}`.
  - `state` is the store's word, with a staged proposal past expiry given as `expired`. Instants are ISO 8601 UTC. Fields are the type's own, and for `11b`'s types they are the cleaned text, never the raw.
  - A field may be added later without changing `format`. Removing or renaming one changes it.

### What the dashboard shows (`7d`)

- **Under each meeting:**
  - waiting items, as `owner: what (due), to review by <date>`;
  - approved items, as `owner: what (due), approved`;
  - a replacement in its root's place, with its edited values;
  - rejected and superseded items are not shown;
  - a line counting the items that expired without review, and naming `pm-ai proposal list --state expired --meeting <id>`;
  - when anything is waiting, a line naming `pm-ai proposal review --scope <scope> --meeting <id>`.
- `MAX_DIGEST_ITEMS` (5), confidential-issue withholding, and "shown under #n in Team Activity" are unchanged. The item counts as shown under an issue when its root is (`7e`'s mapping is keyed by root id).
- **The read** (`open_action_items`, `service.py:5178`) returns the meeting's waiting and approved `action_item` proposals, root ids and replacement ids alike (the prefix, 12 hex characters, then optionally `_e<n>`). It also returns the expired count. It stays a plain read on a read-only connection. "Open action item" in the page's wording now means waiting or approved.

### What Team Activity shows (`7e`)

- **The PM's decision is joined in at read time. Nothing is appended to `member_updates.md`.** The render reads, in one read-only query, the current state of each action-item root on the page and of its newest replacement. Then:
  - a line whose item, or whose newest replacement, is `rejected` is hidden;
  - a line whose item was replaced by an edit shows the replacement's `what`, `owner` and `due`, under the same issue, with the same link kind, and with the PM's confirm or reject of that link still applying;
  - waiting, approved and expired items are shown as they are today. A line is a note of what the meeting said about the issue, and its 14-day window (`UPDATE_WINDOW`) bounds it;
  - a re-summary's `withdrawn` lines work as today.
- If the proposals cannot be read, the page says so in one line and shows the lines as recorded, which is the same fail-open as today's issue notes. Confidential-issue withholding fails closed, as now.
- `member unmatched` and `member link` are unchanged. They work on update ids, which do not change, because the ledger is not rewritten.

### Where it runs (AD-21)

- `proposal list` and `proposal show` compose nothing at a terminal (`composes=False`, as `transcript list`). They read the store on a read-only connection and the meeting records by path, and ask for no master key or keychain. `operational.db` and the meeting records are not encrypted.
- `approve`, `reject` and `edit` are composed as `member link` is. They write one claimed step to `operational.db` and ask for no master key.
- All five join `DAEMON_BOUNDS` (`entry.py:593`) with `CONFIG_SHOW_BOUND_SECONDS`, 1 second. At the prompt they run inline through the daemon and print exactly what the terminal prints.
- `proposal review` joins `TERMINAL_BOUND` (`dispatch.py:4518`), so at the prompt it runs in the prompt's own process (`repl.py:127`), and each answer is written by the same function.
- The listing reads no more than the cards it shows, plus the rows needed for `more`, so it stays within 1 second.
- The spine amendments are in Design Notes.

### The next step offered elsewhere

- `transcript summarize`'s outcome sentence (`pipelines.py:3982`) and `transcript list`'s `next` (`11f`) name `pm-ai proposal review --scope <scope> --meeting <id>` when the meeting has items waiting. In JSON, this is a new `next.review`.

### No real data

Tests use `11d`'s fixture names (`Example Weekly Sync-up`, `Alex Morgan`, `Sam Rivera`) and `example.com` handles. No real meeting, name or item text appears.

**Ask First:**

1. **How long an `action_item` waits for review (its TTL).** The time to live is how long an item waits for the PM's decision. It is not the task's deadline: an approved item never expires. Seven days is AD-13's default, and an item can now be staged again by editing it after expiry. But the PM may review only weekly, and `7e` keeps a meeting's lines for 14 days. Recommended: **(a) 14 days**, effective for every waiting item. Expiry is computed from the longer of the stored and the registered time to live, so items already waiting get 14 days from their creation, and a later, shorter registration never shortens a proposal already staged. Alternatives: (b) 14 days only for items staged after 13a, so items already waiting keep their 7 days; (c) keep 7 days.
2. **Whether 13a needs "done" for an approved item.** Recommended: **(a) no.** An approved item stays approved and shows on the dashboard under its meeting while the meeting is within the dashboard's look-back. `proposal list --state approved` lists them all. Done arrives with story 15's commitment ledger, as a commitment state, because AD-14 forbids a fulfilment state on a proposal. Alternatives: (b) a `proposal done <id>` that writes a "done" line to an interim ledger, which story 15 adopts; (c) let `reject` on an approved item stand in for done. This is already allowed for types with no executor, but it records "not agreed", which is false for a task that was done.

**Never:**

- No external system changes. No executor runs, and no proposal enters `executing` or `executed` in 13a.
- No write to `commitments_log.md` or `event_log/`. CAP-6's `[STAGED_APPROVAL]` entry belongs to story 15's ledger.
- No change the domain table does not allow. No blind retry. No version reset.
- No proposal past its expiry approved. No expiry sweep in a feature.
- No edit applied to the old proposal in place.
- No re-summary superseding a PM's replacement.
- No line rewritten or appended in `member_updates.md` by an approval decision.
- No `people` scope read, listed or offered.
- No proposal chosen by position or "latest".
- No question asked by any command except `review`.
- No master key or keychain asked for.
- No model call.
- No change to `7b`'s ids, its summary record, or its `left` and `superseded` rules, except the version moving up.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Waiting items listed | a personal meeting summarised, 3 items staged | `proposal list`: 3 cards in the summary's order, each "waiting for you until <date>"; last line `pm-ai proposal review` | exit 0 |
| JSON listing | the same, `--json` | one object, `format: 1`, 3 cards, each with `actions.approve.command` including `--if-version 1` | exit 0 |
| Approve | `proposal approve <id>` | card "approved on <date>"; `changed_via: terminal`; the dashboard's next render shows it as approved | exit 0 |
| Approve twice | approve the same id again | "already approved, <when>, from the terminal"; nothing changes | exit 0 |
| Two surfaces | the terminal and the prompt approve one id at once | one change; the other prints "already approved … from the prompt" | both exit 0 |
| Approve vs sweep | the sweep commits `expired` first | approve refused: "expired on <date>, not reviewed"; offers `edit` to stage it again | exit 3 |
| Past expiry, no daemon | staged, expiry passed, daemon never ran | listed as expired; approve refused, the same as after a sweep | exit 3 |
| Reject | `proposal reject <id>` on a waiting item | rejected; gone from the dashboard; its Team Activity line hidden | exit 0 |
| Reject an approved action item | approved `action_item` | rejected (a type with no executor) | exit 0 |
| Edit | `proposal edit <id> --due "Friday"` | old superseded; `<root>_e1` staged with the new due, a new expiry, `replaces` set; the dashboard and Team Activity show "Friday" in its place | exit 0 |
| Edit and approve | `edit … --what "…" --approve` | the replacement approved in the same step | exit 0 |
| Edit an approved item | approved item, `--owner "Alex Morgan"` | old superseded; replacement approved; the project page resolves the owner by exact member name | exit 0 |
| Edit an expired item | expired item, `--due "next week"` | `<root>_e1` staged; the expired one untouched; card "waiting for you" | exit 0 |
| Edit twice at once | two edits of one waiting item | one replacement; the other reloads: "already replaced by <id>" | exit 3 for the loser |
| Edit with no field | `proposal edit <id>` | usage error naming `--what`, `--owner`, `--due` | exit 2 |
| Over the limit | `--what` of 201 characters | refused, naming 200 | exit 2 |
| Clear what | `--what ""` | refused: `what` is required | exit 2 |
| Stale version | `approve <id> --if-version 1` after a re-summary moved it to 2 | refused, showing the current card at version 2 | exit 3 |
| Re-summary keeps an edit | the item edited, then `transcript summarize --again` | root kept superseded; `_e1` untouched; no duplicate on the dashboard | exit 0 |
| Re-summary changes a waiting due | a staged item's due changes, id unchanged | body rewritten, version 1 → 2 | N/A |
| Re-summary vs approve | the re-summary supersedes a waiting item as the PM approves it | one wins; if the re-summary won, approve is refused naming the meeting's `proposal list` | exit 3 |
| Sweep | 3 items past expiry, daemon running | within 15 minutes all `expired`, `changed_at` at their expiry, `changed_via: sweep`; the dashboard counts "3 expired without review" | N/A |
| Sweep, unreadable body | one staged body does not parse | counted and logged, not expired; the others expire | N/A |
| Staged command | an `explicit_command` from a dropped transcript | card shows the verb and target; approve unavailable, with its reason; reject works | approve exit 3 |
| Spoken promise | an `implicit_commitment` | approve: approved, waiting for story 15 | exit 0 |
| Unknown type | a stored type no registration names | listed with its stored summary and "pm-ai does not know this type"; only reject offered | approve exit 3 |
| Review | `proposal review --title weekly` at a terminal | walks 3 cards; `a`, `e` (due changed, then `Y`), `s`; final line "1 approved, 1 edited and approved, 1 skipped" | exit 0 |
| Review conflict | an item expires while its card is open | the answer refused; the current card shown with "expired on <date>" | continues |
| Review without a terminal | stdin not a TTY | refused, naming `proposal list --json` and the verbs | exit 3 |
| Completion | `pm-ai> proposal approve cache<Tab>` | offers the waiting item whose `what` has a word starting "cache", showing its owner, text and meeting | N/A |
| People | `--scope people:example` | refused (AD-4) | exit 3 |
| Bad state | `--state done` | usage error naming the six words | exit 2 |
| Not found | `proposal show prp_nope` | refused: no proposal under that id | exit 3 |
| Prompt | `pm-ai> proposal list` | the same text as at the terminal, inline, within 1 second | N/A |
| Summarize's next step | a summary stages 2 items | its sentence ends with the `proposal review` command; `transcript list --json` has `next.review` | exit 0 |
| Hostile text | an item whose `what` holds a newline and a control character | folded to one line on the card; escaped in JSON | exit 0 |

</frozen-after-approval>

## Code Map

Anchors checked against `main` at `8a29acd`.

- `pm_ai/domain/proposals.py:21` -- `Proposal` (no `scope`, `replaces`, `changed_at`, `changed_via` yet); `:37` `transition`, which checks only the version and terminality, so any edge passes; `:53` `OpenActionItems`
- `pm_ai/domain/lifecycle.py:28` -- `ProposalState`, `is_terminal`; `:76` the AD-14 disjointness check; `:92` `DEFAULT_PROPOSAL_TTL`
- `pm_ai/domain/meeting_summaries.py:62` `WHAT_LIMIT`; `:65` `DUE_LIMIT`; `:71` `ACTION_ITEM`; `:118` `SummaryActionItem`
- `pm_ai/core/extraction.py:51` `EXPLICIT_COMMAND`; `:52` `IMPLICIT_COMMITMENT`; `:94`, `:113` their payloads
- `pm_ai/core/meeting_summary.py:542` -- `action_item_prefix` (`prp_<scope digest>_<meeting>_ai_`); `:547` `action_item_id`
- `pm_ai/storage/service.py:217` -- the `proposals` table; `:718` `_dump_proposal`, `:738` `_load_proposal`; `:1038` `ProposalNotFound`; `:1602` `_ITEM_DIGEST`, `:1603` `_HEX12`; `:5178` `open_action_items`; `:5495` `stage_summary_items` (`:5517` the version reset, `:5534` `kept_superseded`, `:5543` the exact-length sweep); `:5669` `stage_unless_acted` (`:5681` the same reset); `:5695` `stage_proposal`; `:5704` `load_proposal`; `:5716` `transition_proposal`, a read and an unconditional write
- `pm_ai/ports/__init__.py:849` -- `open_action_items` on the port
- `pm_ai/app/wiring.py:4105`, `:4127`, `:4128`, `:4138` -- the writer calls for staging and moving proposals; `:4162` `open_action_items` as a read-only read
- `pm_ai/app/pipelines.py:1149` -- `run_transcript_ingestion`, which stages `11b`'s proposals (`:1234`); `:1249` `_proposal_id`; `:2628` `_meeting_digest`, which reads open items (`:2647`) and skips those shown under an issue (`:2665`); `:2780` `project_meeting_digests`; `:3896` `_written`, which stages items (`:3961`); `:3982` the outcome sentence
- `pm_ai/core/rendering.py:199` `MAX_DIGEST_ITEMS`; `:976` the item lines; `:1018` the meeting's command line
- `pm_ai/domain/renders.py:83` `DigestItem`; `:117` `MeetingDigest`
- `pm_ai/core/member_updates.py:125` `UPDATE_WINDOW`; `:744` `Ledger`; `:800` `parse_ledger`; `:1060` `issue_notes_for`, where the read-time join goes
- `pm_ai/app/ticks.py:209` `HarvestTimers`; `:695` `RenderTimers`; `pm_ai/app/daemon.py:1030` `_lifespan`, `:639`/`:643` where the timers attach
- `pm_ai/surfaces/cli/dispatch.py:1184` `Command`; `:2235` `SCOPE_ARGUMENT`; `:3355` `MEETING_ARGUMENT`; `:3358` `TITLE_OPTION`; `:3370` `JSON_FLAG`; `:3482` `ON_OPTION`; `:4200` `STATE_OPTION`, `:4201` `LIMIT_OPTION`; `:4251` `_transcript_list`, the listing precedent; `:4489` `_member_link`, the composed-write precedent; `:4518` `TERMINAL_BOUND`; `:4554` `TABLE`, where `proposal` joins
- `pm_ai/surfaces/cli/repl.py:127` `runs_here`
- `pm_ai/app/entry.py:593` `DAEMON_BOUNDS`; `:1943` `_meeting_ids`, the completion reader precedent
- `pm_ai/surfaces/cli/completion.py:95` `FREE_TEXT_NAMES`; `:131` `_BY_COMMAND`; `:194` `Readers`; `:309` `Candidate`
- `pm_ai/app/meeting_listing.py:460` `resolve_title`; `pm_ai/domain/meeting_listing.py:137` `NextStep`
- `tests/slice/test_line_requests_hardening.py` -- every daemon-run command declares its bound; `tests/surfaces/test_cli_completion.py` -- every declared name has a source; `tests/surfaces/test_cli_subcommands.py` -- every leaf is reachable; `tests/architecture/test_domain_invariants.py:187` -- the AD-14 check
- `ARCHITECTURE-SPINE.md:305` AD-13; `:311` AD-14; `:369` AD-21; `:568` AD-37

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/proposals.py`, `lifecycle.py`, a new `proposal_types.py` -- the edge table and `IllegalTransition`; `ProposalType` and its field schema; the new body fields; expiry as the longer TTL -- one model
- [ ] `pm_ai/storage/service.py` -- one conditional-update transition with re-read on a miss; the edit step (supersede and insert); the sweep step; the version moving up in `stage_summary_items` and `stage_unless_acted`; `open_action_items` with replacements, approved items and the expired count; reads for the listing and Team Activity on a read-only connection -- every change a compare-and-swap
- [ ] `pm_ai/app/` (a new `proposals.py`) -- the registry built in the composition root with the three types; list, show, approve, reject and edit as one function each for the terminal, the daemon and `review`; scope resolution -- one engine
- [ ] `pm_ai/core/` (a new `proposal_cards.py`) -- `Card`, its actions, and the text and JSON renderers -- one card
- [ ] `pm_ai/app/ticks.py`, `daemon.py` -- `ProposalSweep` -- the scheduler owns expiry
- [ ] `pm_ai/app/pipelines.py`, `pm_ai/core/rendering.py`, `pm_ai/domain/renders.py`, `pm_ai/core/member_updates.py` -- the waiting and approved lines, the expired count, the review line; Team Activity's read-time join; summarize's next step; `7b` and `11b` write `scope`
- [ ] `pm_ai/surfaces/cli/dispatch.py`, `completion.py`, `pm_ai/app/entry.py`, `pm_ai/app/meeting_listing.py` -- the `proposal` group, `DAEMON_BOUNDS` and `TERMINAL_BOUND` entries, completion of ids, `next.review` -- the queue
- [ ] `tests/slice/test_proposals.py`, `tests/slice/test_proposal_sweep.py`, and the existing dashboard, member-update, summary, completion, bound and reachability tests -- one test per matrix row, plus two processes approving one id
- [ ] `ARCHITECTURE-SPINE.md` -- the amendments in Design Notes, re-derived through the architecture skill

**Acceptance Criteria:**
- Given a summarised fixture meeting with 3 staged items, when `pm-ai proposal list --json` runs with no daemon, then it prints one object with 3 cards whose `actions.approve.command` runs as printed, and nothing is written.
- Given one waiting item, when two processes run `proposal approve` on it at the same moment, then the row changes once, its version moves up by one, and both exit 0.
- Given a staged item past its expiry, when `proposal approve` runs before any sweep, then it is refused with exit 3 and the row is unchanged; and when the daemon's sweep runs, then the row is `expired` with `changed_via: sweep`.
- Given an edited item, when the meeting is re-summarised with `--again`, then the replacement is unchanged and the dashboard lists the item once, with the edited text.
- Given the change, when `uv run pytest` runs, then everything passes, and the bound, completion-source, reachability and AD-14 tests pass without edits to their checks.

## Spec Change Log

- **2026-10-22, created** after Andrei's first real use of wave 3: staged action items could be seen on the dashboard but not acted on, and would expire unread. Story 13 split into 13a (this spec), 13b (execution) and 13c (proposals the scheduler raises). Two Ask First questions open.

## Design Notes

**Why approval of a task needs no executor.** An action item is work for a person, not a change pm-ai makes. Its approval means the meeting's reading was right and the task is agreed. Keeping it `approved`, rather than moving it to `executed` at once, leaves story 15 a clear set to hand to the commitment ledger, and leaves `executed` meaning that pm-ai carried something out. A staged spoken command is the opposite case. Approving it means "do it". An approval that sat waiting for 13b's executor would carry out a weeks-old instruction, which is the failure AD-37 names. So its approval is unavailable until 13b.

**Why Team Activity joins at read time instead of appending.** Appending a `withdrawn` or reworded line for each decision would be a second write to a second artifact after the proposal change. A failure between the two would leave the page out of step with the decision. Reading the decision when the page renders keeps one source of truth, and leaves `7e`'s update ids, and so `member link`, as they are.

**Why replacement ids extend the root.** `<root>_e<n>` keeps every replacement findable by its meeting's prefix, which the dashboard and Team Activity already query. It is longer than a root id, so `7b`'s sweep, which matches exactly 12 hex characters after the prefix, can never supersede the PM's edit. And the root is still in the id when the replacement is mapped to an issue.

**Accepted limit, until story 15.** The PM's decisions live only in `operational.db`, which is Tier 2 and not in `BACKUP_TARGETS`. A lost store loses them. Story 15's ledger is Tier 1 and makes approved commitments durable.

**Spine amendments this depends on** (re-derived through the architecture skill, a task above):
- **AD-13** (`:305`), after "an expired proposal never executes.", add: "`[2026-10-22, 13a]` The allowed transitions are a table in `domain`, and any other is refused: `staged → approved | rejected | expired | superseded`; `approved → executing | superseded`, and `approved → rejected` only for a type with no executor; `executing → executed`. A type with no executor registers what approval means; its proposal stays `approved` until a later story registers a hand-off, and never enters `executing` in the meantime. A staged proposal past its expiry is expired to every reader and to approval, whether or not the sweep has reached it. An edit inserts a replacement in the superseded proposal's state, in the same step; editing an expired proposal stages a replacement and leaves the expired one as it is."
- **AD-13**, with Ask First 1 (a): "A proposal expires at its creation plus the longer of its stored time to live and its type's registered one."
- **AD-14** (`:311`), after "Approving a proposal produces a commitment in `PENDING`", add: "`[2026-10-22, 13a]` — from story 15, whose hand-off records a commitment for each approved `action_item` and `implicit_commitment`, those approved before it included; until then approval records only the proposal's state."
- **AD-21** (`:369`): in the foreground list, after "`transcript list` (`11f`), daemon-routed at the prompt in `DAEMON_BOUNDS`, inline", add "and `[2026-10-22, 13a]` `proposal list`, `proposal show`, `proposal approve`, `proposal reject`, `proposal edit` (`13a`), daemon-routed at the prompt in `DAEMON_BOUNDS`, inline, and `proposal review` (`13a`), in `TERMINAL_BOUND`"; add `13a` to **Binds**.
- **AD-37** (`:568`): add `13a` to **Binds**, and after "whichever loses observes the winner and stops." add: "`[2026-10-22, 13a]` Approval checks expiry inside its own claimed step. A version only moves up, a re-staged body included, so a version a surface displayed matches only what it displayed."

## Verification

**Commands:**
- `uv run pytest tests/slice/test_proposals.py tests/slice/test_proposal_sweep.py tests/slice/test_dashboard_slice.py tests/slice/test_member_updates_slice.py tests/slice/test_meeting_summary_slice.py tests/slice/test_transcript_list.py tests/surfaces/test_cli_completion.py tests/surfaces/test_cli_subcommands.py tests/slice/test_line_requests_hardening.py tests/architecture/test_domain_invariants.py -q` -- expected: every matrix row passes
- `uv run pytest -q` -- expected: all pass; the skip count unchanged
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a scratch home (`HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1`), drop and summarise the recorded fixture, then run `pm-ai proposal review` and approve one item, edit one and skip one. Then run `pm-ai dashboard`. Expected: the page shows one approved item, the edited one waiting with its new text, and the skipped one waiting. Move the clock past the time to live (the test clock or a fixture), start the daemon, and wait for one sweep. Expected: the waiting items are counted as expired on the page, and `proposal list --state expired` lists them.
