# Divergence Review R2A — Adversarial Pair Construction

**Target** `ARCHITECTURE-SPINE.md` (status: revision-in-progress, updated 2026-08-19)
**Also read** `tests/architecture/{README.md,test_domain_invariants.py,test_static_rules.py,test_layering.py}`, `.importlinter`, `SOLUTION-DESIGN.md`
**Reviewer** independent, no prior project context
**Date** 2026-08-19

---

## Verdict

The spine is unusually strong on *mechanism* — layering, egress classes, single writer, CAS — and it has already absorbed one adversarial pass (AD-31..AD-37 are visibly the scar tissue). The remaining holes are almost all in the same place the previous pass identified and did not finish closing: **words shared between components that were never pinned to a type, and lifecycle events nobody owns.** I constructed **13 primary pairs** and **6 secondary pairs** of compliant-but-incompatible units. Three of the primary pairs are not merely under-specified, they are **direct contradictions between two ADs in the current document** (P3, P4, P7), where a builder who satisfies one AD necessarily violates another. Two more (P1, P2) route straight into the failure AD-36 and AD-20 were written to prevent, with every existing test still green.

The single most dangerous finding is **P2**: `AD-20` grants at-least-once delivery plus an idempotency key and treats the matter as closed, but none of GitLab, Jira, Teams, or Outlook honours a client-supplied idempotency key. The key therefore only dedups *locally*, against a Tier-2 ledger whose write is not atomic with the external effect. Nothing in the spine says whether that ledger row is written before or after the call. Two compliant implementations produce, respectively, duplicated irreversible FR-26 nudges and silently dropped mutations.

---

## Method

For each candidate I built two concrete units one level down from the spine — two connectors, two skills, two stories, two developers — each of which I could trace to every AD it touches and find compliant. A pair only counts if **both** units pass the written rules *and* the existing `tests/architecture` suite. Generic "you should specify X" observations were discarded; every pair below names the production failure and the AD text that closes it.

---

# Part 1 — Primary divergence pairs

---

## P1 — `target_ref` is a word; `source_ref` is a type. The join between them is where AD-36 dies.

**Unit A — the GitLab comment skill (`pm_ai/skills/post_comment.py`)**
Implements class-M mutation per AD-1. Takes `target_ref="gitlab:alpha:issue:108"` — the developer reasonably reuses the AD-34 reference grammar, since it is the only reference grammar in the document. Derives the idempotency key per AD-20 (`sha256(job_type + target_ref + canonical_payload)`). Records `(target_ref, external_id="887")` in the Tier-2 executed-key ledger per AD-36. Acquires the AD-37 per-target lock on `target_ref`.
*Obeys:* AD-1 (class M in `skills/`), AD-18, AD-20, AD-36, AD-37, AD-5.

**Unit B — the work-item update skill (`pm_ai/skills/update_work_item.py`)**
Same author-adjacent developer, one sprint later. Takes `target_ref="https://gitlab.example.com/acme/backend/-/issues/108"` — the API URL, because that is what the MCP tool and the GitLab client actually consume, and because **AD-34 binds `source_ref` on a `NormalizedEvent`, not `target_ref` on a `Job`**. The existing test suite agrees with B: `test_ad20_mutating_jobs_require_a_key` and `test_ad36_every_class_m_mutation_is_recorded_for_attribution` both use `"gitlab:WI-102"` — a **two-segment** ref that would be rejected by `test_ad34_source_refs_follow_the_fixed_grammar`. The suite itself demonstrates that `target_ref` is not the AD-34 type.
*Obeys:* AD-1, AD-18, AD-20, AD-36, AD-37, AD-5. Nothing in the document is violated.

**The incompatibility**
Three separate invariants are keyed on `target_ref` and all three are string equality:

1. **AD-37's per-target serialization lock.** A's lock key and B's lock key for *the same GitLab issue* are different strings. The lock does not serialize them. AD-37's stated guarantee — "two approved changes to one work item cannot interleave" — is silently void whenever two skills touch one entity, which is the only case the lock exists for.
2. **AD-20's idempotency key.** The key is derived from `target_ref`. Migrating one skill's ref format (a refactor, a self-hosted-to-SaaS move, a trailing slash) rotates every key for already-enqueued jobs. Every `PENDING_RETRY` row replays as a fresh mutation.
3. **AD-36's attribution matching.** Normalization must "mark any harvested event matching one of those as `pm_ai`". The harvested event's ref is `gitlab:alpha:note:887` (AD-34 grammar, enforced). B's ledger row says `https://...issues/108` + `887`. There is no defined matching function between the two, and no defined shape for `external_id` at all.

**The production failure**
FR-06's executor posts "Confirming: TTL change agreed" to WI-108. Four hours later the GitLab connector harvests that note. Attribution matching fails on format. `authored_by` is set to `external`. FR-34's verifier reads it as third-party evidence and transitions the commitment `PENDING → FULFILLED`. **Nothing crashes; the ledger becomes confidently wrong in the direction that looks like success** — which is verbatim the failure AD-36 was written to prevent. `test_ad36_self_authored_events_are_excluded_from_evidence` stays green, because it constructs `sample_event(authored_by="pm_ai")` by hand and never exercises the matching path.

**Closing rule — AD-34 extension (or new AD-38)**

> **Rule (tighten AD-34):** The reference grammar `<system>:<scope>:<kind>:<native_id>` is the **only** legal form of an external entity reference anywhere in the system — `NormalizedEvent.source_ref`, `Job.target_ref`, the executed-key ledger's `target_ref` and `external_ref`, the AD-37 lock key, and every skill argument naming an external entity. `domain.SourceRef` is one type with one parser; a skill or job accepting a raw URL or bare provider id is rejected at the port boundary, not normalized on entry.
> A class-M skill must record its result as a **parsed `SourceRef`** (`gitlab:alpha:note:887`), not a bare provider id; a skill whose API returns no resolvable reference must instead record `(target_ref, idempotency_key, external_window)` and declare `attribution: weak`, which forbids the events it produces from ever being admitted as commitment evidence.

---

## P2 — At-least-once + an idempotency key the providers do not honour. Nothing says when the ledger row is written.

**Unit A — the "nudge stakeholder" job (FR-26), developer A**
One job, one deterministic key (AD-20). The worker CASes `approved → executing` (AD-37), calls the Teams skill, and **on success** appends the key to the Tier-2 executed-key ledger. Retries are exponential-with-jitter and owned by the scheduler (Consistency Conventions).
*Obeys:* AD-1, AD-13, AD-20, AD-21, AD-37, AD-3 Tier 2.

**Unit B — the "create MR" job (FR-28), developer B**
Also one job, one deterministic key. Worker **claims the key in the ledger first**, then performs the external calls, because an at-least-once queue that records after the fact will double-execute on a crash between the API call and the ledger write.
*Obeys:* the identical set. AD-20 says only that the key is mandatory and deterministic; it says nothing about ledger write ordering, nothing about who checks the ledger, and nothing about what a skill does when it finds an existing key.

**The incompatibility**
The two units implement opposite failure modes from the same rule, because AD-20 assumes the *provider* deduplicates on the key. None of GitLab, Jira, Microsoft Graph (chat/mail send), or Notion accepts a client idempotency key on these endpoints. The key is therefore a purely local claim, and the local claim and the remote effect are not atomic.

- A is **at-least-once**: daemon crash after the Teams message is delivered but before the ledger append ⇒ replay ⇒ the stakeholder is nudged twice.
- B is **at-most-once**: crash after the claim but before the MR is opened ⇒ the key is present ⇒ the job is treated as done ⇒ the MR is never created and no retry is scheduled.

B additionally exposes the multi-step problem AD-20 does not model at all: FR-28's "create MR" is *four* external calls (branch, push, open MR, post comment) under *one* key. A crash after step 2 leaves a claimed key, an orphaned branch, no MR, and no defined recovery.

**The production failure**
Either (A) the PM's stakeholder receives the same "why isn't this done" message twice — an irreversible, reputation-carrying effect the spine elsewhere goes to great lengths to protect (AD-35's fail-closed clause exists for exactly this message) — or (B) a commitment silently never gets its MR, the verifier finds no evidence, and AD-35's coverage guard cannot distinguish "we never did it" from "no coverage", so the commitment sits `PENDING` forever. Both are invisible in logs, because in both cases every component reports success.

**Closing rule — new AD-38**

> **AD-38 — External mutation is a three-phase claim, and irreversibility decides the recovery.**
> The Tier-2 executed-key ledger holds a state per key, not a presence bit: `claimed(key, target_ref, started_at) → succeeded(key, external_ref) | failed(key, reason)`. A class-M skill must CAS-insert `claimed` **before** its first external call and record `succeeded` with the resulting `SourceRef` after.
> On encountering a `claimed` row older than the skill's declared `claim_timeout`, the worker must not blind-retry. It calls the skill's declared **existence probe** — a mandatory read-only method on every mutating skill that answers "did this mutation already land?" against the provider. A skill that cannot implement a probe is classified `irreversible` and its jobs **fail to a Proposal for human confirmation** rather than retrying.
> A job that requires more than one external call must be decomposed into one job per call, each with its own derived key and its own `target_ref`, chained by the scheduler. A single key may never cover two external effects.

---

## P3 — `event_telemetry.db` straddles all three storage tiers. `pm-ai reindex` is defined against a tier, not a file.

**Unit A — the storage developer implementing `pm-ai reindex`**
Reads AD-3: "`pm-ai reindex` rebuilds **Tier 3 only**." Tier 3 is "search and commitment indexes, `vector_index/`, caches". The search and commitment indexes live in SQLite. The only SQLite database named in the whole document is `event_telemetry.db` (AD-6 names it, the storage diagram places it in `~/.pm-ai/private/`, AD-25 adds `~/.manager-ai-private/` and `~/.pm-ai/private/` for analytics). A therefore implements reindex as: drop the index tables in `event_telemetry.db`, drop `vector_index/`, replay `event_log.md`.
*Obeys:* AD-3, AD-5, AD-6, AD-24.

**Unit B — the operations developer implementing `pm-ai backup`**
Reads the Deployment & operations section: "**Backup:** markdown scopes only — project scope rides in git; personal scope may be its own private git repository — plus an exported keychain key. Encrypted indexes are explicitly **not** a backup target."  B backs up markdown and the key.
*Obeys:* the Deployment section, AD-3 Tier 1 ("the backup target"), AD-6.

**The incompatibility**
AD-3 Tier 2 — job queue, `PENDING_RETRY` buffer, connector cursors, executed-key ledger, staged proposals, key material — is "durable and **not derivable from Tier 1**. **Must be backed up.**" But:

- AD-3's own *Prevents* clause states that those things "lived inside" `event_telemetry.db`. The revision changed the **prose** to separate the tiers and left the **file layout** unchanged. There is exactly one named database and it holds Tier 1 telemetry (its name), Tier 2 operational rows (AD-3's own admission), and Tier 3 indexes (nowhere else to put them).
- So A's compliant `reindex` — "drop the index tables in the database" — is one careless `DROP`/`VACUUM`/schema-migration away from taking the job queue, the cursors, and the executed-key ledger with it. The AD-3 test (`test_ad3_indexes_rebuild_from_markdown_without_loss`) compares `snapshot_derived_state()` before and after and **will still pass**, because pending external writes are not part of derived state.
- B's compliant `backup` omits Tier 2 entirely, in direct contradiction of AD-3's "must be backed up". The Deployment section and AD-3 cannot both be followed.

**The production failure**
Machine migration, or a `reindex` after a `sqlite-vec` minor bump (which the Stack section explicitly calls "a reindex event"). Restore/rebuild completes; the daemon starts clean. Consequences, in order of how quietly they fail:
1. Pending external writes are gone — approved proposals never execute, silently.
2. Cursors reset — mass re-harvest. Survivable (AD-34 natural key dedups) but no coverage record exists for the gap.
3. **The executed-key ledger is gone.** Every past pm-ai mutation is now unrecognisable to normalization (AD-36 mechanism 1). Re-harvested history marks pm-ai's own comments `external`. The next sweep transitions a batch of commitments to `FULFILLED` on evidence pm-ai manufactured. This is P1's failure again, arriving through a completely different door — which is the argument for closing it at the tier boundary and not only at the ref grammar.

**Closing rule — AD-3 tightening**

> **Rule (tighten AD-3):** A tier is a **physical partition**, not a label. No file, database, or table may contain rows from two tiers. Tier 2 lives in `~/.pm-ai/private/ops.db`; Tier 3 lives in `~/.pm-ai/private/derived.db` and `vector_index/`; Tier-1 harvested telemetry lives in the markdown ledgers. The storage service declares each opened handle's tier at construction and **refuses to execute a drop/rebuild path against a non-Tier-3 handle** — the guard is in the code, not in the operator's memory.
> `pm-ai backup` covers Tier 1 **and Tier 2**. The Deployment section is corrected to match; a backup that omits Tier 2 must fail loudly, not silently succeed. `pm-ai doctor` reports the age of the last Tier-2 backup.

---

## P4 — FR-37 compaction, AD-5 append-only, and AD-3 zero-loss rebuild cannot all hold.

**Unit A — the pruning-pipeline developer (FR-37)**
AD-3: "FR-37 compacts entries older than 7 days into milestone summaries, **which is what keeps this tier bounded**." A implements compaction as a rewrite: read `event_log.md`, replace pre-cutoff entries with `[MILESTONE]` summaries, write the file. The tier is now bounded, as AD-3 requires.
*Obeys:* AD-3 (Tier 1 bounded), AD-24 (no debug output), AD-4.

**Unit B — the storage developer**
AD-5: "Markdown ledgers and logs are **append-only**: a status change is a new entry keyed by id, never an in-place edit." AD-24: `event_log.md` is "append-only, human-readable, **never rotated**." `test_ad5_storage_never_rewrites_a_markdown_ledger_in_place` fails any `write_text` or non-`"a"` `open` mode on a file whose call site mentions `event_log`. B therefore implements compaction as an **appended** `[COMPACTION]` entry that supersedes older entries at fold time.
*Obeys:* AD-5, AD-24, AD-35 (deterministic fold), and the existing test suite.

**The incompatibility**
A satisfies AD-3's boundedness and fails AD-5's append-only rule (and its test). B satisfies AD-5 and **fails AD-3's stated purpose** — the file grows forever, so nothing keeps Tier 1 bounded, and every fold now has to replay full history plus a supersession layer, which pushes the AD-22 50–150 ms retrieval budget onto a linearly growing input. A third consequence hits both: after A's compaction, `test_ad3_indexes_rebuild_from_markdown_without_loss` **must fail**, because compaction by definition discards detail that the pre-compaction index contained. A developer who notices this "fixes" it by making compaction lossless — at which point it is not compaction.

This is a three-way contradiction inside the current document, not an ambiguity.

**The production failure**
Whichever way it lands, it lands late. B's version: at month nine `event_log.md` is 400 MB, the 07:00 briefing's retrieval budget is blown, and FR-37 is quietly declared "not implemented". A's version: a compaction run on a Friday rewrites the ledger, the AD-3 test goes red on Monday, someone deletes the assertion, and the sovereignty property — "delete the derived state, markdown reconstitutes it" — is gone without any decision having been made.

**Closing rule — AD-3 / AD-5 reconciliation**

> **Rule (new AD-39 — Ledgers are segmented; compaction rolls over, never rewrites):** A Tier-1 ledger is a **segment set**: one open segment (`event_log.md`) plus sealed segments (`archive/event_log-<period>.md`). The storage service only ever appends to the open segment and seals a segment by **rename**, never by in-place edit — preserving AD-5 exactly.
> FR-37 compaction appends `[MILESTONE]` summary entries to the open segment and seals the segments they summarize. **Boundedness means bounded working set, not bounded history:** retrieval and folding read the open segment plus the milestone entries by default, and read sealed segments only on explicit historical query.
> AD-3's zero-loss rebuild guarantee is scoped to the **retained segment set**. Discarding a sealed segment is a separate, explicitly-named destructive operation with the same CLI ceremony as discarding Tier 2.

---

## P5 — `<scope>` in the reference grammar means three different things, and the grammar's own arity is undefined.

**Unit A — the GitLab connector developer**
AD-34 fixes `source_ref = <system>:<scope>:<kind>:<native_id>`; the worked example is `gitlab:alpha:commit:9f2a1c`. A reads `<scope>` as the pm-ai **project registry slug** (`alpha`), because AD-4 and AD-10 use "scope" to mean the storage/ownership scope and AD-10's instance tuple literally begins `(scope, connector_type, …)`. So A emits `gitlab:alpha:commit:9f2a1c`.
*Obeys:* AD-34.1, AD-9, AD-27, AD-10.

**Unit B — the Jira connector developer**
B reads `<scope>` as the **provider-side namespace**, because that is what makes a ref resolvable back to a URL and what keeps it unique across projects that share a slug. Jira's namespace is the project key; B emits `jira:PAY:issue:PAY-102`. The document's own example — `jira:alpha:issue:PAY-102` — is ambiguous evidence: `alpha` could be either reading, and `PAY-102` already carries the provider namespace inside `native_id`.
*Obeys:* AD-34.1, AD-9, AD-27, AD-10.

**Two further sub-divergences in the same grammar:**
- **Arity.** AD-34's own example `meeting:mtg_01HX` has **two** segments against a four-segment grammar. A strict parser (four segments required) rejects the document's own example; a lenient parser accepts two-to-four. `test_ad34_source_refs_follow_the_fixed_grammar` asserts `meeting:mtg_01HX` **parses**, so the lenient parser is the tested one — and then `PAY-102` and `commit 9f2a1c` must be rejected on some rule the grammar never states.
- **Speaker.** AD-33 says meeting-derived facts cite "`meeting:<id>` **plus speaker**". Inside the ref or beside it? `test_ad33_source_refs_never_point_at_a_transcript` parses `meeting:mtg_01HX/speaker:alex` — a fifth grammar element with a `/` separator that AD-34 never mentions. And it does so through `pm_ai.domain.citations.SourceRef`, while `test_ad34` uses `pm_ai.domain.identity.SourceRef`. **The test suite already contains two `SourceRef` types, in two modules, with two grammars and two exception types** (`NonDurableReferent` vs `MalformedSourceRef`). The divergence is not hypothetical; it is committed.

**The incompatibility**
AD-34.3 makes `(source_system, source_ref)` **the natural key** for deduplication and joins. Under A and B the same real-world entity has two spellings, so:
- Re-harvest idempotency fails the moment a connector's ref convention shifts (a rename, a group move, a self-hosted migration) — every historical event's key rotates and re-harvest doubles every metric, which is precisely the outcome AD-34 names in its *Prevents*.
- Cross-source joins fail: a commitment cited against `gitlab:alpha:issue:108` cannot be matched to a Jira event about the same work under `jira:PAY:issue:PAY-102`, so evidence from one source is invisible to the other — again the AD-27/AD-34 *Prevents*, re-entering through the field the ADs left open.
- Project rename or re-registration (`pm-ai project add` again after a repo move) silently orphans every A-style ref, because the slug is user-visible and mutable and nothing says it is not.

**The production failure**
A commitment that *was* delivered shows no evidence because the evidence carries the other spelling; the sweeper eventually declares it `BROKEN`; FR-26 sends an irreversible nudge about work that shipped. Or, on a slug change, every citation in `commitments_log.md` becomes unresolvable and the drift auditor (FR-24) reports clean against nothing.

**Closing rule — AD-34.1 tightening**

> **Rule (tighten AD-34.1):** `SourceRef` is a **single type in a single module** (`pm_ai.domain.identity`) with exactly four segments, all mandatory: `<system>:<scope>:<kind>:<native_id>`.
> - `<system>` is a value of the closed `SourceSystem` enum in `domain` (`gitlab`, `jira`, `teams`, `outlook`, `slack`, `notion`, `hr`, `meeting`, `pm_ai`).
> - `<scope>` is the **immutable project id minted by `pm-ai project add`** (`prj_01HX…`), never the user-visible slug, never the provider namespace, never a storage-scope name. Renaming a project or repointing a connector does not change any existing ref. Personal-scope refs use the reserved scope id `personal`.
> - The provider namespace, where needed to resolve a URL, is a **field on `ConnectorInstance`**, not part of the ref.
> - `<native_id>` is the provider's stable id, never a URL and never a display key.
> - A speaker is **not** part of the ref. Meeting-derived facts carry `source_ref = meeting:<prj>:meeting:<id>` **and a separate `actor_id` field** (AD-34.2). The `/speaker:` form is prohibited.
> The word `scope` is reserved in `domain` for the AD-4 storage scope only; the grammar segment is renamed `<project>` throughout to remove the collision.

---

## P6 — AD-25 separates the personal *analytics database* and leaves the *vector index* shared.

**Unit A — the retrieval developer**
Builds one `vector_index/` (the storage diagram shows exactly one, under `~/.pm-ai/private/`) covering coaching history, goals, project telemetry, and meeting extractions, with a `scope` column filtered at query time.
*Obeys:* AD-3 (Tier 3), AD-6 (index unencrypted, `0600`), AD-25 (never opens `~/.manager-ai-private/`), AD-22 (no model in the retrieval path).

**Unit B — the prep-dashboard developer (FR-32)**
Calls retrieval for a project-scope prep dashboard, passing the project and a top-k. Renders the result into `.project-ai/memory/` — a **git-committed** file.
*Obeys:* AD-4, AD-22, AD-25, AD-31 (the frontier call declares `destination="project:alpha"`).

**The incompatibility**
AD-25's own reasoning is that a privacy boundary enforced by "a tag someone remembers to check" is not enforcement, so the personal analytics store is **physically** separate. That reasoning was applied to derived *metrics* (burnout numbers) and not to the **content**, which is what the vector index holds. Coaching history lives in `~/.manager-ai/memory/` — plaintext markdown, indexed like everything else, and *not* inside `~/.manager-ai-private/`. So A's index legitimately contains personal coaching text, AD-25 is untouched (nobody opened the analytics DB), and the only thing standing between a 1:1 confession and a git commit is B remembering a `WHERE scope = …` predicate on every one of the many retrieval call sites (FR-23, FR-24, FR-25, FR-32, FR-33, FR-37).

`test_ad25_project_rendering_cannot_open_the_personal_store` asserts only that no opened datasource string contains `manager-ai-private`. A's shared index passes trivially.

**The production failure**
One retrieval path — a fuzzy anchor match, a "related context" widen, a `k` bump — returns a chunk of `coaching_1on1_history.md` into a prep dashboard, which is committed to the team repository and pushed. FR-16's charter is broken in the one direction that cannot be undone: the material is now in the employer's git history, which AD-31 correctly identifies as the actual adversary.

**Closing rule — AD-25 extension**

> **Rule (extend AD-25 to derived text):** Scope separation is physical for **every** store that can carry scope-bearing content, derived stores included. There is one vector index and one search index **per scope**: `~/.manager-ai/private/index/`, `~/.pm-ai/private/index/<project_id>/`. The retrieval service takes a `scope_set` and opens only those files; cross-scope retrieval is impossible by construction rather than by predicate. A retrieval call with no explicit `scope_set` is a defect, exactly like a model call with no `task_class` (AD-15).
> The `scope_set` permitted for a call is derived from the **destination artifact** (AD-31.3), not chosen by the feature: an artifact under `.project-ai/` may only be produced from that project's scope set.

---

## P7 — AD-33 forbids using the transcript offset for ordering; AD-35 requires ordering within a meeting by `occurred_at`.

**Unit A — the extraction developer**
AD-33: the time offset is "an optional time offset **used only for tracing**." A therefore does not use it for anything else and sets every meeting-derived fact's `occurred_at = meeting.start`.
*Obeys:* AD-33, AD-35 (occurred_at is domain reasoning; not backfilled from `ingested_at`), AD-34.

**Unit B — the ledger developer**
AD-35: `occurred_at` "governs domain reasoning: due dates, 'did the commit follow the promise', **ordering within a meeting**," and folding is `(occurred_at, entry_id)`, "a total order stable across rebuilds." Ordering within a meeting is only possible from the offset, so B sets `occurred_at = meeting.start + offset`.
*Obeys:* AD-35, AD-34, and AD-33's letter as B reads it (the offset feeds ordering, which AD-35 explicitly demands).

**The incompatibility**
Under A, every fact extracted from one meeting has an identical `occurred_at`, so the fold's tiebreak is `entry_id` — a ULID minted at persist time, i.e. **extraction order**, which is model-output order and is not stable across a re-extraction with a different local model (which the Deferred section says Phase 1 will change). Under B the order is transcript order. These differ, and the difference is semantically load-bearing: a meeting in which the team says "we'll ship the TTL change Friday" at 04:12 and "actually, hold the TTL change" at 41:55 folds to two opposite commitment states.

A third builder handling the **manual VTT drop** hits a worse version: AD-35 says an absent `occurred_at` is "flagged, not silently backfilled", but a dropped `.txt` with no timestamps has no provider clock at all. Flagged-and-null entries have no position in a `(occurred_at, entry_id)` total order, so either they sort into a null bucket (nondeterministic against A/B) or they are dropped from the fold entirely.

**The production failure**
`pm-ai reindex` — an operation AD-3 promises is lossless — produces a different commitment state than the live system had, because the fold's effective tiebreak changed. `test_ad35_ledger_folding_is_deterministic` compares `fold(entries)` to `fold(reversed(entries))` and stays green under both A and B, because both are deterministic *given their own timestamps*; the divergence is in how the timestamps were assigned, which no test observes.

**Closing rule — AD-35 / AD-33 reconciliation**

> **Rule (tighten AD-35, amend AD-33):** For a meeting-derived fact, `occurred_at = meeting.start + transcript_offset` when the transcript carries an offset, else `meeting.start`, and the field carries `occurred_at_precision ∈ {exact, meeting, derived}`. AD-33's "used only for tracing" is amended: the offset **may not be a citation target**, but it **is** the ordering basis within a meeting.
> `occurred_at` is **never null on a folded entry.** An entry whose `occurred_at` cannot be established is quarantined in a `needs_timestamp` queue and surfaced to the user; it never enters the fold, because a null has no position in the total order AD-35 requires.
> Ties in `(occurred_at, entry_id)` are resolved by `entry_id`, and `entry_id` for a meeting-derived fact must therefore be **derived deterministically** from `(meeting_id, offset, extraction_hash)` — not a persist-time ULID — or re-extraction reorders history.

---

## P8 — Two components mint `Meeting` records and nothing defines a `Meeting` natural key.

**Unit A — the Outlook/Graph calendar connector**
Harvests calendar events on the AD-9 cadence (4h default), maps them to `NormalizedEvent`s, and the pipeline creates the Tier-1 `Meeting` records AD-33 requires ("id, calendar event reference, title, start, duration, attendees, derived-transcript pointer, processing status"), since FR-03's man-hour cost and FR-32's prep dashboard both need a `Meeting` **before** any transcript exists.
*Obeys:* AD-9, AD-33, AD-5, AD-34, AD-12.

**Unit B — the manual transcript adapter (AD-23)**
A `.vtt` lands in the watched folder at 15:30. AD-23: bind to the calendar event "where one exists, **otherwise the drop supplies title, start, and attendees to mint the record**." No calendar match is found — because the title is `payments-sync-recording.vtt`, or because the connector's next harvest is 90 minutes away, or because the meeting was ad-hoc. B mints a new `Meeting`.
*Obeys:* AD-23, AD-33, AD-5, AD-32 (manual is never auto-execute).

**The incompatibility**
AD-34.3 fixes the natural key **for `NormalizedEvent`** — `(source_system, source_ref)`. `Meeting` is not a `NormalizedEvent`; it is a locally-minted first-class record, and nothing defines its identity, its sole writer, or what "binds to its calendar event where one exists" means operationally (exact id? fuzzy title + start window? attendee overlap?). Two compliant units mint two `Meeting` records for one meeting.

Worse, there is no repair path. When the calendar harvest lands at 16:00, the "right" fix is to merge `mtg_B` into `mtg_A` — but AD-5 makes the ledger append-only, so every `source_ref = meeting:…:mtg_B` already written into `commitments_log.md` can never be repointed.

**The production failure**
- FR-03's Man-Hour Cost double-counts the meeting (two records, both with attendees and duration) — a metric that goes into planning decisions.
- Citations fork: the prep dashboard cites `mtg_A`, the commitments cite `mtg_B`, and the drift auditor (FR-24) walking `fact → meeting → transcript` finds a transcript on one and not the other, reporting "clean" for the half it cannot see.
- AD-33's whole purpose — "one entity rather than three ad-hoc lookups" for FR-03, FR-32, UJ-8 — is defeated while every AD is satisfied.

**Closing rule — AD-33 extension**

> **Rule (extend AD-33):** `Meeting` has a natural key and a single writer.
> - **Single writer:** only `pm_ai.core.meetings.bind_or_mint()` may create a `Meeting`. Connectors and transcript adapters call it; neither creates a record directly.
> - **Natural key:** `(calendar_system, calendar_event_id)` when a calendar reference exists; otherwise the deterministic key `(project_id, date, normalized_title, start rounded to 15 min)`. `bind_or_mint` is idempotent on that key.
> - **Late binding is an alias, never a rewrite.** When a calendar event later matches a drop-minted meeting, an append-only `meeting_alias` entry (`mtg_B → mtg_A`) is written to Tier 1. All citation resolution walks aliases; no existing `source_ref` is ever edited, preserving AD-5.
> - A `Meeting` minted from a drop carries `provenance: manual`, which AD-32 already treats as never auto-executing and which FR-03 must exclude from man-hour cost until it is bound or confirmed.

---

## P9 — AD-35 records coverage in `ingested_at` and consumes it in `occurred_at`, and never says whose coverage counts.

**Unit A — the scheduler developer**
AD-35: "The scheduler logs harvest coverage windows per connector instance." Coverage is operational reasoning, and AD-35 is emphatic that operational reasoning uses `ingested_at`. A therefore records, per instance, `covered(from=last_success_ingested_at, to=now_ingested_at)` on each successful harvest.
*Obeys:* AD-35 (correct clock for operational state), AD-9, AD-3 Tier 2.

**Unit B — the commitment sweeper developer**
AD-35: the sweeper "must not declare `BROKEN` across a window it has no coverage for." The window in question is the commitment's window — promise date to due date — which is **domain** reasoning and therefore `occurred_at`. B asks: "do I have coverage over `[promise.occurred_at, due_date]`?"
*Obeys:* AD-35 (correct clock for domain reasoning), AD-14, AD-34.

**The incompatibility**
A's records are intervals in ingest time; B's question is an interval in event time. AD-35 forbids substituting one for the other — so B's question is **unanswerable from A's data**, by the very rule that created both. And the mapping is genuinely non-trivial: a GitLab harvest at 16:00 with an `updated_after` cursor returns events that *occurred* over the previous days; a provider that orders by update time may never return an old commit at all; a Jira JQL page covers issue-update time, not the work's occurrence time.

Two compliant resolutions, both wrong:
- B treats any successful harvest as covering everything before it. The laptop sleeps over a weekend, harvests successfully at 09:00 Monday before the connector has caught up, coverage looks complete, `BROKEN` fires — **the exact scenario AD-35's *Prevents* describes, reproduced through a compliant implementation.**
- B requires an attested `occurred_at` coverage interval that no connector produces. The sweeper never declares `BROKEN`, FR-34's closed loop silently does nothing, and nothing fails.

**A second unowned question in the same rule:** *whose* coverage? A commitment "I'll get the payments MR merged" — does it require coverage from the GitLab instance only, or from every instance in the project? Developer C requires all instances (any one connector being down suppresses all sweeping forever); developer D requires any instance (a healthy Slack connector "covers" a GitLab outage). Both obey the sentence as written.

**The production failure**
FR-26's nudges are irreversible outbound messages to real colleagues. The rule was written to fail closed and, under the compliant reading A+B, fails open — or fails so far closed that FR-33/FR-34, the product's differentiating feature, silently never fires. Neither state is detectable from logs, and `test_ad35_sweeper_will_not_declare_broken_without_coverage` passes `coverage_gap=True` **by hand**, so it tests the consumer and never the producer.

**Closing rule — AD-35 tightening**

> **Rule (tighten AD-35 coverage):** Coverage is an attestation in **`occurred_at` space**, produced by the connector, not inferred by the scheduler.
> - `harvest()` returns `(events, Coverage)` where `Coverage` is `complete(from_occurred_at, to_occurred_at)` or `none`. A connector whose provider cannot support an occurred-time query declares `none` — permanently, if necessary. `none` is a legitimate answer; a fabricated interval is a defect.
> - The scheduler persists coverage per `ConnectorInstance` in Tier 2, alongside a gap record for every skipped, failed, or cursor-reset cycle (see S2).
> - Every commitment type declares in `domain` its `evidence_sources: set[SourceSystem]` — the connectors capable of producing evidence for it. The sweeper requires `complete` coverage from **each** declared source over the commitment's `occurred_at` window; anything less yields `UNVERIFIED`, never `BROKEN`.
> - `UNVERIFIED` is a first-class, user-visible outcome with its own surface treatment. Suppressed sweeps must be visible, or the fail-closed guard becomes a silent feature outage.

---

## P10 — `authored_by` is binary, decided by two mechanisms with no precedence, and the single-user deployment makes both wrong.

**Unit A — the normalization developer**
AD-36 gives two mechanisms and says "two mechanisms because one of them will have gaps." A reads that as a union: `authored_by = pm_ai if (ledger_match or bot_actor) else external`.
*Obeys:* AD-36, AD-34.

**Unit B — the actor-resolution developer**
B reads "Where the connector runs under a distinct bot identity, actor resolution marks it **independently**" as: actor resolution is authoritative where it applies, ledger matching is the fallback where it does not.
*Obeys:* AD-36, AD-34.2.

**The incompatibility, and a defect both share**
The two readings agree until they disagree, and the disagreement case is the *normal* one for this product. pm-ai is single-user, and AD-10 configures connectors with the PM's own credentials. **There is no distinct bot identity.** So:
- Under B, if the connector token *is* Andrei's account, actor resolution cannot distinguish pm-ai's comment from Andrei's — and if a developer wires "the identity the connector authenticates as" to `pm_ai`, **every commit Andrei personally pushes is marked `pm_ai` and excluded from evidence.** Commitments the PM personally delivered never reach `FULFILLED`.
- Under A, the ledger-match arm is the only working mechanism — and it is exactly the arm P1 and P3 break. When it has a gap, the enum's binary shape forces the answer to `external`, i.e. **the enum fails open into the direction AD-36 exists to prevent.**
- Neither developer has a defined matching function. The ledger holds `(target_ref, external_id)`; the event holds `source_ref`. Whether `external_id="887"` matches `gitlab:prj_01:note:887` is a decision each developer makes alone (substring? suffix? parse-and-compare?).

**The production failure**
Two symmetric silent corruptions of the ledger that feeds FR-33/FR-34 and, through FR-30/FR-31, a career dossier: either the PM's real work is invisible to verification, or pm-ai's own comments are counted as third-party proof of delivery. Both look like success in the UI. `test_ad36_self_authored_events_are_excluded_from_evidence` constructs the flag by hand and never touches the mechanism that assigns it.

**Closing rule — AD-36 tightening**

> **Rule (tighten AD-36):**
> - `authored_by ∈ {pm_ai, external, **unknown**}`. `unknown` is the default when neither mechanism can attest, and **`unknown` is inadmissible as evidence** — the enum fails closed.
> - Attribution is decided exactly once, by one function in `pm_ai.core.attribution`, with a fixed precedence: (1) executed-key ledger match ⇒ `pm_ai`; (2) resolved actor is the registered pm-ai service identity ⇒ `pm_ai`; (3) resolved actor is a known human ⇒ `external`; (4) otherwise ⇒ `unknown`.
> - Matching is on a **parsed `SourceRef`**, per P1's rule — never string containment.
> - The daemon must know whether a distinct service identity exists. `ConnectorInstance` carries `service_identity: Actor | None`. When it is `None` (the connector authenticates as the user), rule (2) is **disabled** for that instance and ledger matching is the sole mechanism; `pm-ai doctor` reports every instance running without a distinct identity, because that is a degraded attribution mode, not a configuration detail.

---

## P11 — AD-32's reversible verbs are a domain enum; AD-18's skill scopes are a registry vocabulary; nothing binds them.

**Unit A — the `set_priority` skill**
Narrow, single-purpose; declares scope `gitlab:issues:write`. The spoken-command classifier maps "bump this to P2" to verb `priority`, which is in AD-32's reversible set, from a provider-authenticated Teams transcript where the speaker resolves to the PM. Auto-executes.
*Obeys:* AD-32 (all three conditions), AD-18, AD-1, AD-20.

**Unit B — the `update_work_item` skill**
General-purpose, declares the same scope `gitlab:issues:write`, accepts a field map. The same classifier maps "bump this to P2" to verb `priority` and dispatches to B with `{"priority": "P2"}`. Compliant, and a natural design — one skill per provider entity is exactly how MCP tools are usually written.
*Obeys:* AD-32, AD-18, AD-1, AD-20.

**The incompatibility**
AD-32 gates on the **classified verb**; AD-18 authorizes on the **declared scope**; the executed **capability** is a third thing nobody bounds. B's skill can also set `state: closed` — an irreversible verb AD-32 says "always stage, regardless of source or speaker" — and it will do so if any field reaches the payload. The reversibility guarantee is enforced on a label attached upstream of an executor that is not constrained by it.

The attack path is exactly the one AD-32's *Prevents* names: the transcript is untrusted input, and the verb classifier is a local 8B model reading it. A line in a meeting — or in an injected calendar invite that reached the same pipeline — that reads "…and priority, also mark it closed" produces a `priority` classification with a payload that closes the issue. AD-12/AD-29 sanitization is aimed at prompt injection, not at payload-field escalation.

**A second unowned obligation in the same AD:** "Every auto-execution emits a card carrying **one-tap undo**." Nothing registers an inverse. Developer A implements undo as a compensating skill call; developer B implements it as "delete the comment" (which for `priority` means restoring a prior value nobody recorded). Neither is required to exist before the verb is declared reversible. And the undo is itself a class-M mutation — needing its own idempotency key, its own event-log entry, and a decision about whether it stages — none of which the spine addresses.

**The production failure**
An auto-executed "reversible" command closes a work item, and the undo button either does nothing or fails, because no inverse was ever registered for the verb that was actually exercised. The security property AD-32 states — that an untrusted transcript can never confer irreversible authority — is false in production while the AD-32 tests (which parametrize over abstract verbs) stay green.

**Closing rule — AD-32 / AD-18 binding**

> **Rule (bind AD-32 to AD-18):** Reversibility is a property of the **skill**, not of the classified verb.
> - Every registered skill declares `verbs: set[Verb]` (from the `domain` enum), `reversibility ∈ {reversible, irreversible}`, and, when `reversible`, an `inverse: SkillRef`. The registry **refuses to load** a skill declaring `reversible` without a registered inverse.
> - Auto-execution resolves a verb to exactly one skill whose declared verb set contains it and whose reversibility is `reversible`. A skill capable of any irreversible effect may not declare a reversible verb; ambiguity (two candidate skills) stages rather than executing.
> - The payload is validated against the **verb's** schema, and fields outside it are **rejected, not dropped** — a payload carrying `state` under verb `priority` is an authorization error and an `event_log.md` entry, not a silently-ignored key.
> - An undo is a class-M mutation with idempotency key `sha256("undo" + original_key)` and its own event-log entry; it never stages, and it is subject to the same AD-37 `target_ref` lock as the mutation it reverses.

---

## P12 — What the PM approved and what the executor performs are two artifacts with nothing binding them.

**Unit A — the HR goal-sync proposal type (FR-31)**
Registers type `hr_goal_sync` with a payload schema and an executor callback (AD-13). The Telegram card renderer summarizes the payload into card text. The executor reads the stored payload at execution time.
*Obeys:* AD-13, AD-20, AD-37, AD-21.

**Unit B — the work-item update proposal type (FR-06)**
Same registration shape. Its executor, following AD-37's instruction that "a worker re-checks state **at execution time**, not only at enqueue time," re-fetches the work item and applies a *diff* computed against current remote state rather than the literal payload — which is the more careful implementation, and the one AD-37 appears to ask for.
*Obeys:* AD-13, AD-20, AD-37.

**The incompatibility**
Under B, the effect performed is a function of remote state at execution time, while the card the PM approved was rendered from the payload at staging time — up to seven days earlier (AD-13's default TTL). The two can differ arbitrarily. Under A they cannot, but A has the opposite problem: the payload may have become invalid and A applies it blindly.

Three unowned lifecycle questions compound it:
1. **`edited` is in the status set with no defined semantics.** `staged → approved → executed | edited | rejected | expired`. Is `edited` terminal? Developer A: edit CASes `staged → edited` (terminal) and mints a **new** proposal with a **fresh 7-day TTL**. Developer B: edit CASes `staged → edited` and the same proposal then goes `edited → approved`, keeping the original expiry. Both honour the enum. Under B, **`edited` proposals are never swept**, because AD-37 says the sweeper CASes `staged → expired` only — so an edited-but-never-reapproved proposal is immortal, which is precisely the "what if he never approves" question AD-13 was created to answer once.
2. **Payload schema versioning across an upgrade.** Staged proposals are durable Tier-2 rows with a 7-day life; `uv tool install` upgrades can land inside that window. Nothing says whether the payload is validated against the current schema at approval, whether the schema version is stored, or what happens when a registered type disappears. Compliant answers range from "silently vanish" to "the approve tap 500s forever" to "coerce into the new schema and execute" — the last being the dangerous one.
3. **A cosmetic edit collides with AD-20.** If the edit changes only whitespace, the derived idempotency key is unchanged, the executed-key ledger already holds it, and the edited proposal executes as a **no-op** while reporting success.

**The production failure**
The PM taps Approve on a card reading "Set WI-108 priority to P2"; six days of remote churn later the executor computes a diff and also reverts a field a colleague changed. Or an upgrade lands and the approval queue is quietly empty on Monday. In both cases the audit trail records an approval and an execution that do not correspond to the same content — which makes `event_log.md`, AD-24's domain truth, actively misleading.

**Closing rule — AD-13 tightening**

> **Rule (tighten AD-13):**
> - **What is approved is the exact payload.** At approval the system stores `approval_hash = sha256(type + schema_version + canonical_payload + rendered_card_text)`. The executor recomputes it and refuses on mismatch. An executor may **read** remote state to decide whether to proceed or abort (AD-37's re-check), but may never compute the effect from it — the effect is the payload.
> - **The card is a pure function of the payload.** One renderer, deterministic, in core; surfaces choose only the widget.
> - **`edited` is terminal.** Editing CASes `staged → edited` and mints a new `Proposal` linked by `supersedes`, with a fresh TTL and a fresh approval hash. The sweeper sweeps `staged` **and** any non-terminal state; no state is exempt from expiry.
> - **A payload's schema version is stored.** A staged proposal whose `schema_version` has no registered executor is expired by the sweeper with a user-visible notice; it is never coerced.
> - An edit that leaves the canonical payload byte-identical is rejected at the surface as a no-op, so it cannot collide with the AD-20 key.

---

## P13 — "one `event_log.md` entry per invocation" — which `event_log.md`?

**Unit A — the HR-goal-sync skill (FR-31)**
Class M, so AD-1 requires "one `event_log.md` entry per invocation". A writes it to `~/.manager-ai/memory/event_log.md`, because goals and career material are sovereign personal scope (AD-4, AD-28).
*Obeys:* AD-1, AD-4, AD-24, AD-28, AD-5.

**Unit B — the GitLab comment skill (FR-06)**
Writes its entry to `<repo>/.project-ai/memory/event_log.md`, because the mutation targets project material.
*Obeys:* the same set.

**The incompatibility**
The storage diagram shows an `event_log` in **both** `~/.manager-ai/memory/` and `repo/.project-ai/memory/`, and AD-24 refers to "`event_log.md`" as though it were one file. Three obligations are defined against that singular:

- **AD-1**: one entry per class-M invocation.
- **AD-17**: "Every frontier call logs token counts and a cost estimate to `event_log.md`; the running monthly total surfaces in briefings and CLI."
- **AD-31.2**: "Every frontier call records scope provenance to `event_log.md`. The CLI answers *what has left this machine, and when* from that record."

With N project logs plus a personal log, **the AD-31 audit is unimplementable** — it would have to scan every registered repository, including ones not currently checked out, on branches that may not contain the entries. AD-17's monthly total is likewise the sum over an unbounded, partially-unavailable file set, and will be wrong (low) in a way nobody notices. Meanwhile B's choice writes a class-M record about a personal HR goal into a git-committed repo if the active project scope is used as the default — AD-28 forbids a `CoachingCommitment` reaching the project ledger but says nothing about **log entries**, which carry the same content.

Note also that the existing tests do not disambiguate: `test_ad31_every_frontier_call_records_scope_provenance` asserts against `router.disclosure_log()` — a **router-internal structure**, not `event_log.md` at all. So the spine says event_log and the enforcement says router memory; a compliant build can satisfy the test while writing nothing durable.

**The production failure**
The CLI's answer to "what has left this machine" is confidently incomplete — the one command whose entire value is completeness. AD-31 explicitly frames the disclosure log as what "converts the charter from an assurance into an audit"; a partial audit is worse than none, for the reason AD-31 itself gives about FR-16.

**Closing rule — AD-24 / AD-1 / AD-31 reconciliation**

> **Rule (tighten AD-24):** `event_log.md` is a **scoped family**, and every writer declares a scope. A closed mapping in `domain` assigns each entry type to exactly one scope owner; the storage service routes by that mapping and rejects an unmapped type. A class-M entry is written to the scope of its **target**, declared by the skill, never to "the active project".
> **In addition**, every class-M invocation and every class-F call appends a record to a single append-only `~/.pm-ai/egress_log.md` — the one file AD-31's disclosure audit and AD-17's cost total read. Two writes, deliberately: the scoped log is the human record, the egress log is the audit, and the audit must be answerable from one file that is always present.
> The AD-31 test is corrected to assert against that durable file, not a router-held list.

---

# Part 2 — Secondary pairs

These are real but narrower, or their blast radius is contained.

**S1 — Actor alias resolution: write-time or read-time?**
AD-34.2 says normalization "maps it through an alias table", implying write-time. Developer A resolves at write time and stores `actor_id`; developer B resolves at read time through the table. When Andrei later maps "Unknown Speaker 3" → alex, A's 400 stored events stay `unresolved` forever (append-only forbids the fix) while B's correct themselves retroactively — so contribution counts feeding FR-30/FR-31 (a **performance review** input) differ by implementation, and neither is reproducible after the other's rebuild. The alias table also fits none of AD-3's three tiers: not derivable (so not Tier 3), not operational (so not naturally Tier 2), not listed in Tier 1 — so its backup status is undefined for a table whose loss silently corrupts identity.
> **Rule:** events store both the raw handle **and** the write-time resolution; `actor_id` is always resolved at read time through the alias table; the alias table is a Tier-1 append-only markdown file in `~/.pm-ai/` with `effective_from`, so resolution is reproducible after any rebuild. Merging two established actors is an explicit `pm-ai actor merge` operation recorded as a Tier-1 entry, never an alias edit.

**S2 — Nobody owns cursor invalidation.**
AD-9 makes `Cursor` opaque bytes replayed verbatim. When the instance config changes (repo repointed, token rotated to an account with different visibility) or the connector's code changes its pagination scheme, developer A keeps the cursor (silent permanent data loss — the connector resumes from a position meaningless in the new context) and developer B resets it (mass re-harvest; survivable via AD-34 dedup, but no coverage gap is recorded, so P9's sweeper believes it has coverage it never had).
> **Rule:** `Cursor` is `(connector_type, cursor_format_version, bytes)`. A connector declares `cursor_format_version` and a set of `cursor_affecting` config fields. The scheduler discards a cursor whose version or config hash no longer matches, restarts from `initial_cursor`, and **records a coverage gap** (AD-35) over the affected window.

**S3 — AD-21's "expected duration" is self-declared.**
`dispatch.plan(estimated_seconds=…)` — developer A uses a per-endpoint constant, developer B a rolling p50. The same logical operation acks on one surface and blocks on the other, breaking AD-7's "identical functionality through the same core services". Worse: an inline path that overruns has no job row (AD-20 mandates durability only for *deferred* work), so the frontier call completes, the client has timed out, and the answer is lost — and the spend is still charged. Opus 5's default-on thinking, which the Stack section flags, makes underestimation the normal case.
> **Rule:** dispatch mode is a function of **task class**, not a per-call estimate. Any path containing a frontier call or a heavy local-model job is asynchronous by construction; inline is permitted only for pure retrieval (AD-22). Every path that reaches a model gets a durable job row before the model is called, so a completed result is never homeless.

**S4 — Sanitizer versioning versus AD-3's rebuild equality.**
AD-29 mandates a derived copy and an untouched raw, but not whether the derived copy is persisted. Developer A persists it (Tier 3, disposable, rebuildable); developer B computes it at prompt-assembly. When the sanitizer gains a rule, A's rebuild produces different bytes and `test_ad3_indexes_rebuild_from_markdown_without_loss` goes red — the obvious "fix" is to pin the sanitizer version per stored copy, which means a known injection string stays live in every cached context.
> **Rule:** the sanitized copy is Tier 3 and carries `sanitizer_version`. A change to the sanitizer is a **reindex event** (like the `sqlite-vec` bump the Stack already names): all derived copies are regenerated, and AD-3's equality guarantee is stated as *rebuildable at the current sanitizer version*, not byte-identical across versions.

**S5 — Ollama residency: pool or adapter?**
AD-19 says the pool bounds heavy jobs and that the daemon must also set `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, `keep_alive: 0` before dispatching transcription. It does not say **who**. Developer A does it in the whisper adapter, developer B in the router. The bound is "configurable" — and the moment it is raised to 2, a whisper job and an Ollama job run concurrently and the `keep_alive: 0` unload fires mid-inference. Raising a documented-as-configurable knob silently breaks an invariant.
> **Rule:** residency is the pool's, not an adapter's. Each heavy job declares `resource_class ∈ {ollama, whisper}`; the pool enforces mutual exclusion **between** resource classes regardless of the configured bound, and the bound is capped at 1 per resource class. Jobs carry a priority from a `domain` enum so the 07:00 briefing is not starved behind a reindex.

**S6 — Nobody owns Tier-3 invalidation when git changes Tier 1 underneath the daemon.**
Project scope is git-committed and hand-editable, and the spine explicitly requires parsers to tolerate hand-edits. A branch switch or `git pull` changes `commitments_log.md` under a running daemon. Developer A caches the folded ledger in memory; developer B re-reads per operation. Nothing owns staleness detection, so A's sweeper acts on commitments that no longer exist on this branch — and FR-26's nudges are irreversible.
> **Rule:** the storage service holds a `(size, mtime, content_hash)` watermark per Tier-1 file, re-validates before every append and every fold, and marks derived state stale on mismatch. External modification of a Tier-1 file is an expected event, not an error. A Tier-3 read whose watermark does not match rebuilds or refuses; it never silently returns stale state.

---

# Part 3 — Direct AD-versus-AD contradictions

Distinct from ambiguity: here a builder satisfying one AD **must** violate another.

| # | ADs in conflict | The contradiction |
|---|---|---|
| C1 | AD-3 (Tier-1 bounded by FR-37 compaction) vs AD-5 / AD-24 (append-only, never rotated) vs AD-3 (Tier-3 rebuilt from Tier 1 with zero loss) | Compaction requires a rewrite; append-only forbids it; and lossy compaction breaks the rebuild guarantee. See **P4**. |
| C2 | AD-33 ("time offset used **only for tracing**") vs AD-35 ("`occurred_at` governs… **ordering within a meeting**") | Ordering within a meeting is only derivable from the offset. See **P7**. |
| C3 | AD-3 ("Tier 2… **must be backed up**") vs Deployment & operations ("**Backup:** markdown scopes only… plus an exported keychain key") | The documented backup omits the tier AD-3 says must be backed up. See **P3**. |
| C4 | AD-3 (tiers have distinct durability promises) vs AD-6 / storage diagram (one `event_telemetry.db` holding Tier-1 telemetry, Tier-2 operational rows, and Tier-3 indexes) | The revision separated the tiers in prose and left them in one file. See **P3**. |
| C5 | AD-34 (four-segment grammar) vs its own example `meeting:mtg_01HX` and AD-33's `meeting:<id>` **plus speaker** | The document's own examples do not parse under its own grammar. See **P5**. |
| C6 | AD-1 class M ("one `event_log.md` entry per invocation") + AD-17 + AD-31.2 vs AD-4 (three scopes, each with its own `event_log.md`) | Three obligations written against a singular file that is actually a family of N. See **P13**. |

---

# Part 4 — Enforcement gaps

`tests/architecture/` is well above average — it is written before the code, it names the AD on every check, and its README is honest about what it cannot see. The gaps below are ones where **enforcement is mechanically possible today** and simply is not present. I have excluded genuine judgement calls (is *this* config project-specific?) and load-testing questions.

### 4a. Defects in the existing suite

1. **`auth.classify()` is called with two incompatible signatures.** `test_ad32_auto_execute_requires_all_three_conditions` calls `classify(source_authenticated=…, speaker_is_pm=…, verb=…)`; `test_ad32_manual_transcripts_never_auto_execute` calls `classify(source=…, speaker_is_pm=…, verb=…)`. One of the two must fail against any implementation. This is P11's vocabulary problem reproduced inside the enforcement layer.
2. **Two `SourceRef` types.** `pm_ai.domain.citations.SourceRef` (accepts `meeting:mtg_01HX/speaker:alex`, raises `NonDurableReferent`) versus `pm_ai.domain.identity.SourceRef` (accepts `meeting:mtg_01HX`, raises `MalformedSourceRef`). The suite encodes P5's divergence as a requirement.
3. **`test_ad33_ledger_entries_are_self_contained` can never pass** — `_sample_extraction()` unconditionally calls `pytest.skip()`. It will read as green-by-skip past the Phase-1 "zero skips" gate unless the fixture is built.
4. **`test_ad3_indexes_rebuild_from_markdown_without_loss` passes vacuously on an empty store**, and `snapshot_derived_state()` equality over a vector index is ill-defined across embedding-model or `sqlite-vec` changes — both of which the Stack section anticipates. It needs seeded fixture data and a defined snapshot normalization.
5. **`test_ad35_sweeper_will_not_declare_broken_without_coverage` supplies `coverage_gap=True` by hand.** A scheduler that never records coverage keeps this green. The producer side is untested (P9).
6. **`test_ad36_*` constructs `authored_by` by hand.** The attribution *mechanism* — the ledger-to-event match — is never exercised (P1, P10).
7. **`test_ad31_every_frontier_call_records_scope_provenance` asserts against `router.disclosure_log()`**, an in-process structure, while AD-31 requires the record in `event_log.md`. A build that writes nothing durable passes (P13).

### 4b. Stated in the spine, mechanically enforceable, currently unenforced

| AD / statement | Why enforceable now |
|---|---|
| **AD-1 class L** — "No model output may be interpolated into an argv" | Pure AST: in `models/local`, the argv list passed to `subprocess.*` must contain only literals and module-level allowlisted constants — no f-string, `.format`, `%`, or `+`. This is a **security property** with zero coverage today. |
| **AD-1 class M** — "one `event_log.md` entry per invocation" | Behavioural: `skills.invoke(...)` appends exactly one entry. |
| **AD-2** — "unpaired senders are rejected and logged" | Behavioural: feed the bridge an unpaired user id, assert rejection + log entry. Only `TRANSPORT` and the absence of `webhook_handler` are checked today. |
| **Stack** — `run_polling()` / `run_webhook()` prohibited | AST: forbidden call names in `surfaces/telegram`. Cheap, and the prerequisite is explicitly load-bearing for AD-19's single loop. |
| **AD-3** — "`pm-ai reindex` rebuilds Tier 3 only" | Behavioural and central to P3: populate the job queue, cursors, and executed-key ledger; run reindex; assert all survive. Nothing tests this, and it is the exact failure AD-3's revision names. |
| **AD-5** — "SQLite runs in WAL mode with the storage service as sole writer" | Behavioural: assert `PRAGMA journal_mode` is `wal` on every opened handle. |
| **AD-6** — "when off the daemon must emit a CLI banner and an `event_log.md` entry"; `vector_index/` at `0600` | Both trivially testable; neither is tested. |
| **AD-7** — "no feature may exist on only one surface" | Enforceable by construction: derive both the CLI command table and the Telegram command table from one core capability registry, and assert set equality. This is a *hard* rule with no check at all. |
| **AD-8** — token file at `0600` | Filesystem-mode assertion. |
| **AD-10** — per-project cursor isolation | The README defers this to an integration environment, but the *shape* is unit-testable with a fake store: two instances of one connector type in two scopes never share a cursor row. |
| **AD-12** — every inbound payload sanitized | Enforceable by **type**, not review: make `ModelPort.complete` accept only a `SanitizedText` newtype, so an unsanitized string is a type error. The README classes this as a review catch; a taint type moves it to the compiler. |
| **AD-15** — "a call without a declared task class is a defect" | One assertion that `complete()` without `task_class` raises. |
| **AD-16** — "tool set is exactly the MCP skills the registry authorized for that flow" | Behavioural: assert the tool list handed to `tool_runner` equals `registry.authorized_for(flow)`. `.importlinter` only blocks named libraries. |
| **AD-18** — registry refuses unlisted skills and out-of-scope calls, and logs the violation | The *contents* of the allowlist are a human call; the *refusal behaviour* is three assertions and has none. |
| **AD-19** — Ollama server-side residency (`OLLAMA_MAX_LOADED_MODELS=1`, `NUM_PARALLEL=1`, `keep_alive: 0` before dispatch) | Not a load test: assert the daemon sets them, and assert the pool never has two heavy jobs in flight with a fake job. The README dismisses all of AD-19 as load-testing; two thirds of it is not. |
| **AD-20** — "FR-04's offline buffer is not a separate mechanism; it is the same queue in `PENDING_RETRY`" | Assert the offline path enqueues into the same table; assert no second buffer module exists. |
| **AD-26 / Stack** — `hasattr(conn, "enable_load_extension")` | The spine calls this the difference between a working install and total storage failure on a clean machine, and specifies the `pm-ai doctor` probe. No test asserts the probe exists or fails correctly. Also mechanically checkable from the lockfile: `sqlcipher3` not `sqlcipher3-binary`, `sqlite-vec==0.1.9` exact, `python-telegram-bot` without `[job-queue]`. |
| **AD-27** — the **`event_log.md` entry-type** enumeration, and "both enumerations are versioned" | Only the `NormalizedEvent` side is tested. The entry-type enum and the version stamp have no coverage. |
| **AD-28** — the reverse direction | Tested one way (project ledger rejects a `CoachingCommitment`); the personal store accepting a project `Commitment` is untested. |
| **AD-29** — the *pipeline* stores raw unmodified | The sanitizer function is tested; that the ingestion path persists the raw payload untouched is not. |
| **AD-30** — "Core services receive their dependencies; they never construct or locate them" | AST: no module under `core/` instantiates an adapter class or calls a `get_*`/`load_*`/`build_*` locator. `.importlinter` catches the import, not the service-locator pattern. |
| **AD-34.3** — "Re-harvesting the same window must be idempotent" | The headline claim of AD-34 and the easiest test in the document: harvest a fixture window twice, assert one row. Absent. |
| **AD-37** — the per-`target_ref` serialization lock | Both AD-37 tests cover proposal CAS; the external-entity lock — the half that P1 breaks — has no test. |

---

# Part 5 — Consolidated remediation list

Ordered by the cost of getting it wrong.

| Pri | Change | Closes |
|---|---|---|
| 1 | **New AD-38** — three-phase claim ledger (`claimed/succeeded/failed`), mandatory existence probe, one external call per job | P2 |
| 2 | **Tighten AD-34.1** — one `SourceRef` type, four mandatory segments, `<scope>` = immutable project id, applies to `target_ref` and `external_ref` too | P1, P5, P10 |
| 3 | **Tighten AD-3** — tiers are physical partitions; `ops.db` / `derived.db` split; reindex refuses non-Tier-3 handles; backup covers Tier 2 | P3, C3, C4 |
| 4 | **New AD-39** — segmented ledgers; compaction seals by rename and never rewrites; rebuild guarantee scoped to retained segments | P4, C1 |
| 5 | **Tighten AD-36** — add `unknown` (inadmissible); one attribution function with fixed precedence; `service_identity` on `ConnectorInstance` | P10, P1 |
| 6 | **Tighten AD-35** — coverage attested by the connector in `occurred_at` space; `evidence_sources` per commitment type; `UNVERIFIED` as a first-class outcome | P9, S2 |
| 7 | **Bind AD-32 to AD-18** — skill-level reversibility, mandatory registered inverse, verb-schema payload rejection | P11 |
| 8 | **Tighten AD-13** — approval hash over payload + card, `edited` terminal, schema version stored, deterministic renderer | P12 |
| 9 | **Extend AD-33** — `Meeting` natural key, `bind_or_mint` sole writer, alias records for late binding | P8 |
| 10 | **Extend AD-25** — per-scope index files; retrieval takes a `scope_set` derived from the destination | P6 |
| 11 | **Reconcile AD-35 / AD-33** — offset is the ordering basis; no null `occurred_at` in a fold; deterministic `entry_id` for meeting-derived facts | P7, C2 |
| 12 | **Tighten AD-24** — scoped `event_log.md` family plus one app-level `egress_log.md` for the AD-31 audit and AD-17 totals | P13, C6 |
| 13 | Secondary rules for S1–S6 (alias resolution, cursor versioning, dispatch by task class, sanitizer versioning, pool-owned residency, Tier-1 watermarks) | S1–S6 |
| 14 | Fix the two suite defects (`classify` signature, dual `SourceRef`) and add the Part-4b checks, prioritizing AD-1 class L argv, AD-3 reindex-vs-Tier-2, AD-34.3 re-harvest idempotency, and AD-37's target lock | Part 4 |

---

## One closing observation

The spine's README already states the pattern precisely: *"wherever two components share a **word** rather than a **type**, there is probably a contract that isn't written down yet."* Eleven of the thirteen primary pairs above are instances of exactly that, and the four highest-severity ones (`target_ref`, `<scope>`, `event_log.md`, `authored_by`) are words that appear inside ADs written to close earlier word-versus-type holes. The lesson is not that AD-31..AD-37 were wrong; it is that **each new AD introduces new vocabulary, and the new vocabulary needs the same treatment as the old.** The mechanical version of that lesson: no AD should introduce an identifier, a field name, or a state name that is not simultaneously given a type in `pm_ai.domain` and a check in `tests/architecture/`. That rule, applied to this document, would have caught most of what is above before a reviewer had to.
