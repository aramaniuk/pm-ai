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
- **The store is conditional on absence, and `KeychainPort` has no such primitive yet.** `store` is specified as "replacing any previous value" (`ports/__init__.py:186-190`), so a read-then-write loses the race the matrix names: two enrolments both observe an empty keychain and the second overwrites the first's key, making every artifact sealed in between unreadable. This slice adds the conditional operation to the port and to the macOS adapter; without it the refusal is advisory.
- **Key material never leaves the keychain.** Not returned to the caller, not logged, not echoed, not in a traceback, not in a test assertion message.
- The three keychain failure modes `ports` already distinguishes — `KeyNotFound`, `KeychainUnavailable`, `KeychainBackendMissing` (`ports/__init__.py:163,174,184`) — stay distinguished. "Nothing stored" and "cannot reach it" lead to different actions.
- **The key length is `AES_KEY_BYTES`, defined in `pm_ai/ports/` by this slice.** `EnvelopeCipher` refuses anything but 32 bytes (`crypto.py:153,192-194`) and `pm_ai.core.enrolment` may not import `pm_ai.storage.crypto`. Duplicating the literal is the shape that already failed once, for the key *name* (`ports/__init__.py:202-206`).
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
- [ ] `pm_ai/ports/__init__.py` -- define `AES_KEY_BYTES` here beside `MASTER_KEY_NAME`, and **re-export it from `pm_ai.storage.crypto`** keeping that module's `__all__` entry -- `core` cannot import `pm_ai.storage.crypto`, and a bare move breaks `tests/architecture/test_cipher.py:38-39`, which imports the name and uses it at six sites, at collection time
- [ ] `pm_ai/ports/__init__.py`, `pm_ai/platform/keychain.py` -- add the conditional-on-absent store to `KeychainPort` and the macOS adapter -- the matrix's race row has no mechanism without it, and AD-14's port inventory gains an operation
- [ ] `pm_ai/core/enrolment.py` -- add `enrol(keychain, *, key_name=MASTER_KEY_NAME)`, `KeyAlreadyEnrolled`, and the read-back equality check -- minting in exactly one place
- [ ] `tests/core/test_enrolment.py` -- one test per matrix row, against a fake `KeychainPort`

**Acceptance Criteria:**
- Given a keychain already holding a key, when enrolment runs, then it refuses and no write reaches the keychain — asserted on the fake, not inferred from the message.
- Given a successful enrolment, when `enrol`'s return value, every log record it emits and every traceback it raises are searched, then the key material appears in none. Stated at service level because `4c` does not exist yet; the no-echo-at-the-prompt and `pm-ai doctor` criteria belong to `4c`, which owns the surface.
- Given `enrol` stores a key, then the name it stored under equals `MASTER_KEY_NAME` and the length equals `AES_KEY_BYTES` — asserted against the constants, because the tests run on a fake and nothing else would notice enrolment writing somewhere the cipher never looks.
- Given both modules, then `pm_ai.storage.crypto.AES_KEY_BYTES is pm_ai.ports.AES_KEY_BYTES` — **identity, not equality**. A copy rather than a re-export leaves two literals that are both `32`, so every length assertion passes while the duplication this task exists to prevent ships invisibly; `ports/__init__.py:202-206` records that exact failure happening once already, for the key *name*.
- Given two enrolments that both observe an empty keychain, then exactly one key is stored — asserted on the fake, because a read-then-write passes a single-threaded test and loses the race in production.
- Given a fake whose read-back returns different material than was stored, then enrolment refuses rather than reporting success.

## Spec Change Log

- **2026-09-03, amended against the second multi-lens review.**
  **The race row had no mechanism** (B5). `KeychainPort.store` is specified as replacing any previous value, so the matrix's two-enrolments-racing row was unimplementable and its loser would overwrite the winner's key — making every artifact sealed in between unreadable. The port and the macOS adapter gain a conditional-on-absent operation, which touches AD-14's inventory.
  **The `AES_KEY_BYTES` move breaks a test module at collection** (B6). `tests/architecture/test_cipher.py:38-39` imports the name and uses it at six sites. It is now defined in `ports` and re-exported from `pm_ai.storage.crypto`, and a criterion asserts **identity** rather than equality — a copy leaves two literals that are both `32`, so every value assertion passes while the duplication ships (C7).
  **The remediation task moved to `4i`** (C8). Both this slice and `4h` claimed the keychain `ABSENT` text, and with `pm-ai setup` as the command that actually fixes it, the retarget belongs with the probe work. `test_doctor.py:123`'s bare `"Enrol"` substring passes whichever command the text names, or none, so `4i` carries the criterion too.

- **2026-09-02, the daemon field moved to `4c`.** The multi-lens review's fix for A3 ("`enrol` has no legal route to a keychain") was added here as a task adding `keychain: KeychainPort` to `Daemon` — which this spec's own frozen `Never: No daemon changes` forbids. Caught at the story-4a review gate and resolved by the human in favour of moving the task rather than amending the frozen clause, which now stands untouched and true. The reasoning: `enrol(keychain, *, key_name)` is a `core` service tested against a fake `KeychainPort` and never needs `Daemon` at all, while `4c` is the slice that calls it from the surface and already owns `pm_ai/app/entry.py`. The route is still mandatory and still recorded, in `4c`'s task list, with the layering reason unchanged.

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
