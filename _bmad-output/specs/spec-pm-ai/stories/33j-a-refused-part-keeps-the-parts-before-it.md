---
title: 'A refused transcript part keeps the parts before it'
type: 'bugfix'
created: '2026-10-20'
status: 'done'
baseline_commit: '82c2edb92303fed10a2777b4160bf658b7632dc9'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Since `33g`, when the download of one new part of a meeting's transcript is refused in a way waiting cannot clear — Graph answers 404, the body is not UTF-8 text, the tenant refuses speaker attribution in both formats — the Graph adapter returns that refusal for the whole meeting and throws away the parts it already downloaded in the same run. For a meeting with nothing stored yet, the refusal becomes the meeting's row, which closes it for good, so even part 1 is never kept. Andrei decided on 2026-10-19 that the parts before the failure are kept.

**Approach:** The adapter downloads the new parts in time order, as today, and stops at the first one refused for good. It returns the parts downloaded before it, in time order, so their numbers stay correct, together with that refusal. Acquisition stores them as `33g` stores any new part and names the failed part in the meeting's row and in the run's refusals. When the very first new part is the one refused, nothing changes from today.

## Boundaries & Constraints

**Always:**
- **Which failures keep the earlier parts.** A part's download answered by a refusal waiting cannot clear: 404, a body that is not UTF-8, attribution refused in both formats, a permission refused (403), any other Graph rejection, a response pm-ai cannot read, or Graph saying the meeting has expired. Each of these comes back from the adapter's download step today as an outcome about the meeting, and none is cleared by asking again.
- **Which failures do not.** A refusal waiting may clear (an empty body, "Teams may still be writing it"), a provider fault that stops the run (a throttle, a server error, no answer, a credential that no longer signs in) and an error the adapter cannot classify keep today's all-or-nothing rule: nothing from this meeting is returned this run, and it is asked again as today. Nothing is lost by that: the next run downloads every part again.
- **What the adapter returns.** When at least one new part was downloaded before the refused one, the answer is the meeting's captured outcome holding those parts, in time order, plus one sentence naming the refused part's transcript id and why, and how many later new parts were not asked for (named by transcript id). Parts after the refused one are not downloaded: they would have to be numbered after a part that is not stored. When the refused part is the first new one, the answer is the refusal alone, exactly as today.
- **The captured outcome carries the refusal.** `TranscriptCaptured` gains one optional field holding that sentence; it is empty in every other case and the manual adapter and `UnenrolledGraphTranscripts` never set it. The port's signature does not change.
- **How acquisition stores them.** Every returned part goes through `33g`'s per-part steps unchanged: a `pending` row, its capture, then the row marked `stored`, with the same refusal handling for a failed write. The meeting's row is `fetched` once any part is stored, and its reason ends with "Not stored:" and the refused part's sentence, the same form `33g` uses for a part whose write was lost. The sentence is also in the run's refusals. If a write fails first, both the write failure and the download refusal are named.
- **The refused part keeps no row.** No `transcript_parts` row is written for it, because it was never downloaded; this matches `33g`'s part whose write was refused for good, whose pending row is removed. Its number is not reserved: the next part stored takes the number after the highest one already recorded.
- **Asking again.** A captured outcome counts as the meeting having been asked, as today, so the ask is recorded and `33g`'s rule decides whether it is asked again: while it has a `pending` part, or until a run that asked about it started after its placement window ended. While it is still asked, each run lists the refused part again as new and tries to download it first; if it is refused again, that run stores nothing new, the meeting keeps its row, and the run's refusals name it, as `33g` does for any refusal about a meeting already holding parts. If it downloads, it is stored as the next part and the parts after it follow. Once the meeting is no longer asked, the refused part and every part after it are not read.
- **Ingestion and the attempt cap are unchanged.** Storing a part resets the meeting's ingestion attempts, as `33g` does. Ingestion reads only `stored` rows, so the kept parts are ingested in part order into the one meeting.
- **Summaries are unchanged.** `7b` offers the meeting's summary once its parts are ingested and summarises the stored parts in order; when a part arrives later (the refused one succeeding on a later run), `7b`'s existing re-open of an automatic summary applies, and `7e`'s member lines follow the summary job as they do today. The summary says nothing about a refused part (see Ask First).

**Ask First:** Nothing. Both questions were answered by Andrei on 2026-10-20, each as written: the refused part keeps no row, and the parts after it are stored only if it later downloads while the meeting is still asked; a summary does not say a part is missing.

**Never:** No change to `33g`'s five decisions before the download, to the placement window, the asked-again rule, the naming rule, or `7b`'s re-open. No part stored out of time order. No retry of a refused part within one run. No new permission.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Part 2 answers 404, nothing stored | new parts T-1, T-2; T-2's content 404 | T-1 stored as `<stem>-graph.vtt`; row `fetched`, reason ends "Not stored:" naming T-2 and 404; run's refusals name T-2 | not retried this run |
| Part 1 answers 404, nothing stored | new parts T-1, T-2; T-1's content 404 | nothing downloaded after T-1; row refused naming T-1, as today | non-retryable, as today |
| Part 3 of three refused | T-1 and T-2 downloaded, T-3 not UTF-8 | T-1 and T-2 stored as parts 1 and 2; T-3 named | not retried this run |
| Part 2 of three refused | T-2 404, T-3 listed after it | T-1 stored; T-2 named; T-3 named as not asked for and not downloaded | not retried this run |
| Attribution refused on part 2 | T-2 refused in both formats | T-1 stored; T-2 named with the attribution code | not retried this run |
| Expired on part 2 | T-2's download says the meeting expired | T-1 stored; T-2 named | not retried this run |
| Empty body on part 2 | T-2's content blank | nothing returned; meeting refused retryably, as today | tried next run, as today |
| Throttle on part 2 | T-2 answers 429 | run stops at this meeting; nothing returned, as today | tried next run, as today |
| Meeting already holding part 1 | part 1 stored; new T-2 stored, T-3 404 | T-2 stored as part 2; row `fetched` naming both parts and T-3 | not retried this run |
| Refused part tried again, still refused | part 1 stored by 33j; next run, inside the window, T-2 404 again | nothing new stored; row kept; run's refusals name T-2 | asked until a run after the window |
| Refused part succeeds later | next run, inside the window, T-2 downloads | T-2 stored as part 2; ingested; automatic summary re-opened by `7b` | N/A |
| Asked after the window | the run that stored part 1 started after the window ended | not asked again; T-2 and later parts never read | N/A |
| Write of part 1 lost, then part 2 refused | part 1's write refused for good, T-2 404 | nothing stored; row refused naming both | non-retryable |
| Ingestion exhausted before | three failed ingestions, then part 2 kept by 33j | attempts reset; part 2 ingested, as `33g` | N/A |

</frozen-after-approval>

## Code Map

Verified against `2d8a559` (origin/main) on 2026-10-20; re-verified against `82c2edb` (main) on 2026-10-21. Since then 11e changed one line each in the adapter (`_fits`) and acquisition (`_select_in`), neither in this slice's path; 7b, 7e, 7c, 7d and 4x touched none of the anchors below except by moving line numbers in storage, `core/transcripts.py` and pipelines (updated here).

- `pm_ai/connectors/transcripts/graph_transcripts.py:32` the module's "all or nothing"; `:297` `_Run.go` (a provider fault or unclassified error refuses the meeting and nothing is returned); `:351` `_match`; `:468-477` decision 5, the all-or-nothing return at `:473`; `:596` `_download` and its refusals (attribution `:611-630`, permission `:630-632`, expired `:634` — a `TranscriptAbsent`, 404 `:640`, protocol `:648`, not UTF-8 `:657`, empty body `:664` retryable)
- `pm_ai/domain/transcripts.py:225` `TranscriptCaptured` (gains the optional refusal); `:276` `TranscriptRefused`; `:328` `PartState` (unchanged)
- `pm_ai/core/transcript_acquisition.py:167` `_select_in`, asked-again rule at `:213`; `:346` the ask recorded for a capture; `:380` `_ends_asking` (a capture counts as asked); `:391` `_settled`; `:440` `_kept`; `:449` `_stored`, its "Not stored:" reason and run refusal, the lost-write path at `:544`
- `pm_ai/connectors/transcripts/manual.py:28` `acquire` (never sets the refusal)
- `pm_ai/storage/service.py:242` `transcript_parts` table; `:4572` `record_pending_part`; `:4620` `mark_part_stored` (the attempt reset); `:4650` `drop_pending_part`; `:4817` `offer_summary` (the re-open)
- `pm_ai/core/transcripts.py:612` `meeting_utterances` (reads `stored` rows only)
- `pm_ai/app/pipelines.py:770` `ingest_captures`, stored, un-ingested parts only at `:884`; `:577` the harvest offers waiting summaries; `:4764` `note_summary_input`; `:4798` `_stored_parts_on_disk`
- `tests/connectors/test_graph_transcripts.py:1540-1552` `test_a_part_whose_download_fails_returns_the_refusal_and_no_other_part`, whose "part 2 fails" case flips and whose "part 1 fails" case stays; `:984` `_daemon`, the `run_harvest` acceptance harness over `FakeGraph`
- `tests/core/test_transcript_acquisition.py:579` the refusal-keeps-rows test; `:747` the lost-write test, the model for the new acquisition tests
- `tests/slice/test_transcript_path.py:1516` the exhausted-ingestion test, the model for the 33j one; `tests/slice/test_meeting_summary_slice.py:801` a later part re-opening the summary through a harvest

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/domain/transcripts.py` -- `TranscriptCaptured` gains the optional refusal sentence, checked non-empty when set -- the outcome
- [x] `pm_ai/connectors/transcripts/graph_transcripts.py` -- decision 5 stops at the first part refused for good and returns the parts before it with the refusal; retryable refusals keep all or nothing; module and `_match` docstrings updated -- the adapter
- [x] `pm_ai/core/transcript_acquisition.py` -- `_stored` appends the carried refusal to the row's "Not stored:" and to the run's refusals -- the caller
- [x] `tests/connectors/test_graph_transcripts.py`, `tests/core/test_transcript_acquisition.py`, `tests/slice/test_transcript_path.py` -- one test per matrix row; the flipped test renamed for what it now pins
- [x] `_bmad-output/specs/spec-pm-ai/stories/33g-every-part-of-a-transcript.md` -- dated Change Log line: a later part refused for good keeps the parts before it, amended by `33j`; the Suggested Review Order's "all or nothing (open decision in deferred-work)" now points at `33j`
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- the DECIDED entry under "Surfaced by the story-33g review (2026-10-19)" marked built by `33j`; if question 1 keeps the recommendation, a limit: a part refused for good costs every part after it once the meeting is no longer asked

**Acceptance Criteria:**
- Given a meeting with nothing stored and two new parts whose second answers 404, when acquisition runs, then part 1 is stored byte-identical to its download, its row is `fetched` naming the second part's transcript id and 404, and the run's refusals name it.
- Given that meeting, when ingestion and the summary job run, then part 1 is ingested and summarised as the meeting's only part.
- Given a meeting whose first new part answers 404, when acquisition runs, then nothing is stored and the row is refused, non-retryable, as before this slice.
- Given an empty body on part 2, when acquisition runs, then nothing is stored and the meeting is asked again, as before this slice.
- Given the change, when `uv run pytest -q` runs, then all pass.

## Spec Change Log

- **2026-10-20, created.** Carries Andrei's decision of 2026-10-19 (`33g` review): a new part refused for good no longer discards the parts downloaded before it; they are returned in time order with the refusal named. Settled here: only refusals waiting cannot clear keep earlier parts; parts after the refused one are not downloaded; the refused part keeps no row; `33g`'s asked-again rule and attempt reset and `7b`'s re-open apply unchanged. Two questions for Andrei in Ask First. Depends on no spine amendment.
- **2026-10-20, Ask First answered (Andrei):** (1) the refused part keeps no row; later parts wait for it while the meeting is still asked, so a part deleted from Teams costs the parts after it — accepted; (2) a summary does not say a part is missing. Spec ready for its build checkpoint.
- **2026-10-21, built and reviewed.** Code Map re-verified against `82c2edb` after 7b, 7e, 7c, 7d, 11e and 4x merged: frozen statements hold, line anchors in storage, `core/transcripts.py` and pipelines moved. The carried sentence is `TranscriptCaptured.refused_part`. Three review layers ran (blind hunter, edge-case hunter, verification gap). Patched: the sentence names the transcript id once and closes its reason before the later-parts clause, with singular wording for one later part; a write failure and a refused part are joined as two sentences; tests check that part 1 is asked for, that an unclassified fault's retryability is the shared rule, and that the second harvest is told part 1 is stored; the fake Graph raises a scripted exception from a list too. No finding needs Andrei's decision; the rest were rejected as decided by this spec (first refused part alone, retryable failures all or nothing, expiry and permission refusals keep earlier parts, the accepted limit in `deferred-work.md`). The summary test lives in `tests/slice/test_meeting_summary_slice.py`. Frozen block unchanged.

## Design Notes

**Why stop at the refused part.** Parts are numbered in time order and a number is taken only by a stored part. Storing a later part while an earlier one is missing would give the later part the earlier one's number; if the earlier one then downloaded on a later run, `33g`'s fourth decision (earlier than a stored part) would refuse it for good. Stopping keeps both numbering and that path open. The alternative in question 1 removes the problem a different way, by giving the refused part a number of its own.

**Why retryable failures stay all or nothing.** Keeping part 1 on a retryable failure would record the meeting as asked and could end its asking before part 2 is read; today's rule loses nothing because the next run downloads every part again. `33g` handles a retryable write failure with a `pending` row; a download failure has no capture to claim a row for.

**Expiry** is included with the refusals because Graph saying the meeting has expired will not change by asking again, and the parts already read are as valid as before.

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_transcripts.py tests/core/test_transcript_acquisition.py tests/slice/test_transcript_path.py -q` -- expected: every matrix row passes, no network
- `uv run pytest -q` -- expected: all pass
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

## Suggested Review Order

**The adapter stops at the refused part**

- Entry point: decision 5 keeps captures before a part refused for good, asks nothing after.
  [`graph_transcripts.py:478`](../../../../pm_ai/connectors/transcripts/graph_transcripts.py#L478)

- Which download outcomes keep earlier parts: non-retryable refusals and expiry only.
  [`graph_transcripts.py:847`](../../../../pm_ai/connectors/transcripts/graph_transcripts.py#L847)

**The outcome carries the refusal**

- One optional field; blank refused, port signature unchanged.
  [`transcripts.py:241`](../../../../pm_ai/domain/transcripts.py#L241)

**Acquisition names it**

- The sentence joins "Not stored:" and the run's refusals, after any write failure.
  [`transcript_acquisition.py:574`](../../../../pm_ai/core/transcript_acquisition.py#L574)

**Tests**

- First new part refused: the refusal alone, as before.
  [`test_graph_transcripts.py:1545`](../../../../tests/connectors/test_graph_transcripts.py#L1545)

- Part 2 refused, every kind waiting cannot clear.
  [`test_graph_transcripts.py:1595`](../../../../tests/connectors/test_graph_transcripts.py#L1595)

- Empty body stays all or nothing.
  [`test_graph_transcripts.py:1645`](../../../../tests/connectors/test_graph_transcripts.py#L1645)

- Acceptance through a harvest: part 1 byte-identical, ingested.
  [`test_graph_transcripts.py:1740`](../../../../tests/connectors/test_graph_transcripts.py#L1740)

- Row and run refusals; lost write plus refused part.
  [`test_transcript_acquisition.py:966`](../../../../tests/core/test_transcript_acquisition.py#L966)

- Summary of the kept part, re-opened when the refused one arrives.
  [`test_meeting_summary_slice.py:832`](../../../../tests/slice/test_meeting_summary_slice.py#L832)

- Exhausted ingestion reset by a kept part.
  [`test_transcript_path.py:1567`](../../../../tests/slice/test_transcript_path.py#L1567)
