---
title: 'config.toml gains a writer'
type: 'feature'
created: '2026-09-03'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'b7ba5a57534c93a709334b3f4b31003a869b4754'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `config.toml` has a reader and no writer. `4a` gave it `load_config` and deliberately no write path, so the one artifact holding `pm_handle` must be typed by hand from a closed vocabulary documented nowhere — an operator discovers what the file may say by triggering refusals one key at a time. A file only a human can write cannot be set from the CLI, or later from Telegram.

**Approach:** `render_config(Config) -> bytes` beside `load_config`, and the fourth key `4a` reserved to the human — `display_timezone`, which `23a`'s renderer and the calendar read that selects the day both need and which had no source anywhere in wave 1. (It named `11a`'s `for_day` as the second consumer until 2026-09-07, when upcoming meetings stopped being persisted and the day's schedule moved to a live read; the key, its validation and its single-read rule are unchanged.) Nothing calls it in this slice — `4h` is its first caller, exactly as `4c` was the loader's.

## Boundaries & Constraints

**Always:**
- **Serialization in `core`, the write elsewhere.** `core` is I/O-free by contract: `render_config` returns bytes and something above it writes them, the mirror of how `load_config` takes bytes and opens nothing. Telegram is the second channel (story 5) and surfaces reach adapters only through core (AD-30), so neither surface may own this.
- **`display_timezone` is a fourth key, validated against the zone database.** `4a` closed the vocabulary at three and reserved a fourth to the human, who added this one on 2026-09-03. It is refused unless `ZoneInfo` accepts it, because a typo'd zone silently shifts which meetings count as today in whichever query selects the day — `11a`'s `for_day` when this was written, `33b`'s live fetch since 2026-09-07 — and `zoneinfo` raises `ZoneInfoNotFoundError`, not a `ValueError`, so the refusal must catch it deliberately.
- **The loader and the renderer gain the key together.** A field added to one and not the other is the drift pair this slice exists to close, and `ACCEPTED_KEYS` derives from the dataclass so a field added without a read is admitted and silently dropped.
- **Round-trip or nothing.** `load_config(render_config(c)) == c` for every admissible `Config`. A renderer and a parser are two vocabularies that drift, which is why `4a` derives `ACCEPTED_KEYS` from the dataclass rather than maintaining a second list.
- **A key at its unset default is omitted, never emitted.** This is policy, not a loader constraint: `4a` refuses an explicitly written unset `pm_handle` and an explicit zero rate, but it *accepts* `verbose_logging = false`. So only the renderer stands between an operator and a file that states a setting they expect an effect from.
- **The file says who writes it.** A generated header names the CLI as the primary channel and states plainly that a hand-edit is read but its comments are not preserved.
- **Hand-editing stays supported.** AD-3's Tier-1 promise is that the file *can* be hand-edited, not that only a human may write it — the same promise `event_log/` keeps while the single writer appends to it.

**Ask First:** Nothing.

**Never, added:** comments in a hand-edited file are not preserved across a rewrite. `tomllib` reads and cannot write, round-tripping comments needs a third-party parser this slice refuses, and the generated header says so — the clause was self-answered where it stood.

**Never:** No encryption-shaped key may be emitted under any circumstance — `4a` refuses them on read, and a writer able to produce one would hand the loader a file it must reject. No new TOML dependency: four typed keys are emitted directly, and the closed vocabulary means there is nothing unknown to round-trip. No file I/O in this module. No probe — that is `4i`. No caller — `4h` wires it.

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

- `pm_ai/core/config.py:104,317,320` -- `Config`, `ACCEPTED_KEYS` and `load_config`; `render_config` (`:413`) joins them and must agree with all three
- `pm_ai/core/config.py:226` -- `__post_init__`, which defines "admissible" for the round-trip rule
- `pm_ai/core/config.py:642,683,702` -- `_number`, `_text`, `_flag`: what the loader accepts, and therefore what a round trip must survive
- `pm_ai/ports/__init__.py:718` -- `ConfigPort`, the **only** settings interface a surface is typed against, through `DaemonPort.config`. A field added to `Config` and not here is unreachable from `23a`'s renderer and `23b`, and nothing fails until a later slice's mypy run
- `tests/architecture/test_static_rules.py:560,632` -- `CONFIG_IMPORTS_ALLOWED` and the sweep that this module reads and writes no file. Adding an import here is a deliberate act; `zoneinfo` is the entry that needed the question answered rather than waved through
- `pm_ai/connectors/graph/calendar.py` -- `zone_of`, the sibling zone lookup. It already separates a typo'd zone from a machine with no timezone database and names the `tzdata` remedy; this slice's refusal follows it
- `pm_ai/domain/scope_model.py:432` -- `config.toml`: Tier 1, plaintext, not gitignored
- `pm_ai/domain/storage_tiers.py:172` -- `_APPEND_ONLY_KEYS`, which `config.toml` is absent from, so a write replaces it whole
- `.importlinter:211-219` -- AD-30, why this cannot live in `surfaces`

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/core/config.py` -- add `display_timezone` to `Config`, its `ZoneInfo` validation to `__post_init__`, and its read to `load_config` -- the fourth key `4a` reserved; `ACCEPTED_KEYS` derives it, so a field without a read is admitted and dropped
- [x] `pm_ai/core/config.py` -- add `render_config(Config) -> bytes` with the generated header, omitting any key at its unset default -- one function, agreeing with `ACCEPTED_KEYS` and `__post_init__`
- [x] `tests/core/test_config_render.py` -- the matrix, with the round trip driven over every combination of set and unset rather than one example
- [x] `pm_ai/ports/__init__.py` -- add the fourth member to `ConfigPort`, and a parity test pinning its members to `fields(Config)` -- found by review; a key `Config` carries and the port does not is unreachable from every surface
- [x] `tests/architecture/test_static_rules.py` -- allowlist `zoneinfo`, and widen the config sweep from read verbs to writes -- both forced by this slice: the module gains an import that can reach a filesystem, and becomes a writer

**Acceptance Criteria:**
- Given every combination of the **four** keys set and unset, when rendered and read back, then the result equals the original `Config` — enumerated, because a renderer that drops one key would pass a single-example test.
- Given any `Config`, then the **set of keys rendered equals the set whose value differs from `Config()`'s**. Round-trip equality alone cannot catch an always-emitted `verbose_logging = false`, because the loader accepts it.
- Given any `Config`, then every rendered key is a member of `ACCEPTED_KEYS` — the checkable form of "no encryption-shaped key is ever emitted". A direct grep for the encryption family cannot fail, since four fields cannot produce a matching key, and its only realistic outcome is a false positive against this slice's own header.
- Given `render_config(Config())`, when the output is read back, then it is `Config()` and the file contains no key — the unset state must survive a write, or a first-run file would be refused by its own loader.
- Given `pyproject.toml`, then no TOML-writing dependency appears in it.

## Spec Change Log

- **2026-09-15, one numeral corrected inside the frozen block, on the human's authorisation.** The `Never` clause read "three typed keys are emitted directly" and the vocabulary has been four since 2026-09-03, when this slice was given `display_timezone`. Handled as an UNLOCK in the sense `triage-wave-1-2026-09-03.md` defined — the fix is clear, the text is frozen, the human authorises and the edit is mechanical — rather than as a renegotiation, because nothing about the intent moves: the clause forbids a TOML-writing dependency and its reason is that the vocabulary is closed and small, which four keys satisfy exactly as three did. The whole frozen block was swept for the same drift rather than only the reported line; the two other mentions of three are `4a` closing the vocabulary at three before this key was added, and `AD-3`, both correct as written and left alone.

- **2026-09-15, implemented, and the three-layer review's fourteen fixes applied.** Five were correctness. `render_config` raised `UnicodeEncodeError` rather than `ConfigRefused` for a lone surrogate in `pm_handle` — admissible at construction, so inside this slice's own "round trip or nothing" clause, and reachable rather than theoretical because argv decodes with `surrogateescape` and `4h` sets the handle from the command line. Refused in `__post_init__` rather than escaped, since no TOML string can carry an unpaired surrogate; the predicate asks by encoding rather than by code-point range, because a range check refuses valid astral characters. `Config(verbose_logging=1)` rendered `verbose_logging = 1.0`, a file this module writes and its own loader refuses — the flag lacked the `bool` guard the rate has had all along. The timezone catch missed `OSError`, and its refusal could not tell a typo'd zone from a machine with no timezone database, which `zone_of` already separates; it now does too, and names the remedy.

  **`ConfigPort` is the finding worth carrying forward.** `Config` gained a fourth field and the port kept declaring three — and it is the only settings interface a surface is typed against, so `display_timezone` was unreachable from `23a`'s renderer and `23b`, the two consumers the key was added for, with nothing in the suite failing. It would have surfaced as a mypy error inside the next slice rather than this one. The Code Map named neither the port nor the static-rules sweep, which is why both are added to it above along with two task lines; the omission is recorded here rather than resolved by a re-derivation, because the implementation was coherent and what was missing was an interface restatement.

  Verification gaps closed alongside: the header was pinned only on the empty-file branch, so a renderer emitting it nowhere else passed the whole suite; the dependency ban parsed requirements with chained `split` and was defeated by `~=`, `!=`, a URL, a marker or a capital; and the static sweep still knew only read verbs while this module had become a writer. Every fix was mutation-checked — reverted individually and confirmed to turn the suite red — rather than merely re-run.

  **Left open:** the frozen `Never` clause still reads "three typed keys are emitted directly" and the vocabulary is four. It is inside `<frozen-after-approval>` and awaits the human's authorisation. One packaging question is deferred rather than answered: `pm_ai.core` now needs a platform timezone database and only `pm_ai.connectors` is recorded as needing one.

- **2026-09-07, the second consumer of `display_timezone` changed name.** Consequent on the decision that no future meeting is persisted: the day's schedule is read live from `33b` rather than from `11a`'s `for_day`, so the two citations naming `for_day` as this key's other reader are corrected. Nothing about the key changes — it is still the fourth and last, still reserved to the human, still validated against the zone database, and still read once so a renderer and a day-selection cannot disagree. Only the reader on the far side of that single read is different.

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

## Suggested Review Order

**The writer itself**

- Start here: the round trip and the omit-at-default rule, both walked from `fields(Config)`.
  [`config.py:413`](../../../../pm_ai/core/config.py#L413)

- Refuses an unrenderable type rather than dropping it, so a future field cannot vanish silently.
  [`config.py:452`](../../../../pm_ai/core/config.py#L452)

- The escape table, and why a surrogate is refused upstream instead of escaped here.
  [`config.py:484`](../../../../pm_ai/core/config.py#L484)

**The fourth key, and what admissible now means**

- The key itself; `""` is unset, and a caller needing a day boundary refuses rather than assuming UTC.
  [`config.py:224`](../../../../pm_ai/core/config.py#L224)

- Three refusals, not one: a typo, a machine with no timezone database, a non-string.
  [`config.py:155`](../../../../pm_ai/core/config.py#L155)

- Probes `ZoneInfo("UTC")` so a missing database cannot be reported as a typo.
  [`config.py:135`](../../../../pm_ai/core/config.py#L135)

- Asks by encoding, never by code-point range — a range check refuses valid astral characters.
  [`config.py:120`](../../../../pm_ai/core/config.py#L120)

- The flag guard the rate has always had; without it the writer emits a file the loader refuses.
  [`config.py:263`](../../../../pm_ai/core/config.py#L263)

**The interface the review caught**

- The fourth member. Without it the key is unreachable from every surface, and nothing fails.
  [`ports/__init__.py:748`](../../../../pm_ai/ports/__init__.py#L748)

- Parity pinned by return annotation, so the next added field fails here, not in a later story.
  [`test_config.py:175`](../../../../tests/core/test_config.py#L175)

**Guards widened because this module changed shape**

- `zoneinfo` allowlisted deliberately — the first entry that can reach a filesystem.
  [`test_static_rules.py:560`](../../../../tests/architecture/test_static_rules.py#L560)

- The sweep now covers writes too; the module is no longer only a reader.
  [`test_static_rules.py:632`](../../../../tests/architecture/test_static_rules.py#L632)

**Tests worth reading rather than counting**

- Guards the sweep itself: fails if the fixtures drift from `ACCEPTED_KEYS`.
  [`test_config_render.py:79`](../../../../tests/core/test_config_render.py#L79)

- Asserts against output on all 16 combinations, not against the constant on the empty one.
  [`test_config_render.py:182`](../../../../tests/core/test_config_render.py#L182)

- The round-trip claim stated as a property: no admissible `Config` may fail to encode.
  [`test_config_render.py:363`](../../../../tests/core/test_config_render.py#L363)

- PEP 508 parsing extracted and tested, after chained `split` let four pin styles through.
  [`test_config_render.py:515`](../../../../tests/core/test_config_render.py#L515)
