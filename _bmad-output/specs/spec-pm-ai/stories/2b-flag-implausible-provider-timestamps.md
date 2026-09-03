---
title: 'Flag implausible provider timestamps'
type: 'feature'
created: '2026-08-29'
status: 'done'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `_append_batch` (`service.py:1089-1111`) stamps `ingested_at` from the injected clock and renders an absent `occurred_at` as the literal `unknown`, but a provider timestamp dated two days into the future is written verbatim and read later as fact. AD-35 requires the implausible one **flagged**. Story 2a added the validator; nothing calls it.

**Approach:** Validate `occurred_at` at persist and record the verdict in the entry. The event is still persisted — the activity is real, only its provider clock is suspect — so the flag travels with the record rather than discarding it.

## Boundaries & Constraints

**Always:**
- A flagged event is **persisted, not rejected**. `persist_events` is all-or-nothing (`service.py:1061-1086`), so raising here would drop an entire harvest batch because one provider clock is wrong.
- The flag is written into the entry, never used to correct the timestamp. No backfill from `ingested_at`, which is the substitution AD-35 forbids.
- `now` for the comparison is the batch's own `at` — the value already taken from the injected clock at `service.py:1080` — so a test controls plausibility with the clock it already injects.

**Ask First:** Whether a flagged event is admissible as evidence. AD-36 governs *authorship*, not timestamp plausibility, and making a flagged event inadmissible changes the commitment verifier (story 15). Default here: the flag is recorded and nothing consumes it yet.

**Never:** No change to `pm_ai/domain/clocks.py` — 2a froze that surface. No new entry field beyond the flag. No rejection, no correction, no logging side channel.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Plausible | `occurred_at` hours before the batch clock | entry unchanged from today's rendering | N/A |
| Future-dated | `occurred_at` 48h after the batch clock | entry persisted, carrying the flag | N/A |
| Absent | `occurred_at is None` | `unknown`, as today; not flagged — absence is a distinct state | N/A |
| Non-UTC provider value | tz-naive or offset datetime | entry persisted, carrying the flag | N/A |
| Mixed batch | one flagged among several | every event persisted; `persisted` counts all and `flagged` counts the suspect | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/storage/service.py:1089-1111` -- `_append_batch`, the only place `occurred_at` is rendered
- `pm_ai/storage/service.py:1080` -- the batch clock read, which becomes the comparison's `now`
- `pm_ai/domain/clocks.py` -- 2a's validator, consumed here for the first time
- `pm_ai/domain/events.py:141` -- the nullable provider field being judged
- `tests/slice/test_storage_resolution.py:272` -- the existing pinned-clock fixture to extend

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/storage/service.py` -- call the validator in `_append_batch` and carry its verdict into the rendered line -- the flag reaches the ledger instead of the exception reaching the batch
- [ ] `tests/slice/test_storage_resolution.py` -- unit-test every matrix row against the injected clock -- plausibility is only observable through that clock

**Acceptance Criteria:**
- Given a batch where one event is future-dated, when persisted, then every event lands in the segment and `PersistResult.persisted` counts all of them.
- Given a flagged entry, when the segment is read, then the original provider timestamp is still present verbatim beside the flag — flagging never edits the value.
- Given every existing storage test, when the suite runs, then none change behaviour: a plausible timestamp renders exactly as before.

## Spec Change Log

- **2026-08-29, resequenced behind 2d before implementation.** The original plan had this depend on 2a alone, which would have meant editing the very f-string 2d deletes — the work done twice and two golden tests disagreeing. Landing it after the renderer made the flag an ordinary field on `EventEntry` instead of a format edit, so this story touches no grammar at all.
- **`PersistResult` gains `flagged`,** from a review finding that nothing reported how many events in a batch carried a suspect clock. Without it a connector whose clock is wrong flags every event it emits and no caller can see that it happened: the entries are in the ledger and nobody is looking. One counter on the report object the batch already returns, defaulted so no existing construction moves.
- **The flag is one token, `occurred_at_flag=implausible`, not a reason code.** A machine-readable reason would have to come from `pm_ai.domain.clocks`, and 2a's surface is frozen by this story's Never. Diagnosis is served by the timestamp itself, which sits beside the flag verbatim.
- **The Ask First stays open, with the stated default taken.** Whether a flagged event is admissible as evidence is story 15's question: AD-36 governs authorship, not plausibility. Here the flag is recorded and nothing consumes it. KEEP: the catch around `validate_occurred_at` — propagating it would discard an entire harvest because one provider clock is wrong.

## Design Notes

The flag is a token in the entry rather than a separate ledger or a log line, because the reader that cares is the one reading the entry. A parallel "suspect timestamps" list would be a second structure describing the same records, which is the failure mode `derivation-services.md` names for derived state generally: two structures that can disagree.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_storage_resolution.py -q` -- expected: all pass, including the new rows
- `uv run pytest -q` -- expected: no new failures and no new skips
