---
title: 'The dashboard reads the whole local day'
type: 'bugfix'
created: '2026-10-16'
status: 'done'
baseline_commit: 'ac7de7b231dd90fa892e1a2c897028736da0c7f9'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The dashboard shows midnight to midnight in `display_timezone`, but it asks the calendar only for the harvest's window: from the render's instant minus the connector's first-run reach-back, to the render's instant plus the window width. Measured 2026-10-16 with a 240-minute width, the 07:00 render listed 3 of the day's 7 meetings and a 10:31 render listed 6; a late-evening render also misses the morning whenever the reach-back is shorter than the time since midnight.

**Approach:** The render asks each calendar for exactly the display day — the two instants the pipeline already computes — while the scheduled harvest keeps its own window unchanged.

## Boundaries & Constraints

**Always:**
- **The render asks for the whole local day.** The range sent to the calendar is the display day's start and end in `display_timezone`, as the pipeline already computes them: local midnight to the next local midnight, converted to UTC.
  - A daylight-saving day is therefore 23 or 25 hours.
  - In a zone whose clocks change at midnight, the day starts at the first local instant that exists.
  - The same two instants select the meetings shown (the existing overlap rule), so the day asked for and the day shown cannot disagree.
- **An all-day entry is placed by its own date.** An all-day entry is a zero-length marker. It is on the page when the calendar date Graph gives it equals the display date. It is not placed by converting its start to the display zone: in a zone west of UTC, that conversion would move a midnight-UTC start onto the previous day.
- **`23b`'s rules stand.**
  - Meetings not yet ended are read live and persisted nowhere.
  - Meetings that have ended come back as records, for display only.
  - The render writes no meeting record, saves no cursor, emits no event, and walks no channel or chat.
  - The connector's split between ended and not-yet-ended meetings is unchanged.
- **The exclusions stand.**
  - A meeting the PM declined is left out, wherever it falls.
  - A cancelled meeting is left out while it has not ended (`_is_the_pms_meeting`).
- **An ended cancelled meeting is listed, marked as cancelled** (Andrei's decision on field-test finding 7, 2026-10-16). The connector already keeps it: `_is_the_pms_meeting` drops a cancelled row only while `end > now`, so an ended one comes back with the day's other ended meetings. What is missing is the mark — `Meeting` carries no cancellation, so the page lists it as an ordinary ended meeting. The row's `is_cancelled` is carried on `Meeting` in memory only, as `tentative` is, absent from the record fields, and the meeting's line adds `cancelled` to its facts. Its record and citations are unchanged; the harvest is unchanged.
  - Copies of one meeting across two tenants are matched as `23b` matches them.
- **The read stays bounded (`9b`).** The live calendar read is one bounded step held to the per-connector fetch budget. If that step is abandoned at its deadline, the calendar counts as unread, never as a failed render. A day is far narrower than the widest span asked for in one request (30 days), so it is one request, walked page by page.
- **One range, never two.** The calendar fetch is given either the harvest's cursor or the render's range. Given both, it refuses with a `ValueError` naming both, rather than mixing them.
- **The harvest is untouched.** `WindowPolicy.window`, its width, its first-run reach-back, the cursor and coverage rules, and transcript acquisition's reach-back behave exactly as before.
- **A calendar connector without the calendar-only read** is still asked through its harvest, and the overlap rule selects the day from what it returns. Graph is the only calendar connector in this build.
- **Depends on spine amendment: AD-9.** AD-9 closes the connector surface. `CalendarReadPort.read_calendar` sits beside that surface as an optional read, inherited from `33d`, and its argument becomes the day's range. The spine names neither the optional read nor its argument, so the amendment records both.

**Ask First:** Nothing.

**Never:**
- No change to the harvest window, its settings, or `connector add graph`'s questions.
- No persistence of a meeting not yet ended, and no harvested records used as the day's source.
- No new setting.
- No change to what the page says about an unread or partially read calendar.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 07:00 render, afternoon meeting | zone UTC−5; meetings at 09:00, 11:30, 14:00, 16:30 local | all four listed; the read was handed 00:00 to 24:00 local | N/A |
| Late evening render | 23:30 local; meetings at 08:00 and 13:00, both ended; reach-back shorter than 23 h | both listed as ended; nothing from tomorrow | N/A |
| Crossing midnight into today | 23:00 yesterday to 01:00 today | listed with its date (overlap rule) | N/A |
| Crossing midnight out of today | 23:30 today to 00:30 tomorrow | listed | N/A |
| Starting just before midnight | 23:58 today | listed; not marked as starting after the range | N/A |
| DST change at 02:00 | the 23-hour and 25-hour days | the range is local midnight to local midnight; a meeting in the last hour is listed | N/A |
| DST change at midnight | a zone whose clocks jump from 00:00 to 01:00 | the range starts at the first existing local instant: 01:00 to 24:00, 23 hours; the fall-back day is 25 hours (measured with `_day_bounds` on 2026-10-16 for three such zones) | N/A |
| All-day, zone west of UTC | UTC−5; an all-day entry dated today | listed today, not yesterday | N/A |
| All-day, zone east of UTC | UTC+9; all-day entries dated yesterday and today | only today's is listed | N/A |
| Declined | response `declined`, any time today | left out | N/A |
| Cancelled, not ended | 15:00 today, render at 07:00 | left out | N/A |
| Cancelled, ended | 08:00 today, render at 10:00 | listed as ended, its facts including `cancelled` | N/A |
| Read times out | the bounded step's deadline passes | "calendar could not be read"; file written | as `9b` |
| Two tenants | two Graph rows | each read for the same local day; merged and deduplicated | one failing → partial |
| Both range and cursor | the fetch is handed both | refused with `ValueError` naming both | a bug; caught by `_ask` as today |
| Harvest after the fix | the scheduled harvest runs | the same request range as before the fix | N/A |

</frozen-after-approval>

## Code Map

Verified against `a1182f8` on 2026-10-16; re-verified against `ac7de7b` on 2026-10-17 (no file in this map changed; `prepare_dashboard` starts at `:1602`, `:1627` is its `_todays_calendar` call).

- `pm_ai/connectors/graph/calendar.py:602-633` -- `WindowPolicy.window`, the harvest's range: `start = now - first_run_reach_back` with no cursor (`:624`), `end = now + width` (`:633`). Unchanged.
- `pm_ai/connectors/graph/calendar.py:863`, `:893` -- `GraphCalendarFetch.fetch`, which takes its range from the policy at `:893` and gains a given range. Also used:
  - `:478` `CalendarWindow`;
  - `:146` `MAX_SERVABLE_SPAN`;
  - `:1116` `is_all_day`, read off the row;
  - `:1240-1278` `_clock_flag`, which measures a row's start against the range's end.
- `pm_ai/connectors/graph/__init__.py` --
  - `:1003` `read_calendar(since)`, today `_read(since, channels=(), chats=())` with an empty cursor;
  - `:1047` the `fetch` call;
  - `:1278-1320` the split between ended and not-yet-ended meetings;
  - `:1561` `_is_the_pms_meeting`;
  - `:2026-2040` `_minutes_of`, which gives an all-day row zero minutes.
- `pm_ai/domain/meetings.py:100` -- `Meeting`, which has no all-day date and no cancellation; each is carried in memory only, as `tentative` is.
- `pm_ai/connectors/graph/calendar.py:688`, `:1117` -- `CalendarRow.is_cancelled`, read off the row; `pm_ai/core/rendering.py:572-587` -- `_meeting_line`, where `tentative` already adds a fact.
- `pm_ai/ports/__init__.py:96-109` -- `CalendarReadPort.read_calendar(since: Cursor)`, documented as reading "exactly as `harvest` reads it".
- `pm_ai/app/pipelines.py` --
  - `:1627` `prepare_dashboard`, which calls `_todays_calendar`;
  - `:1840-1919` `_todays_calendar`, which computes the day at `:1873`;
  - `:1922` `_ask_within`;
  - `:1936-1958` `_ask`;
  - `:1998` `_day_bounds`;
  - `:2014-2031` `_on_day`, which places a zero-length row by its start.
- Tests to change --
  - `tests/connectors/test_graph_channel_messages.py:1159`, the "exactly as harvest" test, rewritten;
  - `tests/connectors/test_graph_chat_messages.py:824`, which calls `read_calendar(Cursor())`;
  - `tests/slice/test_dashboard_slice.py:81` `_Calendar`, a double offering only `harvest`;
  - `tests/slice/test_dashboard_slice.py:394`, the empty-cursor assertion, still true of the fallback.
- `_bmad-output/specs/spec-pm-ai/stories/23b-dashboard-pipeline.md:80` -- a Code Map note saying the forward width "is what makes the day ahead readable at all", no longer true.

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/ports/__init__.py` -- `CalendarReadPort.read_calendar(start, end)`, aware UTC, replacing the always-empty cursor; the docstring says it reads the calendar alone over that range -- the render names its day
- [x] `pm_ai/connectors/graph/calendar.py` -- `fetch` takes an optional given range and walks it instead of the policy's window; a range given together with a cursor is refused -- one fetch, two callers
- [x] `pm_ai/connectors/graph/__init__.py`, `pm_ai/domain/meetings.py` -- `read_calendar` builds the range and reads over it; an all-day row's own date is carried on `Meeting` in memory only, absent from the record fields like `tentative` -- the harvest stays as it was
- [x] `pm_ai/connectors/graph/__init__.py`, `pm_ai/domain/meetings.py`, `pm_ai/core/rendering.py` -- `Meeting.cancelled` set from the row's `is_cancelled`, in memory only and absent from the record fields; `_meeting_line` adds `cancelled` -- an ended cancellation is shown and marked (finding 7)
- [x] `pm_ai/app/pipelines.py` -- `_todays_calendar` hands `_day_bounds`' instants through `_ask_within` and `_ask`; `_on_day` places a marker that carries a date by that date -- the day asked is the day shown
- [x] `tests/slice/test_dashboard_slice.py` -- a double implementing `read_calendar(start, end)` that records its arguments, and a port test that asserts the parameter names -- a structural `isinstance` check passes with the old signature too, so the range must be asserted, not inferred
- [x] `tests/connectors/test_graph_channel_messages.py`, `tests/connectors/test_graph_chat_messages.py`, `tests/slice/test_dashboard_slice.py` -- one test per matrix row; the request's `startDateTime` and `endDateTime` asserted on the recorded URL; the "exactly as harvest" test replaced by one asserting the harvest's range is unchanged -- the matrix is the contract
- [x] `_bmad-output/specs/spec-pm-ai/stories/23b-dashboard-pipeline.md` -- a dated Change Log line saying the dashboard no longer depends on the harvest width (`:80`) -- the stale note is on record

**Acceptance Criteria:**
- Given the daemon with one Graph connector, when the 07:00 render and a scheduled harvest both run, then the render's calendar request covered the display day and the harvest's covered the range `WindowPolicy.window` computes, each asserted on the recorded URLs.
- Given any render, when it finishes, then no meeting record, cursor, coverage window or event-log entry was written, asserted against storage.
- Given the change, when `uv run pytest` runs, then everything passes with the skip count unchanged from the baseline at the slice's start.

## Spec Change Log

- **2026-10-16, created.**
  - **Source:** the field-test bug in Andrei's decision of 2026-10-16. The dashboard's day was read through the harvest window: 3 of 7 meetings at 07:00, 6 at 10:31. First slice of wave 3.
  - **Carries:** the render's request is the display day; the harvest is unchanged; `23b`'s rules, the bounded read (`9b`), and the declined and future-cancelled exclusions are kept.
  - **Decided here (routine, Claude):** `read_calendar` takes the day's two instants in place of the always-empty cursor.
- **2026-10-16, review.**
  - **Added:** all-day entries placed by their own date, and the fetch refuses a range given together with a cursor.
  - **Matrix rows added:** a DST change at midnight, a meeting starting at 23:58, all-day entries west and east of UTC, and range-and-cursor.
  - **Tests:** a recording `read_calendar(start, end)` double and a port test on the parameter names.
  - **Stated:** the AD-9 spine amendment. Added a task for a Change Log line in `23b`.
  - **Wording:** the example zone is now UTC−5; "flagged" became "marked"; the ACs were replaced with system-level checks; the Intent was trimmed.
  - **Rejected:** changing `_day_bounds` for zones that change clocks at midnight. Measured on 2026-10-16, it already yields the correct 23- and 25-hour days there, so that finding became a matrix row and a test instead.
- **2026-10-16, Andrei's decision on field-test finding 7.** An ended cancelled meeting is shown on the dashboard, marked as cancelled, not left out; a cancelled meeting not yet ended stays left out. Read against the code: the connector already keeps the ended row, so the change is the in-memory flag and the line's mark. Always bullet, matrix row, Code Map line and task added.

- **2026-10-17, build review (blind hunter, edge-case hunter, verification gap).** Patched in the code, with the frozen block unchanged:
  - **All-day lines.** An all-day marker's line reads `all day`, with no start time, no ended/in progress/upcoming state and no `0 min`, and markers come before the timed lines. The "have ended" collapse, its count and its "last finished at" are decided over timed meetings only. Without this, west of UTC a correctly placed marker would read as yesterday 19:00 and "ended", and a day of markers alone would say its meetings had ended.
  - `isAllDay` is read once.
  - The display date is derived from `_day_bounds`' start.
  - Test assertions that could not fail were fixed. Acceptance criterion 1 is now tested with one daemon and one connector, and the partial-day Graph test names the failing tenant.
  - **Deferred** to `deferred-work.md`: multi-day all-day entries (Andrei's decision, since the frozen rule places a marker by its start date), and Graph's wire spelling of an all-day start (to be measured from a recorded row).

## Design Notes

**Why the port's argument changes instead of adding a method.** `read_calendar` has one caller, which always passes an empty cursor. A cursor means "where the last harvest stopped", which a live read of one day does not have, so handing the method the day makes it say what it reads. `CalendarWindow` still refuses a naive or reversed range, and `_clock_flag` measures a row's start against the range's end, so a meeting starting at 23:58 is not marked.

**All-day entries.** No recorded fixture holds an all-day row, so how Graph spells such a row's start on the wire is not measured here. Placing the marker by the date Graph gives it is correct whichever spelling Graph uses. The request range overlaps a neighbouring day's all-day entry by up to the zone's offset, and placing by date drops that neighbour. The date is carried in memory only, like `tentative`, so no record grammar changes.

**The overlap filter stays.** Graph's calendar view returns every occurrence that overlaps the range (the connector's note at `calendar.py:1258`).

## Verification

**Commands:**
- `uv run pytest tests/slice/test_dashboard_slice.py tests/connectors/test_graph_channel_messages.py tests/connectors/test_graph_chat_messages.py -q` -- expected: every matrix row passes
- `uv run pytest -q` -- expected: all pass; skip count unchanged from the baseline
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks (if no CLI):**
- On a day with an afternoon meeting, `pm-ai dashboard` run before 11:00 lists it.

## Suggested Review Order

**The day asked is the day shown**

- Entry point: the render computes the display day once and asks for exactly it.
  [`pipelines.py:1877`](../../../../pm_ai/app/pipelines.py#L1877)

- Calendar-only read gets the range; connectors without it fall back to harvest.
  [`pipelines.py:1970`](../../../../pm_ai/app/pipelines.py#L1970)

- The port now takes the day's two UTC instants instead of an always-empty cursor.
  [`ports/__init__.py:108`](../../../../pm_ai/ports/__init__.py#L108)

- Graph passes the range as a span; the harvest path is untouched.
  [`graph/__init__.py:1019`](../../../../pm_ai/connectors/graph/__init__.py#L1019)

- One range, never two: a cursor plus a range is refused.
  [`calendar.py:910`](../../../../pm_ai/connectors/graph/calendar.py#L910)

**All-day entries placed by their own date**

- The date Graph wrote, read before any zone conversion.
  [`calendar.py:1154`](../../../../pm_ai/connectors/graph/calendar.py#L1154)

- A marker carrying a date is on the page only on that date.
  [`pipelines.py:2043`](../../../../pm_ai/app/pipelines.py#L2043)

- All-day line form; timed meetings alone decide the "have ended" collapse.
  [`rendering.py:571`](../../../../pm_ai/core/rendering.py#L571)

**Ended cancelled meetings, marked**

- In-memory flags on tentative's precedent; no record field.
  [`meetings.py:174`](../../../../pm_ai/domain/meetings.py#L174)

- The line adds `cancelled` to its facts.
  [`rendering.py:611`](../../../../pm_ai/core/rendering.py#L611)

- `as_stored` clears both flags so the record round-trip holds.
  [`meeting_records.py:374`](../../../../pm_ai/core/meeting_records.py#L374)

**Tests**

- Acceptance criterion 1: one daemon, render then harvest, each URL range asserted.
  [`test_graph_channel_messages.py:1290`](../../../../tests/connectors/test_graph_channel_messages.py#L1290)

- All-day west of UTC: listed today, rendered as `all day`.
  [`test_graph_channel_messages.py:1377`](../../../../tests/connectors/test_graph_channel_messages.py#L1377)

- Recording double for `read_calendar(start, end)`; slice matrix rows follow.
  [`test_dashboard_slice.py:1433`](../../../../tests/slice/test_dashboard_slice.py#L1433)
