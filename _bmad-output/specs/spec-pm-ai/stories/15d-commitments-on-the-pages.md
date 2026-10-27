---
title: 'commitments on the pages'
type: 'feature'
created: '2026-10-26'
status: 'draft'
baseline_commit: '7a097a7'
review_loop_iteration: 1
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/15a-approved-items-become-commitments.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/15c-the-pms-commitment-commands.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/13a-proposals-approved-edited-or-rejected.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/7d-summaries-and-actions-on-the-dashboard.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/7e-transcripts-update-member-priorities.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Since `15c`, the PM keeps his commitments from the CLI. The pages still show them as proposals:
- **The dashboard lists an approved item as "approved"** under its meeting (`7d`, `13a`), with the proposal's owner and due. It never shows the commitment's state, the PM's own due or owner (`due_set`, `owner_set`), or whether a date has passed. `15c` only leaves out the items he closed.
- **An approved item disappears when its meeting leaves the page's window.** The digest lists the meetings summarised since the last render, at most 7 days back. A commitment due next month is not on the page after a week.
- **Spoken promises are never on a page.** `7d` lists action items only.
- **Team Activity shows the proposal's owner and due** and says nothing of the commitment's state, except that a withdrawn one is hidden (`15c`).
- **A proposal card does not say it is a commitment.** `proposal show` of an item marked done still reads only "approved" (15a promised 15d "the cards show a commitment's state").
- **A wrapper can record "…".** `15c`'s card actions and the reject refusal carry the literal `"…"` where the PM types a value, and the JSON does not say which word that is (`deferred-work.md`, "Surfaced by the story-15c review").

15d changes both pages, the personal one and each project's; there is no separate brief. FR-09's "Morning Briefing" is the personal `daily_dashboard.md`, and story 5 delivers pages to Telegram later.

**Approach:**
- **One block of open commitments** on each page, inside Time-Critical Activities, read from the ledgers, not bound to the page's window: soonest due first, past due only by 15c's rule, at most 10, then "and N more" with the exact `pm-ai commitment list` commands.
- **Under each meeting,** the approved items give way to one line counting that meeting's commitments, with its `pm-ai commitment list … --meeting` command. Waiting items stay proposals, as today.
- **Team Activity** shows the commitment's owner, due and state on an approved item's line. A withdrawn one stays hidden.
- **Proposal cards** of an approved item carry the commitment's state and its `pm-ai commitment show` command.
- **Placeholders a wrapper can recognise:** `<note>`, `<when>`, `<name>` and `<what>` in place of `"…"`, with a `fill` list in the JSON saying where they are, and the CLI refusing any of them typed as a value.

## Boundaries & Constraints

**Always:**

### Prerequisites and what is decided already

- **Built and relied on:**
  - `15a`: `Commitment`, `Due` (`domain/commitments.py`), `CommitmentLog`, `parse_ledger`, `fold` (`core/commitment_log.py`);
  - `15c`: `Details` (`core/commitment_log.py:625`: version, mark in force, `due_by_pm`, `owner_by_pm`, `changed_at`), `past_due` and the card's state and due words (`core/commitment_cards.py`), `list`'s order (`app/commitments.py:1972`), `read_ledger` (`:740`), `LEDGER_READ_FAILURES` (`:765`) and `_unreadable_words` (`:769`), the `pm-ai commitment` commands, and 15c's page rules (`pipelines.py:2676`, `:2782`, `:3162`);
  - `7d`: the meeting digests, `MAX_DIGEST_ITEMS`, `EXCERPT_LIMIT`, `escape` and `cut_at_word`, `shown_elsewhere`;
  - `7e` and `23g`: Team Activity, `IssueNotes` (`items`, `confidential`, `confidentiality_unknown`, `links_unread`), `resolve_label` with the saved alias table, `spoken_numbers` (`member_updates.py:243`);
  - `13a`: `open_action_items`, the proposal card, `ProposalDesk`;
  - `23d`: the project page's scope wall (two renderers).
- **Decided before 15d, not reopened:**
  - past due only when the commitment is open, its due has a date, the date is not estimated, and it is before today in the display zone (15c). This includes a date the PM set;
  - an estimated date is shown as "(meeting day estimated)" and is never called past due (15a, 15c);
  - a commitment's state is never a proposal state (AD-14);
  - an action item on a confidential issue never shows its text on a project page, and the page shows no more about a confidential issue than Team Activity does (Andrei, 2026-10-20, `7d`; `IssueNotes`' docstring, `team_activity.py:178-190`, which keeps that sentence);
  - the project page reads project-scope data only (AD-25, `23d`).
- **15b is not relied on.** Every read goes through `CommitmentLog.read` (or 15c's `read_ledger` by path at a terminal). No index is read.

### Which ledgers each page reads

- **The personal page** reads the personal ledger and each enrolled project's (`daemon.watched`), as its digests read those scopes (AD-38's direction, project into personal). Never a `people` scope.
- **A project page** reads that project's ledger only. Never the personal one (AD-25).
- **One read per scope per render.** The meeting lines, the block and Team Activity share it: `_item_decisions` takes the folded ledger as an argument instead of reading it. 15c reads a project's ledger twice per render (`_digests_over` and `_item_decisions`); 15d reads it once.
- **Page scope order,** used wherever the page names scopes or prints per-scope commands: personal first, then projects by id, ascending.
- **A project no longer enrolled** whose approved commitment-type proposals the store still holds (the catch-up's `proposal_index_at(states=approved)`, placed by `scope_of`) gets one ledger note on the personal page: `` `project:<id>` is no longer enrolled, so its commitments are not listed. `` No command is named: `pm-ai commitment list --scope project:<id>` is refused for a scope that is not enrolled (15c, `not_enrolled`). Its ledger is not read.

### The block of open commitments

- **Where:** in Time-Critical Activities, after the day's meetings and before the meeting summaries. No new heading: CAP-9's four sections stay four.
- **What it lists:** the open commitments of the scopes read (both action items and spoken promises, Andrei, 2026-10-27), whatever the meeting's age. Open is 15c's meaning: not `fulfilled`, `broken` or `withdrawn`, so `pending`, `altered`, and story 16's `unknown` and `error`.
- **Left out of the lines:**
  - **on a project page, a commitment Team Activity shows under an issue** (its root is in `issue_notes.items`). One item is never shown twice on a page: 7d's rule for the digest, applied to the block. They are counted in one line after the others: `- 2 open commitments shown under #12, #14 in Team Activity`;
  - **a root in `malformed_roots`:** see "Ledger edge cases".
- **Order:** 15c's `list` order for open ones, across every scope the page reads: a date first, earliest first; then `unresolved`; then `none`; ties by the meeting's start, newest first, a line with no start after those with one, then the commitment id. The key moves from `app/commitments.py` to `core/commitment_cards.py` as `open_order`, and both call it. Commitments whose text is withheld for a confidential issue (below) come after all others, by the meeting's start, newest first, so their place says nothing of their due.
- **At most 10 lines** (`MAX_PAGE_COMMITMENTS = 10`, `core/rendering.py`, beside `MAX_MEETING_DIGESTS`). Withheld lines count toward the 10.
- **One line per commitment:**

  ```
  - **due Thu 22 Oct, past due** Sam Rivera: send the cache TTL numbers ("by Thursday") — from Example Weekly Sync-up, Mon 19 Oct (`project:alpha`)
  - **due Fri 30 Oct (meeting day estimated)** Alex Morgan: book the load test — from Example Weekly Sync-up, Mon 19 Oct
  - **due Fri 30 Oct, set by you** said by Sam Rivera: share the draft — altered on Sat 24 Oct — from Example Weekly Sync-up, Mon 19 Oct
  - **no due date worked out from "next sprint"** Alex Morgan: migrate the cache — from Example Weekly Sync-up, Mon 19 Oct
  - **due cleared by you** unassigned: write the notes — from Example Weekly Sync-up, Mon 19 Oct
  ```

  1. **The due, in bold:** `page_parts.block_due` (below).
  2. **Who and what:**
     - an action item: `<owner>: <what>`, or `unassigned: <what>` with no owner (the digest's word);
     - a spoken promise: `said by <speaker>: <what>`, with `, owner <name>` when the PM set an owner; with neither a speaker nor an owner, `<what>` alone, never `said by : …`;
     - then the due as said, in quotes, only when a date was worked out from it: the due has a date and `due_by_pm` is false. An `unresolved` due already quotes the words in its due words, and a PM's date or a cleared due quotes nothing;
     - a `what` 15a cut ends with "…".
  3. **The state,** `page_parts.block_state`, between em dashes, only when it is not empty.
  4. **The origin,** mirroring 15c's `_origin` branches:
     - title and start on the line: `from <title>, <start's day>` (an empty title reads `(untitled)`);
     - otherwise, a meeting id: `from meeting <id> (meeting record unavailable when it was approved)`;
     - no meeting id: no origin;
     - on the personal page, a project commitment adds its scope in code.
  5. **Text** (what, names, the due as said, the title) is folded to one line, cut at a word to `EXCERPT_LIMIT`, then escaped (`23f`'s order).
- **After the lines, in this order:**
  1. **Shown under an issue** (project page; above).
  2. **More:** when more open commitments exist than lines: `- and 1 more open commitment: ` or `- and N more open commitments: `, then `` `pm-ai commitment list --scope <scope>` `` for each scope with one left out, comma-separated, in page scope order.
  3. **The next step:** `- to mark or correct them: ` then `` `pm-ai commitment review --scope <scope>` `` for each scope with an open commitment, in page scope order.
  4. **Closed in the window** (below).
  5. **Ledger notes** (below).
- **The command cap.** A per-scope list holds at most 3 commands (`MAX_PAGE_SCOPE_COMMANDS = 3`). With more scopes, the list is one command without `--scope` (`pm-ai commitment list`, `pm-ai commitment review`), which covers personal and every enrolled project (15c). This is the one exception to 15c's rule that every printed command carries `--scope`, made so a page with many projects stays readable.
- **No commitment id, version or `--if-version` in the block.** A page is read hours after it is written; a version on it would be stale, and the PM should not copy an id. `commitment review` walks the open ones and `commitment list` prints each card with its exact commands.
- **None open:** one sentence naming only the scopes that were read: `No open commitment in personal or project:alpha.` (in page scope order, joined with commas and a final "or"); on a project page `` No open commitment in `project:<id>`. `` When no ledger could be read, the sentence is left out and the notes say why. The closed line and the notes follow when they apply.

### The words: `page_parts` (one place)

`core/commitment_cards.py` gains `page_parts(commitment, details, *, now, zone) -> PageParts`. Every word below is built there, from the same functions the card uses (`_state_words`, `_due_heading`, `past_due`). `rendering.py` and `member_updates.py` only compose and escape. `PageParts` has these fields, each listed with every value it takes:

- **`block_state`** (the block line's state):
  - `""` for `pending`;
  - `altered on <date>`;
  - `pm-ai could not check` (`unknown`), `a check failed` (`error`);
  - each followed by ` by telemetry` when `state_source` is telemetry.
- **`block_due`** (the block line's bold due):
  - `due <date>`, then ` (meeting day estimated)` when estimated, `, set by you` when `due_by_pm`, `, past due` when `past_due`;
  - `due cleared by you`: the due is `none` and `due_by_pm` is true (he cleared one that was said or set);
  - `no due date worked out from "<words>"`, `no due date worked out` (unresolved, without and with words said);
  - `no due date said`.
- **`row_state`** (a Team Activity row's state, appended as `; <row_state>`):
  - `""` for `pending`;
  - `commitment done on <date>`, `commitment broken on <date>`, `commitment altered on <date>`;
  - `commitment: pm-ai could not check`, `commitment: a check failed`;
  - each followed by ` by telemetry` when telemetry set it.
- **`row_due`** (what follows `due as said in the meeting: <words>` on a row, or stands alone):
  - `(<date>)`, `(<date>, meeting day estimated)`;
  - `(set by you: <date>)`; with nothing said, the row says `due set by you: <date>` instead;
  - `due cleared by you`, standing alone in place of the said words' date;
  - `, past due` appended after the parenthesis when `past_due`.
- **`past_due`:** 15c's `past_due`.
- **`said`:** the due as said, or `None`; `quote_said` is true exactly when a date was worked out from it (`due.date` is set and `due_by_pm` is false).
- **Today's `_row` date words for a waiting item's line** (`member_updates.py:976-991`, ` (meeting day estimated)`) also move to `core/commitment_cards.py` as `said_due_words(day, estimated)`, so one module holds every such literal.
- **An architecture check** (`tests/architecture/test_layering.py` or a new `test_page_words.py`): the source of `pm_ai/core/member_updates.py` and `pm_ai/core/rendering.py` contains none of `set by you`, `past due`, `meeting day estimated`, `could not check`, `by telemetry`.
- **Broken and past due on a project page (Andrei, 2026-10-27).** Team Activity rows are grouped by member, so a row's "broken" or "past due" would read as a per-person failure record (AD-25). Broken and past due appear on the personal page only:
  - **Team Activity rows** print the row states in `TEAM_ROW_STATES = frozenset({"done", "altered"})` and no `, past due` (`TEAM_ROW_PAST_DUE = False`), both constants in `core/commitment_cards.py`. A broken row, and a row past its date, show no state or past-due words; `unknown` and `error` rows show none either;
  - **the project page's block** leaves `, past due` off `block_due` (`PROJECT_PAGE_PAST_DUE = False`), and its closed line leaves out the broken count. Order is unchanged, so a past date still sorts first;
  - **the personal page** shows both, as above.

### Under each meeting (the digest, `7d` and `13a` amended)

- **Waiting items stay proposals.** They are listed as today: `owner: what (due), to review by <date>`, in the summary's order, with `to review: pm-ai proposal review --scope … --meeting …`. Nothing is written to a ledger while an item waits (15a).
- **An approved item is "recorded"** when the ledger holds a commitment for `root_of(proposal_id)`. A replacement `<root>_e<n>` is recorded through its root.
- **A recorded approved item is no longer listed under its meeting.** It is in the block (open) or the closed count. 15c's leaving out of closed ones is subsumed.
- **One line per meeting with commitments,** after the items. It counts the commitments of that scope's ledger whose `pending` line names this meeting, action items and promises, open and closed (a copy skipped as a duplicate is not counted):
  - one: `- 1 commitment from this meeting, open: ` or `- 1 commitment from this meeting, closed: `;
  - several: `- 3 commitments from this meeting, all open: `, `…, none open: `, or `…, 1 open: `;
  - then `` `pm-ai commitment list --scope <scope> --meeting <id> --state all` ``;
  - no line when the meeting has none, or when its scope's ledger could not be read.
- **An approved item not recorded** (the approval's write failed, or the catch-up has not run) stays in the list as `owner: what (due), approved, not yet recorded as a commitment: ` then `` `pm-ai proposal approve <id> --scope <scope>` ``. `<id>` is the approved proposal's own id: the replacement `<root>_e<n>` when an edit made it, which is the id `approve` writes the line for (15a's repair).
- **`shown_elsewhere` applies** to every item listed under a meeting: waiting items, unrecorded approved items, and approved items listed because the ledger could not be read. Each is counted "shown under #N" instead, as today.
- **The overflow line** counts what is listed: waiting items, plus approved ones only in the two forms above. It names `--state staged` when every hidden item is waiting, and `--state open` otherwise. The pipeline sets this (`MeetingDigest.hidden_waiting_only`, default `False`, which keeps today's `--state open`).
- **"no open action item"** is printed only when the meeting has no item listed, none hidden, none under an issue, none unreadable, and no commitment line.

### Team Activity (`7e`, `15c` amended)

- **The ledger join is independent of the proposal read.** `_item_decisions` applies the withdrawn hiding and the commitment join from the ledger even when `proposals_of_meetings` fails: that failure still shows rejected and edited items as recorded (13a), and never unhides a withdrawn one. Today it returns early (`pipelines.py:3181-3182`), before the withdrawn hiding.
- **A withdrawn item stays hidden,** as 15c decided.
- **An approved action item recorded in the ledger** keeps its row under its issue, and the row shows the commitment, not the proposal:
  - **the owner** is the commitment's owner (the PM's `owner_set` when he set one), resolved to a member by 7e's rule, as the row resolves the proposal's owner today; unresolved stays `action, unassigned`;
  - **the due:** `due as said in the meeting: <words>` as today, then `row_due`; with nothing said, `row_due` alone. The proposal's said due is no longer resolved a second time by `_row`;
  - **the state:** `; <row_state>` when not empty, subject to `TEAM_ROW_STATES` (Andrei, 2026-10-27). Done lines stay, as 15c decided: a line is a note of what the meeting said.
- **A row on a confidential issue** is unchanged: kind, meeting and marker only, no owner, due or state.
- **A waiting item's row** is unchanged (its date words now from `said_due_words`).
- **Joined at read time** from the one ledger read. `ItemDecision` gains `commitment: ItemCommitment | None` (owner, `row_due`, `row_state`), built from `page_parts`.

### Who is named, and what is withheld

- **The personal page** shows names as recorded (the owner as said or typed, the speaker as said), as `7d` shows owner labels there. Nothing is withheld on the personal page (`7d`).
- **A project page** shows a person only when 7e's rule resolves the name to a member (the saved alias table, or an exact display name or username), as its digest does:
  - an owner that resolves to no one shows `unassigned` for an action item; on a promise, `, owner <name>` is dropped;
  - a speaker that resolves to no one shows `said by someone in the meeting: <what>`. These words differ from Team Activity's `said in the meeting` marker, which says how an issue link was found;
  - with no meeting id on the line, the meeting-scoped alias rows are not consulted: only the project-wide rows, then an exact name (`resolve_label` takes `meeting: str | None`; `None` skips its step 1's scoped row);
  - this is the linking reader 15a assigned to 15d: a corrected alias applies to every past line;
  - **the alias-table sentence** (`7d`: "The saved alias table could not be read, so … matched by exact display name or username only") is printed when the table is unreadable and the block or the digest shows any owner or speaker to resolve.
- **Confidential issues on a project page (decided: stricter than 7d).** For each commitment on a project page, the issue numbers it names are `spoken_numbers` (7e's reader: `#142`, `issue 142`, `issue №142`, `задача 142`) over its `what` and its due as said, for action items and promises alike, whether or not 7e linked the item. Then:
  - **on a confidential issue:** its root is in `issue_notes.confidential`, or a number it names is an issue the GitLab record marks confidential. The line is only what Team Activity's `_row` shows for a confidential issue, the kind and the meeting: `- an action item on a confidential issue — from <title>, <day>` (or `a spoken promise on a confidential issue`). No due, owner, speaker, state or quote. It sorts last (above);
  - **confidentiality unknown:** `issue_notes.links_unread` (action items); or it names any number while the GitLab record cannot be read (`confidentiality_unknown`); or it names a number the readable record does not hold (an issue absent from the record is treated as unknown). The text and the due as said are withheld: `**<block_due without the quote>** <who>: an action item whose text is withheld — <state> — from …`. The worked-out date, owner and state stay, as 7d keeps the owner and due;
  - **otherwise** the line is shown in full;
  - when anything is withheld, 7d's existing sentences saying why are printed once.
- **Accepted limit (detection):** only `#142`, `issue 142`, `issue №142` and `задача 142` (any case of the word) are read as issue numbers. `№142` alone, `#142й` (a letter right after the number), and a number written in words are not, so such a text is shown when nothing else withholds it. Widening 7e's reader would change which issues meetings link to, which is 7e's contract, not 15d's.
- **Never on a project page:** a ranking, grouping or count by member (AD-25). The block is ordered by due, and its closed line counts the whole project.

### Closed in the window

- **The line:** `- closed since <since>: 2 done (1 marked by you, 1 by telemetry), 1 broken (marked by you), 1 withdrawn (marked by you): ` then `` `pm-ai commitment list --scope <scope> --state all` `` for each scope with one, in page scope order, under the command cap. A state with 0 is left out. Each count names its source: `(marked by you)`, `(by telemetry)`, or both with their counts. Counts are over the whole page, never per person. On a project page the broken count is left out (above).
- **`<since>`** is the page's window start, Proactive Enablement's (`9b`), written as the digest heading writes it: `_local(since, tz=zone, now=now, dated=True)`, `2026-10-25 07:00`.
- **When it closed (`closed_at`):** the `occurred` of the first line, in fold order `(occurred, entry)`, of the run of lines since which the state has been terminal without a break. A second telemetry verdict, or the PM's mark confirming telemetry's, does not move it, so a closure is counted in one window only. `Details` gains `closed_at: datetime | None`, set by the fold.
- **Counted** when `since < closed_at <= now`. A `closed_at` after `now` (the clock stepped back) is counted as closed at `now`, so a closure is never lost from every page.
- **An undone done mark** is not a closure: the fold already left it out (15c).

### Ledger edge cases

- **A ledger that cannot be read** (`LEDGER_READ_FAILURES`) never fails the render. For that scope:
  - only the approved action items of meetings summarised since `<since>` show, under their meetings, as `owner: what (due), approved`, with the overflow naming `--state open` and no commitment line. Older approved items and spoken promises are not on the page;
  - Team Activity shows its rows as recorded, withdrawn ones included (15c's fail-open);
  - the note says so: `` The commitments of `<scope>` could not be read (<type name>), so they are not listed; only the approved action items of meetings summarised since <since> show, under their meetings: `pm-ai commitment list --scope <scope>`. `` The reason is 15c's `_unreadable_words` form, the exception's type name, never its text, so no path or user name reaches a page.
- **Lines the fold counted but could not use** (malformed, orphans, duplicates, conflicts, refused marks): one note per scope in 15c's words (`bad_lines_words`), then `` `pm-ai commitment list --scope <scope>` ``.
- **A root in `malformed_roots`:** not in the block. Under its meeting, its approved item reads `owner: what (due), approved; a line of commitments_log.md naming <root> does not parse; fix or remove it`. The `proposal approve` repair is not offered: 15a writes no `pending` line for such a root.
- **Approved items not recorded and not under a meeting on this page** (older meetings, and every spoken promise, which no digest lists): one note per scope counting them: `` 2 approved items in `personal` are not yet recorded as commitments: `pm-ai proposal approve <id> --scope personal` `` for each of the first 3, then `` and N more: `pm-ai proposal list --scope personal --state approved` ``. Found from the store's approved rows of the two commitment types (`proposal_index_at(states=approved)`, the catch-up's read) and the ledger read.
- **The same id in two ledgers** (a hand copy): the copy in the first scope in page scope order is the one shown and counted. The other copy is skipped everywhere on the page: the lines, "and N more", the closed counts, the meeting lines and the scopes named in the next-step and more commands. The shown line ends `(also in `<other scope>`)`, its only trace, matching 15c's `also_in`.
- **No scope is read** that the page does not show: a project page never opens the personal ledger, even to say it is unreadable.

### Telemetry and the PM's marks

- **The state's source is shown, never inferred.** "by telemetry" only when `state_source` is telemetry; "marked by you" only for the PM's mark. No word claims a check that did not run.
- **Before story 16** only a hand-written `verdict` line sets a telemetry state. Tests use one, as 15c's do.
- **Past due is shown, not acted on.** The page sends, marks and asks nothing (story 16 owns overdue).

### Proposal cards show the commitment (`13a` amended)

- **`proposal show` and `proposal list`** of an approved item whose type records a commitment (`action_item`, `implicit_commitment`): the full card gains one line after its origin:
  - recorded: `commitment: <block_state or "open">, <block_due>: pm-ai commitment show <root> --scope <scope>`;
  - not recorded: `commitment: not recorded yet: pm-ai proposal approve <id> --scope <scope> records it`;
  - its root in `malformed_roots`: `commitment: a line of commitments_log.md naming <root> does not parse; fix or remove it`;
  - not read: `commitment: the ledger could not be read (<type name>)`, or `commitment: not read within the bound`.
- **13d's compact card** in `proposal review` does not gain it (review walks waiting proposals).
- **In JSON,** the card gains `"commitment": {"commitment_id": string, "recorded": boolean | null, "state": string | null, "state_source": string | null, "past_due": boolean | null, "reason": string | null, "show": [words] | null, "repair": [words] | null}`, and `null` for any other proposal. `recorded`, `state`, `state_source` and `past_due` are `null` unless the ledger was read; `reason` holds the not-read or malformed sentence. `format` stays 1 (a field added).
- **A replacement** (`<root>_e<n>`) shows its root's commitment.
- **The read:** each listed scope's ledger once per command, through `read_ledger` (the store in the daemon, by path at a terminal, 15c's stated exception). Inside `proposal list`'s and `show`'s existing 1-second bound (`entry.py:660-661`): when the bound is reached before a scope's ledger is read, that scope's cards carry the not-read form with the reason `not read within the bound`, and the command still answers.

### Placeholders a wrapper can recognise (the 15c review finding)

- **Four placeholder words** replace `"…"` in every command word list that offers a value for the PM to type. `PLACEHOLDERS = ("<note>", "<when>", "<name>", "<what>")`, one tuple in `core/commitment_cards.py`:
  - `<note>`: the card's `altered … --note <note>`, and the reject refusal's `altered`;
  - `<when>`: the card's `due <id> <when>`, and the reopen command `altered … --due <when>`;
  - `<name>`: the card's `owner <id> <name>`, and the reopen command `altered … --owner <name>`;
  - `<what>`: the reject refusal's `proposal edit <id> --what <what>`.
- **One shape everywhere: a command and its fill.** `{"command": [words], "fill": [{"index": int, "name": "note" | "when" | "name" | "what", "required": boolean}]}`:
  - `command` is the words after `pm-ai`, as today; `index` is a position in that list, counted from 0 in the command as given;
  - a required placeholder is replaced by the PM's value. An optional one (`altered`'s note) is replaced, or removed together with the option word just before it (`--note`), which always precedes it;
  - a wrapper applies removals from the highest index down, so the indexes of the words before stay true;
  - `fill` is `[]` when nothing is to be filled.
- **Where the shape appears:**
  - each card action object: `"command"` and `"fill"` side by side, `fill` added;
  - **the reopen command,** now its own field on a closed commitment's `due` and `owner` actions: `"reopen": {"command", "fill"} | null`. Its `reason` becomes prose without a command: "you marked it broken on Sat 24 Oct; the reopen command reopens it with a due". The text card prints it as `due:       unavailable: <reason>` then `           reopen: pm-ai commitment altered prp_… --scope personal --due '<when>'`. The undo hint in a done commitment's reason stays a complete command with nothing to fill;
  - **the reject refusal:** `commitment.commands.<name>` becomes `{"command": [words], "fill": [...]}`, in both the read and the unreadable-ledger branches.

  `format` stays 1. The refusal's `commands` values change from a list to an object, which 15c's rule ("removing or renaming a field changes it") does not cover; no wrapper reads them yet (they shipped on 2026-10-26), and this is recorded in the Change Log.
- **Placeholders appear only in `command` word lists that carry a `fill` entry for them.** `reason` fields are prose and never contain a placeholder; `next` fields carry complete commands only (15c's `edit_refused` `next` holds the PM's own wording).
- **The text** shows the same words, quoted by `shlex.join` (`--note '<note>'`).
- **The CLI refuses a placeholder as a value.** The check, `is_placeholder(value)` in `core/commitment_cards.py`: let `v = fold_typed(value).strip().casefold()`, with one pair of surrounding `'` or `"` removed and stripped again. `v` is refused when it contains any of the four placeholder words, or equals `…` or `...`. Multi-word due words are joined with one space before the check. A refusal is a usage error (exit 2) on standard error, naming the value to type ("type the note itself, not <note>"); nothing is written.

  | Command | Checked |
  |---|---|
  | `commitment done`, `broken`, `withdrawn` | `--note` |
  | `commitment altered` | `--note`, `--what`, `--owner`, `--due` |
  | `commitment due` | the due words |
  | `commitment owner` | the name |
  | `proposal edit` | `--what`, `--owner`, `--due` |

  `--clear` takes no value and is not checked. In `commitment review`, the `u`, `o`, `a` (each of its questions) and note prompts ask again with the same reason. An empty answer keeps its meaning (Enter keeps, `-` clears).

### Bounds and cost

- **The render reads no meeting record for the block.** The title and start come from the `pending` line, as `commitment list`'s do.
- **Timed:** the `CommitmentLog.read` calls of one render, summed, at most 1 second, over a personal page reading a 5,000-line personal ledger and two 5,000-line project ledgers, and over a project page reading a 5,000-line ledger. Only those calls are timed, measured once per test.
- **No model call, no write, no master key beyond what the render already uses.**

### The architecture tests and the skip count

- **`EXPECTED_SKIPS` stays 6** (`tests/conftest.py:192`). No skipping test depends on 15d.
- **New, passing:**
  - `test_ad25_project_page_reads_only_its_own_commitment_ledger` (`tests/architecture/test_domain_invariants.py`): a project render over a store holding personal and another project's ledgers calls `read_commitments` for that project only;
  - the page-words check (above).
- **`.importlinter` unchanged:** `core/rendering.py` takes domain values (`domain/renders.py`); `core/member_updates.py` and `core/commitment_cards.py` are both in `core`, and `commitment_cards` imports nothing from `member_updates`; the pipelines are in `app`.

### No real data

Tests use `11d`'s fixture names (`Example Weekly Sync-up`, `Alex Morgan`, `Sam Rivera`), `project:alpha`, and `example.com` handles. Nothing reads `~/.pm-ai` or `~/.manager-ai`.

**Ask First:** none open. The four questions were decided by Andrei on 2026-10-27 (Design Notes, "Decided").

**Never:**

- **Pages:**
  - no commitment id, version or `--if-version` in the block or the meeting lines; only a repair names a proposal id;
  - no fifth heading;
  - no "past due" on an estimated, `unresolved` or `none` due, on a closed commitment, or on a date of today;
  - no "by telemetry" without a telemetry state, and no "marked by you" without the PM's mark;
  - no state or due word outside `core/commitment_cards.py`;
  - no item shown twice on one page;
  - no message, mark, verdict or question because a date passed (story 16).
- **Scopes:**
  - no personal ledger read by a project render;
  - no `people` scope read;
  - no per-member count, ranking or grouping on a project page;
  - no broken or past-due words on a project page.
- **Text:**
  - no more about a commitment on a confidential issue than Team Activity's row shows;
  - no withheld text's due as said, and no unresolved spoken name, on a project page;
  - no exception text on a page, only its type name;
  - no field shown unescaped or uncut.
- **The ledger:** no write of any kind. 15d only reads.
- **Proposals:** no proposal state changed or read as a commitment state (AD-14); waiting items stay proposals.
- **Placeholders:** no `"…"` in a command's words; no placeholder without its `fill` entry; no placeholder in a `reason` or `next`; no placeholder accepted as a value.
- **JSON:** no field removed or renamed.
- **Scope of this slice:** no index (15b), no telemetry (16), no Telegram delivery (story 5), no new command.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Block order | personal: open commitments due 22 Oct, 30 Oct (estimated), `unresolved`, `none`; today Sun 25 Oct | four lines in that order; the first "due Thu 22 Oct, past due"; the second "(meeting day estimated)", not past due | N/A |
| Cross-scope order | personal page; `project:alpha` holds dues 20 and 21 Oct, personal 23 Oct and 9 more later ones | the first two lines are `project:alpha`'s; "and 3 more open commitments:" names only `--scope personal` | N/A |
| Due today | due 25 Oct, today 25 Oct | "due Sun 25 Oct", not past due | N/A |
| PM's date past | `commitment due <id> 2026-10-20` on 25 Oct | "due Tue 20 Oct, set by you, past due" | N/A |
| Due cleared | due said "by Friday", then `commitment due <id> --clear` | "due cleared by you"; no quote | N/A |
| Quote only when worked out | due said "by Friday" resolved; then `commitment due <id> friday` | first "(\"by Friday\")" quoted; after, "due Fri 30 Oct, set by you", no quote | N/A |
| Old meeting | an open commitment from a meeting 30 days ago | listed | N/A |
| Promise | an approved promise said by Sam Rivera, no owner | "said by Sam Rivera: …" on the personal page | N/A |
| Promise, no speaker | a promise line with no speaker and no owner | "<what>" alone | N/A |
| No title | a `pending` line with a meeting id and no title | "from meeting mtg_… (meeting record unavailable when it was approved)" | N/A |
| No meeting | a `pending` line with no meeting id | no origin | N/A |
| Altered | marked altered on 24 Oct | "— altered on Sat 24 Oct —" | N/A |
| Telemetry open state | a hand-written `verdict state=unknown` | "— pm-ai could not check by telemetry —" | N/A |
| Cap, one more | 11 open | 10 lines; "and 1 more open commitment:" | N/A |
| Command cap | open ones in personal and 4 projects, 3 left out across 4 scopes | "and 3 more open commitments: `pm-ai commitment list`"; next step `pm-ai commitment review` | N/A |
| Next step | open ones in personal and `project:alpha` | "to mark or correct them:" with `--scope personal`, then `--scope project:alpha` | N/A |
| Closed line | inside the window: 1 PM done, 1 hand-written `verdict state=fulfilled`, 1 broken, 1 withdrawn | "closed since 2026-10-25 07:00: 2 done (1 marked by you, 1 by telemetry), 1 broken (marked by you), 1 withdrawn (marked by you):" and the `--state all` command | N/A |
| Closed before the window | one PM done before `since`, one inside; a telemetry verdict before `since` on a third, then a second verdict inside | "1 done (marked by you)"; the third is not counted (its `closed_at` is the first verdict's) | N/A |
| Clock stepped back | `closed_at` after the render's `now` | counted | N/A |
| Undone mark | marked done, then undone | listed as open; not in the closed line | N/A |
| None open | personal and `project:alpha` read, none open, nothing closed | "No open commitment in personal or project:alpha." | N/A |
| None open, one unreadable | personal read, `project:alpha` unreadable | "No open commitment in personal."; the unreadable note | stated |
| Project page scope | a project page; the personal ledger holds open commitments | only the project's; `read_commitments` called for that project only | N/A |
| One read | a project page with a summarised in-window approved item and a `member_updates.md` line linking it | `read_commitments` called once (on 7a097a7 the same fixture sees two calls) | N/A |
| Shown under an issue | project page; an open commitment whose root Team Activity shows under #12 | not a block line; "1 open commitment shown under #12 in Team Activity" | N/A |
| Meeting line | 2 approved items, one done, and 1 waiting | the waiting item listed; "2 commitments from this meeting, 1 open:" | N/A |
| Meeting line, all and none | 3 open; then 2 closed | "3 commitments from this meeting, all open:"; "2 commitments from this meeting, none open:" | N/A |
| Meeting line, one | 1 closed | "1 commitment from this meeting, closed:" | N/A |
| Waiting only | 3 waiting | listed as today, with the review line | N/A |
| Not recorded | an approved item with no `pending` line | "approved, not yet recorded as a commitment:" with `proposal approve <id> --scope …` | N/A |
| Not recorded, edited | approved, then edited (`<root>_e1` approved), no line | the repair names `<root>_e1` | N/A |
| Recorded, edited | approved, edited, recorded | not listed under the meeting; in the block | N/A |
| Malformed root | a malformed line names the root of an approved item | not in the block; under the meeting "a line of commitments_log.md naming <root> does not parse; fix or remove it"; no repair | stated |
| Old unrecorded | 4 approved items (old meetings or promises) not recorded | note: 3 `proposal approve` commands, "and 1 more:" `proposal list --scope … --state approved` | stated |
| Overflow | 7 waiting | 5 listed; "and 2 more" naming `--state staged` | N/A |
| Overflow, ledger unreadable | 4 waiting and 3 approved, ledger unreadable | 5 listed (4 waiting, 1 approved); "and 2 more" naming `--state open` | N/A |
| Ledger unreadable, personal page | `project:alpha`'s ledger raises `OSError` with a path | the note names `project:alpha` and `OSError`, no path; personal listed; alpha's in-window approved action items under their meetings | stated |
| Ledger unreadable, project page | the project's ledger cannot be read | Team Activity rows as recorded, withdrawn included; the block is the note only | stated |
| Not enrolled | approved commitment-type proposals of `project:gone`, not enrolled | personal page note naming `project:gone`, no command | stated |
| Bad lines | 1 malformed line and 1 refused mark | one note with both counts and `commitment list --scope personal` | stated |
| Same id twice | a hand-copied `pending` line in personal and `project:alpha`, open | one line, under personal, "(also in `project:alpha`)"; `project:alpha` not named in "more" or the next step for it; the meeting line counts it once | N/A |
| Confidential by link | project page; root linked to a confidential issue | "an action item on a confidential issue — from Example Weekly Sync-up, Mon 19 Oct", after the other lines; no due, owner or state anywhere | N/A |
| Confidential by number | project page; an unlinked action item "fix #142", #142 confidential | the same confidential form | N/A |
| Confidential in the due | project page; due said "before the #142 release", #142 confidential | the confidential form | N/A |
| Promise naming a confidential issue | project page; promise "issue 142 first", #142 confidential | "a spoken promise on a confidential issue — from …" | N/A |
| Links unread | project page; `member_updates.md` cannot be read | every action item's text and due as said withheld; dates, owners, states kept; the sentence printed | stated |
| GitLab unread | project page; the record cannot be read; a promise naming "#7" | text withheld, date kept; the sentence printed | stated |
| Number absent | project page; an item naming #999, not in the readable record | withheld as unknown | stated |
| Not detected | project page; "№142" alone, #142 confidential | shown (the accepted limit) | N/A |
| Personal page shows text | the same commitments on the personal page | text shown as said | N/A |
| Project owner unresolved | owner "Sam", no member, no alias | "unassigned: …"; a promise's ", owner Sam" dropped | N/A |
| Project speaker unresolved | promise said by an unmatched name | "said by someone in the meeting: …" | N/A |
| Alias unreadable | the alias table cannot be read; the block shows an owner | 7d's alias sentence printed | stated |
| No meeting id, alias | a meeting-scoped alias row for the name, the line has no meeting id | not used; the project-wide row or an exact name | N/A |
| Team Activity, done | an approved item under #142, marked done 26 Oct | the row ends "; commitment done on Mon 26 Oct" | N/A |
| Team Activity, broken | marked broken | no state words on the row | N/A |
| Team Activity, past due | due 22 Oct exact, open, today 25 Oct | no "past due" on the row | N/A |
| Project block, past due | project page; open, due 22 Oct exact, today 25 Oct | "due Thu 22 Oct", first in order, no "past due"; the personal page says "past due" | N/A |
| Project closed line | project page; 1 done and 1 broken in the window | "1 done (marked by you)"; no broken count; the personal page counts both | N/A |
| Team Activity, PM's owner | `commitment owner <id> "Alex Morgan"`, a member | "action for Alex Morgan" | N/A |
| Team Activity, PM's due | `commitment due <id> friday` | "(set by you: Fri 30 Oct)" | N/A |
| Team Activity, estimated | due against an estimated day | "(Thu 22 Oct, meeting day estimated)", never past due | N/A |
| Team Activity, withdrawn | marked withdrawn | hidden | N/A |
| Team Activity, proposals unreadable | `proposals_of_meetings` fails; one item withdrawn | still hidden; the done one still shows its state | stated |
| Team Activity, confidential | a done item on a confidential issue | kind, meeting and marker only | N/A |
| Proposal card | `proposal show <id>` of an item marked done | "commitment: done on …, due …: pm-ai commitment show <root> --scope …"; JSON `state: "fulfilled"`, `state_source: "pm"` | exit 0 |
| Proposal card, replacement | `proposal show <root>_e1` | its root's commitment | exit 0 |
| Proposal card, not recorded | no `pending` line | "not recorded yet"; `recorded: false`, `past_due: false`, `repair` set | exit 0 |
| Proposal card, unreadable | the ledger cannot be read | "the ledger could not be read (OSError)"; `recorded: null`, `past_due: null` | exit 0 |
| Proposal card, bound | the bound reached before the second scope's ledger | that scope's cards "not read within the bound" | exit 0 |
| Proposal list reads | `proposal list --json`, approved items in personal and `project:alpha` | `read_commitments` called once per scope; timed on a 5,000-line ledger within the 1-second bound | exit 0 |
| Proposal card, waiting | a waiting item | no commitment line; `commitment: null` | exit 0 |
| Placeholder in JSON | `commitment show <id> --json`, open | `actions.altered.command` holds `"<note>"`, `fill` `[{"index": 6, "name": "note", "required": false}]`; `actions.due` holds `"<when>"`, required | exit 0 |
| Reopen command | `show --json` of a broken one | `actions.due.reopen.command` holds `"--due", "<when>"` with its `fill`; `reason` has no placeholder | exit 0 |
| Refusal, open item | `proposal reject <id> --json` of an approved open item | `commitment.commands.altered.command` holds `"<note>"`, `fill` names it; `commands.edit` holds `"<what>"` | exit 3 |
| Refusal, unreadable | the same with the ledger unreadable | the same shapes for `withdrawn` and `altered` | exit 3 |
| Placeholder as a value | `commitment due <id> '<when>'`; `altered <id> --note '<note>'`; `altered --due '<when>'`; `altered --owner '<name>'`; `proposal edit <id> --what '<what>'` | each a usage error naming the value; nothing written | exit 2 |
| Literal ellipsis | `commitment done <id> --note "…"`; `--note "..."`; `--note '"<NOTE>"'` (case and quotes) | usage error | exit 2 |
| Review placeholder | `u` then `<when>`; `o` then `<name>`; `a`, wording `<what>` | each asked again with the reason | continues |
| Timed render | as in Bounds | the summed reads within 1 s | N/A |

</frozen-after-approval>

## Code Map

Anchors checked against `origin/main` at `7a097a7`.

- `pm_ai/app/pipelines.py:2504` `prepare_dashboard`; `:2676` `_meeting_digest` (`:2730` 15c's skip); `:2782` `_digests_over`; `:2823` `_LEDGER_READ_FAILURES`; `:2827` `_commitment_ledger`; `:2842` `_closed_commitments`; `:2857` `personal_meeting_digests`; `:2896` `project_meeting_digests`; `:3096` `_issue_notes`; `:3162` `_item_decisions` (`:3181-3182` the early return, `:3189` its second ledger read)
- `pm_ai/core/rendering.py:195` `MAX_MEETING_DIGESTS`; `:199` `MAX_DIGEST_ITEMS`; `:202` `EXCERPT_LIMIT`; `:378` `render_dashboard`; `:449` `render_project_dashboard`; `:804` `_digest_block` (`:839` the alias sentence); `:940` `_digest_lines` (`:987-991` the approved word, `:995-1001` the overflow, `:1015-1022` "no open action item"); `:2092` `_local`
- `pm_ai/domain/renders.py:83` `DigestItem`; `:121` `MeetingDigest`; `:209` `MeetingDigests`; `:260` `ProjectMeetingDigests`
- `pm_ai/core/member_updates.py:243` `spoken_numbers`; `:892` `resolve_label`; `:953` `_row` (`:976-991` the due part); `:1000` `ItemDecision`; `:1015` `_decided`
- `pm_ai/core/team_activity.py:137` `bare_references`; `:169` `IssueNotes` (`:178-190` the docstring kept)
- `pm_ai/core/commitment_log.py:625` `Details`; `:659` `CommitmentLedger`
- `pm_ai/core/commitment_cards.py:157` `past_due`; `:219` `_state_words`; `:239` `_due_heading`; `:294` `_origin`; `:402` `closed_reason` (`:416` its `"…"`); `:426` `build_card` (`:448`, `:465` the `"…"`); `:523` `card_json`; `:740` `bad_lines_words`
- `pm_ai/app/commitments.py:740` `read_ledger`; `:765` `LEDGER_READ_FAILURES`; `:769` `_unreadable_words`; `:1077` the review commands per open scope; `:1965` `_due_key`; `:1972` `_ordered`
- `pm_ai/app/proposals.py:221` `_card`; `:889-890` the reject refusal's `"…"`
- `pm_ai/core/proposal_cards.py:94` `CardAction`; `:103` `Card`; `:142` `command_line`; `:151` `build_card`; `:398` `LIST_STATES`
- `pm_ai/storage/service.py:1911` `proposal_index_at`; `:6745` `proposals_of_meetings`
- `pm_ai/app/entry.py:660-661` `proposal list` and `show` bounds; `:1923` `_proposal_reader`
- `tests/conftest.py:192` `EXPECTED_SKIPS = 6`
- `prd.md:3` version; `:428` FR-09's Time-Critical consequence; `:966` the 15c Decisions Log entry
- `derivation-services.md:123` the stated exception; `:127` the `CommitmentLog` row
- `ARCHITECTURE-SPINE.md:405` AD-25's heading; `:417` its per-member paragraph

## Tasks & Acceptance

**Execution:**
- [ ] **The words.** `core/commitment_cards.py`: `PageParts` and `page_parts`, `said_due_words`, `TEAM_ROW_STATES`, `TEAM_ROW_PAST_DUE` and `PROJECT_PAGE_PAST_DUE`, `open_order` (moved from `app/commitments.py`, which then calls it), `PLACEHOLDERS`, `is_placeholder`. Tests first: every value of every field, past due, the order equal to 15c's on its matrix rows.
- [ ] **The fold.** `core/commitment_log.py`: `Details.closed_at`. Tests: a second verdict, a confirming mark, an undone mark, file-order swaps.
- [ ] **Placeholders.** `core/proposal_cards.py`: `Fill` and `CardAction.fill`, `reopen`; `build_card` and `closed_reason` with the placeholder words; `card_json` and the reject refusal (`app/proposals.py:889-890`) emitting `{command, fill}`. The value check in the commitment desk's inputs and `ProposalDesk.edit`, and in `review`'s questions (`surfaces/cli/dispatch.py`).
- [ ] **The page values.** `domain/renders.py`: `PageCommitment`, `PageCommitments` (lines, under-issue count and iids, left out per scope, scopes with open ones, closed counts by state and source, notes); `MeetingDigest` gains `commitments`, `open_commitments`, `hidden_waiting_only`; `DigestItem` gains `unrecorded` and `malformed`.
- [ ] **The pipeline.** `app/pipelines.py`: one ledger read per scope per render; the block with the owner rule, `spoken_numbers` withholding, duplicates and notes; `_meeting_digest` by `root_of`; `_item_decisions` taking the ledger, joining independently of the proposal read; the store read for unrecorded and not-enrolled notes.
- [ ] **The render.** `core/rendering.py`: `MAX_PAGE_COMMITMENTS`, `MAX_PAGE_SCOPE_COMMANDS`, the block after the day's meetings on both pages, the meeting line, the unrecorded and malformed lines, the overflow's `--state`, the alias sentence's condition; `render_dashboard` and `render_project_dashboard` gain `commitments: PageCommitments | None = None`. With `None` and today's digests, the output is byte-identical to today's.
- [ ] **Team Activity.** `core/member_updates.py`: `ItemCommitment`, `_decided` and `_row` from `page_parts`, `said_due_words`; `resolve_label(meeting: str | None)`.
- [ ] **Proposal cards.** `core/proposal_cards.py` and `app/proposals.py`: the commitment line and JSON field, one read per scope, the bound.
- [ ] **Tests** (each matrix row, plus):
  - `tests/core/test_commitment_cards.py`: `page_parts`, `open_order`, `fill`, `is_placeholder`;
  - `tests/core/test_rendering_sections.py`, `test_project_rendering.py`: the block, the meeting lines, the notes. **The golden** `tests/core/goldens/dashboard_with_summaries.md` is unchanged: its test passes digests built directly with `approved=True` and no `commitments`, and the render with `commitments=None` is byte-identical. A new golden, `dashboard_with_commitments.md`, records the block, the meeting line and each note;
  - `tests/core/test_member_updates.py`: rows with a commitment, broken and past due left off;
  - `tests/slice/test_dashboard_slice.py`: page rows, the one-read count (the same fixture asserted at 2 reads against `_item_decisions` as it is on 7a097a7, then 1), the timed render;
  - `tests/slice/test_commitment_commands.py` and `test_proposals.py`: placeholder and proposal card rows;
  - `tests/architecture`: the AD-25 test and the page-words check.
- [ ] **Tests the change breaks, with the new expectations:**
  - `tests/slice/test_commitment_commands.py:1181` (`test_the_dashboard_leaves_out_closed_commitments`): every recorded approved item is absent from the meeting; the meeting line says "4 commitments from this meeting, 1 open"; the altered one is a block line; the waiting one is still listed;
  - `:1214` (`…fails_open_on_an_unreadable_ledger`): the items listed as approved, as today, and the page carries the note naming the scope and the type name;
  - `:1222` (`test_team_activity_hides_a_withdrawn_item`): the calls at `:1240` and `:1243` pass the folded ledger (`commitments=CommitmentLog(machine.storage).read(ALPHA)`, and `commitments=None`); `:1241` still asserts `hidden`; `:1242` becomes: `decisions[ids[1]].commitment.row_state == "commitment done on …"`, and `decisions[ids[2]].commitment.row_state == ""` (broken is not shown on a project page); `:1244` unchanged;
  - `:1336` (`…shows_every_line_when_the_ledger_cannot_be_read`): the stub at `:1351-1352` and the monkeypatch at `:1354` are dropped; the call at `:1355` passes `commitments=None`; `:1356` unchanged;
  - `tests/slice/test_proposals.py:303` (`test_approve_prints_the_approved_card_and_the_dashboard_shows_it`): the page shows the item as a block line and "1 commitment from this meeting, open", not "approved" under the meeting;
  - `tests/slice/test_proposals.py:1285` (`test_the_dashboard_lists_waiting_then_approved_capped_at_five`): the 4 waiting items are listed, no overflow line, and "3 commitments from this meeting, all open";
  - `tests/slice/test_dashboard_slice.py:2306` (`test_7d_open_items_show_approved_…`): its approved item is staged directly with no ledger line, so it reads "approved, not yet recorded as a commitment:" with the `proposal approve` command.
- [ ] **The contract.** Apply the amendments in Design Notes, in this order: `prd.md` through bmad-prd; then bmad-spec re-derives `derivation-services.md`; then `ARCHITECTURE-SPINE.md` through the architecture skill.

**Acceptance Criteria:**
- Given open personal commitments due before today, today and later, one estimated, when the personal page renders, then they are listed soonest due first and only the exact earlier date is called past due.
- Given an approved action item marked done inside the window, when the page renders, then it is not listed under its meeting, the meeting's line counts it, and the closed line counts it as marked by the PM.
- Given a project page and a commitment naming a confidential issue, when it renders, then nothing about it beyond its kind and meeting appears on the page.
- Given a project's ledger that cannot be read, when the personal page renders, then the note names that project and the exception's type, and no path.
- Given `commitment show <id> --json`, when a wrapper fills every required `fill` entry and drops the optional ones, then each command runs; and run as given, each is refused with exit 2 and writes nothing.
- Given the change, when `uv run pytest` runs, then everything passes with 6 skips.

## Spec Change Log

- **2026-10-26, created** from story 15's entry (15d: "the dashboard and the morning brief show commitments and their state in place of approved proposals"), 15a's promise to 15d (the cards show a commitment's state; 15d links names; estimated dates shown as estimated), 15c's page rules and accepted limits, the 15c review finding on `"…"` placeholders (Andrei: it belongs in 15d), and Andrei's standing rules (the CLI is an API layer; no memorised ids; no invented evidence; plain language). Three Ask First questions open.
- **2026-10-26, review (three lenses: adversarial, edge-case, verification gap), fixes decided by the coordinating session: frozen block edited (recorded here).** Ask First 1-3 and a new Q4 (Team Activity's broken and past due on a project page) are with Andrei and not decided; Q4's two answers are written as one constant.
  - **Confidential text, stricter:** a commitment on a confidential issue shows only the kind and its meeting, as Team Activity's row, and sorts last; `spoken_numbers` over the what and the due as said, for every item and promise, linked or not; a number absent from the readable record is unknown; withheld text withholds the due as said; the detection limit stated.
  - **No item twice:** the project block leaves out items Team Activity shows, counted "shown under #N"; `shown_elsewhere` covers unrecorded and fail-open approved items.
  - **Names:** "said by someone in the meeting"; an unresolved promise owner dropped; no speaker shows the text alone; no meeting id skips meeting-scoped aliases; the alias sentence when the block names anyone.
  - **Words:** `page_parts` fields spelled out per variant, all in `commitment_cards.py`, with an architecture check; "due cleared by you"; the quote only when worked out; plurals and the meeting line's all and none; the origin's branches; `since`'s format and the scope order; the command cap of 3.
  - **Counts:** `closed_at` from the fold, the interval, a stepped-back clock; one copy of a duplicated id; "none open" names the scopes read; a not-enrolled project's note (with no command, since 15c refuses it).
  - **Ledger edge cases:** malformed roots; replacements by `root_of`, the repair naming the replacement; unrecorded old items and promises in a note; the unreadable note says what the render does and gives the type name only; the Team Activity join independent of the proposal read.
  - **Placeholders:** one `{command, fill}` shape, the reopen command structured and reasons placeholder-free, the refusal's `commands` values become objects (format stays 1; no reader yet), indexes into the command as given and removals highest first, the check named with its table.
  - **Proposal cards:** `past_due` null unless read; the bound's not-read form.
  - **Bounds:** only the summed `CommitmentLog.read` calls are timed.
  - **Tests and anchors:** the broken tests listed with new expectations; matrix rows added; `_proposal_reader` at `entry.py:1923`, `Details` at `commitment_log.py:625`; AD-25's heading text; the order key's reason.

- **2026-10-27, Andrei's four answers: DECIDED 2026-10-27 (Andrei), frozen block edited (recorded here).** (1) Placement A: the block and the per-meeting count line. (2) Spoken promises A: both pages, withheld on a project page by the confidential rules. (3) Proposal cards A: they show the commitment's state (recommended, not objected to). (4) No broken and no past due on a project page's Team Activity, done and altered only; broken and past due on the personal page only, so the project block omits `, past due` (`PROJECT_PAGE_PAST_DUE = False`) and its closed line the broken count. `TEAM_ROW_STATES = {done, altered}`, `TEAM_ROW_PAST_DUE = False`. Ask First closed; matrix rows updated and two added.

## Design Notes

**Readiness check (2026-10-26, against `origin/main` at `7a097a7`).** Verdict: **ready; nothing blocks the spec.**
- **Built as 15d needs:** `CommitmentLog.read` and `read_ledger`; `Details` with `due_by_pm`, `owner_by_pm` and the mark in force; `past_due` and the card's words as pure functions; 15c's order; `IssueNotes`; `resolve_label`; `spoken_numbers`; `proposal_index_at`.
- **Found:**
  - **There is no separate morning brief.** FR-09 names `daily_dashboard.md` the Morning Briefing; story 23's slices build it.
  - **The digest is bound to a window** of at most 7 days (`9b`), so commitments kept under their meetings alone would leave the page after a week (decided: 1).
  - **15c reads a project's ledger twice per render.** 15d reads once.
  - **`7d` deliberately lists no spoken promise** ("so one item found both ways is not shown twice"). Once approved, each is its own commitment (decided: 2).
  - **The `"…"` placeholders are in four places,** not the two the deferral names: the card's `altered`, `due` and `owner` actions (`commitment_cards.py:448`, `:465`), `closed_reason`'s reopen command (`:416`), and the reject refusal (`proposals.py:889-890`). 13a's own `edit` action fills in the current values and has none.
  - **`_item_decisions` returns before the withdrawn hiding** when the proposal read fails (`pipelines.py:3181-3182`), so a withdrawn row shows then. 15d joins the ledger first.
  - **A not-enrolled project's commitments cannot be named with a command:** 15c refuses `commitment list --scope` for it. The note names the project only.

**Why a block and not the meeting lines.** Kept under its meeting, a commitment stays on the page only while its meeting is in the window, so "what is due" would lose everything older than a week. The block answers it from the ledger; the meeting line keeps the link from a meeting to what it produced.

**Why no id or version on the page.** A page is a snapshot read later. A version would be refused as `changed` after any mark; an id asks the PM to copy something he should not have to read.

**Why the order key moves to core.** One order, no drift: the page and `commitment list` call the same function, so the two can never disagree on which commitment comes first.

**Why the confidential form is Team Activity's.** The page must say no more about a confidential issue than Team Activity does (`IssueNotes`). A due, an owner or a state is more than Team Activity's confidential row shows, and on a team page an owner beside a confidential item is itself disclosure.

**Why placeholders are refused as values.** The `fill` list lets a wrapper do it right; refusing the words makes a wrapper that does it wrong write nothing instead of "<note>" in an append-only ledger, where it cannot be removed.

**Decided (Andrei, 2026-10-27).** The Ask First is closed:
1. **Placement: A.** One block of open commitments, from the ledgers, whatever the meeting's age, and a count line under each meeting; waiting items stay. Rejected: B, in place under each meeting only (window-bound), and C, both (each commitment twice).
2. **Spoken promises: A.** Both pages list them; a project page withholds the text on a confidential issue. Rejected: personal page only, or neither.
3. **Proposal cards: A.** `proposal show` and `list` of an approved item show the commitment's state and its `commitment show` command (recommended; Andrei did not object).
4. **Team Activity on a project page:** no broken and no past due; done and altered only. Broken and past due appear on the personal page only, so the project page's block and closed line leave them out too.

**Accepted limits:**
- A commitment shows as of the render; a mark after it shows at the next render.
- Only `#N`, `issue N`, `issue №N` and `задача N` are read as issue numbers (above).
- Telemetry words are in place before any telemetry writes them (story 16).
- A not-enrolled project's commitments are named, not listed.

**Amendments this depends on.** Applied with the build, in this order: the PRD through bmad-prd, then bmad-spec re-derives the SPEC companion, then the spine through the architecture skill. The sentences are exact.

*PRD (`prd.md`), through bmad-prd (v0.16.2 → v0.16.3):*
- **FR-09 consequence** (`:428`): replace "followed by the meeting summaries recorded since the previous render with their open action items (2026-10-16)." with "followed by the open commitments of the page's scopes, soonest due first, a date shown as past only when the commitment is open and its date is not estimated (15c's rule) (2026-10-26, 15d), then the meeting summaries recorded since the previous render with the action items waiting for review and a count of each meeting's commitments (2026-10-16, 2026-10-26)."
- **Decisions Log** (`:966`): after the "> * **2026-10-25 (Story 15c \- v0.16.2):**" entry, add "> * **2026-10-26 (Story 15d \- v0.16.3):** *Commitments on the pages (2026-10-26, Andrei; FR-09, FR-34):* each page lists its open commitments, read from the ledgers whatever the meeting's age, soonest due first, at most 10, with the commands to see and mark them and no id; an approved item leaves its meeting's list for that block, and waiting items stay proposals; Team Activity and the proposal cards show the commitment's owner, due and state; the state's source, the PM or telemetry, is always named; a project page shows a person only when resolved to a member, shows no more about a confidential issue than Team Activity does, shows broken and past due on the personal page only (2026-10-27, Andrei), and never reads the personal ledger; commands a wrapper fills carry placeholder words it can recognise, and the CLI refuses them as values."

*SPEC companions, re-derived by bmad-spec:*
- **`derivation-services.md:127`:** in the `CommitmentLog` row's users, replace "the dashboard digest and Team Activity (read, `15c`)" with "the dashboard digest and Team Activity (read, `15c`), each page's block of open commitments and the proposal cards (read, `15d`)".
- **`derivation-services.md:123`:** replace "completion, and `commitment list` and `show` at a terminal," with "completion, and `commitment list` and `show` and `proposal list` and `show` (`15d`) at a terminal,".
- `SPEC.md` itself is unchanged: CAP-9 keeps its four sections, and CAP-34 already covers the ledger.

*Spine (`ARCHITECTURE-SPINE.md`), through the architecture skill:*
- **AD-25 heading** (`:405`): the revision bracket `[revised 2026-09-23]` becomes `[revised 2026-09-23, 2026-10-26]`, still in backticks, so the line reads: ``### AD-25 — Personal analytics are physically separated from project scope `[revised 2026-09-23, 2026-10-26]` ``.
- **AD-25** (`:417`): after "and it never reads personal-scope or `people`-scope data.", add "`[2026-10-26, 15d]` A project page may list the project's open commitments, ordered by due and never grouped, counted or ranked by member, from that project's ledger only, shows no more about a confidential issue than its Team Activity row does, and shows no broken or past-due mark `[2026-10-27, Andrei, 15d]`."

## Verification

**Commands:**
- `uv run pytest tests/core/test_commitment_cards.py tests/core/test_commitment_log.py tests/core/test_rendering_sections.py tests/core/test_project_rendering.py tests/core/test_member_updates.py tests/slice/test_dashboard_slice.py tests/slice/test_commitment_commands.py tests/slice/test_proposals.py tests/architecture -q` -- expected: every matrix row passes
- `uv run pytest -q` (in the background, about 7 minutes) -- expected: all pass; 6 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a scratch home (`HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1`), drop and summarise the recorded fixture into personal, approve two items, `commitment done` the first and `commitment due` the second `friday`, then render the dashboard. Expected: the block lists the second "due …, set by you"; the meeting says "2 commitments from this meeting, 1 open"; the closed line says "1 done (marked by you)".
- `pm-ai commitment show <second> --json`: `fill` names `<note>`, `<when>` and `<name>`; `pm-ai commitment due <second> '<when>'` exits 2.
