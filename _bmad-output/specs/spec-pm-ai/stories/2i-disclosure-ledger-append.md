---
title: 'Disclosure ledger append'
type: 'feature'
created: '2026-08-29'
status: 'draft'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** CAP-27 requires frontier-call provenance and cost to reach the application-scoped disclosure ledger, and AD-17 requires every frontier call to log token counts and a cost estimate there. The domain half is built — `DisclosureRecord`, `assert_writable`, `cross_scope_split` (`domain/disclosure.py`) — and `assert_writable` is already wired into the writer (`service.py:38`). **Nothing writes the ledger.** `~/.pm-ai/disclosure.md` is declared Tier-1 truth (`scope_model.py:435`) and has no writer at all.

**Approach:** Render a `DisclosureRecord` to one Markdown line and append it through `StorageService`, on the same terms as an event-log entry.

## Boundaries & Constraints

**Always:**
- **`disclosure.md` joins `_APPEND_ONLY_KEYS`** (`storage_tiers.py:159`, today `{event_log/, commitments_log.md}`). Verified absent: `is_append_only(APPLICATION, "disclosure.md")` returns `False`, so `write_artifact` would replace the audit ledger whole and destroy every prior entry — the exact loss `AppendOnlyArtifact` exists to refuse.
- The application scope is the only destination, enforced by the existing guard rather than by the caller choosing correctly. `assert_writable` already raises `CommittedScopeLeak` for any other scope; this story must not route around it.
- One line per call, newline-terminated, obeying the same append rule as an event entry — a record without its terminating newline is not a record.
- Every field of `DisclosureRecord` is rendered: `task_class`, `model`, both token counts, the cost estimate, the contributing scopes and the destination. A cost total that cannot be recomputed from the ledger is not an audit trail.

**Ask First:** Whether the disclosure line reuses 2c/2d's `EventEntry` grammar or gets its own. Sharing means one parser; separating acknowledges that a disclosure record has no actor and no `occurred_at` and would render two fields empty forever.

**Never:** No monthly total, no audit query — that is 2j. No frontier caller: nothing in the tree makes a frontier call yet, so this story provides the write path and its tests, not a producer. No encryption; the ledger is plaintext Markdown by the same rule as every other Tier-1 file.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Ordinary record | a `DisclosureRecord` | one line appended to `~/.pm-ai/disclosure.md` | N/A |
| Wrong scope | routed to a project scope | refused before any write | `CommittedScopeLeak` |
| Personal contributor | `contributing_scopes` naming personal | recorded — the application ledger is its correct home | N/A |
| Whole-file write attempt | `write_artifact` on `disclosure.md` | refused, once the key is added | `AppendOnlyArtifact` |
| Zero-cost call | cost estimate `0.0` | rendered as a number, never omitted | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/disclosure.py` -- the record, the guards, and where the renderer belongs
- `pm_ai/domain/storage_tiers.py:159` -- `_APPEND_ONLY_KEYS`, missing `disclosure.md`
- `pm_ai/domain/scope_model.py:435` -- the ledger's declaration
- `pm_ai/storage/service.py:948-953` -- `write_artifact`'s ledger refusal, which starts covering this file
- `pm_ai/storage/service.py:980` -- the append path to mirror

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/storage_tiers.py` -- add `disclosure.md` to `_APPEND_ONLY_KEYS` -- close the truncation hole first
- [ ] `pm_ai/domain/disclosure.py` -- add the renderer -- one definition of a disclosure line
- [ ] `pm_ai/storage/service.py` + `pm_ai/ports/__init__.py` -- add `append_disclosure`, declared on the port -- the ledger gains a writer
- [ ] `tests/domain/test_disclosure.py` -- test the matrix, including the truncation refusal -- that hole was open and must stay closed

**Acceptance Criteria:**
- Given `write_artifact` is called on `disclosure.md`, then it is refused — a regression test for the gap this story found.
- Given a record whose `contributing_scopes` name personal material, when appended to the application ledger, then it succeeds; when routed anywhere else, then `CommittedScopeLeak`.
- Given several appends, when the file is read, then every prior line is intact.

## Spec Change Log

## Verification

**Commands:**
- `uv run pytest tests/domain/test_disclosure.py -q` -- expected: all pass
- `uv run pytest -q` -- expected: no new failures
