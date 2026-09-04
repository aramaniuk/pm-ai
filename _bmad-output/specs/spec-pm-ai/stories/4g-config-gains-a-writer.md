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

**Problem:** `config.toml` has a reader and no writer. `4a` gave it `load_config` and deliberately no write path, so the one artifact holding `pm_handle` must be typed by hand from a closed vocabulary documented nowhere — an operator discovers what the file may say by triggering refusals one key at a time. A file only a human can write cannot be set from the CLI, or later from Telegram.

**Approach:** `render_config(Config) -> bytes` beside `load_config`, and the fourth key `4a` reserved to the human — `display_timezone`, which `23a`'s renderer and `11a`'s `for_day` both need and which had no source anywhere in wave 1. Nothing calls it in this slice — `4h` is its first caller, exactly as `4c` was the loader's.

## Boundaries & Constraints

**Always:**
- **Serialization in `core`, the write elsewhere.** `core` is I/O-free by contract: `render_config` returns bytes and something above it writes them, the mirror of how `load_config` takes bytes and opens nothing. Telegram is the second channel (story 5) and surfaces reach adapters only through core (AD-30), so neither surface may own this.
- **`display_timezone` is a fourth key, validated against the zone database.** `4a` closed the vocabulary at three and reserved a fourth to the human, who added this one on 2026-09-03. It is refused unless `ZoneInfo` accepts it, because a typo'd zone that reaches `for_day` silently shifts which meetings count as today — and `zoneinfo` raises `ZoneInfoNotFoundError`, not a `ValueError`, so the refusal must catch it deliberately.
- **The loader and the renderer gain the key together.** A field added to one and not the other is the drift pair this slice exists to close, and `ACCEPTED_KEYS` derives from the dataclass so a field added without a read is admitted and silently dropped.
- **Round-trip or nothing.** `load_config(render_config(c)) == c` for every admissible `Config`. A renderer and a parser are two vocabularies that drift, which is why `4a` derives `ACCEPTED_KEYS` from the dataclass rather than maintaining a second list.
- **A key at its unset default is omitted, never emitted.** This is policy, not a loader constraint: `4a` refuses an explicitly written unset `pm_handle` and an explicit zero rate, but it *accepts* `verbose_logging = false`. So only the renderer stands between an operator and a file that states a setting they expect an effect from.
- **The file says who writes it.** A generated header names the CLI as the primary channel and states plainly that a hand-edit is read but its comments are not preserved.
- **Hand-editing stays supported.** AD-3's Tier-1 promise is that the file *can* be hand-edited, not that only a human may write it — the same promise `event_log/` keeps while the single writer appends to it.

**Ask First:** whether a comment in a hand-edited file must survive a rewrite. It does not here: `tomllib` reads and cannot write, and round-tripping comments needs a third-party parser.

**Never:** No encryption-shaped key may be emitted under any circumstance — `4a` refuses them on read, and a writer able to produce one would hand the loader a file it must reject. No new TOML dependency: three typed keys are emitted directly, and the closed vocabulary means there is nothing unknown to round-trip. No file I/O in this module. No probe — that is `4i`. No caller — `4h` wires it.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Round trip | any admissible `Config` | `load_config` of the output equals the input | N/A |
| Fully unset | `Config()` | header only, no keys; reading it back returns `Config()` | N/A |
| Partially set | `pm_handle` set, rate unset | one key emitted; the unset one absent, not zero | N/A |
| Integral rate | `85.0` | emitted so it reads back as a float, not an int | N/A |
| Flag emitted | `verbose_logging=True` | emitted as a TOML boolean, never `1` or `"true"` | N/A |
| Flag at its default | `verbose_logging=False` | omitted, like every other unset key | N/A |
| Timezone set | `display_timezone = "Europe/Warsaw"` | round-trips; `ZoneInfo` accepts it | N/A |
| Timezone unknown | `"Europe/Warsav"` | refused, naming the key — a typo'd zone silently shifts which meetings are today | `ConfigRefused`, catching `ZoneInfoNotFoundError` |
| Timezone unset | no key | omitted; `Config()`'s default is the unset state, and a caller needing a day boundary refuses rather than assuming UTC | N/A |
| Handle needing escapes | a handle containing `"`, `\`, a newline or a control character | escaped, and parses back byte-identical — `Config.__post_init__` admits `"a\nb"`, and an unescaped newline would make the file unparseable by its own loader | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/core/config.py:98,165,168` -- `Config`, `ACCEPTED_KEYS` and `load_config`; `render_config` joins them and must agree with all three
- `pm_ai/core/config.py:123` -- `__post_init__`, which defines "admissible" for the round-trip rule
- `pm_ai/core/config.py:337,378,397` -- `_number`, `_text`, `_flag`: what the loader accepts, and therefore what a round trip must survive
- `pm_ai/core/project_registry.py` -- `4d`'s `render_registry`, the sibling pattern; follow its shape
- `pm_ai/domain/scope_model.py:432` -- `config.toml`: Tier 1, plaintext, not gitignored
- `pm_ai/domain/storage_tiers.py:163` -- `_APPEND_ONLY_KEYS`, which `config.toml` is absent from, so a write replaces it whole
- `.importlinter:211-219` -- AD-30, why this cannot live in `surfaces`

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/config.py` -- add `display_timezone` to `Config`, its `ZoneInfo` validation to `__post_init__`, and its read to `load_config` -- the fourth key `4a` reserved; `ACCEPTED_KEYS` derives it, so a field without a read is admitted and dropped
- [ ] `pm_ai/core/config.py` -- add `render_config(Config) -> bytes` with the generated header, omitting any key at its unset default -- one function, agreeing with `ACCEPTED_KEYS` and `__post_init__`
- [ ] `tests/core/test_config_render.py` -- the matrix, with the round trip driven over every combination of set and unset rather than one example

**Acceptance Criteria:**
- Given every combination of the **four** keys set and unset, when rendered and read back, then the result equals the original `Config` — enumerated, because a renderer that drops one key would pass a single-example test.
- Given any `Config`, then the **set of keys rendered equals the set whose value differs from `Config()`'s**. Round-trip equality alone cannot catch an always-emitted `verbose_logging = false`, because the loader accepts it.
- Given any `Config`, then every rendered key is a member of `ACCEPTED_KEYS` — the checkable form of "no encryption-shaped key is ever emitted". A direct grep for the encryption family cannot fail, since three fields cannot produce a matching key, and its only realistic outcome is a false positive against this slice's own header.
- Given `render_config(Config())`, when the output is read back, then it is `Config()` and the file contains no key — the unset state must survive a write, or a first-run file would be refused by its own loader.
- Given `pyproject.toml`, then no TOML-writing dependency appears in it.

## Spec Change Log

- **2026-09-03, gained the fourth config key.** `display_timezone`, answering the `Ask First` `4a` reserved to the human. `render_dashboard(..., *, tz)` and `for_day(day, *, tz)` both took a timezone and nothing supplied it: `4g` emitted only three keys, `4h` forbids prompting for anything `4a` does not accept, and `23a`'s own `Ask First` recorded that it had no owner in any story. It lands here rather than in a slice of its own because a loader and a renderer that disagree about a file format is the drift pair this slice exists to close. Its validation is against the zone database, since a typo'd zone shifts which meetings count as today, and `zoneinfo` raises `ZoneInfoNotFoundError` rather than a `ValueError`.

- **2026-09-03, split at the sizing gate, second time.** Amending this slice under the human's unlock took it to 2203 body tokens against wave 1's 1600 ceiling. The probe left for `4i`: a serializer and a diagnostic are two independently shippable deliverables, and reviewing them together mixes "is this the right serialization" with "is this the right thing to tell an operator". Each half now sits near 1100.
- **2026-09-03, frozen intent amended under the human's unlock, after the second multi-lens review.** The escape row was too narrow — `Config.__post_init__` admits `"a\nb"`, so an unescaped newline produces a file the loader cannot parse. `verbose_logging` appeared in no row, and the stated reason for omitting unset keys was false for it: `_flag` accepts `false` exactly as it accepts `true`, so the omission rule is policy rather than a constraint the loader enforces. Two criteria could not fail — the encryption-family grep cannot match anything a three-field dataclass emits, and the enumerated round trip is blind to an always-emitted flag — and both are replaced by assertions on the rendered key set.
  KEEP: the round-trip property enumerated over combinations rather than examples. A renderer and a parser are the classic drift pair, and `4a` already paid for that lesson once.

## Design Notes

The round-trip rule is the whole design. Two functions that must agree about a file format is the classic drift pair, and `ACCEPTED_KEYS` is derived from the dataclass rather than written twice for exactly that reason. Enumerating over combinations rather than examples is what makes the agreement checkable instead of asserted.

## Verification

**Commands:**
- `uv run pytest tests/core/test_config_render.py -q` -- expected: all matrix rows pass
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept, AD-30 among them
- `uv run mypy` -- expected: clean
