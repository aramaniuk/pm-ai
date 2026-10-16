---
title: 'Meetings update a project member''s priorities'
type: 'feature'
created: '2026-10-16'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The project dashboard's Team Activity (`23g`) shows each member's priorities and tasks from the project's GitLab issues (`8t`). But much of what changes a member's week is said in meetings: "Speaker 2 takes #142 by Thursday", or a decision to drop a feature. After `7b` those sentences exist as validated action items and decisions, and nothing connects them to the member or the issue. Nor is there anywhere to record that a name said in a meeting is a given person: AD-34's alias table is a dictionary only tests fill, and the spine records that it must be saved before any page shows a resolved name.

**Approach:** After a project meeting is summarised, each action item and decision that names a GitLab issue — by a number said in the meeting, or by a title the local model matches — is appended to a ledger in the project's scope, holding the issue and the speaker label as said. Names become people only through the saved alias table, which this slice builds: one row per name the PM confirmed, mapping it to a GitLab handle. This slice also renders the updates in the slot `23g`'s Team Activity leaves for them. GitLab is never edited.

## Boundaries & Constraints

**Always:**
- **Prerequisites.** `7b`, handing this slice its validated answer — items with ids, owners that are speaker labels of that transcript or null, decisions with references — and its superseded item ids; `8t`'s record of members, issues and merge requests, read through `StoragePort.project_work(scope) -> ProjectWork` (`pm_ai/domain/work.py`): per member a `gitlab:<numeric user id>` handle, display name, username and state; per issue its iid, title (`confidential issue` for a confidential one), state, assignees (handle, display name, username — no state; a person's state is taken from the member row), milestone title and due date. `8t` stores no references, so this slice builds an issue's `gitlab:<pm-ai project id>:issue:<iid>` from the scope and the iid; the `gitlab` handle system `8t` adds to AD-34; `23g`, landed first, with its `issue_notes` slot and its placed-issues function; the spine amendments in the Change Log. If `8t` or `23g` land with a different shape, halt and ask.
- **Project meetings only — by type.** The step and the ledger take a `ProjectScope`, built only from a meeting record whose own scope is a project; a personal or `people` meeting cannot be passed, so no issue list enters its prompt and nothing is written. The project ledger `member_updates.md` is declared in the project tree's `memory/` only — Tier 1, `encrypted=False`, `gitignored=True` — and reading or writing it for a personal or `people` scope raises.
- **Append-only, enforced.** `member_updates.md` and the alias table join `_APPEND_ONLY_KEYS`, are written only through a storage append method, and join `LEDGERS` in the static-rules test, so a whole-file write of either is refused by the writer.
- **What an update is: a meeting note on an issue, never a new priority.** Priority belongs to GitLab and to the people who set it there; pm-ai inferring "this is now high priority" from talk would be invented evidence on a page the team reads. A line is an action item (what, owner label, due as said, and a date when it resolves) or a decision (the statement). A resolved due is labelled as said in the meeting and shown beside the milestone's due date, never replacing it.
- **Due dates resolve only when unambiguous**, against the meeting's day in the display zone: a weekday means its next occurrence after the meeting day, and the meeting's own weekday is ambiguous, so no date; a day and month without a year means the next occurrence on or after the meeting day; anything else ("soon", "next sprint") keeps the words and no date.
- **Which issue.** First, every bare number in the item's own text — `#142`, `issue 142`, `задача 142`, bare by `23g`'s rule (no letter, digit, `/`, `&` or `!` before the `#`, no digit after; a path-qualified `other/proj#142` is ignored) — that is an iid in `8t`'s record; an item naming two gets one line per number; shown as certain. Then, for items with none, one local-model call (`classification`, local-only; `flow` project to project) given the items and the project's open, non-confidential issues (iid and title, each a sanitized fragment) answers, per item, one listed iid or none; an iid not in its list is discarded. Such a link is shown as **model-matched** and listed by `member unmatched` for the PM to confirm or reject (`pm-ai member link <update id> --confirm|--reject`, appended to the ledger); confirmed, it shows as certain; rejected, it is hidden. Confidential issues never enter the prompt — only the placeholder title is stored — so only a spoken number can match one. *Admitted limit:* an issue title is text a person typed, and the sanitizer's phrase lists are English, so a title can steer the match; what bounds it is that only a listed iid is accepted and the link is marked model-matched until confirmed.
- **When the classification call failed or was unavailable** — Ollama not answering, the call failing, timing out or answering unparseably — the numbered lines are written, the rest are recorded as title match pending with the reason, and the meeting is offered again at the next harvest within `7b`'s bound of 3 meetings per harvest, at most 3 attempts; then the pending items are dropped from matching and `doctor` names the meeting.
- **Names become people only in the saved alias table (AD-34).** A speaker label is a display name, never a handle; the alias table is the one place a name becomes a handle. This slice saves that table: Tier 1, in the application scope (`aliases.md`, beside `disclosure.md`, `encrypted=False`, `gitignored=True`), append-only, one row per confirmed name → handle, keyed by one `normalise()` (Unicode NFKC, case-folding, trimming, collapsing whitespace), optionally scoped to one meeting; a later row removing or replacing a mapping. Later name resolution — Graph speakers, chat senders — reads the same table; there is no second map. This closes the spine's open risk that the table be saved before the first page shows a resolved name.
- **How a label resolves, at read time, never guessed.** In order: (1) an alias row for that label scoped to this meeting, then an unscoped one, whose handle is a member in `8t`'s record; (2) exactly one active member whose display name or username equals the label through the same `normalise()`. When `8t`'s members section is unread or absent, rule (2) compares against the assignees `8t` recorded on the project's issues, and the page says members were not read; with neither, every label is unmatched. A generic label — `Speaker <n>`, `Unknown`, `Guest`, their Russian forms, or one with no letters — never matches by rule (2). An owner binds to a member only through a speaker label of that meeting's transcript, which `7b` guarantees. *Why:* Teams speaker labels come from the same company directory as GitLab display names, so equality covers the common case with no judgement; nicknames and transliterations ("Ваня", "Vanya", "Ivan") cannot be told apart from a different person by any rule pm-ai could state, and a wrong match puts work on a colleague's page the whole team reads. *Admitted limit:* a speaker who is not a member but carries a member's exact display name binds to that member until the PM removes the match.
- **The PM's commands.** `member name` asks for a confirmation, so it joins `TERMINAL_BOUND`; `member link` and `member unmatched` have `DAEMON_BOUNDS` entries. `pm-ai member name --scope project:<id> "<label>" <member>` appends an alias row to that member's `gitlab:` handle; `<member>` is the handle, a username, or a display name exactly one active member carries. A member not in `8t`'s record is refused, naming the members it knows. A label already mapped elsewhere is replaced only after a confirmation at the terminal, naming both. `--remove` appends a removal. A generic label is refused unless `--meeting <id>` is given, and then the row applies to that meeting only — one confirmation never attributes every meeting's "Speaker 2" to a colleague. `pm-ai member unmatched --scope project:<id>` lists unmatched labels with their counts, model-matched links awaiting confirmation, and skipped malformed lines.
- **Lines are keyed** by `7b`'s item id (or decision reference) and issue: a repeat appends nothing. A re-summary that rewords an item, or changes its owner or issue, gives a new item id and appends; for each item `7b` superseded, and each decision the new summary no longer lists, a `withdrawn` line is appended, and the reader hides withdrawn items. A malformed line in either ledger is skipped and counted. Text was sanitized by `7b`.
- **This slice renders the updates, in `23g`'s slot.** The rows reach `render_project_dashboard` through its `issue_notes` keyword (`23g`'s `IssueNotes` type, empty by default): finished, escaped lines keyed by issue iid, plus closing lines; `23g` places each issue's lines under that issue wherever it appears and escapes nothing in them. Under each issue the lines show: an action whose owner resolves to an assignee, attributed to that member; an action whose owner resolves to a member who is not an assignee, attributed to that member and marked "not assigned in GitLab"; an action whose owner is null or unmatched, as unassigned — no spoken name on the team page; a decision. Each row: the meeting's title and date, the text with its line breaks folded, cut to 100 characters at a word, then escaped (`23f`'s order), the said due, and its marker — "said in the meeting" for a spoken number or a confirmed link, "model-matched" otherwise. At most 2 per issue, newest meeting first. Under a confidential issue a row shows its kind, meeting and date and no text. One closing line counts, with numbers only: updates on issues the page does not place — closed, or hidden by `23g`'s caps, decided by `23g`'s pure placed-issues function in `pm_ai/core/team_activity.py`, never recomputed here — and unmatched labels awaiting `member unmatched`.
- **Which updates: by meeting date.** Updates from meetings in the last 14 days, whenever their lines were written, so a name confirmed after the meeting shows at the next render.
- **No ranking or comparison of people** (kernel amendment 2026-10-16): rows sit under issues and are never counted per person.
- **Never edits GitLab.** No request to GitLab from this slice at all.

**Ask First:** Nothing beyond the prerequisite shapes above.

**Never:** No GitLab write or read. No priority, label, milestone or assignee changed or inferred. No fuzzy, phonetic or transliterated name match, no model deciding who someone is. No line in a personal or `people` scope. No spoken name on the team page. No second name map. No line for an item that names no issue (it stays a `7b` proposal). No ranking of members.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Number and exact name | owner label equals an assignee's display name; "fix #142 by Thursday", meeting Monday | under #142, attributed to that member, due Thursday, "said in the meeting" | N/A |
| Username | label equals a member's username | matched | N/A |
| Member not assignee | resolved member is not on #142 | under #142, attributed, "not assigned in GitLab" | N/A |
| Two numbers / path-qualified | "#142 and #150" / "other/proj#142" | two lines / ignored, offered to the title match | N/A |
| Title match | "переделать экспорт отчётов", open "Report export rework" | line "model-matched"; listed by `member unmatched` | N/A |
| Confirm / reject | `member link <id> --confirm` / `--reject` | shown as certain / hidden | N/A |
| Invented iid | answer names #999, not listed | discarded | N/A |
| Classification call failed | Ollama down or answer unparseable | numbered lines written; rest pending; offered next harvest, 3 attempts | `doctor` names it after |
| Steering title | an open issue's title carries an instruction | accepted only as a listed iid, marked model-matched | admitted limit |
| Confidential issue | an update on a confidential issue | kind, meeting and date shown; no text | N/A |
| Unmatched owner | label "Ваня" | under the issue as unassigned; counted in the closing line; listed by `member unmatched` | N/A |
| Confirmed later | `member name … "Ваня" ivan.p` two days after the meeting | next render attributes the update; an alias row in `aliases.md` | not a member → refused naming the members |
| Variant spelling | `" ВАНЯ "` after "Ваня" | same key | mapped elsewhere → confirmation naming both |
| Remove | `member name … "Ваня" --remove` | removal row; next render shows unassigned | N/A |
| Generic label | `member name … "Speaker 2" ivan.p` | refused | with `--meeting <id>`, that meeting only |
| Members unread | `8t`'s members section unread | rule (2) over issue assignees; page says members were not read | N/A |
| Closed or capped issue | update on a closed issue, or one past `23g`'s cap | counted in the closing line | N/A |
| Reworded on re-summary | item reworded | new line; old id withdrawn, hidden | N/A |
| Personal meeting | scope personal | cannot be passed; nothing written | type refuses |
| Read for a personal scope | the ledger read for `personal` or `people` | raises | named |
| Whole-file write | `write_artifact` on either ledger | refused | `AppendOnlyArtifact` |
| Due on its own weekday / day without year | "Thursday" on a Thursday / "3 Jan" on 28 Dec | no date / the coming 3 Jan | N/A |
| Malformed line | a hand-edited line | skipped, counted | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/scope_model.py:431-436` -- `APPLICATION_TREE`, where `aliases.md` joins `disclosure.md`; `:746-762` the project tree's `memory/`, where `member_updates.md` joins `commitments_log.md`
- `pm_ai/domain/storage_tiers.py:184-190` -- `_APPEND_ONLY_KEYS`, `is_append_only`
- `pm_ai/storage/service.py:2473,2518` -- `append_event_log`, `append_disclosure`, the append methods the new one follows
- `pm_ai/domain/handles.py:338-420` -- `ALIASES` (a dict only tests fill), `register_alias`, `person_for`: handle → person resolution; unchanged, the saved name → handle table sits beside it
- `pm_ai/domain/identity.py:164,221-258` -- `SourceRef`; issue references in `8t`'s scoped form
- `pm_ai/core/meeting_summary.py` -- `7b`'s validated answer (items with ids, labels, decisions with references) and superseded ids, handed to this step; `MeetingRecord.summary` holds only the block's text, decisions are events and actions staged proposals, so this slice reads the answer, not the record
- `pm_ai/app/pipelines.py` -- `7b`'s `summarize_meeting`, after whose writes this step runs; `prepare_dashboard`, which fills `23g`'s slot
- `pm_ai/surfaces/cli/dispatch.py:2793` -- `TABLE`; the `member` group is new
- `tests/architecture/test_encryption_policy.py:258`, `tests/architecture/test_paths.py:229`, `tests/architecture/test_static_rules.py:431` -- the excluded set, the paths table, `LEDGERS`
- `stories/8t-gitlab-issues-harvest.md`, `stories/23g-team-activity.md` -- the record; Team Activity, its bare-reference rule, caps, the `issue_notes` keyword and `IssueNotes` type, and the placed-issues function `placed_issue_numbers` in `pm_ai/core/team_activity.py`

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/scope_model.py`, `pm_ai/domain/storage_tiers.py` -- the two ledgers declared and append-only -- the boundary and the discipline by declaration
- [ ] `pm_ai/storage/service.py`, `pm_ai/ports/__init__.py` -- an append method for both; reading `member_updates.md` for a non-project scope raises -- enforced, not remembered
- [ ] `pm_ai/core/aliases.py` -- the saved name → handle table: rows, `normalise()`, scoped and removal rows, the read; beside `handles.ALIASES`, which is unchanged -- one place a name becomes a handle
- [ ] `pm_ai/core/member_updates.py` -- new, pure: `ProjectScope`, generic labels, bare numbers, due resolution, line render and parse with skip counts, keys, withdrawals, confirmations, the read-time rule, the rows `23g`'s slot takes -- decisions stated where checked
- [ ] `pm_ai/app/pipelines.py` -- after a project summary: number match, the classification call checked against its list, pending items, appends; `prepare_dashboard` builds the `IssueNotes` value, asking `23g`'s placed-issues function what was placed -- one step, one reader
- [ ] `pm_ai/surfaces/cli/dispatch.py`, `pm_ai/app/entry.py` -- `member name` (`--meeting`, `--remove`, replacement confirmation), `member link`, `member unmatched` -- the PM's controls
- [ ] `pm_ai/app/prompts.py`, `pm_ai/app/entry.py` -- `member name` in `TERMINAL_BOUND`; `DAEMON_BOUNDS` entries for `member link` and `member unmatched` -- routed as AD-21 declares
- [ ] `pm_ai/surfaces/cli/completion.py` -- value sources for `--scope` (enrolled projects), `<member>` (the project's member usernames and handles), `--meeting` (the project's meeting ids), `<update id>` (updates awaiting confirmation); `<label>` free text; `--remove`, `--confirm`, `--reject` as flags -- `4u`'s test covers every argument
- [ ] `tests/architecture/test_encryption_policy.py`, `tests/architecture/test_paths.py`, `tests/architecture/test_static_rules.py` -- the new files in the excluded set, the paths table and `LEDGERS` -- the declarations checked
- [ ] `tests/core/test_member_updates.py`, `tests/core/test_aliases.py`, `tests/slice/test_member_updates.py` -- one test per matrix row with a fake model and `8t`-shaped records; the personal-read test -- the matrix is the contract

**Acceptance Criteria:**
- Given a personal meeting whose action item says `#142`, when it is summarised, then no project file changes and no classification call is made.
- Given an update labelled "Ваня" on issue 41 and no alias row, when the project dashboard renders, then it shows under issue 41 as unassigned with no name; after `member name`, the next render attributes it with no new ledger line written.
- Given `uv run pytest`, then everything passes and no test makes a GitLab request.

## Spec Change Log

- **2026-10-16, created** (decision of 2026-10-16: member priorities are identified from GitLab issues and updated from meeting transcripts). Carries: project meetings only; an update is a meeting note on an issue, never a new priority; the issue by number, then one checked local-model title match; the member decided at read time; append-only keyed lines; no ranking; no GitLab request.
- **2026-10-16, aligned with `8t` and `23g`** (their specs, same day). Read `8t`'s record; `gitlab:<numeric user id>` handles; confidential issues never in a prompt; the milestone's due beside a said one.
- **2026-10-16, review:** matched on display name and username; written against `8t`'s record whatever its storage; members unread → issue assignees; generic labels per meeting only; one `normalise()`; one line per bare number; pending title matches retried 3 times; due-date ambiguity; lines keyed by `7b`'s item id with withdrawals; malformed lines counted.
- **2026-10-16, review (alias table and Team Activity):** confirmed names go into AD-34's saved alias table, built here in the application scope, append-only, with meeting-scoped, removal and replacement rows — no per-project map (decision of 2026-10-16); model-matched links marked and confirmable, spoken numbers certain (decision of 2026-10-16); the update rows, reader and line format moved in from `23g`, filling its typed slot: non-assignee members attributed and marked, closed and capped issues counted, unmatched owners shown as unassigned and counted, updates chosen by meeting date over 14 days; the write target a `ProjectScope` type, reads for personal or `people` raise; the ledgers declared, append-only enforced and in the architecture tests; owners bind only through the transcript's own labels, the same-name non-member an admitted limit; confidential issues' rows without text; the Code Map points at `7b`'s validated answer; "the classification call failed or was unavailable"; issue titles steering the match an admitted limit. Depends on spine amendment: **AD-34** — the alias table is saved: Tier 1, application scope, append-only, one row per confirmed name → handle, optionally scoped to one meeting, the one place a name becomes a handle; closes the open risk that it be saved before a page shows a resolved name; the `gitlab` handle system is `8t`'s.
- **2026-10-16, review (23g's final shape):** rows reach Team Activity as `issue_notes` (`IssueNotes`: finished, escaped lines keyed by issue iid, plus closing lines); the count of updates not placed uses `23g`'s placed-issues function in `pm_ai/core/team_activity.py`; matching on display name and username confirmed.
- **2026-10-16, review (cross-spec):** `8t`'s reader named (`StoragePort.project_work`, `ProjectWork`); issue references built from scope and iid; a person's state from the member row; fold, cut, escape; `member name` in `TERMINAL_BOUND`, `DAEMON_BOUNDS` for `member link` and `member unmatched`; completion sources. The AD-34 amendment, stated: the saved name → handle table **sits beside** the handle → person table that `person_for` and `pm_identity()` read — it records which handle a spoken or displayed name is, and the existing table, unchanged and still unsaved, records which person a handle is; neither replaces the other. Depends on spine amendment **AD-21** — `member name`, `member link` and `member unmatched` are foreground administrative commands parsed from the command line (one amendment with `7b`'s and `7c`'s).

## Design Notes

Storing the label as said and resolving it on read is AD-34's own rule applied to spoken names: the ledger records what was said, the alias table records what the PM confirmed, and a reader joins them, so a correction applies to every past line.

Choices made in review (2026-10-16): with members unread, issue assignees stand in, because they are the same people `8t` read on the same run; an unmatched owner is shown as unassigned rather than hidden, so the update is never lost and no spoken name reaches the team page; a resolved member who is not an assignee is shown under the issue, marked, because the meeting said so and GitLab may lag; updates are chosen by meeting date over 14 days so a later confirmation shows; the alias table lives in the application scope because a name → handle row describes a person, not a project, and later readers in other scopes reuse it.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
