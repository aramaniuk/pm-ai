# Adversarial Divergence Review — ARCHITECTURE-SPINE.md (pm-ai)

**Reviewer stance:** adversary with no prior project context.
**Target:** `_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md`
**Also read:** `tests/architecture/{README.md,conftest.py,test_static_rules.py,test_domain_invariants.py,test_layering.py}`, `.importlinter`, `docs/prd_pm_ai.md`
**Date:** 2026-08-18

---

## Verdict

The spine is unusually strong on **mechanism** — egress, layering, writer ownership, scheduling ownership, model routing — and unusually weak on **meaning**. Every AD pins *shape*; almost none pin *vocabulary, ownership of a value's lifetime, or the order of two facts*. That is the predictable leak in a hexagonal design, and the spine's own test README half-admits it ("wherever two components share a **word** rather than a **type**, there is probably a contract that isn't written down yet") — then stops after five holes.

I found **17** pairs of units that each obey every written AD to the letter and would still build incompatibly. Seven of them (D-1, D-2, D-3, D-8, D-12, D-13, D-14) produce *silent wrong answers in the product's core promise* — commitment verification and the career dossier — rather than crashes. One (D-9) is a literal internal contradiction between AD-1's text and the spine's own container diagram.

The most dangerous single finding is **D-14 (self-authored telemetry echo)**: today, nothing stops pm-ai from writing a comment to a Work Item, harvesting that comment four hours later as "execution telemetry," and using it to mark the commitment it created as `FULFILLED`. The closed loop closes on itself.

---

## Method

For each hole I construct two units *one level down from the spine* — two features, two connectors, two stories, or two developers reading the same ADs on different Tuesdays. Both are fully compliant. The gap between them is the missing AD.

I did **not** count as a hole: anything the spine explicitly defers (signing, Linux, cost caps, local-model selection, multi-user, real-time, vector encryption), or anything where the spine names a single owner and the only question is implementation quality.

New ADs are numbered AD-30 onward. Tightenings are given as replacement Rule text for the existing AD.

---

# Part 1 — Divergence pairs

## D-1 — `source_ref` is a word, not a type

> **Severity: critical.** Breaks the product's headline capability (closed-loop commitment verification) silently and asymmetrically per connector.

**Unit A — GitLab connector (`pm_ai/connectors/gitlab.py`), Story "harvest MR merges."**
Emits `NormalizedEvent(type=MR_MERGED, source_ref="https://gitlab.acme.com/alpha/-/merge_requests/42")` — the canonical browser URL, so a citation surfaced to the PM is clickable.
*Obeys:* AD-9 (one `harvest` method, no scheduling, opaque cursor), AD-12 (payload sanitized outside the connector), AD-27 (`MR_MERGED` is a core enum member), Consistency › Event envelope (all seven fields present; the convention literally offers "URL / commit SHA / ticket anchor" as alternatives), Consistency › Citations.

**Unit B — Jira connector (`pm_ai/connectors/jira.py`), Story "harvest issue transitions."**
Emits `NormalizedEvent(type=WORK_ITEM_CLOSED, source_ref="PROJ-118")` — the native issue key, because that is what a PM says out loud and what the transcript extractor produces.
*Obeys:* exactly the same list. The envelope convention offers "ticket anchor" as a legitimate `source_ref` form.

**The incompatibility.**
`source_ref` is specified as a *set of examples*, not a type with a grammar. Both units satisfy the envelope; neither can be joined against the other. The core has three separate consumers that must compare a `source_ref` to something:

1. FR-33/FR-34 commitment verification — join `Commitment.target_work_item` (a spoken anchor from FR-01, e.g. `"WI-226"`) against evidence `NormalizedEvent.source_ref`.
2. FR-01 anchor matching — fuzzy-match a spoken token against "Work Item IDs in active memory."
3. The citation resolver — dereference `source_ref` back to the raw payload (AD-29) to render evidence.

A `str` equality or `LIKE` join between `"https://gitlab.acme.com/alpha/-/merge_requests/42"` and `"WI-226"` is not merely lossy — it is *unwritable*. So a developer writes a per-provider regex in the core, which is precisely the coupling AD-9 forbade for cursors and forgot to forbid for refs. Add AD-10 (per-project instances) and it gets worse: two registered projects can both have a `WI-226`.

**The production failure.**
Commitments whose evidence arrives from GitLab flip to `FULFILLED`. Commitments whose evidence arrives from Jira never match, sit `PENDING` past their due date, and flip to `BROKEN` by timeout (AD-14). The pre-meeting dashboard (FR-33) shows a team member under `## Broken Commitments` for work they finished and closed. The PM walks into a 1:1 with a fabricated accountability failure. Nothing errors; the join simply never matches, which is indistinguishable from "no evidence yet."

**Close it — new AD-30.**

> ### AD-30 — External entities are identified by a typed `EntityRef`, never by a string
>
> - **Binds:** every connector, every skill, `core/commitments`, `core/extraction`, citations, idempotency keys
> - **Prevents:** two connectors identifying the same real-world entity in shapes that cannot be joined, so commitment verification silently misses evidence from one of them
> - **Rule:** The core defines `EntityRef(provider, entity_type, native_id)` where `provider` and `entity_type` are **closed core enumerations** (AD-27 applies to both). Every `NormalizedEvent.source_ref`, every `Commitment.target_work_item`, every skill mutation target, and every citation target is an `EntityRef` — never a string, never a URL. A human-facing URL travels in a separate optional `display_url` field and is never compared, parsed, or joined. Each connector registers a bidirectional `EntityRef ↔ provider identifier` mapper; the core never parses a provider identifier. Spoken anchors (FR-01) resolve to `EntityRef` inside the anchor matcher before leaving `core/extraction`; an unresolved anchor is `UNMATCHED_ANCHOR` and never a bare string flowing downstream. `EntityRef` equality is structural, and an `EntityRef` without a project/personal scope binding may not be constructed.

---

## D-2 — `actor` is a word, not an identity

> **Severity: critical.** Feeds directly into performance reviews.

**Unit A — Story "GitLab commit telemetry" (developer 1).**
Sets `actor` from the commit author: `"a.ramaniuk@acme.com"` for some commits, `"aramaniuk"` for others (GitLab returns both depending on endpoint), `"Andrei R"` where only the git trailer exists.
*Obeys:* AD-9, AD-12, AD-27, envelope convention (`actor` is required, its type unspecified).

**Unit B — Story "transcript speaker attribution" (developer 2, three weeks later).**
The `TranscriptSourcePort` manual adapter parses a `.vtt` and sets `actor` to the VTT speaker label — `"Andrei Ramaniuk"`, or from a Teams transcript, `"Andrei Ramaniuk (Guest)"`, or the AAD object id `"8f2c…"` when Graph is the source.
*Obeys:* AD-9, AD-12, AD-23 (fallback adapter is first-class), AD-27, envelope convention.

**The incompatibility.**
Both write a `str` into the same field. The core then does three things that require *identity*, not a label:

- FR-30 aggregates custom metrics **per team member** over a quarter.
- FR-33 asks "did the person who promised this actually merge it?" — joining a transcript speaker to a git author.
- FR-31 embeds those aggregates in a career dossier synced back to an HR platform.

Nothing in the spine says who owns the mapping from provider handle to person, or that one exists. AD-27 closes the *event type* taxonomy and leaves the *actor* taxonomy wide open — and actor is exactly as cross-connector as type.

**The production failure.**
Alex appears in the metrics store as four people (`alex.k@acme.com`, `akowalski`, `Alex Kowalski`, `aad:9c1f…`). Each has 25% of his real activity. FR-30's "issues found per MR review" reads as a collapse in review quality. The FR-31 dossier — which the spine routes through an HR MCP skill to Lattice — embeds it. Worse is the symmetric failure: a developer "fixes" this with display-name coalescing, and two people named Alex merge into one record, which is a data-protection incident in a system whose entire pitch is sovereignty.

**Close it — new AD-31.**

> ### AD-31 — Actor identity is resolved by the core; adapters emit provider-native references only
>
> - **Binds:** every connector, `TranscriptSourcePort` adapters, FR-30, FR-31, FR-33, FR-34
> - **Prevents:** the same human appearing as four people in metrics and dossiers, or two humans silently merging on a display name
> - **Rule:** Adapters emit `ActorRef(provider, native_id, display_name)` and never a bare string. `native_id` must be the provider's **stable** identifier (never a display name, never an email that the provider allows to change). The core owns a `Person` registry: `person_id` is a prefixed ULID (`psn_`), and `ActorRef → person_id` links are created only by explicit registration or by an operator-approved Proposal (AD-13) — **never inferred from display-name similarity**. Every aggregation, dossier, metric, and commitment verification joins on `person_id`. An `ActorRef` with no link resolves to `person_id = None`, is excluded from every aggregate rather than being guessed, and surfaces in a reconciliation queue. Storing an unresolved `ActorRef` in place of a `person_id` in any derived table is a defect.

---

## D-3 — Two clocks, one deadline: `occurred_at` vs `ingested_at`

> **Severity: critical.** Produces wrong accountability verdicts and unrecallable outbound messages.

**Unit A — Story "commitment fulfilment verifier" (`core/commitments`).**
Compares the merge event's `occurred_at` against `Commitment.due_date`: "FULFILLED when telemetry confirms the deliverable **on or before** the due date" (FR-34.2, verbatim).
*Obeys:* AD-14 (domain state machine driven by execution telemetry), AD-3, AD-5, envelope convention (both timestamps present).

**Unit B — Story "overdue sweep + FR-26 pre-meeting inquiry" (scheduler).**
Runs nightly. Anything `PENDING` whose `due_date` is in the past with no evidence **ingested** yet becomes `BROKEN`, and — where the item is on tomorrow's agenda — dispatches an FR-26 clarification DM to the owner via an MCP skill. It uses `ingested_at` because AD-9 says so in the only sentence in the spine about cross-source ordering: *"cross-connector ordering uses the envelope's `ingested_at` watermark, never cursor contents."*
*Obeys:* AD-9, AD-13, AD-14, AD-20, AD-21, AD-1.

**The incompatibility.**
AD-9 pins `ingested_at` for *ordering between connectors*. It says nothing about which clock answers *domain* questions. Both readings are defensible and they disagree by up to one harvest interval (4h, FR-02) — or by days when a connector was rate-limited, token-expired, or the laptop was closed (AD-20's `PENDING_RETRY`). There is also no AD constraining `occurred_at` at all: it is provider-reported, subject to provider clock skew, and for the manual `.vtt` adapter it is *derived* from a filename or a meeting start time with no timezone guarantee beyond "ISO-8601 with explicit offset" — a convention that says how to *write* a time, not who is entitled to *assert* one.

The deeper hole: **no AD makes any telemetry-driven transition retroactively recomputable.** AD-5 makes the ledger append-only, so a `BROKEN` entry is permanent and visible; late evidence appends a `FULFILLED` entry after it, and (see D-4) two readers disagree about which wins.

**The production failure.**
The Mac is asleep over a long weekend. Monday 06:00 the sweep runs before the first harvest cycle completes. Three commitments delivered on Friday are marked `BROKEN`. FR-26 fires automated Teams DMs to three engineers asking why their committed work is not done — outbound, irreversible, and wrong. Ninety minutes later the GitLab harvest lands with Friday `occurred_at` values and the verifier flips all three to `FULFILLED`. The PM's credibility, and the product's, is spent.

**Close it — new AD-32.**

> ### AD-32 — Domain time is `occurred_at`; operational time is `ingested_at`; the two never mix in one predicate
>
> - **Binds:** `core/commitments`, scheduler sweeps, FR-26, FR-33, FR-34, FR-37, AD-9, AD-14
> - **Prevents:** a commitment being judged broken because the laptop was closed, and irreversible outbound messages sent on that judgement
> - **Rule:** `occurred_at` is the **only** clock for domain decisions (due dates, fulfilment windows, "did A happen before B" in the world). `ingested_at` is the **only** clock for operational windows (harvest watermarks, dedup, retry, pruning age, retention). A predicate that reads both is a defect. `occurred_at` is provider-asserted and may arrive arbitrarily late, therefore: **(a)** every telemetry-driven state transition must be a pure fold over all evidence, recomputable from scratch, never an incremental one-way flip; **(b)** a `BROKEN` transition is *provisional* until every connector instance in scope has an `ingested_at` watermark past `due_date + grace` (default 24 h) — before that the state renders as `AWAITING_EVIDENCE`; **(c)** no irreversible external action (FR-26 inquiry, HR sync, comment post) may be triggered by a provisional state. A connector whose provider does not supply a trustworthy event time must declare `occurred_at_is_estimated = true`, and estimated times never satisfy an on-or-before-deadline test on their own.

---

## D-4 — Append-only without an order: two ledger folds, two answers

> **Severity: critical.** Falsifies AD-3, the spine's sovereignty property, while its test stays green.

**Unit A — Story "commitment ledger reader" (`core/commitments`).**
Reads `commitments_log.md` top to bottom, folds entries by id, **last block in file order wins**.
*Obeys:* AD-3, AD-5 ("a status change is a new entry keyed by id, never an in-place edit"), Consistency › Markdown ledger entries.

**Unit B — Story "`pm-ai reindex`" (`storage/reindex.py`).**
Parses every entry, sorts by the header `timestamp` field, folds by id, **latest timestamp wins** — reasonable, because reindex parses several files and merges them and needs a total order.
*Obeys:* AD-3, AD-5, the same convention.

**The incompatibility.**
AD-5 mandates append-only and "a new entry keyed by id" but never says **what orders entries**. The convention line — *"a machine-readable header line (id, timestamp, type)… parsers must tolerate hand-edits"* — actively creates the divergence: the spine *invites* hand-editing (AD-6 keeps markdown plaintext precisely so the PM can edit it) while offering two candidate order keys that hand-editing desynchronises. A PM who appends a corrected block at the bottom of the file, or pastes a block back after a git merge, or whose daemon wrote entries during a DST shift or a clock correction, produces a file where A and B compute different terminal states.

There is a second, sharper edge: `.project-ai/` is **git-committed** (AD-4, Deployment › Backup). Git merges of two append-only ledgers interleave blocks by hunk position, not by timestamp — so file order and timestamp order routinely diverge on any machine that has ever pulled.

**The production failure.**
`pm-ai reindex` — the command AD-3 promises is lossless — changes answers. A commitment reads `FULFILLED` in the dashboard before reindex and `BROKEN` after. The spine's *"any state that cannot be reconstructed from markdown is a defect"* is violated in the direction nobody checks: the state *is* reconstructed, just differently. `test_ad3_indexes_rebuild_from_markdown_without_loss` passes throughout, because it snapshots and rebuilds using the same fold on a corpus that never had an out-of-order entry.

**Close it — new AD-33.**

> ### AD-33 — One fold, one order key, one implementation
>
> - **Binds:** `storage/`, `core/commitments`, `core/proposals`, reindex, AD-3, AD-5
> - **Prevents:** the live reader and the rebuilder computing different terminal states from the same file
> - **Rule:** Every markdown ledger entry header carries a `seq` — a per-file, gap-free, monotonically increasing integer assigned by the storage service at append time. **`seq` is the sole ordering key.** `timestamp` is display and domain metadata and is never an ordering key. Exactly one core function, `fold_ledger(entries)`, reduces a ledger to current state; the live reader, the reindexer, the CLI, and every surface call it — a second fold implementation anywhere is a defect. Duplicate or non-monotonic `seq` values, or an entry whose `id` has no prior creating entry, are a **hard parse failure** that aborts the read with a repair instruction — never a silently-chosen winner. Hand-edits are supported by `pm-ai ledger repair`, which renumbers and re-validates; hand-appended blocks without a `seq` are quarantined, not guessed at.

---

## D-5 — What does `[Edit]` mean?

> **Severity: high.** Causes an unintended external mutation from a gesture the user believes is safe.

**Unit A — Story "FR-06 implicit Work Item update card."**
`[Edit]` opens an inline editor, the PM changes the assignee, the payload is updated and the proposal moves `staged → edited`, and the executor runs — because in this developer's reading, editing *is* the act of approval: you looked at it, you changed it, you meant it.
*Obeys:* AD-13 verbatim — `edited` is a listed status in `staged → approved → executed | edited | rejected | expired`, and an external mutation is happening under a Proposal.

**Unit B — Story "FR-31 post-1:1 HR goal sync card."**
`[Edit]` moves the proposal to `edited` (terminal, no execution) and raises a **new** staged proposal with the revised payload, requiring a second `[Approve]` — because FR-31 says goals "remain in STAGED_APPROVAL until Andrei explicitly approves them."
*Obeys:* AD-13 verbatim. Same status list, different topology.

**The incompatibility.**
AD-13 gives a status *set* and calls it a lifecycle, but never draws the edges. Is `edited` terminal or a return to `staged`? Does it execute? The arrow notation `staged → approved → executed | edited | rejected | expired` reads most naturally as *all four being successors of `approved`* — which would make `edited` a post-approval state, a third reading neither developer took. And AD-13's own guard ("no external mutation derived from implicit extraction may execute without an approved Proposal") is satisfied by A only if you accept that editing implies approval, which is exactly the disputed question.

The spine mandates **one card renderer for both surfaces** — so the *same button on the same widget* behaves differently depending on which feature registered the proposal type. This is the worst possible place for a semantic difference: the user cannot see it.

**The production failure.**
The PM has learned on FR-31 cards that `[Edit]` is safe — it stages a revision for a second look. On a Friday evening FR-06 card he taps `[Edit]` to fix a typo in a comment body, and the comment posts to the customer-visible GitLab Work Item immediately, typo fix and all, with the wrong assignee he had not got to yet. AD-20's idempotency key then makes the mistake *permanent and unrepeatable* — a corrected re-post derives a different key and posts a second comment.

**Close it — tighten AD-13.**

> **Replacement Rule text for AD-13 (additions in bold):** … status (`staged → approved → executed`, with `edited | rejected | expired` as **terminal, non-executing** states). **The state graph is closed and defined in the core: `staged` may go to `approved`, `edited`, `rejected`, or `expired`; `approved` may go only to `executed` or `failed`; all other transitions are rejected. `edited` never executes: editing a staged proposal produces a NEW proposal in `staged` with a fresh `prp_` id and a `supersedes` link to the edited one, and the new proposal requires its own explicit approval on either surface.** One card renderer serves both surfaces, **and the renderer — not the feature — owns the meaning of every control; a registered proposal type may supply payload, labels, and TTL, and may not supply or override a transition.** …

---

## D-6 — Two approvals, one proposal: the transition is not atomic

> **Severity: high.**

**Unit A — Telegram approval handler.**
`if p.status == "staged": queue.enqueue(job); storage.append(p, status="approved")`. Enqueue first so a crash between the two leaves work that will run — fail-forward.
*Obeys:* AD-13, AD-20 (the job is a durable row with an idempotency key), AD-5 (the status change is an append), AD-7 (identical functionality across surfaces).

**Unit B — CLI approval-queue handler.**
`storage.append(p, status="approved"); queue.enqueue(job)`. Transition first so a crash leaves an approved-but-unexecuted proposal the sweeper can retry — fail-safe.
*Obeys:* the identical list.

**The incompatibility.**
Nothing in the spine says the `staged → approved` transition is a compare-and-set, and nothing says the enqueue is in the same transaction as the transition. AD-5 gives us a single *writer*, which prevents torn writes — but a single writer serving two callers still happily accepts two sequential "append approved for prp_X" requests. The single asyncio loop (AD-19) does not help: both handlers `await` the storage service, and the interleaving `A: read staged → B: read staged → A: enqueue → B: enqueue` is ordinary.

The realistic trigger is not exotic: Telegram inline buttons do not disable themselves; a double-tap on a phone, or a tap plus a CLI `pm-ai approve prp_…` from the terminal, is a Tuesday. AD-7 *requires* both surfaces to offer this, which guarantees two entry points to the same transition.

**The production failure.**
Two jobs. If both derive the same idempotency key, D-8's ambiguity decides whether they collapse — and for FR-31's HR sync, where the payload contains an LLM-generated goal restatement, they will not. Two performance goals appear in Lattice. The ledger contains two `approved` entries for one `prp_` id, which under D-4 is either "fine" or "hard failure" depending on which fold you got.

**Close it — new AD-34.**

> ### AD-34 — Lifecycle transitions are compare-and-set, and side effects are enlisted in the transition
>
> - **Binds:** `core/proposals`, `core/commitments`, job queue, both surfaces
> - **Prevents:** the same approval being accepted twice from two surfaces, or a job existing for a proposal that was never approved
> - **Rule:** Every state transition on a `Proposal`, `Commitment`, or `Job` goes through `storage.transition(entity_id, expected_from, to)`, which fails with `StaleTransition` when the current state is not `expected_from`. Surfaces render `StaleTransition` as "already handled" and never retry it. Job enqueue, and every other side effect of a transition, happens **inside the same storage transaction as the transition** — no component enqueues before transitioning, and no component transitions without enqueuing atomically. A surface control that has fired is disabled or removed in the rendered card before the transition returns.

---

## D-7 — The proposal expired; the job did not

> **Severity: high.** Directly violates AD-13's own guarantee.

**Unit A — Story "expiry sweep" (scheduler, per AD-13).**
Every hour, `staged` proposals older than TTL move to `expired`. Nothing else.
*Obeys:* AD-13 ("expiry is owned by the scheduler, not by features"; "an expired proposal never executes"), AD-20.

**Unit B — Story "approved-proposal executor" (job queue worker).**
Dequeues `job_…`, calls the registered executor callback with the frozen payload, retries with backoff while the target is unreachable, holding the row in `PENDING_RETRY`.
*Obeys:* AD-20 (durable row, at-least-once, mandatory idempotency key), AD-1, AD-18, AD-21, Consistency › Retries.

**The incompatibility.**
Both readings of "expiry" are compliant, and they are incompatible:
- Does TTL run from `staged` (so approval stops the clock), or from creation (so an approved-but-stuck job can outlive its proposal)? AD-13 does not say.
- Does the executor re-check proposal status at dispatch? AD-13 says "an expired proposal never executes" — but the *job* is not the proposal, and the ER diagram's `JOB ||--o| PROPOSAL : may_produce` points the wrong way to help. A developer reading AD-20 sees an unconditional at-least-once delivery guarantee; a developer reading AD-13 sees a status gate. Nothing reconciles them.

Meanwhile FR-04's offline buffer is explicitly the same queue (AD-20), and NFR-10 promises replay "upon reconnection" with no upper bound. A job can legitimately sit for longer than a 7-day TTL — a closed laptop plus an expired GitLab token is enough.

**The production failure.**
The PM approves "post the agreed scope change to WI-108" on the 1st. The token is expired; the job retries for eleven days. On the 3rd the scope changes in a different meeting; the PM assumes the stale card lapsed. On the 12th the token is refreshed, the job replays, and pm-ai posts an eleven-day-old, now-wrong scope statement to a Work Item the whole team reads. AD-13's headline sentence — *an expired proposal never executes* — was never false in any single component.

**Close it — new AD-35.**

> ### AD-35 — Expiry governs the approval window only; jobs re-check authorization at dispatch and have their own maximum age
>
> - **Binds:** scheduler, job queue, `core/proposals`, AD-13, AD-20, FR-04
> - **Prevents:** an eleven-day-old approved action firing after the world moved on, and an "expired" proposal executing anyway via its job
> - **Rule:** Proposal TTL runs from creation and applies **only** to the `staged` state; the `staged → approved` transition stops the clock and an `approved` proposal is never expired by the sweeper. Every job created on behalf of a Proposal carries `proposal_id`, and re-reads the proposal's status **transactionally at dispatch**: it executes only when the status is `approved`, and otherwise terminates as `abandoned` with an `event_log.md` entry. Every job carries `max_age` (default 14 days from enqueue, overridable per job type, never unbounded); on expiry it terminates as `abandoned` and notifies the surface that requested it — a job is never silently deleted and never runs past `max_age`. Job types that mutate an external system must declare whether they are **time-sensitive**; a time-sensitive job older than its `staleness_window` (default 24 h) requires re-confirmation via a new Proposal rather than executing.

---

## D-8 — The idempotency key formula has three undefined terms

> **Severity: critical.** The spine's own test README calls this "the one to keep if you ever keep only one" — and the formula it protects is under-specified in all three inputs.

`sha256(job_type + target_ref + canonical_payload)` — AD-20 and Consistency › Idempotency keys.

**Unit A — Story "FR-07 post research findings to a Work Item."**
`job_type="post_comment"`, `target_ref="WI-102"` (the spoken anchor, which is what the transcript gave), `canonical_payload=json.dumps(payload, sort_keys=True)`.
*Obeys:* AD-20 verbatim — deterministic, never random.

**Unit B — Story "FR-06 approved comment on an implicit update."**
`job_type="post_comment"`, `target_ref="gitlab:alpha:WI-102"`, and `canonical_payload` includes the LLM-synthesized comment body plus a `generated_at` field, serialized with `repr()`.
*Obeys:* AD-20 verbatim — also deterministic per derivation, also never random.

**The incompatibility — three separate failure modes, all compliant.**

1. **`target_ref` has no scope.** A's key for project Alpha's `WI-102` is identical to project Beta's `WI-102` (AD-10 makes multiple projects the norm; AD-30 above is not yet written). Whichever posts first claims the key; the second is suppressed as a duplicate and **a comment silently never posts**.
2. **The payload is not stable across attempts.** B's body is regenerated after a daemon restart because the payload was not frozen — a different model sample, or merely a new `generated_at`, yields a different key. The replay is not recognised as a replay and **double-posts**. AD-20's determinism requirement is satisfied *within* one derivation and defeated *across* attempts, which is the only place it matters.
3. **"Canonical" is undefined.** `json.dumps(sort_keys=True)` vs `repr()` vs `str()` differ on floats, unicode escaping, `None`/`null`, and dict insertion order — so two job types that mutate the same entity with the same logical payload derive different keys.

And a fourth, structural: **the spine never says what the key is checked against.** GitLab, Jira, Teams, and Lattice have no idempotency header. The key is only meaningful against a *local* claim table, and nothing says whether the claim is recorded before or after egress. Claim-after-egress double-posts on a crash mid-flight; claim-before-egress-without-release drops a mutation on a transient failure. Both readings comply.

**The production failure.**
Duplicate comments and duplicate HR goals on some paths; a silently-dropped mutation on others; and — because both look correct in review — no way to tell which until a stakeholder asks why pm-ai posted the same research summary twice.

**Close it — tighten AD-20 into new AD-36.**

> ### AD-36 — Idempotency keys are scoped, frozen, canonical, and claimed before egress
>
> - **Binds:** job queue, skill layer, AD-20, FR-04, NFR-10
> - **Prevents:** cross-project key collisions dropping a mutation, and regenerated payloads double-posting on replay
> - **Rule:** The key is `sha256(job_type ‖ scope_id ‖ entity_ref.canonical() ‖ canonical_json(idempotent_payload))`, where:
>   - `scope_id` is mandatory — a key without a scope may not be constructed;
>   - `entity_ref` is an `EntityRef` (AD-30), rendered by its single canonical serializer;
>   - `canonical_json` is RFC 8785 JSON Canonicalization Scheme, one implementation in `core/jobs`;
>   - `idempotent_payload` is the payload projected onto an explicit `IDEMPOTENT_FIELDS` allowlist declared by the job type, which **must exclude** timestamps, model output ids, nonces, and any non-deterministic field.
>
>   The payload is **frozen at enqueue** and persisted with the job row; retries derive the key from the persisted payload and never regenerate content. The storage service holds a UNIQUE index on `(scope_id, idempotency_key)`. The skill layer performs **claim → execute → record**: it claims the key in a committed transaction before egress, marks the outcome after, and a claim in `in_flight` state older than the job's timeout resolves to `unknown` — which requires a **read-back verification** against the provider before any retry, never a blind re-send.

---

## D-9 — Who is allowed to read GitLab? AD-1 and the container diagram disagree

> **Severity: high.** A literal internal contradiction, and it splits the audit trail in half.

**Unit A — Story "GitLab connector."**
`pm_ai/connectors/gitlab.py` imports `httpx`, authenticates with the token from `config.json`, and pulls MRs and commits directly.
*Obeys:* AD-9 (connectors do "auth, fetch, and map-to-schema" — auth and fetch are *named* as connector responsibilities), AD-12, `.importlinter:http-confined-to-adapters` (which explicitly permits HTTP in `connectors`), and the spine's own container diagram, which draws `CONN --> EXT`.

**Unit B — Story "Jira connector."**
`pm_ai/connectors/jira.py` holds no HTTP client; it calls `skills/read_jira_issues.py` through the registry, because AD-1 says *"100% of external reads and writes route through the MCP skill layer"* and *"Every skill invocation appends an entry to `event_log.md`."*
*Obeys:* AD-1 verbatim, AD-18 (the read is scope-checked by the registry), AD-9 (still one `harvest` method).

**The incompatibility.**
AD-1 says 100% of external **reads** go through skills. AD-9 says connectors do the fetching. The container diagram draws *both* `CONN --> EXT` and `REG --> EXT`. There is no sentence anywhere that resolves this, so the two developers pick differently — and the consequences are not cosmetic:

| | Unit A (connector fetches) | Unit B (connector via skill) |
|---|---|---|
| Appears in `event_log.md` | No — AD-1's logging obligation attaches to *skill invocations* | Yes |
| Scope-checked by the registry (AD-18) | No | Yes |
| Credentials sourced from | connector config | skill registry declaration |
| Rate limiting owned by | scheduler (AD-9) | split between scheduler and registry |

So the security property "every external touch is logged and authorized" is **true for half the system and false for the other half**, and which half depends on who wrote the connector.

**The production failure.**
The audit trail records every GitLab *write* and no GitLab *reads*. When the PM later asks the question this product exists to answer — "what did pm-ai see, and when?" — the answer is unreconstructable for connector-fetched sources. The scope allowlist (AD-18), sold as the containment mechanism, does not constrain what data the system ingests at all. A compromised or buggy connector exfiltrates or over-fetches with no registry record, while passing every architecture test in the repo.

**Close it — new AD-37 (and amend AD-1's wording).**

> ### AD-37 — Ingress and egress are separate, exclusive, and both registry-declared
>
> - **Binds:** AD-1, AD-9, AD-18, every connector, every skill
> - **Prevents:** half the external traffic escaping the audit trail and the scope allowlist because AD-1 and AD-9 describe the same boundary differently
> - **Rule:** **Connectors are the only inbound path; skills are the only outbound-mutation path, and neither may do the other's job.** A connector performs read-only requests (no POST/PUT/PATCH/DELETE, no state-changing GET); a skill performs no scheduled ingestion. Both are **egress points** in the security sense and are subject to identical obligations: each declares to the registry the providers, capabilities, and scopes it may exercise; the daemon refuses an undeclared or out-of-scope call and logs the violation; **every** external request — read or write — appends an `event_log.md` entry recording provider, capability, entity ref, scope, and outcome. AD-1 is restated as: *the connector layer and the skill layer are the only two components permitted to open a network connection to an external service, and both are registry-authorized and fully logged.*

---

## D-10 — `Scope` means three things, and "exactly three" is false

> **Severity: high.** Makes AD-4's and AD-28's guarantees unwritable as code.

**Unit A — Story "storage path resolution" (developer 1).**
`class Scope(StrEnum): APP; PERSONAL; PROJECT` — "exactly three scopes," per AD-4's first sentence.
*Obeys:* AD-4 verbatim, Consistency › Naming (`Scope` is a listed entity).

**Unit B — Story "connector instance registry" (developer 2).**
`Scope = AppScope | PersonalScope | ProjectScope(project_id)` — because AD-10 defines a connector instance as the tuple `(scope, connector_type, config, cursor)` and *"each registered project gets independently-scheduled harvesting,"* so `scope` must identify *which* project.
*Obeys:* AD-10 verbatim, AD-11, AD-4.

**The incompatibility.**
The spine uses "scope" for three unrelated concepts and one enumeration name:

1. **Storage domain** (AD-4): three directories.
2. **Instance ownership** (AD-10): personal vs *this specific project* — N values, not 3.
3. **Permission** (AD-18): *"each declaring the scopes it may exercise"* — an OAuth-style capability set, entirely unrelated to 1 and 2.

And AD-4's "exactly three" is arithmetically false against the spine's own storage diagram, which shows **five** roots: `~/.pm-ai/`, `~/.pm-ai/private/`, `~/.manager-ai/`, `~/.manager-ai-private/` (AD-25), and `<repo>/.project-ai/`. A developer who takes "exactly three" literally will conclude AD-25's `~/.manager-ai-private/` is a violation — or, worse, will put personal analytics under `~/.pm-ai/private/` "because that's the app scope," which is exactly the join AD-25 exists to make structurally impossible.

**The production failure.**
With Unit A's `Scope`, the storage service cannot route a project write when two projects are registered — either it takes a second, redundant `project_id` argument everywhere (and someone eventually passes the wrong one, writing project Beta's commitments into Alpha's git-committed repo), or path composition is done by string concatenation at call sites, which defeats AD-5's single-writer discipline and makes AD-28's `ScopeViolation` check unwritable: you cannot reject a personal entity in a project ledger if `Scope` cannot say which ledger. Unit B's connector instances key their cursors on a `Scope` that A's enum cannot represent, so two projects **share one GitLab cursor** — and each project's harvest advances past the other's unread events. Telemetry gaps, per project, silent.

**Close it — new AD-38 (and correct AD-4's count).**

> ### AD-38 — Three scope *kinds*, five storage *roots*, and permissions are called capabilities
>
> - **Binds:** AD-4, AD-10, AD-18, AD-25, AD-28, `storage/`, every connector
> - **Prevents:** one word carrying three meanings, per-project cursor collisions, and unwritable scope-violation checks
> - **Rule:** `Scope` is a closed sum type with three **kinds** and N instances: `AppScope | PersonalScope | ProjectScope(project_id)`. Every `ConnectorInstance`, `NormalizedEvent`, `Job`, `Proposal`, and ledger entry carries a `Scope`; a `ProjectScope` without a registered `project_id` cannot be constructed. Storage **roots** are a separate closed enumeration of five — `app`, `app_private`, `personal`, `personal_private`, `project(project_id)` — each declaring its encryption policy (AD-6), its git-committed status, and the closed set of entity types it accepts. The storage service resolves every path through `root_for(entity_type, scope)`; **no component composes a storage path from strings**, and no storage path literal appears outside `pm_ai/storage/paths.py`. AD-4's "exactly three" refers to scope kinds; the private roots are derived companions of the personal and app scopes and are not a fourth kind. What a skill may exercise (AD-18) is a **capability**, never a "scope"; the word `scope` is reserved for the sum type above.

---

## D-11 — Versioned enumerations with no migration contract

> **Severity: high.** Breaks AD-3 exactly where AD-3 matters most.

**Unit A — Story "taxonomy v3" (developer 1).**
Renames `NormalizedEventType.MR_UPDATED` to `WORK_ITEM_UPDATED` because GitLab and Jira turned out to mean the same thing (AD-27 explicitly says adding a type is reviewed *"against existing types for overlap"* — consolidation is the encouraged move). Bumps `TAXONOMY_VERSION` to 3 and writes a SQLite migration.
*Obeys:* AD-27 ("both enumerations are versioned so parsers can read historical entries" — she versioned them), AD-3 (SQLite is derived, so migrating it is optional anyway).

**Unit B — Story "`pm-ai reindex`" (developer 2).**
Parses `event_log.md` from the beginning of time and maps each entry's `type` string onto the **current** enum. An unknown member is skipped with a warning, because aborting a rebuild on one bad line would make reindex unusable.
*Obeys:* AD-3, AD-27, AD-33 above.

**The incompatibility.**
AD-27 says the enumerations are versioned. It does not say **where the version is written** (per-record or global), **whether members may be renamed or removed**, or **who supplies the upgrade function**. So A versions the *enum* and B versions *nothing per record* — and B has no way to know that an eight-month-old `mr_updated` line means today's `work_item_updated`.

Same hole for `Proposal.payload`: AD-13 lets each registered type declare a payload schema, and nothing versions it. A proposal staged before a daemon upgrade is approved after it, and the executor unpacks a payload shaped for the previous schema.

**The production failure.**
`pm-ai reindex` — the one command that is supposed to prove sovereignty — drops every pre-v3 event with a warning buried in the JSON diagnostic log (AD-24 sends it there, away from the audit trail). Commitment verification loses its evidence for everything older than the rename. The markdown files are intact and the system can no longer read them, which is a *worse* failure than data loss because the user's backup looks fine. `test_ad3_indexes_rebuild_from_markdown_without_loss` passes: its corpus was generated by the current version.

**Close it — new AD-39.**

> ### AD-39 — Every persisted record is version-stamped; enum members are immutable; reindex must parse all history
>
> - **Binds:** AD-3, AD-13, AD-27, storage, reindex
> - **Prevents:** a taxonomy change quietly making a year of markdown unreadable while the files sit there intact
> - **Rule:** Every persisted record — event envelope, `event_log.md` entry, ledger entry, proposal payload, job row — carries `schema_version`. Enum members are **never renamed and never removed**: retirement is `deprecated: true` plus a `superseded_by` mapping to a live member, and the retired name stays parseable forever. Every core enumeration and every proposal payload schema ships `migrate(record, from_version) -> record` covering **every version ever released**; a release that adds a version without its migration fails the build. `pm-ai reindex` must parse every historical version; a record it cannot parse **aborts the rebuild** with the offending file and `seq` — it is never skipped, never warned-and-dropped. A `staged` proposal whose payload `schema_version` predates the currently registered type is expired and restaged, never executed against a mismatched executor.

---

## D-12 — Opaque cursors across a hot plugin reload

> **Severity: high.** Fails in the direction nobody notices: silence.

**Unit A — Story "hot plugin loading" (FR-35.3).**
Reloads the connector module in place and keeps the persisted cursor — it is opaque bytes the scheduler "persists and replays verbatim" (AD-9), so replaying it is the only compliant behaviour.
*Obeys:* AD-9 verbatim, AD-10, FR-35.3 ("takes effect dynamically… without requiring a daemon restart").

**Unit B — Story "GitLab connector v2."**
Provider changed pagination; v2's cursor is `{"updated_after": "..."}` where v1's was a keyset token. The developer never touches the scheduler, because AD-9 forbids the scheduler from understanding cursors.
*Obeys:* AD-9, AD-27, AD-12.

**The incompatibility.**
AD-9's opacity rule is correct and creates a blind spot it does not cover: the scheduler **cannot detect a cursor-format change**, because detecting it requires interpreting the bytes. Nothing tells the connector its cursor is from a previous incarnation, and nothing tells the scheduler to reset. Compliance guarantees the failure.

Worse, FR-35.3 mandates hot reload with no drain contract. A `harvest()` from v1 can be in flight when v2 is swapped in; whose cursor is written back on completion? AD-19's single loop means the await point is real.

**The production failure.**
Two shapes, both bad. Either v2 treats the unparseable cursor as "no cursor" and re-harvests from epoch — a rate-limit storm, and (absent D-13's dedup key) thousands of duplicate events flooding the ledger and double-counting FR-30's metrics. Or v2 returns an empty list because the malformed cursor matches nothing, and **GitLab telemetry silently stops**. There is no error, no alert; `pm-ai doctor` probes *connectivity*, which is fine. Commitment verification degrades to "no evidence ever arrives," so every commitment eventually flips to `BROKEN`. The first symptom is the PM confronting his team about a quarter of imaginary broken promises.

**Close it — new AD-40.**

> ### AD-40 — Cursors carry a format version; the scheduler compares versions, never bytes; hot reload drains first
>
> - **Binds:** AD-9, AD-10, FR-35.3, scheduler
> - **Prevents:** a connector upgrade silently stopping telemetry, or silently re-harvesting from epoch
> - **Rule:** A `Cursor` is `(connector_type, cursor_format_version, opaque_bytes)`. Each connector declares `CURSOR_FORMAT_VERSION`; the scheduler compares **only the version integer** and never inspects the bytes. On mismatch the scheduler refuses to replay, resets the instance to a declared `bootstrap_window` (default 7 days), appends an `event_log.md` entry, and raises a user-visible notice on both surfaces. A connector instance that returns zero events for `N` consecutive cycles (default 6, ≈24 h at the 4 h cadence) raises a **staleness alarm** on both surfaces and in `pm-ai doctor` — silence is never treated as health. Hot reload is a scheduler operation: it quiesces the instance, drains any in-flight `harvest`, commits or discards that cursor atomically, then swaps the module. A module-level import side effect that changes harvesting behaviour is a defect.

---

## D-13 — Dedup on what?

> **Severity: critical.** AD-9 assigns dedup an owner and never names its key.

**Unit A — Story "ingestion dedup" (developer 1).**
Dedups on the envelope `id`. It is a `evt_` ULID, "never reused" per the identifier convention, so it is unique by construction.
*Obeys:* AD-9 ("dedup… happens outside the connector, uniformly"), Consistency › Identifiers.

**Unit B — Story "ingestion dedup" (developer 2, alternate timeline).**
Dedups on `(source, source_ref, occurred_at)`.
*Obeys:* the identical list.

**The incompatibility.**
A's key is minted by the *ingestion pipeline at ingest time* — so it is unique per *ingestion*, not per *real-world fact*. Replaying the same GitLab comment after a cursor reset (D-12), a `PENDING_RETRY` replay (AD-20's at-least-once, applied to harvest jobs), or an overlapping bootstrap window mints a fresh ULID and **dedups nothing at all**. A's implementation is a no-op that looks like a feature.

B's key is closer but wrong differently: `occurred_at` for an *edited* comment may not change, so an edit is swallowed as a duplicate; and for a provider whose timestamps have second granularity, two distinct comments in the same second collide. Neither developer is careless — the spine simply never says what identity means for an inbound fact.

**The production failure.**
The path from here to real harm is short and specific. A cursor reset re-harvests a week of MR review comments. With A's dedup, they all land again. FR-30's custom metric *"number of issues found per MR review"* — defined by the PM, aggregated over a quarter, and **embedded automatically into the team member's career dossier** (FR-31) — doubles. The dossier goes into a performance review. The number is wrong, it is wrong in the flattering direction for one person and not for another, and there is no way to notice because `event_log.md` is append-only and both copies are genuine.

**Close it — new AD-41.**

> ### AD-41 — Every inbound fact carries a connector-supplied `dedup_key`; the ULID is never one
>
> - **Binds:** AD-9, AD-27, every connector, ingestion pipeline, FR-30
> - **Prevents:** replayed harvests double-counting into metrics and career dossiers
> - **Rule:** Every `NormalizedEvent` carries `dedup_key = (EntityRef, provider_revision)` supplied by the connector, where `provider_revision` is the provider's stable version discriminator (edit timestamp, ETag, `updated_at`, revision number). The `evt_` ULID is a local surrogate key and **may never be used for deduplication**. The ingestion pipeline enforces `UNIQUE(scope, source, dedup_key)`: a repeat is an idempotent no-op on the ledger and an update-in-place of the derived row; a same-`EntityRef`-different-revision arrival supersedes the prior derived row while both ledger entries remain. A connector that cannot supply a stable `dedup_key` may not be registered — a synthesized content hash is acceptable only when the connector declares `dedup_is_content_hash = true`, and such events are excluded from all counting metrics.

---

## D-14 — The system verifies its own promises

> **Severity: critical.** The closed loop closes on itself. This is the single worst hole in the document.

**Unit A — Story "FR-06 approved commitment sync."**
On approval, the executor posts a comment to WI-108 — *"Alex to ship cache TTL fix by Friday (from Payment Gateway Sync, 2026-08-14)"* — and may transition the Work Item state.
*Obeys:* AD-1, AD-13, AD-18, AD-20, AD-14.

**Unit B — Story "FR-34 closed-loop verification."**
Harvests telemetry, finds evidence referencing WI-108 within the commitment window, and transitions the commitment `PENDING → FULFILLED` per FR-34.2 ("telemetry confirms matching deliverable… with verified commit SHA / ticket closure metadata").
*Obeys:* AD-9, AD-12, AD-14, AD-27, AD-32.

**The incompatibility.**
Unit A **writes to the exact external system Unit B reads as ground truth**, and no AD marks the system's own writes. The container diagram draws `SK --> EXT` and `CONN --> EXT` pointing at the same box and never closes the loop conceptually. So the GitLab connector harvests pm-ai's own comment, normalizes it into a `WORK_ITEM_COMMENTED` event referencing WI-108 with `occurred_at` inside the window, and the verifier — which has no concept of provenance — accepts it as execution telemetry.

The same blind spot covers FR-28's autonomous execution engine, which opens MRs and transitions Work Items under the `pm-ai:execute` label: pm-ai's own MR is harvested as an `MR_MERGED` event and can satisfy a commitment made by a human.

Layered on top is a plain **concurrency** hole: nothing serializes two mutating jobs against one `EntityRef`. An FR-06 approved update setting assignee=Alex/priority=High and an FR-28 autonomous transition to In Review run concurrently on the single loop (AD-19 bounds only *heavy local model* work, not I/O jobs) and interleave. AD-20 makes each individually idempotent and says nothing about the pair; providers resolve field conflicts last-write-wins.

**The production failure.**
Commitments auto-close on evidence pm-ai manufactured. The dashboard shows `## Met Commitments` full of promises nobody kept, the PM stops checking because it always looks green, and the product's central claim — telemetry-verified accountability — is not merely unreliable but *systematically biased toward false confidence*. It is undetectable from inside: the commit SHA in the verification reference is real, the ticket closure is real, and the ledger's append-only audit trail (FR-34.4, "verification hashes, event timestamps, evidence references") records all of it correctly.

**Close it — new AD-42.**

> ### AD-42 — The system's own writes are marked and are never evidence; mutations on one entity are serialized
>
> - **Binds:** AD-1, AD-9, AD-14, AD-19, AD-20, FR-28, FR-33, FR-34
> - **Prevents:** pm-ai verifying its own commitments with telemetry it produced, and two jobs racing on one external entity
> - **Rule:** **(a) Provenance.** Every external mutation is executed under a declared service identity and is recorded locally as an `EgressRecord(job_id, idempotency_key, EntityRef, provider, occurred_at)`. The ingestion pipeline marks every inbound event as `self_authored = true` when its `ActorRef` matches the service identity **or** it correlates to an `EgressRecord`. `self_authored` events are stored and citable but are **structurally excluded** from commitment verification, FR-30 metrics, FR-33 trend auditing, and any "did the team do the work" predicate — the exclusion lives in the query layer, not in a remembered filter.
> **(b) Serialization.** The job queue takes a per-`EntityRef` exclusive lease for the duration of a mutating job. A second mutating job on a leased entity waits; it does not proceed on a timeout. When two distinct approved Proposals would mutate overlapping fields of one entity, the second is **re-staged for confirmation** showing the first's effect, rather than overwriting it.

---

## D-15 — Which copy gets embedded, and by which filter version?

> **Severity: high.** Makes citations and reindex quietly unfaithful.

**Unit A — Story "vector indexing."**
Embeds `sanitized.for_model`, since embeddings exist to feed model context and AD-12 forbids unsanitized text reaching a model.
*Obeys:* AD-12, AD-29 (the raw is untouched), AD-15 (`embedding` is local-only), AD-3.

**Unit B — Story "citation rendering / FR-25 drift audit."**
Resolves `source_ref` to the **raw** payload and shows the PM the true source, per AD-29's whole purpose.
*Obeys:* AD-12, AD-29, Consistency › Citations.

**The incompatibility.**
AD-29 correctly mandates two copies and never says which copy is authoritative for **retrieval**, nor that the derived copy is **versioned**. Consequences:

- The passage most in need of a drift audit — the one whose injection markers were stripped — is embedded in stripped form and may not retrieve at all. FR-25 reports "no drift" for text it never indexed.
- Sanitization is treated as a pure function but is not pinned as one. When the filter improves (it will — it is the anti-prompt-injection layer), the same raw yields a different `for_model`. Nothing records which version produced which stored context.
- `pm-ai reindex` re-derives embeddings with **today's** filter. AD-3 promises "zero data loss" and this is not loss, it is *drift*: retrieval returns different results after a rebuild. The equality assertion in `test_ad3_indexes_rebuild_from_markdown_without_loss` compares a snapshot to a rebuild in one process with one filter version, so it never sees it.
- The PM approves a Proposal whose card quotes raw text; the model that produced the proposal read stripped text. The quoted justification and the actual input differ, which in a dual-authorization product is a correctness bug, not a cosmetic one.

**The production failure.**
A briefing quotes a sentence the model never saw. A drift audit clears a spec that did drift. A reindex silently changes what the system can find, and the only visible symptom is "the assistant used to remember that."

**Close it — new AD-43.**

> ### AD-43 — Sanitization is a pure, versioned function and every derived artefact records which version made it
>
> - **Binds:** AD-3, AD-12, AD-22, AD-29, FR-25, FR-36.2
> - **Prevents:** citations that do not match model input, and a reindex silently changing retrieval results
> - **Rule:** `sanitize(raw) -> Sanitized(for_model, sanitizer_version, raw_digest, redactions)` is **pure and deterministic**: the same input at the same version always yields the same output. `raw_digest` is `sha256` of the raw payload. Every model context, every embedding, and every derived summary is stored with the `sanitizer_version` and `raw_digest` that produced it. Retrieval indexes the sanitized copy **and** stores the raw span offsets, so a citation resolves to the raw payload while remaining anchored to what the model actually read; any citation surfaced to the user carries both the raw excerpt and a `redacted: n` marker where sanitization removed content. `pm-ai reindex` re-derives at each record's **recorded** `sanitizer_version`, not the current one; `pm-ai reindex --resanitize` re-derives at the current version, appends a migration entry to `event_log.md`, and is never implicit.

---

## D-16 — Flipping the encryption toggle

> **Severity: medium-high.** Turns a debug convenience into either a crash loop or a silent privacy failure.

**Unit A — Story "debug encryption toggle: off."**
Reads `encryption = false` from `config.toml`, emits the banner and the `event_log.md` entry (AD-6's only two stated obligations), and writes new data in plaintext. Existing encrypted stores are left as they are.
*Obeys:* AD-6 verbatim.

**Unit B — Story "debug encryption toggle: back on."**
Sees `encryption = true`, opens stores with SQLCipher and envelope-encrypts new blobs. Existing files are left as they are.
*Obeys:* AD-6 verbatim.

**The incompatibility.**
AD-6 specifies the *signal* (banner, log entry, never-default) and not the *state transition*. Neither unit is wrong; together they produce a **mixed store**. SQLCipher cannot open a plaintext database file. Envelope-encrypted and plaintext files coexist in `chat_history/` with no per-file marker.

**The production failure.**
Two shapes. Either the daemon crash-loops under `launchd` `KeepAlive` after the toggle flips back — noisy but survivable — or, far worse, the transcripts written during the debug session stay plaintext in `chat_history/` **forever**, while the banner is gone, `pm-ai doctor` reports "encryption: on," and the user believes their raw meeting recordings are protected. For a product whose entire proposition is sovereignty and NFR-08 compliance, that is the failure that ends it.

**Close it — new AD-44.**

> ### AD-44 — Encryption state is a per-store property changed only by an explicit, complete migration
>
> - **Binds:** AD-6, storage service, `pm-ai doctor`, NFR-08
> - **Prevents:** a debug session leaving plaintext transcripts behind a UI that says encryption is on
> - **Rule:** Every encryptable store carries its encryption state in an on-disk header. Changing it is an explicit command — `pm-ai encryption on|off` — which rewrites **every** affected record, is journalled to `event_log.md` with before/after counts, and is atomic per store: a partial migration rolls back. There is no lazy, per-file, or mixed mode. The daemon **refuses to start** when any store's on-disk state disagrees with the configured state, naming the store and the repair command. `pm-ai doctor` reports the on-disk state per store, never the configured value. The debug banner is emitted on every daemon start and every CLI invocation while any store is unencrypted, not once at the toggle.

---

## D-17 — Purge versus citation

> **Severity: medium-high.**

**Unit A — Story "NFR-09 transcript purge."**
Deletes raw transcript files older than 30 days "after verified conversion into Markdown summaries, Work Item updates, decision logs, and pruned memory indexes."
*Obeys:* NFR-09, AD-3 (markdown remains truth), AD-29 ("the raw payload is stored unmodified **under the retention policy**" — the AD explicitly subordinates itself to retention), AD-5.

**Unit B — Story "citation resolver."**
Dereferences `source_ref` to the raw payload for every fact it surfaces.
*Obeys:* AD-29, Consistency › Citations ("**every** fact surfaced to the user carries a `source_ref` back to the originating event").

**The incompatibility.**
"Every fact carries a resolvable ref" and "raw sources are deleted at 30 days" cannot both hold, and no AD arbitrates. `event_log.md` is *never rotated* (AD-24) and cites sources that are purged at 30 days, so the audit trail's citations decay by design. Neither developer sees the conflict, because each is reading a different document.

Secondary: "verified conversion" has no owner and no definition. Verified by whom? A summary exists — but the FR-25 drift audit that would use the raw runs on demand, months later, and cannot register a claim on it.

**The production failure.**
A commitment made in March, disputed in June: the dashboard cites *"Payment Gateway Sync, 2026-03-14, 00:14:22"* and the resolver returns nothing — an empty panel or a stack trace, depending on the developer. The PM cannot show the evidence for the accountability call the product told him to make. FR-25 drift audits over anything older than 30 days silently compare against nothing and report clean.

**Close it — new AD-45.**

> ### AD-45 — Purge is a tombstone with a digest, and cited evidence cannot be purged out from under a live obligation
>
> - **Binds:** AD-24, AD-29, NFR-09, FR-25, FR-33, FR-34, pruning job
> - **Prevents:** the audit trail citing sources the system silently deleted
> - **Rule:** Purging a raw payload replaces it with a `PurgedSource` tombstone retaining `raw_digest`, `purged_at`, `purge_reason`, byte length, and references to every derived artefact. The citation resolver returns a typed `PurgedSource` that every surface renders explicitly — *"raw purged 2026-06-01; digest a3f9…; summary available"* — never an empty result and never an error. A raw payload **may not be purged** while any `PENDING` or provisional `Commitment`, any unexpired `Proposal`, or any `PENDING_RETRY` job cites it; the purge job skips these and re-evaluates on the next run. "Verified conversion" is defined as: every derived artefact declared by the ingestion pipeline for that payload exists, is parseable, and records the payload's `raw_digest`.

---

# Part 2 — Enforcement gaps

`tests/architecture/` is genuinely good work — the ADVERSARIAL block, the AST checks, and the import contracts are more than most projects ever build. The gaps below are all cases where the spine states a rule, nothing enforces it, and **enforcement is mechanically possible today**.

## 2.1 — ADs the README itself lists as unenforced, that are in fact enforceable

The README waves five ADs away as "human judgement." Three of those are largely mechanizable, and the README's framing is doing harm because it tells the next developer not to try.

| AD | README's claim | What is actually enforceable |
|---|---|---|
| **AD-12** | "a new ingestion path that bypasses the pipeline is a review catch" | **Type-level, not review.** Make it impossible: `ModelPort` methods accept only `SanitizedText`, never `str`. A test inspecting `ModelPort`'s annotations (`test_ad12_model_port_accepts_only_sanitized_text`) catches every bypass at build time, including future ones. Also mechanizable: assert every `TranscriptSourcePort` adapter's return type is `RawPayload`, which has no path to a model context. |
| **AD-18** | "the *contents* of the allowlist are a human decision" | True of the contents, false of the **behaviour**. `test_ad18_unlisted_skill_is_refused` and `test_ad18_out_of_capability_call_is_refused_and_logged` are ordinary behavioural tests of the same shape as the AD-28 test that *was* written. The mechanism, not the policy, is the invariant. |
| **AD-19** | "needs load testing, not a unit test" | True of the *performance* claim, false of the *bound*. `test_ad19_pool_admits_one_heavy_job_at_a_time` with two fake jobs and a barrier is a unit test. So is an AST rule that no `models/local/` inference call is awaited on the event loop. |
| **AD-10** | "correct per-project cursor isolation needs an integration environment" | The **collision** case does not. `test_ad10_two_instances_of_one_connector_type_have_distinct_cursor_keys` is pure. Given D-10, this is the test that would have caught two projects sharing a GitLab cursor. |
| **AD-4** | "'is this configuration project-specific?' needs a reviewer" | The **direction** does not: `test_ad4_personal_scope_rejects_project_config` (writing a `ConnectorInstance` into `PersonalScope` raises `ScopeViolation`) is the exact shape of the AD-28 test. Plus an AST rule: no `.manager-ai` / `.pm-ai` / `.project-ai` string literal outside `pm_ai/storage/paths.py`. |

## 2.2 — ADs with a stated rule and no check at all

These clauses appear in the spine, appear nowhere in `tests/architecture/`, and are mechanically checkable.

1. **AD-1: "Every skill invocation appends an entry to `event_log.md`."** The single most security-relevant obligation in the document. Nothing asserts it. A behavioural test — invoke a fake skill, assert an entry appended — is trivial.
2. **AD-13: "No external mutation derived from implicit extraction may execute without an approved Proposal."** The dual-authorization guarantee that FR-06 and the Non-Goals are built on. Only the *expiry* clause of AD-13 is tested. `test_ad13_mutating_skill_requires_approved_proposal_or_explicit_verbal_auth` — assert `skills.invoke(mutating, authorization=None)` raises — is a ten-line test protecting the product's central safety property.
3. **AD-2: "Access is restricted to cryptographically paired Telegram user IDs; unpaired senders are rejected and logged."** Only `TRANSPORT == "long_polling"` is checked. The pairing check is the actual access control and is behaviourally testable.
4. **AD-5: markdown ledgers are append-only.** `test_ad5_single_writer_owns_all_file_writes` scans every layer **except `storage`** — so the one component that can rewrite a ledger in place is the one component not checked. Add: within `pm_ai/storage`, any `open()` on a `*.md` path must use mode `"a"`, and there must be no `write_text` on a ledger path.
5. **AD-6: "off is never the default in a fresh install"** and "must emit a CLI banner and an `event_log.md` entry." Both trivially testable against the default config template; only the `is_encrypted` path mapping is tested.
6. **AD-8: "a per-user token file at `0600`."** Only the 401 is tested. A `stat().st_mode & 0o777 == 0o600` assertion on the created token file is one line, and file-mode bugs are exactly the kind that ship.
7. **AD-15: "a call without a declared task class is a defect."** Only routing is tested. An AST rule — every call to a `ModelPort` method passes `task_class=` as a keyword — is the same shape as the existing AD-24 check and catches the defect the AD names.
8. **AD-16: the Tool Runner constraint.** `.importlinter` blocks the *forbidden* libraries but nothing asserts the *required* shape: that the frontier adapter calls `client.beta.messages.tool_runner` and that the tool list it passes equals the registry-authorized set for that flow. The second half is the security property; the import ban alone does not deliver it.
9. **Consistency › Identifiers.** Prefixed ULIDs are stated and unchecked. `test_identifier_prefixes` over each entity's id factory asserting `cmt_`/`prp_`/`evt_`/`job_`/`skl_` and ULID sortability. Cheap, and D-13 shows what happens when id semantics drift.
10. **Consistency › Dates & times: "stored UTC."** An AST rule banning `datetime.now()` without `tz=`, `datetime.utcnow()`, and naive `fromtimestamp` outside `surfaces/` is standard and directly guards D-3.
11. **Consistency › Errors: "no external SDK exception escapes an adapter."** Mechanizable as an AST rule: no `raise` of an imported-SDK exception type in `connectors/`, `skills/`, `models/`; every public adapter method's body is wrapped.
12. **Consistency › Retries: "never hand-rolled inside a connector or skill."** Enforce by forbidding `tenacity`, `backoff`, `time.sleep`, and `asyncio.sleep` in `connectors/` and `skills/` — an `.importlinter` contract plus one AST rule.
13. **Consistency › Config: "secrets never in TOML."** Testable: the TOML writer rejects keys matching a secret-name allowlist (`token`, `secret`, `password`, `api_key`, `client_secret`).
14. **AD-23's real claim.** The existing test asserts `len(adapters) >= 2` and `requires_network is False` — it checks the adapters *exist*, not the AD's actual promise that *"the extraction pipeline must be exercisable end-to-end using only the fallback adapter."* An end-to-end test from a fixture `.vtt` through to a staged Proposal is the test the AD asks for.

## 2.3 — Existing checks that are weaker than they look

1. **`test_ad20_idempotency_keys_are_deterministic` — the test the README calls the one to keep — is weak.** It calls `idempotency_key(...)` twice in one process. A key seeded from `time.time()` within the same second, or from a module-level `random` constant, or from `id(obj)`, **passes**. Strengthen it two ways: derive the key in a `subprocess` and compare across processes, and add an AST rule that `pm_ai/core/jobs.py` imports no `uuid`, `random`, `secrets`, or `time`. Per D-8, also assert the key is scope-qualified and derived from the persisted payload.
2. **`test_ad3_indexes_rebuild_from_markdown_without_loss` proves less than it claims.** It snapshots, drops, rebuilds, and compares — in one process, with one code version, over whatever corpus happens to exist. It cannot see D-4 (fold order), D-11 (schema versions), or D-15 (sanitizer version). Require the corpus to contain at least one instance of **every** member of `NormalizedEventType` and every `event_log.md` entry type, at every released `schema_version` — and fail the test when a new enum member has no corpus fixture.
3. **`test_ad24_event_log_is_not_a_debug_sink`** greps unparsed call text for `.debug(`/`.info(` near the token `event_log`. Writing diagnostics through `storage.append_event(...)` bypasses it entirely. Enforce at the type level instead: `append_event` accepts only a closed `EventLogEntryType` (which AD-27 already requires) and rejects free-form strings.
4. **`test_ad9_cursor_is_opaque_to_the_core`** checks for five forbidden attribute names on `Cursor` (`timestamp`, `page`, `offset`, `since`, `value`). A sixth name defeats it. Invert it: assert `Cursor.__slots__ == ("connector_type", "cursor_format_version", "opaque_bytes")` per D-12, and add an AST rule that no module outside `pm_ai/connectors/<type>.py` accesses `.opaque_bytes`.
5. **`.importlinter` layering permits `surfaces → storage` and `surfaces → models`.** The layers list puts `surfaces` above the adapter tier, so the Telegram bridge may import `pm_ai.storage` and `pm_ai.models.router` directly, bypassing core entirely. That is not obviously wrong, but it is **unstated** — and it is exactly how "Telegram and CLI reach identical functionality through the same core services" (AD-7) rots. Either add a contract forbidding `surfaces → {storage, models, connectors}` (surfaces reach the daemon through core services only), or write the allowance into AD-7 explicitly.
6. **`pm_ai.ports` has no forbidden-import contract.** Ports is the one module core is allowed to import. Nothing stops a Protocol file from importing `httpx` types into its signatures, which drags an I/O library into core's transitive closure and quietly punctures AD-1. One `forbidden` contract closes it.
7. **`pm_ai.models` is absent from `http-confined-to-adapters`.** Defensible (the Anthropic SDK needs HTTP), but it means the frontier adapter may make arbitrary un-registry-checked HTTP calls — which under D-9 is the same hole in a different layer. State the exemption deliberately.
8. **Dead constants.** `WRITE_ALLOWED` and `SHELL_ALLOWED` in `test_static_rules.py` are defined and never used; the allowlists are re-expressed inline as literal layer lists. Two sources of truth for the same policy, and the unused one is the one that reads like documentation.
9. **README/test drift already.** The README says "the five tests marked `ADVERSARIAL`"; the block contains six test functions covering five holes. Minor, but this document's own thesis is that unenforced statements drift — and it drifted within a day.

---

# Part 3 — Prioritized close list

**Close before writing any Phase 1 code** (these change data shapes, and retrofitting them means migrating stored markdown):

| # | New/tightened AD | Hole |
|---|---|---|
| 1 | **AD-30** — typed `EntityRef` | D-1 |
| 2 | **AD-31** — `ActorRef` + `Person` registry | D-2 |
| 3 | **AD-33** — `seq` ordering + one `fold_ledger` | D-4 |
| 4 | **AD-39** — `schema_version` on every record; enums immutable | D-11 |
| 5 | **AD-41** — connector-supplied `dedup_key` | D-13 |
| 6 | **AD-38** — Scope kinds vs storage roots vs capabilities | D-10 |

**Close before the first external mutation ships:**

| # | New/tightened AD | Hole |
|---|---|---|
| 7 | **AD-42** — `self_authored` exclusion + per-entity lease | D-14 |
| 8 | **AD-36** — scoped, frozen, canonical, claimed idempotency keys | D-8 |
| 9 | **AD-13 tightened** — `edited` is terminal and non-executing | D-5 |
| 10 | **AD-34** — CAS transitions, side effects enlisted | D-6 |
| 11 | **AD-35** — expiry window + job dispatch re-check + `max_age` | D-7 |
| 12 | **AD-37** — ingress/egress split, both logged and registry-declared | D-9 |

**Close before the first four-hour harvest runs unattended:**

| # | New/tightened AD | Hole |
|---|---|---|
| 13 | **AD-32** — two clocks, provisional `BROKEN`, retroactive folds | D-3 |
| 14 | **AD-40** — cursor format version + staleness alarm + reload drain | D-12 |
| 15 | **AD-43** — versioned pure sanitization | D-15 |
| 16 | **AD-45** — purge tombstones | D-17 |
| 17 | **AD-44** — encryption toggle as complete migration | D-16 |

**Enforcement work, in order of value per line of test code:** AD-13's mutation-requires-approval test; AD-1's every-invocation-is-logged test; strengthening the AD-20 determinism test across processes; the AD-12 `SanitizedText` type gate; the AD-3 corpus-completeness requirement; the AD-18 refusal tests; the AD-15 call-site `task_class` AST rule.

**One meta-observation for whoever owns this spine:** the document's own closing rule — *"an AD nothing enforces is a convention, and conventions drift"* — should be extended. Every one of the seventeen holes above is a place where the spine named a **noun** (`source_ref`, `actor`, `scope`, `cursor`, `edited`, `canonical_payload`, `dedup`, `verified conversion`) and left it as prose. The parallel rule is: *an AD that names a value without naming its type, its owner, and its lifetime is not an invariant — it is a shared word, and shared words diverge faster than conventions do.*
