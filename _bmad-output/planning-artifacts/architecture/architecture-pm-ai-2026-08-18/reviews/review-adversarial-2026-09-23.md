# Adversarial review — ARCHITECTURE-SPINE.md, amendment set of 2026-09-23

**Lens:** construct two units one level down that each obey every AD to the letter and still
build incompatibly. Every pair is a hole to close with a new or tightened AD.

**Reviewed:** `ARCHITECTURE-SPINE.md` (updated 2026-09-23), read in full, against
`pm_ai/` as it stands at `844a06e` and `_bmad-output/specs/spec-pm-ai/stories.yaml`.

**Method.** Each finding names two units that are *actually in the queue* — a shipped module
plus a named story, or two named stories — shows the AD text each one satisfies, and shows the
artifact they end up disagreeing about. Ranked by likelihood of the divergence happening in the
next wave, not by blast radius. Generic advice is excluded; every item cites the clause that
permits the divergence.

**Verdict.** The amendment set is sound where it withdrew overclaims (AD-27, AD-3's backup
relaxation, AD-12's admitted limits). It is *not* sound where it opened a vocabulary, split an
ownership, or made a field optional without saying who reconciles the two halves afterwards.
Six of the eleven findings below trace directly to a clause dated 2026-09-23, and the top four
are all reachable inside the next two stories. **H1 and H4 are correctness bugs waiting on a
story to be written; they are not stylistic.**

---

## H1 — Two units mint two `Meeting` records for one meeting, because the only key that
## survives a provider boundary is declared non-durable

**Amendments in play:** AD-33 `[revised 2026-09-23]` ("It holds no transcript pointer and no
processing status"; "Some fields are deliberately non-durable: the tentative flag and the uid
ride in memory and reach no file"), AD-23 `[revised 2026-09-23]` ("The Protocol does not exist
yet … stories `11b` and `33e` owe it").

**Unit A — story `33e`, the Graph transcript adapter.** Fetches a transcript from Graph and must
bind it to a stored `Meeting` (AD-23: "Every ingested transcript binds to a `Meeting`"; "An
unbound transcript is rejected"). The identifiers Graph hands it are the online-meeting id, the
join URL, and `iCalUId`. It resolves to a stored record by `iCalUId`, because that is the
identifier AD-33 names on the entity and the only one that survives a cross-tenant invite.

**Unit B — story `11b`, the manual adapter and transcript binding.** A `.vtt` lands in the
watched folder. AD-23: "otherwise the drop supplies title, start, and attendees to mint the
record." It resolves to a stored record by `(title, start)` and mints one when that misses.

**Why both obey every AD.** AD-33 requires a Meeting to be Tier-1 with id, calendar event
reference, title, start, duration, attendees and scope; both satisfy it. AD-34's grammar makes
`meeting:<native_id>` a *scopeless global entity*, so neither unit is obliged to qualify the id
by system or scope. AD-23's binding rule is satisfied by both. Nothing in the spine names a
resolution key.

**The clash, and it is already latent in shipped code.** `pm_ai/domain/meetings.py` states that
`meeting_id` **is the provider's per-mailbox event id** — "the same meeting cross-invited to two
tenants arrives twice under two different ids and matching on it deduplicates nothing in the only
case that motivates deduplication." The field that *does* survive, `ical_uid`, is set to `None`
by `meeting_records.as_stored()` and has no key in `_FIELDS`. So:

- the durable id is provider-scoped and duplicable;
- the non-duplicable id is not durable;
- `record_name()` derives the filename from `meeting_id` + start, so two ids are two files;
- AD-33 derives the transcript's home from meeting scope + id, so two records are two transcript
  homes — and AD-47's `link`/`EEXIST` exclusivity cannot help, because the names differ;
- AD-33's citation root then forks: commitments extracted by Unit A cite `meeting:<graph-id>`,
  those extracted by Unit B cite `meeting:<minted-id>`, and FR-25's drift audit joins neither.

AD-33 exists to make the citation root stable. Two compliant units produce two roots for one
meeting, and the mechanism that would have caught it — a natural key — is AD-34's, which
deliberately exempts the scopeless global forms.

**Likelihood: highest in the set.** AD-23 names `11b` and `33e` as the two stories that owe the
port, and the port does not exist to constrain either. `pm_ai/connectors/transcripts/graph.py`
exposes `fetch(meeting_id) -> Transcript`; `manual.py` exposes `load(raw, *, meeting_id) ->
Transcript`. Two adapters for one concept, already two different signatures, with no Protocol
between them. The divergence is not a risk; it is the current state, one story from becoming
persistent.

**Close it with (new AD, or a clause on AD-33):**

> A `Meeting`'s durable identity is a pm-ai-minted id, and the provider identifiers that
> resolved to it are durable alongside it. `meeting_id` is not a provider id. Every provider
> identifier a source may present — per-mailbox event id, `iCalUId`, online-meeting id, join URL
> — is recorded on the record as a resolution alias, the way AD-34's alias table records actor
> handles, and for the same reason: an identifier that quietly resolves to nothing mints a second
> subject. Binding a transcript is a lookup against that alias set; a miss is a refusal
> (AD-23), never a mint, except on the manual path where minting is explicitly permitted and the
> minted record is marked as unreconciled.

The load-bearing half is the second sentence. AD-33's current non-durability decision is
defensible for `tentative` (a fact about a meeting that has not happened) and indefensible for
`ical_uid` (a fact about identity), and the spine reaches the two by one rationale.

---

## H2 — A new task class has no side of the machine, and two units pick opposite defaults

**Amendment in play:** AD-15 `[revised 2026-09-23]` — the vocabulary is OPENED; "What is *not*
yet decided is how a class added later declares which side of the machine it may run on … the gap
is named under Deferred." Deferred: "Revisit when story 7 builds it, before any class beyond the
ten is served."

**Unit A — the local injection classifier.** Named in the project's own memory as the next step
for sanitization ("the fix is the local classifier") and recorded in `8g`/`8h`'s deferred work.
It adds `TaskClass.INJECTION_CLASSIFICATION` to `pm_ai/domain/task_classes.py` and calls
`ModelPort.complete(task_class=..., external=(fragment,), ...)`. Fully compliant: AD-15 says
adding a member "is an ordinary act rather than a violation", the class is typed, declared at the
call site, un-defaulted.

**Unit B — story 7's router.** Reads `task_class` and chooses an adapter. AD-15 gives it a
partition of ten names and nothing else. It must decide what an eleventh means. Three readings
are each defensible from the spine:

1. **default local** — "fail closed", consistent with AD-31's caution and AD-17's cost posture;
2. **default frontier-eligible** — read "these five are local-only, *always*" as the closed side
   and the other list as illustrative, which the word "always" invites;
3. **refuse** — no declared side, no route.

**Why both obey every AD.** AD-15's Rule is a partition of ten names; it says nothing about an
eleventh, and says so explicitly. AD-15's invariant row ("a call without a declared task class is
a defect") is satisfied. The Consistency Conventions row ("Always via `ModelPort` with an
explicit `task_class`") is satisfied. Nothing is violated by any of the three router readings.

**The clash, and its direction is the dangerous one.** Under reading 2, an injection classifier —
a unit whose entire input is *text pm-ai does not trust* — ships that text to Anthropic on its
first call. Under reading 1, a later `drift_audit` or `fact_check` class (the implementer review
already found FR-06's summary card, FR-07's fact-check digest and FR-25's drift audit unnamed by
the ten) silently gets an 8B model and returns degraded synthesis with **no error**, which is
AD-15's stated Prevents run backwards. Either way the failure is silent: nothing crashes, no
check turns red, and AD-17's disclosure ledger records the frontier call as ordinary.

The spine's own defence is ordering — "before any class beyond the ten is served" — and **nothing
makes that ordering binding**. A story adding a member touches one enum and no router. Worse,
`task_classes.py` argues deliberately that the split must not be inferable from the enum
(members alphabetised precisely so `list(TaskClass)[:5]` stops returning the local group), which
guarantees the router carries a *second* structure listing sides. Two structures that can
disagree is the failure AD-44 and AD-45 each name and refuse; AD-15 currently mandates it.

**Likelihood: very high.** Story 7 is the next model story and the classifier is already named.

**Close it with (clause on AD-15):**

> A task class declares its egress side where it is declared. `TaskClass` carries the side as a
> required member attribute — `LOCAL_ONLY` or `FRONTIER_ELIGIBLE` — so a member added without one
> is a construction error at import, in the same place and for the same reason `File` carries its
> `Tier` (AD-44). The router reads that attribute and holds no table of its own; a router-side
> list is the second structure AD-44 forbids. There is no default and no fallback: the router
> refuses a class whose side it cannot read, because a route chosen for a call site is exactly
> what AD-15 exists to prevent.

This also retires the Deferred entry rather than re-scheduling it, and it costs one field.

---

## H3 — AD-48 declares untrusted fields for a consumer that AD-12 does not oblige to read them

**Amendments in play:** AD-48 `[NEW]` ("these declarations have no reader yet … nothing yet
requires a caller assembling a prompt to sanitize all of them rather than some"); AD-12
`[revised 2026-09-23]` (three limits, the first being that `instructions` is a plain `str`).

**Unit A — a connector/payload author** (story 33's Teams chat payloads, or the remaining
story 8 connectors). Declares four untrusted `str` fields on its payload class, passing AD-48's
import-time guard. Author's belief, entirely reasonable from AD-48's Prevents: *declaring a field
untrusted is what makes it handled.*

**Unit B — the first prompt assembler** (story 7's router client, story 23's briefing, story 18's
inquiry). Reads that payload, wraps one field in `Sanitized`, passes it as `external=(...)`, and
interpolates the meeting title, the attendee display names and the work-item title into
`instructions` — because that is how prompts are written and because `instructions` is typed
`str`. Author's belief, equally reasonable from AD-12: *the port is the chokepoint; if it
type-checks, the boundary held.*

**Why both obey every AD.** AD-48 binds declaration only, and says so. AD-12's rule is enforced
at the port, and the port accepts `instructions: str` by design — the AD names this as limit one
and calls the honest call "easy", not mandatory. AD-29 is satisfied (the raw persists). No check
in `tests/architecture/` can see across the gap: `test_sanitize_boundary.py` asserts the port's
types, and the types are satisfied.

**The clash.** A calendar invite title is on AD-12's own enumerated list of externally-sourced
text ("commit messages, MR/PR descriptions, issue comments, **calendar invites**, email bodies,
meeting transcripts, chat messages"). Unit B routes one of those through the guard and three
around it, and AD-12's Prevents — "a new connector or transcript path feeding unsanitized text
into an LLM context" — is false again, this time with the guard green. The producer has declared
everything and the consumer has sanitized something, and no unit is at fault.

Note the asymmetry that makes this worse than a plain gap: AD-48's declarations make the
*omission invisible*, because a reviewer checking "are the untrusted fields declared?" gets yes.

**Likelihood: high.** The first real model call is story 7; every synthesis story after it
assembles prose around provider strings.

**Close it with (clause binding AD-48 to AD-12):**

> A field declared untrusted under AD-48 is readable only as `Sanitized`. The payload class
> exposes no plain-`str` accessor for it, so a caller cannot interpolate it into `instructions`
> without first converting it — which is the conversion AD-12's port already requires. That gives
> AD-48's records the reader they currently lack, and narrows AD-12's first limit from "any
> provider text may be interpolated" to "only text nobody declared untrusted may be", which is a
> claim a check can make. The residual — text from a source with no payload class at all — stays
> named as a limit.

---

## H4 — An honest `EMPTY` harvest can never earn coverage, so a quiet project's commitments can
## never be verified as broken

**Amendments in play:** AD-9 `[revised 2026-09-23]` ("Coverage is `CoverageWindow | None`, and
`None` is the honest answer … one is *refused* from a harvest that returned no rows"); AD-35
`[revised 2026-09-23]` (same clause, plus "The commitment sweeper must not declare `BROKEN`
across a window it has no coverage for").

**Unit A — the GitLab connector as story `8a` rewrote it.** A 4-hour harvest against a quiet
repository: the provider answers 200 with an empty page. The connector returns
`HarvestOutcome.EMPTY`, `coverage=None`. It has no choice —
`HarvestResult.__post_init__` raises on coverage with no rows, and `HarvestOutcome.EMPTY`'s own
docstring states "this outcome never carries coverage."

**Unit B — story 16's commitment sweeper.** Implements AD-35's coverage predicate as written: a
commitment overdue at `occurred_at = T` is covered only if every instance that could evidence it
has a window enclosing the ingestion interval. It reads `coverage`.

**Why both obey every AD.** Unit A is the *corrected* connector — the one the amendment exists to
produce. Unit B implements AD-35's rule verbatim, including its fail-closed instruction.

**The clash, and it is a product-level failure.** In a quiet week every harvest is `EMPTY`, so
every window is `None`, so the sweeper has coverage for nothing, so every overdue commitment
resolves `UNKNOWN` forever. FR-34's closed-loop verification never returns its negative verdict.
FR-26's nudges never fire. AD-14's "Only `FULFILLED` and `BROKEN` are terminal" becomes: only
`FULFILLED` is reachable. The system can confirm a promise was kept and can never say one was
broken — which is exactly the half a PM assistant is bought for, failing silently and in the
direction that looks like politeness.

The spine's two clauses disagree about what evidence *is*. `HarvestOutcome.EMPTY`'s docstring
says "**it is evidence: we looked**". AD-35's predicate reads `coverage`. So the connector
records evidence in a field the verdict does not consult, and the amendment that fixed the
fabrication removed the only path by which "we looked and there was nothing" reaches a verdict.

The 8a defect was a window **fabricated from the clock with no fetch behind it**. The repair
conflated that with a window *earned by a completed fetch that found nothing* — which is not a
fabrication, it is the ordinary case, and it is the single most common result a connector
returns.

**Likelihood: high.** Story 16 is the first unit to read the predicate, and the shape is already
frozen in `domain/harvest.py`.

**Close it with (clause on AD-9/AD-35):**

> Coverage is a claim about the interval the daemon **asked about**, never about what came back.
> A harvest earns a window when a provider response completed over a bounded interval the
> *request* defined — the `since` cursor and the provider's own high-water mark — and is refused
> a window only when no such response exists (`FAILED`), or when the interval's bounds would come
> from the local clock rather than from the request or the response. `EMPTY` therefore earns
> coverage; that is what distinguishes it from `FAILED`, and what AD-35's fail-closed reading
> needs in order to ever reach a verdict. A connector that cannot state its request interval
> without reading `now` returns `None` and says so.

The guard the 8a review wanted is kept intact by the last sentence: the refusal is on
*clock-derived bounds*, not on empty results.

---

## H5 — Two compliant custodians write `config.json` whole, and the last one wins

**Amendment in play:** AD-39 `[revised 2026-09-23]` — "Custody belongs to the daemon; the HTTP
half of acquisition belongs to the connector… **A connector never chooses where a secret lives,
never writes one itself, and never reads another connector's.** Admitted limit: the composition
root injects an in-memory refresh-token store today … the sealed-store update path is owed by
story `8b`."

**Unit A — `pm_ai.core.connector_enrolment`, as shipped.** Owns the sealed store as a *document*:
reads `config.json` via `read_artifact`, mutates a nested `connectors.<instance>.credential` key,
writes it back whole under `storage.exclusive(scope=APPLICATION, artifact=CREDENTIAL_STORE)`. It
preserves keys it does not own, deliberately.

**Unit B — story `8b`'s durable `RefreshTokenStore`.** Implements the Protocol declared in
`pm_ai/connectors/graph/auth.py`: `read() -> str | None`, `write(credential)`, `exclusive()`.
Written during a *harvest*, not during enrolment, because AAD rotates the refresh token on use.
`SealedCredential.encode()` documents the value as "the single string story 8b seals … story 8b's
sealed store holds **exactly one**".

**Why both obey every AD.** AD-39 assigns custody to "the daemon" — a runtime component, not a
module, not a document, not a lock. Both units are inside the daemon. Both go through
`StorageService.write_artifact`, so AD-5's single writer holds. AD-30 injects both. The connector
writes nothing itself in either design. AD-6 seals the artifact by declaration, not by caller.

**The clash, two ways.**

1. **Shape.** One unit believes the sealed store is a keyed document of many credentials; the
   other believes it is one string. `write_artifact` replaces the file whole. Whichever ships
   second either wipes the other's material or has to discover the shape by reading code that
   AD-39 never pointed it at.
2. **Lock.** `StoragePort.exclusive` is keyed `(scope, artifact)`. `RefreshTokenStore.exclusive()`
   takes no arguments and is documented as serialising "against other holders of **this store**".
   If its implementation takes its own lock rather than delegating to the artifact lock, an
   enrolment concurrent with a token rotation loses one write — and **the loss presents as the
   wrong fault**: a dropped rotation means the next refresh replays a token AAD already consumed,
   producing `CredentialStale` → AD-39 `FAILING` → AD-35 `ERROR`. The operator is told to
   re-authenticate a credential that was fine; the actual defect was a lost write in the
   custodian.

**Likelihood: high.** `8b` is named in AD-39 itself as owed, and the Graph store Protocol is
already written with its own lock method.

**Close it with (clause on AD-39):**

> Custody is a module and a document, not a component. One module owns read-modify-write of the
> sealed store; every other holder of a secret goes through it. The document's shape is declared
> once, beside the artifact declaration in the scope model, so a second occupant cannot invent a
> second shape. Serialisation is on the artifact — `exclusive(scope, artifact)` — never on an
> abstraction over it, because two locks over one whole-file replace serialise nothing.

---

## H6 — A 1:1 harvested before its attendee is enrolled is filed `personal` forever, and AD-5
## forbids moving it

**Amendment in play:** AD-4 `[clarified 2026-09-23]` — "'personal' is subject ownership, not
subject matter … A meeting the PM never tagged is the PM's own until they tag it, which is why an
unmapped calendar row is filed here rather than refused."

**Unit A — the Graph calendar connector (`33c`, shipped).** Reads the week's rows. A recurring
"1:1 — Dana" has an attendee not in the people registry, so the row is unmapped and the `Meeting`
is written with `scope = personal`. Exactly what the clarification instructs.

**Unit B — story 28, team-member scope and dossiers.** Enrols Dana as a direct report. From this
point AD-4's table applies: her data is `people` — `is_personal = false`, HR egress permitted on
approval, **deleted on a role change**. AD-33: "a 1:1 with a direct report to `people`."

**Why both obey every AD.** Unit A follows AD-4's clarification verbatim. Unit B follows AD-4's
`people` rule verbatim. AD-5 forbids in-place edits, so neither may rewrite the earlier records.
No AD names a re-classification event, and `Meeting.scope` is required-and-immutable by design.

**The clash, and it inverts the AD's purpose.** Six months of a direct report's 1:1 records —
and, under AD-23, six months of their *transcripts*, since the transcript's home is derived from
the meeting's scope — sit permanently in `~/.manager-ai/`. That is the scope AD-4's own table
describes as the one that **survives a company change**, and AD-31 describes as the one whose
material may **never** reach HR. Meanwhile the identical meeting held next Tuesday lands in
`people/`, the single deletable directory whose whole compensating obligation is that "leaving a
role is one removal, not an audit."

So AD-4's compensating obligation is silently false: the removal is not one directory, because an
arbitrary prefix of every report's history is in another scope under a different rule. And the
split is invisible — both files parse, both render, nothing is unbound, no guard fires. AD-38's
cross-scope invariant even *helps hide it*, since a personal-scope record citing a
personal-scope meeting is perfectly legal.

**Likelihood: medium-high.** The enrolment order — calendars connected first, people registry
populated later — is the ordinary first-run sequence, and `4h`'s first-run command already
sequences connectors before anything else.

**Close it with (clause on AD-4, or a new AD):**

> Subject ownership can be learned late, and learning it is a declared event. When a subject is
> re-classified — an attendee becomes a direct report, a meeting is tagged to a project — the
> records already filed under the previous owner are **re-homed by a declared job** (AD-45),
> which writes the record into the owning scope and appends a supersession entry in both scopes'
> logs rather than editing either (AD-5). Until that job runs, the earlier records are listed by
> `pm-ai doctor` as mis-homed. A scope assigned by absence of information is provisional, and the
> spine says which mechanism makes it final.

The cheaper variant, if re-homing is too large: forbid the provisional filing for *any* meeting
with an external human attendee, and stage it as a Proposal (AD-13) asking the PM whose it is.
That is one card per new counterpart and it never produces a mis-homed transcript.

---

## H7 — Two health substrates, and the spine says which is authoritative for neither consumer

**Amendments in play:** AD-9 `[revised 2026-09-23]` (surface widened to four members, including
`check_health`), AD-39 `[revised 2026-09-23]` ("It is not carried: nothing persists it,
deliberately, because stale health on a diagnostic screen is worse than none"; "`doctor` lists …
`pm-ai connector check` does the live probing and owns the ten-second bound").

**Unit A — story 16's verifier.** Needs to know an instance is unhealthy in order to emit AD-35's
`ERROR`. Health is not persisted, so it uses the durable substrate the spine does provide:
`HarvestFailure` rows behind `StoragePort.harvest_failure(instance)`. Correct per AD-35, which
grounds `ERROR` in "attempts … in the log marked failed".

**Unit B — story 23's briefing.** AD-39: "`pm-ai doctor` and the briefing both surface any
instance not `healthy`." Health is not persisted, so the briefing calls `check_health()` live on
each connector.

**Why both obey every AD.** Unit A obeys AD-35's grounding. Unit B obeys AD-39's sentence
literally, and cannot obey it any other way, because the same AD forbids persisting the answer.

**The clash, three ways.**

1. **They disagree, routinely.** A token that died ten minutes ago has no failure row yet — the
   next harvest is up to 4 hours away (AD-9's FR-02 default) — and probes `FAILING`. A connector
   that failed last night and was repaired at breakfast has a failure row and probes `OK`. The
   07:00 briefing and the commitment verdicts in it can contradict each other on the same page.
2. **The bound is assigned to the wrong command.** AD-39 says the ten-second bound is
   `connector check`'s, "which is why the bound is assignable to one command at all". The
   briefing makes the same probes with no bound named anywhere. `ConnectorRegistry.check_health`
   carries `HEALTH_PROBE_SECONDS = 10.0` as a default, so the briefing inherits a bound the
   spine did not give it — by luck, not by rule.
3. **A push path performs egress.** AD-40 governs unprompted messages; the briefing is a push
   occasion. Under Unit B, generating it makes N outbound provider calls. That is class-H egress
   on the interrupt path, which no AD contemplates and AD-40's budget cannot see.

**Likelihood: medium-high.** Stories 16 and 23 are both queued and both read "is this connector
healthy".

**Close it with (clause on AD-39):**

> Two questions, two substrates, and neither answers the other. *Did harvesting work?* is durable
> and is the only input to a domain verdict or a surfaced report — the harvest-attempt and
> -failure record, which survives the process. *Can I reach the provider right now?* is live,
> diagnostic, bounded, and reaches exactly one caller: `pm-ai connector check`, invoked by a
> human. A briefing, a dashboard or a verdict reads the durable substrate and never probes; a
> probe never contributes to a verdict. That is what "health is not carried" has to mean for it
> to be safe, and it also keeps egress off the push path.

---

## H8 — AD-27's withdrawn versioning leaves a revisit trigger no unit can evaluate

**Amendment in play:** AD-27 `[revised 2026-09-23]` — "Neither enumeration is versioned yet, and
the clause is withdrawn rather than left standing." Deferred: "It becomes real the first time the
grammar changes after something has written segments."

**Unit A — any story adding an entry type or a field.** AD-27 permits it: the self-action
vocabulary binds "a set of **required** — not exhaustive — field names", so adding a required
field to an existing self-action type is an ordinary, reviewed change. Story 16 adding a verdict
field to a commitment entry is unremarkable.

**Unit B — story 19's compaction job.** Reads sealed segments written months earlier to build a
milestone summary, and must carry forward the compaction entries of the segments it replaces
(AD-5). Its parser is written against the grammar as of its own story.

**Why both obey every AD.** There is no versioning clause to violate. Unit A's change is
explicitly permitted. Unit B implements AD-5 verbatim. AD-46's per-file MD5 detects *change*, not
*grammar*, so nothing observes the mismatch.

**The clash.** Unit B meets a segment lacking a field its parser requires. Its choices are to drop
the entries (compaction is the only operation permitted to destroy Tier 1, and this would destroy
it by parse failure rather than by summary) or to fail the whole month (AD-5's sealed segments are
immutable, so no migration is available). Both are silent-ish: one loses a month of history behind
a summary that omits it, the other stops compaction and lets FR-37's bound fail open.

**The structural defect is the trigger, not the absence.** "The first time the grammar changes
after something has written segments" is a condition **no unit can evaluate at build time**. A
story adding a field cannot know whether any machine anywhere holds a segment. So the deferral
can never be discharged by the unit whose change discharges it, and it will be discovered by a
parse error on a user's machine.

**Likelihood: medium.** Entry-type additions are routine; compaction (story 19) is queued.

**Close it with (either, but the trigger must change):**

> Minimal: parsers preserve unknown keys and tolerate absent ones, and a field added to an
> existing entry type is optional-on-read forever. A grammar that only ever grows needs no
> version. **And** restate the deferral's trigger as something a unit can see: *any change to the
> field sets in `pm_ai/domain/event_entries.py`* — observable in a diff, assertable in a test —
> rather than a fact about the installed base.

---

## H9 — Two connectors, two shapes for the same provider text, both passing AD-48

**Amendment in play:** AD-48's named limit — "the rule reaches `str` and `str | None` only.
Nested types that carry text and containers of text fall outside both records, untouched and
undeclared."

**Unit A — a connector that flattens.** Story 8's remaining connectors map a Jira issue to a
payload with `summary: str`, `description: str`, `latest_comment: str`. All three declared
untrusted. Passes AD-48.

**Unit B — a connector that nests.** Story 33's Teams chat maps a message to
`body: MessageBody`, `attachments: tuple[Attachment, ...]`. Every string is one level down. AD-48's
import-time check sees no bare `str` field and passes. Nothing is declared, because nothing is
declarable.

**Why both obey every AD.** AD-27 requires one registered payload shape per observed type and
enforces it at construction — both comply. AD-48's check passes for both. AD-12's port is
satisfied by whatever each consumer chooses to wrap.

**The clash.** This is AD-9's own Prevents — "each connector inventing its own event shape or
applying sanitization inconsistently" — reached by two compliant routes. Equivalent provider text
is untrusted-and-declared in one connector and undeclared in the other; a consumer sanitizing "all
declared untrusted fields" covers Unit A completely and Unit B not at all; and AD-27's promise
that a closed type binds one payload shape gives no help, because the two types are genuinely
different events.

Nesting is not an edge case for the connectors actually queued: chat messages have bodies and
attachments, work items have comment threads, calendar rows have attendee lists with display names.

**Likelihood: medium-high**, and it rises with every connector after GitLab.

**Close it with:**

> The declaration is recursive or the payload is flat. Either AD-48's records address nested
> fields by path and the check walks the type graph, or AD-27's payload registry refuses a
> payload class whose text is not reachable as a top-level `str`/`str | None`. The second is
> cheaper and is the one the current check can enforce; it costs connectors a flattening step and
> buys a declaration surface that is total. Leaving it named-but-open means the first nested
> payload is compliant and uncovered.

---

## H10 — AD-49 states an end-state and assigns no duty, so the shared refusal is never moved

**Amendment in play:** AD-49 `[NEW]` — "When a refusal is raised by `core` *and* by an adapter,
it is declared in `pm_ai.ports`."

**Unit A — story `33e`.** Raises `CredentialStale` from `pm_ai/connectors/graph/auth.py`, where
it already lives. Correct at the time it was written, and `GraphAuthPort`'s own docstring endorses
it: "The concrete classes live beside the adapter."

**Unit B — story `8b`.** AD-39: "Re-consent is a Proposal." `pm_ai.core.connector_enrolment` must
raise or classify the same refusal. `core` may not import `connectors`, so it declares its own
`CredentialStale`.

**Why both obey every AD.** AD-49 binds a *condition on the end state*, not a duty on either
author. Unit A was correct when written and unchanged since. Unit B cannot import Unit A's class
and cannot be asked to move it without editing an adapter it does not own. Neither has violated
the rule; the rule's predicate is simply now true and nobody is named to act on it.

**The clash** is AD-49's own Prevents, verbatim: two classes, one name, the composition root's
`except` clause compiling, reading correctly, and matching one of them. The AD identifies the
failure precisely and then leaves the only moment at which it is preventable unowned.

**Likelihood: medium.** `33e` and `8b` are both wave-2, and `DuplicateConnector`'s docstring
records this exact sequence having already happened once between `8d` and `8b` — the precedent is
in the repo.

**Close it with:**

> The duty falls on the second raiser: a unit that needs to raise a refusal a sibling layer
> already raises moves the declaration to `ports` as part of its own change, rather than
> declaring a second one. And the condition is made observable — a check that no exception class
> name is declared in two packages under `pm_ai/` outside `ports`, which is a static rule and
> fails the build the way AD-49's Prevents assumes something will.

---

## H11 — Two renderers of one Proposal disagree on whether the PM sees raw or sanitized text

**Amendment in play:** AD-29 `[revised 2026-09-23]` — "The derived copy is **not** confined to
model context: a `for_model`-derived slice is what a staged `Proposal` carries as its summary, so
it reaches `operational.db` and the card the PM reads."

**Unit A — story 13's card renderer.** Renders the stored `summary`, which per AD-29 is a
`for_model` slice: redacted.

**Unit B — story 17's draft dispatch, or any surface re-rendering from the payload.** AD-12:
"The raw is what persists; the derived copy is rebuilt at the point of use." Rebuilds from the raw
payload at display time, because a card that shows the PM redacted text is arguably wrong — the
PM is entitled to their own record (AD-6: "transparency over one's own record is a product
principle").

**Why both obey every AD.** AD-29 sanctions the stored sanitized summary. AD-12 sanctions
rebuilding at point of use and binds only the **model** boundary — a human-facing surface is
governed by neither. AD-13's "One card renderer serves both surfaces" constrains Telegram-vs-CLI,
not proposal-vs-draft.

**The clash.** One approval surface shows `[redacted]` where the other shows the injection string
verbatim, for the same proposal. And the second is a live social-engineering surface: an
inline-keyboard card whose text was authored by whoever wrote the MR description, sitting next to
an Approve button. AD-12's threat model stops at the model; AD-32 reasons carefully about a
transcript conferring authority on a *machine* and not at all about provider prose conferring it
on a *human*.

**Likelihood: medium.** Story 13 then 17, both queued; the divergence needs two renderers to
exist, which AD-25's precedent suggests is likely rather than unlikely.

**Close it with:**

> Say which text a human-facing card carries, once, and why. The defensible answer is: the card
> shows the raw (it is the PM's own record, AD-6) **with provider-authored spans marked as such**,
> and never renders provider text in a position where it can be read as pm-ai's own instruction to
> the PM. One renderer per entity, as AD-13 already says for surfaces, extended to say *per
> entity* rather than per surface pair.

---

## Examined and not filed

Recorded so the absence is a decision rather than an omission.

- **AD-9's `sample_events()` and a connector whose mapping needs a credential.** Looks like a
  clash (build offline vs resolve an actor), but AD-34 puts alias resolution in normalization, not
  in the connector, so the mapping a connector performs is genuinely offline. No hole.
- **AD-45's `inputs()` vs AD-46's watch permission.** The "staleness, not file access"
  distinction is stated clearly enough that two units cannot legally diverge; the compaction
  self-trigger is named and its guard (schedule-triggered) is stated.
- **AD-47's `link` vs `replace` split.** Genuinely closed: the taken-name behaviour is the
  discriminator and both shapes are assigned to named artifacts.
- **AD-44's three grains.** The global-by-basename tier rule is a real constraint, but the AD
  states the collision, states the migration path, and refuses at import. A second unit cannot
  diverge silently.
- **AD-3's backup relaxation.** Withdrawing an unimplemented promise cannot create a divergence;
  it removes one.

## Cross-cutting note on enforcement

Six of the eleven findings are invisible to `tests/architecture/` as it stands, and for one
reason: the checks assert *shapes within a unit* (types at the port, tiers on a node, writes
inside `service.py`) and none asserts an *agreement between two units* about a value neither
owns — a resolution key (H1), an egress side (H2), a coverage semantic (H4), a document shape
(H5), a scope assignment made under uncertainty (H6), an authoritative substrate (H7).

The spine's own strongest pattern is the fix and it is already written down three times — AD-44's
"derived, never maintained beside", AD-45's "the dependency graph is derived, never configured",
AD-46's "one function computes that checksum". Each closure above is the same move: put the
contested value *on the declaration* so there is one structure rather than two. H2 and H5 are the
two where the spine currently mandates the second structure outright.
