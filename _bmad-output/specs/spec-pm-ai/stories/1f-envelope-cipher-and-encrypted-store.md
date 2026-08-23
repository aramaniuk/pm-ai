---
title: 'Envelope cipher for credentials and the personal enclave'
type: 'feature'
created: '2026-08-21'
updated: '2026-08-23'
status: 'done'
review_loop_iteration: 2
baseline_commit: '7c40f21'
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/storage-contract.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The classifier from story 1e knows which artifacts should be encrypted, and the keychain from story 1d can hold a key, but nothing encrypts anything.

**Retargeted 2026-08-22, before implementation.** This story was *"Envelope cipher and encrypted operational store"*, and the operational store is no longer encrypted — the user narrowed the encrypted set to credentials and the sovereign personal enclave. What survives is the whole cipher half; only its subjects changed:

| Encrypt | Why it stayed on the list |
| --- | --- |
| `~/.pm-ai/private/config.json` | API credentials — every provider token lands here |
| `~/.manager-ai/private/telegram_cache/` | the PM's own voice notes and dialogue state |

**Narrowed again 2026-08-23.** `personal_analytics.db` was dropped too. It is Tier 2 SQLite under a gitignored 600 enclave — structurally identical to `operational.db`, which had already gone — and it was the *only* remaining reason for `sqlcipher3`, a dependency with no macOS wheel that builds from source on the one platform v1 targets. What keeps burnout figures from an employer is the scope boundary and the egress rules; encryption at rest only answers someone reading the disk, and full-disk encryption already answers that.

So **nothing encrypted is a database.** Both subjects are files, `sqlcipher3` has left `pyproject.toml`, and this story needs one file cipher and no page-level one. `StorageService` needs no key at all — the store it opens is plaintext.

**Approach:** Add a `CryptoPort` and an envelope cipher to `pm_ai/storage/crypto.py` that encrypts and decrypts using a key it is given, writing at file mode `0600` inside directories at `0700`. `pm_ai/app/wiring.py` fetches the key through `KeychainPort` and passes it to whatever opens an encrypted artifact — which, after the narrowing, is the personal-analytics store and the two credential files rather than the single writer.

**Depends on:** story 1d for the keychain port, story 1e for the classifier — which as of 2026-08-22 derives its answer from the scope trees, so `is_encrypted` is the entry point and `ENCRYPTED` in `pm_ai.domain.scope_model` is the authority behind it.

## Boundaries & Constraints

**Always:**
- `pm_ai.storage` and `pm_ai.platform` are sibling layers that may not import each other (`.importlinter`), so `crypto.py` cannot call the keychain adapter. The key is fetched in `pm_ai/app/wiring.py` — the only module permitted to import both — and passed down as a value.
- Whatever cipher library this adds is imported **inside the function that uses it** if it is an optional extra. A module-level import of something absent turns this story's tests into skips that read as coverage — the rule story 1d established for `keyring`.
- Fail closed. A missing key refuses to open an encrypted artifact. There is no fallback to plaintext — a store that silently opens unencrypted is worse than one that will not open.
- Encryption may be disabled by an explicit debug flag. It defaults to on, is never off in a fresh installation, and while off it emits **both** a console warning and an event-log entry.
- Encrypted files are written at mode `0600`.
- **Directories holding encrypted artifacts are created at mode `0700`.** A `0600` file inside a world-readable directory still leaks its name, its size, and its mtime — enough to show that a 1:1 with a named report happened, on a given day, which is the fact the enclave exists to hide. This story owns file modes, so it owns the directories it creates them in. (Deferred here after story 1a and never recorded in this file until 2026-08-22.)

**Ask First:** Choosing a cipher or key-derivation function other than the AES-256 the storage contract specifies. Encrypting an artifact the classifier reports as plaintext.

**Never:** No Markdown encrypted, in any scope. No encryption of the derived store or vector index — they hold indexes and embeddings rather than recoverable text, are rebuildable, and are protected by file permissions and full-disk encryption. No secret written to the event log, to diagnostics, or into a model prompt. No startup probes; that is story 1g.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| `is_encrypted` reports an artifact plaintext | it is written | no cipher is involved, and no key is fetched for it |
| A key is available | a payload is encrypted, then decrypted | the result equals the original payload |
| A key is available | a payload is encrypted | the bytes written differ from the payload, and the file's mode is `0600` |
| A key is available, target directory absent | the encrypted store is opened | every directory created along the way is mode `0700`, and the store itself `0600` |
| A directory that already exists at `0755` | an encrypted artifact is written into it | the directory is tightened to `0700` rather than left as found |
| Two different keys | the same payload is encrypted under each | the two outputs differ |
| A payload encrypted under one key | decryption is attempted with another | raises a typed error rather than returning corrupt data |
| An empty keychain | an encrypted artifact is opened | raises a typed error; it is not opened and no plaintext file is created at its path |
| A valid key | an encrypted file is written, closed, and read back | the content written before closing is recovered exactly |
| Encryption disabled by the debug flag | the daemon starts | encrypted artifacts open in plaintext, and both a console warning and an event-log entry are emitted |
| Encryption disabled, then re-enabled with the original key | the artifact is opened | the mismatch is reported as a typed error rather than read as corruption |
| `pm_ai.storage.crypto` | imported while the `runtime` extra is absent | imports successfully |

</frozen-after-approval>

## Code Map

- `pm_ai/storage/crypto.py` — created by story 1e with the classifier; gains the cipher here.
- `pm_ai/ports/__init__.py:20-140` — the five existing protocols (`ConnectorPort:20`, `ScopePathPort:34`, `VcsPort:84`, `StoragePort:119`, `SkillPort:131`), whose shape `CryptoPort` should follow. Note that neither `KeychainPort` nor `CryptoPort` exists yet, despite the spine's ports inventory listing both — 1d adds the first, this story the second.
- `pm_ai/storage/service.py:263-305` — the constructor and the point where the operational store is opened; it gains the key as an argument. It already requires `paths`, `now` and `vcs` as keyword-only injections with no defaults, and the key follows that pattern.
- `pm_ai/app/wiring.py:87` — the only `StorageService` construction site, where the key is fetched through `KeychainPort` and passed down.
- `pyproject.toml` — the `runtime` extra no longer declares `sqlcipher3`; it left with the last encrypted database on 2026-08-23. Whatever this story adds for file encryption is a new entry, and the lazy-import rule applies to it.

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/ports/__init__.py` — `CryptoPort` (`encrypt`/`decrypt` over bytes) and `DecryptionFailed`. The key is the implementation's, not a parameter: a method taking one puts it in every call site's locals and traceback.
- [x] `pm_ai/storage/crypto.py` — `AesGcmCrypto` (AES-256-GCM, fresh nonce per call), `PlaintextCrypto` for the debug flag, `LazyKeyCrypto`, and `write_encrypted`/`read_encrypted` owning the file and directory modes.
- [x] ~~`pm_ai/storage/service.py` — accept the key~~ — **void.** The operational store is plaintext as of 2026-08-22, so the single writer needs no key and never learns where secrets live.
- [x] `pm_ai/app/wiring.py` — fetches the key through `KeychainPort`, and owns the debug flag's console warning and event-log entry, because only the composition root knows a flag exists.
- [x] `pyproject.toml` — `cryptography` in the `runtime` extra **and** the `dev` group. Optional so the module imports without it; dev so the suite exercises the real cipher rather than skipping, since a skipped encryption test reads as coverage.
- [x] `tests/architecture/test_cipher.py` — new, 24 tests against the real cipher.
- [x] `tests/architecture/test_keychain.py` — one test repaired: it asserted that `keyring` happened not to be installed, and `uv add --optional runtime` installed it. Absence is now simulated.

**Acceptance Criteria:**
- Given the `runtime` extra is not installed, then no test skips on a missing cipher import and every module here imports successfully. **310 passed, 29 skipped**; no skip names `pm_ai.storage.crypto`.
- Given `uv run lint-imports`, then all 12 contracts hold and no module under `pm_ai/storage/` imports `pm_ai.platform`.
- Given an empty keychain, when an encrypted artifact is written, then it raises and **neither the file nor its directory** is created — a zero-length `config.json` is worse than none, because the next reader cannot tell it from a store legitimately emptied.
- Given encryption disabled by the debug flag, then both a console warning and an event-log entry are emitted.
- Given a fresh tree, then `stat` reports `0700` on the directory and `0600` on the file, and a `umask 000` cannot loosen either.
- Given the key is needed for three files in one run, then the keychain is read **once**: a keychain read is a user-visible prompt on some configurations.
- Given nothing encrypted is written at all, then no key is fetched — the daemon starts on a machine where none has been enrolled.

## Design Notes

Encryption and durability are independent properties, and an implementation that derives one from the other gets two artifacts wrong in opposite directions. The vector index is rebuildable and plaintext; `config.json` is neither. Keep the two questions separate. (This note used to make its point with `personal_analytics.db`, which was encrypted and not rebuildable — it left the encrypted set on 2026-08-23, so the example moved and the point did not.)

**Why the key is fetched lazily, which the frozen Approach did not say.** Written eagerly, `build()` refused to construct a daemon on any machine with no key enrolled — which broke 33 existing tests and, more importantly, would refuse to boot a fresh install over a capability that run may never use. Harvesting, briefings and the CLI touch neither encrypted file. Fail-closed is preserved and merely moved to the moment an encrypted artifact is read or written, which is also the moment an operator can act on it. Same shape as the capture guard: ask when the capture is written, not at startup.

**Why `KeyNotFound` is not caught anywhere.** A first run has no key and must mint one. That is a decision with consequences — a new key makes every previously sealed artifact unreadable — so it belongs to whatever owns installation, not to a constructor that would quietly do it and write a second key over a store it could still have opened.

**Why the payload is sealed before anything is created.** `write_encrypted` calls the cipher first, then makes the directory. A refusal therefore leaves no directory and no empty file for a later reader to interpret.

**Why the file mode is set twice.** `os.open`'s mode argument is masked by the process umask, so a user running with `umask 000` would get a world-readable credential file and nothing would report it. The explicit `chmod` after the write is what closes that, and a test runs under `umask 000` to prove it.

## Verification

- `uv run pytest -q -rs` — expected: 29 skipped, none naming `pm_ai.storage.crypto`. Confirmed.
- `uv run python -c "import pm_ai.storage.crypto"` — silent success with the `runtime` extra absent. Confirmed.
- `uv run lint-imports` — `Contracts: 12 kept, 0 broken.` Confirmed.
- Introduce a plaintext fallback for a missing key and confirm the test goes red, then remove it. **Run 2026-08-23:** wrapping the key fetch in `except Exception: PlaintextCrypto()` turns `test_no_key_refuses_the_write_and_leaves_nothing_behind` red. 1 failed, 23 passed. Restored.
- `stat -f '%Sp %N'` on the enclave directory and the store — expected: `drwx------` and `-rw-------`. A `0600` file inside a `0755` directory still publishes its name, size and mtime, which is enough to reveal that a 1:1 with a named report happened on a given day.
