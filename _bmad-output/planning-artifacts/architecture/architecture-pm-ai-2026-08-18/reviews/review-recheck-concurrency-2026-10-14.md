# Recheck: the daemon concurrency amendment after its reviewer gate (2026-10-14)

**Scope.** Every `[2026-10-14]` passage in `ARCHITECTURE-SPINE.md` (AD-5, AD-6, AD-7, AD-19, AD-21, AD-37, AD-39, AD-43, the container diagram, the `GET /v1/status` enforcement row, Open Risks), read against the 2026-10-14 memlog entries (later entries superseding earlier ones where they say so) and four reviews: `review-adversarial-4e-2026-10-12.md`, `review-rubric-concurrency-2026-10-14.md`, `review-currency-concurrency-2026-10-14.md`, `review-adversarial-concurrency-2026-10-14.md`. Statements about today's code checked against `pm_ai/app/daemon.py`, `pm_ai/storage/service.py`, `pm_ai/connectors/registry.py`, `pm_ai/storage/crypto.py`, plus `pm_ai/surfaces/api/app.py`, `pm_ai/connectors/graph/auth.py` and `pm_ai/app/wiring.py` where a claim led there.

**Result.** 31 findings: **30 closed, 0 deliberately open, 1 still open** (rubric F9). The amended text has **one high contradiction** (AD-6 against AD-5 on whether the daemon can start without a master key), **four medium or low contradictions** and **six wording conflicts**. It also makes **one false statement about today's code**: that the status handler is the only thing blocking the loop. The request log writes a file on the loop for every request.

---

## 1. Finding by finding

### review-adversarial-4e-2026-10-12.md

| # | Status | Closing text (spine unless marked) |
|---|---|---|
| 1 Lock and claim order | CLOSED | AD-5: "Inside the daemon, one writer thread makes every write and enters every claim … Every claim is entered on that thread, so the per-thread re-entry guard above is sound in the daemon." |
| 2 Shared connection, transactions spanning calls, busy wait | CLOSED | AD-5: "each claimed step opens `BEGIN IMMEDIATE` and ends with `COMMIT` or `ROLLBACK`, and checks that no transaction is open when it returns, so a busy refusal undoes only that step." Busy-wait half: "The daemon's SQLite busy wait is at most 100 ms." |
| 3 Deadlines abandoning claimed work | CLOSED | AD-5: "A deadline (the shutdown wait, a fetch budget, `run_bounded`) may abandon only a bounded step." |
| 4 Blocking calls on the loop | CLOSED (but see §3, false statement 1) | AD-19: "Nothing on the loop thread blocks: no keychain call, subprocess, file or SQLite access, writer call or claim." |
| 5 Freshness of composed state | CLOSED | AD-5: "The read half of every read-modify-write is inside the claim … `config.toml`, the connector list and each connector row, and credentials: read again at the start of every job and every command the daemon runs for a client." |
| 6 Migration while the daemon runs | CLOSED | AD-5: "`operational.db` is migrated only under its own claim … the schema-version check is the first statement inside each claimed step's `BEGIN IMMEDIATE`." |
| 7 AD-37 lock in one process's memory | CLOSED | AD-37: "the per-target lock is a row in `operational.db`, never a lock in one process's memory and never an AD-5 claim." |
| 8 Plaintext warning with no reader under launchd | CLOSED | Open Risks: "prints the warning once, at start, while it runs in the foreground (a terminal); the start-at-login (launchd) slice owes the warning a reader (`doctor`, or the first client to connect)." |

### review-rubric-concurrency-2026-10-14.md

| # | Status | Closing text |
|---|---|---|
| F1 Credential saved only at the end of the fetch | CLOSED | AD-5: "A refreshed credential is saved when it is issued, not at the end of the fetch." AD-39 bullets 1 and 2 match. |
| F2 Two halves do not fit `9a`; "started" undefined | CLOSED | AD-5: "A job is a sequence of steps, each either bounded or claimed … `9a`'s harvest is calendar fetch, save, transcript fetch, save." Shutdown: "It finishes the step it is running and every step already queued to it." |
| F3 AD-37 against AD-5 | CLOSED | AD-37: "The row is taken by one conditional insert … in a claimed step (AD-5). The provider call runs in a bounded step." |
| F4 Old design in four places | CLOSED (one remnant: AD-19's heading, §2 W4) | AD-19: "One asyncio event loop owns all waiting and scheduling"; "the model pool takes model work only"; AD-43: "In the daemon it is a bounded step, never part of the write"; Open Risks: "their specs still describe the design it replaced"; the diagram now has `WRITER` and `IOPOOL`. |
| F5 Refresh points cover scheduled jobs only | CLOSED | AD-5: "at the start of every job and every command the daemon runs for a client — each harvest cycle, each render, each line `4r` answers, each `connector check`." AD-7 matches. |
| F6 "A call into the writer" undefined | CLOSED (by a different answer: reads leave the writer) | AD-5: "Plain reads that are not the read half of a read-modify-write are not writer calls"; "A writer call made from the writer thread itself runs at once, not queued"; the master key is fetched at start. |
| F7 False present tense; no owners | CLOSED | AD-5: "**Not yet built** … Owners: `4r` builds … `9a` builds … `9b` builds …". The rest stays without an owner, and the spine says so: "have no owner until they are recorded in `stories.yaml`". |
| F8 Version check can race; "exit code 3" unclear | CLOSED | AD-5: "the daemon stops and writes nothing more to the store … prints it as its refusal, exit code 3." |
| F9 One conditional statement does not fit the `renders` upsert | **STILL OPEN** | Spine still reads "changes only through one conditional statement (`UPDATE … WHERE state = ?` or `WHERE version = ?`)" and names `9b`'s `renders` as the example. The memlog entry cites F9 but does not adopt it and gives no reason. |
| F10 "Submitted call" | CLOSED | The phrase is gone. The spine now says "hand work to it". |

### review-currency-concurrency-2026-10-14.md

| # | Status | Closing text |
|---|---|---|
| 1 Shutdown can close the writer under a running step | CLOSED | AD-5: "The wait is `writer.shutdown(wait=True, cancel_futures=True)` in `daemon.run`'s `finally`, before `storage.close()`"; "Every claimed step is safe to kill partway." |
| 2 Legacy implicit transactions | CLOSED | AD-5: "The writer's connection runs with `autocommit=True`; each claimed step opens `BEGIN IMMEDIATE`." |
| 3 Version check outside the transaction | CLOSED | AD-5: "the schema-version check is the first statement inside each claimed step's `BEGIN IMMEDIATE`." |
| 4 Keychain call on the writer thread | CLOSED (by a different answer: fetched at start) | AD-5: "The daemon fetches the master key once, at start … After start, no keychain call is made on the writer thread or inside a claim." |
| 5 Abandoned threads that are not daemon threads; provider premise | CLOSED | AD-5: "Bounded steps run only through `run_bounded` or a pool of daemon threads, never `asyncio.to_thread` or the default executor"; "Microsoft documents that Graph does not revoke the old refresh token … it may be for another provider." |

### review-adversarial-concurrency-2026-10-14.md

| # | Status | Closing text |
|---|---|---|
| 1 The writer thread blocks everything | CLOSED | AD-5: "A writer call is short and never waits … A client request waits at most one second for the writer thread; past that it is answered `503` with `Retry-After`." Duration: "measured by the slice that builds it … the spine states no figure until it is measured." |
| 2 Keychain prompt plus a shutdown with no time limit | CLOSED | AD-5: "The daemon fetches the master key once, at start"; "Every claimed step is safe to kill partway." |
| 3 Stale or lost rotation | CLOSED | AD-5: "The save is made only if the stored entry still equals the one the refresh started from … At most one refresh per connector instance is in flight … the credential store handed to a connector has no write method of its own." |
| 4 `9a` alternates four times | CLOSED | AD-5: "A job may alternate the two … a job stopped at a bounded step is marked `failed`, naming the last claimed step it finished." |
| 5 Commands through the daemon print stale state | CLOSED | AD-5: "A command naming a project enrolled since start is refused, naming the restart"; refresh "at the start of every job and every command the daemon runs for a client." |
| Lesser: version check overtaken; job left `running` | CLOSED | AD-5: "The job it was running stays `running` in the store; the next daemon start's restart pass (built by `4r`) marks stale `running` rows `failed`." |
| Lesser: the writer thread waits on itself | CLOSED | AD-5: "A writer call made from the writer thread itself runs at once"; "No code on the writer thread waits on a future from the I/O pool, the model pool or the loop." |
| Lesser: cursor save with two daemons | CLOSED (by AD-7, already there) | AD-7: "before `9a` attaches any timer, the daemon must hold a per-user exclusive claim for its lifetime that does not depend on the port." With that claim, two daemons never harvest at once. |

**Counts:** 30 closed, 0 deliberately open, 1 still open.

**Still open: rubric F9.** Suggested text for AD-5's transactions paragraph. Replace "changes only through one conditional statement (`UPDATE … WHERE state = ?` or `WHERE version = ?`), never read in one statement and written in another" with: "changes only through one statement, never read in one statement and written in another: a conditional update (`WHERE state = ?` or `WHERE version = ?`) when the new value depends on the old, an insert-or-replace when it does not."

---

## 2. Contradictions in the amended text

**C1 — High. AD-6 against AD-5: can the daemon start without a master key?**
- AD-6 says: "fetched lazily on first use so a fresh install boots without one" and "the invariant is restored by that ordering, not by an eager fetch that would refuse to boot."
- The new sentence in the same paragraph says: "The daemon is the exception: it fetches the key once, at start, and refuses to start without it."
- AD-5 says: "A locked keychain or an unreadable key refuses the start (exit code 3)."

Read together, a fresh install with no key enrolled can no longer start the daemon. That is what `LazyKeyCrypto` exists to prevent (`crypto.py:264-275`: "the first thing a fresh install does is boot"). The rule also says nothing about `PM_AI_DISABLE_ENCRYPTION`, under which no key is needed, or about a key enrolled after start.

Suggested AD-5 text: "**The daemon asks the keychain for the master key once, at start**, on the writer thread, before it listens. A locked keychain or a key it cannot read refuses the start (exit code 3, pm-ai's 'refused' status), naming it. A key not yet enrolled does not refuse the start: the daemon runs, and each step that would read or write an encrypted file is refused, naming `pm-ai key enrol`, with no further keychain call. A key enrolled after start takes effect at the next start, said in the shared restart sentence. With encryption turned off for the process (AD-6), no key is asked for. After start, no keychain call is made on the writer thread or inside a claim."

Suggested AD-6 text: replace "and refuses to start without it (AD-5)" with "and, with no key enrolled, starts and refuses each encrypted read or write instead (AD-5)".

**C2 — Medium. Shutdown accepts no new claimed step, yet shutdown writes.**
- AD-5 Shutdown says: "From then on the writer thread accepts no new claimed step except a credential save."
- The next sentence says: "Jobs whose bounded step is still running are marked `failed`." The steps paragraph adds that such a job "is marked `failed`, naming the last claimed step it finished."

Marking a job `failed` is an `operational.db` write, so it is a claimed step, and the writer has just stopped taking new ones.

Suggested text: "From then on the writer thread accepts no new claimed step except a credential save and the shutdown's own step that marks still-running jobs `failed`."

**C3 — Medium. A schema-version stop writes nothing, but shutdown marks jobs `failed`.**
- The migration paragraph says: "the daemon stops and writes nothing more to the store … The job it was running stays `running`."
- The Shutdown paragraph, which every stop runs through, marks jobs `failed`.

Suggested text, appended to the Shutdown paragraph: "A stop on a schema-version mismatch skips the marking: it writes nothing more to the store, and the next start's restart pass marks those jobs (below)."

**C4 — Medium. AD-7's lifetime claim against AD-5's claims.**
- AD-7 says: "the daemon must hold a per-user exclusive claim for its lifetime."
- AD-5 says: "a claim, and every `operational.db` transaction, opens and closes inside one claimed step"; "A writer call is short and never waits"; and "Every claim is entered on that thread."

A claim held for the process's whole life cannot open and close inside one short step.

Suggested AD-7 text: "…a per-user exclusive lock for its lifetime that does not depend on the port. It is not an AD-5 claim: it is taken once at start, before the writer thread exists, on a file no command claims, and released only when the process ends."

**C5 — Low. Can the credential store write or not?**
- AD-5 says: "the credential store handed to a connector has no write method of its own."
- AD-39 (unchanged) says: "a rotated credential leaves it only through the store the composition root handed it, which seals the new value" and "writes a rotation back through `replace_credential`."

Suggested AD-5 text: "in the daemon, the credential store handed to a connector cannot write by itself: its save hands a compare-and-save step to the writer thread and waits for the answer."

**C6 — Low. Which jobs refresh a credential?**
- AD-39 bullet 2 says: "A refresh happens inside a harvest the scheduler started."
- AD-5 says: "Every job that may refresh (a harvest, a render's calendar read, `connector check`)."

Suggested AD-39 text: "A refresh happens inside a job the daemon runs (a harvest, a render's calendar read, `connector check`) or a foreground command, during a bounded step, …"

### Wording conflicts (low)

- **W1. Two busy-wait figures.** AD-5's 4e paragraph says "the standard library's five-second busy wait (pm-ai sets none of its own)". The new rule says "The daemon's SQLite busy wait is at most 100 ms". Suggested: add "(today; from `4r` the daemon's own connection waits at most 100 ms, below)" after "pm-ai sets none of its own".
- **W2. Every claimed step opens a transaction?** "each claimed step opens `BEGIN IMMEDIATE`" and "the schema-version check is the first statement inside each claimed step's `BEGIN IMMEDIATE`" read as covering file-only steps too, such as the credential save and the `meetings/` append. Suggested: "each claimed step that touches `operational.db` opens …" in both places.
- **W3. Only two credential steps?** AD-5 says "The fetch hands two claimed steps … These are the only claimed steps a bounded step may hand over." Today's refresh (`auth.py:919-960`, story 8m) re-reads after a refused acquisition and saves again after a lost race. That is more than two steps. Suggested: "The fetch hands only two kinds of claimed step to the writer thread: a read of the stored credential, and a compare-and-save. These are the only kinds a bounded step may hand over."
- **W4. AD-19's heading.** It still says "Single asyncio loop for I/O", which contradicts its own rule ("Blocking I/O … runs on the daemon's pool of four I/O workers"). Suggested heading: "One asyncio loop for waiting and scheduling; blocking work on the writer thread and two bounded pools."
- **W5. Where does CPU work that is not a model run?** AD-5 says sanitization, normalization and extraction are "prepared on a worker". AD-19 sends CPU work to "a bounded worker pool" whose examples are all models, and says "the model pool takes model work only". Suggested addition to AD-19: "CPU work that is not a model (sanitization, normalization, extraction) runs on the I/O pool."
- **W6. A size cap from the wrong job.** "A claimed step is bounded in size by the harvest page cap" applies a harvest limit to every claimed step, including a request row and a credential save. Suggested: "A harvest's claimed step is bounded in size by the harvest page cap; …"

---

## 3. Statements about today's code that are false

1. **AD-19: "`4e`'s status handler is the one current exception".** The enforcement row says the same: "It is the one current exception to AD-19's rule that nothing on the loop thread blocks." This is false. `AuthenticatedApp.__call__` (`surfaces/api/app.py:199`) logs every request on the loop thread through a `RotatingFileHandler` (`storage/service.py:1521`, attached in `daemon.py` `_RequestLog.attach`). That is a file write, and sometimes a file rotation, on the loop for every request.

   Suggested AD-19 text: "`4e`'s status handler and its request log line, written on the loop by a file handler, are the current exceptions, and both move off the loop in `4r`." Make the same edit to the enforcement row.
2. **AD-5: "Anything slow or interactive (the keychain fetch) happens before the claim."** Today this holds only where a caller fetches the key first: `assert_writable` calls `LazyKeyCrypto.ready()` (`service.py:1672`), and enrolment and some CLI paths call it. Nothing enforces it. A first decrypt inside `GraphAuth._claimed` (`auth.py:919`) fetches the key inside the claim. "Not yet built" mentions only the daemon's version of this.

   Suggested: "Anything slow or interactive (the keychain fetch) must happen before the claim; outside the daemon today that rests on callers fetching the key first (`assert_writable`), and nothing enforces it."

Checked and true:
- Today the daemon has no writer thread and enters no claim.
- `access_token` saves inside the fetch (`auth.py:943`, `:960`).
- `LazyKeyCrypto` fetches the key on first use (`crypto.py:288-290`).
- `_migrate` takes no claim (`service.py:821`).
- pm-ai sets no busy timeout of its own (`service.py:759`).
- `run_bounded` uses a daemon thread and abandons it rather than killing it (`registry.py:111`).
- `daemon.run` closes storage in its `finally` (`daemon.py:157-163`).
- Exit code 3 is pm-ai's refusal code (`dispatch.py:159`).

---

## 4. Plain language for a non-engineer owner

- **AD-5's transactions paragraph** is written entirely in SQLite terms (`autocommit=True`, `BEGIN IMMEDIATE`, `rowcount`). Suggest a lead sentence: "Each claimed step either saves all of its database changes or none of them, and a change that depends on the old value checks that nobody else changed it first."
- **Shutdown**'s mechanism sentence (`writer.shutdown(wait=True, cancel_futures=True)`, "the web server's lifespan", "`asyncio.run` cancels") belongs to the slice spec. Suggest keeping only the plain part: "The daemon waits for the writer at the very end of its own stop sequence, where a second Ctrl-C cannot skip it."
- **"a pool of daemon threads"** clashes with "the daemon", which means pm-ai's own process everywhere else. Suggest: "background threads that do not keep the process alive when it exits".
- **"exit code 3"** appears unexplained in two places. Suggest: "exit code 3 (pm-ai's 'refused' status)".
- **"`503` with `Retry-After`"**. Suggest: "answered 'busy, try again shortly'".
- **"Every claimed step is safe to kill partway"**: "`flock` released" and "`os.replace`" are fine as parentheses after a plain sentence. The paragraph already starts with one.
