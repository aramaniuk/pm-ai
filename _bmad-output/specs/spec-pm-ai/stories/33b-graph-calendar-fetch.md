---
title: 'Graph calendar fetch'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** With `33a` supplying tokens, nothing yet talks to Microsoft Graph. Reaching `calendarView` correctly is a transport and time problem before it is a modelling one: the endpoint pages, throttles, and returns times in the mailbox timezone rather than aware UTC — and `Meeting.start` and `validate_occurred_at` both require aware UTC, so an unconverted value is refused for every single event in the tenant.

Split from the original `33b` on 2026-09-02 at the sizing gate: fetching Graph correctly and mapping what came back into pm-ai's model are two failure classes, and the review found defects in both.

**Approach:** The Graph HTTP client and the calendar fetch. Pages `calendarView` over a bounded window and returns validated, UTC-normalized `CalendarRow` values plus the coverage actually earned. No domain events, no Meeting records — `33c` owns those.

## Boundaries & Constraints

**Always:**
- **Times are converted to aware UTC here, once.** `calendarView` returns `{dateTime, timeZone}`. Send `Prefer: outlook.timezone="UTC"` **and** convert defensively regardless — a header is a request, not a guarantee, and one unconverted value refuses an entire harvest downstream.
- **Every page is followed.** An `@odata.nextLink` in the response means more meetings exist; stopping at page one silently drops the day's later meetings.
- **Coverage is what was actually walked**, per `8a`, expressed in `ingested_at` as `CoverageWindow`'s docstring requires. `calendarView` takes a real server-side range, so the window is knowable exactly — report it, never compute it from the clock.
- **The requested window is clamped to what the endpoint permits**, and the clamp is reported as the coverage fetched. Claiming a range Graph never returned is the fabrication `8a` exists to remove.
- **Read-only, class H egress** (AD-1). This slice issues `GET` and nothing else.

**Ask First:** How wide the harvest window should be. It interacts with CAP-2's 240-minute cycle, with how far back a first run should reach, and with the endpoint's own limit; a window narrower than the cycle leaves permanent gaps.

**Never:** No `Meeting` records and no `CALENDAR_EVENT_HELD` — `33c`. No chat or channel messages (`33d`), no transcripts (`33e`). No `ConnectorPort` implementation: this slice is the fetcher `33c`'s connector calls.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Single page | a window with three events | three rows, coverage spanning the requested range | N/A |
| Paged response | `@odata.nextLink` present | every page followed; coverage spans all pages walked | partial result per `8a` |
| Empty window | no events in range | ran-and-learned-nothing; **no** coverage claimed | N/A |
| Mailbox timezone | `{dateTime, timeZone}` in a non-UTC zone | converted to aware UTC before the row is emitted | `ImplausibleTimestamp` if unconvertible |
| `Prefer` header ignored | provider returns a local zone anyway | still converted — the defensive path is exercised, not assumed | N/A |
| DST-crossing span | an occurrence across a transition | duration from the zoned pair, never by adding a fixed offset | N/A |
| Throttled | 429 with `Retry-After` | honoured; pages already walked returned with their real coverage | failure outcome, retryable |
| Token expires mid-fetch | 401 on page 3 | `33a`'s silent refresh, the page retried once; earlier pages retained | `CredentialStale` after the retry |
| Graph 5xx | provider error | failure outcome; no coverage, cursor unmoved | surfaced to the caller |
| Window wider than permitted | a range the endpoint rejects | clamped, and the clamp reported as the coverage fetched | refused or split |
| Malformed row | an event with no `start` | refused, naming the event id — never emitted with a guessed time | `MalformedCalendarRow` |
| Provider time implausible | a `start` years in the future | flagged per AD-35; the batch still returns | flag carried on the row |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/graph/client.py`, `pm_ai/connectors/graph/calendar.py` -- new; the HTTP client and the fetcher
- `pm_ai/connectors/graph/auth.py` -- `33a`'s adapter, the token source
- `pm_ai/domain/clocks.py` -- `validate_occurred_at`, which refuses anything not aware UTC
- `pm_ai/domain/lifecycle.py:158-164` -- `CoverageWindow` and its `ingested_at` rule
- `pm_ai/domain/harvest.py` -- `8a`'s three-outcome `HarvestResult`

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/connectors/graph/client.py` -- paging, 429 handling, the `Prefer` header, and 401 refresh-and-retry
- [ ] `pm_ai/connectors/graph/calendar.py` -- `CalendarRow`, the fetch, UTC conversion, window clamping, `MalformedCalendarRow`
- [ ] `tests/connectors/test_graph_calendar_fetch.py` -- the matrix against recorded Graph payload fixtures; no network in any test

**Acceptance Criteria:**
- Given a recorded `calendarView` payload whose times are in a non-UTC mailbox zone, then every emitted row's start is the correct aware-UTC instant — the case that otherwise refuses every event in the tenant.
- Given a two-page response, then rows from both pages are returned and exactly one coverage window is reported spanning both — a fetcher that stops at `nextLink` passes any single-page test.
- Given an empty response, then no coverage is claimed.
- Given a 429 after page one, then page one's rows and its real coverage are returned **with** the failure, not discarded.

## Design Notes

Fixtures are recorded real Graph payloads rather than hand-written dicts, because the shapes that break a fetcher are the ones nobody would invent: `isAllDay` with a midnight-to-midnight span in a zone that shifts, a cancelled occurrence inside a series, `@odata.nextLink` on a response that looks complete. Slice 0's spike exists partly to capture these.

Converting defensively even with the `Prefer` header sent is deliberate belt-and-braces. The header is honoured by the service, not by the protocol, and the failure mode if it is ignored is not a wrong time — it is `validate_occurred_at` refusing every row, which reads as "the connector is broken" rather than "the header did not apply".

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_calendar_fetch.py -q` -- expected: all matrix rows pass, no network
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
