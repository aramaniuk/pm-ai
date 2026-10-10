# Prototype path — one Graph connector and the daily dashboard

Written 2026-09-01, after story 2 merged. A re-phasing proposal, not a new
capability: it selects and slices existing `stories.yaml` work into the shortest
sequence that ends with a real `daily_dashboard.md` built from real Teams data,
and names one story the spec never had.

Companion to `roadmap-phasing.md`, which it does not replace. The roadmap says
what Phase 1 contains; this says what order to build a working prototype in and
what is deliberately left out of it.

## Baseline, measured

`uv run pytest` on `7316178`: **638 passed, 27 skipped**, clean tree.

Complete: stories `1a`-`1m` (13 specs) and `2a`-`2l` (12 specs) — scope
resolver, storage contract, keychain custody, envelope cipher, atomic and
durable writes, derived-tier rebuild, segmented event log with typed Markdown
entries, disclosure ledger, retrospective aggregation.

What is real versus scaffold, because the gap is smaller than the story count
suggests:

| Piece | State |
|---|---|
| storage, crypto, event log, disclosure ledger | real, tested, on disk |
| `app/wiring.py` `build()` → `Daemon` | real composition root; in-memory dicts for `meetings`/`transcripts` |
| `connectors/gitlab.py` | adapter shape only, no network, fabricated `CoverageWindow` |
| `connectors/transcripts/graph.py` | 22 lines against a `_fake_api` dict |
| `pm-ai` command | does not exist — no `[project.scripts]`, `surfaces/cli/__init__.py` is a docstring |
| daemon, loopback API | not implemented |
| `core/rendering`, `core/scheduler`, `models/router` | not implemented (9 of the 27 skips are the model router) |
| config loading | `config.toml` declared, read by nothing; no `tomllib` import in the tree |

So the prototype needs three things that do not exist — an entry point, a Graph
connector that reaches the network, and a renderer — on top of a foundation that
is finished.

## The four decisions this path rests on

Taken 2026-09-01. Each one cuts or adds work, and the sequence below is only
correct given all four.

1. **Maximum Teams surface** — one Graph connector covering chat/channel
   messages, meeting transcripts, and calendar. Wider than a minimal prototype,
   and chosen deliberately. It turns out more coherent than a pick-three:
   transcripts are reachable only for meetings tied to a calendar event, so
   calendar is the lookup path to transcripts rather than a third independent
   resource.
2. **Deterministic dashboard, plus a hand-authored goals file** — no model in
   the path. Three of four sections compute from real declared data; Leadership
   Notes stays an honest empty-with-reason. This removes story 7, the model
   router, the frontier adapter, and every disclosure-ledger write the dashboard
   would otherwise make.
3. **Full story 4** — daemon, loopback API, and REPL, split across the two waves
   rather than dropped.
4. **Delegated auth, device-code flow** — the PM signs in interactively; no
   client secret on the laptop, no hosted redirect URI, and no application
   access policy. **Corrected 2026-09-06 against slice 0:** this decision
   originally read "no tenant-admin consent", and that half was wrong. The
   delegated flow avoids the *app-only* path and the application access policy
   that path requires for transcripts — it does not avoid consent.
   `ChannelMessage.Read.All` and `OnlineMeetingTranscript.Read.All` normally
   require an administrator, and the grant slice 0 obtained came from one.
   Whether an ordinary PM can self-consent to the rest is unmeasured. The
   decision itself stands: admin consent is a one-time app-registration
   prerequisite, not a per-sign-in step, so it does not favour a different
   flow. It does mean a first enrolment needs an administrator to have granted
   all seven declared scopes first — see `33a`'s Always clause, which is the
   authority on the set.

## What was verified against live Graph docs

Checked 2026-09-01, because the design turns on it and the answer was not
something to assume.

- **Transcripts**: delegated `OnlineMeetingTranscript.Read.All` is sufficient.
  The application access policy that app-only requires does not apply to the
  delegated path.
- **Channel messages**: delegated `ChannelMessage.Read.All`. No protected-API
  approval and no metered licensing on `GET /teams/{id}/channels/{id}/messages`
  — those apply to `getAllMessages`, which this design does not use.

Two caveats that changed the design rather than merely informing it:

- **A tenant admin can disable Graph transcript access entirely**, returning
  `403` with inner code `GraphAccessToTranscriptsDisabled`. The docs state there
  is no request-side workaround. Whether this tenant permits it is unknown until
  probed, which is why slice 0 exists.
- **The channel-messages endpoint supports only `$top` and `$expand`** — no
  `$filter`, no `$orderby`. Incremental harvest cannot use a server-side cursor;
  it must page and cut client-side.

Sources:
`https://learn.microsoft.com/en-us/graph/api/onlinemeeting-list-transcripts?view=graph-rest-1.0`,
`https://learn.microsoft.com/en-us/graph/api/channel-list-messages?view=graph-rest-1.0`

## Structure — existing, changed, new

Three views of the same path. The first is the module map against the enforced
layer stack; the second is what happens when the dashboard is generated; the
third is the order the wave-1 slices can actually be built in.

Legend throughout: **unfilled** is in place and untouched, **amber** is existing
code this path modifies, **green** is new.

### Module map

Layer boundaries are the ones `.importlinter` enforces, not a drawing
convention. Dependencies point inward only; the sibling row may not import
across itself.

The enforced order, from `.importlinter`, is a stack and not a graph:

```
pm_ai.app                                                    outermost
pm_ai.surfaces
pm_ai.connectors : skills : storage : models : platform       siblings
pm_ai.core
pm_ai.ports
pm_ai.domain                                                 innermost
```

Mermaid lays `surfaces` beside the sibling row below rather than above it; the
stack above is the authority on the order.

```mermaid
flowchart TB
    subgraph L_app["pm_ai.app — composition root, may import every layer"]
        direction LR
        wiring["wiring.build → Daemon<br/>registers Graph, retires meetings dict"]
        pipes["pipelines.run_harvest"]
        dashpipe["pipelines.run_dashboard"]
    end

    subgraph L_surf["pm_ai.surfaces — reach adapters only through core"]
        direction LR
        scli["cli — entry point, subcommands"]
        sapi["api — daemon, loopback FastAPI<br/>wave 2"]
        srepl["cli — REPL, CAP-18 parity<br/>wave 2"]
        stg["telegram — untouched"]
    end

    subgraph L_adapt["connectors : skills : storage : models : platform — siblings, no cross-imports"]
        direction LR
        agraph["connectors.graph<br/>auth · calendar · messages · transcripts"]
        acreg["connectors.registry<br/>health probe"]
        agit["connectors.gitlab<br/>CoverageWindow fix"]
        astore["storage.service<br/>storage.crypto"]
        aplat["platform<br/>paths · keychain · vcs · doctor"]
        askill["skills<br/>registry · gitlab"]
        amod["models.local · models.frontier<br/>deferred, stays empty"]
    end

    subgraph L_core["pm_ai.core — I/O-free"]
        direction LR
        csan["sanitize · normalize<br/>8e retires the no-op"]
        cnew["rendering · goal_register<br/>meeting_records · config"]
        csched["scheduler — wave 2"]
        cexist["event_log · retrospective<br/>disclosure_ledger · extraction · ledger"]
    end

    subgraph L_ports["pm_ai.ports — imports only pm_ai.domain"]
        direction LR
        gport["GraphAuthPort"]
        cport["ConnectorPort · StoragePort · KeychainPort<br/>CryptoPort · ScopePathPort · SkillPort"]
    end

    subgraph L_dom["pm_ai.domain — imports nothing from pm_ai"]
        direction LR
        dev["events<br/>MessagePayload gains mentions"]
        dharv["harvest<br/>HarvestResult gains ran-and-learned-nothing"]
        dstable["meetings · goals · scope_model<br/>identity · lifecycle · event_entries"]
    end

    dashpipe --> scli
    dashpipe -.->|composition root only| agraph
    scli --> cnew
    agraph --> csan
    astore --> cport
    cnew --> cport
    csan --> dev
    cport --> dstable

    classDef new fill:#0f5132,stroke:#0a3622,color:#ffffff
    classDef chg fill:#664d03,stroke:#413003,color:#ffffff
    classDef defer fill:#495057,stroke:#343a40,color:#ffffff
    class scli,sapi,srepl,agraph,acreg,cnew,csched,gport,dashpipe new
    class agit,wiring,pipes,dev,dharv,csan chg
    class amod,stg defer
```

Two things the map makes visible that the prose does not:

- **The new code concentrates in three places** — `connectors.graph`, four new
  `core` modules, and the CLI surface. Everything below `core` is nearly
  untouched: two field additions in `domain`, one new port. A foundation that
  needs two new fields to carry a whole new provider is a foundation that was
  built right.
- **`models` stays empty.** Decision 2's cut is structural, not a matter of
  degree — no arrow enters that box anywhere in the path.

### Dashboard generation flow

What `pm-ai dashboard` does, and where each of the four sections gets its
content. `core.scheduler` replaces the CLI as the trigger in wave 2; nothing
else in this flow changes.

```mermaid
flowchart LR
    subgraph ext["Microsoft Graph — read-only, class H egress"]
        gapi_cal["/me/calendarView"]
        gapi_msg["channel + chat messages"]
        gapi_tr["/onlineMeetings/../transcripts"]
    end

    trigger["pm-ai dashboard"]
    tick["daemon 07:00 tick"]

    conn["connectors.graph — one ConnectorPort"]
    san["core.sanitize — AD-12, non-destructive"]
    attr["core.normalize — AD-36 provenance"]
    sv["storage.service — the single writer"]

    elog[("event_log/ segments")]
    meet[("meetings/ Tier-1 records")]
    goals[("strategic_goals.md — hand-authored")]

    rend["core.rendering — pure function"]
    out[("memory/daily_dashboard.md")]

    gapi_cal --> conn
    gapi_msg --> conn
    gapi_tr --> conn
    conn --> san --> attr --> sv
    sv --> elog
    sv --> meet

    trigger --> rend
    tick -.->|wave 2| rend
    meet -->|Time-Critical| rend
    elog -->|Proactive Enablement| rend
    goals -->|3-Tier Milestones| rend
    rend --> out

    classDef new fill:#0f5132,stroke:#0a3622,color:#ffffff
    classDef chg fill:#664d03,stroke:#413003,color:#ffffff
    classDef store fill:#084298,stroke:#052c65,color:#ffffff
    class conn,rend,trigger,tick,goals new
    class san,attr chg
    class elog,meet,out store
```

`san` and `attr` are amber because slice `8e` retires the discarded-return-value
no-op here and moves the guard to the model boundary, before any Graph text
passes through them.

### Wave 1 build order

Edges are hard dependencies, not preferences. Anything unconnected is
independent and can move.

```mermaid
flowchart LR
    s0(["slice 0 — spike, throwaway"])
    s1n["1n project artifacts machine-local"]
    s4a["4a config"]
    s4b["4b key enrol"]
    s4c["4c CLI + exit codes + doctor"]
    s4j["4j service subcommands"]
    s4d["4d project registry"]
    s4k["4k project add"]
    s4g["4g config writer"]
    s4i["4i config probe"]
    s4h["4h pm-ai setup"]
    s8a["8a harvest outcomes"]
    s8d["8d connector registry"]
    s8f["8f storage port capabilities"]
    s8b["8b credentials"]
    s8c["8c payload declarations"]
    s8e["8e model boundary"]
    s11a["11a meeting records"]
    s22a["22a goal register"]
    s22b["22b goal writer"]
    s33a["33a Graph auth"]
    s33b["33b Graph fetch"]
    s33c["33c Graph mapping"]
    s23a["23a dashboard sections"]
    s23d["23d scope wall"]
    s23b["23b dashboard pipeline"]
    done(["real daily_dashboard.md"])

    s0 --> s33a
    s4a --> s4c
    s4b --> s4j
    s4c --> s4j
    s8d --> s4j
    s4a --> s4d
    s4a --> s4g
    s4g --> s4h
    s4a --> s4i
    s4i --> s4h
    s4b --> s4h
    s4c --> s4h
    s4d --> s4k
    s1n --> s4k
    s4c --> s4k
    s4k --> s4h
    s4b --> s8b
    s4c --> s8b
    s8d --> s8b
    s8f --> s8b
    s8f --> s11a
    s8b --> s33a
    s8d --> s33a
    s8a --> s33b
    s33a --> s33b
    s11a --> s33c
    s33b --> s33c
    s22a --> s22b
    s4c --> s22b
    s22a --> s23a
    s11a --> s23a
    s23a --> s23d
    s4c --> s23b
    s23a --> s23b
    s23d --> s23b
    s33c --> s23b
    s4g --> s23b
    s23b --> done

    classDef crit fill:#664d03,stroke:#413003,color:#ffffff
    classDef island fill:#0f5132,stroke:#0a3622,color:#ffffff
    classDef goal fill:#084298,stroke:#052c65,color:#ffffff
    class s4a,s4c,s8b,s33a,s33b,s33c,s23b crit
    class s8c,s8e island
    class done,s0 goal
```

Derived from the dependency table in `deferred-work.md`, not drawn by hand, and
cross-checked both ways: 25 slices, **36 dependency edges** in the table and the
same 36 in the diagram, no edge in one and absent from the other, acyclic. The
count has moved five times — four on 2026-09-03 as the wave's specs were amended
against the second review and split at the sizing gate, and once on 2026-09-15
when `4g → 23b` was found missing; it is re-derived from the
table each time rather than adjusted by hand.

**The fifth move is the interesting one, because nothing was split to cause it.**
`display_timezone` was assigned to `4g` on 2026-09-03 and question 5 below records
in as many words that "`23b` reads it once and passes it to both consumers" — an
edge stated in prose, in a decision, and entered in neither the table nor this
graph. It survived two multi-lens reviews and eleven merged slices because every
re-derivation re-read the table, and the table was where the omission was. A
dependency recorded only in a decision's prose is not recorded.

Amber is the critical path, **seven slices**:
`4a → 4c → 8b → 33a → 33b → 33c → 23b`. It runs through the CLI rather than the
key, because `8b` adds `connector add` to the dispatch table `4c` creates — an
edge the first draft of this graph missed entirely.

Green is two islands, not a chain: `8c` and `8e` have no dependants in wave 1
and, since 8e's 2026-09-02 renegotiation, no dependency on each other. 8e
declares the chokepoint — `ModelPort` accepting only `Sanitized` — and reads no
declaration; a caller gathering text for a prompt reads 8c's. Both are needed
before `33d` in wave 2, where the first genuinely untrusted third-party text
arrives, and before story 7 puts a model behind the port. They sit in wave 1
because the defect is live on the GitLab path today and cheapest to close while
nothing can yet bypass the port.

Nine slices have no dependencies at all: `1n`, `4a`, `4b`, `8a`, `8c`, `8d`,
`8e`, `8f`, `22a`. `4a` is already implemented, so eight can start today. `8e`
joined them when its edge from `8c` was removed; `8f` and `1n` were carved out of
`8b` and the scope model on 2026-09-03. `22a` in particular runs parallel to the
entire Graph chain, since the renderer takes a goal register and a clock and does
not care where meetings came from — `11a` no longer joins it there, having gained
a dependency on `8f`'s collection listing, which `for_day` needs.

**`1n` is the one with an ordering constraint rather than a dependency.** It must
land before `4k`: after it, a project directory that already committed
`.project-ai/memory/` can only be fixed with `git rm --cached`, because AD-43's
third row states that adding an ignore rule does not untrack what is already
tracked. Nothing is deployed, so no such directory exists — which stays true only
if the order holds.

**`4c` precedes `4k`, not `4d`.** The circularity the review found was real
while one slice held both the registry and its command: `4d` added `project add`
to the dispatch table `4c` creates. Splitting them on 2026-09-03 dissolved it —
`4d` is the registry, its reader and its probe, and needs only `4a`; `4k` is the
command and needs `4c`. So `4d` is startable immediately and `4k` waits.

**`4h` has four prerequisites and no dependants.** It sequences `4b`'s
enrolment, `4d`'s registration, `4g`'s writer and `4i`'s probe, so it cannot
start until all four exist — and because nothing depends on it, it never delays the critical
path. `4g` and `4i` need only `4a`, so both can land while `4b` and `4d` are still
in flight; that asymmetry is why the group was split at the sizing gate.

## Connector design

### Nothing in the domain has to change

The closed enumerations already cover this connector. `ObservedEventType` holds
`MESSAGE_POSTED` and `CALENDAR_EVENT_HELD`, and `PAYLOAD_FOR`
(`domain/events.py:145`) already binds them to `MessagePayload` and
`MeetingHeldPayload`. The scope model already declares every artifact the path
writes: `memory/daily_dashboard.md` and `strategic_goals.md`
(`domain/scope_model.py:540,544`), `meetings/`, `connectors/`, and the encrypted
`private/config.json`. No closed enumeration opens, and no scope tree changes.

The one exception is stated under "Renderer design" below: `MessagePayload`
needs a `mentions` field.

### Auth

`GraphAuthPort` in `pm_ai/ports/`; MSAL device-code adapter in
`pm_ai/connectors/graph/auth.py`.

**Not in `pm_ai/platform`**, which an earlier draft of this document proposed on
the grounds that AD-26 puts the OS boundary there. That was wrong. The
`http-confined-to-adapters` contract forbids `httpx`, `requests` and `aiohttp`
in `pm_ai.platform`, and states the intent plainly: "Only inbound connectors and
outbound skills may speak HTTP at all." Device-code flow talks to the Microsoft
token endpoint, so it is HTTP and belongs in `connectors`.

The distinction the two layers draw here is worth stating, because it is easy to
collapse: token **custody** is `platform` — the macOS Keychain behind
`KeychainPort`, built by story 1d. Token **acquisition** is `connectors` — an
HTTP conversation with a provider. `connectors/graph/auth.py` obtains, and
stores through the ports it is given.

Note that import-linter would not have caught the mistake: MSAL reaches
`requests` transitively, and the contract lists direct imports. A violation that
passes the gate is worse than one that fails it, which is the reason this is
recorded rather than silently fixed. Whether the contract should name `msal`
directly is left as a question for `33a`.

`msal` is a new runtime dependency and must be pinned in `pyproject.toml`
alongside the existing stack, per the pinning discipline the `anthropic` entry
documents.

`pm-ai connector add --type graph` prints the device code and URL; the PM signs
in in a browser. Scopes: `Calendars.Read`, `Chat.Read`,
`ChannelMessage.Read.All`, `OnlineMeetingTranscript.Read.All`, `offline_access`.

**Write ordering, per story 8's rule.** The refresh token goes to the encrypted
`~/.pm-ai/private/config.json` FIRST, then `connectors/graph.json` at 600. The
key is fetched lazily, so the encrypted write is the one that can refuse; that
order leaves nothing behind on refusal, while the reverse leaves a connector
configured, enabled, and holding no credential — which reads as a working
connector harvesting nothing.

### One connector, three resources

`GraphConnector` satisfies the existing `ConnectorPort` unchanged: `name`,
`system`, `emits()`, `harvest(since) -> HarvestResult`. `emits()` returns
exactly `{CALENDAR_EVENT_HELD, MESSAGE_POSTED}`.

Three resource fetchers behind one connector rather than three connectors: they
share auth, tenant, and cursor, and splitting them would triple the credential
lifecycle for no gain.

### The cursor

Each resource paginates differently, which is precisely why `Cursor.token` is
opaque bytes exposing no `.page` or `.timestamp`:

- `calendarView` — server-side `$filter` on a date range.
- channel and chat messages — no server-side filter available, so page until the
  last-seen watermark is crossed and cut client-side.
- transcripts — per meeting, discovered through calendar.

`Cursor.token` carries an opaque composite, one position per resource.

### Coverage told honestly

`connectors/gitlab.py:62-71` builds its `CoverageWindow` as `now - 4h` to `now`
unconditionally, tied to nothing that proves a fetch happened — so a provider
declining with an empty 200 claims full coverage it never had.

This path fixes that before Graph inherits the pattern. `CoverageWindow.start`
becomes the earliest point actually paged back to and `end` the moment the fetch
finished, both derived from returned pages. `HarvestResult` gains the "ran and
learned nothing" state story 8 asks for.

The transcript 403 makes this load-bearing rather than tidy: a tenant with
transcripts disabled must record *looked-and-refused*, or story 16's three
verdicts later read it as *nothing happened*.

### Sanitization does not currently happen (slice 8e)

Found 2026-09-01 while verifying this document's own claims, and it is a silent
fault rather than a gap.

`app/pipelines.py:26-28` carries the comment "AD-12 — sanitization at the
boundary, uniformly, outside the connector" above:

```python
for event in result.events:
    sanitize(getattr(event.payload, "message", "") or "")
```

Two independent faults:

1. **The return value is discarded.** `sanitize` is a pure function returning a
   `Sanitized(raw, for_model)` pair with no side effects, so the loop computes a
   value and drops it. Nothing downstream ever sees `for_model`. The comment
   describes an invariant the code does not hold.
2. **It reads a field most payloads do not have.** `message` exists on
   `CommitPayload` only. `MessagePayload` — what the Graph connector emits —
   carries `channel` and `excerpt`, so the `getattr` falls back to `""` and
   sanitizes an empty string. Every Teams message body would pass through
   untouched.

Why it is deferrable but not ignorable: under decision 2 no model is in the
path, so nothing harvested reaches a prompt and the injection vector is inert
today. But Teams message bodies are the most injection-prone input in this
design — arbitrary HTML-formatted text from anyone in the tenant — and the AD-12
comment currently asserts a protection that does not exist, which is worse than
having neither.

The fix: sanitize the text field the payload actually declares rather than a
hardcoded name, and persist both halves of the `Sanitized` pair per AD-29 so a
later consumer has `for_model` without re-deriving it. The exact on-disk shape
ties into open question 3, since story 2l put payload content into Tier 1.

Placed in wave 1 rather than beside `33d`: it is a live defect on the existing
GitLab path too, and `8a` is already in the harvest plumbing.

### Transcripts

Chain: `calendarView` event → `onlineMeeting.joinUrl` →
`/me/onlineMeetings?$filter=joinWebUrl eq '<url>'` → meeting id →
`/transcripts` → `/content`. Replaces `graph.py`'s `_fake_api`.

On `GraphAccessToTranscriptsDisabled` the connector degrades to two resources
and reports it through the health probe, rather than failing the harvest.

### Upcoming meetings are not events

`CALENDAR_EVENT_HELD` is past tense, and `MeetingHeldPayload` carries no title
or start time. Upcoming meetings therefore cannot be expressed as events without
opening a closed enum, which a connector may never do.

They are `meetings/` Tier-1 records instead. The `Meeting` entity already
carries `start`, `title`, and `attendees`. Calendar harvest writes Meeting
records for what is coming, and emits `CALENDAR_EVENT_HELD` only once a meeting
has ended. The dashboard's Time-Critical section reads `meetings/`, never
`event_log/`.

This also retires the in-memory `daemon.meetings` dict.

## Renderer design

`pm_ai/core/rendering.py`, which the architecture already anticipated — it is
one of the 27 skipped tests.

**A pure function**: `render_dashboard(meetings, entries, goals, now) -> str`.
No I/O in `core`; `app/` wires it to storage, as the harvest pipeline does.
Injected clock, per story 1b. Golden-file tested.

**Output** through `StorageService.write_artifact` to the personal scope's
`memory/daily_dashboard.md`. Whole-file replace is correct: it is a rendering,
not a ledger, so `write_artifact`'s ledger refusal does not apply.

**Declared inputs**, named now so story 10a's derived job graph can adopt this
later without redesign: `meetings/`, `event_log/` (through story 2h's
`EventLog.read`), `strategic_goals.md`.

### The four sections

| Section | Source |
|---|---|
| Time-Critical Activities | `meetings/` where `start` falls today, ordered by start |
| Proactive Enablement | `MESSAGE_POSTED` entries from the last 24h mentioning the PM, or an unanswered question |
| 3-Tier Strategic Milestones | `strategic_goals.md` grouped by `GoalHorizon` |
| Leadership Notes | honest empty-with-reason in the prototype |

### "3-Tier" resolved

`GoalDomain` (project/team/personal) and `GoalHorizon` (short/medium/long) both
have exactly three values, and CAP-9 says only "3-Tier".

**Read as horizon.** The section is about *milestones* — when things land — and
`GoalHorizon`'s docstring ties it to UJ-9's planning breakdown, while
`GoalDomain` is documented as the `<Tier>` in `[Strategic Alignment: <Tier>]`, a
different job. Recorded here because it is a real ambiguity in the source, and a
reader should be able to see it was decided rather than assumed.

### `MessagePayload` gains one field

It carries only `channel` and `excerpt` (`domain/events.py:110`), but Proactive
Enablement needs Graph's `mentions[]` to know a message is aimed at the PM.

Extending a payload dataclass is legal — AD-27 closes the *type* enumeration,
not the payload shapes — but story 2l put payload content into Tier 1, so a new
field changes the on-disk entry format and takes the same care stories 2c and 2d
took. Small, but not free, and named rather than smuggled in.

**Decided 2026-09-27** (AD-27, AD-48): the field holds the Graph user ids of the people mentioned and defaults to `None`; whether the PM was mentioned is worked out when read. The entry grammar grows additively with no version marker, and lists are written comma-joined with escaping. A list must be declared like a text field; for now only as trusted, so `mentions` is declared trusted because Microsoft generates the ids, and an outside-text list is refused until a slice brings a real one. What `33d` owes before its first real line is at the end of `deferred-work.md`.

### No fabricated content

The renderer never invents. A section with no data states the computed reason —
"No meetings on your calendar today", "No strategic goals declared — author
strategic_goals.md" — never "All clear!", which would be a claim nothing
measured.

## Deviations from the spec, recorded

Both are deliberate, and both are recorded here rather than quietly satisfied,
following the precedent story 2 set when it corrected CAP-10's "JSON line" to
Markdown in `SPEC.md` rather than in the four sources that agreed.

1. **CAP-9's "no empty section" is not met, in two sections rather than one.**
   Leadership Notes will not fill, because filling it needs synthesis and
   decision 2 removed the model from the path. **And Proactive Enablement will
   not fill in wave 1**, because it reads `MESSAGE_POSTED` and `33b`'s `emits()`
   is exactly `{CALENDAR_EVENT_HELD}` — messages arrive with `33d` in wave 2.
   Corrected 2026-09-02 by the spec review; this document originally claimed one
   gap. The renderer states each reason instead of padding.
3. **Time-Critical lists only meetings that have not ended.** This document said
   "`meetings/` where `start` falls today"; `23a` filters ended meetings out, so
   an afternoon run says "all N of today's meetings have ended" rather than
   listing them. Recorded 2026-09-02.
2. **CAP-9's 07:00 deadline is not met in wave 1.** Wave 1 renders on
   `pm-ai dashboard` and has no scheduler. The deadline clause arrives in wave 2
   with slice 9a's scheduled tick.

## Decomposition

Letter slices under the story that owns the capability, following the
convention stories 1 and 2 used. Spec checkpoint and done checkpoint on every
slice; one commit per slice.

### Story 33 — new

The Graph connector has no story in `stories.yaml`. Story 8 is the connector
*framework*; Graph is an *instance* of it, and instances of CAP-35 were left
unenumerated. Story 33: "Microsoft Graph connector — calendar, chat,
transcripts."

### Slice 0 — spike, throwaway

Device-code sign-in against the real tenant; one call each to `calendarView`,
channel messages, and transcripts. Reports what comes back. No code kept.

Answers three unknowns: whether the tenant permits transcripts at all, which
scopes consent cleanly, and what the real payload shapes are. Slice 33e's scope
depends on the first answer.

### Wave 1 — a real dashboard from a real calendar (25 slices)

| Slice | Delivers |
|---|---|
| `1n` | Four project artifacts and `memory/` become gitignored — the wave's only code change. Must precede `4k`. |
| `4a` | Config loading — `tomllib`, `config.toml`. Explicitly not the encryption toggle. |
| `4b` | `pm-ai key enrol` through KeychainPort; the daemon never mints. Retargets 1g's "key absent" remediation. |
| `4c` | CLI entry point, the dispatch and exit-code tables, and `doctor`. No REPL yet. |
| `4j` | The three service leaves: `key enrol`, `config show`, `connector check`. |
| `4d` | `projects.toml` parsed, rendered, read by `build()`, reported by `doctor`. **Added by the spec review.** |
| `4k` | `pm-ai project add <path> [alias]` — creates the tree, generates `.gitignore`, adopts an existing one. |
| `4g` | A writer for `config.toml` — `render_config` beside `load_config`. |
| `4i` | The sixth `doctor` probe: what state `config.toml` is actually in. |
| `4h` | `pm-ai setup` — enrol, register, prompt, write, then report `doctor`. First boot to green. |
| `8a` | `HarvestResult`'s three outcomes and the `CoverageWindow` fix in `gitlab.py`. |
| `8d` | Connector registry, the port's two new members, and per-connector health probes. |
| `8f` | `StoragePort` declares artifact I/O and a collection listing; a declared file mode. |
| `8b` | Credential lifecycle — `pm-ai connector add`, encrypted-write-first, 600. |
| `8c` | Each payload class declares its untrusted text fields, guarded at import. |
| `8e` | Sanitization binds where it can be enforced: `ModelPort` accepts only `Sanitized`. See below. |
| `11a` | Meeting records reach Tier 1 through `meetings/`; retires the in-memory dict. |
| `33a` | `GraphAuthPort` and MSAL device-code adapter, refresh, stale-credential health reporting. |
| `33b` | Graph calendar fetch — paging, throttling, `{dateTime, timeZone}` → aware UTC, honest coverage. |
| `33c` | Calendar rows → Meeting records and `CALENDAR_EVENT_HELD`; `ConnectorPort` conformance. |
| `22a` | Goal register parsed from `strategic_goals.md`; hand-edit tolerant, unparseable lines surfaced not dropped. |
| `22b` | `render_goals`, `pm-ai goal set`, and the `goal_set` event-log entry. |
| `23a` | `core/rendering.py` — the four sections, honest gaps, golden-file tests. |
| `23d` | `render_project_dashboard` — a separate renderer whose signature *is* AD-25's wall. |
| `23b` | `pm-ai dashboard` wiring in `app/`: meetings + event_log + goals → render → `write_artifact`. |

Wave 1 ends with a real `~/.manager-ai/memory/daily_dashboard.md` built from the
PM's actual calendar and actual goals. **This is the working prototype, and a
legitimate point to stop and reassess.**

It also ends with `pm-ai` genuinely installable: `pm-ai setup` walks a clean
machine to a configured one — key enrolled, project onboarded, `config.toml`
written — and `pm-ai doctor` reports whether it worked. That was not in the
original plan; it arrived on 2026-09-03 when the first-run experience was
requested, and it is why the wave grew from seventeen slices to twenty-five.

Ordering constraints inside the wave, as the build-order graph below derives
them: `4a` and `4b` precede everything, because nothing runs without config and a
key; `4c` precedes `4j`, `4k` and `8b`, all of which add subcommands to the
dispatch table it creates; `8d` precedes `8b` and `33a`, which register into it; `8a`
precedes `33b` so Graph inherits a `HarvestResult` that can report an honest
outcome; `11a` precedes `33c` because the mapping writes Meeting records, and
precedes `23a` because Time-Critical reads them; `22a` precedes `23a` because the
renderer takes a goal register; `23a` precedes `23d`, which adds the second renderer
to the same module and reuses its section renderers; and `8c` and `8e` are two independent slices with no
dependants in this wave and, after 8e's renegotiation, no edge between them.

### Wave 2 — the full surface (7 slices)

| Slice | Delivers |
|---|---|
| `33d` | Chat and channel messages — client-side cursor, HTML→text, `MessagePayload.mentions`. |
| `33e` | Transcript resource — `joinUrl` → `onlineMeetings` → `/content`; 403 degradation. Replaces `_fake_api`. |
| `23c` | Proactive Enablement fills from real message events. |
| `4e` | Daemon and loopback-only FastAPI binding. |
| `4f` | REPL at CAP-18 parity, under 1.0s startup. |
| `9a` | Scheduled harvest — 240min ±15, exponential backoff, the missing `try/except` story 9 names, and the 07:00 render tick. |
| `11b` | Real transcript path wired into the existing extraction pipeline. |

### Before wave 2 — added 2026-09-27

Wave 1 is complete, together with six slices it did not list: `8g`, `8h` and `8i`
on the sanitization and coverage path, `4l` (every enrolled project is watched),
`1o` (Python 3.14 exactly) and `1p` (running with encryption off is recorded on
the first protected write). None of the seven wave-2 slices has a spec or a
`stories.yaml` entry yet, and they cannot all be specified correctly today. The
list below was compiled on 2026-09-27 from the open entries in
`deferred-work.md`, the architecture spine's Still-open, Open Risks and Deferred
sections, and the slice-0 spike. Line numbers are as of that date.

The spike's biggest question is already answered: the tenant returned
transcripts with HTTP 200 (`slice-0-graph-spike-2026-09-06.md:15-26`), so `33e`
and `11b` stay in the plan, and the 403 degradation is still required because
the switch is the tenant's.

**Phase 1 — decisions, no code.** Each blocks the slice named.

1. **How event-log lines are versioned** (blocks `33d`, and `23c` through it).
   AD-27's versioning clause was withdrawn with nothing in its place; the
   options on record are a token per line, a header per monthly segment, or a
   dated table. `33d`'s `MessagePayload.mentions` is the first real change to
   the line format, which is where question 3 below said the decision becomes
   unavoidable. `deferred-work.md:275-284`, spine `:1036-1037`.
2. **How `mentions` is labelled as outside text** (blocks `33d`). The rule for
   declaring untrusted fields covers single strings; a list of strings is a
   named hole. Decide with step 1. Spine `:1000`.
3. **Which record wins when one meeting has two** (blocks `33e`, `11b`). A
   hand-dropped transcript matched by title and start time and a Graph
   transcript matched by calendar event can each create a record for the same
   meeting. Spine `:1097`.
4. **What kind of file the dashboard is** (blocks `9a`). It is declared Tier 1
   and hand-editable, yet every render replaces it whole, so a 07:00 scheduled
   render would erase a hand edit daily. Either it becomes rebuildable, or a
   render stops replacing it. Spine `:1092`.
5. **The 5-second rule** (blocks `4e`, `4f`). AD-21 says anything slower than
   5 seconds answers at once and delivers later, on both surfaces;
   `connector check` waits up to 10 seconds by design. Either build the
   deliver-later mechanism or narrow the rule. Spine `:1093`.

Two smaller choices belong inside their slice's spec rather than here: a length
cap for the Proactive Enablement section (`23c`, `deferred-work.md:656-658`),
and whether the message window is "the last 24 hours" or "since the last render"
— they differ after a missed weekend (`9a`, `23c`, `deferred-work.md:660-662`).

**Phase 2 — code before wave 2**, one slice each.

6. **AD-11's project selection order** (needed by `4e`, `4f`, `9a`). A project
   named with `--scope project:<id>` wins over the folder. Today the project is
   chosen before the command line is read, so with several projects enrolled the
   personal dashboard and `goal set` are refused outside every project folder —
   a live bug. A daemon has no working directory, so the scheduled render needs
   this. `deferred-work.md:846-850`, spine AD-11.
7. **Save refreshed Graph tokens** (needed by `4e`, `9a`). A refresh token the
   provider rotates mid-run lives only in memory, and the sealed credential
   store has no update path; two parts of the code also each treat the
   credential file as theirs. `deferred-work.md:612-614`, spine `:1098`.
8. **One connector list** (needed by `4e`). The composition root installs its
   registry over a process-wide global and `connector check` reads the global,
   so the daemon and the CLI can disagree and two daemons cannot share a
   process. `deferred-work.md:470-472`, `:806-808`.
9. **`pm-ai connector add` must create a working Graph connector** (needed by
   `33d`, `9a`). Its probe table holds GitLab only (`connectors/probe.py`), and
   the entry it writes lacks the Graph settings. `deferred-work.md:558-560`,
   `:616-618`.

Renaming one of the two modules called `graph` (`deferred-work.md:562-564`) is
small enough to be `33e`'s first step rather than a slice of its own.

**Phase 3 — outside prerequisites.**

10. **Admin consent** for all seven Graph permissions on any tenant other than
    the spike's, where an administrator granted them. Whether an ordinary user
    can consent to `ChannelMessage.Read.All` and
    `OnlineMeetingTranscript.Read.All` is unmeasured. Spike `:61-65`.
11. **Xcode Command Line Tools** on the build machine: under Python 3.14,
    `watchdog` has no macOS wheel and builds from source when the runtime extra
    is installed, which `4e` needs. Spine `:1128`.

**Phase 4 — the wave-2 specs, in build order.** What each spec must include
beyond the table above:

12. **`33d`, then `23c`.**
    - `33d`: convert HTML to text; paging rules that differ per endpoint
      (spike `:122-134`); real fixtures with personal details removed
      (`deferred-work.md:574-576`); message text reaching log files at `0644`
      rather than the declared `0600` (`:690-692`); proof on each result that a
      fetch happened (`:820-822`).
    - `23c`: a failed event-log read must not render as "no signals"
      (`:674-676`); sender display names must survive, which today they do not
      because the alias table is never saved (`:598-600`).
13. **`33e`, then `11b`.**
    - Both: build `TranscriptSourcePort`, which AD-23 requires and which exists
      only as a docstring (spine `:315-317`); declare Graph's refusals beside it
      (`deferred-work.md:608-610`); match transcripts to occurrences of a
      recurring meeting by time, fetch content separately, and let the 403
      degrade rather than retry (spike `:43-57`, `:144-158`). A project meeting
      nobody tagged is filed personally and cannot move later (spine `:1088`).
    - `11b` only: confirm the transcript path reaches a model only through
      `ModelPort` (`deferred-work.md:379-381`), and type `Extraction.detail`,
      which is an untyped dict built from raw text (`:748-750`).
14. **`4e`, then `4f`.** There is no daemon today: `surfaces/api/` is a
    docstring and `pm_ai` imports no `asyncio`. Writes are kept in order by a
    cross-process file lock no AD names, which `4e` must document or replace
    (spine `:1089`). A writer that lives as long as the daemon also closes the
    two writers that skip the encryption-off warning (spine `:1127`).
15. **`9a`, last.** Its story text must label it temporary, because `10a`'s
    durable queue replaces it (see Deferred, below). It adds the missing
    `try/except` (`pipelines.py:69-110`), replaces the dashboard's harvest that
    starts from an empty cursor and sleeps inline (spine `:1091`,
    `deferred-work.md:710-712`), and handles the coverage row every harvest now
    writes and nothing prunes (`:816-818`). A key unreadable after an OS
    upgrade silently stops the briefing (spine `:1132`).

**Housekeeping, any time.**

16. Two stale notes in `stories.yaml`: `:716-719` still reads "3-Tier" as a time
    horizon (settled as domain on 2026-09-03, question 2 below), and story 8's
    note still says an empty answer earns no coverage (outdated since `8i`).

Left out on purpose: about 80 other open `deferred-work.md` entries and about 30
architecture items that do not touch wave 2.

### Status of the checklist — 2026-10-10

Every step above is done, decided, or verified on this machine; the wave-2 specs
are written and reviewed, and the remaining work is building them.

| Step | State |
|---|---|
| 1–2 | Done 2026-09-27 (AD-27 additive grammar, `mentions` as Graph user ids; AD-48 lists). |
| 3 | **Decided 2026-10-09 (Andrei):** one record per meeting per scope, the first keeps its id; a later hand drop binds by title and start, a later calendar harvest adopts a manual-only record. AD-33. Built by `33e`/`11b`. |
| 4 | **Decided 2026-10-09 (Andrei):** the dashboard is a render product — every render replaces it, no edit survives — so it is Tier 3. AD-3/AD-44/AD-47. The code flip is owed by `9a`. |
| 5 | **Decided 2026-10-09 (Andrei):** AD-21 binds the daemon's request path; foreground commands declare their own bound. |
| 6–9 | Done 2026-10-08/09 (`4m`, `8j`, `8k`, `8l`). Three leftovers split from step 9 are specified: `8m` re-sign-in (with `8q`), `8n`/`8o` GitLab, `4n`/`4p` setup. |
| 10 | Outside: admin consent on another tenant is still unmeasured. None of the wave-2 slices adds a scope, so the spike's grant covers them; `33e` checks whether `OnlineMeetings.Read` is already declared. |
| 11 | **Verified 2026-10-09:** `uv sync --extra runtime` built `watchdog` 6.0.0 from source under Python 3.14.7 and the FSEvents backend loads — on this machine, which runs an x86_64 interpreter under Rosetta (see `deferred-work.md`). |
| 12–15 | **Written and reviewed 2026-10-09/10**, under `_bmad-output/specs/spec-pm-ai/stories/`, every one through the three review lenses with the accepted findings applied. Build order below. |
| 16 | Done 2026-10-09 (both notes corrected in `stories.yaml`). |

**Wave-2 build order** (each spec names what it lands after):

1. `1q` ledgers at their declared mode · `1r` one claim primitive for whole-file writes (added at the 2026-10-10 architecture gate; before any new whole-file path) · `2m` payload line grammar · `8p` a result says when the provider answered — four small foundations, independent of each other.
2. `8q` the sign-in records who signed in · `8m` Graph re-sign-in · `4o` commands without a project · `4n` doctor and setup name the connectors · `4p` setup offers a connector.
3. `33d` channel messages (after 1q, 2m, 8p) · `33f` listed chats (after 33d) · `23c` Proactive Enablement from messages (after 2m, 33d, 8q, 4n).
4. `33e` Graph transcripts · `11b` the transcript path (after 33e) · `11c` the transcript drop command (after 11b).
5. `4e` daemon and loopback API · `4f` the REPL run locally (independent of 4e) · `4r` a line is answered or acknowledged (after 4e) · `4q` the REPL through the daemon (after 4e, 4f, 4r) · `9a` scheduled harvest (after 4e; temporary scheduler, replaced by 10a) · `9b` the morning render (after 9a; flips the dashboard to Tier 3).
6. `8n` GitLab commit transport · `8o` connector add gitlab (after 8n) — when convenient; not on wave 2's critical path.

The wave grew from seven slices to twenty-four: the reviews split what was two
failure classes (`33d`/`33f`, `4n`/`4p`, `8n`/`8o`, `9a`/`9b`), carved out what was
independently shippable (`2m`, `8p`, `8q`, `1q`, `1r`, `4r`, `4q`, `11c`), and added the three leftovers
from step 9 and the `4o` decision.

## Deferred, with reasons

- **Story 3 — MCP execution firewall.** Deferred because the prototype mutates
  nothing external and, under decision 2, puts no model in the path — so no
  harvested text reaches a prompt. It becomes a **hard prerequisite** the moment
  either changes; nothing in wave 1 or 2 does.

  The rationale is *not* that sanitization already works. It does not — see
  slice `8e`. An earlier draft of this document claimed the harvest boundary
  sanitizes; that claim was wrong and the correction is why `8e` exists.
- **Story 7 — Whisper and Ollama.** Decision 2 removed every model from the
  path. Transcript extraction does not need one: `core/extraction.py` is regex,
  not model-backed.
- **Stories 10 and 10a — durable queue, task manager, file watcher.** This is
  the one real debt the path takes on. 10a's rule is "every job is a queue row
  per AD-20, never an in-memory timer", and slice 9a's tick is exactly that
  timer. **9a's story text must label it temporary**, or it becomes the thing
  nobody remembers to replace.
- **Stories 12-21 and 24-32.** Untouched.

## Cost, stated plainly

Stories 1 and 2 were 25 slices and built the storage, crypto, and log
foundation. This path is 24 slices plus a spike — the same order of magnitude
again. "Shortest" means shortest *given the four decisions above*, not small.

The shortest path to something working is wave 1 alone: 25 slices, one of them (`4a`) already implemented.

## Open questions

1. **Does the tenant permit Graph transcript access?** Slice 0 answers it. A
   `403 GraphAccessToTranscriptsDisabled` removes slice 33e and `11b` from the
   plan entirely and there is no workaround.
2. **Is "3-Tier" horizon or domain?** **Domain**, and the earlier answer here —
   "decided as horizon" — was wrong. Settled against the source on 2026-09-03:
   `prd.md:63` names `strategic_goals.md` as "3-Tier Goals (Project, Team,
   Personal Career Goals)", and `prd.md:424` says the domain "is what a goal is
   *about*, and it is the `<Tier>` in the alignment tag, matching §2.1's '3-Tier
   Goals'". `alignment_tag`'s docstring (`goals.py:99-104`) was right all along;
   the word "Milestones" in CAP-9's section title is what misled both this entry
   and `23a`. Corrected in `23a`.
3. **Does a payload gaining a field need an operational schema version bump?**
   **Answered 2026-09-02: no, and story 1i is the wrong owner.** `SCHEMA_VERSION`
   (`service.py:133`) describes `operational.db`'s table shape, and a field added
   to a Tier-1 Markdown line changes no column — the only thing the harvest write
   path puts in SQLite is a `seen` dedup key. The version that would govern an
   entry line is AD-27's entry grammar, which does not exist: 2c withdrew
   `GRAMMAR_VERSION` after finding it written nowhere and read nowhere. `8e` no
   longer persists `for_model` at all (see question 6), so what remains live is
   `MessagePayload.mentions` in `33d` — the first slice that genuinely widens the
   entry grammar, and the point at which AD-27's unmade design decision must be
   taken rather than deferred.
4. **How should a payload declare which of its fields is sanitizable text?**
   Decided in `8c` at review: keyed by payload **class**, not event type, since
   `ReviewPayload` serves two types; validated against the dataclass at import;
   refused by a typed error, never an `assert`, which
   `test_guards_survive_o.py:174-181` forbids anywhere in `pm_ai/`.
5. **What display timezone owns "today"?** **Answered 2026-09-03: a fourth
   `config.toml` key, `display_timezone`.** It had no owner in any story —
   `render_dashboard` and `for_day` both took a `tz` and nothing supplied one,
   `4a` had closed the vocabulary at three keys and `4h` forbids prompting for
   anything `4a` does not accept. The key lands in `4g`, because a loader and a
   renderer that disagree about a file format is the drift pair `4g` exists to
   close; `23b` reads it once and passes it to both consumers, and an unset zone
   is refused rather than defaulted to UTC.
6. **Does persisting `for_model` bump the operational schema version?**
   **Closed 2026-09-02 by removing the premise.** The question was a category
   error (see question 3), and examining it retired the design that raised it:
   AD-12 already requires the guard at the consumer through a `ModelPort`
   accepting only `Sanitized`, no such port existed anywhere in `pm_ai/`, nothing
   in the package referenced `Sanitized` except the module defining it, and
   AD-31's audit record is scope provenance in the disclosure ledger rather than
   the sanitized text. `8e` now declares the port and derives the copy at the
   point of use, persisting nothing and leaving the entry grammar untouched.
