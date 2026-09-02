# Multi-lens review — the twelve wave-1 specs

Run 2026-09-02 against `4fb8bf5`. Lenses: adversarial, edge-case-hunter,
verification-gap. Content class: docs (behaviour-defining specs).
`persistent_facts` resolved empty — no `project-context.md` exists in the repo.

Each lens ran in its own agent with no sight of the others, deliberately: the
specs' author ran the review, and independent readings are the only thing that
offsets that.

**Findings: 28 adversarial, 89 edge-case, 23 verification-gap.** Overlap between
lenses is kept, not deduped — a finding two lenses reached independently is
stronger evidence than either alone, and four did.

Raw verification-gap JSON, with per-finding `evidence` fields citing what was
actually read:
`~/.claude/projects/-Users-andreiramaniuk-3alab-pm-ai/4b602ead-4703-4db1-adc9-e93b0769b3d7/tool-results/toolu_01Fh2KEG4tCC98fPirGpn1na.json`

> **Numbering note, added after this review ran.** Acting on it split the
> original `33b` into `33b` (calendar fetch) and `33c` (calendar mapping), and
> renumbered wave 2's messages and transcripts slices to `33d` and `33e`. Where
> this report says `33c` it means what is now `33d`, and `33d` means `33e`. The
> report is left otherwise as written, as the record of what was found.

## Verdict

**The set is not ready to implement.** Five specs cannot be built as written —
they depend on interfaces that do not exist, or specify guards the repo's own
gates forbid. Separately, every spec in the set carries a verification block
that would fail the suite, and the three architecture tests the wave claims to
un-skip would all pass while proving nothing.

The failure pattern is consistent and worth naming: the specs are accurate
wherever they cite something I opened, and wrong wherever they reason from the
architecture documents. Every finding below in the first two sections is of the
second kind.

## A. Blocks implementation — the spec cannot be built as written

**A1. `StoragePort` declares neither `write_artifact` nor `read_artifact`.**
`ports/__init__.py:286-314` has neither method, and no slice adds them. But 4a,
8b, 11a, 22a and 23b all specify `core` modules performing artifact I/O through
that port. As written the implementer must either import `pm_ai.storage` from
`core` (breaks layering) or type the dependency `Any` (the exact defect story 1k
removed). *Fix:* a task on 11a — the first slice to need it — declaring both
methods on `StoragePort`, as story 2h did for the event-log methods; then 4a,
8b, 22a and 23b depend on 11a.

**A2. `pm-ai` cannot run once on a clean machine.** `entry.main()` calls
`wiring.build()`, which requires a project id and eagerly resolves it
(`wiring.py:99-104`); an unregistered project raises `UnknownProject`
(`paths.py:552-558`). No wave-1 slice delivers `pm-ai project add` or a project
registry. So every subcommand dies before dispatch — including `doctor`, which
4c's own Always says must survive a broken environment. *Fix:* either 4c owns
`project add`, or the wave is explicitly blocked on the slice that does; plus a
4c matrix row for `doctor` on a machine with no registered project.

**A3. `pm-ai key enrol` has no legal route to a keychain.** `enrol(keychain, *,
key_name)` needs a `KeychainPort`. `Daemon` (`wiring.py:36-44`) exposes storage,
crypto, skills, connectors, transcripts, meetings, scope and pm_handle — no
keychain. And `surfaces.cli` cannot construct one: `pm_ai.platform.keychain`
reaches `keyring`, which the `os-behind-platform` contract
(`.importlinter:115-131`) forbids `pm_ai.surfaces` from reaching, indirectly
included. *Fix:* a 4b task adding `keychain: KeychainPort` to `Daemon`, passing
the adapter `build()` already constructs at `wiring.py:115`.

**A4. `parse_goals(raw: bytes | None) -> dict[str, Goal]` cannot construct a
`Goal`.** `Goal.scope` is required, deliberately (`goals.py:45`). 22a never
mentions `scope` in any clause, matrix row or criterion. The implementer will
default it inside the parser — a caller's decision taken by a parser, re-opening
the AD-38 hole the required field closed. *Fix:* `parse_goals(raw, *, scope)`,
with an Always clause stating the scope comes from the tree the file was read
from, never from the file — the rule 11a already states for `Meeting.scope`.
*Reached independently by all three lenses.*

**A5. 8c's completeness guard is forbidden by an existing test.** The matrix
specifies `AssertionError` raised at import.
`tests/architecture/test_guards_survive_o.py:174-181` walks every `.py` under
`pm_ai/` and fails on any `ast.Assert` node — story 1l converted all ten such
guards to typed raises so `python -O` cannot strip them. *Fix:* a typed domain
error raised from an `if`, plus a subprocess `-O` case as 1l's other guards
have. Note the matrix row was copied from 2c, whose own matrix is stale on this
point — worth a separate correction.
*Reached independently by adversarial and verification-gap; verified directly.*

**A6. 8c names no carrier for the sanitized pair.** "Carry the result forward"
has nowhere to go: `NormalizedEvent` is frozen with `payload: object` and a
`__post_init__` type check (`events.py:156-180`), every payload is
`frozen=True, slots=True`, and `persist_events(events, *, scope)` accepts
nothing else. No task changes any signature. The implementer will reach for
`object.__setattr__` or re-derive `for_model` inside storage. *Fix:* name the
carrier in the tasks — a `sanitized` mapping parameter on `persist_events`, or a
`SanitizedEvent` wrapper — and fix it in a matrix row.

## B. Would ship something wrong or unsafe

**B1. I inverted the meaning of `scope_model.py:1052`.** 22a's Intent says
`strategic_goals.md` is "declared Tier-1 in the personal and committed trees".
Line 1052 is a member of `PERSONAL_SUBJECT_ARTIFACTS`, whose comment at
`:1045-1047` states the property is that **no committed scope holds it**. The
file is declared once, personal-only. An implementer following my sentence would
look for or create a project-scope copy — writing personal career goals into a
git-committed employer repository. This is the most consequential error in the
set.

**B2. The project-scope dashboard is incoherent.** Goals live only in the
personal scope; AD-25 forbids a project render reaching it. So a
`--scope project:alpha` dashboard must render a 3-Tier section that is
structurally always empty, whose empty string is "No strategic goals declared" —
false whenever the personal file has goals, and forbidden by 23a's own "nothing
is invented" rule. The alternative implementation reads the personal file and
breaches AD-25 in the very slice whose test claims to prove the wall.
*Fix:* the project render's 3-Tier section states the wall
("strategic goals are personal-scope"), a computed claim about the boundary
rather than about the goals.

**B3. Enrolling a second connector destroys the first's credential.**
`private/config.json` is one sealed file and `write_artifact` replaces files
whole. 8b specifies a write, not a read-modify-write, and has no matrix row for
an existing occupant. *Fix:* a row requiring existing credentials be preserved,
asserted.

**B4. Wave 1 has two permanently empty CAP-9 sections, not one.** Proactive
Enablement reads `MESSAGE_POSTED`, which arrives with 33c in wave 2 — 33b's
`emits()` is exactly `{CALENDAR_EVENT_HELD}`. So the spec set, and
`prototype-path-2026-09-01.md` before it, both claim one honest gap when there
are two. 23a's "full day" golden fixture tests a state wave 1 cannot produce.
This misstates what the end-of-wave-1 reassessment will be looking at.

**B5. "No meetings on your calendar today" is false by mid-afternoon.** 23a
excludes ended meetings from Time-Critical, so a day whose meetings have all
finished renders the no-meetings string — a claim about the world the code did
not compute. The exclusion is also an unrecorded deviation from the design doc,
which says "`meetings/` where `start` falls today". *Fix:* two distinct computed
statements, and record the filter as a deviation.

**B6. "No plaintext credential on disk under any circumstance" is false.** With
`PM_AI_DISABLE_ENCRYPTION` set, `build()` installs `PlaintextCrypto`
(`wiring.py:114-115,153-155`) and `write_artifact` writes `private/config.json`
in plaintext. 8b's own Always ("every write goes through `write_artifact`", "this
story does not consult the toggle") produces exactly what its Never forbids. An
absolute that is known false teaches the reader these clauses are aspirational —
which is the 8c defect in miniature.

**B7. Nothing sets 600 on `connectors/<name>.json`.** `_replace`
(`service.py:884-905`) passes a mode only when `is_encrypted(path)`, and
`connectors/` is declared `encrypted=False` (`scope_model.py:451`), so the file
lands at the umask — typically 0644. 8b asserts 600, changes no storage code,
and forbids the caller deciding. *Fix:* a declared restricted mode honoured by
`write_artifact`.

**B8. Graph timestamps will refuse every event.** `calendarView` returns
`{dateTime, timeZone}`, usually the mailbox zone, not aware UTC. `Meeting.start`
and `validate_occurred_at` both require aware UTC. 33b has no conversion step
and no matrix row. *Fix:* convert at the boundary, or send
`Prefer: outlook.timezone="UTC"`, stated explicitly.

**B9. "Today" has no timezone owner.** `Meeting.start` is UTC,
`render_dashboard(..., now)` takes a UTC instant, and `for_day` takes an
unspecified date — so which meetings are "today" for a PM outside UTC is decided
by whichever slice is written first. 23a declares its Ask First as "Nothing".
*Fix:* `for_day(day, *, tz)`, the display timezone owning the day boundary, and
a matrix row for a 23:30-local meeting that is tomorrow in UTC.
*Reached independently by adversarial and edge-case.*

**B10. The transcript path is a second unsanitized boundary.** 8c fixes
`run_harvest`. `run_transcript_ingestion` (`pipelines.py:51,64`) builds
`DecisionPayload` content on a different path that 8c does not touch. Fixing one
of two boundaries under a story titled "sanitization binds at the boundary"
leaves the same false assurance in place elsewhere.

**B11. Real Graph ids may be unwritable.** 11a routes meeting ids through
`write_artifact`'s `name` validation. Graph meeting ids are long base64url
strings, sometimes dot-leading. If `_capture_name` refuses them, 33b's first
writer is refused on real data. *Fix:* a stated safe-name encoding preserving
the id inside the record.

**B12. An emitted event can outlive a failed record write.** 33b emits
`CALENDAR_EVENT_HELD` citing `meeting:<id>`. If the `meetings/` write fails, the
citation root does not exist and the AD-33 citation is unresolvable. *Fix:*
record first, event only on success.

**B13. Re-harvest destroys hand-edits.** `meetings/` is Tier-1 and
hand-editable by design (AD-3); overlapping harvest windows re-fetch and
`write_artifact` replaces whole. 33b's "re-write of an existing id" row says the
second write simply replaces. *Fix:* refuse or merge over a record edited since
it was written.

**B14. 8a and 8b contradict each other on registration.** 8a defers dynamic
registration and states registration is construction-time; 8b's happy path
requires `pm-ai connector add` to register into a live registry. As written,
enrolment reports success and the connector is invisible until restart with
nothing saying so.

**B15. Five scopes called four.** 33a lists `Calendars.Read`, `Chat.Read`,
`ChannelMessage.Read.All`, `OnlineMeetingTranscript.Read.All` and
`offline_access`, then says "All four" twice, and its criterion asserts the set
"matches the four delegated permissions named above". The set assertion — the
whole insurance against silent consent widening — cannot be written from the
spec. The blanket admin-consent claim also covers the two permissions slice 0
exists to probe.

**B16. 4b never states the key length.** `EnvelopeCipher` refuses anything but
32 bytes (`crypto.py:153,192-194`), and `AES_KEY_BYTES` lives in
`pm_ai.storage.crypto`, which `pm_ai.core.enrolment` may not import. So
enrolment duplicates the literal or the constant is hoisted — and
`ports/__init__.py:202-206` records that two literals for the key *name* already
caused ABSENT on a healthy machine once.

**B17. `config.toml` ships refusing all content.** 4a builds a closed key
vocabulary, names no member, and defers "any setting beyond what wave 1 needs"
to Ask First. Meanwhile wave 1 needs a blended hourly rate (CAP-3's Man-Hour
Cost, raised in 11a's own Ask First) and a PM handle — hardcoded
`"andrei@example.com"` at `wiring.py:44` — and no spec owns either.

**B18. Exit codes are unnamed across three slices.** 4c states refusal and crash
must differ and names no numbers; 8b and 23b both say "refusal exit code". Each
subcommand will pick its own convention.

## C. Verification that would not catch a wrong implementation

**C1. Every spec's verification block would fail the suite.**
`tests/conftest.py` runs a skip ratchet that fails in *both* directions: below
`EXPECTED_SKIPS` it sets `exitstatus = TESTS_FAILED` and demands the baseline be
lowered in the same commit. Every spec claims "skip count lower than baseline"
**and** "no new failures". Contradictory. *Fix:* 8a and 23a each carry a task
lowering `EXPECTED_SKIPS`, and the "no new failures" line accounts for it.
*Verified directly.*

**C2. All three un-skipped tests pass while proving nothing.**
`test_ad27_connectors_only_emit_core_declared_event_types` (`:94`) and
`test_ad34_connectors_do_not_mint_event_ids` (`:483`) are loops over
`registry.all_connectors()` — an empty registry executes no assertion.
`test_ad25_project_rendering_cannot_open_the_personal_store` (`:214-227`) filters
a list and asserts it empty — an empty input passes. So "the skip count falls by
two/one" is satisfied by three stub modules, and a vacuous pass is worse than a
skip because `-rs` makes a skip visible. The AD-27 test's own comment records
this having already happened once.
*Reached independently by adversarial and verification-gap.*

**C3. The AD-25 test cannot catch the leak it exists for.** Its only filter is
`"manager-ai" in s or "personal_analytics" in s`. Neither substring occurs in
`strategic_goals.md`, so a project render declaring the personal goals file
passes verbatim — the exact B2 breach. *Fix:* assert against
`PERSONAL_SUBJECT_ARTIFACTS` and that every datasource is beneath the project
tree, the shape `test_ad38_project_scope_is_the_only_committed_scope`
(`:736`) already uses.

**C4. 8a names a test that does not exist.** The criterion says
`test_ad27_connectors_share_one_event_taxonomy`; the real name is
`test_ad27_connectors_only_emit_core_declared_event_types` at `:94`, not `:104`.
`:104` is a line inside the test body — I cited the grep hit, not the
definition. 33b's Code Map repeats both errors. AD-34 is at `:483`, not `:485`.

**C5. Both coverage criteria pass against a still-fabricating connector.**
`save_cursor` (`service.py:1357-1370`) keys coverage on
`coverage.connector_instance`, not the `instance` argument, so
`coverage_windows("graph")` returns `[]` whenever the connector labels its window
anything else — a fabricated window under another key satisfies the absence
assertion. And `grep -n "timedelta(hours=4)"` is satisfied by
`timedelta(minutes=240)`. The existing coverage test
(`tests/slice/test_vertical_slice.py:96-103`) asserts a containment range that
both the fabricated and the honest window satisfy. *Fix:* pair every absence
with a positive bound assertion read back through `coverage_windows(instance)`.

**C6. 11a's persistence claim is untestable as declared.** The criterion is
"written and the process restarted", but the declared verification is a `core`
unit test against a `StoragePort`, where a restart is unrepresentable. An
accessor that caches in memory and never persists satisfies both the criterion's
observable and the matrix's read-back row. *Fix:* a `tests/slice` case against a
real temporary root, as the existing slice tests do.

**C7. Two of 4b's three criteria cannot be evaluated at its own checkpoint.**
Both name the CLI — "the command's entire output", "`pm-ai doctor` after
enrolment" — and 4b precedes 4c in the build order. *Fix:* restate at service
level; move the surface criteria to 4c.

**C8. Nothing checks the key name agreement.** 4b's tests run against a fake, so
nothing asserts `enrol` stores under the same name `LazyKeyCrypto` fetches and
the probe reads. That is precisely the failure `MASTER_KEY_NAME` was introduced
to prevent.

**C9. `GraphConnector` "satisfies `ConnectorPort`" is checked by nothing.** The
port-conformance test (`:793-826`) asserts `isinstance` for `ScopePaths`,
`GitVcs` and `StorageService` only — no connector. Its docstring says "the
annotations are documentation until something checks them".

**C10. Nothing asserts the provenance a connector emits.** AD-36's rule — a
connector emits `UNKNOWN`, never `EXTERNAL` — lives in a comment at
`gitlab.py:51-57`. The AD-34 test inspects only the absent `id`. A Graph
connector hard-coding `EXTERNAL` would make pm-ai's own writes admissible as
evidence for its own promises, and pass.

**C11. 33a's architecture criteria cannot fail.** "`lint-imports` holds" is true
either way, because `http-confined-to-adapters` names `httpx`, `requests` and
`aiohttp` — not `msal`, which MSAL reaches transitively. And "`pm_ai.platform`
imports nothing from `pm_ai.connectors.graph`" is already guaranteed by the
layer contract with zero new code. So the placement rule the spec argues for at
length is enforced by nothing. *Fix:* decide the `msal` question in this slice
rather than deferring it, then verify by deliberate temporary misplacement.

**C12. Several matrix rows are unfalsifiable.** 33b's all-day row —
"duration handled explicitly, never as zero-cost" — is satisfied by the 24-hour
mapping the same spec's Design Notes call the wrong answer. 4a's "a declared key
at its declared type" has no declared key to test. 33b declares only the
absence of a `joinUrl`, never that a present one is retained — and `Meeting` has
no `join_url` field.

**C13. 4a's central Always is unverifiable.** "Never opens a file" is invisible
to all three declared commands: the single-writer AST check exempts read-mode
opens (`test_static_rules.py:105-127`), the import contract lists only network
and DB clients, and the file-I/O check is scoped to `pm_ai.storage`.

## D. Unhandled paths, by spec

Eighty-nine edge-case findings. Compressed to one line each; the full set is
reproducible from the lens run.

**4a** — encryption key that is also unknown (which refusal wins); nested or
dotted encryption key; `bool`/`int` coercion (`isinstance(True, int)`); UTF-8
BOM; type-valid but unusable value.

**4b** — two enrolments racing on an empty keychain; stored entry present but
corrupt, read as absent and minted over; read-back returns different material;
`key_name` disagreeing with what the cipher fetches.

**4c** — subcommand group with no leaf (`pm-ai key`); the four `Health` states
mapped to exit codes (ABSENT exiting zero); `build()` raising before dispatch;
unhandled exception sharing code 1 with refusal; `--help` raising `SystemExit`
out of `main`.

**8a** — HTTP 429 with `Retry-After`, neither 5xx nor empty; probe exceeding its
own 10s bound; duplicate row across re-paginated pages; cursor position after a
partial page; coverage `start` taken from a provider clock rather than
`ingested_at`; rows returned with no usable clock; `save_cursor` with a cursor
and no coverage.

**8b** — existing occupant in the sealed file (see B3); mode source (see B7);
process killed between the two writes; duplicate detection blind to an orphaned
secret; stdin not a TTY (credential echoed into shell history); keychain
failure modes collapsed; path-unsafe instance name refused only after the probe.

**8c** — declared field `None` at runtime; `ReviewPayload` bound to two event
types; declared field name misspelled or not a field; a payload with provider
text declaring none; entries written before the format change read as
already-sanitized; the transcript boundary (see B10).

**11a** — day boundary and timezone (see B9); tied `start` values breaking
byte-identical re-render; one malformed record blocking every render; the
collection directory absent on first run; `ScopeKind.PEOPLE` meetings;
`meeting_id` reused across scopes; provider ids unsafe as names (see B11);
foreign files (`.DS_Store`) parsed as records.

**22a** — `scope` (see A4); missing title; non-UTF-8 bytes; `goal_id` not
citation-safe; enum values differing in case or padding; the synonyms
`goals.py`'s own docstrings document (operational/tactical/strategic); a
well-formed file declaring zero goals, indistinguishable from absent.

**23a** — aware-but-non-UTC `now`; `project="personal"` or `""`; a meeting
spanning midnight or in progress; Markdown-unsafe text in goal titles and actor
names, not only meeting titles; entries with no `ingested_at`; non-message log
categories rendered as external signals; a personal-scope goal present during a
project render.

**23b** — undeclared scopes (`people`, `application`); unparseable `--scope`;
malformed event-log segment, the third input with no refusal row; write failing
after a successful render; the target existing as a directory; refusal mapped to
the crash exit code.

**33a** — token expiring mid-harvest; `slow_down`/429 while polling; local clock
skew; `interaction_required` (conditional access) as a third distinct remedy;
zero or several cached MSAL accounts; concurrent refresh with token rotation;
scopes narrowed after enrolment; probe with nothing ever enrolled.

**33b** — `@odata.nextLink` pagination, absent from the matrix; 429; modified or
moved series occurrences; declined and tentative responses counted as
time-critical; null attendee email, distribution lists, zero attendees feeding
`man_hour_cost`; provider timezone (see B8); DST-crossing spans; `end == now`
exactly; re-harvest over hand-edits (see B13); an event deleted after its record
was written; a window wider than the endpoint permits.

## What this review does not say

No lens found a problem with the wave's shape: the twelve slices, their
boundaries and the build order survive. Every finding is a defect *within* a
spec — a missing interface, an unowned decision, a claim that does not hold, a
criterion that cannot fail. The decomposition itself was not challenged by any
lens.
