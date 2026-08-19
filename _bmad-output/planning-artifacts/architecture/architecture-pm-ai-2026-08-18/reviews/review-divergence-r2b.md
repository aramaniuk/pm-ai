# Adversarial Divergence Review — ARCHITECTURE-SPINE.md (r2b)

**Reviewer:** independent, no prior project context (by design)
**Target:** `_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md` (status: revision-in-progress, updated 2026-08-19)
**Also read:** `tests/architecture/` (`README.md`, `conftest.py`, `test_static_rules.py`, `test_domain_invariants.py`, `test_layering.py`), `.importlinter`, `SOLUTION-DESIGN.md`
**Date:** 2026-08-19

---

## Verdict

The spine is unusually strong on *mechanism* and unusually weak on *meaning*: AD-34 through AD-37 fixed the identity, clock, attribution, and concurrency machinery, but the fields that machinery operates on — `scope`, `payload`, `canonical_payload`, `target_ref` granularity, "reversible", "coverage", "the event log" — are still shared as **words**, not as **types**, and several of the newest ADs have unassigned owners at exactly the moment that matters (who writes the alias table, who resolves a two-transcript meeting, which event log receives a class-M record). I found **21 pairs of units that each obey every written AD to the letter and would still build incompatibly**, plus **3 outright internal contradictions** and a substantial list of ADs that are stated but unenforced where enforcement is mechanically trivial. Notably, the enforcement suite that the spine calls "executable, not just readable" is currently **neither run nor runnable** — there is no `pyproject.toml`, `pytest` and `lint-imports` are not installed, and every AST check passes vacuously over eleven empty `__init__.py` files.

The document is not build-ready, and the reason is not the 121 open findings it already names. It is that a second builder, six weeks in, reading only this document, has ~21 legal ways to diverge from the first.

---

## Method

For each hole I construct two units one level down — two features, two connectors, two stories, or two developers — that:

1. each satisfy **every** AD in the document as written;
2. would nonetheless produce artifacts that do not compose;
3. fail in a way that is **silent** (the dangerous class) or **irreversible** (the expensive class).

A pair is only reported if I could not find AD text that forbids either side. Where the SOLUTION-DESIGN companion answers a question the spine leaves open, I say so — but the spine is the build contract ("the thing epics and stories must obey"), so an answer that lives only in the companion is not an answer.

---

# Part 1 — Divergence pairs

## Cluster A — Shared word, not shared type

### D-1 · `scope` means four different things, and one of them is attacker-selectable

**Unit A — the frontier disclosure logger.** Implements AD-31.2: records `scopes` on every frontier call, as the set of *scope kinds* that contributed material — `{"personal"}`, `{"project"}`, `{"app"}`. Obeys AD-31, AD-15, AD-17, AD-4. This is exactly what `test_ad31_every_frontier_call_records_scope_provenance` asserts (`assert "personal" in e["scopes"]`).

**Unit B — the normalization layer's `source_ref` builder.** Implements AD-34.1: `source_ref` is `<system>:<scope>:<kind>:<native_id>`, e.g. `gitlab:alpha:commit:9f2a1c`. Here `scope` is a **project slug**. Obeys AD-34, AD-27, AD-9, AD-10 (`ConnectorInstance = (scope, connector_type, config, cursor)`).

**The incompatibility.** The spine uses the word `scope` in four incompatible senses and never types any of them:

| Usage | Meaning | Where |
|---|---|---|
| `~/.pm-ai/` vs `~/.manager-ai/` vs `.project-ai/` | storage-ownership kind | AD-4, AD-25, AD-28 |
| `gitlab:**alpha**:commit:…` | project identifier | AD-34.1 |
| `ConnectorInstance = (**scope**, …)` | either of the above — undecided | AD-10 |
| "each declaring the **scopes** it may exercise" | OAuth-style permission grant | AD-18 |

AD-31's destination boundary is then checked as a string comparison (`destination="project:alpha"` in the test) against a set whose members may be `"personal"` *or* `"alpha"`. AD-10 explicitly permits personal-scope connector instances, so a personal-scope article connector produces `rss:personal:article:…` — meaning the second segment's namespace mixes project slugs with the literal `personal`.

**The production failure.** AD-11 lets the user run `pm-ai project add` with any name. Register a project named `personal`. Now `gitlab:personal:issue:88` is a project-scope ref whose scope segment is indistinguishable from personal-scope material. AD-31's destination check (`personal material must never enter a prompt bound for a project artifact`) passes on a string match, AD-25's separation test (`"manager-ai-private" not in datasource`) passes, and personal burnout signals flow into a team-facing prep dashboard. The privacy charter's central claim is defeated by a project name. Both units are fully compliant; neither is wrong.

**Closing AD (new — AD-38, Scope is a type, not a string).**

> **Rule:** `Scope` is a closed sum type in `domain`: `AppScope | PersonalScope | ProjectScope(project_id)`. No component may represent a scope as a bare string, and no API accepts one. In the AD-34 reference grammar the second segment is the reserved token `_app`, `_personal`, or a `ProjectId`; `ProjectId` is validated at `pm-ai project add` against a reserved-word list containing at minimum `_app`, `_personal`, `app`, `personal`, and must match `^[a-z][a-z0-9-]{1,62}$`. The word "scope" is retired from AD-18: an MCP skill declares `grants`, not scopes. Every boundary decision (AD-25, AD-28, AD-31.3) compares `Scope` values, never rendered strings; a comparison performed on a rendered scope string is a defect.

---

### D-2 · `NormalizedEvent.payload` has no schema, so a closed type enum buys nothing

**Unit A — the GitLab connector.** Maps an MR merge to the core type `work_item_closed` with `payload = {"key": "PAY-102", "mr_iid": 42, "sha": "9f2a1c"}`. Obeys AD-27 (used an existing core type, minted none), AD-34 (grammar, resolved actor, no minted id), AD-9, AD-12.

**Unit B — the Notion connector, added in Phase 3.** Maps a database-row status flip to the *same* core type `work_item_closed` with `payload = {"page_id": "…", "status": "Shipped"}`. Obeys AD-27, AD-34, AD-9, AD-12 identically.

**The incompatibility.** AD-27 closes the *type* enumeration and AD-34 fixes `source_ref` and `actor_id` — and then the envelope carries a free-form `payload` that no AD owns. FR-34's commitment verifier must decide "was the thing closed?" from a dict whose keys are chosen by whichever adapter author wrote it. The verifier written against A reads `payload["key"]`; B's events sail past it.

**The production failure.** Exactly the failure AD-27's own *Prevents* claims to have closed — "commitment verification misses evidence from one of them" — arriving through the payload rather than the type. Commitments backed by Notion evidence never leave `PENDING`, age past their due date, and (per AD-35's coverage rule, which sees *coverage*, because the Notion connector harvested successfully) transition to `BROKEN`. FR-26 then fires an irreversible "why isn't this done" nudge about delivered work. `test_ad27_connectors_only_emit_core_declared_event_types` is green throughout.

**Closing AD (tighten AD-27).**

> **Rule (append):** Each member of `NormalizedEventType` declares a **typed payload schema** — a frozen dataclass in `domain` — and `NormalizedEvent.payload` is an instance of that type, never a `dict`. A connector that cannot populate every required field of a type's schema **may not emit that type**; it emits the nearest type it can fully populate, or nothing, and logs the drop. Core services read only declared schema fields; reading a payload by string key is a defect. Adding or making-required a field on an existing payload schema is a schema-version bump under AD-39 (below) with an explicit backfill rule for stored events.

---

### D-3 · `canonical_payload` is a word; two canonicalizations both pass the idempotency test

**Unit A — `skills/post_comment_workitem.py`.** Computes the AD-20 key as `sha256(job_type + target_ref + json.dumps(payload, sort_keys=True))`, over the full payload including the originating `proposal_id`.

**Unit B — `skills/send_email_draft.py`.** Computes it as `sha256(job_type + target_ref + msgpack(model.model_dump(exclude_none=True)))`, over a payload from which `proposal_id` and the generated `drafted_at` line were excluded as "not semantically part of the message".

Both obey AD-20 (deterministic, derived from `(job_type, target_ref, payload_hash)`, never random) and the Conventions row verbatim. **Both pass `test_ad20_idempotency_keys_are_deterministic`** — that test only asserts each function is stable within and across processes, never that two skills agree on what "canonical" means or which fields count.

**The incompatibility.** Three separate divergences, all legal:

1. **Serialization.** JCS-sorted JSON vs. msgpack of a filtered model produce different bytes for identical semantics.
2. **Field selection.** Including `proposal_id` means the *same comment text re-staged after an edit* gets a new key and posts twice. Excluding it means *two genuinely distinct approvals of identical text* collapse to one key and the second silently no-ops while the surface reports success.
3. **Concatenation.** `job_type + target_ref` with no separator: `("post", "comment:gitlab:…")` and `("post_comment", ":gitlab:…")` hash identically. A cross-job-type collision is a comment posted where an email was queued.

**The production failure.** Duplicate customer-facing emails, or an approval that reports "sent" and sent nothing. Both are the *precise* failure AD-20 exists to prevent, and both survive the test the README singles out as "the one to keep if you ever keep only one."

**Closing AD (tighten AD-20).**

> **Rule (replace the key definition):** `idempotency_key = sha256(b"pmai-idem-v1" || 0x00 || job_type || 0x00 || target_ref || 0x00 || jcs(key_fields))`, where `jcs` is RFC 8785 JSON Canonicalization Scheme and `key_fields` is the projection of the payload onto the **key-field list declared at proposal-type / skill registration**. The registry refuses to load a class-M skill that does not declare its key-field list. Fields excluded by construction: surrogate ids (`prp_`, `job_`, `cmt_`), generation timestamps, and any field the executor does not transmit externally. One implementation of this function lives in `pm_ai.core.jobs` and is the only one; a skill computing its own key is a defect. The executed-key ledger records `(key, outcome, resulting_ref)`; a key present with `outcome=success` short-circuits and returns the recorded `resulting_ref`, and surfaces must render "already executed" distinctly from "executed now".

---

### D-4 · The AD-37 per-target lock key has no fixed granularity

**Unit A — `skills/post_comment_workitem.py`.** Locks on `target_ref = "gitlab:alpha:issue:PAY-102"`.

**Unit B — `skills/set_labels_workitem.py`.** Locks on `target_ref = "gitlab:alpha:issue:PAY-102#labels"` — finer-grained, and defensible: two label writes conflict, a label write and a comment do not. Both refs parse under AD-34's grammar (nothing forbids a fragment), both obey AD-37 ("serialize through a per-target lock keyed by `target_ref`"), AD-20, AD-1 class M.

**The incompatibility.** Two different lock keys for one external entity means no mutual exclusion at all. Worse: a third skill implementing FR-28 (create MR, which also closes PAY-102) reasonably locks on `gitlab:alpha:mr:42`. Three writers, three keys, one entity.

**Compounding gap.** Even with an agreed key, the lock is **local**. Nothing in the spine requires external optimistic concurrency. Any read-modify-write skill — editing a description, replacing a label set, updating an HR goal — will silently clobber a concurrent human edit made in GitLab's UI, and AD-36's attribution ledger will record pm-ai's write as authoritative.

**The production failure.** A PM's manual edit to a work item description is overwritten by an approved proposal three seconds later. The `event_log.md` shows a clean class-M entry. There is no diff, no conflict, and no evidence anything was lost.

**Closing AD (tighten AD-37).**

> **Rule (append):** The lock key is the **canonical entity ref** — `<system>:<scope>:<kind>:<native_id>` with no fragment, sub-path, field suffix, or query. A class-M skill declares its `target_entity_ref` at registration and the registry derives the lock key; a skill may not choose its own. A mutation that affects a second entity (e.g. an MR that closes an issue) declares both and acquires both locks in canonical-ref lexical order. Separately: any skill performing a read-modify-write on an external field **must** carry the provider's concurrency token (ETag / `If-Match` / version / `updated_at`) from the read into the write and fail the job on mismatch rather than overwriting. A skill whose provider offers no such token registers as `blind_write: true` and may only **append** (comments, notes) — never replace a field.

---

### D-5 · "Reversible" is a property of a verb name, not of what the provider actually does

**Unit A — `skills/set_priority_workitem.py` (Jira).** Implements AD-32's reversible verb `priority`: sets `fields.priority`. Registered in the allowlist per AD-18. Auto-executes when source, speaker, and verb qualify. Emits an undo card.

**Unit B — `skills/set_priority_workitem.py` (GitLab).** Same verb, same enum member. In GitLab, priority is a scoped label; setting it also moves the board card and triggers the project's Slack integration webhook to the team channel.

Both obey AD-32 to the letter — the verb is in the closed `domain` enumeration, the source is a tenant-authenticated Graph transcript, the speaker resolves to the PM. Both obey AD-1 class M, AD-18, AD-13, AD-20.

**The incompatibility.** AD-32 defines reversibility in the *domain*, on the verb, once, for all providers. Reversibility is in fact a property of `(verb, provider, entity state)`. Jira's priority change on a closed issue fires a workflow transition and mails every watcher; GitLab's fires a Slack post. "One-tap undo" restores the field and cannot unsend either.

**The production failure.** A sentence spoken in a meeting — no approval, no human in the loop, correctly authorized under every AD — notifies thirty people. The undo card is offered and is a lie. This is precisely the blast radius AD-32's *Prevents* is about, reached through a verb the AD blessed.

**Closing AD (tighten AD-32).**

> **Rule (append):** Reversibility is a property of the **(verb, skill) pair**, not the verb. Every class-M skill declares, per operation, `reversibility: reversible | irreversible`. `reversible` requires (a) a registered inverse operation implemented in the same skill, and (b) an explicit assertion that the operation emits no third-party notification, no workflow transition, and no state change outside the named entity. Absent either, the operation is `irreversible`. The auto-execute-eligible set is computed by the registry as the **intersection** of AD-32's `domain` verb enumeration and the set of operations declaring `reversible`; it is never the verb enumeration alone. An undo card may only be rendered when a registered inverse exists.

---

## Cluster B — Ownership the ADs leave unassigned

### D-6 · Two transcripts, one meeting: identity race plus authorization escalation

**Unit A — the Graph transcript adapter.** Receives the tenant transcript for the 10:00 planning call, binds it to calendar event `msgraph:alpha:event:AAMk…`, mints `meeting:mtg_01HX`. Obeys AD-23 (bound to a Meeting), AD-33 (Meeting is a first-class Tier-1 record), AD-34, AD-32 (provider-authenticated source ⇒ auto-execute eligible).

**Unit B — the manual watched-folder adapter.** The PM exports the same call's `.vtt` and drops it in the folder. No calendar reference is supplied, so per AD-23 "the drop supplies title, start, and attendees to mint the record" — it mints `meeting:mtg_01HY`. Obeys AD-23, AD-33, AD-32 (never an auto-execute source), AD-12, AD-29.

**The incompatibility, part 1 — duplicate identity.** AD-34 fixes the natural key for `NormalizedEvent` as `(source_system, source_ref)`. `Meeting` is not a `NormalizedEvent`. **No AD defines a natural key for `Meeting`, `Commitment`, `Proposal`, or `Actor`.** With `source_system` differing (`msgraph` vs `manual`), no dedup rule can collapse them even if one existed. FR-03's Man-Hour Cost double-counts the meeting (attendees × duration, twice). Commitments extracted from both transcripts duplicate; one auto-executes and one stages, so the PM sees a proposal for a change that already happened.

**The incompatibility, part 2 — the escalation.** This is the serious half. AD-33 deliberately erases the transcript from provenance: facts cite `meeting:<id>` + speaker, and `Meeting` carries a **singular** "derived-transcript pointer". AD-32 keys auto-execute on "the transcript came from a provider-authenticated source". Once a Meeting has two transcripts, "the transcript" is ambiguous:

- Developer X resolves trust at **extraction time** and stamps it on the Extraction. Safe.
- Developer Y resolves trust at **execution time** by dereferencing `Meeting.transcript_pointer`. Both are compliant readings of AD-32 + AD-33.

Under Y, anyone who can write a file into the watched folder — a synced folder, a Downloads-directory drop, a malicious `.vtt` attachment — drops a transcript naming a Graph-bound meeting, becomes bound to that Meeting, and their spoken commands inherit the Graph transcript's provider-authenticated trust level. AD-32's *Prevents* names this attacker verbatim ("anyone able to write a file into the watched folder, obtaining unapproved external write access") and the Rule does not close it, because it reasons about transcripts while AD-33 makes decisions reason about meetings.

**Closing AD (new — AD-40, Meeting identity and per-transcript trust).**

> **Rule:** (1) `Meeting` has a natural key: `calendar_event_ref` where one exists, otherwise `sha256(normalize(title) || start_utc_rounded_5min || sorted(attendee_actor_ids))`. Transcript ingestion resolves against that key **before** minting; a second transcript for an existing Meeting attaches as an additional capture and never mints a Meeting. Meeting minting is a compare-and-swap on the natural key under AD-37.
> (2) A `Meeting` holds **a set** of transcript captures, each carrying its own `source_trust ∈ {provider_authenticated, manual}` and `source_system`.
> (3) **Trust is resolved at extraction time and frozen on the Extraction**, never re-derived from the Meeting at execution time. An Extraction's `source_trust` is the trust of the specific capture it was extracted from. AD-32's first condition reads `extraction.source_trust`, never `meeting.*`.
> (4) A manual capture attaching to a Meeting that already holds a provider-authenticated capture is accepted for extraction and staging, and **downgrades nothing and upgrades nothing** — it cannot cause re-extraction of already-executed commands.
> Natural keys are mandatory for every entity that two adapters can independently produce; the entity list and its keys live in `domain`.

---

### D-7 · AD-36's attribution match: `target_ref` vs `resulting_ref`

**Unit A — the skill layer.** Per AD-36, records every class-M mutation in the Tier-2 ledger: `{"target_ref": "gitlab:alpha:issue:PAY-102", "external_id": "note_88123"}`. This is exactly what `test_ad36_every_class_m_mutation_is_recorded_for_attribution` asserts (`m["target_ref"].endswith("WI-102")`).

**Unit B — normalization.** Per AD-36, "marks any harvested event matching one of those as `pm_ai`." Implements the match on `target_ref`, because that is the field the ledger reliably has.

Both obey AD-36, AD-34, AD-1 class M, AD-20.

**The incompatibility.** The harvested comment arrives with `source_ref = gitlab:alpha:note:88123`, whose `kind` and `native_id` differ from the recorded `target_ref`. Two compliant matching strategies, both catastrophic in opposite directions:

- **Match on `target_ref`** (B's choice): *every* subsequent event on PAY-102 is marked `pm_ai`, including the engineer's actual fix commit and the ticket closure. AD-36 then discards all of it. The commitment never reaches `FULFILLED`, ages out, and — with harvest coverage intact — trips AD-35's sweeper to `BROKEN`. An irreversible FR-26 nudge fires about work that was demonstrably delivered.
- **Match on `external_id` only**, where the connector's `native_id` format happens not to match the skill's captured id (GitLab returns a note `id`, the connector normalizes on `noteable_iid`): nothing matches, nothing is marked `pm_ai`, and the loop closes on itself — the exact failure AD-36 exists to prevent, with its test green.

**The production failure.** In one direction the ledger is confidently wrong toward success; in the other the ledger is confidently wrong toward failure and sends an irreversible message. AD-36's "two mechanisms because one of them will have gaps" does not help: the second mechanism (bot identity) only applies "where the connector runs under a distinct bot identity", which for a PAT-authenticated GitLab connector acting as the PM is exactly never.

**Closing AD (tighten AD-36).**

> **Rule (replace the attribution paragraph):** The skill layer records, for every class-M mutation, a `resulting_ref` in full AD-34 grammar identifying the **artifact it created or changed** (`gitlab:alpha:note:88123`), not only the `target_ref` it acted upon. Attribution matching is **exact equality on `resulting_ref`** and never on `target_ref`. A class-M skill that cannot obtain a `resulting_ref` from its provider registers `attribution: unavailable`; its mutations mark subsequent events on the target entity, within a bounded window of the mutation's `occurred_at`, as `authored_by = unknown`. `unknown` is a third value: it is displayed, and it **counts as neither evidence nor self-authorship** — it suppresses both `FULFILLED` and `BROKEN` for that commitment, which surfaces as `UNVERIFIED` (see D-10). Fail-closed in both directions is the requirement; guessing in either is not.

---

### D-8 · The actor alias table has no owner, no tier, and no backup

**Unit A — normalization.** Per AD-34.2, resolves the native handle through an alias table and **stores the resolved `actor_id` on the persisted event** ("Every event carries an `actor_id` resolved to a single `Actor`"). Obeys AD-34, AD-5 (storage is the writer), AD-27.

**Unit B — `pm-ai actor alias add "Unknown Speaker 3" alex`.** The CLI command that makes AD-34's `unresolved` actor actionable. Adds a mapping. Obeys AD-34, AD-5, AD-8, AD-11.

**The incompatibility.** Neither AD says what happens to the three months of events already persisted with `actor_id = UNRESOLVED`. Both readings are compliant:

- **Forward-only** (the natural implementation, and the only one consistent with AD-5's append-only Tier-1 markdown, which forbids in-place edits of history): historical events stay unresolved forever.
- **Retroactive rewrite**: violates AD-5's append-only rule for Tier 1.

**Compounding gap — the tier is undefined.** AD-3 places every piece of persistent state in exactly one tier and the alias table is in none of them.
- If **Tier 3** (derived): `pm-ai reindex` destroys it, and every manually-established alias silently reverts to `unresolved`. The AD-3 rebuild test still passes, because the rebuild is faithful — the input was lost.
- If **Tier 2** (operational): AD-3 says "must be backed up", but the Deployment section's backup procedure is "markdown scopes only … plus an exported keychain key." The alias table is not backed up under either reading.

**The production failure.** FR-30's custom metrics and FR-31's career dossiers compute "commits by Alex" from stored `actor_id`. Half of Alex's history is missing, permanently and invisibly — the exact failure AD-34's *Prevents* names ("one engineer arriving as a commit email and a speaker label and becoming four people in the metrics that feed a performance review"), arriving through the alias table's lifecycle rather than through resolution itself. Worst case, a reindex resets it and the dossier changes between two readings of the same command.

**Closing AD (tighten AD-34).**

> **Rule (append to 34.2):** The alias table is **Tier 1**: an append-only plaintext markdown record at `~/.pm-ai/identity_aliases.md`, hand-editable, git-diffable, and a named backup target. Persisted events store **both** the connector's raw native handle and the resolution as of ingest. **Actor resolution for all derived output is applied at fold/read time against the current alias table**, never frozen at persist — so adding an alias retroactively corrects all history without editing Tier 1. Adding, changing, or removing an alias appends an `event_log.md` entry; any metric or dossier already emitted to an external artifact before the change is flagged for re-issue. Alias resolution is part of the AD-3 Tier-3 rebuild input, so `pm-ai reindex` reproduces current-alias-resolved state, not ingest-time state.

---

### D-9 · Which `event_log.md`? — and the disclosure record lands in the team's git history

**Unit A — the work-item skill.** Per AD-1 class M ("one `event_log.md` entry per invocation"), appends its invocation record to the **project-scope** `.project-ai/memory/event_log.md` for the target project. Obeys AD-1, AD-4, AD-24, AD-27.

**Unit B — the HR goal-sync skill (FR-31).** Triggered from a personal-scope career dossier, mutating an HR platform. Appends its invocation record to the **personal-scope** `~/.manager-ai/memory/event_log.md`. Obeys AD-1, AD-4 (this is not project configuration), AD-24, AD-28 (a personal undertaking belongs in the personal scope).

**The incompatibility.** At least three `event_log.md` files exist (personal, per-project, and the app scope implied by AD-25's `~/.pm-ai/private/`), and **no AD routes an entry to one of them.** Consequences:

- AD-31.2's CLI answer to *"what has left this machine, and when"* must union an unbounded set of logs, one per registered project plus personal. If A and B disagree about routing, the audit is incomplete — and an incomplete privacy audit reports *clean*.
- AD-36's attribution matcher reads executed-mutation records. If they are scattered by scope and normalization reads only one, self-authored events are counted as evidence.

**The production failure — worse than divergence.** `.project-ai/` is **git-committed** (AD-4, Structural Seed). AD-31.2 requires every frontier call to record *scope provenance* to `event_log.md`. If a briefing that drew on both personal and project material writes its disclosure record to the project event log, then the line *"scopes: personal, project:alpha — task_class: briefing_synthesis — model: claude-opus-5 — 14,200 tokens"* is committed and pushed to the employer's repository. The audit mechanism built to enforce FR-16 becomes the leak: it tells the employer, in git history, that personal coaching material informed the PM's work — against an adversary AD-31 explicitly names as "employer-controlled systems". Token counts and timing across a project's history are also an inference channel about 1:1 frequency and intensity.

**Closing AD (new — AD-41, Event-log routing is fixed in `domain`).**

> **Rule:** Every `event_log.md` entry type declares its destination scope in `domain`; no component chooses.
> (1) A **class-M invocation record** is written to the event log of the scope that owns the target entity, derived from the `Scope` segment of `target_ref` (AD-38).
> (2) An **AD-31 frontier disclosure record** is written **only** to the app scope, `~/.pm-ai/private/disclosure_log.md`, never to any committed or personal scope. It carries scope *names*, task class, model, token counts, and timestamps — never prompt content and never a personal `source_ref`.
> (3) A project-scope `event_log.md` may not contain any entry whose `scopes` include `PersonalScope`. The storage service rejects such a write; this is mechanically testable and must be tested.
> (4) The CLI's "what has left this machine" reads exactly one file — the app-scope disclosure log — so its completeness is a property of the writer, not of an enumeration the reader has to get right.

---

### D-10 · Coverage: whose coverage, and where does "I don't know" go?

**Unit A — sweeper implementation X.** Per AD-35, refuses `BROKEN` where it has no coverage. Requires coverage from **any** connector instance in the commitment's project scope. Compliant.

**Unit B — sweeper implementation Y.** Same AD. Requires coverage from **every** instance in the project scope. Compliant.

**The incompatibility.** AD-35 says "the commitment sweeper must not declare `BROKEN` across a window it has no coverage for" and never says *whose* coverage is relevant to a given commitment. For a commitment "close PAY-102 by Friday", the evidence lives in Jira:

- X: GitLab harvested fine all week, so coverage exists ⇒ declares `BROKEN` even though the Jira connector was down and the ticket was in fact closed on Wednesday. **Irreversible nudge on delivered work — the exact scenario AD-35's *Prevents* describes, unclosed.**
- Y: any single connector instance failing anywhere in the project suppresses every `BROKEN` verdict indefinitely. FR-34's closed loop silently becomes decorative; nothing ever fails, so nobody notices.

**Direct contradiction, not just divergence.** AD-14 fixes the commitment state machine as `PENDING → FULFILLED | ALTERED | BROKEN`. AD-35 forbids `BROKEN` without coverage. **There is no state for "overdue, no coverage, unknown."** The sweeper is required to withhold a verdict it has nowhere to put, so a compliant implementation either lies (`BROKEN`) or is silent (leaves `PENDING`, which renders identically to "on track"). AD-36's `unknown` attribution (D-7) needs the same missing state. This is an internal contradiction between two ADOPTED ADs.

**Closing AD (tighten AD-14 and AD-35).**

> **AD-14 (Rule, replace the state machine):** `PENDING → FULFILLED | ALTERED | BROKEN | UNVERIFIED`. `UNVERIFIED` means *the system cannot see enough to judge* — missing harvest coverage across the window, or evidence attributed `unknown` under AD-36. It is a distinct, surfaced state, rendered distinctly from `PENDING`, and it is not terminal: restored coverage re-evaluates it.
>
> **AD-35 (Rule, replace the coverage bullet):** Every `Commitment` records `evidence_instances: set[ConnectorInstanceId]` at creation, derived from the `system` and `Scope` segments of its `target_ref`. A transition to `BROKEN` requires **continuous coverage from every instance in `evidence_instances`** across `[created_at, due_date]`; anything less yields `UNVERIFIED`. Coverage is recorded as closed intervals in `occurred_at` space **claimed by a completed harvest lease** (AD-42), never as "a harvest ran": a harvest that errors mid-page records coverage only for the pages it actually retrieved. An `UNVERIFIED` commitment older than twice its window escalates to the PM as a question ("I can't see Jira — did PAY-102 close?"), never as an FR-26 nudge. Irreversible outbound messages require a verdict, and `UNVERIFIED` is not one.

---

### D-11 · AD-31's scopes are self-reported, so the audit can affirmatively lie

**Unit A — the 07:00 briefing assembler.** Pulls goals from `~/.manager-ai/` and telemetry from `.project-ai/`, calls `router.route("briefing_synthesis").complete(prompt=…, scopes={"personal","project:alpha"}, destination="personal")`. Writes `daily_dashboard.md` in the personal scope. Fully compliant with AD-31.

**Unit B — the FR-32 pre-meeting prep dashboard.** Needs the same personal goal context to frame the meeting. Declares `destination="project:alpha"` and the router raises `ScopeBoundaryViolation` (as `test_ad31_personal_material_cannot_reach_a_project_destination` requires). The developer "fixes" it: core summarizes the personal goals into a string *before* the router sees anything, and the call becomes `complete(prompt=summary_containing_personal_material, scopes={"project:alpha"}, destination="project:alpha")`. **Green.** Every AD obeyed — AD-31 never says the declared scopes must be true, and nothing can check a `str`.

**The incompatibility.** `scopes` and `destination` are arguments the call site supplies, exactly like `task_class` in AD-15. The entire model data boundary is a self-report. AD-31.2 calls the disclosure log "an audit" — but an audit derived from the auditee's own declaration is a receipt, not an audit.

**The production failure.** Personal burnout and career material reaches a team-facing prep document, and the disclosure log records the call as project-only. The system does not merely fail to prevent the leak; it **produces written evidence that the leak did not occur**. AD-31.1's own argument — "a charter that means something narrower than its words is worse than no charter" — applies to its own mechanism.

**Closing AD (tighten AD-31).**

> **Rule (append, obligation 4):** Scope provenance is **carried, not declared**. Every retrieval result and every context fragment is a `ScopedFragment(scope: Scope, text: str, source_ref: SourceRef)` minted by the storage service at read time; `Scope` is the tier the bytes came from and no component may construct a `ScopedFragment` with a scope it did not read from. `ModelPort.complete(fragments: Sequence[ScopedFragment], task_class, destination)` computes the scope union itself and enforces AD-31.3 on the computed union. The port **accepts no raw prompt string** — a call site holding text it cannot attribute to a fragment cannot make a model call. Prompt assembly (templating, summarization, truncation) operates on fragments and propagates their scopes. Any core function that converts a `ScopedFragment` to a bare `str` before the port is a defect and is AST-checkable.

---

## Cluster C — Partial failure and half-succeeded operations

### D-12 · A multi-step executor has no partial state and no per-step key

**Unit A — the FR-28 "create MR" proposal type.** Per AD-13, registers a type with a payload schema and **one** executor callback. The callback does: create branch → create MR → post a comment linking the commitment. One durable job row, one idempotency key per AD-20 (`job_type="create_mr"`, one `target_ref`, one payload hash). Compliant.

**Unit B — the FR-31 "HR goal sync" proposal type.** Same AD-13 registration, but the author implements it as three chained jobs — create goal, link commitment, post confirmation — each with its own key. Also compliant: AD-20 requires every deferred unit of work to be a durable row, which arguably *favors* B.

**The incompatibility.** AD-13's status enum is `staged → approved → executed | edited | rejected | expired`. **There is no state for "three steps, two done."**

- **A half-succeeds** (branch created, MR creation 502s). The job fails, so the executed-key ledger never records the key as executed, so at-least-once retries the *whole callback* — and GitLab branch creation is not idempotent. Second branch. Third on the next retry. The idempotency key was correct and bought nothing, because it was scoped to a compound operation whose sub-steps have independent external effects.
- **B half-succeeds** at step 2. Steps 1's HR goal exists in Workday. The proposal sits at `approved` forever — not `executed`, not failed, invisible in the approval queue (which renders `staged`). Nobody learns that an orphan HR goal exists.
- AD-1 class M requires "one `event_log.md` entry per invocation." A produces one entry for three external effects; B produces three for one logical proposal. The audit trail is not comparable across proposal types, and a reader cannot reconstruct what happened.

**The production failure.** Three duplicate branches on a work item, or an orphan HR goal linked to nothing, in both cases with an approval queue and an event log that show no problem.

**Closing AD (tighten AD-13 and AD-20).**

> **Rule:** An executor is a declared **sequence of steps**. Each step is an independently persisted job row with its own AD-20 key over its own `(job_type, target_ref, key_fields)`, its own recorded outcome, and its own `event_log.md` entry — so "one entry per invocation" means one entry per *external call*, uniformly. A `Proposal` transitions to `executed` only when every step records success. A step failing after ≥1 step succeeded transitions the Proposal to **`partially_executed`**, a surfaced non-terminal state that enumerates completed and pending steps, is never auto-retried past the failed step boundary, and appears in the approval queue requiring a human decision (resume / compensate / abandon). Every step must be individually idempotent against its provider; a step whose provider offers no idempotency mechanism must perform a **read-check-write** — query for its own prior effect by `resulting_ref` or a deterministic marker before mutating.

---

### D-13 · Two harvests race one cursor; coverage records a window nobody fetched

**Unit A — the 4h scheduler.** Per AD-9/AD-10, invokes `harvest(since=cursor)` on each instance on cadence, persists the returned cursor, records coverage per AD-35. Compliant.

**Unit B — `pm-ai sync --now`.** A thin-client CLI command that asks the daemon to harvest an instance immediately. Obeys AD-7 (the daemon does the work, the CLI holds no state or scheduling), AD-8, AD-9 (the connector still owns no timer), AD-11.

**The incompatibility.** AD-37's compare-and-swap covers "**Proposal and Commitment** transitions" — explicitly those two entities. Cursors are not covered by any concurrency rule anywhere in the spine. Both harvests read cursor `C0`, both fetch overlapping windows, both write back. If the slower-but-later writer persists the *older* cursor, the window between is skipped. Event dedup by natural key (AD-34.3) protects against the duplicate case and does nothing for the skipped case.

**The production failure.** A permanent, silent harvest gap — and, critically, **coverage is recorded for a window whose events were never fetched**, because both harvests "succeeded". AD-35's coverage guard, the last defense against an irreversible FR-26 nudge, now attests to data that does not exist. The sweeper looks at a fully-covered week, sees no fix commit, and declares `BROKEN` on delivered work. The mechanism that exists to fail closed fails open, and the two mechanisms that would have caught it (dedup, coverage) both report healthy.

**Closing AD (new — AD-42, harvest leases and cursor CAS).**

> **Rule:** A harvest runs under an **at-most-one-in-flight lease per `ConnectorInstance`**, held by the scheduler, with a timeout and an owner. A second request for a leased instance is coalesced onto the running harvest — never run concurrently, never silently dropped. Cursor advancement is a compare-and-swap on `(instance_id, cursor_version)`; a write from a stale lease is rejected. Coverage (AD-35) is recorded **only** on a lease that completes successfully, and only over the `occurred_at` interval the connector attests it fully retrieved: a harvest that errors mid-pagination records coverage for retrieved pages and none for the remainder. AD-37's compare-and-swap requirement extends to every entity two writers can reach — Proposal, Commitment, Meeting, ConnectorInstance cursor, and the executed-key ledger — not only Proposal and Commitment.

---

### D-14 · Tier 2 is required to be backed up and is excluded from the backup procedure

**Unit A — `pm-ai backup`.** Implements the Deployment & operations section verbatim: "markdown scopes only — project scope rides in git; personal scope may be its own private git repository — plus an exported keychain key. Encrypted indexes are explicitly **not** a backup target."

**Unit B — `pm-ai restore` / the recovery runbook.** Implements AD-3 verbatim: Tier 2 is "durable and **not derivable from Tier 1**. Must be backed up." Assumes cursors, the job queue, and the executed-idempotency-key ledger survive a restore.

Both quote the document. **This is a direct internal contradiction**, not an ambiguity — AD-3 mandates a backup target that the operations section explicitly excludes, and AD-3's own *Prevents* is about precisely this class of mistake.

**The production failure.** Restore after a disk loss. Cursors are empty ⇒ every connector re-harvests from the beginning (recoverable, thanks to AD-34.3). The **executed-key ledger is empty** ⇒ every `PENDING_RETRY` job and every replayed job believes it has never executed. AD-20's determinism is worthless without the ledger: a deterministic key is only a dedup mechanism if something remembers which keys are spent. Result: a burst of duplicate external mutations — re-posted comments, re-sent emails, re-created MRs — on restore day, at exactly the moment the user's trust in the system is most fragile.

**Closing AD (tighten AD-3).**

> **Rule (append):** Tier 2 is a **named backup target**. `pm-ai backup` produces (a) the markdown scopes, (b) the exported keychain key, and (c) an encrypted Tier-2 snapshot — job queue, cursors, executed-key ledger, staged proposals. `pm-ai restore` refuses to start the scheduler until it has reported the Tier-2 snapshot's age and the operator has confirmed. Independently, **every executed-key ledger entry is mirrored as an `event_log.md` entry** (Tier 1, append-only, `class_m_executed` type) carrying key, `target_ref`, `resulting_ref`, and outcome — so the at-least-once guarantee survives a total Tier-2 loss by rebuilding the spent-key set from Tier 1. The Deployment section is corrected to match; the operations text and AD-3 are one decision, and the AD wins.

---

## Cluster D — Ordering and causality

### D-15 · Deterministic sort key, non-deterministic fold: late-arriving events flip decided states

**Unit A — the live commitment service.** Applies transitions as events arrive, maintaining derived commitment state incrementally. Sorts by `(occurred_at, entry_id)`. Compliant with AD-35, AD-14, AD-5.

**Unit B — `pm-ai reindex`'s ledger fold.** Reads the full ledger and folds from scratch, sorted by `(occurred_at, entry_id)`. Compliant with AD-35 and passes `test_ad35_ledger_folding_is_deterministic` (which only checks that `fold(entries) == fold(reversed(entries))` — invariance to *input* order, not agreement between an incremental and a from-scratch fold).

**The incompatibility.** AD-35 fixed the **sort key** and left open **whether the fold is incremental or from-scratch**, and said nothing about **retroactive insertion**. `entry_id` is a ULID minted at persist (AD-34.3), so it orders by *ingest*; `occurred_at` orders by *world time*. A connector outage, a sleeping laptop, or a provider backfill produces an entry whose `occurred_at` sorts **before** entries already folded. A's incremental state can never see it in its correct position. B's from-scratch fold does.

**The production failure.** The MR that fulfilled a commitment is harvested three days late. A has already declared `BROKEN` and — because FR-26's nudges are irreversible — **already sent the "why isn't this done" message**. B's rebuild folds the late evidence into position and yields `FULFILLED`. `pm-ai reindex` therefore changes commitment states, which is the exact failure AD-35's *Prevents* names, arriving through the fold's *mode* rather than its *key*. The user sees the state silently flip with no explanation and an unretractable message already sent to the team.

**Closing AD (tighten AD-35).**

> **Rule (replace the folding bullet):** Commitment state is a **pure from-scratch function of the entry set**, computed by one fold implementation used identically by the live path and by `reindex`. No component maintains incrementally-mutated commitment state; derived commitment state is Tier 3 and always recomputed. An entry whose `occurred_at` precedes the last `effect_watermark` recorded for its commitment is a **retroactive entry**: it is folded normally, and if it changes a state on which an irreversible effect was already emitted, the fold must append a typed `state_retraction` entry (closed enumeration, AD-27) naming the prior state, the new state, the retroactive evidence, and the effect already emitted. Irreversible effects — FR-26 nudges, class-M writes — are gated on `occurred_at > effect_watermark` for that commitment; the watermark advances only when an effect is emitted. Silent convergence is prohibited: a state that changed after the world was told about it must say so.

---

### D-16 · A proposal approved on day 6 executes on day 11, and every AD is satisfied

**Unit A — the expiry sweeper.** Per AD-37: "the sweeper CASes `staged → expired`." Sweeps `staged` proposals past their TTL. Compliant with AD-13 (scheduler owns expiry) and AD-37.

**Unit B — the job worker.** Per AD-37: "a worker re-checks state **at execution time**, not only at enqueue time," and "the worker CASes `approved → executing`." The proposal was approved on day 6; the laptop went offline; the job sat in `PENDING_RETRY` (AD-20, AD-3 Tier 2) for five days. On day 11 the worker re-checks, finds `approved` — a perfectly valid non-terminal state, never touched by the sweeper because the sweeper only expires `staged` — and executes. Compliant.

**The incompatibility.** AD-37's *Prevents* names this scenario in so many words: "the expiry sweeper racing the job worker so that an **eleven-day-old approved change posts after expiry**, despite AD-13 stating that expired proposals never execute." The Rule then solves a *different* problem — the CAS race between two concurrent transitions — and leaves the staleness itself untouched. AD-13's "an expired proposal never executes" is technically true and operationally empty, because approval removes a proposal from the only clock that exists.

**The production failure.** The customer email drafted for Tuesday's escalation sends the following Saturday. The MR opens against a branch deleted at sprint close. The HR goal syncs against a review cycle that ended. Every one is an irreversible class-M effect, and the PM's mental model — "I approved that ages ago, it obviously went out or obviously didn't" — is wrong in the worst direction.

**Closing AD (tighten AD-13 and AD-37).**

> **Rule:** Both the *approval* and the *effect* have deadlines. A registered proposal type declares `staged_ttl` (default 7 days, per AD-13) **and** `execution_ttl` (default 24 hours from `approved_at`, and never more than 72). The worker's execution-time CAS `approved → executing` additionally asserts `now - approved_at ≤ execution_ttl`; on breach the proposal CASes to **`stale`** (terminal) and the core mints a **new** proposal in `staged` with the same payload, `supersedes: prp_<old>`, and a rendered note that the original approval aged out. Jobs whose proposal has gone `stale` are cancelled by the same sweeper, and a `stale` proposal never executes. The sweeper's remit is every non-terminal state with an elapsed deadline, not `staged` alone.

---

## Cluster E — Migration and versioning

### D-17 · `authored_by` did not exist in Phase 1, and its default is catastrophic in both directions

**Unit A — the Phase 3 ledger parser.** AD-36 is `[NEW]`; Phase-1 `event_log.md` entries carry no `authored_by`. A defaults missing → `external`, reasoning that pm-ai barely wrote anything early on. Obeys AD-36, AD-27 ("versioned so parsers can read historical entries"), and the Conventions rule that "parsers must tolerate hand-edits."

**Unit B — the Phase 3 commitment verifier.** Same field, same absence, defaults missing → `pm_ai`, reasoning fail-closed. Equally compliant.

**The incompatibility.** AD-27 says both enumerations are "versioned so parsers can read historical entries" and **assigns nobody the job of deciding what a missing field means.** Two parsers of the same Tier-1 file produce opposite answers on the field that gates every commitment transition.

**The production failure.** Under A, every Phase-1/2 pm-ai comment counts as fulfilment evidence — AD-36's headline failure ("the ledger becomes confidently wrong in the direction that looks like success") reintroduced wholesale for all pre-AD-36 history. Under B, every genuine Phase-1 commit and merge is discarded as self-authored; no historical commitment can ever reach `FULFILLED`, and the sweeper — seeing coverage — marks the backlog `BROKEN` and fires nudges about a year of delivered work. This same hole applies to every field the newest ADs added to already-written data: `ingested_at`, `coverage`, `actor_id`, the AD-34 `source_ref` grammar (Phase-1 refs may be free-form URLs, which AD-34 says are "rejected at normalization" — rejecting stored history is not a migration), and the pinned embedding dimension.

**Closing AD (new — AD-39, Stored-shape versioning and conservative backfill).**

> **Rule:** Every markdown ledger entry header and every persisted row carries `schema_version`. When a parser encounters an entry whose `schema_version` predates a field's introduction, the field's value is resolved by an **explicit, per-field backfill rule declared in `domain`** — never by a language-level default, a `dict.get(k, default)`, or an optional-type fallback. For any field that gates an **irreversible** behaviour (`authored_by`, `occurred_at`, coverage, `source_trust`), the declared backfill is the **conservative** value, and the conservative value is a distinct third value — `unknown` — which suppresses both positive and negative transitions and surfaces as `UNVERIFIED` (AD-14). Historical `source_ref` values that predate the AD-34 grammar are migrated by a declared, one-time, recorded normalization, not rejected. A field added to a persisted shape without a declared backfill rule fails the build; this is mechanically testable and must be tested.

---

### D-18 · Which copy gets indexed — and the retrieval path is a second, unguarded door into a prompt

**Unit A — the indexer.** Embeds the **raw** payload. Reasoning: AD-29 says the sanitized copy is "used *exclusively* for model context", and the vector index is storage, not model context; and AD-3 requires Tier 3 to rebuild from Tier 1, which holds the raw. Compliant with AD-29, AD-3, AD-5.

**Unit B — the indexer.** Embeds the **sanitized derivative**. Reasoning: AD-15 makes `embedding` a task class, so embedding *is* a model call, so AD-12 and AD-29 apply. Equally compliant.

**The incompatibility, part 1 — the security hole.** Under A, injection text is embedded, retrieved, and — via AD-22's retrieval path feeding AD-22's synthesis path — assembled verbatim into a frontier prompt. AD-12 guards "every payload crossing an **inbound adapter boundary**". Retrieval is not an inbound adapter boundary; it is a **read from our own storage**. The sanitization filter runs exactly once, at ingest, and everything downstream is trusted. A hostile MR description sanitized on the way in is stored raw (AD-29 requires this), indexed raw (A's compliant choice), retrieved raw, and injected into the 07:00 briefing prompt — which, per AD-16, is a Tool Runner loop with the MCP skill registry attached. The prompt-injection path terminates in class-M egress.

**The incompatibility, part 2 — the rebuild.** Under B, `pm-ai reindex` must re-run sanitization to rebuild the index. If the filter changed between the original ingest and the rebuild — and it will, because sanitization filters are patched — the rebuilt embeddings differ from the live ones. `test_ad3_indexes_rebuild_from_markdown_without_loss` compares snapshots and fails; or, worse, it is relaxed to a fuzzy comparison and retrieval quality silently changes across a reindex with nothing reporting it. Separately: the spine never places the sanitized derivative in any AD-3 tier, so whether it is stored at all is undecided, and if it is not stored, it is recomputed on every model call — non-deterministically if the filter uses the `classification` task class (AD-15), which is exactly how an injection classifier would be built.

**Closing AD (tighten AD-12 and AD-29).**

> **Rule:** The sanitized derivative is **Tier 3**, stored alongside the raw, keyed to it, and stamped with `sanitizer_version`. **Everything downstream of the adapter boundary consumes only the derivative** — indexing, embedding, retrieval snippets, prompt assembly, and `ScopedFragment` text (AD-31/D-11). Only citation resolution, drift audit, and human/audit display read the raw. The sanitization filter is **deterministic and pure** — rule-based, no model call, no network — so a rebuild reproduces the index exactly; a bump to `sanitizer_version` is a declared reindex event recorded in `event_log.md`. AD-12's scope is restated: sanitization is a property of *text entering a model context*, enforced at the adapter boundary **and** re-asserted by the fragment builder, which refuses any fragment whose `sanitizer_version` is absent.

---

## Cluster F — Surface parity and the approval envelope

### D-19 · `edited` has no defined semantics, and one reading double-sends

**Unit A — the Telegram card handler.** "Edit" mutates the proposal's payload in place, keeps the id, leaves status at `staged`, and sets `status = edited` only on… nothing, since the enum lists `edited` as a sibling of `executed` and never says whether it is terminal or whether an `edited` proposal can be approved. Obeys AD-13's enum as written.

**Unit B — the CLI approval queue.** "Edit" CASes the original `staged → edited` (terminal) and mints a new `staged` proposal with the revised payload. Obeys AD-13 and AD-37 ("terminal states are terminal" — which never enumerates the terminal set).

**The incompatibility.** AD-13 mandates "one card renderer serves both surfaces" and then leaves the *state semantics behind the button* undefined. Under A, mutating the payload changes `payload_hash`, which changes the AD-20 idempotency key. If the original was already approved and dispatched, the edited version carries a different key and executes as a **new** external mutation. Under B, the same user action supersedes cleanly.

**The production failure.** The PM approves a draft reply on the CLI, then opens Telegram, edits a typo, and taps approve. Two emails reach the customer, both keyed differently, both logged as legitimate class-M invocations, and the per-target lock (AD-37) serializes them into a tidy sequence rather than preventing either. This is the AD-37 headline scenario ("approving the same proposal from Telegram and the CLI and creating two HR goals") surviving AD-37, because the CAS protects the *transition* and nothing protects the *payload*.

**Closing AD (tighten AD-13 and AD-37).**

> **Rule:** A `Proposal` payload is **immutable once staged**. Editing is `staged --CAS--> edited` (terminal) plus minting a new `staged` Proposal carrying `supersedes: prp_<old>` and the revised payload. Only `staged` accepts an edit; an `approved` proposal must first be revoked (`approved --CAS--> rejected`, permitted only while zero steps have executed under D-12) before a replacement is staged. The terminal set is fixed in `domain` as `{executed, edited, rejected, expired, stale}` and is imported from that one definition by the sweeper, the worker, and both surfaces — no component enumerates terminal states locally.

---

### D-20 · One card renderer, two surfaces, one of which has hard limits

**Unit A — the FR-31 HR goal-sync proposal type.** Registers a card with 12 actions (approve, reject, edit, plus per-goal toggles) and a 6,000-character payload preview showing the full dossier text. Obeys AD-13 (registered a type with a payload schema and an executor; built no approval flow), AD-7, AD-21.

**Unit B — the FR-06 work-item update proposal type.** Two actions, a 300-character preview. Equally compliant.

**The incompatibility.** Telegram's constraints are hard: 4,096 characters per message, 64 bytes of `callback_data`, and a practical inline-keyboard button budget. A renders perfectly in the CLI and truncates or fails to send on Telegram. AD-7 forbids a feature existing on only one surface, and AD-13 mandates one renderer — but **no AD specifies the card's capability envelope**, so a registered type can be legal and unrenderable.

**The production failure.** Telegram is the PM's primary surface (the mobile UJs). He sees a truncated card — the visible half of a dossier — and taps Approve. An approval given without sight of what is being approved defeats the entire purpose of the Proposal mechanism: dual authorization degrades to single authorization with extra steps, and the `event_log.md` records a fully-consented approval. If the developer instead packs payload data into `callback_data` to fit the buttons, the 64-byte limit truncates it and the wrong goal syncs.

**Closing AD (tighten AD-13).**

> **Rule (append):** The Proposal card contract is defined by the **most constrained surface**: ≤4,000 rendered characters, ≤6 actions, and action tokens ≤64 bytes carrying only a `prp_` ULID plus an action code — never payload data. A registered proposal type whose card cannot render within the envelope is **rejected at registration**, not at send time. A payload that cannot be shown in full within the envelope must render a summary plus a mandatory `view full` action; on a truncated card the approve action is **disabled** until the full payload has been fetched on that surface, and that fetch is recorded on the proposal. The renderer emits the same envelope for both surfaces so a card that works in the CLI provably works on Telegram.

---

### D-21 · The 5-second rule measures execution, not queue wait

**Unit A — the retrieval endpoint.** `dispatch.plan(estimated_seconds=0.15)` → inline. Correct: AD-22 says retrieval is SQLite plus vector lookup, no model.

**Unit B — the `classification` endpoint.** A local Ollama call, measured p50 of 2.1 seconds. `dispatch.plan(estimated_seconds=2.1)` → inline. Compliant with AD-21 ("requests under 5 seconds may answer inline") and AD-15 (`classification` is local-only) and AD-19 (it goes through the bounded pool).

**The incompatibility.** AD-19 bounds the pool at **one heavy local-model job at a time**. B's 2.1-second call must acquire that single slot. If a 60-second whisper.cpp transcription holds it — the common case, since transcription is the system's highest-volume heavy job — B blocks inline for 62 seconds. AD-21's estimate is of *execution* duration; nothing in the AD mentions **queue wait**, and the test (`dispatch.plan(estimated_seconds=…)`) takes the number from the caller.

**The production failure.** The loopback HTTP request times out; the CLI reports a daemon failure; the user retries, adding another job to the same single-slot queue. Under AD-19's `keep_alive: 0` + `OLLAMA_MAX_LOADED_MODELS=1` regime the retry also forces a model reload. A correctly-implemented 2-second operation becomes an apparent daemon hang, and the two developers' surfaces behave differently for reasons neither can see in the code.

**Closing AD (tighten AD-21).**

> **Rule (append):** The 5-second test applies to **expected end-to-end latency including queue wait**, not execution time. Any operation that acquires the AD-19 heavy-local-model semaphore, or that makes a frontier call, is **asynchronous by construction** regardless of its own duration. Inline is permitted only for paths that touch neither the worker pool nor a frontier model — in practice, AD-22 retrieval and pure in-memory core operations. `dispatch.plan` takes the operation's declared **resource set**, not a caller-supplied second count; a caller-supplied duration is not evidence about a shared resource.

---

# Part 2 — Internal contradictions found along the way

Distinct from divergence pairs: these are places where two parts of the document cannot both be obeyed.

| # | Contradiction | Where | Detail |
|---|---|---|---|
| C-1 | AD-14's state machine has no state for AD-35's mandated verdict | AD-14 vs AD-35 | The sweeper is forbidden to say `BROKEN` without coverage and has nowhere else to put the result. See D-10. |
| C-2 | AD-3 requires Tier 2 to be backed up; Deployment & operations excludes it | AD-3 vs "Backup" | "Must be backed up" vs "markdown scopes only … plus an exported keychain key." See D-14. |
| C-3 | AD-37's *Prevents* names a failure its *Rule* does not close | AD-37 | "an eleven-day-old approved change posts after expiry" — the Rule addresses the CAS race, not the staleness. See D-16. |

A fourth, softer one: the Enforcement section asserts "The spine is executable, not just readable," and `tests/architecture/README.md` names "zero skips" as the Phase 1 exit criterion — while the repository has **no `pyproject.toml`**, so neither `uv run pytest tests/architecture` nor `uv run lint-imports` can execute at all today (both fail with "Failed to spawn"). Every AST check would additionally pass **vacuously**: `pm_ai/` contains sixteen files, all `__init__.py`, none longer than 259 bytes. The README acknowledges the vacuity risk in its "Enforcement-layer corrections" note; the un-runnability appears to be unnoticed.

---

# Part 3 — ADs stated but unenforced, where enforcement is mechanically possible

The README's own "Not mechanically enforced" list (AD-4, AD-10, AD-12, AD-18, AD-19) is honest but understates the gap in two ways: several of those *are* mechanically enforceable in part, and a dozen ADs listed as *enforced* have their load-bearing clause untested. Ordered by consequence.

## Tier 1 — the clause that matters is untested

| AD | Stated but unenforced | Mechanically possible check |
|---|---|---|
| **AD-20** | **That the key is honoured.** Both tests check key *stability* and *presence*; nothing tests that replaying an already-executed key does **not** re-invoke the provider. This is the entire property. | Invoke a fake class-M skill twice with one key against a recording double; assert exactly one provider call and that the second returns the recorded `resulting_ref`. |
| **AD-36** | **The matching direction.** `test_ad36_every_class_m_mutation_is_recorded_for_attribution` proves the skill layer *writes* a record. Nothing proves normalization *reads* it — i.e. that a harvested event corresponding to a recorded mutation actually comes back `authored_by="pm_ai"`. That is the half where D-7 lives. | Record a mutation with a `resulting_ref`, feed the matching harvested event through normalization, assert `authored_by == "pm_ai"`; feed a non-matching sibling event on the same target and assert `authored_by == "external"`. |
| **AD-1 (class H)** | **"A connector that mutates is a defect."** `.importlinter` grants `pm_ai.connectors` unrestricted HTTP; no check restricts the verb. | AST scan of `pm_ai.connectors` for `.post(`, `.put(`, `.patch(`, `.delete(`, and `method=` constants other than `GET`/`HEAD`. Cheap, high value. |
| **AD-1 (class M)** | **"One `event_log.md` entry per invocation."** Untested. | Invoke a fake skill against a recording log; assert exactly one appended entry of the class-M type. |
| **AD-2** | **The bind address.** The test checks `bridge.TRANSPORT` and the absence of `webhook_handler`; nothing checks "binds strictly to `127.0.0.1`" or "zero public listening ports" — the actual NFR-14 property. | Assert the uvicorn/FastAPI config host is `127.0.0.1`; AST scan for `"0.0.0.0"`, `"::"`, and empty-string host literals anywhere in `pm_ai`. Also AST-ban `run_polling(` and `run_webhook(` (a Stack prerequisite with no check). |
| **AD-3** | **That `reindex` does not touch Tier 2** — the precise failure AD-3's *Prevents* describes. The rebuild test covers Tier 3 only. | Snapshot job queue + cursors + executed-key ledger, run `reindex`, assert byte-identical. |
| **AD-13** | **"No external mutation derived from implicit extraction may execute without an approved Proposal."** Untested. | Registry-level: a class-M invocation whose provenance is `implicit_extraction` and whose `approved_proposal_id` is absent must raise. |
| **AD-37** | **Commitment CAS and the per-target lock.** Both are named in the Rule; both tests cover Proposal only. | Mirror `test_ad37_concurrent_approval…` for `Commitment`; drive two concurrent class-M invocations at one `target_ref` and assert serialization. |
| **AD-35** | **Coverage computation.** `test_ad35_sweeper_will_not_declare_broken_without_coverage` passes `coverage_gap=True` in as a parameter — it tests the branch, not that coverage is derived from recorded harvest windows. | Record real coverage intervals from a fake scheduler run with a gap, then assert the sweeper derives `UNVERIFIED` without being told. |

## Tier 2 — enforceable, currently absent

| AD | Gap | Check |
|---|---|---|
| **AD-12** | Listed as "a review catch." It is typeable. | Introduce `RawPayload` and `SanitizedText` as distinct domain types; AST/type check that no `ModelPort` call site and no indexer accepts `RawPayload`. Plus an end-to-end probe: a known injection marker fed through each registered inbound adapter must never appear in a captured prompt. |
| **AD-18** | Listed as "runtime by the registry." The registry's *behaviour* is testable even if the allowlist's *contents* are not. | Assert `registry.invoke("unlisted.skill")` raises and logs; assert an out-of-grant call raises; assert every module under `pm_ai/skills/` appears in the allowlist manifest (catches a skill shipped without registration). |
| **AD-19** | Listed as "needs load testing." The three *documented* server-side settings do not. | Assert the Ollama adapter sets `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, and `keep_alive: 0` before dispatching a heavy job; assert the pool bound defaults to 1; AST-check that whisper/Ollama invocation sites go through the pool's submit function rather than being awaited on the loop. |
| **AD-10** | The README concedes "shape is testable." Nothing tests the shape. | Assert `ConnectorInstance` carries a required `Scope`; assert two instances of one `connector_type` in different scopes receive distinct cursor keys. |
| **AD-4** | Partly judgement — but path construction is not. | AST-ban literal `~/.manager-ai`, `~/.pm-ai`, `.project-ai` strings outside `pm_ai.storage.paths`; force every write through `storage.path_for(scope, kind)`. |
| **AD-9** | The port *signature* is unchecked; only scheduling calls and cursor opacity are. | Runtime-checkable `Protocol` conformance across `registry.all_connectors()`: exactly `harvest(since: Cursor) -> list[NormalizedEvent]`. |
| **AD-30** | "Core services receive their dependencies; they never construct or locate them" — imports are contracted, construction is not. | AST-ban instantiation of any name ending in `Adapter`, and any module-level service-locator lookup, inside `pm_ai.core`. |
| **AD-27** | "Both enumerations are versioned" — untested. | Assert `NormalizedEventType.VERSION` and the log-entry enum version exist; parse a checked-in golden file of historical entries at an older version. |
| **AD-5** | WAL mode is stated and untested; "single writer" is checked as absence-of-writes-elsewhere, not as one connection. | Assert `PRAGMA journal_mode == "wal"`; assert exactly one write connection is constructed. |
| **AD-6** | The default-on property, the CLI banner, and the `event_log.md` entry when encryption is off are all untested. | Assert a fresh-install config has encryption on; assert toggling off produces both the banner and the log entry. |
| **AD-8** | The `0600` token-file mode is untested. | Assert the created token file's mode is `0o600`. |
| **AD-7** | "Telegram and CLI must reach identical functionality" — the strongest parity claim in the document, and unenforced. | Surface-parity test: the set of core operations reachable from the CLI command router equals the set reachable from the Telegram command router. This is the check that catches a feature landing on one surface. |
| **AD-15** | "A call without a declared task class is a defect" (Conventions) — untested; and a *dynamic* task class would defeat routing audit. | Assert `ModelPort.complete` without `task_class` raises; AST-check that every call site passes a literal enum member, never a variable. |
| **AD-16** | Import contracts block the SDKs; nothing asserts the Tool Runner's tool set comes from the registry. | Assert `frontier.build_tools()` returns only registry-authorized skills and is never constructed from a literal list. |
| **AD-24** | The AST check greps for `.debug(`/`.info(` textually near `event_log`; a logger bound through a variable escapes it. | Make debug output *unrepresentable*: the event-log append API accepts only a typed `EventLogEntry` from the AD-27 closed enum. Assert the API has no `str` overload. |
| **AD-29** | The sanitize *function* is tested; the *pipeline* is not — nothing asserts that no path persists `for_model` as the stored payload. | Run a payload through ingestion and assert the persisted Tier-1 bytes equal the raw input. |
| **AD-31** | Coverage of the frontier path is untested — one adapter method that forgets to log defeats the audit. | Make the disclosure write structurally unavoidable in the adapter and assert with a monkeypatched log that no completion path returns without one. Plus: assert no disclosure record is ever written to a project or personal scope (D-9). |
| **AD-33** | "`Meeting` is a first-class Tier-1 record" — untested. | Add `Meeting` to the AD-3 rebuild harness: wipe Tier 3, rebuild, assert Meetings reconstitute from Tier-1 markdown. |
| **AD-23** | Tests `manual.requires_network is False`; does not test the stated property that extraction runs end-to-end on the fallback alone. | Run the extraction pipeline with the Graph adapter unregistered and assert it completes. |
| **Stack** | Every pin is prose; the document notes one pin was already fabricated once. | A dependency-manifest test asserting `sqlcipher3` (not `-binary`), `sqlite-vec==0.1.9` exactly, `python-telegram-bot==22.8` without the `job-queue` extra, and `hasattr(conn, "enable_load_extension")` — the last mirroring the `pm-ai doctor` probe so CI fails on a stock interpreter. |
| **Suite hygiene** | AST checks pass vacuously on the current skeleton, and the suite cannot run at all (no `pyproject.toml`). | Add the manifest and dev deps. Add a guard test that fails when a layer contains only `__init__.py` after a Phase-1 marker exists — "zero skips" does not catch a vacuously-green AST pass. |

## Also noted

`ARCHITECTURE-SPINE.md` Open Risks references `reviews/TRIAGE.md` for "~121 findings across 12 root causes." **The `reviews/` directory did not exist** before this review created it, and no `TRIAGE.md` is present anywhere in the repository. The document's own gating statement ("not build-ready until they are closed") points at a file that cannot be read.

---

# Part 4 — Prioritized close list

Ranked by (irreversibility × silence). The top five all end in an irreversible external effect that no test would catch.

| Rank | Pair(s) | Closing change |
|---|---|---|
| 1 | **D-6** | New **AD-40** — Meeting natural key + per-capture trust frozen at extraction. This is a privilege escalation, not a data-quality bug: a dropped file inherits tenant-authenticated auto-execute authority. |
| 2 | **D-7, D-10, D-17** | Tighten **AD-36 / AD-35**, extend **AD-14** with `UNVERIFIED`, add **AD-39** (conservative backfill). One coherent change: introduce a third value (`unknown` / `UNVERIFIED`) so every fail-closed rule has somewhere to put its answer. Removes C-1. |
| 3 | **D-3, D-12, D-14** | Tighten **AD-20 / AD-13 / AD-3**. Canonical key definition + declared key-fields + per-step jobs + `partially_executed` + Tier-1 mirroring of the executed-key ledger. Removes C-2 and the duplicate-external-write class entirely. |
| 4 | **D-1, D-9, D-11** | New **AD-38** (`Scope` as a type), new **AD-41** (event-log routing), tighten **AD-31** (carried fragments). One coherent change: make the privacy boundary a type the compiler enforces rather than a string the caller declares. |
| 5 | **D-16, D-19** | Tighten **AD-13 / AD-37**: `execution_ttl`, `stale`, immutable staged payloads, one `domain`-owned terminal set. Removes C-3. |
| 6 | **D-13, D-4, D-5** | New **AD-42** (harvest lease + cursor CAS + CAS for every multi-writer entity); tighten **AD-37** (canonical lock ref + external concurrency token) and **AD-32** (per-skill reversibility). |
| 7 | **D-2, D-18** | Tighten **AD-27** (typed payload schemas) and **AD-12/AD-29** (derivative is Tier 3; everything downstream reads it; filter is pure). |
| 8 | **D-8, D-20, D-21** | Tighten **AD-34** (alias table is Tier 1, resolved at read time), **AD-13** (card envelope), **AD-21** (queue wait counts). |

**Suggested sequencing note.** Ranks 1–5 introduce five new ADs and touch eleven existing ones — but every one of them either (a) closes a contradiction the document already contains, or (b) closes a failure whose *Prevents* clause is already written in the spine and whose *Rule* does not reach it. None of them is new scope. That the spine already names most of these failures and misses them by a clause is the strongest signal that the ADs are one revision short rather than one paradigm short.
