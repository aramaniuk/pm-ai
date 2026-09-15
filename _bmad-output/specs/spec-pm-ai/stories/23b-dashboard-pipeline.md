---
title: 'Dashboard pipeline and pm-ai dashboard'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 1
baseline_commit: '37a915092d3c645d192bea57afe87f4e2bbc02df'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** After `23a` and `23d` the renderer is a pure function nothing calls, `11a` holds meetings, `22a` parses goals and `33b` fills both from Graph. Nothing joins them, and `daily_dashboard.md` is still never written. This is the last slice of wave 1 and the one that makes the prototype real.

**Approach:** Add `run_dashboard` to `pm_ai/app/pipelines.py` — read meetings, log entries and goals, render, write through `StorageService.write_artifact` — and expose it as `pm-ai dashboard`.

## Boundaries & Constraints

**Always:**
- **The pipeline lives in `app`.** It must touch storage, core and the scope model at once, which no layer below may do (AD-30) — the same reason `run_harvest` lives there.
- **The write goes through `write_artifact`**, which resolves the path from the declaration and decides sealing. `daily_dashboard.md` is a Tier-1 `File`, unencrypted, not gitignored (`scope_model.py:540`). Whole-file replacement is correct: this is a rendering, not a ledger, so the ledger refusal at `service.py:1116` does not apply and must not be worked around.
- **The default scope is personal**, because CAP-9 names `~/.manager-ai/memory/daily_dashboard.md`.
- **Which renderer runs is a branch on scope, and each gets only its own sources.** `23a`'s `render_dashboard` for personal, `23d`'s `render_project_dashboard` for a project. They are separate functions and the project one has no goals parameter, so this slice cannot hand it personal-scope data even by mistake (AD-25). There is no datasource list to consult — `project_scope_datasources` is not built.
- **The timezone is read once here and passed to every consumer.** `config.toml`'s `display_timezone` — **which makes `4g` a hard dependency of this slice, recorded 2026-09-15** — reaches both the renderer and the calendar read that selects the day, so the day boundary the dashboard shows and the day boundary its meetings were selected by cannot disagree. An unset zone is refused rather than defaulted to UTC.
- **This layer fetches the day's meetings, because the renderer may not.** Since 2026-09-07 an upcoming meeting is not persisted, so today's schedule is read live from `33b`'s fetch. `core` is I/O-free and `render_dashboard` is a pure function, so the call belongs here — the composition root reads, the renderer renders. A fetch that fails is passed through as a stated reason, not as an empty day: `8a`'s outcome and failure already tell ran-and-learned-nothing from ran-and-failed.
- **Which connector is asked is derived from what connectors declare, never from a vendor name.** The calendar is whichever enrolled connector's `emits()` contains `CALENDAR_EVENT_HELD` — a capability the port already declares, so this slice adds no second list to keep in step. **No enrolled connector declaring it is a third answer**, neither an empty day nor a failed fetch, and it reaches the renderer as its own value. Dressing it as a `HarvestFailure` would print `_retry_advice`'s "the connector reports this will not clear on its own" about a connector that was never enrolled — a claim about a thing that does not exist, in the one file the PM reads every morning.
- **Every calendar that answers is read, and no calendar's silence stops another's answer.** Two enrolled connectors are the ordinary two-tenant PM, not a misconfiguration, so their days are **merged into one schedule** rather than refused. Each is asked independently: one that fails takes down its own rows and nothing else.
- **Two copies of one meeting are matched on identity, never on resemblance.** `meeting_id` is Graph's per-mailbox `id`, so the same meeting cross-invited to two tenants arrives under two different ids and id-matching alone would list it twice. The key that survives the crossing is `iCalUId`, which `33b` does not read today; this slice adds it, carried **in memory only** exactly as `tentative` is, so no persisted record grammar changes. Two meetings match when both carry a non-empty `ical_uid` **and both the uid and the start are equal**; failing that, when `meeting_id` is equal and neither side carries a uid to judge by. The start is in the key because whether Graph reuses one `iCalUId` across a series' occurrences is unmeasured — the 2026-09-06 spike listed the field and never compared two occurrences — and the pair is correct either way: two tenants' copies of one meeting share the instant, two occurrences of a series do not. **Never on start and title**: two organisations each holding a "Weekly Sync" at 09:00 is a real double-booking, and merging it away hides the one thing the 07:00 reader most needs to see.
- **A day read in part is rendered in part, and says so.** When one connector answers and another fails, the meetings that arrived are listed **and** the calendar that could not be read is named. Data that is present is never discarded because data elsewhere is missing, and a partial day is never presented as a whole one.
- **The merge is deterministic.** Connectors are consulted in sorted instance order and the first copy of a matched pair is the one kept, so a re-run of unchanged inputs is byte-identical — including which of two copies supplied the title and the tentative flag.
- **The render is deterministic given its inputs**, so re-running produces the same file. Nothing about the output depends on how many times it has run.
- **A missing input is a stated section, not a failure.** No goals file, no meetings and an empty log all produce a valid dashboard.
- **Read and render fully before writing anything.** `write_artifact` replaces whole, so opening the target first and discovering a malformed input afterwards destroys yesterday's dashboard. This is why `23a` returns a string rather than writing as it goes.
- **Exit codes come from `4c`'s table** — `3` for a refusal, `1` for an unexpected exception. This slice may not add to it.

**Ask First:** Nothing. Whether a render appends an event-log entry was decided on 2026-09-03: **it does not.** The discriminator is whether an action changes truth or projects it. A dashboard is fully derivable from Tier 1 plus a clock, so a line per day per scope records nothing a reader could not reconstruct, and CAP-10's own retrospective counts are decisions, proposals and commitments — a render is none of them. `2c`'s vocabulary stays closed.

**Never:** No scheduler and no daemon — `pm-ai dashboard` runs once and exits, and the CLI may hold no scheduler (AD-7). The 07:00 deadline in CAP-9 is knowingly unmet until `9a`. **No harvest triggered from here**, which since 2026-09-07 is a narrower rule than the old wording read. The day's calendar fetch is a read: it persists nothing, writes no event, maps no `meetings/` record and does not advance a cursor. What this command may not do is call `run_harvest`, or anything that does those things — a render that also ingests makes `daily_dashboard.md` a side effect of a write path, and hands a failed harvest a second way to spoil the morning. The network I/O the old clause forbade outright is **accepted deliberately**: the decision that stopped persisting future meetings took that cost knowingly, in exchange for never showing a stale local copy of a row the calendar owns.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | meetings, entries and goals present | file written at the declared path; exit zero | N/A |
| First run ever | nothing harvested, no goals file | valid four-section file, every section stating its reason | N/A |
| Re-run | run twice with unchanged inputs | file byte-identical, **and the event log unchanged** — a render is not a state mutation | N/A |
| Malformed goals file | duplicate goal id | refused, naming the id; **the previous dashboard is left intact** | `MalformedGoals` |
| Calendar unreachable | the day's fetch fails or is refused | the `HarvestFailure` reaches the renderer **as the `meetings` argument**, and the file is still written at exit zero — a day whose calendar could not be read is a dashboard that says so, not a failed command. An empty sequence here would state a query result there was none of | reported, never raised |
| No calendar connector enrolled | nothing enrolled declares `CALENDAR_EVENT_HELD` | `NoCalendarConnector` reaches the renderer **as the `meetings` argument**, the file is written at exit zero, and the section says no connector can answer — never that one reported anything | reported, never raised |
| Two calendar connectors enrolled | both answer for the day | both answers merged into one schedule; file written at exit zero — two tenants is a configuration, not a fault | N/A |
| One meeting in two calendars | a meeting cross-invited to both tenants | listed **once**, matched on `ical_uid` **and** start, failing that on `meeting_id` | N/A |
| Two occurrences of one series today | a recurring meeting twice on the display day | **both** listed — whether Graph reuses one `iCalUId` across occurrences is unmeasured, and the start in the key makes the answer not matter | N/A |
| Two meetings, one `meeting_id`, two uids | distinct non-empty `ical_uid`s | **both** listed — a uid that disagrees is evidence of difference, not a tie to break by id | N/A |
| Two meetings at one time | two tenants each holding a different meeting at 09:00 | **both** listed — a genuine double-booking is the thing the day's reader most needs to see, and resemblance is not identity | N/A |
| One calendar answers, another fails | two connectors, one returns a `HarvestFailure` | `PartialCalendar` reaches the renderer: the meetings that arrived are listed **and** the calendar that could not be read is named; exit zero | reported, never raised |
| One calendar answers **nothing**, another fails | a connector reports an empty day, another fails | still `PartialCalendar`, with no meetings — a calendar that answered and held nothing is not a calendar that could not be read, and the `HarvestFailure` branch would claim the day is unknown when one calendar measured it | reported, never raised |
| Every calendar fails | two connectors, both fail | the `HarvestFailure` branch, as with one — nothing was read, and the section says so rather than showing an empty day | reported, never raised |
| Re-run with two connectors | unchanged inputs, run twice | byte-identical: sorted instance order decides which copy of a matched pair survives | N/A |
| Project scope requested | `--scope project:alpha` | `render_project_dashboard` runs; written to that project's tree; **no personal-scope artifact opened at all** | N/A |
| Timezone unset | `Config().display_timezone` | refused before any read — the day boundary may not be assumed | exit `3` |
| Timezone unknown to `zoneinfo` | a typo'd zone in `config.toml` | refused by `4a`'s loader before this slice runs | `ConfigRefused`, exit `3` |
| Master key absent | no key enrolled | dashboard still written — it is unencrypted, and this must not require a key | N/A |
| Malformed event-log segment | a hand-edited or corrupt segment | refused, naming the segment; previous dashboard intact | exit `3` |
| Undeclared scope requested | `--scope people:bob`, `--scope application` | refused by name — `daily_dashboard.md` is declared in two trees only | exit `3` |
| Unparseable scope argument | `--scope alpha`, `--scope project:` | refused with usage before any read | exit `2` |
| Write fails after a successful render | disk full, read-only filesystem | refused; the previous file survives, since `_replace` publishes atomically | exit `1` |
| Target is a directory | a directory where a `File` is declared | refused, naming the path and the expected node type | exit `1` |

</frozen-after-approval>

## Code Map

Re-derived against `dd2e537` on 2026-09-15; every address below was bounds-checked.

- `pm_ai/app/pipelines.py:21` -- add `run_dashboard(daemon, *, scope, now)`; `run_harvest` is the shape to follow, and its `save_cursor` at `:87` is the line this slice must not reach
- `pm_ai/app/wiring.py:70,219` -- `Daemon.connectors`, the dict the calendar connector is selected from, and the loop at `:219` that fills it from the enrolment rows. `ConnectorPort.system` and `emits()` are declared at `pm_ai/ports/__init__.py:34-37`, so the selection reads a contract rather than a vendor string
- `pm_ai/connectors/graph/__init__.py:599` -- `GraphConnector.harvest`: reports, never raises, and **persists nothing itself** — `run_harvest` is what writes records and advances the cursor. `:703,737` build the `live` list this slice reads
- `pm_ai/domain/harvest.py:213` -- `HarvestResult.live`, mapped and deliberately never written; its docstring names this dashboard as the reader it exists for
- `pm_ai/connectors/graph/calendar.py:544` -- `WindowPolicy.width`, whose forward reach is what makes the day ahead readable at all; settled for this machine at 24h
- `pm_ai/core/rendering.py:219,262` -- `render_dashboard` and `render_project_dashboard`; both `meetings` annotations widen
- `pm_ai/core/rendering.py:350,354` -- `_time_critical`, shared by both renderers: the new branch goes beside the `HarvestFailure` one at `:354`
- `pm_ai/core/rendering.py:445` -- `_retry_advice`, whose "the connector reports" sentence is the reason the third state may not reuse `HarvestFailure`
- `pm_ai/domain/harvest.py:91` -- `HarvestFailure`, and where `NoCalendarConnector`, `PartialCalendar` and `UnreadCalendar` join it. **Not `meetings.py`:** `PartialCalendar` holds a `HarvestFailure`, and `harvest.py` already imports `Meeting` from `meetings.py` (`:26`), so declaring them there would close an import cycle
- `pm_ai/domain/meetings.py:49` -- `Meeting.tentative`, the precedent `ical_uid` follows: a field carried in memory and absent from `meeting_records.py`'s `_FIELDS` (`:165-178`), so nothing it rides on reaches a file
- `pm_ai/connectors/graph/calendar.py:652,997` -- `CalendarRow.event_id` and the `_read` that builds the row; `ical_uid` is added beside them from `raw.get("iCalUId")`, the one identifier stable across tenants
- `pm_ai/connectors/graph/__init__.py:829` -- `calendar_event_ref=row.event_id`, which is why `meeting_id` alone cannot match a cross-invited meeting: both are Graph's per-mailbox `id`
- `pm_ai/core/goal_register.py:134,262` -- `ARTIFACT` and `parse_goals(raw, scope=)`; `None` bytes means an absent file and `b""` an empty one
- `pm_ai/core/event_log.py:52` -- `EventLog.read(scope=)`; `render_dashboard` wants the **unbounded** read and applies its own window
- `pm_ai/core/meeting_records.py:541` -- `for_day`, which since 2026-09-07 is **not** the day-ahead source and is named here so it is not reached for by habit
- `pm_ai/storage/service.py:1087,1116,1132` -- `write_artifact`, its append-only refusal (which correctly does not apply), and `read_artifact` returning `None` for absence rather than raising
- `pm_ai/domain/scope_model.py:540,733` -- both `daily_dashboard.md` declarations: personal at `:540`, and the project one at `:733`, which `1n` made `gitignored=True`
- `pm_ai/core/config.py:224,298` -- `display_timezone` and the `ZoneInfo` validation `4a` runs at load, which is why a typo'd zone never reaches this slice
- `pm_ai/domain/identity.py:41` -- `DataScope`, what a parsed `--scope` must produce
- `pm_ai/surfaces/cli/dispatch.py:473,538` -- the table and `dispatch`. **The CLI work is larger than "add a row":** `Command.takes` (`:244`) is positional and required, nothing in the table parses an option, and the top-level branch at `:599` refuses *every* trailing word for a command with a `run`. `dashboard` is the first command to take an optional argument, so the mechanism lands with it

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/domain/harvest.py` -- declare `NoCalendarConnector`, `UnreadCalendar` and `PartialCalendar`, the calendar's three further answers
- [x] `pm_ai/connectors/graph/calendar.py` -- read `iCalUId` onto `CalendarRow`
- [x] `pm_ai/domain/meetings.py`, `pm_ai/connectors/graph/__init__.py` -- carry `ical_uid` onto `Meeting` in memory only, as `tentative` is, and assert it stays out of `_FIELDS`
- [x] `pm_ai/core/rendering.py` -- widen both renderers' `meetings` annotation to four members; add the `NoCalendarConnector` branch, which attributes nothing to any connector, and the `PartialCalendar` branch, which lists what arrived and names what did not
- [x] `pm_ai/app/pipelines.py` -- add `run_dashboard`: resolve the zone, select **every** calendar connector by `emits()`, read each day live and independently, merge and deduplicate, read goals and the log, render, then write
- [x] `pm_ai/surfaces/cli/dispatch.py` -- give the table an optional-argument mechanism, then add `dashboard` with `--scope` defaulting to personal
- [x] `tests/core/test_rendering_sections.py`, `tests/core/test_project_rendering.py` -- the new branch in both dashboards
- [x] `tests/slice/test_dashboard_slice.py` -- the matrix end to end against a temporary root
- [x] `tests/surfaces/test_cli_dispatch.py` -- the new subcommand, its `--scope` parsing and its refusals

**Acceptance Criteria:**
- Given a temporary root with two harvested meetings and a goals file, when `run_dashboard` runs, then `~/.manager-ai/memory/daily_dashboard.md` exists under that root with four headings and the meetings listed in start order.
- Given a goals file with a duplicate id and a dashboard already on disk, when the pipeline runs, then it refuses **and** the existing file is unchanged byte for byte — a failed render must not destroy yesterday's dashboard.
- Given a clean root with nothing harvested, then the file is still written and every section states its reason.
- Given no master key is enrolled, then the write succeeds — this artifact is unencrypted and must not depend on the enclave.
- Given `--scope project:alpha` against a root whose personal tree holds goals and meetings, when the pipeline runs, then no path beneath the personal root is read — asserted by instrumenting the reader, because `23d`'s AD-25 test checks only what the renderer *declares*, never what `run_dashboard` actually opens.
- Given a daemon with no connector declaring `CALENDAR_EVENT_HELD`, when the pipeline runs, then the file is written at exit zero, the schedule section states that no connector can answer, **and the rendered text does not contain `_retry_advice`'s "the connector reports" sentence** — asserted on the string, because the whole point of the third state is that it makes no claim about a connector.
- Given two connectors declaring `CALENDAR_EVENT_HELD`, each returning one meeting, when the pipeline runs, then both meetings appear in the schedule and the command exits zero.
- Given two connectors returning the same meeting under **different** `meeting_id`s and an equal non-empty `ical_uid`, then it is listed exactly once.
- Given two connectors returning different meetings that share a start and a title, then **both** are listed — asserted explicitly, because the failure mode here is a dashboard that hides a double-booking.
- Given two connectors where one returns meetings and the other a `HarvestFailure`, then the meetings are listed, the failing instance is named in the section, and the command exits zero.
- Given a `Meeting` carrying an `ical_uid`, when it is written through `MeetingRecords.put` and read back, then the round-tripped record has no `ical_uid` key — the field never reaches a file, asserted on the bytes as `tentative` is.
- Given `run_dashboard` has run, then no cursor was saved, no `meetings/` record was written and the event log is unchanged — asserted against the storage double, not inferred from the absence of a call site.

## Spec Change Log

- **2026-09-15, two matrix rows deleted on instruction, at the build's matrix audit.**
  Both described a pipeline that touches `meetings/` records, which the 2026-09-07 realignment removed, and neither could be covered by a test that was not manufactured to satisfy it.
  **`Malformed meeting record` / `MalformedMeeting`.** `run_dashboard` reads no meeting record at all — the day comes off the live calendar — so there is no hand-edited file for it to refuse. The implementing agent declined to invent a read to satisfy the row, which was the right call.
  **`Path traversal in a name`.** The write is `write_artifact(..., artifact=DASHBOARD_ARTIFACT)` with no `name=` (`pipelines.py:302`): `daily_dashboard.md` is a `File` rather than a `Collection`, so no caller-supplied name exists to traverse with. Found during the audit rather than reported by the build.
  Deleted rather than reinterpreted. A row nothing can test is a claim the spec cannot keep, and leaving either in place would have meant every future audit of this slice re-deriving the same conclusion. `write_artifact`'s name validation is unchanged and still covered where a `Collection` is actually written.

- **2026-09-15, the two items the previous entry left open, settled on instruction at the pre-flight gate.**
  **The third calendar state gets its own value.** Nothing stated what a dashboard says when *no connector is enrolled at all*. Reusing `HarvestFailure` was the cheap answer and was measured against the renderer before it was taken: `_time_critical` prints "The calendar could not be read…" and then `_retry_advice`, which for `retryable=False` emits "The connector reports this will not clear on its own" — a report attributed to a connector that was never enrolled, in the one artifact whose stated purpose is that it never asserts what it did not compute. So the union gains `NoCalendarConnector` instead. The cost was measured rather than assumed: `_time_critical` is shared by both renderers (`rendering.py:254,304`), so it is one branch in one function plus a widened annotation in two signatures, and no renderer logic moves.
  **Which connector is the calendar is a declared capability, not a vendor name.** `ConnectorPort` already declares `emits()`, `GraphConnector` returns `CALENDAR_EVENT_HELD` and `GitLabConnectorAdapter` returns `COMMIT_PUSHED`, so the discriminator exists and matching on `system == "graph"` would have been a second list to keep in step with the first.
  **The renderer names no remedy, deliberately.** The obvious sentence to print is `pm-ai connector add graph <instance>`, and it does not work: `probe.PROBES` holds only `_gitlab`, so enrolling a `graph` system is refused with `UnknownConnectorSystem`, and past the probe `enrol_connector` writes none of the four settings `_graph_connector` requires. Both halves are already recorded in `deferred-work.md` and neither is this slice's to fix. A dashboard that printed a command which refuses would be inventing a remedy, which is the same defect as inventing a fact.
  **Two calendar connectors are merged, not refused — the implementer's refusal was overruled the same day.** The proposal was to refuse and exit `3` on the grounds that merging doubles every shared meeting. The human's answer: two tenants is an ordinary configuration, the data is present, and present data is never a reason to fail. Deduplication is the obligation that replaces the refusal.
  **The dedup key had to be added, and the investigation is why the design changed.** `Meeting.meeting_id` and `calendar_event_ref` are both Graph's `id` (`graph/__init__.py:829`, read at `calendar.py:1066`), which is **per-mailbox**: the same meeting cross-invited to two tenants arrives under two different ids, so matching on what the code already had would have deduplicated nothing in the only case that motivated it. `iCalUId` is the identifier that survives the crossing, `33b` does not read it, and this slice adds it. It rides in memory only, on the precedent of `Meeting.tentative` (`meetings.py:49`) — absent from `meeting_records.py`'s `_FIELDS`, so no persisted grammar and no golden changes, and an acceptance criterion asserts on the written bytes that it stays out.
  **Resemblance is explicitly not identity.** A start-and-title fallback was considered and rejected: two organisations each holding a "Weekly Sync" at 09:00 is a genuine double-booking, and a dashboard that merged it away would suppress the single most actionable fact in the file. A matrix row and a criterion now assert that both are listed.
  **The merge creates a partial state, and it is rendered rather than collapsed.** Two connectors where one answers and one fails cannot be expressed by a union carrying either a list or a failure. `PartialCalendar` is the fourth member: the meetings that arrived are listed and the calendar that could not be read is named. Settled by the same rule that overruled the refusal — data that is present is not discarded because data elsewhere is missing. It, `NoCalendarConnector` and `UnreadCalendar` are declared in `domain/harvest.py` rather than `domain/meetings.py`, because `PartialCalendar` holds a `HarvestFailure` and `harvest.py` already imports `Meeting` (`:26`); the other direction would close an import cycle.
  **The slice grew, and the growth is stated rather than absorbed.** It now reaches into `33b`'s row parsing and `33c`'s mapping for one field each, and the renderer's union goes from two members to four. That is wider than the "one branch in one function" the third state was costed at when the option was chosen.
  **The Code Map and execution tasks were re-derived against `dd2e537`**, not patched. They described the pre-2026-09-07 design throughout — `11a`'s `for_day` as the day-ahead source, three inputs where there are now four, and no mention of the timezone read the frozen block requires. Two things the re-derivation found that the old map hid: `HarvestResult.live` is the day-ahead source and is already mapped-and-never-persisted, so the `Never` clause costs this slice nothing to honour; and `--scope` is not a row in the dispatch table but a mechanism the table does not have — `Command.takes` is positional and required, and a top-level command with a `run` refuses every trailing word (`dispatch.py:599`).
  **One stale citation inside the frozen block was corrected on instruction**, as the 2026-09-06 precedent did for `4i`: the ledger refusal moved from `service.py:1039` to `:1116` when `write_artifact` moved to `:1087`. Every other address in the frozen block was bounds-checked and holds.

- **2026-09-15, three defects left by the 2026-09-07 realignment, settled on instruction.**
  **`4g` is a dependency of this slice and was never recorded as one.** The frozen Always requires `display_timezone` and refuses an unset zone; the key lands in `4g`, which is still `ready-for-dev`, and `pm_ai/core/config.py` closes `ACCEPTED_KEYS` at three fields. The decision assigning the key on 2026-09-03 said in as many words that "`23b` reads it once and passes it to both consumers", and the edge was added to no dependency table and no graph. It is now in both, and this slice cannot start before `4g`.
  **The `Never` clause contradicted the Always it was realigned beside.** Always gained a live Graph fetch on 2026-09-07; `Never` kept forbidding network I/O from this command, with that prohibition as its stated reason. The realignment's own decision record accepts the render-time network exposure deliberately, so the clause is narrowed to what it always meant operationally — no `run_harvest`, no persistence, no cursor — and the stale justification is removed rather than reinterpreted at execution time.
  **The unreachable-calendar row the realignment claimed to add here was never added.** `23a` and `23d` both gained one; this slice, which performs the fetch and decides what reaches the renderer, did not — so the one place the honesty rule is actually enforced had no row asserting it. Added, and it fixes exit zero: a calendar that could not be read is a dashboard that says so, not a failed command.
  **Left open deliberately, and not settled here.** Nothing states what happens when *no Graph connector is enrolled at all* — a third state that is neither "no meetings" nor "the fetch failed" — and the Code Map and execution tasks below still describe the pre-2026-09-07 design.

- **2026-09-07, this pipeline gains the calendar read.** Consequent on the decision that no future meeting is persisted. `11a`'s `for_day` is no longer the dashboard's source for the day ahead — the calendar is — and since `core` is I/O-free and `render_dashboard` is pure, the fetch belongs in this layer. The timezone rule is unchanged in substance: one read, passed to the renderer and to whatever selects the day, so the two cannot disagree. What changes is that the selection is now a live fetch whose failure must reach the renderer as a reason rather than as an absence.

- **2026-09-03, amended against the second multi-lens review and the day's decisions.**
  **The `Ask First` on logging a render is answered: it does not log** (Q14). The discriminator is whether an action changes truth or projects it — a dashboard is derivable from Tier 1 plus a clock, and CAP-10's retrospective counts are decisions, proposals and commitments. `2c`'s vocabulary stays closed, and a matrix row now asserts the event log is unchanged after a re-run.
  **The project branch calls a different function.** `project_scope_datasources` is not built: the two dashboards are separate renderers, and the project one has no goals parameter, so this slice cannot pass it personal-scope data even by mistake. That is the wall, and it is structural rather than a list this slice must remember to consult.
  **The timezone has a source and is read here.** `config.toml`'s `display_timezone` reaches both the renderer and `11a`'s `for_day` from one read, so the day the dashboard shows and the day its meetings were selected by cannot disagree. An unset zone is refused rather than defaulted to UTC — the silent wrong answer the key was added to prevent.
  **One review claim did not hold.** B8 said this slice was specified against a "degrades quietly to `UNALIGNED`" model and would crash on an absent goals file. It would not: neither this slice nor `23a` calls `resolve` or `alignment_tag`, so `UnresolvedGoal` is unreachable. `22a`'s Intent was wrong about current behaviour; the knock-on onto the renderers was not.

- **2026-09-02, multi-lens review.** The project-scope row had no criterion and the third input had no refusal path.
  **AD-25 was asserted only against the renderer's declaration**, never against what `run_dashboard` actually reads — so the pipeline could open the personal goals file for a project render and every declared check would pass. A criterion now instruments the reader.
  **The event log was the one input with no refusal row**, though a hand-edited or corrupt segment fails to parse exactly as a malformed goals file does; without the row, the read-before-write guarantee covered two of three inputs.
  **Exit codes now come from `4c`'s table** rather than being described as "the refusal exit code" independently here, in `4c` and in `8b`.
  The edge-case lens added the four filesystem and argument paths: an undeclared scope, an unparseable `--scope`, the write failing after a successful render, and the target existing as a directory where a `File` is declared.
## Design Notes

The refusal cases are the ones worth designing. `write_artifact` replaces whole files, so the natural implementation opens the target, then renders, then discovers a malformed goals file and has already truncated yesterday's dashboard. Reading and rendering must fully succeed before the write is attempted, which is also why `23a` is a pure function returning a string rather than something that writes as it goes.

The master-key row exists because the enclave is easy to over-apply. `daily_dashboard.md` is declared unencrypted, so a first-time user who has not run `pm-ai key enrol` should still get a dashboard; requiring a key here would make the enclave a dependency of the one output the PM sees every morning.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_dashboard_slice.py -q` -- expected: all matrix rows pass
- `uv run pm-ai dashboard` -- expected: file written at the declared path, exit zero
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
