---
title: 'A harvest result says when the provider answered'
type: 'bugfix'
created: '2026-10-09'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Since 2026-09-24, a check that found nothing still records that it checked — the "I looked between 09:00:04 and 09:00:06" record that lets pm-ai later say a promise on a quiet project was broken. But the result a connector hands back cannot see that a check happened. "Checked and found nothing" is worked out from "nothing mapped, nothing refused, nothing failed", which a connector could report without having asked the provider at all, and the record it carries is then taken on trust. The two shipped connectors measure honestly; the next one is held to nothing.

**Approach:** The result gains one more fact: the instant, by this machine's clock, at which the provider's first page was read. Every result that reached the provider carries it; a result claiming "checked and found nothing" without it is refused when built, a coverage window must start at it, and a window without it is refused. Both connectors already read that clock; they now report it. Nothing on disk changes.

## Boundaries & Constraints

**Always:**
- **The instant is a clock reading, never a computation.** It is taken when the provider's first page is read — not when the request was sent, and not derived from the window or from any row. A provider that answered only with an error, or never answered, gave no page, so there is no instant.
- **Every result that reached the provider carries it; a result that never reached the provider carries none.** A failure before any page has none; a walk that read page one and failed on page two has it, and keeps its window under the rules already in force.
- **"Checked and found nothing" requires it.** A result with that outcome and no instant is refused when built, so the one outcome that may carry a window over no rows cannot be reached without proof of an answer.
- **A coverage window starts at that instant, exactly.** A window whose start differs from it is refused when built, and a window with no instant at all is refused: the window is derived from the reading, not kept beside it.
- **The instant is an aware UTC time.** A naive or non-UTC value is refused when built, with its own message, before the window-start comparison is made.
- **Both connectors report the reading they already take.** The instant the GitLab walk and the Graph calendar fetch already measure for the window's start becomes the result's instant; the Graph connector passes it through, including on the run where the page was read and pm-ai then failed while mapping it. No connector reads the clock a second time for it.
- **A clock that stepped backwards still reports the instant.** The window is already withheld in that case; the instant is a fact and stays.
- **Nothing on disk changes.** No result is ever stored or replayed; only the cursor, its coverage window and any failure are written, and those are unchanged. The stored window already begins at this instant.
- **The calendar's window is passed through unchanged.** A later story that harvests several resources in one run (channel messages, 2026-10-09) replaces this with one window built across them — start at the first resource's instant, end at the last resource's walk end — withheld entirely when any listed resource failed. This story changes nothing there.

**Ask First:** Nothing.

**Never:** No new harvest outcome. No change to which results earn a window — the three exclusions in force since 2026-09-24 stand. No change to how the window is persisted or read back. No message harvesting.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Quiet provider | empty page, instant read | checked and found nothing; window starts at the instant | N/A |
| Empty claimed without an answer | "found nothing", no instant | refused when built, naming the missing instant | refused when built |
| Window without an instant | window present, no instant | refused when built | refused when built |
| Window not starting at the instant | window start one second off | refused when built | refused when built |
| Instant not aware UTC | naive, or an offset other than UTC | refused when built with its own message, before the window start is compared | refused when built |
| Harvested with rows | page one read at 09:00:04 | instant 09:00:04; window starts there | N/A |
| Failed before any page | transport refused, or an error status on the first request | no instant, no window; failure carried | N/A |
| Failed after page one | page two 502 | instant set; page one's window and events kept | N/A |
| Clock stepped back | finish earlier than the instant | instant set; no window | N/A |
| GitLab quiet project | empty 200 | instant equals the reading already taken for the window's start | N/A |
| Graph empty calendar | window with no rows | instant equals the fetch's reading; window passed through unchanged | N/A |
| Graph page read, mapping failed | the row mapper raises after the page arrived | failed result; instant kept; no window, cursor unchanged | N/A |
| Stored shape | any run persisted | no new column, no version bump | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/harvest.py:272` `HarvestResult` — gains `answered_at` (AD-9, 2026-10-09); `:345` `__post_init__`, where the three refusals join; `:405-422` the existing coverage refusal to keep beside them
- `pm_ai/connectors/gitlab.py:466` `reached_at` declared; `:522-523` read once the first page is in hand; `:648` the backwards-clock check (`finished_at >= reached_at`) that already withholds the window; `:624-660` the outcome and window built from it — the result gains `answered_at=reached_at`
- `pm_ai/connectors/graph/calendar.py:760` `CalendarFetch`, which gains `answered_at`; `:866,880-883` `reached_at` read once the first page body arrives — the fetch's existing `reached_at` is the source; `:979` the backwards-clock check (`finished >= reached_at`); `:951-990` the window built from it
- `pm_ai/connectors/graph/__init__.py:618,644` the two early returns before any page — no instant; `:669` the third, after the page was read and `_result` raised — carries `fetched.answered_at`; `:685` `_result`; `:760` the coverage pass-through, where the instant is passed through beside it
- `pm_ai/storage/service.py:1650` `save_cursor` — takes the cursor, the window and the failure; unchanged
- `tests/connectors/test_coverage_honesty.py:107` `Ticking`; `:546,584,650,715` the empty, quiet-project, empty-then-failed and page-two-failed cases; `:674` the empty-page-then-gone run and `:1064` the stepping-back clock, both to gain an `answered_at` assertion; `:187-353` the window-bound cases
- `tests/connectors/test_graph_calendar_fetch.py:140` the fetch tests' clock; `:76-77,251` the exact coverage-bound assertions to extend; `:950` the 503-first-request mirror (no instant) and `:1666` the stepping-back mirror (instant set, no window)
- `tests/connectors/test_graph_calendar_mapping.py:1` — the Graph mirror of the empty case
- `tests/slice/test_dashboard_slice.py:120-127` — a fake connector that builds a "found nothing" result with no instant; it must gain one

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/harvest.py` — `answered_at: datetime | None = None` on `HarvestResult`; refuse `EMPTY` without it, refuse a naive or non-UTC value with its own message, then refuse a coverage window with no instant or whose start is not it — the guard moves from honour system to the type
- [ ] `pm_ai/connectors/gitlab.py` — report `reached_at` as `answered_at` — already measured
- [ ] `pm_ai/connectors/graph/calendar.py` — `CalendarFetch.answered_at` from `reached_at` — the fetch carries it out, including when the clock stepped back
- [ ] `pm_ai/connectors/graph/__init__.py` — pass `fetched.answered_at` onto the result in `_result`; the two early returns before a fetch carry none; the third early return (page read, mapping raised) carries `fetched.answered_at`
- [ ] `tests/slice/test_dashboard_slice.py:120-127` — the fake connector's result gains an instant
- [ ] `tests/domain/test_harvest_result.py` (new) — one test per matrix row on the type, including the aware-UTC refusal ordered before the window-start one; `tests/connectors/test_coverage_honesty.py:674,1064` and `tests/connectors/test_graph_calendar_fetch.py:950,1666` — assert `answered_at` equals the first-page reading under the existing fake clocks, or is `None` where no page was read; `test_graph_calendar_mapping.py` — the empty case and the mapping-raised case assert the instant on the result
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` — mark the entry at `:826-828` resolved

**Acceptance Criteria:**
- Given a provider that answers with nothing, when the result is built, then it carries the instant the page was read and a window starting at that instant — and building the same result without the instant, or with a window and no instant, is refused.
- Given every result that reached the provider, from both shipped connectors under a stepping fake clock, then its instant equals the reading taken on the first page, never a later or computed one; and a result that never reached the provider has none.
- Given a Graph page that was read and a mapper that then raised, when the failed result is built, then it still carries the instant.
- Given `uv run pytest -q`, then all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09).

## Verification

**Commands:**
- `uv run pytest tests/domain/test_harvest_result.py tests/connectors/test_coverage_honesty.py tests/connectors/test_graph_calendar_fetch.py tests/slice/test_dashboard_slice.py -q` — expected: all pass
- `uv run pytest -q` — expected: all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09)
- `uv run mypy` — expected: no errors
- `uv run lint-imports` — expected: all contracts kept
