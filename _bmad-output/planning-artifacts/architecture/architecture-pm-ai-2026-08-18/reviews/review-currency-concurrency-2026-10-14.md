# Currency review: daemon concurrency amendment (2026-10-14)

Lens: is each technical premise the amendment relies on true of the pinned stack? The stack: Python 3.14.7 via `uv run`, SQLite 3.53.1 as bundled, uvicorn 0.52.3, starlette 1.6.0 and fastapi 0.141.1 from `uv.lock`, on macOS.
Method: I ran the experiments with `uv run python` in the session scratchpad (`scratchpad/cc/{a,s,s2,c}.py`). I read the installed uvicorn source and checked `man launchd.plist`. One provider premise came from Microsoft's documentation.

**Verdict:** The rules are sound in direction. Two of them are not true of the code as it stands, and nothing in the amendment says what makes them true:
- that shutdown waits for every claimed half before closing the writer;
- that every `operational.db` transaction begins and ends inside one submitted call.

Each needs one sentence that names the mechanism.

## Premises confirmed by measurement

| Premise | Result |
|---|---|
| The standard library's five-second busy wait | `sqlite3.connect` with no `timeout=` reports `PRAGMA busy_timeout` = 5000 |
| Under WAL a stale snapshot is refused at once | An explicit deferred `BEGIN`, then a read, then a commit from another connection, then a write: refused `database is locked` in 0.000 s with `timeout=5.0` |
| A busy refusal does not roll anything back by itself | After `SQLITE_BUSY` the refused connection is still `in_transaction == True`. pm-ai's `_OperationalConnection._busy` rolls back explicitly, which is what the rule needs |
| A conditional `UPDATE … WHERE state = ?` works as a compare-and-swap | `rowcount` is 1, then 0 on a repeat. The caller must check `rowcount`, so the amendment should say so |
| The connection can be used from a thread other than the one that opened it | `StorageService` opens it with `check_same_thread=False` (`service.py:763`). With the default `True`, a writer thread could not use a connection opened on the main thread. Nothing enforces "one thread": the rule is a discipline |
| A deadline abandons a thread and does not kill it | After `asyncio.wait_for(loop.run_in_executor(...), 0.2)` times out, the call finishes on its thread at 1.0 s. `run_bounded`'s docstring already says this. The amendment rightly says "abandon", not "cancel" |
| `shutdown(cancel_futures=True)` cancels only the halves that have not started | Two queued futures were cancelled and the running one completed. This matches "may mark `failed` only a job that has not entered one" |
| uvicorn waits for lifespan shutdown with no limit | `LifespanOn.shutdown` awaits `shutdown_event.wait()` with no timeout. Caveat in finding 1 |

## Findings

### 1. High: shutdown can close the writer while a claimed half is still running

Four facts are confirmed, the first three by measurement or by reading the installed source:

- **Closing the connection under a running writer loses its work.** `asyncio.run` cancels any task still awaiting a writer future, but the writer thread keeps going. `daemon.run` then calls `daemon.storage.close()` in its `finally` (`daemon.py:158–163`). In the `c.py` experiment the close succeeded while a claimed half was mid-transaction. That claimed half then raised `ProgrammingError: Cannot operate on a closed database`. Its uncommitted writes were discarded, and the job's outcome went to a task that had already been cancelled.
- **A second Ctrl-C skips lifespan shutdown entirely.** In uvicorn 0.52.3, `Server.shutdown` runs `if not self.force_exit: await self.lifespan.shutdown()` (`server.py:301`). pm-ai's `_Server._stop` sets `force_exit` on a second SIGINT. So a wait placed in `_lifespan`, where the docstring says background work stops, never runs on that path.
- **The interpreter waits for the writer thread anyway.** `concurrent.futures.thread._python_exit` calls `t.join()` with no timeout. In the experiment `atexit` fired at 1.01 s, after the writer finished. The process waits for the writer, but only after the connection is gone.
- **launchd kills after a set time.** `launchd.plist` documents `ExitTimeOut` as a "system-defined" time between SIGTERM and SIGKILL; I did not measure its value. Under launchd, "without a limit" therefore has one.

**Suggested text:**

> Shutdown waits for the writer by `writer.shutdown(wait=True, cancel_futures=True)` in `daemon.run`'s `finally`, before `storage.close()` — not in the lifespan, which a second Ctrl-C skips, and not on the asyncio tasks that await it, which `asyncio.run` cancels.

Then state the one limit: a forced stop (a second Ctrl-C, or launchd's SIGKILL) ends the process mid-claimed-half. What that leaves is safe:
- the flock is released when the process dies;
- the open SQLite transaction is rolled back the next time the store is opened;
- whole-file writes are `os.replace`, so a file is either the old version or the new one.

### 2. High: the connection does not keep a transaction inside one submitted call

The connection is opened in the legacy mode (`isolation_level=''`, `autocommit == LEGACY_TRANSACTION_CONTROL`). In that mode the library opens a transaction implicitly before any INSERT, UPDATE or DELETE and keeps it open until someone calls `commit()`. Measured:
- A call that wrote and did not commit left `in_transaction == True`.
- The next call's `rollback()` then undid the first call's write as well.
- `_busy` rolls back whatever transaction is open, so a busy refusal in call N undoes any call before it that did not commit.

The rule as written is therefore a discipline that nothing checks.

**Suggested text.** One sentence in AD-5:

> The writer's connection runs with `autocommit=True` (3.12+); each submitted call opens `BEGIN IMMEDIATE` and ends with `COMMIT` or `ROLLBACK`, and the writer asserts `not in_transaction` when the call returns.

Two reasons for this shape:
- Do not use `autocommit=False`. It keeps a transaction, and so a read snapshot, open at all times, which makes the stale-snapshot refusal the normal case.
- `BEGIN IMMEDIATE` goes through the busy wait (measured: refused after 0.33 s at `timeout=0.3`). So the "refused at once because its snapshot went stale" case cannot happen inside the writer. `OperationalStoreBusy`'s two-way wording stays correct for the commands that still write for themselves.

### 3. Medium: the per-call schema-version check can miss a migration

A migration takes `operational.db`'s claim, but the daemon's writer call does not. Only SQLite's own lock puts the two in order. In legacy mode a version `SELECT` runs outside any transaction, so a migration can commit between the check and the write.

**Suggested fix:** read the version inside the call's `BEGIN IMMEDIATE`, after the write lock is held. SQLite re-prepares statements after a schema change on its own, but that does not protect the meaning of the columns.

### 4. Medium: a keychain call can block the writer thread with no limit

`keyring.get_password` can sit on an access prompt indefinitely, and a thread blocked there cannot be killed. Two parts of the amendment meet here:
- AD-19 allows a keychain call to be submitted to "the writer thread … or the worker pool". On the writer thread it would stop every claim behind it, and shutdown "waits without a limit".
- `LazyKeyCrypto` fetches the key the first time anything is encrypted or decrypted (`crypto.py:288`). In the daemon, the first seal could be saving a rotated credential in a claimed half. That puts a keychain call inside a claim, which AD-5 forbids, unless something fetched the key earlier. Today only `StorageService`'s pre-flight (`service.py:1672`) and a few CLI paths do.

**Suggested text:**

> A keychain call runs only on the worker pool, as a bounded half, never on the writer thread; the daemon fetches the master key (`LazyKeyCrypto.ready()`) in the bounded half of the first job that seals, and a keychain call that does not answer is abandoned like any bounded half.

### 5. Low: which threads a deadline may abandon

`run_bounded` uses daemon threads, so an abandoned thread does not hold the process open. Bounded halves sent through `asyncio.to_thread` or the default executor are different: those threads are not daemon threads. Measured: `asyncio.run` waited 2.0 s for an abandoned `to_thread` call. Its default-executor join is bounded at `THREAD_JOIN_TIMEOUT` = 300 s, after which interpreter exit joins with no limit.

**Suggested fix:** state that bounded halves run only through `run_bounded`, or through a pool of daemon threads.

A related premise to state per provider: an abandoned fetch that has already rotated a credential throws the new one away. That is harmless for Microsoft Graph. Microsoft's refresh-token documentation says the platform does not revoke the old refresh token when it is redeemed. It is not harmless for a provider that revokes the old token on use.

Source: [Microsoft identity platform refresh tokens](https://learn.microsoft.com/en-ca/entra/identity-platform/refresh-tokens).
