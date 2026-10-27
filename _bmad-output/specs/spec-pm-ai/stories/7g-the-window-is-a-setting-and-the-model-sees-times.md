---
title: 'The local window is a setting, and the model sees when things were said'
type: 'feature'
created: '2026-10-27'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The local model's window is a constant of 16,384 tokens (`NUM_CTX`), and at the first end-to-end test 3 of 7 automatic summaries failed because hour-long meetings did not fit it. The prompt also drops every utterance's time (`summary_prompt` builds `label: words` only), so the model cannot say when a decision or an action item was said, which `7h` needs.

**Approach:** The window becomes a `config.toml` setting, 32,768 by default and lowerable to 16,384, passed by the composition root to the local adapter, which uses it for every request and every size check; `doctor` names it. The summary prompt gains a time mark, measured from the start of the recording across parts, at the start of each new minute; a transcript with no times gets no marks.

## Boundaries & Constraints

**Always:**
- **The setting.** Key `local_model_window`, a whole number, exactly `16384` or `32768`; default `32768` (Andrei, 2026-10-27). At its default it is omitted from a rendered `config.toml`, as every key is. Any other value — another number, a float, a string, a boolean — is refused by `load_config` as `ConfigRefused` naming both accepted values, and by `Config.__post_init__` for a direct construction; as with every refused key, the daemon does not start and `doctor` names the file. `setup` does not ask for it. `config show` lists it, as it lists every field. A changed window takes effect at the next daemon start, as `local_model` does.
- **The two values live in `pm_ai.domain.model_calls`** (`LOCAL_MODEL_WINDOWS`, `DEFAULT_LOCAL_MODEL_WINDOW`), read by `core.config` and by the adapter, so neither copies the other.
- **Where the window reaches the adapter.** `app.wiring.local_model` constructs `OllamaModel(config.local_model, window=config.local_model_window)`. The adapter refuses a window not in `LOCAL_MODEL_WINDOWS` at construction (`ValueError`). Every request sends it as `num_ctx`, and every check that compared against `NUM_CTX` — the server's 400 refusal, the missing-count refusal, the cap check, the prompt-plus-answer check — compares against the instance's window and names it. `NUM_CTX` is removed; `NUM_PREDICT` (4,096) is unchanged, so 28,672 tokens are left for a prompt at 32,768 and 12,288 at 16,384.
- **The architecture invariants hold unchanged.** AD-15: no new task class, the router and its table untouched; the window is the local adapter's own, and a frontier call never sees it. AD-19: the model pool stays one thread, `keep_alive: 0` and the 300-second deadline stay. AD-12: every fragment is still one `Sanitized` whose `for_model` is a fixed point of the sanitizer; the time mark is pm-ai's own digits, colons and brackets, put before the cleaned label, built as `_joined` builds the label today. The instructions stay constants: one constant sentence explains the marks, nothing from a transcript is interpolated, and 7b's instructions-are-constant test still passes.
- **Times are from the start of the recording.** Part 1's times are its utterances' `offset_seconds` as read. A later stored part's times are its own offsets plus the whole seconds from the first stored part's `created` to its own `created`, never negative. A meeting read from a single `-graph.vtt`, `-manual.vtt` or a `-manual.txt` read as speaker-and-time turns uses its offsets as read.
- **No times, no marks.** A `-manual.txt` read as `Speaker: words` lines carries no time: its reader spaces offsets 30 seconds apart (`_read_text`), which is not a time anyone said. Such a meeting is sent with no marks at all, never `[00:00:00]` or invented 30-second steps. The whole-meeting reader says whether the meeting has times.
- **The mark's form and place.** `[HH:MM:SS] ` before the label (or before the words of an unlabelled utterance), hours at least two digits. A mark is put on the first utterance kept in the prompt, and on each later kept utterance whose minute differs from the last mark shown; a mark therefore holds for the lines after it until the next one. Wordless utterances are dropped as today and carry no mark. Labels the model may answer with are unchanged: the text between the mark and `: `.
- **`doctor` names the window.** The `local model` line, when the model is ready, says the window (`window 32,768 tokens`); at 32,768 its advice adds that `local_model_window = 16384` in `config.toml` lowers memory use, then a daemon restart. pm-ai does not read the machine's memory.
- **A meeting too long for the window** fails as `7b` says, its reason naming the window in use; at 16,384 the reason also says that `local_model_window = 32768` holds about twice as much.
- **Measured, not claimed.** The build records, on the fictional fixtures, the prompt tokens with and without marks, and — run by the coordinating session against the real model, with a fictional prompt near 28,000 tokens — the call's time and Ollama's peak memory at 32,768. Figures already recorded (2026-10-27, M3 Pro 18 GB, `qwen3:8b`, fictional transcript): 32,768 peaks at 9.4–9.7 GB of Ollama memory against 7.0 GB at 16,384; an 18,000-token prompt took 116 s and a 27,000-token one 182 s, against the 300-second deadline; memory pressure reached warning.

**Ask First:**
- If the time marks add more than 5% to the prompt tokens of any fictional fixture.
- If the near-full 32,768 call takes more than 240 seconds, or swap grows during it with nothing else open.

**Never:** No splitting of a meeting or merge pass (`7j`). No time, speaker or quote on decisions or action items, and no change to the answer shape or its validator (`7h`). No other change to the instructions (`7i`). No retry change (`7k`). No window other than the two values, no window for the frontier adapter, no change to the deadline or the output cap. No reading of the machine's memory. No change to `Utterance.offset_seconds`' meaning or to what ingestion stores.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Unset | no `local_model_window` | window 32,768 sent as `num_ctx`; key omitted when rendered | N/A |
| Lowered | `local_model_window = 16384` | window 16,384 sent; `config show` marks it set | N/A |
| Invalid | `8192`, `32768.0`, `"32768"`, `true` | refused; daemon does not start | `ConfigRefused` names 16384 and 32768 |
| Changed while running | edited with the daemon up | old window kept until restart | N/A |
| Fits at 32k only | prompt 20,000 tokens | one call at 32,768 | N/A |
| Too long at 16k | 18,000 tokens, window 16,384 | failed as in `7b`; reason names 16,384 and `local_model_window = 32768` | `PossiblyTruncated` |
| Too long at 32k | 30,000 tokens | failed as in `7b`, naming 32,768 | `PossiblyTruncated` |
| One part | Graph cues at 0:05, 0:40, 1:10 | marks `[00:00:05]` and `[00:01:10]` only | N/A |
| Two parts | part 2 `created` 1,800 s after part 1; its cue at 0:10 | that utterance's time 00:30:10 | N/A |
| Part times out of order | part 2 `created` before part 1's | shift 0, never negative | N/A |
| Turns file | `.docx` or `.txt` with name-and-time lines | marks from those times | N/A |
| No times | `.txt` of `Speaker: words` lines | no mark on any line; one constant instruction sentence still sent | N/A |
| Wordless first line | first utterance empty after cleaning | mark on the first kept utterance | N/A |
| Unlabelled line | words with no speaker, new minute | `[HH:MM:SS] words` | N/A |
| Doctor | model ready, window 32,768 | the line names the window and how to lower it | N/A |
| Instructions | the module's constants | no transcript, provider or model text in them | test fails otherwise |

</frozen-after-approval>

## Code Map

- `pm_ai/core/config.py:239-279` -- `Config`, gaining `local_model_window` after `local_model`; `:281-360` `__post_init__`, its check beside `_refuse_port`/`_refuse_model_name` (`:211`, `:363`); `:391-446` `load_config`, passing the value through as `daemon_port` is; `:804` `_WHOLE_NUMBER_KEYS`, gaining it so it renders as `32768`, not `32768.0`; module docstring `:35-42` lists the accepted keys
- `pm_ai/domain/model_calls.py:241-265` -- beside `SUGGESTED_LOCAL_MODEL` and `SERVER_LIMITS`: the two window constants
- `pm_ai/models/local/ollama.py:107-131` -- `NUM_CTX` and its docstring (the 16k rationale), replaced by the constructor's `window` and the 2026-10-27 figures; `:33-41` module docstring; `:365-386` `OllamaModel.__init__`; `:455-466` the request's `options`; `:564-567`, `:598-603`, `:607`, `:625-630` the four checks naming the window; `:91-104` `__all__`
- `pm_ai/app/wiring.py:783-798` -- `local_model(config)`, the one construction site; `:1316-1319` the "takes effect at the next daemon start" note
- `pm_ai/app/entry.py:1650-1677` -- `_local_model_probe`, which already loads `config.toml`; it names the window on a ready line
- `pm_ai/app/pipelines.py:3971-4033` -- `_prepare`, the summary preparation: reads parts and captures, calls `meeting_utterances` (`:4020`) then `summary_prompt` (`:4029`); `:5027-5036` the too-long reason
- `pm_ai/core/transcripts.py:612-658` -- `meeting_utterances`, which gains the meeting-relative times and whether the meeting has times; `:153-191` `read_transcript` chooses VTT, turns or `Speaker: words`; `:351-366` `_read_text`, the reader whose 30-second offsets are invented; `:431-457` `_read_turns`
- `pm_ai/domain/transcripts.py:84-98` -- `Utterance.offset_seconds`, from the start of its part (read, not changed); `:354-380` `TranscriptPart.created`, the part's start instant
- `pm_ai/core/meeting_summary.py:94-121` -- `SUMMARY_INSTRUCTIONS`, gaining one constant sentence; `:287-332` `summary_prompt` and `_joined`, where the mark is put and the label check (`startswith(f"{label}: ")`) reads past it
- `benchmarks/local_model.py:516-527` -- `--num-ctx` sets `ollama.NUM_CTX` by assignment; it passes the window to the constructor instead (`benchmarks/meeting_summary.py` constructs `OllamaModel(name)` and takes the default unchanged; `7f` may edit that file)
- `tests/models/test_ollama_adapter.py:41,170,432-458,503,804` -- tests reading `NUM_CTX`; `:449-458` pin 16,384 and the 8,619-token recording
- `tests/core/test_config.py`, `tests/core/test_config_render.py`, `tests/core/test_meeting_summary.py`, `tests/core/test_transcript_readers.py`, `tests/slice/test_meeting_summary_slice.py:400-409`, `tests/slice/test_daemon_local_model.py` -- where the new tests go

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/model_calls.py` -- `LOCAL_MODEL_WINDOWS = (16384, 32768)`, `DEFAULT_LOCAL_MODEL_WINDOW = 32768` -- one copy of the two values
- [ ] `pm_ai/core/config.py` -- `local_model_window` field, validation, loader pass-through, whole-number rendering, docstring -- the setting
- [ ] `pm_ai/models/local/ollama.py` -- `window` constructor argument, refused outside the two values; request and the four checks use it; `NUM_CTX` removed, docstrings carry the 2026-10-27 figures -- one window per adapter
- [ ] `pm_ai/app/wiring.py` -- pass `config.local_model_window` -- the composition root is the only place config meets the adapter
- [ ] `pm_ai/app/entry.py` -- the ready `local model` line names the window and, at 32,768, how to lower it -- doctor reports it
- [ ] `pm_ai/core/transcripts.py` -- `meeting_utterances` returns the utterances with their meeting-relative times, or no times, as a small frozen value -- one reader decides
- [ ] `pm_ai/core/meeting_summary.py` -- the constant sentence; `summary_prompt` takes the times and puts a mark at each new minute -- the model sees when
- [ ] `pm_ai/app/pipelines.py` -- pass the times through; the too-long reason at 16,384 names the larger setting -- the caller
- [ ] `benchmarks/local_model.py` -- `--num-ctx` through the constructor -- the monkeypatch would stop working
- [ ] tests (files above) -- one test per matrix row; the request carries the configured window; the adapter refuses another; the instructions-are-constant test; the mark counts on the fictional fixtures -- the matrix is the contract
- [ ] `stories/7a-…md`, `stories/7b-…md` -- a dated Change Log line each: the window is `local_model_window`, 32,768 by default (`7g`) -- earlier frozen blocks amended on the record
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- a new entry: the 16 GB question re-opened at 32,768 (peak 9.4–9.7 GB on 18 GB), the 16,384 decision of 2026-10-19 superseded -- the open question carried forward

**Acceptance Criteria:**
- Given no `local_model_window`, when the daemon summarises a meeting, then the request sends `num_ctx` 32,768 and a prompt-plus-answer of 20,000 tokens is accepted.
- Given a two-part fictional meeting, when its prompt is built, then the second part's marks continue from the first part's start, and every fragment is a `Sanitized` fixed point.
- Given `uv run pytest`, then everything passes, the skip count is unchanged, and no test reaches a real Ollama.

## Spec Change Log

- **2026-10-27, created** (WAVE 4 decisions of 2026-10-27 in `stories.yaml`, story 7). Carries: the window as `local_model_window`, 16,384 or 32,768, default 32,768; the window passed by the composition root to the adapter; time marks from the recording's start across parts, at each new minute, none for a transcript without times; `doctor` naming the window. The readiness survey said a `.txt` without time lines gets offset 0; the code gives it 30-second steps (`_read_text`), which this spec treats as no times.

## Design Notes

**Why marks at each new minute, not on every line.** Measured on the fictional fixtures (characters, `summary_prompt` as built today; tokens not measured):

| Fixture | Utterances | Speaker turns | Minute marks | A mark on every line | On every turn | Each new minute |
|---|---|---|---|---|---|---|
| `mixed_ru_en.vtt` | 200 | 158 | 15 | +29% | +23% | +2.1% |
| `mostly_ru.vtt` | 38 | 37 | 4 | +17% | +17% | +1.8% |
| `transcript_recorded.vtt` | 198 | 74 | 17 | +17% | +6% | +1.4% |

A mark is 11 characters, mostly digits, and digits tend to cost a token each, so the token share is likely higher than the character share — the build measures it (Ask First above). A minute is fine enough for a person to find the moment in a recording; `7h` can tie an item to its exact utterance through its quote.

**Why from the recording's start, not the calendar start.** Part 1's times are already the recording's; a person checking an item against the Teams recording sees the same clock. The calendar start is when the meeting was booked, not when anyone spoke.

**The deadline at 32,768.** At most 28,672 prompt tokens fit. 27,000 took 182 s; a near-full prompt with a long answer may pass 240 s, and a timeout is an attempt (`7b`: the third fails it). The deadline is not raised here; the build measures it (Ask First).

**Questions for Andrei** (each answered provisionally above with the recommendation):
1. Mark density: each new minute (recommended), every speaker turn, or every line?
2. Times measured from the recording's start (recommended) or from the meeting's calendar start?
3. Keep the 300-second deadline at 32,768 and measure a near-full call first (recommended), or raise it now?
4. Should `doctor` read the machine's memory and advise 16,384 on 16 GB or less? Recommended: no, until a 16 GB machine is measured.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; skip count unchanged
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- In a throwaway home, `config.toml` with `local_model_window = 8192`: `pm-ai doctor` names the refused value and both accepted ones.
- Coordinating session, real Ollama, fictional prompt near 28,000 tokens at 32,768: the call's time, Ollama's peak memory and swap recorded in Design Notes.
