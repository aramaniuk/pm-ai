---
title: 'Meeting records reach Tier 1'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `Meeting` is the citation root for everything said in a meeting (AD-33) and is declared Tier-1 in three scope trees (`scope_model.py:554,650,716`). It is never persisted. `Daemon.meetings` is a plain `dict[str, object]` (`wiring.py:43`) that `run_transcript_ingestion` writes into (`pipelines.py:51`), so every citation minted today resolves against process memory and dies with the process. Nothing in `storage/service.py` mentions meetings at all.

**Approach:** Add the `MeetingRecords` accessor named by `derivation-services.md` rule 3, performing its I/O through `StorageService`, and retire the in-memory dict. `33b` becomes its first writer.

## Boundaries & Constraints

**Always:**
- **A meeting is written to the scope that owns its subject** (AD-33/AD-38). `Meeting.scope` is required rather than defaulted precisely because it decides where the record lands and whether a git-committed scope may cite it; the accessor honours the field and never guesses.
- **`assert_citation_legal` still gates ingestion.** `pipelines.py:50` checks the citation direction before extracting; moving meetings to disk does not move that check.
- **All I/O goes through the `StoragePort`** — `pm_ai/storage` forbids file access outside `service.py`, and `derivation-services.md` rule 3 requires shared resources to reach storage through one accessor each. `8b` declares `write_artifact` and `read_artifact` on the port; this slice depends on that.
- **The day boundary belongs to a stated timezone.** `for_day(day, *, tz)` takes it explicitly. `Meeting.start` is aware UTC, so "today" for a PM outside UTC is a different set of meetings, and leaving the parameter implicit means whichever of this slice and `23a` is written first decides it silently.
- **Ordering is total.** `for_day` orders by `(start, meeting_id)`, because two meetings can share a start instant and `23a` requires a byte-identical re-render.
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
| Tied start instants | two meetings at the same moment | ordered by `(start, meeting_id)` | N/A |
| Collection absent | nothing ever written | `for_day` returns empty — a first-run state, not a failure | N/A |
| Malformed record for another day | one bad file, unrelated date | `for_day` reads only its own day; unrelated damage does not raise | scoped `MalformedMeeting` |
| Foreign file in the collection | `.DS_Store`, an editor swap file | ignored, not parsed as a record | N/A |
| `ScopeKind.PEOPLE` meeting | a 1:1 with a direct report | written to that person's tree — `meetings/` is declared there (`scope_model.py:650`) | N/A |
| Id reused across scopes | same id written personal then project | refused; a `meeting:<id>` citation must resolve to one record | `MeetingNotFound` on the second scope |
| Provider id unsafe as a filename | a long, dot-leading Graph id | encoded to a safe stable name; the id is preserved inside the record | N/A |
| Unknown id read | id never written | reported as absent, not an empty `Meeting` | `MeetingNotFound` |
| Listing a day | a date with two meetings | both returned, ordered by `start` | N/A |
| Hand-edited record | a human edited the file | parsed if well-formed; a malformed record is surfaced, never skipped silently | `MalformedMeeting` |
| Name is path-unsafe | a `meeting_id` containing `../` | refused, as `write_artifact` already refuses for captures | propagated |

</frozen-after-approval>

## Code Map

- `pm_ai/core/meeting_records.py` -- new; the accessor, its parser and renderer
- `pm_ai/domain/meetings.py:17-50` -- `Meeting`, `source_ref`, `transcript_home`, `man_hour_cost`; unchanged
- `pm_ai/app/wiring.py:43` -- the `meetings` dict this story removes from `Daemon`
- `pm_ai/app/pipelines.py:50-51` -- the citation check and the dict write
- `pm_ai/storage/service.py:1022,1065` -- `write_artifact` / `read_artifact` and the `name` parameter for `Collection` members
- `pm_ai/domain/scope_model.py:554,650,716` -- the three `meetings/` declarations
- `_bmad-output/specs/spec-pm-ai/derivation-services.md` -- rule 3, which names this accessor

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/meeting_records.py` -- add `MeetingRecords` with `put`, `get`, `for_day(day, *, tz)`, `MeetingNotFound`, `MalformedMeeting`, and the safe-name encoding
- [ ] `pm_ai/app/wiring.py` -- replace the `meetings` dict with the accessor
- [ ] `pm_ai/app/pipelines.py` -- write through the accessor, citation check unmoved
- [ ] `tests/core/test_meeting_records.py` -- the matrix, including a hand-edited file and a malformed one
- [ ] `tests/slice/test_meeting_persistence.py` -- write, discard the accessor, rebuild against the same temporary root, read back -- the only shape that can observe persistence at all

**Acceptance Criteria:**
- Given a meeting written through one accessor, when a **freshly built** accessor over the same temporary root reads it, then the record is returned. Stated this way because a `core` unit test against a `StoragePort` cannot represent a restart, so an accessor that caches in memory and never persists would satisfy both the original criterion and the read-back matrix row.
- Given a real Graph meeting id, when it is written, then it is accepted and `get` returns it — the safe-name encoding is exercised against a genuine id, not a hand-written one, because `33b` is the first writer and a refusal there blocks it.
- Given a personal-scope meeting and a project-scoped daemon, when ingestion runs, then it is refused before any file is written.
- Given `grep -n "meetings" pm_ai/app/wiring.py`, then no `dict` remains.

## Spec Change Log

- **2026-09-02, `wiring.py` citations re-pointed after story 4a.** 4a added one import to `wiring.py`, shifting every line below it, and a parameter plus a docstring paragraph to `build()`, shifting the rest further. The numbers below named other code. **Line numbers only — no wording, no intent, no task, and no acceptance criterion changed.**

- **2026-09-02, multi-lens review.** The persistence claim was untestable and the day boundary was unowned.
  **The central acceptance criterion could not be evaluated.** "Written and the process restarted" was to be verified by a `core` unit test against a `StoragePort`, where a restart is unrepresentable — so an accessor that cached in memory and never wrote a byte would have satisfied both it and the matrix's read-back row, leaving exactly the defect the slice exists to fix. A `tests/slice` case against a real temporary root is now the criterion.
  **`for_day` took a bare date.** `Meeting.start` is aware UTC, so which meetings are "today" for a PM outside UTC depends on a timezone nobody owned — and `23a` declared its own Ask First as "Nothing", so whichever slice was written first would have decided it. The parameter is now explicit. *Raised independently by the adversarial and edge-case lenses.*
  **`StoragePort` declares no artifact methods**, which this accessor assumed. `8b` now declares them and this slice depends on that.
  The edge-case lens added the paths that would have made the dashboard brittle: tied start instants breaking `23a`'s byte-identical re-render, one malformed record blocking every render regardless of date, a `.DS_Store` parsed as a meeting, and — the one that blocks `33b` — real Graph ids being long and sometimes dot-leading, which `write_artifact`'s name validation may refuse outright.
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
