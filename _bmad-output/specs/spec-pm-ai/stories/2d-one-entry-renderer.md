---
title: 'One entry renderer'
type: 'feature'
created: '2026-08-29'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The ledger's line format lives in a six-line f-string inside `_append_batch` (`service.py:1103-1108`) and is re-invented by every other caller. A parser therefore has no definition to work from, and a change to the format is a change in an unknown number of places. CAP-10 requires every entry to carry an entry id, an ISO-8601 timestamp, an actor and a category; only the harvest path does.

**Approach:** Add `EventEntry` and `render_entry` beside 2c's enumeration: one record type, one function producing one line. `_append_batch` builds an `EventEntry` and renders it instead of formatting its own string. Behaviour is unchanged — the format that exists today becomes the format that is defined.

## Boundaries & Constraints

**Always:**
- One function produces every ledger line. After this story, a component that formats an entry itself is a defect a test can find.
- **One record is one line, always.** A wrapped entry would defeat the append rule in `storage-contract.md`: a reader landing between the two lines sees a *complete*, newline-terminated line that is not a complete record, so "a record without its terminating newline is not a record" would stop distinguishing a fragment from a whole. The two-line `COMPACTION` example is prose wrapping, not a record shape.
- **A value carrying a space, `=`, or a quote is rendered quoted**, with backslash escaping; bare otherwise. Every value written today is an id, a ref, an ISO timestamp or an enum, so all of them stay bare and the bytes do not move — but a `DECISION` statement is prose, and a grammar that cannot hold one is a grammar 2f would have to change.
- Byte-compatible with what `_append_batch` writes today for the harvest path, so existing segments and existing tests stay valid.
- Rendering is pure: no clock read, no id minting. The entry id and both timestamps arrive on the `EventEntry`.

**Ask First:** Amending `storage-contract.md:113-117` so its `COMPACTION` example is one line. The wrapping is decided against below on the document's own append rule, but that file is canonical and the edit is a human's to approve.

**Never:** No format change beyond what reconciling the four grammars requires. No new caller migration — retiring the free-string path is 2e. No parser.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Harvested event | today's `_append_batch` inputs | byte-identical to the current line | N/A |
| Value containing a newline | an actor id with `\n` | refused — quoted or not, it would forge a record boundary | `MalformedEntry` |
| Value containing a space or `=` | a decision statement | rendered quoted, escaped, and round-trips | N/A |
| Key containing a separator | a field name with a space or `=` | refused — a key is a bare token | `MalformedEntry` |
| Line beyond the length bound | an oversized payload | refused, naming the bound | `MalformedEntry` |
| Absent `occurred_at` | `None` | renders `unknown`, as today | N/A |
| Minimal entry | id, category, actor, `ingested_at` only | a valid line; optional fields omitted, not rendered empty | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/event_entries.py` -- 2c's module, which gains `EventEntry` and `render_entry`
- `pm_ai/storage/service.py:1103-1108` -- the f-string this replaces
- `pm_ai/storage/service.py:215` -- `_ulid`, which mints the entry id passed in
- `_bmad-output/specs/spec-pm-ai/storage-contract.md:105-117` -- the append rule and the `COMPACTION` example

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/event_entries.py` -- add `EventEntry`, `render_entry`, `MalformedEntry`, `MAX_ENTRY_LENGTH` -- one definition of a ledger line
- [ ] `pm_ai/storage/service.py` -- `_append_batch` builds and renders an `EventEntry` -- the storage service stops owning the format
- [ ] `tests/domain/test_event_entries.py` -- test the matrix, including a golden line asserted byte-for-byte -- a silent format drift is the failure this story exists to prevent

**Acceptance Criteria:**
- Given the harvest path, when a batch is persisted before and after this change, then the segment bytes are identical.
- Given any value carrying a newline, when rendered, then it is refused rather than written — a forged record boundary is the one corruption the append rule cannot detect.
- Given `pm_ai/`, when searched for entry-shaped f-strings outside this module, then only the callers 2e will migrate remain.

## Spec Change Log

- **2026-08-29, Ask First resolved on the document's own rule.** Entries do not wrap. `storage-contract.md` states that a record without its terminating newline is not a record, which is what lets a reader landing mid-flush tell a fragment from a whole; a two-line entry makes the first line complete and the record not, so the property would silently stop holding. Its `COMPACTION` example is prose wrapping — and a compaction replacing several segments would wrap arbitrarily anyway, so the example could never have been the shape. What remains for a human is the edit to that file.
- **Two unhandled paths from the review of the set, now matrix rows.** The original spec refused an embedded newline but not an embedded space or `=`, so an actor id with a space would have shifted every following field on parse — the same corruption class, unguarded. Values now quote when they must. And a rendered line had no length bound, so one oversized payload could write a line no reader could handle. KEEP: bare rendering for values that need no quoting, or the byte-compatibility this story rests on is lost.

## Verification

**Commands:**
- `uv run pytest tests/domain/test_event_entries.py tests/slice/test_storage_resolution.py -q` -- expected: all pass
- `uv run pytest -q` -- expected: no new failures
