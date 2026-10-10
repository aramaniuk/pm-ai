---
title: 'Proactive Enablement fills from real Teams messages'
type: 'feature'
created: '2026-10-09'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The dashboard's Proactive Enablement section was written before any message existed, and it will read real Teams messages wrongly in two ways. An event log that cannot be read — one corrupt line in a monthly segment — refuses the whole dashboard today, and the renderer can only say "no signals in the window", a false claim about a quiet day. And it cannot tell which messages mention the PM, which is the one thing a 07:00 reader wants first. (The build note saying no connector writes messages is slice `33d`'s to delete; that slice lands first.)

**Approach:** Widen the renderer's log input to carry a failure the way the calendar input already does, mark and list first the messages whose mentions hold the PM's own Graph user id, and cap the list honestly.

## Boundaries & Constraints

**Always:**
- **Message details are read through the shared payload decoder** that slice `2m` lands before this one; this slice adds no literal field lookup. A line the decoder refuses is counted in the section's footnote as a line that could not be decoded — not dropped in silence, not a crash.
- **A failed event-log read is stated, never rendered as a quiet day.** Four kinds of failure are stated: a line that is not a well-formed entry, an entry whose kind pm-ai does not know, a segment that is not valid UTF-8 text, and a segment that could not be read from disk (missing, or unreadable). The section says the event log could not be read and why, with the ledger's own reason naming the segment. The pipeline hands the failure to the renderer as the same kind of value the calendar already uses and saves it nowhere; the file is still written, the other sections render from their own inputs, and the command exits zero — as an unreachable calendar does today. On that branch the "No `message_posted` entries in the event log between …" sentence is absent (there was no query result), no "the connector reports" sentence appears (no connector was asked), and no sign-in line is printed (no mention could have been marked anyway).
- **Whether the PM was mentioned is decided when the section is rendered.** An entry whose `mentions` holds one of the PM's own Graph user ids ends with `mentions you` and is listed before every other entry. The ids come from the `user_id` that slice `8m` saves at sign-in on each Graph connector's settings row — only rows the shared connector reader (slice `4n`) accepts: enabled rows of a known system, not every readable file. With two tenants enrolled, either id counts. The project dashboard marks mentions the same way: the rows are pm-ai's own settings, which the wall between project rendering and the personal store does not forbid.
- **A sign-in that predates the saved user id is said, not guessed around.** When a Graph row's `user_id` is absent, blank, whitespace, or not text, it is never compared; nothing is marked for that tenant and one line says the sign-in for that connector predates pm-ai recording the user id, naming the connector and `pm-ai connector sign-in <instance>`.
- **Sender names are what the ledger line carries.** The actor field is rendered as written — a handle, not a name. The alias table that would turn a handle into a name is held in memory and saved nowhere, so no name resolution is built here; it belongs to the slice that puts the alias table in Tier 1.
- **At most 20 signal lines**, mentions of the PM first, then newest first, ties broken by entry id so a re-render is byte-identical. When mentions alone exceed the cap, the newest mentions fill it. When the window held more, one closing line states how many more there were and that they are in the event log — a cut list is never a claim that the day held only those.
- **The window stays the last 24 hours**, and the empty sentence and footnotes keep naming both instants.
- **Each dashboard reads its own scope's log.** The personal dashboard reads the personal log; a project dashboard reads that project's log and nothing else (AD-25, `23d`). A message harvested into a project's log never appears on the personal dashboard, and the reverse.
- **Nothing is truncated here.** The excerpt renders as the line carries it; the only bound is the writer's 16,384-character limit on a whole ledger line, and how long an excerpt is written is `33d`'s choice.

**Ask First:** Nothing. The cap is twenty signal lines — one screen at 07:00 — settled 2026-10-09 with the rest of this slice's decisions; another number would change one constant and the closing line's test.

**Never:**
- No "since the last render" window. That needs a recorded last-render instant, which only slice `9b`'s scheduled render can supply, so `9b` owns that change.
- No name resolution, no alias table read, no reverse index.
- No model call, no write beyond `23b`'s, no scheduling, no change to what `33d` writes, no second literal field lookup.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Real message line | a `33d`-written entry with channel, excerpt, mentions | `- **HH:MM** <actor> in <channel> — <excerpt>` | N/A |
| Mentions the PM | `mentions` holds the PM's `user_id` | the line ends ` — mentions you` and sits above every unmarked line | N/A |
| Mentions someone else | other ids only | listed, unmarked | N/A |
| Mentions not recorded | `mentions` absent from the line | listed, unmarked; no claim either way | N/A |
| Two tenants | two Graph rows, two user ids | a mention of either id is marked | N/A |
| Project dashboard, mention | `--scope project:alpha`; the PM's id in a project-log entry | marked and listed first, from the same connector rows | N/A |
| No `user_id` on a row | absent, blank, whitespace, or not text | no marking for that tenant; one line names the connector and `pm-ai connector sign-in <instance>` | N/A |
| Disabled or unknown-system row | `enabled: false`, or a system pm-ai does not know | its `user_id` is not used; no sign-in line for it | N/A |
| No Graph connector | no Graph row at all | entries listed unmarked; no sign-in line | N/A |
| Over the cap | 23 entries in the window | 20 lines, then "3 more message signals in this window are not shown; they are in the event log" | N/A |
| Exactly the cap | 20 entries | 20 lines, no closing line | N/A |
| Over the cap with mentions | 25 entries, 3 mention the PM | the 3 first, the 17 newest of the rest, the closing line | N/A |
| More mentions than the cap | 24 entries, 22 mention the PM | the 20 newest mentions; the closing line counts 4 | N/A |
| Ordering | same inputs rendered twice | byte-identical; mentions first, newest first, then entry id | N/A |
| Log could not be read | a hand-edited line; an entry of an unknown kind; a segment that is not UTF-8; a segment listed then missing | file written, exit zero; the section names the segment and the reason; the no-signals sentence, any "the connector reports" sentence and the sign-in line are absent; calendar and goals render normally | reported, never raised, never saved |
| Log could not be read, row lacks `user_id` | both at once | the failure sentence only | N/A |
| Decoder refuses a line | a message line without its channel | counted in the footnote as a line that could not be decoded; the others render | N/A |
| Empty window | nothing in 24 h | "No `message_posted` entries in the event log between <since> and <now>"; no sentence about the build | N/A |
| Scope isolation | `--scope project:alpha`; the default | only that project's log, or only the personal log; the other's messages never appear | N/A |

</frozen-after-approval>

## Code Map

Verified against `ddcb9aa` on 2026-10-09.

- `pm_ai/core/rendering.py:109` -- `CalendarAnswer`, the precedent; the log input gets a two-member union beside it (`Sequence[EventEntry] | HarvestFailure`)
- `pm_ai/core/rendering.py:187` -- `MESSAGE_WINDOW`, unchanged. `:241` `NO_PRODUCER_YET` and its `:633` branch are deleted by `33d`, which lands first — not a task here
- `pm_ai/core/rendering.py:247,293` -- both renderers: `entries` widens; both gain a keyword-only value carrying the PM's user ids and the connectors whose row lacks one
- `pm_ai/core/rendering.py:582` -- `_proactive_enablement`: failure branch first (mirror `_time_critical`'s at `:432`, **without** `_retry_advice` at `:537`), then decode, mark, sort, cap
- `pm_ai/core/rendering.py:636` -- the sort, oldest-first today; becomes mentioned-first, newest-first, entry id
- `pm_ai/core/rendering.py:655` -- `_signal_line`, decoding through `2m`'s pair; the marker and closing line join it. `:668` `_window_notes` gains a fourth sentence under a new counter name — its `unreadable` already means a bad `ingested_at`
- `pm_ai/domain/payload_fields.py` -- `2m`'s `decode_payload(entry)` (name as landed); `MessagePayload.mentions: tuple[str, ...] | None` at `events.py:113`; `excerpt` (`:115`) declares no length; `MAX_ENTRY_LENGTH` at `event_entries.py:166`
- `pm_ai/app/pipelines.py:292` -- `EventLog(daemon.storage).read(scope=scope)`, raising today to exit 3; wrap it, turning `MalformedEntry`, `UnknownCategory` (`event_entries.py:198,202`), `UnicodeDecodeError` (a `ValueError`) and `OSError` into `HarvestFailure(reason=<the refusal's text>, retryable=False)`. Passed to the renderer only: `HarvestFailure`'s docstring (`harvest.py:127`) describes the persisted harvest case, and this value is never persisted. The goals read keeps refusing
- `pm_ai/app/wiring.py:1380` -- `_enrolled_configurations`: every readable `connectors/<name>.json`, application scope, no filter. `4n`'s shared reader accepts enabled rows of a known system; this slice lands after `4n` and reads through it, or carries the same filter if ordered first. `graph/__init__.py:218-222` spells the row's keys; `user_id` joins them in `8m`
- `pm_ai/domain/identity.py:341-343` -- the comment says the alias table is "persisted as Tier-1 data"; `ALIASES` (344) is a module dict nothing saves. Stale; named here, not fixed here
- `pm_ai/core/ledger.py:43` -- `parse_segment`, raising at `:66,:72` with the segment name in the text; `service.py:1448` raises `FileNotFoundError` for a vanished segment; `service.py:1618` writes the actor as `actor_id`, which is what the line renders
- `tests/core/test_rendering_sections.py:269,287` -- the calendar precedents asserting the negatives (`NO_MEETINGS not in body`, `"the connector reports" not in body`); the failure-branch test here asserts its three absences the same way
- `tests/core/test_project_rendering.py:226` -- asserts the project renderer's exact parameter list; grows by the new keyword. `tests/architecture/test_domain_invariants.py:227` (`test_ad25_project_rendering_cannot_open_the_personal_store`) matches goal-typed names only, so the project renderer may take the user-ids keyword
- `tests/slice/test_dashboard_slice.py:408` -- the corrupt-segment test, expecting a raise today; flips to written-and-stated
- `tests/core/goldens/dashboard_full_day.md` -- re-ordered newest-first and re-pinned

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/rendering.py` -- widen `entries` on both renderers; the PM-ids parameter; failure branch with its three absences; mark, sort, cap, closing line; the sign-in line naming `pm-ai connector sign-in <instance>`; fourth footnote under its own name -- the section's contract
- [ ] `pm_ai/app/pipelines.py` -- catch the four failure kinds into `HarvestFailure`, passed and not saved; build the PM-ids value from the `4n` reader's Graph rows, a blank, whitespace or non-string `user_id` counting as absent; pass both -- the pipeline passes, the renderer states
- [ ] `tests/core/test_rendering_sections.py`, `tests/core/test_project_rendering.py` -- one test per matrix row, the failure test asserting the negatives as `:269,287` do; the parameter-list assertion; re-pin the golden -- the matrix is the contract
- [ ] `tests/slice/test_dashboard_slice.py` -- the corrupt-segment test flipped; a non-UTF-8 segment; `user_id` read from a row, a disabled row ignored; the project dashboard marking a mention; scope isolation end to end -- what the renderer cannot see
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark resolved: the "no failure representation" entry and the cap entry; append the dated line to `23b`'s Spec Change Log (its "malformed segment → refused" row is superseded)

**Acceptance Criteria:**
- Given a personal log holding one `33d`-written message mentioning the PM's recorded `user_id` and three that do not, when `pm-ai dashboard` runs, then the section lists the mention first, marked, with its channel and excerpt, and the rest newest first.
- Given a corrupt line in a segment and yesterday's dashboard on disk, when the pipeline runs, then the command exits zero and the new file's Proactive Enablement names the segment and the reason, carries no no-signals sentence, no "the connector reports" sentence and no sign-in line, while the other three sections render from their own inputs.
- Given a Graph row without `user_id`, when the pipeline runs, then no line reads `mentions you` and one line names that connector's sign-in as predating the recorded id and names `pm-ai connector sign-in <instance>`.
- Given 23 messages in the window, then exactly 20 are listed and the closing line says 3 more are in the event log.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09).

## Verification

**Commands:**
- `uv run pytest tests/core/test_rendering_sections.py tests/core/test_project_rendering.py tests/slice/test_dashboard_slice.py -q` -- expected: all matrix rows pass, golden re-pinned
- `uv run pytest -q` -- expected: all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09)
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
