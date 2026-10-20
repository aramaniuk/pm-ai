---
title: 'A transcript yields a summary, decisions and action items'
type: 'feature'
created: '2026-10-16'
status: 'done'
baseline_commit: 'cf01128d6881e8eac769ccd56369ddce76b1cb35'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** An ingested transcript yields only what pattern matching finds — a `pm-ai, <verb>` command or an "I'll … by …" promise — and on the field-test transcripts, mixed Russian and English with speaker tags, that is nothing. The meeting record's `## Summary` region is reserved and empty, and no decision has ever been written to an event log, though `[TYPE: DECISION]` is declared for it (CAP-27).

**Approach:** After a transcript is ingested — by the harvest, or by `transcript drop` once the daemon picks it up — or when the PM asks, the local model (`7a`) reads the cleaned transcript in one call and answers in one bounded, validated shape: a summary, the decisions made, the action items agreed. The summary goes into the record's `## Summary` region; each decision becomes a decision event in the meeting's scope; each action item becomes a staged proposal, never executed.

## Boundaries & Constraints

**Always:**
- **Prerequisites:** `7a`; `33g`'s part rows (`transcript_parts`) and per-part ingestion — `33g` built no whole-meeting reader, so this slice builds it; the spine amendments listed in the Change Log — above all AD-34 gaining the `decision` reference kind — made through `bmad-architecture` before this slice is built.
- **What the model reads.** The meeting's whole transcript, read by this slice's whole-meeting reader: every stored part in part order as one sequence when the meeting has `transcript_parts` rows; otherwise its Graph capture (`-graph.vtt`, captured before parts existed); otherwise its dropped capture (`-manual.vtt` or `-manual.txt`), read as `transcript drop` read it — with each utterance one `Sanitized` fragment of its speaker label and words, and the meeting's title another. Instructions are constants pm-ai wrote; no provider or model text is interpolated into them (the port's admitted limit, `ports/__init__.py:1058-1063`, closed here by a test). Task class `extraction`, local-only (AD-15); `flow` from the meeting's scope to the meeting's scope. A transcript with no words left after cleaning records "nothing said" and makes no call.
- **Where each step runs (AD-5, AD-19).** Reading the parts and sanitizing run on an I/O worker, and that step ends when the preparation does; the job is then handed to the model pool for the call and its one retry — the model reached through `require_model` until `7c` builds the router — and what follows a call is handed on again — no I/O worker ever waits on the model pool (AD-19). Every write is a claimed step on the writer thread. Only the daemon summarises: `transcript drop` records the meeting as waiting wherever it runs — in the CLI process, or in the daemon when typed at the prompt with a daemon running — and the daemon picks it up at its next harvest. `transcript summarize`, typed in a terminal or at the prompt, sends its line to the daemon's loopback API (`/v1/prompt`) and is acknowledged as a job, with its own `DAEMON_BOUNDS` entry (AD-21); it never summarises in its own process, and with no daemon running it is refused, naming `pm-ai daemon run`. `--by local` is the default; `--by claude` is refused by name until `7c` builds it.
- **One answer shape, validated in `core`.** A JSON object with exactly three keys: `summary` (1–600 characters), `decisions` (at most 10, each `statement` of 1–200 characters and `said_by`), `action_items` (at most 15, each `what` of 1–200 characters, `owner`, and `due` as said or null, at most 40 characters). **The limits are set so the largest valid answer fits the output cap of 4,096 tokens with margin:** at `7a`'s measured 2.92 characters per token (`qwen3:8b`, the authored meeting), the largest valid answer — JSON included, each speaker label counted at 40 characters — is about 8,000 characters, about 2,740 tokens, within three quarters of the cap (3,072). The build measures characters per token on its own largest valid answer and records the figures in the Design Notes; if that answer would pass three quarters of the cap, the limits are trimmed again before it lands. `said_by` and `owner` are each one of the transcript's speaker labels exactly, or null; a person named but not speaking stays in the item's text. The schema goes to the model as its `shape`; validation does not trust that it was followed. Every returned string then passes `sanitize`, and the bounds and non-emptiness are checked again on the cleaned text. Anything else — not JSON, an extra key, a bound broken before or after cleaning, an empty string, a label not in the transcript — is refused as `ModelAnswerRefused` naming the rule — a refusal of this answer shape, declared in `pm_ai.domain` beside it (AD-49).
- **In the language most of the meeting was held in**, names and terms kept as spoken; the instructions say so and nothing translates afterwards.
- **One retry per call, then a recorded failure.** A refused answer is asked for once more with one sentence appended to the instructions, chosen from a fixed set naming the broken rule — never quoting the answer or the transcript. A second refusal fails the summary with that rule, and the harvest does not offer it again — at temperature 0 the same input gives the same answer; the PM may ask (`transcript summarize`) or ask for a frontier summary (`7c`).
- **A whole meeting is one call.** The window is 16,384 tokens and the output cap 4,096 (`7a`), which leaves 12,288 for the prompt; the real-length recording measured 8,619. No transcript is split. A transcript that does not fit fails the summary, naming the window and the prompt's size: either the server refuses the prompt under `truncate: false`, or the answer comes back `PossiblyTruncated`. It is not retried or offered again automatically — the same input never fits — and the PM may ask for a frontier summary (`7c`). An answer stopped at the output cap (`OutputCapReached`) is an invalid answer: asked once more with the fixed sentence for that rule, then failed naming the cap.
- **No local model is not a failure.** No `local_model`, Ollama not answering, or the model not pulled — before the call or before its retry — records the meeting as waiting, discards the first answer, and is not an attempt; it is offered again at the next harvest. A deadline is an attempt: each model call has 300 seconds, a timed-out summary is offered again at the next harvest, and the third timeout fails it as timing out; `doctor` names the meetings waiting, failed and timing out under the `local model` line.
- **What a harvest offers, bounded.** One summary job in flight per (scope, meeting). At most 3 meetings per harvest, the most recent first; a meeting whose start is more than 30 days old is never offered automatically (its transcript is purged at 30 days, NFR-09). A meeting is summarised only when its transcript row is `ingested`: one with no row, a part that failed ingestion (row `ingest_failed`, even when other parts were ingested), no capture, or a purged capture is not offered, and is refused by name before any model call when requested — a failed part named as the reason.
- **The summary's state is a row** in a new operational table: scope, meeting id, `waiting` / `summarized` / `failed` / `nothing said`, who made it (`local` here), whether it was asked for, the model's name, attempts, a reason, the instant recorded, and a version. A job reads the version when it starts and takes a lease with its first write: one claimed step that compares and sets the row from that version, naming the job (AD-37). Every later write checks the lease inside its own claimed step; a job whose lease is gone stops with nothing further written. A requested summary moves the row to a new version first, which revokes a running automatic job's lease, so that job stops before its next write rather than after its last. No schema version bump, as `11b`'s tables.
- **A later part re-opens an automatic summary.** When `33g` stores a later part and ingests the new part, a summary the harvest made is offered again over the whole meeting; one the PM asked for is kept, replaced only by another request. If an earlier part has been purged, nothing is re-opened.
- **The record's `## Summary` region is pm-ai's and now holds the summary.** It is written by `MeetingRecords.put_summary` — `put` takes a `Meeting` and never writes it — in a fixed grammar: a provenance line (made by the local model `<name>`, automatically or on request, on `<date>`), the summary, a `Decisions` list (statement, said by), an `Action items` list. Every rendered string goes through the record's one value encoding (`render_value`, which escapes newlines), is stripped of control characters, and stays within `MAX_ENTRY_LENGTH`; a leading `#` or `-` is escaped too (`render_value` is extended if it does not already). A summary containing `## Notes` round-trips unchanged. `MeetingRecord` gains `summary`, carried over on every `put` as `## Notes` is. A calendar `put` meeting a region that does not parse keeps the rest of the record, drops that region, names it in the harvest's refusals and sets the summary row back to offered, so it is made again — a hand edit in pm-ai's region is never kept as pm-ai's. `## Notes` is the human's and is never written.
- **Decisions are events in the meeting's scope (CAP-27, AD-38), each with its own reference.** Each `decision` event cites `decision:<meeting id>_<digest>`, the digest the first 12 hex characters of SHA-256 over the decision's cleaned, trimmed statement. Scopeless, as `meeting:` is, because the meeting it extends is; the event's scope is in its envelope and in storage's duplicate check `(scope, system, reference)`, so the same decision from a re-run is one event and no decision is dropped beside the calendar event for the meeting (`events.py:572`, `service.py:3567`). A meeting id that would not parse in that form already fails `meeting:<id>`, so none reaches here. `decision` is added to `PM_AI_MINTED`, so normalisation never attributes one to an outside source. Payload `DecisionPayload(statement)`; who said it lives only in the record's list; actor `none:` — a speaker label is not a person (AD-34); authored by pm-ai (AD-36): pm-ai's reading of a transcript is never evidence a commitment was kept; time is the meeting's start. **Decisions accumulate:** a re-summary appends decisions it words differently and never removes an earlier one — the log holds every reading pm-ai made, the record shows the current one; both are kept on purpose.
- **Action items are staged proposals, never executed.** Type `action_item`, no target, payload `what`, `owner`, `due`, summary the first 80 characters of `what`, citing the meeting (AD-33), created at the staging instant. Id `prp_<scope digest>_<meeting id>_ai_<digest of the cleaned what and owner>` — the scope included, since a meeting may have a record in more than one scope; never by position. Staged through `stage_unless_acted`. A new summary supersedes: each still-staged `action_item` of that scope and meeting whose id the new summary does not produce is moved to `superseded` by compare-and-set; one the PM acted on is left as it is. `11b`'s pattern extraction is unchanged; an item both find is staged twice, under two types.
- **The validated answer is handed on.** `summarize_meeting` returns the validated answer — items with their ids and owner labels, decisions with their references — and the ids it superseded, so `7e` reads the answer rather than parsing the record, whose summary block is text only.
- **Write order, each step complete on its own:** the lease on the state row, the record's block, the decision events, the proposals (supersession included), then the state row's final state — each after the lease check (AD-37). A failure part-way leaves the row unchanged, so the meeting is offered again, and a re-run repeats only what is missing — every write is keyed.
- **Everything lands in the meeting's scope.** A personal meeting's summary, decisions and action items are personal; a `people` meeting's, if a record exists, stay in `people` and are never shown on a dashboard (`7d`).
- **The filter's limit, stated.** The sanitizer's phrase lists are English; an injection spoken in Russian is not matched (`sanitize.py` holds no Cyrillic). What bounds it: a fixed shape, bounded lengths, sanitized output, nothing executed.
- **Measured, not claimed.** pm-ai observes no meeting end in this wave (the harvest runs every 240 minutes), so CAP-3's figure from meeting end is not claimed. Measured on `7a`'s fixture and recorded in the Design Notes: from ingestion to the record written, and from the PM's command to the record written, with the prompt tokens, each call's model time and the total.

**Ask First:**
- If ingestion-to-written exceeds 600 seconds on `7a`'s fixture.

**Never:** No frontier call (`7c`). No splitting of a transcript; a meeting too long for the window fails by name. No execution of anything the model returns. No write to `## Notes`. No owner resolved to a person. No model in the CLI process. No dashboard change (`7d`), and no re-render when a summary lands. No change to pattern extraction or to `11b`'s execute rule.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Harvest ingests a transcript | Graph capture ingested; model ready | block in the record; one event per decision; one staged proposal per action item; row `summarized`, `local`, model named | N/A |
| Multi-part meeting | two stored parts | read as one sequence; one summary | N/A |
| Later part arrives | summarized automatically; `33g` stores part 2 | summarized again over both parts | a requested summary is kept |
| Too long for the window | prompt over the window, or prompt and answer together reaching it | no retry; row `failed` naming the window and the prompt's size; not offered again automatically | `PossiblyTruncated` |
| Output cap | the answer stops at 4,096 tokens | asked once more with the fixed sentence for the cap; the second time row `failed` naming the cap | `OutputCapReached` |
| First answer invalid | an extra key | asked once more with the fixed sentence for that rule | N/A |
| Both invalid | not JSON twice | nothing written; row `failed` naming the rule; not offered again automatically | `ModelAnswerRefused` |
| Label not in transcript | `owner: "Ваня"`, no such speaker | refused as an unknown label | `ModelAnswerRefused` |
| Too long after cleaning | sanitize lengthens a statement past 200 | refused | `ModelAnswerRefused` |
| No model | `local_model` unset, or Ollama down before the retry | row `waiting`; the first answer discarded; offered next harvest | not an attempt |
| Timeout | three calls time out across harvests | attempts counted; third fails it; `doctor` names it | `ModelTimedOut` |
| Nothing said | every utterance empty after cleaning | row `nothing said`; no call | N/A |
| Not ingested or purged | `summarize` on a meeting with no capture | refused before any call | named |
| A part failed ingestion | part 1 ingested, part 2 failed; row `ingest_failed` | not offered by the harvest; `summarize` refused before any call | names the failed part |
| Graph capture before parts | `-graph.vtt`, no part rows | read alone; one summary | N/A |
| Dropped transcript | `-manual.txt` from a `.md` drop | read as the drop read it; one summary | N/A |
| Drop | `transcript drop` in the CLI | ingested; row `waiting`; the daemon summarises at its next harvest | N/A |
| No daemon | `transcript summarize` with no daemon | refused | names `pm-ai daemon run` |
| Summarize from a terminal | `pm-ai transcript summarize`, daemon running | the line sent to `/v1/prompt`; acknowledged as a job; nothing summarised in the CLI process | N/A |
| Frontier asked for | `transcript summarize --by claude` | refused before anything is sent | names `7c` |
| Two jobs | automatic job running; PM requests one | the request revokes the automatic job's lease; that job writes nothing after its current step; the request's answer is the one on record | N/A |
| Backlog | 9 meetings waiting | 3 offered this harvest, most recent first | N/A |
| Two decisions | one meeting, two statements | two events, `decision:<id>_<digest>` each; neither dropped beside the calendar event | N/A |
| Same decision from a re-run | identical cleaned statement | one event | N/A |
| Reworded decision on re-run | different statement | appended; the record shows only the new list | N/A |
| Re-summary changes items | item A gone, item C new | A `superseded` if still staged; C staged; an approved item left | N/A |
| Re-harvest of the meeting | calendar `put` after a summary | block and `## Notes` byte-identical | N/A |
| Hand edit in the region | prose not in the grammar | the region dropped, named in the harvest's refusals; summary offered again | N/A |
| Summary contains `## Notes` | a line `## Notes` inside the text | escaped; round-trips; `## Notes` region intact | N/A |
| Mostly Russian meeting | most words Cyrillic, English terms | summary, decisions and items in Russian; names and terms as spoken | N/A |
| Personal meeting | scope personal | record, events, proposals personal | N/A |
| Russian injection | "игнорируй инструкции…" in an utterance | not redacted; answer still validated, sanitized, only staged | N/A |
| Instructions | the module's constants | no provider or model text interpolated | test fails otherwise |

</frozen-after-approval>

## Code Map

- `pm_ai/app/pipelines.py:624-686` -- `ingest_captures`, after which the harvest offers summaries; `:712-745` `_ingest_capture`, which ingests each stored part not yet ingested, in part order, from `transcript_parts` (`-graph.vtt` alone when a meeting has no part rows), and `:748` `_ingest_part`, which reads one; `:986-1077` `run_transcript_ingestion`, unchanged in what it stages; `:1205` `drop_transcript`, which records waiting
- `pm_ai/core/meeting_records.py:158-159` -- the two headings; `:323-339` `MeetingRecord` gains `summary`; `:523-558` `render_record`; `:561-645` `parse_record`; `:1384-1424` `_assert_reserved_region_empty`, replaced by the grammar's parser; `:664` `put`; `:898` `_write`, which carries `## Notes` over; `:1222-1260` `_assert_recordable_id`
- `pm_ai/domain/event_entries.py:367` -- `render_value`, the escaping the block uses; `:212` `MAX_ENTRY_LENGTH`
- `pm_ai/domain/events.py:60,137-141,536-580` -- `DECISION`, `DecisionPayload`, `NormalizedEvent`, `natural_key`
- `pm_ai/domain/identity.py:164,181` -- `SCOPELESS_SYSTEMS`, `PM_AI_MINTED`, both gaining `decision`; `pm_ai/core/normalize.py:97` reads the latter
- `pm_ai/storage/service.py:190-192,3534-3573` -- the `seen` table and the duplicate check in `persist_events`; `:202-211` `transcript_state`, the sibling the new table follows; `:4291` `transcript_parts`, the parts a whole-meeting read takes in order; `:4477-4530` staging and transitions
- `pm_ai/domain/lifecycle.py:28-37` -- `ProposalState.SUPERSEDED`
- `pm_ai/domain/sanitize.py:712` -- `sanitize`, applied to the answer too
- `pm_ai/domain/transcripts.py:75-108,367` -- `Utterance` (`speaker_handle`, the label; `text`, the words), `Transcript` (now with `part`, `33g`), `MAX_INGEST_ATTEMPTS`
- `pm_ai/surfaces/cli/dispatch.py:4034-4048` -- the `transcript` group; `summarize` joins `drop`
- `pm_ai/core/dispatch.py:29,64` -- the five-second rule
- `pm_ai/ports/__init__.py:1058-1063` -- the port's admitted limit (`instructions` is a plain `str`); `:1113` `complete`; `:1134-1179` `LocalModelUnavailable`, `RemoteModelRefused`, `NoLocalModel`, `PossiblyTruncated`, `OutputCapReached`, `ModelTimedOut`; `pm_ai/domain/model_calls.py:193` `ModelAnswer`
- `pm_ai/models/local/ollama.py:107,115,132` -- `NUM_CTX` 16,384, `NUM_PREDICT` 2,048, `MODEL_CALL_DEADLINE_SECONDS` 300; `:561` the server's refusal of an over-long prompt (`truncate: false`) raised as `PossiblyTruncated`; `:604-616` `OutputCapReached`
- `pm_ai/app/concurrency.py:438` -- `ModelPool`, `:507` `hand_off`; `pm_ai/app/wiring.py:340,353,629` -- `Daemon.model`, `Daemon.model_pool`, `require_model` (`NoLocalModel`); no router yet (`7c`)
- `pm_ai/app/entry.py:477` -- `DAEMON_BOUNDS`; `:1425` `_local_model_probe`, `doctor`'s `local model` line; `:1495` `_loopback`, today the prompt's only client of the daemon
- `pm_ai/surfaces/cli/completion.py:82` -- `FREE_TEXT_NAMES`, where `--meeting` is free text for every command today
- `tests/architecture/test_transcript_path_boundary.py:97-107` -- the pipeline still imports no model client; it reaches `Daemon.model`

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/domain/identity.py` -- `decision` in `SCOPELESS_SYSTEMS` and `PM_AI_MINTED`, after the spine amendment -- each decision its own key, never external
- [x] `pm_ai/core/meeting_summary.py` -- new, pure: the instruction constants (the summary prompt, the fixed retry sentences, one of them for the output cap), the schema with the trimmed limits, fragments from a transcript, the validator, the decision digest and proposal id, the region's grammar -- one shape, checked where defined
- [x] `pm_ai/core/transcripts.py` -- the whole-meeting reader, pure: given the meeting's part rows, its capture names and their texts, the utterances in order — parts in part order when there are rows; otherwise the `-graph.vtt` capture; otherwise the `-manual.vtt` or `-manual.txt` capture, read by the readers `transcript drop` used. In `core` because `core` may not import `storage` (`.importlinter` layering); the reads themselves are the pipeline's -- one meeting, one sequence
- [x] `pm_ai/models/local/ollama.py`, `tests/models/test_ollama_adapter.py` -- `NUM_PREDICT` 2,048 → 4,096 and its docstring (raised by `7b` so the trimmed answer shape fits with margin); the tests that read the constant still pass, and one states that the real-length recording plus the cap fits `NUM_CTX` -- the cap the answer limits are sized against
- [x] `pm_ai/core/meeting_records.py`, `pm_ai/domain/event_entries.py` -- `MeetingRecord.summary`; `put_summary`; the grammar's render and parse; carried over on `put`; an unparseable region dropped and reported -- the summary survives re-harvests
- [x] `pm_ai/storage/service.py`, `pm_ai/ports/__init__.py` -- the summary state table with its version, the lease taken by compare-and-set and checked in every claimed step; offered-meeting query with the bound; superseding staged items -- offered until done, once at a time
- [x] `pm_ai/app/pipelines.py`, `pm_ai/app/daemon.py` -- `summarize_meeting`: preparation on the I/O pool (the step ends there) — the part rows and captures read through storage and handed to the whole-meeting reader; the call on `daemon.model_pool` through `require_model` (no router until `7c`), never awaited from an I/O worker; one call per meeting, the retry, sanitize, the writes in order; offered after ingestion, only for a meeting whose row is `ingested`; `drop_transcript` records waiting -- one path in the daemon
- [x] `pm_ai/surfaces/cli/dispatch.py`, `pm_ai/app/entry.py`, `pm_ai/surfaces/cli/loopback.py` -- `transcript summarize` with `--by local` (default) and `--by claude` refused naming `7c`; typed in a terminal, the line is sent to `/v1/prompt` through the loopback client (`entry._loopback`) and its job followed, as at the prompt; never run in the CLI process; no daemon refused naming `pm-ai daemon run` -- on request
- [x] `pm_ai/platform/doctor.py`, `pm_ai/app/entry.py` -- waiting, failed and timing-out meetings named -- no silent backlog
- [x] `tests/core/test_meeting_summary.py`, `tests/slice/test_meeting_summary.py` -- one test per matrix row with a fake `ModelPort`; the instructions-are-constant check -- the matrix is the contract
- [x] `stories/11b-the-real-transcript-path.md` -- a dated Change Log line: `## Summary` no longer stays empty and its Never "no summary … no model" is narrowed by `7b` -- an earlier frozen block amended on the record
- [x] `pm_ai/app/entry.py` -- a `DAEMON_BOUNDS` entry for `transcript summarize` -- routed as AD-21 declares
- [x] `pm_ai/surfaces/cli/completion.py` -- `transcript summarize`'s `--scope` completes to enrolled scopes and `--by` to `local` and `claude`; `--meeting` stays free text, as for `transcript drop` -- `4u`'s test covers every option
**Acceptance Criteria:**
- Given a project meeting's ingested transcript and a fake model answering a valid shape with two decisions and three action items, when the harvest's summary job runs, then the record holds the block, the project's log holds two `decision` events attributed to pm-ai with actor `none:`, three `action_item` proposals are staged citing the meeting, and nothing is executed.
- Given that record, when the calendar harvest `put`s the same meeting again, then the summary block and `## Notes` are byte-identical.
- Given `uv run pytest`, then everything passes, the skip count is unchanged, and no test reaches a real Ollama.

## Spec Change Log

- **2026-10-16, created** (decision of 2026-10-16: a transcript yields a summary, decisions and action items, local model first). Carries: the whole transcript as sanitized fragments with constant instructions; one bounded JSON shape validated in `core`, sanitized; one retry, then a recorded failure; no model is waiting, not failing; the job on the model pool, writes as claimed steps; a summary state table; the `## Summary` region in a fixed grammar, never `## Notes`; decisions as events by pm-ai with actor `none:`; action items as staged `action_item` proposals; `transcript summarize`; the Russian-injection limit; CAP-3 measured from ingestion and from the command.
- **2026-10-16, amended before approval** (decisions of 2026-10-16). Long transcripts split at speaker turns and merged, never refused; each decision referenced `decision:<meeting id>_<digest>` after AD-34 is amended; output in the meeting's main language.
- **2026-10-16, review:** only the daemon summarises — `drop` records waiting, `summarize` goes through the loopback API or is refused; preparation, calls and writes on their own threads; one job per (scope, meeting), compare-and-set on the state row, a request beats an automatic job, 3 meetings per harvest, none older than 30 days; owners and `said_by` limited to speaker labels; bounds re-checked after cleaning; fixed retry sentences; chunk sizing from `7a`'s figure (2 characters per token until then), a 1,000-token floor, rounds of merging, model-unavailable mid-chunks is waiting; timeouts counted, third fails; "nothing said"; refusals before any call for a meeting not ingested or purged; `put_summary`, escaping, control characters, `MAX_ENTRY_LENGTH`, a hand-mangled region dropped and remade; the decision digest defined, `decision` in `PM_AI_MINTED`, decisions accumulate; action-item ids by digest with the scope, created at staging, superseded by a re-summary; write order; `people` meetings; plain words. Depends on spine amendments: **AD-34** — `decision` joins the closed list of scopeless reference kinds and `PM_AI_MINTED` (scopeless because the meeting it extends is; the scope is in the envelope and the duplicate check); **AD-33** — a decision event cites `decision:<meeting id>_<digest>`, which names its meeting; **AD-1** — local model egress as in `7a`; **AD-49** — `ModelAnswerRefused` is declared in `pm_ai/ports` beside the port. Amends `11b`'s frozen block (task above).
- **2026-10-16, review (hand-off):** the validated answer and superseded ids are returned for `7e`; the Change Log moved before Design Notes, as the template places it.
- **2026-10-16, review (cross-spec):** `drop` records waiting wherever it runs; `transcript summarize` gets a `DAEMON_BOUNDS` entry and completion sources. Depends on spine amendment **AD-21** — `transcript summarize` is a foreground administrative command parsed from the command line, acknowledged as a job (one amendment with `7c`'s and `7e`'s commands).
- **2026-10-16, spine gate:** frozen block edited (recorded here): no I/O worker waits on the model pool — preparation ends on the I/O worker and the job is handed to the model pool (AD-19); the state row is fenced by a lease taken with the first write and checked in every later claimed step, so a superseded job stops before its next write (AD-37), the Two jobs row restated; `ModelAnswerRefused` declared in `pm_ai.domain` beside the answer shape (AD-49).
- **2026-10-19, decision (Andrei):** frozen block edited (recorded here): the re-open of an automatic summary when a later part arrives is this slice's, not `33g`'s. `33g` ingests each new part on its own; "re-ingests" became "ingests the new part". AD-37 amended to bind "`7b`'s re-open when `33g` stores a later part".
- **2026-10-19, decided at `7a` (Andrei), frozen block not yet edited:** the local window is 16,384 tokens and a whole meeting fits one call, so 7b does not chunk. The frozen bullet "A long transcript is split, never refused for length" and its `PossiblyTruncated` split-and-retry are superseded. Every request also carries an output cap; an answer stopped there is refused as `OutputCapReached`, which 7b handles as an invalid answer (asked once more, then refused). Both are rewritten into this block at 7b's readiness.
- **2026-10-19:** Code Map re-verified against `d73e833` after 33g, 11d, 4v, 4w, 4s and 7a merged. Moved: `pipelines.py` `:606-653/672/912-1003/1117`→`:624-686/712-745/986-1077/1205`; `meeting_records.py` `:157-158/323-338/476-511/514-601/1337-1377/617/851/1175-1215`→`:158-159/323-339/523-558/561-645/1384-1424/664/898/1222-1260`; `event_entries.py` `:361`→`:367`; `events.py` `:132-136,529-573`→`:137-141,536-580`; `service.py` `:168,2700-2740/179-194/3421-3470`→`:190-192,3534-3573/202-211/4477-4530`; `transcripts.py` `:76-99,247`→`:75-108,367`; CLI `transcript` group `:2866-2878`→`:4034-4048`; boundary test `:95-106`→`:97-107`. Added: `_ingest_part`, `transcript_parts`, and 7a's port, refusals, adapter constants, model pool, `require_model`, `DAEMON_BOUNDS`, the `local model` line, `_loopback` and `FREE_TEXT_NAMES`. Frozen-block anchors now stale, not edited: `ports/__init__.py:991-996`→`:1058-1063`, `events.py:565`→`:572`, `service.py:2733`→`:3567`. Frozen block unchanged.
- **2026-10-19, readiness decision (Andrei), no chunking:** frozen block edited (recorded here). Carries the decision made at `7a`: a whole meeting is one call in the 16,384-token window. The Approach reads the transcript "in one call"; splitting is gone from "Where each step runs"; the bullet "A long transcript is split, never refused for length" is replaced by "A whole meeting is one call": a transcript that does not fit (`PossiblyTruncated`, or the server refusing it under `truncate: false`) fails naming the window and is not offered again, and `OutputCapReached` is an invalid answer, asked once more and then failed naming the cap. The no-model bullet says "before the call or before its retry"; Measured records prompt tokens, not a chunk count; Never's "No refusal for length" becomes "No splitting of a transcript". Matrix: the Long meeting, One very long turn, Merge in rounds and Chunk still too long rows are out; Too long for the window and Output cap are in; the No model row is reworded. The Tasks and the manual check lose chunks and merging.
- **2026-10-19, readiness decision (Andrei), output cap and answer limits:** frozen block edited (recorded here). At its bounds the old answer shape came to about 16,500 characters, some 5,600 tokens, past 7a's 2,048-token cap. The cap becomes 4,096 (`NUM_PREDICT`, a task here; a dated line in `7a`'s Change Log), which leaves 12,288 tokens for the prompt; the real-length recording needs 8,619. The limits are trimmed: decisions 15 → 10 and items 25 → 15, statements and `what` 300 → 200 characters. The frozen rule now says the limits are set so the largest valid answer fits the cap with margin: about 2,740 tokens at 2.92 characters per token, within three quarters of the cap. The build measures and records the figures, and trims again if needed. The "Too long after cleaning" row now says 200.
- **2026-10-19, readiness decision (Andrei), the whole-meeting reader:** frozen block edited (recorded here). `33g` ingests each part on its own and built no reader for a whole meeting, so the prerequisite no longer names "`33g`'s whole-meeting reading", and "What the model reads" no longer says "as `33g` reads a restarted recording". This slice builds the reader: parts in part order when the meeting has `transcript_parts` rows; otherwise its `-graph.vtt` capture; otherwise its dropped `-manual.*` capture. It is pure, in `pm_ai/core/transcripts.py`, because `core` may not import `storage`; the pipeline does the reads. Two matrix rows were added: a Graph capture from before parts existed, and a dropped transcript.
- **2026-10-19, readiness decision (Andrei), `transcript summarize` through the daemon:** frozen block edited (recorded here). Typed in a terminal or at the prompt, the command sends its line to `/v1/prompt` and is acknowledged as a job. It never summarises in its own process. `--by local` is the default; `--by claude` is refused by name until `7c`. Two matrix rows were added (from a terminal; `--by claude`). The CLI task now names `entry._loopback` and `loopback.py`, and `api/app.py` is dropped from it, since `/v1/prompt` exists. AD-21 already matches: it lists `transcript summarize` with `--by local|claude`, sent over the loopback API and acknowledged as a job, refused naming `pm-ai daemon run` with no daemon. No spine edit was needed for this.
- **2026-10-19, smaller readiness decisions (coordinating session):** frozen block edited (recorded here). (1) The model is reached through `require_model` on `daemon.model_pool` until `7c` builds the router. This is stated in "Where each step runs", and the pipeline task's "through the router" is corrected. (2) `--meeting` completion stays free text, as for `transcript drop`; that part of the completion task is dropped, and `--by` completes to `local` and `claude`. (3) AD-49 in ARCHITECTURE-SPINE.md gains `OutputCapReached` beside `PossiblyTruncated`, with the tag `[2026-10-19, 7a, 7b]`. (4) A meeting is summarised only when its transcript row is `ingested`. A meeting with a part that failed ingestion is not offered and, when requested, is refused naming the failed part. The harvest bullet is reworded and a matrix row added.
- **2026-10-19, readiness (cosmetic):** frozen block edited (recorded here): three cited lines moved by the merged slices are corrected (`ports/__init__.py:991-996` → `:1058-1063`, `events.py:565` → `:572`, `service.py:2733` → `:3567`), as for `33i` and `7a`. No behaviour changes.
- **2026-10-19, built:** where the build differs from the Tasks' wording. The slice test is `tests/slice/test_meeting_summary_slice.py`, since pytest refuses two test modules with one basename. Doctor's summaries line is in `pm_ai/app/entry.py` beside the `local model` line, and `loopback.py` needed no change. `render_value` quotes a leading `#` or `-` only when asked (`quote_marks=True`), because quoting by default changed pinned ledger lines such as `p.channel=#payments`. "No words left after cleaning" means no letter or digit, because the sanitizer leaves a lone zero-width character in place. `benchmarks/meeting_summary.py` is new: it runs this slice's own path against the local model, by hand.
- **2026-10-19, review (three layers):** 21 patches applied, 0 intent or spec gaps. The main ones:
  - a summarize job whose command raised after the job started left its row running;
  - an answer that passed validation but could not be rendered was asked for again at every harvest;
  - an automatic job could act on a row a request had already moved;
  - one meeting's supersession could reach another meeting whose id extends it;
  - a superseded item that came back was reported as acted on by the PM;
  - with no local model, every harvest still read captures.

  Tests were added for the daemon's request path, its pool wiring, a lease revoked before each write, and the reset and re-open paths through the harvest itself. Two findings need Andrei's decision and are in `deferred-work.md`: the measured figures are not yet recorded, and the 600-second bound does not count time queued.

## Design Notes

The retry's sentence differs from the first prompt, or temperature 0 and a fixed seed would return the same invalid answer; it names only the rule, so neither the model's answer nor the transcript can steer the second prompt. The same determinism is why a twice-refused summary is not offered again automatically.

Choices made in review (2026-10-16): 3 meetings per harvest and nothing older than 30 days, so a backlog after a long absence costs bounded model time and never touches purged captures; owners limited to speaker labels, because a free-text owner invites the model to invent a person and `7e` needs a name the transcript actually carries; a hand-edited summary region is dropped and remade rather than kept, because keeping it would present a human's text as pm-ai's; decisions accumulate rather than being withdrawn, because the event log is append-only and a re-summary is a second reading, not a correction.

Measured figures (2026-10-20). The coordinating session ran `benchmarks/meeting_summary.py --model qwen3:8b` in a throwaway root, because the build sandbox cannot reach Ollama. Machine: arm64 Python, M3 Pro, 18 GB. Both inputs used the 10-decision limit of the time.

| Input | Prompt tokens | Answer | Decisions / items | From ingestion | From the command |
|---|---|---|---|---|---|
| Authored meeting (`mixed_ru_en.vtt`) | 2,873 | 382 tokens, 1,144 characters | 5 / 6 | 28.7 s | 27.0 s |
| Redacted recording | 8,789 | 716 tokens, 2,660 characters | 10 / 11 | 67.5 s | 67.2 s |

- Each summary was one call, with no retries.
- The largest answer seen was 3.72 characters per token.
- At 10 decisions, the largest answer the limits allowed (8,158 characters) projects to 2,196 tokens, against three quarters of the cap (3,072). At 25 decisions (12,268 characters) it projects to about 3,300 tokens. That figure is computed, not measured.
- The 600-second Ask First did not trigger.
- Neither summary was Cyrillic (`cyrillic_share` 0.0 on both). The authored meeting's spoken letters are 44% Cyrillic, so English is its majority by letters. The redacted recording's letters are 82% Cyrillic, but its words are scrambled. The language rule stays unverified until the run on `mostly_ru.vtt` (97% Cyrillic).
- Swap went from 0 to 4.2 GB during the run, with other apps open (Office, Chrome, ChatGPT). This is noted as an observation, not a result.

CAP-3's figure from meeting end is not claimed. The figures from ingestion exclude the harvest's interval, because the script runs the job inline right after ingestion.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; skip count unchanged
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- With the model pulled and the daemon running in a throwaway home, `pm-ai transcript drop` the authored fixture into the personal scope; after the next harvest the record's `## Summary` holds the block and `pm-ai doctor` shows nothing waiting.
- `uv run python benchmarks/local_model.py` with this slice's instructions; its scores, prompt tokens, the largest valid answer's characters and tokens, and times are written into the Design Notes.

## Suggested Review Order

**The job and where each stage runs**

- Entry point: the stages, each naming the pool it runs on; no I/O worker waits on the model.
  [`pipelines.py:2504`](../../../../pm_ai/app/pipelines.py#L2504)

- Preparation: the record, the transcript row, the captures, refused by name before any call.
  [`pipelines.py:2396`](../../../../pm_ai/app/pipelines.py#L2396)

- One job in flight per meeting; the pool hand-offs and the stopping paths.
  [`pipelines.py:2805`](../../../../pm_ai/app/pipelines.py#L2805)

- The daemon's wiring of the jobs onto its I/O and model pools.
  [`daemon.py:711`](../../../../pm_ai/app/daemon.py#L711)

- After a harvest: at most 3, most recent first, none older than 30 days, only ingested meetings.
  [`pipelines.py:2959`](../../../../pm_ai/app/pipelines.py#L2959)

- Ingestion records waiting; a later part re-opens an automatic summary.
  [`pipelines.py:3087`](../../../../pm_ai/app/pipelines.py#L3087)

**The answer shape**

- The constant instructions; nothing from a provider or model is interpolated.
  [`meeting_summary.py:89`](../../../../pm_ai/core/meeting_summary.py#L89)

- The validator: checked, cleaned, checked again, refused by rule.
  [`meeting_summary.py:380`](../../../../pm_ai/core/meeting_summary.py#L380)

- Fragments and the labels an owner must match.
  [`meeting_summary.py:238`](../../../../pm_ai/core/meeting_summary.py#L238)

- Keys by digest, never by position: decisions and action items.
  [`meeting_summary.py:481`](../../../../pm_ai/core/meeting_summary.py#L481)

- The refusal and the limits, declared in `domain` beside the shape.
  [`meeting_summaries.py:92`](../../../../pm_ai/domain/meeting_summaries.py#L92)

**The lease and the writes**

- The lease taken by compare-and-set; every later write checks it.
  [`service.py:4661`](../../../../pm_ai/storage/service.py#L4661)

- The fence check inside each claimed step.
  [`service.py:4492`](../../../../pm_ai/storage/service.py#L4492)

- Staging under the lease, with supersession matched on the exact id form.
  [`service.py:4784`](../../../../pm_ai/storage/service.py#L4784)

- The state table, with no schema version bump.
  [`service.py:248`](../../../../pm_ai/storage/service.py#L248)

**The record's region**

- `put_summary`: the region written under the lease check.
  [`meeting_records.py:766`](../../../../pm_ai/core/meeting_records.py#L766)

- The grammar's render and parse; a hand edit is refused and dropped.
  [`meeting_summary.py:538`](../../../../pm_ai/core/meeting_summary.py#L538)

**On request**

- From a terminal, the line goes to the daemon and its job is followed.
  [`entry.py:378`](../../../../pm_ai/app/entry.py#L378)

- The daemon's job, settled by whichever finishes last, the command or the summary.
  [`prompts.py:854`](../../../../pm_ai/app/prompts.py#L854)

- Checked and refused by name: `--by claude`, no daemon, not ingested, a failed part.
  [`pipelines.py:3025`](../../../../pm_ai/app/pipelines.py#L3025)

**Peripherals**

- The whole-meeting reader: parts in order, else the Graph capture, else the dropped one.
  [`transcripts.py:556`](../../../../pm_ai/core/transcripts.py#L556)

- `decision` is scopeless and minted by pm-ai.
  [`identity.py:167`](../../../../pm_ai/domain/identity.py#L167)

- The output cap is raised to 4,096.
  [`ollama.py:115`](../../../../pm_ai/models/local/ollama.py#L115)

- Doctor's summaries line.
  [`entry.py:1536`](../../../../pm_ai/app/entry.py#L1536)

- The benchmark for this slice's own path, run by hand.
  [`meeting_summary.py`](../../../../benchmarks/meeting_summary.py)

- One test or more per matrix row.
  [`test_meeting_summary_slice.py`](../../../../tests/slice/test_meeting_summary_slice.py)
