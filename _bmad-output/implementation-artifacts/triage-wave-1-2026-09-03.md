# Triage — the 2026-09-03 wave-1 review

Routes every finding in `review-wave-1-2026-09-03.md`. Four routes:

- **DECIDE** — a design question the review exposed and no spec may answer alone. Human's.
- **UNLOCK** — the fix is clear but the text is inside an approved spec's frozen block (`4g`, `4h` only). Human authorises, then mechanical.
- **AMEND** — I fix it as the specs' author. The sixteen `in-review` specs are not locked: `frozen-after-approval` locks on approval, and they have not been approved.
- **REJECT** — noise. None.

## DECIDE — ten questions, in dependency order

**D-1. How does a clean machine obtain a writer before a project exists?** (A2)
Blocks `4d` and `4h`, and everything behind `4c`. `build()` resolves the project
scope eagerly (`wiring.py:124-129`) and raises `UnknownProject`. Options: a
`build(project=None)` mode that skips project resolution; an application-scope
writer constructed in `entry.main()` without the resolver; or moving the eager
resolve behind a lazy accessor. **Recommend the first** — it is one parameter,
it keeps the single writer, and `4c` already requires `doctor` to work on an
unregistered machine, so the mode has a second caller.

**D-2. Does `ConnectorPort` gain `sample_events()` and a health method?** (A5)
Blocks `8d` and `33c`'s conformance criterion. `sample_events` is called by the
AD-34 gate and exists nowhere; the registry has nothing to probe. Options:
extend the port (both methods), or keep the port as-is and register a probe
callable beside each connector. **Recommend extending the port** — an
`isinstance` conformance criterion that cannot observe the method the gate calls
is the defect `1k` was written to end.

**D-3. Do `Probe` and `Health` move to `pm_ai.domain`?** (A6)
`pm_ai.connectors` may not import `pm_ai.platform`, so `8d` can neither reuse
them nor sanely redefine them. They are pure value types. **Recommend the move**,
as part of `8d`.

**D-4. What carries a `Meeting` record out of a connector?** (A7)
Blocks `33c`. `HarvestResult` holds events, cursor, coverage; connectors may not
import storage. Options: widen `HarvestResult`; or `33c` emits only events and
`app/pipelines.py` derives and writes records. **Recommend the second** — AD-30
puts orchestration in `app`, and `11a`'s accessor is already the only writer.

**D-5. How does `core.rendering` name a project tree it may not resolve?** (A8)
Blocks `23d`. Options: take a `ScopePathPort`; or return declared artifact names
and let `app` resolve. **Recommend the port parameter** — `1a` established it and
`StorageService` already takes one.

**D-6. What makes `Sanitized` unforgeable?** (B1)
`8e`'s whole claim rests on this. Options: `__post_init__` refusing a `for_model`
that `_INJECTION.sub` would still change; a factory-only constructor with a
module-private marker; or a `NewType` minted in one place. **Recommend the first**
— it is checkable, needs no new concept, and follows `File`/`Dir`/`Collection`.

**D-7. Is an unset `pm_handle` `WARNING` or `ABSENT`?** (B3)
Decides whether `doctor` can ever report green, and whether `4h` can exit `0`.
Both spellings are unhealthy, so this is really: *should an operator who declines
the handle see a non-zero exit forever?* Options: report it, accept exit `4`, and
say so in `4h`; or treat a declined handle as configured-and-empty and report
`OK` with a note. **Recommend the second** — `4a` made unset a legitimate state
and `extract()` fails closed on it, so it is a choice, not a fault. Needs UNLOCK.

**D-8. Does "3-Tier" mean `GoalHorizon` or `GoalDomain`?** (B9)
`23a` says horizon; `alignment_tag`'s docstring says domain and cites the same
spec section (`goals.py:99-104`). One is wrong and the loser gets corrected in
code or in spec. Needs a read of CAP-9 and FR-11 to settle.

**D-9. Which exit code refuses a non-TTY prompt — `2` or `3`?** (B17)
`4c` defines `2` as usage and `3` as refusal, so `3` fits; `4h` says `2`.
**Recommend `3`**, and `4h`'s two rows change. Needs UNLOCK.

**D-10. Three `Ask First` clauses are their slice's primary deliverable.** (D2)
`11a`'s record format, `22a`'s Markdown grammar, `33c`'s `Meeting.scope` and
all-day convention. Each must be answered before its slice can be implemented,
and `22a`/`11a` gate the whole back half of the wave. The `4g`/`4h` precedent
applies: split each into a format decision and an implementation, or answer them
at the checkpoint.

## UNLOCK — frozen text in `4g` and `4h`

Approved 2026-09-03, so their frozen blocks are yours. Nine findings land inside
them: B3 (both matrices), B16 (`4h`'s TTY ordering), B17 (`4h`'s two exit rows),
B24 (`4h` has no row for an inadmissible or changed answer), B25 (`4g`'s escape
row too narrow, `verbose_logging` in no row), B26 (`4g` cannot distinguish
unreadable from absent), and A2's consequence for `4h`'s sequence. Authorise and
I amend them the way `8e` was amended.

## AMEND — mine, on the sixteen unapproved specs

Frozen or not, these are authorship fixes on specs that have not been approved.

**Unbuildable, fix follows from a DECIDE:** A1 (`4d`'s two nonexistent APIs —
mechanical, no decision needed), A3 (`4d` gains `vcs: VcsPort` on `Daemon`,
inserted before the defaulted `config` field), A4 (`8b`'s probe becomes an
injected callable supplied by `app`).

**Wrong or unsafe:** B2 (`8e`'s transcript claim withdrawn or `Extraction`
carries `Sanitized`), B4 (a task giving `read_artifact` an absent case), B5
(`KeychainPort` gains a conditional-on-absent primitive — a port change, so it
touches AD-14's inventory), B6 (`AES_KEY_BYTES` re-exported rather than moved),
B7 (`8c`'s `PipelinePayload` row restated; `channel` and seven other fields
added), B8 (`22a`'s Intent corrected to `UnresolvedGoal`), B10 (a listing method
on `StoragePort`, owned by `8b`), B11 (`11a`/`33c` overwrite contradiction), B12
(`8b` pre-flights the git-exclusion answer), B13 (`8b`'s mode row), B14 (a task
loading `connectors/` at construction), B15 (locking on three read-modify-write
paths), B18 (`8a`/`33b` pick one clock), B19 (a persisted failure outcome), B20
(`33b`'s Windows zone ids), B21 (`33b`'s page bound and origin check), B22
(`8c` resolves annotations with `get_type_hints`), B23 (`22a`'s id charset).

**Verification:** C1 (`23a` gains the `project_scope_datasources` task and the
skip turn, or the seam moves), C2 (`4g` — needs UNLOCK for the matrix, but its
task list is free), C3 (`EXPECTED_SKIPS` stated as deltas with the ordering
named), C4 (`8d` gains a task on `test_domain_invariants.py`), C5 (`33c`'s
conformance criterion restated), C6 (follows D-10), C7 (`4b` asserts identity,
not value), C8 (a criterion on the remediation text), C9 (`33b` asserts the
request header), C10 (`8e`'s four dead rows move to the first slice with a
caller; the deletion criterion becomes an AST check), C11 (`4g`'s grep becomes
`⊆ ACCEPTED_KEYS`), C12 (`4g` asserts the rendered key set), C13 (`4h` scopes
its criterion to named probes), C14 (`22a` drops a criterion that cannot fail),
C15 (`4c` annotates `dispatch` against a Protocol; adds mypy and the full suite
to Verification), C16 (`4c`'s config row moves to `4g` or gains the edge), C17
(`8a` updates `test_vertical_slice.py`).

**Bookkeeping:** D1 (four self-answered `Ask First` clauses become `Never`), D3
(four dependency edges), D4 (four citations).

## Sequence

1. Answer D-1 through D-10 — nine of the sixteen unbuildable or unsafe findings
   depend on one of them.
2. Authorise UNLOCK for `4g`/`4h`.
3. I apply the AMEND list in build order: `4b`, `4c`, `4d`, then `8a`/`8d`/`8b`,
   then `8c`/`8e`, then `11a`/`22a`, then `23a`/`23d`/`23b`, then `33a`/`33b`/`33c`.
4. Re-run the lens pass on what changed, not on the set.
