---
title: 'Atomic and durable writes'
type: 'fix'
created: '2026-08-27'
updated: '2026-08-27'
status: 'ready-for-dev'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/storage-contract.md'
  - '{project-root}/_bmad-output/specs/spec-pm-ai/derivation-services.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** No write the single writer performs is atomic, and one of them loses credentials. `_replace` truncates before writing (`service.py:567`); `_seal` opens with `O_TRUNC` and then discards `os.write`'s return value (`:586`, `:592`), so a crash mid-rotation or a short write leaves `config.json` empty or truncated — and an AES-GCM file cut part-way does not degrade, it fails its tag and becomes unreadable. Every connector credential, gone, with the daemon having done nothing wrong.

A second defect is currently invisible and will not stay that way. `_create_exclusively` (`:554`) claims a capture's name with `O_CREAT|O_EXCL` and fills it afterwards, so the name exists while the content is still arriving. `write_capture` mitigates that with `capture.unlink(missing_ok=True)` in an exception handler, which does nothing for `SIGKILL` or a power loss — and its own docstring names the result: a zero-length file owns the name and every retry, *including the one carrying the content*, is refused as a duplicate. Once a filesystem watcher exists (story 10a) the same window becomes a correctness bug rather than a durability one: transcript processing triggered on a growing capture appends a summary, decisions and commitments to append-only ledgers, then appends all of them again when the file completes, with nothing distinguishing the two.

**Approach:** Stage every whole-file write and make its final name appear only when the content is complete and fsynced. Captures link into place, which refuses a taken name; replacements rename into place, which overwrites deliberately. Appends stay appends and get a parser rule instead.

**Depends on:** stories 1b (writes go through the resolver), 1c and 1j (the capture guard), 1f (the envelope cipher). Blocks nothing, but story 10a's watcher assumes it.

## Boundaries & Constraints

**Always:**
- Every raw write loops until the payload is exhausted. `os.write`'s return value is never discarded.
- `transcripts/temp/` is **already declared** in all three capture-holding trees, as a namespace of `transcripts/` — `RETENTION_MANAGED`, plaintext, gitignored. Resolve it; never compose it. The declaration is not cosmetic: `is_encrypted` fails closed on an undeclared path, so an undeclared staging directory would seal the staged bytes and `os.link` would publish ciphertext under a name every reader treats as plaintext.
- A stale staged file is cleaned by the **30-day retention purge and nothing else**. No startup sweep, no boot-time cleanup, no sweep inside this story — decided 2026-08-28. A file left behind by a `SIGKILL` is gitignored litter that owns no capture name, so nothing is blocked by it and nothing needs to hurry.
- A capture becomes visible atomically or not at all: staged in `transcripts/temp/`, fsynced, linked to its final name, temp unlinked, directory fsynced.
- Capture exclusivity survives. A name already taken is refused, as `O_EXCL` refuses it today, and `CaptureAlreadyExists` stays reachable.
- Staged files carry their final permissions from creation — `0600` for anything sealed — never a chmod after the content is visible.
- `fsync` the staged file before it becomes visible; `fsync` the directory after.
- Whole-file replacement overwrites on purpose. Rotating a token must not be refused because the artifact exists.
- A failure before the final name appears leaves the final name unclaimed, so a retry is not refused as a duplicate.

**Ask First:**
- Any staging primitive that does not route through the resolver. `transcripts/temp/` is declared (see below), so the writer asks `resolve` for it; recomposing it from a dirname is the second copy of the layout AD-4 warns about.

**Never:** No debounce, delay or settle-detection anywhere in this story — completeness is never inferred from elapsed time. No change to the append path's shape: `event_log/` and `commitments_log.md` are appended, not rewritten, and rewriting a ledger is what AD-5 forbids. No watcher, no exclusion list, no job — that is story 10a.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| A capture written normally | after the call returns | the final name holds the whole body and `transcripts/temp/` holds nothing |
| A capture whose name is already taken | written | raises `CaptureAlreadyExists`; the existing file is byte-for-byte unchanged |
| A capture write that raises partway | after the failure | the final name does not exist, so the same name can be written again |
| A capture write killed between staging and linking | next attempt with the same name | succeeds — nothing owns the name |
| A filesystem that does not support `link` | a capture written | the capture is still written, via check-then-rename; the refusal is not the outcome |
| A sealed artifact replaced | after the call returns | the old ciphertext is either wholly present or wholly replaced, never truncated |
| A sealed artifact replaced, staged file inspected before the rename | at that moment | it is already `0600`, not world-readable awaiting a chmod |
| `os.write` returning fewer bytes than requested | writing a sealed payload | the loop continues; the artifact holds every byte |
| An append | mid-flush | complete records are readable and a fragment without its terminating newline is not one |
| A capture directory git would track | written | still refused, as story 1c and 1j require; staging does not bypass the guard |

</frozen-after-approval>

## Code Map

- `pm_ai/storage/service.py:544-555` — `_create_exclusively`, the capture path. Becomes stage-then-link. The `O_EXCL` guarantee moves from the final open to the `link`.
- `pm_ai/storage/service.py:557-567` — `_replace`, whose `path.write_bytes` is the plaintext truncate-then-write.
- `pm_ai/storage/service.py:574-594` — `_seal`: `O_TRUNC` at `:586`, the unlooped `os.write` at `:592`, and the `chmod` after the write at `:594` which staging makes unnecessary.
- `pm_ai/storage/service.py:663-687` — `write_capture`, including the `except BaseException` unlink at `:681` that staging replaces. Its docstring states the failure this story closes.
- `pm_ai/storage/service.py:522-542` — `_append`, unchanged. The parser rule belongs to whoever reads a segment; this story adds no reader.
- `tests/architecture/test_static_rules.py` — `WRITE_CALLS` already covers `os.write`, `os.open` and `os.truncate`, so the new primitives stay inside `service.py` by the existing AD-5 rule.
- `tests/storage/test_captures.py` and the capture tests under `tests/slice/` — every existing capture assertion must still hold; the guarantee is strengthened, not changed.

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/storage/service.py` — a single staging primitive used by both `_create_exclusively` and `_replace`/`_seal`, differing only in how the staged file is published (`os.link` vs `os.replace`).
- [ ] `pm_ai/storage/service.py` — loop `os.write`.
- [ ] `pm_ai/storage/service.py` — create staged files with their final mode; drop the post-write `chmod` if staging makes it dead.
- [ ] `pm_ai/storage/service.py` — `link` fallback: catch the unsupported-operation error and use check-then-rename, without inspecting filesystem type.
- [ ] `tests/architecture/test_atomic_writes.py` — new. One test per matrix row.

**Acceptance Criteria:**
- Given a capture write interrupted after staging and before linking, when the same name is written again, then it succeeds — proving the retry-blocked-forever failure is gone.
- Given a capture whose name exists, when written, then `CaptureAlreadyExists` is raised and the existing bytes are unchanged.
- Given a sealed artifact and a simulated short `os.write`, when it is replaced, then the artifact holds every byte and decrypts.
- Given a staged sealed file observed before publication, then its mode is `0600`.
- Given a `link` that raises unsupported, when a capture is written, then it is written anyway and the test asserts the fallback path ran.
- Given `uv run pytest`, then no previously passing test regresses and the skip count is unchanged or `tests/conftest.py`'s `EXPECTED_SKIPS` is updated in the same commit.

## Design Notes

**Why `link` for captures and `replace` for everything else.** Both are atomic; they differ on a taken name. `rename` overwrites silently, which for a capture means splicing two recordings and making `CaptureAlreadyExists` unreachable — the refusal `write_capture` exists to raise. `link` fails with `EEXIST`, so exclusivity stays kernel-enforced exactly as `O_EXCL` made it. For `config.json` the opposite is wanted: rotating a token *must* overwrite, and refusing a taken name would be the defect.

**Why check-then-rename is an acceptable fallback and refusing is not.** Declining to record a meeting because a repository lives on exFAT is a bad trade. The TOCTOU race that makes check-then-rename weaker needs two *concurrent* writers; AD-5 and AD-19 serialize writes within the process, and the real duplicate arrives as a later retry rather than a concurrent write. Only the two home-directory scopes are guaranteed hardlink-capable — an enrolled project repository can sit anywhere — so the fallback is reachable in practice, and it degrades the guarantee from kernel-enforced to writer-serialized rather than losing it.

**Why this is a story-1 slice and not part of 10a.** The credential-truncation path exists today with no watcher anywhere near it. Carrying it into 10a would gate a live data-loss fix behind a feature it does not depend on.

**Why no timer appears anywhere here.** A debounce is a guess about when writing stopped, and the interval that would be long enough is unknowable — a two-minute standup and a four-hour workshop transcript on a slow disk want different numbers, and the way you learn the number was too short is finding duplicate commitments in the ledger afterwards. `link` makes the name's appearance and the content's completeness one event, which is why `storage-contract.md` states that completeness is never inferred from elapsed time.

## Verification

- `uv run pytest -q -rs` — expected: no previously passing test regresses; skip count unchanged or `EXPECTED_SKIPS` updated in the same commit.
- `uv run mypy` — expected: Success.
- `uv run lint-imports` — expected: all contracts kept; the new primitives add no import.
- Mutation: revert the `os.write` loop to a single call and confirm the short-write test goes red. Replace `os.link` with `os.replace` and confirm the taken-name test goes red. Publish before fsync and confirm the ordering assertion goes red.
