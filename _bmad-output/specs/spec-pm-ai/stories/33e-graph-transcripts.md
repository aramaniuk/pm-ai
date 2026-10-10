---
title: 'A recorded meeting''s transcript is fetched from Microsoft Graph and kept with the meeting'
type: 'feature'
created: '2026-10-10'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** pm-ai records every meeting the PM held or attended but never obtains what was said in it. The Graph transcript adapter is a stub over a fake table, and the transcript source every meeting feature is meant to sit behind exists only as a name in a docstring. The spike against a real tenant settled the real path: a meeting's join link leads to one online meeting holding a transcript per recorded occurrence; a transcript says when it was created and ended, its text is a separate download; and a tenant can switch transcript access off with no way round it.

**Approach:** After each calendar harvest has saved where it got to, pm-ai looks at every meeting recorded within the harvest's reach-back that has no transcript yet — held or attended, since Microsoft lets anyone on the calendar invite read the transcript — asks Graph whether one exists, matches it to the right occurrence by time, downloads it and stores it verbatim as a capture in the meeting's own scope. Where each meeting got to is recorded in the operational store, so an outage, a provider fault or a transcript Teams finishes later is picked up on a later run. Acquisition only; `11b` reads the capture.

## Boundaries & Constraints

**Always:**
- **The first task renames the Graph transcript module**, so the package no longer holds two modules whose last segment is `graph`: `connectors/transcripts/graph.py` becomes `graph_transcripts.py`.
- **Which meetings are tried:** every ended meeting recorded in the scope whose date lies within the harvest's reach-back, that holds a calendar reference and has neither a capture nor a recorded outcome that closes it — not only the meetings this run's harvest returned, so a fault or an outage never makes a meeting unfetchable before it leaves the reach-back, after which it is not tried again. A record with no calendar reference (dropped by hand) is never tried; neither is a meeting the PM declined (decided 2026-10-10: the calendar slice writes no record for it).
- **Where a meeting got to is recorded in the operational store (Tier 2), never on the record (AD-33).** One table, one row per meeting in a scope — state, reason, when: *fetched*; *refused* with its reason, and a refusal that is not retryable is not tried again, the row being why; *absent* (not recorded, or not the PM's to read), tried again while in the reach-back; *skipped* (still being produced), tried next run. `11b` adds the ingestion states; every fetched capture with no ingestion row is handed to `11b`'s ingestion by this acquisition and by `9a`'s cycle, so a daemon killed between capture and ingestion is repaired next run. The table is created on open like the others; the store's version number does not change.
- **Acquisition runs after the harvest's cursor and coverage are saved**, so a fault in it never loses a successful harvest's progress; an error it has no reading for becomes a named refusal for that meeting, never a crash. Transcripts earn no coverage window, and the meeting record is not touched.
- **The join link is resolved from the stored calendar event id**, never stored. A meeting that is not an online meeting, or not a Teams one, has no transcript, and that is not a failure. A lookup answering nothing or 404 means the meeting is not the PM's to read — not organiser or invited, or expired — and is recorded absent with that reason, as is a listing or download saying the meeting has expired. A lookup answering more than one online meeting is refused naming the ids.
- **A series is one online meeting; its transcripts are matched to occurrences by time.** Meetings sharing one join link are resolved once and their transcripts listed once, newest first, stopping once a transcript was created earlier than the reach-back's start less 15 minutes; if the client's page cap is hit before that, the meeting is refused by name. A transcript belongs to the occurrence whose window — start to end, widened by 15 minutes either side — contains the instant it was created, provided it ended after the occurrence started. (This 15 minutes widens a placement window; `11b`'s is a start-to-start match tolerance — two figures, not one rule.) A transcript with no created or no end instant is still being produced: skipped this run. One fitting no occurrence of a one-off meeting, one fitting two occurrences, or two fitting one, is refused naming the transcript ids and the meeting; nothing is stored. One fitting none of a series' occurrences in this run is left alone.
- **The content is fetched as WebVTT and stored verbatim** (AD-29), through the single writer's capture path (AD-43, AD-47): git asked about the final directory, staged in `transcripts/temp/`, then placed in `transcripts/` in the meeting's scope. **One naming rule, owned here and called by `11b` and `11c`:** the record's filename without `.md`, then `-graph.vtt` for a Graph capture or `-manual.vtt` / `-manual.txt` for a dropped file; "has a capture" means any file in `transcripts/` whose name starts with that stem, so a Graph capture and a hand-dropped one cannot both exist unseen. A taken name is refused as a duplicate; nothing is overwritten or appended to. Content that is not UTF-8 text is refused by name. An empty or whitespace-only download is refused by name and tried again next run — Teams may still be writing. A download answering 404 after the listing named it is refused by name, not retried.
- **A tenant that has switched transcript access off** (403 whose detailed code is `GraphAccessToTranscriptsDisabled`) degrades that meeting to a named, non-retryable refusal; the harvest and the other meetings continue. A tenant forbidding speaker-attributed content (`SpeakerAttributionNotAllowed` on the download) is answered by asking once more for Microsoft's unattributed text format: the capture then carries timings and words but no speaker names, so every extraction `11b` makes from it stages — words without names over nothing, chosen and said so in the report.
- **A plain 403 is a permission refusal, named by permission, not retried:** `OnlineMeetings.Read` on the join-link lookup, `OnlineMeetingTranscript.Read.All` on the listing or download. Both are in the declared set and enrolment refuses a partial grant, so either can only come from an enrolment older than the declaration. Neither is the tenant switch.
- **Whether a project transcript may be written is decided by the existing check that git is excluding the directory.** A refusal there is named per meeting; the other meetings continue.
- **One provider fault stops the rest of the run.** A throttle, a server error or no answer makes that meeting's refusal retryable, and the remaining meetings wait for the next run, with the hint carried.
- **The transcript source becomes a real port** in `pm_ai/ports/`, with the refusals its callers handle declared beside it (AD-49): the acquisition contract, implemented by the Graph adapter here and by the manual adapter (`11c`'s dropped file), both yielding captures. Reading a capture's text is `11b`'s separate contract.
- **A connector's meeting id is a calendar reference, never a record id** (gate F2, 2026-10-10): the record accessor resolves and returns the kept id, and `app` fills the held-meeting event's id after the record is written — built in `11b`, relied on here, defined nowhere else.
- **With no Graph connector enrolled**, the daemon's Graph transcript source is a stand-in that refuses by name ("no Graph connector is enrolled").
- **Read-only**, over the existing Graph client with its origin check, page cap, throttle handling and token refresh.

**Ask First:** Nothing — decided 2026-10-10.

**Never:** No parsing, utterances, speakers, extraction, ingestion or summary — `11b`. No manual drop, no watched folder, no `pm_ai.core.transcripts`. No `.docx`. No retry on the tenant switch. No pointer or status on the record. No scheduler: every acceptance criterion runs from tests until `9a` gives the harvest a production caller, and acquisition time then counts inside `9a`'s one-at-a-time harvest. No writes to Graph. No new permission.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Recorded one-off meeting, held or attended | one transcript fits its window | `transcripts/<record stem>-graph.vtt` in the meeting's scope, bytes identical to the download; state fetched | N/A |
| Recorded on an earlier run, missed | in the reach-back, no capture, state absent or retryable | tried this run, not only this run's records | N/A |
| Not recorded | no transcript listed | nothing stored; state absent; tried again while in the reach-back | N/A |
| Not tried | no join link, a non-Teams link, or no calendar reference | no Graph request; nothing refused | N/A |
| Not the PM's to read | lookup answers nothing or 404; or listing or download says expired | state absent with that reason; no fault | N/A |
| Lookup answers two online meetings | one join link, two ids | refused naming both ids | non-retryable |
| Series, three occurrences in the window | one online meeting, two transcripts | resolved and listed once, newest first; each transcript to the occurrence it fits; the third gets nothing | N/A |
| Listing reaches old transcripts | one created before reach-back start less 15 min | no further page fetched | N/A |
| Page cap hit first | long-running series | meeting refused by name; nothing stored | non-retryable |
| Transcript still being produced | no end instant or no created instant | state skipped; tried next run | N/A |
| Fits no occurrence, two occurrences, or two fit one | created outside the widened window; recording restarted | refused naming transcript(s) and meeting; nothing stored | non-retryable |
| Already captured | any file in `transcripts/` starting with the record stem, Graph or manual | not tried; no Graph request | N/A |
| Refused earlier, not retryable | state refused, non-retryable | not tried again | N/A |
| Race on the name | capture appears between check and write | refused as a duplicate; nothing overwritten | `CaptureAlreadyExists` |
| Tenant switched transcripts off | 403 + `GraphAccessToTranscriptsDisabled` | that meeting refused by name; harvest and other meetings unaffected | non-retryable |
| Speaker attribution off | 403 + `SpeakerAttributionNotAllowed` on content | asked again in the unattributed format; stored under the same name; report says unattributed | N/A |
| Permission missing | plain 403 on lookup; on listing or download | refused naming `OnlineMeetings.Read`; `OnlineMeetingTranscript.Read.All` | non-retryable |
| Gone | 404 on the calendar event; 404 on the download after the listing | refused by name; nothing stored | non-retryable |
| Empty download | zero bytes or whitespace only | refused by name; nothing stored; tried next run | retryable |
| Throttled on the second meeting | 429 | first capture kept; second refused with the hint; the rest left for next run | retryable |
| Project directory git would commit | the exclusion check refuses | that meeting refused by name; others continue | `UnprotectedCaptureDir` |
| Content not UTF-8 | undecodable bytes | refused by name; nothing stored | non-retryable |
| Acquisition raises something unclassified | a bug in the adapter | that meeting refused naming the error type; cursor and coverage already saved | named refusal |
| No Graph connector enrolled | the stand-in | refuses by name | named refusal |
| Fetched but never ingested | state fetched, no ingestion row | handed to `11b`'s ingestion next run | N/A |
| Any run | stored or not | no coverage window; the meeting record byte-identical | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/transcripts/graph.py:10-22` -- the stub over `_fake_api`, to rename; constructed only at `pm_ai/app/wiring.py:52,397`; `tests/slice/test_transcript_slice.py:80` asserts `requires_network` — keep it, against the real adapter class, not the stand-in
- `pm_ai/connectors/transcripts/__init__.py:1-6` -- names `TranscriptSourcePort`, which does not exist
- `pm_ai/ports/__init__.py:561` -- `StoragePort`; `:657` `list_collection` (the presence check). `write_capture` is **not** declared on the port though the writer has it — the under-declaration `8f` fixed for artifacts, asserted at `tests/architecture/test_domain_invariants.py:928-931`
- `pm_ai/storage/service.py:1476-1552` -- `write_capture(body, *, scope, name)`; refusals `:486-510`, `UnprotectedCaptureDir` at `pm_ai/domain/storage_tiers.py:314`; `_capture_name` `:307`, `CAPTURE_NAME_LIMIT = 128` `:94`
- `pm_ai/storage/service.py:102` -- `_SCHEMA`, run on every open; `:146-157` why a new table needs no version bump (`SCHEMA_VERSION` stays 1; the versioning test is cited, not changed); `transcript_state(scope, meeting_id, state, reason, at)` joins it
- `pm_ai/core/meeting_records.py:317-343` -- `record_name`: `<utc day>-<slug>-<sha256>.md`, 108 characters before the suffix, so `-manual.vtt` fits the limit; `capture_name(meeting_id, source)` lives beside it
- `pm_ai/domain/meetings.py:34` -- `calendar_event_ref` (set at `pm_ai/connectors/graph/__init__.py:836`); `:72-78` `transcript_home`; the window is `start` plus `duration_minutes`
- `pm_ai/domain/harvest.py:433-439` -- `PersistResult` (`.persisted`, `.duplicates`, 45 call sites): the transcript report is a new field, never a second return value
- `pm_ai/connectors/graph/client.py:623` `page`, `:753` `walk`; `:840` `_send` fixes `Accept: application/json` and `:419-520` the transport decodes bodies as JSON only (`_decoded`, `:544`), so the download needs a bytes path with a caller-chosen `Accept`; `:665-667` the 403 branch raises `ConsentChanged` without the inner-error code
- `pm_ai/connectors/graph/auth.py:106-126` -- the declared set, both permissions present; `:1476-1486` the partial-grant refusal
- `pm_ai/app/pipelines.py:69-136` -- `run_harvest`; records `:114-115`, `save_cursor` `:135` — acquisition goes after it; no command calls it yet (`9a`)
- `pm_ai/app/wiring.py:813-925` -- `_graph_connector`: the auth, client and clock to build the adapter from; `:130` `Daemon.transcripts`
- `tests/connectors/test_graph_calendar_mapping.py:199-215` -- fake-transport pattern; `tests/architecture/test_domain_invariants.py:886` port conformance; `:374`, `:505` stay skipped until `11b`; `tests/conftest.py:58-60` why skip counts are deltas
- `_bmad-output/implementation-artifacts/deferred-work.md:566` -- the rename entry this slice closes

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/connectors/transcripts/graph_transcripts.py` -- `git mv` from `graph.py`; update `wiring.py:52` -- the rename first
- [ ] `pm_ai/domain/transcripts.py` -- `TranscriptCapture` (meeting id, transcript id, created, ended, text, attributed flag) -- the raw value the port returns
- [ ] `pm_ai/ports/__init__.py` -- `TranscriptSourcePort.acquire(meetings) -> outcomes`, one per meeting: captured, absent (reason), skipped, or refused (reason, `retryable`); `write_capture` on `StoragePort` -- AD-23, AD-49, AD-30
- [ ] `pm_ai/storage/service.py` -- `transcript_state` in `_SCHEMA`; `record_transcript_state(scope, meeting_id, state, reason)` and `transcript_states(scope)` on the writer and the port -- Tier 2 state
- [ ] `pm_ai/connectors/graph/client.py` -- a bytes download with a caller-chosen `Accept`; `ConsentChanged` carries the inner-error code -- the content fetch and the tenant switch
- [ ] `pm_ai/connectors/transcripts/graph_transcripts.py` -- the adapter: event by id (`$select=isOnlineMeeting,onlineMeeting,onlineMeetingProvider,start,end`), online meeting by join link, transcripts walked newest-first with the stop rule, time matching, VTT download, the unattributed retry on `SpeakerAttributionNotAllowed`; stops at the first retryable fault -- the port's primary implementation
- [ ] `pm_ai/core/meeting_records.py` -- `capture_name(meeting_id, source)` from `record_name`'s stem; `has_capture(names, stem)` the prefix check -- one encoding for both names
- [ ] `pm_ai/core/transcript_acquisition.py` -- new, I/O-free: selects records in the reach-back with a calendar reference, no capture (`list_collection`) and no closing state, calls the port, writes each capture through `write_capture` into `transcript_home`, records each state, returns the report and the fetched-not-ingested captures -- the port's caller
- [ ] `pm_ai/app/pipelines.py` -- `run_harvest` runs the acquisition after `save_cursor`, wrapped so an unclassified exception is a named refusal; the report rides on `PersistResult` as a new field -- composition
- [ ] `pm_ai/app/wiring.py` -- build the adapter from `_graph_connector`'s auth and client; `Daemon.transcripts["graph"]` is the real one, or the refusing stand-in with no Graph connector enrolled -- composition root
- [ ] `tests/connectors/test_graph_transcripts.py` -- one test per matrix row on recorded payloads shaped as the spike measured, fake transport, no network; a fixture from re-running the spike is preferred to a hand-written one
- [ ] `tests/core/test_transcript_acquisition.py` -- selection by reach-back and state, write, state rows, report, name derivation and the prefix check
- [ ] `tests/architecture/test_domain_invariants.py` -- `write_capture` joins the asserted `StoragePort` members; the adapter asserted against `TranscriptSourcePort`

**Acceptance Criteria:**
- Given a recorded one-off project meeting with a transcript on the provider, when the harvest runs, then `<repo>/.project-ai/transcripts/<record stem>-graph.vtt` exists with bytes identical to the fixture, the state row says fetched, and the meeting record is byte-identical to before.
- Given a meeting recorded by an earlier run whose acquisition was throttled, when the next harvest returns no records, then that meeting is tried and captured.
- Given a series with two transcripts and three recorded occurrences, when the harvest runs, then the lookup and the listing each happen once, two captures land, and the third occurrence has neither a capture nor a refusal.
- Given a 403 with `GraphAccessToTranscriptsDisabled` on the first of three meetings, then all three are refused by name, the calendar's cursor and coverage are unchanged, and none is tried on the next run.
- Given a capture already present under the manual name, when the harvest runs, then no Graph request is made for that meeting — asserted on the recorded requests.
- Given a project whose `transcripts/` git would commit, then that meeting is refused by name and a personal meeting in the same run is still captured.
- Given `grep -rn "transcripts.graph\b" pm_ai tests`, then nothing matches.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count does not move: the two `core.transcripts` checks stay skipped until `11b`.

## Design Notes

Graph reference pages checked 2026-10-10 (v1.0): [Get onlineMeeting](https://learn.microsoft.com/en-us/graph/api/onlinemeeting-get?view=graph-rest-1.0) — `GET /me/onlineMeetings?$filter=JoinWebUrl eq '{joinWebUrl}'`, a collection; [List transcripts](https://learn.microsoft.com/en-us/graph/api/onlinemeeting-list-transcripts?view=graph-rest-1.0) — `$top` supported, `GraphAccessToTranscriptsDisabled` has "no request-side workaround"; [Get callTranscript](https://learn.microsoft.com/en-us/graph/api/calltranscript-get?view=graph-rest-1.0) — `/content`, `text/vtt` default; only while the meeting has not expired; readable by "users who are part of the meeting calendar invite"; `SpeakerAttributionNotAllowed` is a `403` on `/content` only, and the page says to retry with `Accept: application/vnd.microsoft.graph.transcript+text` (header only, not `$format`): cue timings and words, no voice tags, no `WEBVTT` header; [callTranscript](https://learn.microsoft.com/en-us/graph/api/resources/calltranscript?view=graph-rest-1.0) — `createdDateTime`, `endDateTime` in UTC, no start; [Get event](https://learn.microsoft.com/en-us/graph/api/event-get?view=graph-rest-1.0) — `GET /me/events/{id}` with `$select`.

The content is requested at the documented `/content` path built from the two ids, not by following `transcriptContentUrl`: the client refuses what it cannot check against its origin. The unattributed body is stored under the same `-graph.vtt` name; `11b`'s reader chooses by content and reads a cue-timing first line as cues.

`run_harvest` is edited by three slices, in landing order: this one adds acquisition after `save_cursor`, wrapped; `9a` adds the `try/except` around the connector call, cursor ownership and the retry ladder; `11b` adds ingestion of every fetched-not-ingested capture after acquisition.

Two admitted limits. A meeting that overran its slot by more than 15 minutes is refused by name rather than guessed; `11c`'s drop is the repair. A newly recorded meeting after a tenant-wide `403` still costs three requests; per-meeting honesty was chosen over an early stop.

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_transcripts.py tests/core/test_transcript_acquisition.py -q` -- expected: all matrix rows pass, no network
- `uv run pytest -q` -- expected: all pass; the skip count unchanged by this slice
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
