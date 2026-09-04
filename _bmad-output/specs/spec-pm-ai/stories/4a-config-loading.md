---
title: 'Config loading'
type: 'feature'
created: '2026-09-02'
status: 'done'
review_loop_iteration: 1
baseline_commit: '2120c5de1c09a42e560600bd5e957fab45b3e550'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `config.toml` is declared as an application-scope Tier-1 file (`scope_model.py:432`) and read by nothing. No `tomllib` import exists anywhere in `pm_ai/`. A declared artifact with no reader is a promise the layout makes and the code does not keep, and every setting wave 1 needs has nowhere to come from.

**Approach:** Add `pm_ai/core/config.py`: a typed loader that parses bytes handed to it, plus the refusals that keep the encryption toggle out of the file forever. Nothing consumes it in this story — `4c` is the first caller.

## Boundaries & Constraints

**Always:**
- **The loader parses bytes it is given and never opens a file.** `core` is I/O-free by contract, and `read_artifact` (`service.py:1065`) is already the single reader. The caller reads; this module interprets. This is structural rather than a promise: `load_config` takes `bytes | None` and there is nothing to open. Stated because no existing gate would catch a file read here — the single-writer AST sweep exempts read-mode opens (`test_static_rules.py:105-127`), the import contracts list only network and database clients, and the file-I/O rule is scoped to `pm_ai.storage`.
- **`config.toml` may never carry the encryption toggle.** `environment.py` is the only channel, deliberately, because an environment variable dies with the process and a config key is the persistent switch `SPEC.md` forecloses. A key attempting it is **refused by name**, never ignored — and the message says where the setting does live.
- **An unknown key is refused, not ignored.** A typo that silently does nothing is how a setting reads as configured while having no effect.
- Absent and empty are both ordinary first-run states returning defaults. A missing optional config is not a failure.

**The wave-1 vocabulary, named:** three keys, because a closed vocabulary with no members refuses every possible file.

| Key | Type | Why wave 1 needs it |
|---|---|---|
| `blended_hourly_rate` | float | CAP-3's Man-Hour Cost; `Meeting.man_hour_cost` takes it and nothing supplies it |
| `pm_handle` | str | hardcoded `"andrei@example.com"` at `wiring.py:44`; `extract()` takes it |
| `verbose_logging` | bool | the one setting `environment.py:16-17` explicitly sanctions for this file |

**Ask First:** Any fourth key. Story 4's note says what `config.toml` comes to hold is otherwise undecided.

**Never:** No `tomllib` import outside this module. No environment reads — that is `environment.py`, and two readers of one dangerous flag is the failure it exists to prevent. No write path: `config.toml` is hand-edited (AD-3).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Absent | caller reports no file | defaults returned | N/A |
| Encryption key that is also unknown | `encryption_mode = "off"` | the encryption refusal wins; it is checked before the unknown-key sweep | `ConfigRefused` naming the variable |
| Nested or dotted encryption key | `[encryption]` table, or `encryption.disable` | matched on the full dotted path, not top-level names | `ConfigRefused` |
| Bool where float declared | `blended_hourly_rate = true` | refused; `bool` is not accepted as a number despite subclassing `int` | `ConfigRefused` |
| UTF-8 BOM | editor-added `\xef\xbb\xbf` prefix | BOM stripped before parsing — a valid file must not read as malformed | N/A |
| Type-valid but unusable | `blended_hourly_rate = -5.0`, `pm_handle = ""` | refused, naming the key and the admissible range | `ConfigRefused` |
| Empty | `b""` | defaults returned | N/A |
| Valid | a declared key at its declared type | typed `Config` carrying it | N/A |
| Encryption toggle attempted | any key matching the encryption family | refused, naming `PM_AI_DISABLE_ENCRYPTION` as the only channel | `ConfigRefused` |
| Unknown key | a typo of a real key | refused, listing the accepted set | `ConfigRefused` |
| Malformed TOML | truncated table | refused, naming the line | `ConfigRefused` |
| Wrong type | a string where a bool is declared | refused, naming key and expected type | `ConfigRefused` |
| Not UTF-8 | invalid byte sequence | refused at decode, distinctly from malformed TOML | `ConfigRefused` |

</frozen-after-approval>

## Code Map

- `pm_ai/core/config.py` -- new, the whole of this story
- `pm_ai/core/extraction.py:44` -- `speaker_is_pm` gained a `bool(pm_handle)` guard, because retiring the `wiring.py` literal made "no PM configured" a reachable state
- `tests/slice/test_transcript_slice.py:45` -- the AD-32 daemon fixture now states its `pm_handle`; a new test pins the unset case
- `tests/architecture/test_static_rules.py` -- home of the three repository-wide sweeps this story added (the loader opens nothing, one module imports `tomllib`, no developer address survives in the package)
- `pm_ai/domain/scope_model.py:432` -- the declaration that finally gets a reader
- `pm_ai/platform/environment.py:1-22` -- the module docstring states why the toggle may not live in `config.toml`; this story enforces it
- `pm_ai/storage/service.py:1065` -- `read_artifact`, what `4c` will call to get the bytes

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/core/config.py` -- add `Config` (the three keys with defaults), `load_config(raw: bytes | None) -> Config`, `ConfigRefused`, and the encryption-key family as a named constant -- one place states what the file may say
- [x] `pm_ai/app/wiring.py` -- take `pm_handle` from the loaded `Config`, retiring the `wiring.py:44` literal -- the hardcoded address is why this key is in wave 1
- [x] `tests/core/test_config.py` -- one test per matrix row

**Acceptance Criteria:**
- Given a `config.toml` carrying an encryption key, when loaded, then `ConfigRefused` names `PM_AI_DISABLE_ENCRYPTION` — a reader learns where the setting lives rather than only that this is the wrong place.
- Given `grep -rn tomllib pm_ai/`, then exactly one module imports it.
- Given a valid `config.toml` setting `pm_handle`, when the daemon is built, then `Daemon.pm_handle` is that value and `grep -n "andrei@example.com" pm_ai/` has no match — the literal is gone, not shadowed.
- Given `blended_hourly_rate = true`, then it is refused; a `bool` reaching `man_hour_cost` as `1.0` would silently price every meeting at one currency unit per attendee-hour.
- Given `lint-imports` runs, then `pm_ai.core.config` imports no I/O client and nothing outside `core`/`domain`.

## Spec Change Log

- **2026-09-03, the `Ask First` on a fourth key was answered.** This slice reserved "any fourth key" to the human, and on 2026-09-03 they added **`display_timezone`**: `render_dashboard` and `11a`'s `for_day` both take a `tz` and the value had no source anywhere in wave 1, so a 23:30-local meeting silently belonged to the wrong day in the one artifact the PM reads each morning. The frozen key table below is therefore historical from that date — the vocabulary is four keys. The field, its validation and its rendering all land in `4g`, because a loader and a renderer that disagree about a file format is the drift pair this module already guards against, and splitting them across two slices is how they drift.

- **2026-09-02, multi-lens review.** The closed key vocabulary had **no members**: every key was deferred to Ask First, so the loader would have refused every possible `config.toml` — a declared, hand-editable, non-gitignored file that accepts nothing. Three keys are now named, chosen because wave 1 demonstrably needs them: `pm_handle` was hardcoded at `wiring.py:44`, `blended_hourly_rate` is what CAP-3's Man-Hour Cost needs and 11a's own Ask First raised, and `verbose_logging` is the one key `environment.py` sanctions. Retiring the `wiring.py:44` literal became a task here rather than staying nobody's.
  The edge-case lens added five unhandled paths, of which two are substantive: an encryption key that is *also* an unknown key had two matching refusal rows and no precedence, and `isinstance(True, int)` means a TOML `true` would pass a float check and price every meeting at one unit per attendee-hour.
  The verification lens found the central Always — "never opens a file" — invisible to all three declared commands. It is now stated as structural (the signature takes bytes) with the reason no gate covers it, rather than as a promise.
- **2026-09-02, during implementation: retiring the literal made "no PM configured" reachable, and AD-32 had to be told.** No matrix row or criterion covers this, and it is the story's most consequential effect. `wiring.py` defaulted `pm_handle` to one developer's address, so `extract()`'s `speaker_is_pm=(u.speaker_handle == pm_handle)` was always comparing against *something*. With the literal gone, `Config().pm_handle` is `""` — and nothing validates the handles a transcript arrives with, so an authenticated transcript can carry an empty one. A bare equality would then have made an unattributed utterance the PM on any machine with no `config.toml`: an unconfigured install granting spoken-command execution authority, the one direction AD-32 may not fail in. Two changes close it, and both belong to this story because this story opened it — `extract()` now requires a non-empty `pm_handle` before any comparison, and `Config.__post_init__` refuses a whitespace-only handle so the `bool` test is sufficient rather than merely usual. The wave-1 defaults are therefore *unset states* rather than values: `pm_handle = ""` matches nobody and `blended_hourly_rate = 0.0` reports a zero cost rather than a plausible wrong one, while a `config.toml` that writes either explicitly is refused, so absent and abandoned cannot be confused.

## Design Notes

The encryption refusal is a whole named clause rather than an unknown-key case because the two failures teach different things. An unknown key is a typo; an encryption key is someone deliberately trying to do the thing the architecture forbids, and they need to be told where to go instead. Collapsing it into "unknown key" would answer a real question with a shrug.

Refusing unknown keys at all is the less obvious choice — TOML readers usually ignore extras. But a config file whose typos are silent is one where `verbose_loging = true` reads as configured forever, and this file is small enough that a closed vocabulary costs nothing.

## Verification

**Commands:**
- `uv run pytest tests/core/test_config.py -q` -- expected: all matrix rows pass
- `uv run lint-imports` -- expected: contracts kept
- `uv run pytest -q` -- expected: no new failures

## Suggested Review Order

**What the file may say**

- The whole vocabulary and its refusals in one place — start here for the design.
  [`config.py:98`](../../../../pm_ai/core/config.py#L98)
- Refusal order is fixed and documented: decode, parse, encryption, vocabulary, types, values.
  [`config.py:168`](../../../../pm_ai/core/config.py#L168)
- The encryption family, matched per path segment case-folded — the architecture's one-way door.
  [`config.py:282`](../../../../pm_ai/core/config.py#L282)
- Closed vocabulary: a typo is refused, never ignored.
  [`config.py:304`](../../../../pm_ai/core/config.py#L304)

**Invariants the class holds, not only the loader**

- Unset states stay constructible; whitespace handles and unusable rates cannot be built.
  [`config.py:123`](../../../../pm_ai/core/config.py#L123)
- Accepted set derived from the dataclass, so a field cannot be admitted unread.
  [`config.py:165`](../../../../pm_ai/core/config.py#L165)

**Refuse or return, for pathological input too**

- Depth bound, so a 1000-part key refuses instead of exhausting the stack.
  [`config.py:253`](../../../../pm_ai/core/config.py#L253)
- Decode reports the offset in the file, BOM included — not in the stripped text.
  [`config.py:207`](../../../../pm_ai/core/config.py#L207)
- A 400-digit integer refuses by name rather than raising `OverflowError`.
  [`config.py:337`](../../../../pm_ai/core/config.py#L337)

**Who the daemon thinks the PM is**

- One developer's address was the compiled-in default; `Config` is now its only home.
  [`wiring.py:50`](../../../../pm_ai/app/wiring.py#L50)
- Config arrives already parsed — `core` opens nothing, the single reader reads.
  [`wiring.py:62`](../../../../pm_ai/app/wiring.py#L62)
- The consequence: unset matches nobody, so an unconfigured install grants no authority (AD-32).
  [`extraction.py:53`](../../../../pm_ai/core/extraction.py#L53)

**Gates**

- The story's central Always, as an import allowlist rather than a reviewer's denylist.
  [`test_static_rules.py:537`](../../../../tests/architecture/test_static_rules.py#L537)
- One `tomllib` importer, matched on import nodes; and the retired address stays retired.
  [`test_static_rules.py:563`](../../../../tests/architecture/test_static_rules.py#L563)
- Every accepted key round-trips, which is what fails when a fourth arrives unread.
  [`test_config.py:370`](../../../../tests/core/test_config.py#L370)
- One row per matrix scenario, both halves of every type message asserted.
  [`test_config.py:165`](../../../../tests/core/test_config.py#L165)
- AD-32 restated as a test: an unconfigured handle matches no speaker.
  [`test_transcript_slice.py:128`](../../../../tests/slice/test_transcript_slice.py#L128)
