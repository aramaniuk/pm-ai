# Reviewer — adversarial: two compliant units that still diverge (2026-08-27 update)

**Verdict: FIVE holes found, all closable, all applied.** Method: take AD-45, AD-46 and AD-47 as written, construct two units one level down that obey every word, and find where they still cannot interoperate.

## A1 — CRITICAL. Three components compute the source checksum and nothing pins how.

AD-46 requires every index entry to store its source file's MD5. Three places need that number: the **indexing job** (when it writes an entry), the **boot reconciler** (when it compares), and potentially a **shared accessor** (which already owns reads of that file under AD-45's rule 3).

Unit A hashes the raw bytes as stored. Unit B hashes after normalising line endings — defensible, since it is comparing *content* and a `\r\n` round-trip is not a semantic change. Both obey every AD. They disagree on every file forever: the reconciler sees a mismatch at every boot and re-indexes the whole tree, permanently, while both units pass their own tests.

**This project has already solved this exact problem once.** `pm_ai/core/jobs.py:14`, on idempotency keys: *"Two components computing the key must agree byte-for-byte, so the encoding is pinned here rather than left to whoever calls first."* The same discipline was missing here.

**Closed by:** AD-46 now names one function as the sole computer of the checksum, over the raw bytes as stored, with no normalisation of any kind.

## A2 — HIGH. Nothing says who assigns a watcher to an artifact, so the "partition" is unenforceable.

AD-46 requires one watcher per watched artifact and permits a watcher to cover several. But if watchers are created per-job — the obvious reading, since jobs declare the inputs — then `event_log/`, which search indexing and embedding both declare, gets **two** watchers, each unit believing it owns it. Both obey the AD. The partition it asserts is violated by construction, and a change arrives twice.

**Closed by:** the task manager owns the watcher-to-artifact map and derives it from the union of all jobs' `inputs()`. A job never creates a watcher.

## A3 — HIGH. Per-entry checksums and append-only files interact badly, and the resolution is unstated.

An append changes the whole file's MD5. If every entry derived from `event_log/2026-08.md` stores that file's checksum, then a single appended event invalidates **every entry from that segment** — with monthly segments, potentially thousands.

Unit A re-indexes the whole segment on each append: correct, quadratic over a month. Unit B stores a per-record offset and appends incrementally: also correct, and now the two indexes have different schemas and different rebuild semantics, so AD-3's rebuild guarantee means two different things. Both obey every AD.

**Closed by:** AD-46 now states that for an append-only artifact the unit of comparison is the whole file and re-indexing replaces that file's entries, with incremental handling named as an optimisation that must not change what a rebuild produces.

## A4 — HIGH. AD-47's staging can bypass AD-43's capture guard.

AD-43 requires asking git whether the capture directory is tracked *before* writing. AD-47 now writes into `transcripts/temp/` first. Unit A asks about `transcripts/`; unit B asks about `transcripts/temp/` — the directory it is actually about to write to, which is the more literal reading of AD-43. Their answers can differ, and a `.gitignore` negation line re-including a subdirectory is exactly the case AD-43's own rationale calls out.

**Closed by:** AD-47 states the guard is asked about the **final** capture directory, before staging, and that staging never re-asks. The ignore rule is a directory rule covering both.

## A5 — MEDIUM. AD-46 contradicts itself on the comparand.

Boot reconciliation was written as "mtime or digest against what the last derivation saw", while the same AD's checksum paragraph — and `storage-contract.md` — require MD5 because mtime is untrustworthy for precisely the class of writes the boot scan exists for (a restore from backup, a clock change, an editor preserving mtime). Two units could each pick one.

**Closed by:** MD5, stated once.

## Attempted and did not find a hole

- **Compaction's own record re-triggering indexing.** Compaction appends to `event_log/`, which is watched, so indexing runs again on the record itself. One extra pass, not a loop — the second run finds nothing further to do. Not worth an AD.
- **AD-45's leaf argument against AD-5's compaction.** Compaction reads and writes `event_log/`, a genuine self-edge, but its trigger is a schedule so the edge never fires. Already documented in AD-45 with the revisit condition.
- **Lazy key fetch (AD-6) against staged writes (AD-47).** A missing key fails before staging, leaving nothing behind. No interaction.
