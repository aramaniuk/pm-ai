---
title: 'the commitment index'
type: 'feature'
created: '2026-10-26'
status: 'draft'
baseline_commit: '7a097a7'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/derivation-services.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/15a-approved-items-become-commitments.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/15c-the-pms-commitment-commands.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/1h-derived-tier-rebuild.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** CAP-34 says an approved commitment writes "a structured Markdown entry plus an indexed row at `[PENDING]`". Since 15a and 15c the Markdown entry exists, in each scope's `commitments_log.md`, and nothing writes the row:
- **`commitment_index.db` is declared and empty.** The scope model declares it (Tier 3, not encrypted, gitignored) and the path resolver names it, but no code creates it.
- **Story 16 needs it.** Its lifecycle job reads the index to list the open commitments (AD-45), and it must not trust a row the ledger no longer agrees with.
- **No derived index exists yet.** This is the first one, so it sets the pattern 18 and 10a follow: each row carries the checksum of the file it came from, and the index can be dropped and rebuilt from the Markdown alone (AD-3, story 1h).
- **The job runner does not exist.** Story 10a's watcher and queue are what should start indexing (AD-45, AD-46). Andrei decided on 2026-10-24 that, until then, the daemon starts it after each append and checks it at start.

**Approach:**
- **One index file** for every ledger the daemon composes, `~/.pm-ai/private/commitment_index.db`, one row per commitment per scope, plain SQLite, not encrypted, as declared.
- **Rows are the fold.** A scope's rows are computed from its ledger's bytes by 15a's and 15c's own `parse_ledger` and `fold`, through `CommitmentLog`, and each row stores the MD5 of exactly those bytes. Nothing in a row depends on the clock.
- **Re-derived after each append the daemon makes,** to that scope: the approval, the PM's commands and the catch-up all append through one storage call, and that call asks for the scope to be derived again.
- **Checked at daemon start:** every scope whose stored checksum differs from its ledger's is derived again, rows of a scope the daemon no longer composes are removed, and an unusable index file is dropped and rebuilt.
- **Nothing reads it yet.** Every reader of commitments keeps reading the Markdown. Story 16 is the first reader.
- **Temporary, as `9a`'s timers are:** the trigger and the start task are replaced by story 10a's job; the derivation, the schema and the checksums stay.

## Boundaries & Constraints

**Always:**

### Prerequisites and what is decided already

- **Built and relied on:**
  - `15a`: the line kinds, `parse_ledger`, `fold`, `CommitmentLog` (`core/commitment_log.py`), the store's `read_commitments` and `append_commitments`, the recorder and the catch-up at daemon start (`app/commitments.py`);
  - `15c`: `Details` per commitment (version, mark in force, `due_by_pm`, `owner_by_pm`, `changed_at`, `state_at`), the `undone` line, the desk's compare-and-append (`CommitmentDesk._write`);
  - `4r`: the daemon's writer thread and I/O pool; `1r`: `StorageService.exclusive`; `1s`: private files created at `0600`;
  - the scope model's `File("commitment_index.db", Tier.DERIVED, encrypted=False, gitignored=True)` (`scope_model.py:486`) and `ScopePaths.commitment_index_store` (`paths.py:658`).
- **Decided by Andrei, 2026-10-24, not reopened here:** the index is started by a trigger inside the daemon until story 10's queue, plus a check at daemon start.
- **Decided by story 15's entry, not reopened here:** the index is reconstructible from the Markdown alone; each row stores the MD5 of `commitments_log.md` as of the derivation that produced it, per row, not per index; the ledger is read through `CommitmentLog` and never parsed a second way.
- **The slices of story 15:** 15a (ledger, done), **15b (this spec)**, 15c (commands, done), 15d (surfaces, specced in parallel). 15b depends on nothing in 15d, and 15d depends on nothing here.

### Where it is, and what it covers

- **One file:** `~/.pm-ai/private/commitment_index.db`, the application scope's enclave, as the scope model already declares. Not one file per scope: the declaration is one `File` in the application tree, and a file per scope would add a node to two trees, which 1h makes an Ask First (`ARTIFACT_TIER`).
- **It covers the scopes the daemon composes:** personal and every project enrolled at its start (AD-5's refresh point for `projects.toml`). Never a `people` scope or the application scope: neither keeps a ledger (`NotACommitmentScope`, AD-4, AD-28).
- **Not encrypted.** Tier 3 is "explicitly not encrypted" (`storage-contract.md`), nothing encrypted is a database, and the declaration says `encrypted=False`. So the index needs no master key: it is written when the key is absent or the keychain is locked. Protection is the file's `0600`, the enclave's `0700`, and full-disk encryption, as for `operational.db`.
- **Created at the first write only,** at `0600` before SQLite opens it, so its `-wal` and `-shm` follow (story 1s). A read of an absent index answers "no rows" and creates nothing.

### What a row holds

- **Table `commitments`, one row per commitment per scope.** Primary key `(scope, commitment_id)`, so a commitment id hand-copied into two ledgers is two rows, as 15c's `also_in` shows it.

  | column | from the fold | meaning |
  | --- | --- | --- |
  | `scope` | the ledger's scope | `personal` or `project:<id>` |
  | `commitment_id` | `Commitment.commitment_id` | the proposal's root id |
  | `proposal_id` | `Commitment.proposal_id` | the proposal whose values it holds now |
  | `type` | `Commitment.type` | `action_item` or `implicit_commitment` |
  | `state` | `Commitment.state` | a `CommitmentState` value |
  | `state_source` | `Commitment.state_source` | `none`, `pm` or `telemetry` |
  | `state_at` | `Details.state_at` | when the line the state came from occurred; empty for `pending` |
  | `owner` | `Commitment.owner` | a name, never a handle (AD-34); empty when none |
  | `owner_by_pm` | `Details.owner_by_pm` | 1 when the PM set it |
  | `speaker` | `Commitment.speaker` | who said a spoken promise; empty otherwise |
  | `due_value` | `Commitment.due.value` | a date, `unresolved` or `none` |
  | `due_date` | `Commitment.due.date` | the date, empty for the two words |
  | `due_estimated` | `Commitment.due.estimated` | 1 when worked out against an estimated meeting day |
  | `due_by_pm` | `Details.due_by_pm` | 1 when the PM set it |
  | `meeting_id` | `Commitment.meeting_id` | empty when the proposal cites none |
  | `meeting_start` | `Commitment.start` | empty when the line has none |
  | `day`, `day_from` | `Commitment.day`, `Commitment.day_from` | the meeting's day the due was worked out against, and its estimate source |
  | `approved_at` | `Commitment.approved_at` | |
  | `changed_at` | `Details.changed_at` | the newest `occurred` among its lines |
  | `version` | `Details.version` | 15c's version: the entry id of its newest line in the file |
  | `malformed_named` | `commitment_id in ledger.malformed_roots` | 1 when a malformed line names it: a reader acts on nothing it says (15c refuses it as `malformed`) |
  | `source_md5` | the bytes folded | the MD5 of the scope's whole `commitments_log.md`, as read for this derivation |

  Instants are ISO 8601 UTC, dates `YYYY-MM-DD`, empty is SQL `NULL`. One secondary index on `(scope, state, due_date)`, for story 16's "open, by due".
- **What a row leaves out, deliberately:**
  - **the words** (`what`, the title, the PM's note, the due as said). `storage-contract.md` says the indexes hold "lookup structures rather than recoverable text", and every reader that shows a card reads the ledger for it;
  - **anything that depends on the clock or the display zone.** "Past due" is not stored: a reader computes it from `due_date`, `due_estimated` and its own today, as 15c's card does. So a rebuild on another day gives the same rows;
  - **the ledger's bad-line counts** (malformed, orphans, duplicates, conflicts, refused). They describe the file, not a commitment, and 15c's `list` shows them from the Markdown;
  - **a work item reference** story 16 may need. A column added later is a format change (below), not a migration.
- **Table `index_format`, one row:** the format number, `INDEX_FORMAT = 1`, in `pm_ai/core/commitment_index.py`. It says which columns the file has, nothing about freshness: freshness is only ever the per-row checksum.
- **A different format, or a file that is not a usable index, is dropped and rebuilt, never migrated.** Tier 3 is disposable (AD-3), and story `1i`'s schema versioning is for Tier 2.

### The checksum

- **One function:** `source_md5(data: bytes) -> str` in `pm_ai/core/jobs.py`, 32 lower-case hex characters of MD5 over the raw bytes as stored, with no normalisation of any kind (AD-46). It is the only MD5 call in `pm_ai`; story 18's index and 10a's boot reconciliation call the same function.
- **Over the bytes that were folded:** one `CommitmentLog.read_with_bytes(scope)` gives both the fold and the bytes, and the checksum is of those bytes. A row therefore always says "I am the fold of the file whose MD5 is X". It is never computed from a second read.
- **Every row of a scope carries the same checksum,** because the unit of derivation is the whole file (AD-46: "for an append-only artifact the unit of comparison is the whole file").
- **It is change detection only,** never integrity or authenticity (AD-46). Nothing may read it as either.

### Deriving one scope (the only way rows are written)

`derive(scope)`, in `pm_ai/core/commitment_index.py`, through `StoragePort`:
1. **Read** the scope's ledger with `CommitmentLog.read_with_bytes` (a plain file read) and take `source_md5` of the bytes.
2. **Compare** with the checksums the index holds for that scope (`commitment_index_checksums`, a plain read on its own read-only connection). When every row of the scope carries that checksum and there is at least one row, the scope is **current**: nothing is written. This is AD-45's rule that a request whose inputs are unchanged does no work.
3. **Otherwise compute the rows**, `index_rows(scope, ledger, md5)`, a pure function: one row per entry of `ledger.commitments`, each column from the table above.
4. **Replace** the scope's rows in one claimed step (`replace_commitment_rows`): under `exclusive` on `commitment_index.db`, one SQLite transaction deletes every row of the scope and inserts the new ones, and checks the format row first. All or nothing: a failure rolls back and leaves the scope's old rows, which still carry their old checksum and so still read as stale.

- **Steps 1 to 3 run on a worker, step 4 on the writer thread** (AD-5, AD-19). In the daemon, `replace_commitment_rows` is a handed call and the reads are plain reads.
- **A ledger with no commitment** (absent, empty, or only bad lines) has no row to carry its checksum, so it is never "current": its scope is derived at every check, which costs one read and one fold and writes nothing when the scope has no rows. That is the stated cost of a checksum per row rather than per index.
- **An absent ledger** (`read_commitments` gives `b""`) folds to no commitment, so the scope's rows are deleted. This is how a ledger deleted while the daemon was down stops being served (AD-46).

### What starts a derivation

- **After each append the serving daemon makes to a ledger.** `StorageService.append_commitments` (`service.py:4418`) is the one storage call every writer uses: 15a's recorder (an approval through the daemon, and a repeated approve), 15a's catch-up at start, and 15c's desk (every mark, `due`, `owner`, `undo`, and `altered`'s mark and its `revised` line). Story 16's verdicts will use it too.
  - **The hook:** the serving daemon gives its store one callback, `StorageService.on_commitments_appended(callback)`, set in `attach_background` (`daemon.py:1023`), which only the serve path calls. A store built by a command at a terminal has none.
  - **Called after the append returned and its claim was released,** on the writer thread, with the scope, only when bytes were written. An append that raised calls nothing, even when its bytes reached the file; the next check sees them.
  - **The callback does no I/O:** it adds the scope to the indexer's set of requested scopes and wakes its task (`loop.call_soon_threadsafe`). So the writer call stays short (AD-5) and the client's command never waits for the index. A callback that raises is logged and never fails the append: the line is written.
- **The indexer task** (`CommitmentIndexing`, `pm_ai/app/commitment_indexing.py`) takes the requested scopes one at a time and derives each. A scope requested again while it is being derived is derived once more afterwards, so two quick appends give at most two derivations and the last one reads the newer file. Derivations never overlap, so rows from an older read never replace rows from a newer one. This is AD-46's queue-level deduplication on `(job, artifact)`, held in memory until 10a.
- **Appends a terminal process makes, and hand edits, start nothing.** They are seen at the daemon's next append to that scope, which derives the whole file, or at its next start. See Ask First.
- **The indexer never writes a ledger.** It holds no ledger claim, so it can never make a ledger writer meet `LedgerChanged` or `ArtifactBusy`, and its own writes go to Tier 3, which nothing watches, so it cannot start itself (AD-45).

### The check at daemon start

- **When:** once per start, as a one-shot task in `_lifespan` (`daemon.py:1046`), after 15a's catch-up has finished (whatever its outcome), so the start's own lines are derived in the same pass. It never delays serving and never stops the daemon starting: a failure is logged, and the next start tries again.
- **What it does:**
  1. **The file:** an index that cannot be opened as SQLite, lacks a table, or holds another format is dropped (below) and rebuilt from every scope. Logged once, by reason.
  2. **Each scope the daemon composes:** `derive(scope)`. A scope whose stored checksum equals its ledger's is current and costs one read and one hash.
  3. **Each scope with rows that the daemon does not compose now** (a project no longer in `projects.toml`): its rows are removed in one claimed step (`remove_commitment_scopes`). The index describes exactly the scopes the daemon serves.
- **Stale, stated:** a scope is stale when any of its rows carries a checksum other than the MD5 of the ledger as read now, or when it has no row. A missing index file is therefore every scope stale, and is rebuilt whole. Nothing else decides it: not mtime, not size, not the word of whoever asked (AD-46).
- **Requests made during the check** (by the catch-up's or a client's appends) are taken after it, and a scope the check already made current is found current again and costs nothing.

### When the ledger is unreadable or malformed

- **Unreadable** (`OSError`, `DecryptionFailed`, `UnicodeError`, `ScopeResolutionError`): nothing is written for that scope, its existing rows stay with their old checksum, and one warning names the scope and the failure's type. The next append to that scope or the next start tries again.
- **Malformed lines:** the fold already skips and counts them; the rows are what the fold gives. A root a malformed line names gets `malformed_named = 1` when it has a readable `pending` line, and no row when it has none, exactly as 15c refuses or cannot find it.
- **A torn last line** (a write in progress, AD-47) is left out by `parse_ledger`, and the checksum includes its bytes. When the line completes the file's checksum changes, so the next check derives it.
- **The index itself unusable during a derivation** (`sqlite3.DatabaseError`, a missing table, another format): that derivation drops the index and rebuilds every composed scope, once; a drop that fails is logged, and the next start tries again.
- **Disk full, busy or stopped writer** on `replace_commitment_rows`: rolled back, the old rows stay, one warning. It is not retried on a timer, which would be the periodic pipeline AD-46 excludes: the next append to that scope or the next start derives it.
- **A kill partway** through a claimed step: SQLite rolls the transaction back when the file is next opened, and the next start finds the scope stale by its checksum.

### Dropping and rebuilding (for story 1h)

- **Three operations, in `pm_ai/core/commitment_index.py`, over `StoragePort`,** for 1h's `pm_ai.storage.reindex` to compose. 15b does not create `pm_ai.storage.reindex`: 1h owns it, runs after story 19, and covers all three indexes.
  - `commitment_index_snapshot(storage) -> tuple[IndexRow, ...]`: every row, every column, ordered by `(scope, commitment_id)`, so it does not depend on insertion or file order; an absent index is `()`;
  - `drop_commitment_index(storage)`: `StorageService.drop_commitment_index`, which first calls `assert_reindex_safe(frozenset({"commitment_index.db"}))` (`storage_tiers.py:479`), closes its connections, and removes the file and its `-wal` and `-shm`. The claim sidecar `commitment_index.db.lock` is never removed (AD-5). Dropping an absent index does nothing;
  - `rebuild_commitment_index(storage, scopes)`: the start check's steps over the scopes given, with every scope derived.
- **The rebuild is exact.** Same ledgers, same rows: every column comes from the fold of the same bytes, and no column depends on the clock, the zone or the order rows were written. 15b's own test proves snapshot, drop, rebuild, equal snapshot.
- **1h's `test_ad3_indexes_rebuild_from_markdown_without_loss`** keeps skipping on `pm_ai.storage.reindex` until 1h. When 1h lands, its snapshot includes this one.

### Who reads the index after 15b

- **Nothing in production reads its rows.** The start check and `derive` read only the stored checksums. Tests read the rows.
- **Every reader of commitments keeps reading the Markdown:** 15c's desk (`list`, `show`, every verb, `review`), completion and the terminal readers by path, the dashboard digest and Team Activity, and 15d's pages. None switches: a ledger is small, 15c lists two 5,000-line ledgers within 1 second, and switching would add a staleness question to readers that have none today.
- **The rule for the first reader (story 16), stated here so the index is never trusted blindly:** before trusting a scope's rows, a reader compares the stored checksum with `source_md5` of the ledger as it is now, and derives the scope again when they differ. This holds whoever wrote last: a terminal process, a hand edit, or a restore.
- **Plain reads for that reader and the tests:** `commitment_index_rows(scope=None)` and `commitment_index_checksums()` on `StorageService`, each on its own read-only connection.

### Time bounds and what is logged

- **The request path:** unchanged. The hook only records a scope; no command waits for the index, and no command's bound changes.
- **The claimed step:** one transaction per scope. Its duration at a 5,000-line ledger is measured by the build and recorded in this spec's Change Log, as AD-5 asks of each slice that adds a claimed step; a test fails it above 0.5 seconds.
- **Timed tests:** after a daemon append on a 5,000-line project ledger, its rows are current within 2 seconds; the start check over a stale 5,000-line personal and a stale 5,000-line project ledger finishes within 2 seconds.
- **Logged** in the diagnostic log (AD-24), counts and type names only, never a commitment's words, an owner, or an exception's text:
  - each derivation that wrote: `commitment index: <scope> derived again (<trigger|start>), <n> commitments`;
  - the start check, one line: `commitment index at start: <k> current, <j> derived again, <r> scopes removed, <u> unreadable`, plus `the index was unusable (<reason>); dropped and rebuilt` when it was;
  - each failure: `commitment index: <scope> not derived (<TypeName>); the next append or start tries again`.
- **Nothing goes to the event log:** indexing is not a domain event (AD-24).

### No command

15b adds no command, so it adds no `--json`, listing or next step. A rebuild by hand is `pm-ai reindex`, story 1h's and story 4's. A stale or unusable index repairs itself at the next start.

### What story 10a replaces (temporary, as `9a`'s timers are)

- **Thrown away by 10a:** `pm_ai/app/commitment_indexing.py` whole (the in-memory set of requested scopes and the start task), and the storage hook `on_commitments_appended` and its call in `append_commitments`. AD-46 then holds without exception: no change event from the write path.
- **Replaced by:** 10a's commitment indexing job, a queue row (AD-20), declaring `commitments_log.md` as its input and `commitment_index.db` as its output, woken by the watcher on that file (AD-46), deduplicated on `(job, artifact)`, and its boot reconciliation, watcher attached first.
- **Kept by 10a:** the schema, `index_rows`, `derive`, the storage calls, `source_md5`, and the three 1h operations. 10a's job is `derive(scope)` for the scope whose file changed.
- **Labelled:** the module's docstring names 10a as its replacement, as `ticks.py` does; `deferred-work.md` gains the entry.

### The architecture tests and the skip count

- **`EXPECTED_SKIPS` stays 6** (`tests/conftest.py:192`). `test_ad3_indexes_rebuild_from_markdown_without_loss` (`test_domain_invariants.py:578`) still skips on `pm_ai.storage.reindex` (1h's), and 15b creates no module of that name. `test_ad35_…` and `test_ad36_…` still skip on `pm_ai.core.commitments` (16's).
- **New, passing in 15b** (`tests/architecture/test_domain_invariants.py`):
  - `test_ad3_commitment_index_rebuilds_from_markdown_without_loss`: fixture ledgers in personal and a project, derived; snapshot, drop, rebuild; the snapshots are equal;
  - `test_ad3_commitment_index_drop_reaches_tier_3_only`: `drop_commitment_index` calls `assert_reindex_safe`, and the check with `operational.db` added raises `TierViolation` with no file removed;
  - `test_ad46_every_commitment_index_row_carries_its_ledgers_checksum`: after derivation every row's `source_md5` equals `source_md5` of its ledger's bytes; after one more line, every row of that scope reads as stale and no other scope's does;
  - `test_ad46_one_function_computes_source_checksums`: `hashlib.md5` appears in `pm_ai` only inside `source_md5` (`pm_ai/core/jobs.py`);
  - `test_ad35_commitment_index_rows_are_the_fold`: over every fixture ledger of `tests/core/test_commitment_log.py` and 15c's (undone marks, verdicts, revised lines, owner and due set, a malformed root, duplicates, a torn tail), each row equals the projection of `CommitmentLog.read(scope)`, column by column;
  - `test_ad45_indexing_writes_no_ledger`: a derivation makes no `append_commitments` call and does not fire the hook.
- **Unchanged and passing:** the encryption-policy row for `commitment_index.db` (`test_encryption_policy.py:57`), the path rows (`test_paths.py:162`), and the reindex-safety tests (`test_domain_invariants.py:1223`).
- **The storage-call classification** (`tests/slice/test_project_add_and_drop_through_daemon.py:267`) covers every new `StorageService` call: `replace_commitment_rows`, `remove_commitment_scopes` and `drop_commitment_index` in `HANDED_CALLS`, asking no `git` (the index is in the application scope, outside every working tree); `commitment_index_rows` and `commitment_index_checksums` in `PLAIN_READS`; `on_commitments_appended` in `NEVER_FROM_A_JOB` (the daemon's own, at start).
- **`.importlinter` is unchanged:** `core/commitment_index.py` imports `domain`, `ports` and `core` and no `sqlite3` (AD-1's contract forbids it in core); SQLite stays in `pm_ai/storage`; the temporary task is in `app`.
- **Naming** (1h's text checks): no identifier in `pm_ai/storage/` combines the ledger's name with a whole-file write. The index's names say `commitment_index`, never `commitments_log`.

### No real data

Tests use `11d`'s fixture names (`Example Weekly Sync-up`, `Alex Morgan`, `Sam Rivera`) and `example.com` handles. Nothing reads the PM's own `~/.pm-ai` or `~/.manager-ai`.

**Ask First:**

1. **An append made by a terminal process.** The PM runs `pm-ai proposal approve` or `pm-ai commitment done` at a terminal (not through the prompt): that process writes the ledger itself, and no daemon sees the append. 15c's spec said "15b's trigger runs after every append through the store, so 15c's lines are indexed too" (`15c:361`, `:699`), and FR-34's consequence says approval creates "an index row" (`prd.md:675`). Andrei's decision says the trigger is inside the daemon.
   - **(A, recommended) The daemon only.** A terminal's append is indexed at the daemon's next append to that scope or its next start. The index is stale until then, and harmless: nothing reads it in 15b, and its first reader (16) must compare checksums before trusting a scope anyway, because a hand edit leaves it stale in the same way. 15c's sentence is corrected by a Change Log line; FR-34's row still follows every approval, later for a terminal's.
   - **(B) Every process that appends derives the scope after its own append.** The index is current after every pm-ai write, matching 15c's sentence. The cost: every terminal mark and approval also opens and writes the index (inside 15c's 2-second bound, which then needs a timed test), and two processes write the index, under its claim. Hand edits still leave it stale, so 16's checksum rule is needed anyway.
   - **(C) A terminal's command asks the running daemon to derive the scope.** A new loopback request for something nothing reads yet, and it does nothing when no daemon runs.

**Never:**

- **The ledger:** no write to any `commitments_log.md`, no ledger claim taken, no second parser: rows come only from `CommitmentLog.read_with_bytes`, `parse_ledger` and `fold`.
- **The index:**
  - no row that is not the fold of the bytes whose checksum it carries;
  - no column computed from the clock or the display zone, and no "past due", "overdue" or "late" stored;
  - no commitment words, title or note in it;
  - no checksum anywhere but in the rows (never in `operational.db`, AD-46), and no per-index watermark;
  - no migration: a format change drops and rebuilds;
  - no encryption, and no master key or keychain asked for.
- **Triggers:**
  - no derivation on the request path, and no command waiting for one;
  - no periodic sweep or retry timer (AD-46);
  - no watcher (10a's);
  - no trigger from a Tier-3 write.
- **Readers:** no current reader switched from the Markdown to the index.
- **Scope of this slice:** no command, no `pm_ai.storage.reindex` (1h), no `pm_ai.core.commitments` (16), no page change (15d), no `people` scope indexed.
- **Logs:** no commitment words, owner, speaker or exception text in a log line, and no event-log entry.
- **No read of the PM's real store** in a test or a manual check.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Approval through the daemon | `proposal approve` at the prompt; personal ledger gains a `pending` line | one row: `state=pending`, `state_source=none`, `version` = the line's entry, `source_md5` = the file's MD5 | N/A |
| Mark through the daemon | `commitment done` at the prompt | the row: `state=fulfilled`, `state_source=pm`, `state_at` = the mark's `occurred`, new `version` and checksum | N/A |
| Due and owner set | `commitment due … friday`, `commitment owner … "Alex Morgan"` at the prompt | `due_date` set, `due_by_pm=1`, `due_estimated=0`; `owner="Alex Morgan"`, `owner_by_pm=1` | N/A |
| Altered with wording | `commitment altered … --what …` at the prompt | the mark's append and the `revised` line's append each request the scope; the final row has the replacement's `proposal_id` and `state=altered` | N/A |
| Undo | `done`, then `undo` within 4 h, at the prompt | the row's state is the one before the mark; `version` = the `undone` line's entry | N/A |
| Telemetry verdict | a hand-written `verdict state=fulfilled` line, then the next start | `state=fulfilled`, `state_source=telemetry` | N/A |
| Catch-up at start | items approved before 15a; the catch-up writes their lines | rows for them after the start check; the check finds that scope current and writes nothing more | N/A |
| Two quick appends | two marks on one scope within milliseconds | at most two derivations, never overlapping; the rows end as the fold of the file after both | N/A |
| Terminal append (option A) | `commitment done` at a terminal while the daemon runs | no derivation; the scope's rows keep the old checksum; derived at the daemon's next append to that scope or its next start | N/A |
| Hand edit, daemon down | a line edited by hand, then a start | the checksum differs; the scope is derived again; other scopes current | N/A |
| Nothing changed | a start with every ledger as last derived | every scope current; nothing written; one log line with the counts | N/A |
| Ledger deleted | a project's `commitments_log.md` removed while the daemon was down | its rows deleted at the start check | N/A |
| Project dropped | a project no longer in `projects.toml` at start | its rows removed in one step | N/A |
| Empty or absent ledger | personal has no ledger | no row for personal; no error; with no commitment anywhere, no index file is created | N/A |
| Malformed root | a malformed line names a root that has a `pending` line | its row has `malformed_named=1`; a root only a malformed line names has no row | N/A |
| Torn last line | the file ends in a line without its newline | the line is left out; the checksum covers its bytes; after it completes, the next check derives it | N/A |
| Same id in two ledgers | a hand-copied `pending` line in personal and a project | two rows, one per scope | N/A |
| Ledger unreadable | the project's ledger cannot be read at a derivation | nothing written for it; its old rows stay; one warning with the type name | logged; next append or start |
| Index not a database | the file holds other bytes | dropped through `assert_reindex_safe` and rebuilt from every scope; one log line | logged |
| Another format | `index_format` says 2 | dropped and rebuilt | logged |
| Disk full | the replace step fails to commit | rolled back; old rows stay; one warning | logged; next append or start |
| Kill mid-step | the daemon killed inside the replace | the transaction rolled back; the next start derives the scope again | N/A |
| Shutdown with requests waiting | scopes requested as the daemon stops | a step already queued runs or is refused by the stopped writer; the next start checks | logged |
| Hook raises | the callback raises | the append still succeeds and returns its size; one warning | logged |
| Concurrent mark | a 15c mark appends while a derivation of that scope is reading | the mark is not refused (no ledger claim held); its append requests the scope again; the rows end at the newer file | N/A |
| Key absent | no master key enrolled | the index is written as usual | N/A |
| Read of an absent index | `commitment_index_rows()` with no file | `()`; no file created | N/A |
| File mode | the index's first write | the file, `-wal` and `-shm` are `0600`; the enclave `0700` | N/A |
| Snapshot, drop, rebuild | fixture ledgers in personal and a project | equal snapshots | N/A |
| Snapshot order | the same rows inserted in another order | equal snapshots | N/A |
| Drop guard | a drop set with `operational.db` | `TierViolation`; nothing removed | raises |
| Rows are the fold | every fixture ledger | each row equals the projection of `CommitmentLog.read`, column by column | N/A |
| Timed trigger | a daemon append on a 5,000-line project ledger | its rows current within 2 s | N/A |
| Timed start | stale 5,000-line personal and project ledgers | the start check finishes within 2 s | N/A |
| Claimed step | replacing a 5,000-line ledger's rows | under 0.5 s; the measured duration recorded in the Change Log | N/A |
| People scope | anything | never derived, never a row | N/A |

</frozen-after-approval>

## Code Map

Anchors checked against `origin/main` at `7a097a7`.

- `pm_ai/domain/scope_model.py:486` `File("commitment_index.db", Tier.DERIVED, encrypted=False, gitignored=True)`
- `pm_ai/domain/storage_tiers.py:475` `TierViolation`; `:479` `assert_reindex_safe`
- `pm_ai/platform/paths.py:658` `commitment_index_store`
- `pm_ai/core/jobs.py:23` `idempotency_key` (`source_md5` goes beside it)
- `pm_ai/core/commitment_log.py:625` `Details`; `:659` `CommitmentLedger`; `:779` `parse_ledger`; `:904` `fold`; `:1051` `CommitmentLog`; `:1067` `read_with_bytes`
- `pm_ai/domain/commitments.py:102` `StateSource`; `:186` `Due`; `:237` `Commitment`
- `pm_ai/ports/__init__.py:807` `read_commitments`, `:810` `append_commitments` (the index calls are declared beside them)
- `pm_ai/storage/service.py:590` `_mkdir_private`; `:633` `_create_private_file`; `:1391` `_enter_wal`; `:2767` `StorageService`; `:4176` `exclusive`; `:4418` `append_commitments` (the hook's call); `:4443` `_claimed_unless_held`
- `pm_ai/app/commitments.py:376` `catch_up`; `:625` `CommitmentCatchUp`; `:1300` `CommitmentDesk._write`
- `pm_ai/app/daemon.py:1023` `attach_background`; `:1042` the catch-up's attachment; `:1046` `_lifespan`; `:1072` the catch-up's task
- `pm_ai/app/ticks.py:1` the temporary label's precedent
- `pm_ai/app/wiring.py:4103` `HANDED_CALLS`; `:4151` `append_commitments`; `:4189` `PLAIN_READS`; `:4227` `NEVER_FROM_A_JOB`
- `tests/architecture/test_domain_invariants.py:34` `mod`; `:578` AD-3's rebuild test (1h's skip); `:1223` reindex cannot reach Tier 2
- `tests/architecture/test_encryption_policy.py:57`; `tests/architecture/test_paths.py:162`
- `tests/slice/test_project_add_and_drop_through_daemon.py:267` the storage-call classification
- `tests/slice/test_commitment_commands.py:1083` `_fill`, the 5,000-line fixture filler
- `tests/conftest.py:192` `EXPECTED_SKIPS = 6`
- `ARCHITECTURE-SPINE.md:741` AD-45's queue sentence; `:747` AD-46's Binds; `:749` AD-46's Rule
- `SPEC.md:227` the one-mechanism constraint
- `derivation-services.md:69` per-entry MD5; `:109` the commitment indexing row; `:127` the `CommitmentLog` row
- `prd.md:675` FR-34's index-row consequence
- `15c-the-pms-commitment-commands.md:361`, `:699` the trigger requirement

## Tasks & Acceptance

**Execution:**
- [ ] **The checksum.** `core/jobs.py`: `source_md5`. Test: raw bytes, no normalisation (CRLF and LF differ).
- [ ] **The rows.** `pm_ai/core/commitment_index.py` (new): `IndexRow`, `INDEX_FORMAT`, `index_rows`, `derive`, `check`, `commitment_index_snapshot`, `drop_commitment_index`, `rebuild_commitment_index`, each through `StoragePort` with a `step` for handed writes, as `catch_up` takes one. Tests first: rows equal the fold.
- [ ] **The port.** `ports/__init__.py`: the five index calls beside `read_commitments`, with `CommitmentIndexUnusable` declared with them (AD-49).
- [ ] **The store.** `storage/service.py`: the index file opened lazily at `0600` in WAL, the schema and format row, `replace_commitment_rows`, `remove_commitment_scopes`, `drop_commitment_index` through `assert_reindex_safe`, `commitment_index_rows` and `commitment_index_checksums` on read-only connections that create nothing, `on_commitments_appended` and its call after a successful `append_commitments`.
- [ ] **The classification.** `app/wiring.py`: the six calls in `HANDED_CALLS`, `PLAIN_READS` and `NEVER_FROM_A_JOB`.
- [ ] **The temporary task.** `pm_ai/app/commitment_indexing.py` (new): `CommitmentIndexing` with the requested set, the start check after the catch-up, and the loop; the docstring labels it temporary and names 10a. `app/daemon.py`: built and hooked in `attach_background`, started in `_lifespan`.
- [ ] **Tests.**
  - `tests/core/test_commitment_index.py` (new): every column from the fold; the 15c fixture ledgers; snapshot order.
  - `tests/slice/test_commitment_index.py` (new): one test per matrix row, through `attach_background` and `_lifespan` as 15a's slice tests drive them; the timed tests; the `0600` modes; a terminal append not indexed until the next daemon append or start (under option A).
  - `tests/architecture/test_domain_invariants.py`: the six new tests; `tests/architecture/README.md` gains their rows.
- [ ] **The debt on record.** `_bmad-output/implementation-artifacts/deferred-work.md`: the temporary trigger and start task, owner 10a.
- [ ] **The contract.** Apply the amendments in Design Notes: bmad-spec re-derives `SPEC.md` and `derivation-services.md`, then the architecture skill amends the spine, then 15c's Change Log line.

**Acceptance Criteria:**
- Given a personal fixture item approved through the daemon, when the indexer has run, then `commitment_index.db` holds one row at `pending` whose `source_md5` is the MD5 of `commitments_log.md`.
- Given a ledger edited by hand while the daemon was stopped, when the daemon starts, then that scope is derived again and no other scope is written.
- Given a derived index, when it is snapshotted, dropped and rebuilt, then the two snapshots are equal.
- Given a drop set that includes `operational.db`, when the drop is attempted, then it raises `TierViolation` and no file is removed.
- Given the change, when `uv run pytest` runs, then everything passes with 6 skips.

## Spec Change Log

- **2026-10-26, created** from story 15's entry (its invoke notes and its 15b line), Andrei's decision of 2026-10-24 (a trigger inside the daemon until story 10, and a check at daemon start), 15a and 15c as built at `7a097a7`, 1h, `derivation-services.md` and the spine's AD-3, AD-5, AD-45, AD-46 and AD-47. One Ask First open.

## Design Notes

**Readiness check (2026-10-26, against `origin/main` at `7a097a7`).** Verdict: **ready; one question for Andrei, which changes only who triggers.**
- **Built as 15b needs:**
  - `CommitmentLog.read_with_bytes` returns the fold and the bytes it was folded from, so a row and its checksum come from one read;
  - `fold` takes no clock and no zone: the undo window compares two lines' `occurred`, and past due is computed by the card at display time. So rows can be clock-free and a rebuild exact;
  - `Details` already holds `version`, `state_at`, `due_by_pm`, `owner_by_pm` and `changed_at`;
  - every writer appends through `StorageService.append_commitments`: the recorder (`proposals.py:846`, `prompts.py:1178`, `entry.py:1939`), the catch-up (`daemon.storage` on the writer thread) and the desk (`CommitmentDesk._write`). One hook there covers them, and story 16's verdicts later;
  - the index is declared, its path resolved, its tier and encryption tested.
- **Missing, and added by 15b (none blocks):**
  - **no derived index exists at all,** so there is no pattern to follow: no SQLite file besides `operational.db`, no checksum function (`hashlib.md5` appears nowhere in `pm_ai`), no `pm_ai.storage.reindex`. 15b adds the first of each piece AD-46 names, and 1h, 18 and 10a reuse them;
  - **the daemon has no hook after an append.** Added as one callback, set only on the serve path.
- **Contradictions found:**
  - **15c's trigger sentence** (`15c:361`, `:699`: "after every append through the store") versus Andrei's "inside the daemon". A terminal process appends through its own store. Ask First 1;
  - **1h's anchors are stale:** 1h names `assert_reindex_safe` at `storage_tiers.py:175`; it is at `:479`. 1h's spec is not 15b's to edit; noted for 1h's readiness check;
  - **SQLite under the runtime extra:** the brief expected the index might need `sqlite-vec`. It does not: this index is plain SQLite from the standard library. `sqlite-vec` is for `vector_index/` (10a), so 15b adds no dependency and needs no extra beyond what the suite already requires.

**Why one file, not one per scope.** The scope model declares one `commitment_index.db` in the application tree, and the spine's tier tables name one. One per scope would put an index inside each project's `memory/` (gitignored, so harmless to git) but would add two declared nodes, which 1h makes an Ask First. The cost of one file is that it holds personal and project rows together, as `operational.db` already holds every scope's proposals. A reader filters by `scope`; the cross-scope wall (AD-38) is a rule about what a record cites, and no index row cites anything.

**Why the checksum covers the whole file.** The ledger is append-only, so any append changes the file, and the unit of re-derivation is one file (AD-46). Per-line offsets for an incremental append are allowed by AD-46 as an optimisation that must not change what a rebuild produces; 15b does not take it, because a 5,000-line ledger folds well inside the 2-second bound and a commitment's state depends on lines anywhere in the file.

**Why the trigger is in storage, not in each writer.** Three writers today and a fourth in story 16 all append through one call. A hook there cannot be forgotten by the next writer, and it fires only after bytes are written. It is a change event from the write path, which AD-46 forbids, so it is stated as a temporary exception and thrown away by 10a.

**Why readers do not switch.** Nothing needs it: 15c reads a ledger within its bounds. A reader that switched would have to handle a stale scope, which the Markdown readers never face. Story 16 is the first reader that wants a cross-scope "open, by due" query, and it brings the checksum rule with it.

**Accepted limits:**
- Until 10a, a terminal's append (under option A) and a hand edit are indexed late: at the daemon's next append to that scope, or its next start.
- A ledger with no commitment is read and folded at every check.
- A column story 16 needs is a format change: the index is dropped and rebuilt at the next start.

**Amendments this depends on.** Named, not applied. Applied with the build, in this order: bmad-spec re-derives `SPEC.md` and `derivation-services.md`; then the spine through the architecture skill; then 15c's Change Log. The PRD is unchanged: FR-34's "an index row in commitment\_index.db (Tier 3, disposable) with state \[PENDING\]" (`prd.md:675`) holds, at once for an approval through the daemon and at the next append or start for one made at a terminal.

*`SPEC.md`, re-derived by bmad-spec:*
- **`:227`**, after "pm-ai does not publish change events from its own write path, even though it is the single writer, because Markdown is hand-editable by design and a second pipeline for the same question can disagree with the first.", add: "Until story 10a, one stated exception (2026-10-26, 15b): the serving daemon derives a scope's commitment index again after each append it makes to that scope's commitments\_log.md, and checks every scope's index against its ledger's checksum once at start."
- CAP-34 is unchanged: its success already names "an indexed row at `[PENDING]`", and no capability is added.

*`derivation-services.md`, re-derived by bmad-spec:*
- **`:109`**, the commitment indexing row's trigger, "filesystem event on that file", becomes: "filesystem event on that file (story 10a); until then, a request inside the serving daemon after each append it makes, and a check at daemon start (`15b`, temporary)".
- **`:69`**, after "naming the source path and its MD5 as of the derivation that produced it.", add: "In `commitment_index.db` each row carries the MD5 of its scope's whole `commitments_log.md`, as read for the derivation that wrote it, and the scope names the file; a ledger with no commitment has no row and is derived again at every check (`15b`)."
- **`:127`**, the `CommitmentLog` row: "indexing (read)" becomes "indexing (read, `15b`)".

*Spine (`ARCHITECTURE-SPINE.md`), through the architecture skill:*
- **AD-45** (`:741`): after "**Every job is a row in the durable queue** (AD-20), never an in-memory timer.", add: "`[2026-10-26, Andrei, 15b]` Until story 10a, commitment indexing is the one derivation that runs without the queue: an in-memory task in the serving daemon (`pm_ai/app/commitment_indexing.py`), labelled temporary as `9a`'s timers are. 10a replaces it with a queued job and keeps its derivation, its schema and its stored checksums."
- **AD-46** (`:745-749`):
  - in the heading, `[NEW]` becomes `[NEW]` `[revised 2026-10-26]`;
  - **Binds:** add `15b`;
  - after "pm-ai publishes no change events from its own write path.", add: "`[2026-10-26, Andrei, 15b]` One stated exception, temporary until story 10a's watcher and queue replace it: in the serving daemon, each append to a `commitments_log.md` asks for that scope's commitment index to be derived again, and the daemon checks every scope's index rows against its ledger's checksum once at start. Appends another process makes, and hand edits, are seen at the daemon's next append to that scope or its next start, so a reader of the index compares the stored checksum with the ledger before it trusts a scope's rows."

*15c's spec (`stories/15c-the-pms-commitment-commands.md`), a Change Log line (under option A):*
- "**2026-10-26, 15b's spec:** 15b's trigger runs after each append the serving daemon makes, not after every append through the store (`:361`, `:699`); an append a terminal process makes is indexed at the daemon's next append to that scope or its next start (Andrei, 15b's Ask First)."

## Verification

**Commands:**
- `uv run pytest tests/core/test_commitment_index.py tests/slice/test_commitment_index.py tests/slice/test_commitments.py tests/slice/test_commitment_commands.py tests/slice/test_project_add_and_drop_through_daemon.py tests/architecture -q` -- expected: every matrix row passes; the six new architecture tests pass
- `uv run pytest -q` (in the background, about 7 minutes) -- expected: all pass; 6 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a scratch home (`HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1`), start the daemon, drop and summarise the recorded fixture into personal, and approve two items at the prompt. Then `sqlite3 <scratch>/.pm-ai/private/commitment_index.db 'select commitment_id, state, source_md5 from commitments'` and `md5 <scratch>/.manager-ai/memory/commitments_log.md`. Expected: two rows at `pending`, each carrying the file's MD5.
- Stop the daemon, add a comment line (`# note`) to the ledger by hand, which the reader skips, and start it again. Expected: the start log line says one scope derived again, and the rows carry the new MD5.
