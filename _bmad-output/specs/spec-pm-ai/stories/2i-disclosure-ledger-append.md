---
title: 'Disclosure ledger append'
type: 'feature'
created: '2026-08-29'
status: 'in-review'
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
- **Same value encoding as an event entry, no shared vocabulary.** The line is `key=value` pairs quoted by the same rule, so 2j's parser reuses the tokenizer — but the disclosure record gets no `LedgerCategory` member. Adding one would let a disclosure be spelled as an `EventEntry` and appended to a git-committed project log, and `assert_writable` runs only in `_append_batch` (`service.py:1209`), never in `append_event_log` — so nothing would refuse it. That is the leak AD-38 exists to prevent, reintroduced through the vocabulary.
- **No entry id and no category token.** Every line in this file is a frontier call, so there is nothing to tag; and the spine's id prefixes (`cmt_`, `prp_`, `evt_`, `job_`, `skl_`, `goal_`) have no member for one, which is a change this story does not need to make. The file is the vocabulary.
- **Contributing scopes render in sorted order.** A frozenset iterates arbitrarily, and an audit line that differs between two runs over identical data is not an audit line.
- Every field of `DisclosureRecord` is rendered: `task_class`, `model`, both token counts, the cost estimate, the contributing scopes and the destination. A cost total that cannot be recomputed from the ledger is not an audit trail.

**Ask First:** None outstanding. The grammar question is answered below: same value encoding, separate vocabulary, no shared category enum.

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

- **2026-08-29, grammar question resolved: shared encoding, separate vocabulary.** Sharing 2c's `LedgerCategory` would have created a spelling for a disclosure that `append_event_log` accepts into any scope — and the leak guard runs only on the batch path, so a project-scope write would land unrefused. The line therefore reuses the value encoding (so one tokenizer serves both files) and takes no category and no id: every line in `disclosure.md` is a frontier call, and the file is the vocabulary.
- **Sorted scopes added to the Always list.** `contributing_scopes` is a frozenset; rendering it unsorted makes the same call produce different bytes on different runs.

## Verification

**Commands:**
- `uv run pytest tests/domain/test_disclosure.py -q` -- expected: all pass
- `uv run pytest -q` -- expected: no new failures
