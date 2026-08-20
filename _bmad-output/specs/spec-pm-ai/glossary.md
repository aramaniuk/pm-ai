# Glossary — pm-ai

Companion to `SPEC.md`. Terms are contract vocabulary: where a capability or constraint uses one of these words, this is what it means. Scope definitions live in `scope-model.md`; storage tiers in `storage-contract.md`.

**Meeting (citation root)** — The real-world event a transcript derives from, held as a durable record with id, calendar reference, title, start, duration, and attendees. Extracted facts cite the **meeting**, never the transcript: a transcript is one lossy capture and is purged on schedule, which would leave every citation resolving to nothing. The meeting record also carries the Man-Hour Cost inputs, so cost, prep, and missed-meeting analysis read one entity rather than three ad-hoc lookups.

**Disclosure Ledger** (`~/.pm-ai/disclosure.md`) — The single application-scoped, append-only record of every frontier model call: contributing scopes, task class, model, token counts, estimated cost. Deliberately outside every repository and separate from the per-scope event log.

**Execution Firewall** — The boundary isolating the LLM reasoning core from raw terminal or OS shell execution, routing all external mutations exclusively through registry-authorized MCP skill tools.

**Input Sanitization Module** — The pre-parsing layer that inspects and cleanses all incoming operational telemetry (PR descriptions, commit logs, email threads, calendar invites, meeting transcripts) to strip embedded prompt-injection vectors before the payload enters model context. Non-destructive: the raw payload survives unmodified.

**Automated Memory Pruning Pipeline** — The background process that compresses short-term activity streams (routine diffs, daily logs) into structured long-term milestone summaries and maintains vector index embeddings, capping **retrieval** latency at 50–150 ms. Synthesis latency is governed separately.

**User Privacy & Data Boundary Charter** — The binding specification governing personal workload telemetry, burnout metrics, and Socratic coaching records in the sovereign scope. Its adversary is **employer-controlled systems** — team channels, shared repositories, enterprise IT dashboards, HR platforms — to which this material is never exported. Frontier model APIs are a disclosed exception: personal-scope material may enter a model prompt, every such call is recorded in the disclosure ledger, and no record written to a git-committed scope may reference personal-scope material. The charter names its threat model explicitly, because a charter meaning something narrower than its words invites a reader to assume more protection than exists.

**External System Connector** — A modular plugin interfacing with an external API (GitLab, Teams, Outlook, HR MCP, Slack, Jira, Notion) to harvest telemetry, sync state, and post responses using encrypted credential storage. All connectors are outbound pull adapters; none exposes an inbound endpoint.

**Connector Schema** — The standardized data contract and event normalization protocol converting disparate external activity (commits, tickets, channel chats, pages) into unified JSON telemetry entries.

**CLI Interactive REPL Shell** — The terminal shell started by running `pm-ai` with no parameters, accepting fixed commands or open natural-language prompts continuously until `exit` or `quit`.

**Socratic 1:1 Protocol** — The dialogue mechanism over Telegram or CLI in which pm-ai surfaces telemetry-backed blind spots and asks reflective questions rather than issuing directives.

**High-Context Voice Concierge** — The capability to expand short voice prompts into detailed, context-rich correspondence by synthesizing repository specs, meeting transcripts, and project data.

**Contextual Web & Literature Engine** — Background ingestion and situational matching of external RSS feeds and HTTP web pages against live project bottlenecks, team dynamics, and career goals.

**Meeting ROI Metric / Man-Hour Cost** — `attendees × duration_hours × blended_hourly_rate`, where the blended rate is a single PM-configured figure in `~/.pm-ai/config.toml` rather than per-attendee salary data — which keeps compensation out of the telemetry store. Displayed as an informative metric in post-meeting summary headers to foster cost awareness.

**Verbal Commitment Sync** — Automatic extraction of spoken meeting promises and staging of timestamped comments attached to target Work Items or tickets.

**Meeting Commitment Ledger & Closed-Loop Lifecycle** — The persistent accountability mechanism (structured Markdown in the project scope's `commitments_log.md`, indexed in `operational.db`) capturing extracted promises, owners, target deadlines, target Work Items, lifecycle statuses `[STAGED_APPROVAL]`, `[PENDING]`, `[FULFILLED]`, `[ALTERED]`, `[BROKEN]`, `[UNKNOWN]`, and cross-referencing incoming commits, PR review latencies, and ticket state to verify real-world execution.

**Spoken Anchor Protocol & Fuzzy Recovery** — The structured speaking convention identifying target Work Item numbers, with automated fuzzy recovery (≥85% confidence) for phonetic or transcription errors.

**Explicit In-Meeting Command** — A direct spoken directive addressing the assistant by name. It authorizes immediate execution only when source, speaker, and verb all qualify; otherwise it stages.

**Implicit Discussion Extraction** — Information, context, decisions, or ticket updates derived from general team conversation where pm-ai was not invoked. Never mutates external state without approval.

**Interactive Approval Card** — The Telegram card or CLI prompt displaying a proposed implicit update — Work Item with parsed number, owner, priority, documentation, decisions — with `[Approve]` and `[Edit]` actions, required before any external state mutation.

**Asynchronous Missed Meeting Ingestion** — On-demand retrieval and analysis of recorded meeting transcripts for sessions the PM was absent from or optional in.

**Transcript-Triggered Research Task** — An asynchronous research job spawned by an in-meeting command requiring multi-source synthesis, web research, or documentation lookup, delivered via email or Work Item comments.

**Career Dossier** — A pre-meeting executive summary combining recent Git telemetry, custom monitored metrics, and external HR goal tracking, pushed before an employee 1:1. Team-member-scope material.

**Meta-Coaching Scorecard** — The post-session evaluation capturing Coaching Efficiency, Dialogue Quality, and Questioning Precision (each 1-10) to calibrate persona and questioning strategy, plus Domain Distress (1-10), which is recorded and deliberately excluded from tuning.

**Anti-Burnout Telemetry Shield** — Passive monitoring of working hours, calendar density, and PTO usage, surfacing exhaustion risk strictly inside 1:1 coaching dialogue and weekly/daily planning.

**Unified Telemetry & Decision Log Store** — The consolidated per-scope record of operational events, system actions, and leadership decisions as typed JSON entries in `event_log/` and `operational.db`. Segmented: a directory of dated segments, one open, earlier ones sealed and immutable.

**Asynchronous Deep Inquiry Engine** — Complex multi-source telemetry queries (commits, CI/CD, calendar, transcripts) with non-blocking deferred delivery of structured results over Telegram or CLI.

**Documentation Drift Check** — Automated comparison between recent meeting decisions and committed repository Markdown to detect protocol or specification mismatch. Never blocks a Merge Request.

**Pre-Meeting Preparation Dashboard** — The synthesized pre-meeting view combining active work items, blocked items, backlog priorities, multi-day trends, and per-participant cross-source activity.

**Daily Commitment Validation** — Automated verification comparing promises made in previous meetings against harvested Git, Teams, Outlook, and Work Item evidence.

**Transcript Lifecycle Policy** — The retention duration for raw transcript text (default 30 days, configurable), after which raw transcripts purge automatically once extraction into summaries, decisions, and Work Items is verified.
