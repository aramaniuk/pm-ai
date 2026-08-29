---
title: 'Closed entry-type enumeration'
type: 'feature'
created: '2026-08-29'
status: 'draft'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** AD-27 closes *two* enumerations in `domain`: the `NormalizedEvent` types and the **`event_log/` entry types**, both versioned "so parsers can read historical entries". Only the first exists (`events.py:20`). The ledger therefore carries four grammars: `_append_batch` writes the connector vocabulary (`service.py:1104`), `wiring.py:179` writes `- [security]`, `registry.py:108` writes `- [skill]`, and `storage-contract.md:115` specifies a bare `COMPACTION`. Nothing can parse a segment, because there is no statement of what an entry may be.

**Approach:** Add `pm_ai/domain/event_entries.py`: `EntryType`, the closed enumeration of what the ledger may record, and a grammar version constant. Declaration only — the renderer is 2d and the parser is 2f.

## Boundaries & Constraints

**Always:**
- One entry vocabulary, defined here, closed exactly as `NormalizedEventType` is closed: a caller maps into an existing member and may not mint one.
- The enumeration covers what the ledger already carries plus what the spec names: harvested activity, `DECISION` (CAP-27's `[TYPE: DECISION]`), `COMPACTION` (`storage-contract.md:115`), the debug-flag security notice, and skill invocation (AD-1's one-entry-per-invocation).
- Versioned. A segment written under an earlier vocabulary stays readable, which is the whole reason AD-27 asks for the version.
- Pure domain, sibling imports only (AD-30).

**Ask First:** Whether `EntryType` **re-declares** the ten `NormalizedEventType` members or **wraps** the enum. Re-declaring gives two vocabularies that can drift; wrapping ties the ledger's grammar to the connector's and means adding a connector type silently widens the audit vocabulary. Neither is free and the choice is load-bearing for 2f's parser.

**Never:** No renderer, no parser, no caller change, no storage import. Nothing in this story writes an entry.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Known category | `"decision"` | resolves to the `DECISION` member | N/A |
| Unregistered category | `"security_note"` | refused, message listing the closed set | `UnknownEntryType` |
| Historical grammar | an entry stamped with an earlier version | recognised as readable; the version is retrievable | N/A |
| Every ad-hoc tag in the tree | `security`, `skill`, `test` | each has a member or a documented replacement | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/event_entries.py` -- new, the whole of this story
- `pm_ai/domain/events.py:20-38` -- `NormalizedEventType`, the sibling enumeration and the pattern to follow
- `pm_ai/storage/service.py:1104` -- the connector vocabulary as it reaches the ledger today
- `pm_ai/app/wiring.py:179`, `pm_ai/skills/registry.py:108` -- the two ad-hoc tags that need members
- `_bmad-output/specs/spec-pm-ai/storage-contract.md:113-117` -- the `COMPACTION` record's specified shape

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/event_entries.py` -- add `EntryType`, `UnknownEntryType`, and the grammar version -- one statement of what the ledger may record
- [ ] `tests/domain/test_event_entries.py` -- test the matrix, and assert every bracket tag written anywhere in `pm_ai/` maps to a member -- an unmapped tag is an entry no parser will read

**Acceptance Criteria:**
- Given the enumeration, when a grep of `pm_ai/` finds a literal entry tag, then that tag corresponds to a member — the test enumerates rather than trusting review.
- Given `lint-imports` runs, then this module imports nothing outside `pm_ai.domain`.
- Given a member is added later, then the grammar version records that the vocabulary changed.

## Spec Change Log

## Design Notes

Separate from `NormalizedEventType` because the two sets genuinely differ: `COMPACTION` and the security notice are things pm-ai did to itself and no connector will ever emit them, while a connector type is a claim about the outside world. Collapsing them would mean either a connector able to mint `COMPACTION` or an audit category no connector may use sitting in the connector's vocabulary — which is the overlap AD-27 asks each addition to be reviewed against.

## Verification

**Commands:**
- `uv run pytest tests/domain/test_event_entries.py -q` -- expected: all pass
- `uv run lint-imports` -- expected: contracts kept
