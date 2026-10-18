---
title: 'Four fixes from the first reviews'
type: 'bugfix'
created: '2026-10-17'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The reviews of `23e`, `33h`, `8r` and `1s` left four gaps, decided by Andrei on 2026-10-17. An all-day entry running over several days shows only on its first day. A run whose channel walk was cut short is logged as needing a person, though it only waits for its next cycle. The sign-in session has no timeout, so a stalled token endpoint holds a health probe or harvest indefinitely. And `project add` accepts a pm-ai home or a `.project-ai` folder as a project, where two of `1s`'s rules collide.

## Boundaries & Constraints

**Always:**
- **Multi-day all-day entries (amends `23e`).** An all-day entry is on the dashboard on every date from the date Graph gives its start up to, not including, the date Graph gives its end. Its line stays `all day`. The end date is read as `_stated_date` reads the start and carried on `CalendarRow` and `Meeting` in memory only, with no key in the record fields; `Meeting.__post_init__` checks neither date, so a backwards span passes it. No end date, or one not after the start, means one day, on the start date: the connector carries a backwards span and flags it (`_clock_flag`), and refuses a row only when its `end` is missing or unparsable, as `to_utc` does today.
- **Cut channel walk log wording.** `HarvestFailure` gains one boolean field, default false, set only on `33h`'s non-retryable cut after the position. A joined failure (`_one_failure`) carries it when it is not retryable and every non-retryable part carries it; `_with_skipped` keeps it. For such a run the daemon logs that the attempt waits for its next regular cycle because a channel walk was cut short; any other non-retryable failure keeps "needs a person". The field is in memory only, not stored, so `doctor`'s line for a stored failure is unchanged in this slice. No spine amendment: AD-9 names `HarvestFailure` but not its fields.
- **Sign-in timeout (amends `8r`).** The `requests` session handed to MSAL waits at most 10 s to connect and 30 s for each read, bound into the session's `request` method with `functools.partial`, since `requests` has no session-wide timeout and MSAL passes none. A stalled sign-in fails as the same "token endpoint did not answer" error a refused connection gives, handled as today. It covers health probes, harvest refreshes and each device-code poll of `connector add graph`; the device-code flow's overall expiry is unchanged.
- **`project add` refuses special folders.** A project path that is, or is inside, `~/.pm-ai`, `~/.manager-ai`, or any folder named `.project-ai` (name compared casefolded) is refused with `ProjectPathUnusable`, naming the path, the special folder, and that nothing was onboarded. The path is resolved, then each of its existing folders is compared with each home by `os.path.samefile`, so links and case variants on a case-insensitive disk are caught, and `~/.manager-ai-old` is not. The same check runs in-process and through the daemon.

**Ask First:** Whether `doctor` should also stop saying "needs you" for a stored cut walk. That needs the marker stored (a `harvest_failures` column, defined at `storage/service.py:148`, INSERT at `:3347`). Recommended: not in this slice; recorded in deferred-work.

**Never:** No change to which runs retry or when. No "day N of M" mark. No change to how an all-day start date is read. No refusal of a path that contains a home, such as the home folder itself.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Multi-day entry | start Oct 17, end Oct 20 | `all day` on Oct 17, 18, 19; not Oct 20 | N/A |
| West of UTC | UTC−5; start Oct 17, end Oct 19 | Oct 17 and 18; not Oct 16 | N/A |
| East of UTC | UTC+9; start Oct 17, end Oct 19; day Oct 19 | returned for that range, not listed (assumes Graph writes the end as the entry's own midnight under `Prefer: outlook.timezone="UTC"`, unmeasured, `deferred-work.md:1383`) | N/A |
| End not after start | start Oct 17, end Oct 17 or Oct 16 | Oct 17 only | flagged; not refused |
| No end date | `Meeting` with a start date only (`23e`'s tests) | its start date only | N/A |
| Cut walk alone | only failure: a cut after the position | not retried; the cut wording | N/A |
| Cut plus throttled calendar | cut and a retryable calendar failure | not retried; the cut wording | N/A |
| Cut plus expired sign-in | cut and a non-retryable sign-in failure | "needs a person" | N/A |
| Stalled POST | token endpoint accepts, never answers | "did not answer" after about 30 s | as today |
| Stalled GET | discovery accepts, never answers | after about 60 s (read retried once) | as today |
| Unreachable endpoint | connect never completes | after about 20 s (connect retried once) | as today |
| Home as project | `~/.manager-ai`, or `~/.PM-AI` on APFS | refused; registry unchanged | `ProjectPathUnusable` |
| Inside a home | `~/.pm-ai/work`, not yet existing | refused; nothing created | `ProjectPathUnusable` |
| Link into a home | `~/work` linking into `~/.manager-ai` | refused | `ProjectPathUnusable` |
| Project folder | `/repo/.project-ai/memory`, `/repo/.Project-AI` | refused | `ProjectPathUnusable` |
| Normal layout | `/repo/` holding `/repo/.project-ai` | onboarded | N/A |
| Similar name, home itself | `~/.manager-ai-old`; `~` | onboarded | N/A |
| Relative path | `work/repo` | in-process: resolved against the working directory, then checked; daemon: refused as today | `ProjectPathUnusable` (daemon) |

</frozen-after-approval>

## Code Map

Verified against `ba0b747` on 2026-10-17.

- `pm_ai/connectors/graph/calendar.py:720` `all_day_date`; `:1154` set from `start`; `:1222` `_stated_date`; `:1304` backwards span flagged
- `pm_ai/domain/meetings.py:180`, `:182` -- `all_day_date`; `__post_init__`
- `pm_ai/connectors/graph/__init__.py:1659` -- onto `Meeting`
- `pm_ai/core/meeting_records.py:363-376` -- in-memory fields cleared on read-back
- `pm_ai/app/pipelines.py:2026-2044` -- `_on_day`, equality at `:2044`
- `pm_ai/domain/harvest.py:128` -- `HarvestFailure`
- `pm_ai/connectors/graph/channels.py:644-660` -- the non-retryable cut
- `pm_ai/connectors/graph/__init__.py:1873`, `:1889` -- `_with_skipped`; `_one_failure`
- `pm_ai/app/ticks.py:378` -- "needs a person"
- `pm_ai/platform/doctor.py:662-667` -- "FAILING, needs you"
- `pm_ai/storage/service.py:148`, `:3347` -- `harvest_failures` columns; INSERT
- `pm_ai/connectors/graph/auth.py:307` -- `_msal_session`
- `pm_ai/connectors/graph/client.py:539` -- data client `timeout=30`
- msal 1.38.0 `application.py:710-713` -- the `partial` pattern
- `pm_ai/app/wiring.py:3290`, `:3439`, `:3699` -- both `_assert_usable` calls; `_assert_usable`
- `pm_ai/core/project_registry.py:101-102` -- `ProjectPathUnusable`, "today, a relative one"

## Tasks & Acceptance

**Execution:**
- [ ] `calendar.py`, `meetings.py`, `graph/__init__.py`, `meeting_records.py`, `pipelines.py` -- carry the end date in memory; `_on_day` places by range -- fix 1
- [ ] `harvest.py`, `channels.py`, `graph/__init__.py`, `ticks.py` -- the field, set, kept, logged -- fix 2
- [ ] `auth.py` -- the timeouts as named constants in `_msal_session` -- fix 3
- [ ] `wiring.py` -- `_assert_usable` refuses special folders, given the resolver's roots; `project_registry.py` -- `ProjectPathUnusable`'s docstring names this refusal too -- fix 4
- [ ] `tests/slice/test_dashboard_slice.py`, `tests/connectors/test_graph_calendar_mapping.py`, `tests/slice/test_scheduled_harvest.py`, `tests/connectors/test_graph_channel_messages.py`, `tests/connectors/test_graph_auth.py`, `tests/slice/test_project_onboarding.py` -- one test per matrix row, timeouts on a stub transport. `23e`'s single-day tests pass unchanged. A cut failure compared with its stored read-back differs (the stored copy has the field false): compare the stored fields, or expect that
- [ ] story specs, when built, each with a dated Change Log line: `23e` frozen placement sentence (`23e:27`) edited to the range rule; `8r` frozen transport bullet (`8r:29`), "MSAL's default timeout (none)" becoming 10 s connect and 30 s read; `33h` the cut's rule stands, its log wording changed; `1s` `project add` refuses the homes and `.project-ai` folders, and its rule skipping registered repositories inside a home (`1s:28`) stays, for hand-written registry rows, now the only way to reach it
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark the four `23h` entries (`:1381`, `:1390`, `:1399`, `:1417`) resolved; add the stored cut walk in `doctor`

**Acceptance Criteria:**
- Given a daemon whose run ends with only a cut channel walk, when the attempt settles, then `daemon.log` holds the cut wording, not "needs a person", and the next attempt is at the next regular cycle.
- Given the change, when `uv run pytest -q` runs, then all pass and the skip count is unchanged.

## Spec Change Log

- **2026-10-17, created.**
  - **Source:** Andrei's decisions of 2026-10-17 on the batch-1 review findings (memlog, "Batch-1 review decisions"). Four fixes in one slice because each is small and they touch different files.
  - **Review (one critical pass):** added the "no end date" rule, so 23e's tests are unchanged; case-insensitive home matching via `samefile`; the normal-layout, trailing-slash, home-folder and relative-path rows; real timeout totals with the retry adapter; the unmeasured all-day wire format; and doctor's unchanged line, with an Ask First on storing the marker. Hand-written registry rows are not refused (routine, Design Notes).

## Design Notes

**Hand-written registry rows (routine): not refused.** `parse_registry` is in `core`, which may not touch the filesystem or know `$HOME` (`_absolute`'s docstring), so it cannot resolve links or find the homes; a registry refusal stops every command but `doctor`; and `1s`'s fence keeps a hand-written home row safe. `project add` stores the resolved path, so a refused path cannot enter through it.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
