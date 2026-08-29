---
title: 'One entry renderer'
type: 'feature'
created: '2026-08-29'
status: 'draft'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The ledger's line format lives in a six-line f-string inside `_append_batch` (`service.py:1103-1108`) and is re-invented by every other caller. A parser therefore has no definition to work from, and a change to the format is a change in an unknown number of places. CAP-10 requires every entry to carry an entry id, an ISO-8601 timestamp, an actor and a category; only the harvest path does.

**Approach:** Add `EventEntry` and `render_entry` beside 2c's enumeration: one record type, one function producing one line. `_append_batch` builds an `EventEntry` and renders it instead of formatting its own string. Behaviour is unchanged — the format that exists today becomes the format that is defined.

## Boundaries & Constraints

**Always:**
- One function produces every ledger line. After this story, a component that formats an entry itself is a defect a test can find.
- The rendered line is one line, newline-terminated by the writer, and holds no embedded newline — `storage-contract.md`'s append rule is that a record without its terminating newline is not a record, and a two-line entry would break it. The `COMPACTION` example's continuation line is the exception this story must reconcile.
- Byte-compatible with what `_append_batch` writes today for the harvest path, so existing segments and existing tests stay valid.
- Rendering is pure: no clock read, no id minting. The entry id and both timestamps arrive on the `EventEntry`.

**Ask First:** The `COMPACTION` record in `storage-contract.md:113-117` spans two lines with an indented continuation. Either entries may wrap — and the parser must then define where a record ends — or that example folds onto one line. This decides 2f's parser.

**Never:** No format change beyond what reconciling the four grammars requires. No new caller migration — retiring the free-string path is 2e. No parser.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Harvested event | today's `_append_batch` inputs | byte-identical to the current line | N/A |
| Value containing a newline | an actor id with `\n` | refused — it would forge a second record | `MalformedEntry` |
| Absent `occurred_at` | `None` | renders `unknown`, as today | N/A |
| Minimal entry | id, type, actor, `ingested_at` only | a valid line; optional fields omitted, not rendered empty | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/event_entries.py` -- 2c's module, which gains `EventEntry` and `render_entry`
- `pm_ai/storage/service.py:1103-1108` -- the f-string this replaces
- `pm_ai/storage/service.py:215` -- `_ulid`, which mints the entry id passed in
- `_bmad-output/specs/spec-pm-ai/storage-contract.md:105-117` -- the append rule and the `COMPACTION` example

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/event_entries.py` -- add `EventEntry`, `render_entry`, `MalformedEntry` -- one definition of a ledger line
- [ ] `pm_ai/storage/service.py` -- `_append_batch` builds and renders an `EventEntry` -- the storage service stops owning the format
- [ ] `tests/domain/test_event_entries.py` -- test the matrix, including a golden line asserted byte-for-byte -- a silent format drift is the failure this story exists to prevent

**Acceptance Criteria:**
- Given the harvest path, when a batch is persisted before and after this change, then the segment bytes are identical.
- Given any value carrying a newline, when rendered, then it is refused rather than written — a forged record boundary is the one corruption the append rule cannot detect.
- Given `pm_ai/`, when searched for entry-shaped f-strings outside this module, then only the callers 2e will migrate remain.

## Spec Change Log

## Verification

**Commands:**
- `uv run pytest tests/domain/test_event_entries.py tests/slice/test_storage_resolution.py -q` -- expected: all pass
- `uv run pytest -q` -- expected: no new failures
