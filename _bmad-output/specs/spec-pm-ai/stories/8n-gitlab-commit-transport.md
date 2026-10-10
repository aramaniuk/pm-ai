---
title: 'The GitLab connector reads commits from a real GitLab'
type: 'feature'
created: '2026-10-09'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The GitLab connector cannot reach GitLab. Everything around the wire is real — paging, the cursor, when coverage may be claimed, what a commit becomes — but the wire is a placeholder: the health check reads a fixed sentence without opening a socket, and a harvest walks an in-memory list that is empty on every real machine. So a connector holding a credential reports "transport is still a stub" today, and the first harvest that runs would report "GitLab answered and there was nothing here" and record coverage over a list nobody fetched — configured, enabled, holding a credential and harvesting nothing, the state story 8's notes call the worst of the three.

Two more faults hid behind the stub: the connector names itself after the GitLab project path, so one enrolled as `gitlab:alpha` for `acme/web` calls itself `gitlab:acme/web` and every cursor save is refused as naming another connector; and it stamps that path into each commit's reference, which the grammar refuses when the path holds a slash.

**Approach:** A real, read-only HTTP transport for the one thing the connector already maps — a project's commits — with paging, throttling, the wall-clock budget and the coverage rules it already has. It lives in a new `pm_ai/connectors/gitlab/` package, uses the standard library like the Graph client, and speaks HTTP nowhere else. A connector with no credential opens no socket and says so.

## Boundaries & Constraints

**Always:**
- **Only commits are read, from the default branch**, through GitLab's list-commits call (https://docs.gitlab.com/api/commits/), with no branch and no all-branches option — work reaches the default branch by merge. The connector emits one event type and this slice adds none.
- **The project path comes from the row's own `project` setting**, never the instance name, and is sent URL-encoded (`group/project` as `group%2Fproject`, https://docs.gitlab.com/api/rest/ "Namespaced paths"). It must be non-blank with no leading slash, `?`, `#` or space; the first-run reach-back override is checked as Graph's minute settings are (one minute to ten years). A bad value leaves the connector unbuilt with the key named, never silently dropped.
- **The host is a row setting, `host`, defaulting to `https://gitlab.com`** — the one defaulted key, as `tenant` is for Graph. It must be https and may carry a path (`https://git.internal/gitlab` is a supported self-managed layout), with no query, fragment, user info or trailing slash; the API lives under it. The token travels in the request header GitLab documents for personal access tokens (https://docs.gitlab.com/api/rest/authentication/), to that host and no other: a redirect is refused, not followed. A token that cannot be a header value (a newline, non-ASCII) fails without a socket, naming the cause.
- **The connector's name is the enrolled instance name**, handed in by composition; the built-in for a watched project keeps `gitlab:<project id>`. A commit's reference is `gitlab:<pm-ai project id>:commit:<sha>` — the project scope, as the grammar requires, never the provider path.
- **Pages follow GitLab's own next-page header** (https://docs.gitlab.com/api/rest/ "Pagination"), 100 rows a page, under the existing page cap and ten-minute budget, with a thirty-second socket timeout on each request as the Graph client has. A missing next-page value is the last page; a non-numeric one, a next page that does not advance, or a 200 whose body is not a list is a non-retryable failure that keeps what was walked.
- **The cursor is the newest plausible commit date seen on a complete walk, never past now.** The next run asks from **thirty days before the cursor** (`MERGE_LAG_DAYS`, a constant): a commit made on a branch reaches the default branch only when merged, and asking from the cursor alone would miss it. What was already recorded is dropped as a duplicate by its natural key; a commit merged more than thirty days after it was made is a stated loss. **A walk that did not finish moves the cursor nothing**: GitLab lists newest first, so the pages not walked are the older ones. What was walked keeps its events and coverage.
- **A first run reaches back seven days** (`FIRST_RUN_REACH_BACK`, a constant); a row may override it with `first_run_reach_back_minutes`, Graph's key, and it is not asked at enrolment. A stored cursor this connector cannot read — the old decimal form, or anything else — is a non-retryable failure naming it, cursor unchanged, as Graph refuses one.
- **GitLab's row shape is mapped**: `id` is the sha; `message`; `author_email`; `committed_date`, parsed to an aware UTC instant, replaces the invented `committed_at`. A row whose date will not parse refuses that row alone; a date in the future is flagged as implausible, as today, and never sets the cursor.
- **Each answer has one reading.** 401 — token refused. 403 — the token lacks the `read_api` scope. 404 — no project visible at that path (GitLab answers 404 for a project the token cannot see). None of the three is retryable. 429 — the wait hint in seconds (https://docs.gitlab.com/administration/settings/user_and_ip_rate_limits/) is honoured inside the fetch when it fits the remaining budget, through an injected wait that composition sets to a real sleep; no hint, or a date for one, waits a default thirty seconds (`DEFAULT_THROTTLE_BACKOFF`); a hint that does not fit, a third throttle on one page, or a wait nobody injected ends the walk with the hint on the failure, retryable. 5xx and an unreachable host are retryable. Any other status is named, not retryable.
- **No credential, no socket.** A built-in with no credential is not harvested and records no failure — it is not set up, which is not a fault; asked directly, its harvest returns a non-retryable failure saying so, with no coverage.
- **The health check asks for one commit of the project**: healthy on 200, failing with the status's reading otherwise, under the registry's ten-second bound and the socket timeout; it never waits out a throttle hint. The "transport is a stub" state no longer exists.
- **No failure sentence carries the token.** Reasons are composed from the status, never from a response body or an exception's text.
- **Tests open no socket.** The transport is tested with the opener replaced in-process, as the Graph transport is; the harvest through the existing injected page fetch. The in-memory fake list is removed — a default that walks a fake is the defect this slice ends.

**Ask First:** Nothing. Which commits count, the merge lag and the first-run reach-back were settled on 2026-10-09 and are stated above.

**Never:** No merge requests, events or issues. No writes to GitLab. No new dependency. No probe and no CLI change — the next slice. No scheduler. No HTTP outside `pm_ai/connectors/gitlab/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| One page | three commits since the cursor | three events; cursor = newest date; one window | N/A |
| Two pages | next page 2, then none | both pages' events; one window; cursor from the whole walk | N/A |
| Empty | 200 with `[]` | no events; window claimed; cursor unchanged | N/A |
| Page two fails | 200 then 503 | page one kept with its window; cursor unmoved | failure, retryable |
| Merge lag | cursor T | request asks from T − 30 days; re-seen commits counted as duplicates | N/A |
| Future date | one commit dated tomorrow | flagged; cursor = newest plausible date | N/A |
| Token refused / lacks scope | 401 / 403 | nothing walked; names the scope on 403 | failure, not retryable |
| Project not visible | 404 | names the path and host | failure, not retryable |
| Throttled, fits | 429, hint 5 s, budget left | waited through the injected wait; page re-asked | N/A |
| Throttled, no hint or a date | 429 without seconds | waits the default 30 s within the budget | N/A |
| Throttled, does not fit / no wait | hint past the budget, or default wait | what was walked returned; hint on the failure | failure, retryable |
| Host unreachable | connection refused, or silent 30 s | exception type named, never its text | failure, retryable |
| Redirect | 302 elsewhere | refused, not followed | failure, not retryable |
| Bad host | `http://…`, `…/?x`, trailing slash | refused before any socket | failure, not retryable |
| Host with a path | `https://git.internal/gitlab` | requests go to `…/gitlab/api/v4/…` | N/A |
| Bad token value | newline in the token | no socket; cause named | failure, not retryable |
| Body not a list | 200 with `{}` | "not a GitLab commit list" | failure, not retryable |
| Next page unreadable | header `abc` | what was walked kept | failure, not retryable |
| Next page does not advance | header repeats the page | reported, not walked again | failure, not retryable |
| Built-in, no credential | watched project, nothing enrolled | not harvested; nothing recorded; health "not set up yet" | N/A |
| Adapter, no credential | `credential=None`, harvest called | no socket | failure, not retryable |
| Old cursor | decimal offset from the stub | refused, named; cursor unchanged | failure, not retryable |
| First run | empty cursor | asks from now − 7 days, or the row's override | N/A |
| Bad row setting | `project: "/x"`, reach-back `0` | connector not built; key named on stderr | N/A |
| Unparseable date | `committed_date: "yesterday"` | that row refused by sha; the rest kept | row refusal |
| Enrolled name kept | row `gitlab:alpha`, project `acme/web` | `instance` is `gitlab:alpha`; cursor and window save under it | N/A |
| Reference grammar | project `acme/web`, scope `alpha` | `gitlab:alpha:commit:<sha>` | N/A |
| Health check | 200 / 401 / silent | OK / FAILING with reading / FAILING at 10 s | reports, never raises |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/gitlab.py` -- becomes `pm_ai/connectors/gitlab/__init__.py`. `SAMPLE_ROW` (37); `_stubbed_reach` (138); `_fake_api` (157), `reach` (171), `fetch_page` (181); `instance` from `project` (192-201); `_to_event` (203), string-clock refusal (231-248), reference from `self.project` (253); `check_health` (284), stub branch (320-335); `_fetch_from_fake_api` (338); `harvest` (394): decimal cursor (433), `resume` (461, 526), loop bounds (527-575)
- `pm_ai/connectors/graph/client.py` -- the model: `_no_redirect_opener` (371), `_urllib_transport` (419), `timeout=30` (469), backoff constants (146-155), `wait`/`page_cap`/`budget` (597-619), the 429 branch (675-722)
- `pm_ai/connectors/graph/__init__.py:387,404` -- `MAX_SETTING_MINUTES` and `_minutes`, the checks the reach-back override reuses
- `pm_ai/domain/identity.py:160` -- `_REF`, whose scope group refuses `/`; `DataScope.project_id` (50)
- `pm_ai/domain/harvest.py:48,127` -- `Cursor`; `HarvestFailure.retry_after`
- `pm_ai/app/wiring.py:361` -- the built-in per watched project; `:690-751` the enrolled GitLab row, which must now pass the instance name, `host` and the reach-back, and inject `time.sleep`; `_graph_connector` (813) is the pattern for a defaulted row key
- `pm_ai/app/pipelines.py:69` -- `run_harvest`, which must skip a credential-less built-in
- `pm_ai/core/connector_enrolment.py:121` -- `RESERVED_ROW_KEYS`, which governs clashes with the keys below
- `pm_ai/storage/service.py:1584-1591` -- the natural-key duplicate count the merge lag relies on
- `tests/connectors/test_coverage_honesty.py` -- `connector` (129), `daemon` (134), `paging` (154); every `_fake_api=` row converts to `fetch_page=paging(...)`; rows 406 (stale cursor), 427 (duplicates), 492 (string clock), 790 (non-advancing page) change meaning
- `tests/slice/test_vertical_slice.py:38,89,127`, `tests/slice/test_storage_resolution.py:89,158,323,708`, `tests/slice/test_r4_gate_fixes.py:66` -- the other `_fake_api` assignments the removal changes
- `tests/connectors/test_registry.py:213,232,252` -- `reach=` injections and the stub row to replace
- `tests/connectors/test_graph_transport.py:1` -- the no-socket pattern; `tests/connectors/test_graph_calendar_mapping.py:1274` -- `_wired`, the pattern for composing a hand-written row through `build()` and asserting the recorded request
- `_bmad-output/implementation-artifacts/deferred-work.md:573` -- the `urllib.request` contract gap this slice widens

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/connectors/gitlab/client.py` -- the transport: one `GET`, https only, the token header, redirects refused, the 30 s socket timeout, status dispatch, throttle hints within the budget through an injected wait, next-page paging and its refusals -- the wire the connector lacked
- [ ] `pm_ai/connectors/gitlab/__init__.py` -- `instance` and `host` as fields; GitLab's row shape; the reference from the scope's project id; the date cursor, merge lag and first-run reach-back; no credential means no socket; `check_health` through the client; fake list and stub removed -- the harvest becomes honest
- [ ] `pm_ai/app/wiring.py`, `pm_ai/app/pipelines.py` -- pass the row's instance name, validated `host`, `project` and reach-back; inject `time.sleep`; skip a credential-less built-in -- composition owns the settings
- [ ] `tests/connectors/test_gitlab_transport.py` -- the transport against a replaced opener -- every status reading pinned
- [ ] `tests/connectors/test_coverage_honesty.py`, `tests/connectors/test_registry.py`, the three slice files above -- convert the fake-list rows; one test per matrix row, including a hand-written row with `host: https://git.internal/gitlab` and a reach-back composed through `build()`, asserting the first recorded request's host and `since` -- the matrix is the contract
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- note the second stdlib HTTP client under the `:573` entry

**Acceptance Criteria:**
- Given a row enrolled as `gitlab:alpha` for `acme/web` on a single-project machine, when the daemon composes and a harvest runs against a two-page injected transport, then both pages' events persist under `gitlab:alpha`, one window is saved under that name, and the cursor holds the newest commit date.
- Given a transport that fails on page two, when the harvest runs twice, then the second run asks from the same instant as the first.
- Given a built-in with no credential, when the harvest pipeline runs over it, then no transport call is made and no cursor, window or failure is saved.
- Given `uv run pytest`, then all pass and the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09); given `uv run lint-imports`, then every contract holds.

## Design Notes

Row keys this slice reads: `host`, `project`, `first_run_reach_back_minutes`; `RESERVED_ROW_KEYS` (`connector_enrolment.py:121`) governs clashes. Header names, kept out of the frozen block: the token goes in `PRIVATE-TOKEN`; the next page is `x-next-page` (the commits call sends no total headers); the throttle hint is `Retry-After`. `since` is rendered `YYYY-MM-DDTHH:MM:SSZ`, never with `+00:00`. Host rules: https scheme, non-empty host, optional path, no query, fragment, user info or trailing slash; base URL `<host>/api/v4`.

The cursor is an instant because GitLab's list is newest-first with no ascending order. No command clears a stale or unreadable cursor yet — the operator has to delete the row by hand; recorded in deferred-work.

The health check asks for a commit rather than the signed-in user because a `read_user` token reads only the user endpoints (https://docs.gitlab.com/security/tokens/access_token_scopes/): a check on `/user` would pass a token every harvest fails with.

## Verification

**Commands:**
- `uv run pytest tests/connectors tests/slice -q` -- expected: all matrix rows pass, no socket opened
- `uv run pytest -q` -- expected: all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09)
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
