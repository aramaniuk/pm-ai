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
  summary: Module-level `assert` statements used as consistency guards are stripped under `python -O` / `PYTHONOPTIMIZE`, in `pm_ai/platform/paths.py:167-172` and pre-existing in `pm_ai/domain/storage_tiers.py:115,140`.
  evidence: Both modules rely on import-time asserts to enforce that every artifact has exactly one tier and one home. Under an optimized interpreter those invariants vanish silently. Pre-existing pattern, so it is a codebase-wide decision rather than this story's defect.
