# Review R4 — Code Ratification

**Date:** 2026-08-19
**Reviewer role:** fresh reviewer, no prior context in this document set
**Artifacts under review:**

- SPINE — `_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md` (38 ADs, status `revision-in-progress`, updated 2026-08-19)
- CODE — `pm_ai/` (37 modules), `tests/` (97 tests), `.importlinter`, `tests/architecture/`

**Method:** every claim below quotes both sides — spine line and code line with `file:line`. Where a claim could be settled by running something, it was run. Two findings were verified by injecting a deliberate violation into the tree and observing that the suite stayed green; the tree was restored immediately afterwards and `git status pm_ai tests` is clean.

## Reality as measured

```
$ uv run pytest tests -q
66 passed, 31 skipped in 0.46s

$ uv run lint-imports
Analyzed 50 files, 117 dependencies.
Contracts: 12 kept, 0 broken.
```

The 31 skips resolve to 16 distinct missing modules:

| Missing module | Skips | ADs the skips belong to |
|---|---|---|
| `pm_ai.models.router` | 9 | AD-15 (×6), AD-17, AD-31 (×2) |
| `pm_ai.core.commitments` | 5 | AD-28, AD-33, AD-35, AD-36, AD-14 |
| `pm_ai.core.proposals` | 3 | AD-13, AD-37 (×2) |
| `pm_ai.core.transcripts` | 2 | AD-23 (×2) |
| `pm_ai.core.taxonomy` | 1 | AD-27 |
| `pm_ai.core.scheduler` | 1 | AD-9 |
| `pm_ai.core.rendering` | 1 | AD-25 |
| `pm_ai.core.retrieval` | 1 | AD-22 |
| `pm_ai.core.dispatch` | 1 | AD-21 |
| `pm_ai.core.ledger` | 1 | AD-35 |
| `pm_ai.storage.crypto` | 1 | AD-6 |
| `pm_ai.storage.reindex` | 1 | AD-3 |
| `pm_ai.domain.clocks` | 1 | AD-35 |
| `pm_ai.surfaces.telegram.bridge` | 1 | AD-2 |
| `pm_ai.surfaces.api.app` | 1 | AD-8 |
| `pm_ai.connectors.registry` | 1 | AD-27, AD-34 |

**Verdict:** the reconciliation genuinely moved the spine toward the code — AD-3, AD-13, AD-14, AD-27, AD-32, AD-33, AD-34, AD-36, AD-37, AD-38 now describe types that exist with the names the spine uses, and that is real work. But the Enforcement section overstates what runs, four capabilities it names by hand have zero live checks, and the two most load-bearing static rules are bypassable by ordinary Python idioms — demonstrated, not inferred.

---

# DIRECTION 1 — spine says something the code contradicts

## D1-1 [HIGH] AD-9's "exactly one method" is false in the port the spine's own paradigm table points at

**Spine, AD-9 (line 148):**

> Every connector implements exactly one method — `harvest(since: Cursor) -> HarvestResult` — and does only auth, fetch, and map-to-schema.

**Code, `pm_ai/ports/__init__.py:16-26`:**

```python
class ConnectorPort(Protocol):
    """AD-9 — one method, and no scheduling of its own."""
    name: str
    system: str
    def emits(self) -> frozenset[NormalizedEventType]:
    def harvest(self, since: Cursor) -> HarvestResult:
```

Two methods and two attributes. `pm_ai/connectors/gitlab.py:31` implements `emits()`, and the skipped `test_ad27_connectors_only_emit_core_declared_event_types` (test_domain_invariants.py:100) depends on it existing. `emits()` is a good design — AD-27 needs it — but AD-9's sentence is now wrong, and the port docstring at `ports/__init__.py:18` repeats the wrong claim directly above the second method.

**Compounding:** `pm_ai/connectors/__init__.py:3` still states the pre-`HarvestResult` signature:

> Each implements harvest(since: Cursor) -> list[NormalizedEvent]

which contradicts AD-9's `HarvestResult` and, more importantly, drops the `CoverageWindow` that AD-9:148 says "rides in the return type precisely so a connector cannot omit it and leave the sweeper's fail-closed guard silently unarmed." A builder reading the package docstring builds the connector that disarms AD-35. `tests/architecture/README.md:116` repeats the same stale signature.

**CONFIRMED.**

**Fix:** AD-9 → "Every connector implements one harvest method plus a `emits()` declaration of the taxonomy subset it produces (AD-27), and nothing else." Correct both docstrings.

---

## D1-2 [HIGH] `TranscriptSourcePort` does not exist, and the two transcript adapters have incompatible signatures

**Spine, AD-23 (line 234):**

> All transcript ingestion goes through `TranscriptSourcePort`.

**Spine, paradigm table (line 27):**

> `ConnectorPort`, `ModelPort`, `StoragePort`, `TranscriptSourcePort`, `SkillPort`, `KeychainPort`, `SurfacePort` — protocols expressed in domain types

**Code:** `pm_ai/ports/__init__.py` defines exactly three protocols — `ConnectorPort`, `StoragePort`, `SkillPort`. A repo-wide grep finds `TranscriptSourcePort` in exactly one place, a docstring:

```
pm_ai/connectors/transcripts/__init__.py:1:"""Transcript source adapters behind TranscriptSourcePort (AD-23).
```

There is no such protocol to be behind. And the two adapters do not share a shape:

- `pm_ai/connectors/transcripts/graph.py:16` — `def fetch(self, meeting_id: str) -> Transcript`
- `pm_ai/connectors/transcripts/manual.py:19` — `def load(self, raw: str, *, meeting_id: str | None) -> Transcript`

Different method names, different parameters. `pm_ai/app/wiring.py:48` puts both into `transcripts: dict[str, object]` — typed `object`, because nothing common exists to type them as. This is the precise failure mode AD-23 was written to prevent: "The extraction pipeline must be exercisable end-to-end using only the fallback adapter" is true today only because `run_transcript_ingestion` (`app/pipelines.py:33`) takes an already-built `Transcript` and never calls either adapter.

`ModelPort` (AD-15:186 "One `ModelPort` with two adapters"), `KeychainPort`, and `SurfacePort` are likewise absent — defensible for unbuilt layers, but the paradigm table reads present-tense and 4 of its 7 ports do not exist.

**CONFIRMED.**

**Fix:** either build the port and give both adapters one signature, or mark the four unbuilt ports in the table as planned. AD-23's "goes through `TranscriptSourcePort`" is currently unimplementable as written.

---

## D1-3 [HIGH] AD-27's second closed enumeration does not exist, and the two event-log writers already disagree on entry shape

**Spine, AD-27 (line 258):**

> The set of `NormalizedEvent` types **and the set of `event_log/` entry types** are **closed enumerations defined in `domain`**.

**Spine, AD-27 Prevents (line 257):**

> ...two features writing incompatible entry shapes into the audit trail

**Code:** `pm_ai/domain/events.py:20` defines `NormalizedEventType` — the first enumeration, correctly in `domain`. There is **no** event-log entry-type enumeration anywhere. And the repo already has exactly two writers of event-log lines, emitting two unregistered and incompatible shapes:

`pm_ai/storage/service.py:73-76`:

```python
f"- [{_ulid()}] {stamped.type.value} "
f"actor={stamped.actor.actor_id} src={stamped.source_ref} "
f"occurred_at=... ingested_at={at.isoformat()} authored_by={stamped.authored_by.value}"
```

`pm_ai/skills/registry.py:78-80`:

```python
f"- [skill] {qualified_name} target={target.lock_key} "
f"external_id={external_id} key={idempotency_key}"
```

Two writers, two grammars, zero registry. The second carries **no id and no timestamp**, which contradicts the Consistency Convention at spine line 379:

> Markdown ledger entries | Append-only blocks with a machine-readable header line (**id, timestamp, type**) followed by human-readable body

The `[skill]` line has none of the three. The prediction AD-27 makes has already come true inside the slice it was written to govern.

Also unimplemented: AD-27:258 "**Both enumerations are versioned** so parsers can read historical entries." Neither `NormalizedEventType` nor the entry format carries a version.

**CONFIRMED.**

---

## D1-4 [HIGH] Tier 2 is an in-memory dict, so AD-3's durability promise and AD-20's "durable row" are false in the built slice

**Spine, AD-3 (line 90):** Tier 2 is

> Durable and **not derivable from Tier 1**. Must be backed up. Losing it loses pending external writes and resets harvest position — a real consequence, not a cache miss.

**Spine, AD-3 (line 98):** `~/.pm-ai/private/operational.db` (SQLCipher)

**Spine, AD-20 (line 216):**

> Nothing is scheduled in memory only — every deferred unit of work is a persisted row in the SQLite job queue.

**Code, `pm_ai/storage/service.py:27-34`:**

```python
@dataclass
class _Tier2:
    """Operational state — durable, never rebuilt (AD-3)."""
    cursors: dict[str, Cursor] = field(default_factory=dict)
    proposals: dict[str, object] = field(default_factory=dict)
    coverage: list[object] = field(default_factory=list)
    executed: dict[str, tuple[str, str]] = field(default_factory=dict)
```

Four in-process containers. No file, no SQLite, no SQLCipher, nothing that survives the process. The docstring asserts the property the implementation lacks.

Why this matters more than "the slice is a stub": spine line 653 claims

> Two vertical slices ... prove AD-1, AD-9, **AD-20**, AD-30, AD-32, AD-33, AD-35, AD-36, and AD-37 against running code.

AD-20's load-bearing claim is *durability across restart* — "in-memory timers losing work on restart; duplicate external writes on replay" (spine:215). `test_idempotency_key_is_honoured_not_merely_carried` (test_vertical_slice.py:100) proves the key is honoured **within one process**, against a dict. Its own docstring says "A replay after a crash, a retry, or a **Tier-2 restore** must not write twice" — the crash and restore cases are exactly what the in-memory store cannot survive, and the test cannot see that. The executed-key ledger AD-36:340 also depends on ("the skill layer records every class-M mutation ... in the Tier-2 executed-key ledger") is the same dict.

**CONFIRMED.**

**Fix:** either build the file-backed Tier 2, or narrow line 653 — AD-20's determinism half is proven; its durability half is not.

---

## D1-5 [HIGH] AD-36's second attribution mechanism does not exist, and the connector fails open

**Spine, AD-36 (line 340):**

> Attribution is established at both ends: the skill layer records every class-M mutation it performs (target ref plus the resulting external id) in the Tier-2 executed-key ledger, and **normalization marks any harvested event matching one of those as `pm_ai`**. ... Two mechanisms because one of them will have gaps.

**Spine, AD-36 (line 338):**

> `Provenance.UNKNOWN` is the envelope default, so an adapter that forgets to attribute fails closed rather than silently vouching.

**Code:** the first mechanism exists (`skills/registry.py:76` `record_execution`). The second does not. `grep -rn "executed_mutations" pm_ai` returns exactly two production references:

```
pm_ai/storage/service.py:112:    def executed_mutations(...)          # the definition
pm_ai/skills/registry.py:72:      prior = self._storage.executed_mutations()[idempotency_key]   # the replay check
```

No normalization step reads it. There is no normalization step at all — `run_harvest` (`app/pipelines.py:18-30`) goes harvest → discarded sanitize → persist, despite the module docstring at `pipelines.py:1` naming "harvest → sanitize → **normalize** → persist".

Worse, the connector does not merely forget to attribute; it asserts the admissible value for every event:

**`pm_ai/connectors/gitlab.py:51`:** `authored_by=Provenance.EXTERNAL,`

So a comment pm-ai itself posted via `gitlab.post_comment`, harvested back on the next 4h cycle, arrives stamped `EXTERNAL` and is admissible as fulfilment evidence. That is verbatim the failure AD-36 exists to prevent (spine:335: "FR-06's executor posts a comment to WI-108, FR-34's verifier later reads WI-108 activity as fulfilment evidence"). The `UNKNOWN` default at `domain/events.py:143` is the right guard, and the one adapter in the tree overrides it.

The test that claims to cover this — `test_pm_ai_does_not_verify_its_own_write` (test_vertical_slice.py:161), docstring "exercised end to end" — never harvests. It invokes the skill, asserts the mutation was recorded, and then calls:

```python
evaluate_commitment(overdue=True, evidence_admissible=Provenance.PM_AI.admissible_as_evidence, covered=True)
```

That is a restatement of `Provenance.PM_AI.admissible_as_evidence is False`, which `test_unknown_provenance_is_not_evidence` (line 599) already asserts directly. The loop is never closed; the closing hop is the one that is missing.

**CONFIRMED.**

---

## D1-6 [MEDIUM] AD-38's guard on the actual write path is inert

**Spine, AD-38 (line 363):**

> **No record written to a git-committed scope may reference personal-scope material** — not by content, not by `source_ref`, not by scope name.

**Code, `pm_ai/storage/service.py:71`:** `assert_writable(stamped, scope=scope)  # AD-38` — the one call site in the production path, on every persisted `NormalizedEvent`.

**Code, `pm_ai/domain/disclosure.py:70-83`:**

```python
if isinstance(record, DisclosureRecord) and scope != DISCLOSURE_LEDGER_SCOPE:   # not a NormalizedEvent
    raise CommittedScopeLeak(...)
if not scope.is_git_committed:
    return
scopes = getattr(record, "contributing_scopes", None) or ()                     # NormalizedEvent has none
if any(getattr(s, "is_personal", False) for s in scopes):
    raise CommittedScopeLeak(...)
```

`NormalizedEvent` (`domain/events.py:128-144`) has fields `scope, type, source_ref, actor, occurred_at, payload, authored_by, ingested_at` — no `contributing_scopes`. So `getattr(...) or ()` is always empty and the `any(...)` body is unreachable for every input the pipeline ever passes it. The guard returns clean 100% of the time in production. It fires only in tests, against a `DisclosureRecord` (test_domain_invariants.py:648) and a hand-written `_Entry` stub with a `contributing_scopes` class attribute (line 656) — neither of which the daemon ever writes.

This is a check that looks like enforcement at the exact boundary AD-38 names, and enforces nothing there. If a personal-scope `NormalizedEvent` were persisted to a project scope, nothing stops it.

**CONFIRMED.**

**Fix:** either give the envelope a `contributing_scopes` (or make `assert_writable` fall back to `record.scope`), or stop calling it on the event path so its absence is visible.

---

## D1-7 [MEDIUM] The Clocks convention is violated by the storage service, and nothing checks it

**Spine, Consistency Conventions (line 383):**

> Clocks | **No component reads the ambient clock.** `now` is injected by the composition root (AD-30) — AD-35's coverage windows are a fail-closed guard, and a guard that cannot be tested deterministically cannot be trusted

**Code:**

```
pm_ai/storage/service.py:55:        at = datetime.now(timezone.utc)     # append_event_log
pm_ai/storage/service.py:62:        at = datetime.now(timezone.utc)     # persist_events → ingested_at
pm_ai/connectors/gitlab.py:28:      now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
```

`pm_ai/app/wiring.py:37` builds the injectable clock —

```python
clock = now or (lambda: datetime.now(timezone.utc))
```

— and then, at `wiring.py:39`, constructs the storage service without it: `storage = StorageService(root)`. Only the connector receives it (line 45). So `ingested_at` — the clock AD-35:327 makes load-bearing for "cursors, watermarks, replay, sweep windows" — is minted from the ambient clock and cannot be controlled by a test. The convention's own justification is that this is the thing that must be deterministically testable.

The connector's `now` default at gitlab.py:28 is a second, smaller instance: the injection point exists but reads the ambient clock when the caller omits it, so a connector constructed outside `build()` silently loses determinism.

Enforcement: none. `test_ad35_the_two_clocks_are_not_interchangeable` skips on `pm_ai.domain.clocks`, and no AST rule scans for `datetime.now`.

**CONFIRMED.**

---

## D1-8 [MEDIUM] `evt_` ids are random hex, not ULIDs — and two other id conventions drift

**Spine, Consistency Conventions (line 374):**

> Identifiers | Prefixed ULIDs — `cmt_`, `prp_`, `evt_`, `job_`, `skl_`; **sortable by creation time**; never reused.

**Code, `pm_ai/storage/service.py:20-24`:**

```python
def _ulid() -> str:
    """Surrogate id, minted here and nowhere else (AD-34)."""
    import secrets
    return "evt_" + secrets.token_hex(10)
```

A function named `_ulid` that returns random hex. Not sortable by creation time. AD-35:329 makes `entry_id` half of the fold key — "entries fold by `(occurred_at, entry_id)`, a total order stable across rebuilds" — which still holds as a *total order*, but the spine's stated sortability property is simply absent, and any future code that assumes `evt_` ids sort chronologically will be wrong.

Two related drifts:

- `pm_ai/app/pipelines.py:57` mints `proposal_id=f"prp_{meeting.meeting_id}_{i}"` — not a ULID, and **deterministic**, so re-ingesting the same meeting reuses the same proposal ids and silently overwrites `_t2.proposals[...]` (`storage/service.py:100`). "never reused" is violated by construction.
- `pm_ai/core/jobs.py:26` returns `f"idem_{digest[:32]}"`. The `idem_` prefix is absent from the spine's list at line 374, and spine line 378 specifies `sha256(job_type + target_ref + canonical_payload)` without mentioning the 32-hex-char truncation.

**CONFIRMED.**

---

## D1-9 [MEDIUM] AD-33's enumerated `Meeting` fields do not match the entity

**Spine, AD-33 (line 308):**

> **`Meeting` is a first-class Tier-1 record**: id, calendar event reference, title, start, duration, attendees, **derived-transcript pointer, processing status**.

**Code, `pm_ai/domain/meetings.py:21-26`:**

```python
meeting_id: str
title: str
start: datetime
duration_minutes: int
attendees: tuple[Actor, ...]
calendar_event_ref: str | None = None
```

Six fields against eight named. The two missing ones are not incidental — AD-33:310 says "Tracing walks *fact → meeting → transcript if present*", and the transcript pointer is the second hop. Without it the trace cannot be walked from the entity the spine designates as the citation root.

**CONFIRMED.**

---

## D1-10 [MEDIUM] AD-32's `(provider, verb)` key is not derived from the entity being mutated

**Spine, AD-32 (line 296):**

> the verb is **auto-executable** — registered in `domain`'s verb registry, keyed on `(provider, verb)`

and (line 298) "The registry is keyed on the pair for exactly this reason" — `jira:set_priority` auto-executes, `gitlab:set_priority` stages.

**Code, `pm_ai/app/pipelines.py:33`:**

```python
def run_transcript_ingestion(daemon, transcript, meeting, *, provider: str = "gitlab") -> dict:
```

`provider` is a **caller-supplied default**, not read from the target. Two lines later the target is parsed independently:

```python
pipelines.py:41:    results = extract(transcript, meeting, pm_handle=daemon.pm_handle, provider=provider)
pipelines.py:46:    target = TargetRef.parse(ex.detail["target"])
```

`TargetRef` carries `system` (`domain/identity.py:166`) — the actual provider of the entity being mutated — and nothing asserts `provider == target.system`. A transcript saying `pm-ai, set_priority jira:alpha:issue:PAY-102` under the default is authorized as *gitlab*'s `set_priority`; the reverse pairing authorizes a notifying GitLab change under Jira's quiet verb.

Practical impact today is bounded: `SkillRegistry.invoke` keys on `f"{skill.system}.{skill.name}"` (`skills/registry.py:43`), so a mismatched pair raises `SkillNotAuthorized` rather than executing. That is luck from a second check, not the guard AD-32 describes. The authorization decision itself is made against a provider the target never confirmed.

**CONFIRMED** (latent; currently masked by the registry lookup).

---

## D1-11 [MEDIUM] AD-12's sanitization in the harvest pipeline is a discarded call

**Spine, AD-12 (line 166):**

> Every payload crossing an inbound adapter boundary ... passes the sanitization filter before it can reach any model context. The pipeline enforces this centrally.

**Code, `pm_ai/app/pipelines.py:24-26`:**

```python
# AD-12 — sanitization at the boundary, uniformly, outside the connector.
for event in result.events:
    sanitize(getattr(event.payload, "message", "") or "")
```

The return value is discarded. Nothing downstream consumes `for_model`; `persist_events` on the next line receives `result.events` untouched. The loop is a no-op decorated with an AD reference — it reads as enforcement and enforces nothing, and a reviewer scanning for AD-12 coverage will find it and move on.

(The transcript path does this correctly: `core/extraction.py:36` binds `clean = sanitize(u.text)` and carries both `clean.raw` and `clean.for_model` into the `Extraction`.)

`tests/architecture/README.md:83-84` lists AD-12 as "not mechanically enforced — the pipeline enforces it centrally". The pipeline does not.

**CONFIRMED.**

---

## D1-12 [MEDIUM] AD-37's CAS has no legality table, so `STAGED → EXECUTING` succeeds

**Spine, AD-37 (line 348):**

> the sweeper CASes `staged → expired`, the worker CASes `approved → executing`, and whichever loses observes the winner and stops.

**Spine, AD-13 (line 172):**

> No external mutation derived from implicit extraction may execute without an approved Proposal.

**Code, `pm_ai/domain/proposals.py:37-48`:**

```python
def transition(self, to: ProposalState, *, expected_version: int) -> Proposal:
    if expected_version != self.version:
        raise VersionConflict(...)
    if self.state.is_terminal:
        raise TerminalState(...)
    return replace(self, state=to, version=self.version + 1)
```

Two guards — version match and terminality — and no check on the `(from, to)` pair. Any non-terminal state transitions to any state, so `STAGED → EXECUTING` (skipping approval entirely) is a legal call, as is `EXECUTING → STAGED`. The spine names exactly two legal edges and the type enforces neither.

`ProposalState.EXECUTING` is deliberately non-terminal (`lifecycle.py:36-42`), which AD-13:172 justifies — "it is the CAS latch AD-37 uses" — and that is correct; the gap is the absent edge table, not the state set.

**CONFIRMED.**

---

## D1-13 [LOW] AD-13's Proposal field names drift from the spine's list

**Spine, AD-13 (line 172):** "a single `Proposal`: id, type, summary, payload, target executor, expiry, version, **status**"

**Code, `pm_ai/domain/proposals.py:22-31`:** `proposal_id, type, summary, payload, target, cites, created_at, **state**, version, ttl`

- `status` → `state` (and AD-14:178 phrases the disjointness rule as "never share a **status** field")
- "target executor" → `target: TargetRef` only; there is no executor callback field, though AD-13:172 says features "register a proposal type with a payload schema and an **executor callback**"
- "expiry" → `ttl` + a derived `expires_at` property (fine, but not the spine's word)
- `cites: SourceRef` is a good addition (AD-33) that the spine's field list omits

**CONFIRMED**, cosmetic individually, but AD-13 is the AD that exists so five features do not build five approval shapes; its field list is the contract.

---

## D1-14 [LOW] Naming conventions the code does not follow

**Spine (line 372):** "skills as `pm_ai/skills/<verb>_<object>.py`"
**Code:** `pm_ai/skills/gitlab.py`, containing `class PostComment` (line 11). By the convention the file is `post_comment.py`. Note the convention is service-agnostic while the code organizes by service — one of the two should change, and the code's grouping is arguably better, but the spine currently says otherwise.

**Spine (line 373):** "adapters named `<Service><Noun>Adapter`"
**Code:** `GraphTranscriptAdapter` ✓, `ManualTranscriptAdapter` ✓, but `GitLabConnector` (`connectors/gitlab.py:20`) and `StorageService` (`storage/service.py:37`) do not follow it.

**Spine (line 371), entity naming:** lists `CoachingCommitment`, `Commitment`, `ConnectorInstance`, `Skill`, `Job`, `Transcript`, `Verb`. Of these, `Transcript` and `Verb` exist; `Commitment`, `CoachingCommitment`, `ConnectorInstance`, `Skill`, and `Job` have no type anywhere in `pm_ai/` (grep confirms; only `CommitmentState` exists). Defensible for unbuilt paths — flagged so the table is not read as an inventory.

**CONFIRMED.**

---

## D1-15 [LOW] AD-18's "logs the violation" is not implemented

**Spine, AD-18 (line 204):** "the daemon refuses to invoke an unlisted skill or a call exceeding its declared permissions, **and logs the violation**."

**Code, `pm_ai/skills/registry.py:50` and `:61`** raise `SkillNotAuthorized` with a good message and log nothing. There is also no logging infrastructure yet (spine:385 designates `~/.pm-ai/logs/`), so this is a not-yet rather than a contradiction — but AD-18 is the security firewall AD and a silent refusal is a materially different property from a recorded one.

Separately, the permission check at `registry.py:60` is an **equality** test:

```python
if verb.permission is not skill.permission:
```

AD-18 says "a call **exceeding** its declared permissions". Equality happens to be stricter than exceedance here, so it fails closed — worth stating in the AD rather than leaving the reader to infer a partial order that the code does not have.

**CONFIRMED.**

---

## D1-16 [LOW] Dead code paths the spine's rules imply are live

- **`SourceRef.is_durable`** (`domain/identity.py:141-143`) can never return `False`: `parse` (line 121) already raises `NonDurableReferent` for every head in `_NON_DURABLE`, so any constructed instance is durable. `test_ad33_source_refs_never_point_at_a_transcript:406` asserts `.is_durable` on a parsed ref — an assertion that cannot fail. The real guard is the `parse` rejection on the next line, which is genuine.
- **`CommitmentState.ALTERED`** (`lifecycle.py:50`) is unreachable: `evaluate_commitment` (lines 148-166) returns only `FULFILLED`, `PENDING`, `UNKNOWN`, `BROKEN`. AD-14:178 lists `ALTERED` as a state of the machine with no rule producing it.
- **`SkillRegistry` uses `threading.Lock`** (`registry.py:13, 40, 67`) for AD-37's per-target serialization, while AD-19:210 mandates "One asyncio event loop owns all I/O — connector harvests, Telegram long-poll, the loopback API, **MCP calls**." A `threading.Lock` does not serialize coroutines on one loop; when the skill layer becomes async this lock either does nothing or deadlocks the loop. No concurrent test exercises it.

**CONFIRMED.**

---

# DIRECTION 2 — enforcement reality vs. claimed coverage

## D2-1 [CRITICAL] The AD-5 single-writer rule and the AD-1 shell rule are both bypassable — demonstrated

I injected two deliberate violations and ran the suite.

**Injection A** — a truncating write outside `pm_ai.storage`, appended to `pm_ai/core/sanitize.py`:

```python
def _probe(p):
    from pathlib import Path
    Path(p).open("w").write("clobbered")
```

**Injection B** — a shell escape in the composition root, appended to `pm_ai/app/pipelines.py`:

```python
def _probe_shell():
    import subprocess
    subprocess.run(["/bin/echo", "hi"], shell=True)
```

**Result:**

```
$ uv run pytest tests/architecture -q
40 passed, 31 skipped in 0.46s
$ uv run lint-imports
Contracts: 12 kept, 0 broken.
```

Both violations passed every check. Tree restored; `git status pm_ai tests` clean.

**Why A passes.** `tests/architecture/test_static_rules.py:58-65`:

```python
def _write_mode(node: ast.Call) -> bool:
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        return any(c in str(node.args[1].value) for c in "wax+")
    for kw in node.keywords:
        if kw.arg == "mode" and ...
    return False  # bare open(p) is a read
```

It expects the builtin form `open(path, "w")`, where the mode is `args[1]`. For `Path(p).open("w")` the mode is `args[0]`, so `_write_mode` returns `False` and line 78 `continue`s past it as a read. Every `Path`-style write in the codebase is therefore invisible to `test_ad5_single_writer_owns_all_file_writes`.

The irony is exact: the storage service itself writes via `self._segment(...).open("a", encoding="utf-8")` (`storage/service.py:56, 80`) — the one-positional-arg form. So `test_ad5_storage_never_rewrites_a_markdown_ledger_in_place` (line 218), the check written *specifically* because the write-scan exempts `storage`, also cannot see storage's writes. Changing that `"a"` to `"w"` would silently pass both AD-5 checks and destroy Tier 1 history on every append.

**Why B passes.** Two independent gaps line up:

- `test_ad1_no_shell_execution_outside_platform` scans `layers = ["core", "connectors", "skills", "surfaces", "storage"]` (`test_static_rules.py:94`) plus `models`. **`app` and `domain` are not scanned.**
- `.importlinter`'s `subprocess-confined` contract lists `source_modules = pm_ai.core, pm_ai.domain, pm_ai.surfaces, pm_ai.connectors, pm_ai.skills, pm_ai.storage, pm_ai.models.frontier`. **`pm_ai.app` is absent.**

So the composition root — the one module permitted to import everything, and the module that orchestrates the pipeline the model's output flows through — may shell out freely, with `shell=True`, unchecked by either mechanism. AD-1:71-73 states the class-L constraint as `pm_ai.models.local` **only**, and "The LLM core holds zero shell capability... **Its only route to an external effect is class M.** That is the security property."

Note also `test_ad5_single_writer_owns_all_file_writes:74` *does* include `"app"` in its layer list, so the omission of `app` from the shell scan reads as an oversight rather than a decision.

**CONFIRMED — empirically, both.**

**Fix:** `_write_mode` must handle `args[0]` when the call is an attribute-style `.open(...)`; add `app` and `domain` to `SHELL_ALLOWED` scanning and to the `subprocess-confined` contract.

---

## D2-2 [HIGH] The spine's Enforcement section names four capabilities whose tests never run

**Spine, line 632:**

> Behavioural tests | `tests/architecture/test_domain_invariants.py` | Semantics no static check can see — idempotency determinism, closed taxonomies, scope isolation, **routing**, **warn-only budget**, **rebuildability**

Measured against the run:

| Named capability | Test | Status |
|---|---|---|
| idempotency determinism | `test_ad20_idempotency_keys_are_deterministic` | **passes** (genuinely good — forks a subprocess) |
| closed taxonomies | `test_ad27_connectors_only_emit_core_declared_event_types` | **SKIPPED** (`pm_ai.core.taxonomy`) |
| " | `test_event_payloads_are_typed_per_event_type` | passes |
| **scope isolation** | `test_ad25_project_rendering_cannot_open_the_personal_store` | **SKIPPED** (`pm_ai.core.rendering`) |
| **routing** | `test_ad15_*` (6 tests) | **SKIPPED** (`pm_ai.models.router`) |
| **warn-only budget** | `test_ad17_budget_breach_warns_but_never_degrades` | **SKIPPED** (`pm_ai.models.router`) |
| **rebuildability** | `test_ad3_indexes_rebuild_from_markdown_without_loss` | **SKIPPED** (`pm_ai.storage.reindex`) |

Four of six named capabilities have zero executing checks. "Rebuildability" is the one worth singling out: AD-3's entire zero-loss guarantee rests on it, three AD-3 tests pass, and all three (`test_ad3_reindex_cannot_reach_tier_2`, `test_ad3_no_artifact_is_both_rebuilt_and_backed_up`, `test_ad3_every_artifact_has_exactly_one_tier`) assert only against the static `ARTIFACT_TIER` dict in `domain/storage_tiers.py:44-56`. They verify the *table is self-consistent*. Nothing rebuilds anything. A reader of the Enforcement section reasonably concludes rebuildability is checked; a reader of the green run reasonably concludes AD-3 is covered; neither is true of the property AD-3 is about.

Similarly line 630 credits `.importlinter` with AD-15, AD-16, AD-26, AD-7 — see D2-4.

**CONFIRMED.**

---

## D2-3 [HIGH] Eleven ADs are listed as enforced while every check they name skips

Cross-referencing `tests/architecture/README.md:37-72` against the skip list:

| AD | README says enforced by | Reality |
|---|---|---|
| AD-2 | `test_ad2_telegram_uses_outbound_polling_only` | SKIP — `pm_ai.surfaces.telegram.bridge` |
| AD-6 | `test_ad6_markdown_is_never_encrypted` | SKIP — `pm_ai.storage.crypto` |
| AD-8 | `test_ad8_loopback_api_rejects_unauthenticated_requests` | SKIP — `pm_ai.surfaces.api.app` |
| AD-13 | `test_ad13_features_cannot_implement_their_own_proposal_expiry` | SKIP — `pm_ai.core.proposals` |
| AD-15 | `model-clients-confined`, `test_ad15_*` | 6 SKIP; contract vacuous (D2-4) |
| AD-17 | `test_ad17_budget_breach_warns_but_never_degrades` | SKIP |
| AD-21 | `test_ad21_slow_requests_acknowledge_instead_of_blocking` | SKIP — `pm_ai.core.dispatch` |
| AD-22 | `test_ad22_retrieval_path_never_touches_a_model` | SKIP — `pm_ai.core.retrieval` |
| AD-25 | `test_ad25_project_rendering_cannot_open_the_personal_store` | SKIP — `pm_ai.core.rendering` |
| AD-28 | `test_ad28_project_ledger_rejects_personal_commitments` | SKIP — `pm_ai.core.commitments` |
| AD-31 | `test_ad31_*` (2) | both SKIP — `pm_ai.models.router` |

Eleven ADs with a populated "Enforced by" cell and nothing running. The README does have a "Not mechanically enforced" section (lines 74-87) listing AD-4, AD-10, AD-12, AD-18, AD-19 — so it establishes the convention that an unenforced AD is named as such, and then does not apply it to these eleven.

Three further ADs are partially claimed:

- **AD-35** — all three README-named tests skip (`domain.clocks`, `core.ledger`, `core.commitments`). Only the coverage half is live, via two tests the README does not list (`test_coverage_gap_resolves_to_unknown_not_broken:616`, `test_coverage_gap_does_not_fire_an_irreversible_nudge` in the slice). **The two-clocks rule and the deterministic-fold rule have neither implementation nor a running check** — and spine:653 claims AD-35 proven by the slices. It is one-third proven.
- **AD-37** — both README-named tests skip; the property *is* covered by `test_concurrent_approval_from_two_surfaces_yields_one_winner` and `test_expired_proposal_cannot_execute` in `tests/slice/test_transcript_slice.py:173,185`. Right answer, wrong map.
- **AD-23** — both README-named tests skip; covered instead by `test_manual_transcript_without_a_meeting_is_rejected` and `test_manual_adapter_needs_no_network` (test_transcript_slice.py:56,63).

**CONFIRMED.**

---

## D2-4 [MEDIUM] Five import contracts are green over empty modules

`lint-imports` reports 12/12 kept over "50 files, 117 dependencies". Five of those contracts constrain modules that contain nothing but docstrings:

- `pm_ai/models/__init__.py` (5 lines), `models/frontier/__init__.py` (1 line), `models/local/__init__.py` (1 line) → **AD-15 `model-clients-confined`** and **AD-16 `no-builtin-tool-agent`** cannot break.
- `pm_ai/surfaces/__init__.py` (5), `surfaces/api/__init__.py` (1), `surfaces/cli/__init__.py` (1), `surfaces/telegram/__init__.py` (1) → **AD-7 `cli-owns-no-scheduling`** and **AD-30 `surfaces-through-core`** cannot break.
- `pm_ai/platform/__init__.py` (5) → **AD-26 `os-behind-platform`** cannot break.

Spine line 630 lists AD-1, AD-5, AD-7, AD-15, AD-16, AD-26 as what the import contracts catch. Today they catch AD-1 and AD-5 (over real code) and four vacuities. The README itself warns about exactly this at line 105-107 — "AST checks pass **vacuously** against an empty package... treat a stubbed module as a skip in disguise" — and then the spine's Enforcement table credits them anyway.

The contracts are correctly written and will bite the moment code lands. The finding is about the coverage claim, not the contracts.

**CONFIRMED.**

---

## D2-5 [MEDIUM] Several skips target module paths the implementation deliberately did not use — they will never clear on their own

`mod()` (`test_domain_invariants.py:27-32`) skips on `ModuleNotFoundError`. Four tests skip because they import a path the built code chose differently, not because the invariant is unimplemented:

| Test | Imports | Where the code actually put it |
|---|---|---|
| `test_ad9_cursor_is_opaque_to_the_core:140` | `pm_ai.core.scheduler` | `Cursor` is at `pm_ai/domain/harvest.py:21` |
| `test_ad27_connectors_only_emit_core_declared_event_types:95` | `pm_ai.core.taxonomy` | `NormalizedEventType` is at `pm_ai/domain/events.py:20` — **which is where AD-27:258 says it must be** ("closed enumerations defined in `domain`") |
| `test_ad23_transcript_pipeline_works_without_a_live_tenant:301`, `test_ad23_transcript_without_a_meeting_is_rejected:428` | `pm_ai.core.transcripts` | `pm_ai/domain/transcripts.py` |
| `test_ad14_proposal_and_commitment_lifecycles_stay_distinct:159-160` | `commitments.CommitmentStatus`, `proposals.ProposalStatus` | `CommitmentState` / `ProposalState` at `lifecycle.py:45, 24` |

`test_ad27_...` is the pointed one: the test looks for the taxonomy in `core`, the AD says `domain`, the code obeys the AD, and the test skips forever as a result. `test_ad14_...` is worse than a skip — when `pm_ai.core.commitments` eventually lands it will raise `AttributeError` on `CommitmentStatus`, because the implementation named it `CommitmentState`. It is a red test waiting six months to go off for the wrong reason.

Meanwhile AD-14 **is** genuinely enforced, by a mechanism the README does not mention — the import-time assertion at `pm_ai/domain/lifecycle.py:62-63`:

```python
_overlap = {s.value for s in ProposalState} & {s.value for s in CommitmentState}
assert not _overlap, f"AD-14: lifecycle states overlap: {sorted(_overlap)}"
```

which matches AD-14:178's "their member names are disjoint, **asserted at import**" precisely. The map is wrong in both directions here: it credits a skipping test and omits the working guard.

**CONFIRMED.**

---

## D2-6 [MEDIUM] Two AST rules are narrower than they read

**`test_ad11_no_filesystem_discovery_of_projects:149-150`:**

```python
if "registry" in f.path.name:
    continue  # the registry legitimately reads its own file
```

Exempts any file whose *name* contains "registry" — which today silently exempts `pm_ai/skills/registry.py`, unrelated to the project registry. The exemption should be path-anchored (`connectors/registry.py`, or a module-level marker), not a substring of a basename.

**`test_ad24_event_log_is_not_a_debug_sink:169-178`** requires the string `event_log` and a logging level to appear in the **same unparsed call node**. `logger.debug(f"event_log: {x}")` is caught; `path = event_log_segment(); logger.debug(path)` is not. That limit is inherent to AST-level checking and is fine — but the check's docstring (line 165) and failure message (line 182) both still say `event_log.md`, the single-file model AD-24 was revised away from ("**The ledger is a directory of dated segments** (`event_log/`), not a single file", spine:240). Same stale wording at `domain/disclosure.py:3,5` and `domain/identity.py:60`.

**CONFIRMED.**

---

## D2-7 [MEDIUM] `tests/architecture/README.md` still says the package does not exist

**README lines 18-25:**

> ## Status: skipping by design
> `pm_ai/` does not exist yet, so these skip.

**Reality:** `pm_ai/` contains 37 modules and two vertical slices. Same stale claim at `tests/architecture/conftest.py:4-5`, and the skip message at `conftest.py:52` ("pm_ai/ does not exist yet") is now unreachable — `_require_package` never fires, because `PACKAGE_ROOT.is_dir()` is true.

Consequence: a reader auditing the 31 skips is told by the README that they are all "the package doesn't exist yet." They are not — the package exists, and each skip now marks a specific unbuilt module or, per D2-5, a wrong import path. The document that the spine points to at line 636 as the "Full AD→check mapping" opens by mis-describing its own status.

Related: spine line 642-644 and README line 24 both state **"Phase 1 exit criterion: zero skips."** There are 31, while the spine simultaneously reports two vertical slices built. Whether Phase 1 is complete is a question the two documents currently answer differently.

**CONFIRMED.**

---

## D2-8 [LOW] Tests that assert less than they appear to

Not vacuous in the empty-module sense, but weaker than their names and docstrings promise:

- **`test_pm_ai_does_not_verify_its_own_write`** (test_vertical_slice.py:161) — "exercised end to end". Covered fully in D1-5: it reduces to `Provenance.PM_AI.admissible_as_evidence is False`, already asserted at line 599. The harvest-back hop is never taken.
- **`test_ad3_*`** (test_domain_invariants.py:672-705) — three passing tests that assert the `ARTIFACT_TIER` dict is internally consistent. Real value (they would catch an artifact re-tiered into both sets), but they touch no storage and prove nothing about rebuild behaviour.
- **`test_ad36_every_class_m_mutation_is_recorded_for_attribution`** (line 528) — proves the write side of AD-36's two mechanisms. The AD's own sentence is "Two mechanisms **because one of them will have gaps**"; the second mechanism is untested because it is unwritten.
- **`test_ad33_source_refs_never_point_at_a_transcript:406`** — its first assertion (`.is_durable`) cannot fail (D1-16). Its second (the `NonDurableReferent` raises) is genuine and good.
- **`test_ad9_connectors_own_no_scheduling`** — passes over one connector that contains no async, threading, or timer code of any kind. Correct, and currently free.

**CONFIRMED.**

---

# What the reconciliation got right

Recording this so the corrections above are read in proportion.

- **AD-34 is fully realized in types.** The grammar at `domain/identity.py:88`, the scopeless global form, `TargetRef`'s sub-resource rejection (`identity.py:173-177`), and `resolve_actor` returning `UNRESOLVED` rather than the raw handle (`identity.py:234-244`) — all match the AD text exactly, and the tests exercise the failure directions.
- **AD-27's typed-payload half is real.** `PAYLOAD_FOR` + `__post_init__` (`domain/events.py:110-153`) makes a shape mismatch a construction error, which is precisely the "closed type over an open payload is half a contract" fix the AD describes.
- **AD-20's determinism test is the best test in the suite.** `test_ad20_idempotency_keys_are_deterministic:55-69` forks a subprocess and compares — the only boundary at which a `time.time()` or PID seed shows itself. The README is right that this is the one to keep.
- **AD-32 is well covered on both sides.** Eight parametrized cases (line 359-370) plus five slice tests, including the `gitlab:set_priority` vs `jira:set_priority` pair and the unregistered-verb fail-closed path.
- **AD-3's physical tier separation** is enforced by construction — `assert_reindex_safe` (`storage_tiers.py:66`) checks the artifact set rather than intent, and the module-level assertion at line 82 makes rebuild∩backup a permanent import-time invariant.
- **AD-38's `DisclosureRecord` has no `scope` field** by deliberate design (`disclosure.py:38-39`), which removes the routing decision that caused the leak rather than guarding it. That is the right shape of fix.
- **AD-30 holds cleanly.** The `ports-depend-only-on-domain` and `domain-imports-nothing` contracts are real and green over real code, and the pipeline genuinely lives in `app` because it has nowhere else to be.

---

# Recommended actions, ordered

1. **Fix `_write_mode` and add `app` to the shell scan** (D2-1). Two small edits close a demonstrated hole in the two rules the spine leans on hardest. Add a regression test that injects the `Path(p).open("w")` form.
2. **Correct the spine's Enforcement section** (D2-2): remove routing, warn-only budget, scope isolation, and rebuildability from the "catches" column, or mark them pending.
3. **Mark the eleven skip-only ADs in `tests/architecture/README.md`** (D2-3) and move them into the existing "Not mechanically enforced" section until their modules land.
4. **Repoint the four mis-targeted tests** (D2-5) at `pm_ai.domain.*` and rename `CommitmentStatus`/`ProposalStatus` → `CommitmentState`/`ProposalState`; add the `lifecycle.py:62` import assert to the AD-14 row.
5. **Narrow spine line 653.** AD-20 is proven for determinism, not durability (D1-4); AD-35 for coverage, not for the two clocks or the fold (D2-3); AD-36 for the write side, not the read-back (D1-5).
6. **Decide AD-9's method count** and fix `pm_ai/connectors/__init__.py:3`, whose stale `-> list[NormalizedEvent]` signature drops the `CoverageWindow` that AD-35 depends on (D1-1).
7. **Attribute harvested events honestly** — remove the unconditional `Provenance.EXTERNAL` at `connectors/gitlab.py:51` and add the normalization step that reads `executed_mutations()` (D1-5). Until then AD-36's stated guarantee does not hold in the one connector that exists.
8. **Inject the clock into `StorageService`** (D1-7) and refresh the stale `pm_ai/` -does-not-exist wording in the README and conftest (D2-7).

---

## Findings index

| # | Sev | Finding | Anchor |
|---|---|---|---|
| D2-1 | CRITICAL | AD-5 write rule and AD-1 shell rule both bypassable — demonstrated | test_static_rules.py:58-65, :94; .importlinter subprocess-confined |
| D1-4 | HIGH | Tier 2 is in-memory dicts; AD-20 durability unproven | storage/service.py:27-34 |
| D1-5 | HIGH | AD-36 normalization absent; connector hard-codes `EXTERNAL` | connectors/gitlab.py:51 |
| D1-3 | HIGH | AD-27's event_log entry enumeration absent; two writers already disagree | storage/service.py:73, skills/registry.py:78 |
| D1-1 | HIGH | AD-9 "exactly one method" false; stale connector docstring | ports/__init__.py:22-26 |
| D1-2 | HIGH | `TranscriptSourcePort` absent; adapters have divergent signatures | graph.py:16, manual.py:19 |
| D2-2 | HIGH | Enforcement section names 4 capabilities with no running checks | spine:632 |
| D2-3 | HIGH | 11 ADs claimed enforced with only skipping tests | README:37-72 |
| D1-6 | MED | AD-38 write-path guard inert for every real input | disclosure.py:77 |
| D1-7 | MED | Clocks convention violated by storage; unchecked | storage/service.py:55,62 |
| D1-8 | MED | `evt_` ids random not ULID; `prp_` ids collide on re-ingest | storage/service.py:24, pipelines.py:57 |
| D1-9 | MED | `Meeting` missing transcript pointer + processing status | domain/meetings.py:21-26 |
| D1-10 | MED | AD-32 provider decoupled from target's system | app/pipelines.py:33,46 |
| D1-11 | MED | AD-12 sanitization in harvest pipeline is discarded | app/pipelines.py:25-26 |
| D1-12 | MED | AD-37 CAS permits `STAGED → EXECUTING` | domain/proposals.py:37-48 |
| D2-4 | MED | 5 import contracts green over docstring-only modules | .importlinter |
| D2-5 | MED | 4 skips target module paths the code deliberately didn't use | test_domain_invariants.py:95,140,159,301 |
| D2-6 | MED | AD-11 exemption over-broad; AD-24 wording stale | test_static_rules.py:149,165 |
| D2-7 | MED | README/conftest still say `pm_ai/` does not exist | README:18-25, conftest.py:4-5 |
| D1-13 | LOW | Proposal field names drift (`status`→`state`, no executor) | domain/proposals.py:22-31 |
| D1-14 | LOW | Skill file naming, adapter naming, 5 named entities absent | skills/gitlab.py |
| D1-15 | LOW | AD-18 "logs the violation" not implemented | skills/registry.py:50,61 |
| D1-16 | LOW | `is_durable` unreachable-false; `ALTERED` unreachable; threading.Lock vs AD-19 | identity.py:141, lifecycle.py:50, registry.py:67 |
| D2-8 | LOW | Tests asserting less than their names promise | test_vertical_slice.py:161 |
