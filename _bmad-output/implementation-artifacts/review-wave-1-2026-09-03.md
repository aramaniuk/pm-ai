# Multi-lens review — the eighteen unimplemented wave-1 specs

Run 2026-09-03 against `af0c74a`. Lenses: adversarial, edge-case-hunter,
verification-gap, each in its own agent with no sight of the others.
Content class: docs (behaviour-defining specs). `persistent_facts` resolved
empty — no `project-context.md` exists.

Scope: the sixteen specs at `in-review`, plus `4g` and `4h`, which were approved
on 2026-09-03 without ever having had a lens pass.

**Findings: 25 adversarial, 60 edge-case, 16 verification-gap.** Overlap between
lenses is kept, not deduped — a finding two lenses reached independently is
stronger evidence than either alone, and eleven were reached twice or more.

## Verdict

**The set is not ready to implement.** Eight specs cannot be built as written.
The failure is not distributed evenly: it concentrates in the seven specs that
postdate the 2026-09-02 review (`4d`, `8a`, `8c`, `8d`, `8e`, `23a`, `23d`,
`33b`, `33c`) and in the two approved without review (`4g`, `4h`).

The prior review's pattern repeats exactly: **the specs are accurate wherever
they cite something the author opened, and wrong wherever they reason from the
architecture documents.** `4d` — the spec the last review's own fix created —
names two APIs that do not exist.

Two findings from the last review were fixed by moving the fix rather than the
edge, and both re-broke: A1's port methods (`11a` still depends on `8b` with no
recorded edge) and C1's skip counter (`23a` un-skips the test whose fix lives in
`23d`).

## A. Cannot be built as written

**A1. `4d` names two APIs that do not exist.** `ScopePaths.real()` — cited three
times — is `ScopePaths.production()` (`paths.py:459`; `wiring.py:85-86` gets it
right). `projects_registry()` is the `project_registry` property
(`paths.py:638-640`), singular and not callable. *Adversarial.*

**A2. `4d` and `4h` cannot obtain storage on the machine they exist for.**
`build()` resolves the project scope eagerly (`wiring.py:124-129`) and raises
`UnknownProject` for an unregistered id, so `pm-ai project add` and `pm-ai setup`
cannot get a `StorageService` before the registry they write exists. No spec
names an application-scope-only write route. *Edge-case, twice.*

**A3. `4d`'s git-repository check has no route.** Only git can answer it
(`VcsPort`), `Daemon` has no `vcs` field, `build()` constructs `GitVcs()` inline
at `wiring.py:141` for storage only, and `core`/`surfaces` are both forbidden
`subprocess` (`.importlinter:190-204`). The matrix row is unimplementable, so
registration accepts a non-repository and story 1c's guard then refuses every
capture for that project. *Adversarial + edge-case.*

**A4. `8b`'s live credential probe cannot live where it is specified.**
`core-is-io-free` (`.importlinter:31-45`) forbids `httpx`, `requests`,
`aiohttp`, `urllib`, `socket` and `subprocess` in `pm_ai.core`, and no task
declares a port for the probe. `8d`'s probes are legal because they sit in
`pm_ai/connectors/`; `8b`'s cannot. This is the prior review's A3 recurring.
*Adversarial.*

**A5. `8d` has no interface to probe or sample.** `ConnectorPort` declares
`name`, `system`, `emits`, `harvest` — no probe method and no `sample_events()`.
`sample_events` appears exactly once in the repo: the AD-34 test that calls it
(`test_domain_invariants.py:487`). `GitLabConnectorAdapter` has neither. So four
probe matrix rows have no interface, and the AD-34 gate raises `AttributeError`
rather than passing. *Edge-case + verification-gap.*

**A6. `8d` cannot reuse `doctor`'s `Probe`/`Health`.** `pm_ai.connectors` and
`pm_ai.platform` are independent siblings that may not import each other
(`.importlinter:22`). Reuse fails `lint-imports`, which `8d`'s own Verification
asserts passes; redefinition creates a second `Health` and `doctor.run_all` can
never aggregate connector probes. *Adversarial.*

**A7. `33c` has no carrier for the records it must write.** `HarvestResult`
holds events, cursor and coverage only (`harvest.py:34-40`), and
`pm_ai.connectors` may not import `pm_ai.storage`. Nothing carries a `Meeting`
record out of a connector, and no task widens a signature. `Meeting` also has no
field for the tentative and stale states two matrix rows require. *Edge-case.*

**A8. `23d`'s `project_scope_datasources` cannot do its job from `core`.**
`pm_ai.core.rendering` may not import `pm_ai.platform.paths`
(`.importlinter:19-25`), so it can neither resolve a project tree nor raise
`UnknownProject`. *Edge-case.*

## B. Would ship something wrong or unsafe

**B1. `8e`'s central claim is false: `Sanitized` is forgeable.** It is a public
frozen dataclass with two `str` fields and no validation
(`core/sanitize.py:22-31`), so `ModelPort.complete(Sanitized(raw=t, for_model=t))`
type-checks while bypassing `sanitize()` entirely. "Unable to reach a model by
construction" holds only against a bare `str`. *Adversarial + edge-case.*

**B2. `8e`'s claim that the chokepoint covers the transcript path is wrong.**
`Extraction` declares `raw: str` and `for_model: str` (`extraction.py:22-30`),
so the one path that does sanitize today flattens the type at its first hop, and
`pipelines.py:63` sends `ex.detail['rest']` outbound unsanitized. The change log
records the opposite. *Adversarial + edge-case.*

**B3. `4g`'s `WARNING` makes `doctor` exit 4 forever, and `4h`'s exit-0
criterion unmeetable.** `Report.healthy` requires every probe `OK`
(`doctor.py:108-115`) and `4c` exits `4` on any unhealthy probe. An operator who
declines `pm_handle` — which `4a` and `4g` both call a legitimate unset state —
makes `setup` exit `4`, while `4h`'s matrix records that path as unremarkable
and its criterion demands exit `0`. *All three lenses.*

**B4. `read_artifact` has no absent case, and three specs need one.** It ends in
`path.read_bytes()` and raises bare `FileNotFoundError` (`service.py:1065-1079`)
with no `bytes | None` form. So the first `pm-ai doctor`, `config show`, `setup`
and `connector add` on a clean machine each raise out of the command that exists
to survive a broken machine. *Adversarial + edge-case, twice.*

**B5. `4b`'s enrolment race has no mechanism.** `KeychainPort.store` is
specified as "replacing any previous value" (`ports/__init__.py:186-190`); no
conditional-on-absent primitive exists and no task adds one, so the matrix row's
loser overwrites the winner's key — destroying every previously sealed artifact.
*Edge-case.*

**B6. `4b`'s `AES_KEY_BYTES` move breaks `test_cipher.py` at collection.** It is
imported by name at `tests/architecture/test_cipher.py:38-39` and used at six
sites. A bare move lands the slice red on a whole module, and the fix under
pressure is the duplicate literal the task exists to prevent. *Adversarial.*

**B7. `8c`'s `PipelinePayload` row contradicts `8c`'s own guard.**
`PipelinePayload` has two `str` fields, `pipeline_id` and `status`
(`events.py:127-130`), so it cannot be the exemplar of "a class with no text at
all". Every one of the eight payload classes has at least one `str` field.
`MessagePayload.channel` and seven other provider-supplied fields appear in no
row. The implementer resolves the contradiction by weakening the guard, which
reinstates the original bug. *All three lenses.*

**B8. `22a`'s Intent is factually wrong about current behaviour.** With an empty
register a cited recommendation **raises `UnresolvedGoal`** (`goals.py:90-95`);
`UNALIGNED` is returned only when nothing is cited. `23a` and `23b` are
specified against a "degrades to UNALIGNED" model, so `23b`'s "a missing input
is a stated section, not a failure" is unimplementable as written. *Adversarial.*

**B9. `23a` asserts "3-Tier means `GoalHorizon`" against code that says the
opposite.** `alignment_tag`'s docstring (`goals.py:99-104`) states "the tier is
the goal's DOMAIN, matching §2.1" and cites the same spec section. Two files
would assert incompatible meanings for one phrase in the artifact the PM reads
daily. *Adversarial.*

**B10. `11a` and `8b` both need a collection listing that does not exist.**
`for_day` must enumerate `meetings/` and `8b`'s orphan-aware duplicate check
must enumerate `connectors/`; `StoragePort` has no listing method and `8b`'s
task adds only `write_artifact`/`read_artifact`. *Edge-case, twice.*

**B11. `11a` and `33c` contradict each other on overwrite.** `11a`'s matrix says
a second write replaces the record; `33c` says it never silently overwrites a
hand-edit — and the accessor is `33c`'s only route. *Edge-case.*

**B12. `8b` orphans a credential on a `$HOME` that is a git repository.**
`connectors/` is `gitignored=True` (`scope_model.py:451`), so
`_assert_git_excludes` raises `UnprotectedCaptureDir` (`service.py:768`) on the
second write — after the sealed credential is already stored, on every attempt.
*Edge-case.*

**B13. `8b`'s `mode is not None` chmods every parent to 0700.** `_publish`
treats a non-None mode as enclave (`service.py:941-953`), including `~/.pm-ai`.
*Edge-case.*

**B14. Nothing loads `connectors/` at startup, so "active at the next start"
never becomes true.** Neither `8b` nor `8d` reads the directory anywhere.
*Edge-case.*

**B15. Concurrency is unhandled in three read-modify-write paths.**
`projects.toml` (`4d`, `4h`), the sealed credential store (`8b`) and
`config.toml` (`4h`) all use `os.replace` with no exclusivity
(`service.py:1002`), so the last writer wins and a registration or credential is
silently lost. *Edge-case, three times.*

**B16. `4h`'s TTY check is specified after enrolment.** "Writes nothing" is
asserted on files only, so a key can be minted into the keychain before the
refusal. *Edge-case.*

**B17. Exit codes `2` and `3` are assigned to the same condition by different
specs.** A non-TTY stdin is `2` in `4h` and `3` in `8b`, and `4c` defines `2` as
usage and `3` as refusal — so `4h` disagrees with the table it says it reuses.
`pm-ai setup || alert` and `pm-ai connector add || alert` then diverge on one
cause, which is the unobservability the table was declared to end.
*Adversarial + verification-gap.*

**B18. `8a`'s coverage clause names two clocks.** It requires `start` derived
from returned rows *and* expressed in `ingested_at`, which storage assigns
(`events.py:171`). `33b` carries the same conflict. The implementer picks the
provider clock and reinstates AD-35's defect. *Edge-case, twice.*

**B19. A failed harvest is indistinguishable from an empty one after the process
exits.** Both persist as "no coverage" (`service.py:1363-1369`), `PersistResult`
has no failure field, and `evaluate_commitment`'s required `harvest_failed`
(`lifecycle.py:174-180`) has no source — so a dead token reads as patience.
*Edge-case.*

**B20. `33b`'s timezone handling breaks on Windows zone ids.** Graph returns
e.g. `"W. Europe Standard Time"`; `ZoneInfo` raises `ZoneInfoNotFoundError`, and
the matrix routes it to `ImplausibleTimestamp` — flagging every event in the
tenant as a clock fault. *Edge-case.*

**B21. `33b` follows `@odata.nextLink` with no bound and no origin check.**
*Edge-case.*

**B22. `8c`'s type check cannot work as specified.** `events.py:11` has
`from __future__ import annotations`, so `field.type` is the string
`"str | None"`; `Optional[str]` reads as non-text and is wrongly refused.
*Edge-case.*

**B23. `22a`'s citation-safety mechanism catches half its stated cases.**
`SourceRef.parse` checks only segment count and non-emptiness
(`identity.py:26-31`), so `goal:my id` parses happily. *Adversarial.*

**B24. `4h` has no path for an inadmissible answer.** `Config.__post_init__`
raises `ConfigRefused`, which escapes as a traceback after the key and project
are already done. Nor is a changed answer on a second run covered — a new
project id means files *do* change, and `4d` refuses a moved path outright.
*Edge-case, twice.*

**B25. `4g`'s escape row is too narrow.** `Config.__post_init__` admits
`"a\nb"`, so an unescaped control character makes the file unparseable by its
own loader. `verbose_logging` appears in no row at all. *Adversarial + edge-case.*

**B26. `4g` cannot distinguish unreadable from absent.** `bytes | None`
conflates EACCES, a directory and a device with a genuine first run, and reports
`ABSENT` with "ordinary first run" as the remedy. *Edge-case.*

## C. Verification that would not catch a wrong implementation

**C1. `23a`'s declared verification cannot pass.** Creating
`pm_ai/core/rendering.py` ends the skip on
`test_ad25_project_rendering_cannot_open_the_personal_store`, but
`project_scope_datasources` is `23d`'s deliverable and `23a` precedes it.
Verified by creating a stub: `AttributeError`, 1 failed — and the skip ratchet
does not fire because `conftest.py:78-80` returns early on failure. C1's fix was
moved to `23d`; the un-skip happens in `23a`. *Verification-gap.*

**C2. `4g`'s sixth probe breaks four existing `test_doctor.py` assertions.**
Three assert `len(report.probes) == 5`, one asserts the exact name set, and one
asserts `report.healthy`. `4g`'s only test task is additive and its Verification
claims "no new failures". The `run_all` signature change is also observed by
five call sites, `doctor.main()` and a subprocess test. *Verification-gap.*

**C3. `EXPECTED_SKIPS` is order-coupled across `8d` and `23d` and neither says
so.** The ratchet fails in both directions (`conftest.py:81`), and `8d` hard-codes
"is 25", false if `23d` lands first. The wave table also still says "four skipped
tests" while listing three, attributed to the pre-split owners.
*Adversarial + edge-case + verification-gap.*

**C4. `8d`'s central criterion has no task touching the tests it is about.** The
non-emptiness assertion belongs in `test_domain_invariants.py:98,485`; `8d`'s
Execution list names only its own new files. So both architecture gates still
assert nothing over an empty registry — the exact defect C2 named, surviving.
*Verification-gap.*

**C5. `33c`'s port-conformance expectation cannot be met by anything `33c`
changes.** `test_adapters_satisfy_the_ports_they_are_declared_against`
(`:793-826`) never enumerates connectors, so it passes before `33c` starts.
*Verification-gap.*

**C6. `33c`'s all-day duration criterion is self-referential.** "Equals the
stated convention" while the convention is an open `Ask First` — any constant
passes, including the 24-hour answer the spec calls wrong by an order of
magnitude. C12's shape survived the rewrite. *Verification-gap.*

**C7. `4b`'s hoist has no check for the duplication it exists to prevent.** A
copy rather than a move leaves two `AES_KEY_BYTES = 32`; both are 32, so every
length assertion passes. The name has the right property asserted
(`MASTER_KEY_NAME` exists once); the length does not. *Verification-gap.*

**C8. Both remediation retargets are verified by one substring that passes
either way.** `test_doctor.py:123` asserts `"Enrol" in probe.remediation`, which
holds whether the text names `pm-ai key enrol`, `pm-ai setup`, or no command at
all. Neither `4b` nor `4h` carries a criterion or a task for that file.
*Verification-gap.*

**C9. `33b` requires the `Prefer` header and verifies only the conversion.** All
four criteria observe emitted rows; a fetcher that never sends the header passes
every one. *Verification-gap.*

**C10. Four of `8e`'s ten matrix rows cannot fail.** Its Never says the slice
declares the port and stops, so no point of use exists — the injection rows only
re-assert `sanitize()`'s current behaviour, already covered by
`test_ad29_…:139-152`. And its deletion criterion greps `getattr`, so
`sanitize(event.payload.message)` — still discarding a pure function's return —
passes. *Verification-gap.*

**C11. `4g`'s encryption-family grep cannot fail.** `render_config` takes a
`Config` whose three fields cannot produce a matching key. Its only realistic
failure is a false positive against `4g`'s own required header.
*Verification-gap.*

**C12. `4g`'s enumerated round trip cannot observe an emitted
`verbose_logging = false`.** The loader refuses an explicit unset `pm_handle` and
an explicit zero rate, but `_flag` accepts any boolean — so a renderer that
always emits the flag round-trips equal in all eight combinations while
producing exactly the file `4a`'s refusals exist to prevent.
*Verification-gap.*

**C13. `4h`'s "all-probes-green" target is unreachable in this repo.**
`packages_installed()` is FAILING by design and `test_doctor.py:305-317`
documents why; `run_all` also constructs `MacOSKeychainAdapter` when passed none,
and `keyring` is absent. Verified by running the module: two FAILING probes, "pm-ai
is NOT healthy". *Verification-gap.*

**C14. `22a`'s `lint-imports` criterion cannot fail.** `core-is-io-free` already
forbids every client it names, and the way a parser actually does I/O — a bare
`open()` — imports nothing. C13's finding recurring. *Verification-gap.*

**C15. `4c` is the least type-checked module in the wave.** `dispatch` may not
import `pm_ai.app`, so it cannot name `Daemon` and the parameter is implicitly
`Any` — the defect story 1k removed. `4c`'s Verification runs neither
`uv run mypy` nor a full `uv run pytest -q`, the only two commands that would
notice. *Adversarial.*

**C16. `4c`'s config-probe row and criterion belong to `4g`.** No `4g → 4c` edge
is recorded, so `4c` ships with a criterion nobody can evaluate at its own
checkpoint — the C7 defect the review fixed in `4b`. *Adversarial.*

**C17. `8a`'s existing coverage assertion will fail.**
`tests/slice/test_vertical_slice.py:96-103` asserts
`start <= NOW - 30min <= end`, which breaks once the window is the real fetch
range. No task updates it, and `8a` claims "no new failures". *Edge-case.*

## D. Recorded but not this review's problem

**D1. Four `Ask First` clauses are answered in the paragraph that defers them**
— `4c` (argparse), `4g` (comments), `4h` (connector add), `8e` (sanitize move).
They should be `Never` with their existing reasons, or a reader learns that
`Ask First` sometimes means "already decided". *Adversarial.*

**D2. Three `Ask First` clauses defer the slice's primary deliverable.** `11a`'s
is the record format while its first task is the parser and renderer; `22a`'s is
the grammar while its only task is the parser; `33c` carries two, one of which
blocks a criterion. `22a` and `11a` between them gate `23a`, `23d`, `23b` and
`33c` — the entire back half of the wave. *Adversarial.*

**D3. Four dependency edges the specs assert are absent from the table.**
`11a`→`8b` (port methods), `8e`→`8c` (field names), `4c`→`4g` (the probe), and
`8b`'s claim that `22a`/`23b` depend on it is false. The table is what the build
order is read from. *Adversarial.*

**D4. Consolidated citation drift.** `4g` cites `_APPEND_ONLY_KEYS` to
`scope_model.py:432`; it is `storage_tiers.py:163`. `8a` cites `gitlab.py:69`
for a comment at `:65`. `4c` says "both AD-30 contracts"; there are three.
`22a` cites a docstring for inline comments at `goals.py:36-38`. Individually
harmless, collectively corrosive — a reader who checks one and finds it wrong
stops checking, which is how `ScopePaths.real()` survived into a spec created by
a review. *Adversarial.*

## What this review does not say

No lens challenged the wave's shape, the slice boundaries, or the build order.
`8e`'s negative type assertion was verified empirically to work: a fixture under
`tests/` passing a bare `str` produces `arg-type` under an explicit
`uv run mypy <path>`, which overrides `files = ["pm_ai"]` while staying
invisible to a bare `uv run mypy`. Several of the prior review's fixes hold —
C4, C5, C6, C7, C9/C10, C11, and C1 for `8d` and `23d`.
