---
title: 'Segment parser and deterministic fold'
type: 'feature'
created: '2026-08-29'
status: 'draft'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing reads a segment back. CAP-10's retrospective, story 19's compaction and 1h's rebuild all need to, and AD-35 additionally fixes *how*: entries fold by `(occurred_at, entry_id)`, "never file order — otherwise `pm-ai reindex` changes commitment states while AD-3's test still passes." The pre-written `test_ad35_ledger_folding_is_deterministic` (`test_domain_invariants.py:504`) skips on a missing `pm_ai.core.ledger`.

**Approach:** Add `pm_ai/core/ledger.py`: parse a segment into entries, and fold a set of entries in the fixed order. The parser implements `storage-contract.md`'s append rule — a record without its terminating newline is not a record.

## Boundaries & Constraints

**Always:**
- **A trailing fragment is a boundary, not corruption.** A reader landing mid-append sees complete records plus an unterminated tail; the tail is dropped and the read succeeds. Raising here would make every concurrent read a failure.
- Fold order is `(occurred_at, entry_id)` and nothing else. Reversing the input must not change the result — that is the pre-written test's assertion.
- Parse is the inverse of 2d's `render_entry` for every entry the renderer can produce. A round-trip test asserts it rather than review.
- Reading is not writing: this module opens segments through `StorageService`, never by path.

**Ask First:** `_ulid()` (`service.py:215`) returns `"evt_" + secrets.token_hex(10)` — random, **not** time-sortable, though `ARCHITECTURE-SPINE.md:649` says these ids are "sortable by creation time". The fold is deterministic either way, because the tiebreaker is stable once written. But entries sharing an `occurred_at` order arbitrarily rather than by arrival, and anything later relying on the spine's claim will be wrong. Fix the minting or amend the spine.

**Never:** No compaction, no deletion, no index. No repair of a malformed complete record — parse refuses it loudly; only the unterminated tail is tolerated.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Whole segment | n terminated records | n entries, in file order | N/A |
| Mid-append read | n records plus an unterminated tail | n entries; the tail is dropped silently | N/A |
| Malformed complete record | a terminated line that is not an entry | refused, naming the segment and line number | `MalformedEntry` |
| Unknown entry type | a category outside 2c's enumeration | refused — the vocabulary is closed | `UnknownEntryType` |
| Fold determinism | entries, and the same entries reversed | identical result | N/A |
| Absent `occurred_at` | `unknown` in the line | sorts to one end deterministically, never arbitrarily | N/A |
| Empty segment | zero bytes | empty result, no error | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/core/ledger.py` -- new; `parse_segment`, `fold`, `sample_entries`
- `tests/architecture/test_domain_invariants.py:504` -- the pre-written test, which calls `sample_entries()` and `fold()` by name
- `pm_ai/domain/event_entries.py` -- 2c/2d's vocabulary and renderer, inverted here
- `_bmad-output/specs/spec-pm-ai/storage-contract.md:105-109` -- the append rule this parser implements
- `pm_ai/storage/service.py:215` -- `_ulid`, the tiebreaker under question above

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/ledger.py` -- add the parser, the fold, and the `sample_entries` fixture the pre-written test imports -- reading the ledger becomes possible
- [ ] `tests/core/test_ledger.py` -- test every matrix row plus a render/parse round-trip -- the pre-written test asserts only determinism

**Acceptance Criteria:**
- Given the suite runs, then `test_ad35_ledger_folding_is_deterministic` passes rather than skipping, and the skip count falls by one.
- Given a segment truncated at any byte offset, when parsed, then every complete record is returned and no exception is raised.
- Given any `EventEntry`, when rendered and parsed, then the result equals the original.

## Spec Change Log

## Design Notes

In `pm_ai.core` rather than `pm_ai.domain` because parsing reaches a file through `StorageService`, which a domain module may not import (AD-30). The entry *grammar* stays in `domain` where 2c and 2d put it; only the act of reading lives here.

## Verification

**Commands:**
- `uv run pytest tests/core/test_ledger.py tests/architecture/test_domain_invariants.py -q` -- expected: pass, one fewer skip
- `uv run lint-imports` -- expected: contracts kept
