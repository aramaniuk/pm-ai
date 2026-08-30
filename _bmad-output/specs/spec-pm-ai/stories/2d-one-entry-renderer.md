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
- **One grammar, produced by one renderer.** That is the invariant, and it is what a drift test must assert: after this story a component that formats an entry itself is a defect a test can find, and a change to the format is a change in exactly one place. The grammar may still change deliberately — a later story or fix may move a field — but only through `render_entry`, and only with the golden assertion updated in the same commit that moves it, never loosened to accommodate it.
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
- Given the harvest path, when a batch is persisted, then the line matches a golden literal byte for byte with only the minted id masked — and that literal is updated only alongside a deliberate, recorded grammar change.
- Given any value carrying a newline, when rendered, then it is refused rather than written — a forged record boundary is the one corruption the append rule cannot detect.
- Given `pm_ai/`, when searched for entry-shaped f-strings outside this module, then only the callers 2e will migrate remain.

## Spec Change Log

- **2026-08-30, the byte-compatibility clause is replaced by the invariant that survived it.** As written it said the harvest line stays byte-identical to what `_append_batch` wrote before this story, and it held when 2d landed — the golden literal was captured from the real writer and verified. Commit `907ee07`, the CAP-10 fix giving self-action entries a timestamp, then moved `ingested_at` from fifth field to third so a reader finds it in the same place on both write paths, and the golden was updated to match. So a *later fix, made under no spec at all, invalidated a constraint frozen in an earlier one*, and nothing in the process noticed. The risk was never data — nothing is deployed, so no segment exists in the old grammar — it was a reader trusting a guarantee that had lapsed. Replaced with what this story actually protects and what the golden test actually guards: one grammar, one renderer, and a format change that can only happen deliberately and in one place. Approved by the human on 2026-08-30.

- **2026-08-29, Ask First resolved on the document's own rule.** Entries do not wrap. `storage-contract.md` states that a record without its terminating newline is not a record, which is what lets a reader landing mid-flush tell a fragment from a whole; a two-line entry makes the first line complete and the record not, so the property would silently stop holding. Its `COMPACTION` example is prose wrapping — and a compaction replacing several segments would wrap arbitrarily anyway, so the example could never have been the shape. What remains for a human is the edit to that file.
- **Two unhandled paths from the review of the set, now matrix rows.** The original spec refused an embedded newline but not an embedded space or `=`, so an actor id with a space would have shifted every following field on parse — the same corruption class, unguarded. Values now quote when they must. And a rendered line had no length bound, so one oversized payload could write a line no reader could handle. KEEP: bare rendering for values that need no quoting. Every value written today is an id, a ref, an ISO timestamp or an enum, so none gains quotes — quote them all and the golden literal stops matching for a reason nobody intended, which is the drift this story exists to make impossible.

## Verification

**Commands:**
- `uv run pytest tests/domain/test_event_entries.py tests/slice/test_storage_resolution.py -q` -- expected: all pass
- `uv run pytest -q` -- expected: no new failures
