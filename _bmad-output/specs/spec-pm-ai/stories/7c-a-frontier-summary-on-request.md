---
title: 'A frontier summary of one meeting, on request and disclosed'
type: 'feature'
created: '2026-10-16'
status: 'done'
baseline_commit: 'fec0645ae3b842abb79d03b61f38b5c7d4c90b63'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The local model's summary is the default, and sometimes the PM wants a better one than an 8B model writes, or one made from a meeting too long for the local model's window, which `7b` reads in one call and refuses when it does not fit. The frontier reservation gained "a meeting summary the PM explicitly asks for" on 2026-10-16, but nothing in pm-ai can make a frontier call: there is no frontier adapter and no router, no API key is held, and the disclosure ledger has a writer nothing calls.

**Approach:** `pm-ai transcript summarize … --by claude` sends one meeting's cleaned transcript through a router to the frontier adapter, from the daemon only, and writes the answer as `7b` does, marked as made by Claude on request. Each request is disclosed before it is sent and completed after; the month's total is reported and warned on, never enforced. The API key is held in the encrypted credential file, entered by `pm-ai frontier key`.

## Boundaries & Constraints

**Always:**
- **Prerequisites:** `7a`, `7b`; the kernel's frontier-reservation line (SPEC.md Assumptions) re-derived with the 2026-10-16 amendment, done in pull request #74 (the frontier reservation gains an explicitly requested meeting summary); the spine amendments listed in the Change Log.
- **Only on the PM's explicit request.** `transcript summarize` gains `--by local|claude`, `local` when omitted. Nothing else calls the frontier: not ingestion, not a harvest, and never a fallback either way — a local failure is reported, never retried on Claude, and a frontier failure never falls back to local (AD-17).
- **A task class of its own.** `TaskClass` gains `meeting_summary`, frontier-eligible, served by the Sonnet tier (`claude-sonnet-5-5`, AD-15 as amended at the spine gate 2026-10-16; Opus tier `claude-opus-5-5`), as AD-15 assigns every class but `coaching` and `research`. `7b`'s local call stays `extraction`.
- **One router (AD-15).** `pm_ai/models/router.py` routes every `TaskClass` member: local-only classes to `7a`'s adapter, frontier-eligible ones to the frontier adapter at their tier (`coaching` and `research` on Opus, the rest on Sonnet). It checks `flow` (`7a`'s table) before any adapter is reached. A `Daemon.router` is added; `Daemon.model` stays the local model, so `offer_summaries`, the model pool's abort and the `LocalModel` slot are unchanged. With no local model named, frontier routes still work and local ones refuse with `NoLocalModel`. Building it un-skips the nine router test items in `test_domain_invariants.py`, written before the port existed (`complete(prompt=…)`, scope strings, an in-memory log, `record_spend_usd`) and rewritten to the real port — `Sanitized` fragments, `ScopeFlow`, the ledger file — each keeping its invariant; `tests/conftest.py`'s `EXPECTED_SKIPS` falls by nine.
- **The boundary is the meeting's scope, both ways (SPEC Constraints, AD-31).** The prompt holds one meeting's title and transcript and nothing else; `flow` is from that scope to that scope; the answer is written there. A personal meeting's frontier summary lands only in the personal record.
- **Nothing can reach anywhere but Anthropic's API — by construction, each with a test.** The client is built with `api_key` and `base_url` (`https://api.anthropic.com`, a constant) passed explicitly — so neither `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` nor any profile on disk is consulted (verified: `anthropic/_client.py:190-207`, 1.2.0) — `max_retries=0` (the default is 2, `_constants.py:10`), and its own `httpx2.Client` — `httpx2==2.12.0`, the lock's version, declared in the runtime extra since pm-ai now imports it directly — with `trust_env=False`, `follow_redirects=False` and a 300-second timeout (`trust_env` verified in `httpx2/_client.py:192`, 2.12.0). Server-side `fallbacks` are never sent — the parameter is opt-in (verified: `resources/beta/messages/messages.py:134,285`) — and an answer naming a model other than the one asked for is refused by name. **The SDK's own environment reads are refused, not trusted** (spine gate 2026-10-16): passing the key and base URL does not stop SDK 1.2.0 reading `ANTHROPIC_CUSTOM_HEADERS` (merged into every request, able to override the key header) or `ANTHROPIC_LOG` (logs request bodies); while either is set (SDK 1.2.0 reads no OpenTelemetry variable, so the pinned list is these two), every frontier call is refused by name, naming the variable, before any line is written. A test pins that list against the installed SDK, and another asserts no `fallbacks` field is sent.
- **The adapter.** The Tool Runner (AD-16) with no tools and `max_iterations=1` (verified parameter: `messages.py:1327`), so nothing is ever sent a second time without the PM. `7b`'s schema goes as structured output (`output_config.format`); no temperature, `top_p` or `top_k`, no prefill (the spine's API notes); `max_tokens` sized for thinking and answer together. Every stop reason but `end_turn` is refused by name before content is read — `max_tokens`, `refusal`, `pause_turn`, `compaction`, `tool_use`, `stop_sequence`, `model_context_window_exceeded` (the set verified in `types/beta/beta_stop_reason.py`, 1.2.0). The answer goes through `7b`'s validator and `sanitize`, and is written in the language most of the meeting was held in, names and terms as spoken. No retry: an answer stopped by its stop reason is refused with a frontier-specific stop-reason refusal, never `OutputCapReached`, and an invalid answer is settled as refused, so `7b`'s one retry never fires and nothing is sent a second time without the PM (AD-16). *Assumed, not verified:* that the API accepts an empty tool list through the Tool Runner, and that `output_config.format` accepts `7b`'s schema.
- **Too long is refused before sending, not chunked.** The input is estimated locally (2 characters per token, conservative) against the model's context window from the dated model table; over it, the call is refused naming both figures, and nothing is sent. No token-counting request: it would send the transcript.
- **Disclosed before it leaves (AD-31, AD-38).** The router writes the ledger lines around every frontier adapter call, so no caller can skip them; a refusal carries the usage the answer reported. A `sent` line is appended to `~/.pm-ai/disclosure.md` before the request: call id, task class `meeting_summary`, the model, contributing scope and destination both the meeting's, and the input as unknown. After the answer a `completed` line with the same call id records the input and output tokens the answer reported and the estimated cost; a failure after sending records `failed, input unknown` unless the answer reported usage. A call abandoned at its deadline or by the shutdown keeps its `sent` line. If the `sent` line cannot be written, nothing is sent. No secret and no transcript text in any line. The ledger's line grammar gains the call id and status. The call id is random (`uuid4`), never reused; a call belongs to the UTC month of its `sent` line, and lines are paired by call id across months. The monthly total sums every line that reports usage, `completed` or `failed`, and states as unknown every `sent` line with no later line and every `failed` line without usage, never counting either as zero; a line written before this slice, with no call id or status, counts as a completed call of the cost it states (AD-38, spine gate).
- **Cost is computed, never invented.** A dated table beside the adapter holds each model's price per million tokens (the spine's Cost-model note: `claude-sonnet-5-5` $2/$10 with cache hits at 0.05× input, `claude-opus-5-5` $4/$20) and context window, checked against Anthropic's pricing and models pages when this slice is built. A model with no row refuses the call. `ModelAnswer` has no cache-token fields, so no cache price is applied.
- **The budget is watched, never enforced** (AD-17; SPEC: "a monitored target, not a cap"). The job's reply states this call's tokens and cost and the month's total from `DisclosureLedger.monthly_total` against $20 (NFR-13, a constant, no setting), months counted in UTC as that method does (`disclosure_ledger.py:167-174`); past it, the reply carries a warning. No refusal, no downgrade. A malformed ledger line met while totalling is named in the reply; the summary is still written. The month's total also appears as one line on the personal dashboard (`7d`), as AD-17 asks of "briefings and CLI".
- **The key is held in the encrypted credential file**, the one the spec names for API credentials, sealed under the master key — not a keychain item of its own, so the command is `pm-ai frontier key`, not `key`. It sits under its own top-level key, which connector enrolment preserves and no connector listing reads. `frontier key` reads a secret, so it joins `TERMINAL_BOUND` (it needs a terminal and is refused by name elsewhere, as `4q` refuses secrets at the prompt). The CLI reads it at a hidden prompt — never on the command line — and hands it to the running daemon over the authenticated loopback API; the daemon checks it with the frontier adapter's own client by listing models, which bills nothing, then seals it, secret first. So every request to Anthropic leaves from the daemon, through one client. No daemon: refused, naming `pm-ai daemon run`. A failed check — network or a refused key — seals nothing, and a key already held stays until a new one checks. `setup` prints the command as a closing line after its step 4, since entering the key needs the daemon running; `doctor` gains a `frontier key` line, held or none held, contacting nothing — a missing key is not a failure: with none held the line is `OK`, saying how to add one, and `doctor`'s exit code is unaffected; only a stored key that is broken, unreadable or refused affects it (Andrei, 2026-10-20). *Assumed:* that listing models bills nothing.
- **Then `7e`'s step, as after a local summary.** For a project meeting, the frontier answer is handed to `7e` exactly as `7b`'s is; its issue-title match is a local `classification` call and never a frontier one.
- **Written as `7b` writes**, with the provenance line naming Claude, the model, "on request" and the date — no cost in the record: frontier cost lives only in the disclosure ledger (CAP-27). State row `summarized`, made by `frontier` (the value stored; the CLI's word stays `claude`), asked for. A frontier summary is replaced only by another explicit request — `--by local` included. If the ledger's `completed` line is written and the record or events then fail, the row is `failed` and the reply says the call was paid for and its answer not written.
- **The request runs on the frontier pool**, one thread of its own, as a bounded step with a 300-second deadline — never on the I/O pool and never on the model pool, so the wait for Anthropic holds neither an I/O worker nor the one local slot (AD-19, spine gate 2026-10-16). The router picks the pool by the class's side; the caller never does. Preparation ends on the I/O worker before the hand-off.
- **Its writes are fenced as `7b`'s are** (AD-37): the request takes the lease on the summary state row with its first write — revoking a running automatic job's — and every later claimed step checks it. The `sent` and `completed` ledger lines are not fenced: a paid call is always recorded.

**Ask First:**
- If SDK 1.2.0's Tool Runner, or the API, refuses an empty tool set: whether to call `messages.create` without tools, which AD-16's letter does not name.
- If a model id, price or context window on Anthropic's pages differs from the spine's when built.

**Never:** No automatic frontier call. No fallback either way. No other task class called. No personal material in a prompt bound for a project. No key on a command line, in a log or in the ledger. No hard budget stop. No Claude Agent SDK (AD-16). No retry, no resumed turn, no server-side fallback. No token-counting request. No request from the CLI process.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Request | `transcript summarize --scope project:alpha --meeting mtg_… --by claude`; key held; daemon running | `sent` line, one call, `completed` line; block marked Claude; events and proposals as `7b`; reply with tokens, cost, month total | N/A |
| Environment | `ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY`, `HTTPS_PROXY` set | request to `https://api.anthropic.com` with the sealed key, no proxy | N/A |
| SDK environment | `ANTHROPIC_CUSTOM_HEADERS` or `ANTHROPIC_LOG` set | no line, no request | refused naming the variable |
| Personal meeting | `--scope personal --by claude` | personal record only; lines name personal → personal | N/A |
| Personal into project | a flow from personal bound for a project | no line, no request | `ScopeFlowRefused` |
| Too long | estimate over the model's window | nothing sent, no line | refused naming both figures |
| No key | nothing sealed | refused before any line | names `pm-ai frontier key` |
| No daemon | `--by claude` or `frontier key` | refused | names `pm-ai daemon run` |
| Key entered | valid / refused / network down | checked and sealed / nothing sealed / nothing sealed, old key kept | named |
| Stop reasons | `max_tokens`, `refusal`, `pause_turn`, `compaction` … | nothing written; `completed` line with the usage reported | refused by name |
| Fallback model | the answer names another model | nothing written; line records it | refused by name |
| Invalid answer | breaks `7b`'s shape | nothing written; line written; no retry | `ModelAnswerRefused` |
| Output cap | stop reason `max_tokens` | nothing written; `completed` line with the usage reported; one request sent, `7b`'s retry not run | the frontier stop-reason refusal, never `OutputCapReached` |
| One send per request | an invalid or stopped answer | exactly one request sent; the row settles as refused; the PM may ask again | named in the reply |
| Failed after sending | connection drops mid-answer | `failed, input unknown` | named in the reply |
| Abandoned | deadline or shutdown mid-call | `sent` line kept | N/A |
| Ledger unwritable | `disclosure.md` refuses the `sent` line | nothing sent | named |
| Paid, not written | `completed` written, record write fails | row `failed` | reply: paid, not written |
| Month past $20 | total $21.40 | written as usual; reply warns | never refused |
| Malformed ledger line | while totalling | summary written; reply names the line | N/A |
| Unpriced model | no table row | no line, no request | named |
| Local failure | `--by local` fails | reported; Claude never called | N/A |
| Local over a frontier summary | `--by local` | replaces it (an explicit request) | N/A |
| Harvest after | record has a frontier summary | never replaced automatically | N/A |
| Language | mostly Russian meeting | written in Russian, names and terms as spoken | N/A |
| `doctor` | key held / not | `frontier key` OK / OK, none held, naming the command; exit code unaffected (Andrei, 2026-10-20) | contacts nothing; only an unreadable store is a warning |

</frozen-after-approval>

## Code Map

- `pm_ai/ports/__init__.py:1030-1133` -- `ModelPort` as `7a` widens it; the router implements it
- `pm_ai/domain/task_classes.py:55-67` -- `meeting_summary` joins
- `pm_ai/models/frontier/__init__.py` -- empty; the adapter and the model table go here
- `pm_ai/domain/disclosure.py:63-86,180` -- `DisclosureRecord`, `render_disclosure`; gains call id and status
- `pm_ai/core/disclosure_ledger.py:55-188` -- parsing and `monthly_total`, warn-only, UTC months
- `pm_ai/storage/service.py:3427-3466` -- `append_disclosure`, `read_disclosure`
- `pm_ai/core/connector_enrolment.py:331-345,1056-1079` -- `stored_credentials`, `_credentials_in`: the connector half of the sealed file; other top-level keys preserved
- `pm_ai/surfaces/cli/dispatch.py:1558-1631,1684` -- `connector add gitlab`'s hidden prompt; `:4038` `TABLE`, which gains `frontier`; `:2851` `_setup` (step 4 is `_setup_connector`, `:2900`); `:3919` `summarize_refusal` and `pm_ai/domain/meeting_summaries.py:232` `FRONTIER_SUMMARY_REFUSED`, `7b`'s refusal of `--by claude` that this slice replaces (also said at `pm_ai/app/entry.py:385` and `pm_ai/app/pipelines.py:3053`); `:4006` `TERMINAL_BOUND`
- `pm_ai/surfaces/api/app.py` -- the loopback API that carries the key to the daemon
- `tests/architecture/test_domain_invariants.py:297-333,407-435` -- the router tests; `tests/conftest.py:160` `EXPECTED_SKIPS`
- `pyproject.toml:35` -- `anthropic[mcp]==1.2.0`; read in `anthropic/_client.py`, `_constants.py`, `resources/beta/messages/messages.py`, `types/beta/beta_stop_reason.py`, and `httpx2/_client.py`
- `ARCHITECTURE-SPINE.md:319-342,882-935` -- AD-15–AD-17, the API notes, the Cost-model note

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/domain/task_classes.py` -- `MEETING_SUMMARY` -- a declared purpose for the one frontier call
- [x] `pm_ai/models/router.py` -- routes every class, checks `flow`, tiers per AD-15; writes the `sent` line before and the `completed` or `failed` line after every frontier adapter call, from the usage a refusal carries -- AD-15's one router, AD-31's every call
- [x] `pm_ai/models/frontier/anthropic.py`, `pm_ai/models/frontier/models.py` -- the client built explicitly, the Tool Runner call, stop reasons, model check; the dated price and window table -- AD-16, cost computed
- [x] `pm_ai/domain/disclosure.py`, `pm_ai/core/disclosure_ledger.py` -- call id and status on a line; totals count completed calls and name unfinished ones -- disclosed before it leaves
- [x] `pm_ai/app/pipelines.py` -- `request_summary`'s `by` (refused at `:3053` until this slice) reaches `summary_steps`, whose `_call` (`:2470`) is the call; the frontier path calls the router, skips `7b`'s one retry on an invalid or stopped answer, then `7b`'s writes; reports cost and month total -- one path, `7b`'s
- [x] `pm_ai/core/frontier_key.py` -- seal and read the key under its own top-level key of the credential file -- held where API credentials are held
- [x] `pm_ai/surfaces/cli/dispatch.py`, `pm_ai/surfaces/api/app.py`, `pm_ai/platform/doctor.py`, `pm_ai/app/entry.py` -- `--by`; `frontier key` through the daemon; setup's line; the `frontier key` probe -- the PM's controls
- [x] `pm_ai/app/wiring.py` -- a `Daemon.router` added; `Daemon.model` stays the local model -- one door, `7b`'s gating unchanged
- [x] `pm_ai/domain/meeting_summaries.py` -- `SummaryMaker` gains `frontier`; `FRONTIER_SUMMARY_REFUSED` retired -- the made-by value stored
- [x] `pm_ai/core/meeting_summary.py` -- the provenance line written and parsed for Claude as well as "the local model" -- the record names who made it
- [x] `pm_ai/app/daemon.py`, `pm_ai/app/concurrency.py` -- the frontier pool of one thread, started and shut down beside the model pool -- AD-19
- [x] `pm_ai/surfaces/cli/loopback.py` -- the client side of handing the key to the daemon -- `frontier key` through the daemon
- [x] `tests/slice/test_meeting_summary_slice.py:969` -- the test asserting `7b`'s `--by claude` refusal replaced -- the refusal is gone
- [x] `.importlinter` -- `httpx2` added to the forbidden lists beside `httpx` -- the frontier client's stack confined as the local one's is
- [x] `tests/architecture/test_domain_invariants.py`, `tests/conftest.py` -- the router tests rewritten to the real port; `EXPECTED_SKIPS` lowered by nine -- the skips become passes
- [x] `tests/architecture/test_transcript_path_boundary.py` -- the router's and the frontier adapter's `complete` added by name to the functions allowed to take cleaned text -- extended deliberately
- [x] `tests/models/test_router.py`, `tests/models/test_frontier_adapter.py`, `tests/slice/test_frontier_summary.py` -- one test per matrix row with a fake client; the environment row with the variables set -- the matrix is the contract
- [x] `stories/2i-disclosure-ledger-append.md`, `stories/2j-disclosure-ledger-reads.md` -- a dated Change Log line each: the line gains a call id and a status, totals count completed calls -- earlier frozen blocks amended on the record
- [x] `pyproject.toml` -- `httpx2==2.12.0` in the runtime extra -- imported directly, so declared
- [x] `pm_ai/surfaces/cli/dispatch.py`, `pm_ai/app/entry.py` -- `frontier key` in `TERMINAL_BOUND` (declared in `dispatch.py`, read by `pm_ai/app/prompts.py`); `--by claude` covered by `transcript summarize`'s `DAEMON_BOUNDS` entry -- routed as AD-21 declares
- [x] `pm_ai/surfaces/cli/completion.py` -- `--by` already offers `local` and `claude` (`7b`, `:118`); `frontier key` takes no argument -- `4u`'s test covers every option
- [x] `stories/4p-setup-offers-a-connector.md` -- a dated Change Log line: a closing line after step 4 names `pm-ai frontier key` (`7c`) -- an earlier frozen block amended on the record
- [x] `stories/11b-the-real-transcript-path.md` -- a dated Change Log line: its only-acceptor test is widened by name to the router's and the frontier adapter's `complete` (`7c`) -- an earlier frozen block amended on the record

**Acceptance Criteria:**
- Given a held key and a fake client answering a valid shape with 1,200 input and 300 output tokens on `claude-sonnet-5-5`, when the PM asks for a frontier summary of a project meeting, then the ledger holds a `sent` line written before the request and a `completed` line with those counts and the table's cost, both naming that project as source and destination, and the record's block names Claude and no cost.
- Given `uv run pytest`, then everything passes and the skip count is nine lower than before this slice.

## Spec Change Log

- **2026-10-16, created** (decision of 2026-10-16 and the kernel amendment adding an explicitly requested meeting summary to the frontier reservation). Carries: `--by claude` the only way in; `meeting_summary` on the Sonnet tier; the router built here, its tests rewritten; the meeting's scope as source and destination; the Tool Runner with no tools; one disclosure line per request; cost from a dated table; the $20 target warned on, never enforced; the key in the encrypted credential file.
- **2026-10-16, amended before approval** (decisions of 2026-10-16). Output in the meeting's main language; the monitored budget stands.
- **2026-10-16, review:** egress closed by construction — explicit key and base URL, no environment or profile, `max_retries=0`, own `httpx2` client without environment or redirects, 300-second timeout, `max_iterations=1`, every stop reason but `end_turn` refused, fallbacks never sent and another model refused (each SDK behaviour verified in 1.2.0 or marked assumed); no token-counting request, a local estimate refuses before sending; a `sent` line before the request and a `completed` line after, an abandoned call keeps its line; no cost in the record; `pm-ai frontier key`, checked in the daemon; the router routes every class and works without a local model; `EXPECTED_SKIPS` lowered by nine; UTC months; the paid-not-written case; malformed ledger lines named; the dashboard line moved to `7d`, and the AC depending on `7d` moved there. Depends on: the **kernel** re-derive (frontier reservation gains an explicitly requested meeting summary) — prerequisite; spine amendments **AD-15** — `meeting_summary` is frontier-eligible on the Sonnet tier, and a class added later declares its side and tier in the router's table, which a test checks against `TaskClass`; **AD-1** — frontier requests leave from the daemon only, the key check included; **AD-31/AD-38** — a disclosure line is written before a request and completed after, keyed by call id; **AD-16** — the Tool Runner with no tools and one iteration. Amends `2i`'s and `2j`'s frozen blocks (task above).
- **2026-10-16, review (layout):** the Change Log moved before Design Notes, as the template places it.
- **2026-10-16, review (cross-spec):** a frontier summary of a project meeting runs `7e`'s step, whose call stays local; `frontier key` in `TERMINAL_BOUND`; `httpx2` declared; setup's closing line; completion sources; Change Log tasks for `4p` and `11b`. Depends on spine amendments: **Stack** — a row for `httpx2` (2.12.0), now a direct dependency for the frontier client; **AD-21** — `transcript summarize --by claude` and `frontier key` are foreground administrative commands parsed from the command line (one amendment with `7b`'s and `7e`'s).
- **2026-10-16, spine gate:** frozen block edited (recorded here): the Sonnet and Opus tiers are `claude-sonnet-5-5` and `claude-opus-5-5` (AD-15), the price table Sonnet 5.5 $2/$10 (cache hits 0.05× input) and Opus 5.5 $4/$20; `ANTHROPIC_CUSTOM_HEADERS`, `ANTHROPIC_LOG` or an OpenTelemetry setting the SDK reads refuses every frontier call by name, with a matrix row and a test pinning the list, and a test that no `fallbacks` field is sent (AD-1 class F); the request runs on a frontier pool of one thread, never the I/O or model pool (AD-19); the summary row fenced by `7b`'s lease (AD-37); call ids `uuid4`, months by the `sent` line, `failed` lines with usage counted, pre-`7c` lines read as completed (AD-38).
- **2026-10-16, spec sync:** prerequisite re-pointed to SPEC.md Assumptions (done in pull request #74).
- **2026-10-19, re-verified against `main` at 811e973 (after `7a`, `7b`, `4s`–`4w`):** Code Map and task anchors moved to the current lines; `7b`'s `--by claude` refusal named as what this slice replaces; the pipeline task names `request_summary` and `summary_steps` rather than `summarize_meeting`; `TERMINAL_BOUND` is declared in `dispatch.py`, not `prompts.py`; `--by`'s completion is already `7b`'s. Frozen block unchanged.
- **2026-10-20, readiness decisions** (engineering calls settled by the coordinating session and reported to Andrei): frozen block edited (recorded here). A `Daemon.router` is added and `Daemon.model` stays the local model; the router writes the `sent` and `completed` lines around every frontier adapter call, and refusals carry the usage the answer reported; no automatic retry on the frontier path, with a frontier-specific stop-reason refusal (not `OutputCapReached`) and two matrix rows; low-stakes: the "in chunks" wording corrected to `7b`'s one call, `frontier` as the stored made-by, the pinned environment list `ANTHROPIC_CUSTOM_HEADERS` and `ANTHROPIC_LOG`, the `disclosure_ledger.py` anchor `167-174`, no cache-token fields in `ModelAnswer`. Outside it: the ledger moved from the pipeline task to the router's; tasks added for `meeting_summaries.py`, `meeting_summary.py`, `daemon.py` with `concurrency.py`, `loopback.py`, `test_meeting_summary_slice.py:969` and `.importlinter`; what Andrei provides before the build, in Design Notes.
- **2026-10-20, built (without a key):** where the build differs from the Tasks' wording. The side names (`ModelSide`) live in `pm_ai/domain/model_calls.py`, since the pipeline may not import the router; the pipeline asks the router which side a class is on. The frontier pool is a second `ModelPool` named `pm-ai-frontier`, one thread, started and shut down beside the model pool; a frontier step with no frontier pool wired is refused as `FrontierPoolMissing`, never run on the calling thread. The SDK variables are read only in `pm_ai/platform/environment.py` (its new `set_variables`), which wiring hands to the adapter. The `doctor` line is built in `pm_ai/app/entry.py` from `pm_ai/core/frontier_key.py`'s probe, not in `pm_ai/platform/doctor.py`. The key is sealed under the credential file's top-level key `frontier`. The key check uses the router's own frontier adapter on the frontier pool, after a local check that the key can be sealed. A requested Claude summary whose record is re-ingested becomes `failed`, still Claude's, its reason naming `--by claude`; it is never reset to a local automatic row. `uv.lock` gained the `httpx2==2.12.0` pin. The tests are `tests/models/test_router.py`, `tests/models/test_frontier_adapter.py`, `tests/models/test_frontier_review_fixes.py`, `tests/slice/test_frontier_summary.py`, `tests/slice/test_frontier_summary_review.py`, `tests/models/test_frontier_deadline.py` and `tests/surfaces/test_frontier_key_surfaces.py`, since pytest refuses two test modules with one basename.
- **2026-10-20, Ask First (model table):** checked against Anthropic's models and pricing pages, read 2026-10-20: `claude-sonnet-5-5` $2/$10, cache hits $0.10 (0.05×); `claude-opus-5-5` $4/$20, cache hits $0.20 (0.05×); both 1M context and 128K output, the full window at standard price. They match the spine; the item is not triggered. The pages also say a dateless model id is itself the pinned snapshot, so the answer's `model` is expected to equal the id asked for. The other Ask First item (an empty tool set) needs the key (`deferred-work.md`, story-7c section).
- **2026-10-20, review (three layers):** 18 items fixed, 0 intent or spec gaps. The main ones: one frontier call, and the key check, now end at 300 seconds in total from the send, not per network phase (the `sent` line kept, a `failed` line after it, the adapter usable for the next call); an unexpected error on the frontier call settles the row `failed` instead of leaving it waiting forever; a paid call's tokens and cost are reported even when the lease is lost; the reply's month is the `sent` line's UTC month; `breached` is unknown when the month is within $20 but has calls of unknown cost; the key check runs on the router's own adapter and the frontier pool, after a local sealability check; empty call ids and `sent` lines carrying usage are malformed; a 401 or 403 names `pm-ai frontier key`; the reply says "stored UNENCRYPTED" when encryption is off; the adapter refuses an output cap above a model's limit; a re-ingested Claude summary is never turned into a local automatic row; tests added for `build()`'s router with the SDK variables set, the loopback client, `entry`'s `frontier key`, the served endpoint, and `doctor`'s line. `doctor` exiting 4 with no key held went to Andrei (decided, next line).
- **2026-10-20, review decision (Andrei):** frozen block edited (recorded here). A missing frontier key is not a failure, as for an unset local model (2026-10-19): with no key stored, `doctor`'s `frontier key` line is `OK`, says none is held and how to add one (`pm-ai frontier key`), and the exit code is unaffected; only a stored key that is broken, unreadable or refused affects it. The key bullet and the `doctor` matrix row are amended (was `ABSENT`), and tests pin `doctor` exiting healthy with no key. Andrei chose to merge 7c now and run the key checks later, at his desk, by the steps under Verification.

## Design Notes

Choices made in review (2026-10-16):
- **The key check runs in the daemon**, not the CLI: every request to Anthropic then leaves through one client built one way, and the only thing crossing the loopback API is the key, over the authenticated channel the CLI already uses. The cost is that entering a key needs the daemon running.
- **`pm-ai frontier key`** rather than `key anthropic`: `key` is the master key's group, and the frontier key is not in the keychain.
- **Disclosure before sending**: the prompt has left the machine whether or not an answer comes back, so the line that says so cannot wait for one. Token counts are recorded only as the answer reports them; a local estimate is used only to refuse before sending, never written as a fact.
- **No count-tokens request**: it would send the transcript to Anthropic before the PM's request is honoured, a second disclosure for a figure the answer reports anyway.
- **The monthly total on the personal dashboard** (`7d`), so AD-17's "surfaces in briefings" is met by the next slice rather than owed.

Before the build, Andrei provides (2026-10-20): an Anthropic API key from a Console account with billing or credits, and an enrolled master key (the frontier key is sealed under it); optionally a spend limit set in the Console, which pm-ai never enforces. The two Ask First items stand.

Built without a key (Andrei, 2026-10-20): the build starts before the key arrives. Every test uses fakes or a mock `httpx2` transport; nothing contacts `api.anthropic.com`, and no test reads or writes the real keychain or the real `~/.pm-ai` (a temporary home only). Three things only the real API can settle stay open as "Needs Andrei's decision / needs the key", and do not block the build: whether the API accepts an empty tool set through the Tool Runner; whether the model ids, prices and context windows on Anthropic's pages match the spine (the public pages may be read, no API called); whether `output_config.format` accepts `7b`'s schema with `minLength`, `maxLength` and `maxItems`. The manual steps for when the key arrives are under Verification.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; nine fewer skips
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- With a real key in a throwaway home and the daemon running: `pm-ai frontier key`, then `pm-ai transcript summarize --scope personal --meeting <id> --by claude` on the authored fixture; two new lines with one call id in `~/.pm-ai/disclosure.md`, the reply's cost equal to the `completed` line's.

**When the key arrives (Andrei; not yet run, 2026-10-20).** This is the one real-API check; it costs about one cent. Run each step in a fresh terminal, from the repository, with `uv` on PATH.
1. From the project folder (every command below runs there, as `.venv/bin/pm-ai`, never through `uv`, because `uv` must not run with `HOME` changed), make a throwaway home for this shell only: `export PMAI_CHECK="$(mktemp -d)"; echo "$PMAI_CHECK"; export HOME="$PMAI_CHECK"`, then `unset ANTHROPIC_LOG ANTHROPIC_CUSTOM_HEADERS`. The macOS keychain is shared with your real home, so the master key you already enrolled is found there; setup never overwrites it.
2. `.venv/bin/pm-ai setup`: answer as on a new machine, with `none` for the local model and no connector. Expected: step 1 says the master key is already enrolled, and the last line before the report names `pm-ai frontier key` and `pm-ai daemon run`.
3. In a second terminal, `export HOME=<the path step 1 echoed>`, then `.venv/bin/pm-ai daemon run`. Leave it running.
4. `.venv/bin/pm-ai frontier key`, then paste the key at the hidden prompt. Expected: `the Anthropic API key was checked with Anthropic and sealed in ~/.pm-ai/private/config.json; …`. A refused key or no network says so and seals nothing. `.venv/bin/pm-ai doctor` then shows `[     ok] frontier key`.
5. `.venv/bin/pm-ai transcript drop <a short transcript you write yourself> --scope personal`, answering its questions, and note the `mtg_…` meeting id its reply names. Use no real meeting.
6. `.venv/bin/pm-ai transcript summarize --scope personal --meeting <id> --by claude`. Expected:
   - the reply says the summary was made, then `This call: N input and M output tokens, estimated $0.00xx.` and `This month (YYYY-MM, UTC): $0.00 across 1 call(s), against the $20.00 target.`;
   - `$HOME/.pm-ai/disclosure.md` holds two new lines with one `call=` id: `status=sent` with input unknown, then `status=completed` with the same N, M and cost, both naming `personal` as source and destination;
   - the record's `## Summary` ends with a line naming Claude `claude-sonnet-5-5`, on request, with today's date, and no cost.
7. What a failure at step 6 means. Each is reported by name, with nothing written and the `completed` or `failed` line recorded:
   - an error about `tools` or an empty tool list: the API refuses an empty tool set through the Tool Runner (Ask First 1);
   - an error about the schema, `minLength`, `maxLength` or `maxItems`: `output_config.format` does not accept `7b`'s schema;
   - `FrontierModelMismatch`: the answer named a model id other than `claude-sonnet-5-5`;
   - a charge for step 4 on the Console's usage page: listing models is billed.
   Send the reply and the two ledger lines, with the transcript text removed, to the coordinating session.
8. Stop the daemon (Ctrl-C), remove the throwaway home with `rm -rf "$PMAI_CHECK"` in the first terminal, then close both terminals. The key sealed there goes with it; enter it again in your real home with `pm-ai frontier key`.

## Suggested Review Order

**The router: one door, the ledger around every frontier call**

- Entry point: checks `flow`, picks the side and tier, writes `sent` before the adapter is reached.
  [`router.py:188`](../../../../pm_ai/models/router.py#L188)

- The `sent` line, written before anything leaves; unwritable means nothing is sent.
  [`router.py:292`](../../../../pm_ai/models/router.py#L292)

- The `completed` or `failed` line for a refusal, from the usage it carries.
  [`router.py:323`](../../../../pm_ai/models/router.py#L323)

**The adapter: nothing reaches anywhere but Anthropic**

- The refused SDK variables, the pinned list.
  [`anthropic.py:102`](../../../../pm_ai/models/frontier/anthropic.py#L102)

- The client: explicit key and base URL, own `httpx2` client, no environment, no retries.
  [`anthropic.py:300`](../../../../pm_ai/models/frontier/anthropic.py#L300)

- The one Tool Runner call: no tools, one iteration, structured output.
  [`anthropic.py:408`](../../../../pm_ai/models/frontier/anthropic.py#L408)

- Every stop reason but `end_turn`, and another model, refused by name.
  [`anthropic.py:508`](../../../../pm_ai/models/frontier/anthropic.py#L508)

- One call's total 300-second deadline, closing only that call's transport.
  [`anthropic.py:153`](../../../../pm_ai/models/frontier/anthropic.py#L153)

- The key check, on the same client, listing models.
  [`anthropic.py:459`](../../../../pm_ai/models/frontier/anthropic.py#L459)

- The dated price and window table; the local length estimate.
  [`models.py:41`](../../../../pm_ai/models/frontier/models.py#L41)

**The summary job: `7b`'s path with a frontier call**

- The frontier call on the frontier pool; no retry; unexpected errors settle `failed`.
  [`pipelines.py:2582`](../../../../pm_ai/app/pipelines.py#L2582)

- The reply's tokens, cost and month total, warned on, never enforced.
  [`pipelines.py:2621`](../../../../pm_ai/app/pipelines.py#L2621)

- Where the frontier branch joins `7b`'s writes and `7e`'s step.
  [`pipelines.py:2823`](../../../../pm_ai/app/pipelines.py#L2823)

- A re-ingested Claude summary is never turned into a local automatic row.
  [`service.py:4823`](../../../../pm_ai/storage/service.py#L4823)

**The ledger: call ids, statuses, totals by call**

- The line gains call id and status; empty ids and `sent` lines with usage refused.
  [`disclosure.py:107`](../../../../pm_ai/domain/disclosure.py#L107)

- Totals pair lines by call; unfinished calls are unknown, never zero.
  [`disclosure_ledger.py:233`](../../../../pm_ai/core/disclosure_ledger.py#L233)

- Past the target is known only when the unknowns cannot change it.
  [`disclosure_ledger.py:194`](../../../../pm_ai/core/disclosure_ledger.py#L194)

**The key and the PM's controls**

- The daemon checks, after a local sealability check, then seals.
  [`prompts.py:499`](../../../../pm_ai/app/prompts.py#L499)

- Sealed under its own top-level key; the doctor probe, contacting nothing.
  [`frontier_key.py:56`](../../../../pm_ai/core/frontier_key.py#L56)

- The CLI: hidden prompt, terminal-bound, refused without a daemon.
  [`dispatch.py:4028`](../../../../pm_ai/surfaces/cli/dispatch.py#L4028)

- The authenticated loopback endpoint carrying the key.
  [`app.py:73`](../../../../pm_ai/surfaces/api/app.py#L73)

- The client side of the hand-off.
  [`loopback.py:289`](../../../../pm_ai/surfaces/cli/loopback.py#L289)

**Wiring and pools**

- The frontier pool of one thread, beside the model pool; the key check on it.
  [`daemon.py:592`](../../../../pm_ai/app/daemon.py#L592)

- No frontier pool wired is a named fault, never the calling thread.
  [`daemon.py:761`](../../../../pm_ai/app/daemon.py#L761)

- `Daemon.router` built; the adapter given only the refused variables.
  [`wiring.py:705`](../../../../pm_ai/app/wiring.py#L705)

- The made-by value stored, and the provenance line naming Claude.
  [`meeting_summaries.py:135`](../../../../pm_ai/domain/meeting_summaries.py#L135)
  [`meeting_summary.py:568`](../../../../pm_ai/core/meeting_summary.py#L568)

**Peripherals**

- The class and its side; `httpx2` declared and confined.
  [`task_classes.py:66`](../../../../pm_ai/domain/task_classes.py#L66)
  [`pyproject.toml:52`](../../../../pyproject.toml#L52)
  [`.importlinter:45`](../../../../.importlinter#L45)

- The skip count, nine lower.
  [`conftest.py:186`](../../../../tests/conftest.py#L186)

- The matrix, row by row.
  [`test_frontier_summary.py`](../../../../tests/slice/test_frontier_summary.py)
  [`test_frontier_adapter.py`](../../../../tests/models/test_frontier_adapter.py)
  [`test_router.py`](../../../../tests/models/test_router.py)
