---
title: 'The local window is a setting, and the model sees when things were said'
type: 'feature'
created: '2026-10-27'
status: 'ready-for-dev'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The local model's window is a constant of 16,384 tokens (`NUM_CTX`). At the first end-to-end test, 3 of 7 automatic summaries failed because hour-long meetings did not fit it. The prompt also drops every utterance's time: `summary_prompt` builds `label: words` only. So nothing in the prompt says when a decision or an action item was said, which `7h` needs.

**Approach:** The window becomes a `config.toml` setting, 32,768 by default and lowerable to 16,384. The composition root passes it to the local adapter, which uses it for every request and every size check, and `doctor` names it. The summary prompt gains a time mark at each new minute, counted from when transcription started, across parts; a transcript with no times gets no marks. The marks are in the prompt that both summaries share, so the frontier summary (`7c`) gets them and the amended format sentence too.

## Boundaries & Constraints

**Always:**
- **The setting.** Key `local_model_window`, a whole number, exactly `16384` or `32768`; default `32768` (Andrei, 2026-10-27). At its default it is omitted from a rendered `config.toml`, as every key is. Any other value — another number, a float, a string, a boolean — is refused as `ConfigRefused` naming both accepted values, by `load_config` and by `Config.__post_init__` for a direct construction. As with every refused key, the daemon does not start and `doctor` names the file. `setup` does not ask for it; `config show` lists it, as it lists every field. A changed window takes effect at the next daemon start, as `local_model` does.
- **The two values live in `pm_ai.domain.model_calls`** (`LOCAL_MODEL_WINDOWS`, `DEFAULT_LOCAL_MODEL_WINDOW`), read by `core.config` and by the adapter, so neither copies the other.
- **Where the window reaches the adapter.** `app.wiring.local_model` constructs `OllamaModel(config.local_model, window=config.local_model_window)`; the adapter refuses a window not in `LOCAL_MODEL_WINDOWS` at construction (`ValueError`) and exposes it as a read-only `window` property. Every request sends it as `num_ctx`. Every check that compared against `NUM_CTX` — the server's 400 refusal, the missing-count refusal, the cap check, the prompt-plus-answer check — compares against the instance's window and names it. `NUM_CTX` is removed. `NUM_PREDICT` (4,096) is unchanged, which leaves 28,672 tokens for a prompt at 32,768 and 12,288 at 16,384.
- **The architecture invariants hold.** AD-15: no new task class, and the router and its table are untouched; the window is the local adapter's own. AD-19: the model pool stays one thread, and `keep_alive: 0` and the 300-second deadline stay. AD-12: every fragment is still one `Sanitized` whose `for_model` is a fixed point of the sanitizer; the mark is pm-ai's own digits, colons and brackets, put before the cleaned label as `_joined` builds the label today. AD-49: the new refusal below is declared in `pm_ai.ports` beside `PossiblyTruncated`.
- **The instructions stay constants.** `7g` amends the one sentence of `SUMMARY_INSTRUCTIONS` that describes a fragment's format, so it says an utterance may begin with a time mark `[HH:MM:SS]` when the transcript has times. Nothing from a transcript is interpolated, and 7b's instructions-are-constant test still passes. This is not `7i`'s rewrite: no other sentence changes.
- **Times are from when transcription started** (orchestrator, 2026-10-27: part 1's `created`, not the meeting's calendar start). Part 1's times are its utterances' `offset_seconds` as read. A later stored part's times are its own offsets plus the seconds from part 1's `created` to its own `created`, partial seconds dropped, never negative. Only part 1 is the reference: if part 1 is not among the stored parts, the meeting is sent with no marks, and a log line at info level says why, naming the meeting and no transcript text. A meeting read from a single `-graph.vtt`, a `-manual.vtt`, or a `-manual.txt` read as speaker-and-time turns uses its offsets as read.
- **The reader says whether there are times.** A sibling of `read_transcript` returns, with the utterances, which reader it used: WebVTT, speaker-and-time turns, or `Speaker: words` lines. `read_transcript`'s callers are unchanged. The same is threaded through a sibling of `drop_utterances` and through `meeting_utterances`. A `Speaker: words` text carries no time — its reader spaces offsets 30 seconds apart (`_read_text`), which is not a time anyone said — so such a meeting is sent with no marks, never `[00:00:00]` or invented 30-second steps.
- **The mark's form and place.** `[HH:MM:SS] ` goes before the label, or before the words of an unlabelled utterance; hours have at least two digits. A mark is put on the first utterance kept in the prompt, and on each later kept utterance whose minute differs from the last mark shown (Andrei, 2026-10-27). A mark holds for the lines after it until the next one. Wordless utterances are dropped as today and carry no mark. The labels the model may answer with are unchanged: the text between the mark and `: `.
- **The contract with `7h`: the model's reading of a mark is never authoritative** (Andrei, 2026-10-27). `SummaryPrompt` carries, beside its fragments, each kept utterance's time from when transcription started (or none). `7h` works out an item's time in code from the utterance its quote matches; a time the model writes is never stored as the item's time. That is why a mark per minute is enough.
- **`doctor` names the window.** `wiring.local_model_check` and `local_model_presence` take the window. When the model is ready, the `local model` line says the window (`window 32,768 tokens`) and that a change takes effect at the next daemon start. At 32,768 its advice adds that `local_model_window = 16384` in `config.toml` lowers memory use. pm-ai does not read the machine's memory, and gives no memory-based advice until a 16 GB machine is measured (orchestrator, 2026-10-27).
- **A meeting too long for the window** fails as `7b` says, its reason naming the window in use. The two cases where the size is the cause — the server's 400 refusal, and prompt plus answer reaching the window — are raised as `WindowReached`, a subclass of `PossiblyTruncated` declared in `pm_ai.ports`, so every existing handler still catches them. Only for `WindowReached`, and only when the adapter's `window` is 16,384, does the reason add that `local_model_window = 32768` holds about twice as much.
- **The meetings that failed at 16,384 are not re-offered here.** The three in the field test are re-run by hand with `pm-ai transcript summarize`, with `--again` where the record already holds a summary. Automatic re-offer belongs to `7k`.
- **Measured, not claimed.** The build pins, in a unit test, the mark count and the character growth on the three fictional fixtures (Design Notes). At verification, the orchestrator records prompt tokens with and without marks (`prompt_eval_count`, real Ollama, fictional fixtures). It also records the time and Ollama's peak memory of a call near 28,000 prompt tokens at 32,768. Figures already recorded (2026-10-27, M3 Pro 18 GB, `qwen3:8b`, fictional transcript): 32,768 peaks at 9.4–9.7 GB of Ollama memory, against 7.0 GB at 16,384; an 18,000-token prompt took 116 s and a 27,000-token one 182 s, against the 300-second deadline; memory pressure reached warning.

**Ask First:**
- If the marks add more than 5% to the prompt tokens of any fictional fixture.
- The 300-second deadline stays at 32,768, and a near-full fictional call is measured at verification (Andrei, 2026-10-27). Stop and ask if a call near 28,000 tokens at 32,768 takes more than 240 seconds, if three such calls (the most a meeting can hold the model slot for across harvests) would pass 900 seconds, or if swap grows during it with nothing else open.

**Never:** No splitting of a meeting and no merge pass (`7j`). No time, speaker or quote on decisions or action items, and no change to the answer shape or its validator (`7h`). No instruction change beyond the one format sentence (`7i`). No retry or re-offer change (`7k`). No window other than the two values, no window for the frontier adapter, no change to the 300-second deadline (Andrei, 2026-10-27) or the output cap. No reading of the machine's memory. No change to `Utterance.offset_seconds`' meaning or to what ingestion stores. No real transcript, account or home in any test or fixture.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Unset | no `local_model_window` | window 32,768 sent as `num_ctx`; key omitted when rendered | N/A |
| Lowered | `local_model_window = 16384` | window 16,384 sent; `config show` marks it set | N/A |
| Invalid | `8192`, `32768.0`, `"32768"`, `true` | refused; daemon does not start | `ConfigRefused` names 16384 and 32768 |
| Changed while running | edited with the daemon up | old window kept until restart | N/A |
| Fits at 32k only | prompt 20,000 tokens | one call at 32,768 | N/A |
| Too long at 16k | server 400, window 16,384 | failed as in `7b`; reason names 16,384 and `local_model_window = 32768` | `WindowReached` |
| Too long at 32k | prompt plus answer reach 32,768 | failed as in `7b`, naming 32,768; no setting advice | `WindowReached` |
| Count missing | no `eval_count` | failed as in `7b`; no setting advice | `PossiblyTruncated` |
| One part | Graph cues at 0:05, 0:40, 1:10 | marks `[00:00:05]` and `[00:01:10]` only | N/A |
| Two parts | part 2 `created` 1,800.6 s after part 1; its cue at 0:10 | that utterance's time 00:30:10 | N/A |
| Part times out of order | part 2 `created` before part 1's | shift 0, never negative | N/A |
| Part 1 not stored | parts 2 and 3 stored only | no marks; info log names the meeting | N/A |
| `.txt` as turns | `-manual.txt` (or a `.docx` drop) with name-and-time lines | marks from those times | N/A |
| `.txt` as lines | `-manual.txt` of `Speaker: words` lines | no mark on any line; the constant format sentence still sent | N/A |
| Wordless first line | first utterance empty after cleaning | mark on the first kept utterance | N/A |
| Unlabelled line | words with no speaker, new minute | `[HH:MM:SS] words` | N/A |
| Frontier summary | `transcript summarize --by claude` on a timed meeting | the same fragments with marks and the same instructions sent | N/A |
| Doctor | model ready, window 32,768 | the line names the window, the restart, and how to lower it | N/A |
| Instructions | the module's constants | no transcript, provider or model text in them | test fails otherwise |

</frozen-after-approval>

## Code Map

- `pm_ai/core/config.py:239-279` -- `Config`, gaining `local_model_window` after `local_model`; `:281-360` `__post_init__`, its check beside `_refuse_port` and `_refuse_model_name` (`:211`, `:363`); `:391-444` `load_config`, passing the value through as `daemon_port` is; `:804` `_WHOLE_NUMBER_KEYS`, gaining it so it renders as `32768`, not `32768.0`; module docstring `:35-42` lists the accepted keys
- `pm_ai/domain/model_calls.py:241-265` -- beside `SUGGESTED_LOCAL_MODEL` and `SERVER_LIMITS`: the two window constants
- `pm_ai/ports/__init__.py:1275-1282` -- `PossiblyTruncated`; `WindowReached` declared after it
- `pm_ai/models/local/ollama.py:107-131` -- `NUM_CTX` and its docstring (the 16k rationale), replaced by the constructor's `window` and the 2026-10-27 figures; `:33-41` module docstring; `:365-386` `OllamaModel.__init__`, plus a `window` property; `:455-466` the request's `options`; `:564-567` and `:625-630` the two size checks, raising `WindowReached`; `:598-603`, `:607` the missing-count and cap checks naming the window; `:674-690` `local_model_presence`, taking the window for its ready line; `:91-104` `__all__`
- `pm_ai/app/wiring.py:783-798` -- `local_model(config)`, the one construction site; `:812-834` `local_model_check`, passing the window to the presence check; `:1316-1319` the "takes effect at the next daemon start" note
- `pm_ai/app/entry.py:1650-1677` -- `_local_model_probe`, which already loads `config.toml`, passes the window; `:836-838` setup's presence call, the default window
- `pm_ai/app/pipelines.py:3971-4033` -- `_prepare`: reads parts and captures, calls `meeting_utterances` (`:4020`) then `summary_prompt` (`:4028`); `:4049-4060` the local call and `:4086-4092` the frontier call, both sending `prepared.prompt.fragments`; `:5027-5036` the too-long reason, which reads the adapter's `window`
- `pm_ai/core/transcripts.py:153-191` -- `read_transcript`, which chooses WebVTT, turns or `Speaker: words`; its sibling returns the reader too; `:351-366` `_read_text`, whose 30-second offsets are invented; `:405-423` `_turn`, two fields read as minutes and seconds; `:431-457` `_read_turns`; `:516-546` `drop_utterances` and its sibling; `:612-658` `meeting_utterances`, which returns the utterances with their times or none, as a small frozen value
- `pm_ai/domain/transcripts.py:84-98` -- `Utterance.offset_seconds`, from the start of its part (read, not changed); `:354-380` `TranscriptPart.part` and `.created`
- `pm_ai/core/meeting_summary.py:94-121` -- `SUMMARY_INSTRUCTIONS`, its fragment-format sentence (`:99-101`) amended; `:272-284` `SummaryPrompt`, gaining the times; `:287-332` `summary_prompt` and `_joined`, where the mark is put and the label check (`startswith(f"{label}: ")`) reads past it
- `benchmarks/local_model.py:389`, `:485` -- the two `OllamaModel(name)` sites, both given the window; `:508-527` `main`, whose `--num-ctx` sets `ollama.NUM_CTX` by assignment, becomes a choice of the two windows, its parser built by a function a test can call. `benchmarks/meeting_summary.py` constructs `OllamaModel(name)` and takes the default unchanged; `7f` may edit that file
- `tests/models/test_ollama_adapter.py:41,170,432-458,503,804` -- tests reading `NUM_CTX`; `:449-458` pin 16,384 and the 8,619-token recording
- `tests/core/test_meeting_summary.py:205-242` -- the instructions-are-constant test and the sentences it pins
- `tests/slice/test_frontier_summary.py` -- 7c's slice test; checked for a pinned prompt, given a timed-meeting case
- `tests/core/test_config.py`, `tests/core/test_config_render.py`, `tests/core/test_transcript_readers.py`, `tests/slice/test_meeting_summary_slice.py:400-409`, `tests/slice/test_daemon_local_model.py`, `tests/tooling/` -- where the other new tests go

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/model_calls.py` -- `LOCAL_MODEL_WINDOWS = (16384, 32768)`, `DEFAULT_LOCAL_MODEL_WINDOW = 32768` -- one copy of the two values
- [ ] `pm_ai/core/config.py` -- `local_model_window` field, validation, loader pass-through, whole-number rendering, docstring -- the setting
- [ ] `pm_ai/ports/__init__.py` -- `WindowReached(PossiblyTruncated)` -- the size cases named apart from the others
- [ ] `pm_ai/models/local/ollama.py` -- `window` argument and property, refused outside the two values; the request and the four checks use it; the two size checks raise `WindowReached`; `local_model_presence` takes the window; `NUM_CTX` removed; docstrings carry the 2026-10-27 figures -- one window per adapter
- [ ] `pm_ai/app/wiring.py`, `pm_ai/app/entry.py` -- the window passed to the adapter and to the presence check; the ready line names the window, the restart and, at 32,768, how to lower it -- the composition root is the only place config meets the adapter
- [ ] `pm_ai/core/transcripts.py` -- the reader-reporting siblings of `read_transcript` and `drop_utterances`; `meeting_utterances` returns the utterances with their times from part 1's `created`, or none -- one reader decides
- [ ] `pm_ai/core/meeting_summary.py` -- the amended format sentence; `summary_prompt` takes the times, puts a mark at each new minute and keeps each kept utterance's time in `SummaryPrompt` -- the model sees when, and `7h` reads the time in code
- [ ] `pm_ai/app/pipelines.py` -- the times passed through; the too-long reason reads the adapter's `window` and adds the setting advice only for `WindowReached` at 16,384 -- the caller
- [ ] `benchmarks/local_model.py` -- the window at both construction sites; `--num-ctx` limited to the two windows; the parser built by a function -- the monkeypatch would stop working
- [ ] tests (files above) -- one test per matrix row; the request carries the configured window; the adapter refuses another; the instructions-are-constant test; a test pinning the mark count and character growth on the three fictional fixtures; `tests/tooling/test_local_model_benchmark.py` imports the benchmark and parses `--num-ctx 16384` and refuses `8192` -- the matrix is the contract
- [ ] `stories/7a-the-local-model-behind-the-port.md`, `stories/7b-a-transcript-yields-summary-decisions-and-actions.md` -- a dated Change Log line each: the window is `local_model_window`, 32,768 by default; the size refusals are `WindowReached` (`7g`) -- earlier frozen blocks amended on the record
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- a new entry: the 16 GB question re-opened at 32,768 (peak 9.4–9.7 GB on 18 GB), superseding the 16,384 decision of 2026-10-19; a near-full meeting can hold the model slot up to 3 × 300 s, remedy in `7k` -- the open questions carried forward
- [ ] **Orchestrator, not the builder:** through `bmad-architecture`, a dated note on AD-19 that the local window is a setting, 32,768 by default, with the 16 GB question open again, and on AD-49 that `WindowReached` joins `PossiblyTruncated` -- the spine is not edited by a builder

**Acceptance Criteria:**
- Given no `local_model_window`, when the daemon summarises a meeting, then the request sends `num_ctx` 32,768 and a prompt-plus-answer of 20,000 tokens is accepted.
- Given a two-part fictional meeting, when its prompt is built, then the second part's marks continue from part 1's `created`, every fragment is a `Sanitized` fixed point, and `SummaryPrompt` holds each kept utterance's time.
- Given `uv run pytest`, then everything passes, the skip count is unchanged, and no test reaches a real Ollama.

## Spec Change Log

- **2026-10-27, created** (WAVE 4 decisions of 2026-10-27 in `stories.yaml`, story 7). Carries: the window as `local_model_window`, 16,384 or 32,768, default 32,768; the window passed by the composition root to the adapter; time marks from the recording's start across parts, at each new minute, none for a transcript without times; `doctor` naming the window. The readiness survey said a `.txt` without time lines gets offset 0. The code gives it 30-second steps (`_read_text`), which this spec treats as no times.
- **2026-10-27, review (four lenses on `19c6370`; decisions by Andrei through the orchestrator):** frozen block edited (recorded here).
  - The contract with `7h` is written in: an item's time is worked out in code from the utterance its quote matches, never taken from the model, so one mark per minute is a decision, not a question.
  - The reader reports which reader it used, through siblings of `read_transcript` and `drop_utterances` and through `meeting_utterances`; matrix rows for a `.txt` read as turns and one read as lines.
  - `7g` amends the one format sentence of `SUMMARY_INSTRUCTIONS`, which is not `7i`'s rewrite.
  - Shifts are measured from part 1's `created` only, partial seconds dropped; there are no marks when part 1 is not stored; the wording is "from when transcription started".
  - Token measurement moves to the orchestrator at verification; a unit test pins mark counts and character growth.
  - The benchmark gets the window at both construction sites, `--num-ctx` limited to the two windows, and a smoke test.
  - The presence check takes the window; `doctor` says a change takes effect at the next daemon start.
  - The frontier summary gets the marks too, with a matrix row.
  - The setting advice is given only for the two size cases, raised as `WindowReached`; the pipeline reads the adapter's `window`.
  - The three-attempt worst case is in Ask First and deferred to `7k`.
  - Forged marks and clock-time turns files are recorded as limits.
  - The failed field meetings are re-run by hand; re-offer belongs to `7k`.
  - An orchestrator task covers the AD-19 and AD-49 notes.
  - `7f`'s baseline is noted in Design Notes.

- **2026-10-27, decisions:** frozen block edited (recorded here). The 300-second deadline stays at 32,768; a near-full fictional call is measured at verification, with a stop past 240 s for one call or 900 s for three (Andrei, 2026-10-27). Times are counted from when transcription started, part 1's `created` (orchestrator, 2026-10-27). `doctor` does not read the machine's memory, and gives no advice from it until a 16 GB machine is measured (orchestrator, 2026-10-27). No questions remain open.
- **2026-10-27, approved for build (Andrei),** after the four-lens review and his answers (spec PR #133).

## Design Notes

**Why marks at each new minute, not on every line.** Measured on the fictional fixtures, in characters, with `summary_prompt` as built today; tokens were not measured:

| Fixture | Utterances | Speaker turns | Minute marks | A mark on every line | On every turn | Each new minute |
|---|---|---|---|---|---|---|
| `mixed_ru_en.vtt` | 200 | 158 | 15 | +29% | +23% | +2.1% |
| `mostly_ru.vtt` | 38 | 37 | 4 | +17% | +17% | +1.8% |
| `transcript_recorded.vtt` | 198 | 74 | 17 | +17% | +6% | +1.4% |

A mark is 11 characters, mostly digits, and digits tend to cost a token each, so the token share is likely higher than the character share; the orchestrator measures it at verification. Since `7h` takes an item's time from its quote's utterance, the mark only orients the model, and a minute is fine enough for that.

**Why from when transcription started, not the calendar start.** Part 1's times are already the recording's, so a person checking an item against the Teams recording sees the same clock. The calendar start is when the meeting was booked, not when anyone spoke. Whether part 1's `created` is the moment its recording began is assumed from Graph's field, not verified on a real account. At verification the orchestrator checks it on one real two-part meeting and records only yes or no. The builder never touches real data.

**The deadline at 32,768.** At most 28,672 prompt tokens fit. A 27,000-token prompt took 182 s, so a near-full prompt with a long answer may pass 240 s. A timeout is an attempt, and the third fails the summary (`7b`), so one meeting can hold the one model slot for up to 3 × 300 s across harvests. The deadline is not raised (Andrei, 2026-10-27: keep 300 s, measure a near-full fictional call at verification, stop and ask past 240 s for one call or 900 s for three); the remedy, if one is needed, belongs to `7k`.

**Admitted limits.**
- A transcript can contain text that looks like a mark, for example someone saying "[00:42:00]". It reaches the model as words, and the model may misread it. This does not matter once `7h` takes times in code from the quote's utterance.
- A turns file whose times are clock times (`10:03` meaning 10:03 in the morning) is read as minutes and seconds (`_turn`, two fields), so its marks are offsets from nothing. This is the reader's existing limit; `7g` does not change it.

**`7f`'s baseline.** `7f` records the window from the requests actually sent and accepts `--setting key=value` (7f spec at `187337d`). Its baseline is taken on `main` before `7g` merges, at 16,384, with the window recorded, so the effect of `7g` is scored against it.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; skip count unchanged
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a throwaway home, `config.toml` with `local_model_window = 8192`: `pm-ai doctor` names the refused value and both accepted ones.
- Orchestrator, at verification: prompt tokens with and without marks on the three fictional fixtures (real Ollama, `prompt_eval_count`); the time, Ollama's peak memory and swap of a fictional call near 28,000 tokens at 32,768; on one real two-part meeting, whether part 2's marks line up with the recording, recorded as yes or no only.
