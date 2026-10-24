---
title: 'approved items become commitments, in the meeting''s own ledger'
type: 'feature'
created: '2026-10-24'
status: 'draft'
baseline_commit: 'b6f8f62'
review_loop_iteration: 1
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/13a-proposals-approved-edited-or-rejected.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Since `13a`, the PM approves the action items a meeting agreed (`7b`) and the promises a transcript holds (`11b`). An approved item then stays `approved` in `operational.db`, and nothing else happens. 13a left the next step to story 15: "Story 15 records a commitment for each approved `action_item` and `implicit_commitment`, keyed by the proposal's id, including those approved before 15 exists" (13a, Prerequisites).

Today nothing records one:
- **No ledger is written.** No code writes `commitments_log.md`. The project tree declares it (`scope_model.py:762`). The personal tree does not declare it at all, so a personal meeting's approved items have nowhere to go, and the first items the PM approved are personal.
- **No entity or format.** `CommitmentState` and `evaluate_commitment` exist (`lifecycle.py:49`, `:208`), but there is no `Commitment` entity, no ledger line format, no reader and no accessor.
- **No date.** A due is kept only as said ("by Thursday"), and nothing turns it into a date that a later check could call overdue.
- **No backup.** The PM's decisions live only in `operational.db`, which is Tier 2 and not backed up (13a, Design Notes, "Accepted limit, until story 15").
- **An agreed task can vanish.** 13a lets the PM reject an approved action item.

**Approach:** Approval writes the commitment.
- **One line at approval.** When the PM approves an `action_item` or an `implicit_commitment`, pm-ai appends one `pending` line to the `commitments_log.md` of the scope that owns the meeting: a project's, or the personal scope's for a personal meeting. Nothing is written while an item waits.
- **Written once.** Every line has an id worked out from what it records. The commitment is keyed by the proposal's root id. An approval that is repeated, retried or caught up never writes a second line.
- **The due date.** The due is kept as said. A date is also worked out from it against the meeting's day, which the line stores. A pure resolver does this, in English and Russian, with no model.
  - When the words do not name one date, the line says `unresolved`, and such a commitment is never overdue.
  - When the meeting's day was estimated, the due is marked estimated, so no later slice calls a promise late on a guessed day.
- **Who.** An action item's owner is recorded as a name. A spoken promise records who said it (`speaker`) and has no owner unless the PM sets one. Neither is written as a handle. A reader links a name to a person only through an exact saved alias.
- **An edit after approval** appends a `revised` line with the new values. The commitment's state does not change. "Altered" comes only from the PM's own mark.
- **No rejection once approved.** An approved item can no longer be rejected. The refusal points to what works today, `proposal edit`.
- **The PM's own marks.** 15a defines the line format, and the states each mark is allowed from, for done, altered, broken and withdrawn. "Withdrawn" is for an approval made by mistake. Story 15c's commands write these lines.
- **A catch-up at daemon start** writes the missing lines, for items approved before 15a and for any approval whose line was not written. Approving the same item again also writes its missing line.
- **Left for later slices:** the index (15b), the commands (15c) and the surfaces (15d).

## Boundaries & Constraints

**Always:**

### Prerequisites and the slices of story 15

- **Built and relied on:**
  - `13a`: the proposal engine (`ProposalDesk`), the registered types, the edge table, `<root>_e<n>` replacements, and `changed_at`, `changed_via` and `scope` in the body;
  - `7b`: `action_item` proposals with `what`, `owner` and `due` as said;
  - `11b`: `implicit_commitment` proposals with `what` and `when`;
  - `11a`: `MeetingRecords`, with title and start;
  - `11e`: `start_estimated_from` and `ESTIMATE_SOURCES` (`domain/meetings.py:51`);
  - `7e`: the ledgers' line encoding, compare-and-append, and today's `resolve_due`;
  - `7e` and `11e`: the saved alias table, and linking a name when the record is read;
  - `13d`: the cards.
- **Story 15 is split into four slices.** 15a is written in full here; the others get specs of their own.
  - **15a, this spec:** the `Commitment` entity, the `CommitmentLog` accessor and its line format, the due-date resolver, the line written on approval and on an edit of an approved item, the catch-up, no rejection after approval, and the personal ledger.
  - **15b, the index:** `commitment_index.db` (CAP-34's "indexed row at `[PENDING]`"), rebuilt from the Markdown alone. Each row stores the MD5 of `commitments_log.md` (story 15's notes). Until story 10's queue exists, a trigger inside the daemon runs it after each append, and a check runs it at daemon start (Andrei, 2026-10-24).
  - **15c, the commands:** `pm-ai commitment list|show|done|altered|broken|withdrawn|due|owner`. These write the PM's marks, the due date he sets, and the owner he sets, in the line kinds 15a defines.
  - **15d, the surfaces:** the dashboard and the morning brief show commitments in place of approved proposals, and the cards show a commitment's state.
- **What other stories add.**
  - **Story 16** writes telemetry verdicts under their own line kind, and joins a commitment to a work item.
  - **`10a`** adopts this `CommitmentLog` as the accessor its commitment-indexing job reads (`derivation-services.md` rule 3). `11a` building `MeetingRecords` before `10a` is the precedent.

### When a commitment is written (Andrei, 2026-10-24)

- **The ledger starts at approval.** `STAGED_APPROVAL` is a proposal state (AD-14), never a ledger line. A waiting, rejected, expired or superseded proposal writes nothing.
- **Types that record a commitment:** `action_item` and `implicit_commitment`. Their registration gains `commitment=True`, and `ProposalType.rule` (`core/proposals.py:139`) copies it into `TypeRule.records_commitment`. `explicit_command` and unknown types record none. A type that 13b gives an executor records none unless its registration says so.
- **What writes a `pending` line:** a `record()` call (below) after
  - `proposal approve` of a waiting item;
  - `proposal edit --approve` (13a) of a waiting or an expired item, with the replacement's values;
  - `review`'s `a`, and its edit followed by `Y`;
  - an `approve` that finds the item already approved, for that item's root only;
  - and the catch-up at daemon start.
- **The order of the two writes.** The approval is committed in `operational.db` first, in 13a's claimed step. The line is appended afterwards, in its own claimed step on the same writer.
  - A crash, a busy claim, a full disk or a git refusal between the two leaves an approved item with no line.
  - The same `proposal approve <id>` run again writes it, and so does the next daemon start.
  - The reverse order could leave a `pending` line for an approval that was never committed, and an append-only ledger cannot take that back.
- **Which ledger.** The meeting's scope's: the proposal's stored `scope`, else 13a's `ProposalWorld.scope_of` (`proposal_reads.py:89`).
  - A proposal whose scope cannot be told is never approved (13a).
  - A `people` scope never reaches the ledger. 13a refuses it, and the store refuses it again (`NotACommitmentScope`, below).

### Personal meetings get a personal ledger (Andrei, 2026-10-24, amends AD-28)

- **`PERSONAL_TREE`'s `memory/` gains `commitments_log.md`:** `File("commitments_log.md", Tier.TRUTH, encrypted=False, gitignored=False)`. The flags are those of its siblings `meetings/` and `event_log/`:
  - plaintext Markdown (AD-6);
  - Tier 1, so backed up (`BACKUP_TARGETS` follows the tier);
  - not gitignored, because the personal scope may be the PM's own private repository (`storage_tiers.py:251`);
  - `0600`, like every personal file (`restricted_mode`, story 1s).

  One basename has one tier in every scope (AD-44), and both copies are `TRUTH`.
- **`people` gets none (AD-4).** `PEOPLE_TREE` is unchanged. A promise from a 1:1 with a direct report is not a `Commitment` (AD-28 files it as a `CareerGoal`).
- **A `CoachingCommitment` never reaches either ledger** (AD-28). `CommitmentLog.append` accepts only `CommitmentLine` values, which only `render_line` builds from a `Commitment` or a mark. Anything else is refused with `NotACommitmentLine` before the store is called.
- **Append-only already.** The append-only list names `commitments_log.md` by basename (`storage_tiers.py:196`), so the personal copy is append-only with no change there, and a whole-file write of it is refused.

### The `Commitment` entity

- **`pm_ai/domain/commitments.py`** declares `Commitment`, `Due`, `LineKind`, `Mark`, `StateSource`, the mark table `MARKABLE`, `can_mark`, `NotACommitmentScope` and `NotACommitmentLine`. It imports only sibling domain modules (AD-30).
- **`CommitmentState` gains `WITHDRAWN`** (`lifecycle.py:49`), value `"withdrawn"`, for an approval the PM says was a mistake.
  - It is terminal, so telemetry never evaluates it, and it is a verdict, a claim about the world.
  - It is not a value of `ProposalState`, so AD-14's import check still passes.
  - `evaluate_commitment` never returns it.
- **A `Commitment` holds:**
  - `commitment_id`: the proposal's root id (`root_of`, `domain/proposals.py:145`), the id `7b` or `11b` minted. An edit's replacement `<root>_e<n>` is the same commitment;
  - `proposal_id`: the proposal whose values it now holds;
  - `type`: `action_item` or `implicit_commitment`;
  - `meeting_id`, and the meeting's `title` and `start` as the record gave them when the `pending` line was written. Both are absent only when the record could not be read;
  - `day`, the meeting's day the due was worked out against, with `day_from` when that day was estimated (below);
  - `what`, the description: the cleaned text the PM approved on the card, or the text he typed in an edit;
  - `owner`, a name or none, for an action item; for a promise, none unless the PM sets one (15c's `owner_set`);
  - `speaker`, for a promise: who said it, a name;
  - `due`, a `Due`;
  - `state`, a `CommitmentState`, and `state_source`, one of `none`, `pm` or `telemetry`;
  - `approved_at`;
  - `cites`: `meeting:<id>`, plus the speaker for a promise (AD-33, amended below).

  It needs no transcript to be acted on or shown (AD-33).
- **What FR-34 lists that 15a does not record, and why.**
  - **A speaker for an action item.** `7b` records who owns an item, not who said it, so an action item's line has `owner` and no `speaker`.
  - **`target_work_item`.** An item names a work item only through `7e`'s link in `member_updates.md`, which may be model-matched and unconfirmed. Story 16 joins it when it verifies.

### Who: names, linked only when read (AD-34, `7e`, `11e`; Andrei, 2026-10-24)

- **An action item's `owner=`** is `7b`'s owner label, or the name the PM typed in an edit, limited to 80 characters (`OWNER_LIMIT`). The typed value is refused over the limit, as 13a already does.
- **A spoken promise's `speaker=`** is who said "I'll …". `11b`'s staging stores it from now on in the payload, as `speaker: {"raw": <label>, "for_model": <cleaned>}`:
  - `raw` is the transcript's label as given (AD-29);
  - `for_model` is that label through the same `sanitize` as `what`, with line breaks and control and format characters folded;
  - a cleaned label over 80 characters is not stored, since a shortened name is a different name.

  The line records the cleaned text. A promise has no `owner` unless the PM sets one in 15c (`owner_set`). Its card reads `said by <speaker>: <what>, by <when>` (`core/proposals.py:262`, `_promise_card`).
- **A promise staged before 15a** has no speaker stored, so its line has neither `speaker` nor `owner`. That is a known limit.
- **A re-ingestion is not changed by the new field.** When a stored promise payload has no `speaker` and is otherwise equal to the new one, the two compare as equal:
  - in `stage_unless_acted`'s change check (no rewrite, no version bump);
  - and in `_dropped_over` (`pipelines.py:1268`), so there is no false "dropped" count.
- **Why names and not handles.** The kernel's rule that "every person on a ledger line is recorded as the provider's own handle" is about people a provider identified. A spoken or typed name is a display name, and the glossary says a display name is never a handle. AD-34's saved alias table is "the one saved place a name becomes a handle", and "a ledger records a name as said … a reader joins them". This is `7e`'s rule for `member_updates.md` and `11e`'s for `## Participants`.
- **How a reader links a name.** By `AliasTable.handle_for(name, meeting)` (`aliases.py:161`): a row limited to the line's meeting first, then an application-wide row, on `normalise()`'s exact key.
  - Otherwise the name stays a name, shown as said. It is never fuzzy-matched or guessed.
  - A generic label (`Speaker 2`, `Unknown` and their Russian forms) links only through a row limited to that meeting, as in `7e`.
  - A name holding more than one person (`,`, `/`, `&`, ` and `, ` и `) is never linked.
  - On a project page, `7e`'s exact match to one GitLab member's display name or username also applies, and is never saved.
  - 15a writes and reads names only. The linking readers are 15b's and 15d's, and a corrected alias applies to every past line.

### The meeting's day (Andrei, 2026-10-24)

- **Worked out once, when the `pending` line is written, and stored on it** as `day=<YYYY-MM-DD>`. A `revised` line, a `due_set` line and the catch-up all resolve against the stored day, never against the zone or clock of the moment.
- **The day is the date of the record's `start`:**
  - in `display_timezone`, when it is set;
  - when it is not, in the machine's zone for a start read in that zone (the `-machine-zone` sources below, `pipelines.py:1575`);
  - otherwise in UTC.

  Every source records a time today. Should one ever record a date alone, the day is that date as written, with no zone shift.
- **An estimated start** (`start_estimated_from`, every value in `ESTIMATE_SOURCES`):

  | `start_estimated_from` | Day | On the line |
  | --- | --- | --- |
  | absent (a calendar start, or one the PM typed) | the start's day | no `day_from` |
  | `docx-created` | the start's day | `day_from=docx-created`; the due is estimated |
  | `header` | the start's day | `day_from=header`; estimated |
  | `model-header` | the start's day | `day_from=model-header`; estimated |
  | `header-machine-zone` | the start's day, in the machine's zone when `display_timezone` is unset | `day_from=header-machine-zone`; estimated |
  | `model-header-machine-zone` | the same | `day_from=model-header-machine-zone`; estimated |
  | `file-modified` | none: a modification time names no day (`11e`) | no `day`; every due is `unresolved` |
  | any other value | none | no `day`; every due is `unresolved` |

- **An estimated due is never called late on its own.** `Due.estimated` is true when the line has `day_from`. Story 15d shows such a date as estimated. Story 16 must not record `BROKEN` or send any inquiry for a commitment whose only date is estimated. The PM's `due_set` date is never estimated.
- **A record that cannot be read** gives no day. The line omits `title`, `start` and `day`, and the due is `unresolved`. This is not a failure: the line is still written.

### The due date (Andrei, 2026-10-24)

- **The values on every line:**
  - `due_said`: the words as said or typed (`7b`'s `due`, `11b`'s `when`, an edit's `--due`), absent when nothing was said. Words that are empty or only spaces count as nothing said;
  - `due`: `<YYYY-MM-DD>` when resolved, `unresolved` when words were said but name no one date or the day is unknown, `none` when nothing was said.

  `unresolved` is a value, not a missing field, so a reader can tell "said, and pm-ai could not work it out" from "nothing said".
- **No date, never overdue.** `Due.date` is `None` for `unresolved` and `none`, and story 16 must never treat a commitment without a date as late. The PM can set the date (15c's `due_set`). pm-ai never asks for it.
- **The resolver: `pm_ai/core/due_dates.py`, `resolve(said, day) -> Due`.** It is pure and deterministic: no model, no clock, no I/O. It works on `aliases.normalise()` of the text, then:
  - punctuation `,;:!?()` is folded to spaces, and so is `.` except inside a numeric date token;
  - the leading words `by on until till before due` and `до к ко в во на по` are dropped, then a leading `the`;
  - `this` and `этой` are dropped before a unit (`end of this week`, `до конца этой недели`).

  It resolves only the forms below. Any other text, or a form with words besides those listed, is `unresolved`.

  | Form | English | Russian | Resolves to |
  | --- | --- | --- | --- |
  | a weekday | `Thursday`, `thu`, `this Friday` | `четверг`, `до четверга`, `к пятнице`, `в субботу`, `в эту пятницу` (the cases `7e` lists) | its next occurrence **after** the meeting's day; the meeting's own weekday is `unresolved` (today, or a week later) |
  | today | `today`, `end of day`, `EOD` | `сегодня`, `до конца дня` | the meeting's day |
  | tomorrow | `tomorrow` | `завтра` | the day after |
  | the day after tomorrow | `day after tomorrow` | `послезавтра` | two days after |
  | in N days | `in N day`, `in N days`, `within N days` | `через N день`, `через N дня`, `через N дней`, `в течение N дней` | the day plus N |
  | in N weeks | `in a week`, `in N week(s)`, `within a week` | `через неделю`, `через N недели`, `через N недель`, `в течение недели` | the day plus 7 × N |
  | end of the week | `end of week`, `end of the week`, `end of this week`, `EOW` | `до конца недели`, `к концу недели`, `до конца этой недели` | the Friday of the meeting's week (Monday to Sunday). A meeting on a Saturday or Sunday has no such date: `unresolved`. A meeting on that Friday follows the same-weekday rule: `unresolved` |
  | end of the month | `end of month`, `end of the month`, `end of this month` | `до конца месяца`, `к концу месяца`, `до конца этого месяца` | the last day of the meeting's month; a meeting on that day gives that day |
  | day and month | `3 Jan`, `January 3`, `3rd Jan`, `Jan 3rd` | `3 января`, `3-го января` | the next such date **on or after** the day; `29 Feb` only in a leap year |
  | day, month and year | `3 Jan 2027`, `January 3 2027` | `3 января 2027`, `3 января 2027 года`, `3 января 2027 г.`, `3 января 2027г.` | that date |
  | numeric | `2026-10-30` (ISO); `30.10.2027`, `30.10.2027г.`, `30.10`, `3.11.`, `03.11` | the same | that date; without a year, the next one on or after the day |

  - **N** is ASCII digits (at most three), or the words `a`, `an`, `one`, `two`, `three` and `один`, `одну`, `одна`, `два`, `две`, `три`. `через день` stays `unresolved`, because it also means "every other day". N × days of more than 366 is `unresolved`, and so is N = 0. Nothing can overflow.
  - **The numeric token** is `D.M`, `D.M.`, `D.M.YYYY` or `D.M.YYYY` followed by `г`, `г.` or `года`, with one or two digits for the day and the month and four for the year; or ISO `YYYY-MM-DD`.
  - **A dotted number is not a date when it could be a time.** That is when a time word is beside it (`am`, `pm`, `at`, `утра`, `дня`, `вечера`, `ночи`, `ч`, `часов`, `h`), or when it has no year, a one-digit first part and a two-digit second part from `00` to `59` (`к 9.05`, `9.30`). Such a token is `unresolved`. `09.05` and `15.11` are dates.
- **What it refuses on purpose:**
  - `next Thursday` and `в следующий четверг`: people mean either the coming one or the one after;
  - `next week`, `next sprint`, `soon`, `ASAP`, a quarter, a release;
  - a slashed date (`10/11`), since English and Russian put the day and month in different orders;
  - a two-digit year;
  - a time of day with other words (`Thursday morning`, `Thursday 3pm`);
  - an impossible date (`30 February`, `29 Feb` in 2027);
  - a date before the meeting's day;
  - a date without a year that falls more than 300 days after the meeting's day (`3 Jan` said on 4 Jan more likely names the past). A year that was said is taken as said.

  Each of these is `unresolved`. A wrong date makes a promise look broken, which is worse than no date.
- **Sentences name the year only when it differs from the meeting day's:** `due Thu 22 Oct`, but `due Sun 3 Jan 2027`.

### One resolver, so Team Activity and the ledger agree

- **`member_updates.resolve_due` (`member_updates.py:520`) becomes `due_dates.resolve(...).date`.** The tables `_WEEKDAYS`, `_MONTHS` and `_LEADING` (`:469`, `:489`, `:505`) move with it.
- **Team Activity applies the same day rule.** `_row` (`:1027`) works out its day from the line's `start` (`:1035`). `issue_notes_for` (`:1100`) receives, from its caller (`pipelines.py:3074`), each meeting's `start_estimated_from` as its record gives it.
  - A meeting whose start came from `file-modified`, or from an unknown source, shows no date beside the words.
  - An estimated day shows the date with "(meeting day estimated)".
- **What Team Activity shows differently.** Every case `7e` resolves today still resolves the same way, except one:
  - **newly resolved:** today and end of day; tomorrow; the day after tomorrow; in N days or weeks; end of the week; end of the month; `this <weekday>` and `в эту пятницу`; `3-го января`; a day, month and year (`3 Jan 2027`, which today gives none); ISO and dotted numeric dates;
  - **newly unresolved:** a date without a year more than 300 days ahead, which today gives next year's date; and every due of a meeting whose start came from `file-modified`.

### The line format (`pm_ai/core/commitment_log.py`)

- **One line per record**, through the ledgers' one encoding (`render_value` and `scan_fields`, `domain/event_entries.py:367`, `:456`). Each line is newline-terminated and within `MAX_ENTRY_LENGTH`.
- **Every line starts** `- entry=<id> commitment=<root id> kind=<kind>` and ends `occurred=<instant> … at=<instant>`.
  - `entry` is `cml_` plus the first 12 hex characters of SHA-256 over the line's identity (below).
  - `occurred` is when the change was made: the approval or the edit as committed in `operational.db`, or the PM's mark. It is the fold's clock.
  - `at` is when the line was written. A line written by the catch-up differs from the approval's own only in a later `at` and in `written`.
  - Both are ISO 8601 UTC.
- **Limits per field,** so a line always fits:

  | Field | Limit |
  | --- | --- |
  | `what` | 200 characters (`WHAT_LIMIT`) |
  | `owner`, `speaker` | 80 (`OWNER_LIMIT`) |
  | `due_said` | 40 (`DUE_LIMIT`) |
  | `note` | 200 |
  | `title` | 200, cut at a word and marked `title_cut=yes` |
  | `meeting`, `proposal`, `replaces`, `commitment` | the ids pm-ai mints |

  A stored value over its limit, which only a hand edit of the store can make, is not cut: the line is not written, with reason `too_long` (below).
- **The kinds 15a writes:**
  - `- entry=… commitment=<root> kind=pending proposal=<proposal id> type=action_item|implicit_commitment meeting=<id> [title=<title> [title_cut=yes] start=<instant>] [day=<YYYY-MM-DD> [day_from=<source>]] what=<text> [owner=<name>] [speaker=<name>] [due_said=<words>] due=<YYYY-MM-DD>|unresolved|none occurred=<instant> via=<where approved> written=approval|catch_up at=<instant>`

    Its identity is `(commitment, "pending")`.
  - `- entry=… commitment=<root> kind=revised proposal=<replacement id> replaces=<proposal of the newest value line> what=<text> [owner=<name>] [due_said=<words> due=<YYYY-MM-DD>|unresolved|none] occurred=<instant> via=<where edited> written=approval|catch_up at=<instant>`

    Its identity is `(commitment, "revised", proposal)`. It holds the whole set of values after the edit. It carries `due_said` and `due` only when the due as said changed. Otherwise the due already in force stands, including one the PM set.
  - `via` is the proposal's `changed_via`: `terminal` or `prompt` (story 5 adds `telegram`).
- **The kinds 15a defines but does not write.** 15c writes them. 15a's reader folds them, and 15a's tests write them by hand.
  - `- entry=… commitment=<root> kind=marked state=fulfilled|altered|broken|withdrawn by=pm [handle=<person handle>] [note=<text>] occurred=<instant> via=<surface> at=<instant>`, with identity `(commitment, "marked", state, occurred)`.
  - `- entry=… commitment=<root> kind=due_set due=<YYYY-MM-DD>|none by=pm [handle=…] occurred=… via=… at=…`, with identity `(commitment, "due_set", due, occurred)`.
  - `- entry=… commitment=<root> kind=owner_set [owner=<name>] by=pm [handle=…] occurred=… via=… at=…` (no `owner` clears it), with identity `(commitment, "owner_set", owner, occurred)`.
  - `by=pm` says the PM made the change himself at a pm-ai surface. pm-ai has one user, so this is never a guess.
  - `handle` is his person handle when `pm_identity()` (`handles.py:566`) gives one, the lowest in sort order when it gives several, and absent when it gives none ("under his handle", Andrei).
  - These lines are never produced from telemetry, and they are counted apart from it. SM-9's "manual override" is the count of `marked` lines with `fulfilled`, `altered` or `broken`. `withdrawn` is counted on its own: it is excluded from SM-9's broken-promise rate and from every rate story 16 reports.
- **Reserved for story 16:** `kind=verdict state=fulfilled|broken|unknown|error …`, with its evidence references. 15a's reader takes `state=` and keeps the rest unread, so 16 adds fields without changing 15a's parser.
- **States on lines** are always `CommitmentState` values, in lower case: `pending`, `fulfilled`, `altered`, `broken`, `withdrawn`, `unknown`, `error`.

### Reading the ledger

- **The parse follows `pm_ai/core/ledger.py`'s rule in one part, and differs in another.**
  - **The same:** a final line without its newline is a write in progress, and is dropped silently.
  - **Different:** a complete line that will not parse is skipped and counted, not refused for the whole file. The event log is pm-ai's own record. `commitments_log.md` is meant for the PM to read and may be edited by hand, and one bad line must not stop every later approval from being recorded.
- **What is counted:**
  - **as malformed:** a line that is not valid UTF-8 (each line is decoded on its own), does not parse, has an unknown `kind`, or has a `state` that is not a value above;
  - **as an orphan:** any line other than `pending` for a commitment that has no `pending` line;
  - **as a duplicate:** a later line whose `entry` and content equal an earlier one's;
  - **as a conflict:** a later line whose `entry` equals an earlier one's but whose content differs. The first line in file order wins.

  Ignored lines never fail the read, and are logged without their content.
- **Where the counts show:** in `CommitmentLedger` (`malformed`, `orphans`, `duplicates`, `conflicts`); in the catch-up's log line for each scope; and in 15c's `commitment list`.
  - A `pending` line is not written for a root that a malformed line names (its text holds `commitment=<root>`), since that line may be the root's own. The reason is `ledger_malformed`, and the line is written once the PM fixes or removes the bad line.
- **The fold, per commitment:**
  - **values** (`proposal`, `what`, `owner`, `speaker`) come from the newest `pending` or `revised` line, ordered by `(replacement number of proposal, occurred, entry)`. The root counts as 0, and `<root>_e<n>` as n;
  - **the owner** is the newest by `(occurred, entry)` of that value line and any `owner_set` line;
  - **the due** comes from the newest by `(occurred, entry)` of the `pending` line, `revised` lines that carry a due, and `due_set` lines;
  - **the state** comes from the state lines (`marked`, `verdict`), taken in `(occurred, entry)` order:
    - a `marked` line applies only when `can_mark` allows it from the state and source in force, and otherwise it is counted as refused;
    - a `verdict` line applies only while no `marked` line has applied.

    So the state is the newest `marked` line that applied, else the newest `verdict`, mapped to its `CommitmentState`, else `PENDING`. A file-order change never changes the result (AD-35).
- **The PM's marks, and the states they are allowed from** (Andrei, 2026-10-24; `MARKABLE` and `can_mark` in `domain/commitments.py`):

  | State in force (source) | `fulfilled` | `altered` | `broken` | `withdrawn` |
  | --- | --- | --- | --- | --- |
  | `PENDING` | yes | yes | yes | yes |
  | `ALTERED` (PM) | yes | yes, altered again | yes | yes |
  | `UNKNOWN`, `ERROR` (telemetry) | yes | yes | yes | yes |
  | `FULFILLED` (telemetry) | yes: his confirming mark | yes | yes, overriding it | yes |
  | `FULFILLED` (PM) | already: nothing written | refused: a promise he said was kept is not altered | yes | refused, for the same reason |
  | `BROKEN` (telemetry) | yes, overriding it | yes, renegotiated after it was missed | yes: his confirming mark | yes |
  | `BROKEN` (PM) | yes | yes | already: nothing written | yes |
  | `WITHDRAWN` (PM) | yes | yes | yes | already: nothing written |

  - The PM's mark outranks telemetry, and a later `verdict` never changes it.
  - `ALTERED` is not terminal: the commitment is still tracked, under the due in force.
  - `FULFILLED`, `BROKEN` and `WITHDRAWN` are terminal for telemetry (AD-14). Only the PM's mark moves a commitment out of them.

### The accessor and the store

- **`CommitmentLog(storage)`** (`pm_ai/core/commitment_log.py`) is the one accessor for `commitments_log.md` (`derivation-services.md` rule 3). It is a vocabulary over `StoragePort`, like `MeetingRecords` and `EventLog` (`core/event_log.py:54`). It holds no path and opens no file. It offers:
  - `read(scope) -> CommitmentLedger`: the folded commitments by id, the counts, the roots named by malformed lines, and the byte size read;
  - `append(lines: Sequence[CommitmentLine], scope, after)`.

  Every writer uses it: the approval and the catch-up in 15a, 15c's marks, and 16's verdicts. 15b's indexer and `10a`'s job read through it.
- **The store** (`pm_ai/storage/service.py`) gains `read_commitments(scope) -> bytes` and `append_commitments(lines, scope, after) -> int`, modelled on `read_member_updates` and `append_member_updates` (`:4341`, `:4355`):
  - one claimed step (`_claimed_unless_held`), one compare-and-append by size (`_compare_and_append`, `:4404`), one write;
  - a ledger that grew since it was read is refused (`LedgerChanged`);
  - a project ledger is written only once git confirms it is excluded (`_assert_git_excludes`, `:3337`, AD-43), as `member_updates.md` is. The personal one is not asked, because it is not gitignored;
  - a scope that is neither personal nor a project is refused (`NotACommitmentScope`, naming it), before anything is read or written (AD-4, AD-28).
- **Wiring** (`app/wiring.py`):
  - `append_commitments` joins `HANDED_CALLS` (`:4102`), with its git question for a project;
  - `read_commitments` joins `PLAIN_READS` (`:4186`);
  - neither joins `NEVER_FROM_A_JOB` (`:4223`), because 15b's indexer and 16's job will call them;
  - the port (`ports/__init__.py`) gains both, beside `:807`.
- **Written once, by the entry id.** Each writer reads and folds the ledger, and leaves out every line whose `entry` is already there. It appends the rest with `after` set to the size it read. On `LedgerChanged` it reads again and decides again, at most three times, and then reports `busy`. Two approvals, two processes, or an approval racing the catch-up therefore write each line once.

### The approval path (`pm_ai/app/commitments.py`, `pm_ai/app/proposals.py`)

- **`CommitmentRecorder`** is built in the composition root, with the store, `MeetingRecords`, `display_timezone`, the machine's zone and the clock. It has two calls.
  - **`record(proposal, scope)`**, for one root. It reads the ledger and the meeting record, then:
    - when the root has no `pending` line, it writes the `pending` line from the live approved proposal (never a `revised` line on its own);
    - when the root has one, and this proposal's replacement number is higher than the newest value line's, it writes the `revised` line;
    - otherwise it writes nothing.
  - **`catch_up(scopes)`**, at daemon start only (below).
- **`ProposalDesk` calls `record`** after a committed change, never inside the proposal's claimed step:
  - from `_change` (`proposals.py:697`), after a swap to `approved`;
  - from `_already` (`:761`), when the state asked for was `approved`, for that root only;
  - from `_edit` (`:891`), when the replacement is `approved`, whether by `--approve` or because an approved item was edited.
- **Values come from the live approved proposal.**
  - For an `action_item`: `what`, `owner` and `due` from its payload.
  - For an `implicit_commitment`: the cleaned `what`, `when` and `speaker` (`for_model`, as 13a's card shows them).
  - A missing `pending` line takes `occurred` from that proposal's `changed_at`. This is the earliest approval still on record for the root: once a root is superseded by an edit, its own `changed_at` becomes the edit's, so an earlier approval instant is gone.
- **The bound.** `approve` and `edit` keep `PROPOSAL_WRITE_BOUND_SECONDS = 2.0` at the prompt (AD-21). The extra work is one meeting-record read, one ledger read, the git check for a project, and one append. A timed test covers a project-scope approval over a 5,000-line ledger, with the git check and one `LedgerChanged` retry, within the bound.

### What an approval says

- **Text:**
  - written: `<id> approved: you agreed to it, and it is recorded as a commitment, due Thu 22 Oct ("by Thursday").`;
  - an estimated day adds `(meeting day estimated)` after the date;
  - `unresolved` reads `with no due date worked out from "next sprint"`, and `none` reads `with no due date said`;
  - a repeated approve that wrote the line: `<id>: already approved, <when>, from <where>; now recorded as a commitment, due …`;
  - not written: `<id> approved; it could not be recorded as a commitment yet (<reason text>). Run the same command again, or pm-ai records it when the daemon next starts.` After an `edit` or `review` whose replacement was approved, the command named is `pm-ai proposal approve <replacement id> --scope <scope>`. The approval stands, and the exit code is 0;
  - a reason that will not change by trying again (`too_long`, `scope_refused`) ends with `pm-ai cannot record it.` instead.
- **JSON:** the approve and edit objects gain `"commitment"` (`format` stays 1, since it is an added field):

  ```
  "commitment": null | {
    "commitment_id": string,
    "scope": string,
    "state": string,                 // a CommitmentState value; "pending" for a new line
    "recorded": boolean,             // a line for this root is in the ledger now
    "repaired": boolean,             // this command wrote a line an earlier approval had left unwritten
    "reason": null | {"code": string, "text": string},
    "day": string | null,            // YYYY-MM-DD
    "day_from": string | null,       // an ESTIMATE_SOURCES value
    "due": {"said": string | null, "value": string, "date": string | null, "estimated": boolean}
  }
  ```

  - It is `null` for a type that records no commitment.
  - `due.value` is a date, `unresolved` or `none`.
  - `reason` is `null` when `recorded` is true.
  - **The reason codes:**
    - `busy`: the claim was held past its wait, or the ledger kept changing;
    - `git_not_excluded`: AD-43's refusal for a project ledger;
    - `disk`: the write failed;
    - `ledger_unreadable`: the ledger could not be read;
    - `ledger_malformed`: a malformed line names this root;
    - `too_long`: a stored value is over its field limit;
    - `scope_refused`: `NotACommitmentScope`.

    A meeting record that cannot be read is not a reason: the line is written without title, start and day, and `day` is `null`.

### An edit after approval (Andrei, 2026-10-24)

- **Editing an approved item** supersedes it into an approved `<root>_e<n>` (13a), and `record` appends one `revised` line.
  - `replaces` names the proposal of the newest value line.
  - The state is unchanged, so `PENDING` stays `PENDING`. "Altered" comes only from the PM's own mark in 15c.
  - An edit equal to the current values writes nothing (13a's `unchanged`), so it writes no line.
- **Editing a waiting item** writes no line. **Editing with `--approve`** a waiting or an expired item writes the `pending` line, from the replacement's values.

### No rejection after approval (Andrei, 2026-10-24)

- **`approved → rejected` is refused for a type that records a commitment.**
  - `allowed()` (`domain/proposals.py:108`) allows it only for a type with no executor **and** no commitment.
  - The store's conditional update refuses it as `IllegalTransition`, from every surface, with the row unchanged. This needs no look at the ledger.
  - `_default_rule` (`:272`) still allows the edge, but it applies only when no registered rule is known. The store always passes the registered rules (13a's `register_proposal_rules`).
- **The refusal** (exit 3), from `ProposalDesk` before any write: `<id> was approved on <date> and is a commitment now, so it is not rejected; nothing was changed. To correct what was agreed, edit it: pm-ai proposal edit <id> --scope <scope> --what "…". Marking a commitment withdrawn, altered or broken is not available yet.`
  - In JSON it is 13a's refusal object, with `state: "approved"` and the current card, whose `edit` action carries its command. It names no `pm-ai commitment` command until 15c exists.
- **The card** (`proposal_cards.py:268-274`): an approved `action_item` or `implicit_commitment` offers `edit` and `show`. `reject` is unavailable, with the reason "approved, so it is a commitment now; edit it to correct what was agreed".
- **Completion** stops offering approved items of these types for `reject` (`proposals.py:625`).
- **Unchanged:** a waiting item is rejected as before, and so is an approved item of a type that records no commitment, should one exist.

### The catch-up (items approved before 15a, and approvals whose line was not written)

- **When it runs:** once at each daemon start. It is a one-shot task in `_lifespan` (`daemon.py:1033`), created after the sweep (`:1055`) from `app.state.pm_ai_commitments`, which is set beside `pm_ai_sweep` (`:646`).
  - `_lifespan` returns early when `pm_ai_requests` is unset, so there is no catch-up then.
  - Its reads run on the I/O pool and its appends on the writer thread (AD-19), off the request path.
  - It never stops the daemon starting.
- **What it does:**
  1. For each scope in personal and every enrolled project, it reads the ledger first.
  2. Then, once, it reads `proposal_index_at(store, states=["approved"])` and places each row of a commitment type in its scope with `scope_of`. Rows staged before 13a have no stored scope, and are placed this way.
  3. For each live approved proposal whose root has no `pending` line, it writes a `pending` line (`written=catch_up`).
  4. For each whose replacement number is higher than the newest value line's, it writes a `revised` line.
  5. Each scope's lines go in one append. Inside that claimed step it checks again that every proposal is still approved at the version read, and leaves out any that is not.

  The first start after 15a writes the items approved before it. Later starts write nothing, because of the entry ids, so nothing marks the catch-up as done.
- **What it counts, and logs as counts only, one line per scope:**
  - lines written;
  - approved rows with no `changed_at` (no approval instant known, so no line);
  - unreadable bodies;
  - rows whose scope cannot be told;
  - rows that failed `too_long` or `scope_refused`. These will not change, so they are counted and never retried;
  - approved commitment-type rows in scopes it could not visit (a project no longer enrolled, or a ledger it could not read), named by scope;
  - **`pending` lines whose proposal is not approved in `operational.db`** (the store was lost or rolled back). They are flagged by root, and nothing is written for them.
- **No real data.** It is tested with fixtures shaped like `7b`'s rows: personal `action_item`s approved through `proposal approve`, under `11d`'s names (`Example Weekly Sync-up`, `Alex Morgan`, `Sam Rivera`). Nothing reads the PM's own `~/.pm-ai` or `~/.manager-ai`.

### The architecture tests and the skip count

- **The new modules avoid the name `pm_ai.core.commitments`.** That name belongs to story 16's verifier. `test_ad35_sweeper_will_not_declare_broken_without_coverage` and `test_ad36_self_authored_events_are_excluded_from_evidence` (`test_domain_invariants.py:736`, `:750`) call its `evaluate`, `sample_overdue`, `sample_pending` and `sample_event`.
  - If 15a created the module without them, both tests would turn from skips into failures.
  - A stand-in `evaluate` would make them pass while checking nothing.

  15a creates `pm_ai/domain/commitments.py`, `pm_ai/core/commitment_log.py`, `pm_ai/core/due_dates.py` and `pm_ai/app/commitments.py`. The spine's module map is amended.
- **`test_ad33_ledger_entries_are_self_contained` (`:628`): 15a writes its fixture, so the test turns from a skip into a pass.**
  - `_sample_extraction()` (`:649`) is replaced by `_sample_approved_promise()`: an `11b`-shaped approved promise with a stored speaker, in a personal scope, with a meeting record under `11d`'s names.
  - The test calls `commitment_log.from_approved(proposal, record=…, zone=…)`.
  - It checks that `commitment_id`, `what`, `speaker`, `cites`, `meeting_id`, `title`, `start`, `day` and `due` are set, and that `render_line(...)` needs no transcript.
  - Its docstring says the ledger now starts at approval, not at extraction.
- **`test_ad28_project_ledger_rejects_personal_commitments` (`:131`) states the old rule.** It assumes a project-only ledger, which the AD-28 amendment ends, so it is revised with the amendment, not deleted silently. It becomes two tests, which both pass in 15a:
  - **`test_ad28_commitment_ledgers_refuse_people_and_keep_their_own_scope`:** `append_commitments` refuses a `people` scope with `NotACommitmentScope`, and nothing is written. A personal meeting's line lands only in the personal ledger, and a project meeting's only in the project's.
  - **`test_ad28_a_coaching_commitment_never_reaches_a_commitment_ledger`:** `CommitmentLog.append`, given a stand-in coaching undertaking (a test dataclass, since `CoachingCommitment` is not built yet) instead of a `CommitmentLine`, raises `NotACommitmentLine` for the personal and the project scope, and the store is never called.

  Both docstrings name the 2026-10-24 amendment.
- **`EXPECTED_SKIPS` goes from 8 to 6** (`tests/conftest.py:189`), with a delta line above it: "8 → 6, story 15a: `test_ad33_ledger_entries_are_self_contained` runs on a fixture; `test_ad28_…`, split by the AD-28 amendment, runs as two tests. `test_ad35_…` and `test_ad36_…` still skip on `pm_ai.core.commitments`, story 16's."
- **`test_ad14_proposal_and_commitment_lifecycles_stay_distinct` (`:176`) passes unchanged.** `WITHDRAWN` is not a proposal state, and `STAGED_APPROVAL` is not a commitment state.

### Where it collides

- **13b edits the same approve path** (`app/proposals.py`, `_change` after the swap, and `_refuse_approval`).
  - 15a records a commitment for `commitment` types, and 13b runs executors for `executor` types. The types are disjoint, so the merge is mechanical.
  - Whichever lands second rebases. Inside `_change` the order is the swap, then 15a's `record`, then 13b's enqueue.
  - 13b's types record no commitment unless they say so, which leaves the `approved → rejected` edge as 13a left it for them.

### No real data

Tests use `11d`'s fixture names and `example.com` handles. No real meeting, name, item text or date from the PM's store appears, and his own store is never read.

**Never:**

- **The ledger:**
  - no line while an item waits, and no `STAGED_APPROVAL` line in either ledger;
  - no line before the approval is committed;
  - no line written twice, rewritten or removed, and no whole-file write of either ledger;
  - no lone `revised` line without its `pending` line.
- **Scopes:**
  - no commitment in a `people` scope or in the application scope;
  - no `CoachingCommitment` in either ledger;
  - no personal meeting's commitment in a project ledger, and no project meeting's in the personal one.
- **People:**
  - no handle written for an owner or a speaker;
  - no name guessed or fuzzy-matched to a person;
  - no owner taken from a promise's speaker.
- **Due dates:**
  - no date guessed, no model call, and no clock read by the resolver;
  - no date before the meeting's day;
  - no overdue without a date, and no "late" on an estimated day alone;
  - no due resolved against today's zone after the `pending` line stored the day.
- **Proposals:**
  - no rejection of an approved item whose type records a commitment;
  - no refusal or JSON pointing to a command that does not exist yet.
- **Scope of this slice:**
  - no module named `pm_ai.core.commitments`, and no stand-in `evaluate`;
  - no index (15b), no `pm-ai commitment` command (15c), no change to the dashboard or the brief beyond what 13a shows (15d), and no telemetry verdict (16);
  - no change to 13a's waiting, expiry, edit or re-summary rules beyond the reject edge, the card's reject action, completion's reject candidates, and the promise's new `speaker` field and its card line.
- **No read of the PM's real store** in a test or a manual check.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Approve an action item | personal meeting Mon 19 Oct, `due` "by Thursday", `proposal approve <id>` | one `pending` line in `~/.manager-ai/memory/commitments_log.md`: `commitment=<id>`, `day=2026-10-19`, `due_said="by Thursday"`, `due=2026-10-22`, `written=approval`; "…recorded as a commitment, due Thu 22 Oct (\"by Thursday\")" | exit 0 |
| Approve in a project | the same in `project:alpha` | the line in `<repo>/.project-ai/memory/commitments_log.md`; none in the personal ledger | exit 0 |
| JSON | `approve <id> --json` | `commitment: {commitment_id, scope: "personal", state: "pending", recorded: true, repaired: false, reason: null, day: "2026-10-19", day_from: null, due: {said: "by Thursday", value: "2026-10-22", date: "2026-10-22", estimated: false}}` | exit 0 |
| Waiting item | staged, not approved | nothing in either ledger | N/A |
| Approve twice | approve the same id again, line present | `already`; nothing appended; `repaired: false` | exit 0 |
| Two processes | the terminal and the prompt approve one id at once | one swap wins (13a); exactly one `pending` line | both exit 0 |
| Ledger changed in between | another writer appends between the read and the append | `LedgerChanged`; read again; the line appended once | exit 0 |
| Append fails | the claim is busy past its wait | approved; `recorded: false`, `reason.code: "busy"`; the sentence names running it again | exit 0 |
| Repair by approving again | the case above, then `approve <id>` | "already approved …; now recorded as a commitment, due …"; `recorded: true`, `repaired: true` | exit 0 |
| Project git refuses | the project no longer excludes `memory/` | approved; `reason.code: "git_not_excluded"` | exit 0 |
| Edit-approve fails to record | `edit --approve` and the append fails | approved; the sentence names `pm-ai proposal approve <root>_e1 --scope …` | exit 0 |
| Field over its limit | a stored `what` of 300 characters (hand edit of the store) | approved; `reason.code: "too_long"`, "pm-ai cannot record it."; the catch-up counts it and never retries | exit 0 |
| Long title | a meeting title of 500 characters | `title` cut at a word to at most 200 characters, `title_cut=yes` | exit 0 |
| Spoken promise | promise "send the numbers" by "Friday", said by `Sam Rivera` | `pending` line with `speaker="Sam Rivera"`, no `owner`, due the Friday after the meeting; card "said by Sam Rivera: send the numbers, by Friday" | exit 0 |
| Old promise row | a promise staged before 15a, then approved | line without `speaker` or `owner` | exit 0 |
| Re-ingestion of an old promise | the same transcript ingested again after 15a | no version bump, nothing counted as dropped | exit 0 |
| Staged command | `explicit_command` | approval unavailable (13a); no line | exit 3 |
| Edit with approve | waiting item, `edit --due "Friday" --approve` | `<root>_e1` approved; one `pending` line, `commitment=<root>`, `proposal=<root>_e1` | exit 0 |
| Edit an approved item | approved, then `edit --what "send the TTL numbers"` | `<root>_e1` approved; one `revised` line, `replaces=<root>`, no `due` fields; state `PENDING` | exit 0 |
| Edit the due after approval | approved, then `edit --due "Friday"` | `revised` line with `due_said="Friday"` and the date resolved against the stored `day` | exit 0 |
| Edit twice after approval | `_e1` then `_e2` | two `revised` lines; the second `replaces=<root>_e1`; values from `_e2` | exit 0 |
| Edit, same values | approved, edit to the values it holds | `unchanged`; no line | exit 0 |
| Edit a waiting item | `edit --due "Monday"`, no `--approve` | replacement waiting; no line | exit 0 |
| Expired item, edit-approve | expired, `edit --approve` | `pending` line for the root, from the replacement | exit 0 |
| Reject after approval | approved action item, `proposal reject <id>` | refused: "…is a commitment now, so it is not rejected… edit it: pm-ai proposal edit …"; row and ledger unchanged | exit 3 |
| Reject in JSON | the same with `--json` | refusal object, `state: "approved"`; card's `edit` command present; no `pm-ai commitment` words | exit 3 |
| Reject at the store | `change_proposal(id, REJECTED)` on an approved action item | `IllegalTransition`; row unchanged | refused |
| Card of an approved item | `proposal show <id> --json` | `actions.reject.available: false` with the reason; `edit` available | exit 0 |
| Reject a waiting item | staged, `reject` | rejected as in 13a; no line | exit 0 |
| Due unresolved | `due` "next sprint" | `due_said="next sprint"`, `due=unresolved`; "with no due date worked out from \"next sprint\"" | exit 0 |
| No due | `due` absent, or only spaces | no `due_said`, `due=none`; "with no due date said" | exit 0 |
| Meeting's own weekday | meeting Thu 22 Oct, "by Thursday" | `due=unresolved` | N/A |
| English forms | meeting Mon 19 Oct: `today`, `the day after tomorrow`, `in 3 days`, `in two weeks`, `end of this week`, `end of the month`, `3 Jan 2027`, `2026-10-30` | 19 Oct, 21 Oct, 22 Oct, 2 Nov, 23 Oct, 31 Oct, 3 Jan 2027, 30 Oct | N/A |
| Russian forms | meeting Mon 19 Oct: `до четверга`, `завтра`, `через неделю`, `через 2 недели`, `до конца этой недели`, `к концу месяца`, `3-го ноября`, `30.10`, `30.10.2026г.` | 22 Oct, 20 Oct, 26 Oct, 2 Nov, 23 Oct, 31 Oct, 3 Nov, 30 Oct, 30 Oct | N/A |
| End of week on a weekend | `до конца недели`, meeting Sat 24 Oct | `unresolved` | N/A |
| End of week on its Friday | `end of week`, meeting Fri 23 Oct | `unresolved` | N/A |
| Refused forms | `next Thursday`, `в следующий четверг`, `10/11`, `Thursday morning`, `30 February`, `1 Jan 2026` (before the meeting), `через день`, `in 400 days`, `к 9.05`, `9.30 утра` | each `unresolved` | N/A |
| Far ahead | `3 Jan` said on Mon 4 Jan 2027 | `unresolved` (next one more than 300 days ahead) | N/A |
| Leap day | `29 Feb` said in Oct 2027 | 29 Feb 2028 | N/A |
| Year in sentences | due 3 Jan 2027, meeting 2026 | "due Sun 3 Jan 2027" | N/A |
| Estimated day | record `start_estimated_from=header` | `day_from=header`; `due.estimated: true`; "(meeting day estimated)" | exit 0 |
| Every estimate source | each of `docx-created`, `model-header`, `header-machine-zone`, `model-header-machine-zone` | `day_from` that value; estimated | N/A |
| Machine-zone header | `header-machine-zone`, `display_timezone` unset | the day in the machine's zone | N/A |
| Day from a modification time | `start_estimated_from=file-modified` | no `day`; every `due=unresolved` | exit 0 |
| Unknown source | `start_estimated_from=something-new` | no `day`; every `due=unresolved` | exit 0 |
| Record unreadable | the meeting record does not parse at approval | line without `title`, `start`, `day`; `due=unresolved`; `recorded: true` | exit 0 |
| Display zone | start 22:30 UTC Sunday, `display_timezone` Europe/Istanbul (Monday 01:30) | `day` is the Monday | N/A |
| Day stored | the zone setting changes after the `pending` line, then an edit changes the due | the `revised` due is resolved against the stored `day` | N/A |
| Team Activity due | a `7e` row due "tomorrow" | now shows the date beside the words | N/A |
| Team Activity, modified time | a `7e` row from a meeting whose start came from `file-modified` | no date shown | N/A |
| Catch-up of old approvals | three personal action items approved before 15a, empty ledger; daemon starts | three `pending` lines, `written=catch_up`, `occurred` each proposal's `changed_at` | N/A |
| Catch-up again | the daemon restarts | nothing appended | N/A |
| Catch-up runs from start | the daemon built and started through `_lifespan` | the task ran once; lines written | N/A |
| Catch-up, old row without scope | an approved row staged before 13a, NULL scope column | placed by `scope_of`; line written in its scope | N/A |
| Catch-up after a lost edit line | approved root edited, the `revised` line not written | `revised` line from `<root>_e1` | N/A |
| Catch-up, superseded meanwhile | the proposal changes after the catch-up's read | left out inside the claimed step | N/A |
| Catch-up, no change time | an approved row with no `changed_at` | no line; counted | N/A |
| Catch-up, unvisited scope | approved rows in a project no longer enrolled | counted by scope; no line | N/A |
| Store lost | a `pending` line whose proposal is not approved in `operational.db` | flagged by root in the log; nothing written | N/A |
| People scope | `append_commitments(…, scope=people:example)` | `NotACommitmentScope` naming it; nothing read or written | refused |
| Not a commitment line | `CommitmentLog.append` given a stand-in coaching undertaking | `NotACommitmentLine`; the store never called | refused |
| Whole-file write | `write_artifact(…, "commitments_log.md")` for personal | refused as append-only | refused |
| Fold order | file order B then A, A with the earlier `occurred` | the same result as A then B (AD-35) | N/A |
| Value order | `revised` for `_e2` occurring before `revised` for `_e1` (clock step) | values from `_e2` | N/A |
| Due kept | `due_set` by the PM, then a `revised` line without due fields | the PM's date stands | N/A |
| Duplicate line | a hand-copied line, same `entry` and content | ignored; counted as a duplicate | N/A |
| Conflicting line | same `entry`, different content | the first in file order wins; counted as a conflict | N/A |
| Malformed line | a line with an unknown `kind`, a bad `state`, or invalid UTF-8 | skipped and counted; the rest read | N/A |
| Malformed line names a root | a malformed line holding `commitment=<root>`, root without a readable `pending` line | no `pending` written; `reason.code: "ledger_malformed"` | exit 0 |
| Torn last line | the file ends mid-line | that fragment is not a record; the next append starts on a new line | N/A |
| Orphan | a `revised` or `marked` line with no `pending` | ignored; counted | N/A |
| Hand-written mark | `marked state=altered by=pm` after `pending` | fold gives `ALTERED`, source `pm` | N/A |
| Mark not allowed | `marked state=altered` after a PM-marked `fulfilled` | not applied; counted as refused; state stays `FULFILLED` | N/A |
| Withdrawn | `marked state=withdrawn` after `pending` | `WITHDRAWN`; never counted as broken | N/A |
| Mark outranks a verdict | `marked state=broken`, then a later `verdict state=fulfilled` | still `BROKEN` | N/A |
| Large project ledger | 5,000 lines, project scope, one `LedgerChanged` | `approve` within 2 s at the prompt, git check included | exit 0 |
| Hostile text | a `what` holding a newline and a quote | one line, quoted and escaped; reads back equal | N/A |

</frozen-after-approval>

## Code Map

Anchors checked against `origin/main` at `b6f8f62`.

- `pm_ai/domain/lifecycle.py:28` `ProposalState`; `:49` `CommitmentState` (gains `WITHDRAWN`), `:61` `is_terminal`, `:64` `is_verdict`; `:76` the AD-14 disjointness check; `:208` `evaluate_commitment` (story 16's)
- `pm_ai/domain/meetings.py:51` `ESTIMATE_SOURCES`
- `pm_ai/domain/proposals.py:76` `TypeRule` (gains `records_commitment`); `:108` `allowed`; `:145` `root_of`; `:272` `_default_rule`; `:276` `check_change`
- `pm_ai/core/proposals.py:84` `OWNER_LIMIT`; `:98` `ApprovalKind`; `:127` `ProposalType`, `:139` `rule`; `:262` `_promise_card`; `:276` `ACTION_ITEM_TYPE`; `:289` `IMPLICIT_COMMITMENT_TYPE`. The brief's `pm_ai/domain/proposal_types.py` does not exist; the registrations are here.
- `pm_ai/app/proposals.py:625` completion's reject candidates; `:659` `approve`; `:664` `reject`; `:697` `_change`, `:758` its sentence; `:761` `_already`; `:891` `_edit`
- `pm_ai/core/proposal_cards.py:268-274` the approved card's `reject`
- `pm_ai/app/proposal_reads.py:89` `scope_of`; `:134` the zone
- `pm_ai/storage/service.py:1805` `ProposalIndexRow`; `:1909` `proposal_index_at`; `:2318` `_require_project`; `:2327` `_ledger_text`; `:3337` `_assert_git_excludes`; `:4118` `assert_writable`; `:4341` `read_member_updates`; `:4355` `append_member_updates`; `:4404` `_compare_and_append`; `:6470` `change_proposal`; `:6507` `edit_proposal`
- `pm_ai/domain/scope_model.py:532` `PERSONAL_TREE`, `:553` its `memory/`; `:762` the project's `commitments_log.md`; `:1017` `BACKUP_TARGETS`
- `pm_ai/domain/storage_tiers.py:196` `_APPEND_ONLY_KEYS`; `:241` `restricted_mode`
- `pm_ai/domain/event_entries.py:212` `MAX_ENTRY_LENGTH`; `:367` `render_value`; `:456` `scan_fields`
- `pm_ai/core/ledger.py` -- the torn-tail rule, and a complete bad line refused, which commitments do not follow (stated above)
- `pm_ai/core/member_updates.py:469` `_WEEKDAYS`, `:489` `_MONTHS`, `:505` `_LEADING`; `:520` `resolve_due`; `:628` `render_update`; `:1027` `_row`, `:1035` its day; `:1100` `issue_notes_for`
- `pm_ai/core/aliases.py:144` `AliasTable`, `:161` `handle_for`
- `pm_ai/domain/handles.py:566` `pm_identity`
- `pm_ai/core/extraction.py:104` `Promise`; `:125` `Extraction.speaker_handle`
- `pm_ai/app/pipelines.py:1237` the promise's payload; `:1268` `_dropped_over`; `:1575` `_machine_zone`; `:1598` `display_timezone`; `:3074` Team Activity's call; `:4088` the action item's payload
- `pm_ai/core/meeting_records.py:803` `MeetingRecords`, `:999` `get`
- `pm_ai/core/event_log.py:54` `EventLog`
- `pm_ai/app/daemon.py:646` the sweep set on `app.state`; `:1033` `_lifespan` (returns early without `pm_ai_requests`); `:1055` the sweep task
- `pm_ai/app/wiring.py:720` `registered_proposal_types`; `:4102` `HANDED_CALLS`; `:4186` `PLAIN_READS`; `:4223` `NEVER_FROM_A_JOB`
- `pm_ai/ports/__init__.py:807`, `:810` the member-updates calls on the port
- `tests/architecture/test_domain_invariants.py:131` AD-28; `:176` AD-14; `:628` AD-33, `:649` `_sample_extraction`; `:736` AD-35; `:750` AD-36; `:886-897` the epistemic-member checks, which `WITHDRAWN` passes as a verdict
- `tests/conftest.py:189` `EXPECTED_SKIPS = 8`
- `tests/slice/test_proposals.py:421` an approved item rejected; `:1216` the printed reject command of an approved item; `:1234` its JSON reject command
- `tests/slice/test_transcript_path.py:652-655` the promise payload, which gains `speaker`
- `tests/core/test_member_updates.py:281-303` `resolve_due`'s cases
- `tests/architecture/test_capture_guard.py:517` `_preflight_commitments`; `tests/architecture/test_paths.py:169` the personal rows; `tests/architecture/test_encryption_policy.py:52`
- `ARCHITECTURE-SPINE.md:305` AD-13; `:313` AD-14; `:446-454` AD-28; `:505` AD-33; `:554` AD-36; `:570` AD-37; `:1100` the module map
- `SPEC.md:51` CAP-6; `glossary.md:35`; `prd.md:65-67`, `:96`, `:267`, `:653`, `:766`; `success-metrics.md:17`; `scope-model.md:108-112`; `derivation-services.md:99`, `:108`, `:117`, `:125`

## Tasks & Acceptance

**Execution:**
- [ ] **The entity.** `pm_ai/domain/commitments.py`, and `WITHDRAWN` in `lifecycle.py`.
- [ ] **The resolver.** `pm_ai/core/due_dates.py`, with the tables moved from `member_updates.py`. `member_updates.resolve_due` calls it. Team Activity's day follows the same rule, with each meeting's `start_estimated_from` passed in from `pipelines.py:3074`.
- [ ] **The lines and the accessor.** `pm_ai/core/commitment_log.py`: `CommitmentLine`, `render_line`, `parse_ledger` (per-line decoding, the counts), `fold`, `from_approved`, and `CommitmentLog`.
- [ ] **The personal ledger.** The `File` in `PERSONAL_TREE`'s `memory/`.
- [ ] **The store.** `read_commitments`, `append_commitments` and `NotACommitmentScope`, plus the port and the wiring tables.
- [ ] **No rejection after approval.** `TypeRule.records_commitment` and `allowed`; `commitment=True` on two types, copied by `ProposalType.rule`; the card; the refusal; completion.
- [ ] **The recorder.** `pm_ai/app/commitments.py`, `record` and `catch_up`, built in the composition root and called from `_change`, `_already` and `_edit`. Its outcome goes in the sentences and the JSON `commitment`.
- [ ] **The catch-up at start.** `daemon.py`: `app.state.pm_ai_commitments`, and the one-shot task in `_lifespan`.
- [ ] **The promise's speaker.** `Promise.payload` gains `speaker` (raw and cleaned), filled at `11b`'s staging. The card reads "said by". Old payloads compare as equal, in `stage_unless_acted` and `_dropped_over`.
- [ ] **New tests.**
  - `tests/core/test_due_dates.py`: every form in both languages, every refused form, N bounds, numeric tokens, time-like tokens, 29 Feb, 300 days, empty words.
  - `tests/core/test_commitment_log.py`: render and parse round trips; field limits; fold orders (values by replacement number, due, state); duplicates, conflicts, orphans, malformed lines including invalid UTF-8; a torn tail; every cell of the mark table, including refused marks.
  - `tests/domain/test_commitments.py`, or beside the proposal domain tests: `allowed(APPROVED, REJECTED, rule with records_commitment)` is false, and `ProposalType.rule` copies the flag.
  - `tests/slice/test_commitments.py`: one test per matrix row; `change_proposal` raising `IllegalTransition` with the row unchanged; two processes approving one id; the timed project-scope approval; the catch-up through `_lifespan`, idempotent, including a NULL-scope row; the re-ingestion of an old promise; one Team Activity assertion for a newly resolved form.
- [ ] **Existing tests that change.**
  - `test_domain_invariants.py`: AD-33 on its fixture; AD-28 split into two tests.
  - `tests/conftest.py`: `EXPECTED_SKIPS` 8 → 6, with its delta line.
  - `test_proposals.py:421` (the refusal), `:1216` (no printed reject command for the approved item) and `:1234` (`reject` unavailable in JSON).
  - `test_transcript_path.py:652-655`: the payload gains `speaker`.
  - `test_member_updates.py:281-303`: the `"3 Jan 2027"` row now resolves, plus rows for each newly resolved form and for 300 days ahead.
  - `test_paths.py` and `test_encryption_policy.py`: the personal `commitments_log.md`.
  - `test_capture_guard.py:517`: the project ledger's guard reached through `append_commitments`.
- [ ] **The contract.** Apply the amendments in Design Notes, in this order:
  1. `prd.md` through bmad-prd;
  2. then bmad-spec re-derives `SPEC.md` and its companions (`glossary.md`, `scope-model.md`, `derivation-services.md`, `success-metrics.md`);
  3. then `ARCHITECTURE-SPINE.md` through the architecture skill.

**Acceptance Criteria:**
- Given a summarised personal fixture meeting with three waiting items, when the PM approves one, then `~/.manager-ai/memory/commitments_log.md` gains exactly one `pending` line for that root, with `day`, and nothing for the other two.
- Given that approved item, when it is approved again, when the daemon starts twice, and when two processes approve it at once, then the ledger still holds one `pending` line for it.
- Given three approved fixture items and an empty ledger, when the daemon starts through `_lifespan`, then three `pending` lines are written with `written=catch_up` and `occurred` equal to each proposal's `changed_at`; and on the next start nothing more is written.
- Given an approved action item, when `proposal reject` runs on it, then it is refused with exit 3, its JSON names no command that does not exist, and the row and the ledger are unchanged.
- Given a meeting on Monday 2026-10-19, when items due "by Thursday", "до конца недели", "next sprint" and nothing are approved, then their `due` values are `2026-10-22`, `2026-10-23`, `unresolved` and `none`.
- Given a meeting whose start was estimated from its header, when an item due "tomorrow" is approved, then the line has `day_from=header` and the JSON `due.estimated` is true.
- Given the change, when `uv run pytest` runs, then everything passes; the AD-33 test and both AD-28 tests pass; the AD-35 and AD-36 tests still skip on `pm_ai.core.commitments`; and the skip count is 6.

## Spec Change Log

- **2026-10-24, created** after a readiness check, from story 15's entry and Andrei's six decisions of 2026-10-24, given to the coordinating session:
  1. the ledger starts at approval;
  2. personal meetings get a personal ledger;
  3. the PM's own mark of done, altered or broken, counted apart from telemetry;
  4. the due kept as said and resolved to a date against the meeting's day, or unresolved and never overdue;
  5. 15b's index triggered inside the daemon, and checked at daemon start;
  6. no rejection once an item is a commitment.

  Story 15 split into 15a (this spec), 15b (index), 15c (commands) and 15d (surfaces). Two Ask First questions open.
- **2026-10-24, review (three lenses) and Andrei's decisions: frozen block edited (recorded here).**
  - **Andrei's decisions** (2026-10-24, given to the coordinating session, all as recommended or proposed):
    1. An edit after approval writes a `revised` line, and "altered" comes only from his mark. This closes Ask First 1.
    2. "End of the week" is the Friday of the meeting's week, with no date for a weekend meeting. "End of the month" is the month's last day. This closes Ask First 2, and every dependent row now has one outcome.
    3. A `withdrawn` mark, for an approval made by mistake, is counted apart from broken: excluded from SM-9's broken-promise rate and from story 16's rates. `CommitmentState` gains `WITHDRAWN`. Until 15c exists, a refused reject points to `proposal edit`, and its JSON names no command that does not exist.
    4. A spoken promise records `speaker=`, who said it, and has no `owner` unless the PM sets one (a new `owner_set` kind for 15c). The card reads "said by". AD-33 is amended to cite the speaker.
    5. An estimated meeting day, from any source but `file-modified`, still resolves the due. The line records `day_from`, and the due is marked estimated, so 15d and 16 never call a promise late on a guessed day. Every `ESTIMATE_SOURCES` value is covered, and an unknown source gives no day.
  - **Dates and the fold.**
    - `day` is stored on the `pending` line, and `revised` lines and the catch-up resolve against it. A date-only start uses its date with no zone shift. A `-machine-zone` start's day is in the machine's zone when `display_timezone` is unset.
    - A `revised` line carries the due only when the due as said changed.
    - The state is the newest `marked` line that applies (checked against the mark table), else the newest `verdict`, else `PENDING`. A refused mark is counted. States are spelled one way on lines.
    - Value lines are ordered by `(replacement number, occurred, entry)`. For a duplicate `entry`, the first in file order wins, and conflicts are counted apart from exact duplicates.
  - **Reading.**
    - A torn tail is dropped, as in `core/ledger.py`. A complete bad line is counted, not fatal, because the PM may edit this file by hand. The counts show in `CommitmentLedger`, the catch-up's log and 15c's list.
    - Each line is decoded on its own, so invalid UTF-8 is counted.
    - An unknown kind or state is malformed. Every kind other than `pending` can be an orphan.
    - No `pending` line is written for a root a malformed line names (`ledger_malformed`).
  - **The resolver.**
    - A leading `the` is dropped, and so are `this` and `этой` before a unit. The unit forms and number words are listed exactly; `через день` is unresolved; N is capped at 366 days, with no overflow.
    - Time-like dotted numbers are unresolved, and the numeric token is defined.
    - A date without a year more than 300 days ahead is unresolved. 29 Feb is handled. An end of the week on its own Friday follows the same-weekday rule.
    - Empty words mean no due. Sentences show the year only when it differs from the meeting's.
  - **Team Activity.** The "only `3 Jan 2027` changes" claim was false. Every newly resolved and newly unresolved form is now listed, Team Activity applies the same day rule as the ledger, and test rows and a slice assertion are added.
  - **The approval path and the catch-up.**
    - `record()` writes `pending` from the live proposal when the root has none, never a lone `revised`, and adds `revised` only for a newer replacement.
    - The repeated approve records that root only, with its own sentence and `repaired` in JSON.
    - The catch-up runs only at daemon start. It reads the ledger before the proposals, re-checks inside the claimed step, and reads `proposal_index_at(states=["approved"])` once, placing rows with `scope_of`. It counts unvisited scopes and permanent failures, and flags `pending` lines whose proposal is not approved in the store.
    - A missing `pending` line's `occurred` is stated plainly as the earliest approval still on record.
    - `replaces` names the newest value line's proposal.
    - A failed append after `edit` or `review` names `proposal approve <replacement>`.
    - Slice tests prove the catch-up runs from `_lifespan` and is idempotent.
  - **Limits and JSON.**
    - Each field has a limit; a long title is cut and marked; a stored value over its limit is `too_long`, permanent, and never retried. `OWNER_LIMIT` applies to a typed owner, and a name holding several people is never linked.
    - The speaker goes through `sanitize`, with the raw label kept in the proposal body.
    - The JSON `commitment` object is written out with types and reason codes.
  - **The mark table** is split by source: confirming a telemetry verdict writes the PM's own mark, and a PM-marked `FULFILLED` refuses `altered` and `withdrawn`.
  - **Proving the reject rule.** A domain test checks `allowed`, and a store test checks `change_proposal`. `ProposalType.rule` copies the flag. `_default_rule` is noted.
  - **AD-28's coaching test** now names its call (`CommitmentLog.append` accepts only `CommitmentLine`) and passes with a stand-in, so `EXPECTED_SKIPS` goes from 8 to 6.
  - **Re-ingestion.** An old promise payload without `speaker` compares as equal, so there is no version bump or false "dropped" count, and a slice test covers it.
  - **Tests named:** `test_transcript_path.py:652-655`, `test_proposals.py:1216`, and the timed project-scope approval with the git check and one retry.
  - **Amendments completed:** AD-28's Prevents and Rule lead-in, AD-33, AD-14's state list, PRD lines 65-67, 96, 267 and 766, exact sentences for `derivation-services.md:99` and `scope-model.md` §B, SM-9's withdrawn exclusion, and the order the three skills apply them in.

## Design Notes

**Why the line follows the approval, not the same step.** `operational.db` and `commitments_log.md` are two files, so no transaction spans both. Committing the approval first can leave an approved item without its line, which a second `approve` or the next daemon start repairs. Writing the line first could leave a commitment the PM never approved, in a file that cannot take it back. Entry ids worked out from the line's content make every repair write nothing twice.

**Why the day is stored.** The due is resolved when the line is written. A later edit, or the catch-up weeks later, must resolve against the same day. Otherwise a changed `display_timezone`, or a catch-up run on another machine, could move a date by one.

**Why rejection is refused by the type, not by looking at the ledger.** "Refuse once a commitment exists" and "refuse once approved" are the same rule, because the ledger starts at approval. In the domain table, the store enforces it in the same conditional update for every surface, and an approval whose line is not yet written cannot slip past it.

**Why names, and why a promise has a speaker and no owner.** A spoken or typed name says nothing a provider vouched for. Writing a handle on a guess would invent identity at write time, and a ledger line is never rewritten. Readers link names through the saved alias table, as `7e` and `11e` do. "I'll send it" says who spoke; it does not say who must do it, so pm-ai records the speaker and leaves the owner to the PM.

**Why the resolver refuses so much.** A due date decides whether a promise looks broken. The resolver names a date only when the words name exactly one, and everything else is `unresolved`. The PM can fix that with one command (15c), and an unresolved due never makes a promise look late. One resolver serves the ledger and Team Activity, so the two never show different dates for one promise.

**Why bad lines are counted and not fatal.** `core/ledger.py` refuses a whole event-log segment over one bad complete line, because the event log is pm-ai's own record and a bad line there is corruption. This ledger is also the PM's to read and edit. Refusing the whole file would stop every later approval from being recorded until he found the line. So the bad line is counted and shown, and the only write it blocks is a `pending` line for the root it might be.

**Why not `pm_ai.core.commitments`.** Two architecture tests call a verifier, `evaluate`, that is story 16's work. Creating the module in 15a without it would turn them into failures, and a stand-in would turn them into passes that check nothing.

**Accepted limits:**
- A promise staged before 15a has no recorded speaker.
- A meeting whose start came from a file's modification time gives no due dates.
- An approval whose line could not be written is recorded at the next `approve` of the same id, or the next daemon start, not sooner.
- After an edit, a commitment's earliest approval instant is the replacement's, because supersession overwrites the root's `changed_at`.

**Amendments this depends on.** They are applied with the build, in order: the PRD through bmad-prd, then bmad-spec re-derives `SPEC.md` and its companions, then the spine through the architecture skill. The sentences are exact.

*Spine (`ARCHITECTURE-SPINE.md`):*
- **AD-13** (`:305`), in the `[2026-10-22, Andrei, 13a]` paragraph: replace "`approved → rejected` for a type with no executor;" with "`approved → rejected` for a type with no executor whose approval records no commitment `[2026-10-24, Andrei, 15a]`;".
- **AD-14** (`:313`):
  - replace "`PENDING → FULFILLED | ALTERED | BROKEN | UNKNOWN` is a domain state machine" with "`PENDING → FULFILLED | ALTERED | BROKEN | UNKNOWN | ERROR | WITHDRAWN` is a domain state machine";
  - replace "— recorded by story 15, keyed by the proposal's id; until then approval records only the proposal's state;" with "`[2026-10-24, Andrei, 15a]` — one `pending` line appended to the `commitments_log.md` of the scope that owns the meeting when the PM approves, never while the proposal waits (`STAGED_APPROVAL` is a proposal state and is never written to a ledger), keyed by the proposal's root id, so an edit's replacement revises the same commitment; the approval is committed first and the line follows, and a missing line is written later, never twice; the line stores the meeting's day the due was resolved against, and a due resolved on an estimated day is marked estimated and never alone makes a commitment late; once approved, such a proposal is no longer rejected, and the commitment changes only through its own lines — the PM's mark (`fulfilled`, `altered`, `broken`, `withdrawn`), a due or owner he sets, or a telemetry verdict, and a verdict never replaces the PM's mark; `WITHDRAWN`, an approval the PM says was a mistake, is terminal and is never counted as broken;".
- **AD-28** (`:446`):
  - the title becomes "Commitments belong to the scope that owns their meeting; coaching commitments are a separate entity";
  - **Prevents** (`:449`): replace "a 1:1 coaching undertaking being written into the project ledger," with "a 1:1 coaching undertaking being written into a commitment ledger, or a commitment into a ledger of the wrong scope `[2026-10-24, 15a]`,";
  - **Rule** lead-in (`:450`): replace "Three distinct entities, three scopes, no shared storage and no shared code path:" with "Three distinct entities, no shared storage and no shared code path; a `Commitment` is kept in the scope that owns its meeting `[2026-10-24, Andrei, 15a]`:";
  - in the table, the `Commitment` row's scope becomes "the meeting's scope: project — `.project-ai/memory/commitments_log.md`, or personal — `~/.manager-ai/memory/commitments_log.md` `[2026-10-24, Andrei, 15a]`; never `people` (AD-4)";
  - replace "The project-scope ledger has no code path that accepts a personal or people entity." with "Neither commitment ledger has a code path that accepts a `CoachingCommitment` or a `people`-scope entity — the accessor takes only lines built from a `Commitment` — and a commitment is written only to its own meeting's scope: a personal meeting's never to a project's ledger, a project meeting's never to the personal one `[2026-10-24, Andrei, 15a]`.";
  - add `15a` to **Binds**.
- **AD-33** (`:505`): after "meeting-derived facts cite `meeting:<id>` plus speaker, with an optional time offset used only for tracing.", add: "`[2026-10-24, Andrei, 15a]` A commitment from a spoken promise cites its meeting and its speaker, a name as said; one from a summary's action item cites its meeting and its owner, since the summary records who owns an item, not who said it."
- **AD-36** (`:554`): after "self-authored activity may be displayed for context but never counted.", add: "`[2026-10-24, Andrei, 15a]` The PM's own mark of a commitment as fulfilled, altered, broken or withdrawn is not evidence and needs none: it is his decision, recorded as a line of its own kind under his handle, counted apart from telemetry verdicts (SM-9's manual override; `withdrawn` excluded from every broken-promise rate), never produced by pm-ai and never read as telemetry having verified anything."
- **AD-37** (`:570`): add `15a` to **Binds**, and after "Terminal states are terminal, and a worker re-checks state **at execution time**, not only at enqueue time." add: "`[2026-10-24, 15a]` A commitment's lines are appended by compare-and-append: the writer folds the ledger, leaves out every line whose entry id is already there, and appends with the size it read checked in its claimed step; a ledger that grew is read again, never appended to blindly."
- **The module map** (`:1100`): replace "`core/commitments` + storage" with "`domain/commitments`, `core/commitment_log` (the ledger, `15a`), `core/due_dates`, `core/commitments` (the verifier, story 16) + storage".

*PRD (`prd.md`), through bmad-prd:*
- **Personal tree** (`:65-67`): after the `coaching\_1on1\_history.md` line, add "│   ├── commitments\_log.md            \# Commitments from purely personal meetings (FR-34), written on approval (2026-10-24)".
- **Project tree** (`:96`): replace "\# Spoken commitments & promise tracking ledger (FR-34) — MACHINE-LOCAL (gitignored)" with "\# Spoken commitments & promise tracking ledger (FR-34), written on approval — MACHINE-LOCAL (gitignored)".
- **Glossary** (`:267`): replace "(recorded as structured Markdown entries in .project-ai/memory/commitments\_log.md and indexed" with "(recorded as structured Markdown entries in the commitments\_log.md of the scope that owns the meeting — a project's, or the personal scope's — written when the PM approves, and indexed"; and "lifecycle statuses (\[STAGED\_APPROVAL\], \[PENDING\]," with "lifecycle statuses (\[STAGED\_APPROVAL\] — the proposal's state, never a ledger entry — then \[PENDING\], \[WITHDRAWN\],".
- **Architecture box** (`:766`): replace "\- daily\_dashboard.md                    \- commitments\_log.md (FR-34 Ledger) |" with "\- daily\_dashboard.md, commitments\_log.md \- commitments\_log.md (FR-34 Ledger) |".
- **FR-34** (`:653`):
  - header: replace "(.project-ai/memory/commitments\_log.md and indexed" with "(the commitments\_log.md of the scope that owns the meeting — a project's, or the personal scope's — and indexed";
  - 34.1's first sentence becomes "**Extraction & Initial Staging:** Commitments extracted from a meeting (FR-03, FR-06) are staged as proposals while they wait for the PM; \[STAGED\_APPROVAL\] is the proposal's state and is never a ledger entry. When the PM approves one, one structured Markdown entry is appended to the commitments\_log.md of the scope that owns the meeting and indexed in commitment\_index.db (Tier 3, disposable).";
  - replace "Each entry records: commitment\_id, timestamp, speaker, target\_assignee, description, target\_work\_item, due\_date, and initial status (\[STAGED\_APPROVAL\] or \[PENDING\])." with "Each entry records: commitment\_id (the approved proposal's id), the approval time, the speaker of a spoken promise or the owner of an action item, as names (linked to a person only through an exact saved alias, when read), the description, the meeting's day (marked when estimated), the due as said and the date worked out from it against that day or `unresolved`, the meeting it cites, and status \[PENDING\]; the target work item is joined when the commitment is verified.";
  - the \[STAGED\_APPROVAL\] bullet becomes "\[STAGED\_APPROVAL\]: the proposal's state while a spoken commitment waits for the PM's approval on a Telegram/CLI card; nothing is in the ledger yet. Approval writes the commitment at \[PENDING\].";
  - in \[ALTERED\], replace "or via direct PM edit" with "or by the PM's own mark";
  - after the \[ERROR\] bullet add: "\[WITHDRAWN\]: the PM's own mark for an approval made by mistake; terminal, and never counted as a broken promise. **The PM's own mark.** The PM may mark a commitment fulfilled, altered, broken or withdrawn himself; the mark is his, recorded under his handle, needs no telemetry evidence, outranks a telemetry verdict, and is counted apart from telemetry (SM-9's manual override). **No date, never overdue:** a due that names no single date is kept as said and recorded as unresolved, and such a commitment is never overdue until the PM sets a date; a date worked out from an estimated meeting day never alone makes a commitment late.";
  - in the first consequence, replace "in .project-ai/memory/commitments\_log.md" with "in the meeting's scope's commitments\_log.md";
  - Decisions Log line: "*The ledger starts at approval (2026-10-24, Andrei; FR-34, FR-06, Glossary):* \[STAGED\_APPROVAL\] is a proposal state, never a ledger entry; personal meetings keep a personal ledger; the PM's own marks — fulfilled, altered, broken, withdrawn — are recorded under his handle and counted apart from telemetry, withdrawn never as broken; a due is resolved to a date against the meeting's day or recorded as unresolved, and an estimated day never alone makes a promise late."

*SPEC and companions, re-derived by bmad-spec:*
- **CAP-6** (`SPEC.md:51`): replace "and writes a `[STAGED_APPROVAL]` entry to `commitments_log.md` that posts nothing until approved." with "staged as a proposal whose state is `[STAGED_APPROVAL]`, which posts nothing and writes nothing to `commitments_log.md` until approved; approval writes one `[PENDING]` entry to the meeting's scope's `commitments_log.md` (2026-10-24, Andrei, 15a)."
- **Glossary** (`glossary.md:35`):
  - replace "structured Markdown in the project scope's `commitments_log.md`" with "structured Markdown in the `commitments_log.md` of the scope that owns the meeting — a project's, or the personal scope's for a personal meeting, never a `people` scope (2026-10-24, Andrei, 15a)";
  - replace "lifecycle statuses `[STAGED_APPROVAL]`, `[PENDING]`," with "`[STAGED_APPROVAL]`, the proposal's state while the PM has not yet approved (never written to the ledger, which starts at approval — 2026-10-24, Andrei, 15a), then the ledger's statuses `[PENDING]`, `[WITHDRAWN]` (the PM's mark for an approval made by mistake, never counted as broken),";
  - after "needs a human to re-authenticate or fix configuration)", add "; a status is set by telemetry or by the PM's own mark, which is recorded under his handle and counted apart from telemetry; a due is kept as said beside the date worked out from it against the meeting's stored day, or `unresolved`, a commitment with no date is never overdue, and one dated from an estimated day is never late on that alone".
- **SM-9** (`success-metrics.md:17`): replace "versus manual override" with "versus manual override (the PM's own `fulfilled`, `altered` and `broken` marks, counted from their own ledger lines; `withdrawn` marks excluded, 2026-10-24)".
- **`scope-model.md` §B** (`:108-112`): after "│   ├── coaching_1on1_history.md       # Socratic 1:1 logs, meta-feedback & growth notes", add "│   ├── commitments_log.md             # Commitments from purely personal meetings, written on approval (2026-10-24, 15a)".
- **`derivation-services.md`:**
  - `:99`: replace "Transcript processing outputs `commitments_log.md`; commitment indexing inputs it;" with "Approving a commitment writes `commitments_log.md` (`15a`, a request rather than a job); commitment indexing inputs it;";
  - `:108`: in transcript processing's outputs, remove "commitments → `commitments_log.md`, ";
  - `:117`: replace "transcript processing runs → `commitments_log.md` gains entries → commitment indexing runs" with "transcript processing runs → proposals are staged → the PM approves → `commitments_log.md` gains an entry → commitment indexing runs";
  - `:125`: the `CommitmentLog` row's users become "the approval and its catch-up (append, `15a`), the PM's marks (append, `15c`), the lifecycle job (append verdicts, story 16), indexing (read)".

## Verification

**Commands:**
- `uv run pytest tests/core/test_due_dates.py tests/core/test_commitment_log.py tests/slice/test_commitments.py tests/slice/test_proposals.py tests/slice/test_transcript_path.py tests/core/test_member_updates.py tests/architecture -q` -- expected: every matrix row passes; the AD-33 and both AD-28 tests pass
- `uv run pytest -q` (in the background, about 7 minutes) -- expected: all pass; 6 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a scratch home (`HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1`), drop and summarise the recorded fixture into personal. Approve one item, and `edit --due "до пятницы" --approve` another. Expected: the scratch `~/.manager-ai/memory/commitments_log.md` holds two `pending` lines, each with `day`, the second with `proposal=<root>_e1` and the Friday's date. `proposal reject` on the first is refused, naming `proposal edit`.
- In the same scratch home, approve one more item with the daemon stopped, then start the daemon, stop it and start it again. Expected: the third line appears once, with `written=catch_up`.
