---
title: 'Connector credential lifecycle'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
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
- **`connectors/<name>.json` needs a mode mechanism, not an assertion.** `_replace` passes `mode=ENCRYPTED_FILE_MODE if sealed else None` (`service.py:880,903`), and this artifact is declared unencrypted, so today it lands at the umask — typically 0644. A declared restricted mode is added to storage; the caller still decides nothing.
- **Registration is construction-time**, per `8d`. `pm-ai connector add` does not register into a live registry, and its success message says the connector becomes active at the next start.
- **A live probe runs before anything is stored**, within CAP-35's 10-second bound, so a bad credential is refused while the human is present rather than discovered by a silent harvest at 03:00.
- **The credential never appears in output.** Not echoed at the prompt, not logged, not in a traceback, not in the stored connector configuration.
- **Refusal exit codes come from `4c`'s table** — `3` for a refusal — and this slice may not add to it.

**Ask First:** `pm-ai connector disable`, and hot registration into a running radar. Both are CAP-35 clauses; disable needs a poller to halt and hot registration needs a daemon, neither of which exists before `4d`/`9a`.

**Never:** No connector-specific auth logic — device-code flow is `33a`, and this story must work for a plain token too. **No credential written outside `write_artifact`, and no credential in the unencrypted `connectors/` artifact.** A run with `PM_AI_DISABLE_ENCRYPTION` set writes the sealed store in plaintext by the operator's explicit choice, announced at `wiring.py:174-179`; enrolment neither prevents nor re-checks that — see the Change Log for why the absolute form of this clause was withdrawn.

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
| Permissions | after a successful write | `connectors/<name>.json` is mode 600 | asserted, not assumed |

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
- [ ] `pm_ai/ports/__init__.py` -- declare `write_artifact` and `read_artifact` on `StoragePort` -- it has neither today (`:286-314`), and this slice's `core` module cannot do artifact I/O without them; story 2h added the event-log methods for exactly this reason. `11a`, `22a` and `23b` all depend on this
- [ ] `pm_ai/storage/service.py` -- honour a declared restricted mode for unencrypted-but-gitignored artifacts, so `write_artifact` sets 600 from the declaration
- [ ] `pm_ai/core/connector_enrolment.py` -- add `enrol_connector(...)`: probe, read-modify-seal, then configure -- the order is the story
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- add `connector add`, prompting without echo
- [ ] `tests/core/test_connector_enrolment.py` -- the matrix, with the refusal cases asserting **zero** writes

**Acceptance Criteria:**
- Given the master key is absent, when enrolment runs against a provider that would have passed its probe, then no file exists in `connectors/` afterwards — asserted on the filesystem, which is the only assertion that proves the ordering rather than describing it.
- Given a successful enrolment, when `connectors/<name>.json` is read, then it contains no credential material and its mode is 600.
- Given a probe that rejects the credential, then neither the sealed store nor `connectors/` is touched.
- Given a sealed store already holding one connector's credential, when a second is enrolled, then both are readable afterwards — asserted on the decrypted contents, because `write_artifact` replaces whole and the obvious implementation loses the first.
- Given a successful enrolment, when the command's output and every raised traceback are searched, then the credential appears in neither — the original criteria checked only the stored file.
- Given `stat` on `connectors/<name>.json` after a real write to a temporary root, then the mode is 600 — asserted on the filesystem, since the mechanism is new in this slice rather than inherited.

## Spec Change Log

- **2026-09-02, multi-lens review.** One data-loss defect, one absolute that was false, and a missing interface.
  **Enrolling a second connector would have destroyed the first's credential.** `private/config.json` is one sealed file and `write_artifact` replaces whole; the slice specified a write with no matrix row for an existing occupant. Now read-modify-write, asserted on decrypted contents.
  **`StoragePort` declares neither `write_artifact` nor `read_artifact`** (`ports/__init__.py:286-314`). This slice's `core` service, plus `11a`, `22a` and `23b`, all assumed they were there. Declaring them is now a task here, as the earliest slice that needs them — the same move story 2h made for the event-log methods. Without it the implementer's only routes were importing `pm_ai.storage` from `core` or typing the dependency `Any`, the defect story 1k removed.
  **The 600 assertion had no mechanism.** `_replace` passes a mode only when the artifact is sealed (`service.py:880,903`) and `connectors/` is declared unencrypted, so the file would have landed at the umask while the criterion asserted 600 — met only by a `chmod` in the enrolment code, which this slice's own Always forbids.
  **"No plaintext credential on disk under any circumstance" was false** and withdrawn. With `PM_AI_DISABLE_ENCRYPTION` set, `build()` installs `PlaintextCrypto` (`wiring.py:114-115,153-155`), so the clause's own mandated mechanism produces what it forbids. Restated as something the mechanism can keep. An absolute known to be false teaches the reader these clauses are aspirational, which is the `8e` defect in miniature.
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
