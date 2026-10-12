# Currency / reality-check review — AD-5 amendment 2026-10-12 ("Re-entry is per thread")

Lens: was each claim the amendment commits to checked against the kernel, the Python documentation, or the code, rather than asserted from memory? Scope: the new AD-5 sentences from "**Re-entry is per thread**" to "`4e` revisits it with the daemon", and the matching memlog decision line.

Verdict: **Sound on the facts that matter, with one wording defect that contradicts the amendment's own reason.** The kernel behaviour, the `threading.local` limit and every claim about the code were confirmed. The defect: when another thread of the same process holds the claim, the refusal text still says "another pm-ai process". That blames a process that does not exist, which is the outcome the amendment was written to prevent. Two smaller residual limits are noted below.

## Sources checked

- **Run on this machine** (Python 3.14.7, x86_64 under Rosetta, macOS 26.6.2, APFS, under `uv run python -I`; script in the session scratchpad):
  - First descriptor holds `LOCK_EX`. A **second `os.open` in the same thread**, then `flock(LOCK_EX|LOCK_NB)`, gets **EAGAIN**. This is the old behaviour the amendment fixes: re-entry looked like "busy".
  - A `dup` of the holding descriptor is **granted**. It is the same open file description, so the lock is shared, not contended.
  - **Another thread** opening its own descriptor gets **EAGAIN** while the lock is held, and is **granted after `LOCK_UN`**. A blocking `flock` from another thread **waited 0.31 s and was granted** when the holder released. So cross-thread contention behaves like contention between processes and releases normally.
  - `os.O_EXLOCK` exists, so this CPython build has BSD `flock(2)` and does not emulate it with `fcntl`.
  - Two `asyncio` tasks on one loop see the **same** `threading.local` object and the same thread id. A function run through `asyncio.to_thread` sees a **different** one.
  - `Path.resolve()` does **not** fold case on case-insensitive APFS: `.../sub` resolves to `.../sub`, while the directory was created as `.../Sub`. The two spellings share one inode.
- **FreeBSD `flock(2)`** (the BSD lineage of Darwin's page): "Locks are on files, not file descriptors"; `dup`/`fork` give "multiple references to a single lock"; `LOCK_NB` fails with `EWOULDBLOCK`. The page does not spell out that separate `open()` calls contend. The local run above confirms it on Darwin.
- **Python `contextvars` docs**: state should use context variables "instead of `threading.local()` to prevent their state from bleeding to other code" in concurrent code, and context variables are native to asyncio. This confirms the asyncio limit the spine states.
- **Code**: `pm_ai/platform/claims.py` (`_HELD_HERE`, `exclusive`), `pm_ai/storage/service.py` (`_CLAIMED_HERE`, `_canonical`, `exclusive`, `holds_claim`), `pm_ai/domain/claims.py` (`ClaimHeld`, `ClaimReentered`), `pm_ai/ports/__init__.py` (`ClaimPort.exclusive`, `StoragePort.exclusive`/`holds_claim`), `pm_ai/app/wiring.py` (sealed credential store `exclusive`/`write`, `sign_in`), `pm_ai/core/connector_enrolment.py` (`record_user_id`), `pm_ai/connectors/registry.py` (`run_bounded`), `pm_ai/connectors/probe.py`; grep for `async def`/`asyncio` across `pm_ai`.

## Claim-by-claim

| Claim in the amendment | Status | Evidence |
|---|---|---|
| A second claim by the same thread raises `ClaimReentered` | Confirmed | `storage/service.py` checks `_claimed_here()` before the port is asked. `platform/claims.py` checks `_held_here()` before `mkdir`/`open`. The sealed store checks `_holder == get_ident()`. |
| …raised before any directory is created | Confirmed | Both guards run before `mkdir`. The storage guard also runs before `_report_older_build_marker`. |
| …a programming error, never retried | Confirmed for the code read | `ClaimReentered(RuntimeError)` is not an `OSError` and not an `ArtifactBusy`. The sealed store's retry loop catches only `ArtifactBusy` and `OSError`, so it propagates. No retry loop was found that catches it. Probes catch `Exception` and would *report* it as a failed probe. That is not a retry, but it is not "never reported" either (see F4). |
| …never `ArtifactBusy` | Confirmed | It is not a subclass, and the translation catches only `ClaimHeld`. |
| Another thread of the same process is ordinary contention, refused as `ArtifactBusy` | Behaviour confirmed (local run plus code); **wording not** (F1) | Each claim does its own `os.open`, so a second thread gets a separate open file description and EAGAIN, which becomes `ClaimHeld` and then `ArtifactBusy`. It releases normally. |
| The sealed credential store waits it out (memlog) | Confirmed | It loops `attempts × interval` on `ArtifactBusy`. Its own `threading.Lock` gives a truthful "another caller in this process" message for the same-instance case. |
| `StoragePort.holds_claim` answers whether this thread holds one | Confirmed | It reads the same `_claimed_here()` set. It is used by `record_user_id(claimed=True)`, which raises `ClaimNotHeld` otherwise. |
| The guard is thread-local, so asyncio tasks on one thread cannot be told apart | Confirmed | Local run and Python docs. |
| No async claimant exists today | Confirmed | No `async def` or `asyncio` use in `pm_ai`, only docstring mentions. |

## Findings

### F1 — MEDIUM — A cross-thread refusal still says "another pm-ai process"

The amendment's own reason is that "a busy refusal would blame a process that does not exist". It then routes another thread of the same process into that same refusal, and both messages on that path still name a process:

- `pm_ai/platform/claims.py` `ClaimHeld`: "another pm-ai process is holding {name} right now".
- `pm_ai/storage/service.py` `ArtifactBusy`: "{artifact} in {scope} is claimed by another pm-ai process".
- `pm_ai/domain/claims.py` `ClaimHeld` docstring: "Another process holds the claim".

The thread case can happen today. `run_bounded` starts daemon threads and abandons them on timeout. A health check that refreshes a token writes the sealed store from such a thread, and an abandoned thread keeps its claim until it finishes. Another claim in the same command would then be told a different process holds it. This path was traced only as far as "possible", not end to end.

**Fix:** word the refusal as "another pm-ai run or thread", or name no holder ("is claimed right now"). In the spine, add "refused as `ArtifactBusy`, whose message names no process", so the contract carries the wording.

### F2 — LOW — The re-entry guard compares path text; the lock compares inodes

The spine says two spellings of the directory "hold one lock", and that is true of the `flock`. The guard, though, keys on `parent.resolve() / name`, and on case-insensitive APFS `resolve()` keeps the case it was given (measured above). So a same-thread re-entry through `Foo/` and then `foo/` misses the guard. It then meets the thread's own lock and comes back as `ClaimHeld`/`ArtifactBusy`, the outcome the amendment rules out. Today every path comes from `ScopePaths.resolve`, which always spells a path the same way, so the case is not reachable. It is a limit, not a live bug.

**Fix (either):** key both guards on `(st_dev, st_ino)` of the resolved parent plus the name. Or add one clause to the spine: "the guard compares the canonical spelling, and on a case-insensitive volume a differently cased spelling is not recognised as re-entry".

### F3 — LOW — The asyncio limit is correctly stated; two facts for `4e`

These are not defects in the amendment, only facts `4e` will need:
- `contextvars` is the documented replacement. A *mutable* `set` stored in a `ContextVar` is shared by reference with every task created while the claim is held, because the context copy is shallow. A per-task guard therefore has to bind an immutable `frozenset` and re-set it on entry and exit. This was inferred from the documented copy semantics and not measured.
- Work moved to a thread inside a claim (`asyncio.to_thread`, `run_in_executor`, `run_bounded`) runs on another thread. `holds_claim` answers `False` there, and a claim taken there is contention with its own caller. For the sealed store that means waiting `attempts × interval` and then `StoreBusy`. That waits on itself; it is not a deadlock. The spine's one-sentence limit covers tasks but not this inverse case. Worth one clause, or leave it to `4e`.

### F4 — INFO — "Never reported as `ArtifactBusy`" holds; "never retried" holds only in the code read

The probe wrappers (`connectors/graph/__init__.py`, `gitlab.py`, `graph/auth.py`) catch `Exception` and turn it into a failed-health value. A `ClaimReentered` raised inside a probe would surface as a probe failure rather than a crash. That still satisfies the spine's wording, and the bug would not be hidden as "busy". The check was a grep plus a read of the sealed-store loop, not every call site. Nothing in the amendment depends on more than that.

## Not confirmed / out of scope

- Apple's own `flock(2)` page was not fetched. Darwin behaviour rests on the BSD page plus the local measurement, which is the stronger evidence for this machine.
- The `run_bounded` path in F1 (an abandoned health-check thread holding the sealed store's claim) was shown possible from the code structure. It was not reproduced.
