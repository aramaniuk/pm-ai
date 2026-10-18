---
title: 'The GitLab harvest reads the project''s members, issues and merge requests'
type: 'feature'
created: '2026-10-16'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A project member's tasks and priorities are the project's GitLab issues (Andrei's decision of 2026-10-16), and `23g` shows them per member with what moved lately. The GitLab harvest reads only default-branch commits, so pm-ai knows no member, no issue and no unmerged work.

**Approach:** Each GitLab harvest also reads the project's members, and its issues and merge requests changed since the last run, and keeps them in the operational store, keyed by the project's scope, behind one reader — `StoragePort.project_work(scope)` — that `23g` and `7e` call.

## Boundaries & Constraints

**Always:**
- **The token is the enrolled `read_api` token**; no new scope, no re-enrolment.
- **What is kept.** *Issue:* iid, title, state, assignees, milestone title and due date, labels, `updated_at`, `closed_at`, `web_url`, the `confidential` flag. *Merge request:* iid, title, state, source branch, author, draft, `updated_at`, `merged_at`, `closed_at`, `web_url`, and the iids of this project's issues it is related to. *Member:* id, display name, `username`, state, bot. *A person on an issue or merge request* (assignee, author): id, display name, `username`, bot. **Bot** is GitLab's `bot` field: true or false when the answer carries it, *not stated* when it does not — never guessed from a username; `23g` leaves out only people marked true. **Never kept:** a description, a comment, an email (GitLab shows it only to group owners for enterprise users, so it is not reliably present), any field not named here.
- **A GitLab person is `gitlab:<host>:<numeric user id>`**, a new handle system (spine amendment to AD-34; the host added by Andrei's decision of 2026-10-16): the host is the connector row's GitLab server, lowercased, with its port kept only when it is not the scheme's default; a handle is parsed at its last `:`. The id must be an integer (not a boolean), ASCII digits only, and is written as `str(int(id))`; a missing, null or non-integer id gives no handle — the person is kept with display name and `username` only and is never matched by id. Display name and `username` sit beside the handle for display and for `7e`'s name matching, replaced on every harvest, never identity (decision "8t/7e usernames", 2026-10-16); `7e` maps handles to people through the saved alias table AD-34 calls for (decision "7e name map", 2026-10-16).
- **Where it lands: Tier 2, keyed by project scope and connector instance** (spine amendment to AD-3, whose Tier-2 list gains these tables; the instance added at the spine gate 2026-10-16). Every row carries `(scope, instance)`, never the scope alone, so two repositories' issues are never merged by number. **One GitLab instance per project scope, for now** (AD-10): `connector add gitlab` refuses a second enrolled GitLab row filing into a scope that already has one, naming the first. Only enrolled rows count: every watched project already has the built-in, credential-less `gitlab:<project id>` connector, and enrolling under that name replaces it, which is not a second instance (Andrei's decision A, 2026-10-17). Tables in `operational.db`: `gitlab_members`, `gitlab_issues`, `gitlab_issue_assignees`, `gitlab_merge_requests`, `gitlab_merge_request_issues`, and `gitlab_work_status` (per scope, instance and section — members, issues, merge requests: complete or unread, the reason, and the instant of the last complete read). Created on open; the store's version number does not change. Not the event log: its natural key is the reference, so an issue's later states would be dropped as duplicates of its first; `emits()` stays `{commit_pushed}`.
- **One reader:** `StoragePort.project_work(scope) -> ProjectWork` (`pm_ai/domain/work.py`): the members, the issues (open, and closed within 30 days), the merge requests (open, and merged or closed within 30 days) with their linked issue iids, and per section its status, its reason when unread, and its last complete read — so `23g` can say when what it shows is older than one harvest cycle. `23g` and `7e` read nothing else of this slice.
- **One write, one claimed step.** The harvest returns the change in a new `HarvestResult.work` field; `app` hands it to the writer as `record_project_work(scope, change)` — registered in `HANDED_CALLS`, asking git nothing — which reads the current rows, merges and writes in one transaction, placed after the events and before `9a`'s save of position, coverage and failure, so a crash re-reads and the merge replaces rows with themselves. A change counts as something harvested: `EMPTY` carries none, `HARVESTED` may carry only a change.
- **Incremental reading, paged by the filter.** Issues and merge requests are listed `order_by=updated_at&sort=asc&state=all&per_page=100&updated_after=<cursor>`. Each next request asks page 1 again with `updated_after` set to the last `updated_at` read (inclusive, "on or after"), skipping rows this walk already read; only when a whole page is rows already read at one instant does it ask the next page of the same filter. So an item updated mid-walk is read again later rather than skipped by a shifted offset. The cursor is the last `updated_at` read; one position per resource (commits, issues, merge requests), and a stored commit-only cursor still reads, with issues and merge requests then on a first run.
- **First run:** every open issue and merge request of any age (`state=opened`, same order and paging, no `updated_after`), nothing closed — an issue opened two months ago and still open is still a member's task, so `8s`'s 30 days (which governs commits) does not apply to the open set. A first run that completes with nothing sets the cursor to the run's start less five minutes, so the next run is incremental.
- **State.** An opened item is added or replaced; one closed or merged — first seen that way or not — is recorded with its state and time and leaves the open set; merged merge requests count for `23g` (decision "23g signals", 2026-10-16). A merge request in any other state (`locked`) is kept with its raw state, in the open set. Draft is `draft`, or `work_in_progress` on older GitLab, and false when neither is present. Items closed or merged more than 30 days before the run's start (by `closed_at` or `merged_at`, else `updated_at`) are dropped, and a drop is a change.
- **Deleted items.** When the last full check is more than 24 hours old, a run lists `state=opened` completely; an item stored open and not listed is recorded `gone` and leaves the open set. A check cut short changes nothing.
- **Members** are read whole each run from `GET /projects/:id/members/all`. Only a complete walk replaces the member rows and sets the section complete with this run's instant. A cut walk changes no row, sets the section `unread` with the reason (the cap, the budget, or the provider's fault) and keeps the last complete read's instant; it is also named in the run's failure.
- **Links** come from `GET /projects/:id/merge_requests/:iid/related_issues`, paged under the cap, for each merge request read this run, in walk order; entries with no `project_id` or another project's are dropped. At most 500 link requests a run: when the cap is reached, the merge-request cursor stops at the last merge request whose links were read, and the rest are read next run.
- **Confidential.** An issue with `confidential: true` keeps iid, assignees, milestone and dates; its title is stored as `confidential issue` and its labels left out. A merge request linked only to confidential issues has its title stored as `merge request for a confidential issue` and its source branch left out, since GitLab's defaults ("Resolve \"…\"", `142-title-slug`) quote the issue title.
- **Bounds.** One 10-minute budget per harvest shared by commits, members, issues, merge requests and links; each walk has the 500-page cap. Order: commits, members, issues, merge requests with links. **A throttle, a server error or a bound** stops the run alike: the resource being walked applies what it read and advances its cursor to the last row read, resources not started keep theirs, and the run's failure names them (retryable, with the hint).
- **A section GitLab will not show** — 403 or 404 on the members, issues or merge-requests list (a feature turned off, or not visible to the token) — is recorded `unread` with its reason in `gitlab_work_status`, keeps its rows and its last complete read's instant, counts as complete for the run, and is not in the run's failure; it is asked again each run. A complete walk of issues or merge requests sets its section complete with this run's instant; one cut short sets it `unread` with the reason. A 403 or 404 on one merge request's `related_issues` leaves its links as they were and marks them unread.
- **Coverage** is the commit walk's, as `8n` and `8i` define it: the work sections earn no window and never withhold it.
- **Untrusted text.** Titles, labels, milestone titles, branch names, display names and usernames are text a person typed. They sit outside AD-48's payload check, so every reader that puts one in a prompt sanitizes each at the model boundary (AD-12) — `7e`'s prompt builder does, per value.

**Ask First:** Nothing.

**Never:** No body, comment or discussion stored. No write to GitLab. No new token scope. No event-log entry for an issue or merge request. No change to commit events. No ranking or comparison of people (`23g`'s rule). No group or other-project items. No keyset pagination.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First run | no cursor; 40 open issues, one opened 80 days ago; 6 open merge requests | all 40 and 6 stored; cursors at the newest `updated_at` read | N/A |
| First run, nothing open | empty answers | cursors at the run's start less 5 minutes | N/A |
| Assignee | id 17 on `https://gitlab.example.com`, name and username | `gitlab:gitlab.example.com:17` with display name and username | N/A |
| Server on a port | id 17 on `https://gitlab.example.com:8443` | `gitlab:gitlab.example.com:8443:17`, parsed at the last `:` | N/A |
| Non-numeric id | `"id": "x17"` or `true` | kept with name and username, no handle | N/A |
| Username changed | member 17's username differs | replaced; handle unchanged | N/A |
| Two assignees | ids 17 and 23 | both on the issue | N/A |
| Milestone | title and `due_date` | both kept; no due date is none | N/A |
| Issue closed | `state=closed` since the cursor | recorded closed with `closed_at`; out of the open set | N/A |
| Seen first as merged | a merge request merged before it was ever read | recorded merged; counted by `23g` in its window | N/A |
| Prune boundary | closed exactly 30 days before the run's start | kept; one second earlier, dropped | N/A |
| Locked merge request | `state=locked` | kept, raw state, open set | N/A |
| Older GitLab | `work_in_progress: true`, no `draft` | draft | N/A |
| Updated mid-walk | an issue on page 1 updated while page 2 is asked | read again later in the walk; none skipped | N/A |
| Links | related issues 12 (this project), 7 (another), one with no `project_id` | issue 12 only | N/A |
| Link cap | 600 merge requests need links | 500 linked; cursor at the 500th; the rest next run | N/A |
| Confidential issue | `confidential: true` | title `confidential issue`, no labels | N/A |
| Merge request for it | linked only to that issue | title replaced, no branch | N/A |
| Deleted issue | stored open, missing from the daily open check | recorded `gone`; out of the open set | N/A |
| Members cut | budget ends on page 2 of members | member rows unchanged; section `unread` with the reason, last complete read kept; failure names members | retryable |
| Bot member | a member with `bot: true`; another with no `bot` key | first marked bot; second *not stated* | N/A |
| Last read | issues read completely at 08:00, then a run cut short | section `unread`, last complete read 08:00 | N/A |
| Members hidden | 403 on members | section `unread` with reason; rows and last complete read kept; not a failure | N/A |
| Issues off | 404 on the issues list | section `unread`; commits and merge requests read; commit coverage claimed | N/A |
| Throttled | 429 on the merge-request list | what was read applied; run stops with the hint | retryable |
| Crash before cursor save | rows written, cursor not saved | next run re-reads; rows replaced with themselves | N/A |
| Nothing changed | no item updated, none to prune | no write | N/A |
| First enrolment replaces the built-in | a watched project with only the built-in gitlab:<project id> | enrolment accepted, the built-in replaced | N/A |

</frozen-after-approval>

## Code Map

Re-verified against `ba0b747` on 2026-10-17.

- `pm_ai/connectors/gitlab/__init__.py:15-27` the cursor account, `:106` `MERGE_LAG_DAYS`, `:128` `PAGE_CAP`, `:256` `emits`, `:262-316` `_to_event`, `:385` `_asked_from`, `:422` `harvest`
- `pm_ai/connectors/gitlab/client.py:100` `NEXT_PAGE_HEADER`, `:103` `PER_PAGE`, `:118` `FETCH_BUDGET`, `:495-537` `commits_url` and `commits` -- the paging the new lists copy
- `pm_ai/domain/handles.py:77-83` `HANDLE_SYSTEMS`, closed; `:295` `usable_handle`
- `pm_ai/domain/harvest.py:62-90` `HarvestOutcome`; `:273-496` `HarvestResult`, whose `records` at `:314` is the precedent for `work`; the outcome rule at `:380-417`
- `pm_ai/storage/service.py:137-214` `_SCHEMA`, where the tables join (`requests` is the last table); `pm_ai/ports/__init__.py:707` `StoragePort`. Story 1s already creates `operational.db` at 0600 (`service.py:1445-1449`), so the new tables need nothing extra for file modes
- `pm_ai/app/wiring.py:3510` `HANDED_CALLS`, `:3502` `_asks_none`; `:490-502` the built-in `gitlab:<project id>` connector per watched project, named by `_builtin_instance` at `:1242` (returning at `:1246`); `pm_ai/app/pipelines.py:278-377` records, events, then `save_cursor` at `:377`
- `pm_ai/surfaces/cli/dispatch.py:1300` `_connector_add_gitlab`, which calls `assert_unconfigured` at `:1336`, defined at `pm_ai/core/connector_enrolment.py:330` -- where the one-per-scope check belongs
- `pm_ai/domain/events.py:87` `WorkItemPayload`, `:224` `UNTRUSTED_TEXT` -- not used (Design Notes)
- `_bmad-output/specs/spec-pm-ai/stories/8n-gitlab-commit-transport.md:25,30,41` -- amended

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/handles.py` -- the `gitlab` handle system `gitlab:<host>:<id>`: host lowercased, default port dropped, parsed at the last `:`, id ASCII digits written `str(int(id))` -- after the AD-34 amendment; the built-in connector instance name `gitlab:<project id>` (`app/wiring.py:1246`) is not a person handle and is not renamed
- [ ] `pm_ai/domain/work.py` -- new: member, issue and merge-request values, `ProjectWork`, the harvest's change, the I/O-free merge and prune
- [ ] `pm_ai/domain/harvest.py` -- `HarvestResult.work`; the outcome rule
- [ ] `pm_ai/storage/service.py`, `pm_ai/ports/__init__.py` -- the tables, keyed by `(scope, instance)`; `record_project_work` and `project_work` on the writer and the port -- after the AD-3 amendment
- [ ] `pm_ai/core/connector_enrolment.py` (`assert_unconfigured`, `:330`), called from `pm_ai/surfaces/cli/dispatch.py:1336` (`connector add gitlab`) -- a second enrolled GitLab row for a scope that has one is refused, naming the first; enrolling under the built-in's name `gitlab:<project id>` replaces it and is accepted -- one instance per project scope (AD-10, decision A)
- [ ] `pm_ai/app/wiring.py` -- `record_project_work` in `HANDED_CALLS`, `_asks_none`
- [ ] `pm_ai/connectors/gitlab/client.py` -- the members, issues, merge-requests and `related_issues` URLs and pages
- [ ] `pm_ai/connectors/gitlab/__init__.py` -- per-resource cursor reading the old form; filter paging; first run; state, prune, daily open check; links and their cap; confidential rules; the one stop rule; unread sections
- [ ] `pm_ai/app/pipelines.py` -- `record_project_work` after the events, before `save_cursor`
- [ ] `tests/connectors/test_gitlab_work.py`, `tests/core/test_gitlab_work.py`, `tests/slice/` (a harvest through `run_harvest`) -- one test per matrix row, recorded-answer transport, no network, request URLs asserted
- [ ] `tests/architecture/test_domain_invariants.py` -- `gitlab` in the handle systems; `record_project_work` and `project_work` on `StoragePort`
- [ ] `_bmad-output/specs/spec-pm-ai/stories/8n-gitlab-commit-transport.md` -- dated Change Log line: `8n:25` (only commits, one event type), `8n:30` (the cursor as one commit date) and `8n:41` (Never: no merge requests, events or issues) amended by `8t`

**Acceptance Criteria:**
- Given a project with an open issue assigned to two members and a merge request related to it, when `run_harvest` runs, then `project_work(scope)` returns both members with handles, display names and usernames, the issue with its milestone, due date and labels, the merge request linking it, and each section complete with this run's instant — and no stored row holds a description.
- Given a second harvest, when it runs, then the issues and merge-requests requests carry `updated_after` equal to their stored cursors and `sort=asc`.
- Given the change, when `uv run pytest -q` runs, then all pass.

## Spec Change Log

- **2026-10-16, created.** Carries the decisions "Member priorities" and "Team activity" (Andrei 2026-10-16) and the coordinator's merge-request and username additions: members, issues and merge requests read with `read_api`; a `gitlab:<user id>` handle with display name and username; current state kept, not the event log; ascending incremental walks with per-resource cursors; the whole open set on a first run; closed and merged items kept 30 days; confidential issues without text; no bodies; the shared budget and page caps.
- **2026-10-16, review:** moved from a Tier-1 file to Tier-2 tables keyed by project scope, with the reader `StoragePort.project_work(scope)` named; the write is one claimed step before `9a`'s save; `HarvestResult.work` and its outcome rule; paging by the filter so mid-walk updates are not skipped; first-run order, empty first run, first-seen-closed, `locked`, `work_in_progress`, prune basis and boundary, deleted items (daily open check); members replaced only on a complete walk; unread sections for 403/404 counted complete and kept out of the failure, coverage left to the commit walk; one stop rule for throttles and bounds; link paging, project filter and cap ordering; id canonicalisation; confidential merge requests; the project members path verified and the Ask First dropped; the 8n amendment task; untrusted-text sanitisation placed on `7e`; per-section last complete read, a bot flag (true, false or not stated) on members and people, and the members section's unread state for 403/404 and a cut walk, for `23g`. Depends on spine amendments: AD-3 — the Tier-2 list gains GitLab members, issues and merge requests keyed by project scope; AD-34 — the handle systems gain `gitlab`, a numeric user id, digits only.
- **2026-10-16, spine gate:** frozen block edited (recorded here): the Tier-2 tables are keyed by `(scope, instance)`, never the scope alone, and `gitlab_work_status` per scope, instance and section (AD-3); one GitLab instance per project scope for now — `connector add gitlab` refuses a second, naming the first (AD-10), with a task.
- **2026-10-16, spine gate:** frozen block edited (recorded here): the GitLab person handle is `gitlab:<host>:<numeric user id>` (Andrei 2026-10-16, AD-34): host lowercased, port kept only when not the scheme default, parsed at the last `:`, id ASCII digits; the assignee row restated and a port row added; the handles task names the form and says the instance name `gitlab:<project id>` is not renamed.
- **2026-10-17, frozen block amended by Andrei's decision A:** the one-per-scope refusal counts enrolled rows only; replacing the built-in connector is not a second instance. Matrix row added.
- **2026-10-17, Code Map re-verified against `ba0b747`:** moved anchors corrected (`HANDED_CALLS`, `_builtin_instance`, `_SCHEMA`, `HarvestResult` and its outcome rule); the enrolment-check anchors in `dispatch.py` and `connector_enrolment.py` and the 0600 note on `operational.db` added.

## Design Notes

**Tier 2, not a Tier-1 file.** A file replaced whole contradicts Tier 1's append-only promise (the reason `daily_dashboard.md` moved to Tier 3), and Tier 3 does not fit — the state is not rebuildable from Tier 1. Tier 2 holds what is durable and not derivable, takes a transaction for the merge, and needs no new scope-tree node. Staged proposals already keep project material in `operational.db`, so this is not a new kind of tenant there.

**Why not `WorkItemPayload` events.** One assignee, no milestone or labels, and the natural key keeps only an issue's first line; the dashboard needs current state.

**Links.** `related_issues` ("from its title, description, commit messages, comments, and discussions") covers both closing and mentioning, which `23g`'s "worked on lately" needs; `closes_issues` covers closing only. The description is read by GitLab, never by pm-ai.

**Coverage.** Unlike Graph (`33d`), where calendar and messages are both telemetry under one window, issues and merge requests are state, not telemetry; the commit window vouches for commits alone, so a hidden issues section never withholds it.

**Verified against GitLab's reference, 2026-10-16:** `read_api` "Grants read access to the API for the token's scope"; `GET /projects/:id/members/all` ("Lists all project members viewable by the authenticated user, including inherited members") with `id`, `username`, `name`, `state`; email visible only to group owners for enterprise users; issue `state`, `updated_after` ("on or after"), `order_by=updated_at`, `sort`, `confidential`; assignee `id`, `name`, `username`; milestone `title`, `due_date`; merge-request `draft`, `source_branch`, `updated_after`, `merged_at`, `closed_at`; `related_issues` and `closes_issues`; `per_page` maximum 100; keyset pagination for issues from GitLab 18.3 (why it is not used). **Assumed:** a `bot` field on member and assignee objects (the 2026-10-16 examples for `members/all` and the issue list show none; the merge-request reference documents `author.bot`), hence *not stated* as a value; that a turned-off issues feature answers 403 or 404; the `related_issues` answer's shape matching the issue list (with `project_id`); `work_in_progress` on older GitLab; a merge request author's `username` (the reference documents only `author.bot`).

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_gitlab_work.py tests/core/test_gitlab_work.py -q` -- expected: every matrix row passes, no network
- `uv run pytest -q` -- expected: all pass
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks (if no CLI):**
- On the field-test GitLab: one harvest fills the tables and the members request answers.
