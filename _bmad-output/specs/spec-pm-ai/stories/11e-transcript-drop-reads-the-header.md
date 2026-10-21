---
title: 'transcript drop reads the title, start and length from the file, and asks only what is missing'
type: 'feature'
created: '2026-10-21'
status: 'draft'
baseline_commit: '278296c39567e4fc886cb5c011685ec2491fd9bf'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Since `11d`, `transcript drop` reads a Teams `.docx` export, but it still asks for the meeting's title, start, attendees and duration, although the export's first three lines hold the title, the start date and time, and the length. Andrei hit this on his first real drop (2026-10-21) and called it poor; his standing rule is that a command never asks for what its input already says. `11d`'s review recorded the gap (`deferred-work.md`, "Surfaced by the story-11d review": "A `.docx` export's header holds the start date and time and the meeting's length (`NNm NNs`), but the drop still asks for both").

**Approach:** The drop reads the title, start and length from the lines before the first turn of a file read by `11d`'s speaker-and-time reader — a Teams `.docx` and its `.md` conversion — and keeps reading the length from a WebVTT file's last cue, as today. The start has no UTC offset in the file, so it is read as a time in `config.toml`'s `display_timezone`, and the offset used is shown. In place of the questions, the person sees one confirmation line naming what will be used and can accept it, correct a field, or stop. Only what the file does not say, or says in a way pm-ai cannot read, is asked. `--title`, `--start` and `--duration` still win over the file. Andrei's decision of 2026-10-21. Everything else is `11d`'s and `11c`'s: readers, binding, capture, ingestion, the questions asked where the person is and never by the daemon (AD-21). Amends `11c`'s and `11d`'s "the duration asked for anything that is not WebVTT", and fixes half of `11d`'s deferred entry on `--duration` being dropped without a word for a WebVTT file.

## Boundaries & Constraints

**Always:**
- **Where the header is.** Only a file read by `11d`'s speaker-and-time reader has one: its non-blank lines before the first turn (`11d`'s definition), whether the text came from a `.docx` or is a `.md`. Markdown's marks are set aside on a header line as `11d` sets them aside on a speaker line. WebVTT and `Speaker: words` files have no header; for WebVTT the last cue still gives the length (`11c`), and for both the title and start are asked as today.
- **What the header says, line by line.** Each header line is judged on its own, whole:
  - *The start line* is a day, an English month name, a four-digit year, a comma, then a 12-hour time with `am` or `pm`: `14 September 2026, 09:30am` (the form measured in the recorded export; `am`/`pm` in either case; a one-digit day or hour accepted). Any other date form — month first, a 24-hour time, another language — is not a start line.
  - *The length line* is minutes and seconds, `29m 03s` (measured). Hours before them, `1h 02m 03s`, are also read; that form is a guess at how Teams writes a meeting over an hour, unmeasured, and covered by tests only. The length is rounded up to whole minutes, as a last cue is (`29m 03s` is 30), and is used only within `11c`'s 1 to 1,440 minutes.
  - *The title line* is any other header line except one holding only the word `Transcript`. When it ends with Teams' recording stamp, `-YYYYMMDD_HHMMSS-Meeting Recording` (measured), that ending is removed, so `Example Weekly Sync-up-20260914_093000-Meeting Recording` gives `Example Weekly Sync-up`, the title a calendar record of the same meeting carries. The stamp's date and time are never read as the start.
  - A field is read only when exactly one line gives it. Two start lines, two length lines or two title lines leave that field unread; the others are still read.
- **The start's zone.** The start line carries no UTC offset, so it is read as a wall-clock time in `display_timezone`, and the confirmation line shows the offset that gives (`+02:00`). When `display_timezone` is not set, the start is not read: it is asked, and the question says that setting `display_timezone` lets pm-ai read it from the file. A time that occurs twice or not at all in that zone, at a clock change, is not read either: it is asked, saying why.
- **What is used, field by field:** an option given (`--title`, `--start`, `--duration`, `--attendees`) first; else what the file says; else, at a terminal, the question `11c` and `11d` ask; else, without a terminal, `11c`'s refusal naming the missing option, before anything is written. `--duration` now also wins over a WebVTT file's last cue. As today, a duration and attendees are used only when no recorded meeting matches and a new record is made.
- **One confirmation line, where the person is.** At a terminal, when at least one of title, start or duration was read from the file, the questions for those fields are replaced by one line, asked after any question for a field the file did not give: `Example Weekly Sync-up, 2026-09-14 09:30 (+02:00), 30 min — store it? [Y/n/e] `. Values given as options are shown in it too. Enter or `y` uses them; `n` stores nothing and ends the drop like Ctrl-C at a question today ("Nothing was written."); `e` asks each field in the line again, one at a time, showing the value read, Enter keeping it and a typed answer judged as that field's question judges it today; then the drop goes on without a second confirmation. Any other answer asks the line again. With nothing read from the file — a `.txt`, or a WebVTT whose title and start are typed — there is no confirmation line, and the drop asks exactly what it asks today. At the prompt the line is asked in the prompt's process and the values sent to the daemon as options, as `11d` sends its answers; the daemon never asks (AD-21). Without a terminal there is no line: what the file says is used.
- **Attendees: pending Ask First 1, written here as recommended (a).** The file lists speakers by display name; attendees are person handles (AD-34), and pm-ai never turns a display name into a person by guessing. So a dropped transcript gives no attendees: the attendees question is no longer asked, a new record has none unless `--attendees` is given, and speakers stay what they are today, the utterances' speaker labels.
- **A header pm-ai cannot read is not a refusal.** An unreadable or partial header leaves those fields to the question, or to the refusal without a terminal, exactly as if the file had not said them. Nothing about the header refuses a drop, and its lines are still never utterances.
- **The same rule in both processes.** The prompt decides what to ask, and the daemon what to use, by one function reading the header, so the prompt asks a question exactly when the drop would need its answer (as `11d` does for the duration with `carried_minutes`).
- **The fixture names no one.** New test files use only the names and words `11d`'s fixture already uses (`Example Weekly Sync-up`, `Alex Morgan`, `Sam Rivera`, placeholder lines). No line from a real export is added.

**Ask First:**
1. **Attendees** (before the first task). (a) **Recommended.** Attendees stay empty unless `--attendees` is given, and speakers are not attendees. No attendee question; a minted record shows "0 attendees" on the dashboard until a calendar harvest adopts it (`11b`'s adoption brings the calendar's attendees). (b) Speakers' display names are recorded as attendees as plain name labels, not handles. `Meeting.attendees` holds handles only (`identity.py` says so), so this needs a new kind of attendee entry in the record and in `meetings/`; a name label is never resolved to a person. (c) Ask once, offering the speakers' names: "Speakers: Alex Morgan, Sam Rivera. Attendees as handles (Enter for none)". It is still a question, and the names cannot be accepted as answers because they are not handles. (a) is recommended because it is the only one that both asks nothing and adds no guess: the speakers are already kept on every utterance, and a calendar record carries the real attendees.
2. If the recorded fixture's header lines differ from the three forms above once read through `docx_text` (for example the title line carries more than the stamp), before writing the header rule.

**Never:** No guessing a person from a display name. No reading the recording stamp in the title as the start. No start used without its offset shown at a terminal. No default zone when `display_timezone` is unset (UTC or the machine's zone): the start is asked. No question asked by the daemon. No refusal caused by the header alone. No change to the readers' choice, `11b`'s binding, the capture name rule or the sanitization boundary. No real names or spoken words in a fixture or test.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Teams `.docx`, all read | the recorded fixture, `display_timezone = "Europe/Warsaw"`, a terminal | one line `Example Weekly Sync-up, 2026-09-14 09:30 (+02:00), 30 min — store it? [Y/n/e]`; Enter: no other question; minted record titled `Example Weekly Sync-up`, start 07:30 UTC, 30 min, no attendees | N/A |
| `.md` conversion | the fixture's `.md`, same zone | the same line and record | N/A |
| Matches a calendar record | the fixture, a record `Example Weekly Sync-up` at 07:35 UTC in the scope | the line, then binds to it; nothing minted | N/A |
| Correcting a field | the line answered `e`; title kept with Enter; start typed `2026-09-14T09:45+02:00`; duration kept | record uses the typed start, the read title and duration | a refused answer is asked again, as today |
| Stopped | the line answered `n` | "Nothing was written."; `meetings/` and `transcripts/` unchanged | exit 3 |
| Other answer | the line answered `x` | the line asked again | N/A |
| Zone unset | the fixture, no `display_timezone` | start asked, the question naming `display_timezone`; title and duration still read and shown in the line | N/A |
| Clock change | a start line `25 October 2026, 02:30am` in Europe/Warsaw | start asked, saying the time occurs twice in that zone | N/A |
| Title only | a `.docx` whose header is only the title line | line shows the title after the start and duration questions | N/A |
| Unreadable start | `September 14, 2026, 9:30 AM` | not a start line: start asked; title and length read | N/A |
| Two titles | two non-date, non-length header lines | title asked; start and length read | N/A |
| No stamp | title line `Example Weekly Sync-up` | title `Example Weekly Sync-up` | N/A |
| Hours | `1h 02m 03s` | 63 min | N/A |
| Short and long lengths | `0m 20s`; `25h 00m 00s` | 1 min (rounded up); over 1,440 min not used, duration asked | N/A |
| Options win | the fixture with `--title "Other" --start 2026-09-14T10:00+02:00 --duration 45` | the line shows the options' values | N/A |
| `--duration` with WebVTT | a `.vtt` whose last cue ends at 31 min, `--duration 45` | a minted record has 45 min | N/A |
| Plain WebVTT | a `.vtt`, no options, terminal | title and start asked as today; no confirmation line | N/A |
| `.txt` | `Speaker: words` | asked as today; no line | N/A |
| No terminal, all read | the fixture through a pipe, zone set | no question, no line; minted from the header | N/A |
| No terminal, start unread | the fixture through a pipe, zone unset | refused naming `--start <start>`; nothing written | exit 3 |
| At the prompt | the fixture dropped at `pm-ai>`, daemon running | the line asked in the prompt's process; the daemon receives `--title=`, `--start=`, `--duration=` and asks nothing | N/A |
| Header lines | the fixture's three header lines | never utterances (unchanged from `11d`) | N/A |
| Attendees given | the fixture with `--attendees email:a@example.com` | a minted record has that attendee | N/A |

</frozen-after-approval>

## Code Map

Anchors checked against `main` at `278296c`.

- `pm_ai/core/transcripts.py:428` -- `_read_turns`, where the lines before the first turn are the header and are dropped; `:389` `_unmarked`, the Markdown marks set aside; `:402` `_turn`, the speaker-and-time line; `:202` `last_cue_end_minutes`, rounded up; `:508` `carried_minutes`, the one rule the prompt and the daemon share for the duration; `:184-185` `MIN_DROP_MINUTES` and `MAX_DROP_MINUTES`
- `pm_ai/core/docx_text.py:169` -- `docx_text`: each header paragraph comes out as one line (measured on the fixture: the title line, the date line, the length line, then a blank line before the first turn); unchanged
- `pm_ai/surfaces/cli/dispatch.py:3255` -- `DROP_ANSWERS`, the four options; `:3377` `_given`, each option judged as its question judges; `:3434` `_drop_identify`, the title and start asked or refused; `:3466` `_ATTENDEES_QUESTION`; `:3473` `_drop_describe`, attendees and duration asked or refused; `:3653` `_drop_left_to_ask`, whether the prompt may ask; `:3686` `ask_drop_here`, the prompt's questions (`:3727` attendees, `:3733` duration via `carried_minutes`); `:3752` `_transcript_drop`, `:3793-3794` the `identify` and `describe` callbacks handed to the pipeline
- `pm_ai/surfaces/cli/repl.py:655` -- the prompt calls `ask_drop_here` only when `drop_would_ask`
- `pm_ai/app/pipelines.py:1326` -- `drop_transcript`; `:1375` `from_cues = carried_minutes(...)`; `:1471` `_bind_drop`, `:1491` `identify()`, `:1503` `describe(from_cues is None)` and the next line, where the last cue wins over a typed duration; `:2419` `zone_of`, which refuses an unset `display_timezone` rather than defaulting
- `pm_ai/core/config.py:264` -- `display_timezone`, `""` when unset
- `pm_ai/domain/identity.py:328` -- `Meeting.attendees` holds handles only (Ask First 1)
- `tests/core/fixtures/transcripts/` -- `teams_export_recorded.docx`, `.md` and `README.md`: header `Example Weekly Sync-up-20260914_093000-Meeting Recording`, `14 September 2026, 09:30am`, `29m 03s`

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/transcripts.py` -- the header read: title, local start (no zone) and minutes from the lines before the first turn, each only when exactly one line gives it, the recording stamp removed; one function used by both the prompt and the daemon -- I/O-free
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- the order option, file, question, refusal for each field; the zone applied from `display_timezone`, with the clock-change and unset cases asked; the confirmation line with `y`, `n`, `e`; the attendee question removed (per Ask First 1); `ask_drop_here` asks the same and sends the values as options -- where the person is
- [ ] `pm_ai/app/pipelines.py` -- a given `--duration` wins over the last cue; the header's minutes used when the file carries them; the daemon uses the header with its own config when no option is sent -- the daemon never asks
- [ ] `tests/core/test_transcript_readers.py`, `tests/slice/test_transcript_drop.py`, `tests/slice/test_project_add_and_drop_through_daemon.py` -- one test per matrix row; header variants built in the test from the fixture's names; every "nothing written" row lists `meetings/` and `transcripts/` before and after -- the matrix is the contract
- [ ] `_bmad-output/specs/spec-pm-ai/stories/11c-transcript-drop-command.md`, `11d-transcript-drop-formats-and-paths.md` -- when this slice is built: a dated Change Log line in each naming what this slice amends (the duration asked for anything not WebVTT; the attendee question) -- earlier frozen blocks say they were amended
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark the 11d entry on the header as done by `11e`, and the `--duration`-with-WebVTT half of the next entry

**Acceptance Criteria:**
- Given the recorded `.docx` fixture, `display_timezone = "Europe/Warsaw"` and a terminal, when `pm-ai transcript drop fixture.docx --scope personal` runs and the line is accepted with Enter, then no other question is asked, a `mtg_` record titled `Example Weekly Sync-up` starts at 2026-09-14 07:30 UTC and lasts 30 minutes, and the exit is 0.
- Given the same fixture at `pm-ai>` with the daemon running, when it is dropped and accepted, then the daemon receives the values as options and asks nothing.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged by this slice.

## Spec Change Log

- **2026-10-21, created** from Andrei's decision of 2026-10-21, after his first real drop of a Teams `.docx`, and `11d`'s deferred entry on the header. Decisions it carries: the header is the lines before the first turn of a file read by the speaker-and-time reader; the start, length and title lines defined from the measured fixture, the recording stamp removed from the title and never read as the start; a field read only when one line gives it; the start read in `display_timezone` with the offset shown, asked when the zone is unset or the time falls in a clock change; option, then file, then question, then refusal, for each field; `--duration` wins over a last cue; one confirmation line with accept, stop and edit, only when something was read; the daemon never asks; an unreadable header is never a refusal; attendees written as Ask First 1's recommended (a), pending Andrei's answer. Amends `11c`'s and `11d`'s "the duration asked for anything that is not WebVTT" and `11d`'s attendee question at the prompt — a task adds a dated line to each spec's Change Log.

## Design Notes

The header is read only where `11d`'s turn reader already finds it, so nothing new decides which lines are header and which are speech. The start's offset is shown because it decides binding: `11b` matches a calendar record within 15 minutes of the start, so a start read in the wrong zone mints a second record of the same meeting instead of binding — the shown offset is the one place the person can see that before it happens. An unset zone is asked for rather than defaulted, as `zone_of` already refuses to default for the dashboard. The recording stamp's time is not used because its zone is not known from one export. Without a terminal the file's values are used with no line, as options are today: a pipe has no one to confirm, and refusing would ask a script for what the file says. With (a), `11d`'s accepted limit that the prompt asks for attendees before knowing whether a record matches goes away, because the question goes.

## Verification

**Commands:**
- `uv run pytest tests/core/test_transcript_readers.py tests/slice/test_transcript_drop.py tests/slice/test_project_add_and_drop_through_daemon.py -q` -- expected: every matrix row passes
- `uv run pytest -q` -- expected: all pass; the skip count unchanged by this slice
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- `HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai transcript drop <a real export>.docx --scope personal` on Andrei's machine (never committed) -- expected: one confirmation line with the meeting's title, start and length; Enter stores it; exit 0
