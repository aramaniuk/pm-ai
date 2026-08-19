# Reviewer Gate — consolidated triage

Four independent reviewers, run 2026-08-18/19 against `ARCHITECTURE-SPINE.md` as
fresh agents with no authoring context.

| Lens | Findings | Verdict |
| --- | --- | --- |
| Adversarial | 22 | Well-owned, but leaves cross-cutting invariants for downstream epics to invent |
| Divergence | 17 pairs | Strong on mechanism, weak on meaning; 7 pairs produce silent wrong answers |
| Implementer | 62 (14 blocking) | **None of the three slices is buildable without inventing decisions** |
| Tech currency | 20 | Stack mostly real; one pin invented, eight integration prerequisites absent |

**Gate verdict: the spine is not build-ready.** It is structurally sound —
perimeter, layering, single-writer, and model routing all survived attack — and
incomplete at the level of *meaning*: what words denote, who owns what, and what
happens over time.

Two independent reviewers found the AD-1 egress contradiction separately, which
is the strongest available signal that a finding is real rather than clever.

---

## Root causes

The ~121 findings collapse into twelve clusters. Most resolve as one AD each.

### C1 — AD-1's "only egress" is false as written
*adversarial #4 · divergence #9 · implementer #5 · tech F-14*

Connectors call external APIs with `httpx`; the frontier adapter calls Anthropic;
whisper.cpp has no Python API so the local adapter needs `subprocess`, which AD-1
bans; `.importlinter` forbids `httpx` in `surfaces` while python-telegram-bot
depends on it. The contract contradicts the stack in four places.

**Fix:** rewrite AD-1 around *egress classes* — read-harvest (connectors),
mutation (MCP skills only), model (the router), and local subprocess (a single
allowlisted binary path, `shell=False`, `models/local` only). Update the import
contracts and AST rules to match. Everything mutating still routes through MCP.

### C2 — Event identity is undefined beyond `type`
*divergence #1, #2, #13 · implementer #3*

AD-27 closed the type enumeration but never enumerated it, and left every other
field open. GitLab writes `source_ref` as a URL, Jira as a ticket key, so
commitment verification cannot join them. `actor` is a commit email from one
source and a speaker label from another, so one engineer becomes four people in
the FR-30 metrics that feed a performance review.

**Fix:** one AD fixing canonical event identity — `source_ref` grammar, actor
resolution to a single identity, and the dedup key. Then actually write the
enumerations the AD-27 test asserts against (it currently passes vacuously).

### C3 — Time semantics undefined
*divergence #3, #4*

`occurred_at` (provider, skewable) vs `ingested_at` (local) vs file order are
used interchangeably. A laptop asleep over a weekend sends irreversible FR-26
"why isn't this done" messages for work already delivered.

**Fix:** one AD — which timestamp governs due-date evaluation, which governs
ordering, and how ledger folding resolves.

### C4 — pm-ai verifies its own writes
*divergence #14 — the worst finding*

FR-06's executor posts a comment to WI-108; FR-34's verifier later reads WI-108
activity as fulfilment evidence. Both AD-compliant. The system marks commitments
`FULFILLED` on telemetry it manufactured.

**Fix:** an AD excluding pm-ai-authored events from evidence — every event
carries provenance, and the verifier admits only externally-authored ones.

### C5 — The storage truth model contradicts itself
*adversarial #3 · implementer #1 · divergence #4*

AD-3 calls `event_telemetry.db` disposable and rebuildable from markdown, but the
job queue (AD-20), connector cursors (AD-9), and harvested telemetry (FR-02) live
there and appear in no markdown. `pm-ai reindex` would silently discard pending
external writes and reset every cursor — while the AD-3 test stays green.

**Fix:** split the model into three tiers — *truth* (markdown), *operational
state* (durable, NOT rebuildable: queue, cursors, keys), *derived* (rebuildable:
indexes, vectors). Restate AD-3 over the third tier only, and give operational
state its own durability and backup rule. Harvested telemetry needs a decided
home.

### C6 — No composition root
*implementer #2*

Core may not import adapters; adapters may not import each other. The named
pipeline `harvest → sanitize → normalize → index → extract` therefore has no
legal module anywhere in the tree, and connector credentials have no route from
encrypted storage to the adapter.

**Fix:** add an application/wiring layer above core in the paradigm, the source
tree, and the layering contract.

### C7 — No compare-and-swap on shared entities
*divergence #6, #7*

Telegram and CLI approve the same proposal concurrently and create two HR goals.
The expiry sweeper and the job worker race, so an 11-day-old approved change
posts after expiry — despite AD-13 saying expired proposals never execute.

**Fix:** an AD requiring versioned CAS on proposal and commitment transitions.

### C8 — Nothing survives a change over time
*divergence #11, #12, #16, #17*

Taxonomy rename orphans a year of markdown; a connector v2 cursor format change
stops telemetry silently (AD-9's opacity rule guarantees it is undetectable);
the encryption toggle migrates nothing in either direction; NFR-09 purges raw
transcripts at 30 days while every fact carries a `source_ref` pointing at them.

**Fix:** version the ledger grammar and taxonomy; make cursor format changes a
declared reset; define toggle migration; reconcile retention against citations
(**product decision** — see D3).

### C9 — Frontier data boundary undeclared
*adversarial #1*

`coaching` routes to Opus 5 — an external API — while FR-16's charter calls that
material hardware-bound. No AD says what may enter a frontier prompt.
**Product decision — D1.**

### C10 — Spoken commands auto-execute with no authorization model
*adversarial #2 · implementer #4*

FR-05 executes "pm-ai, update WI-226" with no approval and no speaker identity
binding. Anyone in the meeting — or anyone dropping a `.txt` into AD-23's watched
folder — gets unapproved external write access. **Product decision — D2.**

### C11 — Stack corrections
*tech F-01 … F-20*

- **F-01: `Llama 3.3 8B` does not exist.** Llama 3.3 ships 70B only. Invented.
  The nearest "correction" a builder would make (`llama3.3:70b`) needs ~40 GB and
  swaps a 16 GB machine to death — the exact PRD open question AD-19 claims to guard.
- F-02: FastAPI pinned 0.136.1; current is 0.141.1 (five minors stale).
- F-11: `sqlite-vec` cannot load into stock macOS Python — `enable_load_extension`
  is *absent*, not disabled. uv-managed interpreters work; system ones do not.
- F-12: `sqlcipher3-binary` is Linux-x86_64 only; `sqlcipher3` has macOS arm64 wheels.
- F-13: whisper.cpp Core ML is a build-time feature needing Python 3.11 and a
  separate toolchain; first run blocks minutes on ANE compilation.
- F-15: `OLLAMA_KEEP_ALIVE` holds models resident 5 min — AD-19's client-side
  bound does not reach the Ollama server; needs `OLLAMA_MAX_LOADED_MODELS=1`.
- F-16/F-17: PTB's `run_polling()` seizes the event loop AD-19 declares singular;
  its `[job-queue]` extra installs a second scheduler inside the daemon.
- F-06: the 512-token cache minimum is Opus 5's; briefings run on Sonnet 5 (1024).
- F-07: Opus 5 can return `stop_reason: "refusal"`; nothing in the spine handles it.
- F-08: the $20 target is anchored to Sonnet 5 introductory pricing expiring 2026-08-31.
- F-09: `tool_runner` is a beta SDK surface, pinned unannotated.

**Good news, verified by execution:** SQLCipher + sqlite-vec *do* work together.
The integration risk that drove the "leave the vector index unencrypted" decision
does not exist. The decision may still stand on its other merits, but its stated
reason was wrong.

### C12 — Enforcement gaps
*divergence enforcement section · adversarial #5*

- The idempotency test — the one the README calls "keep this if you keep only
  one" — is single-process, so a `time.time()` seed passes it.
- The file-write AST scan **excludes `storage`**, the only layer that could
  rewrite a ledger in place and break append-only.
- `.importlinter` silently permits `surfaces → storage/models`, bypassing core.
- `pm_ai.ports` has no forbidden-import contract.
- 14 stated rules have no check, including the two most security-relevant:
  AD-1's "every skill invocation appends to `event_log.md`" and AD-13's "no
  external mutation without an approved Proposal."
- `test_ad8` uses Flask's `test_client()` against a FastAPI stack.
- Three ADs dismissed as "human judgement" are mechanizable — notably AD-12 via
  a `SanitizedText` type on `ModelPort`, which beats any test.
- AST checks currently pass **vacuously** against an empty package; "zero skips"
  is gameable with stubs.

---

## Decisions required

These are product calls, not architecture calls.

**D1 — What may leave the machine in a frontier prompt?** (C9)
Options: nothing from the personal scope (coaching runs local-only, materially
weaker); personal scope allowed but never raw (summarized/redacted first);
allowed as-is with the charter reworded to cover model APIs explicitly.

**D2 — Do spoken commands keep auto-execute?** (C10)
Options: keep, bound to a verified speaker identity; downgrade to a Proposal like
implicit extractions (loses the zero-friction promise of UJ-7); keep for a
narrow allowlist of low-blast-radius verbs and stage the rest.

**D3 — How long must evidence outlive a transcript?** (C8)
NFR-09 says 30 days; commitments and dossiers cite it for far longer. Options:
extend retention; persist extracted quotes at extraction time so citations
survive the purge; accept expiring citations and mark them.

---

## Recommended sequence

1. **Stack corrections (C11)** — mechanical, no decisions, removes a fabricated
   pin before anyone acts on it.
2. **D1–D3** — everything in C8/C9/C10 depends on them.
3. **C1, C5, C6** — the three that block the implementer outright.
4. **C2, C3, C4, C7** — the silent-wrong-answer cluster.
5. **C12** — re-point enforcement at the revised ADs, then re-run the gate.

Re-running the divergence lens after (4) is the check that the revision worked.
