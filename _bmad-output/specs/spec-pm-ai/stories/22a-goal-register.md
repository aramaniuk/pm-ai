---
title: 'Goal register from strategic_goals.md'
type: 'feature'
created: '2026-09-02'
status: 'ready-for-dev'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `domain/goals.py` defines `Goal`, `GoalDomain`, `GoalHorizon`, and the alignment machinery — `resolve`, `alignment_tag`, `rank_key` — all of which take a `register: dict[str, Goal]`. Nothing builds that register. `strategic_goals.md` is declared Tier-1 exactly once, in the personal tree (`scope_model.py:544`), and is a member of `PERSONAL_SUBJECT_ARTIFACTS` (`:1052`) precisely so that **no committed scope may hold it** (`:1044-1047`) and is described as "hand-editable by design (AD-3)", but no parser exists, so every consumer of the alignment machinery has an empty register. And the consequence is worse than a degraded tag: `UNALIGNED` is returned only when a recommendation cites nothing (`goals.py:64,87-88`), so a recommendation that *does* cite a goal makes `resolve` **raise `UnresolvedGoal`** (`:90-95`). With no register, `alignment_tag` cannot be called on real data at all.

**Approach:** Add the parser that turns the hand-authored Markdown into a register, tolerating human editing and surfacing what it cannot read. `23a`'s 3-Tier Strategic Milestones section is the first consumer.

## Boundaries & Constraints

**Always:**
- **Hand-editable means edit-tolerant, not lenient about meaning.** Extra whitespace, reordered sections and trailing prose are fine. A goal missing its id, domain or horizon is not, because every one of those is load-bearing: the id is what a citation resolves to, and the two enums are closed.
- **An unreadable goal is surfaced, never dropped.** A silently skipped goal makes a citation to it unresolvable, which CAP-11 requires to be visible rather than inferred, and makes a dashboard section quietly short.
- **`UnresolvedGoal` already exists** (`goals.py:57`) for a citation to a goal not in the register; this story does not redefine it and does not widen its meaning to cover parse failures. A goal that failed to parse and a goal that was never written are different facts.
- **Parsing only.** The register is returned; no ranking, no tagging, no recommendation handling — `goals.py` already holds all of that and is unchanged.
- **The scope comes from the caller, never from the file.** `parse_goals(raw, *, scope)`. `Goal.scope` is required deliberately (`goals.py:49`), and it is the personal scope because that is the only tree this file is declared in. A parser defaulting it would be a parser taking a caller's decision — the same rule `11a` states for `Meeting.scope`, and the AD-38 hole the required field closed.
- **A goal-shaped block that fails the grammar is refused, not read as prose.** Otherwise "an unreadable goal is surfaced, never dropped" is satisfied by an implementation that ignores everything non-matching, which is the natural one to write.

**Ask First:** Nothing. The grammar was decided on 2026-09-03: **domain from the section heading, `[id]` and `(horizon)` as the only structured tokens, the title as free text.**

```
## Project

- [g_payments_latency] (medium) Cut payment latency below 200ms
```

Rejected: the `key=value` grammar `11a` uses for meeting records. Four lines per goal is fine for a machine-written record and wrong for a file a human revises, and D-8 made `domain` the grouping axis, which maps onto headings for free. A worked example in the file's own header makes it self-documenting.

**Always, added:** **an id's charset is stated here, not inferred from `SourceRef`.** `SourceRef.parse(f"goal:{id}")` checks only that there are two colon-separated parts and the second is non-empty (`identity.py:26-31`), so `goal:my id` parses happily — a citation with a space in it, unparseable by anything that splits on whitespace. The parser refuses against an explicit charset, and keeps the `SourceRef.parse` check as a second gate.

**Never:** No writer and no command — `22b`.  No write path — this file is authored by hand and pm-ai does not edit it. No goal invention: an absent file yields an empty register and that is a state the renderer states, not one the parser papers over. No changes to `domain/goals.py`.

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
| Goal-shaped but ungrammatical | a block that starts as a goal and breaks | refused, naming the line — never silently treated as prose | `MalformedGoals` |
| Missing title | id and both enums present, no title | refused; `Goal.title` is required too | `MalformedGoals` |
| Not UTF-8 | invalid byte sequence | refused at decode, distinctly from a grammar failure | `MalformedGoals` |
| `goal_id` not citation-safe | an id containing a colon or a space | refused, validated against `SourceRef.parse(f"goal:{id}")` at parse time | `MalformedGoals` |
| Enum value cased or padded | `Short`, ` long ` | stripped and case-folded before matching — this file is typed by hand | N/A |
| Documented synonyms | `operational`, `tactical`, `strategic` | accepted for `SHORT`/`MEDIUM`/`LONG`; `GoalHorizon`'s own docstring names them | N/A |
| Present but goalless | well-formed prose, zero goals | empty register, **distinguishable from absent** so the renderer states the right reason | N/A |
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
- [ ] `pm_ai/core/goal_register.py` -- add `parse_goals(raw: bytes | None, *, scope: DataScope) -> GoalRegister` and `MalformedGoals`, where the result distinguishes absent from present-and-empty
- [ ] `tests/core/test_goal_register.py` -- one test per matrix row, including a deliberately messy but valid hand-edited file

**Acceptance Criteria:**
- Given a file with two goals sharing an id, when parsed, then it is refused and the message names the id — a register that silently kept one would make `resolve` return an arbitrary goal.
- Given a well-formed file, when the register is passed to `alignment_tag` with a recommendation citing one of its goals, then the tag names that goal's domain — the machinery works end to end for the first time.
- Given the signature `parse_goals(raw: bytes | None, *, scope)`, then there is no path to a file — stated structurally rather than as a `lint-imports` criterion, which cannot fail: `core-is-io-free` already forbids every client it would name, and the way a parser actually does I/O is a bare `open()`, which imports nothing.
- Given every goal in a well-formed file, then each `Goal.scope` equals the scope the caller passed — asserted, because a parser that defaults it re-opens AD-38 and nothing else would notice.
- Given a file containing prose and one goal-shaped block with a broken horizon line, then it is refused — the case that separates "surfaced, never dropped" from "ignore what does not match".
- Given a file of well-formed prose with no goals, then the register is present-and-empty rather than absent, so `23a` says "no goals declared" rather than telling the PM to author a file they already wrote.

## Spec Change Log

- **2026-09-03, amended against the second multi-lens review, and the grammar decided.**
  **The Intent was wrong about current behaviour** (B8). It said an empty register makes `UNALIGNED` the only reachable answer; in fact `resolve` **raises `UnresolvedGoal`** for any recommendation that cites a goal (`goals.py:90-95`), and `UNALIGNED` is returned only when nothing is cited. So today's behaviour is an exception, not a degraded tag — a stronger reason for this slice, and one that matters because `23a` and `23b` were specified against the "quietly degrades" reading.
  **The grammar is Markdown-native** (decision Q15): domain from the heading, `[id]` and `(horizon)` as the only structured tokens, title as free text. The `key=value` grammar `11a` uses was rejected here — four lines per goal is right for a machine-written record and wrong for the file a human revises, and this file's failure mode is not a parse error but the PM stopping updating it.
  **The id charset is stated rather than inferred** (B23). The matrix leaned on `SourceRef.parse` to reject an unsafe id, but that check only counts colon-separated parts, so `goal:my id` passes — a citation with a space, unparseable by anything splitting on whitespace.
  **A criterion that could not fail is replaced** (C14). `lint-imports` already forbids every client the old criterion would have caught, and a bare `open()` imports nothing; the `bytes` signature is the real guarantee, so it is stated as one.
  **The writer moved to `22b`.** Goals are not normally typed by hand — they are set in a 1:1, or through the CLI or a Telegram voice command — so this slice was a reader for a hand-editable artifact, exactly as `4a` was. The 4a → 4g precedent applies.

- **2026-09-02, multi-lens review.** Two defects, one of them the most consequential in the wave.
  **The Intent inverted the meaning of `scope_model.py:1052`.** It said `strategic_goals.md` is declared "in the personal and committed trees". That line is a member of `PERSONAL_SUBJECT_ARTIFACTS`, whose checked property is stated at `:1044-1047` as *no committed scope holds it*; the file is declared once, personal-only. An implementer following the original sentence would have looked for or created a project-scope copy — writing the PM's personal career goals into a git-committed employer repository. Corrected, and the reason the line exists is now stated rather than cited.
  **`parse_goals(raw) -> dict[str, Goal]` could not construct a `Goal`.** `Goal.scope` is required and no clause, matrix row or criterion mentioned it, so the implementer would have defaulted it inside the parser. Signature and an Always clause fixed, with a criterion asserting it. *Raised independently by all three lenses.*
  **"An unreadable goal is surfaced, never dropped" was unverifiable.** No matrix row distinguished a goal-shaped block that fails the grammar from prose, so the natural implementation — ignore what does not match — satisfied every row while silently dropping goals. That row now exists.
  The edge-case lens added what a hand-typed file actually contains: case and padding variation, the synonyms `GoalHorizon`'s own docstring documents (operational/tactical/strategic), a missing title, non-UTF-8 bytes, and an id that breaks `SourceRef.parse` later at citation time rather than here. It also caught that a present-but-goalless file was indistinguishable from an absent one, which would have made `23a` tell the PM to author a file they had already written.
## Design Notes

Parsing bytes rather than a path keeps this in `core` and testable without a filesystem, matching `4a`'s shape for the same reason.

Refusing the whole file on one bad goal, rather than returning the goals that parsed, is the deliberate choice. A partial register is indistinguishable to every consumer from a complete one, so a typo in the middle of the file would quietly demote every citation below it to `UNALIGNED` — exactly the drift CAP-11 exists to make visible. Refusing loudly costs one fix and hides nothing.

## Verification

**Commands:**
- `uv run pytest tests/core/test_goal_register.py -q` -- expected: all matrix rows pass
- `uv run lint-imports` -- expected: contracts kept
- `uv run pytest -q` -- expected: no new failures
