# Implementer's Read — ARCHITECTURE-SPINE.md (pm-ai)

**Reviewer stance:** senior engineer handed the spine and told to build. No prior context, did not write the document.
**Documents read:** `ARCHITECTURE-SPINE.md`, `docs/prd_pm_ai.md` v0.9.1, `.importlinter`, `tests/architecture/*`, and `SOLUTION-DESIGN.md` (read last, to check whether it closes gaps — it mostly does not, and where it does it is not the build contract).
**Date:** 2026-08-18

---

## How to read this

I walked three slices the way I would actually build them: name the module, name the function, name the type that crosses each boundary, decide what lands on disk, decide what happens when it fails. Everywhere the spine answered, I say so — the document is unusually strong on *shape* and on the security perimeter. Everywhere I would have had to invent, guess, or flip a coin, that is a finding.

**Verdict up front:** I cannot build any of the three slices without inventing decisions the spine does not make. Slice 1 is blocked on a structural contradiction (where harvested telemetry physically lives, given AD-3), Slice 2 on an authorization hole and an unspecified ledger grammar, Slice 3 on an enforcement contract that forbids the surfaces layer from making the HTTP call the surfaces layer exists to make.

**62 findings.** 11 blocking, 24 material, 27 minor.

---

# SLICE 1 — "Harvest GitLab commits for a registered project and store them"

## What I would build

```
pm_ai/ports/connector.py          ConnectorPort protocol
pm_ai/connectors/gitlab.py        GitLabConnectorAdapter
pm_ai/connectors/registry.py      all_connectors(), load()          [required by tests, absent from spine]
pm_ai/core/taxonomy.py            NormalizedEventType (closed enum)  [required by tests, unenumerated in spine]
pm_ai/core/scheduler.py           Cursor, cadence policy
pm_ai/core/sanitize.py            sanitize(raw) -> Sanitized(raw, for_model)
pm_ai/storage/service.py          the single writer
pm_ai/storage/ledger.py           append_to_project_ledger(...)
???                               the thing that actually runs harvest → sanitize → dedup → persist
```

That last line is the first problem and it is not a small one.

## Step by step

**1. `pm-ai project add /repo/alpha --name alpha`.** AD-11 answers this cleanly: explicit registration only, registry in `~/.pm-ai/`, no filesystem discovery, reachable from both surfaces through the daemon. AD-8 gives me the transport (loopback HTTP + token file at `0600`). Good.

But: the CLI must *write* nothing (AD-7, thin client), so the daemon writes `projects.toml`. Which component? AD-5 says the storage service owns every write and enumerates "markdown, SQLite, vectors, encrypted blobs" — TOML is not in that list, and the registry is not domain state. Meanwhile `test_ad5_single_writer_owns_all_file_writes` forbids `open(p,"w")` anywhere outside `pm_ai/storage/`, so the registry writer must sit in storage regardless. And nobody creates the AD-8 token file: it is a `0600` file that must exist before the CLI's first call, written by a daemon that has not started, chmod'd by a layer that may not write. **[F11, F59]**

**2. Configure the connector instance.** AD-10 gives me the identity — `(scope, connector_type, config, cursor)` — and that is genuinely useful. Then I try to write the port:

```python
class ConnectorPort(Protocol):
    def harvest(self, since: Cursor) -> list[NormalizedEvent]: ...
```

AD-9 says "exactly one method — `harvest(since: Cursor)` — and does only auth, fetch, and map-to-schema". So the adapter authenticates. With what? The GitLab token lives in the encrypted `~/.pm-ai/private/config.json`, owned by the storage service. `pm_ai.connectors` may not import `pm_ai.storage` (the paradigm line: "No adapter imports another adapter", and the import-linter `layering` contract makes them independent siblings). So the decrypted secret has to be handed to the connector by whatever constructs it — and the spine never defines a constructor, a factory, an instance-config type, or who decrypts. The only layer that can see both is the core, which is supposed to be I/O-free and is exactly where I do not want plaintext credentials to sit. **[F7]** — blocking.

Also: `Scope` is listed as a first-class entity and appears in the event envelope, in the connector-instance tuple, and (as a word) in AD-18's skill authorization, but nothing says what a Scope *is* — a string, an enum of three, a `(kind, project_id)` pair, a path. Two developers will pick differently and the envelope will not join. **[F62]**

**3. Schedule it.** AD-9 is clear that the daemon owns cadence, backoff, cursors, and rate limiting, and the AST rule mechanically stops a connector from spawning a task. Excellent — that invariant is real.

But *which module* calls `gitlab.harvest(...)`? `pm_ai.core.scheduler` exists (the tests import `Cursor` from it), and the core may not import `pm_ai.connectors` (dependency direction). Import-linter's layer order puts `surfaces` above the adapters, so the only layer that can legally see connectors, storage, models, and core at once is `pm_ai.surfaces` — which is Telegram, CLI, and the HTTP API. The container diagram shows a Daemon box holding API, SCHED, CORE, SAN, POOL, STORE, ROUTER, REG; the source tree has no home for that box and no `__main__` / launchd entry point. **There is no composition root and no application/orchestration layer in the design.** The pipeline `harvest → sanitize → normalize → index → extract → stage/execute` is named in the paradigm section and lives nowhere. **[F8]** — blocking, and it recurs in every slice.

**4. First harvest.** The instance has no cursor yet. `harvest(since: Cursor)` is non-optional, and AD-9 says a Cursor is "provider-defined bytes ... never parsed or compared by core". So I cannot construct "since 7 days ago" (UJ-10 requires exactly that backfill on connector add), I cannot pass `None` without changing the signature, and I cannot ask the connector to mint one because the port has one method. **[F4]** — blocking for UJ-10.

**5. Map to `NormalizedEvent`.** The envelope is specified: `id, scope, source, type, occurred_at, ingested_at, actor, payload, source_ref`. AD-27 says `type` comes from a closed core enumeration. Then I open `pm_ai/core/taxonomy.py` to map `commit pushed` and the enumeration does not exist — the spine declares the rule and never lists a single member, for either enumeration (event types *or* `event_log.md` entry types). AD-27's stated purpose is that two connectors must not describe the same change differently; with no seed list, the first two connectors written by two people will diverge exactly as AD-27 predicts, and `test_ad27_...` will pass because it only checks that each connector's `emits()` is a subset of whatever the enum happens to contain. **[F2]** — blocking.

Related: AD-27 says "Both enumerations are versioned so parsers can read historical entries", but the Consistency Convention for a ledger entry specifies a header line of `(id, timestamp, type)` — no version field. A parser cannot know which vocabulary version an old entry was written under. **[F3]**

**6. Who fills `id`, `ingested_at`, `scope`?** The connector returns fully-formed `NormalizedEvent`s, so mechanically the connector must populate every required field. But `ingested_at` is defined as *the* cross-connector ordering watermark — a value the connector's own clock must not own — and `scope` is the scheduler's knowledge, not the provider's. Reading A: the connector fills all three. Reading B: the connector returns a partial and the pipeline stamps envelope fields. Both satisfy the written port. **[F6]**

**7. Dedup.** AD-9 says dedup happens "outside the connector, uniformly". On what key? `id` is a prefixed ULID minted per harvest, so replaying a cursor after a crash produces new ids for the same commits. The envelope has no `external_id`; `source_ref` is described loosely as "URL / commit SHA / ticket anchor" and is never declared unique or canonically formatted. There is no dedup key in the design. **[F5]** — blocking; this is the telemetry equivalent of the idempotency bug the test suite says is the one to keep.

**8. Sanitize.** AD-12 and AD-29 are two of the best-specified rules here: every inbound payload, centrally, non-destructive, raw retained. What I still cannot decide is whether the *derived* copy is persisted next to the raw or recomputed at every model call. Persist it and I double the store and owe AD-3 an answer about rebuilding it; recompute it and every briefing pays the sanitizer cost over 30 days of events. **[F38]**

**9. Persist.** Here Slice 1 stops being buildable.

- AD-3: markdown is truth; `event_telemetry.db` and `vector_index/` are "disposable derived indexes"; "Any state that cannot be reconstructed from markdown is a defect."
- AD-29: the raw payload is stored unmodified so citations resolve.
- FR-02: harvested telemetry goes into `event_telemetry.db`.

Compose those and every harvested commit message must exist in markdown, because after a cache wipe I cannot re-fetch it (the provider may have changed, and the cursor is gone with the DB). So `event_log.md` — append-only, **never rotated** (AD-24) — must accumulate every commit, MR comment, calendar invite and email body the radar ever sees, across a 4-hour cycle, forever. That is a multi-GB single markdown file the PM is supposed to grep and hand-edit.

The alternative reading — `event_log.md` holds decisions and actions only, raw telemetry lives in SQLite — makes the DB non-disposable and breaks AD-3, NFR-11, and `test_ad3_indexes_rebuild_from_markdown_without_loss` on day one. The spine supports both readings and rules out neither. **[F1]** — the single most blocking finding in the document.

Three more fall out of the same seam:
- Cursors, job-queue rows, and the FR-04 `PENDING_RETRY` buffer are durable SQLite state (AD-20) that no markdown can reconstruct. `snapshot_derived_state()` in the AD-3 test has no defined boundary, so either these are excluded (and AD-3's "any state" is false) or the test can never go green. **[F10]**
- FR-37 pruning *deletes* raw events older than 7 days into summaries. After a prune, rebuild-with-zero-loss is definitionally false. **[F14]**
- Raw transcripts, audio, and `telegram_cache/` are neither markdown truth nor rebuildable derived index — a third category AD-3's dichotomy does not have. **[F54]**

**10. Which `event_log.md`?** There are three (personal, per-project, and the app scope has none). AD-1 says "Every skill invocation appends an entry to `event_log.md`" — singular, unqualified. A harvest for project alpha, a personal-scope RSS harvest, and a skill that reads a project's GitLab on behalf of a personal briefing each need a routing rule that does not exist. **[F15]**

**11. Failure.** FR-02: on 5xx, "logs the failure to `event_log.md` and retries with exponential backoff". AD-24: `event_log.md` is domain truth and "Writing debug output to `event_log.md` is prohibited". Is a transient GitLab 502 domain truth or diagnostics? I can argue either; the AST check will flag me if I reach for `logger.error` near the string `event_log`, which pushes me toward writing a domain entry — for which there is no entry type (see F2). **[F12]**

And the retry policy itself: "exponential backoff with jitter, owned by the scheduler" — no max attempts, no dead-letter, no giving-up rule, and no `Job` state enumeration anywhere in the document (only `PENDING_RETRY` is named, in passing). A connector whose token was revoked retries forever, silently. **[F13]**

**12. Memory.** FR-02 requires <50MB RSS during a harvest cycle; the port returns `list[NormalizedEvent]` — a materialized list. A 7-day backfill on a busy monorepo blows that budget and there is no streaming/paging form of the port. **[F18]**

## Where the spine answered me in Slice 1

AD-9 (no connector-owned scheduling), AD-10 (per-project instance identity), AD-11 (explicit registration), AD-12/AD-29 (sanitize everything, non-destructively), AD-5's single-writer rule, and the enforcement suite backing all of them. These are real, testable, and I would not have guessed them right on my own.

---

# SLICE 2 — "Ingest a transcript, extract an implicit commitment, stage it, execute on approval"

## What I would build

```
pm_ai/ports/transcript_source.py      TranscriptSourcePort
pm_ai/connectors/graph_transcripts.py GraphTranscriptAdapter
pm_ai/connectors/manual_transcripts.py ManualFolderAdapter        (AD-23, day one)
pm_ai/core/transcripts.py             registered_adapters(), get_adapter()
pm_ai/core/extraction.py              extract(sanitized) -> Extraction
pm_ai/core/proposals.py               Proposal, ProposalStatus, registered_types()
pm_ai/core/commitments.py             Commitment, CommitmentStatus, CoachingCommitment
pm_ai/core/jobs.py                    idempotency_key()
pm_ai/skills/registry.py              invoke(name, payload, idempotency_key)
pm_ai/skills/post_comment_gitlab.py   the executor
pm_ai/storage/ledger.py               append-only commitments_log.md
```

## Step by step

**1. Get the transcript.** AD-23 is one of the best decisions in the document: the port, the Graph adapter, the manual watched-folder adapter built from day one, and the requirement that the pipeline be exercisable end-to-end without a tenant. It de-risks half the PRD.

What it does not say: where the watched folder is (`~/.pm-ai/inbox/`? per-project?), which scope owns it, who polls it and how often (AD-9's uniform pull applies to *connectors*; a transcript source is a different port), whether a re-dropped file re-processes, and what happens to the file after ingestion. **[F28]**

**2. Decide which project this meeting belongs to.** Everything downstream depends on it: the commitment goes to `<repo>/.project-ai/memory/commitments_log.md` (AD-28), the event log entry goes to that project's log, the anchor matching resolves against that project's work items. The spine has no meeting→project attribution rule — not from calendar metadata, not from attendees, not from a per-connector default, not as a Proposal to the PM. A 1:1 coaching transcript and a project architecture sync arrive through the *same* port and must land in physically separated stores (AD-25, AD-28), and nothing tells me how to tell them apart. **[F27]** — blocking; the failure mode is personal coaching material written into a git-committed repo, which is the exact thing AD-28 exists to prevent.

**3. Sanitize, then extract.** AD-15 pins `extraction` as local-only, always. Fine. Then I need to classify each utterance as **explicit command** (executes immediately, no approval) versus **implicit discussion** (must become a Proposal). The spine states the consequence (AD-13: no external mutation from implicit extraction without an approved Proposal) but never states the authorization rule:

> Nothing in the spine binds explicit-command authority to a speaker identity.

"pm-ai, update WI-226 and set the status to closed" is executable authorization *whoever says it*. Combined with AD-23's watched folder — a plain directory where any `.txt` grants execution authority — this is a privilege escalation with no attacker sophistication required, and it sails past AD-12/AD-29 because sanitization strips prompt-injection markers, not legitimate-looking meeting speech. The PRD is equally loose ("explicitly addressing the assistant by name"), but the spine is the security document here: AD-1 defines the perimeter and this is a hole in it. **[F20]** — blocking.

Related: the confidence threshold for classifying explicit vs implicit is unstated (FR-01's ≥85% governs *anchor* matching, not command classification), so a local 8B model's false positive executes an unapproved mutation. **[F20b, folded into F20's guard]**

**4. Pick a task class.** AD-15's list is closed at the call site: `transcription | extraction | classification | embedding | fuzzy_match` (local) and `coaching | briefing_synthesis | research | draft_generation | inquiry_synthesis` (frontier). Now I need to generate the post-meeting summary card body (FR-06), the `[FACT_CHECK_DIGEST]` block (FR-07), and later the drift audit (FR-25). None of those is any of the ten. `briefing_synthesis`? `inquiry_synthesis`? The choice silently changes cost, model tier, and whether the call is even permitted to leave the machine — and "a call without a declared task class is a defect" gives me no room to add one locally. **[F29]**

Separately, AD-15 makes `extraction` local-only *forever* with no escalation path, against SM-7's ≥95% parse precision and the spine's own risk note that local extraction quality is unbenchmarked. There is no "escalate to frontier below confidence X with the PM's consent" seam, and no AD says whether adding one would be a violation. **[F30]**

**5. Build the Proposal.** AD-13 is the best decision in the document — one entity, one lifecycle, one renderer, features register a type plus an executor, expiry owned by the scheduler. It collapses five approval flows and the test suite enforces it. But as written the state machine is wrong in two places and thin in a third:

- `staged → approved → executed | edited | rejected | expired`. `edited` sits as a **terminal peer of `executed`**. UJ-2 and FR-06 both describe Edit-then-Send. Under the written machine, an edited draft can never be sent. Either `edited` means "amended, back to `staged`" (and the arrow is wrong) or edits are terminal (and the product is broken). **[F21]** — blocking, and the kind of thing two developers resolve differently in an afternoon.
- There is **no `failed` state**. The PM approves, the executor gets a GitLab 500. The proposal is `approved` forever, the job retries under AD-20, and no surface can show "this did not go through". **[F22]**
- Fields are `id, type, summary, payload, target executor, expiry, status`. No scope/project, no `created_at`, no provenance `source_ref` — while the Citations convention says *every* fact surfaced to the user carries a `source_ref`. An approval card with no citation back to the transcript line is the one card the PM cannot safely approve. **[F23]**

**6. Write the staged commitment.** FR-06 and FR-34 both require an entry in `commitments_log.md` with status `[STAGED_APPROVAL]` at staging time. AD-14 forbids exactly that — `STAGED_APPROVAL` is a Proposal state, a Commitment begins at `PENDING`, and `test_ad14_...` asserts it. AD-14 is right on the modelling and silent on the consequence: the PRD's requirement that unapproved candidates are visible in the ledger now has no home. Do I write nothing until approval (losing the audit of what was proposed), or write a proposal-typed entry into the commitments ledger (a ledger entry type nobody has defined)? **[F24]**

**7. Define the Commitment.** The spine gives me a state machine and nothing else. FR-34 requires `commitment_id, timestamp, speaker, target_assignee, description, target_work_item, due_date, status`, plus verification hashes and evidence references. None of that is in the spine — which matters because Slice 2's whole point is that Slice 3 and FR-33 can later verify against telemetry, and verification needs `target_work_item` and `due_date` as *typed, queryable* fields. **[F25]**

**8. Write it to markdown.** The convention: "Append-only blocks with a machine-readable header line (id, timestamp, type) followed by human-readable body; parsers must tolerate hand-edits." So `due_date`, `assignee`, and evidence refs live either in an unspecified header extension or in free prose. Then AD-3 requires that `pm-ai reindex` reconstruct the SQLite index **with zero data loss** from those files, and AD-5 requires status changes to be new entries folded by id.

I am being asked to write a lossless parser for a grammar that is not specified, over files a human is explicitly invited to hand-edit, with no schema version, no field list, no escaping rule, and no conflict rule for two entries with the same id and timestamp. This is the load-bearing property of the whole system (sovereignty) resting on an unwritten format. **[F26]** — blocking.

**9. Approve.** The PM taps `[Approve]` in Telegram or runs `pm-ai approve prp_...`. Now: does approval enqueue a Job, always? AD-20 says every deferred unit of work is a durable row and FR-04's offline buffer *is* that queue in `PENDING_RETRY`. AD-21 says under 5 seconds may answer inline — and posting a GitLab comment is ~1 second. Read it one way and the mutation happens inline on the surface's thread with no durable row and therefore no offline replay, breaking FR-04/NFR-10. Read it the other and every mutation is queued. The spine permits both. **[F33]**

**10. Idempotency.** `sha256(job_type + target_ref + canonical_payload)` is the right decision and the test suite is right to call it the one to keep. Three gaps make it unimplementable as written:
- "canonical_payload" — canonical by whose rules? JSON with sorted keys, which separators, which unicode normalization, how are floats and datetimes rendered? Two developers produce different keys; worse, *one* developer produces different keys after a serializer upgrade, and the replay double-posts. The convention says "canonical" and stops. **[F31a]**
- Nothing defines where executed keys are *stored* or who checks them. "The skill layer refuses a mutating invocation that arrives without one" is a presence check, not a dedup. There is no idempotency ledger in the design, and if it lives in SQLite it is derived state that AD-3 says can be wiped — after which every pending job replays and double-posts. **[F31b]**
- No uniqueness window: posting the same comment twice on purpose is silently swallowed forever. **[F31c]**

**11. Invoke the skill.** AD-1 and AD-18 are strong: allowlist, declared scopes, refusal logged. But "each declaring the scopes it may exercise" overloads the word `Scope`, which the Consistency Conventions table has already claimed as a first-class *entity* meaning one of the three data scopes. Reading A: `scopes = ["project:alpha"]` (data boundary). Reading B: `scopes = ["gitlab:issues:write"]` (capability). These produce completely different registries and completely different violation checks. **[F32]**

**12. Log it.** Skill invocation appends to `event_log.md` (AD-1). Which one (F15), with which entry type (F2, F3), and does a *failed* or *refused* invocation append too? Unstated.

**13. Retention vs citation.** The commitment's evidence points at a transcript line. NFR-09 purges raw transcripts at 30 days; AD-29 says citations and audits resolve against the raw source; AD-3 says commitments_log.md is truth and must be rebuildable. At day 31 every commitment's `source_ref` dangles and no rule says whether that is acceptable, whether the cited span should be copied into the ledger before purge, or whether purge should be blocked while a live commitment cites it. The Deferred section explicitly says retention *beyond* raw transcripts is unspecified — but this is the transcript case, which it claims is covered. **[F34]**

**14. Identifiers.** Prefixes are `cmt_, prp_, evt_, job_, skl_`. There is no prefix for Meeting, Transcript, or Extraction — all three are first-class boxes in the ER diagram — and `CoachingCommitment` (AD-28's deliberately distinct entity, in a physically separate store) has no prefix of its own, so it either collides with `cmt_` or invents one. Two id spaces sharing a prefix across two stores is how a personal commitment eventually gets fetched by a project-scope lookup. **[F35]**

**15. Render the card.** "One card renderer serves both surfaces." Living where? Core cannot know about Telegram inline keyboards; surfaces cannot each own the format without becoming two renderers. The intermediate representation (a `Card` value type in core, adapted per surface) is the obvious answer and it is not in the document, not in the entity list, and not in the source tree. **[F36]**

**16. The heavy-work pool.** AD-19 bounds local model work to one job at a time — a good, honest default. It has no priority or preemption concept. A 45-minute meeting transcription occupies the single slot; a 20-second voice note arriving behind it cannot meet NFR-01's 10-second SLA, and the pre-meeting dashboard behind *that* misses FR-32's window. NFR-01, NFR-03, NFR-05 and NFR-06 have no architectural home in the spine at all (only NFR-04's 60s and FR-37's 150ms are bound, by AD-22). **[F37]**

## Where the spine answered me in Slice 2

AD-13 (one Proposal), AD-14 (lifecycles distinct), AD-23 (fallback adapter), AD-28 (project vs coaching commitment), AD-29 (non-destructive sanitization), AD-20's key formula in principle. Every one of these would have been got wrong by default.

---

# SLICE 3 — "Generate the 07:00 daily briefing and deliver it to Telegram"

## What I would build

```
pm_ai/core/scheduler.py       the 07:00 trigger
pm_ai/core/retrieval.py       index lookup, no model (AD-22)
pm_ai/core/briefing.py        context assembly + one frontier call
pm_ai/models/router.py        route("briefing_synthesis") -> sonnet-5
pm_ai/models/frontier/...     Tool Runner adapter
pm_ai/storage/service.py      write ~/.manager-ai/memory/daily_dashboard.md
pm_ai/surfaces/telegram/...   push the card
```

## Step by step

**1. Fire at 07:00 local.** Two things the spine does not survive contact with:

- The Mac is asleep at 07:00. `launchd` `KeepAlive` restarts a dead process; it does not wake a sleeping machine, and the platform layer's listed responsibilities are "keychain access, process supervision, and packaging paths" — no power/wake concern, no `IOPMAssertion`/`pmset` seam. FR-09's testable consequence ("generated by 07:00 AM local time daily") is unobeyable on a laptop as the architecture stands, and there is no missed-trigger catch-up policy: on wake at 09:30, does yesterday's briefing generate, get skipped, or get backfilled? **[F43]** — blocking for the slice's stated acceptance criterion.
- Times are "stored UTC, rendered in the user's local timezone at the surface only". A 07:00 *local* recurring trigger is a scheduling-policy concern living in the core, which by that convention never sees local time. And DST gives a day where 07:00 happens twice and one where it does not. **[F44]**

**2. Retrieve.** AD-22 is a genuinely good split and the test (`retrieval.uses_model_port()` is false) makes it stick. Retrieval must pull cross-project telemetry, commitments, goals, and burnout signals.

**3. Cross the scope wall — in the direction nobody specified.** AD-25 forbids *project-scope rendering* from opening the personal analytics store, and the test checks that direction. The briefing is personal-scope rendering reading *project* telemetry, which is not addressed. Worse, AD-4 says `~/.manager-ai/` "holds sovereign personal material only ... and contains **no** project-specific information or configuration" — and the briefing I am asked to write into `~/.manager-ai/memory/daily_dashboard.md` is, per FR-09 and FR-11, full of project work items, project commitments, and per-project strategic milestones. Reading A ("no project *configuration*", per AD-4's final sentence) permits it; Reading B ("no project-specific *information*", per AD-4's rule sentence) forbids the entire feature. A reviewer applying AD-4 literally rejects the slice. **[F41]** — blocking; **[F46]** for the unstated direction of the wall.

Which registered projects feed one personal briefing, and what happens when there are five, is also unstated. **[F47]**

**4. Synthesize.** `route("briefing_synthesis")` → `claude-sonnet-5`, one call, via Tool Runner (AD-15, AD-16). Clean, enforced, and the Anthropic API notes in the Stack section (thinking on by default, `max_tokens` covering thinking+text, no temperature, `output_config.format` instead of prefill) are the kind of detail that saves a day. Credit where due.

But FR-09 demands four exact headers with "no unpopulated empty sections", and the structured-output mechanism is mentioned only as a stack footnote bound to no AD — there is no validation/repair seam for a model that returns three headers. **[F49]** And prompt caching is asserted as a cost lever with no rule about who owns the stable prefix (persona + rules assembly), so the first refactor that reorders the prefix silently doubles the briefing's cost. **[F50]**

**5. Write the dashboard.** `daily_dashboard.md` is markdown, so AD-5 applies: "Markdown ledgers and logs are **append-only**... never an in-place edit". A daily dashboard is a rendered artifact that must be replaced. It is also not in AD-3's list of truth files, yet it is markdown, so it is neither truth nor a disposable derived index. Third category again, and this time the append-only rule actively contradicts the feature. **[F61]**

**6. Deliver to Telegram — and here the enforcement stops me.** `.importlinter`, contract `http-confined-to-adapters`, forbids `httpx`, `requests`, and `aiohttp` in `pm_ai.surfaces`. AD-1's rule text is broader: "No component may import an HTTP/API client to call an external service outside a connector or skill adapter."

The Telegram bridge lives in `pm_ai/surfaces/telegram/` (Design Paradigm table, source tree, and `test_ad2_...` which imports `pm_ai.surfaces.telegram.bridge`). It must long-poll `api.telegram.org` and POST the card. `python-telegram-bot` 22.8 — the pinned stack — uses `httpx` as its request backend. And the CLI, a "thin client" that reaches the daemon "over authenticated loopback HTTP" (AD-8), needs an HTTP client in `pm_ai/surfaces/cli/` too. Both are forbidden by a contract that fails the build. `test_ad8_...` calls `api.test_client()`, which for FastAPI means `starlette.testclient` — which imports `httpx` — inside `pm_ai.surfaces.api`.

So the spine's own enforcement forbids the two surfaces from doing the only thing the spine says they do. **[F39]** — blocking, and mechanically red on the first commit.

**7. Is a Telegram send an egress?** AD-1: "100% of external reads and writes route through the MCP skill layer" and "Every skill invocation appends an entry to `event_log.md`". A long-poll is an external read; a card push is an external write. Reading A: Telegram is a *surface*, exempt, and AD-1's "100%" is rhetorical. Reading B: Telegram delivery is a skill (`skills/send_telegram_message.py`), and then who owns the receive loop, and does every briefing push need an AD-20 idempotency key against an API that has no idempotency semantics? The spine names Telegram in both roles and reconciles neither. **[F40]**

**8. What port does the scheduler push through?** `SurfacePort` appears once, in the ports table, and is never mentioned again — no method, no adapter naming, no mention in any AD. Slice 3's delivery step is exactly the thing it must define. **[F42]**

**9. May it even notify?** FR-13 restricts push notifications to "scheduled pre-meeting preparation alerts and post-meeting summary cards" — the 07:00 briefing push is not on that list, and the Non-Goals repeat it. The spine has no notification-policy gate, no quiet-hours concept, and FR-13 appears in the Capability map only inside the "FR-09..FR-17" bucket governed by AD-15/17/25/28, none of which is about interruption. There is no seam where a delivery is checked against the interruption policy. **[F45]**

**10. Account for the cost.** AD-17 is a good decision (warn, never degrade) and is tested. Unstated: which `event_log.md` the token/cost entry lands in, which entry type it uses (F2), and where the *running monthly total* lives — a counter in SQLite is derived state AD-3 says is disposable, so a cache wipe resets the PM's spend history unless it is folded from markdown entries, which nothing says it is. **[F48]**

## Where the spine answered me in Slice 3

AD-22's latency split, AD-15's routing table and tiering, AD-16's Tool Runner choice with the reasoning encoded as an import contract, AD-17's warn-only rule, AD-25's physical separation. The 07:00 briefing's *model* story is fully specified; its *trigger*, *scope*, and *delivery* stories are not.

---

# Findings

Severity: **B** blocking (I cannot write the code without a decision), **M** material (I will write code that a second developer writes differently), **m** minor.

## Slice 1 — harvest

| # | Sev | Location | Trigger condition | Guard (concrete fix) | Potential consequence |
|---|---|---|---|---|---|
| F1 | B | AD-3 × AD-29 × FR-02 | First harvested commit must be persisted somewhere durable | Add to AD-3: "Harvested `NormalizedEvent` payloads are **derived-but-unrecoverable** state: the raw payload is written to an append-only, date-partitioned markdown journal at `~/.pm-ai/private/telemetry/YYYY-MM.md` (per scope), which is truth for rebuild; `event_log.md` receives only decisions, actions, and commitment-relevant events." Name the partitioning and the rebuild source explicitly. | Either `event_log.md` grows unbounded and unusable (AD-24 forbids rotation), or the telemetry DB is silently non-disposable and NFR-11 / `test_ad3` are false. Discovered only after months of data. |
| F2 | B | AD-27 | Writing the first connector's `emits()` | Ship the seed enumeration in the spine: list the initial `NormalizedEventType` members (e.g. `code.commit_pushed`, `code.mr_opened`, `code.mr_merged`, `issue.created`, `issue.state_changed`, `issue.commented`, `ci.pipeline_finished`, `calendar.event_scheduled`, `chat.message`, `mail.received`, `doc.updated`, `article.published`) and the `event_log.md` entry types (`DECISION`, `SKILL_INVOCATION`, `PROPOSAL_STAGED/APPROVED/REJECTED/EXPIRED`, `COMMITMENT_*`, `HARVEST_FAILURE`, `SECURITY_VIOLATION`, `MODEL_SPEND`, `ENCRYPTION_DISABLED`). | AD-27's stated failure mode happens anyway: GitLab emits `mr_merged`, Jira emits `issue.done`, commitment verification misses half its evidence, and the test passes because it only checks subset containment. |
| F3 | M | AD-27 × Conventions (ledger entries) | Reading a 6-month-old ledger entry after an enum change | Add `v` to the mandatory header line: `id, timestamp, type, v` (taxonomy version), and state that parsers dispatch on `v`. | "Versioned so parsers can read historical entries" is unimplementable; a taxonomy change makes old entries unparseable and breaks `reindex` — i.e. breaks AD-3. |
| F4 | B | AD-9 | First harvest of a newly added connector (UJ-10's 7-day backfill) | Change the port to `harvest(since: Cursor \| None) -> HarvestResult` and add to AD-9: "A `None` cursor means the connector performs its own bootstrap window, defaulting to the instance's configured `backfill_days` (7)." | Either UJ-10's backfill is silently dropped, or core fabricates a Cursor and violates AD-9's opacity rule, coupling core to GitLab pagination. |
| F5 | B | AD-9 ("dedup ... outside the connector") | Scheduler replays a cursor after a crash mid-cycle | Add `external_id: str` to the event envelope, mandatory and provider-stable (`gitlab:commit:<sha>`), and state that dedup is `(scope, source, external_id)` upsert; `id` (ULID) is internal only. | Every crash-replay duplicates telemetry; commitment verification counts the same MR merge twice; activity summaries inflate. Silent forever. |
| F6 | M | AD-9 × Conventions (event envelope) | Constructing a `NormalizedEvent` inside the GitLab adapter | Add to AD-9: "The connector populates `source`, `type`, `occurred_at`, `actor`, `payload`, `source_ref`, `external_id`. The pipeline stamps `id`, `scope`, and `ingested_at`; a connector that sets them is a defect." | Connector and pipeline both assign `id`/`ingested_at`; cross-connector ordering runs off provider clocks with unknown skew; dedup keys off two different ids. |
| F7 | B | AD-9 ("does only auth") × Paradigm ("no adapter imports another") | Connector needs the GitLab token from encrypted `config.json` | Define a `ConnectorInstanceConfig` value type and state the seam: "the storage service decrypts credentials and the daemon's composition root constructs connector instances with `ConnectorFactory(config: ConnectorInstanceConfig, secrets: SecretRef)`; secrets never enter `pm_ai.core` and are never logged." | Either a layering violation, or plaintext credentials transiting the "I/O-free" core — a security regression on the very axis AD-1 protects. |
| F8 | B | Design Paradigm / Source tree / container diagram | Deciding which module calls `gitlab.harvest()` | Add `pm_ai/app/` (or `pm_ai/daemon/`) to the source tree as the composition root and pipeline host, with an import-linter layer above adapters and below nothing, plus the `__main__` launchd entry point. State explicitly: "the pipeline `harvest → sanitize → normalize → index → extract → stage/execute` lives in `pm_ai.app.pipeline`." | With no legal home, the pipeline gets wedged into `core` (breaks I/O-free, breaks direction) or into `surfaces` (a "thin client" layer owning background work, breaking AD-7). Whichever the first developer picks, the second inherits. |
| F9 | M | AD-9 ("exactly one method") vs `test_ad27_*` | Implementing `pm_ai.connectors.registry.all_connectors()` | Amend AD-9: "exactly one *harvesting* method; every connector additionally exposes `name: str`, `emits() -> frozenset[NormalizedEventType]`, and `probe() -> HealthStatus` (FR-35's 10-second health check)." | The enforcement suite requires an interface the spine forbids; a developer obeying AD-9 literally ships a connector the AD-27 test cannot introspect, and FR-35's health probe has nowhere to live. |
| F10 | B | AD-3 × AD-20 | Writing `snapshot_derived_state()` for the AD-3 test | Add to AD-3 a named exception: "Operational state — connector cursors, job-queue rows, idempotency records — is durable, non-derived, and out of scope for `reindex`. It lives in `~/.pm-ai/private/operational.db`, is excluded from `snapshot_derived_state()`, and its loss costs replay, not data." | Either AD-3's "any state that cannot be reconstructed from markdown is a defect" is knowingly false, or the AD-3 test can never be made green — and the Phase 1 exit criterion is zero skips. |
| F11 | M | AD-5 × Conventions (Config) × AD-8 | Writing `projects.toml` and the `0600` token file | Extend AD-5: "the storage service is the sole writer of *all* files including TOML config, the registry, and the API token file; it is responsible for mode bits. The token is generated on first daemon start and written before the API binds." | No component may legally write config; the token file is a bootstrap chicken-and-egg; someone writes it from `surfaces/cli` and the AST test goes red at the worst moment. |
| F12 | M | FR-02 × AD-24 | GitLab returns 502 during a harvest | State the rule: "external failures that change system behaviour (connector disabled, retry exhausted, auth revoked) are domain entries of type `HARVEST_FAILURE`; individual transient retries are diagnostics." | Either the audit trail fills with transient 502s (destroying AD-24's value) or connector outages are invisible in the record the PM actually reads. |
| F13 | M | Conventions (Retries) × AD-20 | A connector whose token was revoked | Define the `Job` state enum in the spine (`PENDING → RUNNING → PENDING_RETRY → DONE \| DEAD`), a max-attempt count, and a dead-letter rule that emits a domain event. | Infinite silent retry against a 401; the job queue grows; the PM never learns the connector is dead. |
| F14 | M | AD-3 × FR-37 | Pruning runs on day 8 | Add to AD-3: "Rebuild fidelity is defined against the retention policy in force, not against all history. `reindex` reconstructs what the current markdown holds; pruning is an intentional, logged reduction of truth." | AD-3's "zero data loss" and FR-37's compression are contradictory promises; nobody can tell whether a post-prune rebuild diff is a bug. |
| F15 | M | AD-1, AD-24, AD-27 | A skill reads project alpha's GitLab on behalf of the personal briefing | Add a routing rule: "an entry is written to the `event_log.md` of the scope named in the triggering event's `scope` field; cross-scope actions write to the *initiating* scope and carry a `related_scope` field." | Audit entries scatter unpredictably across three files; `pm-ai retrospective --weekly` (FR-10) undercounts; cross-scope actions become unauditable. |
| F16 | m | AD-5 | Crash between the markdown append and the index insert | State ordering: "markdown append fsyncs first, index write second; `reindex` is the reconciliation path." | Index and truth diverge with no defined direction of repair; two developers pick opposite orders and one of them loses data on crash. |
| F17 | M | Layer table ("hot-loadable plugins") × FR-35.3 | PM adds a Jira connector without restarting the daemon | Either specify the mechanism (entry-point group `pm_ai.connectors`, discovered on a `POST /v1/connectors/reload`, instances constructed lazily) or move hot-loading to Deferred with a revisit condition. | FR-35.3's "without a daemon restart" is asserted in two documents and designed in neither; the first implementation restarts the daemon and quietly fails the requirement. |
| F18 | m | AD-9 × FR-02 (50MB RSS) | 7-day backfill on a busy repo | Return `Iterator[NormalizedEvent]` (or a paged `HarvestResult` with a continuation cursor) rather than `list`. | Backfill blows the memory budget on the exact operation UJ-10 makes routine. |
| F19 | M | AD-4 / AD-1 / Deployment | Writing `.project-ai/memory/commitments_log.md` into a git working tree | State who commits: "pm-ai writes files into `.project-ai/` and never runs git; committing is the PM's action" — or add a `git` skill to the registry with declared scopes. | AD-1 forbids shell outside `platform` and there is no git port, so either the PRD's "committed to version control" happens by hope, or someone smuggles `subprocess` into storage and the AD-1 test goes red. |

## Slice 2 — transcript to executed proposal

| # | Sev | Location | Trigger condition | Guard (concrete fix) | Potential consequence |
|---|---|---|---|---|---|
| F20 | B | AD-13 / AD-23 / AD-1 | Any transcript containing "pm-ai, update WI-226..." from any speaker | Add an AD: "Explicit verbal authorization is valid only when the utterance is attributed to a paired principal (the PM's speaker label / Graph attendee identity) **and** the transcript arrived from an authenticated source adapter. Transcripts from the manual watched folder are `unattributed` and can never produce explicit execution — every extraction from them is implicit and requires a Proposal. Classification confidence below 0.9 degrades to implicit." | Anyone in any meeting — or anyone who can drop a file in a folder — gets unapproved write access to GitLab/Jira through the system's own execution path. This is the hole AD-1 exists to close, reached from the inside. |
| F21 | B | AD-13 (status enum) | PM taps `[Edit]` on a draft, then `[Send]` | Correct the machine: `staged → (edited → staged)* → approved → executed \| failed`; `rejected` and `expired` terminal. State that an edit supersedes the payload, resets TTL, and appends a new ledger entry. | An edited proposal can never execute; UJ-2's core interaction is unimplementable as specified, and each developer invents a different repair. |
| F22 | M | AD-13 | Executor returns a GitLab 500 after approval | Add `failed` (with `attempts`, `last_error`) as a terminal-after-retry-exhaustion state; state that `approved → executing → executed \| failed`. | Approved-but-failed mutations are invisible; the PM believes WI-108 was updated and it was not. |
| F23 | M | AD-13 (field list) × Conventions (Citations) | Rendering the approval card | Extend the Proposal: `id, type, scope, created_at, summary, payload, source_refs: list[str], executor, ttl, status`. | The PM approves external mutations with no citation to the transcript line that produced them — approving blind, which is the failure mode the dual-authorization design exists to prevent. |
| F24 | M | AD-14 × FR-06 / FR-34.1 | Staging an implicit commitment | State the resolution: "the ledger records only real commitments (`PENDING` onward). Staged candidates live as Proposals and appear in the approval queue; `commitments_log.md` gains a `PROPOSED` *event* entry (not a status) referencing `prp_…` so the audit is preserved." | Direct PRD/spine conflict with a passing test on the spine's side: either the PRD's ledger audit of unapproved items disappears, or someone writes `STAGED_APPROVAL` into the ledger and turns the AD-14 test red. |
| F25 | M | AD-14 / AD-28 | Defining `Commitment` for verification (FR-33) | Put the field list in the spine: `id, scope, project, speaker, assignee, description, target_ref (work item), due_date, status, evidence: list[EvidenceRef], source_ref`. | FR-33's closed-loop verification has no queryable `due_date` or `target_ref`; `[BROKEN]` cannot be computed; SM-9 is unmeasurable. |
| F26 | B | Conventions (Markdown ledger entries) × AD-3 × AD-5 | Writing `reindex` for `commitments_log.md` | Specify the grammar: a fenced YAML front-block per entry with the typed fields, followed by free prose; state that only the front-block is authoritative for rebuild, that prose is never parsed, and give the conflict rule (last entry by `(timestamp, id, seq)` wins). | The system's defining property — sovereignty via rebuildable plaintext — rests on an unwritten format over hand-editable files. The first hand-edit that reflows a line loses a `due_date` and nothing reports it. |
| F27 | B | AD-23 / AD-28 / AD-25 | A transcript arrives and must be filed | Add to AD-23: "Every transcript carries a mandatory `scope` resolved before extraction, from (1) an explicit `--project` on the on-demand command, (2) the calendar event's mapped project in the registry, (3) otherwise a `scope_assignment` Proposal to the PM. No extraction runs on an unscoped transcript." Add `project_hint` to the registry's calendar mapping. | A 1:1 coaching transcript is extracted into a git-committed project ledger — precisely the AD-28 violation, arriving through a path AD-28 does not guard because the entity type was decided *after* scoping. |
| F28 | M | AD-23 | Building the manual adapter on day one | Specify: watched folder `~/.pm-ai/inbox/transcripts/`, polled by the daemon scheduler at 60s, files moved to `processed/` after ingestion, dedup on content hash, `.vtt/.docx/.txt` only. | Undefined path/cadence/dedup means re-dropping a file re-runs extraction and re-stages proposals; the "day one" adapter is the least-specified component in the document. |
| F29 | M | AD-15 (closed task classes) | Generating the post-meeting summary card | Extend the enumeration with the classes the PRD actually needs — `meeting_synthesis` (frontier, sonnet), `fact_check` (frontier, sonnet), `drift_audit` (frontier, sonnet), `anchor_ranking` (local) — or state the fallback rule for an unlisted operation. | Every developer maps meeting summarization to a different class; cost accounting by task class (SM-5) becomes meaningless, and one mapping sends 45-minute transcripts to Opus. |
| F30 | M | AD-15 ("local-only, always") × SM-7 (≥95%) | Local 8B extraction returns low-confidence anchors | Add an explicit escape hatch and its guard: "extraction may escalate to `meeting_synthesis` only when local confidence < 0.85, only with a per-project opt-in flag, and every escalation logs a `MODEL_ESCALATION` entry" — or record in Deferred that SM-7 is accepted as unreachable until benchmarks say otherwise. | The architecture forbids the only remedy for the risk it names in its own Open Risks section; SM-7 fails and there is no legal fix. |
| F31 | B | AD-20 / Conventions (Idempotency keys) | Any external mutation, and its replay after a restart | (a) Define canonicalization exactly: `sha256(job_type \|"\|" \| target_ref \|"\|" \| json.dumps(payload, sort_keys=True, separators=(",",":"), ensure_ascii=False, default=<ISO-8601 for datetimes>))`. (b) Add an idempotency ledger: executed keys are durable rows in `operational.db` with the result, checked before every mutating invocation, retained 90 days. (c) State the uniqueness window. | The test the README calls "the one to keep" only proves determinism inside one process. Without canonical rules the key changes on a serializer upgrade; without a store, presence-checking a key prevents nothing and every replay double-posts to GitLab. |
| F32 | M | AD-18 ("scopes it may exercise") × Conventions (`Scope` entity) | Writing the skill registry manifest | Rename to avoid the collision: skills declare `capabilities` (e.g. `gitlab:issue:comment`) **and** `data_scopes` (which of the three scopes they may touch); state that both are checked. | The word `Scope` means two things one table apart. One developer builds a data-boundary check, another builds a capability check, and each believes the firewall is enforced. |
| F33 | M | AD-20 × AD-21 | PM approves a 1-second GitLab comment post | State it unambiguously: "every external mutation is a durable job row regardless of expected duration; AD-21's 5-second rule governs *response style*, not durability." | Fast mutations execute inline with no durable row, so FR-04/NFR-10's offline buffering silently does not cover the most common mutation in the product. |
| F34 | M | AD-29 × NFR-09 × Deferred | Day 31 after a meeting that produced a live commitment | Add: "before purge, the cited spans referenced by any live `source_ref` are copied verbatim into the citing ledger entry as `quoted_evidence`; purge is otherwise unconditional." | Every commitment older than 30 days has a dangling citation; drift audits and the audit trail resolve to nothing; AD-29's whole justification evaporates on a timer. |
| F35 | m | Conventions (Identifiers) | Creating a Meeting, a Transcript, or a CoachingCommitment | Complete the prefix table: `mtg_`, `trn_`, `ext_`, and `ccm_` for `CoachingCommitment` (distinct from `cmt_`). | Personal and project commitments share an id space across two physically separated stores — the exact join AD-25/AD-28 exist to make impossible. |
| F36 | m | AD-13 ("one card renderer") | Rendering an approval card on two surfaces | Name the seam: "core produces a `Card` value type (title, fields, citations, actions); each surface adapts it. Surfaces never compose card text." | Two renderers, drifting formats, and AD-7's "identical functionality through the same core services" fails quietly. |
| F37 | M | AD-19 × NFR-01/NFR-03 | A voice note arrives behind a 45-minute transcription | Add priority to the pool: "the bounded pool is priority-ordered — interactive (voice note, on-demand query) preempts scheduled (transcript, embedding, pruning) at the job boundary; long jobs are chunked so head-of-line blocking is bounded to one chunk." | NFR-01's 10-second SLA is unachievable whenever a transcript is in flight; the pre-meeting dashboard misses its 15-minute window; the failure looks random. |
| F38 | m | AD-29 | Assembling model context for any later flow | State it: "the derived copy is not persisted; sanitization is deterministic and re-run at context-assembly time" (or the opposite, with a storage location). | Half the codebase re-sanitizes and half reads a stored copy that the other half never wrote. |

## Slice 3 — briefing and delivery

| # | Sev | Location | Trigger condition | Guard (concrete fix) | Potential consequence |
|---|---|---|---|---|---|
| F39 | B | AD-1 rule text × `.importlinter:http-confined-to-adapters` × Stack (PTB 22.8, FastAPI) | First line of the Telegram bridge or the CLI's daemon client | Amend the contract and the AD: HTTP clients are permitted in `pm_ai.connectors`, `pm_ai.skills`, `pm_ai.surfaces.telegram` (outbound long-poll only, AD-2), and `pm_ai.surfaces.cli` (loopback only). Say why each exception is safe. | The build is red the moment either surface is implemented as designed. The likely "fix" is someone weakening the contract in a hurry — losing the protection for `storage` and `platform` too. |
| F40 | M | AD-1 ("100% of external reads and writes") × AD-2 | Pushing the briefing card | State the exception explicitly: "the Telegram bridge is a surface transport, not an egress; AD-1's 100% rule covers mutations of *systems of record*. Telegram sends are logged as `SURFACE_DELIVERY`, not `SKILL_INVOCATION`, and carry no idempotency key." | Either Telegram delivery is built as a skill (and the long-poll receive loop has no home), or AD-1's absolute claim is quietly false and the next reviewer cannot tell which other exceptions were also intended. |
| F41 | B | AD-4 × FR-09 / FR-11 | Writing `~/.manager-ai/memory/daily_dashboard.md` | Disambiguate AD-4: "`~/.manager-ai/` holds no project *configuration* and no project *credentials*. It may hold personal synthesis that references project material by `source_ref`, because the briefing is personal output. What it must never hold is anything required to *operate* a project." | Read literally, AD-4 forbids the deliverable of Slice 3. A reviewer rejects the briefing; or a developer relocates the briefing to project scope and breaks the personal scope's portability. |
| F42 | M | Ports table (`SurfacePort`) | Scheduler needs to push a card with no user request in flight | Define it: `SurfacePort.deliver(scope, card: Card, target: SurfaceTarget) -> DeliveryReceipt`, implemented by the Telegram and CLI-notification adapters, with the rule that scheduler-initiated delivery goes through it and nothing else. | The one named port that Slice 3 requires has no contract; the scheduler reaches into `surfaces.telegram` directly and the dependency direction inverts. |
| F43 | B | Deployment (launchd) × FR-09 | The Mac is asleep at 07:00 (the normal case) | Add to AD-26/Deployment: "wake scheduling is a `platform` concern — a `pmset repeat wake` entry installed at setup — and every scheduled trigger has a catch-up policy: `run_on_wake_within(grace)` or `skip`. The briefing's grace is 4 hours." | FR-09's only testable consequence is unachievable; the briefing appears at 09:40 or not at all, and the missed-trigger behaviour differs per developer per trigger. |
| F44 | m | Conventions (Dates & times) × AD-7 | DST transition, or any 07:00-local recurrence | State: "recurrence rules are expressed in the user's IANA timezone and materialized to UTC per occurrence; on a skipped local time the trigger fires at the next valid instant, on a repeated one it fires once." | Two briefings on one day, none on another, once a year — debugged the following November. |
| F45 | M | FR-13 / Non-Goals — no AD at all | Pushing any card | Add an AD: "all surface delivery passes a single `NotificationPolicy` gate in core (quiet hours, FR-13's allowlist of permitted push classes, per-surface muting). No component calls `SurfacePort.deliver` directly." | FR-13 and a Non-Goal are enforced by nothing; the first feature that pushes a nice-to-have card breaks the product's core promise not to interrupt, and nothing catches it. |
| F46 | M | AD-25 | Personal briefing reading project telemetry | State both directions: "project-scope rendering may not open the personal analytics store (enforced). Personal-scope rendering may read project telemetry through the retrieval port; it may never write to project scope." | The wall is specified in one direction and tested in one direction; the untested direction is where the briefing lives, so its rules are whatever the first implementation does. |
| F47 | m | AD-4 / AD-10 / FR-09 | PM has five registered projects | State the aggregation rule: all registered projects with `include_in_personal_briefing = true` (default true), ordered by recent activity, capped at N. | Briefing content depends on registry iteration order; adding a sixth project silently changes yesterday's format. |
| F48 | m | AD-17 × AD-3 | Monthly spend total surfaced in the briefing | State: "spend is folded from `MODEL_SPEND` entries in `event_log.md` at read time; no authoritative counter lives in SQLite." | A cache wipe resets the PM's cost history — which is precisely the instrument AD-17 says the $20 figure exists to provide. |
| F49 | m | FR-09 (4 headers, no empty sections) × Stack notes | Model returns three headers | Bind the mechanism to a rule: "briefing synthesis declares `output_config.format` with the four-section schema; a response failing schema validation is retried once, then rendered with an explicit `(no items)` marker." | FR-09's testable consequence fails intermittently with no defined remedy; someone adds a regex post-processor and it becomes load-bearing. |
| F50 | m | Stack (prompt caching) | Second briefing of the month | Name the owner: "`core.context.assemble_prefix()` owns the cached prefix (persona → rules → goals) and its ordering is a compatibility surface; changing it invalidates the cache and must be a deliberate change." | Cost silently doubles after an unrelated refactor reorders the prompt; AD-17's warning fires and nobody knows why. |
| F61 | M | AD-5 (append-only) × AD-3 (truth vs derived) | Writing today's `daily_dashboard.md` over yesterday's | Add a third category: "**rendered markdown artifacts** (`daily_dashboard.md`, project dashboards) are derived, replaceable, and exempt from the append-only rule; they are rebuildable and are not truth." | Either dashboards are append-only and become an unreadable pile, or AD-5's append-only rule is violated on the most-written file in the system with no written exemption. |

## Cross-cutting and PRD coverage

| # | Sev | Location | Trigger condition | Guard (concrete fix) | Potential consequence |
|---|---|---|---|---|---|
| F51 | M | FR-28 — no AD, `skills/` row in the map only | A GitLab item labelled `pm-ai:execute` | Decide and write it: "FR-28 operates through the GitLab API only — branch creation, file content updates, MR creation — via `skills/execute_work_item.py`. No local clone, no local git, no filesystem edits. Work items requiring a real build are out of scope for v1." | FR-28 needs a working tree and a git binary that AD-1 forbids; the first implementer either smuggles `subprocess` past the AST test or discovers mid-sprint that the requirement is unbuildable. |
| F52 | m | AD-6 (debug toggle) × `test_ad6_markdown_is_never_encrypted` | Running the suite with encryption disabled | Define `is_encrypted(path)` as the *policy* predicate (path → should-be-encrypted), not the observed state, and say so in AD-6. | The architecture suite goes red in exactly the mode AD-6 blesses for local debugging; someone "fixes" the test and the real invariant stops being checked. |
| F53 | m | AD-6 | Deciding whether a new path is encrypted | Put the mapping in the spine as a table (path glob → encrypted / plaintext / derived) rather than prose, since enforcement is path-based. | A new store lands under `~/.pm-ai/private/` unencrypted because prose listed four examples and the developer's file was not one of them. |
| F54 | M | AD-3 | Classifying `chat_history/`, `telegram_cache/` | Add the third category to AD-3 explicitly: "**primary non-markdown state** (raw transcripts, audio, mobile cache) — not truth, not derived, not rebuildable; governed by retention, backed up separately or accepted as lossy." | The truth/derived dichotomy has no slot for the raw evidence AD-29 depends on; backup guidance ("markdown scopes only") silently discards it. |
| F55 | m | FR-20 / FR-35.3 — no AD | `pm-ai persona set directness=concise` | Add a config-reload seam: "rules and persona files are read through a cached loader invalidated by mtime; no restart is required and no component caches parsed rules beyond a request." | "Without restarting the daemon" is asserted in the PRD and designed nowhere; personas change on disk and the running daemon ignores them until the next restart. |
| F56 | m | NFR-07 (pre-commit hooks) — no AD | `pm-ai project add` on a repo | Assign it: "`project add` installs a pre-commit hook via the `platform` layer that fails the commit if `.project-ai-private/` or personal-scope paths are staged." | NFR-07's only enforcement mechanism has no owner; the privacy charter relies on the PM's `.gitignore` discipline. |
| F57 | m | FR-30 (`core/metrics` in the map only) | PM defines "issues found per MR review" for Alex | State the scope: custom metric *definitions* are project scope; computed per-person series are personal-analytics scope (`~/.manager-ai-private/`) because they are performance data about a named individual. | Performance metrics about a named employee get written into a git-committed project directory and pushed to the team's remote. |
| F58 | m | Frontmatter `companions: []` vs `SOLUTION-DESIGN.md` (declares source PRD v0.8.0; spine declares v0.9.0) | Two documents in one folder disagreeing | Set `companions: [SOLUTION-DESIGN.md]`, align its source version, and fix its stale text ("executes immediately through a **signed**-registry skill" contradicts AD-18's deferred signing). | A builder reads the companion, believes skills are signed, and reasons about the security model from a retracted premise. |
| F59 | M | AD-8 | Implementing `pm-ai reindex`, `doctor`, `approve`, `connector add` | Define the daemon API surface in the spine or a named companion: resource paths, versioning (`/v1`), the SSE contract, and the error envelope (typed domain errors → HTTP status mapping). | AD-8 fixes the transport and nothing else; every command invents its own request/response shape, and the CLI/Telegram parity AD-7 demands cannot be checked. |
| F60 | M | AD-21 ("delivers the result over the same async channel") | `pm-ai query "..."` returns an ack, then the process exits | State the rule: "results are delivered to the *scope's* subscribed surfaces, not the originating connection. A one-shot CLI invocation blocks on SSE until completion or `--async`; results are always also durable and retrievable via `pm-ai jobs show`." | For a one-shot CLI command "the same async channel" no longer exists when the answer is ready; results are silently dropped or the CLI blocks forever. |
| F62 | M | Conventions (`Scope` entity) / AD-4 / AD-10 / event envelope | Populating `NormalizedEvent.scope` | Define the type in the spine: `Scope = PersonalScope \| ProjectScope(project_id)` with `project_id` the registry key (not a path), and state its serialized form in markdown and SQLite. | The envelope's `scope` is a path in one connector and a name in another; scope-isolation checks compare incomparable values and pass vacuously. |

---

## Summary of counts

| | Blocking | Material | Minor | Total |
|---|---|---|---|---|
| Slice 1 | 6 | 10 | 3 | 19 |
| Slice 2 | 5 | 12 | 2 | 19 |
| Slice 3 | 3 | 6 | 6 | 15 |
| Cross-cutting / PRD | 0 | 6 | 3 | 9 |
| **Total** | **14** | **34** | **14** | **62** |

(Blocking counts include F31 and F26, which span slices but bite first where listed.)

## What the spine gets right, and should not lose in the edit

The perimeter decisions are excellent and I would have got several of them wrong unaided: AD-16's Tool Runner choice encoded as an import contract; AD-13's single Proposal collapsing five approval flows; AD-14's refusal to overload one status field; AD-23's day-one fallback adapter; AD-22's latency split; AD-29's non-destructive sanitization; AD-20's deterministic key formula; AD-25's physical separation instead of a remembered tag check. The enforcement suite is the best part of the package — it is genuinely unusual to be handed contracts before the code.

The pattern in the findings is the one the tests' own README predicts: **ports fix shape, not meaning.** Almost every blocking finding is a place where two components share a *word* — `event_log.md`, `scope`, `derived state`, `canonical payload`, `explicit authorization`, `truth` — with no type and no owner behind it. The document's second pass should add fewer rules and more definitions: the two closed enumerations, the ledger grammar, the event envelope's assignment table, the Scope type, and the missing application layer that the three slices all need and none can name.
