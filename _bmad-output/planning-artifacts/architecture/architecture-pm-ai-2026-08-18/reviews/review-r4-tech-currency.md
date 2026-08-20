# R4 — Technology Currency Review (independent re-verification)

- **Target:** `ARCHITECTURE-SPINE.md` — sections *Stack*, *Integration prerequisites*, *Anthropic API notes*, *Cost-model caveat*
- **Reviewer:** fresh reviewer, no prior context on this document
- **Date:** 2026-08-19
- **Method:** every named technology re-checked against a primary source — PyPI JSON API, GitHub Releases API, vendor docs, vendor model registry, or local empirical test. The document's own "re-verified on 2026-08-19" note was treated as an unverified assertion and ignored as evidence.
- **Prior context:** a previous currency review found a fabricated version pin in this document. Nothing was assumed.

---

## Verdict

**The Stack table is in materially good shape — better than the brief anticipated.** 14 of 15 rows verified as real, current, and correctly constrained, several of them with numbers that match the registry *exactly* (`llama3.1:8b` at 4.9 GB, `sqlcipher3-binary`'s Linux-only wheel set, `keyring`'s universal2 requirement). Every Anthropic API behaviour the document asserts is confirmed against live documentation. No second fabricated pin was found.

**But the single highest-consequence claim in these sections is wrong.** The *Cost-model caveat* — the one section that drives a scheduled future action ("re-baseline in September") — is contradicted by Anthropic's live pricing page. The introductory pricing it says expires on 2026-08-31 has been made permanent, and the price increase it tells the reader to plan for will not happen.

That failure is instructive rather than incidental: it is the one claim in these sections that depends on a *forward-looking vendor commitment* rather than on a current registry state. Registry states were checked correctly; the vendor's change of plan was not. The document's blanket assurance that "every row [was] re-verified against its registry" is therefore true and also insufficient — pricing is not in a registry.

---

## Severity summary

| # | Severity | Claim | Verdict |
|---|---|---|---|
| F1 | **HIGH** | Sonnet 5 intro pricing "$2/$10 … expires 2026-08-31; standard pricing is $3/$15 … Re-baseline in September" | **CONTRADICTED** |
| F2 | **MEDIUM** | "Every row re-verified against its registry on 2026-08-19" | **OVERSTATED** — scope excludes the one thing that was wrong |
| F3 | **MEDIUM** | `stop_reason: "refusal"` framed as an Opus-5 concern | **INCOMPLETE** — applies to Sonnet 5, which is the briefing path |
| F4 | **MEDIUM** | "Thinking is **on by default** on Opus 5" + `max_tokens` truncation warning | **INCOMPLETE** — also true of Sonnet 5, unstated |
| F5 | **MEDIUM** | `anthropic`, `uv`, `Ollama`, `ollama` client pinned as "latest" | **NOT A PIN** — floats a beta-surface dependency |
| F6 | **MEDIUM** | `sqlite-vec` treated as a live dependency | **CONFIRMED but risk understated** — repo idle ~3 months, 0.1.10 stuck in alpha |
| F7 | **LOW** | `uvicorn 0.52.3` | **STALE** — 0.52.4 released 2026-08-19 |
| F8 | **LOW** | Bridge lifecycle `initialize() → start() → start_polling()` | **IMPRECISE** — `start_polling` is on `Application.updater`, not `Application` |
| F9 | **LOW** | `OLLAMA_NUM_PARALLEL=1` presented as a required fix | **REDUNDANT** — already the documented default |
| F10 | **LOW** | whisper.cpp "Metal alone is on by default on Apple Silicon" | **PARTIALLY CONFIRMED** — README asserts Metal GPU inference, not the default-on wording |
| F11 | **INFO** | "Python 3.13 (3.14 is the upgrade path)" | **CONFIRMED but conservative** — 3.14 GA since 2025-10-07; every pinned dep already ships 3.14 wheels |
| F12 | **INFO** | "Embedding model + dimension — Phase 1" | **UNVERIFIABLE by construction** — no candidate named, so nothing to check |

---

## F1 — CONTRADICTED (HIGH): the Sonnet 5 introductory-pricing expiry

**Document claims:**

> The $20/month target (AD-17) is anchored to Claude Sonnet 5 **introductory** pricing of $2/$10 per Mtok, which expires 2026-08-31; standard pricing is $3/$15. Any spend measured before that date understates the steady-state figure by roughly a third on the briefing path. Re-baseline in September.

**Source — Anthropic pricing documentation, retrieved 2026-08-19** (`https://platform.claude.com/docs/en/about-claude/pricing`), verbatim callout on the model-pricing table:

> The $2/$10 per million input/output token pricing for Claude Sonnet 5, announced at launch as introductory pricing through August 31, 2026, **is now the standard price. The previously scheduled increase to $3/$15 per million input/output tokens on September 1, 2026 will not occur.**

The same page's model-pricing table lists Claude Sonnet 5 at **$2 / MTok input, $10 / MTok output** with no introductory qualifier, alongside Claude Sonnet 4.6 at $3/$15 — confirming that $2/$10 is now Sonnet 5's own standing rate rather than a discount against a $3/$15 base.

**Every clause of the caveat is now false:**

| Document clause | Reality |
|---|---|
| "expires 2026-08-31" | Made permanent; no expiry |
| "standard pricing is $3/$15" | Standard pricing for Sonnet 5 *is* $2/$10 |
| "understates the steady-state figure by roughly a third" | Measured spend **is** the steady-state figure |
| "Re-baseline in September" | Nothing to re-baseline |

**Why this matters beyond a stale number.** This is the only claim in the reviewed sections that schedules a future action, and the action is now wrong in the direction that costs real work: someone re-baselines in September, finds the numbers unchanged, and either wastes the cycle or — worse — concludes the measurement harness is broken. It also propagates a wrong assumption into AD-17: the document currently tells the reader that any pre-September $20 measurement is optimistic by ~33% on the briefing path. It is not. The $20 target is anchored to prices that are now durable, which makes AD-17's monitoring target *more* trustworthy than the document claims, not less.

**Also note the cached-vs-live trap.** The `claude-api` skill's own cached model table (cached 2026-06-24) still carries the old framing — "$3.00 ($2.00 intro through 2026-08-31)". A reviewer consulting only that cache would have marked this claim CONFIRMED. The live docs page is authoritative and newer. This is worth recording as a method note: for pricing, cached tables are never sufficient.

**Recommended replacement:**

> The $20/month target (AD-17) is anchored to Claude Sonnet 5 at $2/$10 per Mtok and Claude Opus 5 at $5/$25. Sonnet 5's $2/$10 was introduced as promotional pricing through 2026-08-31 and was subsequently made permanent — the scheduled increase to $3/$15 was cancelled (Anthropic pricing docs, verified 2026-08-19). Measured spend is therefore steady-state; no September re-baseline is needed. Re-verify prices before any decision that depends on them — a pricing page is not a registry, and this figure has already moved once.

---

## F2 — OVERSTATED (MEDIUM): "every row re-verified against its registry"

**Document claims (Stack preamble):**

> Every row re-verified against its registry on 2026-08-19 after a currency review found one fabricated pin.

**Assessment.** The registry verification genuinely happened and was done well — see the Confirmed table below; several rows match registry values to the digit. But the sentence functions as a blanket assurance covering the whole section, and two things it appears to cover were not caught:

1. The pricing claim (F1) is not in any registry, and it is wrong.
2. `uvicorn 0.52.4` was published at 2026-08-19T06:27:40Z (PyPI JSON API) — the same calendar day the row claims verification — and the row still reads 0.52.3.

Neither is damning on its own. Together they show that the assurance's scope is narrower than its wording, which is the same failure pattern this document elsewhere identifies and corrects in AD-1 ("a blanket rule the stack contradicts on day one") and AD-31 ("a charter that means something narrower than its words is worse than no charter"). The Stack preamble makes exactly that mistake about itself.

**Recommendation.** Scope the sentence to what was actually done and name what it excludes: *"Package versions re-verified against PyPI and vendor release feeds on 2026-08-19. Vendor pricing and API behaviour are not registry-backed and are cited to dated documentation retrieved the same day; both can change without a version bump."*

---

## F3 — INCOMPLETE (MEDIUM): refusal handling scoped to Opus 5 only

**Document claims:**

> **Opus 5 can return `stop_reason: "refusal"`** with a `stop_details` category, as a successful HTTP 200 with empty or partial content. Code that reads `content[0]` unconditionally breaks.

**Confirmed as far as it goes.** The behaviour is real: `stop_details` is populated only when `stop_reason == "refusal"` (fields `type`, `category` — an open set including `"cyber"`, `"bio"`, `"reasoning_extraction"`, `"frontier_llm"` — and `explanation`), and is `null` for every other stop reason, so the guard is required before reading it (`claude-api` skill, *Other API Surfaces* → Stop details; `python/claude-api/README.md` → Stop Reasons).

**The gap.** `refusal` is a Messages-API stop reason, not an Opus-5 feature. Per AD-15, briefings, drafts and inquiry synthesis route to `claude-sonnet-5` — and those are the flows AD-17 and AD-31 care most about. A reader implementing the router from this prose will guard the `coaching`/`research` path and leave the `briefing_synthesis` path reading `content[0]` unconditionally, which is precisely the failure mode the paragraph exists to prevent. The document's own example of the consequence — "a decline … surfacing as a failed briefing" — is a *Sonnet 5* path, so the paragraph contradicts its own scoping in its last clause.

**Recommendation.** Restate as a property of the frontier adapter, not of one model: *"Any frontier call may return `stop_reason: "refusal"` … The router checks `stop_reason` before reading content on every task class, not only the Opus-routed ones."*

**One under-specification while you are there.** "should opt into server-side `fallbacks`" names no beta header, so it is not actionable. Current forms: `fallbacks: "default"` with beta `server-side-fallback-2026-07-01` (routes by refusal category, no model list to maintain), or the older array form `fallbacks: [{"model": …}]` with beta `server-side-fallback-2026-06-01`. Pairing either header with the other form returns 400. The parameter is rejected on the Batches API. (Source: `claude-api` skill, Fable-5/Opus-5 refusal sections.)

---

## F4 — INCOMPLETE (MEDIUM): "thinking on by default" scoped to Opus 5

**Document claims:**

> Thinking is **on by default** on Opus 5, and `max_tokens` caps thinking plus response text together — a route sized tightly around its answer will truncate.

**Both halves CONFIRMED**, live docs (`https://platform.claude.com/docs/en/build-with-claude/thinking`, retrieved 2026-08-19):

- *"On Claude Opus 5, Claude Sonnet 5, Claude Fable 5, Claude Mythos 5, and Claude Mythos Preview, thinking is already on: no configuration needed."*
- *"the tokens Claude spends reasoning are billed as output tokens, even when the thinking text isn't returned to you, and they count toward `max_tokens` alongside the response text."*

**The gap is the same as F3:** thinking is on by default on **Sonnet 5 too**, so the `max_tokens` truncation hazard applies to the briefing, draft and inquiry routes as well — and those are the high-volume ones. Sizing `max_tokens` "tightly around its answer" on the Sonnet path will truncate for exactly the reason the document gives, but the document's wording exempts that path.

**Two accurate details worth adding**, both confirmed on the same page:

- `display` defaults to `"omitted"` on Opus 5 *and* Sonnet 5 — thinking blocks come back with an empty `thinking` field. A router that logs thinking text for the disclosure ledger (AD-31/AD-38) will silently log nothing unless it sets `thinking: {"type": "adaptive", "display": "summarized"}`. Omitting reduces latency, not cost — the tokens are billed either way, which matters for AD-17's accounting.
- Thinking configuration and the resolved `effort` value are rendered into the prompt, so **changing `effort` between requests invalidates the prompt cache**. Given that the document's own cache-minimum argument (below) depends on repeated persona/rules prefixes hitting cache, a router that varies `effort` per call defeats it. Pick an effort per task class and hold it.

---

## F5 — NOT A PIN (MEDIUM): four rows pinned as "latest"

`anthropic` = "latest", `uv` = "latest", `Ollama` = "latest", `Ollama Python client` = "pin at build time".

Each package exists and is healthy (see Confirmed table). But "latest" is not a decision, and the Stack preamble already concedes the principle — *"a row marked *Phase 1* is a decision the build makes"* — without applying it here. Two of these deserve real pins:

- **`anthropic`** carries `client.beta.messages.tool_runner`, which AD-16 makes load-bearing for the entire execution firewall, and which the document itself flags as a standing beta-API risk in *Open Risks*. Floating that dependency on `latest` means a routine `uv tool upgrade` can move a beta surface underneath the one mechanism AD-1 relies on to keep the model away from a shell. A beta dependency identified as a risk and then left unpinned is the risk register and the stack disagreeing with each other.
- **`Ollama`** (server) is where AD-19's residency constraints live (`OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`, `keep_alive`). Their defaults have changed historically; an unpinned server can shift the thrashing behaviour AD-19 exists to bound.

**Current values for pinning** (all verified 2026-08-19): `anthropic==0.124.0` (PyPI, published 2026-08-19T16:51:33), `uv 0.12.5` (PyPI + GitHub release 2026-08-14), `Ollama v0.32.14` (GitHub release 2026-08-15), `ollama==0.6.2` (PyPI, 2026-04-29).

---

## F6 — RISK UNDERSTATED (MEDIUM): sqlite-vec maintenance velocity

The pin itself is correct. `sqlite-vec==0.1.9` is the current stable release (PyPI JSON API: `info.version` = `0.1.9`, published 2026-03-31), it ships a `macosx_11_0_arm64` wheel, and the pre-1.0 caution is the project's own — its README states verbatim: *"`sqlite-vec` is a pre-v1, so expect breaking changes!"* The document's `==0.1.9` exact pin and its "a minor bump is a reindex event" prerequisite are both well-judged.

**What is not stated.** Repository activity has slowed materially: last commit 2026-05-18 (GitHub commits API), and the 0.1.10 line has been in alpha since 2026-04-01 with `v0.1.10-alpha.4` (2026-05-18) as the most recent tag — three months with no stable release and no commits. The project is effectively single-maintainer.

This matters because `sqlite-vec` sits under Tier 3 (AD-3) and under the `vector_index/` that FR-37's retrieval budget (AD-22, 50–150 ms) depends on. Tier 3 is rebuildable, which caps the *data* consequence at zero — that is a genuine architectural mitigation and the document earns it. But it does not cap the *dependency* consequence: if `sqlite-vec` goes unmaintained against a future SQLite or macOS change, the daemon loses vector search entirely, and no rebuild helps.

**Recommendation.** Add to *Open Risks* alongside the existing `tool_runner` entry: single-maintainer pre-1.0 extension, ~3 months idle as of 2026-08-19, load-bearing for retrieval. Note the mitigation that already exists — AD-3 Tier 3 is disposable and the embedding write path is behind `StoragePort`, so substituting another vector backend is an adapter change, not a rewrite. Say that explicitly; right now the reader has to derive it.

---

## F7 — STALE (LOW): uvicorn 0.52.3

PyPI JSON API for `uvicorn`, retrieved 2026-08-19: `info.version` = **0.52.4**, published **2026-08-19T06:27:40Z**. The pinned 0.52.3 was published 2026-08-13T16:50:01Z.

One patch behind, published the same day the row claims verification — plausibly a genuine race rather than an error, and harmless in itself. Recorded because the Stack preamble's blanket assurance (F2) implies otherwise, and because it is the concrete evidence that "verified today" decays within the day.

---

## F8 — IMPRECISE (LOW): the Telegram manual-lifecycle sequence

**Document claims:**

> The bridge uses the manual lifecycle — `initialize()` → `start()` → `start_polling()`, with matching shutdown

**The underlying prerequisite is CONFIRMED, with source evidence.** From `python-telegram-bot` v22.8 source (`telegram/ext/_application.py`, tag `v22.8`): `run_polling()` delegates to a private `__run()` which calls `asyncio.get_event_loop()` / `asyncio.new_event_loop()` and then drives everything through `loop.run_until_complete(...)`, with `close_loop: bool = True` as a default parameter — so it acquires, blocks, and then closes the loop. It is genuinely incompatible with AD-19's single shared loop, and prohibiting it is correct. `run_webhook()` (line 853) has the same shape, so the AD-2 prohibition is consistent.

**The imprecision.** `start_polling` is not a method on `Application`. Per the same source's `run_polling` docstring, the documented order is `initialize` → `post_init` → **`telegram.ext.Updater.start_polling`** → `start` → … → `Updater.stop` → `stop` → `post_stop` → `shutdown` → `post_shutdown`. Two corrections follow:

1. The call is `application.updater.start_polling()`.
2. The document's ordering is inverted relative to the library's: PTB starts the updater's polling **before** `Application.start()`, and on shutdown stops the updater **first**. A builder following the document's `initialize() → start() → start_polling()` order is writing an unvalidated sequence against a library that documents the opposite.

Small, but this is a prerequisite the document itself says "fails in Phase 1 if ignored" — worth being exactly right.

---

## F9 — REDUNDANT (LOW): `OLLAMA_NUM_PARALLEL=1`

The Ollama residency prerequisite is otherwise **CONFIRMED** against the official FAQ (`ollama/ollama`, `docs/faq.mdx`) and API docs (`docs/api.md`):

- `OLLAMA_MAX_LOADED_MODELS` — *"The maximum number of models that can be loaded concurrently … The default is 3 \* the number of GPUs or 3 for CPU inference."* Setting it to 1 is a **real and necessary** change from the default. The document's core insight — that a client-side semaphore does not unload a model from Ollama's own process — is correct and well made.
- `keep_alive: 0` — *"If an empty prompt is provided and the `keep_alive` parameter is set to `0`, a model will be unloaded from memory"*, against a default of `5m`. Also a real change.
- `OLLAMA_NUM_PARALLEL` — *"The maximum number of parallel requests each model will process at the same time, **default 1**."* Setting it to 1 changes nothing on current versions.

Keep it as defensive pinning against a default drift (that is a defensible reason, given F5's unpinned Ollama), but say so, so a builder does not spend Phase 1 debugging why setting it had no measurable effect.

---

## F10 — PARTIALLY CONFIRMED (LOW): whisper.cpp Metal default

The **Core ML half is fully CONFIRMED** against the whisper.cpp README: Core ML requires `coremltools` plus `ane_transformers` and `openai-whisper`, Python 3.11 is the recommended toolchain, a `.mlmodelc` is generated via `./models/generate-coreml-model.sh`, the build needs `-DWHISPER_COREML=1`, and — verbatim — *"The first run on a device is slow, since the ANE service compiles the Core ML model to some device-specific format."* Every element of the document's prerequisite is accurate, and deferring Core ML is well-reasoned.

**The Metal claim is weaker than stated.** The README says *"on Apple Silicon, the inference runs fully on the GPU via Metal"* — which supports "Metal alone is sufficient" but does not literally establish the document's "on by default" wording. Not contradicted; just asserted with slightly more confidence than the retrieved source carries. Either soften to match the README's phrasing or cite the build flag that establishes the default.

`small.en` remains available (listed in the README's model set). `v1.9.x` is current: GitHub Releases API gives **v1.9.2 (2026-08-04)**, preceded by v1.9.1 (2026-06-19) and v1.9.0 (2026-06-17) — an actively released project.

---

## F11 — CONFIRMED but conservative (INFO): Python 3.13 / 3.14

Per `endoflife.date/api/python.json` (2026-08-19): **3.14** — released 2025-10-07, latest 3.14.7, EOL 2030-10-31; **3.13** — released 2024-10-07, latest 3.13.15, EOL 2029-10-31. Both are supported; 3.13 is a sound, safe choice and nothing about it is stale.

Worth noting only that the 3.14 upgrade path is already clear today, which the "(3.14 is the upgrade path)" phrasing understates. Every constrained dependency in the table already supports it: `sqlcipher3` 0.6.2 publishes `cp314` **and** `cp314t` (free-threaded) wheels including `macosx_11_0_arm64`; `sqlite-vec` wheels are `py3-none-*` (ABI-independent); `python-telegram-bot` 22.8 requires `>=3.10` and carries explicit `python_version >= "3.14"` dependency branches; `mcp` 2.0.0 likewise branches on 3.14. The blocker is not the ecosystem.

---

## F12 — UNVERIFIABLE by construction (INFO): embedding model

> Embedding model + dimension | Phase 1 | Must be pinned before the first index is written; a change is a reindex event

No candidate is named, so there is nothing to check for currency. The *constraint* is correct and important — dimension is load-bearing for `vector_index/`, and AD-15 makes embedding local-only-always.

Flagged only because this is the one Stack row with no verified content at all, while the neighbouring "Local parsing model" row does name verified candidates with sizes. The asymmetry is worth closing: naming even two candidate embedding models with their dimensions would let Phase 1 confirm they run concurrently under the 16 GB budget alongside whisper.cpp — which is the *same* open question the document already tracks in *Open Risks* for the parsing model, and which silently has a third resident model in it.

---

## Confirmed — full evidence table

Everything below was independently verified; no action needed.

### Stack rows

| Row | Document | Verified | Source |
|---|---|---|---|
| Python | 3.13, 3.14 upgrade path | 3.13 EOL 2029-10-31; 3.14 GA 2025-10-07, EOL 2030-10-31 | `endoflife.date/api/python.json` |
| uv | latest | 0.12.5, 2026-08-14 | PyPI JSON; GitHub `astral-sh/uv` releases |
| `uv tool install --managed-python` | install command | Flag is real: `--managed-python  Require use of uv-managed Python versions [env: UV_MANAGED_PYTHON=]` | `uv tool install --help`, uv 0.12.2 local |
| anthropic SDK | latest, `[mcp]` extra | 0.124.0 (2026-08-19); `mcp` is a declared extra → `mcp<3,>=1.0` | PyPI JSON (`provides_extra`, `requires_dist`) |
| — "pulls a second web stack incl. its own HTTP client" | budget for it | **Confirmed and understated.** `mcp` 2.0.0 requires `httpx2>=2.5.0` — a *different* HTTP client package from the `httpx` the anthropic SDK itself uses — plus `starlette`, `uvicorn>=0.31.1`, `sse-starlette`, `python-multipart`, `pyjwt[crypto]`, `opentelemetry-api`, `jsonschema` | PyPI JSON for `mcp` 2.0.0 |
| python-telegram-bot | 22.8, no `[job-queue]` | 22.8 is current (PyPI + GitHub `v22.8`, 2026-06-12). `job-queue` extra → `apscheduler<3.12.0,>=3.10.4` — literally a second scheduler | PyPI JSON `requires_dist`; GitHub releases |
| Ollama | latest | Server v0.32.14, 2026-08-15 | GitHub `ollama/ollama` releases |
| Ollama Python client | `ollama` | 0.6.2, 2026-04-29 | PyPI JSON |
| Local parsing model — `llama3.1:8b` | 4.9 GB | **4.9 GB exactly**; `8b-instruct-q4_K_M` also 4.9 GB | ollama.com/library/llama3.1/tags |
| Local parsing model — `qwen3:8b` | candidate | Exists at 5.2 GB; `qwen3:8b-q4_K_M` available; sizes 0.6b–235b | ollama.com/library/qwen3/tags |
| **Not** `llama3.3` — "ships 70B only" | rejection rationale | **Confirmed exactly.** All 14 tags are 70B. Default `llama3.3:latest` = 43 GB | ollama.com/library/llama3.3/tags |
| "smallest `llama3.3` build is 26 GB" (Deferred §) | rejection rationale | **Confirmed exactly.** `70b-instruct-q2_K` = 26 GB, the smallest listed | ollama.com/library/llama3.3/tags |
| whisper.cpp | v1.9.x, `small.en`, Metal | v1.9.2 (2026-08-04) current; `small.en` available | GitHub `ggml-org/whisper.cpp` releases + README |
| `sqlcipher3` | 0.6.2 | 0.6.2 current (2026-01-07); ships `macosx_11_0_arm64` wheels for cp39–cp314 → **works on Apple Silicon** | PyPI JSON, per-file wheel tags |
| **Not** `sqlcipher3-binary` — "Linux-x86_64 wheels only" | rejection rationale | **Confirmed exactly.** Every wheel in 0.6.0 (and 0.5.x) is `manylinux2014_x86_64` / `manylinux_2_17_x86_64`. No macOS, no aarch64. Would fail on Apple Silicon | PyPI JSON, per-file wheel tags |
| `sqlite-vec` | `==0.1.9` exact, pre-1.0 | 0.1.9 current stable (2026-03-31); `macosx_11_0_arm64` wheel present; README: *"pre-v1, so expect breaking changes!"* | PyPI JSON; project README |
| FastAPI | 0.141.1 | Current latest, 2026-07-29 | PyPI JSON |
| keyring | 25.7.0, macOS 11+, universal2 | 25.7.0 current (2025-11-16). README verbatim: *"macOS keychain supports macOS 11 (Big Sur) and later requires Python 3.8.7 or later with the 'universal2' binary"* | PyPI JSON; `jaraco/keyring` README |
| APScheduler (fallback) | 3.11.3 | 3.11.3 current stable (2026-06-28). 4.x still alpha (`4.0.0a6`) — 3.x is the right fallback | PyPI JSON |
| Claude models | `claude-opus-5`, `claude-sonnet-5` | Both real and current; Opus 5 $5/$25, Sonnet 5 $2/$10 | Anthropic pricing docs |

### Integration prerequisites

| Prerequisite | Verdict | Evidence |
|---|---|---|
| `sqlite-vec` cannot load into stock macOS Python; `enable_load_extension` is *absent*, not disabled; a uv-managed interpreter has it | **CONFIRMED — empirically, on this machine** | `/usr/bin/python3` (system) → `hasattr(conn, "enable_load_extension")` = **False**; `/usr/local/bin/python3` (python.org build) → **False**; uv-managed `cpython-3.13` at `~/.local/share/uv/python/cpython-3.13-macos-*/bin/python3.13` → **True**. The `pm-ai doctor` assertion the document specifies is exactly the right probe. |
| whisper.cpp Core ML is build-time: `coremltools`, separate Python 3.11 toolchain, generated `.mlmodelc`, slow first-run ANE compile | **CONFIRMED** | whisper.cpp README — see F10 |
| Ollama manages its own residency; client-side pool bound does not unload a model | **CONFIRMED** | `docs/faq.mdx` — see F9 |
| PTB `run_polling()` seizes the event loop; `run_webhook()` likewise prohibited | **CONFIRMED from source** | `_application.py` @ `v22.8` — see F8 |
| A `sqlite-vec` minor bump is a reindex event | **CONFIRMED in spirit** | Project README's pre-v1 breaking-changes warning supports it. The narrower claim that *the `vec0` on-disk format specifically* is unfrozen was not found stated verbatim upstream — reasonable inference from the pre-v1 posture, but it is an inference. |

### Anthropic API notes

| Claim | Verdict | Evidence |
|---|---|---|
| Thinking on by default on Opus 5 | **CONFIRMED** (also Sonnet 5 — F4) | Thinking docs: *"On Claude Opus 5, Claude Sonnet 5, Claude Fable 5 … thinking is already on: no configuration needed."* |
| `max_tokens` caps thinking + response text together | **CONFIRMED** | Thinking docs: *"they count toward `max_tokens` alongside the response text."* Also: *"Thinking tokens count toward the `max_tokens` limit for the turn"* (extended-thinking docs). |
| `temperature` / `top_p` / `top_k` rejected (400) | **CONFIRMED** | Thinking docs: *"On Claude Fable 5, Claude Mythos 5, … Claude Opus 5, Claude Opus 4.8, Claude Opus 4.7, and Claude Sonnet 5, non-default `temperature`, `top_p`, or `top_k` values return a 400 error on every request, regardless of whether thinking is used."* |
| Assistant prefill rejected | **CONFIRMED** | Thinking docs: *"You can't pre-fill the assistant response while thinking is on."* Corroborated by `claude-api` skill: prefill returns 400 on Opus 5 / Sonnet 5 / the 4.6–4.8 family. |
| Use `output_config.format` for structured output | **CONFIRMED** | `claude-api` skill: `output_config: {format: {...}}`; the older `output_format` parameter is deprecated. |
| `output_config.effort` controls depth | **CONFIRMED** | Extended-thinking migration guidance: *"control reasoning depth with `output_config: {effort: ...}` instead of a token budget."* Levels `low`–`max` on Opus 5; default `high`. |
| `stop_reason: "refusal"` as HTTP 200 with `stop_details` category | **CONFIRMED** (scope too narrow — F3) | `claude-api` skill, Stop details + Refusal Fallbacks. |
| **Prompt-cache minimums: 512 on Opus 5, 1024 on Sonnet 5** | **CONFIRMED EXACTLY** | `claude-api` skill `shared/prompt-caching.md` minimum-prefix table: Claude Opus 5 / Fable 5 / Mythos 5 = **512**; Opus 4.8, **Claude Sonnet 5**, Sonnet 4.6/4.5, Opus 4.1/4, Sonnet 4 = **1024**. The document's operational conclusion — briefings run on Sonnet 5, so the persona/rules prefix must clear the *higher* 1024 floor — is correct, and the non-monotonic minimum across generations is a genuinely easy thing to get wrong. Below the floor there is no error, just `cache_creation_input_tokens: 0`. |
| `client.beta.messages.tool_runner` is a beta SDK surface | **CONFIRMED** | `claude-api` skill: Tool Runner is reached via `client.beta.messages.tool_runner`, listed throughout as a beta helper. The AD-16 rationale also holds: the Tool Runner loops only over tools you define and ships **no** built-in Bash/Read/Write/Edit tools, whereas the Claude Agent SDK (`claude-agent-sdk`) ships exactly those — so prohibiting the Agent SDK in this layer while adopting the Tool Runner is a correct and non-obvious distinction, correctly drawn. |

---

## Notes for the author

**What this document does well, and should keep doing.** The rejection rationales are the strongest part of these sections. "Not `sqlcipher3-binary`, which publishes Linux-x86_64 wheels only" and "Not `llama3.3` — it ships 70B only" are both exactly right down to the wheel tags and the 26 GB floor, and both encode *why* a plausible-looking choice fails on this specific target. That is the pattern that survives review. Rows carrying a bare version number and no constraint (`FastAPI 0.141.1`, `uvicorn 0.52.3`) are the ones that quietly decay — and F7 is the proof.

**The one systematic gap.** Three separate findings (F3, F4, and the cache-minimum note the document itself got right) trace to the same root: the *Anthropic API notes* section is written as though the frontier adapter talks to Opus 5. Per AD-15 it talks to two models, and Sonnet 5 carries the higher-volume paths. The section's one correct cross-model observation — the 1024-token cache floor on Sonnet 5 — shows the author knows this; the surrounding prose does not follow through. Retitling the section's claims as adapter properties rather than Opus-5 properties would close F3 and F4 together.

**On method.** Everything that lives in a package registry was verified correctly and thoroughly here, including several details a casual check would miss. The single failure (F1) is in the one class of claim that no registry covers — a vendor's forward-looking pricing commitment, which the vendor then reversed. If this document is re-verified again, treat vendor pricing, beta-surface status, and API behaviour as a separate pass with its own dated citations, rather than as rows in a table whose header promises registry verification.

---

## Sources

- PyPI JSON API — `https://pypi.org/pypi/{anthropic,python-telegram-bot,ollama,sqlcipher3,sqlcipher3-binary,sqlite-vec,fastapi,uvicorn,keyring,apscheduler,uv,mcp}/json` (retrieved 2026-08-19)
- GitHub REST API — releases/commits for `ggml-org/whisper.cpp`, `ollama/ollama`, `astral-sh/uv`, `python-telegram-bot/python-telegram-bot`, `asg017/sqlite-vec` (retrieved 2026-08-19)
- `python-telegram-bot` source at tag `v22.8` — `telegram/ext/_application.py`
- `ollama/ollama` — `docs/faq.mdx`, `docs/api.md` (main branch)
- `github.com/ggml-org/whisper.cpp` — README
- `github.com/asg017/sqlite-vec` — README
- `github.com/jaraco/keyring` — README
- `ollama.com/library/{llama3.1,qwen3,llama3.3}/tags`
- `endoflife.date/api/python.json`
- `https://platform.claude.com/docs/en/about-claude/pricing` (retrieved 2026-08-19)
- `https://platform.claude.com/docs/en/build-with-claude/thinking` (retrieved 2026-08-19)
- `https://platform.claude.com/docs/en/build-with-claude/extended-thinking` (retrieved 2026-08-19)
- `claude-api` skill — `shared/prompt-caching.md` (cache minimums), stop-details and refusal-fallback references, Tool Runner vs Claude Agent SDK disambiguation
- Local empirical tests — `uv tool install --help`; `sqlite3.Connection.enable_load_extension` presence across system CPython, python.org CPython, and uv-managed CPython 3.13
