---
title: 'Graph calendar mapping to records and events'
type: 'feature'
created: '2026-09-02'
status: 'ready-for-dev'
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
- **`Meeting.scope` comes from connector configuration mapping an Outlook category to a project.** The PM tags the meeting in Outlook and the mapping lives in `connectors/` — application scope, Tier 1, gitignored (`scope_model.py:451`) — so it is per-machine and uncommitted, and stays out of `config.toml`, whose vocabulary is closed. **An unmapped row is personal**: it is the PM's own meeting, it still appears in the personal dashboard, and nothing is silently dropped. A Graph event carries no pm-ai scope, so the mapping is the only honest source.
- **An all-day row records `0` minutes.** An all-day entry is a marker — a birthday, an OOO block, a sprint boundary — not a meeting, so it contributes nothing to cost while still appearing in Time-Critical Activities. 1440 is the answer the spec calls wrong by an order of magnitude: five attendees at £100/h would report £12,000 for a birthday. `duration_minutes` is `int` on both `Meeting` and `MeetingHeldPayload`, so the convention must be a whole number, which `0` is.
- **`tentative` is stored and `stale` is derived.** Tentative is provider data — Graph's response status — and `11a`'s record carries it. Stale means "absent from a window we actually harvested", which `8a`'s `CoverageWindow` makes derivable, so storing it would be a second source of truth that goes wrong quietly.
- **Records leave the connector through `HarvestResult`.** It carries events, cursor and coverage today, and a record cannot be derived from an event: `MeetingHeldPayload` holds `meeting_id`, `attendee_count` and `duration_minutes` — a *count*, not the attendee list — and no `title`, `start` or `calendar_event_ref`. So the result widens, this slice returns domain records alongside events, and `app/pipelines.py` writes them through `11a`'s accessor before persisting the events. `pm_ai.connectors` may not import `pm_ai.storage`, so the write cannot happen here.
- **`emits()` returns exactly `{CALENDAR_EVENT_HELD}`.** `MESSAGE_POSTED` joins it in `33d`.
- **A connector mints no event id** (AD-34) and **never asserts `Provenance.EXTERNAL`** (AD-36) — it emits `UNKNOWN` and `core.normalize` decides, as `gitlab.py:51-57` does. Hard-coding `EXTERNAL` would make pm-ai's own writes admissible as evidence that its own promises were kept.
- **The record is written before its event is emitted.** The event cites `meeting:<id>`, so emitting it after a failed `meetings/` write leaves an unresolvable AD-33 citation.
- **`calendar_event_ref` carries the Graph event id**, and `33e` resolves the join URL from that id when it needs one. `Meeting` has no `join_url` field and this slice does not add one.
- **A re-harvest never silently overwrites a hand-edit, and `11a`'s regions are why it need not.** pm-ai owns the record's fields; `## Notes` is preserved verbatim on rewrite. So `11a`'s replace-on-rewrite rule and this clause are the same rule read from two ends, rather than the contradiction the review found between them.

**Ask First:** Nothing. Both questions were decided on 2026-09-03 and are stated in the Always clauses above.

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
| Cancelled row, never written | marked cancelled, no existing record | not written | N/A |
| Cancelled after an earlier harvest wrote it | a record exists, the row is now cancelled | the existing record is marked, not left as upcoming — "not written" is a no-op against a record already on disk | N/A |
| Row mapped to a project | its Outlook category is in the mapping | record lands in that project's `meetings/` | N/A |
| Row with no mapped category | any untagged meeting | personal scope | N/A |
| Category maps to an unregistered project | a stale mapping entry | refused, naming the category and the project id | `UnknownProject` |
| Declined by the PM | declined, still on the calendar | not written | N/A |
| Tentative | tentatively accepted | recorded with `tentative` set — a stored field, because it is provider data | N/A |
| All-day row | midnight to midnight | `duration_minutes` is `0`, so `man_hour_cost` is `0.0` at any attendee count | N/A |
| Attendee edges | null `emailAddress`, a distribution list, zero attendees | null resolves to `UNRESOLVED`; group expansion stated; zero recorded as zero | N/A |
| No online meeting | a room booking with no join reference | record written; `calendar_event_ref` still holds the event id | N/A |
| Record write fails | `meetings/` refuses | the event is **not** emitted | propagated |
| Re-harvest over a hand-edit | the record changed since it was written | refused or merged, never silently replaced | surfaced |
| Row deleted upstream | previously written, absent from a window that **was** harvested | derived as stale from `8a`'s coverage; it stops being time-critical. Absent from a window that was *not* harvested means nothing | N/A |
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
- Given `GraphConnector`, then `isinstance(GraphConnector(...), ConnectorPort)` holds **and `sample_events()` returns a non-empty tuple** — `8d` declares both members on the port, and the conformance test at `test_domain_invariants.py:793-826` enumerates only `ScopePaths`, `GitVcs` and `StorageService`, so it passes before this slice starts. `8d` extends it to connectors; this slice asserts its own adapter against it.
- Given the AD-27 and AD-34 tests with `GraphConnector` registered, then both pass and `all_connectors()` returns two connectors — `8d`'s non-empty assertion now has a second member.
- Given a `meetings/` write that refuses, then no event is persisted.
- Given an all-day row with five attendees, then `man_hour_cost` is `0.0` — a **value**, not a self-reference. "Equals the stated convention" was satisfied by any constant the implementer wrote down, including the 1440 this spec calls wrong by an order of magnitude.
- Given a row whose category is in the mapping, then the record lands in that project's tree and not the personal one; and given an untagged row, then it lands in the personal tree — the mapping is the only thing deciding scope, so both directions are asserted.
- Given a record written by an earlier harvest with hand-written `## Notes`, when the same row is re-harvested, then the notes are byte-identical afterwards — `11a` preserves the region, and this is the slice that would otherwise overwrite it.
- Given a `HarvestResult` from this connector, then it carries the records **and** the events, and `app` writes the records first — asserted on the order, because an event citing `meeting:<id>` emitted before its record leaves an unresolvable AD-33 citation.

## Spec Change Log

- **2026-09-03, both `Ask First` clauses answered, and the review's findings applied.**
  **`Meeting.scope`** comes from connector configuration mapping an Outlook category to a project, with an unmapped row defaulting to personal — the PM tags the meeting in a UI they already use, and the mapping sits in `connectors/`, per-machine and uncommitted. **An all-day row records `0` minutes**, which makes the criterion a value rather than the self-reference the review's C6 found: "equals the stated convention" was satisfied by any constant, including the 1440 this spec itself calls wrong by an order of magnitude.
  **Records had no carrier** (A7). `HarvestResult` holds events, cursor and coverage, and a record cannot be derived from an event — `MeetingHeldPayload` carries an attendee *count*, not the list, and no title, start or `calendar_event_ref`. The result widens, and `app` writes the records because `pm_ai.connectors` may not import `pm_ai.storage`.
  **`tentative` and `stale` had no fields.** Tentative is provider data and is stored on `11a`'s record; stale is derived from `8a`'s coverage, so there is one source of truth for each rather than a stored flag that goes wrong quietly.
  **"Not written" was a no-op against an existing record.** A row cancelled *after* an earlier harvest wrote it would have stayed upcoming forever; it is now marked.
  **The port-conformance criterion could not be met by anything this slice changes** (C5). `test_adapters_satisfy_the_ports_they_are_declared_against` enumerates three adapters and no connector, so it passes before this slice starts. `8d` extends it; this slice asserts its own adapter through it.
  **The overwrite contradiction with `11a` is resolved** (B11), by `11a`'s machine-owned and human-owned regions rather than by either spec giving way.
  **A stale category mapping gained a row:** a category pointing at an unregistered project is refused by name.

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
