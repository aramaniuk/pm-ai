---
title: 'People are written to the event log as handles'
type: 'feature'
created: '2026-10-11'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Today a person reaches an event-log line through an alias-table lookup at write time (`resolve_actor`). Only tests fill that table, so every actor is written as the bare `actor_unresolved` and the provider's address or account is lost. AD-34, the identity rule, now says a line records what the provider said and a reader decides who that is (Andrei, 2026-10-11). `33d`, Teams messages, cannot write a line until this spelling exists.

**Approach:** One producer in `domain` builds person handles `<system>:<handle>` over a closed set of handle systems, each with its own shape check. The envelope actor, the assignee and every mention carry a handle, and every connector switches to it. AD-36 mechanism 3, which recognises pm-ai's own bot accounts, compares handles against a declared set. Self-action lines get one actor constant. Mapping a handle to a person belongs to `2o`.

## Boundaries & Constraints

**Always:**
- **The type.** A frozen dataclass `PersonHandle(system, handle)` whose text form, `str(handle)`, is `<system>:<handle>`. Only the producer's module constructs it. `NormalizedEvent.__post_init__` refuses an actor that is not a `PersonHandle` at runtime, because a value from `row.get` is `Any` and mypy cannot catch it. `WorkItemPayload.assignee` becomes `PersonHandle | None`.
- **Closed handle systems, each with its shape**, declared once in `domain` and disjoint from the scopeless reference systems (`meeting`, `goal`). An import-time check raises on any overlap, so the guard holds under `python -O`.
  - `graph` and `graph_app` take a GUID. `graph` is a Graph user id. `graph_app` is a Graph application id: a bot sender, never mapped to a person.
  - `email`: surrounding whitespace is stripped, then exactly one `@` with text on both sides. No whitespace, comma, colon, quote or control character. ASCII only: non-ASCII is refused until a slice meets one.
  - `none`: the empty handle only, written `none:`.
- **The producer lowercases every handle.** For email this lowercases the whole address. That is deliberate: the standard makes the local part case-sensitive, but in practice it is not.
- **The producer refuses** a display name, the bare `actor_unresolved`, a string with no system, an unknown system (`unresolved:` included), a `:` inside the handle, and a handle of the wrong shape. Each refusal is a typed error that names the value. Commas are refused, so the comma-joined attendee list needs no escaping.
- **`none:` is legal only as an envelope actor.** It is refused as `assignee`, where absence is `None`, as a `mentions` member, and in the own-handle set.
- **The line carries the handle text, as written.**
  - The envelope writer writes `str(stamped.actor)`.
  - Person-list fields are declared by name; today the only one is `MessagePayload.mentions`. Each of their members is checked as a handle. `mentions` keeps `tuple[str, ...] | None`, AD-48's one list shape.
  - A line value that is not in canonical form, such as `email:Dana@X.com`, is a corrupted line, and the refusal names the field. This keeps a round trip byte-stable.
- **Alias code stays until `2o`.** `resolve_actor`, `ALIASES`, `UNRESOLVED_ACTOR` and `UNRESOLVED` remain, with no caller on the write path.
- **Connectors.** An address the producer refuses counts as no address. The refusal is caught per row and never ends a harvest.
  - GitLab commit author: `email:<author_email>`, or `none:`. Commits are GitLab's only source of people today.
  - Graph calendar organizer: `email:<address>`, or `none:`.
  - Graph attendees: recorded in the meeting record as `email:` handles. Duplicates are removed on the handle text after lowercasing. An attendee with no usable address is left out of the list but still counted: the meeting record gains `attendee_count`, the provider's number of attendee entries, and `Meeting.man_hour_cost` multiplies by it instead of by `len(attendees)`. So the meeting-cost figure counts every attendee.
  - `33d` is the first to write `graph:` and `graph_app:`. No connector produces an event from a transcript today, and the producer has no path from a speaker label, so `33e` and `11b` write `none:`.
- **AD-36 mechanism 3:**
  - `attribute` marks an event `pm_ai` when its actor is in `own_handles: frozenset[PersonHandle]`. The match is exact, with no alias lookup.
  - `own_handles` is a required keyword argument of `attribute_all`, with no default. It travels on a `Daemon` field set by `build(...)`.
  - The order of checks is unchanged: mechanism 3 is checked first. Then a scopeless reference is decided by `PM_AI_MINTED`. Then the ledger of pm-ai's executed changes is matched (mechanism 2). Then a scope with an unmatched change of pm-ai's answers `unknown`.
  - The set is empty today. Graph signs in as the user (`8m`), and GitLab uses the user's personal access token (`8o`).
- **Self-action lines are outside the handle rule.** Their actor is the constant `pm-ai` (`DAEMON_ACTOR`).
  - `EventEntry` refuses any other actor on a self-action category, both when it is built and when a line is read, because `parse_line` builds an `EventEntry`.
  - `skill_invoked` moves the skill name from the actor into a new required field `skill`, written `<system>.<skill>` with both parts non-empty.
  - `PM_AI_ACTOR` (`actor_pm_ai`), `PM_AI` and `Actor.is_pm_ai` are retired.
  - A reader tells observed lines and self-action lines apart by the entry's category.
- **The trusted reason for `mentions`** becomes "person handles around Graph user ids, which Microsoft generates and nobody types". The `mentions` comment and the known-limit docstring in `events.py` stop naming `unresolved:` and `actor`/`assignee`.
- **AD-27 exemption, dated 2026-10-11.** AD-27, the line-grammar rule, says a field never changes. This slice changes `assignee`'s type, `skill_invoked`'s actor and required fields, and the meeting record's fields. That is safe because nothing has been written yet: checked 2026-10-11, `~/.pm-ai` holds only `private/operational.db`, with no meeting record and no event-log line. The grammar pin table is updated to match.
- **Interim display, accepted:** until `23c` and `2o` land, the dashboard prints the handle text (`graph:<id>`, `none:`) as the sender.

**Ask First:** Nothing. The owner settled every open point on 2026-10-11.

**Never:**
- No mapping function, no `pm_identity()`, no alias-table persistence: those belong to `2o` and the alias-table entry under Open Risks.
- No Teams fetching (`33d`). No dashboard rendering change (`23c`).
- No retyping of `Meeting.attendees`: each `Actor` holds the handle text.
- No handle check on the actor column of an observed line when it is read.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Graph user | `graph`, a GUID in capitals | `graph:<guid lowercased>` | N/A |
| Graph bot | `graph_app`, a GUID | `graph_app:<guid>` | N/A |
| Graph id not a GUID | `graph`, `u_1` | not built | typed error naming the value |
| Address | `email`, ` Dana@Example.com ` | `email:dana@example.com` | N/A |
| Bad address | no `@`; two `@`; `a b@x`; `a,b@x`; `a:b@x`; `"a"@x`; `dané@x` | not built | typed error naming the value |
| No person | the producer's absence value | `none:` | `none:x` refused |
| Refused values | `Dana Smith`, `actor_unresolved`, `dana@example.com`, `gitlab:dana`, `unresolved:gitlab:x`, `meeting:m1` | not built | typed error naming the value |
| `none:` out of place | as `assignee`, as a `mentions` member, in `own_handles` | refused | typed error naming the field |
| Overlapping systems | a doctored set adds `meeting`, also under `-O` | import refused | typed error |
| Commit author | `author_email` set; absent; `not-an-address` | `actor=email:<address>`; `actor=none:`; `actor=none:` | row kept, never refused |
| Organizer | address; null `emailAddress`; unusable address | `actor=email:<organizer>` (organizer not added to attendees); `none:`; `none:` | row kept |
| Attendees | `Dana@x.com`, `dana@x.com` | `attendees=email:dana@x.com`, `attendee_count=2` | N/A |
| Attendee unusable | `a,b@x.com` or null address beside `c@x.com` | `attendees=email:c@x.com`, `attendee_count=2` | N/A |
| Meeting cost | 60 minutes, `attendee_count=3`, two handles, rate 100 | `man_hour_cost` 300 | N/A |
| Assignee | a handle | `p.assignee=email:dana@example.com`; decodes the equal handle | N/A |
| Mention not a handle | `mentions=("u_1",)` at encode; `p.mentions=u_1` on a line | refused, naming the field | typed error at encode; corrupted line at decode |
| Not canonical on a line | `p.assignee=email:Dana@X.com` | refused, naming the field | corrupted line |
| Own bot | actor `graph_app:<g>`, `own_handles={graph_app:<g>}` | `authored_by=pm_ai` | N/A |
| Same id, other system | actor `graph:<g>`, same set | not marked by mechanism 3 | N/A |
| Skill invoked | `gitlab.post_comment` runs | `skill_invoked actor=pm-ai … skill=gitlab.post_comment` | N/A |
| Wrong self-action actor | built or read with actor `test` | refused | `MalformedEntry` naming the actor |
| Handle built elsewhere | `PersonHandle(...)` or `dataclasses.replace` on one, outside the producer's module | architecture test fails, naming the file and line | N/A |

</frozen-after-approval>

## Code Map

Line numbers measured 2026-10-11.

- `pm_ai/domain/handles.py` (new, name suggested) -- the systems, `PersonHandle`, the producer, the absence value, and the parse function used by the decoder. Model the import-time guard on `_assert_reference_sets_agree` (`identity.py:186`); the scopeless set is `_SCOPELESS` (`identity.py:164`)
- `pm_ai/domain/identity.py:311-361` -- `PM_AI_ACTOR` (312), `is_pm_ai` (333) and `PM_AI` (338) are retired; `UNRESOLVED_ACTOR`, `UNRESOLVED`, `ALIASES` and `resolve_actor` stay. Exports at `pm_ai/domain/__init__.py:102-107`
- `pm_ai/domain/events.py` -- `assignee` (90), `mentions` comment (117-121), trusted reason (271-272), known-limit docstring (298-305), `NormalizedEvent.actor` (514), `__post_init__` (520)
- `pm_ai/domain/payload_fields.py` -- docstring (20-25); `_kind` (114), which picks the person grammar on `member is Actor` (126); person encode (190-200); `_refuse_sentinel` (225); person decode (317-324); `_corrupt_if_sentinel` (331). Both sentinel messages tell callers to write `unresolved:` and must be replaced
- `pm_ai/domain/event_entries.py` -- `SKILL_INVOKED` fields (112), `DAEMON_ACTOR` (161), `EventEntry.__post_init__` (295); `pm_ai/core/ledger.py:123` builds an `EventEntry` on read
- `pm_ai/storage/service.py:1702` -- `actor=stamped.actor.actor_id` becomes `str(stamped.actor)`
- `pm_ai/core/normalize.py:57-108` -- `attribute` (`is_pm_ai` at 83), `attribute_all` (95); caller `pm_ai/app/pipelines.py:82`; `Daemon` at `pm_ai/app/wiring.py:118`, `build` at `:223`
- `pm_ai/connectors/gitlab.py:249-258` -- the commit actor; the per-row `except RowRefused` at 595-597
- `pm_ai/connectors/graph/__init__.py` -- the per-row `try` at 745-755; `_meeting` (847-856); `_to_event` organizer (899-902) and `attendee_count` (910); `_attendees_of` (1108-1142, loop 1132-1141); sample organizer (455); docstring `graph/calendar.py:646`
- `pm_ai/domain/meetings.py:25,80-82` -- `attendees`, `man_hour_cost`; `pm_ai/core/meeting_records.py` -- `_render_attendees` (676), `_parse_attendees` (777), record fields (355-365, 437-442)
- `pm_ai/skills/registry.py:112` -- `actor=qualified_name`. No reader in `pm_ai/` treats `skill_invoked`'s actor as the skill name (grep, 2026-10-11)
- Tests building self-action entries with another actor: `tests/core/test_event_log.py:31,138,188`; `tests/core/test_ledger.py:20-21,34,165,188,199,204,212-217`; `tests/slice/test_storage_resolution.py:122,149-150,189,527,895,906`; `tests/core/test_retrospective.py:34,112`; `tests/architecture/ledger_fixtures.py:46`; `tests/architecture/test_capture_guard.py:480`; `tests/architecture/test_atomic_writes.py:359-360`; `tests/slice/test_vertical_slice.py:186`
- Other tests that change: `tests/domain/test_payload_fields.py:50-51,138-149`; `tests/domain/test_entry_grammar_pins.py:25,36,89`; `tests/domain/test_event_entries.py:177`; `tests/core/test_payload_serialisation.py`; `tests/connectors/test_graph_calendar_mapping.py:65,609-651`; `tests/slice/test_vertical_slice.py:28,122-131`; `tests/slice/test_dashboard_slice.py:443` (persists `actor=Actor("u_dana")`); `tests/slice/test_r4_gate_fixes.py:32,98,113,120-140`; `tests/architecture/test_domain_invariants.py:535-539,687,980-995`; `tests/architecture/test_guards_survive_o.py:39` (`CASES`)

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/handles.py` -- systems and shapes, `PersonHandle`, producer, parse, disjointness guard
- [ ] `pm_ai/domain/events.py`, `identity.py`, `__init__.py` -- actor and assignee types, runtime actor check, restated reason and comments, retirements
- [ ] `pm_ai/domain/payload_fields.py` -- `_kind` learns `PersonHandle`; person fields and named person-list members through parse; canonical form enforced; sentinel messages replaced
- [ ] `pm_ai/domain/event_entries.py`, `pm_ai/skills/registry.py` -- the `pm-ai` actor rule; `skill` field and its grammar
- [ ] `pm_ai/storage/service.py` -- envelope writes the handle text
- [ ] `pm_ai/core/normalize.py`, `pm_ai/app/pipelines.py`, `pm_ai/app/wiring.py` -- `own_handles` required and carried on `Daemon`, empty at the composition root with a comment saying why
- [ ] `pm_ai/connectors/gitlab.py`, `pm_ai/connectors/graph/__init__.py`, `pm_ai/domain/meetings.py`, `pm_ai/core/meeting_records.py` -- handles per the matrix; producer errors caught per row; attendee de-duplication; `attendee_count` stored and priced
- [ ] `tests/domain/test_handles.py` (new) and the listed test files -- one test per matrix row; connector-level tests for every commit, organizer and attendee row; fixtures switched to `pm-ai`
- [ ] `tests/architecture/test_handle_construction.py` (new) -- an AST scan for `PersonHandle(...)` or `dataclasses.replace` on one outside the producer's module, and for any call of `resolve_actor` in `pm_ai/`. The scan follows import aliases and attribute calls (`identity.resolve_actor`) and skips the definitions in `identity.py`
- [ ] `tests/architecture/test_guards_survive_o.py` -- the disjointness guard joins `CASES`

**Acceptance Criteria:**
- Given a GitLab commit and a Graph meeting harvested through `run_harvest`, when the lines are read back, then each `actor=` is an `email:` or `none:` handle and no line holds `actor_unresolved`.
- Given `own_handles` holding one handle and `ALIASES` replaced by a stand-in that fails if read, when `run_harvest` persists an event carrying that handle, then the line has `authored_by=pm_ai` and the alias stand-in was never read.
- Given `mentions=("u_1",)`, when it is encoded, then the encoder refuses naming `mentions`; and given a line with `p.mentions=u_1`, when it is decoded, then it is refused as corrupted naming `mentions`.
- Given a self-action entry with actor `test`, when it is built or a line holding it is parsed, then `MalformedEntry` names the actor; and given a `skill_invoked` entry without `skill`, or with `skill=post_comment`, then it is refused naming `skill`.
- Given `python -O` and a doctored handle-system set that shares a name with the scopeless set, when the module is imported, then the import is refused.
- Given a `PersonHandle` constructed, or a `resolve_actor` called, outside the allowed places, when the architecture test runs, then it fails naming the file and line.
- Given attendees `Dana@x.com` and `dana@x.com`, when the meeting record is written and read back, then it holds one handle and `attendee_count=2`.
- Given `uv run pytest`, then everything passes and the skip count stays at 24 (`EXPECTED_SKIPS`; measured 2026-10-11: 2938 passed, 24 skipped). This slice adds no skip, and none of the 24 skips names a module it creates.

## Design Notes

- **Why a type and one module.** Today two connectors call `resolve_actor` in three places (`gitlab.py:258`, `graph/__init__.py:899,1134`), each choosing its own system and key. A type only the producer builds turns a raw string into a mypy error, a runtime refusal, and a source-scan failure.
- **Where bot handles are stored is deferred** until the first enrolment that records a bot account. None does today: Graph and GitLab both sign in as the user. Until then `own_handles` is empty and mechanism 3 never fires, which is accurate.
- **Meeting cost reads the count, not the list.** `man_hour_cost` used `len(attendees)`. That under-counted every attendee pm-ai could not name, and it would under-count attendees left out for an unusable address. With `attendee_count` stored, the list says who and the count says how many.

## Verification

**Commands:**
- `uv sync --extra runtime` -- expected: success. Run it first.
- `uv run pytest -q` -- expected: all pass, 24 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept. Run it separately: `tests/architecture/test_layering.py` skips silently when the tool is missing.
