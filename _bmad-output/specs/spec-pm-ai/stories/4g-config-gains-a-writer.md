---
title: 'config.toml gains a writer'
type: 'feature'
created: '2026-09-03'
status: 'ready-for-dev'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `config.toml` has a reader and no writer. `4a` gave it `load_config` and deliberately no write path, so the one artifact holding `pm_handle` must be typed by hand from a closed vocabulary documented nowhere — an operator discovers what the file may say by triggering refusals one key at a time. And `doctor` cannot report whether the file is even readable: its five probes ask about packages, the sqlite extension, the keychain, the encryption toggle and git, and none asks about config. A file only a human can write cannot be set from the CLI, or later from Telegram.

**Approach:** `render_config(Config) -> bytes` beside `load_config`, plus a sixth probe reporting the config's three unhealthy states. Nothing calls the renderer in this slice — `4h` is its first caller, exactly as `4c` was the loader's.

## Boundaries & Constraints

**Always:**
- **Serialization in `core`, the write elsewhere.** `core` is I/O-free by contract: `render_config` returns bytes and something above it writes them, the mirror of how `load_config` takes bytes and opens nothing. The reason is the same — Telegram is the second channel (story 5) and surfaces reach adapters only through core (AD-30), so neither surface may own this.
- **Round-trip or nothing.** `load_config(render_config(c)) == c` for every admissible `Config`. A renderer and a parser are two vocabularies that drift, which is why `4a` derives `ACCEPTED_KEYS` from the dataclass rather than maintaining a second list.
- **Unset stays unset.** `4a` refuses an explicitly written unset value — a blank `pm_handle`, a zero rate — so a key sitting at its unset default is *omitted*, never emitted. Emitting it would produce a file the loader refuses.
- **The file says who writes it.** A generated header names the CLI as the primary channel and states plainly that a hand-edit is read but its comments are not preserved.
- **Hand-editing stays supported.** AD-3's Tier-1 promise is that the file *can* be hand-edited, not that only a human may write it — the same promise `event_log/` keeps while the single writer appends to it.

**Ask First:** whether a comment in a hand-edited file must survive a rewrite. It does not here: `tomllib` reads and cannot write, and round-tripping comments needs a third-party parser.

**Never:** No encryption-shaped key may be emitted under any circumstance — `4a` refuses them on read, and a writer able to produce one would hand the loader a file it must reject. No new TOML dependency: three typed keys are emitted directly, and the closed vocabulary means there is nothing unknown to round-trip. No file I/O in this module. No caller — `4h` wires it.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Round trip | any admissible `Config` | `load_config` of the output equals the input | N/A |
| Fully unset | `Config()` | header only, no keys; reading it back returns `Config()` | N/A |
| Partially set | `pm_handle` set, rate unset | one key emitted; the unset one absent, not zero | N/A |
| Integral rate | `85.0` | emitted so it reads back as a float, not an int | N/A |
| Handle needing escapes | a handle containing `"` or `\` | escaped, and parses back byte-identical | N/A |
| Probe: no file | `config.toml` absent | `ABSENT` — an ordinary first run, with the remedy named | N/A |
| Probe: unreadable | malformed, or refused by the loader | `FAILING`, carrying the refusal's own message | `ConfigRefused` caught; a probe reports, never raises |
| Probe: readable but unconfigured | valid file, `pm_handle` unset | `WARNING` — nobody is the PM, so no spoken command can execute | N/A |
| Probe: healthy | valid, `pm_handle` set | `OK` | N/A |
| Probe beside the others | another probe raising | every probe still runs — `1g`'s rule | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/core/config.py:98,165,168` -- `Config`, `ACCEPTED_KEYS` and `load_config`; `render_config` joins them and must agree with all three
- `pm_ai/core/config.py:123` -- `__post_init__`, which defines "admissible" for the round-trip rule
- `pm_ai/core/project_registry.py` -- `4d`'s `render_registry`, the sibling pattern; follow its shape
- `pm_ai/platform/doctor.py:89-100` -- `Probe`, `Health` and `remediation`, the four states a probe may report
- `pm_ai/platform/doctor.py:377-395` -- `run_all`, where a sixth probe joins the five
- `pm_ai/platform/doctor.py:247-272` -- `keychain_reachable`, the closest model: `ABSENT` for "reachable, nothing stored"
- `pm_ai/domain/scope_model.py:432` -- `config.toml`: Tier 1, plaintext, not gitignored, and absent from `_APPEND_ONLY_KEYS`, so a write replaces it whole
- `.importlinter:211-219` -- AD-30, why this cannot live in `surfaces`

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/config.py` -- add `render_config(Config) -> bytes` with the generated header, omitting any key at its unset default -- one function, agreeing with `ACCEPTED_KEYS` and `__post_init__`
- [ ] `pm_ai/platform/doctor.py` -- add the config probe with its four states, taking already-read bytes so the probe opens nothing -- this also makes `4c`'s matrix row about a malformed config reportable, which `4c` has no task for
- [ ] `tests/core/test_config_render.py` -- the matrix, with the round trip driven over every admissible combination rather than one example
- [ ] `tests/architecture/test_doctor.py` -- the probe's four states, beside `1g`'s existing probe tests

**Acceptance Criteria:**
- Given every combination of set and unset keys, when rendered and read back, then the result equals the original `Config` — enumerated, because a renderer that drops one key would pass a single-example test.
- Given `render_config(Config())`, when the output is read back, then it is `Config()` and the file contains no key — the unset state must survive a write, or a first-run file would be refused by its own loader.
- Given a rendered file, then `grep` finds no key matching `4a`'s encryption family in it, for any input.
- Given a `config.toml` the loader refuses, when the probe runs, then the report carries the loader's message and `run_all` still returns every other probe.
- Given `pyproject.toml`, then no TOML-writing dependency appears in it.

## Spec Change Log

- **2026-09-03, split at the sizing gate.** Drafted as one slice with the `pm-ai setup` sequence and measured 1981 body tokens, over the 1600 ceiling wave 1 was sized against. Split rather than kept: a serializer plus a probe and a first-run UX are two independently shippable deliverables, and reviewing them together mixes "is this the right serialization" with "is this the right first-run flow". This half can also land while `4b` and `4d` are still in flight, where the sequence cannot. `4h` holds the sequence.

## Design Notes

The round-trip rule is the whole design. Two functions that must agree about a file format is the classic drift pair, and `4a` already paid for that lesson once — `ACCEPTED_KEYS` is derived from the dataclass rather than written twice for exactly this reason. Enumerating the round trip over combinations rather than examples is what makes the agreement checkable instead of asserted.

The probe reports `WARNING` rather than `OK` for a readable file with no `pm_handle` because that state is silently consequential: story 4a made an unset handle match no speaker, so a machine in this state runs, harvests, renders — and quietly executes nothing that was spoken. An operator should be told that before they wonder why.

## Verification

**Commands:**
- `uv run pytest tests/core/test_config_render.py tests/architecture/test_doctor.py -q` -- expected: all matrix rows pass
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept, AD-30 among them
- `uv run mypy` -- expected: clean
