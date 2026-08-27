# Derivation Services — pm-ai

Companion to `SPEC.md`. What turns Tier-1 truth into Tier-3 derived artifacts, who owns each one, and the rules that bound the machinery. Tiers and rebuildability are in `storage-contract.md`; scopes and paths are in `scope-model.md`.

## The gap this closes

Four derived contents are required by capability and **built by nothing**:

| derived content | lives in | required by |
| --- | --- | --- |
| search index | `derived.db` | CAP-23 (activity query ≤60s), CAP-24 (procedure retrieval ≤15s) |
| commitment index | `derived.db` | CAP-34 ("an indexed row at `[PENDING]`"), CAP-33 |
| embeddings | `vector_index/` | CAP-27 (semantic query ≤5s), CAP-37 (≤500MB, 50–150ms, no model in path) |

Story 19 *caps* the vector index, story 18 *queries* the search index, story 2 needs semantic search — and no story's description says it creates any of them. The architecture anticipated the layer without assigning it: AD-9 states that "sanitization, dedup, **indexing**, and persistence happen outside the connector, uniformly" without naming what does the indexing, and the pre-written suite already expects a `pm_ai.core.scheduler` (`test_domain_invariants.py:148`) that no story builds.

The same gap explains a second smell. `pm_ai/app/pipelines.py` holds `run_harvest` and `run_transcript_ingestion` as functions somebody must call by hand. Nothing calls them. They are jobs without a job runner.

*"Caches" is struck* from the Tier-3 description: it appeared in `storage-contract.md`, no capability asked for it, and an undefined member of the disposable tier is an invitation to put something non-rebuildable there.

## Five rules

1. **Every job is a row in the queue.** Not a rule this design invents — AD-20 already requires it: "nothing is scheduled in memory only." FR-04's offline buffer is the same queue in `PENDING_RETRY`.
2. **One job, one task, declared inputs and outputs.** A transcript job processes transcripts and nothing else. Inputs and outputs are named artifacts (plus parameters where a file cannot carry them).
3. **A resource shared by several jobs is reached through one accessor.** `commitments_log.md` is written by transcript processing and read by indexing and by the sweeper; they use a common interface for create/append/read rather than each parsing Markdown their own way.
4. **The task manager owns the inventory and the scheduling.** Which jobs exist, what each needs, when it runs.
5. **The task manager exposes triggering and publishes state changes.** A caller can request a job; anything interested can observe `queued → running → done | failed`.

## Change detection needs two mechanisms, not one

The obvious design watches the Tier-1 directories. That is half right, and the half it gets wrong matters.

**pm-ai is the single writer (AD-5).** Every write it performs is already a known event, so watching the filesystem for pm-ai's own writes is indirection with a latency penalty. The write path should publish the change directly: exact, immediate, no polling.

**But Markdown is hand-editable by design.** `storage-contract.md` makes plaintext a product property precisely so the PM can "read, grep, diff, and hand-edit their own record without the system's cooperation." A PM who edits `commitments_log.md` in an editor produces a change pm-ai did not write and cannot know about. Derived state then goes stale with nothing having happened from pm-ai's point of view.

So:

- **Write-path events** for everything pm-ai does. Emitted by `StorageService`, which is the only writer, so the event cannot be forgotten by a caller.
- **A reconciliation sweep** for everything it did not — a periodic digest or mtime comparison against what the last derivation saw, which is the only thing that catches an external edit.

The second exists *because* of a deliberate product decision, and that is worth stating: hand-editable truth is a feature that creates a staleness problem, and reconciliation is its cost.

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
| **Transcript processing** | a capture in `transcripts/` | meeting summary → `meetings/`, decisions → `event_log/`, commitments → `commitments_log.md`, staged proposals | capture written |
| **Commitment indexing** | `commitments_log.md` | commitment index in `derived.db` | that file changed |
| **Search indexing** | `rules/`, `event_log/`, `meetings/` | search index in `derived.db` | any of them changed |
| **Embedding** | `event_log/`, `meetings/`, `coaching_1on1_history.md` | `vector_index/` | any of them changed |
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
| commitment index | **story 15** | CAP-34 says "an indexed row"; the story that persists commitments writes it. |
| search index | **story 18** | CAP-23 and CAP-24 both need it and story 18 owns both. |
| the rebuild guarantee | **story 1h, moved** | Nothing to rebuild until the three above exist. |

**Story 1h moves to after story 19.** By then all three indexes exist and the vector index has its 500MB bound, so the snapshot has real content and the rebuild has something to reproduce. Placed earlier it would prove a property about an empty directory — which is what stopped it on 2026-08-27.

The cost of that placement is named in 1h's own Problem statement: "the property most likely to quietly stop being true, since every index added later has to be reconstructible and nothing checks." Stories 2, 15 and 18 each add derived state before 1h can check it, so each carries a dispatch note: whatever you add must be reconstructible from Markdown alone.

## Open questions

- **Does the task manager need a DAG walk, or is one hop enough?** Transcript processing → commitment indexing is one hop. Compaction rewriting `event_log/` segments invalidates both the search index and the embeddings, which invalidate nothing further. If no chain exceeds two hops, "enqueue direct consumers of what changed" is sufficient and a topological sort is unbuilt machinery.
- **What is the reconciliation interval?** Too long leaves a hand-edited commitment unindexed; too short is a digest sweep over the whole Markdown tree on a battery. The answer probably differs per artifact and wants measuring rather than guessing.
- **Is a derivation job's output ever a backup target?** No, by the tier model — but a compaction job's *milestone summary* is Tier 1, which means one job in this table writes truth rather than derived state. That is correct (compaction is a recorded reduction of the record, per AD-5) and it is the one row where "derivation job" is the wrong mental model.
