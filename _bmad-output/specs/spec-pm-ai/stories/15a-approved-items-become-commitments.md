---
title: 'approved items become commitments, in the meeting''s own ledger'
type: 'feature'
created: '2026-10-24'
status: 'draft'
baseline_commit: 'b6f8f62'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/13a-proposals-approved-edited-or-rejected.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Since `13a`, the PM approves the action items a meeting agreed (`7b`) and the promises a transcript holds (`11b`). An approved item then stays `approved` in `operational.db` and nothing else happens. 13a left the next step to story 15: "Story 15 records a commitment for each approved `action_item` and `implicit_commitment`, keyed by the proposal's id, including those approved before 15 exists" (13a, Prerequisites).

Today nothing records one:
- No code writes `commitments_log.md`. The project tree declares it (`scope_model.py:762`), and the personal tree does not declare it at all, so a personal meeting's approved items have nowhere to go. The first items the PM approved are personal.
- `CommitmentState` and `evaluate_commitment` exist (`lifecycle.py:49`, `:208`), but there is no `Commitment` entity, no ledger line format, no reader and no accessor.
- A due is kept only as said ("by Thursday"). Nothing turns it into a date that a later check could call overdue.
- The PM's decisions live only in `operational.db`, which is Tier 2 and is not backed up (13a, Design Notes, "Accepted limit, until story 15").
- 13a lets the PM reject an approved action item, so an agreed task could vanish after it was agreed.

**Approach:** Approval writes the commitment.
- **One line at approval.** When the PM approves an `action_item` or an `implicit_commitment`, pm-ai appends one `pending` line to the `commitments_log.md` of the scope that owns the meeting: a project's, or the personal scope's for a personal meeting. Nothing is written while an item waits.
- **Written once.** Each line has a deterministic id. The commitment is keyed by the proposal's root id. An approval that is repeated, retried or caught up never writes a second line.
- **The due date, resolved.** The due is kept as said. A date is also worked out from it against the meeting's day, by a pure resolver with no model, in English and Russian. When the words do not name one date, the line says `unresolved`, and such a commitment never becomes overdue.
- **The owner, as a name.** It is never written as a handle. A reader links it to a person only through an exact saved alias.
- **An edit after approval** appends a `revised` line with the new values. The commitment's state does not change.
- **A one-time catch-up** writes the missing lines for items approved before 15a, and for any approval whose line was not written. It runs at daemon start, and again whenever the same item is approved once more.
- **No rejection once approved.** An approved item can no longer be rejected. The refusal says to mark the commitment altered or broken instead.
- **The format for later slices.** 15a defines the line format and the state table for the PM's own mark (done, altered or broken), which story 15c's commands will write.
- **Left for later slices:** the index (15b), the commands (15c) and the surfaces (15d).

## Boundaries & Constraints

**Always:**

### Prerequisites and the slices of story 15

- **Built and relied on:**
  - `13a`: the proposal engine, `ProposalDesk`, the registered types, the edge table, `<root>_e<n>` replacements, `changed_at`, `changed_via` and `scope` in the body;
  - `7b`: `action_item` proposals, `what`, `owner`, `due` as said;
  - `11b`: `implicit_commitment` proposals, `what` and `when`;
  - `11a`: `MeetingRecords`, title, start, `start_estimated_from` (`11e`);
  - `7e`: the ledgers' line encoding, compare-and-append, and today's `resolve_due`;
  - `7e` and `11e`: the saved alias table and how a name is linked when read;
  - `13d`: the cards.
- **Story 15 is split into four slices.** 15a is written in full here. The others get specs of their own:
  - **15a, this spec:** the `Commitment` entity, the `CommitmentLog` accessor and its line format, the due-date resolver, the line written on approval and on an edit of an approved item, the catch-up, no rejection after approval, and the personal ledger.
  - **15b, the index:** `commitment_index.db` (CAP-34's "indexed row at `[PENDING]`"), rebuilt from the Markdown alone, with each row storing the MD5 of `commitments_log.md` (story 15's own notes). Until story 10's queue exists, it is started by a trigger inside the daemon after each append, and checked at daemon start (Andrei, 2026-10-24).
  - **15c, the commands:** `pm-ai commitment list|show|done|altered|broken|due`. These are the PM's own marks and a due date he sets himself, written in the line kinds 15a defines.
  - **15d, the surfaces:** the dashboard and the morning brief show commitments in place of approved proposals, and the cards show a commitment's state.
- **What other stories add.** Story 16 writes telemetry verdicts (`FULFILLED`, `BROKEN`, `UNKNOWN`, `ERROR`) under their own line kind, and joins a commitment to a work item. `10a` adopts this `CommitmentLog` as the accessor its commitment-indexing job reads (`10a` notes, `derivation-services.md` rule 3). `11a` building `MeetingRecords` before `10a` is the precedent.

### When a commitment is written (Andrei, 2026-10-24)

- **The ledger starts at approval.** `STAGED_APPROVAL` is a proposal state (AD-14), never a ledger line. A waiting, rejected, expired or superseded proposal writes nothing.
- **Types that record a commitment:** `action_item` and `implicit_commitment`. Their registration gains `commitment=True`. `explicit_command` and any unknown type record none. A type that 13b gives an executor records none unless its registration says so.
- **What writes a `pending` line:**
  - `proposal approve` of a waiting item;
  - `proposal edit --approve` (13a), from a waiting or an expired item; the replacement's values are written;
  - `review`'s `a`, and its edit followed by `Y`;
  - the catch-up (below).
- **The order of the two writes.** The approval is committed in `operational.db` first, in 13a's claimed step. The line is appended afterwards, in its own claimed step on the same writer.
  - A crash, a busy claim, a full disk or a git refusal between the two leaves an approved item with no line.
  - Running the same `proposal approve <id>` again writes it: the `already` path records a missing line.
  - So does the catch-up at the next daemon start.
  - The reverse order could leave a `pending` line for an approval that was never committed, and an append-only ledger cannot take that back.
- **The ledger is the meeting's scope's.** The scope is the proposal's stored `scope`, else 13a's `ProposalWorld.scope_of` fallback. A proposal whose scope cannot be told is never approved (13a). A `people` scope never reaches the ledger: 13a refuses it, and the store refuses it again (`NotACommitmentScope`, below).

### Personal meetings get a personal ledger (Andrei, 2026-10-24, amends AD-28)

- **`PERSONAL_TREE`'s `memory/` gains `commitments_log.md`:** `File("commitments_log.md", Tier.TRUTH, encrypted=False, gitignored=False)`. The flags are those of its siblings `meetings/` and `event_log/`:
  - plaintext Markdown (AD-6);
  - Tier 1 and backed up (`BACKUP_TARGETS` derives from the tier);
  - not gitignored, because the personal scope may be the PM's own private repository (`storage_tiers.py:251`);
  - `0600`, like every personal file (`restricted_mode`, story 1s).

  One basename carries one tier in every scope (AD-44), and both copies are `TRUTH`.
- **`people` gets none (AD-4).** `PEOPLE_TREE` is unchanged. A commitment from a 1:1 with a direct report is not a `Commitment` (AD-28: `CareerGoal`).
- **A `CoachingCommitment` never reaches either ledger** (AD-28). The ledger accepts only the line kinds below, built from a `Commitment`.
- **The append-only list already names `commitments_log.md`** (`storage_tiers.py:196`), keyed by basename, so the personal copy is append-only with no change there. A whole-file write of it is refused.

### The `Commitment` entity

- **`pm_ai/domain/commitments.py`** declares `Commitment`, `Due`, `LineKind`, `MarkedState` and the mark table. It imports only sibling domain modules (AD-30).
- **A `Commitment` holds:**
  - `commitment_id`: the proposal's root id (`root_of`, `domain/proposals.py`), the id `7b` or `11b` minted. An edit's replacement `<root>_e<n>` is the same commitment;
  - `proposal_id`: the proposal whose values it now holds (the root, or its latest approved replacement);
  - `type`: `action_item` or `implicit_commitment`;
  - `meeting_id`, and the meeting's `title` and `start` as the record gave them when the line was written. Both are absent only when the record could not be read;
  - `what`, the description: the cleaned text the PM approved on the card, or the text he typed in an edit;
  - `owner`: a name, or none;
  - `due`: a `Due`;
  - `state`: a `CommitmentState`, `PENDING` from the `pending` line;
  - `approved_at`: when the PM's approval was committed (the proposal's `changed_at`);
  - `cites`: `meeting:<id>` (AD-33).

  It needs no transcript to be acted on or shown (AD-33).
- **What FR-34 lists that 15a does not record, and why:**
  - **`speaker`:** `7b` records an item's owner, not who said it. A promise's speaker is its owner. So there is one person field, `owner`.
  - **`target_work_item`:** an item names a work item only by `7e`'s link in `member_updates.md`, which may be model-matched and unconfirmed. Story 16 joins it when it verifies, from the line's own text and that ledger.

### The owner: a name, linked only when read (AD-34, `7e`, `11e`)

- **The line records the owner as said or typed, as a name** (`owner=`). It never records a handle.
  - For an `action_item`, this is `7b`'s owner label, or the name the PM typed in an edit.
  - For an `implicit_commitment`, it is the speaker of the promise. `11b`'s staging stores it from now on, as `speaker` in the payload: the transcript's label, with line breaks and control and format characters folded. A label over 80 characters (`OWNER_LIMIT`) is not stored, because a shortened name would be a different name.
  - A promise staged before 15a has no speaker stored, so its line has no owner. That is a known limit.
- **Why a name and not a handle.** The kernel's rule that "every person on a ledger line is recorded as the provider's own handle" is about people a provider identified. A spoken or typed name is a display name, and the glossary says a display name is never a handle. AD-34's saved alias table is "the one saved place a name becomes a handle", and "a ledger records a name as said … a reader joins them". This is `7e`'s rule for `member_updates.md` and `11e`'s for `## Participants`.
- **How a reader links it.** By `AliasTable.handle_for(name, meeting)` (`aliases.py:161`): a row limited to the line's meeting first, then an application-wide row, on `normalise()`'s exact key. Otherwise the name stays a name, shown as said. It is never fuzzy-matched or guessed. A generic label (`Speaker 2`, `Unknown`, their Russian forms) links only by a row limited to that meeting, as `7e` does. On a project page, `7e`'s exact match to one GitLab member's display name or username also applies, and is never saved. 15a writes and reads the name only. The linking readers are 15b's and 15d's, and a correction of an alias applies to every past line.

### The due date (Andrei, 2026-10-24)

- **Three values on every line:**
  - `due_said`: the words as said or typed (`7b`'s `due`, `11b`'s `when`, an edit's `--due`), absent when nothing was said;
  - `due`: one of
    - `<YYYY-MM-DD>`, the date resolved from them;
    - `unresolved`, when words were said but do not name one date, or when the meeting's day is not known;
    - `none`, when nothing was said.

  `unresolved` is a value, not a missing field. A reader can tell "said, and pm-ai could not work it out" from "nothing said".
- **No date, never overdue.** A commitment whose `due` is `unresolved` or `none` is never overdue. `Due.date` is `None` for both, and story 16 must not treat a commitment without a date as late. The PM can set the date himself (15c's `due_set` line). pm-ai never asks for it.
- **The meeting's day** is the date of the record's `start` in `display_timezone`, else UTC, as 13a's cards and `11f` read a day.
  - A start estimated from the file's modification time (`start_estimated_from=file-modified`, `11e`) names no day, so every due from that meeting is `unresolved`.
  - A start estimated from `docx-created`, `header` or `model-header` is used as it is.
  - A record that cannot be read gives no day, so the due is `unresolved`, and the line omits `title` and `start`.
- **The resolver** is `pm_ai/core/due_dates.py`, `resolve(said, meeting_day) -> Due`.
  - It is pure and deterministic: no model, no clock, no I/O.
  - It works on `aliases.normalise()` of the text, with punctuation `.,;:!?()` folded to spaces except inside a numeric date, and the leading words `by on until till before due` and `до к ко в во на по` dropped.
  - It resolves only these forms. Any other text, or a form with words besides those listed, is `unresolved`.

  | Form | English | Russian | Resolves to |
  | --- | --- | --- | --- |
  | a weekday | `Thursday`, `thu`, `by Friday`, `this Friday` | `четверг`, `до четверга`, `к пятнице`, `в субботу`, `в эту пятницу` (the cases `7e` lists) | its next occurrence **after** the meeting's day; the meeting's own weekday is `unresolved` (today or a week later) |
  | today, end of day | `today`, `end of day`, `EOD` | `сегодня`, `до конца дня` | the meeting's day |
  | tomorrow | `tomorrow` | `завтра` | the day after |
  | the day after tomorrow | `the day after tomorrow` | `послезавтра` | two days after |
  | in N days or weeks | `in 3 days`, `in a week`, `in 2 weeks`, `within a week` | `через 3 дня`, `через неделю`, `через 2 недели`, `в течение недели` | the meeting's day plus N days, or 7 × N; digits, or `a`/`one` and `неделю`/`одну` |
  | end of the week | `end of week`, `end of the week`, `EOW` | `до конца недели`, `к концу недели` | Ask First 2. With (a): the Friday of the meeting's week (Monday to Sunday), and `unresolved` for a meeting held on a Saturday or Sunday |
  | end of the month | `end of month`, `end of the month` | `до конца месяца`, `к концу месяца` | Ask First 2. With (a): the last day of the meeting's month |
  | day and month | `3 Jan`, `January 3`, `3rd Jan`, `Jan 3rd` | `3 января`, `3-го января` | the next such date **on or after** the meeting's day (`7e`'s rule) |
  | day, month and year | `3 Jan 2027`, `January 3, 2027` | `3 января 2027`, `3 января 2027 года`, `3 января 2027 г.` | that date |
  | numeric | `2026-10-30` (ISO), `30.10.2026`, `30.10` | the same | that date; without a year, the next one on or after the meeting's day |

- **What it refuses on purpose:**
  - `next Thursday` and `в следующий четверг`: people mean either the coming one or the one after;
  - `next week`, `next sprint`, `soon`, `ASAP`, a quarter, a release;
  - a slashed date (`10/11`): the day and month order differs between English and Russian use;
  - a two-digit year;
  - a time of day with other words (`Thursday morning`, `Thursday 3pm`);
  - an impossible date (`30 February`);
  - a date before the meeting's day.

  Each of these is `unresolved`. A wrong date makes a promise look broken, which is worse than no date.
- **One resolver.** `member_updates.resolve_due` (`member_updates.py:520`) becomes a call to `due_dates.resolve(...).date`, so Team Activity and the ledger never disagree about a date.
  - Every case `7e` resolves today resolves the same way.
  - Its one changed case is `3 Jan 2027`, which now resolves to that date instead of none. That is the day, month and year form.
  - The `_WEEKDAYS`, `_MONTHS` and `_LEADING` tables move with it.

### The line format (`pm_ai/core/commitment_log.py`)

- **One line per record**, through the ledgers' one encoding (`render_value` and `scan_fields`, `domain/event_entries.py`), each within `MAX_ENTRY_LENGTH`, newline-terminated. A line without its newline is not a record (storage-contract, AD-47).
- **Every line starts** `- entry=<id> commitment=<root id> kind=<kind>` and ends `occurred=<instant> … at=<instant>`.
  - `entry` is `cml_` plus the first 12 hex characters of SHA-256 over the line's identity (below). It is deterministic, so the same record rendered twice has one id.
  - `occurred` is when the change was made: the approval or the edit as committed in `operational.db`, or the PM's mark. It is the fold's clock.
  - `at` is when the line was written.
  - Both are ISO 8601 UTC. A line written by the catch-up has an `at` later than its `occurred`, and that is the only difference.
- **The kinds 15a writes:**
  - `- entry=… commitment=<root> kind=pending proposal=<proposal id> type=action_item|implicit_commitment meeting=<id> [title=<title> start=<instant>] what=<text> [owner=<name>] [due_said=<words>] due=<YYYY-MM-DD>|unresolved|none occurred=<approval instant> via=<where approved> written=approval|catch_up at=<instant>`

    Its identity is `(commitment, "pending")`, so one commitment has one `pending` line.
  - `- entry=… commitment=<root> kind=revised proposal=<replacement id> replaces=<old id> what=<text> [owner=<name>] [due_said=<words>] due=… occurred=<edit instant> via=<where edited> written=approval|catch_up at=<instant>`

    Its identity is `(commitment, "revised", proposal)`. It holds the whole set of values after the edit, never a difference.
  - `via` is the proposal's `changed_via` (`terminal`, `prompt`; story 5 adds `telegram`). `written` says whether the approval wrote the line or the catch-up did.
- **The kinds 15a defines and does not write.** 15c writes them; 15a's reader folds them, and 15a's tests write them by hand.
  - `- entry=… commitment=<root> kind=marked state=fulfilled|altered|broken by=pm [handle=<person handle>] [note=<text, at most 200 characters>] occurred=<instant> via=<surface> at=<instant>`, with identity `(commitment, "marked", state, occurred)`.
  - `- entry=… commitment=<root> kind=due_set due=<YYYY-MM-DD>|none by=pm [handle=…] occurred=… via=… at=…`, with identity `(commitment, "due_set", due, occurred)`.
  - `by=pm` says the PM made the change himself at a pm-ai surface. pm-ai has one user, so this is never a guess.
  - `handle` is his person handle when `pm_identity()` (`handles.py:566`) gives one, the lowest in sort order when it gives several, and absent when it gives none (Andrei, 2026-10-24: "under his handle").
  - These lines are never produced by telemetry, and they are counted apart from it. SM-9's "manual override" is the count of `marked` lines.
- **Reserved for story 16:** `kind=verdict`, for telemetry's `FULFILLED`, `BROKEN`, `UNKNOWN` and `ERROR`, with its evidence references. 15a's reader keeps a `verdict` line as a state change it does not interpret beyond `state=`, so 16 adds fields without changing 15a's parser.
- **Reading the ledger** (`parse_ledger`, then `fold`):
  - lines are grouped by `commitment` and folded in the order `(occurred, entry)`, never in file order (AD-35);
  - a second line with an `entry` already seen is ignored and counted as a duplicate;
  - a line that does not parse is skipped and counted, never fatal, and logged without its content;
  - a `revised`, `marked` or `due_set` line for a commitment with no `pending` line is counted as an orphan and ignored.
- **What the fold gives:**
  - **values** (`proposal`, `what`, `owner`, `due_said`) from the newest `pending` or `revised` line;
  - **`due`** from the newest line that sets one (`pending`, `revised` or `due_set`);
  - **`state`** `PENDING`, then the newest `marked` line, which a later `verdict` line never replaces (below).
- **The PM's mark: the states that allow it** (Andrei, 2026-10-24; 15c writes it):

  | Current state | `fulfilled` | `altered` | `broken` |
  | --- | --- | --- | --- |
  | `PENDING` | yes | yes | yes |
  | `ALTERED` | yes | yes, altered again | yes |
  | `UNKNOWN`, `ERROR` | yes | yes | yes |
  | `FULFILLED` | already, nothing written | refused: a kept promise is not altered | yes, overriding the verdict |
  | `BROKEN` | yes, overriding the verdict | yes, renegotiated after it was missed | already, nothing written |

  - The PM's mark outranks telemetry. A `verdict` line that occurs after a `marked` line does not change the state. Whether story 16 still writes one then is its own decision.
  - `ALTERED` is not terminal, and the commitment is still tracked under its due, which the PM may set again.
  - `FULFILLED` and `BROKEN` stay terminal for telemetry (AD-14). Only the PM's mark moves a commitment out of them.
  - The table is `MARKABLE` in `domain/commitments.py`, with `can_mark(state, mark)`, so 15c and its tests read one table.

### The accessor and the store

- **`CommitmentLog(storage)`** (`pm_ai/core/commitment_log.py`) is the one accessor for `commitments_log.md` (`derivation-services.md` rule 3). It is a vocabulary over `StoragePort`, like `MeetingRecords` and `EventLog`. It holds no path and opens no file. It offers:
  - `read(scope) -> CommitmentLedger`: the folded commitments by id, the counts of malformed, duplicate and orphan lines, and the byte size read;
  - `append(lines, scope, after)`.

  Every writer goes through it: the approval and the catch-up in 15a, 15c's marks, and 16's verdicts. 15b's indexer and `10a`'s job read through it.
- **The store** (`pm_ai/storage/service.py`) gains `read_commitments(scope) -> bytes` and `append_commitments(lines, scope, after) -> int`, modelled on `read_member_updates` and `append_member_updates` (`:4341`, `:4355`):
  - each append is one claimed step (`_claimed_unless_held`), one compare-and-append by size (`_compare_and_append`, `:4404`), and one write;
  - a ledger that grew since it was read is refused (`LedgerChanged`), and the caller reads it again and decides again;
  - a project ledger is written only once git confirms it is excluded (`_assert_git_excludes`, `:3337`, AD-43), as `member_updates.md` is. The personal one is not asked, since it is not gitignored;
  - a scope that is not `personal` or a project is refused with `NotACommitmentScope`, naming it, before anything is read or written (AD-4, AD-28).
- **Wiring** (`app/wiring.py`):
  - `append_commitments` joins `HANDED_CALLS` (`:4102`), with its git question for a project ledger;
  - `read_commitments` joins `PLAIN_READS` (`:4186`);
  - neither joins `NEVER_FROM_A_JOB` (`:4223`), because 15b's indexer and 16's lifecycle job will call them;
  - the port (`ports/__init__.py`) gains both, beside `read_member_updates` (`:807`).
- **Idempotent by the entry id.** Every writer reads and folds the ledger, leaves out each line whose `entry` is already there, and appends the rest with `after` set to the size it read. On `LedgerChanged`, it reads again, at most three times, and then reports the claim as busy. Two approvals, two catch-ups, or an approval racing the catch-up therefore write each line once, whichever process wins.

### The approval path (`pm_ai/app/commitments.py`, `pm_ai/app/proposals.py`)

- **`CommitmentRecorder`** is built in the composition root with the store, `MeetingRecords`, the display zone and the clock. It offers:
  - `record(proposal, scope)`: the `pending` line, or the `revised` line, for one approved proposal;
  - `catch_up(scopes)`: every missing line in those scopes.

  It reads the meeting record by the proposal's `meeting_id` and scope for the title, the start and the day. For a `revised` line it reuses the `pending` line's title and start, and resolves the new due against the same day.
- **`ProposalDesk`'s writes call it** after a committed change, never inside the proposal's claimed step:
  - `_change` to `approved` (`proposals.py:697`) calls it;
  - so does `_already` when the state asked for was `approved`;
  - so does `_edit` when the replacement is `approved`, either by `--approve` or because an approved item was edited.
- **Values come from the approved proposal:**
  - for an `action_item`, `what`, `owner` and `due` from its payload;
  - for an `implicit_commitment`, the cleaned `what` and `when` (`for_model`, as 13a's card shows them) and the new `speaker`.
- **The outcome says what happened.**
  - On success, the text sentence is `<id> approved: you agreed to it, and it is recorded as a commitment, due Thu 22 Oct ("by Thursday").` A `due` of `unresolved` reads `with no due date worked out from "next sprint"`, and `none` reads `with no due date said`.
  - The approve and edit JSON objects gain `"commitment"`:

    ```
    {"commitment_id", "scope", "state": "pending", "recorded": true|false, "reason"|null, "due": {"said"|null, "date"|null, "value": "<date>|unresolved|none"}}
    ```

    It is `null` for a type that records none. This adds a field, so `format` stays 1 (13a's rule).
  - When the line could not be written, the approval still stands, and the exit code is 0, since the approval was made. `recorded` is `false`, with the reason, and the sentence says: `<id> approved; it could not be recorded as a commitment yet (<reason>). Run the same command again, or pm-ai records it when the daemon next starts.`
- **The bound.** `approve` and `edit` keep `PROPOSAL_WRITE_BOUND_SECONDS = 2.0` at the prompt (AD-21). The extra work is one meeting-record read, one ledger read and one append, measured within the bound by a timed test over a ledger of 5,000 lines.

### An edit after approval

- **An edit of an approved item** supersedes it into an approved `<root>_e<n>` (13a). It also appends one `revised` line holding the replacement's values, with `replaces` naming the old proposal.
  - The state is unchanged: it was `PENDING` and stays `PENDING`.
  - This is the PM correcting what the ledger says was agreed. It is not his mark that the promise changed, which is `altered` in 15c (Ask First 1).
  - An edit equal to the current values writes nothing (13a's `unchanged`), so it writes no line.
- **An edit of a waiting item** writes no line. **An edit with `--approve`** of a waiting or an expired item writes the `pending` line, from the replacement's values.

### No rejection after approval (Andrei, 2026-10-24)

- **`approved → rejected` is refused for a type that records a commitment.**
  - `TypeRule` gains `records_commitment` (`domain/proposals.py:76`), filled from the registration's `commitment`.
  - `allowed()` (`:108`) allows `approved → rejected` only for a type with no executor **and** no commitment.
  - The store's conditional update refuses it as `IllegalTransition`, whatever the surface.
  - This needs no look at the ledger, so a line not yet written changes nothing.
- **The refusal** (exit 3), from `ProposalDesk` before any write: `<id> was approved on <date> and is a commitment now, so it is not rejected; nothing was changed. A commitment is marked altered or broken instead.` In JSON it is the 13a refusal object, with `state: "approved"`.
- **The card** (`proposal_cards.py:268-274`): an approved `action_item` or `implicit_commitment` offers `edit` and `show`. Its `reject` is unavailable, with the reason "approved, so it is a commitment now; a commitment is marked altered or broken instead". Completion stops offering approved items of these types for `reject` (`proposals.py:625`).
- **A waiting item is rejected as before.** So is an approved item of a type that records no commitment, when one exists.

### The catch-up (items approved before 15a, and any approval left without its line)

- **What it does.** For each scope in personal and every enrolled project, it does one indexed read of the proposals that are `approved` and of a type that records a commitment (13a's `state` and `type` columns). It reads the scope's ledger once. Then, for each live approved proposal:
  - when its root has no `pending` line, it adds a `pending` line from that proposal, with `written=catch_up`;
  - when its root's newest `pending` or `revised` line names another proposal, it adds a `revised` line;
  - otherwise it adds nothing.

  All of a scope's missing lines go in one append.
- **The approval instant.** `occurred` is the proposal's `changed_at`, the approval or edit that 13a recorded, never the catch-up's own clock. Every approval since 13a has recorded one.
  - A row with none, written by a test or a hand edit, gets no line and is counted, because its approval time is not known.
  - A body that cannot be read is counted and logged without its content, and gets no line.
  - A row whose scope cannot be told is counted, and gets no line.
- **When it runs:**
  - at daemon start, in `_lifespan` (`daemon.py:1033`), after the timers attach (`:646`): its reads on the I/O pool and its appends on the writer thread (AD-19), off the request path;
  - for one proposal, on every `approve` that finds the item already approved.

  The first daemon start after 15a writes the items approved before it. Nothing marks it as done once, because the entry ids make every later run write nothing.
- **What it reports.** One log line per scope with lines written, counts only. A scope that cannot be read, or whose git check refuses, is logged by name and tried again at the next start. It never stops the daemon starting.
- **No real data.** It is tested with fixtures shaped like `7b`'s rows: a personal `action_item` approved through `proposal approve`, under `11d`'s names (`Example Weekly Sync-up`, `Alex Morgan`, `Sam Rivera`). Nothing reads the PM's own `~/.pm-ai` or `~/.manager-ai`.

### The architecture tests and the skip count

- **The new modules do not use the name `pm_ai.core.commitments`.** That name belongs to story 16's verifier, whose `evaluate` and `sample_overdue`, `sample_pending` and `sample_event` `test_ad35_sweeper_will_not_declare_broken_without_coverage` and `test_ad36_self_authored_events_are_excluded_from_evidence` call (`test_domain_invariants.py:736`, `:750`). If 15a created that module without them, both tests would turn from skips into failures, and a stand-in `evaluate` would make them pass on nothing.
- **What 15a creates instead:**
  - `pm_ai/domain/commitments.py`: the entity, the kinds and the mark table;
  - `pm_ai/core/commitment_log.py`: the lines, the fold and the accessor;
  - `pm_ai/core/due_dates.py`: the resolver;
  - `pm_ai/app/commitments.py`: the recorder and the catch-up.

  The spine's module map is amended (Design Notes).
- **`test_ad33_ledger_entries_are_self_contained` (`:628`): 15a writes its fixture, so it turns from a skip into a pass.**
  - `_sample_extraction()` (`:649`) is replaced by `_sample_approved_action_item()`: a `7b`-shaped approved proposal in a personal scope, with a meeting record under `11d`'s names.
  - The test calls `commitment_log.from_approved(proposal, record=…, zone=…)`.
  - It checks that `commitment_id`, `what`, `cites`, `meeting_id`, `title`, `start` and `due` are set. `owner` may be `None`, because an item can be unassigned, and `speaker` is gone (see "What FR-34 lists").
  - `render_line(...)` needs no transcript.
  - The docstring states the change: the ledger starts at approval, not at extraction.
- **`test_ad28_project_ledger_rejects_personal_commitments` (`:131`) states the old rule.** It assumes a project-only ledger, which the AD-28 amendment ends. It is revised with the amendment, and not deleted silently. It becomes two tests:
  - **`test_ad28_commitment_ledgers_refuse_people_and_hold_their_own_scope` passes in 15a.** `append_commitments` refuses a `people` scope with `NotACommitmentScope`. A personal meeting's line lands in the personal ledger and never in a project's. A project meeting's line never lands in the personal one.
  - **`test_ad28_coaching_commitments_never_reach_a_commitment_ledger` keeps the old test's adversarial half.** A `CoachingCommitment` is refused by both ledgers. It skips on `pm_ai.core.coaching`, the module the spine names for coaching, until the coaching story builds that entity.

  The docstrings name the 2026-10-24 amendment.
- **`EXPECTED_SKIPS` 8 → 7** (`tests/conftest.py:189`), with a delta line in the comment above it:
  - `test_ad33_…` turns from a skip into a pass;
  - `test_ad28_…`'s skip moves to its coaching half, so the count does not change;
  - `test_ad35_…` and `test_ad36_…` keep skipping on `pm_ai.core.commitments`, story 16's;
  - the line reads "8 → 7, story 15a: `test_ad33_ledger_entries_are_self_contained` runs on a fixture; `test_ad28_…` split by the AD-28 amendment, its coaching half skipping on `pm_ai.core.coaching`".
- **`test_ad14_proposal_and_commitment_lifecycles_stay_distinct` (`:176`) passes unchanged.** `STAGED_APPROVAL` is still not a `CommitmentState`.

### Where it collides

- **13b edits the same approve path** (`app/proposals.py`, `_change` after the swap, and `_refuse_approval`). It adds an executor run for `executor` types, and 15a adds the recording for `commitment` types.
  - The two effects apply to disjoint types, so a merge is mechanical.
  - Whichever lands second rebases. The order inside `_change` is: the swap, then 15a's recording, then 13b's enqueue.
  - 13b's types record no commitment unless they say so, which keeps the `approved → rejected` edge as 13a left it for them.
- **13a's tests that change:** `test_an_approved_action_item_can_be_rejected` (`tests/slice/test_proposals.py:421`) becomes a refusal, and `test_json_objects_keep_their_commands` (`:1234`) expects `reject` unavailable on the approved card.

### No real data

Tests use `11d`'s fixture names and `example.com` handles. No real meeting, name, item text or date from the PM's store appears. The PM's own store is never read.

**Ask First:**

1. **Whether an edit of an approved item that changes its due or its wording counts as `altered`.**
   - FR-34 says `[ALTERED]` is "scope or due date updated … via direct PM edit". Andrei's 2026-10-24 decision makes `altered` the PM's own mark, counted as a manual change (SM-9).
   - **Recommended: (a) no.** An edit appends a `revised` line, and the state stays as it was. `altered` is only his explicit mark in 15c. A typo fixed in the wording is not a promise renegotiated, and counting every correction as an alteration would swell SM-9's manual count. FR-34's "via direct PM edit" is then reworded to "by the PM's own mark".
   - **(b)** An edit that changes `due` or `what` also appends a `marked state=altered` line, and an edit of `owner` alone does not.
   - **(c)** Once approved, an item can no longer be edited. Every change goes through 15c's commands, and 13a's edit of an approved item is refused like rejection.
2. **What "end of the week" and "end of the month" resolve to.** These are common in Russian meetings (`до конца недели`). A wrong date makes a promise look late, and an unresolved one never goes overdue until the PM sets a date.
   - **Recommended: (a)** end of the week is the Friday of the meeting's week, and `unresolved` when the meeting is on a Saturday or Sunday. End of the month is its last calendar day.
   - **(b)** End of the week is that week's Sunday, and end of the month its last day.
   - **(c)** Both stay `unresolved`, and the PM sets the date in 15c.

**Never:**

- No line while an item waits. No `STAGED_APPROVAL` line in either ledger.
- No line written before the approval is committed. No line written twice. No line rewritten or removed. No whole-file write of either ledger.
- No commitment in a `people` scope, or in the application scope. No `CoachingCommitment` in either ledger. No personal meeting's commitment in a project ledger, and no project meeting's in the personal one.
- No handle written for an owner. No name guessed or fuzzy-matched to a person.
- No due date guessed. No model call. No clock read by the resolver. No date earlier than the meeting's day. No overdue without a date.
- No rejection of an approved item whose type records a commitment.
- No module named `pm_ai.core.commitments`, and no stand-in `evaluate`.
- No index (15b), no `pm-ai commitment` command (15c), no change to the dashboard or the brief beyond what 13a shows (15d), and no telemetry verdict (16).
- No read of the PM's real store in a test or a manual check.
- No change to 13a's waiting, expiry, edit or re-summary rules beyond the reject edge, the card's reject action, completion's reject candidates, and the promise's new `speaker` field.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Approve an action item | a personal meeting on Mon 19 Oct, item `due` "by Thursday", `proposal approve <id>` | one `pending` line in `~/.manager-ai/memory/commitments_log.md`: `commitment=<id>`, `due_said="by Thursday"`, `due=2026-10-22`, `written=approval`; sentence "…recorded as a commitment, due Thu 22 Oct" | exit 0 |
| Approve in a project | the same in `project:alpha` | the line in `<repo>/.project-ai/memory/commitments_log.md`; none in the personal ledger | exit 0 |
| JSON | `approve <id> --json` | `commitment: {commitment_id, scope: "personal", state: "pending", recorded: true, due: {said, date: "2026-10-22", value: "2026-10-22"}}` | exit 0 |
| Waiting item | staged, not approved | nothing in either ledger | N/A |
| Approve twice | approve the same id again | `already`; the line is there, so nothing is appended | exit 0 |
| Two processes | the terminal and the prompt approve one id at once | one swap wins (13a); exactly one `pending` line | both exit 0 |
| Ledger changed between read and append | a catch-up appends between the approval's read and its append | `LedgerChanged`; read again, the line is still missing, so it is appended once | exit 0 |
| Append fails | the claim is busy past its wait, or the disk is full | approved; `recorded: false` with the reason; the sentence names running it again | exit 0 |
| Repair by approving again | the case above, then `approve <id>` | `already`; the missing `pending` line written | exit 0 |
| Project git refuses | the project's `.gitignore` no longer excludes `memory/` | approved; not recorded, reason the git refusal (AD-43) | exit 0 |
| Spoken promise | `implicit_commitment` with `what` "send the numbers", `when` "Friday", speaker `Sam Rivera` | `pending` line, `owner="Sam Rivera"`, `due` the Friday after the meeting | exit 0 |
| Old promise row | a promise staged before 15a, then approved | line without `owner` | exit 0 |
| Staged command | `explicit_command` | approval unavailable (13a); no line | exit 3 |
| Edit with approve | waiting item, `edit --due "Friday" --approve` | `<root>_e1` approved; one `pending` line, `commitment=<root>`, `proposal=<root>_e1`, the Friday | exit 0 |
| Edit an approved item | approved, then `edit --what "send the TTL numbers"` | `<root>_e1` approved; one `revised` line, `replaces=<root>`; state still `PENDING` | exit 0 |
| Edit, same values | approved, edit to the values it holds | `unchanged`; no line | exit 0 |
| Edit a waiting item | `edit --due "Monday"`, no `--approve` | replacement waiting; no line | exit 0 |
| Expired item staged again and approved | expired, `edit --approve` | `pending` line for the root, from the replacement | exit 0 |
| Reject after approval | approved action item, `proposal reject <id>` | refused, "…is a commitment now, so it is not rejected…"; nothing changed | exit 3 |
| Reject in JSON | the same with `--json` | refusal object, `state: "approved"` | exit 3 |
| Card of an approved item | `proposal show <id> --json` | `actions.reject.available: false` with the reason; `edit` available | exit 0 |
| Reject a waiting item | staged, `reject` | rejected as in 13a; no line | exit 0 |
| Due unresolved | `due` "next sprint" | `due_said="next sprint"`, `due=unresolved`; sentence "with no due date worked out from \"next sprint\"" | exit 0 |
| No due | `due` absent | no `due_said`, `due=none`; "with no due date said" | exit 0 |
| Meeting's own weekday | meeting on a Thursday, "by Thursday" | `due=unresolved` | exit 0 |
| Russian forms | meeting Mon 19 Oct: `до четверга`, `завтра`, `через неделю`, `3 ноября`, `30.10` | 22 Oct, 20 Oct, 26 Oct, 3 Nov, 30 Oct | N/A |
| Refused forms | `next Thursday`, `в следующий четверг`, `10/11`, `Thursday morning`, `30 February`, `1 Jan 2026` (before the meeting) | each `unresolved` | N/A |
| End of week | `до конца недели`, meeting Mon 19 Oct | Ask First 2: (a) `2026-10-23`; (c) `unresolved` | N/A |
| Day from a modification time | record `start_estimated_from=file-modified` | every `due=unresolved` | N/A |
| Record unreadable | the meeting record does not parse at approval | line without `title` and `start`; `due=unresolved` | exit 0 |
| Display zone | meeting 22:30 UTC Sunday, `display_timezone` Europe/Istanbul (Monday 01:30) | the day is Monday | N/A |
| Catch-up of old approvals | three personal action items approved before 15a, ledger empty; daemon starts | three `pending` lines, `written=catch_up`, `occurred` each proposal's `changed_at` | N/A |
| Catch-up again | the daemon restarts | nothing appended | N/A |
| Catch-up after a lost edit line | approved root edited, the `revised` line not written | `revised` line from `<root>_e1` | N/A |
| Catch-up, no change time | an approved row with no `changed_at` | no line; counted in the log | N/A |
| Catch-up, unreadable | one approved body does not parse | counted, logged without content; the others written | N/A |
| Catch-up, project unreadable | a project's ledger read fails | that project logged by name; the others written; daemon starts | N/A |
| People scope | `append_commitments(…, scope=people:example)` | `NotACommitmentScope` naming it; nothing read or written | refused |
| Whole-file write | `write_artifact(…, "commitments_log.md")` for personal | refused as append-only | refused |
| Fold order | lines given in file order B then A, where A has the earlier `occurred` | the same commitment as in order A, B (AD-35) | N/A |
| Duplicate line | a hand-copied line with the same `entry` | ignored, counted as a duplicate | N/A |
| Torn last line | the file ends mid-line | that fragment is not a record; the next append starts on a new line | N/A |
| Orphan | a `revised` line with no `pending` | ignored, counted | N/A |
| Hand-written mark | a `marked state=altered by=pm` line (as 15c will write) after `pending` | fold gives `ALTERED` | N/A |
| Mark table | `can_mark(FULFILLED, altered)`, `can_mark(BROKEN, fulfilled)` | refused; allowed | N/A |
| Mark outranks a verdict | `marked state=broken`, then a later `verdict state=fulfilled` | still `BROKEN` | N/A |
| Large ledger | 5,000 lines in the scope's ledger | `approve` within 2 s at the prompt, measured | exit 0 |
| Hostile text | a `what` holding a newline and a quote | one line, quoted and escaped by `render_value`; reads back equal | N/A |
| Team Activity due | a `7e` row with due "3 Jan 2027" | now shows `(2027-01-03)` beside the words | N/A |

</frozen-after-approval>

## Code Map

Anchors checked against `origin/main` at `b6f8f62`.

- `pm_ai/domain/lifecycle.py:28` `ProposalState`; `:49` `CommitmentState` (`PENDING`, `FULFILLED`, `ALTERED`, `BROKEN`, `UNKNOWN`, `ERROR`), `:61` `is_terminal`; `:76` the AD-14 disjointness check; `:208` `evaluate_commitment` (story 16's)
- `pm_ai/domain/proposals.py:76` `TypeRule` (gains `records_commitment`); `:108` `allowed`, whose `approved → rejected` changes; `:145` `root_of`; `:276` `check_change`
- `pm_ai/core/proposals.py:98` `ApprovalKind`; `:127` `ProposalType`, `:139` `rule`; `:276` `ACTION_ITEM_TYPE`; `:289` `IMPLICIT_COMMITMENT_TYPE` (both gain `commitment=True`); `:84` `OWNER_LIMIT`. The brief's `pm_ai/domain/proposal_types.py` does not exist; the registrations are here.
- `pm_ai/app/proposals.py:625` completion's reject candidates; `:659` `approve`; `:664` `reject`; `:697` `_change`, `:758` its approval sentence; `:761` `_already`; `:891` `_edit`
- `pm_ai/core/proposal_cards.py:268-274` the approved card's `reject` action
- `pm_ai/app/proposal_reads.py:89` `ProposalWorld.scope_of`; `:134` the zone
- `pm_ai/storage/service.py:2318` `_require_project`; `:2327` `_ledger_text`; `:3337` `_assert_git_excludes`; `:4118` `assert_writable`; `:4341` `read_member_updates`; `:4355` `append_member_updates`; `:4404` `_compare_and_append`; `:6470` `change_proposal`; `:6507` `edit_proposal`
- `pm_ai/domain/scope_model.py:532` `PERSONAL_TREE`, `:553` its `memory/`; `:762` the project's `commitments_log.md`; `:1017` `BACKUP_TARGETS`
- `pm_ai/domain/storage_tiers.py:196` `_APPEND_ONLY_KEYS`; `:241` `restricted_mode`
- `pm_ai/domain/event_entries.py:212` `MAX_ENTRY_LENGTH`; `:367` `render_value`; `:456` `scan_fields`
- `pm_ai/core/member_updates.py:469` `_WEEKDAYS`, `:489` `_MONTHS`, `:505` `_LEADING`; `:520` `resolve_due`; `:628` `render_update`, the line precedent; `:1051` the due shown in Team Activity
- `pm_ai/core/aliases.py:144` `AliasTable`, `:161` `handle_for`
- `pm_ai/domain/handles.py:566` `pm_identity`
- `pm_ai/core/extraction.py:104` `Promise` (payload without a speaker); `:125` `Extraction.speaker_handle`, the label
- `pm_ai/app/pipelines.py:1237` the promise's payload; `:4088` the action item's payload; `:1598` `display_timezone`
- `pm_ai/core/meeting_records.py:803` `MeetingRecords`, `:999` `get`; `:205` `start_estimated_from`
- `pm_ai/core/event_log.py:54` `EventLog`, the accessor shape
- `pm_ai/app/daemon.py:646` the sweep attached; `:1033` `_lifespan`
- `pm_ai/app/wiring.py:720` `registered_proposal_types`; `:4102` `HANDED_CALLS`; `:4186` `PLAIN_READS`; `:4223` `NEVER_FROM_A_JOB`
- `pm_ai/ports/__init__.py:807`, `:810` the member-updates calls on the port
- `tests/architecture/test_domain_invariants.py:131` AD-28; `:176` AD-14; `:628` AD-33, `:649` `_sample_extraction`; `:736` AD-35's sweeper; `:750` AD-36. Four skip today on `pm_ai.core.commitments`.
- `tests/conftest.py:189` `EXPECTED_SKIPS = 8`
- `tests/slice/test_proposals.py:421`, `:1234` -- an approved item rejected, and its card's reject command
- `tests/core/test_member_updates.py:281-303` -- `resolve_due`'s cases (`"3 Jan 2027"` → `None` changes)
- `tests/architecture/test_capture_guard.py:517` `_preflight_commitments`; `tests/architecture/test_paths.py:169` the personal rows; `tests/architecture/test_encryption_policy.py:52`
- `ARCHITECTURE-SPINE.md:305` AD-13; `:313` AD-14; `:446` AD-28; `:505` AD-33; `:554` AD-36; `:570` AD-37; `:1100` the module map
- `SPEC.md:51` CAP-6's success; `glossary.md:35` the ledger's entry; `prd.md:653` FR-34; `success-metrics.md:17` SM-9; `derivation-services.md:99`, `:108`, `:117`, `:125`

## Tasks & Acceptance

**Execution:**
- [ ] **The entity.** `pm_ai/domain/commitments.py`: `Commitment`, `Due`, `LineKind`, `MarkedState`, `MARKABLE`, `can_mark`, `NotACommitmentScope`.
- [ ] **The resolver.** `pm_ai/core/due_dates.py`: `resolve`, with the tables moved from `member_updates.py`, and `member_updates.resolve_due` calling it.
- [ ] **The lines and the accessor.** `pm_ai/core/commitment_log.py`:
  - `render_line`, `parse_ledger` and `fold` (by `(occurred, entry)`, with duplicates, orphans and malformed lines counted);
  - `from_approved`;
  - `CommitmentLog.read` and `append`.
- [ ] **The personal ledger.** `pm_ai/domain/scope_model.py`: the `File` in `PERSONAL_TREE`'s `memory/`.
- [ ] **The store.** `pm_ai/storage/service.py`: `read_commitments` and `append_commitments`, `NotACommitmentScope`, the git check for a project. `pm_ai/ports/__init__.py`: both on the port. `pm_ai/app/wiring.py`: `HANDED_CALLS` and `PLAIN_READS`.
- [ ] **No rejection after approval.** `pm_ai/domain/proposals.py` (`TypeRule.records_commitment`, `allowed`), `pm_ai/core/proposals.py` (`commitment=True` on two types), `pm_ai/core/proposal_cards.py` (the approved card's reject), `pm_ai/app/proposals.py` (the refusal, completion).
- [ ] **The recorder.** `pm_ai/app/commitments.py`: `CommitmentRecorder.record` and `catch_up`, built in the composition root. Called from `ProposalDesk._change`, `_already` and `_edit`. Its outcome goes in the sentence and in the JSON `commitment`.
- [ ] **The catch-up at start.** `pm_ai/app/daemon.py` `_lifespan`.
- [ ] **The promise's speaker.** `pm_ai/core/extraction.py` `Promise.payload` gains `speaker`, filled by `pm_ai/app/pipelines.py`'s staging. `IMPLICIT_COMMITMENT_TYPE`'s card shows "promised by <speaker>" when it is recorded.
- [ ] **New tests.** `tests/core/test_due_dates.py` covers every form in both languages and every refused form. `tests/core/test_commitment_log.py` covers render and parse round-trips, the fold order, duplicates, orphans, a torn line, the mark table, and a mark outranking a verdict. `tests/slice/test_commitments.py` has one test per matrix row, two processes approving one id, the timed approve over 5,000 lines, and the catch-up on 7b-shaped fixtures.
- [ ] **Existing tests that change:**
  - `test_domain_invariants.py`: AD-33 rewritten on its fixture; AD-28 split into its two tests, with the amendment named;
  - `tests/conftest.py`: `EXPECTED_SKIPS` 8 → 7, with its delta line;
  - `test_proposals.py:421` and `:1234`: the refusal, and reject unavailable on an approved card;
  - `test_member_updates.py`'s `"3 Jan 2027"` row;
  - `test_paths.py` and `test_encryption_policy.py`: the personal `commitments_log.md`;
  - `test_capture_guard.py:517`: the project ledger's guard reached through `append_commitments`, not only the pre-flight.
- [ ] **The contract.** Apply the amendments in Design Notes, each through the skill that owns the file:
  - `ARCHITECTURE-SPINE.md`, through the architecture skill;
  - `SPEC.md`, `glossary.md`, `scope-model.md`, `derivation-services.md` and `success-metrics.md`, through bmad-spec;
  - `prd.md` FR-34, through bmad-prd.

**Acceptance Criteria:**
- Given a summarised personal fixture meeting with three waiting items, when the PM approves one through `pm-ai proposal approve`, then `~/.manager-ai/memory/commitments_log.md` gains exactly one `pending` line for that root, and nothing for the other two.
- Given that approved item, when it is approved again, when the daemon starts twice, and when two processes approve it at once, then the ledger still holds one `pending` line for it.
- Given three approved fixture items and an empty ledger, when the daemon starts, then three `pending` lines are written with `written=catch_up` and `occurred` equal to each proposal's `changed_at`; and on the next start nothing more is written.
- Given an approved action item, when `proposal reject` runs on it, then it is refused with exit 3, and the row and the ledger are unchanged.
- Given a meeting on Monday 2026-10-19, when items due "by Thursday", "до четверга", "next sprint" and nothing are approved, then their `due` values are `2026-10-22`, `2026-10-22`, `unresolved` and `none`.
- Given the change, when `uv run pytest` runs, then everything passes; `test_ad33_ledger_entries_are_self_contained` passes instead of skipping; `test_ad35_…` and `test_ad36_…` still skip on `pm_ai.core.commitments`; and the skip count is 7.

## Spec Change Log

- **2026-10-24, created** after a readiness check, from story 15's entry and Andrei's six decisions of 2026-10-24, given to the coordinating session:
  1. the ledger starts at approval;
  2. personal meetings get a personal ledger;
  3. the PM's own mark of done, altered or broken, counted apart from telemetry;
  4. the due kept as said and resolved to a date against the meeting's day, or unresolved and never overdue;
  5. 15b's index started by a trigger inside the daemon and checked at daemon start;
  6. no rejection once an item is a commitment.

  Story 15 is split into 15a (this spec), 15b (index), 15c (commands) and 15d (surfaces). Two Ask First questions are open.

## Design Notes

**Why the line follows the approval, and is not written in the same step.** `operational.db` and `commitments_log.md` are two files, so no transaction spans both. Committing the approval first can leave an approved item without its line, which the catch-up and a second `approve` repair. Writing the line first can leave a commitment the PM never approved, in an append-only file that cannot take it back. Deterministic entry ids make every repair write nothing a second time.

**Why rejection is refused by the type, not by looking at the ledger.** "Refuse once a commitment exists" and "refuse once approved" are the same rule, because the ledger starts at approval. Putting it in the domain table means the store enforces it in the same conditional update for every surface. An approval whose line has not been written yet cannot slip past it.

**Why the owner is a name.** A spoken or typed name says nothing a provider vouched for. Writing a handle on a guess would be identity invented at write time, and a ledger line is never rewritten. Readers link names through the saved alias table, as `7e` and `11e` do, so a correction applies to every past line.

**Why one resolver, and why it refuses so much.** Team Activity already shows a date beside a due (`7e`). A second resolver would let the dashboard and the ledger show two dates for one promise. A due date decides whether a promise looks broken, so the resolver names a date only when the words name exactly one. Everything else is `unresolved`, which the PM can fix in one command (15c), and which never makes a promise look late.

**Why not `pm_ai.core.commitments`.** Four architecture tests skip on that name. Two of them call a verifier, `evaluate`, that is story 16's work. Creating the module in 15a without it would turn them into failures, and a stand-in would turn them into passes that check nothing. The ledger and the verifier are separate modules, and the spine's map is amended to say so.

**Accepted limits:**
- A promise staged before 15a has no recorded speaker, so its commitment has no owner.
- A meeting whose start came from a file's modification time gives no due dates.
- An approval whose line could not be written is recorded at the next `approve` of the same id or the next daemon start, not sooner.

**Amendments this depends on.** Each is applied with the build, through the skill that owns the file (a task above). The sentences below are exact.

- **AD-13** (`ARCHITECTURE-SPINE.md:305`), in the `[2026-10-22, Andrei, 13a]` paragraph: replace "`approved → rejected` for a type with no executor;" with "`approved → rejected` for a type with no executor whose approval records no commitment `[2026-10-24, Andrei, 15a]`;".
- **AD-14** (`:313`): replace "— recorded by story 15, keyed by the proposal's id; until then approval records only the proposal's state;" with "`[2026-10-24, Andrei, 15a]` — one `pending` line appended to the `commitments_log.md` of the scope that owns the meeting when the PM approves, never while the proposal waits (`STAGED_APPROVAL` is a proposal state and is never written to a ledger), keyed by the proposal's root id, so an edit's replacement revises the same commitment; the approval is committed first and the line follows, and a missing line is written by a catch-up, never twice; once approved, such a proposal is no longer rejected, and the commitment changes only through its own lines: the PM's mark (`fulfilled`, `altered`, `broken`), a due date he sets, or a telemetry verdict, and a verdict never replaces the PM's mark;".
- **AD-28** (`:446`):
  - The title becomes "Commitments belong to the scope that owns their meeting; coaching commitments are a separate entity".
  - In the table, the `Commitment` row's scope becomes "the meeting's scope: project — `.project-ai/memory/commitments_log.md`, or personal — `~/.manager-ai/memory/commitments_log.md` `[2026-10-24, Andrei, 15a]`; never `people` (AD-4)".
  - Replace "The project-scope ledger has no code path that accepts a personal or people entity." with "Neither commitment ledger has a code path that accepts a `CoachingCommitment` or a `people`-scope entity, and a commitment is written only to its own meeting's scope: a personal meeting's never to a project's ledger, a project meeting's never to the personal one `[2026-10-24, Andrei, 15a]`."
  - Add `15a` to **Binds**.
- **AD-36** (`:554`): after "self-authored activity may be displayed for context but never counted.", add: "`[2026-10-24, Andrei, 15a]` The PM's own mark of a commitment as fulfilled, altered or broken is not evidence and needs none: it is his decision, recorded as a line of its own kind under his handle, counted apart from telemetry verdicts (SM-9's manual override), never produced by pm-ai and never read as telemetry having verified anything."
- **AD-37** (`:570`): add `15a` to **Binds**, and after "Terminal states are terminal, and a worker re-checks state **at execution time**, not only at enqueue time." add: "`[2026-10-24, 15a]` A commitment's lines are appended by compare-and-append: the writer folds the ledger, leaves out every line whose entry id is already there, and appends with the size it read checked in its claimed step; a ledger that grew is read again, never appended to blindly."
- **The module map** (`:1100`): "`core/commitments` + storage" becomes "`domain/commitments`, `core/commitment_log` (the ledger, `15a`), `core/due_dates`, `core/commitments` (the verifier, story 16) + storage".
- **CAP-6** (`SPEC.md:51`), the success: replace "and writes a `[STAGED_APPROVAL]` entry to `commitments_log.md` that posts nothing until approved." with "staged as a proposal whose state is `[STAGED_APPROVAL]`, which posts nothing and writes nothing to `commitments_log.md` until approved; approval writes one `[PENDING]` entry to the meeting's scope's `commitments_log.md` (2026-10-24, Andrei, 15a)."
- **Glossary** (`glossary.md:35`):
  - replace "structured Markdown in the project scope's `commitments_log.md`" with "structured Markdown in the `commitments_log.md` of the scope that owns the meeting — a project's, or the personal scope's for a personal meeting, never a `people` scope (2026-10-24, Andrei, 15a)";
  - replace "lifecycle statuses `[STAGED_APPROVAL]`, `[PENDING]`," with "`[STAGED_APPROVAL]`, the proposal's state while the PM has not yet approved (never written to the ledger, which starts at approval — 2026-10-24, Andrei, 15a), then the ledger's statuses `[PENDING]`,";
  - after "needs a human to re-authenticate or fix configuration)", add "; a status is set by telemetry or by the PM's own mark, which is recorded under his handle and counted apart from telemetry; a due is kept as said beside the date worked out from it, or `unresolved`, and a commitment with no date is never overdue".
- **FR-34** (`prd.md:653`):
  - In the header, replace "(.project-ai/memory/commitments\_log.md and indexed" with "(the commitments\_log.md of the scope that owns the meeting — a project's, or the personal scope's — and indexed".
  - Replace 34.1's first sentence with "**Extraction & Initial Staging:** Commitments extracted from a meeting (FR-03, FR-06) are staged as proposals while they wait for the PM; \[STAGED\_APPROVAL\] is the proposal's state and is never a ledger entry. When the PM approves one, one structured Markdown entry is appended to the commitments\_log.md of the scope that owns the meeting and indexed in commitment\_index.db (Tier 3, disposable)."
  - Replace "Each entry records: commitment\_id, timestamp, speaker, target\_assignee, description, target\_work\_item, due\_date, and initial status (\[STAGED\_APPROVAL\] or \[PENDING\])." with "Each entry records: commitment\_id (the approved proposal's id), the approval time, the owner as a name (linked to a person only through an exact saved alias, when read), the description, the due as said and the date worked out from it against the meeting's day or `unresolved`, the meeting it cites, and status \[PENDING\]; the target work item is joined when the commitment is verified."
  - The \[STAGED\_APPROVAL\] bullet becomes "\[STAGED\_APPROVAL\]: the proposal's state while a spoken commitment waits for the PM's approval on a Telegram/CLI card; nothing is in the ledger yet. Approval writes the commitment at \[PENDING\]."
  - With Ask First 1 (a), \[ALTERED\]'s "or via direct PM edit" becomes "or by the PM's own mark".
  - Add after the \[ERROR\] bullet: "**The PM's own mark.** The PM may mark a commitment fulfilled, altered or broken himself; the mark is his, recorded under his handle, needs no telemetry evidence, outranks a telemetry verdict, and is counted apart from telemetry (SM-9's manual override). **No date, never overdue:** a due that names no single date is kept as said and recorded as unresolved; such a commitment is never overdue until the PM sets a date."
  - In the first consequence, replace "in .project-ai/memory/commitments\_log.md" with "in the meeting's scope's commitments\_log.md".
  - Decisions Log line: "*The ledger starts at approval (2026-10-24, Andrei; FR-34, FR-06, Glossary):* \[STAGED\_APPROVAL\] is a proposal state, never a ledger entry; personal meetings keep a personal ledger; the PM's own mark is recorded under his handle and counted apart from telemetry; a due is resolved to a date against the meeting's day or recorded as unresolved."
- **SM-9** (`success-metrics.md:17`): "versus manual override" becomes "versus manual override (the PM's own marks, counted from their own ledger lines, 2026-10-24)".
- **`scope-model.md`** §B: draw `commitments_log.md` under the personal `memory/`, "Commitments from purely personal meetings (2026-10-24, 15a)".
- **`derivation-services.md`:**
  - line 108's transcript-processing outputs lose "commitments → `commitments_log.md`";
  - line 125's `CommitmentLog` writers become "the approval and its catch-up (append, `15a`), the PM's marks (`15c`), the lifecycle job (append verdicts, story 16); indexing reads";
  - line 117's chain becomes "… transcript processing runs → proposals are staged → the PM approves → `commitments_log.md` gains an entry → commitment indexing runs → the index is current";
  - line 99's example names the approval, not transcript processing, as the ledger's writer.

## Verification

**Commands:**
- `uv run pytest tests/core/test_due_dates.py tests/core/test_commitment_log.py tests/slice/test_commitments.py tests/slice/test_proposals.py tests/core/test_member_updates.py tests/architecture -q` -- expected: every matrix row passes; `test_ad33_…` passes; the two AD-28 tests pass and skip as stated
- `uv run pytest -q` (in the background, about 7 minutes) -- expected: all pass; 7 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a scratch home (`HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1`), drop and summarise the recorded fixture into personal, then `pm-ai proposal approve` one item and `edit --due "до пятницы"` another with `--approve`. Expected: `~/.manager-ai/memory/commitments_log.md` under the scratch home holds two `pending` lines, the second with `proposal=<root>_e1` and the Friday's date. `proposal reject` on the first is refused.
- In the same scratch home, approve one more item with the daemon stopped, delete nothing, start the daemon, and stop it. Expected: no duplicate. Then append a copy of one line by hand and run the tests' reader. Expected: it is counted as a duplicate.
