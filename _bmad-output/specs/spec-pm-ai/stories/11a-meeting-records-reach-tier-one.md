---
title: 'Meeting records reach Tier 1'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `Meeting` is the citation root for everything said in a meeting (AD-33) and is declared Tier-1 in three scope trees (`scope_model.py:554,650,716`). It is never persisted. `Daemon.meetings` is a plain `dict[str, object]` (`wiring.py:42`) that `run_transcript_ingestion` writes into (`pipelines.py:64`), so every citation minted today resolves against process memory and dies with the process. Nothing in `storage/service.py` mentions meetings at all.

**Approach:** Add the `MeetingRecords` accessor named by `derivation-services.md` rule 3, performing its I/O through `StorageService`, and retire the in-memory dict. `33b` becomes its first writer.

## Boundaries & Constraints

**Always:**
- **A meeting is written to the scope that owns its subject** (AD-33/AD-38). `Meeting.scope` is required rather than defaulted precisely because it decides where the record lands and whether a git-committed scope may cite it; the accessor honours the field and never guesses.
- **`assert_citation_legal` still gates ingestion.** `pipelines.py:50` checks the citation direction before extracting; moving meetings to disk does not move that check.
- **All I/O goes through `StorageService`** — `pm_ai/storage` forbids file access outside `service.py`, and `derivation-services.md` rule 3 requires shared resources to reach storage through one accessor each.
- **A meeting id is stable for the life of the record**, because `source_ref` derives from it (`meetings.py:37-39`) and a 30-day transcript purge must not empty a citation.

**Ask First:** The on-disk record format. `meetings/` is a Tier-1 `Collection` of plaintext Markdown, hand-editable by design (AD-3), and choosing its shape sets what every later reader parses. Man-Hour Cost belongs in the summary header per CAP-3, which suggests a header the renderer can read without a model.

**Never:** No transcript handling — binding transcripts to meetings is `11b`. No extraction changes. No Graph code. No commitment or proposal records.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Write and read back | a `Meeting` in the project scope | persisted under that scope's `meetings/`; read back equal | N/A |
| Personal-subject meeting | `Meeting.scope` is personal | lands in the personal tree, not the project one | N/A |
| Citation direction illegal | personal meeting, project-scoped daemon | refused before any write | existing `assert_citation_legal` |
| Re-write of an existing id | same `meeting_id` twice | second write replaces the record; the id and `source_ref` are unchanged | N/A |
| Unknown id read | id never written | reported as absent, not an empty `Meeting` | `MeetingNotFound` |
| Listing a day | a date with two meetings | both returned, ordered by `start` | N/A |
| Hand-edited record | a human edited the file | parsed if well-formed; a malformed record is surfaced, never skipped silently | `MalformedMeeting` |
| Name is path-unsafe | a `meeting_id` containing `../` | refused, as `write_artifact` already refuses for captures | propagated |

</frozen-after-approval>

## Code Map

- `pm_ai/core/meeting_records.py` -- new; the accessor, its parser and renderer
- `pm_ai/domain/meetings.py:17-50` -- `Meeting`, `source_ref`, `transcript_home`, `man_hour_cost`; unchanged
- `pm_ai/app/wiring.py:42` -- the `meetings` dict this story removes from `Daemon`
- `pm_ai/app/pipelines.py:50-51` -- the citation check and the dict write
- `pm_ai/storage/service.py:1022,1065` -- `write_artifact` / `read_artifact` and the `name` parameter for `Collection` members
- `pm_ai/domain/scope_model.py:554,650,716` -- the three `meetings/` declarations
- `_bmad-output/specs/spec-pm-ai/derivation-services.md` -- rule 3, which names this accessor

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/meeting_records.py` -- add `MeetingRecords` with `put`, `get`, `for_day`, `MeetingNotFound`, `MalformedMeeting`
- [ ] `pm_ai/app/wiring.py` -- replace the `meetings` dict with the accessor
- [ ] `pm_ai/app/pipelines.py` -- write through the accessor, citation check unmoved
- [ ] `tests/core/test_meeting_records.py` -- the matrix, including a hand-edited file and a malformed one

**Acceptance Criteria:**
- Given a meeting written and the process restarted, when `get` is called with its id, then the record is returned — the claim the in-memory dict could never satisfy.
- Given a personal-scope meeting and a project-scoped daemon, when ingestion runs, then it is refused before any file is written.
- Given `grep -n "meetings" pm_ai/app/wiring.py`, then no `dict` remains.

## Design Notes

The accessor lives in `core` and takes a `StoragePort`, which keeps it testable without a filesystem and satisfies both the `pm_ai/storage` file-I/O rule and rule 3's one-accessor-per-resource requirement in a single shape.

`for_day` exists because it is what `23a`'s Time-Critical section needs, and putting the query beside the record keeps date handling in one place rather than in the renderer. It orders by `start`, which is the only ordering a dashboard section reading "today" can sensibly use.

Surfacing a malformed hand-edited record rather than skipping it follows the rule the scope-model guards hold: a structure someone hand-edits after install must be refused, not silently ignored. A skipped meeting is a missing dashboard row that nobody can explain.

## Verification

**Commands:**
- `uv run pytest tests/core/test_meeting_records.py -q` -- expected: all matrix rows pass
- `uv run pytest tests/slice -q` -- expected: the transcript slice still passes against the accessor
- `uv run pytest -q` -- expected: no new failures
- `uv run lint-imports` -- expected: contracts kept
