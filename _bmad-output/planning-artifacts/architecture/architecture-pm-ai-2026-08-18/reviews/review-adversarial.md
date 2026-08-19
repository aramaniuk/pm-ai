# Adversarial Review — ARCHITECTURE-SPINE.md (pm-ai)

**Artifact:** `_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md`
**Lens:** adversarial — what is missing, not only what is wrong
**Reviewed:** 2026-08-18 · independent pass, no prior project context
**Supporting context consulted:** `docs/prd_pm_ai.md` v0.9.1, `.importlinter`, `tests/architecture/`, sibling `SOLUTION-DESIGN.md`

This is a strong spine. It is unusually disciplined about ownership, it names the
divergences it creates, and it ships an enforcement layer. The findings below are
therefore mostly about **holes the document does not know it has** — invariants a
downstream epic will have to invent because nothing here decides them, and two
places where the spine contradicts itself or the PRD inside the same page.

**22 findings.**

---

## 1. The frontier adapter is egress that AD-1 says cannot exist

- **location** — AD-1 (MCP execution firewall), AD-15, AD-16, Design Paradigm layer table
- **trigger_condition** — AD-1 states the MCP skill layer is "the **only** egress" and that "100% of external reads and writes route through the MCP skill layer", while AD-15/AD-16 route model calls to `api.anthropic.com` through the model adapter, which is not a skill.
- **guard_snippet** — Rewrite AD-1's rule to name exactly two egress channels and their distinct rules: "External **actions** (reads and mutations against user-facing systems) route only through the MCP skill layer. The frontier model adapter is the one other outbound path; it carries **no** capability to mutate anything except through skills handed to the Tool Runner, and it is governed by AD-1b (data egress) rather than by the skill registry." Then add the missing **AD-1b — what may cross into a frontier prompt**, enumerating the scopes and payload classes permitted (see finding 2).
- **potential_consequence** — A builder reads "only egress" literally, wraps the Anthropic call in a pseudo-skill to comply, and the accounting/routing invariants of AD-15 are bypassed; or a reviewer waves through a second non-skill egress ("it's like the model adapter") because the exception is undocumented.

## 2. No rule governs what personal data may leave the machine in a frontier prompt

- **location** — AD-15, AD-25, AD-6, Capability map row "Security enclave & privacy charter"
- **trigger_condition** — `coaching` is a frontier-eligible task class routed to `claude-opus-5`, and coaching context is exactly the material FR-16's User Privacy & Data Boundary Charter calls "strictly hardware-bound" and NFR-07 calls never-exported — burnout metrics, calendar density, `coaching_1on1_history.md`. The spine physically separates that store (AD-25) and then says nothing about it being serialized into an API request.
- **guard_snippet** — Add an AD: "**Frontier prompts carry a declared data class.** Each `task_class` declares the maximum scope it may read: `briefing_synthesis` and `draft_generation` may read project scope and derived summaries; `coaching` may read personal-scope markdown but **never** `~/.manager-ai-private/` raw analytics — only aggregate flags (e.g. `ELEVATED_WORKLOAD_ALERT`) computed locally. The router refuses a call whose assembled context exceeds the declared class, and logs the class alongside token counts in `event_log.md`." Reconcile explicitly with the Charter's "never exported" wording in the PRD, or downgrade the Charter's wording with a recorded decision.
- **potential_consequence** — The system's headline privacy promise is broken by its headline feature. Raw burnout telemetry ships to a third party, the charter becomes marketing copy, and no test or reviewer catches it because the spine never said it was forbidden.

## 3. Explicit in-meeting commands auto-execute with no authorization model

- **location** — AD-13, AD-1, Capability map row "Transcript ingestion & meeting pipeline"
- **trigger_condition** — FR-05/FR-07 auto-execute external mutations from transcript text addressed to "pm-ai" or "John", bypassing the Proposal gate. The trigger is attacker-controllable: any meeting attendee — or anyone who can place text in a calendar invite, an ingested transcript file, or the manual watched folder of AD-23 — can utter the magic phrase. AD-12/AD-29 sanitize *injection into the model*, not *authority to act*. FR-07 appears nowhere in the Capability → Architecture map.
- **guard_snippet** — Add an AD: "**Explicit-command authority is bound to identity, not to phrasing.** An extracted explicit command executes without approval only when (a) the speaker resolves to the paired principal via transcript speaker labels or diarization, (b) the target ref lies inside a project the principal owns, and (c) the skill's declared scope permits the mutation. Any command failing (a)–(c) degrades to a staged `Proposal`, never to a silent drop. The manual transcript adapter (AD-23) never confers explicit authority." Add FR-05/FR-07 to the Capability map under that AD.
- **potential_consequence** — The execution firewall stops shell access and then hands unauthenticated external write authority to whoever speaks in a meeting. A hostile or careless attendee mutates work items, and the audit trail records it as authorized.

## 4. AD-3's "rebuildable from markdown" is contradicted by AD-20 and AD-9 on the same page

- **location** — AD-3, AD-9, AD-20, Deployment & operations (Backup)
- **trigger_condition** — AD-3 declares `event_telemetry.db` disposable and says "any state that cannot be reconstructed from markdown is a defect." But the same database holds the **job queue** (AD-20: every deferred unit of work is a persisted row), **connector cursors** (AD-9), and the offline `PENDING_RETRY` buffer (FR-04/NFR-10) — none of which exist in any markdown ledger. `pm-ai reindex` after a database loss therefore silently discards pending external mutations and resets every cursor.
- **guard_snippet** — Split the claim: "Derived state (indexes, embeddings, telemetry rows) is rebuildable from markdown and is not a backup target. **Operational state** — job queue rows, connector cursors, executed-idempotency-key ledger — is *not* derivable and is not domain truth; it lives in `~/.pm-ai/private/` under its own durability rule: it must survive daemon restart, is included in backup, and its loss is a recoverable degradation (cursors reset to a bounded look-back window; unexecuted jobs are lost and reported by `pm-ai doctor`), not zero-loss."
- **potential_consequence** — A user follows the documented recovery path (delete the cache, reindex) and loses queued external actions and every harvest cursor, producing either weeks of duplicate re-harvest or a permanent telemetry gap — while the spine told them the operation was lossless.

## 5. Idempotency keys are mandated but nothing says where they are enforced or how long they live

- **location** — AD-20, Consistency Conventions (Idempotency keys)
- **trigger_condition** — GitLab comment posts, emails, and calendar writes largely have no provider-side idempotency-key API, so the guarantee must be enforced by a **local executed-key ledger**. The spine never names that ledger, never says it is durable, and — per finding 4 — the obvious home for it is the database AD-3 declares disposable. There is also no TTL and no story for a *legitimate* repeat of an identical mutation.
- **guard_snippet** — Extend AD-20: "Enforcement is local. The skill layer consults a durable `executed_keys` table before any mutating invocation and records the key plus the provider's response ref on success. That table is operational state (finding 4), backed up, and never dropped by `reindex`. Keys expire after 30 days. A deliberate repeat of an identical mutation must carry an explicit `repeat_seq` component in the payload; there is no other way to re-execute."
- **potential_consequence** — The single most dangerous failure the spine set out to prevent — duplicate external writes on replay — happens anyway, after a cache wipe, with the test suite green.

## 6. "Canonical payload" in the key formula is undefined and unversioned

- **location** — Consistency Conventions (Idempotency keys), AD-20
- **trigger_condition** — `sha256(job_type + target_ref + canonical_payload)` never defines canonicalization (key ordering, unicode normalization, whitespace, number formatting, null handling) or a separator, and never versions the algorithm. The enforcement test only checks determinism *within one process*.
- **guard_snippet** — Specify it: "Canonical form is RFC 8785 JCS over the payload; the key is `sha256("v1" ‖ 0x1f ‖ job_type ‖ 0x1f ‖ target_ref ‖ 0x1f ‖ jcs(payload))`. The version prefix is part of the key; changing canonicalization requires bumping it, and a version bump makes every in-flight job re-executable — so it may only be done with the queue drained." Add a test vector (fixed payload → fixed hex digest) to `test_domain_invariants.py` so cross-version stability, not just intra-run determinism, is what is enforced.
- **potential_consequence** — Two builders (or one builder before and after a refactor) generate different keys for the same logical mutation; replay double-posts; or a dict-ordering change during an upgrade re-executes the whole pending queue.

## 7. Five NFR latency budgets are bound by the frontmatter and governed by no AD

- **location** — frontmatter `binds`, AD-19, AD-21, AD-22, Capability map
- **trigger_condition** — The spine claims to bind NFR-01..NFR-14, but NFR-01 (10 s voice transcription), NFR-02 (45 s voice-to-draft round trip), NFR-03 (600 s meeting post-processing), NFR-05 (15 min research dispatch), and NFR-06 (300 s missed-meeting ingestion) appear in no AD and no Capability-map row. AD-22 covers only retrieval vs synthesis. UJ-2 — the flagship voice journey — is bound by nothing.
- **guard_snippet** — Either add an AD that assigns each SLA an owning component and a measurement point, or explicitly move them to Deferred with a reason. Minimum viable AD: "Each latency-bearing flow declares a budget at its entry point; the job queue records `enqueued_at`/`started_at`/`completed_at` for every job, and `pm-ai doctor` reports p50/p95 per flow against the NFR budget. An SLA with no instrumented flow is a defect."
- **potential_consequence** — The spine claims a binding it does not have. Stories get written to functional acceptance criteria only, nobody owns the 45-second voice round trip, and the product's most-used path degrades with no signal.

## 8. AD-19's single-heavy-job bound makes NFR-01/NFR-02 unachievable, and there is no priority rule

- **location** — AD-19, AD-21, Open Risks
- **trigger_condition** — "One heavy local-model job at a time" plus a FIFO pool means a 20-second voice note (NFR-01: 10 s) can queue behind a 45-minute meeting transcription or a bulk embedding rebuild. The spine specifies the bound but never specifies **scheduling discipline** inside the pool.
- **guard_snippet** — Extend AD-19: "The bounded pool is priority-scheduled, not FIFO. Two classes: **interactive** (voice transcription, fuzzy match, live query embedding) always preempts or precedes **batch** (bulk transcription, index rebuild, pruning). Batch jobs are chunked so a job at the head of the queue cannot hold the single slot longer than 60 s. Interactive queue depth is a `pm-ai doctor` health signal."
- **potential_consequence** — NFR-01 and NFR-02 are missed by design under exactly the load the system is built for, and the fix arrives as a per-feature hack (one feature spawning its own thread) that quietly breaks the single-pool invariant.

## 9. AD-4 says "exactly three scopes" and the same document shows four roots

- **location** — AD-4, AD-25, Structural Seed (Scopes and storage)
- **trigger_condition** — AD-4: "Exactly three scopes." AD-25 then mandates `~/.manager-ai-private/` as a separate store, and the storage diagram shows four top-level trees. The scope enumeration in code (`Scope` is a named entity in the conventions) now has an ambiguous cardinality.
- **guard_snippet** — Restate AD-4 as: "Four storage roots under three **ownership** scopes: application (`~/.pm-ai/`, including `private/`), personal (`~/.manager-ai/` plaintext record + `~/.manager-ai-private/` analytics enclave), and project (`<repo>/.project-ai/`). The `Scope` enum has three members; the private enclaves are properties of their scope, not scopes of their own."
- **potential_consequence** — A builder taking AD-4 literally writes personal analytics into `~/.manager-ai/`, defeating AD-25's physical separation and putting burnout metrics into a directory the PRD encourages users to back up as a git repository.

## 10. Connectors are hot-loadable plugins with no allowlist, no isolation, and no load-path AD

- **location** — Design Paradigm (Inbound adapters, "hot-loadable plugins"), AD-9, AD-10, AD-18
- **trigger_condition** — AD-18 gives the *skill* layer an explicit registry, declared scopes, refusal, and a pluggable verification hook. Connectors get none of that, yet they are inbound code that holds decrypted credentials, runs in the daemon process, and per FR-35.3/UJ-10 is loaded **at runtime without a restart**. Nothing states what happens when a hot-loaded connector raises on import, how an in-flight harvest is drained during a swap, or whether connector code is allowlisted at all.
- **guard_snippet** — Add an AD: "**Connectors are registry-authorized on the same terms as skills.** A connector module is loadable only if listed in the first-party connector registry with a declared credential scope. Hot load is a transactional swap: the scheduler quiesces the instance, drains in-flight harvests, imports and probes the new module, and rolls back to the previous version on any failure — a failed load never leaves an instance unscheduled or a cursor advanced. Connector import failures are isolated: one bad module never prevents daemon start."
- **potential_consequence** — A connector update at 3 p.m. takes down the daemon, or advances a cursor past events it failed to harvest. Worse, the asymmetry invites the reading that inbound code is less security-relevant than outbound code, which is backwards — the inbound side holds the credentials.

## 11. No credential lifecycle, and no secret-redaction rule for a plaintext, git-committed audit trail

- **location** — AD-6, AD-24, AD-4, Consistency Conventions (Config, Logging)
- **trigger_condition** — The spine covers credentials at rest (`config.json`, encrypted) and stops there. Missing: which layer may hold a decrypted secret, OAuth refresh and expiry mid-harvest, revocation, and — most sharply — **redaction**. `event_log.md` is plaintext, append-only, never rotated (AD-24), and in project scope it is *committed to git* (AD-4). Connectors translate external failures into domain errors (Conventions/Errors); an unredacted provider error body containing a bearer token or a signed URL lands in a git repository permanently.
- **guard_snippet** — Add an AD: "**Secrets never leave the storage service in plaintext beyond the adapter that needs them.** Credentials are fetched per-invocation, never cached in core or surfaces, and never included in a model context. Every write to `event_log.md`, structured logs, or a `Proposal` payload passes a redaction filter that strips known-secret shapes (bearer tokens, `?sig=`/`?token=` query params, PEM blocks, connector credential fields). Adapters translate external failures into domain errors carrying a status and a message id, never a raw provider response body. Credential expiry surfaces the connector instance as unhealthy rather than retrying with a dead token."
- **potential_consequence** — A leaked API token in a git-committed, never-rotated file, distributed to every team member who clones the repo — and unfixable by deletion, since git history retains it.

## 12. Nothing constrains what may be written into a git-committed project scope

- **location** — AD-4, AD-28, AD-24
- **trigger_condition** — AD-28 keeps *coaching commitments* out of the project ledger. But `.project-ai/memory/event_log.md` and `commitments_log.md` are committed to a shared repository, and every transcript-derived entry may carry verbatim quotes, attendee names, and personal statements from a recorded meeting. AD-28 guards one entity type; nothing guards content.
- **guard_snippet** — Generalize AD-28: "Project-scope markdown is **published material**. Entries carry structured fields and summaries, never verbatim transcript spans, never attendee contact data, never anything from the personal scopes. `source_ref` points at the raw payload in the encrypted local enclave; the enclave is never inlined into a committed file. A single `is_publishable(entry)` check in the storage service gates every project-scope write."
- **potential_consequence** — The first time a difficult 1:1-adjacent remark from a team meeting is committed to the team's repository, the product has created an HR incident and the user stops trusting it — an unrecoverable failure for a tool whose whole premise is sovereignty.

## 13. Citations outlive the evidence they cite

- **location** — AD-29, Consistency Conventions (Citations), Deferred (retention)
- **trigger_condition** — AD-29 requires the raw payload to be retained so `source_ref` resolves. NFR-09 purges raw transcripts after 30 days. Every fact surfaced to the user "carries a `source_ref` back to the originating event." After 30 days those refs dangle, and the spine says nothing about what a dangling ref renders as, or whether a citation whose evidence has been purged may still be used to justify a commitment status transition.
- **guard_snippet** — Extend AD-29: "A `source_ref` records the evidence's retention class at creation. When the raw payload is purged, the ref resolves to a tombstone carrying the extracted quote, its offsets, and a content hash of the purged original — never to a 404. Commitment verification (AD-14) may cite a tombstoned ref but must mark the evidence as `unverifiable_raw`. Purging never rewrites or removes an existing `source_ref`."
- **potential_consequence** — Month-two briefings cite sources that no longer exist; drift audits and commitment verdicts become unauditable exactly when the accumulated history starts to be valuable — which is the moment the product is supposed to prove itself.

## 14. AD-2 reverses a PRD-testable consequence without recording the supersession

- **location** — AD-2, Open Risks / no divergence table
- **trigger_condition** — AD-2 prohibits Telegram webhooks. PRD FR-19 says "HTTPS webhook/polling" and its testable consequences read "Telegram **webhook endpoint** responds to authorized updates within 2000ms" and non-paired IDs "receive an immediate **HTTP 403**" — both unsatisfiable under outbound long-polling (there is no endpoint and no HTTP response to a stranger). NFR-14 repeats "webhook/polling." The PRD's v0.9.0 reconciliation log closes six divergences; this is not one of them. `SOLUTION-DESIGN.md` argues the case, but the spine is what stories are derived from.
- **guard_snippet** — Add to AD-2: "**Supersedes** FR-19 and NFR-14's 'webhook/polling' wording and FR-19's webhook-latency and HTTP-403 consequences. Restated acceptance: an update from an unpaired Telegram user ID is dropped without a reply and appends a `[SECURITY_UNPAIRED_SENDER]` entry to `event_log.md`; the bridge acknowledges a paired update within 2000 ms of receipt from the long-poll." Raise a PRD amendment so the two documents cannot be read as offering a choice.
- **potential_consequence** — A story written from the PRD implements a webhook path (or a QA gate tests for HTTP 403), and the zero-public-ports guarantee — the strongest security claim in the product — is breached by a tunnel service added to satisfy an acceptance criterion.

## 15. AD-7's surface-parity clause has no mechanism and is falsely listed as enforced

- **location** — AD-7, AD-13, Enforcement table, `tests/architecture/README.md`
- **trigger_condition** — "Telegram and CLI must reach identical functionality... no feature may exist on only one surface" is the most frequently violated class of invariant in dual-surface products, and the spine provides no mechanism (no intent/command registry, no shared capability contract) and no check. The enforcement README lists AD-7 as covered by `cli-owns-no-scheduling`, which enforces something entirely different, and the "not mechanically enforced" list omits parity — so a reader concludes it is covered.
- **guard_snippet** — Extend AD-7: "Every user-reachable capability is registered once in a core `capability registry` (name, parameter schema, response shape). Surfaces are renderers over that registry and define no capabilities of their own." Then add `test_ad7_every_capability_renders_on_both_surfaces` comparing the registry against each surface's dispatch table, and move parity into the README's unenforced list until that test exists.
- **potential_consequence** — Six months in, half the features are CLI-only, Telegram is the "primary surface" per the PRD's own decision log, and the fix is a rewrite of every surface handler.

## 16. The one-card-renderer claim ignores Telegram's hard limits

- **location** — AD-13, AD-21
- **trigger_condition** — "One card renderer serves both surfaces (Telegram inline keyboard, CLI approval queue)." Telegram caps messages at 4096 characters and `callback_data` at 64 bytes; a CLI approval queue has neither limit. A Proposal payload with a long extracted summary, or a proposal id plus action encoded into callback data, cannot be rendered identically.
- **guard_snippet** — Extend AD-13: "The renderer produces a surface-neutral card model (header, body blocks, actions). Surfaces own presentation limits: Telegram truncates body blocks at 3500 characters with a `/show <prp_id>` expansion, and `callback_data` carries only the ULID plus a one-byte action code — never payload content. Proposal identity is always the ULID; no surface encodes payload into a control."
- **potential_consequence** — The first long transcript summary produces a Telegram API error at approval time, and the fix is a Telegram-specific renderer — which forks the card format AD-13 exists to prevent.

## 17. Config precedence across three scopes is unspecified, and `persona.md` exists in two of them

- **location** — AD-4, Consistency Conventions (Config), Structural Seed (Scopes and storage)
- **trigger_condition** — `persona.md` lives in **both** `~/.manager-ai/rules/` and `<repo>/.project-ai/rules/` (PRD §2.1). Rules files exist in both. `config.toml` lives in `~/.pm-ai/`. FR-20 lets the user mutate persona from either surface. The spine never states which layer wins, how they merge, or where a `pm-ai persona set` write lands when Telegram has no working directory (AD-11 says Telegram needs explicit project selection — so a persona edit is scope-ambiguous by construction).
- **guard_snippet** — Add to Consistency Conventions: "**Resolution order** for rules, persona, and settings is project → personal → application default, merged key-wise with the nearest scope winning; the resolved value carries its origin scope so any surface can answer 'why'. A write via `pm-ai persona set` requires an explicit `--scope`; there is no implicit default. Personal-scope rules never read project scope (AD-4)."
- **potential_consequence** — Two features resolve persona differently, a project persona silently overrides personal coaching tone in a 1:1, and the user's persona edit disappears into whichever scope the last-written feature guessed.

## 18. FR-13's notification boundary — a system-wide invariant — has no owning AD

- **location** — Capability map, AD-21, AD-13, Non-Goals reference in AD-1
- **trigger_condition** — FR-13 and the Non-Goals bound push notifications to exactly two triggers (scheduled pre-meeting cards, post-meeting summaries). This is a cross-cutting egress-to-the-user rule that **any** feature can violate with one Telegram send. The spine centralizes proposals (AD-13), scheduling (AD-9), async delivery (AD-21) and model access (AD-15) — and then leaves user-facing pushes uncentralized. FR-13 appears in no AD's `Binds` and in no Capability-map row.
- **guard_snippet** — Add an AD: "**All unsolicited outbound messages to the user pass through one notification gate.** The gate holds a closed enumeration of permitted triggers (pre-meeting prep, post-meeting summary, proposal expiry warning, health-critical alert) and a quiet-hours policy; a send with an unlisted trigger is refused and logged. Replies to user-initiated requests are not notifications and bypass the gate."
- **potential_consequence** — The product's defining restraint — no unsolicited interruptions — erodes one feature at a time, and pm-ai becomes the notification relay the PRD explicitly rejected on 2026-08-16.

## 19. No AD for wall-clock reality on a laptop: sleep, wake, timezone, missed triggers

- **location** — AD-20, AD-9, Consistency Conventions (Dates & times), Deployment & operations
- **trigger_condition** — Time-anchored obligations are everywhere: briefing by 07:00 local (FR-09), T-15 min and T-60 min meeting triggers (FR-26, FR-32), 4-hour harvest cadence ±15 min (FR-02), 7-day proposal TTL, 48-hour milestone alerts, 30-day purge. The host is a laptop that sleeps, travels between timezones, and has its clock stepped. AD-20 makes jobs durable but never says what a durable job does when its scheduled instant passed while the machine was asleep.
- **guard_snippet** — Add an AD: "**Every scheduled job declares a catch-up policy**: `fire_late` (run on wake, marked late), `skip_if_stale(window)` (a T-15 prep card is worthless at T+40), or `coalesce` (many missed harvest ticks collapse to one). Scheduling is on monotonic time; wall-clock anchors resolve against the user's *current* timezone at fire time, and a timezone change re-anchors pending local-time jobs. Clock steps larger than 5 minutes are logged and trigger a scheduler re-plan."
- **potential_consequence** — Prep cards arrive after their meetings, the morning briefing fires at 3 p.m., or a wake from a weekend of sleep stampedes 40 coalesced harvests into the rate limiter at once — and each team fixes it differently.

## 20. No versioning rule for storage schema, ledger format, or embedding model

- **location** — AD-3, AD-27, AD-5, Consistency Conventions (Markdown ledger entries), Deferred (local-model selection)
- **trigger_condition** — AD-27 versions the two enumerations and nothing else. Unversioned: the SQLite schema, the markdown ledger entry format, job-queue payload shapes, and the **embedding model identity**. Local model selection is explicitly Deferred, so the embedding model *will* change — at which point every stored vector is meaningless, yet AD-3 says the index is rebuildable and stops there. Separately, ledger parsers "must tolerate hand-edits" with no rule for an unparseable one.
- **guard_snippet** — Add to AD-3/AD-5: "Every derived store records the generator identity it was built with (schema version, ledger format version, embedding model id + dimension). A mismatch at startup forces a rebuild rather than a silent mixed-generation read. Markdown ledger entries carry a format version in the header line. A block that fails to parse is **quarantined and reported** — never skipped silently and never rewritten — because a skipped block is a lost commitment and AD-3's zero-loss claim is only true if unparseable input is loud."
- **potential_consequence** — Post-benchmark model swap leaves a vector index mixing two embedding spaces; retrieval quietly returns nonsense; and a single hand-edit typo silently deletes a commitment from the rebuilt index while `reindex` reports success.

## 21. Multi-step proposal execution has no partial-failure semantics

- **location** — AD-13, AD-20
- **trigger_condition** — UJ-9's climax executes several external mutations from one approval (write focus blocks to Outlook, update the dashboard, update alignment metrics); UJ-2 dispatches to email and Teams. `Proposal.status` is a single value with `executed` as a terminal state. Nothing says what happens when the second of three skill invocations fails: is the proposal `executed`? retried whole (re-posting the first)? left mid-flight?
- **guard_snippet** — Extend AD-13/AD-20: "A proposal's executor resolves to an ordered list of **jobs**, each individually keyed and retried. Proposal status is derived, not set: `executed` only when every job succeeded; `partially_executed` otherwise, listing which jobs succeeded and which failed. Retry re-enqueues only failed jobs — idempotency keys make a whole-proposal retry safe but a partial retry cheaper. There is no compensation/rollback: external mutations are forward-only, and a partially executed proposal is surfaced to the user rather than silently repaired."
- **potential_consequence** — A half-applied schedule change that the system reports as done; or a retry that re-sends an email already delivered, because the retry unit was the proposal rather than the job.

## 22. The Enforcement section overclaims, and the suite's exit criterion is already gameable

- **location** — Enforcement section, `tests/architecture/README.md`, `tests/architecture/*`
- **trigger_condition** — "The spine is executable, not just readable... these checks fail the build instead." Today `pm_ai/` contains only empty `__init__.py` files, so the AST checks pass **vacuously** and every behavioural test `pytest.skip`s — the suite proves nothing while the spine asserts it is enforced. The README's premise ("`pm_ai/` does not exist yet, so these skip") is already stale, and "zero skips" is satisfiable by creating empty stub modules. The checks themselves are name-based and narrow: `WRITE_ALLOWED` misses `Path.open("w")`, `io.open`, `os.makedirs`, `shutil.copyfileobj`, `json.dump(fp)`; AD-24's check only matches the literal string `event_log` inside a logging call; AD-11's only matches a `.project-ai` string constant in a glob; and `test_ad8` calls `api.test_client()`, which is Flask's API — the Stack section specifies FastAPI.
- **guard_snippet** — (a) Soften the claim to what is true: "these checks catch the mechanical cases; the judgement calls are listed in `tests/architecture/README.md`." (b) Replace "zero skips" with a positive criterion: each behavioural test must assert against a real implementation, verified by a meta-test that fails if a named module exists but exports only stubs. (c) Add the missing write/exec call names and switch AD-24's check to flag any logging call whose target resolves to a ledger path constant. (d) Fix `test_ad8` to use `fastapi.testclient.TestClient`. (e) Add the `layering` gap: the contract places `surfaces` **above** the adapters, so a surface may legally import a connector or storage module directly, bypassing core — add a `forbidden` contract for `pm_ai.surfaces → pm_ai.connectors|pm_ai.storage|pm_ai.models` to match AD-7's thin-client intent.
- **potential_consequence** — A green build is read as a compliant build. The team relaxes review precisely because "the spine is executable", and the first real violations land in the exact classes the checks cannot see.

---

## Document hygiene (not counted above, worth a pass)

- Frontmatter says `companions: []`, but `SOLUTION-DESIGN.md` sits beside it and calls itself a companion to the spine. It also cites PRD **v0.8.0** while the spine cites **v0.9.0** and the PRD is now **v0.9.1**. Set `companions: [SOLUTION-DESIGN.md]` and reconcile the source versions, or the pair will drift silently.
- `FR-07` and `FR-13` appear in no Capability-map row; `UJ-1`, `UJ-2`, and `UJ-4` appear in no AD's `Binds`. A short traceability appendix listing every bound requirement against its governing AD would make the frontmatter's `binds` claim checkable.
- PRD NFR-12 names an 8 GB VRAM NVIDIA/CUDA baseline that AD-26 (macOS on Apple Silicon only, v1) silently overrides. Record it in Deferred alongside Linux support so no story picks up the CUDA branch.
- The PRD's Decisions Log has a duplicated item 6 under the 2026-08-18 v0.8.0 entry — cosmetic, but it is the document the spine binds to.
