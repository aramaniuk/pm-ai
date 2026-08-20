# Adversarial Divergence Review — ARCHITECTURE-SPINE.md (r4)

**Reviewer:** fresh adversarial architecture reviewer, no authoring context
**Target:** `ARCHITECTURE-SPINE.md` (38 ADs, `updated: 2026-08-19`, `status: revision-in-progress`)
**Also attacked:** `pm_ai/` (1,719 LOC), `.importlinter`, `tests/architecture/`
**Date:** 2026-08-19

---

## Verdict

**The contract still does not hold. 12 divergence pairs, 11 CONFIRMED with quotes from both sides.**

The r4 revision closed the *mechanism* holes almost completely — reference grammar,
tier separation, CAS, provenance, disclosure routing are all real now, and several are
enforced by types rather than prose. That work is good and I could not break it.

The new holes are all at the **join between the late ADs (AD-30..AD-38) and the early
ones**, and they cluster in one shape: **AD-38 introduced a *splitting* rule ("an entry
that would need two scopes is two entries") into a document whose identity, dedup, egress,
and logging rules were all written assuming one entry per subject.** Five of the twelve
pairs below are downstream of that single unreconciled edge. A sixth cluster is
**"the Rule names a predicate but never defines its operands"** — AD-35's `covered`,
AD-20's `job_type`, AD-36's `external`.

Severity note: **six of the twelve fail in the direction that looks like success** —
a dropped ledger entry, a commitment marked `FULFILLED` on pm-ai's own comment, an audit
that reports "nothing has left this machine". None crashes. None is caught by the suite.

**Enforcement is also weaker than the document claims.** `uv run pytest tests` today is
`65 passed, 32 skipped`, and the skip mechanism (`mod()` → `pytest.skip` on
`ModuleNotFoundError`) means an AD's check **fails open when its module is missing** —
including if someone deletes or renames it later. The spine says "these checks fail the
build instead"; a third of them currently cannot.

---

## Method

Each pair names two units one level down that **each obey every AD to the letter** and
still build incompatibly. Both sides are quoted. A finding I could not quote from both
sides is not listed. `CONFIRMED` = both quotes verified in the artifact; `PLAUSIBLE` =
the collision is real but one side depends on a reading I cannot pin to a single sentence.

Prior-round findings that remain open are marked **[RE-CONFIRM]** and kept short — they
are not counted toward the twelve.

---

# Part 1 — The AD-38 splitting seam (5 pairs)

## D4-1 · AD-34's natural key and AD-38's "two entries" cannot both hold `[CONFIRMED]` ⚠ TOP

**AD-34, Rule, element 3:**
> **Natural key.** Deduplication uses `(source_system, source_ref)`, never the locally-minted id.

**AD-38, Rule, closing sentence:**
> `event_log/` routing is therefore unambiguous: an entry belongs to the scope that owns its subject, and **an entry that would need two scopes is two entries.**

**Unit A — the cross-scope splitter (`core/`, per AD-38).** A 1:1 that touches a project
work item produces two ledger entries about the same subject: the project-visible part to
`.project-ai/memory/event_log/`, the personal part to `~/.manager-ai/memory/event_log/`.
Both entries describe the same underlying occurrence, so both correctly carry the same
`source_ref` — AD-33 requires it ("`source_ref` points at the most upstream durable
referent"). Fully compliant with AD-33, AD-34, AD-38.

**Unit B — the storage service's dedup (`storage/`, per AD-34).** Rejects the second
write, because `(source_system, source_ref)` is already present.

**The exact incompatibility.** AD-38 *mandates* two records with one natural key. AD-34
*mandates* that one natural key means one record. The two ADs are individually satisfiable
and jointly contradictory. The natural key has no scope component, and AD-38's split is
defined precisely as "same subject, two scopes."

**This is already in the code, and it is silent.** `pm_ai/storage/service.py`:

```python
self._seen: set[tuple[str, str]] = set()   # __init__ — one set for the whole service
...
def persist_events(self, events, *, scope: DataScope) -> PersistResult:
    for ev in events:
        if ev.natural_key in self._seen:   # AD-34 — re-harvest is idempotent
            duplicates += 1
            continue
```

`_seen` is **scope-blind**. `natural_key` is `(source_ref.system, str(source_ref))` — no
scope. So whichever scope is persisted first wins, the other is counted as a duplicate,
and `PersistResult.duplicates` increments where nobody reads it. AD-38's whole purpose —
that the personal half of a cross-scope operation is recorded *somewhere* — is defeated by
AD-34's dedup, and the failure mode is a missing personal record, i.e. exactly the
material AD-31 says the user must be able to audit.

**Second victim, same cause.** AD-10 makes connector instances per-project. A monorepo
registered as two projects harvests the same MR twice, once per scope. Scope-blind dedup
drops it from the second project's ledger, and FR-34's verifier there sees no evidence.

**Closing AD (tighten AD-34.3):**
> Deduplication uses `(scope, source_system, source_ref, entry_role)` where `entry_role`
> distinguishes the parts of an AD-38 split. `NormalizedEvent.natural_key` includes the
> `DataScope`; the storage service maintains one dedup set per scope. A single subject
> legitimately produces one record per owning scope and never more than one per scope.

---

## D4-2 · `Meeting` is a global entity with no owning scope, and AD-38 routes by owner `[CONFIRMED]`

**AD-34, Rule, element 1:**
> A closed set of **global entities that belong to no project** takes the scopeless two-part form `<system>:<native_id>` — today just `meeting:mtg_01HX`.

**AD-38, Rule:**
> an entry belongs to the scope that **owns its subject**

**AD-33, Rule:** > **`Meeting` is a first-class Tier-1 record**: id, calendar event reference, title, start, duration, attendees, derived-transcript pointer, processing status.

**Unit A — `core/meetings` (FR-32 pre-meeting prep, FR-03 man-hour cost).** Reads AD-34
literally: a Meeting belongs to no project, therefore its subject is unowned by any
project scope, therefore `CALENDAR_EVENT_HELD` entries and man-hour-cost records go to the
**application** scope's `event_log/` — the only scope not tied to a project or a person.
Compliant.

**Unit B — `core/extraction` + `core/commitments` (FR-05, FR-33).** A meeting-derived
commitment cites `meeting:<id>` (AD-33), and a `Commitment` "always lives in project scope"
(AD-28). The subject of the entry is the meeting, so it routes the meeting-held entry to
**`.project-ai/memory/event_log/`** alongside the commitment it produced. Equally compliant.

**The exact incompatibility.** The routing function AD-38 declares "unambiguous" is not a
total function: for every scopeless global entity — the only class AD-34 defines — there
is no owning scope to route to. FR-03's man-hour aggregation reads A's ledger and finds
nothing; FR-34's verifier folds B's ledger and finds no meeting record for the commitment
it is verifying.

**Worse than a split brain: it is a privacy decision made by accident.** Unit B's rule
routes a **1:1 coaching meeting** into the git-committed project ledger whenever the
extraction ran while the CLI was bound to a registered repo (AD-11: "The CLI, when run
inside a registered repository, binds to that project scope"). AD-38's general invariant
forbids that — "No record written to a git-committed scope may reference personal-scope
material" — but the meeting ref *carries no scope*, so nothing can detect the violation.
The code confirms it: `pm_ai/domain/meetings.py` `Meeting` has **no scope field**, and
`pm_ai/app/wiring.py` hard-codes `scope = DataScope(ScopeKind.PROJECT, project)` for
everything the daemon persists.

**Closing AD (tighten AD-34.1 + AD-38):**
> Every global entity carries an explicit `owning_scope: DataScope` recorded on its Tier-1
> record at mint time; `meeting:<id>` is scopeless *in the reference grammar only*.
> AD-38's routing is a total function `subject → DataScope` resolved through the entity's
> record, and a subject whose owning scope cannot be resolved is a construction error,
> not a default to the ambient scope.

---

## D4-3 · One skill registry or many: AD-1's owning-scope log and AD-37's per-target lock pull opposite ways `[CONFIRMED]`

**AD-1, class M row:**
> Registry-authorized with declared permissions (AD-18); idempotency-keyed (AD-20); **one entry per invocation in the owning scope's `event_log/`** (AD-38)

**AD-37, Rule:**
> Mutations targeting the same external entity serialize through a **per-target lock keyed by `target_ref`**, so two approved changes to one work item cannot interleave.

**Unit A — per-scope registries.** To satisfy AD-1's "owning scope", the composition root
builds one `SkillRegistry` per scope, each holding that scope's `event_log` handle. A
personal HR sync logs to the personal ledger; a GitLab comment logs to the project ledger.
Compliant with AD-1, AD-18, AD-24, AD-38.

**Unit B — one global registry.** To satisfy AD-37, the lock table must be global: two
registries hold two lock dictionaries, and a `target_ref` locked in one is unlocked in the
other. So there is exactly one registry, and it logs to the one scope it was constructed
with. Compliant with AD-37, AD-20, AD-18.

**The exact incompatibility.** A satisfies AD-1 and breaks AD-37 (the lock serializes
nothing across registries — literally the D-4 defect from r2 reintroduced at a different
granularity). B satisfies AD-37 and breaks AD-1 (a mutation on a personal-scope subject is
logged into the git-committed project ledger — an AD-38 violation on the audit path itself).
Neither AD names the registry's cardinality.

**The code picked B, and the leak is live.** `pm_ai/skills/registry.py`:

```python
class SkillRegistry:
    def __init__(self, storage, *, scope: DataScope) -> None:
        self._scope = scope
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
    ...
            self._storage.append_event_log(  # AD-1
                f"- [skill] {qualified_name} target={target.lock_key} ...",
                scope=self._scope,
            )
```

`self._scope` is fixed at construction — `wiring.build()` passes `DataScope(PROJECT, alpha)`
— so *every* invocation, including `("hr","sync_goal")` and `("outlook","send_email")`
which are in `VERB_REGISTRY` for personal-scope flows, writes its entry into the repo.
`self._locks` is an **instance** attribute, so the moment a second registry exists AD-37's
guarantee evaporates.

**And AD-38's own guard is not on this path.** `StorageService.append_event_log()` — the
method AD-1 mandates for every class-M mutation — **never calls `assert_writable()`**.
Only `persist_events()` does. The one function written to enforce AD-38 is absent from the
one path AD-1 requires.

**Closing AD (tighten AD-1 class M + AD-37):**
> The skill registry is a singleton owned by the composition root; the per-`target_ref`
> lock table is process-global and lives in the storage service, not in the registry.
> The invocation log entry routes to the **owning scope of the target**, resolved from
> `TargetRef` at invocation time — never to a scope fixed at registry construction — and
> `append_event_log` applies AD-38's `assert_writable` guard on every call.

---

## D4-4 · AD-38's `assert_writable` checks one attribute that domain events do not have `[CONFIRMED — code hole the spine does not mention]`

**AD-38, Rule, general invariant:**
> **No record written to a git-committed scope may reference personal-scope material — not by content, not by `source_ref`, not by scope name.**

**The implementation, `pm_ai/domain/disclosure.py`:**

```python
def assert_writable(record: object, *, scope: DataScope) -> None:
    if isinstance(record, DisclosureRecord) and scope != DISCLOSURE_LEDGER_SCOPE:
        raise CommittedScopeLeak(...)
    if not scope.is_git_committed:
        return
    scopes = getattr(record, "contributing_scopes", None) or ()
    if any(getattr(s, "is_personal", False) for s in scopes):
        raise CommittedScopeLeak(...)
```

**Unit A — the disclosure ledger writer.** Emits `DisclosureRecord`, which has
`contributing_scopes`. Guarded. Green test (`test_ad38_no_committed_record_may_reference_personal_scope`
constructs a stub class with exactly that attribute).

**Unit B — every other writer in the system.** `NormalizedEvent` has no
`contributing_scopes` — its fields are `scope, type, source_ref, actor, occurred_at,
payload, authored_by, ingested_at`. So for the only record type the storage service
actually persists, `getattr(...)` returns `None`, the loop is empty, and **the check is a
no-op**. `persist_events()` calls `assert_writable(stamped, scope=scope)` on every event
and the call can never fire.

**The exact incompatibility.** AD-38 forbids referencing personal material "by content,
not by `source_ref`, not by scope name"; the guard inspects **none of those three**. A
`MESSAGE_POSTED` event whose `MessagePayload.excerpt` quotes a coaching conversation, or a
`CALENDAR_EVENT_HELD` whose `source_ref` is a 1:1's `meeting:<id>`, writes into the
git-committed project ledger with the AD-38 test suite green. Unit A's authors believe the
invariant is enforced; Unit B's authors get no signal that it is not.

**This is the *Rule weaker than its own Prevents* case.** AD-38's Prevents says "the audit
mechanism becoming the leak" and names three vectors. Its enforcement covers one attribute
on one class.

**Closing AD (tighten AD-38):**
> `assert_writable` takes a structural walk, not an attribute lookup: any `DataScope`,
> `SourceRef`, or `Actor` reachable from the record — including inside typed payloads —
> is checked, and any `SourceRef` naming a global entity is resolved to its owning scope
> before the check. A record type that cannot be walked is rejected rather than passed.

---

## D4-5 · `disclosure.md` is simultaneously "a single file" and a Tier-1 segmented ledger `[CONFIRMED]`

**AD-38, Rule, table:**
> **Disclosure & cost** … `~/.pm-ai/disclosure.md`, a **single** append-only Tier-1 ledger

**AD-3, Tier 1 row:**
> Plaintext markdown, append-only, hand-editable, git-diffable. A backup target. **Bounded by FR-37 compaction, which replaces whole sealed segments rather than rewriting lines (AD-5).**

**AD-5, Rule:**
> **Append-only means no file is ever edited in place.** … Each ledger is a directory of dated segments (`event_log/2026-08.md`). Exactly one segment is *open* and appended to; all earlier segments are **sealed and immutable**.

**Unit A — the FR-37 pruning job (`storage/` + pruning job, per the Capability map).**
`disclosure.md` is Tier 1, and Tier 1 is bounded by segment-replacing compaction, and
compaction requires sealed segments. So it segments the ledger to
`~/.pm-ai/disclosure/2026-08.md`. Compliant with AD-3 and AD-5 — indeed *required* by them,
since an unbounded single file violates FR-37 and the only permitted bounding mechanism
needs segments.

**Unit B — the audit and cost readers.** AD-31.2: "The CLI answers *'what has left this
machine, and when'* from **that one file**." AD-17: "the running monthly total surfaces in
briefings and CLI." Both open `~/.pm-ai/disclosure.md`. Compliant with AD-38 and AD-31.

**The exact incompatibility.** After A ships, B reads a path that does not exist and
reports **zero disclosures and zero spend** — the audit affirms that nothing has left the
machine. That is the failure AD-31.1 explicitly argues is worse than no charter
("a charter that means something narrower than its words is worse than no charter"),
produced by two units each obeying the ADs.

**Code takes A's side structurally.** `pm_ai/storage/service.py::_segment()` builds
`<scope>/<ledger>/<YYYY-MM>.md` for *every* ledger — the single writer has no code path
that produces a flat file. Meanwhile `pm_ai/domain/storage_tiers.py` registers
`"disclosure.md": Tier.TRUTH` as a **file** while `"event_log/"` and `"meetings/"` carry
directory slashes. The two modules already disagree.

**Closing AD (tighten AD-38 + AD-3):**
> The disclosure ledger is a segmented Tier-1 directory `~/.pm-ai/disclosure/` like every
> other Tier-1 ledger; "single" in AD-38 means **one scope, one ledger**, not one file.
> AD-31 and AD-17 read it through one `fold_disclosure()` API in `core`, and no component
> opens a ledger path directly.

---

# Part 2 — Predicates named but not defined (3 pairs)

## D4-6 · AD-36 defines who may be *demoted* to `pm_ai` and never who may *assert* `external` `[CONFIRMED]` ⚠ TOP

**AD-36, Rule:**
> Every event carries `authored_by ∈ {external, pm_ai, unknown}`. **Only `external` is admissible as evidence.** … `Provenance.UNKNOWN` is the envelope default, so **an adapter that forgets to attribute fails closed** rather than silently vouching. … normalization **marks any harvested event matching one of those as `pm_ai`**.

**Unit A — the GitLab connector.** It harvested from an external system, so it stamps
`authored_by=EXTERNAL` and relies on normalization's demotion path (which AD-36 defines)
to correct pm-ai's own writes. Compliant: AD-36 never forbids an adapter asserting
`external`, and AD-36's second mechanism ("Where the connector runs under a distinct bot
identity, actor resolution marks it independently") presumes connectors participate in
attribution.

**Unit B — the Teams connector.** Reads "the envelope default … fails closed" as
instruction: leaves `authored_by` unset, expecting normalization to be the sole authority.
Equally compliant.

**The exact incompatibility.** A **fails open** — every gap in the executed-key match
(a restore, a comment posted by an earlier build, a bot identity change) leaves pm-ai's own
comment stamped `EXTERNAL`, and `evaluate_commitment` returns `FULFILLED` on telemetry the
system manufactured. That is AD-36's *Prevents* clause verbatim. B **fails closed to the
point of uselessness** — AD-36 grants normalization only the power to mark events `pm_ai`;
nothing in the spine authorizes anyone to promote `unknown → external`, so under B no
commitment can ever reach `FULFILLED`.

**The code already picked A.** `pm_ai/connectors/gitlab.py`, inside the event constructor:

```python
authored_by=Provenance.EXTERNAL,
```

Unconditional, on the only connector that exists. `pm_ai/domain/events.py` meanwhile
documents the opposite intent: `authored_by: Provenance = Provenance.UNKNOWN` with
"an adapter that forgets to attribute fails closed." The one adapter does not forget — it
overrides — and the default that makes AD-36 safe is dead code.

**Compounding: AD-3 says the correction mechanism can be lost.** "Restoring Tier 2 from a
backup opens a re-execution window. Mutations performed after the backup point are absent
from the restored executed-key ledger." AD-3 states the duplicate-write consequence and
**never states the attribution consequence** — the same missing rows mean pm-ai's own
comments become permanently indistinguishable from external evidence.

**Closing AD (tighten AD-36):**
> No inbound adapter may set `authored_by`. Attribution is assigned **only** at
> normalization, which may set `pm_ai` (executed-key match or bot-identity match),
> `external` (positive attribution to a resolved non-pm-ai `Actor`), and `unknown`
> otherwise. An event arriving from an adapter with `authored_by != UNKNOWN` is a
> construction error. After a Tier-2 restore, every event ingested since the backup point
> is re-attributed `unknown` until reconciled — the restore is a recovery event for
> attribution as well as for idempotency.

---

## D4-7 · AD-20 fixes the key formula and leaves both operands undefined `[CONFIRMED]`

**AD-20, Rule:**
> The key is **derived deterministically** from `(job_type, target_ref, payload_hash)`

**Consistency Conventions:**
> Idempotency keys | `sha256(job_type + target_ref + canonical_payload)` — deterministic, never random (AD-20)

**Unit A — the transcript pipeline (FR-05/FR-07 auto-execute path).** `job_type` is the
verb; `payload` is the extraction detail. This is literally what `pm_ai/app/pipelines.py`
does:

```python
key = idempotency_key(ex.detail["verb"], target.lock_key, ex.detail)
inv = daemon.skills.invoke(
    f"{provider}.{ex.detail['verb']}",
    target=target, payload={"comment": ex.detail["rest"]}, idempotency_key=key,
)
```

**Unit B — the proposal executor (AD-13: "features … **register a proposal type** with a
payload schema and an executor callback").** `job_type` is the registered proposal type
(`"gitlab_comment_proposal"`); `payload` is the executor's declared payload schema —
the dict actually sent, `{"comment": ...}`.

**The exact incompatibility.** The same real-world mutation — post this comment on this
work item — yields two different keys. `SkillRegistry.invoke`'s replay guard
(`if self._storage.was_executed(idempotency_key)`) misses, and the comment posts twice.
Both units satisfy AD-20 exactly: deterministic, never random, from the stated triple.

**Two distinct undefined operands, both visible in four lines of shipped code:**

1. **`job_type` has no closed vocabulary.** AD-27 closes the event-type enumeration and
   AD-34 closes the reference grammar precisely because open vocabularies split joins —
   `job_type` got neither treatment, and it is a *key* input, not a display field.
2. **The hashed payload is not the sent payload.** `ex.detail` is `{"verb", "target",
   "rest"}`; the skill receives `{"comment": ex.detail["rest"]}`. Two calls that send
   byte-identical payloads hash differently if their extraction detail differed by a
   whitespace change in `target`, and two calls that send *different* payloads could hash
   identically. The guarantee AD-20 buys is over a dict nobody transmits.

**Closing AD (tighten AD-20):**
> `job_type` is a closed enumeration in `domain`, one member per registered proposal type
> / job kind, versioned like AD-27's taxonomies. The hashed payload is **the payload
> passed to the skill**, canonicalized by `canonical_payload`, and the key is computed by
> the skill layer at invocation from `(job_type, target.lock_key, payload)` — never by the
> caller, so no caller can hash a different object than it sends.

---

## D4-8 · AD-35 forbids mixing clocks and then requires a predicate that can only mix them `[CONFIRMED]` (extends the unclosed r2 D-10)

**AD-35, Rule:**
> **`occurred_at`** … governs **domain reasoning**: due dates … **`ingested_at`** … governs **operational reasoning**: cursors, watermarks, replay, **sweep windows**. The two are **never substituted for one another**. … **Coverage is recorded.** The scheduler logs harvest coverage windows per connector instance, and the commitment sweeper must not declare `BROKEN` across a window it has no coverage for.

**Unit A — the sweeper.** "Was the promise window covered?" The window runs from the
promise to its due date — both `occurred_at`-basis instants (AD-35: due dates are
`occurred_at`). It tests them against `CoverageWindow`, which the code documents as
"Recorded in `ingested_at` terms — the local clock." A cross-clock comparison, which AD-35
forbids — but it is the only way to ask the question AD-35 mandates.

**Unit B — the scheduler-side coverage reader.** Refuses to cross clocks. Defines
`covered` purely in `ingested_at` space: "a harvest for this instance completed within the
last cadence." Also compliant.

**The exact incompatibility.** Commitment due Friday, laptop asleep Fri–Sun, harvest runs
Monday 09:00, sweeper runs Monday 09:05. A: the Fri–Sun interval intersects no coverage
window → `UNKNOWN`, silence. B: a harvest ran five minutes ago → covered → `BROKEN` → and
AD-35 itself says "FR-26's nudges are irreversible." Two compliant sweepers, one of which
fires an unrecallable "why isn't this done" about delivered work. That is AD-35's *Prevents*
clause reproduced by AD-35-compliant code.

**The type system does not help.** `pm_ai/domain/lifecycle.py`:

```python
class CoverageWindow:
    connector_instance: str
    start: datetime
    end: datetime
    def covers(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end
```

`datetime` is `datetime`. The docstring warns that asking in `occurred_at` terms is a bug
and the signature permits nothing else. And `evaluate_commitment(..., covered: bool)` takes
the answer as a bare bool computed by a caller that does not exist yet — the fail-closed
guard is enforceable only by whoever remembers which clock feeds it.

**[RE-CONFIRM] the r2 D-10 quantifier half is still open.** AD-35 says windows are "per
connector instance" and never says *whose* coverage a given commitment needs. `any`-instance
and `every`-instance readings are both compliant and produce, respectively, false `BROKEN`
on a Jira outage and a permanently decorative FR-34.

**Closing AD (tighten AD-35):**
> `occurred_at` and `ingested_at` are **distinct types** in `domain`
> (`WorldTime` / `LocalTime`), not both `datetime`; no function accepts one where the
> other is meant. Coverage windows are recorded in **both** bases — the local interval the
> harvest ran, and the `occurred_at` interval the provider query actually spanned — and
> the sweeper's predicate is over the `occurred_at` interval. `Commitment` records
> `evidence_instances`, derived from the `system` and scope segments of its target ref;
> `BROKEN` requires continuous coverage from **every** such instance across
> `[created_at, due]`, anything less is `UNKNOWN`, and an `UNKNOWN` older than twice its
> window escalates as a question, never as an FR-26 nudge.

---

# Part 3 — Contradictions between an AD and a capability the spine ships (2 pairs)

## D4-9 · AD-31 forbids the career-dossier flow the spine's own capability map requires `[CONFIRMED]`

**AD-31, Rule, obligation 3:**
> **The boundary is on the destination, not only the source.** Personal-scope material must never enter a prompt whose output is bound for a project-scope artifact **or an external system.**

**AD-31, Rule, opening:**
> FR-16's adversary is **employer-controlled systems** — team channels, shared repositories, enterprise dashboards, **HR platforms** — not model APIs.

**Capability → Architecture Map:**
> Custom metric monitoring and **career dossiers (FR-30, FR-31)** | `core/metrics` + **HR skill adapter** | AD-13, AD-15, AD-25

**AD-10, Rule:** > Personal-scope instances (**HR platforms**, article sources, personal calendar) are separate instances under the personal scope.

**Shipped verb, `pm_ai/domain/lifecycle.py`:** `("hr", "sync_goal"): Verb("sync_goal", "hr", SkillPermission.CREATE, False)`

**Unit A — the FR-31 career-dossier feature.** Reads personal-scope goals and coaching
history, calls `draft_generation` (frontier-eligible per AD-15), stages a Proposal (AD-13),
and on approval executes `hr:sync_goal` (class M, AD-1). Reads AD-31's "external system"
as meaning *project-facing* external systems, since AD-25 and AD-28 already keep personal
material out of project scope and FR-31 would otherwise be unbuildable.

**Unit B — the model-boundary enforcer in `core`/`ModelRouter`.** Implements AD-31.3
literally, exactly as the suite requires (`test_ad31_personal_material_cannot_reach_a_
project_destination` asserts `ScopeBoundaryViolation`). Any prompt with a personal
contributing scope and an external destination raises.

**The exact incompatibility.** Under B, FR-30/FR-31 cannot be built at all. Under A, AD-31.3
is advisory — and its Prevents clause ("each feature deciding independently what may enter a
prompt") is realized: each feature decides which external systems the boundary means.
The spine simultaneously **names HR platforms as the adversary**, **routes an HR connector
into the personal scope**, **ships an `hr:sync_goal` mutation verb**, and **forbids personal
material reaching an external system through a model**. Three of those four are compliance
requirements on the same feature.

**Closing AD (tighten AD-31.3 + AD-15):**
> The destination boundary distinguishes **user-directed egress** from **employer-visible
> egress**. A frontier output derived from personal-scope material may reach an external
> system only when (a) the destination is a personal-scope connector instance registered
> under AD-10, and (b) the mutation is an approved Proposal whose card names the
> destination system explicitly to the PM. Everything else is refused. AD-31's opening
> paragraph must stop naming HR platforms as the adversary if FR-31 ships an HR writer,
> or FR-31 must move to Deferred.

---

## D4-10 · AD-32 mandates one-tap undo; AD-18's permission enum cannot express it and AD-32's own registry forbids it `[CONFIRMED]`

**AD-32, Rule, closing:**
> Every auto-execution emits a card carrying **one-tap undo**, plus its `event_log/` entry.

**AD-18, Rule:**
> each declaring the **`SkillPermission`s** it may exercise (`read`, `comment`, `edit`, `transition`, `create`, `send`); the daemon **refuses** to invoke an unlisted skill or a call exceeding its declared permissions

**AD-32, Rule, element 3:**
> the verb is **auto-executable** — registered in `domain`'s verb registry, keyed on `(provider, verb)` … an **unregistered verb never auto-executes**

**Unit A — the comment skill's undo, as a retraction comment.** The forward verb was
`("gitlab","post_comment")`, `reversible=True`. Its inverse is a delete, and there is **no
`delete` member in `SkillPermission`** — the enum ends at `send`. So A implements undo as a
second `post_comment` ("retracted: …"), staying inside its declared `COMMENT` permission.
Compliant with AD-18.

**Unit B — the same undo, as an edit-to-empty.** Declares `EDIT`, uses
`("gitlab","edit_description")`-style semantics to blank the comment body. Also compliant
with AD-18.

**The exact incompatibility, three ways.**
1. **Evidence divergence.** A's undo produces a *new* external event with a new natural key,
   which AD-34's dedup admits and AD-36 will attribute; FR-34's verifier sees activity on
   the work item. B's undo produces no new event. The same user tap leaves two different
   evidence trails, and AD-36's "self-authored activity may be displayed for context but
   never counted" only saves the case where attribution succeeded (see D4-6).
2. **The undo is not one tap under AD-32's own rule.** `VERB_REGISTRY` contains **no inverse
   verbs at all** — no `delete_comment`, no `unset_label`, no `revert_*`. AD-32 says an
   unregistered verb never auto-executes, so the undo of an auto-execution must itself be
   staged as a Proposal and approved. "One-tap undo" costs two taps and a card.
3. **`reversible=True` asserts an inverse that nothing requires to exist.** AD-32 argues at
   length that "Reversibility is not a property of the verb alone" and keys the registry on
   the pair — but it never requires that the reversing verb be registered, permitted, or
   reachable. `reversible` is a claim about the world with no obligation attached to the system.

**Closing AD (tighten AD-32 + AD-18):**
> `Verb.reversible` is expressed as `inverse: VerbKey | None`; `reversible` is derived as
> `inverse is not None`, and a registry entry whose `inverse` is not itself registered
> fails at import. `SkillPermission` gains `delete`. The inverse verb inherits the forward
> verb's authorization — an undo of an auto-execution executes on one tap without a new
> Proposal, and is recorded as a distinct `event_log` entry linked to the forward
> invocation's idempotency key so FR-34 can net them out.

---

# Part 4 — Two more, from the code (2 pairs)

## D4-11 · `Proposal.transition` enforces terminality but no edge set — `staged → executed` is legal `[CONFIRMED — code hole]`

**AD-13, Rule:**
> status (`staged → approved → executing → executed`, or `→ rejected | expired | superseded`). `executing` is a real state, not a transient — it is the **CAS latch** AD-37 uses to make expiry and execution mutually exclusive.

**AD-37, Rule:**
> the sweeper CASes `staged → expired`, the worker CASes `approved → executing`, and whichever loses observes the winner and stops.

**Unit A — the standard approval flow.** `staged → approved → executing → executed`.

**Unit B — a fast path for AD-32 auto-executions** (which produce a card and an event but,
per AD-13, "No external mutation derived from *implicit extraction* may execute without an
approved Proposal" — auto-executed *explicit* commands are outside that sentence). Records
the outcome as `staged → executed` in one hop.

**The exact incompatibility.** Under B, `executing` is never entered, so the AD-37 latch
that makes expiry and execution mutually exclusive is bypassed: the sweeper's
`staged → expired` CAS and B's `staged → executed` CAS both start from `staged`, and
whichever loses raises `VersionConflict` — which AD-37 says means "reload and re-evaluate",
so B reloads, sees `expired`, and... AD-13 says "an expired proposal never executes", but
B already performed the mutation. Both units obey every written CAS rule.

**The code permits B outright.** `pm_ai/domain/proposals.py::transition` checks *only*
version and terminality:

```python
if expected_version != self.version: raise VersionConflict(...)
if self.state.is_terminal:           raise TerminalState(...)
return replace(self, state=to, version=self.version + 1)
```

There is no legal-edge table. `Proposal(state=STAGED).transition(ProposalState.EXECUTED,
expected_version=1)` succeeds. AD-13 states the sequence in prose and nothing enforces it —
`test_ad37_expired_proposals_cannot_execute` only exercises the terminal check.

**Nobody owns the exit from `executing`, either.** `executing` is not terminal, and the
only transitions any AD assigns are *into* it. A worker that CASes `approved → executing`
and then crashes leaves an orphan forever. Unit C (crash recovery) reclaims it as
`executing → approved` and re-runs — safe only if the Tier-2 key survived, which AD-3 says
it may not. Unit D (restart reconciler) treats it as "already sent" and CASes
`executing → executed` without ever performing the mutation. Both readings are consistent
with AD-13 and AD-37, which name neither an owner nor a lease.

**Closing AD (tighten AD-13 + AD-37):**
> The legal edge set is a table in `domain`, enforced in `Proposal.transition`; a
> transition not in the table is a construction error. `executing` carries a
> `lease_expires_at` owned by the job worker; on expiry the scheduler CASes
> `executing → approved` **only after** confirming from the Tier-2 executed-key ledger
> that no mutation was recorded, and `executing → executed` otherwise. Exactly one
> component may leave `executing`, and it is named here.

---

## D4-12 · AD-32's speaker check is `resolves to the PM`; the code compares raw handles against a mutable global `[CONFIRMED — code divergence]`

**AD-32, Rule, condition 2:** > the speaker **resolves to the PM**

**AD-34, Rule, element 2:**
> Every event carries an `actor_id` **resolved** to a single `Actor` in `domain`. Connectors supply their native handle (commit email, tenant account, **speaker label**); normalization maps it through an **alias table**. An unresolvable handle becomes an explicit `unresolved` actor — **never** a raw string used silently as identity.

**Unit A — resolve-then-compare.** Calls `resolve_actor(system="graph", handle=speaker)` and
compares `Actor.actor_id` to the PM's actor. Compliant with AD-32 and AD-34.

**Unit B — the shipped code.** `pm_ai/core/extraction.py`:
`speaker_is_pm=(u.speaker_handle == pm_handle)`, where `pm_handle` is a single string on the
`Daemon` dataclass (`pm_handle: str = "andrei@example.com"`). A raw handle compared against
one configured handle — precisely the "raw string used silently as identity" AD-34 forbids,
on the one decision that gates unapproved external writes.

**The exact incompatibility.** Graph issues tenant UPNs; the alias table exists because the
same person arrives under different handles from different providers. Under B, a PM whose
tenant UPN differs from `pm_handle` never auto-executes (fail-closed, merely broken); a PM
who configures `pm_handle` to a display name that another attendee can also present
auto-executes for the wrong speaker (fail-open). Under A the answer depends on
`ALIASES` — a **module-level mutable dict in `domain`** with no owner, no tier in
`ARTIFACT_TIER`, no backup, and no rule about when it is loaded. Same transcript, same
build, different authorization outcome depending on boot order.

**[RE-CONFIRM]** r2's D-8 flagged the alias table's missing owner/tier and it is still not
in AD-3's tier table nor in `ARTIFACT_TIER`. This pair is the security consequence of that
gap, which is new.

**Closing AD (tighten AD-32 + AD-34):**
> AD-32's speaker condition is expressed over `Actor`, never over a handle string:
> `resolve_actor(...) == config.pm_actor`, and an `unresolved` speaker always stages.
> The alias table is a Tier-1 artifact in the application scope, listed in `ARTIFACT_TIER`,
> loaded by the composition root and injected — never a mutable module global read from
> ambient state, for the same reason `now` is injected under the Clocks convention.

---

# Part 5 — Enforcement is weaker than the Enforcement section claims

**The spine says:**
> The spine is executable, not just readable. A document people must remember is a document that decays; **these checks fail the build instead.**
> **Phase 1 exit criterion: zero skips in `tests/architecture/`.**

**Measured, this repo, today:** `65 passed, 32 skipped`.

**The mechanism, `tests/architecture/test_domain_invariants.py`:**

```python
def mod(dotted: str):
    try:
        return importlib.import_module(dotted)
    except ModuleNotFoundError:
        pytest.skip(f"{dotted} not implemented yet (Phase 1)")
```

Every check for AD-2, AD-6, AD-8, AD-15, AD-17, AD-21, AD-22, AD-25, AD-28 and the
`core.ledger` / `core.commitments` / `domain.clocks` / `models.router` halves of AD-31,
AD-35 and AD-36 currently **skip**. That is defensible while the modules are unbuilt. What
is not defensible is that the same mechanism **fails open forever**: renaming
`pm_ai.models.router` silently disarms both AD-31 checks — the two that make the privacy
charter falsifiable — and the suite stays green. The spine's own rule ("do not edit a check
without editing its AD") is enforced by memory, which is the failure mode the Enforcement
section opens by rejecting.

**Closing change:** a manifest test that asserts the exact set of expected-missing modules,
failing when the set shrinks *or grows*. A module that once existed and disappeared is a
failure, not a skip.

---

# Part 6 — Priority

Ranked by (irreversibility × silence).

| # | Pair | Failure | Close by |
|---|---|---|---|
| 1 | **D4-6** | `FULFILLED` on pm-ai's own comment; the ledger is confidently wrong toward success | Tighten AD-36 — adapters may not set `authored_by` |
| 2 | **D4-1** | The personal half of every cross-scope split is silently dropped as a duplicate | Tighten AD-34.3 — natural key carries scope |
| 3 | **D4-3 + D4-4** | Personal-scope mutations logged into the employer's repo; AD-38's guard is a no-op on the only record type persisted | Tighten AD-1 class M and AD-38's `assert_writable` |
| 4 | **D4-8** | Irreversible FR-26 nudge on delivered work — AD-35's own Prevents, reproduced by compliant code | Tighten AD-35 — distinct clock types, `evidence_instances` |
| 5 | **D4-2** | Meeting-subject entries have no owning scope; a 1:1 can route into a committed ledger undetectably | Tighten AD-34.1 + AD-38 — global entities carry an owning scope |
| 6 | **D4-7** | Double external write; the replay guard misses on a key nobody agrees on | Tighten AD-20 — closed `job_type`, key computed by the skill layer |
| 7 | **D4-9** | FR-30/FR-31 unbuildable, or AD-31.3 advisory | Tighten AD-31.3, or move FR-31 to Deferred |
| 8 | **D4-5** | The audit reports "nothing has left this machine" | Tighten AD-38 — disclosure is a segmented ledger with a fold API |
| 9 | **D4-11** | Mutation performed on a proposal that expired; orphaned `executing` | Tighten AD-13 + AD-37 — edge table, lease, named owner |
| 10 | **D4-10** | Undo is not one tap; two compliant undos leave two evidence trails | Tighten AD-32 + AD-18 — `inverse` verb, `delete` permission |
| 11 | **D4-12** | Auto-execute authorization decided by string equality against a global | Tighten AD-32 + AD-34 — compare `Actor`s; alias table is Tier 1 |
| 12 | **Part 5** | An AD's enforcement can be disarmed by deleting a module | Manifest test over expected-missing modules |

**Shape of the fix.** Ten of the twelve are one clause each, and eight of those ten are
clauses whose *Prevents* is already written correctly in the spine and whose *Rule* misses
it by a sentence. That pattern — the document names the failure and stops one clause short
— is the same one r2 reported, which suggests the revision loop is converging but is not
yet done: each round the ADs get more precise about mechanism and stay silent about the
*operands* of the predicates they introduce. The three predicates left undefined after r4
are `covered` (AD-35), `job_type` (AD-20), and who may assert `external` (AD-36).

**What survived attack.** Layering and the composition root (AD-30) — I could not construct
a pipeline with no legal home. Tier separation (AD-3) — the physical split plus
`assert_reindex_safe` is genuinely structural. The reference grammar (AD-34.1) and
`TargetRef` sub-resource rejection. The `SkillPermission`/`DataScope` type split (AD-18).
Typed payloads (AD-27) at the top level. Egress classification (AD-1) — the four-class
table matches the stack and matches `.importlinter`. These are not soft spots.
