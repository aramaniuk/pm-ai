# Storage Contract — pm-ai

Companion to `SPEC.md`. What persists, which tier it belongs to, what is encrypted, and what survives a loss. Directory layout and scope ownership are in `scope-model.md`.

## Three tiers, and only one is disposable

| Tier | Contents | Backed up | Rebuildable |
| --- | --- | --- | --- |
| **1 — Truth** | Plaintext Markdown: event log segments, `commitments_log.md`, coaching history, goals, rules, meeting records, disclosure ledger | Yes | n/a — it *is* the source |
| **2 — Operational** | Job queue and retry buffer, connector cursors, executed-idempotency-key ledger, staged proposals, key material, personal analytics | Yes | **Never** — not derivable from Markdown |
| **3 — Derived** | Search and commitment indexes (`derived.db`), vector index, caches | No | Yes, entirely from Truth, with zero loss |

**Three tiers are not the whole partition.** Every persistent artifact is in exactly one of the three tiers, `RETENTION_MANAGED`, or `DIAGNOSTIC_ONLY` — five sets, pairwise disjoint:

- **`RETENTION_MANAGED`** — raw captures and transient input under the retention purge below: `transcripts/` in all three scopes that hold them, and `~/.manager-ai/private/telegram_cache/`. Never a backup target in any scope, and nothing may depend on them.
- **`DIAGNOSTIC_ONLY`** — `~/.pm-ai/logs/`, the rotating structured diagnostic logs. Deliberately **not** folded into `RETENTION_MANAGED`: a rotating diagnostic log is not a raw capture, and filing it there would place it under a purge promise nothing implements. It is not `event_log/` and carries none of its guarantees.

An artifact in none of the five is an oversight, not a category. Deleting Tier 3 must result in **zero** data loss. Losing Tier 2 loses pending external writes and resets harvest position, which is why it is a backup target and never a rebuild target — and why it is stored separately from Tier 3.

**Restoring Tier 2 from a backup opens a re-execution window** for mutations performed after the backup point. The CLI must warn; reconciliation against the external system is the PM's call.

**A durability is global by basename; a path is per-scope.** One artifact *name* carries one tier in every scope, and declaring the same name at two tiers is refused rather than resolved. So a project `daily_dashboard.md` cannot be Tier 3 while the personal one is Tier 1, even though each choice is defensible on its own. A *path*, by contrast, is per-scope — `persona.md` and `daily_dashboard.md` each exist in two scopes with different content, which is why the layout is keyed by scope and not by name. Both are Tier 1 today, so nothing is currently lost; needing a per-scope durability means re-keying the tier tables on `(scope, path)` first, which is an architecture change rather than a local override.

**Tier-2 schema change is forward-only.** Ordered migrations that preserve existing rows, each applied at most once, refusing to open a store stamped newer than the code. Drop-and-recreate is prohibited on the one tier no rebuild can reconstruct: it destroys pending external writes and resets every connector cursor.

`personal_analytics.db` is Tier 2 specifically because burnout trends outlive the telemetry they were computed from once compaction prunes it — Tier 3 would mean rebuildable-from-truth, and it is not.

## Encryption at rest

Encryption applies to a **defined set** rather than to all local state, because plaintext Markdown is a deliberate product property.

**Encrypted** (AES-256, 600 permissions) — narrowed 2026-08-22 to credentials and the sovereign personal enclave:

- API credentials (`~/.pm-ai/private/config.json`) — every provider token lands here, including the one `pm-ai connector add` prompts for
- The sovereign personal enclave in full (`~/.manager-ai/private/`): the PM's own voice notes and dialogue state (`telegram_cache/`) and the personal analytics store (`personal_analytics.db`)

**Deliberately dropped from that set**, and what protects each instead:

| No longer encrypted | What holds the line now |
| --- | --- |
| `operational.db` | 600 permissions and full-disk encryption. It holds queue state and cursors rather than record content |
| Raw captures, in all three scopes | The git guard below, plus the 30-day purge. A transcript's exposure is publishing it to a repository, and a cipher never addressed that |
| Team-member records (`~/.pm-ai/private/people/`) | 600 permissions, gitignored, and a single deletable directory |
| Connectors (`~/.pm-ai/connectors/`) | 600 permissions and gitignored. It holds *configuration* — type, domain, cadence, enabled — and the hot-loadable *implementation* modules, and **no secret of any kind**: every connector credential goes to `config.json`, which is encrypted. That split was always in the layout; only CAP-35's wording conflated the two. What matters for this directory is integrity rather than confidentiality, since its contents are executed |

**This is a real reduction in protection, recorded rather than glossed.** A report's performance record is now readable by anything running as the PM's user, and the requirement that a report's peers cannot read it rests on file permissions and the enclave's directory boundary alone. Full-disk encryption is the backstop for all three rows. The captures row is the least changed in substance: their risk was always the repository, not the disk.

**Explicitly not encrypted:**

- **All Markdown in every scope** — including coaching history, strategic goals, event logs, and the commitments ledger. Plaintext by design, so the PM can read, grep, diff, and hand-edit their own record without the system's cooperation.
- **Tier-3 derived state** — search and commitment indexes and the vector index hold derived embeddings and lookup structures rather than recoverable text, rebuild entirely per the tier table, and are protected by 600 permissions plus full-disk encryption.

**Key custody.** The master key is held in the OS keychain so the daemon can start unattended; raw key export is the supported migration path.

**Debug toggle.** Encryption may be disabled by an explicit debug flag. It is never the default in a fresh install, and while active it must emit both a console warning and an event-log entry.

## Retention

Raw transcript text in the encrypted `transcripts/` directories is retained for a default 30 days (configurable), on identical terms in all three scopes. The background runner purges past the threshold **only after verified conversion** into Markdown summaries, Work Item updates, decision logs, and pruned memory indexes.

**Exclusion from version control is verified before the write, by asking git.** `check-ignore` answers about the rules and `ls-files` about the index, and the verdict carries both facts separately, because they call for different repairs — add a rule, versus untrack what is already committed. Matching rule text in `.gitignore` is not an answer: a negation line re-includes an excluded directory, a parent exclude protects a child no rule names, and a directory committed before the rule was added stays tracked no matter what rule follows. The question is keyed on whether the capture path lies inside a git working tree, not on which scope owns it, so a personal scope kept as a private repository is guarded on the same terms as the employer's. A directory git reports as tracked refuses the write; outside a working tree the write proceeds. **git itself is optional** — its absence never blocks recording a meeting, because a machine without git and a directory without a repository both lack anything that could commit a capture. The refusal narrows to a repository demonstrably present and unaskable, which is detectable without a binary by looking for `.git`. `pm-ai doctor` probes `git` so its absence is reported rather than inferred.

Retention for telemetry rows and derived summaries beyond raw transcripts is unspecified and awaits real disk-growth data.

## Compaction and the append-only rule

The event log is **segmented** — a directory of dated segments, exactly one open and appended to, earlier segments sealed and immutable — so compaction can bound growth by replacing whole sealed segments rather than rewriting entries in place.

Activity streams older than 7 days compress into structured long-term milestone summaries. The vector index stays under 500MB indefinitely through automated pruning.

Bounded forgetting is this recorded compaction and nothing else. Retrieval weighting may reorder what surfaces; it may never edit, delete, or rewrite what was logged.

## Latency split

Retrieval (SQLite plus vector lookup, no model in the path) holds **50–150 ms**. Synthesis (retrieval followed by a model call) holds **≤60 s** and is always delivered asynchronously. No synthesized response is expected inside the retrieval budget — the two describe different operations. Full budgets in `nfr-budgets.md`.

## Offline behaviour

On network disruption, incoming audio notes, CLI commands, and state actions buffer in the encrypted operational store and replay sequentially without data loss on reconnection, within 30 seconds of link re-establishment.
