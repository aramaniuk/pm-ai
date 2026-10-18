---
title: 'First runs reach back far enough'
type: 'feature'
created: '2026-10-16'
status: 'done'
baseline_commit: '9e5383fe4c6ec023d243bfd09d9df39e72d6b6d4'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A new connector's first harvest sees too little, and each connector's first run disagrees with what follows. For Microsoft Graph, setup and `pm-ai connector add graph` ask "First-run reach-back in minutes (at least {window}, the window)" with no default, so a person who takes the smallest window (240 minutes) and the smallest reach-back it allows gets a first harvest that records only four hours back, so recent meetings, messages and transcripts are missing from what pm-ai has stored. For GitLab, the first run asks from 7 days back (`FIRST_RUN_REACH_BACK`) while every later run asks from 30 days before its cursor (`MERGE_LAG_DAYS`), so the second run quietly back-fills about three weeks: 38 commits on the first run and 189 on the second (finding 19 in `_bmad-output/implementation-artifacts/field-test-2026-10-15.md`, which lands with PR #71). And a GitLab host typed without `https://` is refused, though https is the only scheme pm-ai would accept anyway.

**Approach:** The Graph reach-back question offers five days (7200 minutes) as what Enter keeps; GitLab's first run asks from as far back as every later run does, 30 days; a GitLab host typed with no scheme is taken as `https://` and the person is told what was read. Rows already on disk are not rewritten.

## Boundaries & Constraints

**Always:**
- **Graph: Enter keeps five days.** The reach-back question, asked by `connector add graph` and therefore by setup's step 4 through the same code (4p), says Enter keeps 7200 minutes (five days). When the window typed before it is wider than five days, Enter keeps the window instead, since the reach-back may never be shorter than the window; the question states the number Enter keeps either way. A typed number is judged exactly as today. The harvest window keeps no default.
- **GitLab: the first run reaches back 30 days**, the same span every later run asks from before its cursor, spelled once: `FIRST_RUN_REACH_BACK` is derived from `MERGE_LAG_DAYS`, so the two cannot drift apart again. A row's own `first_run_reach_back_minutes`, written by hand, still wins. The cursor is the date of the newest commit a complete walk saw, not the time of the run, so the second run asks from 30 days before that date; what it can add beyond the first run's range is the span between that date and the first run's time — usually a few days on an active project, rather than the three weeks a 7-day first run left.
- **GitLab host: no scheme typed means https.** An answer that neither contains `://` nor begins with a scheme followed by `:/` (so `http:/host` is not rescued) is read as `https://` followed by the answer, then judged by today's rules (no query, fragment, user info, trailing slash or `/api/v4`). An accepted answer is saved as the full `https://` address, and one line saying "taken as https://…" is printed straight after the question, so it is on screen above anything the connection check says later. A refused answer quotes what was typed, adding the address it was read as when the scheme was added. An answer that names a scheme is judged as typed, so `http://…` is still refused, naming https as the only scheme the token is sent over.
- **Rows on disk are read, never rewritten.** Every Graph row already carries the reach-back it was enrolled with, and it stays; it still matters after the first run, because every harvest asks about transcripts of meetings within it and a channel or chat listed later starts from it. A person who wants five days edits the row; the next job reads it, as every job reads rows afresh (4r). A GitLab instance that has already harvested has a cursor, so the 30 days changes nothing for it; one enrolled but never harvested reaches back 30 days at its first run.
- **The existing bounds still hold a five-day first run, and nothing new is added:** the calendar asks one span (under the 30-day `MAX_SERVABLE_SPAN`); each channel or chat walk stops at the 200-page cap (fifty messages a page) or the ten-minute fetch budget, and says so as a stated loss; a transcript listing stops once it reaches transcripts older than the reach-back's start; GitLab walks stop at their 500-page cap (100 commits a page) or ten-minute budget. What those bounds cut is reported as today, never silently dropped.
- **Transcripts keep the whole five days** (Andrei, 2026-10-16): there is no separate, shorter transcript look-back. Every Graph harvest therefore asks again about every ended meeting of the last five days that has no transcript yet and no closing answer, as `33e`'s frozen rule ("tried again while in the reach-back") says, and that rule is unchanged. The slice counts the Graph requests transcript acquisition makes per harvest for a stated number of such meetings, on the recorded Graph transport the transcript tests already use, and records the count; it does not change which meetings are asked. There is no per-harvest ceiling on these requests (Andrei, 2026-10-16): acquisition spends the Graph client's ten-minute fetch budget like every Graph walk, and a meeting not reached in one run is asked at the next.

**Ask First:** Any default for the harvest window. Accepting a host without a scheme when it is read from a hand-written row rather than typed.

**Never:**
- No change to which meetings, messages or commits a run asks for once it has a cursor, to any page cap or budget, or to the windows' minimum and ordering rules.
- No migration that rewrites a row's reach-back, and no new question in `connector add gitlab`.
- No `http://` accepted, and no scheme guessed for an answer that names one.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Enter at the window | Enter | asked again: the window has no default | re-asked |
| Enter at the reach-back | window `240`, Enter | row saved with `first_run_reach_back_minutes: 7200`; the question named 7200 | N/A |
| A typed reach-back | window `240`, `900` | `900` saved, as today | N/A |
| Typed below the window | window `480`, `60` | asked again, saying it must be at least 480, as today | re-asked |
| Window wider than five days | window `10080`, Enter | `10080` saved; the question named 10080 as what Enter keeps | N/A |
| Through setup | step 4, `y`, …, Enter at the reach-back | the same 7200, saved by the same path | N/A |
| GitLab first run | no cursor, no row key | asks from 30 days before now | N/A |
| GitLab second run | first run at T; newest commit it saw dated D | asks from D − 30 days; the range it adds beyond the first run's is D − 30 days up to T − 30 days | N/A |
| GitLab short key by hand | `first_run_reach_back_minutes: 1440` | first run asks from one day back; the second still asks from 30 days before its cursor, so it back-fills up to 29 days, as a hand-set key always did | N/A |
| Host without scheme | `git.internal` | saved as `https://git.internal`; "taken as https://git.internal" printed | N/A |
| Host with path, no scheme | `git.internal/gitlab` | saved as `https://git.internal/gitlab` | N/A |
| Host with a port, no scheme | `git.internal:8443` | saved as `https://git.internal:8443` | N/A |
| Host with http | `http://git.internal` | asked again: the token is only sent over https | re-asked |
| Mistyped scheme | `http:/git.internal` | judged as typed, not prefixed; asked again with the scheme reason | re-asked |
| Host without scheme, bad otherwise | `git.internal/api/v4` | asked again with today's reason, quoting `git.internal/api/v4` and naming `https://git.internal/api/v4` as how it was read; no "taken as" line | re-asked |
| Enter at the host | empty | the default host, as today | N/A |
| Existing Graph row | `first_run_reach_back_minutes: 240` on disk | unchanged; transcripts and newly listed chats still use 240 | N/A |

</frozen-after-approval>

## Code Map

Re-verified against `ba0b747` on 2026-10-17.

- `pm_ai/surfaces/cli/dispatch.py:1190-1297` -- `_connector_add_graph`; the window question (`:1250-1253`, no default) and the reach-back question (`:1254-1257`), both through `_ask_minutes`; the docstring's step 3 names "no default"
- `pm_ai/surfaces/cli/dispatch.py:1462-1489` -- `_ask_minutes`: blank is refused ("this has no default"), and its docstring says neither width has a default; gains an optional default, which stays optional because a second caller, the drop duration (`:2883`), keeps no default
- `pm_ai/surfaces/cli/dispatch.py:2267-2305` -- setup's `_setup_connector`, which runs `_connector_add_graph` itself (4p); nothing to change there
- `pm_ai/app/wiring.py:1524`, `:1576-1582`, `:1611-1655` -- `GraphEnrolment`: `minimum_window_minutes` (the pattern for the default it will also expose), `refusal(width=, reach_back=)`
- `pm_ai/connectors/graph/calendar.py:571-602` -- `WindowPolicy.first_run_reach_back` and the "at least the window" refusal; `:624` the first run's start; `:146` `MAX_SERVABLE_SPAN`
- `pm_ai/connectors/graph/client.py:155`, `:181` -- `PAGE_CAP = 200`, `FETCH_BUDGET = 10 minutes`; `pm_ai/connectors/graph/channels.py:520-530`, `chats.py:524-535` -- the stated loss when either stops a walk; both walks start from the reach-back on a first run (`channels.py:376`, `chats.py:291`); `channels.py:586-610` `_cut_short`
- `pm_ai/app/pipelines.py:725-765` -- transcript acquisition after every calendar harvest, from `now - first_run_reach_back`, every run; `pm_ai/core/transcript_acquisition.py:130-176` the selection; `pm_ai/domain/transcripts.py:293-299` `closes`: `absent` and `skipped` never close
- `tests/connectors/test_graph_transcripts.py:245` -- the recorded-transport client the request count is taken on
- `pm_ai/connectors/gitlab/__init__.py:15-21`, `:652-664` -- the cursor is the newest commit date seen on a complete walk (`_advanced`); `:100-126` `REACH_BACK_KEY`, `MERGE_LAG_DAYS = 30`, `FIRST_RUN_REACH_BACK = timedelta(days=7)`; `:221`, `:395` where it applies; `:128` `PAGE_CAP = 500`; `pm_ai/connectors/gitlab/client.py:103`, `:118` `PER_PAGE = 100`, `FETCH_BUDGET`
- `pm_ai/connectors/gitlab/client.py:155-187` -- `host_refusal`: "its scheme is 'missing'"
- `pm_ai/app/wiring.py:2110-2135` -- `GitLabEnrolment.refusal(host=)`, adding the `/api/v4` rule; its sentence quotes `{host!r}` (`:2131`); `:2247` the row's settings hold only `host`, so enrolled GitLab rows carry no reach-back key
- `pm_ai/surfaces/cli/dispatch.py:1348-1354` -- the host question
- `pm_ai/app/wiring.py:1104-1142` -- `_gitlab_connector` reading the row's optional reach-back
- `tests/connectors/test_coverage_honesty.py:575-583` -- `test_a_first_run_reaches_back_seven_days_or_the_rows_override` asserts `NOW - timedelta(days=7)`
- `tests/slice/test_connector_add_graph.py:280-305` -- the happy path asserting four prompts and the saved reach-back; `tests/slice/test_connector_add_gitlab.py:250-262`; `tests/connectors/test_gitlab_transport.py:664-717`; `tests/slice/test_setup_connector_step.py`

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/connectors/graph/calendar.py` -- one constant for the offered reach-back, 7200 minutes, beside `WindowPolicy` -- spelled once, where the rule it must satisfy lives
- [x] `pm_ai/app/wiring.py` -- `GraphEnrolment` exposes the offered reach-back for a given window (the larger of the constant and the window); `GitLabEnrolment` gains the typed-host reading (the rule above) used before `refusal(host=)`, and its refusal quotes the typed text plus the address it was read as -- the composition root reaches `connectors`, the CLI does not
- [x] `pm_ai/surfaces/cli/dispatch.py` -- `_ask_minutes` takes an optional default named in the prompt; the reach-back question passes the offered value; the host question reads the typed answer through the new reading, prints "taken as …" only for an accepted answer whose scheme was added, and saves the full address; docstrings updated -- the questions
- [x] `pm_ai/connectors/gitlab/__init__.py` -- `FIRST_RUN_REACH_BACK = timedelta(days=MERGE_LAG_DAYS)`, its docstring saying why they are one span and what the second run can still add -- consistency
- [x] `tests/slice/test_connector_add_graph.py`, `tests/slice/test_setup_connector_step.py` -- every Graph row of the matrix -- the Graph rows
- [x] `tests/slice/test_connector_add_gitlab.py` -- each host row of the matrix, the saved row asserted on disk -- the host rows
- [x] `tests/connectors/test_gitlab_transport.py`, `tests/connectors/test_coverage_honesty.py` -- first run asks from 30 days back (the seven-day assertion at `test_coverage_honesty.py:575-583` changes); a row key still wins; on a fixture whose newest commit date is pinned, the second run asks from that date minus 30 days -- finding 19
- [x] `tests/connectors/test_graph_transcripts.py` -- count the requests one acquisition makes, on the recorded Graph transport, for a stated number of ended meetings with no transcript inside a 7200-minute reach-back; record the count and the meeting count in this spec's Design Notes -- the stated cost, measured
- [x] `_bmad-output/specs/spec-pm-ai/stories/8l-connector-add-graph.md` -- frozen text edited and a dated Spec Change Log line added, when this slice is built: the reach-back question now offers 7200 minutes, or the window when wider, as what Enter keeps (Andrei, 2026-10-16); the window keeps no default -- its frozen text and Ask First say "no default"
- [x] `_bmad-output/specs/spec-pm-ai/stories/8n-gitlab-commit-transport.md` -- frozen text edited and a dated Spec Change Log line added, when this slice is built: a first run reaches back 30 days (`MERGE_LAG_DAYS`), not seven; a typed host with no scheme is read as https at enrolment, the row reader unchanged -- its frozen text says seven days
- [x] `_bmad-output/specs/spec-pm-ai/stories/8o-connector-add-gitlab.md` -- frozen text edited and a dated Spec Change Log line added, when this slice is built: the first-run constant it names is now 30 days; a host typed without a scheme is taken as https and said so -- its frozen text says "the seven-day constant" and "must be an https address"
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark resolved the field test's GitLab first-run entry (finding 19) and the host part of its small-gaps entry -- what this closes

**Acceptance Criteria:**
- Given a clean machine, when `connector add graph` is answered with a 240-minute window and Enter at the reach-back, and the daemon then harvests the new connector once, then the first harvest's calendar request, read from its recorded URL, starts five days before the run and ends 240 minutes after it (the window), and the meetings that harvest records include ones from those five days.
- Given a GitLab connector enrolled through `connector add gitlab` with a scheme-less host, when the daemon harvests it twice against a fake GitLab, then every request went to the `https://` address and the second run's range started at the newest commit date 
of the first minus 30 days.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged from the baseline at the slice's start.

## Spec Change Log

- **2026-10-16, created.** Carries: Enter keeps 7200 minutes at the Graph reach-back question, or the window when it is wider, in `connector add graph` and therefore setup; the window keeps no default; GitLab's first run derived from `MERGE_LAG_DAYS` (30 days), a hand-written row key still winning; a typed GitLab host with no `://` read as https and said so, a named scheme judged as typed, the row reader unchanged; no row on disk rewritten; existing page caps and budgets named as the bounds of a five-day first run; transcripts keep the full five-day span under `33e`'s unchanged per-run rule, with no separate look-back (Andrei, 2026-10-16), and the per-run request count measured and recorded.
- **2026-10-16, review:** the GitLab second-run claim corrected (the cursor is the newest commit's date, so the second run can add the span from that date to the first run; pinned on a fixture); a row for a hand-set short reach-back; the host prefix applied only to a bare answer (a mistyped scheme is judged as typed), "taken as" printed only for an accepted answer, a refusal quoting the typed text and how it was read; a row for Enter at the window; the transcript request count taken on the recorded Graph transport; `test_coverage_honesty.py`'s seven-day test added to the tasks; change-log tasks for 8l, 8n and 8o spelled out; the field-test timing quoted as reported; the four-hours claim tied to the 240-minute window; build order with 4s stated; repeated acceptance criteria replaced by system-level ones. Depends on no spine amendment.
- **2026-10-16, review (cross-spec):** the Change Log moved before Design Notes; tasks amending done specs say their frozen text is edited, with a dated Change Log line, when this slice is built.
- **2026-10-16, Andrei's decision on the request ceiling.** No per-harvest ceiling on Graph transcript requests; the existing fetch budget bounds the run and meetings not reached wait for the next. One sentence added to the transcripts bullet, since the slice left the point open.
- **2026-10-17, frozen block amended by Andrei's decision B:** since 23e the dashboard reads only the display day, so the Problem sentence and acceptance criterion 1 now speak of what the first harvest requests and records.
- **2026-10-17, Code Map re-verified** against `ba0b747`: `dispatch.py` lines moved by slice 4t, `_ask_minutes`'s second caller noted, the channel walk's first-run start and `_cut_short` added, the 33h horizon note added to Design Notes.
- **2026-10-18, built.** Tasks done as listed; the transcript request count recorded in Design Notes (60 requests for 20 untranscribed meetings). 8l, 8n and 8o carry their dated Change Log lines; deferred-work's finding-19 entry and the host part of the small-gaps entry are marked resolved.

## Design Notes

Why the default lives in the question and not in the row reader: every enrolled Graph row already holds a reach-back, because enrolment has always written one; a reader default would never apply. The question is where the five days have to be offered.

Transcripts: `33e` ties the transcript span to the same row value on purpose — every meeting a first run records must be inside it — and its frozen block says absent transcripts are asked again while in the reach-back. Five days of that is more requests per run than four hours; Andrei kept the five days for transcripts (2026-10-16), and how many requests that is per run is what the counting task records.

Measured (2026-10-18, `test_one_acquisition_over_five_days_asks_three_requests_per_untranscribed_meeting`, on `test_graph_transcripts.py`'s `FakeGraph` transport): for 20 ended one-off Teams meetings inside a 7200-minute reach-back, each with no transcript yet, one acquisition pass, called directly on the recorded fake transport, made 60 Graph requests — three per meeting: the event, the online-meeting lookup by join link, and the transcript listing (one page each). A meeting with a transcript adds its download; a series shares one lookup and one listing (`test_acceptance_a_series_is_looked_up_and_listed_once`). The count excludes the harvest's `calendarView` call and any channel or chat walk. Twenty is a stated count, not a measurement of any real calendar.

GitLab's second run (choice, review 2026-10-16): a 30-day first run cannot make the second run add nothing, because the cursor is the newest commit's date, not the run's time. The spec states the true bound — the second run asks from D − 30 days (D the newest commit's date), so what it adds beyond the first run's range is the range from D − 30 days up to T − 30 days (T the first run's time), a span T − D long — and the test pins the date on a fixture, rather than claiming no back-fill.

Host reading (choice, review 2026-10-16): only a bare answer is prefixed; anything that already looks like a scheme, mistyped or not, is judged as typed, so a typo is shown back rather than silently turned into a different address.

The field-test report gives the first harvest of 1 channel and 13 chats as about 12 seconds, without stating the reach-back it ran at; nothing was measured at five days.

Channel threads (33h, no behaviour change): the channel walk's 90-day thread horizon is measured back from the position, so on a first run with a five-day reach-back it sits about 95 days back; the walk may read a few more pages, still within the 200-page cap and the fetch budget.

Build order: 8s comes before 4s in wave 3. Both change `_connector_add_graph`, `_ask_minutes` and the prompt-count assertions in `tests/slice/test_connector_add_graph.py`; 4s re-baselines them after this slice.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_connector_add_graph.py tests/slice/test_connector_add_gitlab.py tests/slice/test_setup_connector_step.py tests/connectors/test_gitlab_transport.py tests/connectors/test_coverage_honesty.py tests/connectors/test_graph_transcripts.py -q` -- expected: all pass
- `uv run pytest -q` -- expected: all pass; the skip count unchanged from the slice's baseline
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

## Suggested Review Order

**Graph: Enter keeps five days at the reach-back question**

- The offer: five days or the window, whichever is wider, named in the prompt.
  [`dispatch.py:1264`](../../../../pm_ai/surfaces/cli/dispatch.py#L1264)

- Blank keeps the default only when a caller offers one; window and drop duration still refuse.
  [`dispatch.py:1479`](../../../../pm_ai/surfaces/cli/dispatch.py#L1479)

- The composition root computes the offer from the connector's constant.
  [`wiring.py:1586`](../../../../pm_ai/app/wiring.py#L1586)

- The constant, spelled once beside the rule it must satisfy.
  [`calendar.py:541`](../../../../pm_ai/connectors/graph/calendar.py#L541)

**GitLab: a typed host with no scheme is read as https**

- Only a bare answer is prefixed; host part lower-cased to match what is saved.
  [`wiring.py:2122`](../../../../pm_ai/app/wiring.py#L2122)

- Refusal judges the read address, quotes the typed text and how it was read.
  [`wiring.py:2156`](../../../../pm_ai/app/wiring.py#L2156)

- The check reads the host the same way before probing and saving.
  [`wiring.py:2232`](../../../../pm_ai/app/wiring.py#L2232)

- The question prints "taken as …" only when a scheme was added.
  [`dispatch.py:1364`](../../../../pm_ai/surfaces/cli/dispatch.py#L1364)

**GitLab: first run reaches back 30 days**

- Derived from `MERGE_LAG_DAYS`, so first and later runs cannot drift apart.
  [`__init__.py:124`](../../../../pm_ai/connectors/gitlab/__init__.py#L124)

**Tests**

- Acceptance 1: enrol with Enter, harvest once, calendar request spans five days back.
  [`test_connector_add_graph.py:823`](../../../../tests/slice/test_connector_add_graph.py#L823)

- Acceptance 2: bare host enrolled, harvested twice, https and D − 30 days.
  [`test_connector_add_gitlab.py:743`](../../../../tests/slice/test_connector_add_gitlab.py#L743)

- Host reading pinned directly across bare, port, path, case and scheme shapes.
  [`test_connector_add_gitlab.py:663`](../../../../tests/slice/test_connector_add_gitlab.py#L663)

- First run 30 days, second from newest commit date, hand-set key still wins.
  [`test_coverage_honesty.py:577`](../../../../tests/connectors/test_coverage_honesty.py#L577)

- The measured transcript cost: three requests per untranscribed meeting.
  [`test_graph_transcripts.py:1287`](../../../../tests/connectors/test_graph_transcripts.py#L1287)
