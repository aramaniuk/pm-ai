---
title: 'Config loading'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `config.toml` is declared as an application-scope Tier-1 file (`scope_model.py:432`) and read by nothing. No `tomllib` import exists anywhere in `pm_ai/`. A declared artifact with no reader is a promise the layout makes and the code does not keep, and every setting wave 1 needs has nowhere to come from.

**Approach:** Add `pm_ai/core/config.py`: a typed loader that parses bytes handed to it, plus the refusals that keep the encryption toggle out of the file forever. Nothing consumes it in this story — `4c` is the first caller.

## Boundaries & Constraints

**Always:**
- **The loader parses bytes it is given and never opens a file.** `core` is I/O-free by contract, and `read_artifact` (`service.py:1065`) is already the single reader. The caller reads; this module interprets.
- **`config.toml` may never carry the encryption toggle.** `environment.py` is the only channel, deliberately, because an environment variable dies with the process and a config key is the persistent switch `SPEC.md` forecloses. A key attempting it is **refused by name**, never ignored — and the message says where the setting does live.
- **An unknown key is refused, not ignored.** A typo that silently does nothing is how a setting reads as configured while having no effect.
- Absent and empty are both ordinary first-run states returning defaults. A missing optional config is not a failure.

**Ask First:** Adding any setting beyond what wave 1 needs. Story 4's note says what `config.toml` comes to hold is undecided; this story establishes the reader and the refusals, not the vocabulary.

**Never:** No `tomllib` import outside this module. No environment reads — that is `environment.py`, and two readers of one dangerous flag is the failure it exists to prevent. No write path: `config.toml` is hand-edited (AD-3).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Absent | caller reports no file | defaults returned | N/A |
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
- `pm_ai/domain/scope_model.py:432` -- the declaration that finally gets a reader
- `pm_ai/platform/environment.py:1-22` -- the module docstring states why the toggle may not live in `config.toml`; this story enforces it
- `pm_ai/storage/service.py:1065` -- `read_artifact`, what `4c` will call to get the bytes

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/config.py` -- add `Config`, `load_config(raw: bytes | None) -> Config`, `ConfigRefused`, and the encryption-key family as a named constant -- one place states what the file may say
- [ ] `tests/core/test_config.py` -- one test per matrix row

**Acceptance Criteria:**
- Given a `config.toml` carrying an encryption key, when loaded, then `ConfigRefused` names `PM_AI_DISABLE_ENCRYPTION` — a reader learns where the setting lives rather than only that this is the wrong place.
- Given `grep -rn tomllib pm_ai/`, then exactly one module imports it.
- Given `lint-imports` runs, then `pm_ai.core.config` imports no I/O client and nothing outside `core`/`domain`.

## Design Notes

The encryption refusal is a whole named clause rather than an unknown-key case because the two failures teach different things. An unknown key is a typo; an encryption key is someone deliberately trying to do the thing the architecture forbids, and they need to be told where to go instead. Collapsing it into "unknown key" would answer a real question with a shrug.

Refusing unknown keys at all is the less obvious choice — TOML readers usually ignore extras. But a config file whose typos are silent is one where `verbose_loging = true` reads as configured forever, and this file is small enough that a closed vocabulary costs nothing.

## Verification

**Commands:**
- `uv run pytest tests/core/test_config.py -q` -- expected: all matrix rows pass
- `uv run lint-imports` -- expected: contracts kept
- `uv run pytest -q` -- expected: no new failures
