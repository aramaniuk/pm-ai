---
title: 'Connector credential lifecycle'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** CAP-35 requires enrolling a connector without touching code: prompt for a credential, probe it live, store the secret encrypted and the configuration at 600. Nothing does this. A connector's token has nowhere to live, so `33a` has nowhere to put a Graph refresh token, and `8a`'s registry has nothing to register from.

**Approach:** Add the enrolment service: write the credential to the encrypted `private/config.json` **first**, then the connector's configuration to the unencrypted `connectors/` collection. `4c` exposes it as `pm-ai connector add`.

## Boundaries & Constraints

**Always:**
- **The secret is written first.** The master key is fetched lazily, so the encrypted write is the one that can refuse. In that order a refusal leaves nothing behind. The reverse leaves a connector configured, enabled and holding no credential — which reads as a working connector silently harvesting nothing, the worst of the three possible states.
- **Every write goes through `StorageService.write_artifact`.** The declaration decides whether an artifact is sealed, never the caller (`config.json` is `encrypted=True` at `scope_model.py:484`; `connectors/` is `encrypted=False, gitignored=True` at `:451`).
- **A live probe runs before anything is stored**, within CAP-35's 10-second bound, so a bad credential is refused while the human is present rather than discovered by a silent harvest at 03:00.
- **The credential never appears in output.** Not echoed at the prompt, not logged, not in a traceback, not in the stored connector configuration.

**Ask First:** `pm-ai connector disable`, and hot registration into a running radar. Both are CAP-35 clauses; disable needs a poller to halt and hot registration needs a daemon, neither of which exists before `4d`/`9a`.

**Never:** No connector-specific auth logic — device-code flow is `33a`, and this story must work for a plain token too. No plaintext credential on disk under any circumstance, including with encryption disabled by environment: that toggle changes how `write_artifact` seals, and this story does not consult it.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | valid credential, reachable provider | probe passes, secret sealed, config written at 600, connector registers | N/A |
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
- `pm_ai/connectors/registry.py` -- `8a`'s registry, what a successful enrolment registers into
- `pm_ai/ports/__init__.py:163` -- `KeyNotFound`, the lazy-key refusal this ordering is built around
- `stories.yaml` story 8 -- the write-ordering rule, stated there first

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/connector_enrolment.py` -- add `enrol_connector(...)`: probe, seal, then configure -- the order is the story
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- add `connector add`, prompting without echo
- [ ] `tests/core/test_connector_enrolment.py` -- the matrix, with the refusal cases asserting **zero** writes

**Acceptance Criteria:**
- Given the master key is absent, when enrolment runs against a provider that would have passed its probe, then no file exists in `connectors/` afterwards — asserted on the filesystem, which is the only assertion that proves the ordering rather than describing it.
- Given a successful enrolment, when `connectors/<name>.json` is read, then it contains no credential material and its mode is 600.
- Given a probe that rejects the credential, then neither the sealed store nor `connectors/` is touched.

## Design Notes

The write order is the entire point of this story, so the tests assert absence of files rather than presence of error messages. An error message proves the code noticed; an empty directory proves the code left nothing behind. Story 5 will need the identical guarantee for `telegram_cache/`, and this is the shape it should copy.

The orphaned-secret row deserves its own handling rather than a rollback attempt: deleting a just-sealed credential to unwind a failed second write means a delete path in the enrolment flow, and a delete that itself fails leaves the same state with more code. Reporting it, so a human can re-run enrolment or clean up, is the smaller and more honest surface.

## Verification

**Commands:**
- `uv run pytest tests/core/test_connector_enrolment.py -q` -- expected: all matrix rows pass
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
