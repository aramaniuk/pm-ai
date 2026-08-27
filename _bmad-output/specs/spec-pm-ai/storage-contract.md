# Storage Contract — pm-ai

Companion to `SPEC.md`. What persists, which tier it belongs to, what is encrypted, and what survives a loss. Directory layout and scope ownership are in `scope-model.md`.

## Three tiers, and only one is disposable

| Tier | Contents | Backed up | Rebuildable |
| --- | --- | --- | --- |
| **1 — Truth** | Plaintext Markdown: event log segments, `commitments_log.md`, coaching history, goals, rules, meeting records, disclosure ledger | Yes | n/a — it *is* the source |
| **2 — Operational** | Job queue and retry buffer, connector cursors, executed-idempotency-key ledger, staged proposals, key material, personal analytics | Yes | **Never** — not derivable from Markdown |
| **3 — Derived** | Search index (`event_index.db`), commitment index (`commitment_index.db`), vector index (`vector_index/`) | No | Yes, entirely from Truth, with zero loss |

Nothing builds the Tier-3 contents today, and no story claimed to until 2026-08-27. `derivation-services.md` holds the design that closes it, the job that owns each artifact, and the constraint that follows for everything in this tier — and it is why the single `derived.db` became two files on that date: one index per rebuilding job, because a job declares its whole output and two jobs cannot each own half a file. The rule: **an artifact is Tier 3 only if a declared job can rebuild it from Tier 1 alone.** Anything that cannot belongs in another tier. ("Caches" was struck from the row above on the same date: no capability asked for one, and an undefined member of the disposable tier is an invitation to put something non-rebuildable there.)

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
- The PM's own voice notes and dialogue state (`~/.manager-ai/private/telegram_cache/`)

Both are **files**. Nothing encrypted is a database, which is what removed `sqlcipher3` from the dependency set — it had no macOS wheel and would have needed a source build on the one platform v1 targets, for a single file.

**Deliberately dropped from that set**, and what protects each instead:

| No longer encrypted | What holds the line now |
| --- | --- |
| `operational.db` | 600 permissions and full-disk encryption. It holds queue state and cursors rather than record content |
| `personal_analytics.db` | 600 permissions, gitignored, and full-disk encryption — the same terms as `operational.db`, which it matches in every structural respect. What keeps burnout figures away from an employer is the scope boundary and the egress rules, not a cipher: encryption at rest only answers someone reading the disk, and full-disk encryption already answers that |
| Raw captures, in all three scopes | The git guard below, plus the 30-day purge. A transcript's exposure is publishing it to a repository, and a cipher never addressed that |
| Team-member records (`~/.pm-ai/private/people/`) | 600 permissions, gitignored, and a single deletable directory |
| Connectors (`~/.pm-ai/connectors/`) | 600 permissions and gitignored. It holds *configuration* — type, domain, cadence, enabled — and the hot-loadable *implementation* modules, and **no secret of any kind**: every connector credential goes to `config.json`, which is encrypted. That split was always in the layout; only CAP-35's wording conflated the two. What matters for this directory is integrity rather than confidentiality, since its contents are executed |

**This is a real reduction in protection, recorded rather than glossed.** A report's performance record is now readable by anything running as the PM's user, and the requirement that a report's peers cannot read it rests on file permissions and the enclave's directory boundary alone. Full-disk encryption is the backstop for all three rows. The captures row is the least changed in substance: their risk was always the repository, not the disk.

**Explicitly not encrypted:**

- **All Markdown in every scope** — including coaching history, strategic goals, event logs, and the commitments ledger. Plaintext by design, so the PM can read, grep, diff, and hand-edit their own record without the system's cooperation.
- **Tier-3 derived state** — search and commitment indexes and the vector index hold derived embeddings and lookup structures rather than recoverable text, rebuild entirely per the tier table, and are protected by 600 permissions plus full-disk encryption.

**Key custody.** The master key is held in the OS keychain so the daemon can start unattended; raw key export is the supported migration path.

**The key is enrolled before pm-ai is first run.** It is a setup step, not something the daemon arranges for itself — minting a key is irreversible in the only way that matters, because a new one makes every previously sealed artifact unreadable, and a process start is the wrong place to make that decision. The daemon therefore fetches the key **lazily**: it starts without one and harvests, briefs and answers the CLI as normal, and the refusal lands at the moment an encrypted artifact is actually read or written — where an operator can act on it. `pm-ai doctor` reports "reachable, key absent" as a state distinct from an unreachable keychain, which is how a machine that skipped setup says so.

The consequence for anything writing both secret and non-secret state: **write the secret first.** A refused encrypted write leaves nothing behind, so ordering it first makes the whole operation atomic enough. The other order leaves configuration referring to a credential that was never stored — which reads as working.

**Debug toggle.** Encryption may be disabled only by an **environment variable**, set for the lifetime of one process, and only for short-term debugging. While active it emits both a console warning and an event-log entry.

Deliberately not a config key, and this supersedes the spine's Deployment note putting it in `~/.pm-ai/config.toml`. A flag that writes credentials in plaintext must not be able to outlive the session that set it: the console warning scrolls away within minutes, and a startup event-log entry is weeks old by the time anyone wonders why a credential file is readable. An environment variable dies with the process, so **restarting restores encryption unconditionally** — no audit, no expiry logic, and nothing to forget. There is no other legitimate reason to run pm-ai with encryption off.

## How a write becomes visible

Added 2026-08-27. Until a filesystem watcher existed, a half-written file had no observer and only crash durability mattered. Now the moment a name appears is the moment a job starts, so *when* a write becomes visible is part of the contract. Three write shapes, three different answers.

**Append — `event_log/` segments, `commitments_log.md`.** Cannot be made atomic: rename-into-place means rewriting the whole file, which the append-only rule below forbids. It does not need to be. Records are one per line, newline-terminated, so a reader that lands mid-flush sees every complete record plus a fragment at the tail. **The rule is therefore a parser rule: a record without its terminating newline is not a record.** A fragment is a boundary, not corruption. A job that ran early is corrected by the next event, since indexers delete and rebuild a source file's entries rather than adding to them.

**Exclusive create — captures in `transcripts/`.** `O_CREAT|O_EXCL` claims the name before the content exists, so a watcher sees a capture that is still growing. This is the one shape where an early read is unrecoverable: transcript processing appends a summary, decisions and commitments to Tier-1 ledgers, and re-running on the completed file appends all of them a second time with nothing to distinguish the two. **A capture becomes visible atomically or not at all:**

1. Write the whole body into `transcripts/temp/`, flush and `fsync`.
2. `os.link` it to the final name — atomic *and* refusing a name already taken, so exclusivity stays kernel-enforced exactly as `O_EXCL` made it.
3. Unlink the temp name; `fsync` the directory.

`transcripts/temp/` is inside the capture directory deliberately: the derived gitignore rule is a directory rule (`/.project-ai/transcripts/`), so the temp is already excluded from version control with no second rule, and it is on the same filesystem, which `link` requires. **It must be excluded from the watcher** — a recursive watch would otherwise see every temp file. That is an explicit exclusion, not an implied one.

This also closes a hole the current code cannot: `write_capture` unlinks a partly-written capture in an exception handler, which does nothing for `SIGKILL` or a power loss, and the docstring names the consequence — a zero-length file owns the name and every retry, including the one carrying the content, is refused as a duplicate. Under temp-then-link the final name is never claimed until the content is complete, so there is nothing to clean up and nothing to block the retry.

Where `link` is unsupported — exFAT, some network mounts, reachable only through an *enrolled project repository*, never through `~/.pm-ai` or `~/.manager-ai` — fall back to check-then-rename rather than refusing to record the meeting. Exclusivity then rests on writer serialization (AD-5, AD-19) instead of the kernel, which covers the real case: a duplicate name arrives as a later retry, not as a concurrent write. Detect it by attempting the link and catching the error, never by inspecting filesystem type.

**Whole-file replace — `config.json`.** `O_TRUNC` destroys the old content before the new content is written, and an AES-GCM file truncated part-way does not degrade, it fails its tag and becomes unreadable. A crash mid-rotation therefore loses every connector credential. **Write to a temp file in the same directory at `0600`, `fsync`, then `os.replace`.** Here `replace` is right where `link` was right for captures: rotating a token *must* overwrite, so overwrite-on-rename is the wanted behaviour and refusing a taken name would be the defect.

**And every raw write loops.** `os.write` returns how many bytes it wrote and a short write silently truncates — same total unreadability for a sealed file. Write until the payload is exhausted; never discard the return value.

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
