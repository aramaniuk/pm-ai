# Review R4 — good-spine rubric

**Target:** `_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md`
**Reviewer:** fresh pass, no prior review consulted
**Date:** 2026-08-19
**Method:** every claim below was checked against the spine text, `pm_ai/`, `.importlinter`,
`tests/architecture/`, `tests/architecture/README.md`, `pyproject.toml`, `uv.lock`, the PRD (grepped),
and — for Anthropic API claims — the current `claude-api` reference. Findings are labelled
**CONFIRMED** (verified against a source quoted here) or **PLAUSIBLE** (reasoned, not proven).

**Verdict: strong spine, above the bar for build substrate — but three ADs state a `Prevents`
their `Rule` does not close, and one whole dimension (credential/secret lifecycle) is silent.**

---

## 1. Does it fix the real divergence points for FEATURES? Does it miss any?

**Verdict: mostly yes — unusually good coverage of the hard ones — with four genuine misses.**

What it fixes, and fixes well (these are the ones that actually keep independently-built features
coherent, and each is expressed as a type or a chokepoint rather than an exhortation):

| Divergence point | Fixed by | Evidence it is real |
| --- | --- | --- |
| Approval flows | AD-13 (one `Proposal`, register a type, never a flow) | `pm_ai/domain/proposals.py` |
| Egress placement | AD-1 five-class table | `.importlinter` `core-is-io-free`, `http-confined-to-adapters` |
| Event vocabulary **and payload shape** | AD-27 | `PAYLOAD_FOR` registry + `NormalizedEvent.__post_init__` |
| Reference grammar / actor identity / dedup key | AD-34 | `SourceRef.parse`, `resolve_actor`, `natural_key` |
| Two clocks | AD-35 | `occurred_at` vs `ingested_at`, assigned in `StorageService.persist_events` |
| Concurrency on shared entities | AD-37 | `Proposal.transition(expected_version=…)` |
| Which scope a record lands in | AD-38 + `assert_writable` | `pm_ai/domain/disclosure.py` |
| Storage tier separation | AD-3 + `assert_reindex_safe` | `pm_ai/domain/storage_tiers.py` |

The AD-27→AD-34 pairing in particular is the mark of a spine that has already been through one
real divergence: AD-34's own `Prevents` says *"AD-27 closed the type enumeration and left every
other field open."* That is the correct instinct, applied correctly.

### Miss 1 — credential and secret lifecycle (see §7; cross-listed here because it is a *feature*-level divergence, not only an ops one)

### Miss 2 — cross-project mutation containment. **CONFIRMED absent.**

`TargetRef` carries a scope segment (`gitlab:alpha:issue:PAY-102`), and `DataScope` carries a
`project_id`. Nothing in the spine requires the two to agree. Grep: the string `cross-project`
appears exactly once in the whole document, at AD-10, and only about cursors:

> **AD-10 Prevents:** "shared cursors causing cross-project gaps or duplication"

So a Proposal staged under `project:beta` may carry `target=gitlab:alpha:issue:X`, execute, and be
logged into beta's `event_log/`. The code matches the gap: `SkillRegistry.invoke` uses `self._scope`
for the ledger entry and never compares it to `target.scope`. AD-31 fixes the personal→project
direction of the data boundary; project-A→project-B is unaddressed in either direction. With N
registered projects and one shared skill registry, this is exactly the kind of thing two feature
builders will resolve differently (one will check, one won't).

**Suggested fix:** one clause in AD-18 — *a class-M invocation is rejected unless
`target.scope` matches the authorizing `DataScope`'s project id; a deliberate cross-project
mutation is a separate, explicitly-declared skill permission.*

### Miss 3 — how an asynchronous failure reaches the user. **CONFIRMED absent.**

Greps: `alert` = 0 hits, `re-auth`/`reauth` = 0, `surfac.*error` = 0. AD-21 fixes the shape of
*success* delivery ("returns an acknowledgement token plus a job id immediately, then delivers the
result over the same async channel") and says nothing about the failure path. AD-9 gives the
scheduler "backoff and rate limiting"; `pm-ai doctor` reports connector probe status on demand.
Nothing says what happens when a connector has been failing for three days, when a job exhausts its
retries, or when a skill invocation returns a 403.

This is a real feature-coherence problem, not a polish item: the spine's own Open Risks section
names the failure mode — *"the 07:00 briefing simply stops"* — and then leaves each feature to
invent its own answer. Expected divergence: one feature pushes a Telegram message, one writes an
`event_log/` entry, one logs to `~/.pm-ai/logs/` and is never seen.

**Suggested fix:** an AD saying every terminal failure of deferred work produces exactly one record
in a named place and surfaces on a named channel — probably: `event_log/` entry + a single
daily digest, never a per-failure push (which would collide with FR-13's notification boundaries).

### Miss 4 — end-to-end pipeline latency budgets (see §6).

---

## 2. Is every AD's Rule enforceable, and does it close the hole its Prevents names?

**Verdict: three ADs fail this test. This is the highest-value section.**

### 2.1 AD-20 — the idempotency ledger cannot prevent the duplicate it promises to prevent. **CONFIRMED. Severity: HIGH.**

> **Prevents:** "in-memory timers losing work on restart; **duplicate external writes on replay**"
>
> **Rule:** "Delivery is at-least-once, so every job that mutates an external system carries a
> mandatory idempotency key. The key is **derived deterministically** from
> `(job_type, target_ref, payload_hash)` … and the skill layer refuses a mutating invocation that
> arrives without one."

The Rule guarantees the key is *deterministic* and *present*. It never says where the key is
honoured, and every mention in the document points at a **local, after-the-fact** ledger — AD-3
Tier 2 holds the *"executed-idempotency-key ledger"*, AD-36 says the skill layer *"records every
class-M mutation it performs … in the Tier-2 executed-key ledger"*. `grep -i idempoten` over the
spine returns nine hits and not one of them requires the key to be **transmitted to the provider**
or an **intent record to be written before the external call**.

The code implements exactly what the Rule says, and shows the window (`pm_ai/skills/registry.py`):

```python
if self._storage.was_executed(idempotency_key):
    ...
    return Invocation(external_id=prior[1], replayed=True)

external_id = skill.execute(target, payload)          # the external effect happens here
self._storage.record_execution(idempotency_key, target, external_id)   # …recorded only here
```

A crash, kill, or power loss between those two lines leaves the external system mutated and the
local ledger clean. AD-20 promises at-least-once delivery, so the job *will* be replayed, and the
replay will pass the `was_executed` check and write again. The spine already demonstrates that its
authors know a lagging ledger is dangerous — AD-3 spells out the restore case in detail
(*"Mutations performed after the backup point are absent from the restored executed-key ledger, so
a replayed job can act twice"*) — but a restore is a rare operator event, while this window opens
on **every single mutation**.

**Why it matters at this altitude:** this is the difference between "post one comment" and "post
one comment per crash", and the answer differs per provider (GitLab and Jira accept caller-supplied
idempotency semantics differently; Teams/Outlook `send` does not). If the spine doesn't fix it,
each skill author picks a different answer, which is precisely the class of divergence this
document exists to prevent.

**Suggested fix, in AD-20's Rule:** *a mutating invocation writes an `INTENT` row keyed by the
idempotency key before the external call and marks it `DONE` after; a replay that finds `INTENT`
without `DONE` must reconcile against the provider (query by the key or by a caller-supplied
correlation marker) before re-issuing. Where the provider supports a client idempotency token, the
key is transmitted; where it does not, the reconciliation query is mandatory and the skill declares
which of the two it uses.*

### 2.2 AD-36 — the join key its Rule depends on is left open, and the mechanism is unwired. **CONFIRMED. Severity: HIGH.**

> **Prevents:** "FR-06's executor posts a comment to WI-108, FR-34's verifier later reads WI-108
> activity as fulfilment evidence, and the system marks a commitment `FULFILLED` on telemetry it
> manufactured."
>
> **Rule:** "…the skill layer records every class-M mutation it performs (target ref plus the
> resulting external id) in the Tier-2 executed-key ledger, and normalization marks any harvested
> event **matching one of those** as `pm_ai`."

"Matching one of those" is the whole mechanism, and it is undefined. What the ledger holds is a
`TargetRef` lock key plus a provider-returned opaque string:

```python
def record_execution(self, idempotency_key, target: TargetRef, external_id: str) -> None:
    self._t2.executed[idempotency_key] = (target.lock_key, external_id)   # ("gitlab:alpha:issue:PAY-102", "note_1")
```

What a harvested event is identified by is an AD-34 `SourceRef` — `<system>:<scope>:<kind>:<native_id>`.
Nothing in the spine says the provider's returned `external_id` must be expressible in that grammar,
nor which field of the harvested event the match runs against, nor what to do when a mutation to
`issue:PAY-102` surfaces as a `note:` event with a different native id. This is structurally the
same defect AD-34 was written to fix one level up, reappearing one level down.

This is not merely theoretical drift — the reconciliation left it unbuilt. `executed_mutations()`
exists on `StorageService` and is called from exactly one place, `SkillRegistry.invoke`, for the
*replay* check. `run_harvest` in `pm_ai/app/pipelines.py` never calls it, and the connector asserts
attribution unconditionally:

```python
authored_by=Provenance.EXTERNAL,   # pm_ai/connectors/gitlab.py — every row, always
```

Note the second-order problem this exposes: AD-36's stated safety property is *"`Provenance.UNKNOWN`
is the envelope default, so an adapter that forgets to attribute fails closed."* The default only
protects against **omission**. An adapter that *asserts* `EXTERNAL` — which the one existing
connector does, for every row — bypasses the guard entirely, and the normalization pass that was
supposed to override it does not exist. `test_ad36_every_class_m_mutation_is_recorded_for_attribution`
passes because it asserts the ledger was written, not that anything reads it.

**Suggested fix:** fix the join in `domain`, the way AD-34 fixed the reference grammar. Require the
skill layer to record the mutation's result **as a `SourceRef` in AD-34 grammar** (not a bare
provider string), and state that normalization sets `authored_by=pm_ai` on any event whose
`source_ref` **or** whose `target` field matches a recorded one. Then add: *a connector may set
`authored_by` to `UNKNOWN` or `EXTERNAL`, but normalization's decision overrides the connector's.*

### 2.3 AD-12 — sanitization is enforced at the producer, and the Prevents names the consumer. **CONFIRMED. Severity: MEDIUM-HIGH.**

> **Prevents:** "**a new connector or transcript path** feeding unsanitized text into an LLM context"
>
> **Rule:** "Every payload crossing an inbound adapter boundary … passes the sanitization filter
> before it can reach any model context. The pipeline enforces this centrally; a connector cannot
> opt out or apply its own."

"The pipeline enforces this centrally" is true only for paths that go through the pipeline — and a
*new* ingestion path that does not is precisely the thing the `Prevents` names. The project's own
enforcement doc concedes it (`tests/architecture/README.md`, "Not mechanically enforced"):

> **AD-12** (sanitize every inbound payload) — the pipeline enforces it centrally; **a new ingestion
> path that bypasses the pipeline is a review catch.**

The frustrating part is that the chokepoint already exists and the spine declines to use it. AD-15
routes *all* model access through one `ModelPort` and the Consistency Conventions already make one
argument mandatory:

> **Model calls** | Always via `ModelPort` with an explicit `task_class` argument; a call without a
> declared task class is a defect

A `Sanitized` type already exists (`pm_ai/core/sanitize.py`, holding `raw` and `for_model`, which is
also AD-29's non-destructive guarantee). Requiring `ModelPort` to accept `Sanitized` rather than
`str` converts AD-12 from a review catch into a type error at the exact boundary its `Prevents`
describes — and it composes with AD-29 and AD-31 rather than competing with them.

Also worth noting: the slice's central enforcement is currently decorative —

```python
for event in result.events:
    sanitize(getattr(event.payload, "message", "") or "")   # result discarded
```

— it sanitizes one attribute of one payload type and throws the output away. That is a Phase-1
stub, not a spine defect, but it is what an unenforceable rule looks like in practice.

### 2.4 Weaker but real

- **AD-21 — "expected duration" is not a measurable predicate.** *Prevents:* "some flows blocking
  and others acking, so neither users nor builders can predict behavior." *Rule:* "Any request
  whose **expected** duration exceeds 5 seconds…". Two builders can hold different expectations of
  the same operation and both comply. AD-22 nearly supplies the fix (retrieval = no model in path;
  synthesis = model in path) — restating AD-21's trigger structurally ("any request that enqueues a
  job or crosses `ModelPort` is async; everything else may answer inline") would close it.
  **CONFIRMED, severity LOW-MEDIUM.**
- **AD-7 — "no feature may exist on only one surface" is unenforceable and partly untrue.** The
  enforcement table maps AD-7 only to `cli-owns-no-scheduling`, which checks something else
  entirely. FR-18/FR-19/FR-22 are surface-specific by definition. The *enforceable* half of AD-7
  (one daemon owns scheduling and writes; surfaces are thin) is fine and is checked; the parity
  clause is a convention. Worth demoting to Consistency Conventions so nobody reads green tests as
  parity proof. **CONFIRMED, severity LOW.**
- **AD-18 declares plural permissions, the port declares one.** AD-18: "each declaring the
  **`SkillPermission`s** it may exercise (`read`, `comment`, `edit`, …)". `pm_ai/ports/__init__.py`:
  `permission: SkillPermission` (singular), and `SkillRegistry.invoke` checks identity against a
  single verb permission. Harmless today, but a skill that both reads and comments has no legal
  shape. **CONFIRMED, severity LOW.**

---

## 3. Could anything under Deferred let two units diverge?

**Verdict: one defect, three clean.**

- **"MCP skill signing"** — clean. AD-18 keeps a binding allowlist in the meantime and requires the
  load path stay pluggable. Nothing shared is left undecided.
- **"Enforcing cost caps"** — clean. AD-17 decides the present behaviour (warn-only) unambiguously;
  only a future product decision is deferred.
- **"Linux support" / "Multi-user" / "In-meeting real-time"** — clean; all three are scope
  reductions with a port or a Non-Goal behind them.
- **"Encryption of the vector index"** — see §6.3; the *deferral* is fine, its *scope statement* is
  wrong.
- **"Local-model selection" — DEFECT (severity MEDIUM).** The deferral itself is well-formed for the
  parsing model (a class is pinned, the candidates are named, the benchmark is specified). But it
  travels with this Stack row:

  > | Embedding model + dimension | Phase 1 | Must be pinned before the first index is written; a
  > change is a reindex event |

  That leaves a genuinely shared contract undecided between two units that will be built
  separately — whatever writes `vector_index/` and whatever queries it. The constraint names *when*
  to decide but not *where the decision lives* or *what detects a violation*. Because Tier 3 is
  rebuildable and unencrypted, a model swap does not fail loudly; it produces a silently degraded
  similarity ranking. **Fix:** one clause — *the embedding model id and dimension are recorded in
  the index artifact and verified at open; a mismatch is a startup error naming `pm-ai reindex`,
  never a silent re-embed.* That converts the deferral from "undecided contract" into "decided
  contract, unpinned value", which is a legitimate seed.
- **"Retention policy beyond raw transcripts"** — borderline, acceptable. The *mechanism* is already
  fixed (AD-5's seal-and-supersede segments; AD-3 Tier 1 "Bounded by FR-37 compaction"), so only the
  schedule is open, and a schedule is a knob. Minor wording snag: the deferral says retention for
  "telemetry rows" is unspecified, while AD-3 places harvested telemetry in Tier 1 where compaction
  *is* the answer. Reword to "the compaction *schedule* and thresholds" to avoid reading as though
  the mechanism is open.

---

## 4. Is the named technology verified-current and plausible?

**Verdict: yes — the best-verified section of the document. CONFIRMED against current sources.**

Checked against the current Claude API reference:

| Spine claim | Verdict |
| --- | --- |
| `claude-opus-5`, `claude-sonnet-5` are current model ids | **CONFIRMED** — exact ids |
| "Thinking is **on by default** on Opus 5" | **CONFIRMED** — and specifically true of Opus 5 vs 4.8/4.7, which the spine gets right |
| "`temperature`/`top_p`/`top_k` are rejected (400)" | **CONFIRMED** |
| "Assistant prefill is rejected; use `output_config.format`" | **CONFIRMED** |
| "`output_config.effort` controls depth" | **CONFIRMED** |
| "Opus 5 can return `stop_reason: "refusal"` … HTTP 200 … opt into server-side `fallbacks`" | **CONFIRMED**, including the recommendation to enable fallbacks by default |
| "Prompt-cache minimums differ — **512** on Opus 5, **1024** on Sonnet 5" | **CONFIRMED** exactly, including the non-obvious non-monotonicity |
| "Sonnet 5 introductory $2/$10, expires **2026-08-31**; standard $3/$15" | **CONFIRMED** exactly |
| "`client.beta.messages.tool_runner` is a **beta** SDK surface" | **CONFIRMED** |
| "the Claude Agent SDK ships built-in Bash/Read/Write/Edit" and is therefore prohibited (AD-16) | **CONFIRMED** — the Tool Runner / Agent SDK distinction is stated correctly, which is a common error |

Package pins all match `uv.lock` byte-for-byte: `python-telegram-bot 22.8`, `sqlcipher3 0.6.2`,
`sqlite-vec 0.1.9`, `fastapi 0.141.1`, `uvicorn 0.52.3`, `keyring 25.7.0`, `anthropic[mcp]` (0.124.0
resolved). `pyproject.toml` encodes `python-preference = "only-managed"`, which is the
`enable_load_extension` prerequisite the Stack section calls out. The self-flagged fabricated pin
from the earlier currency review appears to be gone.

Two small currency notes:

- **LOW — the cost caveat re-baselines only Sonnet.** The caveat covers the Sonnet 5 intro→standard
  step, but AD-15 routes `coaching` and `research` to Opus 5 at **$5/$25** — 2.5× Sonnet's standard
  input rate — and Opus appears nowhere in the $20 arithmetic. Since coaching is the flagship flow,
  the caveat understates the September re-baseline by more than "roughly a third on the briefing
  path."
- **NIT — "Python 3.13 (3.14 is the upgrade path)" is already stale in its own repo.** The checked-in
  virtualenv is `.venv/lib/python3.14/`. Not a defect (the Stack says the code owns this), but the
  row now describes the past.

### 4.1 One unverified assumption worth an Open Risk. **PLAUSIBLE, severity MEDIUM.**

AD-16: *"Frontier calls use the Anthropic SDK Tool Runner … whose tool set is exactly the MCP skills
the registry has authorized for that flow."* The documented Tool Runner surface takes **locally
defined** tools (`@beta_tool` functions). MCP-backed tools reach the API by a different route
(`mcp_servers` + `tools=[{type: "mcp_toolset"}]` under its own beta), and on that route **tool
execution happens outside this process** — which would bypass `SkillRegistry.invoke` and with it
AD-18's permission gate, AD-20's key check, and AD-37's per-target lock, all in one step.

The spine never states which side of the MCP boundary a skill executes on. The `[mcp]` extra in the
Stack suggests the in-process client (which is the safe answer), but "suggests" is not a decision,
and the entire class-M security argument rests on it. **Fix:** one sentence in AD-16 or AD-18 —
*MCP skills execute in-process via the local MCP client; the server-side MCP connector is prohibited,
because tools executed outside this daemon bypass the registry gate.* Cheap to add, and it is the
load-bearing assumption of AD-1.

---

## 5. Does it ratify rather than contradict `pm_ai/`?

**Verdict: ratifies, and the reconciliation is unusually thorough — with three incomplete spots.**

Ratified, checked line by line: AD-3's tier tables ↔ `ARTIFACT_TIER`/`assert_reindex_safe`;
AD-14's disjoint state names ↔ the import-time `assert not _overlap`; AD-32's three conditions ↔
`core/command_authorization.classify`; AD-34's grammar ↔ `SourceRef.parse`/`TargetRef.parse` (and
`TargetRef` genuinely rejects `#`/`?` sub-resources, as AD-34.1 claims); AD-35's fail-closed rule ↔
`evaluate_commitment`; AD-38 ↔ `assert_writable`; AD-30 ↔ `.importlinter`'s `layering` +
`surfaces-through-core` + `domain-imports-nothing`; AD-9's `HarvestResult` carrying `CoverageWindow`
↔ `domain/harvest.py`. The Consistency Conventions' "**No bare `Scope`**" is honoured in code
(`DataScope`, `SkillPermission`, `ScopeKind`). `uv run pytest tests/` → **65 passed, 32 skipped**,
consistent with the spine's own "Phase 1 exit criterion: zero skips."

Where reconciliation is incomplete:

1. **AD-36's normalization pass does not exist** — §2.2. This is the one place the spine describes a
   mechanism the code does not have and the tests do not check.
2. **The diagrams predate AD-30. CONFIRMED, severity LOW-MEDIUM.** AD-30 is the newest and most
   structural AD — *"`pm_ai.app` is the composition root … it orchestrates the ingestion pipeline"* —
   and `app` appears in **no diagram**. The Invariants diagram runs `SCHED --> C[Core]` and
   `C --> P[Ports]`; the container diagram runs `CONN --> SAN --> CORE --> STORE`. Both depict the
   pre-AD-30 topology in which core orchestrates the pipeline, which is exactly what AD-30's
   `Prevents` says was impossible. Add an `APP[Composition root]` node driving `SCHED`, `CONN`,
   `SAN`, `CORE`, and `STORE`, or the first reader to build from the picture rebuilds the bug.
3. **`companions: []` is false, and the companion contradicts AD-3. CONFIRMED, severity MEDIUM.**
   `SOLUTION-DESIGN.md` sits in the same directory, declares itself *"Companion to
   ARCHITECTURE-SPINE.md"*, and says:

   > "Delete the database and the vector store, run `pm-ai reindex`, and **you lose nothing**."

   against AD-3:

   > "Losing it loses pending external writes and resets harvest position — **a real consequence, not
   > a cache miss**" … "`pm-ai reindex` deletes and rebuilds the Tier-3 artifacts and *cannot* reach
   > Tier 2."

   The companion is the pre-reconciliation claim verbatim. Either it needs the same edit AD-3 got, or
   the frontmatter needs to stop denying it exists. Both, ideally.

Two smaller drifts (both benign, both worth one word each): the layer table lists seven ports
(`ConnectorPort, ModelPort, StoragePort, TranscriptSourcePort, SkillPort, KeychainPort, SurfacePort`)
where `pm_ai/ports/__init__.py` defines three — fine for a Phase-1 slice, but `SurfacePort` has no AD
governing it and no evident purpose under AD-30, and `KeychainPort` overlaps `platform/` per AD-26.
And `pm_ai/storage/service.py`'s `_Tier2` is an in-memory dataclass rather than SQLCipher — correctly
a slice stub, not a contradiction, since AD-3's guarantee is physical separation and separation holds.

---

## 6. Does it cover the PRD's capabilities?

**Verdict: FR coverage is complete; NFR coverage is overclaimed, and two silent PRD deviations.**

**FR-01..FR-37: covered.** Every FR is named or falls inside a named range. `FR-15` is the only one
without a direct mention and it is inside the Capability Map's `FR-09..FR-17` row. Good.

### 6.1 Four NFRs are bound by the frontmatter and appear nowhere. **CONFIRMED, severity MEDIUM.**

Frontmatter: `binds: [FR-01..FR-37, NFR-01..NFR-14]`. Grep for each: **NFR-02, NFR-03, NFR-05, and
NFR-06 do not appear in the spine at all.** All four are end-to-end *pipeline* budgets:

- NFR-02 — 20 s voice note → rendered draft cards, **≤45 s**
- NFR-03 — meeting end → parsed, sanitized, anchors extracted, WI updated, research queued, **≤600 s**
- NFR-05 — transcript-triggered research synthesized and dispatched, **≤15 min**
- NFR-06 — on-demand missed meeting → Summary Card, **≤300 s**

AD-22 fixes two *per-operation* budgets (retrieval 50–150 ms, synthesis ≤60 s) and AD-21 fixes the
sync/async boundary — neither composes into an end-to-end pipeline budget. A feature builder chaining
transcription → extraction → classification → a frontier synthesis → a class-M mutation has four
model calls, a job-queue hop, and no stated ceiling. Each of those four NFRs is a multi-component
budget, which makes it exactly the kind of thing a spine has to allocate rather than leave to
whichever component is built last.

**Fix:** either add a clause to AD-22 (*pipeline budgets are allocated per stage in the epic that
owns the pipeline; AD-22's per-call budgets are the components, not the total*) or narrow the
frontmatter. Silently claiming to bind them is the worst of the three options.

### 6.2 NFR-13's disclosure destination is changed without a note. **CONFIRMED, severity LOW.**

PRD NFR-13: *"Every frontier call records token counts and a cost estimate to **event_log.md**."*
AD-38 correctly overrides this — routing cost records to `event_log/` in a git-committed project
scope is the leak AD-38 exists to prevent, and the reasoning is sound. But AD-31 explicitly flags
its PRD conflict (*"**FR-16 must say so.** A charter that means something narrower than its words is
worse than no charter"*) while AD-38 silently supersedes NFR-13's stated destination. Same treatment,
one line: *NFR-13's `event_log.md` destination is superseded by AD-38; the PRD needs the edit.*

### 6.3 AD-6 relaxes NFR-08's encrypted set, on a rationale that does not hold for `derived.db`. **CONFIRMED, severity MEDIUM.**

PRD NFR-08 encrypts *"the operational telemetry index (event_telemetry.db)"* and explicitly exempts
only *"the vector index, which holds derived embeddings rather than recoverable text."* AD-3 splits
that one file into `operational.db` (Tier 2) and `derived.db` (Tier 3), and AD-6 then says:

> "The Tier-3 artifacts `derived.db` and `vector_index/` are unencrypted: both are rebuildable and
> hold **indexes and embeddings rather than recoverable raw text**"

AD-3 defines Tier 3 as *"Search and commitment indexes, `vector_index/`, caches."* A **search** index
over Tier-1 markdown is not analogous to an embedding table — an FTS index stores the indexed terms,
and in SQLite FTS5 the common configurations retain the content itself. So the PRD's exemption
rationale was written for embeddings and has been extended to a full-text index where it likely does
not apply. Two consequences: a security-relevant relaxation of NFR-08 that the PRD does not authorize
and the spine does not flag as a deviation, and a `Deferred` entry ("Encryption of the vector index …
Revisit only if the index starts holding recoverable raw text") whose trip-wire is aimed at the wrong
artifact.

**Fix:** state the FTS configuration (`content=''` external-content tables keep no text) or move
`derived.db` into the encrypted set. Either is fine; leaving the rationale as written is not, because
it will be quoted later as if it had been checked.

---

## 7. Is every dimension this altitude owns decided, deferred, or an open question?

**Verdict: the operational envelope is unusually well covered for a spine — with two genuine
silences.**

Covered and explicit: **deployment** (`launchd` user agent, `KeepAlive`, single instance),
**install/update** (`uv tool install`), **environments** (*"one — the user's Mac. No staging tier"* —
a real decision, correctly stated rather than omitted), **provider strategy** (Ollama local +
Anthropic frontier, split by task class in AD-15), **observability** (three destinations, no overlap:
`~/.pm-ai/logs/` JSON, `event_log/` domain truth, `disclosure.md` cost/provenance — and AD-24
prohibits mixing them), **health** (`pm-ai doctor` with a named probe list), **backup/restore**
(Tier 1 + Tier 2 named, Tier 3 explicitly excluded, restore's re-execution window stated),
**failure/recovery** (AD-20 durable queue + `PENDING_RETRY`, AD-35 fail-closed on coverage gaps,
AD-37 CAS). This is more than most spines at this altitude carry.

### 7.1 SILENT — credential and secret lifecycle. **CONFIRMED, severity MEDIUM-HIGH.**

Greps over the whole spine: `oauth` = **0**, `refresh` = **0**, `re-auth`/`reauth` = **0**,
`rotat` = 2 (both about log rotation, not key rotation).

What *is* covered: where credentials live (encrypted `config.json`, AD-6), where the master key lives
(macOS Keychain, AD-6), who hands credentials to a connector (`app`, AD-30), and one Open Risk about
Keychain survival across OS upgrade. What is not covered is everything that happens after issuance,
for a product whose PRD (FR-35) says connector setup *"prompts for domain endpoints, authentication
tokens, or OAuth keys"* across GitLab, Teams, Outlook, Slack, Jira, Notion, and HR platforms:

- who refreshes an expiring OAuth token, and where the refresh token is stored;
- what a connector does when refresh fails — AD-9 gives it `harvest(since)` and forbids it a thread,
  so it has no stated way to ask for re-consent;
- how the user is told that harvesting for one project stopped three days ago (see Miss 3);
- whether an expired credential is distinguishable from a coverage gap — this one bites AD-35
  directly: `CommitmentState.UNKNOWN` is the correct answer for a sleeping laptop *and* for a dead
  token, and the two want very different user-facing behaviour;
- key rotation for the Tier-2 SQLCipher master key (only export/import is documented).

Seven connectors, each with its own auth dance, and no shared contract for the lifecycle. This is
textbook per-adapter divergence — the exact failure AD-9 was written to prevent for *scheduling* and
did not extend to *credentials*.

**Fix:** an AD siblings to AD-9 — *credential acquisition, refresh, and expiry are owned by the
composition root, not by connectors; a connector signals `AuthExpired` as a typed domain error and
never prompts, re-auths, or refreshes; the daemon surfaces one re-auth request per instance and
marks the instance's coverage window explicitly `unauthenticated` so AD-35's sweeper distinguishes
"could not see" from "not permitted to see."*

### 7.2 SILENT — schema migration for Tier 2. **CONFIRMED, severity MEDIUM.**

`grep -i migrat` returns exactly one hit, in AD-6, about the Keychain key: *"raw key export/import is
the documented migration path."* Nothing addresses schema evolution.

This matters more here than in a typical system because of AD-3's own logic: Tier 2 is the tier that
is **never a rebuild target** and **must be backed up**, because nothing can reconstruct it. That
makes every schema change to `operational.db` a data-migration event with no owner, no versioning
rule, and no failure mode stated. Tier 3 has a clean story (`sqlite-vec` minor bump = reindex event;
data survives via AD-3). Tier 1 has a partial one (AD-27: *"Both enumerations are versioned so parsers
can read historical entries"*, plus "parsers must tolerate hand-edits"). Tier 2 — the only one that
cannot be regenerated — has none.

Related and equally silent: what happens to a `PENDING_RETRY` job row, or a `staged` Proposal, whose
payload schema changed under it during an upgrade. AD-13 says a registered proposal type declares a
payload schema; nothing says that schema is versioned or what a worker does with a row it cannot
parse. Given AD-20's at-least-once delivery, "cannot parse" must have a defined answer or it will get
one per feature.

**Fix:** one Consistency Convention row (*`operational.db` carries a schema version; migrations are
forward-only, run at daemon start, and back up the file first*) plus a clause on AD-13 (*a staged
payload records its type version; a worker that cannot parse a row moves it to `superseded` and
notifies, never silently drops or best-effort coerces*).

### 7.3 Dimensions correctly judged not-applicable

Multi-user, shared deployment, staging environments, horizontal scale, and CI/CD for the product
itself are all either in Deferred or covered by "one — the user's Mac." No complaint.

---

## Diagrams, ids, placeholders

**Mermaid syntax: all four diagrams parse.** `graph TD`/`graph TB`/`graph LR`/`erDiagram`, quoted
labels around `—`, `<br/>`, and `/`, `-.text.->` dotted-with-label edges, `-.->|"label"|` — all valid.

**Structure: three of four carry real structure; one is mislabelled.**

- **Diagram 1 (under "Invariants & Rules") is a call-flow diagram sitting where a dependency diagram
  is claimed. CONFIRMED, severity LOW-MEDIUM.** It renders `P[Ports] --> CONN[Connector adapters]`,
  immediately above prose reading *"Core services import `ports` and `domain`, never an adapter"* and
  a `.importlinter` contract named `ports-depend-only-on-domain` that forbids exactly that edge. As
  flow it is fine; as the picture of the layering contract it inverts it. Label it "runtime flow", or
  redraw the arrows as imports.
- Same diagram, plus the container view: **no `app` node** — see §5.2.
- **Diagram 3 (Scopes and storage)** is the strongest — the `A3 -.->|"never referenced by"| PROJ`
  edge encodes AD-38's actual invariant. Minor: the arrow points from `A3` to `PROJ` while the label
  reads passively from `A3`'s side, so the direction reads backwards. `PROJ -.->|"never references"| A3`
  says the same thing without the double-take.
- **Diagram 4 (Core entities) contradicts the Consistency Conventions. CONFIRMED, severity LOW.**
  It declares `SCOPE ||--o{ CONNECTOR_INSTANCE` and `SCOPE ||--o{ GOAL`, against:

  > **No bare `Scope`** — the word meant four things and now names none of them (AD-18)

  The code got this right (`DataScope`). Rename the entity `DATA_SCOPE`. It is a two-character fix,
  but the whole point of that convention is that the bare word is how a project named `personal`
  slipped past a privacy check.

**AD ids: clean. CONFIRMED.** AD-1 through AD-38, each appearing exactly once as a heading, no gaps,
no reuse, no renumbering artifacts. Stable and unique.

**Placeholders:**

- `status: revision-in-progress` in the frontmatter, on a document being used as build substrate. If
  the reconciliation is done, promote it; if not, the substrate claim is premature.
- `companions: []` — false (§5.3).
- `[NEW]` / `[ADOPTED — revised 2026-08-19]` tags on nine ADs and not the other twenty-nine. These
  are review-cycle metadata that will read as noise in six months. Either tag all or tag none.
- The two Stack "Phase 1" rows are **not** placeholders in the bad sense — the section explicitly
  says *"a row marked Phase 1 is a decision the build makes, not one this document has made,"* which
  is the correct seed/invariant distinction. (The embedding row still needs the clause in §3.)

---

## Findings, ranked

| # | Sev | Finding | Status |
| --- | --- | --- | --- |
| 1 | HIGH | AD-20's `Prevents` ("duplicate external writes on replay") is not closed by its `Rule`: the key is honoured only against a local ledger written *after* the external call returns, so every mutation has a crash window that double-writes. Nothing requires the key be sent to the provider or an intent record written first. | CONFIRMED |
| 2 | HIGH | AD-36's `Rule` leaves its join key undefined ("matching one of those") — the ledger holds `(lock_key, opaque external_id)`, events are keyed by AD-34 `SourceRef`, and no rule maps between them. The normalization pass it depends on is also unbuilt; the one connector asserts `EXTERNAL` for every row. | CONFIRMED |
| 3 | MED-HIGH | AD-12 enforces sanitization at the producer while its `Prevents` names the consumer ("a new … path feeding unsanitized text into an LLM context"). The project's own enforcement README concedes it is "a review catch". The chokepoint (AD-15's single `ModelPort`) and the type (`Sanitized`) both already exist. | CONFIRMED |
| 4 | MED-HIGH | Credential/secret lifecycle is a fully silent dimension: `oauth`/`refresh`/`re-auth` = 0 hits, across seven connectors the PRD says use OAuth. AD-9 forbids a connector its own thread, so it has no stated route to re-consent; an expired token is indistinguishable from AD-35's coverage gap. | CONFIRMED |
| 5 | MED | Frontmatter binds `NFR-01..NFR-14`; NFR-02/03/05/06 — the four end-to-end pipeline SLAs — appear nowhere. AD-21/AD-22 give per-call budgets that do not compose into a pipeline total. | CONFIRMED |
| 6 | MED | `companions: []` is false, and the companion that exists contradicts AD-3 verbatim: "Delete the database and the vector store … you lose nothing" vs Tier 2 "Losing it loses pending external writes … not a cache miss." | CONFIRMED |
| 7 | MED | AD-6 relaxes NFR-08 by leaving `derived.db` — which holds the **search** index over Tier-1 markdown — unencrypted on an "embeddings, not recoverable text" rationale that holds for vectors and likely not for FTS. The Deferred trip-wire watches the wrong artifact. | CONFIRMED |
| 8 | MED | No rule binds a class-M `TargetRef`'s scope to the authorizing `DataScope` — cross-project mutation containment is unaddressed in both directions. | CONFIRMED |
| 9 | MED | Tier-2 schema migration is silent — the one tier that can never be rebuilt has no versioning, migration, or unparseable-row rule. Staged-proposal payload versioning likewise. | CONFIRMED |
| 10 | MED | AD-16 assumes the beta Tool Runner can be driven by MCP-authorized skills; the spine never states whether skills execute in-process or via the API's server-side MCP connector, and the latter would bypass `SkillRegistry` (AD-18 + AD-20 + AD-37) entirely. | PLAUSIBLE |
| 11 | MED | No AD governs how an asynchronous failure reaches the user; AD-21 covers only the success path, and the spine's own Open Risk ("the 07:00 briefing simply stops") is the symptom. | CONFIRMED |
| 12 | MED | Deferred "Local-model selection" carries an undecided shared contract: the embedding dimension is pinned "before the first index is written" but has no recorded home and no mismatch detection, so a swap degrades silently. | CONFIRMED |
| 13 | LOW-MED | The diagrams predate AD-30 — `app` appears in none of them, and both flow diagrams depict core orchestrating the pipeline, which is precisely what AD-30's `Prevents` says was impossible. | CONFIRMED |
| 14 | LOW-MED | Diagram 1 renders `Ports --> Connector adapters` under an "Invariants & Rules" heading, inverting the layering contract that `.importlinter`'s `ports-depend-only-on-domain` enforces. | CONFIRMED |
| 15 | LOW | AD-21's trigger ("expected duration") is not a measurable predicate; AD-22 already supplies a structural restatement. | CONFIRMED |
| 16 | LOW | The Core-entities ER diagram uses a bare `SCOPE` entity against the Conventions' explicit "No bare `Scope`". | CONFIRMED |
| 17 | LOW | AD-38 silently supersedes NFR-13's `event_log.md` destination, where AD-31 flags its PRD conflict explicitly. Same treatment warranted. | CONFIRMED |
| 18 | LOW | AD-18 declares plural `SkillPermission`s; the port declares one. AD-7's surface-parity clause is unenforceable and mapped in the README to a contract that checks something else. | CONFIRMED |
| 19 | LOW | The cost caveat re-baselines Sonnet 5 only; Opus 5 at $5/$25 carries coaching and research and is absent from the $20 arithmetic. | CONFIRMED |
| 20 | NIT | `status: revision-in-progress`; `[NEW]` tags on 9 of 38 ADs; "Python 3.13" while the repo's venv is 3.14. | CONFIRMED |

---

## What is genuinely good, and should survive editing

Recorded because a review that lists only defects invites over-correction:

- **AD-1's five-class egress table** is the right shape — it replaced a stricter-sounding blanket
  rule with one the stack does not contradict, and says so in the text. That instinct (a rule that
  survives contact) is what makes a spine usable.
- **AD-27 + AD-34 together.** Closing a type enumeration and then noticing the payload and the
  reference grammar were still open is the single highest-value pair in the document.
- **AD-35's `UNKNOWN` and AD-36's third `Provenance` value.** Both are the same insight — a
  two-valued domain fails open — applied in two places, with the failure mode spelled out.
- **AD-38 inverting an earlier decision** rather than patching it, and saying which reviewers found
  it. Auditability of the spine's own history.
- **The Enforcement section.** ADs mapped to named checks, a README listing the five that are *not*
  mechanically enforced, and an honest "zero skips" exit criterion with 32 skips currently showing.
  Most spines claim enforcement; this one publishes its gaps.
- **The Stack's integration prerequisites.** The `enable_load_extension` note is the kind of finding
  that costs a week when it is discovered on a clean install instead of in a document, and it is
  encoded in `pyproject.toml` rather than only described.
