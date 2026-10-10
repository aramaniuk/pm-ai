# pm-ai — Solution Design

**Companion to** `ARCHITECTURE-SPINE.md` (49 ADs) · **Source** `_bmad-output/planning-artifacts/prds/prd-pm-ai-2026-08-18/prd.md` v0.14.2 · **Date** 2026-08-20 · **Last reconciled with the spine** 2026-10-09

---

## How to read this

The **spine** is the build contract: terse, enforceable, and the thing epics and stories must obey. It records decisions and deliberately omits reasoning.

This document is the other half — **why** those decisions were made, what was rejected, where the design diverges from the PRD, and how the pieces behave in motion. Read the spine to build; read this to understand, to argue with, or to remember in six months why something is the way it is.

Where an earlier revision of this document was wrong, it says so rather than quietly reading correctly. Three of its claims described the pre-revision architecture and had become dangerous to follow; two others were simply false.

---

## 1. The shape in one page

pm-ai is a **single long-lived Python daemon** on your Mac. Everything else — the CLI, the Telegram bridge — is a thin client talking to it over authenticated loopback HTTP. That is the design (AD-7, AD-8); the daemon is not built yet, and §2 records what runs in its place today. The daemon is organized **hexagonally**: a core of pure domain logic that knows nothing about GitLab, Telegram, or the filesystem, surrounded by adapters that do.

Traffic across the boundary is **classified**, and each class has exactly one legal home:

- **Connectors** bring the outside world *in* (class H) — read-only by construction, with a closed four-method surface (`harvest`, `emits`, `sample_events`, `check_health` — AD-9), scheduled by the daemon, normalized into a closed event vocabulary, sanitized before anything reaches a model.
- **MCP skills** send changes *out* (class M). They are the only place an external system is ever mutated, and the **model's only route to an external effect**.
- Two further classes exist and are constrained separately: frontier API calls (class F, which can only cause an effect by emitting a tool call that re-enters M), and the local whisper.cpp subprocess (class L).

That classification replaced an earlier, stricter-sounding claim — "MCP skills are the single egress point; nothing else may reach an external system." That sentence was false the day it was written: connectors read, the frontier adapter calls out, transcription spawns a process. A rule contradicted by three paths on day one is a rule that gets weakened to nothing the first time someone hits it.

Underneath, **markdown files are the truth** — but only one of three tiers is disposable:

| Tier | Holds | If you delete it |
| --- | --- | --- |
| **1 — Truth** | markdown segments, the commitments ledger, coaching history, meeting records, the disclosure ledger | restore from backup; this is the record |
| **2 — Operational** | job queue, connector cursors, executed-idempotency-key ledger, staged proposals, the dedup set | **you lose pending external writes and every harvest position.** Not derivable from Tier 1, and no rebuild reconstructs it |
| **3 — Derived** | `event_index.db` (search), `commitment_index.db`, `vector_index/` | nothing — `pm-ai reindex` rebuilds it, each index by a declared job that stores the MD5 of every source entry it derived from (AD-45, AD-46) |

An earlier revision of this document said: *"Delete the database and the vector store, run `pm-ai reindex`, and you lose nothing."* That was true only while Tier 2 did not exist. Following it today destroys the job queue and every cursor. The tiers are now physically separate files precisely so the destructive version of that sentence cannot be executed by accident.

```mermaid
graph LR
    EXT[External systems] -->|"pull — class H"| CONN[Connectors]
    CONN --> NORM["Attribute + normalize (AD-34, AD-36)"]
    NORM --> CORE[Core domain]
    CORE --> T1[("Tier 1 — markdown truth")]
    CORE --> T2[("Tier 2 — operational.db<br/>durable, never rebuilt")]
    CORE --> T3[("Tier 3 — event_index.db, commitment_index.db,<br/>vector_index/ — disposable")]
    CORE --> SAN["Sanitize at ModelPort (AD-12)"]
    SAN -->|"class F"| MODELS[Local / frontier models]
    MODELS -->|"tool call re-enters M"| SKILLS
    CORE --> SKILLS[MCP skills]
    SKILLS -->|"class M — the only mutation"| EXT
    CORE --> SURF[Telegram + CLI]
```

---

## 2. The load-bearing decisions, and why

### One daemon, not scheduled jobs

The PRD's own topology implies it, and the always-on radar plus calendar triggers plus a long-poll connection make it necessary. The alternative — short-lived cron jobs coordinating through SQLite — was rejected because three of the PRD's requirements (the Telegram bridge, the 15-minute pre-meeting trigger, and the offline replay buffer) all want a process that is *already running* when the moment arrives.

The cost is that the daemon becomes a single point of failure. That is bought back by AD-20: every unit of deferred work is a durable row, so a crash loses nothing but time.

**As built, 2026-10.** The daemon does not exist. `pm_ai/surfaces/api/` is a docstring, nothing in the package imports `asyncio`, and every `pm-ai` invocation is the writer. What holds AD-5's single-writer rule in the meantime is cross-process advisory locking — `exclusive()` in `pm_ai/platform/claims.py`, a `flock` on a sidecar the claim never unlinks — which the spine now names as the daemon's substitute `[AD-5, 2026-10-09]`. A whole-file write takes the claim before it reads what it will replace; an append relies on the record rule instead (AD-47); a command that meets a held lock reports `ArtifactBusy` by name rather than retrying silently. Composition is already multi-project: since story 4l (2026-09-24) `wiring.build()` takes every enrolled project as `watched` and builds one connector instance per watched project, so AD-10 holds without the daemon. What is still owed is the daemon itself: wave-2 slices 4e (the process), 4f (the loopback API and the REPL) and 9a (the scheduled tick) turn the paragraph above from design into fact, and 4e decides whether the daemon replaces the lock or keeps it for thin clients that still write.

### Telegram long-polling, not webhooks

The PRD said "webhook/polling" as if they were interchangeable. They are not. A webhook needs a publicly reachable HTTPS endpoint pointing at your laptop, which means a tunnel service — a cloud dependency and an inbound path, directly contradicting NFR-14's zero-public-ports promise, *in the same sentence that made it*. Outbound long-polling needs no inbound port at all. This is the rare case where the stricter security requirement is also the simpler implementation. PRD v0.11.0 now says so in all three places.

### The Tool Runner, not the Claude Agent SDK

The Claude Agent SDK is Claude Code packaged as a library: it ships built-in Bash, Read, Write, Edit, Glob, and Grep tools. Adopting it would mean spending the entire build fighting a library's defaults to preserve the execution firewall.

The Anthropic SDK's **Tool Runner** (`client.beta.messages.tool_runner`) has no built-in tools at all. It loops over exactly the tools you hand it. Point it at the MCP skill registry and the firewall stops being a thing you defend and becomes a thing the architecture simply *is*. The tradeoff is accepted deliberately: `tool_runner` is a beta surface underpinning a security property, so the SDK is pinned exactly rather than floated.

### Four scope kinds, not two

The PRD began with two scopes: personal (`~/.manager-ai/`) and project (`.project-ai/`). Configuration had to live somewhere, and putting per-project connector settings under the personal scope would destroy that scope's defining property — that it survives a job change intact and carries no employer's fingerprints. So `~/.pm-ai/` became the application scope.

The fourth kind arrived only because someone asked a precise question: *where do the results of a 1:1 with a team member go?*

There was no answer. A direct report's career record fits none of the three:

- not `~/.manager-ai/` — that scope travels with you between employers, and its charter is that it never syncs to an HR platform;
- not `<repo>/.project-ai/` — at the time committed in full, so a report's performance objectives would be readable by their peers (and `rules/` and `skills/` still are);
- not `~/.pm-ai/` as it was defined — "system-level state, no personal records".

The tell had been sitting in the PRD's own directory tree the whole time: `team_member_career_mcp.py`, the connector whose entire job is syncing to HR, was filed inside `~/.manager-ai/skills/` — the one scope whose charter forbids exactly that. Four independent review lenses missed it, because each checked the spine against itself.

Records about reports now live at `~/.pm-ai/private/people/`: `0600`-permissioned, gitignored, never committed (it left the encrypted set on 2026-08-27 — see *Encryption that is deliberately partial*), HR-syncable on explicit approval, and **deleted on leaving the role** rather than carried onward. It is stored under the application scope but is its own *kind* in the type system, because two rules turn on telling it apart from personal and neither can be written against a path.

### Storage follows the subject, not the mechanism

The scope model settled into one rule, and it took two passes to see it: **an artifact belongs to the scope that owns its subject.** It already governed log entries; it turned out to govern everything.

Raw meeting transcripts had been filed in the application scope — the one documented as holding *no personal records* — alongside the PM's Telegram voice notes, as though "encrypted blobs the daemon manages" were a category. They aren't. A recording of a team meeting is employer material; a voice note is the PM's own. They now live in different scopes, and a transcript follows its meeting rather than having a home of its own: every scope that owns meetings keeps its captures at `transcripts/`, the way each keeps its own `event_log/` — three of the four; the application scope owns no meetings and holds no captures. The old name, `chat_history/`, described neither a chat nor a history.

That reframing exposed a contradiction that had been live in the architecture: **meeting records sat in the sovereign personal scope**, while every extracted fact cites its meeting and commitments live in the project ledger — then committed in full. Each such commitment referenced personal-scope material by `source_ref` — the precise thing the cross-scope rule forbids. It survived four review lenses because the write guard checked the scope a record *belongs to* and never the scope it *points at*; both are checked now.

**The project scope stopped travelling on 2026-09-03.** Everything under `<repo>/.project-ai/memory/` — `commitments_log.md`, `meetings/`, `event_log/`, `daily_dashboard.md` — is machine-local and gitignored; only `rules/` and `skills/` are committed (AD-3, AD-4). The reason is that a merge falsifies every mechanism that makes the ledger trustworthy: two machines append to the same monthly segment, the whole-file publish clobbers lines pulled in between, the dedup set is per machine so the next harvest re-appends a teammate's events, and sealed segments are declared immutable. The accepted cost is that every project-level aggregation is per-machine. None of this loosens the cross-scope wall: AD-38's invariant dropped its git term and is a pure scope relation, which is why the predicate `is_git_committed` was retired for `is_project`.

### Two 1:1s, two rules

This distinction dissolved an apparent conflict between the privacy charter and the HR integration. The two were never the same data.

| | UJ-1 — PM ↔ pm-ai | UJ-4 — PM ↔ team member |
| --- | --- | --- |
| Subject | you | your report |
| Produces | `CoachingCommitment`, growth notes, burnout signal | `CareerGoal`, performance objectives |
| Scope | `personal` | `people` |
| May sync to HR | **never** | **yes**, on your explicit approval |

Nothing in the charter had to weaken. `CareerGoal` is deliberately *not* a `Commitment`: commitments are verified against execution telemetry and live in the project ledger, and filing a performance objective there would be the same leak in a different costume.

### Encryption that is deliberately partial

NFR-08 said encrypt everything. Taken literally, that would encrypt `coaching_1on1_history.md` and `commitments_log.md` — and the moment those are ciphertext, they stop being greppable, diffable, hand-editable records and become an opaque blob you happen to own.

The narrowed rule, as first written, encrypted six things — credentials, raw transcripts and audio, the Tier-2 operational store, the PM's voice-note cache, the team-member records, and the personal analytics store — and left every `.md` in plaintext. It narrowed again on 2026-08-22 and 2026-08-27, to **exactly two artifacts**: `~/.pm-ai/private/config.json` (credentials) and `~/.manager-ai/private/telegram_cache/` (the PM's own voice notes and dialogue state). Everything else — every `.md`, `operational.db`, `transcripts/` in every scope that holds them, `private/people/`, `personal_analytics.db`, the derived indexes, `connectors/` — is plaintext at `0600` inside `0700`, under FileVault (AD-6). The recorded reasoning: what keeps a capture out of the team's repository is the git guard (AD-43), not a cipher; what protects personal analytics is the scope boundary and the egress rules (AD-25, AD-31); and `operational.db` holds queue state and cursors rather than record content. Tier 3 is plaintext because it holds embeddings and lookup structures rather than recoverable text, and rebuilds from Tier 1. Encryption and tier remain independent axes — one is confidentiality, the other durability — and the pair that proves it is `config.json`, Tier 2 and encrypted, beside `operational.db`, Tier 2 and not. (`personal_analytics.db` is still Tier 2, for the reason given before: its trends outlive the telemetry they were computed from. It is simply no longer encrypted.)

**A correction worth keeping.** This decision was originally justified partly by an unverified claim that SQLCipher and `sqlite-vec` could not be combined. A currency review tested the combination and it *works*. The decision stands on its remaining merits — derived data, rebuildable, no plaintext to protect — but the reason cited at the time was not real. The question then became moot: SQLCipher was dropped altogether on 2026-08-27, because after the narrowing above nothing encrypted is a database any more, and the dependency had no macOS wheel — it would have been a source build for one file (AD-6, Stack). The genuine constraint in this area is different and sharper: `sqlite-vec` cannot load into a stock macOS Python at all, because `enable_load_extension` is absent from those builds. A uv-managed interpreter is required regardless of encryption.

The key sits in the macOS Keychain because the daemon must produce a 07:00 briefing without anyone typing a passphrase. It is enrolled before the first run that writes an encrypted artifact (`pm-ai key enrol`) — pm-ai never mints one — and fetched lazily on first use, so a fresh install boots without it; `pm-ai doctor` tells a missing key apart from an unreachable keychain, and a credential is sealed into `config.json` before the configuration that names it is written (AD-6, AD-47). Key export is the migration path.

### Cost as a gauge, not a governor

NFR-13 stated $20/month as a hard cap. Implemented literally, that means the system degrades or stops working near month-end — which is a worse product than one that costs $25 and tells you so.

The router therefore accounts for every frontier call and warns, but never degrades or blocks. Frontier work is tiered — Opus 5 where reasoning depth *is* the product (Socratic coaching, deep research), Sonnet 5 for briefings, drafts, and inquiry synthesis — which is a config table in the router, not extra machinery. Every call's scope provenance and cost lands in one application-scoped ledger, `~/.pm-ai/disclosure.md`, which is what makes "what has left this machine, and when" an answerable question rather than an assurance.

### One Proposal, not five approval flows

Staged-then-approved appears in at least five PRD requirements: implicit work-item updates, message drafts, HR goal sync, commitments, and the weekly plan. Left unfixed, that is five card formats, five expiry behaviours, and five answers to "what happens if he never taps approve."

One `Proposal` entity with one lifecycle and one renderer collapses it. Features register a type and an executor; they never build a flow. It is kept *separate* from the commitment lifecycle — approval status and real-world fulfilment are different questions, and one overloaded field would eventually conflate them.

---

## 3. Where this diverges from the PRD

All of these are now reconciled in PRD v0.11.0; the table records what changed and why, so neither document drifts back.

| # | PRD originally said | Spine says | Why |
| --- | --- | --- | --- |
| 1 | NFR-08: encrypt all transcripts, indexes, coaching logs, credentials | A defined set; all `.md` plaintext; Tier 3 plaintext | Transparency over one's own record is a product principle |
| 2 | FR-36 + Non-Goals: "signed, statically verified MCP tools" | Execution boundary binding; **signing deferred** to a local allowlist | Single-user, first-party skills. Load path stays pluggable |
| 3 | §2.1: two scopes, config under `~/.manager-ai-private/` | Four scope kinds; app + project config under `~/.pm-ai/` | Keeps the sovereign scope free of employer-specific configuration |
| 4 | NFR-13 + SM-5: spend "capped strictly" | Monitored target, warn-only | A cap that degrades features month-end is worse than knowing the true number |
| 5 | §6: Claude 3.5 Sonnet; Ollama 7B–13B; CUDA baseline | Opus 5 / Sonnet 5 tiered; 8B-class `Q4_K_M`; macOS-only | Claude 3.5 Sonnet retired 2025-10-28; 13B + whisper.cpp will not co-reside at 16GB |
| 6 | FR-37: "synthesized responses within 150 ms" vs NFR-04's 60 s | Retrieval 50–150 ms, synthesis ≤60 s async | No LLM synthesis completes in 150 ms; the two SLAs described different operations |
| 7 | Non-Goal: "all system reads and writes execute via MCP" | Only **model-driven mutations** route through MCP | The blanket version was contradicted by three paths on day one |
| 8 | FR-16: personal data "strictly hardware-bound" | Adversary is employer-controlled systems; frontier APIs are a disclosed, audited exception | The charter promised protection the architecture knowingly does not provide |
| 9 | FR-19 + NFR-14: Telegram "HTTPS webhook/polling" | Outbound long-polling only | A webhook needs the public endpoint NFR-14 forbids in the same sentence |
| 10 | FR-27 + §2.1: one `event_log.md` per scope, holding everything | `event_log/` dated segments; disclosure and cost in a separate application-scoped ledger | Project scope was committed in full at the time, so a disclosure record naming personal material would be pushed to the employer's repo — the separation now rests on the scope relation alone (AD-38, 2026-09-03) |
| 11 | `event_telemetry.db` holds telemetry, job queue, and indexes | `operational.db` (Tier 2) and one Tier-3 index per rebuilding job — `event_index.db`, `commitment_index.db`, `vector_index/` (the single `derived.db` split on 2026-08-27; AD-3, AD-45, AD-46) | One file holding both made the PRD's own tier-scoped NFR-11 unsatisfiable |
| 12 | FR-28: "sandboxed MCP skills" | Registry-authorized with declared permissions | No sandbox is implemented; authorization is not isolation |
| 13 | *(absent)* | Team-member scope for FR-30/FR-31 | A direct report's record had no valid home in any existing scope |

---

## 4. Three flows in motion

### A voice note becomes two sent messages (UJ-2)

```mermaid
sequenceDiagram
    participant A as Andrei
    participant TG as Telegram bridge
    participant Q as Job queue
    participant W as Worker pool
    participant R as ModelRouter
    participant P as Proposals
    participant S as MCP skills

    A->>TG: 20s voice note
    TG->>Q: enqueue transcribe job (durable row)
    TG-->>A: ack (>5s rule)
    Q->>W: whisper.cpp (local only)
    W->>R: extraction (local only)
    R->>P: two draft Proposals w/ cited sources
    P-->>A: card 1 [Send] [Edit]
    A->>P: approve (CAS on version)
    P->>S: dispatch via MCP
    S-->>A: confirmation + event ledger entry
```

The whole path stays local until draft *generation*, the only frontier-eligible step. Transcription, sanitization, and entity extraction never leave the machine.

### A meeting ends (UJ-3, UJ-7)

Transcript arrives via the Graph adapter — or the watched folder, if tenant admin never materializes. Every transcript **binds to a Meeting** or is rejected: an unattributed file must not mint attributed provenance. Sanitization is enforced at the model boundary — the consumer, not the adapter: `ModelPort` accepts externally-sourced text only as `Sanitized`, a producer-side pipeline pass was tried and deleted (story 8e), and the raw record is always retained so citations still resolve (AD-12, AD-29). The derived copy is not confined to model context either — a staged Proposal's summary is built from it.

Extraction then splits by authorization. An earlier revision described that split as *"addressing the assistant by name is the authorization"* — the most dangerous sentence in this document. Anyone in the meeting can say the assistant's name, and anyone who can drop a file into the watched folder can put words in anyone's mouth. Auto-execution now requires **three** independent conditions:

1. the transcript came from a **provider-authenticated source** — a tenant account, not a VTT speaker label;
2. the speaker **resolves to the PM**;
3. the verb is **auto-executable** — registered, reversible, *and* quiet.

Reversibility is not a property of the verb alone: `jira:set_priority` is quiet and auto-executes, while `gitlab:set_priority` is equally reversible but notifies about thirty people, and one-tap undo cannot recall a notification. The registry is keyed on `(provider, verb)` for exactly that reason, and an unregistered verb never auto-executes.

Anything failing any condition becomes a `Proposal`, staged, expiring in 7 days. Irreversible verbs — outbound email, MR creation, closures — always stage regardless.

Approved commitments enter the domain state machine at `PENDING` and are verified against execution telemetry. Two rules govern what counts:

- **pm-ai's own writes are never evidence.** The executor posts a comment to WI-108; the verifier must not later read that comment as proof the promise was kept. Every class-M mutation is recorded with the identifier the provider returned, and normalization marks matching harvested events as self-authored before they reach the ledger.
- **Absence of telemetry is not evidence either — and two silences are different things.** A laptop asleep over a weekend produces no commits. The sweeper consults coverage first, and coverage is a *receipt for an answered request*: a harvest that reached the provider and was told "nothing here" records its window; a harvest that failed, got rows carrying no clock it can believe, or refused every row records none (AD-9, AD-35). With no receipt spanning the window the verdict is `UNKNOWN`, never `BROKEN`, because FR-26's nudges are irreversible and "why isn't this done?" about delivered work is not recoverable. A connector that *attempted* and failed — a dead token, a moved repository — is not missing data but a machine reporting that it is broken, so the verdict set carries a third member, `ERROR` (`pm_ai/domain/lifecycle.py`, 2026-08-28): surfaced by name in `doctor` and the briefing for a human to clear, outranking `UNKNOWN`, never competing with `BROKEN`, and never cleared by waiting. Neither `UNKNOWN` nor `ERROR` may trigger a pre-meeting inquiry to the person who made the promise — pm-ai's own blindness is never someone else's accountability.

### The 07:00 briefing (UJ-9, FR-09) — target design

The scheduler wakes, pulls from the derived indexes (retrieval budget: 50–150 ms), assembles context, and makes exactly one frontier call — Sonnet 5, tiered — to synthesize. It writes `daily_dashboard.md` and pushes a card. Because the personal analytics store (`~/.manager-ai/private/personal_analytics.db`) is a separate database inside the personal scope, burnout signals can inform the briefing while remaining structurally incapable of reaching any project-scope output. The call's scope provenance and cost land in the disclosure ledger.

**As built, 2026-10.** None of that runs yet. `pm-ai dashboard` renders on demand, and `render_dashboard` (`pm_ai/core/rendering.py`) is a pure function over `meetings/`, `event_log/` and `strategic_goals.md` with no model in the path (prototype-path decision 2, 2026-09-01); Leadership Notes renders empty with its reason stated rather than synthesized. There is no scheduler, no derived index behind it, and no Telegram card. The personal dashboard renders wherever the command runs (AD-11, 2026-09-25). Wave 2 adds the 07:00 tick (slice 9a); the synthesized version above also needs the model router (story 7, AD-15) and the declared index jobs (AD-45), neither of which is built.

---

## 5. Cost model

| Path | Model | Rate (per Mtok in/out) | Frequency |
| --- | --- | --- | --- |
| Transcription, extraction, classification, embedding, fuzzy match | Local (whisper.cpp, Ollama) | electricity only | continuous |
| Daily briefing, prep dashboards, drafts, inquiry synthesis | `claude-sonnet-5` | $2 / $10 | several per day |
| Socratic 1:1 coaching, deep research | `claude-opus-5` | $5 / $25 | weekly / on demand |

Sonnet 5's $2/$10 is the **standard** rate: Anthropic made the introductory price permanent on 2026-08-10 and cancelled the increase to $3/$15 previously scheduled for September. An earlier revision of this document told you to re-baseline in September and warned that measurements understated true cost by a third. Both statements were wrong, carried from a stale cached pricing table — a reminder that pricing has a shorter half-life than a version pin and is not registry-backed.

The deliberate design property is that **volume lives on the local side**. Frontier calls are bounded by how often you actually hold a 1:1 or read a briefing — a handful per day — not by telemetry throughput, which is where the volume is. Whether that lands under $20 is exactly what the accounting is there to find out.

---

## 6. Risks and Phase 1 spikes

| Risk | Impact | Handling |
| --- | --- | --- |
| **Graph transcript access needs tenant admin.** Application permissions require an admin-created access policy; personal MS accounts unsupported; transcripts exist only where recording was on | Would block the entire meeting pipeline — roughly half the PRD | `TranscriptSourcePort` with a manual/watched-folder adapter, so the pipeline is testable and shippable without a live tenant. As of 2026-10 the port is a docstring (`pm_ai/connectors/transcripts/__init__.py`) and the Graph and manual adapters are stubs implementing nothing declared; slices 33e and 11b build it (AD-23, recorded as unbuilt 2026-09-23) |
| **A system Python silently breaks all persistence.** `sqlite-vec` needs `enable_load_extension`, absent from python.org and macOS system CPython | Install succeeds, daemon starts, first embedding write fails inside the single writer — passing on the developer's machine, failing on a clean install | `--managed-python` pin plus a `pm-ai doctor` probe. Unguarded, it is a start-up success followed by total storage failure |
| **Concurrent whisper.cpp + Ollama at 16 GB** (PRD Open Question 1) | Swap thrashing; NFR-01/NFR-03 SLAs missed | Bounded pool defaults to one heavy job; the daemon must also constrain the Ollama server, since a client-side semaphore does not unload a resident model |
| **Keychain across an OS or interpreter upgrade is unverified** | The one failure mode here that is silent, unattended, and security-relevant — the 07:00 briefing simply stops | Needs a Phase 1 test |
| **`tool_runner` is a beta surface** underpinning the execution firewall | An SDK change could break the security property, not just the build | Accepted deliberately — it is the only tool loop with no built-in shell. The SDK is pinned exactly, never floated |
| **`sqlite-vec` is pre-1.0 and single-maintainer** (last commit 2026-05-18) | Load-bearing under AD-22 retrieval; a minor bump is a reindex event | Pinned `==0.1.9`. Revisit if the project stalls further |
| **Local model quality for extraction** — anchor matching needs ≥85% confidence | Fuzzy recovery degrades; more `[UNMATCHED_ANCHOR]` prompts | No model pinned. Benchmark `llama3.1:8b` and `qwen3:8b` against real transcripts before locking |
| **Telegram as sole mobile transport** | Availability tied to one third party | Accepted. CLI has full text parity, so no capability is Telegram-only |

---

## 7. Mapping to the PRD roadmap

The spine supports the PRD's four phases without reordering them, but three dependencies are worth naming:

- **Phase 1 must include the storage service, the job queue, and the Proposal entity** even though they are not user-visible. Every later phase assumes them, and retrofitting the single-writer rule after several features already write files would be a rewrite.

  **As built, 2026-10:** the storage service and the Proposal entity exist; the durable job queue does not. `operational.db` holds cursors, coverage windows, harvest failures, executed idempotency keys, the dedup set, proposals and a schema version, and no job table (`pm_ai/storage/service.py`). The queue and its task manager are stories 10/10a, deferred past wave 2, and the derivation layer added on 2026-08-27 now specifies what they must be: declared jobs with declared inputs and outputs (AD-45), invalidated by OS filesystem events behind `FileWatcherPort` and reconciled at boot against each entry's stored source MD5 (AD-46), with every write visible only when complete (AD-47).
- **The transcript-source port belongs in Phase 1**, not Phase 2, because the manual adapter is what makes the Phase 2 meeting pipeline developable while the tenant-permission question is still open. It did not land there: as of 2026-10 the port is still owed by slices 33e and 11b (AD-23).
- **The spine's "Phase 1" and the PRD's §9 Phase 1 are not the same scope.** The spine means "before the contracts can be trusted"; the PRD means a delivery milestone. Read the qualifier, not the word.

---

## 8. Four lessons from the reviewer gate

Four independent lenses reviewed the spine on 2026-08-19, and a fifth reviewed the PRD. The findings that mattered were not about wording.

**A passing test is not evidence that a rule holds.** AD-36 — "pm-ai's own writes are never evidence" — had a green test while the rule was *defeated in code*. The test handed `PM_AI` provenance straight to the evaluator and checked the verdict, proving the downstream half; the step that would have *derived* that provenance from the mutation ledger did not exist, and the only connector hard-coded every harvested event as externally authored. Prefer a test that drives the real path over one that hands in the answer.

**A check nobody checks is a comment.** Two of the spine's load-bearing enforcement rules were bypassable. A single-writer violation written the idiomatic way (`Path(p).open("w")`) scored as a *read*, and a `subprocess.run(shell=True)` in the composition root — the one layer permitted to import everything — was scanned by neither the AST rules nor the import contracts. Both were found by planting real violations against a green suite, not by reading the checks. The standing rule now: a check that cannot be shown to fail on a planted violation is not a check.

**Verify the reviewer too.** Two lenses disagreed about Sonnet 5's pricing; the one that agreed with this document was the one that was wrong, because both it and the document had read the same stale cached table. Convergence between reviewers is not evidence when they share a source.

**Ask the question the documents cannot ask themselves.** The missing scope for team-member records surfaced from a human question about how 1:1s actually work. None of the five lenses found it; each was checking documents against each other.

---

---

## 9. What was deliberately not decided

The spine's Deferred section is the authoritative list, and each entry there carries its own reasoning and revisit condition; this document no longer repeats it. The entries open when this document was written were MCP skill signing (single-user, first-party — revisit on the first foreign skill), Linux and CUDA support (behind ports, later increment), cost-cap enforcement (needs real data first), local model selection (needs benchmarks), multi-user (out of scope by design), real-time in-meeting processing (an explicit PRD Non-Goal), vector index encryption (derived embeddings, rebuildable), retention beyond raw transcripts (needs disk-growth data), Tier-2 schema migration, and the Socratic voice contract (a prompt and product-design decision, not an architectural one). Two have since moved: Tier-2 schema migration was retired as a deferral on 2026-08-22 and built in story 1i (`schema_version`, forward-only migrations, refusal of a newer stamp), and "vector index encryption" widened on 2026-08-27 to the whole derived tier. The spine has gained entries since — backup, the AD-46 coalescing window, AD-45's graph depth, compaction's threshold against monthly segments, Telegram's default project, format and layout evolution, among others — and the reader should take them from there.

The point of naming them is that a builder who hits one knows it is an open question rather than an oversight — and knows the condition under which it gets revisited.
