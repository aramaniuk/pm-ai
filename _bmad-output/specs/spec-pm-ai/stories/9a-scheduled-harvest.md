---
title: 'The daemon harvests every connector on a schedule'
type: 'feature'
created: '2026-10-10'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing in pm-ai harvests on its own. The harvest pipeline is driven only by tests; the one production path to a connector is `pm-ai dashboard`, which reads the calendar from an empty position and keeps nothing it learned, so no real fetch records the coverage the commitment verdicts depend on. A connector that raises mid-harvest skips saving its position and the attempt vanishes. CAP-2's four-hour cycle needs the unattended process slice `4e` builds.

**Approach:** Inside the daemon, one timer per connector instance harvests every 240 minutes (give or take 15), loading and saving the real position, backing off on failures that can clear and stopping on those that cannot. Harvests run on the daemon's pool of workers, which slice `4r` owns; slice `1r` (one claim for whole-file writes) lands before this one. **This scheduler is temporary:** an in-memory timer, which story 10a forbids ("every job is a queue row, never an in-memory timer"); 10a replaces it, and this slice is written so only the timer is thrown away. The 07:00 render is slice `9b`, which builds on this one.

## Boundaries & Constraints

**Always:**
- **The timers run inside the daemon.** The CLI schedules nothing.
- **One timer per connector instance, from a list re-read at every cycle.** The connector list and each instance's set-up state are read from storage whenever a cycle is due, so an instance enrolled by `connector add` after the daemon started gets a timer without a restart. At daemon start the first attempts are spread across the first 15 minutes rather than all at once; the next is due 240 minutes after the previous attempt finished, plus or minus up to 15 minutes chosen at random each time, so instances drift apart.
- **The scheduled harvest is the one read that owns the position.** It loads the saved position before asking and saves position, coverage record and any failure together afterwards, as the pipeline already does. A connector that raises instead of reporting is recorded as a failure naming the exception's type, never its text, with the position unchanged.
- **Fetching transcripts is part of the harvest.** When a connector offers them (`33e`), the fetch runs inside the harvest after the position is saved: its time counts against the harvest, its refusals appear on the `harvests` line, and every transcript fetched but not yet ingested is handed to ingestion (`11b`) in the same cycle, so a daemon killed between the two is repaired next run.
- **Backoff is bounded by the kind of failure.** One that can clear on its own — a server-side error, a timeout, a throttle, an unreachable host, the credentials file busy while nothing was rotated — retries after 1, 2, 4, 8, … minutes, doubling, capped at 240, never sooner than a wait the provider asked for. The provider's wait is saved with the failure and honoured after a restart; only the doubling starts over. One that needs a human — an expired or declined sign-in, revoked consent, a moved repository, broken configuration, a fault pm-ai could not classify — is recorded at once as not retryable, which `doctor` shows as `FAILING` naming the instance and reason. **Such an instance is still tried once at each regular cycle**, so a fix made by hand is noticed without a restart; a success clears the record. A regular cycle is not a retry: the record never reads as a pending one.
- **The credentials file being busy is retryable when nothing was rotated** (today it is recorded as permanent); busy after a rotation keeps today's verdict and remedy.
- **A refused save is a failed attempt, never a lost one.** When the position or coverage cannot be written because another command holds the file or the store is busy, the attempt fails naming the claim that refused it, the refusal is logged, and the instance is retried after 1 minute, the first wait above. Nothing is recorded against the connector.
- **Harvests run one at a time on the daemon's pool of workers.** `4r` owns the pool and the lock that serializes every storage call from a worker; this slice starts no thread of its own, and `9b`'s renders use the same pool. At shutdown `4r`'s order holds: timers cancelled, a running harvest allowed to finish within the wait, then the store closed — a harvest never saves against a closed store.
- **A scheduled harvest has no current project** (AD-11). Every watched project's instances run, plus the personal-scope ones; a coverage or empty-batch write goes to the scope the instance was filed under at build — the project for a project's instance, personal for a Graph instance — and events still land in the scope each declares.
- **An instance that is not set up is skipped**, as slice `8n` states for a built-in without a credential: nothing is recorded, and `doctor` says no harvest has run for it.
- **Coverage records older than 90 days are pruned after each harvest.** A decision: longer than any window a commitment's verification could need (CAP-34 alerts 48 hours before a milestone; compaction starts at 7 days); story 16's verdict builder may shorten it.
- **`pm-ai doctor` gains a `harvests` line**, read from the operational store, contacting nothing: per instance the last attempt, last success and current failure. `OK` when nothing is failing, `WARNING` while a retryable failure is pending, `FAILING` for one that needs a human.
- **No secret reaches the daemon's log or a recorded reason**: exception types and pm-ai's own sentences only, as the pipelines already do.
- **No new setting.** 240, 15 and 90 are constants; the cadence is not per instance.

**Ask First:** Nothing. Trying a permanently failed instance once per regular cycle was a judgement call, stated above as decided.

**Never:**
- No durable queue, task manager or file watcher — story 10a, which deletes this timer. The doubling waits live in memory and a restart starts them over; stated as this design's cost.
- No change to `pm-ai dashboard`'s own read of the day's calendar (`23b`): meetings not yet held are read live and persisted nowhere (2026-09-07); the eight-day walk per render in deferred-work stays open.
- No `[ERROR]` verdicts on commitments and no new dashboard line for a failing instance — story 16 and the dashboard stories own those; this slice records the inputs and `doctor` shows them.
- No rate limiter beyond one-at-a-time and the provider's own hint. No change to `4e`'s request path or API.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Daemon start | two instances enrolled | first attempts spread across the first 15 min, one after the other; next due 240 ±15 min after each finished | N/A |
| Enrolled after start | `connector add` while the daemon runs | the next cycle reads the list and gives it a timer; no restart | N/A |
| Success | provider answers | position, coverage and "no failure" saved together; `doctor` `OK` with the instants | N/A |
| Connector raises | `harvest` throws | failure recorded naming the type; position unchanged; treated as needing a human | not retryable |
| Transient failure | 503, then 503, then 200 | retried after 1 min, then 2; success clears the record and the waits | `WARNING` meanwhile |
| Provider asks for a wait | 429 with `Retry-After: 600` | next attempt no sooner than 10 min; a restart 3 min in waits the remaining 7 | N/A |
| Cap on the waits | nine transient failures in a row | waits 1,2,4,…,128,240,240 min | N/A |
| Permanent failure | expired sign-in | no waits; recorded at once; tried again at the next regular cycle; a success clears it | `doctor` `FAILING`, instance and reason named |
| Credentials file busy | nothing rotated / the replacement could not be saved | retryable, the next attempt clears it / not retryable, today's remedy | N/A / `FAILING` |
| Save refused | position write meets a busy claim | attempt fails naming the claim; logged; retried after 1 min; no connector failure recorded | N/A |
| Shutdown mid-harvest | stop while a harvest runs | the harvest finishes within `4r`'s wait and saves; the store closes after | N/A |
| Transcripts offered | connector fetches two, one not yet ingested | both handed to ingestion this cycle; fetch refusals on the `harvests` line | N/A |
| Not set up | built-in with no credential | skipped; nothing saved | `doctor`: no harvest yet |
| No acting project | a Graph instance and `gitlab:alpha` | coverage in personal and in `alpha`'s tree respectively; each event in its own scope | N/A |
| Two due at once | second instance due during a harvest | runs after it; neither lost | N/A |
| Old coverage | rows ending 91 and 89 days ago | the older pruned after a harvest, the younger kept | N/A |
| Secrets | a failing token | absent from log lines and recorded reasons | N/A |

</frozen-after-approval>

## Code Map

Verified against `ddcb9aa` on 2026-10-10. Sibling specs on disk: `4e`, `4r` (pool, lock, shutdown order), `1r`, `33e`, `11b`.

- `pm_ai/app/pipelines.py:69-136` -- `run_harvest`: `load_cursor` (71), the bare `connector.harvest(cursor)` (73), `save_cursor` (135); `:164` the empty-batch write to `daemon.scope`; `:476-504` `_ask`, the model for catching a raising connector by type
- `pm_ai/app/wiring.py:118-139` -- `Daemon`; `:359-365` `builtin_scopes`, a local handed to `_enrolled_connectors` at `:383-385`; `:752` `_enrolment_scope` — the scope each instance is filed under, which `Daemon` now carries per instance (a Graph row is personal)
- `pm_ai/app/entry.py:122` `main`, `:816` `_Composition`, `:985` `_diagnose` (append after `4n`'s connectors line, as `_selection_probe` at `:1019` is)
- `pm_ai/storage/service.py:103-118` -- `cursors`, `coverage`, `harvest_failures`; `:148-157` why a new table needs no version bump; `:582` `sqlite3.connect(..., check_same_thread=False)` with the default 5-second busy wait — not a concurrency guarantee, hence `4r`'s lock; `:1644` `load_cursor`, `:1650` `save_cursor`, `:1787` `harvest_failure`, `:1821` `coverage_windows`. New: `harvest_runs` (instance, last attempt, last success), a prune of `coverage` by `end`
- `pm_ai/ports/__init__.py:561-621` -- `StoragePort`; the new reads and writes join `load_cursor` (565) and `harvest_failure` (594)
- `pm_ai/domain/harvest.py:29` `UNCLASSIFIED_FAULT_IS_RETRYABLE`; `:127-171` `HarvestFailure` (`retryable`, `retry_after`, `at`) — `retry_after` already persists with the failure, which is what a restart honours
- `pm_ai/connectors/graph/calendar.py:913` -- every `GraphAuthError` recorded `retryable=False`, `CredentialStoreBusy` (`auth.py:261`) among them; `auth.py:972-980` already splits busy-rotated from not
- `pm_ai/platform/doctor.py` -- probe wording lives with the other probes (`4n`); `pm_ai/domain/health.py:58-82` `Probe`, `Report`
- `tests/architecture/test_static_rules.py:82-97` `SCHEDULING_CALLS`, enforced on `connectors` only (`:316`); `test_domain_invariants.py:33` `mod` skips on a missing module and `:154-167` expects `pm_ai.core.scheduler.Cursor` — so the pure module is named `cadence.py`, not `scheduler.py`, which 10a takes; `tests/conftest.py:58-60` wants skip deltas, and this slice's is zero
- `tests/slice/test_vertical_slice.py:50-110` the existing `run_harvest` rows; `tests/architecture/test_doctor.py:293,482,648` seven-probe counts, unchanged by an appended line
- `_bmad-output/implementation-artifacts/deferred-work.md:823`, `:883`, `:894` -- the entries this slice closes; `:716` the one it carries

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/cadence.py` -- new, pure: the constants; next cycle with the random offset from an injected draw, and the spread of first attempts; the doubling waits with the provider's floor; whether a failure retries -- the arithmetic 10a keeps
- [ ] `pm_ai/ports/__init__.py`, `pm_ai/storage/service.py` -- `harvest_runs` table, its read and write, `prune_coverage(before)` -- Tier 2 facts through the single writer
- [ ] `pm_ai/app/wiring.py` -- `Daemon` carries each instance's own scope, filled from the built-ins' and `_enrolment_scope`'s answers, personal for Graph; the enrolled list re-readable on demand -- the harvest has no current project
- [ ] `pm_ai/app/pipelines.py` -- `run_harvest`: catch a raising connector as `_ask` does; catch `ArtifactBusy` (`1r`'s refusal) around `save_cursor` as a failed attempt naming the claim; record the attempt; write the empty batch to the instance's scope; hand every fetched-not-ingested transcript to `11b`'s ingestion; prune -- the missing `try/except`
- [ ] `pm_ai/connectors/graph/calendar.py` -- a busy credentials file with nothing rotated is recorded retryable -- waiting clears it
- [ ] `pm_ai/app/ticks.py` -- new, the temporary timers attached in `4e`'s lifespan on the one loop: one per instance from the re-read list, each harvest submitted to `4r`'s pool, logging by type; the docstring names 10a as its replacement -- the timer, in one file
- [ ] `pm_ai/platform/doctor.py`, `pm_ai/app/entry.py` -- the `harvests` probe from values the composition root reads, appended after the connectors line -- doctor contacts nothing
- [ ] `tests/core/test_cadence.py`, `tests/slice/test_scheduled_harvest.py`, the doctor suite -- one test per matrix row, with an injected clock, draw and sleep; the refused-save row uses a storage wrapper raising `ArtifactBusy` once -- the matrix is the contract
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark `:823`, `:883` (the `run_harvest` half), `:894` resolved; add the temporary-timer entry naming 10a -- the debt is on record

**Acceptance Criteria:**
- Given two enrolled instances and a fake clock, when the timers run through one cycle with one instance failing transiently, then the saved positions, coverage, failure rows and next due instants match the matrix, and no two harvests overlapped.
- Given a storage wrapper refusing the first `save_cursor` with `ArtifactBusy`, when the cycle runs, then the attempt is recorded failed naming the claim, no connector failure is written, and the retry one minute later saves.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged (this slice names no skipped module).

## Design Notes

`run_harvest` is edited by three slices, in landing order: `33e` adds transcript acquisition after `save_cursor` (a fault there never loses a successful harvest's position or coverage), wrapped so an unclassified exception becomes a named refusal, and adds the transcript report as a field on `PersistResult`; this slice adds the `try/except` around the connector call, the attempt record, the refused-save handling and the backoff; `11b` adds ingestion of every un-ingested capture after acquisition.

Harvests run on `4r`'s pool, not on the loop, because every connector's `harvest` is a synchronous HTTP client and would stall the request path for the length of a fetch; AD-19 grants the pool for blocking work. `4r` owns the pool (four workers) and the daemon-level lock serializing every worker's call into the single `StorageService`. Background tasks attach in the app's lifespan on the one loop (`4e`); `ticks.py` hooks there.

The arithmetic is separated from the timer deliberately: 10a's schedule trigger needs the same cadence and waits, so `cadence.py` survives and `ticks.py` is deleted whole.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; skip count unchanged from the baseline
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual check:** in a throwaway home (`HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai …`) with one enrolled instance, start `pm-ai daemon run`; within 15 minutes `pm-ai doctor` shows the `harvests` line with an attempt instant. Run `connector add` for a second instance while the daemon runs; after the next cycle `doctor` shows both.
