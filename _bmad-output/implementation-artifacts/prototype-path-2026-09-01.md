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
   tenant-admin consent, no application access policy.

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
`pm_ai/platform/graph_auth.py`, because AD-26 puts the OS and network boundary
in `platform`.

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

### Sanitization does not currently happen (slice 8c)

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

Placed in wave 1 rather than beside `33c`: it is a live defect on the existing
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

### No fabricated content

The renderer never invents. A section with no data states the computed reason —
"No meetings on your calendar today", "No strategic goals declared — author
strategic_goals.md" — never "All clear!", which would be a claim nothing
measured.

## Deviations from the spec, recorded

Both are deliberate, and both are recorded here rather than quietly satisfied,
following the precedent story 2 set when it corrected CAP-10's "JSON line" to
Markdown in `SPEC.md` rather than in the four sources that agreed.

1. **CAP-9's "no empty section" is not met.** Leadership Notes will not fill in
   the prototype, because filling it needs synthesis and decision 2 removed the
   model from the path. The renderer states the reason instead of padding.
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
scopes consent cleanly, and what the real payload shapes are. Slice 33d's scope
depends on the first answer.

### Wave 1 — a real dashboard from a real calendar (12 slices)

| Slice | Delivers |
|---|---|
| `4a` | Config loading — `tomllib`, `config.toml`. Explicitly not the encryption toggle. |
| `4b` | `pm-ai key enrol` through KeychainPort; the daemon never mints. Retargets 1g's "key absent" remediation. |
| `4c` | CLI entry point and subcommand dispatch. `[project.scripts] pm-ai`. No REPL yet. |
| `8a` | Connector registry, 10s health probe, `HarvestResult` "ran and learned nothing", and the `CoverageWindow` fix in `gitlab.py`. |
| `8b` | Credential lifecycle — `pm-ai connector add`, encrypted-write-first, 600. |
| `8c` | Sanitization actually binds at the harvest boundary. See below. |
| `11a` | Meeting records reach Tier 1 through `meetings/`; retires the in-memory dict. |
| `33a` | `GraphAuthPort` and MSAL device-code adapter, refresh, stale-credential health reporting. |
| `33b` | Graph calendar resource → Meeting records, and `CALENDAR_EVENT_HELD` for ended meetings. |
| `22a` | Goal register parsed from `strategic_goals.md`; hand-edit tolerant, unparseable lines surfaced not dropped. |
| `23a` | `core/rendering.py` — four sections, honest gaps, golden-file tests. |
| `23b` | `pm-ai dashboard` wiring in `app/`: meetings + event_log + goals → render → `write_artifact`. |

Wave 1 ends with a real `~/.manager-ai/memory/daily_dashboard.md` built from the
PM's actual calendar and actual goals. **This is the working prototype, and a
legitimate point to stop and reassess.**

Ordering constraints inside the wave: `4a` and `4b` precede everything (nothing
runs without config and a key); `8a` precedes `33a` so Graph inherits a fixed
`HarvestResult` rather than a fabricated one; `8c` precedes `33c`, where the
first genuinely untrusted third-party text arrives; `11a` precedes `33b` because
calendar harvest writes Meeting records; `22a` precedes `23a` because the
renderer takes a goal register.

### Wave 2 — the full surface (7 slices)

| Slice | Delivers |
|---|---|
| `33c` | Chat and channel messages — client-side cursor, HTML→text, `MessagePayload.mentions`. |
| `33d` | Transcript resource — `joinUrl` → `onlineMeetings` → `/content`; 403 degradation. Replaces `_fake_api`. |
| `23c` | Proactive Enablement fills from real message events. |
| `4d` | Daemon and loopback-only FastAPI binding. |
| `4e` | REPL at CAP-18 parity, under 1.0s startup. |
| `9a` | Scheduled harvest — 240min ±15, exponential backoff, the missing `try/except` story 9 names, and the 07:00 render tick. |
| `11b` | Real transcript path wired into the existing extraction pipeline. |

## Deferred, with reasons

- **Story 3 — MCP execution firewall.** Deferred because the prototype mutates
  nothing external and, under decision 2, puts no model in the path — so no
  harvested text reaches a prompt. It becomes a **hard prerequisite** the moment
  either changes; nothing in wave 1 or 2 does.

  The rationale is *not* that sanitization already works. It does not — see
  slice `8c`. An earlier draft of this document claimed the harvest boundary
  sanitizes; that claim was wrong and the correction is why `8c` exists.
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
foundation. This path is 19 slices plus a spike — the same order of magnitude
again. "Shortest" means shortest *given the four decisions above*, not small.

The shortest path to something working is wave 1 alone: 12 slices.

## Open questions

1. **Does the tenant permit Graph transcript access?** Slice 0 answers it. A
   `403 GraphAccessToTranscriptsDisabled` removes slice 33d and `11b` from the
   plan entirely and there is no workaround.
2. **Is "3-Tier" horizon or domain?** Decided as horizon above. A one-line
   change now; a re-render later.
3. **Does a payload gaining a field need an operational schema version bump?**
   Story 1i built the versioning; whether a Tier-1 Markdown entry format change
   is in its remit needs checking against `1i` before `33c` is specced. This
   covers both `MessagePayload.mentions` and slice `8c`'s persisted `for_model`.
4. **How should a payload declare which of its fields is sanitizable text?**
   Slice `8c` needs an answer that is not a hardcoded field name. A `Protocol`, a
   classvar on each payload, or a mapping beside `PAYLOAD_FOR` are all plausible;
   the choice belongs in `8c`'s spec, not here.
