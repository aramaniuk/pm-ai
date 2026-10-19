---
title: 'The local model runs behind the model port, and is measured'
type: 'feature'
created: '2026-10-16'
status: 'done'
baseline_commit: '3a9d4b489a9ccc444d46483db85e542714956af5'
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
- **No model that runs elsewhere, refused before any prompt leaves.** The local server forwards a cloud model to Ollama's hosted service, and an answer naming `remote_host` or `remote_model` arrives only after the prompt has left. So the adapter, `setup` and `doctor` ask `/api/show` first and refuse by name a model whose answer names `remote_host` or `remote_model`, or whose tag is `:cloud` or `-cloud` (verified in Ollama's API types and cloud docs, 2026-10-16, spine gate); the answer's own fields are checked again as a backstop. `setup` and `doctor` also name `OLLAMA_NO_CLOUD=1`, which turns cloud models off on the server. A 503 from an overloaded server is refused (`LocalModelUnavailable`), never retried.
- **The port is widened once, here, for both adapters.** `complete(*, task_class, instructions, external, flow, shape=None) -> ModelAnswer`. `flow` is required: the scopes the fragments came from and the destination the answer is bound for, a scope or `EXTERNAL`. `shape` is an optional JSON schema the answer must follow. `ModelAnswer` carries the text, the model's name, whether it ran locally, and the token counts and durations the server reported — what AD-31's ledger needs, which `-> str` could not carry (`ports/__init__.py:1067`). `external` stays `Sequence[Sanitized]`. The refusals a caller must catch — `LocalModelUnavailable`, `NoLocalModel`, `PossiblyTruncated`, `ModelTimedOut` — are about the port's transport and are declared in `pm_ai/ports` beside the port; `ScopeFlowRefused` is about the flow table, a rule `domain` owns, and is declared in `pm_ai/domain/model_calls.py` beside that table (AD-49, as applied at the spine gate 2026-10-16).
- **Which flows are allowed is a closed table in `domain`, checked before anything is sent, on every call (SPEC Constraints, AD-31, AD-38).** Allowed: every source and the destination the same `personal`, `project:<id>` or `people:<id>` scope; project material bound for `personal`. Refused by name (`ScopeFlowRefused`, naming sources and destination), with no request made: personal bound for a project, `people` or `EXTERNAL`; personal and `people` in one prompt; `people` bound anywhere but itself; one project's material bound for another project; anything bound for `EXTERNAL` — no current flow has an external destination, and a slice that needs one adds its row; a flow with no sources. Anything the table does not list is refused.
- **The model is named, never pulled.** `config.toml` gains `local_model` (unset is the empty string, as `display_timezone`). A name without a tag means `:latest`, and names are compared in that form. pm-ai never downloads a model. `setup` asks for the name in its step 3 (`config.toml`), after the existing keys, offering the one this slice's measurement selects, and prints `ollama pull <name>`; `doctor` gains a `local model` line from a bounded presence check on `/api/tags` and `/api/show` — a method of the local adapter in `pm_ai.models.local`, on its one client, reached by `setup` and `doctor` through the composition root, never an HTTP client in `pm_ai.platform` (AD-1 class LM): `OK` when the named model is listed; `WARNING` when Ollama does not answer, answers an error, answers late or answers something malformed (naming what came back), or the model is not pulled, each with its remedy; when `local_model` is unset, `OK`, saying how to set one up — an optional model left unset is not a failure, and only a named model that is broken, refused or unreachable affects `doctor`'s exit code (Andrei, 2026-10-19).
- **Every request asks the same way:** `keep_alive: 0`, temperature 0, a fixed seed, a context window `num_ctx` of 16,384 (Andrei, 2026-10-19: a whole meeting in one call, from this slice's measurement), an output cap `num_predict` sized from the longest valid answer measured, with headroom, `truncate: false` so an over-long prompt is refused, never silently shortened, and `think: false` only for a model whose `/api/show` lists thinking among its capabilities. An answer is refused by name when it has no prompt or output token count, when its `done_reason` is not `stop`, or when its prompt and output tokens together reach the window — `PossiblyTruncated`, naming the counts or the field. An answer that stops at the output cap (`done_reason` `length`) is refused as `OutputCapReached`, with a warning in the log naming the model, the cap and the task class, never any transcript text; the caller handles it as an invalid answer. *Assumed, not verified:* those request and answer fields, and that Ollama shortens an over-long prompt rather than refusing it; checked at build against the installed Ollama's API reference.
- **One heavy model job at a time, on its own pool (AD-19).** `concurrency.py` gains a model pool of one daemon thread, shaped like `IOPool`. A model call never runs on the loop, the writer thread or an I/O worker. Its deadline is the request's own timeout, so the worker's slot is held until the request has actually ended and a second job never overlaps a generation still running. A deadline or the shutdown abandons the job (AD-5): the connection is closed and nothing it would have written is written. *Assumed:* that Ollama stops generating when the client disconnects.
- **The server's own limits are named, not set.** The daemon cannot set `OLLAMA_MAX_LOADED_MODELS=1` and `OLLAMA_NUM_PARALLEL=1` on a server it did not start, and pm-ai does not start one. `setup` and `doctor` print both and how to set them — on macOS through `launchctl setenv` followed by restarting the Ollama app, since a shell `export` does not reach it; `OLLAMA_NUM_PARALLEL` already defaults to 1 on current servers — and `doctor` says it cannot read the server's settings. `keep_alive: 0` and the pool of one are what pm-ai itself guarantees.
- **Whisper is not in this wave.** Every transcript arrives as text — Graph's WebVTT, or `transcript drop` — and no audio enters pm-ai until voice notes do. So no whisper.cpp binary and no transcription task; the spec's open question about transcription and parsing together (SPEC.md:266) stays open. AD-19's "`keep_alive: 0` before dispatching transcription" holds trivially: every request sends it.
- **The local adapter answers local-only classes only** (AD-15). Any other class is refused by name, naming the router, which routes it once `7c` has landed.
- **The benchmark is a script run by hand** (`benchmarks/local_model.py`), never by pytest, against a throwaway storage root it creates — never `~/.pm-ai`. For each candidate — the spine's two verified ones, `llama3.1:8b` and `qwen3:8b`, at `Q4_K_M` — three runs alone and three while the daemon, in that throwaway root, persists the largest batch a GitLab harvest can return (50,000 events, `9a`'s figure) in a loop, standing in for heavy background telemetry. Recorded per run: cold load, prompt tokens and rate, output tokens and rate, wall time, peak resident memory of the Ollama runner and of pm-ai, swap used before and after, page-outs during the run, the memory-pressure level, and the input's characters per token. Recorded once: the machine's model, memory and macOS version, the Ollama version, and the CPU architecture pm-ai's Python ran as (`9a` measured under Rosetta). If that architecture is x86_64 under Rosetta, the benchmark stops before its first run and asks (Ask First below) rather than switching interpreters inside this slice. Its input is the letter-redacted recording from PR #72 (198 cues, four speakers, Cyrillic and Latin): its length and alphabet are real, so token counts and timings are; its words are not words, so it measures nothing about quality.
- **Quality is measured on an authored transcript, never a private one.** A fictional mixed Russian/English meeting of about 200 cues and four speakers labelled `Speaker 1`–`Speaker 4` is committed with an answer key: planted decisions, planted action items (each with an owner label and a key token — an invented ticket id or date), and distractors (proposals raised and rejected) that must not come back as decisions. The script scores: whether the answer validated first time, each planted item found (its key token and owner in one returned item), distractors returned, and items matching no key — read by hand and recorded as invented or legitimate. Run with `--private` on a real transcript, it prints only counts and outcomes — prompt tokens, redactions the filter made, items returned, validation — and never a word of the text; the PM's judgement is recorded as counts. Nothing from a real transcript is committed.
- **The measurement chooses the model**: of the candidates that validate on every run, the higher planted-item recall; then the lower peak memory; then the shorter median wall time; then `qwen3:8b`, named so a full tie still has an answer. Recorded with its numbers and offered by `setup`.
- **No figure is stated before it is measured.** Every number lands in this spec's Design Notes with the machine it was measured on.

**Ask First:**
- If no 16 GB Apple Silicon machine is available, the numbers are recorded on the machine at hand with its memory named, and whether 16 GB is enough stays open — confirm before anything calls it answered.
- If one call's wall time at the chosen window exceeds 300 seconds (half of CAP-3's 600), or swap grows during a run alone.
- If no candidate validates on every run.
- If a model running elsewhere cannot be told apart from a local one on the installed Ollama.
- If pm-ai's interpreter runs as x86_64 under Rosetta when the benchmark starts: stop and ask, rather than switching interpreters inside this slice.

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
| Output cap reached | `done_reason` `length` at `num_predict`, below the window | answer discarded; one warning logged naming the model, the cap and the task class, no transcript text | `OutputCapReached`, handled by the caller as an invalid answer |
| Flows | each row of the table, allowed and refused | allowed ones sent; refused ones never reach the transport | `ScopeFlowRefused` naming sources and destination |
| Frontier-eligible class | `briefing_synthesis` to the local adapter | no request made | refused naming the router |
| Deadline | the call outlasts it | connection closed; slot freed when the request ended; nothing written | `ModelTimedOut` |
| Two calls | second submitted while the first runs | runs after it | N/A |
| Shutdown mid-call | daemon stopping | connection closed; nothing written | N/A |
| `doctor` | pulled / not pulled / no answer / error, malformed or slow answer / unset | `OK` / `WARNING` + pull / `WARNING` + start, and the two server settings / `WARNING` naming what came back / `OK` + `setup`, exit code unaffected | the probe never raises |
| Benchmark `--private` | a real transcript | counts and outcomes printed; no word of text | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/ports/__init__.py:1028-1112` -- `ModelPort`; `:1057-1074` its two stated limits (provider text in `instructions`; `-> str` cannot feed the ledger); `:1095` the signature
- `pm_ai/domain/task_classes.py:55-67` -- `TaskClass`; local-only per AD-15: `transcription`, `extraction`, `classification`, `embedding`, `fuzzy_match`
- `pm_ai/domain/sanitize.py:620,712` -- `Sanitized`, `sanitize`; no Cyrillic in the filter's vocabulary
- `pm_ai/domain/disclosure.py:158` -- `assert_citation_legal`, the shape the flow table follows
- `pm_ai/models/__init__.py`, `pm_ai/models/local/__init__.py` -- empty; the adapter goes in `local/`
- `pm_ai/app/concurrency.py:67,351-419` -- `IO_POOL_SIZE`, `IOPool`; the model pool beside it; `pm_ai/app/daemon.py:583,594,689` makes, starts and shuts down the I/O pool
- `pm_ai/app/wiring.py:218-361,363` -- `Daemon` (gains `model`), `build`
- `pm_ai/core/config.py:236-269` -- `Config`; `:31-39` unknown keys refused
- `pm_ai/platform/doctor.py:782,905` -- `daemon_reachable` and `_exchange`, the daemon's loopback exchange (AD-8) — not copied: the presence check is the adapter's own method; `pm_ai/app/entry.py:1283,1414` -- `_diagnose`, where the `local model` line is appended after `transcript ingestion`, calling the adapter through the composition root
- `pm_ai/surfaces/cli/dispatch.py:2749,2451` -- `_setup`; `_setup_config`, its step 3
- `pyproject.toml:37` -- `"ollama"`, removed; `httpx` declared at the lock's 0.28.1 (`uv.lock:400`)
- `.importlinter:81-94` -- model clients confined to `pm_ai.models`; no probe in `pm_ai.platform` talks to Ollama
- `tests/architecture/test_transcript_path_boundary.py:148-162` -- the only functions allowed to take cleaned text: today exactly `ModelPort.complete`; extended deliberately, by name
- `tests/architecture/fixtures/model_port_misuse.py`, `tests/architecture/test_sanitize_boundary.py:91,402` -- `8e`'s mypy fixture; its correct calls gain `flow`, and a call without `flow` becomes a refused case
- `tests/architecture/test_domain_invariants.py:297-333,407-435` -- the router tests stay skipped (`7c`)

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/domain/model_calls.py` -- new, pure: `ScopeFlow`, `EXTERNAL`, `ModelAnswer`, the flow table and `assert_flow_legal` -- one decision for both adapters
- [x] `pm_ai/ports/__init__.py` -- `complete` widened; the four transport refusals declared (`ScopeFlowRefused` in `domain/model_calls.py`); the docstring's ledger limit rewritten as met -- the port changes once
- [x] `pm_ai/models/local/ollama.py` -- the adapter: its own `httpx.Client` (constant URL, no environment, no redirects, deadline timeout), remote-model refusal, request options, `think` by capability, local-only classes, flow check first, truncation refusals, `ModelAnswer` from the reply -- the one local client
- [x] `pm_ai/app/concurrency.py`, `pm_ai/app/wiring.py`, `pm_ai/app/daemon.py` -- model pool of one, started and stopped with the I/O pool; `Daemon.model` wired when `local_model` is set -- AD-19
- [x] `pm_ai/core/config.py`, `pm_ai/models/local/ollama.py` (the presence check), `pm_ai/surfaces/cli/dispatch.py` (`_setup`), `pm_ai/app/entry.py` -- `local_model`, name normalisation, remote models refused from `/api/show` before any prompt; setup's question; the `local model` line in `doctor` through the composition root; `OLLAMA_NO_CLOUD=1` and `launchctl setenv` named -- presence said, never pulled
- [x] `pyproject.toml` -- remove `ollama`; `httpx==0.28.1` -- no client that reads the environment
- [x] `tests/architecture/test_transcript_path_boundary.py` -- the allowed set becomes `ModelPort.complete` and the local adapter's `complete`, named one by one -- a new taker of cleaned text still fails
- [x] `tests/architecture/fixtures/model_port_misuse.py` -- `flow` on the correct calls, which return `ModelAnswer` (not `str`) so mypy stays clean; a call without it marked refused -- `8e`'s fixture follows the port
- [x] `tests/architecture/test_sanitize_boundary.py` (`test_the_port_takes_fragments_rather_than_an_assembled_prompt`) -- the parameter set becomes `{self, task_class, instructions, external, flow, shape}`, every one keyword-only -- `8e`'s fragments-not-prompt check follows the widened port
- [x] `tests/models/test_ollama_adapter.py` -- one test per matrix row over `httpx.MockTransport`, the environment rows with the variables set -- the matrix is the contract
- [x] `benchmarks/local_model.py`, `benchmarks/fixtures/mixed_ru_en.vtt`, `benchmarks/fixtures/mixed_ru_en.key.toml` -- the script (throwaway root), the authored transcript and its key, and a first extraction prompt kept with the script (`7b` re-runs it with its own) -- the measurement
- [x] `stories/8e-sanitization-binds-at-the-boundary.md` -- a dated Change Log line: the port is widened and the fixture amended by `7a` -- an earlier frozen block amended on the record
- [x] `stories/4h-first-run-setup.md` -- a dated Change Log line: setup's step 3 gains the `local_model` question (`7a`) -- an earlier frozen block amended on the record
- [x] `stories/11b-the-real-transcript-path.md` -- a dated Change Log line: its only-acceptor test is widened by name to the local adapter's `complete` (`7a`) -- an earlier frozen block amended on the record

**Acceptance Criteria:**
- Given a mock transport and `HTTPS_PROXY` and `OLLAMA_HOST` naming another host, when a call is made, then the one request the transport sees is to `127.0.0.1:11434` with no authorization header.
- Given the daemon running a model call, when a second is submitted and the shutdown begins, then the second never starts, the first's connection is closed, and the writer closes as `4r` orders it.
- Given `uv run pytest`, then everything passes, the skip count is unchanged, and no test contacts a real Ollama.

## Spec Change Log

- **2026-10-16, created** (decision of 2026-10-16: story 7 comes forward, local model first). Carries: the port widened once for both adapters; the destination rule on local calls too; Ollama on a loopback constant; `local_model` named, never pulled; fixed request options and a measured window, a full window refused; a model pool of one; the server's two limits named rather than set; Whisper out of this wave; the benchmark on the redacted fixture and on an authored transcript with a private counts-only mode; the measurement picks the model.
- **2026-10-16, review:** egress closed by construction — own `httpx` client (constant URL, `trust_env=False`, no redirects, deadline timeout, no key header), the `ollama` package dropped after reading its environment handling (verified, 0.6.2), remote (`cloud`) models refused (assumed behaviour, checked at build); a closed flow table with `EXTERNAL`; refusals declared in `ports` (AD-49); name normalisation; `think` by capability; missing counts and a `done_reason` other than `stop` refused; the slot held until the request ends; doctor's malformed answers; tests over a mock transport only; benchmark in a throwaway root, CPU architecture and characters per token recorded; selection tie order; `8e`'s fixture and the only-takers test amended; every Ollama behaviour marked verified or assumed; plain words. Depends on spine amendments: **AD-19** — the server's two limits are named by setup and doctor, not set; the model pool is one worker; **AD-12** — its limit "nothing in `pm_ai/` implements or calls `ModelPort`" is retired; **AD-1** — local model requests to `127.0.0.1:11434` are their own egress class, loopback only, carrying cleaned text; **AD-49** — the five model refusals live in `pm_ai/ports`; **Stack** — the Ollama Python client row becomes "none: `httpx` directly"; **Deferred, Local-model selection** — benchmarked without whisper.cpp, SPEC open question 266 left open. Amends `8e`'s frozen block (task above).
- **2026-10-16, review (layout):** the Change Log moved before Design Notes, as the template places it.
- **2026-10-16, review (cross-spec):** the `local_model` question placed in setup's step 3; Change Log tasks for `4h` and for `11b`'s only-acceptor test.
- **2026-10-16, spine gate:** frozen block edited (recorded here): remote models refused from `/api/show` before any prompt leaves (`remote_host`, `remote_model`, cloud tags), `OLLAMA_NO_CLOUD=1` named, a 503 refused; `launchctl setenv` and the `OLLAMA_NUM_PARALLEL` default in the limits bullet; `ScopeFlowRefused` declared in `domain` beside the flow table, the other four in `ports` (AD-49); the presence check a method of the local adapter, reached through the composition root, no Ollama client in `pm_ai.platform` (AD-1 class LM); an x86_64-under-Rosetta interpreter stops the benchmark and asks (Ask First).
- **2026-10-19, re-verified:** Code Map re-verified against `75ff91d` after 8r, 4t, 1s, 33h, 23e, 23f, 8s, 23h, 4u, 8t, 33i, 4v, 23g, 4w, 33g, 4s and 11d merged. Moved anchors corrected in `ports/__init__.py`, `concurrency.py`, `daemon.py`, `wiring.py`, `entry.py`, `dispatch.py` (with `_setup_config`, step 3, added), `.importlinter` and `test_domain_invariants.py`; every claim about existing behaviour still holds; frozen block unchanged.
- **2026-10-19, readiness (Andrei):** frozen block edited (recorded here): the cited `ports/__init__.py:998` corrected to `:1067`, where the merged slices moved it; cosmetic, as for `33i`. Tasks gained `test_sanitize_boundary.py`'s parameter-set test and the misuse fixture's `ModelAnswer` returns, which widening the port breaks. Machine: an Apple M3 Pro with 18 GB; numbers are recorded as such and whether 16 GB is enough stays open (Ask First, confirmed by Andrei). pm-ai's interpreter was `x86_64` under Rosetta (an Intel Homebrew `uv`); Andrei installs an arm64 `uv` and Ollama with both candidates pulled before the build, and the environment is rebuilt as arm64 and the suite re-run first.
- **2026-10-19, build:** every request also sends `truncate: false` — measured at build, the server otherwise shortens an over-long prompt silently and the count rule cannot see it (Design Notes). `setup`'s `local_model` question takes `none` to leave it unset, and a step 3 that changed nothing writes nothing. `doctor`'s `local model` line is `ABSENT` with no model named, as the matrix says, so `doctor` reports a machine with no local model as not healthy (exit 4). A frontier-eligible class reaching the local adapter is `NotALocalClass`, a wiring error declared in the adapter, not a fifth port refusal. `RemoteModelRefused` is declared in `ports` as a kind of `LocalModelUnavailable`. The measurement ran; two Ask First items were triggered and are recorded, not acted on.
- **2026-10-19, review patches:** the daemon wires `Daemon.model` from the `config.toml` read at composition, at start only (`refreshed` keeps it; a changed `local_model` takes effect at the next start). Closing an `httpx` client does not interrupt a request in flight, so the real transport runs on an httpcore network backend that records its sockets: the shutdown and a watchdog at the call's total deadline shut them down (`SHUT_RDWR`), and connecting has a 2-second timeout of its own; `httpcore==1.0.9` is declared, as it is now imported directly. A model job hands its write to the writer only through `ModelPool.hand_off`, refused once the shutdown has begun. The adapter's client is built on first use and closed at every daemon stop; the adapter is imported lazily so a machine without the runtime extra still composes. Setup prints `ollama pull` only when the server answered and does not list the model. Cloud tags and the pulled-model list are compared without regard to case; only Ollama's own "exceeds the available context size" 400 is `PossiblyTruncated`; a pulled model that does not list `completion` is a `WARNING`.
- **2026-10-19, done with the selection pending:** two benchmark Ask First stops were hit, so the model choice is not made. Every `llama3.1:8b` call on the redacted recording reached the 300-second deadline (a generation that does not end, diagnosed after the run), and swap grew during `qwen3:8b` runs alone. The code stands as built: `NUM_CTX` is 8,192, the benchmark measured at 16,384, every request sends `truncate: false`, no `num_predict` cap is sent, and setup offers `qwen3:8b` as the selection rule's answer, not as a decision. Four questions are marked "Needs Andrei's decision" in `deferred-work.md`: the model, the window, an output cap, and `truncate: false`. Whether 16 GB is enough stays open (this machine has 18 GB). Also, `Daemon.model` is typed as `LocalModel`, the port plus `close`, so its annotation resolves without the runtime extra.
- **2026-10-19, benchmark decisions (Andrei):** frozen block edited (recorded here). (b) The window is 16,384: a whole meeting fits one call, and `7b` does not chunk. (c) Every request sends an output cap `num_predict` (2,048, sized from the longest valid answer measured, with headroom); an answer stopped at the cap is refused as `OutputCapReached`, a new port refusal beside `PossiblyTruncated`, with a log warning naming the model, the cap and the task class and never transcript text, and the caller handles it as an invalid answer; a matrix row and its tests were added. (d) `truncate: false` is kept and named in the request list: an over-long prompt is refused, never silently shortened. (a) The model is not chosen: the benchmark is re-run in full on both candidates after a reboot with nothing else open, then the choice is made; until then it stays "Needs Andrei's decision".
- **2026-10-19, re-run (Andrei's request):** the benchmark was re-run in full on both candidates after a reboot, with the window at 16,384 and the output cap; the figures are in Design Notes. No Ask First stop triggered: swap stayed at 0 MB, no call exceeded 300 s, and `qwen3:8b` validated on every run. The selection rule answers `qwen3:8b`; the choice stays with Andrei. The cap's log line now names the task class as `task class <name>`.
- **2026-10-19, last decisions (Andrei):** frozen block edited (recorded here). (a) The local model is `qwen3:8b`, as the selection rule gives on the re-run; `setup` offers it (`SUGGESTED_LOCAL_MODEL`). Whether 16 GB is enough stays open: the runner peaked at 8,077 MB on an 18 GB Mac. (doctor) A missing local model is not a failure: with `local_model` unset, the `local model` line is `OK` and says how to set one up, and `doctor`'s exit code is unaffected; only a named model that is broken, refused or unreachable affects it. The doctor bullet and matrix row are amended (was `ABSENT` + `setup`), the unset answer is shared from `pm_ai/domain/model_calls.py` so it is the same without the runtime extra, and tests pin both exits.

## Design Notes

The port's widening is the one place `7b` and `7c` meet: `7b` calls the local adapter with `flow` from the meeting's scope to itself; `7c`'s router puts the frontier adapter behind the same signature and writes the disclosure line from `ModelAnswer`.

The context window is the main memory cost after the weights: its cache grows with `num_ctx`. So `num_ctx` is chosen from the measurement — the smallest power of two holding the fixture's measured prompt tokens plus the answer budget, if its peak memory leaves room at 16 GB — and that peak is what "is 16 GB enough" is asked about. The characters-per-token figure recorded here is what `7b` sizes its chunks with.

Choices made in review (2026-10-16): the `ollama` package is dropped rather than wrapped, because two of its defaults read the environment and closing them from outside depends on its internals; a closed flow table rather than one rule, because the unlisted pairs (one project to another, `people` to anywhere) had no answer; a full-tie order ending in a named model, so the selection rule always answers.

Not answered by this slice: whether whisper.cpp and the parsing model fit together at 16 GB (SPEC.md:266; the spine's Deferred "Local-model selection" assumed both would be benchmarked together).

Measured figures, 2026-10-19 — Apple M3 Pro (Mac15,7), 18 GB, macOS 26.6.2, Ollama 0.40.6, pm-ai's Python 3.14.7 running as arm64 (not Rosetta). The 16 GB question stays open: this is an 18 GB machine (Ask First, confirmed by Andrei). Run with `benchmarks/local_model.py --model qwen3:8b --model llama3.1:8b --num-ctx 16384`, each request as the adapter sends it (`keep_alive: 0`, temperature 0, seed 7, `truncate: false`, the answer shape as `format`). Six runs per model: three alone, three while a second pm-ai process persisted 50,000-event batches through `StorageService` in a throwaway root, in a loop (a stand-in for the daemon's writer under heavy telemetry, not the daemon process itself). Memory is `ps` RSS, sampled every 0.2 s; the session that ran the benchmark was open throughout, and swap stood at 620 MB with the pressure level at 2 (warning) before the first call.

- **Window.** The redacted recording (198 cues) is 8,619 prompt tokens on `qwen3:8b`, so it does not fit the provisional 8,192: at 8,192 the server refused it (400, "exceeds the available context size"). The smallest power of two holding it plus an answer is 16,384, and every figure below was measured there. `NUM_CTX` is 16,384 (Andrei, 2026-10-19). The figures below were measured before the output cap existed: `NUM_PREDICT` is 2,048, sized from the longest valid answer measured (314 output tokens, `qwen3:8b`, the authored meeting) with about 6.5 times that as headroom; 8,619 + 2,048 fits the window; the re-run below measured a capped call at 121–124 s, inside the 300-second deadline. Not measured against `7b`'s answer shape at its bounds.
- **Characters per token**, prompt included: 1.55 on the redacted recording (letter-redacted words — not representative of real text); 2.92 (`qwen3:8b`) and 3.06 (`llama3.1:8b`) on the authored Russian/English meeting. `7b` should size its chunks from the authored figure, not the redacted one.
- **`qwen3:8b`** — redacted recording: wall 31.4–33.7 s, cold load 1.05–3.31 s, prompt 8,619 tokens at 290–291 tokens/s, output 15 tokens at 19.9–21.9 tokens/s. Authored meeting: wall 24.1–24.6 s, cold load 2.6–2.9 s, prompt 2,703 tokens at 332–335 tokens/s, output 314 tokens at 23.1–23.9 tokens/s. Ollama peak RSS 7,132–7,457 MB; pm-ai 28–60 MB alone, 136–159 MB with the load process. Page-outs per call 31–465 alone, 272–826 loaded. Pressure level 1–2. Swap grew during calls with the model running alone: 620 → 1,130 MB (run 1, authored meeting) and 1,026 → 1,196 MB (run 3). Validated first time on all 12 calls. Recall 9 of 11 planted items on every run (missed D5, the sprint-48 onboarding decision, and A4, Speaker 1's release notes); no distractor returned; no item matching no key.
- **`llama3.1:8b`** — redacted recording: every one of the six calls reached the 300-second deadline (`ModelTimedOut`), alone and loaded; no answer, so no counts. Authored meeting: wall 22.0–22.8 s, cold load 2.3–2.6 s, prompt 2,582 tokens at 339–340 tokens/s, output 312 tokens at 24.9–26.0 tokens/s, validated first time on all six; recall 9 of 11 (the same two missed); no distractor; nothing unmatched. Ollama peak RSS 6,819–7,210 MB. Swap did not grow. Why the redacted calls time out was checked after the benchmark with one diagnostic call outside the script (same request at 16,384, plus an output cap `num_predict` 1,024, counts only): it read the prompt (8,324 tokens) in 27.5 s and then generated until the cap — 1,024 output tokens in 47.3 s, `done_reason` `length`, the answer not valid JSON. So the 300-second calls are a generation that does not end on the letter-redacted input, not a slow read; whether it ends on real text is not measured. The adapter sends no output cap, so such a generation holds the model pool's one slot until the deadline.
- **First run, the selection rule's answer:** `qwen3:8b`, the only candidate that validated on every run (`llama3.1:8b` timed out on the real-length input). `setup` offers it (`SUGGESTED_LOCAL_MODEL`). It is not chosen: Andrei wants the benchmark re-run, both candidates in full, after a reboot with nothing else open, to see whether the swap growth goes away (2026-10-19). The cap changes the request, so the figures above are re-measured then.
- **First run, Ask First triggered and not acted on:** (1) one call's wall time exceeded 300 seconds — every `llama3.1:8b` call on the redacted recording; (2) swap grew during runs alone (`qwen3:8b`, twice). Also open: whether 16 GB is enough (this machine has 18 GB). Decided by Andrei, 2026-10-19: the window is 16,384, with no chunking in `7b`; an output cap is sent; `truncate: false` is kept.
- **Re-run, 2026-10-19, after a reboot with nothing else open but the Claude app** (Andrei), the same machine and Ollama, with the window at 16,384 and the output cap (`NUM_PREDICT` 2,048) in every request; the same six runs per model (three alone, three under the stand-in load). Before the first call swap used was 0 MB, the pressure level 1 and 89 % of memory free; after the last call 20 minutes later, swap was still 0 MB and the level 1. Swap stayed 0 MB before and after every call of both models, and the pressure level sampled during every call was 1. So the first run's swap growth did not recur on a machine with nothing else open.
  - **`qwen3:8b`** — redacted recording: wall 31.2–34.1 s, cold load 1.04–3.81 s, prompt 8,619 tokens at 292–293 tokens/s, output 15 tokens at 21.7–22.4 tokens/s. Authored meeting: wall 23.0–23.6 s, cold load 1.81–2.35 s, prompt 2,703 tokens at 335–336 tokens/s, output 314 tokens at 23.7–24.2 tokens/s. Ollama peak RSS 7,508–8,077 MB; pm-ai 29–55 MB alone, 145–168 MB with the load process. Page-outs per call 0–140. Validated first time on all 12 calls. Recall 9 of 11 on every run (missed D5 and A4, as before); no distractor returned; no item matching no key.
  - **`llama3.1:8b`** — redacted recording: every one of the six calls reached the output cap (`OutputCapReached`, one warning logged each, naming the model, the cap and the task class and no text), wall 121.2–124.5 s; no counts, because the answer was refused. Authored meeting: wall 20.8–21.9 s, cold load 1.05–2.33 s, prompt 2,582 tokens at 340–341 tokens/s, output 312 tokens at 25.6–26.1 tokens/s, validated first time on all six; recall 9 of 11 on every run (found D5, missed A4 and A5); no distractor; nothing unmatched. Ollama peak RSS 7,003–7,496 MB. Page-outs per call 0–297.
  - **Ask First on the re-run: none triggered.** No call exceeded 300 s (longest 124.5 s), swap did not grow, and a candidate validated on every run. The selection rule answers `qwen3:8b` again: the only candidate that validated on every run. **Chosen by Andrei, 2026-10-19: `qwen3:8b`.** Whether 16 GB is enough stays open: the runner peaked at 8,077 MB at this window on an 18 GB machine.
- **Checked at build against Ollama 0.40.6** (the frozen block's "assumed" items): `/api/chat` with `stream: false` returns `message.content`, `done_reason`, `prompt_eval_count`, `eval_count`, `total_duration`, `load_duration`, `prompt_eval_duration`, `eval_duration`; `/api/show` lists `capabilities` (`thinking` for `qwen3:8b`, not for `llama3.1:8b`). **The server shortens an over-long prompt silently**: at a 512-token window a 2,018-token prompt came back as `prompt_eval_count` 258, `done_reason` `stop` — under the window, so the count rule could not catch it. So every request also sends `truncate: false`, with which the server refuses the prompt (400), refused here as `PossiblyTruncated`. Whether Ollama stops generating when the client disconnects is still not verified directly; after each `llama3.1:8b` call was abandoned at its deadline, the next call took its ordinary 22 s, which it could not have if a generation were still holding the server's one slot — indirect evidence only. A remote model could not be tried (no cloud model on this machine); the `remote_host`/`remote_model` refusal is tested against the documented answer shape only.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; skip count unchanged
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- With Ollama installed and both models pulled: `uv run python benchmarks/local_model.py --model qwen3:8b` and `--model llama3.1:8b` print every measure for six runs each; the figures, the machine and the chosen model are written into this spec's Design Notes.
- `pm-ai doctor` shows `local model` `OK`; with Ollama quit, `WARNING` naming how to start it.


## Suggested Review Order

**The port, widened once**

- Start here: the new signature both adapters share, and the refusals a caller catches.
  [`ports/__init__.py:1113`](../../../../pm_ai/ports/__init__.py#L1113)

- The closed flow table, checked before anything is sent.
  [`model_calls.py:135`](../../../../pm_ai/domain/model_calls.py#L135)

- What the answer carries for the ledger.
  [`model_calls.py:192`](../../../../pm_ai/domain/model_calls.py#L192)

**Nothing leaves the machine**

- Order of refusals before any request: flow, class, cloud tag, `/api/show`.
  [`ollama.py:422`](../../../../pm_ai/models/local/ollama.py#L422)

- Sockets recorded so an abort or the deadline can cut them.
  [`ollama.py:174`](../../../../pm_ai/models/local/ollama.py#L174)

- The transport rebuilt on that backend; relies on httpx 0.28.1's private `_pool`.
  [`ollama.py:240`](../../../../pm_ai/models/local/ollama.py#L240)

- Status mapping: redirects, 503, 404, the context refusal.
  [`ollama.py:503`](../../../../pm_ai/models/local/ollama.py#L503)

- Truncation refusals: missing counts, `done_reason`, the window.
  [`ollama.py:584`](../../../../pm_ai/models/local/ollama.py#L584)

- Cloud tags and names compared in `:latest` form, case folded.
  [`model_calls.py:243`](../../../../pm_ai/domain/model_calls.py#L243)

**One model job at a time (AD-19)**

- The pool of one; writes pass only through `hand_off`, closed at shutdown.
  [`concurrency.py:438`](../../../../pm_ai/app/concurrency.py#L438)

- Shutdown order: model pool, then the writer drains.
  [`daemon.py:693`](../../../../pm_ai/app/daemon.py#L693)

- The daemon's model set from `config.toml` once it is loaded.
  [`entry.py:1265`](../../../../pm_ai/app/entry.py#L1265)

- Lazy import, so commands still compose without the runtime extra.
  [`wiring.py:595`](../../../../pm_ai/app/wiring.py#L595)

**Named, never pulled**

- The presence check that `setup` and `doctor` share; it never raises.
  [`ollama.py:670`](../../../../pm_ai/models/local/ollama.py#L670)

- Setup's step-3 question and the single pull hint.
  [`dispatch.py:2473`](../../../../pm_ai/surfaces/cli/dispatch.py#L2473)

- The new key; a host key is still refused as unknown.
  [`config.py:279`](../../../../pm_ai/core/config.py#L279)

**The measurement**

- The window (16,384) and the output cap, decided by Andrei.
  [`ollama.py:106`](../../../../pm_ai/models/local/ollama.py#L106)

- The selection rule and the Ask First checks.
  [`local_model.py:467`](../../../../benchmarks/local_model.py#L467)

**Peripherals**

- One test per matrix row, over `MockTransport` and a test-local socket.
  [`test_ollama_adapter.py`](../../../../tests/models/test_ollama_adapter.py)

- Composition and the stop order.
  [`test_daemon_local_model.py`](../../../../tests/slice/test_daemon_local_model.py)

- Dependencies: `ollama` dropped, `httpx` and `httpcore` pinned.
  [`pyproject.toml:47`](../../../../pyproject.toml#L47)
