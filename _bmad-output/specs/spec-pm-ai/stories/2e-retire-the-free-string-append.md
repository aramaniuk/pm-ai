---
title: 'Retire the free-string append'
type: 'refactor'
created: '2026-08-29'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `append_event_log(entry: str, *, scope)` (`service.py:980`, declared at `ports/__init__.py:291`) accepts any string. That is how four grammars reached one ledger, and it is why CAP-10's guarantee — every state mutation carries an entry id, an ISO-8601 timestamp, an actor and a category — holds only on the harvest path. 2c and 2d built the vocabulary and the renderer; the door they replace is still open.

**Approach:** Change the parameter to an `EventEntry` and migrate every caller. After this story the ledger's grammar is enforced by the type system rather than by convention.

## Boundaries & Constraints

**Always:**
- **Storage mints the entry id, never the caller** (AD-34: the surrogate is assigned by the storage service at persist time). `EventEntry.entry_id` is therefore optional at construction and stamped on the way in, exactly as `ingested_at` already is; an entry arriving with an id is refused rather than silently overwritten.
- The port declaration (`ports/__init__.py:291`) changes with the implementation. A Protocol that still says `str` is the divergence 1k's type-checking gate exists to catch, and it would catch this one.
- Both production callers gain the fields the free string omitted: `wiring.py:179` records the debug-flag notice with the daemon as actor, `registry.py:108` records the skill invocation AD-1 requires with the invoked skill as actor.
- Every one of the 14 test call sites moves to the typed form. A test helper minting a plain entry is fine; leaving a string overload for tests is not — it is the same door.

**Ask First:** None expected. If a caller has no sensible actor, that is a gap in 2c's vocabulary and belongs there rather than in a nullable field here.

**Never:** No `str` overload, no compatibility shim, no deprecation window — there is one repository and no deployment. No behaviour change to what the entries *mean*; this story changes how they are constructed.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Typed entry | a valid `EventEntry` | one line appended to the open segment, as before | N/A |
| Debug-flag notice | `wiring.py`'s security entry | lands in the **application** scope, as today, now with actor and category | N/A |
| Skill invocation | `registry.py`'s AD-1 entry | one entry per invocation, in the skill's own scope | N/A |
| Encrypted ledger | a scope declaring `event_log/` sealed | refused, unchanged from today | `AppendToSealedArtifact` |

</frozen-after-approval>

## Code Map

- `pm_ai/storage/service.py:980-982` -- the method being retyped
- `pm_ai/ports/__init__.py:291` -- the port declaration that must move with it
- `pm_ai/app/wiring.py:179-183` -- the `- [security]` caller
- `pm_ai/skills/registry.py:108-112` -- the `- [skill]` caller, AD-1's one-per-invocation entry
- `tests/slice/test_storage_resolution.py` (9), `tests/architecture/test_cipher.py` (2), `tests/architecture/test_atomic_writes.py` (2), `tests/architecture/test_capture_guard.py` (1) -- the 14 call sites

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/ports/__init__.py` -- retype `append_event_log` -- the contract, changed first
- [ ] `pm_ai/storage/service.py` -- accept an `EventEntry` and render it -- the writer stops accepting arbitrary text
- [ ] `pm_ai/app/wiring.py`, `pm_ai/skills/registry.py` -- migrate both production callers -- each gains the fields CAP-10 requires
- [ ] the four test modules -- migrate all 14 call sites -- a remaining string call is a remaining grammar

**Acceptance Criteria:**
- Given the type checker runs, then no caller passes a `str` and the port matches the implementation.
- Given `registry.py` invokes a skill, when the entry is read, then it carries the skill's category and actor — AD-1's requirement becomes checkable rather than assumed.
- Given the four tests that assert segment bytes exactly (`test_storage_resolution.py:115`, `test_cipher.py:498`, `test_atomic_writes.py:350`, `test_capture_guard.py:436`), then each still asserts exact bytes with only the minted id masked — an unchanged pass count reached by loosening them would hide the drift this story could cause.
- Given the full suite, then the count of passing tests is unchanged: this story alters construction, not behaviour.

## Spec Change Log

- **2026-08-29, acceptance tightened before implementation.** The review of the set found "the count of passing tests is unchanged" satisfiable by weakening the four tests that assert segment bytes exactly — the entry id is now minted per call, so the obvious way to keep them green is to stop asserting content. Named those four and required them to keep asserting exact bytes with only the id masked. KEEP: the masking approach, not a looser matcher; these four are the only tests that would notice a grammar drift reaching disk.
- **`entry_id` moves to storage.** 2d gave `EventEntry` a required id because its only caller was already inside the writer. With external callers arriving, AD-34 decides it: the surrogate is assigned at persist time, so the field becomes optional and storage stamps it, mirroring `ingested_at`.

## Design Notes

The widest blast radius in story 2, and deliberately its own spec: 14 call sites is a mechanical change that would otherwise hide a semantic one inside a large diff. Separated, the semantic changes are exactly three lines — the two production callers and the port.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: same pass count as before the change, no new skips
- `uv run lint-imports` -- expected: contracts kept
- the repository's type-checking gate (story 1k) -- expected: clean; a missed caller fails here
