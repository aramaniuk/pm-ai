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
- **The probe's input distinguishes three states, not two.** Absent, unreadable and unobtainable are different answers with different remedies, so the probe takes a value that can say which — not `bytes | None`, which reports a permission error as an ordinary first run.
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
| Handle needing escapes | a handle containing `"`, `\`, a newline or a control character | escaped, and parses back byte-identical — `Config.__post_init__` admits `"a\nb"`, and an unescaped newline would make the file unparseable by its own loader | N/A |
| Flag emitted | `verbose_logging=True` | emitted as a TOML boolean, never `1` or `"true"` | N/A |
| Flag at its default | `verbose_logging=False` | omitted, like every other unset key — the loader *accepts* `false`, so only the renderer prevents a file that states a setting the operator expects an effect from | N/A |
| Probe: no file | `config.toml` absent | `ABSENT` — an ordinary first run, with the remedy named | N/A |
| Probe: unreadable | a directory, a device, or EACCES | `FAILING` naming the read failure — **distinct from absent**, which the caller must therefore distinguish rather than collapsing both into `None` | the read failure is reported, never raised |
| Probe: unparseable | malformed, or refused by the loader | `FAILING`, carrying the refusal's own message | `ConfigRefused` caught; a probe reports, never raises |
| Probe: bytes unobtainable | composition failed, so nothing could read the file | reported as unknown-from-here, distinct from absent — `4c` requires `doctor` to survive a failed composition | N/A |
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
- `pm_ai/domain/scope_model.py:432` -- `config.toml`: Tier 1, plaintext, not gitignored
- `pm_ai/domain/storage_tiers.py:163` -- `_APPEND_ONLY_KEYS`, which `config.toml` is absent from, so a write replaces it whole
- `.importlinter:211-219` -- AD-30, why this cannot live in `surfaces`

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/config.py` -- add `render_config(Config) -> bytes` with the generated header, omitting any key at its unset default -- one function, agreeing with `ACCEPTED_KEYS` and `__post_init__`
- [ ] `pm_ai/platform/doctor.py` -- add the config probe with its four states, taking already-read bytes so the probe opens nothing -- this also makes `4c`'s matrix row about a malformed config reportable, which `4c` has no task for
- [ ] `tests/core/test_config_render.py` -- the matrix, with the round trip driven over every admissible combination rather than one example
- [ ] `tests/architecture/test_doctor.py` -- the probe's states beside `1g`'s existing probe tests, **and update the four assertions a sixth probe breaks**: the probe count and name set (`:282-285`), `:445`, `:611`, and the healthy-machine case (`:316`) which needs config bytes stood in the way `missing_distributions` already is
- [ ] `pm_ai/platform/doctor.py` -- `run_all` gains the config input; its five call sites, `doctor.main()` (`:399`) and the `python -m pm_ai.platform.doctor` subprocess test all observe the signature

**Acceptance Criteria:**
- Given every combination of set and unset keys, when rendered and read back, then the result equals the original `Config` — enumerated, because a renderer that drops one key would pass a single-example test.
- Given `render_config(Config())`, when the output is read back, then it is `Config()` and the file contains no key — the unset state must survive a write, or a first-run file would be refused by its own loader.
- Given any `Config`, then the **set of keys rendered equals the set whose value differs from `Config()`'s** — asserted per combination, not only round-trip equality. `load_config` refuses an explicitly-written unset `pm_handle` and an explicit zero rate, but it *accepts* `verbose_logging = false`, so a renderer that always emits the flag round-trips equal in all eight combinations while producing exactly the file `4a`'s refusals exist to prevent.
- Given any `Config`, then every rendered key is a member of `ACCEPTED_KEYS` — which is the checkable form of "no encryption-shaped key is ever emitted". The direct grep for the encryption family cannot fail, since `Config`'s three fields cannot produce a matching key, and its only realistic outcome is a false positive against this slice's own required header.
- Given a `config.toml` the loader refuses, when the probe runs, then the report carries the loader's message and `run_all` still returns every other probe.
- Given a `config.toml` that exists but cannot be read, then the probe reports the read failure and **not** `ABSENT` — the two have different remedies and collapsing them tells a first-time operator to create a file they already have.
- Given `pyproject.toml`, then no TOML-writing dependency appears in it.

## Spec Change Log

- **2026-09-03, frozen intent amended under the human's unlock, after the second multi-lens review.**
  **The escape row was too narrow** (B25). `Config.__post_init__` admits `"a\nb"`, so an unescaped newline or control character produces a file the loader cannot parse. And **`verbose_logging` appeared in no row at all** — worse, the stated reason for omitting unset keys ("emitting it would produce a file the loader refuses") is false for it: `_flag` accepts `false` exactly as it accepts `true`. The omission rule is now stated as policy, with two rows for the flag.
  **The probe could not distinguish unreadable from absent** (B26). `bytes | None` reports a permission error, a directory and a device as an ordinary first run, with "create the file" as the remedy for a file that already exists. Three input states are now named, plus a fourth for composition having failed — which `4c` requires `doctor` to survive.
  **Two criteria could not fail** (C11, C12). The encryption-family grep cannot match anything a three-field dataclass emits, and the enumerated round trip is blind to an always-emitted `verbose_logging = false`. Replaced with assertions on the rendered key set, which catch both.
  **The sixth probe breaks four existing assertions** (C2) while the Verification block claimed no new failures. A task now owns them, and the `run_all` signature change is named with every site that observes it.
  KEEP: the round-trip property enumerated over combinations rather than examples. A renderer and a parser are the classic drift pair, and `4a` already paid for that lesson once.

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
