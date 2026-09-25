---
title: 'Running with encryption off is recorded when it matters, not on every command'
type: 'bugfix'
created: '2026-09-25'
status: 'done'
review_loop_iteration: 0
baseline_commit: '60d44eab7c7356fca999c3d9f370106158a3ba3a'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** With encryption switched off for debugging, every pm-ai command adds a line to the permanent event log — even `pm-ai --help` or a mistyped command.

The line exists for a good reason. Someone who later finds a credential file readable in plain text needs to find out why, and the record is where they look. But the line is written whenever pm-ai starts up, not when anything is actually written unprotected. Measured 2026-09-25 with one project enrolled: `--help`, a mistyped command, `project` with no subcommand and `doctor` each added a line, and none of them wrote anything else. The event log is never trimmed, so a debugging session fills it with lines that explain nothing. The one line that matters, the one before a credential was really written in plain text, becomes hard to find among them.

With encryption on, which is the normal state, nothing is written by those commands, and that stays true.

**Approach:** Write the line the first time a run actually writes a protected file in plain text, just before the file itself. A run that writes nothing protected leaves the event log untouched. The console warning is unchanged and still appears on every command, because it costs nothing permanent and tells the person at the keyboard straight away.

## Boundaries & Constraints

**Always:**
- **The line comes before the file.** A plain-text protected file with no line explaining it must be impossible, so if the line cannot be written, the protected file is not written either and the failure is reported.
- **One line per run**, however many protected files that run writes.
- **The line is unchanged:** the same category, fields and place (the application's own event log, never a project's).
- **Every writer pm-ai builds follows the same rule.** Today there are three: the daemon's, the one `pm-ai setup` uses, and the one project enrolment uses.
- **Reading a protected file never writes the line.** Only writing one does.
- **Encryption on stays silent** on both the console and the log.

**Ask First:** Any change to what the console warning says or when it appears. Any change to which files count as protected.

**Never:**
- No new setting, flag or environment variable.
- No change to the architecture document here. Its rule currently reads "when off, the daemon emits a CLI banner and an `event_log/` entry", without saying when. Restating it to match is recorded for the next architecture pass, which was deferred on 2026-09-25.
- No per-process global state. The "already recorded" fact belongs to the writer, not to a module variable.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| A command that writes nothing | encryption off; `--help`, a mistyped command, `doctor` | console warning; event log unchanged | N/A |
| A credential is saved | encryption off; a protected file is written | one line in the application event log, written before the file | N/A |
| Several protected writes | encryption off; two protected files written in one run | still exactly one line | N/A |
| An ordinary file is written | encryption off; e.g. a goal or the dashboard | no line | N/A |
| A protected file is read | encryption off; read only | no line | N/A |
| The line cannot be written | encryption off; the event log append fails | the protected file is not written | the append's error propagates |
| Encryption on | any command, including a protected write | no warning, no line | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/app/wiring.py:300-303` -- `crypto = _choose_crypto(...)`, `StorageService(...)`, then `if disabled: _announce_disabled_encryption(storage)` — the per-start write this slice moves; the console `print` inside it stays at start
- `pm_ai/app/wiring.py:408-425` -- `_choose_crypto`, the one place `PlaintextCrypto()` is chosen
- `pm_ai/app/wiring.py:428-460` -- `_announce_disabled_encryption`: the `print` and the `storage.append_event_log(EventEntry(category=SelfActionType.SECURITY, ...), scope=APPLICATION)`; split these two apart
- `pm_ai/app/wiring.py:962-977` -- the writer `pm-ai setup` uses (`StorageService(..., crypto=_choose_crypto(...))`); today it records nothing when encryption is off
- `pm_ai/app/wiring.py:1088-1093` -- the writer project enrolment uses; same
- `pm_ai/storage/crypto.py:245-259` -- `PlaintextCrypto`; its docstring says wiring owns the announcement
- `pm_ai/ports/__init__.py:535-556` -- `CryptoPort`: `encrypt` / `decrypt`
- `pm_ai/storage/service.py:922-930`, `:950-958` -- the two protected write paths call `self._crypto.encrypt(...)` only when `is_encrypted(path)`, before `_publish`; so an `encrypt` call is exactly "about to write a protected file", and `decrypt` (`:966`) is a read
- `pm_ai/domain/scope_model.py:1012` -- `ENCRYPTED`: today `private/config.json` (application) and `private/telegram_cache/` (personal); the event log is not in it
- `pm_ai/surfaces/cli/dispatch.py:615` -- `connector add` saves credentials via `daemon.storage`
- `tests/architecture/test_cipher.py:383` -- `test_the_debug_flag_writes_an_event_log_entry` asserts a line at build time; change it to assert the line after a protected write, and none after build alone
- `tests/architecture/test_cipher.py:345-381`, `:398` -- pass-through, plain-bytes, permissions, console and encryption-on tests; keep passing unchanged. Two assert `isinstance(daemon.crypto, PlaintextCrypto)` either way round, so the recording cipher should be a `PlaintextCrypto`
- `tests/architecture/test_cipher.py:53`, `:325`, `:335` -- `_seal`, `_daemon`, `_event_log_text` helpers to reuse

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/app/wiring.py` -- when encryption is off, give each writer a plain-text cipher that appends the existing security line to the application event log on its first `encrypt`, before returning; bind it to its writer after the writer is built; apply it to all three writers; keep the console warning at start in `build()` only -- moves the record to the moment it explains
- [x] `pm_ai/storage/crypto.py` -- docstring only, if it stops being true -- keeps the note honest
- [x] `tests/architecture/test_cipher.py` -- rewrite the event-log test per the Code Map; add a test per matrix row, including the `entry.main` rows for `--help`, a usage error and `doctor` with encryption off and one project enrolled, and the append-fails row -- the matrix is the contract
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- one entry: AD-6's "emits … an `event_log/` entry" needs restating as "on the first protected write in plain text" in the next architecture pass -- the rule this slice outruns

**Acceptance Criteria:**
- Given encryption off and one project enrolled, when `pm-ai --help` runs, then no file under `~/.pm-ai` changes.
- Given the change, when `uv run pytest` runs, then everything passes with 24 skipped.

## Design Notes

"One per run" is enforced per writer: the "already recorded" flag sits on the cipher, and each writer owns its own. A run that builds two writers and writes a protected file through both would record twice. No command does that today, and a module-level flag to prevent it would be the shared global state the architecture forbids (AD-30).

The binding is circular (the writer holds the cipher, and the cipher needs the writer to append), so the cipher receives its recorder after the writer exists. A write attempted before binding must refuse rather than write unrecorded.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass, 24 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks (if no CLI):**
- In a throwaway `HOME` with encryption off and one project enrolled, run `.venv/bin/pm-ai --help`, a mistyped command and `doctor`, then confirm nothing under `.pm-ai/` changed. Use the venv binary rather than `uv run`, because `uv run` puts its cache in the throwaway home.

## Suggested Review Order

**Where the line is now written**

- The heart of it: the debug cipher records once, under a lock, before its first protected write.
  [`wiring.py:429`](../../../../pm_ai/app/wiring.py#L429)

- Every writer is built here, so none can skip the binding.
  [`wiring.py:485`](../../../../pm_ai/app/wiring.py#L485)

- Start-up now only prints the console warning.
  [`wiring.py:304`](../../../../pm_ai/app/wiring.py#L304)

- The console half and the log half, split apart; the line itself is unchanged.
  [`wiring.py:504`](../../../../pm_ai/app/wiring.py#L504)

**The tests that pin it**

- The measured case: `--help`, a typo, `project`, `doctor` change no file.
  [`test_cipher.py:779`](../../../../tests/architecture/test_cipher.py#L779)

- The line lands before the file, and a failed append keeps the file off disk.
  [`test_cipher.py:412`](../../../../tests/architecture/test_cipher.py#L412)

- Concurrent first writes still record one line.
  [`test_cipher.py:525`](../../../../tests/architecture/test_cipher.py#L525)

- The daemon's and setup's writers are both actually bound.
  [`test_cipher.py:562`](../../../../tests/architecture/test_cipher.py#L562)

**Peripherals**

- The architecture rule's restatement, left for the next architecture pass.
  [`deferred-work.md:840`](../../../../_bmad-output/implementation-artifacts/deferred-work.md#L840)
