# Technology-Currency Review — ARCHITECTURE-SPINE.md (pm-ai)

**Reviewed:** `_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md`
**Review date:** 2026-08-18
**Reviewer role:** technology-verification (no prior project context)
**Method:** every claim re-derived from live sources — PyPI JSON API, GitHub releases API, endoflife.date, ollama.com library index, upstream READMEs, and the bundled `claude-api` skill (authoritative for Anthropic API surface). Where a claim was testable, it was **executed**: a uv-managed CPython 3.13.14 venv was built on macOS and the full pinned stack was installed, imported, and exercised.

**Verdict up front:** the stack is mostly real and mostly current, and the single riskiest-looking integration (SQLCipher + sqlite-vec) actually works — verified empirically. But one pinned model does not exist, one pin is three months stale, the macOS-specific claims are asserted rather than tested, and eight integration prerequisites that will bite in Phase 1 are absent from the document.

**Tally:** 25 verified-good · 22 problematic · 7 unverifiable

---

## Section 1 — Stack table version audit

Raw evidence, gathered 2026-08-18 from `https://pypi.org/pypi/<pkg>/json`, `https://api.github.com/repos/<org>/<repo>/releases/latest`, and `https://endoflife.date/api/python.json`:

| Doc row | Doc says | Reality (2026-08-18) | Status |
|---|---|---|---|
| Python | 3.13 (3.14 upgrade path) | 3.13.15 active (EOL 2029-10-31); 3.14.7 released 2025-10-07 (EOL 2030-10-31) | ✅ |
| uv | latest | 0.12.2 present on review host | ✅ |
| anthropic `[mcp]` | latest | 0.122.0 (2026-08-13); `mcp` is a real extra | ✅ |
| python-telegram-bot | 22.8 | 22.8 (2026-06-12) — is latest | ✅ |
| Ollama | latest | v0.32.14 (2026-08-15) | ✅ |
| Local parsing model | **Llama 3.3 8B `Q4_K_M`** | **Llama 3.3 ships 70B only** | ❌ **invented** |
| whisper.cpp | latest, `small.en` | v1.9.2 (2026-08-04) | ✅ version; ⚠️ prerequisites |
| SQLite + SQLCipher via `sqlcipher3` | latest | 0.6.2 (2026-01-07); macOS arm64 wheels cp310–cp314 | ✅ |
| sqlite-vec | latest | 0.1.9 (2026-03-31) | ✅ version; ⚠️ pre-1.0 |
| FastAPI | **0.136.1** | 0.141.1 (2026-07-29) — **5 minor releases behind** | ⚠️ stale |
| uvicorn | 0.52.3 | 0.52.3 (2026-08-13) — is latest | ✅ |
| keyring | 25.7.0 | 25.7.0 (2025-11-16) — is latest | ✅ version; ⚠️ claim |
| Scheduler / APScheduler | evaluated at build time | 3.11.3 (2026-06-28) is current stable | ✅ |
| Claude models | `claude-opus-5`, `claude-sonnet-5` | both real, current | ✅ |

Nothing in the table is **yanked**. FastAPI 0.136.1 exists, is not yanked, and installs.

---

### F-01 — `Llama 3.3 8B Q4_K_M` does not exist

- **location** — Stack table, row "Local parsing model"; also Deferred § "Local-model selection"
- **trigger_condition** — Llama 3.3 was released in a single size, 70B; there is no 8B variant, so the named benchmark seed cannot be pulled.
- **guard_snippet** — The Ollama library tag index for `llama3.3` lists exactly: `70b`, `70b-instruct-fp16`, `70b-instruct-q2_K … q8_0`, `latest`. No 8B tag exists. Source: https://ollama.com/library/llama3.3/tags . 8B-class instruct models that *do* exist and fit a 16 GB unified-memory budget include `llama3.1:8b` (https://ollama.com/library/llama3.1/tags), `qwen3:8b`, `gemma3:12b`. Replace the row with a model that has a real tag, or state the *class* ("an 8B-class instruct model at Q4_K_M, selected in Phase 1") rather than a fictitious pin.
- **potential_consequence** — Phase 1's first `ollama pull llama3.3:8b-instruct-q4_K_M` returns a 404. Worse, the nearest real substitute is **70B**, which will not load in 16 GB at Q4 (~40 GB) — so a builder who "corrects" the tag to `llama3.3:70b` produces a machine that swaps to death, which is precisely the PRD Open Question the spine says AD-19 guards. Every downstream latency figure derived from this seed (AD-22's 50–150 ms retrieval, the ≤60 s synthesis budget) is anchored to a model that was never measured.

### F-02 — FastAPI pin is three months and five minor releases stale

- **location** — Stack table, `FastAPI | 0.136.1`; and the section header "Verified current at 2026-08-18"
- **trigger_condition** — 0.136.1 was published 2026-04-23; 0.141.1 shipped 2026-07-29, with 0.136.3, 0.137.x, 0.138.x, 0.139.x, 0.140.x (13 patch releases) and 0.141.x in between.
- **guard_snippet** — `https://pypi.org/pypi/fastapi/json` → `info.version == "0.141.1"`, uploaded `2026-07-29T17:18:04`. Both 0.136.1 and 0.141.1 declare identical constraints (`requires_python >=3.10`, `starlette>=0.46.0`, `pydantic>=2.9.0`) and both list a `Programming Language :: Python :: 3.14` classifier, so the upgrade is not blocked by anything in this stack. Either move the pin to `0.141.1` or delete the header's currency claim.
- **potential_consequence** — Low functional risk in isolation; the real damage is to the document's credibility contract. The neighbouring uvicorn pin (0.52.3, published 2026-08-13) *is* same-week current, which proves the table was assembled from at least two different research passes. A builder who trusts "Verified current at 2026-08-18" will not re-check any row — including F-01.

### F-03 — sqlite-vec is pinned as if production-stable; it is 0.1.x

- **location** — Stack table, `sqlite-vec | latest`
- **trigger_condition** — `latest` resolves to 0.1.9, a pre-1.0 release whose own project describes the on-disk `vec0` format as not yet frozen; the table gives it no version floor and no stability caveat, while AD-3 makes the vector index a rebuildable derived artifact and AD-22 makes it load-bearing for the 50–150 ms retrieval budget.
- **guard_snippet** — `https://pypi.org/pypi/sqlite-vec/json` → version `0.1.9`, uploaded `2026-03-31`; upstream https://github.com/asg017/sqlite-vec . Pin an exact version (`sqlite-vec==0.1.9`) rather than `latest`, and record in AD-3 that a sqlite-vec minor bump is a *reindex event*, not a transparent dependency upgrade.
- **potential_consequence** — `latest` in a lockfile-less install means a `uv tool upgrade` can silently change the `vec0` virtual-table format. Because AD-3 guarantees `pm-ai reindex` rebuilds losslessly from markdown, the *data* survives — but the daemon fails to open an existing index on restart with no migration path documented, and the reindex is unbudgeted (AD-22 gives no rebuild-time target).

---

## Section 2 — Does each named technology still exist and fit its stated purpose?

All fourteen named technologies exist and are actively maintained as of 2026-08-18. Latest-release dates cluster tightly (Ollama 2026-08-15, uvicorn 2026-08-13, anthropic 2026-08-13, whisper.cpp 2026-08-04, FastAPI 2026-07-29, APScheduler 2026-06-28, PTB 2026-06-12, sqlite-vec 2026-03-31, sqlcipher3 2026-01-07, keyring 2025-11-16) — nothing is abandoned.

Fit-for-purpose is sound with three exceptions, all covered below: `whisper.cpp` has no Python API (F-12), `sqlite-vec` is pre-1.0 (F-03), and the local parsing model does not exist (F-01).

Two purpose-fit gaps in the table itself:

### F-04 — No Ollama Python client is pinned

- **location** — Stack table (`Ollama | latest`); source tree `models/ local/ (Ollama, whisper.cpp)`; AD-15
- **trigger_condition** — Ollama is named as a *server*; the `ModelPort` local adapter must speak to it from Python, but no client library is pinned and no "raw httpx against `127.0.0.1:11434`" decision is recorded.
- **guard_snippet** — The official client is `ollama` on PyPI (https://pypi.org/project/ollama/), which wraps `httpx`. Either pin it, or record the decision to call the REST API directly — noting that the process already carries **two** HTTP client stacks (see F-17), so adding a third wrapper is a real cost.
- **potential_consequence** — Two builders implement the local adapter two ways (one `ollama.AsyncClient`, one raw `httpx`), diverging on timeout, streaming, and `keep_alive` semantics — and `keep_alive` is exactly the knob that governs the 16 GB risk in F-15. AD-15's "no feature instantiates a model client" is enforced by import-linter, but nothing tells the linter which client is the sanctioned one.

### F-05 — No embedding model or vector dimension is pinned

- **location** — Stack table (no embedding row); AD-15 (`embedding` is a local-only task class); AD-3 (`vector_index/` is derived); scopes diagram (`vector_index/ plain`)
- **trigger_condition** — `embedding` is declared a first-class task class and sqlite-vec is the store, but no embedding model, no dimension, and no distance metric are named anywhere in the spine.
- **guard_snippet** — sqlite-vec's `vec0` virtual table requires the dimension in the DDL at creation time — the review's own test ran `create virtual table vt using vec0(embedding float[4])`, where `4` is a hard schema commitment. See https://alexgarcia.xyz/sqlite-vec/python.html . Name the model and dimension in the Stack table (e.g. `nomic-embed-text` / 768, or `mxbai-embed-large` / 1024, both Ollama-servable), and state the distance metric.
- **potential_consequence** — The dimension is baked into the schema on first index build. Swapping the embedding model in Phase 2 is a full `pm-ai reindex` — recoverable under AD-3, but it makes an unbudgeted rebuild a *routine* consequence of a model swap rather than an exceptional one, and it leaves AD-22's retrieval budget un-anchored (retrieval latency is dimension-dependent).

---

## Section 3 — Anthropic API notes

The document's binding paragraph reads:

> *"thinking is on by default on Opus 5 and `max_tokens` caps thinking plus text together; `temperature` / `top_p` / `top_k` are rejected; assistant prefill is rejected — use `output_config.format` for structured output; `output_config.effort` controls depth. Prompt caching pays off here because persona and rules prefixes repeat across briefings (512-token minimum on Opus 5)."*

Checked claim by claim against the bundled `claude-api` skill (`shared/model-migration.md`, `shared/prompt-caching.md`, `shared/models.md`), which is the authoritative in-repo source for the Anthropic surface, and against `client.beta.messages.tool_runner` introspected on an installed `anthropic==0.122.0`.

| Claim | Verdict | Evidence |
|---|---|---|
| Thinking on by default on Opus 5 | ✅ **correct** | Omitting `thinking` runs adaptive on Claude Opus 5 — a change from Opus 4.8/4.7 where omitting it meant no thinking. |
| `max_tokens` caps thinking + text together | ✅ **correct** | Explicitly called out as a truncation risk for routes that never set `thinking`. |
| `temperature`/`top_p`/`top_k` rejected | ✅ **correct** | Removed on Opus 5 / Fable 5 / Opus 4.8 / 4.7 — 400 if sent. |
| Assistant prefill rejected; use `output_config.format` | ✅ **correct** | Last-assistant-turn prefill 400s; `output_config.format` is the documented replacement. |
| `output_config.effort` controls depth | ✅ **correct** | `low`/`medium`/`high`/`xhigh`/`max`, nested inside `output_config`, default `high`. |
| 512-token prompt-cache minimum on Opus 5 | ✅ **correct but incomplete** | See F-06. |
| `client.beta.messages.tool_runner` (AD-16) | ✅ **exists** | See F-09 for the beta caveat. |

Four things that are **missing** and materially affect ADs the spine treats as binding:

### F-06 — The 512-token cache minimum is quoted for the wrong model

- **location** — Stack § Anthropic API notes, final sentence
- **trigger_condition** — The stated cache justification is "persona and rules prefixes repeat across **briefings**", but AD-15 routes `briefing_synthesis` to **`claude-sonnet-5`**, whose minimum cacheable prefix is 1024 tokens, not 512. Only `coaching` and `research` run on Opus 5.
- **guard_snippet** — Minimum cacheable prefix by model: **Claude Opus 5 / Fable 5 / Mythos 5 = 512 tokens**; **Opus 4.8, Claude Sonnet 5, Sonnet 4.6, Sonnet 4.5 = 1024 tokens**; Opus 4.7 = 2048; Opus 4.6 / Opus 4.5 / Haiku 4.5 = 4096. The minimum is *not* monotonic across generations. Source: bundled `claude-api` skill → `shared/prompt-caching.md` § API reference; live docs at https://platform.claude.com/docs/en/build-with-claude/prompt-caching . Restate as: "512-token minimum on Opus 5 (coaching, research); **1024 on Sonnet 5** (briefings, drafts, inquiry)."
- **potential_consequence** — A sub-1024-token persona/rules prefix on the briefing path **silently does not cache** — no error, `cache_creation_input_tokens: 0`. AD-17 logs token counts and surfaces a monthly total, so the cost model quietly runs ~10× higher than projected on the highest-frequency frontier path, and the only symptom is a number nobody has a baseline for.

### F-07 — Opus 5 can return `stop_reason: "refusal"`; nothing in the spine handles it

- **location** — AD-15 (routing to `claude-opus-5`), AD-16 (frontier adapter), Consistency Conventions § Errors
- **trigger_condition** — Claude Opus 5 ships elevated cybersecurity safeguards whose classifiers can decline a request. The result is a **successful HTTP 200** with `stop_reason: "refusal"` and an empty or partial `content` array — not an exception. The spine's error convention ("adapters translate external failures into domain errors at the boundary; no external SDK exception escapes an adapter") only catches *exceptions*, and a refusal raises none.
- **guard_snippet** — Check `stop_reason` before reading `content`; `stop_details` is informational and can be `null` even on a refusal, so branch on `stop_reason`, never on `stop_details`. Opt into server-side fallbacks by default: `betas=["server-side-fallback-2026-07-01"]` with `fallbacks="default"` (category-routed; cyber-category refusals route to Claude Opus 4.8). Source: bundled `claude-api` skill → `shared/model-migration.md` § Migrating to Claude Opus 5 → New API features, and `python/claude-api/README.md` § Stop Reasons.
- **potential_consequence** — A refused coaching or research call returns `content == []`. Any code doing `response.content[0].text` raises `IndexError` inside the frontier adapter — which AD-16's contract says must not escape — or, worse, code that iterates safely writes an **empty** coaching entry into `coaching_1on1_history.md`. Under AD-3 that markdown is source of truth and under AD-5 it is append-only, so a silent empty entry is unfixable in place.

### F-08 — The $20/month target is anchored to introductory pricing that expires in 13 days

- **location** — AD-17 ("The $20 figure is a monitored target for understanding real efficiency")
- **trigger_condition** — Claude Sonnet 5 — which AD-15 routes `briefing_synthesis`, `draft_generation`, and `inquiry_synthesis` to, i.e. the high-frequency paths — is at **introductory** pricing of $2.00 / $10.00 per MTok **through 2026-08-31**, reverting to $3.00 / $15.00. This document is dated 2026-08-18.
- **guard_snippet** — Current rates: `claude-opus-5` $5.00 in / $25.00 out per MTok; `claude-sonnet-5` $3.00 / $15.00, with **$2.00 / $10.00 introductory through 2026-08-31**. Source: bundled `claude-api` skill → SKILL.md § Current Models (cached 2026-06-24); live rates at https://platform.claude.com/docs/en/pricing . Record which pricing the $20 figure assumes, and note the +50 % step change on 2026-09-01.
- **potential_consequence** — AD-17 makes cost accounting *observability*, and the whole point of the $20 target is "understanding real efficiency". Phase 1 spend measured in August is 33 % below the September steady state on every Sonnet path. The first month's data — the baseline everything else is judged against — is wrong by construction, and the threshold-breach warning fires without explanation right after launch.

### F-09 — AD-16 pins the architecture to a beta SDK surface, unannotated

- **location** — AD-16 ("Frontier calls use the Anthropic SDK Tool Runner (`client.beta.messages.tool_runner`)")
- **trigger_condition** — The method is real — verified by introspection on `anthropic==0.122.0`, `hasattr(client.beta.messages, "tool_runner") is True` — but it lives under `client.beta.*` and is documented as a beta helper. Separately, the Python runner **does not auto-resume `pause_turn`**: a long server-tool turn ends the loop and returns as the final message with no error, no warning, and a silently truncated answer.
- **guard_snippet** — "**Beta:** The tool runner is in beta in the Python SDK." And: "the runner does not auto-resume `pause_turn` (as of `anthropic` 0.116.0) … a paused turn ends the loop and is returned as the final message — no error, no warning, just a silently truncated answer … the Python runner cannot be resumed mid-loop: it exits unconditionally when no client tool ran, and `runner.append_messages(...)` does not prevent the exit." Mitigation is to mirror the conversation history while iterating and restart the runner with the paused turn appended (capped restarts). Source: bundled `claude-api` skill → `python/claude-api/tool-use.md` § Tool Runner. Add to AD-16: the runner is beta, and the adapter must check `stop_reason == "pause_turn"` and restart.
- **potential_consequence** — A research or deep-inquiry flow (FR-23/FR-24) that pauses mid-turn returns a truncated answer that looks complete. AD-16's whole purpose is to make the frontier path auditable; a truncation with no signal defeats that, and AD-24's `event_log.md` records a successful skill invocation for work that never finished.

### F-10 — Thinking tokens are billed but invisible by default

- **location** — AD-17 ("Every frontier call logs token counts and a cost estimate")
- **trigger_condition** — `thinking.display` defaults to `"omitted"` on Opus 5 and Sonnet 5. Thinking still happens and is still billed identically; the `thinking` blocks arrive with empty text. The document does not mention `display` at all.
- **guard_snippet** — "`display: 'summarized'` returns a readable summary of the reasoning; `'omitted'` (the default on all six) streams `thinking` blocks with empty text … `display` controls visibility only — thinking happens and is billed the same under every setting." Cost accounting must read `usage.output_tokens` (which includes thinking), not the length of rendered text. Source: bundled `claude-api` skill → SKILL.md § Thinking & Effort.
- **potential_consequence** — If AD-17's estimator derives cost from visible output, every frontier call under-reports by the thinking share — which on `effort: high` (the default) is often the majority of output tokens. The monthly total surfaced in briefings is systematically low, and the "warn only at threshold breach" rule never fires.

---

## Section 4 — Integration risks between named components

This is where the document is thinnest. The pairings below are all between components the spine explicitly names, and none of the interactions are mentioned.

### ✅ First, the good news — verified by execution, not inference

Three integrations were run end-to-end on macOS with a uv-managed CPython 3.13.14 (`uv python install --managed-python 3.13`):

**(a) SQLCipher + sqlite-vec interoperate.** `sqlcipher3==0.6.2` is built with `SQLITE_ENABLE_LOAD_EXTENSION=1` (confirmed in https://github.com/coleifer/sqlcipher3/blob/master/setup.py, line 123), so sqlite-vec loads into an *encrypted* connection:

```
sqlcipher3 3.51.1
has enable_load_extension: True
vec_version via SQLCipher conn: v0.1.9
cipher header bytes: b'\x93\xfa\xa5s\xc3\x8em0\xf2\xf6\xfa,j~[\x0b'
```

The header is random rather than `SQLite format 3\0`, confirming genuine SQLCipher encryption, and a `vec0` virtual table was created and written inside it. AD-6's split (`event_telemetry.db` encrypted, vectors alongside) is technically achievable.

**(b) The entire pinned stack resolves and imports together with no conflict.** `anthropic[mcp]` + `python-telegram-bot[job-queue]==22.8` + `fastapi==0.136.1` + `uvicorn==0.52.3` + `keyring==25.7.0` + `sqlcipher3` + `sqlite-vec` installs clean and imports clean:

```
fastapi 0.136.1 | starlette 1.6.0
telegram 22.8   | httpx 0.28.1
keyring backend: keyring.backends.macOS.Keyring (priority: 5)
```

**(c) The Python 3.14 upgrade path holds.** The same set resolves on a managed CPython 3.14, and `sqlcipher3` 0.6.2 publishes `cp314` **and** `cp314t` (free-threaded) macOS arm64 wheels.

Now the eight risks the document does not mention.

### F-11 — `sqlite-vec` cannot load into a stock macOS Python's `sqlite3`

- **location** — Stack table (`sqlite-vec`, `uv`); AD-6 (`vector_index/` is unencrypted, outside the SQLCipher DB); AD-3 (index is derived); Deployment § "isolated install via `uv tool install`"
- **trigger_condition** — AD-6 places `vector_index/` *outside* the encrypted `event_telemetry.db`, which invites opening it with the **standard library** `sqlite3`. On macOS, python.org and system CPython builds ship `_sqlite3` compiled without loadable-extension support — the method is not merely disabled, it is **absent**.
- **guard_snippet** — Reproduced on the review host (macOS, python.org CPython 3.12.6):
  ```
  py 3.12.6 (v3.12.6:a4a2d2b0d85 …)
  sqlite lib 3.45.3
  has enable_load_extension: False
  enable_load_extension FAILED: 'sqlite3.Connection' object has no attribute 'enable_load_extension'
  ```
  The **uv-managed** interpreter is fine — `cpython-3.13.14` reports `sqlite lib 3.53.1`, `has enable_load_extension: True`, `enable_load_extension OK`. Upstream context: "The sqlite3 module is not built with loadable extension support by default, because some platforms (notably macOS) have SQLite libraries compiled without this feature" — https://docs.python.org/3/library/sqlite3.html ; see also https://til.simonwillison.net/sqlite/sqlite-extensions-python-macos . **Guard:** pin `requires-python` and install with `uv tool install --managed-python`, or open the vector DB through `sqlcipher3` with no `PRAGMA key` (which works, per (a) above, and removes the interpreter dependency entirely). Add a `pm-ai doctor` probe that asserts `hasattr(conn, "enable_load_extension")` at startup.
- **potential_consequence** — Install succeeds, the daemon starts, and the first embedding write throws `AttributeError` deep in the storage service — the single writer under AD-5, so the failure takes down all persistence, not just vectors. It reproduces only on machines where uv resolved a *system* interpreter, so it will pass on the developer's machine and fail on the first clean install.

### F-12 — `sqlcipher3-binary` is Linux-x86_64 only

- **location** — Stack table, `SQLite (WAL) + SQLCipher via sqlcipher3`
- **trigger_condition** — The document correctly names `sqlcipher3`, but the upstream README prominently recommends `sqlcipher3-binary` as the "completely self-contained" option — and that package publishes **no macOS wheels at all**.
- **guard_snippet** — Every file in `sqlcipher3-binary==0.6.0` is `manylinux2014_x86_64` (cp38–cp314); there is no macOS or arm64 artifact. By contrast `sqlcipher3==0.6.2` publishes `macosx_11_0_arm64` wheels for cp310 through cp314. Verified via `https://pypi.org/pypi/sqlcipher3-binary/json` and `https://pypi.org/pypi/sqlcipher3/json`. Upstream README: "A binary package (wheel) is available for **linux** with a completely self-contained `sqlcipher3`" — https://github.com/coleifer/sqlcipher3 . **Guard:** annotate the row `sqlcipher3` (**not** `sqlcipher3-binary` — Linux-only), and pin the exact version so a wheel is always used rather than a source build (which needs OpenSSL via conan and a full toolchain).
- **potential_consequence** — A builder hitting any SQLCipher friction reaches for the "self-contained" package the upstream README advertises, and gets `No matching distribution found` on Apple Silicon — or, on a Rosetta shell, a Linux-wheel resolution failure that reads as a Python-version problem. Time lost to a dead end the doc could have closed in five words.

### F-13 — whisper.cpp Core ML is a build-time feature with a separate Python toolchain

- **location** — Stack table, `whisper.cpp (Metal + Core ML) | latest, small.en`; AD-19; AD-23
- **trigger_condition** — The row reads as though Metal and Core ML are both properties of "install whisper.cpp". Metal is on by default on Apple Silicon; **Core ML is not**. It requires a specific build flag, a separately generated encoder model, and a Python environment the project otherwise has no reason to own.
- **guard_snippet** — From the upstream README (https://github.com/ggml-org/whisper.cpp#core-ml-support): install `ane_transformers`, `openai-whisper`, and `coremltools`; confirm Xcode is installed and run `xcode-select --install`; **"Python 3.11 is recommended"**; generate the encoder with `./models/generate-coreml-model.sh small.en` (producing `models/ggml-small.en-encoder.mlmodelc`); then build with `cmake -B build -DWHISPER_COREML=1`. Also: **"The first run on a device is slow, since the ANE service compiles the Core ML model to some device-specific format."** Note `openai-whisper` pulls PyTorch, and the recommended Python 3.11 conflicts with the project's pinned 3.13. Homebrew's `whisper-cpp` formula is built **without** Core ML. **Guard:** either drop Core ML from Phase 1 (Metal alone is the default and is sufficient), or add the toolchain, the generated-model artifact, and the one-time ANE compile to the install/`pm-ai doctor` story.
- **potential_consequence** — Three compounding failures. (1) A builder installs whisper.cpp via Homebrew, gets no Core ML, and the "3× faster" assumption behind AD-19's single-heavy-job default evaporates. (2) The generated `.mlmodelc` is a build artifact the packaging story (`uv tool install`) has no place for. (3) **The first transcription after install blocks for minutes** on ANE compilation — blowing AD-21's 5-second acknowledgement rule and AD-22's ≤60 s synthesis budget on the very first user-visible meeting-pipeline run (UJ-3), which is the worst possible time.

### F-14 — whisper.cpp has no Python API, so the local adapter needs `subprocess` — which AD-1 forbids

- **location** — AD-1 ("The LLM core is granted zero shell or raw-terminal capability"); Enforcement table ("AST rules … shell execution"); source tree `models/local/ (Ollama, whisper.cpp)`
- **trigger_condition** — whisper.cpp is a C/C++ project shipping a `whisper-cli` binary and a static library. Driving it from Python means either `subprocess` or a third-party ctypes binding. AD-1 bans shell execution and the Enforcement table says an AST rule catches it — with no stated carve-out for the local model adapter.
- **guard_snippet** — whisper.cpp exposes a CLI (`./build/bin/whisper-cli -m models/ggml-small.en.bin -f samples/jfk.wav`) and a C API; there is no first-party Python package. Third-party bindings exist (`pywhispercpp`, `whispercpp`) but are not first-party and lag upstream. Source: https://github.com/ggml-org/whisper.cpp . **Guard:** state explicitly in AD-1 that `pm_ai.models.local` is the *sole* module permitted to spawn a process, that it may spawn only an allowlisted absolute binary path with no shell interpolation (`shell=False`, argv list), and encode that exception in `tests/architecture/test_static_rules.py` — otherwise the "zero shell" invariant is either violated on day one or the AST rule is quietly weakened to nothing.
- **potential_consequence** — The Phase 1 exit criterion is "zero skips in `tests/architecture/`". The shell-execution AST rule will fail against the only working transcription implementation. Whoever hits it first either adds a `# noqa`-equivalent skip (violating the exit criterion) or loosens the rule globally — which re-opens exactly the shell access AD-1 exists to forbid, across the whole codebase, to fix a problem in one module.

### F-15 — Ollama manages its own memory; AD-19's bounded pool does not reach it

- **location** — AD-19 ("Default bound: one heavy local-model job at a time (configurable), directly addressing the PRD's open question on swap thrashing at the 16GB baseline"); Open Risks
- **trigger_condition** — AD-19 bounds concurrency on the **client** side. Ollama is a separate server process with its own residency policy: by default it keeps a model loaded for 5 minutes after the last request, and parallel requests multiply the allocated context. A client-side semaphore of 1 does not unload anything.
- **guard_snippet** — `OLLAMA_KEEP_ALIVE` defaults to **5 minutes**; negative values mean infinite, zero means no keep-alive. `OLLAMA_MAX_LOADED_MODELS` caps simultaneous residency. "Parallel request processing for a given model results in increasing the context size by the number of parallel requests — for example, a 2K context with 4 parallel requests will result in an 8K context and additional memory allocation." Sources: https://docs.ollama.com/faq and https://github.com/ollama/ollama/blob/main/envconfig/config.go . **Guard:** the daemon must set `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, and either `OLLAMA_KEEP_ALIVE=0` or pass `keep_alive: 0` per request before launching a whisper.cpp job — and `pm-ai doctor` should report the live values.
- **potential_consequence** — The exact failure AD-19 claims to prevent. A Q4 8B model holds ~5–6 GB resident for 5 minutes after an extraction; a concurrent whisper.cpp `small.en` run with Metal + ANE draws from the same 16 GB unified pool with macOS already reserving several GB. The system swaps, and because the client-side bound reads as "1 job at a time", the guard looks satisfied while the machine thrashes. AD-19 explicitly claims to address the PRD's open question; it addresses half of it.

### F-16 — `python-telegram-bot`'s `run_polling()` seizes the event loop AD-19 says is singular

- **location** — AD-19 ("One asyncio event loop owns all I/O — connector harvests, Telegram long-poll, the loopback API, MCP calls"); AD-2 (outbound long-polling only); AD-7 (one daemon)
- **trigger_condition** — PTB's headline convenience method `Application.run_polling()` creates and takes ownership of an event loop and installs signal handlers. The daemon also runs uvicorn (AD-8's loopback HTTP API) in that loop. The two cannot coexist.
- **guard_snippet** — "`run_polling` … blocks the event loop … The error you encounter is `RuntimeError: This event loop is already running`. When combining python-telegram-bot with other asyncio based frameworks, you should instead manually call the methods to start and shut down the application and the updater, while keeping the event loop running and listening for a stop signal yourself." The correct sequence is `Application.initialize()` → `Application.start()` → `Updater.start_polling()`, with matching `stop()`/`shutdown()`. Sources: https://docs.python-telegram-bot.org/telegram.ext.application.html and https://github.com/python-telegram-bot/python-telegram-bot/issues/4107 . **Guard:** add to AD-19 — "the Telegram bridge uses PTB's manual lifecycle (`initialize`/`start`/`start_polling`); `run_polling()` and `run_webhook()` are prohibited" — and add `run_polling` to the forbidden-call AST rules alongside shell execution.
- **potential_consequence** — Whoever wires the Telegram bridge first, following PTB's own quickstart, produces a daemon where the bridge and the loopback API cannot both run. The likely "fix" is a second thread or a second process for Telegram — which directly violates AD-7's "a single daemon owns all background life … the CLI is a thin client that holds no state" and re-introduces the two-writer race AD-5 exists to prevent.

### F-17 — PTB's `[job-queue]` extra silently installs a second scheduler inside the daemon

- **location** — Stack table (`Scheduler | in-house asyncio scheduler (APScheduler evaluated at build time)`); AD-7; AD-9 ("The daemon's scheduler invokes it … and owns cursors, backoff, and rate limiting"); AD-13 ("expiry swept by the scheduler only")
- **trigger_condition** — `python-telegram-bot[job-queue]` (and `[ext]`, and `[all]`) pull `apscheduler>=3.10.4,<3.12.0`, and PTB's `Application` builder attaches a `JobQueue` — a live APScheduler instance — by default whenever it is installed. The spine says the daemon has exactly **one** scheduler that owns all deferred work.
- **guard_snippet** — Verified from `https://pypi.org/pypi/python-telegram-bot/json`: `apscheduler<3.12.0,>=3.10.4; extra == "job-queue"`, and the same dependency under `extra == "ext"` and `extra == "all"`. **Guard:** install plain `python-telegram-bot==22.8` with **no** extras, and construct the application as `ApplicationBuilder().token(...).job_queue(None).build()` so no JobQueue is created. Record it in AD-9 next to "A connector never runs its own thread, timer, or polling loop."
- **potential_consequence** — Two schedulers run in one process. AD-20's durability guarantee ("nothing is scheduled in memory only — every deferred unit of work is a persisted row") is silently false for anything a builder routes through PTB's `job_queue` (its default store is in-memory), and AD-13's proposal-expiry sweep can end up owned by a scheduler that loses all state on restart. The behavioural test for "expiry swept by the scheduler only" will pass while a second, invisible scheduler exists.

### F-18 — The `[mcp]` extra brings a large second web stack, including a second HTTP client

- **location** — Stack table (`anthropic (Python SDK, with [mcp] extra)`, `FastAPI`, `uvicorn`)
- **trigger_condition** — `anthropic[mcp]` pulls `mcp>=1.0,<3`, which resolves to `mcp==2.0.0` — and that package depends on `starlette`, `uvicorn`, `sse-starlette`, `opentelemetry-api`, `pyjwt[crypto]`, `python-multipart`, `jsonschema`, and **`httpx2>=2.5.0`** (a distinct distribution from `httpx`). The daemon therefore ships two ASGI-adjacent stacks and two HTTP client libraries.
- **guard_snippet** — Verified installed set: `mcp==2.0.0`, `mcp-types==2.0.0`, `starlette==1.6.0`, `sse-starlette==3.4.8`, `uvicorn==0.52.3`, `opentelemetry-api==1.44.0`, `pyjwt==2.13.0`, `python-multipart==0.0.32`, `jsonschema==4.26.0`, alongside `httpx==0.28.1` (constrained by PTB's `httpx<0.29,>=0.27`, tighter than anthropic's `httpx<1,>=0.25.0`). Dependency data from `https://pypi.org/pypi/mcp/json`. **No conflict today** — FastAPI 0.136.1 and 0.141.1 both require only `starlette>=0.46.0`, satisfied by 1.6.0. **Guard:** record that `starlette` is co-owned by FastAPI and `mcp`, and that `httpx` is jointly pinned by PTB (upper bound) and anthropic; a lockfile is mandatory rather than optional.
- **potential_consequence** — Not a Phase 1 blocker, but it makes AD-1's "no component may import an HTTP/API client to call an external service outside a connector or skill adapter" harder to enforce: the import-linter forbidden-module list must now name **both** `httpx` and `httpx2`, or the rule has a hole the day someone reaches for the one that isn't listed. It also means a future FastAPI major that raises the starlette floor above what `mcp` accepts becomes an unresolvable upgrade — with no lockfile to make the breakage visible early.

---

## Section 5 — macOS / Apple Silicon assertions made without verification

### F-19 — The keyring "universal2" requirement is quoted accurately but is operationally misleading

- **location** — Stack table, `keyring (macOS Keychain backend) | 25.7.0 — requires a universal2 Python build on macOS 11+`
- **trigger_condition** — The claim is a faithful paraphrase of keyring's README, but it is a stale artifact of a 2021 bug and it does not describe the interpreter this project will actually run on. `uv` ships **arch-specific** CPython builds (`cpython-3.13-macos-aarch64-none` / `-x86_64-none`), not universal2 — so read literally, the note says the pinned toolchain cannot satisfy the pinned dependency.
- **guard_snippet** — keyring's README says: *"macOS keychain supports macOS 11 (Big Sur) and later requires Python 3.8.7 or later with the 'universal2' binary. See #525 for details."* (https://pypi.org/project/keyring/ ; https://github.com/jaraco/keyring/issues/525 — closed). But on a **non-universal2** uv-managed CPython 3.13.14 the backend loads and binds the Security framework correctly:
  ```
  keyring backend: keyring.backends.macOS.Keyring (priority: 5)
  macOS Security framework bound OK: ['CFDataGetBytePtr', 'CFDataGetLength', 'CFDictionaryCreate', …]
  viable: True
  ```
  The real requirement is a Python built against a modern macOS SDK (which every current uv/Homebrew/python.org build is), not literally a universal2 binary. **Guard:** restate as "requires a Python built against the macOS 11+ SDK; uv-managed CPython satisfies this."
- **potential_consequence** — A builder takes the note at face value, discovers uv ships no universal2 interpreter, and either abandons uv for the packaging story (contradicting the Deployment section) or spends time building a universal2 Python to satisfy a constraint that no longer binds.

### F-20 — `launchd` + `uv tool install` + Keychain: three assertions, none tested together

- **location** — Deployment & operations ("Supervision: `launchd` user agent, `KeepAlive`, starts at login"; "Install / update: isolated install via `uv tool install`"); AD-6 ("The master key lives in the macOS Keychain so the daemon starts unattended"); AD-26
- **trigger_condition** — Three claims interlock and none is verified: (a) `uv tool install` places the entry point in `~/.local/bin`, which a launchd agent does not inherit on `PATH`; (b) a LaunchAgent only runs after login, so "starts unattended" means "starts after the user logs in", not "starts at boot"; (c) macOS Keychain items carry an ACL bound to the **binary** that created them, and `uv tool upgrade` replaces that binary.
- **guard_snippet** — LaunchAgents run in the user's login session and do not source the shell profile, so `ProgramArguments` must carry the absolute interpreter/entry-point path (`/Users/<u>/.local/bin/pm-ai`) and any needed environment must be set via `EnvironmentVariables` in the plist — see https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html . Keychain ACL semantics: a generic password's trusted-application list is bound to the code signature/path of the creating binary; a replaced binary triggers an interactive "wants to access key in your keychain" prompt — which is fatal for an unattended agent. **Guard:** verify in Phase 1 that (i) the plist uses absolute paths, (ii) the daemon can read the key after a `uv tool upgrade` without a GUI prompt, and (iii) `KeepAlive` is paired with `ThrottleInterval` so a crash-looping daemon does not respawn continuously.
- **potential_consequence** — The most likely failure is the silent one: after the first `uv tool upgrade`, the daemon restarts, the Keychain prompts, nobody is watching, and the daemon either blocks forever or falls back to the AD-6 "encryption off" path — which is supposed to emit a CLI banner and an `event_log.md` entry, but only if that path was reached deliberately rather than by a swallowed Keychain denial. Silent decryption failure on a supervised background agent is the worst-shaped bug in this design.

### F-21 — "Metal + Core ML" conflates a default with an opt-in

- **location** — Stack table, `whisper.cpp (Metal + Core ML)`
- **trigger_condition** — Presenting the two as a single parenthetical implies both come with the install. Metal (`GGML_METAL`) is enabled by default on Apple Silicon builds; Core ML requires `-DWHISPER_COREML=1` plus a generated encoder (F-13).
- **guard_snippet** — See F-13's guard. The whisper.cpp system banner distinguishes them explicitly — `NEON = 1 | ARM_FMA = 1 | FP16_VA = 1 | BLAS = 1 | COREML = 1` — and `COREML = 1` appears only on a Core ML build. Source: https://github.com/ggml-org/whisper.cpp#core-ml-support . Split the row: `whisper.cpp (Metal — default) + Core ML encoder (opt-in build, see install notes)`.
- **potential_consequence** — Benchmarks in Phase 1 are run on a Metal-only build while the architecture's performance assumptions (and AD-19's single-job default) were reasoned about as if the ANE were carrying the encoder. The measured numbers are ~3× worse than the design assumed, and the conclusion drawn is "the 16 GB baseline is inadequate" rather than "Core ML was never enabled."

### F-22 — The Microsoft Graph Open Risk is three weeks out of date

- **location** — Open Risks, first bullet
- **trigger_condition** — The stated risk (application permissions need an admin-created application access policy; personal Microsoft accounts unsupported; transcripts exist only where recording was enabled) is **accurate**, but incomplete as of this document's date. A tenant-level gate was added on **2026-07-31** — 18 days before the document was written — and an application access policy alone is no longer sufficient.
- **guard_snippet** — Verified baseline claims: *"To use application permission for this API, tenant administrators must create an application access policy and grant it to a user"* and *"Delegated permission for personal Microsoft accounts is not supported"* — https://learn.microsoft.com/en-us/graph/api/calltranscript-get and https://learn.microsoft.com/en-us/graph/cloud-communication-online-meeting-application-access-policy . **New:** after the 2026-07-31 policy change, tenant-level Graph access to transcripts must additionally be enabled in Teams admin center → **Meetings → Meeting settings → Transcript API access**; when off, every transcript request returns **403** with inner-error `GraphAccessToTranscriptsDisabled`. Source: https://learn.microsoft.com/en-us/answers/questions/5965856/graph-api-access-to-transcripts-stopped-working-af . Update the bullet to name **two** admin gates, not one.
- **potential_consequence** — The mitigation (AD-23's manual fallback adapter) is correct and unaffected — this is the spine's strongest defensive decision and it holds. But the *diagnosis* is wrong: a builder who gets the application access policy created will still receive 403s, and the inner-error code is the only way to tell "policy missing" from "tenant toggle off". Without it in the doc, the failure looks like a permissions bug in pm-ai and burns a round-trip with the tenant admin.

---

## Explicitly could NOT verify

Listed rather than passed over silently.

1. **Execution on Apple Silicon (arm64).** The review host resolved as `x86_64-apple-darwin` (uv 0.12.2, Homebrew x86_64). All install/import/runtime tests therefore ran on x86_64. arm64 wheel *existence* is confirmed for `sqlcipher3` (`macosx_11_0_arm64`, cp310–cp314) and `sqlite-vec` (`sqlite_vec-0.1.9-py3-none-macosx_11_0_arm64.whl`), but their execution on Apple Silicon — the only v1 target per AD-26 — was not exercised.
2. **The 16 GB concurrency question.** AD-19's single-heavy-job default and the Open Risk's "unbenchmarked" status could not be tested; no 16 GB Apple Silicon machine was available. F-15 identifies the *mechanism* (Ollama's server-side residency) but not the magnitude.
3. **whisper.cpp Core ML end to end.** Generating `ggml-small.en-encoder.mlmodelc` needs Xcode command-line tools plus a PyTorch/coremltools environment; not attempted. F-13's prerequisites come from the upstream README, not from a completed build. The first-run ANE compile duration ("may take a while") is not quantified upstream, so its collision with AD-21/AD-22 is qualitative.
4. **Anthropic prompt-cache minimums and pricing after the skill's cache date.** The bundled `claude-api` skill's model/pricing table is cached 2026-06-24; `platform.claude.com` was not reachable directly from this environment for live confirmation. The 512/1024 split (F-06) and the Sonnet 5 introductory-pricing expiry (F-08) are taken from that skill, which is authoritative in-repo but two months old on pricing specifically.
5. **The Microsoft Teams transcript toggle's exact label and rollout completeness.** F-22 rests on a single Microsoft Q&A thread; the change is not yet reflected in the main `calltranscript-get` reference page. The direction is clear; the precise admin-center path may differ by tenant.
6. **Keychain ACL survival across `uv tool upgrade`.** F-20(c) is reasoned from documented macOS Keychain ACL semantics, not from an executed upgrade-then-restart cycle. This is the highest-value untested claim in the document and should be Phase 1's first integration test.
7. **Telegram long-poll stability behind NAT over multi-hour idle.** AD-2's outbound-only design is sound in principle; no connection-drop/reconnect behaviour was exercised.

---

## Summary tables

### Verified good (25)

Version currency: uvicorn 0.52.3 · python-telegram-bot 22.8 · keyring 25.7.0 · anthropic 0.122.0 + real `[mcp]` extra · sqlcipher3 0.6.2 (macOS arm64 wheels, cp313 + cp314) · sqlite-vec 0.1.9 · Ollama v0.32.14 · whisper.cpp v1.9.2 · APScheduler 3.11.3 · FastAPI 0.136.1 real and unyanked · Python 3.13 supported by every pin · Python 3.14 upgrade path resolves cleanly.

Anthropic API notes: `claude-opus-5` and `claude-sonnet-5` are real current IDs · thinking on by default on Opus 5 · `max_tokens` caps thinking + text · `temperature`/`top_p`/`top_k` rejected · prefill rejected, `output_config.format` is the replacement · `output_config.effort` controls depth · 512-token cache minimum on Opus 5 · `client.beta.messages.tool_runner` exists · AD-16 is right that the Claude Agent SDK ships built-in Bash/Read/Write/Edit and must be excluded from that layer.

Integration: SQLCipher + sqlite-vec interoperate with genuine encryption (executed) · the full pinned stack resolves and imports with no conflict (executed) · keyring's macOS backend is viable and binds the Security framework (executed) · Microsoft Graph application-access-policy and personal-account constraints are accurately stated as far as they go.

### Problematic (22)

| # | Finding | Severity |
|---|---|---|
| F-01 | Llama 3.3 8B does not exist (70B only) | **Blocker** |
| F-11 | sqlite-vec unloadable on stock macOS Python | **Blocker** |
| F-14 | whisper.cpp needs `subprocess`; AD-1 + AST rule forbid it | **Blocker** |
| F-16 | PTB `run_polling()` vs AD-19's single event loop | **Blocker** |
| F-13 | whisper.cpp Core ML build + toolchain prerequisites | High |
| F-15 | Ollama server-side memory residency defeats AD-19's bound | High |
| F-17 | PTB `[job-queue]` installs a second scheduler | High |
| F-07 | Opus 5 `stop_reason: "refusal"` unhandled | High |
| F-20 | launchd + `uv tool install` + Keychain ACL on upgrade | High |
| F-06 | Cache minimum is 1024 on Sonnet 5, not 512 | Medium |
| F-09 | Tool Runner is beta; `pause_turn` truncates silently | Medium |
| F-12 | `sqlcipher3-binary` is Linux-only (documented trap) | Medium |
| F-22 | Graph Open Risk stale — 2026-07-31 tenant toggle | Medium |
| F-08 | Sonnet 5 intro pricing expires 2026-08-31 | Medium |
| F-10 | Thinking tokens billed but invisible (`display: omitted`) | Medium |
| F-05 | No embedding model or vector dimension pinned | Medium |
| F-04 | No Ollama Python client pinned | Medium |
| F-19 | keyring `universal2` claim stale/misleading | Medium |
| F-21 | Metal (default) conflated with Core ML (opt-in) | Medium |
| F-02 | FastAPI pin 5 releases / 3 months stale | Low |
| F-03 | sqlite-vec pre-1.0 pinned as `latest` | Low |
| F-18 | `[mcp]` brings a second web stack incl. `httpx2` | Low |

### Unverifiable (7)

arm64 execution · 16 GB concurrency magnitude · whisper.cpp Core ML end-to-end · post-2026-06-24 Anthropic pricing/cache changes · Teams transcript toggle exact path · Keychain ACL survival across `uv tool upgrade` · Telegram long-poll stability over long idle.

---

## Recommended Phase 1 ordering

The four blockers are cheap to close and all four are discoverable on day one:

1. Replace the Llama 3.3 8B row with a real tag (F-01) — five minutes, prevents a wrong-turn into 70B.
2. Add a `pm-ai doctor` probe asserting `hasattr(conn, "enable_load_extension")`, and decide interpreter policy (F-11) — turns a deep runtime `AttributeError` into a startup check.
3. Write the AD-1 carve-out for `pm_ai.models.local` **before** writing the AST rule (F-14) — otherwise the exit criterion and the implementation collide.
4. Wire the Telegram bridge with PTB's manual lifecycle and `job_queue(None)` from the first commit (F-16, F-17) — retrofitting this after `run_polling()` is embedded means restructuring the daemon.

Then verify the Keychain-across-upgrade path (F-20), which is the only finding whose failure mode is silent, unattended, and security-relevant.
