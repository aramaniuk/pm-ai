---
title: 'Payloads declare their untrusted text'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `pipelines.py:26-28` sanitizes by guessing a field name — `getattr(event.payload, "message", "")` — and `message` exists on `CommitPayload` alone. For `MessagePayload`, `WorkItemPayload`, `ReviewPayload` and the rest the `getattr` falls back to `""`. The pipeline cannot know which fields came from outside; only the payload can. Nothing states it.

Split from the original `8c` on 2026-09-02 at the sizing gate: declaring untrusted text is a `pm_ai.domain` change with an import-time guard; making the boundary use it is `8e`, in `app` and `storage`.

**Approach:** Each payload class declares which of its fields hold untrusted provider text, refused at import if incomplete.

## Boundaries & Constraints

**Always:**
- **A payload declares its own untrusted fields.** No field name anywhere in the pipeline: a pipeline that guessed is the defect being removed. A payload with no untrusted text declares none, and that is a statement rather than an omission.
- **Declarations are keyed by payload class, not by event type.** `ReviewPayload` is bound to both `REVIEW_SUBMITTED` and `MERGE_COMPLETED` (`events.py:139-140`), so keying by type permits two declarations of one class that disagree.
- **Refused at import by a typed error raised from an `if` — never an `assert`.** `tests/architecture/test_guards_survive_o.py:174-181` AST-sweeps `pm_ai/` and fails on any `ast.Assert`, because story 1l converted all ten import-time guards so `python -O` cannot strip them. This guard carries a subprocess `-O` case as 1l's others do.
- **A declared name is validated against the dataclass** and must be typed `str` or `str | None`. A misspelled name would silently sanitize nothing, reinstating the exact `getattr` fallback this removes.
- **Every class in `PAYLOAD_FOR` is covered or the import fails.** The completeness check is enumerated in a test, not left to review habit.

**Ask First:** Nothing. The versioning question the original `8c` carried belongs to `8e`, which is what persists.

**Never:** No change to `sanitize()` — it is correct and its tests pass. No pipeline or storage changes — `8e`. No new injection patterns: widening detection is a different story from making detection run.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Every class declared | the eight payloads as they stand | import succeeds; each declaration names real fields | N/A |
| Class absent from the registry | a new payload added without a declaration | refused at import | `MissingSanitizableDeclaration` |
| Guard under `python -O` | module imported with `-O` | still refused — a typed raise, not an `assert` | `MissingSanitizableDeclaration` |
| Declared field misspelled | a name not on the dataclass | refused at import, naming class and field | `MissingSanitizableDeclaration` |
| Declared field not text | an `int` or `datetime` field declared | refused — only `str` and `str \| None` can be sanitized | `MissingSanitizableDeclaration` |
| One class, two event types | `ReviewPayload` under both its types | one declaration, keyed by class; divergence impossible | refused at import |
| Class with text declaring none | `DocumentPayload.title`, `DecisionPayload.statement` | every `str` field either declared or explicitly recorded as trusted, with the reason | `MissingSanitizableDeclaration` |
| Class with no text at all | `PipelinePayload` | declares none legitimately; import succeeds | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/events.py:81-133` -- the eight payload classes needing declarations
- `pm_ai/domain/events.py:137-147` -- `PAYLOAD_FOR`, the precedent for a per-type registry and the set completeness is measured against
- `pm_ai/domain/events.py:139-140` -- `ReviewPayload` bound to two event types, the reason declarations key by class
- `pm_ai/domain/invariants.py` -- `InconsistentModel`, the base story 1l's converted guards raise
- `tests/architecture/test_guards_survive_o.py:39-117,174-181` -- the eleven existing import-time guards, their `-O` subprocess cases, and the `assert` ban
- `pm_ai/core/sanitize.py:22-30` -- `Sanitized`, what `8e` will carry

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/events.py` -- declare each payload class's untrusted text fields, add `MissingSanitizableDeclaration`, and refuse at import from an `if` when a class lacks a declaration, names a field it does not have, or declares a non-text field
- [ ] `tests/domain/test_sanitizable_declarations.py` -- the matrix, plus the `-O` subprocess case

**Acceptance Criteria:**
- Given a payload class added to `PAYLOAD_FOR` without a declaration, when the module is imported, then it is refused — **including under `python -O`**, run as a subprocess the way story 1l's guards are.
- Given `grep -rn "assert " pm_ai/domain/events.py`, then there is no match, and `test_guards_survive_o.py` still passes.
- Given every class in `PAYLOAD_FOR`, then each has a declaration and every declared name is a real text field of that class — enumerated, so a class added later cannot slip through.
- Given `MessagePayload`, then `excerpt` is declared — the field the old `getattr` guess never reached, and the one every Teams message body arrives in.

## Spec Change Log

- **2026-09-02, split at the sizing gate.** The original `8c` measured 2,193 tokens and spanned `domain`, `app` and `storage`. This half is the domain declaration and its guard; `8e` is the boundary that uses it. Recorded because the review had judged it a single concern spanning layers, and splitting it was a human's call.
- **Inherited from the 2026-09-02 multi-lens review**, which found the specified `AssertionError` guard forbidden by `test_guards_survive_o.py` — a shape copied from 2c's stale matrix, for the one guard standing between a new payload type and an unsanitized field. It also added keying by class rather than event type, and validating the declared name against the dataclass.

## Design Notes

The declaration goes on the payload because the payload is the only thing that knows which of its fields came from outside. A pipeline-side mapping would be a second structure that can disagree with the payload definitions — the failure the scope model avoids by deriving tier sets from node declarations rather than configuring them.

Requiring an explicit "trusted" record for a `str` field rather than allowing silence is what makes the guard meaningful. Without it, the completeness check passes for a class that declares nothing while carrying provider text, which is the original bug with extra ceremony.

## Verification

**Commands:**
- `uv run pytest tests/domain/test_sanitizable_declarations.py -q` -- expected: all matrix rows pass
- `uv run pytest tests/architecture/test_guards_survive_o.py -q` -- expected: passes; no `assert` introduced
- `uv run pytest -q` -- expected: no new failures
