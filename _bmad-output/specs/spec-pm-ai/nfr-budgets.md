# Non-Functional Budgets — pm-ai

Companion to `SPEC.md`. Cross-cutting budgets every capability is built against. Storage tiers, encryption detail, and retention mechanics are in `storage-contract.md`; the design-bending rules among these also appear as kernel Constraints.

## Performance & latency

| Id | Budget |
| --- | --- |
| **NFR-01** | Voice notes under 30 seconds are transcribed and sanitized by the local Whisper pipeline within **10 seconds** of receipt. |
| **NFR-02** | Full round trip from a 20-second voice note to rendered, context-enriched draft review cards must not exceed **45 seconds**. |
| **NFR-03** | Meeting transcripts are parsed, sanitized, anchors and commands extracted, Work Item state updated, and research tasks queued within **600 seconds** of meeting completion. |
| **NFR-04** | Local database and vector **retrieval** returns within **50–150 ms**. Complex multi-source deep inquiries and pre-meeting briefings complete within **60 seconds** of trigger. |
| **NFR-05** | Transcript-triggered background research synthesizes findings and dispatches email or Work Item follow-ups within **15 minutes** of meeting conclusion. |
| **NFR-06** | On-demand missed-meeting download, sanitization, and full dual-authorization extraction render a summary card within **300 seconds** of invocation. |

The 50–150 ms and 60-second figures describe **different operations**, not a contradiction: retrieval has no model in the path, synthesis does and is always asynchronous.

## Security, privacy & data sovereignty

| Id | Requirement |
| --- | --- |
| **NFR-07** | Sovereign personal files are never indexed into or committed to project repositories. Automated pre-commit hooks verify the private enclaves are gitignored. |
| **NFR-08** | Encryption applies to a **defined set** at AES-256 with 600 permissions, not to all local state — Markdown is plaintext by design and derived state is rebuildable. Master key in the OS keychain; raw key export is the supported migration path; the debug disable flag is never the fresh-install default and must warn on console and in the event log while active. All inbound telemetry passes the Input Sanitization Module. Full set in `storage-contract.md`. |
| **NFR-09** | Raw transcripts in the owning scope's plaintext, gitignored `transcripts/` are retained a default **30 days** (configurable) and purged automatically only after verified conversion into summaries, Work Item updates, decision logs, and pruned indexes. |

## Reliability, offline resilience & hardware

| Id | Requirement |
| --- | --- |
| **NFR-10** | On network disruption, incoming audio notes, CLI commands, and state actions buffer in the operational store (plaintext at 600; the encrypted set is two files, neither a database) and replay sequentially without data loss on reconnection. |
| **NFR-11** | Recovery is **tier-scoped**: Truth and Operational state both survive and are both declared backup targets — a target list, since no backup mechanism ships in v1 (deferred 2026-09-03); only Derived state is disposable and rebuilds with zero loss. Operational state is never a rebuild target, and restoring it opens a re-execution window the CLI must warn about. |
| **NFR-12** | Local extraction, parsing, and transcription run on a quantized **8B-class** open-weight instruct model at `Q4_K_M` plus Whisper `small.en`. Minimum hardware **16GB RAM on Apple Silicon**; v1 is macOS-only, so the CUDA baseline is deferred alongside Linux. Models above 8B-class are out of scope for v1 — they cannot run concurrently with transcription at the 16GB baseline without swap thrashing. |
| **NFR-14** | The daemon binds **loopback only**, exposing zero public HTTP or WebSocket ports. Telegram runs over **outbound HTTPS long-polling** authenticated by paired user-IDs. Webhooks are prohibited: they require the public endpoint this same requirement forbids. |

*(There is no NFR-13 in this group — it is the cost target below, numbered as in the source.)*

### Local model use, as wave 3 builds it (2026-10-16, stories 7a and 7b)

How NFR-12 is held when the local model first runs. Every figure below is a bound the specs set; none is a measurement — 7a and 7b record measured figures, with the machine, when built.

- **Loopback only, by construction.** Ollama is reached at `http://127.0.0.1:11434`, a constant: no host setting, no proxy, no redirect, no authorization header. A model whose tag contains `cloud`, or that the server reports as running elsewhere, is refused. pm-ai names the model in `config.toml` (`local_model`) and never pulls one.
- **One heavy model job at a time**, on a model pool of one worker, never on the loop, the writer thread or an I/O worker; every request sends `keep_alive: 0`, temperature 0 and a fixed seed, so the model is unloaded between calls.
- **The window and the model are chosen by measurement.** `num_ctx` is 8,192 until 7a's benchmark sets it; the model is picked between `llama3.1:8b` and `qwen3:8b` at `Q4_K_M` by validation, then recall on an authored transcript, then peak memory, then wall time. An answer that reaches the window or is cut short is refused, never used.
- **Per summary:** each model call has 300 seconds; a long transcript is split at speaker turns and merged, never refused for length; one retry per call; at most 3 meetings offered per harvest, most recent first, none whose start is more than 30 days old (its transcript is purged at 30 days, NFR-09).
- **Not claimed in wave 3:** NFR-03's 600 seconds from meeting end — pm-ai observes no meeting end yet and reads transcripts at the harvest. 7b measures ingestion-to-written and request-to-written instead, and asks before building on a figure over 600 seconds.
- **Still open:** whether whisper.cpp and the parsing model fit together at 16GB (SPEC Open Questions). Whisper is not in wave 3, so 7a's benchmark measures the parsing model alone, with a GitLab-sized harvest persisting beside it.

## Cost & token efficiency

**NFR-13** — Total monthly operational LLM API spend plus electrical runtime power for local model execution is held to a **monitored target of $20/month per user**, achieved by maximizing deterministic scripts and local execution and reserving frontier calls for high-level synthesis. Every frontier call records token counts and a cost estimate to the application-scoped disclosure ledger, and the running monthly total surfaces in briefings and the CLI.

**A meeting summary the PM explicitly asks for** is a frontier use too (kernel amendment 2026-10-16; story 7c): one meeting's transcript, from its own scope back to that scope, never automatic and never a fallback for a local failure. A `sent` disclosure line precedes the request and a `completed` line records the tokens the answer reported and the cost computed from a dated price table (Sonnet 5 $2/$10, Opus 5 $5/$25 per million tokens, the spine's figures, checked when 7c is built); a model with no row refuses the call. No token-counting request is made, since it would send the transcript. The job's reply carries the call's cost and the month's total, warning past $20, and the personal dashboard's Leadership Notes shows the month's total (story 7d).

**Breaching the target produces a warning only.** The system shall not silently degrade output quality, downgrade models, or disable features on breach. The figure is an instrument for understanding real operating economics; converting it into an enforced cap is a later decision to be taken against actual spend data.

## Harvest request bounds stated by wave 3 (2026-10-16)

- **Graph transcripts (story 8s):** the first-run reach-back defaults to five days and transcript acquisition keeps the whole span, so every harvest asks again about each ended meeting of the last five days that has no transcript and no closing answer. How many requests that is per harvest is counted by 8s's test on the recorded Graph transport and recorded in its spec — not yet stated. There is no per-harvest ceiling on them (Andrei, 2026-10-16): the ten-minute fetch budget bounds the run, and a meeting not reached waits for the next harvest.
- **Graph channels (story 33h):** one request shape (`$top=50&$expand=replies`), the 200-page cap and the shared time budget unchanged; the walk stops after the first page whose oldest first post is more than 90 days before the channel's position. Replies in threads started before that horizon are an admitted limit.
- **GitLab work (story 8t):** one 10-minute budget per harvest shared by commits, members, issues, merge requests and links; each walk capped at 500 pages of 100; at most 500 related-issue requests per run, the rest read next run.
