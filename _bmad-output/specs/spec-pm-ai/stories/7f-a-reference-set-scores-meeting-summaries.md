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

**Drafting the references:** Andrei chose on 2026-10-27 — "I copy, draft; you correct" — that Claude drafts the reference items from the real transcripts, as a one-off outside the disclosure ledger. How the transcripts reach the folder is not this slice's concern (a read-only exception in the home guard, its own PR); the script only reads the folder it is given.

## Boundaries & Constraints

**Always:**
- **The folder.** A command-line argument. Symlinks are resolved first; then the script refuses a folder inside the repo's working tree or whose mode is anything but exactly `0700`, naming the rule and nothing else.
- **Names.** Transcript stems must match `m\d{2,}` (`m01.vtt`); reference ids must match `D\d+` in `[[decision]]`, `A\d+` in `[[action]]`, `X\d+` in `[[not_an_item]]`. A breach is refused naming only the rule.
- **Nothing private on screen.** Standard output and errors carry only stems, reference ids, numbers, enum values and exception type names — never transcript text, answer text, a reference's `about` or `title`, a match word, 7b's `reason` sentence or an exception message. Each meeting runs inside its own guard: an exception prints its type name and the stem. Python logging is routed for the whole run to `<folder>/runs/<stamp>.log` (`0600`), never to the terminal.
- **The run file** (`<folder>/runs/<stamp>.json`, `0600`) holds: `format` (1), the summary commit sha and whether the tree was dirty, the scorer commit sha (equal to it except after `--rescore`), the model, the settings and window actually sent to the adapter, `daemon_running`, and per meeting: the SHA-256 of its reference file, result, 7b's `reason`, wall time, calls, prompt and output tokens, prompt characters, the scores, and each returned item as the domain object has it (`statement`, `said_by`; `what`, `owner`, `due`) — so misses can be read inside the private folder.
- **Summaries** run through `summarize_meeting` in a throwaway storage root at `<folder>/.work/` (`0700`), cleared at start and at end and skipped by the scan — never `~/.pm-ai`. A reference's optional `title` is the meeting title the prompt sees (default "Benchmark meeting").
- **Settings.** `--setting key=value` (repeatable) is applied to the `Config` the throwaway daemon is built with; an unknown key is refused. The window and options recorded are those read from each request actually sent to Ollama (its `options` and `think` only, never its messages), not the module constant — this is how 7g's window and 7j's split mode are scored later.
- **Repeatability is measured, not assumed.** `--repeat N` (default 1) summarises each meeting N times; the run file records, per meeting, whether the answers and the scores were identical across repeats, and each repeat's scores. The baseline is run with `--repeat 2` on one commit.
- **Not summarised.** Every result other than `summarized` and `waiting` (`failed`, `timed out`, `nothing said`, `stopped`) scores every reference item of that meeting as missed and counts in `meetings_not_summarised`. `waiting` (Ollama absent, model not pulled) at any meeting stops the run: nothing is written, and the script says so.
- **A running pm-ai daemon** (found in the process list, never by opening anything under `~/.pm-ai`) is warned about and recorded as `daemon_running: true`; the run continues.
- Totals pool items across meetings (each reference item weighs the same); per-meeting rows are printed too.

**Ask First:** Nothing; the decisions are below.

**Never:** a model or Claude in the matching; a pass/fail threshold inside the script; copying or reading real meetings from the real home; the columns 7h's new fields need; changing 7b's pipeline or `local_model.py`'s key.

## Reference format and matching

One `m<NN>.reference.toml` per meeting beside its transcript (`.vtt` is written as a Graph capture, `.txt` as a manual text capture):

```toml
transcript = "m01.vtt"          # in this folder, .vtt or .txt
title = "optional, never printed"
[[decision]]                    # chosen or ruled out
id = "D1"
about = "free note, never printed"
match = [["ORB-512"], ["flag", "флаг"]]
[[action]]
id = "A1"
owner = ["Speaker 2"]           # accepted spellings; at least one
match = [["ORB-533"]]
[[not_an_item]]                 # raised, never decided
id = "X1"
match = [["dark mode"]]
```

- A proposal ruled out in the meeting is a `[[decision]]` (wave 4's definition), so 7a's rejected proposals X1–X4 are decisions in the example; `[[not_an_item]]` is only for a topic raised and never decided.
- Text is case-folded, `ё` read as `е`, whitespace collapsed. A match word occurs when it starts a word in the text (preceded by the start or a non-letter, non-digit), so a stem like `миграц` covers Russian endings. A word under 4 letters is refused unless it contains a digit or a hyphen. A returned item matches a reference item when every group has at least one word that occurs. A decision's text is its `statement`; an action's is `what` and `due`. The owner is never part of the match.
- Per kind, reference items and returned items are paired by maximum matching (augmenting paths, reference items tried in file order), so file order never lowers the count. A returned item counts for at most one reference item.
- Recall = reference items paired / reference items; precision = returned items paired / returned items. An unmatched returned item may be a real item the reference lacks — Andrei then adds it and re-scores with `--rescore`.
- Owner correct = paired actions whose owner, normalised the same way, equals one accepted spelling / paired actions. A missing owner is wrong.
- Restated as the other kind: an unpaired returned decision matching any reference action, and an unpaired returned action matching any reference decision — counted separately. `not_an_item` hits: unpaired returned items of either kind matching one.
- A group none of whose words occurs in the transcript is printed as a warning (`m01 D1 group 2`) — usually a typo.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Score a folder | `0700` folder outside the repo, references valid, Ollama up | one JSON row per meeting, one totals row, run file and log written | N/A |
| Folder refused | under the repo (after resolving symlinks), or mode not exactly `0700` | refused before any model call | exit 2, rule named |
| Bad reference | missing or unknown key, empty group, short word, duplicate or misnamed id, bad stem, action without owner, transcript outside the folder or not `.vtt`/`.txt`, more decisions than 25 or actions than 15 | refused before any model call | exit 2, stem and id or rule named |
| Transcript without reference | `m04.vtt` alone | warning naming `m04`, not run | N/A |
| Ollama absent | `waiting` at any meeting, first or later | stops; no run file, no rows kept | exit 1, says nothing was written |
| Summary not made | any result but `summarized`/`waiting` | its references missed; counted | row carries result only |
| Exception in a meeting | anything raised | type name and stem printed, message only in the log | that meeting counts as not summarised |
| Rescore | `--rescore <run file>` | stored answers scored against the current references, no model, new run file with both commit shas | refuses a run file outside the folder or an unknown `format` |
| Compare | `--compare <old> <new>` | per-meeting and total deltas, numbers only | refuses different meeting sets, different reference hashes (unless both are `--rescore` files against the same hashes) or an unknown `format`, naming the stems |

</frozen-after-approval>

## Code Map

- `benchmarks/meeting_summary.py:137-175` -- `run()`'s setup (throwaway daemon, `Meeting`, `write_capture`, `record_transcript_state`, `summarize_meeting`); extract as a function taking the model as a parameter, used by both scripts, behaviour unchanged. `measured()` (`:115`) lists the per-call fields the rows reuse.
- `benchmarks/local_model.py:357` -- 7a's token-only `score()`; precedent, not reused.
- `pm_ai/app/pipelines.py:3915-3960` -- `SummaryResult`, `SummaryCall`, `SummaryOutcome`; read-only.
- `pm_ai/domain/meeting_summaries.py:56,61,110-131` -- `MAX_DECISIONS` 25, `MAX_ACTION_ITEMS` 15, the item fields; read-only.
- `pm_ai/models/local/ollama.py:244-267,365-419,455-472` -- the loopback transport, the `transport=` parameter, the request's `options` and `think`; the recorder wraps the adapter's own loopback transport so the deadline cut still applies. Read-only.
- `pm_ai/domain/transcripts.py:156` -- `CaptureFormat.GRAPH` / `MANUAL_TXT` by extension.
- `pm_ai/app/daemon.py:37` -- the daemon's lock lives under the real home; not opened — the process list is read instead.
- `benchmarks/fixtures/mixed_ru_en.vtt` + `.key.toml` -- source of the fictional example.

## Tasks & Acceptance

**Execution:**
- [ ] `benchmarks/reference_scoring.py` -- new, standard library only: folder and name checks, reference loading and validation, folding, maximum matching, rows, totals, run-file format, compare -- testable without a model or `pm_ai`.
- [ ] `benchmarks/score_summaries.py` -- new runner: `--references DIR`, `--model` (default `qwen3:8b`), `--setting`, `--repeat`, `--rescore RUN`, `--compare OLD NEW`; per-meeting guard, logging to the private log, request recorder, daemon check, `.work/` lifecycle.
- [ ] `benchmarks/meeting_summary.py` -- extract the shared setup with an injectable model.
- [ ] `benchmarks/fixtures/mixed_ru_en.reference.toml` -- new fictional reference with `transcript = "m01.vtt"`; tests and the manual check copy it and `mixed_ru_en.vtt` into a `0700` folder as `m01.reference.toml` and `m01.vtt`, since the repo's own fixtures folder is refused by design.
- [ ] `tests/benchmarks/test_reference_scoring.py` -- loads modules by file path; every matrix row; the matching rules; a case where greedy file order pairs fewer items than maximum matching; word-start and short-word rules; a canned model returning `waiting`, `failed` and a valid answer; a marker string placed in answers, references and an exception message never reaching stdout or stderr.

**Acceptance Criteria:**
- Given the fictional reference and a canned answer returning D1 as a decision and A1 both as an action with the wrong owner and as a decision, when scored, then D1 is paired, A1's owner is wrong, and one decision is counted as restating an action.
- Given the fictional reference, when checked against `mixed_ru_en.vtt`, then no group warns.
- Given a run file, when `--rescore` runs, then no model is constructed and the new file carries the original summary sha and the current scorer sha.
- Given `--setting` with an unknown key, then the run is refused with exit 2 before any model call.

## Decisions

1. **Matching by word groups, no model** (Andrei, 2026-10-27). A model judge would be irreproducible and put a second model's opinion in the score.
2. **Returned items' text is kept in the private run file** (`0600`, in the `0700` folder) so misses can be read (Andrei, 2026-10-27).
3. **7h adds its own columns** — items dropped, re-attributed, quote found word-for-word in the transcript — since those fields do not exist before 7h (orchestrator, 2026-10-27).
4. **No pass/fail threshold yet.** It is decided after the measured spread of the `--repeat 2` baseline; until then each slice's run is compared with the previous one (`--compare`) and Andrei decides (orchestrator, 2026-10-27).

No questions are open.

## Design Notes

Routine choices: pooled totals; owner spellings listed in the reference rather than read from 7e's alias table, because the summary returns speaker labels; the first run on the current main is the baseline for 7g onward. The runner is covered by tests with a canned model and the fictional example; a live run needs Ollama and is by hand.

## Verification

**Commands:**
- `uv run pytest tests/benchmarks -q` -- expected: all pass, no model needed
- `uv run pytest -q` -- expected: all pass (background, about 7 minutes)
- `uv run mypy` and `uv run lint-imports` -- expected: clean (`benchmarks/` is outside both; they confirm nothing else moved)

**Manual checks:**
- Stop any running pm-ai daemon first. Copy `mixed_ru_en.vtt` and its reference into a fresh `0700` temporary folder as `m01.*`, run `uv run python benchmarks/score_summaries.py --references <it>` with Ollama up: rows print with no answer text; run file and log are `0600`; `.work/` is gone. UNVERIFIED if Ollama is not running.
- `uv run python benchmarks/meeting_summary.py --model qwen3:8b` still runs and prints the same fields as before the extraction.
