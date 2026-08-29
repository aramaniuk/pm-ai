# Derivation Services — pm-ai

Companion to `SPEC.md`. What turns Tier-1 truth into Tier-3 derived artifacts, who owns each one, and the rules that bound the machinery. Tiers and rebuildability are in `storage-contract.md`; scopes and paths are in `scope-model.md`.

## The gap this closes

Three derived contents are required by capability and **built by nothing**:

| derived content | lives in | required by |
| --- | --- | --- |
| search index | `event_index.db` | CAP-23 (activity query ≤60s), CAP-24 (procedure retrieval ≤15s) |
| commitment index | `commitment_index.db` | CAP-34 ("an indexed row at `[PENDING]`"), CAP-33 |
| embeddings | `vector_index/` | CAP-27 (semantic query ≤5s), CAP-37 (≤500MB, 50–150ms, no model in path) |

The two databases were one `derived.db` until 2026-08-27. Split on the same day the jobs were designed, because a job declares its whole output and two jobs cannot each own half a file — and because their owners (stories 18 and 15) ship at different times, so a single file would have to exist before either index did. `event_index.db` is named for its principal input; it also covers `rules/` and `meetings/`.

Story 19 *caps* the vector index, story 18 *queries* the search index, story 2 needs semantic search — and no story's description says it creates any of them. The architecture anticipated the layer without assigning it: AD-9 states that "sanitization, dedup, **indexing**, and persistence happen outside the connector, uniformly" without naming what does the indexing, and the pre-written suite already expects a `pm_ai.core.scheduler` (`test_domain_invariants.py:148`) that no story builds.

The same gap explains a second smell. `pm_ai/app/pipelines.py` holds `run_harvest` and `run_transcript_ingestion` as functions somebody must call by hand. Nothing calls them. They are jobs without a job runner.

*"Caches" is struck* from the Tier-3 description: it appeared in `storage-contract.md`, no capability asked for it, and an undefined member of the disposable tier is an invitation to put something non-rebuildable there.

## Five rules

1. **Every job is a row in the queue.** Not a rule this design invents — AD-20 already requires it: "nothing is scheduled in memory only." FR-04's offline buffer is the same queue in `PENDING_RETRY`.
2. **One job, one task, declared inputs and outputs.** A transcript job processes transcripts and nothing else. Inputs and outputs are named artifacts (plus parameters where a file cannot carry them).
3. **A resource shared by several jobs is reached through one accessor.** `commitments_log.md` is written by transcript processing and read by indexing and by the lifecycle job; they use a common interface for create/append/read rather than each parsing Markdown their own way.
4. **The task manager owns the inventory and the scheduling.** Which jobs exist, what each needs, when it runs.
5. **The task manager exposes triggering and publishes state changes.** A caller can request a job; anything interested can observe `queued → running → done | failed`.

## Change detection is one pipeline: OS filesystem events

**Decided 2026-08-27, and it overrides the write-path design this file first carried.** Watchers are triggered by system-wide filesystem notifications. pm-ai does not publish change events from its own write path. One pipeline, no second channel — chosen for general compatibility: a mechanism that already sees every writer needs no cooperation from any of them.

The superseded argument was that pm-ai is the single writer (AD-5), so its own writes are already known events and watching the filesystem for them is indirection. That is true and it is not sufficient. Markdown is hand-editable by design — `storage-contract.md` makes plaintext a product property precisely so the PM can "read, grep, diff, and hand-edit their own record without the system's cooperation" — so write-path events cover only some of the writes and a second mechanism was needed to cover the rest. Two pipelines for one question is the failure mode: they can disagree, each needs its own tests, and a job can be triggered twice or not at all depending on which fired. The OS sees both writers with one mechanism.

**What this costs, stated rather than discovered later.** A filesystem notification is not a write: one saved file arrives as several events, and the watcher cannot tell pm-ai's own write from a hand edit. So the watcher **coalesces** — a quiet interval per path before a job is enqueued — where the write path would have been exact and immediate. Latency and a debounce window are the price of the single pipeline.

### What may be watched, and who owns each watch

**Only Tier-1 and `RETENTION_MANAGED` artifacts may be watched.** Corrected 2026-08-27: the rule first said Tier 1 alone, which the transcript job's own trigger row contradicted three lines below it — `transcripts/` is `RETENTION_MANAGED`, not Tier 1, and it has to be watched or nothing notices a meeting recording landing.

The bound is a **permission, not a selector**: an artifact is watched because some job declares it in `inputs()`, and the tier rule says which artifacts are eligible at all. So `telegram_cache/` is eligible and unwatched, because no job consumes it. What the bound really excludes is Tier 3 — no index can ever be watched, which is what stops commitment indexing from re-triggering itself by writing `commitment_index.db`.

**One watcher per watched artifact; one watcher may cover several.** The map from watcher to artifacts is a partition, never an overlap. That is what makes an event unambiguous: exactly one watcher owns the path that changed, so a change cannot arrive twice by two routes or fall between two watchers that each assumed the other had it. Ownership of the resource is what selects the work.

Note the plural, because it is where the ownership rule meets the derived graph: a watcher enqueues **every** job declaring the changed artifact as an input, not one job. `event_log/` feeds both search indexing and embedding, and `meetings/` feeds both as well. One owner of the path, one or more jobs woken by it.

**A recursive watch needs an explicit exclusion list, and `transcripts/temp/` is its first member.** The capture writer stages a transcript there before linking it to its final name (see `storage-contract.md`), so a watch that covered it would fire on every staging write and hand transcript processing a file that is still growing — the exact failure the staging exists to prevent. Nobody declaring `transcripts/temp/` in `inputs()` is not sufficient protection: a recursive watch sees subdirectories whether a job asked for them or not.

### A job must not write what re-triggers it

Tier-1 outputs *are* watched — they have to be, or the chain breaks: `commitments_log.md` is transcript processing's output and commitment indexing's input, and watching it is exactly how the second follows the first. So the tier bound cannot be what prevents a loop, and nothing enforces this rule in code today (decided 2026-08-27: documented, not tested).

**Be deliberate about it.** A job whose output re-triggers the same job spins forever. Compaction is one predicate away from that shape right now — it reads ageing `event_log/` segments and writes milestone summary segments back into `event_log/` — and the only thing saving it is that its trigger is a schedule rather than an event. Make it event-driven and it feeds itself. The same trap waits for any future job that reads Tier 1 and writes Tier 1 in response to a change: a "regenerate `daily_dashboard.md` whenever `meetings/` changes" job is unremarkable to propose and is a loop if its output is watched by anything upstream of it.

**The watcher is an OS API, so it lives behind a platform port** (AD-26) — `FileWatcherPort` in `pm_ai.ports`, its adapter in `pm_ai.platform`, alongside the keychain, the clock, git and the environment. The task manager consumes the port and never imports the watching library, which is what keeps a macOS-only FSEvents backend from being a fact about the core. It is a new runtime dependency; `pm-ai doctor`'s package probe covers it without change.

### Boot reconciliation closes the one gap the pipeline cannot

A stopped daemon missed every write made while it was down, and no notification is coming for them. **The daemon reconciles the state of all watched files at boot** — mtime or digest against what the last derivation saw — and enqueues a job for each path that moved. Confirmed 2026-08-27.

Not periodic. A periodic sweep would be the second pipeline the ruling excludes; a boot scan is not, because it answers a different question — *what did we miss while not listening* rather than *what is changing now* — and it runs exactly when the answer is unknown.

**Replay from a persisted FSEvents id was considered and rejected.** It is more precise, and it is a backend-specific second code path answering the question the boot scan already answers, with its own tests and its own way to be wrong. The same objection that killed the dual pipeline applies one level down.

**Start the watcher first, then scan.** The reverse loses any write landing between the scan finishing and the watcher attaching — a window that is small, real, and silent. In this order the overlap produces duplicate work instead, which costs nothing: a job rebuilds from its declared inputs, so running it twice yields the same output.

**Every index entry stores the MD5 of the source file it came from.** Not one watermark per index — one checksum per entry, in the row (`event_index.db`, `commitment_index.db`) or beside the embedding record (`vector_index/`), naming the source path and its MD5 as of the derivation that produced it.

Per-entry rather than per-index is what makes the re-index unit small. Group the entries by source path, compare the stored MD5 against the file's current MD5, and **the unit of work is one source file**: its entries are deleted and rebuilt, and every other file's entries are untouched. A per-index watermark would only ever have said *stale* about the whole index, so one edited meeting record would have cost a full rebuild of `rules/`, `event_log/` and `meetings/` together.

**It also catches deletion, which a watermark cannot.** A source file removed while the daemon was down produces no checksum to compare and no filesystem event that anybody heard. Because the index holds the set of source paths it derived from, a path with entries and no file is detectable and its entries are dropped. With a single watermark that state is invisible: the index keeps serving rows about a file that no longer exists.

**MD5 is correct here, and is a change-detection claim only.** Its collision weakness is a problem for authentication, and this is not authentication — nobody is forging a Markdown file to collide with its own earlier version, and the PM editing their own record is not an adversary. Nothing may read these checksums as an integrity or authenticity guarantee; the moment something needs that, it needs a different function and a different store.

**Boot reconciliation therefore hashes every source file.** mtime is cheaper but not trustworthy alone: a restore from backup, a clock change, or an editor that preserves mtime each produce a changed file that mtime calls unchanged, and this scan exists precisely for the writes nothing observed. If hashing the Markdown tree at boot becomes slow it wants measuring before an mtime pre-filter is added on top — the pre-filter is an optimisation with a correctness cost, so it needs a number, not an intuition.

**The checksums live inside the index, never in `operational.db`.** A Tier-2 store survives the Tier-3 drop that `pm-ai reindex` performs, so a rebuilt index would inherit checksums for source files it never read and skip them — an index that believes it is current and holds nothing. Held inside the index, the drop takes the checksums with it and the rebuilt index correctly believes it has seen nothing. That their loss costs only a re-derivation is also what makes them Tier-3 state rather than Tier 2.

## The job contract

```
inputs()  -> frozenset[ArtifactRef]     what makes this job stale — NOT what it opens
outputs() -> frozenset[ArtifactRef]     what it creates or updates
run(ctx)  -> JobResult                  one task, no orchestration
```

**`inputs()` declares staleness, not file access.** A job that opens a file for reference has not thereby acquired a trigger. The commitment lifecycle job is the case that forced the distinction: it reads `commitment_index.db` to list the open commitments, and declaring that as an input created an edge meaning *re-run me when the index changes* — which can never fire (Tier 3 is unwatched) and is not what the job needs anyway. What makes it stale is elapsed time. Blur the two meanings and the derived graph stops describing when things run, which is the one thing it exists to describe.

### Three ways a job is triggered

1. **A change to a watched artifact** — the filesystem pipeline above. The edges this produces are exactly `outputs() ∩ inputs()`.
2. **A schedule** — for work no file change can announce. Harvest and compaction are periodic by policy; the commitment lifecycle job is scheduled because two of its three transitions are *the target date passed* and *48 hours before the milestone*, and nothing is written when a deadline arrives.
3. **A direct request from pm-ai's business logic** into the task manager (ruled 2026-08-27). Rule 5's triggering interface is this: a surface, a skill, or a CLI command asks for a named job. `pm-ai reindex` is the obvious one; so is re-running an ingestion the PM knows failed.

Trigger kind and job identity are independent — the same job runs the same way whichever woke it. That is what makes kind 3 safe: a requested job that finds its inputs unchanged does no work, because staleness is decided by comparing the stored per-entry MD5 against the file, not by trusting whoever asked.

**The dependency graph is derived, never configured.** One job's `outputs()` intersecting another's `inputs()` *is* the edge. Transcript processing outputs `commitments_log.md`; commitment indexing inputs it; the chain follows without anybody writing it down twice. That is the same principle the scope model already uses — tier and exclusion sets derived from node declarations rather than maintained beside them — and it fails the same way if abandoned: two structures that can disagree.

A job never enqueues its successor. It declares what it produced; the task manager decides what that makes stale.

## The jobs

| job | inputs | outputs | trigger |
| --- | --- | --- | --- |
| **Harvest** | connector instance + cursor | normalized events → `event_log/` | schedule (240m ±15, per AD-9) |
| **Transcript processing** | a capture in `transcripts/` | meeting summary → `meetings/`, decisions → `event_log/`, commitments → `commitments_log.md`, staged proposals | filesystem event in `transcripts/` |
| **Commitment indexing** | `commitments_log.md` | `commitment_index.db` | filesystem event on that file |
| **Search indexing** | `rules/`, `event_log/`, `meetings/` | `event_index.db` | filesystem event under any of them |
| **Embedding** | `event_log/`, `meetings/`, `coaching_1on1_history.md` | `vector_index/` | filesystem event under any of them |
| **Commitment lifecycle** | harvested telemetry + the clock (**not** the index, which it only reads) | commitment state transitions in `commitments_log.md` | schedule |
| **Compaction** | ageing sealed `event_log/` segments | milestone summary segments, **a `COMPACTION` record in the open segment**, index bounded | schedule (7d / 500MB) |

The chain the design must support, end to end: a meeting ends → the Graph adapter fetches and validates the transcript → the capture lands in `transcripts/` → transcript processing runs → `commitments_log.md` gains entries → commitment indexing runs → the index is current. Each step is one job with one responsibility, and no step names the next.

## Shared resource accessors

Rule 3, made concrete. Each is a class with a narrow interface over one Tier-1 resource, performing its I/O through `StorageService` (the single writer) rather than touching files:

| accessor | resource | used by |
| --- | --- | --- |
| `CommitmentLog` | `commitments_log.md` | transcript processing (append), indexing (read), the lifecycle job (append transitions) |
| `EventLog` | `event_log/` segments | harvest, transcript processing, every audit write |
| `MeetingRecords` | `meetings/` | transcript processing (write), search indexing (read) |

Without these, three jobs parse the same Markdown three ways and the fourth reader disagrees with all of them.

## Ownership

**Infrastructure** — the task manager, the job contract, change events, and the accessors. Proposed owner: **a new story after 10**, which supplies the durable queue it schedules onto. It cannot sit inside story 10 (that story is the queue and offline replay, CAP-4) and it must not be split across 2, 15 and 18, or three stories each build a third of a scheduler.

**Each derived artifact goes to the story whose capability first cannot work without it** — so the index arrives with the feature that needs it, rather than as infrastructure nobody scheduled:

| artifact | owner | why |
| --- | --- | --- |
| embeddings (`vector_index/`) | **story 10a** (moved from 2, 2026-08-29) | CAP-27's semantic query is the first capability that cannot work without them. Placed on story 2 until 2026-08-29, then moved: the job needs the task manager to trigger it, and story 2 shipping before 10a would have defined a job nothing could run. Story 2 keeps CAP-27's ledger clauses. |
| commitment index (`commitment_index.db`) | **story 15** | CAP-34 says "an indexed row"; the story that persists commitments writes it. |
| search index (`event_index.db`) | **story 18** | CAP-23 and CAP-24 both need it and story 18 owns both. |
| the rebuild guarantee | **story 1h, moved** | Nothing to rebuild until the three above exist. |

**Story 1h moves to after story 19.** By then all three indexes exist and the vector index has its 500MB bound, so the snapshot has real content and the rebuild has something to reproduce. Placed earlier it would prove a property about an empty directory — which is what stopped it on 2026-08-27.

The cost of that placement is named in 1h's own Problem statement: "the property most likely to quietly stop being true, since every index added later has to be reconstructible and nothing checks." Stories 2, 15 and 18 each add derived state before 1h can check it, so each carries a dispatch note: whatever you add must be reconstructible from Markdown alone.

## Answered: no job output is a backup target, and compaction is not a derivation job

Resolved 2026-08-27. On the backup axis the answer is no, and the guard exists: `BACKUP_TARGETS` and `REBUILD_TARGETS` are asserted disjoint and `assert_reindex_safe` refuses any artifact whose tier is not `DERIVED` — the pre-written suite already proves it rejects `event_log/`. The wiring requirement that follows: a rebuild path must derive its target set from jobs' `outputs()` and pass it through that function, at which point a Tier-1 output is refused without anyone deciding to refuse it.

Compaction's output being Tier 1 *and* a backup target is correct. It only means "derivation job" was the wrong label — this is the job inventory, and jobs are classified by the tier of what they produce.

Walking the question surfaced the two real gaps, which are not about backup at all: compaction had no precondition for destroying Tier 1, and its record had no location. Both are now rules in `storage-contract.md` under *What authorises the deletion, and where it is recorded*. One residue is parked with story 19: monthly segments and a 7-day threshold do not line up.

## Answered: the coalescing window is not on the correctness path

Q2 asked what the debounce interval should be. Resolved 2026-08-27 by unfolding the three write shapes in `storage-contract.md` instead of picking a number, because a debounce is a *guess about when writing stopped* and the one case where guessing wrong is unrecoverable — a duplicated meeting summary and duplicated commitments in an append-only ledger — is fixed by making the capture's appearance atomic. There is then no interval for a job to fall into.

What remains of the question is efficiency, and it has two answers better than a timer:

- **Whole-record parsing** for appends. A fragment without its terminating newline is not a record, so an early read is harmless rather than something to wait out.
- **Queue-level deduplication** for bursts. A harvest appends per event, which naively means one indexing run per append. If a job for an artifact is pending and not yet started, do not enqueue another — the idempotency key is `(job, artifact)`, which AD-20's durable queue already provides. Exact, and nothing to tune.

A coalescing window survives only as a guard against external editors that truncate and rewrite in place, where the worst case is one wasted indexer pass. Ship a safe default; measure only if it ever shows up as a cost.

## Answered: one hop, no sort

Resolved 2026-08-27 by deriving the live graph rather than estimating it. Every job either produces Tier 1 and is woken by a schedule or an external capture, or produces Tier 3 and is a **leaf** — because Tier 3 is not watched, nothing follows an index being written. So no chain exceeds one job-to-job edge and **the task manager enqueues direct consumers, with no topological sort.**

The bound has a cause rather than being a count of today's seven jobs: it holds as long as no event-triggered job writes an artifact that something upstream of it watches. Nothing enforces that; the section above is the whole of the protection, by decision.

## Open questions

All three questions this file opened on 2026-08-27 were answered the same day; the sections above hold the resolutions. What remains open is one residue, owned by story 19:

- **Compaction's threshold and the event log's segment granularity do not line up.** Segments are monthly and the threshold is "older than 7 days", but the smallest deletable unit is a whole sealed month and the current month is never compactable — so the youngest deletable content is 1 to 31 days old depending on when compaction runs. Either the threshold means something other than what it says, or segments need a finer granularity, or compaction summarises *within* a sealed segment rather than replacing whole ones.
