---
title: 'Envelope cipher and encrypted operational store'
type: 'feature'
created: '2026-08-21'
status: 'ready-for-dev'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/storage-contract.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The classifier from story 1e knows which artifacts should be encrypted, and the keychain from story 1d can hold a key, but nothing encrypts anything. The operational store — pending external writes, connector cursors, and the ledger of mutations already sent to external systems — sits on disk in plaintext.

**Approach:** Add a `CryptoPort` and an envelope cipher to `pm_ai/storage/crypto.py` that encrypts and decrypts using a key it is given, writing at file mode `0600`. Have `StorageService` receive the key as a constructor argument and use it when opening the operational store. `pm_ai/app/wiring.py` fetches the key through `KeychainPort` and passes it down.

**Depends on:** story 1d for the keychain port, story 1e for the classifier.

## Boundaries & Constraints

**Always:**
- `pm_ai.storage` and `pm_ai.platform` are sibling layers that may not import each other (`.importlinter`), so `crypto.py` cannot call the keychain adapter. The key is fetched in `pm_ai/app/wiring.py` — the only module permitted to import both — and passed down as a value.
- `sqlcipher3` is imported **inside the function that uses it**. It is in the `runtime` extra and not installed here, and a module-level import would turn this story's tests into skips that read as coverage.
- Fail closed. A missing key refuses to open the encrypted store. There is no fallback to plaintext.
- Encryption may be disabled by an explicit debug flag. It defaults to on, is never off in a fresh installation, and while off it emits **both** a console warning and an event-log entry.
- Encrypted files are written at mode `0600`.

**Ask First:** Choosing a cipher or key-derivation function other than the AES-256 the storage contract specifies. Encrypting an artifact the classifier reports as plaintext.

**Never:** No Markdown encrypted, in any scope. No encryption of the derived store or vector index — they hold indexes and embeddings rather than recoverable text, are rebuildable, and are protected by file permissions and full-disk encryption. No secret written to the event log, to diagnostics, or into a model prompt. No startup probes; that is story 1g.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| A key is available | a payload is encrypted, then decrypted | the result equals the original payload |
| A key is available | a payload is encrypted | the bytes written differ from the payload, and the file's mode is `0600` |
| Two different keys | the same payload is encrypted under each | the two outputs differ |
| A payload encrypted under one key | decryption is attempted with another | raises a typed error rather than returning corrupt data |
| An empty keychain | the encrypted operational store is opened | raises a typed error; the store is not opened and no plaintext file is created at its path |
| A valid key | the operational store is opened, written, closed, and reopened | the rows written before closing are readable afterwards |
| Encryption disabled by the debug flag | the daemon starts | the store opens in plaintext, and both a console warning and an event-log entry are emitted |
| Encryption disabled, then re-enabled with the original key | the store is opened | the mismatch is reported as a typed error rather than read as corruption |
| `pm_ai.storage.crypto` | imported while the `runtime` extra is absent | imports successfully |

</frozen-after-approval>

## Code Map

- `pm_ai/storage/crypto.py` — created by story 1e with the classifier; gains the cipher here.
- `pm_ai/ports/__init__.py:14-51` — the protocol shape `CryptoPort` should follow.
- `pm_ai/storage/service.py:121-129` — the constructor and the point where the operational store is opened; it gains the key as an argument.
- `pm_ai/app/wiring.py:36-39` — the only `StorageService` construction site, where the key is fetched through `KeychainPort` and passed down.
- `pyproject.toml` — the `runtime` extra declares `sqlcipher3==0.6.2`, not installed here, which is why its import must be lazy.

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/ports/__init__.py` — add `CryptoPort`.
- [ ] `pm_ai/storage/crypto.py` — add envelope encrypt and decrypt over an injected key, writing at mode `0600`, with `sqlcipher3` imported inside the call.
- [ ] `pm_ai/storage/service.py` — accept the key as a constructor argument and use it when opening the operational store; emit the warning and event-log entry when encryption is disabled.
- [ ] `pm_ai/app/wiring.py` — fetch the key through `KeychainPort` and inject it.
- [ ] `tests/architecture/test_cipher.py` — new. One test per matrix row, using a fake keychain.

**Acceptance Criteria:**
- Given the `runtime` extra is not installed, when `uv run pytest` runs, then no test skips on a missing `sqlcipher3` import and every module here imports successfully.
- Given `uv run lint-imports`, then all 12 contracts hold and no module under `pm_ai/storage/` imports `pm_ai.platform`.
- Given an empty keychain, when the operational store is opened, then it raises and no plaintext file exists at its path.
- Given encryption disabled by the debug flag, then both a console warning and an event-log entry are emitted.
- Given the store is written, closed, and reopened with the same key, then the earlier rows are readable.

## Design Notes

Encryption and durability are independent properties, and an implementation that derives one from the other gets two artifacts wrong in opposite directions. `personal_analytics.db` is encrypted because burnout figures are recoverable personal facts, and it is also not rebuildable because the telemetry it was computed from gets pruned. The vector index is the mirror image: rebuildable, and plaintext. Keep the two questions separate.

## Verification

- `uv run pytest -q -rs` — expected: 29 skipped, none naming `pm_ai.storage.crypto`.
- `uv run python -c "import pm_ai.storage.crypto"` — expected: silent success with the `runtime` extra absent.
- `uv run lint-imports` — expected: `Contracts: 12 kept, 0 broken.`
- Introduce a plaintext fallback for a missing key and confirm the test goes red, then remove it.
