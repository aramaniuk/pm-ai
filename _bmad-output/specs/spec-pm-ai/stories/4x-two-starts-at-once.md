---
title: 'Two starts at once'
type: 'bugfix'
created: '2026-10-20'
status: 'done'
baseline_commit: '2d8a55912a7b6def3dcbfd35d7214d4555a1b5df'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `tests/slice/test_daemon_slice.py::test_two_starts_at_once_leave_one_daemon_and_one_token` fails about one run in six. Two things go wrong, and the test fails either way.

- **The store.** Both starts open `operational.db` while they read `projects.toml` and `config.toml` (`wiring.bootstrap`), before either one reaches the port. On a brand-new store both ask SQLite to switch it to WAL. The second one is refused busy at once, and its busy wait is never used. It then exits 1 with a traceback (`OperationalStoreBusy`), when it should lose at the port with exit code 3.
- **The test's reader.** `_wait_listening` called `select` on the daemon's output pipe and then read one line. When the mode-walk line and the "listening on" line arrive together, that one read takes in both, and the second stays in Python's text buffer, where `select` cannot see it. The winner is serving, but the test says it never announced itself and kills it after 30 s.

Andrei approved the fix on 2026-10-20.

## Boundaries & Constraints

**Always:**
- **The store's first switch to WAL waits for the other opener.** When `PRAGMA journal_mode=WAL` is refused busy at store open, the store tries again every 10 ms. It stops once the connection's own busy wait has passed since the first try (5 s by default, the standard library's). One try already under way can run its own busy wait past that point. After that, the open is refused with `OperationalStoreBusy`. The refusal names the store, the time measured and the busy wait, and says the store was not switched to WAL. The port still decides between two daemon starts. The change is in the store's open alone, so it covers every pm-ai command that opens a brand-new store, not only `daemon run`.
- **The daemon tests read the announcement line by line on a thread.** `announced_lines` in `test_daemon_slice.py` replaces the `select`-based reader. It reads every line up to and including "listening on", and it fails with what was read if the daemon says nothing more within 30 s. `test_daemon_requests_slice.py`'s `_wait_listening` uses it too, and the pty and REPL tests reach it through that function.

**Ask First:** Nothing.

**Never:** The flaky test keeps what it checks: one daemon, the loser refused with exit code 3 naming the port, and one token. Nothing is added to it, and no sleep in particular. The port is not moved before composition. The daemon's writer connection (`adopt_daemon_connection`) is unchanged.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Two daemon starts, fresh home | both open the brand-new store at once | one serves; the other exits 3 naming the port | N/A |
| Write lock held, then released | another connection holds the write lock on the empty store, and lets go after 0.3 s | opened in WAL after about 0.3 s | N/A |
| Write lock held; the other switches the store first | as above, but the other switches the store to WAL before letting go | opened in WAL | N/A |
| Write lock held past the busy wait | busy wait 100 ms or 300 ms; the lock is never released | refused after at least the busy wait and less than 1 s more; the store is still in `delete` mode | `OperationalStoreBusy` naming the store and the busy wait |
| Store already in WAL | any store opened before | one try, no lock asked for | as before |
| Two lines in one write | the mode-walk line and the announcement arrive together | both lines read; the announcement is returned | N/A |

</frozen-after-approval>

## Code Map

Verified against `2d8a559` on 2026-10-20.

- `pm_ai/storage/service.py:2130` -- `StorageService.__init__`'s `PRAGMA journal_mode=WAL`, the refused statement
- `pm_ai/storage/service.py:1126` -- `_OperationalConnection`, which turns a busy refusal into `OperationalStoreBusy`
- `pm_ai/app/wiring.py:3657`, `:3688`, `:973` -- `bootstrap` → `application_storage` → `_writer` builds the first `StorageService`, before `_compose`'s handler for an unusable store
- `pm_ai/app/daemon.py:321-323` -- the port, bound in `serve` after composition
- `tests/slice/test_daemon_slice.py:128` -- `_wait_listening`'s `select` and `readline`
- `tests/slice/test_daemon_requests_slice.py:114` -- the same reader, copied

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/storage/service.py` -- `_enter_wal` and `WAL_SWITCH_INTERVAL`, called from `StorageService.__init__` in place of the bare pragma -- the store
- [x] `tests/slice/test_daemon_slice.py` -- `announced_lines`; `_wait_listening` returns its last line -- the reader
- [x] `tests/slice/test_daemon_requests_slice.py` -- `_wait_listening` uses `announced_lines` -- the reader
- [x] `tests/slice/test_daemon_storage.py` -- a test for each store row of the matrix, and two races between real processes: the WAL switch on its own, and the whole open
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark the story-4s entry for this test resolved

**Acceptance Criteria:**
- Given the fix, when the flaky test is run alone 30 times, then all 30 pass.
- Given the change, when `uv run pytest -q` runs, then all pass and 11 are skipped, as before.

## Spec Change Log

- **2026-10-20, created, approved and built.**
  - **Source:** the 7e build's diagnosis, and the story-4s deferred-work entry, which suggested refusing a busy first open with exit code 3. That suggestion was not used, because the flaky test requires the loser to name the port. Andrei approved the fix on 2026-10-20.
  - **Measured before the fix (`2d8a559`):**
    - The flaky test alone: 5 failures in 30 runs, all from the test's reader. In a second loop of 40 runs: 11 from the reader and 2 from the store.
    - Two processes switching an empty file to WAL at once: in 297 rounds of 300, one was refused "database is locked" at once (0 ms). With a retry, 0 rounds of 300 were refused.
    - A write lock held on an empty file: the switch was refused in 0 ms, despite the 5 s busy wait.
  - **Measured after:** the flaky test alone, 0 failures in 30 runs, before the review fixes and again after them. The full suite: 6,599 passed and 11 skipped; `mypy` and `lint-imports` clean. The WAL-switch race test fails in its first round when `_enter_wal` is replaced by the bare pragma. The whole-open race refused the second opener once in about 27 rounds before the fix (5 runs of 60 rounds), so its 20 rounds are an end-to-end check, not the regression test.
  - **Review (blind hunter, edge-case hunter, verification gap), fixed:**
    - The refusal no longer says "nothing was changed", because the open may have created the empty file. It now says the store was not switched to WAL, and a test checks the file is still in `delete` mode.
    - The docstring says a try under way can run past the bound.
    - The bound is tested at two busy waits.
    - Added a test where the other opener switches the store first, and a whole-open race between real processes that checks the version is stamped once.
    - The reader's join is bounded on the failure path.
    - The fixture's docstring now says what it reproduces.
  - **Review, left as they are:**
    - The pragma's answer is still not checked to be `wal`. That was true before this fix, and changing it is not part of this story.
    - Importing between test modules follows `test_logs_command.py`'s existing `import test_daemon_slice`.

## Design Notes

**Why the store waits, rather than the port moving first.** Every command opens the store in `bootstrap` before anything is composed, so two commands on a fresh home can meet the same refusal, and two daemon starts are only one case. A busy refusal here means only that another opener is switching the file, which takes a few milliseconds. Once it is switched, the file stays WAL and asking again succeeds. The port then decides between two daemons as it did before (4e).

**Not fixed here.** The first open in `bootstrap` is outside `_compose`'s handler for an unusable store. So a store still busy after the bound ends the process with a traceback (exit 1), not a refusal (exit 3). This fix makes that rare, and does not change it.

## Verification

**Commands:**
- `for i in $(seq 30); do uv run pytest -q tests/slice/test_daemon_slice.py::test_two_starts_at_once_leave_one_daemon_and_one_token; done` -- expected: 0 failures
- `uv run pytest -q` -- expected: all pass, 11 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

## Suggested Review Order

- The switch, tried again within the busy wait, and the refusal past it.
  [`service.py:1275`](../../../../pm_ai/storage/service.py#L1275)

- Called where the bare pragma was.
  [`service.py:2171`](../../../../pm_ai/storage/service.py#L2171)

- The reader on a thread, and its test with two lines in one write.
  [`test_daemon_slice.py:126`](../../../../tests/slice/test_daemon_slice.py#L126)
  [`test_daemon_slice.py:165`](../../../../tests/slice/test_daemon_slice.py#L165)

- The store's rows and the two races.
  [`test_daemon_storage.py:246`](../../../../tests/slice/test_daemon_storage.py#L246)
  [`test_daemon_storage.py:401`](../../../../tests/slice/test_daemon_storage.py#L401)
