---
title: 'Append-only ledgers are created at their declared file mode'
type: 'bugfix'
created: '2026-10-09'
status: 'done'
baseline_commit: '679f96e94603be6f85afa0cc00c0841234eb5fe9'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** pm-ai declares which of its files are this machine's own business — everything it keeps out of version control — and promises to create them readable and writable by the owner alone (mode `0600`). Files written whole keep that promise. Files written by appending do not: a project's monthly event-log segment, a team member's event-log segment and the application's disclosure ledger are each created with whatever permissions the shell's default-permissions mask (the umask) hands out, typically readable by everyone on the machine. This was measured during the 1n review and a test pins the wrong mode today so nobody mistakes it for fixed. Slice `33d` will write Teams message text into these segments, which is why this lands first.

Meeting records are not affected: each is one file written whole, and that path already honours the declaration.

**Approach:** The append path takes the declared mode exactly as the two whole-file writers do, and applies it when it creates the segment. Appending to a segment that already exists changes nothing about it.

## Boundaries & Constraints

**Always:**
- **The mode is applied when the segment file is created, and only then.** The file never exists at a wider mode, not even for an instant. A later append to the same file does not touch its permissions.
- **An existing segment's mode is left as it is by the append**, including one created before this slice at the wrong mode. Changing modes on files the operator may have set deliberately is not this slice's call. Since 1s the daemon's start-up walk tightens existing segments and their folders; an append still changes no existing file's mode.
- **The three writers share one rule.** The declaration in the scope trees decides the mode; the caller that knows the scope and the artifact passes the declared answer, and no writer invents a mode of its own.
- **The personal event log is private (`0600`) since 1s**, which decides a file's mode by where it lives rather than by its `gitignored` declaration. Until 1s it was declared without a restricted mode and stayed at the umask.
- **The declared mode is set regardless of the umask**, as the whole-file writers already do: the mask can strip owner bits and leave pm-ai unable to append to its own ledger. A ledger declared without a mode under such a mask fails on its second append today and still will; this slice does not change that.
- **Nothing else about appending changes**: a ledger declared encrypted is still refused before any file exists, an entry for a sealed month is still refused, a segment the operator made read-only still refuses with the operating system's own error, and the bytes appended are identical.
- **The 1n test that pins the wrong mode flips to pin the right one**, and 1n's Spec Change Log gains a dated line saying so — the test's own failure message asks for both.

**Ask First:** Nothing. Reporting an existing segment at the wrong mode in `pm-ai doctor` is recorded as deferred (`deferred-work.md`, "Surfaced while writing the wave-2 specs"), since `doctor` would need to walk every project's and person's ledger directory.

**Never:**
- No `chmod` of any existing file by the append path, and no directory modes in this slice — both arrived with 1s: new private folders at `0700`, and the daemon's start-up walk for what already exists.
- No widening to "every plaintext file at `0600`"; that is its own recorded item with its own matrix.
- No change to how appends are flushed or made durable, and no change to any declaration in the scope trees.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First entry of a month, project event log | umask `022`, no segment yet | segment created at `0600`; entry written | N/A |
| Second entry, same month | segment exists at `0600` | entry appended; mode still `0600` | N/A |
| Segment from before this slice | segment exists at `0644` (world-readable) | entry appended; mode still `0644` | N/A |
| Harvest batch creates the segment | several events, no segment yet; the month's directory may or may not exist yet | directory created if needed; segment created at `0600`, one write | N/A |
| Disclosure ledger, first record | umask `022`, no file yet | `disclosure.md` created at `0600` | N/A |
| Team-member event log, first entry | umask `022`, no segment yet | segment created at `0600` | N/A |
| Personal event log (since 1s) | personal event log, umask `022` | segment created at `0600` — the personal scope is private | N/A |
| Umask strips owner bits | umask `200`, declared `0600` | segment created at `0600`; the append succeeds | N/A |
| Existing segment is read-only | segment at `0400` by hand | refused with the operating system's own error, as today; the file untouched | permission error, named |
| Ledger declared encrypted | an encrypted declaration, by test | refused before any file exists, as today | the sealed-artifact refusal, unchanged |

</frozen-after-approval>

## Code Map

Verified against `ddcb9aa` on 2026-10-09.

- `pm_ai/storage/service.py:883-903` -- `_append(self, path, text)`: the sealed-declaration refusal, then `path.open("a")` at 902 — the one writer with no `declared_mode`
- `pm_ai/storage/service.py:905-933`, `:935-961` -- `_create_exclusively` and `_replace`, both `declared_mode: int | None`, both passing `mode=ENCRYPTED_FILE_MODE if sealed else declared_mode` to `_publish`
- `pm_ai/storage/service.py:1023-1045` -- `_publish`'s `os.open(…, mode or 0o666)` then `os.fchmod(descriptor, mode)`, with the measured note on why the umask makes the second call necessary — the pattern to follow at creation
- `pm_ai/storage/service.py:845-880` -- `_segment`: the sealed-month refusal, before any file is touched; `_writable_dir` (715) creates the directory under the process umask
- `pm_ai/storage/service.py:1395`, `:1421`, `:1637` -- the three callers: `append_event_log`, `append_disclosure`, `_append_batch`, each knowing its scope and artifact (`EVENT_LOG`, `DISCLOSURE_LEDGER_ARTIFACT`)
- `pm_ai/storage/service.py:1202` -- `write_artifact` passing `restricted_mode(scope.kind, artifact)` — the call the three callers mirror. Meeting records go through here (`pm_ai/core/meeting_records.py:516`)
- `pm_ai/domain/event_entries.py:166` -- `MAX_ENTRY_LENGTH = 16384`, the one bound on a rendered record, where the measured figure is recorded; `pm_ai/storage/service.py:198-214` -- `_write_all` loops on a short `os.write`, which is a second `write(2)` for the same record
- `pm_ai/domain/storage_tiers.py:192-233` -- `RESTRICTED_FILE_MODE` and `restricted_mode`: `0o600` for an artifact in `GITIGNORED`, else `None`. Measured 2026-10-09: `0o600` for project `event_log/`, people `event_log/`, application `disclosure.md`; `None` for personal `event_log/`
- `tests/architecture/test_storage_capabilities.py:362-397` -- `test_the_append_path_does_not_adopt_the_declared_mode`: real umask `022`, append, asserts `0o644` — flips to `RESTRICTED_FILE_MODE`. `_mode` helper at 71; fixture at 61; `test_a_declared_plaintext_artifact_still_answers_to_the_umask` (265) is the umask pattern to copy
- `tests/architecture/test_cipher.py:698-699` -- calls `daemon.storage._append(target, "one more line\n")` with the two-argument signature; `declared_mode` is a required keyword, so this call gains it (the sealed artifact's mode or `None`) and still observes `AppendToSealedArtifact`
- `tests/architecture/test_atomic_writes.py:237-276` -- the umask-`200` precedent, and the 1m note that monkeypatching `os.umask` cannot fail (`stories/1m-atomic-and-durable-writes.md:101`)
- `_bmad-output/specs/spec-pm-ai/stories/1n-project-artifacts-go-machine-local.md:77-79` -- the Spec Change Log entry that records the non-adoption; it cites `service.py:880`, which is `:883` today
- `_bmad-output/implementation-artifacts/deferred-work.md:492`, `:520`, `:695` -- the three entries this closes; `:926` the `doctor` wrong-mode entry this leaves open

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/storage/service.py` -- `_append` takes a required keyword `declared_mode: int | None`; creates the file with that mode and opens it for append untouched when it exists; the three callers pass `restricted_mode(scope.kind, <artifact>)` -- one rule for three writers
- [x] `tests/architecture/test_cipher.py` -- the direct `_append` call at 698 passes `declared_mode` -- the signature changed
- [x] `tests/architecture/test_storage_capabilities.py` -- flip the 1n row to assert the declared mode; one test per matrix row, each setting a real process umask and restoring it in `finally`; the umask-`200` test sets the mask only after the segment directory exists, since `_writable_dir` creates it under the same mask -- the matrix is the contract
- [x] `pm_ai/domain/event_entries.py` -- measure on this machine the largest record size at which two processes appending to one segment concurrently (each opening with `O_APPEND`, one `os.write` per record, several thousand records each, the file then scanned for any line that is not one whole record) never interleave; record the figure, the date and the procedure in a note beside `MAX_ENTRY_LENGTH` -- a measured bound, not a promise
- [x] `_bmad-output/specs/spec-pm-ai/stories/1n-project-artifacts-go-machine-local.md` -- append a dated Spec Change Log line: the append path (`service.py:883`) adopts the declaration since 1q -- the flipped test's message asks for it
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark the three `_append` entries resolved; the `doctor` wrong-mode entry under "Surfaced while writing the wave-2 specs" stays open

**Acceptance Criteria:**
- Given a project with no event-log segment for this month and a process umask of `022`, when one event is appended, then the segment's mode is `0600` and the file holds that one entry.
- Given a segment created at `0644` by hand, when an event is appended, then the mode is still `0644` and the entry is there.
- Given a segment made read-only by hand, when an event is appended, then the operating system's permission error is raised naming the file, and the file is unchanged.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09).

## Design Notes

Creation and append are told apart by the kernel, not by a prior existence check another process could race. The file is created with the declared mode as the creation mode — the umask can only strip bits, never widen them — so it never exists wider than declared; `fchmod` then puts back anything the umask stripped:

```python
try:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL,
                 0o666 if declared_mode is None else declared_mode)
    created = True
except FileExistsError:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    created = False
try:
    if created and declared_mode is not None:
        os.fchmod(fd, declared_mode)   # restores owner bits an owner-stripping umask removed
    os.write(fd, text.encode("utf-8"))
finally:
    os.close(fd)
```

The append's safety against another process appending at the same moment rests on one record being one `write(2)` call on a descriptor opened for append, and Darwin promises no atomicity for a regular-file write above a page — so this slice measures the largest record size at which concurrent appenders from two processes never interleave on this machine and records the figure beside `MAX_ENTRY_LENGTH` (16,384) with the date: a measured bound, not a promise (AD-5 note, 2026-10-10).

An empty segment left behind by a failure between the exclusive create and the write is harmless under an ordinary umask: it already carries the declared mode, and the next append treats it as existing. Under an owner-stripping umask it is not: the create yields the declared mode minus the masked bits (`0400` at umask `200`), and a kill between the create and the `fchmod` leaves that file behind; the next append opens it as existing, never `fchmod`s it, and pm-ai is refused its own segment until owner-write is restored by hand. That is stated, not repaired — changing an existing file's mode is outside this slice. A ledger declared without a mode (`None`) under an owner-stripping umask is created without owner-write and fails on the second append — today's behaviour, unchanged.

## Verification

**Commands:**
- `uv run pytest tests/architecture/test_storage_capabilities.py tests/architecture/test_cipher.py -q` -- expected: all pass, including the flipped 1n row
- `uv run pytest -q` -- expected: all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09)
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

## Spec Change Log

- **2026-10-17, frozen text edited by 1s (private files stay private).** Since 1s the personal event log is private (`0600`): a file's mode follows where it lives, not its `gitignored` declaration. The daemon's start-up walk tightens existing segments and folders; an append still changes no existing file's mode. The "Always" and "Never" lines and the matrix row on the personal event log say so.

- **2026-10-10, Design Notes corrected after the blind-hunter review.** The review found that the sentence calling an empty segment left between the exclusive create and the write "harmless" is false under an owner-stripping umask: the create yields `0400` at umask `200`, and a kill before `fchmod` leaves a segment the next append opens as existing without restoring owner-write, so pm-ai is locked out of it. The sentence now says so, and `_append`'s docstring says the same. Code behaviour is unchanged by this entry — no `chmod` of an existing file was added, which the Never list forbids.

## Suggested Review Order

**The append path takes the declared mode**

- The one writer that ignored the declaration; mode applied only when the file is created.
  [`service.py:916`](../../../../pm_ai/storage/service.py#L916)

- Creation and append told apart by `O_EXCL`; a vanished file is created again, bounded.
  [`service.py:220`](../../../../pm_ai/storage/service.py#L220)

- `fchmod` restores owner bits an owner-stripping umask removed, on a new file only.
  [`service.py:967`](../../../../pm_ai/storage/service.py#L967)

**One rule for three callers**

- Event log: the caller that knows scope and artifact passes `restricted_mode`.
  [`service.py:1471`](../../../../pm_ai/storage/service.py#L1471)

- Disclosure ledger, same rule.
  [`service.py:1501`](../../../../pm_ai/storage/service.py#L1501)

- Harvest batch, same rule.
  [`service.py:1721`](../../../../pm_ai/storage/service.py#L1721)

**The measured bound**

- Two concurrent appenders never interleaved up to 1 MiB per write; a bound, not a promise.
  [`event_entries.py:181`](../../../../pm_ai/domain/event_entries.py#L181)

**Tests**

- The 1n row flipped to pin `0600`.
  [`test_storage_capabilities.py:365`](../../../../tests/architecture/test_storage_capabilities.py#L365)

- An existing segment keeps its mode — no chmod of operator files.
  [`test_storage_capabilities.py:425`](../../../../tests/architecture/test_storage_capabilities.py#L425)

- The reopen race, forced through a patched `os.open`.
  [`test_storage_capabilities.py:449`](../../../../tests/architecture/test_storage_capabilities.py#L449)

- Real umask 200: pm-ai can still append twice.
  [`test_storage_capabilities.py:538`](../../../../tests/architecture/test_storage_capabilities.py#L538)
