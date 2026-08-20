# Storage Contract — pm-ai

Companion to `SPEC.md`. What persists, which tier it belongs to, what is encrypted, and what survives a loss. Directory layout and scope ownership are in `scope-model.md`.

## Three tiers, and only one is disposable

| Tier | Contents | Backed up | Rebuildable |
| --- | --- | --- | --- |
| **1 — Truth** | Plaintext Markdown: event log segments, `commitments_log.md`, coaching history, goals, rules, meeting records, disclosure ledger | Yes | n/a — it *is* the source |
| **2 — Operational** | Job queue and retry buffer, connector cursors, executed-idempotency-key ledger, staged proposals, key material, personal analytics | Yes | **Never** — not derivable from Markdown |
| **3 — Derived** | Search and commitment indexes (`derived.db`), vector index, caches | No | Yes, entirely from Truth, with zero loss |

Deleting Tier 3 must result in **zero** data loss. Losing Tier 2 loses pending external writes and resets harvest position, which is why it is a backup target and never a rebuild target — and why it is stored separately from Tier 3.

**Restoring Tier 2 from a backup opens a re-execution window** for mutations performed after the backup point. The CLI must warn; reconciliation against the external system is the PM's call.

`personal_analytics.db` is Tier 2 specifically because burnout trends outlive the telemetry they were computed from once compaction prunes it — Tier 3 would mean rebuildable-from-truth, and it is not.

## Encryption at rest

Encryption applies to a **defined set** rather than to all local state, because plaintext Markdown is a deliberate product property.

**Encrypted** (AES-256, 600 permissions):

- The Tier-2 operational store (`operational.db`)
- Raw meeting transcripts and audio (`transcripts/`, in whichever scope owns the meeting)
- The PM's own voice notes and dialogue state (`~/.manager-ai/private/telegram_cache/`)
- API credentials (`config.json`)
- Team-member records (`~/.pm-ai/private/people/`)
- The personal analytics store (`~/.manager-ai/private/personal_analytics.db`)

**Explicitly not encrypted:**

- **All Markdown in every scope** — including coaching history, strategic goals, event logs, and the commitments ledger. Plaintext by design, so the PM can read, grep, diff, and hand-edit their own record without the system's cooperation.
- **Tier-3 derived state** — search and commitment indexes and the vector index hold derived embeddings and lookup structures rather than recoverable text, rebuild entirely per the tier table, and are protected by 600 permissions plus full-disk encryption.

**Key custody.** The master key is held in the OS keychain so the daemon can start unattended; raw key export is the supported migration path.

**Debug toggle.** Encryption may be disabled by an explicit debug flag. It is never the default in a fresh install, and while active it must emit both a console warning and an event-log entry.

## Retention

Raw transcript text in the encrypted `transcripts/` directory is retained for a default 30 days (configurable). The background runner purges past the threshold **only after verified conversion** into Markdown summaries, Work Item updates, decision logs, and pruned memory indexes.

Retention for telemetry rows and derived summaries beyond raw transcripts is unspecified and awaits real disk-growth data.

## Compaction and the append-only rule

The event log is **segmented** — a directory of dated segments, exactly one open and appended to, earlier segments sealed and immutable — so compaction can bound growth by replacing whole sealed segments rather than rewriting entries in place.

Activity streams older than 7 days compress into structured long-term milestone summaries. The vector index stays under 500MB indefinitely through automated pruning.

Bounded forgetting is this recorded compaction and nothing else. Retrieval weighting may reorder what surfaces; it may never edit, delete, or rewrite what was logged.

## Latency split

Retrieval (SQLite plus vector lookup, no model in the path) holds **50–150 ms**. Synthesis (retrieval followed by a model call) holds **≤60 s** and is always delivered asynchronously. No synthesized response is expected inside the retrieval budget — the two describe different operations. Full budgets in `nfr-budgets.md`.

## Offline behaviour

On network disruption, incoming audio notes, CLI commands, and state actions buffer in the encrypted operational store and replay sequentially without data loss on reconnection, within 30 seconds of link re-establishment.
