---
title: 'Team Activity on the project dashboard'
type: 'feature'
created: '2026-10-16'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A project dashboard shows the day's meetings and message signals. It does not show what each person on the project is meant to be working on, or which of those tasks moved recently, even though the commits are in the project's event log and `8t` records the project's members, open issues and merge requests.

**Approach:** The project dashboard gains a third section, Team Activity (Andrei's decision of 2026-10-16). It lists each member with their open assigned issues, and marks an issue as worked on lately when commits reference it or a linked merge request moved, all read from the project's stored records. It also leaves a typed, empty slot under each issue that `7e` fills with meeting updates.

## Boundaries & Constraints

**Always:**
- **Prerequisite: the kernel amendment.** SPEC.md's constraint that workload telemetry never appears on project dashboards (`SPEC.md:245`) must be re-derived before this slice is built. The re-derivation follows Andrei's decision of 2026-10-16: per-member activity against that member's own tasks and priorities may appear on project pages, never as rankings or comparisons between people. If the line is not yet re-derived, this slice does not start.
- **The sources are stored, and project-scope only.** Team Activity reads two things, with no GitLab request and no model at render time:
  - `8t`'s record of members, issues and merge requests, through its one reader, `StoragePort.project_work(scope) -> ProjectWork` (`pm_ai/domain/work.py`). `8t` keeps it in the operational store, keyed by the project's scope, on the PM's machine.
  - The project's event log, for its `commit_pushed` entries.

  Nothing is read from the personal scope, the people scope or the personal store (AD-25). Both are read on the worker, never inside the bounded step, which `9b` keeps for the live calendar read alone.
- **What this slice relies on from `8t`'s record:**
  - members: a `gitlab:<numeric user id>` handle, a display name, a username and a state;
  - issues: iid, title, state, assignees, milestone title and due date, labels;
  - merge requests: iid, state, draft flag, `updated_at`, `merged_at`, and the iids of this project's issues they are linked to;
  - for each section (members, issues, merge requests): whether the last harvest could not read it, with its reason, and the instant of its last complete read;
  - for each member: `bot` as true, false or not stated.

  A confidential issue arrives titled `confidential issue` with no labels, and is shown so. Display names are shown and never compared; the handle is the identity. This slice resolves nobody and never joins a GitLab handle to a commit's `email:` author.
- **Worked on lately: three marks, each saying what it is.** An issue is marked when any of these holds, and every mark that holds is shown. Nothing totals the marks per member.
  - **`N commits`.** `commit_pushed` entries recorded in the commit window carry a bare reference to the issue in their message, and their commit time is no older than the 7-day look-back cap. The mark also shows the latest of those commits: its 8-character id in a code span, its time, and the first line of its message.
  - **`merge request !<iid> updated <time>`**, with `(draft)` when it is a draft. A linked open merge request whose `updated_at` falls in the merge-request window.
  - **`merge request !<iid> merged <time>`.** A linked merge request whose `merged_at` falls in the merge-request window.
- **Which commit is the latest.** The newest by recording time, then by usable commit time, then by id. Its label is the commit time; when that is unknown, implausible or later than the render, the label is the harvest time followed by `(harvested)`, dated by the dashboard's usual rule.
- **A bare reference.**
  - It is `#` followed by ASCII digits `0-9` only.
  - It has no letter, digit, `/`, `&` or `!` immediately before the `#`, and no digit or letter after the number.
  - A number with a leading zero, and `#0`, are not references.
  - A reference qualified with a project path is ignored, because it may name another project's issue.
- **The two windows.**
  - **The commit window** is Proactive Enablement's: it runs from the scope's last successful render (the `renders` row, `9b`), or covers the last 24 hours when no render is recorded, and is never longer than 7 days. The section's first line names it.
  - **The merge-request window** ends at the render and opens 265 minutes earlier than the commit window: one harvest cycle (240) plus its jitter (15) plus GitLab's fetch budget (10), `cadence.CYCLE`, `CYCLE_JITTER` and the GitLab client's `FETCH_BUDGET`. Merge requests have only GitLab's own times, and opening earlier keeps an update made just before a render and harvested just after it.
  - A merge-request time that is absent, unparseable, or more than five minutes after the render gives no mark and is counted in the footnote.
- **Never a ranking or a comparison.**
  - Members are ordered by display name (Unicode-normalised, case-folded), then username (empty when absent), then handle.
  - A member's issues are in priority order: milestone due date earliest first, then a milestone with no due date, then no milestone, then iid.
  - A mark never reorders anything.
  - No member-level total, rate, trend, score, or comparison with another member or another render is printed.
- **Who is listed, and how much.**
  - Members with at least one open assigned issue are listed, at most 15, each with at most 5 issues and a line counting the rest. A closing line counts the further members and says their issues are in GitLab.
  - Members with no open assigned issue are named in one line, at most 10 names and then a count. The line is omitted when the issues section is unread.
  - A member whose state is not active is listed only while issues are assigned, with the state shown.
  - An assignee who is no longer a member is listed by display name, marked as not a project member.
  - A member `8t` records with `bot` true is left out everywhere, including the no-issue line and the member count. A member whose `bot` is not stated is listed like any other.
- **Unassigned issues** form the last group: issues that are marked or carry rows in the slot, at most 5, and a count of the rest.
- **Unlinked work: one closing line, naming no author.** Each count is checked against the open issues in `8t`'s record, not against what is visible on the page:
  - commits in the window that reference no open issue, split into those with no reference and those referencing only a closed or unknown issue;
  - merge requests marked in the window that link no open issue.

  A commit referencing at least one open issue is linked.
- **Texts are cut, folded and escaped.** Issue titles, labels, milestone titles, display names and commit first lines have their line breaks folded as `23f` folds them, are cut to 100 characters before escaping, and are then escaped. Ids go in code spans. The escape leaves `*`, `_` and bare URLs as Markdown reads them, as it does everywhere on the dashboard.
- **Nothing is invented.**
  - No `8t` record: the section says no GitLab issues are recorded for this project, and still prints the unlinked-work line.
  - Members and issues both unread: one statement with both reasons, and no member list.
  - One section unread: that section's reason is stated, and the rest renders from what was read.
  - Stale data said as stale: when a section's last complete read is older than one harvest cycle plus its jitter (255 minutes, `cadence.CYCLE` and `CYCLE_JITTER`) at the render, one line names the section and the time it was last read completely. A section never read completely is said to be so.
  - A record or log that cannot be read at render: stated with its reason, as Proactive Enablement does. The other sections render and the command exits zero.
  - Footnote counts: log lines the decoder refuses or that cannot be placed in time, merge-request times that cannot be used, and commits recorded in the window but committed more than 7 days earlier.
- **The slot for meeting updates.** `render_project_dashboard` gains one keyword parameter, `issue_notes`, defaulting to an empty value. It holds already-rendered rows keyed by issue iid, plus closing lines. Team Activity places an issue's rows under that issue wherever it appears (under each assignee, or under Unassigned) and the closing lines after the unlinked-work line. It escapes nothing in them, because `7e` renders and escapes its own rows. A pure function exposes which issues the section places, so `7e` can count the rest. With the default, nothing is printed. `7e` owns what goes in the slot, its window and its lines.
- **AD-36:** pm-ai writes nothing to GitLab in this build, so no merge-request time can be pm-ai's own doing.
- **The page stays machine-local.** The project copy is gitignored and keeps its file mode (`1n`, `9b`). The privacy reasoning above depends on this.
- **The personal dashboard is unchanged.**
- **Amends `23d`:22** ("This render carries Time-Critical Activities … and Proactive Enablement …"): the project page now has three sections.

**Ask First:** Nothing.

**Never:**
- No GitLab request at render time; no model call.
- No ranking, leaderboard, per-person total, or comparison between people.
- Nothing from the personal or people scope.
- No change to `8t`'s record or to what the commit harvest writes.
- No meeting updates rendered by this slice; `7e` renders them in the slot.
- No branch names read: commits carry none (see the Change Log).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Ordinary morning | issue 41 referenced by 3 commits since the last render; issue 52 with a linked open MR updated last evening | 41: `3 commits` and the latest; 52: `merge request !<iid> updated <time>` | N/A |
| MR merged | linked MR merged this morning | `merge request !<iid> merged <time>` | N/A |
| All three | commits, an updated MR and a merged MR | all three marks | N/A |
| MR updated just before the render | updated 06:50, render 07:00, harvested 08:00 | marked on the next page | N/A |
| Missed harvest | laptop asleep across two cycles | an MR updated only in the gap can miss its mark; stated limit | N/A |
| Unusable MR time | `updated_at` absent, unparseable, or an hour after the render | no mark; counted in the footnote | N/A |
| Back-filled commits | first harvest records commits from 3 weeks ago | not marked; counted in the footnote | N/A |
| Latest commit, unusable time | commit time `unknown` | label `HH:MM (harvested)` | N/A |
| Priority order | due in 1 week, in 3 weeks, milestone without date, no milestone | in that order, then iid; marks do not reorder | N/A |
| Member order | names with case and accents differing; one without username | normalised, case-folded name, then username (empty), then handle | N/A |
| No open issue | three members with none | one line naming them | N/A |
| Many without issues | 40 inherited members with none | 10 names and "30 more" | N/A |
| Issues unread | issues section unread | reason stated; no "no open issue" line | N/A |
| Members and issues unread | both | one statement with both reasons; no list | N/A |
| Bot member | `bot` true, no issues | not listed and not counted | N/A |
| Bot not stated | `bot` not stated | listed like any member | N/A |
| Stale section | issues last read completely 9 hours before the render | one line: the issues are as last read at that time | N/A |
| Fresh sections | every section read completely within 255 minutes | no staleness line | N/A |
| Inactive member | blocked, 2 issues | listed with the state | N/A |
| Former member | assignee not among members | listed, marked not a project member | N/A |
| Over the caps | 18 members with issues; one with 9 | 15 and a closing count; 5 and "4 more open issues" | N/A |
| Unassigned | 9 quiet and 6 marked | 5 marked; "1 more marked" and "9 more open unassigned issues" | N/A |
| Unlinked | 4 commits with no reference; 1 referencing closed #12 only; 1 referencing #12 and open #41 | 4 unreferenced, 1 closed-only; the last is linked to 41 | N/A |
| Hidden by a cap | commit referencing an open issue hidden by the 5-issue cap | linked; not counted as unlinked | N/A |
| Not references | `&#123;`, `abc#5`, `!42`, `#5a`, `#041`, `#0`, `other/proj#5`, `#٣` | none counted | N/A |
| Colour-like | `#333` in a message | read as issue 333; linked only if 333 is open | stated limit |
| Confidential | title `confidential issue` | shown so, with its marks | N/A |
| Long title with a break | 200 characters with `\n` | folded, cut to 100, escaped | N/A |
| Slot empty | default `issue_notes` | nothing printed for notes | N/A |
| Slot filled | rows for issue 41, assigned to two members | rows under 41 for both | N/A |
| Slot for an unlisted issue | rows for a closed issue | not placed here; `7e` counts it using the placed-issues function `placed_issue_numbers` | N/A |
| No record | no `8t` record | "No GitLab issues are recorded for this project" and the unlinked line | N/A |
| Record unreadable | malformed record or corrupt log | stated with the reason; other sections render; exit zero | stated, never raised |
| Personal scope | the reader asked for the personal scope | refused | raises |
| Personal dashboard | any | four sections, unchanged | N/A |
| Same inputs twice | — | byte-identical | N/A |

</frozen-after-approval>

## Code Map

Verified against `a1182f8` on 2026-10-16. `8t` (`StoragePort.project_work(scope) -> ProjectWork`, `pm_ai/domain/work.py`) and `7e` (`pm_ai/core/member_updates.py`) are specified, not built. `8t` lands before this slice; `7e` lands after it and fills the slot.

- `_bmad-output/specs/spec-pm-ai/stories/8t-gitlab-issues-harvest.md` -- the record of members, issues and merge requests in the operational store, its one reader, the `bot` flag, each section's unread state and last complete read; the `gitlab:` handle; confidential issues; closed and merged items kept 30 days.
- `_bmad-output/specs/spec-pm-ai/stories/7e-transcripts-update-member-priorities.md:33` -- `7e` renders its rows in this slot and counts updates on issues not shown.
- `pm_ai/connectors/gitlab/__init__.py:3`, `:22-27` -- commits are read on the default branch only. `:311` -- `CommitPayload(sha=sha, message=message)`, with no branch.
- `pm_ai/connectors/gitlab/client.py:118` -- `FETCH_BUDGET`, 10 minutes.
- `pm_ai/core/cadence.py:71`, `:74` -- `CYCLE` and `CYCLE_JITTER`. `:175`, `:178`, `:244` -- the 24-hour fallback, the 7-day cap, `look_back`.
- `pm_ai/core/rendering.py` --
  - `:213-234` `PROJECT_HEADINGS` and its docstring, which says two sections;
  - `:359-412` `render_project_dashboard`;
  - `:653-780` `_proactive_enablement`, the pattern for the failure branch, the window, the exclusion counts and the cap;
  - `:866` `_window_words`, `:914` `_window_notes`, `:1007` `_ingested_at`, `:1098` `escape`, `:1144` `code`, `:1223` `_local`.
- `pm_ai/app/pipelines.py` --
  - `:1602` `prepare_dashboard`, which reads the records on the worker beside the log read. They are not part of the bounded step, which `9b` keeps for the calendar read alone.
  - `:1634` `since`;
  - `:1666` the project renderer call;
  - `:1724` `_event_log`, the pattern for a failed read becoming a stated value.
- `tests/core/test_project_rendering.py` -- `:242` the parameter list, which grows by `issue_notes`; `:250` the no-goal annotation test; `:365`, `:380`, `:518`, `:608` the heading tests.
- `tests/core/mypy_fixture_project_render_valid.py` -- must still type-check.
- `tests/architecture/test_domain_invariants.py:227` -- the AD-25 test, which must still pass.
- `tests/slice/test_dashboard_slice.py:1073` -- the project page's headings, end to end.
- `_bmad-output/specs/spec-pm-ai/stories/23d-project-render-scope-wall.md:22` -- the sentence amended.

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/team_activity.py` -- new and pure: the bare-reference finder; the grouping of record and commit entries into members and marked issues; the placed-issues function `placed_issue_numbers`; the `IssueNotes` slot type and its empty default -- testable without Markdown
- [ ] `pm_ai/core/rendering.py` -- `TEAM_ACTIVITY` third in `PROJECT_HEADINGS`, with its docstring updated; the section renderer; the `issue_notes` keyword -- the section's contract
- [ ] `pm_ai/app/pipelines.py` -- for a project scope only, read `8t`'s record through `StoragePort.project_work` on the worker, outside the bounded step, with a failed read turned into a stated value; pass it in -- no GitLab call, no personal read
- [ ] `tests/core/` -- one test per matrix row; a table of the finder's matches and non-matches; the parameter-list and heading tests updated; the mypy fixture -- the matrix is the contract
- [ ] `tests/slice/test_dashboard_slice.py` -- end to end: a written `8t` record and commit lines produce the section; the personal page is unchanged; reading `8t`'s record for the personal or people scope raises; no request reaches the GitLab transport during a render -- what the renderer cannot see
- [ ] `_bmad-output/specs/spec-pm-ai/stories/23d-project-render-scope-wall.md` -- a dated Change Log line naming `:22` as amended -- the amendment is on record

**Acceptance Criteria:**
- Given a project with an `8t` record and commit lines, when the 07:00 render runs, then:
  - the project page has Team Activity third;
  - no member-level total appears;
  - no GitLab request was made;
  - nothing outside the project scope was read, asserted against the doubles.
- Given the default `issue_notes`, when the project page renders, then its bytes equal the render without the parameter.
- Given the change, when `uv run pytest` runs, then everything passes with the skip count unchanged from the baseline at the slice's start.

## Spec Change Log

- **2026-10-16, created.**
  - **Source:** Andrei's decisions of 2026-10-16: team activity, which replaces commits-by-author; the kernel amendment allowing per-member activity against tasks and priorities on project pages, never rankings or comparisons; member priorities from GitLab issues (`8t`) and meetings (`7e`).
  - **Built on `8t`'s frozen block:** members, issues and merge requests, `gitlab:` handles with names and usernames for display, and confidential titles.
- **2026-10-16, review.**
  - **New decisions (Andrei's of 2026-10-16):**
    - meeting updates move to `7e`, which renders them in a typed slot left here (`issue_notes`, empty by default), with matrix rows, reader tasks and the unmatched-names line removed;
    - a merge request merged in the window marks its issue;
    - branch names are not read, because commits carry none and merge requests cover unmerged work — a narrowing of "`#iid` in message or branch";
    - the text is written against "`8t`'s record of members, issues and merge requests" and `8t`'s reader, wherever it stores it.
  - **Edge-case findings applied:**
    - members with no issue moved into one capped line, suppressed when issues are unread;
    - one statement when both members and issues are unread;
    - the sort key normalised;
    - Unassigned capped;
    - unlinked work checked against the open set, with a commit referencing any open issue counted as linked;
    - the latest commit and its label defined;
    - commits older than 7 days by commit time not marked, and counted;
    - the reference rule tightened to ASCII digits, no leading zero and no `#0`;
    - the merge-request window also subtracts the fetch budget, and a missed cycle is a stated limit;
    - unusable merge-request times counted;
    - cut, fold and escape order defined.
  - **Coherence findings applied:**
    - the SPEC.md:245 re-derive is a prerequisite gate;
    - records are read on the worker, not in the bounded step;
    - AD-36 stated;
    - `23d`:22 named, with a task;
    - "Team Activity" used throughout;
    - the Intent trimmed;
    - ACs made system-level.
  - **Adversarial findings applied:**
    - the parameter default stated;
    - the mypy fixture and the headings docstring named;
    - "no GitLab request" instead of "no transport request" (the calendar read is live);
    - the personal-scope read test;
    - the machine-local reasoning stated.
  - **Rejected:**
    - Treating `#333` as a colour: a three-digit colour cannot be told apart from issue 333, so it is a stated limit.
    - Opening the merge-request window at the last successful harvest: the render reads no harvest state, so a missed cycle is a stated limit instead.
    - Showing `7e`'s match kind here: `7e` now renders its rows, and its rows carry the marker.
- **2026-10-16, review (cross-spec consistency).** `8t`'s reader named as `StoragePort.project_work(scope) -> ProjectWork` (`pm_ai/domain/work.py`) in the operational store, replacing `pm_ai/core/gitlab_work.py`; members `8t` marks as bots excluded, those not stated listed (the earlier "Rejected: bots" entry withdrawn); each section's last complete read used to say when what is shown is older than one harvest cycle plus jitter (the earlier stale-data rejection withdrawn); the non-calendar reads stated as outside the bounded step.

## Design Notes

**Members without issues on one line.** "Nothing assigned" is a fact the PM can act on. A project that inherits a group's members can have dozens, so they are named on one capped line instead of in the list. Members `8t` marks as bots (a project access-token bot, for one) are left out, since they are not people with tasks; a member whose flag is not stated is listed rather than guessed at by name.

**Two windows.** Commits carry pm-ai's own recording time, so they use the render window exactly (field-test correction 24). The commit-time cap keeps a first harvest's back-fill from marking month-old work as recent. Merge requests carry only GitLab's times, so their window opens one cycle, jitter and fetch budget earlier. A merge request can stay marked on two pages in a row, which is acceptable because the mark describes a state, not news. A merge request updated only during a cycle the daemon missed can lose its mark, and that is a stated limit.

**Ordering without ranking.** Members are alphabetical and issues follow milestones. Neither depends on activity, so the page cannot be read as a league table.

**The slot.** `7e` lands after this slice. Leaving a typed, empty parameter lets `23g` ship and be reviewed alone. `7e` adds rows without touching this section's layout, using the placed-issues function `placed_issue_numbers` to count what it could not place.

## Verification

**Commands:**
- `uv run pytest tests/core tests/slice/test_dashboard_slice.py -q` -- expected: every matrix row passes
- `uv run pytest -q` -- expected: all pass; skip count unchanged from the baseline
- `uv run mypy` -- expected: no errors, including the project-render fixture
- `uv run lint-imports` -- expected: all contracts kept
