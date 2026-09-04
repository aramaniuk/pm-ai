---
title: 'Meeting records reach Tier 1'
type: 'feature'
created: '2026-09-02'
status: 'ready-for-dev'
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
- **All I/O goes through the `StoragePort`** — `pm_ai/storage` forbids file access outside `service.py`, and `derivation-services.md` rule 3 requires shared resources to reach storage through one accessor each. `8f` declares `write_artifact`, `read_artifact` and the collection listing `for_day` needs; this slice depends on that.
- **The day boundary belongs to a stated timezone.** `for_day(day, *, tz)` takes it explicitly. `Meeting.start` is aware UTC, so "today" for a PM outside UTC is a different set of meetings, and leaving the parameter implicit means whichever of this slice and `23a` is written first decides it silently.
- **Ordering is total.** `for_day` orders by `(start, meeting_id)`, because two meetings can share a start instant and `23a` requires a byte-identical re-render.
- **The record is the ledger's field grammar, without the ledger's envelope.** One `key=value` per line, rendered by `render_value` and parsed by `scan_fields` (`event_entries.py:171-184,356`), then a free body. It is **not** a ledger line: `parse_line` requires `- [id] category actor=` and refuses duplicate keys, and this is a document with sections. Saying so matters, because the next reader will otherwise assume `parse_line` applies.
- **`attendees` is one comma-separated value, and comma is therefore reserved.** The grammar has no lists and `parse_line` refuses duplicate keys, so the list cannot be repeated fields. Store handles only — a display name like "Smith, Bob" would split into two attendees, and nothing would notice.
- **Man-Hour Cost is never stored.** CAP-1 puts it in the *summary card* header, which is a rendered surface, and it derives from `blended_hourly_rate` in `config.toml` — so a stored value is wrong the moment the rate changes. The record stores `attendees` and `duration_minutes`; the renderer multiplies.
- **`tentative` is stored, `stale` is derived.** Tentative is provider data — Graph's response status — and must be persisted. Stale means "absent from a window we actually harvested", which `8a`'s `CoverageWindow` already makes derivable, so storing it would be a second source of truth that goes wrong quietly.
- **The record has machine-owned and human-owned regions, and a write preserves the human's.** pm-ai owns the fields; `## Notes` is copied through verbatim. A `## Summary` region is reserved and left empty by this slice — it is transcript-derived and needs a model, which decision 2 puts beyond wave 2. This is what resolves the contradiction between this slice's replace-on-rewrite row and `33c`'s never-silently-overwrite-a-hand-edit rule: the regions differ, so both hold.
- **A meeting id is stable for the life of the record**, because `source_ref` derives from it (`meetings.py:37-39`) and a 30-day transcript purge must not empty a citation.

**Ask First:** Nothing. The record format was decided on 2026-09-03 and is stated above.

**Never:** No amendments, no summary — both need the transcript path (`11b`) and a model, and both are queued with their decisions in `deferred-work.md`. No transcript handling — binding transcripts to meetings is `11b`. No extraction changes. No Graph code. No commitment or proposal records.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Write and read back | a `Meeting` in the project scope | persisted under that scope's `meetings/`; read back equal | N/A |
| Personal-subject meeting | `Meeting.scope` is personal | lands in the personal tree, not the project one | N/A |
| Citation direction illegal | personal meeting, project-scoped daemon | refused before any write | existing `assert_citation_legal` |
| Re-write of an existing id | same `meeting_id` twice | fields are replaced; `## Notes` is preserved byte-identical | N/A |
| Attendee handle containing a comma | a display name slipped in | refused — comma is the list separator and nothing else would notice the split | `MalformedMeeting` |
| All-day meeting | `duration_minutes` is `0` per `33c` | stored and read back as `0`; the renderer's cost is `0.0` | N/A |
| Tied start instants | two meetings at the same moment | ordered by `(start, meeting_id)` | N/A |
| Collection absent | nothing ever written | `for_day` returns empty — a first-run state, not a failure | N/A |
| Malformed record for another day | one bad file, unrelated date | `for_day` reads only its own day; unrelated damage does not raise | scoped `MalformedMeeting` |
| Foreign file in the collection | `.DS_Store`, an editor swap file | ignored, not parsed as a record | N/A |
| `ScopeKind.PEOPLE` meeting | a 1:1 with a direct report | written to that person's tree — `meetings/` is declared there (`scope_model.py:650`) | N/A |
| Id reused across scopes | same id written personal then project | **not detected here.** Enumerating three trees on every `put` would breach AD-25's wall from a project-scoped caller, and the accessor is built against one scope. Uniqueness rests on the provider id `33c` keys on; recorded as a limit | N/A |
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
- [ ] `pm_ai/core/meeting_records.py` -- the render/parse pair for the record, preserving `## Notes` and the amendment log on rewrite
- [ ] `tests/core/test_meeting_records.py` -- the matrix, including a hand-edited file, a malformed one, and a rewrite that must not lose the notes
- [ ] `tests/slice/test_meeting_persistence.py` -- write, discard the accessor, rebuild against the same temporary root, read back -- the only shape that can observe persistence at all

**Acceptance Criteria:**
- Given a meeting written through one accessor, when a **freshly built** accessor over the same temporary root reads it, then the record is returned. Stated this way because a `core` unit test against a `StoragePort` cannot represent a restart, so an accessor that caches in memory and never persists would satisfy both the original criterion and the read-back matrix row.
- Given a real Graph meeting id, when it is written, then it is accepted and `get` returns it — the safe-name encoding is exercised against a genuine id, not a hand-written one, because `33b` is the first writer and a refusal there blocks it.
- Given a personal-scope meeting and a project-scoped daemon, when ingestion runs, then it is refused before any file is written.
- Given a record with hand-written `## Notes`, when the fields and summary are rewritten, then the notes are byte-identical afterwards — asserted by hashing that region, because "preserved" and "regenerated identically" are indistinguishable from a success message.
- Given any `Meeting`, when rendered and parsed back, then it is equal — the render/parse drift pair, the same property `4g` guards for `config.toml`.
- Given `grep -n "meetings" pm_ai/app/wiring.py`, then no `dict` remains.

## Spec Change Log

- **2026-09-03, the record format decided, and the review's findings applied.** The `Ask First` is answered: the record is the ledger's **field** grammar — `key=value` per line through `render_value`/`scan_fields` — plus a free body, and explicitly *not* a ledger line, since `parse_line` requires the `- [id] category actor=` envelope and refuses duplicate keys. One wart is stated rather than discovered: `attendees` is a comma-separated value because the grammar has no lists, so comma is reserved and handles are stored without display names.
  **Man-Hour Cost is computed, never stored** — CAP-1 puts it in a rendered card and it derives from a `config.toml` value that changes. **`tentative` is stored and `stale` is derived** from `8a`'s coverage, so there is one source of truth for each.
  **The record gained machine-owned and human-owned regions**, which resolves the contradiction the review found between this slice's replace-on-rewrite row and `33c`'s never-overwrite-a-hand-edit rule: pm-ai owns the fields and `## Summary`, `## Notes` is preserved, and a criterion hashes that region because "preserved" and "regenerated identically" look the same from a success message. `## Summary` is derived from the transcript **and** the amendment log, so a correction reads correctly instead of sitting below the thing it corrects.
  **Amendments and the summary left the slice.** Both were specified here on 2026-09-03 and taken back out the same day: `## Summary` is transcript-derived and needs a model, which decision 2 removes from waves 1 and 2, and amendments exist to correct a summary. Keeping them would have specified machinery whose input cannot exist yet. The region is reserved, the notes-preservation rule stays, and the design — amendments as provenanced records, the summary derived from transcript *plus* amendments, and `meeting_amended` as a `SelfActionType` member — is queued in `deferred-work.md` with the reasoning that forced it.
  **The cross-scope id row was unimplementable** (adversarial). Detecting reuse means enumerating three trees on every `put`, which breaches AD-25's wall from a project-scoped caller, and `MeetingNotFound` is the wrong error for refusing a write. Recorded as a limit resting on the provider id `33c` keys on.
  **The port dependency moved from `8b` to `8f`**, which is where artifact I/O and the collection listing `for_day` needs are now declared.

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
