---
title: 'Graph calendar mapping to records and events'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `33b` returns UTC-normalized calendar rows and `11a` can persist meetings, but nothing turns one into the other. This is where a calendar event becomes pm-ai's model, and the modelling decisions are the load-bearing ones: which rows become Tier-1 records, which become events, and what a connector is allowed to assert.

Split from the original `33b` on 2026-09-02 at the sizing gate.

**Approach:** `GraphConnector` satisfying `ConnectorPort`, mapping rows to `Meeting` records and `CALENDAR_EVENT_HELD` events, registered through `8d`.

## Boundaries & Constraints

**Always:**
- **Upcoming meetings are records, not events.** `CALENDAR_EVENT_HELD` is past tense and `MeetingHeldPayload` carries no title or start (`events.py:130-133`), so a future meeting cannot be expressed as an event without opening a closed enumeration — which a connector may never do (AD-27). Future rows become `meetings/` records via `11a`; only ended rows emit an event.
- **`emits()` returns exactly `{CALENDAR_EVENT_HELD}`.** `MESSAGE_POSTED` joins it in `33d`.
- **A connector mints no event id** (AD-34) and **never asserts `Provenance.EXTERNAL`** (AD-36) — it emits `UNKNOWN` and `core.normalize` decides, as `gitlab.py:51-57` does. Hard-coding `EXTERNAL` would make pm-ai's own writes admissible as evidence that its own promises were kept.
- **The record is written before its event is emitted.** The event cites `meeting:<id>`, so emitting it after a failed `meetings/` write leaves an unresolvable AD-33 citation.
- **`calendar_event_ref` carries the Graph event id**, and `33e` resolves the join URL from that id when it needs one. `Meeting` has no `join_url` field and this slice does not add one.
- **A re-harvest never silently overwrites a hand-edited record.** `meetings/` is Tier-1 and hand-editable by design (AD-3), and harvest windows overlap.

**Ask First:** How a row's `Meeting.scope` is determined. It decides where the record lands and whether a committed scope may cite it (AD-33/AD-38), and a Graph event carries no pm-ai scope. Attendee-based inference, a configured default, and per-connector configuration are all plausible and the choice is consequential.

**Also Ask First:** the all-day duration convention. The obvious 24-hour mapping flows into `man_hour_cost` (`meetings.py:48-50`) and produces a headline figure wrong by an order of magnitude; 0 is equally wrong. Whatever the value, it is stated and asserted.

**Never:** No HTTP — `33b` owns the wire. No messages (`33d`), no transcripts (`33e`). No writes to Graph.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Upcoming row | starts in 3h | `Meeting` record written; **no** event emitted | N/A |
| Ended row | finished yesterday | `CALENDAR_EVENT_HELD` emitted with attendee count and duration | N/A |
| In progress | started, not ended | record written, no event — it has not been held yet | N/A |
| `end` exactly equals `now` | boundary instant | bounds inclusive on one side only, so ended and in-progress are disjoint | N/A |
| Recurring series | a weekly recurrence in the window | each occurrence is its own record | N/A |
| Modified occurrence | one instance moved, with its own id | keyed on the occurrence id, not the series master's | N/A |
| Cancelled row | marked cancelled | not written as an upcoming meeting | N/A |
| Declined by the PM | declined, still on the calendar | not written | N/A |
| Tentative | tentatively accepted | recorded and marked as tentative | N/A |
| All-day row | midnight to midnight | recorded with the **stated** duration convention, asserted; neither 0 nor 1440 by default | N/A |
| Attendee edges | null `emailAddress`, a distribution list, zero attendees | null resolves to `UNRESOLVED`; group expansion stated; zero recorded as zero | N/A |
| No online meeting | a room booking with no join reference | record written; `calendar_event_ref` still holds the event id | N/A |
| Record write fails | `meetings/` refuses | the event is **not** emitted | propagated |
| Re-harvest over a hand-edit | the record changed since it was written | refused or merged, never silently replaced | surfaced |
| Row deleted upstream | previously written, absent from a later window | record marked stale; it stops being time-critical | N/A |
| Row carries an implausible-time flag | `33b` flagged it | persisted and counted in `PersistResult.flagged` | reported, not raised |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/graph/__init__.py` -- new; `GraphConnector`
- `pm_ai/ports/__init__.py:22-33` -- `ConnectorPort`, satisfied unchanged
- `pm_ai/domain/events.py:130-133,146` -- `MeetingHeldPayload` and its binding to `CALENDAR_EVENT_HELD`
- `pm_ai/connectors/gitlab.py:40-60` -- the reference mapping: AD-34 source refs, `resolve_actor`, `Provenance.UNKNOWN`, no minted id
- `pm_ai/core/meeting_records.py` -- `11a`'s accessor and its safe-name encoding
- `pm_ai/domain/meetings.py:48-50` -- `man_hour_cost`, which the all-day convention feeds
- `pm_ai/app/wiring.py:41` -- `connectors: dict[str, GitLabConnectorAdapter]`, an annotation mypy rejects once a Graph connector is registered
- `tests/architecture/test_domain_invariants.py:94,483,793-826` -- the two tests `8d` unskipped, and the port-conformance test that covers no connector

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/connectors/graph/__init__.py` -- add `GraphConnector` satisfying `ConnectorPort`, registered through `8d`
- [ ] `pm_ai/app/wiring.py` -- widen `Daemon.connectors` to `dict[str, ConnectorPort]` -- mypy, gated inside pytest since story 1k, rejects the current annotation
- [ ] `tests/connectors/test_graph_calendar_mapping.py` -- the matrix against `33b`'s row fixtures

**Acceptance Criteria:**
- Given a window containing one ended and one upcoming row, then exactly one `CALENDAR_EVENT_HELD` is persisted and two `meetings/` records exist — the past/future split, asserted rather than described.
- Given every emitted event, then `authored_by` is `Provenance.UNKNOWN` — asserted, because AD-36's rule lives only in a comment and the AD-34 test inspects only the absent `id`.
- Given `GraphConnector`, then `isinstance(GraphConnector(...), ConnectorPort)` holds — the port-conformance test covers three adapters and no connector, and its docstring says annotations are documentation until something checks them.
- Given the AD-27 and AD-34 tests with `GraphConnector` registered, then both pass and `all_connectors()` returns two connectors — `8d`'s non-empty assertion now has a second member.
- Given a `meetings/` write that refuses, then no event is persisted.
- Given an all-day row, then its recorded duration equals the stated convention — the original row said only "handled explicitly, never as zero-cost", which the wrong 24-hour answer satisfied.

## Spec Change Log

- **2026-09-02, `wiring.py` citations re-pointed after story 4a.** 4a added one import to `wiring.py`, shifting every line below it, and a parameter plus a docstring paragraph to `build()`, shifting the rest further. The numbers below named other code. **Line numbers only — no wording, no intent, no task, and no acceptance criterion changed.**

## Design Notes

The past/future split is the one modelling decision that could not be deferred, because it is forced by a closed enumeration rather than chosen: `MeetingHeldPayload` has no field for a title or a start, so an upcoming meeting has no honest representation as an event. That constraint is what makes `meetings/` the dashboard's Time-Critical source rather than `event_log/`.

Refusing to overwrite a hand-edited record costs a comparison on every re-harvest and buys the property AD-3 asserts: a PM who corrects a meeting title keeps the correction. Without it, `meetings/` is hand-editable in declaration only.

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_calendar_mapping.py -q` -- expected: all matrix rows pass
- `uv run pytest tests/architecture/test_domain_invariants.py -q` -- expected: AD-27, AD-34 and port conformance pass with two connectors
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
