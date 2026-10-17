# Glossary — pm-ai

Companion to `SPEC.md`. Terms are contract vocabulary: where a capability or constraint uses one of these words, this is what it means. Scope definitions live in `scope-model.md`; storage tiers in `storage-contract.md`.

**Meeting (citation root)** — The real-world event a transcript derives from, held as a durable record with id, calendar reference, title, start, duration, and attendees. Extracted facts cite the **meeting**, never the transcript: a transcript is one lossy capture and is purged on schedule, which would leave every citation resolving to nothing. The meeting record also carries the Man-Hour Cost inputs, so cost, prep, and missed-meeting analysis read one entity rather than three ad-hoc lookups.

**Disclosure Ledger** (`~/.pm-ai/disclosure.md`) — The single application-scoped, append-only record of every frontier model call: contributing scopes, task class, model, token counts, estimated cost. Deliberately outside every repository and separate from the per-scope event log. Each call is recorded as a `sent` line before the request — if it cannot be written, nothing is sent — and, when the call ends, a `completed` line (the tokens the answer reported and the computed cost) or a `failed` line with the same call id; a call abandoned at its deadline or at shutdown keeps only its `sent` line (2026-10-16, story 7c). The monthly total counts completed calls and states sent-but-unfinished ones as calls of unknown cost.

**Execution Firewall** — The boundary isolating the LLM reasoning core from raw terminal or OS shell execution, routing all external mutations exclusively through registry-authorized MCP skill tools.

**Input Sanitization Module** — The pre-parsing layer that inspects and cleanses all incoming operational telemetry (PR descriptions, commit logs, email threads, calendar invites, meeting transcripts) to strip embedded prompt-injection vectors before the payload enters model context. Non-destructive: the raw payload survives unmodified.

**Automated Memory Pruning Pipeline** — The background process that compresses short-term activity streams (routine diffs, daily logs) into structured long-term milestone summaries and maintains vector index embeddings, capping **retrieval** latency at 50–150 ms. Synthesis latency is governed separately.

**User Privacy & Data Boundary Charter** — The binding specification governing personal workload telemetry, burnout metrics, and Socratic coaching records in the sovereign scope. Its adversary is **employer-controlled systems** — team channels, shared repositories, enterprise IT dashboards, HR platforms — to which this material is never exported. Frontier model APIs are a disclosed exception: personal-scope material may enter a model prompt, every such call is recorded in the disclosure ledger, and no record written to a git-committed scope may reference personal-scope material. The charter names its threat model explicitly, because a charter meaning something narrower than its words invites a reader to assume more protection than exists.

**External System Connector** — A modular plugin interfacing with an external API (GitLab, Teams, Outlook, HR MCP, Slack, Jira, Notion) to harvest telemetry, sync state, and post responses using encrypted credential storage. All connectors are outbound pull adapters; none exposes an inbound endpoint.

**Person Handle** (`<system>:<handle>`) — How a person appears on a ledger line: the provider's own identifier under a declared system — `graph:<Graph user id>`, `email:<address>`, `graph_app:<application id>` for a bot, `gitlab:<host>:<numeric user id>` for a GitLab member, assignee or author (2026-10-16, story 8t; Andrei, 2026-10-16: the host lowercased, its port kept only when not the scheme default, the handle parsed at its last `:` — GitLab user ids are per host; an id that is not an integer gives no handle). Graph handles carry no tenant, because Entra object ids are GUIDs unique across tenants. A person handle is not a connector instance name: `gitlab:<project id>` names a connector instance and is unchanged, `none:` when no person is behind an event. Matched to a pm-ai person only when read, through the alias table; never a display name. A display name (a Graph message sender's, story 23f) or a GitLab display name and username (story 8t) may be stored **beside** the handle for display and name matching only — never compared as identity, never written in its place. A transcript speaker label is a display name, never a handle.

**Connector Schema** — The standardized data contract and event normalization protocol converting disparate external activity (commits, tickets, channel chats, pages) into typed events written as one Markdown ledger line each in the owning scope's `event_log/` segment (operational.db holds cursors, coverage and dedup state, never the events).

**CLI Interactive REPL Shell** — The terminal shell started by running `pm-ai` with no parameters, accepting fixed commands or open natural-language prompts continuously until `exit` or `quit`.

**Socratic 1:1 Protocol** — The dialogue mechanism over Telegram or CLI in which pm-ai surfaces telemetry-backed blind spots and asks reflective questions rather than issuing directives.

**High-Context Voice Concierge** — The capability to expand short voice prompts into detailed, context-rich correspondence by synthesizing repository specs, meeting transcripts, and project data.

**Contextual Web & Literature Engine** — Background ingestion and situational matching of external RSS feeds and HTTP web pages against live project bottlenecks, team dynamics, and career goals.

**Meeting ROI Metric / Man-Hour Cost** — `attendees × duration_hours × blended_hourly_rate`, where the blended rate is a single PM-configured figure in `~/.pm-ai/config.toml` rather than per-attendee salary data — which keeps compensation out of the telemetry store. Displayed as an informative metric in post-meeting summary headers to foster cost awareness.

**Verbal Commitment Sync** — Automatic extraction of spoken meeting promises and staging of timestamped comments attached to target Work Items or tickets.

**Meeting Commitment Ledger & Closed-Loop Lifecycle** — The persistent accountability mechanism (structured Markdown in the project scope's `commitments_log.md`, indexed in `commitment_index.db`, a Tier-3 index a declared job rebuilds) capturing extracted promises, owners, target deadlines, target Work Items, lifecycle statuses `[STAGED_APPROVAL]`, `[PENDING]`, `[FULFILLED]`, `[ALTERED]`, `[BROKEN]`, `[UNKNOWN]` (pm-ai has not yet had a complete successful look; self-resolving), `[ERROR]` (a contributing connector attempted and failed; needs a human to re-authenticate or fix configuration), and cross-referencing incoming commits, PR review latencies, and ticket state to verify real-world execution.

**Spoken Anchor Protocol & Fuzzy Recovery** — The structured speaking convention identifying target Work Item numbers, with automated fuzzy recovery (≥85% confidence) for phonetic or transcription errors.

**Explicit In-Meeting Command** — A direct spoken directive addressing the assistant by name. It authorizes immediate execution only when source, speaker, and verb all qualify; otherwise it stages.

**Implicit Discussion Extraction** — Information, context, decisions, or ticket updates derived from general team conversation where pm-ai was not invoked. Never mutates external state without approval.

**Interactive Approval Card** — The Telegram card or CLI prompt displaying a proposed implicit update — Work Item with parsed number, owner, priority, documentation, decisions — with `[Approve]` and `[Edit]` actions, required before any external state mutation.

**Asynchronous Missed Meeting Ingestion** — On-demand retrieval and analysis of recorded meeting transcripts for sessions the PM was absent from or optional in.

**Transcript-Triggered Research Task** — An asynchronous research job spawned by an in-meeting command requiring multi-source synthesis, web research, or documentation lookup, delivered via email or Work Item comments.

**Career Dossier** — A pre-meeting executive summary combining recent Git telemetry, custom monitored metrics, and external HR goal tracking, pushed before an employee 1:1. Team-member-scope material.

**Meta-Coaching Scorecard** — The post-session evaluation capturing Coaching Efficiency, Dialogue Quality, and Questioning Precision (each 1-10) to calibrate persona and questioning strategy, plus Domain Distress (1-10), which is recorded and deliberately excluded from tuning.

**Anti-Burnout Telemetry Shield** — Passive monitoring of working hours, calendar density, and PTO usage, surfacing exhaustion risk strictly inside 1:1 coaching dialogue and weekly/daily planning.

**Unified Telemetry & Decision Log Store** — The consolidated per-scope record of operational events, system actions, and leadership decisions as typed entries, one Markdown ledger line each, in `event_log/`; `operational.db` keeps the operational state beside it (cursors, coverage windows, dedup keys, staged proposals). Segmented: a directory of dated segments, one open, earlier ones sealed and immutable.

**Asynchronous Deep Inquiry Engine** — Complex multi-source telemetry queries (commits, CI/CD, calendar, transcripts) with non-blocking deferred delivery of structured results over Telegram or CLI.

**Documentation Drift Check** — Automated comparison between recent meeting decisions and committed repository Markdown to detect protocol or specification mismatch. Never blocks a Merge Request.

**Pre-Meeting Preparation Dashboard** — The synthesized pre-meeting view combining active work items, blocked items, backlog priorities, multi-day trends, and per-participant cross-source activity.

**Daily Commitment Validation** — Automated verification comparing promises made in previous meetings against harvested Git, Teams, Outlook, and Work Item evidence.

**Transcript Lifecycle Policy** — The retention duration for raw transcript text (default 30 days, configurable), after which raw transcripts purge automatically once extraction into summaries, decisions, and Work Items is verified.

## Terms added with wave 3 (2026-10-16)

**Alias Table** (`~/.pm-ai/aliases.md`) — The saved table of names the PM has confirmed: Tier 1, application-wide (Andrei, 2026-10-16), plaintext, gitignored, append-only. One row per confirmed name → person handle (a `gitlab:` handle in wave 3), keyed by one normalisation (Unicode NFKC, case-folded, trimmed, whitespace collapsed), optionally scoped to one meeting; a later row removes or replaces a mapping. Written only by `pm-ai member name`, which asks for confirmation; a generic label (`Speaker <n>`, `Unknown`, `Guest`) is accepted only for one meeting. It is the alias table the kernel's person-handle constraint names, saved for the first time: its rows map names to handles, beside the handle → person mapping every ledger reader resolves through — which still has no saved row shape (an open question in the memlog). Every later name resolution (Graph speakers, chat senders) reuses it; there is no second name map. Application scope because a name → handle row describes a person, not a project.

**Team Activity** — The third section of a project's dashboard, where per-member activity is shown: each project member with their open assigned GitLab issues in milestone order, each issue marked when it was worked on lately (commits referencing it in the commit window, a linked merge request updated or merged in the merge-request window), and under each issue the meeting updates matched to it. Read from stored records only, with no request and no model at render. Never ranks people, prints per-person totals or compares people; never reads personal-scope or team-member-scope data.

**Member's tasks and priorities** — A project member's open GitLab issues (assignees, milestone, labels), as GitLab holds them. pm-ai never infers or changes a priority: what a meeting says about an issue is shown beside it as a meeting update, and a due date said in a meeting is shown beside the milestone's, never replacing it.

**Meeting Update** — One line in a project's `memory/member_updates.md` (Tier 1, append-only): an action item or decision from a summarised project meeting that names a GitLab issue, holding the issue and the speaker label as said. Who it belongs to is decided at read time through a name the PM confirmed (`pm-ai member name`, the alias table), or by an exact match to exactly one active member's display name or username; otherwise it is shown unassigned, so no spoken name reaches the team page. A re-summary that drops an item appends a `withdrawn` line.

**Model-Matched** — A meeting update linked to a GitLab issue by one local-model call choosing among the project's open, non-confidential issue titles, because no issue number was said. Shown as model-matched and listed by `pm-ai member unmatched` until the PM confirms or rejects it (`pm-ai member link`); a number said in the meeting, or a confirmed link, is shown as said in the meeting.

**Meeting Summary** — What a summarised transcript yields, written in the language most of the meeting was held in: a summary in the meeting record's `## Summary` region, each decision as a decision event in the meeting's scope, each action item as a staged proposal that never executes. Made by the local model by default (task class `extraction`); a long transcript is split at speaker turns and merged, never refused for length.

**Frontier Summary on Request** — A meeting summary made by Claude (task class `meeting_summary`) only when the PM asks for it (`pm-ai transcript summarize … --by claude`), from the meeting's own scope back to that scope, disclosed in the disclosure ledger before it is sent, its cost reported with the month's total against the $20 target. Never automatic, never a fallback for a local failure, and no fallback to local when it fails.

**Summary State** — One Tier-2 row per meeting and scope recording whether its summary is `waiting`, `summarized`, `failed` or `nothing said`, who made it (`local` or `frontier`), whether it was asked for, the model, attempts and reason. The dashboard selects meetings by when this row was recorded, not by the meeting's start, so a summary written hours later still appears once.

**Decision Reference** (`decision:<meeting id>_<digest>`) — The source reference of a decision event: the meeting id and the first 12 hex characters of SHA-256 over the decision's cleaned, trimmed statement. Scopeless, as `meeting:` is; each decision has its own, so storage's duplicate check keeps every decision beside the meeting's calendar event, and the same statement from a re-run is one event. Decisions accumulate: a reworded re-summary appends, and none is removed.

**Transcript Part** — One of the transcripts Teams lists when a meeting's transcription was stopped and started again. Every part that fits the meeting and overlaps no other is kept as its own capture (part 1 `<stem>-graph.vtt`, part *n* `<stem>-graph-<n>.vtt`), numbered in time order and ingested in that order into the one meeting; overlapping transcripts are refused as duplicates.

**Display Day** — The dashboard's today: local midnight to the next local midnight in `display_timezone`, asked of the calendar as exactly that range, whatever the harvest window is.

**Private (file mode)** — Every file pm-ai keeps under `~/.pm-ai` and `~/.manager-ai`, and under a project's `.project-ai/memory/` and `.project-ai/transcripts/`, is `0600` in a `0700` folder, decided by where it lives rather than by whether git may see it. A project's shared parts (`.project-ai/`, `rules/`, `skills/`, `.gitignore`) stay at the umask.
