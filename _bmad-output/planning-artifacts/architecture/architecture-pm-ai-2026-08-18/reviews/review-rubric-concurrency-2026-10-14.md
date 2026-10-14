# Rubric review: the daemon concurrency amendment (2026-10-14)

**Scope.** The 2026-10-14 changes to `ARCHITECTURE-SPINE.md`: AD-5's four new paragraphs (one writer thread; bounded and claimed halves; reads inside the claim and refresh points; migration) and its edited claim paragraph, AD-7, AD-19, AD-21's dashboard clause, AD-37, AD-39's two credential bullets, the `GET /v1/status` enforcement row, and the two Open Risks bullets (plaintext warning, concurrency decided). Read against the whole spine, the adversarial review it answers (`review-adversarial-4e-2026-10-12.md`), the draft specs for `4r`, `9a`, `9b`, `4q`, and the code: `pm_ai/app/daemon.py`, `pm_ai/storage/service.py`, `pm_ai/connectors/registry.py` (`run_bounded`), `pm_ai/connectors/graph/auth.py`, `pm_ai/app/wiring.py`, `pm_ai/storage/crypto.py`.

**Verdict.** The amendment fixes most of the right divergence points. Replacing 4r's lock with one writer thread is a better answer than the adversarial review's lock ordering. Five problems need text before `4r` and `9a` are built. First, the new credential rule would lose a token the provider has already retired. Second, the one-bounded-half, one-claimed-half shape does not fit `9a`'s own harvest. Third, AD-37's new text contradicts AD-5. Fourth, four places in the spine and all three draft specs still describe the old design. Fifth, the refresh points cover scheduled jobs but not the commands the daemon runs for a client.

## Checklist

| Item | Result |
|---|---|
| Fixes the real divergence points for `4r`, `9a`, `9b`, `4q` | Mostly. It covers the lock and claim order, the shared connection, deadlines, the loop, freshness, migration and AD-37. It misses: freshness on the request path (F5); jobs with more than one fetch (F2); what counts as "a call into the writer" (F6) |
| Every amended rule is enforceable and prevents its stated divergence | No, in two places. The credential rule loses credentials (F1). The shutdown rule leaves "started" undefined and still lets a late claimed half run (F2) |
| No contradiction with each other or any other AD | No. AD-37's new text contradicts AD-5's claimed half (F3). AD-19's first sentence and AD-43's "on the one asyncio loop" contradict AD-19's new rule (F4) |
| Nothing stated about current code is false | No. Four present-tense statements describe code that does not exist yet (F7) |
| Plain language for a non-engineer owner | Mostly yes. "Bounded half" and "claimed half" are defined where they are first used. "Submitted call" and "exit code 3" for a daemon are unclear (F8) |

---

## F1: High. "Saved in the claimed half" loses a credential the provider has already retired

**Amended text (AD-5, AD-39).** "A credential refreshed during a fetch is returned from the fetch and saved in the claimed half, never inside the bounded call." AD-39 adds: "the fetch returns the refreshed credential and the claimed half saves it."

**Why it breaks.** `GraphAuth.access_token` (`connectors/graph/auth.py:884`) says why it saves right away: "AAD rotates the refresh token on use", and `_rotate` says "the provider has already retired the one this machine still holds". A refresh can also happen in the middle of a walk ("page three comes back 401" → `force_refresh`). Under the amended rule, the new token waits in memory until the whole fetch returns. If the fetch then fails, or a deadline abandons it, the only live credential is gone and the instance needs a person to sign in again. The adversarial review's Finding 3 proposed this sentence. It fixed the abandoned-write race by creating credential loss.

There is a second conflict. Today the refresh reads the stored credential under the claim inside the fetch, and after a refused acquisition it re-reads it, also under the claim (8m's compare-and-set and the concurrent-refresh retry). "The bounded half takes no claim" forbids both, so `9a` has to delete them or break the rule.

**Suggested text.** In AD-5, replace the sentence above with:

> **A refreshed credential is saved the moment it is issued, not at the end of the fetch.** The provider retires the old refresh token when it issues a new one (`connectors/graph/auth.py`, `access_token`). A new token held until the fetch returns is lost if the fetch is then abandoned or fails, and the instance then needs a person to sign in again. So a refresh is three short steps inside the fetch: read the stored credential (a claimed step), ask the provider (bounded, with its own deadline), compare and save (a claimed step). The fetch hands the two claimed steps to the writer thread and waits for them. It never enters a claim itself, and these are the only claimed steps a fetch may hand over. A save handed over by a fetch that a deadline has since abandoned is still made while the writer is open, because it holds the only live credential. Once shutdown has closed the writer, the save is refused and logged by name.

In AD-39's first bullet, replace "— that rotation write, retry included, is done in the job's claimed half (AD-5): the fetch returns the refreshed credential and the claimed half saves it `[2026-10-14]` —" with "— in the daemon, the read and the save are claimed steps the fetch hands to the writer thread, the save made as soon as the provider answers (AD-5); not yet built, owed by `9a` `[2026-10-14]` —". In the second bullet, replace "and its save happens in the claimed half (AD-5)" with "and its save is a claimed step handed to the writer thread as soon as the provider answers (AD-5)".

---

## F2: High. "A job has a bounded half and a claimed half" does not fit `9a`, and the shutdown rule leaves "started" undefined

**Amended text.** "A job has a bounded half and a claimed half … The claimed half starts only after the bounded half has returned … Shutdown waits without a limit for every claimed half that has started before it closes the writer; it may mark `failed` only a job that has not entered one."

**Why it breaks.**
- **`9a`'s harvest has two fetches.** `9a` says transcript fetching "runs inside the harvest after the position is saved", and `11b`'s ingestion follows. That is fetch, save, fetch, save. Read literally, the rule forbids it. Read loosely, it says nothing about the second pair.
- **"Together" in `9a`** ("saves position, coverage record and any failure together") is one transaction only if the whole claimed half is one call on the writer thread. The text says each *transaction* sits inside one submitted call. It does not say a claimed half *is* one call. An implementer can split it and lose the atomicity.
- **"Started" is undefined on a queue.** With one writer thread, a claimed half can be handed over and still be waiting behind another. Has it started?
- **Late claimed halves still run.** A job whose fetch is running at shutdown is marked `failed`. Its fetch returns between that mark and the writer closing, and nothing stops its claimed half. `4r`'s spec allows exactly this ("a render that finishes after being marked `failed` may still write its file").

**Suggested text.** Replace the paragraph from "**A job has a bounded half and a claimed half**" through "…it may mark `failed` only a job that has not entered one." with:

> **A job is a sequence of steps, each either bounded or claimed** `[2026-10-14]`. A bounded step (a network fetch, a provider call, anything with a deadline) runs on a worker, takes no claim and writes nothing. A claimed step runs on the writer thread and contains no network call. It is handed over whole: a claim is opened and closed inside one claimed step, and so is every `operational.db` transaction. A job may alternate the two. `9a`'s harvest is fetch, save, fetch transcripts, save, and the save of position, coverage and failure that `9a` calls "together" is one claimed step. A deadline (the shutdown wait, a fetch budget, `run_bounded`) may abandon only a bounded step. **Shutdown:** stop listening and cancel the timers. From then on the writer thread accepts no new claimed step except a credential save (above). It finishes the step it is running and every step already queued to it, with no time limit. Jobs with a bounded step still running are marked `failed`, and a claimed step they hand over later is refused and logged. Then the writer closes.

---

## F3: High. AD-37's new text contradicts AD-5's claimed half

**Amended text (AD-37).** "Until the daemon is the only writer (story 10), the per-target lock is AD-5's cross-process claim keyed by `target_ref`, or the conditional update in `operational.db` itself."

**Why it breaks.** AD-37's lock exists so that "two approved changes to one work item cannot interleave". It therefore has to be held *across* the mutation's call to the provider. AD-5 now says a claimed half "contains no network call" and a claim is never taken by the half a deadline may abandon. Both rules cannot hold. The claim also cannot be "keyed by `target_ref`": `StorageService.exclusive(scope=, artifact=)` takes a declared artifact path and puts a sidecar beside it, and `jira:alpha:issue:PAY-102` is not a path. Two implementers would pick two sidecar locations, and their locks would never meet. The second option ("the conditional update itself") is the one that works, but it is not described well enough to build.

**Suggested text.** Replace the new AD-37 paragraph with:

> **Until the daemon is the only writer (story 10), the per-target lock is a row in `operational.db`. It is never a lock in one process's memory and never an AD-5 claim** `[2026-10-14]`. An AD-5 claim cannot hold it: a claim names a file, not a `target_ref`, and a claimed step may not call the network, while this lock must be held across the call to the provider. The row is taken by one conditional statement (insert only if no row exists for that `target_ref`) in a claimed step. The call runs in a bounded step. The row is removed in the claimed step that records the outcome. A row left behind by a crash or an abandoned call is not released when time passes: it is the reconciliation task AD-20 names for a key found `in_flight`.

---

## F4: High. The old design is still written in four places, and the three draft specs build it

**Where.**
- **AD-19, first sentence:** "One asyncio event loop owns all I/O — connector harvests, Telegram long-poll, the loopback API, MCP calls." The new paragraph says no file, SQLite or subprocess access runs on the loop, and `9a` runs harvests on the pool because the connector clients are synchronous.
- **AD-19's new paragraph** says "the writer thread (AD-5) or the worker pool". AD-19's only named pool is the heavy-model pool, bounded to one job. `4r`'s pool of four for blocking I/O is not named anywhere in the spine. An implementer could send blocking I/O to the one-at-a-time model pool, or model work to the I/O pool.
- **AD-43:** "`git` is spawned synchronously … so under AD-19 it is I/O on the one asyncio loop … Acceptable because…". The new AD-19 forbids a subprocess on the loop, and under AD-5 the capture write now runs on the writer thread.
- **Open Risks, last bullet:** "`4r` and `9a` are not blocked on the spine." Their specs still say "one daemon-level lock every worker takes before calling the writer", a `threading.Lock` on the `Daemon` (4r Design Notes), "wait up to ten seconds for running jobs", and "a render that finishes after being marked `failed` may still write its file". `9a` and `9b` cite "the lock that serializes every storage call from a worker". An implementer of the spec as written builds the design the spine replaced.

**Suggested text.**
- AD-19, first sentence: "One asyncio event loop owns all waiting and scheduling: Telegram long-poll, the loopback API, timers, and awaiting work handed elsewhere. Blocking work never runs on it. Storage runs on the writer thread (AD-5). Blocking I/O, connector harvests included, runs on the daemon's pool of four I/O workers (`4r`). Heavy local models run on the model pool below."
- AD-19's new paragraph: replace "or the worker pool" with "or the I/O pool (`4r`); the model pool takes model work only".
- AD-43: replace from "so under AD-19 it is I/O on the one asyncio loop" through "not this." with "so in the daemon it runs on the writer thread (AD-5) and every other storage call waits for it, up to that timeout."
- Open Risks: replace "`4r` and `9a` are not blocked on the spine." with "`4r`, `9a` and `9b` are not blocked on the spine, but their specs still build the design it replaced (a worker lock, a ten-second shutdown wait, a render written after it was marked failed). They are re-derived against AD-5's writer thread and steps before any of them is built."
- Deployment diagram: add a `WRITER[Writer thread]` node between the API, scheduler and pool and `STORE`, and label `POOL` as the model pool. Otherwise the picture still shows workers calling the store.

---

## F5: Medium. Refresh points cover scheduled jobs only, so `4q`'s "same output as the terminal" fails

**Amended text.** "`config.toml`: read again at the start of each scheduled job (each harvest cycle and each render)". "connector rows and credentials: read again at the start of each harvest attempt."

**Why it breaks.** `4r` and `4q` run commands in the daemon for a client: `config show`, `dashboard`, and `connector check`. None of them is a scheduled job or a harvest attempt. `4q` promises "the same output as `pm-ai <words>` on the command line". Under the amended text, `config show` through the daemon shows the values from daemon start, while the terminal shows the file. A `dashboard` job reads the calendar with connector rows from daemon start. `connector check` probes credentials from daemon start.

**Suggested text.**

> - `config.toml`: read again at the start of every job and every command the daemon runs for a client: each harvest cycle, each render, each line `4r` answers;
> - connector rows and credentials: read again at the start of every job or command that uses a connector: each harvest attempt, each render's calendar read, each `connector check`.

Make the same change in AD-7's summary sentence.

---

## F6: Medium. "A call into the writer" is undefined

The new AD-5 paragraph does not say whether reads go to the writer thread. All storage calls share one `sqlite3` connection (`service.py:763`, `check_same_thread=False`). A read made on a worker while a write transaction is open on the writer thread sees that write before it commits. Three more gaps sit in the same place:
- **Self-wait.** A storage method called on the writer thread from inside a claimed step, through a forwarding proxy, waits on itself forever.
- **Keychain prompt.** `LazyKeyCrypto` (`storage/crypto.py:263`) fetches the master key on first decrypt. The first decrypt happens inside the credential store's claim, which is now on the writer thread. A keychain prompt there holds every storage call until someone answers it. The spine's existing sentence "Anything slow or interactive (the keychain fetch) happens before the claim" is not true of today's code.
- **Five-second answers.** Waits on the writer thread (the five-second busy wait, the credential file's `attempts × interval`, AD-43's `git` timeout) delay every inline answer that touches storage, which matters for AD-21's five seconds.

**Suggested text (append to "Inside the daemon, one writer thread…"):**

> "A call into the writer" is any call on the storage service, reads included, because they all share one database connection. A call made on the writer thread itself runs directly. Only other threads hand work over, or a claimed step that calls a storage method would wait on itself forever. The master key is fetched (`LazyKeyCrypto.ready`) in a bounded step before the first claimed step that decrypts, so a keychain prompt never holds the writer thread. The waits that do run on the writer thread are bounded and named: the database's five-second busy wait, the credential file's `attempts × interval`, and the capture guard's `git` timeout. An inline answer (AD-21) counts them in its bound.

---

## F7: Medium. Present-tense statements that are false about current code, and obligations with no owner

- AD-5 known limits: "the daemon keeps it by entering every claim on its one writer thread". There is no writer thread. In 4e the status handler runs on the loop and enters no claim. The replaced text was true.
- AD-5: "in the daemon it happens in a job's claimed half, on the writer thread". No such half or thread exists yet.
- AD-5: "`operational.db` is migrated only under its own claim". `_migrate` (`service.py:821`) takes no claim, and neither does the constructor that calls it.
- AD-39: "that rotation write, retry included, is done in the job's claimed half". Today `access_token` saves inside the fetch (`auth.py:884-960`).

The new rules also name no owning slice. The spine marks owed work elsewhere ("Owed by a slice before `33d`", "built by `33e`"), and AD-5 itself notes that the story-10 cutover "has no owner until it is recorded there".

**Suggested text.** In the known-limits clause, replace "the daemon keeps it by entering every claim on its one writer thread (below)" with "in 4e the daemon keeps it by taking no claim at all; from `4r` it keeps it by entering every claim on its one writer thread (below)". After the migration paragraph, add:

> **Not yet built** `[2026-10-14]`. Today the daemon has no writer thread and enters no claim (4e), `access_token` saves a rotated credential inside the fetch, and `_migrate` takes no claim. Owners: `4r` builds the writer thread, the steps for request jobs, and moving status off the loop. `9a` builds the harvest steps, the credential steps, the connector and config refresh, and the skip for projects enrolled after start. `9b` builds the render's refresh. The migration claim and the per-call version check have no owner until they are recorded in `stories.yaml` (through `bmad-spec`).

---

## F8: Medium. The migration check can race, and "exit code 3" is unclear for a daemon

"Checks the schema version again at the start of each writer call" runs outside the write's transaction. A thin client's migration, which takes no SQLite write lock of the daemon's, can land between the check and the write. "Stops … exit code 3, never a write" does not say whether the daemon process exits or only the call is refused.

**Suggested text.** Replace the second sentence of the migration paragraph with:

> A long-lived writer (the daemon) checks the schema version inside the same transaction as each write, after taking the database's write lock (`BEGIN IMMEDIATE`), so a migration by another process cannot land between the check and the write. When the store has moved past it, the daemon refuses that call and every later write, naming the version it found. The job or command that made the call ends with exit code 3. The daemon keeps answering status until it is restarted with the newer pm-ai.

---

## F9: Low. "One conditional statement" does not fit the rows both sides actually share

`9b` has the hand-run `pm-ai dashboard` and the daemon both write the `renders` row. That write is a plain "latest instant" upsert. A conditional update is the wrong shape for it, and the rule as written would force one.

**Suggested text.** Replace "changes only through one conditional statement (`UPDATE … WHERE state = ?` or `WHERE version = ?`)" with "changes only through one statement: an insert-or-replace when the new value does not depend on the old, a conditional update (`WHERE state = ?` or `WHERE version = ?`) when it does".

---

## F10: Low. Wording

"Submitted call" appears once and is engineering shorthand. "Handed to the writer thread" says the same in plain words, and F2's text uses it throughout. The plaintext-warning bullet now reads well and answers the adversarial review's Finding 8 as proposed.

---

## What holds up

- **One writer thread instead of one worker lock.** This removes every lock-and-claim ordering in the adversarial review's Finding 1, because claims never contend inside the daemon. It also makes the per-thread re-entry guard sound without changing it.
- **Transactions inside one call.** Every `operational.db` transaction begins and ends inside one call, so a busy rollback (`_OperationalConnection._roll_back`, `service.py:596`) can only undo the call that met it. This closes Finding 2.
- **Reads inside the claim, with refresh points.** Projects at start only, connector rows each attempt, and the skip for projects enrolled after start match `4q` ("needs a restart") and `9a` (re-read each cycle). They do not contradict each other.
- **The `GET /v1/status` enforcement row** now names itself as the one exception to AD-19 and gives `4r` the move, before `9a` attaches anything to the loop.
- **Unchanged and still true.** AD-21's dashboard clause and the "foreground (a terminal)" narrowing of the plaintext warning are accurate. `run_bounded` (`registry.py:84`) abandons a thread and does not kill it, exactly as the deadline rule assumes.
