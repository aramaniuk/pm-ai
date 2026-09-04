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
- **A `timeZone` Graph returns may be a Windows id, not an IANA one.** `"W. Europe Standard Time"` is what a mailbox with a Windows default sends, and `ZoneInfo` raises `ZoneInfoNotFoundError` for it. That is **not** an implausible clock: routing it there would flag every event in the tenant as a timestamp fault. An unresolvable zone is its own refusal, and mapping the Windows names needs `tzdata`, which this slice declares.
- **Every page is followed, up to a stated bound.** Stopping at page one silently drops the day's later meetings — but "every page" with no ceiling is an unbounded loop over a value the response body controls, so a page cap and a seen-link set are part of the rule, not hardening added later.
- **A `nextLink` is validated before it is followed.** It arrives in the response body, so its origin is checked against the Graph host: a link pointing elsewhere is a refusal, not a page.
- **Coverage is `ingested_at`, and the `calendarView` range is not coverage.** This clause named both, and they are different things: the requested range is *calendar* time — which meetings were asked for — while `CoverageWindow` describes what the daemon did, in `ingested_at` (`lifecycle.py:158-164`). So the window is the connector's own clock across the fetch, per `8a`, and what the server-side range decides is **whether** coverage was earned, not its bounds. Reporting the calendar range as coverage is AD-35's mixed-clock defect wearing this clause's words.
- **A window the endpoint will not serve is split, never silently absorbed.** The old wording said "clamped … refused or split", which are opposite behaviours: a clamp that quietly narrows leaves a permanent hole no later run revisits, because the cursor advances past it. So the request is split into servable spans and every span is walked, or the whole fetch refuses — and what is reported is what was walked.
- **Read-only, class H egress** (AD-1). This slice issues `GET` and nothing else.

**Ask First:** How wide the harvest window should be, and how far back a first run reaches. One constraint is not open: **the window may never be narrower than CAP-2's 240-minute cycle**, or each run leaves a gap the next skips past. Whatever width is chosen must satisfy that, and a matrix row asserts it.

**Never:** No `Meeting` records and no `CALENDAR_EVENT_HELD` — `33c`. No chat or channel messages (`33d`), no transcripts (`33e`). No `ConnectorPort` implementation: this slice is the fetcher `33c`'s connector calls.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Single page | a window with three events | three rows, coverage spanning the requested range | N/A |
| Paged response | `@odata.nextLink` present | every page followed; coverage spans all pages walked | partial result per `8a` |
| Empty window | no events in range | ran-and-learned-nothing; **no** coverage claimed | N/A |
| Mailbox timezone, IANA | `{dateTime, timeZone}` in a non-UTC zone | converted to aware UTC before the row is emitted | N/A |
| Mailbox timezone, Windows id | `"W. Europe Standard Time"` | mapped to IANA, then converted | `UnresolvableTimezone` — never `ImplausibleTimestamp` |
| Zone unresolvable either way | a zone in no map | refused for that row, naming the zone; the batch continues | `UnresolvableTimezone` |
| `Prefer` header ignored | provider returns a local zone anyway | still converted — the defensive path is exercised, not assumed | N/A |
| `Prefer` header sent | any request | asserted **on the recorded request**, not inferred from the rows — every other criterion here observes output, so a fetcher that never sends it passes them all | N/A |
| DST-crossing span | an occurrence across a transition | duration from the zoned pair, never by adding a fixed offset | N/A |
| Throttled | 429 with `Retry-After` in seconds | honoured; pages already walked returned with their real coverage | failure outcome, retryable |
| Throttled, no hint | 429 with no `Retry-After` | a stated default backoff — "honoured" is undefined without the header | failure outcome, retryable |
| Throttled, HTTP-date or long hint | a date value, or a delay past the run's budget | parsed; a hint exceeding the budget returns what was walked rather than blocking | failure outcome, retryable |
| Token expires mid-fetch | 401 on page 3 | `33a`'s silent refresh, the page retried once; earlier pages retained | `CredentialStale` after the retry |
| Graph 5xx | provider error | failure outcome; no coverage, cursor unmoved | surfaced to the caller |
| Graph 403 mid-fetch | an admin revoked the permission | reported as a consent change — neither retryable nor a stale token, so it must not enter `33a`'s refresh path | failure outcome, not retryable |
| Window wider than permitted | a range the endpoint rejects | split into servable spans, every span walked; what is reported is what was walked | failure outcome if a span cannot be served |
| Window narrower than the cycle | a configured width under 240 minutes | refused at construction — each run would leave a gap the next skips past | `ValueError` |
| `nextLink` repeats | a provider loop | refused at the page cap or by the seen-link set, whichever fires first | failure outcome |
| `nextLink` off-host | a link to another origin | refused, not followed | failure outcome |
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

## Spec Change Log

- **2026-09-03, amended against the second multi-lens review.**
  **A Windows timezone id was routed to the wrong error** (B20). Graph sends `"W. Europe Standard Time"` for a mailbox with a Windows default and `ZoneInfo` raises `ZoneInfoNotFoundError`; the matrix routed an unconvertible zone to `ImplausibleTimestamp`, which would flag **every event in the tenant** as a clock fault. Now its own refusal, with `tzdata` declared.
  **"Every page is followed" was an unbounded loop over a value the response body controls** (B21). A page cap and a seen-link set are part of the rule, and a `nextLink` is origin-checked before it is followed.
  **The coverage clause named two clocks** — the same defect as `8a`'s. The `calendarView` range is calendar time; `CoverageWindow` is `ingested_at`. The range decides whether coverage was earned, not its bounds.
  **"Clamped … refused or split" named opposite behaviours.** A clamp that quietly narrows leaves a permanent hole, because the cursor advances past it. Split-or-refuse, and report what was walked.
  **Three throttling paths were undefined** — a 429 with no `Retry-After`, one carrying an HTTP-date, and a hint longer than the run's budget — and a **403 mid-fetch**, an admin revoking consent, had no row at all, so it would have entered `33a`'s refresh-and-retry path as though it were a stale token.
  **The `Prefer` header was required and verified only by conversion** (C9). All four criteria observed emitted rows, so a fetcher that never sent it passed every one; it is now asserted on the recorded request, which the required fixtures already make possible.
  **The `Ask First` on window width is narrowed:** the width stays the human's, but it may never be under CAP-2's 240-minute cycle, and a row asserts it.

## Design Notes

Fixtures are recorded real Graph payloads rather than hand-written dicts, because the shapes that break a fetcher are the ones nobody would invent: `isAllDay` with a midnight-to-midnight span in a zone that shifts, a cancelled occurrence inside a series, `@odata.nextLink` on a response that looks complete. Slice 0's spike exists partly to capture these.

Converting defensively even with the `Prefer` header sent is deliberate belt-and-braces. The header is honoured by the service, not by the protocol, and the failure mode if it is ignored is not a wrong time — it is `validate_occurred_at` refusing every row, which reads as "the connector is broken" rather than "the header did not apply".

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_calendar_fetch.py -q` -- expected: all matrix rows pass, no network
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
