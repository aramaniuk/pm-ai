---
title: 'People on event-log lines are read as people, and the PM is one function'
type: 'feature'
created: '2026-10-11'
status: 'done'
baseline_commit: '57320f4adfbca18bdca441fb11ad5fafdb36c066'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** After `2n`, every person on an observed event's line is a handle `<system>:<handle>` that nothing turns back into a person. And "is this the PM" has three answers: `23c`'s Graph rows, the alias table, and the spoken-command check comparing a speaker label with the `pm_handle` setting. The architecture review of 2026-10-11 found that builders following those documents would disagree about the same line, and about who may give a spoken order.

**Approach:** Two functions in `domain` are the only way from a handle to a person (the architecture's rule that a person is resolved when a line is read, Andrei 2026-10-11): `person_for` maps a handle to a pm-ai person through the alias table, and `pm_identity(rows, aliased)` returns the handles that are the PM. One composition-root function reads the connector rows and calls it; the spoken-command check and `23c` take their answer from that function. `pm_handle` is removed (Andrei, 2026-10-11). Lands after `2n` and `8q`, before `23c`.

## Boundaries & Constraints

**Always:**
- **`person_for(handle: PersonHandle) -> Actor | None`.** It takes only `PersonHandle` (as `2n` names it) and returns the pm-ai person the alias table maps it to, or `None` for "no known person"; a reader given `None` shows the handle as written. After `2n` no `Actor` is written on an event-log line; the `Actor` this returns is a pm-ai person, and its `actor_id` is a pm-ai person id. Nothing rewrites a line, so an alias added later applies to every past line.
- **`graph_app` and `none` never map to a person.** `person_for` returns `None` for them whatever the table holds; it does not rely on `register_alias` having refused them.
- **A reader holding a `mentions` member parses it with `2n`'s parser before mapping.** Self-action lines (actor `pm-ai`) are never mapped; the reader tells the two cases apart by the entry's vocabulary.
- **`register_alias(handle: PersonHandle, person: Actor)`** keys the table by handle; refuses `graph_app` and `none` naming the system; refuses a handle already mapped to a different person, naming both. Correcting an alias belongs to the slice that saves the table. The table stays a module dict only tests fill.
- **One usable-user-id rule in `domain`:** a `user_id` that is not text, is blank, or that the producer refuses is absent; surrounding whitespace is stripped before producing. The row key's spelling lives in `domain`; `8q`'s `recorded_user_id` delegates to this rule and `connectors.graph.USER_ID_KEY` is `domain`'s constant.
- **`pm_identity(rows, aliased)`** returns `graph:<user_id>` for every row whose `system` is exactly `"graph"`, enabled or disabled, so disabling a row does not unmark past lines, plus every handle in `aliased`. Any other row adds nothing.
- **`aliased` is empty until the alias table is saved.** No row names the PM's person and a test-filled dict is not a record, so the app passes an empty set; the slice that saves the table fills it and changes no caller.
- **`read_pm_identity(storage)` in `app` is the one caller.** It reads every row once at the moment of asking and returns the identity plus the rows it could not use (unreadable, or enabled with no usable `user_id`), so `23c`'s sign-in line and its marking come from one read at one instant. `8q`'s row writes go through `write_artifact`, which replaces a file whole, so a read during a write sees a whole row. `23c` and the transcript pipeline call `read_pm_identity`, never `pm_identity`.
- **The spoken-command speaker check asks the identity.** `Utterance` gains `speaker: PersonHandle | None = None`; both transcript adapters leave it `None`, because a speaker label is a display name, never a handle. The identity is read once per ingestion and passed to `extract` as a frozen set; an utterance is the PM's only when its handle is in it. An empty identity matches nobody. Today every transcript command therefore stages until a slice gives an authenticated transcript the speaker's Graph user id.
- **`pm_handle` is removed** (Andrei, 2026-10-11): the config key (a `config.toml` still setting it is refused as an unknown key, like any other), setup's question, doctor's `pm_handle` check, the daemon property and its port, `extract(pm_handle=)`, and their tests. No `config.toml` existed on the owner's machine (checked 2026-10-11).

**Ask First:** Nothing.

**Never:**
- No saving, seeding or correcting of the alias table; no PM person id; no display names on the dashboard.
- No change to the producer, the handle systems or the line grammar (`2n`, `2m`).
- No change to `23c`'s or `11b`'s spec or to rendering.
- No parsing of a speaker label as a handle.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Known person | alias `graph:u_1` → person P | `person_for` returns P | N/A |
| No alias | `email:dana@example.com`, nothing registered | `None`; the reader shows the handle | N/A |
| Alias added later | a handle mapped after the line was written | the unchanged line reads as the person | N/A |
| Bot and absence in the table | `graph_app:a_1` and `none:` written into the table directly | `None` for both | N/A |
| Registering bot or absence | `register_alias` with `graph_app` or `none` | nothing stored | refused, naming the system |
| Remapping | a handle mapped to P, registered again to Q | P kept | refused, naming P and Q |
| One Graph row | `system: "graph"`, `user_id: " U1 "` | `{graph:u1}` (stripped; producer's lower case) | N/A |
| Disabled row | `enabled: false`, `user_id: U1` | `{graph:u1}` | N/A |
| Two tenants | two Graph rows, two ids | both handles | N/A |
| No usable user id | absent, `""`, `"  "`, a number, or a value the producer refuses | row adds nothing; listed as unusable if enabled | no error |
| System not exactly `graph` | `"Graph"`, `"graph "`, `"gitlab"` with `user_id` | adds nothing | N/A |
| No Graph row | none, or none Graph | empty set | N/A |
| Alias table filled in memory | the PM's handle and another mapped to one person | not consulted; `aliased` empty (flips when the table is saved) | N/A |
| Speaker is the PM | GRAPH transcript, utterance handle `graph:u1`, one Graph row with `U1`, auto-executable verb | executes | N/A |
| Provider-cased handle | utterance handle produced from `U1` | matches; executes | N/A |
| Another person | utterance handle `graph:u2` | stages | N/A |
| Label only | GRAPH transcript, any label, no handle | stages | N/A |
| Nothing enrolled | no Graph row, utterance handle present | stages | N/A |
| Old setting | `config.toml` with `pm_handle = "x"` | load refused naming the key | unknown-key refusal |

</frozen-after-approval>

## Code Map

Line numbers measured 2026-10-11; `2n` and `8q` edit several of these files first.

- `pm_ai/domain/identity.py:311-361` -- `Actor` (316), `ALIASES` keyed `(system, handle.lower())` (344), `register_alias` (347), `resolve_actor` (351), `UNRESOLVED_ACTOR` (311) and `UNRESOLVED` (337): `2n` leaves them with no caller on the write path, and this slice removes all three — `person_for` replaces them. The comment at 341-343 claims Tier-1 persistence: correct it
- `pm_ai/domain/handles.py` (`2n`) -- `PersonHandle`, the producer, the parser. `.importlinter`'s `domain-imports-nothing` forbids `pm_ai` imports from `domain`, so rows arrive as plain mappings
- `pm_ai/connectors/graph/__init__.py:216-220` -- row keys; `8q` adds `USER_ID_KEY`
- `pm_ai/core/connector_enrolment.py` -- `8q`'s `recorded_user_id`; `RESERVED_ROW_KEYS` (121)
- `pm_ai/storage/service.py:1374` -- `write_artifact` publishes by `os.replace`
- `pm_ai/app/wiring.py:1382` -- `_enrolled_configurations`, every readable row (or `4n`'s reader); `pm_handle` property (211-220)
- `pm_ai/core/extraction.py:33,66` -- `extract(..., pm_handle=)`, the label comparison; `pm_ai/core/command_authorization.py:16` `classify`
- `pm_ai/domain/transcripts.py:27` -- `Utterance`; adapters build it at `pm_ai/connectors/transcripts/graph.py:21`, `manual.py:28`
- `pm_ai/app/pipelines.py:267` -- passes `daemon.pm_handle`; `pm_ai/ports/__init__.py:866` declares it
- Removal: `pm_ai/core/config.py:214,344,353`; `pm_ai/platform/doctor.py:431-440`; `pm_ai/surfaces/cli/dispatch.py:1342,1411`
- Tests: `tests/slice/test_transcript_slice.py:86,96,105,114,128,151,163,177`; `tests/slice/test_r4_gate_fixes.py:342,356` (GRAPH `Utterance`s); `register_alias` callers `tests/slice/test_vertical_slice.py:124`, `tests/architecture/test_domain_invariants.py:535`; removal `tests/architecture/test_doctor.py:718`, `tests/slice/test_first_run.py:460-464`, `tests/core/test_config.py`

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/domain/identity.py` (or `handles.py`) -- remove `resolve_actor`, `UNRESOLVED_ACTOR` and `UNRESOLVED` and update their remaining test callers; `person_for`, the new `register_alias`, `pm_identity(rows, aliased)`, the usable-user-id rule and key constant
- [x] `pm_ai/core/connector_enrolment.py`, `pm_ai/connectors/graph/__init__.py` -- delegate to `domain`'s rule and constant
- [x] `pm_ai/app/wiring.py` -- `read_pm_identity(storage)`: one read, identity plus unusable rows; `pm_handle` property gone
- [x] `pm_ai/domain/transcripts.py`, `pm_ai/core/extraction.py`, `pm_ai/app/pipelines.py`, `pm_ai/ports/__init__.py` -- `Utterance.speaker`; `extract` takes the frozen identity; the pipeline reads it once per ingestion
- [x] `pm_ai/core/config.py`, `pm_ai/platform/doctor.py`, `pm_ai/surfaces/cli/dispatch.py` -- remove `pm_handle`, setup's question and doctor's check
- [x] `tests/conftest.py` -- an autouse fixture restoring `ALIASES` after each test: the dict lives for the whole process and `register_alias` now refuses a remap, so one test's alias would fail or colour the next
- [x] `tests/domain/test_people.py` (new, name suggested) -- one test per matrix row above the speaker rows; `USER_ID_KEY is` `domain`'s constant; `recorded_user_id` delegates (a value only `domain`'s rule refuses is refused by both)
- [x] `tests/slice/test_transcript_slice.py` -- the speaker rows through the pipeline with one enrolled Graph row; the irreversible-verb and unregistered-verb tests give their utterances the PM's handle so they still test the verb; both adapters leave `speaker` `None`
- [x] `tests/architecture/test_people_readers.py` (new) -- AST scan over `pm_ai/`, not `tests/`, resolving import aliases and `getattr`: `register_alias` the one writer of `ALIASES`, `person_for` its one reader; `pm_identity` called only by `read_pm_identity`; subscript or `.get` reads of the user-id key only in `domain`'s rule, `connector_enrolment` and `read_pm_identity`
- [x] removal tests -- `test_doctor.py:718`, `test_first_run.py:460-464`, `test_config.py` rewritten; `pm_handle` asserted refused as unknown

**Acceptance Criteria:**
- Given two Graph rows saved under `connectors/`, one with `enabled: false`, when `read_pm_identity` is called, then both handles come back; and given a third row added while the daemon runs, then the next call includes it.
- Given an enabled Graph row with no `user_id`, when `read_pm_identity` is called, then that row is listed as unusable in the same result as the identity.
- Given `pm-ai setup` on a fresh home, when it runs, then it asks no `pm_handle` question; and given `pm-ai doctor`, then no line mentions `pm_handle`.
- Given a module in `pm_ai/` that reads `ALIASES`, calls `pm_identity` or reads the user-id key outside the allowed places, when the architecture tests run, then they fail naming the file and line.

## Design Notes

- `pm_identity` takes rows, not user ids, so which rows count (Graph, enabled or disabled) lives in one function.
- `11b` lands after this slice and must keep the speaker comparison through the identity: a Graph transcript stages because its utterances carry no handle, not because of a check on the source.

## Verification

**Commands:**
- `uv sync --extra runtime` -- expected: completes; needed before the suite imports the runtime adapters
- `uv run pytest -q` -- expected: all pass; skips stay at `EXPECTED_SKIPS = 24` (`tests/conftest.py:61`; 2938 passed, 24 skipped on 2026-10-11)
- `uv run pytest tests/architecture/test_people_readers.py -rs -q` -- expected: its tests reported passed, none skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept; run on its own, since the layering test skips silently when it is missing

## Suggested Review Order

1. The two ways from a handle to a person: [`person_for`](../../../../pm_ai/domain/handles.py#L400) and [`register_alias`](../../../../pm_ai/domain/handles.py#L371) (refuses `graph_app`/`none` and a reassignment).
2. Who the PM is: [`usable_user_id`](../../../../pm_ai/domain/handles.py#L420), the one user-id rule, and [`pm_identity`](../../../../pm_ai/domain/handles.py#L437); 8q's [`recorded_user_id`](../../../../pm_ai/core/connector_enrolment.py#L632) delegates, and [`record_user_id`](../../../../pm_ai/core/connector_enrolment.py#L535) now refuses what that rule reads as absent.
3. The one caller: [`read_pm_identity`](../../../../pm_ai/app/wiring.py#L1471) — one read, identity plus unusable rows.
4. The spoken-command check: [`extract`](../../../../pm_ai/core/extraction.py#L34) compares `Utterance.speaker` with the identity; the pipeline reads it once per ingestion in [`pipelines.py`](../../../../pm_ai/app/pipelines.py#L273).
5. `pm_handle` removed: config, doctor, the daemon property, and setup's [`_setup_config`](../../../../pm_ai/surfaces/cli/dispatch.py#L1377), which now asks only what a partial file leaves unset.
6. Tests: `tests/domain/test_people.py`, `tests/slice/test_read_pm_identity.py`, and the AST scan `tests/architecture/test_people_readers.py`.
