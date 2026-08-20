# Roadmap Phasing — pm-ai

Companion to `SPEC.md`. The delivery sequence the capabilities were grouped into. Capabilities are listed by CAP id; the FR ids they correspond to are in `traceability.md`.

## Phase 1 — Core foundation, security enclave & interface bridges

Sovereign directory contract, Telegram bridge with cryptographic pairing, terminal CLI REPL bound to loopback, AES-256 local enclave encryption, local Whisper transcription, event log, commitment ledger structure, input sanitization firewall, MCP execution firewall, and the basic connector framework.

**Capabilities:** CAP-1, CAP-3, CAP-4, CAP-10, CAP-16, CAP-18, CAP-19, CAP-20, CAP-34, CAP-35, CAP-36
**Budgets:** NFR-08, NFR-09, NFR-12, NFR-14

## Phase 2 — High-context concierge, telemetry radar & closed-loop ledger

24/7 background telemetry radar, multi-tool connector expansion, memory pruning pipeline, pre-meeting preparation dashboard, closed-loop commitment validation, automated inquiry proxy, voice/text response synthesis, deep inquiry engine, drift auditor, spoken anchor extraction, dual-authorization extraction, missed-meeting ingestion, and transcript research execution.

**Capabilities:** CAP-2, CAP-5, CAP-6, CAP-7, CAP-8, CAP-21, CAP-22, CAP-23, CAP-24, CAP-25, CAP-26, CAP-27, CAP-28, CAP-32, CAP-33, CAP-34, CAP-35, CAP-37
**Budgets:** NFR-04

## Phase 3 — Socratic coaching & web/literature engine

Socratic 1:1 protocol, daily strategic focus briefings, contextual web and literature recommendation engine, and meeting cost metrics.

**Capabilities:** CAP-9, CAP-11, CAP-12, CAP-13, CAP-14, CAP-17, CAP-29

## Phase 4 — HR MCP integration & leadership experiments

Multi-HR tool MCP skill, career dossier pipeline, cohort and individual metric monitor, and continuous leadership dynamic auditing.

**Capabilities:** CAP-15, CAP-30, CAP-31

## Unphased in the source

**CAP-38, CAP-39, CAP-40** — the continuous self-improvement group — were added after the roadmap table was written and carry no phase assignment in the source. They depend on Phase 2 producing enough proposal and commitment history to measure against, which is also when the Performance Index open question becomes answerable.

## Phase 1 checks the source flags

- Benchmark concurrent Whisper transcription and local model parsing at the 16GB baseline before the hardware floor is trusted.
- Select the specific 8B-class local model; the spec names a class, not a pin.
- Pin the embedding model and dimension before the first index is written — a change is a reindex event.
