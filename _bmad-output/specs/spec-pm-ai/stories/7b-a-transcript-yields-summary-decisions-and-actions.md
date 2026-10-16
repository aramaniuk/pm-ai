---
title: 'A transcript yields a summary, decisions and action items'
type: 'feature'
created: '2026-10-16'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** An ingested transcript yields only what pattern matching finds — a `pm-ai, <verb>` command or an "I'll … by …" promise — and on the field-test transcripts, mixed Russian and English with speaker tags, that is nothing. The meeting record's `## Summary` region is reserved and empty, and no decision has ever been written to an event log, though `[TYPE: DECISION]` is declared for it (CAP-27).

**Approach:** After a transcript is ingested — by the harvest, or by `transcript drop` once the daemon picks it up — or when the PM asks, the local model (`7a`) reads the cleaned transcript, in chunks when it is long, and answers in one bounded, validated shape: a summary, the decisions made, the action items agreed. The summary goes into the record's `## Summary` region; each decision becomes a decision event in the meeting's scope; each action item becomes a staged proposal, never executed.

## Boundaries & Constraints

**Always:**
- **Prerequisites:** `7a`; `33g`'s whole-meeting reading; the spine amendments listed in the Change Log — above all AD-34 gaining the `decision` reference kind — made through `bmad-architecture` before this slice is built.
- **What the model reads.** The meeting's whole transcript — every stored part in time order as one sequence, as `33g` reads a restarted recording — with each utterance one `Sanitized` fragment of its speaker label and words, and the meeting's title another. Instructions are constants pm-ai wrote; no provider or model text is interpolated into them (the port's admitted limit, `ports/__init__.py:991-996`, closed here by a test). Task class `extraction`, local-only (AD-15); `flow` from the meeting's scope to the meeting's scope. A transcript with no words left after cleaning records "nothing said" and makes no call.
- **Where each step runs (AD-5, AD-19).** Reading the parts, sanitizing and splitting run on an I/O worker; each model call on the model pool; every write is a claimed step on the writer thread. Only the daemon summarises: `transcript drop` records the meeting as waiting wherever it runs — in the CLI process, or in the daemon when typed at the prompt with a daemon running — and the daemon picks it up at its next harvest. `transcript summarize` is sent to the running daemon over the loopback API, with its own `DAEMON_BOUNDS` entry, and acknowledged as a job (AD-21); with no daemon running it is refused, naming `pm-ai daemon run`.
- **One answer shape, validated in `core`.** A JSON object with exactly three keys: `summary` (1–600 characters), `decisions` (at most 15, each `statement` of 1–300 characters and `said_by`), `action_items` (at most 25, each `what` of 1–300 characters, `owner`, and `due` as said or null, at most 40 characters). `said_by` and `owner` are each one of the transcript's speaker labels exactly, or null; a person named but not speaking stays in the item's text. The schema goes to the model as its `shape`; validation does not trust that it was followed. Every returned string then passes `sanitize`, and the bounds and non-emptiness are checked again on the cleaned text. Anything else — not JSON, an extra key, a bound broken before or after cleaning, an empty string, a label not in the transcript — is refused as `ModelAnswerRefused` naming the rule.
- **In the language most of the meeting was held in**, names and terms kept as spoken; the instructions say so and nothing translates afterwards.
- **One retry per call, then a recorded failure.** A refused answer is asked for once more with one sentence appended to the instructions, chosen from a fixed set naming the broken rule — never quoting the answer or the transcript. A second refusal fails the summary with that rule, and the harvest does not offer it again — at temperature 0 the same input gives the same answer; the PM may ask (`transcript summarize`) or ask for a frontier summary (`7c`).
- **A long transcript is split, never refused for length.** When it does not fit the window with room for the answer — estimated with `7a`'s recorded characters per token for the chosen model less a fifth, and 2 characters per token until that figure exists — it is split at speaker turns into consecutive chunks that fit; a single turn longer than a chunk is split at a sentence end, then at a word. Each chunk is summarised in the same shape; then a merge call reads the chunk answers (model text, sanitized, as fragments) and answers once more in the same shape, duplicates folded and the bounds checked again; merge input too long for the window is merged in rounds. A chunk or merge answer that comes back `PossiblyTruncated` is split in two and asked again, down to a floor of 1,000 estimated tokens, below which the summary fails naming that floor. Speaker labels stay with their words in every chunk. Each call validates and retries once on its own. With `keep_alive: 0` every call reloads the model, so a long meeting's time is mostly loading; the benchmark records it.
- **No local model is not a failure.** No `local_model`, Ollama not answering, or the model not pulled — before the first call or between chunks — records the meeting as waiting, discards any chunk answers, and is not an attempt; it is offered again at the next harvest. A deadline is an attempt: each model call has 300 seconds, a timed-out summary is offered again at the next harvest, and the third timeout fails it as timing out; `doctor` names the meetings waiting, failed and timing out under the `local model` line.
- **What a harvest offers, bounded.** One summary job in flight per (scope, meeting). At most 3 meetings per harvest, the most recent first; a meeting whose start is more than 30 days old is never offered automatically (its transcript is purged at 30 days, NFR-09). A meeting with no ingested row, no capture, or a purged capture is refused by name before any model call, whether offered or requested.
- **The summary's state is a row** in a new operational table: scope, meeting id, `waiting` / `summarized` / `failed` / `nothing said`, who made it (`local` here), whether it was asked for, the model's name, attempts, a reason, the instant recorded, and a version. A job reads the version when it starts and its final write compares and sets it; a requested summary sets the row first, so an automatic job still running loses its write and is discarded. No schema version bump, as `11b`'s tables.
- **A later part re-opens an automatic summary.** When `33g` stores a later part and re-ingests, a summary the harvest made is offered again over the whole meeting; one the PM asked for is kept, replaced only by another request. If an earlier part has been purged, nothing is re-opened.
- **The record's `## Summary` region is pm-ai's and now holds the summary.** It is written by `MeetingRecords.put_summary` — `put` takes a `Meeting` and never writes it — in a fixed grammar: a provenance line (made by the local model `<name>`, automatically or on request, on `<date>`), the summary, a `Decisions` list (statement, said by), an `Action items` list. Every rendered string goes through the record's one value encoding (`render_value`, which escapes newlines), is stripped of control characters, and stays within `MAX_ENTRY_LENGTH`; a leading `#` or `-` is escaped too (`render_value` is extended if it does not already). A summary containing `## Notes` round-trips unchanged. `MeetingRecord` gains `summary`, carried over on every `put` as `## Notes` is. A calendar `put` meeting a region that does not parse keeps the rest of the record, drops that region, names it in the harvest's refusals and sets the summary row back to offered, so it is made again — a hand edit in pm-ai's region is never kept as pm-ai's. `## Notes` is the human's and is never written.
- **Decisions are events in the meeting's scope (CAP-27, AD-38), each with its own reference.** Each `decision` event cites `decision:<meeting id>_<digest>`, the digest the first 12 hex characters of SHA-256 over the decision's cleaned, trimmed statement. Scopeless, as `meeting:` is, because the meeting it extends is; the event's scope is in its envelope and in storage's duplicate check `(scope, system, reference)`, so the same decision from a re-run is one event and no decision is dropped beside the calendar event for the meeting (`events.py:565`, `service.py:2733`). A meeting id that would not parse in that form already fails `meeting:<id>`, so none reaches here. `decision` is added to `PM_AI_MINTED`, so normalisation never attributes one to an outside source. Payload `DecisionPayload(statement)`; who said it lives only in the record's list; actor `none:` — a speaker label is not a person (AD-34); authored by pm-ai (AD-36): pm-ai's reading of a transcript is never evidence a commitment was kept; time is the meeting's start. **Decisions accumulate:** a re-summary appends decisions it words differently and never removes an earlier one — the log holds every reading pm-ai made, the record shows the current one; both are kept on purpose.
- **Action items are staged proposals, never executed.** Type `action_item`, no target, payload `what`, `owner`, `due`, summary the first 80 characters of `what`, citing the meeting (AD-33), created at the staging instant. Id `prp_<scope digest>_<meeting id>_ai_<digest of the cleaned what and owner>` — the scope included, since a meeting may have a record in more than one scope; never by position. Staged through `stage_unless_acted`. A new summary supersedes: each still-staged `action_item` of that scope and meeting whose id the new summary does not produce is moved to `superseded` by compare-and-set; one the PM acted on is left as it is. `11b`'s pattern extraction is unchanged; an item both find is staged twice, under two types.
- **The validated answer is handed on.** `summarize_meeting` returns the validated answer — items with their ids and owner labels, decisions with their references — and the ids it superseded, so `7e` reads the answer rather than parsing the record, whose summary block is text only.
- **Write order, each step complete on its own:** the record's block, the decision events, the proposals, then the state row. A failure part-way leaves the row unchanged, so the meeting is offered again, and a re-run repeats only what is missing — every write is keyed.
- **Everything lands in the meeting's scope.** A personal meeting's summary, decisions and action items are personal; a `people` meeting's, if a record exists, stay in `people` and are never shown on a dashboard (`7d`).
- **The filter's limit, stated.** The sanitizer's phrase lists are English; an injection spoken in Russian is not matched (`sanitize.py` holds no Cyrillic). What bounds it: a fixed shape, bounded lengths, sanitized output, nothing executed.
- **Measured, not claimed.** pm-ai observes no meeting end in this wave (the harvest runs every 240 minutes), so CAP-3's figure from meeting end is not claimed. Measured on `7a`'s fixture and recorded in the Design Notes: from ingestion to the record written, and from the PM's command to the record written, with the chunk count, each call's model time and the total.

**Ask First:**
- If ingestion-to-written exceeds 600 seconds on `7a`'s fixture.

**Never:** No frontier call (`7c`). No refusal for length. No execution of anything the model returns. No write to `## Notes`. No owner resolved to a person. No model in the CLI process. No dashboard change (`7d`), and no re-render when a summary lands. No change to pattern extraction or to `11b`'s execute rule.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Harvest ingests a transcript | Graph capture ingested; model ready | block in the record; one event per decision; one staged proposal per action item; row `summarized`, `local`, model named | N/A |
| Multi-part meeting | two stored parts | read as one sequence; one summary | N/A |
| Later part arrives | summarized automatically; `33g` stores part 2 | summarized again over both parts | a requested summary is kept |
| Long meeting | transcript over the window | split at speaker turns; chunks summarised; one merged answer; row names the chunk count | N/A |
| One very long turn | a turn longer than a chunk | split at a sentence end, then a word | N/A |
| Merge in rounds | chunk answers over the window | merged in rounds into one answer | N/A |
| Chunk still too long | `PossiblyTruncated` | halved and asked again; below 1,000 tokens fails | names the floor |
| First answer invalid | an extra key | asked once more with the fixed sentence for that rule | N/A |
| Both invalid | not JSON twice | nothing written; row `failed` naming the rule; not offered again automatically | `ModelAnswerRefused` |
| Label not in transcript | `owner: "Ваня"`, no such speaker | refused as an unknown label | `ModelAnswerRefused` |
| Too long after cleaning | sanitize lengthens a statement past 300 | refused | `ModelAnswerRefused` |
| No model | `local_model` unset, or Ollama down mid-chunks | row `waiting`; chunk answers discarded; offered next harvest | not an attempt |
| Timeout | three calls time out across harvests | attempts counted; third fails it; `doctor` names it | `ModelTimedOut` |
| Nothing said | every utterance empty after cleaning | row `nothing said`; no call | N/A |
| Not ingested or purged | `summarize` on a meeting with no capture | refused before any call | named |
| Drop | `transcript drop` in the CLI | ingested; row `waiting`; the daemon summarises at its next harvest | N/A |
| No daemon | `transcript summarize` with no daemon | refused | names `pm-ai daemon run` |
| Two jobs | automatic job running; PM requests one | the request's write wins; the automatic one discarded | N/A |
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

- `pm_ai/app/pipelines.py:606-653` -- `ingest_captures`, after which the harvest offers summaries; `:672` `_ingest_capture` reads one capture (`33g` makes it every part); `:912-1003` `run_transcript_ingestion`, unchanged in what it stages; `:1117` `drop_transcript`, which records waiting
- `pm_ai/core/meeting_records.py:157-158` -- the two headings; `:323-338` `MeetingRecord` gains `summary`; `:476-511` `render_record`; `:514-601` `parse_record`; `:1337-1377` `_assert_reserved_region_empty`, replaced by the grammar's parser; `:617` `put`; `:851` `_write`, which carries `## Notes` over; `:1175-1215` `_assert_recordable_id`
- `pm_ai/domain/event_entries.py:361` -- `render_value`, the escaping the block uses; `MAX_ENTRY_LENGTH`
- `pm_ai/domain/events.py:60,132-136,529-573` -- `DECISION`, `DecisionPayload`, `NormalizedEvent`, `natural_key`
- `pm_ai/domain/identity.py:164,181` -- `SCOPELESS_SYSTEMS`, `PM_AI_MINTED`, both gaining `decision`; `pm_ai/core/normalize.py:97` reads the latter
- `pm_ai/storage/service.py:168,2700-2740` -- the duplicate check in `persist_events`; `:179-194` `transcript_state`, the sibling the new table follows; `:3421-3470` staging and transitions
- `pm_ai/domain/lifecycle.py:28-37` -- `ProposalState.SUPERSEDED`
- `pm_ai/domain/sanitize.py:712` -- `sanitize`, applied to the answer too
- `pm_ai/domain/transcripts.py:76-99,247` -- `Utterance` (label, words), `Transcript`
- `pm_ai/surfaces/cli/dispatch.py:2866-2878` -- the `transcript` group; `summarize` joins `drop`
- `pm_ai/core/dispatch.py:29,64` -- the five-second rule
- `tests/architecture/test_transcript_path_boundary.py:95-106` -- the pipeline still imports no model client; it reaches `Daemon.model`

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/identity.py` -- `decision` in `SCOPELESS_SYSTEMS` and `PM_AI_MINTED`, after the spine amendment -- each decision its own key, never external
- [ ] `pm_ai/core/meeting_summary.py` -- new, pure: the instruction constants (chunk, merge, the fixed retry sentences), the schema, fragments from a transcript, the split, the validator, the decision digest and proposal id, the region's grammar -- one shape, checked where defined
- [ ] `pm_ai/core/meeting_records.py`, `pm_ai/domain/event_entries.py` -- `MeetingRecord.summary`; `put_summary`; the grammar's render and parse; carried over on `put`; an unparseable region dropped and reported -- the summary survives re-harvests
- [ ] `pm_ai/storage/service.py`, `pm_ai/ports/__init__.py` -- the summary state table with its version and compare-and-set; offered-meeting query with the bound; superseding staged items -- offered until done, once at a time
- [ ] `pm_ai/app/pipelines.py`, `pm_ai/app/daemon.py` -- `summarize_meeting`: preparation on the I/O pool, calls on the model pool, chunks and merge, retries, sanitize, the writes in order; offered after ingestion; `drop_transcript` records waiting -- one path in the daemon
- [ ] `pm_ai/surfaces/cli/dispatch.py`, `pm_ai/surfaces/api/app.py` -- `transcript summarize`, sent to the daemon -- on request
- [ ] `pm_ai/platform/doctor.py`, `pm_ai/app/entry.py` -- waiting, failed and timing-out meetings named -- no silent backlog
- [ ] `tests/core/test_meeting_summary.py`, `tests/slice/test_meeting_summary.py` -- one test per matrix row with a fake `ModelPort`; the instructions-are-constant check -- the matrix is the contract
- [ ] `stories/11b-the-real-transcript-path.md` -- a dated Change Log line: `## Summary` no longer stays empty and its Never "no summary … no model" is narrowed by `7b` -- an earlier frozen block amended on the record
- [ ] `pm_ai/app/entry.py` -- a `DAEMON_BOUNDS` entry for `transcript summarize` -- routed as AD-21 declares
- [ ] `pm_ai/surfaces/cli/completion.py` -- `transcript summarize`'s `--scope` and `--meeting` given value sources (enrolled scopes; the scope's meeting ids) -- `4u`'s test covers every option

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

## Design Notes

The retry's sentence differs from the first prompt, or temperature 0 and a fixed seed would return the same invalid answer; it names only the rule, so neither the model's answer nor the transcript can steer the second prompt. The same determinism is why a twice-refused summary is not offered again automatically.

Choices made in review (2026-10-16): 3 meetings per harvest and nothing older than 30 days, so a backlog after a long absence costs bounded model time and never touches purged captures; owners limited to speaker labels, because a free-text owner invites the model to invent a person and `7e` needs a name the transcript actually carries; a hand-edited summary region is dropped and remade rather than kept, because keeping it would present a human's text as pm-ai's; decisions accumulate rather than being withdrawn, because the event log is append-only and a re-summary is a second reading, not a correction.

Measured figures: none yet.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; skip count unchanged
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual checks:**
- With the model pulled and the daemon running in a throwaway home, `pm-ai transcript drop` the authored fixture into the personal scope; after the next harvest the record's `## Summary` holds the block and `pm-ai doctor` shows nothing waiting.
- `uv run python benchmarks/local_model.py` with this slice's instructions; its scores, chunk counts and times are written into the Design Notes.

