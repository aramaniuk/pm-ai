---
title: 'Payloads reach Tier 1'
type: 'feature'
created: '2026-08-30'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A harvested event's payload never reaches the ledger. A commit lands as `- [id] commit_pushed actor=u_42 ingested_at=… src=gitlab:alpha:commit:9f2a1c occurred_at=… authored_by=…` — the `sha` survives only incidentally, inside AD-34's reference grammar, and `message` and `branch` are gone. `storage-contract.md` makes Tier 1 "the source" and Tier 3 rebuildable "entirely from Truth, with zero loss", and `derivation-services.md` turns that into a rule: an artifact is Tier 3 only if a job can rebuild it from Tier 1 alone. `event_index.db` (story 18, CAP-23 and CAP-24) cannot be — the content it would index was never written. Story 1h's rebuild proof passes anyway, because it can only check what *is* in Tier 1. Inherited from story 1's format string and never examined since.

**Approach:** Serialize a payload's declared fields onto the line, derived from the dataclass rather than declared a second time.

## Boundaries & Constraints

**Always:**
- **Derived, never declared.** Payload fields come from `dataclasses.fields(payload)`, so adding a field to a payload puts it on the line with no registry to update and nothing to forget — the principle `derivation-services.md` uses for the job graph, for the same reason.
- **Prefixed `p.`**, so a payload field can never shadow an envelope field. The twenty payload names and the six envelope names are disjoint today; the prefix is what keeps that from being luck.
- **A `None` optional field is omitted, not rendered empty** — absent and empty are different facts, as they are for `occurred_at`.
- **Escapes become a table** — `\n`, `\\`, `\"` — symmetric in `render_value` and the parser. A literal newline still never appears in a line, so the append rule is untouched: a fragment is still detectable and a record still ends where it always did. Today the parser treats a backslash as "the next character, literally", so `\n` decodes to the letter `n`; that is why real commit messages could not be carried.
- **A category's declared fields are required, not exhaustive.** A producer may add a field without a schema change; a producer that drops a required one is refused. Equality would make the schema rigid, which is the failure mode of a second declaration.

**Ask First:** None. The length bound rises with this story (see Design Notes); per-field clipping is the fallback if a real payload proves unreadable.

**Never:** No change to `PAYLOAD_FOR`, to `NormalizedEvent`, or to any payload's shape — this story writes what they already declare. No index; that is story 18. No payload for self-action entries: they carry no payload object, and their fields *are* their content.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Commit with a message | `CommitPayload(sha, message, branch)` | `p.sha`, `p.message`, `p.branch` on the line | N/A |
| Optional field unset | `branch=None` | omitted, not `p.branch=` | N/A |
| Multi-line message | a real commit body | escaped; no literal newline in the line; round-trips exactly | N/A |
| Literal backslash-n in a value | `C:\next` | round-trips distinctly from a newline | N/A |
| A new payload field | a field added to any payload | appears on the line with no other change | N/A |
| Required envelope field dropped | a producer omits `external_id` | refused at construction | `MalformedEntry` |
| Undeclared extra field | a producer adds one | accepted — the schema is a floor | N/A |
| Oversized payload | beyond the raised bound | refused, naming the bound | `MalformedEntry` |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/event_entries.py` -- the escape table, the raised bound, `SELF_ACTION_FIELDS`
- `pm_ai/core/ledger.py` -- the parser half of the escape table
- `pm_ai/storage/service.py:_append_batch` -- where the payload is serialized
- `pm_ai/skills/registry.py` -- the producer whose fields disagree with its schema
- `pm_ai/domain/events.py` -- `PAYLOAD_FOR` and the nine payload types, read but not changed

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/event_entries.py` -- escape table, `SELF_ACTION_FIELDS` replacing the three never-constructed dataclasses, required-subset check in `__post_init__`, raised bound -- one grammar that can carry content
- [ ] `pm_ai/core/ledger.py` -- the matching unescape -- an asymmetric escape is a silent corruption
- [ ] `pm_ai/storage/service.py` -- serialize the payload in `_append_batch` -- Tier 1 becomes the source it is described as
- [ ] `pm_ai/skills/registry.py` -- write `idempotency_key`, drop the duplicated skill name -- the shipped mismatch
- [ ] `tests/architecture/test_static_rules.py` -- assert every `EventEntry(...)` in `pm_ai/` satisfies its category's schema -- runtime checks only cover producers a test runs

**Acceptance Criteria:**
- Given a commit with a multi-line message, when persisted and parsed back, then every payload field equals what the connector supplied.
- Given `dataclasses.fields` of each payload, when an event of that type is persisted, then every non-`None` field appears on the line.
- Given any value, when rendered and parsed, then it round-trips exactly — including one containing a literal backslash followed by `n`.

## Spec Change Log

## Design Notes

The bound rises from 4096 to 16384. A realistic commit line with its message inline is 94 characters, so this is headroom for a long body or an excerpt rather than a licence for large content. Raised rather than clipped because a truncated payload in Tier 1 is the same rebuild gap in smaller form — and hand-readability of the rare long line is worth less than the guarantee that Tier 1 is complete.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: no new failures; the harvest golden moves once, deliberately
- `uv run mypy` -- expected: clean
