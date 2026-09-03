---
title: 'First-run setup'
type: 'feature'
created: '2026-09-03'
status: 'draft'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing configures a new machine. `doctor` reports what is wrong — a keychain holding no key, an unregistered project, a `config.toml` that is absent or unreadable — and every remedy is a separate command in a required order: enrol before any encrypted write, register before anything resolves a path, configure before a spoken command can be attributed. `4b`, `4d` and `4g` each build their piece and nothing sequences them, so a first boot is a runbook the operator does not have.

**Approach:** One `pm-ai setup` that runs the sequence and then reports `doctor`, so "ready" is asserted rather than assumed.

## Boundaries & Constraints

**Always:**
- **Encryption is never offered as a choice.** Setup enrols the master key and never asks whether to encrypt. No persistent setting may disable encryption; `PM_AI_DISABLE_ENCRYPTION` — an environment variable, which dies with the process — is the only channel, and `4a` refuses any encryption-shaped key in `config.toml` by name.
- **Every step is re-runnable, and refuses rather than overwrites.** An enrolled key, a registered project and an existing `config.toml` are ordinary states on a second run, not failures. Nothing here may replace a key: a new one makes every previously sealed artifact unreadable.
- **The sequence is ordered by dependency, and says why.** Key before any encrypted write, project before path resolution, config last because it is the only step whose absence is survivable.
- **Setup asserts its own result.** It ends by running `1g`'s probes and reporting them, exiting per `4c`'s table. "The command succeeded" and "the machine is ready" are different claims, and only the second matters to the operator.
- **Nothing is reimplemented.** `4b`'s `enrol`, `4d`'s registry and `4g`'s `render_config` are called. This slice owns the order, the prompts, and the write.

**Ask First:** whether setup should also offer `connector add` (`8b`) once it exists. Excluded here: a connector needs a tenant and a browser sign-in, which is a different kind of step from three local ones.

**Never:** No encryption toggle. No credential in `config.toml` — that is `8b`'s sealed store. No prompting for anything `4a` does not accept: the closed vocabulary is the whole question set. No daemon, no scheduler, no Telegram surface — Telegram reuses `4g`'s renderer when story 5 lands. No new subcommand beyond `setup`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Clean machine | no key, no project, no config | key enrolled, project registered, `config.toml` written, probes reported | exit `4` if any probe is unhealthy |
| Second run, all configured | key present, project registered, config valid | every step reports already-done and writes nothing | exit `0` |
| Key already enrolled | keychain holds a key | `4b`'s `KeyAlreadyEnrolled` reads as a completed step, not a failure | N/A |
| Keychain unreachable | no backend, or the OS refuses | setup stops at that step and names it; nothing later runs | exit `3` |
| Malformed `config.toml` present | hand-edited, unparseable | refused by name and left untouched; setup does not overwrite it | `ConfigRefused`, exit `3` |
| Operator declines a value | empty answer for `pm_handle` | the key is omitted rather than written blank, which `4a` would refuse | N/A |
| Non-interactive invocation | no TTY | refuses rather than prompting into a pipe | exit `2` |
| Write refused mid-sequence | root unwritable | steps already completed stay done; the refusal names where it stopped | propagated, exit `3` |
| Environment fault after a full run | `sqlite-vec` unavailable | every configuration step completes; the probe is reported | exit `4` |
| Interrupted part-way | operator aborts at the prompt | what was done stays done; re-running continues from there | exit `2` |

</frozen-after-approval>

## Code Map

- `pm_ai/core/enrolment.py` -- `4b`'s `enrol(keychain, *, key_name)` and `KeyAlreadyEnrolled`, called not reimplemented
- `pm_ai/core/project_registry.py` -- `4d`'s registry functions and its refusals
- `pm_ai/core/config.py` -- `4g`'s `render_config`, and `4a`'s `load_config` for the pre-write read
- `pm_ai/surfaces/cli/dispatch.py` -- `4c`'s dispatch table and exit codes, reused not redefined
- `pm_ai/app/entry.py` -- `4c`'s `main`; the rendered bytes reach `StorageService.write_artifact` from here, because `surfaces` may not reach `storage`
- `pm_ai/storage/service.py:1022` -- `write_artifact(payload, *, scope, artifact)`, the single writer
- `pm_ai/platform/doctor.py:377-395` -- `run_all`, the closing report; `4g` adds the config probe it needs
- `pm_ai/platform/doctor.py:260-270` -- the keychain `ABSENT` remediation, whose text should now name `pm-ai setup`

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- add `setup`: enrol, register, prompt, write, report -- one ordered sequence, each step idempotent and each refusal naming its step
- [ ] `pm_ai/app/entry.py` -- route `render_config`'s bytes to `write_artifact`, and read the existing file first so a malformed one is refused before anything is written
- [ ] `pm_ai/platform/doctor.py` -- retarget the keychain `ABSENT` remediation at `pm-ai setup`, which `1g` left pending a command to name
- [ ] `tests/slice/test_first_run.py` -- a clean temporary root driven to all-probes-green with scripted answers, plus the matrix

**Acceptance Criteria:**
- Given a clean temporary root, when `setup` runs with scripted answers, then `doctor` reports keychain, registry and config healthy — asserted on the probe report rather than on setup's exit code, because the two can disagree.
- Given `setup` run twice, then no file's bytes differ after the second run and it exits `0` — asserted on file contents, since "already done" is easy to report and easy to get wrong.
- Given a keychain that already holds a key, then no write reaches the keychain — asserted on the fake, because replacing a key destroys every sealed artifact.
- Given a malformed `config.toml`, then it is byte-identical after the failed run.
- Given `pm-ai setup` with stdin not a TTY, then it refuses with exit `2` and writes nothing.

## Spec Change Log

- **2026-09-03, split at the sizing gate.** Drafted together with the config serializer and probe as one slice measuring 1981 body tokens, over wave 1's 1600 ceiling. `4g` holds the serializer and the probe — independently shippable, and able to land while `4b` and `4d` are still in flight. This half holds the sequence, which cannot start until all three exist.

## Design Notes

Four commands in a required order is not a first-run experience; it is a runbook the operator was never given. The value here is that the ordering knowledge moves into code — and the closing probe report exists because every configuration step can succeed while the machine stays unusable, which is exactly what `sqlite-vec` and `git` faults do.

Config is deliberately last. A machine with no key refuses encrypted writes and one with no project cannot resolve a path, so both are hard stops; a machine with no `pm_handle` runs fine and simply attributes nothing to the PM. Ordering by consequence means an interrupted setup leaves the most valuable steps done.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_first_run.py -q` -- expected: all matrix rows pass
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept, AD-30 among them
- `uv run mypy` -- expected: clean
