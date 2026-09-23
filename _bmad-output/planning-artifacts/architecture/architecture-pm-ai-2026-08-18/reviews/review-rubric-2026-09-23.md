# Rubric Walk — 2026-09-23 amendment

- **Target:** `ARCHITECTURE-SPINE.md` (updated 2026-09-23 — amends AD-4, AD-9, AD-12, AD-15, AD-23, AD-25, AD-27, AD-29, AD-33, AD-34, AD-35, AD-39, AD-41; adds AD-48, AD-49; adds four Deferred entries and an Open Risks row; edits the layer table, source tree, Enforcement table and two diagrams)
- **Lens:** good-spine checklist — divergence coverage, rule enforceability, Deferred safety, tech currency, brownfield ratification, dimensional completeness
- **Date:** 2026-09-23
- **Evidence base:** the spine; `pm_ai/` and `tests/` read directly; `.importlinter`; `pyproject.toml` and `uv.lock`; a live PyPI currency check; a measured run of `uv run pytest` and `uv run lint-imports`

## Measured baseline

Recorded first, because several findings below are about the gap between what the
spine says the suite proves and what it proves.

```
uv run pytest      →  2612 passed, 24 skipped in 53.60s   (exit 0)
uv run lint-imports →  115 files, 471 dependencies. Contracts: 12 kept, 0 broken.
```

All 24 skips are in `tests/architecture/test_domain_invariants.py`, gated by the
`mod()` helper on six modules Phase 1 has not built (`models.router` ×9,
`core.commitments` ×4, `core.proposals` ×3, `core.transcripts` ×2, and one each for
`core.dispatch`, `core.retrieval`, `core.scheduler`, `storage.reindex`,
`surfaces.api.app`, `surfaces.telegram.bridge`). `tests/conftest.py:60` holds
`EXPECTED_SKIPS = 24` and fails in both directions.

## Verdict

**Changes requested.** The amendment is, on the ADs themselves, unusually honest
work: AD-9/AD-35's coverage-honesty revision, AD-27's dual-vocabulary guard,
AD-25's renderer wall, AD-39's custody/HTTP split and AD-12's `Sanitized`
fixed-point are all enforceable rules, all ratified by running code, and each
names its own residual limit rather than claiming completeness. That is the
standard this document set for itself and it mostly meets it.

The failures cluster in the two sections the amendment *also* edited — Enforcement
and Open Risks — where the spine now **overstates one risk that was closed four
weeks before the amendment, mis-credits a check, and gets the membership of its
own coverage-gap list wrong in three places**. Since the entire purpose of those
two sections is to stop green CI reading as compliance, an inaccurate list there
is the failure they exist to prevent.

Beyond that: one of the two new ADs (AD-49) forbids nothing and does not
determine a unique answer; the other (AD-48) is evadable by one type annotation;
a whole dimension — format and layout evolution — is neither decided, deferred,
nor an open question while a full migration runner ships underneath it with no AD;
and AD-4's clarification was not reconciled against AD-33/AD-38, which it
silently puts on a collision course.

Findings 1–6 should land before this spine is treated as reconciled. 7–13 are the
same pass; most are a paragraph each.

---

## Blocking

### 1 — AD-49 is a description, not a constraint (High)

AD-49's Rule: *"When a refusal is raised by `core` and by an adapter, it is
declared in `pm_ai.ports`, beside the protocol it belongs to."* Its own rationale
says *"Three slices arrived at this independently before it was written down."*
That sentence is the problem: it records a habit. Three things are wrong with it
as an invariant.

**It is enforced by nothing.** None of the twelve `.importlinter` contracts
constrains where an exception class is *defined*; `ports-depend-only-on-domain`
(`.importlinter:234`) constrains what `ports` may import, not what must live
there. No test in `tests/architecture/` asserts the rule — `grep -rn "AD-49"`
outside the planning artifacts returns nothing. By the spine's own closing rule
(*"An AD nothing enforces is a convention, and conventions drift"*), AD-49 is a
convention that has been promoted to an AD without acquiring a check.

**It does not determine a unique answer.** The rule's own justification —
*"`ports` may import only `domain`, and both sides may import `ports`, which makes
it the only layer both can reach"* — is false as stated. `domain` is also reachable
by both sides; that is exactly what makes `ports` able to express protocols in
domain types. And the codebase already uses it that way for cross-boundary
refusals: `VcsUnavailable` and `UnpermittedGitSubcommand` live in
`pm_ai/domain/vcs.py:56,66` while `VcsPort` lives in `pm_ai/ports/__init__.py:313`;
`CommittedScopeLeak` lives in `pm_ai/domain/disclosure.py:51` and is raised by
`domain` and caught in `storage` and `app`. So two units one level down can both
comply — one putting a shared refusal in `ports`, the other in `domain` — which is
the divergence an AD exists to prevent.

**Most of its subjects do not meet its condition.** Of the ten refusal classes in
`pm_ai/ports/__init__.py`, only three are raised by both `core` and an adapter:
`DuplicateConnector` (`core/connector_enrolment.py:220` / `connectors/registry.py:162`),
`KeychainUnavailable` (`core/enrolment.py:129` / `platform/keychain.py:106`) and
`KeyAlreadyEnrolled` (`core/enrolment.py:114` / `platform/keychain.py:182`). The
other seven are single-sided — `ArtifactBusy` is raised only in
`storage/service.py:1332`, `DecryptionFailed` only in `storage/crypto.py`,
`KeyNotFound` only in `platform/keychain.py`. The AD's condition therefore
describes 30% of what is in `ports` and says nothing about the rest.

**Fix.** Make the rule decide the case it currently leaves open — state when a
refusal belongs in `domain` (a domain-invariant violation, raised by `domain`
itself) versus `ports` (a boundary refusal raised on both sides), so the two homes
are a partition rather than a preference. Then add the check: an AST sweep that
finds every exception class raised in both `pm_ai.core` and an adapter package and
asserts its defining module is `pm_ai.ports`. It is the same shape as the existing
`test_ad1_the_git_subcommand_set_is_closed_in_domain`
(`tests/architecture/test_static_rules.py:277`).

### 2 — Open Risks asserts a risk that was closed 26 days before the amendment (High)

The first Open Risks bullet reads, verbatim and unamended:

> **`pm_ai.platform` shells out and is scanned for it by nothing.** … `SHELL_ALLOWED = {"platform"}` is defined in `test_static_rules.py` and referenced nowhere. So `shell=True`, `os.system`, `eval` and `exec` are unchecked in the one package that legitimately spawns a process … Needs a `platform`-specific check that permits an allowlisted binary and nothing else, and the permitted command set closed in `domain` the way AD-27/32/34/40 close theirs.

Every load-bearing clause of that is now false.

- `tests/architecture/test_static_rules.py:32` reads `SHELL_ALLOWED = {"platform", "models"}` — a different value from the one quoted.
- It **is** referenced, twice: at `:219` as a disjointness assertion against the
  totally-banned layer list, and at `:235` as
  `@pytest.mark.parametrize("layer", sorted(SHELL_ALLOWED))` driving
  `test_ad1_a_spawning_layer_is_scanned_too` (`:236-272`), which scans each
  spawning layer for `NEVER_SPAWNABLE = {"os.system", "os.popen", "os.execv",
  "eval", "exec"}` (`:35`) and for any `shell=True` keyword (`:261-269`).
- The comments at `:242-249` record the fix being measured the way this document
  demands — by planting `os.system("echo pwned")`, `exec(...)` and
  `subprocess.run(..., shell=True)` in `pm_ai/platform/vcs.py` and observing red.
- The "permitted command set closed in `domain`" half also shipped:
  `GIT_SUBCOMMANDS` is a `frozenset` in `pm_ai/domain/vcs.py:45`, refused at
  `pm_ai/platform/vcs.py:199-205` with `UnpermittedGitSubcommand` **before** the
  PATH lookup, and checked by `test_ad1_the_git_subcommand_set_is_closed_in_domain`
  (`test_static_rules.py:277`).

Only the tail survives: `git` is still located by `shutil.which` rather than an
allowlisted absolute path (`pm_ai/platform/vcs.py:208`).

This matters twice over. First, the amendment's stated job was reconciling the
spine against a completed wave, and this is the largest single thing the wave
closed — the document catalogues two entries elsewhere that "outlived" their fix
by six and eight days, and this one has outlived it by 26. Second, **AD-1's class L
constraint column under-describes its own strongest guard**: it names
`check-ignore` and `ls-files` parenthetically in prose and says nothing about the
closed set in `domain` that actually refuses everything else. A builder reads AD-1
and Open Risks together and concludes the boundary is open when it is closed by a
raise.

**Fix.** Retire the bullet the way the other four retirements are written — struck
through, with what closed it and when — leaving the PATH-lookup residue as its own
short entry. Add the `GIT_SUBCOMMANDS` closure to AD-1's class L row, where it is
the rule rather than a footnote.

### 3 — The "Coverage is overstated" row was amended this run and its membership is wrong in three places (High)

The row carries `[revised 2026-09-23]` and reads: *"Eight ADs (AD-2, 6, 8, 13, 17,
21, 22, 31) have a populated 'Enforced by' cell in the README and no running
check."*

Measured against the 24 actual skips:

- **AD-6 runs.** `test_ad6_markdown_is_never_encrypted`
  (`tests/architecture/test_domain_invariants.py:207`) is not gated, and
  `tests/architecture/test_encryption_policy.py` (15 tests) runs in full.
- **AD-31 runs.** Its checks in `test_paths.py` are live; the encryption and path
  siblings do not skip.
- **AD-28 is missing from the list** and its sole check skips.
- **AD-37 is missing from the list** and its sole check skips.

The count of eight happens to survive; two of its members are wrong and two are
absent. The ADs whose every listed check currently skips are **AD-2, AD-8, AD-13,
AD-17, AD-21, AD-22, AD-28, AD-37**.

The row's entire function is to stop a green suite reading as compliance. A
membership list that is wrong in both directions does the opposite: it clears two
ADs that are covered and, more dangerously, vouches for AD-37 — the compare-and-swap
invariant standing between two surfaces approving one proposal — which is
unexercised.

**Fix.** Replace the list with the measured one, and state the generating rule
(*"every AD whose checks live only behind an unbuilt module in
`test_domain_invariants.py`"*) so the next reader can recompute it rather than
trusting a hand-maintained set — the same reasoning AD-44 gives for deriving
`ARTIFACT_TIER` rather than maintaining it beside the trees.

---

## Should fix in the same pass

### 4 — The Enforcement table mis-credits AD-48 and omits two enforced ADs (Medium-High)

Three defects in a table the amendment edited.

**AD-48 is pointed at the wrong check.** The row *"Guards under optimization |
`tests/architecture/test_guards_survive_o.py` | AD-48 and every import-time
invariant"* credits a file whose docstring names AD-3, AD-14 and AD-44 and never
mentions AD-48. What that file actually enforces is the general property AD-48
*depends on* — that no `assert` statement exists anywhere in `pm_ai/` — which is a
strictly weaker claim than the table makes. The real mechanism is the import-time
`raise` chain in `pm_ai/domain/events.py:327-417` over `UNTRUSTED_TEXT` (`:208`)
and `TRUSTED_TEXT` (`:246`), exercised by `tests/domain/test_sanitizable_declarations.py`
— a file the table does not list.

**AD-41 and AD-42 read as unenforced and are not.** Both have running checks —
`tests/slice/test_alignment.py` against `pm_ai/domain/goals.py`, and
`tests/slice/test_self_improvement.py` against `pm_ai/domain/selfimprovement.py`
(the flattery loop at `:40`, the no-self-widening rule at `:61`, the
estimate-as-measurement rule at `:84`, the proposal-only ceiling at `:94`). The
Enforcement table lists only `tests/architecture/` paths, so the whole `tests/slice/`
tier is invisible to it. Given that the table is the answer to "what does green CI
mean", omitting a tier that proves two of the document's most ambitious ADs
understates the spine's own position.

**The README's coverage map stops at AD-38.** `tests/architecture/README.md:58-95`
has rows for AD-1 through AD-38 with three gaps (AD-10, AD-18, AD-19 sit under
"Not mechanically enforced"). **There are no rows for AD-39 through AD-49 at all** —
eleven ADs, including both added this run and both of the closure-style ADs
(AD-40, AD-41) the document cites as exemplary. The Enforcement section points at
that README for "full AD→check mapping" without saying that the map covers only
the older three-quarters of the spine.

### 5 — AD-48's rule is evadable by one type annotation (Medium-High)

AD-48's Prevents is *"a new payload type adding a text field nobody classified."*
Its Rule reaches *"Every `str` / `str | None` field"*, and the AD then names the
hole itself: *"Nested types that carry text and containers of text fall outside
both records, untouched and undeclared."*

Naming a hole is better than hiding one, but this hole is the Prevents. The
completeness gate at `pm_ai/domain/events.py:394-400` computes
`unaccounted = sorted(n for n in present if _is_text(hints[n]) and n not in declared)`
— so a field annotated `list[str]`, `dict[str, str]` or a nested dataclass is not
*refused* for being undeclared, it is **not asked about**. A payload author who
writes `comments: list[str]` instead of `comment: str` satisfies AD-48 fully and
delivers exactly the outcome AD-48 exists to prevent. The existing
`WorkItemPayload.assignee: Actor | None` is already outside both records today.

The gap is worse than "unreached" because the rule's guarantee is *completeness* —
it is the thing that replaced a producer-side pass whose defect was a guess about a
field name. A completeness check with a silent exemption class is the same failure
one level up.

**Fix.** Make the hole fail closed rather than fail silent: refuse a container-of-text
or nested-dataclass field that appears in neither record, with a message saying the
declaration form for it is not decided yet. That converts an invisible exemption
into a loud one, which is the discipline AD-48's own rationale argues for, and costs
nothing today because no registered payload has such a field that matters.

### 6 — A whole dimension is silent: format, schema and layout evolution (Medium-High)

This is the dimensional-completeness finding. The altitude owns how persisted
artifacts change shape across releases. The spine answers it in three different
states and owns none of them.

**Tier-2 SQLite migration is built with no AD.** `pm_ai/storage/service.py:136-186`
ships a forward-only migration runner with a five-part discipline stated in its own
comments: a `schema_version` table, `SCHEMA_VERSION = 1`, a `MIGRATIONS` tuple, a
rule that a version bump lands only in the commit that appends a migration
(`:142-143`), a rule that a migration must not commit because the runner wraps each
step in a savepoint (`:181-184`), and a refusal to open a store stamped from a later
version (`:590`). Those are invariants by any test this document uses — and no AD
contains them. Worse, the Enforcement table points `test_schema_versioning.py` at
**AD-3**, whose text is about tiers, backup and rebuild and contains no migration
rule at all. That directly violates the spine's own closing instruction: *"do not
edit a check without editing its AD."*

**Tier-1 markdown has no versioning, and the clause was withdrawn this run.** AD-27
retired `GRAMMAR_VERSION` on the correct ground that nothing wrote or read it, and
pushed the choice to Deferred. Tier 1 is the source of truth, hand-editable by
design, and equally unrebuildable. So the binary store that *can* be rebuilt from
Tier 1 has a migration discipline, and the store that cannot has none. That
asymmetry is defensible, but it is a decision the spine makes by omission rather
than states.

**On-disk layout change has no owner at all.** AD-44 declares the four trees as
literal data; nothing says what an already-installed system does when a tree
changes. This is not hypothetical — the spine has moved artifacts between scopes
twice on its own record: `meetings/` from personal to subject-owning on 2026-08-20
(AD-33) and `transcripts/` from the application scope to the owning scope on
2026-09-03 (AD-4/AD-23). Each of those strands an installed machine's files at
paths no resolver will look at again, in the two tiers no rebuild can reconstruct.
Neither AD names the move.

Nothing in Deferred covers any of the three. The retired Tier-2 entry says the work
is *"now scoped work, not a deferral"* — but scoped work with no AD and a check
pointed at the wrong AD is precisely the "layer the spine assumed and never
assigned" that AD-45 was written to fix.

**Fix.** One AD covering artifact evolution, with three clauses: the Tier-2
forward-only discipline (lift it from the code comments, which already state it
better than most ADs state theirs and would then have a home); the Tier-1 grammar
question, kept as a pointer to the existing Deferred entry so the silence is
declared; and layout change — at minimum, that a tree change is a migration event
and a version the resolver can compare, so an unmigrated install refuses rather
than silently reading an empty scope. Repoint `test_schema_versioning.py` at it.

### 7 — AD-41's rules 3, 6 and 7 are unexercised and the AD does not say so (Medium)

The amendment to AD-41 is itself well-ratified: the PM-authored id is validated by
`GOAL_ID` (`pm_ai/core/goal_register.py:189`) with a raise at `:514-521` and a
duplicate raise naming both line numbers at `:409-418`, and `GoalDomain`/`GoalHorizon`
are closed enums at `pm_ai/domain/goals.py:25,33`. That half is fine.

What the amendment did not record is that three of AD-41's seven rules have no
production path, and one of them behaves differently from what the rule says.

Rule 2 requires an edit that removes or renames a goal to leave the citation
*"explicitly unresolved and visible — never silently dropped"*. The implementation
is `resolve()` **raising** `UnresolvedGoal` (`pm_ai/domain/goals.py:85-97`), and
nothing catches it — `grep` for `UnresolvedGoal` outside `domain/goals.py` finds
only docstrings and `tests/slice/test_alignment.py:78`. The raise's own message
tells the caller to *"Surface the recommendation as unaligned rather than dropping
the tag"*, and no caller does. A crash is a third behaviour the AD does not name;
it is neither the silent drop the rule forbids nor the visible unresolved state it
requires. Given that the amendment made the id *more* rename-prone on purpose —
calling this clause "load-bearing rather than defensive" — the unhandled raise is
the one place the amendment's stated reasoning does not hold up.

Rules 6 and 7 are implemented and dead: `rank_key`, `order` and `unaligned`
(`pm_ai/domain/goals.py:114-151`) have no caller in `pm_ai/`, and the dashboard's
`_strategic_milestones` (`pm_ai/core/rendering.py:752`) renders the goal register,
never a `Recommendation`.

None of this is a design error — story 22 owns it. The finding is that the spine is
scrupulous about exactly this admission everywhere else in the same amendment
(AD-23's *"there is no port to conform to"*, AD-12's *"nothing in `pm_ai/`
implements or calls `ModelPort` yet"*, AD-48's *"these declarations have no reader
yet"*) and omits it here, which makes AD-41 read as more proven than its neighbours
when it is less.

### 8 — AD-4's clarification was not reconciled against AD-33 and AD-38 (Medium)

AD-4 `[clarified 2026-09-23]` now files an unmapped calendar row into the personal
scope: *"A meeting the PM never tagged is the PM's own until they tag it, which is
why an unmapped calendar row is filed here rather than refused."*

AD-33 requires `Meeting.scope` to be required rather than defaulted, and says it
decides *"whether a project-scope record may cite this meeting at all"*. That check
exists and raises: `assert_citation_legal` (`pm_ai/domain/disclosure.py:158-179`)
refuses when `into.is_project and cited.is_personal`.

Compose the two. For an untagged work meeting — the default state until the PM
tags it — the meeting is personal-scoped, and every commitment the extraction
pipeline would write into the project ledger cites it and is refused. AD-4 presents
its clarification as the permissive answer (*"filed here rather than refused"*)
while it in fact relocates the refusal from ingest time to citation time, where it
fires on the common case instead of the edge case, and where the operator sees a
`CommittedScopeLeak` about a scope boundary rather than a prompt to tag a meeting.

Story 33c (Outlook-category to project-scope mapping) is the unit that will hit
this; `pm_ai/connectors/graph/calendar.py:684,1134` already carries the category
lists verbatim and maps nothing. It has two compliant readings of the spine and no
way to choose.

**Fix.** State the consequence in AD-4 and name the intended behaviour: either an
untagged meeting yields no project-scope extraction (fail closed, and say so where
the PM can act on it), or untagged rows get a distinct provisional state that is
neither personal nor project until mapped. The second is a real design decision and
belongs here rather than in story 33c.

### 9 — Two Deferred entries permit divergence one level down (Medium)

**9a — "Which side of the machine a *new* task class runs on."** The entry is
honest about the gap and names a trigger (*"Revisit when story 7 builds it, before
any class beyond the ten is served"*), and story 7 precedes the stories that need a
new class, which mitigates it. What it does not say is that **a running test
actively fences the two obvious places to declare the answer**:
`tests/architecture/test_sanitize_boundary.py:553-560` asserts that
`pm_ai/domain/task_classes.py` contains no dict, set, list or tuple, and `:562-568`
asserts `ModelPort.complete` has no `model`, `tier`, `adapter`, `destination`,
`local` or `frontier` parameter and no `route`/`routes`/`TIERS` member. A builder
adding a class and trying to declare its side hits a red architecture test with no
pointer to where the answer does belong. Add that sentence to the Deferred entry,
or the deferral's cost is paid as a surprise.

The underlying exposure is real: the spine's own implementer review found that
FR-06's summary card, FR-07's fact-check digest and FR-25's drift audit are *"none
of those… any of the ten"*, and stories 13, 18 and 20 each need one.

**9b — "Enforcing AD-45's graph depth."** The entry declines enforcement and then
names the exact job that would break it: *"a 'regenerate `daily_dashboard.md`
whenever `meetings/` changes' job is unremarkable to propose and is a loop if its
output is watched upstream."* Story 23 (*Daily briefing and notification
discipline*) is that job, and `daily_dashboard.md` is a declared Tier-1 artifact in
two trees. The deferral is defensible on cost, but it currently rests on nobody
proposing the named job while the named job is in the queue. Either name story 23
in the entry as the revisit trigger, or add the cheap version of the check — assert
no job's `outputs()` intersects its own `inputs()` — which is one set operation
over the inventory AD-45 already requires.

### 10 — AD-12 and AD-29 were amended the same day and disagree on whether a derived copy persists (Medium-Low)

AD-12: *"The raw is what persists; the derived copy is rebuilt at the point of
use."*
AD-29 `[revised 2026-09-23]`: *"a `for_model`-derived slice is what a staged
`Proposal` carries as its summary, so it reaches `operational.db` and the card the
PM reads."*

The code agrees with AD-29. `pm_ai/domain/sanitize.py:654-659` states it precisely —
`Sanitized` is never persisted, but the `[:80]` proposal summary derived from
`for_model` is, and is deliberately not re-derived when the filter widens.
`Proposal.summary` is a plain `str` (`pm_ai/domain/proposals.py:24`).

AD-12's sentence is defensible if "the derived copy" means the `Sanitized` value
specifically, and false on the plain reading a builder will take. Narrow it to *the
model copy* and cross-reference AD-29's accepted consequence, which is the more
careful of the two statements.

---

## Currency and consistency

### 11 — The Stack table was not re-verified in this pass, and diverges from the build in four ways (Medium-Low)

Checked against PyPI today (2026-09-23) and against `pyproject.toml` / `uv.lock`.

Correct and current: `fastapi 0.141.1`, `watchdog ==6.0.0`, `sqlite-vec ==0.1.9`,
`python-telegram-bot 22.8`, `keyring 25.7.0` — each is both the pin and the latest
release. `git 2.50.1` verified as present. The pre-1.0 `sqlite-vec` and the
21-month-stale `watchdog` warnings both still hold.

Divergences:

- **`uvicorn`.** The Stack says **0.52.4**; `pyproject.toml` and `uv.lock` hold
  **0.52.3**. Both exist; latest is 0.53.0. `pyproject.toml`'s own comment calls the
  extra *"The Stack table from ARCHITECTURE-SPINE.md, made executable"*, so a table
  naming a version the build does not hold is the drift that comment exists to
  prevent.
- **Three pinned runtime dependencies are absent from the table entirely.**
  `msal==1.38.0` — the only thing in the system that obtains a Microsoft token, a
  credential path governed by AD-39, pinned 2026-09-06 with two measured behaviours
  documented in `pyproject.toml`; `cryptography>=50.0.0` — the AES-256-GCM that
  implements AD-6, notable because the table still carries the struck-through
  `sqlcipher3` row explaining what was removed and never names what replaced it; and
  `tzdata`, deliberately unpinned and load-bearing for Graph calendar timezone
  resolution on a non-macOS host. A table that omits the credential library and the
  cipher is not the stack.
- **`anthropic==1.2.0` against a current 1.8.0.** The pin is deliberate and well
  argued — `tool_runner` is beta and AD-16 makes it load-bearing — and pinning is the
  right call. But the introspection that justifies it is dated 2026-08-28, six minor
  releases back, and the table presents that verification as current. The relevant
  question the table cannot answer today is whether `tool_runner` has left beta,
  which would change AD-16's standing dependency risk rather than merely the number.

### 12 — The named companion is five weeks and seven ADs stale (Low)

The spine's frontmatter declares `companions: ['SOLUTION-DESIGN.md']`, and
`AGENTS.md` makes the companions part of the build contract. That file is dated
2026-08-20, its header reads *"Companion to `ARCHITECTURE-SPINE.md` (42 ADs)"*, and
it mentions none of AD-43 through AD-49. Its risk table still presents
`TranscriptSourcePort` as the mitigation for Graph tenant dependency, which AD-23
this run downgraded to *"the Protocol does not exist yet."* A builder sent to the
named companion for reasoning gets the pre-AD-43 architecture.

### 13 — Note on AD-23's wording (Low)

AD-23 calls the shipped module *"the shipped Graph transcript adapter."*
`pm_ai/connectors/transcripts/graph.py` is 22 lines with a `_fake_api: dict` field
and a `fetch()` that reads from it — no network, no Protocol, no `isinstance` gate.
"Shipped" overstates it by enough to matter in an AD whose point is that the port
does not exist; "the placeholder Graph adapter" costs nothing and is true.

---

## What the amendment got right

Recorded because a review that lists only defects misrepresents the change.

- **AD-9 / AD-35's coverage honesty is the best invariant in the amendment.**
  `HarvestResult.coverage` is `CoverageWindow | None` (`pm_ai/domain/harvest.py:283`)
  and a window claimed by a rowless harvest **raises** (`harvest.py:348-354`).
  Failures are durable in a real table (`harvest_failures`,
  `pm_ai/storage/service.py:112-118`), and `evaluate_commitment`'s `harvest_failed`
  is required and un-defaulted (`pm_ai/domain/lifecycle.py:208-213`) on the stated
  ground that a safe default would silently restore the indefinite waiting `ERROR`
  exists to end. The AD, the reason and the code all agree.
- **AD-27's dual-vocabulary guard is enforceable and enforced.** Payload shape
  raises at construction (`pm_ai/domain/events.py:442-448`), self-action types are
  refused from the payload registry at import (`event_entries.py:355-364`), and
  `SELF_ACTION_FIELDS` is a required-floor rather than an equality
  (`event_entries.py:101-121`, `:277-281`). Withdrawing the unimplemented
  `GRAMMAR_VERSION` clause rather than leaving it standing is the right call, well
  argued.
- **AD-25's wall is a signature.** `render_dashboard` takes `goals`
  (`pm_ai/core/rendering.py:247`); `render_project_dashboard` has no such parameter
  and no fourth positional slot (`:293`). The leak is inexpressible rather than
  forbidden, and the AD says exactly that.
- **AD-12's chokepoint is real and its three limits are accurately stated.**
  `Sanitized` is `@typing.final` (`pm_ai/domain/sanitize.py:618`) with a fixed-point
  check that raises `ForgedSanitization` (`:667-670`), `ModelPort.complete` takes
  `Sequence[Sanitized]` (`pm_ai/ports/__init__.py:787-793`), and mypy runs inside
  pytest against a fixture built to be wrong. The AD's admission that
  `instructions: str` is a hole, that the transcript path does not reach the port,
  and that nothing calls it yet, is all verified true.
- **AD-39's custody/HTTP split resolves a contradiction the spine really did
  carry.** Refresh lives in `pm_ai/connectors/graph/auth.py:632-676`; the in-memory
  store is named as such and is a required constructor field rather than a
  default_factory (`auth.py:333-358`, `pm_ai/app/wiring.py:576`), so the temporary
  choice is visible at the composition root. Health states exist
  (`pm_ai/domain/health.py:47-54`) and are genuinely persisted nowhere.
- **The enforcement substrate is in good shape.** 12/12 import contracts kept, 2612
  passing, and a bidirectional skip ratchet at 24 with its own ladder recorded. The
  `model-clients-confined` hole the amendment added to Open Risks is real and
  accurately described — `.importlinter:77-93` omits `domain`, `ports` and `app`,
  and the neighbouring `subprocess-confined` contract (`:213-232`) is exactly the
  template for fixing it.

---

## Rubric summary

| Criterion | Verdict |
| --- | --- |
| 1. Fixes the real divergence points, misses none | **Mostly.** Misses format/schema/layout evolution (#6), the untagged-meeting scope interaction (#8), and the declared-side question for a new task class (#9a) |
| 2. Every Rule enforceable and actually prevents its divergence | **No.** AD-49 forbids nothing and does not determine a unique home (#1); AD-48's completeness is evadable by annotation (#5); AD-41's rules 3/6/7 are unexercised and rule 2 behaves differently from what it says (#7). The remaining eleven amended ADs are enforceable and ratified |
| 3. Deferred entries cannot let two units diverge | **Two can.** #9a (a test fences the obvious declaration site) and #9b (the named loop-shaped job is in the queue). The other fourteen entries name a single owner or a real trigger |
| 4. Named technology verified-current | **Partly.** Five pins are current and correct; `uvicorn` names a version the build does not hold, three pinned runtime dependencies including the credential and cipher libraries are absent from the table, and `anthropic`'s verification is six releases old (#11) |
| 5. Ratifies rather than contradicts the brownfield | **Yes on the ADs, no on the meta-sections.** The thirteen amended ADs describe the code accurately. Open Risks asserts a risk closed 26 days earlier (#2), the coverage-gap list is wrong in three places (#3), and the Enforcement table mis-credits AD-48 while omitting the `tests/slice/` tier (#4) |
| 6. Every dimension decided, deferred, or open | **One silent.** Format, schema and layout evolution — built without an AD, withdrawn for Tier 1, absent for layout (#6). The rest of the operational envelope (deployment, environments, supervision, health, boot, backup-as-out-of-scope) is explicitly decided |
