---
title: 'CLI entry point and subcommand dispatch'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `pyproject.toml` declares no `[project.scripts]` and `pm_ai/surfaces/cli/__init__.py` is a one-line docstring. Nothing in this repository can be run. Story 1g built a full diagnostics suite that no operator can reach, and `4a`/`4b` add a config reader and an enrolment service with no way to invoke either.

**Approach:** Console script at `pm_ai/app/entry.py` — the composition root builds, then hands the built `Daemon` and the argument vector to `pm_ai/surfaces/cli/dispatch.py`, which maps subcommands onto core services. Three subcommands land here: `doctor`, `key enrol`, and `config show`.

## Boundaries & Constraints

**Always:**
- **The composition root constructs; the CLI dispatches.** `[project.scripts] pm-ai = "pm_ai.app.entry:main"`. `surfaces` sits *below* `app` in the enforced layer stack, so `surfaces.cli` may not import `pm_ai.app` at all — which is the decisive reason the entry point cannot live in `surfaces`. The CLI receives what it needs and constructs no adapter.
- **The CLI holds no scheduler** (AD-7, enforced by the `cli-owns-no-scheduling` contract at `.importlinter:134-139`). Every subcommand runs once and exits. The 07:00 tick is the daemon's, in `9a`.
- **A bare `pm-ai` exits non-zero with usage.** CAP-18 makes bare invocation open a REPL, and that is `4e`; until then a bare call that silently succeeded would read as a working install.
- **Refusal and crash have different exit codes.** An operator scripting against this needs to tell "you asked for something disallowed" from "pm-ai broke".

**Ask First:** Any argument-parsing dependency. `argparse` is in the standard library and this surface is small; a dependency here would need a reason.

**Never:** No REPL (`4e`). No daemon and no loopback API (`4d`). No `dashboard` subcommand — that is `23b`, and it needs the renderer first. No business logic in `surfaces.cli`: it maps arguments to calls and formats results.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Bare invocation | `pm-ai` | usage printed, non-zero exit | N/A |
| Diagnostics | `pm-ai doctor` | 1g's probes run and print | non-zero if any probe fails |
| Enrolment | `pm-ai key enrol` | `4b`'s service invoked | refusal exit code, message from the service |
| Config shown | `pm-ai config show` | the loaded `Config`, defaults marked as such | refusal exit code on `ConfigRefused` |
| Unknown subcommand | `pm-ai frobnicate` | usage printed, non-zero exit | N/A |
| Malformed config present | `pm-ai doctor` with unparseable `config.toml` | diagnostics still run — a broken config must not hide a broken machine | reported as a probe result |

</frozen-after-approval>

## Code Map

- `pm_ai/app/entry.py` -- new; `main()`, the console script target
- `pm_ai/surfaces/cli/dispatch.py` -- new; argument mapping only
- `pyproject.toml` -- add the `[project.scripts]` table that does not exist
- `pm_ai/app/wiring.py:47` -- `build()`, called by `entry.main()` and by nothing else today
- `.importlinter:134-139` -- AD-7, the contract that fails if the CLI reaches a scheduler
- `.importlinter:148-156` -- AD-30, surfaces reach adapters only through core
- `pm_ai/platform/doctor.py` -- the diagnostics this story makes reachable

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/app/entry.py` -- add `main(argv=None)` building the daemon and delegating -- construction in the one layer permitted to do it
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- add the subcommand table and exit-code mapping -- no adapter construction, no business logic
- [ ] `pyproject.toml` -- declare `[project.scripts]`
- [ ] `tests/surfaces/test_cli_dispatch.py` -- one test per matrix row, `main()` called with an explicit argv

**Acceptance Criteria:**
- Given `uv run pm-ai doctor`, then story 1g's probes execute and print — the diagnostics become reachable for the first time since they were built.
- Given `lint-imports` runs, then `cli-owns-no-scheduling` and both AD-30 contracts hold, and `pm_ai.surfaces` imports no module from `pm_ai.app`.
- Given `main()` is called with an unparseable `config.toml` in place and the argument `doctor`, then probes still run and the config failure is one reported result among them.

## Design Notes

`main(argv=None)` takes its arguments rather than reading `sys.argv` inside, so every row of the matrix is a unit test and none needs a subprocess.

The layer stack does the arguing here. The obvious placement — a `main()` in `surfaces.cli`, where the CLI lives — cannot work: `surfaces` is below `app`, so importing `wiring.build` would invert the stack, and constructing a `StorageService` directly would break AD-30. Splitting entry from dispatch is what the enforced layering leaves, and it is also the better shape: the untestable part (real adapters) sits in one small module, and the part with branches sits in another with no I/O.

That a malformed config must not suppress diagnostics is worth stating because the natural implementation loads config first and dies. `doctor` is the command an operator runs when things are broken; it is the one command that must survive a broken config.

## Verification

**Commands:**
- `uv run pytest tests/surfaces/test_cli_dispatch.py -q` -- expected: all matrix rows pass
- `uv run pm-ai doctor` -- expected: probe report printed; exit code reflects health
- `uv run pm-ai` -- expected: usage, non-zero exit
- `uv run lint-imports` -- expected: contracts kept
