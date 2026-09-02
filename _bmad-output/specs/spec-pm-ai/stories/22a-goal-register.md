---
title: 'Goal register from strategic_goals.md'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `domain/goals.py` defines `Goal`, `GoalDomain`, `GoalHorizon`, and the alignment machinery — `resolve`, `alignment_tag`, `rank_key` — all of which take a `register: dict[str, Goal]`. Nothing builds that register. `strategic_goals.md` is declared Tier-1 in the personal and committed trees (`scope_model.py:544,1052`) and is described as "hand-editable by design (AD-3)", but no parser exists, so every consumer of the alignment machinery has an empty register and CAP-11's `UNALIGNED` is the only reachable answer.

**Approach:** Add the parser that turns the hand-authored Markdown into a register, tolerating human editing and surfacing what it cannot read. `23a`'s 3-Tier Strategic Milestones section is the first consumer.

## Boundaries & Constraints

**Always:**
- **Hand-editable means edit-tolerant, not lenient about meaning.** Extra whitespace, reordered sections and trailing prose are fine. A goal missing its id, domain or horizon is not, because every one of those is load-bearing: the id is what a citation resolves to, and the two enums are closed.
- **An unreadable goal is surfaced, never dropped.** A silently skipped goal makes a citation to it unresolvable, which CAP-11 requires to be visible rather than inferred, and makes a dashboard section quietly short.
- **`UnresolvedGoal` already exists** (`goals.py:57`) for a citation to a goal not in the register; this story does not redefine it and does not widen its meaning to cover parse failures. A goal that failed to parse and a goal that was never written are different facts.
- **Parsing only.** The register is returned; no ranking, no tagging, no recommendation handling — `goals.py` already holds all of that and is unchanged.

**Ask First:** The file's exact Markdown grammar. It is the surface a human types into every week, and a grammar chosen for parser convenience over writing comfort will simply not be maintained. A worked example in the file's own header, so the format documents itself, is the shape worth considering.

**Never:** No write path — this file is authored by hand and pm-ai does not edit it. No goal invention: an absent file yields an empty register and that is a state the renderer states, not one the parser papers over. No changes to `domain/goals.py`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Well-formed file | three goals across three horizons | register of three, keyed by id | N/A |
| Absent file | no `strategic_goals.md` | empty register; not an error | N/A |
| Empty file | `b""` | empty register | N/A |
| Duplicate id | two goals sharing an id | refused — a citation must resolve to one goal | `MalformedGoals` |
| Unknown horizon | a fourth tier invented by hand | refused, listing the three closed values | `MalformedGoals` |
| Unknown domain | a domain outside the three | refused, listing them | `MalformedGoals` |
| Missing id | a goal with a title only | refused, naming the line | `MalformedGoals` |
| Surrounding prose | notes between goals | ignored; goals still parsed | N/A |
| Reordered sections | long-horizon goals written first | parsed; order in the file carries no meaning | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/core/goal_register.py` -- new, the whole of this story
- `pm_ai/domain/goals.py:25-56` -- `GoalDomain`, `GoalHorizon`, `Goal`, and `source_ref`; the closed sets this parser validates against
- `pm_ai/domain/goals.py:57,85-141` -- `UnresolvedGoal` and the machinery that has had no register to work with
- `pm_ai/domain/scope_model.py:544,1052` -- the two declarations of the file
- `pm_ai/storage/service.py:1065` -- `read_artifact`, the caller's reader

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/goal_register.py` -- add `parse_goals(raw: bytes | None) -> dict[str, Goal]` and `MalformedGoals`
- [ ] `tests/core/test_goal_register.py` -- one test per matrix row, including a deliberately messy but valid hand-edited file

**Acceptance Criteria:**
- Given a file with two goals sharing an id, when parsed, then it is refused and the message names the id — a register that silently kept one would make `resolve` return an arbitrary goal.
- Given a well-formed file, when the register is passed to `alignment_tag` with a recommendation citing one of its goals, then the tag names that goal's domain — the machinery works end to end for the first time.
- Given `lint-imports` runs, then `pm_ai.core.goal_register` imports no I/O client.

## Design Notes

Parsing bytes rather than a path keeps this in `core` and testable without a filesystem, matching `4a`'s shape for the same reason.

Refusing the whole file on one bad goal, rather than returning the goals that parsed, is the deliberate choice. A partial register is indistinguishable to every consumer from a complete one, so a typo in the middle of the file would quietly demote every citation below it to `UNALIGNED` — exactly the drift CAP-11 exists to make visible. Refusing loudly costs one fix and hides nothing.

## Verification

**Commands:**
- `uv run pytest tests/core/test_goal_register.py -q` -- expected: all matrix rows pass
- `uv run lint-imports` -- expected: contracts kept
- `uv run pytest -q` -- expected: no new failures
