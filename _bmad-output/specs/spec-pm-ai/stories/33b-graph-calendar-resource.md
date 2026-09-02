---
title: 'Graph calendar resource'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** With `33a` supplying tokens and `11a` supplying durable meeting records, nothing yet reaches Microsoft Graph. The dashboard's Time-Critical Activities section needs today's meetings, and `33d` needs calendar events as the only route to transcripts — `/me/onlineMeetings/{id}/transcripts` works only for meetings tied to a calendar event, so calendar is the lookup path rather than a third independent resource.

**Approach:** Add the calendar resource fetcher and the `GraphConnector` that satisfies `ConnectorPort`: `/me/calendarView` over a bounded window, writing upcoming meetings as Tier-1 `Meeting` records and emitting `CALENDAR_EVENT_HELD` for meetings that have ended.

## Boundaries & Constraints

**Always:**
- **Upcoming meetings are records, not events.** `CALENDAR_EVENT_HELD` is past tense and `MeetingHeldPayload` carries no title or start time (`events.py:130-133`), so a future meeting cannot be expressed as an event without opening a closed enumeration — which a connector may never do (AD-27). Future meetings become `meetings/` records via `11a`; only ended meetings emit an event.
- **`emits()` returns exactly `{CALENDAR_EVENT_HELD}`** in this story. `MESSAGE_POSTED` joins it in `33c`. The pre-written AD-27 test now checks this against the closed enum.
- **A connector may not mint an event id** (AD-34) and may not assert `Provenance.EXTERNAL` (AD-36) — it emits `UNKNOWN` and `core.normalize` decides, as `gitlab.py:57` already does.
- **Coverage is derived from what was fetched**, per `8a`. `calendarView` takes a real server-side date range, so the window is knowable exactly; report it, never compute it from the clock.
- **The meeting's scope is decided before it is written**, not defaulted (AD-33/AD-38).

**Ask First:** How a calendar event's scope is determined. `Meeting.scope` decides where the record lands and whether a committed scope may cite it, and a Graph event carries no pm-ai scope. Attendee-based inference, a configured default, and per-connector configuration are all plausible and the choice is consequential.

**Never:** No chat or channel messages (`33c`). No transcripts (`33d`) — but the `joinUrl` needed to find them is retained on the record. No writes to Graph: read-only, class H egress (AD-1). No Whisper, no model.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Upcoming meeting | event starting in 3h | `Meeting` record written; **no** event emitted | N/A |
| Ended meeting | event that finished yesterday | `CALENDAR_EVENT_HELD` emitted with attendee count and duration | N/A |
| In-progress meeting | started, not ended | record written, no event — it has not been held yet | N/A |
| Empty window | no events in range | ran-and-learned-nothing; no coverage claimed | N/A |
| Recurring series | a weekly recurrence | each occurrence in the window is its own record | N/A |
| Cancelled event | event marked cancelled | not written as an upcoming meeting | N/A |
| All-day event | no meaningful duration | recorded; duration handled explicitly, never as zero-cost | N/A |
| No online meeting | a room booking with no `joinUrl` | record written without a join reference; `33d` skips it | N/A |
| Token stale | refresh rejected | harvest fails, no coverage, cursor unmoved | `CredentialStale` from `33a` |
| Graph 5xx | provider error | failure reported; retryable | surfaced to the caller |
| Provider timestamp implausible | `start` far in the future | flagged per AD-35, batch still persists | `flagged` count on `PersistResult` |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/graph/__init__.py`, `pm_ai/connectors/graph/calendar.py` -- new; the connector and the resource fetcher
- `pm_ai/ports/__init__.py:22-33` -- `ConnectorPort`, satisfied unchanged
- `pm_ai/domain/events.py:130-133,146` -- `MeetingHeldPayload` and its binding to `CALENDAR_EVENT_HELD`
- `pm_ai/connectors/gitlab.py:40-60` -- the reference mapping: AD-34 source refs, `resolve_actor`, `Provenance.UNKNOWN`, no minted id
- `pm_ai/core/meeting_records.py` -- `11a`'s accessor, the writer for upcoming meetings
- `pm_ai/domain/clocks.py` -- `validate_occurred_at`, for provider timestamps
- `tests/architecture/test_domain_invariants.py:104,485` -- the two tests `8a` unskipped, now covering a second connector

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/connectors/graph/calendar.py` -- fetch `calendarView` over a bounded window and map to records and events
- [ ] `pm_ai/connectors/graph/__init__.py` -- add `GraphConnector` satisfying `ConnectorPort`, registered through `8a`
- [ ] `tests/connectors/test_graph_calendar.py` -- the matrix against recorded Graph payload fixtures; no network

**Acceptance Criteria:**
- Given a window containing one ended and one upcoming meeting, when the harvest runs, then exactly one `CALENDAR_EVENT_HELD` is persisted and two `meetings/` records exist — the past/future split, asserted rather than described.
- Given an empty `calendarView` response, then `coverage_windows("graph")` gains no entry.
- Given the suite runs, then the AD-27 and AD-34 tests pass with `GraphConnector` registered — it emits no type outside the closed enum and mints no id.
- Given a room booking with no `joinUrl`, then a record is written and nothing raises.

## Design Notes

Fixtures are recorded real Graph payloads rather than hand-written dicts, because the shapes that break mappers are the ones nobody would invent: `attendees` entries with a null `emailAddress`, `isAllDay` with a midnight-to-midnight span, cancelled occurrences inside a series. The spike in slice 0 exists partly to capture these.

The all-day row is called out because the obvious mapping gives such an event a 24-hour duration, which then flows into `man_hour_cost` and produces a headline number that is wrong by an order of magnitude. Whatever the handling is, it must be deliberate.

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_calendar.py -q` -- expected: all matrix rows pass, no network
- `uv run pytest tests/architecture/test_domain_invariants.py -q` -- expected: AD-27 and AD-34 pass with two connectors registered
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
