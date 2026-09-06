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
log recording what the review changed. The wave grew from twelve slices to seventeen, and to nineteen on 2026-09-03 when first-run setup was requested: `config.toml` had a reader and no writer, so nothing could configure a machine and `doctor` could not report whether it was configured. Split at the sizing gate into `4g` (the writer) and `4h` (the sequence), and `4g` split again on 2026-09-03 once the second review's findings were applied, leaving `4i` for the probe. `4d`, the project registry, was
missing entirely and without it `pm-ai` could not have run once on a clean
machine. The other four are sizing-gate splits: `33b` into fetch and mapping,
`8a` into harvest outcomes and registry, `8c` into declarations and boundary,
and `23a` into sections and scope wall.

| Spec | Delivers | Depends on |
|---|---|---|
| `1n-project-artifacts-go-machine-local` | four project artifacts and `memory/` become `gitignored`; the only code change in the wave's spec set | — |
| `4a-config-loading` | a reader for the declared `config.toml`, and the refusal keeping the encryption toggle out | — |
| `4b-master-key-enrolment` | `pm-ai key enrol`; the daemon never mints | 1d, 1f |
| `4c-cli-entry-point` | `[project.scripts] pm-ai`, the dispatch and exit-code tables, and `doctor` | 4a |
| `4j-cli-service-subcommands` | `key enrol`, `config show`, `connector check` — three leaves on `4c`'s table | 4b, 4c, 8d |
| `4d-project-registry` | `projects.toml` parsed, rendered, read by `build()`, and reported by `doctor` | 4a |
| `4k-project-onboarding` | `pm-ai project add <path> [alias]` — creates the tree, generates `.gitignore`, adopts an existing one | 1n, 4c, 4d |
| `4g-config-gains-a-writer` | a writer for `config.toml` and the probe that reports its state | 4a |
| `4i-config-doctor-probe` | the sixth `doctor` probe, reporting what state `config.toml` is in | 4a |
| `4h-first-run-setup` | `pm-ai setup` — the ordered first-boot sequence, asserted by a probe report | 4b, 4c, 4g, 4i, 4k |
| `8a-honest-harvest-outcomes` | `HarvestResult`'s three outcomes, and coverage derived from what was fetched | — |
| `8d-connector-registry` | the registry two pre-written tests import, and the per-connector health probes | — |
| `8f-storage-port-capabilities` | `StoragePort` declares artifact I/O and a collection listing; a declared file mode | — |
| `8b-credential-lifecycle` | `pm-ai connector add`, sealed write first | 4b, 4c, 8d, 8f |
| `8c-payloads-declare-untrusted-text` | each payload class declares its untrusted fields, guarded at import | — |
| `8e-sanitization-binds-at-the-boundary` | AD-12 holding where it can be enforced: `ModelPort` accepts only `Sanitized` | — |
| `11a-meeting-records-reach-tier-one` | `MeetingRecords`; retires the in-memory dict | 1a, 1b, 8f |
| `33a-graph-device-code-auth` | `GraphAuthPort` and the MSAL adapter | 8b, 8d |
| `33b-graph-calendar-fetch` | `calendarView` paged, throttle-handled, converted to aware UTC, honest coverage | 8a, 33a |
| `33c-graph-calendar-mapping` | rows to Meeting records and ended-meeting events; `ConnectorPort` conformance | 11a, 33b |
| `22a-goal-register` | the register `domain/goals.py` has never had | — |
| `22b-goal-writer` | `render_goals`, `pm-ai goal set`, and the `goal_set` entry | 22a, 4c |
| `23a-dashboard-sections` | `core.rendering`'s four sections, honest gaps | 22a, 11a |
| `23d-project-render-scope-wall` | `render_project_dashboard` — a separate function whose signature *is* AD-25's wall | 23a |
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
  **Closed 2026-09-03 by slice `8f`**, which declares both methods and a collection listing on the port.
  evidence: Verified at the story-4a review gate: the port declares nine methods (`ports/__init__.py:286-314`) and neither of those two, while `StorageService.write_artifact` sits at `service.py:1022` and `read_artifact` at `:1065`. The review filed this as A1, "blocks implementation". It does not: `Daemon.storage` is typed as the concrete `StorageService` (`wiring.py:38`), so `4c` reading `config.toml` and every other wave-1 caller reach both methods legally, and no wave-1 slice depends on the port for artifact access. It remains a real inconsistency of the kind story 2h fixed when it added the event-log methods to the port for this same reason. Recorded because downgrading it from blocker is exactly how it would otherwise be lost — it appears in no story, and the two methods are the whole of AD-3's tiering contract as far as any future port consumer can see.


## Queued by decision, 2026-09-03 — meeting amendments

Decided in full while triaging the wave-1 review, then queued rather than
specified: the machinery corrects a transcript-derived **summary**, and a summary
needs a model, which the prototype path's decision 2 removes from waves 1 and 2.
`11a` reserves the `## Summary` region and preserves `## Notes`; nothing else
here is buildable until story 7 puts a model in the path and `11b` wires the real
transcript.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/11a-meeting-records-reach-tier-one.md`
  summary: A meeting record's `## Summary` is derived from the transcript **and** an append-only amendment log, and each amendment appends a `meeting_amended` entry to the event log.
  evidence: The PM amends through the CLI or Telegram — text or voice — not by hand-editing, so pm-ai owns every write and there is no concurrent editor to merge against; amendments are records carrying instant, actor and surface, appended and never regenerated, while the summary is re-derived from both so a correction reads correctly rather than sitting below the thing it corrects. CAP-10 requires the event-log entry, and it **cannot** be an `ObservedEventType`: those require a `SourceRef` and `persist_events` dedups on the key derived from it, so a second amendment to one meeting would share the first's key and be silently dropped — the failure `2c` documented when it rejected putting `COMPACTION` there. So `SelfActionType` gains `meeting_amended` and `2c`'s payload registry gains a typed payload for it, under `2c`'s standing guards: disjoint value sets, and no member declarable by a connector. Voice amendments additionally need Whisper (story 7); text amendments do not.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4c-cli-entry-point.md`
  summary: 4c's frozen matrix row 10 names `config show`, a leaf the 2026-09-03 sizing split moved to `4j`; the clause is `4j`'s to satisfy.
  evidence: The row was written 2026-09-02, before the split recorded in the same spec's Change Log. 4c's Never clause and the split entry both assign the three service leaves to `4j`. The row's substantive half — config absence is a first-run state, not an error — is covered and passing by `test_an_absent_config_is_a_first_run_not_an_error` and `test_read_optional_turns_absence_into_a_value`. Human chose to accept rather than renegotiate the frozen block (2026-09-04).

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4c-cli-entry-point.md`
  summary: `Command.run` is `Callable[[Context], int]` and `dispatch` drops every word after the leaf name, so no subcommand can receive an argument — `4j`, `4k` and `8b` all need one.
  evidence: `dispatch.py` resolves `leaf = command.leaves.get(rest[0]) if rest else None` and never passes `rest[1:]` on. 4c is unaffected because `doctor` takes no arguments, but 4c's own probe remedy text tells the operator to run `pm-ai project add <path>`, and `8b` adds `pm-ai connector add`. The signature must gain arguments in `4j`, before the first leaf that needs them. Deliberately not patched during 4c review: a "leaf takes no arguments" guard would harden the wrong assumption.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4c-cli-entry-point.md`
  summary: `python -m pm_ai.platform.doctor` returns 0/1 while `pm-ai doctor` returns 0/4, so exit 1 means both "unhealthy machine" and "pm-ai crashed".
  evidence: Measured on 2026-09-04, same probe results: `uv run pm-ai doctor` → 4, `uv run python -m pm_ai.platform.doctor` → 1. 4c's frozen Always says the exit-code table is declared "here and nowhere else", and 4c edited `doctor.py`'s docstring to say dispatch decides the code, but `doctor.main()` still decides its own. `platform` may not import `surfaces`, so the runner cannot reuse the constants; the fix is to retire `doctor.main()` and its `__main__` block now that a console script supersedes them, which also touches `tests/architecture/test_doctor.py`'s `returncode in (0, 1)` assertion. Escalated to the human at 4c's review checkpoint rather than patched.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4c-cli-entry-point.md`
  summary: `--help` is honoured only in argv position 0, so `pm-ai doctor --help` exits 2 with "takes no arguments" and there is no per-command help.
  evidence: `dispatch` consults `_HELP_FLAGS` once, before the table lookup. 4c's matrix specifies only the bare `pm-ai --help` form, so this is unspecified rather than wrong — but it becomes user-visible as soon as `4j` adds leaves worth asking about.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4c-cli-entry-point.md`
  summary: Two or more registered projects are reported as "the enrolled project cannot be resolved to a directory", with the remedy "Re-enrol the repository" — wrong for an operator whose projects both resolve fine.
  evidence: `_select` raises `UnknownProject`, which `_compose` catches in its `ScopeResolutionError` arm. Unreachable today because `_registered_projects()` returns `{}` until `4d`; `4d` owns the choice policy and should give ambiguity its own probe and remedy.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4c-cli-entry-point.md`
  summary: A malformed `config.toml` discards a daemon that composed successfully, so every non-`doctor` subcommand refuses — including the `config show` that would diagnose it.
  evidence: `_compose`'s `ConfigRefused` arm returns `(None, probe)`, so `require_daemon()` refuses. `4j` owns `config show`; carrying the refusal as a probe while keeping the daemon would make the tool usable at the moment it is most needed.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4c-cli-entry-point.md`
  summary: `_compose` mutates `daemon.config` after `build()` instead of using `build`'s own `config` parameter, which exists for exactly this decision.
  evidence: `build()` declares `config: Config | None = None` and its docstring says the parameter is there so `4c` decides what an unparseable config does to a `doctor` run. Not trivially fixable: `_config()` reads through `daemon.storage`, which `build()` creates, so the seam needs either a pre-`build` read from `paths` or a frozen `Daemon`. Harmless today only because nothing inside `build` reads `config`.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4c-cli-entry-point.md`
  summary: `_compose`'s `OSError` arm blames `~/.pm-ai` for any I/O failure raised anywhere inside `build()` or the config read.
  evidence: Its own test proves it fires for a `StorageService.read_artifact` permission error; it would fire identically for an unreadable project repository, sending the operator to check the wrong directory's ownership. The fix is to name the operation that failed or narrow the `try`.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4c-cli-entry-point.md`
  summary: `test_doctor_runs_the_real_probes_and_prints_them` runs the real `MacOSKeychainAdapter` against the developer's login keychain with no `HOME` redirect.
  evidence: `keychain_reachable` calls `keychain.fetch(MASTER_KEY_NAME)`; `tests/architecture/test_cipher.py` notes this "is a user-visible prompt on some configurations". Latent here only because `keyring` is absent without the `runtime` extra, so the probe reports an incomplete install instead. Left unpatched because keeping the probes genuinely real while isolating custody is a design choice best made in `4b`, where the keychain work lives.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4c-cli-entry-point.md`
  summary: `dispatch` reads the module global `TABLE` while documenting that everything it needs arrives as an argument, forcing tests to monkeypatch the global.
  evidence: Both refusal tests do `monkeypatch.setattr(cli, "TABLE", {**cli.TABLE, ...})`. A defaulted `table=TABLE` parameter would make those tests local and let `4j` test its leaves without patching module state.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4c-cli-entry-point.md`
  summary: Retiring `doctor.main()` left two specs citing it — `4i`'s Code Map names `pm_ai/platform/doctor.py:399 -- doctor.main(), a run_all call site`, and `1g`'s (done) verification commands run `python -m pm_ai.platform.doctor`.
  evidence: `doctor.main()` and the `__main__` block were removed on 2026-09-04 so the exit-code table has one declaration. `4i` is unbuilt and its Code Map will mislead its implementer: the surviving `run_all` call site is now `pm_ai/app/entry.py`'s `_diagnose`. `1g` is done and its probes are unaffected — only the command that reaches them changed, to `pm-ai doctor`. Neither spec was edited here: amending another slice's Code Map belongs to that slice's own build.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4b-master-key-enrolment.md`
  summary: A read-back failure leaves an entry in the keychain that no pm-ai command can remove, so every later `pm-ai key enrol` refuses as already-enrolled.
  evidence: Both post-store branches in `core/enrolment.py` raise after `store_if_absent` succeeded, and `KeychainPort.delete` is never called on that path. Deliberately not auto-removed: on the `stored != key` branch the bytes now held may be another process's key, and deleting them would destroy whatever they already sealed. The messages now name the manual recovery (delete the entry in Keychain Access), but the real fix is a `pm-ai key reset` command with its own confirmation, which no wave-1 slice owns.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4b-master-key-enrolment.md`
  summary: `MacOSKeychainAdapter._add_generic_password` — the ctypes call behind the only conditional write pm-ai makes — is substituted in every test that reaches it, so its real marshalling is never executed.
  evidence: All four tests exercising `store_if_absent` on the real adapter `monkeypatch.setattr(keychain_module, "_add_generic_password", ...)` first. Swapping the service/account argument pair, or dropping `restype = c_int32`, changes no observable in the suite — yet either would write under the wrong key or stop recognising `errSecDuplicateItem`, producing exactly the wedged state recorded above. `store` and `fetch` are verified the other way round, by substituting `keyring` and running the adapter's own code; `store_if_absent` should follow that pattern by faking `ctypes.CDLL`.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/8d-connector-registry.md`
  summary: The two architecture gates call `build()`, which replaces the process-global connector registry, and never restore it.
  evidence: `tests/connectors/test_registry.py` has an autouse `_isolated_default` fixture for exactly this reason; `tests/architecture/test_domain_invariants.py` has none, so whichever of those tests runs last decides what a later reader of `all_connectors()` sees. Latent today because nothing reads it after them; a real leak the moment something does.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/8d-connector-registry.md`
  summary: `ConnectorRegistry.instances()` documents itself as what `doctor` lists without contacting anything, but `doctor.run_all` has no connector-membership probe and `instances()` has no production caller.
  evidence: The stated split — membership in `doctor`, reachability in `connector check` — exists only in the docstring. Either `doctor` gains the probe or the docstring stops promising it.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4j-cli-service-subcommands.md`
  summary: `key enrol` is gated behind full composition though it needs only the keychain, so an unparseable config or an unenrolled project blocks the command that fixes a fresh machine.
  evidence: `entry.main` already builds `MacOSKeychainAdapter()` independently of `_compose`, but `_key_enrol` reaches it via `require_daemon().keychain`. This is the same first-run breakage `doctor` was explicitly designed to survive. `4h`'s setup sequence is where the ordering gets decided.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4j-cli-service-subcommands.md`
  summary: Leaves now refuse trailing arguments, which sharpens the deferred argument-passing gap: `4k` and `8b` must give `Leaf` an arity declaration, not merely pass `rest` through.
  evidence: `dispatch` prints "`<group> <leaf>` takes no arguments" and exits 2 for any word after a leaf, and tests assert it. The refusal is right for 4j's three argument-less leaves — silently dropping an invented `--dry-run` is worse — but `project add <path>` and `connector add` now have a tested branch to change rather than an absent one.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/8f-storage-port-capabilities.md`
  summary: `read_optional` in `pm_ai/app/entry.py` is dead weight now that `read_artifact` returns `bytes | None` — its `except FileNotFoundError` is unreachable through the real service.
  evidence: 8f moved absence-as-a-value into the port, which is where `4c` said it belonged once something declared it. Retiring it touches one call site, one test in `tests/surfaces/test_cli_dispatch.py`, and a by-name reference in this file. Left in place because 8f's task list names five files and `app/entry.py` is not one; its docstring was corrected so it no longer claims to do the translation.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/8f-storage-port-capabilities.md`
  summary: `_append` still writes event-log and meeting segments at the umask, so the declared restricted mode never reaches them.
  evidence: `event_log/` and `meetings/` are declared gitignored in the PEOPLE tree, but ledger appends go through `path.open("a")` rather than `_publish`, which is where `restricted_mode` is consulted. Outside 8f's matrix, and it matters the moment the people enclave's segment modes do.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/8b-credential-lifecycle.md`
  summary: Nothing enforces CAP-35's ten-second bound on the *credential* probe, so a hung provider would hang `pm-ai connector add` while holding the exclusive claim open.
  evidence: `ProbeUnreachable` and `CredentialProbePort` both document a bound; no timeout exists in `connectors/probe.py` or `enrol_connector`, and `ProbeUnreachable` is raised only by test fakes. Unreachable today because every probe refuses before any I/O, and it becomes real the moment 33a wires a transport — which is also the slice that can bound it, since `core` may not own a thread and 8d's registry already holds the one bound this codebase has.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/8b-credential-lifecycle.md`
  summary: A half-enrolled connector cannot be recovered through any shipped command — both refusal messages point the operator at an AES-GCM encrypted file.
  evidence: `DuplicateConnector` says "Remove the entry from the sealed store" and `OrphanedCredential` says "enrol again"; `private/config.json` is encrypted under a keychain-held master key and there is no `connector remove`, no `connector list`, and no way to hand-edit it. The design deliberately reports the orphan rather than rolling back, which is right — but the state it chooses is currently a dead end. A `pm-ai connector remove` belongs with the disable clause 8b's Ask First already defers.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/8b-credential-lifecycle.md`
  summary: A GitLab project containing a slash cannot be enrolled, and `gitlab:<group>/<project>` would silently build an adapter for the wrong path.
  evidence: `_assert_nameable` forbids `/` because the instance becomes one path component of `connectors/`, while `wiring._enrolled_connectors` derives the project as `instance.split(":", 1)[1]`. Real GitLab projects are `group/project`. The coupling — the instance suffix *is* the project path — is undocumented, and 33a will hit the same question for Graph.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/8b-credential-lifecycle.md`
  summary: `_assert_nameable` is applied to `system` but every one of its refusals talks about the *instance* name.
  evidence: `pm-ai connector add "git lab" alpha` produces a message about registry keys, cursors and `connectors/<instance>.json`, naming the wrong argument. The validation is right; only the sentence is.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/8b-credential-lifecycle.md`
  summary: A connector's credential is never read back from the sealed store, so a freshly enrolled connector reports ABSENT.
  evidence: `grep stored_credentials pm_ai/` finds only enrolment's own duplicate check. `_enrolled_connectors` constructs adapters with no credential, so `pm-ai connector check` shows a just-enrolled connector as having none. Harmless while no transport exists; it is the wiring 33a needs.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/8f-storage-port-capabilities.md`
  summary: `assert_writable` promises to ask "every question a write would ask" and asks exactly one.
  evidence: The implementation is a single `_assert_git_excludes` call. It takes no `name`, so it never validates the member name `write_artifact` will, and it checks no directory writability — so an unwritable `connectors/` still orphans a credential on every attempt. Either narrow the docstring or widen the check.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/8f-storage-port-capabilities.md`
  summary: `_append` still writes at the umask, so the declared restricted mode reaches two of the three writers.
  evidence: `disclosure.md` is declared GITIGNORED in the application tree and goes through `append_disclosure` → `_append` → `path.open("a")`, never `_publish`, where `restricted_mode` is consulted. `write_artifact` and `write_capture` both honour the declaration; this one does not, which is the selective enforcement the mode rule was introduced to end.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/8b-credential-lifecycle.md`
  summary: RESOLVED 2026-09-04 — three entries above are closed, ahead of story 33a: the credential probe is bounded, the connector's project is declared rather than derived, and `_assert_nameable` names the argument it judges.
  evidence: Closed on branch `wave-1/pre-33a-deferred` rather than left for 33a, because all three change what 33a builds against. The probe bound reuses `registry.run_bounded` so AD-9's exemption stays one file wide instead of two. The remaining 8b/8f entries above — no `connector remove`, `assert_writable` promising more than it asks, `_append` at the umask, and the credential never being read back — are untouched and still open.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4i-config-doctor-probe.md`
  summary: RESOLVED 2026-09-06 — the `doctor.main()` citations are closed in both specs, and `4i`'s whole Code Map was re-derived rather than only the one line.
  evidence: Every `doctor.py` address `4i` carried had moved by the same 2026-09-04 edit, so fixing only `:399` would have left an implementer at `:377-395` for a `run_all` now at `:331-349` and at `:247-272` for a `keychain_reachable` now at `:201-244`. A second cause compounded it: `Health`, `Probe` and `Report` moved to `pm_ai/domain/health.py` when `ConnectorPort` gained a health method, so the map named the wrong *file* for three types. All re-derived against `10511bc`; the surviving `run_all` call site is `pm_ai/app/entry.py:250`; the four `test_doctor.py` assertions moved to `:292-295,313,468,634`. `1g`'s Verification now says `pm-ai doctor` — measured, `uv run python -m pm_ai.platform.doctor` prints nothing and exits 0, so it had become a step that passes without reproducing anything. `1g`'s acceptance criterion is deliberately untouched: it records a verification performed under the command that existed then. The frozen-block citations were corrected the same day on instruction — see the entry below.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/33a-graph-device-code-auth.md`
  summary: ASSIGNED 2026-09-06 — the credential read-back is now in `33a`'s Code Map, task list and acceptance criteria. Still open in code; no longer open in the plan.
  evidence: This file already said "it is the wiring 33a needs", but `33a`'s spec recorded none of it, so the assignment lived only here and an implementer working from the spec would not have found it. Now cited with the three sites: `connector_enrolment.py:142` (`stored_credentials`, which `8b` built and nothing outside enrolment's duplicate check calls), `wiring.py:256-319` (`_enrolled_connectors`, constructing every adapter with `credential=None`), and `gitlab.py:56-60` (the field comment still saying `8b` owns putting a real credential there — `8b` is done and did not). `app` may import both `core` and `storage`, so the read is reachable from `_enrolled_connectors` and from nowhere lower, and a sealed-store read does not violate `33a`'s frozen Never, which forbids resource fetching.

- source_spec: `_bmad-output/implementation-artifacts/prototype-path-2026-09-01.md`
  summary: RESOLVED 2026-09-06 — the "no tenant-admin consent" claim is corrected in both places it was recorded, and two stale scope counts in `33a`'s frozen block are closed with it.
  evidence: Renegotiated on instruction. Decision 4 of the prototype path and `33a`'s Intent both gave "no tenant-admin consent" as part of the rationale for choosing device code; slice 0 measured the opposite, its grant having come from an administrator. What the delegated flow actually avoids is the app-only path and the application access policy that path requires for transcripts, and that half of the claim is kept. The flow choice stands — admin consent is a one-time app-registration prerequisite, not a per-sign-in step — but a first enrolment now needs all seven declared scopes granted beforehand, and both documents say so. `33a`'s partial-consent matrix row went from "three of four scopes" to "six of seven", and its admin-consent clause stopped restating a count at all: the declared set is stated once, and the two prose counts that had drifted since 2026-09-02 were the reason to stop repeating it.

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/4i-config-doctor-probe.md`
  summary: RESOLVED 2026-09-06 — `4i`'s frozen-block citations are corrected on instruction, and every code citation in the spec is now bounds-checked. The file has no address that does not resolve to what it claims.
  evidence: Renegotiated on instruction; addresses only, no claim in any clause changed. The Intent's `doctor.py:377-395` and the Boundaries' `doctor.py:378-382` both pointed past EOF — `doctor.py` is 349 lines — and are now `:341-349` (the five probes, order unchanged) and `:332-336` (`run_all`'s docstring, verbatim). Two others resolved to real but unrelated code, which is the failure mode worth naming: the Boundaries' `Probe (:96-100)` landed in `required_distributions`, pyproject-metadata parsing in the file the citation named, because `Probe` had moved to `pm_ai/domain/health.py:57-68` when `ConnectorPort` gained a health method; and the Boundaries' `service.py:1065` for `read_artifact` landed in another method's docstring, because `8f` added roughly two hundred lines to `pm_ai/storage/service.py` and moved it to `:1093`. Both read as though the right place had been found. `doctor.py:22-24` was verified still exact and left alone. The old addresses survive in the Spec Change Log, where they are history rather than pointers.

