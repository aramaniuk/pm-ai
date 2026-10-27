# pm-ai end-to-end scenario

The recorded walk-through that the wave-end automated test replays. Every step
drives the real `pm-ai` binary (`.venv/bin/pm-ai`) as a subprocess against a
throwaway home, with `PM_AI_DISABLE_ENCRYPTION=1` and its own `daemon_port`, and
the real local model (Ollama, qwen3:8b). Nothing here touches `~/.pm-ai`,
`~/.manager-ai` or the login keychain: `setup` and `key enrol` are never run,
because they are the only commands that write the keychain.

All data is fictional. Steps marked **(B)** come from the guided session on the
real machine and are replayed here with fictional inputs.

Status column: `recorded` = run by hand and the expectation written down;
`automated` = replayed by `tests/e2e/test_wave_e2e.py`.

## Setup

| # | Step | Expectation | Status |
|---|---|---|---|
| S1 | Make `<home>/.pm-ai/config.toml` with `display_timezone = "Europe/Warsaw"`, `daemon_port = <free port>`, `local_model = "qwen3:8b"` | — | recorded |
| S2 | `doctor` before the daemon | runtime, sqlite extension, git, config, local model `ok`; registry `absent`; daemon "not running"; exits non-healthy only for the keychain and registry | automated |
| S3 | Make a git repo `proj-alpha`; `project add <path> alpha` | "alpha is onboarded", `.gitignore` written, restart hint | automated |
| S4 | `daemon run` in the background | "listening on 127.0.0.1:<port>" within 10 s; log has the catch-up and index-at-start lines | automated |

## Transcripts and summaries

| # | Step | Expectation | Status |
|---|---|---|---|
| T1 | `transcript drop <vtt> --scope personal --dry-run` | prints title/start/length/participants, writes nothing | automated |
| T2 | `transcript drop <vtt> --scope personal --title ...` | stored and ingested; new manual-only meeting id; next command printed | automated |
| T3 | `transcript list` | the meeting with transcript `ingested`, summary waiting, and the summarize command | automated |
| T4 | `transcript summarize --scope personal --meeting <id>` | summarised by qwen3:8b; ≥1 decision and ≥1 action item staged; under 120 s (36 s measured) | automated |
| T5 | `transcript drop <txt> --scope project:alpha --start <ISO with offset> --duration 15` | stored against project:alpha with the given start | automated |
| T6 | `transcript drop <md> --dry-run` | participants read from `Name:` lines | automated |
| T7 | `.docx` drop | not yet run | todo |

## Proposals

| # | Step | Expectation | Status |
|---|---|---|---|
| P1 | `proposal list --scope personal` | one card per action item and spoken promise, each with approve/edit/reject commands; under 1 s | automated |
| P2 | `proposal edit <id> --owner <name> --due friday` | `<id>_e1` replaces it | automated |
| P3 | `proposal reject <id>` | rejected; never becomes a commitment | automated |
| P4 | `proposal approve <id>` (two action items, one spoken promise, the `_e1`) | each "recorded as a commitment" | automated |
| P5 | `proposal reject <approved id>` | refused, offers withdrawn / altered / edit | automated |
| P6 | `proposal approve <rejected id>` | refused, nothing changed | automated |

## Commitments

| # | Step | Expectation | Status |
|---|---|---|---|
| C1 | `commitment list` / `--json` | open ones, soonest due first; JSON `format: 1`; under 1 s | automated |
| C2 | `commitment due <id> friday`; `due <id> tuesday` on a Tuesday; `due <id> 2026-11-09` | dates set "by you"; same weekday means next week | automated |
| C3 | `commitment owner <id> "<name>"` | owner set | automated |
| C4 | `commitment owner <id> <email>` | refused (AD-34) | automated |
| C5 | `commitment owner <id> @handle` | should be refused; **accepted today** (finding F7) | automated (xfail) |
| C6 | `done` → `undo` → `undo` again | reopened, then "already undone, nothing written" | automated |
| C7 | `broken --if-version <stale>` | refused, names what changed | automated |
| C8 | `altered --what`, `withdrawn --note`, `broken --note` | states change; closed ones leave the open list | automated |
| C9 | `commitment show <unknown> --json` | `outcome: refused`, `code: not_found` | automated |
| C10 | undo at 4 h 01 | needs a controllable clock; in-process check only | todo |
| C11 | every verb under 2 s | 1.0–1.3 s measured | recorded |

## Goals, members, pages

| # | Step | Expectation | Status |
|---|---|---|---|
| G1 | `goal set <id> --domain project --horizon medium --title ...`, `list`, `show`, `update`, `delete --yes` | round trip; dashboard Milestones lists it | automated |
| M1 | `member unmatched` / `member name` with `--scope personal` | refused: needs a project scope | automated |
| M2 | `member name <label> <name> --scope project:alpha` with no GitLab harvest | refused: name not a GitLab member | recorded |
| D1 | `dashboard` and `dashboard --scope project:alpha` | written; open-commitments block, count line per meeting, closed line, goals | automated |
| D2 | the "and N more" overflow (> 10 open), confidential item | not yet run | todo |
| L1 | `logs --lines 40`, `logs --level error` | no errors | automated |
| L2 | `doctor` with the daemon up | daemon `running`, renders listed | recorded |

## Open findings replayed as expected failures

| Finding | Test | Flips when |
|---|---|---|
| F1 | `test_f1_absolute_due_resolves_on_a_dropped_file` | an ISO due resolves on a meeting whose start was estimated |
| F6 | `test_f6_next_weekday_resolves` | `next friday` names a date |
| F7 | `test_f7_owner_handle_refused` | `@handle` is refused as an owner |

Track B steps that need the real tenant (Graph sign-in, harvest, category routing) are not replayed; their findings are in `findings-2026-10-27.md`.
