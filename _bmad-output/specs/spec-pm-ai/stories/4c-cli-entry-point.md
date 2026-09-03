---
title: 'CLI entry point and subcommand dispatch'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `pyproject.toml` declares no `[project.scripts]` and `pm_ai/surfaces/cli/__init__.py` is a one-line docstring. Nothing in this repository can be run. Story 1g built a full diagnostics suite that no operator can reach, and `4a`/`4b` add a config reader and an enrolment service with no way to invoke either.

**Approach:** Console script at `pm_ai/app/entry.py` — the composition root builds, then hands the built `Daemon` and the argument vector to `pm_ai/surfaces/cli/dispatch.py`, which maps subcommands onto core services. Three subcommands land here: `doctor`, `key enrol`, and `config show`.

**`4d` follows this slice, not the reverse:** `4d` adds `project add` to the dispatch table **this** slice creates, so the other order is circular. Until `4d` exists only `doctor` is usable on an unregistered machine, which is why the Always below requires it to survive a failed composition.

## Boundaries & Constraints

**Always:**
- **The composition root constructs; the CLI dispatches.** `[project.scripts] pm-ai = "pm_ai.app.entry:main"`. `surfaces` sits *below* `app` in the enforced layer stack, so `surfaces.cli` may not import `pm_ai.app` at all — which is the decisive reason the entry point cannot live in `surfaces`. The CLI receives what it needs and constructs no adapter.
- **The CLI holds no scheduler** (AD-7, enforced by the `cli-owns-no-scheduling` contract at `.importlinter:134-139`). Every subcommand runs once and exits. The 07:00 tick is the daemon's, in `9a`.
- **A bare `pm-ai` exits non-zero with usage.** CAP-18 makes bare invocation open a REPL, and that is `4e`; until then a bare call that silently succeeded would read as a working install.
- **The exit-code table is declared here and nowhere else.** Three slices map outcomes to codes (`8b`, `23b` reuse this); leaving each to choose makes `pm-ai doctor || alert` and `pm-ai dashboard || retry` behave differently. `0` success · `1` unexpected exception · `2` usage or unknown subcommand · `3` refusal (a stated, deliberate no) · `4` `doctor` reports an unhealthy machine. `8b` and `23b` reuse these values and may not add to the table.
- **`dispatch` annotates a Protocol, never `Daemon`.** `surfaces` may not import `pm_ai.app`, so an unannotated parameter is implicitly `Any` — story `1k`'s defect, in the wave's most branch-heavy module. The Protocol lives in `pm_ai/ports/` and names only `storage`, `keychain`, `config` and `scope`.
- **`doctor` runs even when composition fails.** It is the command for a broken machine, so a broken machine must not make it unreachable — an unregistered project, an unwritable root or an unparseable `config.toml` each become one reported probe result rather than a traceback.
- **All four `Health` states map explicitly.** `ABSENT` is not healthy (`doctor.py:64-72` says so: setup incomplete, encrypted writes will be refused), so it must not exit `0`.

**Ask First:** Any argument-parsing dependency. `argparse` is in the standard library and this surface is small; a dependency here would need a reason.

**Never:** No REPL (`4e`). No daemon and no loopback API (`4d`). No `dashboard` subcommand — that is `23b`, and it needs the renderer first. No business logic in `surfaces.cli`: it maps arguments to calls and formats results.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Bare invocation | `pm-ai` | usage printed, exit `2` | N/A |
| Subcommand group, no leaf | `pm-ai key`, `pm-ai config` | group usage printed, exit `2` | N/A — the groups exist in the table from the start, their leaves arrive with `4j` |
| Help requested | `pm-ai --help` | usage printed, exit `0`; `SystemExit` caught so `main()` returns a code | N/A |
| Composition fails | no registered project, or an unwritable root | `doctor` still reaches every probe not needing the failed component | reported as probe results |
| Unexpected exception | a bug anywhere below | exit `1`, distinct from `2` and `3` | traceback to stderr |
| Probe reports `ABSENT` | key not enrolled | exit `4` — setup is incomplete, which is not success | N/A |
| Diagnostics | `pm-ai doctor` | 1g's probes run and print | exit `4` if any probe is not healthy |
| Enrolment prompt | any invocation | nothing about the key is echoed or printed | N/A |
| Unknown subcommand | `pm-ai frobnicate` | usage printed, exit `2` | N/A |
| Config absent on a clean machine | no `config.toml` yet | `doctor` and `config show` both run; absence is a first-run state, not an error | `FileNotFoundError` translated here, never surfaced |
| Malformed config present | `pm-ai doctor` with unparseable `config.toml` | diagnostics still run and print — this slice does not load config before dispatch, so a broken one cannot hide a broken machine | `4i`'s probe reports the config itself; this slice only guarantees it does not block |

</frozen-after-approval>

## Code Map

- `pm_ai/app/entry.py` -- new; `main()`, the console script target
- `pm_ai/surfaces/cli/dispatch.py` -- new; argument mapping only
- `pyproject.toml` -- add the `[project.scripts]` table that does not exist
- `pm_ai/app/wiring.py:62` -- `build()`, called by `entry.main()` and by nothing else today; it already takes `keychain: KeychainPort | None = None` (`:69`)
- `pm_ai/app/wiring.py:37-47` -- `Daemon`, which gains the keychain field; `config` at `:47` carries a default and so fixes the insertion point
- `.importlinter:134-139` -- AD-7, the contract that fails if the CLI reaches a scheduler
- `.importlinter:148-156` -- AD-30, surfaces reach adapters only through core
- `pm_ai/platform/doctor.py` -- the diagnostics this story makes reachable

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/app/entry.py` -- add `main(argv=None)` building the daemon and delegating -- construction in the one layer permitted to do it
- [ ] `pm_ai/app/wiring.py` -- add `keychain: KeychainPort` to `Daemon`, **before** the defaulted `config` field (`wiring.py:47`), hoisting the adapter from the call argument at `wiring.py:140` -- `4j` and `4h` both need it and neither may construct it (`.importlinter:115-129`); appending after a defaulted field raises `TypeError` at class creation, breaking every `Daemon` construction in the suite
- [ ] `pm_ai/ports/__init__.py` -- declare the Protocol `dispatch` annotates, naming only the members the CLI touches -- `surfaces` may not name `Daemon`, and an implicit `Any` here is the one story `1k` retired
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- add the subcommand table, the exit-code mapping and `doctor` -- no adapter construction, no business logic; `4j` adds the remaining leaves
- [ ] `pm_ai/app/entry.py` -- translate `read_artifact`'s `FileNotFoundError` into the absent case -- it ends in `path.read_bytes()` (`service.py:1079`) with no `bytes | None` form, and `4a`, `4i` and `4h` all need absence as a value rather than an exception
- [ ] `pyproject.toml` -- declare `[project.scripts]`
- [ ] `tests/surfaces/test_cli_dispatch.py` -- one test per matrix row, `main()` called with an explicit argv, asserting the **exact** exit integer per row

**Acceptance Criteria:**
- Given `uv run pm-ai doctor`, then story 1g's probes execute and print — the diagnostics become reachable for the first time since they were built.
- Given a dispatcher that returns `1` for every non-success outcome, then the suite fails — each matrix row asserts its exact code, so a single collapsed value cannot pass.
- Given a machine with no registered project, then `pm-ai doctor` still prints a probe report rather than raising `UnknownProject`.
- Given `lint-imports` runs, then `cli-owns-no-scheduling` and both AD-30 contracts hold, and `pm_ai.surfaces` imports no module from `pm_ai.app`.
- Given `main()` is called with an unparseable `config.toml` in place and the argument `doctor`, then every probe still runs and prints — verifiable here without a config probe, because the guarantee is that dispatch does not load config first. What the *report* says about the config is `4i`'s criterion, at `4i`'s checkpoint.

## Spec Change Log

- **2026-09-03, split at the sizing gate.** Amending this slice against the second review took it to 2340 body tokens against wave 1's 1600. The three additive subcommands left for `4j` — `key enrol`, `config show`, `connector check` — each a dispatch entry over a service another slice already builds. What stays is the part that cannot be split: `[project.scripts]`, `entry.main()`, the daemon Protocol, the absent-case translation, the dispatch and exit-code tables, and `doctor`, the one command that must work on a broken machine and the reason the slice exists.
  One knock-on, in the right direction: `connector check` moving to `4j` means **`8d` no longer needs the CLI**, so it returns to being an independent starter and the build-order lengthening recorded on 2026-09-03 is undone. `4j` depends on `8d` instead.
  The `keychain: KeychainPort` field stays here rather than following `key enrol`, because it is a composition change this slice owns and both `4j` and `4h` consume it — putting it in `4j` would chain setup behind the connector work.

- **2026-09-03, amended against the second multi-lens review.**
  **`dispatch` could not name what it is handed** (C15). `surfaces` may not import `pm_ai.app`, so the daemon parameter was implicitly `Any` — story `1k`'s defect, in the wave's most branch-heavy new module — and this slice's Verification ran neither `uv run mypy` nor the full suite, the only two commands that would notice. A Protocol in `pm_ai/ports/` is now a task, and both commands are in the block.
  **`read_artifact` has no absent case, and three slices need one** (B4). It ends in `path.read_bytes()` with no `bytes | None` form, so the first `doctor`, `config show` and `setup` on a clean machine each raise out of the command that exists to survive a broken machine. Translating it is a task here, in the first slice that reads the file, with a matrix row for the clean-machine case.
  **The config-probe row belongs to `4i`** (C16). This slice's matrix asserted a probe result for an unparseable config while having no task touching `doctor.py` and no such probe existing. Rather than gain a `4i` dependency — which measurably lengthened the wave's critical path from seven slices to eight — the row and criterion are restated as what this slice can verify alone: dispatch does not load config before running the probes, so a broken config cannot block them. What the report *says* about the config is `4i`'s criterion at `4i`'s checkpoint.
  **`pm-ai connector check` joins the table deliberately** (D-3b). `8d`'s health probing became a separate command, and this slice's `Never` forbids other slices extending the exit-code table, so the entry is added here rather than improvised there.

- **2026-09-02, `wiring.py` citations re-pointed after story 4a.** 4a added one import to `wiring.py`, shifting every line below it, and a parameter plus a docstring paragraph to `build()`, shifting the rest further. The numbers below named other code. **Line numbers only — no wording, no intent, no task, and no acceptance criterion changed.**

- **2026-09-02, inherited `4b`'s daemon field, and two citations story 4a shifted.** The keychain field moved here from `4b`, whose frozen `Never: No daemon changes` forbade the task the wave-1 review had added to it; resolved by the human at the story-4a review gate. This slice is where the need is real — it is the one calling `enrol` from a surface that may not reach `keyring` — and it already owns composition through `entry.main()`.
  Two line citations drifted when 4a added the `config` field to `Daemon` and a parameter to `build()`: the Code Map's `wiring.py:47` was `build()` and is now the `config` field, with `build()` at `:62`. The contract citation inherited from `4b`'s task read `.importlinter:115-131` and is `115-129` — the range ends at `launchd`. That field also set a trap for this task, now stated in it: `config` carries a default and is last, so a non-default `keychain` appended after it raises `TypeError` at class creation rather than failing a test.

- **2026-09-02, multi-lens review.** Three gaps, one of them a hard blocker.
  **`pm-ai` could not have run once on a clean machine.** `build()` eagerly resolves the project scope (`wiring.py:129`, whose comment explains the eagerness) and an unregistered project raises `UnknownProject` (`paths.py:553`) — so every subcommand, `doctor` included, would have died before dispatch, defeating this slice's own Always about `doctor` surviving a broken environment. The registry had no owner in any story; it is now `4d`, and this slice depends on it. A criterion and a matrix row cover `doctor` on an unregistered machine regardless.
  **Exit codes were named nowhere.** This slice, `8b` and `23b` all said "non-zero" or "refusal exit code" and no integers, so each subcommand would have chosen its own convention and the stated distinction between "disallowed" and "pm-ai broke" would have been unobservable to the operator it exists for. The table is now declared here, the other two slices reuse it, and every matrix row asserts its exact value.
  The edge-case lens added the four paths a real operator hits first: a subcommand group with no leaf, `--help` raising `SystemExit` out of `main` (which would have broken the explicit-argv design), composition failing before dispatch, and `ABSENT` exiting `0` — the last being the summary an operator trusts while the morning briefing cannot decrypt anything.
## Design Notes

`main(argv=None)` takes its arguments rather than reading `sys.argv` inside, so every row of the matrix is a unit test and none needs a subprocess.

The layer stack does the arguing here. The obvious placement — a `main()` in `surfaces.cli`, where the CLI lives — cannot work: `surfaces` is below `app`, so importing `wiring.build` would invert the stack, and constructing a `StorageService` directly would break AD-30. Splitting entry from dispatch is what the enforced layering leaves, and it is also the better shape: the untestable part (real adapters) sits in one small module, and the part with branches sits in another with no I/O.

That a malformed config must not suppress diagnostics is worth stating because the natural implementation loads config first and dies. `doctor` is the command an operator runs when things are broken; it is the one command that must survive a broken config.

## Verification

**Commands:**
- `uv run pytest tests/surfaces/test_cli_dispatch.py -q` -- expected: all matrix rows pass
- `uv run mypy` -- expected: clean; this is the command that would notice an implicitly-`Any` daemon parameter
- `uv run pytest -q` -- expected: no new failures
- `uv run pm-ai doctor` -- expected: probe report printed; exit code reflects health
- `uv run pm-ai` -- expected: usage, non-zero exit
- `uv run lint-imports` -- expected: contracts kept
