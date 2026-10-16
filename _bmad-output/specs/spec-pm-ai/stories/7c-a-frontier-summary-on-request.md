---
title: 'A frontier summary of one meeting, on request and disclosed'
type: 'feature'
created: '2026-10-16'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The local model's summary is the default, and sometimes the PM wants a better one than an 8B model writes, or one made from the whole meeting in a single reading rather than in chunks. The frontier reservation gained "a meeting summary the PM explicitly asks for" on 2026-10-16, but nothing in pm-ai can make a frontier call: there is no frontier adapter and no router, no API key is held, and the disclosure ledger has a writer nothing calls.

**Approach:** `pm-ai transcript summarize … --by claude` sends one meeting's cleaned transcript through a router to the frontier adapter, from the daemon only, and writes the answer as `7b` does, marked as made by Claude on request. Each request is disclosed before it is sent and completed after; the month's total is reported and warned on, never enforced. The API key is held in the encrypted credential file, entered by `pm-ai frontier key`.

## Boundaries & Constraints

**Always:**
- **Prerequisites:** `7a`, `7b`; the kernel's Constraints line re-derived with the 2026-10-16 amendment (the frontier reservation gains an explicitly requested meeting summary) — it is in the memlog and not yet in `SPEC.md`; the spine amendments listed in the Change Log.
- **Only on the PM's explicit request.** `transcript summarize` gains `--by local|claude`, `local` when omitted. Nothing else calls the frontier: not ingestion, not a harvest, and never a fallback either way — a local failure is reported, never retried on Claude, and a frontier failure never falls back to local (AD-17).
- **A task class of its own.** `TaskClass` gains `meeting_summary`, frontier-eligible, served by the Sonnet tier, as AD-15 assigns every class but `coaching` and `research`. `7b`'s local call stays `extraction`.
- **One router (AD-15).** `pm_ai/models/router.py` routes every `TaskClass` member: local-only classes to `7a`'s adapter, frontier-eligible ones to the frontier adapter at their tier (`coaching` and `research` on Opus, the rest on Sonnet). It checks `flow` (`7a`'s table) before any adapter is reached. `Daemon.model` becomes the router; with no local model named, frontier routes still work and local ones refuse with `NoLocalModel`. Building it un-skips the nine router test items in `test_domain_invariants.py`, written before the port existed (`complete(prompt=…)`, scope strings, an in-memory log, `record_spend_usd`) and rewritten to the real port — `Sanitized` fragments, `ScopeFlow`, the ledger file — each keeping its invariant; `tests/conftest.py`'s `EXPECTED_SKIPS` falls by nine.
- **The boundary is the meeting's scope, both ways (SPEC Constraints, AD-31).** The prompt holds one meeting's title and transcript and nothing else; `flow` is from that scope to that scope; the answer is written there. A personal meeting's frontier summary lands only in the personal record.
- **Nothing can reach anywhere but Anthropic's API — by construction, each with a test.** The client is built with `api_key` and `base_url` (`https://api.anthropic.com`, a constant) passed explicitly — so neither `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` nor any profile on disk is consulted (verified: `anthropic/_client.py:190-207`, 1.2.0) — `max_retries=0` (the default is 2, `_constants.py:10`), and its own `httpx2.Client` — `httpx2==2.12.0`, the lock's version, declared in the runtime extra since pm-ai now imports it directly — with `trust_env=False`, `follow_redirects=False` and a 300-second timeout (`trust_env` verified in `httpx2/_client.py:192`, 2.12.0). Server-side `fallbacks` are never sent — the parameter is opt-in (verified: `resources/beta/messages/messages.py:134,285`) — and an answer naming a model other than the one asked for is refused by name.
- **The adapter.** The Tool Runner (AD-16) with no tools and `max_iterations=1` (verified parameter: `messages.py:1327`), so nothing is ever sent a second time without the PM. `7b`'s schema goes as structured output (`output_config.format`); no temperature, `top_p` or `top_k`, no prefill (the spine's API notes); `max_tokens` sized for thinking and answer together. Every stop reason but `end_turn` is refused by name before content is read — `max_tokens`, `refusal`, `pause_turn`, `compaction`, `tool_use`, `stop_sequence`, `model_context_window_exceeded` (the set verified in `types/beta/beta_stop_reason.py`, 1.2.0). The answer goes through `7b`'s validator and `sanitize`, and is written in the language most of the meeting was held in, names and terms as spoken. No retry. *Assumed, not verified:* that the API accepts an empty tool list through the Tool Runner, and that `output_config.format` accepts `7b`'s schema.
- **Too long is refused before sending, not chunked.** The input is estimated locally (2 characters per token, conservative) against the model's context window from the dated model table; over it, the call is refused naming both figures, and nothing is sent. No token-counting request: it would send the transcript.
- **Disclosed before it leaves (AD-31, AD-38).** A `sent` line is appended to `~/.pm-ai/disclosure.md` before the request: call id, task class `meeting_summary`, the model, contributing scope and destination both the meeting's, and the input as unknown. After the answer a `completed` line with the same call id records the input and output tokens the answer reported and the estimated cost; a failure after sending records `failed, input unknown` unless the answer reported usage. A call abandoned at its deadline or by the shutdown keeps its `sent` line. If the `sent` line cannot be written, nothing is sent. No secret and no transcript text in any line. The ledger's line grammar gains the call id and status, and the monthly total counts completed calls and states sent-but-unfinished ones as calls of unknown cost.
- **Cost is computed, never invented.** A dated table beside the adapter holds each model's price per million tokens (the spine's Cost-model note: Sonnet 5 $2/$10, Opus 5 $5/$25) and context window, checked against Anthropic's pricing and models pages when this slice is built. A model with no row refuses the call.
- **The budget is watched, never enforced** (AD-17; SPEC: "a monitored target, not a cap"). The job's reply states this call's tokens and cost and the month's total from `DisclosureLedger.monthly_total` against $20 (NFR-13, a constant, no setting), months counted in UTC as that method does (`disclosure_ledger.py:166-176`); past it, the reply carries a warning. No refusal, no downgrade. A malformed ledger line met while totalling is named in the reply; the summary is still written. The month's total also appears as one line on the personal dashboard (`7d`), as AD-17 asks of "briefings and CLI".
- **The key is held in the encrypted credential file**, the one the spec names for API credentials, sealed under the master key — not a keychain item of its own, so the command is `pm-ai frontier key`, not `key`. It sits under its own top-level key, which connector enrolment preserves and no connector listing reads. `frontier key` reads a secret, so it joins `TERMINAL_BOUND` (it needs a terminal and is refused by name elsewhere, as `4q` refuses secrets at the prompt). The CLI reads it at a hidden prompt — never on the command line — and hands it to the running daemon over the authenticated loopback API; the daemon checks it with the frontier adapter's own client by listing models, which bills nothing, then seals it, secret first. So every request to Anthropic leaves from the daemon, through one client. No daemon: refused, naming `pm-ai daemon run`. A failed check — network or a refused key — seals nothing, and a key already held stays until a new one checks. `setup` prints the command as a closing line after its step 4, since entering the key needs the daemon running; `doctor` gains a `frontier key` line, held or `ABSENT`, contacting nothing. *Assumed:* that listing models bills nothing.
- **Then `7e`'s step, as after a local summary.** For a project meeting, the frontier answer is handed to `7e` exactly as `7b`'s is; its issue-title match is a local `classification` call and never a frontier one.
- **Written as `7b` writes**, with the provenance line naming Claude, the model, "on request" and the date — no cost in the record: frontier cost lives only in the disclosure ledger (CAP-27). State row `summarized`, made by `frontier`, asked for. A frontier summary is replaced only by another explicit request — `--by local` included. If the ledger's `completed` line is written and the record or events then fail, the row is `failed` and the reply says the call was paid for and its answer not written.
- **The request runs on the I/O pool** as a bounded step with a 300-second deadline — it is network waiting, not local memory.

**Ask First:**
- If SDK 1.2.0's Tool Runner, or the API, refuses an empty tool set: whether to call `messages.create` without tools, which AD-16's letter does not name.
- If a model id, price or context window on Anthropic's pages differs from the spine's when built.

**Never:** No automatic frontier call. No fallback either way. No other task class called. No personal material in a prompt bound for a project. No key on a command line, in a log or in the ledger. No hard budget stop. No Claude Agent SDK (AD-16). No retry, no resumed turn, no server-side fallback. No token-counting request. No request from the CLI process.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Request | `transcript summarize --scope project:alpha --meeting mtg_… --by claude`; key held; daemon running | `sent` line, one call, `completed` line; block marked Claude; events and proposals as `7b`; reply with tokens, cost, month total | N/A |
| Environment | `ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY`, `HTTPS_PROXY` set | request to `https://api.anthropic.com` with the sealed key, no proxy | N/A |
| Personal meeting | `--scope personal --by claude` | personal record only; lines name personal → personal | N/A |
| Personal into project | a flow from personal bound for a project | no line, no request | `ScopeFlowRefused` |
| Too long | estimate over the model's window | nothing sent, no line | refused naming both figures |
| No key | nothing sealed | refused before any line | names `pm-ai frontier key` |
| No daemon | `--by claude` or `frontier key` | refused | names `pm-ai daemon run` |
| Key entered | valid / refused / network down | checked and sealed / nothing sealed / nothing sealed, old key kept | named |
| Stop reasons | `max_tokens`, `refusal`, `pause_turn`, `compaction` … | nothing written; `completed` line with the usage reported | refused by name |
| Fallback model | the answer names another model | nothing written; line records it | refused by name |
| Invalid answer | breaks `7b`'s shape | nothing written; line written; no retry | `ModelAnswerRefused` |
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
| `doctor` | key held / not | `frontier key` OK / `ABSENT` naming the command | contacts nothing |

</frozen-after-approval>

## Code Map

- `pm_ai/ports/__init__.py:963-1046` -- `ModelPort` as `7a` widens it; the router implements it
- `pm_ai/domain/task_classes.py:55-67` -- `meeting_summary` joins
- `pm_ai/models/frontier/__init__.py` -- empty; the adapter and the model table go here
- `pm_ai/domain/disclosure.py:63-86,180` -- `DisclosureRecord`, `render_disclosure`; gains call id and status
- `pm_ai/core/disclosure_ledger.py:55-188` -- parsing and `monthly_total`, warn-only, UTC months
- `pm_ai/storage/service.py:2518-2548` -- `append_disclosure`, `read_disclosure`
- `pm_ai/core/connector_enrolment.py:298-312,886-904` -- the connector half of the sealed file; other top-level keys preserved
- `pm_ai/surfaces/cli/dispatch.py:1235-1300,1354` -- `connector add gitlab`'s hidden prompt; `:2793` `TABLE`, which gains `frontier`; `:2320` `_setup`
- `pm_ai/surfaces/api/app.py` -- the loopback API that carries the key to the daemon
- `tests/architecture/test_domain_invariants.py:295-345,405-436` -- the router tests; `tests/conftest.py:135` `EXPECTED_SKIPS`
- `pyproject.toml:35` -- `anthropic[mcp]==1.2.0`; read in `anthropic/_client.py`, `_constants.py`, `resources/beta/messages/messages.py`, `types/beta/beta_stop_reason.py`, and `httpx2/_client.py`
- `ARCHITECTURE-SPINE.md:298-317,837-877` -- AD-15–AD-17, the API notes, the Cost-model note

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/task_classes.py` -- `MEETING_SUMMARY` -- a declared purpose for the one frontier call
- [ ] `pm_ai/models/router.py` -- routes every class, checks `flow`, tiers per AD-15 -- AD-15's one router
- [ ] `pm_ai/models/frontier/anthropic.py`, `pm_ai/models/frontier/models.py` -- the client built explicitly, the Tool Runner call, stop reasons, model check; the dated price and window table -- AD-16, cost computed
- [ ] `pm_ai/domain/disclosure.py`, `pm_ai/core/disclosure_ledger.py` -- call id and status on a line; totals count completed calls and name unfinished ones -- disclosed before it leaves
- [ ] `pm_ai/app/pipelines.py` -- `summarize_meeting` takes who makes it; the frontier path writes `sent`, calls, writes `completed`, then `7b`'s writes; reports cost and month total -- one path, `7b`'s
- [ ] `pm_ai/core/frontier_key.py` -- seal and read the key under its own top-level key of the credential file -- held where API credentials are held
- [ ] `pm_ai/surfaces/cli/dispatch.py`, `pm_ai/surfaces/api/app.py`, `pm_ai/platform/doctor.py`, `pm_ai/app/entry.py` -- `--by`; `frontier key` through the daemon; setup's line; the `frontier key` probe -- the PM's controls
- [ ] `pm_ai/app/wiring.py` -- `Daemon.model` is the router -- one door
- [ ] `tests/architecture/test_domain_invariants.py`, `tests/conftest.py` -- the router tests rewritten to the real port; `EXPECTED_SKIPS` lowered by nine -- the skips become passes
- [ ] `tests/architecture/test_transcript_path_boundary.py` -- the router's and the frontier adapter's `complete` added by name to the functions allowed to take cleaned text -- extended deliberately
- [ ] `tests/models/test_router.py`, `tests/models/test_frontier_adapter.py`, `tests/slice/test_frontier_summary.py` -- one test per matrix row with a fake client; the environment row with the variables set -- the matrix is the contract
- [ ] `stories/2i-disclosure-ledger-append.md`, `stories/2j-disclosure-ledger-reads.md` -- a dated Change Log line each: the line gains a call id and a status, totals count completed calls -- earlier frozen blocks amended on the record
- [ ] `pyproject.toml` -- `httpx2==2.12.0` in the runtime extra -- imported directly, so declared
- [ ] `pm_ai/app/prompts.py`, `pm_ai/app/entry.py` -- `frontier key` in `TERMINAL_BOUND`; `--by claude` covered by `transcript summarize`'s `DAEMON_BOUNDS` entry -- routed as AD-21 declares
- [ ] `pm_ai/surfaces/cli/completion.py` -- `--by` offers `local` and `claude`; `frontier key` takes no argument -- `4u`'s test covers every option
- [ ] `stories/4p-setup-offers-a-connector.md` -- a dated Change Log line: a closing line after step 4 names `pm-ai frontier key` (`7c`) -- an earlier frozen block amended on the record
- [ ] `stories/11b-the-real-transcript-path.md` -- a dated Change Log line: its only-acceptor test is widened by name to the router's and the frontier adapter's `complete` (`7c`) -- an earlier frozen block amended on the record

**Acceptance Criteria:**
- Given a held key and a fake client answering a valid shape with 1,200 input and 300 output tokens on Sonnet 5, when the PM asks for a frontier summary of a project meeting, then the ledger holds a `sent` line written before the request and a `completed` line with those counts and the table's cost, both naming that project as source and destination, and the record's block names Claude and no cost.
- Given `uv run pytest`, then everything passes and the skip count is nine lower than before this slice.

## Spec Change Log

- **2026-10-16, created** (decision of 2026-10-16 and the kernel amendment adding an explicitly requested meeting summary to the frontier reservation). Carries: `--by claude` the only way in; `meeting_summary` on the Sonnet tier; the router built here, its tests rewritten; the meeting's scope as source and destination; the Tool Runner with no tools; one disclosure line per request; cost from a dated table; the $20 target warned on, never enforced; the key in the encrypted credential file.
- **2026-10-16, amended before approval** (decisions of 2026-10-16). Output in the meeting's main language; the monitored budget stands.
- **2026-10-16, review:** egress closed by construction — explicit key and base URL, no environment or profile, `max_retries=0`, own `httpx2` client without environment or redirects, 300-second timeout, `max_iterations=1`, every stop reason but `end_turn` refused, fallbacks never sent and another model refused (each SDK behaviour verified in 1.2.0 or marked assumed); no token-counting request, a local estimate refuses before sending; a `sent` line before the request and a `completed` line after, an abandoned call keeps its line; no cost in the record; `pm-ai frontier key`, checked in the daemon; the router routes every class and works without a local model; `EXPECTED_SKIPS` lowered by nine; UTC months; the paid-not-written case; malformed ledger lines named; the dashboard line moved to `7d`, and the AC depending on `7d` moved there. Depends on: the **kernel** re-derive (frontier reservation gains an explicitly requested meeting summary) — prerequisite; spine amendments **AD-15** — `meeting_summary` is frontier-eligible on the Sonnet tier, and a class added later declares its side and tier in the router's table, which a test checks against `TaskClass`; **AD-1** — frontier requests leave from the daemon only, the key check included; **AD-31/AD-38** — a disclosure line is written before a request and completed after, keyed by call id; **AD-16** — the Tool Runner with no tools and one iteration. Amends `2i`'s and `2j`'s frozen blocks (task above).
- **2026-10-16, review (layout):** the Change Log moved before Design Notes, as the template places it.
- **2026-10-16, review (cross-spec):** a frontier summary of a project meeting runs `7e`'s step, whose call stays local; `frontier key` in `TERMINAL_BOUND`; `httpx2` declared; setup's closing line; completion sources; Change Log tasks for `4p` and `11b`. Depends on spine amendments: **Stack** — a row for `httpx2` (2.12.0), now a direct dependency for the frontier client; **AD-21** — `transcript summarize --by claude` and `frontier key` are foreground administrative commands parsed from the command line (one amendment with `7b`'s and `7e`'s).

## Design Notes

Choices made in review (2026-10-16):
- **The key check runs in the daemon**, not the CLI: every request to Anthropic then leaves through one client built one way, and the only thing crossing the loopback API is the key, over the authenticated channel the CLI already uses. The cost is that entering a key needs the daemon running.
- **`pm-ai frontier key`** rather than `key anthropic`: `key` is the master key's group, and the frontier key is not in the keychain.
- **Disclosure before sending**: the prompt has left the machine whether or not an answer comes back, so the line that says so cannot wait for one. Token counts are recorded only as the answer reports them; a local estimate is used only to refuse before sending, never written as a fact.
- **No count-tokens request**: it would send the transcript to Anthropic before the PM's request is honoured, a second disclosure for a figure the answer reports anyway.
- **The monthly total on the personal dashboard** (`7d`), so AD-17's "surfaces in briefings" is met by the next slice rather than owed.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; nine fewer skips
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- With a real key in a throwaway home and the daemon running: `pm-ai frontier key`, then `pm-ai transcript summarize --scope personal --meeting <id> --by claude` on the authored fixture; two new lines with one call id in `~/.pm-ai/disclosure.md`, the reply's cost equal to the `completed` line's.

