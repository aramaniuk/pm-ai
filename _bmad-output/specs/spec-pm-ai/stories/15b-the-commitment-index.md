---
title: 'the commitment index'
type: 'feature'
created: '2026-10-26'
status: 'draft'
baseline_commit: '7a097a7'
review_loop_iteration: 1
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
- **Rows are the fold.** A scope's rows are computed from its ledger's bytes by 15a's and 15c's own `parse_ledger` and `fold`, through `CommitmentLog`, and each row stores the MD5 of exactly those bytes. No row holds a commitment's words or a person's name, and nothing in a row depends on the clock.
- **Re-derived after each append the serving daemon makes** to that scope (Andrei, 2026-10-27): the approval, the PM's commands and the catch-up all append through one storage call, and that call asks for the scope to be derived again.
- **Checked at daemon start:** every stale scope is derived again, rows of a scope positively gone from the enrolled projects are removed, and an unusable index file is dropped and rebuilt.
- **Nothing reads it yet.** Every reader of commitments keeps reading the Markdown. Story 16 is the first reader.
- **Temporary, as `9a`'s timers are:** the trigger and the start task are replaced by story 10a's job; the derivation, the schema and the checksums stay.

## Boundaries & Constraints

**Always:**

### Prerequisites and what is decided already

- **Built and relied on:**
  - `15a`: the line kinds, `parse_ledger`, `fold`, `CommitmentLog` (`core/commitment_log.py`), the store's `read_commitments` and `append_commitments`, the recorder and the catch-up at daemon start (`app/commitments.py`);
  - `15c`: `Details` per commitment (version, mark in force, `due_by_pm`, `changed_at`, `state_at`), the `undone` line, the desk's compare-and-append (`CommitmentDesk._write`);
  - `4r`: the daemon's writer thread and I/O pool; `1r`: `StorageService.exclusive`; `1s`: private files created at `0600`;
  - the scope model's `File("commitment_index.db", Tier.DERIVED, encrypted=False, gitignored=True)` (`scope_model.py:486`) and `ScopePaths.commitment_index_store` (`paths.py:658`).
- **Decided by Andrei, 2026-10-24, not reopened here:** the index is started by a trigger inside the daemon until story 10's queue, plus a check at daemon start.
- **Decided by story 15's entry, not reopened here:** the index is reconstructible from the Markdown alone; each row stores the MD5 of `commitments_log.md` as of the derivation that produced it, per row, not per index; the ledger is read through `CommitmentLog` and never parsed a second way.
- **The slices of story 15:** 15a (ledger, done), **15b (this spec)**, 15c (commands, done), 15d (surfaces, specced in parallel). 15b depends on nothing in 15d, and 15d depends on nothing here.
- **Decided by Andrei, 2026-10-27:** only the serving daemon derives the index. An append a terminal process makes is indexed at the daemon's next append to that scope or at its next start.

### Where it is, and what it covers

- **One file:** `~/.pm-ai/private/commitment_index.db`, the application scope's enclave, as the scope model already declares. Not one file per scope: the declaration is one `File` in the application tree, and a file per scope would add a node to two trees, which 1h makes an Ask First (`ARTIFACT_TIER`).
- **The scope set: the scopes the daemon composed at its start** (AD-5's refresh point for `projects.toml`): personal and every project the registry enrolled then, captured once as `ProposalWorld.scopes` from `open_world` (`app/proposal_reads.py`), the same set and the same read 15a's catch-up uses. Never a `people` scope or the application scope: neither keeps a ledger (`NotACommitmentScope`, AD-4, AD-28). The hook ignores a scope outside this set.
- **Not encrypted.** Tier 3 is "explicitly not encrypted" (`storage-contract.md`), nothing encrypted is a database, and the declaration says `encrypted=False`. So the index needs no master key: it is written when the key is absent or the keychain is locked. Protection is the file's `0600`, the enclave's `0700`, and full-disk encryption, as for `operational.db`.
- **Created at the first write only,** at `0600` before SQLite opens it, in WAL mode, so its `-wal` and `-shm` take the same mode (story 1s). A read never creates an absent index (below).

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

  `IndexRow` (`pm_ai/core/commitment_index.py`) has exactly these fields, in this order.
- **The values, pinned so a rebuild compares byte for byte:**
  - an instant is `at.astimezone(timezone.utc).isoformat(timespec="microseconds")`, always ending `+00:00`; a date is `YYYY-MM-DD`; a flag is `0` or `1`; empty is SQL `NULL`;
  - one function writes them, used by `index_rows` and by no test: the tests write their expected values by hand in the same form.
- **One secondary index** on `(scope, state, due_date)`, for story 16's "open, by due".
- **What a row leaves out, deliberately:**
  - **names: the owner and the speaker.** `storage-contract.md:62` says the indexes hold "lookup structures rather than recoverable text", and a name is recoverable text about a person. Story 16's lifecycle job asks which commitments are open and when they fall due, which no name answers; a reader that shows who owns a commitment reads the ledger. `owner_by_pm` goes too: it says where an owner came from, and without the owner no reader has a use for it;
  - **the words** (`what`, the title, the PM's note, the due as said), for the same reason. Every reader that shows a card reads the ledger for it;
  - **anything that depends on the clock or the display zone.** "Past due" is not stored: a reader computes it from `due_date`, `due_estimated` and its own today, as 15c's card does. So a rebuild on another day or in another zone gives the same rows;
  - **the ledger's bad-line counts** (malformed, orphans, duplicates, conflicts, refused). They describe the file, not a commitment, and 15c's `list` shows them from the Markdown;
  - **a work item reference** story 16 may need. A column added later is a format change (below), not a migration.
- **Table `index_format`, exactly one row, two integer columns:** `index_format`, the columns' format (`INDEX_FORMAT = 1`, in `pm_ai/core/commitment_index.py`), and `fold_version`, the fold's (`FOLD_VERSION = 1`, a new constant beside `fold` in `core/commitment_log.py`). A change to what the fold computes from a ledger (a new line kind, a changed rule) bumps `FOLD_VERSION` in the same change, as `fold`'s docstring then says, so an index derived by the older fold is never served as current. They say which rows the file holds, nothing about freshness: freshness is only ever the per-row checksum.
- **A different format, or a file that is not a usable index, is dropped and rebuilt, never migrated.** Tier 3 is disposable (AD-3), and story `1i`'s schema versioning is for Tier 2.

### The checksum

- **One function:** `source_md5(data: bytes) -> str` in `pm_ai/core/jobs.py`: `hashlib.md5(data, usedforsecurity=False).hexdigest()`, 32 lower-case hex characters over the raw bytes as stored, with no normalisation of any kind (AD-46). It is the only MD5 in `pm_ai`; story 18's index and 10a's boot reconciliation call the same function.
- **Over the bytes that were folded:** one `CommitmentLog.read_with_bytes(scope)` gives both the fold and the bytes, which are `read_commitments`' return value, and the checksum is of those bytes. A row therefore always says "I am the fold of the file whose MD5 is X". It is never computed from a second read.
- **The ledger is plaintext** (the scope model declares `commitments_log.md` unencrypted, AD-6), so those bytes are the file as the PM sees it. If a ledger were ever encrypted, the checksum would run over the ciphertext, and that is a format change.
- **Every row of a scope carries the same checksum,** because the unit of derivation is the whole file (AD-46: "for an append-only artifact the unit of comparison is the whole file").
- **It is change detection only,** never integrity or authenticity (AD-46). Nothing may read it as either.

### Deriving one scope (the only way rows are written)

`derive(scope)`, in `pm_ai/core/commitment_index.py`, through `StoragePort`:
1. **Read** the scope's ledger with `CommitmentLog.read_with_bytes` (a plain file read) and take `source_md5` of the bytes.
2. **Compare** with what the index holds for that scope (`commitment_index_checksums`, a plain read):
   - **rows that all carry that checksum:** the scope is **current**; nothing is folded or written (AD-45: a request whose inputs are unchanged does no work);
   - **no rows, and the fold has no commitment** (an absent ledger, an empty one, or only bad lines): also **current**, nothing written;
   - **otherwise** the scope is stale: go on.
3. **Compute the rows,** `index_rows(scope, ledger, md5)`, a pure function: one row per entry of `ledger.commitments`, each column from the table above.
4. **Replace** the scope's rows in one claimed step: `replace_commitment_rows(scope, rows, expected_md5s)`, where `expected_md5s` is the set of checksums step 2 saw for the scope (empty when it saw no rows). Under `exclusive` on `commitment_index.db`, in one SQLite transaction, it:
   1. checks the format row;
   2. reads the scope's stored checksums again, and writes nothing unless they still equal `expected_md5s`. When they differ (another writer derived the scope meanwhile), it does nothing, says so, and the scope stays requested;
   3. writes only the difference, by `(commitment_id, version)`: deletes rows whose commitment is gone, inserts or replaces rows that are new or whose version changed, and leaves the others;
   4. sets every remaining row of the scope to the new `source_md5` in one statement.

   All or nothing: a failure rolls back and leaves the scope's old rows, which still carry their old checksum and so still read as stale.

- **Steps 1 to 3 run on a worker, step 4 on the writer thread** (AD-5, AD-19). In the daemon, `replace_commitment_rows` is a handed call and the reads are plain reads.
- **An absent ledger** (`read_commitments` gives `b""`) folds to no commitment, so a scope that had rows has them deleted. This is how a ledger deleted while the daemon was down stops being served (AD-46). **A project whose repository root directory does not exist is not an absent ledger:** it is unreadable (below), and its rows stay. `derive` checks the root before step 1.
- **One connection per claimed step.** Each write step opens its own connection to the index path and closes it when the step ends, so a file dropped and recreated by another store or process is always the file at the path; no connection outlives a step. Reads likewise open, read and close.

### What starts a derivation

- **After each append the serving daemon makes to a ledger.** `StorageService.append_commitments` (`service.py:4418`) is the one storage call every writer uses: 15a's recorder (an approval through the daemon, and a repeated approve), 15a's catch-up at start, and 15c's desk (every mark, `due`, `owner`, `undo`, and `altered`'s mark and its `revised` line). Story 16's verdicts will use it too.
- **The hook:** the serving daemon gives its store one callback, `StorageService.on_commitments_appended(callback)` (`None` clears it), set in `attach_background` (`daemon.py:1023`), which only the serve path calls. A store built by a command at a terminal has none. It is a `StorageService` call only, never declared on `StoragePort`.
  - **When it fires:** in a `finally`, whenever the append got past its size check, whether it then returned or raised. An append that raised after its bytes reached the file is thereby covered, as 15c's re-read may report it made. An append refused before the size check (`LedgerChanged`, `NotACommitmentScope`, `UnprotectedCaptureDir`, a held claim) fires nothing: no byte was written.
  - **On the writer thread, after the claim is released,** with the scope.
  - **The callback does exactly one thing:** `loop.call_soon_threadsafe(indexer.request, scope)`. The set of requested scopes is touched only on the daemon's event loop, never from the writer thread, so it needs no lock. The writer call stays short (AD-5), and the client's command never waits for the index. A callback that raises is logged and never fails the append: the line is written.
  - **Before the loop is attached** (an append between `attach_background` and `_lifespan`), the callback records nothing but a count, logged with the start check's line, and the start check, which runs after, covers every scope.
- **The indexer task** (`CommitmentIndexing`, `pm_ai/app/commitment_indexing.py`):
  - the requested scopes are an ordered set (a `dict` keyed by scope), served first in, first out, so a scope requested again and again cannot starve the others;
  - it removes a scope from the set **before** step 1's read, so a request arriving during that scope's derivation adds it again, and it is derived once more after the scopes ahead of it;
  - derivations run one at a time, never overlapping, so rows from an older read never replace rows from a newer one. This is AD-46's queue-level deduplication on `(job, artifact)`, held in memory until 10a;
  - **it never dies:** each derivation is wrapped in `try/except Exception`, which logs the type name and goes on to the next scope. Only its own cancellation ends it.
- **Appends a terminal process makes, and hand edits, start nothing** (Andrei, 2026-10-27). They are seen at the daemon's next append to that scope, which derives the whole file, or at its next start.
- **The indexer never writes a ledger.** It holds no ledger claim, so it can never make a ledger writer meet `LedgerChanged` or `ArtifactBusy`, and its own writes go to Tier 3, which nothing watches, so it cannot start itself (AD-45).

### Options not chosen

Andrei chose the daemon only (2026-10-27). Not chosen: every appending process deriving the scope itself, and a terminal command asking the running daemon to derive it.

### The check at daemon start

- **When:** once per start, as a one-shot task in `_lifespan` (`daemon.py:1046`), after 15a's catch-up has finished. It waits on the catch-up's task with `asyncio.wait({task})`, which returns whatever the task's outcome (an exception or a cancellation inside it is the catch-up's, already logged), so only the indexer's own cancellation can stop it. When there is no catch-up task (`pm_ai_commitments` absent), it runs at once. No timer and no polling (AD-46). It never delays serving and never stops the daemon starting: a failure is logged, and the next start tries again.
- **What it does:**
  1. **The file:** an absent index is created at the first write. An unusable index (below) is dropped and rebuilt from every scope. Logged once, by reason.
  2. **Each scope of the scope set:** `derive(scope)`.
  3. **Each scope with rows that is not in the scope set:** its rows are removed in one claimed step (`remove_commitment_scopes`), **only when the registry read at start succeeded** (`registry_fault` is `None`) and the scope is a project positively absent from it. When the registry could not be read, no rows are removed, and the log line says so.
- **Stale, stated:** a scope is stale when any of its rows carries a checksum other than the MD5 of the ledger as read now, or when it has no row and its ledger folds to at least one commitment. Nothing else decides it: not mtime, not size, not the word of whoever asked (AD-46). A missing index file is therefore every scope with a commitment stale, and is rebuilt whole.
- **Requests made during the check** (by the catch-up's or a client's appends) are taken after it, and a scope the check already made current is found current again and costs one read and one hash.

### The index file: unusable, busy, or absent

- **Unusable** means only these, and nothing else:
  - `sqlite3.DatabaseError` other than `sqlite3.OperationalError` (the file is not a database, or is corrupt);
  - the file has some of the index's tables but not all;
  - `index_format` holds zero rows or several, or a value that is not an integer;
  - a different `index_format` or `fold_version`.
- **An unusable index is dropped and rebuilt once.** `drop_commitment_index`, then `rebuild_commitment_index` over the scope set. **When the drop or the rebuild fails,** the index is marked unusable in memory until the next start: every later derivation in this run is skipped with one log line (not one per append), and nothing tries a full rebuild on every append.
- **Busy or disk:** `sqlite3.OperationalError` (busy, locked, full, I/O error, cannot open) and `OSError` on a write step roll the transaction back, keep the rows, and log one warning. Not unusable, so nothing is dropped. It is not retried on a timer, which would be the periodic pipeline AD-46 excludes: the next append to that scope or the next start derives it.
- **A file with no tables at all** (created but never written, as by a kill right after creation) gets the schema and the format row at the next write step. Not unusable.
- **An absent index:** `commitment_index_checksums` returns `{}` and `commitment_index_rows` returns `()` without opening anything, because SQLite's read-only open (`mode=ro`) raises on an absent file and a read-write open would create it. Reads never create an absent file. For an existing index in WAL mode a read may make `-wal` and `-shm` appear; they take the database's `0600`.
- **A kill partway** through a claimed step: SQLite rolls the transaction back when the file is next opened, and the next start finds the scope stale by its checksum.

### When the ledger is unreadable or malformed

- **Unreadable** (`OSError`, `UnicodeError`, `ScopeResolutionError`, or a project root that does not exist): nothing is written for that scope, its existing rows stay with their old checksum, and one warning names the scope and the failure's type. The next append to that scope or the next start tries again. `DecryptionFailed` is not in the list: the ledger is never encrypted, so a decryption failure here would be a defect, and it reaches the derivation's `except Exception` and is logged as one.
- **Malformed lines:** the fold already skips and counts them; the rows are what the fold gives. A root a malformed line names gets `malformed_named = 1` when it has a readable `pending` line, and no row when it has none, exactly as 15c refuses or cannot find it.
- **A torn last line** (a write in progress, AD-47) is left out by `parse_ledger`, and the checksum includes its bytes. When the line completes the file's checksum changes, so the next check derives it.

### Shutdown

- **One outcome:** `_lifespan`'s `finally` first clears the hook (`on_commitments_appended(None)`), then cancels the indexer with the other tasks.
  - requested scopes not yet derived are dropped, with one log line counting them: `commitment index: <n> requested scope(s) not derived at shutdown; the next start checks them`;
  - a derivation at its read (steps 1 to 3) is abandoned;
  - a replace step already handed to the writer thread runs to its end, as AD-5's shutdown runs every step already queued;
  - an append after the hook was cleared starts nothing;
  - the next start's check derives whatever is stale.

### Dropping and rebuilding (for story 1h)

- **Three operations, in `pm_ai/core/commitment_index.py`, over `StoragePort`,** for 1h's `pm_ai.storage.reindex` to compose. 15b does not create `pm_ai.storage.reindex`: 1h owns it, runs after story 19, and covers all three indexes.
  - `commitment_index_snapshot(storage) -> tuple[IndexRow, ...]`: every row, every column, ordered by `(scope, commitment_id)`, so it does not depend on insertion or file order; an absent index is `()`;
  - `drop_commitment_index(storage)`: `StorageService.drop_commitment_index`, a claimed step under `exclusive` on `commitment_index.db`, which first calls `assert_reindex_safe` on the artifact key of the very path it removes (`storage_tiers.py:479`), then removes the file and its `-wal` and `-shm`. The claim sidecar `commitment_index.db.lock` is never removed (AD-5). Dropping an absent index does nothing;
  - `rebuild_commitment_index(storage, scopes)`: `derive` for each scope given, with every one of them derived (the stored checksums are not consulted). **The other scopes' rows are untouched:** removing rows of scopes no longer composed is the start check's step, not the rebuild's. 1h passes the whole scope set.
- **The rebuild is exact.** Same ledgers, same rows: every column comes from the fold of the same bytes, the values are pinned, and no column depends on the clock, the zone or the order rows were written. 15b's own test proves snapshot, drop, rebuild, equal snapshot.
- **1h's `test_ad3_indexes_rebuild_from_markdown_without_loss`** keeps skipping on `pm_ai.storage.reindex` until 1h. When 1h lands, its snapshot includes this one.

### The port

- **`StoragePort` gains five calls,** beside `read_commitments` and `append_commitments`, with `CommitmentIndexUnusable` declared with them (AD-49):
  - `replace_commitment_rows(scope, rows, expected_md5s)`: the conditional replace (a write);
  - `remove_commitment_scopes(scopes)`: delete every row of those scopes (a write);
  - `drop_commitment_index()`: the guarded drop (a write);
  - `commitment_index_rows(scope=None)`: the rows, one scope or all, ordered by `(scope, commitment_id)` (a read);
  - `commitment_index_checksums()`: per scope, the set of stored checksums and the row count (a read).
- **`on_commitments_appended`** is on `StorageService` only. The daemon's own wiring calls it; no job and no core code can.

### Who reads the index after 15b

- **Nothing in production reads its rows.** The start check and `derive` read only the stored checksums. Tests read the rows.
- **Every reader of commitments keeps reading the Markdown:** 15c's desk (`list`, `show`, every verb, `review`), completion and the terminal readers by path, the dashboard digest and Team Activity, and 15d's pages. None switches: a ledger is small, 15c lists two 5,000-line ledgers within 1 second, and switching would add a staleness question to readers that have none today.
- **The rule for the first reader (story 16), stated here so the index is never trusted blindly:** before trusting a scope's rows, a reader compares the stored checksum with `source_md5` of the ledger as it is now, and derives the scope again when they differ. This holds whoever wrote last: a terminal process, a hand edit, or a restore.

### Time bounds and what is logged

- **The request path:** unchanged. The hook only posts a request to the loop; no command waits for the index, and no command's bound changes.
- **The claimed step** stays inside AD-5's bound for a writer call (a client waits at most 1 second for the writer thread): it writes only the rows that changed and one checksum update. Its duration is measured by the build at a 5,000-line and a 50,000-line ledger, with one commitment changed and with the scope derived whole, and recorded in this spec's Change Log, as AD-5 asks of each slice that adds a claimed step. A test fails it above 0.5 seconds in each case.
- **Timed tests:** after a daemon append on a 5,000-line project ledger, its rows are current within 2 seconds; the start check over a stale 5,000-line personal and a stale 5,000-line project ledger finishes within 2 seconds. The derivation of a 50,000-line ledger (on a worker, off the writer thread) is measured and recorded; no bound is promised for it.
- **Logged** in the diagnostic log (AD-24), counts and type names only, never a commitment's words, a name, or an exception's text:
  - each derivation that wrote: `commitment index: <scope> derived again (<trigger|start>), <n> rows written, <m> removed`;
  - a conditional replace that found the scope changed: `commitment index: <scope> changed meanwhile; derived again`;
  - the start check, one line: `commitment index at start: <k> current, <j> derived again, <r> scopes removed, <u> unreadable, <e> requests before start`, plus `the index was unusable (<reason>); dropped and rebuilt` when it was, and `scopes not removed: the projects registry could not be read` when that applies;
  - each failure: `commitment index: <scope> not derived (<TypeName>); the next append or start tries again`;
  - the latch, once: `commitment index unusable and not rebuilt (<TypeName>); derivations skipped until the next start`;
  - shutdown's count line (above).
- **Nothing goes to the event log:** indexing is not a domain event (AD-24).

### No command

15b adds no command, so it adds no `--json`, listing or next step. A rebuild by hand is `pm-ai reindex`, story 1h's and story 4's. A stale or unusable index repairs itself at the next start.

### What story 10a replaces (temporary, as `9a`'s timers are)

- **Thrown away by 10a:** `pm_ai/app/commitment_indexing.py` whole (the in-memory set of requested scopes and the start task), and the storage hook `on_commitments_appended` and its call in `append_commitments`. AD-46 then holds without exception: no change event from the write path.
- **Replaced by:** 10a's commitment indexing job, a queue row (AD-20), declaring `commitments_log.md` as its input and `commitment_index.db` as its output, woken by the watcher on that file (AD-46), deduplicated on `(job, artifact)`, and its boot reconciliation, watcher attached first.
- **Kept by 10a:** the schema, `index_rows`, `derive`, the storage calls, `source_md5`, `FOLD_VERSION`, and the three 1h operations. 10a's job is `derive(scope)` for the scope whose file changed.
- **Labelled:** the module's docstring names 10a as its replacement, as `ticks.py` does; `deferred-work.md` gains the entry.

### The architecture tests and the skip count

- **`EXPECTED_SKIPS` stays 6** (`tests/conftest.py:192`). `test_ad3_indexes_rebuild_from_markdown_without_loss` (`test_domain_invariants.py:578`) still skips on `pm_ai.storage.reindex` (1h's), and 15b creates no module of that name. `test_ad35_…` and `test_ad36_…` still skip on `pm_ai.core.commitments` (16's).
- **New, passing in 15b** (`tests/architecture/test_domain_invariants.py`):
  - **`test_ad3_commitment_index_rebuilds_from_markdown_without_loss`:**
    - fixture ledgers in personal and a project are derived; the first snapshot is non-empty and holds exactly the expected `(scope, commitment_id)` pairs from both;
    - after the drop, the db, `-wal` and `-shm` are absent and the snapshot is `()`;
    - the rebuild wrote every scope (its report counts each as derived, none as current);
    - the snapshot after it equals the first;
    - a second rebuild with a clock a week later and another `display_timezone` (and machine `TZ`) gives an equal snapshot;
    - the set of `IndexRow`'s field names equals this spec's column table.
  - **`test_ad3_commitment_index_drop_reaches_tier_3_only`:** the guarded key is tied to the removed path. With both redirected to `operational.db`, the drop raises `TierViolation`, and `operational.db`, its `-wal` and its `-shm` still exist.
  - **`test_ad46_every_commitment_index_row_carries_its_ledgers_checksum`:** over a fake store whose `read_commitments` returns bytes A on the first call and A plus one line on later calls: the rows are non-empty, every row's `source_md5` is `source_md5(A)`, and the rows equal the fold of A; at the next check every row of that scope reads as stale and no other scope's does.
  - **`test_ad46_one_function_computes_source_checksums`:** an AST scan of `pm_ai` finds `hashlib.md5`, `from hashlib import md5` and `hashlib.new("md5")` nowhere but inside `source_md5` (`pm_ai/core/jobs.py`).
  - **`test_ad35_commitment_index_rows_are_the_fold`:** one ledger per named case, each row's expected values written in the test from that case's `Commitment` and `Details` fields, without importing `index_rows` or any shared mapping helper. The cases: a plain `pending`; a PM `done` then `undone`; a hand-written telemetry `verdict`; a `revised` line from an edit; `due_set` and `owner_set` (the due columns change, no owner column exists); a malformed line naming a root with a `pending` line, and one naming a root without; the same id in two ledgers; a torn last line.
  - **`test_ad45_indexing_writes_no_ledger`:**
    - a stale scope is derived: rows are written, the ledger's bytes are unchanged, the hook did not fire, and `holds_claim(scope=…, artifact=COMMITMENTS_LOG)` is `False` inside `read_commitments`;
    - a concurrent mark: `derive` is held inside `read_commitments` on an `Event` while 15c's `done` runs on another thread; `done` is not refused as busy; after the release, the final rows are the fold of the ledger after the mark.
- **Unchanged and passing:** the encryption-policy row for `commitment_index.db` (`test_encryption_policy.py:57`), the path rows (`test_paths.py:162`), and the reindex-safety tests (`test_domain_invariants.py:1223`).
- **The file's modes** (`tests/architecture/test_private_modes.py`, beside `:457-470`): with a connection open on the index, the db, `-wal` and `-shm` are `0600` and the enclave `0700`.
- **The storage-call classification** (`tests/slice/test_project_add_and_drop_through_daemon.py:267`) covers every new `StorageService` call:
  - `replace_commitment_rows`, `remove_commitment_scopes` and `drop_commitment_index` in `HANDED_CALLS`, asking no `git` (the index is in the application scope, outside every working tree);
  - `commitment_index_rows` and `commitment_index_checksums` in `PLAIN_READS`;
  - `on_commitments_appended` in `NEVER_FROM_A_JOB`: the daemon's own, at start and at shutdown, and not on `StoragePort`.
- **`.importlinter` is unchanged:** `core/commitment_index.py` imports `domain`, `ports` and `core` and no `sqlite3` (AD-1's contract forbids it in core); SQLite stays in `pm_ai/storage`; the temporary task is in `app`.
- **Naming** (1h's text checks): no identifier in `pm_ai/storage/` combines the ledger's name with a whole-file write. The index's names say `commitment_index`, never `commitments_log`.

### No real data

Tests use `11d`'s fixture names (`Example Weekly Sync-up`, `Alex Morgan`, `Sam Rivera`) and `example.com` handles. Nothing reads the PM's own `~/.pm-ai` or `~/.manager-ai`.

**Ask First:** none open. The one question, who derives the index after an append made by a terminal process, was decided by Andrei on 2026-10-27: the daemon only (above).

**Never:**

- **The ledger:** no write to any `commitments_log.md`, no ledger claim taken, no second parser: rows come only from `CommitmentLog.read_with_bytes`, `parse_ledger` and `fold`.
- **The index:**
  - no row that is not the fold of the bytes whose checksum it carries;
  - no column computed from the clock or the display zone, and no "past due", "overdue" or "late" stored;
  - no commitment words, title, note, owner or speaker in it;
  - no checksum anywhere but in the rows (never in `operational.db`, AD-46), and no per-index watermark;
  - no migration: a format change drops and rebuilds;
  - no drop for a busy or full disk, and no full rebuild on every append;
  - no encryption, and no master key or keychain asked for;
  - no read that creates the file.
- **Triggers:**
  - no derivation on the request path, and no command waiting for one;
  - no periodic sweep, retry timer or polling (AD-46);
  - no watcher (10a's);
  - no trigger from a Tier-3 write;
  - no touch of the requested set off the event loop.
- **Readers:** no current reader switched from the Markdown to the index.
- **Scope of this slice:** no command, no `pm_ai.storage.reindex` (1h), no `pm_ai.core.commitments` (16), no page change (15d), no `people` scope indexed, no rows removed on a registry that could not be read.
- **Logs:** no commitment words, name or exception text in a log line, and no event-log entry.
- **No read of the PM's real store** in a test or a manual check.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Approval through the daemon | `proposal approve` at the prompt; personal ledger gains a `pending` line | one row: `state=pending`, `state_source=none`, `version` = the line's entry, `source_md5` = the file's MD5 | N/A |
| Mark through the daemon | `commitment done` at the prompt | the row: `state=fulfilled`, `state_source=pm`, `state_at` = the mark's `occurred`, new `version`; every row of the scope carries the new checksum | N/A |
| Due set | `commitment due … friday` at the prompt | `due_date` set, `due_by_pm=1`, `due_estimated=0` | N/A |
| Owner set | `commitment owner … "Alex Morgan"` at the prompt | the row's `version` and checksum change; no column holds the name | N/A |
| Altered with wording | `commitment altered … --what …` at the prompt | the mark's append and the `revised` line's append each request the scope; the final row has the replacement's `proposal_id` and `state=altered` | N/A |
| Undo | `done`, then `undo` within 4 h, at the prompt | the row's state is the one before the mark; `version` = the `undone` line's entry | N/A |
| Telemetry verdict | a hand-written `verdict state=fulfilled` line, then the next start | `state=fulfilled`, `state_source=telemetry` | N/A |
| Catch-up at start | items approved before 15a; the catch-up writes their lines | rows for them after the start check; the check finds that scope current and writes nothing more | N/A |
| Two quick appends | two marks on one scope within milliseconds | at most two derivations, never overlapping; the rows end as the fold of the file after both | N/A |
| Hot scope | personal requested repeatedly while a project is requested once | the project is derived in its turn (first in, first out) | N/A |
| Append raised after writing | the append's bytes reached the file, then it raised | the hook fired; the scope is derived | N/A |
| Append refused early | `LedgerChanged` at the size check | no hook; nothing derived | N/A |
| Terminal append | `commitment done` at a terminal while the daemon runs | no derivation; the scope's rows keep the old checksum; derived at the daemon's next append to that scope or its next start | N/A |
| Hand edit, daemon down | a line edited by hand, then a start | the checksum differs; the scope is derived again; other scopes current | N/A |
| Nothing changed | a start with every ledger as last derived, personal having no ledger | every scope current, personal included (no rows, no commitment); nothing written; the start line counts them all as current | N/A |
| Ledger deleted | a project's `commitments_log.md` removed while the daemon was down | its rows deleted at the start check | N/A |
| Project root missing | an enrolled project's repository directory is gone | unreadable: its rows stay; one warning | logged |
| Project dropped | a project no longer in a readable `projects.toml` at start | its rows removed in one step | N/A |
| Registry unreadable | `projects.toml` cannot be read at start | no rows removed; the start line says why | logged |
| Empty or absent ledger | personal has no ledger | no row for personal; current; with no commitment anywhere, no index file is created | N/A |
| Malformed root | a malformed line names a root that has a `pending` line | its row has `malformed_named=1`; a root only a malformed line names has no row | N/A |
| Torn last line | the file ends in a line without its newline | the line is left out; the checksum covers its bytes; after it completes, the next check derives it | N/A |
| Same id in two ledgers | a hand-copied `pending` line in personal and a project | two rows, one per scope | N/A |
| Ledger unreadable | the project's ledger cannot be read at a derivation | nothing written for it; its old rows stay; one warning with the type name | logged; next append or start |
| Derivation raises | an unexpected exception inside one derivation | logged by type; the indexer goes on to the next scope | logged |
| Index not a database | the file holds other bytes | dropped and rebuilt from every scope; one log line | logged |
| Index partly made | one table present, the other missing; or `index_format` empty | unusable: dropped and rebuilt | logged |
| Index with no tables | an empty SQLite file | the schema and format row created at the next write; not dropped | N/A |
| Another format | `index_format` 2, or `fold_version` 2 | dropped and rebuilt | logged |
| Drop or rebuild fails | the drop raises `OSError` | latched unusable until the next start; later derivations skipped with one log line | logged once |
| Busy or full | `sqlite3.OperationalError` on the replace step | rolled back; old rows stay; nothing dropped; one warning | logged; next append or start |
| Changed meanwhile | the stored checksums differ from step 2's when the replace runs | nothing written; the scope stays requested and is derived again | logged |
| Dropped by another store | a second store drops the index while the daemon's store is running, then a derivation | the next step opens the file at the path; the rows are visible through the path | N/A |
| Kill mid-step | the daemon killed inside the replace | the transaction rolled back; the next start derives the scope again | N/A |
| Request before the loop | an append after `attach_background`, before `_lifespan` | counted; covered by the start check | N/A |
| Shutdown with requests waiting | scopes requested as the daemon stops | hook cleared first; pending scopes dropped with one count line; a replace already handed to the writer completes; the next start checks | logged |
| Hook raises | the callback raises | the append still succeeds and returns its size; one warning | logged |
| No catch-up task | `pm_ai_commitments` absent | the start check runs at once | N/A |
| Catch-up failed | the catch-up task raised | the start check still runs | N/A |
| Concurrent mark | a 15c mark appends while a derivation of that scope is reading | the mark is not refused (no ledger claim held); its append requests the scope again; the rows end at the newer file | N/A |
| Key absent | no master key enrolled | the index is written as usual | N/A |
| Read of an absent index | `commitment_index_rows()`, `commitment_index_checksums()` with no file | `()` and `{}`; nothing opened; no file created | N/A |
| File mode | the index with a connection open | the file, `-wal` and `-shm` are `0600`; the enclave `0700` | N/A |
| Snapshot, drop, rebuild | fixture ledgers in personal and a project | equal snapshots; the drop leaves no db, `-wal` or `-shm` | N/A |
| Rebuild another day and zone | a clock a week later and another display zone | equal snapshots | N/A |
| Rebuild a subset | `rebuild_commitment_index(storage, [personal])` | personal's rows written; the project's rows untouched | N/A |
| Snapshot order | the same rows inserted in another order | equal snapshots | N/A |
| Drop guard | the guarded key and the removed path both `operational.db` | `TierViolation`; `operational.db`, `-wal` and `-shm` still exist | raises |
| Rows are the fold | each named case | each row equals the values written in the test from `Commitment` and `Details` | N/A |
| Timed trigger | a daemon append on a 5,000-line project ledger | its rows current within 2 s | N/A |
| Timed start | stale 5,000-line personal and project ledgers | the start check finishes within 2 s | N/A |
| Claimed step | 5,000-line and 50,000-line ledgers, one commitment changed and whole | each under 0.5 s; the measured durations recorded in the Change Log | N/A |
| People scope | anything | never derived, never a row | N/A |

</frozen-after-approval>

## Code Map

Anchors checked against `origin/main` at `7a097a7`.

- `pm_ai/domain/scope_model.py:486` `File("commitment_index.db", Tier.DERIVED, encrypted=False, gitignored=True)`
- `pm_ai/domain/storage_tiers.py:475` `TierViolation`; `:479` `assert_reindex_safe`
- `pm_ai/platform/paths.py:658` `commitment_index_store`
- `pm_ai/core/jobs.py:23` `idempotency_key` (`source_md5` goes beside it)
- `pm_ai/core/commitment_log.py:625` `Details`; `:659` `CommitmentLedger`; `:779` `parse_ledger`; `:904` `fold` (`FOLD_VERSION` beside it); `:1051` `CommitmentLog`; `:1067` `read_with_bytes`
- `pm_ai/domain/commitments.py:102` `StateSource`; `:186` `Due`; `:237` `Commitment`
- `pm_ai/ports/__init__.py:807` `read_commitments`, `:810` `append_commitments` (the five index calls are declared beside them)
- `pm_ai/storage/service.py:590` `_mkdir_private`; `:633` `_create_private_file`; `:1391` `_enter_wal`; `:2767` `StorageService`; `:4176` `exclusive`; `:4418` `append_commitments` (the hook's `finally`); `:4443` `_claimed_unless_held`; `:4455` `_compare_and_append` (the size check)
- `pm_ai/app/proposal_reads.py:42` `ProposalWorld` (`registry_fault` `:48`, `scopes` `:55`); `:114` `open_world`
- `pm_ai/app/commitments.py:376` `catch_up`; `:625` `CommitmentCatchUp`; `:1300` `CommitmentDesk._write`
- `pm_ai/app/daemon.py:1023` `attach_background`; `:1042` the catch-up's attachment; `:1046` `_lifespan`; `:1072` the catch-up's task
- `pm_ai/app/ticks.py:1` the temporary label's precedent
- `pm_ai/app/wiring.py:4103` `HANDED_CALLS`; `:4151` `append_commitments`; `:4189` `PLAIN_READS`; `:4227` `NEVER_FROM_A_JOB`
- `tests/architecture/test_domain_invariants.py:34` `mod`; `:578` AD-3's rebuild test (1h's skip); `:1223` reindex cannot reach Tier 2
- `tests/architecture/test_private_modes.py:457-470` the SQLite sidecar modes
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
- [ ] **The checksum.** `core/jobs.py`: `source_md5` with `usedforsecurity=False`. Test: raw bytes, no normalisation (CRLF and LF differ).
- [ ] **The fold's version.** `core/commitment_log.py`: `FOLD_VERSION = 1` beside `fold`, and the bump rule in `fold`'s docstring.
- [ ] **The rows.** `pm_ai/core/commitment_index.py` (new): `IndexRow`, `INDEX_FORMAT`, the pinned value writer, `index_rows`, `derive`, `check`, `commitment_index_snapshot`, `drop_commitment_index`, `rebuild_commitment_index`, each through `StoragePort` with a `step` for handed writes, as `catch_up` takes one. Tests first: the named fold cases.
- [ ] **The port.** `ports/__init__.py`: the five index calls beside `read_commitments`, with `CommitmentIndexUnusable` declared with them (AD-49).
- [ ] **The store.** `storage/service.py`: a connection per step, the index file created at `0600` in WAL, the schema and format row, the unusable rules, the conditional difference-only `replace_commitment_rows`, `remove_commitment_scopes`, `drop_commitment_index` as a claimed step through `assert_reindex_safe`, `commitment_index_rows` and `commitment_index_checksums` that open nothing for an absent file, `on_commitments_appended` and its `finally` in `append_commitments`.
- [ ] **The classification.** `app/wiring.py`: the six calls in `HANDED_CALLS`, `PLAIN_READS` and `NEVER_FROM_A_JOB`.
- [ ] **The temporary task.** `pm_ai/app/commitment_indexing.py` (new): `CommitmentIndexing` with the ordered set on the loop, the start check after `asyncio.wait` on the catch-up, the loop, the unusable latch, the shutdown count; the docstring labels it temporary and names 10a. `app/daemon.py`: built and hooked in `attach_background`, started in `_lifespan`, the hook cleared first in its `finally`.
- [ ] **Tests.**
  - `tests/core/test_commitment_index.py` (new): the pinned values; snapshot order; the subset rebuild; the conditional replace.
  - `tests/slice/test_commitment_index.py` (new): one test per matrix row; the timed tests; the 50,000-line measurement; a terminal append not indexed until the next daemon append or start.
  - **The wiring slice test:** the very `state` `attach_background` filled is passed to `_lifespan`; an item is approved through `rig.path.prompt`; `commitment_index_rows` then holds its row. The test builds no indexer and calls no hook itself. Two more: the callback fired after `attach_background` and before `_lifespan` (covered by the start check), and one fired during shutdown (starts nothing; counted).
  - `tests/architecture/test_domain_invariants.py`: the six new tests; `tests/architecture/test_private_modes.py`: the modes test; `tests/architecture/README.md` gains their rows.
- [ ] **The debt on record.** `_bmad-output/implementation-artifacts/deferred-work.md`: the temporary trigger and start task, owner 10a.
- [ ] **The contract.** Apply the amendments in Design Notes: the PRD through bmad-prd; then bmad-spec re-derives `SPEC.md` and `derivation-services.md`; then the architecture skill amends the spine; then 15c's Change Log line.

**Acceptance Criteria:**
- Given a personal fixture item approved through the daemon, when the indexer has run, then `commitment_index.db` holds one row at `pending` whose `source_md5` is the MD5 of `commitments_log.md`.
- Given a ledger edited by hand while the daemon was stopped, when the daemon starts, then that scope is derived again and no other scope is written.
- Given a derived index, when it is snapshotted, dropped and rebuilt, then the two snapshots are equal and non-empty.
- Given a drop whose key and path are `operational.db`, when it is attempted, then it raises `TierViolation` and `operational.db` and its sidecars remain.
- Given the change, when `uv run pytest` runs, then everything passes with 6 skips.

## Spec Change Log

- **2026-10-26, created** from story 15's entry (its invoke notes and its 15b line), Andrei's decision of 2026-10-24 (a trigger inside the daemon until story 10, and a check at daemon start), 15a and 15c as built at `7a097a7`, 1h, `derivation-services.md` and the spine's AD-3, AD-5, AD-45, AD-46 and AD-47. One Ask First open.
- **2026-10-26, review (three lenses: adversarial, edge case, verification gap), fixes decided by the coordinating session: frozen block edited (recorded here).** The Ask First stayed open with Andrei.
  - **The open question:** every sentence that depends on option A is marked so, and "What options B and C change" lists the replacements; the PRD is amended under A rather than said to hold.
  - **Privacy:** `owner`, `speaker` and `owner_by_pm` dropped; story 16 needs none, and `storage-contract.md:62` keeps names out of indexes.
  - **The file:** "unusable" defined narrowly; `OperationalError` is busy or disk, never a drop; an absent file is read as empty without opening it; a file with no tables gets its schema, a partial one is unusable; the sidecars' modes tested with a connection open; `fold_version` stored beside `index_format`; a failed drop or rebuild latches until the next start; a connection per claimed step, so a drop by another store is safe.
  - **The derivation:** it never kills the indexer; `usedforsecurity=False`; instants pinned to UTC microseconds; `DecryptionFailed` dropped from the failure list (the ledger is plaintext); the replace is conditional on the checksums step 2 saw and writes only the changed rows; a scope with no row and no commitment is current; a missing project root is unreadable, not deleted; no rows removed when the registry could not be read; a subset rebuild leaves other scopes alone.
  - **Concurrency and lifecycle:** the callback only posts to the loop; requests before the loop are covered by the start check; the set is first in, first out, and a scope leaves it before its read; the hook fires in a `finally` once past the size check; the start check waits on the catch-up with `asyncio.wait`; shutdown clears the hook first and has one outcome.
  - **Bounds:** the claimed step measured at 5,000 and 50,000 lines.
  - **The port:** its five calls named; the hook on `StorageService` only.
  - **Tests:** the AD-3 test checks a non-empty snapshot, the removed files, a whole rebuild, another day and zone, and the field set; the fold test writes its expected values per named case; the checksum test uses a store that changes between reads; the drop guard ties the key to the path; the no-ledger-write test checks the claim and a concurrent mark; a wiring slice test through `attach_background` and `_lifespan`; the single-MD5 test is an AST scan.
  - **Anchors:** `derivation-services.md:127`'s amendment anchored on its unique phrase.
- **2026-10-27, Ask First answered: DECIDED 2026-10-27 (Andrei), frozen block edited (recorded here).** Option A: only the serving daemon derives the index; an append a terminal process makes is indexed at the daemon's next append to that scope or at its next start. The Ask First is closed, the option markers are plain statements, the B and C replacement list is reduced to one line saying they were not chosen, and the FR-34 amendment stays as named.

## Design Notes

**Readiness check (2026-10-26, against `origin/main` at `7a097a7`).** Verdict: **ready; one question for Andrei, which changes only who triggers** (decided 2026-10-27: the daemon only).
- **Built as 15b needs:**
  - `CommitmentLog.read_with_bytes` returns the fold and the bytes it was folded from, so a row and its checksum come from one read;
  - `fold` takes no clock and no zone: the undo window compares two lines' `occurred`, and past due is computed by the card at display time. So rows can be clock-free and a rebuild exact;
  - `Details` already holds `version`, `state_at`, `due_by_pm` and `changed_at`;
  - every writer appends through `StorageService.append_commitments`: the recorder (`proposals.py:846`, `prompts.py:1178`, `entry.py:1939`), the catch-up (`daemon.storage` on the writer thread) and the desk (`CommitmentDesk._write`). One hook there covers them, and story 16's verdicts later;
  - the scope set and the registry's failure are already one read (`open_world`), used by the catch-up;
  - the index is declared, its path resolved, its tier and encryption tested.
- **Missing, and added by 15b (none blocks):**
  - **no derived index exists at all,** so there is no pattern to follow: no SQLite file besides `operational.db`, no checksum function (`hashlib.md5` appears nowhere in `pm_ai`), no `pm_ai.storage.reindex`. 15b adds the first of each piece AD-46 names, and 1h, 18 and 10a reuse them;
  - **the daemon has no hook after an append.** Added as one callback, set only on the serve path.
- **Contradictions found:**
  - **15c's trigger sentence** (`15c:361`, `:699`: "after every append through the store") versus Andrei's "inside the daemon". A terminal process appends through its own store. Decided by Andrei on 2026-10-27: the daemon only, and 15c gets a Change Log line;
  - **1h's anchors are stale:** 1h names `assert_reindex_safe` at `storage_tiers.py:175`; it is at `:479`. 1h's spec is not 15b's to edit; noted for 1h's readiness check;
  - **SQLite under the runtime extra:** the brief expected the index might need `sqlite-vec`. It does not: this index is plain SQLite from the standard library. `sqlite-vec` is for `vector_index/` (10a), so 15b adds no dependency.

**Why one file, not one per scope.** The scope model declares one `commitment_index.db` in the application tree, and the spine's tier tables name one. One per scope would put an index inside each project's `memory/` (gitignored, so harmless to git) but would add two declared nodes, which 1h makes an Ask First. The cost of one file is that it holds personal and project rows together, as `operational.db` already holds every scope's proposals. A reader filters by `scope`; the cross-scope wall (AD-38) is a rule about what a record cites, and no index row cites anything.

**Why the checksum covers the whole file, and only changed rows are rewritten.** The ledger is append-only, so any append changes the file, and the unit of re-derivation is one file (AD-46). The fold runs over the whole file, because a commitment's state depends on lines anywhere in it; but the claimed step writes only rows whose version changed, plus one checksum update for the scope, so the writer thread's work grows with what changed rather than with the file. AD-46 allows that as long as a rebuild produces the same rows, which the AD-3 test checks.

**Why the replace is conditional.** Only the daemon writes the index, and its derivations are serial, so the condition never fails there. It costs one read inside the transaction and makes a second writer (1h's `pm-ai reindex`, a test's second store) unable to overwrite newer rows with older ones.

**Why the trigger is in storage, not in each writer.** Three writers today and a fourth in story 16 all append through one call. A hook there cannot be forgotten by the next writer, and it fires only once bytes may have been written. It is a change event from the write path, which AD-46 forbids, so it is stated as a temporary exception and thrown away by 10a.

**Why `FOLD_VERSION` is stored rather than relying on `INDEX_FORMAT`.** The columns and the fold change for different reasons and in different files. A rule that "`INDEX_FORMAT` is bumped whenever the fold changes" asks whoever edits `fold` in `commitment_log.py` to remember a constant in another module; a constant beside `fold` is where that editor already is.

**Why readers do not switch.** Nothing needs it: 15c reads a ledger within its bounds. A reader that switched would have to handle a stale scope, which the Markdown readers never face. Story 16 is the first reader that wants a cross-scope "open, by due" query, and it brings the checksum rule with it.

**Accepted limits:**
- Until 10a, a terminal's append and a hand edit are indexed late: at the daemon's next append to that scope, or its next start.
- A ledger with no commitment and no rows is read, hashed and folded at every check.
- A column story 16 needs is a format change: the index is dropped and rebuilt at the next start.
- An index the daemon could not drop or rebuild stays unused until the next start.

**Amendments this depends on.** Named, not applied. Applied with the build, in this order: the PRD through bmad-prd; then bmad-spec re-derives `SPEC.md` and `derivation-services.md`; then the spine through the architecture skill; then 15c's Change Log. The sentences are exact.

*PRD (`prd.md`), through bmad-prd:*
- **FR-34 consequence** (`:675`): in "> * Approved spoken commitment creates a valid, structured Markdown entry in the meeting's scope's commitments\_log.md and an index row in commitment\_index.db (Tier 3, disposable) with state \[PENDING\].", "and an index row in commitment\_index.db (Tier 3, disposable) with state \[PENDING\]" becomes "and an index row in commitment\_index.db (Tier 3, disposable) with state \[PENDING\], written by the serving daemon at once for an approval it makes, and at its next append to that scope or its next start for one made at a terminal (2026-10-26, 15b)".

*`SPEC.md`, re-derived by bmad-spec:*
- **`:227`**, after "pm-ai does not publish change events from its own write path, even though it is the single writer, because Markdown is hand-editable by design and a second pipeline for the same question can disagree with the first.", add: "Until story 10a, one stated exception (2026-10-26, 15b): the serving daemon derives a scope's commitment index again after each append it makes to that scope's commitments\_log.md, and checks every scope's index against its ledger's checksum once at start."
- CAP-34 is unchanged: its success already names "an indexed row at `[PENDING]`", and no capability is added.

*`derivation-services.md`, re-derived by bmad-spec:*
- **`:109`**, the commitment indexing row's trigger, "filesystem event on that file", becomes: "filesystem event on that file (story 10a); until then, a request inside the serving daemon after each append it makes, and a check at daemon start (`15b`, temporary)".
- **`:69`**, after "naming the source path and its MD5 as of the derivation that produced it.", add: "In `commitment_index.db` each row carries the MD5 of its scope's whole `commitments_log.md`, as read for the derivation that wrote it, and the scope names the file; a scope with no row is current when its ledger folds to no commitment (`15b`)."
- **`:127`**, the `CommitmentLog` row: "the lifecycle job (append verdicts, story 16), indexing (read)" becomes "the lifecycle job (append verdicts, story 16), indexing (read, `15b`)".

*Spine (`ARCHITECTURE-SPINE.md`), through the architecture skill:*
- **AD-45** (`:741`): after "**Every job is a row in the durable queue** (AD-20), never an in-memory timer.", add: "`[2026-10-26, Andrei, 15b]` Until story 10a, commitment indexing is the one derivation that runs without the queue: an in-memory task in the serving daemon (`pm_ai/app/commitment_indexing.py`), labelled temporary as `9a`'s timers are. 10a replaces it with a queued job and keeps its derivation, its schema and its stored checksums."
- **AD-46** (`:745-749`):
  - in the heading, `[NEW]` becomes `[NEW]` `[revised 2026-10-26]`;
  - **Binds:** add `15b`;
  - after "pm-ai publishes no change events from its own write path.", add: "`[2026-10-26, Andrei, 15b]` One stated exception, temporary until story 10a's watcher and queue replace it: in the serving daemon, each append to a `commitments_log.md` asks for that scope's commitment index to be derived again, and the daemon checks every scope's index rows against its ledger's checksum once at start. Appends another process makes, and hand edits, are seen at the daemon's next append to that scope or its next start, so a reader of the index compares the stored checksum with the ledger before it trusts a scope's rows."

*15c's spec (`stories/15c-the-pms-commitment-commands.md`), a Change Log line:*
- "**2026-10-26, 15b's spec:** 15b's trigger runs after each append the serving daemon makes, not after every append through the store (`:361`, `:699`); an append a terminal process makes is indexed at the daemon's next append to that scope or its next start (Andrei, 2026-10-27, 15b's Ask First)."

## Verification

**Commands:**
- `uv run pytest tests/core/test_commitment_index.py tests/slice/test_commitment_index.py tests/slice/test_commitments.py tests/slice/test_commitment_commands.py tests/slice/test_project_add_and_drop_through_daemon.py tests/architecture -q` -- expected: every matrix row passes; the six new architecture tests and the modes test pass
- `uv run pytest -q` (in the background, about 7 minutes) -- expected: all pass; 6 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a scratch home (`HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1`), start the daemon, drop and summarise the recorded fixture into personal, and approve two items at the prompt. Then `sqlite3 <scratch>/.pm-ai/private/commitment_index.db 'select commitment_id, state, source_md5 from commitments'` and `md5 <scratch>/.manager-ai/memory/commitments_log.md`. Expected: two rows at `pending`, each carrying the file's MD5.
- Stop the daemon, add a comment line (`# note`) to the ledger by hand, which the reader skips, and start it again. Expected: the start log line counts personal as derived again and every other scope (projects with no ledger included) as current, and the rows carry the new MD5.
