---
title: 'A reference set scores meeting summaries'
type: 'feature'
created: '2026-10-27'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/7b-a-transcript-yields-summary-decisions-and-actions.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Wave 4 changes the local meeting summary four times (7g window and timestamps, 7h speaker/time/quote per item, 7i the decision/action/owner prompt, 7j parts and a merge pass), and nothing can tell whether a change made the decisions and actions better or worse. `benchmarks/meeting_summary.py` (7b) measures outcome, tokens, time and Cyrillic share but scores no content; recall exists only in `benchmarks/local_model.py`, against one fictional meeting with planted tokens. Real meetings have no planted tokens.

**Approach:** A hand-run script summarises every transcript in a private reference folder through 7b's own path, compares the returned decisions and action items with the reference items Andrei corrected, and prints counts and rates only. Matching is by word groups written in each reference item — no model judges the match. The repo carries the scorer, its tests and one fictional reference; the real references never enter it.

## Boundaries & Constraints

**Always:**
- The reference folder is a command-line argument. The script refuses a folder inside the repo's working tree or one whose mode lets group or others in (anything but `0700`), naming the rule and nothing else.
- Standard output and errors never carry transcript text, answer text, a reference's `about` note, a match word, or 7b's `reason` sentence. They carry the meeting's file stem, reference ids, numbers and enum values. The orchestrator names the copied files neutrally (`m01.vtt`, …).
- Each run writes one run file, mode `0600`, into `<folder>/runs/`: the commit sha and whether the tree was dirty, the model, the window, per-meeting rows, totals, 7b's `reason` per meeting, and the returned items' text — so misses can be read inside the private folder (question 2).
- Summaries run through `summarize_meeting` in a throwaway storage root (a temporary directory, deleted after the run), never `~/.pm-ai`, exactly as `meeting_summary.py` does. Temperature 0 and the fixed seed already make a run repeatable, so one run per meeting.
- A meeting whose summary is not made (`failed`, `timed out`, `nothing said`) scores every reference item as missed and is counted in `meetings_not_summarised`. `waiting` (Ollama absent, model not pulled) stops the run before scoring anything.
- Totals pool items across meetings (each reference item weighs the same); per-meeting rows are printed too.

**Ask First:** Nothing beyond the questions below.

**Never:** a model or Claude in the matching; a pass/fail threshold inside the script (question 4); reading or copying real meetings (the orchestrator does that, outside this slice); the columns 7h's new fields need (question 3); changing 7b's pipeline or `local_model.py`'s fixture key.

## Reference format and matching

One `<name>.reference.toml` per meeting beside its transcript (`.vtt` is written as a Graph capture, `.txt` as a manual text capture):

```toml
transcript = "m01.vtt"
[[decision]]                    # chosen or ruled out (wave 4's definition)
id = "D1"
about = "free note, never printed"
match = [["ORB-512"], ["flag", "флаг"]]
[[action]]
id = "A1"
owner = ["Speaker 2"]           # accepted spellings of who took it on
match = [["ORB-533"]]
[[not_an_item]]                 # raised, never decided: must not come back
id = "X1"
match = [["svelte"]]
```

- Text is case-folded, `ё` read as `е`, whitespace collapsed. A returned item matches a reference item when every group has at least one word that is a substring of it — substrings let a stem like `миграц` cover Russian endings. A decision's text is its `statement`; an action's is `what` and `due`. The owner is never part of the match.
- Each reference item, in file order, takes the first not-yet-taken returned item of its own kind that matches. One returned item counts for at most one reference item; a duplicate counts as unmatched.
- Per kind: recall = reference items taken / reference items; precision = returned items taken / returned items. An unmatched returned item may be a real item the reference lacks — Andrei then adds it to the reference and re-scores (`--rescore`).
- Owner correct = taken actions whose owner, normalised the same way, equals one accepted spelling / taken actions. A missing owner is wrong.
- Restated as the other kind: a returned decision not taken that matches any reference action, and a returned action not taken that matches any reference decision — counted separately.
- `not_an_item` hits: returned items of either kind, not taken, matching one.
- A group none of whose words occurs in the transcript is printed as a warning (`m01 D1 group 2`) — usually a typo in the reference.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Score a folder | 0700 folder outside the repo, references valid, Ollama up | one JSON row per meeting, one totals row, run file written | N/A |
| Folder in repo or wrong mode | path under the repo, or mode `0755` | refuses before any model call | exit 2, rule named |
| Bad reference | missing `match`, empty group, duplicate id, missing transcript, unknown table | refuses before any model call | exit 2, file stem and id named |
| Ollama absent | `waiting` on the first meeting | stops, nothing scored, no run file | exit 1 |
| Summary not made | `failed` / `timed out` | its references missed; counted | row carries result only |
| Rescore | `--rescore <run file>` after the references changed | scores the stored answers, no model call, new run file | refuses a run file outside the folder |
| Compare | `--compare <old> <new>` | per-meeting and total deltas, numbers only | refuses run files of different meeting sets, naming the stems |

</frozen-after-approval>

## Code Map

- `benchmarks/meeting_summary.py:137-175` -- `run()`'s setup (throwaway daemon, `Meeting`, `write_capture`, `record_transcript_state`, `summarize_meeting`); extract it as a function both scripts call, behaviour unchanged.
- `benchmarks/local_model.py:357` -- 7a's `score()`, token-only matching against `fixtures/mixed_ru_en.key.toml`; read-only precedent, not reused.
- `pm_ai/app/pipelines.py:3915-3960` -- `SummaryResult`, `SummaryCall`, `SummaryOutcome` (`answer`, `calls`, `prompt_characters`, `reason`); read-only.
- `pm_ai/domain/meeting_summaries.py:110-131` -- `SummaryDecision.statement`, `SummaryActionItem.what/owner/due`; read-only.
- `pm_ai/models/local/ollama.py:107` -- `NUM_CTX`, recorded as the window until 7g makes it a setting.
- `pm_ai/domain/transcripts.py:156` -- `CaptureFormat.GRAPH` / `MANUAL_TXT` by extension.
- `benchmarks/fixtures/mixed_ru_en.vtt` + `.key.toml` -- source of the fictional example; 7a's rejected proposals (X1–X4) become ruled-out decisions in the new format.

## Tasks & Acceptance

**Execution:**
- [ ] `benchmarks/reference_scoring.py` -- new, standard library only: load and validate a reference, fold text, match, per-meeting row, totals, compare, folder checks -- testable without a model or `pm_ai`.
- [ ] `benchmarks/score_summaries.py` -- new runner: `--references DIR`, `--model` (default `qwen3:8b`), `--rescore RUN`, `--compare OLD NEW`; runs, prints, writes the run file.
- [ ] `benchmarks/meeting_summary.py` -- extract the setup shared with the runner.
- [ ] `benchmarks/fixtures/mixed_ru_en.reference.toml` -- new fictional reference for `mixed_ru_en.vtt` in the format above.
- [ ] `tests/benchmarks/test_reference_scoring.py` -- loads the module by file path; covers every matrix row except the live run, the matching rules above, and that serialized rows, totals and errors contain none of the canned answer's or reference's marker strings.

**Acceptance Criteria:**
- Given the fictional reference and a canned answer returning D1 as a decision and A1 both as an action with the wrong owner and as a decision, when scored, then decision recall counts D1, A1's owner is wrong, and one decision is counted as restating an action.
- Given the fictional reference, when checked against `mixed_ru_en.vtt`, then no group warns.
- Given a run file, when `--rescore` runs, then no model is constructed.

## Questions for Andrei

1. **Matching by word groups, no model.** Each reference item names the words that must appear; an unclear match is fixed by editing the reference. Recommended: yes. A model judge would be irreproducible and would put a second model's opinion in the score.
2. **Keep the returned items' text in the private run file** (`0600`, inside the `0700` folder), so you can read what missed. Recommended: yes; without it a miss cannot be told from a reference gap.
3. **7h adds its own columns** — items dropped, items re-attributed, quote found word-for-word in the transcript — because those fields do not exist before 7h. Recommended: yes; 7f does not pre-build them.
4. **No pass/fail line in the script.** The orchestrator compares each slice's run with the previous one (`--compare`) and you decide. Recommended: yes, until the first baseline shows how much scores move between runs.

## Design Notes

Routine choices made here: one run per meeting (temperature 0, fixed seed); pooled totals; owner spellings listed in the reference rather than read from 7e's alias table, because the summary returns speaker labels; the first run on the current main is the baseline for 7g onward; a reference item's `about` stays in the private folder only. The runner is scored by its tests and the fictional example; a live run needs Ollama and is by hand.

## Verification

**Commands:**
- `uv run pytest tests/benchmarks -q` -- expected: all pass, no model needed
- `uv run pytest -q` -- expected: all pass (background, about 7 minutes)
- `uv run mypy` and `uv run lint-imports` -- expected: clean (`benchmarks/` is outside both; they confirm nothing else moved)

**Manual checks:**
- Copy `mixed_ru_en.vtt` and its reference into a fresh `0700` temporary folder, run `uv run python benchmarks/score_summaries.py --references <it>` with Ollama up: rows print with no answer text, the run file is `0600`. UNVERIFIED if Ollama is not running.
