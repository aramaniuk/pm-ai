---
title: 'Two ledger vocabularies, named for their subjects'
type: 'feature'
created: '2026-08-29'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** AD-27 closes *two* enumerations in `domain`: the `NormalizedEvent` types and the **`event_log/` entry types**, both versioned "so parsers can read historical entries". Only the first exists (`events.py:20`), and it is named for how values reach it rather than what they are about. So four grammars reach one ledger — the connector vocabulary (`service.py:1104`), `- [security]` (`wiring.py:179`), `- [skill]` (`registry.py:108`), and a bare `COMPACTION` (`storage-contract.md:115`) — and nothing can parse a segment, because nothing states what an entry may be.

**Approach:** Rename `NormalizedEventType` to `ObservedEventType` and add `pm_ai/domain/event_entries.py` with `SelfActionType`, giving both enumerations a name and a stated role so membership is decidable rather than a matter of taste. Their value sets are asserted disjoint, so one occurrence has exactly one member. Declaration only — the renderer is 2d, the parser 2f.

## Boundaries & Constraints

**Always:**
- **Each enum is named for its subject**, so a call site states which kind of record it is building. Neither re-declares a member of the other, their value sets are asserted disjoint rather than trusted, and `LedgerCategory` is their union — what a segment line may be tagged with.
- **`ObservedEventType` — the world, observed.** Subject: something outside pm-ai, with pm-ai as witness. A member qualifies only if it has a durable external referent in AD-34's grammar, can carry a provider `occurred_at`, and is dedup-able by natural key. May be evidence, subject to AD-36.
- **`SelfActionType` — pm-ai, acting.** Subject: pm-ai itself, its only witness. A member qualifies only if it has no external referent, is never evidence, and is never deduplicated — each occurrence is a distinct fact about the machine and all must survive.
- The membership test is one question: **did this happen, or did pm-ai do it?** A candidate that seems to be both is two records — what `authored_by` exists to distinguish.
- Each enumeration owns its payload registry, so an operational entry is typed rather than free text.
- AD-27 also asks that both vocabularies be versioned. **Not delivered by this story** — see the Change Log; a constant that nothing writes or reads is not versioning, and choosing where the version lives is a design decision.
- Pure domain, sibling imports only (AD-30).

**Ask First:** Adding a member to either enumeration. AD-27 requires each addition to be reviewed for overlap; the roles make that review answerable, but the answer is a human's.

**Never:** No renderer, no parser, no storage import, and no behaviour change — the rename moves call sites, never what they do. Nothing here writes an entry.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Known operational category | `"compaction"` | resolves to the `COMPACTION` member | N/A |
| Unregistered category | `"security_note"` | refused, message listing the closed set | `UnknownCategory` |
| A value in both enumerations | any overlap between the two value sets | refused at import — one occurrence, one member | `InconsistentVocabulary` |
| Member lacks a payload | absent from its enum's registry | refused at import | `InconsistentVocabulary` |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/event_entries.py` -- new; `SelfActionType` and the union
- `pm_ai/domain/events.py:20-38` -- the enum being renamed, and the pattern the new one follows
- `pm_ai/ports/__init__.py:26`, `pm_ai/connectors/gitlab.py:31` -- `emits()`, the rename's production call sites
- `pm_ai/storage/service.py:1104` -- the connector vocabulary reaching the ledger today
- `pm_ai/app/wiring.py:179`, `pm_ai/skills/registry.py:108` -- the two ad-hoc tags needing members
- `storage-contract.md:113-117` -- the `COMPACTION` record's specified shape

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/events.py` -- rename `NormalizedEventType` to `ObservedEventType` and add `DECISION` with its payload -- the name states the subject; the member follows the role
- [ ] `pm_ai/domain/event_entries.py` -- add `SelfActionType`, its payload registry, `LedgerCategory`, `UnknownCategory`, and the grammar version -- one statement of what pm-ai may record about itself
- [ ] `tests/domain/test_event_entries.py` -- test the matrix, assert the two value sets are disjoint, and assert each enumeration's members satisfy its stated role -- the roles are the thing that prevents duplication, so they are tested rather than described

**Acceptance Criteria:**
- Given both enumerations, when their values are intersected, then the result is empty — no occurrence can be spelled two ways.
- Given `SelfActionType`, when each member is checked against the connector taxonomy test at `test_domain_invariants.py:106`, then none is declarable by a connector — the role is enforced, not documented.
- Given `lint-imports` runs, then this module imports nothing outside `pm_ai.domain`.

## Spec Change Log

- **2026-09-02, corrected while reviewing wave 1.** Both import-time rows named
  `AssertionError`, a guard shape story 1l retired: `test_guards_survive_o.py:174-181`
  AST-sweeps `pm_ai/` and fails on any `ast.Assert`, because `python -O` strips
  them. The shipped code raises `InconsistentVocabulary(InconsistentModel)`
  (`event_entries.py:195`) and always did, so this was stale documentation of
  correct code — but it was copied verbatim into wave 1's `8c` for the one guard
  standing between a new payload type and an unsanitized field, which is how a
  stale matrix row becomes a real defect two stories later.

- **2026-08-30, code review: the versioning row was fiction and is withdrawn.** `GRAMMAR_VERSION = 1` shipped as a module constant that nothing wrote into a line and nothing consulted while parsing, so a segment written under one grammar was byte-indistinguishable from one written under any other. Its only test asserted the constant was an integer — it could not fail for the reason AD-27 asks for the field, and three review layers found it independently. Removed rather than back-filled: stamping a version on every line forever is a real cost, and the choice between that, a per-segment header, and a dated grammar table is a design decision no story has taken. AD-27's versioning clause is now openly unmet and recorded in deferred-work, which is better than a constant that reads as satisfying it.

- **Rejected alternative, recorded in full.** Not because one list could drift from another — because `ObservedEventType` is the type tag of `NormalizedEvent`, whose envelope requires a `SourceRef` and derives `natural_key` from it (`events.py:158`). A `COMPACTION` has no external referent, and `persist_events` skips any row whose natural key it has seen, so the second compaction in a scope would vanish from the audit trail. One enum remains possible with two record classes; it costs three runtime exclusion lists to do what one type boundary does for free (see the change log).

- **2026-08-29, `DECISION` move approved; both enums renamed for their subjects.** The role statements made the old names the weakest part of the design: `NormalizedEventType` names how a value got there (a connector normalised into it) rather than what it is about, and `EntryType` named nothing at all. They become `ObservedEventType` and `SelfActionType`, with `LedgerCategory` as the union a segment line carries. `SystemActionType` was rejected — `SourceRef.system` already means GitLab or Jira here, so "system" would have pointed at the outside world in an enum whose whole role is that it does not. `NormalizedEvent` keeps its name: normalisation is a true and separate property of the record, and "a normalized event carries an observed-event type" reads correctly. The rename is behaviour-preserving and lands as its own commit, ahead of the new enum.

- **2026-08-29, challenged in review: why not one enum?** The question was whether the four operational categories could simply be added to `ObservedEventType`. Investigated rather than defended, and the spec's original rationale ("two vocabularies that can drift") was found to be the weak form of the argument — it is replaced above. The decisive constraint is the envelope, not the enum: `NormalizedEvent` requires a `SourceRef` (`events.py:158`) and `persist_events` deduplicates on the natural key derived from it, so an operational record either invents a synthetic referent — widening AD-34's closed scopeless set, a second closed set — or loses its second occurrence to dedup. The single-enum option survives only with two record classes, and then costs three runtime exclusion lists: `PAYLOAD_FOR`'s unguarded index (`events.py:147`), the connector `emits()` subset test (`test_domain_invariants.py:106`), and 2f's parser. Two enums let the type system state once what those three would restate.
  The challenge also produced two improvements the original spec lacked. **`DECISION` moves to `ObservedEventType`**: applying the stated role, a decision's subject is the PM, not pm-ai, and AD-33 already rules that a transcript's referent is its meeting — so `meeting:<id>` fits AD-34's grammar unchanged. It was in `SelfActionType` only because it appeared in CAP-27's prose beside the ledger. And **each enumeration gains its own payload registry**, so `COMPACTION` carries typed replaced-segment checksums rather than the free-text line `storage-contract.md:115` sketches.
  KEEP: the roles are enforced by tests — disjoint value sets, and no `SelfActionType` member declarable by a connector — because a role that is only described is a role that erodes.

## Design Notes

**Why two, not one.** The envelope, not the enum: `NormalizedEvent` requires a `SourceRef` and dedups on a key derived from it (`events.py:158`), which an operational record has no honest way to supply. Full accounting in the change log.

**Where each member lands, and the two that are not obvious.**

The ten harvested types stay where they are. Of the four new ones:

| Member | Enum | Why |
| --- | --- | --- |
| `DECISION` | `ObservedEventType` | the PM decided; pm-ai only observed it. AD-33 already rules a transcript's referent is its meeting, and `meeting:<id>` is in AD-34's scopeless set |
| `COMPACTION` | `SelfActionType` | pm-ai deleted a sealed segment. No external witness, and every occurrence must survive |
| `SECURITY` | `SelfActionType` | the daemon's own posture on this machine |
| `SKILL_INVOKED` | `SelfActionType` | its provider `external_id` looks like a referent, but AD-36 makes pm-ai's own writes never evidence: the record is the act, not the effect |

`- [test]` gets no member; fixtures write real categories.

## Verification

**Commands:**
- `uv run pytest tests/domain/test_event_entries.py -q` -- expected: all pass
- `uv run lint-imports` -- expected: contracts kept
