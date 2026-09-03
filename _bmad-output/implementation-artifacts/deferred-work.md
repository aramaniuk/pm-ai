# Queued and deferred work

## Story 1 decomposition

`stories.yaml` story 1 ("Scope and storage foundation") is implemented as nine
specs under `_bmad-output/specs/spec-pm-ai/stories/`, in this order. Story 2 in
the queue begins after `1i`.

| Spec | Delivers | Depends on |
|---|---|---|
| `1a-scope-path-resolver` | one object resolving scope + artifact to a path | — |
| `1b-storage-writes-through-the-resolver` | storage writes where the scope model says, with an injected clock | 1a |
| `1c-refuse-unprotected-captures` | a raw capture cannot be written into a git-tracked directory | 1a, 1b |
| `1d-keychain-port-and-macos-adapter` | master-key custody in the macOS Keychain | — |
| `1e-encryption-classifier` | which artifacts are encrypted at rest | — |
| `1f-envelope-cipher-and-encrypted-store` | encryption at rest for the operational store | 1d, 1e |
| `1g-startup-diagnostics` | the two clean-install failures become visible | 1d, 1f |
| `1h-derived-tier-rebuild` | the derived tier is provably disposable | 1a, 1b |
| `1i-operational-schema-versioning` | the unrebuildable store can be upgraded safely | 1b |


## Story 2 decomposition

`stories.yaml` story 2 ("Event log and disclosure ledger") is eleven specs under
`_bmad-output/specs/spec-pm-ai/stories/`, in this order. Sized to the 1600-token
spec ceiling: the largest is 1262, the set totals 12,051.

| Spec | Delivers | Depends on |
|---|---|---|
| `2a-two-clock-bases` | AD-35's two bases and the implausible-timestamp refusal | — |
| `2b-flag-implausible-provider-timestamps` | the flag reaches the ledger instead of the exception reaching the batch | 2a |
| `2c-closed-entry-type-enumeration` | AD-27's second closed enumeration, which never existed | — |
| `2d-one-entry-renderer` | one definition of a ledger line, replacing four grammars | 2c |
| `2e-retire-the-free-string-append` | `append_event_log` takes a typed entry; 14 call sites move | 2d |
| `2f-segment-parser-and-deterministic-fold` | segments become readable; fold by `(occurred_at, entry_id)` | 2d |
| `2g-open-and-sealed-segments` | exactly one open segment; sealed ones refuse writes | — |
| `2h-event-log-accessor` | derivation-services rule 3's `EventLog` | 2f, 2g |
| `2i-disclosure-ledger-append` | the disclosure ledger gains a writer | — |
| `2j-disclosure-ledger-reads` | AD-17's monthly total and AD-31's period query | 2i |
| `2k-retrospective-aggregation` | CAP-10's weekly counts by category | 2h |

Decisions taken at the sizing gate (2026-08-29):

- **The entry format is Markdown.** `SPEC.md` CAP-10 said "appends a JSON line"
  against `storage-contract.md`'s example, the Tier-1 "plaintext Markdown" row,
  the `%Y-%m.md` segment name and the shipped `_append_batch`. Corrected in
  `SPEC.md` rather than in the four sources that agreed.
- **Embeddings moved to story 10a**, with CAP-27's semantic-query clause. Story 2
  therefore creates no Tier-3 artifact, which strengthens `derivation-services.md`'s
  case for running `1h` after story 19 rather than next.
- **Spec ceiling honoured at 1600 tokens**, unlike story 1 where every spec
  exceeded it (measured: 1710-3895 body tokens, median 2821). The cost is spec
  count: eleven thin specs rather than six fat ones.

Two defects the sizing pass found, each now owned by the spec that fixes it:

- `disclosure.md` is Tier-1 truth and **absent from `_APPEND_ONLY_KEYS`**
  (`storage_tiers.py:159`), so `write_artifact` would replace the audit ledger
  whole. Verified: `is_append_only(APPLICATION, "disclosure.md")` returns `False`.
  Fixed by `2i`.
- `_ulid()` (`service.py:215`) returns `"evt_" + secrets.token_hex(10)` — random,
  **not** time-sortable, though `ARCHITECTURE-SPINE.md:649` calls these ids
  "sortable by creation time". The fold stays deterministic, but entries sharing
  an `occurred_at` order arbitrarily. Raised as an Ask First in `2f`.

## Story 2 decomposition

`stories.yaml` story 2 ("Event log and disclosure ledger") is implemented as
eleven specs, in this order. Embeddings and semantic query — `vector_index/`,
originally assigned here by `derivation-services.md` — were **deferred to story
10a** by decision on 2026-08-29: the artifact needs the task manager and the job
runner that 10a supplies, and story 2 would otherwise define a job nothing can
trigger. Story 2 therefore creates no Tier-3 artifact at all, which strengthens
the case already made in `derivation-services.md` for running `1h` after story 19.

| Spec | Delivers | Depends on |
|---|---|---|
| `2a-two-clock-bases` | which clock governs which reasoning; an implausible provider timestamp refused | — |
| `2b-flag-implausible-provider-timestamps` | that refusal reaches the persist path as a flag | 2a, 2d |
| `2c-closed-entry-type-enumeration` | two ledger vocabularies named for their subjects: `ObservedEventType` (renamed) and `SelfActionType` | — |
| `2d-one-entry-renderer` | one function producing every ledger line | 2c |
| `2e-retire-the-free-string-append` | `append_event_log` takes a typed entry; every caller migrates | 2d |
| `2f-segment-parser-and-deterministic-fold` | segments read back; fold by `(occurred_at, entry_id)` | 2d |
| `2g-open-and-sealed-segments` | exactly one open segment; sealed months refuse writes | 2e |
| `2h-event-log-accessor` | derivation-services rule 3, over `event_log/` | 2f, 2g |
| `2i-disclosure-ledger-append` | the application-scoped ledger gains a writer | 2d |
| `2j-disclosure-ledger-reads` | AD-17's monthly total and AD-31's period query gain a source | 2i |
| `2k-retrospective-aggregation` | CAP-10's counts by category, as a weekly trend | 2h |
| `2l-payloads-reach-tier-one` | a payload's content reaches the ledger, so a Tier-3 index can be rebuilt from Tier 1 | 2d, 2f |

**2b depends on 2d, not on 2a alone.** Recorded here because the review of
2026-08-29 found the original ordering had 2b writing a flag into a line format
that 2d then replaces — the work would have been done twice and the two golden
tests would have disagreed.

## Wave 1 decomposition

The seventeen specs of the prototype path's first wave, under
`_bmad-output/specs/spec-pm-ai/stories/`, in build order. Unlike stories 1 and
2 these do not decompose one story: they select slices of stories 4, 8, 11, 22,
23 and the new 33. Full rationale in `prototype-path-2026-09-01.md`.

**Revised 2026-09-02 after a three-lens review** (`review-wave-1-2026-09-02.md`,
140 findings). Every spec is at `review_loop_iteration: 1` and carries a change
log recording what the review changed. The wave grew from twelve slices to seventeen, and to nineteen on 2026-09-03 when first-run setup was requested: `config.toml` had a reader and no writer, so nothing could configure a machine and `doctor` could not report whether it was configured. Split at the sizing gate into `4g` (writer and probe) and `4h` (the sequence). `4d`, the project registry, was
missing entirely and without it `pm-ai` could not have run once on a clean
machine. The other four are sizing-gate splits: `33b` into fetch and mapping,
`8a` into harvest outcomes and registry, `8c` into declarations and boundary,
and `23a` into sections and scope wall.

| Spec | Delivers | Depends on |
|---|---|---|
| `4a-config-loading` | a reader for the declared `config.toml`, and the refusal keeping the encryption toggle out | — |
| `4b-master-key-enrolment` | `pm-ai key enrol`; the daemon never mints | 1d, 1f |
| `4c-cli-entry-point` | `[project.scripts] pm-ai`, subcommand dispatch, and the exit-code table | 4a, 4b |
| `4d-project-registry` | `pm-ai project add` and `projects.toml`; without it nothing runs on a clean machine | 4a, 4c |
| `4g-config-gains-a-writer` | a writer for `config.toml` and the probe that reports its state | 4a |
| `4h-first-run-setup` | `pm-ai setup` — the ordered first-boot sequence, asserted by a probe report | 4b, 4c, 4d, 4g |
| `8a-honest-harvest-outcomes` | `HarvestResult`'s three outcomes, and coverage derived from what was fetched | — |
| `8d-connector-registry` | the registry two pre-written tests import, and the CAP-35 health probes | — |
| `8b-credential-lifecycle` | `pm-ai connector add`, sealed write first | 4b, 4c, 8d |
| `8c-payloads-declare-untrusted-text` | each payload class declares its untrusted fields, guarded at import | — |
| `8e-sanitization-binds-at-the-boundary` | AD-12 holding where it can be enforced: `ModelPort` accepts only `Sanitized` | — |
| `11a-meeting-records-reach-tier-one` | `MeetingRecords`; retires the in-memory dict | 1a, 1b |
| `33a-graph-device-code-auth` | `GraphAuthPort` and the MSAL adapter | 8b, 8d |
| `33b-graph-calendar-fetch` | `calendarView` paged, throttle-handled, converted to aware UTC, honest coverage | 8a, 33a |
| `33c-graph-calendar-mapping` | rows to Meeting records and ended-meeting events; `ConnectorPort` conformance | 11a, 33b |
| `22a-goal-register` | the register `domain/goals.py` has never had | — |
| `23a-dashboard-sections` | `core.rendering`'s four sections, honest gaps | 22a, 11a |
| `23d-project-render-scope-wall` | `project_scope_datasources` and AD-25's one-directional wall | 23a, 4d |
| `23b-dashboard-pipeline` | `pm-ai dashboard` writing the real file | 4c, 23a, 23d, 33c |

Four skipped tests stop skipping across the wave: the AD-27 taxonomy and AD-34
no-minted-ids checks (`8a`, both importing `pm_ai.connectors.registry`), and
AD-25's personal-store wall (`23a`, importing `pm_ai.core.rendering`). Baseline
to measure against is 638 passed, 27 skipped at `7316178`.

## Wave 1 sizing — measured, and the ceiling revised

Measured 2026-09-02 with `tiktoken` `cl100k_base`, on spec body excluding
frontmatter and the Spec Change Log — the like-for-like basis, since story 2's
gate ran at creation before its own change logs existed.

| Set | Specs | Total | Median | Max |
|---|---|---|---|---|
| Story 2, shipped | 12 | 15,184 | 1,239 | 1,544 (`2c`) |
| Wave 1, revised | 14 | 27,243 | 1,952 | 2,377 (`23a`) |

**Every wave-1 spec exceeds the 1600-token ceiling story 2 set**, except `33b`
after its split. The smallest of the fourteen (1,698) exceeds story 2's largest
(1,544).

Where the weight sits, mean tokens per section:

| Section | Story 2 | Wave 1 |
|---|---|---|
| Frozen intent block | 783 | 1,206 |
| Code Map | 123 | 168 |
| Tasks & Acceptance | 208 | 404 |
| Design Notes | 85 | 148 |
| Verification | 51 | 74 |

The overrun is concentrated in the frozen block and in Tasks & Acceptance — the
I/O matrix rows and the positive acceptance assertions the review demanded. It is
not in the explanatory prose, which barely moved. So trimming cannot fix it.

**Ruling: the ceiling is 2,400 for wave 1, and the 1600 figure is recorded as
calibrated on a different shape of work.** Story 2's slices were narrow domain
changes — one enumeration, one renderer, one parser — carrying four to eight
matrix rows each. Wave 1's are integration slices: a connector, a CLI surface, a
credential lifecycle, each spanning three layers and carrying twelve to
seventeen matrix rows because the review found that many real unhandled paths.

The decisive evidence is `33c`: written from scratch on 2026-09-02 *with the
ceiling in mind*, split off a spec specifically to reduce size, and it still
measures 1,969. A ceiling that a deliberately-scoped fresh slice cannot meet is
measuring the wrong thing.

What the ceiling still buys, and is kept for: `33b` was 2,497 before the split
and genuinely held two failure classes — talking to Graph correctly, and mapping
what came back. The gate caught that, which is the point of having one.

**All three remaining candidates were split by decision on 2026-09-02**, after
the ruling above was recorded. `8a` separated a `domain.harvest` type change from
a `connectors` registry. `8c` separated the domain declarations from the `app`
and `storage` boundary that acts on them. `23a` separated the four section
renderers from `project_scope_datasources`, the AD-25 wall — a seam that turned
out to divide two distinct failure classes, text that misleads a reader and a
privacy leak into a git-committed repository, which is a better argument for the
split than sizing alone.

### After the splits — measured 2026-09-02

Same basis: `tiktoken` `cl100k_base`, body excluding frontmatter and change log.
Rows in build order.

| Slice | Spec | Tokens | Before |
|---|---|---:|---:|
| `4a` | config-loading | 1,698 | 1,698 |
| `4b` | master-key-enrolment | 1,733 | 1,733 |
| `4c` | cli-entry-point | 1,998 | 1,998 |
| `4d` | project-registry | 1,760 | 1,760 |
| `8a` | honest-harvest-outcomes | 1,511 | 2,275 |
| `8d` | connector-registry | 1,553 | *new* |
| `8b` | credential-lifecycle | 2,037 | 2,037 |
| `8c` | payloads-declare-untrusted-text | 1,494 | 2,193 |
| `8e` | sanitization-binds-at-the-boundary | 1,605 | *new* |
| `11a` | meeting-records-reach-tier-one | 1,952 | 1,952 |
| `33a` | graph-device-code-auth | 2,014 | 2,014 |
| `33b` | graph-calendar-fetch | 1,558 | 1,558 |
| `33c` | graph-calendar-mapping | 1,969 | 1,969 |
| `22a` | goal-register | 1,865 | 1,865 |
| `23a` | dashboard-sections | 1,695 | 2,377 |
| `23d` | project-render-scope-wall | 1,652 | *new* |
| `23b` | dashboard-pipeline | 1,827 | 1,814 |

| | Specs | Total | Median | Max | Over 2,400 | Over 1,600 |
|---|---:|---:|---:|---:|---:|---:|
| Story 2, shipped | 12 | 15,184 | 1,239 | 1,544 | 0 | 0 |
| Wave 1, before splits | 14 | 27,243 | 1,952 | 2,377 | 0 | 13 |
| Wave 1, after splits | 17 | 29,921 | 1,733 | 2,037 | 0 | 13 |

`8b` (2,037) is now the largest and sits comfortably inside the ceiling.

**Two things the table shows that are worth keeping in view.** Total grew by
2,678 tokens across three splits — about 890 each, which is the duplicated frame
every split pays for: a second Intent, Boundaries, Code Map and Verification
block. And **thirteen of seventeen are still over the original 1,600**, which the
splits did not change and were never going to: the median moved 1,952 to 1,733
while the count over 1,600 stayed at thirteen. That is the same conclusion the
ruling above reached, now with the splits done as evidence rather than as an
argument.

Decisions taken at the sizing gate (2026-09-02):

- **Slice 0 is a throwaway spike**, not a spec. Device-code sign-in against the
  real tenant and one call each to the three resources. Its only output is an
  answer: whether the tenant permits transcripts at all. A `403
  GraphAccessToTranscriptsDisabled` deletes `33e` and `11b` from the plan and
  has no workaround.
- **Story 3 stays deferred on the correct grounds.** An earlier draft justified
  it with "sanitization already binds at the boundary", which is false — hence
  `8c`. The real ground is that the prototype mutates nothing external and puts
  no model in the path, so nothing harvested reaches a prompt. It becomes a hard
  prerequisite the moment either changes.
- **The critical path is seven slices**: `4a → 4c → 8b → 33a → 33b → 33c → 23b`,
  re-derived 2026-09-02 after the splits. It runs through the CLI rather than the
  key, because `8b` adds `connector add` to the dispatch table `4c` creates.
  Seven slices have no dependencies and can start immediately — `4a`, `4b`, `8a`,
  `8c`, `8d`, `11a`, `22a` — and `8c → 8e` is an island with no dependants in
  this wave.
- **`4c` precedes `4d`.** An earlier draft had the reverse, which was circular:
  `4d` adds `project add` to the dispatch table `4c` creates. It resolves in this
  direction only because `4c` requires `doctor` to work on a machine with no
  registered project.
- **`EXPECTED_SKIPS` is lowered inside the slice that unskips a test** — `8a` by
  two, `23a` by one. `tests/conftest.py:88-104` fails the run when skips fall
  *below* the baseline and demands it be turned in the same commit, so the first
  draft's "skip count falls, no new failures" was self-contradictory in all
  twelve specs.

Known debt this wave takes on, recorded so it is not discovered later:

- **`9a`'s scheduler will be an in-memory timer**, which story 10a explicitly
  forbids ("every job is a queue row per AD-20, never an in-memory timer"). It
  is in wave 2, and its spec must label it temporary.
- **CAP-9 is knowingly unmet in two clauses** — Leadership Notes and the 07:00
  deadline. Both recorded in story 23's queue entry and in `23a`/`23b`.
- **AD-27's versioning clause remains unmet** from story 2, unchanged by this
  wave. `33d` touches the Tier-1 entry format and cannot defer the question to
  story 1i: 1i versions `operational.db`'s table shape, and a field added to a
  markdown line changes no column. The unmade design decision is the entry-grammar
  one recorded below, which has no owner. `8c` was named here in
  error — the sizing-gate split left it declaring untrusted fields and writing
  nothing — and `8e`, which did write the field, stopped: it now derives the
  sanitized copy at the point of use and changes no entry format
  (renegotiated 2026-09-02). So `33d` in wave 2 is where the decision becomes
  unavoidable, on real data.

## Open, raised by story 2f

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/2f-segment-parser-and-deterministic-fold.md`
  summary: `_ulid()` (`pm_ai/storage/service.py:215`) returns `"evt_" + secrets.token_hex(10)` — random, not time-sortable — while `ARCHITECTURE-SPINE.md:649` says these ids are "sortable by creation time". Either the minting gains a time prefix or the spine drops the claim.
  evidence: AD-35's fold is `(occurred_at, entry_id)`, and it is deterministic either way because the id is stable once written — so nothing is broken today. The concrete risk is the `id > cursor` pagination the claim invites (incremental indexing in story 18, oldest-first selection in story 19, paged reads in 2h): with random ids that query silently returns the wrong set rather than failing.
  narrowed 2026-08-29: two arguments first made against a time-sortable id do not hold. It would **not** introduce a third clock — `append_event_log` reads `at` on the line before it mints, so a prefix would re-encode `ingested_at` rather than read a new clock. Its real cost is that `_ulid()` has three call sites and one, the `.part` staging name at `service.py:857`, has no clock in scope. What is also now settled: a time-sortable id would not have delivered arrival order anyway — 48-bit millisecond resolution buckets a fast batch and orders within it by the random tail. Arrival order is file order, and that is now documented on `parse_segment` and tested. So this question is narrowed to one thing only: does anything want `id > cursor` pagination? If not, drop the claim from the spine.

## Surfaced by story 2g, deferred

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/2g-open-and-sealed-segments.md`
  summary: A clock that moves backwards across a month boundary makes every append refuse with `SealedSegment` until wall-clock catches up — for `pm_ai/skills/registry.py`, that lands *after* the skill already executed, so the mutation happened and AD-1's one-entry-per-invocation record is lost rather than merely delayed.
  evidence: The refusal is correct — the alternative is writing into a month compaction may already have summarised and deleted — but its blast radius is not bounded anywhere. An NTP correction of a few seconds across midnight on the 1st is the realistic trigger. Wants either a bounded tolerance for writes just past a boundary, or a quarantine that holds refused entries until the open segment accepts them. Not story 2g's to decide: compaction (story 19) is what makes a sealed segment genuinely unwritable, and until it exists the refusal protects nothing that is happening yet.

## Surfaced by the story-2 code review (2026-08-30)

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/2c-closed-entry-type-enumeration.md`
  summary: AD-27 requires both closed vocabularies to be "versioned so parsers can read historical entries", and nothing implements it. A `GRAMMAR_VERSION` constant was removed by this review because it was written nowhere and read nowhere.
  evidence: Three review layers found it independently. The design choice is unmade: a version field on every line (honest, but a permanent per-record cost on a file meant to be grepped by hand), a per-segment header line (cheap, but the append rule says every line is a record), or a dated table mapping grammar changes to date ranges (free, but only correct if every change is dated and recorded). It becomes real the first time the entry grammar changes after something has written segments — which has not happened, since nothing is deployed.

## Deferred to later stories

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/1a-scope-path-resolver.md`
  summary: Implement `pm_ai/core/rendering.py` so that rendering a project-scope artifact names its data sources without any code path to the personal analytics store, satisfying `test_ad25_project_rendering_cannot_open_the_personal_store`.
  evidence: This is render-time scope isolation, not storage layout. It entered story 1 only because its pre-written test skips on a missing module. It belongs with story 4, the first story that renders project-scope output.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/1a-scope-path-resolver.md`
  summary: Implement `pm_ai/domain/clocks.py`, declaring which of `occurred_at` and `ingested_at` governs due-date reasoning versus sweep reasoning, and rejecting implausible provider timestamps.
  evidence: Declaring the two clock bases belongs with the event log, story 2, where that distinction is acted on. It is separate from passing a clock into StorageService, which stays in 1b because those three system-clock reads are storage's own.

## Surfaced by review, deferred

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/1a-scope-path-resolver.md`
  summary: Artifacts named in `scope-model.md` but absent from `ARTIFACT_TIER` have no resolved path — `projects.toml`, `connectors/`, `logs/`, `private/config.json`, `daily_dashboard.md`, `chat_history/`. Deciding their tiers is an Ask First on the tier table.
  evidence: The resolver's own docstring names `projects.toml` as the registry it depends on, yet the artifact has no layout entry, so the next caller invents its path — the exact failure `UnknownArtifact` exists to prevent. `private/config.json` is needed by story 1f and `chat_library`/`chat_history` by 1e, so this must be settled before those land.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/1a-scope-path-resolver.md`
  summary: `telegram_cache/` is placed at `~/.manager-ai/private/` by `scope-model.md:69`, `storage-contract.md:27` and `ARCHITECTURE-SPINE.md:152,684`, but `tests/architecture/test_domain_invariants.py:201` spells it `~/.pm-ai/private/telegram_cache/state.json`. Story 1e must reconcile, and should build that fixture's paths from the resolver rather than from literals.
  evidence: Three canonical sources agree against one test. The test currently skips, so nothing reports the disagreement; when 1e implements the classifier it would be validated against a path the resolver never returns, and the real location would default to unencrypted.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/1a-scope-path-resolver.md`
  summary: Directory permissions are left to the umask. The enclave, the people directories, the personal enclave and transcript directories should be created at `0700`.
  evidence: The storage contract specifies `0600` for encrypted files but says nothing about the directories holding them. Material described as unreadable by a report's peers is currently created with default permissions. Belongs with story 1f, which owns file modes.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/1a-scope-path-resolver.md`
  summary: Module-level `assert` statements used as consistency guards are stripped under `python -O` / `PYTHONOPTIMIZE`, in `pm_ai/platform/paths.py` (inside `_assert_declarations_agree()`) and pre-existing in `pm_ai/domain/storage_tiers.py`.
  evidence: Both modules rely on import-time asserts to enforce that every artifact has exactly one tier and one home. Under an optimized interpreter those invariants vanish silently. Pre-existing pattern, so it is a codebase-wide decision rather than this story's defect.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/1b-storage-writes-through-the-resolver.md`
  summary: `ScopePathPort` is absent from the architecture spine's port inventory, and `ScopePaths` does not follow the spine's `<Service><Noun>Adapter` naming convention for a port implementation.
  evidence: ARCHITECTURE-SPINE.md:27 enumerates seven ports and :513 sets the adapter naming convention; the new port satisfies neither. The spine is a skill-derived artifact that AGENTS.md warns is re-rendered over hand edits, so this needs a re-run of the architecture skill rather than a manual patch.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/1b-storage-writes-through-the-resolver.md`
  summary: No type checker is configured, so every Protocol in `pm_ai/ports/` is documentation rather than a contract. `[dependency-groups]` holds only pytest and import-linter, and there is no CI.
  evidence: A port and its implementation can diverge with a green suite. Story 1b adds isinstance conformance tests as a partial substitute, but that catches attribute existence only — not signatures, keyword arguments, or return types. Adding mypy or pyright is a repo-wide decision.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/1b-storage-writes-through-the-resolver.md`
  summary: The suite silently drops to 31 skips when `lint-imports` is not on PATH, and the skipped test is the layering contract that the storage/platform sibling design depends on.
  evidence: `tests/architecture/test_layering.py:21-22` skips on `shutil.which("lint-imports") is None`, so `python -m pytest` outside an activated venv reports green while never checking import direction. Pre-existing, and it makes any run count that is not produced by `uv run` untrustworthy.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/1b-storage-writes-through-the-resolver.md`
  summary: Moving off the flattened `<root>/<scope>_<id>/event_log/` layout has no migration, so Tier-1 segments written under the old layout would be orphaned and the daemon would start an empty ledger.
  evidence: No deployment exists and no data is at risk today, so this is correctly out of scope for 1b. It becomes real the moment anything writes segments before story 4 stands up the daemon.

## Deferred from: code review of story-1 branch (2026-08-28)

- `PlaintextCrypto.decrypt` silently returns ciphertext when the debug flag is set over previously sealed files; the envelope carries no magic header to detect it. Debug-only path, documented as never-the-default. [pm_ai/storage/crypto.py:218]
- `schema_version` has no single-row constraint and a non-integer value raises unwrapped; single-writer + WAL makes both remote. [pm_ai/storage/service.py:475]
- The skip ratchet stands down for `pytest .` or absolute-path invocations — it judges only `[]`/`["tests"]` argument spellings. [tests/conftest.py]
- NFR-09's staged-file monthly cleanup is decided in comments but owned by no story; `.part` files are dot-prefixed, hidden from the operator the purge rule serves.
- `_PLACEMENTS_BY_KEY[...]` direct indexing can `KeyError` if a future `FOREIGN_ROOTS` node is declared outside the application tree. [pm_ai/platform/paths.py:614]

## Deferred by decision at the story-1 review gate (2026-08-28)

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/1h-derived-tier-rebuild.md`
  summary: Story 1h (derived-tier rebuild — `pm_ai/storage/reindex.py`, `test_ad3_indexes_rebuild_from_markdown_without_loss` un-skipped) ships as its own follow-up story rather than on the story-1 branch.
  evidence: The story is still `ready-for-dev` with nothing depending on it yet — the derived tier it rebuilds is written by later stories. The decomposition table above lists 1h inside story 1; this entry records the explicit decision (review gate, 2026-08-28) to merge story 1 without it rather than hold a 70-file branch for an independent slice. It remains the next `ready-for-dev` story in the queue.

- source_spec: `_bmad-output/specs/spec-pm-ai/SPEC.md` (constraint: "Everything else … is 600-permissioned and unencrypted")
  summary: Implement 600 permissions for the whole plaintext set — every file `_publish` writes plaintext, `operational.db` at creation, captures, and team-member records — as a follow-up story; today only the two encrypted files and their enclave directories are tightened (0600/0700).
  evidence: `storage-contract.md` makes 600 the load-bearing substitute for the encryption dropped on 2026-08-23, and nothing implements it for the plaintext set (story-1 code review, 2026-08-28). Deferred by decision at the review gate: the change concentrates in the single writer but touches every write path and deserves its own matrix (umask interaction, git-committed project files, sqlite sidecar files) rather than riding a review patch.

## Deferred at the story-4a review gate (2026-09-02)

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4a-config-loading.md`
  summary: `blended_hourly_rate` has no upper bound, so a finite but enormous rate (`1e308`) makes `Meeting.man_hour_cost` return `inf` — the silent propagation `nan` and `inf` were refused to prevent.
  evidence: Reproduced: `load_config(b'blended_hourly_rate = 1e308')` returns `1e+308` and is admissible. The loader refuses `nan`, `inf`, zero and negatives as unusable, so this is the one remaining value class that type-checks, passes the admissibility rules, and still poisons every cost CAP-3 computes. Deferred rather than patched because the ceiling is a policy call — any threshold picked during a review patch would be invented, and CAP-3's own currency assumptions are not yet written down.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4a-config-loading.md`
  summary: `config.toml` is hand-edited (AD-3) with a closed key vocabulary, but no sample file or documentation states the three key names, their types, or their admissible ranges.
  evidence: The vocabulary exists only inside `pm_ai/core/config.py` and this story file, and the loader refuses every unknown key — so a user discovers what the file may say by triggering refusals one at a time. Deferred because the natural home is the operator-facing surface story 4c stands up, not a loader that no caller reaches yet.

## Deferred at the story-4a review gate (2026-09-02), second pass

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/8e-sanitization-binds-at-the-boundary.md`
  summary: Story `11b` wires the real transcript path and owes a confirmation that no path to a model bypasses `ModelPort` — an obligation `8e` hands it and nothing outside `8e` records. `11b` is a wave-2 slice with no spec yet.
  evidence: `run_transcript_ingestion` (`pipelines.py:51,64`) reaches `extract()`, which calls `sanitize` itself and keeps the pair (`extraction.py:36,50-51,63-64`), but reaches `stage_proposal` rather than the harvest path. Under `8e`'s original persist design this was scoped out as uncovered; under the consumer-side design it is covered by the same chokepoint, so what remains for `11b` is narrower — confirming the transcript path reaches models only through the port, not building a second sanitization. `11a` also defers transcript binding to `11b`. Recorded because when `11b` is written the obligation is otherwise discoverable only by re-reading `8e`.

- source_spec: `_bmad-output/implementation-artifacts/review-wave-1-2026-09-02.md` (finding A1)
  summary: `StoragePort` declares neither `read_artifact` nor `write_artifact` while `StorageService` implements both, so the Protocol under-declares its own implementation and nothing typed against the port can reach the single reader or the single writer.
  evidence: Verified at the story-4a review gate: the port declares nine methods (`ports/__init__.py:286-314`) and neither of those two, while `StorageService.write_artifact` sits at `service.py:1022` and `read_artifact` at `:1065`. The review filed this as A1, "blocks implementation". It does not: `Daemon.storage` is typed as the concrete `StorageService` (`wiring.py:38`), so `4c` reading `config.toml` and every other wave-1 caller reach both methods legally, and no wave-1 slice depends on the port for artifact access. It remains a real inconsistency of the kind story 2h fixed when it added the event-log methods to the port for this same reason. Recorded because downgrading it from blocker is exactly how it would otherwise be lost — it appears in no story, and the two methods are the whole of AD-3's tiering contract as far as any future port consumer can see.

