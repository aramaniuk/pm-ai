# Adversarial review: AD-5 amendment "Re-entry is per thread" (2026-10-12)

Reviewed: `ARCHITECTURE-SPINE.md` AD-5 (uncommitted diff on `docs/claim-reentry-per-thread`), the matching `.memlog.md` decision line, and the code it describes: `pm_ai/platform/claims.py`, `pm_ai/storage/service.py` (`exclusive`, `holds_claim`), `pm_ai/app/wiring.py` (`SealedRefreshTokenStore`), `pm_ai/core/connector_enrolment.py` (`replace_credential`, `record_user_id`), `pm_ai/connectors/registry.py` (`run_bounded`, `check_health`), `pm_ai/connectors/graph/auth.py`, `pm_ai/storage/crypto.py` (`LazyKeyCrypto`), `pm_ai/domain/claims.py`, `pm_ai/ports/__init__.py`. Read-only.

Method: for each hole, two units one level down that each obey every AD to the letter (the amended AD-5 especially) and still build incompatibly. Each hole ends with proposed wording.

## Verdict

**Accept the amendment, with tightenings before `4e`.** It states what the code does: two thread-local guards (`_HELD_HERE` in the platform lock, `_CLAIMED_HERE` in storage) raise `ClaimReentered` before anything is created, and another thread's hold comes back as `ArtifactBusy`. For the case it was written for, a CLI command nesting a claim on one thread, it is closed. But "per thread" is only the right identity while a claim's body starts and finishes on one thread and never waits on another. The spine says neither. Under AD-19 (one asyncio loop, a worker pool) and AD-7 (a long-lived daemon), the two holes below are reachable by units that follow the letter. The asyncio limit is recorded and deferred to `4e`. That does not close the hole: it leaves `4e` free to build the incompatible pair.

## Findings, most severe first

### F1 (high): a claim held across a suspension or a thread hop

**Pair.** Unit A is the daemon's harvest job (`4e`/`13`, AD-19/AD-45). It runs on the one asyncio loop and moves blocking storage calls off the loop with `asyncio.to_thread`, which AD-19 permits because it bounds only the heavy-model pool. It enters `MeetingRecords.put`'s claim in one `to_thread` call, or holds an `ExitStack` across an `await` between pages. Unit B is `StorageService.exclusive` exactly as amended. Each obeys its rules. Together they fail three ways:

1. **Same-thread tasks.** Two harvest tasks on the loop thread both write one project's `meetings/`. The second gets `ClaimReentered`, a programming error that is never retried. The same contention between two processes would be `ArtifactBusy`, which is retryable. One situation gets two verdicts depending on where the scheduler happened to run it. The spine records this limit ("indistinguishable"), but no rule stops a unit from building it.
2. **Pool-thread poisoning.** The claim is entered on executor thread X, and the coroutine is cancelled before the exit runs, or the exit runs in a later `to_thread` call on thread Y. Thread X's thread-local set keeps the path, and the descriptor keeps the `flock`. After that, every claim on that path from thread X is `ClaimReentered` (blamed on pm-ai as a bug), and every claim from any other thread is `ArtifactBusy` (blamed on "another pm-ai process"). This lasts for the life of the daemon. The code captures the set at entry so that the discard hits the right set, but only if the exit runs at all.
3. **Write on another thread.** The claim is taken on thread X and the write is dispatched on thread Y. `record_user_id(claimed=True)` refuses (`holds_claim` is false on Y). `SealedRefreshTokenStore.write` refuses (`_holder` is a different ident). `replace_credential(claimed=True)` writes unchecked (see F3).

**Proposed wording** (replace the sentences from "The guard is thread-local…" to "…revisits it with the daemon."):

> **A claim's body is synchronous and runs start to finish on the thread that entered it.** No `await`, no generator `yield` to outside code, and no hand-off of the claim, or of a write made under it, to another thread, task or executor. A caller on the asyncio loop takes a claim only inside one blocking call it offloads whole: claim, read, write and release in a single function run on one thread. Until `4e` supplies a writer thread that owns every claim, an async claimant is refused at review. The guard is thread-local, so it cannot detect a breach between tasks on one thread; this rule is what prevents one.

### F2 (high): waiting on another thread from inside a claim, and claims held by abandonable threads

**Pair (delegation).** Unit A holds `config.json`'s claim on the main thread and calls `run_bounded(call)`. AD-9/CAP-35 say bounded calls go through `run_bounded`, and nothing forbids doing that inside a claim. Unit B, `call`, refreshes a token through `SealedRefreshTokenStore`, which claims `config.json` on the bounded thread. Under the amendment, B's attempt is "another thread … ordinary contention". It isn't: the holder is waiting on the claimant. B waits out the sealed store's budget (`attempts × interval`, about 2 s), raises `StoreBusy` worded as "another pm-ai process", and A's call fails every time. This is a self-deadlock presented as retryable contention, the failure the amendment set out to remove, moved one thread over. It is not live today: enrolment probes before its claim, and Graph enrolment uses `InMemoryRefreshTokenStore`. Nothing in the spine prevents it.

**Pair (abandonment; adjacent to live code).** `ConnectorRegistry.check_health` runs each probe on a daemon thread and abandons it at the deadline (`registry.py`, by design). The Graph probe calls `access_token`, which takes `config.json`'s claim through `SealedRefreshTokenStore` and reads the sealed store inside it. On the first decrypt, `LazyKeyCrypto` fetches the key from the keychain, which `crypto.py` says can be "a user-visible prompt". So a probe can be abandoned at 10 s while it holds `config.json`'s claim behind an unanswered keychain prompt. In a CLI process, exit releases the lock. In the daemon (AD-7, long-lived), every later claim on the credential store is refused until the prompt is answered, and each refusal blames "another pm-ai process".

**Proposed wording** (add after "…refused as `ArtifactBusy`."):

> **A thread holding a claim never waits on another thread or task**: no `join`, `run_bounded`, `Future.result` or queue read inside a claim's body. Contention between threads is "ordinary" only because the holder makes progress without the claimant. **No claim is taken by work that can be abandoned**: a call run under `run_bounded`, or any call with an abandoning deadline, either takes no claim or is not abandoned while holding one. Blocking for a person, such as a keychain prompt, happens before the claim is taken and never inside it: an encrypted artifact's key is made ready (`LazyKeyCrypto.ready`) before `exclusive` is entered.

### F3 (medium-high): three answers to "does this thread hold the claim"

**Pair.** `record_user_id(claimed=True)` checks `StoragePort.holds_claim` and raises `ClaimNotHeld`. `replace_credential(claimed=True)`, which writes the same `config.json` under the same claim, trusts the flag and checks nothing. `SealedRefreshTokenStore.write` answers the question a third way, from its own per-object `_holder` thread ident. So a second store object for the same instance on a thread that does hold the file's claim is refused, while `replace_credential` called with `claimed=True` from a thread holding nothing writes without serialization. A new writer modelled on `replace_credential` follows the letter, because the spine says only that `holds_claim` "answers whether this thread holds one", not that a writer must ask. `ClaimNotHeld` is also declared in `core` (`connector_enrolment.py`), while its sibling `ClaimReentered` is in `domain`. Under AD-49 both concern the claim's contract, so they belong together.

**Proposed wording** (replace "`StoragePort.holds_claim` answers whether this thread holds one."):

> **`StoragePort.holds_claim` is the one answer to "is this write inside its claim".** A writer that accepts `claimed=True` checks it and raises `ClaimNotHeld` (declared in `domain` beside `ClaimReentered`) when the thread does not hold the claim. A flag is never trusted, and no object keeps its own record of who holds a claim as a second source.

Owed in code: `replace_credential` gains the check, `SealedRefreshTokenStore.write` asks `holds_claim` instead of `_holder`, and `ClaimNotHeld` moves to `pm_ai/domain/claims.py`.

### F4 (medium): one claim guarding another artifact, undeclared

**Pair.** `connectors/<instance>.json` rows are written under `config.json`'s claim, both in enrolment step 7 and in `record_user_id`. The spine lists only `config.json`, `meetings/` and the project registry as claimants, and says nothing about which claim guards a connector row. A later row writer (a `categories` editor, `connector disable`, `4o`) that follows AD-5's letter either claims `connectors/<instance>.json` itself or takes no claim, since its read-modify-write is of that file. Both writers believe they are serialized, but under different locks, so a recorded `user_id` and a changed `enabled` overwrite each other. That is two owners of one entity.

**Proposed wording** (after "`config.json` (the sealed token store), `meetings/` and the project registry all claim through it."):

> **Which claim guards an artifact is declared once, in the scope model beside `CLAIM_SIDECAR_SUFFIX`**, and every read-modify-write of that artifact takes that claim and no other. A connector's row in `connectors/` is guarded by `config.json`'s claim, because enrolment writes both in one step. An artifact with no declared claim is never read-modified-written.

### F5 (medium): same-process contention worded as another process, and stale "per process" text

**Pair.** Under the amendment, another thread's hold is `ArtifactBusy`, but both refusal texts say "another pm-ai process": `ClaimHeld` in `platform/claims.py`, and `ArtifactBusy` in `StorageService.exclusive`. That is a claim the code did not compute, so the message is false in exactly the case the amendment newly defines. Two readings of re-entry also remain. The `2026-10-11` memlog event's owed line ("a second claim on a path **the process** already holds raises a programming error"), the `ClaimReentered` docstring ("never taken twice by one process") and `record_user_id`'s docstring all say per process. The spine now says per thread. A unit built from the 10-11 line treats another thread's hold as a bug, while one built from the spine treats it as busy. That is the busy-versus-bug split the review was asked to look for.

**Proposed wording:**

> A refusal for contention names what pm-ai knows: "another pm-ai process, or another thread of this one". Or the storage service keeps a process-wide table of held paths and their owning thread, so it can say which.

The memlog's 10-11 owed item is marked superseded by the 10-12 decision on the next derive. The two docstrings are corrected in the next code slice.

### F6 (low): re-entry by interruption rather than nesting

**Pair.** `4e`'s daemon installs a SIGTERM handler, or an `atexit` hook or finalizer, that persists state through `storage.exclusive`. A Python signal handler runs on the main thread between bytecodes. If the main thread is inside the same claim at that moment, the handler gets `ClaimReentered` during shutdown. No caller nested anything; the interpreter did. A `__del__` or `weakref` callback can run on whichever thread triggers garbage collection, with the same result.

**Proposed wording:**

> No claim is taken from a signal handler, `atexit` hook, finalizer or weakref callback; these set a flag the owning loop acts on.

### F7 (low): where a `ClaimReentered` surfaces

The report-never-raise wrappers absorb it. The Graph `check_health` catch-all turns it into a `FAILING` probe ("a bug in pm-ai"). The harvest catch-alls in `graph/__init__.py` and `app/pipelines.py` turn it into a `FAILED` harvest with `retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE`, which is `False` today. This follows the letter: never retried, never busy. But the retry guarantee rests on a constant two catch-alls away, and the rule never says where a programming error becomes visible.

**Proposed wording:**

> Inside a report-never-raise boundary, `ClaimReentered` becomes a non-retryable failure or a `FAILING` probe that names it as a pm-ai bug, and never retryable, whatever the unclassified-fault default becomes.

### F8 (low): nested claims on different artifacts have no order

Refusing instead of waiting prevents a deadlock, but two threads nesting `meetings/` and `config.json` in opposite orders meet the sealed store's wait. One stalls about 2 s and fails with `StoreBusy` on every overlap. Nothing nests claims today.

**Proposed wording:**

> Claims on different artifacts are nested only in a declared order (project registry, then `meetings/`, then `config.json`). A nesting outside that order is refused at review.

## What holds

- The guard is checked before any directory is created, in both layers, as the spine says.
- `ClaimReentered` is not a subclass of `ClaimHeld`, so no translation turns it into `ArtifactBusy`.
- The storage-layer guard is module-global, so it sees claims taken through any `StorageService` instance in the process, as `holds_claim`'s docstring says.
- The sealed store's per-object lock and retry stay within "the only wait".
- Enrolment probes before taking its claim, so the F2 delegation pair is not live today.
