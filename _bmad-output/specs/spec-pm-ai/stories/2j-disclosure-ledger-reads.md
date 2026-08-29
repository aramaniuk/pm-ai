---
title: 'Disclosure ledger reads'
type: 'feature'
created: '2026-08-29'
status: 'draft'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** AD-17 makes cost accounting observability: "the running monthly total surfaces in briefings and CLI" and at threshold breach "the system warns only". AD-31 asks the same file a different question — what has left this machine. Both read a ledger that, after 2i, can only be written. Neither question has a source.

**Approach:** Parse `disclosure.md` back into records and provide the two aggregates the ADs name: a monthly cost and token total, and the records within a period. The surfaces that display them stay with their own stories.

## Boundaries & Constraints

**Always:**
- Reading is the inverse of 2i's renderer, asserted by a round-trip test rather than by two hand-written formats agreeing.
- The same tolerance the event log has: an unterminated trailing line is a boundary, not corruption. The daemon may be appending while a briefing reads.
- Totals are computed from the ledger on every call, never cached and never stored. A stored total is a second structure that can disagree with the records it summarises — and AD-17's whole point is that the figure is *evidence*, not a counter.
- The threshold **warns only**. This story returns numbers and, at most, a breached flag. No degradation, no model switch, no refusal — AD-17 forbids all three.

**Ask First:** Where the monthly threshold lives. AD-17 names "$20 as a monitored target"; `config.toml` is the daemon's settings file, but no story has claimed this key. Hard-coding it here would put a budget figure in the domain.

**Never:** No briefing, no CLI, no rendering for a human — stories 4 and 9 own those surfaces. No enforcement of any kind. No write path.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Monthly total | a ledger spanning three months | cost and token totals for the requested month only | N/A |
| Empty ledger | file absent or zero bytes | zero totals, no error — nothing has left the machine | N/A |
| Mid-append read | an unterminated trailing line | every complete record counted; the tail ignored | N/A |
| Malformed complete record | a terminated line that will not parse | refused, naming the line | `MalformedDisclosure` |
| Period query | a start and end instant | records within it, in ledger order | N/A |
| Threshold breach | total above the configured target | totals returned with a breached flag; nothing is blocked | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/core/disclosure_ledger.py` -- new; the parser and the two aggregates
- `pm_ai/domain/disclosure.py` -- 2i's renderer, inverted here
- `pm_ai/core/ledger.py` -- 2f's trailing-fragment rule, the precedent this follows
- `_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md:236-241` -- AD-17

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/disclosure_ledger.py` -- add the parser, `monthly_total`, and the period query -- both AD questions gain a source
- [ ] `tests/core/test_disclosure_ledger.py` -- test the matrix plus a render/parse round-trip -- one format, asserted rather than maintained twice

**Acceptance Criteria:**
- Given any `DisclosureRecord`, when appended and read back, then every field survives — including the cost estimate at full precision.
- Given a ledger whose total exceeds the target, when queried, then the result reports the breach and no call path is altered.
- Given a ledger truncated mid-append, when totalled, then the figure covers every complete record and no exception is raised.

## Spec Change Log

## Design Notes

Recomputed rather than accumulated because the ledger is the audit trail: a total that drifts from its records is worse than no total, and at this size — one line per frontier call — reading the file is cheap. If it ever is not, the fix is an index in Tier 3, which `derivation-services.md` already has a shape for.

## Verification

**Commands:**
- `uv run pytest tests/core/test_disclosure_ledger.py -q` -- expected: all pass
- `uv run lint-imports` -- expected: contracts kept
