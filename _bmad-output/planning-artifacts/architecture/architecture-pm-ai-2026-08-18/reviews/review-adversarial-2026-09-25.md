# Adversarial review: the 2026-09-25 spine update

- **Target:** `ARCHITECTURE-SPINE.md`, working-tree diff against `HEAD` (branch `architecture/ad-6-timing-and-python-3-14`)
- **Changed statements reviewed:** AD-6's new paragraph on when the two disabled-encryption announcements happen; the Stack table's Python row; the watchdog row; the closed Enforcement row ("Composition is eager…")
- **Lens:** build two units, one level down, that each obey every AD as written and still come out incompatible. Only holes this change creates or leaves open are counted.
- **Checked against the code:** `pm_ai/app/wiring.py` (`_choose_crypto`, `_RecordedPlaintext`, `_writer`, `_record_disabled_encryption`, `application_storage`, `onboard_project`), `pm_ai/app/entry.py` (setup's writer), `pm_ai/storage/service.py` (`_replace`, `_create_exclusively`, `append_event_log`), `tests/architecture/test_cipher.py`

## Verdict

**Accept after tightening.** The Python and watchdog rows agree with `pyproject.toml`, `.python-version`, mypy and `AGENTS.md`, and nothing else in the spine or SOLUTION-DESIGN still says 3.13. The AD-6 amendment matches what the code does. It has three real holes, though. Each lets two units that follow the spine to the letter disagree about how many entries get written, which process decides, or whether the explaining entry survives. Separately, the closed Enforcement row claims more than the test measured.

## Findings

### F1 — High. The warning and the entry can end up in different processes, and the text no longer says which process decides

**What changed:** the old text said *"the daemon emits a CLI banner and an `event_log/` entry"*. The new text ties the warning to *"every command"* and the entry to *"a writer's first protected write"*. It does not say which process reads the flag for each one.

**Two conforming units:**
- **Unit A:** a thin CLI client, built to AD-7 ("every other process is a thin client"). It prints the warning on every command, as the new AD-6 says, based on `encryption_off()` in its own environment. AD-6 names `pm_ai.platform.environment` as the one place the flag is read, and the CLI is a composition root.
- **Unit B:** the long-lived daemon that owns the writer. It reads the same function in its own environment and records before its first plaintext protected write.

**Result:** a shell with the variable set talks to a daemon started without it. The user is told credentials are going out in plaintext, and they are not. The reverse case is the dangerous one: a daemon started with the variable set, driven from a clean shell. It writes plaintext credentials, while every command the user types prints nothing. The event-log entry does exist, but AD-6's own text says that entry is "weeks old by the time anyone wonders". So the one announcement meant for the person at the keyboard now comes from the wrong process.

**It is already partly true today with no daemon.** The warning prints only inside `build()` (`wiring.py:303-304`). `application_storage` and `onboard_project` each choose their own cipher from `encryption_off()` and never warn. On a machine with no project enrolled, composition stops before `build()`, so `pm-ai setup` gets a disabled, recording cipher and prints nothing. It does not write a protected file today (`config.toml` is plaintext). Even so, "the console warning prints on every command" is false for every command that stops before `build()`. The first setup step that stores a connector token would turn that into a plaintext credential written with no console warning.

**Close it:** tighten AD-6. *The process that owns the writer is the only one that reads the flag, and it owns both announcements. A thin client shows the posture the writer reports, never the value in its own environment. Wherever a disabled cipher is chosen, the warning is printed in the same place.* Until the daemon exists, "the same place" means `_choose_crypto`, not `build()`.

### F2 — High. "One entry per writer" leaves the number of entries up to how many writers the code happens to build, and nothing says writers in one process share one decision

**What changed:** the amendment says *"One entry per writer, not per process … every writer the composition root builds is bound this way"*. That treats several writers per process as normal. AD-5 still says *"a single storage service … owns every write"*, and nothing in AD-5 or AD-6 limits how many writers a process may build or when.

**Two conforming units:**
- **Unit A:** `SetupSession` (`entry.py:242-264`) builds a **new** writer through `application_storage(...)` on each method call: `read_config`, then `write_config`. Each gets its own `_RecordedPlaintext` with its own "already recorded" flag. Suppose a later setup step writes a protected artifact, say a connector token, through `write_config`, then through a second helper that builds another writer. That setup run appends two `security` lines. Both units obey AD-6: one entry per writer.
- **Unit B:** the daemon builds one writer for the whole process and appends one line.

**Result:** how many entries a command writes depends on how many writers a module happens to build, and that is a coding style, not a decision. It is the same kind of growth that 1p closed ("a line per command, burying the one that mattered"), now in the form "a line per helper call". The memlog records "per writer, not per process" as Andrei's decision, and this finding does not reopen it. The hole is that "writer" has no lifetime in the spine, so the bound the decision was meant to give can be undone without breaking any rule.

**The decision itself is also read in three places.** `build()` takes an `encryption_disabled` override (`wiring.py:296`), while `application_storage` (`:1062`) and `onboard_project` (`:1178`) call `encryption_off()` themselves. In production they agree, because the environment is the same. But nothing in the spine stops two writers in one process from holding different ciphers. Unit A could seal `private/config.json` while Unit B, in the same process, writes the same file in plaintext, so the file's format would depend on which writer wrote it last.

**The binding guarantee reaches only one file.** `test_every_writer_wiring_builds_goes_through_the_one_that_binds` counts `StorageService(` in the text of `wiring.py` only. A writer built in `entry.py`, or in any other `pm_ai/app` module (AD-30 makes the whole package the composition root), with a plain `PlaintextCrypto()` would not refuse, because only `_RecordedPlaintext` refuses when unbound. It would write protected files in plaintext with no entry, and every rule and test would still pass.

**Close it:** tighten AD-6, and also AD-5 or AD-30:
1. *Each process decides once whether encryption is off, in the composition root, and every writer that process builds uses that answer.*
2. *A writer lives as long as the command or daemon it serves. It is built once per process per resolver, never per call.* Or, if per-call writers must stay, the "already recorded" flag moves to one recorder object shared by every writer in the process. That keeps the flag off module globals (AD-30) and gives one entry per process in practice.
3. *The only pass-through cipher the composition root may hand to a writer is the recording one.* Enforce it over the whole `pm_ai.app` package, not over the text of one file.

### F3 — High. "Must be impossible" does not survive compaction, which is the only thing allowed to destroy Tier 1

**What changed:** the amendment makes this an invariant: *"a plaintext protected file with no entry explaining it must be impossible."* The entry goes in the application scope's `event_log/`, which is Tier 1 and bounded by FR-37 compaction (AD-3, AD-5).

**Two conforming units:**
- **Unit A:** a compactor built exactly to AD-5. It replaces sealed application-scope segments with a milestone summary, and it "carries forward the compaction entries of the segments it replaces". That is the only kind of entry AD-5 tells it to keep.
- **Unit B:** the recorder from AD-6, which wrote the `security` line into a segment that has since been sealed.

**Result:** once compaction runs, the `security` line is gone. The plaintext protected file it explained is still on disk. Restarting turns encryption back on for *new* writes, but nothing re-seals a file that was already written. That is exactly the state the amendment calls impossible, and both units followed the spine to get there. The Tier-1 bound was the reason 1p moved the entry, so the entry's own survival under that bound belongs in the same rule.

**Close it:** extend AD-5's carry-forward rule to name `security` entries next to compaction entries, or any category the spine calls an explanation of what is on disk. Otherwise, narrow AD-6's claim to "no plaintext protected file is *written* without an entry".

### F4 — Medium. What an entry means and what it contains are unstated, so the recorder and its reader can disagree

**Two conforming units:**
- **Unit A:** the recorder. It writes `protection=encryption-at-rest`, `disabled_by=environment variable` (`wiring.py:541-549`), names no file, and records **before** the bytes are published. `_replace` and `_create_exclusively` call `encrypt` before `_publish`, so a publish that then fails leaves an entry for a write that never happened. Examples are a capture refused with `EEXIST` under AD-23 and a full disk. The flag is set by then, so later writes through that writer add nothing.
- **Unit B:** `pm-ai doctor`, the flag's second consumer named in AD-6, or any later "which protected files are plaintext?" check. AD-6 describes the entry as the thing that "explains a plaintext credential", so this unit reasonably reads it as "a protected file was written in plaintext at this time" and may expect a path.

**Result:** the reader either over-reports, when the write failed, or looks for a file name the entry never carries. Neither unit breaks a rule. AD-6 names only the category and the scope.

**Close it:** one sentence in AD-6: *the entry records a posture — "from here on, this writer may write protected files in plaintext" — not a file; it names no path; it may precede a write that then fails. Finding which protected files are actually in plaintext is done with the classifier and an envelope check, never with the log.* If a path is wanted, the entry's fields have to be listed in the spine.

### F5 — Medium. The closed Enforcement row claims more than was measured

The row now says *"building no longer writes"* and *"`--help`, a mistyped command, `project` and `doctor` change no file."* The test that encodes it, `test_a_command_that_writes_nothing_leaves_every_file_alone`, runs each command **once first**, because *"the first command on a fresh home creates the operational database"*. It measures only the second run, and `_tree` compares files only, not directories. So what was measured is: on a machine already in use, these commands change no file, and no command writes the `security` line. On a fresh home, the first `--help` still creates a Tier-2 file.

**Why it matters under this lens:** a later unit reading "building no longer writes" can reasonably put `build()` on a read-only diagnostic path, for example a doctor probe run against a backup or a read-only home, and it would fail there. The row's title claim, that Tier 1 is no longer changed, holds and is tested. The broader wording does not.

**Close it:** reword to *"building no longer writes to Tier 1. On a fresh home the first command still creates the Tier-2 operational database, and a second run of these commands changes no file."* It is the same fact, stated only as far as the test proves it.

### F6 — Low. The Python row pins the whole system to a detail the spine itself calls seed

Line 1015 (Deferred) keeps "the fingerprinted rule version that pins … the Unicode version" out of the spine on purpose, with "one module with one owner". The new Python row makes that same detail the reason for a system-wide pin, and says moving to 3.15 "is its own slice" without saying whose.

**Two conforming units:** the sanitizer's owner, acting within their seed authority, re-measures and bumps the fingerprint to Unicode 17. The Stack row still says "3.14 exactly" because of Unicode 16. Or the reverse: a dependency upgrade moves to 3.15 as "its own slice" and misses that the sanitizer has to re-measure first. The code already catches both through the fingerprint test, so the risk is low. But the row's reason and the Deferred entry now disagree about who owns the decision.

**Close it:** in the Python row, add *"the gate is `test_the_rule_fingerprint_matches_its_version`; the sanitizer's owner decides the move to another minor version"*, or point to the Deferred line.

## Checked and clean

- Python row against `pyproject.toml` (`>=3.14,<3.15`, mypy `3.14`), `.python-version` (`3.14`) and `AGENTS.md`: consistent. `SOLUTION-DESIGN.md` has no stale 3.13.
- Watchdog row: "≥3.9 against this project's 3.14" is correct and constrains nothing new.
- Record, then write, under a lock, with retry on a failed append (`_RecordedPlaintext.encrypt`): matches "if the entry cannot be written, the protected file is not written either". Tested by `test_a_failed_append_keeps_the_protected_file_off_the_disk` and `…_is_retried_rather_than_forgotten`.
- Scope: the entry goes to application scope, which agrees with AD-38's rule that the scope owning the subject gets the record. The daemon's own posture is application-scope subject matter.
- Recursion: the recorder's own append goes to a plaintext ledger (`_append` refuses a sealed one), so recording cannot trigger another `encrypt`.
