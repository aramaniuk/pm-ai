---
title: 'Sanitization binds at the harvest boundary'
type: 'bugfix'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `pipelines.py:26-28` carries the comment "AD-12 — sanitization at the boundary, uniformly, outside the connector" above `sanitize(getattr(event.payload, "message", "") or "")`, and **discards the return value**. `sanitize` is pure, returning a `Sanitized(raw, for_model)` pair with no side effects (`sanitize.py:34`), so the loop computes a value and drops it. Nothing downstream ever sees `for_model`. The comment asserts a protection that does not exist, which is worse than having none.

Found 2026-09-01 while verifying the prototype-path spec's own claims. Split from the original `8c` on 2026-09-02: `8c` declares which fields are untrusted; this slice makes the boundary act on those declarations and persists the result.

**Approach:** Sanitize the fields `8c`'s declarations name, and carry both halves of the pair into storage.

## Boundaries & Constraints

**Always:**
- **Non-destructive, per AD-29.** The raw is retained and `for_model` derived. A citation resolves against the raw; only `for_model` may enter a prompt. Overwriting the raw destroys the evidence a citation exists to reach.
- **The carrier is named, not chosen at the keyboard.** `persist_events(events, *, scope)` gains `sanitized: Mapping[SourceRef, Mapping[str, Sanitized]]`. `NormalizedEvent` is frozen with a `__post_init__` type check (`events.py:156-180`) and every payload is `frozen=True, slots=True`, so there is nowhere to put the pair on the event itself — and the two things an implementer would otherwise reach for, `object.__setattr__` or re-deriving inside storage, are respectively a mutation of a frozen record and the second source of truth this fix exists to avoid.
- **The result is persisted**, so no later consumer re-derives it and none can forget to.
- **Only declared fields are touched.** The field names live in `8c`'s declarations; this slice reads them and never names one.

**Ask First — blocking, and must be answered before this spec's checkpoint closes:** whether persisting `for_model` in a Tier-1 entry bumps the operational schema version. Story 1i owns versioning; this is the first entry-format change since 2l. Blocking rather than deferrable because the task list commits to persisting, and 2c's change log records withdrawing `GRAMMAR_VERSION` precisely because entries written under different grammars were byte-indistinguishable.

**Never:** No change to `sanitize()` or to `8c`'s declarations. No connector changes: AD-12 puts this outside the connector deliberately.

**Scoped out, with an owner:** the transcript path. `run_transcript_ingestion` (`pipelines.py:51,64`) reaches `extract()`, which calls `sanitize` itself and keeps the pair (`extraction.py:36,50-51,63-64`) — so it is not the same defect. But it reaches `stage_proposal` rather than `persist_events`, so this carrier does not cover it. **`11b` owns proving the transcript boundary holds**, and its spec must say so.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Commit message with injection | `CommitPayload.message` containing "ignore previous instructions" | persisted with raw intact and `for_model` redacted | N/A |
| Teams message with injection | `MessagePayload.excerpt` containing the same | redacted — the case that silently passed before | N/A |
| Declared field is `None` | `MessagePayload.excerpt is None` | skipped; `for_model` stays `None`, never coerced to `""` | N/A |
| Payload declaring no fields | `PipelinePayload` | nothing sanitized, no error | N/A |
| Multi-field payload | two declared text fields | both sanitized independently, keyed by field name | N/A |
| Clean text | no pattern match | `for_model` equals raw; `was_modified` is false (`sanitize.py:30`) | N/A |
| Entry written before this change | a segment with no `for_model` | absence distinguished from "equals raw"; never read as already-sanitized | surfaced, not defaulted |
| Batch refused mid-persist | one event fails validation | all-or-nothing, as `persist_events` already is; no half-sanitized batch | propagated |

</frozen-after-approval>

## Code Map

- `pm_ai/app/pipelines.py:26-28` -- the discarded call, the whole of the defect
- `pm_ai/core/sanitize.py:22-30,34` -- `Sanitized`, `was_modified`, and `sanitize()`; unchanged
- `pm_ai/domain/events.py` -- `8c`'s declarations, read here
- `pm_ai/storage/service.py:1261` -- `persist_events`, gaining the `sanitized` parameter
- `pm_ai/core/extraction.py:36,50-51,63-64` -- the transcript path, which already keeps the pair; scoped out above

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/app/pipelines.py` -- sanitize the fields `8c` declares and carry the result forward instead of dropping it; delete the hardcoded `"message"`, and correct the comment so it describes what the code does
- [ ] `pm_ai/storage/service.py` -- add the `sanitized` parameter to `persist_events` and persist both halves
- [ ] `tests/core/test_sanitize_boundary.py` -- the matrix, including the `MessagePayload` case that regressed silently

**Acceptance Criteria:**
- Given a harvested event whose `MessagePayload.excerpt` contains an injection pattern, when the harvest runs, then the persisted record's `for_model` is redacted and its raw is byte-identical to the input — the exact case that passed untouched before.
- Given every class in `PAYLOAD_FOR` with a declared field, then a harvest carrying that payload persists a `for_model` for it — enumerated over the registry, not spot-checked on four types, because the original defect was precisely that seven of eight went unsanitized.
- Given `pipelines.py:26`, then the AD-12 comment describes what the code does.
- Given `grep -n 'getattr(event.payload' pm_ai/app/pipelines.py`, then there is no match.

## Spec Change Log

- **2026-09-02, split at the sizing gate** from the original `8c` (2,193 tokens). `8c` keeps the domain declarations and their import-time guard; this slice is the `app` and `storage` half.
- **Inherited from the 2026-09-02 multi-lens review**, which found "carry the result forward" had no declared carrier against frozen dataclasses and an unchanged `persist_events` signature; that the versioning question was blocking rather than deferrable; and that the transcript path is a second boundary — investigation showing it already keeps the pair, so it is scoped out with `11b` named rather than left ambiguous.

## Design Notes

Worth recording why this was inert rather than exploitable: under the prototype path's second decision no model is in the path, so nothing harvested reaches a prompt. The vector opens the moment either changes, and Teams message bodies — arbitrary HTML-formatted text from anyone in the tenant — are the most injection-prone input the design has.

Enumerating the acceptance criterion over `PAYLOAD_FOR` rather than testing a few types is deliberate. The original bug was not that sanitization was wrong for one payload; it was that it silently did nothing for seven of eight, and a spot-check would have reproduced exactly that blind spot.

## Verification

**Commands:**
- `uv run pytest tests/core/test_sanitize_boundary.py -q` -- expected: all matrix rows pass
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
