---
title: 'Goals are set, not typed'
type: 'feature'
created: '2026-09-03'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `22a` gives `strategic_goals.md` a parser and no writer, so the file can only be produced by hand. But goals are not normally typed by hand: they are set in a 1:1 with pm-ai, or through the CLI or a Telegram voice command. A declared hand-editable artifact with a reader and no writer is exactly what `4a` was for `config.toml`, and it has the same consequence — the operator discovers the format by triggering refusals.

**Approach:** `render_goals` beside `parse_goals`, `pm-ai goal set`, and the event-log entry the mutation requires. Hand-editing stays supported and stops being the channel.

## Boundaries & Constraints

**Always:**
- **Round-trip or nothing, and it is harder here than for `config.toml`.** `parse_goals(render_goals(reg)) == reg` for every admissible register — and the grammar carries **prose**: the title is free text after `[id]` and `(horizon)`. So a rewrite must preserve what the PM wrote, not normalise it.
- **Rendering is in `core`, the write elsewhere.** `render_goals` returns bytes and something above it writes them, the mirror of `parse_goals` taking bytes and opening nothing. Telegram is the second channel (story 5) and surfaces reach adapters only through core (AD-30).
- **Setting a goal appends a `SelfActionType` entry.** CAP-10 requires it: a goal is Tier-1 truth and setting one mutates it. It cannot be an `ObservedEventType` — those require a `SourceRef` and `persist_events` dedups on the key derived from it, so a second revision of one goal would share the first's key and be silently dropped, the failure `2c` documented when it rejected `COMPACTION` there. The member is **`goal_set`**: creating and revising are both setting, and goals appear in no CAP-10 retrospective aggregate, so one member is enough.
- **The file's header carries a worked example.** It is the surface a human may still edit, and a format that documents itself is the only kind that survives being edited by someone who has not read a spec.
- **Domain and horizon are closed vocabularies.** `GoalDomain` and `GoalHorizon` (`goals.py:24-38`); a value outside either is refused rather than written and discovered on the next read.
- **Only the text channel lands here.** Voice needs Whisper (story 7) and a 1:1 needs the Socratic protocol (Phase 3); both call this same renderer when they arrive.

**Ask First:** Nothing.

**Never:** No parsing — `22a` owns it. No goal *deletion*: retiring a goal raises what happens to recommendations citing it, and nothing in wave 1 needs it. No alignment logic, no `resolve`, no `alignment_tag`. No model call of any kind.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Round trip | any admissible register | `parse_goals` of the output equals the input, titles byte-identical | N/A |
| First goal | no `strategic_goals.md` | file created with the header example and one goal under its domain heading | N/A |
| Second goal, same domain | one goal present | both under one heading, existing title untouched | N/A |
| Second goal, new domain | `Project` present, a `Team` goal set | a second heading appended; the first section unchanged | N/A |
| Revision | an existing id set again | that line replaced; every other line byte-identical | N/A |
| Title carrying Markdown | a title with `[`, `]`, `(`, `)` or a pipe | escaped or quoted so it parses back identically | N/A |
| Title carrying a newline | pasted from elsewhere | refused — the grammar is one goal per line | `MalformedGoals` |
| Id outside the charset | `my goal` | refused, naming the charset `22a` states | `MalformedGoals` |
| Domain or horizon unknown | a typo | refused against the closed enumerations | `MalformedGoals` |
| Hand-edited file, then a set | the PM reordered sections and added a comment | the set succeeds; unrelated lines and section order survive | N/A |
| Hand-edited file, malformed | the PM broke the grammar | refused by `22a` and **not overwritten** | `MalformedGoals` |
| Non-interactive invocation | no TTY | refused rather than prompting into a pipe | exit `3` |
| Event log after a set | one goal set | exactly one `goal_set` entry; a second revision adds another | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/core/goal_register.py` -- `22a`'s `parse_goals` and `MalformedGoals`; `render_goals` joins them and must agree with both
- `pm_ai/domain/goals.py:24-38` -- `GoalDomain` and `GoalHorizon`, the two closed vocabularies
- `pm_ai/domain/goals.py:45-49` -- `Goal`, whose `scope` comes from the caller and never from the file
- `pm_ai/domain/event_entries.py` -- `SelfActionType` and its payload registry, which `goal_set` joins
- `pm_ai/domain/scope_model.py:544,1052` -- `strategic_goals.md`, personal-only and a member of `PERSONAL_SUBJECT_ARTIFACTS`
- `pm_ai/surfaces/cli/dispatch.py` -- `4c`'s table and exit codes, reused not extended
- `pm_ai/core/config.py:168` -- `4a`/`4g`'s reader-then-writer split, the shape this slice follows

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/goal_register.py` -- add `render_goals(register) -> bytes` with the header example, preserving titles verbatim and refusing values outside the closed vocabularies
- [ ] `pm_ai/domain/event_entries.py` -- add `goal_set` to `SelfActionType` and its typed payload to that enumeration's registry -- `2c`'s guards must hold: disjoint value sets, and no member declarable by a connector
- [ ] `pm_ai/app/entry.py` -- read, merge, render, write through `write_artifact`, then append the entry -- `core` opens nothing and `surfaces` may not reach storage
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- add `goal set` as a leaf on `4c`'s table
- [ ] `tests/core/test_goal_render.py`, `tests/slice/test_goal_setting.py` -- the matrix, and the hand-edit-survives case against a real temporary root

**Acceptance Criteria:**
- Given a register with titles containing Markdown punctuation, when rendered and parsed back, then every title is byte-identical — the drift pair, and the reason this round trip is harder than `4g`'s: prose, not three typed keys.
- Given a file the PM has reordered and commented, when one goal is set, then every unrelated line and the section order are unchanged — asserted by diffing the file, because a renderer that regenerates from the parsed model would silently normalise the PM's file and pass a round-trip test.
- Given a goal set twice, then the event log holds two `goal_set` entries — asserted because an `ObservedEventType` would have deduped the second away, and one entry would read as one revision.
- Given a malformed `strategic_goals.md`, then `goal set` refuses and the file is byte-identical afterwards.
- Given the rendered file, then its header contains a worked example that `parse_goals` itself accepts — a self-documenting format that does not parse is worse than none.

## Spec Change Log

- **2026-09-03, split from `22a` by decision.** `22a` was written as a parser only, and the human established that goals are not normally typed by hand: they are set in a 1:1 with pm-ai, or through the CLI or a Telegram voice command, with hand-editing retained as a possibility. That made `22a` a reader for a writable artifact — exactly what `4a` was for `config.toml` — so this slice is the `4g` of goals.
  Three things follow from the grammar decided that day (domain from the heading, `[id]` and `(horizon)` structured, title free text). The round trip must preserve **prose**, which is strictly harder than `4g`'s three typed keys. A hand-edited file's section order and comments must survive a set, which a regenerate-from-model renderer would silently normalise while passing a round-trip test. And the mutation needs `goal_set` on `SelfActionType`, for the same reason `meeting_amended` cannot be an `ObservedEventType`: `persist_events` would dedup the second revision of one goal away.

## Verification

**Commands:**
- `uv run pytest tests/core/test_goal_render.py tests/slice/test_goal_setting.py -q` -- expected: all matrix rows pass
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept, AD-30 among them
- `uv run mypy` -- expected: clean
