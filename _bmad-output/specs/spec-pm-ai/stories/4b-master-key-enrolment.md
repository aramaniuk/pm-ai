---
title: 'Master-key enrolment'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 1d built key custody behind `KeychainPort` and 1f made encrypted writes depend on a master key fetched lazily, so a clean install boots fine and refuses the first encrypted write. `doctor.py:64-72` already reports this as `ABSENT` — "reachable, nothing stored", an ordinary first-run state — and states plainly that encrypted writes will be refused until the key is enrolled. No command exists to leave that state. The key must be configured before pm-ai is first useful, and nothing can configure it.

**Approach:** Add the enrolment service in `pm_ai/core/enrolment.py`, taking a `KeychainPort`: mint a key, store it, refuse to overwrite one. `4c` exposes it as `pm-ai key enrol`.

## Boundaries & Constraints

**Always:**
- **The daemon never mints.** A new key makes every previously sealed artifact permanently unreadable, and that is not a decision a process start may take. Minting happens only here, only when a human invokes it.
- **An existing key is never overwritten.** Enrolment against a populated keychain refuses and names the consequence. Replacing a key is a different act with data loss attached and belongs to whoever owns rotation.
- **Key material never leaves the keychain.** Not returned to the caller, not logged, not echoed, not in a traceback, not in a test assertion message.
- The three keychain failure modes `ports` already distinguishes — `KeyNotFound`, `KeychainUnavailable`, `KeychainBackendMissing` (`ports/__init__.py:163,174,184`) — stay distinguished. "Nothing stored" and "cannot reach it" lead to different actions.

**Ask First:** Key rotation, or re-enrolment that re-encrypts existing artifacts. Rotation means rewriting every sealed artifact and has no owner in any story.

**Never:** No daemon changes. No interaction with the encryption toggle — an operator running with encryption disabled still enrols a real key. No file written anywhere: the keychain is the custody boundary 1d established.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh install | keychain reachable, no key | key minted and stored; success reported without echoing it | N/A |
| Already enrolled | keychain holds a key | refused, naming the data-loss consequence | `KeyAlreadyEnrolled` |
| Keychain unreachable | backend present, cannot answer | refused, carrying the probe's remediation | `KeychainUnavailable` |
| Backend missing | `keyring` absent from the build | refused, distinctly from unreachable | `KeychainBackendMissing` |
| Store succeeds, read-back fails | write reported ok, key not retrievable | refused loudly — a key that cannot be read back is not enrolled | `KeychainUnavailable` |

</frozen-after-approval>

## Code Map

- `pm_ai/core/enrolment.py` -- new, the whole of this story
- `pm_ai/ports/__init__.py:163-247` -- the three failure types and `KeychainPort`, reused not redefined
- `pm_ai/platform/keychain.py` -- the macOS adapter 1d built
- `pm_ai/platform/doctor.py:64-72,247` -- the `ABSENT` state and the probe whose remediation text this command becomes
- `pm_ai/storage/crypto.py` -- `LazyKeyCrypto`, why absence surfaces at first encrypted write rather than at boot

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/enrolment.py` -- add `enrol(keychain, *, key_name)`, `KeyAlreadyEnrolled`, and the read-back check -- minting in exactly one place
- [ ] `pm_ai/platform/doctor.py` -- point the `ABSENT` remediation at `pm-ai key enrol` by name -- 1g deliberately left this text pending a command to name
- [ ] `tests/core/test_enrolment.py` -- one test per matrix row, against a fake `KeychainPort`

**Acceptance Criteria:**
- Given a keychain already holding a key, when enrolment runs, then it refuses and no write reaches the keychain — asserted on the fake, not inferred from the message.
- Given a successful enrolment, when the command's entire output and every raised traceback are searched, then the key material appears in neither.
- Given `pm-ai doctor` after enrolment, then the keychain probe reports healthy rather than `ABSENT`.

## Design Notes

The read-back check exists because a keychain write that reports success and stores nothing is indistinguishable, from the caller's side, from one that worked — until the first encrypted write months later. Verifying immediately turns a silent latent failure into a loud one at the only moment a human is present to act on it.

Enrolment logic lives in `core` taking a port, not in `surfaces`, so it is testable against every keychain failure a real machine has and none of which a test may provoke for real. This is the same shape 1g used for its probes, and for the same reason.

## Verification

**Commands:**
- `uv run pytest tests/core/test_enrolment.py -q` -- expected: all matrix rows pass
- `uv run lint-imports` -- expected: contracts kept; `core` imports no OS API
- `uv run pytest -q` -- expected: no new failures
