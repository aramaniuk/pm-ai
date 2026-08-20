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
| **NFR-09** | Raw transcripts in the owning scope's encrypted `transcripts/` are retained a default **30 days** (configurable) and purged automatically only after verified conversion into summaries, Work Item updates, decision logs, and pruned indexes. |

## Reliability, offline resilience & hardware

| Id | Requirement |
| --- | --- |
| **NFR-10** | On network disruption, incoming audio notes, CLI commands, and state actions buffer in the encrypted operational store and replay sequentially without data loss on reconnection. |
| **NFR-11** | Recovery is **tier-scoped**: Truth and Operational state both survive and are both backup targets; only Derived state is disposable and rebuilds with zero loss. Operational state is never a rebuild target, and restoring it opens a re-execution window the CLI must warn about. |
| **NFR-12** | Local extraction, parsing, and transcription run on a quantized **8B-class** open-weight instruct model at `Q4_K_M` plus Whisper `small.en`. Minimum hardware **16GB RAM on Apple Silicon**; v1 is macOS-only, so the CUDA baseline is deferred alongside Linux. Models above 8B-class are out of scope for v1 — they cannot run concurrently with transcription at the 16GB baseline without swap thrashing. |
| **NFR-14** | The daemon binds **loopback only**, exposing zero public HTTP or WebSocket ports. Telegram runs over **outbound HTTPS long-polling** authenticated by paired user-IDs. Webhooks are prohibited: they require the public endpoint this same requirement forbids. |

*(There is no NFR-13 in this group — it is the cost target below, numbered as in the source.)*

## Cost & token efficiency

**NFR-13** — Total monthly operational LLM API spend plus electrical runtime power for local model execution is held to a **monitored target of $20/month per user**, achieved by maximizing deterministic scripts and local execution and reserving frontier calls for high-level synthesis. Every frontier call records token counts and a cost estimate to the application-scoped disclosure ledger, and the running monthly total surfaces in briefings and the CLI.

**Breaching the target produces a warning only.** The system shall not silently degrade output quality, downgrade models, or disable features on breach. The figure is an instrument for understanding real operating economics; converting it into an enforced cap is a later decision to be taken against actual spend data.
