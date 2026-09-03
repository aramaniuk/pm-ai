---
title: 'Sanitization binds at the model boundary'
type: 'bugfix'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `pipelines.py:26-28` carries the comment "AD-12 — sanitization at the boundary, uniformly, outside the connector" above `sanitize(getattr(event.payload, "message", "") or "")`, and **discards the return value**. `sanitize` is pure (`sanitize.py:34`), so the loop computes a value and drops it while the comment asserts a protection that does not exist.

The deeper defect is that no protection *could* exist here. AD-12's own second clause requires enforcement "at the consumer, not only at the producer", through a `ModelPort` that "accepts only that type for externally-sourced text" — and **no `ModelPort` exists anywhere in `pm_ai/`**. Nothing in the package references `Sanitized` except the module defining it. A producer-side rule is one forgotten call site away from being false, which is precisely what this line is.

Found 2026-09-01 while verifying the prototype-path spec's own claims. Split from the original `8c` on 2026-09-02: `8c` declares which fields are untrusted; this slice makes that declaration unbypassable at the point of use.

**Approach:** Make unsanitized text unable to reach a model by construction. Move `Sanitized` into `pm_ai.domain` so `pm_ai.ports` may name it, declare `ModelPort` with externally-sourced text typed `Sanitized` and never `str`, and delete the no-op that pretended to do this at the producer.

## Boundaries & Constraints

**Always:**
- **Non-destructive, per AD-29.** The raw is retained and `for_model` derived. A citation resolves against the raw; only `for_model` may enter a prompt. Overwriting the raw destroys the evidence a citation exists to reach.
- **The guard is a type at the chokepoint, not a convention at the producer.** Omitting sanitization must be a construction error at the one boundary every model call passes, which is what AD-12's consumer clause asks for and what a comment above a discarded call cannot deliver.
- **Nothing is persisted, and the Tier-1 entry grammar does not change.** `sanitize` is pure over a raw that AD-29 guarantees is retained, so the derived copy is reconstructible at the point of use and needs no home on a segment line. Deliberate: a `for_model` field on every line would widen the entry grammar, and AD-27's versioning clause is unmet — 2c withdrew `GRAMMAR_VERSION` as a constant written nowhere and read nowhere, and the design choice behind it is unmade. Persisting inherits that decision; deriving does not need it. The audit record AD-31 requires is scope provenance in the disclosure ledger — contributing scopes, task class, model, token counts, destination — not the sanitized text.
- **Only declared fields are untrusted.** The field names live in `8c`'s declarations; a caller gathering text for a prompt reads them, and this slice names none.

**Ask First:** whether `sanitize()` and its pattern follow the type into `pm_ai.domain`. The minimal move is the type alone, which is what this slice specifies.

**Never:** No change to `sanitize()`'s behaviour or to `8c`'s declarations. No connector changes — AD-12 puts this outside the connector deliberately. **No adapter, no routing table, no task-class enumeration:** AD-15's router and its local and frontier adapters belong to story 7, which wires them "behind the model port". This slice declares the port and stops. No new field on any Tier-1 entry.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Bare `str` where external text is expected | a caller passes raw provider text to `ModelPort` | does not type-check — the omission is a construction error | `arg-type`, asserted by running mypy on a fixture as a subprocess |
| `Sanitized` passed | the derived copy | accepted | N/A |
| Commit message with injection | `CommitPayload.message` containing "ignore previous instructions" | sanitizing at the point of use yields a redacted `for_model`; the raw is untouched | N/A |
| Teams message with injection | `MessagePayload.excerpt` containing the same | redacted — the case that silently passed before | N/A |
| Declared field is `None` | `MessagePayload.excerpt is None` | nothing to sanitize; never coerced to `""` | N/A |
| Payload declaring no fields | `PipelinePayload` | nothing to sanitize, no error | N/A |
| Clean text | no pattern match | `for_model` equals raw; `was_modified` is false (`sanitize.py:30`) | N/A |
| Internally-sourced text | a prompt fragment pm-ai wrote itself | may be `str`; the discipline is scoped to externally-sourced text | N/A |
| The retired no-op | `pipelines.py:26-28` | gone, and the comment that claimed it worked gone with it | N/A |
| Tier-1 entry format | any harvested event | byte-identical to today: no `for_model` field, no grammar change | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/app/pipelines.py:26-28` -- the discarded call and its false comment, deleted here
- `pm_ai/core/sanitize.py:22-30,34` -- `Sanitized`, which moves; `sanitize()` and the pattern, which stay
- `pm_ai/ports/__init__.py:1-6` -- the docstring stating `ports` imports nothing but `pm_ai.domain`, which is *why* the type must move; `StoragePort:286` is the shape to follow
- `.importlinter:211-219` -- AD-30, the contract that fails if `ports` reaches `core`
- `tests/architecture/test_types.py` -- mypy already runs as a subprocess inside pytest (story 1k); the precedent this slice's negative type test follows
- `pm_ai/domain/events.py` -- `8c`'s declarations, read by callers rather than by this slice

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/sanitize.py` -- move `Sanitized` here from `pm_ai.core.sanitize` -- `pm_ai.ports` may import only `pm_ai.domain` (`.importlinter:211-219`), and a port that cannot name the type cannot demand it
- [ ] `pm_ai/core/sanitize.py` -- import the moved type; `sanitize()` and `_INJECTION` stay, behaviour unchanged
- [ ] `pm_ai/ports/__init__.py` -- declare `ModelPort`, externally-sourced text typed `Sanitized` and never `str`; no adapters, no routing
- [ ] `pm_ai/app/pipelines.py` -- delete the no-op and the comment claiming it sanitized
- [ ] `tests/architecture/test_sanitize_boundary.py` -- the matrix, plus the negative type test: a fixture calling `ModelPort` with a `str`, mypy run on it as a subprocess, asserting `arg-type`

**Acceptance Criteria:**
- Given a fixture passing a bare `str` where `ModelPort` expects externally-sourced text, when mypy runs on it, then it reports `arg-type` — the omission is a construction error per AD-12's consumer clause, not a review catch. Asserted by subprocess because `[tool.mypy] files = ["pm_ai"]` does not check `tests/`.
- Given `grep -n 'getattr(event.payload' pm_ai/app/pipelines.py`, then there is no match, and no comment claims a sanitization the code does not perform.
- Given `uv run lint-imports`, then AD-30 holds with `ModelPort` naming `Sanitized` — which is what moving the type buys.
- Given a harvested event carrying an injection pattern, then the Tier-1 entry written for it is byte-identical to today's: no `for_model` field, no grammar change.
- Given `grep -rn "Sanitized" pm_ai/`, then `pm_ai.ports` is among the matches — the type has a consumer for the first time.

## Spec Change Log

- **2026-09-02, frozen intent renegotiated by the human: persistence dropped for consumer-side enforcement.** The slice previously carried both halves of the pair into `persist_events` and wrote `for_model` onto the Tier-1 entry, with a blocking `Ask First` on whether that bumped the operational schema version. Three findings retired that shape. The question was a category error: `SCHEMA_VERSION` describes `operational.db`'s table shape, this slice adds no column, and the only thing the write path puts in SQLite is a `seen` dedup key — so story 1i could not receive it. The version that *would* govern a segment line is AD-27's entry grammar, which does not exist. And the reason to persist did not survive examination: AD-12 already requires the guard at the consumer through a `ModelPort` accepting only `Sanitized`, that port does not exist, nothing in `pm_ai/` uses `Sanitized` at all, and AD-31's audit record is scope provenance in the disclosure ledger rather than the sanitized text. Deriving at the point of use is therefore free, and it deletes the versioning problem instead of deferring it.
  Two consequences. The transcript path is **no longer scoped out**: it was excluded because `stage_proposal` bypassed the `persist_events` carrier, and with no carrier the chokepoint covers every path uniformly. And the ordering deadline against `23b` is gone — nothing writes a new field, so no segment can predate the change.
  KEEP: the `PAYLOAD_FOR` enumeration instinct — the original defect was that seven of eight payload types went unsanitized, so any caller-side test must enumerate the registry rather than spot-check.
- **2026-09-02, split at the sizing gate** from the original `8c` (2,193 tokens). `8c` keeps the domain declarations and their import-time guard; this slice is the boundary that makes them binding.
- **Inherited from the 2026-09-02 multi-lens review**, which found "carry the result forward" had no declared carrier against frozen dataclasses and an unchanged `persist_events` signature; that the versioning question was blocking rather than deferrable; and that the transcript path is a second boundary. The carrier and versioning findings are superseded by the renegotiation above; the review's investigation of the transcript path stands and is what shows the chokepoint covers it.

## Design Notes

Worth recording why this was inert rather than exploitable: under the prototype path's second decision no model is in the path, so nothing harvested reaches a prompt. That is also what makes this the right moment to fix it structurally — there is no deployed data and no model call to migrate, so the port can be declared before anything can bypass it.

Declaring a port with no adapter is the shape story 1d used for `KeychainPort`: custody first, cipher later. Here the reason is stronger, because the port's whole purpose is to refuse a call that does not exist yet.

## Verification

**Commands:**
- `uv run pytest tests/architecture/test_sanitize_boundary.py -q` -- expected: all matrix rows pass, negative type test included
- `uv run lint-imports` -- expected: 12 contracts kept, AD-30 among them
- `uv run mypy` -- expected: clean
- `uv run pytest -q` -- expected: no new failures
