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
3. **A resource shared by several jobs is reached through one accessor.** `commitments_log.md` is written by transcript processing and read by indexing and by the sweeper; they use a common interface for create/append/read rather than each parsing Markdown their own way.
4. **The task manager owns the inventory and the scheduling.** Which jobs exist, what each needs, when it runs.
5. **The task manager exposes triggering and publishes state changes.** A caller can request a job; anything interested can observe `queued → running → done | failed`.

## Change detection is one pipeline: OS filesystem events

**Decided 2026-08-27, and it overrides the write-path design this file first carried.** Watchers are triggered by system-wide filesystem notifications. pm-ai does not publish change events from its own write path. One pipeline, no second channel — chosen for general compatibility: a mechanism that already sees every writer needs no cooperation from any of them.

The superseded argument was that pm-ai is the single writer (AD-5), so its own writes are already known events and watching the filesystem for them is indirection. That is true and it is not sufficient. Markdown is hand-editable by design — `storage-contract.md` makes plaintext a product property precisely so the PM can "read, grep, diff, and hand-edit their own record without the system's cooperation" — so write-path events cover only some of the writes and a second mechanism was needed to cover the rest. Two pipelines for one question is the failure mode: they can disagree, each needs its own tests, and a job can be triggered twice or not at all depending on which fired. The OS sees both writers with one mechanism.

**What this costs, stated rather than discovered later.** A filesystem notification is not a write: one saved file arrives as several events, and the watcher cannot tell pm-ai's own write from a hand edit. So the watcher **coalesces** — a quiet interval per path before a job is enqueued — where the write path would have been exact and immediate. Latency and a debounce window are the price of the single pipeline.

**Only Tier-1 paths are watched.** A job's own output must never be a watched path, or commitment indexing writing `commitment_index.db` triggers commitment indexing. The invalidation graph already handles the real case: a change to an input invalidates its consumers, and derived outputs have no consumers to invalidate.

**The watcher is an OS API, so it lives behind a platform port** (AD-26) — `FileWatcherPort` in `pm_ai.ports`, its adapter in `pm_ai.platform`, alongside the keychain, the clock, git and the environment. The task manager consumes the port and never imports the watching library, which is what keeps a macOS-only FSEvents backend from being a fact about the core. It is a new runtime dependency; `pm-ai doctor`'s package probe covers it without change.

**One gap the pipeline cannot close by itself: writes that happened while nothing was watching.** A daemon that was stopped missed them, and no notification is coming. This is not a second pipeline — it is the same stream resumed, or a catch-up. FSEvents can replay from a persisted event id, which is the preferred form because the events are the same events. Where the backend has no history to replay, the fallback is a **reconciliation scan at startup only** — mtime or digest against what the last derivation saw. Not periodic: a periodic sweep is the second pipeline the ruling excludes.

## The job contract

```
inputs()  -> frozenset[ArtifactRef]     what this job reads
outputs() -> frozenset[ArtifactRef]     what it creates or updates
run(ctx)  -> JobResult                  one task, no orchestration
```

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
| **Commitment sweep** | commitment index + harvested telemetry | commitment state transitions | schedule |
| **Compaction** | ageing `event_log/` segments | milestone summary segments; index bounded | schedule (7d / 500MB) |

The chain the design must support, end to end: a meeting ends → the Graph adapter fetches and validates the transcript → the capture lands in `transcripts/` → transcript processing runs → `commitments_log.md` gains entries → commitment indexing runs → the index is current. Each step is one job with one responsibility, and no step names the next.

## Shared resource accessors

Rule 3, made concrete. Each is a class with a narrow interface over one Tier-1 resource, performing its I/O through `StorageService` (the single writer) rather than touching files:

| accessor | resource | used by |
| --- | --- | --- |
| `CommitmentLog` | `commitments_log.md` | transcript processing (append), indexing (read), sweeper (append transitions) |
| `EventLog` | `event_log/` segments | harvest, transcript processing, every audit write |
| `MeetingRecords` | `meetings/` | transcript processing (write), search indexing (read) |

Without these, three jobs parse the same Markdown three ways and the fourth reader disagrees with all of them.

## Ownership

**Infrastructure** — the task manager, the job contract, change events, and the accessors. Proposed owner: **a new story after 10**, which supplies the durable queue it schedules onto. It cannot sit inside story 10 (that story is the queue and offline replay, CAP-4) and it must not be split across 2, 15 and 18, or three stories each build a third of a scheduler.

**Each derived artifact goes to the story whose capability first cannot work without it** — so the index arrives with the feature that needs it, rather than as infrastructure nobody scheduled:

| artifact | owner | why |
| --- | --- | --- |
| embeddings (`vector_index/`) | **story 2** | CAP-27's semantic query is the first capability that cannot work without them, and story 2 delivers CAP-27. Its description covers only the ledger today and needs widening. |
| commitment index (`commitment_index.db`) | **story 15** | CAP-34 says "an indexed row"; the story that persists commitments writes it. |
| search index (`event_index.db`) | **story 18** | CAP-23 and CAP-24 both need it and story 18 owns both. |
| the rebuild guarantee | **story 1h, moved** | Nothing to rebuild until the three above exist. |

**Story 1h moves to after story 19.** By then all three indexes exist and the vector index has its 500MB bound, so the snapshot has real content and the rebuild has something to reproduce. Placed earlier it would prove a property about an empty directory — which is what stopped it on 2026-08-27.

The cost of that placement is named in 1h's own Problem statement: "the property most likely to quietly stop being true, since every index added later has to be reconstructible and nothing checks." Stories 2, 15 and 18 each add derived state before 1h can check it, so each carries a dispatch note: whatever you add must be reconstructible from Markdown alone.

## Open questions

- **Does the task manager need a DAG walk, or is one hop enough?** Transcript processing → commitment indexing is one hop. Compaction rewriting `event_log/` segments invalidates both the search index and the embeddings, which invalidate nothing further. If no chain exceeds two hops, "enqueue direct consumers of what changed" is sufficient and a topological sort is unbuilt machinery.
- **What is the coalescing window per watched path?** Replaces the reconciliation-interval question, which the single-pipeline ruling answered. Too short re-runs a job on each of the several events one save produces; too long leaves a hand-edited commitment unindexed. Wants measuring against real editor save behaviour rather than guessing, and probably differs between a transcript landing and a Markdown edit.
- **Is a derivation job's output ever a backup target?** No, by the tier model — but a compaction job's *milestone summary* is Tier 1, which means one job in this table writes truth rather than derived state. That is correct (compaction is a recorded reduction of the record, per AD-5) and it is the one row where "derivation job" is the wrong mental model.
