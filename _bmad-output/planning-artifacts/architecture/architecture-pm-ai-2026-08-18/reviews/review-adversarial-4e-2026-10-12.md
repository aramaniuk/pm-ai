# Adversarial review — spine amendment for slice 4e (2026-10-12)

**Scope.** The 2026-10-12 amendment to `ARCHITECTURE-SPINE.md`: AD-5's "Every `pm-ai` invocation that writes is a writer, the daemon among them" paragraph and its new "The daemon keeps the claim" close; the closed "There is no daemon" enforcement-gap row; the narrowed plaintext-warning open risk. Read against the 4e code in the worktree (`pm_ai/app/daemon.py`, `pm_ai/surfaces/api/app.py`, `pm_ai/app/entry.py`, `pm_ai/storage/service.py`) and the next units that build on it: `4r` (prompt/jobs endpoints, four-worker pool, daemon writer lock, shutdown order), `9a` (scheduled harvest), `4q` (REPL through the daemon), and the thin-client commands that still write until story 10.

**Method.** For each finding, two units that obey every AD as written and still build incompatibly. Each one is a hole; each comes with the AD text that would close it.

**Verdict.** The amendment correctly describes 4e as built, but it fixes the concurrency rules to 4e's single-threaded shape ("its one handler runs on the loop's thread and takes no claim"). The specs already queued for `4r` and `9a` break that shape, and the spine says nothing about the in-process lock, the shared SQLite connection, deadlines, or the daemon's cached composition. The holes below are real conflicts between specs that are already written, not hypotheticals. Not ready to gate 4r/9a until findings 1–4 have spine text.

---

## Finding 1 — Critical: the daemon's writer lock and AD-5's claim have no stated order, and every way of nesting them breaks a rule

**Unit A — `4r` (spec line 28, 106):** "one daemon-level lock every worker takes before calling the writer", a plain `threading.Lock` on the `Daemon`, because `check_same_thread=False` serializes nothing. `9a` and `9b` take the same lock.

**Unit B — `9a` `run_harvest` on a pool worker**, obeying AD-5: it enters `StorageService.exclusive("meetings/")`, and inside the claim body it calls `MeetingRecords.put` and then `save_cursor`. Each of those is a "call into the writer".

There are only three ways to place the lock, and each one breaks something:

- **Lock taken per writer call, including inside a claim body.** Worker A holds the `meetings/` claim and blocks on the writer lock, which worker B holds while B's own job takes a claim. That is exactly what AD-5 forbids: "no wait on another thread that claims". If the lock is the non-reentrant `threading.Lock` that 4r names, and the claim was entered from inside a locked call, A also deadlocks itself.
- **Lock held for the whole job.** The four workers become one, and the 503 in 4r's "writer refuses the row" case now means "another job is running", not "the database is busy".
- **Claim taken outside the lock.** That is what the letter of AD-5 allows. But the claim body waits on the lock, and we are back to the first case.

The loop thread is a third participant. 4e's status handler reads through the writer on the loop thread without the lock. The 4e docstring says this is fine because the loop is "the only thread that touches the writer in this slice". Once 4r lands it is not: the loop thread and the workers share one `sqlite3` connection, and the loop thread holds no lock.

**Proposed AD-5 text (new paragraph after "Re-entry is per thread"):**

> **Inside the daemon, the writer lock is outermost and covers one unit of storage work.** A worker takes the daemon's writer lock, then any claim; it runs the claim body, releases the claim, then releases the lock. Each claim body is entered exactly once per lock hold. No thread waits for the writer lock while it holds a claim. No claim is entered in the daemon without the writer lock. Writer methods called inside a claim body do not take the lock again: they check that the lock is held, the same way `claimed=True` checks `holds_claim`. The loop thread never takes the writer lock and never calls the writer.

---

## Finding 2 — Critical: one SQLite connection shared by threads, with transactions that span calls and a busy rollback that undoes another thread's work

**Unit A — `OperationalStoreBusy` (4e, `service.py` `_OperationalConnection._busy`):** on `SQLITE_BUSY` it calls `_roll_back()`, which rolls back the connection's open transaction ("its open transaction was rolled back, so nothing from it was kept").

**Unit B — `4r`'s job-state moves (`UPDATE … WHERE state = ?`) and `9a`'s `save_cursor` + `harvest_runs` write**, running on different workers. If the lock is held per call (Finding 1), they share one connection. Python's `sqlite3` opens an implicit transaction on the first DML and keeps it open until `commit()`, across calls.

So worker A's half-written cursor/coverage pair is still uncommitted when worker B's insert into `requests` meets the thin client's write lock. B rolls back, and A's work goes with it. A then commits nothing, or commits a pair the matrix says can never be split. The amendment's sentence "a write that waits it out is refused by name … never left hanging" holds. What it does not say is that it may also discard a *different* write than the one that waited.

The 5-second busy wait has the same problem. The spine says "pm-ai sets none of its own". If that wait happens under the writer lock, all four workers stall for 5 s. If it happens on the loop thread (Finding 4), the loop stalls too.

**Proposed AD-5 text (append to the `operational.db` sentence):**

> Each `operational.db` transaction begins and commits, or rolls back, inside one writer call made under the writer lock. No transaction is open when the lock is released. A busy refusal can therefore only undo the call that met it. A row that a thin client also writes, before story 10, changes only through one conditional statement (`UPDATE … WHERE state|version = ?`). It is never read in one statement and written in another, because the writer lock does not reach the thin client's process.

---

## Finding 3 — High: AD-5's "no claim taken by work that can be abandoned on a timeout" conflicts with three deadlines the next slices require

**Unit A — AD-5 (unchanged):** "no claim taken by work that can be abandoned on a timeout."

**Unit B, three times over:**
- **`4r` shutdown order (spec line 30), cited by `9a`:** "wait up to ten seconds for running jobs; mark any still running `failed` … close the writer". A `9a` harvest on the pool takes the `meetings/` claim, and a Graph harvest that persists a refreshed credential takes `config.json`'s claim. Both are abandonable by this rule. The 4r spec even expects a straggler to keep writing after it has been marked `failed`.
- **AD-21 / `4r` per-connector fetch budget**, enforced by `run_bounded` (`connectors/registry.py:84`), which abandons a thread at the deadline. If the abandoned call refreshes and saves a token, it takes a claim inside abandoned work.
- **`4q` Ctrl-C "abandons the wait; the daemon finishes the command on its own".** This one is safe, because the work is not abandoned, only the client's wait. It is listed so the fix does not over-reach.

Each slice obeys its own AD. Together they write `config.json` from a thread nobody is waiting for, after the job row says `failed` and possibly after the writer was closed.

**Proposed AD-5 text (replace the clause "and no claim taken by work that can be abandoned on a timeout"):**

> A job has a bounded half and a claimed half. The bounded half (network fetch, provider call, anything with a deadline) takes no claim and writes nothing. The claimed half starts only after the bounded half has returned, on the same worker, and contains no network call and no wait except the writer lock. A deadline (the shutdown wait, a fetch budget, `run_bounded`) may abandon only a bounded half. Shutdown waits without a limit for every claimed half that has started before it closes the writer; it may mark `failed` only a job that has not entered one. A credential refreshed during a fetch is returned from the fetch and saved in the claimed half, never inside the bounded call.

---

## Finding 4 — High: AD-19 bans only CPU work from the loop, so the amendment approves blocking (and interactive) calls on the loop thread

**Unit A — 4e as approved by the amendment:** "its one handler runs on the loop's thread and takes no claim". `read_status` is `async` "on purpose" and calls `run_all` on the loop thread. That runs `keychain_reachable` (a Security-framework fetch, which AD-5 itself calls "slow or interactive"), `git_available` (a subprocess), `sqlite_extension_support`, and two file reads through `read_artifact`. AD-19 lets it: it moves only "CPU/GPU-bound work" off the loop.

**Unit B — `9a` timers and `4r`'s event stream, both attached to the same loop in `_lifespan`**, plus `4r`'s inline answers promised under five seconds (AD-21). One status request that meets a locked keychain stops every timer, every open stream and every inline answer until someone clicks the dialog. The AD-21 promise cannot be kept, and no unit broke a rule.

The amendment also turns the AD-5 known limit ("asyncio tasks sharing one thread are indistinguishable") into a fact about 4e ("its one handler … takes no claim") rather than a rule. The first async handler `4r` adds that reaches the writer reopens the limit.

**Proposed AD-19 text (add to Rule):**

> **Nothing on the loop thread blocks.** No keychain call, subprocess, file or SQLite access, writer call, or claim runs on the loop. Each is submitted to the daemon's bounded pool (`4r`) and the loop awaits the result. This is what makes AD-5's thread-local guard sound in the daemon: no claim is ever taken on the loop thread, so asyncio tasks never share a claim. 4e's status handler is the one exception, and it moves to the pool when `4r` lands.

And in AD-5, replace "the daemon keeps it (`4e`: its one handler runs on the loop's thread and takes no claim)" with "the daemon keeps it by AD-19: no claim is taken on the loop thread."

---

## Finding 5 — High: no owner for how fresh the daemon's composition is, so three slices give three answers and a stale credential can overwrite a fresh one

The daemon "composes once" (4e). Thin clients keep writing the files that composition came from: `projects.toml`, connector rows and credentials in `config.json`, and `config.toml`.

- **4e status:** reads `config.toml` and `projects.toml` again on every request.
- **4q (matrix line 46):** after `project add` while the daemon runs, "the daemon sees beta after a restart".
- **9a (line 23):** the connector list is re-read every cycle, so `connector add` for an instance in project beta gets a timer without a restart. That timer belongs to a project whose scope the running daemon did not compose (4q says it needs a restart), so `9a` harvests into a scope the daemon "does not watch", and `4q` refuses lines that name that scope.
- **Credentials:** `Daemon.connectors` hold what was composed at start. `pm-ai connector sign-in` (thin client) rotates the credential in `config.json` under the claim. The daemon then either keeps using the stale credential, which records a "needs a human" failure the human has already fixed, or refreshes from its composed copy and writes it back under the claim. AD-5's claim makes that write serialized, not correct: the claim protects a read-modify-write only if the read happens inside it. The amendment's "takes the same claim for any whole-file read-modify-write" does not say the read must be inside the claim.

**Proposed AD-5 / AD-7 text:**

> **The read half of every read-modify-write is inside the claim.** It is never a copy composed earlier or cached in memory. **Whatever the daemon composed from an artifact a thin client may write is a cache with one declared refresh point**, named beside the artifact in wiring:
> - the project registry: at each cycle of the scheduler, or on restart, but one of the two, used by every unit;
> - connector rows and credentials: read at the start of each attempt;
> - `config.toml`: on restart.
>
> A thin-client write the daemon will not see until a refresh point says so in its output, with one sentence shared by every such command. No unit acts on a scope the daemon has not composed.

---

## Finding 6 — Medium: no rule on schema migration while the daemon runs

**Unit A — the daemon** migrates `operational.db` once, at start (`_migrate`), and then runs for days. It refuses a newer version only at open.

**Unit B — a thin client from an upgraded install** (`uv sync`, the daemon not restarted) opens the same file, finds an older `user_version`, and migrates it under the running daemon. Nothing claims the migration. The daemon keeps writing the old shape into the new schema. `4r` and `9a` both add tables "without a version bump", which makes this skew hard to see.

**Proposed AD-5 text:**

> `operational.db` is migrated only under its own claim (`operational.db.lock`), and only by the process the version check says is allowed. Until story 10 that is any writer. A long-lived writer (the daemon) checks `user_version` again at the start of each writer call and stops, naming the version it found, when the store has moved past it. Exit code 3, never a write.

---

## Finding 7 — Medium: AD-37's per-target lock is in-process by default, and until story 10 the writers are cross-process

**Unit A — a Telegram or REPL approval through the daemon** (AD-37: CAS plus "a per-target lock keyed by `target_ref`"). The natural build is a dict of `threading.Lock` on the `Daemon`.

**Unit B — a thin-client approval or expiry path** (any `pm-ai` verb that changes a Proposal before story 10). It takes AD-5's cross-process claim, or nothing. Two locks never meet, so two approved mutations to one work item can interleave: the race AD-37 exists to prevent, now across processes.

**Proposed AD-37 text:**

> Until the daemon is the sole writer (story 10), the per-target lock is AD-5's claim on a sidecar keyed by `target_ref`, or the CAS row in `operational.db` itself. It is never a lock in one process's memory. The CAS is one conditional statement (Finding 2).

---

## Finding 8 — Low: the narrowed plaintext-warning risk assumes a terminal

The narrowed risk says the daemon "prints the warning once, at start", on stdout. The gap row names "start-at-login under launchd (a later slice)". Once the daemon starts at login, nothing reads stdout, so the warning is gone for exactly the writer the narrowing relies on. The event-log entry still records it. **Suggested:** in the open-risks bullet, add "…prints the warning once, at start, **while it runs in the foreground**. The launchd slice owes the warning a reader (`doctor`, or the first client to connect)."

---

## What holds up

- The gap-row closure is accurate to the code: loopback-only bind, a `0600` token by exclusive create that is never minted over, bearer check before routing, and an identity header so `doctor` can tell what answered.
- The token-by-exclusive-create and the request-log-in-`logs/` claims match `take_token` and `_RequestLog`. The daemon makes no whole-file read-modify-write in 4e.
- Naming `OperationalStoreBusy` rather than leaving a bare "database is locked" is right. Finding 2 is about what the rollback undoes, not about the name.
