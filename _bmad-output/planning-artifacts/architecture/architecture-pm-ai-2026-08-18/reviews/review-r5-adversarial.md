# Adversarial Divergence Review — ARCHITECTURE-SPINE.md (r5)

**Reviewer:** adversarial architecture reviewer (gate lens: *construct two compliant units that build incompatibly*)
**Target:** `ARCHITECTURE-SPINE.md` — 44 ADs, `updated: 2026-08-22`, `status: final`
**Also attacked:** `pm_ai/` (4,738 LOC), `.importlinter`, `tests/` (226 passed, 31 skipped)
**Date:** 2026-08-22

---

## Verdict

**The spine does not yet close. 17 divergence pairs; 11 verified reachable against the running code, 6 constructed against unbuilt paths.**

AD-43 and AD-44 are the two strongest ADs in the document — each replaces a guess with an
authority, and each is enforced by types rather than prose. Neither is where the trouble is.
The trouble is at their **seams**, and the seams have one shape in common:

> **AD-44 declares that artifact identity is `(scope, relative path)` — and then every table
> derived from it, every guard keyed on it, and every set asserted over it is keyed by
> *basename* or by *scope kind*, not by that pair.**

Six of the pairs below are downstream of that single unreconciled edge (F1, F2, F3, F4, F10, F11).
A second cluster is **"the AD names a verdict but not its operands"** — AD-36's provenance for a
scopeless referent (F5), AD-39's replacement for a withheld `UNKNOWN` (F6), AD-43's
permanent-vs-transient refusal (F15), AD-36.2's namespace for the word `scope` (F7).

**Nine of the seventeen fail in the direction that looks like success**: a transcript in a
repository, a backup missing its WAL, a report's dossier addressed under a scope label whose
privacy rules do not apply, pm-ai's own comment admitted as evidence that pm-ai's own promise was
kept. None crashes. None is caught by the suite. Two of them (F2, F1) are *reachable today* with a
single line added to a scope tree.

**One structural defect must be fixed before anything else is read:** AD-44 was inserted into the
middle of AD-42's numbered rule list. Lines 533–537 — AD-42's items 4b, 5 and 6 — render *inside*
AD-44's Rule block. The clause the Deferred section cites as `AD-42.6` currently reads as
`AD-44.6`, and it contradicts the AD-1 amendment made in the same revision (F16).

---

## Method

Each pair names two units one level down that **each obey every AD to the letter** and still
cannot be built into one system. Where a pair is reachable against the code today, the evidence
is a command and its output, not a reading. Where the path is unbuilt, the pair is marked
**PROSPECTIVE** and the compliant reasoning of each unit is quoted from the AD that licenses it.

---

## F1 — `people/` has two addresses and two privacy labels for one directory `[HIGH · VERIFIED]`

**Unit A — `PeopleDossierWriter`** (FR-30/FR-31, UJ-4). Writes a report's career dossier with
`DataScope(ScopeKind.PEOPLE, person_id="alice")`. Compliant with AD-4: "`people` is a fourth scope
*kind* … a distinct kind and not merely a directory".

**Unit B — `TeamMetricsWriter`** (FR-30's per-employee monitored metrics). AD-4 also says people is
"stored as a sub-scope of the application scope", and AD-44 declares `people/` as a `Collection`
node **inside `APPLICATION_TREE`** carrying `Tier.TRUTH`. So Unit B addresses it the way AD-44 says
artifacts are addressed — `resolve(DataScope(APPLICATION), "people/")` — and writes there.

Both are compliant. They write the same directory:

```
resolve(APPLICATION, 'people/')            -> /Users/x/.pm-ai/private/people
scope_root(PEOPLE, person_id='alice').parent -> /Users/x/.pm-ai/private/people   # same dir: True
```

**The incompatibility:** every rule that protects a direct report is keyed on the *label*, not the
path — by AD-4's own design ("A scope kind is never inferred from a path"). `DataScope(APPLICATION)`
has `is_people == False` and `is_personal == False`. So a record Unit B writes into `people/`:

- passes `assert_writable` into a **git-committed** scope (verified: an application-scoped record
  cites application scope, and AD-38's guard returns without raising);
- is invisible to AD-31's HR-egress rule, which turns on `is_people`;
- is invisible to AD-38's "no record in a committed scope may reference people-scope material".

AD-4 built `people` as a kind *precisely because* "two rules turn on telling it apart and neither
can be written against a path". AD-44 then gave the same bytes a second, weaker identity in another
scope's tree, and nothing refuses it.

**Close it:** AD-44 must state that **a scope root is never a node in another scope's tree**. The
`people/` root is anchored by `ScopePaths.people_root` (it already is); the tree entry must go, or
`resolve` must refuse `people/` under `APPLICATION`.

---

## F2 — Two per-scope tier decisions are mutually exclusive, and the domain package refuses to import `[HIGH · VERIFIED]`

AD-44's title says identity is `(scope, relative path)` and its Prevents cites `daily_dashboard.md`
existing in both `memory/` directories as the reason: "A layout keyed by name cannot represent
either, so this is a correctness ceiling."

**Unit A — `ProjectDashboardRenderer`** (FR-32). The project dashboard is a *rendering* of
`commitments_log.md` plus `event_log/` — nothing in it originates there. AD-3's Tier-3 test is
explicit and narrow: "rebuildable from Tier 1 with zero loss". It passes. Unit A declares
`File("daily_dashboard.md", Tier.DERIVED)` in `PROJECT_TREE` and adds it to `pm-ai reindex`.

**Unit B — `PersonalBriefingRenderer`** (FR-09). The personal dashboard carries frontier-synthesised
prose and the Strategic Rationale Snippets of AD-41.4. A frontier call is not reproducible, and
AD-42.4b forbids the memory loop rewriting the record — so it fails Tier 3's test. Unit B keeps
`File("daily_dashboard.md", Tier.TRUTH)` in `PERSONAL_TREE`.

Both readings are exactly AD-3's stated test, applied to two different files that happen to share a
spelling — the case AD-44 says it exists to represent. Result:

```
MalformedLayout: one artifact key, two durability promises:
  ['daily_dashboard.md is TRUTH elsewhere and DERIVED in the project scope'].
```

`import pm_ai.domain` fails. Neither unit can ship; the build stops.

**The hole:** AD-44 never states the constraint the derivation actually imposes. `ARTIFACT_TIER`,
`BACKUP_TARGETS`, `REBUILD_TARGETS`, `RETENTION_MANAGED` and `DIAGNOSTIC_ONLY` are all keyed by
*basename* (verified: 25 keys, all bare names except where a scope makes one ambiguous), so the
real rule is **"one basename, one durability, globally"** — the opposite of what the AD's title
promises. The code says so in a docstring; the spine does not say it at all.

**Close it:** either AD-44 states the global basename→durability constraint as a first-class rule
(and AD-3 explains why a durability is a property of a *kind* of file rather than of an artifact),
or the derived tables are keyed by `(scope, relative path)` as the AD's own title claims.

---

## F3 — A retention-managed subtree lives inside a backup target `[HIGH · VERIFIED]`

AD-3: raw captures "are never a backup target and nothing may depend on them surviving", stated
unconditionally and repeated in Deployment ("in any scope"). AD-44 derives `BACKUP_TARGETS` from
the trees. `people/` is `Tier.TRUTH`, therefore a backup target; `people/<id>/transcripts/` is
`RETENTION_MANAGED`.

```
resolve(APPLICATION, 'people/')          -> /Users/x/.pm-ai/private/people
resolve(PEOPLE:alice, 'transcripts/')    -> /Users/x/.pm-ai/private/people/alice/transcripts
contained: True
```

**Unit A — `BackupDriver` (directory-granular).** Iterates `BACKUP_TARGETS`, resolves each in each
scope that declares it, copies the resolved path. `people/` is one of them and it is a directory, so
Unit A copies it whole — every report's verbatim 1:1 recordings into the backup.

**Unit B — `BackupDriver` (leaf-granular).** Iterates only `File` and non-nesting `Collection`
leaves, so it skips `people/` in favour of the per-person tree and excludes `transcripts/`.

Both obey AD-3 and AD-44 to the letter. The pairwise-disjointness assertion in `scope_model` is over
**keys**, and these keys *are* disjoint; the paths are nested. The module already contains the
mirrored assertion for the other direction ("no Tier-1 path lies inside a Tier-3 one") — the
containment class of bug is known and this instance of it is unguarded.

This also defeats AD-4's compensating obligation. AD-4 accepts filing `people` under the application
scope *because* "`people/` is a single deletable directory: leaving a role is one removal, not an
audit." Under Unit A the removal is one directory and N backups.

**Close it:** AD-3's disjointness must be asserted over **resolved paths under containment**, not
over keys — no `RETENTION_MANAGED` or `DIAGNOSTIC_ONLY` path inside a `BACKUP_TARGETS` path, and
vice versa. And AD-4 must say what "deleted on role change" means for a backup already taken.

---

## F4 — AD-43's authority is gated on a scope *kind*, but the spine sanctions a second repository `[HIGH · VERIFIED]`

AD-43's whole thesis is that only git can answer whether git tracks a path, and that a text check
gets two of three real cases wrong "in the direction that publishes a transcript". The
implementation asks that question only when `scope.is_git_committed`, which is `True` for `PROJECT`
and nothing else — and `GITIGNORE_REQUIRED` maps `transcripts/` to a project-shaped rule
(`/.project-ai/transcripts/`), asserted project-only by `test_gitignore_rules_cover_the_paths_the_resolver_returns`.

**Unit A — `CaptureWriter`.** For a personal or people capture it does not consult git at all,
citing the reasoning in `_assert_git_excludes`: "A capture in the personal or team-member scope is
excluded by *where it is*; there is no repository."

**Unit B — `PersonalScopeBackup`.** Follows the spine's own Deployment instruction verbatim: "If the
personal scope is kept as a private git repository, `private/` must be gitignored there." It runs
`git init` in `~/.manager-ai/` and gitignores `private/`.

`PERSONAL_TREE` places `Collection("transcripts", RETAINED)` **at the scope root**, not inside
`private/`. `private/` holds `telegram_cache/` and `personal_analytics.db` only. So Unit B commits
`~/.manager-ai/transcripts/` — verbatim recordings of the PM's own sessions, and of any meeting the
scope owns — into a git repository, and AD-43's guard is never armed because `is_git_committed` is a
property of the *kind* while "is inside a repository" is a property of the *machine*.

The failure is exactly AD-43's third table row (a directory committed before any rule existed), in
the one scope AD-43 does not ask about.

**Close it:** AD-43 must be scope-kind-agnostic — every capture write asks git about its own path,
and "not a repository" is one of the answers (a pass), not a reason to skip the question. Note that
this is what AD-43's own Rule already says ("Any inability to consult it (**not a repository**, …)
refuses the write") — the implementation reads that as *don't ask*, the AD reads it as *ask, and
treat this answer as a refusal*. Those are opposite. Pick one in the AD.

---

## F5 — A scopeless referent bypasses AD-36's fail-closed default `[HIGH · VERIFIED]`

Three ADs meet here and none of them covers the intersection:

- AD-33: "meeting-derived facts cite `meeting:<id>` plus speaker" — mandatory, for every fact.
- AD-34.1: `meeting:` and `goal:` take the **scopeless** two-part form.
- AD-36.2: the provenance join key is "`SourceRef.native_id == external_id` within the same
  `(system, scope)`", against a ledger "keyed by `TargetRef`". And `TargetRef.parse` **refuses a
  scopeless ref** ("not a mutable external entity").

So a meeting-referent event is **unjoinable by construction**, and AD-36 does not say what
provenance an unjoinable event carries.

**Unit A — `Verifier` (fail-closed reading).** AD-36: "`unknown` is a required third value…
defaulting to `external` fails *open* — an event nobody could attribute would count as proof a
promise was kept." An event the ledger cannot be consulted about is exactly that. Unit A resolves
every scopeless referent to `UNKNOWN`, and no meeting-derived fact is ever evidence.

**Unit B — `Normalizer` (the code today, `pm_ai/core/normalize.py:70`).**

```python
if ref.scope is None:
    return Provenance.EXTERNAL  # global entities (meetings) are never our writes
```

Defensible: a meeting is not an external system pm-ai can write to. But `EXTERNAL` is the one value
AD-36 calls admissible evidence, and it is now reachable **without the ledger ever being read** for
the entire class of referents AD-33 makes mandatory for meeting-derived facts. `Extraction.cites`
is a `meeting:` ref (`extraction.py:49,62`); `Meeting.source_ref` mints it (`meetings.py:39`).

Under Unit B, FR-34 can mark a commitment `FULFILLED` on a meeting-derived event — and the
extraction that produced that event is pm-ai's own act. That is the closed loop AD-36 exists to
open, arriving through the citation rule instead of through the connector. The Open Risks record
this defect as *fixed*; it is fixed for scoped refs only.

**Close it:** AD-36 must state the provenance of a global-entity referent explicitly, and AD-34 must
say which `DataScope` a `meeting:`/`goal:` ref resolves to (Meeting has a required `scope` field —
Goal has nothing). "The ledger cannot be consulted" and "we know it is not ours" are two different
claims and the code currently spells the first as the second.

---

## F6 — AD-35 and AD-39 give opposite instructions for the same silence `[HIGH · PROSPECTIVE]`

- AD-35: "the commitment sweeper **must not declare `BROKEN`** across a window it has no coverage
  for… this must fail closed." Verdict: `UNKNOWN`.
- AD-39: "**Absence of telemetry from an unhealthy instance is never reported as a coverage gap**,
  and never contributes an `UNKNOWN` that looks like patience."

**Unit A — `CommitmentSweeper` (AD-35 reading).** An unhealthy instance still produced no coverage,
so the window is uncovered and the verdict is `UNKNOWN`. FR-26's irreversible nudge does not fire.
Unit A is literally compliant with AD-35 and violates AD-39's second clause: it *did* contribute an
`UNKNOWN` that looks like patience.

**Unit B — `CommitmentSweeper` (AD-39 reading).** The silence is explained by ill health, so it is
not a coverage gap and does not license `UNKNOWN`. With the coverage guard removed for that
instance, the commitment is evaluated on the evidence in hand — there is none — and it goes
`BROKEN`. FR-26 fires an irreversible "why isn't this done" message about work already delivered.
Unit B is literally compliant with AD-39 and violates AD-35.

AD-39 removes AD-35's guard without naming the verdict that replaces it. Both units are defensible,
and one of them sends the message the entire clock/coverage apparatus was built to prevent.

**Close it:** AD-39 must name the third outcome — an unhealthy instance's window yields neither
`BROKEN` nor a patient `UNKNOWN` but a **surfaced health failure** that suppresses the nudge and
raises the connector, with AD-14's state machine saying which member holds it. As written, AD-14's
`UNKNOWN` is the only non-terminal state available, so AD-39's prohibition has no target.

---

## F7 — `scope` in the reference grammar is a different namespace from `DataScope`, and AD-36's join does not say which `[HIGH · VERIFIED]`

The Consistency Conventions row is unambiguous: "**No bare `Scope`** — the word meant four things
and now names none of them". AD-34.1 then defines `source_ref` as
`<system>:<scope>:<kind>:<native_id>`, where that middle segment is the *provider's* project slug —
and `SourceRef` carries it in a field literally named `scope`. AD-34.3's natural key
`(scope, source_system, source_ref)` uses the *`DataScope`*. So one event carries two different
things spelled `scope`:

```python
# events.py:164   — dedup uses the DataScope
return (str(self.scope), self.source_ref.system, str(self.source_ref))
# normalize.py:75 — the AD-36 join uses the ref segment
if (ref.system, ref.scope, ref.native_id) in artifacts:
```

**Unit A — `GitLabConnector`.** AD-34 requires refs "joinable, uniform across sources", so the slug
must be the provider's identifier for the project: `gitlab:<gitlab_project_path>:commit:<sha>`.

**Unit B — `PostComment` skill.** It builds its `TargetRef` from the proposal payload, and the only
project identity the core holds is `DataScope.project_id` — pm-ai's registry id. So it records
`gitlab:<registry_id>:issue:<n>`.

Both obey AD-34 exactly. If the two identifiers differ by one character, `own_artifact_index`'s
`(system, scope, external_id)` triple never matches, every pm-ai mutation attributes as `EXTERNAL`,
and FR-34 counts pm-ai's own comment as proof pm-ai's own promise was kept. Green suite throughout.

This is not hypothetical: the grammar's slug is `[A-Za-z0-9_.-]+` — **no slash** — so a real GitLab
project path (`acme/alpha`) *cannot be spelled*, forcing every connector author to invent a
single-token stand-in. Today `wiring.py:93` passes the registry id as the connector's `project`, so
the two namespaces coincide by wiring accident and the vertical slice proves nothing about their
agreement. AD-36.2 claims it states the join key "rather than [leaving] the mapping to each
implementer" — it states the *shape* and leaves the *namespace* open, which is the same defect one
level down.

**Close it:** name the middle segment something that is not `scope` (`realm`, `provider_project`),
state which namespace fills it, and state how it maps to `DataScope.project_id`. Then AD-36.2's
`(system, scope)` has one meaning.

---

## F8 — `goal:` is scopeless while goals live in two scopes, and AD-38's citation guard cannot see it `[HIGH · PROSPECTIVE]`

AD-33 records that filing `meetings/` in the wrong scope "made AD-38 false on the main path", and the
fix was to make `Meeting.scope` **required rather than defaulted**, then to check both directions.
`Goal` has had no such treatment, and the same shape is now in place:

- AD-41.5: `strategic_goals.md` is **personal** scope.
- AD-28: `CareerGoal` is **people** scope.
- AD-34.1: both are cited as `goal:<id>` — the **scopeless** form, in a closed set.
- AD-38: no committed record may reference personal- or people-scope material "not by content, **not
  by `source_ref`**, not by scope name."

**Unit A — `CommitmentWriter`.** AD-34 calls scopeless forms "**global entities** that belong to no
project". A ref that belongs to no scope cannot violate a cross-scope rule, so Unit A writes an
aligned commitment citing `goal:goal_01HX` into `.project-ai/memory/commitments_log.md`.

**Unit B — `DriftAuditor`.** Resolves the id, finds a `CareerGoal` in the people scope, and reports
an AD-38 violation: a direct report's performance objective is now in a repository their peers can
read.

`assert_citation_legal(cited=…, into=…)` requires the caller to *already hold* the owning scope. For
Meeting that works — the scope is a required field on the record and `pipelines.py:47` passes it
before extraction. For Goal there is no equivalent, and no AD says a `goal:` ref must be resolved
before it is cited.

**Close it:** AD-41.2 must give `Goal` a required `scope` field exactly as AD-33 gave `Meeting` one,
and AD-38's guard must run on `goal:` citations. Alternatively `goal:` leaves AD-34's scopeless set —
a goal that lives in a scope is not a global entity.

---

## F9 — The `ConnectorInstance` cursor has two homes and two tiers `[MED-HIGH · PROSPECTIVE]`

- AD-10: "A connector instance is the tuple `(scope, connector_type, config, cursor)`."
- AD-44/`APPLICATION_TREE`: `Collection("connectors", Tier.TRUTH)` — "One file per connector
  instance, named after the instance."
- AD-3: "connector cursors" are **Tier 2**.

**Unit A — `ConnectorRegistry`.** Persists the AD-10 tuple as one record per instance in
`connectors/<instance>.toml`, because AD-10 says the cursor *is part of the instance* and AD-44 says
that directory holds one file per instance. Tier 1: plaintext, hand-editable, a backup target.

**Unit B — `StorageService.save_cursor`** (the code today). Cursor in `operational.db`, Tier 2.

Both are compliant. Together the cursor has two durability promises, and the restore path mixes them:
Tier 1 comes back from git or a file backup, Tier 2 from its own backup, and the two cursors disagree.
Re-harvest then either doubles every metric or opens a permanent gap — the two outcomes AD-34.3 and
AD-3 respectively exist to prevent. Unit A additionally puts one field away from AD-39's "Secrets
never leave the encrypted store in a durable form": a refresh token is "config" by AD-10's own
spelling.

**Close it:** AD-10 must say the cursor is *referenced by* the instance and *owned by* Tier 2, and
AD-44's `connectors/` node must say what a connector file may **not** contain.

---

## F10 — SQLite sidecars are artifacts in no set, and `resolve()` refuses to name them `[MED-HIGH · VERIFIED]`

AD-5 mandates WAL mode (`service.py:302`, `PRAGMA journal_mode=WAL`). WAL creates
`operational.db-wal` and `operational.db-shm` on disk. Neither is a node in any tree, so neither is
in any tier, and `resolve` refuses an undeclared key by design ("guessing is how the same file
acquires two paths").

**Unit A — `BackupDriver`.** Copies exactly the paths `BACKUP_TARGETS` resolves to. It copies
`operational.db` without its WAL — so the backup is missing the most recent committed transactions,
i.e. precisely the "pending external writes and connector cursors" AD-3 says no rebuild can
reconstruct. Silent; the file opens fine.

**Unit B — `ReindexJob`.** Deletes `derived.db` by path per `REBUILD_TARGETS` and leaves
`derived.db-wal` / `-shm` orphaned beside a freshly created database.

Both obey AD-44 to the letter, and neither *can* do better: naming the sidecar requires composing an
undeclared path, which `paths.py` calls the exact defect it exists to prevent.

**Close it:** AD-44 needs a node type (or a `File` attribute) for an artifact whose physical form is
several files — "the database and its journal" as one addressable unit — and AD-3 must say that a
Tier-2 backup is not a file copy.

---

## F11 — A `Dir` with no tier is a declared, addressable artifact in none of the five sets `[MED · VERIFIED]`

AD-3's amendment closes with: "every persistent artifact is in exactly one of the three tiers,
`RETENTION_MANAGED`, or `DIAGNOSTIC_ONLY` — and the sets are derived from the scope model (AD-44), so
an artifact cannot enter one without a declaration to derive it from."

```
application untiered declared nodes: ['memory/', 'private/']
personal    untiered declared nodes: ['memory/', 'private/']
people      untiered declared nodes: ['memory/']
project     untiered declared nodes: ['memory/']
```

All four are keys: `resolve(APPLICATION, 'private/')` succeeds, `is_directory('private/')` is `True`.
`rules/` carries a tier "because the directory is also addressed as a unit"; `memory/` does not.
Nothing states the rule that distinguishes them.

**Unit A — `pm-ai doctor` completeness probe.** Enumerates `artifacts_in(kind)` and asserts each is
accounted for by one of the five sets — the literal AD-3 sentence. It reports two unaccounted
artifacts per scope and fails.

**Unit B — `scope_model` author.** "A `Dir` needs no tier of its own — its declared members carry
theirs." Also literally compliant.

The consequence is not academic: `private/` is an addressable, creatable directory holding Tier 1
(`people/`), Tier 2 (`operational.db`, `config.json`) and Tier 3 (`derived.db`, `vector_index/`), and
it answers no question about backup or rebuild. Any unit that reaches for it as a unit — a re-key
after AD-6's encryption toggle, an enclave wipe, a permissions audit — gets no durability answer at
all.

**Close it:** AD-3's completeness claim must be scoped to *leaves*, or AD-44 must make `tier`
required on `Dir` too. As written the claim is false of eight declared nodes.

---

## F12 — Two owners for the NFR-09 purge, and AD-5's enforcement cannot see one of them `[MED · VERIFIED]`

**Unit A — `MeetingPipeline` (in `app`).** Follows `write_capture`'s own contract: "Returns the path
written, because the caller that has just produced a capture is the one that has to purge it at
thirty days (NFR-09)." It calls `path.unlink()` when the capture ages out.

**Unit B — `PruningJob` (in `storage`).** Follows AD-5 ("A single storage service inside the daemon
owns every write — no other component opens a file for writing") and AD-7 ("the daemon owns … the
pruning pipeline"). It enumerates `transcripts/` and unlinks from inside the single writer.

Both cite an authority. Together they race on the same file, and one of them violates AD-5 — but the
check cannot tell: `WRITE_CALLS` in `tests/architecture/test_static_rules.py` contains `os.unlink`,
`os.remove`, `shutil.rmtree` and no method form, so `Path.unlink()` / `Path.rmdir()` in `app` passes
`test_ad5_single_writer_owns_all_file_writes`. This is the same class of miss as the
`Path.open("w")` bypass closed on 2026-08-19, one method over.

**Close it:** AD-5 must say deletion is a write and name its owner; `WRITE_CALLS` must include the
`Path` method forms; and `write_capture`'s docstring must stop assigning the purge to its caller.

---

## F13 — Two representations of "which segments are superseded" `[MED · PROSPECTIVE]`

AD-5 requires compaction to write a new summary segment and **record which sealed segments it
supersedes**. AD-44 makes `event_log/` a `Collection`, so nothing inside it is declarable and
`resolve` refuses any key naming one — the supersession record therefore has no declared home.

**Unit A — `Compactor` (manifest).** Writes `event_log/_manifest.md`. Legal on disk (a Collection's
members are named at runtime), but it is a persistent artifact in no tier — the exact oversight AD-3
warns about — and a reader that folds `*.md` by `(occurred_at, entry_id)` will parse it as a segment.

**Unit B — `Compactor` (in-band).** Adds a `compaction` member to AD-27's closed entry enumeration
and records supersession as an ordinary append-only entry. Self-describing, no new artifact.

Both are compliant. A reader built for one **double-counts** under the other: it folds the summary
segment *and* the segments it supersedes, and commitment states then change across a `pm-ai reindex`
— the outcome AD-35's determinism bullet exists to forbid, with AD-3's test still green.

**Close it:** AD-5 must name the supersession record's form and home. AD-44 needs a way for a
`Collection` to declare the *shape* of its runtime members (segment naming is already deterministic
in `storage`; the manifest question is not).

---

## F14 — The text matcher survives as a second, weaker authority reachable from layers the port is not `[MED · VERIFIED]`

AD-43: "exclusion is answered by **git itself** … behind `VcsPort`, with the adapter in
`pm_ai.platform`". `pm_ai.core` and `pm_ai.surfaces` may not import `pm_ai.platform`
(`.importlinter`: `os-behind-platform`, `subprocess-confined`), and they only reach `VcsPort` if the
composition root injects the adapter. Meanwhile `assert_capture_dir_ignored` — the text matcher — is
still exported from `pm_ai.domain` (`domain/__init__.py:48`), performs no I/O, and is importable
from anywhere.

**Unit A — `StorageService`.** Asks `VcsPort`. For AD-43's third case (rule present, directory
committed earlier) it refuses.

**Unit B — `pm-ai doctor` / `project add` validator, in `core`.** Not wired with a `VcsPort`, and
structurally unable to construct one. It calls `assert_capture_dir_ignored(CAPTURES, gitignore_text)`
— a legal domain function whose docstring even says it is "the pure form of the question". For the
same repository it reports **protected**.

Two components, one repository, opposite verdicts, and the one that talks to the user is the wrong
one. AD-43 says git is the authority; it never says the matcher may not be consulted, and the
layering makes the matcher the only option for an un-wired unit.

**Close it:** AD-43 must state that the matcher is **never** consulted for a protection decision
(test-only), or `VcsPort` must be a standard injected dependency of every unit that reports
protection status.

---

## F15 — `VcsUnavailable` collapses a permanent failure and a transient one, and AD-20 retries both `[MED · VERIFIED]`

AD-43: "Any inability to consult it (not a repository, binary absent, unexpected exit, timeout)
refuses the write." One error type, five causes, no permanence classification. `storage` converts
every one into `UnprotectedCaptureDir`.

**Unit A — `JobWorker`.** AD-20: every deferred unit is a durable row, delivery is at-least-once,
"exponential backoff with jitter, owned by the scheduler". A refused capture write is a failed job,
so it retries. A missing `git` binary never resolves; the job retries past NFR-09's 30 days, the
source drop in the watched folder is purged, and the meeting is lost with no message anywhere the PM
looks.

**Unit B — `TranscriptIngestion`.** AD-23: "Losing a transcript is recoverable, since it is transient
input nothing may depend on." So a refusal is terminal: drop, log, move on. Under a transiently
unmounted network repository, Unit B discards a capture that would have succeeded ten seconds later.

Both compliant, opposite behaviour, and neither is wrong under the spine as written. AD-39 already
owns this vocabulary (`healthy | degraded | needs_consent | failed`) — and scopes it to connector
instances, so the VCS authority has none.

**Close it:** AD-43 must classify its refusals as permanent or transient, and say which are jobs
(AD-20) and which are health states (AD-39). "Unknown is not permission" answers the *write*
question and says nothing about the *retry* question.

---

## F16 — AD-42.6 contradicts the AD-1 class-L amendment made in the same revision, and the two enforcement mechanisms already disagree `[MED · VERIFIED]`

AD-1's amended class L: "whisper.cpp; read-only local queries (`git check-ignore`, `git ls-files`) |
`pm_ai.models.local` for whisper.cpp, **`pm_ai.platform` for local queries**".

AD-42.6, unamended (line 537): "The registry stays a first-party allowlist (AD-18); **class L stays
whisper.cpp alone (AD-1)**."

And the Deferred entry on sandboxed execution still requires "an explicit **AD-1 amendment**" before
class L widens — which is exactly what happened on 2026-08-22, uncredited.

The two live enforcement mechanisms have already forked on it:

| Mechanism | Permits subprocess in |
| --- | --- |
| `.importlinter` `subprocess-confined` | `pm_ai.models.local`, `pm_ai.platform` |
| `tests/architecture/test_static_rules.py` | `SHELL_ALLOWED = {"platform"}` (line 17), with `models/local` carved out separately in the test body |

`SHELL_ALLOWED` is now dead code that disagrees with the contract beside it. A unit that reads
AD-42.6 and writes the corresponding check ("no subprocess outside `models.local`") fails
`platform/vcs.py`; a unit that reads AD-1 and writes the other one passes it. Both cite the spine.

**Close it:** amend AD-42.6 to the current class L, delete `SHELL_ALLOWED`, and record the class-L
widening in the Deferred entry that demands it.

---

## F17 — `person_id` is a path component, a scope subject, and an unowned identity `[MED · VERIFIED]`

`person_id` is interpolated into a filesystem path (`paths.py` `_directory_name`: no separators, no
leading dot, **lowercase only**), is the subject of a `DataScope`, and names the directory AD-4 calls
"a single deletable directory". No AD says what it is or who mints it. AD-34.2's alias table resolves
**Actors**, not subject ids, and nothing says the two are related.

**Unit A — `PeopleScopeWriter`.** Uses the resolved `actor_id` from AD-34.2, because that is the one
identity the spine says is canonical for a person.

**Unit B — `HrConnector` / FR-30 metrics.** Uses the HR platform's employee id, because that is what
the FR-31 sync must key on. If it arrives as `Alice.Smith` the resolver refuses it
(`MalformedSubjectId`), so Unit B normalizes — by its own rule, which is not Unit A's.

One report, two directories. AD-4's "leaving a role is one removal, not an audit" becomes two
removals, one of which nobody knows exists. And there is a second fork above it: **nothing declares a
people registry.** AD-11 requires an explicit registry for projects and forbids filesystem discovery;
`people/` is a `Collection`, so the only way to answer "who are my direct reports" — for NFR-09's
purge, for a role-change deletion, for the briefing — is to list the directory. Unit A lists the
directory; Unit B reads the HR platform; they disagree about a report who left.

**Close it:** AD-4 must define `person_id` (what mints it, its relation to `Actor`, its normalization)
and declare a people registry, or state explicitly that the directory listing *is* the registry and
why AD-11's prohibition does not apply.

---

## Also worth an AD, briefly

- **AD-4 and AD-44 disagree about what `people` holds.** AD-4: "career dossiers, goals agreed in a
  team 1:1, per-employee monitored metrics (FR-30, FR-31)". `PEOPLE_TREE` declares
  `memory/event_log/`, `memory/meetings/`, `transcripts/` — and `resolve` refuses an undeclared key,
  so **`CareerGoal` and FR-30's metrics have no home**. One unit files CareerGoals as
  `memory/career_goals.md` (Tier 1); another files them inside `memory/meetings/<id>/` (per the
  module's own comment); a third adds `metrics.db` and must choose Tier 1 ("agreed goals are truth")
  or Tier 2 (AD-25's reasoning for `personal_analytics.db`, which applies word for word to a
  longitudinal performance trend). Nothing arbitrates, and F2 means the third unit's basename choice
  can break the other two's build.
- **AD-42.5's Performance Index has no declared artifact.** "Lives beside the disclosure ledger …
  answerable from one file" is either `disclosure.md` — which AD-38 reserves for frontier provenance
  and cost, and which AD-17 parses for a monthly total — or an undeclared file `resolve` refuses.
  AD-24's Logging row says "Three destinations, no overlap", leaving no room for a fourth
  application-scoped record kind.
- **`GitVcs` resolves its binary through `PATH`.** AD-1 class L requires an "allowlisted **absolute**
  binary path"; `platform/vcs.py` uses `shutil.which("git")`. The whisper.cpp adapter will pin an
  absolute configured path (compliant); the git adapter takes whatever is first on `PATH` — inside
  the single writer's write path, and its verdict is the only thing keeping verbatim minutes out of
  the employer's repository.
- **`[NEW]` has stopped carrying information.** AD-30 through AD-44 are all tagged `[NEW]` across
  three revisions. A reader cannot tell which ADs this revision touched, which is what sent AD-42.6
  and `SHELL_ALLOWED` out of sync with AD-1.
- **Document structure (fix first).** AD-44 (line 517) is inserted into the middle of AD-42's
  numbered list; AD-42's items 4b, 5 and 6 (lines 533–537) render inside AD-44's Rule block, and
  AD-42's numbering runs 1, 2, 3, 4, 4b, 5, 6. Two units citing "AD-44.6" and "AD-42.6" mean the same
  paragraph, and a unit implementing the scope model currently reads "pm-ai does not generate skill
  code" as one of its clauses.

---

## What holds

Recorded so the next reviewer does not re-attack it:

- **AD-44's `Collection` type is right, and its refusals bite.** A `File` or `Dir` nested in a
  `Collection` is refused at construction; a `Dir` with no members is refused; a name carrying a
  separator is refused. The trees are diffable against `scope-model.md` as claimed, and the two
  divergences are marked at their declaration sites rather than dropped.
- **AD-43's trailing-slash reasoning is correct and is the load-bearing detail.** `_as_git_path`
  appends the slash, `check-ignore` is deliberately run *without* `--no-index`, and the two-fact
  verdict (`ignored`, `tracked`) is conjoined in one place (`is_excluded`) so no caller re-derives
  it. I could not construct a repository state where the adapter answers "protected" for a directory
  git tracks.
- **Tier 2 / Tier 3 physical separation holds structurally.** `assert_reindex_safe` checks the
  artifact *set* rather than intent, and `test_no_tier_one_artifact_lives_inside_a_rebuildable_one`
  checks containment in the direction it was written for. F3 is the missing mirror, not a
  regression.
- **The AD-13 / AD-14 disjointness, `TargetRef` sub-resource rejection, and `NonDurableReferent`**
  are all construction errors rather than conventions. AD-33's "a transcript is never a
  `source_ref`" is unrepresentable, as intended.
- **`_durability_index` refusing a two-durability key is the right instinct** even though F2 shows
  the AD never states it: the failure is loud, at import, with the conflict named. Fix the AD, not
  the check.

---

## Priority

| # | Finding | Severity | Reachable today |
| --- | --- | --- | --- |
| — | AD-44 inserted into AD-42's rule list (lines 517–537) | **fix first** | document |
| F1 | `people/` has two addresses and two privacy labels | HIGH | yes |
| F2 | Basename-keyed derivation forbids two compliant per-scope tiers | HIGH | yes |
| F5 | Scopeless referent bypasses AD-36's fail-closed default | HIGH | yes |
| F4 | AD-43 gated on scope kind; the spine sanctions a personal repository | HIGH | yes |
| F7 | Two namespaces spelled `scope`; AD-36's join does not say which | HIGH | yes |
| F3 | `transcripts/` nested inside a backup target | HIGH | yes |
| F6 | AD-35 vs AD-39 on the same silence | HIGH | no (unbuilt) |
| F8 | `goal:` scopeless across two scopes; AD-38 cannot see it | HIGH | no (unbuilt) |
| F9 | Cursor has two homes and two tiers | MED-HIGH | no (unbuilt) |
| F10 | SQLite sidecars in no set and unnameable | MED-HIGH | yes |
| F11 | Tier-less `Dir` falsifies AD-3's completeness claim | MED | yes |
| F12 | Two owners for the purge; `Path.unlink` invisible to AD-5's check | MED | yes |
| F13 | Two representations of segment supersession | MED | no (unbuilt) |
| F14 | Text matcher survives as a second authority | MED | yes |
| F15 | Refusal permanence undefined; AD-20 retries forever | MED | yes |
| F16 | AD-42.6 vs the AD-1 class-L amendment; `SHELL_ALLOWED` forked | MED | yes |
| F17 | `person_id` unowned; no people registry | MED | yes |

**Recommendation:** `status: final` is premature. F1 and F2 are one line of a scope tree away from
being live, F5 is live now, and F16 is an internal contradiction introduced by this revision. The
smallest set that closes the cluster: one new AD fixing **addressing** (a scope root is never another
scope's artifact; the derived tables' true key; sidecars and multi-file artifacts), and one tightening
**verdicts** (what provenance a scopeless referent carries, what replaces a withheld `UNKNOWN`, and
whether a refusal is permanent).
