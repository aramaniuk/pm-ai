---
title: 'Master-key enrolment'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 1
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
- **The key length is `AES_KEY_BYTES`, and this slice hoists that constant to `pm_ai/ports/`.** `EnvelopeCipher` refuses anything but 32 bytes (`crypto.py:153,192-194`), the constant lives in `pm_ai.storage.crypto`, and `pm_ai.core.enrolment` may not import it under the layering contract. Duplicating the literal is the shape that already failed once: `ports/__init__.py:202-206` records two independent literals for the key *name* causing ABSENT to be reported on a healthy machine.
- **One name, from one constant.** `enrol` stores under `MASTER_KEY_NAME`, the same constant `LazyKeyCrypto` fetches and `keychain_reachable` probes.

**Ask First:** Key rotation, or re-enrolment that re-encrypts existing artifacts. Rotation means rewriting every sealed artifact and has no owner in any story.

**Never:** No daemon changes. No interaction with the encryption toggle — an operator running with encryption disabled still enrols a real key. No file written anywhere: the keychain is the custody boundary 1d established.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh install | keychain reachable, no key | key minted and stored; success reported without echoing it | N/A |
| Two enrolments racing | both observe an empty keychain | store is conditional-on-absent, not read-then-write; the loser refuses | `KeyAlreadyEnrolled` |
| Stored entry corrupt | present but truncated or not key material | refused as already-enrolled; **never** read as absent and minted over | `KeyAlreadyEnrolled` |
| Read-back mismatch | stored value differs from what was minted | refused; compared for equality, not merely for retrievability | `KeychainUnavailable` |
| Minted key wrong length | anything but `AES_KEY_BYTES` | refused before storing | `ValueError` |
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
- [ ] `pm_ai/ports/__init__.py` -- move `AES_KEY_BYTES` here beside `MASTER_KEY_NAME` -- `core` cannot import `pm_ai.storage.crypto`, and a duplicated literal is the failure `:202-206` records
- [ ] `pm_ai/app/wiring.py` -- add `keychain: KeychainPort` to `Daemon`, passing the adapter `build()` already constructs at `wiring.py:115` -- without it `4c` has no legal route: `surfaces` may not reach `keyring`, indirectly included, under `.importlinter:115-131`
- [ ] `pm_ai/core/enrolment.py` -- add `enrol(keychain, *, key_name=MASTER_KEY_NAME)`, `KeyAlreadyEnrolled`, and the read-back equality check -- minting in exactly one place
- [ ] `pm_ai/platform/doctor.py` -- point the `ABSENT` remediation at `pm-ai key enrol` by name -- 1g deliberately left this text pending a command to name
- [ ] `tests/core/test_enrolment.py` -- one test per matrix row, against a fake `KeychainPort`

**Acceptance Criteria:**
- Given a keychain already holding a key, when enrolment runs, then it refuses and no write reaches the keychain — asserted on the fake, not inferred from the message.
- Given a successful enrolment, when `enrol`'s return value, every log record it emits and every traceback it raises are searched, then the key material appears in none. Stated at service level because `4c` does not exist yet; the no-echo-at-the-prompt and `pm-ai doctor` criteria belong to `4c`, which owns the surface.
- Given `enrol` stores a key, then the name it stored under equals `MASTER_KEY_NAME` and the length equals `AES_KEY_BYTES` — asserted against the constants, because the tests run on a fake and nothing else would notice enrolment writing somewhere the cipher never looks.
- Given a fake whose read-back returns different material than was stored, then enrolment refuses rather than reporting success.

## Spec Change Log

- **2026-09-02, multi-lens review.** Three findings changed the slice's contents.
  **Two of three acceptance criteria could not be evaluated at this slice's own checkpoint** — both named the CLI, which `4c` builds afterwards. Restated at service level, with the surface criteria moved to `4c`.
  **The key length was never stated.** `EnvelopeCipher` accepts only 32 bytes and `AES_KEY_BYTES` sits in `pm_ai.storage.crypto`, which `core` may not import — so the implementer would have duplicated the literal, which `ports/__init__.py:202-206` records having already caused ABSENT on a healthy machine when it happened to the key *name*. Hoisting the constant is now a task, and a criterion asserts both the name and the length against the constants.
  **`enrol` had no legal route to a keychain.** `Daemon` exposes no keychain field, and `surfaces` may not reach `keyring` even indirectly (`.importlinter:115-131`). Adding the field to `Daemon` is now a task here rather than an improvisation in `4c`.
  The edge-case lens added the two paths that destroy data: two enrolments racing on an empty keychain, and a corrupt stored entry read as absent and minted over. Both now refuse.
## Design Notes

The read-back check exists because a keychain write that reports success and stores nothing is indistinguishable, from the caller's side, from one that worked — until the first encrypted write months later. Verifying immediately turns a silent latent failure into a loud one at the only moment a human is present to act on it.

Enrolment logic lives in `core` taking a port, not in `surfaces`, so it is testable against every keychain failure a real machine has and none of which a test may provoke for real. This is the same shape 1g used for its probes, and for the same reason.

## Verification

**Commands:**
- `uv run pytest tests/core/test_enrolment.py -q` -- expected: all matrix rows pass
- `uv run lint-imports` -- expected: contracts kept; `core` imports no OS API
- `uv run pytest -q` -- expected: no new failures
