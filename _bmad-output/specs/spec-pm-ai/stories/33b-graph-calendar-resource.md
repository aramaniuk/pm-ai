---
title: 'Graph calendar resource'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 1
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
- **Coverage is derived from what was fetched**, per `8a`. `calendarView` takes a real server-side date range, so the window is knowable exactly; report it, never compute it from the clock — and express it in `ingested_at`, as `CoverageWindow`'s docstring requires.
- **Graph times are converted to aware UTC at the boundary.** `calendarView` returns `{dateTime, timeZone}`, normally the mailbox zone, not aware UTC — and `Meeting.start` and `validate_occurred_at` both require aware UTC, so an unconverted value is refused for every single event. Send `Prefer: outlook.timezone="UTC"` and convert defensively regardless; a header is a request, not a guarantee.
- **The record is written before its event is emitted.** `CALENDAR_EVENT_HELD` cites `meeting:<id>`, so emitting it while the `meetings/` write failed leaves an unresolvable AD-33 citation.
- **`calendar_event_ref` carries the Graph event id**, and `33d` resolves the join URL from that id when it needs it. `Meeting` has no `join_url` field and this slice does not add one.
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
| All-day event | `isAllDay`, midnight-to-midnight | recorded with a **stated** duration convention that is neither 0 nor 1440 minutes; the value is asserted | N/A |
| Paged response | `@odata.nextLink` present | every page followed; coverage spans all pages walked | partial result per `8a` |
| Throttled | 429 with `Retry-After` | honoured; pages already walked returned with their real coverage | failure outcome, retryable |
| Provider timezone | `{dateTime, timeZone}` in the mailbox zone | converted to aware UTC before `Meeting.start` | `ImplausibleTimestamp` if unconvertible |
| DST-crossing occurrence | a series spanning a transition | duration computed from the zoned pair, never by adding a fixed offset | N/A |
| Modified occurrence | one instance moved, with its own id | keyed on the occurrence id, not the series master's | N/A |
| Declined or tentative | PM declined; event still on the calendar | declined not written; tentative recorded and marked | N/A |
| Attendee edges | null `emailAddress`, a distribution list, zero attendees | null resolves to `UNRESOLVED`; a group's expansion is stated; zero recorded as zero | N/A |
| `end` exactly equals `now` | boundary instant | bounds inclusive on one side only, so ended and in-progress are disjoint | N/A |
| Event deleted after its record was written | absent from a later window | the record is marked stale; a cancelled meeting stops being time-critical | N/A |
| Re-harvest over a hand-edit | the record was edited between runs | refused or merged — `meetings/` is hand-editable by design (AD-3) | surfaced |
| Record write fails | the `meetings/` write is refused | the event is **not** emitted | propagated |
| Window wider than the endpoint permits | a range Graph rejects | the window is clamped and the clamp reported as the coverage actually fetched | refused or split |
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
- `tests/architecture/test_domain_invariants.py:94,483` -- the two tests `8a` unskipped, now covering a second connector
- `tests/architecture/test_domain_invariants.py:793-826` -- the port-conformance test, which covers three adapters and no connector
- `pm_ai/app/wiring.py:40` -- `connectors: dict[str, GitLabConnectorAdapter]`, an annotation mypy will reject once a Graph connector is registered

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/connectors/graph/calendar.py` -- fetch `calendarView` over a bounded window and map to records and events
- [ ] `pm_ai/connectors/graph/__init__.py` -- add `GraphConnector` satisfying `ConnectorPort`, registered through `8a`
- [ ] `pm_ai/app/wiring.py` -- widen `Daemon.connectors` to `dict[str, ConnectorPort]` -- the current annotation names the GitLab class specifically and mypy, gated inside pytest since story 1k, will reject a Graph connector
- [ ] `tests/connectors/test_graph_calendar.py` -- the matrix against recorded Graph payload fixtures; no network

**Acceptance Criteria:**
- Given a window containing one ended and one upcoming meeting, when the harvest runs, then exactly one `CALENDAR_EVENT_HELD` is persisted and two `meetings/` records exist — the past/future split, asserted rather than described.
- Given an empty `calendarView` response, then `coverage_windows("graph")` gains no entry.
- Given the suite runs, then the AD-27 and AD-34 tests pass with `GraphConnector` registered — it emits no type outside the closed enum and mints no id.
- Given a room booking with no `joinUrl`, then a record is written and nothing raises; and given an online meeting, then `calendar_event_ref` holds the Graph event id `33d` will resolve from.
- Given every emitted event, then `authored_by` is `Provenance.UNKNOWN` — asserted, because AD-36's rule lives only in a comment at `gitlab.py:51-57` and the AD-34 test inspects only the absent `id`. A connector hard-coding `EXTERNAL` would make pm-ai's own writes admissible as evidence for its own promises, and pass every other check.
- Given `GraphConnector`, then `isinstance(GraphConnector(...), ConnectorPort)` holds — the port-conformance test covers `ScopePaths`, `GitVcs` and `StorageService` only, and its docstring says annotations are documentation until something checks them.
- Given a `calendarView` payload carrying `{dateTime, timeZone}` in a non-UTC mailbox zone, then `Meeting.start` is the correct aware-UTC instant — the case that otherwise refuses every event in the tenant.

## Spec Change Log

- **2026-09-02, multi-lens review.** One finding would have made the connector return nothing at all.
  **Graph timestamps would have refused every event.** `calendarView` returns `{dateTime, timeZone}`, normally the mailbox zone, while `Meeting.start` and `validate_occurred_at` both require aware UTC. The first draft had no conversion step and no matrix row, so the first real fetch would have failed on every item. Now converted at the boundary, with `Prefer: outlook.timezone="UTC"` sent and defensive conversion regardless.
  **An emitted event could have outlived a failed record write**, leaving `CALENDAR_EVENT_HELD` citing a `meeting:<id>` that does not exist — an unresolvable AD-33 citation. Ordering is now stated.
  **Re-harvest would have destroyed hand-edits.** `meetings/` is Tier-1 and hand-editable by design (AD-3), and the original "re-write of an existing id" row said the second write simply replaces — so every overlapping harvest window would have overwritten the PM's edits.
  **Three checks were doing nothing.** Nothing asserted the provenance a connector emits, though AD-36's rule lives only in a comment and a connector hard-coding `EXTERNAL` would make pm-ai's own writes admissible as evidence for its own promises. Nothing checked `isinstance(..., ConnectorPort)` — the port-conformance test covers three adapters and no connector. And the all-day row, "handled explicitly, never as zero-cost", was satisfied by the 24-hour mapping this spec's own Design Notes call the wrong answer; the convention is now stated and asserted.
  **`Daemon.connectors` is typed `dict[str, GitLabConnectorAdapter]`** (`wiring.py:40`), which mypy — gated inside pytest since story 1k — rejects the moment a Graph connector is registered. Widening it is now a task.
  **`joinUrl` retention resolved without a new field:** `calendar_event_ref` holds the Graph event id and `33d` resolves the join URL from it. The original declared only the *absence* case, and `Meeting` has no `join_url`.
  Test names and lines corrected to `:94` and `:483`, repeating `8a`'s fix.
## Design Notes

Fixtures are recorded real Graph payloads rather than hand-written dicts, because the shapes that break mappers are the ones nobody would invent: `attendees` entries with a null `emailAddress`, `isAllDay` with a midnight-to-midnight span, cancelled occurrences inside a series. The spike in slice 0 exists partly to capture these.

The all-day row is called out because the obvious mapping gives such an event a 24-hour duration, which then flows into `man_hour_cost` and produces a headline number that is wrong by an order of magnitude. Whatever the handling is, it must be deliberate.

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_calendar.py -q` -- expected: all matrix rows pass, no network
- `uv run pytest tests/architecture/test_domain_invariants.py -q` -- expected: AD-27 and AD-34 pass with two connectors registered
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
