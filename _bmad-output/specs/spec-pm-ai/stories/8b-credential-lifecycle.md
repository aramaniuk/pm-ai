---
title: 'Connector credential lifecycle'
type: 'feature'
created: '2026-09-02'
status: 'done'
baseline_commit: '9eebc95edd16ed4a78fc501169e3e3558adeddc1'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** CAP-35 requires enrolling a connector without touching code: prompt for a credential, probe it live, store the secret encrypted and the configuration at 600. Nothing does this. A connector's token has nowhere to live, so `33a` has nowhere to put a Graph refresh token, and `8d`'s registry has nothing to register from.

**Approach:** Add the enrolment service: write the credential to the encrypted `private/config.json` **first**, then the connector's configuration to the unencrypted `connectors/` collection. `4c` exposes it as `pm-ai connector add`.

## Boundaries & Constraints

**Always:**
- **The secret is written first.** The master key is fetched lazily, so the encrypted write is the one that can refuse. In that order a refusal leaves nothing behind. The reverse leaves a connector configured, enabled and holding no credential — which reads as a working connector silently harvesting nothing, the worst of the three possible states.
- **Every write goes through `write_artifact`.** The declaration decides whether an artifact is sealed, never the caller (`config.json` is `encrypted=True` at `scope_model.py:484`; `connectors/` is `encrypted=False, gitignored=True` at `:451`).
- **`private/config.json` is read-modify-written, never replaced.** It is one sealed file holding every connector's credential, and `write_artifact` replaces whole — so a plain write while enrolling a second connector destroys the first's token.
- **`connectors/<name>.json` is written at 0600 through `8f`'s declared mode**, not by this slice deciding anything. The caller names what it writes; the declaration decides how.
- **The duplicate check enumerates both stores**, using `8f`'s collection listing — checking `connectors/` alone misses an orphaned credential, and checking the sealed store alone misses a configured connector.
- **Something must load `connectors/` at start, or "active at the next start" never becomes true.** Neither this slice nor `8d` reads the directory today. Loading it at composition is a task here, since this is the slice that writes it.
- **The sealed store's read-modify-write is exclusive.** `_replace` publishes by `os.replace` with no exclusivity, so two enrolments racing destroy one credential — the failure the read-modify-write rule was added to prevent, one level down.
- **Registration is construction-time**, per `8d`. `pm-ai connector add` does not register into a live registry, and its success message says the connector becomes active at the next start.
- **A live probe runs before anything is stored**, within CAP-35's 10-second bound, so a bad credential is refused while the human is present rather than discovered by a silent harvest at 03:00.
- **The probe is injected, never imported.** `core-is-io-free` forbids `httpx`, `requests`, `aiohttp`, `urllib`, `socket` and `subprocess` in `pm_ai.core` (`.importlinter:31-45`), so `enrol_connector` receives the probe as a parameter and `app` supplies it from a `connectors`-layer adapter. `8d`'s probes are legal because they live in `pm_ai/connectors/`; this one cannot be. Without the parameter the only routes are an illegal import — which this slice's own Verification claims passes `lint-imports` — or typing the dependency `Any`, which is what story `1k` retired.
- **The git-exclusion answer is pre-flighted, like the name.** `connectors/` is `gitignored=True`, so `_assert_git_excludes` (`service.py:697-717`) refuses its write when git cannot answer — and on a machine where `$HOME` is a git repository without the rule, that refusal lands on the *second* write, after the sealed credential is already stored. Every attempt would then orphan a credential. The question is asked before the first write, exactly as the instance name is checked before the probe.
- **The credential never appears in output.** Not echoed at the prompt, not logged, not in a traceback, not in the stored connector configuration.
- **Refusal exit codes come from `4c`'s table** — `3` for a refusal — and this slice may not add to it.

**Ask First:** `pm-ai connector disable`, and hot registration into a running radar. Both are CAP-35 clauses; disable needs a poller to halt and hot registration needs a daemon, neither of which exists before `4d`/`9a`.

**Never:** No second `DuplicateConnector`. `8d` declares it in `pm_ai/connectors/registry.py`, which `core` sits below and may not import, so this slice's refusal either reuses a type declared in a layer both may reach or is named differently — two unrelated types with one name is worse than either. No connector-specific auth logic — device-code flow is `33a`, and this story must work for a plain token too. **No credential written outside `write_artifact`, and no credential in the unencrypted `connectors/` artifact.** A run with `PM_AI_DISABLE_ENCRYPTION` set writes the sealed store in plaintext by the operator's explicit choice, announced at `wiring.py:142-143,184`; enrolment neither prevents nor re-checks that — see the Change Log for why the absolute form of this clause was withdrawn.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | valid credential, reachable provider | probe passes, secret sealed, config written at 600; success says the connector activates at the next start | N/A |
| Second connector | sealed store already holds one credential | both credentials present afterwards — read-modify-write, asserted | N/A |
| Killed between writes | SIGKILL after sealing, before configuring | the orphan is detectable on the next run, since no reporting code executed | surfaced at the next enrolment |
| Duplicate check sees an orphan | sealed credential present, `connectors/` entry absent | duplicate is checked against **both** stores, not `connectors/` alone | `DuplicateConnector` |
| Stdin not a TTY | run from cron, a pipe, or CI | refused rather than falling back to an echoing prompt | exit `3` |
| Instance name path-unsafe | `../graph`, a leading dot | refused **before** the probe runs, so no orphan is possible | propagated |
| Keychain unreachable vs backend missing | two distinct machine faults | kept distinct, as `4b` keeps them; nothing written in either case | respective error type |
| Probe fails | credential rejected by provider | refused; **nothing written**, neither half | `ProbeFailed` |
| Probe times out | provider silent past 10s | refused as unreachable, distinctly from rejected | `ProbeFailed` |
| Master key absent | keychain reachable, no key | refused at the encrypted write; nothing written | `KeyNotFound` |
| Encrypted write refuses | sealing fails for any reason | nothing written — the ordering guarantee, asserted | `DecryptionFailed` / propagated |
| Config write fails after secret stored | disk full at the second write | refused, and the orphaned secret is reported so it can be cleaned | surfaced, never silent |
| Duplicate connector | instance already enrolled | refused; existing credential untouched | `DuplicateConnector` |
| Permissions | after a successful write | `connectors/<name>.json` is mode 600, and `~/.pm-ai` is **unchanged** | asserted on both, not assumed |
| First enrolment ever | `private/config.json` does not exist | the absent sealed store reads as an empty mapping, not a failure | `FileNotFoundError` translated, never surfaced |
| `$HOME` is a git repository, git absent | the exclusion question cannot be answered | refused **before** the sealed write, so no credential is orphaned | propagated, exit `3` |
| Two enrolments racing | both read the same sealed store | one wins, the other refuses; **no credential is lost** | propagated |
| Duplicate read with no master key | checking the sealed store on a keyless machine | the `connectors/` half is checked first, so the refusal is about the key and not a spurious duplicate | `KeyNotFound` |
| Restart after enrolment | connector configured, daemon restarted | the connector is registered from `connectors/` — which is what "active at the next start" means | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/core/connector_enrolment.py` -- new, the whole of this story
- `pm_ai/domain/scope_model.py:451,484` -- the two declarations whose different sealing makes the write order matter
- `pm_ai/storage/service.py:1022` -- `write_artifact`, and its `name` parameter for members of a `Collection`
- `pm_ai/connectors/registry.py` -- `8d`'s registry, what a successful enrolment registers into
- `pm_ai/ports/__init__.py:163` -- `KeyNotFound`, the lazy-key refusal this ordering is built around
- `stories.yaml` story 8 -- the write-ordering rule, stated there first

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/core/connector_enrolment.py` -- add `enrol_connector(..., probe)`: probe, read-modify-seal, then configure -- the order is the story, and the probe is a parameter because `core` may not speak HTTP
- [x] `pm_ai/connectors/probe.py`, `pm_ai/app/entry.py` -- the probe adapter and its injection -- `8d`'s probes are legal in `connectors`; this one has the same constraint
- [x] `pm_ai/app/wiring.py` -- load `connectors/` at composition and register what it holds -- otherwise "active at the next start" is never true
- [x] `pm_ai/surfaces/cli/dispatch.py` -- add `connector add`, prompting without echo
- [x] `tests/core/test_connector_enrolment.py` -- the matrix, with the refusal cases asserting **zero** writes

**Acceptance Criteria:**
- Given the master key is absent, when enrolment runs against a provider that would have passed its probe, then no file exists in `connectors/` afterwards — asserted on the filesystem, which is the only assertion that proves the ordering rather than describing it.
- Given a successful enrolment, when `connectors/<name>.json` is read, then it contains no credential material and its mode is 600 — the mode comes from `8f`'s declaration, so this asserts the declaration is right rather than that this slice set it.
- Given a machine with no `private/config.json`, when the first connector is enrolled, then it succeeds — through `8f`'s absence-tolerant read.
- Given a probe that rejects the credential, then neither the sealed store nor `connectors/` is touched.
- Given a sealed store already holding one connector's credential, when a second is enrolled, then both are readable afterwards — asserted on the decrypted contents, because `write_artifact` replaces whole and the obvious implementation loses the first.
- Given a successful enrolment, when the command's output and every raised traceback are searched, then the credential appears in neither — the original criteria checked only the stored file.
- Given a successful enrolment and a fresh composition, then the connector is in the registry — which is the only assertion that makes "active at the next start" mean anything.
- Given `uv run lint-imports`, then `pm_ai.core.connector_enrolment` reaches no HTTP client — which is what the injected probe buys, and what an imported one would have broken while this block claimed it passed.

## Spec Change Log

- **2026-09-03, split at the sizing gate.** Amending this slice against the second review took it to 2906 body tokens against wave 1's 1600. Three tasks were storage capabilities rather than credential lifecycle — declaring `write_artifact` and `read_artifact` on `StoragePort`, the collection listing the duplicate check needs, and the restricted-mode mechanism — and `11a` needs the same listing. They are now `8f`, which also closes the review's A1 for the first time since it was downgraded.

- **2026-09-03, amended against the second multi-lens review.**
  **The live probe was specified inside `core`, where it is illegal** (A4). `core-is-io-free` forbids every HTTP client and `subprocess` in `pm_ai.core`, so the implementer's only routes were an illegal import — which this slice's own Verification claims passes `lint-imports` — or an `Any`-typed dependency, the defect story `1k` retired. The probe is now a parameter, supplied by `app` from a `connectors`-layer adapter, which is the same arrangement that makes `8d`'s probes legal. This is the prior review's A3 recurring in a new place.
  **The duplicate check could not enumerate** (B10). `StoragePort` has no listing method, so an orphan-aware check over `connectors/` was unwritable. One is declared here beside the two artifact methods.
  **The mode fix would have tightened `~/.pm-ai`** (B13). `_publish` treats any non-`None` mode as enclave and chmods every parent to 0700, so the obvious implementation restricts the application root as a side effect and still passes a file-only assertion. The criterion now asserts the parents are unchanged.
  **A git-repository `$HOME` orphaned a credential on every attempt** (B12). `connectors/` is gitignored, so `_assert_git_excludes` refuses its write when git cannot answer — and that refusal lands on the second write, after the secret is sealed. The question is pre-flighted, like the instance name.
  **Nothing loaded `connectors/` at start** (B14), so "active at the next start" could never become true — neither this slice nor `8d` read the directory. Loading it at composition is a task here.
  **The first enrolment ever would have failed** (edge-case): `read_artifact` raises `FileNotFoundError` and the absent sealed store must read as an empty mapping.
  **Two enrolments racing destroyed one credential** (B15) — `os.replace` with no exclusivity, the read-modify-write rule failing one level down.
  **`DuplicateConnector` was about to exist twice**, once here and once in `8d`'s registry, in layers that cannot see each other. Now a `Never`.

- **2026-09-02, `wiring.py` citations re-pointed after story 4a.** 4a added one import to `wiring.py`, shifting every line below it, and a parameter plus a docstring paragraph to `build()`, shifting the rest further. The numbers below named other code. **Line numbers only — no wording, no intent, no task, and no acceptance criterion changed.**

- **2026-09-02, multi-lens review.** One data-loss defect, one absolute that was false, and a missing interface.
  **Enrolling a second connector would have destroyed the first's credential.** `private/config.json` is one sealed file and `write_artifact` replaces whole; the slice specified a write with no matrix row for an existing occupant. Now read-modify-write, asserted on decrypted contents.
  **`StoragePort` declares neither `write_artifact` nor `read_artifact`** (`ports/__init__.py:286-314`). This slice's `core` service, plus `11a`, `22a` and `23b`, all assumed they were there. Declaring them is now a task here, as the earliest slice that needs them — the same move story 2h made for the event-log methods. Without it the implementer's only routes were importing `pm_ai.storage` from `core` or typing the dependency `Any`, the defect story 1k removed.
  **The 600 assertion had no mechanism.** `_replace` passes a mode only when the artifact is sealed (`service.py:880,903`) and `connectors/` is declared unencrypted, so the file would have landed at the umask while the criterion asserted 600 — met only by a `chmod` in the enrolment code, which this slice's own Always forbids.
  **"No plaintext credential on disk under any circumstance" was false** and withdrawn. With `PM_AI_DISABLE_ENCRYPTION` set, `build()` installs `PlaintextCrypto` (`wiring.py:140,179-180`), so the clause's own mandated mechanism produces what it forbids. Restated as something the mechanism can keep. An absolute known to be false teaches the reader these clauses are aspirational, which is the `8e` defect in miniature.
  **Contradiction with the registry slice resolved** in its favour (that rule now lives in `8d`, split from `8a` on 2026-09-02): registration is construction-time and the success message says so.
  The edge-case lens added the non-TTY case, where `getpass` falls back to an echoing prompt and the credential lands in shell history; the orphan-blind duplicate check; and a path-unsafe instance name refused only *after* the probe and seal, guaranteeing the orphan the slice tries to avoid.
## Design Notes

The write order is the entire point of this story, so the tests assert absence of files rather than presence of error messages. An error message proves the code noticed; an empty directory proves the code left nothing behind. Story 5 will need the identical guarantee for `telegram_cache/`, and this is the shape it should copy.

The orphaned-secret row deserves its own handling rather than a rollback attempt: deleting a just-sealed credential to unwind a failed second write means a delete path in the enrolment flow, and a delete that itself fails leaves the same state with more code. Reporting it, so a human can re-run enrolment or clean up, is the smaller and more honest surface.

## Verification

**Commands:**
- `uv run pytest tests/core/test_connector_enrolment.py -q` -- expected: all matrix rows pass
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
