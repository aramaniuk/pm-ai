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

**2b depends on 2d, not on 2a alone.** Recorded here because the review of
2026-08-29 found the original ordering had 2b writing a flag into a line format
that 2d then replaces — the work would have been done twice and the two golden
tests would have disagreed.

## Open, raised by story 2f

- source_spec: `_bmad-output/specs/spec-pm-ai/stories/2f-segment-parser-and-deterministic-fold.md`
  summary: `_ulid()` (`pm_ai/storage/service.py:215`) returns `"evt_" + secrets.token_hex(10)` — random, not time-sortable — while `ARCHITECTURE-SPINE.md:649` says these ids are "sortable by creation time". Either the minting gains a time prefix or the spine drops the claim.
  evidence: AD-35's fold is `(occurred_at, entry_id)`, and it is deterministic either way because the id is stable once written — so nothing is broken today and 2f shipped without deciding. What is wrong is the spine: entries sharing an `occurred_at` order arbitrarily rather than by arrival, and the next component to read the spine's claim and rely on it will be wrong. Note that a time-sortable id would need the injected clock inside minting, which introduces a third clock into a codebase where AD-35 already assigns arrival-order reasoning to `ingested_at`.

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
