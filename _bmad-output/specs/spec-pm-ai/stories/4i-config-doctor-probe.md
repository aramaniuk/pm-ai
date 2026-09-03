---
title: 'doctor reports the config'
type: 'feature'
created: '2026-09-03'
status: 'ready-for-dev'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `doctor` cannot say whether `config.toml` is readable. Its five probes ask about runtime packages, the sqlite extension, the keychain, the encryption toggle and git (`doctor.py:377-395`), and none asks about config — so five healthy probes can be reported on a machine whose configuration is unparseable. `4c` promises in a matrix row that an unparseable `config.toml` becomes "one reported probe result", and has no task touching `doctor.py`; `4h` needs the probe to assert that setup left the machine ready.

**Approach:** A sixth probe, taking already-read bytes so it opens nothing, and reporting the states a configuration can actually be in.

## Boundaries & Constraints

**Always:**
- **A probe reports; it never raises.** `doctor.py:22-24` states the rule and `Probe` (`:96-100`) is the shape. A `ConfigRefused` from the loader is caught and carried as the probe's own detail, because an operator needs the loader's message, not a traceback.
- **The input distinguishes three states, not two.** Absent, unreadable and unobtainable are different answers with different remedies, so the probe takes a value that can say which — never `bytes | None`, which reports a permission error as an ordinary first run and tells an operator to create a file they already have.
- **`doctor` survives a failed composition.** `4c` requires it, so "nothing could read the file from here" is a reportable state rather than an exception, and it is distinct from the file being absent.
- **Every probe still runs when one fails.** `run_all` is sequential and independent on purpose (`doctor.py:378-382`); adding a sixth does not change that.
- **The probe opens nothing.** `read_artifact` is the single reader (`service.py:1065`); the caller reads and this probe interprets, the same split `4a` established for the loader.

**Ask First:** Nothing.

**Never:** No write. No config *rendering* — that is `4g`. No new probe for anything but config. No change to the five existing probes' behaviour, only to `run_all`'s signature.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| No file | `config.toml` absent | `ABSENT` — an ordinary first run, naming `pm-ai setup` as the remedy | N/A |
| Unreadable | a directory, a device, or EACCES | `FAILING` naming the read failure — **distinct from absent** | the failure is reported, never raised |
| Unparseable | malformed TOML, or refused by the loader | `FAILING`, carrying the loader's own message verbatim | `ConfigRefused` caught |
| Unobtainable | composition failed, so nothing could read it | reported as unknown-from-here, distinct from absent | N/A |
| Readable, handle unset | valid file, `pm_handle` empty | `WARNING` — nobody is the PM, so no spoken command can execute. `4h` never produces this state, so it means a hand-edit removed the handle | N/A |
| Healthy | valid, `pm_handle` set | `OK` | N/A |
| Beside a failing sibling | another probe raises | this probe still runs and is still reported | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/platform/doctor.py:89-100` -- `Probe`, `Health` and `remediation`, the four states a probe may report
- `pm_ai/platform/doctor.py:377-395` -- `run_all`, where a sixth probe joins five and whose signature gains the config input
- `pm_ai/platform/doctor.py:247-272` -- `keychain_reachable`, the closest model: `ABSENT` for "reachable, nothing stored", with its remedy named
- `pm_ai/platform/doctor.py:399` -- `doctor.main()`, a `run_all` call site
- `pm_ai/core/config.py:168` -- `load_config`, which this probe calls and whose `ConfigRefused` it carries
- `tests/architecture/test_doctor.py:282-285,316,445,611` -- the four assertions a sixth probe breaks

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/platform/doctor.py` -- add the config probe with its states, and give `run_all` the config input -- the probe interprets bytes it is handed and opens nothing
- [ ] `tests/architecture/test_doctor.py` -- **update the four assertions a sixth probe breaks**: the probe count and name set (`:282-285`), `:445`, `:611`, and the healthy-machine case (`:316`), which needs config bytes stood in the way `missing_distributions` already is -- then add the new probe's states
- [ ] `pm_ai/platform/doctor.py` -- point the keychain `ABSENT` remediation at the command that fixes it, which `1g` deliberately left pending -- and assert the command name, not the bare word `"Enrol"` that `:123` currently matches

**Acceptance Criteria:**
- Given a `config.toml` the loader refuses, when the probe runs, then the report carries the loader's own message and `run_all` still returns every other probe.
- Given a file that exists but cannot be read, then the probe reports the read failure and **not** `ABSENT` — the two have different remedies, and collapsing them tells a first-time operator to create a file they already have.
- Given `run_all` with no config input available, then the probe reports unknown-from-here and `doctor` still exits having run every machine probe — the state `4c` requires it to survive.
- Given the keychain `ABSENT` remediation, then it names its command literally, asserted by that string rather than by `"Enrol"` — the substring at `test_doctor.py:123` passes whichever command the text names, or none.
- Given `uv run pytest -q`, then the suite passes with six probes — the four existing assertions updated in this slice, not left for the next one to discover.

## Spec Change Log

- **2026-09-03, split from `4g` at the sizing gate.** `4g` reached 2203 body tokens once the second multi-lens review's findings were applied under the human's unlock. The serializer stays there; this is the diagnostic. Carried over from that review: the probe must distinguish absent from unreadable from unobtainable (B26), and a sixth probe breaks four existing `test_doctor.py` assertions while `4g`'s Verification block claimed no new failures (C2). The remediation-retarget task and its criterion come from C8, which found that `test_doctor.py:123`'s bare `"Enrol"` substring passes whether the text names `pm-ai key enrol`, `pm-ai setup`, or no command at all.

## Design Notes

`WARNING` rather than `OK` for a readable file with no `pm_handle`, because that state is silently consequential: `4a` made an unset handle match no speaker, so the machine runs, harvests and renders while quietly executing nothing that was spoken. Since `pm_handle` is mandatory at first boot, reaching this state means a hand-edit removed it — which is exactly the kind of thing a diagnostic exists to notice.

## Verification

**Commands:**
- `uv run pytest tests/architecture/test_doctor.py -q` -- expected: all states pass, six probes
- `uv run pytest -q` -- expected: no new failures
- `uv run mypy` -- expected: clean
