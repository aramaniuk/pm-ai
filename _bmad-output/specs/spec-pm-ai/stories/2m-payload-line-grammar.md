---
title: 'Event-log lines carry payload fields through one encoder and one decoder'
type: 'feature'
created: '2026-10-09'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The event log's writer and its readers disagree about how an event's details are named on a line, and nothing checks that they agree. The writer puts details under a `p.` prefix; the dashboard's message section reads them without it, so the first real Teams message would render as an actor and nothing else, with no error, because a missing detail reads as "not recorded". The dashboard tests pass only because they build entries by hand with the wrong names.

Two more gaps block the Teams message slice: a list detail (the people a message mentions) and a person detail (a work item's assignee) are written as Python's internal text, which nothing can read back; and the check that every piece of outside text is declared trusted or untrusted refuses a list that *is* declared and accepts one that is not.

The architecture settled all of this (AD-27 on 2026-09-27; AD-48's list rule on 2026-10-08) and names this slice as the owner of the enforcement (AD-27 / AD-48, 2026-10-09). Two tightenings from the architecture gate ride with it (AD-27 / AD-48, 2026-10-10): a person pm-ai could not name keeps the handle it came with on the line, and a list declaration is read as a type, never as text.

**Approach:** One encode/decode pair in the domain turns a payload into its `p.` line fields and back, used by the writer and the dashboard; lists get the comma-joined form; the declaration check learns the list rule; a pin test freezes the grammar and a round-trip test runs every payload through the pair.

## Boundaries & Constraints

**Always:**
- **One pair, used everywhere.** The writer builds payload fields only through the encoder; no reader in `pm_ai/` looks a payload field up by its literal line name — it decodes and reads the attribute, so a misspelled name is caught before anything runs instead of reading as empty. A test scans the source for literal lookups.
- **The line grammar only grows** (AD-27). No field is removed, renamed, retyped or given a new meaning; an added field is optional; a required field never grows. A reader treats a missing field as "not recorded" and ignores a field it does not know. A line is never re-rendered. **One dated exemption** (AD-27, 2026-10-09): no machine has written a real event-log line yet, so the Python-internal spelling of the assignee and of lists is corrected now, before any line exists.
- **A person on a line is the id alone** (AD-34). The assignee is written as its actor id; a display name is an alias, never reaches a line, and reading the line back gives a person with that id and no name — the one deliberate loss the round trip allows.
- **A person pm-ai could not name keeps the handle** (AD-27 / AD-34, 2026-10-10). When no alias entry matches the handle a connector saw — a commit email, a tenant account — the line carries that handle under the reserved spelling `unresolved:<system>:<handle>` (for example `unresolved:gitlab:dana@example.com`), never the bare `actor_unresolved` sentinel, so the line keeps what a later alias entry resolves and a rebuild can re-resolve it. A member of an id list (`mentions`) that could not be resolved is written the same way, so the sender of a message and a mention of the same person are joinable on the line. Reading it back gives a person whose id is that spelling, and the encoder refuses the bare sentinel in a payload field, naming the field. Producing the spelling is the connector's job when it resolves the handle; this slice makes the line carry it.
- **Absent and empty differ.** A detail that was not recorded is left off the line, as today. A list defaults to "not recorded" ("the connector never asked"); an empty list is written as a present, empty value.
- **Lists, as AD-27 states:** elements joined by `,`; a `,` or `\` inside an element is escaped with `\`; an empty element is refused at encode. The list layer sits *inside* the line layer: the joined text is one field value the line quotes and escapes by its own rules, and a reader undoes the line layer first, then splits. A trailing separator, a dangling `\`, or a `\` before any other character is refused as a corrupted line, naming the field.
- **Counts round-trip as numbers:** the encoder refuses a negative count, naming the field; the decoder accepts digits only.
- **A line is refused, naming the field,** when a detail the payload cannot be built without is missing, or when the same detail appears twice; a detail with a default takes its default. Decoding an entry whose kind carries no payload (a self-action) is refused naming the kind — never a stray lookup error, never a silent nothing.
- **Lists are declared, trusted only, for now** (AD-48, 2026-10-08). A list is exactly "a tuple of text, or not recorded"; declared any other way — a Python list, a required tuple, any other container — it is refused at import like an undeclared field. A trusted list carries its reason; an untrusted list is refused at import until a slice brings a real one. The check keeps raising a typed error, never asserting.
- **A list declaration is read as a type, not as text** (AD-48, 2026-10-10). The check resolves every annotation with `typing.get_type_hints` and compares the resolved type, so `Optional[tuple[str, ...]]`, `None | tuple[str, ...]` and `typing.Tuple[str, ...] | None` are one declaration, and an annotation written as a string is never compared as text — the same way the text check already reads `Optional[str]` and `str | None` as one.
- **The message payload gains `mentions`**, a list of Graph user ids defaulting to "not recorded", declared trusted for the reason the spine gives (Microsoft generates the ids; nobody types them). The Teams message slice fills it and changes no grammar.
- **The pin test is a literal table**, not derived from the classes at run time: every payload class's field names, types, whether each is optional and its default; every self-action kind's required fields; every member of both vocabularies.
- **The dashboard's message line reads channel and excerpt through the decoder**, its tests build entries through the encoder, and its rendered text is unchanged by this slice (`23c` re-pins the full-day golden when it reorders the section).
- **The versioning comment in `event_entries.py` states the settled rule**: additive only, no version marker (2026-09-27).

**Ask First:** Nothing — every rule here was adopted in the spine on 2026-09-27 / 2026-10-08. The assignee rule follows from AD-34 (names were kept off lines when `mentions` was decided; the alias table is where a name comes from), so the open AD-48 question of how a nested name is classified does not touch the event log.

**Never:**
- No version marker on a line or segment; no dated grammar table.
- No change to envelope fields, line quoting, meeting records or the disclosure ledger.
- No untrusted list declaration, no sanitizer walking a list, no change to how nested types are declared.
- No Teams fetching, no filling of `mentions`, no refusal of mentions without ids: the Teams message slice.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Text fields | `CommitPayload(sha, message, branch)` | `p.sha`, `p.message`, `p.branch`; decodes equal | N/A |
| A detail not recorded, text or list | `branch=None`, `mentions=None` | left off the line; decodes as not recorded | N/A |
| A person | `WorkItemPayload(assignee=Actor("u_1", "Dana"))` | `p.assignee=u_1`, no name on the line; decodes `Actor("u_1")`, no name | N/A |
| A person pm-ai could not name | `assignee=Actor("unresolved:gitlab:dana@example.com")`; `mentions=("u_1", "unresolved:graph:dana@example.com")` | `p.assignee=unresolved:gitlab:dana@example.com`; the list member spelled the same; decodes an `Actor` whose id is that spelling, and the tuple | `assignee=Actor("actor_unresolved")` refused at encode; typed error, names the field |
| A count | `ReviewPayload(comment_count=0)` | `p.comment_count=0`; decodes `0` as a number | N/A |
| A count not digits, or negative | hand-edited `p.comment_count=many` or `-1`; `comment_count=-1` at encode | refused, naming the field | corrupted-line refusal at decode; typed error at encode |
| A list of two | `mentions=("u_1", "u_2")` | `p.mentions=u_1,u_2`; decodes the tuple | N/A |
| An element with `,` or `\` | `("a,b", "c\d")` | `a\,b,c\\d` inside the field; round-trips | N/A |
| An element the line must quote | `("a b",)` | quoted by the line layer, split after unquoting; round-trips | N/A |
| An empty list | `mentions=()` | `p.mentions=` present and empty; decodes `()` | N/A |
| An empty element | `("",)` | refused at encode | typed error, names the field |
| A hand-edited list | `u_1,` / `u_1\` / `u_1\x` | refused, naming the field | corrupted-line refusal |
| The same detail twice | a line carrying `p.channel` twice | refused, naming the field | corrupted-line refusal |
| A field the class does not know | line carries `p.later=1` | ignored; decode succeeds | N/A |
| A required detail missing | message line with no `p.channel` | refused, naming the field | corrupted-line refusal |
| A kind with no payload | a self-action entry handed to the decoder | refused, naming the kind | typed error |
| Bytes on disk | a message line with `("u_1", "u_2")`; one with `()` | pinned byte for byte, beside today's commit line | N/A |
| A message on the dashboard | entry built through the encoder, in the window | `- **08:15** u_dana in #payments — <excerpt>` | N/A |
| Round trip | every payload class, every field set | decodes equal, after a person is reduced to its id | N/A |
| A trusted list with a reason | `MessagePayload.mentions` as declared | module imports | N/A |
| An untrusted list | a test alters the declarations to list it untrusted, also under `python -O` | refused at import | declaration refusal |
| An undeclared or mis-shaped list | a list in neither record; a Python list; a required tuple; another container | refused at import | declaration refusal |
| Three spellings of one list declaration | a doctored registry declares `mentions` as `Optional[tuple[str, ...]]`, then `None | tuple[str, ...]`, then `typing.Tuple[str, ...] | None` | each accepted as the one list declaration; module imports | N/A |
| Grammar drift | a field renamed, retyped, removed or made required; a default changed; a vocabulary member removed; a required field added | pin test fails naming the change | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/domain/events.py:84-137` -- the eight payload classes; `MessagePayload` (113) gains `mentions: tuple[str, ...] | None = None`; `WorkItemPayload.assignee: Actor | None` (89). `Actor` is `pm_ai/domain/identity.py:316` (`actor_id`, `display_name`); `UNRESOLVED_ACTOR = "actor_unresolved"` (311) and `UNRESOLVED` (337) are the bare sentinel the line never carries; `resolve_actor` (351) is where a connector's handle becomes one or the other, and nothing in `pm_ai/` sets `assignee=` today
- `pm_ai/domain/events.py:208-292` -- `UNTRUSTED_TEXT`, `TRUSTED_TEXT`; the "known limit" docstring (279-291) narrows to nested types
- `pm_ai/domain/events.py:295-308` -- `_is_text`, reading the resolved hint by origin and args; needs a sibling accepting exactly `tuple[str, ...] | None` the same way. `get_type_hints` is already called once at 347, so the three spellings resolve to one `tuple[str, ...]`-and-`None` union before any comparison. In `_assert_payload_text_is_declared` (311-417) the `not_text` refusal (373-379) rejects a declared list and the `unaccounted` sweep (392-401) lets an undeclared one through. Raises `MissingSanitizableDeclaration`, never asserts; `list[str]`, a required tuple and any other container raise the same
- `pm_ai/domain/event_entries.py:62-71` -- the stale versioning comment; `SELF_ACTION_FIELDS` (101); `ESCAPES`/`render_value`/`scan_fields`/`_read_value` (182-415) are the line layer; `MalformedEntry` (198) is the corrupted-line refusal; `EventEntry` (241) is what the decoder takes
- `pm_ai/domain/payload_fields.py` (new, name suggested) -- `encode_payload(payload) -> tuple[tuple[str, str], ...]`, `decode_payload(entry) -> object`, the `p.` prefix spelled once, the list join/split, `Actor` written as `actor_id`. On a `SelfActionType` entry `decode_payload` raises its own typed error naming the category — never `KeyError`, never `None`. May import `events` and `event_entries` (the reverse is a cycle). Do not extend `pm_ai/domain/__init__.py`; its docstring says why
- `pm_ai/storage/service.py:275-296` -- `_payload_fields`, `str(value)` per non-`None` field, so a tuple and an `Actor` become their repr; called at 1631 in `_append_batch`
- `pm_ai/core/rendering.py:655-665` -- `_signal_line`, the unprefixed lookups; `SIGNAL_CATEGORIES` (228). `core` may import `domain`
- `tests/core/test_rendering_sections.py:84-111`, `tests/core/test_project_rendering.py:112-133` -- two hand-written `message()` builders with unprefixed names. `tests/core/goldens/dashboard_full_day.md:9-10` is the only golden; the project tests compare text in-test
- `tests/domain/test_sanitizable_declarations.py:73-113` -- `_instead_of_decision`/`_refusal` doctor the registry. `:416-423` pins `assignee` undeclared; `:435` pins a container as outside the rule (inverted here); `_run_optimized` (454) and `:534`, the `-O` doctored-registry case to copy
- `tests/architecture/test_doctor.py:411-447` -- `test_the_environment_is_read_in_exactly_one_place`, the AST scan to copy for the literal-lookup test; `tests/domain/test_sanitizable_declarations.py:514-531` scans by AST too
- `tests/core/test_payload_serialisation.py` -- storage-level round trip; reads `p.message` literally to pin bytes, which a writer test may do
- `tests/architecture/test_sanitize_boundary.py:931-989` -- pins a commit line byte for byte; its docstring (937-939) calls the versioning clause unmet
- `tests/architecture/test_guards_survive_o.py:39-136` -- `CASES`, the roster of import-time guards. This slice adds no module-level guard (the list rule lives inside the existing one)

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/domain/payload_fields.py` (new) -- encoder and decoder: list join/split with escaping, counts (negative refused at encode, digits only at decode), `Actor` as its id (the `unresolved:<system>:<handle>` spelling passes through unchanged; the bare `actor_unresolved` id is refused), a repeated key refused, a self-action entry refused by name
- [ ] `pm_ai/domain/events.py` -- type check accepting exactly `tuple[str, ...] | None` as a list, compared on the resolved hint, never on annotation text; the list rule in `_assert_payload_text_is_declared` (declared or refused; untrusted refused; trusted with reason); `MessagePayload.mentions`; the "known limit" docstring narrowed to nested types
- [ ] `pm_ai/domain/event_entries.py` -- replace the comment at 62-71 with the settled rule
- [ ] `pm_ai/storage/service.py` -- `_append_batch` builds payload fields through the encoder; `_payload_fields` goes
- [ ] `pm_ai/core/rendering.py` -- `_signal_line` decodes the entry and reads `channel`/`excerpt` off the payload
- [ ] `tests/core/test_rendering_sections.py`, `tests/core/test_project_rendering.py` -- both `message()` builders build their fields through the encoder; the full-day golden and the project tests' expected text unchanged by this slice
- [ ] `tests/domain/test_payload_fields.py` (new) -- one test per matrix row, plus the round trip over every class in `PAYLOAD_FOR` with every field set, comparing after an `Actor` is reduced to its `actor_id`
- [ ] `tests/domain/test_entry_grammar_pins.py` (new) -- the literal table: payload fields with type, optionality and default; `SELF_ACTION_FIELDS`; both vocabularies
- [ ] `tests/domain/test_sanitizable_declarations.py` -- the list cases (trusted, untrusted, undeclared, `list[str]`, required tuple, other container); one test declaring the list under each of the three spellings in a doctored registry and importing; a `_run_optimized` case that doctors the registry with an untrusted list and expects the refusal; `:435` inverted
- [ ] `tests/architecture/test_payload_lookups.py` (new) -- scans `pm_ai/` by AST for the literal `"p."` prefix and the payload field names used as subscripts or `.get` keys outside `pm_ai/domain/payload_fields.py`
- [ ] `tests/core/test_payload_serialisation.py`, `tests/architecture/test_sanitize_boundary.py` -- a `mentions` list through `persist_events` and back; a message line with a two-element list and one with an empty list pinned byte for byte beside the commit line; the docstring at 937-939 corrected
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark resolved: the versioning entry (`:302-304`), the "known debt" paragraph (`:275-284`), the machinery entry (`:860-862`), the dashboard message-line entry (`:864-866`), the stale-docstring entry (`:942-944`); `:868-870` stays with `33d`

**Acceptance Criteria:**
- Given a `message_posted` event with a channel, an excerpt and two mentions, when it is persisted, parsed back and decoded, then the payload equals what the connector supplied and the dashboard line shows channel and excerpt.
- Given a work item whose assignee carries a display name, when it is persisted and decoded, then the line holds the id alone and the decoded assignee has that id and no name.
- Given a work item whose assignee is `Actor("unresolved:gitlab:dana@example.com")` and a message mentioning `unresolved:graph:dana@example.com`, when both are persisted and decoded, then each line carries that spelling and each decoded person has it as its id; and given `Actor("actor_unresolved")` as the assignee, when it is encoded, then the encoder refuses naming `assignee`.
- Given the pin table, when any payload field's name, type, optionality or default, any self-action required field or any vocabulary member is changed without editing the table, then the pin test fails and names it.
- Given `python -O`, when a list is declared untrusted in a doctored registry, then import is refused with `MissingSanitizableDeclaration`.
- Given the source tree, when a reader outside `pm_ai/domain/payload_fields.py` looks a payload field up by its literal name, then the architecture test fails naming the file and line.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09).

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09)
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
