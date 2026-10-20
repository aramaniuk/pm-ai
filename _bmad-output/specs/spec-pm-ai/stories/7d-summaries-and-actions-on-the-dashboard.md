---
title: 'Meeting summaries and open action items on the dashboard'
type: 'feature'
created: '2026-10-16'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Once `7b` and `7c` write summaries and stage action items, nothing the PM reads each morning shows them: the dashboard lists meetings by time, duration and attendees only, and a staged action item is visible nowhere. Summaries are also written hours after a meeting — at the next harvest, or when the PM asks — so a dashboard choosing meetings by when they started would miss most of them. And AD-17's monthly frontier total is shown nowhere the PM looks each morning.

**Approach:** Each scope's dashboard gains, at the end of Time-Critical Activities, the meetings whose summary — or its failure — was recorded since that scope's last render, each with a one-line excerpt, who made it, and its open action items, read from the summary state, the meeting records and the proposals store, with no model in the render path. Leadership Notes stays free of synthesis, its sentence corrected, and on the personal dashboard gains one computed line: the month's frontier spend against the $20 target.

## Boundaries & Constraints

**Always:**
- **Prerequisites:** `7e` (the saved alias table and its read-time rule); `7b` (the summary state row, `put_summary`'s grammar, `action_item` proposal ids carrying the scope); `7c` (the ledger's call status, for the spend line).
- **Placement** (memlog "7d placement", 2026-10-16). Under Time-Critical Activities as a nested list after the day's schedule, no new heading — CAP-9's four stay exactly four. A summary is quoted from a record, not synthesized at render. Leadership Notes stays synthesis-free; its sentence that nothing in the dashboard "was written rather than computed" stops being true once a model-written excerpt appears, so it now says no synthesis runs at render and that the summaries above are quoted from their records, each naming the model that wrote it. The project file has no Leadership Notes; there each excerpt's own attribution is the statement.
- **Which meetings: by when the summary was recorded.** Summary state rows in the scope whose recorded instant lies between the scope's look-back instant (`9b`'s `since`: the last render, 24 hours when none, never more than 7 days) and the render's instant, in states `summarized`, `waiting`, `failed` or `nothing said` — the last listed with "the transcript held no words after cleaning" and no excerpt. At most 10, the most recently recorded; more are stated as a count. Each record is read on its own, by id, so one malformed record is stated by name and the others still show.
- **What each shows.** Start in the display zone, title, the summary as `7b`'s parser decodes it (`render_value`'s escapes undone), its line breaks folded as `23f` folds them, then cut to 200 characters by `cut_at_word` — at the last space before the limit, a hard cut when there is none, with "…" when cut — then escaped (`23f`'s order: fold, cut, escape); who made it, from the record's parsed `RecordedSummary` fields (`made_by`, `model`, `requested`): the local model or Claude, the model, automatically or on request; up to 5 open action items as `owner: what (due)` with the rest as a count — on a project dashboard the owner is shown only once the label is resolved to a project member by `7e`'s read-time rule (the alias table, or an exact display name or username), otherwise as unassigned, so spoken names stay off the team page; the personal dashboard shows the label; and the meeting id with the command that asks for a summary. A row `waiting` or `failed` with no summary in the record says so, with the reason cut to the section's bound; a `waiting` one names its maker from the row's `made_by` (the local model or Claude). A row `waiting` or `failed` while the record still holds an earlier summary (a later part re-opened it) shows that summary and adds that a newer one is waiting, or failed with its reason. Every string goes through `escape`.
- **Open action items:** proposals of type `action_item` for that scope and meeting — the scope is part of `7b`'s id — that are `staged` and whose `expires_at` is after the render's instant. `11b`'s pattern promises are not listed, so one item found both ways is not shown twice. On a project dashboard an item that `7e` places on the same page under an issue — a row Team Activity shows — is left out of the list and does not count toward the 5; its meeting shows "shown under #N" in its place, so one item is never shown twice on the page.
- **Project dashboards never read personal records (AD-25, AD-38) — by signature.** The project renderer takes a `ProjectMeetingDigests` value whose constructor refuses a digest from any scope but that project; a personal digest cannot be passed to it. A project's dashboard reads that project's summary state rows, meeting records and proposals, plus `7e`'s resolver, which reads `aliases.md` and `8t`'s members for that project — and nothing else. The personal dashboard reads personal ones and each enrolled project's — a new read across scopes, from project into the PM's own file, the direction AD-38 allows and `_belongs` already takes for meetings (`pipelines.py:2326`); a project that cannot be read or is no longer enrolled is stated in one line naming it, and the others render. `people` rows and records are never read for either dashboard.
- **The month's frontier spend, personal dashboard only.** One line in Leadership Notes, from `DisclosureLedger.monthly_total` over the current UTC month (a call counted in the month of its `sent` line): calls whose lines report usage — `completed`, or `failed` with usage — and their total cost against the $20 monitored target, calls of unknown cost if any, and a warning when the total is past it (AD-17, NFR-13). A ledger that cannot be read is stated with its reason; malformed lines are left out of the total and named after it, as `7c`'s `_frontier_spend` does (`skip_malformed`). Never on a project dashboard: the ledger names every scope.
- **No model at render time.** `core/rendering.py` stays pure; `prepare_dashboard` reads state rows, records, proposals and the ledger as reads on the worker (AD-5, `4r`), and never reaches `Daemon.model` or `Daemon.router`. A test renders with a `Daemon.model` and a `Daemon.router` that each raise if called, and the dashboard path imports nothing from `pm_ai.models`.
- **Nothing is invented.** No rows in the window is stated with the window it looked over; an unreadable record or scope is stated, never rendered as "no summaries".

**Ask First:** Nothing. Placement and selection were settled 2026-10-16 (memlog "7d placement").

**Never:** No model call, no summary written, no re-render when a summary lands (the next render shows it). No new heading. No `people` row or record read. No ledger line on a project dashboard. No approve or reject control — the dashboard is a render product (SPEC).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Afternoon render | a morning meeting summarized at 13:00, 3 open items | after the schedule: excerpt, "the local model qwen3:8b, automatically", 3 items | N/A |
| 07:00 render | a 15:00 meeting summarized by Claude at 18:00 yesterday | listed with "Claude …, on request" | N/A |
| Summary written late | meeting last Tuesday, summary recorded since the last render | listed | N/A |
| Long summary | 450 characters, a space at 193 | cut at 193 with "…", then escaped | N/A |
| No space | 250 characters without a space | hard cut at 199 with "…" (200 characters) | N/A |
| Many items | 8 open items | 5 shown, "and 3 more" | N/A |
| Owner on a project page | owner label "Ваня", no alias row / an alias row to a member | "unassigned: …" / that member's display name | N/A |
| Approved or expired | one approved; one staged past `expires_at` | neither listed | N/A |
| Pattern promise | an `11b` promise for the same meeting | not listed | N/A |
| Placed by `7e` | on a project page, an open item that Team Activity shows under issue #142 | left out of the meeting's items, not counted toward 5; the meeting shows "shown under #142" | N/A |
| Waiting / failed | no summary yet | "summary waiting for the local model" (or "for Claude", by the row's maker) / "summary failed: <reason>" | N/A |
| Re-opened | record holds a summary; row `waiting` after a later part | the summary shown, "a newer summary is waiting" | N/A |
| Project render | a personal meeting summarized the same day | absent; the renderer cannot take it | N/A |
| Personal render | a project meeting summarized | listed | N/A |
| Project unreadable | an enrolled project's store cannot be read | one line naming it; the others render | stated |
| People meeting | a `people` row in the window | never read | N/A |
| One malformed record | two rows; one record malformed | that one named; the other shown | stated |
| Nothing to show | no row in the window | a sentence naming the window | N/A |
| Hostile text | a newline and `|` in a title or summary | escaped, one bullet | N/A |
| Model wired | `Daemon.model` and `Daemon.router` raise if called | render succeeds; neither called | N/A |
| More than 10 | 14 rows | 10 most recent, "and 4 earlier" | N/A |
| Spend line | $21.40 this UTC month, one call unfinished | personal Leadership Notes: total, target, one call of unknown cost, a warning | N/A |
| Ledger unreadable | `disclosure.md` cannot be read / has a malformed line | the line states the fault / the total over the lines that read, then the malformed lines named | N/A |
| Frontier summary in a project | a project meeting summarized by Claude | listed on the project file with its attribution; no spend line there | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/core/rendering.py:1-71` -- purity and honesty rules; `:221-262` `HEADINGS`, `PROJECT_HEADINGS` (the project file's three since `23g`: Time-Critical Activities, Proactive Enablement, Team Activity); `:340-394` `render_dashboard`; `:397-463` `render_project_dashboard`, which since `23g` and `7e` takes `work` and `issue_notes` and whose signature gains the project-only digests after them; `:506-570` `_time_critical`, with four branches (no calendar, partial, unreadable, the schedule) after each of which the nested list goes; `:640-664` `_meeting_line`; `:909-918` `REASON_LIMIT`, `_bounded`, the section's bound for a reason; `:1494-1507` `_leadership_notes`, the sentence reworded and the spend line added; `:1512` `escape`, which folds line breaks first (`23f`); `:1610` `fold_breaks`
- `pm_ai/app/pipelines.py:1785-1867` -- `prepare_dashboard`: the reads, `look_back` for `since` at `:1817`, the two branches from `:1826`; `:1979-2015` `_issue_notes`, `7e`'s reads of the alias table and `member_updates.md`; `:2326-2343` `_belongs`; `:2621-2678` `_frontier_spend`, `7c`'s sentence over the month's total
- `pm_ai/core/meeting_records.py:824` -- `get`, one record by id; `:334-352` `MeetingRecord.summary`, a `RecordedSummary` from `7b`
- `pm_ai/core/meeting_summary.py:538-550` -- `action_item_prefix`, `action_item_id` (the scope digest is in the id); `:568-576` the provenance line's grammar, `Made by the local model|Claude <model>, automatically|on request, on <date>.`; `:654` `parse_summary_region`, which decodes the values
- `pm_ai/domain/meeting_summaries.py:135` -- `SummaryMaker`; `:156` `RecordedSummary` (`model`, `requested`, `made_on`, `answer`, `made_by`); `:173` `SummaryStateKind`; `:186` `SummaryState`, whose `at` is the row's last write
- `pm_ai/storage/service.py:217-222,261-275` -- the `proposals` and `meeting_summaries` tables; `:4921` `summary_state`; `:5227-5307` `stage_summary_items`, which matches a meeting's items by prefix and exact length and moves dropped ones to `superseded`; `:5436` `load_proposal`
- `pm_ai/domain/proposals.py:33-35` -- `expires_at`
- `pm_ai/core/disclosure_ledger.py:45` -- `MONTHLY_TARGET_USD`; `:54-78` `MonthlyTotal`; `:233-310` `monthly_total`
- `pm_ai/core/member_updates.py:935` -- `people_of`; `:965` `resolve_label`, `7e`'s read-time rule; `:1005` `cut_at_word`
- `pm_ai/app/wiring.py:413,428` -- `Daemon.model` and `Daemon.router` (`7c`)
- `pm_ai/surfaces/cli/dispatch.py:3977` -- `pm-ai transcript summarize --scope <scope> --meeting <id> [--by local|claude]`, the command that asks for a summary
- `tests/architecture/test_domain_invariants.py:227` -- the AD-25 signature test, which must still pass
- `tests/core/test_rendering_sections.py`, `tests/core/goldens/dashboard_full_day.md`, `tests/slice/test_dashboard_slice.py` -- hold today's Leadership Notes sentence

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/renders.py` -- `MeetingDigest` (start, title, excerpt, maker, items, hidden count, id, newer-summary note or the reason there is none) and `ProjectMeetingDigests`, refusing any other scope -- the wall as a type
- [ ] `pm_ai/core/rendering.py` -- the nested list in Time-Critical Activities for both renderers; Leadership Notes reworded with the spend line, personal only -- one section text for both files
- [ ] `pm_ai/core/rendering.py`, `pm_ai/core/member_updates.py` -- `cut_at_word` moves from `member_updates.py:1005` to beside `fold_breaks` in `rendering.py` (which `member_updates` already imports, so `rendering` cannot import it back), `member_updates` imports it from there, and the excerpt uses it with 200 -- one cut, with "…", for both stories
- [ ] `pm_ai/core/team_activity.py`, `pm_ai/core/member_updates.py` -- `IssueNotes` also carries, for each action item a row Team Activity shows, its item id and issue iid, so the project digests leave those items out and say "shown under #N" -- no item twice on a project page
- [ ] `pm_ai/storage/service.py`, `pm_ai/ports/__init__.py` -- summary rows recorded in a window for a scope; open `action_item` proposals for a scope and meeting at an instant -- what to show
- [ ] `pm_ai/app/pipelines.py` -- `prepare_dashboard` builds the digests per scope, reading each record by id, and the spend line; unreadable records and scopes become stated lines -- reads only, no model
- [ ] `tests/core/test_rendering_sections.py`, the goldens, `tests/slice/test_dashboard_slice.py` -- one test per matrix row; goldens regenerated and read -- the matrix is the contract
- [ ] `tests/architecture/test_dashboard_has_no_model.py` -- the dashboard path imports nothing from `pm_ai.models`; a render with a `Daemon.model` and a `Daemon.router` that each raise if called succeeds -- no model at render time
- [ ] `stories/23a-dashboard-sections.md` -- a dated Change Log line: Leadership Notes' text and matrix row `:50` amended by `7d` -- an earlier frozen block amended on the record
- [ ] `tests/core/test_project_rendering.py:247`, `tests/core/mypy_fixture_project_render_valid.py` -- the parameter list and the fixture follow the project renderer's signature, after `23g`'s `issue_notes` -- the signature checked
- [ ] `stories/23d-project-render-scope-wall.md` -- a dated Change Log line: the project render's inputs grow by `ProjectMeetingDigests` and `7e`'s resolver (`7d`) -- an earlier frozen block amended on the record

**Acceptance Criteria:**
- Given a personal and a project meeting each summarized yesterday afternoon, when both dashboards render at 07:00, then the personal file lists both, the project file lists only its own, and both still have exactly their headings.
- Given a personal meeting summarized by Claude, when the project's dashboard renders, then nothing from that summary appears in it. (Moved from `7c`.)
- Given the change, when `uv run pytest` runs, then everything passes, the skip count is unchanged, and the AD-25 signature test passes unedited.

## Spec Change Log

- **2026-10-16, created** (decision of 2026-10-16: summaries and actions surfaced on the dashboard). Carries: summaries under Time-Critical Activities, Leadership Notes left synthesis-free with its sentence corrected; an excerpt, its maker, up to 5 open action items and the meeting id; the project wall; no model in the render path.
- **2026-10-16, review:** meetings chosen by when their summary or failure was recorded (memlog "7d placement"); each record read by id so one malformed record hides nothing; the cut at a word before escaping, a hard cut without a space; the maker from the provenance line; a re-opened summary shown with its newer state; open items filtered by `expires_at` and scope; the project wall as a `ProjectMeetingDigests` type; the personal read across projects declared, an unreadable project stated; `people` never read; the month's frontier spend as one line in personal Leadership Notes (AD-17); the AC about a personal Claude summary moved here from `7c`; plain words. Depends on spine amendment: **AD-17** — the monthly total surfaces on the personal dashboard's Leadership Notes (the briefing), never a project one. Amends `23a`'s frozen block (task above).
- **2026-10-16, review (project owners):** on a project dashboard an action item's owner is shown only once matched to a member through `7e`'s alias table, otherwise as unassigned (memlog "7d project pages"); `7e` becomes a prerequisite; the Change Log moved before Design Notes, as the template places it.
- **2026-10-16, review (cross-spec):** a project page's reads named — its rows, records, proposals, and `7e`'s resolver over `aliases.md` and `8t`'s members; owners "resolved by `7e`'s read-time rule"; `nothing said` listed; the summary decoded, folded, cut, escaped; "reads on the worker"; the project-render tests and mypy fixture, and a Change Log task for `23d`.
- **2026-10-16, spine gate:** frozen block edited (recorded here): the spend line counts every call whose line reports usage, `completed` or `failed`, in the UTC month of its `sent` line (AD-38 as tightened at the gate).
- **2026-10-20, re-verified against `main` at 705d9c4** (after `23e`–`23h`, `7a`, `7b`, `7e`, `7c`): Code Map line numbers moved and anchors added for what those stories built (`RecordedSummary`, `SummaryState`, the provenance grammar, `stage_summary_items`, `resolve_label`, `cut_at_word`, `MONTHLY_TARGET_USD`, `_frontier_spend`, `Daemon.router`, `transcript summarize`); the project-render signature test is at `:247`. Frozen block unchanged; the mismatches found went to Andrei as questions.
- **2026-10-20, review decisions (Andrei; the low-stakes ones by the coordinating session):** frozen block edited (recorded here). Andrei: on a project page an action item that `7e` places under an issue is left out of 7d's list, its meeting showing "shown under #N" (matrix row "Placed by `7e`" added; `IssueNotes` task added); "recorded" is `SummaryState.at`, the row's last write, accepted with no behaviour change (Design Notes). Coordinating session: a waiting summary names its maker from the row (local model or Claude); "made by" renders from the parsed `RecordedSummary` fields, not the provenance line's text; `cut_at_word` moves beside `fold_breaks` and is used with 200, so a cut ends in "…" (matrix rows for the cut amended; task added); the no-model render test also gives a `Daemon.router` that raises if called; the spend line totals past malformed lines and then names them, as `7c`'s `_frontier_spend` does; `decision:` events stay off the personal page (no change).
- **2026-10-20, readiness (cosmetic):** frozen block edited (recorded here): the cited `pipelines.py:2034` corrected to `:2326`, where the merged slices moved `_belongs`. No behaviour changes.

## Design Notes

Selecting by the summary's recorded instant, not the meeting's start, is what makes a late summary appear once: the window is the one Proactive Enablement uses, so the next render after the summary shows it and the one after does not repeat it.

"Recorded" is `SummaryState.at`, which is the row's last write, not only its settling: offering, re-opening, a job taking the lease and a timeout each move it (`service.py:4776`, `:5100`, `:5173`). So a `waiting` row a job touched after the last render comes back on the next one, saying again that the summary is waiting. Accepted by Andrei on 2026-10-20: it is what the row says, and a summary that settles is still shown once.

Choices made in review (2026-10-16): the spend line goes in Leadership Notes because it is computed, personal, and the only place AD-17's "surfaces in briefings" can mean today; the wall is a type the project renderer takes, so a wrong branch fails at construction, not at review; a re-opened summary is shown with its newer state rather than hidden, so the PM never loses a summary that existed.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; skip count unchanged
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a throwaway home with a summarized meeting from `7b`'s manual check, `pm-ai dashboard --scope personal`: the meeting appears after the schedule with its excerpt and maker, and Leadership Notes has the spend line.

