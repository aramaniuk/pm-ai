---
title: 'A real transcript reaches the extraction pipeline'
type: 'feature'
created: '2026-10-10'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** pm-ai can turn a meeting transcript into staged proposals, but nothing real reaches that step: it runs only from tests, which hand it a transcript and a meeting already joined. A Graph transcript stored by `33e` is never read, the step assumes the meeting belongs to whichever project pm-ai is acting in — so a personal meeting's transcript, harvested while acting in a project, would be refused for the wrong reason — and what it extracts travels in a free-form dictionary built from raw spoken text, the one hole `8e`'s sanitization boundary could not close. A meeting the PM recorded by hand before the calendar harvest saw it would be recorded twice.

**Approach:** One ingestion path that binds a transcript to a meeting by the one-record-per-meeting rule, reads Graph's WebVTT and plain speaker-labelled text, writes everything into the meeting's own scope, and carries cleaned text in values of a fixed shape. The capture `33e` stores is its first real caller, ingested by the harvest that stored it; what has been ingested is recorded in the operational store so nothing is ingested twice or forgotten. The hand-drop command is `11c`, built on this.

## Boundaries & Constraints

**Always:**
- **Binding follows the one-record-per-meeting rule (D3, AD-33).** A named meeting id wins and must exist in the given scope. Otherwise the candidates are every record in that scope whose start is within 15 minutes either side of the given start, inclusive, read across day files (a window straddling midnight reads both days), and whose title equals the given one after Unicode normalisation, case-folding, trimming and collapsing runs of whitespace. One candidate binds; two refuse, naming both; none mints a record marked manual-only with a pm-ai id — `mtg_` plus random hex, the shape event ids use — from the given title, start, attendees and duration. A transcript with no meeting is never ingested (AD-23). This 15 minutes is a start-to-start tolerance; `33e`'s widens a transcript's placement window — two figures, not one rule.
- **The same lookup runs on every calendar-sourced record write (gates F2 and F3, AD-33, 2026-10-10).** A connector's meeting id is a calendar reference, never a record id. The record accessor looks up by calendar reference, then by title and start, and returns the kept id; `app` fills the held-meeting event's meeting id and source reference from it before the harvest's events are persisted. On a match with a manual-only record the calendar adopts it: the calendar's fields replace the manual ones, only the id and the `## Notes` region survive, and the accessor re-files the record under the calendar start's day — the one legitimate move between day files, not reported as a displaced meeting. A calendar arrival with two manual-only candidates adopts neither, mints, and names both in the harvest's refusals without refusing the row.
- **Two readers, chosen by content, not by extension.** A file whose first non-blank line is `WEBVTT` — or a cue timing line, since Microsoft's unattributed format has no header — is read as WebVTT cues: a byte-order mark and Windows line endings are accepted; NOTE, STYLE and REGION blocks, cue identifiers and settings are skipped; offsets come from the cue times; a cue with several `<v Speaker>` spans becomes one utterance per span; a cue without a voice tag has an empty speaker. Anything else is tried as today's `Speaker: words` text. A file neither accepts is refused by name — the only reader answer `11c` refuses on. The readers are this slice's own internal contract; the acquisition port is `33e`'s, and its two adapters (Graph, and the dropped file) yield captures, not utterances.
- **No Graph capture executes anything in this slice.** A hand-supplied transcript is `TranscriptSource.MANUAL` and never auto-executes (AD-32). A capture `33e` stored is `TranscriptSource.GRAPH`, but its speaker labels are display names the speakers chose, not accounts, so the PM-handle equality test is not applied to a Graph source: every Graph extraction stages until a later slice resolves names through the alias table (AD-34). The execute branch stays and is tested directly.
- **Everything the ingestion writes lands in the meeting's scope — the record, the capture's home, the proposals' citation — never the scope pm-ai is acting in.** The citation check at the top of ingestion compared the meeting's scope with itself and goes; what still guards AD-38 is the storage-side check on every write that cites another scope, exercised directly by its own tests. A `people` meeting handed to ingestion is legal; only `11c`'s drop declines `people`.
- **The Graph hand-off and the ingestion record.** `33e` stores a capture under the one naming rule it owns (called here, never redefined) and records *fetched* in the operational store's transcript table. This slice adds *ingested* and *ingest failed*; a failed ingestion is retried on up to three later runs, then named by `doctor`. The harvest ingests every fetched capture with no ingested row after acquisition, so `33e`'s acquisition and `9a`'s cycle both feed this path and a daemon killed between capture and ingestion is repaired next run. A capture with an ingested row is never ingested twice. One capture's failure is recorded by name and neither undoes its capture nor stops the others. The capture stays on disk; the 30-day purge is NFR-09's.
- **Ingestion never dies after the record is written.** A spoken target naming a provider with no enrolled skill stages instead of executing; a skill raising mid-loop settles that extraction's once-only key as failed, stages the extraction, and the loop continues.
- **Every extraction carries cleaned text in a value of fixed shape, not a free-form dictionary.** A spoken command (verb, target, remainder) and a promise (what, when) become values whose text fields keep the cleaned text and the original together, so the path cannot drop one before a port (`8e`'s named limit). A spoken target the reference grammar refuses stages the command as text. A promise has no work-item target and no once-only key — it only stages — so its proposal's target is empty and loads back as none. The verb's provider is read from the spoken target, not defaulted. The comment posted outbound by an executed command is the spoken words — a quotation, not a prompt — chosen visibly at the call.
- **The model obligation `8e` handed this slice is a test, not a model.** Extraction is pattern matching and no model is in the wave-2 path (prototype path, decision 2); the confirmation is a test that reads the transcript path's source and refuses any model-client import, and that `ModelPort.complete` is the only callable in pm-ai declared to accept cleaned text.
- **Every proposal staged cites the meeting (AD-33), and the record's `## Summary` region stays empty** — it needs a model, and the reason is already written where `11a` reserved it.

**Ask First:** Nothing. The Graph speaker question was settled on 2026-10-10 and is the fourth Always clause.

**Never:** No command — `11c`. No Graph fetching or capture write — `33e`. No `.docx`, no watched folder (story 10a), no summary, no amendments (queued 2026-09-03), no `[UNMATCHED_ANCHOR]`, no research dispatch, no model, adapter or router. No manual re-ingest command (deferred). No event-log entry for the ingestion itself: there is no declared action type for it, and inventing one is `2c`'s guarded change.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Named meeting | id recorded in the given scope | bound to it; proposals cite it | N/A |
| Named meeting absent | id not in that scope | refused, naming id and scope; nothing written | `MeetingNotFound` |
| Title and start match one record | "Weekly Sync" at 09:07; record "weekly  sync" at 09:00; or a composed vs decomposed "Café"; or exactly 15 min off | bound to it; no new record | N/A |
| Match straddles midnight UTC | start 00:05, record 23:55 the day before | matched — both day files read | N/A |
| Two candidates | two records within 15 minutes | refused, naming both ids | refusal naming both |
| No match | title, start, attendees, duration given | manual-only `mtg_` record minted in the given scope | N/A |
| No meeting at all | nothing to bind to | refused before anything is written | `UnboundTranscript` |
| Calendar arrives for a manual-only record | same title, start 5 min off, across midnight or not | adopted: calendar fields replace manual, id and `## Notes` kept; re-filed under the calendar day without a displaced-meeting error; the event carries the kept id | N/A |
| Calendar arrives, two manual-only candidates | both within 15 min | neither adopted; new record minted; both named in the harvest's refusals; row kept | N/A |
| Personal meeting while acting in a project | meeting scope personal, daemon in alpha | record and proposals' citation personal; ingested, not refused | N/A |
| `people` meeting handed to ingestion | a 1:1 record | ingested into the `people` tree | N/A |
| A write citing another scope | a committed write cites a personal meeting | refused by the storage-side guard | `CommittedScopeLeak` |
| Teams-style VTT | BOM, CRLF, NOTE and STYLE blocks, cue ids and settings | read; blocks and settings skipped; cue text kept | N/A |
| Cue with two voice spans | `<v A>hi</v><v B>hello</v>` | two utterances, same offset | N/A |
| Cue without a voice tag, or the unattributed format | `<v>` absent; or a cue-timing first line, no `WEBVTT` | utterances with an empty speaker; they can only stage | N/A |
| Not VTT, not text | no header, no timing, no `Speaker:` lines | refused, naming the file | refusal |
| PM speaks a reversible command | `MANUAL` or `GRAPH`, label equals the handle | staged, never executed | N/A |
| Execute branch, no enrolled skill | extraction marked execute, provider not enrolled | staged; nothing invoked | N/A |
| Execute branch, skill raises | extraction marked execute, skill raises | key settled failed; extraction staged; the next extraction processed | named in the result |
| Unparseable spoken target | `pm-ai, post_comment the-thing` | staged as text; no target; never executed | N/A |
| Promise | `I'll send the deck by Friday` | proposal with an empty target, citing the meeting; loads back with no target | N/A |
| Harvest stored two captures | two fetched rows, no ingested rows | both ingested after acquisition; rows become ingested | one failing is recorded failed, the other still ingests |
| Capture already ingested, or failed three times | ingested row; third failed row | not ingested again; the thrice-failed one named by `doctor` | N/A |
| Record write fails after extraction | `meetings/` refuses | nothing staged or executed (`11a`'s order kept) | propagated |
| Transcript path imports | the pipeline, extraction and the readers | no `anthropic`, `ollama`, `httpx`; cleaned text accepted only by `ModelPort.complete` | test fails otherwise |

</frozen-after-approval>

## Code Map

- `pm_ai/app/pipelines.py:183-247` -- `run_transcript_ingestion`: untyped parameters, `provider="gitlab"` default; `:193` the tautological `assert_citation_legal(cited=meeting.scope, into=daemon.scope)` to remove; `:225-232` the execute branch reading `detail[...]`; `:240` the invented `gitlab:alpha:issue:0` target
- `pm_ai/app/pipelines.py:69-136` -- `run_harvest`: records `:114-115`, events `:122`, `save_cursor` `:135`
- `pm_ai/core/extraction.py:22-30` -- `Extraction` with `raw`, `for_model`, `detail: dict`; `:49` the sanitize call; `:74`, `:87` the two dicts built from raw text; `:66` the PM-handle test, to gate on the source
- `pm_ai/connectors/transcripts/manual.py:19-33` -- `load(raw, meeting_id)` parsing `Speaker: text` lines; the readers move behind one internal contract
- `pm_ai/domain/transcripts.py:13-19,26-47` -- `TranscriptSource`, `Utterance`, `Transcript`, `UnboundTranscript`
- `pm_ai/domain/meetings.py:17-33,68-78` -- `Meeting`, `source_ref`, `transcript_home`; the manual-only mark is a new field
- `pm_ai/core/meeting_records.py:453-599` -- `put` `:466` gains the lookup and returns the kept id, `get` `:523`, `for_day` `:549`; `:237` `MeetingDisplaced`, not raised for the adoption move; `:167-183` `_FIELDS` gains the manual-only field; `capture_name(meeting_id, source)` is `33e`'s, beside `record_name` `:317`
- `pm_ai/domain/disclosure.py:158-177` -- `assert_citation_legal`; unchanged, still called by storage on every write that cites another scope
- `pm_ai/storage/service.py:304` -- the `evt_` + `token_hex(10)` id shape; `:373` serialises `str(p.target)` and `:391` `TargetRef.parse(d["target"])` — both must admit an absent target; `:1894` `stage_proposal` writes over by key; `:102` `_SCHEMA`, where `33e`'s `transcript_state` gains `ingested` and `ingest_failed` (no version bump, `:146-157`)
- `pm_ai/domain/proposals.py:21-31` -- `Proposal.target: TargetRef` becomes `TargetRef | None`
- `pm_ai/core/jobs.py:23` -- `idempotency_key(job_type, target_ref, payload: dict)`; takes the typed value's rendering; none for a promise
- `pm_ai/app/wiring.py:130,397` -- `Daemon.transcripts`, the acquisition adapters; `:52` the module `33e` renamed
- `pm_ai/ports/__init__.py:721,788` -- `ModelPort.complete`, the only `Sanitized` consumer; `:743` its sentence naming this slice
- `pm_ai/surfaces/cli/dispatch.py:609` -- `_doctor`, which names thrice-failed captures
- `tests/architecture/test_static_rules.py:400-412` -- the forbidden-import list to reuse
- `tests/architecture/test_domain_invariants.py:374,505` -- the two `pm_ai.core.transcripts` checks (`registered_adapters`, `get_adapter`, `ingest(payload, meeting)`) this slice un-skips; `:886` the port conformance both adapters join; `tests/conftest.py:58-60` why the count is a delta
- `tests/slice/test_r4_gate_fixes.py:327,348` -- the two AD-38 tests, rewritten to exercise the storage-side guard directly; `:345`, `:359` the ingestion calls, rewritten to assert ingestion into the personal and the `people` tree. `tests/slice/test_transcript_slice.py`, `tests/slice/test_meeting_persistence.py:129` -- callers of the current signature

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/extraction.py` -- typed values carrying `Sanitized` in place of `detail: dict`; parse the spoken target into `TargetRef` or stage as text; provider from the target; the PM-handle test skipped for `GRAPH` -- `8e`'s named limit and the Graph rule
- [ ] `pm_ai/domain/proposals.py` -- `target: TargetRef | None`; `pm_ai/storage/service.py:373,391` serialise and load the absent target; `pm_ai/core/jobs.py` keys on the typed value's rendering -- no invented target
- [ ] `pm_ai/core/transcripts.py` -- new: `registered_adapters()`, `get_adapter(name)` over a registry `app` fills at wiring (`core` imports no adapter), `ingest(payload, meeting)` raising `UnboundTranscript` on `None`; the reader contract with the VTT reader (BOM, CRLF, skipped blocks, multi-span cues, header-or-timing detection) and the text reader kept from `manual.py` -- the two file shapes; the skip count falls by two
- [ ] `pm_ai/domain/meetings.py` / `pm_ai/core/meeting_records.py` -- manual-only mark; `find_candidates(scope, title, start)` over the window's day(s), titles compared after NFKC and `casefold()`; `put` looks up by calendar reference then title and start, adopts, re-files, returns the kept id -- D3, gates F2/F3
- [ ] `pm_ai/storage/service.py` -- `ingested` and `ingest_failed` rows in `transcript_state` with an attempt count; `fetched_not_ingested(scope)` -- the ingestion record
- [ ] `pm_ai/app/pipelines.py` -- a bind step (named id, match, mint); `run_transcript_ingestion` typed, the tautological guard removed, the loop surviving a missing or raising skill; `run_harvest` fills the held-meeting event's id from `put`'s return and ingests every fetched-not-ingested capture after acquisition -- one path
- [ ] `pm_ai/surfaces/cli/dispatch.py:609` -- `_doctor` names captures whose ingestion failed three times -- the loud end of the retry
- [ ] `tests/architecture/test_transcript_path.py` -- the import walk and the `Sanitized`-acceptor check -- the `8e` obligation
- [ ] `tests/slice/test_transcript_path.py`, `tests/slice/test_r4_gate_fixes.py:327-359` -- one test per matrix row against a temporary root, the adoption rows including the midnight-straddling one, the Promise row ending with a load-back asserting `target is None`; the AD-38 tests rewritten as the Code Map says -- the matrix is the contract

**Acceptance Criteria:**
- Given a project meeting recorded in alpha and a VTT whose voice tag equals the PM's handle saying `pm-ai, post_comment gitlab:alpha:issue:102 ship it`, when it is ingested as `MANUAL` and again as `GRAPH`, then each time one proposal is staged citing `meeting:<id>` and nothing is executed.
- Given pm-ai acting in project alpha, when a personal meeting's transcript is ingested, then the record and the proposals' citation are personal and nothing is refused.
- Given a manual-only record "Weekly Sync" at 23:55 UTC, when the calendar harvest returns the same meeting at 00:05 the next day, then one record exists, under the next day's file, with the calendar's fields, the old id and the `## Notes` region, and the held-meeting event cites that id.
- Given two fetched captures with no ingested rows, when the harvest runs, then both are ingested after acquisition, a reader that raises for one leaves the other's proposals staged and its own row failed, and a second run ingests neither again.
- Given `uv run pytest`, then everything passes and the skip count falls by two: the two `core.transcripts` checks run.

## Design Notes

`run_harvest` is edited by three slices, in landing order: `33e` adds transcript acquisition after `save_cursor`, wrapped; `9a` adds the `try/except` around the connector call, cursor ownership and the retry ladder; this slice adds ingestion of every fetched-not-ingested capture after acquisition, and the kept-id fill on the held-meeting event before events are persisted.

## Verification

**Commands:**
- `uv run pytest tests/architecture/test_transcript_path.py tests/slice/test_transcript_path.py tests/slice/test_r4_gate_fixes.py -q` -- expected: every matrix row passes
- `uv run pytest -q` -- expected: all pass; two fewer skips than before this slice
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
