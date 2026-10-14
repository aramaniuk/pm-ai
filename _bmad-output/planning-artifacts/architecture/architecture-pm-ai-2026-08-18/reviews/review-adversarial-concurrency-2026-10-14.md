# Adversarial review — the daemon's concurrency rules (spine amendment, 2026-10-14)

**Scope.** The 2026-10-14 amendment to `ARCHITECTURE-SPINE.md`: AD-5's new paragraphs (one writer thread; the bounded half and the claimed half; when the daemon re-reads what it composed; migration), and the matching edits to AD-7, AD-19, AD-21, AD-26 and AD-37. I read the amendment against the 2026-10-12 review it answers, the code it has to sit on top of (`pm_ai/app/wiring.py` `SealedRefreshTokenStore`, `pm_ai/storage/crypto.py` `LazyKeyCrypto`, `pm_ai/storage/service.py` `_OperationalConnection`), and the queued specs for `4r`, `9a`, `9b` and `4q`. Those specs predate the amendment, so I treat their *behaviour* as binding and their *mechanism* (4r's `threading.Lock`) as about to change.

**Method.** For each finding I name two units. Each unit obeys every AD as now written, and together they still build something incompatible or broken. Each finding ends with AD text that would close it.

**Verdict.** The amendment closes the lock-order and shared-transaction holes from 2026-10-12. But moving every storage call onto one thread moves the old loop-blocking problem onto that thread. The amendment itself puts two waits there: the credential store's two-second retry and SQLite's five-second busy wait. The code puts a third: the keychain fetch. So a single slow claimed half can stall `4r`'s answers, the status endpoint and shutdown. Credential rotation and `9a`'s harvest, which has four steps rather than two, also still have holes. Findings 1–3 need spine text before `4r` and `9a` are amended to match.

---

## Finding 1 — Critical: the one writer thread blocks everything behind it, and the amendment itself puts waits on it

**Unit A — `9a`'s claimed half, written to the amended AD-5.** After the fetch returns, this one call runs on the writer thread: the `meetings/` claim, then the batch (sanitized, encrypted, appended), then the cursor, coverage and `harvest_runs` rows, then the 90-day coverage prune. `9a` also hands every fetched transcript to `11b`'s ingestion in the same cycle, and ingestion is sanitization plus pattern extraction, all CPU work. AD-5 allows all of this to run inside the claimed half. The claimed half also *has to* be one call. Claims are entered on one thread, and the per-thread re-entry guard has no way to tell "another job holds `meetings/`" from "this thread re-entered `meetings/`". So if two jobs' claimed halves took turns between calls, the guard would raise `ClaimReentered`, a programming error, rather than `ArtifactBusy`.

**Unit B — `4r`'s request path.** `POST /v1/prompt` must write the `requests` row before it sends `202`, and every inline command (`config show`, `help`) reads storage. Under the amended AD-5 all of that is a call submitted to the writer thread. So is the status endpoint once `4r` moves it off the loop. `doctor` gives that endpoint two seconds.

**The waits are built in, not occasional:**

- AD-5 as amended: the sealed credential store's wait (`CLAIM_WAIT_ATTEMPTS × CLAIM_WAIT_INTERVAL`, 40 × 0.05 s, about 2 s of `time.sleep`) "happens in a job's claimed half, on the writer thread". A busy `config.json` therefore puts the whole daemon to sleep for two seconds.
- AD-5, unchanged: the daemon's connection keeps the standard library's 5-second busy wait ("pm-ai sets none of its own"). A hand-run `pm-ai dashboard` or `connector add` holding `operational.db`'s write lock stops the writer thread, and everything waiting on it, for five seconds per call.
- The 2026-10-12 review raised this exact point for the lock (Finding 2, second paragraph). It is not closed. The stall has moved from "four workers" to "the whole daemon".

The result: `4r`'s `202` ("answers at once") and its inline under-five-second answers both wait for a harvest persist, a two-second sleep or a five-second busy wait. `doctor` then reports a running daemon as not answering. No unit broke a rule. AD-21's bound is declared per pipeline and does not count time spent waiting for the writer thread.

**Proposed AD-5 text (replaces the writer-thread paragraph):**

> **Inside the daemon, one writer thread makes every write and enters every claim** `[2026-10-14]`. Reads that are not the read half of a read-modify-write are not writer calls. They run on the calling worker, on that worker's own read-only `operational.db` connection or as a plain file read. WAL means those reads never wait for the writer. **A writer call is short and never waits.** It makes no network call, no keychain call and no sleep. It does no sanitization, normalization, extraction, encryption key fetch or model work: those are prepared on a worker beforehand, and the call only persists the prepared bytes. It tries a claim once, and a held claim refuses the call as `ArtifactBusy`. It meets SQLite's lock with a busy wait of at most 100 ms (the daemon's connection sets this; the CLI keeps the default). Any retry, the sealed store's included, is the *submitting worker* resubmitting after its interval. The writer thread never sleeps. The daemon's request path waits at most one second for a writer call. Past that it answers `503` with `Retry-After`, never a late `202`. A claimed half is one writer call, because claims are entered on one thread and the per-thread guard could not tell two interleaved jobs apart. Its size is bounded by the harvest page cap, and its duration at that cap is measured and recorded beside the cap.

And strike "in the daemon it happens in a job's claimed half, on the writer thread, never inside a bounded call" from the sealed-store sentence. Replace it with: "in the daemon the wait is the submitting worker's, between attempts of a claimed half; never a sleep on the writer thread."

---

## Finding 2 — Critical: a keychain prompt on the writer thread plus a shutdown that waits without a limit gives a daemon that cannot stop

**Unit A — `LazyKeyCrypto` (`storage/crypto.py:270-300`), unchanged and AD-6-compliant.** It fetches the master key from the keychain the first time an encrypted artifact is read or written, and caches it. In the daemon, that first touch is a writer call, often inside a claim: the first sealed `config.json` rotation under its claim, or the first encrypted `meetings/` write under the `meetings/` claim. AD-5 says "anything slow or interactive (the keychain fetch) happens before the claim". The code does it inside the claim, and nothing in the amendment makes the key be fetched any earlier. A locked login keychain or an access dialog then blocks the writer thread until someone clicks.

**Unit B — the amended shutdown rule.** It "waits without a limit for every claimed half that has started". Ctrl-C during the dialog waits forever. Under launchd (the start-at-login slice), "without a limit" is not true either: launchd sends SIGKILL after its exit timeout, 20 s by default. So the guarantee that actually holds is "a claimed half may be killed partway", and the spine nowhere requires a claimed half to be safe to kill.

Each unit obeys its AD. Together the daemon hangs at a terminal and is killed under launchd, with a claimed half partly written either way.

**Proposed AD-5 / AD-6 text:**

> **The daemon fetches the master key once, at start, on the writer thread, before it listens and before the writer thread takes any call.** A key it cannot read stops the start, naming the keychain (exit 3). After start, no keychain call is made on the writer thread or inside a claim. The status endpoint's keychain probe runs on a worker. **Shutdown waits for a claimed half that has started, and nothing else.** A second interrupt, or a termination signal from a supervisor, ends the process at once. That is safe only because **every claimed half must be safe to kill partway**: the record rule (AD-47) holds for each append, a whole-file replace is publish-by-rename, and the `operational.db` rows that go together (cursor, coverage, attempt) are written in one transaction that comes last in the call. A kill therefore leaves either the previous position or the new one, never half of each. A test kills a claimed half partway and checks this.

---

## Finding 3 — High: a refreshed credential can be saved over a newer one, or lost because its job has no claimed half

**Unit A — three bounded halves that refresh one Graph instance at once, each obeying AD-26 as amended:** `9a`'s scheduled harvest; `4r`'s `dashboard` job, whose live calendar read AD-21 now calls a bounded half; and `connector check` sent through the daemon (`4q` routes it there as a job). Each reads refresh token R1 in its bounded half. Each refresh returns a different rotated token (R2, R3, R4) "from the fetch, saved in the claimed half".

**Unit B — the claimed halves.** AD-5 says "the read half of every read-modify-write is inside the claim". Each claimed half does re-read inside the claim, and then saves its own token anyway, because nothing tells it to compare. They are queued on the writer thread in whatever order they finished, so the last one saved can be the oldest rotation. Two further cases:

- A person runs `pm-ai connector sign-in` while a daemon fetch is in flight. The fetch then saves a token refreshed from the *old* sign-in over the new one. That is the AD-26 failure ("records a needs-a-human failure the human already fixed") in a new form.
- `connector check` and the dashboard's live read have no claimed half that writes `config.json`. AD-5 gives a refresh nowhere else to be saved, so the rotated token is thrown away. For a provider that retires the old refresh token on rotation, the instance is dead until the next sign-in.

Today the Graph connector saves inside `harvest` through `store.exclusive()` / `store.write()`. The amendment forbids that in the bounded half but does not take the write method away, so nothing stops it.

**Proposed AD-26 text (custody bullet, after "saved in the claimed half"):**

> **A rotation is saved only if nothing has replaced the credential since the fetch read it.** Inside `config.json`'s claim, the claimed half reads the instance's entry again. It saves the rotated credential only if that entry still equals the one the bounded half started from. Otherwise it drops its own rotation and logs that it lost the race, because the stored entry is newer: a later rotation or a person's sign-in. **In the daemon, at most one refresh per instance is in flight.** A second bounded half that needs a refresh waits for the first one's save, or uses its result. **Every job whose bounded half may refresh has a claimed half that saves the rotation**, `connector check` and the dashboard's live read included. **The credential store handed to a connector in the daemon has no write method**: a bounded half cannot save, by its type.

---

## Finding 4 — High: "a bounded half and a claimed half" does not describe `9a`'s harvest, which alternates four times

**Unit A — `9a`, frozen intent:** fetch the calendar (bounded), save position, coverage and failure together (claimed), *then* fetch transcripts ("after the position is saved; its time counts against the harvest"; network, so bounded), then hand them to ingestion (claimed). That is bounded, claimed, bounded, claimed.

**Unit B — the amended AD-5:** "A job has a bounded half and a claimed half. The claimed half starts only after the bounded half has returned … Shutdown … may mark `failed` only a job that has not entered one." A harvest in its transcript fetch has already entered a claimed half. Read literally, shutdown may not mark it `failed` and must wait out a network fetch with no limit, which is the one thing a deadline is supposed to cut short. The other reading is that `9a` must fit the transcript fetch inside the first bounded half, which contradicts `9a`'s rule that a transcript fault never loses a saved position.

**Proposed AD-5 text (replaces "A job has a bounded half and a claimed half"):**

> **A job is a sequence of steps, each bounded or claimed**, and no step is both. A deadline may abandon a job only at a bounded step. Shutdown waits only for a claimed step in progress. A job stopped at a bounded step is marked `failed` with its result naming the last claimed step it finished. Each claimed step leaves storage complete without the steps after it. `9a`'s harvest is four steps: calendar fetch, position save, transcript fetch, ingestion.

---

## Finding 5 — High: the re-read rules cover scheduled jobs only, so a command sent through the daemon prints stale state and AD-21's "same output as the terminal" fails

**Unit A — the amended AD-5 list:** `config.toml` is read again "at the start of each scheduled job (each harvest cycle and each render)". Connector rows and credentials are read again "at the start of each harvest attempt". Projects are read at start only.

**Unit B — `4r` and `4q`:** `config show`, `help`, `dashboard` and `connector check` sent through the daemon are request-path commands, not scheduled jobs. So after a hand edit of `config.toml`, `config show` typed at the REPL prints the daemon's start-time copy, while `pm-ai config show` at a shell prints the file. AD-21 (2026-10-10) says these must be the same. `connector check` sent through the daemon reports the connectors the daemon composed, which AD-26 requires to be "every enrolled row", while `doctor` (which `4q` runs locally) reads the rows fresh. That breaks AD-26's "one count". And "at each harvest attempt" refreshes one instance's row: it cannot tell the daemon a new instance exists, yet `9a` and `4q` both promise that a `connector add` is picked up at the next cycle.

**Proposed AD-5 text (replaces the list):**

> - enrolled projects (`projects.toml`): at daemon start only. Every command that names a project enrolled since start, whether scheduled or sent through the daemon, is refused, naming the restart.
> - `config.toml`, the enrolled connector rows (the list and each row) and credentials: read again at the start of **every unit of work**: each scheduled job, each job sent through the daemon, each inline command. Never a copy kept from an earlier unit.
>
> A command sent through the daemon prints what the same command at a terminal would print at that moment, apart from projects enrolled since start.

---

## Lesser findings

- **The per-call version check can be overtaken by a migration (Medium).** "Checks the schema version again at the start of each writer call" leaves a gap: a hand-run command can migrate between the check and the write. Proposed: *the check is the first statement inside the call's own write transaction (`BEGIN IMMEDIATE`, then `PRAGMA user_version`), so the migration, which holds the same write lock, cannot fall between them.* Also say that a refused call leaves its job `running` for the next start to mark `failed`, because the daemon may not write the `failed` itself.
- **The writer thread can deadlock waiting on itself (Medium).** If the façade always submits and waits, a claimed half that calls any storage method blocks the writer thread on its own queue. That is 2026-10-12 Finding 1's self-deadlock in a new form. Proposed: *a writer call made from the writer thread runs at once rather than being queued, and no code on the writer thread waits on a future: not the worker pool, not the model pool (AD-19), not the loop.*
- **A cursor read in the bounded half is safe only while one daemon owns cursors (Low).** `9a` runs harvests one at a time, so the claimed half's save cannot be overtaken inside one daemon. Two daemons can overtake it, and AD-7's per-user claim is still unbuilt. Proposed: *`save_cursor` is conditional on the cursor that was loaded (`WHERE cursor = ?`)*. This is the conditional statement AD-5 already requires for rows written by more than one process.

---

## Which 2026-10-12 findings are now closed

| 2026-10-12 finding | Status |
| --- | --- |
| 1 — the order of the writer lock and the claim | **Closed.** No lock any more; every claim is entered on the one writer thread. A new self-deadlock risk is listed above (writer thread waiting on itself). |
| 2 — a shared connection, transactions spanning calls, a busy rollback undoing another thread's work | **Rollback half closed** (one transaction per call; rows that hand-run commands also write change only through conditional statements). **Busy-wait half open**: the five-second wait now stops the whole daemon (Finding 1). |
| 3 — deadlines abandoning claimed work | **Closed for two-step jobs.** Open for `9a`'s four steps (Finding 4) and for shutdown under launchd (Finding 2). |
| 4 — blocking calls on the loop | **Closed** for the loop (AD-19), with `4e`'s status handler as the named exception until `4r`. The keychain call moved to the writer thread rather than going away (Finding 2). |
| 5 — how fresh the daemon's composed state is | **Partly closed.** Refresh points are declared and the scheduler is guarded. Commands sent through the daemon are not covered (Finding 5), and reading inside the claim without comparing does not stop a stale credential being saved (Finding 3). |
| 6 — migration while the daemon runs | **Mostly closed.** The check can still be overtaken (Lesser findings). |
| 7 — AD-37's per-target lock in one process's memory | **Closed.** |
| 8 — the plaintext warning with no reader under launchd | **Closed** as a recorded obligation on the launchd slice. |

## What holds up

- One transaction per writer call, plus conditional statements for rows that another process also writes. This is the right fix for 2026-10-12's busy-rollback finding.
- Entering every claim on one thread makes the per-thread guard sound in the daemon, as long as each claimed half is one call (Finding 1 makes that explicit).
- "Nothing on the loop thread blocks" closes AD-19's gap, and naming the one exception, with the story that removes it, is honest.
- AD-37 as AD-5's claim or a conditional statement, never a lock in one process's memory, is correct for as long as hand-run commands still write (until story 10).
