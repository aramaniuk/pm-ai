---
title: 'The local model runs behind the model port, and is measured'
type: 'feature'
created: '2026-10-16'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** No model runs in pm-ai. `ModelPort` is declared and has no implementation, `pm_ai/models/local/` and `pm_ai/models/frontier/` hold only a docstring, and the port's own docstring says it cannot yet carry what a frontier call must disclose. Story 7 also owes a measurement the spec leaves open: whether 16 GB of Apple Silicon memory is enough for the 8B-class model. Until a model runs, a transcript yields only what pattern matching finds — on the field-test transcripts, nothing useful.

**Approach:** An adapter for the local Ollama server behind `ModelPort`, reached on loopback only by a client built so it cannot be pointed anywhere else; the model named in `config.toml` and never pulled by pm-ai; a model pool of one worker in the daemon (AD-19); the port widened once, for both adapters, to carry where the answer goes and what the call used; `setup` and `doctor` say whether the model is there and how to get it. A benchmark script, run by hand, records time, memory and swap on a transcript of real length and alphabet, and scores answers on an authored mixed Russian/English transcript; the measurement picks the model. Whisper is not in this wave.

## Boundaries & Constraints

**Always:**
- **Prerequisites, named before the build:** the spine amendments listed in the Change Log; PR #72's fixture `tests/connectors/fixtures/graph/transcript_recorded.vtt` on `main` — hard, the benchmark has no other input of real length.
- **Nothing can leave the machine through this adapter — by construction, each with a test.** The adapter does not use the `ollama` Python package: version 0.6.2 reads `OLLAMA_HOST` and `OLLAMA_API_KEY` from the environment, adds the key as a bearer header, and follows redirects by default (verified: `ollama/_client.py:85,108-113`, 0.6.2). It builds its own `httpx.Client` with the base URL `http://127.0.0.1:11434` as a constant, `trust_env=False` (no environment or system proxy), `follow_redirects=False`, no authorization header, and a timeout equal to the caller's deadline. There is no host setting: a `config.toml` key naming one is refused as an unknown key, as `4a` refuses every unknown key. Tests inject an `httpx.MockTransport`; wiring never passes one, so no test can reach a real Ollama on a developer's machine.
- **No model that runs elsewhere.** A model name whose tag contains `cloud`, or that the server reports as running on a remote host, is refused by the adapter, `setup` and `doctor`. *Assumed, not verified:* that Ollama relays such models to its hosted service when the user is signed in, and how `/api/show` marks them; the build checks both against the installed Ollama's API reference and records the version.
- **The port is widened once, here, for both adapters.** `complete(*, task_class, instructions, external, flow, shape=None) -> ModelAnswer`. `flow` is required: the scopes the fragments came from and the destination the answer is bound for, a scope or `EXTERNAL`. `shape` is an optional JSON schema the answer must follow. `ModelAnswer` carries the text, the model's name, whether it ran locally, and the token counts and durations the server reported — what AD-31's ledger needs, which `-> str` could not carry (`ports/__init__.py:998`). `external` stays `Sequence[Sanitized]`. The refusals a caller must catch — `LocalModelUnavailable`, `NoLocalModel`, `PossiblyTruncated`, `ModelTimedOut`, `ScopeFlowRefused` — are declared in `pm_ai/ports` beside the port (AD-49).
- **Which flows are allowed is a closed table in `domain`, checked before anything is sent, on every call (SPEC Constraints, AD-31, AD-38).** Allowed: every source and the destination the same `personal`, `project:<id>` or `people:<id>` scope; project material bound for `personal`. Refused by name (`ScopeFlowRefused`, naming sources and destination), with no request made: personal bound for a project, `people` or `EXTERNAL`; personal and `people` in one prompt; `people` bound anywhere but itself; one project's material bound for another project; anything bound for `EXTERNAL` — no current flow has an external destination, and a slice that needs one adds its row; a flow with no sources. Anything the table does not list is refused.
- **The model is named, never pulled.** `config.toml` gains `local_model` (unset is the empty string, as `display_timezone`). A name without a tag means `:latest`, and names are compared in that form. pm-ai never downloads a model. `setup` asks for the name in its step 3 (`config.toml`), after the existing keys, offering the one this slice's measurement selects, and prints `ollama pull <name>`; `doctor` gains a `local model` line from a bounded loopback request to `/api/tags`: `OK` when the named model is listed; `WARNING` when Ollama does not answer, answers an error, answers late or answers something malformed (naming what came back), or the model is not pulled, each with its remedy; `ABSENT` when `local_model` is unset.
- **Every request asks the same way:** `keep_alive: 0`, temperature 0, a fixed seed, a context window `num_ctx` that is a constant set from this slice's measurement (a conservative 8,192 until then), and `think: false` only for a model whose `/api/show` lists thinking among its capabilities. An answer is refused by name when it has no prompt or output token count, when its `done_reason` is not `stop`, or when its prompt and output tokens together reach the window — `PossiblyTruncated`, naming the counts or the field. *Assumed, not verified:* those request and answer fields, and that Ollama shortens an over-long prompt rather than refusing it; checked at build against the installed Ollama's API reference.
- **One heavy model job at a time, on its own pool (AD-19).** `concurrency.py` gains a model pool of one daemon thread, shaped like `IOPool`. A model call never runs on the loop, the writer thread or an I/O worker. Its deadline is the request's own timeout, so the worker's slot is held until the request has actually ended and a second job never overlaps a generation still running. A deadline or the shutdown abandons the job (AD-5): the connection is closed and nothing it would have written is written. *Assumed:* that Ollama stops generating when the client disconnects.
- **The server's own limits are named, not set.** The daemon cannot set `OLLAMA_MAX_LOADED_MODELS=1` and `OLLAMA_NUM_PARALLEL=1` on a server it did not start, and pm-ai does not start one. `setup` and `doctor` print both and how to set them; `doctor` says it cannot read the server's settings. `keep_alive: 0` and the pool of one are what pm-ai itself guarantees.
- **Whisper is not in this wave.** Every transcript arrives as text — Graph's WebVTT, or `transcript drop` — and no audio enters pm-ai until voice notes do. So no whisper.cpp binary and no transcription task; the spec's open question about transcription and parsing together (SPEC.md:266) stays open. AD-19's "`keep_alive: 0` before dispatching transcription" holds trivially: every request sends it.
- **The local adapter answers local-only classes only** (AD-15). Any other class is refused by name, naming the router, which routes it once `7c` has landed.
- **The benchmark is a script run by hand** (`benchmarks/local_model.py`), never by pytest, against a throwaway storage root it creates — never `~/.pm-ai`. For each candidate — the spine's two verified ones, `llama3.1:8b` and `qwen3:8b`, at `Q4_K_M` — three runs alone and three while the daemon, in that throwaway root, persists the largest batch a GitLab harvest can return (50,000 events, `9a`'s figure) in a loop, standing in for heavy background telemetry. Recorded per run: cold load, prompt tokens and rate, output tokens and rate, wall time, peak resident memory of the Ollama runner and of pm-ai, swap used before and after, page-outs during the run, the memory-pressure level, and the input's characters per token. Recorded once: the machine's model, memory and macOS version, the Ollama version, and the CPU architecture pm-ai's Python ran as (`9a` measured under Rosetta). Its input is the letter-redacted recording from PR #72 (198 cues, four speakers, Cyrillic and Latin): its length and alphabet are real, so token counts and timings are; its words are not words, so it measures nothing about quality.
- **Quality is measured on an authored transcript, never a private one.** A fictional mixed Russian/English meeting of about 200 cues and four speakers labelled `Speaker 1`–`Speaker 4` is committed with an answer key: planted decisions, planted action items (each with an owner label and a key token — an invented ticket id or date), and distractors (proposals raised and rejected) that must not come back as decisions. The script scores: whether the answer validated first time, each planted item found (its key token and owner in one returned item), distractors returned, and items matching no key — read by hand and recorded as invented or legitimate. Run with `--private` on a real transcript, it prints only counts and outcomes — prompt tokens, redactions the filter made, items returned, validation — and never a word of the text; the PM's judgement is recorded as counts. Nothing from a real transcript is committed.
- **The measurement chooses the model**: of the candidates that validate on every run, the higher planted-item recall; then the lower peak memory; then the shorter median wall time; then `qwen3:8b`, named so a full tie still has an answer. Recorded with its numbers and offered by `setup`.
- **No figure is stated before it is measured.** Every number lands in this spec's Design Notes with the machine it was measured on.

**Ask First:**
- If no 16 GB Apple Silicon machine is available, the numbers are recorded on the machine at hand with its memory named, and whether 16 GB is enough stays open — confirm before anything calls it answered.
- If one call's wall time at the chosen window exceeds 300 seconds (half of CAP-3's 600), or swap grows during a run alone.
- If no candidate validates on every run.
- If a model running elsewhere cannot be told apart from a local one on the installed Ollama.

**Never:** No whisper.cpp, no audio. No `ollama` Python package. No frontier adapter, router or disclosure write (`7c`). No summary, decision or action item written anywhere (`7b`). No model pull, no `ollama serve` spawned, no host setting, no proxy, no redirect. No model in any test pytest runs. No transcript text in the benchmark's output or in anything committed.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Call | model pulled, Ollama running, `extraction` | `ModelAnswer` with text, model, local, counts, durations; request to 127.0.0.1:11434 carrying `keep_alive: 0`, temperature 0, the seed, `num_ctx` | N/A |
| Proxy and key in the environment | `HTTPS_PROXY`, `OLLAMA_HOST=<another host>`, `OLLAMA_API_KEY` set | request still to 127.0.0.1:11434, no proxy, no authorization header | N/A |
| Redirect | the server answers 307 to another host | not followed | refused naming the status |
| Host in config | a host key in `config.toml` | refused as an unknown key | `4a`'s refusal |
| Cloud model | `local_model = "gpt-oss:120b-cloud"` | refused by setup, doctor and the adapter | names the rule |
| Name without tag | `local_model = "qwen3"` | compared as `qwen3:latest` | N/A |
| Shape given | a JSON schema | sent as the request's format; text returned as given — validation is the caller's | N/A |
| Thinking model | capabilities list thinking | `think: false` sent; otherwise not sent | N/A |
| Ollama not running | nothing on 127.0.0.1:11434 | refused; no retry here | `LocalModelUnavailable` naming the address and how to start Ollama |
| Model not pulled | `qwen3:8b` not listed | refused | `LocalModelUnavailable` naming `ollama pull qwen3:8b` |
| No model named | `local_model` unset | no local adapter wired | `NoLocalModel` |
| Window reached or cut short | counts reach `num_ctx`, `done_reason` not `stop`, or a count missing | answer discarded | `PossiblyTruncated` naming the counts or the field |
| Flows | each row of the table, allowed and refused | allowed ones sent; refused ones never reach the transport | `ScopeFlowRefused` naming sources and destination |
| Frontier-eligible class | `briefing_synthesis` to the local adapter | no request made | refused naming the router |
| Deadline | the call outlasts it | connection closed; slot freed when the request ended; nothing written | `ModelTimedOut` |
| Two calls | second submitted while the first runs | runs after it | N/A |
| Shutdown mid-call | daemon stopping | connection closed; nothing written | N/A |
| `doctor` | pulled / not pulled / no answer / error, malformed or slow answer / unset | `OK` / `WARNING` + pull / `WARNING` + start, and the two server settings / `WARNING` naming what came back / `ABSENT` + `setup` | the probe never raises |
| Benchmark `--private` | a real transcript | counts and outcomes printed; no word of text | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/ports/__init__.py:963-1046` -- `ModelPort`; `:991-1008` its two stated limits (provider text in `instructions`; `-> str` cannot feed the ledger); `:1029` the signature
- `pm_ai/domain/task_classes.py:55-67` -- `TaskClass`; local-only per AD-15: `transcription`, `extraction`, `classification`, `embedding`, `fuzzy_match`
- `pm_ai/domain/sanitize.py:620,712` -- `Sanitized`, `sanitize`; no Cyrillic in the filter's vocabulary
- `pm_ai/domain/disclosure.py:158` -- `assert_citation_legal`, the shape the flow table follows
- `pm_ai/models/__init__.py`, `pm_ai/models/local/__init__.py` -- empty; the adapter goes in `local/`
- `pm_ai/app/concurrency.py:67,351-404` -- `IO_POOL_SIZE`, `IOPool`; the model pool beside it; `pm_ai/app/daemon.py:307` starts the I/O pool
- `pm_ai/app/wiring.py:209-296,353` -- `Daemon` (gains `model`), `build`
- `pm_ai/core/config.py:236-269` -- `Config`; `:31-39` unknown keys refused
- `pm_ai/platform/doctor.py:782,905` -- `daemon_reachable` and `_exchange`, the bounded loopback request to copy; `pm_ai/app/entry.py:1330,1461` -- `_diagnose`, the line appended after `transcript ingestion`
- `pm_ai/surfaces/cli/dispatch.py:2320` -- `_setup`
- `pyproject.toml:37` -- `"ollama"`, removed; `httpx` declared at the lock's 0.28.1 (`uv.lock:400`)
- `.importlinter:80-93` -- model clients confined to `pm_ai.models`; the probe in `pm_ai.platform` uses the standard library
- `tests/architecture/test_transcript_path_boundary.py:148-162` -- the only functions allowed to take cleaned text: today exactly `ModelPort.complete`; extended deliberately, by name
- `tests/architecture/fixtures/model_port_misuse.py`, `tests/architecture/test_sanitize_boundary.py:91,402` -- `8e`'s mypy fixture; its correct calls gain `flow`, and a call without `flow` becomes a refused case
- `tests/architecture/test_domain_invariants.py:295-345,405-436` -- the router tests stay skipped (`7c`)

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/model_calls.py` -- new, pure: `ScopeFlow`, `EXTERNAL`, `ModelAnswer`, the flow table and `assert_flow_legal` -- one decision for both adapters
- [ ] `pm_ai/ports/__init__.py` -- `complete` widened; the five refusals declared; the docstring's ledger limit rewritten as met -- the port changes once
- [ ] `pm_ai/models/local/ollama.py` -- the adapter: its own `httpx.Client` (constant URL, no environment, no redirects, deadline timeout), remote-model refusal, request options, `think` by capability, local-only classes, flow check first, truncation refusals, `ModelAnswer` from the reply -- the one local client
- [ ] `pm_ai/app/concurrency.py`, `pm_ai/app/wiring.py`, `pm_ai/app/daemon.py` -- model pool of one, started and stopped with the I/O pool; `Daemon.model` wired when `local_model` is set -- AD-19
- [ ] `pm_ai/core/config.py`, `pm_ai/surfaces/cli/dispatch.py` (`_setup`), `pm_ai/platform/doctor.py`, `pm_ai/app/entry.py` -- `local_model`, name normalisation, remote names refused; setup's question; the `local model` probe -- presence said, never pulled
- [ ] `pyproject.toml` -- remove `ollama`; `httpx==0.28.1` -- no client that reads the environment
- [ ] `tests/architecture/test_transcript_path_boundary.py` -- the allowed set becomes `ModelPort.complete` and the local adapter's `complete`, named one by one -- a new taker of cleaned text still fails
- [ ] `tests/architecture/fixtures/model_port_misuse.py` -- `flow` on the correct calls; a call without it marked refused -- `8e`'s fixture follows the port
- [ ] `tests/models/test_ollama_adapter.py` -- one test per matrix row over `httpx.MockTransport`, the environment rows with the variables set -- the matrix is the contract
- [ ] `benchmarks/local_model.py`, `benchmarks/fixtures/mixed_ru_en.vtt`, `benchmarks/fixtures/mixed_ru_en.key.toml` -- the script (throwaway root), the authored transcript and its key, and a first extraction prompt kept with the script (`7b` re-runs it with its own) -- the measurement
- [ ] `stories/8e-sanitization-binds-at-the-boundary.md` -- a dated Change Log line: the port is widened and the fixture amended by `7a` -- an earlier frozen block amended on the record
- [ ] `stories/4h-first-run-setup.md` -- a dated Change Log line: setup's step 3 gains the `local_model` question (`7a`) -- an earlier frozen block amended on the record
- [ ] `stories/11b-the-real-transcript-path.md` -- a dated Change Log line: its only-acceptor test is widened by name to the local adapter's `complete` (`7a`) -- an earlier frozen block amended on the record

**Acceptance Criteria:**
- Given a mock transport and `HTTPS_PROXY` and `OLLAMA_HOST` naming another host, when a call is made, then the one request the transport sees is to `127.0.0.1:11434` with no authorization header.
- Given the daemon running a model call, when a second is submitted and the shutdown begins, then the second never starts, the first's connection is closed, and the writer closes as `4r` orders it.
- Given `uv run pytest`, then everything passes, the skip count is unchanged, and no test contacts a real Ollama.

## Spec Change Log

- **2026-10-16, created** (decision of 2026-10-16: story 7 comes forward, local model first). Carries: the port widened once for both adapters; the destination rule on local calls too; Ollama on a loopback constant; `local_model` named, never pulled; fixed request options and a measured window, a full window refused; a model pool of one; the server's two limits named rather than set; Whisper out of this wave; the benchmark on the redacted fixture and on an authored transcript with a private counts-only mode; the measurement picks the model.
- **2026-10-16, review:** egress closed by construction — own `httpx` client (constant URL, `trust_env=False`, no redirects, deadline timeout, no key header), the `ollama` package dropped after reading its environment handling (verified, 0.6.2), remote (`cloud`) models refused (assumed behaviour, checked at build); a closed flow table with `EXTERNAL`; refusals declared in `ports` (AD-49); name normalisation; `think` by capability; missing counts and a `done_reason` other than `stop` refused; the slot held until the request ends; doctor's malformed answers; tests over a mock transport only; benchmark in a throwaway root, CPU architecture and characters per token recorded; selection tie order; `8e`'s fixture and the only-takers test amended; every Ollama behaviour marked verified or assumed; plain words. Depends on spine amendments: **AD-19** — the server's two limits are named by setup and doctor, not set; the model pool is one worker; **AD-12** — its limit "nothing in `pm_ai/` implements or calls `ModelPort`" is retired; **AD-1** — local model requests to `127.0.0.1:11434` are their own egress class, loopback only, carrying cleaned text; **AD-49** — the five model refusals live in `pm_ai/ports`; **Stack** — the Ollama Python client row becomes "none: `httpx` directly"; **Deferred, Local-model selection** — benchmarked without whisper.cpp, SPEC open question 266 left open. Amends `8e`'s frozen block (task above).
- **2026-10-16, review (layout):** the Change Log moved before Design Notes, as the template places it.
- **2026-10-16, review (cross-spec):** the `local_model` question placed in setup's step 3; Change Log tasks for `4h` and for `11b`'s only-acceptor test.

## Design Notes

The port's widening is the one place `7b` and `7c` meet: `7b` calls the local adapter with `flow` from the meeting's scope to itself; `7c`'s router puts the frontier adapter behind the same signature and writes the disclosure line from `ModelAnswer`.

The context window is the main memory cost after the weights: its cache grows with `num_ctx`. So `num_ctx` is chosen from the measurement — the smallest power of two holding the fixture's measured prompt tokens plus the answer budget, if its peak memory leaves room at 16 GB — and that peak is what "is 16 GB enough" is asked about. The characters-per-token figure recorded here is what `7b` sizes its chunks with.

Choices made in review (2026-10-16): the `ollama` package is dropped rather than wrapped, because two of its defaults read the environment and closing them from outside depends on its internals; a closed flow table rather than one rule, because the unlisted pairs (one project to another, `people` to anywhere) had no answer; a full-tie order ending in a named model, so the selection rule always answers.

Not answered by this slice: whether whisper.cpp and the parsing model fit together at 16 GB (SPEC.md:266; the spine's Deferred "Local-model selection" assumed both would be benchmarked together).

Measured figures: none yet.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; skip count unchanged
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- With Ollama installed and both models pulled: `uv run python benchmarks/local_model.py --model qwen3:8b` and `--model llama3.1:8b` print every measure for six runs each; the figures, the machine and the chosen model are written into this spec's Design Notes.
- `pm-ai doctor` shows `local model` `OK`; with Ollama quit, `WARNING` naming how to start it.

