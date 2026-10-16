---
title: 'Every part of a meeting''s transcript is kept'
type: 'feature'
created: '2026-10-16'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** When a meeting's transcription is stopped and started again, Teams lists one transcript per part, and `33e` refuses any occurrence two transcripts fit — "a recording restarted — so none is stored" — for good. On the field test a recurring meeting held two parts, 08:33:21–08:39:41 and 08:46:21–09:02:20 UTC, 6 min 40 s apart, and pm-ai kept neither. Andrei decided on 2026-10-15 that every part is kept.

**Approach:** Every transcript that fits an occurrence and overlaps no other is a part of that meeting: stored as its own capture, byte-identical to its download, numbered in time order, and ingested part by part, in that order, into the one meeting. Overlapping transcripts stay refused, now named as duplicates; a part listed after earlier parts were stored is added on a later harvest.

## Boundaries & Constraints

**Always:**
- **What a part is.** `33e`'s matching is unchanged: a transcript fits the occurrence whose window — start to end, widened by `PLACEMENT_SLACK` (15 min) either side — holds its created instant, provided it ended after the occurrence started. Of the transcripts fitting one occurrence, two **overlap** when each was created before the other ended (one ending exactly as the next is created does not).
- **Decisions in this order, before any write:**
  1. **Not finished:** a fitting transcript with no end instant, or any transcript in the online meeting's listing with no created instant (it may be an unfinished part), skips every meeting of that online meeting not yet holding all its parts — this run, nothing stored.
  2. **Fits two occurrences** sharing the join link: refused for both meetings, as `33e`.
  3. **Overlap** among the fitting transcripts, stored parts included: refused, naming both as duplicates covering the same stretch of the meeting.
  4. **Earlier than a stored part:** a new transcript created before the newest stored part is refused naming it — numbering is time order.
  5. Otherwise the new parts are stored, in time order.
- **Who a refusal reaches.** A meeting with no stored part gets the refusal as its row, non-retryable, as today. A meeting already holding parts keeps its row; the refusal covers the new transcript(s) only and is listed in the run's refusals. The same holds for an absent or skipped answer: once a meeting holds a part, only a new stored part changes its row.
- **One naming rule, extended.** Part 1 keeps `<record stem>-graph.vtt`, so every capture already on disk is a part 1 and nothing is renamed; part *n* ≥ 2 is `<record stem>-graph-<n>.vtt`. The stem is at most 108 characters and the capture-name limit 128, so a part number up to nine digits fits. The "has a capture" prefix rule is unchanged; nothing is overwritten or appended to.
- **Parts in Tier 2.** A new `transcript_parts` table, one row per part: scope, meeting id, part number, the provider's transcript id, created, ended, `pending` or `stored`, whether it was ingested, when stored. A `transcript_part_asks` table holds when each meeting was last asked. Both created on open; the store's version number does not change. The meeting record gains nothing.
- **Each part is one claimed step, safe to kill:** its row is written `pending` (transcript id and instants), then its capture, then the row marked `stored`. On the next selection a `pending` row whose capture is on disk is marked `stored`; a `pending` row with no capture is fetched again under the same number. A meeting with a `-graph.vtt` and no part rows at all is a capture `33e` wrote: part 1 of a closed meeting, never asked again.
- **A refused write.** A retryable refusal (a full disk, an unprotected directory, an empty body) leaves the part `pending` with its reason, and the meeting is asked again while any part is pending. A non-retryable one removes the pending row and the meeting's row names the part as lost. The row is `fetched` once any part is stored.
- **Asked again, and when it stops.** A meeting holding Graph parts and no hand-dropped capture is asked again while it has a pending part, or until a run that asked about it started after its placement window ended (start to end plus 15 minutes) — read from `transcript_part_asks`. After that, nothing more can fit: a part fits only if created before the window ended, and one in progress skips the meeting.
- **A stored part missing from a later listing** is not an error: numbering continues from the highest stored part, and the overlap test reads the stored parts' instants from the table.
- **The port learns what is stored.** `TranscriptSourcePort.acquire` receives each meeting's stored parts (transcript id, created, ended); the Graph adapter downloads only new ones and returns them, in time order, in one captured outcome. Every implementer changes: the Graph adapter, `UnenrolledGraphTranscripts`, the manual adapter, and the test fakes.
- **Ingestion is per part, once, in part order, into the one meeting.** `11b`'s ingestion reads the parts from `transcript_parts` (falling back to `-graph.vtt` alone when a meeting has no rows) and ingests each part not yet ingested, bound to the meeting, with that part's own attribution. Earlier parts are never re-extracted. Proposal ids: part 1 keeps `prp_<meeting>_<source>_<index>`; part *n* ≥ 2 is `prp_<meeting>_<source>_p<n>_<index>`. A tracing offset (AD-33) is the offset within its part, with the part number beside it. The meeting's row is `ingested` when every stored part is.
- **Storing a new part resets the meeting's ingestion attempt count** (a row rewritten to `fetched` does not reset it today), so a meeting whose earlier ingestion failed three times still ingests its new part.
- **Recurring meetings:** a series is resolved and listed once per run; parts are matched per occurrence by time.

**Ask First:** Nothing.

**Never:** No concatenation on disk; no edit of a stored capture. No guess between overlapping transcripts. No change to the placement window, the listing's stop rule, the tenant-switch or attribution handling. No new permission.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Restarted transcription | two fitting parts 08:33–08:39 and 08:46–09:02 | `<stem>-graph.vtt` and `<stem>-graph-2.vtt`, each byte-identical; two `stored` rows; row `fetched` naming both; ingested part 1 then part 2 | N/A |
| One part | one fitting transcript | `<stem>-graph.vtt`, as `33e` | N/A |
| Overlapping, nothing stored | parts 08:30–08:50 and 08:40–09:00 | nothing stored; row refused naming both as duplicates | non-retryable |
| New overlaps a stored part | part 1 stored; a new transcript overlapping it | not stored; named in the run's refusals; row kept | N/A |
| Unfinished part | part 2 with no end instant | skipped; nothing stored, part 1 included | tried next run |
| Unplaced transcript | the listing holds one with no created instant | every not-yet-complete meeting of that online meeting skipped | tried next run |
| Overlap and unfinished | two overlapping, one unfinished | skipped (rule 1 first) | tried next run |
| Later part | part 1 stored at 09:05, window ends 09:15; part 2 listed next run | stored as `-graph-2.vtt`; row `fetched`; part 2 ingested, part 1 untouched | N/A |
| Asked after the window | a run asks at 09:20 and finds nothing new | not asked again | N/A |
| Late part earlier in time | new transcript created before the stored part | not stored; named; row kept | N/A |
| Part fits two occurrences | created inside two occurrences' windows | refused for both meetings; nothing stored | non-retryable |
| Stored part no longer listed | part 1's id absent from the listing, part 2 new | stored as part 2 | N/A |
| Crash after capture | `pending` row, capture on disk | marked `stored` at next selection | N/A |
| Crash before capture | `pending` row, no capture | fetched again under the same number | N/A |
| Retryable write refusal on part 2 | disk full | part 1 kept; part 2 `pending`; meeting asked again | tried next run |
| New part after exhausted ingestion | three failed ingestions, then part 2 stored | attempts reset; part 2 ingested | N/A |
| Mixed attribution | part 1 with speaker names, part 2 without | each part ingested by its own attribution | N/A |
| Captured before this slice | `-graph.vtt`, no part rows | part 1 of a closed meeting; no request | N/A |
| Hand-dropped capture | `-manual.vtt` present | not asked, as `33e` | N/A |
| Series, one occurrence restarted | three occurrences, one with two parts | listed once; two captures for that one, one each for the others | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/transcripts/graph_transcripts.py:14-19` matching account; `:103` `PLACEMENT_SLACK`; `:211` `GraphTranscriptAdapter`, `:250` `UnenrolledGraphTranscripts.acquire`; `:325-389` `_match`, the refusal at `:351-357`; `:508` `_download`; `:635` `_fits` (a transcript with no created instant never fits today)
- `pm_ai/domain/transcripts.py:121` `CaptureFormat`; `:136` `TranscriptCapture`; `:170` `TranscriptCaptured` (one `capture`); `:278-292` the attempt-cap rule ("a row rewritten to `fetched` cannot reset the cap"); `:293` `closes`; `:316` `TranscriptReportEntry`
- `pm_ai/ports/__init__.py:670` `TranscriptSourcePort.acquire`; `pm_ai/connectors/transcripts/manual.py:28`
- `pm_ai/core/meeting_records.py:405` `capture_stem`, `:417` `capture_name`, `:432` `has_capture`
- `pm_ai/core/transcript_acquisition.py:130-175` `_select_in` (prefix check and part-1-only repair at `:150-169`); `:178` `acquire_transcripts`; `:276` `_stored`; `:343` `_awaiting_ingestion` (keys on the `-graph.vtt` tail)
- `pm_ai/app/pipelines.py:606` `ingest_captures`; `:672` `_ingest_capture`; `:981` proposal ids; `:1190` the drop refused when any capture exists (`11c`)
- `pm_ai/app/wiring.py:3506` `HANDED_CALLS`
- `pm_ai/storage/service.py:117` `CAPTURE_NAME_LIMIT`; `:179` `transcript_state`, the model for the new tables
- `tests/connectors/test_graph_transcripts.py:456` the two-parts refusal, which flips; `tests/connectors/test_graph_transcripts_recorded.py` (PR #72) `test_the_restarted_occurrence_is_refused_naming_both_and_nothing_is_downloaded`, which flips

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/transcripts.py` -- `TranscriptCaptured` carries the new parts in time order; a stored-part value; the report names every part -- the outcome
- [ ] `pm_ai/ports/__init__.py`, `graph_transcripts.py` (both adapters), `manual.py`, test fakes -- `acquire` takes the stored parts -- the port
- [ ] `pm_ai/connectors/transcripts/graph_transcripts.py` -- the five decisions in order; only new transcripts downloaded -- the adapter
- [ ] `pm_ai/core/meeting_records.py` -- `capture_name(..., part=1)`, part 1 unnumbered -- one naming rule
- [ ] `pm_ai/storage/service.py` -- `transcript_parts` and `transcript_part_asks` in `_SCHEMA`; part writes and reads on the writer and the port; the attempt reset -- Tier 2
- [ ] `pm_ai/app/wiring.py` -- the new writes in `HANDED_CALLS` with `_asks_none`; the capture stays `write_capture`
- [ ] `pm_ai/core/transcript_acquisition.py` -- asked-again rule; pending repair; one claimed step per part; row rules -- the caller
- [ ] `pm_ai/app/pipelines.py` -- per-part ingestion and proposal ids -- ingestion
- [ ] `tests/connectors/test_graph_transcripts.py`, `tests/core/test_transcript_acquisition.py`, `tests/connectors/test_graph_transcripts_recorded.py`, `tests/slice/test_transcript_path.py` -- one test per matrix row; the two flipped tests
- [ ] `_bmad-output/specs/spec-pm-ai/stories/33e-graph-transcripts.md` -- dated Change Log line: `33e:28` (matching), `33e:29` ("One naming rule, owned here" — extended with part numbers), `33e:57` ("two fit one") amended by `33g`
- [ ] `_bmad-output/specs/spec-pm-ai/stories/11b-the-real-transcript-path.md` -- dated Change Log line: `11b:28` ("never ingested twice" — now per part) and the row at `11b:64` amended by `33g`
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- a part Graph lists only after a run past the window is not read; `11c`'s drop refuses a meeting with any capture, so the repair is to remove its captures by hand and drop the whole transcript again

**Acceptance Criteria:**
- Given the recorded listing and an occurrence on 2026-10-15 at 08:30 UTC for 30 minutes, when acquisition runs, then two captures land, each byte-identical to its download, with part rows 1 and 2 in creation order.
- Given those two parts, when ingestion runs, then every extraction binds to the one meeting, part 1's proposals carry `33e`'s id form and part 2's carry `p2`.
- Given a meeting ingested with one part, when a second part is stored and ingestion runs, then part 1's proposals are unchanged, one the PM approved stays approved, and part 1 is not extracted again.
- Given the change, when `uv run pytest -q` runs, then all pass.

## Spec Change Log

- **2026-10-16, created.** Carries Andrei's decision of 2026-10-15: non-overlapping transcripts of one occurrence are parts, each stored verbatim and numbered in time order (part 1 keeps `-graph.vtt`, later parts `-graph-<n>.vtt`); overlapping ones refused as duplicates; a later part added; part rows in Tier 2, nothing on the record. Supersedes `33e`'s "a recording restarted — so none is stored".
- **2026-10-16, review:** decisions ordered (unfinished, including an unplaced transcript, before two-occurrence, overlap and earlier-than-stored); who a refusal reaches stated; the asked-again rule bounded by a recorded ask after the window; each part one claimed step with a `pending` row and repair; retryable write refusals keep the part pending; a stored part missing from the listing handled; ingestion made per part (earlier parts never re-extracted, part-scoped proposal ids, per-part attribution, offsets carry the part number); the attempt reset; every port implementer and `HANDED_CALLS` named; the `11c` drop repair replaced by a recorded limit; `11b` and `33e:29` added to the amendments. Depends on no spine amendment.
- **2026-10-16, review (consistency):** the per-part rationale corrected — `7b` keeps pattern extraction unchanged; per-part ingestion is kept for independence from extraction's determinism and to avoid re-ingesting earlier parts.

## Design Notes

**Per-part ingestion, not whole-meeting re-ingestion.** Re-ingesting the whole meeting when a part arrives would re-extract part 1; today's extraction is a per-utterance pattern loop and stable, and `7b` keeps it unchanged (its model writes a summary beside it), so re-extraction would reproduce part 1's ids. Per-part ingestion is still chosen: it keeps every earlier proposal fixed without depending on extraction staying deterministic in any later slice, and it does not re-run part 1's ingestion work each time a part arrives. The meeting is still seen whole: every extraction binds to the one meeting, in part order, and a reader that needs the full text (`7b`'s summary) reads the parts in order from `transcript_parts`.

**Mixed attribution** falls out of the same choice: each part's extractions follow that part's attribution, so an unattributed part stages its own extractions under `11b`'s rule and leaves an attributed part's alone.

**Unplaced transcript.** Skipping a whole online meeting while any listed transcript has no created instant is conservative: without the instant pm-ai cannot tell whether it is this occurrence's unfinished part. A transcript stuck that way blocks the series' unfinished meetings until they leave the reach-back — an admitted limit, named in the run's report each time.

Part 1 keeps the unnumbered name so the field test's stored capture, `_awaiting_ingestion`'s tail and `33e`'s repair keep working with no migration. The recorded fixture's slot is assumed (08:30 UTC, 30 minutes), as PR #72 states; the field test's parts are measured.

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_transcripts.py tests/connectors/test_graph_transcripts_recorded.py tests/core/test_transcript_acquisition.py -q` -- expected: every matrix row passes, no network
- `uv run pytest -q` -- expected: all pass
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
