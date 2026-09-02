---
title: 'Sanitization binds at the harvest boundary'
type: 'bugfix'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `pipelines.py:26-28` carries the comment "AD-12 — sanitization at the boundary, uniformly, outside the connector" above `sanitize(getattr(event.payload, "message", "") or "")`. Two independent faults make it a no-op. **The return value is discarded** — `sanitize` is pure, returning a `Sanitized(raw, for_model)` pair with no side effects, so nothing downstream ever sees `for_model`. And **it reads a field most payloads do not have**: `message` exists on `CommitPayload` alone, so for `MessagePayload`, `WorkItemPayload`, `ReviewPayload` and the rest the `getattr` falls back to `""` and sanitizes an empty string. The comment asserts a protection that does not exist, which is worse than having none.

**Approach:** Make each payload declare which of its fields is untrusted text, sanitize those, and persist both halves of the pair per AD-29. Found 2026-09-01 while verifying the prototype-path spec's own claims.

## Boundaries & Constraints

**Always:**
- **A payload declares its own sanitizable fields.** No hardcoded field name anywhere in the pipeline: the failure being fixed is exactly a pipeline that guessed. A payload with no untrusted text declares none, and that is a statement rather than an omission.
- **Non-destructive, per AD-29.** The raw is retained and `for_model` is derived. A citation resolves against the raw; only `for_model` may enter a prompt. Overwriting the raw would destroy the evidence a citation exists to reach.
- **Every payload type is covered or the build fails**, through a **typed error raised from an `if`** — never an `assert`. `tests/architecture/test_guards_survive_o.py:174-181` AST-sweeps `pm_ai/` and fails on any `ast.Assert` node, because story 1l converted all ten import-time guards so `python -O` cannot strip them. The guard carries a subprocess `-O` case as 1l's others do.
- **Declarations are keyed by payload class, not by event type.** `ReviewPayload` is bound to both `REVIEW_SUBMITTED` and `MERGE_COMPLETED` (`events.py:139-140`), so keying by type permits two declarations of one class that disagree.
- **A declared field name is validated against the dataclass at import** and must be `str` or `str | None`. A misspelled name would silently sanitize nothing — reinstating the exact `getattr` fallback this story removes.
- The result is persisted, so no later consumer re-derives it and none can forget to.

**The carrier is named, not chosen at the keyboard.** `persist_events(events, *, scope)` gains a `sanitized: Mapping[SourceRef, Mapping[str, Sanitized]]` parameter. `NormalizedEvent` is frozen with a `__post_init__` type check (`events.py:156-180`) and every payload is `frozen=True, slots=True`, so there is nowhere to put the pair on the event itself — and the alternatives an implementer would reach for, `object.__setattr__` or re-deriving inside storage, are respectively a mutation of a frozen record and the second source of truth this story's Design Notes argue against.

**Ask First — blocking, and must be answered before this spec's checkpoint closes:** whether persisting `for_model` in a Tier-1 entry bumps the operational schema version. Story 1i owns versioning; this is the first entry-format change since 2l. It is blocking rather than deferrable because the task list already commits to persisting, and 2c's change log records withdrawing `GRAMMAR_VERSION` precisely because entries written under different grammars were byte-indistinguishable.

**Never:** No change to `sanitize()` itself — the function is correct and its tests pass; only its callers are wrong. No new injection patterns: widening detection is a different story from making detection run. No connector changes: AD-12 puts this outside the connector deliberately.

**Scoped out, with an owner:** the transcript path. `run_transcript_ingestion` (`pipelines.py:51,64`) builds `DecisionPayload` content through `extract()`, which calls `sanitize` itself and keeps the pair (`extraction.py:36,50-51,63-64`) — so it is not the same defect. But it reaches `stage_proposal` rather than `persist_events`, so this slice's carrier does not cover it. **`11b` owns proving the transcript boundary holds**, and its spec must say so; fixing one of two boundaries under a story with this title would leave the same false assurance somewhere else.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Commit message with injection | `CommitPayload.message` containing "ignore previous instructions" | persisted with raw intact and `for_model` redacted | N/A |
| Teams message with injection | `MessagePayload.excerpt` containing the same | redacted — the case that silently passed before | N/A |
| Payload with no text | `PipelinePayload` | declares no sanitizable field; nothing sanitized, no error | N/A |
| Multi-field payload | `WorkItemPayload` with two text fields | both sanitized, independently | N/A |
| Payload type absent from the registry | a new type added without a declaration | refused at import | `MissingSanitizableDeclaration` |
| Guard under `python -O` | the module imported with `-O` | still refused — a typed raise, not an `assert` | `MissingSanitizableDeclaration` |
| Declared field is `None` at runtime | `MessagePayload.excerpt is None` | skipped; `for_model` stays `None`, never coerced to `""` | N/A |
| Declared field misspelled | a name not on the dataclass | refused at import, naming the class and the field | `MissingSanitizableDeclaration` |
| One class, two event types | `ReviewPayload` under both its types | one declaration, keyed by class; divergence impossible | refused at import |
| Payload with provider text declaring none | `DocumentPayload.title`, `DecisionPayload.statement` | every `str` field either declared or explicitly recorded as trusted | `MissingSanitizableDeclaration` |
| Entry written before this change | a segment with no `for_model` | absence distinguished from "equals raw"; never read as already-sanitized | surfaced, not defaulted |
| Clean text | no pattern match | `for_model` equals raw; `was_modified` is false | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/app/pipelines.py:26-28` -- the discarded call, the whole of the defect
- `pm_ai/core/sanitize.py:34` -- `sanitize()`, correct and unchanged; `Sanitized.was_modified` at `:30`
- `pm_ai/domain/events.py:82-133` -- the eight payload types needing declarations
- `pm_ai/domain/events.py:137-147` -- `PAYLOAD_FOR`, the precedent for a per-type registry and the place a completeness check already has a shape to follow
- `pm_ai/storage/service.py:1261` -- `persist_events`, where the sanitized form must land

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/events.py` -- declare each payload class's untrusted text fields and add `MissingSanitizableDeclaration`; refuse at import from an `if` when a class in `PAYLOAD_FOR` lacks a declaration, names a field it does not have, or declares a non-`str` field
- [ ] `pm_ai/app/pipelines.py` -- sanitize the declared fields and carry the result forward instead of dropping it; delete the hardcoded `"message"`
- [ ] `pm_ai/storage/service.py` -- add the `sanitized` parameter to `persist_events` and persist both halves
- [ ] `tests/core/test_sanitize_boundary.py` -- one test per matrix row, including the `MessagePayload` case that regressed silently

**Acceptance Criteria:**
- Given a harvested event whose `MessagePayload.excerpt` contains an injection pattern, when the harvest pipeline runs, then the persisted record's `for_model` is redacted and its raw is byte-identical to the input — the exact case that passed untouched before.
- Given a payload type is added to `PAYLOAD_FOR` without a sanitizable-field declaration, when the module is imported, then it is refused — including under `python -O`, run as a subprocess the way story 1l's guards are.
- Given `grep -rn "assert " pm_ai/domain/events.py`, then there is no match, and `test_guards_survive_o.py` still passes — the guard is a typed raise.
- Given every class in `PAYLOAD_FOR`, then each has a declaration and every declared name is a real `str` field of that class — enumerated, so a type added later cannot slip through.
- Given the misleading comment at `pipelines.py:26`, then it now describes what the code does.

## Spec Change Log

- **2026-09-02, multi-lens review.** The guard was forbidden, the carrier was missing, and the deferred question was blocking.
  **The specified `AssertionError` guard is banned by an existing test.** `tests/architecture/test_guards_survive_o.py:174-181` walks every `.py` under `pm_ai/` and fails on any `ast.Assert`, because story 1l converted all ten import-time guards to typed raises so `python -O` cannot strip them. Specifying an `assert` for the one guard standing between a new payload type and an unsanitized field would have reintroduced 1l's defect in the security-critical path. Now a typed error from an `if`, with an `-O` subprocess case. The matrix row was copied from 2c, whose matrix is stale on this point — worth correcting there separately.
  **"Carry the result forward" had nowhere to go.** `NormalizedEvent` is frozen with a `__post_init__` type check, every payload is `frozen=True, slots=True`, and `persist_events` accepts nothing else — so the implementer would have reached for `object.__setattr__` or re-derived `for_model` inside storage. The carrier is now named: a `sanitized` parameter on `persist_events`.
  **The versioning question is blocking, not deferrable.** The task list already commits to persisting, so deferring the decision means changing the Tier-1 entry format with the question open — exactly what 2c's change log records withdrawing `GRAMMAR_VERSION` over.
  **The transcript path is a second boundary**, and investigating it changed the answer: `extract()` already calls `sanitize` and keeps the pair (`extraction.py:36,50-51,63-64`), so it is not the same defect — but it reaches `stage_proposal`, which this carrier does not cover. Scoped out explicitly with `11b` named as owner, rather than left ambiguous.
  The edge-case lens added the paths that would have silently reinstated the bug: a declared field that is `None` at runtime, a misspelled field name, `ReviewPayload` keyed under two event types, and pre-existing entries with no `for_model` read as already-sanitized.
## Design Notes

The declaration goes on the payload rather than in the pipeline because the payload is the only thing that knows which of its fields came from outside. A pipeline-side mapping would be a second structure that can disagree with the payload definitions — the same failure mode the scope model avoids by deriving tier sets from node declarations rather than configuring them.

Worth recording why this was inert rather than exploitable: under the prototype path's second decision no model is in the path, so nothing harvested reaches a prompt. The vector opens the moment either changes, and Teams message bodies — arbitrary HTML-formatted text from anyone in the tenant — are the most injection-prone input the design has. Fixing it while `8a` is already in the harvest plumbing is cheaper than revisiting.

## Verification

**Commands:**
- `uv run pytest tests/core/test_sanitize_boundary.py -q` -- expected: all matrix rows pass
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
