---
title: 'Sanitization binds at the harvest boundary'
type: 'bugfix'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `pipelines.py:26-28` carries the comment "AD-12 — sanitization at the boundary, uniformly, outside the connector" above `sanitize(getattr(event.payload, "message", "") or "")`. Two independent faults make it a no-op. **The return value is discarded** — `sanitize` is pure, returning a `Sanitized(raw, for_model)` pair with no side effects, so nothing downstream ever sees `for_model`. And **it reads a field most payloads do not have**: `message` exists on `CommitPayload` alone, so for `MessagePayload`, `WorkItemPayload`, `ReviewPayload` and the rest the `getattr` falls back to `""` and sanitizes an empty string. The comment asserts a protection that does not exist, which is worse than having none.

**Approach:** Make each payload declare which of its fields is untrusted text, sanitize those, and persist both halves of the pair per AD-29. Found 2026-09-01 while verifying the prototype-path spec's own claims.

## Boundaries & Constraints

**Always:**
- **A payload declares its own sanitizable fields.** No hardcoded field name anywhere in the pipeline: the failure being fixed is exactly a pipeline that guessed. A payload with no untrusted text declares none, and that is a statement rather than an omission.
- **Non-destructive, per AD-29.** The raw is retained and `for_model` is derived. A citation resolves against the raw; only `for_model` may enter a prompt. Overwriting the raw would destroy the evidence a citation exists to reach.
- **Every payload type is covered or the build fails.** A registry that silently omits a type reintroduces the bug for that type. The completeness check is a test, not a review habit.
- The result is persisted, so no later consumer re-derives it and none can forget to.

**Ask First:** Whether persisting `for_model` beside `raw` in a Tier-1 entry needs an operational schema version bump. Story 1i owns versioning and this is the first change to the entry format since 2l; the answer is a human's.

**Never:** No change to `sanitize()` itself — the function is correct and its tests pass; only its callers are wrong. No new injection patterns: widening detection is a different story from making detection run. No connector changes: AD-12 puts this outside the connector deliberately.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Commit message with injection | `CommitPayload.message` containing "ignore previous instructions" | persisted with raw intact and `for_model` redacted | N/A |
| Teams message with injection | `MessagePayload.excerpt` containing the same | redacted — the case that silently passed before | N/A |
| Payload with no text | `PipelinePayload` | declares no sanitizable field; nothing sanitized, no error | N/A |
| Multi-field payload | `WorkItemPayload` with two text fields | both sanitized, independently | N/A |
| Payload type absent from the registry | a new type added without a declaration | refused at import | `AssertionError` |
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
- [ ] `pm_ai/domain/events.py` -- declare each payload's untrusted text fields, and assert at import that every type in `PAYLOAD_FOR` has a declaration -- the completeness guarantee, enforced the way 2c enforced disjointness
- [ ] `pm_ai/app/pipelines.py` -- sanitize the declared fields and carry the result forward instead of dropping it; delete the hardcoded `"message"`
- [ ] `pm_ai/storage/service.py` -- persist both halves
- [ ] `tests/core/test_sanitize_boundary.py` -- one test per matrix row, including the `MessagePayload` case that regressed silently

**Acceptance Criteria:**
- Given a harvested event whose `MessagePayload.excerpt` contains an injection pattern, when the harvest pipeline runs, then the persisted record's `for_model` is redacted and its raw is byte-identical to the input — the exact case that passed untouched before.
- Given a payload type is added to `PAYLOAD_FOR` without a sanitizable-field declaration, when the module is imported, then it fails — asserted by a test that adds one.
- Given the misleading comment at `pipelines.py:26`, then it now describes what the code does.

## Design Notes

The declaration goes on the payload rather than in the pipeline because the payload is the only thing that knows which of its fields came from outside. A pipeline-side mapping would be a second structure that can disagree with the payload definitions — the same failure mode the scope model avoids by deriving tier sets from node declarations rather than configuring them.

Worth recording why this was inert rather than exploitable: under the prototype path's second decision no model is in the path, so nothing harvested reaches a prompt. The vector opens the moment either changes, and Teams message bodies — arbitrary HTML-formatted text from anyone in the tenant — are the most injection-prone input the design has. Fixing it while `8a` is already in the harvest plumbing is cheaper than revisiting.

## Verification

**Commands:**
- `uv run pytest tests/core/test_sanitize_boundary.py -q` -- expected: all matrix rows pass
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
