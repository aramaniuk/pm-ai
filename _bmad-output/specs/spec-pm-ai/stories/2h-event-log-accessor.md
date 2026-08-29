---
title: 'EventLog accessor'
type: 'feature'
created: '2026-08-29'
status: 'draft'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `derivation-services.md`'s rule 3 names three shared Tier-1 accessors and gives the reason: "without these, three jobs parse the same Markdown three ways and the fourth reader disagrees with all of them." `EventLog` is the one story 2 is responsible for — harvest, transcript processing and every audit write all touch `event_log/`, and after 2f so do the retrospective and, later, compaction and the search index. Each currently reaches `StorageService` directly, or would.

**Approach:** Add `EventLog`: one narrow interface over a scope's segments — append an entry, read entries across segments in order, name the open segment — performing its I/O through `StorageService` rather than touching files.

## Boundaries & Constraints

**Always:**
- All I/O goes through `StorageService` (AD-5, the single writer). The accessor holds no path and opens no file; it is a vocabulary, not a second writer.
- Reads span segments and return entries in the fold order 2f fixed, so two callers asking the same question get the same answer.
- Per scope. A scope is an argument, never a construction-time default — the debug-flag entry goes to the application scope while a skill entry goes to the skill's own, and a bound scope would make one of those a mistake nobody sees.
- Additive: `StorageService`'s methods stay public and 2e's callers keep working. This story adds the accessor and moves nothing.

**Ask First:** Whether `CommitmentLog` and `MeetingRecords` — rule 3's other two accessors — land here as empty siblings or wait for the stories that own their resources (15 and 4). Building all three now risks two interfaces designed without a caller.

**Never:** No job runner, no `inputs()`/`outputs()`, no task manager — that is story 10a. No caller migration beyond what the accessor's own tests need. No caching.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Append | an `EventEntry` and a scope | delegated to the writer; lands in the open segment | N/A |
| Read all | a scope with several segments | every entry, in fold order, across segments | N/A |
| Read a range | a bounded period | only entries within it; segments outside are not opened | N/A |
| Empty log | a scope with no `event_log/` yet | empty result, no directory created | N/A |
| Sealed-segment append | an `at` outside the open segment | 2g's refusal propagates unchanged | `SealedSegment` |
| Truncated tail | a segment mid-append | 2f's rule applies: complete records only | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/core/event_log.py` -- new, the accessor
- `pm_ai/core/ledger.py` -- 2f's parser and fold, which this composes
- `pm_ai/storage/service.py:980` -- the append it delegates to
- `pm_ai/ports/__init__.py:284` -- `StoragePort`, the type the accessor depends on rather than the concrete service
- `_bmad-output/specs/spec-pm-ai/derivation-services.md` -- rule 3 and the accessor table

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/event_log.py` -- add `EventLog` over append, read-all, read-range and open-segment -- one vocabulary for the ledger
- [ ] `tests/core/test_event_log.py` -- test the matrix against a fake `StoragePort` -- depending on the port is what makes that possible

**Acceptance Criteria:**
- Given the accessor, when constructed with a fake `StoragePort`, then every method is exercisable without a filesystem — proving it holds no path.
- Given a scope with three segments, when all entries are read, then the order matches `ledger.fold` applied to the union.
- Given `lint-imports`, then `pm_ai.core` does not import `pm_ai.storage` concretely.

## Spec Change Log

## Verification

**Commands:**
- `uv run pytest tests/core/test_event_log.py -q` -- expected: all pass
- `uv run lint-imports` -- expected: contracts kept
