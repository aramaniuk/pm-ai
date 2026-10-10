---
title: 'pm-ai transcript drop ingests a hand-supplied transcript'
type: 'feature'
created: '2026-10-10'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** With `11b` the ingestion path is real, but only a Graph harvest reaches it. AD-23 requires the pipeline to be exercisable end-to-end without a tenant, from a transcript somebody downloaded or typed — and there is no way to hand pm-ai such a file.

**Approach:** `pm-ai transcript drop <file> --scope <personal|project:<id>> [--meeting <id>]` takes a `.vtt` or `.txt`, binds it to a meeting record in that scope by `11b`'s rule — asking at the terminal for the title, start, attendees and duration a file does not carry — stores the file as a capture in that scope through the existing exclusion check, and runs `11b`'s ingestion. The watched-folder trigger needs story 10a's watcher, so the drop is a command.

## Boundaries & Constraints

**Always:**
- **`--scope` is required and names where the drop looks for and writes the meeting** — `personal` or `project:<id>` — never where pm-ai was started. `people:<id>` parses and is refused by name; so is a project that is not enrolled.
- **Binding is `11b`'s, in this order.** A named `--meeting <id>` wins and must exist in that scope; with it nothing is asked, so no terminal is needed. Otherwise the command asks for the title and start — a VTT from Teams carries neither — and matches by `11b`'s rule (same title, start within 15 minutes); two candidates refuse, naming both; none asks for the attendees as comma-separated handles and, for a `.txt`, the duration in minutes, then mints a manual-only record. For a `.vtt` the duration is the last cue's end, and the closing line says so. The start is a date-time with its offset (`2026-10-10T14:00+03:00`), stored as UTC; anything else is asked again naming that form. Attendees in a form the record's own rule refuses are asked again naming the rule. Without a terminal, a drop that would need to ask is refused before anything is written.
- **Binding writes nothing.** The bind step answers with the meeting — found, or minted in memory — and the record is first written by `11b`'s ingestion, after extraction. So a refusal at the file, the exclusion check or the capture leaves `meetings/` exactly as it was: no record, no capture.
- **The file must read to at least one utterance before anything is written.** `11b`'s readers decide by content, not by extension; the drop refuses only on their one answer "neither reader accepts this", and refuses by name a file that reads to zero utterances (a header-only VTT, cues with empty payloads). A path that is a directory, unreadable, a dangling link, or not UTF-8 is refused by name — never a traceback.
- **The file is copied into that scope's `transcripts/` through the existing capture path** — git is asked whether it is excluding the directory, a taken name is refused. The name is `33e`'s rule (the record's filename stem, then `-manual.vtt` or `-manual.txt`); no second rule. A meeting that already has any capture, Graph or manual, refuses the drop naming the existing capture: a transcript is verbatim input, never amended, and removing the capture is the way to drop again. The capture stays afterwards; the 30-day purge is NFR-09's.
- **A dropped file is `TranscriptSource.MANUAL`**: fully valid for extraction and staging, never an auto-execute source (AD-32). Every proposal cites the meeting (AD-33); nothing is executed, so the closing line counts what was staged and never says "executed".
- **Order: read, bind, capture, ingest.** A refusal at any step leaves nothing from the later ones. A capture already written when ingestion fails stays and the failure names it; on success the drop records the capture as ingested in the operational store's transcript table (`11b`'s), on failure it records nothing there.
- **Exit codes are `4c`'s:** `0` ingested, printing the capture path, the meeting id, matched or minted, and the count staged; `2` a command line pm-ai cannot read (`--scope` missing or `--scope alpha`, no file argument), answered with the one-line reminder of the syntax; `3` a deliberate refusal carrying its reason — every refusal row below; `1` a bug.

**Ask First:** Nothing. The syntax, the scopes and the binding were decided on 2026-10-09 and 2026-10-10.

**Never:** No `.docx` — needs a parser dependency; refused by name and recorded. No watched folder (story 10a). No `people:<id>` drops — a 1:1's transcript is a later slice. No re-ingest of a stored capture (deferred). No change to `11b`'s path, readers or binding rule, nor to `33e`'s naming rule. No Graph code.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Drop with a named meeting | `.vtt`, `--scope project:alpha --meeting <id>` recorded there; terminal or not | capture in alpha's `transcripts/`; proposals cite that meeting; nothing asked; exit 0 | N/A |
| Named meeting absent | `--meeting <id>` not in that scope | refused, naming id and scope; nothing written, no record | exit 3 |
| Title and start match one record | answered "Weekly Sync" at 09:07; record "weekly  sync" at 09:00 | bound; no new record; no further questions | N/A |
| Two candidates | two records within 15 minutes | refused, naming both ids; nothing written | exit 3 |
| No match, terminal present | attendees answered; duration answered for `.txt`, last cue's end for `.vtt` | manual-only `mtg_` record minted in the chosen scope; capture and proposals follow; the closing line names the duration's source | N/A |
| No match, no terminal | input is not a terminal | refused before anything is written; no record | exit 3 |
| An answer the question refuses | start `2026-10-09 14:00`; a handle carrying a comma | asked again, naming the form `2026-10-10T14:00+03:00`, or the attendee rule | N/A |
| Personal meeting while started in a project | `--scope personal`, pm-ai run inside alpha | record and capture in the personal tree; exit 0 | N/A |
| Meeting already has a capture | `<stem>-graph.vtt` or `<stem>-manual.txt` present | refused naming that capture; nothing re-ingested | exit 3 |
| Git cannot confirm exclusion | project `transcripts/` tracked, or git unreachable | refused with git's reason; nothing written, no record | exit 3 |
| Path unusable | missing, a directory, unreadable, a dangling link, or not UTF-8 | refused, naming the path and which; no traceback | exit 3 |
| Zero utterances | header-only VTT, cues with empty payloads, or whitespace only | refused by name before the capture; no record | exit 3 |
| `.docx` | any Word file | refused, naming the deferral | exit 3 |
| Neither reader accepts | no `WEBVTT` header, no cue timing, no `Speaker:` lines | refused, naming the file | exit 3 |
| `.txt` that is WebVTT | `WEBVTT` header under a `.txt` name | read as VTT; capture named `-manual.txt` | N/A |
| PM speaks a reversible command | voice tag equals the handle | staged, never executed (AD-32) | N/A |
| Ingestion fails after the capture | the record write refuses | capture kept and named; nothing staged; no ingested row | exit 3 |
| `--scope project:zeta` or `--scope people:bob` | not enrolled; declined | refused naming the project or the scope | exit 3 |
| `--scope alpha`, no `--scope`, or no file argument | not a readable command line | the syntax reminder | exit 2 |

</frozen-after-approval>

## Code Map

- `pm_ai/surfaces/cli/dispatch.py:547-607` -- `Command` (`takes`, `optional`, `options`, `target`); `:1583` `TABLE`; `:1077-1113` the `--scope` reading to reuse, which already parses `people:`; `:1116` `_dashboard`, the handler pattern (target from `parse`, refusals → `Refusal`); `:366` `Context`, `:340` `_no_dashboard`, the injection pattern; `:727` the no-terminal refusal; `:892` `_ask_until`, `:902` `_ask_minutes`, the re-asking questions; `:107-125` the exit codes
- `pm_ai/app/entry.py:138-141,177,261-286` -- `parsed.target` into `_compose`; `dashboard=_dashboard(daemon)`, the injection to copy for the drop
- `pm_ai/app/pipelines.py` -- `11b`'s bind step (returns a `Meeting`, writes nothing) and `run_transcript_ingestion` (writes the record, then stages); the drop sequence lives beside them
- `pm_ai/core/transcripts.py` -- `11b`'s readers and their "neither accepts" refusal
- `pm_ai/storage/service.py:1476` -- `write_capture(body, scope=, name=)` and its refusals; `:307` `_capture_name`, `:94` `CAPTURE_NAME_LIMIT = 128`; `transcript_state` (`33e`, `11b`) for the ingested row
- `pm_ai/core/meeting_records.py:317` -- `record_name`; `capture_name(meeting_id, source)` and the prefix presence check beside it (`33e`); `:36-38` the attendee grammar (comma reserved) the re-ask names
- `pm_ai/domain/storage_tiers.py:314` -- `UnprotectedCaptureDir`, the git refusal the command passes through verbatim
- `pm_ai/domain/transcripts.py:13-19` -- `TranscriptSource.MANUAL`
- `tests/surfaces/test_cli_subcommands.py` -- the `connector add` rows (monkeypatched `isatty` and input) as the pattern; `tests/slice/test_connector_add_graph.py` -- a slice test through `main()` against a temporary home; `tests/conftest.py:58-60` why the skip count is a delta (this slice moves it by nothing)

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/app/pipelines.py` -- the drop sequence: read to utterances (refuse on zero or on neither reader), bind (`11b`, a value), copy through `write_capture` into the meeting's scope under `33e`'s name after the prefix presence check, ingest, record ingested; refusals surfaced by type -- one function the command calls
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- `transcript drop <file> --scope <scope> [--meeting <id>]` in `TABLE`; path checks (missing, directory, unreadable, dangling link, not UTF-8, `.docx`); enrolled-project check; the questions, re-asked with the reason (offset form, attendee rule); `.vtt` duration from the last cue; `people:` refused; every refusal → `Refusal`; the exit-0 sentence counting staged -- the command
- [ ] `pm_ai/app/entry.py` -- inject the drop bound to this daemon, as `dashboard` is -- `surfaces` may not reach `app`
- [ ] `tests/surfaces/test_cli_subcommands.py`, `tests/slice/test_transcript_drop.py` -- one test per matrix row, the slice ones through `main()` against a temporary home; every "nothing written" row lists `meetings/` and `transcripts/` afterwards and asserts both unchanged -- the matrix is the contract

**Acceptance Criteria:**
- Given a project enrolled and a `.vtt` whose voice tag equals the PM's handle saying `pm-ai, post_comment gitlab:alpha:issue:102 ship it`, when `pm-ai transcript drop` runs with `--scope project:alpha` and answers that match no record, then a `mtg_` record exists in alpha's `meetings/`, `<stem>-manual.vtt` exists in alpha's `transcripts/`, one proposal is staged citing `meeting:<that id>`, nothing is executed, the closing line says one staged and the duration taken from the last cue, and the exit is 0.
- Given the same drop run again, then it is refused naming the existing capture, the exit is 3, and no second proposal exists.
- Given a project whose `transcripts/` git would commit and a `.txt` matching no record, when the drop runs and the questions are answered, then the exit is 3 with git's reason and `meetings/` holds no new record.
- Given `uv run pytest`, then everything passes and the skip count is unchanged by this slice.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_transcript_drop.py tests/surfaces/test_cli_subcommands.py -q` -- expected: every matrix row passes
- `uv run pytest -q` -- expected: all pass; the skip count unchanged by this slice
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- `HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai transcript drop sample.vtt --scope personal` on a fresh home (no project needed): asks title, start, attendees; prints the capture path, the minted id and the count staged; exit 0. Run again: exit 3 naming the capture.
