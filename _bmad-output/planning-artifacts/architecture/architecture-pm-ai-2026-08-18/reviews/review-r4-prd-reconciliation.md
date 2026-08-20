# Review R4 — PRD ↔ Spine Reconciliation Audit

**Date:** 2026-08-19
**Source input:** `_bmad-output/planning-artifacts/prds/prd-pm-ai-2026-08-18/prd.md` (v0.10.0, 802 lines)
**Derived artifact:** `_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md` (653 lines)
**Question:** (1) Did the architecture run's deliberate divergences get pushed back into the PRD? (2) What did the AD structure silently drop?

Line numbers are as-of the versions above. Every claim below quotes both documents.

---

## HALF 1 — Status of the seven flagged divergences

| # | Flag | Verdict |
| --- | --- | --- |
| a | NFR-08 encryption scope | **PARTIAL** — principle reconciled, storage artifact names stale and now self-contradictory |
| b | FR-36 signing deferred | **RECONCILED in FR-36 and Glossary; NOT in Non-Goals**; plus a terminology relapse AD-18 explicitly forbids |
| c | §2.1 / §6 three-scope model | **RECONCILED** |
| d | NFR-13 / SM-5 monitored target | **RECONCILED in NFR-13 and SM-5**, but NFR-13 routes cost records to the wrong ledger (contradicts f) |
| e | §6 model naming | **RECONCILED** (stale mention survives only in a superseded Decisions Log entry) |
| f | FR-27 disclosure ledger + segmented event log | **PARTIAL** — FR-27 reconciled; §2.1, §6, and 8 other requirements still say `event_log.md` (single file) |
| g | FR-16 privacy charter | **NOT RECONCILED** — the Glossary says it, FR-16 does not, and FR-16's actual wording contradicts the spine |

---

### (a) NFR-08 encryption scope — PARTIAL

**Spine, AD-6 (line 130):**
> Only these are encrypted: `operational.db` (Tier 2, SQLCipher), `chat_history/` and `telegram_cache/` (envelope-encrypted files), and `config.json` (credentials). **All `.md` files in every scope stay plaintext by design** … The Tier-3 artifacts `derived.db` and `vector_index/` are unencrypted

**Spine, AD-3 tier table (lines 98–99):**
> | 2 | `~/.pm-ai/private/operational.db` (SQLCipher) | **never** | **yes** |
> | 3 | `~/.pm-ai/private/derived.db`, `vector_index/` | yes | no |

**PRD, NFR-08 (line 671):**
> Encrypted at rest with AES-256 and 600 file permissions: the operational telemetry index (event\_telemetry.db), raw meeting transcripts and voice notes (chat\_history/), the mobile conversation cache (telegram\_cache/), API credentials (config.json), and the personal analytics store in \~/.manager-ai-private/. **Explicitly not encrypted:** (a) all Markdown files in every scope … and (b) the vector index

**Assessment.** The *principle* is fully reconciled: plaintext `.md` everywhere, plaintext vector index, keychain custody, debug toggle with banner + log entry. Those are verbatim matches of AD-6's intent.

**What did not reconcile:** the PRD still names a single `event_telemetry.db`. The spine split that file in two — precisely because AD-3's `Prevents` clause names the old single-file design as the contradiction it exists to kill:

> **Spine AD-3, line 84:** "the earlier version's own contradiction — it called `event_telemetry.db` disposable while the job queue, connector cursors, and idempotency ledger lived inside it"

`grep -c "event_telemetry.db" prd.md` → **18 occurrences**; `grep -c "operational.db"` → **0**; `grep -c "derived.db"` → **0**.

This is not cosmetic, because the PRD is now internally contradictory. NFR-11 *was* reconciled and now demands the split in prose:

> **PRD NFR-11 (line 677):** "Operational state is **not** derivable from Markdown … so it is never a rebuild target and **must be stored separately from Derived state**."

…while §2.1 (line 38) still declares one file holding both:

> `├── event\_telemetry.db              # Cross-project SQLite telemetry, job queue & commitments index (Encrypted)`

Job queue = Tier 2 (encrypted, never rebuilt). Commitments *index* = Tier 3 (plaintext, rebuildable). The PRD encrypts both and stores both in one file, which NFR-11 in the same document forbids. Every downstream reference inherits the error: FR-02 line 275 ("writes raw parsed diffs to ~/.pm-ai/private/event\_telemetry.db"), FR-04 line 291, FR-33 line 573, FR-34 lines 581/583/596, NFR-10 line 676 ("buffer in encrypted event\_telemetry.db"), §6 line 651, Glossary lines 229/236/246.

**Fix:** rename to `operational.db` / `derived.db` across all 18 sites; move the search and commitment indexes out of the encrypted set in NFR-08 and §2.1.

---

### (b) FR-36 signing — RECONCILED in the requirement, NOT in Non-Goals

**Spine, AD-18 (line 204):**
> The skill registry is an allowlist of first-party modules, each declaring the **`SkillPermission`s** it may exercise … Cryptographic signature verification is deferred (see Deferred)

**PRD, FR-36.3 (line 354):**
> The MCP skill registry is an explicit local allowlist of first-party skill modules, each declaring the scopes it may exercise. … **Cryptographic signature verification is deferred** — it is not required while every skill is authored by the PM and installed locally. The skill load path shall remain pluggable so verification can be introduced without restructuring.

**PRD, revisit condition (line 356):**
> implement signature verification before the first skill authored by anyone other than the PM is installed, or before skills are distributed to other users

Matches the spine's `Deferred` entry (line 614) exactly. `grep "statically verified" prd.md` → **0 hits**; `grep "signed"` → 4 hits, all of which are `designed` / `assigned` / the Decisions Log narration of the change. The word is gone. **Reconciled.**

**Glossary — reconciled.** Line 224:
> **Execution Firewall:** … routing all external **mutations** exclusively through registry-authorized Model Context Protocol (MCP) skill tools.

"Mutations", not "all reads and writes" — matches AD-1's class-M framing.

**Non-Goals — NOT reconciled.** PRD line 602:
> **No Open Shell / Raw Terminal Execution:** pm-ai will never grant raw shell access to the LLM core. **All system reads and writes must execute via registry-authorized MCP tools.**

The spine repudiates that exact sentence. AD-1, line 73:
> That is the security property; **the earlier "100% of reads and writes route through MCP" was a stricter-sounding claim that the connector, frontier, and transcription paths each contradicted.**

The Non-Goal is a load-bearing statement (AD-1's `Binds:` line names `Non-Goals` explicitly, line 61), and it currently asserts a property the architecture proves false on day one — connectors (class H), the frontier adapter (class F), and whisper.cpp (class L) all read or write outside MCP by design. The Glossary was fixed and the Non-Goal was missed.

**Second, smaller relapse.** FR-36.3 says skills declare "the **scopes** they may exercise". AD-18 forbids this word for this concept, and says why:

> `SkillPermission` is a distinct type from `DataScope` (AD-4) and **is never called "scope"** — the two were one word in an earlier draft, which is how a project literally named `personal` could have satisfied a privacy check.

The v0.9.0 Decisions Log entry (line 782) propagates the same wording: "an explicit local allowlist of first-party skills with declared scopes." The PRD reverted the term the spine ADopted specifically to prevent a privacy-check bypass.

---

### (c) §2.1 / §6 three-scope model — RECONCILED

**Spine, AD-4 (line 112):**
> Exactly three scopes. `~/.pm-ai/` holds application-level state: daemon settings, project registry, per-project connector configuration, credentials. `~/.manager-ai/` holds sovereign personal material only … `<repo>/.project-ai/` holds committed per-project material.

**PRD §2.1 (lines 22–24):** all three present, in that order, with matching content —
> **Application Scope (\~/.pm-ai/):** System-level state owned by the application itself — daemon settings, the registry of enrolled projects, per-project connector configuration, encrypted credentials …
> **Sovereign Personal PM Scope (\~/.manager-ai/):** … Contains **no** project-specific information or configuration. This scope survives independently across project, role, or company transitions
> **Isolated Project Scopes (\<project-root\>/.project-ai/):** Repository-specific directory committed to version control

The ASCII tree (lines 26–90) renders all three blocks (A/B/C) plus `~/.manager-ai-private/`. §6's storage contract (line 650) carries the third box:
> `\[\~/.pm-ai/\] (Application Scope: settings, project registry, connectors)`

Glossary entries exist for all three (lines 221–223). **Fully reconciled**, including the "survives role and company transitions" rationale that AD-4's `Prevents` clause states.

---

### (d) NFR-13 / SM-5 cost target — RECONCILED as a target; wrong ledger destination

**Spine, AD-17 (line 198):**
> At threshold breach the system **warns only** — no degradation to local models, no hard stop, no feature blocking. The $20 figure is a monitored target for understanding real efficiency, not a circuit breaker.

**PRD, NFR-13 (line 683):**
> shall be held to a **monitored target** of $20/month per user … **Breaching the target produces a warning only** — the system shall not silently degrade output quality, downgrade models, or disable features on breach. The figure is an instrument for understanding the system's real operating economics; converting it into an enforced cap is a later decision to be taken against actual spend data.

**PRD, SM-5 (line 693):**
> Total monthly operating cost … tracked against the $20/user monitored target … **a breach is a signal to investigate, not a failure condition.**

`grep "capped strictly" prd.md` → **0 hits.** Both reconciled, and SM-5 even carries "with every frontier call attributed by task class", matching AD-15's task-class routing. **Reconciled.**

**Residue, non-blocking:** two Decisions Log entries still say "strictly below $20/month per user" (lines 777 and 778 — note these are a *verbatim duplicate line*, an editing defect worth fixing), and the Assumptions Index (line 722) says "keeps monthly token and power spend below $20/month". Both are historical/assumption framing and are superseded on the record by the v0.9.0 entry item 4 (line 784), so they are acceptable — but the duplicated line 777/778 should be deleted.

**Real defect inside this item:** NFR-13 sends cost records to the wrong file.
> **PRD NFR-13 (line 683):** "Every frontier call records token counts and a cost estimate to **event\_log.md**"

vs.
> **Spine AD-17 (line 198):** "Every frontier call logs token counts and a cost estimate to **the application-scoped disclosure ledger (AD-38)**"
> **Spine AD-38 (line 359):** "Disclosure & cost … `~/.pm-ai/disclosure.md`, a **single** append-only Tier-1 ledger | **Never** [committed]"

NFR-13 therefore directly contradicts the PRD's own reconciled FR-27 (line 527: "never to the per-scope event log"). This is the exact leak AD-38 exists to prevent, still live in the NFR section.

---

### (e) §6 model topology naming — RECONCILED

**Spine, AD-15 (line 186):**
> Tier within the frontier class: `coaching` and `research` → `claude-opus-5`; the rest → `claude-sonnet-5`.

**PRD §6 (lines 634–635):**
> `|          Frontier LLM (Claude Opus 5 / Claude Sonnet 5, tiered)         |`
> `|  (Opus 5: 1:1 coaching, deep research | Sonnet 5: briefings, drafts)   |`

Exact task-class match. `grep "Claude 3.5" prd.md` → 2 hits, both in the Addendum: line 736 is the original 2026-08-16 Model Strategy entry, and line 785 is the v0.9.0 entry that explicitly supersedes it ("Claude 3.5 Sonnet was retired 2025-10-28 … Supersedes the 2026-08-16 Model Strategy entry"). A decisions log correctly retains superseded history. **Reconciled.**

One dangling intent from that superseded entry is discussed in HALF 2 (§H2-6): "with an explicit migration path to 100% local operation" was never re-stated or retracted, and the spine has no such path.

---

### (f) FR-27 disclosure ledger + segmented event log — PARTIAL

**FR-27 itself is fully reconciled.** PRD lines 523–527:
> Store system decisions … in the per-scope event log across both manager … and project … scopes … An entry belongs to the scope that owns its subject; an entry that would need two scopes is two entries.
> The event log is **segmented** — a directory of dated segments, exactly one open and appended to, earlier segments sealed and immutable — so FR-37 compaction can bound its growth by replacing whole sealed segments rather than rewriting entries in place.
> **Disclosure and cost records are a separate ledger.** Every frontier model call records its scope provenance, task class, model, token counts, and estimated cost to a single application-scoped ledger at \~/.pm-ai/disclosure.md — never to the per-scope event log.

Matches AD-38 (line 359) and AD-5's segmentation rule (line 122) point for point, including the "audit mechanism would become the leak" rationale. The Glossary gained a matching **Disclosure Ledger** entry (line 220), and §2.1 gained the file (line 32):
> `├── disclosure.md                       # Frontier-call provenance & cost ledger (FR-27) - never committed`

**What did not reconcile: everything outside FR-27 still treats `event_log.md` as a single file.** `grep -c "event\\_log.md" prd.md` → **19 occurrences**, none of them updated to a directory:

- §2.1 personal scope, line 59: `│   └── event\_log.md    # Multi-project master audit trail & decision log (FR-10, FR-27)`
- §2.1 project scope, line 85: `│   │   └── event\_log.md   # Project Alpha specific audit trail & decision log (FR-27)`
- §6 storage contract, line 648: `|  \- event\_log.md (Unified Telemetry)      \- event\_log.md (Unified Telemetry)   |`
- FR-10, line 392: "Every state mutation appends an immutable JSON line to \~/.manager-ai/memory/**event\_log.md**"
- FR-37.1, line 367: "compress … into structured long-term project milestone summaries stored in **event\_log.md**" — this is the compaction operation, and per AD-5 it must write a *new segment* that supersedes sealed ones, not append into a file
- Also FR-02 (line 276), FR-19 (line 464), FR-36 (line 360), NFR-08 (line 671), NFR-13 (line 683), Glossary (lines 229, 246)

Compare the spine, AD-24 (line 240):
> **The ledger is a directory of dated segments** (`event_log/2026-08.md`), not a single file, per AD-5

FR-27 asserts the new model; §2.1 and ten other sites still depict the old one, and §2.1 is the document's canonical directory contract. A builder reading §2.1 will create a file.

Additionally, §2.1's personal-scope `event_log.md` is described as the "**Multi-project master** audit trail" (line 59) — a cross-project aggregate. That contradicts both the reconciled FR-27 ("An entry belongs to the scope that owns its subject") and AD-38's routing rule, and would place project-subject entries in the personal scope.

---

### (g) FR-16 privacy charter — NOT RECONCILED

This is the flag the spine states as a *requirement on the PRD*, not merely a note. Spine AD-31, obligation 1 (line 285):

> 1. **FR-16 must say so.** A charter that means something narrower than its words is worse than no charter, because it invites a reader to assume more protection than exists.

**Spine AD-31 rule (line 284):**
> FR-16's adversary is **employer-controlled systems** — team channels, shared repositories, enterprise dashboards, HR platforms — not model APIs. **Personal-scope material may therefore enter a frontier prompt**, and the Socratic coaching flow routes to `claude-opus-5`

**PRD FR-16, current wording in full (line 435):**
> Governed by the **User Privacy & Data Boundary Charter**: burnout telemetry, working hour dynamics, and personal coaching records are **strictly hardware-bound** to \~/.manager-ai/ and shall never be published or synced to team channels, public repositories, or enterprise dashboards. Realizes UJ-1, UJ-9.

FR-16 does **not** name its adversary, does **not** mention frontier model APIs, does **not** state the disclosed exception, and does **not** reference the disclosure ledger. Worse, "**strictly hardware-bound**" is an affirmative claim that the data never leaves the machine — the precise over-reading AD-31 says a charter must not invite. Under AD-15 (line 186) `coaching` routes to `claude-opus-5`, so coaching records leave the machine on every 1:1. FR-16 as written is false.

**The Glossary was reconciled; FR-16 was not.** Glossary line 227:
> **User Privacy & Data Boundary Charter:** … Its adversary is **employer-controlled systems** … **Frontier model APIs are a disclosed exception: personal-scope material may enter a model prompt**, every such call is recorded in the disclosure ledger, and no record written to a git-committed scope may reference personal-scope material. The charter names its threat model explicitly because a charter meaning something narrower than its words invites a reader to assume more protection than exists.

That is a near-verbatim adoption of AD-31 — into the wrong section. The Decisions Log claims otherwise. Line 797:
> 3. *Privacy Charter (**FR-16** / Glossary):* Reworded to name its adversary - employer-controlled systems, not model APIs - with frontier calls a disclosed, logged exception.

The FR-16 half of that claim is not true of the current text. `grep "hardware-bound" prd.md` → 2 hits: FR-16 (line 435) and the v0.8.0 Decisions Log (line 774). The v0.8.0 phrasing was never retracted in FR-16.

Related residue: FR-16's second testable consequence (line 439) — "Anti-burnout indicators and personal workload analytics are strictly excluded from all public or project-level files in \<project-root\>/.project-ai/" — is correct and matches AD-25/AD-38, but is narrower than AD-31 obligation 3, which also forbids personal material entering *a prompt whose output is bound for a project artifact*. That destination-side rule (spine line 287: "Burnout signals may shape your briefing; they may not reach a team-facing dashboard by way of a model that read both") has no PRD home at all.

---

## HALF 2 — What the AD structure dropped

The spine's frontmatter claims a binding it does not honour. Line 11:

> `binds: [FR-01..FR-37, NFR-01..NFR-14, UJ-1..UJ-10]`

Mechanical check against that claim:

| Never mentioned anywhere in the spine | Count |
| --- | --- |
| **FR-11, FR-12, FR-13, FR-14, FR-15** | 5 requirements |
| **NFR-02, NFR-03, NFR-05, NFR-06** | 4 latency SLAs |
| **UJ-2, UJ-4** | 2 of 10 user journeys |
| **SM-1…SM-9, SM-C1…SM-C3** | all 12 — `grep -c "SM-"` → 0 |
| **JTBD-1…JTBD-12** | all 12 — `grep -c "JTBD"` → 0 |

(FR-11–FR-15 are technically inside the range token `FR-09..FR-17` in one Capability Map row, line 598 — a range that names no individual requirement and binds them to nothing specific.)

The pattern is exactly the one predicted: the ADs captured structural and security FRs richly and dropped tone, product principles, UX promises, and measurement.

### H2-1 — The entire measurement layer, including the "do not optimize" constraints (highest severity)

**PRD §8.2 (lines 701–703):**
> **SM-C1 (Message Draft Volume):** Do not optimize for raw volume of generated drafts. Focus on draft acceptance rate without extensive manual edits (≥85%). Counterbalances SM-2.
> **SM-C2 (Literature Push Frequency):** Do not optimize for number of articles recommended per week (cap at 3 situational citations/week to avoid cognitive spam). Counterbalances SM-4.
> **SM-C3 (Coaching Session Frequency):** Do not force daily coaching prompts; respect PM-initiated cadences to avoid session fatigue. Counterbalances SM-3.

**Spine:** `grep -c "SM-\|counter\|Counter" ARCHITECTURE-SPINE.md` → **0**.

These are not metrics — they are *negative design constraints on behaviour*, and they are the only thing in either document preventing the assistant from becoming the noisy relay the Vision (line 16) explicitly rejects: "Rather than acting as … a noisy notification relay". Each counter-metric names a specific failure mode a builder will otherwise walk into: draft spam, citation spam, coaching-prompt fatigue. AD-13 governs *how* a proposal is approved; nothing governs *how many* proposals it is acceptable to generate. The spine's Enforcement section (lines 628–632) turns ADs into failing tests; there is no test that could fail for "generates 40 draft cards a day", because no AD says it shouldn't.

The nine primary SM targets are likewise absent, so nothing in the build substrate encodes what "working" means: SM-7 ≥95% spoken-anchor precision, SM-9 ≥90% commitment-transition accuracy, SM-8 ≥80% implicit-approval acceptance. AD-32 and AD-36 make commitment verification *safe*; no AD makes it *accurate enough to be useful*, and the difference is what determines whether the product is worth running.

### H2-2 — The Socratic tone contract and the persona system (FR-12, FR-14, FR-20)

**PRD FR-12 consequence (line 408):**
> System frames responses as open-ended questions ending in question marks (**≥80% of turns**) rather than direct prescriptive directives.

This is the single most falsifiable statement of the product's personality in the PRD — the thing that separates "Socratic PM companion" from "chatbot". `grep "FR-12" ARCHITECTURE-SPINE.md` → **0 hits**. The spine mentions "Socratic" twice (lines 266, 284) and only ever as a *storage-scope* question ("a personal undertaking from a Socratic 1:1 is a distinct entity"), never as an interaction quality.

**PRD FR-20 (lines 469–472):**
> Dynamically loaded persona profiles (persona.md) defining assistant **tone, directness, and constructiveness levels** across CLI and Telegram outputs. … Executing pm-ai persona set directness=concise via CLI or Telegram **immediately updates persona.md parameters and alters downstream response formatting without restarting the daemon.**

**Spine:** FR-20 appears exactly once, in the Capability Map row "CLI & Telegram surfaces (FR-18, FR-19, FR-20, FR-22) | `surfaces/` | AD-2, AD-7, AD-8, AD-13, AD-21" (line 604). None of those five ADs concerns tone, persona loading, or hot-reload. `grep -c "tone" ARCHITECTURE-SPINE.md` → **0**. The word "persona" survives in the spine only as a prompt-caching remark (line 459: "the caching benefit assumed for repeated persona and rules prefixes must clear the higher floor") — persona reduced to a token-count consideration.

**PRD FR-14 (lines 420–424):** the Meta-Coaching Scorecard — two 1–10 ratings that "dynamically alter persona parameters for subsequent sessions" — is the feedback loop that makes the persona system adaptive rather than static. `grep "FR-14"` → 0 real hits; `grep -ci "scorecard"` → **0**. UJ-1's climax and resolution (lines 118–119) are built entirely on this loop:
> pm-ai … generates a 2-question Meta-Coaching Scorecard prompt (1-10 rating scale). … Andrei rates the session in 5 seconds; pm-ai **updates its internal persona tuning** without interrupting live work.

The spine has no entity, port, or AD for persona state, and `persona.md` does not appear in the Scopes-and-storage diagram (lines 517–543) — the PERS subgraph shows `rules/` generically (line 529) but never names the file. A builder working from the spine alone would not know persona configuration exists.

### H2-3 — The interruption boundary: FR-13 and its Non-Goal (a product promise with a security-grade rule shape)

**PRD FR-13 (lines 412–416):**
> Maintain strict work-hour **quiet boundaries** by silencing unsolicited alerts and background telemetry interruptions during deep work hours. Scheduled push notifications are explicitly permitted for delivering pre-meeting analysis briefings (FR-32, 15m/1h prior) and post-meeting summary/approval cards (FR-06).
> * Background telemetry harvesters emit **zero** unsolicited active desktop/mobile push notifications during active work hours.
> * Push notifications are **strictly restricted** to scheduled pre-meeting preparation alerts and post-meeting summary cards.

**PRD Non-Goal (line 605):**
> **No Unsolicited Mid-Work Interruptions:** pm-ai will not send unprompted notifications or message relays during active work hours. Push notifications are strictly bounded to scheduled pre-meeting prep cards (15m/1h prior) and post-meeting summary/approval reports.

**PRD Decisions Log, 2026-08-16 (line 733):**
> **(Noise Filtering Boundary):** Explicitly **rejected forwarding raw Teams/Slack notifications into Telegram.** Voice interface scoped strictly for high-context draft synthesis.

**Spine:** `grep "FR-13"` → 0 real hits (both matches are substrings of `NFR-13`). `grep -ci "notification"` → 1 hit, and it is about Jira vs GitLab verb reversibility (line 298), not about the user's attention. `grep -ci "quiet"` → 1 hit, same line.

This is the clearest example of the AD structure losing something it was well-shaped to hold. FR-13 is a *closed allowlist of permitted outbound surface events* — structurally identical to AD-1's egress classification, which the spine enforces with import contracts and AST rules. It would have made a natural AD ("outbound push to the PM is allowlisted; the allowlist is pre-meeting cards and post-meeting cards; everything else is pull"). Instead the spine's only outbound-surface rule is AD-2's "Telegram delivers only to the cryptographically paired user" (line 70) — *who* may receive, never *when* or *how often*. AD-21 (line 222) mandates async delivery for slow requests, which if anything increases unsolicited pushes. Nothing stops the connector scheduler, the commitment sweeper, or the drift auditor from pushing at 22:00, and the enforcement suite has no check that could fail.

### H2-4 — Four of the six concrete latency SLAs

**Present in the spine:** AD-21's 5-second acknowledge threshold (line 222) and AD-22's 50–150 ms retrieval / ≤60 s synthesis split (line 228). NFR-01 survives as a single passing reference (line 429: "revisit only if transcription misses NFR-01 with Metal").

**Absent entirely:**
- **NFR-02 (line 662):** "Full round-trip time from receiving a 20-second voice note to rendering individual, context-enriched draft review cards … must not exceed **45 seconds**." — this is UJ-2's whole value proposition and SM-2's target.
- **NFR-03 (line 663):** "Meeting transcripts must be parsed, sanitized, spoken anchors/commands extracted, Work Item state updated via MCP, and staged research tasks queued within **600 seconds**" — the headline promise of UJ-3.
- **NFR-05 (line 665):** transcript-triggered research dispatched "within **15 minutes** of meeting conclusion".
- **NFR-06 (line 666):** on-demand missed-meeting extraction rendering a Summary Card "within **300 seconds**".

AD-22's generic "≤60 s" synthesis budget does not substitute: NFR-03 and NFR-06 are multi-stage *pipeline* budgets spanning download, sanitize, extract, and MCP mutation, and NFR-02 is an end-to-end budget across transcription plus synthesis plus rendering. The spine tells a builder how to *structure* async delivery (AD-21) but never how fast any pipeline must finish, so no design choice can be rejected on latency grounds.

### H2-5 — Two user journeys the spine claims to bind but never mentions

`binds: UJ-1..UJ-10` (line 11), yet `grep -c "UJ-2\|UJ-4"` → **0**.

**UJ-2 (lines 120–129)** is the mobile voice concierge — the product's flagship demo and the origin of JTBD-1. Its defining UX detail is a sequencing promise the spine's Proposal model does not carry:
> pm-ai generates individual draft cards in Telegram **one by one**, displaying target recipient, full enriched draft body, and cited source artifacts. Andrei reviews Draft 1 (Laura), taps [Send]; reviews Draft 2 (Alex), taps [Edit], tweaks a sentence, and taps [Send].

AD-13 (line 172) defines one `Proposal` with "One card renderer serves both surfaces" — a *per-proposal* model with no notion of an ordered review queue, of batch-from-one-utterance grouping, or of the "one by one" pacing that keeps a 20-second voice note from producing a wall of cards. FR-21 (line 476) repeats the promise — "reviewed **one by one** before dispatch" — and it survives nowhere in the derived document.

**UJ-4 (lines 139–147)** is the Career Dossier 1:1 flow. The Capability Map has a row for FR-30/FR-31 (line 601), but the journey's ordering constraint — dossier pushed 15 minutes before the calendar event, then post-meeting goal extraction, then explicit approval, then HR sync — is nowhere. AD-13 covers the approval step generically; the pre-meeting trigger timing and the two-phase shape are lost.

### H2-6 — Product principles from the Vision, §0, and Non-Goals

**The cost-displacement thesis.** §0 (line 12):
> It **completely replaces legacy cloud RAG architectures (AWS + Onyx @ $800+/month)** with a git-backed, markdown-driven operating system designed to **eradicate managerial cognitive tax, protect executive bandwidth** …

`grep -ci "cognitive"` → 0. The $800→$20 displacement is the product's economic reason to exist and the entire justification for AD-17's $20 figure; the spine carries the $20 number (line 198) with no memory of what it replaced, which is why the Cost-model caveat (lines 466–471) reads as a pricing footnote rather than a threat to the thesis.

**Non-Goal: No Cloud Vector DB / Heavy SaaS RAG** (line 607):
> The system will not depend on cloud-hosted vector databases or SaaS RAG infrastructure.

`grep -ci "cloud\|SaaS"` → **0**. The Stack happens to be local (sqlite-vec, line 407), but nothing forbids a future AD from adding a hosted index. This is a one-line AD the spine never wrote, and unlike most Non-Goals it is trivially enforceable in `.importlinter` alongside the AD-16 library ban.

**Non-Goal: No Pre-Merge Doc Gatekeeping** (line 609), backed by a Decisions Log entry (line 734, "code is ground truth"):
> Developer Merge Requests will **never** be blocked by documentation drift checks.

`grep -ci "gatekeep\|blocked"` → **0**. This directly constrains FR-25, the drift auditor, which the spine *does* carry (Capability Map line 603, AD-33 line 304). The spine gives the drift auditor a home and a citation model but omits the one thing it must never do — and "block the MR" is the obvious next feature for anyone building a drift auditor.

**The 100%-local migration path.** Decisions Log, 2026-08-16 (line 736):
> Adopted deterministic code/local model priority for regular tasks, reserving [frontier] for high-level synthesis **with an explicit migration path to 100% local operation.**

`grep -c "100% local"` → **0** in the spine. The v0.9.0 entry (line 785) supersedes this entry's *model name* but says nothing about the migration path, so the intent is formally still live in the PRD. AD-15 (line 186) hardcodes `coaching`/`research` to `claude-opus-5` with no local fallback path and no port-level provision for one, which makes the stated migration path harder rather than preserving it. Either the spine should carry it as a constraint on AD-15, or the PRD should retire it explicitly.

### H2-7 — Smaller drops, confirmed by grep

- **NFR-07's enforcement mechanism** (line 670): "Automated **pre-commit hooks** verify that the private enclaves are gitignored." → `grep -ci "pre-commit\|gitignore"` in spine → **0**. AD-4 and AD-25 assert scope separation as a rule; the PRD's only *mechanical* guard against committing the personal enclave is gone, and this is the guard that protects AD-38's whole premise.
- **Meeting ROI / Man-Hour Cost as a cultural instrument.** The spine keeps the data ("It is also where FR-03's Man-Hour Cost inputs live", line 308) but drops the intent. Glossary line 234: "displayed as an informative metric within post-meeting summary header blocks **to foster team cost awareness**", and FR-03 line 280 qualifies it "(as a **secondary informative** metric)" — a deliberate de-emphasis so it does not read as a scolding. `grep -ci "blended\|hourly\|Meeting ROI"` → **0**; the formula and the "single PM-configured figure … rather than per-attendee salary data - keeping compensation out of the telemetry store" privacy decision (line 791) are both absent.
- **FR-17's citation ceiling.** FR-17 (line 447): "Cites **at most 3** situational articles/web pages per week … requiring exact URL and title matches from article\_sources.md", reinforced by SM-C2. Spine reduces FR-17 to one Capability Map cell: "Literature & web ingestion (FR-17) | personal-scope connector instance | AD-10, AD-12" (line 599). `grep -ci "RSS"` → 0. The anti-spam cap — the entire reason this feature is not annoying — is gone.
- **UJ-10's 7-day backfill on connector add** (line 213): "pm-ai triggers a background historical telemetry backfill (past 7 days)". `grep -ci "backfill"` in spine → 1 hit, and it is AD-35's "not silently **backfilled** from `ingested_at`" (line 328) — a different concept. AD-9 defines `harvest(since: Cursor)` with no initial-cursor policy, so a new connector's starting position is undefined.
- **Open Question 2** (line 716), the deferred pm-ai Performance Index. The spine's Open Risks carries Open Question 1 explicitly (line 649: "remains unbenchmarked (PRD Open Question 1)") and drops OQ-2 — `grep -ci "Performance Index"` → 0. Since OQ-2 is about *self-measurement*, its loss compounds H2-1.
- **Phase alignment.** PRD §9 Phase 1 (line 708) is a large feature set (FR-01, FR-03, FR-04, FR-10, FR-16, FR-18, FR-19, FR-20, FR-34, FR-35, FR-36 …). The spine's "Phase 1" means something entirely different — stack pinning, two vertical slices, and "zero skips in `tests/architecture/`" (line 642). Same word, two scopes, no mapping between them. Anyone sequencing work from both documents will mis-plan.

---

## Additional divergences found that were on neither list

These are places where the PRD and spine disagree and *neither* document flags it.

**1. Telegram webhooks — a live contradiction of a security AD.**

> **Spine AD-2 (line 79):** "Telegram uses **outbound long-polling only** — **webhooks are prohibited**, because they require a publicly reachable HTTPS endpoint or tunnel."
> **Spine prerequisites (line 436):** "It is prohibited (as is `run_webhook()`, per AD-2)."

> **PRD FR-19 (line 461):** "…1:1 coaching sessions over Telegram **HTTPS webhook/polling**."
> **PRD FR-19 consequence (line 465):** "**Telegram webhook endpoint** responds to authorized updates within 2000ms."
> **PRD NFR-14 (line 679):** "Telegram mobile communications must rely strictly on **HTTPS webhook/polling** authenticated by paired user-IDs"

The PRD names webhooks in three places including a testable consequence built entirely on one. NFR-14 does this in the same sentence as "exposing zero public HTTP or WebSocket ports" — which a webhook requires. This is the same class of internal contradiction the architecture run was created to catch, and it is still open in the section AD-2 binds (`Binds: … NFR-14`, line 77).

**2. NFR-12's model class and hardware baseline — spine narrowed the PRD without a flag.**

> **PRD NFR-12 (line 678):** "quantized **7B to 13B** open-weight models (via Ollama) … 16GB RAM minimum on Apple Silicon (M-series) **or 8GB VRAM NVIDIA GPU (CUDA)**"
> **Spine Stack (line 403):** "an **8B-class** instruct model at `Q4_K_M`" / Deferred (line 617): "**Anything above 8B-class is out**"
> **Spine AD-26 (line 252):** "v1 targets **macOS on Apple Silicon only**."

The spine narrows 7B–13B to 8B-class and drops CUDA entirely, both deliberately and with stated reasons — but neither appears in the flag list, and the PRD still promises 13B models and NVIDIA support. NFR-12 is bound by AD-15 and AD-19 (`Binds: NFR-12`, lines 184, 208) so this is a real, unpropagated narrowing.

**3. FR-28 says "sandboxed MCP skills."** PRD line 535: "producing ready-to-merge Merge Requests or PRs using **sandboxed** MCP skills." The spine has no sandbox concept — AD-18 provides an allowlist plus declared permissions, which is authorization, not sandboxing. `grep -ci "sandbox"` in spine → 0. FR-28 promises an isolation property the architecture does not implement.

---

## Recommended edits to the PRD

Ordered by severity.

1. **FR-16 (line 435)** — replace "strictly hardware-bound to ~/.manager-ai/" with the Glossary charter's own text: name employer-controlled systems as the adversary, state the frontier-API exception, reference `~/.pm-ai/disclosure.md`, and add AD-31 obligation 3 (personal material may not enter a prompt whose output is bound for a project artifact or external system). Required by AD-31 obligation 1. **[flag g]**
2. **NFR-13 (line 683)** — change "records token counts and a cost estimate to event\_log.md" → "to the application-scoped disclosure ledger at ~/.pm-ai/disclosure.md (FR-27)". Currently contradicts the PRD's own FR-27. **[flag d/f]**
3. **Non-Goal 1 (line 602)** — replace "All system reads and writes must execute via registry-authorized MCP tools" with AD-1's classification: only *model-driven external mutations* route through MCP; harvest, frontier, and local-subprocess paths are separately classified and constrained. **[flag b]**
4. **§2.1, §6, and 10 other sites** — `event_log.md` → `event_log/` (directory of dated segments). Remove "Multi-project master audit trail" from the personal-scope entry (line 59). **[flag f]**
5. **All 18 `event_telemetry.db` sites** — split into `private/operational.db` (encrypted, Tier 2) and `private/derived.db` (plaintext, Tier 3); update NFR-08's encrypted set and NFR-10's "buffer in encrypted event_telemetry.db" accordingly. Resolves the NFR-11 self-contradiction. **[flag a]**
6. **FR-36.3 (line 354)** — "the scopes it may exercise" → "the **permissions** it may exercise (`read`, `comment`, `edit`, `transition`, `create`, `send`)". AD-18 forbids "scope" here by name. **[flag b]**
7. **FR-19 (lines 461, 465) and NFR-14 (line 679)** — remove webhooks; outbound long-polling only, per AD-2.
8. **NFR-12 (line 678)** — align the model class (8B-class `Q4_K_M`) and drop or explicitly defer the CUDA/NVIDIA baseline, per AD-26.
9. **FR-28 (line 535)** — "sandboxed MCP skills" → "registry-authorized MCP skills with declared permissions", or specify what sandboxing means.
10. **Addendum (lines 777–778)** — delete the duplicated Decisions Log line.

## Recommended additions to the spine

1. An AD for the **outbound-attention allowlist** (FR-13 + Non-Goal "No Unsolicited Mid-Work Interruptions"). It has AD shape — a closed list of permitted push events — and is enforceable in the existing behavioural test suite.
2. Carry the **counter-metrics (SM-C1/C2/C3)** as explicit negative constraints; they are the only stated defence against the noisy-relay failure mode the Vision rejects.
3. Carry **NFR-02/03/05/06** as named pipeline budgets alongside AD-21 and AD-22.
4. Give **persona/tone** (FR-12 ≥80% question ratio, FR-14 scorecard feedback loop, FR-20 hot-reload) a home — at minimum an entity and a storage location; `persona.md` does not appear in the storage diagram at all.
5. Record the **Non-Goals the spine dropped**: no cloud vector DB / SaaS RAG, and no pre-merge doc gatekeeping (a direct constraint on FR-25, which the spine does carry).
6. Resolve the **100%-local migration path**: either constrain AD-15 to preserve it, or have the PRD retire it explicitly.
7. Rename or map the spine's **"Phase 1"** against the PRD's §9 Phase 1 — the same word currently denotes two different scopes.
