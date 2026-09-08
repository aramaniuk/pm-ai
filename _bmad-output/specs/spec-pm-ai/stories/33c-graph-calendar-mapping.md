---
title: 'Graph calendar mapping to records and events'
type: 'feature'
created: '2026-09-02'
status: 'done'
review_loop_iteration: 0
baseline_commit: '19b9ef2835be943f2cff53458610482bf1731813'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `33b` returns UTC-normalized calendar rows and `11a` can persist meetings, but nothing turns one into the other. This is where a calendar event becomes pm-ai's model, and the modelling decisions are the load-bearing ones: which rows become Tier-1 records, which become events, and what a connector is allowed to assert.

Split from the original `33b` on 2026-09-02 at the sizing gate.

**Approach:** `GraphConnector` satisfying `ConnectorPort`, mapping rows to `Meeting` records and `CALENDAR_EVENT_HELD` events, registered through `8d`.

## Boundaries & Constraints

**Always:**
- **Upcoming meetings are neither records nor events — they are read live.** Decided 2026-09-07: the calendar is the source of truth for a meeting that has not happened, and a local copy of a row that can move or vanish outside pm-ai cannot be kept accurate. So an upcoming row is mapped to a `Meeting` in memory and handed to whoever asked; nothing is persisted. Only an **ended** row is written to `meetings/` via `11a` and emits `CALENDAR_EVENT_HELD`. The closed-enumeration constraint still holds and still matters — `MeetingHeldPayload` carries no title or start (`events.py:130-133`) and a connector may never open the enumeration (AD-27) — which is why an upcoming row cannot be an event either, and why `23b` reaches this fetch rather than the event log for the day ahead.
- **`Meeting.scope` comes from connector configuration mapping an Outlook category to a project.** The PM tags the meeting in Outlook and the mapping lives in `connectors/` — application scope, Tier 1, gitignored (`scope_model.py:451`) — so it is per-machine and uncommitted, and stays out of `config.toml`, whose vocabulary is closed. **An unmapped row is personal**: it is the PM's own meeting, it still appears in the personal dashboard, and nothing is silently dropped. A Graph event carries no pm-ai scope, so the mapping is the only honest source.
- **An all-day row records `0` minutes.** An all-day entry is a marker — a birthday, an OOO block, a sprint boundary — not a meeting, so it contributes nothing to cost while still appearing in Time-Critical Activities. 1440 is the answer the spec calls wrong by an order of magnitude: five attendees at £100/h would report £12,000 for a birthday. `duration_minutes` is `int` on both `Meeting` and `MeetingHeldPayload`, so the convention must be a whole number, which `0` is.
- **`tentative` is carried, never stored, and `stale` is gone.** Tentative is a response status on a meeting that has not occurred, so it rides on the in-memory `Meeting` the dashboard renders and reaches no file — `11a` no longer has the field. `stale` meant "absent from a window we harvested", which is a cancellation; with no stored future record there is nothing to go stale, and a row that disappears from the calendar simply is not in the next live read.
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
| Upcoming row | starts in 3h | mapped to a `Meeting` and returned; **nothing** written, **no** event emitted | N/A |
| Ended row | finished yesterday | `CALENDAR_EVENT_HELD` emitted with attendee count and duration | N/A |
| In progress | started, not ended | mapped and returned, nothing written, no event — it has not been held yet | N/A |
| `end` exactly equals `now` | boundary instant | bounds inclusive on one side only, so ended and in-progress are disjoint | N/A |
| Recurring series | a weekly recurrence in the window | each occurrence is its own `Meeting`; the ended ones are each their own record | N/A |
| Modified occurrence | one instance moved, with its own id | keyed on the occurrence id, not the series master's | N/A |
| Cancelled upcoming row | marked cancelled, still in the window | not returned and not written — it is not a meeting the PM has | N/A |
| Cancelled after it had already ended | an ended row, later marked cancelled | the record stands — a meeting that happened is not undone by a later calendar edit, and its citations must keep resolving | N/A |
| Row mapped to a project | its Outlook category is in the mapping | record lands in that project's `meetings/` | N/A |
| Row with no mapped category | any untagged meeting | personal scope | N/A |
| Category maps to an unregistered project | a stale mapping entry | refused, naming the category and the project id | `UnknownProject` |
| Declined by the PM | declined, still on the calendar | not written | N/A |
| Tentative | tentatively accepted | carried on the returned `Meeting` for display; stored nowhere, because it is a question about a meeting that has not happened | N/A |
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
- `pm_ai/core/meeting_records.py` -- `11a`'s accessor and its safe-name encoding; **changed** by this slice — `as_stored` clears `tentative`, because the field joined `Meeting` here and is not a field of the record
- `pm_ai/domain/meetings.py:48-50` -- `man_hour_cost`, which the all-day convention feeds
- `pm_ai/app/wiring.py:41` -- `connectors: dict[str, GitLabConnectorAdapter]`, an annotation mypy rejects once a Graph connector is registered
- `tests/architecture/test_domain_invariants.py:94,483,793-826` -- the two tests `8d` unskipped, and the port-conformance test that covers no connector
- `pm_ai/domain/meetings.py:17-33` -- `Meeting`, which gains `tentative`; `11a` removed the field from the *record*, and the frozen block requires it on the in-memory object
- `pm_ai/domain/harvest.py:167-192` -- `HarvestResult`, which widens to carry domain records beside `events`
- `pm_ai/connectors/graph/calendar.py:531-576` -- `WindowPolicy`, whose two widths are required with no defaults; `:548` carries a docstring the 2026-09-07 renegotiation falsified
- `pm_ai/connectors/graph/calendar.py:635-707,769-794` -- `CalendarRow`, `RowRefusal` and `GraphCalendarFetch.fetch`, the rows this slice maps
- `pm_ai/app/wiring.py:273-362` -- `_enrolled_connectors`, which skips every `system != "gitlab"` row and says so: "the Graph rows here arrive with 33b's calendar adapter"
- `pm_ai/core/config.py:97-165` -- `Config` and `ACCEPTED_KEYS`, the closed three-key vocabulary the window widths must **not** join

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/connectors/graph/__init__.py` -- add `GraphConnector` satisfying `ConnectorPort`, registered through `8d`
- [x] `pm_ai/domain/meetings.py` -- add `tentative: bool = False` to `Meeting`; carried in memory, written to no record
- [x] `pm_ai/core/meeting_records.py` -- clear `tentative` in `as_stored`, so the render/parse round trip stays true for a meeting a connector marked
- [x] `pm_ai/domain/harvest.py` -- widen `HarvestResult` to carry domain records beside `events`, with `__post_init__` keeping the outcome honest about them
- [x] `pm_ai/app/pipelines.py` -- write the records through `11a`'s `MeetingRecords` accessor **before** persisting the events
- [x] `pm_ai/app/wiring.py` -- read the Graph enrolment row in `_enrolled_connectors` and build a `GraphConnector` from it; widen `Daemon.connectors` to `dict[str, ConnectorPort]` -- mypy, gated inside pytest since story 1k, rejects the current annotation
- [x] `pm_ai/connectors/graph/calendar.py:548` -- correct the `WindowPolicy.width` docstring: `33c` returns upcoming meetings live and records none. The forward reach survives unchanged; only the reason stated for it was falsified
- [x] `tests/connectors/test_graph_calendar_mapping.py` -- the matrix against `33b`'s row fixtures

**Acceptance Criteria:**
- Given a window containing one ended and one upcoming row, then exactly one `CALENDAR_EVENT_HELD` is persisted, exactly **one** `meetings/` record exists, and both rows are returned to the caller — the past/future split, asserted rather than described.
- Given every emitted event, then `authored_by` is `Provenance.UNKNOWN` — asserted, because AD-36's rule lives only in a comment and the AD-34 test inspects only the absent `id`.
- Given `GraphConnector`, then `isinstance(GraphConnector(...), ConnectorPort)` holds **and `sample_events()` returns a non-empty tuple** — `8d` declares both members on the port, and the conformance test at `test_domain_invariants.py:793-826` enumerates only `ScopePaths`, `GitVcs` and `StorageService`, so it passes before this slice starts. `8d` extends it to connectors; this slice asserts its own adapter against it.
- Given the AD-27 and AD-34 tests with `GraphConnector` registered, then both pass and `all_connectors()` returns two connectors — `8d`'s non-empty assertion now has a second member.
- Given a `meetings/` write that refuses, then no event is persisted.
- Given an all-day row with five attendees, then `man_hour_cost` is `0.0` — a **value**, not a self-reference. "Equals the stated convention" was satisfied by any constant the implementer wrote down, including the 1440 this spec calls wrong by an order of magnitude.
- Given a row whose category is in the mapping, then the record lands in that project's tree and not the personal one; and given an untagged row, then it lands in the personal tree — the mapping is the only thing deciding scope, so both directions are asserted.
- Given a record written by an earlier harvest with hand-written `## Notes`, when the same row is re-harvested, then the notes are byte-identical afterwards — `11a` preserves the region, and this is the slice that would otherwise overwrite it.
- Given a `HarvestResult` from this connector, then it carries the records **and** the events, and `app` writes the records first — asserted on the order, because an event citing `meeting:<id>` emitted before its record leaves an unresolvable AD-33 citation.
- Given a Graph enrolment row carrying both widths, then `build()` constructs a `GraphConnector` from them; and given a row missing either width, then the connector is **not** built and the missing key is named — no default is supplied, because a width accepted by silence is invisible in every composition that took it.
- Given `config.toml`, then `ACCEPTED_KEYS` is unchanged at three — the widths are per-machine connector configuration and the config vocabulary stays closed.
- Given a `Meeting` mapped from a tentatively-accepted row, then `tentative` is `True` on the returned object and appears nowhere in any file `11a` writes — asserted on the record's bytes, because "not stored" and "stored as false" read alike from the caller.

## Spec Change Log

- **2026-09-08, the review's findings applied, and two guards the implementation was missing.** Sixteen patches, all confirmed and several demonstrated against the running code. Two of them change what this connector refuses and belong here rather than only in a docstring:

  **A row whose subject the record grammar will not hold now refuses itself.** `_meeting` put `row.subject` onto `Meeting.title` ungated, so a subject carrying a C0 control character, or one long enough to blow `11a`'s field-line bound, raised `MalformedMeeting` out of `MeetingRecords.put` — inside `run_harvest`, with the whole batch in hand: nothing persisted, cursor not advanced. And it did not clear. The window reaches backward on every run, so the same row killed the same harvest every cycle until the PM edited Outlook, with nothing naming which row. Guarded now the way `_assert_citable` guards the id, for the reason that guard's docstring already gave, and the id gained the length half of the same bound.

  **The row's five settings, not four, and the fifth is defaulted.** `tenant` was read and defaulted to a literal `"organizations"` under a docstring saying nothing was defaulted. It is defaulted — to `GraphDeviceCodeAuth.tenant` itself now, rather than to a copy — and the minute counts are bounded at `MAX_SETTING_MINUTES`, ten years, because `timedelta` raises `OverflowError` past roughly 1.5e12 and `OverflowError` is not a `ValueError`: one mistyped digit in a hand-edited `connectors/` row took `build()` down, and with it `pm-ai doctor` — the command that diagnoses a mistyped `connectors/` row.

  The rest are refusals made narrower or made at all — a case-colliding category mapping, a non-string category key that used to arrive as the literal tag `"None"`, `harvest` raising past its own contract when `_result` faulted — and tests for the five behaviours that shipped unpinned: the injected credential and `wait`, `_persist_by_scope`'s single-scope and empty branches, and `HarvestResult`'s EMPTY-with-records refusal. `SAMPLE_NOW` is deleted; nothing read it.

- **2026-09-08, implemented, and six things the implementation found that the frozen block does not describe.** None of them changes the intent; each is a place the code had to answer a question the spec left to it, and each is stated here rather than only in a docstring.

  **Two carriers, not one.** `HarvestResult` gained `records` *and* `live`. The frozen block says the result widens to carry records, and separately that both an ended and an upcoming row are "returned to the caller" — those are two lists, because `records` is precisely the list `app` writes and a single list plus a boolean would put one `if` between the pipeline and persisting a meeting the calendar can move ten seconds later. `__post_init__` widened with them: a window of only upcoming rows is `HARVESTED`, and it earns its coverage, which the old "coverage with no events" refusal would have rejected.

  **`run_harvest` now persists per event scope.** A Graph harvest is the first to emit events in more than one scope — a project meeting's and a personal meeting's — and `assert_writable` (AD-38) refuses a personal-scoped event offered to a committed project log, correctly. One `persist_events` call per scope; for every connector that came before, every event carries the daemon's own scope and the grouping produces exactly the single call it replaced. The cost is that all-or-nothing is now per scope, which is the most atomicity two files in two trees can have.

  **A declined row is not returned either.** The matrix says "not written"; the code also keeps it out of `live`, because a dashboard showing a meeting its reader declined is showing them somebody else's calendar. Cancellation cuts the other way and only for the future half, which is what makes "the record stands" true without a delete: an ended row is recorded whatever `isCancelled` says.

  **`Meeting.attendees` holds distinct actors, and that under-counts.** `11a` refuses a repeated handle in a record — one person twice is two people in the Man-Hour Cost — and AD-34 collapses every handle pm-ai cannot resolve onto one `UNRESOLVED` actor, so a meeting of five strangers is one attendee in the record and `man_hour_cost` reports a fifth of the truth until the alias table is populated. The alternative was refusing every such meeting, which on a machine with an empty alias table is every meeting. `MeetingHeldPayload.attendee_count` carries the provider's own count beside it, so the measured number is not lost — and the two numbers deliberately differ.

  **A refreshed Graph credential is not written back.** `_graph_connector` hands `GraphDeviceCodeAuth` an `InMemoryRefreshTokenStore` seeded from the sealed store: the enrolled token is read at every start and a rotation AAD performs mid-run lives only for that process. Writing back needs an update path through `8b`'s sealed store that does not exist — `enrol_connector` is its only writer and it refuses an instance already configured — so the gap is recorded rather than closed with a second writer of `private/config.json` in `app`.

  **`UnknownProject` is declared twice, in two packages that cannot see each other.** `pm_ai.connectors` and `pm_ai.platform` are independent siblings, so the connector cannot raise the resolver's class. It raises its own, at construction of the mapping rather than mid-harvest, which is also the only moment a `ConnectorPort` may refuse at all. The duplication is the shape `DuplicateConnector` was moved to `pm_ai.ports` to avoid, and the move is not available here: the resolver's version is a `ScopePathError`, whose base lives in the module a caller of `ScopePathPort` is forbidden to import.

- **2026-09-08, the two harvest-window widths answered, and three gaps between the frozen text and the Code Map closed.** `33b` left `WindowPolicy.width` and `first_run_reach_back` required with no defaults, on the reasoning that a default here is this codebase answering a question the human reserved. `deferred-work.md` recorded the consequence: nothing in the shipped tree can construct a Graph calendar fetch, and `33c` is the slice that must.
  **The widths ride the per-machine Graph enrolment row in `connectors/`** — the file `_enrolled_connectors` already reads, and the same place this spec's frozen block puts the Outlook-category-to-project mapping. `config.toml`'s vocabulary stays closed at three keys: these are per-machine connector settings, and the frozen block already routes that class of setting here. A row missing either width builds no connector and names the missing key, rather than defaulting.
  **The values for this machine are `width = 24h` and `first_run_reach_back = 7d`** — above CAP-2's 240-minute floor, and reach-back at or above width, so `__post_init__` accepts both.
  **Three in-scope gaps between the frozen block and the Code Map**, all required by the frozen text and none of them listed: `Meeting` gains `tentative` (the frozen block requires it in memory; `11a` removed it from the record), `HarvestResult` widens to carry records (the frozen block says the result widens; the Code Map omits `harvest.py`), and `calendar.py:548` still says "`33c` writes upcoming meetings as records" — falsified by the 2026-09-07 renegotiation, while the forward reach it justifies survives.
  **Not changed, and checked:** `GraphAuthError` and `GraphUnreachable` stay in `auth.py`. The deferred entry predicts `33c` is where `pm_ai.core` gains something to catch; it is not. `ConnectorPort.harvest` returns a `HarvestResult` and `check_health` reports without raising, so `GraphConnector` converts both into a `HarvestFailure` inside the connectors layer — the sibling-adapter import `calendar.py:64` already makes — and the only caller, `pipelines.py:24`, has no `except`.

- **2026-09-07, renegotiated on instruction: an upcoming row is read live, never recorded.** The calendar owns a meeting that has not happened. A local copy of a row that can be moved or cancelled outside pm-ai cannot be kept accurate, so persisting it buys duplication plus the obligation to model staleness — and this slice was where that obligation showed up, as a cancellation-marking rewrite and a `tentative` field.
  **The past/future split survives and gets sharper.** It was already forced by a closed enumeration: `MeetingHeldPayload` has no title or start, so an upcoming meeting has no honest representation as an event. It now cuts between *returned* and *persisted* rather than between two kinds of record. Only an ended row becomes a `meetings/` record and a `CALENDAR_EVENT_HELD`.
  **Four matrix rows changed and one reversed.** Upcoming, in-progress and tentative rows are mapped and returned rather than written. The cancellation pair collapses: an upcoming cancelled row is simply not returned, and a row cancelled *after* it ended keeps its record — a meeting that happened is not undone by a later calendar edit, and its citations must keep resolving. That last row previously said the record was "marked, not left as upcoming", which no longer has a meaning.
  **`tentative` stays useful and stops being durable** — it rides the in-memory `Meeting` so the dashboard can show it, and reaches no file.

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

## Suggested Review Order

**Start here — the one modelling decision, in the three places it lands**

- Which rows are the PM's meeting at all: declined never, cancelled only while it has not ended.
  [`graph/__init__.py:764`](../../../../pm_ai/connectors/graph/__init__.py#L764)

- The past/future split itself — `end <= now` records and emits, anything later is handed back.
  [`graph/__init__.py:685`](../../../../pm_ai/connectors/graph/__init__.py#L685)

- Two carriers on the result, and why `records` is the list `app` writes rather than a flag.
  [`harvest.py:195`](../../../../pm_ai/domain/harvest.py#L195)

**What the connector may assert, and what it may not**

- One row as a `Meeting`: scope from the mapping, `tentative` in memory, the event id as the ref.
  [`graph/__init__.py:805`](../../../../pm_ai/connectors/graph/__init__.py#L805)

- The event — `Provenance.UNKNOWN` (AD-36), no minted id (AD-34), and the count that is not `len(attendees)`.
  [`graph/__init__.py:836`](../../../../pm_ai/connectors/graph/__init__.py#L836)

- An id that cannot be both a `meeting:` citation and a filename refuses its own row.
  [`graph/__init__.py:904`](../../../../pm_ai/connectors/graph/__init__.py#L904)

**Scope, which a Graph event does not carry**

- Category to project, casefolded, first match in the row's order, personal for everything else.
  [`graph/__init__.py:235`](../../../../pm_ai/connectors/graph/__init__.py#L235)

- A stale mapping refused at construction, and why it is not the resolver's `UnknownProject`.
  [`graph/__init__.py:159`](../../../../pm_ai/connectors/graph/__init__.py#L159)

**Cost, and the two places it could have been wrong by an order of magnitude**

- An all-day row is `0` minutes, so a birthday costs nothing.
  [`graph/__init__.py:124`](../../../../pm_ai/connectors/graph/__init__.py#L124)

- Distinct actors, the loss `11a`'s grammar forces, and where the measured count went instead.
  [`graph/__init__.py:1068`](../../../../pm_ai/connectors/graph/__init__.py#L1068)

**Composition**

- The record written before its event, because the event cites it.
  [`pipelines.py:70`](../../../../pm_ai/app/pipelines.py#L70)

- One `persist_events` per event scope — AD-38 refuses a personal event in a committed log.
  [`pipelines.py:90`](../../../../pm_ai/app/pipelines.py#L90)

- The enrolment row: five settings, four of them undefaulted, and a missing key said out loud.
  [`wiring.py:440`](../../../../pm_ai/app/wiring.py#L440)

**Where the frozen block reached beyond the Code Map**

- `tentative` rides the object and reaches no file — the field `11a` deliberately does not have.
  [`meetings.py:49`](../../../../pm_ai/domain/meetings.py#L49)

- `as_stored` clears it, so `11a`'s round-trip property stays true of a mapped meeting.
  [`meeting_records.py:283`](../../../../pm_ai/core/meeting_records.py#L283)

- The forward reach survives; the reason `33b` gave for it did not.
  [`calendar.py:546`](../../../../pm_ai/connectors/graph/calendar.py#L546)

**Peripherals**

- The matrix, one test per row, driven from `calendarView` JSON rather than hand-built rows.
  [`test_graph_calendar_mapping.py:1`](../../../../tests/connectors/test_graph_calendar_mapping.py#L1)

- The row that used to kill every later harvest: a subject the record grammar refuses.
  [`test_graph_calendar_mapping.py:749`](../../../../tests/connectors/test_graph_calendar_mapping.py#L749)

- The two injections composition makes, each pinned by a mutation that would otherwise ship green.
  [`test_graph_calendar_mapping.py:1116`](../../../../tests/connectors/test_graph_calendar_mapping.py#L1116)

- `EMPTY` may not sit beside records or live rows — the refusal's row in the honesty table.
  [`harvest.py:267`](../../../../pm_ai/domain/harvest.py#L267)
