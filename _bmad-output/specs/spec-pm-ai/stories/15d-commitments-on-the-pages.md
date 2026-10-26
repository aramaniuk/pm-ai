---
title: 'commitments on the pages'
type: 'feature'
created: '2026-10-26'
status: 'draft'
baseline_commit: '7a097a7'
review_loop_iteration: 0
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
- **The morning brief is the personal dashboard.** FR-09's "Strategic Focus Morning Briefing" is `daily_dashboard.md` in the personal scope. No other brief exists in the code; story 5 delivers pages to Telegram later. So "the dashboard and the morning brief" means the personal page and each project's page.
- **A proposal card does not say it is a commitment.** `proposal show` of an item marked done still reads only "approved" (15a promised 15d "the cards show a commitment's state").
- **A wrapper can record "…".** `15c`'s card actions and the reject refusal carry the literal `"…"` where the PM types a value, and the JSON does not say which word that is (`deferred-work.md`, "Surfaced by the story-15c review").

**Approach:**
- **One block of open commitments** on each page, inside Time-Critical Activities, read from the ledgers, not bound to the page's window: soonest due first, past due shown only by 15c's rule, at most 10, then "and N more" with the exact `pm-ai commitment list` command.
- **Under each meeting,** the approved items give way to one line counting that meeting's commitments, with its `pm-ai commitment list … --meeting` command. Waiting items stay proposals, as today.
- **Team Activity** shows the commitment's owner, due and state on an approved item's line. A withdrawn one stays hidden.
- **Proposal cards** of an approved item carry the commitment's state and its `pm-ai commitment show` command.
- **Placeholders a wrapper can recognise:** `<note>`, `<when>`, `<name>` and `<what>` in place of `"…"`, a `fill` list in the JSON saying where they are, and the CLI refusing any of them typed as a value.

## Boundaries & Constraints

**Always:**

### Prerequisites and what is decided already

- **Built and relied on:**
  - `15a`: `Commitment`, `Due` (`domain/commitments.py`), `CommitmentLog`, `parse_ledger`, `fold` (`core/commitment_log.py`);
  - `15c`: `Details` (version, mark in force, `due_by_pm`, `owner_by_pm`, `changed_at`, `state_at`), `past_due`, the state and due words (`core/commitment_cards.py`), `list`'s order (`app/commitments.py:1972`), `read_ledger` (`:740`) and `LEDGER_READ_FAILURES` (`:765`), the `pm-ai commitment` commands, and the page rules of 15c (`pipelines.py:2676`, `:2782`, `:3162`);
  - `7d`: the meeting digests, `MAX_DIGEST_ITEMS`, `EXCERPT_LIMIT`, `escape` and `cut_at_word`;
  - `7e` and `23g`: Team Activity, `IssueNotes` (`confidential`, `confidentiality_unknown`, `links_unread`), `resolve_label` with the saved alias table, `bare_references`;
  - `13a`: `open_action_items`, the proposal card, `ProposalDesk`;
  - `23d`: the project page's scope wall (two renderers).
- **Decided before 15d, not reopened:**
  - past due only when the commitment is open, its due has a date, the date is not estimated, and it is before today in the display zone (15c);
  - an estimated date is shown as "(meeting day estimated)" and is never called past due (15a, 15c);
  - a commitment's state is never a proposal state (AD-14);
  - an action item on a confidential issue never shows its text on a project page, and every linked item's text is withheld when that cannot be told (Andrei, 2026-10-20, `7d`);
  - the project page reads project-scope data only (AD-25, `23d`).
- **15b is not relied on.** Every read here goes through `CommitmentLog.read` (or 15c's `read_ledger` by path at a terminal). No index is read.

### Which ledgers each page reads

- **The personal page** (the morning brief) reads the personal ledger and each enrolled project's (`daemon.watched`), as its digests read those scopes (AD-38's direction, project into personal). Never a `people` scope.
- **A project page** reads that project's ledger only. Never the personal one (AD-25).
- **One read per scope per render.** The digest's per-meeting line, the block and Team Activity share it. 15c reads a project's ledger twice per render (`_digests_over` and `_item_decisions`); 15d reads it once.

### The block of open commitments

- **Where:** in Time-Critical Activities, after the day's meetings and before the meeting summaries. No new heading: CAP-9's four sections stay four. Open commitments are what is due, so they sit next to the day's schedule.
- **What it lists:** the open commitments, both action items and spoken promises, whatever the meeting's age. Open is 15c's meaning: not `fulfilled`, `broken` or `withdrawn` (so `pending`, `altered`, and story 16's `unknown` and `error`).
- **Order:** 15c's `list` order for open ones, across every scope the page reads: a date first, earliest first; then `unresolved`; then `none`; ties by the meeting's start, newest first, a line with no start after those with one, then the commitment id. The key moves from `app/commitments.py` to `core/commitment_cards.py` as `open_order`, so `commitment list` and the pages share it.
- **At most 10** (`MAX_PAGE_COMMITMENTS = 10`, `core/rendering.py`, beside `MAX_MEETING_DIGESTS`).
- **One line per commitment:**

  ```
  - **due Thu 22 Oct, past due** Sam Rivera: send the cache TTL numbers ("by Thursday") — from Example Weekly Sync-up, Mon 19 Oct (`project:alpha`)
  - **due Fri 30 Oct (meeting day estimated)** Alex Morgan: book the load test — from Example Weekly Sync-up, Mon 19 Oct
  - **due Fri 30 Oct, set by you** said by Sam Rivera: share the draft — altered on Sat 24 Oct — from Example Weekly Sync-up, Mon 19 Oct
  - **no due date worked out from "next sprint"** Alex Morgan: migrate the cache — from Example Weekly Sync-up, Mon 19 Oct
  ```

  - **The due first, in bold,** in 15c's heading words: `due <date>`, then `(meeting day estimated)`, `, set by you` and `, past due` as they apply; or `no due date worked out from "<words>"`, `no due date worked out`, `no due date said`.
  - **Then who and what,** as 15c's field line: `<owner>: <what>` for an action item; `said by <speaker>: <what>` for a promise, with `, owner <name>` when the PM set one. One difference, kept from the digest: an action item with no owner shows `unassigned: <what>`. The due as said follows in quotes when a date was worked out from it. A `what` 15a cut ends with "…".
  - **Then the state,** only when it is not plain `pending`: `altered on <date>`, `pm-ai could not check` (`unknown`), `a check failed` (`error`), each followed by ` by telemetry` when telemetry set it. These are 15c's state words.
  - **Then the origin:** the meeting's title and its start's day; on the personal page, a project commitment's scope in code. With no title on the line: "from a meeting whose record was unavailable when it was approved".
  - **Text** is folded to one line, cut at a word to `EXCERPT_LIMIT`, then escaped (`23f`'s order), as the digest's fields are.
- **The words come from one place.** `core/commitment_cards.py` gains `page_parts(commitment, details, *, now, zone)`, returning the due words, the state words, `past_due` and the due as said, from the same functions the card uses (`_state_words`, `_due_heading`, `past_due`). The page composes who and what (the owner rule and the withholding below) and escapes. No other module builds a state or due word.
- **After the lines, in this order:**
  1. **More:** when more than 10 are open, `- and N more open commitments: ` then, for each scope with one left out, `` `pm-ai commitment list --scope <scope>` ``, comma-separated. 15c's rule that every printed command carries `--scope` holds.
  2. **The next step:** `- to mark or correct them: ` then `` `pm-ai commitment review --scope <scope>` `` for each scope with an open commitment, in the page's scope order (personal first, then projects by id).
  3. **Closed in the window:** when any commitment closed between the page's window start (`since`, Proactive Enablement's) and now: `- closed since <since>: 2 done (1 marked by you, 1 by telemetry), 1 broken (marked by you), 1 withdrawn (marked by you): ` then `` `pm-ai commitment list --scope <scope> --state all` `` for each scope with one. When it closed is the PM's mark's `occurred`, or `Details.state_at` for telemetry. Counts are over the whole page, never per person. A state with 0 is left out, and each count names its source in parentheses: `(marked by you)`, `(by telemetry)`, or both with their counts.
  4. **Ledger notes** (below).
- **No commands with an id and no `--if-version` on a page.** A page is read hours after it is written; a version on it would be stale, and the PM should not copy an id. `commitment review` walks the open ones and `commitment list` shows each card with its exact commands.
- **None open:** one sentence, `No open commitment in personal or the enrolled projects.` on the personal page, `` No open commitment in `project:<id>`. `` on a project page, followed by the closed line and the ledger notes when they apply.

### Under each meeting (the digest, `7d` and `13a` amended)

- **Waiting items stay proposals.** They are listed as today: `owner: what (due), to review by <date>`, in the summary's order, with `to review: pm-ai proposal review --scope … --meeting …`. Nothing is written to a ledger while an item waits (15a).
- **An approved item recorded in the ledger is no longer listed under its meeting.** It is in the block (open) or in the closed count. 15c's leaving out of closed ones is subsumed.
- **One line per meeting with commitments,** after the items: `- 2 commitments from this meeting, 1 open: ` then `` `pm-ai commitment list --scope <scope> --meeting <id> --state all` ``. It counts the commitments of that scope's ledger whose `pending` line names this meeting, action items and promises alike, open or closed. `1 commitment from this meeting, open` and `…, closed` for one. No line when the meeting has none.
- **An approved item with no line in the ledger** (the approval's write failed, or the catch-up has not run) stays in the list as `owner: what (due), approved, not yet recorded as a commitment: ` then `` `pm-ai proposal approve <id> --scope <scope>` ``, which writes the line (15a's repair, as 15c's `not_found` names it).
- **The overflow line** counts what is listed: waiting items, plus approved ones only when shown as above. It names `--state staged` when every hidden item is waiting, and `--state open` otherwise. Today it always names `--state open` (13a).
- **"no open action item"** is printed only when the meeting has no item listed, none hidden, none under an issue, none unreadable, and no commitment.
- **Team Activity placement** (`shown_elsewhere`) still applies to waiting items. An approved item is no longer listed here, so it is never counted "shown under #N".

### Team Activity (`7e`, `15c` amended)

- **A withdrawn item stays hidden,** as 15c decided.
- **An approved action item recorded in the ledger** keeps its row under its issue, and the row shows the commitment, not the proposal:
  - **the owner** is the commitment's owner (the PM's `owner_set` when he set one), resolved to a member by 7e's rule, exactly as the row resolves the proposal's owner today; unresolved stays `action, unassigned`;
  - **the due:** `due as said in the meeting: <words>` as today, then the date in parentheses from the commitment: `(Fri 30 Oct)`, `(Fri 30 Oct, meeting day estimated)`, or `(set by you: Fri 30 Oct)` when the PM set it; `, past due` by 15c's rule. With the PM's date and nothing said: `due set by you: Fri 30 Oct`. The proposal's said due is no longer resolved a second time by `_row`;
  - **the state,** when it is not plain `pending`: `; commitment done on <date>`, `broken on <date>`, `altered on <date>`, `pm-ai could not check` or `a check failed`, with ` by telemetry` when telemetry set it. Done and broken lines stay, as 15c decided: a line is a note of what the meeting said.
- **A row on a confidential issue** is unchanged: kind and marker only, no owner, due or state.
- **A waiting item's row** is unchanged.
- **Joined at read time,** in `_item_decisions` from the one ledger read. `ItemDecision` gains `commitment: ItemCommitment | None` (owner, due words, state words), built from `page_parts`.

### Who is named, and what is withheld

- **The personal page** shows names as recorded (the owner as said or as the PM typed it, the speaker as said), as `7d` shows owner labels there. Nothing is withheld on the personal page (`7d`).
- **A project page** shows a person only when 7e's rule resolves the name to a member (the saved alias table, or an exact display name or username), as its digest does. An owner that resolves to no one shows `unassigned`; a speaker that resolves to no one shows `said in the meeting` with no name. Spoken names stay off the team page. This is the linking reader 15a assigned to 15d: a corrected alias applies to every past line.
- **Confidential issues, on a project page (Andrei, 2026-10-20, kept):**
  - an action item commitment whose root is in `issue_notes.confidential`, or any action item when `issue_notes.links_unread`, shows `an action item on a confidential issue` in place of its text; its due, state, owner and meeting stay;
  - a spoken promise is not linked to an issue by 7e. Its text is withheld, as `a spoken promise naming a confidential issue`, when it names an issue number (`bare_references`) that the GitLab record says is confidential, or names any issue number while the record cannot be read (`confidentiality_unknown`);
  - when anything is withheld, the page's existing sentences that say why (`7d`) are printed, once.
- **Never on a project page:** a ranking, grouping or count by member (AD-25). The block is ordered by due only, and its closed line counts the whole project.

### When a ledger cannot be read (fail open)

- **Reading a ledger may fail** with `LEDGER_READ_FAILURES` (15c's tuple: an unreadable file, a store that cannot decrypt, a scope that cannot be resolved). The render never fails for it.
- **For that scope:**
  - its digests show approved items as today (`owner: what (due), approved`), with the overflow naming `--state open`, and no commitment line;
  - Team Activity shows its rows as recorded, withdrawn ones included (15c's fail-open);
  - the block lists the other scopes' commitments and says, among the ledger notes: `` The commitments of `<scope>` could not be read, so they are not listed and its approved action items show under their meetings as approved: <reason>. `` The reason is the failure's own words, folded, cut and escaped as the digest's reasons are.
- **Lines the fold counted but could not use** (malformed, orphans, duplicates, conflicts, refused marks) never fail the read. When a scope has any, one note per scope in 15c's words (`bad_lines_words`): `` commitments_log.md in `<scope>`: 1 line could not be read, 1 mark was not applied; `pm-ai commitment list --scope <scope>` shows the same. ``
- **The same id in two ledgers** (a hand copy) is listed once, under the first scope in the page's order, with ``(also in `<other scope>`)``.
- **No scope is read** on the page that the page does not show: the project page never opens the personal ledger to say it is unreadable.

### Telemetry and the PM's marks

- **The state's source is shown, never inferred.** "by telemetry" is printed only when `state_source` is telemetry; "marked by you" only when it is the PM's mark. No word claims a check that did not run.
- **Before story 16** only a hand-written `verdict` line sets a telemetry state. The words are in place, and tests use a hand-written line, as 15c's do.
- **An undone done mark** is not a mark: the fold already left it out (15c), so the page shows the state before it.
- **Past due is shown, not acted on.** The page sends, marks and asks nothing (story 16 owns overdue).

### Proposal cards show the commitment (`13a` amended)

- **`proposal show` and `proposal list`** of an approved item whose type records a commitment (`action_item`, `implicit_commitment`): the full card gains one line after its origin:
  - recorded: `commitment: <state words>, <due words>: pm-ai commitment show <root> --scope <scope>`;
  - not recorded: `commitment: not recorded yet: pm-ai proposal approve <id> --scope <scope> records it`;
  - ledger unreadable: `commitment: the ledger could not be read: <reason>`.
- **13d's compact card** in `proposal review` does not gain it (review walks waiting proposals).
- **In JSON,** the card gains `"commitment": {"commitment_id": string, "recorded": boolean | null, "state": string | null, "state_source": string | null, "past_due": boolean, "show": [words] | null, "repair": [words] | null}`, `null` for any other proposal. `recorded` is `null` when the ledger could not be read. `format` stays 1 (a field added).
- **A replacement** (`<root>_e<n>`) shows its root's commitment.
- **The read:** each listed scope's ledger once per command, through `read_ledger`: through the store in the daemon, by path at a terminal, as `commitment list` and `show` do (15c's stated exception). Within `proposal list`'s and `show`'s existing 1-second bound.

### Placeholders a wrapper can recognise (the 15c review finding)

- **Four placeholder words** replace `"…"` wherever a command offers a value for the PM to type:
  - `<note>` in `commitment altered … --note <note>`;
  - `<when>` in `commitment due <id> <when>`, and in `closed_reason`'s `altered … --due <when>`;
  - `<name>` in `commitment owner <id> <name>`, and in `closed_reason`'s `altered … --owner <name>`;
  - `<what>` in the reject refusal's `proposal edit <id> --what <what>`.

  `PLACEHOLDERS`, one tuple in `core/commitment_cards.py`, holds them.
- **The text** shows the same words, quoted by `shlex.join` (`--note '<note>'`), so a person sees what to replace.
- **In JSON,** each card action gains `"fill": [{"index": int, "name": "note" | "when" | "name" | "what", "required": boolean}]`, the index into `command`; `[]` when nothing is to be filled. The reject refusal's `commitment` object gains `"fill": {action: [same]}`. A required placeholder is replaced by the PM's value. An optional one (`altered`'s note) is either replaced, or removed together with the option word just before it (`--note`), which always precedes it. `format` stays 1: the change is a value and an added field, and no wrapper exists yet.
- **The CLI refuses a placeholder as a value.** A value that is exactly one of the four words, or exactly `…`, after folding, for `--note`, `--what`, `--owner`, `--due`, `due <words>` or `owner <name>` (on `commitment altered`, `due`, `owner`, and `proposal edit`), is a usage error (exit 2) naming the value to type. So a command run as given writes nothing. In `commitment review`, such a value is asked again with the same reason.

### Bounds and cost

- **The render reads no meeting record for the block.** The title and start come from the `pending` line, as `commitment list`'s do.
- **A timed test:** a personal page over a 5,000-line personal ledger and two 5,000-line project ledgers, and a project page over a 5,000-line ledger, each add at most 1 second to the render's reads.
- **No model call, no write, no master key beyond what the render already uses.**

### The architecture tests and the skip count

- **`EXPECTED_SKIPS` stays 6** (`tests/conftest.py:192`). No skipping test depends on 15d.
- **New, passing:** `test_ad25_project_page_reads_only_its_own_commitment_ledger` (`tests/architecture/test_domain_invariants.py`): a project render over a store holding personal and another project's ledgers calls `read_commitments` for that project only.
- **`.importlinter` unchanged:** `core/rendering.py` takes domain values (`domain/renders.py`); `core/commitment_cards.py` imports `domain` and `core`; the pipelines are in `app`.

### No real data

Tests use `11d`'s fixture names (`Example Weekly Sync-up`, `Alex Morgan`, `Sam Rivera`), `project:alpha`, and `example.com` handles. Nothing reads `~/.pm-ai` or `~/.manager-ai`.

**Ask First:** three questions for Andrei, below Design Notes. The frozen block is written for the recommended answer of each.

**Never:**

- **Pages:**
  - no commitment id, version or `--if-version` printed in the block or the meeting lines; only the repair of an unrecorded approval names a proposal id;
  - no fifth heading;
  - no "past due" on an estimated, `unresolved` or `none` due, on a closed commitment, or on a date of today;
  - no "by telemetry" without a telemetry state, and no "marked by you" without the PM's mark;
  - no state or due word built outside `core/commitment_cards.py`;
  - no message, mark, verdict or question because a date passed (story 16).
- **Scopes:**
  - no personal ledger read by a project render;
  - no `people` scope read;
  - no per-member count, ranking or grouping on a project page.
- **Text:**
  - no action item text on a confidential issue on a project page, and none for linked items when confidentiality cannot be told;
  - no unresolved spoken name on a project page;
  - no field shown unescaped or uncut.
- **The ledger:** no write of any kind. 15d only reads.
- **Proposals:** no proposal state changed or read as a commitment state (AD-14); waiting items stay proposals.
- **JSON:** no placeholder without a `fill` entry, no `"…"` in a command's words, and no field removed or renamed.
- **Scope of this slice:** no index (15b), no telemetry (16), no Telegram delivery (story 5), no new command.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Block order | personal: open commitments due 22 Oct, 30 Oct (estimated), `unresolved`, `none`; today Sun 25 Oct | four lines in that order; the first says "due Thu 22 Oct, past due"; the second "(meeting day estimated)", not past due | N/A |
| Due today | due 25 Oct, today 25 Oct | "due Sun 25 Oct", not past due | N/A |
| Set by the PM | `commitment due <id> friday` was run | "due Fri 30 Oct, set by you" | N/A |
| Old meeting | an open commitment from a meeting 30 days ago, out of the digest's window | listed in the block | N/A |
| Promise | an approved spoken promise said by Sam Rivera, no owner | "said by Sam Rivera: …" on the personal page | N/A |
| Altered | the PM marked it altered on 24 Oct | "— altered on Sat 24 Oct —" | N/A |
| Telemetry open state | a hand-written `verdict state=unknown` | "pm-ai could not check by telemetry" | N/A |
| Cap | 13 open: 9 personal, 4 in `project:alpha` | 10 lines; "and 3 more open commitments:" naming `commitment list --scope` for each scope with one left out | N/A |
| Next step | open ones in personal and `project:alpha` | "to mark or correct them:" with `commitment review --scope personal` and `--scope project:alpha` | N/A |
| Closed line | in the window: 1 marked done by the PM, 1 done by a hand-written verdict, 1 broken, 1 withdrawn | "closed since …: 2 done (1 marked by you, 1 by telemetry), 1 broken (marked by you), 1 withdrawn (marked by you):" and the `--state all` command | N/A |
| Undone mark | marked done, then undone | listed as open; not in the closed line | N/A |
| None open | no open commitment, nothing closed | "No open commitment in personal or the enrolled projects." | N/A |
| Project page scope | a project page; the personal ledger holds open commitments | only the project's listed; `read_commitments` called for that project only | N/A |
| Meeting line | a meeting with 2 approved items, one done, and 1 waiting | the waiting item listed; "2 commitments from this meeting, 1 open:" with `--meeting … --state all` | N/A |
| Waiting only | a meeting with 3 waiting items | listed as today, with the review line | N/A |
| Not recorded | an approved item with no `pending` line | listed "approved, not yet recorded as a commitment:" with `proposal approve <id> --scope …` | N/A |
| Overflow | 7 waiting items | 5 listed; "and 2 more" naming `--state staged` | N/A |
| Overflow, ledger unreadable | 4 waiting and 3 approved, the ledger unreadable | 5 listed (4 waiting, 1 approved); "and 2 more" naming `--state open` | N/A |
| Ledger unreadable, personal page | `project:alpha`'s ledger cannot be read | personal commitments listed; the note names `project:alpha` and its reason; its approved items under their meetings as approved; no meeting line for it | stated |
| Ledger unreadable, project page | the project's ledger cannot be read | Team Activity rows as recorded, withdrawn included; the block is the note only | stated |
| Bad lines | personal ledger with 1 malformed line and 1 refused mark | the commitments, then one note naming both counts and `commitment list --scope personal` | stated |
| Same id twice | a hand-copied `pending` line in personal and `project:alpha` | listed once, under personal, "(also in `project:alpha`)" | N/A |
| Confidential, action item | project page; a commitment whose root is linked to a confidential issue | "an action item on a confidential issue"; due, state and owner shown | N/A |
| Links unread | project page; `member_updates.md` cannot be read | every action item's text withheld in the block; the page says why | stated |
| Promise naming a confidential issue | project page; a promise "fix #142 first", #142 confidential | "a spoken promise naming a confidential issue" | N/A |
| Promise, GitLab unread | project page; the GitLab record cannot be read; a promise naming "#7" | withheld; the confidentiality sentence printed | stated |
| Promise naming no issue | project page; the GitLab record cannot be read; a promise naming no number | its text shown | N/A |
| Personal page shows text | the same commitments on the personal page | text shown as said | N/A |
| Project owner unresolved | project page; owner "Sam" not a member and no alias | "unassigned: …" | N/A |
| Project speaker unresolved | project page; promise said by an unmatched name | "said in the meeting: …" | N/A |
| Alias corrected | an alias saved after the approval | the next render shows the member | N/A |
| Team Activity, done | an approved item linked to #142, marked done 26 Oct | its row ends "; commitment done on Mon 26 Oct" | N/A |
| Team Activity, PM's owner | `commitment owner <id> "Alex Morgan"`, Alex a member | the row says "action for Alex Morgan" | N/A |
| Team Activity, PM's due | `commitment due <id> friday` | "(set by you: Fri 30 Oct)" | N/A |
| Team Activity, past due | due 22 Oct worked out exactly, open, today 25 Oct | ", past due" | N/A |
| Team Activity, estimated | due worked out against an estimated day | "(Thu 22 Oct, meeting day estimated)", never past due | N/A |
| Team Activity, withdrawn | marked withdrawn | hidden | N/A |
| Team Activity, confidential | a done item on a confidential issue | kind and marker only, no state | N/A |
| One read | a project page render | `read_commitments` called once for the project | N/A |
| Proposal card | `proposal show <id>` of an approved item marked done | the line "commitment: done on …, due …: pm-ai commitment show <root> --scope …"; JSON `commitment.state: "fulfilled"`, `state_source: "pm"` | exit 0 |
| Proposal card, replacement | `proposal show <root>_e1` | its root's commitment | exit 0 |
| Proposal card, not recorded | an approved item with no `pending` line | "not recorded yet", `recorded: false`, `repair` the `proposal approve` words | exit 0 |
| Proposal card, unreadable | the ledger cannot be read | "the ledger could not be read: …", `recorded: null` | exit 0 |
| Proposal card, waiting | a waiting item | no commitment line; `commitment: null` | exit 0 |
| Placeholder in JSON | `commitment show <id> --json` of an open one | `actions.altered.command` holds `"<note>"`, `fill` `[{"index": …, "name": "note", "required": false}]`; `actions.due` holds `"<when>"`, required | exit 0 |
| Placeholder in the refusal | `proposal reject <id> --json` of an approved item | `commitment.commands.edit` holds `"<what>"`; `commitment.fill.edit` names it | exit 3 |
| Placeholder run as given | `commitment due <id> '<when>'`; `commitment altered <id> --note '<note>'`; `commitment owner <id> …` | usage error naming the value to type; nothing written | exit 2 |
| Placeholder typed in review | `u`, then `<when>` | asked again with the reason | continues |
| Closed reason | `show --json` of a broken one | `actions.due.reason` names `commitment altered <id> --scope … --due '<when>'` | exit 0 |
| Timed render | 5,000-line personal and two project ledgers; a 5,000-line project ledger | each page's ledger reads within 1 s | N/A |

</frozen-after-approval>

## Code Map

Anchors checked against `origin/main` at `7a097a7`.

- `pm_ai/app/pipelines.py:2504` `prepare_dashboard` (the personal and project branches); `:2676` `_meeting_digest` (`:2730` the 15c skip of closed items); `:2782` `_digests_over`; `:2823` `_LEDGER_READ_FAILURES`; `:2827` `_commitment_ledger`; `:2842` `_closed_commitments`; `:2857` `personal_meeting_digests`; `:2896` `project_meeting_digests`; `:3096` `_issue_notes`; `:3162` `_item_decisions` (`:3189` its second ledger read)
- `pm_ai/core/rendering.py:195` `MAX_MEETING_DIGESTS`; `:199` `MAX_DIGEST_ITEMS`; `:202` `EXCERPT_LIMIT`; `:378` `render_dashboard`; `:449` `render_project_dashboard`; `:804` `_digest_block`; `:940` `_digest_lines` (`:987-991` the approved word, `:995-1001` the overflow line, `:1015-1022` "no open action item")
- `pm_ai/domain/renders.py:83` `DigestItem`; `:121` `MeetingDigest`; `:209` `MeetingDigests`; `:260` `ProjectMeetingDigests`
- `pm_ai/core/member_updates.py:953` `_row` (`:976-991` the due part); `:1000` `ItemDecision`; `:1015` `_decided`
- `pm_ai/core/team_activity.py:137` `bare_references`; `:169` `IssueNotes`
- `pm_ai/core/commitment_cards.py:157` `past_due`; `:219` `_state_words`; `:239` `_due_heading`; `:402` `closed_reason` (`:416` its `"…"`); `:426` `build_card` (`:448` `--note "…"`, `:465` `due`/`owner` `"…"`); `:523` `card_json`; `:740` `bad_lines_words`
- `pm_ai/app/commitments.py:740` `read_ledger`; `:765` `LEDGER_READ_FAILURES`; `:1965` `_due_key`; `:1972` `_ordered` (the key moves to `open_order`); `:1077` the review commands per open scope
- `pm_ai/app/proposals.py:221` `_card`; `:889-890` the reject refusal's `"…"` words
- `pm_ai/core/proposal_cards.py:94` `CardAction`; `:103` `Card`; `:142` `command_line`; `:151` `build_card`
- `pm_ai/app/entry.py:660-661` `proposal list` and `show` bounds; `:1924` their by-path reader
- `tests/slice/test_commitment_commands.py:1181`, `:1214`, `:1222`, `:1336`: 15c's page tests, which change (below)
- `tests/core/goldens/dashboard_with_summaries.md`; `tests/core/test_rendering_sections.py`; `tests/core/test_project_rendering.py`; `tests/slice/test_dashboard_slice.py`; `tests/core/test_member_updates.py`; `tests/core/test_commitment_cards.py`
- `tests/conftest.py:192` `EXPECTED_SKIPS = 6`
- `prd.md:3` version; `:428` FR-09's Time-Critical consequence; `:966` the 15c Decisions Log entry
- `derivation-services.md:123` the stated exception; `:127` the `CommitmentLog` row
- `ARCHITECTURE-SPINE.md:417` AD-25's per-member paragraph

## Tasks & Acceptance

**Execution:**
- [ ] **The words.** `core/commitment_cards.py`: `page_parts`, `open_order` (moved from `app/commitments.py`, which then calls it), `PLACEHOLDERS`, and the `fill` list on `CardAction` (`core/proposal_cards.py` gains `fill: tuple[Fill, ...] = ()`). Tests first: every due and state word, past due, the order key equal to 15c's on its matrix rows.
- [ ] **Placeholders.** `build_card`, `closed_reason` and the reject refusal (`app/proposals.py:889-890`) use the placeholder words with `fill`; `card_json` and the refusal's `commitment` object emit `fill`. The usage check for a placeholder value in `app/commitments.py` (the desk's inputs) and `app/proposals.py` (`edit`), and in `review`'s questions (`surfaces/cli/dispatch.py`).
- [ ] **The page values.** `domain/renders.py`: `PageCommitment` (due words, `past_due`, who, what, withheld, state words, title, start, scope, also in) and `PageCommitments` (shown, left out per scope, scopes with open ones, closed counts by state and source, unreadable scopes with reasons, bad-line notes per scope). `MeetingDigest` gains `commitments: int` and `open_commitments: int`; `DigestItem` gains `unrecorded: bool`.
- [ ] **The pipeline.** `app/pipelines.py`: one ledger read per scope per render, shared; the block built for each page with the owner rule and the withholding; `_meeting_digest` drops recorded approved items and counts the meeting's commitments; `_item_decisions` fills `ItemDecision.commitment`.
- [ ] **The render.** `core/rendering.py`: `MAX_PAGE_COMMITMENTS`, the block after the day's meetings on both pages, the meeting line, the unrecorded line, the overflow's `--state`; `render_dashboard` and `render_project_dashboard` gain `commitments: PageCommitments | None = None`.
- [ ] **Team Activity.** `core/member_updates.py`: `ItemCommitment`, `_decided` and `_row` showing the commitment's owner, due and state.
- [ ] **Proposal cards.** `core/proposal_cards.py` and `app/proposals.py`: the commitment line and JSON field, one ledger read per scope per command.
- [ ] **Tests.**
  - `tests/core/test_commitment_cards.py`: `page_parts`, `open_order`, `fill` on every action.
  - `tests/core/test_rendering_sections.py` and `test_project_rendering.py`: the block, the meeting line, the notes; the golden `dashboard_with_summaries.md` re-recorded.
  - `tests/core/test_member_updates.py`: the row with a commitment.
  - `tests/slice/test_dashboard_slice.py`: one test per page matrix row, the one-read count, the timed render.
  - `tests/slice/test_commitment_commands.py`: the placeholder rows; **15c's page tests change**: `:1181` (closed items left out, an altered one still shown as approved) now asserts every recorded approved item is absent from the meeting and counted in its line, and the altered one is in the block; `:1214` (fail open) also asserts the note; `:1222` and `:1336` pass unchanged.
  - `tests/slice/test_proposals.py`: the proposal card rows.
  - `tests/architecture/test_domain_invariants.py`: the AD-25 test.
- [ ] **The contract.** Apply the amendments in Design Notes, in this order: `prd.md` through bmad-prd; then bmad-spec re-derives `derivation-services.md`; then `ARCHITECTURE-SPINE.md` through the architecture skill.

**Acceptance Criteria:**
- Given open personal commitments due before today, today and later, one estimated, when the personal page renders, then they are listed soonest due first and only the exact earlier date is called past due.
- Given an approved action item marked done, when the page renders, then it is not listed under its meeting, the meeting's line counts it, and the closed line counts it as marked by the PM.
- Given a project page and a commitment on a confidential issue, when it renders, then its text appears nowhere on the page.
- Given a project's ledger that cannot be read, when the personal page renders, then the note names that project and its approved items show under their meetings as approved.
- Given `commitment show <id> --json`, when a wrapper fills every required `fill` entry and drops the optional ones, then each command runs; and run as given, each is refused with exit 2 and writes nothing.
- Given the change, when `uv run pytest` runs, then everything passes with 6 skips.

## Spec Change Log

- **2026-10-26, created** from story 15's entry (15d: "the dashboard and the morning brief show commitments and their state in place of approved proposals"), 15a's promise to 15d (the cards show a commitment's state; 15d links names; estimated dates shown as estimated), 15c's page rules and accepted limits, the 15c review finding on `"…"` placeholders (Andrei: it belongs in 15d), and Andrei's standing rules (the CLI is an API layer; no memorised ids; no invented evidence; plain language). Three Ask First questions open.

## Design Notes

**Readiness check (2026-10-26, against `origin/main` at `7a097a7`).** Verdict: **ready; nothing blocks the spec.**
- **Built as 15d needs:** `CommitmentLog.read` and `read_ledger`; `Details` with `due_by_pm`, `owner_by_pm`, `state_at` and the mark in force; `past_due` and the card's state and due words as pure functions; 15c's order; `IssueNotes` with the confidential set and both "cannot tell" flags; `resolve_label`; `bare_references`.
- **Found:**
  - **There is no separate morning brief.** FR-09 names `daily_dashboard.md` the Morning Briefing; story 23's slices build it. 15d changes the personal page and the project page; nothing else is "the brief".
  - **The digest is bound to a window** of at most 7 days (`9b`), so moving commitments into the meeting lines alone would hide every commitment older than a week. This is why the block exists (Ask First 1).
  - **15c reads a project's ledger twice per render** (`_digests_over` via `_closed_commitments`, and `_item_decisions`). 15d reads once.
  - **`7d` deliberately lists no spoken promise** ("so one item found both ways is not shown twice"). 15d lists approved promises in the block, because once approved each is its own commitment; the PM approved both if both exist (Ask First 2).
  - **The `"…"` placeholders** are in four places, not the two the deferral names: the card's `altered`, `due` and `owner` actions (`commitment_cards.py:448`, `:465`), `closed_reason`'s reopen command (`:416`, text only), and the reject refusal's `altered` and `edit` commands (`proposals.py:889-890`). 13a's own `edit` action fills in the current values and has none.

**Why a block and not the meeting lines.** "In place of approved proposals" read literally keeps each item under its meeting with its state. That keeps a commitment on the page only while its meeting is in the window, so the one question the morning brief must answer, "what is due", would lose everything older than a week. The block answers it from the ledger; the meeting line keeps the link from a meeting to what it produced.

**Why no id or version on the page.** A page is a snapshot read later. A version would be refused as `changed` after any mark; an id asks the PM to copy something he should not have to read. `commitment review --scope …` walks the open ones with exact commands.

**Why the order key moves to core.** The page and `commitment list` must order the same way, or the "and N more" command shows a different first ten. One function, called by both.

**Why placeholders are refused as values.** The `fill` list lets a wrapper do it right; refusing the words makes a wrapper that does it wrong write nothing instead of "<note>" in the PM's ledger, which cannot be removed (append-only).

**Accepted limits:**
- A commitment shows on the page as of the render; a mark after it shows at the next render.
- A spoken promise on a project page is checked for confidential issues only by the numbers it names; a promise about a confidential issue that names no number is shown, as a meeting summary's excerpt is.
- Telemetry words are in place before any telemetry writes them (story 16).

**Amendments this depends on.** Applied with the build, in this order: the PRD through bmad-prd, then bmad-spec re-derives the SPEC companion, then the spine through the architecture skill. The sentences are exact.

*PRD (`prd.md`), through bmad-prd (v0.16.2 → v0.16.3):*
- **FR-09 consequence** (`:428`): replace "followed by the meeting summaries recorded since the previous render with their open action items (2026-10-16)." with "followed by the open commitments of the page's scopes, soonest due first, a date shown as past only when it was worked out exactly (2026-10-26, 15d), then the meeting summaries recorded since the previous render with the action items waiting for review and a count of each meeting's commitments (2026-10-16, 2026-10-26)."
- **Decisions Log** (`:966`): after the "> * **2026-10-25 (Story 15c \- v0.16.2):**" entry, add "> * **2026-10-26 (Story 15d \- v0.16.3):** *Commitments on the pages (2026-10-26, Andrei; FR-09, FR-34):* each page lists its open commitments, action items and spoken promises, read from the ledgers whatever the meeting's age, soonest due first, at most 10, with the commands to see and mark them and no id; an approved item leaves its meeting's list for that block, and waiting items stay proposals; Team Activity and the proposal cards show the commitment's owner, due and state; the state's source, the PM or telemetry, is always named; a project page shows a person only when resolved to a member, withholds text on confidential issues, and never reads the personal ledger; commands a wrapper fills carry placeholder words it can recognise, and the CLI refuses them as values."

*SPEC companions, re-derived by bmad-spec:*
- **`derivation-services.md:127`:** in the `CommitmentLog` row's users, replace "the dashboard digest and Team Activity (read, `15c`)" with "the dashboard digest and Team Activity (read, `15c`), each page's block of open commitments and the proposal cards (read, `15d`)".
- **`derivation-services.md:123`:** replace "completion, and `commitment list` and `show` at a terminal," with "completion, and `commitment list` and `show` and `proposal list` and `show` (`15d`) at a terminal,".
- `SPEC.md` itself is unchanged: CAP-9 keeps its four sections, and CAP-34 already covers the ledger.

*Spine (`ARCHITECTURE-SPINE.md`), through the architecture skill:*
- **AD-25** (`:417`): after "and it never reads personal-scope or `people`-scope data.", add "`[2026-10-26, 15d]` A project page may list the project's open commitments, ordered by due and never grouped, counted or ranked by member, from that project's ledger only."
- AD-25's heading gains `[revised 2026-10-26]`.

## Ask First

1. **Where commitments go on the page.** *Recommended: A.*
   - **A.** A block of open commitments in Time-Critical Activities, from the ledgers, whatever the meeting's age; under each meeting, approved items give way to one count line. Waiting items stay. (This spec.)
   - **B.** In place under each meeting only: each approved item's line shows its state, owner and due. No block. A commitment leaves the page when its meeting leaves the 7-day window.
   - **C.** Both: the block, and each approved item kept under its meeting with its state. Each commitment shows twice.
2. **Spoken promises on the pages.** *Recommended: A.*
   - **A.** Both pages list approved promises in the block, "said by …"; a project page withholds a promise's text when it names a confidential issue, or any issue when that cannot be told. (This spec.)
   - **B.** The personal page only; a project page lists action items only.
   - **C.** Neither page, as `7d`: action items only.
3. **Proposal cards in 15d.** *Recommended: A.*
   - **A.** `proposal show` and `list` of an approved item carry the commitment's state and its `commitment show` command, as 15a promised. (This spec.)
   - **B.** Leave the proposal cards as they are; the pages and `commitment show` are enough.

## Verification

**Commands:**
- `uv run pytest tests/core/test_commitment_cards.py tests/core/test_rendering_sections.py tests/core/test_project_rendering.py tests/core/test_member_updates.py tests/slice/test_dashboard_slice.py tests/slice/test_commitment_commands.py tests/slice/test_proposals.py tests/architecture -q` -- expected: every matrix row passes
- `uv run pytest -q` (in the background, about 7 minutes) -- expected: all pass; 6 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a scratch home (`HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1`), drop and summarise the recorded fixture into personal, approve two items, `commitment done` the first and `commitment due` the second `friday`, then render the dashboard. Expected: the block lists the second "due …, set by you"; the meeting says "2 commitments from this meeting, 1 open"; the closed line says "1 done (marked by you)".
- `pm-ai commitment show <second> --json`: `fill` names `<note>`, `<when>` and `<name>`; `pm-ai commitment due <second> '<when>'` exits 2.
